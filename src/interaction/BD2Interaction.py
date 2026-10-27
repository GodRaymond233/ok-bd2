import ctypes
import threading
import time
from typing import Callable

import win32api
import win32con
import win32gui
from ok.device.intercation import INPUT, MOUSEINPUT, PostMessageInteraction, SendInput
from ok.util.logger import Logger
from win32api import GetCursorPos, SetCursorPos
from winerror import ERROR_ACCESS_DENIED

logger = Logger.get_logger(__name__)

CLICK_MODE_STANDARD = "正式版方案（点击时会占用鼠标）"
CLICK_MODE_BACKGROUND = "测试版方案（后台点击）"
CLICK_MODE_OPTIONS = (CLICK_MODE_STANDARD, CLICK_MODE_BACKGROUND)
INPUT_WAIT_TIMEOUT = 30.0
INPUT_WAIT_INTERVAL = 0.25
MOUSE_PRESS_MIN_SECONDS = 0.05
MOUSE_RELEASE_SETTLE_SECONDS = 0.05


class BD2Interaction(PostMessageInteraction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None
        self._operating = False
        self._input_lock = threading.RLock()
        self._click_mode_provider: Callable[[], str] | None = None
        self.user32 = ctypes.windll.user32
        self._activate_required = True
        self.hwnd_window.visible_monitors.append(self)

    def on_visible(self, visible):
        self._activate_required = not visible

    def scroll(self, x, y, scroll_amount):
        with self._input_lock:
            self.try_activate()
            logger.debug(f"scroll {x}, {y}, {scroll_amount}")

            base_hwnd = self.hwnd_window.top_hwnd or self.hwnd_window.hwnd
            if x > 0 and y > 0:
                top_x, top_y = self.hwnd_window.get_top_window_cords(x, y)
                abs_x, abs_y = win32gui.ClientToScreen(base_hwnd, (int(top_x), int(top_y)))
                self.bg_mouse_pos = (top_x, top_y)
                self._dynamic_target_hwnd = self._target_hwnd_at(abs_x, abs_y, base_hwnd)
                long_position = win32api.MAKELONG(abs_x, abs_y)
            else:
                self._dynamic_target_hwnd = base_hwnd
                long_position = 0

            w_param = win32api.MAKELONG(0, win32con.WHEEL_DELTA * scroll_amount)
            self.post(win32con.WM_MOUSEWHEEL, w_param, long_position)

    def _target_hwnd_at(self, abs_x, abs_y, fallback_hwnd):
        for hwnd_info in getattr(self.hwnd_window, "hwnds", []):
            candidate = hwnd_info[0]
            if not win32gui.IsWindow(candidate):
                continue
            try:
                left = hwnd_info[4]
                top = hwnd_info[5]
                right = left + hwnd_info[2]
                bottom = top + hwnd_info[3]
                if left <= abs_x < right and top <= abs_y < bottom:
                    return candidate
            except Exception:
                continue
        return fallback_hwnd

    def click(
        self,
        x=-1,
        y=-1,
        move_back=False,
        name=None,
        down_time=0.01,
        move=True,
        key="left",
    ):
        with self._input_lock:
            if self.background_click_enabled():
                if x < 0:
                    x, y = round(self.capture.width * 0.5), round(self.capture.height * 0.5)
                return PostMessageInteraction.click(
                    self,
                    x,
                    y,
                    move_back=False,
                    name=name,
                    down_time=down_time,
                    move=True,
                    key=key,
                )

            self.try_activate()
            if x < 0:
                x, y = round(self.capture.width * 0.5), round(self.capture.height * 0.5)

            should_restore = move and move_back and not self._operating
            if move:
                if should_restore:
                    self.cursor_position = GetCursorPos()
                abs_x, abs_y = self.capture.get_abs_cords(x, y)
                SetCursorPos((abs_x, abs_y))
                time.sleep(0.025)

            click_pos = win32api.MAKELONG(x, y)
            if key == "left":
                btn_down = win32con.WM_LBUTTONDOWN
                btn_mk = win32con.MK_LBUTTON
                btn_up = win32con.WM_LBUTTONUP
            elif key == "middle":
                btn_down = win32con.WM_MBUTTONDOWN
                btn_mk = win32con.MK_MBUTTON
                btn_up = win32con.WM_MBUTTONUP
            else:
                btn_down = win32con.WM_RBUTTONDOWN
                btn_mk = win32con.MK_RBUTTON
                btn_up = win32con.WM_RBUTTONUP
            self.post(btn_down, btn_mk, click_pos)
            try:
                time.sleep(max(down_time, MOUSE_PRESS_MIN_SECONDS))
            finally:
                self.post(btn_up, 0, click_pos)
                # 游戏按帧采样鼠标；松开后先留在目标上，再由外层操作恢复位置。
                time.sleep(MOUSE_RELEASE_SETTLE_SECONDS)

            if should_restore:
                self._restore_cursor()

    def set_click_mode_provider(self, provider: Callable[[], str] | None) -> None:
        with self._input_lock:
            self._click_mode_provider = provider

    def background_click_enabled(self) -> bool:
        provider = getattr(self, "_click_mode_provider", None)
        if provider is None:
            return False
        try:
            return provider() == CLICK_MODE_BACKGROUND
        except Exception as error:
            logger.error("read click mode exception", error)
            return False

    def wait_for_input(self, wait=time.sleep, status=None):
        """Wait for cursor access before any callback or input blocking starts."""
        deadline = time.monotonic() + INPUT_WAIT_TIMEOUT
        interrupted = False
        while True:
            try:
                position = GetCursorPos()
            except win32api.error as error:
                if error.winerror != ERROR_ACCESS_DENIED:
                    raise
                if not interrupted:
                    interrupted = True
                    message = "输入桌面暂不可用（可能为UAC），等待恢复，最长30秒"
                    logger.warning(message)
                    if status is not None:
                        status(message)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待输入桌面恢复超时，本次操作尚未执行") from error
                wait(min(INPUT_WAIT_INTERVAL, remaining))
            else:
                if interrupted:
                    self._activate_required = True
                    if status is not None:
                        status("输入桌面已恢复，继续尚未执行的操作")
                return position

    def operate(
        self, fun: Callable, block=False, restore_cursor=True, *,
        input_wait=time.sleep, input_status=None,
    ):
        with self._input_lock:
            result = None
            is_outer_operate = False
            if not self._operating:
                self.cursor_position = self.wait_for_input(input_wait, input_status)
                self._operating = True
                is_outer_operate = True

            try:
                if block:
                    self.block_input()
                result = fun()
            except Exception as e:
                logger.error("operate exception", e)
                raise
            finally:
                if is_outer_operate:
                    self._operating = False
                    if restore_cursor:
                        self._restore_cursor()
                if block:
                    self.unblock_input()
            return result

    def wait_until_idle(self, timeout: float = 2.0) -> bool:
        """Wait until the current mouse operation releases the interaction lock."""
        acquired = self._input_lock.acquire(timeout=max(0.0, timeout))
        if acquired:
            self._input_lock.release()
        return acquired

    def move(self, x, y, down_btn=0):
        with self._input_lock:
            return super().move(x, y, down_btn=down_btn)

    def swipe(self, x1, y1, x2, y2, duration=3, settle_time=0):
        with self._input_lock:
            return super().swipe(
                x1,
                y1,
                x2,
                y2,
                duration=duration,
                settle_time=settle_time,
            )

    def right_click(self, x=-1, y=-1, move_back=False, name=None):
        with self._input_lock:
            return super().right_click(x, y, move_back=move_back, name=name)

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        with self._input_lock:
            return super().mouse_down(x, y, name=name, key=key)

    def update_mouse_pos(self, x, y, activate=True):
        with self._input_lock:
            return super().update_mouse_pos(x, y, activate=activate)

    def mouse_up(self, key="left"):
        with self._input_lock:
            return super().mouse_up(key=key)

    def _restore_cursor(self):
        time.sleep(0.025)
        try:
            SetCursorPos(self.cursor_position)
        except Exception as e:
            logger.error("restore cursor exception", e)

    def block_input(self):
        self.user32.BlockInput(True)

    def unblock_input(self):
        self.user32.BlockInput(False)

    def move_mouse_relative(self, dx, dy):
        with self._input_lock:
            mi = MOUSEINPUT(dx, dy, 0, 1, 0, None)
            i = INPUT(0, mi)
            SendInput(1, ctypes.pointer(i), ctypes.sizeof(INPUT))

    def try_activate(self):
        if self._activate_required:
            if self.hwnd_window.is_foreground():
                self._activate_required = False
                return
            super().try_activate()
            self._activate_required = not self.hwnd_window.is_foreground()
