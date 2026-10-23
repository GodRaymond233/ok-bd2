from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, ComboBox, PrimaryPushButton

from src.window_resolution import (  # noqa: F401 - re-exported for the Qt card's callers
    DEFAULT_MANUAL_RESOLUTION,
    MANUAL_RESOLUTIONS,
    RESIZE_TIMEOUT_SECONDS,
    RESIZE_UI_TIMEOUT_MS,
    ManualResolutionError,
    Win32WindowBackend,
    WindowResizeResult,
    _check_cancelled,
    ensure_resolution_change_safe,
    format_resolution,
    resize_game_window,
)


class _ResizeSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class _ResizeJob:
    def __init__(
        self,
        device_manager: object,
        executor: object | None,
        target: tuple[int, int],
        resizer: Callable[..., WindowResizeResult],
    ):
        self.device_manager = device_manager
        self.executor = executor
        self.target = target
        self.resizer = resizer
        self.signals = _ResizeSignals()
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.resizer(
                self.device_manager,
                self.target,
                executor=self.executor,
                cancel_event=self.cancel_event,
            )
            _check_cancelled(self.cancel_event)
        except Exception as exc:
            self.signals.failed.emit(str(exc) or type(exc).__name__)
            return
        self.signals.succeeded.emit(result)


class ManualResolutionController(QObject):
    def __init__(
        self,
        widget: "ManualResolutionWidget",
        *,
        resizer: Callable[..., WindowResizeResult] = resize_game_window,
    ):
        super().__init__(widget)
        self.widget = widget
        self.resizer = resizer
        self._busy = False
        self._job: _ResizeJob | None = None
        self._worker_thread: threading.Thread | None = None
        self._timed_out = False
        self._shutting_down = False
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(RESIZE_UI_TIMEOUT_MS)
        self._watchdog.timeout.connect(self._resize_timed_out)

    @Slot()
    def apply_selected_resolution(self) -> None:
        if self._shutting_down or self._busy:
            return
        if self._job is not None:
            self.widget.set_status("上一次调整仍在等待系统调用结束，请稍后重试。")
            return

        from ok import og

        executor = getattr(og, "executor", None)
        try:
            ensure_resolution_change_safe(executor)
        except ManualResolutionError as exc:
            self._show_failure(str(exc))
            return

        target = self.widget.selected_resolution
        device_manager = getattr(og, "device_manager", None)
        if device_manager is None:
            self._show_failure("设备管理器尚未就绪。")
            return

        self._busy = True
        self.widget.apply_button.setEnabled(False)
        self.widget.set_status(f"正在调整到 {format_resolution(target)}…")

        job = _ResizeJob(device_manager, executor, target, self.resizer)
        job.signals.succeeded.connect(self._resize_succeeded)
        job.signals.failed.connect(self._resize_failed)
        worker = threading.Thread(
            target=job.run,
            name="ManualResolution",
            daemon=True,
        )
        self._job = job
        self._worker_thread = worker
        try:
            worker.start()
            self._watchdog.start()
        except Exception as exc:
            job.cancel()
            self._finish()
            self._show_failure(str(exc) or type(exc).__name__)

    @Slot(object)
    def _resize_succeeded(self, result: WindowResizeResult) -> None:
        if self._shutting_down:
            return
        if self._timed_out:
            self._finish()
            return
        self._finish()
        resolution = format_resolution((result.width, result.height))
        suffix = "（尺寸已是目标值）" if not result.changed else ""
        self.widget.set_status(f"当前游戏窗口：{resolution}{suffix}")
        from ok.ui.qt.util.Alert import alert_info

        alert_info(f"游戏窗口已调整为 {resolution}。")

    @Slot(str)
    def _resize_failed(self, error: str) -> None:
        if self._shutting_down:
            return
        if self._timed_out:
            self._finish()
            return
        self._finish()
        self._show_failure(error)

    @Slot()
    def _resize_timed_out(self) -> None:
        if self._job is None:
            return
        self._timed_out = True
        self._job.cancel()
        self._busy = False
        self.widget.apply_button.setEnabled(True)
        self._show_failure("系统窗口调用超时，已停止后续步骤；请稍后重试。")

    def _show_failure(self, error: str) -> None:
        self.widget.set_status(f"调整失败：{error}")
        from ok.ui.qt.util.Alert import alert_error

        alert_error(f"手动调整分辨率失败：{error}")

    def _finish(self) -> None:
        if self._watchdog.isActive():
            self._watchdog.stop()
        self._busy = False
        if not self._shutting_down:
            self.widget.apply_button.setEnabled(True)
        self._job = None
        self._worker_thread = None
        self._timed_out = False

    @Slot()
    def _shutdown(self) -> None:
        if getattr(self, "_shutting_down", True):
            return
        self._shutting_down = True
        if self._watchdog.isActive():
            self._watchdog.stop()
        job = self._job
        if job is not None:
            job.cancel()
            for signal, slot in (
                (job.signals.succeeded, self._resize_succeeded),
                (job.signals.failed, self._resize_failed),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._busy = False
        self._job = None
        self._worker_thread = None


class ManualResolutionWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.resolution_combo = ComboBox(self)
        for resolution in MANUAL_RESOLUTIONS:
            self.resolution_combo.addItem(
                format_resolution(resolution),
                userData=resolution,
            )

        default_index = MANUAL_RESOLUTIONS.index(DEFAULT_MANUAL_RESOLUTION)
        self.resolution_combo.setCurrentIndex(default_index)
        self.resolution_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.resolution_combo.setToolTip("选择游戏窗口客户区的目标分辨率")

        self.apply_button = PrimaryPushButton("应用")
        self.apply_button.setToolTip(
            "将游戏客户区调整到所选分辨率；空间足够时转为窗口模式，"
            "边框无法容纳时保留无边框"
        )

        self.status_label = CaptionLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #888888;")
        self.status_label.hide()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.resolution_combo, 1)
        row.addWidget(self.apply_button, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(row)
        layout.addWidget(self.status_label)

        self.controller = ManualResolutionController(self)
        self.apply_button.clicked.connect(self.controller.apply_selected_resolution)
        self.destroyed.connect(self.controller._shutdown)

    @Slot()
    def _shutdown(self) -> None:
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller._shutdown()

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)

    @property
    def selected_resolution(self) -> tuple[int, int]:
        value = self.resolution_combo.currentData()
        return int(value[0]), int(value[1])

    def set_status(self, status: str) -> None:
        self.status_label.setText(status)
        self.status_label.setToolTip(status)
        self.status_label.setVisible(bool(status))
