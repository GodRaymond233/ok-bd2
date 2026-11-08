import unittest

from src.compat.update_card_ui import (
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


if __name__ == "__main__":
    unittest.main()
