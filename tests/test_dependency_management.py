import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DependencyManagementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        cls.runtime_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        cls.dev_requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    def test_ok_script_is_consistently_pinned(self):
        dependencies = self.pyproject["project"]["dependencies"]
        self.assertIn("ok-script==1.0.190", dependencies)
        self.assertIn("ok-script==1.0.190", self.runtime_requirements)
        self.assertIn("ok-script==1.0.190", self.dev_requirements)
        self.assertNotIn("ok-script==1.0.180", self.runtime_requirements)

    def test_msvc_runtime_is_consistently_constrained(self):
        dependencies = self.pyproject["project"]["dependencies"]
        self.assertIn("msvc-runtime>=14.44.35112", dependencies)
        self.assertIn("msvc-runtime==14.44.35112", self.runtime_requirements)
        self.assertIn("msvc-runtime==14.44.35112", self.dev_requirements)

    def test_onnxocr_is_exactly_pinned_with_pypi_provenance(self):
        dependencies = self.pyproject["project"]["dependencies"]
        self.assertIn("onnxocr-ppocrv5==0.0.22", dependencies)
        self.assertIn("onnxocr-ppocrv5==0.0.22", self.runtime_requirements)
        self.assertIn("onnxocr-ppocrv5==0.0.22", self.dev_requirements)

        package = next(item for item in self.lock["package"] if item["name"] == "onnxocr-ppocrv5")
        self.assertEqual("0.0.22", package["version"])
        self.assertEqual(
            "sha256:08ca735a2038e5fc61038935b9010438f33b7e63f0eea7c5ecf468a56ee48bb5",
            package["sdist"]["hash"],
        )
        self.assertEqual(1, len(package["wheels"]))
        self.assertEqual(
            "sha256:b98271ce876d9720c788c437d69f63469cad53cb19945da843dbbe46f8a1271a",
            package["wheels"][0]["hash"],
        )

    def test_s1_dependency_pins_remain_unchanged(self):
        dependencies = self.pyproject["project"]["dependencies"]
        self.assertIn("ok-script==1.0.190", dependencies)
        self.assertIn("pyappify==1.0.6", dependencies)
        self.assertIn("msvc-runtime>=14.44.35112", dependencies)

        for name, version in (
            ("ok-script", "1.0.190"),
            ("pyappify", "1.0.6"),
            ("msvc-runtime", "14.44.35112"),
        ):
            with self.subTest(name=name):
                package = next(item for item in self.lock["package"] if item["name"] == name)
                self.assertEqual(version, package["version"])

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
        refresh = (ROOT / "scripts" / "refresh_dependencies.ps1").read_text(encoding="utf-8")
        check = (ROOT / "scripts" / "check_dependency_exports.ps1").read_text(encoding="utf-8")
        for script in (refresh, check):
            with self.subTest(script=script[:20]):
                self.assertIn("--locked", script)
                self.assertIn("--no-header", script)
        self.assertIn("--no-dev", refresh)
        self.assertIn("uv lock --quiet --check", check)


if __name__ == "__main__":
    unittest.main()
