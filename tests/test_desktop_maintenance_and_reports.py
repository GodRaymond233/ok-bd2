import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.desktop.backups import BackupStore
from src.diagnostics.service import DiagnosticsManager
from tests.helpers.desktop import make_client


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
        with patch("src.desktop.backups.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.prepare_startup("1.4.12")
        self.assertFalse((self.store.configs / ".core_version").exists())
        self.assertEqual(self.store.list(), [])
        first = self.store.prepare_startup("v1.4.12")
        self.assertEqual(first["version"], "unknown")
        self.assertNotIn(".core_version", self.store.read(first["id"])["files"])
        self.assertIsNone(self.store.prepare_startup("1.4.12"))
        manual = self.store.create("1.4.12", "manual")
        for patch_version in range(13, 19):
            self.store.prepare_startup(f"1.4.{patch_version}")
        self.assertEqual(len(self.store.list()), 6)
        self.assertIn(manual["id"], {item["id"] for item in self.store.list()})
        self.assertEqual(self.store.list()[0]["version"], "1.4.17")
        self.assertFalse(self.store.root.is_relative_to(self.working))

    def test_downgrade_refuses_to_load_newer_configs_until_a_matching_restore(self):
        self.store.prepare_startup("1.4.12")
        snapshot = self.store.create("1.4.12", "manual")
        self.store.prepare_startup("1.4.13")
        self.data.write_text("newer data", "utf-8")
        from src.desktop.backups import ConfigVersionError

        with self.assertRaisesRegex(ConfigVersionError, "较新版本") as raised:
            self.store.prepare_startup("1.4.12")
        self.assertIn(snapshot["id"], [item["id"] for item in raised.exception.backups])
        self.assertEqual(self.data.read_text("utf-8"), "newer data")
        for backup_id in ("../working", "C:/configs"):
            with self.subTest(backup_id=backup_id), self.assertRaises(ValueError):
                self.store.restore(backup_id, "1.4.12")
            self.assertEqual("newer data", self.data.read_text("utf-8"))
        safety = self.store.restore(snapshot["id"], "1.4.12")
        self.assertEqual(safety["version"], "1.4.13")
        self.assertIsNone(self.store.prepare_startup("1.4.12"))
        self.assertIn("before", self.data.read_text("utf-8"))

        (self.store.root / snapshot["id"] / "configs/task_run_history.json").write_text(
            "tampered", "utf-8"
        )
        with self.assertRaises(ValueError):
            self.store.restore(snapshot["id"], "1.4.12")
        self.assertIn("before", self.data.read_text("utf-8"))
        self.assertTrue(next(b for b in self.store.list() if b["id"] == snapshot["id"])["invalid"])

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


class ReportRoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = make_client(self)
        self.reports = self.client.app.state.reports
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.reports.manager = DiagnosticsManager(
            project_root=self.root, output_dir=self.root / "reports", app_version="test"
        )
        self.executor.nullable_frame = lambda: None
        self.executor.pause = Mock(side_effect=lambda: setattr(self.executor, "paused", True))
        self.executor.start = Mock(side_effect=lambda: setattr(self.executor, "paused", False))

    def prepare(self):
        response = self.client.post("/api/bd2/report/prepare")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["session_id"]

    def test_only_the_same_uninterrupted_run_can_resume_and_stale_sessions_are_refused(self):
        for interrupt in ("none", "pause", "changed", "legacy"):
            with self.subTest(interrupt=interrupt):
                self.executor.paused = False
                self.executor.current_task = None
                self.executor.start.reset_mock()
                self.reports.legacy_check = None
                session = self.prepare()
                if interrupt == "pause":
                    self.client.post("/api/executor/pause")
                elif interrupt == "changed":
                    self.executor.current_task = SimpleNamespace(enabled=False, start_time=1)
                elif interrupt == "legacy":
                    self.reports.legacy_check = lambda: "旧版正在运行"
                response = self.client.post(
                    "/api/bd2/report/finish", json={"session_id": session, "resume": True}
                )
                self.assertEqual(200 if interrupt == "none" else 400, response.status_code)
                if interrupt == "none":
                    self.executor.start.assert_called_once()
                else:
                    self.executor.start.assert_not_called()
                    self.client.post("/api/bd2/report/finish", json={"session_id": session})
                self.assertEqual(
                    400,
                    self.client.post(
                        "/api/bd2/report/finish", json={"session_id": session}
                    ).status_code,
                )

    def test_zip_leaves_out_the_current_frame_and_is_revealed_by_registered_id(self):
        self.executor.paused = True
        frame = np.full((720, 1280, 3), 100, np.uint8)
        self.reports.preview.latest_frame = lambda: (frame, 0.1)
        session = self.prepare()
        response = self.client.post(
            "/api/bd2/report/build",
            json={"session_id": session, "description": "没有找到预期的任务结果"},
        )
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()["result"]
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual("manual", manifest["report_context"]["source"])
            self.assertIn("state/task-summary.json", archive.namelist())
            self.assertNotIn("screenshots/current.webp", archive.namelist())
        with patch("ok.util.explorer.reveal_in_explorer") as reveal:
            self.assertEqual(400, self.client.post("/api/bd2/reports/unknown/reveal").status_code)
            reveal.assert_not_called()
            self.assertEqual(
                200, self.client.post(f"/api/bd2/reports/{result['id']}/reveal").status_code
            )
            reveal.assert_called_once_with(Path(result["path"]))
        self.assertTrue(self.executor.paused)
        self.executor.start.assert_not_called()

    def test_failure_notification_never_waits_for_report_compression(self):
        import threading

        self.reports._lock.acquire()
        worker = threading.Thread(
            target=self.reports.on_failure,
            args=({"run": "worker-failure", "task_name": "task", "info": {}},),
        )
        try:
            worker.start()
            worker.join(1)
            self.assertFalse(worker.is_alive())
        finally:
            self.reports._lock.release()
            worker.join(2)
        self.assertEqual("worker-failure", self.reports.state()["failure_prompt"]["run"])
