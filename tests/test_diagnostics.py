import ast
import hashlib
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.diagnostics.bundle import MAX_ARCHIVE_BYTES, ReportBundleBuilder
from src.diagnostics.models import DiagnosticSnapshot
from src.diagnostics.redaction import DiagnosticRedactor
from src.diagnostics.service import DiagnosticsManager


class _TaskStub:
    paused = False

    def __init__(self):
        self.info = {
            "状态": "等待商店页面",
            "当前阶段": "购买商品",
            "鼠标点击": "购买按钮 x=0.5 y=0.6",
            "OCR 文本": "不应进入受限任务摘要",
        }


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


class DiagnosticsManagerTest(unittest.TestCase):
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
            any(
                "任务 _TaskStub 状态快照失败" in warning for warning in snapshot.warnings
            )
        )


class InteractionSafetyContractTest(unittest.TestCase):
    def test_all_mouse_entry_points_use_the_diagnostic_input_lock(self):
        interaction_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "interaction"
            / "BD2Interaction.py"
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
                        "manifest.json",
                        "screenshots/current.webp",
                        "state/task-summary.json",
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

    def test_declined_screenshot_is_recorded_without_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builder = ReportBundleBuilder(
                project_root=root,
                output_dir=root / "output",
                app_version="0.1.23",
            )
            snapshot = DiagnosticSnapshot(
                captured_at="2026-08-14T13:00:00+08:00",
                frame=np.zeros((20, 20, 3), dtype=np.uint8),
            )

            result = builder.build(snapshot, "点击后没有反应", include_screenshot=False)

            with zipfile.ZipFile(result.archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertNotIn("screenshots/current.webp", archive.namelist())
                self.assertIn("screenshot_declined", manifest["omissions"])

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
