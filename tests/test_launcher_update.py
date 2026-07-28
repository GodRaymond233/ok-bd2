import re
import unittest

from src.config import config


class LauncherUpdateConfigTest(unittest.TestCase):
    def test_launcher_update_targets_verified_v0114_launcher(self):
        self.assertEqual(
            {
                "to_version": "1.1.9",
                "zip_url": (
                    "https://github.com/GodRaymond233/ok-bd2/releases/download/"
                    "v0.1.14/ok-bd2-win32.zip"
                ),
                "sha256": (
                    "9f9537587e2cf2925bd182a245710da554a0571a3504c77ac4043fbd2247a6d0"
                ),
            },
            config["update_pyappify"],
        )

    def test_launcher_hash_is_lowercase_sha256(self):
        self.assertRegex(
            config["update_pyappify"]["sha256"],
            re.compile(r"^[0-9a-f]{64}$"),
        )


if __name__ == "__main__":
    unittest.main()
