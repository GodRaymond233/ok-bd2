import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.interaction.BD2Interaction import InputTargetChanged
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.quick_hunt import QuickHuntFeatureMixin
from src.utils.home_confirmation import modal_panel_bbox


class HomePopupRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.task = object.__new__(BaseBD2Task)
        self.task.info_set = Mock()
        self.task.log_info = Mock()
        self.task.operate_click = Mock()
        self.task.sleep = Mock()
        self.frame = np.full((540, 960, 3), 24, dtype=np.uint8)
        self.frame[130:420, 240:750] = 210
        self.assertIsNotNone(modal_panel_bbox(self.frame))

    def test_clicks_the_dark_margin_once_even_if_panel_bounds_shift(self):
        self.assertTrue(self.task.dismiss_home_modal_popup(self.frame))
        shifted = np.roll(self.frame, 2, axis=1)
        self.assertFalse(self.task.dismiss_home_modal_popup(shifted))
        self.task.operate_click.assert_called_once_with(
            .01, .50, name="主页弹窗关闭", after_sleep=.6, require_fresh_target=True,
        )

    def test_margin_with_visible_ui_is_not_clicked(self):
        self.frame[260:280, 7:13] = 220
        self.assertFalse(self.task.dismiss_home_modal_popup(self.frame))
        self.task.operate_click.assert_not_called()

    def test_recognized_cross_takes_priority_over_the_dark_margin(self):
        with patch("src.tasks.BaseBD2Task.modal_close_point", return_value=(.75, .25)):
            self.assertTrue(self.task.dismiss_home_modal_popup(self.frame))
        self.assertEqual((.75, .25), self.task.operate_click.call_args.args)

    def test_cancelled_click_leaves_room_to_retry_using_a_fresh_frame(self):
        self.task.operate_click.side_effect = [InputTargetChanged(), None]
        self.assertTrue(self.task.dismiss_home_modal_popup(self.frame))
        self.assertTrue(self.task.dismiss_home_modal_popup(self.frame.copy()))
        self.assertEqual(2, self.task.operate_click.call_count)


class QuickHuntInputRecoveryTest(unittest.TestCase):
    @staticmethod
    def box(x):
        return SimpleNamespace(name="光之洞穴", x=x, y=10, width=20, height=10)

    def setUp(self):
        self.task = QuickHuntFeatureMixin()
        self.vision = SimpleNamespace(ocr_boxes=Mock(), click_client=Mock())
        self.task._quick_vision = lambda: self.vision
        self.task._normalize_text = lambda text: text
        self.task._save_flow_diagnostic = Mock()
        self.task.log_info = Mock()
        self.frames = [np.zeros((300, 600, 3), dtype=np.uint8) for _ in range(2)]
        self.task.capture_frame = Mock(side_effect=self.frames)
        self.task._active_time = Mock(side_effect=[0.0, 0.0, 0.4, 1.1])
        self.task.sleep = Mock()

    def test_wait_then_uses_new_ocr_point_without_reusing_the_old_frame(self):
        self.vision.ocr_boxes.side_effect = [[self.box(40), self.box(90)], [self.box(120)]]
        self.vision.click_client.side_effect = [InputTargetChanged(), None]

        self.assertTrue(self.task._quick_hunt_click_ocr(["光之洞穴"], None, 1, "洞穴入口"))
        self.assertEqual(2, self.task.capture_frame.call_count)
        self.assertEqual(
            [
                call((50, 15), self.frames[0].shape, after_sleep=.8),
                call((130, 15), self.frames[1].shape, after_sleep=.8),
            ],
            self.vision.click_client.call_args_list,
        )

    def test_wait_then_stops_if_the_original_control_disappears(self):
        self.vision.ocr_boxes.side_effect = [[self.box(40)], []]
        self.vision.click_client.side_effect = InputTargetChanged()

        self.assertFalse(self.task._quick_hunt_click_ocr(["光之洞穴"], None, 1, "洞穴入口"))
        self.assertEqual(2, self.task.capture_frame.call_count)
        self.vision.click_client.assert_called_once()
        self.task._save_flow_diagnostic.assert_called_once_with("quick_hunt_button_failed")
