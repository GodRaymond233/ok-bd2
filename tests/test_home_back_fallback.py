"""回归：自动返回主页失败后按返回键兜底，以及一键日常的调用链。"""

import unittest
from unittest.mock import Mock

import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.DailyBatchTask import DailyBatchTask


class HomeBackFallbackTest(unittest.TestCase):
    def _task(self, *, home_results=None, return_results=None, presses=3):
        task = object.__new__(BaseBD2Task)
        task.config = {"回主页返回键兜底次数": presses}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = Mock()
        task.log_warning = Mock()
        task.sleep = Mock()
        task.capture_frame = Mock(
            return_value=np.zeros((100, 100, 3), dtype=np.uint8)
        )
        task._auto_return_home_cooldown_until = 0.0
        task.back = Mock()
        task.auto_return_main_home = Mock(side_effect=return_results or [])
        if home_results is None:
            task._home_scan = Mock(return_value=(False, False))
        else:
            task._home_scan = Mock(side_effect=home_results)
        return task

    def test_already_home_does_not_press_back(self):
        task = self._task(home_results=[(True, False)])
        self.assertTrue(task.try_return_home_with_back())
        task.back.assert_not_called()
        task.auto_return_main_home.assert_not_called()

    def test_back_key_then_home_confirmed(self):
        task = self._task(return_results=[False, True])
        self.assertTrue(task.try_return_home_with_back())
        self.assertEqual(2, task.back.call_count)
        self.assertEqual(2, task.auto_return_main_home.call_count)
        # 兜底清掉失败冷却，后续自动返回主页不会被 30 秒冷却挡住。
        self.assertEqual(0.0, task._auto_return_home_cooldown_until)

    def test_stops_at_press_limit(self):
        task = self._task(return_results=[False, False, False, False], presses=3)
        self.assertFalse(task.try_return_home_with_back())
        self.assertEqual(3, task.back.call_count)
        self.assertEqual(3, task.auto_return_main_home.call_count)

    def test_disabled_by_zero_presses(self):
        task = self._task(presses=0)
        self.assertFalse(task.try_return_home_with_back(max_presses=0))
        task.back.assert_not_called()

    def test_cancellation_propagates(self):
        for exception in (TaskDisabledException, FinishedException):
            with self.subTest(exception=exception):
                task = self._task()
                task.back = Mock(side_effect=exception)
                with self.assertRaises(exception):
                    task.try_return_home_with_back()


class DailyBatchFallbackTest(unittest.TestCase):
    def _batch(self, **config):
        batch = object.__new__(DailyBatchTask)
        batch.config = {
            "回主页失败后按返回键": True,
            "回主页返回键最多次数": 2,
            **config,
        }
        batch.log_info = Mock()
        batch.log_warning = Mock()
        return batch

    def test_fallback_invoked_with_configured_press_count(self):
        batch = self._batch()
        child = Mock()
        child.try_return_home_with_back = Mock(return_value=True)
        self.assertTrue(batch._try_home_back_fallback(child, "自动PVP"))
        child.try_return_home_with_back.assert_called_once_with(max_presses=2)

    def test_switch_off_disables_fallback(self):
        batch = self._batch(**{"回主页失败后按返回键": False})
        child = Mock()
        child.try_return_home_with_back = Mock(return_value=True)
        self.assertFalse(batch._try_home_back_fallback(child, "自动PVP"))
        child.try_return_home_with_back.assert_not_called()

    def test_failed_child_uses_fallback_when_recovery_fails(self):
        batch = self._batch()
        child = Mock()
        child.name = "自动PVP"
        child.recover_daily_home = Mock(return_value=False)
        child.try_return_home_with_back = Mock(return_value=True)
        self.assertTrue(batch._recover_failed_child(child))
        child.try_return_home_with_back.assert_called_once_with(max_presses=2)

    def test_failed_child_short_circuits_when_recovery_succeeds(self):
        batch = self._batch()
        child = Mock()
        child.name = "自动PVP"
        child.recover_daily_home = Mock(return_value=True)
        child.try_return_home_with_back = Mock(return_value=True)
        self.assertTrue(batch._recover_failed_child(child))
        child.try_return_home_with_back.assert_not_called()

    def test_recovery_cancellation_propagates(self):
        batch = self._batch()
        child = Mock()
        child.recover_daily_home = Mock(side_effect=TaskDisabledException)
        with self.assertRaises(TaskDisabledException):
            batch._recover_failed_child(child)


if __name__ == "__main__":
    unittest.main()
