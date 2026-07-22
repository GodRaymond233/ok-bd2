import unittest
from types import SimpleNamespace

from src.config import config
from src.tasks.DailyBatchTask import DailyBatchChild, DailyBatchTask


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
        task.info_set = lambda *_args, **_kwargs: None
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

    def test_config_exposes_seven_child_switches_in_requested_order(self):
        executor = SimpleNamespace(scene=None)
        task = DailyBatchTask(executor, SimpleNamespace())
        expected = [
            "公会、小屋、酒馆",
            "快速狩猎",
            "免费抽抽乐",
            "广场女神像",
            "自动PVP",
            "跑图",
            "跑商",
        ]
        self.assertEqual(expected, task.config_type["启用"]["sub_configs"][True])
        self.assertTrue(all(task.default_config[key] for key in expected))

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


if __name__ == "__main__":
    unittest.main()
