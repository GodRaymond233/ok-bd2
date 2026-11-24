import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from src.tasks import run_history
from src.tasks.BaseBD2Task import CartridgeSpecialPageResult
from src.tasks.map_trade.models import MatchResult
from src.tasks.map_trade.navigator_constants import QUICK_SWITCH_TEMPLATE as NAVIGATOR_SWITCH
from src.tasks.PVPTask import QUICK_PACK_TEMPLATE
from src.tasks.SquareGoddessTask import (
    FANTASIA_SQUARE_TEMPLATE,
    GODDESS_ALREADY_COMPLETE,
    GODDESS_NAVIGATION_CLICKED,
    QUICK_SWITCH_TEMPLATE,
    SQUARE_CARTRIDGE_SLOT_POINT,
    SQUARE_HOME_POINT,
    SQUARE_NOTICE_TEMPLATE,
    TEMPLATE_DIR,
    SquareGoddessTask,
)
from src.utils import task_vision
from src.utils.cartridge_quick_switch import (
    BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS,
    GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    LIFE_GAMEPLAY_CATEGORY_POINT,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    category_highlight_ratio,
)
from src.utils.goddess_navigation import (
    NEW_DAILY_ICON,
    NavigationObservation,
    TextBox,
    is_goddess_completion,
    scan_navigation,
)
from tests.helpers import FakeClock, StageHistoryTestCase

FIXTURES = Path(__file__).parent / "fixtures" / "quick_switch"


def square_task():
    task = object.__new__(SquareGoddessTask)
    task.config = {}
    task.info_set = Mock()
    task.log_info = Mock()
    task.sleep = Mock()
    task.operate_click = Mock()
    task._click_client = Mock()
    return task


class CartridgeFixtureTest(unittest.TestCase):
    def test_only_selected_category_is_highlighted(self):
        battle = BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION
        life = LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION
        for name, selected, other in (("battle", battle, life), ("life", life, battle)):
            with self.subTest(category=name):
                frame = cv2.imread(str(FIXTURES / f"{name}_gameplay_selected_fhd.png"))
                self.assertIsNotNone(frame)
                self.assertEqual((REFERENCE_HEIGHT, REFERENCE_WIDTH), frame.shape[:2])
                self.assertGreaterEqual(
                    category_highlight_ratio(frame, selected), GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO
                )
                self.assertLess(
                    category_highlight_ratio(frame, other), GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO
                )

    def test_dark_square_button_and_home_strip_across_all_three_specs(self):
        for name, expected in (("in_square_dark_button", True), ("home_bottom_strip", False)):
            frame = cv2.imread(str(FIXTURES / f"{name}_1600x901.png"))
            self.assertIsNotNone(frame)
            for spec in (QUICK_SWITCH_TEMPLATE, QUICK_PACK_TEMPLATE, NAVIGATOR_SWITCH):
                with self.subTest(frame=name, spec=spec):
                    result = task_vision.match_template(
                        frame, spec, {}, TEMPLATE_DIR, cache={}, min_size=5
                    )
                    self.assertEqual(expected, task_vision.passes_match(result, spec, {}), result)


def ocr_box(text, x, y, width, height=30):
    return SimpleNamespace(name=text, confidence=0.99, x=x, y=y, width=width, height=height)


class GoddessNavigationTest(unittest.TestCase):
    def test_scaled_navigation_anchors_require_destination_and_neighbor_icon(self):
        for width, height in ((1920, 1080), (1280, 720)):
            for destination in (
                ["移动至艾力克史温女"],
                ["多动至", "艾力克史温女"],
                ["每日派遣"],
                ["移动至其他任务"],
                ["女温史克力艾至动移"],
            ):
                for icon in (True, False):
                    with self.subTest(width=width, destination=destination, icon=icon):
                        boxes = [ocr_box("每日奖励", 100, 40, 100)]
                        boxes.extend(
                            ocr_box(text, 100 + i * 65, 75, 60 if len(text) == 3 else 175)
                            for i, text in enumerate(destination)
                        )
                        ocr = Mock(return_value=boxes)
                        match = Mock()
                        result = scan_navigation(
                            np.zeros((height, width, 3), np.uint8),
                            ocr=ocr,
                            normalize=str,
                            match=match,
                            passes=lambda *_: icon,
                            icon_specs=(NEW_DAILY_ICON,),
                        )
                        expected = icon and "艾力克史温女" in "".join(destination)
                        self.assertEqual(
                            (height, width // 2, 3), ocr.call_args_list[0].kwargs["frame"].shape
                        )
                        self.assertEqual(expected, result.state == "ready")
                        self.assertEqual(expected, result.navigation is not None)
                        if expected:
                            self.assertGreaterEqual(result.navigation.x, width // 2)
                            self.assertTrue(match.called)

    def test_completion_joins_only_neighboring_words_of_the_finished_goddess_task(self):
        for words, expected in (
            (["向女神像许愿", ("1/1完成！", 220, 102)], True),
            (["向女神像许愿", ("1/1完成！", 500, 102)], False),
            (["向女神像许愿", ("1/1完成！", 220, 180)], False),
            *[
                ([line], True)
                for line in (
                    "向女神像许愿1/1完成！",
                    "女神像许愿 1／1 完成",
                    "向女神像许愿2/2完成！",
                    "向女神像许愿任务已完成！",
                    "每日任务向女神像许愿 １／１ 完成！",
                )
            ],
            *[
                ([line], False)
                for line in (
                    "向女神像许愿0/1",
                    "向女神像许愿进行中",
                    "向女神像许愿0/1未完成",
                    "向女神像许愿1/2完成",
                    "向女神像许愿0/0完成",
                    "向女神像许愿任务尚未完成",
                    "玩家：向女神像许愿1/1完成！",
                    "每日派遣1/1完成！",
                    "创建队伍",
                )
            ],
        ):
            with self.subTest(words=words):
                boxes = [ocr_box(words[0], 30, 100, 180)]
                boxes.extend(ocr_box(text, x, y, 120) for text, x, y in words[1:])
                result = scan_navigation(
                    np.zeros((1080, 1920, 3), np.uint8),
                    ocr=Mock(return_value=boxes),
                    normalize=str,
                    match=Mock(),
                    passes=lambda *_: False,
                    icon_specs=(NEW_DAILY_ICON,),
                )
                self.assertIs(is_goddess_completion(result.text), expected)

    def test_navigation_requires_stable_rows_or_stable_completion_evidence(self):
        first = NavigationObservation(
            "ready", "", TextBox("移动至艾力克史温女", 0.99, 900, 200, 180, 20)
        )
        moved = NavigationObservation(
            "ready", "", TextBox("移动至艾力克史温女", 0.99, 900, 300, 180, 20)
        )
        done = NavigationObservation("absent", "向女神像许愿1/1完成！")
        absent = NavigationObservation("absent", "创建队伍")
        unknown = NavigationObservation("unknown", "每日奖励")
        for frames, square, text, expected, clicks in (
            (
                [first, unknown, moved, moved],
                True,
                "创建队伍",
                GODDESS_NAVIGATION_CLICKED,
                [call(990, 310, 1280, 720, after_sleep=2.0)],
            ),
            ([done, absent, done, done], False, "创建队伍", GODDESS_ALREADY_COMPLETE, []),
            ([absent] * 9, True, "创建队伍", GODDESS_ALREADY_COMPLETE, []),
            ([absent] * 9, False, "创建队伍", None, []),
            ([absent] * 9, True, "", None, []),
            ([unknown] * 9, True, "创建队伍", None, []),
        ):
            with self.subTest(expected=expected, square=square, text=text):
                task = square_task()
                clock = FakeClock()
                task.sleep = clock.sleep
                task.capture_frame = Mock(return_value=np.zeros((720, 1280, 3), np.uint8))
                task._observe_goddess_navigation = Mock(side_effect=frames)
                task._goddess_prayer_signals = Mock(return_value=(None, False, text))
                task._match = Mock()
                task._passes = Mock(return_value=square)
                with patch("src.tasks.SquareGoddessTask.monotonic", clock):
                    self.assertEqual(expected, task._click_goddess_daily_navigation_until(3))
                self.assertEqual(clicks, task._click_client.call_args_list)
                expected_reads = 4 if frames[0] in (first, done) else 7 if expected else 9
                self.assertEqual(expected_reads, task._observe_goddess_navigation.call_count)


class SquareGoddessTest(StageHistoryTestCase):
    def test_slot_two_requires_life_category_and_confirmed_square(self):
        category = call(*LIFE_GAMEPLAY_CATEGORY_POINT, after_sleep=0.0)
        slot = call(*SQUARE_CARTRIDGE_SLOT_POINT, after_sleep=0.0)
        for highlighted, square, expected, slots, rewards in (
            (False, [], False, [category], 0),
            (True, [True], True, [category, slot], 0),
            (True, [False, True], True, [category, slot], 1),
            (True, [False, False], False, [category, slot], 1),
        ):
            with self.subTest(highlighted=highlighted, square=square):
                task = square_task()
                task.open_cartridge_quick_switcher = Mock(return_value=True)
                task._wait_for_life_gameplay_category = Mock(return_value=highlighted)
                task._wait_for_template = Mock(side_effect=square)
                task._handle_recent_cartridge_special_pages = Mock(
                    return_value=CartridgeSpecialPageResult.HANDLED
                )
                self.assertIs(task._enter_square_from_home(), expected)
                self.assertEqual(slots, task.operate_click.call_args_list)
                self.assertEqual(
                    [call(0.5)]
                    + [call(FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS)] * int(highlighted),
                    task.sleep.call_args_list,
                )
                self.assertEqual(
                    [call(allow_pvp_pages=False)] * rewards,
                    task._handle_recent_cartridge_special_pages.call_args_list,
                )
                self.assertEqual(
                    [FANTASIA_SQUARE_TEMPLATE] * len(square),
                    [c.args[0] for c in task._wait_for_template.call_args_list],
                )

    def test_quick_switch_click_uses_three_consecutive_stable_frames(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(
            score=0.95,
            pixel_score=0.90,
            position=(760, 960),
            size=(64, 60),
            zncc_score=0.90,
        )
        # Six clear frames preceded the desktop interruption in the report.
        matches = iter([match] * 6)
        task._match = lambda *_: next(matches, MatchResult(-1.0, (0, 0), (0, 0)))
        task._passes = lambda candidate, _spec: candidate.score > 0.9
        task._mf_offset_for_frame = lambda *_args: (0, 0)
        sleeps = []
        task.sleep = sleeps.append
        clicks = []
        task._click_client = lambda x, y, width, height, after_sleep=0.0: clicks.append(
            (x, y, width, height, after_sleep)
        )

        self.assertTrue(
            SquareGoddessTask._click_template_until(
                task,
                QUICK_SWITCH_TEMPLATE,
                timeout=1,
                name="快速切换按钮",
                stabilize=True,
            )
        )
        self.assertEqual([(792, 990, 1920, 1080, 0.0)], clicks)
        self.assertEqual(2, len(sleeps))
        self.assertTrue(all(seconds == 0.1 for seconds in sleeps))

    def test_wish_is_recorded_before_dispatch_and_resumed_without_praying_twice(self):
        for navigation, confirmed, recorded in (
            (None, False, False),
            (GODDESS_NAVIGATION_CLICKED, False, False),
            (GODDESS_NAVIGATION_CLICKED, True, True),
            (GODDESS_ALREADY_COMPLETE, False, True),
        ):
            with self.subTest(navigation=navigation, confirmed=confirmed):
                self.reset_stage_history()
                task = square_task()
                task._click_goddess_daily_navigation_until = Mock(return_value=navigation)
                task.capture_frame = Mock()
                task._ocr_text = Mock(return_value="")
                task._wait_for_goddess_prayer_completion = Mock(return_value=confirmed)
                task._click_square_notice_if_present = Mock(side_effect=[False, True])
                self.assertFalse(task._pray_at_goddess())
                self.assertEqual(
                    recorded, run_history.read_stage(task, "女神像许愿").get("state") == "done"
                )
                self.assertIs(task._pray_at_goddess(), recorded)
                self.assertEqual(
                    1 if recorded else 2, task._click_goddess_daily_navigation_until.call_count
                )
                self.assertEqual(
                    int(navigation == GODDESS_NAVIGATION_CLICKED) * (1 if recorded else 2),
                    task._wait_for_goddess_prayer_completion.call_count,
                )
                self.assertEqual(
                    [call(timeout=5.0)] * (2 if recorded else 0),
                    task._click_square_notice_if_present.call_args_list,
                )

    def test_prayer_click_requires_unique_reliable_interaction_prompt(self):
        for width, height in ((1920, 1080), (1280, 720)):
            task = object.__new__(SquareGoddessTask)
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            task_label = SimpleNamespace(
                name="向女神像许愿0/1",
                confidence=0.99,
                x=width * 0.75,
                y=height * 0.2,
                width=200,
                height=30,
            )
            prompt = SimpleNamespace(
                name="向女神像许愿",
                confidence=0.99,
                x=width * (862 / 1920),
                y=height * (147 / 1080),
                width=120,
                height=30,
            )
            boxes = [task_label, prompt]
            task._bd2_ocr_boxes = lambda *args, **kwargs: boxes
            self.assertEqual(
                (round(prompt.x + 60), round(prompt.y + 15)),
                task._goddess_prayer_signals(frame)[0],
            )
            for x, y in ((0.15, 0.25), (0.65, 0.35), (0.7, 0.8)):
                prompt.x, prompt.y = width * x, height * y
                self.assertEqual(
                    (round(prompt.x + 60), round(prompt.y + 15)),
                    task._goddess_prayer_signals(frame)[0],
                )
            boxes.append(SimpleNamespace(**vars(prompt)))
            self.assertEqual((None, True), task._goddess_prayer_signals(frame)[:2])
            boxes.pop()
            boxes.append(
                SimpleNamespace(
                    name="1/1完成！",
                    confidence=0.99,
                    x=prompt.x + prompt.width + 5,
                    y=prompt.y,
                    width=100,
                    height=30,
                )
            )
            self.assertEqual((None, False), task._goddess_prayer_signals(frame)[:2])
            boxes.pop()
            prompt.confidence = 0.6
            self.assertEqual((None, True), task._goddess_prayer_signals(frame)[:2])
            prompt.name = "向女神像许愿1/1完成！"
            prompt.confidence = 0.99
            self.assertEqual((None, False), task._goddess_prayer_signals(frame)[:2])

    def test_prayer_keeps_observing_after_confirmation_window_and_click_limit(self):
        for outcome in ("late_absence", "completed", "blank", "prompt", "missing", "wrong scene"):
            with self.subTest(outcome=outcome):
                clock, clicks = [0.0], []
                task = object.__new__(SquareGoddessTask)
                task.config = {"女神像许愿最多点击次数": 1, "女神像完成确认等待秒数": 8.0}
                task.info_set = lambda *args: None
                task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
                task.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)

                def click(*args, **kwargs):
                    clicks.append(args)
                    task.sleep(kwargs.get("after_sleep", 0))

                def signals(frame):
                    if outcome == "missing":
                        return None, False, ""
                    if not clicks or outcome == "prompt":
                        return (1410, 880), True, "向女神像许愿"
                    return None, False, "" if outcome == "blank" else "创建队伍"

                def observe(frame):
                    if outcome == "blank":
                        return NavigationObservation("unknown", "")
                    if outcome == "completed":
                        return NavigationObservation("absent", "向女神像许愿1/1完成！")
                    if outcome == "prompt" or clock[0] < 11:
                        return NavigationObservation("unknown", "每日奖励")
                    return NavigationObservation("absent", "创建队伍")

                task._click_client = click
                task._goddess_prayer_signals = signals
                task._observe_goddess_navigation = observe
                task._match = lambda *args: None
                task._passes = lambda *args: outcome != "wrong scene"
                with patch("src.tasks.SquareGoddessTask.monotonic", lambda: clock[0]):
                    self.assertEqual(
                        outcome in {"late_absence", "completed"},
                        task._wait_for_goddess_prayer_completion(20),
                    )
                self.assertEqual(int(outcome != "missing"), len(clicks))
                self.assertLessEqual(clock[0], 21)

    def test_dispatch_uses_notice_center_or_two_empty_square_frames(self):
        for notice, square, expected, clicks in (
            (True, True, True, [call(1409, 887, 1920, 1080, after_sleep=1.0)]),
            (False, True, True, []),
            (False, False, False, []),
        ):
            with self.subTest(notice=notice, square=square):
                task = square_task()
                clock = FakeClock()
                task.sleep = clock.sleep
                task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), np.uint8))
                task._match = Mock(return_value=MatchResult(0.99, (1376, 862), (66, 51)))
                task._passes = lambda _, spec: notice if spec is SQUARE_NOTICE_TEMPLATE else square
                with patch("src.tasks.SquareGoddessTask.monotonic", clock):
                    self.assertIs(task._click_square_notice_if_present(timeout=2), expected)
                self.assertEqual(clicks, task._click_client.call_args_list)
                self.assertEqual(1 if notice else 2 if square else 6, task.capture_frame.call_count)

    def test_square_return_home_requires_scene_evidence_and_bounds_clicks(self):
        for home_after, in_square, expected, clicks in (
            (2, True, True, 2),
            (4, True, False, 3),
            (2, False, False, 0),
        ):
            with self.subTest(home_after=home_after, in_square=in_square):
                clock = FakeClock()
                task = object.__new__(SquareGoddessTask)
                task.config = {}
                task.info_set = Mock()
                task.capture_frame = Mock()
                task.operate_click = Mock()
                task._read_home_confirmation_signals = lambda *_: (
                    task.operate_click.call_count >= home_after,
                    0,
                    0,
                    "",
                )
                task.dismiss_home_announcement = Mock(return_value=False)
                task._match = Mock()
                task._passes = lambda _, spec: in_square and spec is QUICK_SWITCH_TEMPLATE
                task.sleep = clock.sleep
                with patch("src.tasks.BaseBD2Task.monotonic", clock):
                    self.assertIs(task._return_home_from_square(), expected)
                self.assertEqual(
                    [call(*SQUARE_HOME_POINT, after_sleep=1.0)] * clicks,
                    task.operate_click.call_args_list,
                )
