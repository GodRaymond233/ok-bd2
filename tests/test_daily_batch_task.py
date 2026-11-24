import tempfile
import threading
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from ok.task.exceptions import TaskDisabledException

from src.tasks import run_history, scheduler
from src.tasks.DailyBatchTask import (
    RUN_MODE_ALL,
    RUN_MODE_INCOMPLETE,
    SHUTDOWN_COUNTDOWN_SECONDS,
    DailyBatchChild,
    DailyBatchTask,
)
from src.tasks.login_gate import defer_until_login, release_after_login
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.PVPTask import PVPTask
from src.tasks.run_history import BEIJING_TZ, RunHistoryStore, set_default_store


class _ChildTask:
    def __init__(self, name, calls, result=True):
        self.name = name
        self.calls = calls
        self.result = result
        self.config = {"启用": False, "保留配置": 1}

    def info_clear(self):
        pass

    def run(self):
        self.calls.append((self.name, self.config.get("启用")))
        return self.result


class DailyBatchTaskTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        set_default_store(RunHistoryStore(f"{directory.name}/history.json"))
        self.addCleanup(set_default_store, None)
        scheduler.set_default_store(scheduler.TaskScheduleStore(f"{directory.name}/schedule.json"))
        self.addCleanup(scheduler.set_default_store, None)

    def make_task(self, children, child_specs, task_config):
        task = object.__new__(DailyBatchTask)
        task.lock = threading.Lock()
        task.child_tasks = child_specs
        task.config = task_config
        task.info = {}
        task.info_set = lambda key, value: task.info.__setitem__(key, value)
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.log_error = lambda *_args, **_kwargs: None
        resets = []
        task._executor = SimpleNamespace(
            get_task_by_class=lambda cls: children.get(cls),
            reset_scene=lambda **kwargs: resets.append(kwargs),
        )
        return task, resets

    def test_rewards_recheck_after_success_but_observe_failure_backoff(self):
        from src.tasks.DailyBatchTask import DAILY_BATCH_CHILDREN
        from src.tasks.MissionRewardTask import MissionRewardTask
        from src.tasks.run_history import default_store

        spec = DAILY_BATCH_CHILDREN[-1]
        self.assertIs(spec.task_class, MissionRewardTask)
        for ok in (True, False):
            with self.subTest(ok=ok):
                calls = []
                rewards = _ChildTask("每日及每周奖励", calls)
                rewards.recheck_in_batch = True
                default_store().record_task_done(rewards, ok=ok)
                task, _ = self.make_task({MissionRewardTask: rewards}, (spec,), {"启用": True})
                with mock.patch.object(scheduler, "default_store") as schedule:
                    schedule.return_value.is_due.return_value = False
                    schedule.return_value.backoff_remaining_minutes.return_value = 3
                    self.assertTrue(task.run(RUN_MODE_INCOMPLETE))
                self.assertEqual([(rewards.name, True)] if ok else [], calls)

    def test_child_order_and_config_restoration_after_failure(self):
        for result, recovered in ((True, True), (False, True), (False, False)):
            with self.subTest(result=result, recovered=recovered):
                calls = []
                first = _ChildTask("first", calls, result)
                later = _ChildTask("later", calls)
                first.recover_daily_home = mock.Mock(return_value=recovered)
                configs = first.config, later.config
                task, _ = self.make_task(
                    {str: first, MapCollectionTask: later},
                    (DailyBatchChild("first", str), DailyBatchChild("later", MapCollectionTask)),
                    {"启用": True},
                )
                self.assertEqual(result, task.run())
                self.assertEqual(
                    [("first", True)] + ([("later", True)] if result or recovered else []), calls
                )
                self.assertIs(configs[0], first.config)
                self.assertIs(configs[1], later.config)
                self.assertFalse(first.config["启用"])
                self.assertFalse(later.config["启用"])

    def test_pvp_settlement_skips_in_both_modes_without_home_or_completion(self):
        for mode in (RUN_MODE_ALL, RUN_MODE_INCOMPLETE):
            for crosses_during_run in (False, True):
                with self.subTest(mode=mode, crosses=crosses_during_run):
                    pvp = object.__new__(PVPTask)
                    pvp.name = "镜中之战"
                    pvp.config = {}
                    pvp.info = {}
                    pvp.info_set = lambda key, value: pvp.info.__setitem__(key, value)
                    pvp.info_clear = pvp.info.clear
                    pvp.log_info = mock.Mock()
                    pvp.auto_return_main_home = mock.Mock(return_value=True)
                    opens = datetime(2026, 11, 2, 8, tzinfo=BEIJING_TZ).timestamp()

                    def run():
                        pvp.info.update({"状态": "PVP未执行", "PVP 结算休战": True})
                        return True

                    pvp.run = mock.Mock(side_effect=run)
                    later = _ChildTask(f"later-{mode}-{crosses_during_run}", [])
                    specs = (
                        DailyBatchChild("自动PVP", PVPTask),
                        DailyBatchChild("later", _ChildTask),
                    )
                    task, _ = self.make_task(
                        {PVPTask: pvp, _ChildTask: later}, specs, {"启用": True}
                    )
                    with (
                        mock.patch(
                            "src.tasks.PVPTask.pvp_settlement_end_ts",
                            return_value=None if crosses_during_run else opens,
                        ),
                        mock.patch.object(scheduler, "default_store") as schedule,
                    ):
                        schedule.return_value.is_due.return_value = True
                        self.assertTrue(task.run(run_mode=mode))
                    if crosses_during_run:
                        pvp.run.assert_called_once()
                    else:
                        pvp.auto_return_main_home.assert_not_called()
                        pvp.run.assert_not_called()
                    self.assertEqual("自动PVP", task.info["跳过"])
                    self.assertEqual("later", task.info["完成"])
                    self.assertEqual("-", task.info["失败"])
                    self.assertEqual(
                        {"reason": "settlement", "minutes": None}, task.skip_reasons()["自动PVP"]
                    )
                    from src.tasks.run_history import default_store

                    self.assertIsNone(default_store().last_run(pvp.name))

    def test_progress_is_published_after_each_child(self):
        class First:
            pass

        class Second:
            pass

        first = _ChildTask("first", [])
        second = _ChildTask("second", [])
        specs = (DailyBatchChild("第一项", First), DailyBatchChild("第二项", Second))
        task, _resets = self.make_task(
            {First: first, Second: second}, specs, {"启用": True, "第一项": True, "第二项": True}
        )
        observed = []
        original_run = second.run
        boundary = datetime(2026, 10, 7, 4, tzinfo=BEIJING_TZ).timestamp()

        def second_run():
            observed.append(task.info.get("完成"))
            self.assertEqual(boundary - 60, history.last_run("first")["finished"])
            self.assertEqual(60, history.last_run("first")["duration"])
            return original_run()

        second.run = second_run

        with tempfile.TemporaryDirectory() as directory:
            history = RunHistoryStore(f"{directory}/history.json")
            with (
                mock.patch("src.tasks.run_history.default_store", return_value=history),
                mock.patch.object(scheduler, "default_store"),
                mock.patch(
                    "src.tasks.DailyBatchTask.time.time",
                    side_effect=[boundary - 120, boundary - 60, boundary - 30, boundary + 60],
                ),
            ):
                self.assertTrue(DailyBatchTask.run(task))
            task.name = "一键完成日常"
            history.record_task_done(task, finished=boundary + 90)
            reloaded = RunHistoryStore(history.path)
            self.assertFalse(reloaded.is_completed_today("first", now=boundary + 90))
            self.assertTrue(reloaded.is_completed_today("second", now=boundary + 90))
        self.assertEqual(["第一项"], observed)
        self.assertEqual("第一项、第二项", task.info.get("完成"))

    def test_cancellation_preserves_finished_children_and_stops_the_batch(self):
        for boundary in ("finish_gameplay_at_home", "run"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                classes = [type(name, (), {}) for name in ("First", "Cancelled", "Later")]
                calls = []
                children = [_ChildTask(name, calls) for name in ("first", "cancelled", "later")]
                cancelled = children[1]
                original_config = cancelled.config
                setattr(cancelled, boundary, mock.Mock(side_effect=TaskDisabledException))
                specs = tuple(
                    DailyBatchChild(child.name, cls) for cls, child in zip(classes, children)
                )
                task, resets = self.make_task(dict(zip(classes, children)), specs, {"启用": True})
                task._maybe_shutdown_after_daily = mock.Mock()
                history = RunHistoryStore(f"{directory}/history.json")
                with (
                    mock.patch("src.tasks.run_history.default_store", return_value=history),
                    mock.patch.object(scheduler, "default_store"),
                ):
                    with self.assertRaises(TaskDisabledException):
                        DailyBatchTask.run(task)
                expected = [("first", True)]
                if boundary == "finish_gameplay_at_home":
                    expected.append(("cancelled", True))
                self.assertEqual(expected, calls)
                self.assertIs(original_config, cancelled.config)
                self.assertEqual(2, len(resets))
                self.assertTrue(RunHistoryStore(history.path).last_run("first")["ok"])
                if boundary == "finish_gameplay_at_home":
                    self.assertTrue(history.last_run("cancelled")["ok"])
                else:
                    self.assertIsNone(history.last_run("cancelled"))
                self.assertIsNone(history.last_run("later"))
                task._maybe_shutdown_after_daily.assert_not_called()

    def test_collection_batch_respects_refresh_cycles_and_schedules_its_result(self):
        from src.tasks.auto_scheduler import _any_batch_child_due
        from src.tasks.run_history import day_start_ts, week_start_ts

        for period, day, hour, should_run, result_period, next_day in (
            ("今日", 17, 7, False, "今日", 17),
            ("今日", 17, 8, True, "今日", 18),
            ("今日", 17, 8, True, "本周", 19),
            ("本周", 17, 8, False, "本周", 19),
            ("本周", 19, 8, True, "今日", 20),
        ):
            with (
                self.subTest(period=period, day=day, hour=hour, result=result_period),
                tempfile.TemporaryDirectory() as directory,
            ):
                now = datetime(2026, 10, day, hour, tzinfo=BEIJING_TZ).timestamp()
                previous = datetime(2026, 10, 16, 23, tzinfo=BEIJING_TZ).timestamp()
                history = RunHistoryStore(f"{directory}/history.json")
                schedule = scheduler.TaskScheduleStore(f"{directory}/schedule.json")
                calls = []
                child = _ChildTask("每周跑图", calls)

                def completion_info(period, stamp):
                    anchor = week_start_ts if period == "本周" else day_start_ts
                    cycle = datetime.fromtimestamp(anchor(stamp, refresh_hour=8), BEIJING_TZ)
                    return {
                        "状态": "跑图完成。",
                        "跑图完成范围": period,
                        "跑图周期": cycle.date().isoformat(),
                    }

                child.info = completion_info(period, previous)
                history.record_task_done(child, finished=previous, ok=True)
                child.info = completion_info(result_period, now)
                task, _ = self.make_task(
                    {MapCollectionTask: child},
                    (DailyBatchChild("跑图", MapCollectionTask),),
                    {"启用": True, "跑图": True, "完成日常后自动关机": True},
                )
                with (
                    mock.patch("time.time", return_value=now),
                    mock.patch("src.tasks.run_history.default_store", return_value=history),
                    mock.patch.object(scheduler, "default_store", return_value=schedule),
                    mock.patch("src.tasks.DailyBatchTask._schedule_system_shutdown") as shutdown,
                ):
                    self.assertEqual(
                        should_run,
                        _any_batch_child_due(task, task.executor, history, schedule, now=now),
                    )
                    self.assertTrue(task.run(RUN_MODE_INCOMPLETE))
                    self.assertEqual(should_run, bool(calls))
                    self.assertEqual("跑图", task.info["完成" if should_run else "跳过"])
                    shutdown.assert_called_once_with(SHUTDOWN_COUNTDOWN_SECONDS)
                    if should_run:
                        expected = datetime(2026, 10, next_day, 8, tzinfo=BEIJING_TZ).timestamp()
                        self.assertEqual(expected, schedule.next_run(child.name))

    def test_skip_reasons_say_why_each_child_was_skipped(self):
        calls = []
        children = {}
        specs = []
        for key, name in (
            ("不参与", "off"), ("已完成", "done"), ("退避中", "快速狩猎"),
            ("失败", "failing"), ("之后", "later"),
        ):
            kind = type(name, (), {})
            children[kind] = _ChildTask(name, calls, result=name != "failing")
            specs.append(DailyBatchChild(key, kind))
        task, _ = self.make_task(children, tuple(specs), {"启用": True, "不参与": False})
        self.assertEqual({}, task.skip_reasons())
        history = run_history.default_store()
        schedule_store = scheduler.default_store()
        done = children[specs[1].task_class]
        history.record_task_done(done, ok=True)
        schedule_store.delay_after_run("快速狩猎", ok=False)
        self.assertFalse(DailyBatchTask.run(task, RUN_MODE_INCOMPLETE))
        reasons = task.skip_reasons()
        self.assertEqual(
            {"不参与": "off", "已完成": "done", "退避中": "not_due", "之后": "stopped"},
            {key: value["reason"] for key, value in reasons.items()},
        )
        self.assertGreater(reasons["退避中"]["minutes"], 0)
        self.assertIsNone(reasons["已完成"]["minutes"])
        self.assertEqual([("failing", True)], calls)

        # The next run starts from a clean slate, and callers get copies.
        reasons["之后"]["reason"] = "changed"
        task.config["不参与"] = True
        task.config["失败"] = False
        schedule_store.mark_due_now("快速狩猎", now=time.time() - 3600)
        self.assertTrue(DailyBatchTask.run(task, RUN_MODE_ALL))
        self.assertEqual(
            {"失败": "off"},
            {key: value["reason"] for key, value in task.skip_reasons().items()},
        )

    def test_login_defers_and_reenqueues_the_batch_once(self):
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        login = SimpleNamespace(login_pending=True)
        task, _ = self.make_task({AutoLoginTask: login}, (), {"启用": True})
        task._enabled = True
        self.assertTrue(defer_until_login(task))
        self.assertTrue(task._start_after_login)
        self.assertIn("等待自动登录", task.info["状态"])
        task._enabled = False
        task._executor.onetime_tasks = [task]
        task._executor.enqueue_onetime_task = mock.Mock(return_value=True)
        self.assertTrue(release_after_login(task.executor))
        self.assertTrue(task._enabled)
        self.assertFalse(task._start_after_login)
        task._enabled = False
        self.assertFalse(release_after_login(task.executor))
        task.executor.enqueue_onetime_task.assert_called_once_with(task)

    def test_shutdown_requires_every_enabled_child_to_complete(self):
        for case, ok, attempts, announced in (
            ("complete", True, 1, True),
            ("boundary", True, 1, True),
            ("backoff", True, 0, False),
            ("disabled", True, 0, False),
            ("failure", False, 0, False),
            ("rejected", True, 1, False),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                calls = []
                child = _ChildTask("快速狩猎", calls, result=case != "failure")
                task, _ = self.make_task(
                    {str: child},
                    (DailyBatchChild("hunt", str), DailyBatchChild("off", int)),
                    {"启用": True, "off": False, "完成日常后自动关机": case != "disabled"},
                )
                task.log_error = mock.Mock()
                history = RunHistoryStore(f"{directory}/history")
                schedule = scheduler.TaskScheduleStore(f"{directory}/schedule")
                if case == "boundary":
                    history.is_completed = mock.Mock(side_effect=[True, False])
                if case == "backoff":
                    schedule.delay_after_run(child.name, ok=False)
                with (
                    mock.patch("src.tasks.run_history.default_store", return_value=history),
                    mock.patch.object(scheduler, "default_store", return_value=schedule),
                    mock.patch(
                        "src.tasks.DailyBatchTask._schedule_system_shutdown",
                        return_value=case != "rejected",
                    ) as shutdown,
                ):
                    self.assertEqual(ok, task.run(RUN_MODE_INCOMPLETE))
                self.assertEqual(
                    [mock.call(SHUTDOWN_COUNTDOWN_SECONDS)] * attempts, shutdown.call_args_list
                )
                self.assertEqual(announced, "自动关机" in task.info["状态"])
                self.assertEqual(case == "rejected", task.log_error.called if ok else False)
                self.assertEqual(
                    [] if case in {"boundary", "backoff"} else [(child.name, True)], calls
                )

    def test_schedule_system_shutdown_uses_system32_and_reports_exit_code(self):
        from src.tasks.DailyBatchTask import _schedule_system_shutdown

        for returncode, expected in ((0, True), (5, False)):
            with self.subTest(returncode=returncode):
                completed = SimpleNamespace(returncode=returncode)
                with (
                    mock.patch(
                        "src.tasks.DailyBatchTask.subprocess.run", return_value=completed
                    ) as run,
                    mock.patch.dict("os.environ", {"SystemRoot": r"C:\Windows"}),
                ):
                    self.assertEqual(
                        expected, _schedule_system_shutdown(SHUTDOWN_COUNTDOWN_SECONDS)
                    )
                command = run.call_args.args[0]
                # 绝对路径调用 System32 的 shutdown.exe，防止安装目录同名顶替。
                self.assertEqual(r"C:\Windows\System32\shutdown.exe", command[0])
                self.assertEqual(["/s", "/t", "60"], command[1:])
                self.assertTrue(run.call_args.kwargs["capture_output"])


if __name__ == "__main__":
    unittest.main()
