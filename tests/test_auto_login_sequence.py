import threading
import unittest
from time import monotonic
from types import SimpleNamespace

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
            "BrownDustX OCR 阈值": 0.2,
            "TOUCH TO START 阈值": 0.78,
            "加载页面阈值": 0.72,
            "小屋按钮阈值": 0.78,
            "小屋按钮遮挡阈值": 0.62,
            "小屋亮度比例阈值": 0.75,
            "主页 UI 等待宽限秒数": 15.0,
            "登录后主页总等待秒数": 300.0,
            "登录超时重试间隔秒数": 60.0,
            "小屋按钮点击 X 百分比": 8.6979,
            "小屋按钮点击 Y 百分比": 14.3519,
            "公告清理点击 X 百分比": 8.8020833333,
            "公告清理点击 Y 百分比": 56.9444444444,
            "登录按钮点击 X 百分比": 72.2396,
            "登录按钮点击 Y 百分比": 65.0926,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._save_flow_diagnostic = lambda _name: None
        task.sleep = lambda *_args, **_kwargs: None
        task.ocr = lambda *_args, **_kwargs: []
        task._home_bright_since = None
        task._login_clicked_at = None
        task._waiting_home_since = None
        task._login_retry_not_before = 0.0
        task._last_clear_click_at = 0.0
        task._last_confirm_click_at = 0.0
        task._last_download_click_at = 0.0
        task._finished = False
        task._executor = SimpleNamespace(get_task_by_class=lambda _cls: None)
        task._home_brightness_ratio = lambda _frame: 0.0
        task._home_gacha_ocr_text = lambda _frame: ""
        return task

    def test_waiting_loading_checks_home_before_loading(self):
        task = self._task()
        task._state = "waiting_loading"
        task._login_clicked_at = monotonic()
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)

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

    def test_browndustx_branch_checks_confirm_and_touch_only(self):
        task = self._task()
        task._state = "waiting"
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            if spec is BROWNDUSTX_TEMPLATE:
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            if spec is CONFIRM_TEMPLATE:
                return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)
            if spec is TOUCH_TO_START_TEMPLATE:
                return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)
            if spec in HOME_BUTTON_TEMPLATES:
                return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)
            self.fail(f"unexpected match: {spec.name}")

        task._match = fake_match

        AutoLoginTask.run(task)

        self.assertEqual(
            [
                spec.name for spec in HOME_BUTTON_TEMPLATES
            ] + [
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

    def test_waiting_task_detects_existing_home_and_stops_after_confirmation(self):
        task = self._task()
        task._state = "waiting"
        task.trigger_interval = 0
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._home_brightness_ratio = lambda _frame: 1.0
        task._home_gacha_ocr_text = lambda _frame: "抽抽乐"
        logged_in = []
        task.mark_logged_in = lambda: logged_in.append(True)

        def fake_match(_frame, spec):
            if spec in (BROWNDUSTX_TEMPLATE, TOUCH_TO_START_TEMPLATE):
                return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)
            if spec is HOME_BUTTON_TEMPLATE:
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            if spec in HOME_BUTTON_TEMPLATES:
                return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)
            self.fail(f"unexpected match: {spec.name}")

        task._match = fake_match

        AutoLoginTask.run(task)

        self.assertEqual("done", task._state)
        self.assertEqual([True], logged_in)
        self.assertTrue(task._finished)
        self.assertFalse(AutoLoginTask.should_trigger(task))

    def test_waiting_task_does_not_accept_home_without_gacha_ocr(self):
        task = self._task()
        task._state = "waiting"
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._home_brightness_ratio = lambda _frame: 1.0

        def fake_match(_frame, spec):
            if spec is HOME_BUTTON_TEMPLATE:
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)

        task._match = fake_match

        AutoLoginTask.run(task)

        self.assertEqual("waiting", task._state)
        self.assertFalse(task._finished)

    def test_confirm_is_checked_even_when_browndustx_parent_does_not_match(self):
        task = self._task()
        task._state = "waiting"
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._is_browndustx_confirm = lambda _frame, _confirm: True
        task._sleep_after_recognition = lambda: None
        clicks = []
        confirm = MatchResult(0.99, (650, 685), (606, 76), pixel_score=0.99)

        def fake_match(_frame, spec):
            if spec is CONFIRM_TEMPLATE:
                return confirm
            return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)

        task._match = fake_match
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))

        AutoLoginTask.run(task)

        self.assertEqual([(953, 723, 1.0)], clicks)
        self.assertEqual("waiting_update", task._state)

    def test_update_prompt_clicks_download_box_right_of_cancel(self):
        task = self._task()
        task._state = "waiting_update"
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._sleep_after_recognition = lambda: None
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task.ocr = lambda *_args, **_kwargs: [
            self._ocr_box("下载", 930, 300, 65, 38),
            self._ocr_box("将下载游戏所需数据。", 850, 360, 200, 30),
            self._ocr_box("下载容量", 680, 560, 90, 30),
            self._ocr_box("可用空间 224,592 MB", 825, 680, 270, 34),
            self._ocr_box("取消", 850, 755, 50, 32),
            self._ocr_box("下载", 1025, 755, 48, 32),
        ]
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))

        AutoLoginTask.run(task)

        self.assertEqual([(1049, 771, 1.0)], clicks)
        self.assertEqual("downloading", task._state)

    def test_update_prompt_does_not_click_download_title_without_cancel_pair(self):
        task = self._task()
        boxes = [
            self._ocr_box("下载", 930, 300, 65, 38),
            self._ocr_box("下载容量", 680, 560, 90, 30),
            self._ocr_box("可用空间 224,592 MB", 825, 680, 270, 34),
        ]

        self.assertIsNone(AutoLoginTask._find_update_download_button(task, boxes))

    def test_update_prompt_requires_capacity_and_available_space_context(self):
        task = self._task()
        boxes = [
            self._ocr_box("下载容量", 680, 560, 90, 30),
            self._ocr_box("取消", 850, 755, 50, 32),
            self._ocr_box("下载", 1025, 755, 48, 32),
        ]

        self.assertIsNone(AutoLoginTask._find_update_download_button(task, boxes))

    def test_download_progress_waits_without_clicking(self):
        task = self._task()
        task._state = "downloading"
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task.ocr = lambda *_args, **_kwargs: [
            self._ocr_box("正在下载", 100, 960, 90, 30),
            self._ocr_box("0.11%", 1740, 965, 75, 30),
        ]
        clicks = []
        task.operate_click = lambda *args, **kwargs: clicks.append((args, kwargs))

        AutoLoginTask.run(task)

        self.assertEqual([], clicks)
        self.assertEqual("downloading", task._state)

    def test_touch_to_start_resumes_after_download_finishes(self):
        task = self._task()
        task._state = "downloading"
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._sleep_after_recognition = lambda: None

        def fake_match(_frame, spec):
            if spec is TOUCH_TO_START_TEMPLATE:
                return MatchResult(0.92, (700, 600), (400, 80), pixel_score=0.92)
            return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)

        task._match = fake_match
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))

        AutoLoginTask.run(task)

        self.assertEqual(1, len(clicks))
        self.assertAlmostEqual(0.722396, clicks[0][0])
        self.assertAlmostEqual(0.650926, clicks[0][1])
        self.assertEqual(2.0, clicks[0][2])
        self.assertEqual("waiting_loading", task._state)
        self.assertIsNotNone(task._login_clicked_at)
        self.assertGreater(task._login_clicked_at, 0.0)
        self.assertEqual(0.0, task._login_retry_not_before)

    def test_login_wait_hard_timeout_resets_state_and_backs_off(self):
        task = self._task()
        task._state = "waiting_home"
        snapshots = []
        task._save_flow_diagnostic = lambda name: snapshots.append((name, task._state))
        task._login_clicked_at = monotonic() - 301.0
        task._waiting_home_since = monotonic() - 301.0
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        warnings = []
        task.log_warning = lambda message, notify=False: warnings.append(
            (message, notify)
        )
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))

        AutoLoginTask._wait_loading_then_home(
            task,
            np.zeros((1440, 2560, 3), dtype=np.uint8),
        )

        self.assertEqual("waiting", task._state)
        self.assertEqual([("auto_login_home_timeout_failed", "waiting_home")], snapshots)
        self.assertIsNone(task._login_clicked_at)
        self.assertIsNone(task._waiting_home_since)
        self.assertEqual("登录后等待主页超时", statuses["状态"])
        self.assertEqual("等待登录页", statuses["阶段"])
        self.assertEqual(1, len(warnings))
        self.assertIn("超时", warnings[0][0])
        self.assertTrue(warnings[0][1])
        self.assertGreaterEqual(
            task._login_retry_not_before,
            monotonic() + 60.0 - 1.0,
        )

        info_calls = []
        task.info_set = lambda key, value: info_calls.append((key, value))
        task.capture_frame = lambda: self.fail("退避期内不得重新抓帧识别")
        task.trigger_interval = 0

        self.assertFalse(AutoLoginTask.run(task))
        self.assertEqual([], info_calls)

    def test_clearing_state_still_enforces_login_wait_budget(self):
        # 进入 clearing 后总预算继续有效：三信号长时间凑不齐时走统一的
        # 超时重置+退避，而不是无界点击公告清理位置（BUG-20260912-04）。
        task = self._task()
        task._state = "clearing"
        task._login_clicked_at = monotonic() - 301.0
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        warnings = []
        task.log_warning = lambda message, notify=False: warnings.append(
            (message, notify)
        )
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        cleared = []
        task._clear_popups_until_home = (
            lambda *_args, **_kwargs: cleared.append(1) or False
        )

        self.assertFalse(AutoLoginTask.run(task))

        self.assertEqual([], cleared)
        self.assertEqual("waiting", task._state)
        self.assertIsNone(task._login_clicked_at)
        self.assertEqual("登录后等待主页超时", statuses["状态"])
        self.assertEqual(1, len(warnings))
        self.assertIn("超时", warnings[0][0])
        self.assertGreaterEqual(
            task._login_retry_not_before,
            monotonic() + 60.0 - 1.0,
        )

    def test_run_resumes_login_flow_after_backoff_expires(self):
        task = self._task()
        task._state = "waiting"
        task._login_retry_not_before = monotonic() - 1.0
        task.trigger_interval = 0
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task.ocr = lambda *_args, **_kwargs: []
        info_calls = []
        task.info_set = lambda key, value: info_calls.append((key, value))

        self.assertFalse(AutoLoginTask.run(task))

        self.assertIn(("内部状态", "waiting"), info_calls)
        self.assertFalse(task._finished)

    def test_finishing_releases_login_gated_batch(self):
        from src.tasks.DailyBatchTask import DailyBatchTask

        task = self._task()
        task._state = "waiting"
        task.trigger_interval = 0
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._home_brightness_ratio = lambda _frame: 1.0
        task._home_gacha_ocr_text = lambda _frame: "抽抽乐"
        task.mark_logged_in = lambda: None

        def fake_match(_frame, spec):
            if spec is HOME_BUTTON_TEMPLATE:
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)

        task._match = fake_match
        batch = SimpleNamespace(
            _start_after_login=True,
            _enabled=False,
            lock=threading.Lock(),
            log_info=lambda *_args, **_kwargs: None,
        )
        enqueued = []
        task._executor = SimpleNamespace(
            get_task_by_class=lambda cls: batch if cls is DailyBatchTask else None,
            enqueue_onetime_task=lambda t: enqueued.append(t) or True,
        )

        AutoLoginTask.run(task)

        self.assertTrue(task._finished)
        self.assertEqual([batch], enqueued)
        self.assertTrue(batch._enabled)
        self.assertFalse(batch._start_after_login)

    def test_successful_home_confirmation_clears_login_retry_backoff(self):
        task = self._task()
        task._state = "clearing"
        task._login_retry_not_before = monotonic() + 60.0
        task._home_bright_since = monotonic() - 5.0
        task._home_brightness_ratio = lambda _frame: 1.0
        task._home_gacha_ocr_text = lambda _frame: "抽抽乐"
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        task.log_info = lambda *_args, **_kwargs: None
        logged_in = []
        task.mark_logged_in = lambda: logged_in.append(True)
        task._match = lambda _frame, _spec: MatchResult(
            0.9,
            (120, 130),
            (90, 90),
            pixel_score=0.9,
        )

        AutoLoginTask._clear_popups_until_home(
            task,
            np.zeros((1440, 2560, 3), dtype=np.uint8),
        )

        self.assertEqual([True], logged_in)
        self.assertTrue(task._finished)
        self.assertEqual("done", task._state)
        self.assertEqual(0.0, task._login_retry_not_before)

    def test_login_page_ocr_error_keeps_task_schedulable(self):
        task = self._task()
        task._state = "waiting"
        task.trigger_interval = 0
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.ocr = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ocr failed"))
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))

        AutoLoginTask.run(task)

        self.assertFalse(task._finished)
        self.assertTrue(AutoLoginTask.should_trigger(task))

    def test_waiting_home_clicks_notice_clear_position_after_grace(self):
        task = self._task()
        task._state = "waiting_home"
        task._waiting_home_since = monotonic() - 20.0
        task._home_brightness_ratio = lambda _frame: 0.235
        task._sleep_after_recognition = lambda: None
        clicks = []

        def fake_match(_frame, spec):
            if spec in HOME_BUTTON_TEMPLATES:
                return MatchResult(0.72, (120, 130), (90, 90), pixel_score=0.72)
            if spec is LOADING_TEMPLATE:
                return MatchResult(-1.0, (0, 0), (0, 0), pixel_score=-1.0)
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

    def _assert_notice_clear_click(self, clicks):
        self.assertEqual(1, len(clicks))
        x, y, after_sleep = clicks[0]
        self.assertAlmostEqual(169 / 1920, x)
        self.assertAlmostEqual(615 / 1080, y)
        self.assertEqual(0.2, after_sleep)

    @staticmethod
    def _ocr_box(name, x, y, width, height):
        return SimpleNamespace(
            name=name,
            x=x,
            y=y,
            width=width,
            height=height,
            confidence=1.0,
        )


if __name__ == "__main__":
    unittest.main()

class AutoLoginEscRecoveryTest(unittest.TestCase):
    """自动返回主页逻辑：宽限、auto_return_main_home 调用、等待加载、次数上限。"""

    def _task(self):
        task = object.__new__(AutoLoginTask)
        task.config = {
            "主页 UI 等待宽限秒数": 15.0,
            "游戏内返回主页尝试间隔秒数": 4.0,
            "游戏内返回主页最大尝试次数": 3,
            "返回主页后加载等待秒数": 6.0,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task._esc_home_attempts = 0
        task._last_esc_home_at = 0.0
        task._esc_home_wait_since = None
        task._esc_hold_until = 0.0
        task._esc_home_user_notified_at = 0.0
        task._state = "waiting"
        task._match = lambda *_args, **_kwargs: MatchResult(-1.0, (0, 0), (0, 0))
        task._passes = lambda *_args, **_kwargs: False
        return task

    def _frame(self):
        return np.zeros((10, 10, 3), dtype=np.uint8)

    def test_calls_auto_return_after_grace_then_holds_for_load(self):
        task = self._task()
        task._esc_home_wait_since = monotonic() - 30.0
        calls = []
        task.auto_return_main_home = lambda **_kwargs: calls.append(True)

        self.assertTrue(AutoLoginTask._maybe_return_home(task, self._frame()))
        self.assertEqual([True], calls)
        self.assertEqual(1, task._esc_home_attempts)

        # 紧接着再调用：处于“等待加载”保持期，不重复动作
        self.assertTrue(AutoLoginTask._maybe_return_home(task, self._frame()))
        self.assertEqual([True], calls)
        self.assertEqual(1, task._esc_home_attempts)

    def test_attempt_cap_stops_and_asks_manual(self):
        task = self._task()
        task._esc_home_wait_since = monotonic() - 30.0
        task._esc_home_attempts = 3  # 已达上限
        task._esc_hold_until = 0.0
        task._last_esc_home_at = 0.0
        calls = []
        task.auto_return_main_home = lambda **_kwargs: calls.append(True)

        self.assertTrue(AutoLoginTask._maybe_return_home(task, self._frame()))
        self.assertEqual([], calls)
        self.assertEqual(3, task._esc_home_attempts)



if __name__ == "__main__":
    unittest.main()
