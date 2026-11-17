"""回归：自动返回主页的房子图像识别、多帧复核与通知降噪。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks.BaseBD2Task import SCENE_HOME_MAX_CLICKS, BaseBD2Task
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
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

    def _task(self, home_results, *, house_match=None, scene_points=None, task_type=BaseBD2Task):
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
        points = iter(scene_points) if scene_points is not None else None

        def home_scan(_frame):
            result = next(results)
            task._scene_home_point = next(points) if points is not None else None
            return result

        task._home_scan = home_scan
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
                task, clicks = self._task(
                    [(False, True)] * 3,
                    scene_points=[(0.9363, 0.0535)] * 3,
                )
                setattr(task, boundary, Mock(side_effect=TaskDisabledException))
                with self.assertRaises(TaskDisabledException):
                    task.auto_return_main_home()
                self.assertEqual([], clicks)
                self.assertEqual([], task._test_logs["warning"])
                self.assertEqual(0.0, task._auto_return_home_cooldown_until)

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

    def test_unrecognized_scene_stops_without_keyboard_input(self):
        task, clicks = self._task([(False, False)])
        task.config["自动返回主页最大步数"] = 4
        task.send_key = Mock()
        self.assertFalse(task.auto_return_main_home())
        task.send_key.assert_not_called()
        self.assertEqual([], clicks)
        self.assertEqual(1, len(task._test_captures))

    def test_h_ocr_missing_but_house_image_match_is_positive_signal(self):
        task = object.__new__(BaseBD2Task)
        task.config = {}
        task.info_set = lambda *_args: None
        task._bd2_ocr_boxes = lambda *_args, **_kwargs: []
        task._read_home_confirmation_signals = lambda *_args: (False, 0, 0.0, "")
        house = MatchResult(0.95, (40, 20), (20, 20))
        task._match_house_button = lambda _frame: house
        self.assertEqual(
            (False, True),
            BaseBD2Task._home_scan(task, np.zeros((100, 100, 3), dtype=np.uint8)),
        )

    def test_scene_return_point_is_clicked_when_house_template_misses(self):
        # 格鲁菲餐厅：房子模板 -1.000（字形是空心房子+H，不是主页的房子+齿轮），
        # 只能靠几何判据给出的点退出；点完下一轮必须确认到主页。
        point = (0.9363, 0.0535)
        task, clicks = self._task(
            [(False, True), (False, True), (False, True), (True, False)],
            scene_points=[point] * 4,
        )
        self.assertTrue(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([point], clicks)
        self.assertGreaterEqual(len(task._test_logs["info"]), 1)
        self.assertIn("场景返回控件", task._test_logs["info"][0][0])

    def test_scene_point_instability_stops_without_click(self):
        task, clicks = self._task(
            [(False, True), (False, True), (False, True)],
            scene_points=[(0.9363, 0.0535), (0.9000, 0.0535), (0.9000, 0.0535)],
        )
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual([], clicks)

    def test_scene_return_point_click_cap(self):
        task, clicks = self._task(
            [(False, True)] * 40,
            scene_points=[(0.9363, 0.0535)] * 40,
        )
        task.config["自动返回主页最大步数"] = 12
        self.assertFalse(BaseBD2Task.auto_return_main_home(task))
        self.assertEqual(SCENE_HOME_MAX_CLICKS, len(clicks))
        self.assertTrue(
            any("场景返回控件已点击" in message for message, _ in task._test_logs["warning"])
        )

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
                task._bd2_ocr_boxes = lambda *_args, **_kwargs: []
                task._sleep_after_recognition = lambda: None
                self.assertEqual(clears, task.auto_return_main_home())
                self.assertEqual([HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT], clicks)
                self.assertEqual(2, task.capture_frame.call_count)


if __name__ == "__main__":
    unittest.main()
