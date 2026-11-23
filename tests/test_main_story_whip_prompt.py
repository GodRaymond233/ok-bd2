"""Real screenshot regression: the nearby whip prompt must beat tracker clicks."""

import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from src.tasks import main_story_walk
from tests import test_main_story_walk as walk_tests
from tests.test_main_story_walk import (
    PROMPT_CLICK,
    TRACKER_OBJECTIVE_POINT,
    MainStoryWalkState,
    _FakeWalkTask,
)


def screenshot_frame():
    frame = np.zeros((2158, 3838, 3), np.uint8)
    root = Path(__file__).parent / "fixtures" / "main_story_whip"
    for name, x, y in (
        ("world_f.png", 1780, 695),
        ("tracker_e.png", 3090, 290),
        ("hud_f.png", 2700, 1690),
    ):
        crop = np.array(Image.open(root / name).convert("RGB"))[..., ::-1]
        frame[y : y + crop.shape[0], x : x + crop.shape[1]] = crop
    return frame


class WhipPromptTest(unittest.TestCase):
    def test_real_world_and_hud_f_are_found_and_tracker_e_is_rejected(self):
        task = main_story_walk.MainStoryWalkFeatureMixin()
        task.config = {}
        task.log_warning = lambda *_a, **_k: None
        task.info_set = lambda *_a: None
        frame = screenshot_frame()
        target = task._walk_interact_target(frame)
        self.assertIsNotNone(target)
        point, _, _ = target
        self.assertAlmostEqual(908, point[0], delta=5)
        self.assertAlmostEqual(366, point[1], delta=5)
        self.assertIsNotNone(task._walk_hud_interact_target(frame))
        # The task tracker remains on screen after the world F disappears.
        frame[695:795, 1780:2190] = 0
        self.assertIsNone(task._walk_interact_target(frame))

    def test_visible_prompt_interacts_before_advancing_tracker(self):
        task = _FakeWalkTask([(MainStoryWalkState.FIELD, "寻找魔法工…0/2")])
        task.interact_score = 0.9778
        task.interact_works = True
        task.interact_result_state = MainStoryWalkState.BATTLE
        self.assertTrue(walk_tests.WalkLoopTest()._run(task))
        self.assertEqual([PROMPT_CLICK], task.clicks)
        self.assertNotIn(TRACKER_OBJECTIVE_POINT, task.clicks)
