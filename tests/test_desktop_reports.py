"""Preview fidelity and the report session's pause, export and resume contract."""

import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.desktop.preview import LivePreview
from src.diagnostics.runtime import RuntimeEvidence
from src.diagnostics.service import DiagnosticsManager
from tests.test_desktop_device_settings import make_client


class PreviewTest(unittest.TestCase):
    def test_preview_is_bounded_but_report_keeps_the_original_and_disconnect_drops_cache(self):
        frame = np.full((1080, 1920, 3), 128, np.uint8)
        executor = SimpleNamespace(nullable_frame=lambda: frame, _last_frame_time=time.time())
        method = Mock()
        preview = LivePreview(
            SimpleNamespace(
                executor=executor, device_manager=SimpleNamespace(capture_method=method)
            )
        )
        encoded = preview.image()
        self.assertEqual((540, 960), cv2.imdecode(np.frombuffer(encoded, np.uint8), 1).shape[:2])
        original, age = preview.latest_frame()
        self.assertEqual(frame.shape, original.shape)
        self.assertLess(age, 2)
        original[:] = 0
        self.assertTrue(np.all(preview.latest_frame()[0] == 128))
        method.get_frame.assert_not_called()
        method.connected.return_value = False
        with self.assertRaisesRegex(ValueError, "连接"):
            preview.image()
        self.assertEqual((None, None), preview.latest_frame())


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

    def test_prepare_blocks_mutations_and_finishing_requires_explicit_resume(self):
        session = self.prepare()
        self.assertTrue(self.executor.paused)
        self.assertTrue(self.headless.diagnostic_pause)
        self.assertTrue(self.client.get("/api/ui/capture").json()["diagnostic_pause"])
        self.assertEqual(session, self.client.get("/api/bd2/report").json()["session_id"])
        for path, body in (
            ("/api/executor/resume", {}),
            ("/api/capture-methods/select", {"id": "auto"}),
            ("/api/settings/Basic Options/config", {"key": "Trigger Interval", "value": 5}),
        ):
            self.assertEqual(400, self.client.post(path, json=body).status_code, path)
        self.assertEqual(
            200,
            self.client.post("/api/bd2/report/finish", json={"session_id": session}).status_code,
        )
        self.executor.start.assert_not_called()
        self.assertFalse(self.headless.diagnostic_pause)
        self.assertTrue(self.executor.paused)

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

    def test_failure_keeps_session_and_never_resumes_task_and_inputs_are_validated(self):
        session = self.prepare()
        for body in (
            {"description": "短"},
            {"description": "x" * 2001},
            {"description": "有效的问题描述", "session_id": "stale"},
        ):
            response = self.client.post(
                "/api/bd2/report/build", json={"session_id": session, **body}
            )
            self.assertEqual(400, response.status_code)
        with patch.object(self.reports.manager, "build_report", side_effect=OSError("磁盘已满")):
            response = self.client.post(
                "/api/bd2/report/build",
                json={"session_id": session, "description": "有效的问题描述"},
            )
        self.assertEqual(400, response.status_code)
        self.assertIn("磁盘已满", response.text)
        self.assertEqual(session, self.client.get("/api/bd2/report").json()["session_id"])
        self.executor.start.assert_not_called()

    def test_failure_prompt_defers_deduplicates_and_accepts_or_declines_without_auto_export(self):
        evidence = RuntimeEvidence(self.root)
        self.addCleanup(evidence.stop)
        self.reports.evidence = evidence
        evidence.failure_notifier = self.reports._failure_notifier
        self.addCleanup(self.reports.stop)
        failure = {"event": "failed", "parent": None, "run": "failed-run",
                   "task_name": "一键完成日常", "info": {"失败": "快速狩猎"}}
        evidence._notify_failure(dict(failure, parent="batch", run="child"))
        self.assertIsNone(self.client.get("/api/bd2/report").json()["failure_prompt"])
        self.executor.current_task = object()
        evidence._notify_failure(failure)
        self.assertIsNone(self.client.get("/api/bd2/report").json()["failure_prompt"])
        self.executor.current_task = None
        prompt = self.client.get("/api/bd2/report").json()["failure_prompt"]
        self.assertEqual("failed-run", prompt["run"])
        self.assertEqual("快速狩猎", prompt["failed"])
        self.assertEqual(400, self.client.post("/api/bd2/report/failure", json={
            "run": "stale", "generate": True}).status_code)
        declined = self.client.post("/api/bd2/report/failure", json={
            "run": prompt["run"], "generate": False}).json()
        self.assertIsNone(declined["session_id"])
        self.assertIsNone(declined["result"])
        evidence._notify_failure(failure)
        self.assertIsNone(self.client.get("/api/bd2/report").json()["failure_prompt"])
        evidence._notify_failure(dict(failure, run="next-run"))
        accepted = self.client.post("/api/bd2/report/failure", json={
            "run": "next-run", "generate": True})
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertIsNone(accepted.json()["result"])
        self.assertEqual({"source": "failure_prompt", "failure_run": "next-run"},
                         self.reports.snapshot.report_context)
        evidence._notify_failure(dict(failure, run="while-form-open"))
        self.assertIsNone(self.client.get("/api/bd2/report").json()["failure_prompt"])
        self.client.post("/api/bd2/report/finish",
                         json={"session_id": accepted.json()["session_id"]})
        self.assertEqual("while-form-open",
                         self.client.get("/api/bd2/report").json()["failure_prompt"]["run"])
        self.assertEqual([], list(self.root.glob("reports/*.zip")))
        self.executor.start.assert_not_called()

    def test_failure_notification_never_waits_for_report_compression(self):
        import threading

        self.reports._lock.acquire()
        worker = threading.Thread(target=self.reports.on_failure,
            args=({"run": "worker-failure", "task_name": "task", "info": {}},))
        try:
            worker.start()
            worker.join(1)
            self.assertFalse(worker.is_alive())
        finally:
            self.reports._lock.release()
            worker.join(2)
        self.assertEqual("worker-failure", self.reports.state()["failure_prompt"]["run"])
