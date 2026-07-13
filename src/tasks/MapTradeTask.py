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
                if not bool(self.config.get(config_key, True)):
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
        "价表来源",
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
        self.description = "每日执行 Q_sp6 商店进货与最高价出售；利润料理按配置周期制作。"
        self.icon = FluentIcon.SHOPPING_CART
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True

        self.default_config.update(
            {
                "启用": True,
                "执行跑商": True,
                "制作利润料理": True,
                "低价进货": True,
                "最高价出售": True,
                "收藏重建周期": "每周",
                "使用在线价表": True,
                "自定义最高价表": _empty_manual_calendar(),
                "出售保险": True,
                "出售白名单": "，".join(DEFAULT_SALE_WHITELIST),
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
                "执行跑商": "前往 Q_sp6 商人，砍价、低价进货并按最高价白名单出售。",
                "制作利润料理": "制作选中的五种 5 星利润料理。",
                "低价进货": "每周对齐一次收藏星标，并购买全部收藏材料。",
                "最高价出售": "仅出售当天价表和出售白名单的交集。",
                "收藏重建周期": "每周只重建一次收藏；选择每次可强制重新核对。",
                "使用在线价表": "先检查作者维护的价表，失败时回退缓存和随包快照。",
                "自定义最高价表": (
                    "关闭在线价表后使用。必须包含 1-31 日，每行格式："
                    "日期=物品@商店,物品@商店；空日期写成 29=。"
                ),
                "出售保险": "开启时每种目标只卖 1 件；关闭时选择 MAX。",
                "出售白名单": (
                    "用逗号、分号或换行追加物品；五种已选利润料理始终纳入出售白名单。"
                ),
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
                "执行跑商": {
                    "sub_configs": {
                        True: [
                            "低价进货",
                            "最高价出售",
                            "收藏重建周期",
                            "使用在线价表",
                            "出售保险",
                            "出售白名单",
                        ]
                    }
                },
                "收藏重建周期": {
                    "type": "drop_down",
                    "options": ["每周", "每次"],
                },
                "使用在线价表": {"sub_configs": {False: ["自定义最高价表"]}},
                "自定义最高价表": {"type": "text_edit"},
                "制作利润料理": {
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
        _migrate_collection_config(legacy)
        super().load_config()
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
        elif key == "使用在线价表" and value is False:
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
                ("利润料理", "制作利润料理", trader.run_cooking),
                ("跑商", "执行跑商", trader.run_trade),
            ),
        )
