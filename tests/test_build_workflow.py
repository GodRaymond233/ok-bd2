import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
PYAPPIFY_CONFIG = ROOT / "pyappify.yml"


class BuildWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        cls.test_workflow = TEST_WORKFLOW.read_text(encoding="utf-8")
        cls.pyappify_config = PYAPPIFY_CONFIG.read_text(encoding="utf-8")

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
            "check_dependency_exports.ps1",
            "prepare_pyappify_launcher.ps1",
            "prepare_release_notes.ps1",
            "refresh_dependencies.ps1",
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

    def test_launcher_version_is_consistently_pinned(self):
        script = (ROOT / "scripts" / "prepare_pyappify_launcher.ps1").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("v1.1.6", self.workflow)
        self.assertNotIn("v1.1.6", script)
        self.assertEqual(6, self.workflow.count("v1.1.7"))
        self.assertIn('[string]$Version = "v1.1.7"', script)

    def test_launcher_uses_project_icon(self):
        self.assertIn('icon: "icons/icon.png"', self.pyappify_config)
        self.assertTrue((ROOT / "icons" / "icon.png").is_file())

    def test_workflows_validate_uv_lock_and_exports(self):
        action = "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b"
        for workflow in (self.workflow, self.test_workflow):
            with self.subTest(workflow=workflow[:20]):
                self.assertIn(action, workflow)
                self.assertIn('version: "0.11.21"', workflow)
                self.assertIn(r".\scripts\check_dependency_exports.ps1", workflow)

    def test_build_tests_do_not_receive_release_credentials(self):
        run_tests = self.workflow.split("      - name: Run tests", 1)[1].split(
            "      - name: Inline ok-script for update repository", 1
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
                self.assertIn(f'{variable}: ""', run_tests)
                self.assertIn(f'"{variable}"', run_tests)

    def test_build_tests_run_before_inlining_dependencies(self):
        run_tests = self.workflow.index("      - name: Run tests")
        inline_dependencies = self.workflow.index(
            "      - name: Inline ok-script for update repository"
        )
        validate_inline = self.workflow.index(
            "      - name: Validate inlined update repository"
        )

        self.assertLess(run_tests, inline_dependencies)
        self.assertLess(inline_dependencies, validate_inline)
        self.assertIn(
            'Test-Path -LiteralPath "ok" -PathType Container',
            self.workflow,
        )
        self.assertIn(
            "requirements.txt still contains ok-script after inlining.",
            self.workflow,
        )


if __name__ == "__main__":
    unittest.main()
