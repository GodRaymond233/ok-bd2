import unittest

from src.compat.update_card_ui import (
    MIN_CHECK_UPDATES_LAUNCHER_VERSION,
    launcher_supports_update_check,
    parse_launcher_version,
)


class LauncherUpdateSupportTest(unittest.TestCase):
    def test_parse_launcher_version(self):
        self.assertEqual((1, 1, 9), parse_launcher_version("1.1.9"))
        self.assertEqual((1, 2, 3), parse_launcher_version("v1.2.3"))
        self.assertIsNone(parse_launcher_version("V1.2.3"))
        self.assertIsNone(parse_launcher_version(None))
        self.assertIsNone(parse_launcher_version("dev"))
        self.assertIsNone(parse_launcher_version(""))

    def test_launcher_supports_update_check_threshold(self):
        self.assertIsNone(launcher_supports_update_check(None))
        self.assertIsNone(launcher_supports_update_check("dev"))
        self.assertFalse(launcher_supports_update_check("1.1.9"))
        self.assertTrue(
            launcher_supports_update_check(
                ".".join(str(part) for part in MIN_CHECK_UPDATES_LAUNCHER_VERSION)
            )
        )
        self.assertTrue(launcher_supports_update_check("1.2.3"))


if __name__ == "__main__":
    unittest.main()
