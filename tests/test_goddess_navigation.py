import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks.SquareGoddessTask import (
    GODDESS_ALREADY_COMPLETE,
    GODDESS_NAVIGATION_CLICKED,
    SquareGoddessTask,
)
from src.utils.goddess_navigation import (
    NEW_DAILY_ICON,
    NavigationObservation,
    TextBox,
    is_goddess_completion,
    is_goddess_destination,
    scan_navigation,
)


class GoddessNavigationTest(unittest.TestCase):
    def test_destination_preserves_order_and_allows_one_action_typo(self):
        for text in ("移动至艾力克史温女", "多动至艾力克史温女…"):
            self.assertTrue(is_goddess_destination(text))
        for text in ("每日派遣", "移动至其他任务", "女温史克力艾至动移"):
            self.assertFalse(is_goddess_destination(text))

    def test_completion_requires_exact_goddess_task_and_full_count(self):
        for text in (
            "向女神像许愿1/1完成！",
            "女神像许愿 1／1 完成",
            "向女神像许愿2/2完成！",
            "向女神像许愿任务已完成！",
        ):
            self.assertTrue(is_goddess_completion(text))
        for text in (
            "向女神像许愿0/1",
            "向女神像许愿进行中",
            "每日派遣1/1完成！",
            "创建队伍",
        ):
            self.assertFalse(is_goddess_completion(text))

    def test_title_anchor_scales_and_requires_neighbor_icon(self):
        for width, height in ((1920, 1080), (1280, 720)):
            for icon_present in (False, True):
                with self.subTest(size=(width, height), icon=icon_present):
                    calls = []

                    def ocr(**kwargs):
                        shape = kwargs["frame"].shape
                        calls.append(shape)
                        if shape[0] == int(height * .75):
                            return [SimpleNamespace(name="每日奖励", confidence=.99,
                                                    x=100, y=100, width=80, height=24)]
                        return [
                            SimpleNamespace(name="每日奖励", confidence=.99,
                                            x=100, y=40, width=100, height=30),
                            SimpleNamespace(name="多动至艾力克史温女", confidence=.99,
                                            x=100, y=75, width=240, height=30),
                        ]

                    observed_rois = []
                    result = scan_navigation(
                        np.zeros((height, width, 3), dtype=np.uint8), ocr=ocr,
                        normalize=lambda text: text,
                        match=lambda frame, spec: observed_rois.append(spec.roi),
                        passes=lambda *args: icon_present, icon_specs=(NEW_DAILY_ICON,),
                    )
                    self.assertEqual((height, width - int(width * .50), 3), calls[0])
                    self.assertEqual("ready" if icon_present else "unknown", result.state)
                    self.assertTrue(observed_rois)
                    if result.navigation:
                        self.assertGreaterEqual(result.navigation.x, int(width * .50))

    def test_navigation_requires_two_consecutive_stable_observations(self):
        task = object.__new__(SquareGoddessTask)
        task.sleep = lambda *args: None
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        first = TextBox("移动至艾力克史温女", .99, 900, 200, 180, 20)
        moved = TextBox("移动至艾力克史温女", .99, 900, 300, 180, 20)
        sequence = iter([
            NavigationObservation("ready", "", first),
            NavigationObservation("unknown", ""),
            NavigationObservation("ready", "", moved),
            NavigationObservation("ready", "", moved),
        ])
        task._observe_goddess_navigation = lambda frame: next(sequence)
        clicks = []
        task._click_client = lambda *args, **kwargs: clicks.append(args)
        with patch("src.tasks.SquareGoddessTask.monotonic", return_value=0):
            self.assertEqual(
                GODDESS_NAVIGATION_CLICKED,
                task._click_goddess_daily_navigation_until(1),
            )
        self.assertEqual([(990, 310, 1280, 720)], clicks)

    def test_explicit_completion_requires_two_consecutive_observations(self):
        task = object.__new__(SquareGoddessTask)
        task.sleep = lambda *args: None
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        sequence = iter([
            NavigationObservation("absent", "向女神像许愿1/1完成！"),
            NavigationObservation("absent", "创建队伍"),
            NavigationObservation("absent", "向女神像许愿1/1完成！"),
            NavigationObservation("absent", "向女神像许愿2/2完成！"),
        ])
        task._observe_goddess_navigation = lambda frame: next(sequence)
        task._click_client = lambda *args, **kwargs: self.fail("No navigation click")
        with patch("src.tasks.SquareGoddessTask.monotonic", return_value=0):
            self.assertEqual(
                GODDESS_ALREADY_COMPLETE,
                task._click_goddess_daily_navigation_until(1),
            )

    def test_completion_rejects_unknown_blank_or_remaining_task(self):
        for state in ("unknown", "ready", "ambiguous"):
            task = object.__new__(SquareGoddessTask)
            task.sleep = lambda *args: None
            task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
            task._observe_goddess_navigation = lambda frame: NavigationObservation(state, "")
            task._ocr_pattern_click_point = lambda *args, **kwargs: (None, "")
            task._match = lambda *args: None
            task._passes = lambda *args: True
            with patch("src.tasks.SquareGoddessTask.monotonic", side_effect=[0, 0, 3]):
                self.assertFalse(task._wait_for_daily_navigation_to_disappear(2))

    def test_completion_needs_stable_square_and_absent_prayer_prompt(self):
        for square, prayer, expected in ((True, None, True), (False, None, False),
                                         (True, (100, 100), False)):
            task = object.__new__(SquareGoddessTask)
            task.sleep = lambda *args: None
            task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
            task._observe_goddess_navigation = lambda frame: NavigationObservation(
                "absent", "创建队伍"
            )
            task._ocr_pattern_click_point = lambda *args, **kwargs: (prayer, "")
            task._match = lambda *args: None
            task._passes = lambda *args: square
            clock = iter(range(20))
            with patch("src.tasks.SquareGoddessTask.monotonic", side_effect=clock):
                self.assertEqual(expected, task._wait_for_daily_navigation_to_disappear(8))
