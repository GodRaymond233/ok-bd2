"""Map-trade shop tests (split from test_map_trade.py)."""

import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.tasks.map_trade.data import (
    SHOP_CARTRIDGE_LABELS,
    SHOP_CARTRIDGE_PAGES,
    SHOP_FAVORITE_POINTS,
    SHOP_PURCHASE_REFERENCES,
    SHOP_UNFAVORITED_POINTS,
    shop_purchase_reference,
)
from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    PINNED_CARD_IDS,
    STORY_COLLECTION_MAPS,
    CollectionMapRole,
    MatchResult,
)
from src.tasks.map_trade.trader import (
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    STAR_PIXEL_THRESHOLD,
    STAR_POST_CLICK_DELAY,
    STAR_ROI_HALF_SIZE_X,
    STAR_ROI_HALF_SIZE_Y,
    STAR_TEMPLATE_THRESHOLD,
    Trader,
)
from src.tasks.map_trade.trader_constants import SHOP_CARTRIDGE_OCR_RELATIVE_ROI
from src.tasks.map_trade.vision import Vision
from src.utils.calibration import FHD_1080
from src.utils.image_utils import relative_roi_frame, scale_reference_roi

ROOT = Path(__file__).resolve().parents[1]


class ShopAndCatalogTest(unittest.TestCase):
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

    def test_shop_cartridge_ocr_relative_roi_matches_fhd_rect_at_supported_resolutions(self):
        reference_rect = (200, 70, 300, 1010)
        for bounds in SHOP_CARTRIDGE_OCR_RELATIVE_ROI:
            self.assertGreaterEqual(bounds, 0.0)
            self.assertLessEqual(bounds, 1.0)
        self.assertEqual(1.0, SHOP_CARTRIDGE_OCR_RELATIVE_ROI[3])
        self.assertEqual(SHOP_CARTRIDGE_RECOGNITION_REGION, SHOP_CARTRIDGE_OCR_RELATIVE_ROI)

        for frame_width, frame_height in (
            (1280, 720),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
        ):
            with self.subTest(size=(frame_width, frame_height)):
                frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)
                expected = scale_reference_roi(
                    reference_rect,
                    (frame_width, frame_height),
                    FHD_1080.size,
                )
                left, top, crop = relative_roi_frame(frame, SHOP_CARTRIDGE_OCR_RELATIVE_ROI)
                self.assertEqual(expected[:2], (left, top))
                self.assertEqual((expected[3], expected[2]), crop.shape[:2])
                self.assertGreater(crop.size, 0)

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

    def test_shop_cartridge_competition_pairs_full_frame_ocr_and_template_at_1440p(self):
        # OCR 框契约：ocr_boxes 返回的框必须已是完整客户区坐标（相对 ROI 裁剪偏移
        # 已加回），模板候选与竞争比较才能在 1440p 帧上与 1080p 基线一致地配对。
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        scale = 4 / 3
        scores = {
            "shop/cartridges/story_cartridge_17.png": 0.981,
            "shop/cartridges/story_cartridge_11.png": 0.858,
            "shop/cartridges/story_cartridge_01.png": 0.794,
        }

        def match_all(_frame, spec, **_kwargs):
            score = scores.get(spec.file_name)
            if score is None:
                return ()
            center = (round(235 * scale), round(184 * scale))
            return (MatchResult(score, center, (104, 76), pixel_score=0.95),)

        scaled_ocr_boxes = [
            SimpleNamespace(
                name="剧情游戏卡 17",
                confidence=0.953,
                x=round(318 * scale),
                y=round(184 * scale),
                width=round(140 * scale),
                height=round(23 * scale),
            ),
            SimpleNamespace(
                name="试炼之路",
                confidence=0.992,
                x=round(318 * scale),
                y=round(213 * scale),
                width=round(90 * scale),
                height=round(24 * scale),
            ),
        ]
        ocr_calls = []
        task = SimpleNamespace(
            info_set=lambda *_args, **_kwargs: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = SimpleNamespace(
            match_all=match_all,
            ocr_boxes=lambda _frame, _name, **kwargs: ocr_calls.append(kwargs)
            or scaled_ocr_boxes,
            threshold_for=lambda _spec: 0.72,
        )

        confirmed = trader._confirmed_shop_cartridge_detections(frame)

        self.assertEqual({"S17"}, confirmed.keys())
        detection = confirmed["S17"]
        self.assertEqual("S11", detection.runner_up.shop_id)
        self.assertAlmostEqual(0.123, detection.margin, places=3)
        self.assertEqual("S17", detection.ocr.shop_id)
        self.assertEqual(1.0, detection.ocr.name_similarity)
        self.assertEqual([{"relative_roi": SHOP_CARTRIDGE_OCR_RELATIVE_ROI}], ocr_calls)

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
