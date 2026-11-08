import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.SquareGoddessTask import (
    GODDESS_ALREADY_COMPLETE,
    GODDESS_NAVIGATION_CLICKED,
    SquareGoddessTask,
)
from src.utils.goddess_navigation import (
    NavigationObservation,
    TextBox,
    is_goddess_completion,
    is_goddess_destination,
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
            "每日任务向女神像许愿 １／１ 完成！",
        ):
            self.assertTrue(is_goddess_completion(text))
        for text in (
            "向女神像许愿0/1",
            "向女神像许愿进行中",
            "向女神像许愿0/1未完成",
            "向女神像许愿1/2完成",
            "向女神像许愿0/0完成",
            "向女神像许愿任务尚未完成",
            "玩家：向女神像许愿1/1完成！",
            "每日派遣1/1完成！",
            "创建队伍",
        ):
            self.assertFalse(is_goddess_completion(text))

    def test_navigation_requires_two_consecutive_stable_observations(self):
        task = object.__new__(SquareGoddessTask)
        task.info_set = lambda *args: None
        task._goddess_prayer_signals = lambda frame: (None, False, "创建队伍")
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
        task.info_set = lambda *args: None
        task._goddess_prayer_signals = lambda frame: (None, False, "创建队伍")
        task._match = lambda *args: None
        task._passes = lambda *args: False
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

    def test_completion_rejects_unknown_with_task_text_or_other_state(self):
        for state, text in (("unknown", "每日奖励"), ("ready", ""), ("ambiguous", "")):
            task = object.__new__(SquareGoddessTask)
            task.sleep = lambda *args: None
            task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
            task._observe_goddess_navigation = (
                lambda frame, state=state, text=text: NavigationObservation(state, text)
            )
            task._goddess_prayer_signals = lambda *args: (None, False, "创建队伍")
            task._match = lambda *args: None
            task._passes = lambda *args: True
            with patch("src.tasks.SquareGoddessTask.monotonic", side_effect=[0, 0, 3]):
                self.assertFalse(task._wait_for_daily_navigation_to_disappear(2))

    def test_completion_needs_stable_square_and_absent_prayer_prompt(self):
        for square, prayer, text, expected in (
            (True, False, "创建队伍", True), (False, False, "创建队伍", False),
            (True, True, "许愿", False), (True, False, "", False),
        ):
            task = object.__new__(SquareGoddessTask)
            task.sleep = lambda *args: None
            task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
            task._observe_goddess_navigation = lambda frame: NavigationObservation(
                "absent", "创建队伍"
            )
            task._goddess_prayer_signals = lambda *args: (None, prayer, text)
            task._match = lambda *args: None
            task._passes = lambda *args: square
            clock = iter(range(20))
            with patch("src.tasks.SquareGoddessTask.monotonic", side_effect=clock):
                self.assertEqual(expected, task._wait_for_daily_navigation_to_disappear(8))
