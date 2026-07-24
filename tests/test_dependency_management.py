import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DependencyManagementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.runtime_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        cls.dev_requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    def test_ok_script_is_consistently_pinned(self):
        dependencies = self.pyproject["project"]["dependencies"]
        self.assertIn("ok-script==1.0.174", dependencies)
        self.assertIn("ok-script==1.0.174", self.runtime_requirements)
        self.assertIn("ok-script==1.0.174", self.dev_requirements)
        self.assertNotIn("ok-script==1.0.172", self.runtime_requirements)

    def test_uv_lock_targets_windows_and_is_committed(self):
        self.assertEqual(
            ["sys_platform == 'win32'"],
            self.pyproject["tool"]["uv"]["environments"],
        )
        self.assertTrue((ROOT / "uv.lock").is_file())
        self.assertFalse((ROOT / "requirements.in").exists())

    def test_runtime_and_development_exports_are_separated(self):
        self.assertNotIn("ruff==", self.runtime_requirements)
        self.assertIn("ruff==", self.dev_requirements)

    def test_dependency_scripts_use_locked_exports(self):
        refresh = (ROOT / "scripts" / "refresh_dependencies.ps1").read_text(
            encoding="utf-8"
        )
        check = (ROOT / "scripts" / "check_dependency_exports.ps1").read_text(
            encoding="utf-8"
        )
        for script in (refresh, check):
            with self.subTest(script=script[:20]):
                self.assertIn("--locked", script)
                self.assertIn("--no-header", script)
        self.assertIn("--no-dev", refresh)
        self.assertIn("uv lock --quiet --check", check)


if __name__ == "__main__":
    unittest.main()
