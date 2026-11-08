import unittest

from src.debug_profile import (
    configure_debug_profile,
    next_development_version,
    release_version,
)


class DebugProfileTest(unittest.TestCase):
    def test_release_version_and_next_patch_are_strict(self):
        self.assertEqual((0, 1, 21), release_version("v0.1.21"))
        self.assertEqual("0.1.22", next_development_version("v0.1.21"))
        with self.assertRaises(ValueError):
            release_version("v0.1.21-beta")

    def test_debug_profile_uses_next_release_and_exact_title(self):
        config = {
            "debug": False,
            "version": "0.1.1",
            "gui_title": "ok-bd2",
            "my_app": ["src.globals", "Globals"],
        }

        self.assertEqual(
            "0.1.22",
            configure_debug_profile(config, release_tag="v0.1.21"),
        )
        self.assertTrue(config["debug"])
        self.assertEqual("0.1.22", config["version"])
        self.assertEqual("ok-bd2 0.1.22 开发版", config["gui_title"])
        self.assertEqual(["src.globals", "Globals"], config["my_app"])


if __name__ == "__main__":
    unittest.main()
