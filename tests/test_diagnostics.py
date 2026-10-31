import ast
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.diagnostics.bundle import (
    MAX_ARCHIVE_BYTES,
    MAX_DIAGNOSTIC_FRAME_LOOKBACK_SECONDS,
    ReportBundleBuilder,
)
from src.diagnostics.log_collection import collect, sources_for, timestamp
from src.diagnostics.models import DiagnosticSnapshot
from src.diagnostics.redaction import DiagnosticRedactor
from src.diagnostics.runtime import RuntimeEvidence
from src.diagnostics.service import DiagnosticsManager


class _TaskStub:
    paused = False

    def __init__(self):
        self.start_time = time.time() - 5
        self.info = {
            "状态": "等待商店页面",
            "当前阶段": "购买商品",
            "鼠标点击": "购买按钮 x=0.5 y=0.6",
            "OCR 文本": "不应进入受限任务摘要",
        }

    def info_snapshot(self):
        return dict(self.info)


class _RacyInfoDict(dict):
    """Dict whose copy fails the way concurrent mutation does.

    Overriding ``__iter__`` keeps CPython's dict-merge off the exact-dict fast
    path so the copy goes through ``keys()``; both raise RuntimeError.
    """

    def __iter__(self):
        raise RuntimeError("dictionary changed size during iteration")

    def keys(self):
        raise RuntimeError("dictionary changed size during iteration")


class _InteractionStub:
    def __init__(self, idle=True):
        self.idle = idle
        self.wait_calls = []

    def wait_until_idle(self, timeout):
        self.wait_calls.append(timeout)
        return self.idle


class _ExecutorStub:
    def __init__(self, frame, interaction, *, paused=False):
        self._frame = frame
        self._last_frame_time = time.time()
        self._interaction = interaction
        self.paused = paused
        self.current_task = _TaskStub()
        self.pause_calls = 0
        self.start_calls = 0

    @property
    def interaction(self):
        return self._interaction

    def nullable_frame(self):
        if self.pause_calls:
            raise AssertionError("frame must be copied before pausing")
        return self._frame

    def pause(self):
        self.pause_calls += 1
        self.paused = True
        return True

    def start(self):
        self.start_calls += 1
        self.paused = False


class _CaptureMethodStub:
    @staticmethod
    def get_name():
        return "WGC"

    @staticmethod
    def get_frame():
        raise AssertionError("diagnostics must not capture from the UI thread")


class _DeviceManagerStub:
    def __init__(self, interaction):
        self.capture_method = _CaptureMethodStub()
        self.interaction = interaction


class DiagnosticLogEvidenceTest(unittest.TestCase):
    def test_failure_frames_survive_recovery_restart_and_current_screenshot_option(self):
        from src.tasks.BaseBD2Task import BaseBD2Task

        class FailedTask(BaseBD2Task):
            def run(self):
                self.info["当前阶段"] = "按钮确认"
                self.capture_frame()
                # Framework sleeps invalidate executor.nullable_frame().
                self._executor._frame = None
                self._save_flow_diagnostic("button_not_found")
                self.picture.fill(220)
                self.info["当前阶段"] = "返回主页"
                self.capture_frame()
                return False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = object.__new__(FailedTask)
            task.info = {"api_key": "secret-example"}
            task.info_set = lambda key, value: task.info.__setitem__(key, value)
            task.picture = np.full((48, 64, 3), 20, np.uint8)
            task.next_frame = lambda: task.picture
            task._executor = SimpleNamespace(
                trigger_tasks=[], _frame=None, _last_frame_time=0,
                nullable_frame=lambda: None, device_manager=SimpleNamespace(capture_method=None),
            )
            recorder = RuntimeEvidence(root)
            try:
                with patch("src.diagnostics.runtime._recorder", recorder), patch(
                    "src.diagnostics.runtime.time.time", return_value=time.time() - 7200,
                ):
                    self.assertFalse(task.run())
                    stored = recorder.snapshot()
                self.assertEqual(2, len(stored["failures"][0]["frames"]))
                self.assertFalse((root / "probe_outputs").exists())
            finally:
                recorder.stop()
            manager = DiagnosticsManager(
                project_root=root, output_dir=root / "out", app_version="test",
            )
            snapshot = replace(
                manager.prepare(), task_started_at=time.time() - 1,
                frame=np.full((48, 64, 3), 250, np.uint8),
                report_context={
                    "source": "failure_prompt", "failure_run": stored["failures"][0]["run"],
                },
            )
            for include_current in (True, False):
                with self.subTest(include_current=include_current):
                    result = manager.build_report(
                        snapshot, "两小时后重启上报", include_screenshot=include_current,
                    )
                    with zipfile.ZipFile(result.archive_path) as archive:
                        self.assertIsNone(archive.testzip())
                        self.assertEqual(
                            include_current, "screenshots/current.webp" in archive.namelist(),
                        )
                        manifest = json.loads(archive.read("manifest.json"))
                        self.assertEqual(include_current, manifest["capture"]["included"])
                        self.assertEqual(snapshot.report_context, manifest["report_context"])
                        frames = manifest["capture"]["diagnostic_frames"]
                        self.assertEqual(2, len(frames))
                        for entry, expected in zip(frames, (20, 220)):
                            image = cv2.imdecode(
                                np.frombuffer(archive.read(entry["path"]), np.uint8), 1,
                            )
                            self.assertAlmostEqual(expected, float(image.mean()), delta=2)
                            self.assertEqual(snapshot.failures[0]["run"], entry["run"])
                        self.assertEqual("按钮确认", frames[0]["info"]["当前阶段"])
                        self.assertEqual("返回主页", frames[1]["info"]["当前阶段"])
                        state = archive.read("state/failures.json").decode()
                        self.assertNotIn('"data"', state)
                        self.assertNotIn("secret-example", state)
                        self.assertNotIn(stored["failures"][0]["frames"][0]["data"], state)
                        self.assertNotIn("diagnostic_frames_declined", manifest["omissions"])
                        summary = archive.read("summary.txt").decode()
                        self.assertIn("报告入口：任务失败后询问", summary)
                        self.assertIn("本次 2 张，历史/补充 0 张", summary)
                        self.assertTrue(all(entry["path"] in summary for entry in frames))
                        for line in archive.read("checksums.sha256").decode().splitlines():
                            digest, name = line.split("  ", 1)
                            self.assertEqual(digest, hashlib.sha256(archive.read(name)).hexdigest())
            store = root / "logs" / "diagnostic-evidence.json"
            corrupted = json.loads(store.read_bytes())
            corrupted["failures"][0]["frames"][0]["data"] = "not-base64"
            corrupted["failures"][0]["frames"][1]["data"] = {"invalid": True}
            store.write_text(json.dumps(corrupted), encoding="utf-8")
            result = manager.build_report(
                manager.prepare(), "损坏图片仍保留故障日志", include_screenshot=True,
            )
            self.assertEqual([], result.manifest["capture"]["diagnostic_frames"])
            self.assertTrue(any(
                "failure_frame_invalid" in item for item in result.manifest["omissions"]
            ))
            self.assertTrue(any(
                "invalid_frame_payload" in item for item in result.manifest["omissions"]
            ))

    def test_failure_frames_are_bounded_and_encoding_errors_preserve_task_outcome(self):
        from src.diagnostics.runtime import FAILURE_FRAME_LIMIT
        from src.tasks.BaseBD2Task import BaseBD2Task

        class Crash(BaseBD2Task):
            def run(self):
                for value in (20, 80, 140):
                    self._save_flow_diagnostic("stage_failed")
                    self.picture.fill(value)
                raise ValueError("original failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = RuntimeEvidence(root)
            task = object.__new__(Crash)
            task.info = {}
            task.picture = np.full((24, 32, 3), 0, np.uint8)
            task._executor = SimpleNamespace(
                trigger_tasks=[], nullable_frame=lambda: task.picture, _last_frame_time=0,
            )
            try:
                with patch("src.diagnostics.runtime._recorder", recorder):
                    for _ in range(6):
                        with self.assertRaisesRegex(ValueError, "original failure"):
                            task.run()
                    evidence = recorder.snapshot()
                    self.assertEqual(5, len(evidence["failures"]))
                    self.assertTrue(all(len(f["frames"]) == 2 for f in evidence["failures"]))
                    self.assertTrue(all(f["frames_omitted"] >= 1 for f in evidence["failures"]))
                    self.assertNotIn('"data"', json.dumps(evidence["events"]))
                    for failure in evidence["failures"]:
                        for frame in failure["frames"]:
                            self.assertLessEqual(
                                len(frame["data"]), (FAILURE_FRAME_LIMIT + 2) // 3 * 4,
                            )
                    manager = DiagnosticsManager(
                        project_root=root, output_dir=root / "out", app_version="test",
                    )
                    snapshot = replace(manager.prepare(), report_context={
                        "source": "failure_prompt", "failure_run": evidence["failures"][-2]["run"],
                    })
                    result = manager.build_report(snapshot, "多次失败", include_screenshot=False)
                    self.assertEqual(10, len(result.manifest["capture"]["diagnostic_frames"]))
                    self.assertNotIn("diagnostic_frames_count_limit", result.manifest["omissions"])
                    with zipfile.ZipFile(result.archive_path) as archive:
                        summary = archive.read("summary.txt").decode()
                        self.assertIn("本次 2 张，历史/补充 8 张", summary)
                        for frame in result.manifest["capture"]["diagnostic_frames"]:
                            label = (
                                "本次" if frame["run"] == snapshot.report_context["failure_run"]
                                else "历史/补充"
                            )
                            self.assertTrue(any(
                                line.startswith(f"- {label} |") and frame["path"] in line
                                for line in summary.splitlines()
                            ))
                    batch_runs = {failure["run"] for failure in snapshot.failures[-2:]}
                    batch_snapshot = replace(
                        snapshot,
                        failures=tuple(
                            {**failure, "parent": "batch"} if failure["run"] in batch_runs
                            else failure for failure in snapshot.failures
                        ),
                        report_context={"source": "failure_prompt", "failure_run": "batch"},
                    )
                    batch_result = manager.build_report(
                        batch_snapshot, "批次多项失败", include_screenshot=False,
                    )
                    with zipfile.ZipFile(batch_result.archive_path) as archive:
                        self.assertIn(
                            "本次 4 张，历史/补充 6 张", archive.read("summary.txt").decode(),
                        )
                    with patch("src.diagnostics.bundle._encode_frame", side_effect=OSError("disk")):
                        with self.assertRaisesRegex(ValueError, "original failure"):
                            task.run()
                    failure = recorder.snapshot()["failures"][-1]
                    self.assertTrue(all("omission" in frame for frame in failure["frames"]))
                    result = manager.build_report(
                        manager.prepare(), "截图失败仍可上报", include_screenshot=True,
                    )
                    self.assertTrue(any(
                        "frame_capture_failed:OSError" in item
                        for item in result.manifest["omissions"]
                    ))
            finally:
                recorder.stop()

    def test_failure_notification_survives_evidence_write_errors(self):
        from unittest.mock import Mock

        from src.tasks.BaseBD2Task import BaseBD2Task

        class Crash(BaseBD2Task):
            def run(self):
                raise ValueError("original task error")

        for failed_write in (1, 2):
            with (
                self.subTest(failed_write=failed_write),
                tempfile.TemporaryDirectory() as directory,
            ):
                recorder = RuntimeEvidence(Path(directory))
                task = object.__new__(Crash)
                task.info = {}
                task._executor = SimpleNamespace(trigger_tasks=[], nullable_frame=lambda: None)
                notify = Mock()
                recorder.failure_notifier = notify
                persist = recorder._persist
                writes = []

                def write():
                    writes.append(True)
                    if len(writes) == failed_write:
                        raise OSError("evidence directory unavailable")
                    return persist()

                try:
                    with patch("src.diagnostics.runtime._recorder", recorder), patch.object(
                        recorder, "_persist", side_effect=write,
                    ):
                        with self.assertRaisesRegex(ValueError, "original task error"):
                            task.run()
                        state = recorder.snapshot()
                    notify.assert_called_once()
                    self.assertEqual(1, len(state["failures"]))
                    self.assertIn("evidence_write_failed:OSError", state["omissions"])
                    action_at = round(time.time()) + 0.1234563
                    with patch("src.diagnostics.runtime.time.time", return_value=action_at):
                        recorder.report_action("prompt_accepted", notify.call_args.args[0]["run"])
                    state = recorder.snapshot()
                    self.assertEqual("report_prompt_accepted", state["events"][-1]["event"])
                    self.assertEqual(notify.call_args.args[0]["run"], state["events"][-1]["run"])
                    self.assertEqual(1, len(state["failures"]))
                    builder = ReportBundleBuilder(
                        project_root=Path(directory), output_dir=Path(directory)/"out",
                        app_version="test",
                    )
                    _, events, _ = builder.capture_logs(
                        datetime.fromtimestamp(action_at).astimezone().isoformat(timespec="microseconds"),
                    )
                    self.assertTrue(any(e["event"] == "report_prompt_accepted" for e in events))
                finally:
                    recorder.stop()

    def test_pvp_page_failure_and_quick_hunt_cleanup_keep_original_scene(self):
        from src.tasks.BaseBD2Task import BaseBD2Task, CartridgeSpecialPageResult
        from src.tasks.quick_hunt import QuickHuntFeatureMixin

        class PvpEntry(BaseBD2Task):
            def run(self):
                return self.open_cartridge_quick_switcher(
                    ensure_home=lambda: True, click_quick_switch=lambda: True,
                    confirm_quick_switch_page=lambda: False,
                )

        class Hunt(QuickHuntFeatureMixin, BaseBD2Task):
            def run(self):
                return self.run_quick_hunt()

        with tempfile.TemporaryDirectory() as directory:
            recorder = RuntimeEvidence(Path(directory))
            pvp, hunt = object.__new__(PvpEntry), object.__new__(Hunt)
            for task in (pvp, hunt):
                task.info = {}
                task.config = {}
                task.info_set = lambda key, value: None
                task._executor = SimpleNamespace(
                    trigger_tasks=[], nullable_frame=lambda: np.full((24, 32, 3), 40, np.uint8),
                )
            pvp._recent_cartridge_is_pvp = lambda: False
            pvp._sleep_after_recognition = lambda: None
            pvp.operate_click = lambda *args, **kwargs: None
            pvp._handle_recent_cartridge_special_pages = (
                lambda **kwargs: CartridgeSpecialPageResult.ABSENT
            )
            hunt._status_set = lambda *args: None
            hunt._quick_hunt_open_menu = lambda: "opened"
            hunt._quick_hunt_run_rice_scheduler = lambda: False

            def return_home():
                hunt._executor.nullable_frame = lambda: np.full((24, 32, 3), 230, np.uint8)
                return True

            hunt._quick_hunt_return_home = return_home
            try:
                with patch("src.diagnostics.runtime._recorder", recorder):
                    self.assertFalse(pvp.run())
                    self.assertFalse(hunt.run())
                evidence = recorder.snapshot()["failures"]
                self.assertEqual(
                    "cartridge_quick_switch_page_failed", evidence[0]["frames"][0]["reason"],
                )
                self.assertEqual("quick_hunt_failed", evidence[1]["frames"][0]["reason"])
                self.assertEqual(2, len(evidence[1]["frames"]))
            finally:
                recorder.stop()

    def test_login_timeout_is_captured_but_polling_and_explicit_stop_are_not(self):
        from src.tasks.BaseBD2Task import BaseBD2Task
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        class Login(AutoLoginTask):
            def run(self):
                if self.timed_out:
                    self._handle_login_wait_timeout(301.0)
                return False

        class Stopped(BaseBD2Task):
            def run(self):
                self._enabled = False
                self._task_stop_requested()
                return False

        with tempfile.TemporaryDirectory() as directory:
            recorder = RuntimeEvidence(Path(directory))
            task = object.__new__(Login)
            task.info = {}
            task.config = {}
            task._login_clicked_at = 0
            task.info_set = lambda key, value: task.info.__setitem__(key, value)
            task.log_warning = lambda *args, **kwargs: None
            task._reset_login_state = lambda: None
            task._executor = SimpleNamespace(
                trigger_tasks=[task], nullable_frame=lambda: np.zeros((24, 32, 3), np.uint8),
            )
            stopped = object.__new__(Stopped)
            stopped.info = {}
            stopped._executor = SimpleNamespace(trigger_tasks=[])
            try:
                with patch("src.diagnostics.runtime._recorder", recorder):
                    task.timed_out = False
                    self.assertFalse(task.run())
                    task.timed_out = True
                    self.assertFalse(task.run())
                    self.assertFalse(stopped.run())
                evidence = recorder.snapshot()
                self.assertEqual(1, len(evidence["failures"]))
                self.assertEqual(
                    "auto_login_home_timeout_failed",
                    evidence["failures"][0]["frames"][0]["reason"],
                )
                self.assertEqual("stopped", evidence["events"][-1]["event"])
            finally:
                recorder.stop()

    def test_daily_batch_own_failure_records_frame_and_safe_plain_task_info(self):
        from src.tasks.DailyBatchTask import DailyBatchChild, DailyBatchTask
        from src.tasks.DailyTask import DailyTask

        with tempfile.TemporaryDirectory() as directory:
            recorder = RuntimeEvidence(Path(directory))
            task = object.__new__(DailyBatchTask)
            task.info = {"api_key": "secret-example"}
            task.config = {"启用": True}
            task.child_tasks = (DailyBatchChild("missing", DailyTask),)
            task._auto_login_pending = lambda: False
            task._take_run_mode = lambda mode: "all"
            task.info_set = lambda key, value: task.info.__setitem__(key, value)
            task.log_info = lambda *args, **kwargs: None
            task.logger = SimpleNamespace(error=lambda *args: None)
            task._write_log_images = lambda *args: None
            task._executor = SimpleNamespace(
                trigger_tasks=[], get_task_by_class=lambda cls: None,
                nullable_frame=lambda: np.zeros((24, 32, 3), np.uint8),
            )
            try:
                with patch("src.diagnostics.runtime._recorder", recorder), patch(
                    "src.tasks.run_history.default_store", return_value=None,
                ), patch("src.tasks.scheduler.default_store", return_value=None):
                    self.assertFalse(task.run())
                failures = recorder.snapshot()["failures"]
                self.assertEqual(1, len(failures))
                self.assertEqual("DailyBatchTask", failures[0]["task"])
                self.assertEqual("missing", failures[0]["info"]["失败"])
                self.assertIn("data", failures[0]["frames"][0])
                self.assertNotIn("secret-example", json.dumps(failures))
            finally:
                recorder.stop()

    def test_framework_exception_observer_survives_handler_reconfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            task = _TaskStub()
            recorder = RuntimeEvidence(
                Path(directory), executor_getter=lambda: SimpleNamespace(current_task=task)
            )
            logger = logging.getLogger("diagnostic-observer-test")
            logger.addFilter(recorder)
            logger.propagate = False
            threads = []
            try:
                with patch(
                    "src.diagnostics.bundle.flush_ok_logging",
                    side_effect=lambda: threads.append(threading.current_thread().name) or True,
                ):
                    logger.handlers = [logging.NullHandler()]
                    logger.error("OCR:CLIENT_LOGIC_ERROR exception stopped")
                    logger.error(
                        "TaskExecutor:task exception stopped\nTraceback\nValueError: before run"
                    )
                    evidence = recorder.snapshot()
                self.assertEqual(1, len(evidence["failures"]))
                self.assertEqual("exception", evidence["failures"][0]["event"])
                self.assertEqual(["BD2DiagnosticEvidence"], threads)
            finally:
                logger.removeFilter(recorder)
                recorder.stop()

    def test_failure_store_limits_and_unreadable_store_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = RuntimeEvidence(root)
            task = _TaskStub()
            fixed = time.time()
            try:
                with patch("src.diagnostics.runtime.time.time", return_value=fixed):
                    for i in range(7):
                        recorder.event(
                            {"id": str(i), "task": "Task", "object": task, "started": fixed},
                            "failed",
                        )
                    evidence = recorder.snapshot()
                self.assertEqual(5, len(evidence["failures"]))
                self.assertEqual("2", evidence["failures"][0]["run"])
                self.assertIn("failure_count_limit", " ".join(evidence["omissions"]))
                self.assertLessEqual(recorder.path.stat().st_size, 8 * 1024 * 1024)
            finally:
                recorder.stop()
            recorder.path.write_bytes(b"not json")
            reopened = RuntimeEvidence(root)
            try:
                evidence = reopened.snapshot()
                self.assertEqual([], evidence["failures"])
                self.assertIn("persisted_evidence_unreadable", evidence["omissions"])
            finally:
                reopened.stop()

    def test_collection_failure_preserves_snapshot_and_restores_running_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            (root / "logs" / "ok-script.log").write_text("bounded fallback", encoding="utf-8")
            manager = DiagnosticsManager(
                project_root=root, output_dir=root / "out", app_version="test"
            )
            with patch("src.diagnostics.bundle.collect", side_effect=ValueError("parse failed")):
                snapshot = manager.prepare()
            self.assertIn("digest_failed:ValueError", snapshot.logs["omissions"])
            self.assertIn("bounded fallback", snapshot.logs["files"]["recent.log"])
            executor = _ExecutorStub(np.zeros((2, 2, 3)), _InteractionStub())
            with patch.object(manager.builder, "capture_logs", side_effect=OSError("read failed")):
                with self.assertRaises(OSError):
                    manager.prepare(executor=executor)
            self.assertEqual(1, executor.start_calls)

    def test_rotation_failure_survives_large_active_tail_and_references_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            before = "2026-09-11 23:59:40,000 INFO MainThread ocr_zone:阈值=0.85 坐标=(21,42)\n"
            failure = (
                "2026-09-11 23:59:59,000 WARNING MainThread DailyBatchTask:广场失败，批次中止\n"
                "Traceback (most recent call last):\n  original stack\nValueError: exact reason\n"
            )
            rotation = root / "logs" / "ok-script.2026-09-11.log"
            rotation.write_bytes((before + failure * 3).encode("utf-8"))
            active = root / "logs" / "ok-script.log"
            active.write_text(
                "2026-09-12 00:01:00,000 INFO MainThread OCR:CLIENT_LOGIC_ERROR\n" * 1000,
                encoding="utf-8",
            )
            paths = sources_for(root)
            self.assertIn(rotation.resolve(), paths)
            result = collect(
                paths,
                timestamp("2026-09-12T00:02:00"),
                DiagnosticRedactor(),
                budget=24 * 1024,
                scan_bytes=64 * 1024,
            )
            digest = json.loads(result["files"]["recent-digest.json"])
            self.assertEqual(1, len(digest["incidents"]))
            incident = digest["incidents"][0]
            self.assertEqual(3, incident["event_count"])
            self.assertEqual("candidate_only", incident["classification"])
            self.assertIn(before.rstrip(), result["files"]["incidents.log"])
            self.assertIn(failure.rstrip(), result["files"]["incidents.log"])
            ref = incident["first_event"]
            lines = result["files"][ref["file"]].splitlines()
            self.assertIn("WARNING", lines[ref["line"]])
            start, end = ref["source_bytes"]
            self.assertEqual(failure.encode(), rotation.read_bytes()[start:end])
            self.assertLessEqual(sum(len(t.encode()) for t in result["files"].values()), 24 * 1024)

    def test_scan_limits_and_unparseable_or_oversized_records_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for i in range(10):
                path = root / f"source-{i}.log"
                path.write_text(
                    "2026-09-12 00:00:00,000 ERROR MainThread failure\n" + "stack\n" * 300,
                    encoding="utf-8",
                )
                paths.append(path)
            result = collect(
                paths,
                timestamp("2026-09-12T00:01:00"),
                DiagnosticRedactor(),
                budget=16 * 1024,
                scan_bytes=1000,
            )
            self.assertTrue(any("source_file_limit" in x for x in result["omissions"]))
            self.assertTrue(any("scan_byte_limit" in x for x in result["omissions"]))
            self.assertNotIn("stack", result["files"]["recent.log"])
            unknown = root / "unknown.log"
            unknown.write_text("api_key=hidden\nno timestamp\n", encoding="utf-8")
            result = collect([unknown], time.time(), DiagnosticRedactor())
            self.assertIn("unparsed_time", " ".join(result["omissions"]))
            self.assertNotIn("hidden", str(result))
            huge = root / "huge.log"
            huge.write_text(
                "2026-09-12 00:00:00,000 ERROR MainThread failure\n" + "stack\n" * 6000,
                encoding="utf-8",
            )
            result = collect(
                [huge], timestamp("2026-09-12T00:01:00"), DiagnosticRedactor(), budget=16 * 1024
            )
            self.assertIn("output_budget", " ".join(result["omissions"]))
            self.assertNotIn("stack", result["files"]["recent.log"])

    def test_dialog_snapshot_freezes_logs_before_more_records_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            path = root / "logs" / "ok-script.log"
            path.write_text("before opening dialog\n", encoding="utf-8")
            manager = DiagnosticsManager(
                project_root=root, output_dir=root / "out", app_version="test"
            )
            snapshot = manager.prepare()
            path.rename(root / "logs" / "ok-script.2026-09-11.log")
            path.write_text("after opening dialog\n", encoding="utf-8")
            result = manager.build_report(snapshot, "延迟反馈", include_screenshot=False)
            with zipfile.ZipFile(result.archive_path) as archive:
                text = archive.read("logs/recent.log").decode()
                self.assertIn("before opening dialog", text)
                self.assertNotIn("after opening dialog", text)
                self.assertEqual(
                    snapshot.captured_at,
                    json.loads(archive.read("state/task-summary.json"))["captured_at"],
                )

    def test_failure_survives_restart_and_source_deletion_with_original_info(self):
        from src.tasks.BaseBD2Task import BaseBD2Task

        class FailedTask(BaseBD2Task):
            def run(self):
                self.info["状态"] = "业务失败"
                return False

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            path = root / "logs" / "ok-script.log"
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S,000")
            path.write_text(f"{now} INFO MainThread OCR:0.85 x=0.6\n", encoding="utf-8")
            recorder = RuntimeEvidence(root)
            task = object.__new__(FailedTask)
            task.info = {
                "当前阶段": "购买",
                "阈值": "0.850",
                "卡带": "16",
                "count": 17,
                "api_key": "secret-example",
            }
            task._executor = SimpleNamespace(trigger_tasks=[])
            try:
                with patch("src.diagnostics.runtime._recorder", recorder):
                    self.assertIs(task.run(), False)
                evidence = recorder.snapshot()
                self.assertEqual(1, len(evidence["failures"]))
                self.assertNotIn("secret-example", str(evidence))
                self.assertEqual("0.850", evidence["failures"][0]["info"]["阈值"])
                self.assertEqual(17, evidence["failures"][0]["info"]["count"])
            finally:
                recorder.stop()
            path.unlink()
            manager = DiagnosticsManager(
                project_root=root, output_dir=root / "out", app_version="test"
            )
            snapshot = manager.prepare()
            self.assertEqual("FailedTask", snapshot.failures[0]["task"])
            self.assertEqual("failed", snapshot.failures[0]["event"])
            result = manager.build_report(snapshot, "重启后上报", include_screenshot=False)
            with zipfile.ZipFile(result.archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertIn("ok-script.log", manifest["log_sources"])
                log_names = [n for n in archive.namelist() if n.startswith("logs/")]
                self.assertLessEqual(sum(len(archive.read(n)) for n in log_names), 4 * 1024 * 1024)
                self.assertIn("OCR:0.85", "".join(archive.read(n).decode() for n in log_names))
                self.assertLessEqual(len(archive.read("logs/recent-digest.json")), 64 * 1024)
                recorded = {item["path"] for item in manifest["files"]}
                self.assertEqual(
                    set(archive.namelist()) - {"manifest.json", "checksums.sha256"}, recorded
                )

    def test_batch_parent_stop_and_trigger_poll_have_distinct_outcomes(self):
        from ok.task.exceptions import TaskDisabledException

        from src.tasks.BaseBD2Task import BaseBD2Task

        class Child(BaseBD2Task):
            def run(self):
                if self.stop_requested:
                    raise TaskDisabledException()
                return self.result

        class Batch(BaseBD2Task):
            def run(self):
                try:
                    result = self.child.run()
                    recorder.snapshot()
                    self.test_case.assertEqual([], notifications)
                    continued.append("remaining tasks finished")
                    return result
                except TaskDisabledException:
                    return False

        with tempfile.TemporaryDirectory() as directory:
            recorder = RuntimeEvidence(Path(directory))
            child, batch = object.__new__(Child), object.__new__(Batch)
            captures = []
            notifications = []
            continued = []
            recorder.failure_notifier = lambda failure: notifications.append(
                (failure, list(continued)),
            )
            batch.test_case = self

            def frame():
                captures.append(True)
                return np.zeros((24, 32, 3), np.uint8)

            for task in (child, batch):
                task.info = {}
                task._executor = SimpleNamespace(trigger_tasks=[], nullable_frame=frame)
            batch.child = child
            child.stop_requested = False
            child.result = False
            try:
                with patch("src.diagnostics.runtime._recorder", recorder):
                    batch.run()
                    child.stop_requested = True
                    batch.run()
                    child.stop_requested = False
                    child._executor.trigger_tasks = [child]
                    child.run()
                evidence = recorder.snapshot()
                self.assertEqual(1, len(evidence["failures"]))
                self.assertEqual("Batch", evidence["failures"][0]["parent_task"])
                outcomes = [event["event"] for event in evidence["events"]]
                self.assertEqual(2, outcomes.count("stopped"))
                self.assertEqual(2, outcomes.count("failed"))
                self.assertEqual(1, len(captures))
                self.assertEqual(1, len(evidence["failures"][0]["frames"]))
                self.assertEqual(1, len(notifications))
                self.assertEqual("Batch", notifications[0][0]["task_name"])
                self.assertEqual(["remaining tasks finished"], notifications[0][1])
                child._executor.trigger_tasks = []
                with patch("src.diagnostics.runtime._recorder", recorder):
                    child.run()
                recorder.snapshot()
                self.assertEqual(2, len(notifications))
                self.assertEqual("Child", notifications[1][0]["task_name"])
                child.result = True
                with patch("src.diagnostics.runtime._recorder", recorder):
                    child.run()
                recorder.snapshot()
                self.assertEqual(2, len(notifications))
            finally:
                recorder.stop()

    def test_multiline_exception_and_repeated_runtime_event_do_not_copy_again(self):
        from src.tasks.BaseBD2Task import BaseBD2Task

        class Crash(BaseBD2Task):
            def run(self):
                raise ValueError("failure reason\nsecond line")

        with tempfile.TemporaryDirectory() as directory:
            task = object.__new__(Crash)
            task.info = {"当前阶段": "入场"}
            task._executor = SimpleNamespace(trigger_tasks=[])
            recorder = RuntimeEvidence(
                Path(directory), executor_getter=lambda: SimpleNamespace(current_task=task)
            )
            notifications = []

            def notify(failure):
                notifications.append(failure)
                raise RuntimeError("report UI unavailable")

            recorder.failure_notifier = notify
            try:
                with patch("src.diagnostics.runtime._recorder", recorder):
                    with self.assertRaises(ValueError):
                        task.run()
                record = logging.LogRecord(
                    "ok", logging.ERROR, "", 0, "TaskExecutor:Crash exception stopped", (), None
                )
                recorder.emit(record)
                evidence = recorder.snapshot()
                self.assertEqual(1, len(evidence["failures"]))
                self.assertIn("Traceback", evidence["failures"][0]["detail"])
                self.assertIn("second line", evidence["failures"][0]["detail"])
                self.assertEqual(1, len(notifications))
                self.assertIn("failure_notification_failed:RuntimeError", evidence["omissions"])
                run = {
                    "id": evidence["failures"][0]["run"],
                    "task": "Crash",
                    "object": task,
                    "started": time.time(),
                }
                with patch(
                    "src.diagnostics.runtime.collect",
                    side_effect=AssertionError("duplicate capture"),
                ):
                    recorder.event(run, "failed")
                    evidence = recorder.snapshot()
                self.assertEqual(2, evidence["failures"][0]["count"])
                self.assertEqual(1, len(notifications))
            finally:
                recorder.stop()


class DiagnosticRedactorTest(unittest.TestCase):
    def test_redacts_paths_credentials_email_and_url_queries(self):
        redactor = DiagnosticRedactor(known_roots=[Path(r"C:\Users\Alice")])
        source = (
            r"File C:\Users\Alice\Documents\trace.log "
            "alice@example.com token=plain-secret "
            "Authorization: Bearer bearer-secret 'client_secret': 'json-secret' "
            "https://example.test/report?account=42"
        )

        result = redactor.redact(source)

        for secret in (
            "Alice",
            "alice@example.com",
            "plain-secret",
            "bearer-secret",
            "json-secret",
            "account=42",
        ):
            self.assertNotIn(secret, result)
        self.assertIn("<PATH>", result)
        self.assertIn("<EMAIL>", result)
        self.assertIn("<REDACTED", result)

    def test_redacts_prefixed_credentials_and_authorization_schemes(self):
        source = (
            "bot_token=abcdefgh123 auth_token=xyz bot_secret=s3cr3t "
            "db_password=hunter2 Authorization: Basic dXNlcjpwYXNz "
            "proxy_authorization=Token opaque-token Authorization=Bearer bearer-token"
        )

        result = DiagnosticRedactor().redact(source)

        for secret in (
            "abcdefgh123",
            "xyz",
            "s3cr3t",
            "hunter2",
            "dXNlcjpwYXNz",
            "opaque-token",
            "bearer-token",
        ):
            self.assertNotIn(secret, result)
        self.assertIn("bot_token=<REDACTED>", result)
        self.assertIn("auth_token=<REDACTED>", result)
        self.assertIn("bot_secret=<REDACTED>", result)
        self.assertIn("db_password=<REDACTED>", result)

    def test_redacts_suffixed_credential_keys_userinfo_urls_and_scheme_values(self):
        redactor = DiagnosticRedactor()
        source = (
            "Token = abc123secret secret_key = 'quoted-secret' "
            "password_hash: hashvalue api_key=Bearer, "
            "https://user:pass@host/path "
            "https://alice:tok@example.test/x?account=42"
        )

        result = redactor.redact(source)

        for secret in (
            "abc123secret",
            "quoted-secret",
            "hashvalue",
            "user:pass",
            "alice:tok",
            "account=42",
        ):
            self.assertNotIn(secret, result)
        self.assertIn("Token = <REDACTED>", result)
        self.assertIn("secret_key = <REDACTED>", result)
        self.assertIn("password_hash: <REDACTED>", result)
        # 值恰好是认证 scheme（无后续凭据）时仍按键值对脱敏。
        self.assertIn("api_key=<REDACTED>,", result)
        self.assertIn("https://<REDACTED>@host/path", result)
        self.assertIn("https://<REDACTED>@example.test/x?<REDACTED_QUERY>", result)

    def test_windows_path_redaction_follows_spaced_segments_only(self):
        redactor = DiagnosticRedactor()

        result = redactor.redact(
            r"Log dir C:\Program Files\ok-bd2\logs\ok-bd2.log rotated"
        )

        self.assertNotIn("C:\\Program", result)
        self.assertNotIn("ok-bd2\\logs", result)
        # 空格续段只到分隔符链结束；路径后的普通文字保持原样。
        self.assertIn("<PATH>/ok-bd2.log rotated", result)

    def test_quoted_windows_path_keeps_text_outside_quotes(self):
        redactor = DiagnosticRedactor()

        result = redactor.redact(
            r'loaded "C:\Program Files\ok-bd2\logs\ok-bd2.log" ok'
        )

        self.assertNotIn("Program Files", result)
        self.assertIn('"<PATH>/ok-bd2.log" ok', result)

    def test_redaction_keeps_plain_sentences_untouched(self):
        redactor = DiagnosticRedactor()
        text = (
            "任务执行完成，共扫描 12 个关卡，全部通过。"
            "The build passed all checks today."
        )

        self.assertEqual(text, redactor.redact(text))


class DiagnosticsManagerTest(unittest.TestCase):
    def test_prepare_failure_restores_only_its_own_pause(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DiagnosticsManager(
                project_root=Path(temp_dir), output_dir=Path(temp_dir), app_version="test",
            )
            for already_paused, fail_at_pause in ((False, True), (False, False), (True, False)):
                with self.subTest(already_paused=already_paused, fail_at_pause=fail_at_pause):
                    executor = _ExecutorStub(None, _InteractionStub(), paused=already_paused)
                    error = RuntimeError("prepare failed")

                    def failing_pause():
                        executor.paused = True
                        raise error

                    target = (
                        patch.object(executor, "pause", side_effect=failing_pause)
                        if fail_at_pause else
                        patch("src.diagnostics.service._task_snapshot", side_effect=error)
                    )
                    with target, self.assertRaises(RuntimeError) as raised:
                        manager.prepare(executor=executor)
                    self.assertIs(error, raised.exception)
                    self.assertEqual(already_paused, executor.paused)
                    self.assertEqual(0 if already_paused else 1, executor.start_calls)

    def test_prepare_reports_when_restoring_running_state_also_fails(self):
        executor = _ExecutorStub(None, _InteractionStub())
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DiagnosticsManager(
                project_root=Path(temp_dir), output_dir=Path(temp_dir), app_version="test",
            )
            with (
                patch(
                    "src.diagnostics.service._task_snapshot", side_effect=RuntimeError("snapshot"),
                ),
                patch.object(executor, "start", side_effect=RuntimeError("resume")),
                self.assertRaisesRegex(RuntimeError, "请手动继续任务"),
            ):
                manager.prepare(executor=executor)

    def test_prepare_captures_before_pause_waits_for_mouse_and_can_resume(self):
        frame = np.full((24, 32, 3), 127, dtype=np.uint8)
        interaction = _InteractionStub()
        executor = _ExecutorStub(frame, interaction)
        device_manager = _DeviceManagerStub(interaction)
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DiagnosticsManager(
                project_root=Path(temp_dir),
                output_dir=Path(temp_dir),
                app_version="0.1.test",
            )

            snapshot = manager.prepare(executor=executor, device_manager=device_manager)

            self.assertEqual(1, executor.pause_calls)
            self.assertTrue(executor.paused)
            self.assertEqual([2.0], interaction.wait_calls)
            self.assertTrue(snapshot.safe_point_reached)
            self.assertTrue(np.array_equal(frame, snapshot.frame))
            self.assertIsNot(frame, snapshot.frame)
            self.assertEqual("WGC", snapshot.capture_method)
            self.assertEqual(executor.current_task.start_time, snapshot.task_started_at)
            self.assertEqual("购买商品", snapshot.task["当前阶段"])
            self.assertNotIn("OCR 文本", snapshot.task)

            self.assertTrue(manager.resume(snapshot, executor))
            self.assertEqual(1, executor.start_calls)
            self.assertFalse(executor.paused)

    def test_prepare_prefers_background_live_preview_frame(self):
        executor_frame = np.full((24, 32, 3), 32, dtype=np.uint8)
        preview_frame = np.full((24, 32, 3), 224, dtype=np.uint8)
        interaction = _InteractionStub()
        executor = _ExecutorStub(executor_frame, interaction)
        device_manager = _DeviceManagerStub(interaction)
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DiagnosticsManager(
                project_root=Path(temp_dir),
                output_dir=Path(temp_dir),
                app_version="0.1.test",
            )

            snapshot = manager.prepare(
                executor=executor,
                device_manager=device_manager,
                preferred_frame=preview_frame,
                preferred_frame_age_seconds=0.03,
            )

        self.assertTrue(np.array_equal(preview_frame, snapshot.frame))
        self.assertIsNot(preview_frame, snapshot.frame)
        self.assertEqual(0.03, snapshot.frame_age_seconds)

    def test_prepare_records_unconfirmed_safe_point(self):
        interaction = _InteractionStub(idle=False)
        executor = _ExecutorStub(None, interaction)
        device_manager = _DeviceManagerStub(interaction)
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DiagnosticsManager(
                project_root=Path(temp_dir),
                output_dir=Path(temp_dir),
                app_version="0.1.test",
            )

            snapshot = manager.prepare(executor=executor, device_manager=device_manager)

        self.assertFalse(snapshot.safe_point_reached)
        self.assertTrue(any("鼠标操作" in warning for warning in snapshot.warnings))

    def test_prepare_reports_task_info_snapshot_failure(self):
        task = _TaskStub()
        task.info = _RacyInfoDict(task.info)
        interaction = _InteractionStub()
        executor = _ExecutorStub(None, interaction)
        executor.current_task = task
        device_manager = _DeviceManagerStub(interaction)
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DiagnosticsManager(
                project_root=Path(temp_dir),
                output_dir=Path(temp_dir),
                app_version="0.1.test",
            )

            snapshot = manager.prepare(executor=executor, device_manager=device_manager)

        # The task identity survives; only the info payload is dropped.
        self.assertEqual("_TaskStub", snapshot.task["class"])
        self.assertIn("paused", snapshot.task)
        self.assertNotIn("状态", snapshot.task)
        self.assertNotIn("当前阶段", snapshot.task)
        self.assertTrue(
            any("任务 _TaskStub 状态快照失败" in warning for warning in snapshot.warnings)
        )


class InteractionSafetyContractTest(unittest.TestCase):
    def test_all_mouse_entry_points_use_the_diagnostic_input_lock(self):
        interaction_path = (
            Path(__file__).resolve().parents[1] / "src" / "interaction" / "BD2Interaction.py"
        )
        module = ast.parse(interaction_path.read_text(encoding="utf-8"))
        interaction_class = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "BD2Interaction"
        )
        methods = {
            node.name: node
            for node in interaction_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        mouse_entry_points = {
            "click",
            "scroll",
            "operate",
            "move",
            "swipe",
            "right_click",
            "mouse_down",
            "update_mouse_pos",
            "mouse_up",
            "move_mouse_relative",
        }

        self.assertLessEqual(mouse_entry_points, methods.keys())
        for method_name in mouse_entry_points:
            with self.subTest(method=method_name):
                lock_contexts = [
                    item.context_expr
                    for node in ast.walk(methods[method_name])
                    if isinstance(node, ast.With)
                    for item in node.items
                ]
                self.assertTrue(
                    any(
                        isinstance(context, ast.Attribute)
                        and isinstance(context.value, ast.Name)
                        and context.value.id == "self"
                        and context.attr == "_input_lock"
                        for context in lock_contexts
                    ),
                    f"{method_name} must hold _input_lock",
                )


class ReportBundleBuilderTest(unittest.TestCase):
    def test_builds_bounded_redacted_standard_zip_with_valid_checksums(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output"
            logs = root / "logs"
            logs.mkdir()
            secret_path = root / "private" / "account.json"
            (logs / "ok-script.log").write_text(
                f"opening {secret_path}\napi_key=top-secret\nmail=user@example.com\n",
                encoding="utf-8",
            )
            snapshot = DiagnosticSnapshot(
                captured_at="2026-08-14T13:00:00+08:00",
                frame=np.full((120, 160, 3), 96, dtype=np.uint8),
                frame_age_seconds=0.05,
                capture_method="WGC",
                task={"class": "MapTradeTask", "当前阶段": "购买", "状态": "失败"},
                executor_was_running=True,
                report_context={"source": "manual"},
            )
            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=output,
                app_version="0.1.23",
            )

            result = builder.build(
                snapshot,
                f"读取 {secret_path} 时失败，联系 user@example.com",
                include_screenshot=True,
            )

            self.assertTrue(result.archive_path.is_file())
            self.assertLessEqual(result.archive_path.stat().st_size, MAX_ARCHIVE_BYTES)
            self.assertFalse(list(output.glob("*.tmp")))
            with zipfile.ZipFile(result.archive_path) as archive:
                names = set(archive.namelist())
                self.assertEqual(
                    {
                        "checksums.sha256",
                        "logs/recent.log",
                        "logs/recent-digest.json",
                        "logs/incidents.log",
                        "manifest.json",
                        "screenshots/current.webp",
                        "state/task-summary.json",
                        "state/failures.json",
                        "state/trace.jsonl",
                        "summary.txt",
                    },
                    names,
                )
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(1, manifest["schema_version"])
                self.assertEqual(result.report_id, manifest["report_id"])
                self.assertTrue(manifest["privacy"]["redacted"])
                self.assertFalse(manifest["privacy"]["raw_config_included"])
                self.assertTrue(manifest["capture"]["included"])
                self.assertEqual({"source": "manual"}, manifest["report_context"])
                self.assertIn("报告入口：手动生成", archive.read("summary.txt").decode())

                combined_text = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in names
                    if name.endswith((".txt", ".log", ".json", ".jsonl"))
                )
                for secret in ("top-secret", "user@example.com", str(secret_path)):
                    self.assertNotIn(secret, combined_text)

                for line in archive.read("checksums.sha256").decode("utf-8").splitlines():
                    digest, relative_path = line.split("  ", 1)
                    self.assertEqual(
                        digest,
                        hashlib.sha256(archive.read(relative_path)).hexdigest(),
                    )

    def test_declined_current_screenshot_keeps_failure_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            probe_outputs = root / "probe_outputs"
            probe_outputs.mkdir()
            frame = np.full((40, 60, 3), 128, dtype=np.uint8)
            success, encoded = cv2.imencode(".png", frame)
            self.assertTrue(success)
            (probe_outputs / "pvp_auto_battle_failed.png").write_bytes(encoded.tobytes())
            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=root / "output",
                app_version="0.1.23",
            )
            snapshot = DiagnosticSnapshot(
                captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                frame=np.zeros((20, 20, 3), dtype=np.uint8),
            )

            result = builder.build(snapshot, "点击后没有反应", include_screenshot=False)

            with zipfile.ZipFile(result.archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertNotIn("screenshots/current.webp", archive.namelist())
                self.assertIn(
                    "screenshots/diagnostic/pvp_auto_battle_failed.webp",
                    archive.namelist(),
                )
                self.assertEqual(1, len(manifest["capture"]["diagnostic_frames"]))
                self.assertIn("screenshot_declined", manifest["omissions"])
                self.assertNotIn("diagnostic_frames_declined", manifest["omissions"])

    def test_packages_recent_failure_frames_but_not_regular_probe_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            probe_outputs = root / "probe_outputs"
            probe_outputs.mkdir()
            frame = np.full((40, 60, 3), 128, dtype=np.uint8)
            failed_name = "map_trade_\u4e70_failed.png"
            success, encoded = cv2.imencode(".png", frame)
            self.assertTrue(success)
            (probe_outputs / failed_name).write_bytes(encoded.tobytes())
            (probe_outputs / "map_trade_return_home_error.png").write_bytes(encoded.tobytes())
            (probe_outputs / "ordinary_probe.png").write_bytes(encoded.tobytes())
            (probe_outputs / "pvp_stage_not_found.png").write_bytes(encoded.tobytes())
            (probe_outputs / "quick_hunt_start_unconfirmed.png").write_bytes(encoded.tobytes())

            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=root / "output",
                app_version="1.1.2",
            )
            captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
            result = builder.build(
                DiagnosticSnapshot(captured_at=captured_at),
                "跑商买入失败",
                include_screenshot=True,
            )

            with zipfile.ZipFile(result.archive_path) as archive:
                names = set(archive.namelist())
                self.assertIn(
                    "screenshots/diagnostic/map_trade_\u4e70_failed.webp",
                    names,
                )
                self.assertIn(
                    "screenshots/diagnostic/map_trade_return_home_error.webp",
                    names,
                )
                self.assertNotIn("screenshots/diagnostic/ordinary_probe.webp", names)
                manifest = json.loads(archive.read("manifest.json"))
                diagnostic_frames = manifest["capture"]["diagnostic_frames"]
                self.assertEqual(4, len(diagnostic_frames))
                self.assertEqual(
                    {
                        failed_name,
                        "map_trade_return_home_error.png",
                        "pvp_stage_not_found.png",
                        "quick_hunt_start_unconfirmed.png",
                    },
                    {item["source"] for item in diagnostic_frames},
                )

    def test_excludes_failure_frames_outside_current_report_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            probe_outputs = root / "probe_outputs"
            probe_outputs.mkdir()
            frame = np.full((40, 60, 3), 128, dtype=np.uint8)
            success, encoded = cv2.imencode(".png", frame)
            self.assertTrue(success)
            old_path = probe_outputs / "old_task_failed.png"
            recent_path = probe_outputs / "current_task_error.png"
            old_path.write_bytes(encoded.tobytes())
            recent_path.write_bytes(encoded.tobytes())

            captured_epoch = time.time()
            old_epoch = captured_epoch - MAX_DIAGNOSTIC_FRAME_LOOKBACK_SECONDS - 1
            os.utime(old_path, (old_epoch, old_epoch))
            os.utime(recent_path, (captured_epoch - 2, captured_epoch - 2))

            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=root / "output",
                app_version="1.1.2",
            )
            captured_at = (
                datetime.fromtimestamp(captured_epoch).astimezone().isoformat(timespec="seconds")
            )
            result = builder.build(
                DiagnosticSnapshot(captured_at=captured_at),
                "当前任务失败",
                include_screenshot=True,
            )

            with zipfile.ZipFile(result.archive_path) as archive:
                names = set(archive.namelist())
                self.assertNotIn("screenshots/diagnostic/old_task_failed.webp", names)
                self.assertIn("screenshots/diagnostic/current_task_error.webp", names)

    def test_task_start_time_excludes_previous_run_failure_frames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            probe_outputs = root / "probe_outputs"
            probe_outputs.mkdir()
            frame = np.full((40, 60, 3), 128, dtype=np.uint8)
            success, encoded = cv2.imencode(".png", frame)
            self.assertTrue(success)
            previous_path = probe_outputs / "previous_run_failed.png"
            current_path = probe_outputs / "current_run_failed.png"
            previous_path.write_bytes(encoded.tobytes())
            current_path.write_bytes(encoded.tobytes())

            captured_epoch = time.time()
            os.utime(previous_path, (captured_epoch - 20, captured_epoch - 20))
            os.utime(current_path, (captured_epoch - 2, captured_epoch - 2))
            task_started_at = captured_epoch - 10

            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=root / "output",
                app_version="1.1.2",
            )
            captured_at = (
                datetime.fromtimestamp(captured_epoch).astimezone().isoformat(timespec="seconds")
            )
            result = builder.build(
                DiagnosticSnapshot(
                    captured_at=captured_at,
                    task_started_at=task_started_at,
                ),
                "当前任务失败",
                include_screenshot=True,
            )

            with zipfile.ZipFile(result.archive_path) as archive:
                names = set(archive.namelist())
                self.assertNotIn(
                    "screenshots/diagnostic/previous_run_failed.webp",
                    names,
                )
                self.assertIn("screenshots/diagnostic/current_run_failed.webp", names)

    def test_manifest_records_incomplete_log_flush(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=root / "output",
                app_version="0.1.23",
            )
            snapshot = DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00")

            with patch("src.diagnostics.bundle.flush_ok_logging", return_value=False):
                result = builder.build(snapshot, "日志可能尚未刷新", include_screenshot=False)

            with zipfile.ZipFile(result.archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertIn("log_flush_incomplete", manifest["omissions"])

    def test_requires_a_description(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=root,
                app_version="0.1.23",
            )
            snapshot = DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00")

            with self.assertRaisesRegex(ValueError, "问题现象"):
                builder.build(snapshot, "   ", include_screenshot=False)


if __name__ == "__main__":
    unittest.main()
