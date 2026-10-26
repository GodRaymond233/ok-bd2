"""回归：自动返回主页的房子图像识别、多帧复核与通知降噪。"""

import unittest
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks.BaseBD2Task import TEMPLATE_DIR, BaseBD2Task
from src.tasks.PVPTask import PVPTask
from src.tasks.trigger.AutoLoginTask import AutoLoginTask
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    HOME_GACHA_OCR_RELATIVE_ROI,
    HOME_LEFT_COLUMN_OCR_RELATIVE_ROI,
)
from src.utils.vision_models import MatchResult


class AutoReturnMainHomeTest(unittest.TestCase):
    def test_daily_recovery_confirms_home_during_click_cooldown_without_clicks(self):
        task, clicks = self._task([(True, False)])
        task._auto_return_home_cooldown_until = float("inf")
        self.assertTrue(task.recover_daily_home())
        self.assertEqual([], clicks)

    def test_daily_recovery_propagates_stop_and_finish_during_return(self):
        for exception in (TaskDisabledException, FinishedException):
            with self.subTest(exception=exception):
                task, clicks = self._task([(False, False)])
                task.capture_frame = Mock(side_effect=[task._test_frame, exception()])
                with self.assertRaises(exception):
                    task.recover_daily_home()
                self.assertEqual([], clicks)

    def _task(self, home_results, *, house_match=None, task_type=BaseBD2Task):
        task = object.__new__(task_type)
        task.config = {
            "自动返回主页最大步数": 1,
            "自动返回主页失败冷却秒数": 30.0,
            "返回主页动作确认秒数": 0.0,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task._test_logs = {"info": [], "warning": []}
        task.log_info = lambda message, **kwargs: task._test_logs["info"].append(
            (message, kwargs)
        )
        task.log_warning = lambda message, **kwargs: task._test_logs["warning"].append(
            (message, kwargs)
        )
        task.sleep = lambda *_args, **_kwargs: None
        task._test_captures = []
        task._test_frame = np.zeros((100, 100, 3), dtype=np.uint8)

        def capture_frame():
            task._test_captures.append(True)
            return task._test_frame

        task.capture_frame = capture_frame
        results = iter(home_results)
        task._home_scan = lambda _frame: next(results)
        task._match_house_button = lambda _frame: house_match
        task._click_top_left_back = lambda *_args, **_kwargs: False
        task._clear_dimmed_home_announcement = lambda _frame: False
        task.operate_click = lambda x, y, **kwargs: task._test_clicks.append((x, y))
        task._test_clicks = []
        task._auto_return_home_cooldown_until = 0.0
        return task, task._test_clicks

    def test_transient_gacha_miss_on_home_does_not_click_house(self):
        house = MatchResult(1.0, (40, 20), (20, 20))
        task, clicks = self._task(
            [(False, True), (True, True)],
            house_match=house,
        )
        self.assertTrue(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([], clicks)

    def test_cancellation_propagates_from_capture_and_return_click(self):
        for boundary in ("capture_frame", "operate_click"):
            with self.subTest(boundary=boundary):
                task, clicks = self._task([(False, True)] * 3)
                setattr(task, boundary, Mock(side_effect=TaskDisabledException))
                with self.assertRaises(TaskDisabledException):
                    task.auto_return_main_home()
                self.assertEqual([], clicks)
                self.assertEqual([], task._test_logs["warning"])
                self.assertEqual(0.0, task._auto_return_home_cooldown_until)

    def test_final_step_click_is_confirmed_before_success(self):
        house = MatchResult(0.95, (40, 20), (20, 20))
        task, clicks = self._task(
            [(False, True), (False, True), (False, True), (True, False)],
            house_match=house,
        )
        self.assertTrue(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([(0.5, 0.3)], clicks)

    def test_final_step_click_without_home_confirmation_fails(self):
        house = MatchResult(0.95, (40, 20), (20, 20))
        task, clicks = self._task(
            [(False, True), (False, True), (False, True), (False, False)],
            house_match=house,
        )
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([(0.5, 0.3)], clicks)
        self.assertGreater(task._auto_return_home_cooldown_until, 0.0)

    def test_unstable_house_hint_stops_without_click(self):
        house = MatchResult(0.95, (40, 20), (20, 20))
        task, clicks = self._task(
            [(False, True), (False, False)],
            house_match=house,
        )
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([], clicks)

    def test_final_failure_notifies_once_then_enters_cooldown(self):
        task, _clicks = self._task([(False, False)])
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual(1, len(task._test_captures))
        self.assertEqual(1, len(task._test_logs["warning"]))
        self.assertTrue(task._test_logs["warning"][0][1].get("notify"))

        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual(1, len(task._test_captures))
        self.assertEqual(1, len(task._test_logs["warning"]))

    def test_failure_notification_can_be_left_to_outer_retry_loop(self):
        task, _clicks = self._task([(False, False)])
        self.assertFalse(
            BaseBD2Task.auto_return_main_home(task, notify_failure=False)
        )
        self.assertEqual(1, len(task._test_logs["warning"]))
        self.assertFalse(task._test_logs["warning"][0][1].get("notify"))

    def test_h_ocr_missing_but_house_image_match_is_positive_signal(self):
        task = object.__new__(BaseBD2Task)
        task.config = {}
        task.info_set = lambda *_args: None
        task._ocr_boxes = lambda *_args, **_kwargs: []
        task._read_home_confirmation_signals = lambda *_args: (False, 0, 0.0, "")
        house = MatchResult(0.95, (40, 20), (20, 20))
        task._match_house_button = lambda _frame: house
        self.assertEqual(
            (False, True),
            BaseBD2Task._home_scan(task, np.zeros((100, 100, 3), dtype=np.uint8)),
        )

    def test_home_signal_skips_house_matching(self):
        for task_type, (width, height) in product(
            (BaseBD2Task, PVPTask, AutoLoginTask), ((1920, 1080), (1280, 720)),
        ):
            for brightness, left, gacha, expected in (
                (255, "我的小屋 格鲁TALK 街机游戏", "抽抽乐", True),
                (80, "我的小屋 格鲁TALK 街机游戏", "抽抽乐", False),
                (255, "我的小屋", "抽抽乐", False),
                (255, "我的小屋 格鲁TALK", "", False),
                (255, "", "", False),
            ):
                with self.subTest(
                    task=task_type.__name__, size=(width, height),
                    brightness=brightness, left=left, gacha=gacha,
                ):
                    task, clicks = self._task([], task_type=task_type)
                    del task._home_scan
                    frame = np.full((height, width, 3), brightness, dtype=np.uint8)
                    reads = []

                    def read_text(input_frame, _name, *, relative_roi, **_kwargs):
                        self.assertIs(frame, input_frame)
                        reads.append(relative_roi)
                        return left if relative_roi == HOME_LEFT_COLUMN_OCR_RELATIVE_ROI else gacha

                    task._quick_vision = lambda: SimpleNamespace(ocr_text=read_text)
                    task._ocr_boxes = Mock(return_value=[SimpleNamespace(name="抽抽乐 我的小屋")])
                    task._match_house_button = Mock(return_value=None)
                    self.assertEqual((expected, False), task._home_scan(frame))
                    self.assertEqual(HOME_LEFT_COLUMN_OCR_RELATIVE_ROI, reads[0])
                    self.assertTrue(all(roi == HOME_GACHA_OCR_RELATIVE_ROI for roi in reads[1:]))
                    self.assertEqual([], clicks)
                    self.assertEqual(0 if expected else 1, task._match_house_button.call_count)

    def test_dimmed_home_cleanup_requires_fresh_bright_confirmation(self):
        for clears in (True, False):
            with self.subTest(clears=clears):
                task, clicks = self._task([])
                del task._home_scan
                del task._clear_dimmed_home_announcement
                dark = np.full((1080, 1920, 3), 80, dtype=np.uint8)
                bright = np.full_like(dark, 255)
                task.capture_frame = Mock(side_effect=[dark, bright if clears else dark])
                def read_text(_frame, _name, *, relative_roi, **_kwargs):
                    if relative_roi == HOME_LEFT_COLUMN_OCR_RELATIVE_ROI:
                        return "我的小屋 格鲁TALK"
                    return "抽抽乐"

                task._quick_vision = lambda: SimpleNamespace(ocr_text=read_text)
                task._ocr_boxes = lambda *_args, **_kwargs: []
                task._sleep_after_recognition = lambda: None
                self.assertEqual(clears, task.auto_return_main_home())
                self.assertEqual([HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT], clicks)
                self.assertEqual(2, task.capture_frame.call_count)


class ReturnHomeHouseTemplateTest(unittest.TestCase):
    def _task(self):
        task = object.__new__(BaseBD2Task)
        task.config = {"返回主页房子模板阈值": 0.85}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task._central_template_cache = {}
        return task

    @staticmethod
    def _synthetic_frame(width, height):
        asset = cv2.imread(
            str(Path(TEMPLATE_DIR) / "return_home_house_button.png"),
            cv2.IMREAD_UNCHANGED,
        )
        scale = width / 1920.0
        resized = cv2.resize(
            asset[:, :, :3],
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        expected_center = (round(width * 0.94), round(height * 0.05))
        left = expected_center[0] - resized.shape[1] // 2
        top = expected_center[1] - resized.shape[0] // 2
        frame[
            top : top + resized.shape[0],
            left : left + resized.shape[1],
        ] = resized
        return frame, expected_center

    def test_matches_at_2560_1920_1280(self):
        for width, height in ((2560, 1440), (1920, 1080), (1280, 720)):
            with self.subTest(width=width, height=height):
                task = self._task()
                frame, expected_center = self._synthetic_frame(width, height)
                result = BaseBD2Task._match_house_button(task, frame)
                self.assertIsNotNone(result)
                actual_center = (
                    result.position[0] + result.size[0] // 2,
                    result.position[1] + result.size[1] // 2,
                )
                self.assertLessEqual(abs(actual_center[0] - expected_center[0]), 2)
                self.assertLessEqual(abs(actual_center[1] - expected_center[1]), 2)

    def test_blank_frame_does_not_match(self):
        task = self._task()
        self.assertIsNone(
            BaseBD2Task._match_house_button(
                task,
                np.zeros((1080, 1920, 3), dtype=np.uint8),
            )
        )


if __name__ == "__main__":
    unittest.main()
