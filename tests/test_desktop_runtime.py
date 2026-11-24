import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from ok import BaseTask, Config
from ok.core.events import EventMessage, communicate
from ok.task.exceptions import TaskDisabledException
from starlette.websockets import WebSocketDisconnect

from src.compat.starter_guard import starter_wait_status
from src.desktop import language as language_module
from src.desktop.app import adapt_runtime, create_app
from src.desktop.config_store import save_config
from src.desktop.events import EventBuffer
from src.desktop.language import stored_language
from src.desktop.okweb import app as framework
from src.desktop.security import LocalSession
from src.desktop.starter_wait import STARTER_WAIT, StarterWait
from src.desktop.tools import DesktopTools
from src.window_resolution import ManualResolutionError
from tests.helpers.desktop import (
    ORIGIN,
    TOKEN,
    make_client,
    make_executor,
)


class LocalSessionTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()

        @app.get("/api/state")
        def state():
            return {"ready": True}

        @app.post("/api/config")
        def change_config():
            return {"saved": True}

        @app.websocket("/api/events")
        async def events(websocket: WebSocket):
            await websocket.accept()
            await websocket.send_json({"connected": True})
            await websocket.close()

        app.add_middleware(LocalSession, token=TOKEN, origin=ORIGIN)
        self.client = TestClient(app, base_url=ORIGIN)

    def tearDown(self):
        self.client.close()

    def login(self):
        response = self.client.get(f"/launch/{TOKEN}", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])

    def test_capability_required_for_http_and_websocket(self):
        self.assertEqual(self.client.get("/api/state").status_code, 403)
        self.assertEqual(self.client.get("/launch/wrong").status_code, 403)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(ORIGIN.replace("http:", "ws:") + "/api/events"):
                pass
        for header, status in (
            (f"Bearer {TOKEN}", 200),
            (f"Bearer {'b' * 43}", 403),
            (f"Basic {TOKEN}", 403),
            ("", 403),
        ):
            with self.subTest(authorization=header):
                self.assertEqual(
                    status,
                    self.client.get("/api/state", headers={"Authorization": header}).status_code,
                )
        self.login()
        self.assertTrue(self.client.get("/api/state").json()["ready"])
        for method, path, headers, expected in (
            ("post", "/api/config", {"Origin": "https://example.com"}, 403),
            ("get", "/api/state", {"Host": "attacker.example"}, 403),
            ("post", "/api/config", {"Origin": ORIGIN}, 200),
        ):
            with self.subTest(method=method, headers=headers):
                self.assertEqual(
                    expected, self.client.request(method, path, headers=headers).status_code
                )
        with (
            self.assertRaises(WebSocketDisconnect),
            self.client.websocket_connect(
                ORIGIN.replace("http:", "ws:") + "/api/events", headers={"Origin": "null"}
            ),
        ):
            pass
        with self.client.websocket_connect(
            ORIGIN.replace("http:", "ws:") + "/api/events"
        ) as websocket:
            self.assertTrue(websocket.receive_json()["connected"])


class RuntimeControlTest(unittest.TestCase):
    def test_update_requires_checked_newer_version_and_blocks_mutation_during_handoff(self):
        from src.desktop.maintenance import install_maintenance

        self.enterContext(patch("src.desktop.maintenance.active_launcher", return_value=None))
        app, executor, facade = self.make_runtime([BaseTask])
        runtime = app.state.runtime
        runtime.check_for_updates = lambda release_only=True: {
            "versions": [{"version": "v1.4.13"}, {"version": "v1.4.11"}]
        }
        runtime.about = lambda: {"update_supported": True}
        runtime._pyappify_module = SimpleNamespace(pyappify_executable=__file__)
        request = Mock()
        install_maintenance(app, runtime, "1.4.12", request)
        task = executor.onetime_tasks[0]
        with TestClient(app) as client:
            self.assertEqual(
                client.post("/api/updates/apply", json={"version": "v1.4.13"}).status_code, 400
            )
            runtime.check_for_updates()
            with patch("src.desktop.maintenance.active_launcher", return_value=Path("other.exe")):
                with self.assertRaisesRegex(ValueError, "另一安装目录"):
                    runtime.check_for_updates()
                self.assertEqual(
                    client.post("/api/updates/apply", json={"version": "v1.4.13"}).status_code, 400
                )
            self.assertEqual(
                client.post("/api/updates/apply", json={"version": "v1.4.11"}).status_code, 400
            )
            self.assertFalse(getattr(facade, "maintenance", False))
            task._enabled = True
            response = client.post("/api/updates/apply", json={"version": "v1.4.13"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(facade.maintenance)
            self.assertTrue(executor.paused)
            self.assertFalse(task.enabled)
            request.assert_called_once_with(
                operation="update",
                version="1.4.12",
                target="v1.4.13",
                backup_id="",
                launcher=__file__,
            )
            self.assertEqual(client.post(f"/api/tasks/{task.name}/start").status_code, 400)
            self.assertEqual(
                client.post(
                    f"/api/tasks/{task.name}/config", json={"key": "value", "value": 2}
                ).status_code,
                400,
            )
            self.assertEqual(client.post("/api/desktop/backups").status_code, 400)
            self.assertTrue(facade.maintenance)
            self.assertEqual(
                client.post("/api/updates/apply", json={"version": "v1.4.13"}).status_code, 400
            )
            self.assertEqual(request.call_count, 1)

    def make_runtime(self, task_classes, legacy_check=None):
        self.enterContext(patch("ok.core.events.communicate.task.emit"))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app_facade = SimpleNamespace(start_controller=SimpleNamespace(start=Mock(), starting=False))
        executor = make_executor()
        for index, task_class in enumerate(task_classes):
            task = task_class.__new__(task_class)
            BaseTask.__init__(task, executor=executor, app=app_facade)
            task.name = f"task-{index}"
            task.default_config = {"value": 1}
            task.config = Config(task.name, task.default_config, folder=directory.name)
            executor.onetime_tasks.append(task)
        executor.get_task_by_class = lambda cls: next(
            (task for task in executor.onetime_tasks if isinstance(task, cls)), None
        )
        engine = SimpleNamespace(
            config={},
            task_executor=executor,
            headless_app=app_facade,
            get_task=lambda name: (
                next(task for task in executor.onetime_tasks if task.name == name),
                False,
            ),
            start_runtime=Mock(),
            quit=Mock(),
        )
        app = framework.create_web_app({}, ok_instance=engine)
        self.controller_start = app_facade.start_controller.start
        adapt_runtime(app.state.runtime, legacy_check=legacy_check)
        return app, executor, app_facade

    def test_stop_cancels_current_or_queued_task_without_resuming_executor(self):
        app, executor, facade = self.make_runtime([BaseTask, BaseTask])
        current, queued = executor.onetime_tasks
        with TestClient(app) as client:
            for pause in (None, "global", "diagnostic_pause", "window_resize_in_progress"):
                for target in ("current", "queued", "global-stop"):
                    with self.subTest(pause=pause, target=target):
                        executor.paused = pause is not None
                        facade.diagnostic_pause = pause == "diagnostic_pause"
                        facade.window_resize_in_progress = pause == "window_resize_in_progress"
                        executor.current_task = current
                        executor.onetime_task_queue = [queued]
                        current._enabled = queued._enabled = True
                        current._paused = queued._paused = True
                        current.running, queued.running = True, False
                        task = queued if target == "queued" else current
                        response = (
                            client.post("/api/executor/stop-task")
                            if target == "global-stop"
                            else client.post(
                                f"/api/tasks/{task.name}/action", json={"action": "stop"}
                            )
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertFalse(task.enabled)
                        self.assertFalse(task.paused)
                        self.assertEqual(executor.paused, pause is not None)
                        executor.start.assert_not_called()
                        if task is current:
                            self.assertTrue(queued.enabled)
                            self.assertEqual(executor.onetime_task_queue, [queued])
                            with self.assertRaises(TaskDisabledException):
                                executor.sleep(0.01)
                        else:
                            self.assertTrue(current.enabled)
                            self.assertEqual(executor.onetime_task_queue, [])

    def test_desktop_toggle_uses_current_state_and_resume_guards(self):
        blocked = [None]
        app, executor, facade = self.make_runtime([BaseTask], legacy_check=lambda: blocked[0])
        runtime = app.state.runtime
        runtime.toggle_execution()
        self.controller_start.assert_called_once()
        self.controller_start.reset_mock()
        executor.thread = SimpleNamespace(is_alive=lambda: True)
        runtime.toggle_execution()
        self.assertTrue(executor.paused)
        for attribute in ("diagnostic_pause", "window_resize_in_progress"):
            setattr(facade, attribute, True)
            with self.assertRaises(RuntimeError):
                runtime.toggle_execution()
            setattr(facade, attribute, False)
        blocked[0] = "旧版正在运行"
        with self.assertRaisesRegex(RuntimeError, "旧版"):
            runtime.toggle_execution()
        blocked[0] = None
        facade.start_controller.starting = True
        with self.assertRaisesRegex(ValueError, "正在启动"):
            runtime.toggle_execution()
        facade.start_controller.starting = False
        self.controller_start.assert_not_called()
        task = executor.onetime_tasks[0]
        executor.current_task = task
        task._enabled = task._paused = True
        runtime.toggle_execution()
        self.assertFalse(task.paused)
        executor.start.assert_called_once()

    def test_legacy_instance_refuses_start_resume_and_enable(self):
        message = "检测到正在运行的旧版 ok-bd2（进程 40）。请先退出旧版，再开始任务。"
        app, executor, facade = self.make_runtime([BaseTask], legacy_check=lambda: message)
        task = executor.onetime_tasks[0]
        with TestClient(app, raise_server_exceptions=False) as client:
            for method, path, body in (
                ("post", f"/api/tasks/{task.name}/start", None),
                ("post", "/api/executor/resume", None),
                ("post", f"/api/tasks/{task.name}/action", {"action": "resume"}),
            ):
                with self.subTest(path=path, body=body):
                    task._enabled = path.endswith("action")
                    task._paused = path.endswith("action")
                    response = getattr(client, method)(path, json=body)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json()["detail"], message)
            self.controller_start.assert_not_called()


class DesktopToolsTest(unittest.TestCase):
    def setUp(self):
        from tests.helpers.desktop import make_client

        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(
            patch(
                "src.desktop.tools.Config",
                side_effect=lambda name, defaults: Config(name, defaults, folder=self.folder),
            )
        )
        self.client = make_client(self)
        self.runtime = self.client.app.state.runtime
        self.tools = self.client.app.state.tools
        self.engine = self.runtime.ok
        self.engine._mcp_server = None
        self.engine.set_mcp_enabled = Mock()
        self.executor.exit_event = threading.Event()
        self.executor._start_thread_locked = Mock()
        self.task = BaseTask(executor=self.executor, app=self.headless)
        self.task.config = Config("fixture", {"value": 1}, folder=self.folder)
        self.task.default_config = {"value": 1}
        self.executor.onetime_tasks.append(self.task)
        self.engine.get_task = lambda name: (self.task, False)

    def post(self, action, **body):
        return self.client.post(f"/api/desktop/tools/{action}", json=body)

    def execute(self, **body):
        response = self.post("run", **body)
        self.assertEqual(response.status_code, 200, response.text)
        self.executor._run_job(self.executor._pop_job())
        return self.client.get("/api/desktop/tools").json()

    def test_tool_ownership_blocks_mutations_until_cancelled(self):
        response = self.post("run", kind="code", code="1 + 1")
        self.assertTrue(response.json()["active"])
        self.assertTrue(self.client.get("/api/ui/capture").json()["tools_busy"])
        for path, body in (
            ("/api/desktop/tools/run", {"kind": "check"}),
            ("/api/desktop/tools/reset", {}),
            (f"/api/tasks/{self.task.name}/start", {}),
            (f"/api/tasks/{self.task.name}/config", {"key": "value", "value": 2}),
            ("/api/bd2/report/prepare", {}),
            ("/api/desktop/backups", {}),
        ):
            with self.subTest(path=path):
                refused = self.client.post(path, json=body)
                self.assertEqual(refused.status_code, 400, refused.text)
                self.assertIn("调试操作", refused.text)
        cancelled = self.post("stop").json()
        self.assertTrue(cancelled["result"]["cancelled"])
        self.assertFalse(cancelled["active"])
        self.assertIsNone(self.executor.exclusive_owner)
        self.assertFalse(self.client.get("/api/ui/capture").json()["tools_busy"])
        self.assertEqual(self.task.config["value"], 1)


class ConfigPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Config(
            "web-test", {f"key{i}": 0.2 for i in range(16)}, folder=self.directory.name
        )

    def test_parallel_edits_preserve_all_keys_on_disk(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: save_config(self.config, {f"key{i}": i + 1}), range(16)))
        saved = json.loads(Path(self.config.config_file).read_text(encoding="utf-8"))
        self.assertEqual(saved, {f"key{i}": i + 1 for i in range(16)})
        self.assertEqual(saved, self.config)
        reloaded = Config(
            "web-test", {f"key{i}": 0.2 for i in range(16)}, folder=self.directory.name
        )
        for key in saved:
            self.assertIsInstance(reloaded[key], float)
        with self.assertRaises(ValueError):
            save_config(self.config, {"key0": 3}, specifications={"key0": {"max": 2}})

    def test_failed_replace_keeps_previous_disk_and_memory(self):
        previous = Path(self.config.config_file).read_bytes()
        with patch("src.desktop.config_store.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(RuntimeError, "保存配置失败"):
                save_config(self.config, {"key0": 42})
        self.assertEqual(self.config["key0"], 0.2)
        self.assertEqual(Path(self.config.config_file).read_bytes(), previous)
        self.assertEqual(list(Path(self.directory.name).glob("*.tmp")), [])


class EventBackpressureTest(unittest.TestCase):
    def test_log_flood_is_bounded_before_asyncio_dispatch(self):
        loop = Mock()
        buffer = EventBuffer(loop, limit=4)
        buffer.receive(EventMessage("task_done", ("daily",), {}))
        for index in range(1000):
            buffer.receive(EventMessage("log", (20, f"line {index}"), {}))
        self.assertEqual(len(buffer.queue), 4)
        loop.call_soon_threadsafe.assert_called_once()
        messages, dropped = buffer.drain()
        self.assertEqual(messages[0]["event"], "task_done")
        self.assertEqual(messages[-1]["args"][1], "line 999")
        self.assertEqual(dropped, 997)
        buffer.close()
        buffer.receive(EventMessage("log", (20, "after close"), {}))
        self.assertEqual(len(buffer.queue), 0)


class Window:
    def __init__(self, width, height):
        self.width = width
        self.height = height


class WindowTest(unittest.TestCase):
    def test_capture_changes_respect_active_tasks_and_report_window_size(self):
        from src.desktop.app import AUTO_CAPTURE_CONFIG
        from src.window_resolution import WindowResizeResult

        client = make_client(self, window=Window(1600, 900))
        capture_path = "/api/capture-methods/select"
        self.manager.config["capture"] = "WGC"
        for busy, expected in ((True, 400), (False, 200)):
            with self.subTest(capture_busy=busy):
                self.executor.current_task = object() if busy else None
                response = client.post(capture_path, json={"id": "auto"})
                self.assertEqual(expected, response.status_code, response.text)
                self.assertEqual(
                    "WGC" if busy else AUTO_CAPTURE_CONFIG, self.manager.config["capture"]
                )
        size = {"width": 1920, "height": 1080}
        for state, code, reason in (
            ("running", 400, "暂停"),
            ("starting", 400, "正在启动游戏"),
            ("minimized", 400, "最小化"),
            ("ready", 200, ""),
        ):
            with self.subTest(state=state):
                self.executor.current_task = object() if state == "running" else None
                self.headless.start_controller.starting = state == "starting"

                def resize(manager, target, executor=None):
                    self.assertTrue(self.headless.window_resize_in_progress)
                    self.assertEqual(target, (1920, 1080))
                    if state == "minimized":
                        raise ManualResolutionError("游戏窗口已最小化，请先恢复窗口。")
                    return WindowResizeResult(*target, changed=True)

                with patch("src.desktop.app.resize_game_window", side_effect=resize) as operation:
                    reply = client.post("/api/bd2/window/resize", json=size)
                self.assertEqual(code, reply.status_code, reply.text)
                self.assertEqual(int(state in {"ready", "minimized"}), operation.call_count)
                self.assertFalse(getattr(self.headless, "window_resize_in_progress", False))
                if reason:
                    self.assertIn(reason, reply.json()["detail"])
                else:
                    self.assertEqual({**size, "changed": True}, reply.json())


class TestNotificationTest(unittest.TestCase):
    def post(self, client, channel):
        return client.post("/api/bd2/notify/test", json={"channel": channel})

    def test_sends_one_message_through_the_chosen_channel_only(self):
        client = make_client(self)
        notify = self.executor.global_config.configs["Notification"]
        notify["Telegram Bot Token"] = " 123:abc "
        notify["Telegram Chat ID"] = "42"
        notify["Discord Webhook"] = "https://discord.example/hook"
        with (
            patch("ok.notification.providers.TelegramBotProvider.send", return_value=True) as tg,
            patch("ok.notification.providers.DiscordProvider.send") as discord,
        ):
            response = self.post(client, "telegram")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(("123:abc", "42"), tg.call_args.args[:2])
        discord.assert_not_called()


class StarterWaitTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.wait = StarterWait(clock=lambda: self.now)
        self.wait.install()
        self.addCleanup(self.wait.uninstall)
        self.events = []
        listener = communicate.any.connect(lambda event: self.events.append(event.name))
        self.addCleanup(communicate.any.disconnect, listener)

    def test_capture_ui_and_cancel_route_use_the_process_wait(self):
        client = make_client(self)
        self.addCleanup(communicate.starting_emulator.emit, True, None, 0)
        self.assertIsNone(client.get("/api/ui/capture").json()["starter_wait"])
        controller = self.headless.start_controller
        controller.starting = True
        controller.cancel_start.return_value = True
        self.assertEqual({"cancelled": True}, client.post("/api/bd2/starter/cancel").json())
        controller.cancel_start.assert_called_once_with()
        controller.cancel_start.return_value = False
        self.assertEqual({"cancelled": False}, client.post("/api/bd2/starter/cancel").json())
        controller.cancel_start.return_value = True
        controller.cancel_start.reset_mock()
        cancel = threading.Event()
        starter_wait_status.emit("", cancel)
        self.assertIsNotNone(STARTER_WAIT.snapshot())
        self.assertIsNotNone(client.get("/api/ui/capture").json()["starter_wait"])
        self.assertEqual({"cancelled": True}, client.post("/api/bd2/starter/cancel").json())
        self.assertTrue(cancel.is_set())
        controller.cancel_start.assert_called_once_with()
        controller.starting = False
        self.assertEqual({"cancelled": False}, client.post("/api/bd2/starter/cancel").json())


HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class LanguageRouteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ui_config.json"
        patcher = patch.object(language_module, "preference_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        executor = make_executor()
        executor.global_config = SimpleNamespace(get_all_visible_configs=lambda: [])
        self.engine = SimpleNamespace(
            config={"version": "0.1.1"},
            task_executor=executor,
            headless_app=SimpleNamespace(
                start_controller=SimpleNamespace(start=Mock(), starting=False)
            ),
            start_runtime=Mock(),
            quit=Mock(),
            exit_event=threading.Event(),
        )
        app = create_app(
            {"version": "0.1.1"},
            token=TOKEN,
            origin=ORIGIN,
            ok_instance=self.engine,
            ui_dist=Path(self.temporary.name),
        )
        self.client = TestClient(app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_reads_saves_and_refuses(self):
        state = self.client.get("/api/desktop/language", headers=HEADERS).json()
        self.assertEqual(state["language"], "Auto")
        self.assertEqual(state["catalogs"]["en_US"]["一键完成日常"], "Complete dailies")
        saved = self.client.post(
            "/api/desktop/language", json={"language": "en_US"}, headers=HEADERS
        )
        self.assertEqual(saved.json()["language"], "en_US")
        self.assertEqual(stored_language(self.path), "en_US")
        refused = self.client.post(
            "/api/desktop/language", json={"language": "ja_JP"}, headers=HEADERS
        )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(stored_language(self.path), "en_US")


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_task(self, file_path):
        self.calls.append(("run_task", file_path))

    def run_code(self, source):
        self.calls.append(("run_code", source))

    def reload_tasks(self, task_ids=None):
        self.calls.append(("reload_tasks", task_ids))


class FakeMcpServer:
    def __init__(self, ok, config):
        self.runner = FakeRunner()

    def start(self):
        pass


class McpRunGuardTest(unittest.TestCase):
    def test_every_runner_entry_checks_the_client_gates(self):
        runtime = SimpleNamespace(
            ok=SimpleNamespace(config={"mcp": {"enabled": True}}, _mcp_server=None),
            executor=SimpleNamespace(basic_options={}),
        )
        tools = DesktopTools(runtime)
        tools.guard = Mock(side_effect=ValueError("任务运行中"))
        with patch("ok.core.mcp_server.LocalMcpServer", FakeMcpServer):
            tools.start_mcp()
        runner = runtime.ok._mcp_server.runner
        # run_code arrived with ok-script 3.0.10 and runs a snippet as a task.
        for name, argument in (
            ("run_task", "task.py"),
            ("run_code", "self.sleep(1)"),
            ("reload_tasks", None),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                getattr(runner, name)(argument)
        self.assertEqual([], runner.calls)
        tools.guard.side_effect = None
        runner.run_code("1 + 1")
        self.assertEqual([("run_code", "1 + 1")], runner.calls)
