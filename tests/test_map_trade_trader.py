"""Map-trade trader tests (split from test_map_trade.py)."""

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from ok.task.exceptions import TaskDisabledException

from src.tasks.map_trade.models import (
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
    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    Q_SP6_BARGAIN_CLICK_DELAY,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_STORY_NUMBER,
    QUICK_SWITCH_TEMPLATE,
    RETURN_HOME_ANNOUNCEMENT_KEYWORD_GROUPS,
    RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS,
    RETURN_HOME_ANNOUNCEMENT_OCR_REGION,
    RETURN_HOME_TIMEOUT,
    SANDBOX_TEMPLATES,
    STORY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    Navigator,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.navigator_constants import (
    AREA_MAP_BACK_TEMPLATE,
    CHAPTER_HOME_TEMPLATES,
    CLASSIFY_SHOP_TABS_REFERENCE_ROI,
    CLASSIFY_SHOP_TITLE_REFERENCE_ROI,
    DISCOUNT_SHOP_BACK_POINT,
    PROBE_STORY_BADGE_CONFIRM_SECONDS,
)
from src.tasks.map_trade.progress import UTC_PLUS_8
from src.tasks.map_trade.trader import (
    Trader,
)
from src.tasks.map_trade.trader_constants import (
    BUY_ALL_FAVORITES_KEYWORD,
    BUY_ALL_FAVORITES_STABLE_HITS,
    BUY_CONFIRM_DIALOG_REGION,
    BUY_CONFIRM_KEYWORDS,
    BUY_TO_SELL_POST_CLICK_DELAY,
    BUY_TO_SELL_PRE_CLICK_DELAY,
    BUY_TO_SELL_SOLD_OUT_STABLE_HITS,
    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
    SALE_120_PERCENT_MARKER_BETA_TEMPLATE,
    SALE_120_PERCENT_MARKER_MAX_RESULTS,
    SALE_120_PERCENT_MARKER_PEAK_RADIUS,
    SALE_120_PERCENT_MARKER_TEMPLATE,
    SALE_CLOSE_POINT,
    SALE_CONFIRM_POINT,
    SALE_DIALOG_OPEN_MAX_CLICKS,
    SALE_DIALOG_REGION,
    SALE_EMPTY_NAME_STABLE_HITS,
    SALE_MAX_POINT,
    SALE_MIN_POINT,
    SELL_MODE_POINT,
    SHOP_MODE_TITLE_REGION,
)
from src.tasks.map_trade.vision import Vision
from src.tasks.MapTradeTask import BD2MapTradeSellTestTask, MapTradeTask
from src.utils.calibration import FHD_1080
from src.utils.home_confirmation import HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT
from src.utils.image_utils import scale_reference_roi
from tests.helpers import FakeClock, StageHistoryTestCase
from tests.helpers.map_trade import create_trader
from tests.helpers.recognition_fixtures import load_bgr

ROOT = Path(__file__).resolve().parents[1]


class TradeSellTestTaskTest(unittest.TestCase):
    def test_sell_button_reuses_daily_flow_and_restores_config(self):
        for outcome in (True, False, TaskDisabledException()):
            with self.subTest(outcome=outcome):
                executor = SimpleNamespace(scene=None, get_task_by_class=Mock())
                daily = MapTradeTask(executor, SimpleNamespace())
                executor.get_task_by_class.return_value = daily
                original = dict(daily.default_config)
                original.update({"启用": False, "买": True, "卖": False,
                                 "出售白名单": "黄油", "使用出售黑名单": True})
                daily.config = original
                daily.log_info = daily.log_warning = daily.log_completion = Mock()
                daily._save_diagnostic = Mock()
                task = BD2MapTradeSellTestTask(executor, SimpleNamespace())
                self.assertEqual("测试", task.group_name)
                self.assertTrue(task.visible)
                with (
                    patch("src.tasks.MapTradeTask.Vision"),
                    patch("src.tasks.MapTradeTask.Navigator") as navigator_type,
                    patch("src.tasks.MapTradeTask.ProgressStore"),
                    patch("src.tasks.MapTradeTask.Trader") as trader_type,
                ):
                    trader = trader_type.return_value
                    observed = []

                    def sell():
                        observed.append(dict(daily.config))
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome

                    trader.run_sell.side_effect = sell
                    navigator_type.return_value.return_home.return_value = NavigationResult(
                        True, ScreenState.HOME,
                    )
                    if isinstance(outcome, Exception):
                        with self.assertRaises(TaskDisabledException):
                            task.run()
                    else:
                        self.assertIs(outcome, task.run())
                        self.assertEqual(daily.info_snapshot(), task.info_snapshot())
                    trader_type.assert_called_once()
                    self.assertIs(daily, trader_type.call_args.args[0])
                    trader.run_cooking.assert_not_called()
                    trader.run_buy.assert_not_called()
                    trader.run_sell.assert_called_once_with()
                expected = dict(original, **{"启用": True, "制作料理": False,
                                            "买": False, "卖": True})
                self.assertEqual([expected], observed)
                self.assertIs(original, daily.config)
                self.assertFalse(original["启用"])
                self.assertTrue(original["买"])
                self.assertFalse(original["卖"])


class SellFlowTest(StageHistoryTestCase):
    def test_native_2k_sale_recovery_requires_exact_percent_and_unique_marker(self):
        from unittest.mock import Mock

        entry = CalendarEntry("火烤鱼板棒", "S17:试炼之路")
        for scene in ("new", "old"):
            frame = load_bgr(
                ROOT / f"tests/fixtures/map_trade/trade_shop/sale_native_2k_{scene}.png",
            )
            for texts in (("120%",), ("119%",), ("1120%",), ("4120%",), ("120",),
                          ("12%",), ("120%", "118%"), ()):
                with self.subTest(scene=scene, texts=texts):
                    trader = create_trader()
                    name = self._text_box(entry.item, 795, 226, 141, 34)
                    trader.vision.ocr_boxes = Mock(side_effect=lambda _f, title, **_kw:
                        [name] if title == "出售商品列表" else
                        [self._text_box(text, 672, 230, 55, 20) for text in texts])
                    candidates = trader._locate_sale_items(entry, frame)
                    self.assertEqual(int(texts == ("120%",)), len(candidates))
                    self.assertIsNone(trader._sale_reference_view)
                    if candidates:
                        self.assertEqual((866, 243), candidates[0].center)
                        self.assertTrue(candidates[0].percent_box.reference_view)
                        self.assertAlmostEqual(656, candidates[0].percent_box.x, delta=2)
                        self.assertAlmostEqual(230, candidates[0].percent_box.y, delta=2)
            duplicate = frame.copy()
            duplicate[210:276, 595:795] = 0
            for x in (600, 718):
                duplicate[230:248, x:x + 71] = frame[230:248, 656:727]
            trader.vision.ocr_boxes = Mock(return_value=[name])
            self.assertEqual([], trader._sale_template_percent_boxes(
                duplicate, (595, 210, 200, 66), cv2.cvtColor(duplicate, cv2.COLOR_BGR2GRAY),
            ))
            trader.vision.ocr_boxes.assert_not_called()

    def test_recovered_sale_rechecks_fresh_frame_before_click(self):
        from unittest.mock import Mock

        for changed in ("size", "source", "item"):
            trader = create_trader()
            frame = np.zeros((1440, 2560, 3), np.uint8)
            trader._sale_name_signature = Mock(return_value=())
            trader._sale_toast_id = Mock(return_value=None)
            trader._locate_sale_items = Mock(return_value=[])
            trader.vision.capture = Mock(
                return_value=(frame[:1000] if changed == "size" else frame),
            )
            trader.vision.click_client = Mock()
            candidate = SimpleNamespace(center=(866, 243), percent_box=SimpleNamespace(
                reference_view=True, capture_source="old" if changed == "source" else None,
            ))
            self.assertIsNone(trader._sell_one_candidate(
                CalendarEntry("火烤鱼板棒", "S17"), candidate, frame, previous_owned=None,
            ))
            trader.vision.click_client.assert_not_called()

    def test_pending_sale_reads_stock_and_never_repeats_the_confirmation(self):
        from unittest.mock import Mock

        from src.tasks import run_history

        trader = create_trader()
        trader.task = SimpleNamespace(config={}, log_info=Mock(), log_warning=Mock(),
                                      info_set=Mock(), operate_click=Mock())
        trader.vision = SimpleNamespace(click_client=Mock())
        trader._sale_name_signature = Mock(return_value=())
        trader._sale_toast_id = Mock(return_value=None)
        trader._last_sale_toast_id = None
        trader._wait_sale_dialog_item = Mock(return_value=True)
        trader._wait_owned_quantity = Mock(return_value=100)
        trader._wait_available_quantity = Mock(return_value=50)
        trader._ensure_sell_page = Mock(return_value=True)
        entry = CalendarEntry("苹果", "1")
        candidate = SimpleNamespace(center=(10, 10), percent_box=None)
        frame = np.zeros((30, 30, 3), dtype=np.uint8)
        run_history.save_stage(trader.task, "出售:1:苹果", "pending",
                               evidence={"owned": 100, "selected": 10})
        self.assertIsNone(trader._sell_one_candidate(entry, candidate, frame, previous_owned=None))
        trader.task.operate_click.assert_not_called()
        trader._wait_owned_quantity.return_value = 90
        self.assertEqual((100, True),
                         trader._sell_one_candidate(entry, candidate, frame, previous_owned=None))
        self.assertEqual("done", run_history.read_stage(trader.task, "出售:1:苹果")["state"])
        self.assertEqual(10, trader._sale_sold[entry])
        self.assertEqual(10, trader._sale_recovered[entry])

        entry = CalendarEntry("苹果", "1", reserve=5500)
        trader._wait_owned_quantity.return_value = 8400
        trader._wait_available_quantity.return_value = 8400
        trader._choose_sale_quantity = Mock(return_value=True)
        trader._wait_sale_completion = Mock(return_value=False)
        trader.vision.capture = Mock(return_value=frame)
        trader.vision.ocr_text = Mock(return_value="拥有 8400 个 可购买 8400 个 2850 个")
        self.assertIsNone(trader._sell_one_candidate(entry, candidate, frame, previous_owned=None))
        pending = run_history.read_stage(trader.task, "出售:1:苹果")
        self.assertEqual("pending", pending["state"])
        self.assertEqual({"owned": 8400, "selected": 2850}, pending["evidence"])
        trader.task.operate_click.reset_mock()
        trader._wait_owned_quantity.return_value = 5550
        self.assertEqual((8400, True), trader._sell_one_candidate(
            entry, candidate, frame, previous_owned=None))
        self.assertEqual("done", run_history.read_stage(trader.task, "出售:1:苹果")["state"])
        self.assertEqual(2850, trader._sale_sold[entry])
        trader.task.operate_click.assert_called_once_with(*SALE_CLOSE_POINT, after_sleep=0.5)
        entry = CalendarEntry("苹果", "1", reserve=90)
        trader._wait_owned_quantity.return_value = 90
        run_history.save_stage(trader.task, "出售:1:苹果", "pending",
                               evidence={"owned": 100, "selected": None})
        self.assertEqual((90, False),
                         trader._sell_one_candidate(entry, candidate, frame, previous_owned=None))
        self.assertEqual("done", run_history.read_stage(trader.task, "出售:1:苹果")["state"])

        trader._reset_sale_results()
        run_history.save_stage(trader.task, "出售:1:苹果", "pending",
                               evidence={"owned": 100, "selected": 10})
        self.assertEqual((90, False), trader._sell_one_candidate(
            entry, candidate, frame, previous_owned=None))
        self.assertEqual(10, trader._sale_sold[entry])
        self.assertIn("已达到保留量 90 个", trader._sale_not_sold[entry])

        trader._reset_sale_results()
        run_history.save_stage(trader.task, "出售:1:苹果", "pending",
                               evidence={"owned": 100, "selected": 10})

        def empty_page(_entry):
            trader._last_sale_page_empty = True
            return None

        trader._wait_sale_item_candidates = empty_page
        self.assertTrue(trader._sell_selected_entry(entry))
        self.assertEqual({}, trader._sale_sold)
        self.assertIn("出售数量未确认", trader._sale_not_sold[entry])

    @staticmethod
    def _sale_name_match(text, names):
        trader = create_trader()
        trader.vision = SimpleNamespace(simplify=lambda value: value)
        catalog = trader._sale_title_catalog(CalendarEntry(names[0], "", aliases=names[1:]))
        return Trader._sale_name_matches(text, names, catalog)

    def _make_sale_template_trader(self, ocr_boxes, markers=()):
        trader = create_trader()
        match_calls = []

        def match_all(frame, spec, **kwargs):
            match_calls.append((frame, spec, kwargs))
            search_roi = kwargs.get("search_roi")
            if search_roi is None:
                return tuple(markers)
            left, top, width, height = search_roi
            right = left + width
            bottom = top + height
            return tuple(
                marker
                for marker in markers
                if left <= marker.position[0]
                and top <= marker.position[1]
                and marker.position[0] + marker.size[0] <= right
                and marker.position[1] + marker.size[1] <= bottom
            )

        trader.vision = SimpleNamespace(
            ocr_boxes=ocr_boxes,
            simplify=lambda value: value,
            match_all=match_all,
            threshold_for=lambda spec: spec.threshold,
        )
        trader.task = SimpleNamespace(info_set=lambda *_args: None)
        return trader, match_calls

    @staticmethod
    def _marker_result(
        x: int,
        y: int,
        width: int = 52,
        height: int = 14,
    ) -> MatchResult:
        return MatchResult(
            0.99,
            (x, y),
            (width, height),
            pixel_score=0.98,
            zncc_score=0.97,
        )

    @staticmethod
    def _text_box(
        name: str,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            name=name,
            confidence=0.99,
            x=x,
            y=y,
            width=width,
            height=height,
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
        trader = create_trader()
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

    def test_run_sell_only_stops_and_logs_when_shop_entry_fails(self):
        warnings = []
        trader = create_trader()
        trader.task = SimpleNamespace(log_warning=warnings.append, log_info=Mock())
        trader._resolve_sale_entries = lambda: [
            CalendarEntry("水果罐头", "S2:苍蓝魔女")
        ]
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

    def test_sell_shop_selection_resets_page_when_landing_never_confirmed(self):
        scrolls = []
        trader = create_trader()
        trader.task = SimpleNamespace(
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        trader._sell_cartridge_page = 0
        trader._wait_for_shop_page = lambda _shop_ids: False
        trader._scroll_shop_cartridges = lambda scroll_amount, count, interval, after_sleep: (
            scrolls.append((scroll_amount, count, interval, after_sleep))
        )
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        )
        trader._shop_list_top_row_index = lambda _frame: 5

        # 两次落点确认都失败：会话页号必须作废，否则后续条目从失真基准少滚。
        self.assertFalse(trader.select_shop_tab("E7:戏水女王"))
        self.assertEqual(
            [(-1, 20, 0.0, 0.5), (-1, 25, 0.0, 0.5)],
            scrolls,
        )
        self.assertIsNone(trader._sell_cartridge_page)

    def test_run_sell_stops_before_calendar_when_sell_page_is_not_confirmed(self):
        trader = create_trader()
        trader.task = SimpleNamespace(log_warning=lambda *_args: None, log_info=Mock())
        trader._resolve_sale_entries = lambda: [
            CalendarEntry("水果罐头", "S2:苍蓝魔女")
        ]
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(True, ScreenState.SHOP)
        )
        trader._ensure_sell_page = lambda: False
        trader.sell_max_price_items = lambda: self.fail("未确认出售页面时不得加载价表或开始出售")

        self.assertFalse(trader.run_sell())

    def test_locate_sale_items_matches_120_percent_only_in_each_name_left_roi(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = [
            self._text_box("4120%", 492, 451, 56, 13),
            self._text_box("水果罐头", 613, 560, 42, 23),
            self._text_box("4129", 824, 560, 56, 13),
            self._text_box("水果罐头", 945, 560, 42, 23),
            self._text_box("120", 1156, 560, 52, 14),
            self._text_box("水果罐头", 1277, 560, 42, 23),
        ]
        markers = (
            self._marker_result(493, 563),
            self._marker_result(825, 563),
            self._marker_result(1157, 563),
        )
        trader, match_calls = self._make_sale_template_trader(
            lambda _frame, _name, target_height=900: boxes,
            markers,
        )

        candidates = trader._locate_sale_items(
            CalendarEntry("水果罐头", "S2:苍蓝魔女"),
            frame,
        )
        self.assertEqual(
            [(634, 572), (966, 572), (1298, 572)],
            [candidate.center for candidate in candidates],
        )
        self.assertEqual((493, 563), (candidates[0].percent_box.x, candidates[0].percent_box.y))
        self.assertEqual(3, len(match_calls))
        self.assertTrue(all(call[0] is frame for call in match_calls))
        self.assertTrue(
            all(call[1] is SALE_120_PERCENT_MARKER_TEMPLATE for call in match_calls)
        )
        self.assertEqual(
            [(463, 548, 150, 47), (795, 548, 150, 47), (1127, 548, 150, 47)],
            [call[2]["search_roi"] for call in match_calls],
        )
        for _frame, _spec, kwargs in match_calls:
            self.assertEqual(SALE_120_PERCENT_MARKER_TEMPLATE.threshold, kwargs["minimum_score"])
            self.assertEqual(SALE_120_PERCENT_MARKER_PEAK_RADIUS, kwargs["peak_radius"])
            self.assertEqual(SALE_120_PERCENT_MARKER_MAX_RESULTS, kwargs["max_results"])

    def test_locate_sale_items_pairs_real_markers_with_name_ocr(self):
        fixture_path = (
            ROOT / "tests" / "fixtures" / "map_trade" / "trade_shop" / "sale_120_markers_fhd.png"
        )
        fixture = load_bgr(fixture_path)
        self.assertIsNotNone(fixture)
        boxes = [
            self._text_box("4120%", 493, 563, 52, 14),
            self._text_box("水果罐头", 613, 560, 42, 23),
            self._text_box("4129", 825, 563, 52, 14),
            self._text_box("水果罐头", 945, 560, 42, 23),
            self._text_box("120", 1157, 563, 52, 14),
            self._text_box("水果罐头", 1277, 560, 42, 23),
        ]
        trader = create_trader()
        trader.vision = Vision(
            SimpleNamespace(config={}, vision_threshold_key="跑图跑商识图阈值")
        )
        trader.vision.ocr_boxes = lambda *_args, **_kwargs: boxes
        trader.task = SimpleNamespace(info_set=lambda *_args: None)

        candidates = trader._locate_sale_items(
            CalendarEntry("水果罐头", "S2:苍蓝魔女"),
            fixture,
        )

        self.assertEqual(
            [(634, 572), (966, 572), (1298, 572)],
            [candidate.center for candidate in candidates],
        )
        self.assertEqual(
            [(493, 563), (825, 563), (1157, 563)],
            [
                (candidate.percent_box.x, candidate.percent_box.y)
                for candidate in candidates
            ],
        )

    def test_locate_sale_items_ignores_bad_percent_ocr_when_local_template_misses(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        target_heights = []
        boxes = [
            self._text_box("4120%", 492, 451, 56, 13),
            self._text_box("4129", 824, 451, 56, 13),
            self._text_box("120", 1156, 451, 52, 14),
            self._text_box("水果罐头", 945, 560, 42, 23),
        ]

        def ocr_boxes(_frame, _name, target_height=900):
            target_heights.append(target_height)
            return boxes

        trader, match_calls = self._make_sale_template_trader(ocr_boxes, ())

        self.assertEqual(
            [],
            trader._locate_sale_items(
                CalendarEntry("水果罐头", "S2:苍蓝魔女"),
                frame,
            ),
        )
        self.assertEqual([900, 840, 960], target_heights)
        self.assertEqual(1, len(match_calls))
        self.assertFalse(trader._last_sale_unavailable)
        self.assertEqual(
            "商品名左侧局部ROI未命中↑120%模板",
            trader._last_sale_reason,
        )

    def test_locate_sale_items_with_no_ocr_output_is_not_marked_sold_out(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        target_heights = []
        trader, _match_calls = self._make_sale_template_trader(
            lambda _frame, _name, target_height=900: (
                target_heights.append(target_height) or []
            ),
            (self._marker_result(493, 563),),
        )

        self.assertEqual(
            [],
            trader._locate_sale_items(
                CalendarEntry("白糖", "S2:苍蓝魔女"),
                frame,
            ),
        )
        self.assertEqual([900, 840, 960], target_heights)
        self.assertFalse(trader._last_sale_unavailable)
        self.assertEqual("全画面OCR未返回任何文本", trader._last_sale_reason)

    def test_name_missed_with_other_page_text_is_marked_unavailable(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        target_heights = []
        other_item = self._text_box("胡萝卜", 613, 560, 42, 23)
        trader, match_calls = self._make_sale_template_trader(
            lambda _frame, _name, target_height=900: (
                target_heights.append(target_height) or [other_item]
            ),
            (self._marker_result(493, 563),),
        )

        self.assertEqual(
            [],
            trader._locate_sale_items(
                CalendarEntry("白糖", "S2:苍蓝魔女"),
                frame,
            ),
        )
        self.assertEqual([900, 840, 960], target_heights)
        self.assertEqual([], match_calls)
        self.assertTrue(trader._last_sale_unavailable)
        self.assertEqual("全画面OCR未识别到商品名", trader._last_sale_reason)

    def test_locate_sale_items_rejects_ambiguous_local_template_candidates(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = [
            self._text_box("兽肉", 598, 560, 44, 23),
            self._text_box("兽肉", 650, 560, 44, 23),
        ]
        trader, _match_calls = self._make_sale_template_trader(
            lambda *_args, **_kwargs: boxes,
            (
                self._marker_result(480, 562),
                self._marker_result(490, 562),
            ),
        )

        self.assertEqual([], trader._locate_sale_items(CalendarEntry("兽肉", "S3"), frame))
        self.assertFalse(trader._last_sale_unavailable)
        self.assertEqual(
            "商品名左侧局部ROI模板候选不唯一",
            trader._last_sale_reason,
        )

    def test_sale_name_match_rejects_single_char_fragment_against_alias(self):
        # 回归 BUG-20260913-06：卖出后页面上的杂散单字母框"C"曾反向命中
        # 英文别名 Chocolat Cocktail，令已售完页面被误判为商品仍在售。
        normalized_names = ("巧克力鸡尾酒", "chocolatcocktail")
        self.assertFalse(self._sale_name_match("c", normalized_names))
        self.assertTrue(self._sale_name_match("cocktail", normalized_names))
        self.assertTrue(self._sale_name_match("巧克力鸡尾酒", normalized_names))
        self.assertTrue(self._sale_name_match("鸡尾酒", normalized_names))

    def test_ambiguous_sale_name_rereads_current_box_at_each_resolution(self):
        for width, height in ((1920, 1080), (1280, 720)):
            for recovered in ("巧克", "巧克力"):
                with self.subTest(size=(width, height), recovered=recovered):
                    frame = np.zeros((height, width, 3), dtype=np.uint8)
                    scale = height / 1080
                    box = self._text_box("巧克", round(600 * scale), round(560 * scale),
                                         round(60 * scale), round(24 * scale))
                    rereads = []

                    def ocr_boxes(image, name, **kwargs):
                        if name == "出售商品名复读":
                            rereads.append((image.shape, kwargs))
                            return [self._text_box(recovered, 0, 0, 30, 12)]
                        return [box]

                    marker = self._marker_result(round(493 * scale), round(562 * scale),
                                                 round(52 * scale), round(14 * scale))
                    trader, _searches = self._make_sale_template_trader(ocr_boxes, (marker,))
                    entry = CalendarEntry("巧克力", "S4")
                    candidates = trader._locate_sale_items(entry, frame)
                    if recovered == "巧克":
                        self.assertEqual([], candidates)
                        self.assertTrue(trader._last_sale_name_seen)
                        self.assertFalse(trader._last_sale_unavailable)
                        self.assertIn("歧义", trader._last_sale_reason)
                        self.assertEqual(6, len(rereads))
                        self.assertIsNone(trader._sale_name_signature(entry, frame))
                    else:
                        self.assertEqual(1, len(candidates))
                        self.assertEqual(1, len(rereads))
                    padding = max(1, round(box.height * 0.2))
                    self.assertTrue(all(shape == (box.height + 2 * padding,
                                                 box.width + 2 * padding, 3)
                                        for shape, _kwargs in rereads))
                    self.assertTrue(all(kwargs == {"target_height": 0, "ocr_scale": 2.0}
                                        for _shape, kwargs in rereads))

    def test_sale_name_match_rejects_long_dialogue_sentence(self):
        normalized_names = ("金枪鱼罐头",)
        dialogue = "这里好像正在收购金枪鱼罐头，如果有多余的就卖掉吧。"
        self.assertFalse(self._sale_name_match(dialogue, normalized_names))
        self.assertTrue(self._sale_name_match("金枪鱼罐头", normalized_names))

    def test_ensure_cartridge_goods_loaded_handles_missing_ingredients(self):
        trader = create_trader()
        trader.task = SimpleNamespace(log_info=lambda *_args: None, sleep=lambda *_args: None)
        refreshed = []
        trader.refresh_shop_cartridge = lambda shop: refreshed.append(shop) or True

        # Case 1: 包含食物和料理食材 -> 不触发刷新
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda _frame, _name, **_kw: "食物 料理食材 鱼子酱罐头",
        )
        self.assertTrue(trader._ensure_cartridge_goods_loaded("S12"))
        self.assertEqual([], refreshed)

        # Case 2: 仅包含食物，未包含料理食材 -> 触发一次切卡带刷新
        ocr_outputs = iter(["食物 鱼子酱蛋包饭", "食物 料理食材 鱼子酱罐头"])
        trader.vision.ocr_text = lambda _frame, _name, **_kw: next(ocr_outputs)
        self.assertTrue(trader._ensure_cartridge_goods_loaded("S12"))
        self.assertEqual(["S12"], refreshed)

    def test_sell_resolved_entries_stops_when_refresh_cannot_return_to_target(self):
        selected = []
        refresh_selections = []
        statuses = []
        trader = create_trader()
        trader.task = SimpleNamespace(
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            sleep=lambda *_args: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        trader.vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            ocr_text=lambda *_args, **_kwargs: "食物 鱼子酱蛋包饭",
        )
        trader.select_shop_tab = lambda shop: selected.append(shop) or True
        trader._confirmed_shop_cartridge_detections = lambda _frame: {
            "S11": object(), "S12": object(),
        }
        trader._select_purchase_cartridge = lambda shop: (
            refresh_selections.append(shop) or shop == "S11"
        )
        trader._sell_selected_entry = lambda _entry: self.fail(
            "未返回目标卡带时不得继续出售或标记商品缺货"
        )

        self.assertFalse(trader._sell_resolved_entries([
            CalendarEntry("鱼子酱罐头", "S12"),
            CalendarEntry("豆子", "S12"),
            CalendarEntry("辣椒", "S13"),
        ]))
        self.assertEqual(["S12"], selected)
        self.assertEqual(["S11", "S12"], refresh_selections)
        self.assertIn("鱼子酱罐头：未出售：商店卡带刷新失败", dict(statuses)["未出售商品"])

    def test_missing_120_percent_is_recognition_failure_before_any_sale(self):
        trader = create_trader()
        trader.task = SimpleNamespace(
            operate_click=lambda *_args, **_kwargs: None,
            sleep=lambda *_args: None,
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )

        def fail_wait(_entry):
            trader._last_sale_unavailable = False
            trader._last_sale_reason = "↑120%模板未命中"
            return None

        trader._wait_sale_item_candidates = fail_wait

        self.assertFalse(trader._sell_selected_entry(CalendarEntry("豆子", "S12:海边天使")))
        self.assertFalse(trader._last_sale_unavailable)
        self.assertEqual(
            "↑120%模板未命中",
            trader._last_sale_reason,
        )

    def test_marker_failure_after_sale_with_name_still_visible_remains_failure(self):
        warnings = []
        clicked = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        candidate = SimpleNamespace(center=(620, 572), percent_box=None)
        scans = [([candidate], frame), None]
        trader = create_trader()
        trader.task = SimpleNamespace(
            config={},
            log_info=lambda *_args: None,
            log_warning=warnings.append,
            info_set=lambda *_args: None,
        )

        def wait_scan(_entry):
            located = scans.pop(0)
            if located is None:
                trader._last_sale_unavailable = False
                trader._last_sale_reason = "↑120%模板未命中"
                trader._last_sale_page_empty = False
            return located

        trader._wait_sale_item_candidates = wait_scan

        def sell_one(_entry, candidate, frame, **_kwargs):
            clicked.append((candidate.center, frame.shape))
            return 352_927, True

        trader._sell_one_candidate = sell_one

        self.assertFalse(
            trader._sell_selected_entry(
                CalendarEntry("白糖", "S2:苍蓝魔女"),
            )
        )
        self.assertEqual(1, len(clicked))
        self.assertFalse(trader._last_sale_unavailable)
        self.assertIn("不能判定当前页已售完", warnings[0])

    def test_sale_after_successful_sale_requires_two_name_absence_scans(self):
        clock = FakeClock()
        warnings = []
        logs = []
        statuses = []
        sleeps = clock.sleeps
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        candidate = SimpleNamespace(center=(620, 572), percent_box=None)
        locate_calls = []
        trader = create_trader()
        trader.task = SimpleNamespace(
            config={},
            sleep=clock.sleep,
            log_info=logs.append,
            log_warning=warnings.append,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        trader.vision = SimpleNamespace(capture=lambda: frame)

        def locate(_entry, _frame):
            locate_calls.append(True)
            if len(locate_calls) == 1:
                trader._last_sale_name_seen = True
                trader._last_sale_ocr_output = True
                return [candidate]
            trader._last_sale_name_seen = False
            trader._last_sale_ocr_output = True
            trader._last_sale_reason = "全画面OCR未识别到商品名"
            return []

        trader._locate_sale_items = locate
        trader._sell_one_candidate = lambda *_args, **_kwargs: (352_927, True)

        with patch(
            "src.tasks.map_trade.trader_sell.monotonic",
            clock,
        ):
            self.assertTrue(
                trader._sell_selected_entry(
                    CalendarEntry("白糖", "S2:苍蓝魔女"),
                )
            )

        self.assertEqual(1 + SALE_EMPTY_NAME_STABLE_HITS, len(locate_calls))
        self.assertEqual(SALE_EMPTY_NAME_STABLE_HITS - 1, len(sleeps))
        self.assertEqual([], warnings)
        self.assertFalse(trader._last_sale_unavailable)
        self.assertEqual("", trader._last_sale_reason)
        self.assertTrue(any("商品名连续消失" in value for key, value in statuses))
        self.assertTrue(any("当前商店页已无剩余可出售组" in message for message in logs))

    def test_normal_sale_confirms_max_or_one_insured_item(self):
        for insurance in (False, True):
            with self.subTest(insurance=insurance):
                clicks = []
                client_clicks = []
                quantities = []
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={"出售保险": insurance},
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
                candidate = SimpleNamespace(center=(640, 462), percent_box=None)
                located = ([candidate], frame)
                scans = [located, located if insurance else None]

                def pop_scan(_entry):
                    result = scans.pop(0)
                    if result is None:
                        trader._last_sale_unavailable = True
                        trader._last_sale_reason = "全画面OCR未识别到商品名"
                        trader._last_sale_page_empty = True
                    return result

                trader._wait_sale_item_candidates = pop_scan
                trader._sale_name_signature = lambda _entry, _frame: ()
                trader._wait_sale_dialog_item = lambda _entry: True
                trader._wait_owned_quantity = lambda: 400
                trader._wait_available_quantity = lambda: 400
                trader._wait_selected_sale_quantity = lambda expected, **_kwargs: (
                    quantities.append(expected) or expected
                )
                trader._wait_sale_completion = lambda *_args, **_kwargs: True

                self.assertTrue(trader._sell_selected_entry(
                    CalendarEntry("甜辣酱", "S10:霍尔蒙克斯")
                ))
                self.assertEqual([((640, 462), frame.shape, 0.5)], client_clicks)
                self.assertEqual(
                    [
                        (*(SALE_MIN_POINT if insurance else SALE_MAX_POINT), 0.5),
                        (*SALE_CONFIRM_POINT, 0.5),
                    ],
                    clicks,
                )
                self.assertEqual([1 if insurance else 400], quantities)
                self.assertEqual(1, len(scans))

    def test_sale_confirmation_checks_reserve_and_current_group_limit(self):
        cases = (
            (False, "苹果", 5500, 8400, 8400, 8400, False),
            (False, "苹果", 5500, 8400, 8400, 2901, False),
            (False, "苹果", 5500, 8400, 8400, 2900, True),
            (False, "苹果", 5500, 8400, 8400, 2850, True),
            (True, "苹果", 5500, 8400, 8400, 2850, True),
            (False, "苹果", 5500, 8400, 1200, 1201, False),
            (False, "苹果", 5500, 8400, 1200, 1200, True),
            (False, "苹果", 5500, 8400, 8400, 0, False),
            (False, "黄油", 0, 77850, 77850, 77850, True),
            (False, "黄油", 0, 8400, 400, 400, True),
            (False, "黄油", 0, 8400, 400, 399, False),
            (True, "黄油", 0, 8400, 400, 1, True),
            (True, "黄油", 0, 8400, 400, 400, False),
        )
        for insurance, item, reserve, owned, available, selected, confirmed in cases:
            with self.subTest(insurance=insurance, item=item, reserve=reserve, owned=owned,
                              available=available, selected=selected):
                clicks = []
                clock = FakeClock()
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={"出售保险": insurance},
                    operate_click=lambda x, y, after_sleep=0: clicks.append((x, y)),
                    log_info=lambda *_a: None, log_warning=lambda *_a: None,
                    info_set=lambda *_a: None,
                    sleep=clock.sleep,
                )
                trader.vision = SimpleNamespace(
                    click_client=lambda *_a, **_k: None, capture=lambda: frame,
                    ocr_text=lambda *_a, **_k: (
                        f"{item} 拥有{owned:,}个 {selected}个 可购买{available}个"
                    ),
                )
                trader._sale_name_signature = lambda *_a: ()
                trader._sale_toast_id = lambda *_a: None
                trader._wait_sale_dialog_item = lambda *_a: True
                trader._wait_owned_quantity = lambda: owned
                trader._wait_available_quantity = lambda: available
                trader._wait_sale_completion = lambda *_a, **_k: True
                with patch("src.tasks.map_trade.trader_sell.monotonic",
                           clock):
                    result = trader._sell_one_candidate(
                        CalendarEntry(item, "S2", reserve=reserve),
                        SimpleNamespace(center=(640, 462), percent_box=None),
                        frame, previous_owned=None,
                    )
                self.assertEqual((owned, True) if confirmed else None, result)
                self.assertEqual(confirmed, SALE_CONFIRM_POINT in clicks)
                self.assertEqual(selected if confirmed else 0, sum(trader._sale_sold.values()))

    def test_partial_sale_confirms_remaining_stock_without_resubmitting(self):
        for scenario in (
            "sold", "last_blank", "unchanged", "wrong_stock", "wrong_title", "open", "blank",
        ):
            with self.subTest(scenario=scenario):
                self.reset_stage_history()
                clock = FakeClock()
                clicks = []
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                candidate = SimpleNamespace(center=(620, 572), percent_box=None)
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={}, info_set=Mock(), log_info=Mock(), log_warning=Mock(),
                    sleep=clock.sleep,
                    operate_click=lambda x, y, **_kw: clicks.append((x, y)),
                )
                trader.vision = SimpleNamespace(
                    capture=lambda: frame, click_client=Mock(), simplify=lambda text: text,
                    ocr_text=lambda _frame, _name, **_kw: (
                        "苹果 拥有77,850个 可购买77850个" if scenario == "open"
                        else "" if scenario == "blank" or (
                            scenario == "last_blank" and _name == "出售交易完成" and clock.now > 1
                        ) else "出售 料理食材 苹果"
                    ),
                )
                trader._sale_name_signature = Mock(return_value=(("苹果", 600, 560, 60, 24),))
                trader._sale_toast_id = Mock(return_value=None)
                wait_candidates = trader._wait_sale_item_candidates
                trader._locate_sale_items = Mock(return_value=[candidate])
                trader._wait_sale_item_candidates = Mock(side_effect=lambda entry, **kwargs: (
                    wait_candidates(entry, **kwargs) if kwargs else ([candidate], frame)
                ))
                trader._wait_sale_dialog_item = Mock(
                    side_effect=[True, False] if scenario == "wrong_title" else None,
                    return_value=True,
                )

                def stock():
                    if SALE_CONFIRM_POINT not in clicks or scenario == "unchanged":
                        return 77850
                    return 5499 if scenario == "wrong_stock" else 5500

                trader._wait_owned_quantity = stock
                trader._wait_available_quantity = Mock(return_value=77850)
                trader._choose_sale_quantity = Mock(return_value=True)
                trader._wait_selected_sale_quantity = Mock(return_value=72350)
                trader._ensure_sell_page = Mock(return_value=True)
                with patch("src.tasks.map_trade.trader_sell.monotonic", clock):
                    result = trader._sell_selected_entry(CalendarEntry("苹果", "S2", reserve=5500))
                self.assertEqual(scenario in {"sold", "last_blank"}, result)
                self.assertEqual(1, clicks.count(SALE_CONFIRM_POINT))
                if scenario in {"open", "blank"}:
                    trader.vision.click_client.assert_called_once()
                record = self.history.stage("SimpleNamespace", "出售:S2:苹果")
                self.assertEqual("done" if result else "pending", record["state"])

    def test_last_sale_batch_finishes_only_after_transaction_confirmation(self):
        for completed in (True, False):
            with self.subTest(completed=completed):
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={"出售保险": False}, operate_click=lambda *_a, **_k: None,
                    info_set=lambda *_a: None, log_info=lambda *_a: None,
                    log_warning=lambda *_a: None,
                )
                trader.vision = SimpleNamespace(click_client=lambda *_a, **_k: None)
                quantities = iter((132301, 99999))
                available = iter((32302, 99999))
                scans = []
                selected = []

                def scan(_entry):
                    scans.append(True)
                    self.assertLessEqual(len(scans), 2, "确认最后一组售完后不得继续扫描")
                    return [SimpleNamespace(center=(630, 572), percent_box=None)], frame

                trader._wait_sale_item_candidates = scan
                trader._sale_name_signature = lambda *_a: ()
                trader._sale_toast_id = lambda *_a: None
                trader._wait_sale_dialog_item = lambda *_a: True
                trader._wait_owned_quantity = lambda: next(quantities)
                trader._wait_available_quantity = lambda: next(available)
                trader._wait_selected_sale_quantity = lambda expected, **_kwargs: (
                    selected.append(expected) or expected
                )
                trader._wait_sale_completion = lambda *_a, **_k: len(scans) == 1 or completed
                self.assertEqual(
                    completed, trader._sell_selected_entry(CalendarEntry("巧克力", "S4")),
                )
                self.assertEqual([32302, 99999], selected)
                self.assertEqual(completed, trader._last_sale_cleared)
                self.assertEqual(
                    132301 if completed else 32302,
                    sum(trader._sale_sold.values()),
                )

    def test_uncertain_sale_signature_cannot_confirm_transaction(self):
        trader = create_trader()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader.vision = SimpleNamespace(capture=lambda: frame,
                                       ocr_text=lambda *_a, **_k: "出售 食物 料理食材",
                                       simplify=lambda value: value)
        trader.task = SimpleNamespace(log_warning=lambda *_a: None)
        trader._sale_toast_id = lambda *_a: None
        trader._sale_name_signature = lambda *_a: None
        self.assertFalse(trader._wait_sale_completion(
            CalendarEntry("巧克力", "S4"), frame, (("巧克力", 600, 560, 60, 24),), timeout=0,
        ))

    def test_sale_dialog_open_stops_after_click_limit(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        client_clicks = []
        warnings = []
        trader = create_trader()
        trader.task = SimpleNamespace(
            log_info=lambda *_args: None,
            log_warning=warnings.append,
        )
        trader.vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            )
        )
        trader._sale_name_signature = lambda _entry, _frame: ()
        trader._sale_toast_id = lambda _frame: None
        trader._wait_sale_dialog_item = lambda _entry: False
        trader.vision.capture = Mock(return_value=frame)
        trader._locate_sale_items = Mock(return_value=[
            SimpleNamespace(center=(951, 682), percent_box=None),
        ])

        result = trader._sell_one_candidate(
            CalendarEntry("item", "S3"),
            SimpleNamespace(center=(951, 682), percent_box=None),
            frame,
            previous_owned=None,
        )

        self.assertIsNone(result)
        self.assertEqual(SALE_DIALOG_OPEN_MAX_CLICKS, len(client_clicks))
        self.assertEqual(1, len(warnings))

    def test_sale_dialog_wrong_item_stops_without_reclick_or_quantity_change(self):
        trader, _ocr_calls, _warnings, clock = self._title_trader([("豆子", "豆子", "豆子")])
        clicks = []
        trader.vision.click_client = lambda *args, **kwargs: clicks.append(args)
        trader._sale_name_signature = lambda *_args: ()
        trader._sale_toast_id = lambda *_args: None
        trader._wait_owned_quantity = lambda: self.fail("错误商品不得进入数量设置")
        with patch("src.tasks.map_trade.trader_sell.monotonic", lambda: clock[0]):
            result = trader._sell_one_candidate(
                CalendarEntry("姜黄", "S12"), SimpleNamespace(center=(620, 579), percent_box=None),
                np.zeros((1080, 1920, 3), dtype=np.uint8), previous_owned=None,
            )
        self.assertIsNone(result)
        self.assertEqual(1, len(clicks))

    def test_sell_selected_entry_rescans_after_each_completed_sale(self):
        for insurance, reserve in ((False, 0), (True, 5500)):
            with self.subTest(insurance=insurance, reserve=reserve):
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                first = SimpleNamespace(center=(620, 572), percent_box=None)
                second = SimpleNamespace(center=(620, 572), percent_box=None)
                scans = [([first], frame), ([second], frame), None]
                clicked = []
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={"出售保险": insurance},
                    log_info=lambda *_args: None,
                )

                def pop_scan(_entry):
                    located = scans.pop(0)
                    if located is None:
                        trader._last_sale_unavailable = True
                        trader._last_sale_reason = "全画面OCR未识别到商品名"
                        trader._last_sale_page_empty = True
                    return located

                trader._wait_sale_item_candidates = pop_scan

                def sell_one(_entry, candidate, _frame, **_kwargs):
                    clicked.append(candidate.center)
                    return 240524 - len(clicked), True

                trader._sell_one_candidate = sell_one

                self.assertTrue(trader._sell_selected_entry(
                    CalendarEntry("兽肉", "S3", reserve=reserve)
                ))
                self.assertEqual([(620, 572), (620, 572)], clicked)
                self.assertEqual([], scans)

    def _title_trader(self, readings, shape=(1080, 1920, 3)):
        calls, warnings = [], []
        clock = [0.0]
        position = [-1, 0]
        frame = np.full(shape, (20, 70, 150), dtype=np.uint8)

        def capture():
            position[0] += 1
            position[1] = 0
            return frame.copy()

        def ocr_boxes(target, name, **kwargs):
            frame_readings = readings[min(position[0], len(readings) - 1)]
            text = frame_readings[position[1]]
            position[1] += 1
            calls.append((position[0], name, target.copy(), kwargs))
            texts = text if isinstance(text, tuple) else (text,)
            return [SimpleNamespace(name=value, confidence=0.75) for value in texts if value]

        trader = create_trader()
        trader.task = SimpleNamespace(
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            info_set=lambda *_args: None,
            log_warning=warnings.append,
        )
        trader.vision = SimpleNamespace(
            capture=capture,
            ocr_boxes=ocr_boxes,
            simplify=lambda value: value,
        )
        return trader, calls, warnings, clock

    def test_sale_dialog_accepts_all_turmeric_spellings_in_two_captures(self):
        for spelling in ("姜黄", "姜黃", "薑黄", "薑黃"):
            with self.subTest(spelling=spelling):
                trader, calls, warnings, clock = self._title_trader(
                    [(spelling, spelling, spelling), (spelling, spelling, spelling)]
                )
                with patch("src.tasks.map_trade.trader_sell.monotonic", lambda: clock[0]):
                    self.assertTrue(trader._wait_sale_dialog_item(CalendarEntry("姜黄", "S12")))
                self.assertEqual([0, 1], [call[0] for call in calls])
                self.assertTrue(all(call[1].endswith("/原图") for call in calls))
                self.assertEqual([], warnings)

    def test_sale_dialog_rejects_fragments_and_single_frame_confirmation(self):
        for text in ("", "姜", "黄", "姜黄油", "白糖"):
            with self.subTest(text=text):
                trader, calls, _warnings, clock = self._title_trader([(text, text, text)])
                with patch("src.tasks.map_trade.trader_sell.monotonic", lambda: clock[0]):
                    self.assertFalse(trader._wait_sale_dialog_item(
                        CalendarEntry("白糖" if text == "白糖" else "姜黄", "S12"), timeout=0,
                    ))
                self.assertLessEqual(len(calls), 3)

    def test_sale_dialog_rejects_wrong_or_ambiguous_identity_before_fallback(self):
        for texts, aliases in (
            ("豆子", ()),
        ):
            with self.subTest(texts=texts, aliases=aliases):
                trader, calls, warnings, clock = self._title_trader([(texts, texts, texts)])
                with patch("src.tasks.map_trade.trader_sell.monotonic", lambda: clock[0]):
                    self.assertFalse(trader._wait_sale_dialog_item(
                        CalendarEntry("姜黄", "S12", aliases=aliases),
                    ))
                self.assertEqual(2, len(calls))
                self.assertTrue(trader._sale_dialog_rejected)
                self.assertTrue(warnings)

    def test_sale_completion_ignores_previous_transaction_toast(self):
        frames = [
            np.zeros((1080, 1920, 3), dtype=np.uint8),
            np.zeros((1080, 1920, 3), dtype=np.uint8),
        ]
        toast_texts = iter(["交易差价5 完成!", "交易差价6 完成!"])
        trader = create_trader()
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

    def test_sale_completion_rejects_blank_page_ocr_as_name_disappearance(self):
        clock = FakeClock()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader = create_trader()
        trader.task = SimpleNamespace(
            sleep=clock.sleep,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **_kwargs: "" if name == "出售弹窗完成确认" else "",
            simplify=lambda value: value,
        )
        trader._sale_name_signature = lambda *_args: ()

        with patch(
            "src.tasks.map_trade.trader_sell.monotonic",
            clock,
        ):
            self.assertFalse(
                trader._wait_sale_completion(
                    CalendarEntry("苹果", "S2"),
                    frame,
                    (("苹果", 620, 560, 44, 23),),
                    timeout=1.0,
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
        trader = create_trader()
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
        trader._ensure_cartridge_goods_loaded = Mock(return_value=True)

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(["S10:霍尔蒙克斯"], selected)
        self.assertEqual(["甜辣酱", "藏红花"], sold)
        self.assertIn("卖：魅惑粉末标记为不出售，跳过。", logs)
        self.assertEqual("未出售：价表标记为不出售", trader._sale_not_sold[entries[0]])

    def test_enabled_sale_blacklist_excludes_matching_allowed_entry(self):
        sold = []
        logs = []
        statuses = []
        entries = (
            CalendarEntry("番茄", "S1:血骑士"),
            CalendarEntry("大麦", "S18:救赎"),
        )
        trader = create_trader()
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
        trader._ensure_cartridge_goods_loaded = Mock(return_value=True)

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(["番茄"], sold)
        self.assertIn(("出售黑名单", "开启"), statuses)
        self.assertIn("卖：大麦命中出售黑名单，跳过。", logs)
        self.assertIn("大麦：未出售：命中出售黑名单", dict(statuses)["未出售商品"])

    def test_missing_sale_item_is_reported_and_does_not_stop_next_item(self):
        statuses = []
        warnings = []
        attempted = []
        entries = (
            CalendarEntry("豆子", "S12:海边天使"),
            CalendarEntry("小麦", "S12:海边天使"),
        )
        trader = create_trader()
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
                "未发现商品名，可能无货或已经售出"
                if trader._last_sale_unavailable
                else ""
            )
            return not trader._last_sale_unavailable

        trader._sell_selected_entry = sell
        trader._ensure_cartridge_goods_loaded = Mock(return_value=True)

        self.assertTrue(trader.sell_max_price_items())
        self.assertEqual(["豆子", "小麦"], attempted)
        self.assertIn(
            "剧情游戏卡12 黑羽毛·豆子：未出售：未发现商品名，可能无货或已经售出",
            dict(statuses)["未出售商品"],
        )
        self.assertEqual([], warnings)

    def test_sale_execution_failure_stops_following_calendar_entry(self):
        attempted = []
        entries = (
            CalendarEntry("豆子", "S12:海边天使"),
            CalendarEntry("小麦", "S12:海边天使"),
        )
        trader = create_trader()
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
        trader._ensure_cartridge_goods_loaded = Mock(return_value=True)

        self.assertFalse(trader.sell_max_price_items())
        self.assertEqual(["豆子"], attempted)
        self.assertEqual("出售完成确认超时", trader._sale_not_sold[entries[0]])
        self.assertEqual("未出售：前序商品出售失败，未执行", trader._sale_not_sold[entries[1]])


    def test_sale_dialog_empty_title_resets_consecutive_confirmation(self):
        trader, calls, _warnings, clock = self._title_trader([
            ("白糖", "白糖", "白糖"),
            ("", "", ""),
            ("白糖", "白糖", "白糖"),
            ("白糖", "白糖", "白糖"),
        ])
        with patch("src.tasks.map_trade.trader_sell.monotonic", lambda: clock[0]):
            self.assertTrue(trader._wait_sale_dialog_item(CalendarEntry("白糖", "S2")))
        self.assertEqual([0, 1, 1, 1, 2, 3], [c[0] for c in calls])

    def test_sale_dialog_separates_owned_and_available_quantities(self):
        calls = []
        trader = create_trader()
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


class TradeAssetsTest(unittest.TestCase):

    def test_sale_120_marker_template_matches_all_real_fixture_markers_at_fhd(self):
        fixture_path = (
            ROOT / "tests" / "fixtures" / "map_trade" / "trade_shop" / "sale_120_markers_fhd.png"
        )
        frame = load_bgr(fixture_path)
        self.assertIsNotNone(frame)
        task = SimpleNamespace(config={}, vision_threshold_key="跑图跑商识图阈值")
        vision = Vision(task)
        threshold = vision.threshold_for(SALE_120_PERCENT_MARKER_TEMPLATE)

        results = vision.match_all(
            frame,
            SALE_120_PERCENT_MARKER_TEMPLATE,
            minimum_score=threshold,
            peak_radius=SALE_120_PERCENT_MARKER_PEAK_RADIUS,
            max_results=SALE_120_PERCENT_MARKER_MAX_RESULTS,
        )

        self.assertEqual(
            [(493, 563), (825, 563), (1157, 563)],
            sorted(result.position for result in results),
        )
        for result in results:
            self.assertGreaterEqual(result.score, threshold)
            self.assertGreaterEqual(
                result.pixel_score,
                SALE_120_PERCENT_MARKER_TEMPLATE.min_pixel_score,
            )

    def test_sale_120_marker_template_matches_real_markers_after_resolution_scaling(self):
        fixture_path = (
            ROOT / "tests" / "fixtures" / "map_trade" / "trade_shop" / "sale_120_markers_fhd.png"
        )
        fixture = load_bgr(fixture_path)
        self.assertIsNotNone(fixture)
        task = SimpleNamespace(config={}, vision_threshold_key="跑图跑商识图阈值")
        vision = Vision(task)
        threshold = vision.threshold_for(SALE_120_PERCENT_MARKER_TEMPLATE)

        for height, width in ((720, 1280), (1440, 2560), (2160, 3840)):
            with self.subTest(height=height):
                interpolation = cv2.INTER_AREA if height < 1080 else cv2.INTER_CUBIC
                frame = cv2.resize(fixture, (width, height), interpolation=interpolation)
                results = vision.match_all(
                    frame,
                    SALE_120_PERCENT_MARKER_TEMPLATE,
                    minimum_score=threshold,
                    peak_radius=SALE_120_PERCENT_MARKER_PEAK_RADIUS,
                    max_results=SALE_120_PERCENT_MARKER_MAX_RESULTS,
                )
                self.assertEqual(3, len(results))
                centers = sorted(result.center for result in results)
                expected_centers = [
                    (
                        round((x + 26) * width / 1920),
                        round((563 + 7) * height / 1080),
                    )
                    for x in (493, 825, 1157)
                ]
                for center, expected in zip(centers, expected_centers, strict=True):
                    self.assertLessEqual(abs(center[0] - expected[0]), 2)
                    self.assertLessEqual(abs(center[1] - expected[1]), 2)
                for result in results:
                    self.assertGreaterEqual(result.score, threshold)
                    self.assertGreaterEqual(
                        result.pixel_score,
                        SALE_120_PERCENT_MARKER_TEMPLATE.min_pixel_score,
                    )

    def test_beta_marker_matches_inside_name_driven_local_roi_with_full_frame_position(self):
        fixture_path = (
            ROOT / "tests" / "fixtures" / "map_trade" / "trade_shop" / "sale_120_markers_fhd.png"
        )
        frame = load_bgr(fixture_path)
        self.assertIsNotNone(frame)
        task = SimpleNamespace(config={}, vision_threshold_key="跑图跑商识图阈值")
        vision = Vision(task)
        results = vision.match_all(
            frame,
            SALE_120_PERCENT_MARKER_BETA_TEMPLATE,
            minimum_score=SALE_120_PERCENT_MARKER_BETA_TEMPLATE.threshold,
            peak_radius=SALE_120_PERCENT_MARKER_PEAK_RADIUS,
            max_results=SALE_120_PERCENT_MARKER_MAX_RESULTS,
            search_roi=(463, 548, 150, 47),
        )

        self.assertEqual([(493, 563)], [result.position for result in results])
        self.assertGreaterEqual(results[0].score, 0.99)
        self.assertGreaterEqual(results[0].pixel_score, 0.97)

    def test_sold_out_template_separates_recorded_buy_and_sell_frames(self):
        fixture_root = ROOT / "tests" / "fixtures" / "map_trade" / "trade_shop"
        task = SimpleNamespace(
            config={"跑商识图阈值": 0.72},
            vision_threshold_key="跑商识图阈值",
        )
        vision = Vision(task)
        results = {}
        for name in ("before_purchase.png", "after_purchase.png", "sell_page.png"):
            frame = load_bgr(fixture_root / name)
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

        def click_template(timeout, after_sleep):
            template_clicks.append((timeout, after_sleep))
            return True

        vision.click_client = lambda point, frame_shape, after_sleep=0: client_clicks.append(
            (point, frame_shape, after_sleep)
        )
        navigator = Navigator(task, vision)
        navigator._click_quick_switch = click_template
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._select_cartridge_category = lambda _category: True
        navigator._wait_for_story_badge_with_scroll = lambda _number: (badge_frame, badge_detection)
        navigator.vision.capture = lambda: badge_frame
        navigator._cartridge_category_confirmed = lambda *_args: True
        navigator._find_story_badge = lambda *_args: (badge_detection, "")
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
            [(45.0, 1.0)],
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
            [PROBE_STORY_BADGE_CONFIRM_SECONDS, Q_SP6_BARGAIN_RECHECK_DELAY,
             Q_SP6_BARGAIN_CLICK_DELAY],
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
                "购买 仓库管理石怪 出售 一键购买全部收藏",
                "购买 仓库管理石怪 出售 一键购买全部收藏",
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
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)

        with patch(
            "src.tasks.map_trade.navigator_trade.monotonic", side_effect=lambda: sum(sleeps),
        ):
            self.assertTrue(navigator._wait_for_bargain_shop_confirmation(timeout=5.0))
        self.assertEqual(("砍价后商店页面 OCR稳定", "2/2"), statuses[-1])
        self.assertEqual(3, len(sleeps))

    def test_bargain_shop_confirmation_rejects_sold_out_while_bargain_popup_visible(self):
        texts = iter(
            (
                "购买全部收藏 砍价成功率100% 取消",
                "购买全部收藏 砍价成功率100% 取消",
            )
        )
        warnings = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=warnings.append,
            log_info=lambda *_args: None,
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        sold_out = MatchResult(
            0.90,
            (500, 250),
            (100, 50),
            pixel_score=0.95,
            zncc_score=0.90,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, _name: next(texts),
            simplify=lambda value: value,
            match=lambda *_args: sold_out,
            passes=lambda result, _spec: result is sold_out,
        )
        navigator = Navigator(task, vision)

        self.assertFalse(navigator._wait_for_bargain_shop_confirmation(timeout=0.0))
        self.assertTrue(warnings)

    def test_merchant_interaction_miss_fails_without_navigation_fallback(self):
        clock = FakeClock()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed_match = MatchResult(-1.0, (0, 0), (0, 0))
        clicks = []
        fallback_calls = []
        warnings = []

        def passes(result, spec):
            return (
                result.score >= spec.threshold
                and result.pixel_score >= spec.min_pixel_score
                and result.zncc_score >= spec.min_zncc_score
            )

        task = SimpleNamespace(
            config={},
            sleep=clock.sleep,
            log_warning=warnings.append,
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

        with patch("src.tasks.map_trade.navigator_trade.monotonic", clock):
            clicked = navigator._click_merchant_interaction(timeout=2.0, after_sleep=1.2)

        self.assertFalse(clicked)
        self.assertEqual([], clicks)
        self.assertEqual([], fallback_calls)
        self.assertEqual(1, len(warnings))
        warnings.clear()
        with patch("src.tasks.map_trade.navigator_trade.monotonic", clock):
            self.assertFalse(navigator._enter_q_sp6_shop(0.0, log_timeout=False))
        self.assertEqual([], warnings)


class PurchaseEvidenceTest(StageHistoryTestCase):
    def test_pending_purchase_only_rechecks_result_and_never_confirms_again(self):
        from unittest.mock import Mock

        from src.tasks import run_history

        trader = create_trader()
        trader.task = SimpleNamespace(config={}, log_warning=Mock())
        trader._wait_for_buy_all_favorites_state = Mock(return_value=((10, 10), None))
        trader._wait_for_purchase_completion = Mock(return_value=False)
        trader.vision = SimpleNamespace(click_client=Mock())
        run_history.save_stage(trader.task, "购买全部收藏", "pending")
        self.assertFalse(trader.buy_all_favorites())
        trader.vision.click_client.assert_not_called()
        trader._wait_for_purchase_completion.return_value = True
        self.assertFalse(trader.buy_all_favorites())
        trader.vision.click_client.assert_not_called()
        trader._wait_for_buy_all_favorites_state.return_value = (None, None)
        self.assertTrue(trader.buy_all_favorites())
        trader.vision.click_client.assert_not_called()

class TradeRegionTest(unittest.TestCase):

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

class MerchantNavigationTest(unittest.TestCase):
    def test_buy_entry_final_new_template_miss_fails_without_fallback(self):
        clicks = []
        client_clicks = []
        shop_entry_attempts = []
        navigation_attempts = []
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
            click_client=lambda point, frame_shape, after_sleep=0: client_clicks.append(
                (point, frame_shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._click_quick_switch = lambda **_kwargs: True
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._select_cartridge_category = lambda _category: True
        navigator._wait_for_story_badge_with_scroll = lambda _number: (badge_frame, badge_detection)
        navigator.vision.capture = lambda: badge_frame
        navigator._cartridge_category_confirmed = lambda *_args: True
        navigator._find_story_badge = lambda *_args: (badge_detection, "")
        shop_entry_results = iter((False, False))
        navigator._enter_q_sp6_shop = lambda timeout, *, log_timeout: (
            shop_entry_attempts.append((timeout, log_timeout)) or next(shop_entry_results)
        )
        navigator._auto_navigate_to_merchant_shop = lambda: (
            navigation_attempts.append(True) or False
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
        self.assertEqual([], clicks)
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
        self.assertEqual([True], navigation_attempts)

    def test_buy_entry_auto_navigates_to_merchant_when_landmark_stays_invisible(self):
        client_clicks = []
        shop_entry_attempts = []
        navigation_attempts = []
        keyword_checks = []
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            operate_click=lambda *args, **kwargs: None,
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
            click_client=lambda point, frame_shape, after_sleep=0: client_clicks.append(
                (point, frame_shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator._click_quick_switch = lambda **_kwargs: True
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._select_cartridge_category = lambda _category: True
        navigator._wait_for_story_badge_with_scroll = lambda _number: (badge_frame, badge_detection)
        navigator.vision.capture = lambda: badge_frame
        navigator._cartridge_category_confirmed = lambda *_args: True
        navigator._find_story_badge = lambda *_args: (badge_detection, "")
        shop_entry_results = iter((False, False))
        navigator._enter_q_sp6_shop = lambda timeout, *, log_timeout: (
            shop_entry_attempts.append((timeout, log_timeout)) or next(shop_entry_results)
        )
        navigator._auto_navigate_to_merchant_shop = lambda: (
            navigation_attempts.append(True) or True
        )
        navigator._wait_for_ocr_keywords = lambda keywords, timeout, name: (
            keyword_checks.append((keywords, timeout, name)) or True
        )
        navigator._wait_for_bargain_shop_confirmation = lambda: True

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
        self.assertEqual([True], navigation_attempts)
        self.assertEqual(
            [
                (Q_SP6_SHOP_PRIORITY_TIMEOUT, False),
                (45.0, True),
            ],
            shop_entry_attempts,
        )

    def test_auto_navigate_fails_without_ocr_when_guide_button_missing(self):
        ocr_calls = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            click_template=lambda *_args, **_kwargs: False,
            click_ocr=lambda *args, **kwargs: ocr_calls.append(args) or False,
        )
        navigator = Navigator(task, vision)

        self.assertFalse(navigator._auto_navigate_to_merchant_shop())
        self.assertEqual([], ocr_calls)

    def test_wait_auto_navigation_fails_when_movement_never_starts(self):
        clock = FakeClock()
        missed = MatchResult(-1.0, (0, 0), (0, 0))
        task = SimpleNamespace(
            config={},
            sleep=clock.sleep,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: missed,
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)

        with patch(
            "src.tasks.map_trade.navigator_trade.monotonic",
            clock,
        ):
            self.assertFalse(navigator._wait_merchant_auto_navigation())

class PurchaseFlowTest(StageHistoryTestCase):
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
        trader = create_trader()
        trader.task = task
        trader.progress = progress
        trader.now_provider = lambda: datetime(2026, 7, 19, 7, 59, tzinfo=UTC_PLUS_8)
        trader.navigator = SimpleNamespace(
            enter_q_sp6_buy_flow=lambda: NavigationResult(True, ScreenState.SHOP)
        )
        trader.rebuild_favorites = lambda: actions.append(("rebuild",)) or True
        trader.buy_all_favorites = lambda: actions.append(("buy-all",)) or True
        trader._wait_for_buy_all_favorites_state = lambda: (
            (1454, 1004), np.zeros((1080, 1920, 3), dtype=np.uint8)
        )

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

    def test_buy_phase_skips_sold_out_before_rebuilding_favorites(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        sold_out = MatchResult(0.99, (560, 230), (30, 18), pixel_score=0.99, zncc_score=0.99)
        for button_visible in (True, False):
            with self.subTest(button_visible=button_visible):
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={"收藏重建周期": "每次"},
                    sleep=lambda *_args: None,
                    log_info=lambda *_args: None,
                    log_warning=lambda message: self.fail(message),
                )
                trader.now_provider = lambda: datetime(2026, 9, 10, 20, tzinfo=UTC_PLUS_8)
                trader.navigator = SimpleNamespace(
                    enter_q_sp6_buy_flow=lambda: NavigationResult(True, ScreenState.SHOP)
                )
                trader.rebuild_favorites = lambda: self.fail("售罄后不应切卡重建收藏")
                trader.buy_all_favorites = lambda: self.fail("售罄后不应继续购买")
                trader.vision = SimpleNamespace(
                    capture=lambda: frame,
                    ocr_boxes=lambda *_args: [
                        SimpleNamespace(
                            name="一键购买全部收藏", x=1377, y=990, width=152, height=28
                        )
                    ] if button_visible else [],
                    simplify=lambda value: value,
                    match=lambda *_args: sold_out,
                    passes=lambda *_args: True,
                )

                self.assertTrue(trader.run_buy())
                self.assertTrue(trader._buy_completed_in_current_shop)

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
        )

        self.assertFalse(task._run_phases(navigator, phases))
        self.assertEqual(["buy", "home"], actions)

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
        trader = create_trader()
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
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )

        with patch("src.tasks.map_trade.trader_buy.monotonic", side_effect=lambda: sum(sleeps)):
            located = trader._wait_for_buy_all_favorites_state()

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

    def test_buy_all_favorites_prioritizes_same_frame_sold_out_over_visible_button(self):
        for sold_out_frames, should_buy in (
            ((True, True), False),
            ((False, True, True), False),
            ((True, False, True, True), False),
            ((False, True, False, False), True),
        ):
            with self.subTest(sold_out_frames=sold_out_frames):
                frames = [
                    np.full((2, 2, 3), index, dtype=np.uint8)
                    for index in range(len(sold_out_frames))
                ]
                captures = iter(frames)
                ocr_frames = []
                template_frames = []
                clicks = []
                trader = create_trader()
                trader.task = SimpleNamespace(
                    sleep=lambda *_args: None,
                    operate_click=lambda *_args, **_kwargs: clicks.append("confirm"),
                    log_info=lambda *_args: None,
                    log_warning=lambda message: self.fail(message),
                )

                def match(frame, spec):
                    self.assertIs(spec, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
                    template_frames.append(frame)
                    matched = sold_out_frames[int(frame[0, 0, 0])]
                    return MatchResult(float(matched), (0, 0), (1, 1))

                trader.vision = SimpleNamespace(
                    capture=lambda: next(captures),
                    ocr_boxes=lambda frame, _name: ocr_frames.append(frame) or [
                        SimpleNamespace(name="一键购买全部收藏", x=0, y=0, width=2, height=2)
                    ],
                    simplify=lambda value: value,
                    match=match,
                    passes=lambda result, _spec: result.score == 1.0,
                    click_client=lambda *_args, **_kwargs: clicks.append("buy"),
                )
                trader._wait_for_purchase_confirmation = lambda: True
                trader._wait_for_purchase_completion = lambda: True

                self.assertTrue(trader.buy_all_favorites())
                self.assertEqual(["buy", "confirm"] if should_buy else [], clicks)
                self.assertEqual(len(sold_out_frames), len(ocr_frames))
                self.assertEqual(len(ocr_frames), len(template_frames))
                for ocr_frame, template_frame in zip(ocr_frames, template_frames):
                    self.assertIs(ocr_frame, template_frame)

    def test_buy_all_favorites_stops_when_confirmation_is_missing(self):
        clicks = []
        warnings = []
        trader = create_trader()
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
        trader._wait_for_buy_all_favorites_state = lambda: ((969, 669), frame)
        trader._wait_for_purchase_confirmation = lambda: False

        self.assertFalse(trader.buy_all_favorites())
        self.assertEqual([((969, 669), frame.shape, 0.3)], clicks)
        self.assertEqual(
            ["买：点击一键购买全部收藏后，未同时识别到确认标题和询问文字。"],
            warnings,
        )

    def test_buy_all_favorites_requires_post_confirm_transaction_evidence(self):
        clicks = []
        warnings = []
        trader = create_trader()
        trader.task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_info=lambda *_args: None,
            log_warning=warnings.append,
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        trader.vision = SimpleNamespace(
            click_client=lambda point, shape, after_sleep=0: clicks.append(
                (point, shape, after_sleep)
            )
        )
        trader._wait_for_buy_all_favorites_state = lambda: ((969, 669), frame)
        trader._wait_for_purchase_confirmation = lambda: True
        trader._wait_for_purchase_completion = lambda: False

        self.assertFalse(trader.buy_all_favorites())
        self.assertEqual(2, len(clicks))
        self.assertIn("未观察到售罄模板", warnings[0])


    def test_purchase_confirmation_requires_both_texts_in_given_region(self):
        ocr_calls = []
        warnings = []
        text = {"value": "一键购买全部收藏"}
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        trader = create_trader()
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

class TradeHomeConfirmationTest(unittest.TestCase):

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
                return False, 0, 0.0, ""
            return True, 3, 253.0, "抽抽乐"

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
                brightness=0.0,
            )
        )
        text["value"] = "更新 抢先看"
        self.assertTrue(
            navigator._clear_return_home_announcement_if_needed(
                frame,
                brightness=0.0,
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
        frame = load_bgr(ROOT / "tests/fixtures/map_trade/home_return/update_notice.png")
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
                brightness=0.0,
            )
        )
        self.assertEqual([(720, 1200, 3)], ocr_shapes)

class TradeScreenClassificationTest(unittest.TestCase):
    def test_screen_classification_only_reports_home_after_all_three_signals(self):
        task = SimpleNamespace(
            config={},
            info_set=lambda *_args, **_kwargs: None,
        )
        result = MatchResult(-1.0, (0, 0), (0, 0))
        gacha_text = {"value": ""}
        left_text = {"value": "我的小屋 格鲁TALK 街机游戏"}

        def ocr_text(_frame, name, **_kwargs):
            if name == "主页左列":
                return left_text["value"]
            return gacha_text["value"]

        vision = SimpleNamespace(
            capture=lambda: np.full((1080, 1920, 3), 255, dtype=np.uint8),
            match=lambda *_args: result,
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            ocr_text=ocr_text,
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        self.assertNotEqual(ScreenState.HOME, navigator.classify())
        gacha_text["value"] = "抽抽乐"
        self.assertEqual(ScreenState.HOME, navigator.classify())
        left_text["value"] = "设置 公告"
        self.assertNotEqual(ScreenState.HOME, navigator.classify())

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

    def test_trade_classify_requires_interaction_options_and_talent_cards(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        cases = (
            ("对话 商店", "天赋技能 砍价 选择", ScreenState.MERCHANT_DIALOG),
            ("对话", "天赋技能 选择", ScreenState.SANDBOX),
            ("对话 商店", "选择", ScreenState.SANDBOX),
            ("", "", ScreenState.SANDBOX),
        )
        for options, talents, expected in cases:
            with self.subTest(options=options, talents=talents):
                seen_frames = []

                def ocr(frame_arg, name, **_kwargs):
                    seen_frames.append(frame_arg)
                    return {
                        "跑商商人交互选项": options,
                        "跑商商人天赋技能": talents,
                    }.get(name, "")

                task = SimpleNamespace(config={}, info_set=lambda *_args: None)
                vision = SimpleNamespace(capture=self._fresh_frame, ocr_text=ocr,
                                         simplify=lambda value: value)
                navigator = Navigator(task, vision)
                navigator._home_confirmation_signals = lambda _frame: (False,)
                navigator.classify = lambda _frame: ScreenState.SANDBOX
                self.assertEqual(expected, navigator.classify_trade(frame))
                self.assertTrue(all(value is frame for value in seen_frames))

    @staticmethod
    def _fresh_frame():
        return np.zeros((1080, 1920, 3), dtype=np.uint8)

    def test_shared_classify_never_uses_trade_merchant_signals(self):
        task = SimpleNamespace(config={}, info_set=lambda *_args: None)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        failed = MatchResult(-1.0, (0, 0), (0, 0))
        ocr_names = []
        matched_specs = []

        def ocr(_frame, name, **_kwargs):
            ocr_names.append(name)
            return ""

        def match(_frame, spec):
            matched_specs.append(spec)
            return failed

        vision = SimpleNamespace(
            capture=lambda: frame,
            match=match,
            passes=lambda *_args: False,
            threshold_for=lambda spec: spec.threshold,
            template_brightness_ratio=lambda *_args: 0.0,
            ocr_text=ocr,
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)

        navigator.classify(frame)

        self.assertNotIn("跑商商人交互选项", ocr_names)
        self.assertNotIn("跑商商人天赋技能", ocr_names)
        self.assertNotIn(MERCHANT_CLICK_LOCATION_TEMPLATE, matched_specs)

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

class TradeHomeRecoveryTest(unittest.TestCase):
    def test_return_home_from_shop_closes_discount_shop_then_uses_home_button(self):
        actions = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: actions.append(("click", x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            ocr_text=lambda *_args, **_kwargs: "商店商品列表",
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
        navigator.wait_for_q_sp6_sandbox = lambda timeout: (
            actions.append(("sandbox", timeout)) or True
        )

        result = navigator.return_home()

        self.assertTrue(result.success)
        self.assertEqual(ScreenState.HOME, result.state)
        self.assertEqual(
            [
                ("click", *DISCOUNT_SHOP_BACK_POINT, 0.0),
                (
                    "ocr",
                    DISCOUNT_SHOP_CLOSE_KEYWORDS,
                    DISCOUNT_SHOP_CLOSE_TIMEOUT,
                    "折扣商店关闭确认",
                    0.25,
                    DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
                ),
                ("click", *DISCOUNT_SHOP_CLOSE_POINT, 0.8),
                ("click", *DISCOUNT_SHOP_BACK_POINT, 0.8),
                ("sandbox", navigator._loading_timeout()),
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
        close_actions = actions[:5]
        actions.clear()
        result = navigator.leave_discount_shop()
        self.assertTrue(result.success)
        self.assertEqual(ScreenState.SANDBOX, result.state)
        self.assertEqual(close_actions, actions)

        actions.clear()
        navigator.wait_for_q_sp6_sandbox = lambda _timeout: False
        navigator.classify_trade = lambda: ScreenState.UNKNOWN
        result = navigator.return_home()
        self.assertFalse(result.success)
        self.assertEqual(ScreenState.UNKNOWN, result.state)
        self.assertEqual(close_actions[:4], actions)

    def test_return_home_from_shop_stops_when_close_dialog_is_not_confirmed(self):
        actions = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: actions.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            ocr_text=lambda *_args, **_kwargs: "商店商品列表",
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
        self.assertEqual([(*DISCOUNT_SHOP_BACK_POINT, 0.0)], actions)

    def test_return_home_closes_confirmed_quick_switch_before_home(self):
        for confirmed, matched, next_state in (
            (True, True, ScreenState.SANDBOX),
            (True, True, ScreenState.HOME),
            (True, True, ScreenState.CARD_MENU),
            (True, False, ScreenState.CARD_MENU),
            (False, True, ScreenState.CARD_MENU),
        ):
            with self.subTest(confirmed=confirmed, matched=matched, next_state=next_state):
                actions = []
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                back = MatchResult(0.99, (150, 30), (40, 40), pixel_score=0.95)
                navigator = Navigator(
                    SimpleNamespace(config={}, info_set=lambda *_: None),
                    SimpleNamespace(
                        capture=lambda: frame,
                        match=lambda _frame, spec: (
                            self.assertIs(spec, AREA_MAP_BACK_TEMPLATE) or back
                        ),
                        passes=lambda *_: matched,
                        click_client=lambda *_a, **_k: actions.append("back"),
                    ),
                )
                navigator.classify = lambda: ScreenState.CARD_MENU
                navigator._wait_for_quick_switch_page = lambda: confirmed
                navigator.wait_state = lambda *_: actions.append("wait_sandbox") or next_state
                navigator._click_chapter_home_button = lambda: actions.append("home")
                navigator._wait_for_cartridge_home = lambda **_: (
                    actions.append("confirm_home") or True
                )
                result = navigator.return_home()
                self.assertEqual(next_state in {ScreenState.HOME, ScreenState.SANDBOX},
                                 result.success)
                expected = ["back", "wait_sandbox"] if confirmed and matched else []
                if next_state == ScreenState.SANDBOX:
                    expected += ["home", "confirm_home"]
                self.assertEqual(expected, actions)
                if result.success:
                    self.assertEqual(ScreenState.HOME, result.state)

    def test_return_home_from_sandbox_clicks_home_once(self):
        actions = []
        matched_specs = []
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: actions.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda _frame, spec: matched_specs.append(spec)
            or MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
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
        self.assertGreaterEqual(len(matched_specs), len(CHAPTER_HOME_TEMPLATES))

    def test_return_home_recognizes_white_house_in_video(self):
        crop = load_bgr(
            ROOT / "tests/fixtures/map_trade/home_return/white_house_video_810.png"
        )
        self.assertIsNotNone(crop)
        source = np.zeros((810, 1440, 3), dtype=np.uint8)
        source[:crop.shape[0], 1238:] = crop
        for width, height in ((1280, 720), (1440, 810), (1920, 1080), (2560, 1440)):
            for visible in (True, False):
                with self.subTest(size=(width, height), visible=visible):
                    sample = source.copy()
                    if not visible:
                        sample[:90, 1315:1410] = 0
                    frame = cv2.resize(sample, (width, height))
                    task = SimpleNamespace(
                        config={}, capture_frame=lambda: frame,
                        info_set=Mock(), operate_click=Mock(), sleep=Mock(),
                    )
                    vision = Vision(task)
                    vision.click_client = Mock()
                    navigator = Navigator(task, vision)
                    navigator.require_home_match = True
                    navigator.classify = lambda: ScreenState.SANDBOX
                    navigator._wait_for_cartridge_home = Mock(return_value=True)

                    result = navigator.return_home()

                    self.assertEqual(visible, result.success)
                    task.operate_click.assert_not_called()
                    if visible:
                        vision.click_client.assert_called_once()
                        center = vision.click_client.call_args.args[0]
                        self.assertAlmostEqual(1357 / 1440, center[0] / width, delta=0.005)
                        self.assertAlmostEqual(37 / 810, center[1] / height, delta=0.005)
                        navigator._wait_for_cartridge_home.assert_called_once()
                        self.assertEqual(ScreenState.HOME, result.state)
                    else:
                        vision.click_client.assert_not_called()
                        navigator._wait_for_cartridge_home.assert_not_called()

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
                    sleep=lambda *_args: None,
                )
                vision = SimpleNamespace(
                    capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
                    match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
                    passes=lambda *_args: False,
                )
                navigator = Navigator(task, vision)
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
            sleep=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
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

class TradeCartridgeMenuTest(unittest.TestCase):

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

        self.assertFalse(navigator._wait_for_cartridge_category(timeout=0.0))
        highlight["value"] = STORY_CATEGORY_HIGHLIGHT_MIN_RATIO
        self.assertTrue(navigator._wait_for_cartridge_category(timeout=0.0))

        text["value"] = "角色游戏卡"
        self.assertFalse(navigator._wait_for_cartridge_category(timeout=0.0))
