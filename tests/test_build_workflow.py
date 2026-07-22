import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


class BuildWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")

    def test_hot_switch_defaults_to_fast_and_accepts_compact(self):
        self.assertIn("REQUESTED_MODE: ${{ vars.PACKAGE_BUILD_MODE }}", self.workflow)
        self.assertIn('$mode = "fast"', self.workflow)
        self.assertIn('@("fast", "compact")', self.workflow)
        self.assertIn('if ($mode -eq "fast") { "zlib" } else { "lzma" }', self.workflow)

    def test_fast_and_compact_jobs_are_both_present(self):
        self.assertIn("  package-fast:", self.workflow)
        self.assertIn("  package-compact:", self.workflow)
        self.assertIn("  release-compact:", self.workflow)
        self.assertIn("needs.prepare.outputs.package_mode == 'fast'", self.workflow)
        self.assertIn("needs.prepare.outputs.package_mode == 'compact'", self.workflow)

    def test_compact_mode_keeps_china_and_global_profiles(self):
        self.assertIn("          - profile: China", self.workflow)
        self.assertIn("          - profile: Global", self.workflow)
        self.assertIn("ok-bd2-win32-China-setup.exe", self.workflow)
        self.assertIn("ok-bd2-win32-Global-setup.exe", self.workflow)

    def test_launcher_reuse_is_guarded_by_launcher_inputs(self):
        self.assertIn("Find reusable launcher", self.workflow)
        self.assertIn("scripts/prepare_pyappify_launcher.ps1", self.workflow)
        self.assertIn("ok-bd2-win32.zip", self.workflow)
        self.assertIn("restore_pyappify_launcher.ps1", self.workflow)

    def test_required_workflow_scripts_are_packaged(self):
        scripts = (
            "prepare_pyappify_launcher.ps1",
            "prepare_release_notes.ps1",
            "restore_pyappify_launcher.ps1",
            "select_pyappify_profile.ps1",
        )
        for script in scripts:
            with self.subTest(script=script):
                self.assertTrue((ROOT / "scripts" / script).is_file())

    def test_launcher_script_supports_both_compression_modes(self):
        script = (ROOT / "scripts" / "prepare_pyappify_launcher.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('[ValidateSet("zlib", "lzma")]', script)
        self.assertIn("[switch]$SkipBuild", script)
        self.assertIn("-Name compression", script)


if __name__ == "__main__":
    unittest.main()
