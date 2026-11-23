import threading
import unittest
from time import monotonic
from types import SimpleNamespace

import numpy as np

from src.tasks.trigger.AutoLoginTask import (
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
        task._state = "waiting"
        task._session_window = (None, None)
        task._executor = SimpleNamespace(get_task_by_class=lambda _cls: None)
        task._read_home_confirmation_signals = lambda *_: (False, 0, 0, "")
        return task

    def test_finished_login_only_rearms_for_a_new_game_window(self):
        task = self._task()
        task._state = "done"
        task.trigger_interval = 0
        task.capture_frame = lambda: self.fail("finished login requested input")
        self.assertFalse(task.should_trigger())
        self.assertFalse(task.run())
        task._executor.device_manager = SimpleNamespace(hwnd_window=SimpleNamespace(hwnd=32, pid=5))
        self.assertTrue(task.should_trigger())
        self.assertEqual("waiting", task._state)

    def test_idle_home_observation_follows_gameplay_without_claiming_input(self):
        from unittest.mock import Mock, patch

        from src.scene.BD2Scene import BD2Scene

        scene = BD2Scene()
        task = self._task()
        task._enabled = True
        task._state = "done"
        task._match = lambda *_: MatchResult(-1.0, (0, 0), (0, 0))
        backend = Mock(return_value=([], []))
        task.ocr_fun = Mock(return_value=backend)
        task.operate_click = lambda *_args, **_kwargs: self.fail("observer sent input")
        executor = SimpleNamespace(
            current_task=None, trigger_tasks=[task], can_capture=lambda: True,
            method=SimpleNamespace(get_frame=lambda **_: np.zeros((10, 10, 3), np.uint8)),
            exit_event=threading.Event(), _ocr_lib={"default": object()},
            _get_wake_version=lambda: 0,
        )
        task._executor = executor
        signals = iter((True, False, False, False))

        def read(frame, vision, *_):
            vision.task.ocr(frame=frame, threshold=.2, target_height=720)
            return next(signals), 0, 0, ""

        with patch("src.scene.BD2Scene.read_home_signals", side_effect=read), \
             patch("src.scene.BD2Scene.monotonic", return_value=10) as clock:
            scene.observe_home(executor)
            scene._pending_home_sample[0].result(timeout=2)
            scene.observe_home(executor)
            self.assertIs(scene.at_home, True)
            clock.return_value = 13
            scene.observe_home(executor)
            scene._pending_home_sample[0].result(timeout=2)
            scene.observe_home(executor)
            self.assertIs(scene.at_home, False)
            self.assertIsNone(executor.current_task)
            self.assertTrue(task._finished)
            clock.return_value = 20
            self.assertIsNone(scene.at_home)
            scene.observe_home(executor)
            scene.record_home(True)
            scene._pending_home_sample[0].result(timeout=2)
            scene.observe_home(executor)
            self.assertIs(scene.at_home, True)
            clock.return_value = 23
            scene.observe_home(executor)
            scene._pending_home_sample[0].result(timeout=2)
            executor.device_manager = SimpleNamespace(hwnd_window=SimpleNamespace(pid=9, hwnd=80))
            scene.observe_home(executor)
            self.assertIs(scene.at_home, True)
            self.assertEqual({"home_observer"}, {call.args[-1] for call in backend.call_args_list})

    def test_daily_dispatches_while_idle_observation_is_waiting(self):
        from unittest.mock import Mock, patch

        from src.compat.executor_cleanup import _execute
        from src.scene.BD2Scene import BD2Scene

        scene = BD2Scene()
        login = self._task()
        login._enabled = True
        login._state = "done"
        observing, release, queued, ran = (threading.Event() for _ in range(4))
        executor = Mock(scene=scene, current_task=None, paused=False, _exclusive_owner=None)
        executor.exit_event = threading.Event()
        executor.trigger_tasks = [login]
        executor._ocr_lib = {"default": object()}
        executor._last_frame_time = 0
        executor._pop_job.return_value = None
        executor._wait_for_activity.side_effect = lambda *_: queued.wait(2)
        login._executor = executor
        executor.device_manager.hwnd_window = None
        daily = Mock(waits_for_login=False, exit_after_task=False, config={})

        def run_daily():
            ran.set()
            executor.exit_event.set()

        daily.run.side_effect = run_daily
        executor.next_task.side_effect = lambda: (daily if queued.is_set() else None, False, False)

        def slow_ocr(*_):
            observing.set()
            release.wait(3)
            return False, 0, 0, ""

        with patch("src.scene.BD2Scene.read_home_signals", side_effect=slow_ocr), \
             patch("src.compat.executor_cleanup.prevent_sleeping"), \
             patch("src.tasks.run_history.record_finished_task"), \
             patch("src.compat.executor_cleanup.communicate"):
            thread = threading.Thread(target=_execute, args=(executor,), daemon=True)
            thread.start()
            try:
                self.assertTrue(observing.wait(2))
                queued.set()
                self.assertTrue(ran.wait(1))
            finally:
                release.set()
                queued.set()
                executor.exit_event.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        daily.run.assert_called_once()


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

        task.capture_frame = lambda: np.zeros((20, 20, 3), np.uint8)
        task.run()

        self.assertEqual("waiting", task._state)
        self.assertEqual([("auto_login_home_timeout_failed", "waiting_home")], snapshots)
        self.assertIsNone(task._login_clicked_at)
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

    def test_stable_home_hands_off_once_after_releasing_executor(self):
        # The batch keeps the chosen mode throughout login.
        from unittest.mock import patch

        from src.compat.executor_cleanup import _finish_task
        from src.tasks.DailyBatchTask import RUN_MODE_INCOMPLETE, DailyBatchTask

        task = self._task()
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._read_home_confirmation_signals = lambda *_: (True, 2, 250, "抽抽乐")
        task.operate_click = lambda *args, **kwargs: self.fail("clicked stable home")
        task.mark_logged_in = lambda: None
        batch = object.__new__(DailyBatchTask)
        batch._start_after_login = True
        batch.request_run_mode(RUN_MODE_INCOMPLETE)
        batch.park_run_mode()
        batch.lock = threading.Lock()
        batch.log_info = lambda *_: None
        owners = []
        executor = task._executor
        executor.onetime_tasks = [batch]
        executor.current_task = task
        executor.get_task_by_class = lambda cls: batch if cls is DailyBatchTask else None
        executor.enqueue_onetime_task = lambda _: owners.append(executor.current_task) or True
        task.running = True
        with patch("src.tasks.trigger.AutoLoginTask.monotonic", side_effect=[10, 10, 13, 13]):
            task.run()
            self.assertFalse(task._finished)
            task.run()
        self.assertTrue(task._finished)
        self.assertEqual([], owners)
        with patch("src.compat.executor_cleanup.communicate.task.emit"):
            _finish_task(executor, task, True)
            _finish_task(executor, task, True)
        self.assertEqual([None], owners)
        self.assertEqual(RUN_MODE_INCOMPLETE, batch._take_run_mode(None))

    def test_check_in_card_covering_the_left_hud_interrupts_home_confirmation(self):
        from unittest.mock import Mock, patch

        from tests.test_modal_popup_dismiss import _draw_close_glyph

        def ocr_text(_frame, name, **_):
            return "我的小屋 格鲁TALK" if "左列" in name else "抽抽乐"

        for width, height in ((1920, 1080), (1280, 720)):
            with self.subTest(size=(width, height)):
                task = self._task()
                del task._read_home_confirmation_signals
                task._quick_vision = lambda: SimpleNamespace(ocr_text=ocr_text)
                task.operate_click = Mock()
                task.mark_logged_in = Mock()
                popup = np.full((height, width, 3), 55, np.uint8)
                popup[round(.24 * height):round(.76 * height),
                      round(.22 * width):round(.78 * width)] = 255
                # Bright home artwork with an illuminated gacha label also crosses the OCR region.
                home = popup.copy()
                home[round(.90 * height):round(.98 * height),
                     round(.05 * width):round(.12 * width)] = 230
                no_cross = popup.copy()
                # A cross-shaped HUD icon remains visible beyond the card's right edge.
                _draw_close_glyph(
                    no_cross, (round(.816 * width), round(.26 * height)),
                    size=round(.02 * height), value=120,
                )
                cross = (round(.76 * width), round(.26 * height))
                _draw_close_glyph(popup, cross, size=round(.025 * height))
                bright_hud = popup.copy()
                bright_hud[height // 6:height // 4, width // 12:width // 6] = 255
                frames = iter((home, popup, bright_hud, no_cross, home, home))
                task.capture_frame = lambda: next(frames)
                with (
                    patch("src.tasks.trigger.AutoLoginTask.monotonic") as clock,
                    patch("src.tasks.BaseBD2Task.monotonic", new=clock),
                ):
                    for now in (10, 12, 16, 17, 19):
                        clock.return_value = now
                        task.run()
                        task.mark_logged_in.assert_not_called()
                    clock.return_value = 22.1
                    task.run()
                task.mark_logged_in.assert_called_once()
                self.assertTrue(task._finished)
                self.assertEqual(2, task.operate_click.call_count)
                for click in task.operate_click.call_args_list:
                    x, y = click.args
                    self.assertAlmostEqual(cross[0], x * width, delta=2)
                    self.assertAlmostEqual(cross[1], y * height, delta=2)
                    self.assertTrue(click.kwargs["require_fresh_target"])

    def test_home_tasks_resume_in_request_order_and_skip_cancelled_requests(self):
        from src.tasks.BargainLevelTask import BargainLevelTask
        from src.tasks.DailyTask import DailyTask
        from src.tasks.FreeGachaTask import FreeGachaTask
        from src.tasks.login_gate import defer_until_login, release_after_login
        from src.tasks.PVPTask import PVPTask

        login = SimpleNamespace(login_pending=True)
        queued = []
        executor = SimpleNamespace(
            onetime_tasks=[], get_task_by_class=lambda _: login,
            enqueue_onetime_task=lambda task: queued.append(task) or True,
        )
        for kind in (PVPTask, DailyTask, FreeGachaTask, BargainLevelTask):
            task = object.__new__(kind)
            task._enabled = True
            task.lock = threading.Lock()
            task.info_set = lambda *_: None
            task._executor = executor
            executor.onetime_tasks.append(task)
        pvp, daily, gacha, merchant = executor.onetime_tasks
        self.assertFalse(defer_until_login(merchant))
        for task in (gacha, daily, pvp):
            self.assertTrue(defer_until_login(task))
        daily._start_after_login = False
        self.assertTrue(release_after_login(executor))
        self.assertFalse(release_after_login(executor))
        self.assertEqual([gacha, pvp], queued)

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
