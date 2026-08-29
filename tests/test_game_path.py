import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.game_path import (
    calculate_pc_exe_path,
    get_launch_game_id,
    get_launcher_exe_names,
    resolve_game_exe_path,
    resolve_launcher_exe_path,
    seed_device_manager_game_path,
    seed_device_manager_launch_path,
)


class LaunchGameIdTest(unittest.TestCase):
    def test_env_override_wins(self):
        self.assertEqual(
            get_launch_game_id({"OK_BD2_LAUNCH_GAME_ID": "10000009"}),
            "10000009",
        )

    def test_falls_back_to_default_without_registration(self):
        with patch("src.game_path._neowiz_registered_games", return_value=[]):
            self.assertEqual(get_launch_game_id(env={}), "10000001")

    def test_prefers_registration_with_existing_install_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir) / "BrownDust2_10000002"
            install_dir.mkdir()
            registrations = [
                ("10000001", r"D:\missing\Browndust2_10000001", "BrownDust II.exe"),
                ("10000002", str(install_dir), "BrownDust II.exe"),
            ]
            with patch("src.game_path._neowiz_registered_games", return_value=registrations):
                self.assertEqual(get_launch_game_id(env={}), "10000002")

    def test_uses_registration_even_when_path_missing(self):
        registrations = [
            ("10000002", r"E:\gone\Browndust2_10000002", "BrownDust II.exe"),
        ]
        with patch("src.game_path._neowiz_registered_games", return_value=registrations):
            self.assertEqual(get_launch_game_id(env={}), "10000002")

    def test_resolve_game_exe_path_uses_registered_install(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir) / "Browndust2_10000002"
            install_dir.mkdir()
            game_path = install_dir / "BrownDust II.exe"
            game_path.write_bytes(b"")
            registrations = [
                ("10000002", str(install_dir), "BrownDust II.exe"),
            ]

            with (
                patch("src.game_path.find_running_game_path", return_value=""),
                patch("src.game_path._registry_install_values", return_value=[]),
                patch("src.game_path._neowiz_registered_games", return_value=registrations),
            ):
                self.assertEqual(
                    resolve_game_exe_path(env={}),
                    str(game_path),
                )


class GamePathTest(unittest.TestCase):
    def test_resolve_game_exe_path_uses_existing_env_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            game_path = Path(temp_dir) / "BrownDust II.exe"
            game_path.write_bytes(b"")

            with patch.dict(os.environ, {"OK_BD2_GAME_PATH": str(game_path)}):
                self.assertEqual(resolve_game_exe_path(), str(game_path))

    def test_resolve_game_exe_path_uses_running_path_before_default_install(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            game_path = Path(temp_dir) / "BrownDust II.exe"
            game_path.write_bytes(b"")

            with patch("src.game_path.find_running_game_path", return_value=""):
                self.assertEqual(
                    resolve_game_exe_path(running_path=game_path, env={}),
                    str(game_path),
                )

    def test_seed_device_manager_game_path_sets_pc_full_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            game_path = Path(temp_dir) / "BrownDust II.exe"
            game_path.write_bytes(b"")

            class DeviceManager:
                config = {"pc_full_path": ""}

            with patch.dict(os.environ, {"OK_BD2_GAME_PATH": str(game_path)}):
                self.assertEqual(seed_device_manager_game_path(DeviceManager), str(game_path))

            self.assertEqual(DeviceManager.config["pc_full_path"], str(game_path))

    def test_resolve_launcher_exe_path_uses_existing_env_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_path = Path(temp_dir) / "Browndust2Starter.exe"
            launcher_path.write_bytes(b"")

            env = {"OK_BD2_LAUNCHER_PATH": str(launcher_path)}
            self.assertEqual(resolve_launcher_exe_path(env=env), str(launcher_path))

    def test_resolve_launcher_exe_path_prefers_running_starter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_path = Path(temp_dir) / "Browndust2Starter.exe"
            launcher_path.write_bytes(b"")

            with patch(
                "src.game_path.find_running_launcher_path",
                return_value=str(launcher_path),
            ):
                self.assertEqual(resolve_launcher_exe_path(env={}), str(launcher_path))

    def test_launcher_exe_names_accept_comma_separated_overrides(self):
        self.assertEqual(
            ["PrimaryStarter.exe", "FallbackStarter.exe"],
            get_launcher_exe_names(
                {
                    "OK_BD2_LAUNCHER_EXE": (
                        " PrimaryStarter.exe, FallbackStarter.exe "
                    )
                }
            ),
        )

    def test_resolve_launcher_exe_path_uses_programdata_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_path = (
                Path(temp_dir)
                / "Neowiz"
                / "Browndust2Starter"
                / "Browndust2Starter.exe"
            )
            launcher_path.parent.mkdir(parents=True)
            launcher_path.write_bytes(b"")

            with (
                patch("src.game_path.find_running_launcher_path", return_value=""),
                patch("src.game_path._registry_install_values", return_value=[]),
            ):
                self.assertEqual(
                    resolve_launcher_exe_path(env={"PROGRAMDATA": temp_dir}),
                    str(launcher_path),
                )

    def test_resolve_launcher_exe_path_uses_registry_install_location(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_path = Path(temp_dir) / "Browndust2Starter.exe"
            launcher_path.write_bytes(b"")

            with (
                patch("src.game_path.find_running_launcher_path", return_value=""),
                patch(
                    "src.game_path._registry_install_values",
                    return_value=[str(launcher_path.parent)],
                ),
            ):
                self.assertEqual(
                    resolve_launcher_exe_path(env={"PROGRAMDATA": temp_dir + "-missing"}),
                    str(launcher_path),
                )

    def test_calculate_pc_exe_path_does_not_fall_back_to_game_executable(self):
        game_path = Path("D:/Games/BrownDust II.exe")
        with patch("src.game_path.resolve_launcher_exe_path", return_value=""):
            self.assertEqual(calculate_pc_exe_path(game_path), "")

    def test_seed_device_manager_launch_path_sets_pc_full_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher_path = Path(temp_dir) / "Browndust2Starter.exe"
            launcher_path.write_bytes(b"")

            class DeviceManager:
                config = {"pc_full_path": ""}

            env = {"OK_BD2_LAUNCHER_PATH": str(launcher_path)}
            self.assertEqual(
                seed_device_manager_launch_path(DeviceManager, env=env),
                str(launcher_path),
            )
            self.assertEqual(DeviceManager.config["pc_full_path"], str(launcher_path))


if __name__ == "__main__":
    unittest.main()
