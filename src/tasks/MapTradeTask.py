from __future__ import annotations

import json
import time
from pathlib import Path

from ok import Config
from ok.util.file import get_relative_path
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.calendar import parse_manual_calendar
from src.tasks.map_trade.models import DEFAULT_RECIPES, DEFAULT_SALE_WHITELIST
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.trader import Trader
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
    if "制作料理" not in legacy and "制作利润料理" in legacy:
        migrated["制作料理"] = bool(legacy["制作利润料理"])
    return migrated


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
        skipped: list[str] = []
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
                    (completed if success else failed).append(name)
                    if not success:
                        self._save_diagnostic(f"{self.diagnostic_prefix}_{name}_failed")
                except Exception as exc:
                    failed.append(name)
                    self.log_error(f"{self.task_log_name}子流程失败：{name}。", exc)
                    self._save_diagnostic(f"{self.diagnostic_prefix}_{name}_error")
        finally:
            self.info_set("当前阶段", "返回章节主页")
            returned = navigator.return_home()
            if not returned.success:
                failed.append("返回章节主页")
                self.log_warning(returned.message)
                self._save_diagnostic(f"{self.diagnostic_prefix}_return_home_error")

        self.info_set("完成", "、".join(completed) or "-")
        self.info_set("失败", "、".join(failed) or "-")
        self.info_set("跳过", "、".join(skipped) or "-")
        if failed:
            self.info_set("状态", f"{self.task_log_name}部分流程未完成。")
            return False
        self.info_set("状态", f"{self.task_log_name}完成。")
        return True

    def _save_diagnostic(self, name: str) -> None:
        try:
            self.save_frame(name, self.capture_frame())
        except Exception as exc:
            self.log_warning(f"诊断截图保存失败：{exc}")

    def _drag_client(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        """Drag with foreground mouse input only; these tasks never send keys."""

        def action():
            import win32api
            import win32con

            interaction = getattr(self.executor, "interaction", None)
            if interaction is not None and hasattr(interaction, "force_activate"):
                interaction.force_activate()
            elif interaction is not None and hasattr(interaction, "try_activate"):
                interaction.try_activate()
            capture = getattr(interaction, "capture", None)

            def to_screen(point: tuple[int, int]) -> tuple[int, int]:
                if capture is not None and hasattr(capture, "get_abs_cords"):
                    return capture.get_abs_cords(point[0], point[1])
                return point

            start_abs = to_screen(start)
            end_abs = to_screen(end)
            steps = max(6, round(duration / 0.03))
            win32api.SetCursorPos(start_abs)
            time.sleep(0.03)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            try:
                for index in range(1, steps + 1):
                    ratio = index / steps
                    x = round(start_abs[0] + (end_abs[0] - start_abs[0]) * ratio)
                    y = round(start_abs[1] + (end_abs[1] - start_abs[1]) * ratio)
                    win32api.SetCursorPos((x, y))
                    time.sleep(duration / steps)
            finally:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(after_sleep)

    def _scroll_client(
        self,
        relative_point: tuple[float, float],
        scroll_amount: int,
        count: int = 1,
        interval: float = 0.1,
        after_sleep: float = 0.0,
    ) -> None:
        """Send foreground mouse-wheel events at a relative client point."""

        frame = self.capture_frame()
        height, width = frame.shape[:2]
        x = round(max(0.0, min(1.0, relative_point[0])) * width)
        y = round(max(0.0, min(1.0, relative_point[1])) * height)
        wheel_count = max(1, int(count))
        wheel_interval = max(0.0, float(interval))
        interaction = getattr(self.executor, "interaction", None)
        if interaction is None or not hasattr(interaction, "scroll"):
            raise RuntimeError("当前交互对象不支持鼠标滚轮")

        def action():
            import win32api

            if hasattr(interaction, "force_activate"):
                interaction.force_activate()
            elif hasattr(interaction, "try_activate"):
                interaction.try_activate()
            capture = getattr(interaction, "capture", None)
            if capture is not None and hasattr(capture, "get_abs_cords"):
                win32api.SetCursorPos(capture.get_abs_cords(x, y))
            for index in range(wheel_count):
                interaction.scroll(x, y, int(scroll_amount))
                if index + 1 < wheel_count:
                    time.sleep(wheel_interval)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(after_sleep)

class MapTradeTask(MapAutomationTaskBase):
    """Daily cooking and merchant task, separated from weekly map collection."""

    vision_threshold_key = TRADE_VISION_THRESHOLD_KEY
    ocr_threshold_key = TRADE_OCR_THRESHOLD_KEY
    task_log_name = "跑商"
    diagnostic_prefix = "map_trade"
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "导航状态",
        "剧情角标",
        "Q_sp6商店点击",
        "商品卡带页",
        "商品卡带页确认",
        "收藏重建进度",
        "购买库存日期",
        "价表来源",
        "出售价表日期",
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
        self.description = "每日按配置执行买、卖与制作料理。"
        self.icon = FluentIcon.SHOPPING_CART
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True

        self.default_config.update(
            {
                "启用": True,
                "买": True,
                "收藏重建周期": "每周",
                "卖": True,
                "使用程序默认价表": True,
                "使用在线价表": True,
                "自定义最高价表": _empty_manual_calendar(),
                "出售保险": False,
                "使用出售白名单": True,
                "出售白名单": "，".join(DEFAULT_SALE_WHITELIST),
                "使用出售黑名单": False,
                "出售黑名单": "",
                "制作料理": True,
                "料理制作周期": "每周",
                "料理保险": True,
                "5星料理": list(DEFAULT_RECIPES),
                TRADE_VISION_THRESHOLD_KEY: 0.72,
                TRADE_OCR_THRESHOLD_KEY: 0.20,
                "加载页面等待秒数": 45.0,
            }
        )
        self.config_description.update(
            {
                "买": "按每日08:00库存批次进入 Q_sp6 完成砍价，并按本地卡带与灰星坐标表重建收藏。",
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
                    "用逗号、分号或换行追加物品；五种已选利润料理始终纳入出售白名单。"
                ),
                "使用出售黑名单": (
                    "开启后排除出售黑名单中的商品；黑名单优先于出售白名单。"
                ),
                "出售黑名单": "用逗号、分号或换行填写禁止出售的商品。",
                "制作料理": "制作选中的五种 5 星利润料理。",
                "料理制作周期": "每周制作一次，或每次运行都尝试制作。",
                "料理保险": "开启时每种料理只做 1 份；关闭时选择 MAX。",
                "5星料理": "可同时选择多种利润料理。",
                TRADE_VISION_THRESHOLD_KEY: "商店、导航与料理模板的最低匹配可信度。",
                TRADE_OCR_THRESHOLD_KEY: "商店文字和按钮识别的最低可信度。",
                "加载页面等待秒数": "进入卡带或传送后等待加载完成的最长秒数。",
            }
        )
        self.config_type.update(
            {
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
                        ]
                    }
                },
                "使用出售白名单": {"sub_configs": {True: ["出售白名单"]}},
                "使用出售黑名单": {"sub_configs": {True: ["出售黑名单"]}},
                "出售黑名单": {"type": "text_edit"},
                "使用程序默认价表": {
                    "sub_configs": {False: ["使用在线价表"]}
                },
                "使用在线价表": {"sub_configs": {False: ["自定义最高价表"]}},
                "自定义最高价表": {"type": "text_edit"},
                "制作料理": {
                    "sub_configs": {True: ["料理制作周期", "料理保险", "5星料理"]}
                },
                "料理制作周期": {"type": "drop_down", "options": ["每周", "每次"]},
                "5星料理": {"type": "multi_selection", "options": list(DEFAULT_RECIPES)},
                TRADE_VISION_THRESHOLD_KEY: {"min": 0.50, "max": 0.95, "step": 0.01},
                TRADE_OCR_THRESHOLD_KEY: {"min": 0.05, "max": 0.95, "step": 0.01},
                "加载页面等待秒数": {"min": 10.0, "max": 120.0, "step": 1.0},
            }
        )

    def load_config(self):
        legacy = _read_config(_config_path(self.__class__.__name__))
        section_values = _trade_section_migration_values(legacy)
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
        progress = ProgressStore()
        progress.load()
        trader = Trader(self, vision, navigator, progress)
        return self._run_phases(
            navigator,
            (
                ("买", "买", trader.run_buy),
                ("卖", "卖", trader.run_sell),
                ("制作料理", "制作料理", trader.run_cooking),
            ),
        )
