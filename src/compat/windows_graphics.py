from __future__ import annotations

import time
from dataclasses import dataclass

import ok
from ok.device.capture_methods import update as capture_update
from ok.util import window as ok_window
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

WGC_RESIZE_STABLE_SECONDS = 0.8
WGC_MIN_CAPTURE_SIZE = (1280, 720)
WGC_INVALID_SIZE_LOG_INTERVAL = 5.0
_WGC_PATCH_MARKER = "_ok_bd2_resize_stability_enabled"
_HWND_PATCH_MARKER = "_ok_bd2_capture_identity_enabled"


@dataclass
class ResizeStabilityGate:
    """Track one continuously changing capture size until it becomes stable."""

    delay_seconds: float = WGC_RESIZE_STABLE_SECONDS
    observed_size: tuple[int, int] | None = None
    changed_at: float = 0.0
    active: bool = False

    def observe(self, size: tuple[int, int], now: float) -> bool:
        if not self.active or size != self.observed_size:
            self.observed_size = size
            self.changed_at = now
            self.active = True
            return False
        return now - self.changed_at >= self.delay_seconds

    def defer(self, now: float) -> None:
        self.changed_at = now

    def clear(self) -> None:
        self.observed_size = None
        self.changed_at = 0.0
        self.active = False


def _windows_graphics_available() -> bool:
    if ok_window.WINDOWS_BUILD_NUMBER < ok_window.WGC_MIN_BUILD:
        return False
    try:
        from ok.rotypes.roapi import GetActivationFactory
        from ok.rotypes.Windows.Graphics.Capture import IGraphicsCaptureItemInterop

        GetActivationFactory("Windows.Graphics.Capture.GraphicsCaptureItem").astype(
            IGraphicsCaptureItemInterop
        )
        return True
    except Exception as exc:
        ok_window.logger.error(f"check WGC available failed: {exc}", exception=exc)
        return False


def _capture_size(size: object) -> tuple[int, int]:
    if isinstance(size, tuple):
        return int(size[0]), int(size[1])
    return int(getattr(size, "Width", 0)), int(getattr(size, "Height", 0))


def _valid_capture_size(size: object) -> bool:
    width, height = _capture_size(size)
    min_width, min_height = WGC_MIN_CAPTURE_SIZE
    return width >= min_width and height >= min_height


def _capture_identity_signature(hwnd_window: object) -> tuple:
    """Return only identities that require a full WGC session replacement."""

    hwnds = tuple(
        hwnd_info[0] for hwnd_info in (getattr(hwnd_window, "hwnds", None) or [])
    )
    return (
        getattr(hwnd_window, "hwnd", 0),
        getattr(hwnd_window, "top_hwnd", 0),
        hwnds,
    )


def patch_hwnd_capture_target_signature(hwnd_window_class: type) -> None:
    if getattr(hwnd_window_class, _HWND_PATCH_MARKER, False):
        return
    hwnd_window_class.capture_target_signature = property(
        _capture_identity_signature
    )
    setattr(hwnd_window_class, _HWND_PATCH_MARKER, True)


def _resize_gate(capture: object) -> ResizeStabilityGate:
    gate = getattr(capture, "_ok_bd2_resize_gate", None)
    if gate is None:
        gate = ResizeStabilityGate()
        setattr(capture, "_ok_bd2_resize_gate", gate)
    return gate


def _supported_target_signature(capture: object) -> tuple | None:
    return getattr(capture, "_ok_bd2_supported_target_signature", None)


def _remember_supported_target(capture: object) -> None:
    window = getattr(capture, "hwnd_window", None)
    if window is not None:
        capture._ok_bd2_supported_target_signature = _capture_identity_signature(window)


def patch_windows_graphics_capture_class(capture_class: type) -> None:
    """Patch ok-script 1.0.190 without editing the installed dependency."""

    if getattr(capture_class, _WGC_PATCH_MARKER, False):
        return

    original_init = capture_class.__init__
    original_start_or_stop = capture_class.start_or_stop
    original_close = capture_class.close

    def stable_init(self, *args, **kwargs):
        self._ok_bd2_resize_gate = ResizeStabilityGate()
        self._ok_bd2_last_invalid_size_log = 0.0
        self._ok_bd2_supported_target_signature = None
        original_init(self, *args, **kwargs)

    def stable_start_or_stop(self, capture_cursor=False):
        window = getattr(self, "hwnd_window", None)
        if window is not None and getattr(window, "exists", False):
            size = (getattr(window, "width", 0), getattr(window, "height", 0))
            target_signature = _capture_identity_signature(window)
            supported_signature = _supported_target_signature(self)
            if supported_signature is not None and supported_signature != target_signature:
                self._ok_bd2_supported_target_signature = None
                supported_signature = None
            if supported_signature is not None and not _valid_capture_size(size):
                now = time.monotonic()
                last_log = float(
                    getattr(self, "_ok_bd2_last_invalid_size_log", 0.0)
                )
                if now - last_log >= WGC_INVALID_SIZE_LOG_INTERVAL:
                    logger.info(
                        "WGC closing because the established window is below the "
                        f"supported size: {size[0]}x{size[1]}"
                    )
                    self._ok_bd2_last_invalid_size_log = now
                self.close()
                return False
        return original_start_or_stop(self, capture_cursor=capture_cursor)

    def stable_frame_arrived_callback(self, *args):
        next_frame = None
        frame = None
        reset_size: tuple[int, int] | None = None
        close_for_invalid_size = False
        gate = _resize_gate(self)

        with self.lock:
            if self.exit_event.is_set():
                logger.warning("frame_arrived_callback exit_event.is_set() return")
                return
            try:
                self.last_frame_time = time.time()
                if self.frame_pool is not None:
                    next_frame = self.frame_pool.TryGetNextFrame()

                if next_frame is not None:
                    observed_size = _capture_size(next_frame.ContentSize)
                    current_size = _capture_size(self.last_size)
                    now = time.monotonic()

                    if gate.active or observed_size != current_size:
                        stable = gate.observe(observed_size, now)
                        if stable:
                            if (
                                _valid_capture_size(observed_size)
                                or _supported_target_signature(self) is None
                            ):
                                if observed_size != current_size:
                                    reset_size = observed_size
                                else:
                                    gate.clear()
                            else:
                                close_for_invalid_size = True
                    elif self.frame_requested.is_set():
                        if _valid_capture_size(observed_size):
                            _remember_supported_target(self)
                        frame = self.convert_dx_frame(next_frame)
            except Exception as exc:
                logger.error(
                    f"frame_arrived_callback error {exc}", exception=exc
                )
                return
            finally:
                if next_frame is not None and hasattr(next_frame, "Close"):
                    next_frame.Close()

            # Recreate or close only after the current frame is closed. Keeping
            # this under the same lock prevents close() from releasing D3D
            # resources in parallel with the resize operation.
            if close_for_invalid_size:
                logger.info(
                    "WGC closing after the established capture shrank below the "
                    f"supported size: {observed_size[0]}x{observed_size[1]}"
                )
                self.close()
                return

            if reset_size is not None:
                try:
                    from ok.rotypes.Windows.Graphics import SizeInt32

                    size = SizeInt32(*reset_size)
                    self.reset_framepool(size)
                    self.last_size = size
                    gate.clear()
                    if _valid_capture_size(reset_size):
                        _remember_supported_target(self)
                    logger.info(
                        "WGC frame pool resized once after stability: "
                        f"{reset_size[0]}x{reset_size[1]}"
                    )
                except Exception as exc:
                    gate.defer(time.monotonic())
                    logger.error(
                        f"WGC stable resize failed for {reset_size}: {exc}",
                        exception=exc,
                    )

            if frame is not None:
                self.last_frame = frame
                self.frame_requested.clear()
                self.frame_event.set()

    def stable_close(self):
        with self.lock:
            _resize_gate(self).clear()
            return original_close(self)

    capture_class.__init__ = stable_init
    capture_class.start_or_stop = stable_start_or_stop
    capture_class.frame_arrived_callback = stable_frame_arrived_callback
    capture_class.close = stable_close
    setattr(capture_class, _WGC_PATCH_MARKER, True)


def enable_windows_10_wgc() -> None:
    ok.windows_graphics_available = _windows_graphics_available
    ok_window.windows_graphics_available = _windows_graphics_available
    capture_update.windows_graphics_available = _windows_graphics_available

    from ok.device.capture_methods.hwnd_window import HwndWindow
    from ok.device.capture_methods.windows_graphics import (
        WindowsGraphicsCaptureMethod,
    )

    patch_hwnd_capture_target_signature(HwndWindow)
    patch_windows_graphics_capture_class(WindowsGraphicsCaptureMethod)
