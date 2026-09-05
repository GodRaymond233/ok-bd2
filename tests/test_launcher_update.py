import os
import re
import unittest
from unittest.mock import Mock, patch

from src.compat.starter_launch import (
    starter_launch_arguments,
    starter_launch_uri,
    wrap_starter_execute,
)
from src.compat.launcher_update_notice import launcher_download_url, launcher_requires_reinstall
from src.config import config


class LauncherUpdateConfigTest(unittest.TestCase):
    def starter_uri_context(self, game_id: str = "10000001"):
        return patch("src.compat.starter_launch.get_launch_game_id", return_value=game_id)

    def test_starter_launch_includes_official_shortcut_uri(self):
        with self.starter_uri_context():
            self.assertEqual(
                '"browndust2:games/10000001?usn=0"',
                starter_launch_arguments(
                    r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe"
                ),
            )

    def test_starter_launch_supports_another_registered_game_id(self):
        with self.starter_uri_context("10000002"):
            self.assertEqual(
                '"browndust2:games/10000002?usn=0"',
                starter_launch_arguments(
                    r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe"
                ),
            )
            self.assertEqual(starter_launch_uri(), "browndust2:games/10000002?usn=0")

    def test_starter_launch_preserves_additional_arguments(self):
        with self.starter_uri_context():
            self.assertEqual(
                '"browndust2:games/10000001?usn=0" --future-option',
                starter_launch_arguments(
                    r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe",
                    "--future-option",
                ),
            )

    def test_starter_launch_accepts_configured_executable_names(self):
        with patch.dict(
            os.environ,
            {
                "OK_BD2_LAUNCHER_EXE": (
                    "PrimaryStarter.exe, FallbackStarter.exe"
                )
            },
        ):
            for executable_name in ("PrimaryStarter.exe", "FallbackStarter.exe"):
                with self.subTest(executable_name=executable_name):
                    with self.starter_uri_context():
                        self.assertEqual(
                            '"browndust2:games/10000001?usn=0"',
                            starter_launch_arguments(rf"D:\Launchers\{executable_name}"),
                        )

    def test_non_starter_launch_arguments_are_unchanged(self):
        self.assertEqual(
            "--existing-option",
            starter_launch_arguments(r"D:\Games\OtherLauncher.exe", "--existing-option"),
        )

    def test_starter_execute_wrapper_forwards_uri_to_ok_script(self):
        original_execute = Mock(return_value=True)
        wrapped_execute = wrap_starter_execute(original_execute)

        with self.starter_uri_context():
            self.assertTrue(
                wrapped_execute(
                    r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe",
                    start_method="start",
                )
            )
        original_execute.assert_called_once_with(
            r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe",
            arguments='"browndust2:games/10000001?usn=0"',
            start_method="start",
        )

    def test_dx11_option_is_hidden_and_rejects_enablement_for_starter_launch(self):
        basic_options = config["global_configs"][0]
        self.assertEqual("Basic Options", basic_options.name)
        self.assertFalse(basic_options.default_config["Launch with DX11"])
        self.assertTrue(basic_options.config_type["Launch with DX11"]["hidden"])
        self.assertEqual(
            (False, "ok-bd2 通过 Neowiz Starter 启动游戏，暂不支持由程序强制传递 DX11 参数。"),
            basic_options.validator("Launch with DX11", True),
        )
        self.assertEqual((True, ""), basic_options.validator("Launch with DX11", False))

    def test_launcher_update_targets_verified_v123_launcher(self):
        self.assertEqual(
            {
                "to_version": "1.2.3",
                "zip_url": (
                    "https://github.com/GodRaymond233/ok-bd2/releases/download/"
                    "v1.2.0/ok-bd2-win32.zip"
                ),
                "sha256": (
                    "98d13a723d28e3c6f41c73869024d6bdbca91239719dad547d994dccf95aa862"
                ),
            },
            config["update_pyappify"],
        )

    def test_launcher_zip_url_matches_to_version_release_payload(self):
        zip_url = config["update_pyappify"]["zip_url"]
        self.assertIn("/v1.2.0/", zip_url)
        self.assertTrue(zip_url.endswith("ok-bd2-win32.zip"))

    def test_config_exposes_download_link_for_update_errors(self):
        links = config["links"]["default"]
        self.assertEqual(
            "https://github.com/GodRaymond233/ok-bd2/releases/latest",
            links["download"],
        )

    def test_launcher_hash_is_lowercase_sha256(self):
        self.assertRegex(
            config["update_pyappify"]["sha256"],
            re.compile(r"^[0-9a-f]{64}$"),
        )

    def test_old_launcher_requires_reinstall_notice(self):
        self.assertTrue(launcher_requires_reinstall("1.1.9"))
        self.assertFalse(launcher_requires_reinstall("1.2.3"))
        self.assertFalse(launcher_requires_reinstall(None))

    def test_launcher_notice_uses_default_download_link(self):
        self.assertEqual(
            "https://github.com/GodRaymond233/ok-bd2/releases/latest",
            launcher_download_url(config),
        )


if __name__ == "__main__":
    unittest.main()
