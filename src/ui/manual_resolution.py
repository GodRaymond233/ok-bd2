from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

import win32api
import win32con
import win32gui
from ok.util.logger import Logger
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, ComboBox, PrimaryPushButton

logger = Logger.get_logger(__name__)

MANUAL_RESOLUTIONS: tuple[tuple[int, int], ...] = (
    (3840, 2160),
    (2560, 1440),
    (1920, 1080),
    (1600, 900),
    (1366, 768),
    (1280, 720),
)
DEFAULT_MANUAL_RESOLUTION = (1920, 1080)
RESIZE_TIMEOUT_SECONDS = 3.0
RESIZE_POLL_INTERVAL_SECONDS = 0.05
RESIZE_UI_TIMEOUT_MS = 6000


class ManualResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowResizeResult:
    width: int
    height: int
    changed: bool


class Win32WindowBackend:
    """Small Win32 boundary kept injectable for deterministic tests."""

    @staticmethod
    def is_window(hwnd: int) -> bool:
        return bool(win32gui.IsWindow(hwnd))

    @staticmethod
    def is_minimized(hwnd: int) -> bool:
        return bool(win32gui.IsIconic(hwnd))

    @staticmethod
    def is_maximized(hwnd: int) -> bool:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        is_zoomed = user32.IsZoomed
        is_zoomed.argtypes = (wintypes.HWND,)
        is_zoomed.restype = wintypes.BOOL
        return bool(is_zoomed(hwnd))

    @staticmethod
    def restore(hwnd: int) -> None:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    @staticmethod
    def get_window_placement(hwnd: int) -> tuple:
        return tuple(win32gui.GetWindowPlacement(hwnd))

    @staticmethod
    def set_window_placement(hwnd: int, placement: tuple) -> None:
        win32gui.SetWindowPlacement(hwnd, placement)

    @staticmethod
    def get_style(hwnd: int) -> int:
        return int(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE))

    @staticmethod
    def set_style(hwnd: int, style: int) -> None:
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

    @staticmethod
    def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
        return tuple(int(value) for value in win32gui.GetWindowRect(hwnd))

    @staticmethod
    def get_client_size(hwnd: int) -> tuple[int, int]:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        return int(right - left), int(bottom - top)

    @staticmethod
    def _get_monitor_info(hwnd: int) -> dict:
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        return win32api.GetMonitorInfo(monitor)

    @classmethod
    def get_monitor_area(cls, hwnd: int) -> tuple[int, int, int, int]:
        return tuple(int(value) for value in cls._get_monitor_info(hwnd)["Work"])

    @classmethod
    def get_monitor_bounds(cls, hwnd: int) -> tuple[int, int, int, int]:
        return tuple(int(value) for value in cls._get_monitor_info(hwnd)["Monitor"])

    @staticmethod
    def get_frame_size(hwnd: int, style: int, target: tuple[int, int]) -> tuple[int, int]:
        ex_style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE))
        has_menu = bool(win32gui.GetMenu(hwnd))
        rect = wintypes.RECT(0, 0, target[0], target[1])
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        adjust_for_dpi = getattr(user32, "AdjustWindowRectExForDpi", None)
        if adjust_for_dpi is not None:
            adjust_for_dpi.argtypes = (
                ctypes.POINTER(wintypes.RECT),
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
                wintypes.UINT,
            )
            adjust_for_dpi.restype = wintypes.BOOL
            get_dpi_for_window = user32.GetDpiForWindow
            get_dpi_for_window.argtypes = (wintypes.HWND,)
            get_dpi_for_window.restype = wintypes.UINT
            dpi = int(get_dpi_for_window(hwnd) or 96)
            adjusted = adjust_for_dpi(
                ctypes.byref(rect),
                style & 0xFFFFFFFF,
                has_menu,
                ex_style & 0xFFFFFFFF,
                dpi,
            )
        else:
            adjust = user32.AdjustWindowRectEx
            adjust.argtypes = (
                ctypes.POINTER(wintypes.RECT),
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            adjust.restype = wintypes.BOOL
            adjusted = adjust(
                ctypes.byref(rect),
                style & 0xFFFFFFFF,
                has_menu,
                ex_style & 0xFFFFFFFF,
            )
        if not adjusted:
            raise ctypes.WinError(ctypes.get_last_error())
        return (
            int(rect.right - rect.left - target[0]),
            int(rect.bottom - rect.top - target[1]),
        )

    @staticmethod
    def set_window_pos(
        hwnd: int,
        rect: tuple[int, int, int, int],
        *,
        frame_changed: bool,
    ) -> None:
        left, top, right, bottom = rect
        flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
        if frame_changed:
            flags |= win32con.SWP_FRAMECHANGED
        win32gui.SetWindowPos(
            hwnd,
            0,
            left,
            top,
            right - left,
            bottom - top,
            flags,
        )


def format_resolution(resolution: tuple[int, int]) -> str:
    return f"{resolution[0]} × {resolution[1]}"


def ensure_resolution_change_safe(executor: object | None) -> None:
    if executor is None:
        return
    current_task = getattr(executor, "current_task", None)
    if current_task is not None and not bool(getattr(executor, "paused", False)):
        raise ManualResolutionError("任务正在运行，请先暂停任务再调整分辨率。")


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ManualResolutionError("调整已取消。")


def _target_outer_rect(
    target: tuple[int, int],
    frame_size: tuple[int, int],
    monitor_area: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    target_outer_width = target[0] + frame_size[0]
    target_outer_height = target[1] + frame_size[1]
    monitor_left, monitor_top, monitor_right, monitor_bottom = monitor_area
    monitor_width = monitor_right - monitor_left
    monitor_height = monitor_bottom - monitor_top
    if target_outer_width > monitor_width or target_outer_height > monitor_height:
        raise ManualResolutionError(
            f"当前显示器无法容纳 {format_resolution(target)} 游戏客户区。"
        )

    left = monitor_left + (monitor_width - target_outer_width) // 2
    top = monitor_top + (monitor_height - target_outer_height) // 2
    return left, top, left + target_outer_width, top + target_outer_height


def _plan_resize(
    backend: Win32WindowBackend,
    hwnd: int,
    target: tuple[int, int],
    original_style: int,
) -> tuple[int, tuple[int, int, int, int]]:
    state_bits = win32con.WS_MAXIMIZE | win32con.WS_MINIMIZE
    windowed_style = (
        (original_style | win32con.WS_CAPTION)
        & ~win32con.WS_POPUP
        & ~state_bits
    )
    borderless_style = (
        (original_style | win32con.WS_POPUP)
        & ~win32con.WS_CAPTION
        & ~win32con.WS_THICKFRAME
        & ~state_bits
    )
    work_area = backend.get_monitor_area(hwnd)
    monitor_bounds = backend.get_monitor_bounds(hwnd)
    attempts = (
        (windowed_style, work_area),
        (borderless_style, work_area),
        (borderless_style, monitor_bounds),
    )
    attempted: set[tuple[int, tuple[int, int, int, int]]] = set()
    last_error: ManualResolutionError | None = None
    for style, area in attempts:
        key = (style, area)
        if key in attempted:
            continue
        attempted.add(key)
        frame_size = backend.get_frame_size(hwnd, style, target)
        try:
            return style, _target_outer_rect(target, frame_size, area)
        except ManualResolutionError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ManualResolutionError(f"当前显示器无法容纳 {format_resolution(target)} 游戏客户区。")


def _restore_window(
    backend: Win32WindowBackend,
    hwnd: int,
    style: int,
    rect: tuple[int, int, int, int],
    placement: tuple | None,
) -> None:
    try:
        restored_style = style
        if placement is not None:
            restored_style &= ~(win32con.WS_MAXIMIZE | win32con.WS_MINIMIZE)
        backend.set_style(hwnd, restored_style)
        restore_rect = tuple(placement[4]) if placement is not None else rect
        backend.set_window_pos(hwnd, restore_rect, frame_changed=True)
        if placement is not None:
            backend.set_window_placement(hwnd, placement)
    except Exception as exc:
        logger.error(f"restore game window failed: {exc}", exception=exc)


def resize_game_window(
    device_manager: object,
    target: tuple[int, int],
    *,
    executor: object | None = None,
    backend: Win32WindowBackend | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = RESIZE_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
) -> WindowResizeResult:
    if target not in MANUAL_RESOLUTIONS:
        raise ManualResolutionError(f"不支持的目标分辨率：{format_resolution(target)}。")
    ensure_resolution_change_safe(executor)
    _check_cancelled(cancel_event)

    preferred = device_manager.get_preferred_device()
    if preferred is None or preferred.get("device") != "windows":
        raise ManualResolutionError("请先在首页选择并连接 PC 游戏窗口。")

    hwnd_window = getattr(device_manager, "hwnd_window", None)
    if hwnd_window is None:
        raise ManualResolutionError("游戏窗口管理器尚未就绪，请先刷新窗口列表。")

    hwnd_window.do_update_window_size()
    _check_cancelled(cancel_event)
    hwnd = int(getattr(hwnd_window, "hwnd", 0) or 0)
    backend = backend or Win32WindowBackend()
    if not hwnd or not backend.is_window(hwnd):
        raise ManualResolutionError("当前游戏窗口已失效，请刷新后重新选择。")
    if backend.is_minimized(hwnd):
        raise ManualResolutionError("游戏窗口已最小化，请先恢复窗口。")

    original_style = backend.get_style(hwnd)
    original_rect = backend.get_window_rect(hwnd)
    original_maximized = backend.is_maximized(hwnd)
    original_placement = (
        backend.get_window_placement(hwnd) if original_maximized else None
    )
    current_client_size = backend.get_client_size(hwnd)
    if current_client_size == target:
        return WindowResizeResult(*target, changed=False)

    target_style, target_rect = _plan_resize(
        backend,
        hwnd,
        target,
        original_style,
    )
    _check_cancelled(cancel_event)
    # Narrow the UI-to-worker race immediately before the first mutating call.
    ensure_resolution_change_safe(executor)

    try:
        if original_maximized:
            backend.restore(hwnd)
            _check_cancelled(cancel_event)
            if backend.is_maximized(hwnd):
                raise ManualResolutionError("游戏窗口无法退出最大化状态，请先手动还原窗口。")

        current_style = backend.get_style(hwnd)
        if target_style != current_style:
            backend.set_style(hwnd, target_style)
            _check_cancelled(cancel_event)
        backend.set_window_pos(hwnd, target_rect, frame_changed=True)
        _check_cancelled(cancel_event)

        deadline = monotonic() + max(0.0, timeout)
        while True:
            _check_cancelled(cancel_event)
            actual = backend.get_client_size(hwnd)
            if actual == target:
                hwnd_window.do_update_window_size()
                logger.info(f"manual game resolution changed to {target[0]}x{target[1]}")
                return WindowResizeResult(*actual, changed=True)
            if monotonic() >= deadline:
                break
            sleep(RESIZE_POLL_INTERVAL_SECONDS)

        raise ManualResolutionError(
            "窗口没有稳定到目标分辨率，"
            f"当前客户区为 {format_resolution(actual)}。"
        )
    except Exception:
        _restore_window(
            backend,
            hwnd,
            original_style,
            original_rect,
            original_placement,
        )
        hwnd_window.do_update_window_size()
        raise


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
        self.destroyed.connect(self._shutdown)

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
