import unittest
from time import monotonic

import numpy as np

from src.tasks.trigger.AutoLoginTask import (
    BROWNDUSTX_TEMPLATE,
    CONFIRM_TEMPLATE,
    HOME_BUTTON_TEMPLATE,
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
            "主页 UI 等待宽限秒数": 15.0,
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

        self.assertEqual([HOME_BUTTON_TEMPLATE.name, LOADING_TEMPLATE.name], calls)
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

        self.assertEqual([HOME_BUTTON_TEMPLATE.name], calls)

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


if __name__ == "__main__":
    unittest.main()
