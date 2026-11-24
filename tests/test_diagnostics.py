import ast
import hashlib
import json
import os
import tempfile
import time
import unittest
import zipfile
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
                trigger_tasks=[task],
                nullable_frame=lambda: np.zeros((24, 32, 3), np.uint8),
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


class DiagnosticRedactorTest(unittest.TestCase):
    def test_redacts_paths_identities_and_credentials(self):
        redactor = DiagnosticRedactor(known_roots=[Path(r"C:\Users\Alice")])
        for source, secrets, markers in (
            (
                r"File C:\Users\Alice\Documents\trace.log alice@example.com",
                ("Alice", "alice@example.com"),
                ("<PATH>", "<EMAIL>"),
            ),
            (
                "token=plain-secret Authorization: Bearer bearer-secret "
                "'client_secret': 'json-secret'",
                ("plain-secret", "bearer-secret", "json-secret"),
                ("<REDACTED",),
            ),
            ("https://example.test/report?account=42", ("account=42",), ("example.test",)),
            (
                "bot_token=abcdefgh123 auth_token=xyz bot_secret=s3cr3t db_password=hunter2",
                ("abcdefgh123", "xyz", "s3cr3t", "hunter2"),
                (
                    "bot_token=<REDACTED>",
                    "auth_token=<REDACTED>",
                    "bot_secret=<REDACTED>",
                    "db_password=<REDACTED>",
                ),
            ),
            (
                "Authorization: Basic dXNlcjpwYXNz proxy_authorization=Token opaque-token "
                "Authorization=Bearer bearer-token",
                ("dXNlcjpwYXNz", "opaque-token", "bearer-token"),
                ("<REDACTED",),
            ),
        ):
            with self.subTest(source=source):
                result = redactor.redact(source)
                for secret in secrets:
                    self.assertNotIn(secret, result)
                for marker in markers:
                    self.assertIn(marker, result)


class DiagnosticsManagerTest(unittest.TestCase):
    def test_report_preparation_waits_for_mouse_and_restores_only_its_own_pause(self):
        frame = np.full((24, 32, 3), 127, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = DiagnosticsManager(
                project_root=Path(temp_dir), output_dir=Path(temp_dir), app_version="test"
            )
            for idle in (False, True):
                with self.subTest(mouse_idle=idle):
                    interaction = _InteractionStub(idle=idle)
                    executor = _ExecutorStub(frame, interaction)
                    snapshot = manager.prepare(
                        executor=executor, device_manager=_DeviceManagerStub(interaction)
                    )
                    self.assertEqual(1, executor.pause_calls)
                    self.assertTrue(executor.paused)
                    self.assertEqual([2.0], interaction.wait_calls)
                    self.assertEqual(idle, snapshot.safe_point_reached)
                    self.assertEqual(not idle, any("鼠标操作" in w for w in snapshot.warnings))
                    np.testing.assert_array_equal(frame, snapshot.frame)
                    self.assertIsNot(frame, snapshot.frame)
                    self.assertEqual("WGC", snapshot.capture_method)
                    self.assertEqual(executor.current_task.start_time, snapshot.task_started_at)
                    self.assertEqual("购买商品", snapshot.task["当前阶段"])
                    self.assertNotIn("OCR 文本", snapshot.task)
                    self.assertTrue(manager.resume(snapshot, executor))
                    self.assertEqual(1, executor.start_calls)
                    self.assertFalse(executor.paused)
            for already_paused, fail_at_pause in ((False, True), (False, False), (True, False)):
                with self.subTest(already_paused=already_paused, fail_at_pause=fail_at_pause):
                    executor = _ExecutorStub(None, _InteractionStub(), paused=already_paused)
                    error = RuntimeError("prepare failed")

                    def failing_pause():
                        executor.paused = True
                        raise error

                    failure = (
                        patch.object(executor, "pause", side_effect=failing_pause)
                        if fail_at_pause
                        else patch("src.diagnostics.service._task_snapshot", side_effect=error)
                    )
                    with failure, self.assertRaises(RuntimeError) as raised:
                        manager.prepare(executor=executor)
                    self.assertIs(error, raised.exception)
                    self.assertEqual(already_paused, executor.paused)
                    self.assertEqual(0 if already_paused else 1, executor.start_calls)


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

    def test_only_recent_failure_images_are_included_even_when_current_frame_is_declined(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outputs = root / "probe_outputs"
            outputs.mkdir()
            now = time.time()
            recent = {
                "pvp_auto_battle_failed",
                "map_trade_买_failed",
                "map_trade_return_home_error",
                "pvp_stage_not_found",
                "quick_hunt_start_unconfirmed",
            }
            frame = np.full((40, 60, 3), 128, dtype=np.uint8)
            success, encoded = cv2.imencode(".png", frame)
            self.assertTrue(success)
            for name in recent | {"ordinary_probe", "old_task_failed"}:
                path = outputs / (name + ".png")
                path.write_bytes(encoded.tobytes())
                age = MAX_DIAGNOSTIC_FRAME_LOOKBACK_SECONDS + 10 if name == "old_task_failed" else 2
                os.utime(path, (now - age, now - age))
            builder = ReportBundleBuilder(
                project_root=root, output_dir=root / "out", app_version="test"
            )
            snapshot = DiagnosticSnapshot(
                captured_at=datetime.fromtimestamp(now).astimezone().isoformat(), frame=frame
            )
            result = builder.build(snapshot, "反馈", include_screenshot=False)
            with zipfile.ZipFile(result.archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(
                    {name + ".png" for name in recent},
                    {item["source"] for item in manifest["capture"]["diagnostic_frames"]},
                )
                self.assertEqual(
                    {f"screenshots/diagnostic/{name}.webp" for name in recent},
                    {name for name in archive.namelist() if name.startswith("screenshots/")},
                )
                self.assertIn("screenshot_declined", manifest["omissions"])
                self.assertNotIn("diagnostic_frames_declined", manifest["omissions"])
