"""Low-resolution quick-switch lattice recovery regression tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from src.tasks.map_trade.models import MatchResult, NavigationResult, ScreenState
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.navigator_constants import (
    STORY_BADGE_GRID_MIN_MARGIN,
    STORY_BADGE_GRID_PIXEL_SCORE,
    STORY_BADGE_GRID_TEMPLATE_SCORE,
    STORY_BADGE_GRID_ZNCC_SCORE,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.vision import Vision
from tests.helpers.recognition_fixtures import load_bgr

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "map_trade" / "story_badge_grid"


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
    return load_bgr(FIXTURE_ROOT / name)


def _anchor(center_x: int, center_y: int, number: int = 1) -> StoryBadgeDetection:
    best = StoryBadgeCandidate(
        number,
        MatchResult(
            0.97,
            (center_x - 9, center_y - 9),
            (18, 18),
            pixel_score=0.92,
            zncc_score=0.80,
        ),
    )
    runner = StoryBadgeCandidate(
        20 if number != 20 else 19,
        MatchResult(
            0.90,
            (center_x - 9, center_y - 9),
            (18, 18),
            pixel_score=0.88,
            zncc_score=0.65,
        ),
    )
    return StoryBadgeDetection(best, runner)


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
                for row in engine.ocr(frame) if row
                for _polygon, (text, confidence) in row if confidence >= threshold
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
            (1329, 704), frame.shape, after_sleep=1.0,
        )
        navigator._scroll_cartridge_bar.assert_not_called()
        completion = navigator.card_status.detect(frame, (1329, 704))
        self.assertFalse(completion.complete_region)
        self.assertFalse(navigator._completion_satisfies_actions(completion))

    def test_native_720_q6_uses_slot_grid_after_strict_gate_rejects(self):
        frame = _load_fixture("native_720_q6_visible.png")
        navigator = self._navigator()
        base = navigator._story_badge_detections(frame)

        strict, strict_reason = navigator._find_story_badge_from_detections(frame, 6, base)
        recovered, reason = navigator._find_story_badge(frame, 6)

        self.assertIsNone(strict)
        self.assertIn("未达到角标严格或编码恢复门槛", strict_reason)
        self.assertEqual("", reason)
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual("slot_grid", recovered.recovery_mode)
        self.assertEqual(6, recovered.best.number)
        self.assertEqual((53, 615), recovered.best.result.position)
        self.assertEqual((18, 18), recovered.best.result.size)
        self.assertGreaterEqual(recovered.best.result.score, STORY_BADGE_GRID_TEMPLATE_SCORE)
        self.assertGreaterEqual(recovered.best.result.pixel_score, STORY_BADGE_GRID_PIXEL_SCORE)
        self.assertGreaterEqual(recovered.best.result.zncc_score, STORY_BADGE_GRID_ZNCC_SCORE)
        self.assertGreaterEqual(recovered.margin, STORY_BADGE_GRID_MIN_MARGIN)

    def test_reported_native_864_q6_uses_same_template_family(self):
        frame = _load_fixture("native_864_q6_visible.png")
        recovered, reason = self._navigator()._find_story_badge(frame, 6)

        self.assertEqual("", reason)
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual("slot_grid", recovered.recovery_mode)
        self.assertEqual((784, 738), recovered.best.result.position)
        self.assertEqual((22, 22), recovered.best.result.size)
        self.assertGreaterEqual(recovered.margin, STORY_BADGE_GRID_MIN_MARGIN)

    def test_slot_grid_does_not_invent_q6_in_later_720_viewport(self):
        frame = _load_fixture("native_720_q6_absent.png")
        detection, reason = self._navigator()._find_story_badge(frame, 6)

        self.assertIsNone(detection)
        self.assertIn("未达到角标严格或编码恢复门槛", reason)

    def test_low_margin_grid_identity_remains_rejected(self):
        frame = _load_fixture("native_720_q6_absent.png")
        detection, reason = self._navigator()._find_story_badge(frame, 12)

        self.assertIsNone(detection)
        self.assertIn("候选分差不足", reason)

    def test_shared_inspection_recovers_q6_once_and_keeps_other_targets_strict(self):
        frame = _load_fixture("native_720_q6_visible.png")
        inspected = self._navigator().inspect_story_badges(frame, (6, 18))

        q6, q6_reason = inspected[6]
        q18, q18_reason = inspected[18]
        self.assertEqual("", q6_reason)
        self.assertIsNotNone(q6)
        assert q6 is not None
        self.assertEqual("slot_grid", q6.recovery_mode)
        self.assertIsNone(q18)
        self.assertIn("未达到角标栅格恢复门槛", q18_reason)

    def test_grid_fit_requires_a_low_resolution_majority_lattice(self):
        navigator = self._navigator()
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        anchors = tuple(_anchor(62 + 120 * index, 624) for index in range(7))
        noisy = anchors + (_anchor(47, 601),)

        grid = navigator._story_badge_grid(frame, noisy)

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertAlmostEqual(120.0, grid.spacing)
        self.assertAlmostEqual(62.0, grid.phase)
        self.assertAlmostEqual(624.0, grid.center_y)
        self.assertEqual(7, grid.anchors)
        self.assertIsNone(navigator._story_badge_grid(frame, anchors[:-1]))
        self.assertIsNone(
            navigator._story_badge_grid(
                np.zeros((1080, 1920, 3), dtype=np.uint8),
                anchors,
            )
        )


if __name__ == "__main__":
    unittest.main()
