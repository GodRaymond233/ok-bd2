import subprocess
import unittest
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
