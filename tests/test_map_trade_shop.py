"""Map-trade shop tests (split from test_map_trade.py)."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks.map_trade.data import (
    SHOP_CARTRIDGE_PAGES,
    SHOP_FAVORITE_POINTS,
)
from src.tasks.map_trade.models import (
    MatchResult,
)
from src.tasks.map_trade.trader import (
    STAR_POST_CLICK_DELAY,
)
from src.tasks.map_trade.trader_constants import (
    SHOP_CARTRIDGE_OCR_RELATIVE_ROI,
    SHOP_UP_SCROLL_RECOGNITION_INTERVAL,
)
from tests.helpers.map_trade import create_trader

ROOT = Path(__file__).resolve().parents[1]


class ShopAndCatalogTest(unittest.TestCase):

    def test_s1_normal_and_dimmed_templates_both_enter_competition(self):
        seen = []
        trader = create_trader()
        trader.vision = SimpleNamespace(
            match_all=lambda _frame, spec, **_kwargs: seen.append(spec.file_name) or ()
        )
        trader._shop_cartridge_template_candidates(
            np.zeros((1080, 1920, 3), dtype=np.uint8)
        )
        self.assertIn(
            "shop/cartridges/story_cartridge_01.png",
            seen,
        )
        self.assertIn(
            "shop/cartridges/story_cartridge_01_dimmed.png",
            seen,
        )

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
        trader = create_trader()
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

    def test_favorite_rebuild_corrects_off_page_landing_via_ocr_top_row(self):
        scrolls = []
        task = SimpleNamespace(
            log_info=lambda *_args, **_kwargs: None,
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda *_args, **_kwargs: None,
        )
        progress = SimpleNamespace(
            favorite_card_complete=lambda _shop_id: False,
            mark_favorite_card=lambda _shop_id: None,
            mark_favorites_built=lambda: None,
        )
        trader = create_trader()
        trader.task = task
        trader.progress = progress
        trader._reset_shop_to_first_page = lambda: True
        confirmations = iter((True, False, True, True, True))
        trader._wait_for_shop_page = lambda _shop_ids: next(confirmations)
        # 第2页落点低一格：顶部行号实测到 S10（行 9），锚点 S11（行 10）。
        trader._shop_list_top_row_index = lambda _frame: 9
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        )
        trader._scroll_shop_cartridges = lambda scroll_amount, count, interval, after_sleep: (
            scrolls.append((scroll_amount, count, interval, after_sleep))
        )
        trader._select_purchase_cartridge = lambda _shop_id: True
        trader._align_unfavorited_points = lambda _shop_id: True

        self.assertTrue(trader.rebuild_favorites())
        self.assertEqual(
            [
                (-1, 9, 0.1, 0.5),
                (-1, 1, 0.0, SHOP_UP_SCROLL_RECOGNITION_INTERVAL),
                (-1, 10, 0.1, 0.5),
                (-1, 1, 0.1, 0.5),
            ],
            scrolls,
        )

    def test_favorite_rebuild_aborts_when_corrected_landing_still_unconfirmed(self):
        scrolls = []
        warnings = []
        task = SimpleNamespace(
            log_info=lambda *_args, **_kwargs: None,
            log_warning=lambda message, *_args, **_kwargs: warnings.append(message),
            info_set=lambda *_args, **_kwargs: None,
        )
        progress = SimpleNamespace(
            favorite_card_complete=lambda _shop_id: False,
            mark_favorite_card=lambda _shop_id: None,
            mark_favorites_built=lambda: None,
        )
        trader = create_trader()
        trader.task = task
        trader.progress = progress
        trader._reset_shop_to_first_page = lambda: True
        confirmations = iter((True, False, False))
        trader._wait_for_shop_page = lambda _shop_ids: next(confirmations)
        trader._shop_list_top_row_index = lambda _frame: 9
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        )
        trader._scroll_shop_cartridges = lambda scroll_amount, count, interval, after_sleep: (
            scrolls.append((scroll_amount, count, interval, after_sleep))
        )
        trader._select_purchase_cartridge = lambda _shop_id: True
        trader._align_unfavorited_points = lambda _shop_id: True

        self.assertFalse(trader.rebuild_favorites())
        self.assertEqual(
            [(-1, 9, 0.1, 0.5), (-1, 1, 0.0, SHOP_UP_SCROLL_RECOGNITION_INTERVAL)],
            scrolls,
        )
        self.assertTrue(warnings)
        self.assertIn("第2页边界卡带", warnings[0])

    def test_reset_shop_page_scrolls_up_one_step_then_recognizes_again(self):
        task = SimpleNamespace(
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda *_args, **_kwargs: None,
        )
        trader = create_trader()
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

    def test_favorite_rebuild_adds_missing_favorite_and_skips_empty_slots(self):
        clicks = []
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda *_args, **_kwargs: None,
        )
        trader = create_trader()
        trader.task = task
        trader.vision = SimpleNamespace(capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8))
        states = {1: "gray", 2: "yellow", 6: "gray"}
        trader._star_state = lambda _frame, slot, _point: states.get(slot)
        trader._wait_for_star_state = lambda _slot, _point, _expected: True

        self.assertTrue(trader._align_unfavorited_points("S1"))
        self.assertEqual(
            [(*SHOP_FAVORITE_POINTS[1], STAR_POST_CLICK_DELAY)],
            clicks,
        )

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
        trader = create_trader()
        trader.task = task
        trader.vision = vision
        trader._star_state = lambda *_args: None

        self.assertTrue(trader._wait_for_star_state(6, SHOP_FAVORITE_POINTS[6], "gray"))
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
        trader = create_trader()
        trader.task = task
        trader.vision = vision
        trader._star_state = lambda *_args: None

        self.assertFalse(trader._wait_for_star_state(6, SHOP_FAVORITE_POINTS[6], "gray"))
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
        trader = create_trader()
        trader.task = task
        trader.vision = vision
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        self.assertEqual("gray", trader._star_state(frame, 7, point))
        result["value"] = MatchResult(0.951, (1231, 238), (24, 24), 0.919)
        yellow["value"] = True
        self.assertEqual("yellow", trader._star_state(frame, 7, point))

    def test_select_purchase_cartridge_reclicks_when_first_click_is_swallowed(self):
        from src.tasks.map_trade import trader_cartridge as cartridge_module

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        result = MatchResult(0.9, (220, 230), (78, 57), pixel_score=0.95)
        detection = SimpleNamespace(best=SimpleNamespace(result=result))
        clock = {"now": 0.0}
        clicks = []

        task = SimpleNamespace(
            info_set=lambda *_args, **_kwargs: None,
            log_warning=lambda *_args, **_kwargs: None,
            sleep=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
        )
        trader = create_trader()
        trader.task = task
        trader._wait_for_cartridge_match = lambda _shop_id: (
            frame,
            SimpleNamespace(),
            result,
        )
        trader._confirmed_shop_cartridge_detections = lambda _frame: {"S3": detection}
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            click_client=lambda center, _shape, after_sleep=0.0: clicks.append(center),
            template_brightness_ratio=lambda *_args, **_kwargs: (
                1.013 if len(clicks) >= 2 else 0.516
            ),
        )

        with patch.object(cartridge_module, "monotonic", lambda: clock["now"]):
            self.assertTrue(trader._select_purchase_cartridge("S3"))

        self.assertEqual(2, len(clicks))

    def _competition_trader(self, scores):
        def match_all(_frame, spec, **_kwargs):
            score = scores.get(spec.file_name)
            if score is None:
                return ()
            return (MatchResult(score, (150, 200), (78, 57), pixel_score=0.9),)

        ocr_boxes = [
            SimpleNamespace(
                name="剧情游戏卡 3",
                confidence=0.95,
                x=160,
                y=205,
                width=140,
                height=23,
            ),
            SimpleNamespace(
                name="迷雾神射手",
                confidence=0.99,
                x=160,
                y=225,
                width=90,
                height=24,
            ),
        ]
        task = SimpleNamespace(
            info_set=lambda *_args, **_kwargs: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        trader = create_trader()
        trader.task = task
        trader.vision = SimpleNamespace(
            match_all=match_all,
            ocr_boxes=lambda *_args, **_kwargs: ocr_boxes,
            threshold_for=lambda _spec: 0.72,
        )
        return trader

    def test_shop_cartridge_competition_accepts_desaturated_720p_margin(self):
        # 720p 未选中行去色渲染后，真实匹配可低至 ~0.90 而 runner 贴近 0.83。
        trader = self._competition_trader(
            {
                "shop/cartridges/story_cartridge_03.png": 0.906,
                "shop/cartridges/story_cartridge_11.png": 0.828,
            }
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        confirmed = trader._confirmed_shop_cartridge_detections(frame)

        self.assertIn("S3", confirmed)
        self.assertAlmostEqual(0.078, confirmed["S3"].margin, places=3)

    def test_shop_cartridge_competition_rejects_near_tie_margin(self):
        trader = self._competition_trader(
            {
                "shop/cartridges/story_cartridge_03.png": 0.906,
                "shop/cartridges/story_cartridge_11.png": 0.870,
            }
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        confirmed = trader._confirmed_shop_cartridge_detections(frame)

        self.assertNotIn("S3", confirmed)

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
        trader = create_trader()
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
        trader = create_trader()
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
        trader = create_trader()
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
