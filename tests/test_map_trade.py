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
from src.tasks.map_trade.calendar import (
    PURCHASE_STOCK_REFRESH_HOUR,
    SALE_PRICE_REFRESH_HOUR,
    PriceCalendarClient,
    parse_calendar_payload,
    parse_manual_calendar,
    purchase_stock_date,
    sale_price_calendar_date,
)
from src.tasks.map_trade.collector import Collector
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
    COLLECTABLE_CARDS,
    DEFAULT_SALE_WHITELIST,
    PINNED_CARD_IDS,
    RECIPE_TEMPLATES,
    STORY_CARDS,
    CalendarEntry,
    CollectionResult,
    MatchResult,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import (
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
    HOME_TEMPLATES,
    Q_SP6_BARGAIN_CONFIRM_DELAY,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PAGE_KEYWORDS,
    Q_SP6_SHOP_PAGE_OCR_INTERVAL,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_SHOP_TEMPLATE,
    Q_SP6_SHOP_VERTICAL_OFFSET,
    Q_SP6_STORY_NUMBER,
    QUICK_SWITCH_CARTRIDGE_REGION,
    QUICK_SWITCH_PAGE_KEYWORDS,
    QUICK_SWITCH_SCROLL_INTERVAL,
    QUICK_SWITCH_SCROLL_POINT,
    QUICK_SWITCH_SCROLL_RESET_AMOUNT,
    QUICK_SWITCH_SCROLL_RESET_COUNT,
    QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    QUICK_SWITCH_SCROLL_UP_AMOUNT,
    QUICK_SWITCH_SCROLL_UP_COUNT,
    QUICK_SWITCH_TEMPLATE,
    RETURN_HOME_TIMEOUT,
    STORY_BADGE_MIN_MARGIN,
    STORY_BADGE_PIXEL_SCORE,
    STORY_BADGE_SPECS,
    STORY_BADGE_TEMPLATE_SCORE,
    STORY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    STORY_CATEGORY_HIGHLIGHT_REGION,
    STORY_CATEGORY_POINT,
    Navigator,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.progress import (
    UTC_PLUS_8,
    VALID_FAVORITE_SHOP_IDS,
    ProgressStore,
    daily_cycle_key,
    weekly_cycle_key,
)
from src.tasks.map_trade.trader import (
    BUY_ALL_FAVORITES_KEYWORD,
    BUY_ALL_FAVORITES_POINT,
    BUY_ALL_FAVORITES_REGION,
    BUY_ALL_FAVORITES_STABLE_HITS,
    BUY_CONFIRM_DIALOG_REGION,
    BUY_CONFIRM_KEYWORDS,
    BUY_CONFIRM_POINT,
    BUY_CONFIRM_PRE_CLICK_DELAY,
    BUY_CONFIRM_TIMEOUT,
    BUY_TO_SELL_POST_CLICK_DELAY,
    BUY_TO_SELL_PRE_CLICK_DELAY,
    BUY_TO_SELL_SOLD_OUT_KEYWORD,
    FIRST_SALE_ITEM_POINT,
    FIRST_SALE_ITEM_REGION,
    FIRST_SALE_QUANTITY_REGION,
    PREMIUM_RATE_TEMPLATE,
    PRICE_SORT_TEMPLATE,
    SALE_CONFIRM_POINT,
    SALE_DIALOG_REGION,
    SALE_MAX_POINT,
    SALE_SLIDER_REGION,
    SALE_SORT_MAX_CLICKS,
    SELL_MODE_POINT,
    SELL_SORT_MODE_REGION,
    SELL_SORT_OPTION_POINT,
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    SHOP_MODE_TITLE_REGION,
    STAR_PIXEL_THRESHOLD,
    STAR_POST_CLICK_DELAY,
    STAR_TEMPLATE_THRESHOLD,
    Trader,
)
from src.tasks.map_trade.vision import Vision, parse_used_limit
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.MapTradeTask import (
    MAP_OCR_THRESHOLD_KEY,
    MAP_VISION_THRESHOLD_KEY,
    TRADE_OCR_THRESHOLD_KEY,
    TRADE_VISION_THRESHOLD_KEY,
    MapTradeTask,
    _migrate_collection_config,
    _trade_section_migration_values,
)

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
            (
                "快速切换按钮: client=(959,539), "
                "relative=(0.500000,0.500000)"
            ),
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
        template_root = ROOT / "offline-train" / "train-source-screenshots"
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
        task.ocr = lambda **_kwargs: [
            SimpleNamespace(name="确认", x=10, y=20, width=30, height=10)
        ]
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

    def test_skill_count_parser(self):
        self.assertEqual((3, 5), parse_used_limit("3 / 5"))
        self.assertEqual((10, 10), parse_used_limit("次数 10:10"))
        self.assertIsNone(parse_used_limit("11/10"))
        self.assertIsNone(parse_used_limit("次数未知"))


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

        template_root = ROOT / "offline-train" / "train-source-screenshots"
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
        self.assertEqual((1, 2, 3, 4), tuple(
            page.page_number for page in SHOP_CARTRIDGE_PAGES
        ))
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
        trader._scroll_shop_cartridges = (
            lambda scroll_amount, count, interval, after_sleep: scrolls.append(
                (scroll_amount, count, interval, after_sleep)
            )
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
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        )
        visible = iter((False, False, True))
        trader._cartridge_visible = lambda _shop_id, _frame: next(visible)
        scrolls = []
        trader._scroll_shop_cartridges = (
            lambda scroll_amount, count, interval, after_sleep: scrolls.append(
                (scroll_amount, count, interval, after_sleep)
            )
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
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        )
        states = iter((False, True))
        trader._gray_star_present = lambda _frame, _slot, _point: next(states)

        self.assertTrue(trader._align_unfavorited_points("S1"))
        self.assertEqual([(*SHOP_FAVORITE_POINTS[6], STAR_POST_CLICK_DELAY)], clicks)
        self.assertEqual(1.0, STAR_POST_CLICK_DELAY)

    def test_gray_star_detection_anchors_30_square_at_supplied_point(self):
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
                point[0] - 15 / 1920,
                point[1] - 15 / 1080,
                point[0] + 15 / 1920,
                point[1] + 15 / 1080,
            ),
            spec.relative_roi,
        )

    def test_gray_star_recognizer_separates_slot_seven_gray_and_yellow_renders(self):
        point = SHOP_FAVORITE_POINTS[7]
        result = {"value": MatchResult(0.857, (1231, 238), (24, 24), 0.937)}
        yellow = {"value": False}
        task = SimpleNamespace(config={}, info_set=lambda *_args, **_kwargs: None)
        vision = SimpleNamespace(
            match=lambda *_args, **_kwargs: result["value"],
            passes=lambda value, spec: (
                value.score >= spec.threshold
                and value.pixel_score >= spec.min_pixel_score
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

        self.assertEqual(18, len(ids))
        self.assertTrue(PINNED_CARD_IDS.isdisjoint(ids))
        self.assertNotIn("Q_sp6", ids)
        self.assertNotIn("Q_sp20", ids)

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
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda _frame, name, relative_roi: ocr_calls.append(
                (name, relative_roi)
            )
            or next(texts),
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
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
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
        trader._switch_from_completed_buy_to_sell = (
            lambda: actions.append("switch") or True
        )
        trader.sell_max_price_items = lambda: actions.append("sell") or True

        self.assertTrue(trader.run_sell())
        self.assertEqual(["switch", "sell"], actions)
        self.assertFalse(trader._buy_completed_in_current_shop)

    def test_sell_page_does_not_click_when_already_on_sell(self):
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: self.fail(
                "已经在出售页时不应再次点击"
            ),
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
        trader._scroll_shop_cartridges = (
            lambda scroll_amount, count, interval, after_sleep: scrolls.append(
                (scroll_amount, count, interval, after_sleep)
            )
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
            reach_merchant_shop=lambda: NavigationResult(True, ScreenState.SHOP)
        )
        trader._ensure_sell_page = lambda: False
        trader.sell_max_price_items = lambda: self.fail(
            "未确认出售页面时不得加载价表或开始出售"
        )

        self.assertFalse(trader.run_sell())

    def test_price_sort_template_switches_to_premium_and_checks_first_slot(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        price = MatchResult(0.97, (1735, 36), (48, 47), pixel_score=0.96)
        missing = MatchResult(0.20, (1735, 36), (48, 47), pixel_score=0.20)
        client_clicks = []
        fixed_clicks = []
        ocr_calls = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: fixed_clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda *_args: None,
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: price if spec is PRICE_SORT_TEMPLATE else missing,
            passes=lambda result, spec: (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
            ),
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            ),
            ocr_text=lambda _frame, name, relative_roi: ocr_calls.append(
                (name, relative_roi)
            )
            or ("120% 黄油" if name == "出售首格商品" else "8,400"),
            simplify=lambda value: value,
        )

        quantity = trader._prepare_first_sale_item(
            CalendarEntry("黄油", "S2:苍蓝魔女", aliases=("奶油",), reserve=5500)
        )

        self.assertEqual(8400, quantity)
        self.assertEqual([(price.center, frame.shape, 0.5)], client_clicks)
        self.assertEqual([(*SELL_SORT_OPTION_POINT, 0.5)], fixed_clicks)
        self.assertEqual(SELL_SORT_MODE_REGION, PREMIUM_RATE_TEMPLATE.relative_roi)
        self.assertEqual(SELL_SORT_MODE_REGION, PRICE_SORT_TEMPLATE.relative_roi)
        self.assertEqual(
            [
                ("出售首格商品", FIRST_SALE_ITEM_REGION),
                ("出售首格库存", FIRST_SALE_QUANTITY_REGION),
            ],
            ocr_calls,
        )

    def test_price_sort_clicks_option_at_most_twice_until_target_reaches_first_slot(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        price = MatchResult(0.97, (1735, 36), (48, 47), pixel_score=0.96)
        missing = MatchResult(0.20, (1735, 36), (48, 47), pixel_score=0.20)
        texts = iter(("100% 别的商品", "120% 甜辣酱", "400"))
        clicks = []
        client_clicks = []
        sleeps = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=sleeps.append,
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: price if spec is PRICE_SORT_TEMPLATE else missing,
            passes=lambda result, spec: (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
            ),
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            ),
            ocr_text=lambda *_args, **_kwargs: next(texts),
            simplify=lambda value: value,
        )

        self.assertEqual(
            400,
            trader._prepare_first_sale_item(CalendarEntry("甜辣酱", "S10:霍尔蒙克斯")),
        )
        self.assertEqual(
            [(*SELL_SORT_OPTION_POINT, 0.5), (*SELL_SORT_OPTION_POINT, 0.5)],
            clicks,
        )
        self.assertEqual([(price.center, frame.shape, 0.5)], client_clicks)
        self.assertEqual([0.5], sleeps)
        self.assertEqual(2, SALE_SORT_MAX_CLICKS)

    def test_first_slot_without_target_120_percent_marks_item_unavailable(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        premium = MatchResult(0.97, (1735, 36), (48, 47), pixel_score=0.96)
        missing = MatchResult(0.20, (1735, 36), (48, 47), pixel_score=0.20)
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: None,
            sleep=lambda *_args: None,
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: (
                premium if spec is PREMIUM_RATE_TEMPLATE else missing
            ),
            passes=lambda result, spec: (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
            ),
            ocr_text=lambda *_args, **_kwargs: "120% 其他商品",
            simplify=lambda value: value,
        )

        self.assertFalse(
            trader._sell_selected_entry(CalendarEntry("豆子", "S12:海边天使"))
        )
        self.assertTrue(trader._last_sale_unavailable)
        self.assertEqual(
            "未发现120%，可能无货或已经售出",
            trader._last_sale_reason,
        )

    def test_normal_sale_double_clicks_first_slot_then_uses_max_and_sell(self):
        clicks = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            config={"出售保险": False},
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader._prepare_first_sale_item = lambda _entry: 400
        trader._wait_owned_quantity = lambda: 400

        self.assertTrue(
            trader._sell_selected_entry(CalendarEntry("甜辣酱", "S10:霍尔蒙克斯"))
        )
        self.assertEqual(
            [
                (*FIRST_SALE_ITEM_POINT, 0.5),
                (*FIRST_SALE_ITEM_POINT, 0.5),
                (*SALE_MAX_POINT, 0.5),
                (*SALE_CONFIRM_POINT, 0.5),
            ],
            clicks,
        )

    def test_butter_reserve_uses_proportional_slider_point(self):
        clicks = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            config={"出售保险": False},
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
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
            ocr_text=lambda _frame, name, relative_roi: calls.append(
                (name, relative_roi)
            )
            or "拥有 8,400 个",
            simplify=lambda value: value,
        )

        self.assertEqual(8400, trader._wait_owned_quantity(timeout=0.0))
        self.assertEqual([("出售弹窗库存", SALE_DIALOG_REGION)], calls)

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
                "未发现120%，可能无货或已经售出"
                if trader._last_sale_unavailable
                else ""
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
        template_root = ROOT / "offline-train" / "train-source-screenshots"
        templates = [card.template for card in STORY_CARDS]
        templates.extend(RECIPE_TEMPLATES.values())
        templates.extend(
            [QUICK_SWITCH_TEMPLATE.file_name, Q_SP6_SHOP_TEMPLATE.file_name]
        )
        templates.extend(spec.file_name for _number, spec in STORY_BADGE_SPECS)
        templates.extend(
            [PREMIUM_RATE_TEMPLATE.file_name, PRICE_SORT_TEMPLATE.file_name]
        )

        for relative_path in templates:
            with self.subTest(template=relative_path):
                self.assertTrue((template_root / relative_path).is_file())

    def test_daily_trade_task_runs_without_weekly_collection(self):
        actions = []
        task = object.__new__(MapTradeTask)
        task.config = {
            "启用": True,
            "买": True,
            "卖": True,
            "制作料理": True,
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

        self.assertEqual(["buy", "sell", "cooking", "home"], actions)

    def test_buy_entry_uses_common_quick_switch_and_requested_clicks(self):
        clicks = []
        client_clicks = []
        template_clicks = []
        shop_entry_attempts = []
        keyword_checks = []
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
        vision.click_client = (
            lambda point, frame_shape, after_sleep=0: client_clicks.append(
                (point, frame_shape, after_sleep)
            )
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_story_category = lambda: True
        navigator._wait_for_story_badge = (
            lambda _number: (badge_frame, badge_detection)
        )
        shop_entry_results = iter((False, True))
        navigator._enter_q_sp6_shop = (
            lambda timeout, *, log_timeout: shop_entry_attempts.append(
                (timeout, log_timeout)
            )
            or next(shop_entry_results)
        )
        navigator._wait_for_ocr_keywords = (
            lambda keywords, timeout, name: keyword_checks.append((keywords, timeout, name)) or True
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
                (*BARGAIN_CONFIRM_POINT, Q_SP6_BARGAIN_CONFIRM_DELAY),
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

    def test_buy_entry_prioritizes_visible_q_sp6_shop_before_quick_switch(self):
        clicks = []
        shop_entry_attempts = []
        keyword_checks = []
        sleeps = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda seconds: sleeps.append(seconds),
            log_warning=lambda *_args, **_kwargs: None,
            open_cartridge_quick_switcher=lambda **_kwargs: self.fail(
                "visible Q_sp6 shop must bypass quick-switch navigation"
            ),
        )
        vision = SimpleNamespace()
        navigator = Navigator(task, vision)
        navigator._enter_q_sp6_shop = (
            lambda timeout, *, log_timeout: shop_entry_attempts.append(
                (timeout, log_timeout)
            )
            or True
        )
        navigator._wait_for_ocr_keywords = (
            lambda keywords, timeout, name: keyword_checks.append(
                (keywords, timeout, name)
            )
            or True
        )

        result = navigator.enter_q_sp6_buy_flow()

        self.assertTrue(result.success)
        self.assertEqual(
            [(Q_SP6_SHOP_PRIORITY_TIMEOUT, False)],
            shop_entry_attempts,
        )
        self.assertEqual(
            [
                (*BARGAIN_POINT, 0.0),
                (*BARGAIN_CONFIRM_POINT, Q_SP6_BARGAIN_CONFIRM_DELAY),
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
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
            open_cartridge_quick_switcher=lambda **_kwargs: self.fail(
                "visible Q_sp6 shop must bypass quick-switch navigation"
            ),
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator._enter_q_sp6_shop = lambda *_args, **_kwargs: True
        navigator._wait_for_ocr_keywords = (
            lambda keywords, *_args, **_kwargs: keywords != ("砍价",)
        )
        navigator.classify = lambda: ScreenState.MERCHANT_DIALOG

        result = navigator.enter_q_sp6_buy_flow()

        self.assertFalse(result.success)
        self.assertEqual("商店页面未识别到砍价入口", result.message)
        self.assertEqual([], clicks)

    def test_q_sp6_shop_entry_clicks_once_then_waits_for_warehouse_page_ocr(self):
        client_clicks = []
        keyword_checks = []
        warnings = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            sleep=lambda *_args: None,
            log_warning=lambda message: warnings.append(message),
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: MatchResult(
                0.99,
                (1151, 239),
                (30, 28),
                pixel_score=0.98,
            ),
            passes=lambda result, spec: (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
            ),
            click_client=lambda point, frame_shape, after_sleep=0: client_clicks.append(
                (point, frame_shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_ocr_keywords = (
            lambda keywords, timeout, name, interval=0.5: keyword_checks.append(
                (keywords, timeout, name, interval)
            )
            or True
        )

        self.assertTrue(
            navigator._enter_q_sp6_shop(5.0, log_timeout=True)
        )
        self.assertEqual(
            [
                ((1166, 403), frame.shape, 0.0),
            ],
            client_clicks,
        )
        self.assertEqual(
            [
                (
                    Q_SP6_SHOP_PAGE_KEYWORDS,
                    45.0,
                    "商店页面",
                    Q_SP6_SHOP_PAGE_OCR_INTERVAL,
                )
            ],
            keyword_checks,
        )
        self.assertEqual(("仓库", "严加管理"), Q_SP6_SHOP_PAGE_KEYWORDS)
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
        self.assertTrue(all(spec.scale_ratios == (1.0,) for _, spec in STORY_BADGE_SPECS))
        self.assertEqual((191 / 1920, 900 / 1080), BARGAIN_POINT)
        self.assertEqual((1047 / 1920, 652 / 1080), BARGAIN_CONFIRM_POINT)
        self.assertEqual("image/green/BusinQuickIcoGE.png", QUICK_SWITCH_TEMPLATE.file_name)
        self.assertEqual((0.25, 0.85, 0.65, 1.0), QUICK_SWITCH_TEMPLATE.relative_roi)
        self.assertEqual((0.95, 0.975, 1.0, 1.025, 1.05), QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertEqual(0.72, QUICK_SWITCH_TEMPLATE.min_pixel_score)
        self.assertEqual(0.84, QUICK_SWITCH_TEMPLATE.minimum_safe_threshold)
        self.assertIsNotNone(QUICK_SWITCH_TEMPLATE.candidate_center_roi)
        self.assertTrue(all(spec.min_pixel_score == 0.80 for spec in HOME_TEMPLATES))

    def test_q_sp6_shop_click_offsets_150_reference_pixels_down(self):
        match_1080 = MatchResult(0.99, (1151, 239), (30, 28), pixel_score=0.98)
        match_720 = MatchResult(0.99, (767, 155), (20, 28), pixel_score=0.98)

        self.assertEqual(150 / 1080, Q_SP6_SHOP_VERTICAL_OFFSET)
        self.assertEqual(
            (1166, 403),
            Navigator._q_sp6_shop_click_point(match_1080, (1080, 1920, 3)),
        )
        self.assertEqual(
            (777, 269),
            Navigator._q_sp6_shop_click_point(match_720, (720, 1280, 3)),
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
            )
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
            click_stable_template=lambda spec, timeout, after_sleep: template_clicks.append(
                (spec, timeout, after_sleep)
            )
            or True,
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
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

    def test_collection_card_scroll_resets_down_then_scans_up_in_bottom_region(self):
        scrolls = []
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
        self.assertEqual((0.5, 970 / 1080), QUICK_SWITCH_SCROLL_POINT)
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

    def test_collection_card_entry_handles_insert_prompt_then_reconfirms_sandbox(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        sleeps = []
        states = iter((ScreenState.UNKNOWN, ScreenState.SANDBOX))
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
            click_ocr=lambda patterns, roi, after_sleep, name: clicks.append(
                (tuple(patterns), roi, after_sleep, name)
            )
            or True,
            match=lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: next(states)

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
            click_ocr=lambda patterns, roi, after_sleep, name: ocr_clicks.append(
                (tuple(patterns), roi, after_sleep, name)
            )
            or True,
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
            )
        )
        navigator.classify = lambda _frame=None: next(states)

        result = navigator._wait_for_story_sandbox(12, timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual([(skip_result.center, frame.shape, 0.8)], client_clicks)
        self.assertEqual(
            [((r"确认",), FIRST_CARD_CONFIRM_REGION, 0.8, "首次卡带确认")],
            ocr_clicks,
        )

    def test_buy_entry_stops_when_shop_template_is_not_found_after_story_selection(self):
        clicks = []
        client_clicks = []
        shop_entry_attempts = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
            open_cartridge_quick_switcher=lambda **_kwargs: True,
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
            click_client=lambda point, frame_shape, after_sleep=0: client_clicks.append(
                (point, frame_shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._wait_for_story_category = lambda: True
        navigator._wait_for_story_badge = (
            lambda _number: (badge_frame, badge_detection)
        )
        navigator._enter_q_sp6_shop = (
            lambda timeout, *, log_timeout: shop_entry_attempts.append(
                (timeout, log_timeout)
            )
            or False
        )
        navigator.classify = lambda: ScreenState.UNKNOWN

        result = navigator.enter_q_sp6_buy_flow()

        self.assertFalse(result.success)
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
        task = SimpleNamespace(
            config={"收藏重建周期": "每周"},
            sleep=lambda seconds: actions.append(("sleep", seconds)),
            log_info=lambda message: actions.append(("log", message)),
            log_warning=lambda *_args, **_kwargs: None,
        )
        progress = SimpleNamespace(
            should_rebuild_favorites=lambda every_run=False: actions.append(
                ("should", every_run)
            )
            or True,
            clear_favorite_cards=lambda: actions.append(("clear",)),
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.progress = progress
        trader.now_provider = lambda: datetime(2026, 7, 19, 7, 59, tzinfo=UTC_PLUS_8)
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(
                True, ScreenState.MERCHANT_DIALOG
            )
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

        actions.clear()
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

    def test_buy_all_favorites_uses_current_button_and_confirmation_regions(self):
        clicks = []
        logs = []
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda seconds: logs.append(("sleep", seconds)),
            log_info=lambda message: logs.append(("log", message)),
            log_warning=warnings.append,
        )
        trader._wait_for_buy_all_favorites_button = lambda: True
        trader._wait_for_purchase_confirmation = lambda: True

        self.assertTrue(trader.buy_all_favorites())
        self.assertEqual(
            [
                (*BUY_ALL_FAVORITES_POINT, 0.3),
                (*BUY_CONFIRM_POINT, 0.8),
            ],
            clicks,
        )
        self.assertEqual(
            ((1324 + 1545) / 2 / 1920, (982 + 1029) / 2 / 1080),
            BUY_ALL_FAVORITES_POINT,
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

    def test_buy_all_button_requires_two_consecutive_ocr_hits_in_given_region(self):
        ocr_calls = []
        sleeps = []
        statuses = []
        texts = iter(("一键购买全部收藏", "", "-键购买全部收藏", "一键购买全部收藏"))
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            sleep=sleeps.append,
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda captured, name, relative_roi: ocr_calls.append(
                (captured.shape, name, relative_roi)
            )
            or next(texts),
            simplify=lambda value: value,
        )

        self.assertTrue(trader._wait_for_buy_all_favorites_button())
        self.assertEqual(3, len(sleeps))
        self.assertEqual(BUY_ALL_FAVORITES_KEYWORD, "购买全部收藏")
        self.assertEqual(BUY_ALL_FAVORITES_STABLE_HITS, 2)
        self.assertEqual(
            (1324 / 1920, 982 / 1080, 1545 / 1920, 1029 / 1080),
            BUY_ALL_FAVORITES_REGION,
        )
        self.assertTrue(
            all(call[2] == BUY_ALL_FAVORITES_REGION for call in ocr_calls)
        )
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
            ocr_text=lambda captured, name, relative_roi: ocr_calls.append(
                (captured.shape, name, relative_roi)
            )
            or text["value"],
            simplify=lambda value: value,
        )

        self.assertFalse(trader._wait_for_purchase_confirmation(timeout=0.0))
        text["value"] = "一键购买全部收藏 是否购买所有加入收藏的商品？"
        self.assertTrue(trader._wait_for_purchase_confirmation(timeout=0.0))
        self.assertEqual(
            ("一键购买全部收藏", "是否购买所有加入收藏的商品"),
            BUY_CONFIRM_KEYWORDS,
        )
        self.assertTrue(
            all(call[2] == BUY_CONFIRM_DIALOG_REGION for call in ocr_calls)
        )

    def test_buy_all_favorites_stops_when_confirmation_is_missing(self):
        clicks = []
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            sleep=lambda *_args: None,
            log_info=lambda *_args, **_kwargs: None,
            log_warning=warnings.append,
        )
        trader._wait_for_buy_all_favorites_button = lambda: True
        trader._wait_for_purchase_confirmation = lambda: False

        self.assertFalse(trader.buy_all_favorites())
        self.assertEqual([(*BUY_ALL_FAVORITES_POINT, 0.3)], clicks)
        self.assertEqual(
            ["买：点击一键购买全部收藏后，未同时识别到确认标题和询问文字。"],
            warnings,
        )

    def test_buy_home_confirmation_requires_button_and_brightness(self):
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        result = MatchResult(0.80, (10, 10), (20, 20), pixel_score=0.90)
        brightness = {"value": 0.74}
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: result,
            passes=lambda *_args: True,
            template_brightness_ratio=lambda *_args: brightness["value"],
        )
        navigator = Navigator(task, vision)

        self.assertFalse(navigator._wait_for_cartridge_home(timeout=0.0))
        brightness["value"] = 0.80
        self.assertTrue(navigator._wait_for_cartridge_home(timeout=0.0))

    def test_return_home_from_shop_closes_discount_shop_then_uses_home_button(self):
        actions = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: actions.append(
                ("click", x, y, after_sleep)
            ),
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
            lambda keywords, timeout, name, interval=0.5, relative_roi=None: actions.append(
                ("ocr", keywords, timeout, name, interval, relative_roi)
            )
            or True
        )
        navigator._wait_for_cartridge_home = lambda timeout: actions.append(
            ("home", timeout)
        ) or True

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
            operate_click=lambda *_args, **_kwargs: self.fail(
                "未确认关闭弹窗时不得继续点击"
            ),
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_reference=lambda x, y, after_sleep=0: actions.append(
                (x, y, after_sleep)
            )
        )
        states = iter((ScreenState.SHOP, ScreenState.SHOP))
        navigator = Navigator(task, vision)
        navigator.classify = lambda: next(states)
        navigator._wait_for_ocr_keywords = lambda *_args, **_kwargs: False

        result = navigator.return_home()

        self.assertFalse(result.success)
        self.assertEqual([(82, 36, 0.0)], actions)

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
        self.assertIn("制作料理", trade.default_config)
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
        self.assertEqual(
            ["料理制作周期", "料理保险", "5星料理"],
            trade.config_type["制作料理"]["sub_configs"][True],
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

    def test_collection_stops_after_three_consecutive_card_failures(self):
        task = SimpleNamespace(
            config={"卡带单步重试次数": 1},
            log_warning=lambda *_args: None,
        )
        progress = ProgressStore(
            Path(tempfile.gettempdir()) / "unused-map-trade-test.json",
            lambda: datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8),
        )
        progress.state = SimpleNamespace(
            depleted_today=False,
            daily_submaps=0,
            weekly_submap_count=0,
            completed_submaps=lambda _card: set(),
        )
        progress.load = lambda: progress.state
        navigator = SimpleNamespace(
            select_card=lambda _card: NavigationResult(True, ScreenState.SANDBOX),
            enter_collection_submap=lambda _index: NavigationResult(
                False, ScreenState.UNKNOWN, "failed"
            ),
        )

        result = Collector(task, object(), navigator, progress).run()

        self.assertFalse(result.success)
        self.assertEqual("连续三张卡带采集失败", result.message)


class CalendarTest(unittest.TestCase):
    def test_market_refresh_boundaries_use_utc_plus_8_business_dates(self):
        self.assertEqual(23, SALE_PRICE_REFRESH_HOUR)
        self.assertEqual(8, PURCHASE_STOCK_REFRESH_HOUR)

        self.assertEqual(
            date(2026, 7, 19),
            sale_price_calendar_date(
                datetime(2026, 7, 19, 22, 59, 59, tzinfo=UTC_PLUS_8)
            ),
        )
        self.assertEqual(
            date(2026, 7, 20),
            sale_price_calendar_date(
                datetime(2026, 7, 19, 23, 0, 0, tzinfo=UTC_PLUS_8)
            ),
        )
        self.assertEqual(
            date(2026, 8, 1),
            sale_price_calendar_date(
                datetime(2026, 7, 31, 23, 30, tzinfo=UTC_PLUS_8)
            ),
        )

        self.assertEqual(
            date(2026, 7, 18),
            purchase_stock_date(
                datetime(2026, 7, 19, 7, 59, 59, tzinfo=UTC_PLUS_8)
            ),
        )
        self.assertEqual(
            date(2026, 7, 19),
            purchase_stock_date(
                datetime(2026, 7, 19, 8, 0, 0, tzinfo=UTC_PLUS_8)
            ),
        )

    def test_sell_reads_current_time_when_loading_calendar_after_23(self):
        selected_days = []
        statuses = []
        logs = []
        trader = object.__new__(Trader)
        trader.started_at = datetime(2026, 7, 19, 22, 50, tzinfo=UTC_PLUS_8)
        trader.now_provider = lambda: datetime(
            2026, 7, 19, 23, 30, tzinfo=UTC_PLUS_8
        )
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
            "卖：当前北京时间2026-07-19 23:30:00，"
            "按2026-07-20最高价表执行（每日23:00刷新）。",
            logs,
        )

    def test_bundled_calendar_has_version_timezone_and_all_days(self):
        loaded = parse_calendar_payload(BUNDLED_CALENDAR.read_text(encoding="utf-8"), "test")

        self.assertEqual(set(range(1, 32)), set(loaded.days))
        self.assertEqual((), loaded.entries_for(29))
        self.assertGreaterEqual(sum(len(entries) for entries in loaded.days.values()), 100)
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
        self.assertFalse(charm.sell)
        self.assertEqual(["哈密瓜"], [entry.item for entry in loaded.entries_for(19)])
        self.assertEqual(["灵魂鲜奶油"], [entry.item for entry in loaded.entries_for(20)])
        self.assertFalse(loaded.entries_for(20)[0].sell)

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
                for submap in range(3):
                    self.assertTrue(store.mark_submap(card.card_id, submap))
                    self.assertTrue(path.exists())
                    self.assertFalse(path.with_suffix(".json.tmp").exists())

            self.assertEqual(21, store.state.daily_submaps)
            self.assertTrue(store.state.depleted_today)
            self.assertEqual(21, store.state.weekly_submap_count)
            with self.assertRaisesRegex(RuntimeError, "daily collection limit"):
                store.mark_submap(COLLECTABLE_CARDS[7].card_id, 0)

    def test_progress_rejects_pinned_collection_cards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()

            with self.assertRaisesRegex(ValueError, "invalid collection card"):
                store.mark_submap("Q_sp6", 0)

    def test_daily_reset_preserves_weekly_submaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 7, 12, 3, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            store.mark_submap("Q_sp1", 0)
            now[0] = datetime(2026, 7, 12, 4, 0, tzinfo=UTC_PLUS_8)

            state = ProgressStore(path, lambda: now[0]).load()

            self.assertEqual({0}, state.completed_submaps("Q_sp1"))
            self.assertEqual(0, state.daily_submaps)
            self.assertFalse(state.depleted_today)

    def test_weekly_reset_clears_submaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            store = ProgressStore(path, lambda: datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8))
            store.load()
            store.mark_submap("Q_sp1", 0)

            state = ProgressStore(
                path, lambda: datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)
            ).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.weekly_submap_count)

    def test_all_eighteen_cards_make_fifty_four_weekly_submaps(self):
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
                for submap in range(3):
                    store.mark_submap(card.card_id, submap)

            self.assertEqual(54, store.state.weekly_submap_count)

    def test_corrupt_file_recovers_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            path.write_text("{broken", encoding="utf-8")

            state = ProgressStore(path, lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8)).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(1, len(list(path.parent.glob("progress.corrupt-*.json"))))


if __name__ == "__main__":
    unittest.main()
