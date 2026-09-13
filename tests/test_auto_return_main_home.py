"""回归：自动返回主页的房子图像识别、多帧复核与通知降噪。"""

import unittest
from pathlib import Path

import cv2
import numpy as np

from src.tasks.BaseBD2Task import TEMPLATE_DIR, BaseBD2Task
from src.utils.vision_models import MatchResult


class AutoReturnMainHomeTest(unittest.TestCase):
    def _task(self, home_results, *, house_match=None):
        task = object.__new__(BaseBD2Task)
        task.config = {
            "自动返回主页最大步数": 1,
            "自动返回主页失败冷却秒数": 30.0,
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

    def test_click_uses_house_match_center_after_stable_rechecks(self):
        house = MatchResult(0.95, (40, 20), (20, 20))
        task, clicks = self._task(
            [(False, True), (False, True), (False, True)],
            house_match=house,
        )
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([(0.5, 0.3)], clicks)

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
        task._ocr_boxes = lambda *_args, **_kwargs: []
        house = MatchResult(0.95, (40, 20), (20, 20))
        task._match_house_button = lambda _frame: house
        self.assertEqual(
            (False, True),
            BaseBD2Task._home_scan(task, np.zeros((100, 100, 3), dtype=np.uint8)),
        )

    def test_home_signal_skips_house_matching(self):
        class Box:
            def __init__(self, name):
                self.name = name
                self.x = 1
                self.y = 1
                self.width = 2
                self.height = 2

        task = object.__new__(BaseBD2Task)
        task._ocr_boxes = lambda *_args, **_kwargs: [
            Box("抽抽乐"),
            Box("我的小屋"),
        ]
        task._match_house_button = lambda _frame: self.fail(
            "home frame must not run house matching"
        )
        self.assertEqual(
            (True, False),
            BaseBD2Task._home_scan(task, np.zeros((100, 100, 3), dtype=np.uint8)),
        )


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
