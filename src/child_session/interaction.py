"""Host-side mouse adapter. No host desktop input APIs are used here."""

import threading

from ok.device.interaction_methods.base import BaseInteraction

from src.interaction.BD2Interaction import InputTargetChanged


def window_context(info):
    return tuple(info.get(key) for key in ("session", "pid", "hwnd", "width", "height"))


class BD2ChildSessionInteraction(BaseInteraction):
    is_child_session = True

    def __init__(self, capture, hwnd_window=None):
        if not getattr(getattr(capture, "hwnd_window", None), "is_child_session", False):
            raise ValueError("子会话输入必须配对子会话截图")
        super().__init__(capture)
        self.hwnd_window = hwnd_window or capture.hwnd_window
        self._input_lock = threading.RLock()

    def target_context(self):
        return window_context(self.capture.hwnd_window.info(refresh=True))

    def background_click_enabled(self):
        return True

    def operate(self, func, block=True, restore_cursor=True, expected_context=None):
        with self._input_lock:
            previous = getattr(self, "_operation_context", None)
            self._operation_context = expected_context
            try:
                return func()
            finally:
                self._operation_context = previous

    def wait_until_idle(self, timeout=5):
        acquired = self._input_lock.acquire(timeout=timeout)
        if acquired:
            self._input_lock.release()
        return acquired

    def _input(self, operation, **values):
        with self._input_lock:
            capture = self.capture
            if not getattr(capture.hwnd_window, "is_child_session", False):
                raise RuntimeError("子会话截图与输入配对已失效")
            expected = getattr(self, "_operation_context", None) or capture.input_context
            if not expected or expected != self.target_context():
                raise InputTargetChanged("子会话窗口已变化，请重新截图识别")
            reply = capture.client.call("bd2_mouse", operation=operation,
                                        context=list(expected), **values)
            if reply.get("target_changed"):
                raise InputTargetChanged("子会话窗口已变化，本次输入尚未发送")
            if not reply.get("ok"):
                raise RuntimeError("子会话鼠标操作失败")
            return True

    def click(self, x=-1, y=-1, move_back=False, name=None, move=True,
              down_time=0.05, key="left"):
        if x < 0 or y < 0:
            x, y = self.capture.hwnd_window.width // 2, self.capture.hwnd_window.height // 2
        return self._input("click", x=int(x), y=int(y), key=key, down_time=down_time)

    def scroll(self, x, y, scroll_amount):
        return self._input("scroll", x=int(x), y=int(y), amount=int(scroll_amount))

    def swipe(self, from_x, from_y, to_x, to_y, duration, settle_time=0):
        return self._input("swipe", from_x=int(from_x), from_y=int(from_y),
                           to_x=int(to_x), to_y=int(to_y), duration=float(duration) / 1000,
                           settle_time=float(settle_time or 0))
