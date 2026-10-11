from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import cv2
import numpy as np
from ok import Logger

logger = Logger.get_logger(__name__)


@dataclass(frozen=True)
class StarterObservation:
    state: str = "absent"
    stage: str = ""
    bars: tuple[float, float] | None = None
    identity: tuple[int, int] | None = None
    io_bytes: int | None = None
    observed_at: float = 0.0
    progress_at: float = 0.0


def progress_bars(frame: np.ndarray) -> tuple[float, float] | None:
    """Measure the two aligned fills relative to window width, not as percentages."""
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame[height // 2 :, :, :3], cv2.COLOR_BGR2HSV)

    def rectangles(lower, upper):
        mask = cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        result = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if (
                w >= max(8, width * 0.008)
                and 2 <= h <= max(12, height * 0.035)
                and w >= h / 2
                and cv2.contourArea(contour) >= (w - 1) * (h - 1) * 0.8
            ):
                result.append((x, y, w, h))
        return result

    orange = rectangles((5, 110, 130), (30, 255, 255))
    blue = rectangles((90, 65, 110), (120, 255, 255))
    for ox, oy, ow, oh in sorted(orange, key=lambda rect: rect[1], reverse=True):
        for bx, by, bw, bh in blue:
            if (
                abs(ox - bx) <= max(3, width * 0.01)
                and oh <= by - oy <= max(24, height * 0.07)
                and abs(oh - bh) <= max(3, height * 0.008)
                and max(ow, bw) >= max(oh, bh) * 6
            ):
                return ow / width, bw / width
    return None


def classify_starter_frame(frame: np.ndarray | None, text: str = "") -> StarterObservation:
    text = "".join(text.casefold().split())
    failures = (
        "下载失败",
        "下載失敗",
        "更新失败",
        "更新失敗",
        "解压失败",
        "解壓失敗",
        "磁盘空间不足",
        "磁碟空間不足",
        "downloadfailed",
        "updatefailed",
        "extractionfailed",
        "notenoughdiskspace",
        "无法连接服务器",
        "無法連接伺服器",
    )
    if any(word in text for word in failures):
        return StarterObservation("error", "启动器报告下载、解压或连接失败，请检查更新窗口")
    stages = (
        ("解压", ("正在解压", "正在解壓", "解压中", "解壓中", "extracting", "unpacking")),
        ("下载", ("正在下载", "正在下載", "下载中", "下載中", "downloading")),
        ("校验", ("正在校验", "正在校驗", "正在验证", "正在驗證", "verifying", "validating")),
        ("安装", ("正在安装", "正在安裝", "installing")),
        ("更新", ("正在更新", "更新中", "updating", "patching")),
    )
    bars = progress_bars(frame) if frame is not None else None
    stage = next((name for name, words in stages if any(word in text for word in words)), "")
    if stage or bars is not None:
        return StarterObservation("updating", stage or "更新", bars)
    wizard = (
        "选择安装游戏的位置",
        "選擇安裝遊戲的位置",
        "选择安装位置",
        "選擇安裝位置",
        "选择目标位置",
        "選擇目標位置",
        "choosetheinstalllocation",
        "chooseinstalllocation",
        "selectdestinationlocation",
    )
    if any(word in text for word in wizard):
        return StarterObservation("wizard", "请选择游戏安装位置并完成启动器向导")
    return StarterObservation("unknown", "暂时无法识别更新窗口内容")


class StarterWindowCapture:
    """Own an independent capture; never rebind the game's device or interaction."""

    def __init__(self, stop_event):
        self.stop_event = stop_event
        self.key = None
        self.wgc = None
        self.bitblt = None

    def get_frame(self, window):
        import win32gui
        import win32process
        from ok.device.capture_methods.bitblt import BitBltCaptureMethod
        from ok.device.capture_methods.bitblt_utils import capture_by_bitblt, get_crop_point
        from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

        hwnd = window.hwnd
        if (
            not win32gui.IsWindow(hwnd)
            or win32gui.IsIconic(hwnd)
            or win32process.GetWindowThreadProcessId(hwnd)[1] != window.pid
        ):
            return None
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width, height = right - left, bottom - top
        if width < 16 or height < 16:
            return None
        key = (window.pid, hwnd, width, height)
        if key != self.key:
            self.close()
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            target = SimpleNamespace(
                hwnd=hwnd,
                top_hwnd=hwnd,
                hwnds=[],
                exists=True,
                width=width,
                height=height,
                window_width=right - left,
                window_height=bottom - top,
                app_exit_event=self.stop_event,
                capture_target_signature=(hwnd,),
                real_x_offset=0,
                real_y_offset=0,
                real_width=0,
                real_height=0,
            )
            self.key = key
            self.bitblt = BitBltCaptureMethod(target)
            try:
                self.wgc = WindowsGraphicsCaptureMethod(target)
            except Exception as exc:
                logger.warning(f"starter WGC unavailable: {exc}")
        frame = None
        if self.wgc is not None:
            try:
                frame = self.wgc.get_frame()
            except Exception as exc:
                logger.debug(f"starter capture failed: {exc}")
        if not self._usable(frame) and not self.stop_event.is_set():
            target = self.bitblt.hwnd_window
            x, y = get_crop_point(target.window_width, target.window_height, width, height)
            frame = capture_by_bitblt(self.bitblt, hwnd, width, height, x, y, True)
        return frame if self._usable(frame) else None

    @staticmethod
    def _usable(frame):
        return (
            isinstance(frame, np.ndarray)
            and frame.ndim == 3
            and frame.size > 0
            and frame[:, :, :3].std(axis=(0, 1)).max() > 2
        )

    def close(self):
        for capture in (self.wgc, self.bitblt):
            if capture is not None:
                capture.close()
        self.wgc = self.bitblt = None
        self.key = None


class StarterObserver:
    def __init__(self, stop_event):
        self.stop_event = stop_event
        self.captures = {}

    def __call__(self, windows):
        from dataclasses import replace

        keys = {(window.pid, window.hwnd) for window in windows}
        for key in self.captures.keys() - keys:
            self.captures.pop(key).close()
        observations = []
        for window in windows:
            if self.stop_event.is_set():
                break
            key = (window.pid, window.hwnd)
            capture = self.captures.setdefault(key, StarterWindowCapture(self.stop_event))
            try:
                frame = capture.get_frame(window)
                if self.stop_event.is_set():
                    break
                observation = classify_starter_frame(frame, self._text(frame))
                observations.append(
                    replace(
                        observation,
                        identity=key,
                        io_bytes=self._io_bytes(window.pid),
                    )
                )
            except Exception as exc:
                logger.warning(f"starter observation failed: {exc}")
                observations.append(StarterObservation("unknown", identity=key))
        priority = {"error": 4, "updating": 3, "wizard": 2, "unknown": 1, "absent": 0}
        return max(
            observations, key=lambda item: priority[item.state], default=StarterObservation()
        )

    @staticmethod
    def _text(frame):
        if frame is None:
            return ""
        from ok import og

        # Task.ocr waits for an unpaused executor, which cannot happen before startup.
        # Use the already warmed engine on the launcher's own image instead.
        engine = getattr(getattr(og, "executor", None), "_ocr_lib", {}).get("default")
        if engine is None:
            return ""
        height = frame.shape[0]
        region = frame[height // 2 :] if height > 500 and progress_bars(frame) else frame
        try:
            result = engine.ocr(region)
            return " ".join(item[1][0] for item in (result[0] or []) if item[1][1] >= 0.65)
        except Exception as exc:
            logger.debug(f"starter OCR unavailable: {exc}")
            return ""

    @staticmethod
    def _io_bytes(pid):
        import psutil

        try:
            process = psutil.Process(pid)
            counters = [item.io_counters() for item in [process, *process.children(recursive=True)]]
            return sum(item.read_bytes + item.write_bytes for item in counters)
        except (psutil.Error, OSError):
            return None

    def close(self):
        for capture in self.captures.values():
            capture.close()
        self.captures.clear()
