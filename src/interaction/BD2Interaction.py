import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

import win32api
import win32con
import win32gui
from ok import og
from ok.device.intercation import (
    INPUT,
    MOUSEINPUT,
    PostMessageInteraction,
    SendInput,
)
from ok.util.logger import Logger
from win32api import GetCursorPos, SetCursorPos

from src.interaction.keyboard_layout import QwertyPhysicalKeyMapper
from src.tasks.BaseBD2Task import HOTKEY_CONFIG_LEGACY_NAME, HOTKEY_CONFIG_NAME

logger = Logger.get_logger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MAPVK_VK_TO_VSC = 0
GUARD_INTERVAL_SECONDS = 0.005
APPLY_TIMEOUT_SECONDS = 0.5
HOTKEY_WAIT_TIMEOUT_SECONDS = 0.2


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    ]


class KEYBOARDINPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]


class BD2Interaction(PostMessageInteraction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None
        self._operating = False
        self._input_lock = threading.RLock()
        self.user32 = ctypes.windll.user32
        self._configure_user32_keyboard_api()
        self.qwerty_physical_key_mapper = QwertyPhysicalKeyMapper()
        self._disable_key_mapping = 0
        self._activate_required = True
        self._held_key_repeaters = {}
        self._managed_key_lock = threading.RLock()
        self._managed_key_cv = threading.Condition(self._managed_key_lock)
        self._managed_desired_keys = set()
        self._managed_stop = threading.Event()
        self._managed_guard_started = False
        self.hwnd_window.visible_monitors.append(self)

    def _configure_user32_keyboard_api(self):
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.TranslateMessage.restype = wintypes.BOOL
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = wintypes.LPARAM

    def on_visible(self, visible):
        self._activate_required = not visible

    def send_key(self, key, down_time=0.01):
        with self._input_lock:
            key = self._map_key(key)
            self._disable_key_mapping += 1
            try:
                self._managed_key_down(key)
                time.sleep(down_time)
                return self._managed_key_up(key)
            finally:
                self._managed_key_up(key)
                self._disable_key_mapping -= 1

    def send_key_down(self, key, activate=True):
        with self._input_lock:
            key = self._map_key(key)
            return self._managed_key_down(key, activate=activate)

    def send_key_up(self, key):
        with self._input_lock:
            key = self._map_key(key)
            return self._managed_key_up(key)

    def send_key_debug(self, key, mode="standard", down_time=0.02):
        with self._input_lock:
            key = self._map_key(key)
            if mode == "maa_managed":
                self._managed_key_down(key)
                time.sleep(down_time)
                return self._managed_key_up(key)
            if mode == "standard":
                return super().send_key(key, down_time=down_time)
            if mode == "activate_standard":
                self.force_activate()
                return super().send_key(key, down_time=down_time)
            if mode == "char":
                self.force_activate()
                return self._post_key_sequence(
                    key,
                    self._candidate_hwnds(primary_only=True),
                    down_time=down_time,
                    include_char=True,
                    fixed_lparam=False,
                    char_uses_vk=False,
                )
            if mode == "fixed_char":
                self.force_activate()
                return self._post_key_sequence(
                    key,
                    self._candidate_hwnds(primary_only=True),
                    down_time=down_time,
                    include_char=True,
                    fixed_lparam=True,
                    char_uses_vk=True,
                )
            if mode == "all_windows":
                self.force_activate()
                return self._post_key_sequence(
                    key,
                    self._candidate_hwnds(),
                    down_time=down_time,
                    include_char=False,
                    fixed_lparam=False,
                    char_uses_vk=False,
                )
            if mode == "all_windows_char":
                self.force_activate()
                return self._post_key_sequence(
                    key,
                    self._candidate_hwnds(),
                    down_time=down_time,
                    include_char=True,
                    fixed_lparam=False,
                    char_uses_vk=False,
                )
            raise ValueError(f"Unknown key debug mode: {mode}")

    def force_activate(self):
        base_hwnd = self.hwnd_window.hwnd
        current_hwnd = self.hwnd
        self._send_activate_message(base_hwnd, use_post=True)
        self.activate(base_hwnd)
        if current_hwnd != base_hwnd:
            self._send_activate_message(current_hwnd, use_post=True)
            self.activate(current_hwnd)
        self._activate_required = False

    def on_destroy(self):
        self._stop_managed_key_guard()
        try:
            return super().on_destroy()
        except AttributeError:
            return None

    def describe_key_debug_targets(self):
        descriptions = []
        for hwnd in self._candidate_hwnds():
            descriptions.append(self._describe_hwnd(hwnd))
        return descriptions

    def _post_key_sequence(
        self,
        key,
        hwnds,
        down_time=0.02,
        include_char=False,
        fixed_lparam=False,
        char_uses_vk=False,
    ):
        vk_code = self.get_key_by_str(key)
        down_lparam = 0x1E0001 if fixed_lparam else self.make_lparam(vk_code, is_up=False)
        up_lparam = 0xC01E0001 if fixed_lparam else self.make_lparam(vk_code, is_up=True)
        char_code = vk_code if char_uses_vk else self._char_code(key, vk_code)
        for hwnd in hwnds:
            if not win32gui.IsWindow(hwnd):
                continue
            self.post(win32con.WM_KEYDOWN, vk_code, down_lparam, hwnd=hwnd)
            if include_char and char_code is not None:
                self.post(win32con.WM_CHAR, char_code, down_lparam, hwnd=hwnd)
            time.sleep(down_time)
            self.post(win32con.WM_KEYUP, vk_code, up_lparam, hwnd=hwnd)

    def _post_key_down(self, key, repeated=False):
        vk_code = self.get_key_by_str(key)
        lparam = self.make_lparam(vk_code, is_up=False)
        if repeated:
            lparam |= 1 << 30
        for hwnd in self._candidate_hwnds(primary_only=True):
            if win32gui.IsWindow(hwnd):
                self.post(win32con.WM_KEYDOWN, vk_code, lparam, hwnd=hwnd)

    def _post_key_up(self, key):
        vk_code = self.get_key_by_str(key)
        lparam = self.make_lparam(vk_code, is_up=True)
        for hwnd in self._candidate_hwnds(primary_only=True):
            if win32gui.IsWindow(hwnd):
                self.post(win32con.WM_KEYUP, vk_code, lparam, hwnd=hwnd)

    def _start_key_repeater(self, key):
        self._stop_key_repeater(key)
        stop_event = threading.Event()
        self._held_key_repeaters[key] = stop_event

        def repeat_keydown():
            while not stop_event.wait(0.05):
                self._post_key_down(key, repeated=True)

        threading.Thread(
            target=repeat_keydown,
            name=f"BD2KeyRepeat-{key}",
            daemon=True,
        ).start()

    def _stop_key_repeater(self, key):
        stop_event = self._held_key_repeaters.pop(key, None)
        if stop_event is not None:
            stop_event.set()

    def _managed_key_down(self, key, activate=True):
        vk_code = self.get_key_by_str(key)
        if activate:
            self._send_activation_hint()
        self._ensure_managed_guard()
        with self._managed_key_cv:
            self._managed_desired_keys.add(vk_code)
            self._managed_key_cv.notify_all()
        return self._wait_for_key_state(vk_code, pressed=True)

    def _managed_key_up(self, key):
        vk_code = self.get_key_by_str(key)
        with self._managed_key_cv:
            self._managed_desired_keys.discard(vk_code)
            self._managed_key_cv.notify_all()
        return self._wait_for_key_state(vk_code, pressed=False)

    def _ensure_managed_guard(self):
        with self._managed_key_lock:
            if self._managed_guard_started:
                return
            self._managed_stop.clear()
            threading.Thread(
                target=self._managed_key_guard_loop,
                name="BD2ManagedKeyGuard",
                daemon=True,
            ).start()
            self._managed_guard_started = True

    def _stop_managed_key_guard(self):
        with self._managed_key_cv:
            desired_keys = list(self._managed_desired_keys)
            self._managed_desired_keys.clear()
            self._managed_stop.set()
            self._managed_key_cv.notify_all()
        for vk_code in desired_keys:
            self._send_keyboard_event(vk_code, key_up=True)

    def _managed_key_guard_loop(self):
        while not self._managed_stop.is_set():
            with self._managed_key_cv:
                desired_keys = set(self._managed_desired_keys)
                self._managed_key_cv.wait(timeout=GUARD_INTERVAL_SECONDS)

            for vk_code in desired_keys:
                if not self._is_key_pressed_now(vk_code):
                    self._ensure_key_pressed(vk_code)

        with self._managed_key_lock:
            self._managed_guard_started = False

    def _wait_for_key_state(self, vk_code, pressed):
        if pressed:
            return self._ensure_key_pressed(vk_code)
        return self._ensure_key_released(vk_code)

    def _ensure_key_pressed(self, vk_code):
        if self._is_key_pressed_now(vk_code):
            return True

        deadline = time.monotonic() + APPLY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._send_activation_hint()
            self._press_key_with_hotkey_guard(vk_code)
            if self._is_key_pressed_now(vk_code):
                return True
            time.sleep(GUARD_INTERVAL_SECONDS)

        logger.warning(
            f"managed key press did not apply vk={vk_code} "
            f"current={self._is_key_pressed_now(vk_code)}"
        )
        return False

    def _ensure_key_released(self, vk_code):
        if not self._is_key_pressed_now(vk_code):
            return True

        deadline = time.monotonic() + APPLY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._send_activation_hint()
            self._send_keyboard_event(vk_code, key_up=True)
            if not self._is_key_pressed_now(vk_code):
                return True
            time.sleep(GUARD_INTERVAL_SECONDS)

        logger.warning(
            f"managed key release did not apply vk={vk_code} "
            f"current={self._is_key_pressed_now(vk_code)}"
        )
        return False

    def _press_key_with_hotkey_guard(self, vk_code):
        hotkey_id = 0xBD2000 + (vk_code & 0xFFFF)
        registered = bool(self.user32.RegisterHotKey(None, hotkey_id, 0, vk_code))
        if not registered:
            logger.warning(f"RegisterHotKey failed for managed key vk={vk_code}")

        try:
            sent = self._send_keyboard_event(vk_code, key_up=False)
            if registered:
                self._drain_managed_hotkey(hotkey_id)
            return sent
        finally:
            if registered:
                self.user32.UnregisterHotKey(None, hotkey_id)

    def _drain_managed_hotkey(self, hotkey_id):
        message = wintypes.MSG()
        deadline = time.monotonic() + HOTKEY_WAIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            while self.user32.PeekMessageW(
                ctypes.byref(message),
                None,
                win32con.WM_HOTKEY,
                win32con.WM_HOTKEY,
                win32con.PM_REMOVE,
            ):
                if message.wParam == hotkey_id:
                    return True
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
            time.sleep(GUARD_INTERVAL_SECONDS)
        return False

    def _send_activation_hint(self):
        for hwnd in self._candidate_hwnds(primary_only=True):
            self._send_activate_message(hwnd, use_post=True)

    def _send_activate_message(self, hwnd, use_post=True):
        if not hwnd or not win32gui.IsWindow(hwnd):
            return
        if use_post:
            win32gui.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
        else:
            win32gui.SendMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    def _is_key_pressed_now(self, vk_code):
        return bool(self.user32.GetAsyncKeyState(vk_code) & 0x8000)

    @staticmethod
    def _send_keyboard_event(vk_code, key_up=False):
        scan_code = win32api.MapVirtualKey(vk_code, MAPVK_VK_TO_VSC)
        flags = KEYEVENTF_KEYUP if key_up else 0
        keyboard_input = KEYBOARDINPUT(
            INPUT_KEYBOARD,
            INPUT_UNION(ki=KEYBDINPUT(vk_code, scan_code, flags, 0, None)),
        )
        written = SendInput(1, ctypes.pointer(keyboard_input), ctypes.sizeof(KEYBOARDINPUT))
        if written != 1:
            logger.warning(f"SendInput keyboard failed vk={vk_code} key_up={key_up}")
        return written == 1

    def _candidate_hwnds(self, primary_only=False):
        candidates = []
        for hwnd in (self.hwnd_window.hwnd, self.hwnd_window.top_hwnd, self.hwnd):
            if hwnd:
                candidates.append(hwnd)
        if not primary_only:
            for hwnd_info in getattr(self.hwnd_window, "hwnds", []):
                if hwnd_info and hwnd_info[0]:
                    candidates.append(hwnd_info[0])

        unique = []
        for hwnd in candidates:
            if hwnd not in unique:
                unique.append(hwnd)
        return unique

    @staticmethod
    def _char_code(key, vk_code):
        key = str(key).lower()
        named_chars = {
            "esc": 27,
            "enter": 13,
            "return": 13,
            "tab": 9,
            "backspace": 8,
            "space": 32,
        }
        if key in named_chars:
            return named_chars[key]
        if len(key) == 1:
            return ord(key)
        if 32 <= vk_code <= 126:
            return vk_code
        return None

    @staticmethod
    def _describe_hwnd(hwnd):
        if not hwnd:
            return "0:<empty>"
        if not win32gui.IsWindow(hwnd):
            return f"{hwnd}:<invalid>"
        try:
            class_name = win32gui.GetClassName(hwnd)
        except Exception as e:
            class_name = f"<class error: {e}>"
        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception as e:
            title = f"<title error: {e}>"
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception as e:
            rect = f"<rect error: {e}>"
        return f"{hwnd}:{class_name}:{title}:{rect}"

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

    def _map_key(self, key):
        key_config = og.global_config.get_config(HOTKEY_CONFIG_NAME)
        if key_config is None:
            key_config = og.global_config.get_config(HOTKEY_CONFIG_LEGACY_NAME)
        use_qwerty = key_config.get(
            "使用 QWERTY 物理按键",
            key_config.get("Use QWERTY Physical Keys", False),
        )
        if self._disable_key_mapping or not use_qwerty:
            return key

        return self.qwerty_physical_key_mapper.map_key(key) or key

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
            time.sleep(down_time)
            self.post(btn_up, 0, click_pos)

            if should_restore:
                self._restore_cursor()

    def operate(self, fun: Callable, block=False, restore_cursor=True):
        with self._input_lock:
            result = None
            is_outer_operate = False
            if not self._operating:
                self.cursor_position = GetCursorPos()
                self._operating = True
                is_outer_operate = True

            if block:
                self.block_input()
            try:
                result = fun()
            except Exception as e:
                logger.error("operate exception", e)
            finally:
                if is_outer_operate:
                    self._operating = False
                    if restore_cursor:
                        self._restore_cursor()
                if block:
                    self.unblock_input()
            return result

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
        mi = MOUSEINPUT(dx, dy, 0, 1, 0, None)
        i = INPUT(0, mi)
        SendInput(1, ctypes.pointer(i), ctypes.sizeof(INPUT))

    def try_activate(self):
        if self._activate_required:
            if not self.hwnd_window.is_foreground():
                super().try_activate()
            self._activate_required = False
