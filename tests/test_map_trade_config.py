"""Map-trade config tests (split from test_map_trade.py)."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.tasks import MapCollectionTask as map_collection_task_module
from src.tasks import MapTradeTask as map_trade_task_module
from src.tasks.map_trade.models import (
    CollectionResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.progress import UTC_PLUS_8
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


class MapTradeLegacyConfigTest(unittest.TestCase):
    def test_daily_trade_runs_cooking_before_buy_and_sell(self):
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

        self.assertEqual(["cooking", "buy", "sell", "home"], actions)


class MapTradeConfigTest(unittest.TestCase):
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
        self.assertIn("料理", trade.description)
        for mapping_name in ("default_config", "config_description"):
            with self.subTest(mapping=mapping_name):
                self.assertTrue(COOKING_CONFIG_KEYS.issubset(getattr(trade, mapping_name)))
        self.assertTrue(
            {"制作料理", "料理制作周期", "5星料理"}.issubset(trade.config_type)
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

    def test_load_config_preserves_restored_cooking_keys(self):
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
            self.assertTrue(trade.config["制作料理"])
            self.assertEqual("每周", trade.config["料理制作周期"])
            self.assertTrue(trade.config["料理保险"])
            self.assertEqual([], trade.config["5星料理"])

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
