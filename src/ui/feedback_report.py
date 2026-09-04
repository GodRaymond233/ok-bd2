from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
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
from src.ui.quest_theme import (
    BODY_FONT,
    MONO_FONT,
    chip_qss,
    on_theme_changed,
    palette,
    rgba,
)


def _apply_report_dialog_theme(dialog: QDialog, restyle_chrome=None) -> None:
    """Keep report dialogs readable when qfluentwidgets switches theme.

    ``restyle_chrome`` re-renders theme-tinted pixmaps (inline icons), which
    QSS cannot reach.
    """

    def update() -> None:
        colors = palette()
        dialog.setStyleSheet(
            f"""
            QDialog {{
                background-color: {colors['bg_panel']};
                color: {colors['ink']};
                font-family: {BODY_FONT};
            }}
            QLabel {{ color: {colors['ink']}; background: transparent; }}
            QLabel#bd2ReportTitle {{
                color: {colors['ink']}; font-size: 20px; font-weight: 900;
            }}
            QLabel#bd2ReportHint {{ color: {colors['ink_dim']}; font-size: 12px; }}
            QLabel#bd2ReportSection {{
                color: {colors['ink_faint']}; font-size: 11px; font-weight: 700;
            }}
            QLabel#bd2ReportPrivacy {{ color: {colors['ink_faint']}; font-size: 11px; }}
            QLabel#bd2ReportId {{
                {chip_qss(colors['accent'], colors['accent_soft'])}
                font-family: {MONO_FONT}; font-size: 12px; padding: 3px 10px;
            }}
            QLabel#bd2ReportPreviewText {{
                color: rgba(255, 255, 255, 0.62); font-size: 12px;
            }}
            QLabel#bd2ReportPauseText {{
                color: {colors['warn_ink']}; font-size: 12px; font-weight: 600;
            }}
            QFrame#bd2ReportAccentBar {{
                background-color: {colors['accent']};
                border: none; border-radius: 2px;
            }}
            QFrame#bd2ReportPreview {{
                background-color: #151515;
                border: 1px solid rgba(127, 127, 127, 0.35);
                border-radius: 10px;
            }}
            QFrame#bd2ReportPauseNotice {{
                background-color: {colors['warn_soft']};
                border: 1px solid {rgba(colors['warn_ink'], 0.35)};
                border-radius: 7px;
            }}
            QTextEdit {{
                background-color: {colors['card']};
                color: {colors['ink']};
                border: 1px solid {colors['line_strong']};
                border-radius: 9px;
                padding: 8px 10px;
                selection-background-color: {colors['accent_deep']};
                font-family: {BODY_FONT};
                font-size: 13px;
            }}
            QTextEdit:focus {{ border: 1px solid {colors['accent']}; }}
            QTextEdit#bd2ReportMessage {{
                background-color: {colors['inset']};
                font-family: {MONO_FONT};
                font-size: 12px;
            }}
            QCheckBox {{ color: {colors['ink']}; spacing: 7px; font-size: 12px; }}
            QProgressBar {{
                background-color: {colors['card']};
                border: 1px solid {colors['line']};
                border-radius: 5px;
                min-height: 10px;
                max-height: 10px;
                color: {colors['ink_dim']};
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                background-color: {colors['accent']};
                border-radius: 4px;
            }}
            """
        )
        if restyle_chrome is not None:
            restyle_chrome(colors)

    update()
    on_theme_changed(update, dialog)


def _dialog_header(title_text: str, hint_text: str) -> QHBoxLayout:
    """Accent bar + title + hint, matching the quest page section headers."""

    bar = QFrame()
    bar.setObjectName("bd2ReportAccentBar")
    bar.setFixedSize(4, 36)

    title = QLabel(title_text)
    title.setObjectName("bd2ReportTitle")
    hint = QLabel(hint_text)
    hint.setObjectName("bd2ReportHint")
    hint.setWordWrap(True)

    text_column = QVBoxLayout()
    text_column.setContentsMargins(0, 0, 0, 0)
    text_column.setSpacing(4)
    text_column.addWidget(title)
    text_column.addWidget(hint)

    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(10)
    header.addWidget(bar, 0, Qt.AlignmentFlag.AlignVCenter)
    header.addLayout(text_column, 1)
    return header


def _section_caption(text: str) -> QLabel:
    caption = QLabel(text)
    caption.setObjectName("bd2ReportSection")
    return caption


class FeedbackReportDialog(QDialog):
    def __init__(self, snapshot: DiagnosticSnapshot, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成问题报告")
        self.setModal(True)
        self.setMinimumWidth(640)

        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText("请输入问题现象（必填）")
        self.description_edit.setAcceptRichText(False)
        self.description_edit.setMaximumHeight(110)

        self.preview = QFrame()
        self.preview.setObjectName("bd2ReportPreview")
        self.preview.setMinimumHeight(220)
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(8)
        if snapshot.frame is not None:
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image_label.setStyleSheet("background: transparent; border: none;")
            image = LiveScreenshotWidget._frame_to_image(snapshot.frame)
            image_label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    520,
                    292,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            preview_layout.addWidget(image_label)
        else:
            placeholder_icon = QLabel()
            placeholder_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder_icon.setStyleSheet("background: transparent; border: none;")
            placeholder_icon.setPixmap(
                FluentIcon.PHOTO.icon(color=QColor(255, 255, 255, 110)).pixmap(30, 30)
            )
            placeholder_text = QLabel("当前没有可用的游戏窗口截图，仍可生成日志报告")
            placeholder_text.setObjectName("bd2ReportPreviewText")
            placeholder_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_layout.addStretch(1)
            preview_layout.addWidget(placeholder_icon)
            preview_layout.addWidget(placeholder_text)
            preview_layout.addStretch(1)

        self.include_screenshot = QCheckBox("附带上方游戏窗口截图（建议）")
        self.include_screenshot.setChecked(snapshot.frame is not None)
        self.include_screenshot.setEnabled(snapshot.frame is not None)

        self.privacy_icon = QLabel()
        self.privacy_icon.setFixedSize(12, 12)
        privacy = QLabel(
            "隐私范围：只导出受限运行信息、脱敏后的最近日志和你确认的游戏截图；"
            "不导出原始配置、环境变量、进程列表、用户名或机器名。"
        )
        privacy.setObjectName("bd2ReportPrivacy")
        privacy.setWordWrap(True)
        privacy_row = QHBoxLayout()
        privacy_row.setContentsMargins(0, 0, 0, 0)
        privacy_row.setSpacing(6)
        privacy_row.addWidget(self.privacy_icon, 0, Qt.AlignmentFlag.AlignTop)
        privacy_row.addWidget(privacy, 1)

        cancel_button = PushButton("取消")
        cancel_button.clicked.connect(self.reject)
        self.create_button = PrimaryPushButton(FluentIcon.SEND, "生成报告")
        self.create_button.setEnabled(False)
        self.create_button.clicked.connect(self._accept_if_valid)
        self.description_edit.textChanged.connect(
            lambda: self.create_button.setEnabled(bool(self.description))
        )

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(self.create_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(10)
        layout.addLayout(
            _dialog_header(
                "请描述刚才遇到的问题",
                "一句话说明“做了什么、看到了什么”即可，例如：跑商砍价后一直停在商店门口。",
            )
        )
        layout.addSpacing(8)
        layout.addWidget(_section_caption("问题描述"))
        layout.addWidget(self.description_edit)
        layout.addSpacing(4)
        layout.addWidget(_section_caption("现场截图"))
        layout.addWidget(self.preview)
        layout.addWidget(self.include_screenshot)
        layout.addSpacing(2)
        layout.addLayout(privacy_row)
        layout.addSpacing(6)
        layout.addLayout(button_row)

        _apply_report_dialog_theme(self, self._restyle_chrome)

    def _restyle_chrome(self, colors) -> None:
        self.privacy_icon.setPixmap(
            FluentIcon.INFO.icon(color=QColor(colors["ink_faint"])).pixmap(12, 12)
        )

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

        report_id = QLabel(result.report_id)
        report_id.setObjectName("bd2ReportId")
        id_row = QHBoxLayout()
        id_row.setContentsMargins(0, 0, 0, 0)
        id_row.setSpacing(8)
        id_row.addWidget(_section_caption("报告编号"))
        id_row.addWidget(report_id)
        id_row.addStretch(1)

        message = QTextEdit()
        message.setObjectName("bd2ReportMessage")
        message.setReadOnly(True)
        message.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        message.setPlainText(result.group_message)
        message.setMaximumHeight(135)

        self.pause_icon = QLabel()
        self.pause_icon.setFixedSize(13, 13)
        pause_text = QLabel("为避免现场被后续操作覆盖，任务当前保持暂停。")
        pause_text.setObjectName("bd2ReportPauseText")
        pause_notice = QFrame()
        pause_notice.setObjectName("bd2ReportPauseNotice")
        pause_row = QHBoxLayout(pause_notice)
        pause_row.setContentsMargins(12, 7, 12, 7)
        pause_row.setSpacing(8)
        pause_row.addWidget(self.pause_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        pause_row.addWidget(pause_text, 1)

        copy_button = PushButton(FluentIcon.COPY, "再次复制反馈文字")
        copy_button.clicked.connect(self._copy_message)
        open_button = PushButton(FluentIcon.FOLDER, "打开 ZIP 位置")
        open_button.clicked.connect(self._reveal_archive)
        keep_paused_button = PushButton("保持暂停并关闭")
        keep_paused_button.clicked.connect(self.accept)
        resume_button = PrimaryPushButton(FluentIcon.PLAY, "继续运行")
        resume_button.clicked.connect(self._resume)

        button_row = QHBoxLayout()
        button_row.addWidget(copy_button)
        button_row.addWidget(open_button)
        button_row.addStretch(1)
        button_row.addWidget(keep_paused_button)
        button_row.addWidget(resume_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(10)
        layout.addLayout(
            _dialog_header(
                "问题报告已生成",
                "反馈文字已复制到剪贴板，ZIP 文件也已在资源管理器中选中。"
                "把两者一起发到群里即可。",
            )
        )
        layout.addSpacing(8)
        layout.addLayout(id_row)
        layout.addSpacing(4)
        layout.addWidget(_section_caption("反馈文字"))
        layout.addWidget(message)
        layout.addSpacing(4)
        layout.addWidget(pause_notice)
        layout.addSpacing(6)
        layout.addLayout(button_row)

        _apply_report_dialog_theme(self, self._restyle_chrome)

    def _restyle_chrome(self, colors) -> None:
        self.pause_icon.setPixmap(
            FluentIcon.PAUSE.icon(color=QColor(colors["warn_ink"])).pixmap(13, 13)
        )

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
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    self.manager.resume(self._snapshot, self._executor)
                    self._reset_state()
                    return

                self._start_build(
                    dialog.description,
                    include_screenshot=dialog.include_screenshot.isChecked(),
                )
            finally:
                # 对话框归父窗口所有，exec 结束后不释放会连同其主题回调
                # 一直存活；deleteLater 触发 destroyed 让回调断连。
                dialog.deleteLater()
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
            ready_dialog.deleteLater()
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
        from ok.ui.qt.util.Alert import alert_error

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
