import os
import re
import unittest
from unittest.mock import Mock, patch

from src.compat.starter_launch import (
    BROWNDUST2_LAUNCH_URI,
    starter_launch_arguments,
    wrap_starter_execute,
)
from src.config import config


class LauncherUpdateConfigTest(unittest.TestCase):
    def test_starter_launch_includes_official_shortcut_uri(self):
        self.assertEqual(
            f'"{BROWNDUST2_LAUNCH_URI}"',
            starter_launch_arguments(
                r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe"
            ),
        )

    def test_starter_launch_preserves_additional_arguments(self):
        self.assertEqual(
            f'"{BROWNDUST2_LAUNCH_URI}" --future-option',
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
                    self.assertEqual(
                        f'"{BROWNDUST2_LAUNCH_URI}"',
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

        self.assertTrue(
            wrapped_execute(
                r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe",
                start_method="start",
            )
        )
        original_execute.assert_called_once_with(
            r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe",
            arguments=f'"{BROWNDUST2_LAUNCH_URI}"',
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
