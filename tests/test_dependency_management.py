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
        self.assertIn("ok-script[ocr]==3.0.4", dependencies)
        self.assertIn("ok-script==3.0.4", self.runtime_requirements)
        self.assertIn("ok-script==3.0.4", self.dev_requirements)
        self.assertNotIn("ok-script==1.0.190", self.runtime_requirements)

    def test_native_runtime_versions_match_the_lock(self):
        dependencies = self.pyproject["project"]["dependencies"]
        self.assertIn("ok-script[ocr]==3.0.4", dependencies)
        self.assertIn("pyappify==1.0.13", dependencies)
        self.assertIn("msvc-runtime>=14.44.35112", dependencies)
        self.assertIn("openvino==2026.4.1", dependencies)

        for name, version in (
            ("ok-script", "3.0.4"),
            ("pyappify", "1.0.13"),
            ("msvc-runtime", "14.44.35112"),
            ("openvino", "2026.4.1"),
        ):
            with self.subTest(name=name):
                package = next(item for item in self.lock["package"] if item["name"] == name)
                self.assertEqual(version, package["version"])


if __name__ == "__main__":
    unittest.main()
