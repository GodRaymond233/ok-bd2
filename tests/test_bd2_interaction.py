import logging
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
import win32api
import win32con
from ok.device.intercation import PostMessageInteraction
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.interaction import BD2Interaction as win_input
from src.interaction.BD2Interaction import (
    CLICK_MODE_BACKGROUND,
    CLICK_MODE_STANDARD,
    BD2Interaction,
    InputTargetChanged,
)
from src.tasks.BaseBD2Task import BaseBD2Task, task_info_snapshot
from src.tasks.BD2InputTestTask import (
    BACKGROUND_MOUSE_MOVE_KEY,
    CLICK_MODE_KEY,
    REFERENCE_POINT_MODE_KEY,
    REFERENCE_POINT_X_KEY,
    REFERENCE_POINT_Y_KEY,
    BD2BackgroundMouseClickInputTestTask,
    BD2ClickModeSelectorTask,
    BD2MouseWheelInputTestTask,
)
from src.tasks.task_notifications import log_task_completion, suppress_task_completion_notifications


def make_interaction():
    """Build a BD2Interaction without running its device ``__init__``."""
    interaction = object.__new__(BD2Interaction)
    interaction._input_lock = threading.RLock()
    interaction._operating = False
    interaction._click_mode_provider = None
    interaction.cursor_position = (1, 2)
    calls = []
    interaction._restore_cursor = lambda: calls.append("restore_cursor")
    interaction.block_input = lambda: calls.append("block_input") or True
    interaction.unblock_input = lambda: calls.append("unblock_input") or True
    interaction._mouse_buttons_down = lambda: False
    return interaction, calls


class InputTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(win_input.logger, "error"))
        self.get_cursor = self.enterContext(
            patch.object(win_input, "GetCursorPos", return_value=(1, 2))
        )
        self.set_cursor = self.enterContext(patch.object(win_input, "SetCursorPos"))
        self.api_set = self.enterContext(patch.object(win32api, "SetCursorPos"))
        self.enterContext(patch.object(win32api, "GetCursorPos", return_value=(1, 2)))

    def test_prepared_click_is_discarded_after_input_wait_or_window_change(self):
        for mode in (CLICK_MODE_STANDARD, CLICK_MODE_BACKGROUND):
            changes = ("window", "client", "target", "foreground", "none")
            if mode == CLICK_MODE_STANDARD:
                changes += ("held",)
            for changed in changes:
                with (
                    self.subTest(mode=mode, changed=changed),
                    patch(
                        "src.interaction.BD2Interaction.win32gui.GetForegroundWindow",
                        return_value=456,
                    ) as foreground,
                    patch(
                        "src.interaction.BD2Interaction.win32gui.GetWindowRect",
                        return_value=(0, 0, 1920, 1080),
                    ) as rect,
                    patch(
                        "src.interaction.BD2Interaction.win32gui.GetClientRect",
                        return_value=(0, 0, 1920, 1080),
                    ) as client,
                ):
                    interaction, calls = make_interaction()
                    interaction.set_click_mode_provider(lambda: mode)
                    interaction.hwnd_window = SimpleNamespace(top_hwnd=123, hwnd=123)
                    buttons = iter([True, False, False] if changed == "held" else [False, False])
                    interaction._mouse_buttons_down = lambda: next(buttons)
                    task = object.__new__(BaseBD2Task)
                    task.info_set = Mock()
                    task.sleep = Mock()
                    task.click = Mock()
                    task._check_action_interval = lambda *_: True
                    task._executor = SimpleNamespace(interaction=interaction)
                    task._frame_input_context = task._input_target_context()
                    task._wait_for_input = lambda _: setattr(task, "_input_wait_seconds", 1.0)
                    if changed == "window":
                        rect.return_value = (10, 10, 1930, 1090)
                    elif changed == "client":
                        client.return_value = (0, 0, 1280, 720)
                    elif changed == "target":
                        interaction.hwnd_window.top_hwnd = 124
                    elif changed == "foreground":
                        foreground.return_value = 789
                    accepted = changed in {"foreground", "none"}
                    if accepted:
                        task.operate_click(0.5, 0.5, require_fresh_target=True)
                    else:
                        with self.assertRaises(InputTargetChanged) as caught:
                            task.operate_click(0.5, 0.5, require_fresh_target=True)
                        self.assertTrue(caught.exception.input_not_dispatched)
                    self.assertEqual(int(accepted), task.click.call_count)
                    self.assertEqual(mode == CLICK_MODE_STANDARD, "unblock_input" in calls)
                    self.assertFalse(interaction._operating)

    def test_capture_only_publishes_frames_from_an_accessible_input_desktop(self):
        denied = win32api.error(5, "GetCursorPos", "access denied")
        for mode in (CLICK_MODE_STANDARD, CLICK_MODE_BACKGROUND):
            for positions, count, waited in (
                ([denied, (1, 2), (1, 2)], 1, 0.25),
                ([(1, 2), denied, (1, 2), (1, 2), (1, 2)], 2, 0.25),
                ([(1, 2), (1, 2)], 1, 0.0),
            ):
                with self.subTest(mode=mode, reads=positions):
                    interaction, calls = make_interaction()
                    interaction.hwnd_window = SimpleNamespace(top_hwnd=123, hwnd=123)
                    interaction.set_click_mode_provider(lambda: mode)
                    task = object.__new__(BaseBD2Task)
                    task.info_set = Mock()
                    now = [0.0]

                    def wait(seconds):
                        now[0] += seconds

                    task._executor = SimpleNamespace(
                        interaction=interaction,
                        check_enabled=lambda: None,
                        reset_scene=lambda **_: None,
                        exit_event=SimpleNamespace(wait=wait),
                        device_manager=SimpleNamespace(capture_method="test"),
                    )
                    frames = [np.zeros((2, 2, 3), np.uint8) for _ in range(count)]
                    task.next_frame = Mock(side_effect=frames)
                    self.get_cursor.side_effect = positions
                    with (
                        patch.object(
                            win_input.win32gui, "GetForegroundWindow", side_effect=range(100, 116)
                        ),
                        patch.object(
                            win_input.win32gui, "GetWindowRect", return_value=(0, 0, 1920, 1080)
                        ),
                        patch.object(
                            win_input.win32gui, "GetClientRect", return_value=(0, 0, 1920, 1080)
                        ),
                        patch("src.tasks.BaseBD2Task.monotonic", side_effect=lambda: now[0]),
                        patch("src.tasks.BaseBD2Task.observe_frame") as observe,
                    ):
                        self.assertIs(frames[-1], task.capture_frame())
                    observe.assert_called_once_with(task, frames[-1])
                    self.assertEqual(count, task.next_frame.call_count)
                    self.assertEqual(waited, now[0])
                    self.assertEqual(0.0, task._active_time(lambda: now[0]))
                    self.assertEqual([], calls)

    def test_operate_tracks_lock_ownership_and_propagates_callback_errors(self):
        for name, buttons, error, expected in (
            ("held", [True, False, False], None, ["wait", "block", "outer", "restore", "unblock"]),
            (
                "race",
                [False, True, False, False],
                None,
                ["block", "unblock", "wait", "block", "outer", "restore", "unblock"],
            ),
            ("denied", [False], RuntimeError, ["block", "restore"]),
            ("cancel", [True], TaskDisabledException, ["wait", "restore"]),
            (
                "nested",
                [False, False],
                ValueError,
                ["block", "outer", "inner", "restore", "unblock"],
            ),
            ("unhealthy", [False, False], RuntimeError, ["block", "outer", "restore", "unblock"]),
        ):
            with self.subTest(scenario=name):
                interaction, calls = make_interaction()
                interaction._mouse_buttons_down = Mock(side_effect=buttons)
                interaction.block_input = lambda: calls.append("block") or name != "denied"
                interaction.unblock_input = lambda: calls.append("unblock") or name != "unhealthy"
                interaction._restore_cursor = lambda: calls.append("restore")

                def wait(_):
                    calls.append("wait")
                    if name == "cancel":
                        raise TaskDisabledException()

                def outer():
                    calls.append("outer")
                    if name == "nested":
                        interaction.operate(lambda: calls.append("inner"), block=True)
                        self.assertTrue(interaction._operating)
                        raise ValueError("callback failed")

                with self.assertRaises(error) if error else nullcontext():
                    interaction.operate(outer, block=True, input_wait=wait)
                self.assertEqual(expected, calls)
                self.assertFalse(interaction._operating)
                self.assertTrue(interaction.wait_until_idle(0))
                if name == "unhealthy":
                    with self.assertRaises(RuntimeError):
                        interaction.operate(outer)
                    self.assertEqual(expected, calls)

    def test_cursor_admission_recovers_times_out_or_propagates_cancellation(self):
        locked = win32api.error(5, "GetCursorPos", "denied")
        bad_window = win32api.error(1400, "GetCursorPos", "invalid window")
        for label, reads, writes, interruption, error, waits in (
            ("locked read", [locked, locked, (7, 8)], None, None, None, 2),
            ("locked write", None, [OSError("SetCursorPos"), None], None, None, 1),
            ("timeout", locked, None, None, TimeoutError, 120),
            ("disabled", locked, None, TaskDisabledException, TaskDisabledException, 1),
            ("finished", locked, None, FinishedException, FinishedException, 1),
            ("bad window", bad_window, None, None, win32api.error, 0),
        ):
            with self.subTest(case=label):
                interaction, calls = make_interaction()
                self.get_cursor.side_effect = reads
                self.set_cursor.reset_mock()
                self.set_cursor.side_effect = writes
                now = [0.0]

                def wait(seconds):
                    self.assertFalse(interaction._operating)
                    self.assertEqual([], calls)
                    now[0] += seconds
                    if interruption:
                        raise interruption()

                status = Mock()
                with patch.object(win_input.time, "monotonic", side_effect=lambda: now[0]):
                    with self.assertRaises(error) if error else nullcontext():
                        interaction.operate(
                            lambda: calls.append("click"),
                            block=True,
                            input_wait=wait,
                            input_status=status,
                        )
                self.assertEqual(waits * 0.25, now[0])
                self.assertFalse(interaction._operating)
                if error:
                    self.assertEqual([], calls)
                    self.set_cursor.assert_not_called()
                    self.assertEqual(0 if error is win32api.error else 1, status.call_count)
                else:
                    self.assertEqual(
                        ["block_input", "click", "restore_cursor", "unblock_input"], calls
                    )
                    self.assertTrue(interaction._activate_required)
                    self.assertEqual((7, 8) if reads else (1, 2), interaction.cursor_position)
                    self.assertEqual(2, status.call_count)

    def test_failed_mouse_down_posts_release_once_and_propagates_error(self):
        interaction, calls = make_interaction()
        interaction.try_activate = Mock()
        interaction._dynamic_target_hwnd = 0
        interaction.hwnd_window = SimpleNamespace(top_hwnd=123, hwnd=123)
        error = win32api.error(5, "PostMessage", "access denied")
        with (
            patch.object(win_input.win32gui, "PostMessage", side_effect=[error, None]) as post,
            patch.object(win_input.time, "sleep"),
            self.assertRaises(win32api.error) as caught,
        ):
            interaction.operate(lambda: interaction.click(10, 10, move=False), block=True)
        self.assertIs(error, caught.exception)
        self.assertFalse(error.input_not_dispatched)
        self.assertEqual(
            [win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP],
            [c.args[1] for c in post.call_args_list],
        )
        self.assertEqual(["block_input", "restore_cursor", "unblock_input"], calls)

    def test_background_click_scroll_and_drag_send_only_window_input(self):
        interaction, calls = make_interaction()
        interaction.set_click_mode_provider(lambda: CLICK_MODE_BACKGROUND)
        with patch.object(PostMessageInteraction, "click", autospec=True) as post:
            interaction.click(320, 540, move_back=True, down_time=0.02)
        post.assert_called_once_with(
            interaction, 320, 540, move_back=False, name=None, down_time=0.02, move=True, key="left"
        )
        self.set_cursor.assert_not_called()
        interaction.scroll = Mock()
        interaction.move = Mock()
        interaction.mouse_down = Mock()
        interaction.mouse_up = Mock()
        interaction.try_activate = Mock()
        interaction.capture = SimpleNamespace(get_abs_cords=lambda x, y: (x, y))
        task = object.__new__(BaseBD2Task)
        task._executor = SimpleNamespace(interaction=interaction)
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._wait_for_input = Mock()
        task.info_set = Mock()
        task.sleep = Mock()
        with patch("win32api.mouse_event") as mouse, patch("time.sleep"):
            task.scroll_client((0.25, 0.5), 1, count=2)
            task.drag_client((100, 100), (200, 300), duration=0.1)
        self.api_set.assert_not_called()
        self.assertEqual([call((1, 2)), call((1, 2))], self.set_cursor.call_args_list)
        mouse.assert_not_called()
        self.assertEqual([], calls)
        self.assertEqual(2, interaction.scroll.call_count)
        interaction.scroll.assert_called_with(480, 540, 1)
        interaction.mouse_down.assert_called_once_with(100, 100)
        interaction.mouse_up.assert_called_once()
        self.assertEqual((200, 300), interaction.move.call_args.args)

    def test_activation_retries_each_background_input_until_foreground(self):
        interaction, _ = make_interaction()
        interaction._activated_hwnds = (100,)
        interaction.hwnd_window = SimpleNamespace(
            hwnd=100, top_hwnd=200, is_foreground=Mock(), visible_monitors=[]
        )
        interaction._dynamic_target_hwnd = 0
        interaction.on_visible(False)
        self.assertEqual((), interaction._activated_hwnds)
        self.assertTrue(interaction._activate_required)
        with patch.object(PostMessageInteraction, "activate", autospec=True) as activate:
            for samples, required, hwnds in (
                ([False, False], True, [100, 200]),
                ([False, True], False, [100, 200]),
                ([True], False, []),
                ([False, False], True, [100, 200]),
            ):
                with self.subTest(foreground=samples):
                    interaction.hwnd_window.is_foreground.side_effect = samples
                    activate.reset_mock()
                    interaction.try_activate()
                    self.assertEqual(required, interaction._activate_required)
                    self.assertEqual(hwnds, [c.args[-1] for c in activate.call_args_list])
        interaction.on_visible(True)
        self.assertFalse(interaction._activate_required)

    def test_input_probes_dispatch_configured_mouse_events(self):
        interaction, _ = make_interaction()
        frame = np.zeros((1080, 1920, 3), np.uint8)
        executor = SimpleNamespace(scene=None, interaction=None)
        selector = BD2ClickModeSelectorTask(executor, SimpleNamespace())
        selector.config = {CLICK_MODE_KEY: CLICK_MODE_STANDARD}
        selector.on_create()
        executor.interaction = interaction
        self.assertFalse(selector.should_trigger())
        self.assertFalse(interaction.background_click_enabled())
        selector.config[CLICK_MODE_KEY] = CLICK_MODE_BACKGROUND
        self.assertTrue(interaction.background_click_enabled())
        for kind, config, posts, scrolls in (
            (
                BD2BackgroundMouseClickInputTestTask,
                {
                    REFERENCE_POINT_MODE_KEY: True,
                    REFERENCE_POINT_X_KEY: 173,
                    REFERENCE_POINT_Y_KEY: 54,
                    BACKGROUND_MOUSE_MOVE_KEY: False,
                },
                [
                    call(
                        interaction,
                        173,
                        54,
                        move_back=False,
                        name="bd2_background_mouse_click",
                        down_time=0.02,
                        move=False,
                        key="left",
                    )
                ],
                [],
            ),
            (
                BD2MouseWheelInputTestTask,
                {},
                [
                    call(
                        interaction,
                        346,
                        538,
                        move_back=False,
                        name="bd2_mouse_wheel_focus",
                        down_time=0.02,
                        move=True,
                        key="left",
                    )
                ],
                [call(346, 538, 1)] * 9,
            ),
        ):
            with self.subTest(probe=kind.__name__):
                task = object.__new__(kind)
                task._executor = executor
                task.config = config
                task.run_input_probe = lambda _, details, action: action(frame)
                task.operate = lambda action, **_: action()
                task.sleep = Mock()
                interaction.scroll = Mock()
                with patch.object(PostMessageInteraction, "click", autospec=True) as post:
                    task.run()
                self.assertEqual(posts, post.call_args_list)
                self.assertEqual(scrolls, interaction.scroll.call_args_list)
                self.assertEqual([call(0.1)] * max(0, len(scrolls) - 1), task.sleep.call_args_list)
                self.set_cursor.assert_not_called()


class TaskFeedbackTest(unittest.TestCase):
    def test_snapshots_are_independent_during_concurrent_info_updates(self):
        for instance_lock in (False, True):
            with self.subTest(instance_lock=instance_lock):
                task = object.__new__(BaseBD2Task)
                task.info = {}
                task.logger = Mock(spec=logging.Logger)
                if instance_lock:
                    task._info_lock = threading.RLock()
                start = threading.Barrier(3)

                def update():
                    start.wait(timeout=5)
                    for i in range(300):
                        task.info_set(f"k{i % 20}", i)
                        task.info_incr("counter", 2)
                        task.info_add("added", 3)
                        task.info_add_to_list("items", i)

                def clear():
                    start.wait(timeout=5)
                    for _ in range(200):
                        task.info_clear()

                with ThreadPoolExecutor(2) as pool:
                    writes = [pool.submit(update), pool.submit(clear)]
                    start.wait(timeout=5)
                    for _ in range(200):
                        self.assertIsInstance(task_info_snapshot(task), dict)
                    for writer in writes:
                        writer.result(timeout=5)
                task.info_set("状态", "运行中")
                snapshot = task_info_snapshot(task)
                self.assertEqual("运行中", snapshot["状态"])
                snapshot["状态"] = "修改"
                self.assertEqual("运行中", task.info["状态"])

        class RacyInfo(dict):
            def keys(self):
                raise RuntimeError("dictionary changed size during iteration")

            __iter__ = keys

        self.assertEqual({}, task_info_snapshot(SimpleNamespace(info=RacyInfo(a=1))))

    def test_completion_notification_resumes_after_child_finishes(self):
        task = SimpleNamespace(log_info=Mock())
        with suppress_task_completion_notifications(task):
            log_task_completion(task, "子任务完成")
        log_task_completion(task, "任务完成")
        self.assertEqual(
            [call("子任务完成", notify=False), call("任务完成", notify=True)],
            task.log_info.call_args_list,
        )
