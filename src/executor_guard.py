"""One automation executor per Windows session.

Executor lock
    Every entry point of this code generation (Qt ``main.py``, ``main_debug.py``,
    the desktop core ``--service`` and the headless ``-t`` runs) takes the session-wide
    named mutex ``Local\\ok-bd2-executor`` before ok-script builds its executor.  The
    name does not depend on the install directory, so two installs or a Qt window
    plus the desktop client cannot drive the game at the same time.  A second
    instance is refused; nobody is terminated.  Windows releases the mutex when the
    holder exits, including a forced termination.  The handle is not inheritable, so
    a game launched by the core cannot keep it alive.

Older versions
    Versions before this module never ask for the lock and cannot be constrained.  The
    running core detects them instead: before every start request and, cached, before
    every task the executor picks.  While one is running, tasks are refused and the
    executor pauses.  Nothing is terminated, and a legacy instance started later is
    only noticed at the next check.

Identity checks for legacy detection (all must hold):
    * the process is a Python interpreter whose entry script is ``main.py`` or
      ``main_debug.py``;
    * the entry script lives in an ok-bd2 tree (``src/interaction/BD2Interaction.py``
      and ``src/config.py`` exist next to it), or the interpreter belongs to a PyAppify
      ``data/apps/ok-bd2`` install whose ``working`` tree has those files;
    * it is not this process, one of its ancestors (the shell and the bootstrap) or an
      explicitly registered process;
    * it has been running for at least ``LEGACY_GRACE_SECONDS`` (a refused new-version
      instance exits within that time);
    * it does not carry the ``OK_BD2_EXECUTOR_GUARD`` marker set by this generation.
    Matching is done on live processes only, so stale PID files and reused PIDs of
    other programs never count.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath

from ok import Logger

logger = Logger.get_logger(__name__)

LOCK_NAME = "Local\\ok-bd2-executor"
OWNER_FILE_NAME = "ok-bd2-executor-owner.json"
POSIX_LOCK_FILE_NAME = "ok-bd2-executor.lock"
GENERATION_ENV = "OK_BD2_EXECUTOR_GUARD"
GENERATION = "1"
LEGACY_GRACE_SECONDS = 3.0
LEGACY_CACHE_SECONDS = 5.0
ENTRY_SCRIPTS = frozenset({"main.py", "main_debug.py"})
APP_MARKERS = (("src", "interaction", "BD2Interaction.py"), ("src", "config.py"))
PYAPPIFY_APP_PARTS = ("data", "apps", "ok-bd2")


def mark_current_process() -> None:
    """Tag this process (and its children) as a lock-aware generation."""
    os.environ[GENERATION_ENV] = GENERATION


@dataclass(frozen=True)
class InstanceInfo:
    pid: int
    create_time: float | None
    mode: str
    cwd: str
    started_at: str

    def describe(self) -> str:
        return f"进程 {self.pid}（{self.mode}，目录 {self.cwd}，启动于 {self.started_at}）"


def _process_create_time(pid: int) -> float | None:
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:
        return None


class _Kernel32Mutex:
    """The two kernel32 calls the mutex backend needs."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def create(self, name: str):
        # NULL security attributes: the handle is not inherited by child processes.
        handle = self._kernel32.CreateMutexW(None, False, name)
        return handle, self._ctypes.get_last_error()

    def close(self, handle) -> None:
        self._kernel32.CloseHandle(handle)


class _WindowsMutexBackend:
    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str = LOCK_NAME, api=None):
        self.name = name
        self._api = api
        self._handle = None

    def _calls(self):
        if self._api is None:
            self._api = _Kernel32Mutex()
        return self._api

    def try_acquire(self) -> bool:
        api = self._calls()
        handle, error = api.create(self.name)
        if not handle:
            raise OSError(error, f"CreateMutexW failed for {self.name}")
        if error == self.ERROR_ALREADY_EXISTS:
            api.close(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle:
            self._calls().close(self._handle)
            self._handle = None


class _FileLockBackend:
    """Non-Windows stand-in (tests, development); released when the process dies."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd = None

    def try_acquire(self) -> bool:
        import fcntl

        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def default_backend():
    if os.name == "nt":
        return _WindowsMutexBackend()
    return _FileLockBackend(Path(tempfile.gettempdir()) / POSIX_LOCK_FILE_NAME)


class ExecutorLock:
    def __init__(self, backend=None, owner_file: Path | None = None, create_time=None):
        self.backend = backend or default_backend()
        self.owner_file = Path(owner_file or Path(tempfile.gettempdir()) / OWNER_FILE_NAME)
        self._create_time = create_time or _process_create_time
        self.held = False

    def acquire(self, mode: str) -> bool:
        if self.held:
            return True
        if not self.backend.try_acquire():
            return False
        self.held = True
        info = InstanceInfo(
            pid=os.getpid(),
            create_time=self._create_time(os.getpid()),
            mode=mode,
            cwd=os.getcwd(),
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        try:
            temporary = self.owner_file.with_name(f"{self.owner_file.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(asdict(info), ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.owner_file)
        except OSError as error:
            logger.warning(f"could not record executor owner: {error}")
        return True

    def holder(self) -> InstanceInfo | None:
        """The recorded holder, only if that exact process is still alive."""
        try:
            data = json.loads(self.owner_file.read_text(encoding="utf-8"))
            info = InstanceInfo(**data)
        except (OSError, ValueError, TypeError):
            return None
        alive_since = self._create_time(info.pid)
        if alive_since is None:
            return None
        if info.create_time is not None and abs(alive_since - info.create_time) > 1.0:
            return None  # the PID now belongs to another process
        return info

    def release(self) -> None:
        if not self.held:
            return
        holder = self.holder()
        if holder is not None and holder.pid == os.getpid():
            try:
                self.owner_file.unlink()
            except OSError:
                pass
        self.backend.release()
        self.held = False


@dataclass(frozen=True)
class LegacyInstance:
    pid: int
    create_time: float
    location: str
    command: str

    def describe(self) -> str:
        return f"进程 {self.pid}（{self.location}）"


def _is_python(name: str) -> bool:
    name = (name or "").lower()
    if os.name == "nt":
        return name in {"python.exe", "pythonw.exe"}
    return name.startswith("python")


def _entry_script(cmdline) -> str | None:
    for argument in list(cmdline or [])[1:]:
        if argument.lower().endswith(".py"):
            return argument
    return None


def _path_type(text: str):
    return PureWindowsPath if ("\\" in text or ":" in text[:3]) else Path


def has_app_markers(directory: str | os.PathLike) -> bool:
    base = Path(directory)
    return all((base.joinpath(*parts)).is_file() for parts in APP_MARKERS)


def pyappify_working_dir(exe: str | None) -> str | None:
    if not exe:
        return None
    parts = _path_type(exe)(exe).parts
    lowered = [part.lower() for part in parts]
    size = len(PYAPPIFY_APP_PARTS)
    for index in range(len(lowered) - size + 1):
        if tuple(lowered[index : index + size]) == PYAPPIFY_APP_PARTS:
            return str(_path_type(exe)(*parts[: index + size], "working"))
    return None


class LegacyDetector:
    def __init__(
        self,
        process_iter=None,
        now=time.time,
        self_pid=None,
        excluded_pids=(),
        grace_seconds=LEGACY_GRACE_SECONDS,
        marker_check=has_app_markers,
    ):
        self._process_iter = process_iter
        self._now = now
        self.self_pid = os.getpid() if self_pid is None else self_pid
        self.excluded_pids = set(excluded_pids)
        self.grace_seconds = grace_seconds
        self._marker_check = marker_check

    def _iterate(self):
        if self._process_iter is not None:
            return self._process_iter()
        import psutil

        return psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time"])

    def _excluded(self) -> set[int]:
        excluded = {self.self_pid, *self.excluded_pids}
        if self._process_iter is None:
            try:
                import psutil

                excluded.update(parent.pid for parent in psutil.Process(self.self_pid).parents())
            except Exception:
                pass
        return excluded

    def scan(self) -> list[LegacyInstance]:
        found = []
        excluded = self._excluded()
        for process in self._iterate():
            try:
                instance = self._identify(process, excluded)
            except Exception as error:  # access denied, process exited, ...
                logger.debug(f"legacy scan skipped a process: {error}")
                continue
            if instance is not None:
                found.append(instance)
        return found

    def _identify(self, process, excluded) -> LegacyInstance | None:
        info = getattr(process, "info", None) or {}
        pid = info.get("pid", getattr(process, "pid", None))
        if pid is None or pid in excluded or not _is_python(info.get("name")):
            return None
        create_time = info.get("create_time")
        if create_time is None or self._now() - create_time < self.grace_seconds:
            return None
        script = _entry_script(info.get("cmdline"))
        if script is None or _path_type(script)(script).name.lower() not in ENTRY_SCRIPTS:
            return None
        location = self._app_directory(process, script, info.get("exe"))
        if location is None:
            return None
        try:
            marker = process.environ().get(GENERATION_ENV)
        except Exception:
            marker = None
        if marker == GENERATION:
            return None  # a lock-aware instance (for example one being refused right now)
        return LegacyInstance(
            pid=pid,
            create_time=create_time,
            location=location,
            command=" ".join(info.get("cmdline") or []),
        )

    def _app_directory(self, process, script: str, exe: str | None) -> str | None:
        script_path = _path_type(script)(script)
        candidates = []
        if script_path.is_absolute():
            candidates.append(str(script_path.parent))
        else:
            try:
                cwd = process.cwd()
            except Exception:
                cwd = None
            if cwd:
                candidates.append(str(_path_type(cwd)(cwd, *script_path.parent.parts)))
        working = pyappify_working_dir(exe)
        if working:
            candidates.append(working)
        for candidate in candidates:
            if self._marker_check(candidate):
                return candidate
        return None


class LegacyGuard:
    """Caches legacy scans for the executor loop; start requests always rescan."""

    def __init__(
        self,
        detector: LegacyDetector | None = None,
        cache_seconds=LEGACY_CACHE_SECONDS,
        clock=time.monotonic,
    ):
        self.detector = detector or LegacyDetector()
        self.cache_seconds = cache_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at = None
        self._instances: list[LegacyInstance] = []

    def instances(self, force: bool = False) -> list[LegacyInstance]:
        with self._lock:
            now = self._clock()
            stale = self._checked_at is None or now - self._checked_at >= self.cache_seconds
            if force or stale:
                self._instances = self.detector.scan()
                self._checked_at = now
            return list(self._instances)

    def blocking_message(self, force: bool = False) -> str | None:
        instances = self.instances(force=force)
        if not instances:
            return None
        listed = "；".join(instance.describe() for instance in instances)
        return f"检测到正在运行的旧版 ok-bd2（{listed}）。请先退出旧版，再开始任务。"


_executor_lock: ExecutorLock | None = None
_legacy_guard: LegacyGuard | None = None
_install_lock = threading.Lock()


def acquire_executor_lock(mode: str, lock: ExecutorLock | None = None):
    """Take the session lock; returns ``(True, None)`` or ``(False, holder_or_None)``."""
    global _executor_lock
    mark_current_process()
    lock = lock or ExecutorLock()
    if lock.acquire(mode):
        _executor_lock = lock
        logger.info(f"executor lock acquired ({mode}, pid={os.getpid()})")
        return True, None
    holder = lock.holder()
    logger.error(f"executor lock is held by {holder.describe() if holder else 'another instance'}")
    return False, holder


def release_executor_lock() -> None:
    global _executor_lock
    if _executor_lock is not None:
        _executor_lock.release()
        _executor_lock = None


def refusal_message(holder: InstanceInfo | None) -> str:
    who = holder.describe() if holder else "另一个 ok-bd2 实例"
    return f"{who}正在控制游戏。同一时间只能运行一个 ok-bd2，请先退出它。"


def legacy_guard() -> LegacyGuard:
    global _legacy_guard
    with _install_lock:
        if _legacy_guard is None:
            _legacy_guard = LegacyGuard()
        return _legacy_guard


def _is_guard(function) -> bool:
    return getattr(function, "_bd2_executor_guard", False) is True


def install_executor_guards(guard: LegacyGuard | None = None) -> bool:
    """Refuse start requests and executor picks while a legacy instance runs."""
    global _legacy_guard
    from ok.core.events import communicate
    from ok.core.start_controller import StartController
    from ok.task.TaskExecutor import TaskExecutor

    with _install_lock:
        if guard is not None:
            _legacy_guard = guard
        elif _legacy_guard is None:
            _legacy_guard = LegacyGuard()
        if _is_guard(StartController.do_start):
            return False
        original_do_start = StartController.do_start
        original_next_task = TaskExecutor.next_task

    def guarded_do_start(controller, task=None, exit_after=False):
        message = legacy_guard().blocking_message(force=True)
        if message is not None:
            logger.warning(message)
            communicate.starting_emulator.emit(True, message, 0)
            return False
        return original_do_start(controller, task, exit_after)

    def guarded_next_task(executor):
        picked = original_next_task(executor)
        if picked[0] is None:
            return picked
        message = legacy_guard().blocking_message()
        if message is None:
            return picked
        logger.warning(f"{message}（已暂停执行器）")
        if not executor.paused:
            executor.pause()
        communicate.notification.emit(message, "ok-bd2", True, True, None, None, None)
        return None, False, False

    for wrapper, original in (
        (guarded_do_start, original_do_start),
        (guarded_next_task, original_next_task),
    ):
        wrapper._bd2_executor_guard = True
        wrapper.__wrapped__ = original
    StartController.do_start = guarded_do_start
    TaskExecutor.next_task = guarded_next_task
    return True


def uninstall_executor_guards() -> None:
    """Restore ok-script's methods (tests)."""
    from ok.core.start_controller import StartController
    from ok.task.TaskExecutor import TaskExecutor

    if _is_guard(StartController.do_start):
        StartController.do_start = StartController.do_start.__wrapped__
    if _is_guard(TaskExecutor.next_task):
        TaskExecutor.next_task = TaskExecutor.next_task.__wrapped__


def show_refusal_dialog(message: str) -> None:
    """Windows message box for windowed entries; the service reports through the shell."""
    if os.name != "nt":
        print(message, file=sys.stderr)
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(0, message, "ok-bd2", 0x30 | 0x1000)
