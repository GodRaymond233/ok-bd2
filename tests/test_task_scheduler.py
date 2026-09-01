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
        ):
            self.assertEqual(scheduler.TASK_POLICIES[name].anchor, "daily")
        self.assertEqual(scheduler.TASK_POLICIES["每周跑图"].anchor, "weekly")
        # 批处理自身没有调度策略：自动调度只消费子任务账本，批处理整体
        # 的 next_run 无消费者（避免只写不读的误导条目）。
        self.assertNotIn("一键完成日常", scheduler.TASK_POLICIES)


class _FakeStartController:
    def __init__(self):
        self.started = []

    def start(self, task):
        self.started.append(task)


class _FakeOg:
    def __init__(self, executor, debug=True):
        self.executor = executor
        # 自动执行每周跑图只允许调试模式；默认按调试模式构造，正式模式
        # 由显式传 debug=False 的用例覆盖。
        self.app = SimpleNamespace(start_controller=_FakeStartController(), debug=debug)


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

    def _build_with_login(self, login_enabled, login_finished):
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        batch, executor = self._build({})
        login = SimpleNamespace(_enabled=login_enabled, _finished=login_finished)
        executor._tasks_by_class[AutoLoginTask] = login
        return batch, executor

    def test_auto_start_waits_for_login_to_finish(self):
        # HIGH 回归：登录 trigger 是逐帧推进的状态机，周期间有执行器空闲
        # 窗口；登录未完成时绝不启动任务，否则批处理顶掉登录并把子任务
        # 烧进 30 分钟退避，且触发任务不发 task_done、再无复查来源。
        batch, executor = self._build_with_login(True, False)
        og = _FakeOg(executor)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)
        self.assertEqual([], batch.requested_modes)

    def test_auto_start_proceeds_after_login_finished(self):
        batch, executor = self._build_with_login(True, True)
        og = _FakeOg(executor)
        self.assertEqual(
            "一键完成日常（仅执行今日未完成）",
            auto_scheduler.run_due_tasks_once(og),
        )
        self.assertEqual(og.app.start_controller.started, [batch])

    def test_auto_start_proceeds_when_login_task_disabled(self):
        # 停用自动登录的用户不能被登录等待卡死；未就绪场景由主页确认与
        # 退避兜底。
        batch, executor = self._build_with_login(False, False)
        og = _FakeOg(executor)
        self.assertEqual(
            "一键完成日常（仅执行今日未完成）",
            auto_scheduler.run_due_tasks_once(og),
        )
        self.assertEqual(og.app.start_controller.started, [batch])

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

    def test_disabled_batch_starts_nothing_even_when_switch_on(self):
        # HIGH 回归：批处理“启用”关闭但“启动自动执行日常”残留开启时，
        # 不得出现 start -> run() 立即返回 -> task_done -> 再 start 的空转。
        batch, executor = self._build({"启用": False})
        og = _FakeOg(executor)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)

    def test_weekly_map_auto_start_requires_debug_mode(self):
        # 每周跑图在正式前端保持隐藏，自动执行开关只在调试模式生效。
        map_task = _TaskStub("每周跑图")
        batch = _BatchStub(
            {"启用": True, "启动自动执行每周跑图": True},
            (),
        )
        executor = _ExecutorStub(
            {MapCollectionTask: map_task, DailyBatchTask: batch}
        )
        og = _FakeOg(executor, debug=False)
        self.assertIsNone(auto_scheduler.run_due_tasks_once(og))
        self.assertEqual([], og.app.start_controller.started)


class InstallAutoSchedulerTest(unittest.TestCase):
    """生产入口在 QApplication 创建前安装，信号必须仍然接上（HIGH 回归）."""

    def _reset_install_state(self):
        from ok.gui.Communicate import communicate

        runner = getattr(auto_scheduler.install_auto_scheduler, "_runner", None)
        if runner is not None:
            for signal, slot in (
                (communicate.task_done, runner.on_task_done),
                (communicate.task_list_updated, runner.on_first_app_signal),
                (communicate.starting_emulator, runner.on_first_app_signal),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        auto_scheduler.install_auto_scheduler._installed = False
        if runner is not None:
            auto_scheduler.install_auto_scheduler._runner = None

    def setUp(self):
        # 导入 src.config 会触发一次真实安装（install_quest_ui →
        # install_auto_scheduler）；每个用例前清掉该状态，保证可重装。
        self._reset_install_state()

    def tearDown(self):
        self._reset_install_state()

    def test_installs_without_qapplication_and_schedules_on_first_signal(self):
        from unittest.mock import patch

        from ok.gui.Communicate import communicate

        with patch(
            "PySide6.QtCore.QCoreApplication.instance", return_value=None
        ), patch(
            "PySide6.QtCore.QTimer.singleShot"
        ) as single_shot_mock:
            self.assertTrue(auto_scheduler.install_auto_scheduler())
            # 幂等：重复安装直接拒绝。
            self.assertFalse(auto_scheduler.install_auto_scheduler())
            # 导入期（无应用实例）不得排布任何定时器。
            self.assertEqual(single_shot_mock.call_args_list, [])
            # 任务列表刷新是应用就绪后的必发信号，应触发首次检查排布。
            communicate.task_list_updated.emit()
            self.assertEqual(single_shot_mock.call_count, 1)
            delay = single_shot_mock.call_args[0][0]
            self.assertEqual(
                delay, int(auto_scheduler.INSTALL_DELAY_SECONDS * 1000)
            )
            # 重复信号不重复排布。
            communicate.starting_emulator.emit(False, None, 0)
            self.assertEqual(single_shot_mock.call_count, 1)

    def test_task_done_before_other_signals_schedules_startup_check(self):
        from unittest.mock import patch

        from ok.gui.Communicate import communicate

        with patch(
            "PySide6.QtCore.QCoreApplication.instance", return_value=None
        ), patch(
            "PySide6.QtCore.QTimer.singleShot"
        ) as single_shot_mock:
            self.assertTrue(auto_scheduler.install_auto_scheduler())
            communicate.task_done.emit(None)
            # task_done 既兜底首次检查，也排布 8 秒后的例行复查。
            delays = [call[0][0] for call in single_shot_mock.call_args_list]
            self.assertIn(
                int(auto_scheduler.INSTALL_DELAY_SECONDS * 1000), delays
            )
            self.assertIn(
                int(auto_scheduler.RECHECK_DELAY_SECONDS * 1000), delays
            )


if __name__ == "__main__":
    unittest.main()
