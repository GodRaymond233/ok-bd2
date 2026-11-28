"""Regression coverage for shop transactions, selection and cooking."""

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from src.tasks import run_history
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.calendar import purchase_stock_date
from src.tasks.map_trade.data import SHOP_CARTRIDGE_PAGES, SHOP_FAVORITE_POINTS
from src.tasks.map_trade.models import (
    DEFAULT_RECIPES,
    CalendarEntry,
    MatchResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    BARGAIN_CONFIRM_POINT,
    BARGAIN_POINT,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    Q_SP6_BARGAIN_CLICK_DELAY,
    Navigator,
)
from src.tasks.map_trade.progress import UTC_PLUS_8
from src.tasks.map_trade.trader_constants import (
    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
    SALE_120_PERCENT_MARKER_BETA_TEMPLATE,
    SALE_120_PERCENT_MARKER_MAX_RESULTS,
    SALE_120_PERCENT_MARKER_PEAK_RADIUS,
    SALE_120_PERCENT_MARKER_TEMPLATE,
    SALE_CLOSE_POINT,
    SALE_CONFIRM_POINT,
    SHOP_CARTRIDGE_OCR_RELATIVE_ROI,
    SHOP_UP_SCROLL_RECOGNITION_INTERVAL,
)
from src.tasks.map_trade.trader_cooking import (
    COOKING_IDENTITY_SPECS,
    COOKING_MAX_QUANTITY_TEMPLATE,
    COOKING_QUANTITY_ATTEMPTS,
    COOKING_TEMPLATE_DIR,
    CookingDetailSnapshot,
    CookingListSnapshot,
    CookingRecipeOutcome,
)
from src.tasks.map_trade.trader_sell import SaleItemCandidate
from src.tasks.map_trade.vision import Vision
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
)
from tests.helpers import FakeClock, StageHistoryTestCase
from tests.helpers.map_trade import create_trader
from tests.helpers.recognition_fixtures import load_bgr

ROOT = Path(__file__).resolve().parents[1]

FIXTURES = ROOT / "tests/fixtures/map_trade"


def text_box(name, x, y, width, height):
    return SimpleNamespace(name=name, confidence=0.99, x=x, y=y, width=width, height=height)


def sale_harness():
    trader = create_trader()
    trader.task.log_info = Mock()
    trader.task.log_warning = Mock()
    trader.task.operate_click = Mock()
    trader.vision.click_client = Mock()
    trader._sale_name_signature = Mock(return_value=())
    trader._sale_toast_id = Mock(return_value=None)
    trader._wait_sale_dialog_item = Mock(return_value=True)
    trader._ensure_sell_page = Mock(return_value=True)
    return trader


class SaleRecognitionTest(unittest.TestCase):
    def test_sale_marker_template_gates_and_positions_at_native_and_scaled_sizes(self):
        fixture = load_bgr(FIXTURES / "trade_shop/sale_120_markers_fhd.png")
        self.assertIsNotNone(fixture)
        vision = create_trader().vision
        standard, beta = SALE_120_PERCENT_MARKER_TEMPLATE, SALE_120_PERCENT_MARKER_BETA_TEMPLATE
        for width, height, spec, roi, xs in (
            (1920, 1080, standard, None, [493, 825, 1157]),
            (1280, 720, standard, None, [493, 825, 1157]),
            (2560, 1440, standard, None, [493, 825, 1157]),
            (3840, 2160, standard, None, [493, 825, 1157]),
            (1920, 1080, beta, (463, 548, 150, 47), [493]),
        ):
            with self.subTest(height=height, spec=spec.file_name):
                interpolation = cv2.INTER_AREA if height < 1080 else cv2.INTER_CUBIC
                frame = cv2.resize(fixture, (width, height), interpolation=interpolation)
                threshold = vision.threshold_for(spec)
                hits = sorted(
                    vision.match_all(
                        frame,
                        spec,
                        search_roi=roi,
                        minimum_score=threshold,
                        peak_radius=SALE_120_PERCENT_MARKER_PEAK_RADIUS,
                        max_results=SALE_120_PERCENT_MARKER_MAX_RESULTS,
                    ),
                    key=lambda hit: hit.position,
                )
                self.assertEqual(len(xs), len(hits))
                for x, hit in zip(xs, hits, strict=True):
                    self.assertAlmostEqual(round((x + 26) * width / 1920), hit.center[0], delta=2)
                    self.assertAlmostEqual(round(570 * height / 1080), hit.center[1], delta=2)
                    self.assertGreaterEqual(hit.score, 0.99 if roi else threshold)
                    self.assertGreaterEqual(hit.pixel_score, 0.97 if roi else spec.min_pixel_score)
                    if height == 1080:
                        self.assertEqual((x, 563), hit.position)

    def test_locate_sale_items_pairs_real_markers_with_name_ocr(self):
        fixture = load_bgr(FIXTURES / "trade_shop/sale_120_markers_fhd.png")
        self.assertIsNotNone(fixture)
        boxes = [
            text_box("4120%", 493, 563, 52, 14),
            text_box("水果罐头", 613, 560, 42, 23),
            text_box("4129", 825, 563, 52, 14),
            text_box("水果罐头", 945, 560, 42, 23),
            text_box("120", 1157, 563, 52, 14),
            text_box("水果罐头", 1277, 560, 42, 23),
        ]
        trader = create_trader()
        trader.vision = Vision(SimpleNamespace(config={}, vision_threshold_key="跑图跑商识图阈值"))
        entry = CalendarEntry("水果罐头", "S2:苍蓝魔女")
        for labels, image, centers, markers, unavailable in (
            (
                boxes,
                fixture,
                [(634, 572), (966, 572), (1298, 572)],
                [(493, 563), (825, 563), (1157, 563)],
                False,
            ),
            (boxes, np.zeros_like(fixture), [], [], False),
            ([], fixture, [], [], False),
            ([text_box("胡萝卜", 613, 560, 42, 23)], fixture, [], [], True),
        ):
            with self.subTest(labels=len(labels), centers=centers):
                trader.vision.ocr_boxes = Mock(return_value=labels)
                candidates = trader._locate_sale_items(entry, image)
                self.assertEqual(centers, [candidate.center for candidate in candidates])
                self.assertEqual(markers, [(c.percent_box.x, c.percent_box.y) for c in candidates])
                self.assertIs(trader._last_sale_unavailable, unavailable)

    def test_native_2k_sale_recovery_requires_exact_percent_and_unique_marker(self):
        entry = CalendarEntry("火烤鱼板棒", "S17:试炼之路")
        for scene in ("new", "old"):
            frame = load_bgr(
                ROOT / f"tests/fixtures/map_trade/trade_shop/sale_native_2k_{scene}.png",
            )
            for texts in (
                ("120%",),
                ("119%",),
                ("1120%",),
                ("4120%",),
                ("120",),
                ("12%",),
                ("120%", "118%"),
                (),
            ):
                with self.subTest(scene=scene, texts=texts):
                    trader = create_trader()
                    name = text_box(entry.item, 795, 226, 141, 34)
                    trader.vision.ocr_boxes = Mock(
                        side_effect=lambda _f, title, **_kw: (
                            [name]
                            if title == "出售商品列表"
                            else [text_box(text, 672, 230, 55, 20) for text in texts]
                        )
                    )
                    candidates = trader._locate_sale_items(entry, frame)
                    self.assertEqual(int(texts == ("120%",)), len(candidates))
                    self.assertIsNone(trader._sale_reference_view)
                    if texts == ("120%",):
                        self.assertEqual((866, 243), candidates[0].center)
                        self.assertTrue(candidates[0].percent_box.reference_view)
                        self.assertAlmostEqual(656, candidates[0].percent_box.x, delta=2)
                        self.assertAlmostEqual(230, candidates[0].percent_box.y, delta=2)
            duplicate = frame.copy()
            duplicate[210:276, 595:795] = 0
            for x in (600, 718):
                duplicate[230:248, x : x + 71] = frame[230:248, 656:727]
            trader.vision.ocr_boxes = Mock(return_value=[name])
            self.assertEqual(
                [],
                trader._sale_template_percent_boxes(
                    duplicate,
                    (595, 210, 200, 66),
                    cv2.cvtColor(duplicate, cv2.COLOR_BGR2GRAY),
                ),
            )
            trader.vision.ocr_boxes.assert_not_called()

    def test_recovered_sale_rechecks_fresh_frame_before_click(self):
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
            candidate = SimpleNamespace(
                center=(866, 243),
                percent_box=SimpleNamespace(
                    reference_view=True,
                    capture_source="old" if changed == "source" else None,
                ),
            )
            self.assertIsNone(
                trader._sell_one_candidate(
                    CalendarEntry("火烤鱼板棒", "S17"),
                    candidate,
                    frame,
                    previous_owned=None,
                )
            )
            trader.vision.click_client.assert_not_called()

    def test_dialog_title_needs_two_consecutive_unambiguous_frames(self):
        for item, lines, timeout, valid, rejected in (
            *[("姜黄", [word, word], 2, True, False) for word in ("姜黄", "姜黃", "薑黄", "薑黃")],
            *[
                ("秃鹫羽毛", [word, word], 2, True, False)
                for word in ("秃鹫羽毛", "秃鷲羽毛", "禿鹫羽毛", "禿鷲羽毛")
            ],
            ("穿山甲鳞片", ["穿山甲鱗片", "穿山甲鱗片"], 2, True, False),
            ("黑啤酒炖锅", ["黑啤酒燉锅", "黑啤酒燉锅"], 2, True, False),
            ("苹果", ["蘋果", "蘋果"], 2, True, False),
            ("三角美乃滋饭团", ["三角美乃滋飯團", "三角美乃滋飯團"], 2, True, False),
            *[("姜黄", [word], 0, False, False) for word in ("", "姜", "黄", "姜黄油")],
            ("白糖", ["白糖"], 0, False, False),
            ("姜黄", ["豆子"], 2, False, True),
            ("巧克力", [("巧克力", "鸡尾酒")], 2, False, True),
            ("巧克力", ["巧克力鸡尾酒"], 2, False, True),
            ("巧克力鸡尾酒", [("巧克力", "鸡尾酒")] * 2, 2, True, False),
            ("苹果", [("苹果", "豆子")] * 2, 2, True, False),
            ("白糖", ["白糖", "", "白糖", "白糖"], 2, True, False),
        ):
            with self.subTest(item=item, lines=lines):
                trader = create_trader()
                clock = FakeClock()
                trader.task.sleep = clock.sleep
                trader.task.log_warning = Mock()
                frame = np.full((1080, 1920, 3), (20, 70, 150), np.uint8)
                trader.vision.capture = Mock(return_value=frame)

                def read(_frame, _name, **_kwargs):
                    line = lines[min(trader.vision.capture.call_count, len(lines)) - 1]
                    words = line if isinstance(line, tuple) else (line,)
                    return [SimpleNamespace(name=w, confidence=0.75) for w in words if w]

                trader.vision.ocr_boxes = Mock(side_effect=read)
                with patch("src.tasks.map_trade.trader_sell.monotonic", clock):
                    self.assertIs(
                        trader._wait_sale_dialog_item(CalendarEntry(item, "S12"), timeout=timeout),
                        valid,
                    )
                self.assertEqual(rejected, trader._sale_dialog_rejected)
                self.assertEqual(2 if rejected else len(lines), trader.vision.capture.call_count)
                if rejected:
                    trader.task.log_warning.assert_called()
                if valid and "" not in lines:
                    self.assertEqual(
                        ["原图"] * 2,
                        [c.args[1].split("/")[-1] for c in trader.vision.ocr_boxes.call_args_list],
                    )

    def test_variant_char_folding_has_no_cross_item_collisions(self):
        from src.tasks.map_trade.calendar import BUNDLED_CALENDAR_HISTORY, parse_calendar_payload
        from src.tasks.map_trade.trader_constants import CALENDAR_DIR

        trader = create_trader()
        catalog = trader._sale_title_catalog(CalendarEntry("秃鹫羽毛", "S9"))
        collisions = {key: ids for key, ids in catalog.items() if len(ids) > 1}
        self.assertEqual({}, collisions)
        canonical: set[str] = set()
        for filename in ("price_calendar.v1.json", *(name for _, name in BUNDLED_CALENDAR_HISTORY)):
            bundled = parse_calendar_payload((CALENDAR_DIR / filename).read_bytes())
            canonical.update(item.item for items in bundled.days.values() for item in items)
        self.assertEqual(len(canonical), len({trader._normal(name) for name in canonical}))
        for variant in ("秃鹫羽毛", "秃鷲羽毛", "禿鹫羽毛", "禿鷲羽毛"):
            self.assertEqual({"秃鹫羽毛"}, catalog.get(trader._normal(variant)))

    def test_retry_after_unconfirmed_dialog_title_stops_batch_and_reports_dialog_ocr(self):
        trader = create_trader()
        frame = np.full((1080, 1920, 3), (20, 70, 150), np.uint8)
        trader.vision.capture = Mock(return_value=frame)
        trader.vision.click_client = Mock()
        trader._sale_name_signature = Mock(return_value=None)
        trader._sale_toast_id = Mock(return_value=None)
        trader.select_shop_tab = Mock(return_value=True)
        trader._ensure_cartridge_goods_loaded = Mock(return_value=True)
        trader._sale_date_current = Mock(return_value=True)
        trader.task.log_info = Mock()
        trader.task.log_warning = Mock()
        candidate = SaleItemCandidate(
            center=(639, 782),
            name_box=SimpleNamespace(x=600, y=770, width=80, height=24),
            percent_box=SimpleNamespace(x=540, y=770, width=50, height=24),
        )
        trader._wait_sale_item_candidates = Mock(return_value=([candidate], frame))

        def fake_wait(_entry):
            trader._last_sale_dialog_title_texts = ("未知异体商品名",)
            return False

        def fake_locate(_entry, _frame):
            # 模拟补点前在弹窗遮罩画面上未识出商品名，导致 _last_sale_unavailable 被置 True。
            trader._last_sale_unavailable = True
            return []

        trader._wait_sale_dialog_item = Mock(side_effect=fake_wait)
        trader._locate_sale_items = Mock(side_effect=fake_locate)

        first = CalendarEntry("秃鹫羽毛", "S9")
        second = CalendarEntry("松露油", "C1")
        self.assertFalse(trader._sell_resolved_entries([first, second]))
        self.assertEqual(1, trader.vision.click_client.call_count)
        self.assertEqual(1, trader.select_shop_tab.call_count)
        self.assertFalse(trader._last_sale_unavailable)
        self.assertIn("弹窗标题区 OCR 未确认", trader._last_sale_reason)
        self.assertIn("未知异体商品名", trader._last_sale_reason)
        self.assertNotIn("未出售：未出售：", trader._sale_not_sold[first])
        self.assertEqual("未出售：前序商品出售失败，未执行", trader._sale_not_sold[second])


class SaleLedgerTest(StageHistoryTestCase):
    def test_market_refresh_blocks_new_entry_and_final_confirmation(self):
        trader = create_trader()
        trader.task = SimpleNamespace(
            config={}, log_info=Mock(), log_warning=Mock(), info_set=Mock(), operate_click=Mock()
        )
        trader._sale_calendar_date = datetime(2026, 11, 18).date()
        trader.now_provider = lambda: datetime(2026, 11, 18, 23, tzinfo=UTC_PLUS_8)
        trader.select_shop_tab = Mock()
        entry = CalendarEntry("苹果", "S1")
        self.assertFalse(trader._sell_resolved_entries([entry]))
        trader.select_shop_tab.assert_not_called()

        trader.vision = SimpleNamespace(click_client=Mock())
        trader._sale_name_signature = Mock(return_value=())
        trader._sale_toast_id = Mock(return_value=None)
        trader._wait_sale_dialog_item = Mock(return_value=True)
        trader._wait_owned_quantity = Mock(return_value=100)
        trader._wait_available_quantity = Mock(return_value=50)
        trader._choose_sale_quantity = Mock(return_value=True)
        trader._wait_selected_sale_quantity = Mock(return_value=(50, None))
        candidate = SimpleNamespace(center=(10, 10), percent_box=None)
        self.assertIsNone(
            trader._sell_one_candidate(
                entry,
                candidate,
                np.zeros((30, 30, 3), np.uint8),
                previous_owned=None,
            )
        )
        trader.task.operate_click.assert_not_called()
        self.assertNotEqual(
            "pending",
            run_history.read_stage(trader.task, "出售:S1:苹果").get(
                "state",
            ),
        )
        self.assertIn("价表已刷新", trader._last_sale_reason)

    def test_pending_sale_reconciles_exact_stock_without_a_second_submission(self):
        for owned, selected, remaining, reserve, expected, counted in (
            (100, 10, 100, 0, None, 0),
            (100, 10, 90, 0, (100, True), 10),
            (8400, 2850, 5550, 5500, (8400, True), 2850),
            (100, None, 90, 90, (90, False), 0),
            (100, 10, 90, 90, (90, False), 10),
        ):
            with self.subTest(owned=owned, selected=selected, remaining=remaining, reserve=reserve):
                self.reset_stage_history()
                trader = sale_harness()
                entry = CalendarEntry("苹果", "1", reserve=reserve)
                stage = "出售:1:苹果"
                run_history.save_stage(
                    trader.task, stage, "pending", evidence={"owned": owned, "selected": selected}
                )
                trader._wait_owned_quantity = Mock(return_value=remaining)
                trader._wait_available_quantity = Mock(return_value=remaining)
                frame = np.zeros((30, 30, 3), np.uint8)
                candidate = SimpleNamespace(center=(10, 10), percent_box=None)
                self.assertEqual(
                    expected,
                    trader._sell_one_candidate(entry, candidate, frame, previous_owned=None),
                )
                record = run_history.read_stage(trader.task, stage)
                self.assertEqual("done" if expected else "pending", record["state"])
                self.assertEqual(counted, trader._sale_sold.get(entry, 0))
                self.assertEqual(counted, trader._sale_recovered.get(entry, 0))
                self.assertNotIn(
                    SALE_CONFIRM_POINT, [c.args for c in trader.task.operate_click.call_args_list]
                )
                if counted and reserve:
                    trader.task.operate_click.assert_called_once_with(
                        *SALE_CLOSE_POINT, after_sleep=0.5
                    )
                if expected == (90, False) and counted:
                    self.assertIn("已达到保留量 90 个", trader._sale_not_sold[entry])

    def test_unconfirmed_partial_sale_persists_selected_quantity_for_recovery(self):
        trader = sale_harness()
        frame = np.zeros((30, 30, 3), np.uint8)
        entry = CalendarEntry("苹果", "1", reserve=5500)
        candidate = SimpleNamespace(center=(10, 10), percent_box=None)
        trader._wait_owned_quantity = Mock(return_value=8400)
        trader._wait_available_quantity = Mock(return_value=8400)
        trader._choose_sale_quantity = Mock(return_value=True)
        trader._wait_sale_completion = Mock(return_value=False)
        trader.vision.capture = Mock(return_value=frame)
        trader.vision.ocr_text = Mock(return_value="拥有 8400 个 可购买 8400 个 2850 个")
        self.assertIsNone(trader._sell_one_candidate(entry, candidate, frame, previous_owned=None))
        pending = run_history.read_stage(trader.task, "出售:1:苹果")
        self.assertEqual("pending", pending["state"])
        self.assertEqual({"owned": 8400, "selected": 2850}, pending["evidence"])
        self.assertEqual(
            1, [c.args for c in trader.task.operate_click.call_args_list].count(SALE_CONFIRM_POINT)
        )

        def missing(_entry):
            trader._last_sale_page_empty = True
            return None

        trader._wait_sale_item_candidates = missing
        self.assertTrue(trader._sell_selected_entry(entry))
        self.assertEqual({}, trader._sale_sold)
        self.assertIn("出售数量未确认", trader._sale_not_sold[entry])
        self.assertEqual(
            1, [c.args for c in trader.task.operate_click.call_args_list].count(SALE_CONFIRM_POINT)
        )

    def test_sale_checkpoint_follows_item_identity_across_calendar_spellings(self):
        trader = sale_harness()
        trader.task.config["出售保险"] = True
        frame = np.zeros((30, 30, 3), np.uint8)
        candidate = SimpleNamespace(center=(10, 10), percent_box=None)
        trader._wait_sale_item_candidates = Mock(return_value=([candidate], frame))
        trader._wait_owned_quantity = Mock(return_value=100)
        trader._wait_available_quantity = Mock(return_value=100)
        trader._choose_sale_quantity = Mock(return_value=True)
        trader._wait_selected_sale_quantity = Mock(return_value=(1, 25))
        trader._wait_sale_completion = Mock(return_value=False)
        entry = CalendarEntry("蜂蜜黃油杏仁", "S9:铁假面")
        prefix = "出售:S9:铁假面:"
        canonical = prefix + "蜂蜜黄油杏仁"
        legacy = prefix + "Honey Butter Almond"
        owner = run_history.stage_owner(trader.task)
        evidence = {"owned": 100, "selected": 1}
        self.assertFalse(trader._sell_selected_entry(entry))
        self.assertEqual({canonical}, set(self.history.pending_stages(owner, prefix)))
        self.history_mock.return_value = run_history.RunHistoryStore(self.history.path)
        self.assertFalse(trader._sell_selected_entry(CalendarEntry("蜂蜜奶油杏仁", entry.shop)))
        trader.task.operate_click.assert_called_once_with(*SALE_CONFIRM_POINT, after_sleep=0.5)

        self.reset_stage_history()
        run_history.save_stage(trader.task, legacy, "pending", evidence=evidence)
        run_history.save_stage(trader.task, "出售:S1:蜂蜜黄油杏仁", "pending", evidence=evidence)
        trader._wait_owned_quantity.return_value = 99
        trader.task.operate_click.reset_mock()
        self.assertTrue(trader._sell_selected_entry(entry))
        self.assertEqual("done", self.history.stage(owner, legacy)["state"])
        self.assertEqual({}, self.history.stage(owner, canonical))
        trader.task.operate_click.assert_called_once_with(*SALE_CLOSE_POINT, after_sleep=0.5)

        for key in (canonical, legacy):
            run_history.save_stage(trader.task, key, "pending", evidence=evidence)
        trader.task.operate_click.reset_mock()
        trader.vision.click_client.reset_mock()
        self.assertFalse(trader._sell_selected_entry(entry))
        trader.vision.click_client.assert_not_called()
        def missing(_entry):
            trader._last_sale_page_empty = True
            trader._last_sale_unavailable = True
            return None

        trader._wait_sale_item_candidates.side_effect = missing
        self.assertFalse(trader._sell_selected_entry(entry))
        self.assertFalse(trader._last_sale_unavailable)
        self.assertEqual({legacy, canonical}, set(self.history.pending_stages(owner, prefix)))
        trader.task.operate_click.assert_not_called()

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
            with self.subTest(
                insurance=insurance,
                item=item,
                reserve=reserve,
                owned=owned,
                available=available,
                selected=selected,
            ):
                self.reset_stage_history()
                clicks = []
                clock = FakeClock()
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={"出售保险": insurance},
                    operate_click=lambda x, y, after_sleep=0: clicks.append((x, y)),
                    log_info=lambda *_a: None,
                    log_warning=lambda *_a: None,
                    info_set=lambda *_a: None,
                    sleep=clock.sleep,
                )
                trader.vision = SimpleNamespace(
                    click_client=lambda *_a, **_k: None,
                    capture=lambda: frame,
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
                with patch("src.tasks.map_trade.trader_sell.monotonic", clock):
                    result = trader._sell_one_candidate(
                        CalendarEntry(item, "S2", reserve=reserve),
                        SimpleNamespace(center=(640, 462), percent_box=None),
                        frame,
                        previous_owned=None,
                    )
                self.assertEqual((owned, True) if confirmed else None, result)
                self.assertEqual(confirmed, SALE_CONFIRM_POINT in clicks)
                self.assertEqual(selected if confirmed else 0, sum(trader._sale_sold.values()))

    def test_partial_sale_confirms_remaining_stock_without_resubmitting(self):
        for scenario in (
            "sold",
            "last_blank",
            "unchanged",
            "wrong_stock",
            "wrong_title",
            "open",
            "blank",
        ):
            with self.subTest(scenario=scenario):
                self.reset_stage_history()
                clock = FakeClock()
                clicks = []
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                candidate = SimpleNamespace(center=(620, 572), percent_box=None)
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={},
                    info_set=Mock(),
                    log_info=Mock(),
                    log_warning=Mock(),
                    sleep=clock.sleep,
                    operate_click=lambda x, y, **_kw: clicks.append((x, y)),
                )
                trader.vision = SimpleNamespace(
                    capture=lambda: frame,
                    click_client=Mock(),
                    simplify=lambda text: text,
                    ocr_text=lambda _frame, _name, **_kw: (
                        "苹果 拥有77,850个 可购买77850个"
                        if scenario == "open"
                        else ""
                        if scenario == "blank"
                        or (scenario == "last_blank" and _name == "出售交易完成" and clock.now > 1)
                        else "出售 料理食材 苹果"
                    ),
                )
                trader._sale_name_signature = Mock(return_value=(("苹果", 600, 560, 60, 24),))
                trader._sale_toast_id = Mock(return_value=None)
                wait_candidates = trader._wait_sale_item_candidates
                trader._locate_sale_items = Mock(return_value=[candidate])
                trader._wait_sale_item_candidates = Mock(
                    side_effect=lambda entry, **kwargs: (
                        wait_candidates(entry, **kwargs) if kwargs else ([candidate], frame)
                    )
                )
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
                trader._wait_selected_sale_quantity = Mock(return_value=(72350, None))
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
                self.reset_stage_history()
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                trader = create_trader()
                trader.task = SimpleNamespace(
                    config={"出售保险": False},
                    operate_click=lambda *_a, **_k: None,
                    info_set=lambda *_a: None,
                    log_info=lambda *_a: None,
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
                    selected.append(expected) or (expected, None)
                )
                trader._wait_sale_completion = lambda *_a, **_k: len(scans) == 1 or completed
                self.assertEqual(
                    completed,
                    trader._sell_selected_entry(CalendarEntry("巧克力", "S4")),
                )
                self.assertEqual([32302, 99999], selected)
                self.assertEqual(completed, trader._last_sale_cleared)
                self.assertEqual(
                    132301 if completed else 32302,
                    sum(trader._sale_sold.values()),
                )

    def test_saffron_sale_requires_a_fresh_credit_matching_the_quote(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        signature = (("藏红花", 620, 560, 44, 23),)
        quote = "藏红花 拥有4,265个 4265个 149275-179,130 ▲120% 可购买4265个 MIN -10 +10 MAX 出售"
        for previous, current, dialog, completed in (
            ("出售", "出售 +179,130 悠丝缇亚 食物", "", True),
            ("出售", "出售 +179,131 悠丝缇亚 食物", "", False),
            ("出售 +179,130", "出售 +179,130 悠丝缇亚 食物", "", False),
            ("出售", "出售 +179,130 悠丝缇亚 食物", "拥有4265个", False),
        ):
            with self.subTest(previous=previous, current=current, dialog=dialog):
                trader = create_trader()
                trader.task = SimpleNamespace(
                    info_set=lambda *_args: None,
                    log_warning=lambda *_args: None,
                )
                trader.vision = SimpleNamespace(
                    capture=lambda: frame,
                    simplify=lambda value: value,
                    ocr_text=lambda _frame, name, **_kwargs: {
                        "出售弹窗已选数量": quote,
                        "出售前到账提示": previous,
                        "出售弹窗完成确认": dialog,
                        "出售交易完成": current,
                    }[name],
                )
                trader._sale_name_signature = lambda *_args: signature
                self.assertEqual(
                    (4265, 179130),
                    trader._wait_selected_sale_quantity(
                        4265,
                        timeout=0,
                        maximum=4265,
                    ),
                )
                self.assertEqual(
                    completed,
                    trader._wait_sale_completion(
                        CalendarEntry("藏红花", "S10"),
                        frame,
                        signature,
                        timeout=0,
                        before_toast_id=5,
                        owned=4265,
                        selected=4265,
                        payout=179130,
                    ),
                )


class PurchaseLedgerTest(StageHistoryTestCase):
    def test_recorded_sold_out_overrides_a_visible_buy_button_on_the_same_frame(self):
        template = BUY_TO_SELL_SOLD_OUT_TEMPLATE
        buy, sold, sell = [
            load_bgr(FIXTURES / "trade_shop" / f"{name}.png")
            for name in ("before_purchase", "after_purchase", "sell_page")
        ]
        vision = Vision(
            SimpleNamespace(config={"跑商识图阈值": 0.72}, vision_threshold_key="跑商识图阈值")
        )
        for frame, expected in ((buy, False), (sold, True), (sell, False)):
            self.assertIsNotNone(frame)
            result = vision.match(frame, template)
            self.assertEqual(expected, vision.passes(result, template))
            if expected:
                self.assertGreaterEqual(result.score, 0.95)
                self.assertGreaterEqual(result.pixel_score, 0.96)
                self.assertGreaterEqual(result.zncc_score, 0.95)
        for frames, submit in (
            ([sold, sold], False),
            ([buy, sold, sold], False),
            ([sold, buy, sold, sold], False),
            ([buy, sold, buy, buy], True),
        ):
            with self.subTest(count=len(frames), submit=submit):
                self.reset_stage_history()
                trader = create_trader()
                trader.task.log_info = trader.task.log_warning = Mock()
                trader.vision.capture = Mock(side_effect=frames)
                trader.vision.match = Mock(wraps=trader.vision.match)
                trader.vision.ocr_boxes = Mock(
                    return_value=[text_box("一键购买全部收藏", 100, 80, 40, 10)]
                )
                trader.vision.click_client = Mock()
                trader._wait_for_purchase_confirmation = Mock(return_value=True)
                trader._wait_for_purchase_completion = Mock(return_value=True)
                self.assertTrue(trader.buy_all_favorites())
                self.assertEqual(int(submit), trader.vision.click_client.call_count)
                self.assertEqual(int(submit), len(trader.task.clicks))
                self.assertEqual(len(frames), trader.vision.ocr_boxes.call_count)
                self.assertEqual(len(frames), trader.vision.match.call_count)
                for frame, ocr, match in zip(
                    frames,
                    trader.vision.ocr_boxes.call_args_list,
                    trader.vision.match.call_args_list,
                    strict=True,
                ):
                    self.assertIs(frame, ocr.args[0])
                    self.assertIs(frame, match.args[0])

    def test_purchase_arms_on_confirm_and_reconciles_uncertain_input_without_resubmission(self):
        for scenario, state, submission in (
            ("missing question", None, None),
            ("unsent", None, None),
            ("unknown", "pending", "armed"),
            ("sent", "pending", "sent"),
            ("confirmed", "done", None),
        ):
            with self.subTest(scenario=scenario):
                self.reset_stage_history()
                trader = create_trader()
                trader.task.log_info = trader.task.log_warning = Mock()
                frame = np.zeros((720, 1280, 3), np.uint8)
                trader._wait_for_buy_all_favorites_state = Mock(return_value=((10, 10), frame))
                trader._wait_for_purchase_confirmation = Mock(
                    return_value=scenario != "missing question"
                )
                trader._wait_for_purchase_completion = Mock(return_value=scenario == "confirmed")
                trader.vision.click_client = Mock()
                trader.task.operate_click = Mock()
                if scenario in {"unsent", "unknown"}:
                    error = RuntimeError("input failed")
                    error.input_not_dispatched = scenario == "unsent"
                    trader.task.operate_click.side_effect = error
                    with self.assertRaisesRegex(RuntimeError, "input failed"):
                        trader.buy_all_favorites()
                else:
                    self.assertEqual(scenario == "confirmed", trader.buy_all_favorites())
                stage = "购买全部收藏"
                record = run_history.read_stage(trader.task, stage)
                self.assertEqual(state, record.get("state"))
                self.assertEqual(submission, record.get("evidence", {}).get("submission"))
                self.assertEqual(
                    int(scenario != "missing question"), trader.task.operate_click.call_count
                )
                trader.vision.click_client.assert_called_once()
                if state == "pending":
                    trader.task.operate_click.reset_mock()
                    trader.vision.click_client.reset_mock()
                    trader._wait_for_purchase_completion.return_value = False
                    self.assertFalse(trader.buy_all_favorites())
                    trader._wait_for_purchase_completion.return_value = True
                    self.assertFalse(trader.buy_all_favorites())
                    trader._wait_for_buy_all_favorites_state.return_value = (None, frame)
                    self.assertTrue(trader.buy_all_favorites())
                    trader.task.operate_click.assert_not_called()
                    trader.vision.click_client.assert_not_called()
                    self.assertEqual("done", run_history.read_stage(trader.task, stage)["state"])

    def test_purchase_stock_rollover_archives_unknown_and_confirmation_boundary_stops(self):
        for evidence in ({"stock_date": "2026-11-12"}, {}):
            with self.subTest(evidence=evidence):
                trader = create_trader()
                trader.task.log_info = trader.task.log_warning = Mock()
                now = datetime(2026, 11, 13, 7, 59, tzinfo=UTC_PLUS_8)
                trader.now_provider = lambda: now
                with patch(
                    "src.tasks.run_history.time.time",
                    return_value=datetime(2026, 11, 12, 20, tzinfo=UTC_PLUS_8).timestamp(),
                ):
                    run_history.save_stage(
                        trader.task,
                        "购买全部收藏",
                        "pending",
                        evidence=evidence,
                    )
                record = run_history.read_stage(trader.task, "购买全部收藏")
                for hour, minute in ((3, 59), (4, 0), (7, 59)):
                    now = now.replace(hour=hour, minute=minute)
                    self.assertEqual(
                        record, trader._purchase_record_for_stock(purchase_stock_date(now))
                    )
                now = now.replace(hour=8, minute=0)
                self.assertEqual({}, trader._purchase_record_for_stock(now.date()))
                retired = run_history.default_store()._retired_stages[-1]
                self.assertEqual(record, retired["record"])
                self.assertIn("未知", retired["reason"])
                self.assertEqual({}, run_history.read_stage(trader.task, "购买全部收藏"))

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        trader._wait_for_buy_all_favorites_state = Mock(return_value=((10, 10), frame))
        trader._wait_for_purchase_confirmation = Mock(return_value=True)
        trader.vision.click_client = Mock()
        trader.task.operate_click = Mock()

        def refresh(_seconds):
            nonlocal now
            now = now.replace(hour=8, minute=0)

        for during_input in (False, True):
            now = datetime(2026, 11, 14, 7, 59, tzinfo=UTC_PLUS_8)

            def operate(callback):
                if during_input:
                    refresh(0)
                return callback()

            trader.task.operate = operate
            trader.task.sleep = (lambda _seconds: None) if during_input else refresh
            self.assertFalse(trader.buy_all_favorites())
            trader.task.operate_click.assert_not_called()
            self.assertEqual({}, run_history.read_stage(trader.task, "购买全部收藏"))


class ShopSelectionTest(unittest.TestCase):
    def test_favorites_are_recorded_only_for_confirmed_pages_and_aligned_cards(self):
        pages = SHOP_CARTRIDGE_PAGES
        first_page = list(pages[0].shop_ids)
        all_cards = [shop for page in pages for shop in page.shop_ids]
        for observations, ok, cards in (
            ([True] * 4, True, all_cards),
            ([True, False, True, True, True], True, all_cards),
            ([True, False, False], False, first_page),
        ):
            with self.subTest(observations=observations):
                trader = create_trader()
                trader.task.log_info = trader.task.log_warning = Mock()
                trader.progress.favorite_card_complete = Mock(return_value=False)
                trader.progress.mark_favorite_card = Mock()
                trader.progress.mark_favorites_built = Mock()
                trader._reset_shop_to_first_page = Mock(return_value=True)
                trader._wait_for_shop_page = Mock(side_effect=observations)
                trader._shop_list_top_row_index = Mock(return_value=9)
                trader._scroll_shop_cartridges = Mock()
                trader._select_purchase_cartridge = Mock(return_value=True)
                trader._align_favorite_slots = Mock(return_value=True)
                self.assertIs(trader.rebuild_favorites(), ok)
                expected = [call(shop) for shop in cards]
                self.assertEqual(expected, trader._select_purchase_cartridge.call_args_list)
                self.assertEqual(expected, trader._align_favorite_slots.call_args_list)
                self.assertEqual(expected, trader.progress.mark_favorite_card.call_args_list)
                self.assertEqual(int(ok), trader.progress.mark_favorites_built.call_count)
                scrolls = [(-1, 9, 0.1, 0.5)]
                if False in observations:
                    scrolls.append((-1, 1, 0.0, SHOP_UP_SCROLL_RECOGNITION_INTERVAL))
                if ok:
                    scrolls.extend([(-1, 10, 0.1, 0.5), (-1, 1, 0.1, 0.5)])
                self.assertEqual(
                    scrolls,
                    [
                        tuple(c.kwargs.values()) if c.kwargs else c.args
                        for c in trader._scroll_shop_cartridges.call_args_list
                    ],
                )

    def test_favorite_alignment_requires_star_or_matching_removal_toast(self):
        for toast, seventh, expected in (
            ("已将商品甜椒从收藏中移除", "yellow", True),
            ("已将商品甜椒加入收藏", "yellow", False),
            ("已将商品甜椒从收藏中移除", None, False),
        ):
            with self.subTest(toast=toast, seventh=seventh):
                trader = create_trader()
                trader.task.log_warning = Mock()

                def star(_frame, slot, _point, *, expected_yellow=None):
                    if expected_yellow is None:
                        return "yellow" if slot == 1 else None
                    if slot == 1:
                        return "gray"
                    return "yellow" if 2 <= slot <= 6 else seventh if slot == 7 else None

                trader._star_state = star
                trader.vision.ocr_text = Mock(return_value=toast)
                self.assertIs(trader._align_favorite_slots("S1"), expected)
                self.assertEqual(
                    [SHOP_FAVORITE_POINTS[i] for i in (1, 6)],
                    [(x, y) for x, y, _ in trader.task.clicks],
                )
                self.assertEqual(not expected, trader.task.log_warning.called)

    def test_cartridge_selection_pairs_template_competition_with_ocr_identity(self):
        for height, winner, best, second, label, name, expected in (
            (720, 3, 0.906, 0.828, 3, "迷雾神射手", True),
            (720, 3, 0.906, 0.870, 3, "迷雾神射手", False),
            (1080, 17, 0.981, 0.858, 17, "试炼之路", True),
            (1440, 17, 0.981, 0.858, 17, "试炼之路", True),
            (1080, 17, 0.981, 0.858, 18, "救赎", False),
        ):
            with self.subTest(height=height, best=best, second=second, label=label):
                scale = height / 1080
                frame = np.zeros((height, round(1920 * scale), 3), np.uint8)
                scores = {
                    f"shop/cartridges/story_cartridge_{winner:02}.png": best,
                    "shop/cartridges/story_cartridge_11.png": second,
                    "shop/cartridges/story_cartridge_01.png": 0.794,
                }

                def match(_frame, spec, **_):
                    if spec.file_name not in scores:
                        return ()
                    return (
                        MatchResult(
                            scores[spec.file_name],
                            (round(235 * scale), round(184 * scale)),
                            (round(78 * scale), round(57 * scale)),
                            pixel_score=0.95,
                        ),
                    )

                trader = create_trader()
                trader.task.log_warning = Mock()
                trader.vision.match_all = match
                trader.vision.threshold_for = Mock(return_value=0.72)
                trader.vision.ocr_boxes = Mock(
                    return_value=[
                        text_box(
                            f"剧情游戏卡 {label}",
                            round(318 * scale),
                            round(184 * scale),
                            round(140 * scale),
                            round(23 * scale),
                        ),
                        text_box(
                            name,
                            round(318 * scale),
                            round(213 * scale),
                            round(90 * scale),
                            round(24 * scale),
                        ),
                    ]
                )
                found = trader._confirmed_shop_cartridge_detections(frame)
                key = f"S{winner}"
                self.assertEqual({key} if expected else set(), set(found))
                if expected:
                    self.assertAlmostEqual(best - second, found[key].margin, places=3)
                    self.assertEqual("S11", found[key].runner_up.shop_id)
                    self.assertEqual(key, found[key].ocr.shop_id)
                    self.assertEqual(1.0, found[key].ocr.name_similarity)
                    self.assertEqual(
                        {"relative_roi": SHOP_CARTRIDGE_OCR_RELATIVE_ROI},
                        trader.vision.ocr_boxes.call_args.kwargs,
                    )


class TradeNavigationTest(unittest.TestCase):
    def test_bargain_requires_entry_text_then_a_confirmed_shop(self):
        for entry, description, shop, count, expected in (
            (False, False, False, 0, False),
            (True, False, False, 1, False),
            (True, True, False, 2, False),
            (True, True, True, 2, True),
        ):
            with self.subTest(entry=entry, description=description, shop=shop):
                task = SimpleNamespace(
                    config={}, operate_click=Mock(), sleep=Mock(), log_warning=Mock()
                )
                nav = Navigator(task, SimpleNamespace())
                nav._enter_q_sp6_shop = Mock(return_value=True)
                nav._wait_for_ocr_keywords = Mock(side_effect=[entry, description])
                nav._wait_for_bargain_shop_confirmation = Mock(return_value=shop)
                nav.classify = Mock(return_value=ScreenState.MERCHANT_DIALOG)
                self.assertIs(nav.enter_q_sp6_buy_flow().success, expected)
                self.assertEqual(
                    [BARGAIN_POINT, BARGAIN_CONFIRM_POINT][:count],
                    [c.args for c in task.operate_click.call_args_list],
                )
                self.assertEqual(
                    int(description), nav._wait_for_bargain_shop_confirmation.call_count
                )
                self.assertEqual(
                    int(entry), task.sleep.call_args_list.count(call(Q_SP6_BARGAIN_CLICK_DELAY))
                )

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

        navigator._home_confirmation_signals = Mock(return_value=(False,))
        navigator.classify = Mock(return_value=ScreenState.SANDBOX)
        for options, talents, expected in (
            ("对话 商店", "天赋技能 砍价 选择", ScreenState.MERCHANT_DIALOG),
            ("对话", "天赋技能 选择", ScreenState.SANDBOX),
            ("对话 商店", "选择", ScreenState.SANDBOX),
            ("", "", ScreenState.SANDBOX),
        ):
            with self.subTest(options=options, talents=talents):
                texts = {"跑商商人交互选项": options, "跑商商人天赋技能": talents}
                vision.ocr_text = Mock(side_effect=lambda _, name, **kw: texts[name])
                self.assertIs(navigator.classify_trade(frame), expected)
                self.assertGreater(vision.ocr_text.call_count, 0)
                for reading in vision.ocr_text.call_args_list:
                    self.assertIs(reading.args[0], frame)

    def test_return_home_recognizes_white_house_in_video(self):
        crop = load_bgr(ROOT / "tests/fixtures/map_trade/home_return/white_house_video_810.png")
        self.assertIsNotNone(crop)
        source = np.zeros((810, 1440, 3), dtype=np.uint8)
        source[: crop.shape[0], 1238:] = crop
        for width, height in ((1280, 720), (1440, 810), (1920, 1080), (2560, 1440)):
            for visible in (True, False):
                with self.subTest(size=(width, height), visible=visible):
                    sample = source.copy()
                    if not visible:
                        sample[:90, 1315:1410] = 0
                    frame = cv2.resize(sample, (width, height))
                    task = SimpleNamespace(
                        config={},
                        capture_frame=lambda: frame,
                        info_set=Mock(),
                        operate_click=Mock(),
                        sleep=Mock(),
                    )
                    vision = Vision(task)
                    vision.click_client = Mock()
                    navigator = Navigator(task, vision)
                    navigator.require_home_match = True
                    navigator.classify = lambda frame=None: ScreenState.SANDBOX
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

    def test_update_notice_clears_only_after_paired_keywords_inside_the_recorded_crop(self):
        frame = load_bgr(FIXTURES / "home_return/update_notice.png")
        self.assertIsNotNone(frame)
        for labels, expected in (
            (["更新"], False),
            (["7天内不再显示", "更新", "抢先看", "前往查看"], True),
        ):
            with self.subTest(labels=labels):
                task = SimpleNamespace(
                    config={"跑商 OCR 阈值": 0.2},
                    info_set=Mock(),
                    log_info=Mock(),
                    operate_click=Mock(),
                    _home_p95_threshold=lambda: 185,
                )
                task.ocr = Mock(return_value=[SimpleNamespace(name=text) for text in labels])
                task._quick_vision = lambda: Vision(task)
                self.assertIs(
                    BaseBD2Task.dismiss_home_announcement(task, frame, (False, 0, 0.0, ""), "跑商"),
                    expected,
                )
                task.ocr.assert_called_once()
                crop = task.ocr.call_args.kwargs["frame"]
                self.assertEqual((720, 1200, 3), crop.shape)
                self.assertGreater(np.count_nonzero(crop), 0)
                self.assertEqual(
                    [call(*HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT, after_sleep=0.2)]
                    * int(expected),
                    task.operate_click.call_args_list,
                )

    def test_q_sp6_confirmation_requires_sandbox_and_merchant_location_same_frame(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(
            0.96,
            (1200, 700),
            (80, 120),
            pixel_score=0.93,
            zncc_score=0.88,
        )
        matched_specs = []
        task = CookingTask()
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: matched_specs.append(spec) or match,
            passes=lambda result, spec: (
                result is match and spec is MERCHANT_CLICK_LOCATION_TEMPLATE
            ),
        )
        navigator = Navigator(task, vision)
        navigator._classify_trade_frame = lambda _frame: ScreenState.SANDBOX

        self.assertTrue(navigator.wait_for_q_sp6_sandbox(0.0))
        self.assertEqual([MERCHANT_CLICK_LOCATION_TEMPLATE], matched_specs)

        navigator._classify_trade_frame = lambda _frame: ScreenState.HOME
        self.assertFalse(navigator.wait_for_q_sp6_sandbox(0.0, log_timeout=False))
        self.assertEqual([], task.logs)
        self.assertFalse(navigator.wait_for_q_sp6_sandbox(0.0))
        self.assertEqual("warning", task.logs[-1][0])


class CookingTask:
    def __init__(self, **config):
        self.config = {
            "料理制作周期": "每周",
            "料理保险": True,
            **config,
        }
        self.clicks = []
        self.logs = []
        self.infos = []
        self.saved_frames = []

    def operate_click(self, x, y, after_sleep=0.0, **_kwargs):
        self.clicks.append((x, y, after_sleep))

    def sleep(self, _seconds):
        return None

    def save_frame(self, name, frame):
        self.saved_frames.append((name, frame))
        return Path("probe_outputs") / f"{name}.png"

    def log_info(self, message):
        self.logs.append(("info", message))

    def log_warning(self, message, **_kwargs):
        self.logs.append(("warning", message))

    def info_set(self, key, value):
        self.infos.append((key, value))


class CookingFlowTest(StageHistoryTestCase):
    def test_recipe_priority_and_sale_whitelist_follow_the_selected_groups(self):
        trader = create_trader()
        recipes = trader._selected_cooking_recipes()
        self.assertEqual(
            ("闪闪铁板虾", "透明沙拉", "街头烤鸡肉串", "橄榄油意面", "三明治便当", "巧克力面包",
             "冰镇甜点", "泰瑞丝派", "土豆汤", "水果冰沙", "巧克力鸡尾酒", "黑啤酒炖锅",
             "蜂蜜黄油杏仁", "火烤鱼板棒", "鱼子酱蛋包饭", "藏红花甜茶", "三文鱼芥末寿司",
             "凯洛养身汤", "卢戈山参烤串", "煤炭饼干", "地狱火紫菜包饭", "香草牛排"),
            recipes,
        )
        for recipe in recipes:
            template = COOKING_TEMPLATE_DIR / COOKING_IDENTITY_SPECS[recipe].file_name
            self.assertTrue(template.is_file(), recipe)
        trader.task.config["青框料理（吐司）"] = ["巧克力面包"]
        catalog = trader._sale_title_catalog(CalendarEntry("巧克力面包", "E1"))
        whitelist = trader._sale_whitelist(catalog)
        self.assertNotIn("三明治便当", whitelist)
        self.assertIn("巧克力面包", whitelist)

    def test_max_quantity_gate_on_recorded_min_and_max_slider_at_client_sizes(self):
        fixtures = Path(__file__).parent / "fixtures/map_trade/cooking_quantity"
        for name, expected in (("min", False), ("max", True)):
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            frame[970:1035, 300:730] = load_bgr(fixtures / f"{name}.png")
            for size in ((1920, 1080), (1280, 720), (1191, 669)):
                with self.subTest(state=name, size=size):
                    current = cv2.resize(frame, size)
                    trader = create_trader()
                    trader.task = CookingTask()
                    trader.vision = Vision(trader.task)
                    detail = CookingDetailSnapshot(
                        current, MatchResult(1, (0, 0), (1, 1)), True, 0.7
                    )
                    trader._cooking_detail_snapshot = lambda _: detail
                    self.assertEqual(expected, trader._wait_for_max_detail("recipe", 0) is detail)

    def test_recipe_selection_recovers_once_and_never_submits_wrong_detail(self):
        from src.interaction.BD2Interaction import InputTargetChanged

        for scenario in ("list", "wrong", "stuck", "late", "input"):
            with self.subTest(scenario=scenario):
                recipe = "巧克力鸡尾酒"
                frame = np.full((1080, 1920, 3), 145, dtype=np.uint8)
                card = MatchResult(0.99, (1000, 600), (100, 100), pixel_score=0.99)
                start = MatchResult(0.99, (1100, 970), (400, 60), pixel_score=0.99)
                detail = CookingDetailSnapshot(frame, start, True, 0.7)
                trader = create_trader()
                trader.task = CookingTask()
                clicks = []
                interrupted = []
                page = {"kind": "list"}

                def click(point, *_args, **_kwargs):
                    if scenario == "input" and not interrupted:
                        interrupted.append(True)
                        raise InputTargetChanged("mouse wait")
                    clicks.append(point)
                    if point == card.center:
                        page["kind"] = "wrong" if scenario in {"wrong", "stuck"} else "list"

                trader.vision = SimpleNamespace(
                    capture=lambda: frame,
                    match=lambda *_: card,
                    passes=lambda *_: True,
                    click_client=click,
                )
                trader._wait_for_cooking_list = lambda _: (
                    CookingListSnapshot(frame, card) if page["kind"] == "list" else None
                )
                trader._wait_for_cooking_detail = lambda *_: (
                    detail if len(clicks) >= 2 and scenario != "stuck" else None
                )
                trader._cooking_detail_snapshot = lambda *_: detail if scenario == "late" else None
                trader._cooking_controls_snapshot = lambda: (
                    detail if page["kind"] == "wrong" else None
                )
                trader._click_cooking_back = Mock(
                    side_effect=lambda *_args, **_kwargs: page.update(kind="list"),
                )
                trader._select_max_cooking_quantity = Mock(return_value=detail)
                trader._wait_for_cooking_started = lambda *_: True
                trader._wait_for_cooking_result = lambda *_: frame
                trader._return_from_detail_to_list = lambda *_: True
                trader._recover_cooking_list = Mock(wraps=trader._recover_cooking_list)
                result = trader._cook_one_recipe(recipe)
                self.assertEqual(scenario != "stuck", result is CookingRecipeOutcome.COOKED)
                self.assertEqual(int(scenario != "stuck"), clicks.count(start.center))
                self.assertEqual(1 if scenario == "late" else 2, clicks.count(card.center))
                self.assertEqual(
                    0 if scenario == "late" else 1, trader._recover_cooking_list.call_count
                )
                self.assertEqual(
                    int(scenario in {"wrong", "stuck"}), trader._click_cooking_back.call_count
                )
                self.assertTrue(trader.task.saved_frames)

    def test_lost_max_click_retries_and_never_starts_without_confirmation(self):
        recipe = "巧克力鸡尾酒"
        frame = np.full((1080, 1920, 3), 145, dtype=np.uint8)
        card = MatchResult(0.99, (1000, 600), (100, 100), pixel_score=0.99)
        start = MatchResult(0.99, (1100, 970), (400, 60), pixel_score=0.99)
        detail = CookingDetailSnapshot(frame, start, True, 0.7)
        for ignored, already_max, expected_clicks, expected in (
            (1, False, 2, CookingRecipeOutcome.COOKED),
            (
                COOKING_QUANTITY_ATTEMPTS,
                False,
                COOKING_QUANTITY_ATTEMPTS,
                CookingRecipeOutcome.FAILED,
            ),
            (COOKING_QUANTITY_ATTEMPTS, True, 1, CookingRecipeOutcome.COOKED),
        ):
            with self.subTest(ignored=ignored, already_max=already_max):
                self.reset_stage_history()
                state = {"max": already_max, "clicks": 0, "started": False}
                trader = create_trader()
                trader.task = CookingTask()

                def click(point, _shape, after_sleep=0.0):
                    if point == (690, 875):
                        state["clicks"] += 1
                        if state["clicks"] > ignored:
                            state["max"] = True
                    elif point == start.center:
                        state["started"] = True

                trader.vision = SimpleNamespace(
                    match=lambda *_args: card,
                    passes=lambda _match, spec: (
                        state["max"] if spec is COOKING_MAX_QUANTITY_TEMPLATE else True
                    ),
                    click_client=click,
                    simplify=lambda value: value,
                    ocr_boxes=lambda *_args, **_kwargs: [
                        SimpleNamespace(name="MAX", x=650, y=850, width=80, height=50)
                    ],
                )
                trader._wait_for_cooking_list = lambda _: CookingListSnapshot(frame, card)
                trader._wait_for_cooking_detail = lambda *_args: detail
                trader._cooking_detail_snapshot = lambda *_args: detail
                trader._wait_for_cooking_started = lambda *_args: True
                trader._wait_for_cooking_result = lambda *_args: frame
                trader._return_from_detail_to_list = lambda *_args: True
                trader._recover_cooking_list = lambda: True
                with patch("src.tasks.map_trade.trader_cooking.COOKING_QUANTITY_VERIFY_SECONDS", 0):
                    self.assertIs(expected, trader._cook_one_recipe(recipe))
                self.assertEqual(expected_clicks, state["clicks"])
                self.assertEqual(expected is CookingRecipeOutcome.COOKED, state["started"])

    def test_cooking_submission_requires_new_result_and_restores_list_before_next_batch(self):
        recipe = DEFAULT_RECIPES[0]
        frame = np.zeros((1080, 1920, 3), np.uint8)
        match = MatchResult(0.98, (1110, 975), (40, 40), pixel_score=0.96)
        detail = CookingDetailSnapshot(frame, match, True, 0.72)
        for prior in (None, "pending", "done"):
            for result_text, expected in (
                (recipe + "×10000", True),
                (recipe, False),
                ("其他料理×60", False),
            ):
                with self.subTest(prior=prior, text=result_text):
                    self.reset_stage_history()
                    trader = create_trader()
                    trader.task = CookingTask()
                    if prior is not None:
                        run_history.save_stage(
                            trader.task,
                            f"料理:{recipe}",
                            prior,
                            evidence={"recipe": recipe, "quantity": "MAX"},
                        )
                    clock = FakeClock()
                    trader.task.sleep = clock.sleep
                    trader._open_recipe_detail = Mock(return_value=detail)
                    trader._select_max_cooking_quantity = Mock(return_value=detail)
                    trader._wait_for_cooking_started = Mock(return_value=True)
                    trader._cooking_detail_snapshot = Mock(return_value=detail)
                    trader._return_from_detail_to_list = Mock(return_value=True)
                    trader._recover_cooking_list = Mock(return_value=True)
                    trader.vision.click_client = Mock()
                    trader.vision.capture = Mock(return_value=frame)
                    trader.vision.ocr_text = Mock(
                        side_effect=lambda *a, **kw: result_text if clock() >= 51 else ""
                    )
                    with patch("src.tasks.map_trade.trader_cooking.monotonic", clock):
                        for turn in (1, 2):
                            result = trader._cook_one_recipe(recipe)
                            self.assertIs(
                                result,
                                CookingRecipeOutcome.COOKED
                                if expected
                                else CookingRecipeOutcome.FAILED,
                            )
                            self.assertEqual(turn, trader.vision.click_client.call_count)
                            self.assertEqual(
                                turn * int(expected), trader._return_from_detail_to_list.call_count
                            )
                            self.assertGreaterEqual(clock(), 51)

    def test_cooking_batches_stop_at_quota_and_exit_after_partial_failure(self):
        a, b, c = DEFAULT_RECIPES[:3]
        cooked, unavailable = CookingRecipeOutcome.COOKED, CookingRecipeOutcome.UNAVAILABLE
        failed, absent = CookingRecipeOutcome.FAILED, CookingRecipeOutcome.ABSENT
        for outcomes, calls, final_card, exit_ok, expected in (
            (
                [cooked, unavailable, cooked, unavailable, unavailable],
                [a, a, b, b, c],
                None,
                True,
                True,
            ),
            ([cooked, unavailable, failed], [a, a, b], None, True, False),
            ([absent, cooked, unavailable, unavailable], [a, b, b, c], None, True, True),
            ([cooked, cooked, absent], [a, a, a], None, True, False),
            ([cooked] * 3 + [unavailable] * 2, [a, a, a, b, c], unavailable, True, True),
            ([cooked] * 3, [a, a, a], object(), True, False),
            ([unavailable] * 3, [a, b, c], None, False, False),
        ):
            with self.subTest(calls=calls, expected=expected):
                self.reset_stage_history()
                trader = create_trader()
                trader.task = CookingTask(**{"单道料理批次上限": 3})
                trader._selected_cooking_recipes = Mock(return_value=(a, b, c))
                trader._cook_one_recipe = Mock(side_effect=outcomes)
                trader._select_recipe_card = Mock(return_value=final_card)

                def enter():
                    trader._cooking_opened = True
                    return True

                trader._enter_cooking_list = enter
                trader._leave_cooking_to_q_sp6 = Mock(return_value=exit_ok)
                trader.progress.cooking_recipe_complete = Mock(return_value=False)
                trader.progress.mark_cooking_recipe_complete = Mock()
                self.assertIs(trader.run_cooking(), expected)
                self.assertEqual([call(r) for r in calls], trader._cook_one_recipe.call_args_list)
                probe = [call(a, inspect_only=True)] if outcomes[:3] == [cooked] * 3 else []
                self.assertEqual(probe, trader._select_recipe_card.call_args_list)
                trader._leave_cooking_to_q_sp6.assert_called_once()
                trader.progress.mark_cooking_recipe_complete.assert_not_called()
