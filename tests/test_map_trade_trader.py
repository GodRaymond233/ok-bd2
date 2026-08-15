"""Map-trade trader tests (split from test_map_trade.py)."""

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.map_trade.data import SHOP_CARTRIDGE_PAGES
from src.tasks.map_trade.models import (
    RECIPE_TEMPLATES,
    STORY_CARDS,
    CalendarEntry,
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    BARGAIN_CONFIRM_POINT,
    BARGAIN_POINT,
    CHAPTER_HOME_POINT,
    DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
    DISCOUNT_SHOP_CLOSE_KEYWORDS,
    DISCOUNT_SHOP_CLOSE_POINT,
    DISCOUNT_SHOP_CLOSE_TIMEOUT,
    HOME_TEMPLATES,
    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    Q_SP6_BARGAIN_CLICK_DELAY,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_STORY_NUMBER,
    QUICK_SWITCH_CARTRIDGE_REGION,
    QUICK_SWITCH_PAGE_KEYWORDS,
    QUICK_SWITCH_TEMPLATE,
    RETURN_HOME_ANNOUNCEMENT_KEYWORD_GROUPS,
    RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS,
    RETURN_HOME_ANNOUNCEMENT_OCR_REGION,
    RETURN_HOME_TIMEOUT,
    SANDBOX_TEMPLATES,
    STORY_BADGE_CANDIDATE_ZNCC_SCORE,
    STORY_BADGE_SPECS,
    STORY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    STORY_CATEGORY_HIGHLIGHT_REGION,
    STORY_CATEGORY_POINT,
    TRADE_MERCHANT_CONTEXT_TEMPLATE,
    Navigator,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.navigator_constants import (
    CHAPTER_HOME_RELATIVE_ROI,
    CHAPTER_HOME_TEMPLATES,
    CLASSIFY_CARD_MENU_CATEGORY_REFERENCE_ROI,
    CLASSIFY_CARD_MENU_CATEGORY_RELATIVE_ROI,
    CLASSIFY_CARD_MENU_TITLE_REFERENCE_ROI,
    CLASSIFY_CARD_MENU_TITLE_RELATIVE_ROI,
    CLASSIFY_COOKING_MATERIALS_REFERENCE_ROI,
    CLASSIFY_COOKING_MATERIALS_RELATIVE_ROI,
    CLASSIFY_COOKING_TITLE_REFERENCE_ROI,
    CLASSIFY_COOKING_TITLE_RELATIVE_ROI,
    CLASSIFY_LOADING_REFERENCE_ROI,
    CLASSIFY_LOADING_RELATIVE_ROI,
    CLASSIFY_SHOP_TABS_REFERENCE_ROI,
    CLASSIFY_SHOP_TABS_RELATIVE_ROI,
    CLASSIFY_SHOP_TITLE_REFERENCE_ROI,
    CLASSIFY_SHOP_TITLE_RELATIVE_ROI,
)
from src.tasks.map_trade.progress import UTC_PLUS_8
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
    BUY_TO_SELL_SOLD_OUT_STABLE_HITS,
    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
    SALE_CONFIRM_POINT,
    SALE_DIALOG_REGION,
    SALE_ITEM_NAME_LEFT_OFFSET_X,
    SALE_MAX_POINT,
    SALE_SLIDER_REGION,
    SELL_MODE_POINT,
    SHOP_MODE_TITLE_REGION,
    Trader,
)
from src.tasks.map_trade.vision import Vision
from src.tasks.MapTradeTask import MapTradeTask
from src.utils.calibration import FHD_1080
from src.utils.home_confirmation import HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT
from src.utils.image_utils import relative_roi_frame, scale_reference_roi
from src.utils.template_resolution import offline_template_scale

ROOT = Path(__file__).resolve().parents[1]


class SellFlowTest(unittest.TestCase):
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

    def test_buy_and_sell_switches_current_shop_after_stable_sold_out_template(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        clicks = []
        sleeps = []
        match = MatchResult(
            0.96,
            (408, 248),
            (25, 15),
            pixel_score=0.96,
            zncc_score=0.96,
        )
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
            return "购买"

        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=ocr_text,
            simplify=lambda value: value,
            match=lambda captured, spec: (
                match
                if captured is frame and spec is BUY_TO_SELL_SOLD_OUT_TEMPLATE
                else self.fail("unexpected template match")
            ),
            passes=lambda result, spec: (
                result is match and spec is BUY_TO_SELL_SOLD_OUT_TEMPLATE
            ),
        )
        trader._ensure_sell_page = lambda: True

        self.assertTrue(trader._switch_from_completed_buy_to_sell())
        self.assertEqual(
            [(*SELL_MODE_POINT, BUY_TO_SELL_POST_CLICK_DELAY)],
            clicks,
        )
        self.assertEqual(
            [BUY_TO_SELL_PRE_CLICK_DELAY],
            [value for value in sleeps if value == BUY_TO_SELL_PRE_CLICK_DELAY],
        )
        self.assertEqual(BUY_TO_SELL_SOLD_OUT_STABLE_HITS - 1, sleeps.count(0.25))
        self.assertEqual(
            ("商店买卖页标题", None, SHOP_MODE_TITLE_REGION),
            ocr_calls[0],
        )
        self.assertEqual([ocr_calls[0]] * BUY_TO_SELL_SOLD_OUT_STABLE_HITS, ocr_calls)

    def test_buy_and_sell_accepts_sell_page_without_waiting_for_sold_out(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            sleep=lambda *_args: self.fail("出售页不应等待售罄"),
            operate_click=lambda *_args, **_kwargs: self.fail("出售页不应再次点击"),
            log_info=lambda *_args, **_kwargs: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda *_args, **_kwargs: "出售",
            simplify=lambda value: value,
            match=lambda *_args, **_kwargs: self.fail("出售页不应匹配售罄模板"),
        )

        self.assertTrue(trader._switch_from_completed_buy_to_sell())

    def test_sell_page_switch_retries_when_first_click_is_ignored(self):
        texts = iter(("购买", "购买", "出售"))
        clicks = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda *_args, **_kwargs: next(texts),
            simplify=lambda value: value,
        )

        self.assertTrue(trader._ensure_sell_page())
        self.assertEqual([(*SELL_MODE_POINT, 0.5)] * 2, clicks)

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
            enter_q_sp6_buy_flow=lambda: (
                actions.append("enter") or NavigationResult(True, ScreenState.SHOP)
            ),
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

    def test_locate_sale_items_match_name_and_120_percent_with_left_offset(self):
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

        candidates = trader._locate_sale_items(
            CalendarEntry("水果罐头", "S2:苍蓝魔女"),
            frame,
        )
        self.assertEqual([(640, 462)], [candidate.center for candidate in candidates])
        self.assertEqual(115, SALE_ITEM_NAME_LEFT_OFFSET_X)

    def test_locate_sale_items_reject_when_probe_not_in_120_percent_box(self):
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

        self.assertEqual(
            [],
            trader._locate_sale_items(
                CalendarEntry("水果罐头", "S2:苍蓝魔女"),
                frame,
            ),
        )
        self.assertTrue(trader._last_sale_unavailable)
        self.assertEqual("商品名左侧115参考像素未落在120%框内", trader._last_sale_reason)

    def test_locate_sale_items_scale_left_offset_at_720p(self):
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

        candidates = trader._locate_sale_items(
            CalendarEntry("水果罐头", "S2:苍蓝魔女"),
            frame,
        )
        self.assertEqual([(427, 308)], [candidate.center for candidate in candidates])

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

        candidates = trader._locate_sale_items(CalendarEntry("兽肉", "S3:迷雾神射手"), frame)

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

        self.assertEqual([], trader._locate_sale_items(CalendarEntry("兽肉", "S3"), frame))
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

        candidates = trader._locate_sale_items(CalendarEntry("兽肉", "S3"), frame)

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

        self.assertTrue(trader._sell_selected_entry(CalendarEntry("甜辣酱", "S10:霍尔蒙克斯")))
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

        self.assertTrue(trader._sell_selected_entry(CalendarEntry("兽肉", "S3")))
        self.assertEqual([(620, 572), (620, 572)], clicked)
        self.assertEqual([], scans)

    def test_wait_sale_item_candidates_retries_until_located(self):
        sleeps = []
        warnings = []
        trader = object.__new__(Trader)
        trader.task = SimpleNamespace(
            sleep=sleeps.append,
            log_warning=warnings.append,
        )
        frames = [np.zeros((1080, 1920, 3), dtype=np.uint8) for _ in range(2)]
        calls = []
        trader.vision = SimpleNamespace(capture=lambda: frames[min(len(calls), 1)])
        trader._locate_sale_items = lambda _entry, _frame: (
            calls.append(_frame) or ([] if len(calls) < 2 else [SimpleNamespace(center=(640, 462))])
        )

        located = trader._wait_sale_item_candidates(
            CalendarEntry("水果罐头", "S2:苍蓝魔女"),
            timeout=5.0,
            interval=0.1,
        )
        self.assertEqual([(640, 462)], [candidate.center for candidate in located[0]])
        self.assertIs(frames[1], located[1])
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
                calls.append((name, relative_roi)) or "兽肉 拥有335,005个 1个 可购买94,481个"
            ),
            simplify=lambda value: value,
        )

        self.assertEqual(335005, trader._wait_owned_quantity(timeout=0.0))
        self.assertEqual(94481, trader._wait_available_quantity(timeout=0.0))
        self.assertEqual(
            94481,
            Trader._selected_quantity_from_text("兽肉 拥有335,005个 94481个 可购买94481个"),
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
                "" if name == "出售弹窗完成确认" else next(toast_texts)
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


class TradeAssetsTest(unittest.TestCase):
    def test_card_and_recipe_templates_are_packaged(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        templates = [card.template for card in STORY_CARDS]
        templates.extend(RECIPE_TEMPLATES.values())
        templates.extend(
            [
                QUICK_SWITCH_TEMPLATE.file_name,
                MERCHANT_CLICK_LOCATION_TEMPLATE.file_name,
                BUY_TO_SELL_SOLD_OUT_TEMPLATE.file_name,
            ]
        )
        templates.extend(spec.file_name for _number, spec in STORY_BADGE_SPECS)

        for relative_path in templates:
            with self.subTest(template=relative_path):
                self.assertTrue((template_root / relative_path).is_file())

    def test_sold_out_template_separates_recorded_buy_and_sell_frames(self):
        fixture_root = ROOT / "tests" / "fixtures" / "map_trade" / "trade_shop"
        task = SimpleNamespace(
            config={"跑商识图阈值": 0.72},
            vision_threshold_key="跑商识图阈值",
        )
        vision = Vision(task)
        results = {}
        for name in ("before_purchase.png", "after_purchase.png", "sell_page.png"):
            frame = cv2.imread(str(fixture_root / name), cv2.IMREAD_COLOR)
            self.assertIsNotNone(frame, name)
            results[name] = vision.match(frame, BUY_TO_SELL_SOLD_OUT_TEMPLATE)

        self.assertFalse(
            vision.passes(results["before_purchase.png"], BUY_TO_SELL_SOLD_OUT_TEMPLATE)
        )
        self.assertTrue(
            vision.passes(results["after_purchase.png"], BUY_TO_SELL_SOLD_OUT_TEMPLATE)
        )
        self.assertFalse(
            vision.passes(results["sell_page.png"], BUY_TO_SELL_SOLD_OUT_TEMPLATE)
        )
        positive = results["after_purchase.png"]
        self.assertGreaterEqual(positive.score, 0.95)
        self.assertGreaterEqual(positive.pixel_score, 0.96)
        self.assertGreaterEqual(positive.zncc_score, 0.95)


class BuyEntryTest(unittest.TestCase):
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
        self.assertEqual(
            [Q_SP6_BARGAIN_RECHECK_DELAY, Q_SP6_BARGAIN_CLICK_DELAY],
            sleeps,
        )

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
        self.assertEqual(
            [Q_SP6_BARGAIN_RECHECK_DELAY, Q_SP6_BARGAIN_CLICK_DELAY],
            sleeps,
        )

    def test_buy_entry_does_not_click_bargain_before_bargain_ocr(self):
        clicks = []
        sleeps = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=sleeps.append,
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
        self.assertEqual([Q_SP6_BARGAIN_RECHECK_DELAY], sleeps)

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
            ocr_text=lambda *_args, **_kwargs: self.fail("merchant click step must not call OCR"),
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

    def test_buy_entry_uses_seven_quick_page_labels_and_story_badge_templates(self):
        self.assertEqual(
            (
                "最近",
                "店长游戏卡",
                "剧情游戏卡",
                "角色游戏卡",
                "战斗玩法游戏卡带",
                "生活玩法游戏卡带",
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

        self.assertTrue(navigator._click_merchant_interaction(timeout=1.0, after_sleep=1.2))
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
                    "src.tasks.map_trade.navigator_trade.monotonic",
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

        with patch("src.tasks.map_trade.navigator_trade.monotonic", side_effect=(0.0, 3.0)):
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
                for path in template_root.joinpath("image", "green").glob("Merchant_*.png")
            )
        )

    def test_sell_only_shop_entry_fails_when_shop_ocr_miss_after_positive_entry_click(self):
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
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda *_args, **_kwargs: "",
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)
        navigator.wait_trade_state = lambda wanted, timeout: ScreenState.MERCHANT_DIALOG

        result = navigator._bargain_and_enter_shop()

        self.assertFalse(result.success)
        self.assertEqual(ScreenState.MERCHANT_DIALOG, result.state)
        self.assertEqual([], reference_clicks)
        self.assertIn("商店页OCR未确认", result.message)
        self.assertFalse(any("商店页OCR未确认" in warning for warning in warnings))

    def test_shop_entry_missing_never_clicks_blind_point(self):
        click_ocr_calls = []
        reference_clicks = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_ocr=lambda *args, **kwargs: click_ocr_calls.append((args, kwargs)) or False,
            click_reference=lambda *args, **kwargs: reference_clicks.append((args, kwargs)),
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda *_args, **_kwargs: "",
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        result = navigator._bargain_and_enter_shop()

        self.assertFalse(result.success)
        self.assertEqual([], reference_clicks)
        entry_calls = [
            call
            for _args, kwargs in click_ocr_calls
            for call in [kwargs]
            if "商店入口" in str(call.get("name", ""))
        ]
        self.assertEqual(3, len(entry_calls))
        self.assertIn("未识别到商店/进入商店入口", result.message)


class BuyPhaseAndClassifyTest(unittest.TestCase):
    def test_classify_rois_keep_reference_rect_boundaries_at_supported_resolutions(self):
        roi_pairs = (
            (CLASSIFY_LOADING_REFERENCE_ROI, CLASSIFY_LOADING_RELATIVE_ROI),
            (CLASSIFY_SHOP_TABS_REFERENCE_ROI, CLASSIFY_SHOP_TABS_RELATIVE_ROI),
            (CLASSIFY_SHOP_TITLE_REFERENCE_ROI, CLASSIFY_SHOP_TITLE_RELATIVE_ROI),
            (
                CLASSIFY_CARD_MENU_TITLE_REFERENCE_ROI,
                CLASSIFY_CARD_MENU_TITLE_RELATIVE_ROI,
            ),
            (
                CLASSIFY_CARD_MENU_CATEGORY_REFERENCE_ROI,
                CLASSIFY_CARD_MENU_CATEGORY_RELATIVE_ROI,
            ),
            (CLASSIFY_COOKING_TITLE_REFERENCE_ROI, CLASSIFY_COOKING_TITLE_RELATIVE_ROI),
            (
                CLASSIFY_COOKING_MATERIALS_REFERENCE_ROI,
                CLASSIFY_COOKING_MATERIALS_RELATIVE_ROI,
            ),
        )
        for frame_width, frame_height in (
            (1280, 720),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
        ):
            frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)
            for reference_rect, relative_roi in roi_pairs:
                with self.subTest(size=(frame_width, frame_height), rect=reference_rect):
                    expected = scale_reference_roi(
                        reference_rect,
                        (frame_width, frame_height),
                        FHD_1080.size,
                    )
                    left, top, crop = relative_roi_frame(frame, relative_roi)
                    self.assertEqual(expected[:2], (left, top))
                    self.assertEqual((expected[3], expected[2]), crop.shape[:2])
                    self.assertGreater(crop.size, 0)

    def test_chapter_home_templates_use_nonempty_right_edge_roi_at_supported_resolutions(self):
        for frame_width, frame_height in (
            (1280, 720),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
        ):
            frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)
            expected_left = round(frame_width * 0.86)
            expected_bottom = round(frame_height * 0.18)
            for spec in CHAPTER_HOME_TEMPLATES:
                with self.subTest(size=(frame_width, frame_height), template=spec.name):
                    left, top, crop = relative_roi_frame(frame, spec.relative_roi)
                    self.assertIs(CHAPTER_HOME_RELATIVE_ROI, spec.relative_roi)
                    self.assertEqual((expected_left, 0), (left, top))
                    self.assertEqual(
                        (expected_bottom, frame_width - expected_left),
                        crop.shape[:2],
                    )
                    self.assertEqual(frame_width, left + crop.shape[1])
                    self.assertGreater(crop.size, 0)

    def test_shop_classification_uses_actual_crop_geometry_not_ocr_name(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        expected_crops = {}
        for reference_rect, marker, texts in (
            (CLASSIFY_SHOP_TABS_REFERENCE_ROI, 17, ("购买", "出售")),
            (CLASSIFY_SHOP_TITLE_REFERENCE_ROI, 29, ("仓库", "严加管理")),
        ):
            left, top, width, height = scale_reference_roi(
                reference_rect,
                (frame.shape[1], frame.shape[0]),
                FHD_1080.size,
            )
            frame[top : top + height, left : left + width] = marker
            expected_crops[texts] = frame[top : top + height, left : left + width].copy()

        cropped_frames = []

        def image_driven_ocr(*, frame, **_kwargs):
            cropped_frames.append(frame.copy())
            for texts, expected_crop in expected_crops.items():
                if frame.shape == expected_crop.shape and np.array_equal(frame, expected_crop):
                    return [
                        SimpleNamespace(
                            name=text,
                            confidence=0.99,
                            x=0,
                            y=0,
                            width=10,
                            height=10,
                        )
                        for text in texts
                    ]
            return []

        task = SimpleNamespace(
            config={"跑图跑商 OCR 阈值": 0.2},
            info_set=lambda *_args: None,
            ocr=image_driven_ocr,
        )
        failed = MatchResult(-1.0, (0, 0), (0, 0))
        vision = Vision(task)
        vision.match = lambda *_args: failed
        vision.passes = lambda *_args: False
        vision.template_brightness_ratio = lambda *_args: 0.0
        navigator = Navigator(task, vision)

        self.assertEqual(ScreenState.SHOP, navigator.classify(frame))
        for expected_crop in expected_crops.values():
            self.assertTrue(
                any(
                    crop.shape == expected_crop.shape and np.array_equal(crop, expected_crop)
                    for crop in cropped_frames
                )
            )

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

    def test_successful_phases_emit_standalone_completion_notification(self):
        actions = []
        notifications = []
        task = object.__new__(MapTradeTask)
        task.config = {"买": True, "卖": False}
        task.task_log_name = "跑商"
        task.info_set = lambda *_args: None
        task.log_info = lambda message, notify=False: notifications.append(
            (message, notify)
        )
        task.log_warning = lambda *_args: None
        task.log_error = lambda *_args: None
        task._save_diagnostic = lambda *_args: None
        navigator = SimpleNamespace(
            return_home=lambda: (
                actions.append("home")
                or NavigationResult(True, ScreenState.HOME)
            )
        )
        phases = (
            ("买", "买", lambda: actions.append("buy") or True),
            ("卖", "卖", lambda: actions.append("sell") or True),
        )

        self.assertTrue(task._run_phases(navigator, phases))
        self.assertEqual(["buy", "home"], actions)
        self.assertEqual(("跑商：所有已开启流程完成。", True), notifications[-1])

    def test_return_home_exception_keeps_the_original_phase_failure(self):
        actions = []
        statuses = {}
        errors = []
        diagnostics = []
        task = object.__new__(MapTradeTask)
        task.config = {"买": True, "卖": True}
        task.info_set = statuses.__setitem__
        task.log_info = lambda *_args: None
        task.log_warning = lambda *_args: None
        task.log_error = lambda *args: errors.append(args)
        task._save_diagnostic = diagnostics.append

        def return_home():
            actions.append("home")
            raise RuntimeError("return failed")

        navigator = SimpleNamespace(return_home=return_home)
        phases = (
            ("买", "买", lambda: actions.append("buy") or False),
            ("卖", "卖", lambda: actions.append("sell") or True),
        )

        self.assertFalse(task._run_phases(navigator, phases))
        self.assertEqual(["buy", "home"], actions)
        self.assertEqual("买、返回章节主页", statuses["失败"])
        self.assertEqual(1, len(errors))
        self.assertEqual(
            ["map_trade_买_failed", "map_trade_return_home_error"],
            diagnostics,
        )

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

    def test_return_home_update_notice_is_cleared_then_strictly_reconfirmed(self):
        frames = iter(("notice", "home"))
        clicks = []
        result = MatchResult(0.80, (10, 10), (20, 20), pixel_score=0.90)
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
            log_info=lambda *_args, **_kwargs: None,
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            clear_temporary_home_announcement_if_needed=lambda **_kwargs: False,
        )

        def ocr_text(frame, name, **_kwargs):
            if name == "返回主页公告":
                return "更新 抢先看 7天内不再显示 前往查看"
            if name == "主页抽抽乐" and frame == "home":
                return "抽抽乐"
            return ""

        vision = SimpleNamespace(
            capture=lambda: next(frames),
            match=lambda *_args: result,
            passes=lambda _match, _spec: False,
            template_brightness_ratio=lambda frame, *_args: (
                0.0 if frame == "notice" else 0.80
            ),
            ocr_text=ocr_text,
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        def signals(frame, clear_context=None):
            if frame == "notice":
                return False, -1.0, -1.0, 0.0, "", False
            return True, 0.80, 0.90, 0.80, "抽抽乐", True

        navigator._home_confirmation_signals = signals

        self.assertTrue(
            navigator._wait_for_cartridge_home(
                timeout=1.0,
                allow_return_announcement_cleanup=True,
            )
        )
        self.assertEqual(
            [(*HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT, 0.2)],
            clicks,
        )

    def test_return_home_notice_cleanup_requires_explicit_notice_keywords(self):
        clicks = []
        task = SimpleNamespace(
            config={},
            log_info=lambda *_args, **_kwargs: None,
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
        )
        text = {"value": "更新"}
        vision = SimpleNamespace(
            ocr_text=lambda _frame, name, relative_roi: text["value"],
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        self.assertFalse(
            navigator._clear_return_home_announcement_if_needed(
                frame,
                brightness_ratio=0.0,
            )
        )
        text["value"] = "更新 抢先看"
        self.assertTrue(
            navigator._clear_return_home_announcement_if_needed(
                frame,
                brightness_ratio=0.0,
            )
        )
        self.assertEqual([(*HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT, 0.2)], clicks)
        self.assertIn(("更新", "抢先看"), RETURN_HOME_ANNOUNCEMENT_KEYWORD_GROUPS)
        self.assertEqual(3, RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS)
        self.assertEqual(
            (360 / 1920, 180 / 1080, 1560 / 1920, 900 / 1080),
            RETURN_HOME_ANNOUNCEMENT_OCR_REGION,
        )

    def test_return_home_notice_fixture_uses_expected_ocr_region_and_keywords(self):
        frame = cv2.imread(
            str(
                ROOT
                / "tests"
                / "fixtures"
                / "map_trade"
                / "home_return"
                / "update_notice.png"
            ),
            cv2.IMREAD_COLOR,
        )
        self.assertIsNotNone(frame)
        ocr_shapes = []

        def ocr(**kwargs):
            target = kwargs["frame"]
            ocr_shapes.append(target.shape)
            self.assertGreater(int(np.count_nonzero(target)), 0)
            return [
                SimpleNamespace(name="7天内不再显示"),
                SimpleNamespace(name="更新"),
                SimpleNamespace(name="抢先看"),
                SimpleNamespace(name="前往查看"),
            ]

        task = SimpleNamespace(
            config={"跑商 OCR 阈值": 0.2},
            ocr=ocr,
            info_set=lambda *_args, **_kwargs: None,
            log_info=lambda *_args, **_kwargs: None,
            operate_click=lambda *_args, **_kwargs: None,
        )
        navigator = Navigator(task, Vision(task))

        self.assertTrue(
            navigator._clear_return_home_announcement_if_needed(
                frame,
                brightness_ratio=0.0,
            )
        )
        self.assertEqual([(720, 1200, 3)], ocr_shapes)

    def test_return_home_update_notice_clicks_at_most_three_times(self):
        clicks = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
            log_info=lambda *_args, **_kwargs: None,
            operate_click=lambda x, y, after_sleep=0: clicks.append(
                (x, y, after_sleep)
            ),
            clear_temporary_home_announcement_if_needed=lambda **_kwargs: False,
        )
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda *_args, **_kwargs: "更新 抢先看",
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)
        samples = {"count": 0}

        def signals(_frame, clear_context=None):
            samples["count"] += 1
            if samples["count"] >= 5:
                return True, 0.80, 0.90, 0.80, "抽抽乐", True
            return False, -1.0, -1.0, 0.0, "", False

        navigator._home_confirmation_signals = signals

        self.assertTrue(
            navigator._wait_for_cartridge_home(
                timeout=1.0,
                allow_return_announcement_cleanup=True,
            )
        )
        self.assertEqual(
            [(*HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT, 0.2)] * 3,
            clicks,
        )

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
                "BROWN DUST II 94%" if name == "界面分类加载" else ""
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
            ocr_text=lambda _frame, name, **_kwargs: {
                "跑商界面分类商店页": "购买 出售",
                "跑商界面分类商店标题": "仓库管理石怪 仓库 严加管理 天赋技能 砍价",
            }.get(name, ""),
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
            ocr_text=lambda *_args, **_kwargs: "",
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
            ocr_text=lambda _frame, name, **_kwargs: {
                "界面分类商店页": "购买 出售",
                "界面分类商店标题": "仓库管理石怪 仓库 严加管理 天赋技能",
            }.get(name, ""),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertEqual(ScreenState.SHOP, navigator.classify())

    def test_classify_map_mode_card_menu_and_cooking_use_scoped_signals(self):
        task = SimpleNamespace(config={}, info_set=lambda *_args: None)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed = MatchResult(-1.0, (0, 0), (0, 0))
        texts = {
            "界面分类加载": "",
            "界面分类商店页": "",
            "界面分类商店标题": "",
            "界面分类卡带标题": "",
            "界面分类卡带页": "",
            "界面分类料理标题": "",
            "界面分类料理材料": "",
        }
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda *_args: failed,
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=lambda _frame, name, **_kwargs: texts.get(name, ""),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)
        map_mode = [MapPageMode.DIRECT_TELEPORT]
        navigator._detect_map_page_mode = lambda _frame: SimpleNamespace(mode=map_mode[0])

        self.assertEqual(ScreenState.AREA_MAP, navigator.classify(frame))
        map_mode[0] = MapPageMode.UNKNOWN
        texts["界面分类卡带标题"] = "游戏卡珍藏集"
        self.assertEqual(ScreenState.CARD_MENU, navigator.classify(frame))
        texts["界面分类卡带标题"] = ""
        texts["界面分类料理标题"] = "料理"
        texts["界面分类料理材料"] = "所需材料"
        self.assertEqual(ScreenState.COOKING, navigator.classify(frame))

    def test_return_home_from_shop_closes_discount_shop_then_uses_home_button(self):
        actions = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: actions.append(("click", x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_reference=lambda x, y, after_sleep=0: actions.append(
                ("reference", x, y, after_sleep)
            ),
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda: ScreenState.SHOP
        navigator._wait_for_ocr_keywords = (
            lambda keywords, timeout, name, interval=0.5, relative_roi=None: (
                actions.append(("ocr", keywords, timeout, name, interval, relative_roi)) or True
            )
        )
        navigator._wait_for_cartridge_home = lambda timeout, **kwargs: (
            actions.append(("home", timeout, kwargs)) or True
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
                (
                    "home",
                    RETURN_HOME_TIMEOUT,
                    {"allow_return_announcement_cleanup": True},
                ),
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
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_reference=lambda x, y, after_sleep=0: actions.append((x, y, after_sleep)),
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
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
        navigator._wait_for_cartridge_home = lambda timeout, **kwargs: (
            actions.append(("wait_home", timeout, kwargs)) or True
        )

        result = navigator.return_home()

        self.assertTrue(result.success)
        self.assertEqual(
            [
                (*CHAPTER_HOME_POINT, 0.0),
                (
                    "wait_home",
                    RETURN_HOME_TIMEOUT,
                    {"allow_return_announcement_cleanup": True},
                ),
            ],
            actions,
        )

    def test_return_home_closes_each_confirmed_map_page_before_home(self):
        cases = (
            (
                ScreenState.AREA_MAP,
                {
                    MapPageMode.DIRECT_TELEPORT,
                    MapPageMode.GENERATE_TELEPORT,
                },
            ),
            (
                ScreenState.SANDBOX_MAP,
                {MapPageMode.SANDBOX_LARGE_MAP},
            ),
        )
        for state, expected_modes in cases:
            with self.subTest(state=state):
                actions = []
                task = SimpleNamespace(
                    config={"加载页面等待秒数": 45.0},
                    operate_click=lambda x, y, after_sleep=0: actions.append(
                        ("click", x, y, after_sleep)
                    ),
                )
                navigator = Navigator(task, SimpleNamespace())
                navigator.classify = lambda: state
                navigator._close_confirmed_map_page = (
                    lambda received_modes, **kwargs: (
                        self.assertEqual(expected_modes, received_modes)
                        or actions.append(("close", kwargs))
                        or NavigationResult(True, ScreenState.SANDBOX)
                    )
                )
                navigator._wait_for_cartridge_home = lambda timeout, **kwargs: (
                    actions.append(("home", timeout, kwargs)) or True
                )

                result = navigator.return_home()

                self.assertTrue(result.success)
                self.assertEqual(
                    [
                        ("close", {"timeout": 45.0}),
                        ("click", *CHAPTER_HOME_POINT, 0.0),
                        (
                            "home",
                            RETURN_HOME_TIMEOUT,
                            {"allow_return_announcement_cleanup": True},
                        ),
                    ],
                    actions,
                )

    def test_return_home_does_not_click_home_when_map_close_fails(self):
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            operate_click=lambda *_args, **_kwargs: self.fail(
                "failed map close must stop before home click"
            ),
        )
        navigator = Navigator(task, SimpleNamespace())
        navigator.classify = lambda: ScreenState.AREA_MAP
        navigator._close_confirmed_map_page = lambda *_args, **_kwargs: NavigationResult(
            False,
            ScreenState.AREA_MAP,
            "视觉模式冲突",
            map_page_mode=MapPageMode.UNKNOWN,
        )

        result = navigator.return_home()

        self.assertFalse(result.success)
        self.assertIn("关闭地图页面失败", result.message)

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
        navigator._wait_for_cartridge_home = lambda timeout, **kwargs: (
            actions.append(("wait_home", timeout, kwargs)) or True
        )

        result = navigator.return_home()

        self.assertTrue(result.success)
        self.assertEqual(
            [
                ({ScreenState.HOME, ScreenState.SANDBOX}, 45.0),
                (*CHAPTER_HOME_POINT, 0.0),
                (
                    "wait_home",
                    RETURN_HOME_TIMEOUT,
                    {"allow_return_announcement_cleanup": True},
                ),
            ],
            actions,
        )

    def test_buy_quick_page_requires_all_seven_labels(self):
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        text = {
            "value": (
                "最近 店长游戏卡 剧情游戏卡 角色游戏卡 "
                "战斗玩法游戏卡带 生活玩法游戏卡带"
            )
        }
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
