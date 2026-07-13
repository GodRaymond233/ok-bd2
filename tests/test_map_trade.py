import ast
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks import MapCollectionTask as map_collection_task_module
from src.tasks import MapTradeTask as map_trade_task_module
from src.tasks.BaseBD2Task import green_mask_from_template
from src.tasks.map_trade.calendar import (
    PriceCalendarClient,
    parse_calendar_payload,
    parse_manual_calendar,
)
from src.tasks.map_trade.collector import Collector
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
from src.tasks.map_trade.progress import (
    UTC_PLUS_8,
    ProgressStore,
    daily_cycle_key,
    weekly_cycle_key,
)
from src.tasks.map_trade.trader import Trader
from src.tasks.map_trade.vision import Vision, parse_used_limit
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.MapTradeTask import (
    MAP_OCR_THRESHOLD_KEY,
    MAP_VISION_THRESHOLD_KEY,
    TRADE_OCR_THRESHOLD_KEY,
    TRADE_VISION_THRESHOLD_KEY,
    MapTradeTask,
    _migrate_collection_config,
)

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_CALENDAR = ROOT / "assets" / "map_trade" / "price_calendar.v1.json"


class FakeTask:
    def __init__(self):
        self.config = {"跑图跑商 OCR 阈值": 0.2}
        self.clicks = []

    def operate_click(self, x, y, after_sleep=0):
        self.clicks.append((x, y, after_sleep))

    def capture_frame(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8)

    def info_set(self, *_args):
        return None

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

    def test_configured_threshold_overrides_template_default(self):
        task = FakeTask()
        task.config["跑图跑商识图阈值"] = 0.81

        self.assertEqual(0.81, Vision(task).threshold_for(TemplateSpec("test", "unused.png", 0.7)))

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

    def test_skill_count_parser(self):
        self.assertEqual((3, 5), parse_used_limit("3 / 5"))
        self.assertEqual((10, 10), parse_used_limit("次数 10:10"))
        self.assertIsNone(parse_used_limit("11/10"))
        self.assertIsNone(parse_used_limit("次数未知"))


class CatalogAndSafetyTest(unittest.TestCase):
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

        for relative_path in templates:
            with self.subTest(template=relative_path):
                self.assertTrue((template_root / relative_path).is_file())

    def test_daily_trade_task_runs_without_weekly_collection(self):
        actions = []
        task = object.__new__(MapTradeTask)
        task.config = {
            "启用": True,
            "制作利润料理": True,
            "执行跑商": True,
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

            def run_cooking(self):
                actions.append("cooking")
                return True

            def run_trade(self):
                actions.append("trade")
                return True

        with (
            patch.object(map_trade_task_module, "Vision", lambda *_args: object()),
            patch.object(map_trade_task_module, "ProgressStore", FakeProgress),
            patch.object(map_trade_task_module, "Navigator", FakeNavigator),
            patch.object(map_trade_task_module, "Trader", FakeTrader),
        ):
            self.assertTrue(MapTradeTask.run(task))

        self.assertEqual(["cooking", "trade", "home"], actions)

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
        self.assertIn("执行跑商", trade.default_config)
        self.assertNotIn("执行地图采集", trade.default_config)
        self.assertIn("执行地图采集", collection.default_config)
        self.assertNotIn("执行跑商", collection.default_config)
        self.assertIn(TRADE_VISION_THRESHOLD_KEY, trade.default_config)
        self.assertIn(TRADE_OCR_THRESHOLD_KEY, trade.default_config)
        self.assertIn(MAP_VISION_THRESHOLD_KEY, collection.default_config)
        self.assertIn(MAP_OCR_THRESHOLD_KEY, collection.default_config)

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

    def test_manual_calendar_requires_every_day(self):
        with self.assertRaisesRegex(ValueError, "必须覆盖 1-31 日"):
            parse_manual_calendar("1=透明沙拉@S6:异教塔")

    def test_manual_calendar_rejects_unknown_shop(self):
        with self.assertRaisesRegex(ValueError, "未知商店"):
            parse_manual_calendar(self._manual("8=透明沙拉@不存在"))

    def test_online_failure_uses_valid_cache_then_bundled_snapshot(self):
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
                self.assertEqual("cache", client.load(use_online=True).source)
            cache.write_text("broken", encoding="utf-8")
            with patch.object(client, "_fetch", side_effect=OSError("offline")):
                self.assertEqual("bundled", client.load(use_online=True).source)

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
