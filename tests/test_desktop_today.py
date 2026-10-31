"""Today's progress, the recent-runs list and the BD2 routes the desktop client reads."""

import json
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from ok import BaseTask, Config
from ok.ui.web import app as framework

from src.desktop.app import adapt_runtime, create_app
from src.task_guides import GUIDES
from src.tasks.DailyBatchTask import (
    RUN_MODE_ALL,
    RUN_MODE_INCOMPLETE,
    DailyBatchChild,
    DailyBatchTask,
)
from src.tasks.quick_hunt_plan import PLAN_KEY, load_book, preset_plans
from src.tasks.QuickHuntTask import QuickHuntTask
from src.tasks.run_history import (
    BEIJING_TZ,
    RECENT_LIMIT,
    RunHistoryStore,
    day_start_ts,
    set_default_store,
    week_start_ts,
)
from src.tasks.today import batch_items, completion_state, refresh_times, today_summary
from tests.test_desktop_app import ORIGIN, TOKEN, make_executor

# Thursday 2026-10-22 10:00 in Beijing: after the 04:00 and 08:00 refreshes.
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
    children = {_Daily: _child("公会、小屋、酒馆"), _Hunt: _child("快速狩猎"),
                _Collection: _child("每周跑图")}
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

    def test_changed_configuration_no_longer_counts_as_done(self):
        executor, children = _batch_executor()
        _record(self.store, children[_Daily], NOW - 600)
        children[_Daily].config = {"启用": True, "选项": 2}

        self.assertFalse(batch_items(executor, self.store, NOW)[0]["done"])

    def test_weekly_collection_tells_quota_from_week(self):
        task = _child("每周跑图")
        quota_cycle = datetime.fromtimestamp(
            day_start_ts(NOW, refresh_hour=8), tz=BEIJING_TZ).date().isoformat()
        week_cycle = datetime.fromtimestamp(
            week_start_ts(NOW, refresh_hour=8), tz=BEIJING_TZ).date().isoformat()

        self.assertIsNone(completion_state(task, self.store, NOW))
        _record(self.store, task, NOW - 600, 跑图完成范围="今日", 跑图周期=quota_cycle)
        self.assertEqual("quota", completion_state(task, self.store, NOW))
        _record(self.store, task, NOW - 300, 跑图完成范围="本周", 跑图周期=week_cycle)
        self.assertEqual("week", completion_state(task, self.store, NOW))

    def test_summary_carries_switches_skip_reasons_and_refresh_times(self):
        executor, _children = _batch_executor({"完成日常后自动关机": True})

        summary = today_summary(executor, self.store, NOW)

        self.assertEqual(
            {"name": "一键完成日常", "enabled": True, "auto_start": False, "shutdown": True},
            summary["batch"],
        )
        self.assertEqual({"狩猎": {"reason": "not_due", "minutes": 25}}, summary["skipped"])
        self.assertEqual(3, len(summary["items"]))
        self.assertEqual(refresh_times(NOW), summary["refresh"])
        json.dumps(summary)

    def test_refresh_times_follow_beijing_anchors(self):
        def beijing(ts):
            return datetime.fromtimestamp(ts, tz=BEIJING_TZ).strftime("%m-%d %H:%M %a")

        self.assertEqual(
            {"daily": "10-23 04:00 Fri", "collection_quota": "10-23 08:00 Fri",
             "collection_week": "10-26 08:00 Mon"},
            {key: beijing(value) for key, value in refresh_times(NOW).items()},
        )

    def test_without_the_batch_task(self):
        executor = SimpleNamespace(onetime_tasks=[], get_task_by_class=lambda cls: None)

        summary = today_summary(executor, self.store, NOW)

        self.assertIsNone(summary["batch"])
        self.assertEqual([], summary["items"])
        self.assertEqual({}, summary["skipped"])


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
            runs[0]["details"], RunHistoryStore(str(self.path)).recent_runs()[0]["details"])
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

    def test_file_written_before_the_list_existed(self):
        self.path.write_text(json.dumps({
            "version": 1,
            "tasks": {"快速狩猎": {"finished": NOW, "ok": True}},
            "stages": {},
        }), encoding="utf-8")
        store = RunHistoryStore(str(self.path))
        self.assertEqual([], store.recent_runs())
        self.assertTrue(store.is_completed_today("快速狩猎", now=NOW + 1))

        _record(store, _child("白嫖抽抽乐"), NOW + 5)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(1, saved["version"])
        self.assertEqual(["白嫖抽抽乐"], [run["task"] for run in saved["recent"]])
        self.assertIn("快速狩猎", saved["tasks"])

    def test_malformed_entries_are_dropped(self):
        self.path.write_text(json.dumps({
            "version": 1, "tasks": {}, "stages": {},
            "recent": [{"task": "a", "finished": NOW}, {"task": 3, "finished": NOW},
                       {"task": "b"}, "x"],
        }), encoding="utf-8")
        runs = RunHistoryStore(str(self.path)).recent_runs()
        self.assertEqual(["a"], [run["task"] for run in runs])


class Bd2RoutesTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("ok.core.events.communicate.task.emit"))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = directory.name
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
        self.hunt = self._task(BaseTask, "快速狩猎", {
            "启用": True, "识别阈值": 0.7, "等待秒数": 5.0, "_私有": 1})
        self.executor.get_task_by_class = lambda cls: next(
            (task for task in self.executor.onetime_tasks if isinstance(task, cls)), None)
        self.legacy = None
        engine = SimpleNamespace(
            config={},
            task_executor=self.executor,
            headless_app=self.facade,
            get_task=lambda name: (
                next(task for task in self.executor.onetime_tasks if task.name == name), False),
            start_runtime=Mock(),
            quit=Mock(),
            exit_event=threading.Event(),
        )
        self.app = create_app(
            {}, token=TOKEN, origin=ORIGIN, ok_instance=engine, ui_dist=self.folder,
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

    def test_daily_start_requests_the_mode_then_starts_the_batch(self):
        for mode in (RUN_MODE_INCOMPLETE, RUN_MODE_ALL):
            with self.subTest(mode=mode):
                self.batch.request_run_mode.reset_mock()
                self.controller_start.reset_mock()
                response = self.post_start(mode)
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual("一键完成日常", response.json()["name"])
                self.batch.request_run_mode.assert_called_once_with(mode)
                self.controller_start.assert_called_once()
                self.assertIs(self.batch, self.controller_start.call_args.args[0])

    def test_daily_start_refuses_instead_of_queueing(self):
        cases = {
            "unknown mode": (lambda: None, "bogus", "未知的运行方式"),
            "another task runs": (
                lambda: setattr(self.executor, "current_task", self.hunt), RUN_MODE_ALL, "已有任务",
            ),
            "batch queued": (
                lambda: setattr(self.batch, "_enabled", True), RUN_MODE_ALL, "已有任务",
            ),
            "another task queued": (
                lambda: setattr(self.hunt, "_enabled", True), RUN_MODE_ALL, "已有任务",
            ),
            "waiting for login": (
                lambda: setattr(self.batch, "_start_after_login", True), RUN_MODE_ALL, "已有任务"),
            "game starting": (
                lambda: setattr(self.facade.start_controller, "starting", True), RUN_MODE_ALL,
                "正在启动游戏"),
            "older ok-bd2": (lambda: setattr(self, "legacy", "旧版正在运行"), RUN_MODE_ALL, "旧版"),
        }
        for name, (arrange, mode, message) in cases.items():
            with self.subTest(name):
                self.executor.current_task = None
                self.batch._enabled = False
                self.hunt._enabled = False
                self.batch._start_after_login = False
                self.facade.start_controller.starting = False
                self.legacy = None
                self.batch.request_run_mode.reset_mock()
                self.controller_start.reset_mock()
                arrange()
                response = self.post_start(mode)
                self.assertEqual(400, response.status_code, response.text)
                self.assertIn(message, response.json()["detail"])
                self.batch.request_run_mode.assert_not_called()
                self.controller_start.assert_not_called()

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

    def test_task_start_can_resume_the_current_task(self):
        self.executor.current_task = self.hunt
        self.hunt._enabled = True
        self.hunt._paused = True
        response = self.client.post(f"/api/tasks/{self.hunt.name}/start", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        self.controller_start.assert_called_once_with(self.hunt, exit_after=False)

    def test_today_route(self):
        response = self.client.get("/api/bd2/today", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual("一键完成日常", body["batch"]["name"])
        self.assertEqual([], body["items"])
        self.assertEqual({"daily", "collection_quota", "collection_week"}, set(body["refresh"]))

    def test_history_route_filters_and_clamps(self):
        hunt, gacha = _child("快速狩猎"), _child("白嫖抽抽乐")
        for index in range(3):
            _record(self.store, hunt, NOW + index)
        _record(self.store, gacha, NOW + 10)

        def get(**params):
            response = self.client.get("/api/bd2/history", params=params, headers=self.headers)
            self.assertEqual(200, response.status_code, response.text)
            return response.json()

        self.assertEqual(4, len(get(task="", limit=300)))

        hunts = get(task="快速狩猎", limit=2)
        self.assertEqual([NOW + 2, NOW + 1], [run["finished"] for run in hunts])
        self.assertEqual(1, len(get(limit=0)))
        self.assertEqual(4, len(get(limit=1000)))
        self.assertEqual("白嫖抽抽乐", get()[0]["task"])

    def tasks(self):
        response = self.client.get("/api/tasks", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        return {task["name"]: task for task in response.json()}

    def test_task_payload_counts_hidden_maintenance_settings(self):
        hunt = self.tasks()["快速狩猎"]
        self.assertEqual(2, hunt["hidden_config_count"])
        self.assertEqual(["启用"], [field["key"] for field in hunt["config"]])

    def test_task_payload_carries_the_run_state_and_guide(self):
        self.assertEqual("done", self.tasks()["快速狩猎"]["run_state"])
        self.hunt._enabled = True
        self.assertEqual("run", self.tasks()["快速狩猎"]["run_state"])
        self.hunt._enabled = False
        self.hunt.info = {"状态": "快速狩猎中止。"}
        self.assertEqual("abort", self.tasks()["快速狩猎"]["run_state"])
        self.assertIsNone(self.tasks()["快速狩猎"]["guide"])
        guided = self._task(QuickHuntTask, "带说明", {"启用": True, PLAN_KEY: ""})
        payload = self.tasks()[guided.name]
        self.assertEqual(GUIDES["QuickHuntTask"][2], payload["guide"])
        # The hunt plan is JSON from a dedicated editor; the client must not offer a text box.
        fields = {field["key"]: field for field in payload["config"]}
        self.assertEqual("hunt_plan", fields[PLAN_KEY]["editor"])
        self.assertNotIn("editor", fields["启用"])

    def test_routes_need_the_launch_capability(self):
        for path in ("/api/bd2/today", "/api/bd2/history"):
            with self.subTest(path=path):
                self.assertEqual(403, self.client.get(path).status_code)
        response = self.client.post("/api/bd2/daily/start", json={"mode": "all"})
        self.assertEqual(403, response.status_code)

    def plan_task(self):
        task = self._task(QuickHuntTask, "方案任务", {
            PLAN_KEY: "", "快速狩猎狩猎场": True, "快速狩猎冒险航线": True,
            "快速狩猎双倍策略": "强制双倍", "快速狩猎资源倾向": "经验", "其他设置": 7,
        })
        self.batch.child_tasks = (DailyBatchChild("狩猎", QuickHuntTask),)
        self.batch.lock = threading.RLock()
        return task

    def plan_read(self, task):
        response = self.client.get(f"/api/bd2/hunt-plans/{task.name}", headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def plan_save(self, task, book, revision):
        return self.client.post(f"/api/bd2/hunt-plans/{task.name}",
                                json={"book": book, "revision": revision}, headers=self.headers)

    def test_plan_read_and_preview_preserve_legacy_config_and_never_start_tasks(self):
        task = self.plan_task()
        before = Path(task.config.config_file).read_bytes()
        data = self.plan_read(task)
        self.assertTrue(data["inherited"])
        self.assertEqual(load_book(task.config), data["book"])
        self.assertEqual("exp", data["book"]["plans"][0]["rules"][0]["condition"])
        body = {"book": data["book"], "rice": 90, "states": {"金币": False, "经验": True}}
        path = f"/api/bd2/hunt-plans/{task.name}/preview"
        response = self.client.post(path, json=body, headers=self.headers)
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn("经验 / 史莱姆", response.json()["text"])
        self.assertTrue(response.json()["runnable"])
        body["states"]["经验"] = None
        response = self.client.post(path, json=body, headers=self.headers)
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["runnable"])
        for invalid in ({"rice": True}, {"rice": -1}, {"states": {"金币": "yes"}}):
            response = self.client.post(path, json={**body, **invalid}, headers=self.headers)
            self.assertEqual(400, response.status_code)
        self.assertEqual(before, Path(task.config.config_file).read_bytes())
        self.controller_start.assert_not_called()

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

    def test_invalid_plan_cannot_be_saved_through_either_config_endpoint(self):
        task = self.plan_task()
        data = self.plan_read(task)
        before = Path(task.config.config_file).read_bytes()
        for change in (
            lambda b: b.update(version=2),
            lambda b: b.update(active=99),
            lambda b: b["plans"][0].update(name=" "),
            lambda b: b["plans"][0].update(reserve=-1),
            lambda b: b["plans"][0]["rules"][0].update(condition="always"),
            lambda b: b["plans"][0]["fallback"].append(
                {"target": "gold", "mode": "count", "count": 0}),
            lambda b: b["plans"][0]["rules"][0]["steps"][0].update(target=[]),
        ):
            book = deepcopy(data["book"])
            change(book)
            self.assertEqual(400, self.plan_save(task, book, data["revision"]).status_code)
        response = self.client.post(f"/api/tasks/{task.name}/config", headers=self.headers,
                                    json={"key": PLAN_KEY, "value": '{"version":2}'})
        self.assertEqual(400, response.status_code)
        self.assertEqual(before, Path(task.config.config_file).read_bytes())

    def test_plan_writes_obey_execution_and_diagnostic_guards(self):
        task = self.plan_task()
        data = self.plan_read(task)
        before = Path(task.config.config_file).read_bytes()
        for target, key in ((task, "_enabled"), (self.batch, "_enabled"),
                            (self.batch, "_start_after_login"),
                            (self.facade.start_controller, "starting"),
                            (self.facade, "diagnostic_pause"),
                            (self.facade, "window_resize_in_progress")):
            with self.subTest(key=key), patch.object(target, key, True, create=True):
                self.assertTrue(self.plan_read(task)["busy"])
                response = self.plan_save(task, data["book"], data["revision"])
                self.assertEqual(400, response.status_code)
        self.assertEqual(before, Path(task.config.config_file).read_bytes())

    def test_changed_or_corrupt_plan_requires_explicit_matching_revision(self):
        task = self.plan_task()
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

    def test_plan_routes_require_authentication_and_a_supported_task(self):
        task = self.plan_task()
        path = f"/api/bd2/hunt-plans/{task.name}"
        self.assertEqual(403, self.client.get(path).status_code)
        self.assertEqual(403, self.client.post(path, json={}).status_code)
        for name, code in (("missing", 404), (self.hunt.name, 400)):
            response = self.client.get(f"/api/bd2/hunt-plans/{name}", headers=self.headers)
            self.assertEqual(code, response.status_code)

    def test_serves_the_bundled_font(self):
        response = self.client.get("/fonts/MiSans-Regular.otf", headers=self.headers)
        self.assertEqual(200, response.status_code)
        self.assertGreater(len(response.content), 1_000_000)


class AdaptRuntimeTest(unittest.TestCase):
    def test_start_daily_is_exposed_on_the_runtime(self):
        executor = make_executor()
        executor.get_task_by_class = lambda cls: None
        facade = SimpleNamespace(start_controller=SimpleNamespace(start=Mock(), starting=False))
        engine = SimpleNamespace(
            config={}, task_executor=executor, headless_app=facade,
            get_task=lambda name: None, start_runtime=Mock(), quit=Mock(),
        )
        runtime = framework.create_web_app({}, ok_instance=engine).state.runtime
        adapt_runtime(runtime)
        with self.assertRaisesRegex(ValueError, "没有一键完成日常"):
            runtime.start_daily(RUN_MODE_ALL)


if __name__ == "__main__":
    unittest.main()
