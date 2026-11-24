import ctypes
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok.core.events import EventBus
from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
from ok.task.exceptions import FinishedException, TaskDisabledException
from ok.task.TaskExecutor import TaskExecutor

from src import executor_guard as guard
from src.compat import executor_cleanup
from src.compat.windows_graphics import (
    patch_windows_graphics_capture_class,
)
from src.tasks.run_history import RunHistoryStore

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
    def test_only_fresh_mutex_is_held_until_release(self):
        for error, acquired, closed in (
            (0, True, []),
            (guard._WindowsMutexBackend.ERROR_ALREADY_EXISTS, False, [7]),
        ):
            with self.subTest(error=error):
                api = FakeMutexApi(error=error)
                backend = guard._WindowsMutexBackend(api=api)
                self.assertEqual(acquired, backend.try_acquire())
                self.assertEqual(api.name, r"Local\ok-bd2-executor")
                self.assertEqual(closed, api.closed)
                backend.release()
                self.assertEqual([7], api.closed)


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
            owner = waiting.holder()
            self.assertEqual((child.pid, "qt"), (owner.pid, owner.mode))
            for created in (None, owner.create_time + 100):
                with self.subTest(created=created):
                    self.assertIsNone(self.make_lock(create_time=lambda _: created).holder())
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

    def test_only_matching_older_instances_block_the_executor(self):
        for process, expected in (
            (self.legacy(), [40]),
            (self.legacy(environ_error=AccessDenied()), [40]),
            (self.legacy(cmdline=["python.exe", r"D:\ok-bd2-dev\main_debug.py"], cwd="C:/"), [40]),
            (
                self.legacy(
                    exe=r"D:\Games\ok-bd2\data\apps\ok-bd2\python\pythonw.exe",
                    cwd_error=AccessDenied(),
                ),
                [40],
            ),
            (self.legacy(cwd=r"D:\ok-ww"), []),
            (self.legacy(1), []),
            (self.legacy(60), []),
            (self.legacy(environ={guard.GENERATION_ENV: guard.GENERATION}), []),
            (self.legacy(create_time=999.0), []),
            (self.legacy(name="notepad.exe"), []),
            (self.legacy(cmdline=["python.exe", "main_desktop.py"]), []),
            (self.legacy(cmdline=["python.exe", "-c", "from multiprocessing import spawn"]), []),
            (self.legacy(cwd_error=AccessDenied()), []),
        ):
            with self.subTest(process=process.info):
                self.assertEqual(
                    expected, [p.pid for p in detector_for([process], excluded={60}).scan()]
                )


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

    def test_refused_start_leaves_no_task_waiting_for_the_next_launch(self):
        controller = object.__new__(self.StartController)
        task = object()
        with (
            patch("ok.core.events.communicate.task.emit"),
            patch("ok.core.events.communicate.starting_emulator.emit"),
        ):
            controller._add_pending(task)
            self.messages.append("检测到正在运行的旧版 ok-bd2")
            self.assertFalse(controller.do_start(task, False, from_queue=True))
        self.assertFalse(controller.is_task_pending(task))
        self.original_do_start.assert_not_called()


class ExecutorCleanupTest(unittest.TestCase):
    def make_executor(self, *, trigger=False):
        executor_cleanup.enable_executor_cleanup()
        executor = object.__new__(TaskExecutor)
        task = SimpleNamespace(
            name="cleanup test",
            running=False,
            config={},
            exit_after_task=False,
            disable=Mock(),
            info_set=Mock(),
            _app=SimpleNamespace(tr=lambda value: value),
        )
        executor.exit_event = threading.Event()
        executor.paused = False
        executor._exclusive_owner = None
        executor.current_task = None
        executor._last_frame_time = time.time()
        executor._frame = np.ones((2, 2, 3), dtype=np.uint8)
        executor._get_wake_version = Mock(return_value=0)
        executor._pop_job = Mock(return_value=None)
        executor.next_task = Mock(return_value=(task, False, trigger))
        executor._prepare_task_for_run = Mock()
        executor.reset_scene = Mock()
        executor.destroy = Mock()
        executor.trigger_tasks = [task] if trigger else []
        return executor, task

    def test_onetime_completion_and_exceptions_release_sleep_and_task_state(self):
        for exception, cleanup_error, success in (
            (None, None, True),
            ("waiting for login", None, False),
            (TaskDisabledException, None, False),
            (FinishedException, None, False),
            (RuntimeError, None, False),
            (None, TaskDisabledException, True),
            (None, FinishedException, False),
        ):
            with self.subTest(exception=exception, cleanup_error=cleanup_error):
                executor, task = self.make_executor()
                task.info = {"状态": "任务完成。" if success else "任务失败。"}
                task.info_set.side_effect = task.info.__setitem__
                task._start_after_login = exception == "waiting for login"
                if cleanup_error:
                    task.finish_gameplay_at_home = Mock(side_effect=cleanup_error)

                def run():
                    executor.exit_event.set()
                    if exception is not None and not task._start_after_login:
                        raise exception("stop test")
                    return success

                task.run = Mock(side_effect=run)
                events = EventBus()
                with (
                    tempfile.TemporaryDirectory() as directory,
                    patch.object(executor_cleanup, "prevent_sleeping") as prevent_sleep,
                    patch.object(executor_cleanup, "communicate", events),
                    patch("ok.core.events.communicate", events),
                    patch("ok.core.events._dispatcher", None),
                    patch("src.tasks.scheduler.default_store") as schedules,
                    patch.object(
                        events.task_done,
                        "emit",
                        wraps=events.task_done.emit,
                    ) as completed,
                ):
                    store = RunHistoryStore(os.path.join(directory, "history.json"))
                    with patch("src.tasks.run_history.default_store", return_value=store):
                        if cleanup_error:

                            def interrupt_cleanup():
                                self.assertEqual(success, store.last_run(task.name)["ok"])
                                raise cleanup_error

                            task.finish_gameplay_at_home.side_effect = interrupt_cleanup
                        executor.execute()
                    recent = store.recent_runs()
                    if task._start_after_login:
                        self.assertEqual([], recent)
                    else:
                        self.assertEqual(1, len(recent))
                        self.assertEqual(success, recent[0]["ok"])
                        self.assertGreaterEqual(recent[0]["duration"], 0)
                        if exception in (TaskDisabledException, FinishedException):
                            self.assertEqual("已中止", recent[0]["status"])
                        elif exception is RuntimeError:
                            self.assertEqual("stop test", recent[0]["details"]["Error"])
                    self.assertEqual(
                        1 if exception is None or task._start_after_login else 0,
                        completed.call_count,
                    )
                    self.assertEqual(
                        int(exception is None), schedules.return_value.delay_after_record.call_count
                    )
                self.assertEqual([call(True), call(False)], prevent_sleep.call_args_list)
                self.assertFalse(task.running)
                self.assertIsNone(executor.current_task)
                executor.destroy.assert_called_once_with()

    def test_task_disabled_exception_finishes_onetime_tasks_but_not_trigger_tasks(self):
        # As in ok-script 3.0.10: otherwise the executor picks the one-time task again.
        for trigger in (False, True):
            with self.subTest(trigger=trigger):
                executor, task = self.make_executor(trigger=trigger)
                task._start_after_login = False

                def run():
                    executor.exit_event.set()
                    raise TaskDisabledException("stopped by the task itself")

                task.run = Mock(side_effect=run)
                with (
                    patch.object(executor_cleanup, "prevent_sleeping"),
                    patch.object(executor_cleanup, "communicate"),
                    patch.object(executor_cleanup, "_record_interrupted") as interrupted,
                ):
                    executor.execute()
                self.assertEqual(0 if trigger else 1, task.disable.call_count)
                interrupted.assert_called_once_with(task, trigger, "已中止")
                self.assertFalse(task.running)


def pending_read_capture():
    class RequestLock:
        def __init__(self, capture):
            self.capture = capture
            self.lock = threading.RLock()

        def acquire(self, **kwargs):
            if self.capture.request_started.is_set():
                self.capture.close_waiting.set()
            return self.lock.acquire(**kwargs)

        def release(self):
            self.lock.release()

        def __enter__(self):
            self.acquire()

        def __exit__(self, *_args):
            self.release()

    class FakeCapture:
        def __init__(self):
            self.lock = threading.RLock()
            self.get_frame_lock = RequestLock(self)
            self.exit_event = threading.Event()
            self._frame_cancel_generation = 0
            self.hwnd_window = SimpleNamespace(exists=False)
            self.frame_event = threading.Event()
            self.frame_requested = threading.Event()
            self.request_started = threading.Event()
            self.close_waiting = threading.Event()
            self.woken_before_close_wait = False
            self.failures = []
            self.lifecycle = []
            self.last_size = (1280, 720)
            frame = SimpleNamespace(
                ContentSize=(1024, 576),
                Close=lambda: self.lifecycle.append("frame closed"),
            )
            self.frame_pool = SimpleNamespace(TryGetNextFrame=lambda: frame)

        def start_or_stop(self, capture_cursor=False):
            with self.lock:
                self.close()
            return False

        def close(self):
            # ok-script 2.0.6 wakes a pending reader before taking its lock.
            with self.lock:
                self._frame_cancel_generation += 1
                self.frame_requested.clear()
                self.frame_event.set()
            if not self.get_frame_lock.acquire(timeout=0.5):
                self.failures.append("close waited on a reader holding get_frame_lock")
                return
            try:
                with self.lock:
                    self.frame_pool = None
                    self.lifecycle.append("capture closed")
            finally:
                self.get_frame_lock.release()

        def do_get_frame(self):
            with self.get_frame_lock:
                self.request_started.set()
                if not self.close_waiting.wait(1.0):
                    self.failures.append("close did not reach the pending frame lock")
                self.woken_before_close_wait = self.frame_event.is_set()
                if self.lock.acquire(timeout=1.0):
                    self.lock.release()
                else:
                    self.failures.append("reader waited on capture resource lock")

    return FakeCapture


def timeout_capture():
    class FakeCapture:
        def __init__(self, frames, state):
            self.lock = threading.RLock()
            self.get_frame_lock = threading.RLock()
            self.exit_event = threading.Event()
            self._frame_cancel_generation = 0
            self.hwnd_window = SimpleNamespace(
                exists=state != "disconnected", hwnd=10, width=1280, height=720
            )
            self.frames = iter(frames)
            self.state = state
            self.read_calls = self.close_calls = 0
            self.deadlines = []
            if state == "exit":
                self.exit_event.set()

        do_get_frame = WindowsGraphicsCaptureMethod.do_get_frame
        do_get_frame_wait = WindowsGraphicsCaptureMethod.do_get_frame_wait
        get_frame_for_probe = WindowsGraphicsCaptureMethod.get_frame_for_probe

        def _do_get_frame(self, frame_deadline=None):
            self.read_calls += 1
            self.last_deadline = frame_deadline
            self.deadlines.append(frame_deadline)
            if self.state == "cancelled":
                self._frame_cancel_generation += 1
            if self.state == "error_once" and self.read_calls == 1:
                raise RuntimeError("capture failed")
            if self.state.startswith("watchdog") and self.read_calls == 1:
                self.close()
                return self._do_get_frame(frame_deadline=frame_deadline)
            return next(self.frames)

        def start_or_stop(self, capture_cursor=False):
            return True

        def close(self):
            self.close_calls += 1
            self._frame_cancel_generation += 1

        def frame_arrived_callback(self, *_args):
            pass

    return FakeCapture


class WindowsGraphicsCompatibilityTest(unittest.TestCase):
    def test_close_and_target_changes_do_not_invert_pending_frame_locks(self):
        FakeCapture = pending_read_capture()

        patch_windows_graphics_capture_class(FakeCapture)
        for entry in ("close", "frame_arrived_callback"):
            with self.subTest(entry=entry):
                capture = FakeCapture()
                capture._ok_bd2_supported_target_signature = ()
                capture._ok_bd2_resize_gate.observe((1024, 576), 0.0)

                def read_frame():
                    try:
                        capture.do_get_frame()
                    except Exception as exc:
                        capture.failures.append(str(exc))

                reader = threading.Thread(target=read_frame)
                closer = threading.Thread(target=getattr(capture, entry))
                reader.start()
                self.assertTrue(capture.request_started.wait(1.0))
                closer.start()
                for worker in (reader, closer):
                    worker.join(2.0)
                    self.assertFalse(worker.is_alive())
                self.assertEqual([], capture.failures)
                self.assertIsNone(capture.frame_pool)
                self.assertFalse(capture._ok_bd2_resize_gate.active)
                self.assertTrue(capture.woken_before_close_wait)
                if entry == "frame_arrived_callback":
                    self.assertEqual(["frame closed", "capture closed"], capture.lifecycle)

    def test_frame_pool_close_can_finish_an_inflight_callback_under_resource_lock(self):
        class FakeCapture:
            def __init__(self):
                self.lock = threading.RLock()
                self.exit_event = threading.Event()
                self.callback_finished_during_close = False
                self.callback = None
                self.frame_pool = SimpleNamespace(Close=self.close_pool)

            def start_or_stop(self, capture_cursor=False):
                return True

            def frame_arrived_callback(self, *_args):
                pass

            def close_pool(self):
                self.callback = threading.Thread(target=self.frame_arrived_callback)
                self.callback.start()
                self.callback.join(0.5)
                self.callback_finished_during_close = not self.callback.is_alive()

            def close(self):
                with self.lock:
                    self.exit_event.set()
                    self.frame_pool.Close()
                    self.frame_pool = None

        patch_windows_graphics_capture_class(FakeCapture)
        capture = FakeCapture()
        capture.close()
        capture.callback.join(1.0)
        self.assertFalse(capture.callback.is_alive())
        self.assertTrue(capture.callback_finished_during_close)

    def test_frame_timeout_restarts_once_and_respects_window_and_cancellation(self):
        frame = np.ones((720, 1280, 3), dtype=np.uint8)

        FakeCapture = timeout_capture()

        patch_windows_graphics_capture_class(FakeCapture)
        cases = (
            ("healthy", (frame,), frame, 1, 0),
            ("recovered", (None, frame), frame, 2, 1),
            ("unavailable", (None, None), None, 2, 1),
            ("exit", (None,), None, 1, 0),
            ("disconnected", (None,), None, 1, 0),
            ("cancelled", (None,), None, 1, 0),
            ("watchdog_recovered", (frame,), frame, 2, 1),
            ("watchdog_unavailable", (None, None), None, 2, 1),
        )
        for entry in ("poll", "wait", "probe"):
            for state, frames, expected, reads, closes in cases:
                with self.subTest(entry=entry, state=state):
                    capture = FakeCapture(frames, state)
                    if entry == "poll":
                        actual = capture.do_get_frame()
                    elif entry == "wait":
                        actual = capture.do_get_frame_wait(0.1)
                    else:
                        actual = capture.get_frame_for_probe(1.5)
                        self.assertIsNotNone(capture.last_deadline)
                    self.assertIs(actual, expected)
                    self.assertEqual(reads, capture.read_calls)
                    self.assertEqual(closes, capture.close_calls)
                    self.assertEqual(1, len(set(capture.deadlines)))

        capture = FakeCapture((None, frame), "error_once")
        with self.assertRaisesRegex(RuntimeError, "capture failed"):
            capture.do_get_frame()
        self.assertIs(capture.do_get_frame(), frame)
        self.assertEqual((3, 1), (capture.read_calls, capture.close_calls))

        capture = FakeCapture((None, frame), "recovered")
        with patch("src.compat.windows_graphics.time.monotonic", side_effect=(0, 2)):
            self.assertIsNone(capture._do_get_frame(frame_deadline=1))
        self.assertEqual((1, 0), (capture.read_calls, capture.close_calls))
        self.assertEqual(1, capture.last_deadline)

        capture = FakeCapture((frame,), "healthy")
        with patch("src.compat.windows_graphics.time.monotonic", return_value=2):
            self.assertIsNone(capture._do_get_frame(frame_deadline=1))
        self.assertEqual((0, 0), (capture.read_calls, capture.close_calls))

    def test_mapped_frame_is_owned_contiguous_bgr_and_excludes_row_padding(self):
        class FakeCapture:
            def __init__(self):
                self.last_size = SimpleNamespace(Width=3, Height=2)

            def start_or_stop(self, capture_cursor=False):
                return True

            def close(self):
                pass

        patch_windows_graphics_capture_class(FakeCapture)
        mapped = np.arange(32, dtype=np.uint8).reshape(2, 4, 4)
        expected = mapped[:, :3, :3].copy()
        mapinfo = SimpleNamespace(pData=ctypes.c_void_p(mapped.ctypes.data), RowPitch=16)
        frame = FakeCapture().read_mapped_frame(mapinfo)
        mapped.fill(0)
        np.testing.assert_array_equal(expected, frame)
        self.assertTrue(frame.flags.c_contiguous)
