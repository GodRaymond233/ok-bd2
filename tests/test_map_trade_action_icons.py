"""Map-trade action_icons tests (split from test_map_trade.py)."""

import unittest
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    ACTION_ICON_BRIGHT_CORE_GRAY,
    COOKING_ICON,
    INTERACT_ICON,
    SEARCH_ICON,
    SUMMON_ICON,
    ActionIconDetector,
    ActionIconState,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.map_trade.vision import Vision
from src.utils.vision_models import MatchCandidateEvidence


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

    def test_consensus_rejects_zero_or_nan_pixel_evidence(self):
        for pixel_score in (0.0, float("nan")):
            with self.subTest(pixel_score=pixel_score):
                result = MatchResult(
                    0.95,
                    (1510, 862),
                    (44, 38),
                    pixel_score=pixel_score,
                    zncc_score=0.95,
                    gradient_zncc_score=0.95,
                    edge_score=0.95,
                )
                passed, _reason = ActionIconDetector._evidence_identity_passes(
                    result, ABSORB_ICON, margin=0.40, relative_contrast=0.95,
                )
                self.assertFalse(passed)

    def test_search_countdown_template_is_a_negative(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        countdown = MatchResult(
            0.891,
            (1549, 970),
            (51, 50),
            pixel_score=0.759,
            zncc_score=0.461,
            gradient_zncc_score=0.90,
            edge_score=0.90,
        )
        detector, _calls = self._detector(countdown, brightness=0.95)
        self.assertEqual(ActionIconState.ABSENT, detector.detect(frame, SEARCH_ICON).state)

    def test_search_translucent_background_requires_strong_shape_and_margin(self):
        observed = MatchResult(
            .929, (1550, 970), (51, 50), pixel_score=.749, zncc_score=.946,
            gradient_zncc_score=.816, edge_score=.962,
        )
        for changes, margin, expected in (
            ({}, .37, True), ({"score": .89}, .37, False),
            ({"zncc_score": .89}, .37, False),
            ({"gradient_zncc_score": .70}, .37, False),
            ({"edge_score": .85}, .37, False),
            ({"pixel_score": .60}, .37, False), ({}, .01, False), ({}, -1, False),
        ):
            with self.subTest(changes=changes, margin=margin):
                passed, _ = ActionIconDetector._evidence_identity_passes(
                    replace(observed, **changes), SEARCH_ICON, margin,
                )
                self.assertEqual(expected, passed)
        passed, _ = ActionIconDetector._evidence_identity_passes(
            observed, replace(ABSORB_ICON, slot_name="search"), .37,
        )
        self.assertFalse(passed)

    def test_generic_icons_use_template_identity_without_dimmed_state(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        low_pixel_shape_match = MatchResult(
            0.958,
            (100, 100),
            (40, 40),
            pixel_score=0.41,
            zncc_score=0.921,
        )
        detector, calls = self._detector(low_pixel_shape_match, brightness=0.20)

        self.assertEqual(
            ActionIconState.AVAILABLE,
            detector.detect(frame, INTERACT_ICON).state,
        )
        self.assertEqual(
            ActionIconState.AVAILABLE,
            detector.detect(frame, COOKING_ICON).state,
        )
        self.assertEqual(
            [ACTION_ICON_BRIGHT_CORE_GRAY] * 2,
            calls,
        )

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
