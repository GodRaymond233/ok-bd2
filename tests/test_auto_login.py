import threading
import unittest
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.compat.executor_cleanup import _execute, _finish_task
from src.scene.BD2Scene import BD2Scene
from src.tasks.DailyBatchTask import RUN_MODE_INCOMPLETE, DailyBatchTask
from src.tasks.DailyTask import DailyTask
from src.tasks.FreeGachaTask import FreeGachaTask
from src.tasks.login_gate import defer_until_login
from src.tasks.trigger.AutoLoginTask import AutoLoginTask, MatchResult
from tests.helpers.modal_glyph import draw_close_glyph


class AutoLoginTest(unittest.TestCase):
    def _task(self):
        executor = SimpleNamespace(scene=None, get_task_by_class=lambda _: None)
        task = AutoLoginTask(executor, SimpleNamespace())
        task.config = dict(task.default_config)
        task.info_set = Mock()
        task.log_info = Mock()
        task.log_warning = Mock()
        task.sleep = Mock()
        task._save_flow_diagnostic = Mock()
        task._sleep_after_recognition = Mock()
        task._read_home_confirmation_signals = lambda *_: (False, 0, 0, "")
        task._match = lambda *_: MatchResult(-1.0, (0, 0), (0, 0))
        task.ocr = lambda **_: []
        task.capture_frame = Mock(return_value=np.zeros((108, 192, 3), np.uint8))
        task.operate_click = Mock()
        return task

    def test_update_download_requires_context_and_a_paired_button(self):
        boxes = [
            SimpleNamespace(name=name, x=x, y=y, width=w, height=h, confidence=1.0)
            for name, x, y, w, h in (
                ("下载", 930, 300, 65, 38),
                ("将下载游戏所需数据。", 850, 360, 200, 30),
                ("下载容量", 680, 560, 90, 30),
                ("可用空间 224,592 MB", 825, 680, 270, 34),
                ("取消", 850, 755, 50, 32),
                ("下载", 1025, 755, 48, 32),
            )
        ]
        for indices, state, clicks in (
            (range(6), "downloading", [call(1049, 771, after_sleep=1.0)]),
            ((0, 2, 3), "waiting_update", []),
            ((2, 4, 5), "waiting_update", []),
        ):
            with self.subTest(boxes=indices):
                task = self._task()
                task._state = "waiting_update"
                task.ocr = Mock(return_value=[boxes[i] for i in indices])
                task.run()
                self.assertEqual(state, task._state)
                self.assertEqual(clicks, task.operate_click.call_args_list)

    def test_idle_home_observer_releases_input_for_daily_dispatch(self):
        scene = BD2Scene()
        login = self._task()
        login._enabled = True
        login._state = "done"
        login._session_window = (None, None)
        observing, release, queued, ran = (threading.Event() for _ in range(4))
        executor = Mock(scene=scene, current_task=None, paused=False, _exclusive_owner=None)
        executor.exit_event = threading.Event()
        executor.trigger_tasks = [login]
        executor._ocr_lib = {"default": object()}
        executor._last_frame_time = 0
        executor._pop_job.return_value = None
        executor._wait_for_activity.side_effect = lambda *_: queued.wait(2)
        executor.method.get_frame.return_value = np.zeros((10, 10, 3), np.uint8)
        login._executor = executor
        login.ocr_fun = Mock(return_value=Mock(return_value=([], [])))
        executor.device_manager.hwnd_window = None
        daily = Mock(waits_for_login=False, exit_after_task=False, config={})

        def run_daily():
            ran.set()
            executor.exit_event.set()

        daily.run.side_effect = run_daily
        executor.next_task.side_effect = lambda: (daily if queued.is_set() else None, False, False)

        def slow_ocr(frame, vision, *_):
            vision.task.ocr(frame=frame, threshold=0.2, target_height=720)
            observing.set()
            release.wait(5)
            return False, 0, 0, ""

        with (
            patch("src.scene.BD2Scene.read_home_signals", side_effect=slow_ocr),
            patch("src.compat.executor_cleanup.prevent_sleeping"),
            patch("src.tasks.run_history.record_finished_task"),
            patch("src.compat.executor_cleanup.communicate"),
        ):
            thread = threading.Thread(target=_execute, args=(executor,), daemon=True)
            thread.start()
            try:
                self.assertTrue(observing.wait(2))
                self.assertIsNone(executor.current_task)
                scene.record_home(True)
                queued.set()
                self.assertTrue(ran.wait(1))
            finally:
                release.set()
                queued.set()
                executor.exit_event.set()
                thread.join(2)
            scene._pending_home_sample[0].result(timeout=2)
            executor.exit_event.clear()
            executor.current_task = None
            scene.observe_home(executor)
        self.assertIs(scene.at_home, True)
        self.assertFalse(thread.is_alive())
        daily.run.assert_called_once()
        login.ocr_fun.assert_called_once_with("home_observer")
        login.operate_click.assert_not_called()
        self.assertTrue(login._finished)

    def test_home_timeout_records_evidence_then_backs_off(self):
        task = self._task()
        task._state = "waiting_home"
        task._login_clicked_at = monotonic() - 301.0
        task._waiting_home_since = task._login_clicked_at
        task.run()
        self.assertEqual("waiting", task._state)
        task._save_flow_diagnostic.assert_called_once_with("auto_login_home_timeout_failed")
        self.assertIsNone(task._login_clicked_at)
        self.assertIn(call("状态", "登录后等待主页超时"), task.info_set.call_args_list)
        self.assertIn(call("阶段", "等待登录页"), task.info_set.call_args_list)
        task.log_warning.assert_called_once()
        self.assertTrue(task.log_warning.call_args.kwargs["notify"])
        self.assertGreaterEqual(task._login_retry_not_before, monotonic() + 59)
        task.capture_frame.reset_mock()
        self.assertFalse(task.run())
        task.capture_frame.assert_not_called()
        task.operate_click.assert_not_called()

    def test_check_in_card_covering_the_left_hud_interrupts_home_confirmation(self):
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
                popup[
                    round(0.24 * height) : round(0.76 * height),
                    round(0.22 * width) : round(0.78 * width),
                ] = 255
                # Bright home artwork with an illuminated gacha label also crosses the OCR region.
                home = popup.copy()
                home[
                    round(0.90 * height) : round(0.98 * height),
                    round(0.05 * width) : round(0.12 * width),
                ] = 230
                no_cross = popup.copy()
                # A cross-shaped HUD icon remains visible beyond the card's right edge.
                draw_close_glyph(
                    no_cross,
                    (round(0.816 * width), round(0.26 * height)),
                    size=round(0.02 * height),
                    value=120,
                )
                cross = (round(0.76 * width), round(0.26 * height))
                draw_close_glyph(popup, cross, size=round(0.025 * height))
                bright_hud = popup.copy()
                bright_hud[height // 6 : height // 4, width // 12 : width // 6] = 255
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

    def test_stable_home_releases_live_requests_in_order_after_executor_is_free(self):
        login = self._task()
        login._enabled = True
        login._read_home_confirmation_signals = lambda *_: (True, 2, 250, "抽抽乐")
        login.mark_logged_in = Mock()
        executor = login.executor
        batch, cancelled, gacha = (
            object.__new__(cls) for cls in (DailyBatchTask, DailyTask, FreeGachaTask)
        )
        executor.onetime_tasks = [batch, cancelled, gacha]
        executor.get_task_by_class = lambda cls: login if cls is AutoLoginTask else None
        executor.current_task = login
        owners = []
        executor.enqueue_onetime_task = lambda t: owners.append((t, executor.current_task)) or True
        for task in (gacha, cancelled, batch):
            task.lock = threading.Lock()
            task._executor = executor
            task._enabled = True
            task.info_set = Mock()
            task.log_info = Mock()
            self.assertTrue(defer_until_login(task))
        cancelled._start_after_login = False
        batch.request_run_mode(RUN_MODE_INCOMPLETE)
        batch.park_run_mode()
        login.running = True
        with patch("src.tasks.trigger.AutoLoginTask.monotonic", side_effect=[10, 10, 13, 13]):
            login.run()
            self.assertFalse(login._finished)
            login.run()
        self.assertTrue(login._finished)
        self.assertEqual([], owners)
        with patch("src.compat.executor_cleanup.communicate.task.emit"):
            _finish_task(executor, login, True)
            _finish_task(executor, login, True)
        self.assertEqual([(gacha, None), (batch, None)], owners)
        self.assertEqual(RUN_MODE_INCOMPLETE, batch._take_run_mode(None))
        login.operate_click.assert_not_called()
        login.trigger_interval = 0
        self.assertFalse(login.should_trigger())
        self.assertFalse(login.run())
        executor.device_manager = SimpleNamespace(hwnd_window=SimpleNamespace(hwnd=32, pid=5))
        self.assertTrue(login.should_trigger())
        self.assertEqual("waiting", login._state)
