"""Lifecycle of the desktop core service (handshake, ready, shutdown, parent loss)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import executor_guard as guard
from src.desktop import protocol, service

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 43


class HandshakeTest(unittest.TestCase):
    def line(self, **fields):
        return json.dumps({"v": 1, "token": TOKEN, **fields}) + "\n"

    def test_valid_handshake(self):
        parsed = service.parse_handshake(self.line(parent_pid=1234))
        self.assertEqual(parsed.token, TOKEN)
        self.assertEqual(parsed.parent_pid, 1234)
        self.assertIsNone(service.parse_handshake(self.line()).parent_pid)

    def test_rejected_handshakes(self):
        for line in (
            None,
            "",
            "not json\n",
            json.dumps({"v": 2, "token": TOKEN}),
            json.dumps({"token": TOKEN}),
            self.line(token="short"),
            self.line(token="x" * 40 + "/.;"),
            self.line(token="x" * 300),
            self.line(parent_pid=True),
            self.line(parent_pid=-1),
            self.line(parent_pid="12"),
            json.dumps([1, 2]),
        ):
            with self.subTest(line=line), self.assertRaises(service.HandshakeError):
                service.parse_handshake(line)

    def test_missing_handshake_times_out(self):
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "r", encoding="utf-8")
        try:
            started = time.monotonic()
            with self.assertRaisesRegex(service.HandshakeError, "没有收到"):
                service.read_handshake(stream, timeout=0.2)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            # EOF first: closing a file another thread is reading would block.
            os.close(write_fd)
            time.sleep(0.05)
            stream.close()


class ProtocolPiecesTest(unittest.TestCase):
    def test_channel_writes_prefixed_ascii_json_and_survives_closed_stream(self):
        stream = io.StringIO()
        channel = service.ProtocolChannel(stream)
        channel.send("ready", port=5, core_version="版本")
        line = stream.getvalue()
        self.assertTrue(line.startswith(protocol.LINE_PREFIX))
        self.assertTrue(line.isascii())
        payload = json.loads(line[len(protocol.LINE_PREFIX) :])
        self.assertEqual(payload, {"type": "ready", "port": 5, "core_version": "版本"})
        stream.close()
        channel.fatal("startup", "after close")

    def test_shutdown_starts_once_with_watchdog(self):
        stream = io.StringIO()
        quit_runtime = Mock()
        watchdog = Mock()
        controller = service.ShutdownController(
            service.ProtocolChannel(stream), quit_runtime, Mock(), watchdog=watchdog
        )
        self.assertTrue(controller.request("shell:user"))
        self.assertFalse(controller.request("stdin-closed"))
        self.assertEqual(controller.reason, "shell:user")
        quit_runtime.assert_called_once_with()
        watchdog.assert_called_once_with(grace_period=protocol.EXIT_WATCHDOG_SECONDS, exit_code=0)
        self.assertIn('"reason": "shell:user"', stream.getvalue())

    def test_console_handlers_bound_to_stdout_are_rerouted(self):
        import logging

        original, replacement = io.StringIO(), io.StringIO()
        logger = logging.getLogger("okbd2-route-test")
        handler = logging.StreamHandler(original)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.assertGreaterEqual(service.route_console_logs(original, replacement), 1)
        logger.warning("moved")
        self.assertEqual(original.getvalue(), "")
        self.assertIn("moved", replacement.getvalue())

    def test_command_reader(self):
        requests = []
        stream = io.StringIO('garbage\n{"cmd": "ping"}\n{"cmd": "shutdown", "reason": "tray"}\n')
        reader = service.CommandReader(stream, requests.append, Mock()).start()
        reader.thread.join(timeout=2)
        self.assertEqual(requests, ["shell:tray", "stdin-closed"])

    def test_parent_watch_reports_vanished_and_replaced_parent(self):
        for identities in ([100.0, None], [100.0, 250.0]):
            with self.subTest(identities=identities):
                values = iter(identities + [identities[-1]] * 10)
                lost = threading.Event()
                watch = service.ParentWatch(
                    42, lambda reason: lost.set(), interval=0.02, identity=lambda pid: next(values)
                ).start()
                self.assertTrue(lost.wait(2))
                watch.stop()


class FakePipe:
    """A pipe as PeekNamedPipe and ReadFile see it: chunks arrive over time, then it ends.

    ``None`` is a poll that finds nothing waiting.
    """

    def __init__(self, *arrivals):
        self.arrivals = list(arrivals)
        self.pending = b""
        self.overreads = []

    def waiting(self):
        if not self.pending:
            if not self.arrivals:
                raise BrokenPipeError(109, "The pipe has been ended")
            self.pending = self.arrivals.pop(0) or b""
        return len(self.pending)

    def read(self, size):
        if size > len(self.pending):
            self.overreads.append(size)  # a real pipe would block here
        data, self.pending = self.pending[:size], self.pending[size:]
        return data

    def reader(self):
        return service.PolledPipeReader(self.waiting, self.read, interval=0)


class ControlInputTest(unittest.TestCase):
    def test_polled_reader_splits_lines_and_ends_with_the_pipe(self):
        shutdown = json.dumps({"cmd": "shutdown", "reason": "退出"}, ensure_ascii=False).encode()
        pipe = FakePipe(
            b'{"v": 1',
            None,
            b"}\n" + shutdown[:-4],  # cuts the last character in half
            shutdown[-4:] + b'\n{"cmd"',
            None,
            b': "x"}',
        )
        reader = pipe.reader()
        self.assertEqual(reader.readline(), '{"v": 1}\n')
        self.assertEqual(json.loads(reader.readline()), {"cmd": "shutdown", "reason": "退出"})
        self.assertEqual(reader.readline(), '{"cmd": "x"}')
        self.assertEqual(reader.readline(), "")
        self.assertEqual(reader.readline(), "")
        self.assertEqual(pipe.overreads, [])

    def test_handshake_and_commands_share_the_polled_reader(self):
        handshake = json.dumps({"v": 1, "token": TOKEN}) + "\n"
        command = '{"cmd": "shutdown", "reason": "tray"}\n'
        reader = FakePipe((handshake + command).encode()).reader()  # one chunk, two lines
        self.assertEqual(service.read_handshake(reader, timeout=2).token, TOKEN)
        requests = []
        service.CommandReader(reader, requests.append, Mock()).start().thread.join(timeout=2)
        self.assertEqual(requests, ["shell:tray", "stdin-closed"])

    def test_other_streams_are_read_as_before(self):
        text = io.StringIO("line\n")
        self.assertIs(service.control_input(text), text)
        self.assertIsNone(service.control_input(None))
        if os.name != "nt":
            read_fd, write_fd = os.pipe()
            os.close(write_fd)
            with os.fdopen(read_fd, "r", encoding="utf-8") as stream:
                self.assertIs(service.control_input(stream), stream)

    @unittest.skipUnless(os.name == "nt", "PeekNamedPipe exists on Windows only")
    def test_windows_pipe_is_polled(self):
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "rb")
        self.addCleanup(stream.close)
        reader = service.control_input(stream)
        self.assertIsInstance(reader, service.PolledPipeReader)
        os.write(write_fd, b'{"cmd": "shutdown"}\n{"cmd"')
        self.assertEqual(reader.readline(), '{"cmd": "shutdown"}\n')
        os.close(write_fd)
        self.assertEqual(reader.readline(), '{"cmd"')
        self.assertEqual(reader.readline(), "")


NATIVE_IMPORT_CHILD = """
import logging
import sys

sys.path.insert(0, sys.argv[1])
from src.desktop import service

assert "numpy" not in sys.modules, "numpy was loaded before the command reader started"
reader = service.CommandReader(
    service.control_input(sys.stdin),
    lambda reason: print("EXIT", reason, flush=True),
    logging.getLogger(),
).start()
import numpy  # noqa: E402,F401  native DLLs load while the reader waits on a silent pipe

print("IMPORTED", flush=True)
reader.thread.join(30)
"""


class NativeImportWhileReadingTest(unittest.TestCase):
    """F5: a blocking stdin read on Windows stopped ``import numpy`` until the shell wrote."""

    def test_numpy_loads_while_the_command_reader_waits(self):
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", NATIVE_IMPORT_CHILD, str(ROOT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, PYTHONUTF8="1"),
        )
        lines = []
        output = threading.Thread(target=lambda: lines.extend(child.stdout), daemon=True)
        output.start()
        try:
            deadline = time.monotonic() + 60
            while "IMPORTED\n" not in lines and child.poll() is None:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            self.assertIn("IMPORTED\n", lines, "".join(lines)[-2000:])
            child.stdin.close()  # the shell is gone
            self.assertEqual(child.wait(timeout=10), 0, "".join(lines)[-2000:])
        finally:
            if child.poll() is None:
                child.kill()  # only the child started here
                child.wait(timeout=10)
            output.join(timeout=5)
        self.assertIn("EXIT stdin-closed\n", lines)


class Collector:
    """A stdout stand-in that notices protocol lines as they arrive."""

    def __init__(self):
        self.lines = []
        self.changed = threading.Condition()

    def write(self, text):
        with self.changed:
            self.lines.extend(line for line in text.splitlines() if line)
            self.changed.notify_all()

    def flush(self):
        pass

    def messages(self):
        return [
            json.loads(line[len(protocol.LINE_PREFIX) :])
            for line in self.lines
            if line.startswith(protocol.LINE_PREFIX)
        ]

    def wait_for(self, message_type, timeout=15):
        deadline = time.monotonic() + timeout
        with self.changed:
            while True:
                found = [m for m in self.messages() if m["type"] == message_type]
                if found:
                    return found[-1]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"no {message_type} line in {self.lines}")
                self.changed.wait(remaining)


def fake_engine():
    from ok.task.TaskExecutor import TaskExecutor

    executor = TaskExecutor.__new__(TaskExecutor)
    executor.__dict__.update(
        lock=threading.Lock(),
        onetime_tasks=[],
        onetime_task_queue=[],
        trigger_tasks=[],
        current_task=None,
        thread=None,
        paused=False,
        basic_options={},
    )
    exit_event = threading.Event()
    return SimpleNamespace(
        config={},
        task_executor=executor,
        headless_app=SimpleNamespace(
            start_controller=SimpleNamespace(start=Mock(), starting=False)
        ),
        start_runtime=Mock(),
        quit=exit_event.set,
        exit_event=exit_event,
    )


def open_shell_pipe(testcase, **handshake_fields):
    """stdin as the shell provides it: the handshake, then a pipe that stays open."""
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "r", encoding="utf-8")
    writer = os.fdopen(write_fd, "w", encoding="utf-8")
    writer.write(json.dumps({"v": 1, "token": TOKEN, **handshake_fields}) + "\n")
    writer.flush()

    def close():
        # EOF first: closing a file another thread is reading would block.
        try:
            writer.close()
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
        stdin.close()

    testcase.addCleanup(close)
    return stdin, writer


NO_LEGACY = SimpleNamespace(
    instances=lambda force=False: [], blocking_message=lambda force=False: None
)


class RunServiceTest(unittest.TestCase):
    """The real loopback server, with the game runtime replaced by a stand-in."""

    def start_service(self, parent_pid=None):
        read_fd, write_fd = os.pipe()
        stdin = os.fdopen(read_fd, "r", encoding="utf-8")
        self.stdin_writer = os.fdopen(write_fd, "w", encoding="utf-8")
        self.addCleanup(stdin.close)
        self.stdout = Collector()
        self.watchdog = Mock()
        engine = fake_engine()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        from src.desktop.app import create_app

        def factory(config, **kwargs):
            return create_app(config, ok_instance=engine, ui_dist=Path(directory.name), **kwargs)

        handshake = {"v": 1, "token": TOKEN}
        if parent_pid is not None:
            handshake["parent_pid"] = parent_pid
        self.stdin_writer.write(json.dumps(handshake) + "\n")
        self.stdin_writer.flush()
        self.result = {}

        def run():
            self.result["code"] = service.run_service(
                lambda: {"version": "9.9.9"},
                stdin=stdin,
                stdout=self.stdout,
                dependency_check=lambda: None,
                lock_acquirer=lambda mode: (True, None),
                guard_installer=lambda: None,
                app_factory=factory,
                watchdog=self.watchdog,
                legacy=NO_LEGACY,
            )

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_if_running)
        return self.stdout.wait_for("ready")

    def stop_if_running(self):
        if self.thread.is_alive():
            try:
                self.stdin_writer.write('{"cmd": "shutdown"}\n')
                self.stdin_writer.flush()
            except (OSError, ValueError):
                pass
            self.thread.join(timeout=15)
        try:
            self.stdin_writer.close()
        except (OSError, ValueError):
            pass

    def get(self, port, path, token=TOKEN):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_ready_line_serves_authenticated_api_and_shutdown_command_exits(self):
        ready = self.start_service()
        self.assertEqual(ready["api"], protocol.API_VERSION)
        self.assertEqual(ready["core_version"], "9.9.9")
        self.assertEqual(ready["pid"], os.getpid())
        info = self.get(ready["port"], "/api/desktop/info")
        self.assertEqual(info["core_version"], "9.9.9")
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.get(ready["port"], "/api/desktop/info", token=None)
        self.assertEqual(denied.exception.code, 403)
        self.stdin_writer.write('{"cmd": "shutdown", "reason": "user"}\n')
        self.stdin_writer.flush()
        self.thread.join(timeout=15)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.result["code"], protocol.EXIT_OK)
        self.assertEqual(self.stdout.wait_for("exiting")["reason"], "shell:user")
        self.watchdog.assert_called_once_with(
            grace_period=protocol.EXIT_WATCHDOG_SECONDS, exit_code=0
        )

    def test_closed_stdin_means_the_shell_is_gone(self):
        self.start_service()
        started = time.monotonic()
        self.stdin_writer.close()
        exiting = self.stdout.wait_for("exiting")
        self.assertEqual(exiting["reason"], "stdin-closed")
        self.assertLess(time.monotonic() - started, 2.0)
        self.thread.join(timeout=15)
        self.assertEqual(self.result["code"], protocol.EXIT_OK)

    def test_parent_loss_starts_exit_within_two_seconds(self):
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        self.start_service(parent_pid=parent.pid)
        parent.kill()
        parent.wait(timeout=10)
        killed = time.monotonic()
        exiting = self.stdout.wait_for("exiting", timeout=5)
        self.assertEqual(exiting["reason"], "parent-exited")
        self.assertLessEqual(time.monotonic() - killed, 2.0)
        self.thread.join(timeout=15)
        self.assertFalse(self.thread.is_alive())

    def test_client_shutdown_request_uses_the_same_path(self):
        ready = self.start_service()
        request = urllib.request.Request(
            f"http://127.0.0.1:{ready['port']}/api/desktop/shutdown",
            method="POST",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(json.loads(response.read()), {"accepted": True})
        self.assertEqual(self.stdout.wait_for("exiting")["reason"], "client-request")
        self.thread.join(timeout=15)
        self.assertFalse(self.thread.is_alive())

    def test_startup_failure_reports_fatal_and_releases(self):
        stdout = Collector()
        stdin, _writer = open_shell_pipe(self)
        code = service.run_service(
            lambda: (_ for _ in ()).throw(RuntimeError("配置损坏")),
            stdin=stdin,
            stdout=stdout,
            dependency_check=lambda: None,
            lock_acquirer=lambda mode: (True, None),
            guard_installer=lambda: None,
            watchdog=Mock(),
            legacy=NO_LEGACY,
        )
        self.assertEqual(code, protocol.EXIT_STARTUP_FAILED)
        fatal = stdout.wait_for("fatal", timeout=1)
        self.assertEqual(fatal["code"], "startup")
        self.assertIn("配置损坏", fatal["message"])

    def test_refused_lock_reports_holder(self):
        stdout = Collector()
        holder = guard.InstanceInfo(77, 1.0, "qt", "D:\\ok-bd2", "now")
        stdin, _writer = open_shell_pipe(self)
        code = service.run_service(
            lambda: {},
            stdin=stdin,
            stdout=stdout,
            lock_acquirer=lambda mode: (False, holder),
            watchdog=Mock(),
            legacy=NO_LEGACY,
        )
        self.assertEqual(code, protocol.EXIT_EXECUTOR_LOCKED)
        fatal = stdout.wait_for("fatal", timeout=1)
        self.assertEqual((fatal["code"], fatal["holder_pid"]), ("executor-locked", 77))
        self.assertIn("进程 77", fatal["message"])


class StartupSupervisionTest(unittest.TestCase):
    """F3: the shell is supervised from the handshake on, not only after ``ready``."""

    def start_slow_service(self, **handshake_fields):
        self.stdin, self.writer = open_shell_pipe(self, **handshake_fields)
        self.stdout = Collector()
        self.watchdog = Mock()
        self.loading = threading.Event()
        self.release = threading.Event()
        self.factory = Mock()
        self.addCleanup(self.release.set)

        def slow_config():
            self.loading.set()
            self.assertTrue(self.release.wait(20))
            return {"version": "9.9.9"}

        self.result = {}

        def run():
            self.result["code"] = service.run_service(
                slow_config,
                stdin=self.stdin,
                stdout=self.stdout,
                dependency_check=lambda: None,
                lock_acquirer=lambda mode: (True, None),
                guard_installer=lambda: None,
                app_factory=self.factory,
                watchdog=self.watchdog,
                legacy=NO_LEGACY,
            )

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.assertTrue(self.loading.wait(10))

    def assert_cancelled_before_ready(self):
        self.release.set()
        self.thread.join(timeout=15)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.result["code"], protocol.EXIT_OK)
        self.assertNotIn("ready", [message["type"] for message in self.stdout.messages()])
        self.factory.assert_not_called()
        self.watchdog.assert_called_once_with(
            grace_period=protocol.EXIT_WATCHDOG_SECONDS, exit_code=0
        )

    def test_closed_stdin_during_startup_starts_the_exit_at_once(self):
        self.start_slow_service()
        closed = time.monotonic()
        self.writer.close()
        exiting = self.stdout.wait_for("exiting", timeout=5)
        self.assertEqual(exiting["reason"], "stdin-closed")
        self.assertLess(time.monotonic() - closed, 2.0)
        self.assertTrue(self.thread.is_alive())  # still inside the slow step, watchdog armed
        self.assert_cancelled_before_ready()

    def test_parent_loss_during_startup_starts_the_exit_within_two_seconds(self):
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        self.start_slow_service(parent_pid=parent.pid)
        parent.kill()
        parent.wait(timeout=10)
        killed = time.monotonic()
        exiting = self.stdout.wait_for("exiting", timeout=5)
        self.assertEqual(exiting["reason"], "parent-exited")
        self.assertLessEqual(time.monotonic() - killed, 2.0)
        self.assert_cancelled_before_ready()

    def test_shutdown_after_the_runtime_exists_quits_it_without_starting_it(self):
        stdin, writer = open_shell_pipe(self)
        stdout = Collector()
        engine = fake_engine()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        from src.desktop.app import create_app

        def factory(config, **kwargs):
            app = create_app(config, ok_instance=engine, ui_dist=Path(directory.name), **kwargs)
            writer.write('{"cmd": "shutdown", "reason": "tray"}\n')
            writer.flush()
            stdout.wait_for("exiting", timeout=5)
            return app

        code = service.run_service(
            lambda: {"version": "9.9.9"},
            stdin=stdin,
            stdout=stdout,
            dependency_check=lambda: None,
            lock_acquirer=lambda mode: (True, None),
            guard_installer=lambda: None,
            app_factory=factory,
            watchdog=Mock(),
            legacy=NO_LEGACY,
        )
        self.assertEqual(code, protocol.EXIT_OK)
        self.assertEqual(stdout.wait_for("exiting")["reason"], "shell:tray")
        self.assertNotIn("ready", [message["type"] for message in stdout.messages()])
        self.assertTrue(engine.exit_event.is_set())  # quit as soon as it was attached
        engine.start_runtime.assert_not_called()  # the server lifespan never ran

    def test_controller_orders_ready_and_exit(self):
        stream = io.StringIO()
        controller = service.ShutdownController(
            service.ProtocolChannel(stream), None, Mock(), watchdog=Mock()
        )
        controller.checkpoint()
        sent = []
        controller.announce_ready(lambda: sent.append("ready"))
        self.assertEqual(sent, ["ready"])
        self.assertTrue(controller.request("stdin-closed"))  # no runtime yet: nothing to quit
        with self.assertRaises(service.StartupCancelled):
            controller.checkpoint()
        with self.assertRaises(service.StartupCancelled):
            controller.announce_ready(lambda: sent.append("late"))
        self.assertEqual(sent, ["ready"])
        quit_runtime = Mock()
        controller.attach_runtime(quit_runtime)
        quit_runtime.assert_called_once_with()


class ServiceConfigTest(unittest.TestCase):
    """F2: the service entry must not run ok-script's per-directory mutex either."""

    def test_service_config_drops_gui_and_the_directory_mutex(self):
        prepared = service.service_config({"gui": {"type": "qt"}, "check_mutex": True, "a": 1})
        self.assertNotIn("gui", prepared)
        self.assertIs(prepared["use_gui"], False)
        self.assertIs(prepared["check_mutex"], False)
        self.assertEqual(prepared["a"], 1)

    def test_the_runtime_built_by_run_service_skips_the_directory_mutex(self):
        """Through the real create_app -> WebRuntime -> ok.OK, stopped right after the check."""
        import ok
        from ok import og
        from ok.util.config import Config

        class ReachedAfterMutex(Exception):
            pass

        missing = object()
        saved_globals = {name: getattr(og, name, missing) for name in ("ok", "config")}
        saved_folder = Config.__dict__.get("config_folder", missing)

        def restore():
            for name, value in saved_globals.items():
                if value is missing:
                    if hasattr(og, name):
                        delattr(og, name)
                else:
                    setattr(og, name, value)
            if saved_folder is not missing:
                Config.config_folder = saved_folder

        self.addCleanup(restore)
        check_mutex = Mock(return_value=True)
        stdin, _writer = open_shell_pipe(self)
        stdout = Collector()
        with (
            patch.object(ok, "check_mutex", check_mutex, create=True),
            patch.object(
                ok,
                "config_logger",
                Mock(side_effect=ReachedAfterMutex("ok.OK 已越过互斥检查")),
                create=True,
            ),
        ):
            code = service.run_service(
                lambda: {"version": "9.9.9", "check_mutex": True},
                stdin=stdin,
                stdout=stdout,
                dependency_check=lambda: None,
                lock_acquirer=lambda mode: (True, None),
                guard_installer=lambda: None,
                watchdog=Mock(),
                legacy=NO_LEGACY,
            )
        self.assertEqual(code, protocol.EXIT_STARTUP_FAILED)
        self.assertIn("ok.OK 已越过互斥检查", stdout.wait_for("fatal", timeout=1)["message"])
        check_mutex.assert_not_called()


class EntryProcessTest(unittest.TestCase):
    """``main.py --service`` as the shell starts it, up to the point a game is needed."""

    def run_entry(self, stdin_text, temp_dir, timeout=60):
        env = dict(os.environ, PYTHONUTF8="1", TMPDIR=temp_dir, TEMP=temp_dir, TMP=temp_dir)
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "main.py"), "--service"],
            cwd=temp_dir,
            input=stdin_text,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )

    def fatal(self, result):
        messages = [
            json.loads(line[len(protocol.LINE_PREFIX) :])
            for line in result.stdout.splitlines()
            if line.startswith(protocol.LINE_PREFIX)
        ]
        fatal = [message for message in messages if message["type"] == "fatal"]
        self.assertTrue(fatal, result.stdout + result.stderr[-2000:])
        return fatal[-1]

    def test_missing_handshake_exits_with_code_10(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_entry("", temp_dir)
        self.assertEqual(result.returncode, protocol.EXIT_HANDSHAKE_FAILED, result.stderr[-2000:])
        self.assertEqual(self.fatal(result)["code"], "handshake")

    def test_second_core_is_refused_by_the_session_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            if os.name == "nt":
                backend = guard._WindowsMutexBackend()
            else:
                backend = guard._FileLockBackend(Path(temp_dir) / guard.POSIX_LOCK_FILE_NAME)
            if not backend.try_acquire():
                self.skipTest("another ok-bd2 instance holds the executor lock")
            try:
                handshake = json.dumps({"v": 1, "token": TOKEN}) + "\n"
                result = self.run_entry(handshake, temp_dir)
            finally:
                backend.release()
        self.assertEqual(result.returncode, protocol.EXIT_EXECUTOR_LOCKED, result.stderr[-2000:])
        self.assertEqual(self.fatal(result)["code"], "executor-locked")


if __name__ == "__main__":
    unittest.main()
