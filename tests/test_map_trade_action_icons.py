"""Map-trade action_icons tests (split from test_map_trade.py)."""

import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS,
    ACTION_ICON_BRIGHT_CORE_GRAY,
    ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR,
    ACTION_ICON_CONSENSUS_ZNCC_FLOOR,
    ACTION_ICON_TEMPLATE_SCORE,
    ACTION_ICON_USED_MAX_BRIGHTNESS,
    ACTION_ICON_USED_ZNCC_SCORE,
    ACTION_ICON_ZNCC_SCORE,
    ACTION_ICONS,
    ACTION_SLOT_CENTER_RELATIVE_ROIS,
    ACTION_SLOT_CENTERS_REFERENCE,
    ACTION_SLOT_RELATIVE_ROIS,
    COOKING_ICON,
    COOKING_ICON_SCALE_RATIOS,
    SEARCH_ICON,
    SEARCH_ICON_TEMPLATE_SCORE,
    SUBDUE_ICON,
    SUMMON_ICON,
    ActionIconDetector,
    ActionIconState,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.map_trade.vision import Vision


class ActionIconTest(unittest.TestCase):
    @staticmethod
    def _detector(
        result: MatchResult,
        brightness: float,
    ) -> tuple[ActionIconDetector, list[int]]:
        brightness_calls = []

        def passes(candidate, spec):
            return candidate.score >= spec.threshold and candidate.zncc_score >= spec.min_zncc_score

        vision = SimpleNamespace(
            match=lambda _frame, _spec: result,
            passes=passes,
            template_brightness_ratio=lambda *_args, **kwargs: (
                brightness_calls.append(kwargs["minimum_template_gray"]) or brightness
            ),
        )
        return ActionIconDetector(vision), brightness_calls

    def test_action_icon_specs_use_green_templates_and_shape_identity_gates(self):
        self.assertEqual(6, len(ACTION_ICONS))
        self.assertEqual(
            {
                "SearchIcoGE.png",
                "AbsorbIcoGE.png",
                "SummonIcoGE.png",
                "SubdueIcoGE.png",
                "InteractIcoGE.png",
                "CookingIcoGE.png",
            },
            {Path(icon.template.file_name).name for icon in ACTION_ICONS},
        )
        for icon in ACTION_ICONS:
            with self.subTest(icon=icon.name):
                self.assertTrue(icon.template.file_name.startswith("image/green/"))
                self.assertEqual(
                    SEARCH_ICON_TEMPLATE_SCORE
                    if icon is SEARCH_ICON
                    else ACTION_ICON_TEMPLATE_SCORE,
                    icon.template.threshold,
                )
                self.assertEqual(
                    ACTION_ICON_USED_ZNCC_SCORE
                    if icon in (ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON)
                    else ACTION_ICON_ZNCC_SCORE,
                    icon.template.min_zncc_score,
                )
                if icon in (ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON):
                    self.assertEqual(ACTION_ICON_USED_ZNCC_SCORE, icon.available_min_zncc)
                self.assertIsNone(icon.template.min_pixel_score)
        for icon, slot in (
            (SEARCH_ICON, "search"),
            (ABSORB_ICON, "absorb"),
            (SUMMON_ICON, "summon"),
            (SUBDUE_ICON, "subdue"),
        ):
            with self.subTest(slot=slot):
                self.assertIsNone(icon.template.roi)
                self.assertEqual(ACTION_SLOT_RELATIVE_ROIS[slot], icon.template.relative_roi)
                self.assertEqual(
                    ACTION_SLOT_CENTER_RELATIVE_ROIS[slot],
                    icon.template.candidate_center_roi,
                )
                if icon in (ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON):
                    self.assertIsNotNone(icon.detection_template)
                    self.assertEqual(
                        ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR,
                        icon.detection_template.threshold,
                    )
                    self.assertEqual(
                        ACTION_ICON_CONSENSUS_ZNCC_FLOOR,
                        icon.detection_template.min_zncc_score,
                    )
        self.assertEqual(COOKING_ICON_SCALE_RATIOS, COOKING_ICON.template.scale_ratios)
        self.assertIn(1.40, COOKING_ICON.template.scale_ratios)

    def test_action_slot_rois_are_proportional_reference_calibrations(self):
        self.assertEqual(
            {
                "search": (1575, 994),
                "absorb": (1530, 880),
                "summon": (1577, 774),
                "subdue": (1682, 729),
                "teleport": (1795, 788),
            },
            ACTION_SLOT_CENTERS_REFERENCE,
        )
        for name, (left, top, right, bottom) in ACTION_SLOT_RELATIVE_ROIS.items():
            with self.subTest(slot=name):
                self.assertGreaterEqual(left, 0.0)
                self.assertGreaterEqual(top, 0.0)
                self.assertLessEqual(right, 1.0)
                self.assertLessEqual(bottom, 1.0)
                self.assertLess(right - left, 0.10)
                self.assertLess(bottom - top, 0.16)

    def test_marginal_correlated_metric_does_not_veto_limited_icon_identity(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        marginal = MatchResult(
            0.9499,
            (1510, 862),
            (44, 38),
            pixel_score=0.78,
            zncc_score=0.6387,
        )
        detector, _calls = self._detector(marginal, brightness=0.95)

        self.assertEqual(ActionIconState.AVAILABLE, detector.detect(frame, ABSORB_ICON).state)

    def test_consensus_rejects_zero_or_nan_pixel_evidence(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for pixel_score in (0.0, float("nan")):
            with self.subTest(pixel_score=pixel_score):
                result = MatchResult(
                    0.95,
                    (1510, 862),
                    (44, 38),
                    pixel_score=pixel_score,
                    zncc_score=0.51,
                )
                detector, _calls = self._detector(result, brightness=0.95)
                self.assertEqual(
                    ActionIconState.ABSENT,
                    detector.detect(frame, ABSORB_ICON).state,
                )

    def test_wrong_group_or_empty_slot_fails_absolute_identity_floor(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        wrong_group = MatchResult(
            0.934,
            (1510, 862),
            (44, 38),
            pixel_score=0.90,
            zncc_score=0.90,
        )
        detector, _calls = self._detector(wrong_group, brightness=0.95)

        self.assertEqual(ActionIconState.ABSENT, detector.detect(frame, ABSORB_ICON).state)

        empty = MatchResult(
            ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR + 0.01,
            (1510, 862),
            (44, 38),
            pixel_score=0.22,
            zncc_score=ACTION_ICON_CONSENSUS_ZNCC_FLOOR - 0.01,
        )
        detector, _calls = self._detector(empty, brightness=0.95)
        self.assertEqual(ActionIconState.ABSENT, detector.detect(frame, ABSORB_ICON).state)

    def test_search_countdown_template_is_a_negative(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        countdown = MatchResult(
            0.891,
            (1549, 970),
            (51, 50),
            pixel_score=0.759,
            zncc_score=0.461,
        )
        detector, _calls = self._detector(countdown, brightness=0.95)
        self.assertEqual(ActionIconState.ABSENT, detector.detect(frame, SEARCH_ICON).state)

    def test_action_slot_geometry_scales_at_all_supported_resolutions(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            with self.subTest(resolution=(width, height)):
                for name, relative in ACTION_SLOT_RELATIVE_ROIS.items():
                    left, top, region = Vision._relative_roi(frame, relative)
                    expected_x = ACTION_SLOT_CENTERS_REFERENCE[name][0] * width / 1920
                    expected_y = ACTION_SLOT_CENTERS_REFERENCE[name][1] * height / 1080
                    self.assertLessEqual(left, expected_x)
                    self.assertGreaterEqual(left + region.shape[1], expected_x)
                    self.assertLessEqual(top, expected_y)
                    self.assertGreaterEqual(top + region.shape[0], expected_y)

    def test_low_raw_pixel_does_not_reject_shape_confirmed_icons(self):
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
            detector.detect(frame, SEARCH_ICON).state,
        )
        self.assertEqual(
            ActionIconState.USED,
            detector.detect(frame, SUBDUE_ICON).state,
        )
        self.assertEqual(
            ActionIconState.AVAILABLE,
            detector.detect(frame, COOKING_ICON).state,
        )
        self.assertEqual(
            [ACTION_ICON_BRIGHT_CORE_GRAY] * 3,
            calls,
        )

    def test_dimmed_limited_icons_are_used_instead_of_absent(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        identity = MatchResult(
            0.974,
            (100, 100),
            (40, 40),
            pixel_score=0.71,
            zncc_score=0.823,
        )
        detector, _calls = self._detector(
            identity,
            brightness=ACTION_ICON_USED_MAX_BRIGHTNESS,
        )

        self.assertEqual(ActionIconState.USED, detector.detect(frame, ABSORB_ICON).state)
        self.assertEqual(ActionIconState.USED, detector.detect(frame, SUMMON_ICON).state)
        self.assertEqual(ActionIconState.USED, detector.detect(frame, SUBDUE_ICON).state)

    def test_bright_limited_icons_use_calibrated_structural_floor(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        relaxed_identity = MatchResult(
            0.98,
            (100, 100),
            (40, 40),
            pixel_score=0.72,
            zncc_score=ACTION_ICON_USED_ZNCC_SCORE,
        )

        dimmed, _calls = self._detector(
            relaxed_identity,
            ACTION_ICON_USED_MAX_BRIGHTNESS,
        )
        bright, _calls = self._detector(
            relaxed_identity,
            ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS,
        )

        self.assertEqual(ActionIconState.USED, dimmed.detect(frame, ABSORB_ICON).state)
        self.assertEqual(ActionIconState.AVAILABLE, bright.detect(frame, ABSORB_ICON).state)

    def test_limited_icon_brightness_has_used_unknown_and_available_bands(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        identity = MatchResult(
            0.98,
            (100, 100),
            (40, 40),
            pixel_score=0.90,
            zncc_score=0.90,
        )
        cases = (
            (ACTION_ICON_USED_MAX_BRIGHTNESS, ActionIconState.USED),
            (0.80, ActionIconState.UNKNOWN),
            (ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS, ActionIconState.AVAILABLE),
        )
        for brightness, expected in cases:
            with self.subTest(brightness=brightness):
                detector, _calls = self._detector(identity, brightness)
                self.assertEqual(expected, detector.detect(frame, ABSORB_ICON).state)

    def test_shape_failure_is_absent_and_skips_brightness_classification(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed_shape = MatchResult(
            ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR - 0.001,
            (100, 100),
            (40, 40),
            pixel_score=0.99,
            zncc_score=ACTION_ICON_CONSENSUS_ZNCC_FLOOR - 0.001,
        )
        detector, calls = self._detector(failed_shape, brightness=1.0)

        detection = detector.detect(frame, ABSORB_ICON)

        self.assertEqual(ActionIconState.ABSENT, detection.state)
        self.assertEqual([], calls)
