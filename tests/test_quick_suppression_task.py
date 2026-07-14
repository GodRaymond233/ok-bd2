import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.QuickSuppressionTask import (
    CHAPTER_ONE_POINT,
    CHAPTER_TWO_POINT,
    NEW_CARTRIDGE_NOTICE,
    QUICK_SWITCH_POINT,
    SUPPRESSION_POINTS,
    QuickSuppressionTask,
)


class QuickSuppressionTaskTest(unittest.TestCase):
    def test_slot_config_is_dropdown_defaulting_to_four(self):
        task = object.__new__(QuickSuppressionTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch(
            "src.tasks.QuickSuppressionTask.BaseBD2Task.__init__",
            return_value=None,
        ):
            QuickSuppressionTask.__init__(task)

        self.assertEqual("4号位", task.default_config["刷几号位"])
        self.assertEqual(
            ["4号位", "5号位"],
            task.config_type["刷几号位"]["options"],
        )
        self.assertEqual("刷压制等级", task.name)
        self.assertEqual(
            "在第一章战斗地图中开始，同时要求第二章也处于战斗地图中",
            task.description,
        )
        self.assertEqual("自动刷级", task.group_name)

    def test_reference_points_are_stored_as_ratios(self):
        self.assertEqual((1680 / 1920, 740 / 1080), SUPPRESSION_POINTS["4号位"])
        self.assertEqual((1792 / 1920, 791 / 1080), SUPPRESSION_POINTS["5号位"])
        self.assertEqual((910 / 1920, 1000 / 1080), QUICK_SWITCH_POINT)
        self.assertEqual((871 / 1920, 970 / 1080), CHAPTER_TWO_POINT)
        self.assertEqual((690 / 1920, 974 / 1080), CHAPTER_ONE_POINT)

    def test_cycle_uses_selected_slot_for_both_suppression_clicks(self):
        task = object.__new__(QuickSuppressionTask)
        task.info_set = lambda *_args, **_kwargs: None
        clicks = []
        switches = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task._switch_chapter = lambda chapter, point: switches.append((chapter, point)) or True

        self.assertTrue(task._run_cycle("5号位"))
        self.assertEqual(
            [
                (*SUPPRESSION_POINTS["5号位"], 2.0),
                (*SUPPRESSION_POINTS["5号位"], 2.0),
            ],
            clicks,
        )
        self.assertEqual(
            [("第二章", CHAPTER_TWO_POINT), ("第一章", CHAPTER_ONE_POINT)],
            switches,
        )

    def test_switch_waits_two_seconds_after_loading_disappears_then_ocr(self):
        task = object.__new__(QuickSuppressionTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        sleeps = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task._wait_for_loading_cycle = lambda _chapter: True
        task.sleep = lambda seconds: sleeps.append(seconds)
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._ocr_text = lambda *_args, **_kwargs: NEW_CARTRIDGE_NOTICE

        self.assertTrue(task._switch_chapter("第二章", CHAPTER_TWO_POINT))
        self.assertEqual(
            [(*QUICK_SWITCH_POINT, 2.0), (*CHAPTER_TWO_POINT, 0)],
            clicks,
        )
        self.assertEqual([2.0], sleeps)

    def test_switch_stops_when_loading_cycle_is_not_confirmed(self):
        task = object.__new__(QuickSuppressionTask)
        task.operate_click = lambda *_args, **_kwargs: None
        task._wait_for_loading_cycle = lambda _chapter: False
        task.sleep = lambda *_args, **_kwargs: self.fail("must not wait for OCR")
        task.capture_frame = lambda: self.fail("must not OCR")

        self.assertFalse(task._switch_chapter("第一章", CHAPTER_ONE_POINT))

    def test_notice_matching_ignores_ocr_whitespace(self):
        self.assertTrue(QuickSuppressionTask._contains_notice("立刻前往 探索\n告示板"))
        self.assertFalse(QuickSuppressionTask._contains_notice("探索告示板"))


if __name__ == "__main__":
    unittest.main()
