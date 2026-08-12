"""Pixel-preserving full-HD tutorial-video regressions for map-trade vision."""

import unittest
from pathlib import Path

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
from tests.helpers.recognition_fixtures import load_fhd_bgr

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
            ("story_sandbox_ready.png", ActionIconState.AVAILABLE),
            ("story_sandbox_used.png", ActionIconState.USED),
        )
        icons = (
            (ABSORB_ICON, "absorb"),
            (SUMMON_ICON, "summon"),
            (SUBDUE_ICON, "subdue"),
        )

        for fixture_name, expected_state in cases:
            frame = load_fhd_bgr(FIXTURE_ROOT / fixture_name)
            for icon, center_name in icons:
                with self.subTest(fixture=fixture_name, action=icon.name):
                    detection = self.actions.detect(frame, icon)
                    self.assertEqual(expected_state, detection.state)
                    self._assert_near(
                        self,
                        detection.match.center,
                        ACTION_SLOT_CENTERS_REFERENCE[center_name],
                    )

    def test_story_sandbox_fixtures_confirm_group_one(self) -> None:
        for fixture_name in (
            "story_sandbox_ready.png",
            "story_sandbox_used.png",
        ):
            with self.subTest(fixture=fixture_name):
                confirmation = self.navigator._match_story_sandbox_signals(
                    load_fhd_bgr(FIXTURE_ROOT / fixture_name)
                )

                self.assertTrue(confirmation.passed)
                self.assertEqual(1, confirmation.skill_group)

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
