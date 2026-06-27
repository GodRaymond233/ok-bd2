import ok
from ok.device.capture_methods import update as capture_update
from ok.util import window as ok_window


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


def enable_windows_10_wgc() -> None:
    ok.windows_graphics_available = _windows_graphics_available
    ok_window.windows_graphics_available = _windows_graphics_available
    capture_update.windows_graphics_available = _windows_graphics_available
