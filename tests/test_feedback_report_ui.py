import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from ok import og
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import PushButton, Theme, qconfig

from src.diagnostics.models import DiagnosticSnapshot
from src.ui.feedback_report import (
    FeedbackReportDialog,
    _ReportBuildJob,
    install_feedback_report,
)
from src.ui.live_screenshot import LiveScreenshotWidget


class _StartTabStub(QWidget):
    def __init__(self):
        super().__init__()
        self.debug_layout = QHBoxLayout(self)
        self.export_log_button = PushButton("Export Logs")
        self.debug_layout.addWidget(self.export_log_button)


class _ReportManagerStub:
    def __init__(self, *, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = []

    def build_report(self, snapshot, description, *, include_screenshot):
        self.calls.append((snapshot, description, include_screenshot))
        if self.error is not None:
            raise self.error
        return self.result


class _CaptureFuture:
    def __init__(self, result):
        self._result = result
        self.callbacks = []

    def result(self):
        return self._result

    def add_done_callback(self, callback):
        self.callbacks.append(callback)


class _CaptureExecutor:
    def __init__(self):
        self.submissions = []
        self.shutdown_calls = []

    def submit(self, function):
        future = _CaptureFuture((None, "下一帧"))
        self.submissions.append((function, future))
        return future

    def shutdown(self, **kwargs):
        self.shutdown_calls.append(kwargs)


class FeedbackReportUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_config = getattr(og, "config", None)
        self.original_theme = qconfig.theme
        og.config = {"version": "0.1.test"}

    def tearDown(self):
        og.config = self.original_config
        qconfig.set(qconfig.themeMode, self.original_theme, save=False)

    def test_install_adds_one_primary_entry_and_keeps_raw_export(self):
        tab = _StartTabStub()

        install_feedback_report(tab)
        install_feedback_report(tab)

        self.assertTrue(tab._bd2_feedback_report_installed)
        self.assertEqual("生成问题报告", tab.feedback_report_button.text())
        self.assertEqual("导出原始日志", tab.export_log_button.text())
        self.assertEqual(2, tab.debug_layout.count())
        tab.close()

    def test_dialog_disables_screenshot_consent_when_no_frame_exists(self):
        dialog = FeedbackReportDialog(
            DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00")
        )

        self.assertFalse(dialog.include_screenshot.isChecked())
        self.assertFalse(dialog.include_screenshot.isEnabled())
        dialog.close()

    def test_create_button_stays_disabled_until_description_entered(self):
        dialog = FeedbackReportDialog(
            DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00")
        )

        self.assertFalse(dialog.create_button.isEnabled())
        dialog.description_edit.setPlainText("跑商砍价后一直停在商店门口")
        self.assertTrue(dialog.create_button.isEnabled())
        dialog.description_edit.clear()
        self.assertFalse(dialog.create_button.isEnabled())
        dialog.close()

    def test_report_dialog_follows_dark_theme_palette(self):
        qconfig.set(qconfig.themeMode, Theme.DARK, save=False)
        dialog = FeedbackReportDialog(
            DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00")
        )

        self.assertIn("background-color: #272727", dialog.styleSheet())
        self.assertIn("background-color: #2B2B2B", dialog.styleSheet())
        self.assertIn("color: #F0F0F0", dialog.styleSheet())

        qconfig.set(qconfig.themeMode, Theme.LIGHT, save=False)
        self.assertIn("background-color: #FAFAFA", dialog.styleSheet())
        dialog.close()

    def test_live_preview_exposes_a_bgr_copy_without_recapturing(self):
        widget = LiveScreenshotWidget()
        widget._active = True
        source = np.array(
            [
                [[10, 20, 30], [40, 50, 60]],
                [[70, 80, 90], [100, 110, 120]],
            ],
            dtype=np.uint8,
        )
        widget._display_frame(widget._frame_to_image(source), "test")
        widget._last_frame_at = time.time()

        first, age = widget.latest_frame(max_age_seconds=2.0)
        self.assertTrue(np.array_equal(source, first))
        self.assertIsNot(source, first)
        self.assertLess(age, 1.0)

        first.fill(0)
        second, _ = widget.latest_frame(max_age_seconds=2.0)
        self.assertTrue(np.array_equal(source, second))
        widget._shutdown()
        widget.close()

    def test_live_preview_waits_for_timed_out_capture_before_retrying(self):
        widget = LiveScreenshotWidget()
        widget._active = True
        executor = _CaptureExecutor()
        first_future = _CaptureFuture((None, "当前请求完成"))
        widget._capture_executor = executor
        widget._capture_pending = True
        widget._capture_pending_at = time.time() - 3.0
        widget._capture_future = first_future
        statuses = []
        widget.status_ready.connect(statuses.append)

        with patch.object(widget, "isVisible", return_value=True):
            widget._request_frame()
            widget._request_frame()

        self.assertEqual([], executor.submissions)
        self.assertEqual(["截图超时，等待当前请求完成"], statuses)
        self.assertTrue(widget._capture_timeout_reported)

        widget._capture_finished(first_future)
        self.assertFalse(widget._capture_pending)
        self.assertFalse(widget._capture_timeout_reported)

        with patch.object(widget, "isVisible", return_value=True):
            widget._request_frame()
        self.assertEqual(1, len(executor.submissions))
        self.assertIs(executor, widget._capture_executor)

        widget._shutdown()
        widget.close()

    def test_report_build_job_emits_success(self):
        expected_result = object()
        manager = _ReportManagerStub(result=expected_result)
        snapshot = DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00")
        job = _ReportBuildJob(manager, snapshot, "页面卡住", True)
        succeeded = []
        failed = []
        job.signals.succeeded.connect(succeeded.append)
        job.signals.failed.connect(failed.append)

        job.run()

        self.assertEqual([expected_result], succeeded)
        self.assertEqual([], failed)
        self.assertEqual([(snapshot, "页面卡住", True)], manager.calls)

    def test_report_build_job_emits_sanitized_failure(self):
        manager = _ReportManagerStub(error=RuntimeError("archive failed"))
        snapshot = DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00")
        job = _ReportBuildJob(manager, snapshot, "页面卡住", False)
        succeeded = []
        failed = []
        job.signals.succeeded.connect(succeeded.append)
        job.signals.failed.connect(failed.append)

        job.run()

        self.assertEqual([], succeeded)
        self.assertEqual(["RuntimeError: archive failed"], failed)


if __name__ == "__main__":
    unittest.main()
