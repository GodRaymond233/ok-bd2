import tempfile
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from ok.task.exceptions import TaskDisabledException

from src.config import config
from src.tasks import scheduler
from src.tasks.DailyBatchTask import (
    RUN_MODE_ALL,
    RUN_MODE_INCOMPLETE,
    SHUTDOWN_COUNTDOWN_SECONDS,
    DailyBatchChild,
    DailyBatchTask,
)
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.run_history import BEIJING_TZ, RunHistoryStore, set_default_store
from src.tasks.task_notifications import log_task_completion


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
    def test_failed_child_recovers_and_later_child_runs_without_successful_batch(self):
        class First:
            pass
        class Later:
            pass
        calls = []
        first = _ChildTask("first", calls, result=False)
        first.recover_daily_home = mock.Mock(return_value=True)
        later = _ChildTask("later", calls)
        task, _ = self.make_task({First: first, Later: later},
                                (DailyBatchChild("first", First), DailyBatchChild("later", Later)),
                                {"启用": True, "first": True, "later": True})
        task._maybe_shutdown_after_daily = mock.Mock()
        self.assertFalse(task.run())
        self.assertEqual([("first", True), ("later", True)], calls)
        self.assertEqual("first", task.info["失败"])
        self.assertIn("执行结束", task.info["状态"])
        task._maybe_shutdown_after_daily.assert_not_called()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        set_default_store(RunHistoryStore(f"{directory.name}/history.json"))
        self.addCleanup(set_default_store, None)

    def make_task(self, children, child_specs, task_config):
        task = object.__new__(DailyBatchTask)
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

    def test_registered_before_the_other_daily_tasks(self):
        self.assertEqual(
            ["src.tasks.DailyBatchTask", "DailyBatchTask"],
            config["onetime_tasks"][0],
        )

    def test_config_exposes_all_daily_children_including_map_collection(self):
        executor = SimpleNamespace(scene=None)
        task = DailyBatchTask(executor, SimpleNamespace())
        expected = [
            "公会、小屋、酒馆",
            "免费抽抽乐",
            "快速狩猎",
            "广场女神像",
            "跑图",
            "跑商",
            "自动PVP",
        ]
        self.assertEqual(expected, task.config_type["启用"]["sub_configs"][True])
        self.assertTrue(all(task.default_config[key] for key in expected))
        self.assertIn("跑图", task.description)
        self.assertIs(MapCollectionTask, task.child_tasks[-3].task_class)
        daily_classes = [child.task_class.__name__ for child in task.child_tasks]
        self.assertEqual(daily_classes, [
            class_name for _module, class_name in config["onetime_tasks"]
            if class_name in daily_classes
        ])

    def test_weekly_map_collection_card_is_visible_in_daily_group_in_both_modes(self):
        from qfluentwidgets import FluentIcon

        from src.tasks.debug_registry import install_debug_tasks

        for debug in (False, True):
            with self.subTest(debug=debug):
                cfg = {"onetime_tasks": list(config["onetime_tasks"])}
                if debug:
                    install_debug_tasks(cfg)
                    install_debug_tasks(cfg)
                self.assertEqual(
                    1, cfg["onetime_tasks"].count(
                        ["src.tasks.MapCollectionTask", "MapCollectionTask"]
                    ),
                )
                task = MapCollectionTask(SimpleNamespace(scene=None), SimpleNamespace(debug=debug))
                self.assertEqual("每周跑图", task.name)
                self.assertTrue(task.visible)
                self.assertEqual("日常/周常", task.group_name)
                self.assertEqual(FluentIcon.CALENDAR, task.group_icon)

    def test_runs_enabled_children_in_order_and_restores_their_configs(self):
        class First:
            pass

        class Second:
            pass

        calls = []
        first = _ChildTask("first", calls)
        second = _ChildTask("second", calls)
        original_first_config = first.config
        original_second_config = second.config
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
        )
        task, resets = self.make_task(
            {First: first, Second: second},
            specs,
            {"启用": True, "第一项": True, "第二项": True},
        )

        self.assertTrue(DailyBatchTask.run(task))
        self.assertEqual([("first", True), ("second", True)], calls)
        self.assertIs(original_first_config, first.config)
        self.assertIs(original_second_config, second.config)
        self.assertEqual(2, len(resets))

    def test_progress_is_published_after_each_child(self):
        class First:
            pass

        class Second:
            pass

        first = _ChildTask("first", [])
        second = _ChildTask("second", [])
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
        )
        task, _resets = self.make_task(
            {First: first, Second: second},
            specs,
            {"启用": True, "第一项": True, "第二项": True},
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
            with mock.patch(
                "src.tasks.run_history.default_store", return_value=history
            ), mock.patch.object(scheduler, "default_store"), mock.patch(
                "src.tasks.DailyBatchTask.time.time",
                side_effect=[boundary - 120, boundary - 60, boundary - 30, boundary + 60],
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
        for boundary in ("auto_return_main_home", "run"):
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
                with mock.patch(
                    "src.tasks.run_history.default_store", return_value=history
                ), mock.patch.object(scheduler, "default_store"):
                    with self.assertRaises(TaskDisabledException):
                        DailyBatchTask.run(task)
                self.assertEqual([("first", True)], calls)
                self.assertIs(original_config, cancelled.config)
                self.assertEqual(2, len(resets))
                self.assertTrue(RunHistoryStore(history.path).last_run("first")["ok"])
                self.assertIsNone(history.last_run("cancelled"))
                self.assertIsNone(history.last_run("later"))
                task._maybe_shutdown_after_daily.assert_not_called()

    def test_failure_stops_later_children_and_disabled_switch_is_skipped(self):
        class Skipped:
            pass

        class Failed:
            pass

        class Later:
            pass

        calls = []
        skipped = _ChildTask("skipped", calls)
        failed = _ChildTask("failed", calls, result=False)
        later = _ChildTask("later", calls)
        specs = (
            DailyBatchChild("关闭项", Skipped),
            DailyBatchChild("失败项", Failed),
            DailyBatchChild("后续项", Later),
        )
        task, _resets = self.make_task(
            {Skipped: skipped, Failed: failed, Later: later},
            specs,
            {"启用": True, "关闭项": False, "失败项": True, "后续项": True},
        )

        self.assertFalse(DailyBatchTask.run(task))
        self.assertEqual([("failed", True)], calls)

    def test_child_completion_is_silent_and_batch_emits_one_overview(self):
        class NotifyingChild:
            pass

        notifications = []
        child = _ChildTask("child", [])
        child.log_info = lambda message, notify=False: notifications.append(
            ("child", message, notify)
        )

        def run_child():
            log_task_completion(child, "子任务完成。")
            return True

        child.run = run_child
        specs = (DailyBatchChild("子任务", NotifyingChild),)
        task, _resets = self.make_task(
            {NotifyingChild: child},
            specs,
            {"启用": True, "子任务": True},
        )
        task.log_info = lambda message, notify=False: notifications.append(
            ("batch", message, notify)
        )

        self.assertTrue(DailyBatchTask.run(task))
        self.assertEqual(
            [
                ("batch", "一键完成日常：开始 子任务。", False),
                ("child", "子任务完成。", False),
                ("batch", "一键完成日常：子任务 完成。", False),
                ("batch", "一键完成日常完成：已执行 1 项，跳过 0 项。", True),
            ],
            notifications,
        )
        self.assertFalse(
            hasattr(child, "_completion_notification_suppression_depth")
        )

    def test_incomplete_mode_skips_children_completed_today_without_mutating_config(self):
        class First:
            pass

        class Second:
            pass

        calls = []
        first = _ChildTask("first", calls)
        second = _ChildTask("second", calls)
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
        )
        task, _resets = self.make_task(
            {First: first, Second: second},
            specs,
            {"启用": True, "第一项": True, "第二项": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunHistoryStore(f"{temp_dir}/history.json")
            set_default_store(store)
            store.record_task_done(
                SimpleNamespace(
                    name="first",
                    config=first.config,
                    start_time=0,
                    info={"状态": "first 完成。"},
                )
            )
            original_config = task.config
            try:
                self.assertTrue(DailyBatchTask.run(task, RUN_MODE_INCOMPLETE))
            finally:
                set_default_store(None)

        self.assertEqual([("second", True)], calls)
        self.assertIs(original_config, task.config)
        self.assertEqual("第一项", task.info.get("跳过"))

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
            with self.subTest(period=period, day=day, hour=hour, result=result_period), \
                    tempfile.TemporaryDirectory() as directory:
                now = datetime(2026, 10, day, hour, tzinfo=BEIJING_TZ).timestamp()
                previous = datetime(2026, 10, 16, 23, tzinfo=BEIJING_TZ).timestamp()
                history = RunHistoryStore(f"{directory}/history.json")
                schedule = scheduler.TaskScheduleStore(f"{directory}/schedule.json")
                calls = []
                child = _ChildTask("每周跑图", calls)

                def completion_info(period, stamp):
                    anchor = week_start_ts if period == "本周" else day_start_ts
                    cycle = datetime.fromtimestamp(anchor(stamp, refresh_hour=8), BEIJING_TZ)
                    return {"状态": "跑图完成。", "跑图完成范围": period,
                            "跑图周期": cycle.date().isoformat()}

                child.info = completion_info(period, previous)
                history.record_task_done(child, finished=previous, ok=True)
                child.info = completion_info(result_period, now)
                task, _ = self.make_task(
                    {MapCollectionTask: child}, (DailyBatchChild("跑图", MapCollectionTask),),
                    {"启用": True, "跑图": True, "完成日常后自动关机": True},
                )
                with mock.patch("time.time", return_value=now), mock.patch(
                    "src.tasks.run_history.default_store", return_value=history,
                ), mock.patch.object(scheduler, "default_store", return_value=schedule), \
                        mock.patch(
                            "src.tasks.DailyBatchTask._schedule_system_shutdown",
                        ) as shutdown:
                    self.assertEqual(should_run, _any_batch_child_due(
                        task, task.executor, history, schedule, now=now,
                    ))
                    self.assertTrue(task.run(RUN_MODE_INCOMPLETE))
                    self.assertEqual(should_run, bool(calls))
                    self.assertEqual("跑图", task.info["完成" if should_run else "跳过"])
                    shutdown.assert_called_once_with(SHUTDOWN_COUNTDOWN_SECONDS)
                    if should_run:
                        expected = datetime(2026, 10, next_day, 8, tzinfo=BEIJING_TZ).timestamp()
                        self.assertEqual(expected, schedule.next_run(child.name))

    def test_requested_run_mode_is_transient_and_next_run_defaults_to_all(self):
        class First:
            pass

        class Second:
            pass

        calls = []
        first = _ChildTask("first", calls)
        second = _ChildTask("second", calls)
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
        )
        task, _resets = self.make_task(
            {First: first, Second: second},
            specs,
            {"启用": True, "第一项": True, "第二项": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunHistoryStore(f"{temp_dir}/history.json")
            set_default_store(store)
            store.record_task_done(
                SimpleNamespace(
                    name="first",
                    config=first.config,
                    start_time=0,
                    info={"状态": "first 完成。"},
                )
            )
            try:
                task.request_run_mode(RUN_MODE_INCOMPLETE)
                self.assertTrue(DailyBatchTask.run(task))
                self.assertTrue(DailyBatchTask.run(task))
            finally:
                set_default_store(None)

        self.assertEqual(
            [("second", True), ("first", True), ("second", True)],
            calls,
        )

    def test_incomplete_mode_skips_child_in_failure_backoff(self):
        class First:
            pass

        calls = []
        first = _ChildTask("快速狩猎", calls)
        specs = (DailyBatchChild("第一项", First),)
        task, _resets = self.make_task(
            {First: first},
            specs,
            {"启用": True, "第一项": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            schedule_store = scheduler.TaskScheduleStore(f"{temp_dir}/schedule.json")
            scheduler.set_default_store(schedule_store)
            # INCOMPLETE 模式会读 run_history 判断“今日已完成”；必须钉住
            # 独立空账本，否则开发机上 configs/task_run_history.json 的真实
            # 记录会把用例变成空跑。
            set_default_store(RunHistoryStore(f"{temp_dir}/history.json"))
            try:
                # 刚失败过：next_run 在未来，处于退避期。
                schedule_store.delay_after_run("快速狩猎", ok=False)
                self.assertTrue(DailyBatchTask.run(task, RUN_MODE_INCOMPLETE))
            finally:
                scheduler.set_default_store(None)
                set_default_store(None)

        self.assertEqual([], calls)
        self.assertEqual("第一项", task.info.get("跳过"))

    def test_incomplete_mode_runs_child_once_backoff_expired(self):
        class First:
            pass

        calls = []
        first = _ChildTask("快速狩猎", calls)
        specs = (DailyBatchChild("第一项", First),)
        task, _resets = self.make_task(
            {First: first},
            specs,
            {"启用": True, "第一项": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            schedule_store = scheduler.TaskScheduleStore(f"{temp_dir}/schedule.json")
            scheduler.set_default_store(schedule_store)
            set_default_store(RunHistoryStore(f"{temp_dir}/history.json"))
            try:
                # 退避点已过期（next_run 在过去）→ 视为到期，应执行。
                schedule_store.mark_due_now("快速狩猎", now=time.time() - 3600)
                self.assertTrue(DailyBatchTask.run(task, RUN_MODE_INCOMPLETE))
            finally:
                scheduler.set_default_store(None)
                set_default_store(None)

        self.assertEqual([("快速狩猎", True)], calls)

    def test_requested_run_mode_expires_and_defaults_to_all(self):
        # MEDIUM 回归：启动自动执行注入的 run_mode 带有效期。启动失败时
        # run() 不执行、请求无法消费，过期作废，避免残留的 INCOMPLETE 被
        # 之后的用户手动点击静默继承。
        class First:
            pass

        first = _ChildTask("快速狩猎", [])
        task, _resets = self.make_task(
            {First: first},
            (DailyBatchChild("第一项", First),),
            {"启用": True, "第一项": True},
        )
        task.request_run_mode(RUN_MODE_INCOMPLETE)
        self.assertEqual(RUN_MODE_INCOMPLETE, task._take_run_mode(None))

        task.request_run_mode(RUN_MODE_INCOMPLETE)
        task._requested_run_mode_deadline = time.monotonic() - 1.0
        self.assertEqual(RUN_MODE_ALL, task._take_run_mode(None))
        # 作废后不留残留。
        self.assertEqual(RUN_MODE_ALL, task._take_run_mode(None))

    def test_run_defers_while_auto_login_pending(self):
        # 回归：手动点开始且游戏冷启动时，onetime 出队优先于登录 trigger
        # 且运行期间 trigger 不执行；立即跑子任务只会在登录页把主页确认
        # 烧超时并中止整批，登录完成后也没有任何东西重新拉起批次。
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        class First:
            pass

        calls = []
        first = _ChildTask("first", calls)
        login = SimpleNamespace(_enabled=True, _finished=False)
        task, _resets = self.make_task(
            {First: first, AutoLoginTask: login},
            (DailyBatchChild("第一项", First),),
            {"启用": True, "第一项": True},
        )

        self.assertTrue(DailyBatchTask.run(task))
        self.assertEqual([], calls)
        self.assertTrue(task._start_after_login)
        self.assertIn("等待自动登录", task.info.get("状态"))

    def test_run_proceeds_when_auto_login_settled(self):
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        class First:
            pass

        for login in (
            SimpleNamespace(_enabled=True, _finished=True),
            SimpleNamespace(_enabled=False, _finished=False),
            None,
        ):
            with self.subTest(login=login):
                calls = []
                first = _ChildTask("first", calls)
                children = {First: first}
                if login is not None:
                    children[AutoLoginTask] = login
                task, _resets = self.make_task(
                    children,
                    (DailyBatchChild("第一项", First),),
                    {"启用": True, "第一项": True},
                )

                self.assertTrue(DailyBatchTask.run(task))
                self.assertEqual([("first", True)], calls)
                self.assertFalse(getattr(task, "_start_after_login", False))

    def test_deferral_preserves_requested_run_mode(self):
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        login = SimpleNamespace(_enabled=True, _finished=False)
        task, _resets = self.make_task(
            {AutoLoginTask: login},
            (),
            {"启用": True},
        )
        task.request_run_mode(RUN_MODE_INCOMPLETE)

        self.assertTrue(DailyBatchTask.run(task))
        self.assertEqual(RUN_MODE_INCOMPLETE, task._take_run_mode(None))

    def test_release_after_login_reenqueues_gated_batch_once(self):
        task, _resets = self.make_task({}, (), {"启用": True})
        task._start_after_login = True
        task._enabled = False
        enqueued = []
        task._executor = SimpleNamespace(
            get_task_by_class=lambda cls: task if cls is DailyBatchTask else None,
            enqueue_onetime_task=lambda t: enqueued.append(t) or True,
        )

        self.assertTrue(DailyBatchTask.release_after_login(task._executor))
        self.assertEqual([task], enqueued)
        self.assertTrue(task._enabled)
        self.assertFalse(task._start_after_login)

        task._enabled = False
        self.assertFalse(DailyBatchTask.release_after_login(task._executor))
        self.assertEqual(1, len(enqueued))

    def test_all_mode_records_child_schedule_after_success_and_failure(self):
        class First:
            pass

        class Second:
            pass

        calls = []
        first = _ChildTask("快速狩猎", calls)
        second = _ChildTask("镜中之战", calls, result=False)
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
        )
        task, _resets = self.make_task(
            {First: first, Second: second},
            specs,
            {"启用": True, "第一项": True, "第二项": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            schedule_store = scheduler.TaskScheduleStore(f"{temp_dir}/schedule.json")
            scheduler.set_default_store(schedule_store)
            try:
                self.assertFalse(DailyBatchTask.run(task))
            finally:
                scheduler.set_default_store(None)

        # 成功子任务推迟到下一次日常锚点（北京 04:00）。
        first_next = schedule_store.next_run("快速狩猎")
        self.assertIsNotNone(first_next)
        self.assertGreaterEqual(first_next, scheduler.next_daily_anchor_ts() - 1)
        self.assertTrue(schedule_store.last_run_ok("快速狩猎"))
        # 失败子任务按失败间隔退避，不推进锚点。
        second_next = schedule_store.next_run("镜中之战")
        self.assertIsNotNone(second_next)
        self.assertGreater(second_next, time.time() + 29 * 60)
        self.assertFalse(schedule_store.last_run_ok("镜中之战"))

    def test_shutdown_scheduled_when_all_enabled_children_completed(self):
        class First:
            pass

        class Second:
            pass

        class Disabled:
            pass

        first = _ChildTask("first", [])
        second = _ChildTask("second", [])
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
            DailyBatchChild("关闭项", Disabled),
        )
        task, _resets = self.make_task(
            {First: first, Second: second},
            specs,
            {
                "启用": True,
                "完成日常后自动关机": True,
                "第一项": True,
                "第二项": True,
                # 关闭的子任务不属于今日日常，不影响关机判定。
                "关闭项": False,
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            set_default_store(RunHistoryStore(f"{temp_dir}/history.json"))
            try:
                with mock.patch(
                    "src.tasks.DailyBatchTask._schedule_system_shutdown"
                ) as shutdown:
                    self.assertTrue(DailyBatchTask.run(task))
            finally:
                set_default_store(None)

        shutdown.assert_called_once_with(SHUTDOWN_COUNTDOWN_SECONDS)
        self.assertIn("自动关机", task.info.get("状态"))

    def test_shutdown_counts_child_completed_before_the_run(self):
        class First:
            pass

        class Second:
            pass

        first = _ChildTask("first", [])
        second = _ChildTask("second", [])
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
        )
        task, _resets = self.make_task(
            {First: first, Second: second},
            specs,
            {"启用": True, "完成日常后自动关机": True, "第一项": True, "第二项": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunHistoryStore(f"{temp_dir}/history.json")
            set_default_store(store)
            store.record_task_done(
                SimpleNamespace(name="first", start_time=0, info={"状态": "first 完成。"})
            )
            try:
                with mock.patch(
                    "src.tasks.DailyBatchTask._schedule_system_shutdown"
                ) as shutdown:
                    self.assertTrue(DailyBatchTask.run(task, RUN_MODE_INCOMPLETE))
            finally:
                set_default_store(None)

        shutdown.assert_called_once_with(SHUTDOWN_COUNTDOWN_SECONDS)

    def test_shutdown_not_scheduled_when_child_not_completed(self):
        class First:
            pass

        class Second:
            pass

        first = _ChildTask("快速狩猎", [])
        second = _ChildTask("镜中之战", [])
        specs = (
            DailyBatchChild("第一项", First),
            DailyBatchChild("第二项", Second),
        )
        task, _resets = self.make_task(
            {First: first, Second: second},
            specs,
            {"启用": True, "完成日常后自动关机": True, "第一项": True, "第二项": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            schedule_store = scheduler.TaskScheduleStore(f"{temp_dir}/schedule.json")
            scheduler.set_default_store(schedule_store)
            set_default_store(RunHistoryStore(f"{temp_dir}/history.json"))
            try:
                # 退避中的子任务被跳过且今日未完成：批运行成功也不关机。
                schedule_store.delay_after_run("镜中之战", ok=False)
                with mock.patch(
                    "src.tasks.DailyBatchTask._schedule_system_shutdown"
                ) as shutdown:
                    self.assertTrue(DailyBatchTask.run(task, RUN_MODE_INCOMPLETE))
            finally:
                scheduler.set_default_store(None)
                set_default_store(None)

        shutdown.assert_not_called()

    def test_shutdown_not_scheduled_after_failure_or_when_switch_off(self):
        class First:
            pass

        class Second:
            pass

        for switch_on, second_result in ((False, True), (True, False)):
            with self.subTest(switch_on=switch_on, second_result=second_result):
                first = _ChildTask("first", [])
                second = _ChildTask("second", [], result=second_result)
                specs = (
                    DailyBatchChild("第一项", First),
                    DailyBatchChild("第二项", Second),
                )
                task, _resets = self.make_task(
                    {First: first, Second: second},
                    specs,
                    {
                        "启用": True,
                        "完成日常后自动关机": switch_on,
                        "第一项": True,
                        "第二项": True,
                    },
                )
                with mock.patch(
                    "src.tasks.DailyBatchTask._schedule_system_shutdown"
                ) as shutdown:
                    DailyBatchTask.run(task)
                shutdown.assert_not_called()

    def test_shutdown_not_scheduled_when_no_enabled_children(self):
        class First:
            pass

        first = _ChildTask("first", [])
        specs = (DailyBatchChild("第一项", First),)
        task, _resets = self.make_task(
            {First: first},
            specs,
            # 全部子开关关闭时一次成功运行没有执行任何日常；
            # 「全部已启用子任务已完成」对空集合为真，但不得触发关机。
            {"启用": True, "完成日常后自动关机": True, "第一项": False},
        )
        with mock.patch(
            "src.tasks.DailyBatchTask._schedule_system_shutdown"
        ) as shutdown:
            self.assertTrue(DailyBatchTask.run(task))
        shutdown.assert_not_called()
        self.assertNotIn("自动关机", task.info.get("状态", ""))

    def test_shutdown_survives_day_boundary_for_pre_completed_children(self):
        class First:
            pass

        first = _ChildTask("first", [])
        specs = (DailyBatchChild("第一项", First),)
        task, _resets = self.make_task(
            {First: first},
            specs,
            {"启用": True, "完成日常后自动关机": True, "第一项": True},
        )

        class BoundaryStore:
            """运行循环内记「今日已完成」，之后重算视为已跨过 04:00 日界。"""

            def __init__(self):
                self.calls = 0

            def is_completed(self, _name, now=None, *, scope=None):
                self.calls += 1
                return self.calls == 1

        set_default_store(BoundaryStore())
        try:
            with mock.patch(
                "src.tasks.DailyBatchTask._schedule_system_shutdown"
            ) as shutdown:
                self.assertTrue(DailyBatchTask.run(task, RUN_MODE_INCOMPLETE))
        finally:
            set_default_store(None)

        # 运行前已完成跳过的子任务不能因关机判定时刻重算翻转为「非今日」而漏关机。
        shutdown.assert_called_once_with(SHUTDOWN_COUNTDOWN_SECONDS)

    def test_shutdown_not_claimed_when_system_rejects_schedule(self):
        class First:
            pass

        first = _ChildTask("first", [])
        specs = (DailyBatchChild("第一项", First),)
        task, _resets = self.make_task(
            {First: first},
            specs,
            {"启用": True, "完成日常后自动关机": True, "第一项": True},
        )
        errors = []
        task.log_error = lambda *args, **kwargs: errors.append(args)
        with mock.patch(
            "src.tasks.DailyBatchTask._schedule_system_shutdown",
            return_value=False,
        ) as shutdown:
            self.assertTrue(DailyBatchTask.run(task))
        shutdown.assert_called_once_with(SHUTDOWN_COUNTDOWN_SECONDS)
        # shutdown.exe 非零退出（如权限拒绝）时不得虚假宣称已安排关机。
        self.assertNotIn("自动关机", task.info.get("状态", ""))
        self.assertTrue(errors)

    def test_schedule_system_shutdown_uses_system32_and_reports_exit_code(self):
        from src.tasks.DailyBatchTask import _schedule_system_shutdown

        for returncode, expected in ((0, True), (5, False)):
            with self.subTest(returncode=returncode):
                completed = SimpleNamespace(returncode=returncode)
                with mock.patch(
                    "src.tasks.DailyBatchTask.subprocess.run",
                    return_value=completed,
                ) as run, mock.patch.dict("os.environ", {"SystemRoot": r"C:\Windows"}):
                    self.assertEqual(
                        expected,
                        _schedule_system_shutdown(SHUTDOWN_COUNTDOWN_SECONDS),
                    )
                command = run.call_args.args[0]
                # 绝对路径调用 System32 的 shutdown.exe，防止安装目录同名顶替。
                self.assertEqual(r"C:\Windows\System32\shutdown.exe", command[0])
                self.assertEqual(["/s", "/t", "60"], command[1:])
                self.assertTrue(run.call_args.kwargs["capture_output"])


if __name__ == "__main__":
    unittest.main()
