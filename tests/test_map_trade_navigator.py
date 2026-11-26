"""Map navigation, field recovery and cartridge recognition."""

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
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
    TeleportScope,
)
from src.tasks.map_trade.navigator import (
    SANDBOX_SKILL_GROUP_PIXEL_SCORE,
    SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
    SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_CENTER_ROI,
    SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_STATE_TEMPLATES,
    STORY_BADGE_ENCODED_MIN_MARGIN,
    STORY_BADGE_ENCODED_PIXEL_SCORE,
    STORY_BADGE_ENCODED_TEMPLATE_SCORE,
    STORY_BADGE_ENCODED_ZNCC_SCORE,
    STORY_BADGE_MIN_MARGIN,
    STORY_BADGE_TEMPLATE_SCORE,
    Navigator,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.navigator_constants import (
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    AreaMapContext,
)
from src.tasks.map_trade.navigator_sandbox import TELEPORT_PAGE_ARROW_REFERENCE
from src.tasks.map_trade.vision import Vision
from tests.helpers import FakeClock
from tests.helpers.recognition_fixtures import load_bgr

ROOT = Path(__file__).resolve().parents[1]
NAV_CLOCK = "src.tasks.map_trade.navigator_sandbox.monotonic"


def frame_navigator(frame, clock=None, **vision):
    clock = clock or FakeClock()
    task = SimpleNamespace(
        config={}, info_set=Mock(), log_warning=Mock(), sleep=clock.sleep, operate_click=Mock()
    )
    return Navigator(task, SimpleNamespace(capture=lambda: frame, **vision))


class NavigationImageTest(unittest.TestCase):
    def test_quick_switch_button_identity(self):
        matcher = Vision(SimpleNamespace(config={}, info_set=Mock()))
        navigator = Navigator(matcher.task, matcher)
        fixtures = ROOT / "tests/fixtures/quick_switch"
        for scene in (2, 3):
            frame = load_bgr(
                fixtures / f"square_native_1280x738_{scene}.png",
            )
            self.assertFalse(Vision.evaluate_frame_geometry(frame).accepted)
            self.assertEqual((529, 684), navigator._find_quick_switch_button(frame).center)
            for bounds in ((1180, 5, 1230, 58), (170, 132, 212, 173)):
                missing = frame.copy()
                left, top, right, bottom = bounds
                missing[top:bottom, left:right] = 0
                self.assertIsNone(navigator._find_quick_switch_button(missing))
            wide = cv2.resize(frame, (1722, 738))
            self.assertIsNone(navigator._find_quick_switch_button(wide))
        for name, expected in (
            ("snow_town_button_fhd.png", (907, 999)),
            ("in_square_dark_button_1600x901.png", (662, 833)),
        ):
            source = load_bgr(fixtures / name)
            for scale in (2 / 3, 1.0, 4 / 3):
                for brightness in (-15, 0, 15):
                    with self.subTest(scene=name, scale=scale, brightness=brightness):
                        frame = cv2.resize(source, None, fx=scale, fy=scale)
                        frame = np.clip(
                            frame.astype(np.int16) + brightness,
                            0,
                            255,
                        ).astype(np.uint8)
                        found = navigator._find_quick_switch_button(frame)
                        self.assertIsNotNone(found)
                        self.assertAlmostEqual(expected[0] * scale, found.center[0], delta=4)
                        self.assertAlmostEqual(expected[1] * scale, found.center[1], delta=4)
        for name in (
            "home_bottom_strip_1600x901.png",
            "battle_gameplay_selected_fhd.png",
            "life_gameplay_selected_fhd.png",
        ):
            with self.subTest(scene=name):
                frame = load_bgr(fixtures / name)
                self.assertIsNone(navigator._find_quick_switch_button(frame))
        frame = load_bgr(fixtures / "snow_town_button_fhd.png")
        frame[970:1030, 580:650] = frame[970:1030, 880:950]
        self.assertIsNone(navigator._find_quick_switch_button(frame))

    def test_quick_switch_click_requires_stability_and_selector_confirmation(self):
        frame = load_bgr(ROOT / "tests/fixtures/quick_switch/snow_town_button_fhd.png")
        for page_ready in (False, True):
            with self.subTest(page_ready=page_ready):
                clicks = []
                task = SimpleNamespace(
                    config={},
                    capture_frame=lambda: frame,
                    sleep=lambda *_args: None,
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
        navigator._wait_for_current_sandbox = Mock(
            return_value=NavigationResult(
                False,
                ScreenState.UNKNOWN,
                "未稳定确认当前剧情卡带箱庭",
            )
        )
        navigator._find_quick_switch_button = Mock()
        self.assertFalse(navigator.open_cartridge_quick_switcher_from_sandbox().success)
        navigator._find_quick_switch_button.assert_not_called()
        self.assertEqual([], clicks)

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
                    self.assertAlmostEqual(
                        (117 + 180 * index) * scale, found.result.center[0], delta=5
                    )
                    absent = frame.copy()
                    absent[
                        :, round((90 + 180 * index) * scale) : round((250 + 180 * index) * scale)
                    ] = 0
                    self.assertIsNone(find_card(absent, card.number).result)

    def test_real_map_page_fixtures_are_mutually_exclusive_across_resolutions(self):
        fixtures = ROOT / "tests/fixtures/map_trade/map_pages"
        cases = {
            "direct_snow": ("移动魔法阵", "战斗Ⅰ 华特波尔雪山", MapPageMode.DIRECT_TELEPORT),
            "direct": ("移动魔法阵", "", MapPageMode.DIRECT_TELEPORT),
            "generate": ("移动魔法阵 传说", "", MapPageMode.GENERATE_TELEPORT),
            "sandbox_large": (
                "战斗Ⅱ 卢戈森林深处",
                "在战场中查看 探索 0 世界地图",
                MapPageMode.SANDBOX_LARGE_MAP,
            ),
            "direct_900": ("移动魔法阵", "", MapPageMode.DIRECT_TELEPORT),
            "direct_soft_1161": ("移动魔法阵", "安全 达雷普镇", MapPageMode.DIRECT_TELEPORT),
            "generate_beginner_811": (
                "移动魔法阵 初级",
                "安全 卢戈镇",
                MapPageMode.GENERATE_TELEPORT,
            ),
        }
        for name, (header, footer, expected) in cases.items():
            suffix = "webp" if name == "direct_snow" else "png"
            source = load_bgr(fixtures / f"{name}.{suffix}")
            sizes = (
                [(source.shape[1], source.shape[0])]
                if name
                in (
                    "direct_soft_1161",
                    "generate_beginner_811",
                )
                else [(height * 16 // 9, height) for height in (720, 900, 1080, 1440, 2160)]
            )
            for width, height in sizes:
                grades = (
                    ("初级", "新手", "进阶", "高手", "达人", "传说", "", "传说初级")
                    if (name == "generate_beginner_811")
                    else (None,)
                )
                for grade in grades:
                    with self.subTest(page=name, size=(width, height), grade=grade):
                        label_text = header if grade is None else f"移动魔法阵 {grade}"
                        frame = cv2.resize(
                            source,
                            (width, height),
                            interpolation=(
                                cv2.INTER_AREA if height < source.shape[0] else cv2.INTER_CUBIC
                            ),
                        )
                        matcher = Vision(SimpleNamespace(config={}))
                        navigator = Navigator(
                            SimpleNamespace(info_set=Mock()),
                            SimpleNamespace(
                                match=matcher.match,
                                passes=matcher.passes,
                                simplify=str,
                                ocr_text=lambda _frame, label, **_kwargs: (
                                    label_text if label == "地图页面左上标题" else footer
                                ),
                            ),
                        )
                        detection = navigator._detect_map_page_mode(frame)
                        self.assertEqual(expected, detection.mode)
                        if grade is not None:
                            scope = (
                                TeleportScope.CURRENT_CARTRIDGE
                                if grade == "传说"
                                else TeleportScope.UNKNOWN
                                if grade in ("", "传说初级")
                                else TeleportScope.CURRENT_MAP
                            )
                            self.assertEqual(scope, detection.teleport_scope)

    def test_teleport_arrows_require_the_correct_side_and_center(self):
        source = load_bgr(ROOT / "tests/fixtures/map_trade/map_pages/direct_snow.webp")
        matcher = Vision(SimpleNamespace(config={}))
        for height in (720, 900, 1080, 1440, 2160):
            width = height * 16 // 9
            frame = cv2.resize(
                source,
                (width, height),
                interpolation=(cv2.INTER_AREA if height < source.shape[0] else cv2.INTER_CUBIC),
            )
            for spec, expected_x in (
                (TELEPORT_MAP_FORWARD_TEMPLATE, 380),
                (TELEPORT_MAP_BACKWARD_TEMPLATE, 1540),
            ):
                with self.subTest(height=height, direction=spec.name):
                    match = matcher.match(frame, spec)
                    self.assertTrue(matcher.passes(match, spec))
                    self.assertAlmostEqual(expected_x, match.center[0] * 1920 / width, delta=4)
                    self.assertAlmostEqual(520, match.center[1] * 1080 / height, delta=4)
        swapped = source.copy()
        swapped[:, 310:450] = source[:, 1470:1610]
        swapped[:, 1470:1610] = source[:, 310:450]
        for frame in (np.zeros_like(source), swapped):
            for spec in (TELEPORT_MAP_FORWARD_TEMPLATE, TELEPORT_MAP_BACKWARD_TEMPLATE):
                with self.subTest(spec=spec.name, blank=not frame.any()):
                    self.assertFalse(matcher.passes(matcher.match(frame, spec), spec))

    def test_navigation_circle_requires_its_inner_glyph_at_client_scale(self):
        source = load_bgr(ROOT / "tests/fixtures/map_trade/map_pages/sandbox_large.png")
        for height in (720, 1080, 1440):
            with self.subTest(height=height):
                frame = cv2.resize(source, (height * 16 // 9, height))
                task = SimpleNamespace(config={}, info_set=Mock())
                navigator = Navigator(task, Vision(task))
                circle = navigator._sandbox_navigation_teleport(frame)
                self.assertIsNotNone(circle)
                self.assertLess(abs(circle.center[0] / frame.shape[1] - 384 / 1920), 0.005)
                self.assertLess(abs(circle.center[1] / height - 464 / 1080), 0.005)
                x, y = circle.position
                w, h = circle.size
                frame[y + h // 4 : y + h - h // 4, x + w // 4 : x + w - w // 4] = 90
                self.assertIsNone(navigator._sandbox_navigation_teleport(frame))

    def test_retreat_dialog_ocr_separates_title_from_confirm_button(self):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        from src.tasks.BaseBD2Task import BaseBD2Task
        from src.tasks.PVPTask import PVPTask
        from src.tasks.SquareGoddessTask import SquareGoddessTask

        engine = ONNXPaddleOcr(use_angle_cls=False, use_npu=False, use_openvino=True)
        source = load_bgr(
            Path(__file__).parent / "fixtures/map_trade/video_fhd/field_retreat_confirm.png",
        )
        for task_type in (BaseBD2Task, PVPTask, SquareGoddessTask):
            task = object.__new__(task_type)
            task._executor = SimpleNamespace(
                paused=False,
                debug=False,
                config={"ocr": {"default": {"lib": "onnxocr"}}},
                blocked_by_pause=lambda: False,
                ocr_lib=lambda _: engine,
                ocr_po_translation=None,
                text_fix={},
            )
            task.config = {}
            task.info_set = Mock()
            task.log_info = task.log_warning = Mock()
            task.log_debug = None
            task.ocr_default_threshold = 0.2
            task.ocr_target_height = 0
            vision = Vision(task)
            navigator = Navigator(task, vision)
            for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
                with self.subTest(task=task_type.__name__, size=(width, height)):
                    boxes = vision.ocr_boxes(
                        cv2.resize(source, (width, height)),
                        "采集战斗逃跑",
                        raise_on_error=True,
                    )
                    self.assertEqual(3, sum(box.name == "逃跑" for box in boxes))
                    stage, point = navigator._field_retreat_action(boxes)
                    self.assertEqual("confirm", stage)
                    self.assertAlmostEqual(1060 / 1920, point[0] / width, delta=0.006)
                    self.assertAlmostEqual(635 / 1080, point[1] / height, delta=0.006)


class SandboxIdentityTest(unittest.TestCase):
    def test_story_skill_group_requires_complementary_slots_on_one_frame(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        selected, second_off, second_on, first_off = SANDBOX_SKILL_STATE_TEMPLATES
        self.assertEqual(
            (
                SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
                SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
                SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
                SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
            ),
            SANDBOX_SKILL_STATE_TEMPLATES,
        )
        for spec, roi in (
            (selected, SANDBOX_SKILL_SLOT_1_CENTER_ROI),
            (second_on, SANDBOX_SKILL_SLOT_2_CENTER_ROI),
        ):
            self.assertEqual(roi, spec.candidate_center_roi)
        for spec in SANDBOX_SKILL_STATE_TEMPLATES:
            self.assertTrue(spec.green_mask)
            self.assertEqual(SANDBOX_SKILL_GROUP_TEMPLATE_SCORE, spec.threshold)
            self.assertEqual(SANDBOX_SKILL_GROUP_PIXEL_SCORE, spec.min_pixel_score)
            self.assertEqual(SANDBOX_SKILL_GROUP_TEMPLATE_SCORE, spec.minimum_safe_threshold)
        on, off, ambiguous = (0.55, 0.20, 0.90), (0.01, 0.90, 0.60), (0.15, 0.15, 0.80)
        for allowed, first, second, expected in (
            ([selected, second_off], on, off, 1),
            (SANDBOX_SKILL_STATE_TEMPLATES, on, off, 1),
            (SANDBOX_SKILL_STATE_TEMPLATES, off, on, 2),
            (SANDBOX_SKILL_STATE_TEMPLATES, ambiguous, ambiguous, None),
        ):
            with self.subTest(hits=len(allowed), first=first):
                match = Mock(return_value=MatchResult(0.99, (100, 100), (40, 40), 0.96, 0.90))
                nav = frame_navigator(
                    frame,
                    match=match,
                    passes=lambda _, spec: spec in allowed,
                    template_hsv_color_ratios=lambda _, spec, m: (
                        first if spec in (selected, first_off) else second
                    ),
                )
                _, hits, group = nav._sandbox_skill_group_evidence(frame, None)
                self.assertEqual((len(allowed), expected), (hits, group))
                self.assertEqual(
                    [(frame, spec) for spec in SANDBOX_SKILL_STATE_TEMPLATES],
                    [c.args for c in match.call_args_list],
                )

    def test_story_badge_scores_margin_and_ocr_must_agree(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[942:947, 93:98] = 255
        score, pixel, zncc = (
            STORY_BADGE_ENCODED_TEMPLATE_SCORE,
            STORY_BADGE_ENCODED_PIXEL_SCORE,
            STORY_BADGE_ENCODED_ZNCC_SCORE,
        )

        def badge(q, pos, p, z=0.0):
            return MatchResult(q, (pos, 930), (30, 28), pixel_score=p, zncc_score=z)

        good = badge(0.99, 80, 0.98, 0.90)
        weak = badge(0.80, 81, 0.82, 0.70)
        runner = badge(0.90, 81, 0.90, zncc - STORY_BADGE_ENCODED_MIN_MARGIN - 0.001)
        for label, targets, rivals, digit, rejected in (
            ("strict", [good], [weak], "", ""),
            ("ambiguous", [good], [badge(0.99, 81, 0.98, 0.90)], "", "候选分差不足"),
            ("low match", [badge(STORY_BADGE_TEMPLATE_SCORE - 0.01, 80, 0.99)], [weak], "", "门槛"),
            ("conflict", [good], [weak], "8", "角标OCR数字冲突"),
            (
                "duplicate",
                [good, badge(0.98, 480, 0.97, 0.80)],
                [weak, badge(0.79, 481, 0.81, 0.60)],
                "6",
                "",
            ),
            ("encoded", [badge(score, 80, pixel, zncc)], [runner], "", ""),
            ("match floor", [badge(score - 0.001, 80, pixel, zncc)], [runner], "", "编码恢复门槛"),
            ("pixel floor", [badge(score, 80, pixel - 0.001, zncc)], [runner], "", "编码恢复门槛"),
            (
                "structure floor",
                [badge(score, 80, pixel, zncc - 0.001)],
                [runner],
                "",
                "编码恢复门槛",
            ),
            (
                "structure margin",
                [badge(score, 80, pixel, zncc)],
                [badge(0.90, 81, 0.90, zncc - STORY_BADGE_ENCODED_MIN_MARGIN + 0.001)],
                "",
                "候选分差不足",
            ),
        ):
            with self.subTest(case=label):
                matches = {
                    "story_cartridge_badge_06.png": targets,
                    "story_cartridge_badge_08.png": rivals,
                }
                ocr = Mock(side_effect=lambda img, *a, **k: digit if img.max() > 200 else "")
                nav = frame_navigator(
                    frame,
                    match_all=lambda _, spec, **kw: matches.get(Path(spec.file_name).name, ()),
                    ocr_text=ocr,
                )
                detection, reason = nav._find_story_badge(frame, 6)
                if rejected:
                    self.assertIsNone(detection)
                    self.assertIn(rejected, reason)
                    if digit == "8":
                        self.assertIn("模板=6", reason)
                        self.assertIn("OCR=8", reason)
                    else:
                        ocr.assert_not_called()
                else:
                    self.assertEqual("", reason)
                    self.assertEqual((6, 8), (detection.best.number, detection.runner_up.number))
                    self.assertEqual(80, detection.best.result.position[0])
                    if digit:
                        self.assertEqual(6, detection.ocr_number)
                    if label == "strict":
                        self.assertGreaterEqual(detection.margin, STORY_BADGE_MIN_MARGIN)

    def test_grid_runner_needs_independent_digit_confirmation(self):
        grid = StoryBadgeDetection(
            best=StoryBadgeCandidate(8, MatchResult(0.9755, (407, 462), (14, 14), 0.9206, 0.8060)),
            runner_up=StoryBadgeCandidate(
                5, MatchResult(0.9767, (400, 462), (14, 14), 0.9204, 0.8136)
            ),
        )
        frame = np.zeros((540, 960, 3), np.uint8)
        frame[468:474, 403:409] = 255
        promoted = Navigator._story_badge_grid_runner_detections(5, (grid,))
        self.assertEqual(1, len(promoted))
        self.assertEqual(
            ("slot_grid_runner", 5), (promoted[0].recovery_mode, promoted[0].best.number)
        )
        for digit in ("5", ""):
            with self.subTest(ocr=digit):
                nav = frame_navigator(
                    frame, ocr_text=lambda img, *a, **k: digit if img.max() > 200 else ""
                )
                found, reason = nav._find_story_badge_from_detections(frame, 5, promoted)
                if digit:
                    self.assertEqual("", reason)
                    self.assertEqual(
                        ("slot_grid_runner", 5), (found.recovery_mode, found.ocr_number)
                    )
                else:
                    self.assertIsNone(found)
                    self.assertIn("角标次优候选OCR未确认", reason)

    def test_current_field_requires_two_consecutive_bright_frames(self):
        field, unknown = ScreenState.SANDBOX, ScreenState.UNKNOWN
        for states, brightness, first_capture, read_time, timeout, success, samples in (
            ((field, field), 0, 0, 0.1, 2.0, False, None),
            ((field, field), 100, 0, 0.1, 2.0, True, 2),
            ((field, unknown, field, field), 100, 0.0, 0.1, 3.0, True, 4),
            ((field, field), 100, 0.0, 1.498, 1.984, True, 2),
            ((field, field), 100, 4.206, 0.438, 3.0, True, 2),
            ((field, unknown), 100, 4.206, 0.438, 3.0, False, None),
            ((unknown,), 100, 4.206, 0.438, 3.0, False, 1),
            ((ScreenState.SANDBOX_MAP,), 100, 0.0, 0.1, 3.0, False, None),
            ((field, unknown, field, unknown), 100, 0.0, 0.6, 3.0, False, None),
        ):
            with self.subTest(states=states, brightness=brightness, first_capture=first_capture):
                clock = FakeClock()
                frames = []

                def capture():
                    if not frames:
                        clock.sleep(first_capture)
                    frames.append(np.full((8, 8, 3), brightness, np.uint8))
                    return frames[-1]

                def classify(_):
                    clock.sleep(read_time)
                    return states[min(len(frames) - 1, len(states) - 1)]

                task = SimpleNamespace(info_set=Mock(), sleep=clock.sleep, operate_click=Mock())
                nav = Navigator(task, SimpleNamespace(capture=capture))
                nav.classify = Mock(side_effect=classify)
                nav._auto_moving = Mock(return_value=False)
                with patch(NAV_CLOCK, clock):
                    result = nav._wait_for_current_sandbox(timeout=timeout)
                self.assertEqual(success, result.success)
                self.assertGreater(len(frames), 0)
                if samples is not None:
                    self.assertEqual(samples, len(frames))
                if success:
                    self.assertEqual(ScreenState.SANDBOX, result.state)
                else:
                    self.assertIn("连续箱庭=", result.message)
                self.assertLess(clock(), 9.0)
                task.operate_click.assert_not_called()


class RetreatScreen:
    def __init__(self, size, blocked, auto):
        self.width, self.height = size
        self.blocked = blocked
        self.auto = auto
        self.clock = FakeClock()
        self.state = "battle"
        self.clicks = []
        self.field_times = []
        self.frame = np.full((self.height, self.width, 3), 120, np.uint8)
        task = SimpleNamespace(
            config={"加载页面等待秒数": 10},
            info_set=Mock(),
            sleep=self.sleep,
            _handle_recent_cartridge_special_pages=Mock(return_value=None),
        )
        self.nav = Navigator(
            task,
            SimpleNamespace(
                capture=self.capture,
                click_client=self.click,
                ocr_boxes=self.boxes,
                ocr_text=lambda *a, **k: "TURN.1" if self.state == "battle" else "",
            ),
        )
        self.nav._black_frame = lambda _: self.state == "loading"
        self.nav.classify = self.classify
        self.nav._cancel_walk = Mock()
        self.nav._walk_text = self.walk_text
        self.nav._leave_walk_battle_result = Mock(return_value=False)
        self.nav._handle_story_card_intermediate = Mock(return_value=False)
        self.nav._auto_moving = Mock(return_value=False)

    def sleep(self, seconds):
        if self.blocked == "stop":
            raise TaskDisabledException()
        self.clock.sleep(seconds)

    def capture(self):
        if self.state == "loading" and self.blocked != "loading":
            self.state = "field"
        return self.frame

    def classify(self, _frame):
        if self.state == "field":
            self.field_times.append(self.clock())
            return ScreenState.SANDBOX
        return ScreenState.UNKNOWN

    def walk_text(self, _frame):
        if self.blocked == "field_unreadable":
            return None
        if self.blocked == "field_moving" and not self.nav._cancel_walk.called:
            return "自动移动中"
        return ""

    def box(self, name, x, y):
        return SimpleNamespace(
            name=name,
            x=x * self.width,
            y=y * self.height,
            width=0.05 * self.width,
            height=0.03 * self.height,
        )

    def boxes(self, *args, **kwargs):
        if self.state == "battle":
            return (
                [self.box("自动战斗中", 0.05, 0.86), self.box("结束自动战斗", 0.86, 0.9)]
                if self.auto
                else []
            )
        menu = [
            self.box("暂停", 0.47, 0.18),
            self.box("重新挑战", 0.46, 0.9),
            self.box("点击继续", 0.66, 0.9),
            self.box("逃跑", 0.26, 0.9),
        ]
        if self.state == "menu":
            return menu + ([menu[-1]] if self.blocked == "ambiguous" else [])
        if self.state != "dialog":
            raise AssertionError(f"unexpected OCR on {self.state}")
        dialog = [self.box("逃跑", 0.47, 0.39)]
        for missing, label, x, y in (
            ("missing_question", "是否放弃战斗逃跑？", 0.39, 0.49),
            ("missing_cancel", "取消", 0.42, 0.70 if self.blocked == "wrong_row" else 0.55),
            ("title_only", "逃跑", 0.58, 0.55),
        ):
            if self.blocked != missing:
                dialog.append(self.box(label, x, y))
        if self.blocked == "dialog_ambiguous":
            dialog.append(dialog[-1])
        return menu + dialog

    def click(self, point, _shape, *, after_sleep=0):
        stage, next_state = {
            "battle": ("pause", "menu"),
            "menu": ("escape", "dialog"),
            "dialog": ("confirm", "loading"),
        }[self.state]
        self.clicks.append((stage, point))
        if stage != self.blocked and sum(label == stage for label, _ in self.clicks) >= 2:
            self.state = next_state
        self.clock.sleep(after_sleep)


class NavigationRecoveryTest(unittest.TestCase):
    def test_retreat_requires_observed_transitions_and_unique_paired_buttons(self):
        for size in ((1920, 1080), (1280, 720)):
            for entry in ("field", "story", "recent", "auto"):
                for blocked, expected in (
                    (None, (2, 2, 2)),
                    ("field_moving", (2, 2, 2)),
                    ("pause", (3, 0, 0)),
                    ("escape", (2, 3, 0)),
                    ("confirm", (2, 2, 3)),
                    ("ambiguous", (2, 0, 0)),
                    ("dialog_ambiguous", (2, 2, 0)),
                    ("missing_cancel", (2, 2, 0)),
                    ("missing_question", (2, 2, 0)),
                    ("title_only", (2, 2, 0)),
                    ("wrong_row", (2, 2, 0)),
                    ("loading", (2, 2, 2)),
                    ("field_unreadable", (2, 2, 2)),
                    ("stop", (1, 0, 0)),
                ):
                    with self.subTest(size=size, entry=entry, blocked=blocked):
                        screen = RetreatScreen(size, blocked, entry == "auto")
                        nav = screen.nav

                        def recover():
                            if entry == "story":
                                return nav._wait_for_story_sandbox(14)
                            if entry == "recent":
                                return nav._recent_field_ready()
                            return nav.wait_for_collection_field(screen.frame)

                        with patch(NAV_CLOCK, screen.clock):
                            if blocked == "stop":
                                with self.assertRaises(TaskDisabledException):
                                    recover()
                            elif blocked in {None, "field_moving"}:
                                result = recover()
                                if entry == "story":
                                    self.assertTrue(result.success)
                                    self.assertEqual(ScreenState.SANDBOX, result.state)
                                elif entry == "recent":
                                    self.assertTrue(result)
                                else:
                                    self.assertIs(screen.frame, result)
                                self.assertEqual("field", screen.state)
                                self.assertEqual(3 if blocked else 2, len(screen.field_times))
                                self.assertLessEqual(screen.clock() - screen.field_times[0], 0.31)
                                self.assertEqual(int(bool(blocked)), nav._cancel_walk.call_count)
                            elif entry == "story":
                                result = recover()
                                self.assertFalse(result.success)
                                self.assertIn("遭遇战退出", result.message)
                            else:
                                with self.assertRaisesRegex(RuntimeError, "遭遇战退出"):
                                    recover()
                        width, height = size
                        points = (
                            ("pause", (round(width * 1792 / 1920), round(height * 52 / 1080))),
                            ("escape", Navigator._ocr_box_center(screen.box("逃跑", 0.26, 0.90))),
                            ("confirm", Navigator._ocr_box_center(screen.box("逃跑", 0.58, 0.55))),
                        )
                        self.assertEqual(
                            [p for p, count in zip(points, expected) for _ in range(count)],
                            screen.clicks,
                        )
                        self.assertLessEqual(screen.clock(), 31)

    def test_nav_menu_retries_only_fresh_destinations_until_movement(self):
        frame = np.full((720, 1280, 3), 120, np.uint8)
        for labels, outcomes, expected, points in (
            (["狩猎扬", "快速狩猎", "艾琳"], ["menu", "moving"], "狩猎场", 2),
            (["艾琳"], ["menu"] * 3, None, 3),
            (["艾琳"], ["unreachable"], None, 1),
            (["艾琳"], ["stuck"], None, 1),
            (["艾琳"], ["moving"], "艾琳", 1),
            (["快速狩猎"], [], None, 0),
            (["狩猎场", "狩猎场"], [], None, 0),
        ):
            with self.subTest(labels=labels, outcomes=outcomes):

                def boxes(_, label, roi, *, ocr_scale=1.0):
                    if ocr_scale == 1.0:
                        return []
                    return [
                        SimpleNamespace(
                            name=text,
                            x=300 + nav.vision.click_client.call_count,
                            y=100 + i * 40,
                            width=80,
                            height=20,
                        )
                        for i, text in enumerate(labels)
                    ]

                clock = FakeClock()
                nav = frame_navigator(
                    frame,
                    clock,
                    ocr_boxes=Mock(side_effect=boxes),
                    simplify=str,
                    click_template=Mock(return_value=True),
                    click_client=Mock(),
                )
                nav._hunting_ground_field = Mock(return_value=False)
                nav._restore_navigation_field = Mock(
                    return_value=NavigationResult(True, ScreenState.SANDBOX)
                )
                nav._confirm_travel = Mock(side_effect=outcomes)
                nav._wait_for_field_hud = Mock(
                    return_value=NavigationResult(True, ScreenState.SANDBOX)
                )
                nav._wait_auto_move_finished = Mock(return_value="done")
                with patch(NAV_CLOCK, clock):
                    self.assertEqual(expected, nav._travel_via_nav_menu("狩猎场", "艾琳"))
                self.assertEqual(
                    [(340 + i, 110) for i in range(points)],
                    [c.args[0] for c in nav.vision.click_client.call_args_list],
                )
                self.assertEqual(int(expected is not None), nav._wait_for_field_hud.call_count)
                self.assertGreaterEqual(
                    sum(
                        c.kwargs.get("ocr_scale") == 2.0
                        for c in nav.vision.ocr_boxes.call_args_list
                    ),
                    1,
                )

    def test_field_map_closure_retries_only_while_identity_is_fresh(self):
        large, unknown = MapPageMode.SANDBOX_LARGE_MAP, MapPageMode.UNKNOWN
        for modes, waits, read_time, expected, clicks in (
            ([large], [True], 0.322, True, 1),
            ([unknown], [], 0, False, 0),
            ([large, large], [False, False], 0, False, 2),
            ([large, large], [False, True], 0, True, 2),
            ([large, unknown], [False, True], 0, True, 1),
            ([large, unknown], [False, False], 0, False, 1),
            ([large], [], 8.1, False, 0),
        ):
            with self.subTest(modes=modes, waits=waits, read_time=read_time):
                clock = FakeClock()
                nav = frame_navigator(np.full((8, 8, 3), 100, np.uint8), clock)
                observations = iter(modes)

                def detect(_):
                    clock.sleep(read_time)
                    return SimpleNamespace(mode=next(observations))

                nav._detect_map_page_mode = detect
                nav.classify = Mock(return_value=ScreenState.UNKNOWN)
                nav._click_map_return = Mock()
                nav._wait_for_current_sandbox = Mock(
                    side_effect=[NavigationResult(value, ScreenState.UNKNOWN) for value in waits]
                )
                with patch(NAV_CLOCK, clock):
                    self.assertEqual(expected, nav._close_field_map())
                self.assertEqual(clicks, nav._click_map_return.call_count)

    def test_portal_retry_waits_for_field_and_honors_cancellation(self):
        passed = NavigationResult(
            True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.DIRECT_TELEPORT
        )
        failed = NavigationResult(False, ScreenState.UNKNOWN, "直传页未确认")
        for waits, restore, success, events in (
            ([passed], True, True, []),
            ([failed, passed], True, True, ["restore", "click"]),
            ([failed] * 3, True, False, ["restore", "click", "restore", "click", "restore"]),
            ([failed], False, False, ["restore"]),
            ([TaskDisabledException()], True, None, []),
            ([FinishedException()], True, None, []),
        ):
            with self.subTest(waits=waits, restore=restore):
                seen = []
                nav = Navigator(SimpleNamespace(info_set=Mock()), SimpleNamespace())
                nav._wait_for_sandbox_map_open = Mock(side_effect=waits)
                nav._restore_navigation_field = lambda: (
                    seen.append("restore") or NavigationResult(restore, ScreenState.SANDBOX)
                )
                nav._click_sandbox_teleport_interaction = lambda: seen.append("click") or True
                if success is None:
                    with self.assertRaises(type(waits[0])):
                        nav._confirm_portal_interaction("交互")
                else:
                    self.assertEqual(success, nav._confirm_portal_interaction("交互").success)
                self.assertEqual(events, seen)

    def test_navigation_recovery_waits_for_a_confirmed_field_or_map(self):
        field = np.full((8, 8, 3), 100, np.uint8)
        illustrated_loading = np.full_like(field, 232)
        black = np.zeros_like(field)
        for destination in (ScreenState.SANDBOX, ScreenState.SANDBOX_MAP, None):
            for initial in (illustrated_loading, black):
                with self.subTest(destination=destination, black=initial is black):
                    clock = FakeClock()
                    nav = frame_navigator(initial, clock, click_client=Mock())
                    nav.vision.capture = lambda: field if destination and clock() >= 1 else initial
                    map_mode = MapPageMode.SANDBOX_LARGE_MAP
                    nav._detect_map_page_mode = lambda frame: SimpleNamespace(mode=(
                        map_mode if frame is field and destination == ScreenState.SANDBOX_MAP
                        else MapPageMode.UNKNOWN
                    ))
                    nav.classify = lambda frame: (
                        ScreenState.SANDBOX if frame is field else ScreenState.UNKNOWN
                    )
                    nav._auto_moving = Mock(return_value=False)
                    nav._close_confirmed_map_page = Mock(
                        return_value=NavigationResult(True, ScreenState.SANDBOX)
                    )

                    with patch(NAV_CLOCK, clock):
                        result = nav._restore_navigation_field()

                    self.assertEqual(destination is not None, result.success)
                    if destination == ScreenState.SANDBOX_MAP:
                        self.assertEqual(
                            {map_mode}, nav._close_confirmed_map_page.call_args.args[0],
                        )
                    else:
                        nav._close_confirmed_map_page.assert_not_called()
                    self.assertGreaterEqual(clock(), 1)
                    self.assertLessEqual(clock(), 4)
                    nav.vision.click_client.assert_not_called()

    def test_nightmare_winter_uses_skill_then_fast_travel_before_local_walk(self):
        card = CARD_BY_ID["Q_ep2"]
        target, current = card.targets[1:]
        arrived = NavigationResult(True, ScreenState.SANDBOX)
        failed = NavigationResult(False, ScreenState.UNKNOWN, "技能未到达目标")
        direct = NavigationResult(
            True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        skill = replace(direct, map_page_mode=MapPageMode.GENERATE_TELEPORT)
        for ground, skill_entry, skill_arrival, menu_arrival, expected in (
            (direct, skill, arrived, False, ["circle", "teleport"]),
            (failed, skill, arrived, False, ["circle", "skill", "teleport"]),
            (failed, failed, failed, True, ["circle", "skill", "狩猎场"]),
            (failed, skill, failed, True, ["circle", "skill", "teleport", "狩猎场"]),
            (failed, skill, failed, False,
             ["circle", "skill", "teleport", "狩猎场", "艾琳", "walk", "teleport"]),
        ):
            with self.subTest(expected=expected):
                navigator = frame_navigator(np.full((8, 8, 3), 100, np.uint8))
                navigator._detect_map_page_mode = Mock(
                    return_value=SimpleNamespace(mode=MapPageMode.UNKNOWN)
                )
                navigator._restore_navigation_field = Mock(return_value=arrived)
                navigator.current_collection_target = Mock(return_value=current.key)
                navigator._confirm_collection_arrival = Mock(return_value=arrived)
                navigator._collection_field_title = Mock(return_value=target.title)
                navigator._walk_to_circle_neighbour = Mock(return_value=None)
                events = []
                navigator.open_teleport_map_from_sandbox = lambda: events.append("circle") or ground
                navigator._open_teleport_skill = lambda _: events.append("skill") or skill_entry
                navigator._travel_via_nav_menu = lambda entry: events.append(entry) or (
                    entry if menu_arrival else None
                )
                navigator._walk_to_sandbox_teleport_interaction = lambda: (
                    events.append("walk") or direct
                )

                def teleport(_, __, opened):
                    events.append("teleport")
                    return skill_arrival if opened is skill else arrived

                navigator._teleport_collection_target = teleport
                self.assertTrue(navigator._navigate_collection_target(card, target).success)
                self.assertEqual(expected, events)

    def test_chapter_eighteen_recovers_the_circle_before_changing_maps(self):
        card = CARD_BY_ID["Q_sp18"]
        town, sanctuary, memory = card.targets
        opened = NavigationResult(
            True,
            ScreenState.AREA_MAP,
            map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        missing = NavigationResult(False, ScreenState.SANDBOX, "未识别交互入口")
        for local_ok, safe_after_walk in ((True, True), (False, True), (False, False)):
            with self.subTest(local=local_ok, safe=safe_after_walk):
                events = []
                navigator = Navigator(SimpleNamespace(info_set=Mock()), SimpleNamespace())
                navigator.open_teleport_map_from_sandbox = Mock(return_value=missing)
                navigator._restore_navigation_field = Mock(
                    side_effect=[
                        NavigationResult(True, ScreenState.SANDBOX),
                        NavigationResult(safe_after_walk, ScreenState.SANDBOX, "菜单未确认关闭"),
                        *[NavigationResult(True, ScreenState.SANDBOX)] * 4,
                    ]
                )
                navigator.current_collection_target = Mock(side_effect=[sanctuary.key, town.key])
                walk_results = iter([opened] if local_ok else [missing, opened])
                navigator._walk_to_sandbox_teleport_interaction = Mock(
                    side_effect=lambda: events.append("walk-circle") or next(walk_results)
                )
                navigator._travel_via_nav_menu = Mock(
                    side_effect=lambda entry: events.append(entry) or entry
                )
                navigator._collection_field_title = Mock(return_value=town.title)
                navigator._hunting_ground_field = Mock(return_value=False)
                navigator._walk_to_collection_map = Mock(
                    side_effect=lambda *args: (
                        events.append("town-exit") or NavigationResult(True, ScreenState.SANDBOX)
                    )
                )
                navigator._open_teleport_skill = Mock(return_value=missing)

                result = navigator._open_teleport_map_anywhere(card, target=memory)

                self.assertEqual(local_ok or safe_after_walk, result.success)
                self.assertEqual(
                    ["walk-circle", "艾琳", "town-exit", "walk-circle"]
                    if not local_ok and safe_after_walk
                    else ["walk-circle"],
                    events,
                )
                navigator._open_teleport_skill.assert_not_called()
                if local_ok:
                    navigator._travel_via_nav_menu.assert_not_called()
                elif safe_after_walk:
                    navigator._walk_to_collection_map.assert_called_once_with(card, town, sanctuary)
                else:
                    self.assertIn("本图回阵", result.message)
                    self.assertIn("菜单未确认关闭", result.message)
                    navigator._travel_via_nav_menu.assert_not_called()

    def test_existing_hunting_ground_requires_closed_map_and_same_frame_field_controls(self):
        for height in (720, 1080):
            frame = np.full((height, height * 16 // 9, 3), 120, dtype=np.uint8)
            for closed in (True, False):
                with self.subTest(height=height, closed=closed):
                    state = [MapPageMode.SANDBOX_LARGE_MAP]
                    events = []
                    task = SimpleNamespace(config={}, info_set=Mock(), operate_click=Mock())
                    vision = SimpleNamespace(
                        capture=lambda: frame,
                        simplify=str,
                        click_template=Mock(),
                        ocr_text=Mock(return_value="自动狩猎 M 离开"),
                    )
                    navigator = Navigator(task, vision)
                    navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(mode=state[0])
                    navigator.classify = lambda _frame: (
                        ScreenState.SANDBOX
                        if state[0] == MapPageMode.UNKNOWN
                        else ScreenState.UNKNOWN
                    )

                    def close(_frame, _mode):
                        events.append("close")
                        if closed:
                            state[0] = MapPageMode.UNKNOWN

                    navigator._click_map_return = close
                    navigator._wait_for_current_sandbox = Mock(
                        side_effect=lambda **_: NavigationResult(
                            closed,
                            ScreenState.SANDBOX if closed else ScreenState.UNKNOWN,
                        )
                    )
                    entry = navigator._travel_via_nav_menu("狩猎场", "艾琳")
                    self.assertEqual("狩猎场" if closed else None, entry)
                    self.assertEqual(["close"] * (1 if closed else 2), events)
                    vision.click_template.assert_not_called()
                    task.operate_click.assert_not_called()
                    if closed:
                        self.assertIs(frame, vision.ocr_text.call_args.args[0])
                    else:
                        vision.ocr_text.assert_not_called()
            for field, text, expected in (
                (True, "自动狩猎 M 离开", True),
                (True, "极难 更改 离开", True),
                (True, "自动狩猎", False),
                (True, "离开", False),
                (True, "旅馆 商店 快速狩猎", False),
                (False, "自动狩猎 离开", False),
            ):
                with self.subTest(height=height, field=field, text=text):
                    vision = SimpleNamespace(
                        capture=lambda: frame, simplify=str, ocr_text=Mock(return_value=text)
                    )
                    navigator = Navigator(SimpleNamespace(), vision)
                    navigator.classify = Mock(
                        return_value=(ScreenState.SANDBOX if field else ScreenState.UNKNOWN)
                    )
                    self.assertEqual(expected, navigator._hunting_ground_field())
                    navigator.classify.assert_called_once_with(frame)
                    if not field:
                        vision.ocr_text.assert_not_called()

    def test_hunting_ground_portal_walks_only_after_confirmed_field_and_preserves_failure(self):
        passed = NavigationResult(
            True,
            ScreenState.AREA_MAP,
            map_page_mode=MapPageMode.DIRECT_TELEPORT,
        )
        for opened, restored, walked, expected, walk_count in (
            (passed, True, passed, True, 0),
            (NavigationResult(False, ScreenState.UNKNOWN, "页面未知"), True, passed, False, 0),
            (NavigationResult(False, ScreenState.SANDBOX, "交互未确认"), False, passed, False, 0),
            (NavigationResult(False, ScreenState.SANDBOX, "交互未确认"), True, passed, True, 1),
            (
                NavigationResult(False, ScreenState.SANDBOX, "交互未确认"),
                True,
                NavigationResult(False, ScreenState.UNKNOWN, "目的地未确认"),
                False,
                1,
            ),
        ):
            with self.subTest(opened=opened, restored=restored, walked=walked):
                events = []
                navigator = Navigator(SimpleNamespace(), SimpleNamespace())
                navigator.open_teleport_map_from_sandbox = Mock(return_value=opened)
                navigator._restore_navigation_field = Mock(
                    side_effect=lambda: (
                        events.append("restore") or NavigationResult(restored, ScreenState.UNKNOWN)
                    )
                )
                navigator._walk_to_sandbox_teleport_interaction = Mock(
                    side_effect=lambda: events.append("walk") or walked
                )
                result = navigator._open_hunting_ground_portal()
                self.assertEqual(expected, result.success)
                self.assertEqual(
                    walk_count,
                    navigator._walk_to_sandbox_teleport_interaction.call_count,
                )
                if walk_count:
                    self.assertEqual(["restore", "walk"], events)
                    if not walked.success:
                        self.assertIn("交互未确认", result.message)
                        self.assertIn("目的地未确认", result.message)
                if opened.success or opened.state == ScreenState.UNKNOWN:
                    navigator._restore_navigation_field.assert_not_called()
        for signal in (TaskDisabledException, FinishedException):
            navigator.open_teleport_map_from_sandbox = Mock(side_effect=signal)
            navigator._walk_to_sandbox_teleport_interaction.reset_mock()
            with self.assertRaises(signal):
                navigator._open_hunting_ground_portal()
            navigator._walk_to_sandbox_teleport_interaction.assert_not_called()


class CollectionTravelTest(unittest.TestCase):
    def test_portal_walk_counts_distinct_patrol_catches_and_cancels_after_reload(self):
        for scenario, timeout, message in (
            ("success", 45, ""),
            ("patrol limit", 45, "巡逻驱逐"),
            ("timeout", 3.5, "自动移动超时"),
        ):
            with self.subTest(scenario=scenario):
                clock = FakeClock()
                field = np.full((8, 8, 3), 120, dtype=np.uint8)
                loading = np.full_like(field, 232)
                phases = ["moving", "caught", "caught", "loading", "caught", "moving"]
                phases += {
                    "success": ["idle"],
                    "patrol limit": ["caught", "loading", "loading", "moving"],
                    "timeout": ["moving", "loading", "loading", "moving"],
                }[scenario]

                def phase():
                    if nav.vision.click_client.called:
                        return "idle"
                    return phases[min(int(clock() * 2), len(phases) - 1)]

                def read_walk(frame, *_args, **_kwargs):
                    if frame is loading:
                        return ""
                    return {
                        "moving": "自动移动中：魔法阵",
                        "caught": "被监视型康德D02发现，已遭驱逐。自动移动中：魔法阵",
                    }.get(phase(), "")

                def cancel(_, __, *, after_sleep):
                    self.assertGreaterEqual(clock(), 4.5)
                    clock.sleep(after_sleep)

                nav = frame_navigator(
                    field, clock, click_client=Mock(side_effect=cancel), ocr_text=read_walk,
                    simplify=str,
                )
                nav.vision.capture = lambda: loading if phase() == "loading" else field
                nav.classify = lambda frame: (
                    ScreenState.UNKNOWN if frame is loading else ScreenState.SANDBOX
                )
                nav._select_sandbox_teleport_walk = Mock(return_value=True)
                nav._sandbox_navigation_page_has_keyword = Mock(return_value=False)
                nav._field_battle_visible = Mock(return_value=False)
                nav._click_sandbox_teleport_interaction = Mock(return_value=True)
                nav._confirm_portal_interaction = Mock(
                    return_value=NavigationResult(True, ScreenState.AREA_MAP)
                )

                with (
                    patch(NAV_CLOCK, clock),
                    patch(
                        "src.tasks.map_trade.navigator_sandbox.SANDBOX_NAVIGATION_WALK_TIMEOUT",
                        timeout,
                    ),
                ):
                    result = nav._walk_to_sandbox_teleport_interaction()

                self.assertEqual(scenario == "success", result.success)
                if message:
                    self.assertIn(message, result.message)
                    nav._click_sandbox_teleport_interaction.assert_not_called()
                    nav.vision.click_client.assert_called_once()
                    self.assertLess(clock(), 8)
                else:
                    nav._click_sandbox_teleport_interaction.assert_called_once()
                    nav.vision.click_client.assert_not_called()

    @staticmethod
    def _area_context(
        text: str,
        target_key: str | None = None,
        *,
        teleports: tuple[MatchResult, ...] = (),
        page_mode: MapPageMode = MapPageMode.DIRECT_TELEPORT,
    ) -> AreaMapContext:
        match = MatchResult(0.99, (100, 100), (30, 30), pixel_score=0.98)
        return AreaMapContext(
            frame_shape=(1080, 1920, 3),
            raw_text=text,
            normalized_text=text,
            map_page_mode=page_mode,
            candidate_target_keys=(target_key,) if target_key else (),
            resolved_target_key=target_key,
            left_arrow=None,
            right_arrow=None,
            teleports=teleports,
            overlap_arrow=None,
            back_button=match,
            confirmation_text=(
                "移动魔法阵 传说" if page_mode == MapPageMode.GENERATE_TELEPORT else "移动魔法阵"
            ),
            teleport_scope=TeleportScope.CURRENT_CARTRIDGE,
        )

    def test_circle_retries_share_a_budget_and_verify_arrival(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        white = MatchResult(0.98, (800, 400), (50, 50), 0.96, 0.94)
        purple = MatchResult(0.99, (1000, 600), (50, 50), 0.97, 0.96)
        page = replace(
            self._area_context(target.title, target.key, teleports=(white,)),
            skill_teleports=(purple,),
        )
        missing = replace(page, teleports=(), skill_teleports=())
        field = replace(missing, map_page_mode=MapPageMode.UNKNOWN)
        for scene, clicks, read_title, success in (
            ("last white", "wwww", True, True),
            ("purple", "wwwwp", True, True),
            ("ignored", "wwwwpppp", False, False),
            ("different page", "w", False, False),
            ("changed scope", "w", False, False),
            ("before first", "", False, False),
            ("disappeared", "w", False, False),
            ("reappears", "ww", True, True),
            ("wrong arrival", "w", True, False),
            ("still loading", "w", False, False),
            ("disabled", "w", False, False),
            ("finished", "w", False, False),
        ):
            with self.subTest(scene=scene):
                clock = FakeClock()
                nav = frame_navigator(np.zeros((8, 8, 3), np.uint8), clock)
                seen = []

                def click(match, *args, **kwargs):
                    seen.append(("w" if match.center[0] < 900 else "p", clock()))
                    return True

                def capture(_):
                    if scene in ("disabled", "finished"):
                        error = TaskDisabledException if scene == "disabled" else FinishedException
                        raise error()
                    if scene in ("before first", "changed scope"):
                        return replace(page, teleport_scope=TeleportScope.CURRENT_MAP)
                    if scene == "different page":
                        return replace(page, resolved_target_key=card.targets[2].key)
                    if scene == "disappeared" or scene == "reappears" and clock() < 1:
                        return missing
                    if (
                        (scene == "last white" and len(seen) == 4)
                        or (scene == "purple" and seen[-1][0] == "p")
                        or (scene == "reappears" and len(seen) == 2)
                        or scene in ("wrong arrival", "still loading")
                    ):
                        return field
                    return replace(
                        page, teleports=(replace(white, position=(800 + len(seen) % 2, 400)),)
                    )

                nav._capture_area_map_context = capture
                nav._click_teleport_map_destination = click
                landed = NavigationResult(scene != "still loading", ScreenState.UNKNOWN)
                nav._wait_for_story_sandbox = Mock(return_value=landed)
                actual = card.targets[2] if scene == "wrong arrival" else target
                nav._collection_field_title = Mock(return_value=actual.title)
                with patch(NAV_CLOCK, clock):
                    initial = missing if scene == "before first" else page
                    if scene in ("disabled", "finished"):
                        error = TaskDisabledException if scene == "disabled" else FinishedException
                        with self.assertRaises(error):
                            nav._click_collection_destination(card, target, initial)
                    else:
                        result = nav._click_collection_destination(card, target, initial)
                        self.assertEqual(success, result.success)
                self.assertEqual(clicks, "".join(k for k, _ in seen))
                self.assertEqual(int(read_title), nav._collection_field_title.call_count)
                if scene == "reappears":
                    self.assertGreaterEqual(seen[1][1], TELEPORT_MAP_TRAVEL_SETTLE_SECONDS)
                if scene == "disappeared":
                    self.assertAlmostEqual(TELEPORT_MAP_TRAVEL_SETTLE_SECONDS, clock(), delta=0.01)

    def test_named_collection_routes_verify_target_page_after_hunting_ground(self):
        left = MatchResult(0.99, (100, 500), (30, 30), 0.98)
        right = replace(left, position=(1500, 500))
        circle = MatchResult(0.99, (880, 450), (60, 60), 0.95, 0.93)
        for card_id, transit in (
            ("Q_sp5", "战斗 君主之墓"),
            ("Q_sp7", "战斗 蜥蜴人祭坛"),
            ("Q_sp10", "安全 埃兰旅馆"),
        ):
            for operation in ("initialize", "advance", "target absent"):
                with self.subTest(card=card_id, operation=operation):
                    card = CARD_BY_ID[card_id]
                    target = card.targets[0 if operation == "initialize" else 1]
                    pages = [
                        self._area_context(t.title, t.key, teleports=(circle,))
                        for t in card.targets
                        if t != target or operation != "target absent"
                    ]
                    pages.append(self._area_context(transit))
                    pages = [
                        replace(
                            p,
                            left_arrow=left if i else None,
                            right_arrow=right if i + 1 < len(pages) else None,
                        )
                        for i, p in enumerate(pages)
                    ]
                    position = len(pages) - 1
                    clicked = []

                    def click(point, _shape, **kwargs):
                        nonlocal position
                        if point in (left.center, TELEPORT_PAGE_ARROW_REFERENCE["left"]):
                            position = max(0, position - 1)
                        elif point in (right.center, TELEPORT_PAGE_ARROW_REFERENCE["right"]):
                            position = min(len(pages) - 1, position + 1)
                        else:
                            self.assertEqual(target.key, pages[position].resolved_target_key)
                            clicked.append(point)

                    clock = FakeClock()
                    nav = frame_navigator(np.zeros((8, 8, 3), np.uint8), clock, click_client=click)
                    mode = MapPageMode.DIRECT_TELEPORT
                    nav._detect_map_page_mode = Mock(return_value=SimpleNamespace(mode=mode))
                    nav.current_collection_target = Mock(return_value=None)
                    sandbox = NavigationResult(True, ScreenState.SANDBOX)
                    nav.ensure_small_minimap = Mock(return_value=True)
                    nav._click_sandbox_teleport_interaction = Mock(return_value=False)
                    opened = NavigationResult(True, ScreenState.AREA_MAP, map_page_mode=mode)
                    nav._open_teleport_map_anywhere = Mock(return_value=opened)
                    nav._restore_navigation_field = Mock(return_value=sandbox)
                    nav._walk_to_circle_neighbour = Mock(return_value=None)
                    missing = NavigationResult(False, ScreenState.SANDBOX)
                    nav._walk_to_sandbox_teleport_interaction = Mock(return_value=missing)
                    nav._capture_area_map_context = lambda _: (
                        self._area_context("", page_mode=MapPageMode.UNKNOWN)
                        if clicked
                        else pages[position]
                    )
                    nav._wait_for_story_sandbox = Mock(return_value=sandbox)
                    nav._confirm_collection_arrival = Mock(return_value=sandbox)
                    with patch(NAV_CLOCK, clock):
                        result = (
                            nav.prepare_collection_main_area(card_id, via_hunting_ground=True)
                            if operation == "initialize"
                            else nav.advance_collection_map(card_id, card.targets[0], target)
                        )
                    nav._open_teleport_map_anywhere.assert_called_once_with(card, target=target)
                    found = operation != "target absent"
                    self.assertEqual(found, result.success)
                    self.assertEqual([circle.center] if found else [], clicked)
                    self.assertEqual(
                        [(card, target)] if found else [],
                        [c.args for c in nav._confirm_collection_arrival.call_args_list],
                    )
                    self.assertLess(clock(), 60)

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
                    clock = FakeClock()

                    def phase():
                        return phases[min(int(clock() * 2), len(phases) - 1)]

                    task = SimpleNamespace(
                        config={},
                        info_set=Mock(),
                        sleep=clock.sleep,
                    )
                    vision = SimpleNamespace(
                        capture=lambda: black if phase() == "loading" else frame,
                        simplify=lambda text: text,
                        ocr_text=lambda *_args, **_kwargs: {
                            "moving": "自动移动中",
                            "caught": "被发现驱逐",
                            "encounter": "TURN.1",
                        }.get(phase(), ""),
                    )
                    navigator = Navigator(task, vision)
                    navigator.ensure_small_minimap = lambda: True
                    navigator.classify = lambda _frame=None: (
                        ScreenState.UNKNOWN
                        if phase() in {"battle", "encounter"}
                        else ScreenState.SANDBOX
                    )
                    navigator._click_walk_exit = Mock(return_value=True)
                    navigator._close_field_map = Mock(return_value=True)
                    navigator._leave_walk_battle_result = Mock(return_value=True)
                    navigator._retreat_from_field_battle = Mock(return_value=frame)
                    navigator._cancel_walk = Mock()
                    opened = []

                    def open_map():
                        opened.append(clock())
                        if len(opened) == 1:
                            return current.title
                        self.assertEqual("idle", phase(), "徒步停止前不能开图复核")
                        return target.title

                    navigator._open_field_map = open_map
                    with (
                        patch(NAV_CLOCK, clock),
                        patch("src.tasks.map_trade.navigator_sandbox.RESUMING_WALK_TIMEOUT", 4),
                        patch(
                            "src.tasks.map_trade.navigator_sandbox.SANDBOX_NAVIGATION_WALK_TIMEOUT",
                            4,
                        ),
                    ):
                        result = navigator._walk_to_collection_map(card, current, target)
                    self.assertEqual(name != "timeout", result.success)
                    self.assertEqual(1 if name == "timeout" else 2, len(opened))
                    if name == "timeout":
                        self.assertIn("徒步等待超时", result.message)
                        navigator._cancel_walk.assert_called_once()
                        self.assertLessEqual(clock(), 4.5)
                        navigator._close_field_map.assert_not_called()
                    if name == "battle":
                        navigator._leave_walk_battle_result.assert_called_once()
                    if name == "encounter":
                        navigator._retreat_from_field_battle.assert_called_once()

    def test_exit_question_requires_unique_paired_button_and_observed_close(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        question = SimpleNamespace(
            name="是否自动移动前往所选目的地？", x=600, y=400, width=400, height=40
        )
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
                    info_set=Mock(),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
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

                navigator = Navigator(
                    task,
                    SimpleNamespace(
                        capture=lambda: frame,
                        click_client=click,
                        ocr_boxes=read_boxes,
                        simplify=lambda text: text,
                    ),
                )
                navigator._exit_label_points = Mock(return_value=[(100, 200), (300, 400)])
                navigator._walk_text = lambda _frame: (
                    None
                    if mode == "unknown"
                    else ""
                    if dismissed[0] or mode == "no_dialog"
                    else "是否自动移动前往所选目的地"
                )
                navigator._field_map_header = lambda _frame=None: (
                    "" if mode in {"unknown", "no_dialog"} else "安全卢戈镇"
                )
                navigator.classify = Mock(
                    return_value=(
                        ScreenState.SANDBOX if mode == "no_dialog" else ScreenState.UNKNOWN
                    )
                )
                navigator._close_field_map = Mock(return_value=mode != "close_failed")
                with patch(
                    "src.tasks.map_trade.navigator_sandbox.monotonic", side_effect=lambda: clock[0]
                ):
                    result = navigator._click_walk_exit(
                        ("Q_sp1", "main_area", "battle_area_1"),
                        CARD_BY_ID["Q_sp1"].targets[1],
                        "安全卢戈镇",
                    )
                self.assertEqual(expected, result)
                self.assertEqual(["exit", *labels], clicked)
                self.assertEqual(close_count, navigator._close_field_map.call_count)

    def test_inventory_full_walk_confirms_once_and_stops_before_retry(self):
        for width, height, left, top in ((1920, 1080, 560, 80), (1280, 720, 373, 53)):
            for mode in (
                "paired",
                "missing_confirm",
                "ambiguous",
                "title_only",
                "body_only",
                "changed_pair",
            ):
                with self.subTest(size=(width, height), mode=mode):
                    frame = np.full((height, width, 3), 120, dtype=np.uint8)
                    clock, clicks = [0.0], []

                    def box(name):
                        return SimpleNamespace(
                            name=name,
                            x=width * 0.5 - left - 20,
                            y=height * 0.57 - top - 10,
                            width=40,
                            height=20,
                        )

                    def ocr(*, name, **_kwargs):
                        texts = ["无法移动", "消耗品背包需有空位，才能前往战斗区域。", "自动移动中"]
                        if mode == "title_only":
                            del texts[1]
                        elif mode == "body_only":
                            del texts[0]
                        if mode == "changed_pair" and name == "出口自动移动确认窗":
                            texts = []
                        texts += ["确认"] * (
                            0 if mode == "missing_confirm" else 2 if mode == "ambiguous" else 1
                        )
                        return [box(text) for text in texts]

                    task = SimpleNamespace(
                        config={},
                        info_set=Mock(),
                        log_warning=Mock(),
                        ocr=ocr,
                        capture_frame=lambda: frame,
                        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    )
                    task.operate_click = lambda x, y, after_sleep, **_kwargs: (
                        clicks.append((x, y)),
                        task.sleep(after_sleep),
                    )
                    navigator = Navigator(task, Vision(task))
                    navigator.classify = Mock(return_value=ScreenState.SANDBOX)
                    with patch(
                        "src.tasks.map_trade.navigator_sandbox.monotonic",
                        side_effect=lambda: clock[0],
                    ):
                        if mode in {"title_only", "body_only"}:
                            with patch.object(navigator, "_cancel_walk"):
                                self.assertEqual("timeout", navigator._wait_auto_move_finished(1))
                        else:
                            with self.assertRaisesRegex(RuntimeError, "消耗品背包已满.*清理背包"):
                                navigator._wait_auto_move_finished(30)
                            self.assertLessEqual(clock[0], 0.8)
                            task.log_warning.assert_called_once()
                    self.assertEqual(1 if mode == "paired" else 0, len(clicks))
                    if mode == "paired":
                        self.assertAlmostEqual(0.5, clicks[0][0])
                        self.assertAlmostEqual(0.57, clicks[0][1], delta=1 / height)

    def test_teleport_map_dispatch_requires_known_mode_and_generation_button(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        circle = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        for mode, labels, success, expected_points in (
            (MapPageMode.UNKNOWN, (), False, []),
            (MapPageMode.SANDBOX_LARGE_MAP, (), False, []),
            (MapPageMode.DIRECT_TELEPORT, (), True, [circle.center]),
            (
                MapPageMode.GENERATE_TELEPORT,
                ("生成魔法阵", "取消", "生成5"),
                True,
                [circle.center, (1100, 644)],
            ),
            (MapPageMode.GENERATE_TELEPORT, ("生成魔法阵", "取消"), False, [circle.center]),
            (
                MapPageMode.GENERATE_TELEPORT,
                ("生成魔法阵", "取消", "生成", "生成5"),
                False,
                [circle.center],
            ),
        ):
            with self.subTest(mode=mode, labels=labels):
                boxes = [
                    SimpleNamespace(name=t, x=990, y=620, width=220, height=48) for t in labels
                ]
                clock = FakeClock()
                nav = frame_navigator(
                    frame,
                    clock,
                    ocr_boxes=Mock(return_value=boxes),
                    simplify=str,
                    click_client=Mock(),
                )
                with patch(NAV_CLOCK, clock):
                    result = nav._click_teleport_map_destination(
                        circle, frame.shape, page_mode=mode
                    )
                self.assertEqual(success, result)
                self.assertEqual(
                    expected_points, [c.args[0] for c in nav.vision.click_client.call_args_list]
                )
                nav.task.operate_click.assert_not_called()
