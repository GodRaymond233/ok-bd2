import unittest
from unittest.mock import Mock, patch

from src.tasks.PVPTask import (
    PVP_AUTO_BATTLE_CANCEL_SCREEN_ROI,
    PVP_AUTO_BATTLE_CLICK_REFERENCE,
    PVPTask,
)
from src.utils.vision_models import MatchResult


class PVPCocktailTest(unittest.TestCase):
    def task(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = Mock()
        task.log_info = Mock()
        task.log_warning = Mock()
        task._save_flow_diagnostic = Mock()
        task._click_screen_reference = Mock()
        task._click_frame_point = Mock()
        task.capture_frame = Mock()
        task.back = Mock()
        task.sleep = Mock()
        return task

    def test_balance_uses_matching_frames_and_ignores_additional_cocktails(self):
        task = self.task()
        task._ocr_text = Mock(side_effect=["0/40 +7", "0 / 40"])
        self.assertEqual(0, task._confirm_free_ap_balance())
        self.assertEqual(2, task.capture_frame.call_count)
        task._ocr_text.side_effect = ["0/40", "7/40", "无法识别"]
        self.assertIsNone(task._confirm_free_ap_balance())

    def test_zero_free_balance_skips_and_partial_balance_uses_one_multiplier(self):
        for balance in (0, 7):
            with self.subTest(balance=balance):
                task = self.task()
                task._skip_weekly_settlement = Mock(return_value=False)
                task._click_pvp_stage_once = Mock(return_value=True)
                task._wait_for_ocr_patterns = Mock(
                    side_effect=[(True, "自动战斗"), (True, "鲜血鸡尾酒")],
                )
                task._confirm_free_ap_balance = Mock(return_value=balance)
                task._skip_cocktail_battle = Mock(return_value="free_ap_unavailable")
                task._ensure_free_ap_enabled = Mock(return_value=True)
                task._ensure_multiplier = Mock(return_value=True)
                task._select_max_battle_count = Mock()
                task._wait_battle_start_or_ap_shortage = Mock(return_value="started")
                result = task._start_auto_battle(40)
                if balance == 0:
                    self.assertEqual(("free_ap_unavailable", 40), result)
                    task._skip_cocktail_battle.assert_called_once_with("免费鲜血鸡尾酒已耗尽")
                    task._click_screen_reference.assert_called_once_with(
                        *PVP_AUTO_BATTLE_CLICK_REFERENCE, after_sleep=1.0,
                    )
                    task._ensure_free_ap_enabled.assert_not_called()
                    task._wait_battle_start_or_ap_shortage.assert_not_called()
                else:
                    self.assertEqual(("started", 1), result)
                    task._ensure_multiplier.assert_called_once_with(1)
                    task._wait_battle_start_or_ap_shortage.assert_called_once_with(1)

    def test_skip_cancels_popup_before_using_formation_back(self):
        task = self.task()
        menu, formation, hub = object(), object(), object()
        task.capture_frame.side_effect = [menu, formation, hub]
        task._ocr_text = Mock(side_effect=["自动战斗 设置鲜血鸡尾酒消耗量", "", ""])
        task._click_ocr_pattern_center = Mock(return_value=True)
        task._formation_return_arrow = Mock(return_value=(150, 74))
        task._home_scan = Mock(return_value=(False, False))
        task._match = Mock()
        task._passes = Mock(return_value=True)
        task._return_home_from_pvp_hub = Mock(return_value=True)
        task.recover_daily_home = Mock()

        self.assertEqual("free_ap_unavailable", task._skip_cocktail_battle("免费鲜血鸡尾酒已耗尽"))
        cancel_roi = task._screen_reference_roi_to_reference_roi(PVP_AUTO_BATTLE_CANCEL_SCREEN_ROI)
        task._click_ocr_pattern_center.assert_called_once_with(
            [r"^取消$"], "PVP 取消自动战斗", cancel_roi, after_sleep=.8,
        )
        task._formation_return_arrow.assert_called_once_with(formation)
        task._click_frame_point.assert_called_once_with(formation, (150, 74), after_sleep=1.0)
        task._return_home_from_pvp_hub.assert_called_once_with()
        task.recover_daily_home.assert_not_called()
        task.back.assert_not_called()

    def test_fading_menu_cannot_be_mistaken_for_the_formation_page(self):
        task = self.task()
        menu = "自动战斗 设置鲜血鸡尾酒消耗量"
        task._ocr_text = Mock(return_value=menu)
        task._click_ocr_pattern_center = Mock(return_value=True)
        task._formation_return_arrow = Mock()
        task.recover_daily_home = Mock(return_value=False)

        self.assertEqual("home_recovery_failed", task._skip_cocktail_battle("免费鲜血鸡尾酒已耗尽"))
        self.assertEqual(2, task._click_ocr_pattern_center.call_count)
        task._formation_return_arrow.assert_not_called()
        task._click_frame_point.assert_not_called()

    def test_formation_arrow_has_two_page_anchors_even_if_one_label_is_missing(self):
        task = self.task()
        task._match = Mock(return_value=MatchResult(.98, (137, 49), (26, 50)))
        task._passes = Mock(return_value=True)
        task._ocr_text = Mock(side_effect=["1 BATTLE", "阵形", "自动战斗"])
        self.assertEqual((150, 74), task._formation_return_arrow(object()))
        task._ocr_text.side_effect = ["BATTLE", "", ""]
        self.assertIsNone(task._formation_return_arrow(object()))
        task._passes.return_value = False
        self.assertIsNone(task._formation_return_arrow(object()))

    def test_rerun_can_exit_leftover_formation_before_entering_from_home(self):
        task = self.task()
        task._wait_for_template = Mock(return_value=False)
        task._formation_return_arrow = Mock(return_value=(150, 74))
        task._leave_unstarted_battle = Mock(return_value=True)
        task._enter_pvp_from_home = Mock(return_value=True)

        self.assertTrue(task._ensure_pvp_hub())
        task._leave_unstarted_battle.assert_called_once_with()
        task._enter_pvp_from_home.assert_called_once_with()

    def test_unconfirmed_start_only_waits_for_results_if_a_result_is_visible(self):
        result_page = "反复战斗结果 胜利分 已完成1次的战斗 积分变化 斗魂奖牌总获得量"
        for result, expected in ((result_page, "started"), ("自动战斗", "failed")):
            with self.subTest(result=result):
                task = self.task()
                task.config["PVP 战斗开始等待秒数"] = 1
                task._ocr_text = Mock(side_effect=["", "", result])
                with patch("src.tasks.PVPTask.monotonic", side_effect=[0, .4, 2]):
                    self.assertEqual(expected, task._wait_battle_start_or_ap_shortage(40))
