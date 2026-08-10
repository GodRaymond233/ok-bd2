import ast
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks import MapCollectionTask as map_collection_task_module
from src.tasks import MapTradeTask as map_trade_task_module
from src.tasks.BaseBD2Task import BaseBD2Task, green_mask_from_template
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
    ActionIconDetection,
    ActionIconDetector,
    ActionIconState,
)
from src.tasks.map_trade.calendar import (
    PURCHASE_STOCK_REFRESH_HOUR,
    SALE_PRICE_REFRESH_HOUR,
    PriceCalendarClient,
    parse_calendar_payload,
    parse_manual_calendar,
    purchase_stock_date,
    sale_price_calendar_date,
)
from src.tasks.map_trade.card_status import (
    ABSORB_COMPLETED_TEMPLATE,
    ABSORB_PENDING_TEMPLATE,
    SUPPRESS_COMPLETED_TEMPLATE,
    SUPPRESS_PENDING_TEMPLATE,
    CardActionDetection,
    CardActionState,
    CardStatusDetector,
    CollectionCardSelectionOutcome,
    CollectionCardSelectionResult,
    StoryCardCompletion,
    card_icon_region,
)
from src.tasks.map_trade.collector import (
    ABSORB_ACTION,
    ACTION_FEEDBACK_CHARACTER_RATIO,
    ACTION_FEEDBACK_RELATIVE_ROI,
    ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS,
    ACTION_FEEDBACK_TIMEOUT,
    ACTION_ICON_DETECTION_INTERVAL,
    ACTION_OCR_WINDOW_INTERVAL,
    BATTLE_ACTIONS,
    SEARCH_ACTION,
    SEARCH_COUNTDOWN_REFERENCE_ROI,
    SEARCH_COUNTDOWN_RELATIVE_ROI,
    SKILL_FAILURE_EVIDENCE_LIMIT,
    SKILL_FIXED_COUNT_REFERENCE_ROIS,
    SKILL_GROUP_REFERENCE_POINTS,
    SKILL_GROUP_RELATIVE_POINTS,
    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SKILL_OCR_FALLBACK_UPSCALE,
    SKILL_OCR_UPSCALE,
    SUMMON_ACTION,
    Collector,
    SearchCountdownSession,
    SkillExecutionResult,
    SkillFeedbackObservation,
)
from src.tasks.map_trade.data import (
    SHOP_CARTRIDGE_BRIGHTNESS,
    SHOP_CARTRIDGE_LABELS,
    SHOP_CARTRIDGE_PAGES,
    SHOP_FAVORITE_POINTS,
    SHOP_PURCHASE_REFERENCES,
    SHOP_UNFAVORITED_POINTS,
    shop_purchase_reference,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    DAILY_ABSORB_LIMIT,
    DAILY_SUMMON_LIMIT,
    DAILY_SUPPRESS_LIMIT,
    DEFAULT_SALE_WHITELIST,
    PINNED_CARD_IDS,
    RECIPE_TEMPLATES,
    STORY_CARDS,
    STORY_COLLECTION_MAPS,
    CalendarEntry,
    CollectionActionState,
    CollectionMapRole,
    CollectionResult,
    MatchResult,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import (
    AREA_MAP_BACK_TEMPLATE,
    AREA_MAP_OPEN_RELATIVE_POINT,
    AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
    AREA_MAP_TITLE_OCR_RELATIVE_ROI,
    BARGAIN_CONFIRM_POINT,
    BARGAIN_POINT,
    CHAPTER_HOME_POINT,
    DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
    DISCOUNT_SHOP_CLOSE_KEYWORDS,
    DISCOUNT_SHOP_CLOSE_POINT,
    DISCOUNT_SHOP_CLOSE_TIMEOUT,
    FIRST_CARD_CONFIRM_REGION,
    FIRST_CARD_INSERT_REGION,
    FIRST_CARD_SKIP_TEMPLATE,
    HAND_TEMPLATE,
    HOME_TEMPLATES,
    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
    PROBE_QUICK_SWITCH_SCROLL_COUNT,
    PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_POINT,
    PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    PROBE_STORY_BADGE_CONFIRM_SECONDS,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_STORY_NUMBER,
    QUICK_SWITCH_CARTRIDGE_REGION,
    QUICK_SWITCH_PAGE_KEYWORDS,
    QUICK_SWITCH_SCROLL_FOCUS_POINT,
    QUICK_SWITCH_SCROLL_INTERVAL,
    QUICK_SWITCH_SCROLL_POINT,
    QUICK_SWITCH_SCROLL_RESET_AMOUNT,
    QUICK_SWITCH_SCROLL_RESET_COUNT,
    QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    QUICK_SWITCH_SCROLL_UP_AMOUNT,
    QUICK_SWITCH_SCROLL_UP_COUNT,
    QUICK_SWITCH_TEMPLATE,
    RETURN_HOME_TIMEOUT,
    SANDBOX_CONFIRM_ACTION_TEMPLATES,
    SANDBOX_MAP_SETTLE_SECONDS,
    SANDBOX_MAP_TELEPORT_TEMPLATE,
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
    SANDBOX_TELEPORT_SKILL_POLL_INTERVAL,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
    SANDBOX_TEMPLATES,
    STORY_BADGE_CANDIDATE_ZNCC_SCORE,
    STORY_BADGE_MIN_MARGIN,
    STORY_BADGE_OCR_MIN_CONFIDENCE,
    STORY_BADGE_PIXEL_SCORE,
    STORY_BADGE_SPECS,
    STORY_BADGE_TEMPLATE_SCORE,
    STORY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    STORY_CATEGORY_HIGHLIGHT_REGION,
    STORY_CATEGORY_POINT,
    STORY_SANDBOX_STABLE_HITS,
    TELEPORT_GENERATION_OCR_TIMEOUT,
    TELEPORT_INTERACTION_CLICK_DELAY,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_SKILL_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    TRADE_MERCHANT_CONTEXT_TEMPLATE,
    AreaMapContext,
    LocatedStoryCard,
    Navigator,
    ProbedStoryCard,
    SandboxConfirmation,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.progress import (
    STATE_SCHEMA_VERSION,
    UTC_PLUS_8,
    VALID_FAVORITE_SHOP_IDS,
    ProgressStore,
    daily_cycle_key,
    weekly_cycle_key,
)
from src.tasks.map_trade.trader import (
    BUY_ALL_FAVORITES_KEYWORD,
    BUY_ALL_FAVORITES_STABLE_HITS,
    BUY_CONFIRM_DIALOG_REGION,
    BUY_CONFIRM_KEYWORDS,
    BUY_CONFIRM_POINT,
    BUY_CONFIRM_PRE_CLICK_DELAY,
    BUY_CONFIRM_TIMEOUT,
    BUY_TO_SELL_POST_CLICK_DELAY,
    BUY_TO_SELL_PRE_CLICK_DELAY,
    BUY_TO_SELL_SOLD_OUT_KEYWORD,
    SALE_CONFIRM_POINT,
    SALE_DIALOG_REGION,
    SALE_ITEM_NAME_LEFT_OFFSET_X,
    SALE_MAX_POINT,
    SALE_SLIDER_REGION,
    SELL_MODE_POINT,
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    SHOP_MODE_TITLE_REGION,
    STAR_PIXEL_THRESHOLD,
    STAR_POST_CLICK_DELAY,
    STAR_ROI_HALF_SIZE_X,
    STAR_ROI_HALF_SIZE_Y,
    STAR_TEMPLATE_THRESHOLD,
    Trader,
)
from src.tasks.map_trade.vision import Vision, parse_used_limit
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.MapTradeTask import (
    COOKING_CONFIG_KEYS,
    MAP_OCR_THRESHOLD_KEY,
    MAP_VISION_THRESHOLD_KEY,
    TRADE_OCR_THRESHOLD_KEY,
    TRADE_VISION_THRESHOLD_KEY,
    MapTradeTask,
    _migrate_collection_config,
    _trade_section_migration_values,
)
from src.utils.template_resolution import offline_template_scale

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_CALENDAR = ROOT / "assets" / "map_trade" / "price_calendar.v1.json"


class FakeTask:
    def __init__(self):
        self.config = {"跑图跑商 OCR 阈值": 0.2}
        self.clicks = []
        self.infos = []

    def operate_click(self, x, y, after_sleep=0):
        self.clicks.append((x, y, after_sleep))

    def capture_frame(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def info_set(self, *args):
        self.infos.append(args)

    def sleep(self, *_args):
        return None


class SaveFrameTest(unittest.TestCase):
    def test_save_frame_preserves_bgr_bgra_and_grayscale_channels(self):
        from src.tasks import BaseBD2Task as base_task_module

        task = object.__new__(BaseBD2Task)
        task.info_set = lambda *_args: None
        frames = (
            np.array([[[3, 2, 1]]], dtype=np.uint8),
            np.array([[[3, 2, 1, 4]]], dtype=np.uint8),
            np.array([[7]], dtype=np.uint8),
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(base_task_module, "PROBE_OUTPUT_DIR", Path(directory)):
                for index, frame in enumerate(frames):
                    with self.subTest(channels=frame.shape):
                        path = task.save_frame(f"channel_{index}", frame)
                        saved = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                        np.testing.assert_array_equal(saved, frame)


class VisionTest(unittest.TestCase):
    def test_reference_conversion_for_all_supported_resolutions(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(resolution=(width, height)):
                self.assertEqual(
                    (width // 2, height // 2), Vision.reference_point(640, 360, width, height)
                )
                self.assertEqual(
                    (width // 4, height // 4, width // 2, height // 2),
                    Vision.reference_roi((320, 180, 640, 360), width, height),
                )

    def test_sandbox_teleport_template_uses_tight_shared_slot_at_all_resolutions(self):
        self.assertEqual(
            ACTION_SLOT_RELATIVE_ROIS["teleport"],
            SANDBOX_TELEPORT_SKILL_TEMPLATE.relative_roi,
        )
        self.assertEqual(
            ACTION_SLOT_CENTER_RELATIVE_ROIS["teleport"],
            SANDBOX_TELEPORT_SKILL_TEMPLATE.candidate_center_roi,
        )

        source_template = np.random.default_rng(42).integers(
            0,
            256,
            (20, 20),
            dtype=np.uint8,
        )
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(resolution=(width, height)):
                task = FakeTask()
                vision = Vision(task)
                vision._load = lambda _spec: (source_template, None)
                scale = offline_template_scale(
                    SANDBOX_TELEPORT_SKILL_TEMPLATE.file_name,
                    width,
                    height,
                    reference_scale=SANDBOX_TELEPORT_SKILL_TEMPLATE.reference_scale,
                )
                scaled = vision._resize_template(source_template, scale)
                center_x = round(ACTION_SLOT_CENTERS_REFERENCE["teleport"][0] * width / 1920)
                center_y = round(ACTION_SLOT_CENTERS_REFERENCE["teleport"][1] * height / 1080)
                radius_x = round(55 * min(width / 1920, height / 1080))
                frame = np.random.default_rng(width).integers(
                    0,
                    20,
                    (height, width),
                    dtype=np.uint8,
                )

                def paste(center):
                    left = center[0] - scaled.shape[1] // 2
                    top = center[1] - scaled.shape[0] // 2
                    frame[top : top + scaled.shape[0], left : left + scaled.shape[1]] = scaled

                # This candidate remains inside the search crop but outside
                # the center gate and must therefore be rejected.
                paste((center_x + radius_x, center_y))
                outside = vision.match(frame, SANDBOX_TELEPORT_SKILL_TEMPLATE)
                self.assertFalse(vision.passes(outside, SANDBOX_TELEPORT_SKILL_TEMPLATE))

                frame = np.random.default_rng(width + 1).integers(
                    0,
                    20,
                    (height, width),
                    dtype=np.uint8,
                )
                paste((center_x, center_y))
                inside = vision.match(frame, SANDBOX_TELEPORT_SKILL_TEMPLATE)
                self.assertTrue(vision.passes(inside, SANDBOX_TELEPORT_SKILL_TEMPLATE))
                self.assertLessEqual(abs(inside.center[0] - center_x), 2)
                self.assertLessEqual(abs(inside.center[1] - center_y), 2)

    def test_template_click_uses_match_center(self):
        task = FakeTask()
        vision = Vision(task)
        vision.wait_template = lambda *_args, **_kwargs: MatchResult(0.9, (100, 200), (40, 20))

        self.assertTrue(vision.click_template(TemplateSpec("test", "unused.png")))
        self.assertEqual((120 / 1280, 210 / 720, 0.8), task.clicks[-1])
        self.assertIn(
            (
                "test点击中心",
                "center=(120,210), match=0.900, pixel=-1.000",
            ),
            task.infos,
        )

    def test_stable_template_click_waits_for_temporal_consensus(self):
        task = FakeTask()
        sleeps = []
        task.sleep = sleeps.append
        vision = Vision(task)
        result = MatchResult(0.91, (100, 200), (40, 20), pixel_score=0.92)
        vision.match = lambda *_args, **_kwargs: result
        vision.passes = lambda *_args, **_kwargs: True

        self.assertTrue(
            vision.click_stable_template(
                TemplateSpec("stable", "unused.png"),
                timeout=0.01,
            )
        )
        self.assertEqual((120 / 1280, 210 / 720, 0.8), task.clicks[-1])
        self.assertEqual(10, len(sleeps))
        self.assertTrue(all(seconds == 0.1 for seconds in sleeps))
        self.assertTrue(any(key == "stable稳定识别" for key, _value in task.infos))

    def test_operate_click_log_converts_relative_target_to_client_pixels(self):
        self.assertEqual(
            ("快速切换按钮: client=(959,539), relative=(0.500000,0.500000)"),
            BaseBD2Task._click_log_message(
                0.5,
                0.5,
                1918,
                1079,
                "快速切换按钮",
            ),
        )

    def test_configured_threshold_overrides_template_default(self):
        task = FakeTask()
        task.config["跑图跑商识图阈值"] = 0.81

        self.assertEqual(0.81, Vision(task).threshold_for(TemplateSpec("test", "unused.png", 0.7)))

    def test_template_pass_requires_pixel_similarity_when_configured(self):
        task = FakeTask()
        task.config["跑图跑商识图阈值"] = 0.72
        vision = Vision(task)
        spec = TemplateSpec("test", "unused.png", 0.72, min_pixel_score=0.80)

        self.assertFalse(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), pixel_score=0.79),
                spec,
            )
        )
        self.assertTrue(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), pixel_score=0.81),
                spec,
            )
        )

    def test_template_pass_requires_zncc_when_configured(self):
        task = FakeTask()
        task.config["跑图跑商识图阈值"] = 0.72
        vision = Vision(task)
        spec = TemplateSpec("test", "unused.png", 0.72, min_zncc_score=0.85)

        self.assertFalse(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), zncc_score=0.84),
                spec,
            )
        )
        self.assertTrue(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), zncc_score=0.86),
                spec,
            )
        )

    def test_match_all_returns_multiple_peaks_in_full_frame_coordinates(self):
        task = FakeTask()
        vision = Vision(task)
        rng = np.random.default_rng(7)
        template = rng.integers(0, 256, (20, 20), dtype=np.uint8)
        frame = np.zeros((1080, 1920), dtype=np.uint8)
        frame[930:950, 100:120] = template
        frame[930:950, 500:520] = template
        spec = TemplateSpec(
            "multi",
            "quick_switch_cartridges/unused.png",
            relative_roi=QUICK_SWITCH_CARTRIDGE_REGION,
        )
        vision._load = lambda _spec: (template, None)

        matches = vision.match_all(
            frame,
            spec,
            minimum_score=0.95,
            peak_radius=5,
        )

        self.assertEqual([(110, 940), (510, 940)], sorted(value.center for value in matches))
        self.assertTrue(all(value.pixel_score == 1.0 for value in matches))

    def test_story_cartridge_brightness_calibration_separates_selected_state(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        normal = cv2.imread(
            str(template_root / SHOP_CARTRIDGE_BRIGHTNESS.normal_template),
            cv2.IMREAD_GRAYSCALE,
        )
        unselected = cv2.imread(
            str(template_root / SHOP_CARTRIDGE_BRIGHTNESS.unselected_template),
            cv2.IMREAD_GRAYSCALE,
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

    def test_green_screen_mask_excludes_only_pure_green(self):
        template = np.array([[[0, 255, 0], [1, 254, 1], [20, 30, 40]]], dtype=np.uint8)

        mask = green_mask_from_template(template)

        np.testing.assert_array_equal(mask, np.array([[0, 255, 255]], dtype=np.uint8))

    def test_root_rgba_template_uses_alpha_without_masking_opaque_green(self):
        with tempfile.TemporaryDirectory() as directory:
            template = np.array(
                [
                    [
                        [0, 255, 0, 255],
                        [20, 30, 40, 0],
                    ]
                ],
                dtype=np.uint8,
            )
            path = Path(directory) / "root-alpha.png"
            self.assertTrue(cv2.imwrite(str(path), template))
            with patch(
                "src.tasks.map_trade.vision.TEMPLATE_DIR",
                Path(directory),
            ):
                _gray, mask = Vision(FakeTask())._load(TemplateSpec("root alpha", path.name))

        np.testing.assert_array_equal(mask, np.array([[255, 0]], dtype=np.uint8))

    def test_template_color_ratios_only_measure_alpha_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            template = np.zeros((2, 2, 4), dtype=np.uint8)
            template[:, :, 3] = np.array([[255, 255], [255, 0]], dtype=np.uint8)
            path = Path(directory) / "color-mask.png"
            self.assertTrue(cv2.imwrite(str(path), template))
            frame = np.array(
                [
                    [
                        [0, 100, 0],
                        [0, 0, 100],
                    ],
                    [
                        [80, 80, 80],
                        [0, 255, 0],
                    ],
                ],
                dtype=np.uint8,
            )
            with patch(
                "src.tasks.map_trade.vision.TEMPLATE_DIR",
                Path(directory),
            ):
                ratios = Vision(FakeTask()).template_color_ratios(
                    frame,
                    TemplateSpec("colors", path.name),
                    MatchResult(1.0, (0, 0), (2, 2)),
                )

        self.assertIsNotNone(ratios)
        self.assertAlmostEqual(1 / 3, ratios[0])
        self.assertAlmostEqual(1 / 3, ratios[1])
        self.assertAlmostEqual(1 / 3, ratios[2])

    def test_template_hsv_color_ratios_only_measure_alpha_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            template = np.zeros((2, 2, 4), dtype=np.uint8)
            template[:, :, 3] = np.array([[255, 255], [255, 0]], dtype=np.uint8)
            path = Path(directory) / "hsv-color-mask.png"
            self.assertTrue(cv2.imwrite(str(path), template))
            frame = np.array(
                [
                    [
                        [0, 200, 255],
                        [160, 160, 160],
                    ],
                    [
                        [20, 20, 20],
                        [0, 200, 255],
                    ],
                ],
                dtype=np.uint8,
            )
            with patch(
                "src.tasks.map_trade.vision.TEMPLATE_DIR",
                Path(directory),
            ):
                ratios = Vision(FakeTask()).template_hsv_color_ratios(
                    frame,
                    TemplateSpec("hsv colors", path.name),
                    MatchResult(1.0, (0, 0), (2, 2)),
                )

        self.assertIsNotNone(ratios)
        self.assertAlmostEqual(1 / 3, ratios[0])
        self.assertAlmostEqual(1 / 3, ratios[1])
        self.assertAlmostEqual(2 / 3, ratios[2])

    def test_star_color_uses_saturation(self):
        match = MatchResult(0.9, (0, 0), (20, 20))
        yellow = np.full((20, 20, 3), (0, 255, 255), dtype=np.uint8)
        gray = np.full((20, 20, 3), (128, 128, 128), dtype=np.uint8)

        self.assertTrue(Vision.star_is_yellow(yellow, match))
        self.assertFalse(Vision.star_is_yellow(gray, match))

    def test_ocr_roi_coordinates_are_returned_in_full_frame_space(self):
        task = FakeTask()
        task.ocr = lambda **_kwargs: [SimpleNamespace(name="确认", x=10, y=20, width=30, height=10)]
        vision = Vision(task)

        boxes = vision.ocr_boxes(task.capture_frame(), "roi", roi=(100, 200, 300, 100))

        self.assertEqual(
            (110, 220, 30, 10), (boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height)
        )

    def test_ocr_roi_outside_frame_is_rejected_before_ocr(self):
        task = FakeTask()
        task.ocr = lambda **_kwargs: self.fail("空裁剪区域不得送入OCR")
        vision = Vision(task)

        boxes = vision.ocr_boxes(
            task.capture_frame(),
            "outside",
            roi=(1400, 800, 100, 100),
        )

        self.assertEqual([], boxes)

    def test_relative_ocr_roi_coordinates_are_returned_in_full_frame_space(self):
        task = FakeTask()
        task.ocr = lambda **_kwargs: [SimpleNamespace(name="确认", x=10, y=20, width=30, height=10)]
        vision = Vision(task)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        boxes = vision.ocr_boxes(
            frame,
            "relative-roi",
            relative_roi=BUY_CONFIRM_DIALOG_REGION,
        )

        self.assertEqual(
            (711, 348, 30, 10),
            (boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height),
        )

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


class CatalogAndSafetyTest(unittest.TestCase):
    def test_shop_favorite_points_preserve_supplied_1920_by_1080_calibration(self):
        expected_reference_points = {
            1: (580, 140),
            2: (913, 141),
            3: (1244, 140),
            4: (1576, 140),
            5: (581, 250),
            6: (912, 251),
            7: (1244, 250),
            8: (1575, 250),
            9: (580, 359),
            10: (912, 362),
            11: (1243, 360),
            12: (1576, 360),
            13: (580, 469),
            14: (913, 470),
            15: (1244, 471),
        }

        self.assertEqual(
            {
                point_number: (x / 1920, y / 1080)
                for point_number, (x, y) in expected_reference_points.items()
            },
            SHOP_FAVORITE_POINTS,
        )

    def test_shop_unfavorited_points_preserve_supplied_cartridge_requirements(self):
        expected = {
            "S1": {6},
            "S2": {1},
            "S3": {8, 9, 12, 13},
            "S4": {3, 4, 11, 12, 13},
            "S5": {2, 4, 8},
            "S6": {8, 9},
            "S7": {5, 9},
            "S8": {3, 4, 9, 10, 11, 12},
            "S9": {1, 2, 3, 4, 5, 6, 7, 8},
            "S10": {2, 3, 4, 5, 9, 12},
            "S11": {9},
            "S12": {3, 4, 6, 11, 12, 13},
            "S13": {7, 8, 9, 11, 12, 13},
            "S14": {2, 3, 4, 5, 9, 11, 12},
            "S15": {1, 8, 9},
            "S16": {7, 9, 10},
            "S17": {2, 8, 9, 10},
            "S18": {2, 9},
            "S19": {3, 8, 9},
            "R1": set(),
            "R2": {4},
            "R3": {3, 10},
            "R4": set(),
            "R5": {3, 7, 8, 9, 11},
            "R6": {3, 7, 8, 9, 11},
            "R7": {4},
            "E1": set(),
            "E2": {4},
            "E3": {3, 8, 10},
            "E5": {4},
            "E7": {5},
        }

        self.assertEqual(
            {shop: frozenset(points) for shop, points in expected.items()},
            SHOP_UNFAVORITED_POINTS,
        )
        self.assertEqual(set(range(1, 20)), {int(key[1:]) for key in expected if key[0] == "S"})
        self.assertEqual(set(range(1, 8)), {int(key[1:]) for key in expected if key[0] == "R"})
        self.assertEqual({1, 2, 3, 5, 7}, {int(key[1:]) for key in expected if key[0] == "E"})
        for shop, points in SHOP_UNFAVORITED_POINTS.items():
            with self.subTest(shop=shop):
                self.assertTrue(points <= SHOP_FAVORITE_POINTS.keys())

    def test_local_purchase_references_connect_cartridges_templates_and_coordinates(self):
        self.assertEqual(SHOP_UNFAVORITED_POINTS.keys(), SHOP_PURCHASE_REFERENCES.keys())
        self.assertEqual(SHOP_CARTRIDGE_LABELS.keys(), SHOP_PURCHASE_REFERENCES.keys())

        template_root = ROOT / "recognition-assets" / "template-assets"
        for shop_id, reference in SHOP_PURCHASE_REFERENCES.items():
            with self.subTest(shop=shop_id):
                self.assertEqual(shop_id, reference.shop_id)
                self.assertEqual(SHOP_CARTRIDGE_LABELS[shop_id], reference.label)
                self.assertEqual(
                    SHOP_UNFAVORITED_POINTS[shop_id],
                    reference.unfavorited_slots,
                )
                self.assertEqual(
                    tuple(
                        (slot, SHOP_FAVORITE_POINTS[slot])
                        for slot in sorted(SHOP_UNFAVORITED_POINTS[shop_id])
                    ),
                    reference.unfavorited_points,
                )
                for file_name in reference.cartridge_templates:
                    self.assertTrue((template_root / file_name).is_file(), file_name)

        self.assertEqual(
            SHOP_PURCHASE_REFERENCES["S1"],
            shop_purchase_reference("S1:血骑士"),
        )
        self.assertEqual(2, len(SHOP_PURCHASE_REFERENCES["S1"].cartridge_templates))
        self.assertTrue((template_root / "shop/cartridges/star_gray.png").is_file())
        with self.assertRaisesRegex(KeyError, "未知商品卡带"):
            shop_purchase_reference("E4:旧编号")

    def test_shop_cartridge_pages_preserve_supplied_scroll_calibration(self):
        expected_pages = (
            tuple(f"S{number}" for number in range(1, 11)),
            (*tuple(f"S{number}" for number in range(11, 20)), "R1"),
            (
                *tuple(f"R{number}" for number in range(2, 8)),
                "E1",
                "E2",
                "E3",
                "E5",
            ),
            ("E7",),
        )

        self.assertEqual(
            (0, 9, 10, 1),
            tuple(page.scroll_down_from_previous for page in SHOP_CARTRIDGE_PAGES),
        )
        self.assertEqual((1, 2, 3, 4), tuple(page.page_number for page in SHOP_CARTRIDGE_PAGES))
        self.assertEqual(expected_pages, tuple(page.shop_ids for page in SHOP_CARTRIDGE_PAGES))
        self.assertEqual(
            (("S1",), ("R1", "S11"), ("E5", "R2"), ("E7",)),
            tuple(page.confirmation_shop_ids for page in SHOP_CARTRIDGE_PAGES),
        )
        flattened = tuple(shop_id for page in SHOP_CARTRIDGE_PAGES for shop_id in page.shop_ids)
        self.assertEqual(31, len(flattened))
        self.assertEqual(31, len(set(flattened)))
        self.assertEqual(SHOP_PURCHASE_REFERENCES.keys(), set(flattened))

    def test_favorite_rebuild_uses_local_pages_and_records_each_cartridge(self):
        selected = []
        aligned = []
        marked = []
        confirmed = []
        scrolls = []
        built = []
        task = SimpleNamespace(
            log_info=lambda *_args, **_kwargs: None,
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda *_args, **_kwargs: None,
        )
        progress = SimpleNamespace(
            favorite_card_complete=lambda _shop_id: False,
            mark_favorite_card=marked.append,
            mark_favorites_built=lambda: built.append(True),
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.progress = progress
        trader._reset_shop_to_first_page = lambda: True
        trader._wait_for_shop_page = lambda shop_ids: confirmed.append(shop_ids) or True
        trader._scroll_shop_cartridges = lambda scroll_amount, count, interval, after_sleep: (
            scrolls.append((scroll_amount, count, interval, after_sleep))
        )
        trader._select_purchase_cartridge = lambda shop_id: selected.append(shop_id) or True
        trader._align_unfavorited_points = lambda shop_id: aligned.append(shop_id) or True

        self.assertTrue(trader.rebuild_favorites())

        expected = [shop_id for page in SHOP_CARTRIDGE_PAGES for shop_id in page.shop_ids]
        self.assertEqual(expected, selected)
        self.assertEqual(expected, aligned)
        self.assertEqual(expected, marked)
        self.assertEqual(
            [page.confirmation_shop_ids for page in SHOP_CARTRIDGE_PAGES],
            confirmed,
        )
        self.assertEqual(
            [(-1, 9, 0.1, 0.5), (-1, 10, 0.1, 0.5), (-1, 1, 0.1, 0.5)],
            scrolls,
        )
        self.assertEqual([True], built)

    def test_reset_shop_page_scrolls_up_one_step_then_recognizes_again(self):
        task = SimpleNamespace(
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda *_args, **_kwargs: None,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = SimpleNamespace(capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8))
        visible = iter((False, False, True))
        trader._cartridge_visible = lambda _shop_id, _frame: next(visible)
        scrolls = []
        trader._scroll_shop_cartridges = lambda scroll_amount, count, interval, after_sleep: (
            scrolls.append((scroll_amount, count, interval, after_sleep))
        )

        self.assertTrue(trader._reset_shop_to_first_page())
        self.assertEqual([(1, 1, 0.0, 0.5), (1, 1, 0.0, 0.5)], scrolls)

    def test_empty_favorite_point_waits_one_second_before_gray_star_recheck(self):
        clicks = []
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda *_args, **_kwargs: None,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda *_args, **_kwargs: "",
            simplify=lambda value: value,
        )
        states = iter((False, True))
        trader._gray_star_present = lambda _frame, _slot, _point: next(states)

        self.assertTrue(trader._align_unfavorited_points("S1"))
        self.assertEqual([(*SHOP_FAVORITE_POINTS[6], STAR_POST_CLICK_DELAY)], clicks)
        self.assertEqual(1.0, STAR_POST_CLICK_DELAY)

    def test_gray_star_detection_anchors_enlarged_region_at_supplied_point(self):
        captured = []
        point = SHOP_FAVORITE_POINTS[6]
        result = MatchResult(0.99, (900, 240), (24, 24), pixel_score=0.98)
        task = SimpleNamespace(
            config={},
            info_set=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            match=lambda _frame, spec: captured.append(spec) or result,
            passes=lambda value, spec: (
                value.score >= spec.threshold and value.pixel_score >= spec.min_pixel_score
            ),
            star_is_yellow=lambda *_args: False,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = vision

        self.assertTrue(
            trader._gray_star_present(
                np.zeros((1080, 1920, 3), dtype=np.uint8),
                6,
                point,
            )
        )
        spec = captured[0]
        self.assertEqual("shop/cartridges/star_gray.png", spec.file_name)
        self.assertTrue(spec.green_mask)
        self.assertEqual(STAR_TEMPLATE_THRESHOLD, spec.threshold)
        self.assertEqual(STAR_PIXEL_THRESHOLD, spec.min_pixel_score)
        self.assertEqual(
            (
                point[0] - STAR_ROI_HALF_SIZE_X / 1920,
                point[1] - STAR_ROI_HALF_SIZE_Y / 1080,
                point[0] + STAR_ROI_HALF_SIZE_X / 1920,
                point[1] + STAR_ROI_HALF_SIZE_Y / 1080,
            ),
            spec.relative_roi,
        )

    def test_gray_star_search_region_scales_to_cover_offset_at_720p_and_4k(self):
        point = SHOP_FAVORITE_POINTS[6]
        rel_roi = (
            point[0] - STAR_ROI_HALF_SIZE_X / 1920,
            point[1] - STAR_ROI_HALF_SIZE_Y / 1080,
            point[0] + STAR_ROI_HALF_SIZE_X / 1920,
            point[1] + STAR_ROI_HALF_SIZE_Y / 1080,
        )
        # 720p 实机测量：实际灰星中心约 (601,180)，对应标定点 (608,167)。
        offset = (601 / 1280, 180 / 720)
        for size in ((1080, 1920), (720, 1280), (2160, 3840)):
            with self.subTest(size=size):
                frame = np.zeros((size[0], size[1], 3), dtype=np.uint8)
                left, top, region = Vision._relative_roi(frame, rel_roi)
                right = left + region.shape[1]
                bottom = top + region.shape[0]
                expected = (round(size[1] * point[0]), round(size[0] * point[1]))
                actual = (round(size[1] * offset[0]), round(size[0] * offset[1]))
                self.assertTrue(left <= expected[0] < right and top <= expected[1] < bottom)
                self.assertTrue(left <= actual[0] < right and top <= actual[1] < bottom)

    def test_gray_star_wait_accepts_removal_toast_confirmation(self):
        statuses = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        texts = iter(("已将商品甜椒从收藏中移除", ""))
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, _name: next(texts),
            simplify=lambda value: value,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = vision
        trader._gray_star_present = lambda *_args: False

        self.assertTrue(trader._wait_for_gray_star(6, SHOP_FAVORITE_POINTS[6]))
        self.assertIn(("6 取消收藏提示", "已将商品甜椒从收藏中移除"), statuses)

    def test_gray_star_wait_fails_when_toast_reports_added_to_favorites(self):
        warnings = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=warnings.append,
            info_set=lambda *_args: None,
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, _name: "已将商品甜椒加入收藏",
            simplify=lambda value: value,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = vision
        trader._gray_star_present = lambda *_args: False

        self.assertFalse(trader._wait_for_gray_star(6, SHOP_FAVORITE_POINTS[6]))
        self.assertTrue(any("取消收藏未生效" in message for message in warnings))

    def test_gray_star_recognizer_separates_slot_seven_gray_and_yellow_renders(self):
        point = SHOP_FAVORITE_POINTS[7]
        result = {"value": MatchResult(0.857, (1231, 238), (24, 24), 0.937)}
        yellow = {"value": False}
        task = SimpleNamespace(config={}, info_set=lambda *_args, **_kwargs: None)
        vision = SimpleNamespace(
            match=lambda *_args, **_kwargs: result["value"],
            passes=lambda value, spec: (
                value.score >= spec.threshold and value.pixel_score >= spec.min_pixel_score
            ),
            star_is_yellow=lambda *_args, **_kwargs: yellow["value"],
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = vision
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        self.assertTrue(trader._gray_star_present(frame, 7, point))
        result["value"] = MatchResult(0.951, (1231, 238), (24, 24), 0.919)
        yellow["value"] = True
        self.assertFalse(trader._gray_star_present(frame, 7, point))

    def test_shop_cartridge_recognition_and_scroll_use_separate_regions(self):
        trader = object.__new__(Trader)
        spec = trader._cartridge_spec("R2")

        self.assertEqual(
            (228 / 1920, 117 / 1080, 463 / 1920, 959 / 1080),
            SHOP_CARTRIDGE_SCROLL_REGION,
        )
        self.assertEqual(
            (200 / 1920, 70 / 1080, 500 / 1920, 1.0),
            SHOP_CARTRIDGE_RECOGNITION_REGION,
        )
        self.assertAlmostEqual(((228 + 463) / 2) / 1920, SHOP_CARTRIDGE_SCROLL_POINT[0])
        self.assertAlmostEqual(((117 + 959) / 2) / 1080, SHOP_CARTRIDGE_SCROLL_POINT[1])
        self.assertEqual(SHOP_CARTRIDGE_RECOGNITION_REGION, spec.relative_roi)

    def test_shop_cartridge_keeps_strict_local_threshold(self):
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(threshold_for=lambda _spec: 0.72)
        spec = trader._cartridge_spec("E7")

        self.assertFalse(
            trader._cartridge_match_passes(
                MatchResult(0.75, (220, 851), (92, 47)),
                spec,
            )
        )
        self.assertTrue(
            trader._cartridge_match_passes(
                MatchResult(0.80, (220, 851), (92, 47)),
                spec,
            )
        )

    def test_shop_cartridge_competition_and_ocr_reject_old_single_template_false_hit(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        scores = {
            "shop/cartridges/story_cartridge_17.png": 0.981,
            "shop/cartridges/story_cartridge_11.png": 0.858,
            "shop/cartridges/story_cartridge_01.png": 0.794,
        }

        def match_all(_frame, spec, **_kwargs):
            score = scores.get(spec.file_name)
            if score is None:
                return ()
            return (MatchResult(score, (235, 184), (78, 57), pixel_score=0.95),)

        ocr_boxes = [
            SimpleNamespace(
                name="剧情游戏卡 17",
                confidence=0.953,
                x=318,
                y=184,
                width=140,
                height=23,
            ),
            SimpleNamespace(
                name="试炼之路",
                confidence=0.992,
                x=318,
                y=213,
                width=90,
                height=24,
            ),
        ]
        task = SimpleNamespace(
            info_set=lambda *_args, **_kwargs: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = SimpleNamespace(
            match_all=match_all,
            ocr_boxes=lambda *_args, **_kwargs: ocr_boxes,
            threshold_for=lambda _spec: 0.72,
        )

        confirmed = trader._confirmed_shop_cartridge_detections(frame)

        self.assertEqual({"S17"}, confirmed.keys())
        detection = confirmed["S17"]
        self.assertEqual("S11", detection.runner_up.shop_id)
        self.assertAlmostEqual(0.123, detection.margin, places=3)
        self.assertEqual("S17", detection.ocr.shop_id)
        self.assertEqual(1.0, detection.ocr.name_similarity)
        self.assertNotIn("S1", confirmed)

    def test_shop_cartridge_competition_rejects_ocr_id_disagreement(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task = SimpleNamespace(
            info_set=lambda *_args, **_kwargs: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: (
                (MatchResult(0.98, (235, 184), (78, 57), pixel_score=0.95),)
                if spec.file_name == "shop/cartridges/story_cartridge_17.png"
                else ()
            ),
            ocr_boxes=lambda *_args, **_kwargs: [
                SimpleNamespace(
                    name="剧情游戏卡18",
                    confidence=0.99,
                    x=318,
                    y=184,
                    width=140,
                    height=23,
                ),
                SimpleNamespace(
                    name="救赎",
                    confidence=0.99,
                    x=318,
                    y=213,
                    width=55,
                    height=24,
                ),
            ],
            threshold_for=lambda _spec: 0.72,
        )

        self.assertEqual({}, trader._confirmed_shop_cartridge_detections(frame))

    def test_catalog_excludes_pinned_cards(self):
        ids = {card.card_id for card in COLLECTABLE_CARDS}

        self.assertEqual(17, len(ids))
        self.assertTrue(PINNED_CARD_IDS.isdisjoint(ids))
        self.assertNotIn("Q_sp6", ids)
        self.assertNotIn("Q_sp18", ids)
        self.assertNotIn("Q_sp20", ids)
        self.assertEqual(
            set(STORY_COLLECTION_MAPS),
            {card.number for card in COLLECTABLE_CARDS},
        )
        for card in COLLECTABLE_CARDS:
            with self.subTest(card=card.card_id):
                self.assertEqual(
                    [
                        CollectionMapRole.MAIN_AREA,
                        CollectionMapRole.BATTLE_AREA_1,
                        CollectionMapRole.BATTLE_AREA_2,
                    ],
                    [target.role for target in card.targets],
                )
                self.assertEqual(
                    STORY_COLLECTION_MAPS[card.number],
                    tuple(target.title for target in card.targets),
                )

    @staticmethod
    def _area_context(
        text: str,
        target_key: str | None = None,
        *,
        left: bool = False,
        right: bool = False,
        candidate_keys: tuple[str, ...] | None = None,
        teleports: tuple[MatchResult, ...] = (),
    ) -> AreaMapContext:
        match = MatchResult(0.99, (100, 100), (30, 30), pixel_score=0.98)
        keys = (
            candidate_keys if candidate_keys is not None else ((target_key,) if target_key else ())
        )
        return AreaMapContext(
            frame_shape=(1080, 1920, 3),
            raw_text=f"移动魔法阵 {text}",
            normalized_text=f"移动魔法阵{text}",
            is_area_map=True,
            candidate_target_keys=keys,
            resolved_target_key=target_key if len(keys) == 1 else None,
            left_arrow=match if left else None,
            right_arrow=match if right else None,
            teleports=teleports,
            overlap_arrow=None,
            back_button=match,
        )

    def test_area_map_title_resolution_prefers_longest_nested_story_title(self):
        card = CARD_BY_ID["Q_sp1"]

        self.assertEqual(
            (CollectionMapRole.BATTLE_AREA_2.value,),
            Navigator._target_keys_in_text(
                card,
                "移动魔法阵卢戈森林深处",
            ),
        )

    def test_area_map_scan_skips_unknown_pages_and_confirms_target(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        contexts = iter(
            (
                self._area_context("额外安全图", left=True, right=True),
                self._area_context(
                    target.title,
                    target.key,
                    left=True,
                    right=True,
                ),
            )
        )
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator._move_area_map = lambda *_args: next(contexts)

        located, moved, reason = navigator._locate_collection_target(
            card,
            target,
            self._area_context("主城区外页", right=True),
        )

        self.assertTrue(moved)
        self.assertEqual("", reason)
        self.assertEqual(target.key, located.resolved_target_key)

    def test_area_map_scan_stops_on_ambiguous_target_title(self):
        card = CARD_BY_ID["Q_sp1"]
        target = card.targets[1]
        ambiguous = self._area_context(
            "标题歧义",
            right=True,
            candidate_keys=(
                CollectionMapRole.BATTLE_AREA_1.value,
                CollectionMapRole.BATTLE_AREA_2.value,
            ),
        )
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())

        located, moved, reason = navigator._locate_collection_target(card, target, ambiguous)

        self.assertIsNone(located)
        self.assertFalse(moved)
        self.assertIn("多个目标", reason)

    def test_area_map_back_template_uses_recognition_center_without_external_roi(self):
        self.assertEqual("image/green/BackButGe.png", AREA_MAP_BACK_TEMPLATE.file_name)
        self.assertIsNone(AREA_MAP_BACK_TEMPLATE.roi)
        self.assertEqual(0.90, AREA_MAP_BACK_TEMPLATE.threshold)
        self.assertEqual(0.80, AREA_MAP_BACK_TEMPLATE.min_zncc_score)

    def test_area_map_uses_user_confirmed_relative_geometry(self):
        self.assertEqual((289 / 1920, 253 / 1080), AREA_MAP_OPEN_RELATIVE_POINT)
        self.assertEqual(
            (654 / 1920, 946 / 1080, 1268 / 1920, 1021 / 1080),
            TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
        )
        self.assertEqual(TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI, AREA_MAP_TITLE_OCR_RELATIVE_ROI)
        self.assertEqual((136 / 1920, 52 / 1080), TELEPORT_MAP_RETURN_RELATIVE_POINT)

    def test_prepare_collection_main_closes_map_when_initial_title_is_main(self):
        card = CARD_BY_ID["Q_sp1"]
        events = []
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.UNKNOWN,
        )
        navigator._wait_for_collection_teleport_map = lambda _card: self._area_context(
            card.targets[0].title,
            card.targets[0].key,
            right=True,
        )
        navigator.return_teleport_map_to_sandbox = lambda number: (
            events.append(("return", number)) or NavigationResult(True, ScreenState.SANDBOX)
        )
        navigator._reset_collection_teleport_map_to_main = lambda *_args: self.fail(
            "already-main title must not be paged"
        )

        result = navigator.prepare_collection_main_area(card.card_id)

        self.assertTrue(result.success)
        self.assertEqual([("return", 1)], events)

    def test_prepare_collection_main_pages_to_first_title_then_teleports(self):
        card = CARD_BY_ID["Q_sp1"]
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        contexts = iter(
            (
                self._area_context(
                    card.targets[1].title,
                    card.targets[1].key,
                    left=True,
                    right=True,
                ),
                self._area_context(
                    card.targets[0].title,
                    card.targets[0].key,
                    right=True,
                    teleports=(teleport,),
                ),
            )
        )
        moves = []
        clicks = []
        generation_calls = []
        vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            )
        )
        navigator = Navigator(SimpleNamespace(), vision)
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.UNKNOWN,
        )
        navigator._click_teleport_generation = lambda received, shape: (
            generation_calls.append((received, shape)) or True
        )
        initial = self._area_context(
            card.targets[2].title,
            card.targets[2].key,
            left=True,
        )
        navigator._wait_for_collection_teleport_map = lambda _card: initial
        navigator._move_area_map = lambda _card, _context, direction: (
            moves.append(direction) or next(contexts)
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )

        result = navigator.prepare_collection_main_area(card.card_id)

        self.assertTrue(result.success)
        self.assertEqual(["left", "left"], moves)
        self.assertEqual(
            [(teleport.center, (1080, 1920, 3), TELEPORT_MAP_TRAVEL_SETTLE_SECONDS)],
            clicks,
        )
        self.assertEqual([], generation_calls)

    def test_advance_collection_map_moves_back_exactly_one_confirmed_page(self):
        card = CARD_BY_ID["Q_sp1"]
        current, target = card.targets[1:]
        teleport = MatchResult(0.99, (1000, 500), (60, 60), 0.95, 0.93)
        clicks = []
        moves = []
        vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(SimpleNamespace(), vision)
        navigator.open_teleport_map_from_sandbox = lambda: NavigationResult(
            True,
            ScreenState.UNKNOWN,
            "已点击箱庭5号传送阵技能",
            teleport_map_opened_by_skill=True,
        )
        generation_calls = []
        navigator._click_teleport_generation = lambda received, shape: (
            generation_calls.append((received, shape)) or True
        )
        navigator._wait_for_collection_teleport_map = lambda _card: self._area_context(
            current.title,
            current.key,
            left=True,
            right=True,
        )
        navigator._move_area_map = lambda _card, _context, direction: (
            moves.append(direction)
            or self._area_context(
                target.title,
                target.key,
                left=True,
                teleports=(teleport,),
            )
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )

        result = navigator.advance_collection_map(card.card_id, current, target)

        self.assertTrue(result.success)
        self.assertEqual(["right"], moves)
        self.assertEqual([], clicks)
        self.assertEqual([(teleport, (1080, 1920, 3))], generation_calls)

    def test_teleport_map_page_arrows_are_strict_and_directional(self):
        self.assertEqual("image/green/TpMapLeft.png", TELEPORT_MAP_FORWARD_TEMPLATE.file_name)
        self.assertEqual("image/green/TpMapRight.png", TELEPORT_MAP_BACKWARD_TEMPLATE.file_name)
        for spec in (TELEPORT_MAP_FORWARD_TEMPLATE, TELEPORT_MAP_BACKWARD_TEMPLATE):
            with self.subTest(spec=spec.name):
                self.assertEqual(0.95, spec.threshold)
                self.assertEqual(0.85, spec.min_pixel_score)
                self.assertEqual(0.90, spec.min_zncc_score)
                self.assertEqual(0.95, spec.minimum_safe_threshold)
                self.assertIsNone(spec.roi)
                self.assertIsNone(spec.relative_roi)

    def test_sandbox_teleport_skill_is_separate_from_map_skill(self):
        self.assertEqual(
            "image/green/Skill3-4GE.png",
            SANDBOX_TELEPORT_SKILL_TEMPLATE.file_name,
        )
        self.assertEqual(0.95, SANDBOX_TELEPORT_SKILL_TEMPLATE.threshold)
        self.assertEqual(0.85, SANDBOX_TELEPORT_SKILL_TEMPLATE.min_pixel_score)
        self.assertEqual(0.85, SANDBOX_TELEPORT_SKILL_TEMPLATE.min_zncc_score)
        self.assertNotIn(
            SANDBOX_TELEPORT_SKILL_TEMPLATE,
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
        )

    def test_sandbox_map_teleport_template_uses_sandbox_asset_name(self):
        self.assertEqual("箱庭地图传送阵模板", SANDBOX_MAP_TELEPORT_TEMPLATE.name)
        self.assertEqual(
            "image/green/SandboxNviTpCircleMapGE.png",
            SANDBOX_MAP_TELEPORT_TEMPLATE.file_name,
        )
        self.assertNotEqual(
            SANDBOX_MAP_TELEPORT_TEMPLATE.file_name,
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE.file_name,
        )
        for file_name in (
            "image/SandboxTpCircleMap.png",
            "image/green/SandboxNviTpCircleMapGE.png",
            "image/green/SandboxTpCircleMapGE.png",
            "image/green/TpCircleMapNewGE.png",
        ):
            with self.subTest(file_name=file_name):
                self.assertTrue((ROOT / "recognition-assets/template-assets" / file_name).is_file())

    def test_teleport_map_route_prefers_interaction_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        hand = MatchResult(
            0.968,
            (820, 470),
            (44, 43),
            pixel_score=0.92,
            zncc_score=0.90,
        )
        clicks = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            sleep=lambda *_args: self.fail("a passing interaction must click immediately"),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda received, spec: (
                self.assertIs(received, frame)
                or self.assertIs(spec, HAND_TEMPLATE)
                or hand
            ),
            passes=lambda result, spec: result is hand and spec is HAND_TEMPLATE,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "交互按钮已确认传送阵地图",
        )
        navigator._click_sandbox_teleport_skill = lambda: self.fail(
            "interaction route must not click the fifth skill"
        )

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertFalse(result.teleport_map_opened_by_skill)
        self.assertEqual(
            [(hand.center, frame.shape, TELEPORT_INTERACTION_CLICK_DELAY)],
            clicks,
        )

    def test_teleport_map_route_uses_skill_center_when_interaction_is_missing(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        skill = MatchResult(
            0.968,
            (1760, 790),
            (44, 43),
            pixel_score=0.873,
            zncc_score=0.900,
        )
        clicks = []
        task = SimpleNamespace(
            capture_frame=lambda: frame,
            info_set=lambda *_args: None,
            sleep=lambda *_args: self.fail("a passing skill must click immediately"),
        )
        def match(received, spec):
            self.assertIs(received, frame)
            if spec is HAND_TEMPLATE:
                return MatchResult(-1.0, (0, 0), (0, 0))
            self.assertIs(spec, SANDBOX_TELEPORT_SKILL_TEMPLATE)
            return skill

        vision = SimpleNamespace(
            capture=lambda: frame,
            match=match,
            passes=lambda result, spec: (
                result is skill and spec is SANDBOX_TELEPORT_SKILL_TEMPLATE
            ),
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "技能已确认传送阵地图",
        )

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertTrue(result.teleport_map_opened_by_skill)
        self.assertEqual(
            [(skill.center, frame.shape, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_teleport_map_route_fails_without_blind_click_when_skill_is_missing(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        missing = MatchResult(-1.0, (0, 0), (0, 0))
        clicks = []
        fixed_clicks = []
        sleeps = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            sleep=sleeps.append,
            operate_click=lambda *args, **kwargs: fixed_clicks.append((args, kwargs)),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: (
                self.assertIn(spec, (HAND_TEMPLATE, SANDBOX_TELEPORT_SKILL_TEMPLATE))
                or missing
            ),
            passes=lambda *_args: False,
            click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
        )
        navigator = Navigator(task, vision)
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            False,
            ScreenState.SANDBOX,
            "未确认传送阵地图",
        )

        with patch(
            "src.tasks.map_trade.navigator.monotonic",
            side_effect=(100.0, 100.0, 106.0),
        ):
            result = navigator.open_teleport_map_from_sandbox()

        self.assertFalse(result.success)
        self.assertEqual("未可靠识别箱庭5号传送阵技能，已停止打开传送阵地图", result.message)
        self.assertEqual([], clicks)
        self.assertEqual([], fixed_clicks)
        self.assertEqual([SANDBOX_TELEPORT_SKILL_POLL_INTERVAL], sleeps)

    def test_teleport_skill_failure_ocr_is_explicit_and_enters_walk_fallback(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda received, name: (
                self.assertIs(received, frame)
                or self.assertEqual("箱庭5号传送阵技能失败", name)
                or "无法在魔法阵附近使用天赋技能"
            ),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX

        result = navigator._wait_for_sandbox_map_open(
            "箱庭5号传送阵技能",
            detect_skill_failure=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertIn("魔法阵附近", result.message)
        self.assertTrue(
            navigator._sandbox_teleport_skill_failure_matches(
                result.message
            )
        )

    def test_teleport_skill_failure_routes_to_walk_fallback_result(self):
        task = SimpleNamespace(info_set=lambda *_args: None)
        navigator = Navigator(task, SimpleNamespace())
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._click_sandbox_teleport_skill = lambda: True
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            False,
            ScreenState.SANDBOX,
            "箱庭5号传送阵技能失败 OCR命中：无法在魔法阵附近使用天赋技能",
        )
        fallback_calls = []

        def walk_fallback():
            fallback_calls.append(True)
            return NavigationResult(True, ScreenState.AREA_MAP, "已通过徒步回退")

        navigator._walk_to_sandbox_teleport_interaction = walk_fallback

        result = navigator.open_teleport_map_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual([True], fallback_calls)
        self.assertFalse(result.teleport_map_opened_by_skill)
        self.assertIn("徒步回退", result.message)

    def test_walk_fallback_selects_unique_navigation_teleport_then_interacts(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(
            0.96,
            (500, 300),
            (40, 40),
            pixel_score=0.90,
            zncc_score=0.88,
        )
        hand = MatchResult(
            0.97,
            (820, 470),
            (44, 43),
            pixel_score=0.92,
            zncc_score=0.90,
        )
        clicks = []
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)

        def match(received, spec):
            self.assertIs(received, frame)
            self.assertIs(spec, HAND_TEMPLATE)
            return hand

        vision = SimpleNamespace(
            capture=lambda: frame,
            match=match,
            match_all=lambda received, spec, **_kwargs: (
                self.assertIs(received, frame)
                or self.assertIs(spec, SANDBOX_MAP_TELEPORT_TEMPLATE)
                or (teleport,)
            ),
            threshold_for=lambda spec: spec.threshold,
            passes=lambda result, spec: result in (hand, teleport),
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._click_sandbox_navigation_map = lambda: True
        navigator._sandbox_navigation_page_has_keyword = lambda _frame: True
        menu_calls = []

        def click_menu(_frame):
            menu_calls.append(True)
            return len(menu_calls) == 1

        navigator._click_sandbox_navigation_menu_teleport = click_menu
        navigator._click_sandbox_navigation_destination_confirmation = lambda _frame: True
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "徒步交互已确认传送阵地图",
        )

        result = navigator._walk_to_sandbox_teleport_interaction()

        self.assertTrue(result.success)
        self.assertEqual(
            [
                (teleport.center, frame.shape, 3.0),
                (hand.center, frame.shape, TELEPORT_INTERACTION_CLICK_DELAY),
            ],
            clicks,
        )

    def test_walk_fallback_destination_confirmation_clicks_ocr_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        box = SimpleNamespace(name="确认", x=700, y=500, width=120, height=40)
        task = SimpleNamespace(info_set=lambda *_args: None)
        vision = SimpleNamespace(
            ocr_boxes=lambda received, name: (
                self.assertIs(received, frame)
                or self.assertEqual("箱庭徒步导航传送阵确认", name)
                or [box]
            ),
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        self.assertTrue(
            navigator._click_sandbox_navigation_destination_confirmation(frame)
        )
        self.assertEqual([((760, 520), frame.shape, 0.25)], clicks)

    def test_teleport_generation_clicks_unique_white_center_then_generate_box_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        clicks = []
        ocr_frames = []
        boxes = [
            SimpleNamespace(name="生成魔法阵", x=880, y=430, width=120, height=40),
            SimpleNamespace(name="取消", x=740, y=620, width=220, height=48),
            SimpleNamespace(name="生成5", x=990, y=620, width=220, height=48),
        ]
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: ocr_frames.append(frame) or frame,
            ocr_boxes=lambda received, name: (
                self.assertIs(received, frame) or self.assertEqual("传送阵生成确认", name) or boxes
            ),
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        self.assertTrue(navigator._click_teleport_generation(teleport, frame.shape))
        self.assertEqual(
            [
                (teleport.center, frame.shape, 0.5),
                ((1100, 644), frame.shape, TELEPORT_MAP_TRAVEL_SETTLE_SECONDS),
            ],
            clicks,
        )
        self.assertEqual([frame], ocr_frames)

    def test_teleport_generation_rejects_missing_and_selects_strongest_multiple_candidate(self):
        weaker = MatchResult(0.989, (800, 400), (60, 60), 0.932, 0.947)
        stronger = MatchResult(0.994, (1038, 659), (60, 60), 0.949, 0.973)
        task = SimpleNamespace(info_set=lambda *_args: None)
        vision = SimpleNamespace(click_client=lambda *_args, **_kwargs: None)
        navigator = Navigator(task, vision)
        card = CARD_BY_ID["Q_sp1"]
        missing = self._area_context(
            card.targets[1].title,
            card.targets[1].key,
            teleports=(),
        )
        result = navigator._click_collection_destination(card, card.targets[1], missing)
        self.assertFalse(result.success)
        self.assertIn("未识别到", result.message)

        selected = []
        navigator._click_teleport_map_destination = (
            lambda teleport, _shape, opened_by_skill=False: selected.append(teleport) or True
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )
        multiple = self._area_context(
            card.targets[1].title,
            card.targets[1].key,
            teleports=(weaker, stronger),
        )
        result = navigator._click_collection_destination(card, card.targets[1], multiple)
        self.assertTrue(result.success)
        self.assertEqual([stronger], selected)

    def test_teleport_generation_rejects_missing_keyword_without_generate_click(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        clicks = []
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_boxes=lambda *_args: [
                SimpleNamespace(name="生成魔法阵", x=880, y=430, width=120, height=40),
                SimpleNamespace(name="取消", x=740, y=620, width=220, height=48),
            ],
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        with patch(
            "src.tasks.map_trade.navigator.monotonic",
            side_effect=(100.0, 100.0, 109.0),
        ):
            result = navigator._click_teleport_generation(
                teleport,
                frame.shape,
                timeout=TELEPORT_GENERATION_OCR_TIMEOUT,
            )

        self.assertFalse(result)
        self.assertEqual([(teleport.center, frame.shape, 0.5)], clicks)

    def test_teleport_generation_rejects_ambiguous_generate_button(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        teleport = MatchResult(0.99, (800, 400), (60, 60), 0.95, 0.93)
        clicks = []
        task = SimpleNamespace(info_set=lambda *_args: None, sleep=lambda *_args: None)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_boxes=lambda *_args: [
                SimpleNamespace(name="生成魔法阵", x=880, y=430, width=120, height=40),
                SimpleNamespace(name="取消", x=740, y=620, width=220, height=48),
                SimpleNamespace(name="生成5", x=990, y=620, width=220, height=48),
                SimpleNamespace(name="生成5", x=1230, y=620, width=220, height=48),
            ],
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        with patch(
            "src.tasks.map_trade.navigator.monotonic",
            side_effect=(100.0, 100.0, 109.0),
        ):
            result = navigator._click_teleport_generation(
                teleport,
                frame.shape,
                timeout=TELEPORT_GENERATION_OCR_TIMEOUT,
            )

        self.assertFalse(result)
        self.assertEqual([(teleport.center, frame.shape, 0.5)], clicks)

    def test_return_teleport_map_clicks_confirmed_point_and_reuses_stable_sandbox_wait(self):
        clicks = []
        confirmed_numbers = []
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_story_sandbox = lambda number: (
            confirmed_numbers.append(number)
            or NavigationResult(True, ScreenState.SANDBOX, f"Q_sp{number}")
        )

        result = navigator.return_teleport_map_to_sandbox(1)

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertEqual([1], confirmed_numbers)
        self.assertEqual(
            [(*TELEPORT_MAP_RETURN_RELATIVE_POINT, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_return_teleport_map_prefers_recognized_back_button_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        back = MatchResult(0.98, (110, 35), (40, 40), 0.95, 0.92)
        clicks = []
        task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: self.fail(
                "recognized back button must win over the fallback point"
            )
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: (
                back if spec is AREA_MAP_BACK_TEMPLATE else MatchResult(-1.0, (0, 0), (0, 0))
            ),
            passes=lambda result, _spec: result is back,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )

        result = navigator.return_teleport_map_to_sandbox(1)

        self.assertTrue(result.success)
        self.assertEqual(
            [(back.center, frame.shape, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_open_story_quick_switcher_from_sandbox_never_detours_through_home(self):
        fixed_clicks = []
        template_clicks = []
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: fixed_clicks.append((x, y, after_sleep)),
            open_cartridge_quick_switcher=lambda **_kwargs: self.fail(
                "sandbox route must not use the global-home entry"
            ),
        )
        vision = SimpleNamespace(
            click_stable_template=lambda spec, timeout, after_sleep: (
                template_clicks.append((spec, timeout, after_sleep)) or True
            ),
        )
        navigator = Navigator(task, vision)
        navigator.return_home = lambda: self.fail(
            "sandbox route must not return to the global home"
        )
        navigator._wait_for_current_sandbox = lambda: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True

        result = navigator.open_story_quick_switcher_from_sandbox()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.CARD_MENU, result.state)
        self.assertEqual([(QUICK_SWITCH_TEMPLATE, 10.0, 1.0)], template_clicks)
        self.assertEqual([(*STORY_CATEGORY_POINT, 0.5)], fixed_clicks)

    def test_open_story_quick_switcher_from_sandbox_stops_before_click_when_unconfirmed(self):
        task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: self.fail(
                "unconfirmed sandbox must not be clicked"
            )
        )
        vision = SimpleNamespace(
            click_stable_template=lambda *_args, **_kwargs: self.fail(
                "unconfirmed sandbox must not scan quick switch"
            )
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_current_sandbox = lambda: NavigationResult(
            False,
            ScreenState.UNKNOWN,
            "未稳定确认当前剧情卡带箱庭",
        )

        result = navigator.open_story_quick_switcher_from_sandbox()

        self.assertFalse(result.success)
        self.assertIn("未稳定确认", result.message)

    def test_open_story_quick_switcher_reuses_immediately_prior_sandbox_confirmation(self):
        template_clicks = []
        task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_stable_template=lambda spec, timeout, after_sleep: (
                template_clicks.append((spec, timeout, after_sleep)) or True
            ),
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_current_sandbox = lambda: self.fail(
            "the caller already confirmed the same sandbox"
        )
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True

        result = navigator.open_story_quick_switcher_from_sandbox(
            sandbox_already_confirmed=True,
        )

        self.assertTrue(result.success)
        self.assertEqual([(QUICK_SWITCH_TEMPLATE, 10.0, 1.0)], template_clicks)

    def test_current_sandbox_confirmation_requires_consecutive_frames(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        states = iter(
            (
                ScreenState.SANDBOX,
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        captures = []
        navigator = Navigator(
            SimpleNamespace(sleep=lambda *_args: None),
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator.classify = lambda _frame=None: next(states)
        navigator._match_story_sandbox_signals = lambda _frame: SandboxConfirmation(
            2,
            2,
            3,
            1,
        )

        result = navigator._wait_for_current_sandbox(timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertEqual(4, len(captures))

    def test_interaction_button_template_uses_strict_three_score_gates(self):
        self.assertEqual("image/green/IcoHand.png", HAND_TEMPLATE.file_name)
        self.assertEqual(0.95, HAND_TEMPLATE.threshold)
        self.assertEqual(0.90, HAND_TEMPLATE.min_pixel_score)
        self.assertEqual(0.85, HAND_TEMPLATE.min_zncc_score)
        self.assertEqual(0.95, HAND_TEMPLATE.minimum_safe_threshold)

    def test_area_map_entry_uses_skill_center_and_no_fixed_point(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        skill = MatchResult(0.97, (1760, 790), (44, 43), 0.88, 0.90)
        clicks = []
        navigator = object.__new__(Navigator)
        navigator.task = SimpleNamespace(
            info_set=lambda *_args: None,
            operate_click=lambda *args, **kwargs: self.fail(
                f"area-map entry must not use fixed point: {args}, {kwargs}"
            ),
        )
        navigator.vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: (
                self.assertIn(spec, (HAND_TEMPLATE, SANDBOX_TELEPORT_SKILL_TEMPLATE))
                or skill
            ),
            passes=lambda result, spec: (
                result is skill and spec is SANDBOX_TELEPORT_SKILL_TEMPLATE
            ),
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator.classify = lambda: ScreenState.SANDBOX
        navigator._click_sandbox_teleport_interaction = lambda: False
        navigator._wait_for_sandbox_map_open = lambda *_args, **_kwargs: NavigationResult(
            True,
            ScreenState.AREA_MAP,
            "技能已确认传送阵地图",
        )

        result = navigator.ensure_area_map()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.AREA_MAP, result.state)
        self.assertEqual(
            [(skill.center, frame.shape, SANDBOX_MAP_SETTLE_SECONDS)],
            clicks,
        )

    def test_area_map_context_reads_title_roi_and_confirmation_from_same_frame(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []

        class FakeVision:
            @staticmethod
            def simplify(value):
                return value

            @staticmethod
            def ocr_text(received, name, **kwargs):
                ocr_calls.append((received, name, kwargs.get("relative_roi")))
                if name == "区域地图确认":
                    return "移动魔法阵"
                return "卢戈森林深处"

            @staticmethod
            def match(_frame, _spec):
                return MatchResult(-1.0, (0, 0), (0, 0))

            @staticmethod
            def passes(_result, _spec):
                return False

            @staticmethod
            def threshold_for(spec):
                return spec.threshold

            @staticmethod
            def match_all(*_args, **_kwargs):
                return ()

        navigator = Navigator(SimpleNamespace(), FakeVision())
        context = navigator._area_map_context(frame, CARD_BY_ID["Q_sp1"])

        self.assertTrue(context.is_area_map)
        self.assertEqual("卢戈森林深处", context.raw_text)
        self.assertEqual("移动魔法阵", context.confirmation_text)
        self.assertEqual(CollectionMapRole.BATTLE_AREA_2.value, context.resolved_target_key)
        self.assertEqual(2, len(ocr_calls))
        self.assertTrue(all(call[0] is frame for call in ocr_calls))
        self.assertEqual(None, ocr_calls[0][2])
        self.assertEqual("传送阵地图名", ocr_calls[1][1])
        self.assertEqual(TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI, ocr_calls[1][2])

    def test_area_map_teleport_template_is_enabled_only_and_strict(self):
        self.assertIs(
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES[0],
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
        )
        self.assertEqual(1, len(TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES))
        spec = TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE
        self.assertEqual("传送阵地图传送阵", spec.name)
        self.assertEqual("image/green/TpCircleMapNewGE.png", spec.file_name)
        self.assertEqual(0.95, spec.threshold)
        self.assertEqual(0.90, spec.min_pixel_score)
        self.assertEqual(0.85, spec.min_zncc_score)
        self.assertEqual(0.95, spec.minimum_safe_threshold)

        path = ROOT / "recognition-assets/template-assets" / spec.file_name
        template = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        self.assertIsNotNone(template)
        self.assertEqual((50, 54, 3), template.shape)
        green = (
            (template[:, :, 0] == 0)
            & (template[:, :, 1] == 255)
            & (template[:, :, 2] == 0)
        )
        self.assertGreater(np.count_nonzero(green), 0)
        self.assertLess(np.count_nonzero(green), template.shape[0] * template.shape[1])

        skill_spec = TELEPORT_MAP_SKILL_TEMPLATE
        self.assertEqual("传送阵地图传送技能", skill_spec.name)
        self.assertEqual("image/green/TpSkillMapGE.png", skill_spec.file_name)
        self.assertEqual(0.95, skill_spec.threshold)
        self.assertEqual(0.90, skill_spec.min_pixel_score)
        self.assertEqual(0.85, skill_spec.min_zncc_score)
        self.assertEqual(0.95, skill_spec.minimum_safe_threshold)
        self.assertNotIn(skill_spec, TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES)

        skill_path = ROOT / "recognition-assets/template-assets" / skill_spec.file_name
        skill_template = cv2.imread(str(skill_path), cv2.IMREAD_UNCHANGED)
        self.assertIsNotNone(skill_template)
        self.assertEqual((43, 43, 4), skill_template.shape)
        self.assertGreater(np.count_nonzero(skill_template[:, :, 3]), 0)
        self.assertLess(
            np.count_nonzero(skill_template[:, :, 3]),
            skill_template.shape[0] * skill_template.shape[1],
        )

        sandbox_skill = SANDBOX_TELEPORT_SKILL_TEMPLATE
        self.assertEqual("image/green/Skill3-4GE.png", sandbox_skill.file_name)
        self.assertNotIn(sandbox_skill, TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES)

    def test_area_map_teleports_reject_dim_candidate_before_click_context(self):
        frame = np.zeros((300, 500, 3), dtype=np.uint8)
        enabled = MatchResult(0.98, (80, 80), (52, 52), 0.94, 0.93)
        disabled = MatchResult(0.98, (280, 80), (52, 52), 0.94, 0.93)
        cv2.circle(frame, enabled.center, 16, (230, 230, 230), -1)
        cv2.circle(frame, disabled.center, 16, (100, 100, 100), -1)

        class FakeVision:
            @staticmethod
            def threshold_for(_spec):
                return 0.95

            @staticmethod
            def match_all(*_args, **_kwargs):
                return (enabled, disabled)

            @staticmethod
            def passes(_result, _spec):
                return True

        navigator = object.__new__(Navigator)
        navigator.task = SimpleNamespace(info_set=lambda *_args: None)
        navigator.vision = FakeVision()

        self.assertGreater(
            navigator._area_map_teleport_bright_neutral_ratio(frame, enabled),
            AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
        )
        self.assertLess(
            navigator._area_map_teleport_bright_neutral_ratio(frame, disabled),
            AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
        )
        self.assertEqual((enabled,), navigator._area_map_teleports(frame))

    def test_sale_whitelist_allows_only_intersection(self):
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(simplify=lambda value: value)
        trader.task = SimpleNamespace(config={"出售白名单": ""})
        whitelist = trader._sale_whitelist()

        self.assertTrue(trader._entry_allowed(CalendarEntry("透明沙拉", "E1:夏日骑士"), whitelist))
        self.assertTrue(
            trader._entry_allowed(CalendarEntry("透明化沙拉", "E1:夏日骑士"), whitelist)
        )
        self.assertFalse(trader._entry_allowed(CalendarEntry("牛奶", "S2:苍蓝魔女"), whitelist))
        self.assertTrue(trader._entry_allowed(CalendarEntry("黄油", "S2:苍蓝魔女"), whitelist))

    def test_sell_page_switch_uses_given_title_region_and_waits_half_second(self):
        texts = iter(("购买", "出售"))
        ocr_calls = []
        clicks = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda _frame, name, relative_roi: (
                ocr_calls.append((name, relative_roi)) or next(texts)
            ),
            simplify=lambda value: value,
        )

        self.assertTrue(trader._ensure_sell_page())
        self.assertEqual([(*SELL_MODE_POINT, 0.5)], clicks)
        self.assertEqual(
            (226 / 1920, 24 / 1080, 359 / 1920, 80 / 1080),
            SHOP_MODE_TITLE_REGION,
        )
        self.assertEqual(
            [("商店买卖页标题", SHOP_MODE_TITLE_REGION)] * 2,
            ocr_calls,
        )

    def test_buy_and_sell_switches_current_shop_after_full_frame_sold_out_ocr(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        clicks = []
        sleeps = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=sleeps.append,
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )

        def ocr_text(_frame, name, roi=None, relative_roi=None):
            ocr_calls.append((name, roi, relative_roi))
            if name == "买后售罄确认":
                return f"洋葱 {BUY_TO_SELL_SOLD_OUT_KEYWORD}"
            return "出售"

        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=ocr_text,
            simplify=lambda value: value,
        )

        self.assertTrue(trader._switch_from_completed_buy_to_sell())
        self.assertEqual(
            [(*SELL_MODE_POINT, BUY_TO_SELL_POST_CLICK_DELAY)],
            clicks,
        )
        self.assertEqual([BUY_TO_SELL_PRE_CLICK_DELAY], sleeps)
        self.assertEqual(
            ("买后售罄确认", None, None),
            ocr_calls[0],
        )
        self.assertEqual(
            ("商店买卖页标题", None, SHOP_MODE_TITLE_REGION),
            ocr_calls[1],
        )

    def test_run_sell_after_buy_reuses_current_shop_without_home_navigation(self):
        actions = []
        trader = object.__new__(Trader)
        trader._buy_completed_in_current_shop = True
        trader.task = SimpleNamespace(log_info=lambda *_args: None)
        trader.navigator = SimpleNamespace(
            reach_merchant_shop=lambda: self.fail("买卖连续执行时不应重新从主页进商店")
        )
        trader._switch_from_completed_buy_to_sell = lambda: actions.append("switch") or True
        trader.sell_max_price_items = lambda: actions.append("sell") or True

        self.assertTrue(trader.run_sell())
        self.assertEqual(["switch", "sell"], actions)
        self.assertFalse(trader._buy_completed_in_current_shop)

    def test_run_sell_only_enters_default_buy_shop_then_switches_to_sell(self):
        actions = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            log_warning=lambda *_args: self.fail("成功入店时不应记录警告"),
        )
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: actions.append("enter")
            or NavigationResult(True, ScreenState.SHOP),
        )
        trader._ensure_sell_page = lambda: actions.append("sell-page") or True
        trader.sell_max_price_items = lambda: actions.append("sell") or True

        self.assertTrue(trader.run_sell())
        self.assertEqual(["enter", "sell-page", "sell"], actions)

    def test_run_sell_only_stops_and_logs_when_shop_entry_fails(self):
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(log_warning=warnings.append)
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(
                False,
                ScreenState.UNKNOWN,
                "未进入默认购买页",
            ),
        )
        trader._ensure_sell_page = lambda: self.fail("入店失败后不得切出售页")
        trader.sell_max_price_items = lambda: self.fail("入店失败后不得出售")

        self.assertFalse(trader.run_sell())
        self.assertEqual(["卖：未进入默认购买页"], warnings)

    def test_run_sell_only_stops_before_sell_page_when_entry_is_not_shop(self):
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(log_warning=warnings.append)
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(
                True,
                ScreenState.MERCHANT_DIALOG,
                "仍在商人对话",
            ),
        )
        trader._ensure_sell_page = lambda: self.fail("非商店状态不得切出售页")
        trader.sell_max_price_items = lambda: self.fail("非商店状态不得出售")

        self.assertFalse(trader.run_sell())
        self.assertEqual(
            ["卖：进入商店后状态为merchant_dialog，未确认商店页，停止出售。"],
            warnings,
        )

    def test_sell_page_does_not_click_when_already_on_sell(self):
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: self.fail("已经在出售页时不应再次点击"),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda *_args, **_kwargs: "出售",
            simplify=lambda value: value,
        )

        self.assertTrue(trader._ensure_sell_page(timeout=0.0))

    def test_sell_shop_selection_reuses_buy_multitemplate_page_flow(self):
        confirmed = []
        scrolls = []
        selected = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            log_warning=lambda *_args: None,
        )
        trader._reset_shop_to_first_page = lambda: True
        trader._wait_for_shop_page = lambda shop_ids: confirmed.append(shop_ids) or True
        trader._scroll_shop_cartridges = lambda scroll_amount, count, interval, after_sleep: (
            scrolls.append((scroll_amount, count, interval, after_sleep))
        )
        trader._select_purchase_cartridge = lambda shop_id: selected.append(shop_id) or True

        self.assertTrue(trader.select_shop_tab("R2:火晶片"))
        self.assertEqual(
            [page.confirmation_shop_ids for page in SHOP_CARTRIDGE_PAGES[:3]],
            confirmed,
        )
        self.assertEqual(
            [(-1, 9, 0.1, 0.5), (-1, 10, 0.1, 0.5)],
            scrolls,
        )
        self.assertEqual(["R2"], selected)

    def test_run_sell_stops_before_calendar_when_sell_page_is_not_confirmed(self):
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(log_warning=lambda *_args: None)
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(True, ScreenState.SHOP)
        )
        trader._ensure_sell_page = lambda: False
        trader.sell_max_price_items = lambda: self.fail("未确认出售页面时不得加载价表或开始出售")

        self.assertFalse(trader.run_sell())

    def test_locate_sale_item_matches_name_and_120_percent_with_left_offset(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = [
            SimpleNamespace(name="水果罐头", confidence=1.0, x=598, y=451, width=84, height=23),
            SimpleNamespace(name="↑120%", confidence=0.99, x=492, y=451, width=56, height=13),
            SimpleNamespace(name="胡萝卜", confidence=1.0, x=928, y=451, width=62, height=22),
            SimpleNamespace(name="4118%", confidence=0.9, x=818, y=440, width=86, height=38),
        ]
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(
            ocr_boxes=lambda _frame, _name, target_height=720: boxes,
            simplify=lambda value: value,
        )
        trader.task = SimpleNamespace(info_set=lambda *_args: None)

        self.assertEqual(
            (640, 462),
            trader._locate_sale_item(CalendarEntry("水果罐头", "S2:苍蓝魔女"), frame),
        )
        self.assertEqual(115, SALE_ITEM_NAME_LEFT_OFFSET_X)

    def test_locate_sale_item_rejects_when_probe_not_in_120_percent_box(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = [
            SimpleNamespace(name="水果罐头", confidence=1.0, x=598, y=451, width=84, height=23),
            SimpleNamespace(name="120%", confidence=0.99, x=300, y=451, width=40, height=13),
        ]
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(
            ocr_boxes=lambda _frame, _name, target_height=720: boxes,
            simplify=lambda value: value,
        )
        trader.task = SimpleNamespace(info_set=lambda *_args: None)

        self.assertIsNone(
            trader._locate_sale_item(CalendarEntry("水果罐头", "S2:苍蓝魔女"), frame)
        )
        self.assertTrue(trader._last_sale_unavailable)
        self.assertEqual("商品名左侧115参考像素未落在120%框内", trader._last_sale_reason)

    def test_locate_sale_item_scales_left_offset_at_720p(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        boxes = [
            SimpleNamespace(name="水果罐头", confidence=1.0, x=399, y=301, width=56, height=15),
            # 720p：偏移 115*1280/1920≈77；商品名中心(427,308) 左移77 → (350,308)。
            SimpleNamespace(name="120%", confidence=0.99, x=328, y=301, width=37, height=9),
        ]
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(
            ocr_boxes=lambda _frame, _name, target_height=900: boxes,
            simplify=lambda value: value,
        )
        trader.task = SimpleNamespace(info_set=lambda *_args: None)

        self.assertEqual(
            (427, 308),
            trader._locate_sale_item(CalendarEntry("水果罐头", "S2:苍蓝魔女"), frame),
        )

    def test_locate_sale_items_returns_all_same_item_in_reading_order(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = [
            SimpleNamespace(name="兽肉", x=598, y=560, width=44, height=23),
            SimpleNamespace(name="↑120%", x=492, y=562, width=56, height=14),
            SimpleNamespace(name="兽肉", x=928, y=560, width=44, height=23),
            SimpleNamespace(name="↑120%", x=820, y=562, width=56, height=14),
            SimpleNamespace(name="兽肉", x=1262, y=560, width=43, height=23),
            SimpleNamespace(name="↑120%", x=1154, y=562, width=56, height=14),
            SimpleNamespace(name="兽肉", x=1594, y=560, width=42, height=23),
            SimpleNamespace(name="↑120%", x=1485, y=562, width=56, height=14),
        ]
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(
            ocr_boxes=lambda *_args, **_kwargs: boxes,
            simplify=lambda value: value,
        )
        trader.task = SimpleNamespace(info_set=lambda *_args: None)

        candidates = trader._locate_sale_items(
            CalendarEntry("兽肉", "S3:迷雾神射手"), frame
        )

        self.assertEqual(
            [(620, 572), (950, 572), (1284, 572), (1615, 572)],
            [candidate.center for candidate in candidates],
        )

    def test_locate_sale_items_rejects_ambiguous_one_to_many_pairing(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = [
            SimpleNamespace(name="兽肉", x=598, y=560, width=44, height=23),
            SimpleNamespace(name="兽肉", x=650, y=560, width=44, height=23),
            SimpleNamespace(name="↑120%", x=480, y=550, width=220, height=40),
        ]
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(
            ocr_boxes=lambda *_args, **_kwargs: boxes,
            simplify=lambda value: value,
        )
        trader.task = SimpleNamespace(info_set=lambda *_args: None)

        self.assertEqual(
            [], trader._locate_sale_items(CalendarEntry("兽肉", "S3"), frame)
        )
        self.assertEqual("商品名左侧115参考像素未落在120%框内", trader._last_sale_reason)

    def test_locate_sale_items_keeps_valid_pair_when_another_name_has_no_pair(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = [
            SimpleNamespace(name="兽肉", x=598, y=560, width=44, height=23),
            SimpleNamespace(name="兽肉", x=928, y=560, width=44, height=23),
            SimpleNamespace(name="↑120%", x=492, y=562, width=56, height=14),
        ]
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(
            ocr_boxes=lambda *_args, **_kwargs: boxes,
            simplify=lambda value: value,
        )
        trader.task = SimpleNamespace(info_set=lambda *_args: None)

        candidates = trader._locate_sale_items(
            CalendarEntry("兽肉", "S3"), frame
        )

        self.assertEqual([(620, 572)], [candidate.center for candidate in candidates])

    def test_sale_item_without_matching_row_marks_item_unavailable(self):
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: None,
            sleep=lambda *_args: None,
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )

        def fail_wait(_entry):
            trader._last_sale_unavailable = True
            trader._last_sale_reason = "全画面OCR未识别到120%"
            return None

        trader._wait_sale_item_candidates = fail_wait

        self.assertFalse(trader._sell_selected_entry(CalendarEntry("豆子", "S12:海边天使")))
        self.assertTrue(trader._last_sale_unavailable)
        self.assertEqual("全画面OCR未识别到120%", trader._last_sale_reason)

    def test_normal_sale_clicks_located_item_name_then_uses_max_and_sell(self):
        clicks = []
        client_clicks = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            config={"出售保险": False},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            )
        )
        candidates = [SimpleNamespace(center=(640, 462))]
        scans = [([candidates[0]], frame), None]
        trader._wait_sale_item_candidates = lambda _entry: scans.pop(0)
        trader._sale_name_signature = lambda _entry, _frame: ()
        trader._wait_sale_dialog_item = lambda _entry: True
        trader._wait_owned_quantity = lambda: 400
        trader._wait_available_quantity = lambda: 400
        trader._wait_selected_sale_quantity = lambda _expected: True
        trader._wait_sale_completion = lambda *_args, **_kwargs: True

        self.assertTrue(
            trader._sell_selected_entry(CalendarEntry("甜辣酱", "S10:霍尔蒙克斯"))
        )
        self.assertEqual([((640, 462), frame.shape, 0.5)], client_clicks)
        self.assertEqual(
            [
                (*SALE_MAX_POINT, 0.5),
                (*SALE_CONFIRM_POINT, 0.5),
            ],
            clicks,
        )

    def test_sell_selected_entry_rescans_after_each_completed_sale(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        first = SimpleNamespace(center=(620, 572))
        second = SimpleNamespace(center=(620, 572))
        scans = [([first], frame), ([second], frame), None]
        clicked = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(log_info=lambda *_args: None)
        trader._wait_sale_item_candidates = lambda _entry: scans.pop(0)

        def sell_one(_entry, candidate, _frame, **_kwargs):
            clicked.append(candidate.center)
            return 240524 - len(clicked), True

        trader._sell_one_candidate = sell_one

        self.assertTrue(
            trader._sell_selected_entry(CalendarEntry("兽肉", "S3"))
        )
        self.assertEqual([(620, 572), (620, 572)], clicked)
        self.assertEqual([], scans)

    def test_wait_sale_item_point_retries_until_located(self):
        sleeps = []
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            sleep=sleeps.append,
            log_warning=warnings.append,
        )
        frames = [np.zeros((1080, 1920, 3), dtype=np.uint8) for _ in range(2)]
        calls = []
        trader.vision = SimpleNamespace(
            capture=lambda: frames[min(len(calls), 1)]
        )
        trader._locate_sale_item = lambda _entry, _frame: (
            calls.append(_frame) or (None if len(calls) < 2 else (640, 462))
        )

        located = trader._wait_sale_item_point(
            CalendarEntry("水果罐头", "S2:苍蓝魔女"),
            timeout=5.0,
            interval=0.1,
        )
        self.assertEqual(((640, 462), frames[1]), located)
        self.assertEqual(1, len(sleeps))
        self.assertEqual([], warnings)

    def test_butter_reserve_uses_proportional_slider_point(self):
        clicks = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            config={"出售保险": False},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            log_info=lambda *_args: None,
            info_set=lambda *_args: None,
        )

        self.assertTrue(
            trader._choose_sale_quantity(
                CalendarEntry("黄油", "S2:苍蓝魔女", reserve=5500),
                owned=8400,
            )
        )
        left, top, right, bottom = SALE_SLIDER_REGION
        ratio = (2900 - 1) / (8400 - 1)
        self.assertEqual(1, len(clicks))
        self.assertAlmostEqual(left + ((right - left) * ratio), clicks[0][0])
        self.assertAlmostEqual((top + bottom) / 2, clicks[0][1])
        self.assertEqual(0.5, clicks[0][2])

    def test_sale_slider_left_edge_represents_selling_one_item(self):
        left, top, _right, bottom = SALE_SLIDER_REGION

        self.assertEqual(
            (left, (top + bottom) / 2),
            Trader._sale_slider_point(owned=5501, reserve=5500),
        )
        self.assertIsNone(Trader._sale_slider_point(owned=5500, reserve=5500))

    def test_sale_dialog_owned_quantity_uses_given_region(self):
        calls = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(sleep=lambda *_args: None)
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda _frame, name, relative_roi: (
                calls.append((name, relative_roi)) or "拥有 8,400 个"
            ),
            simplify=lambda value: value,
        )

        self.assertEqual(8400, trader._wait_owned_quantity(timeout=0.0))
        self.assertEqual([("出售弹窗库存", SALE_DIALOG_REGION)], calls)

    def test_sale_dialog_separates_owned_and_available_quantities(self):
        calls = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(sleep=lambda *_args: None)
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda _frame, name, relative_roi: (
                calls.append((name, relative_roi))
                or "兽肉 拥有335,005个 1个 可购买94,481个"
            ),
            simplify=lambda value: value,
        )

        self.assertEqual(335005, trader._wait_owned_quantity(timeout=0.0))
        self.assertEqual(94481, trader._wait_available_quantity(timeout=0.0))
        self.assertEqual(
            94481,
            Trader._selected_quantity_from_text(
                "兽肉 拥有335,005个 94481个 可购买94481个"
            ),
        )
        self.assertEqual(
            [
                ("出售弹窗库存", SALE_DIALOG_REGION),
                ("出售弹窗可购买数量", SALE_DIALOG_REGION),
            ],
            calls,
        )

    def test_sale_completion_ignores_previous_transaction_toast(self):
        frames = [
            np.zeros((1080, 1920, 3), dtype=np.uint8),
            np.zeros((1080, 1920, 3), dtype=np.uint8),
        ]
        toast_texts = iter(["交易差价5 完成!", "交易差价6 完成!"])
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            sleep=lambda *_args: None,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frames[0],
            ocr_text=lambda _frame, name, **_kwargs: (
                ""
                if name == "出售弹窗完成确认"
                else next(toast_texts)
            ),
            simplify=lambda value: value,
            ocr_boxes=lambda *_args, **_kwargs: [],
        )

        self.assertTrue(
            trader._wait_sale_completion(
                CalendarEntry("兽肉", "S3"),
                frames[0],
                (("兽肉", 620, 560, 44, 23),),
                timeout=1.0,
                before_toast_id=5,
            )
        )

    def test_rare_items_are_skipped_and_same_shop_is_selected_only_once(self):
        selected = []
        sold = []
        logs = []
        entries = (
            CalendarEntry("魅惑粉末", "S6:异教塔", sell=False),
            CalendarEntry("甜辣酱", "S10:霍尔蒙克斯"),
            CalendarEntry("藏红花", "S10:霍尔蒙克斯"),
        )
        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 18)
        trader.calendar_client = SimpleNamespace(
            load=lambda **_kwargs: SimpleNamespace(
                source="bundled",
                entries_for=lambda _day: entries,
            )
        )
        trader.task = SimpleNamespace(
            config={
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": "",
            },
            log_info=logs.append,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader._sale_whitelist = lambda: set()
        trader._entry_allowed = lambda _entry, _whitelist: True
        trader.select_shop_tab = lambda shop: selected.append(shop) or True
        trader._sell_selected_entry = lambda entry: sold.append(entry.item) or True

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(["S10:霍尔蒙克斯"], selected)
        self.assertEqual(["甜辣酱", "藏红花"], sold)
        self.assertIn("卖：魅惑粉末标记为不出售，跳过。", logs)

    def test_disabled_sale_whitelist_sells_all_allowed_calendar_entries(self):
        sold = []
        logs = []
        statuses = []
        entries = (
            CalendarEntry("番茄", "S1:血骑士"),
            CalendarEntry("魅惑粉末", "S6:异教塔", sell=False),
            CalendarEntry("大麦", "S18:救赎"),
        )
        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 4)
        trader.calendar_client = SimpleNamespace(
            load=lambda **_kwargs: SimpleNamespace(
                source="bundled",
                entries_for=lambda _day: entries,
            )
        )
        trader.task = SimpleNamespace(
            config={
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": "",
                "使用出售白名单": False,
                "出售白名单": "番茄",
            },
            log_info=logs.append,
            log_warning=lambda *_args: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        trader.select_shop_tab = lambda _shop: True
        trader._sell_selected_entry = lambda entry: sold.append(entry.item) or True

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(["番茄", "大麦"], sold)
        self.assertIn(("出售白名单", "关闭"), statuses)
        self.assertIn("卖：出售白名单已关闭，执行价表中全部允许出售的商品。", logs)

    def test_enabled_sale_blacklist_excludes_matching_allowed_entry(self):
        sold = []
        logs = []
        statuses = []
        entries = (
            CalendarEntry("番茄", "S1:血骑士"),
            CalendarEntry("大麦", "S18:救赎"),
        )
        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 4)
        trader.calendar_client = SimpleNamespace(
            load=lambda **_kwargs: SimpleNamespace(
                source="bundled",
                entries_for=lambda _day: entries,
            )
        )
        trader.task = SimpleNamespace(
            config={
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": "",
                "使用出售白名单": False,
                "使用出售黑名单": True,
                "出售黑名单": "大麦",
            },
            log_info=logs.append,
            log_warning=lambda *_args: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        trader.vision = SimpleNamespace(simplify=lambda value: value)
        trader.select_shop_tab = lambda _shop: True
        trader._sell_selected_entry = lambda entry: sold.append(entry.item) or True

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(["番茄"], sold)
        self.assertIn(("出售黑名单", "开启"), statuses)
        self.assertIn("卖：大麦命中出售黑名单，跳过。", logs)

    def test_missing_120_percent_item_is_reported_and_does_not_stop_next_item(self):
        statuses = []
        warnings = []
        attempted = []
        entries = (
            CalendarEntry("豆子", "S12:海边天使"),
            CalendarEntry("小麦", "S12:海边天使"),
        )
        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 21, 12, tzinfo=UTC_PLUS_8)
        trader.calendar_client = SimpleNamespace(
            load=lambda **_kwargs: SimpleNamespace(
                source="bundled",
                entries_for=lambda _day: entries,
            )
        )
        trader.task = SimpleNamespace(
            config={
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": "",
            },
            log_info=lambda *_args: None,
            log_warning=warnings.append,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        trader.vision = SimpleNamespace(simplify=lambda value: value)
        trader._sale_whitelist = lambda: set()
        trader._entry_allowed = lambda _entry, _whitelist: True
        trader.select_shop_tab = lambda _shop: True

        def sell(entry):
            attempted.append(entry.item)
            trader._last_sale_unavailable = entry.item == "豆子"
            trader._last_sale_reason = (
                "未发现120%，可能无货或已经售出" if trader._last_sale_unavailable else ""
            )
            return not trader._last_sale_unavailable

        trader._sell_selected_entry = sell

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(["豆子", "小麦"], attempted)
        self.assertIn(
            ("未出售商品", "豆子（未发现120%，可能无货或已经售出）"),
            statuses,
        )
        self.assertIn(
            "未出售商品：豆子（未发现120%，可能无货或已经售出）",
            warnings,
        )

    def test_sale_execution_failure_stops_following_calendar_entry(self):
        attempted = []
        entries = (
            CalendarEntry("豆子", "S12:海边天使"),
            CalendarEntry("小麦", "S12:海边天使"),
        )
        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 21, 12, tzinfo=UTC_PLUS_8)
        trader.calendar_client = SimpleNamespace(
            load=lambda **_kwargs: SimpleNamespace(
                source="bundled",
                entries_for=lambda _day: entries,
            )
        )
        trader.task = SimpleNamespace(
            config={
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": "",
            },
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader._sale_whitelist = lambda: set()
        trader._entry_allowed = lambda _entry, _whitelist: True
        trader.select_shop_tab = lambda _shop: True

        def fail(entry):
            attempted.append(entry.item)
            trader._last_sale_unavailable = False
            trader._last_sale_reason = "出售完成确认超时"
            return False

        trader._sell_selected_entry = fail

        self.assertFalse(trader.sell_max_price_items())
        self.assertEqual(["豆子"], attempted)

    def test_map_trade_sources_do_not_call_keyboard_interfaces(self):
        sources = [
            ROOT / "src" / "tasks" / "MapTradeTask.py",
            ROOT / "src" / "tasks" / "MapCollectionTask.py",
        ]
        sources.extend((ROOT / "src" / "tasks" / "map_trade").glob("*.py"))
        forbidden_calls = {"send_key", "key_down", "key_up", "press_key"}
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            with self.subTest(source=source.name):
                self.assertTrue(forbidden_calls.isdisjoint(called))

    def test_card_and_recipe_templates_are_packaged(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        templates = [card.template for card in STORY_CARDS]
        templates.extend(RECIPE_TEMPLATES.values())
        templates.extend(
            [
                QUICK_SWITCH_TEMPLATE.file_name,
                MERCHANT_CLICK_LOCATION_TEMPLATE.file_name,
            ]
        )
        templates.extend(spec.file_name for _number, spec in STORY_BADGE_SPECS)

        for relative_path in templates:
            with self.subTest(template=relative_path):
                self.assertTrue((template_root / relative_path).is_file())

    def test_daily_trade_ignores_legacy_cooking_switch(self):
        actions = []
        task = object.__new__(MapTradeTask)
        task.config = {
            "启用": True,
            "买": True,
            "卖": True,
            "制作料理": True,
            "料理制作周期": "每周",
            "料理保险": True,
            "5星料理": [],
        }
        task.info_set = lambda *_args: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_error = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task._save_diagnostic = lambda *_args: None

        class FakeProgress:
            def __init__(self):
                self.now_provider = lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8)

            def load(self):
                return None

        class FakeNavigator:
            def __init__(self, *_args):
                pass

            def return_home(self):
                actions.append("home")
                return NavigationResult(True, ScreenState.HOME)

        class FakeTrader:
            def __init__(self, *_args):
                pass

            def run_buy(self):
                actions.append("buy")
                return True

            def run_sell(self):
                actions.append("sell")
                return True

            def run_cooking(self):
                actions.append("cooking")
                return True

        with (
            patch.object(map_trade_task_module, "Vision", lambda *_args: object()),
            patch.object(map_trade_task_module, "ProgressStore", FakeProgress),
            patch.object(map_trade_task_module, "Navigator", FakeNavigator),
            patch.object(map_trade_task_module, "Trader", FakeTrader),
        ):
            self.assertTrue(MapTradeTask.run(task))

        self.assertEqual(["buy", "sell", "home"], actions)

    def test_buy_entry_uses_home_quick_switch_and_merchant_template(self):
        clicks = []
        client_clicks = []
        template_clicks = []
        shop_entry_attempts = []
        keyword_checks = []
        shop_confirm_checks = []
        sleeps = []

        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda seconds: sleeps.append(seconds),
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace()
        badge_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge_detection = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                Q_SP6_STORY_NUMBER,
                MatchResult(0.99, (80, 930), (30, 28), pixel_score=0.98),
            ),
            runner_up=StoryBadgeCandidate(
                8,
                MatchResult(0.80, (81, 930), (31, 31), pixel_score=0.82),
            ),
        )

        def click_template(spec, timeout, after_sleep):
            template_clicks.append((spec, timeout, after_sleep))
            return True

        vision.click_stable_template = click_template
        vision.click_client = lambda point, frame_shape, after_sleep=0: client_clicks.append(
            (point, frame_shape, after_sleep)
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True
        navigator._wait_for_story_badge = lambda _number: (badge_frame, badge_detection)
        shop_entry_results = iter((False, True))
        navigator._enter_q_sp6_shop = lambda timeout, *, log_timeout: (
            shop_entry_attempts.append((timeout, log_timeout)) or next(shop_entry_results)
        )
        navigator._wait_for_ocr_keywords = lambda keywords, timeout, name: (
            keyword_checks.append((keywords, timeout, name)) or True
        )
        navigator._wait_for_bargain_shop_confirmation = lambda: (
            shop_confirm_checks.append(True) or True
        )

        def open_quick_switcher(**callbacks):
            return (
                callbacks["ensure_home"]()
                and callbacks["click_quick_switch"]()
                and callbacks["confirm_quick_switch_page"]()
            )

        task.open_cartridge_quick_switcher = open_quick_switcher

        result = navigator.enter_q_sp6_buy_flow()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SHOP, result.state)
        self.assertEqual([True], shop_confirm_checks)
        self.assertEqual(
            [(QUICK_SWITCH_TEMPLATE, 10.0, 1.0)],
            template_clicks,
        )
        self.assertEqual(
            [
                (Q_SP6_SHOP_PRIORITY_TIMEOUT, False),
                (45.0, True),
            ],
            shop_entry_attempts,
        )
        self.assertEqual(
            [
                (*STORY_CATEGORY_POINT, 0.5),
                (*BARGAIN_POINT, 0.0),
                (*BARGAIN_CONFIRM_POINT, 0.0),
            ],
            clicks,
        )
        self.assertEqual(
            [
                (badge_detection.best.result.center, badge_frame.shape, 0.0),
            ],
            client_clicks,
        )
        self.assertEqual(
            [
                (("砍价",), Q_SP6_BARGAIN_OCR_TIMEOUT, "砍价入口"),
                (("使用砍价技能后可享受商店折扣价",), 10.0, "砍价说明"),
            ],
            keyword_checks,
        )
        self.assertEqual([Q_SP6_BARGAIN_RECHECK_DELAY], sleeps)

    def test_buy_entry_uses_initial_merchant_template_before_home_navigation(self):
        clicks = []
        shop_entry_attempts = []
        keyword_checks = []
        shop_confirm_checks = []
        sleeps = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda seconds: sleeps.append(seconds),
            log_warning=lambda *_args, **_kwargs: None,
            open_cartridge_quick_switcher=lambda **_kwargs: self.fail(
                "initial merchant template hit must bypass HOME navigation"
            ),
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator._enter_q_sp6_shop = lambda timeout, *, log_timeout: (
            shop_entry_attempts.append((timeout, log_timeout)) or True
        )
        navigator._wait_for_ocr_keywords = lambda keywords, timeout, name: (
            keyword_checks.append((keywords, timeout, name)) or True
        )
        navigator._wait_for_bargain_shop_confirmation = lambda: (
            shop_confirm_checks.append(True) or True
        )

        result = navigator.enter_q_sp6_buy_flow()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SHOP, result.state)
        self.assertEqual([True], shop_confirm_checks)
        self.assertEqual(
            [(Q_SP6_SHOP_PRIORITY_TIMEOUT, False)],
            shop_entry_attempts,
        )
        self.assertEqual(
            [
                (*BARGAIN_POINT, 0.0),
                (*BARGAIN_CONFIRM_POINT, 0.0),
            ],
            clicks,
        )
        self.assertEqual(
            [
                (("砍价",), Q_SP6_BARGAIN_OCR_TIMEOUT, "砍价入口"),
                (("使用砍价技能后可享受商店折扣价",), 10.0, "砍价说明"),
            ],
            keyword_checks,
        )
        self.assertEqual([Q_SP6_BARGAIN_RECHECK_DELAY], sleeps)

    def test_buy_entry_does_not_click_bargain_before_bargain_ocr(self):
        clicks = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator._enter_q_sp6_shop = lambda *_args, **_kwargs: True
        navigator._wait_for_ocr_keywords = lambda keywords, *_args, **_kwargs: keywords != ("砍价",)
        navigator.classify = lambda: ScreenState.MERCHANT_DIALOG

        result = navigator.enter_q_sp6_buy_flow()

        self.assertFalse(result.success)
        self.assertEqual("商店页面未识别到砍价入口", result.message)
        self.assertEqual([], clicks)

    def test_buy_entry_stops_when_shop_page_is_not_confirmed_after_bargain(self):
        clicks = []
        shop_confirm_checks = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator._enter_q_sp6_shop = lambda *_args, **_kwargs: True
        navigator._wait_for_ocr_keywords = lambda *_args, **_kwargs: True
        navigator._wait_for_bargain_shop_confirmation = lambda: (
            shop_confirm_checks.append(True) or False
        )
        navigator.classify = lambda: ScreenState.SHOP

        result = navigator.enter_q_sp6_buy_flow()

        self.assertFalse(result.success)
        self.assertEqual("砍价确认后未通过OCR确认商店页面", result.message)
        self.assertEqual(
            [(*BARGAIN_POINT, 0.0), (*BARGAIN_CONFIRM_POINT, 0.0)],
            clicks,
        )
        self.assertEqual([True], shop_confirm_checks)

    def test_bargain_shop_confirmation_requires_popup_closed_and_stable_hits(self):
        texts = iter(
            (
                "仓库 严加管理 砍价成功率100% 取消",
                "BROWN DUST II",
                "仓库管理石怪 仓库 严加管理 天赋技能",
                "仓库管理石怪 仓库 严加管理 天赋技能",
            )
        )
        sleeps = []
        statuses = []
        task = SimpleNamespace(
            config={},
            sleep=sleeps.append,
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, _name: next(texts),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertTrue(navigator._wait_for_bargain_shop_confirmation(timeout=5.0))
        self.assertEqual(("砍价后商店页面 OCR稳定", "2/2"), statuses[-1])
        self.assertEqual(3, len(sleeps))

    def test_bargain_shop_confirmation_times_out_when_popup_never_closes(self):
        texts = iter(["仓库 严加管理 砍价成功率100% 取消"] * 20)
        warnings = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=warnings.append,
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, _name: next(texts),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertFalse(navigator._wait_for_bargain_shop_confirmation(timeout=0.0))
        self.assertTrue(warnings)

    def test_buy_shop_entry_clicks_new_template_center_without_shop_ocr_gate(self):
        client_clicks = []
        matched_specs = []
        warnings = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            sleep=lambda *_args: None,
            log_warning=lambda message: warnings.append(message),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda *_args, **_kwargs: self.fail(
                "merchant click step must not call OCR"
            ),
            match=lambda _frame, spec: (
                matched_specs.append(spec)
                or MatchResult(
                    0.99,
                    (1151, 239),
                    (30, 28),
                    pixel_score=0.98,
                    zncc_score=0.98,
                )
            ),
            passes=lambda result, spec: (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
                and result.zncc_score >= spec.min_zncc_score
            ),
            click_client=lambda point, frame_shape, after_sleep=0: client_clicks.append(
                (point, frame_shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        self.assertTrue(navigator._enter_q_sp6_shop(5.0, log_timeout=True))
        self.assertEqual(
            [
                ((1166, 253), frame.shape, 0.0),
            ],
            client_clicks,
        )
        self.assertEqual([MERCHANT_CLICK_LOCATION_TEMPLATE], matched_specs)
        self.assertEqual([], warnings)

    def test_buy_entry_uses_six_quick_page_labels_and_story_badge_templates(self):
        self.assertEqual(
            (
                "店长游戏卡",
                "剧情游戏卡",
                "角色游戏卡",
                "玩法游戏卡",
                "最近",
                "活动游戏卡",
            ),
            QUICK_SWITCH_PAGE_KEYWORDS,
        )
        self.assertEqual((557 / 1920, 877 / 1080), STORY_CATEGORY_POINT)
        self.assertEqual(6, Q_SP6_STORY_NUMBER)
        self.assertEqual((0.0, 908 / 1080, 1.0, 1.0), QUICK_SWITCH_CARTRIDGE_REGION)
        self.assertEqual(tuple(range(1, 21)), tuple(value[0] for value in STORY_BADGE_SPECS))
        self.assertEqual(
            "quick_switch_cartridges/story_cartridge_badge_06.png",
            STORY_BADGE_SPECS[5][1].file_name,
        )
        self.assertTrue(
            all(spec.relative_roi == QUICK_SWITCH_CARTRIDGE_REGION for _, spec in STORY_BADGE_SPECS)
        )
        self.assertTrue(all(not spec.green_mask for _, spec in STORY_BADGE_SPECS))
        self.assertTrue(
            all(
                spec.min_zncc_score == STORY_BADGE_CANDIDATE_ZNCC_SCORE
                for _, spec in STORY_BADGE_SPECS
            )
        )
        self.assertTrue(all(spec.scale_ratios == (1.0,) for _, spec in STORY_BADGE_SPECS))
        template_root = ROOT / "recognition-assets" / "template-assets"
        for _number, spec in STORY_BADGE_SPECS:
            template = cv2.imread(
                str(template_root / spec.file_name),
                cv2.IMREAD_UNCHANGED,
            )
            self.assertIsNotNone(template, spec.file_name)
            self.assertEqual((29, 29, 4), template.shape, spec.file_name)
            self.assertGreater(np.count_nonzero(template[:, :, 3] == 0), 0)
            self.assertGreater(np.count_nonzero(template[:, :, 3] == 255), 0)
            self.assertTrue(np.all(template[[0, 0, -1, -1], [0, -1, 0, -1], 3] == 0))
        self.assertEqual((191 / 1920, 900 / 1080), BARGAIN_POINT)
        self.assertEqual((1047 / 1920, 652 / 1080), BARGAIN_CONFIRM_POINT)
        self.assertEqual("image/green/QuickSwitchPlayIco.png", QUICK_SWITCH_TEMPLATE.file_name)
        self.assertEqual((0.25, 0.85, 0.65, 1.0), QUICK_SWITCH_TEMPLATE.relative_roi)
        self.assertEqual((0.95, 0.975, 1.0, 1.025, 1.05), QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertEqual(0.85, QUICK_SWITCH_TEMPLATE.min_pixel_score)
        self.assertEqual(0.88, QUICK_SWITCH_TEMPLATE.minimum_safe_threshold)
        self.assertEqual(0.85, QUICK_SWITCH_TEMPLATE.min_zncc_score)
        self.assertIsNotNone(QUICK_SWITCH_TEMPLATE.candidate_center_roi)
        self.assertTrue(all(spec.min_pixel_score == 0.80 for spec in HOME_TEMPLATES))

    def test_merchant_interaction_uses_location_match_center(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(
            0.935,
            (1229, 245),
            (55, 40),
            pixel_score=0.952,
            zncc_score=0.935,
        )
        clicks = []
        statuses = []

        def passes(result, spec):
            return (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
                and result.zncc_score >= spec.min_zncc_score
            )

        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda captured, spec: (
                self.assertIs(spec, MERCHANT_CLICK_LOCATION_TEMPLATE) or match
            ),
            passes=passes,
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)

        self.assertTrue(
            navigator._click_merchant_interaction(timeout=1.0, after_sleep=1.2)
        )
        self.assertEqual([((1256, 265), frame.shape, 1.2)], clicks)
        self.assertEqual(
            "MerchantClickLocation.png",
            MERCHANT_CLICK_LOCATION_TEMPLATE.file_name,
        )
        self.assertFalse(MERCHANT_CLICK_LOCATION_TEMPLATE.green_mask)
        self.assertEqual(0.90, MERCHANT_CLICK_LOCATION_TEMPLATE.threshold)
        self.assertEqual(0.90, MERCHANT_CLICK_LOCATION_TEMPLATE.min_pixel_score)
        self.assertEqual(
            0.90,
            MERCHANT_CLICK_LOCATION_TEMPLATE.minimum_safe_threshold,
        )
        self.assertEqual(0.90, MERCHANT_CLICK_LOCATION_TEMPLATE.min_zncc_score)
        self.assertEqual(
            (0.90, 0.95, 1.0, 1.05, 1.10),
            MERCHANT_CLICK_LOCATION_TEMPLATE.scale_ratios,
        )
        self.assertAlmostEqual(
            1.0,
            offline_template_scale(
                MERCHANT_CLICK_LOCATION_TEMPLATE.file_name,
                1920,
                1080,
            ),
        )
        self.assertAlmostEqual(
            2 / 3,
            offline_template_scale(
                MERCHANT_CLICK_LOCATION_TEMPLATE.file_name,
                1280,
                720,
            ),
        )
        self.assertEqual(
            (
                "商人点击位置",
                "pass; match=0.935; pixel=0.952; zncc=0.935",
            ),
            statuses[-2],
        )
        self.assertEqual(
            ("商人交互点击位置", "center=(1256,265)"),
            statuses[-1],
        )

    def test_merchant_interaction_rejects_each_metric_below_floor_without_click(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        base_metrics = {
            "score": 0.935,
            "pixel_score": 0.952,
            "zncc_score": 0.935,
        }

        for metric in base_metrics:
            with self.subTest(metric=metric):
                metrics = {**base_metrics, metric: 0.899}
                match = MatchResult(
                    metrics["score"],
                    (1229, 245),
                    (55, 40),
                    pixel_score=metrics["pixel_score"],
                    zncc_score=metrics["zncc_score"],
                )
                clicks = []
                task = SimpleNamespace(
                    config={},
                    capture_frame=lambda: frame,
                    operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
                    sleep=lambda *_args: None,
                    info_set=lambda *_args: None,
                    log_warning=lambda *_args: None,
                )
                vision = Vision(task)
                vision.match = lambda _frame, _spec, result=match: result
                navigator = Navigator(task, vision)

                with patch(
                    "src.tasks.map_trade.navigator.monotonic",
                    side_effect=(0.0, 1.0),
                ):
                    self.assertFalse(
                        navigator._click_merchant_interaction(
                            timeout=0.0,
                            after_sleep=1.2,
                        )
                    )
                self.assertFalse(vision.passes(match, MERCHANT_CLICK_LOCATION_TEMPLATE))
                self.assertEqual([], clicks)

    def test_merchant_interaction_miss_fails_without_navigation_fallback(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed_match = MatchResult(-1.0, (0, 0), (0, 0))
        clicks = []
        fallback_calls = []

        def passes(result, spec):
            return (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
                and result.zncc_score >= spec.min_zncc_score
            )

        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda captured, spec: failed_match,
            passes=passes,
            click_client=lambda point, shape, after_sleep=0: clicks.append(point),
            wait_template=lambda *_args, **_kwargs: fallback_calls.append("wait_template"),
            click_template=lambda *_args, **_kwargs: fallback_calls.append("click_template"),
        )
        navigator = Navigator(task, vision)
        navigator.classify_trade = lambda: ScreenState.SANDBOX

        with patch("src.tasks.map_trade.navigator.monotonic", side_effect=(0.0, 3.0)):
            result = navigator.reach_merchant_shop()

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertEqual(MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE, result.message)
        self.assertEqual([], clicks)
        self.assertEqual([], fallback_calls)

    def test_merchant_marker_asset_is_removed(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        self.assertFalse(
            any(
                path.name.endswith("IcoGE.png")
                for path in template_root.joinpath("image", "green").glob(
                    "Merchant_*.png"
                )
            )
        )

    def test_sell_only_shop_entry_survives_shop_ocr_miss_after_positive_entry_click(self):
        click_ocr_results = iter((False, True))
        reference_clicks = []
        warnings = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=warnings.append,
        )
        vision = SimpleNamespace(
            click_ocr=lambda *_args, **_kwargs: next(click_ocr_results),
            click_reference=lambda *args, **kwargs: reference_clicks.append((args, kwargs)),
        )
        navigator = Navigator(task, vision)
        navigator.wait_trade_state = lambda wanted, timeout: ScreenState.MERCHANT_DIALOG

        result = navigator._bargain_and_enter_shop()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SHOP, result.state)
        self.assertEqual([], reference_clicks)
        self.assertTrue(any("商店页OCR未确认" in warning for warning in warnings))

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
                (0.55, 0.20, 0.90)
                if spec in selected_slot_specs
                else (0.01, 0.90, 0.60)
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
                if spec
                is SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE
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
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda *_args: None,
        )
        navigator = Navigator(
            task,
            SimpleNamespace(capture=lambda: captures.append(frame) or frame),
        )
        navigator.classify = lambda _frame=None: ScreenState.SANDBOX
        navigator._match_story_sandbox_signals = lambda _frame: next(confirmations)

        with patch(
            "src.tasks.map_trade.navigator.monotonic",
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
        self.assertEqual(SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER, (1672, 1010))
        self.assertEqual(6, len(captures))

    def test_story_sandbox_group_two_does_not_confirm_when_switch_stays_on_group_two(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        captures = []
        clicks = []
        task = SimpleNamespace(
            info_set=lambda *_args: None,
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
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
            "src.tasks.map_trade.navigator.monotonic",
            side_effect=[100.0] * 6 + [101.0],
        ):
            result = navigator._wait_for_current_sandbox(timeout=0.1, interval=0.0)

        self.assertFalse(result.success)
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
        self.assertIn("未达到角标双阈值", reason)

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

    def test_collection_card_selection_uses_common_quick_switch_and_badge_center(self):
        clicks = []
        client_clicks = []
        template_clicks = []
        opened_callbacks = []
        badge_targets = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                1,
                MatchResult(0.99, (300, 930), (30, 28), pixel_score=0.98),
            ),
            runner_up=StoryBadgeCandidate(
                7,
                MatchResult(0.80, (301, 930), (31, 31), pixel_score=0.82),
            ),
        )

        def open_quick_switcher(**callbacks):
            opened_callbacks.append(tuple(callbacks))
            return (
                callbacks["ensure_home"]()
                and callbacks["click_quick_switch"]()
                and callbacks["confirm_quick_switch_page"]()
            )

        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            open_cartridge_quick_switcher=open_quick_switcher,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_stable_template=lambda spec, timeout, after_sleep: (
                template_clicks.append((spec, timeout, after_sleep)) or True
            ),
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda: ScreenState.HOME
        navigator.return_home = lambda: NavigationResult(True, ScreenState.HOME)
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True
        navigator._wait_for_story_badge_with_scroll = lambda number: (
            badge_targets.append(number) or (frame, badge)
        )
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )

        result = navigator.select_card("Q_sp1")

        self.assertTrue(result.success)
        self.assertEqual("Q_sp1", result.message)
        self.assertEqual([1], badge_targets)
        self.assertEqual(
            [("ensure_home", "click_quick_switch", "confirm_quick_switch_page")],
            opened_callbacks,
        )
        self.assertEqual([(QUICK_SWITCH_TEMPLATE, 10.0, 1.0)], template_clicks)
        self.assertEqual([(*STORY_CATEGORY_POINT, 0.5)], clicks)
        self.assertEqual([(badge.best.result.center, frame.shape, 1.0)], client_clicks)

    def test_collection_card_visual_completion_is_checked_before_clicking(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                1,
                MatchResult(
                    0.99,
                    (80, 921),
                    (29, 29),
                    pixel_score=0.98,
                    zncc_score=0.97,
                ),
            ),
            runner_up=StoryBadgeCandidate(
                7,
                MatchResult(
                    0.80,
                    (81, 921),
                    (29, 29),
                    pixel_score=0.82,
                    zncc_score=0.70,
                ),
            ),
        )
        located = LocatedStoryCard(CARD_BY_ID["Q_sp1"], frame, badge)
        completion = StoryCardCompletion(
            absorb=CardActionDetection(CardActionState.COMPLETED),
            suppress=CardActionDetection(CardActionState.COMPLETED),
            bounds=(78, 1015, 258, 1065),
            complete_region=True,
        )
        statuses = []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *values: statuses.append(values),
                log_warning=lambda *_args: None,
            ),
            SimpleNamespace(),
        )
        navigator._locate_story_card = lambda _card_id: located
        navigator.card_status = SimpleNamespace(
            detect=lambda detected_frame, center: (
                self.assertIs(frame, detected_frame)
                or self.assertEqual(badge.best.result.center, center)
                or completion
            )
        )
        navigator._enter_located_story_card = lambda _located: self.fail(
            "visually completed card must not be clicked"
        )

        result = navigator.select_collection_card("Q_sp1")

        self.assertTrue(result.success)
        self.assertEqual(
            CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
            result.outcome,
        )
        self.assertEqual(ScreenState.CARD_MENU, result.state)
        self.assertIs(completion, result.completion)
        self.assertIn(("卡带完成度", "completed"), statuses)

    def test_formal_collection_skips_card_when_preentry_status_is_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            selected = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                selected.append((card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    NavigationResult(True, ScreenState.CARD_MENU, "视觉完成"),
                )

            navigator = SimpleNamespace(
                select_collection_card=select,
                prepare_collection_main_area=lambda *_args: self.fail(
                    "preentry-complete card must not enter the sandbox"
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (CARD_BY_ID["Q_sp1"],),
            ):
                result = collector.run()

        self.assertTrue(result.success)
        self.assertEqual([("Q_sp1", False)], selected)

    def test_collection_card_pending_or_unknown_status_still_enters(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                1,
                MatchResult(0.99, (80, 921), (29, 29), pixel_score=0.98),
            ),
            runner_up=None,
        )
        located = LocatedStoryCard(CARD_BY_ID["Q_sp1"], frame, badge)
        entered = []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *_args: None,
                log_warning=lambda *_args: None,
            ),
            SimpleNamespace(),
        )
        navigator._locate_story_card = lambda _card_id: located
        navigator._enter_located_story_card = lambda value: (
            entered.append(value) or NavigationResult(True, ScreenState.SANDBOX, "Q_sp1")
        )

        for state in (CardActionState.PENDING, CardActionState.UNKNOWN):
            with self.subTest(state=state):
                completion = StoryCardCompletion(
                    absorb=CardActionDetection(state),
                    suppress=CardActionDetection(CardActionState.COMPLETED),
                    bounds=(78, 1015, 258, 1065),
                    complete_region=True,
                )
                navigator.card_status = SimpleNamespace(
                    detect=lambda *_args, value=completion: value
                )

                result = navigator.select_collection_card("Q_sp1")

                self.assertEqual(
                    CollectionCardSelectionOutcome.ENTERED,
                    result.outcome,
                )
        self.assertEqual([located, located], entered)

    def test_collection_completion_api_rejects_non_collection_story_cards(self):
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator._locate_story_card = lambda _card_id: self.fail(
            "non-collection card must not be located"
        )

        result = navigator.select_collection_card("Q_sp6")

        self.assertFalse(result.success)
        self.assertEqual(CollectionCardSelectionOutcome.FAILED, result.outcome)
        self.assertIn("非跑图剧情卡带", result.message)

    def test_collection_status_detection_error_continues_without_skipping(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        located = LocatedStoryCard(
            CARD_BY_ID["Q_sp1"],
            frame,
            StoryBadgeDetection(
                best=StoryBadgeCandidate(
                    1,
                    MatchResult(0.99, (80, 921), (29, 29), pixel_score=0.98),
                ),
                runner_up=None,
            ),
        )
        warnings = []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *_args: None,
                log_warning=lambda value: warnings.append(value),
            ),
            SimpleNamespace(),
        )
        navigator._locate_story_card = lambda _card_id: located
        navigator.card_status = SimpleNamespace(
            detect=lambda *_args: (_ for _ in ()).throw(RuntimeError("template missing"))
        )
        navigator._enter_located_story_card = lambda _located: NavigationResult(
            True,
            ScreenState.SANDBOX,
        )

        result = navigator.select_collection_card("Q_sp1")

        self.assertEqual(CollectionCardSelectionOutcome.ENTERED, result.outcome)
        self.assertIsNone(result.completion)
        self.assertTrue(any("按未知继续进入" in value for value in warnings))

    def test_collection_card_scroll_resets_down_then_scans_up_in_bottom_region(self):
        scrolls = []
        clicks = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        detection = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                12,
                MatchResult(0.99, (500, 930), (30, 28), pixel_score=0.98),
            ),
            runner_up=StoryBadgeCandidate(
                2,
                MatchResult(0.80, (501, 930), (31, 31), pixel_score=0.82),
            ),
        )
        results = iter(
            (
                (None, "未达到双阈值，检测目标数=9"),
                (None, "未达到双阈值，检测目标数=9"),
                (detection, ""),
            )
        )
        task = SimpleNamespace(
            _scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            log_warning=lambda *_args, **_kwargs: None,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        navigator._find_story_badge = lambda _frame, _number: next(results)

        found = navigator._wait_for_story_badge_with_scroll(12, scan_steps=1)

        self.assertIsNotNone(found)
        self.assertEqual(detection, found[1])
        self.assertEqual(
            [
                (
                    (QUICK_SWITCH_SCROLL_POINT, QUICK_SWITCH_SCROLL_RESET_AMOUNT),
                    {
                        "count": QUICK_SWITCH_SCROLL_RESET_COUNT,
                        "interval": QUICK_SWITCH_SCROLL_INTERVAL,
                        "after_sleep": QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
                    },
                ),
                (
                    (QUICK_SWITCH_SCROLL_POINT, QUICK_SWITCH_SCROLL_UP_AMOUNT),
                    {
                        "count": QUICK_SWITCH_SCROLL_UP_COUNT,
                        "interval": QUICK_SWITCH_SCROLL_INTERVAL,
                        "after_sleep": QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
                    },
                ),
            ],
            scrolls,
        )
        self.assertEqual((43 / 1920, 974 / 1080), QUICK_SWITCH_SCROLL_POINT)
        self.assertEqual(
            [((QUICK_SWITCH_SCROLL_FOCUS_POINT), {"after_sleep": 0.0})],
            clicks,
        )
        self.assertEqual((43 / 1920, 974 / 1080), QUICK_SWITCH_SCROLL_FOCUS_POINT)
        self.assertEqual(1, QUICK_SWITCH_SCROLL_UP_AMOUNT)

    def test_collection_card_scroll_stops_on_badge_ambiguity(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        warnings = []
        task = SimpleNamespace(
            _scroll_client=lambda *_args, **_kwargs: self.fail(
                "ambiguous badge must stop before scrolling"
            ),
            log_warning=warnings.append,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        navigator._find_story_badge = lambda _frame, _number: (
            None,
            "候选分差不足：0.020<0.050",
        )

        found = navigator._wait_for_story_badge_with_scroll(12, scan_steps=1)

        self.assertIsNone(found)
        self.assertIn("存在歧义", warnings[0])

    def test_probe_story_card_scrolls_up_once_then_rechecks_same_frame_status(self):
        first_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matched_frame = np.ones((1080, 1920, 3), dtype=np.uint8)
        confirmed_frame = np.full((1080, 1920, 3), 2, dtype=np.uint8)
        frames = iter((first_frame, matched_frame, confirmed_frame))
        detection = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                10,
                MatchResult(
                    0.99,
                    (500, 930),
                    (30, 28),
                    pixel_score=0.98,
                    zncc_score=0.97,
                ),
            ),
            runner_up=StoryBadgeCandidate(
                12,
                MatchResult(
                    0.80,
                    (501, 930),
                    (31, 31),
                    pixel_score=0.82,
                    zncc_score=0.70,
                ),
            ),
        )
        completion = StoryCardCompletion(
            absorb=CardActionDetection(CardActionState.PENDING),
            suppress=CardActionDetection(CardActionState.COMPLETED),
            bounds=(485, 1010, 665, 1060),
            complete_region=True,
        )
        scrolls = []
        clicks = []
        sleeps = []
        task = SimpleNamespace(
            _scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=sleeps.append,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: next(frames)))
        find_results = iter(
            (
                (None, "未达到角标双阈值"),
                (detection, ""),
                (detection, ""),
            )
        )
        navigator._find_story_badge = lambda *_args: next(find_results)
        detected_frames = []
        navigator.card_status = SimpleNamespace(
            detect=lambda frame, center: detected_frames.append((frame, center)) or completion
        )

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=5)

        self.assertIsNotNone(result)
        self.assertIs(confirmed_frame, result.located.frame)
        self.assertIs(completion, result.completion)
        self.assertEqual(
            [
                (
                    (PROBE_QUICK_SWITCH_SCROLL_POINT, PROBE_QUICK_SWITCH_SCROLL_AMOUNT),
                    {
                        "count": PROBE_QUICK_SWITCH_SCROLL_COUNT,
                        "interval": PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
                        "after_sleep": PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
                    },
                )
            ],
            scrolls,
        )
        self.assertEqual((43 / 1920, 974 / 1080), PROBE_QUICK_SWITCH_SCROLL_POINT)
        self.assertEqual(1, PROBE_QUICK_SWITCH_SCROLL_AMOUNT)
        self.assertEqual(5, PROBE_QUICK_SWITCH_SCROLL_COUNT)
        self.assertEqual(0.1, PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS)
        self.assertEqual(0.5, PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS)
        self.assertEqual(
            [((QUICK_SWITCH_SCROLL_FOCUS_POINT), {"after_sleep": 0.0})],
            clicks,
        )
        self.assertEqual([PROBE_STORY_BADGE_CONFIRM_SECONDS], sleeps)
        self.assertEqual(
            [
                (matched_frame, detection.best.result.center),
                (confirmed_frame, detection.best.result.center),
            ],
            detected_frames,
        )

    def test_probe_story_card_scrolls_again_when_status_strip_is_clipped(self):
        edge_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        centered_frame = np.ones((1080, 1920, 3), dtype=np.uint8)
        confirmed_frame = np.full((1080, 1920, 3), 2, dtype=np.uint8)
        frames = iter((edge_frame, centered_frame, confirmed_frame))
        edge_badge = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (1850, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (1851, 930), (31, 31), 0.82, 0.70),
            ),
        )
        centered_badge = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (500, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (501, 930), (31, 31), 0.82, 0.70),
            ),
        )
        clipped = StoryCardCompletion(
            CardActionDetection(CardActionState.UNKNOWN),
            CardActionDetection(CardActionState.UNKNOWN),
            (1835, 1010, 1920, 1060),
            complete_region=False,
        )
        complete = StoryCardCompletion(
            CardActionDetection(CardActionState.COMPLETED),
            CardActionDetection(CardActionState.COMPLETED),
            (485, 1010, 665, 1060),
            complete_region=True,
        )
        scrolls = []
        clicks = []
        task = SimpleNamespace(
            _scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=lambda *_args: None,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: next(frames)))
        badges = iter(((edge_badge, ""), (centered_badge, ""), (centered_badge, "")))
        navigator._find_story_badge = lambda *_args: next(badges)
        completions = iter((clipped, complete, complete))
        navigator.card_status = SimpleNamespace(detect=lambda *_args: next(completions))

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=1)

        self.assertIsNotNone(result)
        self.assertIs(centered_badge, result.located.badge)
        self.assertIs(complete, result.completion)
        self.assertEqual(1, len(scrolls))
        self.assertEqual(
            [((QUICK_SWITCH_SCROLL_FOCUS_POINT), {"after_sleep": 0.0})],
            clicks,
        )

    def test_probe_story_card_ambiguity_stops_without_scrolling(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        warnings = []
        task = SimpleNamespace(
            _scroll_client=lambda *_args, **_kwargs: self.fail(
                "ambiguous probe badge must not scroll"
            ),
            sleep=lambda *_args: self.fail("ambiguous probe badge must not sleep"),
            info_set=lambda *_args: None,
            log_warning=warnings.append,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        navigator._find_story_badge = lambda *_args: (
            None,
            "候选分差不足（ZNCC）：0.020<0.050",
        )
        navigator.card_status = SimpleNamespace(
            detect=lambda *_args: self.fail("ambiguous probe badge must not read status")
        )

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=3)

        self.assertIsNone(result)
        self.assertIn("存在歧义", warnings[0])

    def test_probe_story_card_recheck_failure_stops_without_scrolling_or_clicking(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        detection = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (500, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (501, 930), (31, 31), 0.82, 0.70),
            ),
        )
        completion = StoryCardCompletion(
            CardActionDetection(CardActionState.COMPLETED),
            CardActionDetection(CardActionState.COMPLETED),
            (485, 1010, 665, 1060),
            complete_region=True,
        )
        warnings = []
        task = SimpleNamespace(
            _scroll_client=lambda *_args, **_kwargs: self.fail(
                "a vanished recheck must stop before scrolling"
            ),
            sleep=lambda *_args: None,
            info_set=lambda *_args: None,
            log_warning=warnings.append,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        results = iter(((detection, ""), (None, "未识别")))
        navigator._find_story_badge = lambda *_args: next(results)
        navigator.card_status = SimpleNamespace(detect=lambda *_args: completion)

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=3)

        self.assertIsNone(result)
        self.assertIn("点击前复核失败", warnings[0])

    def test_probe_story_card_scan_limit_is_bounded_without_reset(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        scrolls = []
        clicks = []
        captures = []
        task = SimpleNamespace(
            _scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=lambda *_args: None,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        navigator = Navigator(
            task,
            SimpleNamespace(capture=lambda: captures.append(True) or frame),
        )
        navigator._find_story_badge = lambda *_args: (None, "未识别")

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=12)

        self.assertIsNone(result)
        self.assertEqual(4, len(captures))
        self.assertEqual(3, len(scrolls))
        self.assertEqual([5, 5, 2], [call[1]["count"] for call in scrolls])
        self.assertEqual(
            [((QUICK_SWITCH_SCROLL_FOCUS_POINT), {"after_sleep": 0.0})],
            clicks,
        )
        self.assertTrue(
            all(
                call[0] == (PROBE_QUICK_SWITCH_SCROLL_POINT, PROBE_QUICK_SWITCH_SCROLL_AMOUNT)
                for call in scrolls
            )
        )

    def test_enter_probe_story_card_uses_revalidated_located_card(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (500, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (501, 930), (31, 31), 0.82, 0.70),
            ),
        )
        completion = StoryCardCompletion(
            CardActionDetection(CardActionState.COMPLETED),
            CardActionDetection(CardActionState.COMPLETED),
            (485, 1010, 665, 1060),
            complete_region=True,
        )
        located = LocatedStoryCard(CARD_BY_ID["Q_sp10"], frame, badge)
        probed = ProbedStoryCard(located, completion)
        entered = []
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        expected = NavigationResult(True, ScreenState.SANDBOX, "Q_sp10")
        navigator._enter_located_story_card = lambda value: entered.append(value) or expected

        result = navigator.enter_probe_story_card(probed)

        self.assertIs(expected, result)
        self.assertEqual([located], entered)

    def test_collection_card_entry_handles_insert_prompt_then_reconfirms_sandbox(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        sleeps = []
        states = iter(
            (
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            sleep=lambda seconds: sleeps.append(seconds),
        )

        def ocr_text(_frame, name, roi=None):
            if name == "新卡带插入提示":
                self.assertEqual(FIRST_CARD_INSERT_REGION, roi)
                return "未插好游戏卡 插入"
            return ""

        vision = SimpleNamespace(
            capture=lambda: frame,
            simplify=lambda value: value,
            ocr_text=ocr_text,
            click_ocr=lambda patterns, roi, after_sleep, name: (
                clicks.append((tuple(patterns), roi, after_sleep, name)) or True
            ),
            match=lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: next(states)
        navigator._match_story_sandbox_signals = lambda _frame: SandboxConfirmation(
            2,
            2,
            3,
            1,
        )

        result = navigator._wait_for_story_sandbox(12, timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual("Q_sp12", result.message)
        self.assertEqual(
            [
                (
                    (r"插入", r"未插好游戏卡"),
                    FIRST_CARD_INSERT_REGION,
                    0.8,
                    "新卡带插入",
                )
            ],
            clicks,
        )
        self.assertEqual([], sleeps)

    def test_collection_card_entry_handles_skip_and_confirmation_with_mouse(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        client_clicks = []
        ocr_clicks = []
        skip_result = MatchResult(0.99, (1500, 20), (100, 40), pixel_score=0.98)
        task = SimpleNamespace(config={}, sleep=lambda *_args: None)
        prompts = iter(("", ""))
        confirmations = iter(("确认",))

        def ocr_text(_frame, name, roi=None):
            if name == "新卡带插入提示":
                return next(prompts)
            if name == "首次卡带确认":
                self.assertEqual(FIRST_CARD_CONFIRM_REGION, roi)
                return next(confirmations)
            return ""

        matches = iter(
            (
                skip_result,
                MatchResult(-1.0, (0, 0), (0, 0)),
                MatchResult(-1.0, (0, 0), (0, 0)),
            )
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            simplify=lambda value: value,
            ocr_text=ocr_text,
            click_ocr=lambda patterns, roi, after_sleep, name: (
                ocr_clicks.append((tuple(patterns), roi, after_sleep, name)) or True
            ),
            match=lambda _frame, spec: (
                self.assertEqual(FIRST_CARD_SKIP_TEMPLATE, spec) or next(matches)
            ),
            passes=lambda result, _spec: result.score >= 0.72,
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        states = iter(
            (
                ScreenState.UNKNOWN,
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        navigator.classify = lambda _frame=None: next(states)
        navigator._match_story_sandbox_signals = lambda _frame: SandboxConfirmation(
            2,
            2,
            3,
            1,
        )

        result = navigator._wait_for_story_sandbox(12, timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual([(skip_result.center, frame.shape, 0.8)], client_clicks)
        self.assertEqual(
            [((r"确认",), FIRST_CARD_CONFIRM_REGION, 0.8, "首次卡带确认")],
            ocr_clicks,
        )

    def test_collection_card_entry_requires_consecutive_stable_sandbox_frames(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        states = iter(
            (
                ScreenState.SANDBOX,
                ScreenState.LOADING,
                ScreenState.SANDBOX,
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        captures = []
        task = SimpleNamespace(config={}, sleep=lambda *_args: None)
        vision = SimpleNamespace(capture=lambda: captures.append(frame) or frame)
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: next(states)
        navigator._handle_story_card_intermediate = lambda _frame: False
        navigator._match_story_sandbox_signals = lambda _frame: SandboxConfirmation(
            2,
            2,
            3,
            1,
        )

        result = navigator._wait_for_story_sandbox(1, timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual(STORY_SANDBOX_STABLE_HITS, 2)
        self.assertEqual(6, len(captures))

    def test_buy_entry_final_new_template_miss_fails_without_fallback(self):
        clicks = []
        client_clicks = []
        shop_entry_attempts = []
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        badge_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge_detection = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                6,
                MatchResult(0.99, (80, 930), (30, 28), pixel_score=0.98),
            ),
            runner_up=StoryBadgeCandidate(
                8,
                MatchResult(0.80, (81, 930), (31, 31), pixel_score=0.82),
            ),
        )
        vision = SimpleNamespace(
            click_stable_template=lambda *_args, **_kwargs: True,
            click_client=lambda point, frame_shape, after_sleep=0: client_clicks.append(
                (point, frame_shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True
        navigator._wait_for_story_badge = lambda _number: (badge_frame, badge_detection)
        shop_entry_results = iter((False, False))
        navigator._enter_q_sp6_shop = lambda timeout, *, log_timeout: (
            shop_entry_attempts.append((timeout, log_timeout)) or next(shop_entry_results)
        )
        navigator.classify = lambda: ScreenState.UNKNOWN

        def open_quick_switcher(**callbacks):
            return (
                callbacks["ensure_home"]()
                and callbacks["click_quick_switch"]()
                and callbacks["confirm_quick_switch_page"]()
            )

        task.open_cartridge_quick_switcher = open_quick_switcher

        result = navigator.enter_q_sp6_buy_flow()

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.UNKNOWN, result.state)
        self.assertEqual(MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE, result.message)
        self.assertEqual([(*STORY_CATEGORY_POINT, 0.5)], clicks)
        self.assertEqual(
            [(badge_detection.best.result.center, badge_frame.shape, 0.0)],
            client_clicks,
        )
        self.assertEqual(
            [
                (Q_SP6_SHOP_PRIORITY_TIMEOUT, False),
                (45.0, True),
            ],
            shop_entry_attempts,
        )

    def test_buy_phase_enters_shop_then_runs_or_skips_local_favorite_rebuild(self):
        actions = []
        warnings = []
        task = SimpleNamespace(
            config={"收藏重建周期": "每周"},
            sleep=lambda seconds: actions.append(("sleep", seconds)),
            log_info=lambda message: actions.append(("log", message)),
            log_warning=warnings.append,
        )
        progress = SimpleNamespace(
            should_rebuild_favorites=lambda every_run=False: (
                actions.append(("should", every_run)) or True
            ),
            clear_favorite_cards=lambda: actions.append(("clear",)),
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.progress = progress
        trader.now_provider = lambda: datetime(2026, 7, 19, 7, 59, tzinfo=UTC_PLUS_8)
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(True, ScreenState.SHOP)
        )
        trader.rebuild_favorites = lambda: actions.append(("rebuild",)) or True
        trader.buy_all_favorites = lambda: actions.append(("buy-all",)) or True

        self.assertTrue(trader.run_buy())
        self.assertEqual(
            [
                ("log", "买：按2026-07-18库存批次执行（每日08:00刷新）。"),
                ("should", False),
                ("rebuild",),
                ("buy-all",),
            ],
            actions,
        )

        actions.clear()
        warnings.clear()
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(
                True,
                ScreenState.MERCHANT_DIALOG,
            )
        )
        self.assertFalse(trader.run_buy())
        self.assertEqual(
            [("log", "买：按2026-07-18库存批次执行（每日08:00刷新）。")],
            actions,
        )
        self.assertIn(
            "买：砍价后状态为merchant_dialog，未确认商店页，停止购买。",
            warnings,
        )

        actions.clear()
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(True, ScreenState.SHOP)
        )
        task.config["收藏重建周期"] = "每周"
        progress.should_rebuild_favorites = lambda every_run=False: False
        self.assertTrue(trader.run_buy())
        self.assertEqual(
            [
                ("log", "买：按2026-07-18库存批次执行（每日08:00刷新）。"),
                ("log", "买：本周收藏已经按本地表重建，跳过收藏调整。"),
                ("buy-all",),
            ],
            actions,
        )

        actions.clear()
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(True, ScreenState.SHOP)
        )
        task.config["收藏重建周期"] = "永不"
        progress.should_rebuild_favorites = lambda **_kwargs: self.fail(
            "永不模式不应读取收藏重建进度"
        )
        self.assertTrue(trader.run_buy())
        self.assertEqual(
            [
                ("log", "买：按2026-07-18库存批次执行（每日08:00刷新）。"),
                ("log", "买：收藏重建周期设为永不，跳过收藏调整。"),
                ("buy-all",),
            ],
            actions,
        )

    def test_phase_failure_stops_later_phases(self):
        actions = []
        task = object.__new__(MapTradeTask)
        task.config = {"买": True, "卖": True, "制作料理": True}
        task.info_set = lambda *_args: None
        task.log_info = lambda *_args: None
        task.log_warning = lambda *_args: None
        task.log_error = lambda *_args: None
        task._save_diagnostic = lambda *_args: None
        navigator = SimpleNamespace(
            return_home=lambda: (
                actions.append("home")
                or NavigationResult(
                    True,
                    ScreenState.HOME,
                )
            )
        )
        phases = (
            ("买", "买", lambda: actions.append("buy") or False),
            ("卖", "卖", lambda: actions.append("sell") or True),
            ("制作料理", "制作料理", lambda: actions.append("cooking") or True),
        )

        self.assertFalse(task._run_phases(navigator, phases))
        self.assertEqual(["buy", "home"], actions)

    def test_buy_all_favorites_clicks_ocr_button_center_and_confirmation_point(self):
        clicks = []
        logs = []
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda seconds: logs.append(("sleep", seconds)),
            log_info=lambda message: logs.append(("log", message)),
            log_warning=warnings.append,
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader.vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            )
        )
        trader._wait_for_buy_all_favorites_button = lambda: ((1454, 1004), frame)
        trader._wait_for_purchase_confirmation = lambda: True

        self.assertTrue(trader.buy_all_favorites())
        self.assertEqual(
            [
                ((1454, 1004), frame.shape, 0.3),
                (*BUY_CONFIRM_POINT, 0.8),
            ],
            clicks,
        )
        self.assertEqual(
            (701 / 1920, 328 / 1080, 1219 / 1920, 753 / 1080),
            BUY_CONFIRM_DIALOG_REGION,
        )
        self.assertEqual((1045 / 1920, 697 / 1080), BUY_CONFIRM_POINT)
        self.assertEqual(30.0, BUY_CONFIRM_TIMEOUT)
        self.assertEqual([], warnings)
        self.assertEqual(
            [
                (
                    "log",
                    "买：购买确认弹窗OCR完成，等待0.8秒后点击确认。",
                ),
                ("sleep", BUY_CONFIRM_PRE_CLICK_DELAY),
                ("log", "买：已确认购买全部收藏商品。"),
            ],
            logs,
        )

    def test_buy_all_button_requires_two_consecutive_full_frame_ocr_hits(self):
        ocr_calls = []
        sleeps = []
        statuses = []
        boxes = iter(
            (
                [SimpleNamespace(name="一键购买全部收藏", x=1324, y=982, width=221, height=47)],
                [],
                [SimpleNamespace(name="-键购买全部收藏", x=1379, y=992, width=148, height=24)],
                [SimpleNamespace(name="一键购买全部收藏", x=1377, y=990, width=152, height=28)],
            )
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            sleep=sleeps.append,
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_boxes=lambda captured, name: (
                ocr_calls.append((captured.shape, name)) or next(boxes)
            ),
            simplify=lambda value: value,
        )

        located = trader._wait_for_buy_all_favorites_button()

        self.assertIsNotNone(located)
        point, located_frame = located
        self.assertEqual((1453, 1004), point)
        self.assertIs(frame, located_frame)
        self.assertEqual(3, len(sleeps))
        self.assertEqual(BUY_ALL_FAVORITES_KEYWORD, "购买全部收藏")
        self.assertEqual(BUY_ALL_FAVORITES_STABLE_HITS, 2)
        self.assertTrue(all(call[0] == frame.shape for call in ocr_calls))
        self.assertEqual(
            ("一键购买全部收藏按钮 OCR稳定", "2/2"),
            statuses[-1],
        )

    def test_purchase_confirmation_requires_both_texts_in_given_region(self):
        ocr_calls = []
        warnings = []
        text = {"value": "一键购买全部收藏"}
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            sleep=lambda *_args: None,
            log_warning=warnings.append,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda captured, name, relative_roi: (
                ocr_calls.append((captured.shape, name, relative_roi)) or text["value"]
            ),
            simplify=lambda value: value,
        )

        self.assertFalse(trader._wait_for_purchase_confirmation(timeout=0.0))
        text["value"] = "一键购买全部收藏 是否购买所有加入收藏的商品？"
        self.assertTrue(trader._wait_for_purchase_confirmation(timeout=0.0))
        self.assertEqual(
            ("一键购买全部收藏", "是否购买所有加入收藏的商品"),
            BUY_CONFIRM_KEYWORDS,
        )
        self.assertTrue(all(call[2] == BUY_CONFIRM_DIALOG_REGION for call in ocr_calls))

    def test_buy_all_favorites_stops_when_confirmation_is_missing(self):
        clicks = []
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_info=lambda *_args, **_kwargs: None,
            log_warning=warnings.append,
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        trader.vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            )
        )
        trader._wait_for_buy_all_favorites_button = lambda: ((969, 669), frame)
        trader._wait_for_purchase_confirmation = lambda: False

        self.assertFalse(trader.buy_all_favorites())
        self.assertEqual([((969, 669), frame.shape, 0.3)], clicks)
        self.assertEqual(
            ["买：点击一键购买全部收藏后，未同时识别到确认标题和询问文字。"],
            warnings,
        )

    def test_buy_home_confirmation_requires_button_brightness_and_ocr(self):
        announcement_signals = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
            clear_temporary_home_announcement_if_needed=lambda **signals: (
                announcement_signals.append(signals) if not announcement_signals else None
            ),
        )
        result = MatchResult(0.80, (10, 10), (20, 20), pixel_score=0.90)
        brightness = {"value": 0.74}
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: result,
            passes=lambda *_args: True,
            template_brightness_ratio=lambda *_args: brightness["value"],
            ocr_text=lambda *_args, **_kwargs: "抽抽乐",
        )
        navigator = Navigator(task, vision)

        self.assertFalse(navigator._wait_for_cartridge_home(timeout=0.0))
        self.assertEqual(1, len(announcement_signals))
        self.assertTrue(announcement_signals[0]["button_found"])
        self.assertEqual(0.74, announcement_signals[0]["brightness_ratio"])
        self.assertEqual("抽抽乐", announcement_signals[0]["gacha_ocr_text"])
        brightness["value"] = 0.80
        self.assertTrue(navigator._wait_for_cartridge_home(timeout=0.0))
        vision.ocr_text = lambda *_args, **_kwargs: ""
        self.assertFalse(navigator._wait_for_cartridge_home(timeout=0.0))

    def test_screen_classification_only_reports_home_after_all_three_signals(self):
        task = SimpleNamespace(
            config={},
            info_set=lambda *_args, **_kwargs: None,
        )
        result = MatchResult(0.80, (10, 10), (20, 20), pixel_score=0.90)
        gacha_text = {"value": ""}
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: result,
            passes=lambda *_args: True,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.80,
            ocr_text=lambda *_args, **_kwargs: gacha_text["value"],
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertNotEqual(ScreenState.HOME, navigator.classify())
        gacha_text["value"] = "抽抽乐"
        self.assertEqual(ScreenState.HOME, navigator.classify())

    def test_loading_ocr_rejects_high_score_low_fidelity_sandbox_candidate(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed = MatchResult(-1.0, (0, 0), (0, 0))
        false_sandbox = MatchResult(
            0.98,
            (100, 100),
            (40, 40),
            pixel_score=0.34,
            zncc_score=0.20,
        )

        def match(_frame, spec):
            return false_sandbox if spec in SANDBOX_TEMPLATES else failed

        def passes(result, spec):
            return (
                result.score >= spec.threshold
                and (spec.min_pixel_score is None or result.pixel_score >= spec.min_pixel_score)
                and (spec.min_zncc_score is None or result.zncc_score >= spec.min_zncc_score)
            )

        vision = SimpleNamespace(
            match=match,
            passes=passes,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda _frame, name, **_kwargs: (
                "BROWN DUST II 94%" if name == "界面分类" else ""
            ),
            simplify=lambda value: value,
        )
        navigator = Navigator(SimpleNamespace(config={}), vision)

        self.assertEqual(ScreenState.LOADING, navigator.classify(frame))
        self.assertEqual(2, len(SANDBOX_TEMPLATES))
        spec = SANDBOX_TEMPLATES[0]
        self.assertEqual("image/UI_miniMap_B.png", spec.file_name)
        self.assertEqual(0.90, spec.threshold)
        self.assertEqual(0.90, spec.min_pixel_score)
        self.assertEqual(0.90, spec.min_zncc_score)
        self.assertIs(QUICK_SWITCH_TEMPLATE, SANDBOX_TEMPLATES[1])

    def test_quick_switch_button_alone_is_a_valid_sandbox_signal(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed = MatchResult(-1.0, (0, 0), (0, 0))
        quick_switch = MatchResult(
            0.98,
            (820, 980),
            (60, 50),
            pixel_score=0.91,
            zncc_score=0.92,
        )

        def match(_frame, spec):
            return quick_switch if spec is QUICK_SWITCH_TEMPLATE else failed

        def passes(result, spec):
            return (
                result.score >= spec.threshold
                and (spec.min_pixel_score is None or result.pixel_score >= spec.min_pixel_score)
                and (spec.min_zncc_score is None or result.zncc_score >= spec.min_zncc_score)
            )

        vision = SimpleNamespace(
            match=match,
            passes=passes,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda *_args, **_kwargs: "",
            simplify=lambda value: value,
        )
        navigator = Navigator(SimpleNamespace(config={}), vision)

        self.assertEqual(ScreenState.SANDBOX, navigator.classify(frame))

    def test_trade_classify_shop_page_wins_over_merchant_dialog_template(self):
        task = SimpleNamespace(config={}, info_set=lambda *_args: None)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        merchant = MatchResult(0.90, (1000, 40), (60, 40), pixel_score=0.85)
        failed = MatchResult(-1.0, (0, 0), (0, 0))

        def match(_frame, spec):
            if spec == TRADE_MERCHANT_CONTEXT_TEMPLATE:
                return merchant
            return failed

        vision = SimpleNamespace(
            capture=lambda: frame,
            match=match,
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda _frame, name, **_kwargs: (
                "仓库管理石怪 仓库 严加管理 天赋技能 砍价" if name == "跑商界面分类" else ""
            ),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertEqual(ScreenState.SHOP, navigator.classify_trade())

    def test_trade_classify_merchant_dialog_requires_shop_ocr_absent(self):
        task = SimpleNamespace(config={}, info_set=lambda *_args: None)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        merchant = MatchResult(0.90, (1000, 40), (60, 40), pixel_score=0.85)
        failed = MatchResult(-1.0, (0, 0), (0, 0))

        def match(_frame, spec):
            if spec == TRADE_MERCHANT_CONTEXT_TEMPLATE:
                return merchant
            return failed

        vision = SimpleNamespace(
            capture=lambda: frame,
            match=match,
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda _frame, name, **_kwargs: (
                "与仓库管理石怪砍价 砍价成功率100% 取消" if name == "跑商界面分类" else ""
            ),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertEqual(ScreenState.MERCHANT_DIALOG, navigator.classify_trade())

    def test_shared_classify_never_uses_trade_merchant_template(self):
        task = SimpleNamespace(config={}, info_set=lambda *_args: None)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        merchant = MatchResult(
            0.99,
            (1000, 40),
            (60, 40),
            pixel_score=0.95,
            zncc_score=0.94,
        )
        failed = MatchResult(-1.0, (0, 0), (0, 0))
        matched_specs = []

        def match(_frame, spec):
            matched_specs.append(spec)
            return merchant if spec == TRADE_MERCHANT_CONTEXT_TEMPLATE else failed

        vision = SimpleNamespace(
            capture=lambda: frame,
            match=match,
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda *_args, **_kwargs: "",
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertEqual(ScreenState.UNKNOWN, navigator.classify())
        self.assertNotIn(TRADE_MERCHANT_CONTEXT_TEMPLATE, matched_specs)

    def test_classify_shop_ocr_fallback_without_merchant_template(self):
        task = SimpleNamespace(config={}, info_set=lambda *_args: None)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed = MatchResult(-1.0, (0, 0), (0, 0))
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda *_args: failed,
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda _frame, name, **_kwargs: (
                "仓库管理石怪 仓库 严加管理 天赋技能" if name == "界面分类" else ""
            ),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertEqual(ScreenState.SHOP, navigator.classify())

    def test_return_home_from_shop_closes_discount_shop_then_uses_home_button(self):
        actions = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: actions.append(("click", x, y, after_sleep)),
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_reference=lambda x, y, after_sleep=0: actions.append(
                ("reference", x, y, after_sleep)
            )
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda: ScreenState.SHOP
        navigator._wait_for_ocr_keywords = (
            lambda keywords, timeout, name, interval=0.5, relative_roi=None: (
                actions.append(("ocr", keywords, timeout, name, interval, relative_roi)) or True
            )
        )
        navigator._wait_for_cartridge_home = lambda timeout: (
            actions.append(("home", timeout)) or True
        )

        result = navigator.return_home()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.HOME, result.state)
        self.assertEqual(
            [
                ("reference", 82, 36, 0.0),
                (
                    "ocr",
                    DISCOUNT_SHOP_CLOSE_KEYWORDS,
                    DISCOUNT_SHOP_CLOSE_TIMEOUT,
                    "折扣商店关闭确认",
                    0.25,
                    DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
                ),
                ("click", *DISCOUNT_SHOP_CLOSE_POINT, 0.8),
                ("reference", 82, 36, 0.8),
                ("click", *CHAPTER_HOME_POINT, 0.0),
                ("home", RETURN_HOME_TIMEOUT),
            ],
            actions,
        )
        self.assertEqual((1045 / 1920, 639 / 1080), DISCOUNT_SHOP_CLOSE_POINT)
        self.assertEqual((1797 / 1920, 63 / 1080), CHAPTER_HOME_POINT)
        self.assertEqual(10.0, RETURN_HOME_TIMEOUT)

    def test_return_home_from_shop_stops_when_close_dialog_is_not_confirmed(self):
        actions = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda *_args, **_kwargs: self.fail("未确认关闭弹窗时不得继续点击"),
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_reference=lambda x, y, after_sleep=0: actions.append((x, y, after_sleep))
        )
        states = iter((ScreenState.SHOP, ScreenState.SHOP))
        navigator = Navigator(task, vision)
        navigator.classify = lambda: next(states)
        navigator._wait_for_ocr_keywords = lambda *_args, **_kwargs: False

        result = navigator.return_home()

        self.assertFalse(result.success)
        self.assertEqual([(82, 36, 0.0)], actions)

    def test_return_home_from_sandbox_clicks_home_once(self):
        actions = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: actions.append((x, y, after_sleep)),
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator.classify = lambda: ScreenState.SANDBOX
        navigator._wait_for_cartridge_home = lambda timeout: (
            actions.append(("wait_home", timeout)) or True
        )

        result = navigator.return_home()

        self.assertTrue(result.success)
        self.assertEqual(
            [(*CHAPTER_HOME_POINT, 0.0), ("wait_home", RETURN_HOME_TIMEOUT)],
            actions,
        )

    def test_return_home_from_unknown_page_does_not_click(self):
        task = SimpleNamespace(
            config={},
            operate_click=lambda *_args, **_kwargs: self.fail("unknown page must not be clicked"),
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator.classify = lambda: ScreenState.UNKNOWN

        result = navigator.return_home()

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.UNKNOWN, result.state)
        self.assertIn("未执行点击", result.message)

    def test_return_home_waits_out_loading_then_clicks_home_once(self):
        actions = []
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            operate_click=lambda x, y, after_sleep=0: actions.append((x, y, after_sleep)),
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator.classify = lambda: ScreenState.LOADING
        navigator.wait_state = lambda wanted, timeout: (
            actions.append((wanted, timeout)) or ScreenState.SANDBOX
        )
        navigator._wait_for_cartridge_home = lambda timeout: (
            actions.append(("wait_home", timeout)) or True
        )

        result = navigator.return_home()

        self.assertTrue(result.success)
        self.assertEqual(
            [
                ({ScreenState.HOME, ScreenState.SANDBOX}, 45.0),
                (*CHAPTER_HOME_POINT, 0.0),
                ("wait_home", RETURN_HOME_TIMEOUT),
            ],
            actions,
        )

    def test_buy_quick_page_requires_all_six_labels(self):
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        text = {"value": "店长游戏卡 剧情游戏卡 角色游戏卡 玩法游戏卡 最近"}
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda *_args: text["value"],
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertFalse(navigator._wait_for_quick_switch_page(timeout=0.0))
        text["value"] += " 活动游戏卡"
        self.assertTrue(navigator._wait_for_quick_switch_page(timeout=0.0))

    def test_buy_story_category_requires_label_and_visual_highlight(self):
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        text = {"value": "剧情游戏卡"}
        highlight = {"value": STORY_CATEGORY_HIGHLIGHT_MIN_RATIO - 0.01}
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda *_args: text["value"],
            simplify=lambda value: value,
            bright_neutral_ratio=lambda *_args: highlight["value"],
        )
        navigator = Navigator(task, vision)

        self.assertFalse(navigator._wait_for_story_category(timeout=0.0))
        highlight["value"] = STORY_CATEGORY_HIGHLIGHT_MIN_RATIO
        self.assertTrue(navigator._wait_for_story_category(timeout=0.0))

        text["value"] = "角色游戏卡"
        self.assertFalse(navigator._wait_for_story_category(timeout=0.0))

    def test_story_category_highlight_region_uses_1920_reference_ratios(self):
        self.assertEqual(
            (445 / 1920, 840 / 1080, 670 / 1920, 915 / 1080),
            STORY_CATEGORY_HIGHLIGHT_REGION,
        )

    def test_bright_neutral_ratio_detects_category_highlight(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        left, top, region = Vision._relative_roi(frame, STORY_CATEGORY_HIGHLIGHT_REGION)
        required = round(region.shape[0] * region.shape[1] * 0.06)
        width = region.shape[1]
        frame[
            top : top + required // width + 1,
            left : left + width,
        ] = (220, 220, 220)

        ratio = Vision.bright_neutral_ratio(frame, STORY_CATEGORY_HIGHLIGHT_REGION)

        self.assertGreaterEqual(ratio, STORY_CATEGORY_HIGHLIGHT_MIN_RATIO)

    def test_weekly_map_task_runs_collection_without_trade(self):
        actions = []
        task = object.__new__(MapCollectionTask)
        task.config = {"启用": True, "执行地图采集": True}
        task.info_set = lambda *_args: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_error = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task._save_diagnostic = lambda *_args: None

        class FakeProgress:
            def load(self):
                return None

        class FakeNavigator:
            def __init__(self, *_args):
                pass

            def return_home(self):
                actions.append("home")
                return NavigationResult(True, ScreenState.HOME)

        class FakeCollector:
            def __init__(self, *_args):
                pass

            def run(self):
                actions.append("collection")
                return CollectionResult(True)

        with (
            patch.object(map_collection_task_module, "Vision", lambda *_args: object()),
            patch.object(map_collection_task_module, "ProgressStore", FakeProgress),
            patch.object(map_collection_task_module, "Navigator", FakeNavigator),
            patch.object(map_collection_task_module, "Collector", FakeCollector),
        ):
            self.assertTrue(MapCollectionTask.run(task))

        self.assertEqual(["collection", "home"], actions)

    def test_daily_and_weekly_cards_expose_separate_configurations(self):
        executor = SimpleNamespace(scene=None)
        app = SimpleNamespace()
        trade = MapTradeTask(executor, app)
        collection = MapCollectionTask(executor, app)

        self.assertEqual("每日跑商", trade.name)
        self.assertEqual("每周跑图", collection.name)
        self.assertIn("买", trade.default_config)
        self.assertIn("卖", trade.default_config)
        self.assertNotIn("料理", trade.description)
        for mapping_name in ("default_config", "config_description", "config_type"):
            with self.subTest(mapping=mapping_name):
                self.assertTrue(
                    COOKING_CONFIG_KEYS.isdisjoint(getattr(trade, mapping_name))
                )
        self.assertNotIn("执行跑商", trade.default_config)
        self.assertNotIn("执行地图采集", trade.default_config)
        self.assertIn("执行地图采集", collection.default_config)
        self.assertNotIn("买", collection.default_config)
        self.assertNotIn("卖", collection.default_config)
        self.assertNotIn("制作料理", collection.default_config)
        self.assertIn(TRADE_VISION_THRESHOLD_KEY, trade.default_config)
        self.assertIn(TRADE_OCR_THRESHOLD_KEY, trade.default_config)
        self.assertIn(MAP_VISION_THRESHOLD_KEY, collection.default_config)
        self.assertIn(MAP_OCR_THRESHOLD_KEY, collection.default_config)

        self.assertEqual(
            ["收藏重建周期"],
            trade.config_type["买"]["sub_configs"][True],
        )
        self.assertEqual(
            ["每周", "每次", "永不"],
            trade.config_type["收藏重建周期"]["options"],
        )
        self.assertEqual(
            [
                "使用程序默认价表",
                "出售保险",
                "使用出售白名单",
                "使用出售黑名单",
            ],
            trade.config_type["卖"]["sub_configs"][True],
        )
        self.assertTrue(trade.default_config["使用程序默认价表"])
        self.assertFalse(trade.default_config["出售保险"])
        self.assertTrue(trade.default_config["使用出售白名单"])
        self.assertEqual(
            ["出售白名单"],
            trade.config_type["使用出售白名单"]["sub_configs"][True],
        )
        self.assertFalse(trade.default_config["使用出售黑名单"])
        self.assertEqual("", trade.default_config["出售黑名单"])
        self.assertEqual(
            ["出售黑名单"],
            trade.config_type["使用出售黑名单"]["sub_configs"][True],
        )
        self.assertEqual("text_edit", trade.config_type["出售黑名单"]["type"])
        self.assertEqual(
            ["使用在线价表"],
            trade.config_type["使用程序默认价表"]["sub_configs"][False],
        )
        self.assertEqual(
            ["自定义最高价表"],
            trade.config_type["使用在线价表"]["sub_configs"][False],
        )

    def test_load_config_removes_legacy_cooking_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "MapTradeTask.json"
            target.write_text(
                json.dumps(
                    {
                        "启用": True,
                        "制作料理": True,
                        "料理制作周期": "每周",
                        "料理保险": True,
                        "5星料理": [],
                        "制作利润料理": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with (
                patch.object(map_trade_task_module, "_config_path", return_value=target),
                patch.object(map_trade_task_module.Config, "config_folder", temp_dir),
            ):
                trade = MapTradeTask(SimpleNamespace(scene=None), SimpleNamespace())
                trade.load_config()

            self.assertTrue(trade.config["启用"])
            self.assertTrue(
                COOKING_CONFIG_KEYS.isdisjoint(trade.config)
            )

    def test_manual_calendar_is_validated_only_when_both_other_sources_are_off(self):
        trade = MapTradeTask(SimpleNamespace(scene=None), SimpleNamespace())
        trade.config = {
            "使用程序默认价表": True,
            "使用在线价表": False,
            "自定义最高价表": "invalid",
        }

        self.assertIsNone(trade.validate_config("使用在线价表", False))
        trade.config["使用程序默认价表"] = False
        self.assertIn(
            "缺少 '='",
            trade.validate_config("使用在线价表", False),
        )

    def test_legacy_trade_switches_migrate_to_three_sections(self):
        self.assertEqual(
            {"买": False, "卖": True, "制作料理": False},
            _trade_section_migration_values(
                {
                    "执行跑商": True,
                    "低价进货": False,
                    "最高价出售": True,
                    "制作利润料理": False,
                }
            ),
        )
        self.assertEqual(
            {"买": False, "卖": False, "制作料理": True},
            _trade_section_migration_values(
                {
                    "执行跑商": False,
                    "低价进货": True,
                    "最高价出售": True,
                    "制作利润料理": True,
                }
            ),
        )

    def test_legacy_combined_config_seeds_weekly_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "MapCollectionTask.json"
            legacy = {
                "启用": False,
                "执行地图采集": False,
                "跑图跑商识图阈值": 0.83,
                "跑图跑商 OCR 阈值": 0.31,
                "加载页面等待秒数": 61.0,
                "卡带单步重试次数": 4,
            }
            with patch.object(map_trade_task_module, "_config_path", return_value=target):
                _migrate_collection_config(legacy)

            migrated = json.loads(target.read_text(encoding="utf-8"))
            self.assertFalse(migrated["启用"])
            self.assertFalse(migrated["执行地图采集"])
            self.assertEqual(0.83, migrated[MAP_VISION_THRESHOLD_KEY])
            self.assertEqual(0.31, migrated[MAP_OCR_THRESHOLD_KEY])
            self.assertEqual(61.0, migrated["加载页面等待秒数"])
            self.assertEqual(4, migrated["卡带单步重试次数"])

    def test_collection_retries_the_same_first_card_then_stops(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            attempts = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 3},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                attempts.append((card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.FAILED,
                    NavigationResult(False, ScreenState.UNKNOWN, "failed"),
                )

            navigator = SimpleNamespace(select_collection_card=select)
            result = Collector(task, object(), navigator, progress).run()

        self.assertFalse(result.success)
        self.assertEqual("未能进入卡带 Q_sp1", result.message)
        self.assertEqual([("Q_sp1", False)] * 3, attempts)

    def test_formal_collection_runs_safe_battle_one_battle_two_then_verifies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.state.daily_submaps = 18
            progress.state.daily_summons = 12
            progress.state.daily_suppressions = 12
            progress.save()
            events = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                events.append(("select", card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED,
                    NavigationResult(True, ScreenState.SANDBOX),
                )

            navigator = SimpleNamespace(
                select_collection_card=select,
                prepare_collection_main_area=lambda card_id: (
                    events.append(("prepare", card_id))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                advance_collection_map=lambda card_id, current, target: (
                    events.append(("advance", card_id, current.key, target.key))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                open_story_quick_switcher_from_sandbox=lambda: (
                    events.append(("quick",)) or NavigationResult(True, ScreenState.CARD_MENU)
                ),
                inspect_collection_card_completion=lambda card_id: (
                    events.append(("inspect", card_id))
                    or CollectionCardSelectionResult(
                        CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                        NavigationResult(True, ScreenState.CARD_MENU),
                    )
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            search = SearchCountdownSession((0.1, 0.2, 0.3, 0.4), 87)
            collector._start_search = (
                lambda **_kwargs: events.append(("search",)) or search
            )
            collector._verify_search_countdown = lambda value: (
                events.append(("countdown", value.value)) or True
            )
            collector._use_actions = lambda actions, **_kwargs: (
                events.append(("actions", tuple(action.name for action in actions)))
                or SkillExecutionResult(True)
            )

            result = collector.run()

        self.assertTrue(result.success)
        self.assertTrue(result.depleted)
        self.assertEqual(3, result.completed_submaps)
        self.assertEqual(
            [
                ("select", "Q_sp1", False),
                ("prepare", "Q_sp1"),
                ("search",),
                ("actions", ("吸收",)),
                ("advance", "Q_sp1", "main_area", "battle_area_1"),
                ("countdown", 87),
                ("actions", tuple(action.name for action in BATTLE_ACTIONS)),
                ("advance", "Q_sp1", "battle_area_1", "battle_area_2"),
                ("actions", tuple(action.name for action in BATTLE_ACTIONS)),
                ("quick",),
                ("inspect", "Q_sp1"),
            ],
            events,
        )
        self.assertEqual(21, progress.state.daily_absorbs)
        self.assertEqual(14, progress.state.daily_summons)
        self.assertEqual(14, progress.state.daily_suppressions)
        self.assertTrue(progress.state.card_verified("Q_sp1"))

    def test_collection_never_starts_a_card_that_cannot_fit_daily_absorbs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.state.daily_submaps = 19
            progress.save()
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )
            navigator = SimpleNamespace(
                select_collection_card=lambda *_args, **_kwargs: self.fail(
                    "an incomplete card must not be started"
                )
            )

            result = Collector(task, object(), navigator, progress).run()

        self.assertTrue(result.success)
        self.assertTrue(result.depleted)
        self.assertEqual(0, result.completed_submaps)
        self.assertTrue(progress.state.depleted_today)

    def test_formal_collection_skips_chapter_fourteen_without_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.state.daily_submaps = 18
            progress.state.daily_summons = 12
            progress.state.daily_suppressions = 12
            progress.save()
            selected = []
            warnings = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda value: warnings.append(value),
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                selected.append((card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED,
                    NavigationResult(True, ScreenState.SANDBOX),
                )

            navigator = SimpleNamespace(
                select_collection_card=select,
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                advance_collection_map=lambda *_args: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                open_story_quick_switcher_from_sandbox=lambda: NavigationResult(
                    True,
                    ScreenState.CARD_MENU,
                ),
                inspect_collection_card_completion=lambda _card_id: CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    NavigationResult(True, ScreenState.CARD_MENU),
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            collector._start_search = lambda **_kwargs: SearchCountdownSession(
                (0.1, 0.2, 0.3, 0.4),
                80,
            )
            collector._verify_search_countdown = lambda _session: True
            collector._use_actions = lambda _actions, **_kwargs: SkillExecutionResult(True)
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (CARD_BY_ID["Q_sp14"], CARD_BY_ID["Q_sp15"]),
            ):
                result = collector.run()

        self.assertTrue(result.success)
        self.assertEqual([("Q_sp15", False)], selected)
        self.assertEqual(set(), progress.state.completed_targets("Q_sp14"))
        self.assertTrue(progress.state.card_verified("Q_sp15"))
        self.assertTrue(any("第14章" in value for value in warnings))

    def test_observed_skill_limit_finishes_current_battle_actions_then_stops(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )
            events = []
            navigator = SimpleNamespace(
                select_collection_card=lambda *_args, **_kwargs: CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED,
                    NavigationResult(True, ScreenState.SANDBOX),
                ),
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                advance_collection_map=lambda _card_id, current, target: (
                    events.append(("advance", current.key, target.key))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                open_story_quick_switcher_from_sandbox=lambda: self.fail(
                    "battle two must be left for the next daily cycle"
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            collector._start_search = lambda **_kwargs: SearchCountdownSession(
                (0.1, 0.2, 0.3, 0.4),
                80,
            )
            collector._verify_search_countdown = lambda _session: True
            action_results = iter(
                (
                    SkillExecutionResult(True),
                    SkillExecutionResult(True, depleted=True),
                )
            )
            collector._use_actions = lambda _actions, **_kwargs: next(action_results)
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (CARD_BY_ID["Q_sp1"],),
            ):
                result = collector.run()

        self.assertTrue(result.success)
        self.assertTrue(result.depleted)
        self.assertEqual(2, result.completed_submaps)
        self.assertEqual(
            {"main_area", "battle_area_1"},
            progress.state.completed_targets("Q_sp1"),
        )
        self.assertEqual(
            [("advance", "main_area", "battle_area_1")],
            events,
        )


    def test_final_map_pending_is_success_warning_and_completed_target_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            card = CARD_BY_ID["Q_sp1"]
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                info_set=lambda *_args: None,
                log_warning=lambda *_args: None,
            )
            selected = []
            navigator = SimpleNamespace(
                select_collection_card=lambda card_id, **_kwargs: (
                    selected.append(card_id)
                    or CollectionCardSelectionResult(
                        CollectionCardSelectionOutcome.ENTERED,
                        NavigationResult(True, ScreenState.SANDBOX),
                    )
                ),
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True, ScreenState.SANDBOX
                ),
                advance_collection_map=lambda *_args: NavigationResult(
                    True, ScreenState.SANDBOX
                ),
                open_story_quick_switcher_from_sandbox=lambda: NavigationResult(
                    True, ScreenState.CARD_MENU
                ),
                inspect_collection_card_completion=lambda _card_id: NavigationResult(
                    True, ScreenState.CARD_MENU
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            collector._start_search = lambda **_kwargs: SearchCountdownSession(
                (0.1, 0.2, 0.3, 0.4), 80
            )
            collector._verify_search_countdown = lambda _session: True

            def use_actions(actions, *, card_id, map_role):
                for action in actions:
                    limit = {"吸收": 21, "召集": 21, "压制": 60}[action.name]
                    progress.arm_action(
                        card_id,
                        map_role,
                        action.name,
                        baseline=(0, limit),
                    )
                    progress.mark_action_local_done(
                        card_id,
                        map_role,
                        action.name,
                        pending=True,
                    )
                return SkillExecutionResult(
                    True,
                    pending_actions=tuple(action.name for action in actions),
                )

            collector._use_actions = use_actions
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (card,),
            ):
                result = collector.run()

            self.assertTrue(result.success)
            self.assertIn("末图有", result.message)
            self.assertTrue(progress.state.card_complete(card.card_id))
            self.assertEqual(7, progress.pending_count())

            # A rerun sees the durable verified target and must not select or
            # click it merely because count settlement remains pending.
            selected.clear()
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (card,),
            ):
                resumed = Collector(task, object(), navigator, progress).run()
            self.assertTrue(resumed.success)
            self.assertEqual([], selected)


class CollectorSkillTest(unittest.TestCase):
    @staticmethod
    def _skill_collector(states, counts):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        statuses = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        vision = SimpleNamespace(
            click_reference=lambda *_args, **_kwargs: None,
            wait_template=lambda *_args, **_kwargs: MatchResult(
                0.99,
                (100, 100),
                (40, 40),
                pixel_score=0.90,
                zncc_score=0.95,
            ),
            capture=lambda: frame,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
            ocr_text=lambda _frame, name, **_kwargs: "87" if name == "探查倒计时" else "",
            click_client=lambda center, shape, after_sleep=0: clicks.append(
                (center, shape, after_sleep)
            ),
        )
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        executed_icons = set()

        def detect(_frame, icon):
            if icon.name in executed_icons:
                state = (
                    ActionIconState.ABSENT
                    if icon is SEARCH_ICON
                    else ActionIconState.USED
                )
            else:
                state = states[icon.name]
            return ActionIconDetection(
                state,
                MatchResult(
                    0.98,
                    (100, 100),
                    (40, 40),
                    pixel_score=0.70 if state is ActionIconState.USED else 0.95,
                    zncc_score=0.90,
                ),
                0.65 if state is ActionIconState.USED else 1.0,
            )

        collector.action_icons = SimpleNamespace(detect=detect)
        count_iters = {name: iter(values) for name, values in counts.items()}
        collector._read_count_window = (
            lambda action, _detection=None: next(count_iters[action.name])
        )

        def feedback(action):
            executed_icons.add(action.icon.name)
            was_used = states[action.icon.name] is ActionIconState.USED
            return SkillFeedbackObservation(
                "失败反馈" if was_used else "成功反馈",
                "failure" if was_used else "success",
                1.0,
            )

        collector._read_action_feedback = feedback
        return collector, clicks, statuses

    def test_skill_failure_evidence_is_bounded_and_replayable(self):
        warnings = []
        collector = Collector(
            SimpleNamespace(
                config={},
                log_warning=warnings.append,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector._last_skill_observations["召集"] = {
            "state": "unknown",
            "match": 0.9499,
            "pixel": 0.78,
            "zncc": 0.6387,
            "phase": "battle_area_1",
        }
        for index in range(SKILL_FAILURE_EVIDENCE_LIMIT + 5):
            collector._record_skill_failure(
                f"Q_sp{index + 1}",
                "战斗区域1",
                SkillExecutionResult(False, message="召集图标状态未知"),
            )

        evidence = collector.skill_failure_evidence
        self.assertEqual(SKILL_FAILURE_EVIDENCE_LIMIT, len(evidence))
        self.assertEqual("Q_sp6", evidence[0]["card"])
        self.assertEqual("战斗区域1", evidence[-1]["phase"])
        self.assertEqual(0.6387, evidence[-1]["observations"]["召集"]["zncc"])
        self.assertEqual(SKILL_FAILURE_EVIDENCE_LIMIT + 5, len(warnings))

    def test_action_icon_detection_retries_a_transient_miss(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.969, (1500, 850), (50, 45), 0.87, 0.739),
            1.0,
        )
        sleeps = []
        detections = iter((missing, available))
        task = SimpleNamespace(
            config={},
            sleep=lambda seconds: sleeps.append(seconds),
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(capture=lambda: frame)
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector.action_icons = SimpleNamespace(
            detect=lambda *_args: next(detections),
        )

        selected_frame, selected = collector._detect_action_icon(SUMMON_ICON)

        self.assertIs(frame, selected_frame)
        self.assertIs(available, selected)
        self.assertEqual([ACTION_ICON_DETECTION_INTERVAL], sleeps)

    def test_skill_menu_can_merge_each_icon_from_short_stable_window(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.969, (1500, 850), (50, 45), 0.87, 0.739),
            1.0,
        )
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        sleeps = []
        call_number = [0]
        task = SimpleNamespace(
            config={},
            sleep=lambda seconds: sleeps.append(seconds),
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        vision = SimpleNamespace(capture=lambda: frame)
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())

        def detect(_frame, icon):
            frame_number = call_number[0] // 2
            call_number[0] += 1
            if frame_number == 0:
                return available if icon is ABSORB_ICON else missing
            if frame_number == 1:
                return missing if icon is ABSORB_ICON else available
            return missing

        collector.action_icons = SimpleNamespace(detect=detect)

        self.assertTrue(collector._open_skill_menu((ABSORB_ICON, SUMMON_ICON)))
        self.assertEqual(6, call_number[0])
        self.assertEqual(
            [ACTION_ICON_DETECTION_INTERVAL] * 2,
            sleeps,
        )

    def test_action_feedback_region_and_character_threshold_follow_calibration(self):
        self.assertEqual(
            (735 / 1920, 210 / 1080, 1182 / 1920, 270 / 1080),
            ACTION_FEEDBACK_RELATIVE_ROI,
        )
        self.assertEqual(0.80, ACTION_FEEDBACK_CHARACTER_RATIO)
        self.assertEqual((1550, 969, 52, 44), SEARCH_COUNTDOWN_REFERENCE_ROI)
        self.assertEqual(
            {
                "吸收": (1498, 890, 66, 37),
                "召集": (1542, 790, 66, 33),
                "压制": (1645, 743, 75, 33),
            },
            SKILL_FIXED_COUNT_REFERENCE_ROIS,
        )
        self.assertEqual(
            {
                1: (1671, 1011),
                2: (1749, 1011),
                3: (1824, 1011),
            },
            SKILL_GROUP_REFERENCE_POINTS,
        )
        self.assertEqual(
            {
                group: (x / 1920, y / 1080)
                for group, (x, y) in SKILL_GROUP_REFERENCE_POINTS.items()
            },
            SKILL_GROUP_RELATIVE_POINTS,
        )
        self.assertEqual(0.8, SKILL_GROUP_SWITCH_SETTLE_SECONDS)
        self.assertEqual(
            1.0,
            Collector._feedback_character_ratio(
                "在74秒内确认隐藏物品的位置。",
                "在秒内确认隐藏物品的位置",
            ),
        )
        self.assertGreaterEqual(
            Collector._feedback_character_ratio(
                "周围没有可以吸收的拾取勿。",
                "周围没有可以吸收的拾取物",
            ),
            ACTION_FEEDBACK_CHARACTER_RATIO,
        )
        self.assertLess(
            Collector._feedback_character_ratio(
                "召集带奖励的战场怪物",
                "没有可制伏的怪物",
            ),
            ACTION_FEEDBACK_CHARACTER_RATIO,
        )

    def test_action_feedback_window_matches_absorb_failure(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        sleeps = []
        feedbacks = iter(("", "周围没有可以吸收的拾取物。"))
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda seconds: sleeps.append(seconds),
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda _frame, name, **kwargs: (
                    calls.append((name, kwargs))
                    or next(feedbacks)
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        feedback = collector._read_action_feedback(ABSORB_ACTION)

        self.assertEqual("failure", feedback.outcome)
        self.assertEqual(1.0, feedback.ratio)
        self.assertEqual(2, len(calls))
        self.assertEqual([ACTION_OCR_WINDOW_INTERVAL], sleeps)
        self.assertTrue(all(name == "吸收执行反馈" for name, _kwargs in calls))
        self.assertTrue(
            all(
                kwargs["relative_roi"] == ACTION_FEEDBACK_RELATIVE_ROI
                and kwargs["target_height"] == 1080
                for _name, kwargs in calls
            )
        )

    def test_absorb_failure_feedback_has_precedence_over_positive_text(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None, info_set=lambda *_args: None),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda *_args, **_kwargs: (
                    "吸收周围的拾取物 周围没有可以吸收的拾取物"
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        feedback = collector._read_action_feedback(ABSORB_ACTION)

        self.assertEqual("failure", feedback.outcome)

    def test_action_feedback_window_runs_until_timeout_when_ocr_stays_empty(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        sleeps = []
        clock = [0.0]
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda seconds: (
                    sleeps.append(seconds),
                    clock.__setitem__(0, clock[0] + seconds),
                ),
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda _frame, name, **kwargs: (
                    calls.append((name, kwargs)) or ""
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        with patch("src.tasks.map_trade.collector.monotonic", side_effect=lambda: clock[0]):
            feedback = collector._read_action_feedback(SEARCH_ACTION)

        self.assertIsNone(feedback.outcome)
        self.assertEqual(13, len(calls))
        self.assertEqual(ACTION_FEEDBACK_TIMEOUT, sum(sleeps))
        self.assertEqual(ACTION_FEEDBACK_TIMEOUT, clock[0])

    def test_search_waits_after_feedback_match_before_countdown(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        sleeps = []
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1550, 969), (52, 44)),
        )
        absent = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        detections = iter((available, absent))
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda seconds: sleeps.append(seconds),
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: (
                "在44秒内确认隐藏物品的位置。"
                if name == "探查执行反馈"
                else "44"
            ),
            click_client=lambda *_args, **_kwargs: None,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector._open_skill_menu = lambda *_args, **_kwargs: True
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))

        result = collector._start_search()

        self.assertIsInstance(result, SearchCountdownSession)
        self.assertEqual([ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS], sleeps)

    def test_search_countdown_uses_manual_region_after_icon_match(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        clicks = []
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1550, 969), (52, 44), 0.95, 0.92),
            1.0,
        )
        absent = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        detections = iter((available, absent))
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ocr_text=lambda _frame, name, **kwargs: (
                    ocr_calls.append((name, kwargs))
                    or (
                        "在48秒内确认隐藏物品的位置。"
                        if name == "探查执行反馈"
                        else "48"
                    )
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector._open_skill_menu = lambda *_args, **_kwargs: True
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))

        result = collector._start_search()

        self.assertEqual(SEARCH_COUNTDOWN_RELATIVE_ROI, result.relative_roi)
        self.assertEqual(available.match.center, clicks[0][0][0])
        countdown_kwargs = next(
            kwargs for name, kwargs in ocr_calls if name == "探查倒计时"
        )
        self.assertEqual(SEARCH_COUNTDOWN_RELATIVE_ROI, countdown_kwargs["relative_roi"])
        self.assertEqual(SKILL_OCR_UPSCALE, countdown_kwargs["ocr_scale"])

    def test_dimmed_absorb_and_summon_require_failed_execution_feedback(self):
        collector, clicks, _statuses = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.USED,
                "召集": ActionIconState.USED,
            },
            {
                "探查": ((1, 40), (2, 40)),
                "吸收": ((2, 21), (2, 21)),
                "召集": ((1, 21), (1, 21)),
            },
        )

        result = collector._use_skills()

        self.assertTrue(result.completed)
        self.assertFalse(result.depleted)
        self.assertEqual(3, len(clicks))

    def test_skill_ocr_failure_is_not_reported_as_completed(self):
        collector, clicks, _statuses = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {"吸收": (None,)},
        )

        result = collector._use_skills()

        self.assertFalse(result.completed)
        self.assertFalse(result.depleted)
        self.assertIn("OCR 失败", result.message)
        self.assertEqual(1, len(clicks))

    def test_pre_exhausted_available_skill_does_not_complete_current_map(self):
        collector, clicks, _statuses = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {"吸收": ((21, 21),)},
        )

        result = collector._use_skills()

        self.assertFalse(result.completed)
        self.assertTrue(result.depleted)
        self.assertEqual(1, len(clicks))

    def test_mid_sequence_exhaustion_waits_for_all_three_skills(self):
        collector, clicks, _statuses = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {
                "吸收": ((21, 21),),
            },
        )

        result = collector._use_skills()

        self.assertFalse(result.completed)
        self.assertTrue(result.depleted)
        self.assertEqual(1, len(clicks))

    def test_all_three_completed_can_report_depleted_after_completion(self):
        collector, clicks, _statuses = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {
                "吸收": ((20, 21), (21, 21)),
                "召集": ((20, 21), (21, 21)),
            },
        )

        result = collector._use_skills()

        self.assertTrue(result.completed)
        self.assertTrue(result.depleted)
        self.assertEqual(3, len(clicks))

    def test_battle_flow_executes_absorb_summon_and_suppression(self):
        collector, clicks, _statuses = self._skill_collector(
            {
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
                "制服": ActionIconState.AVAILABLE,
            },
            {
                "吸收": ((4, 21), (5, 21)),
                "召集": ((2, 21), (3, 21)),
                "压制": ((6, 60), (7, 60)),
            },
        )

        result = collector._use_actions(BATTLE_ACTIONS)

        self.assertTrue(result.completed)
        self.assertFalse(result.depleted)
        self.assertEqual(3, len(clicks))

    def test_suppression_count_roi_uses_manual_fixed_region(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        detection = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1440, 700), (80, 80), 0.95, 0.92),
            0.95,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: calls.append((name, kwargs)) or "7/60",
        )
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )

        count = collector._read_count(BATTLE_ACTIONS[-1], detection)

        self.assertEqual((7, 60), count)
        self.assertEqual("压制次数", calls[0][0])
        self.assertNotIn("roi", calls[0][1])
        self.assertEqual(1080, calls[0][1]["target_height"])
        self.assertEqual(
            BATTLE_ACTIONS[-1].fixed_count_relative_roi,
            calls[0][1]["relative_roi"],
        )
        self.assertEqual(SKILL_OCR_UPSCALE, calls[0][1]["ocr_scale"])

    def test_post_click_count_ocr_uses_manual_region_after_icon_refresh(self):
        before_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        after_frame = np.ones((1080, 1920, 3), dtype=np.uint8)
        before = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
            0.95,
        )
        after = ActionIconDetection(
            ActionIconState.USED,
            MatchResult(0.98, (960, 420), (52, 48), 0.95, 0.92),
            0.65,
        )
        detections = iter((before, after))
        frames = iter((before_frame, after_frame))
        count_detections = []
        clicks = []
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: next(frames),
                click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
        counts = iter(((0, 21), (1, 21)))
        collector._read_count_window = lambda _action, detection: (
            count_detections.append(detection) or next(counts)
        )
        collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
            "吸收成功",
            None,
        )

        result = collector._use_action(ABSORB_ACTION)

        self.assertTrue(result.completed)
        self.assertEqual([before, after], count_detections)
        self.assertEqual(
            (before.match.center, before_frame.shape, 0.0),
            (clicks[0][0][0], clicks[0][0][1], clicks[0][1]["after_sleep"]),
        )

    def test_absorb_and_summon_count_rois_use_manual_fixed_regions(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        calls = []
        detection = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
            0.95,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: calls.append((name, kwargs)) or "7/21",
        )
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )

        for action in (ABSORB_ACTION, SUMMON_ACTION):
            with self.subTest(action=action.name):
                self.assertIsNone(action.count_roi)
                self.assertEqual((7, 21), collector._read_count(action, detection))

        self.assertEqual(2, len(calls))
        for name, kwargs in calls:
            self.assertIn(name, {"吸收次数", "召集次数"})
            self.assertNotIn("roi", kwargs)
            self.assertEqual(1080, kwargs["target_height"])
            self.assertEqual(
                next(
                    action.fixed_count_relative_roi
                    for action in (ABSORB_ACTION, SUMMON_ACTION)
                    if f"{action.name}次数" == name
                ),
                kwargs["relative_roi"],
            )
            self.assertEqual(SKILL_OCR_UPSCALE, kwargs["ocr_scale"])

    def test_battle_arrival_checks_search_countdown_without_matching_search_icon(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: ocr_calls.append((name, kwargs)) or "44",
        )
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                log_warning=lambda *_args: None,
            ),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(
            detect=lambda *_args: self.fail(
                "active search must not be template-matched after map travel"
            )
        )
        session = SearchCountdownSession((0.4, 0.5, 0.6, 0.7), 45)

        self.assertTrue(collector._verify_search_countdown(session))
        self.assertEqual(
            [
                (
                    "战斗区域1探查倒计时",
                    {
                        "relative_roi": session.relative_roi,
                        "target_height": 1080,
                        "ocr_scale": SKILL_OCR_UPSCALE,
                    },
                )
            ],
            ocr_calls,
        )

    def test_skill_menu_recovery_clicks_group_one_and_retries_icon_detection(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        group_clicks = []
        action_clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1550, 969), (52, 44), 0.95, 0.92),
            1.0,
        )
        detections = iter(
            [missing] * 6 + [available, available, available, missing]
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: group_clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: (
                "在44秒内确认隐藏物品的位置。"
                if name == "探查执行反馈"
                else "44"
            ),
            click_client=lambda center, shape, after_sleep=0: action_clicks.append(
                (center, shape, after_sleep)
            ),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))

        result = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)

        self.assertIsInstance(result, SearchCountdownSession)
        self.assertEqual(
            [
                (
                    SKILL_GROUP_RELATIVE_POINTS[1][0],
                    SKILL_GROUP_RELATIVE_POINTS[1][1],
                    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
                )
            ],
            group_clicks,
        )
        self.assertEqual([(available.match.center, frame.shape, 0.0)], action_clicks)
        self.assertEqual(SEARCH_COUNTDOWN_RELATIVE_ROI, result.relative_roi)

    def test_skill_menu_recovery_fails_after_one_group_one_click(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
        )
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._use_actions(
            (ABSORB_ACTION,),
            map_role=CollectionMapRole.BATTLE_AREA_1,
        )

        self.assertEqual(
            [
                (
                    SKILL_GROUP_RELATIVE_POINTS[1][0],
                    SKILL_GROUP_RELATIVE_POINTS[1][1],
                    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
                )
            ],
            clicks,
        )
        self.assertFalse(result.completed)
        self.assertIn("未确认采集技能栏", result.message)

    def test_skill_menu_recovery_latch_allows_at_most_one_click_across_calls(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        warnings = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda *_args: None,
            log_warning=warnings.append,
            info_set=lambda *_args: None,
        )
        collector = Collector(
            task,
            SimpleNamespace(capture=lambda: frame),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        self.assertFalse(
            collector._open_skill_menu(
                (ABSORB_ICON,),
                allow_group_one_recovery=True,
            )
        )
        self.assertFalse(
            collector._open_skill_menu(
                (ABSORB_ICON,),
                allow_group_one_recovery=True,
            )
        )
        self.assertEqual(1, len(clicks))
        self.assertTrue(any("不再重复点击" in value for value in warnings))

    def test_skill_menu_recovery_is_disabled_without_cartridge_context(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        collector = Collector(
            task,
            SimpleNamespace(capture=lambda: frame),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._start_search()

        self.assertFalse(result.completed)
        self.assertEqual([], clicks)

    def test_missing_action_after_menu_confirmation_never_uses_fixed_action_point(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        collector = Collector(
            task,
            SimpleNamespace(capture=lambda: frame),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector._open_skill_menu = lambda *_args, **_kwargs: True
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._use_action(
            ABSORB_ACTION,
            map_role=CollectionMapRole.BATTLE_AREA_1,
        )

        self.assertFalse(result.completed)
        self.assertIn("未识别到吸收图标", result.message)
        self.assertEqual([], clicks)


    def test_fixed_count_ocr_uses_immediate_three_x_fallback(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: (
                calls.append((name, kwargs))
                or (
                    "2"
                    if kwargs.get("ocr_scale") == SKILL_OCR_UPSCALE
                    else "1/21"
                )
            ),
        )
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )

        self.assertEqual((1, 21), collector._read_count(ABSORB_ACTION))
        self.assertEqual(
            [SKILL_OCR_UPSCALE, SKILL_OCR_FALLBACK_UPSCALE],
            [kwargs["ocr_scale"] for _name, kwargs in calls],
        )

    def test_formal_bare_post_count_keeps_local_success_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            clicks = []
            detections = iter(
                (
                    ActionIconDetection(
                        ActionIconState.AVAILABLE,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.95,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                )
            )
            task = SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                info_set=lambda *_args: None,
                log_warning=lambda *_args: None,
            )
            vision = SimpleNamespace(
                capture=lambda: frame,
                click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
            )
            collector = Collector(task, vision, SimpleNamespace(), progress)
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((0, 21), None))
            collector._read_count_window = lambda action, detection: next(count_values)
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual(("吸收",), result.pending_actions)
            self.assertEqual(1, len(clicks))
            record = progress.get_action_record(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"
            )
            self.assertEqual(CollectionActionState.PENDING.value, record["state"])

    def test_formal_single_outlier_post_count_stays_pending_without_absolute_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            clicks = []
            detections = iter(
                (
                    ActionIconDetection(
                        ActionIconState.AVAILABLE,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.95,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                )
            )
            collector = Collector(
                SimpleNamespace(
                    config={},
                    sleep=lambda *_args: None,
                    info_set=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((0, 21), (17, 21)))
            collector._read_count_window = lambda _action, _detection=None, **_kwargs: next(
                count_values
            )
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual({}, progress.state.observed_counts)
            self.assertFalse(progress.state.depleted_today)
            self.assertEqual(1, progress.effective_used("吸收"))
            record = progress.get_action_record(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"
            )
            self.assertEqual(CollectionActionState.PENDING.value, record["state"])
            self.assertEqual([17, 21], record["observed"])

    def test_formal_used_icon_completes_without_click_or_count_ocr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            clicks = []
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: used)
            collector._read_count_window = lambda *_args, **_kwargs: self.fail(
                "pre-existing USED must not read or click"
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual([], clicks)
            self.assertEqual(
                CollectionActionState.PREEXISTING_USED.value,
                progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")["state"],
            )

    def test_first_day_preexisting_baseline_settles_after_target_and_allows_next_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            first = Collector(
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(capture=lambda: frame),
                SimpleNamespace(),
                progress,
            )
            first.action_icons = SimpleNamespace(detect=lambda *_args: used)

            result = first._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual((), first.skill_failure_evidence)
            record = progress.get_action_record(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"
            )
            self.assertEqual([0, 21], record["baseline"])
            self.assertEqual(CollectionActionState.PREEXISTING_USED.value, record["state"])
            progress.mark_target(
                "Q_sp1", CollectionMapRole.MAIN_AREA.value, require_actions=True
            )
            self.assertEqual(1, progress.effective_used("吸收"))
            self.assertEqual(1, progress.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, progress.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(CollectionActionState.SETTLED.value, record["state"])
            self.assertEqual((1, 21), progress.state.observed_counts["吸收"])

            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            detections = iter((available, used, used))
            clicks = []
            second = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            second.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((1, 21), (2, 21)))

            def stable_count_window(_action, _detection=None, **_kwargs):
                second._last_count_window_stable = True
                return next(count_values)

            second._read_count_window = stable_count_window
            second._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )
            next_result = second._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_1,
            )

            self.assertTrue(next_result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual(2, progress.effective_used("吸收"))
            self.assertEqual(
                CollectionActionState.SETTLED.value,
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收"
                )["state"],
            )

    def test_preexisting_used_checkpoint_covers_older_pending_without_second_increment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            progress.mark_target(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1.value, require_actions=False
            )
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(capture=lambda: frame),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: used)

            def stable_checkpoint(_action, _detection=None, **_kwargs):
                collector._last_count_window_stable = True
                return (2, 21)

            collector._read_count_window = stable_checkpoint
            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertEqual((), result.pending_actions)
            self.assertIn("已由明亮帧对账", result.message)
            self.assertEqual(0, progress.pending_count("吸收"))
            self.assertEqual((2, 21), progress.state.observed_counts["吸收"])
            old = progress.get_action_record(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收"
            )
            current = progress.get_action_record(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收"
            )
            self.assertEqual(CollectionActionState.SETTLED.value, old["state"])
            self.assertEqual(CollectionActionState.SETTLED.value, current["state"])
            self.assertEqual([1, 21], current["baseline"])
            self.assertTrue(current["covered"])
            self.assertEqual(2, progress.effective_used("吸收"))
            progress.mark_target(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_2.value, require_actions=False
            )
            self.assertEqual(2, progress.effective_used("吸收"))
            self.assertEqual(0, progress.reconcile_pending("吸收", (2, 21)))

    def test_restart_armed_intent_blocks_repeat_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21)
            )
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertFalse(result.completed)
            self.assertIn("禁止重复点击", result.message)
            self.assertEqual([], clicks)

    def test_battle_two_bright_checkpoint_settles_battle_one_before_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                pending=True,
            )
            progress.mark_target(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1.value, require_actions=False
            )

            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            detections = iter((available, used, used))
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((1, 21), (2, 21)))

            def read_count_window(_action, _detection=None, **_kwargs):
                collector._last_count_window_stable = True
                return next(count_values)

            collector._read_count_window = read_count_window
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual(
                "settled",
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收"
                )["state"],
            )
            self.assertEqual(
                "settled",
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收"
                )["state"],
            )

    def test_battle_two_stable_observed_after_local_lower_bound_allows_new_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21)
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )
            self.assertEqual(1, progress.reconcile_pending("吸收", (1, 21)))
            progress.mark_target(
                "Q_sp1", CollectionMapRole.MAIN_AREA.value, require_actions=True
            )

            progress.arm_action(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", baseline=(1, 21)
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            progress.mark_target(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1.value, require_actions=False
            )
            self.assertEqual(2, progress.state.daily_absorbs)

            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            detections = iter((available, used, used))
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((2, 21), (3, 21)))

            def stable_count_window(_action, _detection=None, **_kwargs):
                collector._last_count_window_stable = True
                return next(count_values)

            collector._read_count_window = stable_count_window
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual(
                CollectionActionState.SETTLED.value,
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收"
                )["state"],
            )
            self.assertEqual(
                CollectionActionState.SETTLED.value,
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收"
                )["state"],
            )
            self.assertEqual((3, 21), progress.state.observed_counts["吸收"])

    def test_battle_two_bright_zero_blocks_click_when_battle_one_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            progress.mark_target(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1.value, require_actions=False
            )
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)
            collector._read_count_window = lambda *_args, **_kwargs: (0, 21)

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertFalse(result.completed)
            self.assertIn("待对账", result.message)
            self.assertEqual([], clicks)
            self.assertIsNone(
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收"
                )
            )

    def test_used_single_checkpoint_does_not_settle_old_pending_or_create_current_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            before_observed = dict(progress.state.observed_counts)
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: used)

            def single_checkpoint(_action, _detection=None, **_kwargs):
                collector._last_count_window_stable = False
                return (17, 21)

            collector._read_count_window = single_checkpoint

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertFalse(result.completed)
            self.assertIn("待对账", result.message)
            self.assertEqual([], clicks)
            self.assertEqual(before_observed, progress.state.observed_counts)
            self.assertEqual(1, progress.pending_count("吸收"))
            self.assertIsNone(
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收"
                )
            )

    def test_available_single_checkpoint_blocks_new_click_and_global_reconcile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)

            def single_checkpoint(_action, _detection=None, **_kwargs):
                collector._last_count_window_stable = False
                return (17, 21)

            collector._read_count_window = single_checkpoint

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertFalse(result.completed)
            self.assertIn("窗口不稳定", result.message)
            self.assertEqual([], clicks)
            self.assertEqual({}, progress.state.observed_counts)
            self.assertEqual(1, progress.pending_count("吸收"))
            self.assertIsNone(
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收"
                )
            )

    def test_previous_observed_delta_ignores_new_local_lower_bound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()

            progress.arm_action(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21)
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )
            self.assertEqual(1, progress.reconcile_pending("吸收", (1, 21)))
            progress.mark_target(
                "Q_sp1", CollectionMapRole.MAIN_AREA.value, require_actions=True
            )

            progress.arm_action(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", baseline=(1, 21)
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            progress.mark_target(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1.value, require_actions=False
            )

            self.assertEqual(2, progress.state.daily_absorbs)
            self.assertEqual(1, progress.reconcile_pending("吸收", (2, 21)))
            self.assertEqual(0, progress.pending_count("吸收"))
            self.assertEqual((2, 21), progress.state.observed_counts["吸收"])

    def test_click_exception_leaves_formal_action_armed_not_clicked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            collector = Collector(
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        RuntimeError("click interrupted")
                    ),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)
            collector._read_count_window = lambda *_args, **_kwargs: (0, 21)

            with self.assertRaisesRegex(RuntimeError, "click interrupted"):
                collector._use_action(
                    ABSORB_ACTION,
                    card_id="Q_sp1",
                    map_role=CollectionMapRole.MAIN_AREA,
                )

            self.assertEqual(
                "armed",
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"
                )["state"],
            )


class CalendarTest(unittest.TestCase):
    def test_market_refresh_boundaries_use_utc_plus_8_business_dates(self):
        self.assertEqual(23, SALE_PRICE_REFRESH_HOUR)
        self.assertEqual(8, PURCHASE_STOCK_REFRESH_HOUR)

        self.assertEqual(
            date(2026, 7, 19),
            sale_price_calendar_date(datetime(2026, 7, 19, 22, 59, 59, tzinfo=UTC_PLUS_8)),
        )
        self.assertEqual(
            date(2026, 7, 20),
            sale_price_calendar_date(datetime(2026, 7, 19, 23, 0, 0, tzinfo=UTC_PLUS_8)),
        )
        self.assertEqual(
            date(2026, 8, 1),
            sale_price_calendar_date(datetime(2026, 7, 31, 23, 30, tzinfo=UTC_PLUS_8)),
        )

        self.assertEqual(
            date(2026, 7, 18),
            purchase_stock_date(datetime(2026, 7, 19, 7, 59, 59, tzinfo=UTC_PLUS_8)),
        )
        self.assertEqual(
            date(2026, 7, 19),
            purchase_stock_date(datetime(2026, 7, 19, 8, 0, 0, tzinfo=UTC_PLUS_8)),
        )

    def test_sell_reads_current_time_when_loading_calendar_after_23(self):
        selected_days = []
        statuses = []
        logs = []
        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 19, 22, 50, tzinfo=UTC_PLUS_8)
        trader.now_provider = lambda: datetime(2026, 7, 19, 23, 30, tzinfo=UTC_PLUS_8)
        trader.calendar_client = SimpleNamespace(
            load=lambda **_kwargs: SimpleNamespace(
                source="bundled",
                entries_for=lambda day: selected_days.append(day) or (),
            )
        )
        trader.task = SimpleNamespace(
            config={
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": "",
                "出售白名单": "",
                "5星料理": [],
            },
            info_set=lambda key, value: statuses.append((key, value)),
            log_info=logs.append,
        )
        trader.vision = SimpleNamespace(simplify=lambda value: value)

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual([20], selected_days)
        self.assertIn(("出售价表日期", "2026-07-20"), statuses)
        self.assertIn(
            "卖：当前北京时间2026-07-19 23:30:00，按2026-07-20最高价表执行（每日23:00刷新）。",
            logs,
        )

    def test_bundled_calendar_has_version_timezone_and_all_days(self):
        loaded = parse_calendar_payload(BUNDLED_CALENDAR.read_text(encoding="utf-8"), "test")

        self.assertEqual(set(range(1, 32)), set(loaded.days))
        self.assertEqual((), loaded.entries_for(29))
        self.assertGreaterEqual(sum(len(entries) for entries in loaded.days.values()), 60)
        self.assertGreater(len(loaded.entries_for(28)), 0)
        self.assertEqual(
            "S6:异教塔",
            parse_manual_calendar(self._manual("8=透明沙拉@S6:异教塔")).entries_for(8)[0].shop,
        )

    def test_bundled_calendar_days_17_to_20_follow_confirmed_sale_table(self):
        loaded = parse_calendar_payload(BUNDLED_CALENDAR.read_text(encoding="utf-8"))

        self.assertEqual(
            [("米", "S5:沙漠之花"), ("土豆", "S16:三国同盟"), ("泰瑞丝派", "R1:杰登之门")],
            [(entry.item, entry.shop) for entry in loaded.entries_for(17)],
        )
        self.assertEqual(
            ["黄油", "魅惑粉末", "甜辣酱", "藏红花", "萝卜缨"],
            [entry.item for entry in loaded.entries_for(18)],
        )
        butter, charm, *_rest = loaded.entries_for(18)
        self.assertEqual(5500, butter.reserve)
        self.assertTrue(butter.sell)
        self.assertTrue(charm.sell)
        self.assertEqual(["哈密瓜"], [entry.item for entry in loaded.entries_for(19)])
        self.assertEqual(["灵魂鲜奶油"], [entry.item for entry in loaded.entries_for(20)])
        self.assertTrue(loaded.entries_for(20)[0].sell)

    def test_manual_calendar_requires_every_day(self):
        with self.assertRaisesRegex(ValueError, "必须覆盖 1-31 日"):
            parse_manual_calendar("1=透明沙拉@S6:异教塔")

    def test_manual_calendar_rejects_unknown_shop(self):
        with self.assertRaisesRegex(ValueError, "未知商店"):
            parse_manual_calendar(self._manual("8=透明沙拉@不存在"))

    def test_bundled_calendar_is_the_default_and_skips_online_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            sources = temp / "sources.json"
            sources.write_text(
                json.dumps({"global": ["https://unused.test/calendar.json"]}),
                encoding="utf-8",
            )
            client = PriceCalendarClient(
                BUNDLED_CALENDAR,
                temp / "cache.json",
                sources,
            )
            with patch.object(client, "_fetch") as fetch:
                loaded = client.load(use_bundled=True, use_online=True)

            self.assertEqual("bundled", loaded.source)
            fetch.assert_not_called()

    def test_online_failure_uses_valid_cache_without_reenabling_bundled(self):
        payload = json.loads(BUNDLED_CALENDAR.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            sources = temp / "sources.json"
            sources.write_text(
                json.dumps({"global": ["https://invalid.test/calendar.json"]}), encoding="utf-8"
            )
            cache = temp / "cache.json"
            cache.write_text(
                json.dumps({"source": "old", "etag": "x", "payload": payload}), encoding="utf-8"
            )
            client = PriceCalendarClient(BUNDLED_CALENDAR, cache, sources)
            with patch.object(client, "_fetch", side_effect=OSError("offline")):
                self.assertEqual(
                    "cache",
                    client.load(use_bundled=False, use_online=True).source,
                )
            cache.write_text("broken", encoding="utf-8")
            with patch.object(client, "_fetch", side_effect=OSError("offline")):
                with self.assertRaisesRegex(RuntimeError, "在线价表和本地缓存均不可用"):
                    client.load(use_bundled=False, use_online=True)

    def test_manual_calendar_is_used_only_when_bundled_and_online_are_disabled(self):
        client = PriceCalendarClient(BUNDLED_CALENDAR)
        manual = self._manual("8=透明沙拉@S6:异教塔")

        loaded = client.load(
            use_bundled=False,
            use_online=False,
            manual_text=manual,
        )

        self.assertEqual("manual", loaded.source)
        self.assertEqual("透明沙拉", loaded.entries_for(8)[0].item)

    def test_trader_passes_all_three_source_settings_to_calendar_client(self):
        captured = {}

        def load(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(source="manual", entries_for=lambda _day: ())

        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 18)
        trader.calendar_client = SimpleNamespace(load=load)
        trader.task = SimpleNamespace(
            config={
                "使用程序默认价表": False,
                "使用在线价表": False,
                "自定义最高价表": "manual-calendar",
                "出售白名单": "",
                "5星料理": [],
            },
            log_info=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(simplify=lambda value: value)

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(
            {
                "use_bundled": False,
                "use_online": False,
                "manual_text": "manual-calendar",
            },
            captured,
        )

    def test_fetch_sends_cached_etag(self):
        payload = BUNDLED_CALENDAR.read_bytes()
        captured = {}

        class Response:
            headers = {"Content-Type": "application/json", "ETag": '"new"'}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return payload

        def fake_open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

        client = PriceCalendarClient(BUNDLED_CALENDAR, timeout=5.0)
        with patch("urllib.request.urlopen", side_effect=fake_open):
            loaded, _payload, etag = client._fetch(
                "https://example.test/calendar.json", etag='"old"'
            )

        self.assertEqual("https://example.test/calendar.json", loaded.source)
        self.assertEqual('"old"', captured["request"].get_header("If-none-match"))
        self.assertEqual(5.0, captured["timeout"])
        self.assertEqual('"new"', etag)

    def test_bundled_snapshot_covers_default_sale_whitelist(self):
        loaded = parse_calendar_payload(BUNDLED_CALENDAR.read_text(encoding="utf-8"))
        entries = [entry for day in loaded.days.values() for entry in day]
        trader = object.__new__(Trader)
        trader.vision = SimpleNamespace(simplify=lambda value: value)

        for item in DEFAULT_SALE_WHITELIST:
            trader.task = SimpleNamespace(config={"出售白名单": item})
            whitelist = trader._sale_whitelist()
            with self.subTest(item=item):
                self.assertTrue(any(trader._entry_allowed(entry, whitelist) for entry in entries))

    def test_default_sale_whitelist_uses_current_core_recipes(self):
        self.assertTrue(
            {
                "蜂蜜黄油杏仁",
                "香草牛排",
                "冰镇甜点",
                "火烤鱼板棒",
                "鱼子酱蛋包饭",
            }.issubset(DEFAULT_SALE_WHITELIST)
        )
        self.assertNotIn("烤蜂蜜苹果", DEFAULT_SALE_WHITELIST)
        self.assertNotIn("桑格利亚酒", DEFAULT_SALE_WHITELIST)

    @staticmethod
    def _manual(replacement: str = "") -> str:
        day = replacement.split("=", 1)[0] if replacement else ""
        return "\n".join(
            replacement if str(value) == day else f"{value}=" for value in range(1, 32)
        )


class ProgressTest(unittest.TestCase):
    def test_daily_cycle_changes_at_four_am(self):
        before = datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8)
        after = datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)

        self.assertEqual("2026-07-12", daily_cycle_key(before))
        self.assertEqual("2026-07-13", daily_cycle_key(after))

    def test_weekly_cycle_changes_monday_at_four_am(self):
        sunday = datetime(2026, 7, 12, 4, 0, tzinfo=UTC_PLUS_8)
        monday_before = datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8)
        monday_after = datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)

        self.assertEqual("2026-07-06", weekly_cycle_key(sunday))
        self.assertEqual("2026-07-06", weekly_cycle_key(monday_before))
        self.assertEqual("2026-07-13", weekly_cycle_key(monday_after))

    def test_favorite_cartridge_progress_saves_each_card_and_requires_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"

            def now():
                return datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8)

            store = ProgressStore(path, now)
            store.load()

            self.assertTrue(store.should_rebuild_favorites())
            self.assertTrue(store.mark_favorite_card("S1"))
            self.assertFalse(store.mark_favorite_card("S1"))
            self.assertTrue(store.favorite_card_complete("S1"))
            with self.assertRaisesRegex(RuntimeError, "rebuild is incomplete"):
                store.mark_favorites_built()

            resumed = ProgressStore(path, now)
            resumed.load()
            self.assertTrue(resumed.favorite_card_complete("S1"))
            for shop_id in sorted(VALID_FAVORITE_SHOP_IDS - {"S1"}):
                self.assertTrue(resumed.mark_favorite_card(shop_id))
            resumed.mark_favorites_built()
            self.assertFalse(resumed.should_rebuild_favorites())

            resumed.clear_favorite_cards()
            self.assertTrue(resumed.should_rebuild_favorites())
            self.assertEqual(set(), resumed.state.completed_favorite_cards)

    def test_progress_saves_each_submap_and_stops_at_twenty_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            store = ProgressStore(path, lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8))
            store.load()
            for card in COLLECTABLE_CARDS[:7]:
                for target in card.targets:
                    self.assertTrue(
                        store.mark_target(card.card_id, target.key, require_actions=False)
                    )
                    self.assertTrue(path.exists())
                    self.assertFalse(path.with_suffix(".json.tmp").exists())
                self.assertTrue(store.mark_card_verified(card.card_id))

            self.assertEqual(DAILY_ABSORB_LIMIT, store.state.daily_absorbs)
            self.assertEqual(14, store.state.daily_summons)
            self.assertEqual(14, store.state.daily_suppressions)
            self.assertTrue(store.state.depleted_today)
            self.assertEqual(21, store.state.weekly_submap_count)
            self.assertTrue(
                all(store.state.card_verified(card.card_id) for card in COLLECTABLE_CARDS[:7])
            )
            with self.assertRaisesRegex(RuntimeError, "daily collection limit"):
                next_card = COLLECTABLE_CARDS[7]
                store.mark_target(
                    next_card.card_id,
                    next_card.targets[0].key,
                    require_actions=False,
                )

    def test_collection_skill_limits_match_three_two_two_per_card(self):
        self.assertEqual(21, DAILY_ABSORB_LIMIT)
        self.assertEqual(21, DAILY_SUMMON_LIMIT)
        self.assertEqual(60, DAILY_SUPPRESS_LIMIT)

    def test_progress_rejects_pinned_collection_cards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()

            with self.assertRaisesRegex(ValueError, "invalid collection card"):
                store.mark_target(
                    "Q_sp6", CollectionMapRole.MAIN_AREA.value, require_actions=False
                )

    def test_card_visual_verification_requires_all_three_map_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            store.mark_target(
                "Q_sp1", CollectionMapRole.MAIN_AREA.value, require_actions=False
            )

            with self.assertRaisesRegex(RuntimeError, "targets are incomplete"):
                store.mark_card_verified("Q_sp1")

            self.assertFalse(store.state.card_verified("Q_sp1"))

    def test_daily_reset_preserves_weekly_submaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 7, 12, 3, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            for target in CARD_BY_ID["Q_sp1"].targets:
                store.mark_target("Q_sp1", target.key, require_actions=False)
            store.mark_card_verified("Q_sp1")
            now[0] = datetime(2026, 7, 12, 4, 0, tzinfo=UTC_PLUS_8)

            state = ProgressStore(path, lambda: now[0]).load()

            self.assertEqual(
                {target.key for target in CARD_BY_ID["Q_sp1"].targets},
                state.completed_targets("Q_sp1"),
            )
            self.assertEqual(0, state.daily_submaps)
            self.assertEqual(0, state.daily_summons)
            self.assertEqual(0, state.daily_suppressions)
            self.assertFalse(state.depleted_today)
            self.assertTrue(state.card_verified("Q_sp1"))

    def test_weekly_reset_clears_submaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            store = ProgressStore(path, lambda: datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8))
            store.load()
            store.mark_target(
                "Q_sp1", CollectionMapRole.MAIN_AREA.value, require_actions=False
            )

            state = ProgressStore(
                path, lambda: datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)
            ).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.weekly_submap_count)
            self.assertEqual([], state.verified_cards)

    def test_all_seventeen_cards_make_fifty_one_weekly_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: now[0],
            )
            store.load()
            for card_index, card in enumerate(COLLECTABLE_CARDS):
                if card_index in {7, 14}:
                    now[0] = now[0].replace(day=now[0].day + 1)
                    store.load()
                for target in card.targets:
                    store.mark_target(card.card_id, target.key, require_actions=False)

            self.assertEqual(51, store.state.weekly_submap_count)

    def test_schema_one_collection_progress_resets_without_losing_other_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "weekly_key": weekly_cycle_key(now),
                        "daily_key": daily_cycle_key(now),
                        "cards": {"Q_sp1": [0, 1]},
                        "daily_submaps": 5,
                        "depleted_today": False,
                        "favorite_week": weekly_cycle_key(now),
                        "favorite_cards": ["S1"],
                        "cooking_week": weekly_cycle_key(now),
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.daily_submaps)
            self.assertEqual(0, state.daily_summons)
            self.assertEqual(0, state.daily_suppressions)
            self.assertEqual({"S1"}, state.completed_favorite_cards)
            self.assertEqual(weekly_cycle_key(now), state.cooking_week)
            self.assertEqual(STATE_SCHEMA_VERSION, saved["schema_version"])

    def test_schema_two_collection_progress_resets_for_role_specific_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "weekly_key": weekly_cycle_key(now),
                        "daily_key": daily_cycle_key(now),
                        "cards": {
                            "Q_sp1": [
                                "main_area",
                                "battle_area_1",
                                "battle_area_2",
                            ]
                        },
                        "daily_submaps": 3,
                        "depleted_today": False,
                        "favorite_week": weekly_cycle_key(now),
                        "favorite_cards": ["S1"],
                        "cooking_week": weekly_cycle_key(now),
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.daily_absorbs)
            self.assertEqual([], state.verified_cards)
            self.assertEqual({"S1"}, state.completed_favorite_cards)

    def test_corrupt_file_recovers_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            path.write_text("{broken", encoding="utf-8")

            state = ProgressStore(path, lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8)).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(1, len(list(path.parent.glob("progress.corrupt-*.json"))))

    def test_schema_three_migrates_without_losing_collection_or_trade_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8)
            week = weekly_cycle_key(now)
            day = daily_cycle_key(now)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "weekly_key": week,
                        "daily_key": day,
                        "cards": {"Q_sp1": ["main_area"]},
                        "daily_submaps": 4,
                        "daily_summons": 2,
                        "daily_suppressions": 2,
                        "depleted_today": False,
                        "verified_cards": [],
                        "favorite_week": week,
                        "favorite_cards": ["S1"],
                        "cooking_week": week,
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(STATE_SCHEMA_VERSION, saved["schema_version"])
            self.assertEqual({"main_area"}, state.completed_targets("Q_sp1"))
            self.assertEqual(
                (4, 2, 2),
                (state.daily_submaps, state.daily_summons, state.daily_suppressions),
            )
            self.assertEqual({"S1"}, state.completed_favorite_cards)
            self.assertEqual(week, state.cooking_week)

    def test_action_ledger_is_idempotent_and_archives_at_daily_rollover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 8, 11, 3, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()

            self.assertTrue(
                store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            )
            self.assertFalse(
                store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            )
            store.mark_action_clicked("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            store.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )
            self.assertEqual(1, store.pending_count())

            now[0] = datetime(2026, 8, 11, 4, 0, tzinfo=UTC_PLUS_8)
            resumed = ProgressStore(path, lambda: now[0])
            state = resumed.load()

            self.assertEqual({}, state.action_records)
            self.assertEqual(1, len(state.archived_action_records))
            self.assertEqual(
                CollectionActionState.ARCHIVED.value,
                next(iter(state.archived_action_records.values()))["state"],
            )
            self.assertEqual(0, resumed.pending_count())

    def test_pending_reconciliation_rejects_stale_or_wrong_denominator_and_settles_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            store.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )

            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 20)))
            self.assertEqual(1, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, store.pending_count())

    def test_preexisting_baseline_rejects_equal_invalid_and_lower_observations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            self.assertTrue(
                store.mark_action_preexisting_used(
                    "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"
                )
            )
            record = store.get_action_record(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"
            )
            self.assertEqual([0, 21], record["baseline"])
            self.assertEqual({}, store.state.observed_counts)
            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 20)))
            self.assertEqual({}, store.state.observed_counts)
            self.assertEqual(1, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual((1, 21), store.state.observed_counts["吸收"])
            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(CollectionActionState.SETTLED.value, record["state"])

    def test_target_commit_covers_reservation_without_double_counting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            store.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )
            self.assertEqual(1, store.effective_daily_counts()["吸收"])
            self.assertTrue(
                store.mark_target(
                    "Q_sp1", CollectionMapRole.MAIN_AREA.value, require_actions=False
                )
            )
            self.assertEqual(1, store.state.daily_submaps)
            self.assertEqual(1, store.effective_daily_counts()["吸收"])
            self.assertFalse(
                store.mark_target(
                    "Q_sp1", CollectionMapRole.MAIN_AREA.value, require_actions=False
                )
            )

    def test_mark_target_requires_role_actions_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()

            with self.assertRaisesRegex(RuntimeError, "requires durable local action"):
                store.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)
            self.assertEqual(set(), store.state.completed_targets("Q_sp1"))

    def test_reconcile_pending_uses_positive_delta_deterministically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            for role in (
                CollectionMapRole.MAIN_AREA,
                CollectionMapRole.BATTLE_AREA_1,
            ):
                store.arm_action("Q_sp1", role, "吸收", baseline=(0, 21))
                store.mark_action_local_done("Q_sp1", role, "吸收", pending=True)

            self.assertEqual(1, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(1, store.pending_count("吸收"))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(1, store.reconcile_pending("吸收", (2, 21)))
            self.assertEqual(0, store.pending_count("吸收"))
            self.assertEqual((2, 21), store.state.observed_counts["吸收"])

    def test_schema_four_sanitizes_action_keys_and_quarantines_stale_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8)
            day = daily_cycle_key(now)
            week = weekly_cycle_key(now)

            def record(daily, card, role, action, *, key=None):
                canonical = "|".join((daily, card, role, action))
                return key or canonical, {
                    "daily_key": daily,
                    "card_id": card,
                    "map_role": role,
                    "action": action,
                    "state": "pending",
                    "local_done": True,
                    "reservation": True,
                }

            valid_key, valid = record(
                day, "Q_sp1", CollectionMapRole.MAIN_AREA.value, "吸收"
            )
            stale_key, stale = record(
                "2026-08-09", "Q_sp1", CollectionMapRole.MAIN_AREA.value, "吸收"
            )
            malformed_key, malformed = record(
                day, "Q_sp1", CollectionMapRole.MAIN_AREA.value, "吸收", key="not-canonical"
            )
            invalid_action_key, invalid_action = record(
                day, "Q_sp1", CollectionMapRole.MAIN_AREA.value, "探查"
            )
            invalid_role_key, invalid_role = record(day, "Q_sp1", "main", "吸收")
            path.write_text(
                json.dumps(
                    {
                        "schema_version": STATE_SCHEMA_VERSION,
                        "weekly_key": week,
                        "daily_key": day,
                        "action_records": {
                            valid_key: valid,
                            stale_key: stale,
                            malformed_key: malformed,
                            invalid_action_key: invalid_action,
                            invalid_role_key: invalid_role,
                            "broken-value": "not-a-record",
                        },
                        "observed_counts": {
                            "吸收": [1, 21],
                            "探查": [99, 99],
                        },
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()

            self.assertEqual({valid_key}, set(state.action_records))
            self.assertEqual((1, 21), state.observed_counts["吸收"])
            self.assertNotIn("探查", state.observed_counts)
            self.assertGreaterEqual(len(state.archived_action_records), 5)
            self.assertTrue(
                all(
                    record.get("state") == CollectionActionState.ARCHIVED.value
                    and record.get("reservation") is False
                    for record in state.archived_action_records.values()
                )
            )
            resumed_store = ProgressStore(path, lambda: now)
            resumed_store.load()
            self.assertEqual(2, resumed_store.effective_used("吸收"))

    def test_weekly_rollover_archives_unresolved_actions_and_resets_absolute_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            store.arm_action(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21)
            )
            store.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )
            now[0] = datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)

            state = ProgressStore(path, lambda: now[0]).load()

            self.assertEqual("2026-07-13", state.weekly_key)
            self.assertEqual({}, state.action_records)
            self.assertEqual({}, state.observed_counts)
            self.assertEqual(
                (0, 0, 0),
                (state.daily_submaps, state.daily_summons, state.daily_suppressions),
            )
            archived = next(iter(state.archived_action_records.values()))
            self.assertEqual(CollectionActionState.ARCHIVED.value, archived["state"])
            self.assertFalse(archived["reservation"])



if __name__ == "__main__":
    unittest.main()
