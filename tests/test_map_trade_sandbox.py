"""Map-trade sandbox tests (split from test_map_trade.py)."""

import unittest
from itertools import cycle
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    SUMMON_ICON,
)
from src.tasks.map_trade.card_status import (
    SUPPRESS_COMPLETED_TEMPLATE,
    SUPPRESS_PENDING_TEMPLATE,
)
from src.tasks.map_trade.models import (
    MatchResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    SANDBOX_CONFIRM_ACTION_TEMPLATES,
    SANDBOX_SKILL_GROUP_PIXEL_SCORE,
    SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
    SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO,
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
    SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
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
    STORY_BADGE_OCR_MIN_CONFIDENCE,
    STORY_BADGE_PIXEL_SCORE,
    STORY_BADGE_TEMPLATE_SCORE,
    STORY_SANDBOX_SWITCH_WINDOW,
    Navigator,
    SandboxConfirmation,
)

ROOT = Path(__file__).resolve().parents[1]


class SandboxConfirmationTest(unittest.TestCase):
    def test_story_card_state_templates_are_packaged_with_alpha_masks(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        expected_shapes = {
            "image/green/StoryAbsorbAvailableGE.png": (29, 29, 4),
            "image/green/StoryAbsorbCompletedGE.png": (29, 31, 4),
            "image/green/StorySuppressAvailableGE.png": (28, 28, 4),
            "image/green/StorySuppressCompletedGE.png": (28, 28, 4),
        }

        for file_name, expected_shape in expected_shapes.items():
            with self.subTest(file_name=file_name):
                template = cv2.imread(
                    str(template_root / file_name),
                    cv2.IMREAD_UNCHANGED,
                )
                self.assertIsNotNone(template)
                self.assertEqual(expected_shape, template.shape)
                self.assertGreater(np.count_nonzero(template[:, :, 3] == 0), 0)
                self.assertGreater(np.count_nonzero(template[:, :, 3] == 255), 0)
        suppress_pending = cv2.imread(
            str(template_root / SUPPRESS_PENDING_TEMPLATE.file_name),
            cv2.IMREAD_UNCHANGED,
        )
        suppress_completed = cv2.imread(
            str(template_root / SUPPRESS_COMPLETED_TEMPLATE.file_name),
            cv2.IMREAD_UNCHANGED,
        )
        self.assertEqual(0, suppress_pending[-1, -1, 3])
        self.assertEqual(255, suppress_completed[-1, -1, 3])

    def test_sandbox_skill_slot_templates_are_packaged_with_alpha_masks(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        expected_shapes = {
            "image/green/SandboxSkillSlot1AvailableGE.png": (44, 44, 4),
            "image/green/SandboxSkillSlot2UsedGE.png": (44, 44, 4),
            "image/green/SandboxSkillSlot1UsedGE.png": (44, 44, 4),
            "image/green/SandboxSkillSlot2AvailableGE.png": (45, 45, 4),
        }

        for file_name, expected_shape in expected_shapes.items():
            with self.subTest(file_name=file_name):
                template = cv2.imread(
                    str(template_root / file_name),
                    cv2.IMREAD_UNCHANGED,
                )
                self.assertIsNotNone(template)
                self.assertEqual(expected_shape, template.shape)
                self.assertGreater(np.count_nonzero(template[:, :, 3] == 0), 0)
                self.assertGreater(np.count_nonzero(template[:, :, 3] == 255), 0)

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

        confirmation = navigator._match_story_sandbox_signals(frame)

        self.assertEqual(4, confirmation.skill_state_hits)
        self.assertEqual(1, confirmation.skill_group)
        self.assertTrue(confirmation.passed)

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

    def test_story_sandbox_skill_group_uses_other_slot_template_when_missing(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        result = MatchResult(0.99, (1644, 983), (55, 55), 0.94, 0.60)
        vision = SimpleNamespace(
            template_hsv_color_ratios=lambda _frame, spec, _result: (
                (0.71, 0.28, 1.0)
                if spec is SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE
                else (0.01, 0.99, 0.56)
            )
        )
        navigator = Navigator(SimpleNamespace(info_set=lambda *_args: None), vision)

        state = navigator._sandbox_skill_slot_state(
            frame,
            SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
            MatchResult(-1.0, (0, 0), (0, 0)),
            False,
            SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
            result,
            True,
        )

        self.assertEqual("selected", state)

    def test_story_sandbox_confirmation_matches_all_signal_groups_and_accepts_three_actions(self):
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

        confirmation = navigator._match_story_sandbox_signals(frame)

        self.assertEqual(1, confirmation.map_signal_hits)
        self.assertEqual(2, confirmation.skill_state_hits)
        self.assertEqual(3, confirmation.action_hits)
        self.assertEqual(1, confirmation.skill_group)
        self.assertTrue(confirmation.passed)
        self.assertEqual(
            (
                *SANDBOX_TEMPLATES,
                *SANDBOX_SKILL_STATE_TEMPLATES,
                *(spec for _name, spec in SANDBOX_CONFIRM_ACTION_TEMPLATES),
            ),
            tuple(spec for _frame, spec in calls),
        )

    def test_story_sandbox_confirmation_rejects_missing_group_or_action_evidence(self):
        self.assertFalse(SandboxConfirmation(1, 2, 3, None).passed)
        self.assertFalse(SandboxConfirmation(0, 2, 3, 1).passed)
        self.assertFalse(SandboxConfirmation(1, 1, 3, 1).passed)
        self.assertFalse(SandboxConfirmation(1, 2, 2, 1).passed)
        self.assertFalse(SandboxConfirmation(1, 4, 3, None).passed)

    def test_story_sandbox_group_two_switches_to_group_one_once_before_stabilizing(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        confirmations = iter(
            (
                SandboxConfirmation(1, 2, 3, 2),
                SandboxConfirmation(1, 2, 3, 1),
                SandboxConfirmation(1, 2, 3, None),
                SandboxConfirmation(1, 2, 3, 1),
                SandboxConfirmation(1, 2, 3, None),
                SandboxConfirmation(1, 2, 3, 1),
            )
        )
        captures = []
        clicks = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
        )
        navigator = Navigator(
            task,
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX
        navigator._match_story_sandbox_signals = lambda _frame: next(confirmations)

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=[100.0] * 20,
        ):
            result = navigator._wait_for_current_sandbox(timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual(
            [
                (
                    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT[0],
                    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT[1],
                    0.5,
                )
            ],
            clicks,
        )
        self.assertEqual(SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER, (1671, 1011))
        self.assertEqual(6, len(captures))

    def test_story_sandbox_group_two_does_not_confirm_when_switch_stays_on_group_two(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        captures = []
        clicks = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
        )
        navigator = Navigator(
            task,
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX
        navigator._match_story_sandbox_signals = lambda _frame: SandboxConfirmation(
            1,
            2,
            3,
            2,
        )

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=[100.0] * 20,
        ):
            result = navigator._wait_for_current_sandbox(timeout=2.0, interval=0.0)

        self.assertFalse(result.success)
        self.assertIn("点击技能组1后连续", result.message)
        self.assertIn("帧仍识别为技能组2，切换失败", result.message)
        self.assertEqual(
            [
                (
                    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT[0],
                    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT[1],
                    0.5,
                )
            ],
            clicks,
        )
        self.assertEqual(1 + STORY_SANDBOX_SWITCH_WINDOW, len(captures))

    def test_story_sandbox_group_two_alternating_with_none_does_not_fail_early(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        confirmations = cycle(
            (
                SandboxConfirmation(1, 2, 3, 2),
                SandboxConfirmation(1, 2, 3, None),
            )
        )
        captures = []
        clicks = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
        )
        navigator = Navigator(
            task,
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX
        navigator._match_story_sandbox_signals = lambda _frame: next(confirmations)

        with patch(
            "src.tasks.map_trade.navigator_sandbox.monotonic",
            side_effect=[100.0] * 9 + [101.0],
        ):
            result = navigator._wait_for_current_sandbox(timeout=0.1, interval=0.0)

        self.assertFalse(result.success)
        self.assertEqual("未稳定确认当前剧情卡带箱庭", result.message)
        self.assertNotIn("切换失败", result.message)
        self.assertEqual(
            [
                (
                    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT[0],
                    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT[1],
                    0.5,
                )
            ],
            clicks,
        )
        self.assertEqual(8, len(captures))


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

    def test_story_badge_ranking_uses_alpha_zncc_discrimination(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matches = {
            "story_cartridge_badge_06.png": (
                MatchResult(
                    0.96,
                    (80, 930),
                    (29, 29),
                    pixel_score=0.96,
                    zncc_score=0.91,
                ),
            ),
            "story_cartridge_badge_08.png": (
                MatchResult(
                    0.99,
                    (81, 930),
                    (29, 29),
                    pixel_score=0.99,
                    zncc_score=0.70,
                ),
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
        self.assertAlmostEqual(0.21, detection.margin)

    def test_story_badge_detection_records_matching_ocr_assistance(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matches = {
            "story_cartridge_badge_06.png": (
                MatchResult(
                    STORY_BADGE_TEMPLATE_SCORE + 0.04,
                    (80, 930),
                    (29, 29),
                    pixel_score=STORY_BADGE_PIXEL_SCORE + 0.03,
                ),
            ),
            "story_cartridge_badge_08.png": (
                MatchResult(0.80, (81, 930), (29, 29), pixel_score=0.82),
            ),
        }
        ocr_calls = []

        def ocr_text(frame, name, **kwargs):
            ocr_calls.append((frame.shape, name, kwargs))
            return "6"

        vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: matches.get(
                Path(spec.file_name).name,
                (),
            ),
            ocr_text=ocr_text,
        )
        navigator = Navigator(SimpleNamespace(), vision)

        detection, reason = navigator._find_story_badge(frame, 6)

        self.assertEqual("", reason)
        self.assertIsNotNone(detection)
        self.assertEqual(6, detection.ocr_number)
        self.assertEqual("6", detection.ocr_text)
        self.assertEqual((272, 288, 3), ocr_calls[0][0])
        self.assertEqual("剧情角标数字辅助", ocr_calls[0][1])
        self.assertEqual(0, ocr_calls[0][2]["target_height"])
        self.assertEqual(
            STORY_BADGE_OCR_MIN_CONFIDENCE,
            ocr_calls[0][2]["minimum_threshold"],
        )

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

    def test_story_badge_detection_rejects_duplicate_target_number(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matches = {
            "story_cartridge_badge_06.png": (
                MatchResult(0.99, (80, 930), (30, 28), pixel_score=0.98),
                MatchResult(0.98, (480, 930), (30, 28), pixel_score=0.97),
            ),
            "story_cartridge_badge_08.png": (
                MatchResult(0.80, (81, 930), (31, 31), pixel_score=0.82),
                MatchResult(0.79, (481, 930), (31, 31), pixel_score=0.81),
            ),
        }
        vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: matches.get(
                Path(spec.file_name).name,
                (),
            )
        )
        navigator = Navigator(SimpleNamespace(), vision)

        detection, reason = navigator._find_story_badge(frame, 6)

        self.assertIsNone(detection)
        self.assertIn("同一编号出现2个有效位置", reason)
