"""Map-trade navigator tests (split from test_map_trade.py)."""

import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks.map_trade.event_card_art import find_card
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    EVENT_CARDS,
    EVENT_WALK_EDGES,
    WALK_LABEL_EDGES,
    CollectionMapRole,
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    Navigator,
)
from src.tasks.map_trade.navigator_constants import (
    AREA_MAP_BACK_TEMPLATE,
    AREA_MAP_CHANGE_INTERVAL,
    AREA_MAP_OPEN_RELATIVE_POINT,
    AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
    HAND_TEMPLATE,
    SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT,
    SANDBOX_MAP_SETTLE_SECONDS,
    SANDBOX_MAP_TELEPORT_TEMPLATE,
    SANDBOX_TELEPORT_SKILL_POLL_INTERVAL,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
    STORY_CATEGORY_POINT,
    TELEPORT_GENERATION_OCR_TIMEOUT,
    TELEPORT_INTERACTION_CLICK_DELAY,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_HEADER_OCR_RELATIVE_ROI,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_SKILL_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    AreaMapContext,
    LocatedStoryCard,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.navigator_sandbox import TELEPORT_PAGE_ARROW_REFERENCE
from src.tasks.map_trade.vision import Vision
from src.utils.image_utils import read_template_image
from tests.helpers import FakeClock
from tests.helpers.recognition_fixtures import load_bgr

ROOT = Path(__file__).resolve().parents[1]


class NavigatorTest(unittest.TestCase):
    def test_portal_entry_reuses_maps_and_restores_field_before_skill_actions(self):
        frame = np.full((90, 160, 3), 120, dtype=np.uint8)
        for initial in MapPageMode:
            for can_close in (True, False):
                with self.subTest(mode=initial, can_close=can_close):
                    mode = [initial]
                    events = []
                    navigator = Navigator(SimpleNamespace(info_set=Mock()), SimpleNamespace(
                        capture=lambda: frame,
                    ))
                    navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(mode=mode[0])
                    navigator.classify = Mock(return_value=ScreenState.UNKNOWN)
                    navigator._wait_for_current_sandbox = Mock(return_value=NavigationResult(
                        True, ScreenState.SANDBOX,
                    ))
                    def close(*_args, **_kwargs):
                        events.append("close")
                        if can_close:
                            mode[0] = MapPageMode.UNKNOWN
                            navigator.classify.return_value = ScreenState.SANDBOX
                        return NavigationResult(can_close, ScreenState.UNKNOWN)
                    navigator._close_confirmed_map_page = Mock(side_effect=close)
                    navigator._click_sandbox_teleport_interaction = Mock(return_value=False)
                    navigator._click_sandbox_teleport_skill = Mock(
                        side_effect=lambda: events.append("skill") or True,
                    )
                    navigator._wait_for_sandbox_map_open = Mock(return_value=NavigationResult(
                        True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.GENERATE_TELEPORT,
                    ))
                    result = navigator.open_teleport_map_from_sandbox()
                    if initial.is_teleport_map:
                        self.assertTrue(result.success)
                        self.assertEqual(initial, result.map_page_mode)
                        self.assertEqual([], events)
                    elif initial == MapPageMode.SANDBOX_LARGE_MAP and can_close:
                        self.assertTrue(result.success)
                        self.assertEqual(["close", "skill"], events)
                    else:
                        self.assertFalse(result.success)
                        navigator._click_sandbox_teleport_skill.assert_not_called()
                        navigator._click_sandbox_teleport_interaction.assert_not_called()

    def test_portal_interaction_retries_only_after_field_recovery_and_stops_at_limit(self):
        failed = NavigationResult(False, ScreenState.UNKNOWN, "直传页未确认")
        passed = NavigationResult(
            True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        for waits, recover, success, reclicks in (
            ([passed], True, True, 0),
            ([failed, passed], True, True, 1),
            ([failed] * 3, True, False, 2),
            ([failed], False, False, 0),
        ):
            with self.subTest(waits=waits, recover=recover):
                events = []
                navigator = Navigator(SimpleNamespace(info_set=Mock()), SimpleNamespace())
                navigator._wait_for_sandbox_map_open = Mock(side_effect=waits)
                navigator._restore_navigation_field = Mock(side_effect=lambda: (
                    events.append("restore") or NavigationResult(recover, ScreenState.UNKNOWN)
                ))
                navigator._click_sandbox_teleport_interaction = Mock(side_effect=lambda: (
                    events.append("click") or True
                ))
                result = navigator._confirm_portal_interaction("交互")
                self.assertEqual(success, result.success)
                self.assertEqual(reclicks, navigator._click_sandbox_teleport_interaction.call_count)
                for index, event in enumerate(events):
                    if event == "click":
                        self.assertEqual("restore", events[index - 1])
        navigator._wait_for_sandbox_map_open = Mock(side_effect=TaskDisabledException())
        navigator._restore_navigation_field.reset_mock()
        with self.assertRaises(TaskDisabledException):
            navigator._confirm_portal_interaction("交互")
        navigator._restore_navigation_field.assert_not_called()

    def test_navigation_menu_upscale_requires_unique_stable_destination(self):
        frame = np.full((720, 1280, 3), 120, dtype=np.uint8)
        for labels, expected in (
            (["狩猎扬", "快速狩猎", "艾琳"], "狩猎场"),
            (["快速狩猎"], None),
            (["狩猎场", "狩猎场"], None),
            (["艾琳"], "艾琳"),
        ):
            with self.subTest(labels=labels):
                clock = [0.0]
                scales = []
                def boxes(_frame, _label, _roi, *, ocr_scale=1.0):
                    scales.append(ocr_scale)
                    return [] if ocr_scale == 1.0 else [
                        SimpleNamespace(name=text, x=300, y=100 + row * 40, width=80, height=20)
                        for row, text in enumerate(labels)
                    ]
                navigator = Navigator(SimpleNamespace(
                    info_set=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                ), SimpleNamespace(capture=lambda: frame, ocr_boxes=boxes, simplify=str))
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic",
                           side_effect=lambda: clock[0]):
                    result = navigator._nav_menu_choice(("狩猎场", "艾琳"))
                self.assertEqual(expected, None if result is None else result[0])
                self.assertIn(2.0, scales)
                if result is not None:
                    self.assertEqual((720, 1280, 3), result[2])
                    self.assertGreaterEqual(scales.count(2.0), 2)

    def test_navigation_menu_open_retries_are_bounded_and_reset_each_trip(self):
        frame = np.full((90, 160, 3), 120, dtype=np.uint8)
        destination = ("艾琳", (100, 60), frame.shape)
        for choices, button_closed, menu_text, expected, presses in (
            ([None, destination], True, [], "艾琳", 2),
            ([None] * 3, True, [], None, 3),
            ([None], False, [], None, 1),
            ([None], True, ["旅馆"], None, 1),
        ):
            with self.subTest(choices=choices, closed=button_closed):
                vision = SimpleNamespace(
                    capture=lambda: frame, click_template=Mock(return_value=True),
                    match=Mock(), passes=Mock(return_value=button_closed), click_client=Mock(),
                )
                navigator = Navigator(SimpleNamespace(config={}, log_warning=Mock()), vision)
                navigator._restore_navigation_field = Mock(return_value=NavigationResult(
                    True, ScreenState.SANDBOX,
                ))
                navigator._nav_menu_choice = Mock(side_effect=choices)
                navigator._nav_menu_entries = Mock(return_value=({}, menu_text))
                navigator._confirm_travel = Mock(return_value="moving")
                navigator._wait_for_field_hud = Mock(return_value=NavigationResult(
                    True, ScreenState.SANDBOX,
                ))
                navigator._wait_auto_move_finished = Mock(return_value="done")
                navigator._navigation_failure = "旧失败"
                self.assertEqual(expected, navigator._travel_via_nav_menu("艾琳"))
                self.assertEqual(presses, vision.click_template.call_count)
                self.assertNotIn("旧失败", navigator._navigation_failure)
                self.assertEqual(int(expected is not None), vision.click_client.call_count)

    def test_navigation_destination_reclicks_fresh_menu_and_stops_on_unconfirmed_trip(self):
        for outcomes, success, click_count in (
            (["menu", "moving"], True, 2),
            (["menu"] * 3, False, 3),
            (["stuck"], False, 1),
        ):
            with self.subTest(outcomes=outcomes):
                vision = SimpleNamespace(
                    click_template=Mock(return_value=True), click_client=Mock(),
                )
                navigator = Navigator(SimpleNamespace(config={}), vision)
                navigator._restore_navigation_field = Mock(return_value=NavigationResult(
                    True, ScreenState.SANDBOX,
                ))
                navigator._nav_menu_choice = Mock(side_effect=[
                    ("艾琳", (100 + i, 60), (900, 1600, 3)) for i in range(click_count)
                ])
                navigator._confirm_travel = Mock(side_effect=outcomes)
                navigator._wait_for_field_hud = Mock(return_value=NavigationResult(
                    True, ScreenState.SANDBOX,
                ))
                navigator._wait_auto_move_finished = Mock(return_value="done")
                self.assertEqual("艾琳" if success else None,
                                 navigator._travel_via_nav_menu("艾琳"))
                self.assertEqual(click_count, vision.click_client.call_count)
                self.assertEqual([(100 + i, 60) for i in range(click_count)], [
                    call.args[0] for call in vision.click_client.call_args_list
                ])
                self.assertEqual(int(success), navigator._wait_for_field_hud.call_count)

    def test_navigation_confirmation_requires_observed_transition_and_finite_clicks(self):
        frame = np.full((90, 160, 3), 120, dtype=np.uint8)
        for mode, expected, expected_clicks in (
            ("accepted_second", "moving", 2),
            ("stuck", "stuck", 3),
            ("menu", "menu", 0),
            ("other_menu_text", "stuck", 0),
            ("unknown", "stuck", 0),
        ):
            with self.subTest(mode=mode):
                clock = [0.0]
                clicks = []
                def title(*_args, **_kwargs):
                    return "是否自动移动前往所选目的地" if mode == "stuck" or (
                        mode == "accepted_second" and len(clicks) < 2
                    ) else ""
                def boxes(_frame, name, **_kwargs):
                    return [SimpleNamespace(name="确认", x=80, y=50, width=20, height=10)]
                def click(*args, after_sleep=0):
                    clicks.append(args[0])
                    clock[0] += after_sleep
                navigator = Navigator(SimpleNamespace(
                    log_warning=Mock(), info_set=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                ), SimpleNamespace(
                    capture=lambda: frame, simplify=str, ocr_boxes=boxes,
                    ocr_text=lambda _frame, name, **kwargs: "" if name == "导航提示" else title(),
                    click_client=click, match=Mock(), passes=Mock(return_value=True),
                    click_ocr=Mock(),
                ))
                navigator._nav_menu_entries = Mock(return_value=(
                    {"艾琳": (100, 60)} if mode == "menu" else {},
                    ["旅馆"] if mode == "other_menu_text" else [],
                ))
                navigator.classify = Mock(return_value=(
                    ScreenState.SANDBOX if mode in {"accepted_second", "other_menu_text"}
                    else ScreenState.UNKNOWN
                ))
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic",
                           side_effect=lambda: clock[0]):
                    self.assertEqual(expected, navigator._confirm_travel("艾琳"))
                self.assertEqual(expected_clicks, len(clicks))

    def test_walk_question_is_confirmed_and_remaining_map_is_closed_before_waiting(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clock = [0.0]
        task = SimpleNamespace(
            info_set=Mock(), config={},
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame, click_client=Mock()))
        navigator.wait_state = Mock(return_value=ScreenState.SANDBOX)
        navigator._open_field_map = Mock(return_value="安全 学校1楼")
        navigator._sandbox_navigation_page_has_keyword = Mock(return_value=True)
        navigator._sandbox_navigation_teleport = Mock(return_value=MatchResult(
            0.99, (400, 400), (40, 40), 0.99,
        ))
        navigator._walk_text = Mock(return_value="是否自动移动前往所选目的地？")
        navigator._click_sandbox_navigation_destination_confirmation = Mock(return_value=True)
        events = []
        navigator._close_confirmed_map_page = Mock(side_effect=lambda *a, **k: (
            events.append("close") or NavigationResult(True, ScreenState.SANDBOX)
        ))
        navigator._wait_auto_move_finished = Mock(
            side_effect=lambda *a: events.append("walk") or "done",
        )
        navigator._click_sandbox_teleport_interaction = Mock(return_value=True)
        navigator._wait_for_sandbox_map_open = Mock(return_value=NavigationResult(
            True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.DIRECT_TELEPORT,
        ))
        with patch("src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0]):
            self.assertTrue(navigator._walk_to_sandbox_teleport_interaction().success)
        navigator._click_sandbox_navigation_destination_confirmation.assert_called_once()
        self.assertEqual(["close", "walk"], events)

    def test_walk_fallback_reuses_confirmed_map_and_rejects_other_pages(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for state in (ScreenState.SANDBOX, ScreenState.SANDBOX_MAP, ScreenState.UNKNOWN,
                      ScreenState.HOME, ScreenState.AREA_MAP):
            with self.subTest(state=state):
                clock = [0.0]
                navigator = Navigator(SimpleNamespace(
                    info_set=Mock(), sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                ), SimpleNamespace(capture=lambda: frame, click_client=Mock()))
                navigator.wait_state = Mock(return_value=state)
                navigator._open_field_map = Mock(return_value="安全 卢戈镇")
                navigator._click_sandbox_navigation_map = Mock()
                navigator._sandbox_navigation_page_has_keyword = Mock(return_value=True)
                navigator._sandbox_navigation_teleport = Mock(return_value=None)
                navigator._close_confirmed_map_page = Mock()
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic",
                           side_effect=lambda: clock[0]):
                    result = navigator._walk_to_sandbox_teleport_interaction()
                self.assertFalse(result.success)
                self.assertEqual(int(state == ScreenState.SANDBOX),
                                 navigator._open_field_map.call_count)
                navigator._click_sandbox_navigation_map.assert_not_called()
                navigator.vision.click_client.assert_not_called()
                self.assertEqual(int(state in {ScreenState.SANDBOX, ScreenState.SANDBOX_MAP}),
                                 navigator._close_confirmed_map_page.call_count)

    def test_exit_question_requires_unique_paired_button_and_observed_close(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        question = SimpleNamespace(name="是否自动移动前往所选目的地？", x=600, y=400,
                                   width=400, height=40)
        confirm = SimpleNamespace(name="确认", x=900, y=600, width=80, height=40)
        cancel = SimpleNamespace(name="取消", x=700, y=600, width=80, height=40)
        for mode, expected, labels, close_count in (
            ("confirmed", True, ["确认"], 1),
            ("close_failed", False, ["确认"], 1),
            ("persistent", False, ["确认", "取消"], 0),
            ("duplicate", False, ["取消"], 0),
            ("unpaired", False, [], 0),
            ("unknown", False, [], 0),
            ("no_dialog", True, [], 0),
        ):
            with self.subTest(mode=mode):
                clock, dismissed, clicked = [0.0], [False], []
                task = SimpleNamespace(
                    info_set=Mock(), sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                )
                def click(point, _shape, after_sleep=0):
                    if point == (100, 200):
                        clicked.append("exit")
                    else:
                        label = "确认" if point == (940, 620) else "取消"
                        clicked.append(label)
                        if mode in {"confirmed", "close_failed"} or label == "取消":
                            dismissed[0] = True
                    clock[0] += after_sleep
                def read_boxes(*_args, **_kwargs):
                    if mode == "unpaired":
                        return [confirm, cancel]
                    return [question, confirm, cancel] + ([confirm] if mode == "duplicate" else [])
                navigator = Navigator(task, SimpleNamespace(
                    capture=lambda: frame, click_client=click, ocr_boxes=read_boxes,
                    simplify=lambda text: text,
                ))
                navigator._exit_label_points = Mock(return_value=[(100, 200), (300, 400)])
                navigator._walk_text = lambda _frame: (
                    None if mode == "unknown" else "" if dismissed[0] or mode == "no_dialog"
                    else "是否自动移动前往所选目的地"
                )
                navigator._field_map_header = lambda _frame=None: (
                    "" if mode in {"unknown", "no_dialog"} else "安全卢戈镇"
                )
                navigator.classify = Mock(return_value=(
                    ScreenState.SANDBOX if mode == "no_dialog" else ScreenState.UNKNOWN
                ))
                navigator._close_field_map = Mock(return_value=mode != "close_failed")
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic",
                           side_effect=lambda: clock[0]):
                    result = navigator._click_walk_exit(
                        ("Q_sp1", "main_area", "battle_area_1"),
                        CARD_BY_ID["Q_sp1"].targets[1], "安全卢戈镇",
                    )
                self.assertEqual(expected, result)
                self.assertEqual(["exit", *labels], clicked)
                self.assertEqual(close_count, navigator._close_field_map.call_count)

    def test_exit_question_control_exceptions_propagate_without_more_clicks(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for error in (TaskDisabledException(), FinishedException()):
            with self.subTest(error=type(error).__name__):
                vision = SimpleNamespace(
                    capture=lambda: frame, click_client=Mock(),
                    ocr_boxes=Mock(side_effect=error),
                )
                navigator = Navigator(SimpleNamespace(info_set=Mock()), vision)
                navigator._walk_text = Mock(return_value="是否自动移动前往所选目的地")
                with self.assertRaises(type(error)):
                    navigator._exit_walk_started("安全卢戈镇")
                vision.click_client.assert_not_called()

    def test_exit_without_question_accepts_walk_evidence_but_not_unknown_pages(self):
        black = np.zeros((1080, 1920, 3), dtype=np.uint8)
        lit = np.full((1080, 1920, 3), 128, dtype=np.uint8)
        for frame, text, state, expected in (
            (black, "", ScreenState.UNKNOWN, True),
            (lit, "自动移动中：卢戈森林", ScreenState.UNKNOWN, True),
            (lit, "被巡逻发现，已被驱逐", ScreenState.UNKNOWN, True),
            (lit, "", ScreenState.LOADING, True),
            (lit, "", ScreenState.UNKNOWN, False),
            (lit, "", ScreenState.SANDBOX_MAP, False),
        ):
            with self.subTest(text=text, state=state, black=not frame.any()):
                navigator = Navigator(
                    SimpleNamespace(info_set=Mock()),
                    SimpleNamespace(capture=lambda frame=frame: frame, click_client=Mock()),
                )
                navigator._walk_text = Mock(return_value=text)
                navigator._field_map_header = Mock(return_value="")
                navigator.classify = Mock(return_value=state)
                self.assertIs(expected, navigator._exit_walk_started("安全卢戈镇"))
                navigator.vision.click_client.assert_not_called()

    def test_page_turn_retries_only_on_confirmed_same_page_and_respects_ends(self):
        card = CARD_BY_ID["Q_ep2"]
        before = self._area_context("恶梦之城2", "battle_area_2", left=True, right=True)
        after = self._area_context("雷瓦汀据点", "main_area", left=True)
        for scenario in ("dropped", "unknown", "end", "force"):
            with self.subTest(scenario=scenario):
                vision = SimpleNamespace(click_client=Mock())
                navigator = Navigator(SimpleNamespace(info_set=Mock()), vision)
                context = after if scenario in {"end", "force"} else before
                navigator._wait_for_area_map_change = Mock(
                    side_effect=[None, after] if scenario == "dropped" else [after],
                )
                if scenario == "unknown":
                    navigator._wait_for_area_map_change = Mock(return_value=None)
                navigator._area_map_page_state = Mock(
                    return_value=("unknown" if scenario == "unknown" else "same", context),
                )
                result = navigator._move_area_map(
                    card, context, "right", force=scenario == "force",
                )
                if scenario in {"end", "unknown"}:
                    self.assertIsNone(result)
                else:
                    self.assertIs(after, result)
                self.assertEqual(
                    {"dropped": 2, "unknown": 1, "end": 0, "force": 1}[scenario],
                    vision.click_client.call_count,
                )

    def test_field_map_arrival_requires_matching_name_and_successful_close(self):
        card = CARD_BY_ID["Q_cp2"]
        target = card.targets[1]
        for title, closed, success in (
            (target.title, True, True), (target.title, False, False),
            (card.targets[2].title, True, False), ("未知地图", True, False),
        ):
            with self.subTest(title=title, closed=closed):
                navigator = Navigator(SimpleNamespace(), SimpleNamespace())
                navigator.ensure_small_minimap = Mock(return_value=True)
                navigator._open_field_map = Mock(return_value=title)
                navigator._close_field_map = Mock(return_value=closed)
                navigator.ensure_area_map = Mock()
                result = navigator._confirm_collection_arrival(card, target)
                self.assertEqual(success, result.success)
                navigator.ensure_area_map.assert_not_called()

    def test_field_map_close_requires_field_evidence_and_only_retries_visible_map(self):
        frame = np.full((8, 8, 3), 120, dtype=np.uint8)
        for modes, waits, expected, clicks in (
            ((MapPageMode.SANDBOX_LARGE_MAP,), (True,), True, 1),
            ((MapPageMode.UNKNOWN,), (), False, 0),
            ((MapPageMode.SANDBOX_LARGE_MAP,) * 2, (False, False), False, 2),
            ((MapPageMode.SANDBOX_LARGE_MAP,) * 2, (False, True), True, 2),
            ((MapPageMode.SANDBOX_LARGE_MAP, MapPageMode.UNKNOWN), (False, False), False, 1),
            ((MapPageMode.SANDBOX_LARGE_MAP, MapPageMode.UNKNOWN), (False, True), True, 1),
        ):
            with self.subTest(modes=modes, waits=waits):
                clock = [0.0]
                task = SimpleNamespace(
                    config={}, info_set=Mock(), operate_click=Mock(), log_warning=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                )
                vision = SimpleNamespace(
                    capture=lambda: frame,
                    match=Mock(), passes=Mock(return_value=False),
                )
                navigator = Navigator(task, vision)
                navigator._detect_map_page_mode = Mock(
                    side_effect=[SimpleNamespace(mode=mode) for mode in modes],
                )
                navigator.classify = Mock(return_value=ScreenState.UNKNOWN)
                navigator._wait_for_current_sandbox = Mock(
                    side_effect=[NavigationResult(value, ScreenState.UNKNOWN) for value in waits],
                )
                with patch(
                    "src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0],
                ):
                    self.assertEqual(expected, navigator._close_field_map())
                self.assertEqual(clicks, task.operate_click.call_count)

    def test_auto_move_wait_requires_readable_idle_field(self):
        frame = np.full((8, 8, 3), 120, dtype=np.uint8)
        for error, screen, expected in (
            (ValueError("OCR unavailable"), ScreenState.SANDBOX, "timeout"),
            (None, ScreenState.UNKNOWN, "timeout"),
            (None, ScreenState.SANDBOX, "done"),
        ):
            with self.subTest(error=error, screen=screen):
                clock = [0.0]
                task = SimpleNamespace(
                    config={}, info_set=Mock(),
                    capture_frame=lambda: frame,
                    ocr=Mock(return_value=[], side_effect=error),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                )
                vision = Vision(task)
                navigator = Navigator(task, vision)
                navigator.classify = lambda _frame: screen
                navigator._leave_walk_battle_result = Mock(return_value=False)
                with patch(
                    "src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0],
                ):
                    self.assertEqual(expected, navigator._wait_auto_move_finished(1))
                    self.assertEqual(expected == "done", navigator._wait_resuming_walk(1))

    def test_portal_walk_leaves_victory_and_requires_confirmed_idle(self):
        frame = np.full((108, 192, 3), 120, dtype=np.uint8)
        for leave_count, expected in ((1, "done"), (0, "timeout"), (2, "timeout")):
            with self.subTest(leave_count=leave_count):
                clock, clicks = [0.0], []
                vision = SimpleNamespace(
                    capture=lambda: frame, simplify=lambda text: text,
                    ocr_text=lambda *_a, **_k: "" if clicks else "胜利",
                    ocr_boxes=lambda *_a, **_k: [
                        SimpleNamespace(name="离开", x=130, y=80, width=20, height=10)
                    ] * leave_count,
                    click_client=lambda *a, **k: clicks.append(a),
                )
                navigator = Navigator(SimpleNamespace(
                    info_set=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                ), vision)
                navigator.classify = lambda *_a: (
                    ScreenState.SANDBOX if clicks else ScreenState.UNKNOWN
                )
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic",
                           side_effect=lambda: clock[0]):
                    self.assertEqual(expected, navigator._wait_auto_move_finished(5))
                self.assertEqual(int(leave_count == 1), len(clicks))

    def test_navigation_menu_fallback_requires_field_and_reports_missing_entry(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for state in (ScreenState.HOME, ScreenState.SANDBOX):
            with self.subTest(state=state):
                task = SimpleNamespace(operate_click=Mock(), log_warning=Mock())
                vision = SimpleNamespace(
                    click_template=Mock(return_value=False), capture=lambda: frame,
                    match=Mock(), passes=Mock(return_value=False),
                )
                navigator = Navigator(task, vision)
                navigator.classify = Mock(return_value=state)
                navigator._restore_navigation_field = Mock(return_value=NavigationResult(
                    state == ScreenState.SANDBOX, state,
                ))
                navigator._nav_menu_choice = Mock(return_value=None)
                navigator._nav_menu_entries = Mock(return_value=({}, []))
                self.assertIsNone(navigator._travel_via_nav_menu("艾琳"))
                self.assertEqual(int(state == ScreenState.SANDBOX), task.operate_click.call_count)

    def test_chapter_eighteen_routes_use_ocr_exit_and_verified_town(self):
        card = CARD_BY_ID["Q_sp18"]
        town, sanctuary, memory = card.targets
        self.assertEqual("科库托斯暗黑神殿", town.title)
        self.assertIsNone(WALK_LABEL_EDGES[(card.card_id, town.key, sanctuary.key)])
        arrived = NavigationResult(True, ScreenState.SANDBOX)
        for current, target in ((town, sanctuary), (sanctuary, town)):
            with self.subTest(origin=current.title, target=target.title):
                navigator = Navigator(SimpleNamespace(), SimpleNamespace())
                navigator._walk_to_collection_map = Mock(return_value=arrived)
                navigator._travel_via_nav_menu = Mock(return_value="艾琳")
                navigator.current_collection_target = Mock(return_value=town.key)
                result = navigator.advance_collection_map(card.card_id, current, target)
                self.assertTrue(result.success)
                if current is town:
                    navigator._walk_to_collection_map.assert_called_once_with(card, town, sanctuary)
                else:
                    navigator._travel_via_nav_menu.assert_called_once_with("艾琳")
                    navigator._walk_to_collection_map.assert_not_called()
        self.assertEqual("第一记忆空间", memory.title)

    def test_chapter_eighteen_reset_accepts_already_at_town_without_teleport(self):
        card = CARD_BY_ID["Q_sp18"]
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator.ensure_small_minimap = Mock(return_value=True)
        navigator._travel_via_nav_menu = Mock(return_value=None)
        navigator.current_collection_target = Mock(return_value=card.targets[0].key)
        navigator._open_teleport_map_anywhere = Mock()
        result = navigator.prepare_collection_main_area(card.card_id, via_hunting_ground=True)
        self.assertTrue(result.success)
        navigator._open_teleport_map_anywhere.assert_not_called()

    def test_chapter_eighteen_leaves_confirmed_town_to_find_a_circle(self):
        card = CARD_BY_ID["Q_sp18"]
        arrived = NavigationResult(True, ScreenState.SANDBOX)
        opened = NavigationResult(
            True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        for current in (card.targets[0].key, card.targets[1].key, None):
            with self.subTest(current=current):
                navigator = Navigator(SimpleNamespace(), SimpleNamespace())
                navigator.current_collection_target = Mock(return_value=current)
                navigator._walk_to_collection_map = Mock(return_value=arrived)
                navigator.open_teleport_map_from_sandbox = Mock(return_value=opened)
                result = navigator._walk_to_circle_neighbour(card)
                if current == card.targets[0].key:
                    self.assertIs(opened, result)
                    navigator._walk_to_collection_map.assert_called_once_with(
                        card, *card.targets[:2],
                    )
                else:
                    self.assertIsNone(result)
                    navigator._walk_to_collection_map.assert_not_called()

    def test_overlapping_circle_is_expanded_once_and_arrival_is_verified(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        circle = MatchResult(0.99, (800, 400), (50, 50), 0.98)
        initial = replace(self._area_context(target.title, target.key), overlap_arrow=circle)
        for title, key, expected in ((target.title, target.key, True), ("未知地图", None, False)):
            with self.subTest(title=title):
                expanded = self._area_context(title, key, teleports=(circle,))
                clock = FakeClock()
                task = SimpleNamespace(sleep=clock.sleep, info_set=Mock())
                vision = SimpleNamespace(click_client=Mock())
                navigator = Navigator(task, vision)
                navigator._capture_area_map_context = Mock(
                    side_effect=lambda _card: expanded if vision.click_client.called else initial,
                )
                navigator._click_teleport_map_destination = Mock(return_value=True)
                arrived = NavigationResult(True, ScreenState.SANDBOX)
                navigator._wait_for_story_sandbox = Mock(return_value=arrived)
                navigator._confirm_collection_arrival = Mock(return_value=arrived)
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic", clock):
                    result = navigator._click_collection_destination(card, target, initial)
                self.assertEqual(expected, result.success)
                vision.click_client.assert_called_once()
                if expected:
                    navigator._confirm_collection_arrival.assert_called_once_with(card, target)
                else:
                    navigator._click_teleport_map_destination.assert_not_called()

    def test_unreachable_npc_navigation_does_not_report_arrival(self):
        navigator = Navigator(SimpleNamespace(), SimpleNamespace(
            click_template=Mock(return_value=True), click_client=Mock(),
        ))
        navigator._nav_menu_choice = Mock(return_value=("艾琳", (100, 100), (1080, 1920, 3)))
        navigator._restore_navigation_field = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        navigator._confirm_travel = Mock(return_value="unreachable")
        navigator._wait_for_field_hud = Mock()
        self.assertIsNone(navigator._travel_via_nav_menu("艾琳"))
        navigator._wait_for_field_hud.assert_not_called()

    def test_small_chapter_eighteen_exit_uses_upscaled_ocr_centers(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        label = SimpleNamespace(name="科库托斯圣城", x=483, y=270, width=82, height=16)
        vision = SimpleNamespace(
            ocr_boxes=Mock(side_effect=[[], [label]]), simplify=lambda text: text,
        )
        navigator = Navigator(SimpleNamespace(info_set=Mock()), vision)
        points = navigator._exit_label_points(frame, CARD_BY_ID["Q_sp18"].targets[1])
        self.assertEqual([(524, 296), (524, 278), (524, 308)], points)
        self.assertEqual(
            [1.0, 2.0], [c.kwargs["ocr_scale"] for c in vision.ocr_boxes.call_args_list],
        )

    def test_event_cover_replay_handles_playing_card_scaling_and_absence(self):
        band = load_bgr(ROOT / "tests/fixtures/quick_switch/event_bar_video_1080.png")
        source = np.zeros((1080, 1920, 3), dtype=np.uint8)
        source[895:1050] = band
        for scale in (2 / 3, 1.0, 2.0):
            frame = cv2.resize(source, None, fx=scale, fy=scale)
            for index, card in enumerate(EVENT_CARDS):
                with self.subTest(scale=scale, card=card.card_id):
                    found = find_card(frame, card.number)
                    self.assertIsNotNone(found.result, found.reason)
                    self.assertAlmostEqual((117 + 180 * index) * scale,
                                           found.result.center[0], delta=5)
                    absent = frame.copy()
                    absent[:, round((90 + 180 * index) * scale):
                           round((250 + 180 * index) * scale)] = 0
                    self.assertIsNone(find_card(absent, card.number).result)

    def test_activity_routes_walk_and_return_via_verified_town(self):
        for card_id, source, destination in EVENT_WALK_EDGES:
            card = CARD_BY_ID[card_id]
            current = next(t for t in card.targets if t.key == source)
            target = next(t for t in card.targets if t.key == destination)
            navigator = object.__new__(Navigator)
            expected = NavigationResult(True, ScreenState.SANDBOX, target.title)
            navigator._walk_to_collection_map = Mock(return_value=expected)
            navigator._travel_via_nav_menu = Mock(return_value=None)
            navigator._open_teleport_map_anywhere = Mock()
            with self.subTest(card=card_id, destination=destination):
                self.assertIs(expected, navigator.advance_collection_map(card_id, current, target))
                navigator._walk_to_collection_map.assert_called_once_with(card, current, target)
                navigator._open_teleport_map_anywhere.assert_not_called()

    def test_activity_entry_waits_through_color_loading_longer_than_three_seconds(self):
        clock = [0.0]
        frame = np.full((1080, 1920, 3), 120, dtype=np.uint8)
        states = iter([ScreenState.UNKNOWN] * 8 + [ScreenState.SANDBOX] * 2)
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45}, info_set=Mock(), log_warning=Mock(),
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            _handle_recent_cartridge_special_pages=lambda **_kwargs: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=Mock(return_value=""),
            click_client=lambda *_args, after_sleep=0: task.sleep(after_sleep),
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: next(states)
        navigator._auto_moving = lambda _frame: False
        navigator._handle_story_card_intermediate = lambda _frame: False
        badge = StoryBadgeDetection(
            StoryBadgeCandidate(2, MatchResult(1, (280, 922), (29, 29))), None,
        )
        located = LocatedStoryCard(CARD_BY_ID["Q_ep2"], frame, badge)
        navigator._confirm_story_badge_before_click = Mock(return_value=(frame, badge))
        with patch("src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0]):
            result = navigator._enter_located_story_card(located)
        self.assertTrue(result.success)
        self.assertGreater(clock[0], 3)

    def test_activity_return_stops_when_home_button_is_unrecognized(self):
        navigator = object.__new__(Navigator)
        navigator.task = SimpleNamespace(sleep=Mock(), operate_click=Mock(), info_set=Mock())
        navigator.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: MatchResult(-1, (0, 0), (0, 0)),
            passes=lambda *_args: False, click_client=Mock(),
        )
        navigator.require_home_match = True
        navigator.classify = lambda: ScreenState.SANDBOX
        result = navigator.return_home()
        self.assertFalse(result.success)
        navigator.task.operate_click.assert_not_called()
        navigator.vision.click_client.assert_not_called()

    def test_activity_exit_requires_unique_picker_and_confirmed_victory(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        battle = SimpleNamespace(name="战斗区", x=200, y=300, width=100, height=40)
        leave = SimpleNamespace(name="离开", x=900, y=900, width=100, height=40)
        for text, boxes, expected in (
            ("VICTORY", [leave], True), ("普通战斗", [leave], False),
            ("VICTORY", [leave, leave], False),
        ):
            navigator = object.__new__(Navigator)
            navigator.vision = SimpleNamespace(
                ocr_text=lambda *_args: text, ocr_boxes=lambda *_args, **_kwargs: boxes,
                click_client=Mock(),
            )
            with self.subTest(text=text, count=len(boxes)):
                self.assertEqual(expected, navigator._leave_walk_battle_result(frame))
                self.assertEqual(int(expected), navigator.vision.click_client.call_count)
        for boxes, expected in (([battle], True), ([battle, battle], False), ([], False)):
            navigator.vision.capture = lambda: frame
            navigator.vision.ocr_boxes = lambda *_args, **_kwargs: boxes
            navigator.vision.click_client.reset_mock()
            self.assertEqual(
                expected, navigator._click_activity_exit_picker(CARD_BY_ID["Q_ep1"].targets[1]),
            )
            self.assertEqual(int(expected), navigator.vision.click_client.call_count)

    def test_collection_field_retries_battle_result_until_idle_field(self):
        frame = np.full((8, 8, 3), 120, dtype=np.uint8)
        leave = SimpleNamespace(name="离开", x=900, y=900, width=100, height=40)
        for accepted_click in (1, 2, None):
            with self.subTest(accepted_click=accepted_click):
                clock = [0.0]
                click_times = []
                field_at = [None]
                task = SimpleNamespace(
                    config={"加载页面等待秒数": 10}, info_set=Mock(), operate_click=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                )

                def phase():
                    if field_at[0] is None or clock[0] < field_at[0]:
                        return "victory"
                    return "field"

                def ocr_text(_frame, _name, **kwargs):
                    if kwargs.get("relative_roi") == (0.40, 0.05, 0.60, 0.16):
                        return "TURN 1" if phase() == "battle" else ""
                    return "VICTORY" if phase() == "victory" else ""

                def click(_point, _shape, *, after_sleep=0):
                    self.assertEqual("victory", phase())
                    click_times.append(clock[0])
                    if len(click_times) == accepted_click:
                        field_at[0] = clock[0] + after_sleep
                    task.sleep(after_sleep)

                vision = SimpleNamespace(
                    capture=lambda: frame, ocr_text=ocr_text,
                    ocr_boxes=Mock(return_value=[leave]), click_client=Mock(side_effect=click),
                )
                navigator = Navigator(task, vision)
                navigator.classify = lambda _frame=None: (
                    ScreenState.SANDBOX if phase() == "field" else ScreenState.UNKNOWN
                )
                navigator._walk_text = lambda _frame: (
                    "自动移动中" if clock[0] < field_at[0] + 1 else ""
                )
                with patch(
                    "src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0],
                ):
                    if accepted_click is None:
                        with self.assertRaisesRegex(RuntimeError, "遭遇战恢复超时"):
                            navigator.wait_for_collection_field(frame)
                    else:
                        self.assertIs(frame, navigator.wait_for_collection_field(frame))

                if accepted_click is None:
                    self.assertGreater(len(click_times), 1)
                    self.assertGreaterEqual(clock[0], 180)
                    self.assertLess(clock[0], 182)
                else:
                    self.assertEqual(accepted_click, len(click_times))
                    self.assertGreaterEqual(clock[0], field_at[0] + 1.5)
                self.assertTrue(all(
                    later - earlier >= 1.5
                    for earlier, later in zip(click_times, click_times[1:])
                ))
                task.operate_click.assert_not_called()
                for call in vision.click_client.call_args_list:
                    self.assertEqual(((950, 920), frame.shape), call.args)
                    self.assertEqual({"after_sleep": 1.0}, call.kwargs)

    def test_collection_field_does_not_retreat_without_a_turn_counter(self):
        frame = np.full((8, 8, 3), 120, dtype=np.uint8)
        for text, timeout in (("RETURN 1", 10), ("TURN", 10), ("", 10)):
            with self.subTest(text=text):
                clock = [0.0]
                task = SimpleNamespace(
                    config={"加载页面等待秒数": 10}, info_set=Mock(), operate_click=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                )
                vision = SimpleNamespace(
                    capture=lambda: frame, ocr_text=Mock(return_value=text), click_client=Mock(),
                )
                navigator = Navigator(task, vision)
                navigator.classify = lambda _frame=None: ScreenState.UNKNOWN
                navigator._leave_walk_battle_result = Mock(return_value=False)
                with patch(
                    "src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0],
                ), self.assertRaises(RuntimeError):
                    navigator.wait_for_collection_field()

                self.assertGreaterEqual(clock[0], timeout)
                self.assertLess(clock[0], timeout + 1)
                task.operate_click.assert_not_called()
                vision.click_client.assert_not_called()

    def test_collection_retreat_confirms_each_transition_and_bounds_retries(self):
        for width, height, entry in (
            (1920, 1080, False), (1280, 720, False),
            (1920, 1080, True), (1280, 720, True),
        ):
            for blocked in (
                None, "pause", "escape", "confirm", "ambiguous", "loading", "stop",
                "dialog_ambiguous", "missing_cancel", "missing_question", "title_only", "wrong_row",
                "field_moving", "field_unreadable",
            ):
                with self.subTest(size=(width, height), entry=entry, blocked=blocked):
                    clock = [0.0]
                    state = ["battle"]
                    clicks = []
                    attempts = {"pause": 0, "escape": 0, "confirm": 0}
                    field_observations = []
                    cancelled = []
                    frame = np.full((height, width, 3), 120, dtype=np.uint8)

                    def box(name, x, y):
                        return SimpleNamespace(
                            name=name, x=x * width, y=y * height,
                            width=width * 0.05, height=height * 0.03,
                        )

                    menu = box("逃跑", 0.26, 0.90)
                    dialog = box("逃跑", 0.58, 0.55)
                    pause = box("暂停", 0.47, 0.18)
                    restart = box("重新挑战", 0.46, 0.90)
                    resume = box("点击继续", 0.66, 0.90)
                    title = box("逃跑", 0.47, 0.39)
                    question = box("是否放弃战斗逃跑？", 0.39, 0.49)
                    cancel = box("取消", 0.42, 0.70 if blocked == "wrong_row" else 0.55)

                    def ocr_boxes(*_args, **_kwargs):
                        self.assertNotEqual("field", state[0])
                        if state[0] == "menu":
                            return [pause, restart, resume, menu] + (
                                [menu] if blocked == "ambiguous" else []
                            )
                        if state[0] == "dialog":
                            return (
                                [pause, restart, resume, menu, title]
                                + ([] if blocked == "missing_question" else [question])
                                + ([] if blocked == "missing_cancel" else [cancel])
                                + ([] if blocked == "title_only" else [dialog])
                                + ([dialog] if blocked == "dialog_ambiguous" else [])
                            )
                        return []

                    def click(point, _shape, *, after_sleep=0):
                        stage = {"battle": "pause", "menu": "escape", "dialog": "confirm"}[state[0]]
                        expected = {
                            "pause": (round(width * 1792 / 1920), round(height * 52 / 1080)),
                            "escape": Navigator._ocr_box_center(menu),
                            "confirm": Navigator._ocr_box_center(dialog),
                        }[stage]
                        self.assertEqual(expected, point)
                        clicks.append(stage)
                        attempts[stage] += 1
                        # Exercise a dropped first click at every stage.
                        if stage != blocked and attempts[stage] >= 2:
                            state[0] = {
                                "pause": "menu", "escape": "dialog", "confirm": "loading",
                            }[stage]
                        clock[0] += after_sleep

                    def capture():
                        if state[0] == "loading" and blocked != "loading":
                            state[0] = "field"
                        return frame

                    def sleep(seconds):
                        if blocked == "stop":
                            raise TaskDisabledException()
                        clock[0] += seconds

                    task = SimpleNamespace(
                        config={"加载页面等待秒数": 10}, info_set=Mock(),
                        sleep=sleep,
                        _handle_recent_cartridge_special_pages=Mock(return_value=None),
                    )
                    vision = SimpleNamespace(
                        capture=capture, ocr_boxes=ocr_boxes, click_client=click,
                        ocr_text=lambda *_a, **_k: "TURN.1" if state[0] == "battle" else "",
                    )
                    navigator = Navigator(task, vision)
                    navigator._black_frame = lambda _frame: state[0] == "loading"
                    def classify(_frame):
                        if state[0] == "field":
                            field_observations.append(clock[0])
                            return ScreenState.SANDBOX
                        return ScreenState.UNKNOWN
                    navigator.classify = classify
                    navigator._walk_text = lambda _frame: (
                        None if blocked == "field_unreadable" else
                        "自动移动中" if blocked == "field_moving" and not cancelled else ""
                    )
                    navigator._cancel_walk = lambda: cancelled.append(True)
                    navigator._leave_walk_battle_result = Mock(return_value=False)
                    navigator._handle_story_card_intermediate = Mock(return_value=False)
                    navigator._auto_moving = Mock(return_value=False)
                    with patch(
                        "src.tasks.map_trade.navigator_sandbox.monotonic",
                        side_effect=lambda: clock[0],
                    ):
                        def recover():
                            return (
                                navigator._wait_for_story_sandbox(14)
                                if entry else navigator.wait_for_collection_field(frame)
                            )

                        if blocked in (None, "field_moving"):
                            result = recover()
                            if entry:
                                self.assertTrue(result.success)
                                self.assertEqual(ScreenState.SANDBOX, result.state)
                            else:
                                self.assertIs(frame, result)
                            self.assertEqual(
                                ["pause"] * 2 + ["escape"] * 2 + ["confirm"] * 2, clicks,
                            )
                            self.assertEqual("field", state[0])
                            self.assertEqual(3 if cancelled else 2, len(field_observations))
                            self.assertLessEqual(clock[0] - field_observations[0], 0.31)
                            self.assertEqual(int(blocked == "field_moving"), len(cancelled))
                        elif blocked == "stop":
                            with self.assertRaises(TaskDisabledException):
                                recover()
                            self.assertEqual(["pause"], clicks)
                        else:
                            if entry:
                                result = recover()
                                self.assertFalse(result.success)
                                self.assertIn("遭遇战退出", result.message)
                            else:
                                with self.assertRaisesRegex(RuntimeError, "遭遇战退出"):
                                    recover()
                            self.assertLessEqual(clock[0], 31)
                            self.assertLessEqual(max(attempts.values()), 3)
                            if blocked in attempts:
                                self.assertEqual(3, attempts[blocked])
                            if blocked == "ambiguous":
                                self.assertEqual(0, attempts["escape"])
                            if blocked in {
                                "dialog_ambiguous", "missing_cancel", "missing_question",
                                "title_only", "wrong_row",
                            }:
                                self.assertEqual(0, attempts["confirm"])

    def test_story_entry_limits_battle_recovery_to_confirmed_encounters(self):
        for text, black, entry, field in (
            ("TURN.1", False, True, False), ("RETURN.1", False, True, False),
            ("TURN", False, True, False), ("", False, True, True),
            ("TURN.1", True, True, False), ("TURN.1", False, False, False),
        ):
            with self.subTest(text=text, black=black, entry=entry, field=field):
                frame = np.full((8, 8, 3), 0 if black else 120, dtype=np.uint8)
                clock = [0.0]
                task = SimpleNamespace(
                    config={"加载页面等待秒数": 1}, info_set=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    _handle_recent_cartridge_special_pages=Mock(return_value=None),
                )
                vision = SimpleNamespace(
                    capture=lambda: frame, ocr_text=Mock(return_value=text), click_client=Mock(),
                )
                navigator = Navigator(task, vision)
                navigator.classify = Mock(return_value=(
                    ScreenState.SANDBOX if field else ScreenState.UNKNOWN
                ))
                navigator._auto_moving = Mock(return_value=False)
                navigator._handle_story_card_intermediate = Mock(return_value=False)
                navigator._retreat_from_field_battle = Mock(return_value=frame)
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic", lambda: clock[0]):
                    result = (
                        navigator._wait_for_story_sandbox(14)
                        if entry else navigator._wait_for_current_sandbox(timeout=1)
                    )
                should_retreat = entry and not black and text == "TURN.1"
                self.assertEqual(field or should_retreat, result.success)
                self.assertEqual(
                    int(should_retreat), navigator._retreat_from_field_battle.call_count,
                )
                if should_retreat:
                    navigator.classify.assert_not_called()
                    task._handle_recent_cartridge_special_pages.assert_not_called()
                    self.assertTrue(navigator._retreat_from_field_battle.call_args.kwargs["stop_walk"])
                if black or not entry:
                    vision.ocr_text.assert_not_called()
                vision.click_client.assert_not_called()
        for signal in (TaskDisabledException, FinishedException):
            with self.subTest(signal=signal):
                navigator.vision.capture = lambda: np.full((8, 8, 3), 120, dtype=np.uint8)
                navigator._retreat_from_field_battle = Mock(side_effect=signal)
                with self.assertRaises(signal):
                    navigator._wait_for_story_sandbox(14)

    def test_retreat_dialog_ocr_separates_title_from_confirm_button(self):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        from src.tasks.BaseBD2Task import BaseBD2Task

        engine = ONNXPaddleOcr(use_angle_cls=False, use_npu=False, use_openvino=True)
        task = object.__new__(BaseBD2Task)
        task._executor = SimpleNamespace(
            paused=False, debug=False, config={"ocr": {"default": {"lib": "onnxocr"}}},
            ocr_lib=lambda _: engine, ocr_po_translation=None, text_fix={},
        )
        task.config = {}
        task.info_set = Mock()
        task.log_info = task.log_warning = Mock()
        task.log_debug = None
        task.ocr_default_threshold = 0.2
        task.ocr_target_height = 0
        vision = Vision(task)
        navigator = Navigator(task, vision)
        source = load_bgr(
            Path(__file__).parent / "fixtures/map_trade/video_fhd/field_retreat_confirm.png",
        )
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            with self.subTest(size=(width, height)):
                boxes = vision.ocr_boxes(cv2.resize(source, (width, height)), "采集战斗逃跑")
                self.assertEqual(3, sum(box.name == "逃跑" for box in boxes))
                stage, point = navigator._field_retreat_action(boxes)
                self.assertEqual("confirm", stage)
                self.assertAlmostEqual(1060 / 1920, point[0] / width, delta=0.006)
                self.assertAlmostEqual(635 / 1080, point[1] / height, delta=0.006)

    def test_activity_walk_accepts_alias_and_rechecks_arrival_map(self):
        card = CARD_BY_ID["Q_ep7"]
        navigator = object.__new__(Navigator)
        navigator.task = SimpleNamespace(info_set=Mock())
        navigator.ensure_small_minimap = lambda: True
        headers = iter(("安全 室内泳池", "战斗Ⅰ 音乐节活动现场"))
        navigator._open_field_map = lambda: next(headers)
        navigator._close_field_map = Mock(return_value=True)
        navigator._click_walk_exit = Mock(return_value=True)
        navigator._wait_resuming_walk = Mock(return_value=True)
        navigator._loading_timeout = lambda: 45
        navigator._wait_for_field_hud = Mock(
            return_value=NavigationResult(True, ScreenState.SANDBOX),
        )
        result = navigator._walk_to_collection_map(card, card.targets[0], card.targets[1])
        self.assertTrue(result.success)
        navigator._close_field_map.assert_called_once()

    def test_collection_walk_waits_for_idle_before_arrival_check_and_times_out(self):
        frame = np.full((8, 8, 3), 120, dtype=np.uint8)
        black = np.zeros_like(frame)
        cases = (
            ("moving", ("moving",) * 6 + ("idle",)),
            ("expelled", ("moving", "caught", "loading", "moving", "idle")),
            ("battle", ("moving", "battle", "moving", "idle")),
            ("encounter", ("moving", "encounter", "moving", "idle")),
            ("timeout", ("moving",)),
        )
        for card_id, source, destination in (*WALK_LABEL_EDGES, *EVENT_WALK_EDGES):
            card = CARD_BY_ID[card_id]
            current = next(item for item in card.targets if item.key == source)
            target = next(item for item in card.targets if item.key == destination)
            for name, phases in cases:
                with self.subTest(card=card_id, destination=destination, scenario=name):
                    clock = [0.0]

                    def phase():
                        return phases[min(int(clock[0] * 2), len(phases) - 1)]

                    task = SimpleNamespace(
                        config={}, info_set=Mock(),
                        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    )
                    vision = SimpleNamespace(
                        capture=lambda: black if phase() == "loading" else frame,
                        simplify=lambda text: text,
                        ocr_text=lambda *_args, **_kwargs: {
                            "moving": "自动移动中", "caught": "被发现驱逐",
                            "encounter": "TURN.1",
                        }.get(phase(), ""),
                    )
                    navigator = Navigator(task, vision)
                    navigator.ensure_small_minimap = lambda: True
                    navigator.classify = lambda _frame=None: (
                        ScreenState.UNKNOWN
                        if phase() in {"battle", "encounter"} else ScreenState.SANDBOX
                    )
                    navigator._click_walk_exit = Mock(return_value=True)
                    navigator._close_field_map = Mock(return_value=True)
                    navigator._leave_walk_battle_result = Mock(return_value=True)
                    navigator._retreat_from_field_battle = Mock(return_value=frame)
                    opened = []

                    def open_map():
                        opened.append(clock[0])
                        if len(opened) == 1:
                            return current.title
                        self.assertEqual("idle", phase(), "徒步停止前不能开图复核")
                        return target.title

                    navigator._open_field_map = open_map
                    with (
                        patch("src.tasks.map_trade.navigator_sandbox.monotonic",
                              side_effect=lambda: clock[0]),
                        patch("src.tasks.map_trade.navigator_sandbox.RESUMING_WALK_TIMEOUT", 4),
                        patch("src.tasks.map_trade.navigator_sandbox.SANDBOX_NAVIGATION_WALK_TIMEOUT",
                              4),
                    ):
                        result = navigator._walk_to_collection_map(card, current, target)
                    self.assertEqual(name != "timeout", result.success)
                    self.assertEqual(1 if name == "timeout" else 2, len(opened))
                    if name == "timeout":
                        self.assertIn("徒步等待超时", result.message)
                        self.assertLessEqual(clock[0], 4.5)
                        navigator._close_field_map.assert_not_called()
                    if name == "battle":
                        navigator._leave_walk_battle_result.assert_called_once()
                    if name == "encounter":
                        navigator._retreat_from_field_battle.assert_called_once()


    def test_quick_switch_identity_survives_brightness_and_client_scaling(self):
        matcher = Vision(SimpleNamespace(config={}, info_set=lambda *_args: None))
        navigator = Navigator(matcher.task, matcher)
        for name, expected in (
            ("snow_town_button_fhd.png", (907, 999)),
            ("in_square_dark_button_1600x901.png", (662, 833)),
        ):
            source = load_bgr(ROOT / "tests/fixtures/quick_switch" / name)
            for scale in (2 / 3, 1.0, 4 / 3):
                for brightness in (-15, 0, 15):
                    with self.subTest(scene=name, scale=scale, brightness=brightness):
                        frame = cv2.resize(source, None, fx=scale, fy=scale)
                        frame = np.clip(
                            frame.astype(np.int16) + brightness, 0, 255,
                        ).astype(np.uint8)
                        found = navigator._find_quick_switch_button(frame)
                        self.assertIsNotNone(found)
                        self.assertAlmostEqual(expected[0] * scale, found.center[0], delta=4)
                        self.assertAlmostEqual(expected[1] * scale, found.center[1], delta=4)

    def test_quick_switch_rejects_absence_and_duplicate_icons(self):
        matcher = Vision(SimpleNamespace(config={}, info_set=lambda *_args: None))
        navigator = Navigator(matcher.task, matcher)
        for name in (
            "home_bottom_strip_1600x901.png", "battle_gameplay_selected_fhd.png",
            "life_gameplay_selected_fhd.png",
        ):
            with self.subTest(scene=name):
                frame = load_bgr(ROOT / "tests/fixtures/quick_switch" / name)
                self.assertIsNone(navigator._find_quick_switch_button(frame))
        frame = load_bgr(ROOT / "tests/fixtures/quick_switch/snow_town_button_fhd.png")
        frame[970:1030, 580:650] = frame[970:1030, 880:950]
        self.assertIsNone(navigator._find_quick_switch_button(frame))

    def test_quick_switch_click_requires_stability_and_selector_confirmation(self):
        frame = load_bgr(ROOT / "tests/fixtures/quick_switch/snow_town_button_fhd.png")
        for page_ready in (False, True):
            with self.subTest(page_ready=page_ready):
                clicks = []
                task = SimpleNamespace(
                    config={}, capture_frame=lambda: frame, sleep=lambda *_args: None,
                    info_set=lambda *_args: None,
                )
                vision = Vision(task)
                vision.click_client = lambda center, shape, **_kwargs: clicks.append(center)
                navigator = Navigator(task, vision)
                navigator._wait_for_quick_switch_page = lambda: page_ready
                navigator._select_cartridge_category = lambda *_args: page_ready
                result = navigator.open_cartridge_quick_switcher_from_sandbox(
                    sandbox_already_confirmed=True,
                )
                self.assertEqual(page_ready, result.success)
                self.assertEqual([(907, 999)], clicks)

        samples = iter([MatchResult(0.98, (885, 981), (44, 36))] + [None] * 20)
        navigator._find_quick_switch_button = lambda _frame: next(samples)
        clicks.clear()
        self.assertFalse(navigator._click_quick_switch(timeout=1, after_sleep=0))
        self.assertEqual([], clicks)

    @staticmethod
    def _area_context(
        text: str,
        target_key: str | None = None,
        *,
        left: bool = False,
        right: bool = False,
        candidate_keys: tuple[str, ...] | None = None,
        teleports: tuple[MatchResult, ...] = (),
        page_mode: MapPageMode = MapPageMode.DIRECT_TELEPORT,
    ) -> AreaMapContext:
        match = MatchResult(0.99, (100, 100), (30, 30), pixel_score=0.98)
        keys = (
            candidate_keys if candidate_keys is not None else ((target_key,) if target_key else ())
        )
        return AreaMapContext(
            frame_shape=(1080, 1920, 3),
            raw_text=text,
            normalized_text=text,
            map_page_mode=page_mode,
            candidate_target_keys=keys,
            resolved_target_key=target_key if len(keys) == 1 else None,
            left_arrow=match if left else None,
            right_arrow=match if right else None,
            teleports=teleports,
            overlap_arrow=None,
            back_button=match,
            confirmation_text=(
                "移动魔法阵 传说"
                if page_mode == MapPageMode.GENERATE_TELEPORT
                else "移动魔法阵"
            ),
        )

    def test_ensure_card_menu_home_fallback_uses_shared_recent_cartridge_guard(self):
        calls = []
        task = SimpleNamespace(
            open_cartridge_quick_switcher=lambda **kwargs: (
                calls.append(kwargs) or True
            )
        )
        vision = SimpleNamespace(
            click_stable_template=lambda *_args, **_kwargs: self.fail(
                "the shared entry owns quick-switch clicking"
            )
        )
        navigator = Navigator(task, vision)
        states = iter((ScreenState.UNKNOWN,))
        navigator.classify = lambda: next(states)
        navigator.return_home = lambda: NavigationResult(True, ScreenState.HOME)
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True

        result = navigator.ensure_card_menu()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.CARD_MENU, result.state)
        self.assertEqual(1, len(calls))
        self.assertIs(calls[0]["ensure_home"], navigator._wait_for_cartridge_home)
        self.assertIs(
            calls[0]["confirm_quick_switch_page"],
            navigator._wait_for_quick_switch_page,
        )

    def test_area_map_title_resolution_prefers_longest_nested_story_title(self):
        card = CARD_BY_ID["Q_sp1"]

        self.assertEqual(
            (CollectionMapRole.BATTLE_AREA_2.value,),
            Navigator._target_keys_in_text(
                card,
                "卢戈森林深处",
            ),
        )

    def test_real_map_page_fixtures_are_mutually_exclusive_across_resolutions(self):
        fixtures = ROOT / "tests/fixtures/map_trade/map_pages"
        cases = {
            "direct_snow": (
                "移动魔法阵",
                "战斗Ⅰ 华特波尔雪山",
                MapPageMode.DIRECT_TELEPORT,
            ),
            "direct": (
                "移动魔法阵",
                "",
                MapPageMode.DIRECT_TELEPORT,
            ),
            "generate": (
                "移动魔法阵 传说",
                "",
                MapPageMode.GENERATE_TELEPORT,
            ),
            "sandbox_large": (
                "战斗Ⅱ 卢戈森林深处",
                "在战场中查看 探索 0 世界地图",
                MapPageMode.SANDBOX_LARGE_MAP,
            ),
        }
        cases["direct_900"] = ("移动魔法阵", "", MapPageMode.DIRECT_TELEPORT)
        for height in (720, 900, 1080, 1440, 2160):
            width = height * 16 // 9
            for name, (header, footer, expected) in cases.items():
                with self.subTest(height=height, page=name):
                    suffix = "webp" if name == "direct_snow" else "png"
                    source = load_bgr(fixtures / f"{name}.{suffix}")
                    self.assertIsNotNone(source)
                    interpolation = cv2.INTER_AREA if height < 1080 else cv2.INTER_CUBIC
                    frame = cv2.resize(source, (width, height), interpolation=interpolation)
                    matcher = Vision(SimpleNamespace(config={}))
                    vision = SimpleNamespace(
                        match=matcher.match,
                        passes=matcher.passes,
                        simplify=lambda value: value,
                        ocr_text=lambda _frame, label, **_kwargs: (
                            header if label == "地图页面左上标题" else footer
                        ),
                    )
                    navigator = Navigator(
                        SimpleNamespace(info_set=lambda *_args: None),
                        vision,
                    )

                    detection = navigator._detect_map_page_mode(frame)

                    self.assertEqual(expected, detection.mode)
                    if name == "direct_snow":
                        for spec, expected_x in (
                            (TELEPORT_MAP_FORWARD_TEMPLATE, 380),
                            (TELEPORT_MAP_BACKWARD_TEMPLATE, 1540),
                        ):
                            match = matcher.match(frame, spec)
                            self.assertTrue(matcher.passes(match, spec))
                            self.assertAlmostEqual(
                                expected_x, match.center[0] * 1920 / width, delta=4,
                            )
                            self.assertAlmostEqual(520, match.center[1] * 1080 / height, delta=4)

    def test_teleport_arrows_reject_blank_and_wrong_side(self):
        source = load_bgr(ROOT / "tests/fixtures/map_trade/map_pages/direct_snow.webp")
        matcher = Vision(SimpleNamespace(config={}))
        swapped = source.copy()
        swapped[:, 310:450] = source[:, 1470:1610]
        swapped[:, 1470:1610] = source[:, 310:450]
        for frame in (np.zeros_like(source), swapped):
            for spec in (TELEPORT_MAP_FORWARD_TEMPLATE, TELEPORT_MAP_BACKWARD_TEMPLATE):
                with self.subTest(spec=spec.name, blank=not frame.any()):
                    self.assertFalse(matcher.passes(matcher.match(frame, spec), spec))

    def test_direct_map_without_arrows_requires_known_footer_and_exclusive_header(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for footer, direct, generated, expected in (
            ("战斗Ⅰ 华特波尔雪山", True, False, MapPageMode.DIRECT_TELEPORT),
            ("战斗 未知地图", True, False, MapPageMode.UNKNOWN),
            ("华特波尔雪山", True, False, MapPageMode.UNKNOWN),
            ("战斗Ⅰ 华特波尔雪山", False, False, MapPageMode.UNKNOWN),
            ("战斗Ⅰ 华特波尔雪山", True, True, MapPageMode.UNKNOWN),
        ):
            with self.subTest(footer=footer, direct=direct, generated=generated):
                navigator = Navigator(
                    SimpleNamespace(info_set=lambda *_args: None),
                    SimpleNamespace(
                        simplify=lambda value: value,
                        ocr_text=lambda _frame, label, **_kwargs: (
                            "移动魔法阵" if label == "地图页面左上标题" else footer
                        ),
                    ),
                )
                navigator._map_page_template_signal = lambda _frame, spec: (
                    {"交互直传页图标": direct, "技能生成页图标": generated}.get(spec.name, False),
                    spec.name,
                )
                self.assertEqual(expected, navigator._detect_map_page_mode(frame).mode)

    def test_map_page_identity_reads_the_dedicated_upper_left_roi(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        navigator = Navigator(
            SimpleNamespace(info_set=lambda *_args: None),
            SimpleNamespace(
                simplify=lambda value: value,
                ocr_text=lambda _frame, label, **kwargs: (
                    ocr_calls.append((label, kwargs.get("relative_roi")))
                    or "移动魔法阵"
                ),
            ),
        )
        navigator._map_page_template_signal = lambda _frame, spec: (
            spec.name in {"交互直传页图标", "传送阵地图向前"},
            spec.name,
        )

        detection = navigator._detect_map_page_mode(frame)

        self.assertEqual(MapPageMode.DIRECT_TELEPORT, detection.mode)
        self.assertEqual(
            [("地图页面左上标题", TELEPORT_MAP_HEADER_OCR_RELATIVE_ROI)],
            ocr_calls,
        )

    def test_map_page_open_wait_rejects_trigger_visual_mode_conflict(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.GENERATE_TELEPORT
        )

        result = navigator._wait_for_sandbox_map_open(
            "传送阵交互按钮",
            expected_mode=MapPageMode.DIRECT_TELEPORT,
        )

        self.assertFalse(result.success)
        self.assertEqual(MapPageMode.GENERATE_TELEPORT, result.map_page_mode)
        self.assertIn("不一致", result.message)

    def test_map_page_open_wait_requires_two_stable_visual_frames(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        captures = []
        sleeps = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            sleep=sleeps.append,
        )
        navigator = Navigator(
            task,
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.DIRECT_TELEPORT
        )

        result = navigator._wait_for_sandbox_map_open(
            "传送阵交互按钮",
            expected_mode=MapPageMode.DIRECT_TELEPORT,
        )

        self.assertTrue(result.success)
        self.assertEqual(MapPageMode.DIRECT_TELEPORT, result.map_page_mode)
        self.assertEqual(2, len(captures))
        self.assertEqual([0.15], sleeps)

    def test_sandbox_large_map_requires_two_footer_keywords_and_known_title(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        text = {
            "地图页面左上标题": "战斗Ⅱ 卢戈森林深处",
            "箱庭大地图底部控件": "探索 0",
        }
        navigator = Navigator(
            SimpleNamespace(info_set=lambda *_args: None),
            SimpleNamespace(
                simplify=lambda value: value,
                ocr_text=lambda _frame, label, **_kwargs: text[label],
            ),
        )
        navigator._map_page_template_signal = lambda _frame, spec: (
            True,
            f"{spec.name}=pass",
        )

        self.assertEqual(
            MapPageMode.UNKNOWN,
            navigator._detect_map_page_mode(frame).mode,
        )
        text["箱庭大地图底部控件"] = "在战场中查看 探索 0"
        self.assertEqual(
            MapPageMode.SANDBOX_LARGE_MAP,
            navigator._detect_map_page_mode(frame).mode,
        )
        text["地图页面左上标题"] = "未知区域"
        text["箱庭大地图底部控件"] = "在战场中查看 探索 0 世界地图"
        self.assertEqual(
            MapPageMode.UNKNOWN,
            navigator._detect_map_page_mode(frame).mode,
        )
        for title in (
            "安全 科库托斯暗黑神殿", "安全 记忆岔路口", "安全 学校2楼", "安全 埃兰旅馆",
        ):
            with self.subTest(title=title):
                text["地图页面左上标题"] = title
                self.assertEqual(
                    MapPageMode.SANDBOX_LARGE_MAP, navigator._detect_map_page_mode(frame).mode,
                )
        for left, right in ((True, False), (False, True), (False, False)):
            with self.subTest(left=left, right=right):
                navigator._map_page_template_signal = lambda _frame, spec: (
                    left if "左箭头" in spec.name else right, spec.name,
                )
                self.assertEqual(
                    MapPageMode.SANDBOX_LARGE_MAP,
                    navigator._detect_map_page_mode(frame).mode,
                )
        navigator._map_page_template_signal = lambda *_: (False, "miss")
        text["箱庭大地图底部控件"] = "探索 世界地图"
        self.assertEqual(MapPageMode.UNKNOWN, navigator._detect_map_page_mode(frame).mode)
        self.assertEqual((), navigator._target_keys_in_text(CARD_BY_ID["Q_sp18"], "安全记忆岔路口"))

    def test_ensure_area_map_recovers_existing_generate_mode(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        navigator = Navigator(SimpleNamespace(), SimpleNamespace(capture=lambda: frame))
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.GENERATE_TELEPORT
        )

        result = navigator.ensure_area_map()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual(MapPageMode.GENERATE_TELEPORT, result.map_page_mode)

    def test_unknown_page_mode_never_clicks_teleport_destination(self):
        navigator = Navigator(
            SimpleNamespace(info_set=lambda *_args: None),
            SimpleNamespace(
                click_client=lambda *_args, **_kwargs: self.fail(
                    "unknown map mode must never click"
                )
            ),
        )
        teleport = MatchResult(0.99, (100, 100), (40, 40), 0.95, 0.93)

        self.assertFalse(
            navigator._click_teleport_map_destination(
                teleport,
                (1080, 1920, 3),
                page_mode=MapPageMode.UNKNOWN,
            )
        )

    def test_sandbox_large_map_is_rejected_before_area_controls_are_scanned(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            ocr_text=lambda *_args, **_kwargs: self.fail(
                "large map must not enter teleport title OCR"
            ),
            match=lambda *_args, **_kwargs: self.fail(
                "large map must not scan teleport controls"
            ),
        )
        navigator = Navigator(
            SimpleNamespace(info_set=lambda *_args: None),
            vision,
        )
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.SANDBOX_LARGE_MAP,
            header_text="战斗Ⅱ 卢戈森林深处",
        )

        context = navigator._area_map_context(frame, CARD_BY_ID["Q_sp1"])

        self.assertFalse(context.is_area_map)
        self.assertEqual(MapPageMode.SANDBOX_LARGE_MAP, context.map_page_mode)
        self.assertEqual((), context.teleports)

    def test_area_map_back_template_uses_scoped_finite_scale_matching(self):
        self.assertEqual("image/green/BackButGe.png", AREA_MAP_BACK_TEMPLATE.file_name)
        self.assertIsNone(AREA_MAP_BACK_TEMPLATE.roi)
        self.assertIsNotNone(AREA_MAP_BACK_TEMPLATE.relative_roi)
        self.assertEqual((0.70, 0.75, 0.80), AREA_MAP_BACK_TEMPLATE.scale_ratios)
        self.assertEqual(0.88, AREA_MAP_BACK_TEMPLATE.threshold)
        self.assertEqual(0.85, AREA_MAP_BACK_TEMPLATE.min_pixel_score)

    def test_area_map_uses_user_confirmed_relative_geometry(self):
        self.assertEqual((289 / 1920, 253 / 1080), AREA_MAP_OPEN_RELATIVE_POINT)
        self.assertEqual(
            (654 / 1920, 946 / 1080, 1268 / 1920, 1021 / 1080),
            TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
        )
        self.assertEqual((136 / 1920, 52 / 1080), TELEPORT_MAP_RETURN_RELATIVE_POINT)

    def test_prepare_collection_main_closes_map_when_initial_title_is_main(self):
        card = CARD_BY_ID["Q_sp1"]
        events = []
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator.ensure_small_minimap = lambda: True
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        navigator._wait_for_collection_teleport_map = lambda _card: self._area_context(
            card.targets[0].title,
            card.targets[0].key,
            right=True,
        )
        navigator.return_teleport_map_to_sandbox = lambda number: (
            events.append(("return", number)) or NavigationResult(True, ScreenState.SANDBOX)
        )
        navigator._seek_collection_page = lambda *_args: self.fail(
            "already-main title must not be paged"
        )

        result = navigator.prepare_collection_main_area(card.card_id)

        self.assertTrue(result.success)
        self.assertEqual([("return", 1)], events)

    def test_prepare_collection_main_pages_to_first_title_then_teleports(self):
        card = CARD_BY_ID["Q_sp1"]
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        contexts = iter(
            (
                self._area_context(
                    card.targets[1].title,
                    card.targets[1].key,
                    left=True,
                    right=True,
                ),
                self._area_context(
                    card.targets[0].title,
                    card.targets[0].key,
                    right=True,
                    teleports=(teleport,),
                ),
            )
        )
        moves = []
        clicks = []
        generation_calls = []
        vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            )
        )
        navigator = Navigator(SimpleNamespace(), vision)
        navigator.ensure_small_minimap = lambda: True
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        navigator._click_teleport_generation = lambda received, shape: (
            generation_calls.append((received, shape)) or True
        )
        initial = self._area_context(
            card.targets[2].title,
            card.targets[2].key,
            left=True,
        )
        navigator._wait_for_collection_teleport_map = lambda _card: initial
        navigator._move_area_map = lambda _card, _context, direction: (
            moves.append(direction) or next(contexts)
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )
        arrivals = []
        navigator._confirm_collection_arrival = lambda received_card, target: (
            arrivals.append((received_card.card_id, target.key))
            or NavigationResult(True, ScreenState.SANDBOX, target.title)
        )

        result = navigator.prepare_collection_main_area(card.card_id)

        self.assertTrue(result.success)
        self.assertEqual(["left", "left"], moves)
        self.assertEqual(
            [(teleport.center, (1080, 1920, 3), 0.0)],
            clicks,
        )
        self.assertEqual([], generation_calls)
        self.assertEqual([(card.card_id, card.targets[0].key)], arrivals)

    def test_prepare_collection_main_pages_from_unresolved_hotel_submap_then_teleports(self):
        card = CARD_BY_ID["Q_sp10"]
        teleport = MatchResult(0.99, (880, 450), (60, 60), 0.95, 0.93)
        contexts = iter(
            (
                self._area_context(
                    card.targets[2].title,
                    card.targets[2].key,
                    left=True,
                    right=True,
                ),
                self._area_context(
                    card.targets[1].title,
                    card.targets[1].key,
                    left=True,
                    right=True,
                ),
                self._area_context(
                    card.targets[0].title,
                    card.targets[0].key,
                    left=False,
                    right=True,
                    teleports=(teleport,),
                ),
            )
        )
        moves = []
        clicks = []
        vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            )
        )
        navigator = Navigator(SimpleNamespace(), vision)
        navigator.ensure_small_minimap = lambda: True
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        hotel_initial = self._area_context(
            "安全 埃兰旅馆",
            target_key=None,
            left=True,
            right=True,
        )
        clock = [0.0]
        navigator.task.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        navigator._capture_area_map_context = Mock(return_value=hotel_initial)
        navigator._move_area_map = lambda _card, _context, direction: (
            moves.append(direction) or next(contexts)
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )
        navigator._confirm_collection_arrival = Mock(
            return_value=NavigationResult(True, ScreenState.SANDBOX)
        )

        with patch("src.tasks.map_trade.navigator_sandbox.monotonic", lambda: clock[0]):
            result = navigator.prepare_collection_main_area(card.card_id)

        self.assertTrue(result.success)
        self.assertEqual(2, navigator._capture_area_map_context.call_count)
        self.assertEqual(["left", "left", "left"], moves)
        self.assertEqual(
            [(teleport.center, (1080, 1920, 3), 0.0)],
            clicks,
        )
        navigator._confirm_collection_arrival.assert_called_once_with(card, card.targets[0])

    def test_wait_for_collection_teleport_map_confirms_stable_transit_titles(self):
        card = CARD_BY_ID["Q_sp10"]
        for mode in (MapPageMode.DIRECT_TELEPORT, MapPageMode.GENERATE_TELEPORT):
            for title in ("安全 埃兰旅馆", "战斗 君主之墓", "战斗 蜥蜴人祭坛"):
                with self.subTest(mode=mode, title=title):
                    observed = self._area_context(title, page_mode=mode)
                    clock = [0.0]
                    navigator = Navigator(SimpleNamespace(
                        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    ), SimpleNamespace())
                    navigator._capture_area_map_context = Mock(return_value=observed)
                    with patch("src.tasks.map_trade.navigator_sandbox.monotonic", lambda: clock[0]):
                        context = navigator._wait_for_collection_teleport_map(card, timeout=1)
                    self.assertIs(context, observed)
                    self.assertEqual(2, navigator._capture_area_map_context.call_count)
                    self.assertIsNone(context.resolved_target_key)

    def test_wait_for_collection_teleport_map_requires_consecutive_page_evidence(self):
        card = CARD_BY_ID["Q_sp10"]
        transit = self._area_context("安全 埃兰旅馆", left=True, right=True)
        unknown = replace(transit, map_page_mode=MapPageMode.UNKNOWN)
        large_map = replace(transit, map_page_mode=MapPageMode.SANDBOX_LARGE_MAP)
        generated = replace(transit, map_page_mode=MapPageMode.GENERATE_TELEPORT)
        ambiguous = replace(transit, candidate_target_keys=("main_area", "battle_area_1"))
        cases = (
            ("changing title", [transit, replace(transit, normalized_text="其他旅馆")] * 2, False),
            ("blank title", [replace(transit, normalized_text="")] * 4, False),
            ("large map", [large_map] * 4, False),
            ("unknown", [unknown] * 4, False),
            ("ambiguous targets", [ambiguous], False),
            ("changing mode", [transit, generated] * 2, False),
            ("interrupted", [transit, unknown] * 2, False),
            ("fresh confirmation after interruption", [transit, unknown, transit, transit], True),
        )
        for name, observations, accepted in cases:
            with self.subTest(case=name):
                samples = iter(observations)
                clock = [0.0]
                navigator = Navigator(SimpleNamespace(
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                ), SimpleNamespace(click_client=Mock()))
                navigator._capture_area_map_context = Mock(
                    side_effect=lambda _card: next(samples, observations[-1]),
                )
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic", lambda: clock[0]):
                    result = navigator._wait_for_collection_teleport_map(
                        card, timeout=AREA_MAP_CHANGE_INTERVAL * 3.5,
                    )
                if accepted:
                    self.assertIs(result, transit)
                    self.assertEqual(4, navigator._capture_area_map_context.call_count)
                else:
                    self.assertFalse(result.success)
                navigator.vision.click_client.assert_not_called()
        for signal in (TaskDisabledException, FinishedException):
            with self.subTest(signal=signal):
                navigator._capture_area_map_context = Mock(side_effect=signal)
                with self.assertRaises(signal):
                    navigator._wait_for_collection_teleport_map(card)

    def test_collection_routes_seek_named_targets_after_hunting_ground_transit(self):
        left = MatchResult(0.99, (100, 500), (30, 30), pixel_score=0.98)
        right = MatchResult(0.99, (1500, 500), (30, 30), pixel_score=0.98)
        teleport = MatchResult(0.99, (880, 450), (60, 60), 0.95, 0.93)
        for card_id, transit_title in (
            ("Q_sp5", "战斗 君主之墓"), ("Q_sp7", "战斗 蜥蜴人祭坛"),
            ("Q_sp10", "安全 埃兰旅馆"),
        ):
            for operation in ("initialize", "advance", "target absent"):
                with self.subTest(card=card_id, operation=operation):
                    card = CARD_BY_ID[card_id]
                    target = card.targets[0 if operation == "initialize" else 1]
                    pages = [self._area_context(
                        item.title, item.key, teleports=(teleport,),
                    ) for item in card.targets if operation != "target absent" or item != target]
                    pages.append(self._area_context(transit_title))
                    pages = [replace(page,
                        left_arrow=left if index else None,
                        right_arrow=right if index < len(pages) - 1 else None,
                    ) for index, page in enumerate(pages)]
                    position = [len(pages) - 1]
                    clock = [0.0]
                    destination_clicks = []

                    def click(point, _shape, **_kwargs):
                        if point in (left.center, TELEPORT_PAGE_ARROW_REFERENCE["left"]):
                            position[0] = max(0, position[0] - 1)
                        elif point in (right.center, TELEPORT_PAGE_ARROW_REFERENCE["right"]):
                            position[0] = min(len(pages) - 1, position[0] + 1)
                        else:
                            self.assertEqual(teleport.center, point)
                            self.assertEqual(target.key, pages[position[0]].resolved_target_key)
                            destination_clicks.append(point)

                    navigator = Navigator(SimpleNamespace(
                        info_set=Mock(),
                        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    ), SimpleNamespace(click_client=click))
                    sandbox = NavigationResult(True, ScreenState.SANDBOX)
                    opened = NavigationResult(
                        True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.DIRECT_TELEPORT,
                    )
                    navigator.ensure_small_minimap = Mock(return_value=True)
                    navigator._click_sandbox_teleport_interaction = Mock(return_value=False)
                    navigator.open_teleport_map_from_sandbox = Mock(side_effect=(
                        (opened,) if operation == "initialize" else
                        (NavigationResult(False, ScreenState.SANDBOX), opened)
                    ))
                    navigator._restore_navigation_field = Mock(return_value=sandbox)
                    navigator._walk_to_circle_neighbour = Mock(return_value=None)
                    navigator._walk_to_sandbox_teleport_interaction = Mock(
                        return_value=NavigationResult(False, ScreenState.SANDBOX),
                    )
                    navigator._travel_to_hunting_ground = Mock(return_value="狩猎场")
                    navigator._capture_area_map_context = lambda _card: pages[position[0]]
                    navigator._wait_for_loading_screen = Mock(return_value=True)
                    navigator._wait_for_story_sandbox = Mock(return_value=sandbox)
                    navigator._confirm_collection_arrival = Mock(return_value=sandbox)
                    with patch("src.tasks.map_trade.navigator_sandbox.monotonic", lambda: clock[0]):
                        result = (
                            navigator.prepare_collection_main_area(card_id, via_hunting_ground=True)
                            if operation == "initialize" else
                            navigator.advance_collection_map(card_id, card.targets[0], target)
                        )
                    navigator._travel_to_hunting_ground.assert_called_once_with()
                    if operation == "target absent":
                        self.assertFalse(result.success)
                        self.assertIn("左右翻页都未找到", result.message)
                        self.assertEqual([], destination_clicks)
                        navigator._confirm_collection_arrival.assert_not_called()
                        self.assertLess(clock[0], 60)
                    else:
                        self.assertTrue(result.success)
                        self.assertEqual([teleport.center], destination_clicks)
                        navigator._confirm_collection_arrival.assert_called_once_with(card, target)

    def test_advance_collection_map_moves_back_exactly_one_confirmed_page(self):
        card = CARD_BY_ID["Q_sp1"]
        current, target = card.targets[1:]
        teleport = MatchResult(0.99, (1000, 500), (60, 60), 0.95, 0.93)
        clicks = []
        moves = []
        vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(SimpleNamespace(), vision)
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "已点击箱庭5号传送阵技能",
            map_page_mode=MapPageMode.GENERATE_TELEPORT,
        )
        generation_calls = []
        navigator._click_teleport_generation = lambda received, shape: (
            generation_calls.append((received, shape)) or True
        )
        navigator._wait_for_collection_teleport_map = lambda _card: self._area_context(
            current.title,
            current.key,
            left=True,
            right=True,
            page_mode=MapPageMode.GENERATE_TELEPORT,
        )
        navigator._move_area_map = lambda _card, _context, direction: (
            moves.append(direction)
            or self._area_context(
                target.title,
                target.key,
                left=True,
                teleports=(teleport,),
                page_mode=MapPageMode.GENERATE_TELEPORT,
            )
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )
        arrivals = []
        navigator._confirm_collection_arrival = lambda received_card, received_target: (
            arrivals.append((received_card.card_id, received_target.key))
            or NavigationResult(True, ScreenState.SANDBOX, received_target.title)
        )

        result = navigator.advance_collection_map(card.card_id, current, target)

        self.assertTrue(result.success)
        self.assertEqual(["right"], moves)
        self.assertEqual([], clicks)
        self.assertEqual([(teleport, (1080, 1920, 3))], generation_calls)
        self.assertEqual([(card.card_id, target.key)], arrivals)

    def test_teleport_map_page_arrows_are_strict_and_directional(self):
        self.assertEqual("image/green/TpMapLeft.png", TELEPORT_MAP_FORWARD_TEMPLATE.file_name)
        self.assertEqual("image/green/TpMapRight.png", TELEPORT_MAP_BACKWARD_TEMPLATE.file_name)
        for spec in (TELEPORT_MAP_FORWARD_TEMPLATE, TELEPORT_MAP_BACKWARD_TEMPLATE):
            with self.subTest(spec=spec.name):
                self.assertEqual(0.93, spec.threshold)
                self.assertEqual(0.85, spec.min_pixel_score)
                self.assertEqual(0.90, spec.min_zncc_score)
                self.assertEqual(0.93, spec.minimum_safe_threshold)
                self.assertIsNone(spec.roi)
                self.assertIsNotNone(spec.relative_roi)

    def test_sandbox_teleport_skill_is_separate_from_map_skill(self):
        self.assertEqual(
            "image/green/Skill3-4GE.png",
            SANDBOX_TELEPORT_SKILL_TEMPLATE.file_name,
        )
        self.assertEqual(0.95, SANDBOX_TELEPORT_SKILL_TEMPLATE.threshold)
        self.assertEqual(0.85, SANDBOX_TELEPORT_SKILL_TEMPLATE.min_pixel_score)
        self.assertEqual(0.85, SANDBOX_TELEPORT_SKILL_TEMPLATE.min_zncc_score)
        self.assertNotIn(
            SANDBOX_TELEPORT_SKILL_TEMPLATE,
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
        )

    def test_sandbox_map_teleport_template_uses_sandbox_asset_name(self):
        self.assertEqual("箱庭地图传送阵模板", SANDBOX_MAP_TELEPORT_TEMPLATE.name)
        self.assertEqual(
            "image/green/SandboxNviTpCircleMapGE.png",
            SANDBOX_MAP_TELEPORT_TEMPLATE.file_name,
        )
        self.assertNotEqual(
            SANDBOX_MAP_TELEPORT_TEMPLATE.file_name,
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE.file_name,
        )
        for file_name in (
            "image/SandboxTpCircleMap.png",
            "image/green/SandboxNviTpCircleMapGE.png",
            "image/green/SandboxTpCircleMapGE.png",
            "image/green/TpCircleMapNewGE.png",
        ):
            with self.subTest(file_name=file_name):
                self.assertTrue((ROOT / "recognition-assets/template-assets" / file_name).is_file())

    def test_teleport_map_route_prefers_interaction_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        hand = MatchResult(
            0.968,
            (820, 470),
            (44, 43),
            pixel_score=0.92,
            zncc_score=0.90,
        )
        clicks = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            sleep=lambda *_args: self.fail("a passing interaction must click immediately"),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda received, spec: (
                self.assertIs(received, frame) or self.assertIs(spec, HAND_TEMPLATE) or hand
            ),
            passes=lambda result, spec: result is hand and spec is HAND_TEMPLATE,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._detect_map_page_mode = Mock(
            return_value=SimpleNamespace(mode=MapPageMode.UNKNOWN),
        )
        navigator._restore_navigation_field = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "交互按钮已确认传送阵地图",
            map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        navigator._click_sandbox_teleport_skill = lambda: self.fail(
            "interaction route must not click the fifth skill"
        )

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual(MapPageMode.DIRECT_TELEPORT, result.map_page_mode)
        self.assertEqual(
            [(hand.center, frame.shape, TELEPORT_INTERACTION_CLICK_DELAY)],
            clicks,
        )

    def test_teleport_map_route_uses_skill_center_when_interaction_is_missing(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        skill = MatchResult(
            0.968,
            (1760, 790),
            (44, 43),
            pixel_score=0.873,
            zncc_score=0.900,
        )
        clicks = []
        task = SimpleNamespace(
            capture_frame=lambda: frame,
            info_set=lambda *_args: None,
            sleep=lambda *_args: self.fail("a passing skill must click immediately"),
        )

        def match(received, spec):
            self.assertIs(received, frame)
            if spec is HAND_TEMPLATE:
                return MatchResult(-1.0, (0, 0), (0, 0))
            self.assertIs(spec, SANDBOX_TELEPORT_SKILL_TEMPLATE)
            return skill

        vision = SimpleNamespace(
            capture=lambda: frame,
            match=match,
            passes=lambda result, spec: result is skill and spec is SANDBOX_TELEPORT_SKILL_TEMPLATE,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._detect_map_page_mode = Mock(
            return_value=SimpleNamespace(mode=MapPageMode.UNKNOWN),
        )
        navigator._restore_navigation_field = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "技能已确认传送阵地图",
            map_page_mode=MapPageMode.GENERATE_TELEPORT,
        )

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual(MapPageMode.GENERATE_TELEPORT, result.map_page_mode)
        self.assertEqual(
            [(skill.center, frame.shape, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_teleport_map_route_fails_without_blind_click_when_skill_is_missing(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        missing = MatchResult(-1.0, (0, 0), (0, 0))
        clicks = []
        fixed_clicks = []
        sleeps = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            sleep=sleeps.append,
            operate_click=lambda *args, **kwargs: fixed_clicks.append((args, kwargs)),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: (
                self.assertIn(spec, (HAND_TEMPLATE, SANDBOX_TELEPORT_SKILL_TEMPLATE)) or missing
            ),
            passes=lambda *_args: False,
            click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
        )
        navigator = Navigator(task, vision)
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._detect_map_page_mode = Mock(
            return_value=SimpleNamespace(mode=MapPageMode.UNKNOWN),
        )
        navigator._restore_navigation_field = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            False,
            ScreenState.SANDBOX,
            "未确认传送阵地图",
        )

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=(100.0, 100.0, 106.0),
        ):
            result = navigator.open_teleport_map_from_sandbox()

        self.assertFalse(result.success)
        self.assertEqual("未可靠识别箱庭5号传送阵技能，已停止打开传送阵地图", result.message)
        self.assertEqual([], clicks)
        self.assertEqual([], fixed_clicks)
        self.assertEqual([SANDBOX_TELEPORT_SKILL_POLL_INTERVAL], sleeps)

    def test_teleport_skill_failure_ocr_is_explicit_and_enters_walk_fallback(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda received, name: (
                self.assertIs(received, frame)
                or self.assertEqual("箱庭5号传送阵技能失败", name)
                or "无法在魔法阵附近使用天赋技能"
            ),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX

        result = navigator._wait_for_sandbox_map_open(
            "箱庭5号传送阵技能",
            expected_mode=MapPageMode.GENERATE_TELEPORT,
            detect_skill_failure=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertIn("魔法阵附近", result.message)
        self.assertTrue(navigator._sandbox_teleport_skill_failure_matches(result.message))

    def test_teleport_skill_failure_routes_to_walk_fallback_result(self):
        task = SimpleNamespace(info_set=lambda *_args: None)
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        navigator._detect_map_page_mode = Mock(
            return_value=SimpleNamespace(mode=MapPageMode.UNKNOWN),
        )
        navigator._restore_navigation_field = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._click_sandbox_teleport_skill = lambda: True
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            False,
            ScreenState.SANDBOX,
            "箱庭5号传送阵技能失败 OCR命中：无法在魔法阵附近使用天赋技能",
        )
        fallback_calls = []

        def walk_fallback():
            fallback_calls.append(True)
            return NavigationResult(
                True,
                ScreenState.AREA_MAP,
                "已通过徒步回退",
                map_page_mode=MapPageMode.DIRECT_TELEPORT,
            )

        navigator._walk_to_sandbox_teleport_interaction = walk_fallback

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual([True], fallback_calls)
        self.assertEqual(MapPageMode.DIRECT_TELEPORT, result.map_page_mode)
        self.assertIn("徒步回退", result.message)

    def test_walk_fallback_selects_unique_navigation_teleport_then_interacts(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(
            0.96,
            (500, 300),
            (40, 40),
            pixel_score=0.90,
            zncc_score=0.88,
        )
        hand = MatchResult(
            0.97,
            (820, 470),
            (44, 43),
            pixel_score=0.92,
            zncc_score=0.90,
        )
        clicks = []
        clock = [0.0]
        task = SimpleNamespace(
            config={}, info_set=lambda *_args: None,
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )

        def match(received, spec):
            self.assertIs(received, frame)
            self.assertIs(spec, HAND_TEMPLATE)
            return hand

        vision = SimpleNamespace(
            capture=lambda: frame,
            simplify=lambda text: text,
            ocr_text=lambda *_args, **_kwargs: "",
            match=match,
            match_all=lambda received, spec, **_kwargs: (
                self.assertIs(received, frame)
                or self.assertIs(spec, SANDBOX_MAP_TELEPORT_TEMPLATE)
                or (teleport,)
            ),
            threshold_for=lambda spec: spec.threshold,
            passes=lambda result, spec: result in (hand, teleport),
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX
        navigator._open_field_map = lambda: "安全区域"
        navigator._click_sandbox_navigation_map = lambda: True
        navigator._sandbox_navigation_page_has_keyword = lambda _frame: True
        navigator._close_confirmed_map_page = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        menu_calls = []

        def click_menu(_frame):
            menu_calls.append(True)
            return len(menu_calls) == 1

        navigator._click_sandbox_navigation_menu_teleport = click_menu
        navigator._click_sandbox_navigation_destination_confirmation = lambda _frame: True
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "徒步交互已确认传送阵地图",
            map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0],
        ):
            result = navigator._walk_to_sandbox_teleport_interaction()

        self.assertTrue(result.success)
        self.assertEqual(
            [
                (teleport.center, frame.shape, 3.0),
                (hand.center, frame.shape, TELEPORT_INTERACTION_CLICK_DELAY),
            ],
            clicks,
        )

    def test_walk_fallback_destination_confirmation_clicks_ocr_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        box = SimpleNamespace(name="确认", x=700, y=500, width=120, height=40)
        task = SimpleNamespace(info_set=lambda *_args: None)
        vision = SimpleNamespace(
            ocr_boxes=lambda received, name: (
                self.assertIs(received, frame)
                or self.assertEqual("箱庭徒步导航传送阵确认", name)
                or [box]
            ),
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        self.assertTrue(navigator._click_sandbox_navigation_destination_confirmation(frame))
        self.assertEqual([((760, 520), frame.shape, 0.25)], clicks)

    def test_teleport_generation_clicks_unique_white_center_then_generate_box_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        clicks = []
        ocr_frames = []
        boxes = [
            SimpleNamespace(name="生成魔法阵", x=880, y=430, width=120, height=40),
            SimpleNamespace(name="取消", x=740, y=620, width=220, height=48),
            SimpleNamespace(name="生成5", x=990, y=620, width=220, height=48),
        ]
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: ocr_frames.append(frame) or frame,
            ocr_boxes=lambda received, name: (
                self.assertIs(received, frame) or self.assertEqual("传送阵生成确认", name) or boxes
            ),
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        self.assertTrue(navigator._click_teleport_generation(teleport, frame.shape))
        self.assertEqual(
            [
                (teleport.center, frame.shape, 0.5),
                ((1100, 644), frame.shape, TELEPORT_MAP_TRAVEL_SETTLE_SECONDS),
            ],
            clicks,
        )
        self.assertEqual([frame], ocr_frames)

    def test_teleport_generation_rejects_missing_and_selects_strongest_multiple_candidate(self):
        weaker = MatchResult(0.989, (800, 400), (60, 60), 0.932, 0.947)
        stronger = MatchResult(0.994, (1038, 659), (60, 60), 0.949, 0.973)
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            sleep=lambda _seconds: None,
        )
        vision = SimpleNamespace(click_client=lambda *_args, **_kwargs: None)
        navigator = Navigator(task, vision)
        card = CARD_BY_ID["Q_sp1"]
        missing = self._area_context(
            card.targets[1].title,
            card.targets[1].key,
            teleports=(),
        )
        navigator._capture_area_map_context = lambda _card: missing
        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=(100.0, 100.0, 101.0, 103.0),
        ):
            result = navigator._click_collection_destination(card, card.targets[1], missing)
        self.assertFalse(result.success)
        self.assertIn("未识别到", result.message)

        selected = []
        navigator._click_teleport_map_destination = (
            lambda teleport, _shape, *, page_mode: (
                self.assertEqual(MapPageMode.DIRECT_TELEPORT, page_mode)
                or selected.append(teleport)
                or True
            )
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )
        navigator._confirm_collection_arrival = Mock(
            return_value=NavigationResult(True, ScreenState.SANDBOX)
        )
        multiple = self._area_context(
            card.targets[1].title,
            card.targets[1].key,
            teleports=(weaker, stronger),
        )
        result = navigator._click_collection_destination(card, card.targets[1], multiple)
        self.assertTrue(result.success)
        self.assertEqual([stronger], selected)
        navigator._confirm_collection_arrival.assert_called_once_with(card, card.targets[1])

        navigator._confirm_collection_arrival.reset_mock()
        navigator._wait_for_story_sandbox = lambda _number: NavigationResult(
            False, ScreenState.AREA_MAP, "still on the teleport page"
        )
        result = navigator._click_collection_destination(card, card.targets[1], multiple)
        self.assertFalse(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        navigator._confirm_collection_arrival.assert_not_called()

    def test_collection_destination_retries_until_first_page_teleport_renders(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        refreshed = self._area_context(
            target.title,
            target.key,
            teleports=(teleport,),
        )
        captures = iter((refreshed,))
        sleeps = []
        selected = []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *_args: None,
                sleep=sleeps.append,
            ),
            SimpleNamespace(),
        )
        navigator._capture_area_map_context = lambda _card: next(captures)
        navigator._click_teleport_map_destination = (
            lambda received, _shape, *, page_mode: (
                selected.append((received, page_mode)) or True
            )
        )
        navigator._wait_for_story_sandbox = lambda _number: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )
        navigator._confirm_collection_arrival = Mock(
            return_value=NavigationResult(True, ScreenState.SANDBOX)
        )

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=(100.0, 100.0),
        ):
            result = navigator._click_collection_destination(
                card,
                target,
                self._area_context(target.title, target.key),
            )

        self.assertTrue(result.success)
        self.assertEqual([AREA_MAP_CHANGE_INTERVAL], sleeps)
        self.assertEqual([(teleport, MapPageMode.DIRECT_TELEPORT)], selected)
        navigator._confirm_collection_arrival.assert_called_once_with(card, target)

    def test_collection_destination_requires_matching_arrival_map(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        navigator = Navigator(
            SimpleNamespace(info_set=lambda *_args: None),
            SimpleNamespace(click_client=lambda *_args, **_kwargs: None),
        )
        navigator._click_teleport_map_destination = lambda *_args, **_kwargs: True
        navigator.ensure_small_minimap = Mock(return_value=False)
        navigator._wait_for_story_sandbox = lambda _number: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )
        for mode in (MapPageMode.DIRECT_TELEPORT, MapPageMode.GENERATE_TELEPORT):
            for actual in (target, card.targets[2], None):
                with self.subTest(mode=mode, actual=actual.key if actual else None):
                    arrived = self._area_context(
                        actual.title if actual else "",
                        actual.key if actual else None,
                        page_mode=mode,
                    )
                    navigator.ensure_area_map = Mock(return_value=NavigationResult(
                        True, ScreenState.AREA_MAP, map_page_mode=mode,
                    ))
                    navigator._capture_area_map_context = Mock(return_value=arrived)
                    navigator._close_area_map = Mock(return_value=NavigationResult(
                        True, ScreenState.SANDBOX,
                    ))

                    result = navigator._click_collection_destination(
                        card, target, self._area_context(
                            target.title, target.key, teleports=(teleport,), page_mode=mode,
                        ),
                    )

                    navigator.ensure_area_map.assert_called_once_with()
                    navigator._capture_area_map_context.assert_called_once_with(card)
                    if actual == target:
                        self.assertTrue(result.success)
                        self.assertEqual(ScreenState.SANDBOX, result.state)
                        navigator._close_area_map.assert_called_once_with(arrived)
                    else:
                        self.assertFalse(result.success)
                        self.assertEqual(ScreenState.AREA_MAP, result.state)
                        self.assertIn("到达后地图不符", result.message)
                        self.assertIn(target.title, result.message)
                        self.assertIn(actual.key if actual else "未知", result.message)
                        navigator._close_area_map.assert_not_called()

    def test_collection_arrival_propagates_stop_signals(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        navigator = Navigator(SimpleNamespace(info_set=Mock()), SimpleNamespace())
        navigator._click_teleport_map_destination = Mock(return_value=True)
        navigator._wait_for_story_sandbox = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        navigator._capture_area_map_context = Mock()
        navigator.ensure_small_minimap = Mock(return_value=False)
        for mode in (MapPageMode.DIRECT_TELEPORT, MapPageMode.GENERATE_TELEPORT):
            for signal in (TaskDisabledException, FinishedException):
                with self.subTest(mode=mode, signal=signal):
                    navigator.ensure_area_map = Mock(side_effect=signal)
                    with self.assertRaises(signal):
                        navigator._click_collection_destination(
                            card, target, self._area_context(
                                target.title, target.key, teleports=(teleport,), page_mode=mode,
                            ),
                        )
                    navigator._capture_area_map_context.assert_not_called()

    def test_teleport_generation_rejects_missing_keyword_without_generate_click(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        clicks = []
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_boxes=lambda *_args: [
                SimpleNamespace(name="生成魔法阵", x=880, y=430, width=120, height=40),
                SimpleNamespace(name="取消", x=740, y=620, width=220, height=48),
            ],
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=(100.0, 100.0, 109.0),
        ):
            result = navigator._click_teleport_generation(
                teleport,
                frame.shape,
                timeout=TELEPORT_GENERATION_OCR_TIMEOUT,
            )

        self.assertFalse(result)
        self.assertEqual([(teleport.center, frame.shape, 0.5)], clicks)

    def test_teleport_generation_rejects_ambiguous_generate_button(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        clicks = []
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_boxes=lambda *_args: [
                SimpleNamespace(name="生成魔法阵", x=880, y=430, width=120, height=40),
                SimpleNamespace(name="取消", x=740, y=620, width=220, height=48),
                SimpleNamespace(name="生成5", x=990, y=620, width=220, height=48),
                SimpleNamespace(name="生成5", x=1230, y=620, width=220, height=48),
            ],
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=(100.0, 100.0, 109.0),
        ):
            result = navigator._click_teleport_generation(
                teleport,
                frame.shape,
                timeout=TELEPORT_GENERATION_OCR_TIMEOUT,
            )

        self.assertFalse(result)
        self.assertEqual([(teleport.center, frame.shape, 0.5)], clicks)

    def test_return_teleport_map_clicks_confirmed_point_and_reuses_stable_sandbox_wait(self):
        clicks = []
        confirmed_numbers = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.DIRECT_TELEPORT
        )
        navigator._wait_for_story_sandbox = lambda number, **_kwargs: (
            confirmed_numbers.append(number)
            or NavigationResult(True, ScreenState.SANDBOX, f"Q_sp{number}")
        )

        result = navigator.return_teleport_map_to_sandbox(1)

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertEqual([1], confirmed_numbers)
        self.assertEqual(
            [(*TELEPORT_MAP_RETURN_RELATIVE_POINT, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_return_teleport_map_prefers_recognized_back_button_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        back = MatchResult(0.98, (110, 35), (40, 40), 0.95, 0.92)
        clicks = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda *_args, **_kwargs: self.fail(
                "recognized back button must win over the fallback point"
            )
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: (
                back if spec is AREA_MAP_BACK_TEMPLATE else MatchResult(-1.0, (0, 0), (0, 0))
            ),
            passes=lambda result, _spec: result is back,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.GENERATE_TELEPORT
        )
        navigator._wait_for_story_sandbox = lambda number, **_kwargs: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )

        result = navigator.return_teleport_map_to_sandbox(1)

        self.assertTrue(result.success)
        self.assertEqual(
            [(back.center, frame.shape, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_close_sandbox_large_map_uses_its_own_confirmed_relative_point(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.SANDBOX_LARGE_MAP
        )
        navigator._wait_for_current_sandbox = lambda **_kwargs: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )

        result = navigator._close_confirmed_map_page(
            {MapPageMode.SANDBOX_LARGE_MAP}
        )

        self.assertTrue(result.success)
        self.assertEqual(
            [(*SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )
        self.assertNotEqual(
            SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT,
            TELEPORT_MAP_RETURN_RELATIVE_POINT,
        )

    def test_close_map_page_unknown_mode_never_clicks(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: self.fail(
                "unknown page must not use a calibrated return point"
            ),
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        navigator.classify = Mock(return_value=ScreenState.UNKNOWN)
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.UNKNOWN
        )

        result = navigator._close_confirmed_map_page(
            {MapPageMode.DIRECT_TELEPORT}
        )

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.UNKNOWN, result.state)

    def test_open_cartridge_quick_switcher_from_sandbox_never_detours_through_home(self):
        fixed_clicks = []
        template_clicks = []
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: fixed_clicks.append((x, y, after_sleep)),
            open_cartridge_quick_switcher=lambda **_kwargs: self.fail(
                "sandbox route must not use the global-home entry"
            ),
        )
        vision = SimpleNamespace()
        navigator = Navigator(task, vision)
        navigator._click_quick_switch = lambda timeout, after_sleep: (
            template_clicks.append((timeout, after_sleep)) or True
        )
        navigator.return_home = lambda: self.fail(
            "sandbox route must not return to the global home"
        )
        navigator._wait_for_current_sandbox = lambda: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_cartridge_category = lambda **_kwargs: True

        result = navigator.open_cartridge_quick_switcher_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.CARD_MENU, result.state)
        self.assertEqual([(10.0, 1.0)], template_clicks)
        self.assertEqual([(*STORY_CATEGORY_POINT, 0.5)], fixed_clicks)

    def test_open_cartridge_quick_switcher_from_sandbox_stops_before_click_when_unconfirmed(self):
        task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: self.fail(
                "unconfirmed sandbox must not be clicked"
            )
        )
        vision = SimpleNamespace(
            click_stable_template=lambda *_args, **_kwargs: self.fail(
                "unconfirmed sandbox must not scan quick switch"
            )
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_current_sandbox = lambda: NavigationResult(
            False,
            ScreenState.UNKNOWN,
            "未稳定确认当前剧情卡带箱庭",
        )

        result = navigator.open_cartridge_quick_switcher_from_sandbox()

        self.assertFalse(result.success)
        self.assertIn("未稳定确认", result.message)

    def test_open_cartridge_quick_switcher_reuses_immediately_prior_sandbox_confirmation(self):
        template_clicks = []
        task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace()
        navigator = Navigator(task, vision)
        navigator._click_quick_switch = lambda timeout, after_sleep: (
            template_clicks.append((timeout, after_sleep)) or True
        )
        navigator._wait_for_current_sandbox = lambda: self.fail(
            "the caller already confirmed the same sandbox"
        )
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_cartridge_category = lambda **_kwargs: True

        result = navigator.open_cartridge_quick_switcher_from_sandbox(
            sandbox_already_confirmed=True,
        )

        self.assertTrue(result.success)
        self.assertEqual([(10.0, 1.0)], template_clicks)

    def test_current_sandbox_confirmation_requires_consecutive_frames(self):
        frame = np.full((1080, 1920, 3), 100, dtype=np.uint8)
        states = iter(
            (
                ScreenState.SANDBOX,
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        captures = []
        navigator = Navigator(
            SimpleNamespace(sleep=lambda *_args: None),
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator.classify = lambda _frame=None: next(states)

        result = navigator._wait_for_current_sandbox(timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertEqual(4, len(captures))

    def test_interaction_button_template_uses_strict_three_score_gates(self):
        self.assertEqual("image/green/IcoHand.png", HAND_TEMPLATE.file_name)
        self.assertEqual(0.95, HAND_TEMPLATE.threshold)
        self.assertEqual(0.90, HAND_TEMPLATE.min_pixel_score)
        self.assertEqual(0.85, HAND_TEMPLATE.min_zncc_score)
        self.assertEqual(0.95, HAND_TEMPLATE.minimum_safe_threshold)

    def test_area_map_entry_uses_skill_center_and_no_fixed_point(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        skill = MatchResult(0.97, (1760, 790), (44, 43), 0.88, 0.90)
        clicks = []
        navigator = object.__new__(Navigator)
        navigator.task = SimpleNamespace(
            info_set=lambda *_args: None,
            operate_click=lambda *args, **kwargs: self.fail(
                f"area-map entry must not use fixed point: {args}, {kwargs}"
            ),
        )
        navigator.vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: (
                self.assertIn(spec, (HAND_TEMPLATE, SANDBOX_TELEPORT_SKILL_TEMPLATE)) or skill
            ),
            passes=lambda result, spec: result is skill and spec is SANDBOX_TELEPORT_SKILL_TEMPLATE,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(
            mode=MapPageMode.UNKNOWN
        )
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX
        navigator._wait_for_current_sandbox = Mock(return_value=NavigationResult(
            True, ScreenState.SANDBOX,
        ))
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "技能已确认传送阵地图",
            map_page_mode=MapPageMode.GENERATE_TELEPORT,
        )

        result = navigator.ensure_area_map()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual(
            [(skill.center, frame.shape, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_area_map_context_reads_title_roi_and_confirmation_from_same_frame(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []

        class FakeVision:
            @staticmethod
            def simplify(value):
                return value

            @staticmethod
            def ocr_text(received, name, **kwargs):
                ocr_calls.append((received, name, kwargs.get("relative_roi")))
                return "卢戈森林深处"

            @staticmethod
            def match(_frame, _spec):
                return MatchResult(-1.0, (0, 0), (0, 0))

            @staticmethod
            def passes(_result, _spec):
                return False

            @staticmethod
            def threshold_for(spec):
                return spec.threshold

            @staticmethod
            def match_all(*_args, **_kwargs):
                return ()

        navigator = Navigator(SimpleNamespace(), FakeVision())
        navigator._detect_map_page_mode = lambda received: (
            self.assertIs(received, frame)
            or SimpleNamespace(
                mode=MapPageMode.DIRECT_TELEPORT,
                header_text="移动魔法阵",
            )
        )
        context = navigator._area_map_context(frame, CARD_BY_ID["Q_sp1"])

        self.assertTrue(context.is_area_map)
        self.assertEqual("卢戈森林深处", context.raw_text)
        self.assertEqual("移动魔法阵", context.confirmation_text)
        self.assertEqual(CollectionMapRole.BATTLE_AREA_2.value, context.resolved_target_key)
        self.assertEqual(1, len(ocr_calls))
        self.assertTrue(all(call[0] is frame for call in ocr_calls))
        self.assertEqual("传送阵地图名", ocr_calls[0][1])
        self.assertEqual(TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI, ocr_calls[0][2])

    def test_area_map_teleport_template_is_enabled_only_and_strict(self):
        self.assertIs(
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES[0],
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
        )
        self.assertEqual(1, len(TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES))
        spec = TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE
        self.assertEqual("传送阵地图传送阵", spec.name)
        self.assertEqual("image/green/TpCircleMapNewGE.png", spec.file_name)
        self.assertEqual(0.95, spec.threshold)
        self.assertEqual(0.90, spec.min_pixel_score)
        self.assertEqual(0.85, spec.min_zncc_score)
        self.assertEqual(0.95, spec.minimum_safe_threshold)

        path = ROOT / "recognition-assets/template-assets" / spec.file_name
        template = read_template_image(path)
        self.assertIsNotNone(template)
        self.assertEqual((50, 54, 3), template.shape)
        green = (template[:, :, 0] == 0) & (template[:, :, 1] == 255) & (template[:, :, 2] == 0)
        self.assertGreater(np.count_nonzero(green), 0)
        self.assertLess(np.count_nonzero(green), template.shape[0] * template.shape[1])

        skill_spec = TELEPORT_MAP_SKILL_TEMPLATE
        self.assertEqual("传送阵地图传送技能", skill_spec.name)
        self.assertEqual("image/green/TpSkillMapGE.png", skill_spec.file_name)
        self.assertEqual(0.95, skill_spec.threshold)
        self.assertEqual(0.90, skill_spec.min_pixel_score)
        self.assertEqual(0.85, skill_spec.min_zncc_score)
        self.assertEqual(0.95, skill_spec.minimum_safe_threshold)
        self.assertNotIn(skill_spec, TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES)

        skill_path = ROOT / "recognition-assets/template-assets" / skill_spec.file_name
        skill_template = read_template_image(skill_path)
        self.assertIsNotNone(skill_template)
        self.assertEqual((43, 43, 4), skill_template.shape)
        self.assertGreater(np.count_nonzero(skill_template[:, :, 3]), 0)
        self.assertLess(
            np.count_nonzero(skill_template[:, :, 3]),
            skill_template.shape[0] * skill_template.shape[1],
        )

        sandbox_skill = SANDBOX_TELEPORT_SKILL_TEMPLATE
        self.assertEqual("image/green/Skill3-4GE.png", sandbox_skill.file_name)
        self.assertNotIn(sandbox_skill, TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES)

    def test_teleport_map_teleports_reject_dim_candidate_before_click_context(self):
        frame = np.zeros((300, 500, 3), dtype=np.uint8)
        enabled = MatchResult(0.98, (80, 80), (52, 52), 0.94, 0.93)
        disabled = MatchResult(0.98, (280, 80), (52, 52), 0.94, 0.93)
        cv2.circle(frame, enabled.center, 16, (230, 230, 230), -1)
        cv2.circle(frame, disabled.center, 16, (100, 100, 100), -1)

        class FakeVision:
            @staticmethod
            def threshold_for(_spec):
                return 0.95

            @staticmethod
            def match_all(*_args, **_kwargs):
                return (enabled, disabled)

            @staticmethod
            def passes(_result, _spec):
                return True

        navigator = object.__new__(Navigator)
        navigator.task = SimpleNamespace(info_set=lambda *_args: None)
        navigator.vision = FakeVision()

        self.assertGreater(
            navigator._area_map_teleport_bright_neutral_ratio(frame, enabled),
            AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
        )
        self.assertLess(
            navigator._area_map_teleport_bright_neutral_ratio(frame, disabled),
            AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
        )
        self.assertEqual((enabled,), navigator._teleport_map_teleports(frame))
