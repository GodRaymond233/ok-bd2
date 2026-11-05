"""Loopback API of the desktop core.

The LocalSession, runtime control, configuration and event tests are ported from
``codex/webui-refactor`` (``tests/test_webui.py`` at ``b8f623d``) without the plan editor,
which main does not have yet.
"""

import asyncio
import json
import tempfile
import threading
import unittest
from collections import ChainMap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from ok import BaseTask, Config
from ok.core.events import EventMessage
from ok.device.capture_methods import bitblt
from ok.device.capture_methods.bitblt import BitBltCaptureMethod, ForegroundBitBltCaptureMethod
from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
from ok.task.exceptions import TaskDisabledException
from ok.task.TaskExecutor import TaskExecutor
from starlette.websockets import WebSocketDisconnect

from src.config import config as bd2_config
from src.desktop.app import adapt_runtime, create_app, frontend_ready
from src.desktop.config_store import save_config
from src.desktop.events import EventBuffer, install_events
from src.desktop.okweb import app as framework
from src.desktop.security import LocalSession
from src.tasks.DailyBatchTask import DailyBatchTask
from src.tasks.QuickHuntTask import QuickHuntTask

TOKEN = "a" * 43
ORIGIN = "http://127.0.0.1:32123"


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
        self.login()
        self.assertTrue(self.client.get("/api/state").json()["ready"])
        with self.client.websocket_connect(
            ORIGIN.replace("http:", "ws:") + "/api/events"
        ) as websocket:
            self.assertTrue(websocket.receive_json()["connected"])

    def test_authenticated_cross_origin_requests_are_rejected(self):
        self.login()
        self.assertEqual(
            self.client.post("/api/config", headers={"Origin": "https://example.com"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/state", headers={"Host": "attacker.example"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/config", headers={"Origin": ORIGIN}).status_code,
            200,
        )
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                ORIGIN.replace("http:", "ws:") + "/api/events", headers={"Origin": "null"}
            ):
                pass

    def test_bearer_header_authenticates_the_shell_process(self):
        good = {"Authorization": f"Bearer {TOKEN}"}
        self.assertEqual(self.client.get("/api/state", headers=good).status_code, 200)
        for header in (f"Bearer {'b' * 43}", f"Basic {TOKEN}", "Bearer", ""):
            with self.subTest(header=header):
                response = self.client.get("/api/state", headers={"Authorization": header})
                self.assertEqual(response.status_code, 403)
        hostile = dict(good, Origin="https://example.com")
        self.assertEqual(self.client.get("/api/state", headers=hostile).status_code, 403)


def make_executor():
    executor = TaskExecutor.__new__(TaskExecutor)
    executor.__dict__.update(
        scene=None,
        lock=threading.Lock(),
        _wake_condition=threading.Condition(),
        _wake_version=0,
        onetime_tasks=[],
        onetime_task_queue=[],
        trigger_tasks=[],
        current_task=None,
        thread=None,
        paused=False,
        debug_mode=False,
        basic_options={},
        start=Mock(),
        reset_scene=Mock(),
    )
    return executor


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
            request.assert_called_once_with(operation="update", version="1.4.12",
                target="v1.4.13", backup_id="", launcher=__file__)
            self.assertEqual(client.post(f"/api/tasks/{task.name}/start").status_code, 400)
            self.assertEqual(client.post(
                f"/api/tasks/{task.name}/config", json={"key": "value", "value": 2}
            ).status_code, 400)
            self.assertEqual(client.post("/api/desktop/backups").status_code, 400)
            self.assertTrue(facade.maintenance)
            self.assertEqual(
                client.post("/api/updates/apply", json={"version": "v1.4.13"}).status_code, 400
            )
            self.assertEqual(request.call_count, 1)

    def test_update_keeps_trigger_preference_and_refuses_unfinished_tasks(self):
        from ok import TriggerTask

        from src.desktop.maintenance import install_maintenance

        self.enterContext(patch("src.desktop.maintenance.active_launcher", return_value=None))
        app, executor, facade = self.make_runtime([TriggerTask])
        task = executor.onetime_tasks.pop()
        executor.trigger_tasks.append(task)
        runtime = app.state.runtime
        runtime.check_for_updates = lambda release_only=True: {"versions": [{"version": "v1.4.13"}]}
        runtime.about = lambda: {"update_supported": True}
        runtime._pyappify_module = SimpleNamespace(pyappify_executable=__file__)
        request = Mock()
        install_maintenance(app, runtime, "1.4.12", request)
        runtime.check_for_updates()
        task._enabled = True
        task.config["_enabled"] = True
        executor.current_task = task
        task.running = True
        with patch("src.desktop.maintenance.time.monotonic", side_effect=[0, 21]):
            with self.assertRaisesRegex(RuntimeError, "安全停止"):
                runtime.update_to_version("v1.4.13")
        request.assert_not_called()
        self.assertTrue(task.enabled)
        self.assertTrue(task.config["_enabled"])
        self.assertFalse(facade.maintenance)
        self.assertTrue(executor.paused)

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

    def test_desktop_toggle_pauses_a_running_task_the_way_the_run_panel_shows(self):
        app, executor, _ = self.make_runtime([BaseTask])
        runtime = app.state.runtime
        task = executor.onetime_tasks[0]
        executor.thread = SimpleNamespace(is_alive=lambda: True)
        executor.current_task = task
        task._enabled = True
        runtime.toggle_execution()
        # The panel's state comes from task.paused (run.ts); a global pause would stay invisible.
        self.assertTrue(task.paused)
        self.assertFalse(executor.paused)
        payload = next(item for item in runtime.tasks() if item["name"] == task.name)
        self.assertTrue(payload["paused"])
        runtime.toggle_execution()
        self.assertFalse(task.paused)

    def test_desktop_toggle_pauses_globally_when_no_one_time_task_runs(self):
        app, executor, _ = self.make_runtime([BaseTask])
        runtime = app.state.runtime
        executor.thread = SimpleNamespace(is_alive=lambda: True)
        # A trigger task (for example the login wait) cannot be paused on its own.
        executor.current_task = SimpleNamespace(name="login", paused=False, enabled=True)
        runtime.toggle_execution()
        self.assertTrue(executor.paused)

    def test_batch_child_config_is_locked_until_batch_and_login_wait_finish(self):
        app, executor, facade = self.make_runtime([QuickHuntTask, DailyBatchTask])
        child, batch = executor.onetime_tasks
        config = child.config
        previous = Path(config.config_file).read_bytes()
        with TestClient(app, raise_server_exceptions=False) as client:
            for phase in ("queued", "running", "login"):
                with self.subTest(phase=phase):
                    batch._enabled = phase == "queued"
                    batch.running = phase == "running"
                    batch._start_after_login = phase == "login"
                    child.config = ChainMap({"启用": True}, config) if batch.running else config
                    for suffix, body in (
                        ("config", {"key": "value", "value": 2}),
                        ("config/reset", {}),
                    ):
                        response = client.post(f"/api/tasks/{child.name}/{suffix}", json=body)
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertIn("停止", response.json()["detail"])
                    self.assertEqual(config["value"], 1)
                    self.assertEqual(Path(config.config_file).read_bytes(), previous)
            response = client.post(f"/api/tasks/{batch.name}/action", json={"action": "stop"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(batch._start_after_login)
            self.assertFalse(DailyBatchTask.release_after_login(executor))
            child.config = config
            response = client.post(
                f"/api/tasks/{child.name}/config", json={"key": "value", "value": 2}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["config_busy"], False)

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

    def test_report_pause_rejects_http_resume_without_starting_then_allows_retry(self):
        start = Mock()
        controller = SimpleNamespace(start=start, starting=False)
        app_facade = SimpleNamespace(start_controller=controller, diagnostic_pause=True)
        executor = SimpleNamespace(
            get_all_tasks=lambda: [],
            current_task=None,
            thread=None,
            paused=True,
            basic_options={},
        )
        engine = SimpleNamespace(
            config={},
            task_executor=executor,
            headless_app=app_facade,
            start_runtime=Mock(),
            quit=Mock(),
        )
        app = framework.create_web_app({}, ok_instance=engine)
        adapt_runtime(app.state.runtime)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/executor/resume")
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("问题报告", response.json()["detail"])
            start.assert_not_called()
            app_facade.diagnostic_pause = False
            response = client.post("/api/executor/resume")
            self.assertEqual(response.status_code, 200, response.text)
            start.assert_called_once()

    def test_frontend_ready_preserves_launcher_upgrade_and_exit_cancellation(self):
        launcher = Mock()
        stopped = threading.Event()
        runtime = SimpleNamespace(pyappify_module=launcher, ok=SimpleNamespace(exit_event=stopped))
        update = {
            "to_version": "1.2.3",
            "sha256": "hash",
            "zip_url": "https://example.test/app.zip",
        }
        frontend_ready({"update_pyappify": update}, runtime)
        launcher.hide_pyappify.assert_called_once_with()
        launcher.upgrade.assert_called_once_with(
            "1.2.3",
            "hash",
            ["https://example.test/app.zip"],
            exit_event=stopped,
        )


class DesktopAppTest(unittest.TestCase):
    def make_app(self, ui_dist, request_shutdown=None, legacy=()):
        engine = SimpleNamespace(
            config={"version": "0.1.1"},
            task_executor=make_executor(),
            headless_app=SimpleNamespace(
                start_controller=SimpleNamespace(start=Mock(), starting=False)
            ),
            start_runtime=Mock(),
            quit=Mock(),
            exit_event=threading.Event(),
        )
        return create_app(
            {"version": "0.1.1"},
            token=TOKEN,
            origin=ORIGIN,
            ok_instance=engine,
            ui_dist=ui_dist,
            legacy_instances=lambda: list(legacy),
            request_shutdown=request_shutdown,
        )

    def test_serves_built_client_and_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            headers = {"Authorization": f"Bearer {TOKEN}"}
            with TestClient(self.make_app(dist), base_url=ORIGIN) as client:
                response = client.get("/", headers=headers)
                self.assertIn("界面尚未构建", response.text)
                self.assertFalse(
                    client.get("/api/desktop/info", headers=headers).json()["ui_built"]
                )
            (dist / "assets").mkdir()
            (dist / "index.html").write_text("<!doctype html><title>client</title>", "utf-8")
            (dist / "assets" / "main.js").write_text("console.log(1)", "utf-8")
            with TestClient(self.make_app(dist), base_url=ORIGIN) as client:
                self.assertIn("client", client.get("/", headers=headers).text)
                self.assertEqual(client.get("/assets/main.js", headers=headers).status_code, 200)
                self.assertEqual(client.get("/assets/main.js").status_code, 403)
                self.assertIn(
                    "default-src 'self'",
                    client.get("/", headers=headers).headers["content-security-policy"],
                )

    def test_info_reports_api_version_and_legacy_instances(self):
        legacy = [SimpleNamespace(pid=40, location="D:\\ok-bd2", describe=lambda: "进程 40")]
        with TestClient(self.make_app(Path("missing"), legacy=legacy), base_url=ORIGIN) as client:
            info = client.get(
                "/api/desktop/info", headers={"Authorization": f"Bearer {TOKEN}"}
            ).json()
        self.assertEqual(info["api"], 1)
        self.assertEqual(info["core_version"], "0.1.1")
        self.assertEqual(info["legacy_instances"][0]["pid"], 40)

    def test_shutdown_request_reaches_the_service(self):
        requested = []
        with TestClient(
            self.make_app(Path("missing"), requested.append), base_url=ORIGIN
        ) as client:
            response = client.post(
                "/api/desktop/shutdown", headers={"Authorization": f"Bearer {TOKEN}"}
            )
        self.assertEqual(response.json(), {"accepted": True})
        self.assertEqual(requested, ["client-request"])

    def test_ui_ready_runs_launcher_hand_off_once(self):
        with patch("src.desktop.app.frontend_ready") as ready:
            with TestClient(self.make_app(Path("missing")), base_url=ORIGIN) as client:
                headers = {"Authorization": f"Bearer {TOKEN}"}
                self.assertTrue(client.post("/api/ui/ready", headers=headers).json()["accepted"])
                self.assertFalse(client.post("/api/ui/ready", headers=headers).json()["accepted"])
        for _ in range(50):
            if ready.call_count:
                break
            threading.Event().wait(0.02)
        ready.assert_called_once()


class FakeDeviceManager:
    """The parts of ok-script's DeviceManager that capture_ui reads, with BD2's methods."""

    def __init__(self, device, capture="windows", capture_method=None):
        self.device = device
        self.config = {"capture": capture, "interaction": ""}
        self.capture_method = capture_method

    def get_preferred_device(self):
        return self.device

    def get_devices(self):
        return [self.device]

    def get_preferred_capture(self):
        return self.config["capture"]

    def available_capture_methods(self, device=None):
        return list(bd2_config["windows"]["capture_method"])

    def available_interaction_methods(self, device=None):
        return ["BD2Interaction"]

    def set_capture(self, capture):
        self.config["capture"] = capture


GAME_WINDOW = {
    "imei": "pc_660888",
    "device": "windows",
    "nick": "BrownDust II",
    "address": "",
    "connected": True,
    "resolution": "1920x1080",
}
# What update_pc_device records while the game is not running.
NO_GAME_WINDOW = {
    "imei": "pc",
    "device": "windows",
    "nick": "BrownDust II.exe",
    "address": "",
    "connected": False,
}


def new(capture_class):
    """A capture object without a window; capture_in_use only looks at its class."""
    return capture_class.__new__(capture_class)


class CaptureUiTest(unittest.TestCase):
    """The game window card of the link check page (F7 of the stage 1 Windows checks)."""

    def client(self, device, **manager_options):
        manager = FakeDeviceManager(device, **manager_options)
        engine = SimpleNamespace(
            config={},
            task_executor=make_executor(),
            device_manager=manager,
            headless_app=SimpleNamespace(
                start_controller=SimpleNamespace(start=Mock(), starting=False),
                overlay_state=lambda: {},
                tr=lambda text: text,
            ),
            start_runtime=Mock(),
            quit=Mock(),
        )
        app = framework.create_web_app({}, ok_instance=engine)
        adapt_runtime(app.state.runtime)
        return TestClient(app)

    def capture_ui(self, device, **manager_options):
        with self.client(device, **manager_options) as client:
            response = client.get("/api/ui/capture")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_automatic_choice_reports_the_method_in_use(self):
        # U4: devices.json keeps capture="windows" and ok-script started WGC.
        ui = self.capture_ui(GAME_WINDOW, capture_method=new(WindowsGraphicsCaptureMethod))
        self.assertIsNone(ui["capture_preference"])
        self.assertEqual(ui["capture_in_use"], "WGC")
        self.assertFalse(any(item["selected"] for item in ui["capture_methods"]))
        self.assertEqual(
            [item["id"] for item in ui["capture_methods"]],
            ["WGC", "BitBlt_RenderFull", "ForegroundBitBlt"],
        )

    def test_chosen_method_and_fallback(self):
        for render_full, expected in ((True, "BitBlt_RenderFull"), (False, "BitBlt")):
            with (
                self.subTest(render_full=render_full),
                patch.object(bitblt, "render_full", render_full),
            ):
                ui = self.capture_ui(
                    GAME_WINDOW,
                    capture="BitBlt_RenderFull",
                    capture_method=new(BitBltCaptureMethod),
                )
                self.assertEqual(ui["capture_preference"], "BitBlt_RenderFull")
                self.assertEqual(ui["capture_in_use"], expected)
        ui = self.capture_ui(
            GAME_WINDOW, capture="WGC", capture_method=new(ForegroundBitBltCaptureMethod)
        )
        self.assertEqual(ui["capture_preference"], "WGC")
        self.assertEqual(ui["capture_in_use"], "ForegroundBitBlt")

    def test_nothing_in_use_without_a_connected_window(self):
        # ok-script keeps its last capture object after the game window is gone.
        ui = self.capture_ui(NO_GAME_WINDOW, capture_method=new(WindowsGraphicsCaptureMethod))
        self.assertIsNone(ui["capture_in_use"])
        self.assertIsNone(self.capture_ui(GAME_WINDOW)["capture_in_use"])

    def test_device_labels_are_chinese(self):
        for device, label in (
            (GAME_WINDOW, "PC 已连接：BrownDust II 1920x1080"),
            (NO_GAME_WINDOW, "PC 未连接：BrownDust II.exe"),
            ({"imei": "emulator-5554", "device": "adb", "connected": False}, "安卓 未连接"),
        ):
            with self.subTest(label=label):
                (payload,) = self.capture_ui(device)["devices"]
                self.assertEqual(payload["label"], label)
                self.assertEqual(payload["id"], device["imei"])
                self.assertEqual(payload["connected"], device["connected"])

    def test_selecting_a_method_returns_the_same_payload(self):
        with self.client(GAME_WINDOW, capture_method=new(WindowsGraphicsCaptureMethod)) as client:
            ui = client.post("/api/capture-methods/select", json={"id": "BitBlt_RenderFull"}).json()
        self.assertEqual(ui["capture_preference"], "BitBlt_RenderFull")
        self.assertEqual(ui["capture_in_use"], "WGC")
        self.assertEqual(ui["devices"][0]["label"], "PC 已连接：BrownDust II 1920x1080")


class ConfigPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Config(
            "web-test", {f"key{i}": 0 for i in range(16)}, folder=self.directory.name
        )

    def test_parallel_edits_preserve_all_keys_on_disk(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: save_config(self.config, {f"key{i}": i + 1}), range(16)))
        saved = json.loads(Path(self.config.config_file).read_text(encoding="utf-8"))
        self.assertEqual(saved, {f"key{i}": i + 1 for i in range(16)})
        self.assertEqual(saved, self.config)

    def test_failed_replace_keeps_previous_disk_and_memory(self):
        previous = Path(self.config.config_file).read_bytes()
        with patch("src.desktop.config_store.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(RuntimeError, "保存配置失败"):
                save_config(self.config, {"key0": 42})
        self.assertEqual(self.config["key0"], 0)
        self.assertEqual(Path(self.config.config_file).read_bytes(), previous)
        self.assertEqual(list(Path(self.directory.name).glob("*.tmp")), [])

    def test_invalid_type_and_unknown_keys_do_not_mutate(self):
        for changes in ({"key0": True}, {"unknown": 1}, {"_enabled": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                save_config(self.config, changes)
        self.assertEqual(self.config["key0"], 0)

    def test_client_integer_values_preserve_float_settings_across_restart(self):
        config = Config("float-setting", {"delay": 0.2}, folder=self.directory.name)
        save_config(config, {"delay": 1}, specifications={"delay": {"min": 0, "max": 2}})
        reloaded = Config("float-setting", {"delay": 0.2}, folder=self.directory.name)
        self.assertEqual(reloaded["delay"], 1.0)
        self.assertIsInstance(reloaded["delay"], float)
        with self.assertRaises(ValueError):
            save_config(config, {"delay": 3}, specifications={"delay": {"max": 2}})


class EventBackpressureTest(unittest.TestCase):
    def test_overflow_resynchronizes_task_state_without_navigation_reload(self):
        app = FastAPI()
        snapshot = {"status": {"paused": True}}
        runtime = SimpleNamespace(event_session_key="test", capture_ui=Mock(return_value=snapshot))
        buffers = []

        def flooded_buffer(loop):
            buffer = EventBuffer(loop, limit=4)
            for index in range(8):
                buffer.receive(EventMessage("log", (20, f"line {index}"), {}))
            buffers.append(buffer)
            return buffer

        install_events(app, runtime)
        with (
            patch("src.desktop.events.EventBuffer", side_effect=flooded_buffer),
            TestClient(app) as client,
        ):
            with client.websocket_connect("/api/events?session_key=test") as websocket:
                messages = [websocket.receive_json() for _ in range(6)]
        self.assertTrue(buffers[0].closed)
        self.assertEqual([message["event"] for message in messages], ["log"] * 5 + ["task"])
        self.assertTrue(messages[-1]["resync"])
        self.assertEqual(messages[-1]["ui"], snapshot)
        runtime.capture_ui.assert_called_once_with()

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

    def test_drain_allows_next_wakeup(self):
        async def exercise():
            buffer = EventBuffer(asyncio.get_running_loop())
            for index in range(2):
                buffer.receive(EventMessage("task", (str(index),), {}))
                await asyncio.wait_for(buffer.ready.wait(), timeout=1)
                messages, dropped = buffer.drain()
                self.assertEqual(messages[0]["args"], [str(index)])
                self.assertEqual(dropped, 0)
            buffer.close()

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
