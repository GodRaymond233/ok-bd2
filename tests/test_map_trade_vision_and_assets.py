from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    ACTION_SLOT_CENTERS_REFERENCE,
    SEARCH_ICON,
    SUBDUE_ICON,
    SUMMON_ICON,
    ActionIconDetector,
    ActionIconState,
)
from src.tasks.map_trade.card_status import (
    ABSORB_COMPLETED_TEMPLATE,
    ABSORB_PENDING_TEMPLATE,
    SUPPRESS_COMPLETED_TEMPLATE,
    SUPPRESS_PENDING_TEMPLATE,
    CardActionDetection,
    CardActionState,
    CardStatusDetector,
    StoryCardCompletion,
    card_icon_region,
)
from src.tasks.map_trade.data import SHOP_CARTRIDGE_BRIGHTNESS
from src.tasks.map_trade.models import (
    MatchResult,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.navigator_constants import (
    STORY_BADGE_GRID_MIN_MARGIN,
    STORY_BADGE_GRID_PIXEL_SCORE,
    STORY_BADGE_GRID_TEMPLATE_SCORE,
    STORY_BADGE_GRID_ZNCC_SCORE,
)
from src.tasks.map_trade.trader import BUY_CONFIRM_DIALOG_REGION
from src.tasks.map_trade.vision import (
    Vision,
    parse_used_limit,
)
from src.utils.image_utils import read_template_image, to_gray
from src.utils.template_resolution import offline_template_scale
from src.utils.vision_models import MatchCandidateEvidence
from tests.helpers.map_trade import FakeTask
from tests.helpers.recognition_fixtures import load_bgr, load_fhd_bgr

ROOT = Path(__file__).resolve().parents[1]


class VisionTest(unittest.TestCase):
    def test_story_cartridge_brightness_calibration_separates_selected_state(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        normal = to_gray(load_bgr(template_root / SHOP_CARTRIDGE_BRIGHTNESS.normal_template))
        unselected = to_gray(
            load_bgr(
                template_root / SHOP_CARTRIDGE_BRIGHTNESS.unselected_template,
            )
        )
        self.assertIsNotNone(normal)
        self.assertIsNotNone(unselected)

        correlation = cv2.matchTemplate(unselected, normal, cv2.TM_CCOEFF_NORMED)
        _minimum, score, _minimum_point, point = cv2.minMaxLoc(correlation)
        x, y = point
        aligned_unselected = unselected[y : y + normal.shape[0], x : x + normal.shape[1]]
        unselected_ratio = Vision.foreground_brightness_ratio(
            normal,
            aligned_unselected,
            minimum_reference_gray=SHOP_CARTRIDGE_BRIGHTNESS.foreground_min_gray,
        )
        normal_ratio = Vision.foreground_brightness_ratio(
            normal,
            normal,
            minimum_reference_gray=SHOP_CARTRIDGE_BRIGHTNESS.foreground_min_gray,
        )

        self.assertEqual((2, 3), point)
        self.assertGreater(score, 0.99)
        self.assertAlmostEqual(1.0, normal_ratio, places=6)
        self.assertAlmostEqual(
            SHOP_CARTRIDGE_BRIGHTNESS.unselected_reference_ratio,
            unselected_ratio,
            delta=0.02,
        )
        self.assertTrue(SHOP_CARTRIDGE_BRIGHTNESS.is_selected(normal_ratio))
        self.assertFalse(SHOP_CARTRIDGE_BRIGHTNESS.is_selected(unselected_ratio))

    def test_alpha_mask_preserves_opaque_green_in_color_measurements(self):
        template = np.zeros((2, 2, 4), dtype=np.uint8)
        template[0, 0] = (0, 255, 0, 255)
        template[:, :, 3] = ((255, 255), (255, 0))
        frame = np.array([[(0, 100, 0), (0, 0, 100)], [(80, 80, 80), (0, 255, 0)]], np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "color-mask.png"
            self.assertTrue(cv2.imwrite(str(path), template))
            with patch("src.tasks.map_trade.vision.TEMPLATE_DIR", Path(directory)):
                vision = Vision(FakeTask())
                spec = TemplateSpec("colors", path.name)
                _, mask = vision._load(spec)
                np.testing.assert_array_equal(mask, template[:, :, 3])
                ratios = vision.template_color_ratios(frame, spec, MatchResult(1, (0, 0), (2, 2)))
                np.testing.assert_allclose(ratios, [1 / 3] * 3)

    def test_ocr_scale_resizes_input_and_restores_box_coordinates(self):
        task = FakeTask()
        captured_shapes = []

        def ocr(**kwargs):
            captured_shapes.append(kwargs["frame"].shape)
            return [SimpleNamespace(name="确认", x=20, y=30, width=40, height=10)]

        task.ocr = ocr
        vision = Vision(task)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        left, top, region = Vision._relative_roi(frame, BUY_CONFIRM_DIALOG_REGION)

        boxes = vision.ocr_boxes(
            frame,
            "scaled-roi",
            relative_roi=BUY_CONFIRM_DIALOG_REGION,
            ocr_scale=2.0,
        )

        self.assertEqual((region.shape[0] * 2, region.shape[1] * 2, 3), captured_shapes[0])
        self.assertEqual(
            (left + 10, top + 15, 20, 5),
            (boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height),
        )

    def test_skill_count_parser(self):
        self.assertEqual((3, 5), parse_used_limit("3 / 5"))
        self.assertEqual((10, 10), parse_used_limit("次数 10:10"))
        self.assertIsNone(parse_used_limit("11/10"))
        self.assertIsNone(parse_used_limit("次数未知"))
        for text, limit, used, expected in (
            ("7/121", 21, 7, None),
            ("7|21", 21, 7, (7, 21)),
            ("7:21", 21, 7, (7, 21)),
            ("7121", 21, 7, (7, 21)),
            ("721", 21, None, (7, 21)),
            ("7175", 75, 7, (7, 75)),
            ("11775", 75, 11, None),
            ("1121", 21, None, None),
            ("1121", 21, 11, (11, 21)),
            ("1121", 21, 1, (1, 21)),
            ("2121", 21, None, None),
            ("2121", 21, 21, (21, 21)),
            ("2121", 21, 2, (2, 21)),
            ("121/121", 21, None, None),
            ("7/21 8/21", 21, 7, None),
            ("提示7121", 21, 7, None),
            ("2121", None, None, None),
            ("1Y21", 21, 1, (1, 21)),
            ("1V21", 21, None, (1, 21)),
            (" 1 y 21 ", 21, 1, (1, 21)),
            ("1v21", 21, 1, (1, 21)),
            ("1Y21", None, None, None),
            ("1Y21", 70, 1, None),
            ("1Y21", 21, 2, None),
            ("22Y21", 21, None, None),
            ("0Y0", 0, None, None),
            ("提示1Y21", 21, 1, None),
            ("1V21 extra", 21, 1, None),
            ("1Y21 2V21", 21, None, None),
            ("1X21", 21, 1, None),
        ):
            with self.subTest(text=text, limit=limit, used=used):
                self.assertEqual(expected, parse_used_limit(text, limit, used))


class FrameGeometryTest(unittest.TestCase):
    @staticmethod
    def _scene(width: int, height: int) -> np.ndarray:
        rng = np.random.default_rng(7)
        return rng.integers(40, 220, size=(height, width, 3), dtype=np.uint8)

    def test_supported_geometry_and_scene_edges(self):
        for width, height, scene, y, rect, accepted, reason in (
            (1280, 720, (1280, 720), 0, (0, 0, 1280, 720), True, ""),
            (1200, 800, (1200, 675), 62, (0, 62, 1200, 675), False, ""),
            (1956, 1100, (1886, 1100), 0, (0, 0, 1956, 1100), True, ""),
            (1000, 600, (1000, 600), 0, None, False, "画面比例不是16:9"),
            (800, 450, (800, 450), 0, None, False, "画面物理尺寸过小"),
        ):
            with self.subTest(width=width, height=height):
                frame = np.zeros((height, width, 3), np.uint8)
                frame[y : y + scene[1], : scene[0]] = self._scene(*scene)
                geometry = Vision.evaluate_frame_geometry(frame)
                self.assertEqual(accepted, geometry.accepted)
                if rect is not None:
                    self.assertEqual(rect, geometry.content_rect)
                    self.assertEqual(bool(y), geometry.has_black_bars)
                else:
                    self.assertIn(reason, geometry.rejection_reasons[0])


class ActionIconTest(unittest.TestCase):
    @staticmethod
    def _detector(
        result: MatchResult,
        brightness: float,
    ) -> tuple[ActionIconDetector, list[int]]:
        brightness_calls = []

        vision = Vision(SimpleNamespace(config={}))
        vision.match = lambda _frame, _spec: result
        vision.match_slot_evidence = lambda *_args, **_kwargs: (
            MatchCandidateEvidence(result=result, scale=1.0),
        )
        vision.template_brightness_ratio = lambda *_args, **kwargs: (
            brightness_calls.append(kwargs["minimum_template_gray"]) or brightness
        )
        return ActionIconDetector(vision), brightness_calls

    def test_search_translucent_background_requires_strong_shape_and_margin(self):
        observed = MatchResult(
            0.929,
            (1550, 970),
            (51, 50),
            pixel_score=0.749,
            zncc_score=0.946,
            gradient_zncc_score=0.816,
            edge_score=0.962,
        )
        for changes, margin, expected in (
            ({}, 0.37, True),
            ({"score": 0.89}, 0.37, False),
            ({"zncc_score": 0.89}, 0.37, False),
            ({"gradient_zncc_score": 0.70}, 0.37, False),
            ({"edge_score": 0.85}, 0.37, False),
            ({"pixel_score": 0.60}, 0.37, False),
            ({}, 0.01, False),
            ({}, -1, False),
        ):
            with self.subTest(changes=changes, margin=margin):
                passed, _ = ActionIconDetector._evidence_identity_passes(
                    replace(observed, **changes),
                    SEARCH_ICON,
                    margin,
                )
                self.assertEqual(expected, passed)
        passed, _ = ActionIconDetector._evidence_identity_passes(
            observed,
            replace(ABSORB_ICON, slot_name="search"),
            0.37,
        )
        self.assertFalse(passed)

    def test_dimmed_slot_evidence_accepts_low_pixel_only_with_confirmed_contrast(self):
        match = MatchResult(
            score=0.994,
            position=(1542, 752),
            size=(69, 45),
            pixel_score=0.644,
            zncc_score=0.951,
            gradient_zncc_score=0.861,
            edge_score=0.935,
        )
        passed, reason = ActionIconDetector._evidence_identity_passes(
            match,
            SUMMON_ICON,
            margin=0.40,
            relative_contrast=0.57,
        )
        self.assertTrue(passed, msg=f"Should pass identity for dimmed icon, got: {reason}")

        passed_bright, reason_bright = ActionIconDetector._evidence_identity_passes(
            match,
            SUMMON_ICON,
            margin=0.40,
            relative_contrast=0.95,
        )
        self.assertFalse(passed_bright)
        self.assertIn("局部像素证据过低", reason_bright)


class CardStatusTest(unittest.TestCase):
    def test_native_1368_completion_recovery_preserves_source_coordinates(self):
        root = Path(__file__).resolve().parents[1] / "tests/fixtures/map_trade"
        detector = CardStatusDetector(Vision(SimpleNamespace(config={})))
        for index in (0, 1):
            frame = cv2.imread(str(root / f"completion_native_1368_{index}.png"))
            result = detector.detect(frame, (66, 667 - index))
            self.assertEqual(CardActionState.COMPLETED, result.suppress.state)
            self.assertIn("参考视图720", result.suppress.reason)
            self.assertTrue(result.suppress.completed)
            for evidence in result.suppress.completed:
                x, y = evidence.result.center
                self.assertTrue(result.bounds[0] <= x <= result.bounds[2])
                self.assertTrue(result.bounds[1] <= y <= result.bounds[3])

    def test_suppression_completion_tolerates_small_scale_changes_without_confusing_icons(self):
        vision = Vision(SimpleNamespace(config={}))
        detector = CardStatusDetector(vision)
        template_root = Path(__file__).resolve().parents[1] / "recognition-assets/template-assets"

        for width, height in (
            (1280, 720),
            (1368, 769),
            (1600, 900),
            (1920, 1080),
            (2054, 1155),
            (2304, 1296),
            (2560, 1440),
        ):
            center = (round(width * 0.35), round(height * 0.75))
            bounds, _complete = card_icon_region(center, (height, width, 3))
            left = bounds[0] + round(width * 0.025)
            top = bounds[1] + round(height * 0.007)

            def render(spec, ratio=1.0, *, duplicate=False):
                frame = np.full((height, width, 3), 80, dtype=np.uint8)
                asset = read_template_image(template_root / spec.file_name)
                scale = round(offline_template_scale(spec.file_name, width, height) * ratio, 3)
                icon = cv2.resize(
                    asset[:, :, :3],
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
                )
                active = (
                    cv2.resize(
                        asset[:, :, 3],
                        None,
                        fx=scale,
                        fy=scale,
                        interpolation=cv2.INTER_NEAREST,
                    )
                    > 0
                )
                icon_height, icon_width = icon.shape[:2]
                for x in (left, left + round(width * 0.04)) if duplicate else (left,):
                    region = frame[top : top + icon_height, x : x + icon_width]
                    region[active] = icon[active]
                return frame

            for ratio in (0.98, 1.0, 1.02):
                with self.subTest(height=height, ratio=ratio):
                    result = detector.detect(render(SUPPRESS_COMPLETED_TEMPLATE, ratio), center)
                    self.assertEqual(CardActionState.COMPLETED, result.suppress.state)
                    self.assertEqual(1, len(result.suppress.completed))

            for name, frame, expected in (
                ("absorb", render(ABSORB_COMPLETED_TEMPLATE), CardActionState.UNKNOWN),
                ("pending", render(SUPPRESS_PENDING_TEMPLATE), CardActionState.PENDING),
                (
                    "duplicate",
                    render(SUPPRESS_COMPLETED_TEMPLATE, duplicate=True),
                    CardActionState.UNKNOWN,
                ),
                ("empty", np.full((height, width, 3), 80, dtype=np.uint8), CardActionState.UNKNOWN),
            ):
                with self.subTest(height=height, case=name):
                    self.assertEqual(expected, detector.detect(frame, center).suppress.state)

    @staticmethod
    def _detection(state):
        return CardActionDetection(state)

    @staticmethod
    def _detector(matches, ratios=None):
        default_ratios = {
            ABSORB_PENDING_TEMPLATE.file_name: (0.20, 0.0, 0.55),
            ABSORB_COMPLETED_TEMPLATE.file_name: (0.0, 0.0, 0.85),
            SUPPRESS_PENDING_TEMPLATE.file_name: (0.0, 0.50, 0.40),
            SUPPRESS_COMPLETED_TEMPLATE.file_name: (0.0, 0.0, 0.92),
        }
        if ratios is not None:
            default_ratios.update(ratios)
        vision = SimpleNamespace(
            threshold_for=lambda spec: spec.threshold,
            match_all=lambda _frame, spec, **_kwargs: matches.get(
                spec.file_name,
                (),
            ),
            template_color_ratios=lambda _frame, spec, _result: default_ratios[spec.file_name],
        )
        return CardStatusDetector(vision)

    def test_story_card_completion_uses_conservative_three_value_logic(self):
        expected = {
            (CardActionState.COMPLETED, CardActionState.COMPLETED): CardActionState.COMPLETED,
            (CardActionState.PENDING, CardActionState.COMPLETED): CardActionState.PENDING,
            (CardActionState.COMPLETED, CardActionState.PENDING): CardActionState.PENDING,
            (CardActionState.PENDING, CardActionState.UNKNOWN): CardActionState.PENDING,
            (CardActionState.UNKNOWN, CardActionState.PENDING): CardActionState.PENDING,
            (CardActionState.COMPLETED, CardActionState.UNKNOWN): CardActionState.UNKNOWN,
            (CardActionState.UNKNOWN, CardActionState.COMPLETED): CardActionState.UNKNOWN,
            (CardActionState.UNKNOWN, CardActionState.UNKNOWN): CardActionState.UNKNOWN,
        }
        for states, result in expected.items():
            with self.subTest(states=states):
                completion = StoryCardCompletion(
                    absorb=self._detection(states[0]),
                    suppress=self._detection(states[1]),
                    bounds=(0, 0, 1, 1),
                    complete_region=True,
                )
                self.assertEqual(result, completion.state)

    def test_card_status_requires_one_exclusive_color_consistent_match(self):
        match = MatchResult(0.99, (100, 1020), (35, 35), pixel_score=0.97, zncc_score=0.97)
        frame = np.zeros((1080, 1920, 3), np.uint8)
        pending = (ABSORB_PENDING_TEMPLATE, SUPPRESS_PENDING_TEMPLATE)
        complete = (ABSORB_COMPLETED_TEMPLATE, SUPPRESS_COMPLETED_TEMPLATE)
        scenarios = [
            (pending, {}, CardActionState.PENDING),
            (complete, {}, CardActionState.COMPLETED),
            ((pending[0], complete[0], complete[1], complete[1]), {}, CardActionState.UNKNOWN),
        ]
        for spec, ratios in (
            (pending[0], (0.119, 0, 0.80)),
            (complete[0], (0, 0, 0.779)),
            (pending[1], (0, 0.199, 0.80)),
            (complete[1], (0, 0, 0.849)),
        ):
            scenarios.append(((spec,), {spec.file_name: ratios}, CardActionState.UNKNOWN))
        for templates, ratios, expected in scenarios:
            with self.subTest(templates=templates, colors=ratios):
                matches = {s.file_name: (match,) * templates.count(s) for s in templates}
                result = self._detector(matches, ratios).detect(frame, (100, 935))
                self.assertEqual(
                    (expected,) * 3, (result.absorb.state, result.suppress.state, result.state)
                )


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "map_trade" / "video_fhd"


class _VideoFixtureTask:
    """Minimal task surface needed by real Vision and Navigator instances."""

    config = {"跑图跑商识图阈值": 0.2}

    def __init__(self) -> None:
        self.infos: list[tuple[object, ...]] = []

    def info_set(self, *args: object) -> None:
        self.infos.append(args)

    def ocr(self, **_kwargs: object) -> list[object]:
        return []


class MapTradeVideoRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.task = _VideoFixtureTask()
        self.vision = Vision(self.task)
        self.navigator = Navigator(self.task, self.vision)
        self.actions = ActionIconDetector(self.vision)

    @staticmethod
    def _assert_near(
        test: unittest.TestCase,
        actual: tuple[int, int],
        expected: tuple[int, int],
        tolerance: int = 10,
    ) -> None:
        test.assertLessEqual(abs(actual[0] - expected[0]), tolerance)
        test.assertLessEqual(abs(actual[1] - expected[1]), tolerance)

    def test_limited_actions_preserve_available_and_used_states(self) -> None:
        cases = (
            ("story_sandbox_ready.png", (ActionIconState.AVAILABLE,) * 3),
            ("story_sandbox_used.png", (ActionIconState.USED,) * 3),
            (
                "beach_skills_dimmed.png",
                (
                    ActionIconState.USED,
                    ActionIconState.USED,
                    ActionIconState.AVAILABLE,
                ),
            ),
            (
                "beach_skills_compressed.png",
                (
                    ActionIconState.USED,
                    ActionIconState.USED,
                    ActionIconState.AVAILABLE,
                ),
            ),
        )
        icons = (
            (ABSORB_ICON, "absorb"),
            (SUMMON_ICON, "summon"),
            (SUBDUE_ICON, "subdue"),
        )

        for fixture_name, expected_states in cases:
            source = load_fhd_bgr(FIXTURE_ROOT / fixture_name)
            if fixture_name in {"story_sandbox_ready.png", "story_sandbox_used.png"}:
                _, hits, group = self.navigator._sandbox_skill_group_evidence(source, None)
                self.assertGreaterEqual(hits, 2)
                self.assertEqual(1, group)
            widths = (1920,) if "compressed" in fixture_name else (1280, 1920, 2560, 3840)
            for width in widths:
                frame = cv2.resize(
                    source,
                    (width, width * 9 // 16),
                    interpolation=cv2.INTER_AREA if width < 1920 else cv2.INTER_CUBIC,
                )
                for (icon, center_name), expected_state in zip(icons, expected_states, strict=True):
                    with self.subTest(fixture=fixture_name, width=width, action=icon.name):
                        detection = self.actions.detect(frame, icon)
                        self.assertEqual(expected_state, detection.state, detection.reason)
                        self._assert_near(
                            self,
                            detection.match.center,
                            tuple(
                                round(value * width / 1920)
                                for value in ACTION_SLOT_CENTERS_REFERENCE[center_name]
                            ),
                        )

    def test_limited_actions_reject_other_skill_group_and_empty_slots(self) -> None:
        other_group = load_bgr(FIXTURE_ROOT / "sandbox_other_skill_group.png")
        self.assertIsNotNone(other_group)
        for frame in (other_group, np.full((1080, 1920, 3), 220, dtype=np.uint8)):
            for icon in (ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON):
                with self.subTest(shape=frame.shape, action=icon.name):
                    self.assertEqual(ActionIconState.ABSENT, self.actions.detect(frame, icon).state)
        source = load_fhd_bgr(FIXTURE_ROOT / "story_sandbox_ready.png")
        low_contrast = (source.astype(np.float32) * 0.75).astype(np.uint8)
        for icon in (ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON):
            detected = self.actions.detect(low_contrast, icon)
            self.assertEqual(ActionIconState.UNKNOWN, detected.state, detected.reason)
            self.assertFalse(detected.clickable)
        frame = load_fhd_bgr(FIXTURE_ROOT / "beach_skills_dimmed.png")
        for error in (AttributeError, TypeError, ValueError):
            with (
                self.subTest(error=error),
                patch.object(
                    self.vision,
                    "match_slot_evidence",
                    side_effect=error("invalid evidence"),
                ),
            ):
                detection = self.actions.detect(frame, ABSORB_ICON)
                self.assertEqual(ActionIconState.ABSENT, detection.state)
                self.assertFalse(detection.clickable)

    def test_used_absorb_on_bright_scene_keeps_identity_without_becoming_clickable(self):
        source = load_fhd_bgr(FIXTURE_ROOT / "city_absorb_used.png")
        variants = {"used": source}
        x, y = ACTION_SLOT_CENTERS_REFERENCE["absorb"]
        for name in ("summon", "subdue", "teleport"):
            sx, sy = ACTION_SLOT_CENTERS_REFERENCE[name]
            frame = source.copy()
            frame[y - 45 : y + 45, x - 45 : x + 45] = source[sy - 45 : sy + 45, sx - 45 : sx + 45]
            variants[name] = frame
        flipped = source.copy()
        flipped[y - 45 : y + 45, x - 45 : x + 45] = cv2.flip(
            source[y - 45 : y + 45, x - 45 : x + 45],
            1,
        )
        variants["mirrored"] = flipped
        empty = source.copy()
        empty[y - 72 : y + 73, x - 82 : x + 83] = 160
        variants["empty"] = empty
        for name, source in variants.items():
            for width in (1280, 1920, 2560):
                with self.subTest(variant=name, width=width):
                    frame = cv2.resize(source, (width, width * 9 // 16))
                    detected = self.actions.detect(frame, ABSORB_ICON)
                    self.assertEqual(
                        ActionIconState.USED if name == "used" else ActionIconState.ABSENT,
                        detected.state,
                        detected.reason,
                    )
                    self.assertFalse(detected.clickable)

    def test_teleport_map_keeps_two_enabled_candidates_and_selects_lower_left(self) -> None:
        frame = load_fhd_bgr(FIXTURE_ROOT / "teleport_map_multiple_enabled.png")

        candidates = self.navigator._teleport_map_teleports(frame)
        selected = self.navigator._select_map_teleport(candidates)

        self.assertEqual(2, len(candidates))
        self.assertIsNotNone(selected)
        assert selected is not None
        self._assert_near(self, selected.center, (834, 625), tolerance=6)

    def test_encoded_q5_badge_is_selected_but_q6_is_not(self) -> None:
        frame = load_fhd_bgr(FIXTURE_ROOT / "story_badge_05_encoded.png")

        q5, _q5_reason = self.navigator._find_story_badge(frame, 5)
        q6, _q6_reason = self.navigator._find_story_badge(frame, 6)

        self.assertIsNotNone(q5)
        assert q5 is not None
        self.assertEqual(5, q5.best.number)
        self._assert_near(self, q5.best.result.center, (1353, 936), tolerance=6)
        self.assertIsNone(q6)


GRID_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "map_trade" / "story_badge_grid"


class _FixtureTask:
    def __init__(self) -> None:
        self.config: dict[str, object] = {}
        self.status: dict[str, object] = {}

    @staticmethod
    def ocr(**_kwargs):
        return []

    def info_set(self, key: str, value) -> None:
        self.status[key] = value


def _load_fixture(name: str) -> np.ndarray:
    return load_bgr(GRID_FIXTURES / name)


class StoryBadgeGridTest(unittest.TestCase):
    @staticmethod
    def _navigator() -> Navigator:
        task = _FixtureTask()
        return Navigator(task, Vision(task))

    def test_video_badges_use_real_ocr_and_recheck_card_visibility_before_click(self):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        engine = ONNXPaddleOcr(use_angle_cls=False, use_npu=False, use_openvino=True)

        def ocr(frame, threshold, **_kwargs):
            return [
                SimpleNamespace(name=text, confidence=confidence)
                for row in engine.ocr(frame)
                if row
                for _polygon, (text, confidence) in row
                if confidence >= threshold
            ]

        frame = _load_fixture("video_810_q16_visible.png")
        task = _FixtureTask()
        task.ocr = ocr
        task.sleep = lambda _seconds: None
        task.log_warning = lambda *_args: None
        navigator = Navigator(task, Vision(task))
        navigator.vision.capture = lambda: frame
        navigator._scroll_cartridge_bar = Mock(
            side_effect=AssertionError("target is already visible"),
        )

        found = navigator._wait_for_story_badge_with_scroll(16)

        self.assertIsNotNone(found)
        self.assertEqual((1044, 704), found[1].best.result.center)
        self.assertEqual("16", found[1].ocr_text)
        self.assertEqual("ordered_slot", found[1].recovery_mode)
        self.assertIsNotNone(navigator._confirm_story_badge_before_click(*found))

        # Keep the same ordered slot but replace its digit with a real 18 badge.
        changed = frame.copy()
        changed[690:719, 1030:1059] = frame[690:719, 1300:1329]
        navigator.vision.capture = lambda: changed
        self.assertIsNone(navigator._confirm_story_badge_before_click(*found))
        navigator._scroll_cartridge_bar.assert_not_called()

        frame = _load_fixture("video_810_q20_at_end.png")
        navigator.vision.capture = lambda: frame
        navigator.classify = lambda: ScreenState.CARD_MENU
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_cartridge_category = lambda **_kwargs: True
        navigator.vision.click_client = Mock()
        navigator._wait_for_story_sandbox = Mock(
            return_value=NavigationResult(True, ScreenState.SANDBOX),
        )

        self.assertTrue(navigator.select_card("Q_sp20").success)
        navigator.vision.click_client.assert_called_once_with(
            (1329, 704),
            frame.shape,
            after_sleep=1.0,
        )
        navigator._scroll_cartridge_bar.assert_not_called()
        completion = navigator.card_status.detect(frame, (1329, 704))
        self.assertFalse(completion.complete_region)
        self.assertFalse(navigator._completion_satisfies_actions(completion))

    def test_native_grid_decodes_visible_slots_and_rejects_ambiguous_digits(self):
        for height, position, size in ((720, (53, 615), (18, 18)), (864, (784, 738), (22, 22))):
            with self.subTest(height=height):
                frame = _load_fixture(f"native_{height}_q6_visible.png")
                navigator = self._navigator()
                if height == 720:
                    strict, reason = navigator._find_story_badge_from_detections(
                        frame, 6, navigator._story_badge_detections(frame)
                    )
                    inspected = navigator.inspect_story_badges(frame, (6, 18))
                    q6, found_reason = inspected[6]
                    self.assertEqual("", found_reason)
                    self.assertIsNotNone(q6)
                    self.assertEqual("slot_grid", q6.recovery_mode)
                    self.assertIsNone(inspected[18][0])
                    self.assertIn("未达到角标栅格恢复门槛", inspected[18][1])
                    self.assertIsNone(strict)
                    self.assertIn("未达到角标严格或编码恢复门槛", reason)
                recovered, reason = navigator._find_story_badge(frame, 6)
                self.assertEqual("", reason)
                self.assertIsNotNone(recovered)
                self.assertEqual("slot_grid", recovered.recovery_mode)
                self.assertEqual(6, recovered.best.number)
                self.assertEqual(position, recovered.best.result.position)
                self.assertEqual(size, recovered.best.result.size)
                self.assertGreaterEqual(
                    recovered.best.result.score, STORY_BADGE_GRID_TEMPLATE_SCORE
                )
                self.assertGreaterEqual(
                    recovered.best.result.pixel_score, STORY_BADGE_GRID_PIXEL_SCORE
                )
                self.assertGreaterEqual(
                    recovered.best.result.zncc_score, STORY_BADGE_GRID_ZNCC_SCORE
                )
                self.assertGreaterEqual(recovered.margin, STORY_BADGE_GRID_MIN_MARGIN)
        frame = _load_fixture("native_720_q6_absent.png")
        for digit, expected_reason in ((6, "未达到角标严格或编码恢复门槛"), (12, "候选分差不足")):
            with self.subTest(absent=digit):
                found, reason = self._navigator()._find_story_badge(frame, digit)
                self.assertIsNone(found)
                self.assertIn(expected_reason, reason)
