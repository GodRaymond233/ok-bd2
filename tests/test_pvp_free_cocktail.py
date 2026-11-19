"""回归：镜中之战免费鸡尾酒不可用时的跳过处理与非免费开关。"""

import unittest
from unittest.mock import Mock, patch

from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks.PVPTask import (
    PVP_FREE_AP_SWITCH_SCREEN_POINT,
    PVPTask,
)


class PVPFreeCocktailTest(unittest.TestCase):
    def _task(self, **config):
        task = object.__new__(PVPTask)
        task.config = {"启用": True, "竞技场战斗倍数": 1, **config}
        task.info = {}
        task.info_set = lambda key, value: task.info.__setitem__(key, value)
        task.log_info = Mock()
        task.log_warning = Mock()
        task.log_completion = Mock()
        task._auto_return_home_cooldown_until = 0.0
        return task

    def test_allow_paid_defaults_off(self):
        self.assertFalse(self._task()._allow_paid_cocktail())
        self.assertTrue(
            self._task(**{"允许使用非免费鸡尾酒": True})._allow_paid_cocktail()
        )

    # ---- 免费不可用 -> 跳过并回主页 ----

    def test_skip_records_success_only_after_home_recovery(self):
        for recovered, state in ((True, "free_ap_unavailable"), (False, "home_recovery_failed")):
            with self.subTest(recovered=recovered):
                task = self._task()
                task._leave_unstarted_battle = Mock(return_value=recovered)
                task._save_flow_diagnostic = Mock()
                self.assertEqual(state, task._skip_cocktail_battle("免费鸡尾酒不足"))
                task._leave_unstarted_battle.assert_called_once_with()
                self.assertEqual(0.0, task._auto_return_home_cooldown_until)

    def test_home_recovery_error_does_not_report_success(self):
        task = self._task()
        task._leave_unstarted_battle = Mock(side_effect=RuntimeError("capture unavailable"))
        task._save_flow_diagnostic = Mock()
        self.assertEqual("home_recovery_failed", task._skip_cocktail_battle("免费鸡尾酒不足"))
        task.log_completion.assert_not_called()

    def test_home_recovery_propagates_cancellation(self):
        for exception in (TaskDisabledException, FinishedException):
            with self.subTest(exception=exception):
                task = self._task()
                task._leave_unstarted_battle = Mock(side_effect=exception)
                with self.assertRaises(exception):
                    task._skip_cocktail_battle("免费鸡尾酒不足")

    def test_run_treats_free_ap_unavailable_as_skip(self):
        task = self._task()
        task._skip_weekly_settlement = Mock(return_value=False)
        task._target_multiplier = Mock(return_value=1)
        task._ensure_pvp_hub = Mock(return_value=True)
        task._start_auto_battle = Mock(return_value=("free_ap_unavailable", 1))
        task._wait_result_and_leave = Mock()
        self.assertTrue(task.run())
        task._wait_result_and_leave.assert_not_called()

    def test_run_fails_after_unsuccessful_skip_recovery(self):
        task = self._task()
        task._skip_weekly_settlement = Mock(return_value=False)
        task._target_multiplier = Mock(return_value=1)
        task._ensure_pvp_hub = Mock(return_value=True)
        task._start_auto_battle = Mock(return_value=("home_recovery_failed", 1))
        task._wait_result_and_leave = Mock()
        self.assertFalse(task.run())
        task._wait_result_and_leave.assert_not_called()
        task.log_completion.assert_not_called()

    # ---- 允许非免费鸡尾酒 ----

    def test_free_ap_disabled_when_switch_already_off(self):
        task = self._task()
        task._free_ap_switch_on = Mock(return_value=False)
        task._click_screen_reference = Mock()
        self.assertTrue(task._ensure_free_ap_disabled())
        task._click_screen_reference.assert_not_called()

    def test_free_ap_disabled_clicks_once_when_switch_on(self):
        task = self._task()
        task._free_ap_switch_on = Mock(side_effect=[True, False])
        task._click_screen_reference = Mock()
        self.assertTrue(task._ensure_free_ap_disabled())
        task._click_screen_reference.assert_called_once_with(
            *PVP_FREE_AP_SWITCH_SCREEN_POINT, after_sleep=1.0
        )

    def test_free_ap_disabled_does_not_abort_when_switch_stuck(self):
        task = self._task()
        task._free_ap_switch_on = Mock(return_value=True)
        task._click_screen_reference = Mock()
        self.assertFalse(task._ensure_free_ap_disabled())
        self.assertEqual(3, task._click_screen_reference.call_count)

    # ---- _start_auto_battle 的两条分派路径 ----

    def _start_task(self, **config):
        task = self._task(**config)
        task._skip_weekly_settlement = Mock(return_value=False)
        task._click_pvp_stage_once = Mock(return_value=True)
        task._wait_for_ocr_patterns = Mock(return_value=(True, "鲜血鸡尾酒"))
        task._click_screen_reference = Mock()
        task._click_ocr_pattern_center = Mock(return_value=True)
        task._mf_roi = Mock(return_value=(0, 0, 1, 1))
        task._confirm_free_ap_balance = Mock(return_value=None)
        task._ensure_free_ap_enabled = Mock(return_value=True)
        task._ensure_free_ap_disabled = Mock(return_value=True)
        task._ensure_multiplier = Mock(return_value=True)
        task._select_max_battle_count = Mock()
        task._skip_cocktail_battle = Mock(
            return_value="free_ap_unavailable"
        )
        task._wait_battle_start_or_ap_shortage = Mock(return_value="started")
        return task

    def test_paid_mode_skips_free_switch_and_continues(self):
        task = self._start_task(**{"允许使用非免费鸡尾酒": True})
        with patch("src.tasks.PVPTask.pvp_settlement_end_ts", return_value=None):
            self.assertEqual(("started", 1), task._start_auto_battle(1))
        task._ensure_free_ap_disabled.assert_called_once_with()
        task._ensure_free_ap_enabled.assert_not_called()
        task._skip_cocktail_battle.assert_not_called()
        task._ensure_multiplier.assert_called_once_with(1)

    def test_free_mode_skips_when_switch_cannot_be_enabled(self):
        task = self._start_task()
        task._ensure_free_ap_enabled = Mock(return_value=False)
        with patch("src.tasks.PVPTask.pvp_settlement_end_ts", return_value=None):
            self.assertEqual(("free_ap_unavailable", 1), task._start_auto_battle(1))
        task._skip_cocktail_battle.assert_called_once_with("未能开启仅用免费鸡尾酒")
        task._ensure_free_ap_disabled.assert_not_called()
        task._ensure_multiplier.assert_not_called()


if __name__ == "__main__":
    unittest.main()
