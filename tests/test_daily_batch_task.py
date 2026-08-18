import tempfile
import unittest
from types import SimpleNamespace

from src.config import config
from src.tasks.DailyBatchTask import (
    RUN_MODE_INCOMPLETE,
    DailyBatchChild,
    DailyBatchTask,
)
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.run_history import RunHistoryStore, set_default_store
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

    def test_config_hides_map_collection_and_exposes_remaining_children(self):
        executor = SimpleNamespace(scene=None)
        task = DailyBatchTask(executor, SimpleNamespace())
        expected = [
            "公会、小屋、酒馆",
            "快速狩猎",
            "免费抽抽乐",
            "广场女神像",
            "自动PVP",
            "跑商",
        ]
        self.assertEqual(expected, task.config_type["启用"]["sub_configs"][True])
        self.assertTrue(all(task.default_config[key] for key in expected))
        self.assertNotIn("跑图", task.default_config)
        self.assertNotIn("跑图", task.description)

    def test_formal_weekly_map_collection_card_is_registered_but_hidden(self):
        executor = SimpleNamespace(scene=None)
        task = MapCollectionTask(executor, SimpleNamespace(debug=False))

        self.assertFalse(task.visible)
        self.assertEqual("日常/周常", task.group_name)
        self.assertIn(
            ["src.tasks.MapCollectionTask", "MapCollectionTask"],
            config["onetime_tasks"],
        )

    def test_debug_weekly_map_collection_card_moves_to_internal_testing_group(self):
        executor = SimpleNamespace(scene=None)
        task = MapCollectionTask(executor, SimpleNamespace(debug=True))

        self.assertTrue(task.visible)
        self.assertEqual("内测功能", task.group_name)

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


if __name__ == "__main__":
    unittest.main()
