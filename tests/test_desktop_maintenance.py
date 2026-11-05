import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.desktop.backups import BackupStore, prepare_installed_configs


class ConfigurationBackupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.working = Path(self.temporary.name) / "data/apps/ok-bd2/working"
        self.working.mkdir(parents=True)
        self.store = BackupStore(self.working)
        self.store.configs.mkdir()
        self.data = self.store.configs / "task_run_history.json"
        self.data.write_text('{"version":1,"progress":"before"}', "utf-8")

    def test_first_upgrade_captures_unknown_data_before_marker_and_retains_five(self):
        first = self.store.prepare_startup("v1.4.12")
        self.assertEqual(first["version"], "unknown")
        self.assertNotIn(".core_version", self.store.read(first["id"])["files"])
        self.assertIsNone(self.store.prepare_startup("1.4.12"))
        for patch_version in range(13, 19):
            self.store.prepare_startup(f"1.4.{patch_version}")
        self.assertEqual(len(self.store.list()), 5)
        self.assertEqual(self.store.list()[0]["version"], "1.4.17")
        self.assertFalse(self.store.root.is_relative_to(self.working))

    def test_failed_backup_prevents_startup_marker_from_advancing(self):
        with patch("src.desktop.backups.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.prepare_startup("1.4.12")
        self.assertFalse((self.store.configs / ".core_version").exists())
        self.assertEqual(self.store.list(), [])
        self.assertIn("before", self.data.read_text("utf-8"))

    def test_downgrade_refuses_to_load_newer_configs_until_a_matching_restore(self):
        self.store.prepare_startup("1.4.12")
        snapshot = self.store.create("1.4.12", "manual")
        self.store.prepare_startup("1.4.13")
        self.data.write_text("newer data", "utf-8")
        with self.assertRaisesRegex(ValueError, "较新核心"):
            self.store.prepare_startup("1.4.12")
        self.assertEqual(self.data.read_text("utf-8"), "newer data")
        safety = self.store.restore(snapshot["id"], "1.4.12")
        self.assertEqual(safety["version"], "1.4.13")
        self.assertIsNone(self.store.prepare_startup("1.4.12"))
        self.assertIn("before", self.data.read_text("utf-8"))

    def test_restore_verifies_data_and_version_then_replaces_the_complete_directory(self):
        snapshot = self.store.create("v1.4.12", "manual")
        self.data.write_text("after", "utf-8")
        (self.store.configs / "added.json").write_text("{}", "utf-8")
        with self.assertRaises(ValueError):
            self.store.restore(snapshot["id"], "1.4.11")
        self.assertEqual(self.data.read_text("utf-8"), "after")
        safety = self.store.restore(snapshot["id"], "1.4.12")
        self.assertIn("before", self.data.read_text("utf-8"))
        self.assertFalse((self.store.configs / "added.json").exists())
        self.assertEqual(
            (self.store.root / safety["id"] / "configs/task_run_history.json").read_text("utf-8"),
            "after",
        )

    def test_corruption_or_traversal_cannot_replace_current_data(self):
        snapshot = self.store.create("1.4.12", "manual")
        (self.store.root / snapshot["id"] / "configs/task_run_history.json").write_text(
            "tampered", "utf-8"
        )
        for backup_id in (snapshot["id"], "../working", "C:/configs"):
            with self.subTest(backup_id=backup_id), self.assertRaises(ValueError):
                self.store.restore(backup_id, "1.4.12")
        self.assertIn("before", self.data.read_text("utf-8"))
        self.assertTrue(self.store.list()[0]["invalid"])

    def test_interrupted_directory_swap_recovers_original_before_loading_configs(self):
        self.store._root()
        previous = self.store.configs.with_name("configs.before-restore-" + "a" * 32)
        self.store.configs.rename(previous)
        (self.store.root / "restore.json").write_text(
            json.dumps({"previous": previous.name}), "utf-8"
        )
        self.store.prepare_startup("1.4.12")
        self.assertIn("before", self.data.read_text("utf-8"))
        self.assertFalse(previous.exists())

    def test_existing_source_checkout_does_not_gain_startup_backups(self):
        with patch("src.desktop.backups.Path.cwd", return_value=Path(self.temporary.name)):
            prepare_installed_configs({"version": "1.4.12"})
        self.assertFalse((Path(self.temporary.name) / "configs").exists())


if __name__ == "__main__":
    unittest.main()
