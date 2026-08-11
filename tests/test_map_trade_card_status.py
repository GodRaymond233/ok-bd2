"""Map-trade card_status tests (split from test_map_trade.py)."""

import unittest
from types import SimpleNamespace

import numpy as np

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
from src.tasks.map_trade.models import MatchResult


class CardStatusTest(unittest.TestCase):
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

    def test_card_icon_region_scales_both_axes_and_rejects_clipped_cards(self):
        self.assertEqual(
            ((85, 1015, 265, 1065), True),
            card_icon_region((100, 935), (1080, 1920, 3)),
        )
        self.assertEqual(
            ((57, 676, 177, 710), True),
            card_icon_region((67, 623), (720, 1280, 3)),
        )
        self.assertEqual(
            ((788, 767, 938, 808), True),
            card_icon_region((800, 700), (900, 1600, 3)),
        )
        self.assertEqual(
            ((1885, 1015, 1920, 1065), False),
            card_icon_region((1900, 935), (1080, 1920, 3)),
        )

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

    def test_card_status_detector_requires_one_exclusive_match_per_action(self):
        match = MatchResult(
            0.99,
            (100, 1020),
            (35, 35),
            pixel_score=0.97,
            zncc_score=0.97,
        )
        pending = {
            ABSORB_PENDING_TEMPLATE.file_name: (match,),
            SUPPRESS_PENDING_TEMPLATE.file_name: (match,),
        }
        completed = {
            ABSORB_COMPLETED_TEMPLATE.file_name: (match,),
            SUPPRESS_COMPLETED_TEMPLATE.file_name: (match,),
        }
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        pending_result = self._detector(pending).detect(frame, (100, 935))
        completed_result = self._detector(completed).detect(frame, (100, 935))

        self.assertEqual(CardActionState.PENDING, pending_result.absorb.state)
        self.assertEqual(CardActionState.PENDING, pending_result.suppress.state)
        self.assertEqual(CardActionState.PENDING, pending_result.state)
        self.assertEqual(CardActionState.COMPLETED, completed_result.absorb.state)
        self.assertEqual(CardActionState.COMPLETED, completed_result.suppress.state)
        self.assertEqual(CardActionState.COMPLETED, completed_result.state)

        ambiguous = {
            ABSORB_PENDING_TEMPLATE.file_name: (match,),
            ABSORB_COMPLETED_TEMPLATE.file_name: (match,),
            SUPPRESS_COMPLETED_TEMPLATE.file_name: (match, match),
        }
        ambiguous_result = self._detector(ambiguous).detect(frame, (100, 935))
        self.assertEqual(CardActionState.UNKNOWN, ambiguous_result.absorb.state)
        self.assertEqual(CardActionState.UNKNOWN, ambiguous_result.suppress.state)
        self.assertEqual(CardActionState.UNKNOWN, ambiguous_result.state)

    def test_card_status_color_thresholds_cannot_be_bypassed_by_gray_match(self):
        match = MatchResult(
            0.99,
            (100, 1020),
            (35, 35),
            pixel_score=0.97,
            zncc_score=0.97,
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        scenarios = (
            (
                ABSORB_PENDING_TEMPLATE,
                (0.119, 0.0, 0.80),
                "absorb",
            ),
            (
                ABSORB_COMPLETED_TEMPLATE,
                (0.0, 0.0, 0.779),
                "absorb",
            ),
            (
                SUPPRESS_PENDING_TEMPLATE,
                (0.0, 0.199, 0.80),
                "suppress",
            ),
            (
                SUPPRESS_COMPLETED_TEMPLATE,
                (0.0, 0.0, 0.849),
                "suppress",
            ),
        )

        for spec, ratios, attribute in scenarios:
            with self.subTest(spec=spec.file_name):
                result = self._detector(
                    {spec.file_name: (match,)},
                    {spec.file_name: ratios},
                ).detect(frame, (100, 935))
                self.assertEqual(
                    CardActionState.UNKNOWN,
                    getattr(result, attribute).state,
                )

    def test_card_status_detector_does_not_scan_a_clipped_region(self):
        vision = SimpleNamespace(
            threshold_for=lambda _spec: self.fail("clipped card must not match"),
            match_all=lambda *_args, **_kwargs: self.fail("clipped card must not match"),
        )

        result = CardStatusDetector(vision).detect(
            np.zeros((1080, 1920, 3), dtype=np.uint8),
            (1900, 935),
        )

        self.assertFalse(result.complete_region)
        self.assertEqual(CardActionState.UNKNOWN, result.absorb.state)
        self.assertEqual(CardActionState.UNKNOWN, result.suppress.state)
