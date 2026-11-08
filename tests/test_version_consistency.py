import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VersionConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.expected_version = cls.pyproject["project"]["version"]

    def test_runtime_config_exposes_the_same_version(self):
        from src.config import config, runtime_version

        self.assertEqual(self.expected_version, config["version"])
        self.assertEqual(self.expected_version, runtime_version(ROOT / "pyproject.toml"))

    def test_source_version_is_not_duplicated_in_the_inline_release_marker(self):
        from src.config import version

        self.assertEqual("release-tag-unset", version)
        self.assertNotEqual(self.expected_version, version)


if __name__ == "__main__":
    unittest.main()
