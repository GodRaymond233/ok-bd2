from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QTextEdit,
    QVBoxLayout,
)
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton

from src.diagnostics.models import DiagnosticSnapshot, ReportResult
from src.diagnostics.service import DiagnosticsManager
from src.ui.live_screenshot import LiveScreenshotWidget
from src.ui.quest_theme import on_theme_changed, palette


def _apply_report_dialog_theme(dialog: QDialog) -> None:
    """Keep report dialogs readable when qfluentwidgets switches theme."""

    def update() -> None:
        colors = palette()
        dialog.setStyleSheet(
            f"""
            QDialog {{
                background-color: {colors['bg_panel']};
                color: {colors['ink']};
            }}
            QLabel {{ color: {colors['ink']}; background: transparent; }}
            QLabel#bd2ReportTitle {{ color: {colors['ink']}; font-size: 20px; font-weight: 900; }}
            QLabel#bd2ReportHint {{ color: {colors['ink_dim']}; font-size: 12px; }}
            QLabel#bd2ReportStatus {{ color: {colors['ink_dim']}; font-size: 12px; }}
            QLabel#bd2ReportPrivacy {{ color: {colors['ink_faint']}; font-size: 11px; }}
            QLabel#bd2ReportPauseNotice {{
                color: {colors['warn_ink']}; font-size: 11px; font-weight: 600;
                padding: 6px 10px; border-radius: 6px;
                background-color: {colors['warn_soft']};
            }}
            QTextEdit {{
                background-color: {colors['card']};
                color: {colors['ink']};
                border: 1px solid {colors['line_strong']};
                border-radius: 9px;
                padding: 8px;
                selection-background-color: {colors['accent_deep']};
            }}
            QCheckBox {{ color: {colors['ink']}; spacing: 7px; }}
            """
        )

    update()
    on_theme_changed(update, dialog)


class FeedbackReportDialog(QDialog):
    def __init__(self, snapshot: DiagnosticSnapshot, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成问题报告")
        self.setModal(True)
        self.setMinimumWidth(640)
        _apply_report_dialog_theme(self)

        title = QLabel("请描述刚才遇到的问题")
        title.setObjectName("bd2ReportTitle")

        hint = QLabel("一句话说明“做了什么、看到了什么”即可，例如：跑商砍价后一直停在商店门口。")
        hint.setObjectName("bd2ReportHint")
        hint.setWordWrap(True)

        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText("请输入问题现象（必填）")
        self.description_edit.setAcceptRichText(False)
        self.description_edit.setMaximumHeight(110)

        self.preview = QLabel()
        self.preview.setObjectName("bd2ReportPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setStyleSheet(
            "background: #111; border: 1px solid rgba(255,255,255,0.10);"
            " border-radius: 10px; color: rgba(255,255,255,0.68);"
        )
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
        privacy.setObjectName("bd2ReportPrivacy")
        privacy.setWordWrap(True)

        cancel_button = PushButton("取消")
        cancel_button.clicked.connect(self.reject)
        create_button = PrimaryPushButton("生成报告")
        create_button.clicked.connect(self._accept_if_valid)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(create_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(14)
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
        self.setMinimumWidth(640)
        _apply_report_dialog_theme(self)

        title = QLabel(f"报告 {result.report_id} 已生成")
        title.setObjectName("bd2ReportTitle")

        status = QLabel(
            "反馈文字已复制到剪贴板，ZIP 文件也已在资源管理器中选中。"
            "把两者一起发到群里即可。"
        )
        status.setObjectName("bd2ReportStatus")
        status.setWordWrap(True)

        message = QTextEdit()
        message.setReadOnly(True)
        message.setPlainText(result.group_message)
        message.setMaximumHeight(135)

        pause_notice = QLabel("为避免现场被后续操作覆盖，任务当前保持暂停。")
        pause_notice.setObjectName("bd2ReportPauseNotice")

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
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(14)
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


class _ReportBuildSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class _ReportBuildJob(QRunnable):
    def __init__(
        self,
        manager: DiagnosticsManager,
        snapshot: DiagnosticSnapshot,
        description: str,
        include_screenshot: bool,
    ):
        super().__init__()
        self.manager = manager
        self.snapshot = snapshot
        self.description = description
        self.include_screenshot = include_screenshot
        self.signals = _ReportBuildSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.manager.build_report(
                self.snapshot,
                self.description,
                include_screenshot=self.include_screenshot,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.succeeded.emit(result)


class FeedbackReportController(QObject):
    def __init__(self, start_tab, manager: DiagnosticsManager):
        super().__init__(start_tab)
        self.start_tab = start_tab
        self.manager = manager
        self._busy = False
        self._snapshot: DiagnosticSnapshot | None = None
        self._executor = None
        self._progress_dialog: QProgressDialog | None = None
        self._build_job: _ReportBuildJob | None = None

    @Slot()
    def create_report(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            from ok import og

            self._executor = getattr(og, "executor", None)
            device_manager = getattr(og, "device_manager", None)
            preferred_frame, preferred_age = self._latest_preview_frame()

            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._snapshot = self.manager.prepare(
                    executor=self._executor,
                    device_manager=device_manager,
                    preferred_frame=preferred_frame,
                    preferred_frame_age_seconds=preferred_age,
                )
            finally:
                QApplication.restoreOverrideCursor()

            dialog = FeedbackReportDialog(self._snapshot, self.start_tab.window())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.manager.resume(self._snapshot, self._executor)
                self._reset_state()
                return

            self._start_build(
                dialog.description,
                include_screenshot=dialog.include_screenshot.isChecked(),
            )
        except Exception as exc:
            self._close_progress()
            self._resume_after_failure()
            self._show_error(str(exc))
            self._reset_state()

    def _latest_preview_frame(self):
        widget = getattr(self.start_tab, "live_screenshot_widget", None)
        latest_frame = getattr(widget, "latest_frame", None)
        if not callable(latest_frame):
            return None, None
        try:
            return latest_frame(max_age_seconds=2.0)
        except Exception:
            return None, None

    def _start_build(self, description: str, *, include_screenshot: bool) -> None:
        if self._snapshot is None:
            raise RuntimeError("诊断现场尚未准备完成")

        progress = QProgressDialog(self.start_tab.window())
        progress.setWindowTitle("生成问题报告")
        progress.setLabelText("正在脱敏并打包诊断信息…")
        progress.setRange(0, 0)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        _apply_report_dialog_theme(progress)
        progress.show()
        self._progress_dialog = progress

        job = _ReportBuildJob(
            self.manager,
            self._snapshot,
            description,
            include_screenshot,
        )
        job.signals.succeeded.connect(self._build_succeeded)
        job.signals.failed.connect(self._build_failed)
        self._build_job = job
        QThreadPool.globalInstance().start(job)

    @Slot(object)
    def _build_succeeded(self, result: ReportResult) -> None:
        self._close_progress()
        try:
            QApplication.clipboard().setText(result.group_message)
            try:
                from ok.util.explorer import reveal_in_explorer

                reveal_in_explorer(result.archive_path)
            except Exception:
                pass

            ready_dialog = ReportReadyDialog(result, self.start_tab.window())
            ready_dialog.exec()
            if ready_dialog.resume_requested and self._snapshot is not None:
                self.manager.resume(self._snapshot, self._executor)
        except Exception as exc:
            self._resume_after_failure()
            self._show_error(str(exc))
        finally:
            self._reset_state()

    @Slot(str)
    def _build_failed(self, error: str) -> None:
        self._close_progress()
        self._resume_after_failure()
        self._show_error(error)
        self._reset_state()

    def _resume_after_failure(self) -> None:
        if self._snapshot is None:
            return
        try:
            self.manager.resume(self._snapshot, self._executor)
        except Exception:
            pass

    @staticmethod
    def _show_error(error: str) -> None:
        from ok.gui.util.Alert import alert_error

        alert_error(f"生成问题报告失败：{error}", tray=True)

    def _close_progress(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None

    def _reset_state(self) -> None:
        self._busy = False
        self._snapshot = None
        self._executor = None
        self._build_job = None


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
