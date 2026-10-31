"""Pixel-preserving full-HD tutorial-video regressions for map-trade vision."""

import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    ACTION_SLOT_CENTERS_REFERENCE,
    SUBDUE_ICON,
    SUMMON_ICON,
    ActionIconDetector,
    ActionIconState,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.vision import Vision
from tests.helpers.recognition_fixtures import load_bgr, load_fhd_bgr

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
            ("beach_skills_dimmed.png", (
                ActionIconState.USED, ActionIconState.USED, ActionIconState.AVAILABLE,
            )),
            ("beach_skills_compressed.png", (
                ActionIconState.USED, ActionIconState.USED, ActionIconState.AVAILABLE,
            )),
        )
        icons = (
            (ABSORB_ICON, "absorb"),
            (SUMMON_ICON, "summon"),
            (SUBDUE_ICON, "subdue"),
        )

        for fixture_name, expected_states in cases:
            source = load_fhd_bgr(FIXTURE_ROOT / fixture_name)
            widths = (1920,) if "compressed" in fixture_name else (1280, 1920, 2560, 3840)
            for width in widths:
                frame = cv2.resize(
                    source, (width, width * 9 // 16),
                    interpolation=cv2.INTER_AREA if width < 1920 else cv2.INTER_CUBIC,
                )
                for (icon, center_name), expected_state in zip(icons, expected_states, strict=True):
                    with self.subTest(fixture=fixture_name, width=width, action=icon.name):
                        detection = self.actions.detect(frame, icon)
                        self.assertEqual(expected_state, detection.state, detection.reason)
                        self._assert_near(
                            self, detection.match.center,
                            tuple(round(value * width / 1920)
                                  for value in ACTION_SLOT_CENTERS_REFERENCE[center_name]),
                        )

    def test_limited_actions_reject_other_skill_group_and_empty_slots(self) -> None:
        other_group = load_bgr(FIXTURE_ROOT / "sandbox_other_skill_group.png")
        self.assertIsNotNone(other_group)
        for frame in (other_group, np.full((1080, 1920, 3), 220, dtype=np.uint8)):
            for icon in (ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON):
                with self.subTest(shape=frame.shape, action=icon.name):
                    self.assertEqual(ActionIconState.ABSENT, self.actions.detect(frame, icon).state)
        frame = load_fhd_bgr(FIXTURE_ROOT / "beach_skills_dimmed.png")
        for error in (AttributeError, TypeError, ValueError):
            with self.subTest(error=error), patch.object(
                self.vision, "match_slot_evidence", side_effect=error("invalid evidence"),
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
            frame[y - 45:y + 45, x - 45:x + 45] = source[sy - 45:sy + 45, sx - 45:sx + 45]
            variants[name] = frame
        flipped = source.copy()
        flipped[y - 45:y + 45, x - 45:x + 45] = cv2.flip(
            source[y - 45:y + 45, x - 45:x + 45], 1,
        )
        variants["mirrored"] = flipped
        empty = source.copy()
        empty[y - 72:y + 73, x - 82:x + 83] = 160
        variants["empty"] = empty
        for name, source in variants.items():
            for width in (1280, 1920, 2560):
                with self.subTest(variant=name, width=width):
                    frame = cv2.resize(source, (width, width * 9 // 16))
                    detected = self.actions.detect(frame, ABSORB_ICON)
                    self.assertEqual(
                        ActionIconState.USED if name == "used" else ActionIconState.ABSENT,
                        detected.state, detected.reason,
                    )
                    self.assertFalse(detected.clickable)

    def test_limited_actions_keep_intermediate_contrast_unknown(self) -> None:
        source = load_fhd_bgr(FIXTURE_ROOT / "story_sandbox_ready.png")
        frame = (source.astype(np.float32) * 0.75).astype(np.uint8)
        for icon in (ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON):
            with self.subTest(action=icon.name):
                detection = self.actions.detect(frame, icon)
                self.assertEqual(ActionIconState.UNKNOWN, detection.state, detection.reason)
                self.assertFalse(detection.clickable)

    def test_story_sandbox_fixtures_confirm_skill_group_one(self) -> None:
        for fixture_name in (
            "story_sandbox_ready.png",
            "story_sandbox_used.png",
        ):
            with self.subTest(fixture=fixture_name):
                frame = load_fhd_bgr(FIXTURE_ROOT / fixture_name)
                _matches, hits, group = self.navigator._sandbox_skill_group_evidence(frame, None)
                self.assertGreaterEqual(hits, 2)
                self.assertEqual(1, group)

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


if __name__ == "__main__":
    unittest.main()
