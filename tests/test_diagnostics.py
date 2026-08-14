import hashlib
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

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
