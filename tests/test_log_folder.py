import tempfile
import unittest
from pathlib import Path

from src.ui.log_folder import ensure_log_folder, log_folder_path


class LogFolderTest(unittest.TestCase):
    def test_log_folder_path_uses_logs_under_base_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)

            self.assertEqual((base_path / "logs").resolve(), log_folder_path(base_path))

    def test_ensure_log_folder_creates_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = ensure_log_folder(Path(temp_dir))

            self.assertTrue(folder.exists())
            self.assertTrue(folder.is_dir())


if __name__ == "__main__":
    unittest.main()
