"""Map-trade sandbox tests (split from test_map_trade.py)."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    SUMMON_ICON,
)
from src.tasks.map_trade.models import (
    MatchResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    SANDBOX_SKILL_GROUP_PIXEL_SCORE,
    SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
    SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO,
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
    SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_CENTER_ROI,
    SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_STATE_TEMPLATES,
    SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO,
    SANDBOX_TEMPLATES,
    STORY_BADGE_ENCODED_MIN_MARGIN,
    STORY_BADGE_ENCODED_PIXEL_SCORE,
    STORY_BADGE_ENCODED_TEMPLATE_SCORE,
    STORY_BADGE_ENCODED_ZNCC_SCORE,
    STORY_BADGE_MIN_MARGIN,
    STORY_BADGE_PIXEL_SCORE,
    STORY_BADGE_TEMPLATE_SCORE,
    Navigator,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from tests.helpers import FakeClock

ROOT = Path(__file__).resolve().parents[1]


class SandboxConfirmationTest(unittest.TestCase):

    def test_sandbox_skill_group_templates_use_strict_slot_specific_gates(self):
        self.assertEqual(
            (
                SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
                SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
                SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
                SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
            ),
            SANDBOX_SKILL_STATE_TEMPLATES,
        )
        for spec in SANDBOX_SKILL_STATE_TEMPLATES:
            with self.subTest(spec=spec.name):
                self.assertTrue(spec.green_mask)
                self.assertEqual(SANDBOX_SKILL_GROUP_TEMPLATE_SCORE, spec.threshold)
                self.assertEqual(SANDBOX_SKILL_GROUP_PIXEL_SCORE, spec.min_pixel_score)
                self.assertIsNone(spec.min_zncc_score)
                self.assertEqual(
                    SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
                    spec.minimum_safe_threshold,
                )
        self.assertEqual(
            SANDBOX_SKILL_SLOT_1_CENTER_ROI,
            SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE.candidate_center_roi,
        )
        self.assertEqual(
            SANDBOX_SKILL_SLOT_2_CENTER_ROI,
            SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE.candidate_center_roi,
        )
        self.assertEqual(
            "image/green/SandboxSkillSlot1AvailableGE.png",
            SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE.file_name,
        )
        self.assertEqual(
            "image/green/SandboxSkillSlot2UsedGE.png",
            SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE.file_name,
        )
        self.assertLess(
            SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO,
            SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO,
        )

    def test_story_sandbox_skill_group_uses_color_when_all_templates_match(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        passed_specs = {
            SANDBOX_TEMPLATES[0],
            *SANDBOX_SKILL_STATE_TEMPLATES,
            ABSORB_ICON.template,
            SEARCH_ICON.template,
            SUMMON_ICON.template,
        }
        result = MatchResult(0.99, (100, 100), (40, 40), 0.96, 0.90)
        selected_slot_specs = {
            SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
            SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
        }
        vision = SimpleNamespace(
            match=lambda _frame, _spec: result,
            passes=lambda _result, spec: spec in passed_specs,
            template_hsv_color_ratios=lambda _frame, spec, _result: (
                (0.55, 0.20, 0.90) if spec in selected_slot_specs else (0.01, 0.90, 0.60)
            ),
        )
        navigator = Navigator(SimpleNamespace(info_set=lambda *_args: None), vision)

        _matches, hits, group = navigator._sandbox_skill_group_evidence(frame, None)
        self.assertEqual(4, hits)
        self.assertEqual(1, group)

    def test_story_sandbox_skill_group_rejects_ambiguous_slot_color(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        result = MatchResult(0.99, (100, 100), (40, 40), 0.96, 0.90)
        vision = SimpleNamespace(
            template_hsv_color_ratios=lambda _frame, _spec, _result: (
                0.15,
                0.15,
                0.80,
            )
        )
        navigator = Navigator(SimpleNamespace(info_set=lambda *_args: None), vision)

        state = navigator._sandbox_skill_slot_state(
            frame,
            SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
            result,
            True,
            SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
            result,
            True,
        )

        self.assertEqual("unknown", state)

    def test_skill_group_evidence_matches_each_slot_on_the_same_frame(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        passed_specs = {
            SANDBOX_TEMPLATES[0],
            SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
            SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
            ABSORB_ICON.template,
            SEARCH_ICON.template,
            SUMMON_ICON.template,
        }
        calls = []
        result = MatchResult(0.99, (100, 100), (40, 40), 0.96, 0.90)
        vision = SimpleNamespace(
            match=lambda received, spec: calls.append((received, spec)) or result,
            passes=lambda _result, spec: spec in passed_specs,
            template_hsv_color_ratios=lambda _frame, spec, _result: (
                (0.55, 0.20, 0.90)
                if spec is SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE
                else (0.01, 0.90, 0.60)
            ),
        )
        navigator = Navigator(SimpleNamespace(info_set=lambda *_args: None), vision)

        _matches, hits, group = navigator._sandbox_skill_group_evidence(frame, None)
        self.assertEqual(2, hits)
        self.assertEqual(1, group)
        self.assertEqual(
            SANDBOX_SKILL_STATE_TEMPLATES,
            tuple(spec for _frame, spec in calls),
        )

    def test_story_field_confirmation_requires_stable_hud_without_clicking(self):
        frame = np.full((1080, 1920, 3), 100, dtype=np.uint8)
        clicks, captures = [], []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *_: None,
                operate_click=lambda *args, **kwargs: clicks.append(args),
                sleep=lambda *_: None,
            ),
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator.classify = lambda _frame: ScreenState.SANDBOX
        result = navigator._wait_for_current_sandbox(timeout=2.0, interval=0.0)
        self.assertTrue(result.success)
        self.assertEqual([], clicks)
        self.assertEqual(2, len(captures))

    def test_story_field_confirmation_does_not_accept_black_loading_frames(self):
        clock = FakeClock()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        navigator = Navigator(
            SimpleNamespace(info_set=lambda *_: None, sleep=clock.sleep),
            SimpleNamespace(capture=lambda: frame),
        )
        navigator.classify = lambda _frame: ScreenState.SANDBOX
        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic", clock,
        ):
            result = navigator._wait_for_current_sandbox(timeout=2.0, interval=0.2)
        self.assertFalse(result.success)


class StoryBadgeTest(unittest.TestCase):
    def test_story_badge_detection_requires_dual_scores_and_candidate_margin(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matches = {
            "story_cartridge_badge_06.png": (
                MatchResult(
                    STORY_BADGE_TEMPLATE_SCORE + 0.04,
                    (80, 930),
                    (30, 28),
                    pixel_score=STORY_BADGE_PIXEL_SCORE + 0.03,
                ),
            ),
            "story_cartridge_badge_08.png": (
                MatchResult(0.80, (81, 930), (31, 31), pixel_score=0.82),
            ),
        }
        vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: matches.get(
                Path(spec.file_name).name,
                (),
            ),
            ocr_text=lambda *_args, **_kwargs: "",
        )
        navigator = Navigator(SimpleNamespace(), vision)

        detection, reason = navigator._find_story_badge(frame, 6)

        self.assertEqual("", reason)
        self.assertIsNotNone(detection)
        self.assertEqual(6, detection.best.number)
        self.assertEqual(8, detection.runner_up.number)
        self.assertGreaterEqual(detection.margin, STORY_BADGE_MIN_MARGIN)

        matches["story_cartridge_badge_08.png"] = (
            MatchResult(0.96, (81, 930), (31, 31), pixel_score=0.97),
        )
        detection, reason = navigator._find_story_badge(frame, 6)
        self.assertIsNone(detection)
        self.assertIn("候选分差不足", reason)

    def test_story_badge_ocr_cannot_relax_strict_template_thresholds(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matches = {
            "story_cartridge_badge_06.png": (
                MatchResult(
                    STORY_BADGE_TEMPLATE_SCORE - 0.01,
                    (80, 930),
                    (29, 29),
                    pixel_score=STORY_BADGE_PIXEL_SCORE + 0.02,
                ),
            ),
            "story_cartridge_badge_08.png": (
                MatchResult(0.80, (81, 930), (29, 29), pixel_score=0.82),
            ),
        }
        vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: matches.get(
                Path(spec.file_name).name,
                (),
            ),
            ocr_text=lambda *_args, **_kwargs: self.fail(
                "OCR must not rescue a low-confidence template candidate"
            ),
        )
        navigator = Navigator(SimpleNamespace(), vision)

        detection, reason = navigator._find_story_badge(frame, 6)

        self.assertIsNone(detection)
        self.assertIn("未达到角标严格或编码恢复门槛", reason)

    def test_story_badge_encoded_recovery_requires_all_structural_gates(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        for failed_gate, overrides in (
            ("match", {"score": STORY_BADGE_ENCODED_TEMPLATE_SCORE - 0.001}),
            ("pixel", {"pixel_score": STORY_BADGE_ENCODED_PIXEL_SCORE - 0.001}),
            ("zncc", {"zncc_score": STORY_BADGE_ENCODED_ZNCC_SCORE - 0.001}),
            (
                "margin",
                {
                    "runner_zncc": (
                        STORY_BADGE_ENCODED_ZNCC_SCORE
                        - STORY_BADGE_ENCODED_MIN_MARGIN
                        + 0.001
                    )
                },
            ),
        ):
            with self.subTest(failed_gate=failed_gate):
                score = overrides.get("score", STORY_BADGE_ENCODED_TEMPLATE_SCORE)
                pixel_score = overrides.get(
                    "pixel_score",
                    STORY_BADGE_ENCODED_PIXEL_SCORE,
                )
                zncc_score = overrides.get("zncc_score", STORY_BADGE_ENCODED_ZNCC_SCORE)
                runner_zncc = overrides.get(
                    "runner_zncc",
                    zncc_score - STORY_BADGE_ENCODED_MIN_MARGIN,
                )
                matches = {
                    "story_cartridge_badge_06.png": (
                        MatchResult(
                            score,
                            (80, 930),
                            (29, 29),
                            pixel_score=pixel_score,
                            zncc_score=zncc_score,
                        ),
                    ),
                    "story_cartridge_badge_08.png": (
                        MatchResult(
                            0.90,
                            (81, 930),
                            (29, 29),
                            pixel_score=0.90,
                            zncc_score=runner_zncc,
                        ),
                    ),
                }
                vision = SimpleNamespace(
                    match_all=lambda _frame, spec, **_kwargs: matches.get(
                        Path(spec.file_name).name,
                        (),
                    ),
                    ocr_text=lambda *_args, **_kwargs: self.fail(
                        "rejected encoded candidates must not reach OCR"
                    ),
                )

                detection, reason = Navigator(
                    SimpleNamespace(),
                    vision,
                )._find_story_badge(frame, 6)

                self.assertIsNone(detection)
                self.assertIn(
                    "候选分差不足" if failed_gate == "margin" else "编码恢复门槛",
                    reason,
                )

    def test_story_badge_detection_rejects_conflicting_ocr_number(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matches = {
            "story_cartridge_badge_06.png": (
                MatchResult(0.99, (80, 930), (30, 28), pixel_score=0.98),
            ),
            "story_cartridge_badge_08.png": (
                MatchResult(0.80, (81, 930), (31, 31), pixel_score=0.82),
            ),
        }
        vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: matches.get(
                Path(spec.file_name).name,
                (),
            ),
            ocr_text=lambda *_args, **_kwargs: "8",
        )
        navigator = Navigator(SimpleNamespace(), vision)

        detection, reason = navigator._find_story_badge(frame, 6)

        self.assertIsNone(detection)
        self.assertIn("角标OCR数字冲突", reason)
        self.assertIn("模板=6", reason)
        self.assertIn("OCR=8", reason)

    def test_story_badge_duplicate_resolved_by_single_ocr_confirmation(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matches = {
            "story_cartridge_badge_06.png": (
                MatchResult(0.99, (80, 930), (30, 28), pixel_score=0.98, zncc_score=0.90),
                MatchResult(0.98, (480, 930), (30, 28), pixel_score=0.97, zncc_score=0.80),
            ),
            "story_cartridge_badge_08.png": (
                MatchResult(0.80, (81, 930), (31, 31), pixel_score=0.82, zncc_score=0.70),
                MatchResult(0.79, (481, 930), (31, 31), pixel_score=0.81, zncc_score=0.60),
            ),
        }

        # Only the first duplicate position carries a readable digit.
        frame[942:947, 93:98] = 255

        def fake_ocr_text(prepared, *_args, **_kwargs):
            if float(np.max(prepared)) > 200:
                return "6"
            return ""

        vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: matches.get(
                Path(spec.file_name).name,
                (),
            ),
            ocr_text=fake_ocr_text,
        )
        navigator = Navigator(SimpleNamespace(), vision)

        detection, reason = navigator._find_story_badge(frame, 6)

        self.assertEqual(reason, "")
        self.assertIsNotNone(detection)
        self.assertEqual(detection.best.number, 6)
        self.assertEqual(detection.best.result.position[0], 80)
        self.assertEqual(detection.ocr_number, 6)


class StoryBadgeRunnerTierTest(unittest.TestCase):
    @staticmethod
    def _grid_detection() -> StoryBadgeDetection:
        best = StoryBadgeCandidate(
            8,
            MatchResult(
                0.9755,
                (407, 462),
                (14, 14),
                pixel_score=0.9206,
                zncc_score=0.8060,
            ),
        )
        runner = StoryBadgeCandidate(
            5,
            MatchResult(
                0.9767,
                (400, 462),
                (14, 14),
                pixel_score=0.9204,
                zncc_score=0.8136,
            ),
        )
        return StoryBadgeDetection(best=best, runner_up=runner)

    def test_runner_up_with_full_floors_is_promoted(self):
        promoted = Navigator._story_badge_grid_runner_detections(
            5,
            (self._grid_detection(),),
        )

        self.assertEqual(1, len(promoted))
        self.assertEqual("slot_grid_runner", promoted[0].recovery_mode)
        self.assertEqual(5, promoted[0].best.number)

    def test_promoted_runner_requires_digit_ocr_confirmation(self):
        frame = np.zeros((540, 960, 3), dtype=np.uint8)
        frame[468:474, 403:409] = 255
        promoted = Navigator._story_badge_grid_runner_detections(
            5,
            (self._grid_detection(),),
        )

        def ocr_text(prepared, *_args, **_kwargs):
            return "5" if float(np.max(prepared)) > 200 else ""

        confirming = Navigator(
            SimpleNamespace(),
            SimpleNamespace(ocr_text=ocr_text),
        )
        detection, reason = confirming._find_story_badge_from_detections(
            frame,
            5,
            promoted,
        )
        self.assertEqual("", reason)
        self.assertIsNotNone(detection)
        self.assertEqual("slot_grid_runner", detection.recovery_mode)
        self.assertEqual(5, detection.ocr_number)

        empty = Navigator(
            SimpleNamespace(),
            SimpleNamespace(ocr_text=lambda *_args, **_kwargs: ""),
        )
        detection, reason = empty._find_story_badge_from_detections(
            frame,
            5,
            promoted,
        )
        self.assertIsNone(detection)
        self.assertIn("角标次优候选OCR未确认", reason)
