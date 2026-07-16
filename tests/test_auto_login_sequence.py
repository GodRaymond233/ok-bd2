import unittest
from dataclasses import replace
from time import monotonic

import numpy as np

from src.tasks.trigger.AutoLoginTask import (
    BROWNDUSTX_TEMPLATE,
    CONFIRM_TEMPLATE,
    HOME_BUTTON_TEMPLATE,
    HOME_BUTTON_TEMPLATES,
    LOADING_TEMPLATE,
    TOUCH_TO_START_TEMPLATE,
    AutoLoginTask,
    MatchResult,
)


class AutoLoginSequenceTest(unittest.TestCase):
    def _task(self):
        task = object.__new__(AutoLoginTask)
        task.config = {
            "BrownDustX 阈值": 0.82,
            "BrownDustX 像素阈值": 0.86,
            "BrownDustX Confirm 阈值": 0.82,
            "BrownDustX Confirm 像素阈值": 0.86,
            "TOUCH TO START 阈值": 0.78,
            "加载页面阈值": 0.72,
            "小屋按钮阈值": 0.78,
            "小屋按钮遮挡阈值": 0.62,
            "小屋亮度比例阈值": 0.75,
            "主页 UI 等待宽限秒数": 15.0,
            "小屋按钮点击 X 百分比": 8.6979,
            "小屋按钮点击 Y 百分比": 14.3519,
            "公告清理点击 X 百分比": 8.8020833333,
            "公告清理点击 Y 百分比": 56.9444444444,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task._home_bright_since = None
        task._login_clicked_at = None
        task._waiting_home_since = None
        task._last_clear_click_at = 0.0
        task._finished = False
        return task

    def test_waiting_loading_checks_home_before_loading(self):
        task = self._task()
        task._state = "waiting_loading"
        task._login_clicked_at = monotonic()
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            return MatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match

        AutoLoginTask._wait_loading_then_home(
            task,
            np.zeros((10, 10, 3), dtype=np.uint8),
        )

        self.assertEqual(
            [spec.name for spec in HOME_BUTTON_TEMPLATES] + [LOADING_TEMPLATE.name],
            calls,
        )
        self.assertEqual("waiting_home", task._state)

    def test_home_has_priority_over_loading(self):
        task = self._task()
        task._state = "loading"
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            if spec is HOME_BUTTON_TEMPLATE:
                return MatchResult(0.9, 0.9, (0, 0), (1, 1))
            if spec is LOADING_TEMPLATE:
                self.fail("loading should not be checked after home is found")
            return MatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._clear_popups_until_home = lambda *_args, **_kwargs: False

        AutoLoginTask._wait_loading_then_home(
            task,
            np.zeros((10, 10, 3), dtype=np.uint8),
        )

        self.assertEqual([spec.name for spec in HOME_BUTTON_TEMPLATES], calls)

    def test_browndustx_branch_checks_confirm_and_touch_only(self):
        task = self._task()
        task._state = "waiting"
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._record_browndustx_text = lambda *_args, **_kwargs: None
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            if spec is BROWNDUSTX_TEMPLATE:
                return MatchResult(0.9, 0.9, (0, 0), (1, 1))
            if spec is CONFIRM_TEMPLATE:
                return MatchResult(-1.0, -1.0, (0, 0), (0, 0))
            if spec is TOUCH_TO_START_TEMPLATE:
                return MatchResult(-1.0, -1.0, (0, 0), (0, 0))
            self.fail(f"unexpected match: {spec.name}")

        task._match = fake_match

        AutoLoginTask.run(task)

        self.assertEqual(
            [
                BROWNDUSTX_TEMPLATE.name,
                CONFIRM_TEMPLATE.name,
                TOUCH_TO_START_TEMPLATE.name,
            ],
            calls,
        )

    def test_finished_task_is_not_scheduled_or_captured_again(self):
        task = self._task()
        task._finished = True
        task.trigger_interval = 0
        task.capture_frame = lambda: self.fail("finished auto-login must not capture")

        self.assertFalse(AutoLoginTask.should_trigger(task))
        self.assertFalse(AutoLoginTask.run(task))

    def test_unfinished_task_remains_schedulable(self):
        task = self._task()
        task.trigger_interval = 0

        self.assertTrue(AutoLoginTask.should_trigger(task))

    def test_browndustx_pixel_match_keeps_confirm_detection_active(self):
        task = self._task()
        task._state = "waiting"
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._record_browndustx_text = lambda *_args, **_kwargs: None
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            if spec is BROWNDUSTX_TEMPLATE:
                return MatchResult(0.54, 0.96, (0, 0), (1, 1))
            if spec is CONFIRM_TEMPLATE:
                return MatchResult(-1.0, -1.0, (0, 0), (0, 0))
            if spec is TOUCH_TO_START_TEMPLATE:
                return MatchResult(-1.0, -1.0, (0, 0), (0, 0))
            self.fail(f"unexpected match: {spec.name}")

        task._match = fake_match

        AutoLoginTask.run(task)

        self.assertEqual(
            [
                BROWNDUSTX_TEMPLATE.name,
                CONFIRM_TEMPLATE.name,
                TOUCH_TO_START_TEMPLATE.name,
            ],
            calls,
        )

    def test_browndustx_confirm_clicks_detected_button_center(self):
        task = self._task()
        task._state = "waiting"
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._record_browndustx_text = lambda *_args, **_kwargs: None
        task._is_browndustx_confirm = lambda _frame, _confirm: True
        task._sleep_after_recognition = lambda: None
        clicks = []
        confirm = MatchResult(0.9, 0.9, (1000, 800), (240, 80))

        def fake_match(_frame, spec):
            if spec is BROWNDUSTX_TEMPLATE:
                return MatchResult(0.9, 0.9, (0, 0), (1, 1))
            if spec is CONFIRM_TEMPLATE:
                return confirm
            return MatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))

        AutoLoginTask.run(task)

        self.assertEqual([(1120, 840, 1.0)], clicks)

    def test_home_button_templates_use_720p_assets_and_green_mask(self):
        task = self._task()
        task._templates = {}
        task._template_masks = {}

        for original_spec in HOME_BUTTON_TEMPLATES[1:]:
            spec = replace(original_spec, green_mask=False)
            self.assertTrue(spec.file_name.startswith("image/green/"))
            template = AutoLoginTask._load_template(task, spec)
            mask = AutoLoginTask._load_template_mask(task, spec)
            self.assertEqual(template.shape, mask.shape)
            self.assertGreater(mask.size, int(np.count_nonzero(mask)))

    def test_waiting_home_clicks_notice_clear_position_after_grace(self):
        task = self._task()
        task._state = "waiting_home"
        task._waiting_home_since = monotonic() - 20.0
        task._home_brightness_ratio = lambda _frame: 0.235
        task._sleep_after_recognition = lambda: None
        clicks = []

        def fake_match(_frame, spec):
            if spec in HOME_BUTTON_TEMPLATES:
                return MatchResult(0.72, 0.72, (120, 130), (90, 90))
            if spec is LOADING_TEMPLATE:
                return MatchResult(-1.0, -1.0, (0, 0), (0, 0))
            self.fail(f"unexpected match: {spec.name}")

        task._match = fake_match
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task.send_key = lambda *_args, **_kwargs: self.fail("popup clearing must not send keys")

        AutoLoginTask._wait_loading_then_home(
            task,
            np.zeros((1440, 2560, 3), dtype=np.uint8),
        )

        self._assert_notice_clear_click(clicks)
        self.assertEqual("clearing", task._state)

    def test_clearing_keeps_clicking_dimmed_home_without_rewaiting(self):
        task = self._task()
        task._state = "clearing"
        task._home_brightness_ratio = lambda _frame: 0.235
        task._sleep_after_recognition = lambda: None
        clicks = []
        task._match = lambda _frame, _spec: MatchResult(0.72, 0.72, (120, 130), (90, 90))
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task.send_key = lambda *_args, **_kwargs: self.fail("popup clearing must not send keys")

        AutoLoginTask._clear_popups_until_home(
            task,
            np.zeros((1440, 2560, 3), dtype=np.uint8),
        )

        self._assert_notice_clear_click(clicks)
        self.assertEqual("clearing", task._state)

    def test_clearing_keeps_clicking_when_dimmed_home_match_flickers_low(self):
        task = self._task()
        task._state = "clearing"
        task._home_bright_since = monotonic()
        task._home_brightness_ratio = lambda _frame: 0.235
        task._sleep_after_recognition = lambda: None
        clicks = []
        task._match = lambda _frame, _spec: MatchResult(0.40, 0.40, (120, 130), (90, 90))
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task.send_key = lambda *_args, **_kwargs: self.fail("popup clearing must not send keys")

        AutoLoginTask._clear_popups_until_home(
            task,
            np.zeros((1440, 2560, 3), dtype=np.uint8),
        )

        self._assert_notice_clear_click(clicks)
        self.assertEqual("clearing", task._state)
        self.assertIsNone(task._home_bright_since)

    def _assert_notice_clear_click(self, clicks):
        self.assertEqual(1, len(clicks))
        x, y, after_sleep = clicks[0]
        self.assertAlmostEqual(169 / 1920, x)
        self.assertAlmostEqual(615 / 1080, y)
        self.assertEqual(0.2, after_sleep)


if __name__ == "__main__":
    unittest.main()
