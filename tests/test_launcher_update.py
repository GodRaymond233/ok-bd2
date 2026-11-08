import os
import unittest
from unittest.mock import Mock, patch

from src.compat.starter_launch import (
    starter_launch_arguments,
    wrap_starter_execute,
)
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


if __name__ == "__main__":
    unittest.main()
