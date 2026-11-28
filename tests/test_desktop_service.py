import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src import executor_guard as guard
from src.desktop import bootstrap, protocol, service
from src.desktop.shell_install import install_shell

ROOT = Path(__file__).resolve().parents[1]


TOKEN = "t" * 43


class HandshakeTest(unittest.TestCase):
    def line(self, **fields):
        return json.dumps({"v": 1, "token": TOKEN, **fields}) + "\n"

    def test_handshake_validates_the_token_version_and_parent(self):
        parsed = service.parse_handshake(self.line(parent_pid=1234))
        self.assertEqual((parsed.token, parsed.parent_pid), (TOKEN, 1234))
        self.assertIsNone(service.parse_handshake(self.line()).parent_pid)
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
    def test_handshake_and_commands_share_the_polled_reader(self):
        handshake = json.dumps({"v": 1, "token": TOKEN}) + "\n"
        command = '{"cmd": "shutdown", "reason": "tray"}\n'
        reader = FakePipe((handshake + command).encode()).reader()  # one chunk, two lines
        self.assertEqual(service.read_handshake(reader, timeout=2).token, TOKEN)
        requests = []
        service.CommandReader(reader, requests.append, Mock()).start().thread.join(timeout=2)
        self.assertEqual(requests, ["shell:tray", "stdin-closed"])


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


SHELL = ROOT / "desktop" / "shell"


def ts_number(source, name):
    match = re.search(rf"\b{name}:\s*([0-9_]+)", source)
    return int(match.group(1).replace("_", ""))


class ShellContractTest(unittest.TestCase):
    def setUp(self):
        self.source = (SHELL / "src" / "protocol.ts").read_text(encoding="utf-8")

    def test_line_prefix_and_versions_match(self):
        self.assertIn(f'LINE_PREFIX = "{protocol.LINE_PREFIX}"', self.source)
        self.assertIn(f"PROTOCOL_VERSION = {protocol.PROTOCOL_VERSION};", self.source)
        self.assertEqual(ts_number(self.source, "min"), protocol.API_VERSION)
        self.assertGreaterEqual(ts_number(self.source, "max"), protocol.API_VERSION)
        for key in ("OK", "HANDSHAKE_FAILED", "EXECUTOR_LOCKED", "STARTUP_FAILED"):
            with self.subTest(code=key):
                self.assertEqual(ts_number(self.source, key), getattr(protocol, f"EXIT_{key}"))
        messages = re.findall(r'\{"type": "(\w+)"', protocol.__doc__)
        self.assertIn("notification", messages)
        for message in messages:
            self.assertIn(f'case "{message}":', self.source)
        self.assertEqual(ts_number(self.source, "readyMs"), protocol.READY_TIMEOUT_SECONDS * 1000)
        self.assertGreater(
            ts_number(self.source, "shutdownMs"), protocol.EXIT_WATCHDOG_SECONDS * 1000 + 5000
        )
        self.assertLessEqual(protocol.PARENT_POLL_SECONDS, 1)
        package = json.loads((SHELL / "package.json").read_text("utf-8"))
        lock = json.loads((SHELL / "package-lock.json").read_text("utf-8"))
        self.assertEqual(package["version"], bootstrap.REQUIRED_SHELL_VERSION)
        self.assertEqual(package["build"]["executableName"] + ".exe", "ok-bd2-desktop.exe")
        self.assertEqual(package["devDependencies"]["electron"], "44.11.3")
        self.assertEqual(
            lock["packages"]["node_modules/electron"]["version"],
            package["devDependencies"]["electron"],
        )


NO_QT_TASKS_SCRIPT = r"""
import importlib.abc
import os
import sys
import tempfile
import threading
from types import SimpleNamespace

class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6', 'qfluentwidgets', 'shiboken6', 'webview'}:
            raise ImportError('Qt is unavailable in service mode: ' + fullname)

sys.meta_path.insert(0, NoQt())
sys.path.insert(0, os.getcwd())

from ok import Config, og
from ok.task.TaskExecutor import TaskExecutor
from ok.util.clazz import init_class_by_name
from ok.util.handler import ExitEvent, Handler

from src.config import config
from src.debug_profile import configure_debug_profile
from src.tasks.debug_registry import install_debug_tasks

configure_debug_profile(config, release_tag='v0.1.1')
install_debug_tasks(config)
Config.config_folder = tempfile.mkdtemp()
og.config = config
executor = TaskExecutor.__new__(TaskExecutor)
executor.__dict__.update(
    scene=None, lock=threading.Lock(), _wake_condition=threading.Condition(), _wake_version=0,
    onetime_tasks=[], onetime_task_queue=[], trigger_tasks=[], current_task=None, thread=None,
    paused=False, debug_mode=False, basic_options={}, config_folder=Config.config_folder,
    global_config=None,
)
app = SimpleNamespace(debug=False, locale='zh_CN', tr=lambda text: text)
scene = init_class_by_name(*config['scene'])
created = []
for module, name in config['onetime_tasks'] + config['trigger_tasks']:
    task = init_class_by_name(module, name, executor=executor, app=app)
    task.after_init(executor=executor, scene=scene)
    created.append(type(task).__name__)

exit_event = ExitEvent()
og.handler = Handler(exit_event, 'service-mode-test')
og.device_manager = SimpleNamespace(config={}, get_preferred_device=lambda: None)
from src.globals import Globals
from src.compat import executor_cleanup
from src.tasks import auto_scheduler
Globals(exit_event)
assert auto_scheduler.install_auto_scheduler._installed
assert TaskExecutor.execute is executor_cleanup._execute
exit_event.set()
og.handler.join(timeout=5)

loaded = sorted(m for m in sys.modules if m.split('.')[0] in {'PySide6', 'qfluentwidgets'})
assert not loaded, loaded
sys.stdout.write(f"CREATED {len(created)} {','.join(created)}\n")
"""


DESKTOP_IMPORTS_SCRIPT = r"""
import importlib
import importlib.abc
import pkgutil
import sys

class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6', 'qfluentwidgets', 'shiboken6', 'webview'}:
            raise ImportError('not allowed in service mode: ' + fullname)

sys.meta_path.insert(0, NoQt())
sys.path.insert(0, '.')
import src.desktop

names = ['src.executor_guard', 'src.ui_mode', 'src.icons']
names += [f'src.desktop.{info.name}' for info in pkgutil.iter_modules(src.desktop.__path__)]
for name in names:
    importlib.import_module(name)
print('IMPORTED', ','.join(sorted(names)))
"""


def run_isolated(script, **env):
    return subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=ROOT,
        env=dict(os.environ, PYTHONUTF8="1", **env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


class ServiceModeWithoutQtTest(unittest.TestCase):
    def test_every_registered_task_is_created_without_importing_qt(self):
        result = run_isolated(NO_QT_TASKS_SCRIPT)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        line = next(line for line in result.stdout.splitlines() if line.startswith("CREATED "))
        _, count, names = line.split(" ", 2)
        names = names.split(",")
        self.assertEqual(int(count), len(names))
        for expected in (
            "DailyBatchTask",
            "DailyTask",
            "QuickHuntTask",
            "SquareGoddessTask",
            "MapTradeTask",
            "MapCollectionTask",
            "FreeGachaTask",
            "PVPTask",
            "AutoLoginTask",
            "BD2DiagnosisTask",
        ):
            self.assertIn(expected, names)

    def test_desktop_modules_import_without_qt_or_pywebview(self):
        result = run_isolated(DESKTOP_IMPORTS_SCRIPT, OK_BD2_UI="service")
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        line = next(line for line in result.stdout.splitlines() if line.startswith("IMPORTED "))
        imported = line.split(" ", 1)[1].split(",")
        for expected in ("app", "config_store", "events", "protocol", "security", "service"):
            self.assertIn(f"src.desktop.{expected}", imported)


PROBE = Path(__file__).resolve().parents[1] / "desktop" / "scripts" / "lifecycle_probe.py"


_spec = importlib.util.spec_from_file_location("lifecycle_probe", PROBE)


probe = importlib.util.module_from_spec(_spec)


_spec.loader.exec_module(probe)


class CheckShellTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, manifest=None, executable=True):
        if executable:
            (self.root / bootstrap.SHELL_EXECUTABLE).write_bytes(b"")
        if manifest is not None:
            (self.root / bootstrap.MANIFEST_NAME).write_text(json.dumps(manifest), "utf-8")

    def assert_refused(self, code):
        with self.assertRaises(bootstrap.BootstrapError) as raised:
            bootstrap.check_shell(self.root)
        self.assertEqual(raised.exception.exit_code, code)

    def test_missing_or_mismatched_manifest(self):
        self.write()
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)
        self.write({"version": "0.0.9", "api": {"min": 1, "max": 1}})
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)
        self.write({"version": bootstrap.REQUIRED_SHELL_VERSION, "api": {"min": 2, "max": 3}})
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)


class ShellDownloadTest(unittest.TestCase):
    def test_pinned_download_uses_next_source_and_keeps_old_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "shell/0.1.0/sentinel"
            old.parent.mkdir(parents=True)
            old.write_text("old", "utf-8")
            archive = root / "shell.zip"
            manifest = {"version": bootstrap.REQUIRED_SHELL_VERSION, "api": {"min": 1, "max": 1}}
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(bootstrap.SHELL_EXECUTABLE, b"verified shell")
                output.writestr(bootstrap.MANIFEST_NAME, json.dumps(manifest))
            release = root / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "version": bootstrap.REQUIRED_SHELL_VERSION,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "urls": [(root / "missing.zip").as_uri(), archive.as_uri()],
                    }
                ),
                "utf-8",
            )
            destination = root / "shell" / bootstrap.REQUIRED_SHELL_VERSION
            executable = install_shell(
                destination,
                bootstrap.REQUIRED_SHELL_VERSION,
                bootstrap.check_shell,
                release_file=release,
            )
            self.assertEqual(executable.read_bytes(), b"verified shell")
            self.assertEqual(old.read_text("utf-8"), "old")
            executable.unlink()
            self.assertEqual(
                install_shell(
                    destination,
                    bootstrap.REQUIRED_SHELL_VERSION,
                    bootstrap.check_shell,
                    release_file=release,
                ).read_bytes(),
                b"verified shell",
            )
            self.assertEqual(len(list(destination.parent.glob("*.broken-*"))), 1)
            archive.unlink()
            self.assertEqual(
                install_shell(
                    destination,
                    bootstrap.REQUIRED_SHELL_VERSION,
                    bootstrap.check_shell,
                    release_file=release,
                ),
                executable,
            )

    def test_hash_mismatch_and_unsafe_zip_leave_no_installed_version(self):
        for name, wrong_hash in (
            ("../outside", False),
            ("C:/outside", False),
            ("file:stream", False),
            ("CON.txt", False),
            (bootstrap.SHELL_EXECUTABLE, True),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(name, b"bad")
                release = root / "release.json"
                release.write_text(
                    json.dumps(
                        {
                            "version": bootstrap.REQUIRED_SHELL_VERSION,
                            "sha256": "0" * 64
                            if wrong_hash
                            else hashlib.sha256(archive.read_bytes()).hexdigest(),
                            "urls": [archive.as_uri()],
                        }
                    ),
                    "utf-8",
                )
                destination = root / "shell" / bootstrap.REQUIRED_SHELL_VERSION
                with self.assertRaises(ValueError):
                    install_shell(
                        destination,
                        bootstrap.REQUIRED_SHELL_VERSION,
                        bootstrap.check_shell,
                        release_file=release,
                    )
                self.assertFalse(destination.exists())
                self.assertFalse((root / "outside").exists())
