import unittest
from types import SimpleNamespace

import numpy as np

from src.tasks.BD2InputTestTask import (
    DEFAULT_WHEEL_REGION,
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

    def test_default_region_preserves_supplied_1920_by_1080_calibration(self):
        self.assertEqual(
            (228 / 1920, 117 / 1080, 463 / 1920, 959 / 1080),
            DEFAULT_WHEEL_REGION,
        )

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

    def test_wheel_action_supports_down_and_scales_to_client_size(self):
        task, interaction, _operate_calls = self.make_task()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        task._perform_wheel_action(frame, (0.25, 0.75), -1, 2, 0.2)

        self.assertEqual((320, 540), interaction.clicks[0][:2])
        self.assertEqual([(320, 540, -1), (320, 540, -1)], interaction.scrolls)
        self.assertEqual([0.2], task.sleeps)

    def test_configured_region_clamps_and_corrects_reversed_edges(self):
        task, _interaction, _operate_calls = self.make_task()
        task.config = {
            "区域左 X 百分比": 80,
            "区域上 Y 百分比": 120,
            "区域右 X 百分比": 20,
            "区域下 Y 百分比": -10,
        }

        self.assertEqual((0.2, 0.0, 0.8, 1.0), task._configured_wheel_region())


if __name__ == "__main__":
    unittest.main()
