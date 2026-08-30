"""ALAS/NKAS 式 next_run 调度账本与启动自动执行的回归."""

import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

from src.tasks import auto_scheduler, scheduler
from src.tasks.DailyBatchTask import DailyBatchChild, DailyBatchTask
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.run_history import (
    BEIJING_TZ,
    RunHistoryStore,
    set_default_store,
)


def _beijing_ts(year, month, day, hour, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=BEIJING_TZ).timestamp()


class NextAnchorTest(unittest.TestCase):
    def test_next_daily_anchor(self):
        # 周三 09:00 -> 周四 04:00
        self.assertEqual(
            scheduler.next_daily_anchor_ts(_beijing_ts(2026, 8, 19, 9, 0)),
            _beijing_ts(2026, 8, 20, 4, 0),
        )
        # 03:00 -> 当天 04:00
        self.assertEqual(
            scheduler.next_daily_anchor_ts(_beijing_ts(2026, 8, 19, 3, 0)),
            _beijing_ts(2026, 8, 19, 4, 0),
        )
        # 恰好 04:00 -> 明天 04:00
        self.assertEqual(
            scheduler.next_daily_anchor_ts(_beijing_ts(2026, 8, 19, 4, 0)),
            _beijing_ts(2026, 8, 20, 4, 0),
        )

    def test_next_weekly_anchor(self):
        # 周三 12:00 -> 下周一 04:00
        self.assertEqual(
            scheduler.next_weekly_anchor_ts(_beijing_ts(2026, 8, 19, 12, 0)),
            _beijing_ts(2026, 8, 24, 4, 0),
        )
        # 周一 03:00（本周尚未刷新）-> 当天 04:00
        self.assertEqual(
            scheduler.next_weekly_anchor_ts(_beijing_ts(2026, 8, 17, 3, 0)),
            _beijing_ts(2026, 8, 17, 4, 0),
        )
        # 周一 05:00 -> 下周一 04:00
        self.assertEqual(
            scheduler.next_weekly_anchor_ts(_beijing_ts(2026, 8, 17, 5, 0)),
            _beijing_ts(2026, 8, 24, 4, 0),
        )


class TaskScheduleStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "task_schedule.json")
        self.store = scheduler.TaskScheduleStore(self.path)

    def test_no_record_means_due(self):
        self.assertTrue(self.store.is_due("快速狩猎"))
        self.assertIsNone(self.store.next_run("快速狩猎"))
        self.assertEqual(self.store.backoff_remaining_minutes("快速狩猎"), 0.0)

    def test_success_daily_delay_lands_on_next_4am(self):
        next_run = self.store.delay_after_run(
            "快速狩猎", ok=True, now=_beijing_ts(2026, 8, 19, 9, 0)
        )
        self.assertEqual(next_run, _beijing_ts(2026, 8, 20, 4, 0))
        self.assertFalse(
            self.store.is_due("快速狩猎", now=_beijing_ts(2026, 8, 19, 23, 0))
        )
        self.assertTrue(
            self.store.is_due("快速狩猎", now=_beijing_ts(2026, 8, 20, 4, 0))
        )

    def test_success_weekly_delay_lands_on_next_monday(self):
        next_run = self.store.delay_after_run(
            "每周跑图", ok=True, now=_beijing_ts(2026, 8, 19, 12, 0)
        )
        self.assertEqual(next_run, _beijing_ts(2026, 8, 24, 4, 0))

    def test_failure_delays_by_backoff_interval(self):
        now = _beijing_ts(2026, 8, 19, 9, 0)
        next_run = self.store.delay_after_run("镜中之战", ok=False, now=now)
        self.assertEqual(next_run, now + 30 * 60)
        self.assertAlmostEqual(
            self.store.backoff_remaining_minutes("镜中之战", now=now + 60), 29.0
        )
        self.assertTrue(self.store.last_run_ok("镜中之战") is False)

    def test_success_interval_can_be_nearer_than_anchor(self):
        policy = scheduler.SchedulePolicy(
            anchor="daily", success_interval_minutes=15.0
        )
        now = _beijing_ts(2026, 8, 19, 3, 30)
        next_run = self.store.delay_after_run(
            "自定义", ok=True, now=now, policy=policy
        )
        # now+15min(03:45) 比锚点 04:00 更近，按 ALAS 语义取最近者。
        self.assertEqual(next_run, now + 15 * 60)

    def test_unknown_task_is_not_scheduled(self):
        self.assertIsNone(self.store.delay_after_run("未知任务", ok=True))
        self.assertIsNone(self.store.next_run("未知任务"))

    def test_mark_due_now_forces_execution(self):
        self.store.delay_after_run(
            "快速狩猎", ok=True, now=_beijing_ts(2026, 8, 19, 9, 0)
        )
        forced = _beijing_ts(2026, 8, 19, 10, 0)
        self.store.mark_due_now("快速狩猎", now=forced)
        self.assertTrue(self.store.is_due("快速狩猎", now=forced))

    def test_roundtrip_and_corrupt_inputs(self):
        self.store.delay_after_run(
            "快速狩猎", ok=True, now=_beijing_ts(2026, 8, 19, 9, 0)
        )
        record = self.store.next_run("快速狩猎")
        reloaded = scheduler.TaskScheduleStore(self.path)
        self.assertEqual(reloaded.next_run("快速狩猎"), record)

        with open(self.path, "w", encoding="utf-8") as file:
            file.write("{not json")
        self.assertIsNone(scheduler.TaskScheduleStore(self.path).next_run("快速狩猎"))

        with open(self.path, "w", encoding="utf-8") as file:
            file.write('{"version": 999, "tasks": {"a": {"next_run": 1}}}')
        self.assertIsNone(scheduler.TaskScheduleStore(self.path).next_run("a"))

    def test_policy_registry_covers_batch_children_and_weekly_map(self):
        for name in (
            "公会、小屋、酒馆",
            "快速狩猎",
            "白嫖抽抽乐",
            "广场女神像",
            "镜中之战",
            "每日跑商",
            "一键完成日常",
        ):
            self.assertEqual(scheduler.TASK_POLICIES[name].anchor, "daily")
        self.assertEqual(scheduler.TASK_POLICIES["每周跑图"].anchor, "weekly")


class _FakeStartController:
    def __init__(self):
        self.started = []

    def start(self, task):
        self.started.append(task)


class _FakeOg:
    def __init__(self, executor):
        self.executor = executor
        self.app = SimpleNamespace(start_controller=_FakeStartController())


class _ExecutorStub:
    def __init__(self, tasks_by_class, current_task=None):
        self._tasks_by_class = tasks_by_class
        self.current_task = current_task

    def get_task_by_class(self, cls):
        return self._tasks_by_class.get(cls)


class _BatchStub:
    def __init__(self, config, child_tasks):
        self.name = "一键完成日常"
        self.config = config
        self.child_tasks = child_tasks
        self.requested_modes = []

    def request_run_mode(self, run_mode):
        self.requested_modes.append(run_mode)


class _TaskStub:
    def __init__(self, name):
        self.name = name


class RunDueTasksOnceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.history = RunHistoryStore(os.path.join(self.dir, "history.json"))
        self.schedule = scheduler.TaskScheduleStore(
            os.path.join(self.dir, "schedule.json")
        )
        set_default_store(self.history)
        scheduler.set_default_store(self.schedule)
        self.addCleanup(set_default_store, None)
        self.addCleanup(scheduler.set_default_store, None)

    def _build(self, config):
        class First:
            pass

        first = _TaskStub("first")
        batch = _BatchStub(
            {"启用": True, "启动自动执行日常": True, **config},
            (DailyBatchChild("第一项", First),),
        )
        executor = _ExecutorStub({First: first, DailyBatchTask: batch})
        return batch, executor

    def test_switch_off_starts_nothing(self):
        batch, executor = self._build({"启动自动执行日常": False})
        og = _FakeOg(executor)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)

    def test_due_child_starts_batch_in_incomplete_mode(self):
        batch, executor = self._build({})
        og = _FakeOg(executor)
        self.assertEqual(
            "一键完成日常（仅执行今日未完成）",
            auto_scheduler.run_due_tasks_once(og),
        )
        self.assertEqual(og.app.start_controller.started, [batch])
        self.assertEqual(batch.requested_modes, ["incomplete"])

    def test_child_completed_today_is_not_due(self):
        batch, executor = self._build({})
        self.history.record_task_done(
            SimpleNamespace(
                name="first", start_time=0, info={"状态": "first 完成。"}
            )
        )
        og = _FakeOg(executor)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)

    def test_child_in_failure_backoff_is_not_due(self):
        class First:
            pass

        first = _TaskStub("快速狩猎")
        batch = _BatchStub(
            {"启用": True, "启动自动执行日常": True},
            (DailyBatchChild("第一项", First),),
        )
        executor = _ExecutorStub({First: first, DailyBatchTask: batch})
        self.schedule.delay_after_run("快速狩猎", ok=False)
        og = _FakeOg(executor)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)

    def test_busy_executor_starts_nothing(self):
        batch, executor = self._build({})
        executor.current_task = _TaskStub("正在运行")
        og = _FakeOg(executor)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)

    def test_missing_batch_task_starts_nothing(self):
        og = _FakeOg(_ExecutorStub({}))
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))

    def test_weekly_map_auto_start_when_due(self):
        map_task = _TaskStub("每周跑图")
        batch = _BatchStub(
            {"启用": True, "启动自动执行每周跑图": True},
            (),
        )
        executor = _ExecutorStub(
            {MapCollectionTask: map_task, DailyBatchTask: batch}
        )
        og = _FakeOg(executor)
        self.assertEqual("每周跑图", auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([map_task], og.app.start_controller.started)

    def test_weekly_map_completed_this_week_is_not_due(self):
        map_task = _TaskStub("每周跑图")
        batch = _BatchStub(
            {"启用": True, "启动自动执行每周跑图": True},
            (),
        )
        executor = _ExecutorStub(
            {MapCollectionTask: map_task, DailyBatchTask: batch}
        )
        self.history.record_task_done(
            SimpleNamespace(
                name="每周跑图", start_time=0, info={"状态": "每周跑图完成。"}
            )
        )
        og = _FakeOg(executor)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)


if __name__ == "__main__":
    unittest.main()
