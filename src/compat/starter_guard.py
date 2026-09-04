from __future__ import annotations

import ctypes
import ntpath
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import wraps
from importlib import import_module
from time import monotonic

from ok import Logger

from src.game_path import get_launcher_exe_names

logger = Logger.get_logger(__name__)

DEFAULT_SETUP_EXE = "BD2StarterSetup.exe"
ENV_SETUP_EXE = "OK_BD2_SETUP_EXE"
ENV_WIZARD_GRACE_SECONDS = "OK_BD2_STARTER_WIZARD_GRACE_SECONDS"
DEFAULT_WIZARD_GRACE_SECONDS = 30.0
DEFAULT_POLL_SECONDS = 2.0
WIZARD_RE_NOTIFY_SECONDS = 60.0

_WAIT_WRAP_MARKER = "_ok_bd2_starter_wait_guard_enabled"
_EXECUTE_WRAP_MARKER = "_ok_bd2_starter_preflight_enabled"

_GUARD_STATE = {
    "preflight_notified": False,
    "last_wizard_notify_at": 0.0,
}


@dataclass(frozen=True)
class StarterWindow:
    pid: int
    exe_name: str
    title: str
    width: int
    height: int


def reset_starter_guard_state() -> None:
    _GUARD_STATE["preflight_notified"] = False
    _GUARD_STATE["last_wizard_notify_at"] = 0.0


def get_setup_exe_names(env: dict[str, str] | None = None) -> list[str]:
    value = _env(env, ENV_SETUP_EXE, DEFAULT_SETUP_EXE)
    names = [_clean_exe_name(part) for part in value.split(",")]
    return [name for name in names if name]


def get_watcher_exe_names(env: dict[str, str] | None = None) -> set[str]:
    names = {name.casefold() for name in get_launcher_exe_names(env)}
    names.update(name.casefold() for name in get_setup_exe_names(env))
    return names


def get_wizard_grace_seconds(env: dict[str, str] | None = None) -> float:
    raw = _env(env, ENV_WIZARD_GRACE_SECONDS, str(DEFAULT_WIZARD_GRACE_SECONDS))
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_WIZARD_GRACE_SECONDS


def find_starter_windows(env: dict[str, str] | None = None) -> list[StarterWindow]:
    allowed = get_watcher_exe_names(env)
    if os.name != "nt" or not allowed:
        return []
    return _enumerate_visible_windows(allowed)


def starter_preflight_warning(env: dict[str, str] | None = None) -> str | None:
    from src.game_path import resolve_game_exe_path

    if resolve_game_exe_path(env=env):
        return None
    return (
        "启动前体检：未能在常见安装位置或注册表找到游戏本体 BrownDust II.exe。"
        "若游戏已安装在其他位置，请设置 OK_BD2_GAME_PATH 指向游戏本体；"
        "若尚未安装，请先运行官方 BD2StarterSetup.exe 完成安装。"
    )


def enable_starter_launch_guard() -> None:
    """Detect a lingering Starter install wizard while waiting for the game window."""
    start_controller = import_module("ok.core.start_controller")
    _patch_stable_wait(start_controller)
    _patch_execute_preflight(start_controller)


def notify_starter_wizard(windows: Iterable[StarterWindow], elapsed_seconds: float) -> None:
    lines = [
        f"Neowiz 启动器窗口已持续可见 {elapsed_seconds:.0f} 秒，但游戏窗口尚未出现。",
        "若启动器正在显示“选择安装游戏的位置”等安装/修复界面，"
        "请手动完成该向导（必要时先运行官方 BD2StarterSetup.exe 修复安装）。",
        "向导完成后启动器会继续拉起游戏，脚本会持续等待，无需重启脚本。",
    ]
    message = "\n".join(lines)
    logger.warning(f"starter wizard suspected: {[str(window) for window in windows]}")
    now = monotonic()
    if now - _GUARD_STATE["last_wizard_notify_at"] < WIZARD_RE_NOTIFY_SECONDS:
        return
    _GUARD_STATE["last_wizard_notify_at"] = now
    _emit_notification(message, "BD2 启动器安装向导提示")


def _emit_notification(message: str, title: str) -> None:
    communicate = import_module("ok.core.events").communicate
    communicate.notification.emit(message, title, True, True, "start", None, None)


class StarterWizardWatcher:
    """Watch for a Starter/Setup window that stays visible while the game window is absent."""

    def __init__(
        self,
        env: dict[str, str] | None = None,
        *,
        find_windows: Callable[[], list[StarterWindow]] | None = None,
        notify: Callable[[list[StarterWindow], float], None] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        grace_seconds: float | None = None,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.grace_seconds = (
            get_wizard_grace_seconds(env) if grace_seconds is None else max(1.0, grace_seconds)
        )
        self._find_windows = find_windows or (lambda: find_starter_windows(env))
        self._notify = notify or notify_starter_wizard
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._sleep = sleep
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._visible_since: float | None = None
        self.notified = False

    def start(self) -> None:
        if self._thread is not None or os.name != "nt":
            return
        self._thread = threading.Thread(target=self._run, name="ok-bd2-starter-guard", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout)

    def __enter__(self) -> StarterWizardWatcher:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            try:
                self.poll_once()
            except Exception as exc:
                logger.warning(f"starter guard poll failed: {exc}")

    def poll_once(self) -> None:
        windows = self._find_windows()
        now = self._clock()
        if not windows:
            self._visible_since = None
            return
        if self._visible_since is None:
            self._visible_since = now
            logger.info(f"starter window visible: {windows[0]}")
            return
        elapsed = now - self._visible_since
        if not self.notified and elapsed >= self.grace_seconds:
            self.notified = True
            self._notify(windows, elapsed)


def _patch_stable_wait(start_controller) -> None:
    original_wait = start_controller.StartController._wait_until_started_window_stable
    if getattr(original_wait, _WAIT_WRAP_MARKER, False):
        return

    @wraps(original_wait)
    def wait_with_starter_guard(self):
        with StarterWizardWatcher():
            return original_wait(self)

    setattr(wait_with_starter_guard, _WAIT_WRAP_MARKER, True)
    start_controller.StartController._wait_until_started_window_stable = wait_with_starter_guard


def _patch_execute_preflight(start_controller) -> None:
    original_execute = start_controller.execute
    if getattr(original_execute, _EXECUTE_WRAP_MARKER, False):
        return

    @wraps(original_execute)
    def execute_with_preflight(game_cmd, arguments=None, start_method="start"):
        if _is_starter_or_setup_command(game_cmd):
            message = starter_preflight_warning()
            if message:
                _warn_preflight(message)
        return original_execute(game_cmd, arguments=arguments, start_method=start_method)

    setattr(execute_with_preflight, _EXECUTE_WRAP_MARKER, True)
    start_controller.execute = execute_with_preflight


def _is_starter_or_setup_command(game_cmd) -> bool:
    command_path = str(game_cmd or "").strip().strip('"')
    if not command_path:
        return False
    allowed = get_watcher_exe_names()
    return ntpath.basename(command_path).casefold() in allowed


def _warn_preflight(message: str) -> None:
    logger.warning(message)
    if _GUARD_STATE["preflight_notified"]:
        return
    _GUARD_STATE["preflight_notified"] = True
    _emit_notification(message, "BD2 游戏启动前体检")


def _enumerate_visible_windows(allowed_exe_names: set[str]) -> list[StarterWindow]:
    import psutil

    user32 = ctypes.windll.user32
    wintypes = ctypes.wintypes
    windows: list[StarterWindow] = []
    pid_exe_names: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_value = pid.value
        if pid_value not in pid_exe_names:
            try:
                exe_path = psutil.Process(pid_value).exe() or ""
            except Exception:
                exe_path = ""
            pid_exe_names[pid_value] = os.path.basename(exe_path).casefold()
        exe_name = pid_exe_names[pid_value]
        if exe_name not in allowed_exe_names:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        windows.append(
            StarterWindow(
                pid_value,
                exe_name,
                title,
                max(0, rect.right - rect.left),
                max(0, rect.bottom - rect.top),
            )
        )
        return True

    user32.EnumWindows(enum_callback, 0)
    return windows


def _env(env: dict[str, str] | None, key: str, default: str) -> str:
    return (env if env is not None else os.environ).get(key, default).strip()


def _clean_exe_name(value: object) -> str:
    return str(value or "").strip().strip("\"'")
