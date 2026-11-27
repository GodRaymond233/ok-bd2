import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from ok import BaseTask, Config
from ok.core.template_store import CocoTemplateStore

from src.desktop.app import create_app
from src.tasks.DailyBatchTask import (
    DailyBatchChild,
    DailyBatchTask,
)
from src.tasks.quick_hunt_plan import PLAN_KEY, load_book, preset_plans
from src.tasks.QuickHuntTask import QuickHuntTask
from src.tasks.run_history import (
    BEIJING_TZ,
    RECENT_LIMIT,
    RunHistoryStore,
    set_default_store,
)
from src.tasks.today import batch_items, refresh_times
from tests.helpers.desktop import ORIGIN, TOKEN, make_executor

NOW = datetime(2026, 10, 22, 10, 0, tzinfo=BEIJING_TZ).timestamp()


class _Daily:
    pass


class _Hunt:
    pass


class _Collection:
    pass


def _child(name):
    return SimpleNamespace(name=name, config={"启用": True, "选项": 1}, start_time=0, info={})


def _record(store, task, finished, status="完成。", **info):
    task.info = {"状态": status, **info}
    store.record_task_done(task, finished=finished, started=finished - 60)


def _batch_executor(config=None):
    children = {
        _Daily: _child("公会、小屋、酒馆"),
        _Hunt: _child("快速狩猎"),
        _Collection: _child("每周跑图"),
    }
    batch = SimpleNamespace(
        name="一键完成日常",
        config={"启用": True, "日常": True, "狩猎": True, "跑图": True, **(config or {})},
        child_tasks=(
            DailyBatchChild("日常", _Daily),
            DailyBatchChild("狩猎", _Hunt),
            DailyBatchChild("跑图", _Collection),
        ),
        skip_reasons=lambda: {"狩猎": {"reason": "not_due", "minutes": 25}},
    )
    executor = SimpleNamespace(
        onetime_tasks=[batch, *children.values()],
        get_task_by_class=children.get,
    )
    return executor, children


class TodaySummaryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = RunHistoryStore(f"{directory.name}/history.json")

    def test_children_in_run_order_with_participation_and_last_run(self):
        executor, children = _batch_executor({"狩猎": False})
        _record(self.store, children[_Daily], NOW - 600)
        _record(self.store, children[_Hunt], NOW - 86400, status="快速狩猎中止。")

        items = batch_items(executor, self.store, NOW)

        self.assertEqual(["公会、小屋、酒馆", "快速狩猎", "每周跑图"], [i["name"] for i in items])
        self.assertEqual(["日常", "狩猎", "跑图"], [i["key"] for i in items])
        self.assertEqual([True, False, True], [i["enabled"] for i in items])
        self.assertEqual([True, False, False], [i["done"] for i in items])
        self.assertEqual(["today", None, None], [i["completion"] for i in items])
        self.assertEqual([False, False, True], [i["weekly"] for i in items])
        self.assertEqual(
            {"finished": NOW - 600, "duration": 60, "ok": True, "status": "完成。"},
            items[0]["last"],
        )
        self.assertFalse(items[1]["last"]["ok"])
        self.assertIsNone(items[2]["last"])

        def beijing(ts):
            return datetime.fromtimestamp(ts, tz=BEIJING_TZ).strftime("%m-%d %H:%M %a")

        self.assertEqual(
            {
                "daily": "10-23 04:00 Fri",
                "collection_quota": "10-23 08:00 Fri",
                "collection_week": "10-26 08:00 Mon",
            },
            {key: beijing(value) for key, value in refresh_times(NOW).items()},
        )


class RecentRunsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name, "history.json")

    def test_newest_first_per_task_and_bounded(self):
        store = RunHistoryStore(str(self.path))
        hunt, gacha = _child("快速狩猎"), _child("白嫖抽抽乐")
        _record(store, hunt, NOW - 300)
        _record(store, gacha, NOW - 200)
        _record(store, hunt, NOW - 100, status="快速狩猎中止。", Error="未找到目标", 失败="狩猎")

        runs = store.recent_runs("快速狩猎")
        self.assertEqual([NOW - 100, NOW - 300], [run["finished"] for run in runs])
        self.assertEqual([False, True], [run["ok"] for run in runs])
        self.assertEqual("快速狩猎中止。", runs[0]["status"])
        self.assertEqual(NOW - 160, runs[0]["started"])
        self.assertEqual(60, runs[0]["duration"])
        self.assertEqual({"Error": "未找到目标", "失败": "狩猎"}, runs[0]["details"])
        self.assertEqual(
            runs[0]["details"], RunHistoryStore(str(self.path)).recent_runs()[0]["details"]
        )
        newest = store.recent_runs(limit=2)
        self.assertEqual(["快速狩猎", "白嫖抽抽乐"], [run["task"] for run in newest])
        runs[0]["status"] = "changed"
        self.assertEqual("快速狩猎中止。", store.recent_runs("快速狩猎")[0]["status"])

        for index in range(RECENT_LIMIT + 5):
            _record(store, gacha, NOW + index)
        reloaded = RunHistoryStore(str(self.path))
        self.assertEqual(RECENT_LIMIT, len(reloaded.recent_runs(limit=RECENT_LIMIT * 2)))
        self.assertEqual([], reloaded.recent_runs("快速狩猎"))
        self.assertEqual(NOW + RECENT_LIMIT + 4, reloaded.recent_runs()[0]["finished"])


class Bd2RoutesTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("ok.core.events.communicate.task.emit"))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = directory.name
        self.enterContext(
            patch("src.compat.startup.SCHEDULED_FAILURE_DIR", Path(directory.name) / "starts")
        )
        self.store = RunHistoryStore(f"{directory.name}/history.json")
        set_default_store(self.store)
        self.addCleanup(set_default_store, None)

        controller = SimpleNamespace(start=Mock(), starting=False)
        self.facade = SimpleNamespace(start_controller=controller)
        # adapt_runtime wraps the controller's start; keep the underlying one.
        self.controller_start = self.facade.start_controller.start
        self.executor = make_executor()
        self.batch = self._task(DailyBatchTask, "一键完成日常", {"启用": True})
        self.batch.child_tasks = ()
        self.batch.request_run_mode = Mock()
        self.hunt = self._task(
            BaseTask, "快速狩猎", {"启用": True, "识别阈值": 0.7, "等待秒数": 5.0, "_私有": 1}
        )
        self.executor.get_task_by_class = lambda cls: next(
            (task for task in self.executor.onetime_tasks if isinstance(task, cls)), None
        )
        self.legacy = None
        engine = SimpleNamespace(
            config={},
            task_executor=self.executor,
            headless_app=self.facade,
            get_task=lambda name: (
                next(task for task in self.executor.onetime_tasks if task.name == name),
                False,
            ),
            start_runtime=Mock(),
            quit=Mock(),
            exit_event=threading.Event(),
        )
        self.app = create_app(
            {},
            token=TOKEN,
            origin=ORIGIN,
            ok_instance=engine,
            ui_dist=self.folder,
            legacy_check=lambda: self.legacy,
        )
        self.client = self.enterContext(TestClient(self.app, base_url=ORIGIN))
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    def _task(self, task_class, name, defaults):
        task = task_class.__new__(task_class)
        BaseTask.__init__(task, executor=self.executor, app=self.facade)
        task.name = name
        task.default_config = dict(defaults)
        task.config = Config(name, task.default_config, folder=self.folder)
        self.executor.onetime_tasks.append(task)
        return task

    def post_start(self, mode):
        return self.client.post("/api/bd2/daily/start", json={"mode": mode}, headers=self.headers)

    def test_inventory_report_accepts_optional_day_and_reports_storage_errors(self):
        route = "/api/bd2/trade-inventory"
        with patch("src.desktop.app.read_inventory_report") as read:
            for day in (None, "2026-11-24"):
                report = {"days": [], "day": day, "comparison_day": None, "items": []}
                read.return_value = report
                response = self.client.get(
                    route, params={"day": day} if day else {}, headers=self.headers,
                )
                self.assertEqual(200, response.status_code)
                self.assertEqual(report, response.json())
                read.assert_called_with(day)
            read.reset_mock()
            invalid = self.client.get(route, params={"day": "bad"}, headers=self.headers)
            self.assertEqual(422, invalid.status_code)
            read.assert_not_called()
            read.side_effect = OSError("inventory unavailable")
            self.assertEqual(503, self.client.get(route, headers=self.headers).status_code)

    def test_task_start_refuses_other_active_or_pending_tasks(self):
        for phase in ("running", "paused", "queued", "login"):
            with self.subTest(phase=phase):
                self.executor.current_task = self.batch if phase in ("running", "paused") else None
                self.executor.paused = phase == "paused"
                self.batch._enabled = phase == "queued"
                self.batch._start_after_login = phase == "login"
                self.controller_start.reset_mock()
                response = self.client.post(
                    f"/api/tasks/{self.hunt.name}/start", headers=self.headers
                )
                self.assertEqual(400, response.status_code, response.text)
                self.assertIn("已有任务", response.json()["detail"])
                self.controller_start.assert_not_called()
                self.assertFalse(self.hunt.enabled)

    def tasks(self):
        response = self.client.get("/api/tasks", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        return {task["name"]: task for task in response.json()}

    def plan_task(self):
        task = self._task(
            QuickHuntTask,
            "方案任务",
            {
                PLAN_KEY: "",
                "快速狩猎狩猎场": True,
                "快速狩猎冒险航线": True,
                "快速狩猎双倍策略": "强制双倍",
                "快速狩猎资源倾向": "经验",
                "其他设置": 7,
            },
        )
        self.batch.child_tasks = (DailyBatchChild("狩猎", QuickHuntTask),)
        self.batch.lock = threading.RLock()
        return task

    def plan_read(self, task):
        response = self.client.get(f"/api/bd2/hunt-plans/{task.name}", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def plan_save(self, task, book, revision):
        return self.client.post(
            f"/api/bd2/hunt-plans/{task.name}",
            json={"book": book, "revision": revision},
            headers=self.headers,
        )

    def test_plan_save_round_trips_to_qt_format_and_write_failure_preserves_config(self):
        task = self.plan_task()
        data = self.plan_read(task)
        book = {"version": 1, "active": 1, "plans": preset_plans()[:2]}
        book["plans"][1]["reserve"] = 12
        response = self.plan_save(task, book, data["revision"])
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(task.name, response.json()["name"])
        reloaded = Config(task.name, task.default_config, folder=self.folder)
        self.assertEqual(book, load_book(reloaded))
        self.assertEqual(7, reloaded["其他设置"])
        before = Path(task.config.config_file).read_bytes()
        data = self.plan_read(task)
        book["plans"][1]["name"] = "尚未保存的改名"
        with patch("src.desktop.config_store.os.replace", side_effect=OSError("disk full")):
            response = self.plan_save(task, book, data["revision"])
        self.assertEqual(400, response.status_code)
        self.assertEqual(before, Path(task.config.config_file).read_bytes())
        self.assertEqual(data["book"], load_book(task.config))

        data = self.plan_read(task)
        task.config["快速狩猎资源倾向"] = "金币"
        before = Path(task.config.config_file).read_bytes()
        self.assertEqual(409, self.plan_save(task, data["book"], data["revision"]).status_code)
        self.assertEqual(before, Path(task.config.config_file).read_bytes())
        task.config[PLAN_KEY] = "{broken"
        before = Path(task.config.config_file).read_bytes()
        broken = self.plan_read(task)
        self.assertIsNone(broken["book"])
        self.assertTrue(broken["load_error"])
        self.assertEqual(before, Path(task.config.config_file).read_bytes())
        self.assertEqual(200, self.plan_save(task, data["book"], broken["revision"]).status_code)


HEADERS = {"Authorization": f"Bearer {TOKEN}"}


GOOD = "from ok import BaseTask\n\nclass A(BaseTask):\n    pass\n"


def done_job(error=None, result=None):
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)
    return SimpleNamespace(future=future)


class FakeTask:
    enabled = False
    running = False
    paused = False

    def __init__(self, name):
        self.name = name


class FakeManager:
    """ok-script's custom task manager: files load into tasks, broken ones into task_errors."""

    has_custom = True

    def __init__(self, folder, executor):
        self.task_folder = str(folder)
        self.task_map = {}
        self.task_errors = {}
        self.executor = executor
        self.reloaded = []
        self.deleted = []

    def _load(self, path):
        source = Path(path).read_text(encoding="utf-8")
        if "broken" in source:
            self.task_errors[path] = "invalid syntax (A.py, line 3)"
            raise SyntaxError(self.task_errors[path])
        self.task_errors.pop(path, None)
        task = next((item for item, data in self.task_map.items() if data[0] == path), None)
        if task is None:
            task = FakeTask(f"任务 {Path(path).stem}")
            self.task_map[task] = [path, ""]
        return task

    def load_single_user_task(self, path):
        try:
            return self._load(path)
        except SyntaxError:
            return None

    def reload_task_code(self, task):
        self.reloaded.append(task)
        try:
            return done_job(result=[self._load(self.task_map[task][0])])
        except SyntaxError as error:
            return done_job(error=error)

    def delete_task(self, task):
        path = self.task_map.pop(task)[0]
        self.deleted.append(path)
        os.remove(path)

    def load_import_folder(self, folder):
        self.imported = folder


class ScriptCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = self.root / "ok_tasks"
        self.folder.mkdir()
        executor = make_executor()
        executor.submit = lambda operation, name=None: self.run_now(operation)
        executor.global_config = SimpleNamespace(get_all_visible_configs=lambda: [])
        executor.feature_set = Mock()
        self.manager = FakeManager(self.folder, executor)
        self.starts = []
        controller = SimpleNamespace(
            start=lambda task=None, exit_after=False: self.starts.append(task), starting=False
        )
        self.capture = SimpleNamespace(connected=lambda: True, get_frame=lambda: None)
        self.engine = SimpleNamespace(
            config={"version": "0.1.1"},
            task_executor=executor,
            task_manager=self.manager,
            device_manager=SimpleNamespace(
                config={}, capture_method=self.capture, get_hwnd_name=lambda: "BrownDust II"
            ),
            headless_app=SimpleNamespace(start_controller=controller),
            start_runtime=Mock(),
            quit=Mock(),
            exit_event=threading.Event(),
            get_task=lambda name: (
                next(task for task in self.manager.task_map if task.name == name),
                None,
            ),
        )
        self.controller = controller
        self.enterContext(patch.object(Config, "config_folder", str(self.root / "configs")))
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.app = create_app(
            {"version": "0.1.1"},
            token=TOKEN,
            origin=ORIGIN,
            ok_instance=self.engine,
            ui_dist=self.root,
        )
        self.runtime = self.app.state.runtime
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def run_now(self, operation):
        try:
            return done_job(result=operation())
        except Exception as error:
            return done_job(error=error)

    def post(self, path, body=None):
        return self.client.post(path, json=body or {}, headers=HEADERS)

    def get(self, path):
        return self.client.get(path, headers=HEADERS)

    def script(self, name="A", code=GOOD):
        path = self.folder / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        self.manager.load_single_user_task(str(path))
        return path


class ScriptRoutesTest(ScriptCase):
    def test_create_copy_delete(self):
        for body, reason in (
            ({"class_name": "1bad", "task_name": "x"}, "类名"),
            ({"class_name": "../evil", "task_name": "x"}, "类名"),
            ({"class_name": "Good", "task_name": " "}, "任务名称"),
        ):
            self.assertIn(reason, self.post("/api/scripts", body).json()["detail"])
        created = self.post(
            "/api/scripts", {"class_name": "New", "task_name": "新任务", "description": "说明"}
        ).json()
        self.assertEqual(created["name"], "New.py")
        self.assertIn("self.name = '新任务'", created["code"])
        self.assertIn(
            "已有同名",
            self.post("/api/scripts", {"class_name": "New", "task_name": "x"}).json()["detail"],
        )

        copied = self.post("/api/scripts/New.py/copy").json()
        self.assertEqual(copied["name"], "New_copy.py")
        self.assertIn("self.name = '新任务_copy'", copied["code"])
        self.assertEqual(self.post("/api/scripts/New.py/copy").json()["name"], "New_copy_1.py")

        self.assertEqual(self.post("/api/scripts/New.py/delete").json(), {"deleted": "New.py"})
        self.assertFalse((self.folder / "New.py").exists())
        self.assertEqual(self.get("/api/scripts/New.py").status_code, 400)

    def import_package(self, name="pack.okscript", content=b"zip"):
        return self.client.post(
            f"/api/bd2/scripts/import?name={name}",
            content=content,
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
        )

    def test_import_installs_a_new_package_or_waits_for_a_restart(self):
        with patch(
            "ok.core.plugin_staging.install_or_stage_plugin",
            return_value={"folder": "f", "restart_required": False},
        ):
            self.assertEqual(self.import_package().json(), {"restart_required": False})
        self.assertEqual(self.manager.imported, "f")
        self.manager.imported = None
        with patch(
            "ok.core.plugin_staging.install_or_stage_plugin",
            return_value={"folder": "f", "restart_required": True},
        ):
            self.assertEqual(self.import_package().json(), {"restart_required": True})
        self.assertIsNone(self.manager.imported)

    def test_recording_listens_to_the_mouse_only(self):
        self.engine.device_manager.config["capture"] = "ChildSession"
        self.assertEqual(400, self.post("/api/scripts-record/start").status_code)
        self.engine.device_manager.config.clear()
        listener = Mock()
        mouse = SimpleNamespace(
            Listener=Mock(return_value=listener),
            Button=SimpleNamespace(left="l", right="r", middle="m"),
        )
        keyboard = SimpleNamespace(Listener=Mock())
        pynput = ModuleType("pynput")
        pynput.mouse = mouse
        pynput.keyboard = keyboard
        modules = {"pynput": pynput, "pynput.mouse": mouse, "pynput.keyboard": keyboard}
        modules["ok.ui.qt"] = None
        window = SimpleNamespace(
            hwnd=123,
            x=100,
            y=200,
            real_x_offset=10,
            real_y_offset=20,
            real_width=1280,
            real_height=720,
        )
        now = [0.0]
        with (
            patch.dict(sys.modules, modules),
            patch("ok.og.device_manager", SimpleNamespace(hwnd_window=window), create=True),
            patch("win32gui.GetForegroundWindow", return_value=456) as foreground,
            patch("src.desktop.scripts.time.monotonic", side_effect=lambda: now[0]),
        ):
            state = self.post("/api/scripts-record/start").json()
            self.assertTrue(state["recording"])
            self.assertFalse(state["active"])
            self.assertEqual(state["target"], "BrownDust II")
            keyboard.Listener.assert_not_called()
            listener.start.assert_called_once()
            recorder = mouse.Listener.call_args.kwargs["on_click"].__self__
            foreground.return_value = 123
            recorder.on_window(True)
            self.assertTrue(self.get("/api/bd2/scripts/recording").json()["active"])
            recorder.on_click(750, 400, "l", True)
            now[0] = 0.1
            recorder.on_click(750, 400, "l", False)
            recorder.on_click(50, 50, "l", True)
            recorder.on_click(50, 50, "l", False)
            self.assertEqual(self.get("/api/bd2/scripts/recording").json()["clicks"], 1)
            recorder.on_click(750, 400, "l", True)
            recorder.on_window(False)
            now[0] = 10.1
            recorder.on_window(True)
            recorder.on_click(750, 400, "l", False)
            source = "class A:\n    def run(self):\n        pass\n"
            stopped = self.post(
                "/api/scripts-record/stop", {"code": source, "loop": "count", "count": 2}
            ).json()
        self.assertFalse(stopped["recording"])
        self.assertIn(
            "for _ in range(2):\n            self.click_relative(0.500000, 0.250000,",
            stopped["code"],
        )
        self.assertIn("down_time=0.100", stopped["code"])
        self.assertEqual(1, stopped["code"].count("self.click_relative"))
        listener.stop.assert_called_once()
        self.assertEqual(self.post("/api/scripts-record/stop", {}).status_code, 400)


class TemplateRoutesTest(ScriptCase):
    def setUp(self):
        super().setUp()
        self.store = CocoTemplateStore(self.root / "ok_templates")
        self.runtime._template_store = self.store
        self.image = self.store.folder / "0.png"
        cv2.imwrite(str(self.image), np.zeros((20, 40, 3), dtype=np.uint8))

    def test_template_save_accepts_only_the_scripts_destination_outside_debug(self):
        self.post(
            "/api/templates/0.png/annotations",
            {"annotations": [{"category": "ok", "bbox": [1, 2, 3, 4]}]},
        )
        for body, reason in (
            ({"destination": "assets"}, "调试模式"),
            ({"generate_label_enum": True, "enum_path": "src/Labels"}, "ok_tasks"),
            ({"generate_label_enum": True, "enum_path": "../evil"}, "相对路径无效"),
        ):
            self.assertIn(reason, self.post("/api/bd2/templates/save", body).json()["detail"])
        self.assertFalse((self.root / "src").exists())
        process = self.engine.task_executor.feature_set.process_data
        process.assert_not_called()
        result = self.post(
            "/api/bd2/templates/save", {"format": "split", "destination": "tasks"}
        ).json()
        folder = Path(result["folder"])
        self.assertEqual(folder, (self.root / "ok_tasks" / "assets" / "templates").resolve())
        self.assertEqual(len(list(folder.glob("ok*.png"))), 1)
        process.assert_called_once()
