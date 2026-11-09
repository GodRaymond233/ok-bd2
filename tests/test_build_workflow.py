import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.install_dependencies import install_requirements

ROOT = Path(__file__).resolve().parents[1]
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
PYAPPIFY_CONFIG = ROOT / "pyappify.yml"
RUN_CHECKS_SCRIPT = ROOT / "scripts" / "run_checks.ps1"


class BuildWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        cls.test_workflow = TEST_WORKFLOW.read_text(encoding="utf-8")
        cls.pyappify_config = PYAPPIFY_CONFIG.read_text(encoding="utf-8")
        cls.run_checks_script = RUN_CHECKS_SCRIPT.read_text(encoding="utf-8")

    def test_build_quality_gate_does_not_receive_release_credentials(self):
        quality_gate = self.workflow.split("  quality:", 1)[1].split(
            "  prepare:", 1
        )[0]
        for variable in (
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "CNB_GH",
            "OK_GH",
            "SIGNPATH_API_TOKEN",
            "MirrorChyanUploadToken",
            "ACTIONS_RUNTIME_TOKEN",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        ):
            with self.subTest(variable=variable):
                self.assertIn(f'{variable}: ""', quality_gate)
                self.assertIn(f'"{variable}"', quality_gate)

    def test_keyboard_scan_covers_project_keyboard_apis(self):
        for pattern in (
            '"pydirectinput"',
            '"pynput.keyboard"',
            '"from pynput import keyboard"',
            '"from pynput.keyboard"',
            '"keyboard.press"',
            '"keyboard.send"',
            '"keyboard.type"',
            '"keyboard.hotkey"',
            '"keyboard.Key"',
            '"keyboard.Listener"',
            '"keyDown"',
            '"keyUp"',
            '"keybd_event"',
            '"KEYEVENTF"',
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, self.run_checks_script)
        self.assertNotIn('"SendInput"', self.run_checks_script)

    def test_desktop_payload_is_built_before_sync_and_reaches_both_release_modes(self):
        prepare = self.workflow.split("  prepare:", 1)[1].split("  package-fast:", 1)[0]
        for required in ("npm --prefix desktop/ui run build",
                         "npm --prefix desktop/shell run pack:win",
                         "desktop/scripts/build-shell-release.py", "src/desktop/shell-release.json",
                         "name: desktop-release"):
            self.assertIn(required, prepare)
        self.assertLess(prepare.index("Build Electron interface and shell"),
                        prepare.index("Sync update repositories"))
        for job, following in (("package-fast", "package-compact"),
                               ("release-compact", None)):
            section = self.workflow.split(f"  {job}:", 1)[1]
            if following:
                section = section.split(f"  {following}:", 1)[0]
            self.assertIn("name: desktop-release", section)
            self.assertIn("Copy-Item desktop-release/*.zip pyappify_dist/", section)

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows launcher resource assembly")
    def test_installer_shell_resource_keeps_its_versioned_layout_and_checks_hash(self):
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "ok-bd2-shell-0.3.5-win-x64.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("ok-bd2-desktop.exe", b"synthetic shell")
                output.writestr("shell-manifest.json", json.dumps({"version": "0.3.5"}))
                output.writestr("resources/app.asar", b"synthetic app")
            manifest = root / "shell-release.json"
            manifest.write_text(json.dumps({"version": "0.3.5", "sha256":
                hashlib.sha256(archive.read_bytes()).hexdigest()}), "utf-8")
            script = root / "check.ps1"
            script.write_text('''param($Source, $Release, $Target)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
$function = $ast.Find({param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Add-DesktopShellResource'
}, $true)
Invoke-Expression $function.Extent.Text
$config = [pscustomobject]@{bundle=[pscustomobject]@{resources=@('data')}}
Add-DesktopShellResource $Release $Target 'ok-bd2' $config
$config | ConvertTo-Json -Depth 10
''', "utf-8")
            command = ["powershell.exe", "-NoProfile", "-File", str(script),
                       str(ROOT / "scripts/prepare_pyappify_launcher.ps1"), str(root),
                       str(root / "tauri")]
            env = {key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"}
            run = subprocess.run(command, capture_output=True, text=True, env=env,
                                 encoding="utf-8", errors="replace")
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            resources = json.loads(run.stdout)["bundle"]["resources"]
            self.assertEqual("data/apps/ok-bd2/shell/0.3.5/", resources["desktop-shell/"])
            self.assertTrue((root / "tauri/desktop-shell/resources/app.asar").is_file())
            manifest.write_text(json.dumps({"version": "0.3.5", "sha256": "0" * 64}), "utf-8")
            run = subprocess.run(command, capture_output=True, text=True, env=env,
                                 encoding="utf-8", errors="replace")
            self.assertNotEqual(0, run.returncode)
            self.assertIn("SHA256 mismatch", run.stderr)


class DependencyInstallTest(unittest.TestCase):
    def test_transient_failure_retries_then_succeeds(self):
        results = [
            subprocess.CompletedProcess([], 1, "ReadTimeoutError: download timed out"),
            subprocess.CompletedProcess([], 0, "Installed"),
        ]
        with patch("scripts.install_dependencies.subprocess.run", side_effect=results) as run, \
                patch("scripts.install_dependencies.time.sleep") as sleep:
            self.assertEqual(0, install_requirements("requirements.txt"))
        self.assertEqual(2, run.call_count)
        self.assertEqual(["-r", "requirements.txt"], run.call_args.args[0][-2:])
        sleep.assert_called_once_with(15)

    def test_permanent_and_unknown_failures_are_not_retried(self):
        for message in (
            "No matching distribution found for nonexistent==99.0",
            "ResolutionImpossible: conflicting dependencies; download timed out",
            "THESE PACKAGES DO NOT MATCH THE HASHES; ConnectionError",
            "Unexpected build failure",
        ):
            with self.subTest(message=message), \
                    patch("scripts.install_dependencies.subprocess.run", return_value=
                          subprocess.CompletedProcess([], 7, message)) as run, \
                    patch("scripts.install_dependencies.time.sleep") as sleep:
                self.assertEqual(7, install_requirements("requirements.txt"))
                run.assert_called_once()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
