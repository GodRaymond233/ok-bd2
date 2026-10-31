import os
import threading
import time
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from ok import og
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import PushButton, Theme, qconfig

from src.diagnostics.models import DiagnosticSnapshot
from src.ui.feedback_report import (
    FeedbackReportController,
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

    def test_dialog_makes_current_screenshot_optional(self):
        for frame in (None, np.zeros((20, 20, 3), dtype=np.uint8)):
            with self.subTest(has_frame=frame is not None):
                dialog = FeedbackReportDialog(
                    DiagnosticSnapshot(captured_at="2026-08-14T13:00:00+08:00", frame=frame)
                )

                self.assertFalse(dialog.include_screenshot.isChecked())
                self.assertEqual(frame is not None, dialog.include_screenshot.isEnabled())
                if frame is not None:
                    dialog.include_screenshot.setChecked(True)
                    self.assertTrue(dialog.include_screenshot.isChecked())
                dialog.close()

    def test_failure_prompt_uses_ui_thread_and_opens_report_on_accept(self):
        tab = _StartTabStub()
        evidence = SimpleNamespace(failure_notifier=None, report_action=Mock())
        controller = FeedbackReportController(tab, _ReportManagerStub(), evidence=evidence)
        failure = {"run": "standalone", "task_name": "广场女神像", "info": {}}
        create_button, later_button = object(), object()
        ui_threads = []
        with patch("src.ui.feedback_report.QMessageBox") as message_box, patch(
            "src.ui.feedback_report._apply_report_dialog_theme"
        ), patch.object(controller, "create_report") as create_report:
            prompt = message_box.return_value
            prompt.addButton.side_effect = [create_button, later_button]
            prompt.clickedButton.return_value = create_button
            prompt.exec.side_effect = lambda: ui_threads.append(threading.get_ident())
            worker = threading.Thread(target=evidence.failure_notifier, args=(failure,))
            worker.start()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            prompt.exec.assert_not_called()
            self.app.processEvents()
            self.assertEqual([threading.get_ident()], ui_threads)
            create_report.assert_called_once_with(
                report_context={"source": "failure_prompt", "failure_run": "standalone"},
            )
            self.assertEqual([
                call("prompt_offered", "standalone"), call("prompt_accepted", "standalone"),
            ], evidence.report_action.call_args_list)
            prompt.setDefaultButton.assert_called_once_with(later_button)
            prompt.deleteLater.assert_called_once_with()
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(evidence.failure_notifier)

    def test_failure_prompt_waits_for_current_report_and_can_be_declined(self):
        tab = _StartTabStub()
        manager = _ReportManagerStub()
        evidence = SimpleNamespace(failure_notifier=None, report_action=Mock())
        controller = FeedbackReportController(tab, manager, evidence=evidence)
        controller._busy = True
        create_button, later_button = object(), object()
        with patch("src.ui.feedback_report.QMessageBox") as message_box, patch(
            "src.ui.feedback_report._apply_report_dialog_theme"
        ), patch.object(controller, "create_report") as create_report:
            prompt = message_box.return_value
            prompt.addButton.side_effect = [create_button, later_button]
            prompt.clickedButton.return_value = later_button
            controller.failure_requested.emit({"run": "first", "task_name": "跑图", "info": {}})
            controller.failure_requested.emit({
                "run": "batch", "task_name": "一键完成日常", "info": {"失败": "广场女神像、跑图"},
            })
            self.app.processEvents()
            prompt.exec.assert_not_called()
            controller._reset_state()
            self.app.processEvents()
            prompt.exec.assert_called_once_with()
            self.assertIn("一键完成日常", prompt.setText.call_args.args[0])
            self.assertIn("广场女神像、跑图", prompt.setInformativeText.call_args.args[0])
            create_report.assert_not_called()
            self.assertEqual([], manager.calls)
            self.assertIsNone(controller._pending_failure)
            self.assertEqual([
                call("prompt_offered", "batch"), call("prompt_declined", "batch"),
            ], evidence.report_action.call_args_list)
        tab.close()

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

    def test_parent_destruction_releases_capture_and_ignores_late_results(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                parent = QWidget()
                executor = _CaptureExecutor()
                with patch.object(
                    LiveScreenshotWidget, "_new_capture_executor", return_value=executor
                ):
                    widget = LiveScreenshotWidget()
                widget.setParent(parent)
                widget._active = True
                future = Future()
                widget._capture_future = future
                widget._capture_pending = True
                parent.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

                self.assertFalse(widget._active)
                self.assertEqual(
                    [{"wait": False, "cancel_futures": True}], executor.shutdown_calls
                )
                if failed:
                    future.set_exception(RuntimeError("capture ended after deletion"))
                else:
                    future.set_result((None, "late frame"))
                widget._capture_finished(future)
                widget._shutdown()
                self.assertEqual(1, len(executor.shutdown_calls))
                self.assertFalse(widget._capture_pending)

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
