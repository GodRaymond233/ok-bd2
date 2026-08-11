"""Map-trade calendar tests (split from test_map_trade.py)."""

import json
import tempfile
import unittest
from datetime import (
    date,
    datetime,
)
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.tasks.map_trade.calendar import (
    PURCHASE_STOCK_REFRESH_HOUR,
    SALE_PRICE_REFRESH_HOUR,
    PriceCalendarClient,
    parse_calendar_payload,
    parse_manual_calendar,
    purchase_stock_date,
    sale_price_calendar_date,
)
from src.tasks.map_trade.models import DEFAULT_SALE_WHITELIST
from src.tasks.map_trade.progress import UTC_PLUS_8
from src.tasks.map_trade.trader import Trader

ROOT = Path(__file__).resolve().parents[1]


BUNDLED_CALENDAR = ROOT / "assets" / "map_trade" / "price_calendar.v1.json"


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
