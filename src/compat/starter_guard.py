from __future__ import annotations

import ntpath
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from functools import wraps
from importlib import import_module
from time import monotonic

from ok import Logger
from ok.core.events import EventSignal

from src.compat.starter_update import StarterObservation, StarterObserver
from src.game_path import get_launcher_exe_names

logger = Logger.get_logger(__name__)

DEFAULT_SETUP_EXE = "BD2StarterSetup.exe"
ENV_SETUP_EXE = "OK_BD2_SETUP_EXE"
ENV_WIZARD_GRACE_SECONDS = "OK_BD2_STARTER_WIZARD_GRACE_SECONDS"
DEFAULT_WIZARD_GRACE_SECONDS = 30.0
DEFAULT_POLL_SECONDS = 2.0
WIZARD_RE_NOTIFY_SECONDS = 60.0
UPDATE_STALL_SECONDS = 300.0
CAPTURE_LOST_GRACE_SECONDS = 300.0
OBSERVATION_FRESH_SECONDS = 15.0

starter_wait_status = EventSignal("bd2_starter_wait_status")

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
    hwnd: int = 0


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
    """Keep game startup pending while the official launcher is updating."""
    from src.compat.starter_wait_ui import install_starter_wait_ui

    start_controller = import_module("ok.core.start_controller")
    if os.name == "nt":
        # rotypes initializes STA at import time; load it before the MTA capture worker.
        try:
            import_module("ok.rotypes.roapi")
        except Exception as exc:
            logger.warning(f"starter WinRT unavailable, will try PrintWindow: {exc}")
    _patch_stable_wait(start_controller)
    _patch_execute_preflight(start_controller)
    install_starter_wait_ui()


def notify_starter_wizard(windows: Iterable[StarterWindow], elapsed_seconds: float) -> None:
    lines = [
        f"已识别到游戏安装位置选择界面，等待操作已超过 {elapsed_seconds:.0f} 秒。",
        "请在 Neowiz 启动器中选择安装位置并完成向导。",
        "向导完成后启动器会继续拉起游戏，脚本会持续等待，无需重启脚本。",
    ]
    message = "\n".join(lines)
    logger.info(f"starter wizard recognized: {[str(window) for window in windows]}")
    now = monotonic()
    if now - _GUARD_STATE["last_wizard_notify_at"] < WIZARD_RE_NOTIFY_SECONDS:
        return
    _GUARD_STATE["last_wizard_notify_at"] = now
    _emit_notification(message, "BD2 启动器安装向导提示", error=False)


def _emit_notification(message: str, title: str, *, error: bool = True) -> None:
    communicate = import_module("ok.core.events").communicate
    communicate.notification.emit(message, title, error, True, "start", None, None)


class StarterWizardWatcher:
    """Publish immutable launcher observations without blocking the startup/cancel loop."""

    def __init__(
        self,
        env: dict[str, str] | None = None,
        *,
        find_windows: Callable[[], list[StarterWindow]] | None = None,
        notify: Callable[[list[StarterWindow], float], None] | None = None,
        observe: Callable[[list[StarterWindow]], StarterObservation] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        grace_seconds: float | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.grace_seconds = (
            get_wizard_grace_seconds(env) if grace_seconds is None else max(1.0, grace_seconds)
        )
        self._find_windows = find_windows or (lambda: find_starter_windows(env))
        self._notify = notify or notify_starter_wizard
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._stop_event = threading.Event()
        self._observer = None if observe is not None else StarterObserver(self._stop_event)
        self._observe = observe or self._observer
        self._thread: threading.Thread | None = None
        self._visible_since: float | None = None
        self.notified = False
        self.latest = StarterObservation(observed_at=clock(), progress_at=clock())
        self._progress_bars = None
        self._io_baseline = None

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
        import pythoncom

        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        try:
            while not self._stop_event.is_set():
                try:
                    self.poll_once()
                except Exception as exc:
                    logger.warning(f"starter guard poll failed: {exc}")
                self._stop_event.wait(self._poll_seconds)
        finally:
            try:
                if self._observer is not None:
                    self._observer.close()
            finally:
                pythoncom.CoUninitialize()

    def poll_once(self) -> None:
        windows = self._find_windows()
        observation = self._observe(windows)
        if self._stop_event.is_set():
            return
        now = self._clock()
        previous = self.latest
        changed = (observation.identity, observation.state, observation.stage) != (
            previous.identity,
            previous.state,
            previous.stage,
        )
        bars_changed = observation.bars is not None and (
            self._progress_bars is None
            or any(abs(a - b) >= 0.003 for a, b in zip(observation.bars, self._progress_bars))
        )
        io_changed = (
            observation.io_bytes is not None
            and self._io_baseline is not None
            and (observation.io_bytes - self._io_baseline >= 65536)
        )
        progress_at = now if changed or bars_changed or io_changed else previous.progress_at
        if changed or bars_changed:
            self._progress_bars = observation.bars
        if (
            changed
            or io_changed
            or self._io_baseline is None
            or (observation.io_bytes is not None and observation.io_bytes < self._io_baseline)
        ):
            self._io_baseline = observation.io_bytes
        self.latest = replace(observation, observed_at=now, progress_at=progress_at)
        if changed:
            logger.info(f"starter state: {self.latest}")
        elif bars_changed or io_changed:
            logger.debug(f"starter progress: {self.latest}")
        if observation.state != "wizard":
            self._visible_since = None
            self.notified = False
            return
        if self._visible_since is None:
            self._visible_since = now
            return
        elapsed = now - self._visible_since
        if not self.notified and elapsed >= self.grace_seconds:
            self.notified = True
            self._notify(windows, elapsed)


class StarterWaitPolicy:
    def __init__(self, timeout: float, now: float):
        self.timeout = timeout
        self.started_at = now
        self.deadline = now + timeout
        self.last_confirmed_at = None
        self.was_protected = False

    def evaluate(self, observation: StarterObservation, now: float) -> tuple[int, str, str]:
        """Return countdown, waiting text, and a terminal error (if any)."""
        state = observation.state
        if now - observation.observed_at > OBSERVATION_FRESH_SECONDS:
            state = "unknown"
        elapsed = int(now - self.started_at)
        if state == "error":
            return 0, "", observation.stage
        if state in {"updating", "wizard"}:
            self.last_confirmed_at = now
            self.was_protected = True
            self.deadline = now + self.timeout
            if state == "wizard":
                return 0, f"等待完成游戏安装向导，已等待 {elapsed} 秒\n请在启动器中选择安装位置", ""
            message = f"游戏正在{observation.stage}，已等待 {elapsed} 秒"
            if now - observation.progress_at >= UPDATE_STALL_SECONDS:
                message += "\n连续 5 分钟未检测到进度或磁盘活动变化，请检查启动器；仍在等待"
            return 0, message, ""
        if state == "unknown" and self.last_confirmed_at is not None:
            if now - self.last_confirmed_at >= CAPTURE_LOST_GRACE_SECONDS:
                return 0, "", "持续 5 分钟无法读取游戏更新窗口，请检查启动器后重新启动任务。"
            self.deadline = now + self.timeout
            return 0, f"暂时无法读取更新进度，已等待 {elapsed} 秒\n正在重新检测启动器", ""
        if state == "absent" and self.was_protected:
            self.deadline = now + self.timeout
            self.was_protected = False
            self.last_confirmed_at = None
        remaining = self.deadline - now
        if remaining <= 0:
            return 0, "", "等待游戏窗口超时，请检查游戏启动器。"
        return max(1, int(remaining)), "", ""


def _patch_stable_wait(start_controller) -> None:
    original_wait = start_controller.StartController._wait_until_started_window_stable
    if getattr(original_wait, _WAIT_WRAP_MARKER, False):
        return

    @wraps(original_wait)
    def wait_with_starter_guard(self):
        if os.name != "nt":
            return original_wait(self)
        return _wait_for_game_window(self, start_controller)

    setattr(wait_with_starter_guard, _WAIT_WRAP_MARKER, True)
    start_controller.StartController._wait_until_started_window_stable = wait_with_starter_guard


def _wait_for_game_window(controller, start_controller) -> bool:
    cancel = threading.Event()
    policy = StarterWaitPolicy(controller.start_timeout, monotonic())
    stable_identity = None
    stable_since = None
    last_status_at = float("-inf")
    events = start_controller.communicate
    try:
        with StarterWizardWatcher() as watcher:
            while not controller.exit_event.is_set() and not cancel.is_set():
                now = monotonic()
                window = getattr(start_controller.og.device_manager, "hwnd_window", None)
                if window is not None:
                    window.do_update_window_size()
                    width, height = window.width, window.height
                    min_width, min_height = controller.STARTED_WINDOW_MIN_SIZE
                    if window.hwnd and width >= min_width and height >= min_height:
                        identity = (window.hwnd, width, height)
                        if identity != stable_identity:
                            stable_identity, stable_since = identity, now
                        elif now - stable_since >= controller.STARTED_WINDOW_STABLE_SECONDS:
                            return True
                    else:
                        stable_identity = stable_since = None
                else:
                    stable_identity = stable_since = None
                seconds, message, error = policy.evaluate(watcher.latest, now)
                if error:
                    events.starting_emulator.emit(True, controller.tr(error), 0)
                    return False
                if now - last_status_at >= DEFAULT_POLL_SECONDS:
                    events.starting_emulator.emit(False, None, seconds)
                    starter_wait_status.emit(message, cancel)
                    last_status_at = now
                controller.exit_event.wait(controller.STARTED_WINDOW_POLL_INTERVAL)
        events.starting_emulator.emit(True, None, 0)
        return False
    finally:
        # Queued UI callbacks and a slow capture worker must not outlive this wait's authority.
        cancel.set()


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
    import pywintypes
    import win32gui
    import win32process

    windows: list[StarterWindow] = []
    processes: dict[int, tuple[str, bool]] = {}

    def enum_callback(hwnd, _lparam):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in processes:
                process = psutil.Process(pid)
                name = process.name().casefold()
                allowed = name in allowed_exe_names or any(
                    parent.name().casefold() in allowed_exe_names for parent in process.parents()
                )
                processes[pid] = name, allowed
            name, allowed = processes[pid]
            if allowed:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if right - left >= 100 and bottom - top >= 60:
                    windows.append(
                        StarterWindow(
                            pid,
                            name,
                            win32gui.GetWindowText(hwnd).strip(),
                            right - left,
                            bottom - top,
                            hwnd,
                        )
                    )
        except (psutil.Error, pywintypes.error, OSError):
            pass
        return True

    win32gui.EnumWindows(enum_callback, None)
    return windows


def _env(env: dict[str, str] | None, key: str, default: str) -> str:
    return (env if env is not None else os.environ).get(key, default).strip()


def _clean_exe_name(value: object) -> str:
    return str(value or "").strip().strip("\"'")
