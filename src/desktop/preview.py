"""Bounded preview transport; diagnostics keep the unscaled source frame."""

import threading
import time

import cv2


def jpeg(frame):
    image = frame
    height, width = image.shape[:2]
    if image.ndim == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if width > 960:
        image = cv2.resize(image, (960, max(1, round(height * 960 / width))))
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not success:
        raise ValueError("画面编码失败")
    return encoded.tobytes()


class LivePreview:
    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = threading.Lock()
        self._frame = None
        self._frame_time = 0.0
        self._jpeg = None
        self._encoded_at = 0.0

    def latest_frame(self):
        with self._lock:
            age = time.time() - self._frame_time
            if self._frame is None or age > 2:
                return None, None
            return self._frame.copy(), max(0.0, age)

    def image(self):
        with self._lock:
            method = getattr(self.runtime.device_manager, "capture_method", None)
            if method is None or not method.connected():
                self._frame = self._jpeg = None
                raise ValueError("等待游戏窗口和截图方式连接")
            if self._jpeg is not None and time.monotonic() - self._encoded_at < 0.2:
                return self._jpeg
            executor = self.runtime.executor
            frame = executor.nullable_frame()
            frame_time = getattr(executor, "_last_frame_time", 0)
            if frame is None or time.time() - frame_time > 0.2:
                frame = method.get_frame()
                frame_time = time.time()
            if frame is None:
                raise ValueError("暂无游戏画面，请确认游戏窗口可见")
            self._frame, self._frame_time = frame.copy(), frame_time
            self._jpeg = jpeg(self._frame)
            self._encoded_at = time.monotonic()
            return self._jpeg
