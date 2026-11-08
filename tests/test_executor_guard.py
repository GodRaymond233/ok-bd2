import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import executor_guard as guard

ROOT = Path(__file__).resolve().parents[1]


class FakeMutexApi:
    def __init__(self, error=0, handle=7):
        self.error = error
        self.handle = handle
        self.closed = []

    def create(self, name):
        self.name = name
        return self.handle, self.error

    def close(self, handle):
        self.closed.append(handle)


class WindowsMutexBackendTest(unittest.TestCase):
    def test_new_mutex_is_kept_and_released(self):
        api = FakeMutexApi()
        backend = guard._WindowsMutexBackend(api=api)
        self.assertTrue(backend.try_acquire())
        self.assertEqual(api.name, "Local\\ok-bd2-executor")
        self.assertEqual(api.closed, [])
        backend.release()
        self.assertEqual(api.closed, [7])

    def test_existing_mutex_is_refused_and_handle_closed(self):
        api = FakeMutexApi(error=guard._WindowsMutexBackend.ERROR_ALREADY_EXISTS)
        backend = guard._WindowsMutexBackend(api=api)
        self.assertFalse(backend.try_acquire())
        self.assertEqual(api.closed, [7])


@unittest.skipIf(os.name == "nt", "file lock stand-in is used off Windows only")
class ExecutorLockTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make_lock(self, create_time=None):
        return guard.ExecutorLock(
            backend=guard._FileLockBackend(self.root / "executor.lock"),
            owner_file=self.root / "owner.json",
            create_time=create_time,
        )

    def test_second_holder_is_refused_with_live_owner_info(self):
        first = self.make_lock(create_time=lambda pid: 100.0)
        second = self.make_lock(create_time=lambda pid: 100.0)
        self.assertTrue(first.acquire("qt"))
        self.assertFalse(second.acquire("service"))
        holder = second.holder()
        self.assertEqual(holder.pid, os.getpid())
        self.assertEqual(holder.mode, "qt")
        first.release()
        self.assertFalse((self.root / "owner.json").exists())
        self.assertTrue(second.acquire("service"))
        second.release()

    def test_owner_record_of_reused_or_dead_pid_is_ignored(self):
        record = {
            "pid": os.getpid(),
            "create_time": 100.0,
            "mode": "qt",
            "cwd": "D:/old",
            "started_at": "2026-10-20T10:00:00",
        }
        (self.root / "owner.json").write_text(json.dumps(record), encoding="utf-8")
        reused = self.make_lock(create_time=lambda pid: 900.0)
        self.assertIsNone(reused.holder())
        dead = self.make_lock(create_time=lambda pid: None)
        self.assertIsNone(dead.holder())
        same = self.make_lock(create_time=lambda pid: 100.4)
        self.assertEqual(same.holder().cwd, "D:/old")

    def test_lock_is_released_when_holder_process_is_killed(self):
        script = (
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from pathlib import Path\n"
            "from src import executor_guard as g\n"
            "root = Path(sys.argv[2])\n"
            "lock = g.ExecutorLock(backend=g._FileLockBackend(root / 'executor.lock'),"
            " owner_file=root / 'owner.json')\n"
            "print('acquired' if lock.acquire('qt') else 'refused', flush=True)\n"
            "time.sleep(60)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(ROOT), str(self.root)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            # ok-script's console logger also writes to stdout; wait for the marker line.
            marker = ""
            while marker not in {"acquired", "refused"}:
                line = child.stdout.readline()
                self.assertTrue(line, "child exited before reporting the lock result")
                marker = line.strip()
            self.assertEqual(marker, "acquired")
            waiting = self.make_lock()
            self.assertFalse(waiting.acquire("service"))
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=10)
            self.assertTrue(waiting.acquire("service"))
            waiting.release()
        finally:
            if child.poll() is None:
                child.kill()
            child.stdout.close()


class FakeProcess:
    def __init__(
        self,
        pid,
        name="python.exe",
        cmdline=None,
        create_time=0.0,
        exe=None,
        cwd=None,
        environ=None,
        cwd_error=None,
        environ_error=None,
    ):
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "exe": exe,
            "cmdline": cmdline,
            "create_time": create_time,
        }
        self._cwd = cwd
        self._environ = environ or {}
        self._cwd_error = cwd_error
        self._environ_error = environ_error

    def cwd(self):
        if self._cwd_error:
            raise self._cwd_error
        return self._cwd

    def environ(self):
        if self._environ_error:
            raise self._environ_error
        return self._environ


class AccessDenied(Exception):
    pass


BD2_DIRS = {"D:\\ok-bd2", "D:\\Games\\ok-bd2\\data\\apps\\ok-bd2\\working", "D:\\ok-bd2-dev"}


def detector_for(processes, excluded=(), now=1000.0):
    return guard.LegacyDetector(
        process_iter=lambda: processes,
        now=lambda: now,
        self_pid=1,
        excluded_pids=excluded,
        marker_check=lambda directory: directory in BD2_DIRS,
    )


@patch.object(
    guard, "_is_python", lambda name: (name or "").lower() in {"python.exe", "pythonw.exe"}
)
class LegacyDetectorTest(unittest.TestCase):
    def legacy(self, pid=40, **overrides):
        values = dict(
            cmdline=["C:\\Python\\pythonw.exe", "main.py"], cwd="D:\\ok-bd2", create_time=900.0
        )
        values.update(overrides)
        return FakeProcess(pid, **values)

    def test_detects_older_windowed_instance_by_app_identity(self):
        found = detector_for([self.legacy()]).scan()
        self.assertEqual([item.pid for item in found], [40])
        self.assertEqual(found[0].location, "D:\\ok-bd2")
        self.assertIn("进程 40", found[0].describe())

    def test_detects_absolute_debug_entry_and_pyappify_layout(self):
        absolute = self.legacy(
            41, cmdline=["python.exe", "D:\\ok-bd2-dev\\main_debug.py"], cwd="C:\\"
        )
        installed = self.legacy(
            42,
            exe="D:\\Games\\ok-bd2\\data\\apps\\ok-bd2\\python\\pythonw.exe",
            cwd_error=AccessDenied("cwd"),
        )
        found = detector_for([absolute, installed]).scan()
        self.assertEqual(sorted(item.pid for item in found), [41, 42])

    def test_negative_cases_are_not_reported(self):
        processes = [
            # Another ok-script application (different game) with the same entry name.
            self.legacy(50, cwd="D:\\ok-ww"),
            # This process, and an ancestor such as the shell's bootstrap.
            self.legacy(1),
            self.legacy(60),
            # A refused new-generation instance still showing its message box.
            self.legacy(70, environ={guard.GENERATION_ENV: guard.GENERATION}),
            # Started moments ago (inside the grace period).
            self.legacy(80, create_time=999.0),
            # A reused PID now owned by an unrelated program.
            FakeProcess(90, name="notepad.exe", cmdline=["notepad.exe", "main.py"]),
            # The desktop bootstrap and helper interpreters.
            self.legacy(91, cmdline=["python.exe", "main_desktop.py"]),
            self.legacy(92, cmdline=["python.exe", "-c", "from multiprocessing import spawn"]),
            # Cannot read the working directory and no PyAppify layout to fall back on.
            self.legacy(93, cwd_error=AccessDenied("cwd")),
        ]
        self.assertEqual(detector_for(processes, excluded={60}).scan(), [])

    def test_unreadable_environment_is_treated_as_an_older_instance(self):
        found = detector_for([self.legacy(environ_error=AccessDenied("environ"))]).scan()
        self.assertEqual([item.pid for item in found], [40])


class InstalledGuardsTest(unittest.TestCase):
    """The checks sit on the core's own start and pick paths, not on a client button."""

    def setUp(self):
        from ok.core.start_controller import StartController
        from ok.task.TaskExecutor import TaskExecutor

        self.messages = []
        self.legacy = SimpleNamespace(
            blocking_message=lambda force=False: self.messages[-1] if self.messages else None
        )
        self.original_do_start = Mock(return_value=True)
        self.picked = (SimpleNamespace(name="自动登录游戏"), True, True)
        self.original_next_task = Mock(return_value=self.picked)
        self.enterContext(patch.object(StartController, "do_start", self.original_do_start))
        self.enterContext(patch.object(TaskExecutor, "next_task", self.original_next_task))
        self.enterContext(patch.object(guard, "_legacy_guard", None))
        self.assertTrue(guard.install_executor_guards(self.legacy))
        self.addCleanup(guard.uninstall_executor_guards)
        self.StartController = StartController
        self.TaskExecutor = TaskExecutor

    def test_start_requests_are_refused_while_legacy_runs(self):
        controller = object.__new__(self.StartController)
        emitted = []
        with patch(
            "ok.core.events.communicate.starting_emulator.emit",
            side_effect=lambda *args: emitted.append(args),
        ):
            self.assertTrue(controller.do_start("task"))
            self.messages.append("检测到正在运行的旧版 ok-bd2")
            self.assertFalse(controller.do_start("task"))
        self.original_do_start.assert_called_once_with(controller, "task", False)
        self.assertEqual(emitted, [(True, "检测到正在运行的旧版 ok-bd2", 0)])

    def test_executor_picks_pause_while_legacy_runs(self):
        executor = SimpleNamespace(paused=False)
        executor.pause = Mock(side_effect=lambda: setattr(executor, "paused", True))
        self.assertEqual(self.TaskExecutor.next_task(executor), self.picked)
        self.messages.append("检测到正在运行的旧版 ok-bd2")
        with patch("ok.core.events.communicate.notification.emit") as notify:
            self.assertEqual(self.TaskExecutor.next_task(executor), (None, False, False))
        executor.pause.assert_called_once_with()
        notify.assert_called_once()


class AcquireExecutorLockTest(unittest.TestCase):
    def test_marks_process_and_reports_holder(self):
        lock = Mock()
        lock.acquire.return_value = False
        lock.holder.return_value = guard.InstanceInfo(12, 1.0, "qt", "D:\\ok-bd2", "now")
        with patch.dict(os.environ, {}, clear=False):
            acquired, holder = guard.acquire_executor_lock("service", lock=lock)
            self.assertEqual(os.environ[guard.GENERATION_ENV], guard.GENERATION)
        self.assertFalse(acquired)
        self.assertIn("进程 12", guard.refusal_message(holder))
        self.assertIn("另一个 ok-bd2", guard.refusal_message(None))


if __name__ == "__main__":
    unittest.main()
