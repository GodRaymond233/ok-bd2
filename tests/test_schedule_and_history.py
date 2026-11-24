import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from src.tasks import auto_scheduler, run_history, scheduler
from src.tasks.DailyBatchTask import DailyBatchChild, DailyBatchTask
from src.tasks.run_history import (
    BEIJING_TZ,
    RunHistoryStore,
    configuration_scope,
    day_start_ts,
    pvp_settlement_end_ts,
    record_finished_task,
    week_start_ts,
)
from src.tasks.trigger.AutoLoginTask import AutoLoginTask


def beijing(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=BEIJING_TZ).timestamp()


def task_record(name, info=None, **kwargs):
    return SimpleNamespace(name=name, info=info or {}, start_time=1000.0, **kwargs)


class ScheduleHistoryTest(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = directory / "history.json"
        self.schedule_path = directory / "schedule.json"
        self.store = RunHistoryStore(self.path)
        self.schedule = scheduler.TaskScheduleStore(self.schedule_path)
        self.enterContext(patch.object(run_history, "default_store", return_value=self.store))
        self.enterContext(patch.object(scheduler, "default_store", return_value=self.schedule))

    def test_run_outcome_classification(self):
        for name, info, ok in (
            ("每日跑商", {"状态": "跑商完成。", "Error": "boom"}, False),
            ("公会、小屋、酒馆", {"状态": "结束。", "失败": "['公会签到']"}, False),
            ("每日跑商", {}, False),
            ("刷压制等级", {"状态": "刷压制等级已停止。"}, False),
            ("地图探针", {"状态": "未执行：测试卡带起止范围无效"}, False),
            ("一键完成日常", {"状态": "一键完成日常中止。"}, False),
            ("每日跑商", {"状态": "跑商部分流程未完成。", "失败": "跑商买入"}, False),
            ("刷砍价等级", {"状态": "页面文字确认超时，任务结束。", "结果": "失败"}, False),
            (
                "每日跑商",
                {"状态": "跑商完成。", "完成": "跑商买入", "失败": "-", "跳过": "-"},
                True,
            ),
            ("公会、小屋、酒馆", {"状态": "结束。", "完成": "3", "失败": "0", "跳过": "0"}, True),
        ):
            with self.subTest(task=name, info=info):
                self.store.record_task_done(task_record(name, info), finished=beijing(18, 9))
                self.assertEqual(ok, self.store.last_run(name)["ok"])

    def test_pending_stage_survives_failed_writes_and_corrupt_files_refuse_spending(self):
        self.store.save_stage("狩猎", "MIN", "pending", "today", {"cost": 6})
        original = self.path.read_bytes()
        pending = self.store.stage("狩猎", "MIN")
        self.assertEqual(("pending", {"cost": 6}), (pending["state"], pending["evidence"]))
        with patch.object(run_history, "write_json_file", side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError):
                self.store.save_stage("狩猎", "MIN", "done", "today")
        self.assertEqual(original, self.path.read_bytes())
        for store in (self.store, RunHistoryStore(self.path)):
            self.assertEqual(pending, store.stage("狩猎", "MIN"))

        self.path.write_text("{broken", encoding="utf-8")
        broken = RunHistoryStore(self.path)
        with self.assertRaises(RuntimeError):
            broken.stage("料理", "牛排")
        with self.assertRaises(RuntimeError):
            broken.save_stage("料理", "牛排", "pending", "today")
        broken.record_task_done(task_record("日常", {"状态": "失败"}))
        self.assertEqual("{broken", self.path.read_text(encoding="utf-8"))

    def test_retire_pending_is_atomic_preserves_evidence_and_checks_snapshot(self):
        self.store.save_stage("狩猎", "米饭", "pending", "old", {"cost": 6})
        self.store.save_stage("狩猎", "火把", "pending", "today", {"cost": 1})
        original = self.store.stage("狩猎", "米饭")
        stale = {**original, "scope": "different"}
        with self.assertRaises(RuntimeError):
            self.store.retire_stages("狩猎", {"米饭": stale}, "expired")
        with patch("src.tasks.run_history.write_json_file", side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError):
                self.store.retire_stages("狩猎", {"米饭": original}, "expired")
        self.assertEqual(original, self.store.stage("狩猎", "米饭"))
        self.assertEqual(original, RunHistoryStore(self.path).stage("狩猎", "米饭"))
        self.store.retire_stages("狩猎", {"米饭": original}, "expired")
        reloaded = RunHistoryStore(self.path)
        self.assertEqual({}, reloaded.stage("狩猎", "米饭"))
        self.assertEqual("pending", reloaded.stage("狩猎", "火把")["state"])
        with open(self.path, encoding="utf-8") as stream:
            saved = json.load(stream)
        self.assertEqual(1, saved["version"])
        self.assertEqual(1, len(saved["retired_stages"]))
        self.assertEqual(original, saved["retired_stages"][0]["record"])
        self.assertEqual("expired", saved["retired_stages"][0]["reason"])

    def test_concurrent_records_keep_disk_and_memory_consistent(self):
        first_saving = threading.Event()
        second_at_write_boundary = threading.Event()
        release_first = threading.Event()
        writer_id = threading.local()
        record_lock = threading.RLock()

        class ObservedLock:
            def __enter__(self):
                if getattr(writer_id, "name", None) == "second":
                    second_at_write_boundary.set()
                record_lock.acquire()

            def __exit__(self, *_args):
                record_lock.release()

        self.store._lock = ObservedLock()

        def save(path, data):
            snapshot = json.dumps(data)
            if writer_id.name == "first":
                first_saving.set()
                if not release_first.wait(5):
                    raise TimeoutError("first writer was not released")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(snapshot)
            if writer_id.name == "second":
                second_at_write_boundary.set()

        def record(name):
            writer_id.name = name
            self.store.record_task_done(task_record(name), finished=2000, ok=True)

        with patch("src.tasks.run_history.write_json_file", side_effect=save):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(record, "first")
                try:
                    self.assertTrue(first_saving.wait(5))
                    second = executor.submit(record, "second")
                    self.assertTrue(second_at_write_boundary.wait(5))
                finally:
                    release_first.set()
                first.result(timeout=5)
                second.result(timeout=5)
        reloaded = RunHistoryStore(self.path)
        for name in ("first", "second"):
            self.assertIsNotNone(self.store.last_run(name))
            self.assertEqual(self.store.last_run(name), reloaded.last_run(name))

    def test_completion_expires_with_task_scope_and_beijing_refresh(self):
        task = task_record("公会、小屋、酒馆", {"状态": "完成"}, config={"执行小屋签到": False})
        finished = beijing(18, 3, 59)
        self.store.record_task_done(task, started=finished - 30, finished=finished, ok=True)
        scope = configuration_scope(task)
        self.assertTrue(self.store.is_completed_today(task.name, now=finished, scope=scope))
        task.config["执行小屋签到"] = True
        self.assertFalse(
            self.store.is_completed_today(task.name, now=finished, scope=configuration_scope(task))
        )
        self.store.record_task_done(task_record("镜中之战"), finished=finished + 120, ok=False)
        batch = task_record(
            "一键完成日常", {"状态": "中止", "完成": task.name, "失败": "自动PVP", "跳过": "跑商"}
        )
        self.store.record_task_done(batch, finished=beijing(18, 9))
        record = self.store.last_run(task.name)
        self.assertEqual(
            (True, finished, 30), (record["ok"], record["finished"], record["duration"])
        )
        self.assertFalse(self.store.last_run("镜中之战")["ok"])
        self.assertIsNone(self.store.last_run("每日跑商"))
        self.store.record_task_done(task, finished=finished + 10, ok=False)
        reloaded = RunHistoryStore(self.path)
        self.assertTrue(reloaded.is_completed_today(task.name, now=finished + 15, scope=scope))
        self.assertFalse(reloaded.last_run(task.name)["ok"])
        self.assertFalse(reloaded.is_completed_today(task.name, now=beijing(18, 4), scope=scope))
        self.assertEqual(beijing(17, 4), day_start_ts(finished))
        self.assertEqual(beijing(17, 4), week_start_ts(beijing(19, 12)))

    def test_schedule_anchors_use_the_completed_cycle_and_period(self):
        for now, next_day in ((beijing(19, 3), 19), (beijing(19, 4), 20), (beijing(19, 9), 20)):
            with self.subTest(daily=now):
                self.assertEqual(
                    beijing(next_day, 4), self.schedule.delay_after_run("快速狩猎", True, now)
                )
                self.assertFalse(self.schedule.is_due("快速狩猎", now=beijing(next_day, 4) - 1))
                self.assertTrue(self.schedule.is_due("快速狩猎", now=beijing(next_day, 4)))
        for period, cycle, moment, due in (
            ("今日", "2026-08-19", beijing(19, 12), beijing(20, 8)),
            ("本周", "2026-08-17", beijing(19, 12), beijing(24, 8)),
            ("本周", "2026-08-17", beijing(24, 7, 59), beijing(24, 8)),
            ("本周", "2026-08-24", beijing(24, 8), beijing(31, 8)),
            ("今日", "2026-08-24", beijing(24, 8), beijing(25, 8)),
            ("本周", "2026-08-17", beijing(24, 12), beijing(24, 12)),
        ):
            with self.subTest(period=period, cycle=cycle, moment=moment):
                task = task_record(
                    "每周跑图", {"状态": "跑图完成。", "跑图完成范围": period, "跑图周期": cycle}
                )
                with patch("time.time", return_value=moment):
                    record_finished_task(task, True)
                self.assertEqual(due, self.schedule.next_run(task.name))

    def test_reloaded_schedule_honors_pvp_settlement_and_rejects_invalid_records(self):
        for sunday, monday in (
            ((2026, 11, 1), (2026, 11, 2)),
            ((2027, 1, 31), (2027, 2, 1)),
            ((2028, 12, 31), (2029, 1, 1)),
        ):
            closes = datetime(*sunday, 23, tzinfo=BEIJING_TZ).timestamp()
            opens = datetime(*monday, 8, tzinfo=BEIJING_TZ).timestamp()
            for now, expected in (
                (closes - 0.001, None),
                (closes, opens),
                (closes + 3600, opens),
                (opens - 0.001, opens),
                (opens, None),
                (opens + 3600, None),
            ):
                with self.subTest(now=now):
                    self.assertEqual(expected, pvp_settlement_end_ts(now))
            name = "镜中之战"
            task = task_record(name, {"状态": "完成"})
            self.store.record_task_done(task, finished=closes - 3600, ok=True)
            previous = self.store.last_run(name).copy()
            self.schedule.delay_after_run(name, True, now=closes - 3600, scope="old")
            self.assertFalse(self.schedule.is_due(name, now=closes, scope="changed"))
            task.info = {"状态": "赛季结算，PVP未执行，本次跳过。", "PVP 结算休战": True}
            self.store.record_task_done(task, finished=opens - 60, ok=True)
            self.assertEqual(previous, self.store.last_run(name))
            self.assertFalse(self.store.is_completed(name, now=opens))
            self.assertEqual(
                opens, self.schedule.delay_after_record(name, False, self.store, now=opens - 60)
            )
            reloaded = scheduler.TaskScheduleStore(self.schedule_path)
            self.assertFalse(reloaded.is_due(name, now=opens - 0.001))
            self.assertTrue(reloaded.is_due(name, now=opens))
            self.assertTrue(reloaded.is_due("快速狩猎", now=closes))
        for damaged in ("{broken", '{"version": 999, "tasks": {"快速狩猎": {"next_run": 1}}}'):
            with self.subTest(damaged=damaged):
                self.schedule_path.write_text(damaged, encoding="utf-8")
                restored = scheduler.TaskScheduleStore(self.schedule_path)
                self.assertIsNone(restored.next_run("快速狩猎"))
                self.assertTrue(restored.is_due("快速狩猎", now=opens))

    def test_due_batch_requires_idle_executor_login_and_eligible_child(self):
        now = beijing(19, 9)
        for case, expected_modes, started, accepted in (
            ("due", ["incomplete"], True, True),
            ("refused", ["incomplete", "all"], True, False),
            ("switch off", [], False, False),
            ("disabled batch", [], False, False),
            ("busy", [], False, False),
            ("pending login", [], False, False),
            ("disabled child", [], False, False),
            ("completed", [], False, False),
            ("new scope", ["incomplete"], True, True),
            ("backoff", [], False, False),
            ("backoff elapsed", ["incomplete"], True, True),
            ("disabled login", ["incomplete"], True, True),
        ):
            with self.subTest(case=case):
                directory = self.path.parent / case
                directory.mkdir()
                history = RunHistoryStore(directory / "history.json")
                schedule = scheduler.TaskScheduleStore(directory / "schedule.json")
                child = task_record("快速狩猎", config={"小屋": False})
                config = {
                    "启用": case != "disabled batch",
                    "启动自动执行日常": case != "switch off",
                    "狩猎": case != "disabled child",
                }
                batch = SimpleNamespace(
                    config=config,
                    request_run_mode=Mock(),
                    child_tasks=[DailyBatchChild("狩猎", type(child))],
                )
                tasks = {type(child): child, DailyBatchTask: batch}
                if case in {"disabled login", "pending login"}:
                    tasks[AutoLoginTask] = SimpleNamespace(login_pending=case == "pending login")
                executor = SimpleNamespace(
                    get_task_by_class=tasks.get, current_task=child if case == "busy" else None
                )
                start = Mock(return_value=case != "refused")
                og = SimpleNamespace(
                    executor=executor,
                    app=SimpleNamespace(start_controller=SimpleNamespace(start=start)),
                )
                if case in {"completed", "new scope"}:
                    history.record_task_done(child, finished=now, ok=True)
                    schedule.delay_after_record(child.name, True, history, now=now)
                    child.config["小屋"] = case == "new scope"
                if case.startswith("backoff"):
                    schedule.delay_after_run(
                        child.name, False, now=now - (1800 if case == "backoff elapsed" else 0)
                    )
                with (
                    patch.object(run_history, "default_store", return_value=history),
                    patch.object(
                        scheduler,
                        "default_store",
                        return_value=scheduler.TaskScheduleStore(schedule.path),
                    ),
                    patch("time.time", return_value=now),
                ):
                    result = auto_scheduler.run_due_tasks_once(og)
                self.assertEqual("一键完成日常（仅执行今日未完成）" if accepted else None, result)
                self.assertEqual([call(batch)] if started else [], start.call_args_list)
                self.assertEqual(
                    [call(mode) for mode in expected_modes], batch.request_run_mode.call_args_list
                )

    def test_startup_schedules_once_retries_until_accepted_and_stops_on_exit(self):
        from ok.core.events import EventBus, EventSignal
        from ok.util.handler import ExitEvent

        install = auto_scheduler.install_auto_scheduler
        handler, exit_event = Mock(), ExitEvent()
        # Local signals prevent callbacks registered by other suites from firing.
        events = EventBus()
        with (
            patch("ok.core.events.communicate", events),
            patch.object(auto_scheduler, "login_ready", EventSignal("login_ready")),
            patch.object(install, "_installed", False, create=True),
            patch.object(install, "_runner", None, create=True),
            patch.object(
                auto_scheduler, "run_due_tasks_once", side_effect=[None, "started"]
            ) as run,
        ):
            self.assertTrue(install(handler=handler, exit_event=exit_event))
            self.assertFalse(install(handler=handler))
            handler.post.assert_not_called()
            events.task_done.emit(None)
            events.start_success.emit()
            startup, recheck = handler.post.call_args_list
            self.assertEqual(auto_scheduler.INSTALL_DELAY_SECONDS, startup.kwargs["delay"])
            self.assertEqual(auto_scheduler.RECHECK_DELAY_SECONDS, recheck.kwargs["delay"])
            handler.post.reset_mock()
            startup.args[0]()
            handler.post.assert_called_once_with(
                startup.args[0], delay=auto_scheduler.STARTUP_RETRY_INTERVAL_SECONDS
            )
            handler.post.reset_mock()
            startup.args[0]()
            handler.post.assert_not_called()
            exit_event.set()
            recheck.args[0]()
            self.assertEqual(2, run.call_count)
