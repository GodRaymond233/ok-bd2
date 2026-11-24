from __future__ import annotations

import json
from collections import ChainMap
from pathlib import Path

from ok import Config
from ok.task.exceptions import FinishedException, TaskDisabledException
from ok.util.file import get_relative_path

from src.icons import TaskIcon as FluentIcon
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.calendar import parse_manual_calendar
from src.tasks.map_trade.models import COOKING_GROUPS, DEFAULT_SALE_WHITELIST, LEGACY_COOKING_GROUPS
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.trader import Trader
from src.tasks.map_trade.trader_constants import split_items
from src.tasks.map_trade.trader_pricing import parse_sale_reserves
from src.tasks.map_trade.vision import Vision

LEGACY_VISION_THRESHOLD_KEY = "跑图跑商识图阈值"
LEGACY_OCR_THRESHOLD_KEY = "跑图跑商 OCR 阈值"
TRADE_VISION_THRESHOLD_KEY = "跑商识图阈值"
TRADE_OCR_THRESHOLD_KEY = "跑商 OCR 阈值"
MAP_VISION_THRESHOLD_KEY = "跑图识图阈值"
MAP_OCR_THRESHOLD_KEY = "跑图 OCR 阈值"

def _empty_manual_calendar() -> str:
    return "\n".join(f"{day}=" for day in range(1, 32))


def _config_path(name: str) -> Path:
    return Path(get_relative_path(Config.config_folder, f"{name}.json"))


def _read_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _trade_section_migration_values(legacy: dict) -> dict[str, bool]:
    """Map the previous trade switches to the three top-level sections."""

    migrated: dict[str, bool] = {}
    trade_enabled = bool(legacy.get("执行跑商", True))
    if "买" not in legacy and ({"执行跑商", "低价进货"} & legacy.keys()):
        migrated["买"] = trade_enabled and bool(legacy.get("低价进货", True))
    if "卖" not in legacy and ({"执行跑商", "最高价出售"} & legacy.keys()):
        migrated["卖"] = trade_enabled and bool(legacy.get("最高价出售", True))
    return migrated


def _cooking_migration_values(legacy: dict) -> dict[str, list[str]]:
    old_keys = {"黄框料理": "5星料理", "红框料理": "料理清单", "蓝框料理": "额外料理"}
    if not (legacy.keys() & (old_keys.keys() | old_keys.values())):
        return {}
    old_defaults = {"红框料理": LEGACY_COOKING_GROUPS["红框料理"], "黄框料理": (), "蓝框料理": ()}
    first_generation = {
        group: set(split_items(legacy.get(key, old_defaults[group]) or ()))
        for group, key in old_keys.items()
    }
    customized = any(first_generation[g] != set(default) for g, default in old_defaults.items())
    selected = set()
    for group, recipes in LEGACY_COOKING_GROUPS.items():
        fallback = first_generation[group] if customized else recipes
        values = split_items(legacy[group] or ()) if group in legacy else fallback
        selected.update(set(recipes).intersection(values))
    if selected:
        selected.add("三文鱼芥末寿司")
    if all(selected.issuperset(recipes) for recipes in COOKING_GROUPS.values()):
        return {}
    return {
        group: [recipe for recipe in recipes if recipe in selected]
        for group, recipes in COOKING_GROUPS.items() if group not in legacy
    }


def _migrate_collection_config(legacy: dict) -> None:
    """Seed the new weekly card from the previous combined task config once."""

    target = _config_path("MapCollectionTask")
    if target.exists() or not legacy:
        return

    key_map = {
        "识别成功后等待秒数": "识别成功后等待秒数",
        "启用": "启用",
        "执行地图采集": "执行地图采集",
        LEGACY_VISION_THRESHOLD_KEY: MAP_VISION_THRESHOLD_KEY,
        LEGACY_OCR_THRESHOLD_KEY: MAP_OCR_THRESHOLD_KEY,
        "加载页面等待秒数": "加载页面等待秒数",
        "卡带单步重试次数": "卡带单步重试次数",
    }
    migrated = {
        new_key: legacy[old_key]
        for old_key, new_key in key_map.items()
        if old_key in legacy
    }
    if not migrated:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    temporary.replace(target)


class MapAutomationTaskBase(BaseBD2Task):
    waits_for_login = True
    """Shared mouse-only plumbing for the daily trade and weekly map cards."""

    vision_threshold_key = LEGACY_VISION_THRESHOLD_KEY
    ocr_threshold_key = LEGACY_OCR_THRESHOLD_KEY
    task_log_name = "跑图跑商"
    diagnostic_prefix = "map_trade"

    def validate_config(self, key, value):
        if key not in {self.vision_threshold_key, self.ocr_threshold_key}:
            return None
        try:
            if not 0.0 < float(value) <= 1.0:
                return "阈值必须大于 0 且不超过 1。"
        except (TypeError, ValueError):
            return "阈值必须是数字。"
        return None

    def _run_phases(self, navigator, phases) -> bool:
        completed: list[str] = []
        failed: list[str] = []
        partial_warnings: list[str] = []
        skipped: list[str] = []
        cancelled = False
        self.info_set("状态", f"{self.task_log_name}启动。")
        try:
            for name, config_key, action in phases:
                if isinstance(config_key, (tuple, list, set)):
                    enabled = any(bool(self.config.get(key, True)) for key in config_key)
                else:
                    enabled = bool(self.config.get(config_key, True))
                if not enabled:
                    skipped.append(name)
                    continue
                self.info_set("当前阶段", name)
                self.log_info(f"{self.task_log_name}：开始{name}。")
                try:
                    result = action()
                    success = bool(getattr(result, "success", result))
                    message = str(getattr(result, "message", ""))
                    if message:
                        self.log_info(f"{name}：{message}")
                    if success and getattr(result, "skipped", False):
                        skipped.append(name)
                    else:
                        (completed if success else failed).append(name)
                    if not success:
                        self._save_diagnostic(f"{self.diagnostic_prefix}_{name}_failed")
                        if getattr(result, "partial", False):
                            partial_warnings.append(message)
                            continue
                        self.log_warning(
                            f"{self.task_log_name}：{name}失败，停止后续阶段。"
                        )
                        break
                except (TaskDisabledException, FinishedException):
                    raise
                except Exception as exc:
                    failed.append(name)
                    self.log_error(f"{self.task_log_name}子流程失败：{name}。", exc)
                    self._save_diagnostic(f"{self.diagnostic_prefix}_{name}_error")
                    self.log_warning(
                        f"{self.task_log_name}：{name}异常，停止后续阶段。"
                    )
                    break
        except (TaskDisabledException, FinishedException):
            cancelled = True
            raise
        finally:
            if not cancelled:
                self.info_set("当前阶段", "返回章节主页")
                try:
                    returned = navigator.return_home()
                except (TaskDisabledException, FinishedException):
                    raise
                except Exception as exc:
                    failed.append("返回章节主页")
                    self.log_error(f"{self.task_log_name}：返回章节主页异常。", exc)
                    self._save_diagnostic(f"{self.diagnostic_prefix}_return_home_error")
                else:
                    if not returned.success:
                        failed.append("返回章节主页")
                        self.log_warning(returned.message)
                        self._save_diagnostic(f"{self.diagnostic_prefix}_return_home_error")

        self.info_set("完成", "、".join(completed) or "-")
        self.info_set("失败", "、".join(failed) or "-")
        self.info_set("跳过", "、".join(skipped) or "-")
        if failed:
            if len(partial_warnings) == len(failed):
                self.info_set("状态", f"{self.task_log_name}执行结束，存在问题。")
                self.log_warning("；".join(partial_warnings))
                return False
            self.info_set("状态", f"{self.task_log_name}部分流程未完成。")
            return False
        self.info_set("状态", f"{self.task_log_name}完成。")
        self.log_completion(f"{self.task_log_name}：所有已开启流程完成。")
        return True

    def _save_diagnostic(self, name: str) -> None:
        self._save_flow_diagnostic(name)

class MapTradeTask(MapAutomationTaskBase):
    """Daily merchant task, separated from weekly map collection."""

    vision_threshold_key = TRADE_VISION_THRESHOLD_KEY
    ocr_threshold_key = TRADE_OCR_THRESHOLD_KEY
    task_log_name = "跑商"
    diagnostic_prefix = "map_trade"
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "导航状态",
        "主页小屋按钮",
        "主页亮度",
        "主页抽抽乐 OCR",
        "剧情角标",
        "Q_sp6商店点击",
        "Q_sp6商人前确认",
        "料理状态",
        "料理进度",
        "商品卡带页",
        "商品卡带页确认",
        "收藏重建进度",
        "购买库存日期",
        "价表来源",
        "出售价表日期",
        "已出售商品",
        "未出售商品",
        "完成",
        "失败",
        "跳过",
        "Log",
        "Warning",
        "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "每日跑商"
        self.description = "每日按配置依次执行购买、制作料理和出售。"
        self.icon = FluentIcon.SHOPPING_CART
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True

        self.default_config.update(
            {
                "启用": True,
                "买": True,
                "收藏重建周期": "每周",
                "制作料理": True,
                **{group: list(recipes) for group, recipes in COOKING_GROUPS.items()},
                "单道料理批次上限": 20,
                "卖": True,
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": _empty_manual_calendar(),
                "出售保险": False,
                "使用出售白名单": True,
                "出售白名单": "，".join(DEFAULT_SALE_WHITELIST),
                "使用出售黑名单": False,
                "出售黑名单": "",
                "商品保留量": "",
                TRADE_VISION_THRESHOLD_KEY: 0.72,
                TRADE_OCR_THRESHOLD_KEY: 0.20,
                "加载页面等待秒数": 45.0,
            }
        )
        self.config_description.update(
            {
                "制作料理": "按黄、青、蓝、红四组顺序制作已选食谱，每批选择 MAX，做到食谱变灰。",
                "黄框料理（橄榄油和洋葱）": "优先制作闪闪铁板虾、透明沙拉。",
                "青框料理（吐司）": "优先制作三明治便当。",
                "蓝框料理（肉桂）": "优先制作冰镇甜点。",
                "红框料理（无冲突）": "按图五中从上到下、同排从左到右的顺序制作。",
                "单道料理批次上限": "单道料理本次最多制作的批数；达到上限后提示尚未确认材料耗尽。",
                "买": "按每日08:00库存批次砍价购买，收藏表包含31张卡带的190个商品格。",
                "收藏重建周期": (
                    "每周只重建一次收藏；每次会强制重新核对；永不则只购买当前收藏。"
                ),
                "卖": "按每日23:00刷新后的有效日期出售最高价商品。",
                "使用程序默认价表": (
                    "默认使用随程序附带、由作者确认的价表；关闭后才显示在线价表。"
                ),
                "使用在线价表": (
                    "关闭程序默认价表后使用；在线获取失败时回退本地缓存。"
                ),
                "自定义最高价表": (
                    "程序默认价表和在线价表均关闭后使用。必须包含 1-31 日，每行格式："
                    "日期=物品@商店,物品@商店；空日期写成 29=。"
                ),
                "出售保险": (
                    "开启时每种普通目标只卖最小数量；默认关闭并选择 MAX。"
                    "价表中带保留量的商品始终优先执行保留规则。"
                ),
                "使用出售白名单": (
                    "开启时只出售最高价表和出售白名单的交集；关闭时出售价表中全部允许出售的商品。"
                ),
                "出售白名单": (
                    "用逗号、分号或换行追加物品。所有已选料理均自动纳入出售白名单。"
                ),
                "使用出售黑名单": (
                    "开启后排除出售黑名单中的商品；黑名单优先于出售白名单。"
                ),
                "出售黑名单": "用逗号、分号或换行填写禁止出售的商品。",
                "商品保留量": (
                    "每行填写「物品=数量」，例如「杏仁=5000」；使用价表中的完整商品名。"
                    "填写-1时该商品不出售，留空沿用价表保留量；仍受最高价和黑白名单限制。"
                ),
                TRADE_VISION_THRESHOLD_KEY: "商店、导航与料理模板的最低匹配可信度。",
                TRADE_OCR_THRESHOLD_KEY: "商店文字和按钮识别的最低可信度。",
                "加载页面等待秒数": "进入卡带或传送后等待加载完成的最长秒数。",
            }
        )
        self.config_type.update(
            {
                "制作料理": {
                    "sub_configs": {True: [*COOKING_GROUPS, "单道料理批次上限"]},
                },
                **{
                    group: {"type": "multi_selection", "options": list(recipes)}
                    for group, recipes in COOKING_GROUPS.items()
                },
                "单道料理批次上限": {"min": 1, "max": 100, "step": 1},
                "买": {"sub_configs": {True: ["收藏重建周期"]}},
                "收藏重建周期": {
                    "type": "drop_down",
                    "options": ["每周", "每次", "永不"],
                },
                "卖": {
                    "sub_configs": {
                        True: [
                            "使用程序默认价表",
                            "出售保险",
                            "使用出售白名单",
                            "使用出售黑名单",
                            "商品保留量",
                        ]
                    }
                },
                "使用出售白名单": {"sub_configs": {True: ["出售白名单"]}},
                "使用出售黑名单": {"sub_configs": {True: ["出售黑名单"]}},
                "出售黑名单": {"type": "text_edit"},
                "商品保留量": {"type": "text_edit"},
                "使用程序默认价表": {
                    "sub_configs": {False: ["使用在线价表"]}
                },
                "使用在线价表": {"sub_configs": {False: ["自定义最高价表"]}},
                "自定义最高价表": {"type": "text_edit"},
                TRADE_VISION_THRESHOLD_KEY: {"min": 0.50, "max": 0.95, "step": 0.01},
                TRADE_OCR_THRESHOLD_KEY: {"min": 0.05, "max": 0.95, "step": 0.01},
                "加载页面等待秒数": {"min": 10.0, "max": 120.0, "step": 1.0},
            }
        )
    def load_config(self):
        legacy = _read_config(_config_path(self.__class__.__name__))
        section_values = {
            **_trade_section_migration_values(legacy),
            **_cooking_migration_values(legacy),
        }
        _migrate_collection_config(legacy)
        super().load_config()
        for key, value in section_values.items():
            self.config[key] = value
        key_map = {
            LEGACY_VISION_THRESHOLD_KEY: TRADE_VISION_THRESHOLD_KEY,
            LEGACY_OCR_THRESHOLD_KEY: TRADE_OCR_THRESHOLD_KEY,
        }
        for old_key, new_key in key_map.items():
            if new_key not in legacy and old_key in legacy:
                self.config[new_key] = legacy[old_key]
    def validate_config(self, key, value):
        if key == "商品保留量":
            try:
                parse_sale_reserves(str(value))
            except ValueError as exc:
                return str(exc)
            return None
        current_config = self.config if self.config is not None else self.default_config
        manual_text = None
        if key == "自定义最高价表":
            manual_text = str(value)
        elif (
            key == "使用程序默认价表"
            and value is False
            and not bool(current_config.get("使用在线价表", True))
        ):
            manual_text = str(current_config.get("自定义最高价表", ""))
        elif (
            key == "使用在线价表"
            and value is False
            and not bool(current_config.get("使用程序默认价表", True))
        ):
            manual_text = str(current_config.get("自定义最高价表", ""))
        if manual_text is not None:
            try:
                parse_manual_calendar(manual_text)
            except ValueError as exc:
                return str(exc)
        return super().validate_config(key, value)

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "跑商已禁用。")
            return True

        vision = Vision(self)
        navigator = Navigator(self, vision)
        with ProgressStore() as progress:
            progress.load()
            trader = Trader(self, vision, navigator, progress)
            phases = (
                ("买", "买", trader.run_buy),
                ("制作料理", "制作料理", trader.run_cooking),
                ("卖", "卖", trader.run_sell),
            )
            return self._run_phases(navigator, phases)


class BD2MapTradeSellTestTask(BaseBD2Task):
    """Run the registered daily merchant task with only its sale phase enabled."""

    status_keys = MapTradeTask.status_keys

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "跑商当天纯卖测试"
        self.description = "按每日跑商当前配置执行当天最高价出售，结束后返回主页。"
        self.icon = FluentIcon.SHOPPING_CART
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
        self.visible = True

    def run(self):
        trade_task = self.executor.get_task_by_class(MapTradeTask)
        if trade_task is None:
            self.log_warning("未找到每日跑商任务，无法启动纯卖测试。")
            return False
        original_config = trade_task.config
        self.info_set("状态", "正在执行当天纯卖，详细进度见每日跑商。")
        # Use the same task instance so pending-sale evidence also applies to this entry point.
        trade_task.config = ChainMap(
            {"启用": True, "制作料理": False, "买": False, "卖": True},
            original_config or {},
        )
        try:
            trade_task.info_clear()
            return trade_task.run()
        finally:
            trade_task.config = original_config
            for key, value in trade_task.info_snapshot().items():
                self.info_set(key, value)
