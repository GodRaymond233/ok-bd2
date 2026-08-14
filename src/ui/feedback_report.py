from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton

from src.diagnostics.models import DiagnosticSnapshot, ReportResult
from src.diagnostics.service import DiagnosticsManager
from src.ui.live_screenshot import LiveScreenshotWidget


class FeedbackReportDialog(QDialog):
    def __init__(self, snapshot: DiagnosticSnapshot, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成问题报告")
        self.setModal(True)
        self.setMinimumWidth(560)

        title = QLabel("请描述刚才遇到的问题")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")

        hint = QLabel("一句话说明“做了什么、看到了什么”即可，例如：跑商砍价后一直停在商店门口。")
        hint.setWordWrap(True)

        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText("请输入问题现象（必填）")
        self.description_edit.setAcceptRichText(False)
        self.description_edit.setMaximumHeight(110)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setStyleSheet("background: #111; border-radius: 6px; color: #bbb;")
        if snapshot.frame is not None:
            image = LiveScreenshotWidget._frame_to_image(snapshot.frame)
            self.preview.setPixmap(
                QPixmap.fromImage(image).scaled(
                    520,
                    292,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.preview.setText("当前没有可用的游戏窗口截图，仍可生成日志报告")

        self.include_screenshot = QCheckBox("附带上方游戏窗口截图（建议）")
        self.include_screenshot.setChecked(snapshot.frame is not None)
        self.include_screenshot.setEnabled(snapshot.frame is not None)

        privacy = QLabel(
            "隐私范围：只导出受限运行信息、脱敏后的最近日志和你确认的游戏截图；"
            "不导出原始配置、环境变量、进程列表、用户名或机器名。"
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: #888;")

        cancel_button = PushButton("取消")
        cancel_button.clicked.connect(self.reject)
        create_button = PrimaryPushButton("生成报告")
        create_button.clicked.connect(self._accept_if_valid)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(create_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.description_edit)
        layout.addWidget(self.preview)
        layout.addWidget(self.include_screenshot)
        layout.addWidget(privacy)
        layout.addLayout(button_row)

    @property
    def description(self) -> str:
        return self.description_edit.toPlainText().strip()

    @Slot()
    def _accept_if_valid(self) -> None:
        if not self.description:
            QMessageBox.warning(self, "还差一步", "请先填写问题现象。")
            self.description_edit.setFocus()
            return
        self.accept()


class ReportReadyDialog(QDialog):
    def __init__(self, result: ReportResult, parent=None):
        super().__init__(parent)
        self.result = result
        self.resume_requested = False
        self.setWindowTitle("问题报告已生成")
        self.setModal(True)
        self.setMinimumWidth(560)

        title = QLabel(f"报告 {result.report_id} 已生成")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")

        status = QLabel(
            "反馈文字已复制到剪贴板，ZIP 文件也已在资源管理器中选中。"
            "把两者一起发到群里即可。"
        )
        status.setWordWrap(True)

        message = QTextEdit()
        message.setReadOnly(True)
        message.setPlainText(result.group_message)
        message.setMaximumHeight(135)

        pause_notice = QLabel("为避免现场被后续操作覆盖，任务当前保持暂停。")
        pause_notice.setStyleSheet("color: #a66b00;")

        copy_button = PushButton("再次复制反馈文字")
        copy_button.clicked.connect(self._copy_message)
        open_button = PushButton("打开 ZIP 位置")
        open_button.clicked.connect(self._reveal_archive)
        keep_paused_button = PushButton("保持暂停并关闭")
        keep_paused_button.clicked.connect(self.accept)
        resume_button = PrimaryPushButton("继续运行")
        resume_button.clicked.connect(self._resume)

        button_row = QHBoxLayout()
        button_row.addWidget(copy_button)
        button_row.addWidget(open_button)
        button_row.addStretch(1)
        button_row.addWidget(keep_paused_button)
        button_row.addWidget(resume_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(status)
        layout.addWidget(message)
        layout.addWidget(pause_notice)
        layout.addLayout(button_row)

    @Slot()
    def _copy_message(self) -> None:
        QApplication.clipboard().setText(self.result.group_message)

    @Slot()
    def _reveal_archive(self) -> None:
        from ok.util.explorer import reveal_in_explorer

        reveal_in_explorer(self.result.archive_path)

    @Slot()
    def _resume(self) -> None:
        self.resume_requested = True
        self.accept()


class FeedbackReportController(QObject):
    def __init__(self, start_tab, manager: DiagnosticsManager):
        super().__init__(start_tab)
        self.start_tab = start_tab
        self.manager = manager
        self._busy = False

    @Slot()
    def create_report(self) -> None:
        if self._busy:
            return
        self._busy = True
        snapshot = None
        try:
            from ok import og

            executor = getattr(og, "executor", None)
            device_manager = getattr(og, "device_manager", None)
            snapshot = self.manager.prepare(
                executor=executor,
                device_manager=device_manager,
            )

            dialog = FeedbackReportDialog(snapshot, self.start_tab.window())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.manager.resume(snapshot, executor)
                return

            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                result = self.manager.build_report(
                    snapshot,
                    dialog.description,
                    include_screenshot=dialog.include_screenshot.isChecked(),
                )
            finally:
                QApplication.restoreOverrideCursor()

            QApplication.clipboard().setText(result.group_message)
            try:
                from ok.util.explorer import reveal_in_explorer

                reveal_in_explorer(result.archive_path)
            except Exception:
                pass

            ready_dialog = ReportReadyDialog(result, self.start_tab.window())
            ready_dialog.exec()
            if ready_dialog.resume_requested:
                self.manager.resume(snapshot, executor)
        except Exception as exc:
            if snapshot is not None:
                try:
                    from ok import og

                    self.manager.resume(snapshot, getattr(og, "executor", None))
                except Exception:
                    pass
            from ok.gui.util.Alert import alert_error

            alert_error(f"生成问题报告失败：{exc}", tray=True)
        finally:
            self._busy = False


def install_feedback_report(start_tab) -> None:
    if getattr(start_tab, "_bd2_feedback_report_installed", False):
        return
    debug_layout = getattr(start_tab, "debug_layout", None)
    if debug_layout is None:
        return

    from ok import og
    from ok.util.file import get_downloads_folder

    app_config = getattr(og, "config", None) or {}
    app_version = str(app_config.get("version", "unknown"))
    manager = DiagnosticsManager(
        project_root=Path.cwd(),
        output_dir=Path(get_downloads_folder()),
        app_version=app_version,
    )
    controller = FeedbackReportController(start_tab, manager)
    button = PrimaryPushButton(FluentIcon.FEEDBACK, "生成问题报告")
    button.setToolTip("生成可直接发送到群聊的隐私化诊断 ZIP")
    button.clicked.connect(controller.create_report)
    debug_layout.insertWidget(0, button)

    raw_export_button = getattr(start_tab, "export_log_button", None)
    if raw_export_button is not None:
        raw_export_button.setText("导出原始日志")
        raw_export_button.setToolTip("旧版兜底入口：不会脱敏，会导出全部日志和截图")

    start_tab.feedback_report_button = button
    start_tab.feedback_report_controller = controller
    start_tab._bd2_feedback_report_installed = True
