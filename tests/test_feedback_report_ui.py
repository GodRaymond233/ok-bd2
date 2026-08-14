import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok import og
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import PushButton

from src.diagnostics.models import DiagnosticSnapshot
from src.ui.feedback_report import FeedbackReportDialog, install_feedback_report


class _StartTabStub(QWidget):
    def __init__(self):
        super().__init__()
        self.debug_layout = QHBoxLayout(self)
        self.export_log_button = PushButton("Export Logs")
        self.debug_layout.addWidget(self.export_log_button)


class FeedbackReportUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_config = getattr(og, "config", None)
        og.config = {"version": "0.1.test"}

    def tearDown(self):
        og.config = self.original_config

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


if __name__ == "__main__":
    unittest.main()
