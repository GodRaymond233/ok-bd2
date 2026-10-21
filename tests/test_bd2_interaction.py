import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import win32api
from ok.device.intercation import PostMessageInteraction
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.interaction.BD2Interaction import (
    CLICK_MODE_BACKGROUND,
    BD2Interaction,
)
from src.tasks.BaseBD2Task import BaseBD2Task


def make_interaction():
    """Build a BD2Interaction without running its device ``__init__``."""
    interaction = object.__new__(BD2Interaction)
    interaction._input_lock = threading.RLock()
    interaction._operating = False
    interaction._click_mode_provider = None
    interaction.cursor_position = (1, 2)
    calls = []
    interaction._restore_cursor = lambda: calls.append("restore_cursor")
    interaction.block_input = lambda: calls.append("block_input")
    interaction.unblock_input = lambda: calls.append("unblock_input")
    return interaction, calls


class BD2InteractionOperateTest(unittest.TestCase):
    def test_task_waits_for_cursor_access_before_dispatching_once(self):
        interaction, calls = make_interaction()
        task = object.__new__(BaseBD2Task)
        task.info_set = lambda key, message: calls.append((key, message))

        def wait(seconds):
            self.assertEqual(0.25, seconds)
            self.assertFalse(interaction._operating)
            self.assertNotIn("block_input", calls)
            self.assertNotIn("click", calls)
            calls.append("wait")

        task._executor = SimpleNamespace(
            interaction=interaction, check_enabled=lambda: None,
            exit_event=SimpleNamespace(wait=wait), reset_scene=lambda **_kwargs: None,
        )
        denied = win32api.error(5, "GetCursorPos", "access denied")
        with patch(
            "src.interaction.BD2Interaction.GetCursorPos",
            side_effect=[denied, denied, (7, 8)],
        ):
            self.assertEqual(42, task.operate(lambda: calls.append("click") or 42))
        self.assertEqual(2, calls.count("wait"))
        self.assertEqual(1, calls.count("click"))
        self.assertEqual((7, 8), interaction.cursor_position)
        self.assertFalse(interaction._operating)
        self.assertTrue(interaction._activate_required)
        self.assertEqual(["block_input", "click", "restore_cursor", "unblock_input"], calls[-4:])

    def test_input_wait_timeout_or_cancellation_never_dispatches_or_blocks(self):
        for interruption in (None, TaskDisabledException(), FinishedException()):
            with self.subTest(interruption=type(interruption).__name__):
                interaction, calls = make_interaction()

                def wait(_seconds):
                    if interruption is not None:
                        raise interruption

                with (
                    patch("src.interaction.BD2Interaction.GetCursorPos", side_effect=
                          win32api.error(5, "GetCursorPos", "access denied")),
                    patch("src.interaction.BD2Interaction.time.monotonic", side_effect=[0, 0, 30]),
                ):
                    with self.assertRaises(type(interruption) if interruption else TimeoutError):
                        interaction.operate(
                            lambda: calls.append("click"), block=True, input_wait=wait,
                        )
                self.assertEqual([], calls)
                self.assertFalse(interaction._operating)
                self.assertTrue(interaction.wait_until_idle(0))

    def test_non_access_errors_and_callback_errors_are_not_replayed(self):
        for from_callback in (False, True):
            with self.subTest(from_callback=from_callback):
                interaction, calls = make_interaction()
                error = win32api.error(5 if from_callback else 1400, "test", "failure")

                def callback():
                    calls.append("callback")
                    raise error

                with patch(
                    "src.interaction.BD2Interaction.GetCursorPos",
                    side_effect=None if from_callback else error, return_value=(7, 8),
                ):
                    with self.assertRaises(win32api.error) as caught:
                        interaction.operate(
                            callback, block=True, input_wait=lambda _: calls.append("wait"),
                        )
                self.assertIs(error, caught.exception)
                self.assertNotIn("wait", calls)
                self.assertEqual(int(from_callback), calls.count("callback"))
                self.assertFalse(interaction._operating)

    def test_operate_reraises_callback_exception_and_cleans_up(self):
        interaction, calls = make_interaction()
        error = ValueError("boom")

        def failing_callback():
            raise error

        with patch(
            "src.interaction.BD2Interaction.GetCursorPos",
            return_value=(7, 8),
        ):
            with self.assertRaises(ValueError) as caught:
                interaction.operate(failing_callback, block=True)

        self.assertIs(error, caught.exception)
        self.assertFalse(interaction._operating)
        self.assertIn("block_input", calls)
        self.assertIn("restore_cursor", calls)
        self.assertIn("unblock_input", calls)

    def test_operate_returns_callback_result_and_cleans_up(self):
        interaction, calls = make_interaction()

        with patch(
            "src.interaction.BD2Interaction.GetCursorPos",
            return_value=(7, 8),
        ) as get_cursor_pos:
            result = interaction.operate(lambda: 42, block=True)

        self.assertEqual(42, result)
        self.assertFalse(interaction._operating)
        get_cursor_pos.assert_called_once_with()
        self.assertIn("block_input", calls)
        self.assertIn("restore_cursor", calls)
        self.assertIn("unblock_input", calls)

    def test_nested_operate_keeps_outer_layer_state_isolated(self):
        interaction, calls = make_interaction()
        states_after_inner = []

        def outer_callback():
            inner_result = interaction.operate(lambda: "inner", block=True)
            states_after_inner.append((inner_result, interaction._operating))
            raise RuntimeError("outer boom")

        with patch(
            "src.interaction.BD2Interaction.GetCursorPos",
            return_value=(7, 8),
        ) as get_cursor_pos:
            with self.assertRaises(RuntimeError):
                interaction.operate(outer_callback)

        self.assertEqual([("inner", True)], states_after_inner)
        self.assertFalse(interaction._operating)
        get_cursor_pos.assert_called_once_with()
        self.assertEqual(1, calls.count("restore_cursor"))


class BD2InteractionClickModeTest(unittest.TestCase):
    def test_background_mode_routes_click_through_post_message_without_cursor_move(self):
        interaction, _calls = make_interaction()
        interaction.set_click_mode_provider(lambda: CLICK_MODE_BACKGROUND)

        with (
            patch.object(PostMessageInteraction, "click", autospec=True) as post_click,
            patch("src.interaction.BD2Interaction.SetCursorPos") as set_cursor_pos,
        ):
            interaction.click(320, 540, move_back=True, down_time=0.02)

        post_click.assert_called_once_with(
            interaction,
            320,
            540,
            move_back=False,
            name=None,
            down_time=0.02,
            move=True,
            key="left",
        )
        set_cursor_pos.assert_not_called()

    def test_operate_click_does_not_block_input_in_background_mode(self):
        task = object.__new__(BaseBD2Task)
        task._executor = SimpleNamespace(
            interaction=SimpleNamespace(background_click_enabled=lambda: True),
        )
        task._check_action_interval = lambda *_args: True
        task.click = lambda *_args, **_kwargs: True
        task.info_set = lambda *_args: None
        task.sleep = lambda *_args: None
        operate_options = []

        def operate(action, block=True, restore_cursor=True):
            operate_options.append((block, restore_cursor))
            return action()

        task.operate = operate
        task.operate_click(0.25, 0.75)

        self.assertEqual([(False, False)], operate_options)


class BD2InteractionActivationTest(unittest.TestCase):
    def test_try_activate_keeps_retry_flag_until_foreground_is_verified(self):
        interaction, _calls = make_interaction()
        interaction._activate_required = True
        foreground = iter((False, False, True))
        interaction.hwnd_window = SimpleNamespace(is_foreground=lambda: next(foreground))

        with patch.object(PostMessageInteraction, "try_activate") as parent_activate:
            interaction.try_activate()
            self.assertTrue(interaction._activate_required)
            parent_activate.assert_called_once_with()

            interaction.try_activate()

        self.assertFalse(interaction._activate_required)


if __name__ == "__main__":
    unittest.main()
