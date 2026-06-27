import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.game_path import resolve_game_exe_path, seed_device_manager_game_path


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


if __name__ == "__main__":
    unittest.main()
