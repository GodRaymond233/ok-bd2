import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from ok.device.intercation import PostMessageInteraction

from src.interaction.BD2Interaction import (
    CLICK_MODE_BACKGROUND,
    CLICK_MODE_OPTIONS,
    CLICK_MODE_STANDARD,
)
from src.tasks.BD2InputTestTask import (
    BACKGROUND_MOUSE_MOVE_KEY,
    CLICK_MODE_KEY,
    DEFAULT_WHEEL_REGION,
    REFERENCE_POINT_MODE_KEY,
    REFERENCE_POINT_X_KEY,
    REFERENCE_POINT_Y_KEY,
    BD2BackgroundMouseClickInputTestTask,
    BD2ClickModeSelectorTask,
    BD2MouseWheelInputTestTask,
)


class FakeInteraction:
    def __init__(self):
        self.clicks = []
        self.scrolls = []

    def click(self, x, y, **kwargs):
        self.clicks.append((x, y, kwargs))

    def scroll(self, x, y, amount):
        self.scrolls.append((x, y, amount))


class BD2MouseWheelInputTestTaskTest(unittest.TestCase):
    def make_task(self):
        task = object.__new__(BD2MouseWheelInputTestTask)
        interaction = FakeInteraction()
        task._executor = SimpleNamespace(interaction=interaction)
        task.config = {}
        task.sleeps = []
        task.sleep = task.sleeps.append
        operate_calls = []

        def operate(action, block=True, restore_cursor=True):
            operate_calls.append((block, restore_cursor))
            return action()

        task.operate = operate
        task.log_warning = lambda *_args, **_kwargs: None
        return task, interaction, operate_calls

    def test_wheel_action_clicks_center_then_scrolls_up_nine_times(self):
        task, interaction, operate_calls = self.make_task()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        left, top, right, bottom = DEFAULT_WHEEL_REGION
        center = ((left + right) / 2, (top + bottom) / 2)

        task._perform_wheel_action(frame, center, 1, 9, 0.1)

        self.assertEqual([(True, True)], operate_calls)
        self.assertEqual(1, len(interaction.clicks))
        click_x, click_y, click_options = interaction.clicks[0]
        self.assertEqual((346, 538), (click_x, click_y))
        self.assertFalse(click_options["move_back"])
        self.assertTrue(click_options["move"])
        self.assertEqual([(346, 538, 1)] * 9, interaction.scrolls)
        self.assertEqual([0.1] * 8, task.sleeps)


class BD2BackgroundMouseClickInputTestTaskTest(unittest.TestCase):

    def test_run_uses_reference_point_and_configured_message_move(self):
        task = object.__new__(BD2BackgroundMouseClickInputTestTask)
        task.config = {
            REFERENCE_POINT_MODE_KEY: True,
            REFERENCE_POINT_X_KEY: 173,
            REFERENCE_POINT_Y_KEY: 54,
            BACKGROUND_MOUSE_MOVE_KEY: False,
        }
        clicks = []
        task._perform_background_click = lambda frame, x, y, send_move: clicks.append(
            (frame.shape, x, y, send_move)
        )
        task.run_input_probe = lambda _name, _details, action: action(
            np.zeros((1080, 1920, 3), dtype=np.uint8)
        )

        task.run()

        self.assertEqual([((1080, 1920, 3), 173 / 1920, 54 / 1080, False)], clicks)

    def test_background_click_bypasses_bd2_physical_click_override(self):
        task = object.__new__(BD2BackgroundMouseClickInputTestTask)
        interaction = object.__new__(PostMessageInteraction)
        task._executor = SimpleNamespace(interaction=interaction)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        with patch.object(PostMessageInteraction, "click", autospec=True) as click:
            task._perform_background_click(frame, 0.25, 0.75, True)

        click.assert_called_once_with(
            interaction,
            320,
            540,
            move_back=False,
            name="bd2_background_mouse_click",
            down_time=0.02,
            move=True,
            key="left",
        )


class BD2ClickModeSelectorTaskTest(unittest.TestCase):
    def test_selector_binds_after_interaction_initializes_and_updates_provider_live(self):
        providers = []
        executor = SimpleNamespace(scene=None, interaction=None)
        task = BD2ClickModeSelectorTask(
            executor,
            SimpleNamespace(),
        )

        self.assertEqual(CLICK_MODE_STANDARD, task.default_config[CLICK_MODE_KEY])
        self.assertEqual(
            list(CLICK_MODE_OPTIONS),
            task.config_type[CLICK_MODE_KEY]["options"],
        )

        task.config = {
            "_enabled": True,
            CLICK_MODE_KEY: CLICK_MODE_STANDARD,
        }
        task.on_create()
        self.assertEqual([], providers)

        executor.interaction = SimpleNamespace(
            set_click_mode_provider=providers.append,
        )
        self.assertFalse(task.should_trigger())
        provider = providers[-1]
        self.assertEqual(CLICK_MODE_STANDARD, provider())

        self.assertFalse(task.should_trigger())
        self.assertEqual(1, len(providers))

        task.config[CLICK_MODE_KEY] = CLICK_MODE_BACKGROUND
        self.assertEqual(CLICK_MODE_BACKGROUND, provider())

        task._enabled = False
        self.assertEqual(CLICK_MODE_STANDARD, provider())


if __name__ == "__main__":
    unittest.main()
