"""Map-trade navigator tests (split from test_map_trade.py)."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.map_trade.models import (
    CARD_BY_ID,
    CollectionMapRole,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    AREA_MAP_BACK_TEMPLATE,
    AREA_MAP_OPEN_RELATIVE_POINT,
    AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
    HAND_TEMPLATE,
    QUICK_SWITCH_TEMPLATE,
    SANDBOX_MAP_SETTLE_SECONDS,
    SANDBOX_MAP_TELEPORT_TEMPLATE,
    SANDBOX_TELEPORT_SKILL_POLL_INTERVAL,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
    STORY_CATEGORY_POINT,
    TELEPORT_GENERATION_OCR_TIMEOUT,
    TELEPORT_INTERACTION_CLICK_DELAY,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_SKILL_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    AreaMapContext,
    Navigator,
    SandboxConfirmation,
)

ROOT = Path(__file__).resolve().parents[1]


class NavigatorTest(unittest.TestCase):
    @staticmethod
    def _area_context(
        text: str,
        target_key: str | None = None,
        *,
        left: bool = False,
        right: bool = False,
        candidate_keys: tuple[str, ...] | None = None,
        teleports: tuple[MatchResult, ...] = (),
    ) -> AreaMapContext:
        match = MatchResult(0.99, (100, 100), (30, 30), pixel_score=0.98)
        keys = (
            candidate_keys if candidate_keys is not None else ((target_key,) if target_key else ())
        )
        return AreaMapContext(
            frame_shape=(1080, 1920, 3),
            raw_text=f"移动魔法阵 {text}",
            normalized_text=f"移动魔法阵{text}",
            is_area_map=True,
            candidate_target_keys=keys,
            resolved_target_key=target_key if len(keys) == 1 else None,
            left_arrow=match if left else None,
            right_arrow=match if right else None,
            teleports=teleports,
            overlap_arrow=None,
            back_button=match,
        )

    def test_area_map_title_resolution_prefers_longest_nested_story_title(self):
        card = CARD_BY_ID["Q_sp1"]

        self.assertEqual(
            (CollectionMapRole.BATTLE_AREA_2.value,),
            Navigator._target_keys_in_text(
                card,
                "移动魔法阵卢戈森林深处",
            ),
        )

    def test_area_map_scan_skips_unknown_pages_and_confirms_target(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        contexts = iter(
            (
                self._area_context("额外安全图", left=True, right=True),
                self._area_context(
                    target.title,
                    target.key,
                    left=True,
                    right=True,
                ),
            )
        )
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator._move_area_map = lambda *_args: next(contexts)

        located, moved, reason = navigator._locate_collection_target(
            card,
            target,
            self._area_context("主城区外页", right=True),
        )

        self.assertTrue(moved)
        self.assertEqual("", reason)
        self.assertEqual(target.key, located.resolved_target_key)

    def test_area_map_scan_stops_on_ambiguous_target_title(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        ambiguous = self._area_context(
            "标题歧义",
            right=True,
            candidate_keys=(
                CollectionMapRole.BATTLE_AREA_1.value,
                CollectionMapRole.BATTLE_AREA_2.value,
            ),
        )
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())

        located, moved, reason = navigator._locate_collection_target(card, target, ambiguous)

        self.assertIsNone(located)
        self.assertFalse(moved)
        self.assertIn("多个目标", reason)

    def test_area_map_back_template_uses_recognition_center_without_external_roi(self):
        self.assertEqual("image/green/BackButGe.png", AREA_MAP_BACK_TEMPLATE.file_name)
        self.assertIsNone(AREA_MAP_BACK_TEMPLATE.roi)
        self.assertEqual(0.90, AREA_MAP_BACK_TEMPLATE.threshold)
        self.assertEqual(0.80, AREA_MAP_BACK_TEMPLATE.min_zncc_score)

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
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.UNKNOWN,
        )
        navigator._wait_for_collection_teleport_map = lambda _card: self._area_context(
            card.targets[0].title,
            card.targets[0].key,
            right=True,
        )
        navigator.return_teleport_map_to_sandbox = lambda number: (
            events.append(("return", number)) or NavigationResult(True, ScreenState.SANDBOX)
        )
        navigator._reset_collection_teleport_map_to_main = lambda *_args: self.fail(
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
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.UNKNOWN,
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
            [(teleport.center, (1080, 1920, 3), TELEPORT_MAP_TRAVEL_SETTLE_SECONDS)],
            clicks,
        )
        self.assertEqual([], generation_calls)
        self.assertEqual([(card.card_id, card.targets[0].key)], arrivals)

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
            ScreenState.UNKNOWN,
            "已点击箱庭5号传送阵技能",
            teleport_map_opened_by_skill=True,
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
        )
        navigator._move_area_map = lambda _card, _context, direction: (
            moves.append(direction)
            or self._area_context(
                target.title,
                target.key,
                left=True,
                teleports=(teleport,),
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
                self.assertEqual(0.95, spec.threshold)
                self.assertEqual(0.85, spec.min_pixel_score)
                self.assertEqual(0.90, spec.min_zncc_score)
                self.assertEqual(0.95, spec.minimum_safe_threshold)
                self.assertIsNone(spec.roi)
                self.assertIsNone(spec.relative_roi)

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
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "交互按钮已确认传送阵地图",
        )
        navigator._click_sandbox_teleport_skill = lambda: self.fail(
            "interaction route must not click the fifth skill"
        )

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertFalse(result.teleport_map_opened_by_skill)
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
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "技能已确认传送阵地图",
        )

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertTrue(result.teleport_map_opened_by_skill)
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
            detect_skill_failure=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertIn("魔法阵附近", result.message)
        self.assertTrue(navigator._sandbox_teleport_skill_failure_matches(result.message))

    def test_teleport_skill_failure_routes_to_walk_fallback_result(self):
        task = SimpleNamespace(info_set=lambda *_args: None)
        navigator = Navigator(task, SimpleNamespace())
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
            return NavigationResult(True, ScreenState.AREA_MAP, "已通过徒步回退")

        navigator._walk_to_sandbox_teleport_interaction = walk_fallback

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual([True], fallback_calls)
        self.assertFalse(result.teleport_map_opened_by_skill)
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
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)

        def match(received, spec):
            self.assertIs(received, frame)
            self.assertIs(spec, HAND_TEMPLATE)
            return hand

        vision = SimpleNamespace(
            capture=lambda: frame,
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
        navigator._click_sandbox_navigation_map = lambda: True
        navigator._sandbox_navigation_page_has_keyword = lambda _frame: True
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
        )

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
        task = SimpleNamespace(info_set=lambda *_args: None)
        vision = SimpleNamespace(click_client=lambda *_args, **_kwargs: None)
        navigator = Navigator(task, vision)
        card = CARD_BY_ID["Q_sp1"]
        missing = self._area_context(
            card.targets[1].title,
            card.targets[1].key,
            teleports=(),
        )
        result = navigator._click_collection_destination(card, card.targets[1], missing)
        self.assertFalse(result.success)
        self.assertIn("未识别到", result.message)

        selected = []
        navigator._click_teleport_map_destination = (
            lambda teleport, _shape, opened_by_skill=False: selected.append(teleport) or True
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )
        navigator._confirm_collection_arrival = lambda _card, _target: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )
        multiple = self._area_context(
            card.targets[1].title,
            card.targets[1].key,
            teleports=(weaker, stronger),
        )
        result = navigator._click_collection_destination(card, card.targets[1], multiple)
        self.assertTrue(result.success)
        self.assertEqual([stronger], selected)

    def test_collection_destination_fails_when_arrival_map_does_not_match(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        navigator = Navigator(
            SimpleNamespace(info_set=lambda *_args: None),
            SimpleNamespace(click_client=lambda *_args, **_kwargs: None),
        )
        navigator._click_teleport_map_destination = lambda *_args, **_kwargs: True
        navigator._wait_for_story_sandbox = lambda _number: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )
        navigator._confirm_collection_arrival = lambda _card, _target: NavigationResult(
            False,
            ScreenState.AREA_MAP,
            "到达后地图不符：目标=卢戈森林，实际=battle_area_2",
        )

        result = navigator._click_collection_destination(
            card,
            target,
            self._area_context(
                target.title,
                target.key,
                teleports=(teleport,),
            ),
        )

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertIn("到达后地图不符", result.message)

    def test_confirm_collection_arrival_checks_actual_area_map_title(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator.ensure_area_map = lambda: NavigationResult(
            True,
            ScreenState.AREA_MAP,
        )
        navigator._capture_area_map_context = lambda _card: self._area_context(
            card.targets[2].title,
            card.targets[2].key,
        )
        navigator._close_area_map = lambda _context: self.fail(
            "a mismatched arrival map must remain open for failure handling"
        )

        result = navigator._confirm_collection_arrival(card, target)

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertIn(target.title, result.message)
        self.assertIn(card.targets[2].key, result.message)

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
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_story_sandbox = lambda number: (
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
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
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

    def test_open_story_quick_switcher_from_sandbox_never_detours_through_home(self):
        fixed_clicks = []
        template_clicks = []
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: fixed_clicks.append((x, y, after_sleep)),
            open_cartridge_quick_switcher=lambda **_kwargs: self.fail(
                "sandbox route must not use the global-home entry"
            ),
        )
        vision = SimpleNamespace(
            click_stable_template=lambda spec, timeout, after_sleep: (
                template_clicks.append((spec, timeout, after_sleep)) or True
            ),
        )
        navigator = Navigator(task, vision)
        navigator.return_home = lambda: self.fail(
            "sandbox route must not return to the global home"
        )
        navigator._wait_for_current_sandbox = lambda: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True

        result = navigator.open_story_quick_switcher_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.CARD_MENU, result.state)
        self.assertEqual([(QUICK_SWITCH_TEMPLATE, 10.0, 1.0)], template_clicks)
        self.assertEqual([(*STORY_CATEGORY_POINT, 0.5)], fixed_clicks)

    def test_open_story_quick_switcher_from_sandbox_stops_before_click_when_unconfirmed(self):
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

        result = navigator.open_story_quick_switcher_from_sandbox()

        self.assertFalse(result.success)
        self.assertIn("未稳定确认", result.message)

    def test_open_story_quick_switcher_reuses_immediately_prior_sandbox_confirmation(self):
        template_clicks = []
        task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_stable_template=lambda spec, timeout, after_sleep: (
                template_clicks.append((spec, timeout, after_sleep)) or True
            ),
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_current_sandbox = lambda: self.fail(
            "the caller already confirmed the same sandbox"
        )
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True

        result = navigator.open_story_quick_switcher_from_sandbox(
            sandbox_already_confirmed=True,
        )

        self.assertTrue(result.success)
        self.assertEqual([(QUICK_SWITCH_TEMPLATE, 10.0, 1.0)], template_clicks)

    def test_current_sandbox_confirmation_requires_consecutive_frames(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
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
        navigator._match_story_sandbox_signals = lambda _frame: SandboxConfirmation(
            2,
            2,
            3,
            1,
        )

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
        navigator.classify = lambda: ScreenState.SANDBOX
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "技能已确认传送阵地图",
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
                if name == "区域地图确认":
                    return "移动魔法阵"
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
        context = navigator._area_map_context(frame, CARD_BY_ID["Q_sp1"])

        self.assertTrue(context.is_area_map)
        self.assertEqual("卢戈森林深处", context.raw_text)
        self.assertEqual("移动魔法阵", context.confirmation_text)
        self.assertEqual(CollectionMapRole.BATTLE_AREA_2.value, context.resolved_target_key)
        self.assertEqual(2, len(ocr_calls))
        self.assertTrue(all(call[0] is frame for call in ocr_calls))
        self.assertEqual(None, ocr_calls[0][2])
        self.assertEqual("传送阵地图名", ocr_calls[1][1])
        self.assertEqual(TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI, ocr_calls[1][2])

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
        template = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
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
        skill_template = cv2.imread(str(skill_path), cv2.IMREAD_UNCHANGED)
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
