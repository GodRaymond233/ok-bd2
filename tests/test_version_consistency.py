import re
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

    def test_inlined_update_repository_uses_its_release_tag_without_pyproject(self):
        import src.config as app_config

        previous_version = app_config.version
        self.addCleanup(setattr, app_config, "version", previous_version)
        app_config.version = "v0.1.18"

        self.assertEqual("v0.1.18", app_config.runtime_version(ROOT / "missing-pyproject.toml"))

    def test_pyappify_inline_pattern_targets_the_release_marker(self):
        source = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
        inlined = re.sub(r'version = ".+"', 'version = "v0.1.18"', source)

        self.assertIn('version = "v0.1.18"', inlined)
        self.assertNotIn('version = "release-tag-unset"', inlined)


if __name__ == "__main__":
    unittest.main()
