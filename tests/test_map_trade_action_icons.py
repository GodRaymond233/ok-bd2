"""Map-trade action_icons tests (split from test_map_trade.py)."""

import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    ACTION_ICON_BRIGHT_CORE_GRAY,
    ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR,
    ACTION_ICON_CONSENSUS_ZNCC_FLOOR,
    ACTION_ICON_TEMPLATE_SCORE,
    ACTION_ICON_USED_ZNCC_SCORE,
    ACTION_ICON_ZNCC_SCORE,
    ACTION_ICONS,
    ACTION_SLOT_CENTER_RELATIVE_ROIS,
    ACTION_SLOT_CENTERS_REFERENCE,
    ACTION_SLOT_RELATIVE_ROIS,
    COOKING_ICON,
    COOKING_ICON_SCALE_RATIOS,
    INTERACT_ICON,
    SEARCH_ICON,
    SEARCH_ICON_TEMPLATE_SCORE,
    SUBDUE_ICON,
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
