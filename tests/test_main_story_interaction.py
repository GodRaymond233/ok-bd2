"""Interaction glyphs, progress counters, animation, and bounded retries."""

import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from src.tasks import main_story_walk as walk
from tests import test_main_story_walk as helpers


def helmet_frame():
    frame = np.zeros((2158, 3837, 3), np.uint8)
    root = Path(__file__).parent / "fixtures" / "main_story_helmet"
    for name, x, y in (
        ("world_f.png", 1780, 695),
        ("tracker_e.png", 3090, 290),
        ("hud_f.png", 2700, 1690),
    ):
        crop = np.array(Image.open(root / name).convert("RGB"))[..., ::-1]
        frame[y : y + crop.shape[0], x : x + crop.shape[1]] = crop
    return frame


class InteractionTest(unittest.TestCase):
    def test_helmet_f_is_found_on_bright_background_at_multiple_resolutions(self):
        task = walk.MainStoryWalkFeatureMixin()
        task.config = {}
        task.info_set = lambda *_a: None
        task.log_warning = lambda *_a, **_k: None
        for size in ((3837, 2158), (2560, 1440), (1920, 1080)):
            with self.subTest(size=size):
                frame = cv2.resize(helmet_frame(), size)
                target = task._walk_interact_target(frame)
                self.assertIsNotNone(target)
                self.assertAlmostEqual(905, target[0][0], delta=5)
                self.assertAlmostEqual(368, target[0][1], delta=5)

    def test_progress_counter_restarts_stall_timer(self):
        task = helpers._FakeWalkTask(
            [(walk.MainStoryWalkState.FIELD, "")],
            config={
                "推主线停滞秒数": 15,
                "推主线时间上限分钟": 0.4,
                "推主线推进间隔秒数": 1000,
            },
        )
        start = task.clock.now
        task._walk_state = lambda *_a: (
            walk.MainStoryWalkState.FIELD,
            "目标",
            f"寻找魔法工具{int((task.clock.now - start) // 10)}/2",
            True,
            None,
        )
        self.assertTrue(helpers.WalkLoopTest()._run(task))
        self.assertEqual([], task.alerts)

    def test_stationary_prompt_is_not_blocked_by_animated_background(self):
        task = helpers._FakeWalkTask(
            [(walk.MainStoryWalkState.FIELD, "寻找魔法工具1/2")],
            frames=lambda index: np.full(
                (1080, 1920, 3), 200 if index % 2 else 0, np.uint8
            ),
        )
        task.interact_score = 0.98
        task.interact_works = True
        task.interact_result_state = walk.MainStoryWalkState.BATTLE
        self.assertTrue(helpers.WalkLoopTest()._run(task))
        self.assertEqual([helpers.PROMPT_CLICK], task.clicks)

    def test_one_missing_frame_does_not_confirm_prompt_consumed(self):
        task = helpers._FakeWalkTask(
            [(walk.MainStoryWalkState.FIELD, "寻找魔法工具1/2")]
        )
        samples = iter([None, ((900, 368), "F", 0.98)] * 10)
        task._walk_interact_target = lambda *_a: next(samples)
        with patch.object(walk, "monotonic", task.clock):
            self.assertFalse(task._walk_prompt_consumed(0.2))
