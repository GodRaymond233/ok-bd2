import threading
import types
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.compat import starter_guard
from src.compat.starter_guard import (
    StarterWaitPolicy,
    StarterWizardWatcher,
    _is_starter_or_setup_command,
    _patch_execute_preflight,
    _patch_stable_wait,
    get_setup_exe_names,
    get_watcher_exe_names,
    get_wizard_grace_seconds,
    notify_starter_wizard,
    reset_starter_guard_state,
    starter_preflight_warning,
)
from src.compat.starter_update import (
    StarterObservation,
    StarterObserver,
    classify_starter_frame,
)

STARTER_PATH = r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe"
SETUP_PATH = r"C:\ProgramData\Neowiz\Browndust2Starter\BD2StarterSetup.exe"
GAME_PATH = r"D:\Neowiz\Browndust2\Browndust2_10000001\BrownDust II.exe"


def make_window() -> starter_guard.StarterWindow:
    return starter_guard.StarterWindow(
        pid=4242,
        exe_name="browndust2starter.exe",
        title="BrownDust II",
        width=302,
        height=193,
    )


class StarterWizardWatcherTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.notify = Mock()
        self.watcher = StarterWizardWatcher(
            grace_seconds=30.0,
            find_windows=lambda: [],
            notify=self.notify,
            observe=lambda windows: (
                StarterObservation("wizard") if windows else StarterObservation()
            ),
            clock=lambda: self.now[0],
        )

    def poll_with_window(self):
        self.watcher._find_windows = lambda: [make_window()]

    def test_notifies_once_after_grace(self):
        self.poll_with_window()
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.now[0] += 29.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.now[0] += 2.0
        self.watcher.poll_once()
        self.notify.assert_called_once()
        self.assertTrue(self.watcher.notified)

        self.now[0] += 30.0
        self.watcher.poll_once()
        self.notify.assert_called_once()

    def test_no_notify_without_window(self):
        self.watcher.poll_once()
        self.now[0] += 120.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

    def test_grace_clock_resets_when_window_disappears(self):
        self.poll_with_window()
        self.watcher.poll_once()
        self.now[0] += 29.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.watcher._find_windows = lambda: []
        self.watcher.poll_once()
        self.poll_with_window()
        self.now[0] += 2.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.now[0] += 31.0
        self.watcher.poll_once()
        self.notify.assert_called_once()

    def test_unknown_window_does_not_become_install_wizard_after_thirty_seconds(self):
        self.poll_with_window()
        self.watcher._observe = lambda _: StarterObservation("unknown")
        self.watcher.poll_once()
        self.now[0] += 120
        self.watcher.poll_once()
        self.notify.assert_not_called()

    def test_package_reset_and_disk_activity_refresh_progress(self):
        self.poll_with_window()
        observation = StarterObservation("updating", "解压", (0.5, 0.9), (4242, 1), 100)
        self.watcher._observe = lambda _: observation
        self.watcher.poll_once()
        self.now[0] += 200
        self.watcher.poll_once()
        self.assertEqual(self.watcher.latest.progress_at, 1000)
        observation = replace(observation, bars=(0.5, 0.05))
        self.watcher.poll_once()
        self.assertEqual(self.watcher.latest.progress_at, 1200)
        self.now[0] += 200
        observation = replace(observation, io_bytes=100000)
        self.watcher.poll_once()
        self.assertEqual(self.watcher.latest.progress_at, 1400)
        self.notify.assert_not_called()


def update_frame(orange=450, blue=250):
    frame = np.full((720, 1000, 3), 245, dtype=np.uint8)
    for y in (630, 652):
        cv2.rectangle(frame, (99, y - 1), (900, y + 10), (155, 155, 155), 1)
    cv2.rectangle(frame, (100, 630), (100 + orange, 638), (0, 155, 255), -1)
    cv2.rectangle(frame, (100, 652), (100 + blue, 660), (235, 155, 95), -1)
    return frame


class StarterFrameTest(unittest.TestCase):
    def test_dual_progress_bars_survive_scaling_and_package_reset(self):
        for scale in (0.75, 1, 1.5):
            with self.subTest(scale=scale):
                frame = cv2.resize(update_frame(), None, fx=scale, fy=scale)
                result = classify_starter_frame(frame)
                self.assertEqual(result.state, "updating")
                self.assertAlmostEqual(result.bars[0], 0.45, delta=0.005)
                self.assertAlmostEqual(result.bars[1], 0.25, delta=0.005)
        reset = classify_starter_frame(update_frame(450, 20))
        self.assertLess(reset.bars[1], 0.03)

    def test_unaligned_colors_and_blank_frames_are_unknown(self):
        frame = update_frame()
        frame[640:, :600] = 245
        cv2.rectangle(frame, (650, 652), (890, 660), (235, 155, 95), -1)
        for image in (frame, np.zeros_like(frame), None):
            with self.subTest(blank=image is None):
                self.assertEqual(classify_starter_frame(image).state, "unknown")

    def test_stage_wizard_and_failure_text_are_distinct(self):
        cases = [
            ("正在解壓縮。", "updating", "解压"),
            ("正在下载", "updating", "下载"),
            ("Verifying game files", "updating", "校验"),
            ("请选择安装游戏的位置", "wizard", None),
            ("Choose Install Location", "wizard", None),
            ("下载", "unknown", None),
            ("BrownDust II", "unknown", None),
            ("更新失败，请重试", "error", None),
        ]
        for text, state, stage in cases:
            with self.subTest(text=text):
                result = classify_starter_frame(None, text)
                self.assertEqual(result.state, state)
                if stage:
                    self.assertEqual(result.stage, stage)
        self.assertEqual(classify_starter_frame(update_frame(), "下载失败").state, "error")

    def test_observer_reads_warmed_ocr_without_unpausing_executor(self):
        engine = Mock()
        engine.ocr.return_value = [[(None, ("正在解压缩", 0.95))]]
        executor = types.SimpleNamespace(_ocr_lib={"default": engine}, paused=True)
        with patch("ok.og.executor", executor):
            self.assertEqual(StarterObserver._text(update_frame()), "正在解压缩")
        self.assertTrue(executor.paused)


class StarterWaitPolicyTest(unittest.TestCase):
    def test_update_longer_than_start_timeout_then_full_game_launch_grace(self):
        policy = StarterWaitPolicy(120, 0)
        for now in range(0, 1801, 2):
            observation = StarterObservation("updating", "解压", observed_at=now, progress_at=now)
            seconds, message, error = policy.evaluate(observation, now)
            self.assertEqual(seconds, 0)
            self.assertIn("解压", message)
            self.assertFalse(error)
        absent = StarterObservation(observed_at=1802)
        self.assertEqual(policy.evaluate(absent, 1802), (120, "", ""))
        self.assertEqual(policy.evaluate(replace(absent, observed_at=1921), 1921)[0], 1)
        self.assertTrue(policy.evaluate(replace(absent, observed_at=1922), 1922)[2])

    def test_unknown_window_keeps_ordinary_timeout(self):
        policy = StarterWaitPolicy(120, 0)
        for now in (0, 30, 119):
            result = policy.evaluate(StarterObservation("unknown", observed_at=now), now)
            self.assertGreater(result[0], 0)
            self.assertFalse(result[2])
        self.assertTrue(policy.evaluate(StarterObservation("unknown", observed_at=120), 120)[2])

    def test_stall_warns_but_disk_progress_or_stage_change_can_resume(self):
        policy = StarterWaitPolicy(120, 0)
        result = policy.evaluate(StarterObservation("updating", "解压", observed_at=301), 301)
        self.assertIn("5 分钟", result[1])
        self.assertFalse(result[2])
        result = policy.evaluate(
            StarterObservation(
                "updating",
                "解压",
                observed_at=302,
                progress_at=302,
            ),
            302,
        )
        self.assertNotIn("5 分钟", result[1])

    def test_capture_loss_has_bounded_recovery_without_install_warning(self):
        policy = StarterWaitPolicy(120, 0)
        policy.evaluate(StarterObservation("updating", "下载", observed_at=10), 10)
        for now in (20, 150, 309):
            seconds, message, error = policy.evaluate(
                StarterObservation("unknown", observed_at=now),
                now,
            )
            self.assertEqual(seconds, 0)
            self.assertIn("无法读取", message)
            self.assertFalse(error)
        self.assertTrue(policy.evaluate(StarterObservation("unknown", observed_at=310), 310)[2])

    def test_stale_worker_does_not_extend_update_wait_forever(self):
        policy = StarterWaitPolicy(120, 0)
        observation = StarterObservation("updating", "下载", observed_at=0)
        policy.evaluate(observation, 0)
        self.assertIn("无法读取", policy.evaluate(observation, 16)[1])
        self.assertTrue(policy.evaluate(observation, 301)[2])

    def test_install_wizard_waits_for_user_and_explicit_error_stops(self):
        policy = StarterWaitPolicy(120, 0)
        self.assertIn(
            "安装向导",
            policy.evaluate(
                StarterObservation(
                    "wizard",
                    observed_at=900,
                ),
                900,
            )[1],
        )
        self.assertEqual(
            policy.evaluate(
                StarterObservation(
                    "error",
                    "下载失败",
                    observed_at=902,
                ),
                902,
            ),
            (0, "", "下载失败"),
        )


class StarterGuardConfigTest(unittest.TestCase):
    def test_setup_exe_names_default(self):
        self.assertEqual(get_setup_exe_names(), ["BD2StarterSetup.exe"])

    def test_setup_exe_names_env_list(self):
        env = {"OK_BD2_SETUP_EXE": " OneSetup.exe, 'TwoSetup.exe' "}
        self.assertEqual(get_setup_exe_names(env), ["OneSetup.exe", "TwoSetup.exe"])

    def test_watcher_names_merge_launcher_and_setup(self):
        env = {"OK_BD2_LAUNCHER_EXE": "AltStarter.exe", "OK_BD2_SETUP_EXE": "AltSetup.exe"}
        self.assertEqual(
            get_watcher_exe_names(env),
            {"altstarter.exe", "altsetup.exe"},
        )

    def test_wizard_grace_seconds_default_and_env(self):
        grace_env = {"OK_BD2_STARTER_WIZARD_GRACE_SECONDS": "45"}
        invalid_env = {"OK_BD2_STARTER_WIZARD_GRACE_SECONDS": "abc"}
        floor_env = {"OK_BD2_STARTER_WIZARD_GRACE_SECONDS": "0"}
        self.assertEqual(get_wizard_grace_seconds(), 30.0)
        self.assertEqual(get_wizard_grace_seconds(grace_env), 45.0)
        self.assertEqual(get_wizard_grace_seconds(invalid_env), 30.0)
        self.assertEqual(get_wizard_grace_seconds(floor_env), 1.0)


class StarterPreflightTest(unittest.TestCase):
    def test_warning_when_game_missing(self):
        with patch("src.game_path.resolve_game_exe_path", return_value=""):
            message = starter_preflight_warning()
        self.assertIn("BD2StarterSetup.exe", message)
        self.assertIn("OK_BD2_GAME_PATH", message)

    def test_no_warning_when_game_found(self):
        with patch("src.game_path.resolve_game_exe_path", return_value=GAME_PATH):
            self.assertIsNone(starter_preflight_warning())


class StarterCommandTest(unittest.TestCase):
    def test_accepts_launcher_and_setup_commands(self):
        self.assertTrue(_is_starter_or_setup_command(STARTER_PATH))
        self.assertTrue(_is_starter_or_setup_command(f'"{SETUP_PATH}"'))
        self.assertFalse(_is_starter_or_setup_command(GAME_PATH))
        self.assertFalse(_is_starter_or_setup_command(""))
        self.assertFalse(_is_starter_or_setup_command(None))


class ExecutePreflightPatchTest(unittest.TestCase):
    def setUp(self):
        reset_starter_guard_state()
        self.addCleanup(reset_starter_guard_state)
        self.execute_calls = []

        def inner_execute(game_cmd, arguments=None, start_method="start"):
            self.execute_calls.append((game_cmd, arguments, start_method))
            return True

        self.module = types.SimpleNamespace(execute=inner_execute)
        _patch_execute_preflight(self.module)

    def test_preflight_notifies_once_and_still_launches(self):
        message = "preflight warning"
        with (
            patch.object(starter_guard, "starter_preflight_warning", return_value=message),
            patch.object(starter_guard, "_emit_notification") as emit,
        ):
            self.assertTrue(self.module.execute(STARTER_PATH, start_method="start"))
            self.assertTrue(self.module.execute(STARTER_PATH, start_method="start"))
        emit.assert_called_once_with(message, "BD2 游戏启动前体检")
        self.assertEqual(
            self.execute_calls,
            [(STARTER_PATH, None, "start"), (STARTER_PATH, None, "start")],
        )

    def test_non_starter_command_skips_preflight(self):
        with patch.object(starter_guard, "_emit_notification") as emit:
            self.assertTrue(self.module.execute(GAME_PATH, arguments="--flag"))
        emit.assert_not_called()
        self.assertEqual(self.execute_calls, [(GAME_PATH, "--flag", "start")])

    def test_patch_is_idempotent(self):
        wrapped = self.module.execute
        _patch_execute_preflight(self.module)
        self.assertIs(self.module.execute, wrapped)


class StableWaitPatchTest(unittest.TestCase):
    def setUp(self):
        self.wait_calls = []

        def original_wait(controller):
            self.wait_calls.append(controller)
            return True

        class FakeStartController:
            _wait_until_started_window_stable = original_wait

        self.controller_class = FakeStartController
        self.module = types.SimpleNamespace(StartController=FakeStartController)

    def test_wrapper_uses_update_aware_wait_on_windows(self):
        controller = self.controller_class()
        with patch.object(starter_guard, "_wait_for_game_window", return_value=True) as wait:
            _patch_stable_wait(self.module)
            self.assertTrue(controller._wait_until_started_window_stable())
        wait.assert_called_once_with(controller, self.module)
        self.assertEqual(self.wait_calls, [])

    def test_non_windows_preserves_framework_wait(self):
        controller = self.controller_class()
        with patch.object(starter_guard.os, "name", "posix"):
            _patch_stable_wait(self.module)
            self.assertTrue(controller._wait_until_started_window_stable())
        self.assertEqual(self.wait_calls, [controller])

    def test_patch_is_idempotent(self):
        _patch_stable_wait(self.module)
        wrapped = self.controller_class._wait_until_started_window_stable
        _patch_stable_wait(self.module)
        self.assertIs(self.controller_class._wait_until_started_window_stable, wrapped)


class NotifyStarterWizardTest(unittest.TestCase):
    def setUp(self):
        reset_starter_guard_state()
        self.addCleanup(reset_starter_guard_state)

    def test_notify_emits_and_throttles_repeat(self):
        with patch.object(starter_guard, "_emit_notification") as emit:
            notify_starter_wizard([make_window()], 45.0)
            notify_starter_wizard([make_window()], 120.0)
        emit.assert_called_once()

    def test_notify_again_after_reset(self):
        with patch.object(starter_guard, "_emit_notification") as emit:
            notify_starter_wizard([make_window()], 45.0)
            reset_starter_guard_state()
            notify_starter_wizard([make_window()], 45.0)
        self.assertEqual(emit.call_count, 2)


class FindStarterWindowsTest(unittest.TestCase):
    def test_non_windows_returns_empty(self):
        with patch.object(starter_guard.os, "name", "posix"):
            self.assertEqual(starter_guard.find_starter_windows(), [])


class GameWindowWaitTest(unittest.TestCase):
    def run_wait(self, game_at=None, cancel_at=None, observation=None, exit_at=None):
        now = [0.0]
        cancel_events = []
        window = types.SimpleNamespace(hwnd=0, width=0, height=0, do_update_window_size=Mock())
        watcher = Mock()
        watcher.latest = observation or StarterObservation()

        def advance(seconds):
            now[0] += seconds
            if game_at is not None and now[0] >= game_at:
                window.hwnd, window.width, window.height = 123, 1920, 1080
            if cancel_at is not None and now[0] >= cancel_at and cancel_events:
                cancel_events[-1].set()
            watcher.latest = replace(watcher.latest, observed_at=now[0], progress_at=now[0])

        exit_event = Mock()
        exit_event.wait.side_effect = advance
        exit_event.is_set.side_effect = lambda: exit_at is not None and now[0] >= exit_at
        controller = types.SimpleNamespace(
            start_timeout=120,
            exit_event=exit_event,
            tr=lambda text: text,
            STARTED_WINDOW_MIN_SIZE=(100, 100),
            STARTED_WINDOW_STABLE_SECONDS=2,
            STARTED_WINDOW_POLL_INTERVAL=0.2,
        )
        module = types.SimpleNamespace(
            communicate=types.SimpleNamespace(starting_emulator=Mock()),
            og=types.SimpleNamespace(device_manager=types.SimpleNamespace(hwnd_window=window)),
        )
        with (
            patch.object(starter_guard, "StarterWizardWatcher") as watcher_cls,
            patch.object(starter_guard, "monotonic", side_effect=lambda: now[0]),
            patch.object(
                starter_guard.starter_wait_status,
                "emit",
                side_effect=lambda text, cancel: cancel_events.append(cancel),
            ),
        ):
            watcher_cls.return_value.__enter__.return_value = watcher
            result = starter_guard._wait_for_game_window(controller, module)
        watcher_cls.return_value.__exit__.assert_called_once()
        self.assertTrue(all(event.is_set() for event in cancel_events))
        return result, now[0], module.communicate.starting_emulator

    def test_slow_update_hands_off_only_after_game_window_stabilizes(self):
        result, elapsed, events = self.run_wait(
            game_at=600,
            observation=StarterObservation("updating", "解压"),
        )
        self.assertTrue(result)
        self.assertGreaterEqual(elapsed, 602)
        self.assertFalse(any(call.args[1] for call in events.emit.call_args_list))

    def test_normal_launch_timeout_returns_failure(self):
        result, elapsed, events = self.run_wait()
        self.assertFalse(result)
        self.assertGreaterEqual(elapsed, 120)
        self.assertIn("超时", events.emit.call_args.args[1])

    def test_cancel_and_application_exit_release_wait(self):
        for kwargs in ({"cancel_at": 10}, {"exit_at": 10}):
            with self.subTest(kwargs=kwargs):
                result, elapsed, events = self.run_wait(
                    observation=StarterObservation("updating", "下载"),
                    **kwargs,
                )
                self.assertFalse(result)
                self.assertLess(elapsed, 11)
                events.emit.assert_called_with(True, None, 0)


class StarterWaitUiTest(unittest.TestCase):
    def test_update_message_stops_countdown_and_cancel_only_sets_event(self):
        from src.compat.starter_wait_ui import show_starter_wait_status

        cancel = threading.Event()
        dialog = Mock(spec=["restart_countdown", "loading_label", "widget", "width"])
        dialog.width.return_value = 1000
        dialog.loading_label.heightForWidth.return_value = 76
        window = types.SimpleNamespace(emulator_starting_dialog=dialog)
        with patch("qfluentwidgets.PushButton") as button_cls:
            show_starter_wait_status(window, "游戏正在解压\n已等待 600 秒", cancel)
            show_starter_wait_status(window, "游戏正在校验", cancel)
        button_cls.assert_called_once()
        dialog.restart_countdown.assert_called_with(0)
        dialog.loading_label.setText.assert_called_with("游戏正在校验")
        callback = button_cls.return_value.clicked.connect.call_args.args[0]
        callback()
        self.assertTrue(cancel.is_set())
        dialog.loading_label.setText.reset_mock()
        show_starter_wait_status(window, "过期消息", cancel)
        dialog.loading_label.setText.assert_not_called()


if __name__ == "__main__":
    unittest.main()
