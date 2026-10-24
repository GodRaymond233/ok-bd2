"""Quick-hunt configuration and feature mixins for daily-style tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from time import monotonic
from typing import Literal

import cv2
import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks import run_history
from src.tasks.map_trade.models import TemplateSpec
from src.tasks.quick_hunt_plan import (
    LEGACY_KEYS,
    PLAN_KEY,
    RICE_PER_HUNT,
    TARGETS,
    load_book,
    select_steps,
    step_count,
)
from src.tasks.quick_hunt_vision import parse_free_pool, parse_stone_rows, read_free_switch
from src.tasks.task_vision_mixin import (
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    TaskVisionMixin,
)
from src.utils.home_confirmation import HOME_LEFT_COLUMN_REQUIRED_HITS
from src.utils.image_utils import crop_relative

QUICK_HUNT_CHILD_CONFIG_KEYS = (
    PLAN_KEY,
    "快速狩猎圣石洞穴",
    "快速狩猎圣石目标",
    "快速狩猎火把分配",
    "快速狩猎模板阈值",
    "快速狩猎像素相似度阈值",
    "快速狩猎界面等待秒数",
    "快速狩猎结算等待秒数",
    "快速狩猎入口测试",
    "快速狩猎菜单测试",
    "快速狩猎圣石测试",
    "快速狩猎完整测试",
)

QUICK_HUNT_CONFIG_KEYS = ("执行快速狩猎", *QUICK_HUNT_CHILD_CONFIG_KEYS, *LEGACY_KEYS)

def _quick_hunt_relative_roi(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> tuple[float, float, float, float]:
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return (
        left / REFERENCE_WIDTH,
        top / REFERENCE_HEIGHT,
        right / REFERENCE_WIDTH,
        bottom / REFERENCE_HEIGHT,
    )

QUICK_HUNT_ENTRY_POINT = (1756 / REFERENCE_WIDTH, 262 / REFERENCE_HEIGHT)

QUICK_HUNT_RED_POINT = (1782 / REFERENCE_WIDTH, 237 / REFERENCE_HEIGHT)

# 红点判定（OpenCV HSV，H 0-179）：红色在 H 轴两端（<=10 或 >=170），
# 饱和度与明度需同时达到下限，避免把粉/暗色 UI 元素误判为红点。
RED_DOT_HUE_WRAPAROUND_MAXIMUM = 10
RED_DOT_HUE_WRAPAROUND_MINIMUM = 170
RED_DOT_SATURATION_MINIMUM = 140
RED_DOT_VALUE_MINIMUM = 150

# 菜单按钮可用态判定：灰度 >= 170 的"亮像素"占按钮区域至少 2%。
BUTTON_ENABLED_BRIGHT_GRAY_MINIMUM = 170
BUTTON_ENABLED_BRIGHT_MIN_RATIO = 0.02

QUICK_HUNT_RESOURCE_CAPACITIES = {"米饭": 90, "火把": 60}

# Both free counters shift right when the yellow stored balance is absent.
# Cover both layouts; color filtering and fraction validation exclude reserves.
QUICK_HUNT_RESOURCE_ROI = _quick_hunt_relative_roi(1580, 38, 1800, 80)

QUICK_HUNT_BUTTON_ROI = _quick_hunt_relative_roi(1720, 1018, 1599, 963)

QUICK_HUNT_COUNT_ROI = _quick_hunt_relative_roi(1298, 826, 623, 257)

QUICK_HUNT_START_ROI = _quick_hunt_relative_roi(1136, 805, 963, 764)

QUICK_HUNT_REWARD_ROI = _quick_hunt_relative_roi(1055, 1019, 857, 965)

QUICK_HUNT_REWARD_TITLE_ROI = _quick_hunt_relative_roi(825, 285, 1100, 400)

QUICK_HUNT_MENU_FOREGROUND_ROI = _quick_hunt_relative_roi(100, 120, 260, 520)

# Reward/count overlays dim the left tabs (p95 32-58); restored menu frames are 160-166.
QUICK_HUNT_MENU_FOREGROUND_P95_MINIMUM = 120

QUICK_HUNT_DIALOG_ROI = _quick_hunt_relative_roi(750, 630, 1200, 915)

QUICK_HUNT_MAP_SCAN_ROI = _quick_hunt_relative_roi(1528, 865, 330, 165)

# A quick-hunt count dialog is only trusted after one of its own controls is
# visible.  The map list behind a swallowed click can contain a valid map name,
# so map OCR alone must never open the execution path.
QUICK_HUNT_DIALOG_CONFIRM_ATTEMPTS = 3
QUICK_HUNT_DIALOG_CONFIRM_TIMEOUT = 2.0
QUICK_HUNT_ACTION_ATTEMPTS = 3
QUICK_HUNT_RETRY_INTERVAL = 2.0

QUICK_HUNT_CRYSTAL_TITLE_ROI = _quick_hunt_relative_roi(340, 452, 235, 128)

QUICK_HUNT_STONE_LIST_ROI = QUICK_HUNT_CRYSTAL_TITLE_ROI

# 狩猎菜单左列"圣石洞穴"入口（图标+文字）区域；识别点击优先于固定参考点。
QUICK_HUNT_CRYSTAL_CLICK_ROI = _quick_hunt_relative_roi(260, 520, 100, 400)

# 实机 OCR（RPT-20260901-233554 与 20260724 录屏帧复现）稳定把末字"穴"
# 读成"空/究"（圣石洞空/圣石洞究），四字全匹配从未命中；左列该三字前缀
# 唯一，放宽到"圣石洞"。
QUICK_HUNT_CRYSTAL_ENTRY_PATTERN = r"圣石洞"

QUICK_HUNT_STONE_COUNT_ROI = _quick_hunt_relative_roi(1794, 288, 1689, 80)

QUICK_HUNT_DOUBLE_ROI = _quick_hunt_relative_roi(168, 337, 135, 205)

QUICK_HUNT_ADVENTURE_LIST_ROI = _quick_hunt_relative_roi(228, 504, 128, 116)

QUICK_HUNT_ADVENTURE_LABEL_PATTERNS = {
    "金币": r"^金币$",
    "经验": r"^史莱姆$",
}

QUICK_HUNT_ADVENTURE_MAP_PATTERNS = {
    "金币": r"哥布林遗迹",
    "经验": r"史莱姆王国",
}

QUICK_HUNT_CRYSTAL_POINT = (177, 449)

QUICK_HUNT_RETURN_POINT = (101, 55)

QUICK_HUNT_STONE_ELEMENTS = ("火", "水", "风", "光", "暗")

QUICK_HUNT_RETURN_MAP_PATTERNS = (
    ("野猪洞穴", re.compile(r"野猪洞穴")),
    ("蜥蜴人祭坛", re.compile(r"蜥.?蜴.?人.?祭坛")),
    ("守山人休息处", re.compile(r"守山人休息处")),
    ("哥布林遗迹", re.compile(r"哥布林遗迹")),
    ("史莱姆王国", re.compile(r"史莱姆王国")),
    ("属性洞穴", re.compile(r"[火水风光暗].?之?.?洞穴")),
)

QUICK_HUNT_HUNTING_MAP_PATTERN = "|".join(
    pattern.pattern for _label, pattern in QUICK_HUNT_RETURN_MAP_PATTERNS[:3]
)

QUICK_HUNT_EXECUTION_MAP_PATTERNS = QUICK_HUNT_RETURN_MAP_PATTERNS

QUICK_HUNT_LIST_COLLAPSE_TEMPLATE = TemplateSpec(
    "快速狩猎资源列表收起",
    "image/green/Battle_ListCollapseGE.png",
    threshold=0.78,
    relative_roi=_quick_hunt_relative_roi(1600, 195, 1785, 579),
    green_mask=True,
    min_pixel_score=0.72,
)

QUICK_HUNT_DOUBLE_TEMPLATE = TemplateSpec(
    "当前航线双倍",
    "Double.png",
    threshold=0.8,
    relative_roi=QUICK_HUNT_DOUBLE_ROI,
    min_pixel_score=0.72,
)


QUICK_HUNT_TEST_ACTION_LABELS = {
    "inspect_entry": "只读检查入口",
    "open_menu": "打开狩猎菜单",
    "inspect_menu": "只读检查菜单",
    "rice": "执行米饭流程",
    "crystal": "执行圣石洞穴",
    "home": "返回主页",
    "full": "完整快速狩猎",
}


@dataclass(frozen=True)
class HuntDialog:
    map_name: str
    count: int
    cost: int
    pool: int
    resource: str
    free_state: str
    switch_point: tuple[float, float] | None
    start_point: tuple[int, int]
    frame_shape: tuple[int, ...]

    @property
    def signature(self) -> tuple:
        return self.map_name, self.count, self.cost, self.pool, self.resource, self.free_state


class QuickHuntConfigMixin:
    include_quick_hunt_config = False

    quick_hunt_default_config = {
        PLAN_KEY: "",
        '执行快速狩猎': True,
        '快速狩猎冒险航线': True,
        '快速狩猎狩猎场': True,
        '快速狩猎圣石洞穴': True,
        '快速狩猎圣石目标': "数量最少的属性",
        '快速狩猎火把分配': "用完可用火把",
        '快速狩猎双倍策略': "优先双倍",
        '快速狩猎资源倾向': "金币",
        '快速狩猎米饭分配': "狩猎场x1 / 双倍图MAX",
        '快速狩猎模板阈值': 0.78,
        '快速狩猎像素相似度阈值': 0.72,
        '快速狩猎界面等待秒数': 8.0,
        '快速狩猎结算等待秒数': 15.0,
        '快速狩猎入口测试': "",
        '快速狩猎菜单测试': "",
        '快速狩猎圣石测试': "",
        '快速狩猎完整测试': "",
    }

    quick_hunt_config_description = {
        PLAN_KEY: "按双倍状态选择规则，依次执行自定义刷取步骤。",
        '执行快速狩猎': "消耗免费米饭和火把。",
        '快速狩猎冒险航线': "消耗米饭扫荡金币或经验冒险航线。",
        '快速狩猎狩猎场': "不切换关卡，直接消耗米饭扫荡游戏当前默认狩猎场。",
        '快速狩猎圣石洞穴': "读取五种圣石数量并扫荡当前最少的属性洞穴。",
        '快速狩猎圣石目标': "自动补充数量最少的属性，或指定需要的属性。",
        '快速狩猎火把分配': "圣石洞穴独立使用火把，不占用米饭预算。",
        '快速狩猎双倍策略': "优先双倍会检测金币和经验并在都双倍时选择金币；"
        "强制双倍优先配置资源；忽视双倍固定选择金币。",
        '快速狩猎资源倾向': "强制双倍策略下优先检查金币或经验。",
        '快速狩猎米饭分配': "狩猎场 MIN 时双倍冒险航线使用 MAX；"
        "当天已确认完成的 MIN 不重复执行；狩猎场 MAX 时跳过冒险航线。",
        '快速狩猎模板阈值': "快速狩猎模板匹配最低分数。",
        '快速狩猎像素相似度阈值': "快速狩猎模板还必须达到的像素相似度。",
        '快速狩猎界面等待秒数': "等待狩猎菜单、地图和按钮出现的最长时间。",
        '快速狩猎结算等待秒数': "点击狩猎后等待奖励页或资源不足提示的最长时间。",
        '快速狩猎入口测试': "只读检查不会点击；打开菜单会先确认首页，再检查并点击"
        "1920×1080参考点(1782,237)。测试前停留在首页。",
        '快速狩猎菜单测试': "只读检查不会点击；执行米饭会按当前配置实际消耗米饭。"
        "测试前需要已经打开快速狩猎菜单。",
        '快速狩猎圣石测试': "执行圣石会按当前配置实际消耗火把；返回主页仅测试返回流程。"
        "测试前需要已经打开快速狩猎菜单。",
        '快速狩猎完整测试': "从首页执行完整快速狩猎流程，会实际消耗米饭和火把。",
    }

    def _quick_hunt_type_config(self) -> dict:
        return {
            '执行快速狩猎': {
                "sub_configs": {
                    True: [
                        PLAN_KEY,
                        "快速狩猎圣石洞穴",
                        "快速狩猎圣石目标",
                        "快速狩猎火把分配",
                        "快速狩猎模板阈值",
                        "快速狩猎像素相似度阈值",
                        "快速狩猎界面等待秒数",
                        "快速狩猎结算等待秒数",
                        "快速狩猎入口测试",
                        "快速狩猎菜单测试",
                        "快速狩猎圣石测试",
                        "快速狩猎完整测试",
                    ]
                }
            },
            '快速狩猎双倍策略': {
                "type": "drop_down",
                "options": ["优先双倍", "强制双倍", "忽视双倍"],
            },
            '快速狩猎圣石目标': {
                "type": "drop_down", "options": ["数量最少的属性", *QUICK_HUNT_STONE_ELEMENTS],
            },
            '快速狩猎火把分配': {
                "type": "drop_down", "options": ["用完可用火把", "只刷一次"],
            },
            '快速狩猎资源倾向': {
                "type": "drop_down",
                "options": ["金币", "经验"],
            },
            '快速狩猎米饭分配': {
                "type": "drop_down",
                "options": ["狩猎场x1 / 双倍图MAX", "狩猎场MAX / 跳过冒险航线"],
            },
            '快速狩猎模板阈值': {"min": 0.5, "max": 0.95, "step": 0.01},
            '快速狩猎像素相似度阈值': {
                "min": 0.5,
                "max": 0.95,
                "step": 0.01,
            },
            '快速狩猎界面等待秒数': {"min": 2.0, "max": 30.0, "step": 1.0},
            '快速狩猎结算等待秒数': {"min": 5.0, "max": 60.0, "step": 1.0},
            '快速狩猎入口测试': {
                "type": "button",
                "buttons": [
                    {
                        "text": "只读检查入口",
                        "callback": lambda _checked=False: self._queue_quick_hunt_test(
                            "inspect_entry"
                        ),
                    },
                    {
                        "text": "打开狩猎菜单",
                        "callback": lambda _checked=False: self._queue_quick_hunt_test(
                            "open_menu"
                        ),
                    },
                ],
            },
            '快速狩猎菜单测试': {
                "type": "button",
                "buttons": [
                    {
                        "text": "只读检查菜单",
                        "callback": lambda _checked=False: self._queue_quick_hunt_test(
                            "inspect_menu"
                        ),
                    },
                    {
                        "text": "执行米饭(消耗)",
                        "callback": lambda _checked=False: self._queue_quick_hunt_test(
                            "rice"
                        ),
                    },
                ],
            },
            '快速狩猎圣石测试': {
                "type": "button",
                "buttons": [
                    {
                        "text": "执行圣石(消耗)",
                        "callback": lambda _checked=False: self._queue_quick_hunt_test(
                            "crystal"
                        ),
                    },
                    {
                        "text": "返回主页",
                        "callback": lambda _checked=False: self._queue_quick_hunt_test(
                            "home"
                        ),
                    },
                ],
            },
            '快速狩猎完整测试': {
                "type": "button",
                "callback": lambda _checked=False: self._queue_quick_hunt_test("full"),
                "text": "完整执行(消耗)",
            },
        }

    def _install_quick_hunt_config(self) -> None:
        if self.include_quick_hunt_config:
            self._quick_hunt_vision = None
            self._quick_hunt_test_action = None
        self.default_config.update(self.quick_hunt_default_config)
        self.config_description.update(self.quick_hunt_config_description)
        self.config_type.update(self._quick_hunt_type_config())
        for key in LEGACY_KEYS:
            self.config_type.setdefault(key, {})["hidden"] = True
        if not self.include_quick_hunt_config:
            for key in QUICK_HUNT_CONFIG_KEYS:
                self.default_config.pop(key, None)
                self.config_description.pop(key, None)
                self.config_type.pop(key, None)


class QuickHuntFeatureMixin:
    def _queue_quick_hunt_test(self, action: str) -> None:
        label = QUICK_HUNT_TEST_ACTION_LABELS.get(action)
        if label is None:
            self.log_warning(f"不支持的快速狩猎测试动作：{action}", notify=True)
            return
        if self.enabled or self.running:
            self.log_warning("已有任务正在运行，请停止后再启动快速狩猎测试。", notify=True)
            return

        self._quick_hunt_test_action = action
        try:
            self.start()
            self._status_set("快速狩猎测试状态", f"已加入队列：{label}")
        except Exception as exc:
            self._quick_hunt_test_action = None
            self.log_error(f"无法启动快速狩猎测试：{label}", exc, notify=True)

    def _run_quick_hunt_test(self, action: str) -> bool:
        label = QUICK_HUNT_TEST_ACTION_LABELS.get(action, action)
        self._status_set("当前任务", f"快速狩猎测试：{label}")
        self._status_set("快速狩猎测试状态", f"执行中：{label}")
        try:
            if action == "inspect_entry":
                success = self._quick_hunt_inspect_entry()
                detail = "识别完成"
            elif action == "open_menu":
                opened = self._quick_hunt_open_menu()
                success = opened == "opened"
                detail = {
                    "opened": "菜单已打开",
                    "failed": "入口识别或菜单确认失败",
                }.get(opened, opened)
            elif action == "inspect_menu":
                success = self._quick_hunt_inspect_menu()
                detail = "识别完成"
            elif action == "rice":
                success = self._quick_hunt_run_rice_scheduler()
                detail = "米饭流程完成" if success else "米饭流程失败"
            elif action == "crystal":
                success = self._quick_hunt_run_crystal_cave()
                detail = "圣石流程完成" if success else "圣石流程失败"
            elif action == "home":
                success = self._quick_hunt_return_home()
                detail = "已确认主页" if success else "返回主页失败"
            elif action == "full":
                success = self.run_quick_hunt()
                detail = "完整流程完成" if success else "完整流程失败"
            else:
                success = False
                detail = f"不支持的动作：{action}"

            result = "通过" if success else "失败"
            self._status_set("快速狩猎测试状态", f"{label}：{result}；{detail}")
            self.log_completion(f"快速狩猎测试结束：{label}，{result}，{detail}")
            return success
        except (TaskDisabledException, FinishedException):
            raise
        except Exception as exc:
            self._status_set("快速狩猎测试状态", f"{label}：异常")
            self.log_error(f"快速狩猎测试异常：{label}", exc, notify=True)
            return False
        finally:
            self._quick_hunt_test_action = None

    def _quick_hunt_inspect_entry(self) -> bool:
        """Inspect entry signals without moving the mouse or clicking."""

        frame = self.capture_frame()
        home_ok, left_hits, p95_brightness, gacha_text = (
            self._quick_hunt_home_signals(frame)
        )
        self._status_set(
            "快速狩猎首页按钮",
            f"左列关键词 {left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}"
            f"({'通过' if home_ok else '未通过'})",
        )
        is_red, point, bgr, hsv = self._quick_hunt_entry_red_state(frame)
        self._status_set(
            "快速狩猎红点识别",
            f"point={point}, BGR={bgr}, HSV={hsv}, {'红色' if is_red else '非红色'}",
        )
        self._status_set(
            "快速狩猎主页亮度",
            f"p95={p95_brightness:.0f}/{self._home_p95_threshold():.0f}",
        )
        self._status_set("快速狩猎主页抽抽乐 OCR", gacha_text or "-")
        return True

    def _quick_hunt_inspect_menu(self) -> bool:
        """Inspect menu OCR and templates using one frame without clicking."""

        vision = self._quick_vision()
        frame = self.capture_frame()
        ocr_regions = (
            ("快速狩猎菜单 OCR", "测试-菜单标题", None),
            ("快速狩猎资源 OCR", "测试-资源数量", QUICK_HUNT_RESOURCE_ROI),
            ("快速狩猎按钮 OCR", "测试-快速狩猎按钮", QUICK_HUNT_BUTTON_ROI),
            ("快速狩猎次数 OCR", "测试-次数选择", QUICK_HUNT_COUNT_ROI),
            ("快速狩猎开始 OCR", "测试-开始狩猎", QUICK_HUNT_START_ROI),
            ("快速狩猎奖励 OCR", "测试-奖励页面", QUICK_HUNT_REWARD_ROI),
            ("快速狩猎异常 OCR", "测试-异常弹窗", QUICK_HUNT_DIALOG_ROI),
            ("快速狩猎地图 OCR", "测试-地图范围", QUICK_HUNT_MAP_SCAN_ROI),
            ("快速狩猎圣石 OCR", "测试-圣石列表", QUICK_HUNT_STONE_LIST_ROI),
            ("快速狩猎圣石数量", "测试-圣石数量", QUICK_HUNT_STONE_COUNT_ROI),
        )
        for status_key, name, roi in ocr_regions:
            text = vision.ocr_text(frame, name, relative_roi=roi)
            self._status_set(status_key, text or "-")

        collapse = self._quick_spec(QUICK_HUNT_LIST_COLLAPSE_TEMPLATE)
        collapse_match = vision.match(frame, collapse)
        self._status_set(
            "快速狩猎收起模板",
            f"{collapse_match.score:.3f}/{collapse_match.pixel_score:.3f}"
            f"({'通过' if vision.passes(collapse_match, collapse) else '未通过'})",
        )

        self._quick_hunt_double_states(frame)
        return True

    def run_quick_hunt(self) -> bool:
        """Run the quick-hunt scheduler using PC-safe mouse input."""

        opened = self._quick_hunt_open_menu()
        if opened != "opened":
            self._status_set("快速狩猎结果", "无法进入狩猎菜单")
            return False

        success = True
        cancelled = False
        try:
            rice_ok = self._quick_hunt_run_rice_scheduler()
            success = success and rice_ok
            if rice_ok and bool(self.config.get("快速狩猎圣石洞穴", True)):
                crystal_ok = self._quick_hunt_run_crystal_cave()
                success = success and crystal_ok
        except (TaskDisabledException, FinishedException):
            cancelled = True
            raise
        except Exception:
            self._save_flow_diagnostic("quick_hunt_error")
            raise
        finally:
            if not cancelled:
                if not success:
                    self._save_flow_diagnostic("quick_hunt_failed")
                home_ok = self._quick_hunt_return_home()
                success = success and home_ok

        self._status_set("快速狩猎结果", "完成" if success else "失败")
        return success

    def _quick_hunt_home_signals(
        self,
        frame,
    ) -> tuple[bool, int, float, str]:
        return self._home_confirmation_signals(frame, "快速狩猎主页抽抽乐")

    def _wait_for_quick_hunt_home(self, interval: float = 0.35) -> bool:
        end_at = monotonic() + float(self.config.get("主页确认等待秒数", 10.0))
        last_left_hits = 0
        last_p95 = 0.0
        last_gacha_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            home_ok, last_left_hits, last_p95, last_gacha_text = (
                self._quick_hunt_home_signals(frame)
            )
            self._status_set(
                "快速狩猎首页按钮",
                f"左列关键词 {last_left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}",
            )
            self._status_set(
                "快速狩猎主页亮度",
                f"p95={last_p95:.0f}/{self._home_p95_threshold():.0f}",
            )
            self._status_set(
                "快速狩猎主页抽抽乐 OCR",
                last_gacha_text or "-",
            )
            if home_ok:
                return True
            self.clear_temporary_home_announcement_if_needed(
                left_hits=last_left_hits,
                required_left_hits=HOME_LEFT_COLUMN_REQUIRED_HITS,
                brightness=last_p95,
                brightness_threshold=self._home_p95_threshold(),
                gacha_ocr_text=last_gacha_text,
                context="快速狩猎确认主页",
            )
            self.sleep(interval)

        self.log_info(
            "快速狩猎：未同时确认左列关键词、亮度和抽抽乐文字，"
            f"left={last_left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}, "
            f"p95={last_p95:.0f}/{self._home_p95_threshold():.0f}, "
            f"ocr={last_gacha_text or '-'}。"
        )
        return False

    @staticmethod
    def _quick_hunt_entry_red_state(
        frame,
    ) -> tuple[bool, tuple[int, int], tuple[int, int, int], tuple[int, int, int]]:
        height, width = frame.shape[:2]
        x = max(0, min(width - 1, round(width * QUICK_HUNT_RED_POINT[0])))
        y = max(0, min(height - 1, round(height * QUICK_HUNT_RED_POINT[1])))
        if frame.ndim < 3 or frame.shape[2] < 3:
            return False, (x, y), (0, 0, 0), (0, 0, 0)

        bgr = tuple(int(value) for value in frame[y, x, :3])
        hsv_pixel = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0, 0]
        hsv = tuple(int(value) for value in hsv_pixel)
        hue, saturation, value = hsv
        is_red = (
            (hue <= RED_DOT_HUE_WRAPAROUND_MAXIMUM or hue >= RED_DOT_HUE_WRAPAROUND_MINIMUM)
            and saturation >= RED_DOT_SATURATION_MINIMUM
            and value >= RED_DOT_VALUE_MINIMUM
        )
        return is_red, (x, y), bgr, hsv

    def _quick_hunt_open_menu(self) -> str:
        self._status_set("快速狩猎当前阶段", "确认首页并打开狩猎菜单")
        if not self._wait_for_quick_hunt_home():
            self._status_set("快速狩猎入口", "未确认首页")
            return "failed"

        self._status_set("快速狩猎入口", "首页已确认")
        confirm_timeout = max(3.0, self._quick_hunt_ui_timeout() / 2.0)
        for attempt in range(1, 4):
            clicked_by_ocr = self._quick_hunt_click_ocr(
                [r"^快速狩猎$"],
                None,
                2.0,
                name="主页快速狩猎入口",
            )
            if clicked_by_ocr:
                self._status_set("快速狩猎入口", "已点击 OCR 文字框中心")
            else:
                self.operate_click(*QUICK_HUNT_ENTRY_POINT, after_sleep=1.0)
                self._status_set("快速狩猎入口", "OCR 未命中，已点击固定入口中心")

            text, _box = self._quick_hunt_wait_ocr(
                [r"狩猎场"],
                None,
                confirm_timeout,
                name="快速狩猎菜单确认",
            )
            if text:
                self._status_set("快速狩猎入口", "已进入")
                self._status_set("快速狩猎菜单", "狩猎场")
                return "opened"
            if attempt >= 3:
                break
            # 入口点击可能被主页动画吞掉（RPT-20260901-233554 23:32 一轮
            # 单击定胜负直接失败）；仍在主页才补点，避免在未知页面盲点。
            home_ok, *_rest = self._quick_hunt_home_signals(self.capture_frame())
            if not home_ok:
                text, _box = self._quick_hunt_wait_ocr(
                    [r"狩猎场"], None, self._quick_hunt_ui_timeout(),
                    name="快速狩猎转场确认",
                )
                if text:
                    return "opened"
                break
            self.log_info(f"快速狩猎：第{attempt}次点击入口后未确认狩猎菜单，重试。")

        self._status_set("快速狩猎入口", "点击后未确认菜单")
        return "failed"

    def _quick_hunt_run_rice_scheduler(self) -> bool:
        self._status_set("快速狩猎当前阶段", "米饭调度")
        self._quick_hunt_selected_plan_target = "hunt"
        try:
            book = load_book(self.config)
            plan = book["plans"][book["active"]]
            plan_record = run_history.read_stage(self, "狩猎方案")
            scope = run_history.stage_scope(self)
            is_same_plan = plan_record.get("scope") == scope
            if is_same_plan and plan_record.get("state") == "done":
                self._status_set("快速狩猎米饭", "本周期方案已完成")
                return True
            if not plan["fallback"] and all(not rule["steps"] for rule in plan["rules"]):
                self._status_set("快速狩猎米饭", "方案保留米饭")
                return True
            rice = self._quick_hunt_free_rice()
            if rice == 0:
                self._quick_hunt_record_exhausted("米饭")
            if rice is not None and rice - plan.get("reserve", 0) < RICE_PER_HUNT:
                self._status_set("快速狩猎米饭", "可用免费米饭不足一次，保留余量")
                return True
            if is_same_plan and plan_record.get("state") == "pending":
                evidence = plan_record["evidence"]
                matched, steps = evidence["matched"], evidence["steps"]
                allowance = evidence["allowance"]
            else:
                states = self._quick_hunt_plan_double_states() if plan["rules"] else {}
                matched, steps = select_steps(plan, states)
                allowance = max(0, rice - plan.get("reserve", 0)) if rice is not None else 0
            if not steps:
                self._status_set("快速狩猎米饭", "方案保留米饭")
                return True
            if rice is None:
                raise ValueError("无法确认免费米饭数量，停止刷取。")
            run_history.save_stage(self, "狩猎方案", "pending", scope=scope,
                                   evidence={"matched": matched, "steps": steps,
                                             "allowance": allowance})
        except ValueError as exc:
            self.log_info(f"快速狩猎：{exc}")
            return False
        self._status_set("快速狩猎当前阶段", f"{plan['name']} / {matched}")
        self.log_info(f"快速狩猎方案：{plan['name']}；命中{matched}")
        return self._quick_hunt_execute_rice_steps(plan, steps, allowance, scope)

    def _quick_hunt_execute_rice_steps(
        self, plan: dict, steps: list[dict], allowance: int, scope: str,
    ) -> bool:
        """Consume only the persisted plan allowance, honoring every completed step checkpoint."""
        # Snapshot the allowance once: newly regenerated rice is left for the next run.
        for step_index, item in enumerate(steps):
            target = item["target"]
            step_key = f"方案:{step_index}:{target}"
            mode = item.get("mode", "count")
            expected = (
                (QUICK_HUNT_HUNTING_MAP_PATTERN
                 if self._quick_hunt_selected_plan_target != "hunt" else None)
                if target == "hunt" else
                QUICK_HUNT_ADVENTURE_MAP_PATTERNS[{"gold": "金币", "exp": "经验"}[target]]
            )
            should_use_max = mode == "all" and plan.get("reserve", 0) <= 0
            if should_use_max:
                record = run_history.read_stage(self, f"狩猎提交:米饭:{step_key}:MAX")
                if record.get("state") == "done" and record.get("scope") == scope:
                    allowance = 0
                    continue
                completed = [
                    run_history.read_stage(self, f"狩猎提交:米饭:{step_key}:{ordinal}")
                    for ordinal in range(step_count(item, allowance))
                ]
                should_use_max = not any(
                    record.get("state") == "done" and record.get("scope") == scope
                    for record in completed
                )
            if should_use_max:
                current = self._quick_hunt_free_rice()
                if current is None:
                    self.log_info("快速狩猎：无法确认剩余免费米饭，停止刷取。")
                    return False
                # MAX may include rice regenerated after the plan's allowance was frozen.
                should_use_max = current <= allowance
            if should_use_max:
                if current - plan.get("reserve", 0) < RICE_PER_HUNT:
                    self._status_set("快速狩猎米饭", "已达到保留数量或不足一次")
                    return True
                result = self._quick_hunt_execute_plan_step(
                    target, "MAX", f"{TARGETS[target]} MAX", expected,
                    min(current, allowance), f"{step_key}:MAX",
                )
                if result == "done":
                    allowance = 0
                    continue
                if result != "budget_limited":
                    return result == "depleted"
            count = step_count(item, allowance)
            if not count:
                continue
            for index in range(count):
                checkpoint_name = f"{step_key}:{index}"
                record = run_history.read_stage(self, f"狩猎提交:米饭:{checkpoint_name}")
                if record.get("state") == "done" and record.get("scope") == scope:
                    allowance -= RICE_PER_HUNT
                    continue
                current = self._quick_hunt_free_rice()
                if current is None:
                    self.log_info("快速狩猎：无法确认剩余免费米饭，停止刷取。")
                    return False
                if current - plan.get("reserve", 0) < RICE_PER_HUNT:
                    self._status_set("快速狩猎米饭", "已达到保留数量或不足一次")
                    return True
                result = self._quick_hunt_execute_plan_step(
                    target, "MIN", f"{TARGETS[target]} {index + 1}/{count}", expected,
                    RICE_PER_HUNT, checkpoint_name,
                )
                if result != "done":
                    return result == "depleted"
                allowance -= RICE_PER_HUNT
        run_history.save_stage(self, "狩猎方案", "done", scope=scope)
        self._status_set("快速狩猎米饭", "方案执行结束")
        return True

    def _quick_hunt_execute_plan_step(
        self, target: str, count_mode: str, stage: str,
        expected_map_pattern: str | None, max_cost: int, checkpoint_name: str,
    ) -> str:
        """Select a plan target and retry once only after a confirmed wrong-map cancellation."""
        for attempt in range(2):
            if not self._quick_hunt_select_plan_target(target):
                return "failed"
            result = self._quick_hunt_execute_current_map(
                count_mode, f"{stage}重试" if attempt else stage,
                expected_map_pattern=expected_map_pattern,
                max_cost=max_cost,
                checkpoint_name=checkpoint_name,
            )
            if result != "wrong_map":
                return result
            self._quick_hunt_selected_plan_target = None
        return result

    def _quick_hunt_free_rice(self) -> int | None:
        return self._quick_hunt_free_resource("米饭")

    def _quick_hunt_free_resource(self, resource: str, frame=None) -> int | None:
        if frame is not None:
            crop = crop_relative(frame, QUICK_HUNT_RESOURCE_ROI)
            if crop.size == 0 or crop.ndim != 3 or crop.shape[2] < 3:
                return None
            crop = crop[:, :, :3]
            # Free balances are white; stored balances are yellow.
            white = cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV),
                                (0, 0, 170), (179, 60, 255))
            text = self._quick_hunt_ocr_text(
                cv2.bitwise_and(crop, crop, mask=white), (0, 0, 1, 1),
                name=f"免费{resource}数量",
            )
            value = parse_free_pool(text, QUICK_HUNT_RESOURCE_CAPACITIES[resource])
            if value is None:
                # Hard white masking can erase the fraction separator. Read intact
                # text boxes; never join neighbouring reserve counters or guess zero.
                boxes = self._quick_vision().ocr_boxes(
                    crop, f"免费{resource}原图回读", relative_roi=(0, 0, 1, 1),
                )
                fractions = [
                    str(box.name).strip() for box in boxes
                    if re.fullmatch(r"[0-9]+\s*[/：:|\-~]\s*[0-9]+", str(box.name).strip())
                ]
                self._status_set(f"免费{resource}原图回读", " | ".join(fractions) or "-")
                if len(fractions) == 1:
                    value = parse_free_pool(fractions[0], QUICK_HUNT_RESOURCE_CAPACITIES[resource])
            return value[0] if value is not None else None
        values = []
        for _ in range(2):
            value = self._quick_hunt_free_resource(resource, self.capture_frame())
            if value is None:
                return None
            values.append(value)
            self.sleep(0.15)
        return values[0] if values[0] == values[1] else None

    def _quick_hunt_plan_double_states(self):
        # Confirm the menu labels before treating an absent badge as non-double.
        confirmed, _ = self._quick_hunt_wait_ocr(
            [r"狩猎场"], None, self._quick_hunt_ui_timeout(), name="方案菜单确认",
        )
        if not confirmed:
            return {"金币": None, "经验": None}
        first = self._quick_hunt_double_states()
        self.sleep(0.2)
        second = self._quick_hunt_double_states()
        return {key: first[key] if first[key] == second[key] else None for key in first}

    def _quick_hunt_select_plan_target(self, target):
        if target == "hunt":
            # The quick-hunt menu opens on the current default hunting ground.
            # Preserve it until an adventure route changed the selected tab.
            if getattr(self, "_quick_hunt_selected_plan_target", "hunt") == "hunt":
                return True
            if not self._quick_hunt_click_ocr(
                [r"^狩猎场$"],
                QUICK_HUNT_ADVENTURE_LIST_ROI,
                self._quick_hunt_ui_timeout(),
                name="返回狩猎场",
            ):
                return False
            confirmed, _ = self._quick_hunt_wait_ocr(
                [QUICK_HUNT_HUNTING_MAP_PATTERN],
                QUICK_HUNT_MAP_SCAN_ROI,
                self._quick_hunt_ui_timeout(),
                name="狩猎场地图确认",
            )
            selected = bool(confirmed)
        else:
            selected = self._quick_hunt_click_adventure({"gold": "金币", "exp": "经验"}[target])
        if selected:
            self._quick_hunt_selected_plan_target = target
        return selected

    def _quick_hunt_run_crystal_cave(self) -> bool:
        self._status_set("快速狩猎当前阶段", "圣石洞穴")
        if not self._quick_hunt_enter_crystal_cave():
            return False
        torches = self._quick_hunt_free_resource("火把")
        if torches is None:
            self.log_info("快速狩猎：免费火把数量未确认，停止刷取。")
            return False
        if torches == 0:
            self._quick_hunt_record_exhausted("火把")
            self._status_set("快速狩猎火把", "0，跳过")
            return True

        element = self.config.get("快速狩猎圣石目标", "数量最少的属性")
        if element == "数量最少的属性":
            stone_counts = self._quick_hunt_stone_counts()
            if stone_counts is None:
                return False
            element = min(QUICK_HUNT_STONE_ELEMENTS, key=stone_counts.__getitem__)
            self._status_set(
                "快速狩猎圣石数量",
                "、".join(f"{name}={stone_counts[name]}" for name in QUICK_HUNT_STONE_ELEMENTS)
                + f"；选择={element}",
            )
        if element not in QUICK_HUNT_STONE_ELEMENTS:
            self.log_info("快速狩猎：不支持的圣石目标。")
            return False
        expected_map_pattern = rf"{re.escape(element)}.?之?.?洞穴"
        clicked = self._quick_hunt_click_ocr(
            [expected_map_pattern],
            QUICK_HUNT_STONE_LIST_ROI,
            self._quick_hunt_ui_timeout(),
            name=f"选择{element}属性洞穴",
        )
        if not clicked:
            return False
        mode = "MIN" if self.config.get("快速狩猎火把分配") == "只刷一次" else "MAX"
        result = self._quick_hunt_execute_current_map(
            mode, f"{element}属性圣石", expected_map_pattern=expected_map_pattern,
            resource="火把",
        )
        if result not in ("done", "depleted"):
            return False
        self._status_set("快速狩猎火把", "已耗尽" if result == "depleted" else "完成")
        return True

    def _quick_hunt_record_exhausted(self, resource: str) -> None:
        pending = run_history.default_store().pending_stages(
            run_history.stage_owner(self), f"狩猎提交:{resource}:")
        for key, record in pending.items():
            run_history.save_stage(
                self, key, "done",
                evidence={**record["evidence"], "result": "资源已耗尽"},
            )

    def _quick_hunt_enter_crystal_cave(self, attempts: int = 3) -> bool:
        """Open the crystal cave panel and confirm the attribute cave list.

        结算"点击画面即可返回"的关闭动画可能吞掉紧随其后的点击，单次盲点
        定胜负会让整个快速狩猎确定性失败；这里优先 OCR 识别左列"圣石洞穴"
        文字框点击其中心，识别不到再回退固定参考点，确认失败在有限次数内重试。
        """
        confirm_timeout = max(3.0, self._quick_hunt_ui_timeout() / 2.0)
        confirm_patterns = [r"[火水风光暗].?洞穴"]
        for attempt in range(1, max(1, attempts) + 1):
            clicked = self._quick_hunt_click_ocr(
                [QUICK_HUNT_CRYSTAL_ENTRY_PATTERN],
                QUICK_HUNT_CRYSTAL_CLICK_ROI,
                2.0,
                name="圣石洞穴入口",
            )
            if not clicked:
                # RPT-20260902-225925：实机条带 OCR 连续 20 秒全空，但 6 秒前
                # 整屏 OCR 能读到"圣石洞穴"，该客户端入口可能偏移到条带外；
                # 条带未命中先整屏 OCR 兜底（"圣石洞"三字前缀在狩猎菜单内唯一）。
                clicked = self._quick_hunt_click_ocr(
                    [QUICK_HUNT_CRYSTAL_ENTRY_PATTERN],
                    None,
                    2.0,
                    name="圣石洞穴入口整屏",
                )
            if not clicked:
                seen = self._quick_hunt_ocr_text(
                    self.capture_frame(),
                    QUICK_HUNT_CRYSTAL_CLICK_ROI,
                    name="圣石洞穴入口区域",
                )
                self._status_set("圣石洞穴入口区域 OCR", seen or "-")
                if not self._quick_hunt_menu_still_open():
                    break
                self._click_reference(*QUICK_HUNT_CRYSTAL_POINT, after_sleep=0.8)
            text, _box = self._quick_hunt_wait_ocr(
                confirm_patterns,
                QUICK_HUNT_CRYSTAL_TITLE_ROI,
                confirm_timeout,
                name="圣石洞穴确认",
            )
            if text:
                return True
            self.log_info(
                f"快速狩猎：圣石洞穴第{attempt}/{max(1, attempts)}次点击未确认"
                "属性洞穴列表"
                + ("，重试。" if attempt < max(1, attempts) else "。")
            )
        self.log_info("快速狩猎：点击圣石洞穴后未确认属性洞穴列表。")
        self._save_flow_diagnostic("quick_hunt_crystal_entry_failed")
        return False

    def _quick_hunt_click_adventure(self, resource: str) -> bool:
        pattern = QUICK_HUNT_ADVENTURE_LABEL_PATTERNS.get(resource)
        if pattern is None:
            return False
        return self._quick_hunt_click_ocr(
            [pattern],
            QUICK_HUNT_ADVENTURE_LIST_ROI,
            self._quick_hunt_ui_timeout(),
            name=f"选择{resource}航线",
        )

    def _quick_hunt_double_states(self, frame=None) -> dict[str, bool]:
        vision = self._quick_vision()
        if frame is None:
            frame = self.capture_frame()
        spec = self._quick_spec(QUICK_HUNT_DOUBLE_TEMPLATE)
        matches = vision.match_all(
            frame,
            spec,
            minimum_score=vision.threshold_for(spec),
        )
        split_y = frame.shape[0] * (
            QUICK_HUNT_DOUBLE_ROI[1] + QUICK_HUNT_DOUBLE_ROI[3]
        ) / 2
        states = {"金币": False, "经验": False}
        details = []
        for match in matches:
            resource = "金币" if match.center[1] < split_y else "经验"
            states[resource] = True
            details.append(
                f"{resource}@{match.center}={match.score:.3f}/{match.pixel_score:.3f}"
            )
        self._status_set(
            "快速狩猎双倍识别",
            f"金币={'双倍' if states['金币'] else '非双倍'}，"
            f"经验/史莱姆={'双倍' if states['经验'] else '非双倍'}；"
            + ("；".join(details) or "未命中Double.png"),
        )
        return states

    def _quick_hunt_dialog_ocr(
        self,
        frame,
        name: str,
    ) -> tuple[bool, str, str]:
        """Read controls that are unique to the quick-hunt count dialog."""

        vision = self._quick_vision()
        count_boxes = vision.ocr_boxes(
            frame,
            f"{name}-次数",
            relative_roi=QUICK_HUNT_COUNT_ROI,
        )
        count_values = [
            self._normalize_text(getattr(box, "name", ""))
            for box in count_boxes
        ]
        count_text = " ".join(
            str(getattr(box, "name", "")) for box in count_boxes
        )
        count_open = any(
            "min" in value or "max" in value
            for value in count_values
        )

        start_text = ""
        start_open = False
        if not count_open:
            start_boxes = vision.ocr_boxes(
                frame,
                f"{name}-开始",
                relative_roi=QUICK_HUNT_START_ROI,
            )
            start_values = [
                self._normalize_text(getattr(box, "name", ""))
                for box in start_boxes
            ]
            start_text = " ".join(
                str(getattr(box, "name", "")) for box in start_boxes
            )
            start_open = any("狩猎" in value for value in start_values)
        return count_open or start_open, count_text, start_text

    def _quick_hunt_wait_confirmation_dialog(self, timeout: float, name: str) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            opened, count_text, start_text = self._quick_hunt_dialog_ocr(frame, name)
            last_text = " ".join(
                part for part in (count_text, start_text) if part
            )
            self._status_set(f"{name} OCR", last_text or "-")
            if opened:
                return True
            self.sleep(0.3)
        self.log_info(
            f"快速狩猎：{name}超时，最后一次 OCR={last_text or '-'}。"
        )
        return False

    def _quick_hunt_menu_still_open(self, frame=None) -> bool:
        if frame is None:
            frame = self.capture_frame()
        text = self._quick_hunt_ocr_text(
            frame, QUICK_HUNT_MENU_FOREGROUND_ROI, name="快速狩猎菜单门禁",
        )
        crop = crop_relative(self._to_gray(frame), QUICK_HUNT_MENU_FOREGROUND_ROI)
        return bool(
            crop.size
            and np.percentile(crop, 95) >= QUICK_HUNT_MENU_FOREGROUND_P95_MINIMUM
            and all(word in self._normalize_text(text) for word in ("狩猎场", "圣石洞"))
        )

    def _quick_hunt_dialog_snapshot(self, frame, stage: str, resource: str) -> HuntDialog | None:
        boxes = self._quick_vision().ocr_boxes(
            frame, f"{stage}-提交视窗", relative_roi=QUICK_HUNT_COUNT_ROI,
        )
        text = " ".join(str(getattr(box, "name", "")) for box in boxes)
        normalized = self._normalize_text(text)
        self._status_set(f"{stage}-提交视窗 OCR", text or "-")
        if not all(word in normalized for word in ("min", "max", "取消")):
            return None
        counts = [(box, re.fullmatch(r"狩猎(\d+)次", self._normalize_text(box.name)))
                  for box in boxes]
        counts = [(box, match) for box, match in counts if match]
        # OCR may omit “用”; the switch pixels still determine the free-only state.
        labels = [box for box in boxes if re.search(
            rf"仅使用?免费{re.escape(resource)}", self._normalize_text(box.name),
        )]
        if len(counts) != 1 or len(labels) != 1:
            return None
        count_box, match = counts[0]
        label = labels[0]
        label_bottom = getattr(label, "y", 0) + getattr(label, "height", 0)
        count_top = getattr(count_box, "y", 0)
        maps = sorted(
            (box for box in boxes
             if label_bottom < getattr(box, "y", 0) + getattr(box, "height", 0) / 2 < count_top),
            key=lambda box: (box.y, box.x),
        )
        map_name = self._normalize_text(" ".join(box.name for box in maps))
        if not map_name:
            return None
        starts = []
        for box in self._quick_vision().ocr_boxes(
            frame, f"{stage}-消耗", relative_roi=QUICK_HUNT_START_ROI,
        ):
            cost = re.fullmatch(r"狩猎\D*(\d+)", self._normalize_text(box.name))
            point = self._quick_hunt_box_center(box)
            if cost and point and self._quick_hunt_box_enabled(frame, box):
                starts.append((int(cost[1]), point))
        pool = self._quick_hunt_free_resource(resource, frame)
        if len(starts) != 1 or pool is None:
            return None
        free_state, switch_point = read_free_switch(frame, label)
        return HuntDialog(map_name, int(match[1]), starts[0][0], pool, resource,
                          free_state, switch_point, starts[0][1], frame.shape)

    def _quick_hunt_ensure_free_only(self, stage: str, resource: str) -> bool:
        end_at = monotonic() + self._quick_hunt_ui_timeout()
        previous = None
        hits = clicks = 0
        saw_on = False
        next_click = 0.0
        while monotonic() <= end_at:
            snapshot = self._quick_hunt_dialog_snapshot(self.capture_frame(), stage, resource)
            state = snapshot.free_state if snapshot else "unknown"
            hits = hits + 1 if state == previous and state != "unknown" else 1
            previous = state
            self._status_set("快速狩猎免费开关", state)
            if state == "on":
                saw_on = True
                if hits >= 2:
                    return True
            elif (state == "off" and hits >= 2 and not saw_on and snapshot.switch_point
                  and clicks < 2 and monotonic() >= next_click):
                self._quick_vision().click_client(
                    snapshot.switch_point, snapshot.frame_shape, after_sleep=0.5,
                )
                clicks += 1
                hits = 0
                next_click = monotonic() + QUICK_HUNT_RETRY_INTERVAL
            self.sleep(0.3)
        self.log_info(f"快速狩猎：{stage}未能确认仅使用免费{resource}，停止提交。")
        return False

    def _quick_hunt_prepare_dialog(
        self, count_mode: str, stage: str, expected_map_pattern: str | None,
        resource: str, max_cost: int | None,
    ) -> HuntDialog | Literal["budget_limited"] | None:
        if not self._quick_hunt_ensure_free_only(stage, resource):
            return None
        # Read the price of one hunt on this map before deriving its MAX quantity.
        modes = ("MIN", "MAX") if count_mode == "MAX" else ("MIN",)
        unit_cost = None
        map_name = None
        for mode in modes:
            end_at = monotonic() + self._quick_hunt_ui_timeout()
            previous = None
            next_click = 0.0
            clicks = 0
            confirmed = None
            while monotonic() <= end_at:
                snapshot = self._quick_hunt_dialog_snapshot(self.capture_frame(), stage, resource)
                if snapshot is None or snapshot.free_state != "on":
                    previous = None
                    self.sleep(0.3)
                    continue
                if (
                    (expected_map_pattern
                     and not re.search(expected_map_pattern, snapshot.map_name))
                    or (map_name is not None and map_name != snapshot.map_name)
                ):
                    return None
                expected_count = 1 if mode == "MIN" else snapshot.pool // unit_cost
                budget = snapshot.pool if max_cost is None else min(snapshot.pool, max_cost)
                if mode == "MAX" and expected_count * unit_cost > budget:
                    self.log_info(f"快速狩猎：{stage}当前MAX超出方案余量，取消后逐次执行。")
                    return "budget_limited"
                valid = (
                    snapshot.count == expected_count and snapshot.count > 0
                    and 0 < snapshot.cost <= budget
                    and (mode != "MAX" or snapshot.cost == snapshot.count * unit_cost)
                    and (resource != "米饭" or snapshot.cost == snapshot.count * RICE_PER_HUNT)
                )
                if valid and previous == snapshot.signature:
                    confirmed = snapshot
                    break
                previous = snapshot.signature if valid else None
                if not valid and clicks < QUICK_HUNT_ACTION_ATTEMPTS and monotonic() >= next_click:
                    if not self._quick_hunt_click_ocr(
                        [rf"^{mode}$"], QUICK_HUNT_COUNT_ROI,
                        min(1.0, max(0.0, end_at - monotonic())), name=f"{stage}-{mode}",
                    ):
                        return None
                    clicks += 1
                    next_click = monotonic() + QUICK_HUNT_RETRY_INTERVAL
                self.sleep(0.3)
            if confirmed is None:
                self.log_info(f"快速狩猎：{stage}未确认{mode}次数、免费余额与消耗稳定一致。")
                return None
            unit_cost = confirmed.cost if mode == "MIN" else unit_cost
            map_name = confirmed.map_name
        self._status_set(
            "快速狩猎消耗核对", f"{confirmed.count}次；{confirmed.cost}/{confirmed.pool}{resource}",
        )
        return confirmed

    def _quick_hunt_cancel_dialog(self, stage: str, timeout: float | None = None) -> bool:
        end_at = monotonic() + (
            self._quick_hunt_ui_timeout() if timeout is None else max(0.0, timeout)
        )
        clicks = menu_hits = 0
        next_click = 0.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            if self._quick_hunt_menu_still_open(frame):
                menu_hits += 1
                if menu_hits >= 2:
                    return True
            else:
                menu_hits = 0
                opened, _count, _start = self._quick_hunt_dialog_ocr(frame, stage)
                if opened and clicks < QUICK_HUNT_ACTION_ATTEMPTS and monotonic() >= next_click:
                    boxes = self._quick_vision().ocr_boxes(
                        frame, f"{stage}-取消", relative_roi=QUICK_HUNT_COUNT_ROI,
                    )
                    cancel = [box for box in boxes if self._normalize_text(box.name) == "取消"]
                    point = self._quick_hunt_box_center(cancel[0]) if len(cancel) == 1 else None
                    if point:
                        self._quick_vision().click_client(point, frame.shape, after_sleep=0.5)
                        clicks += 1
                        next_click = monotonic() + QUICK_HUNT_RETRY_INTERVAL
            self.sleep(0.3)
        self._save_flow_diagnostic("quick_hunt_cancel_failed")
        return False

    def _quick_hunt_execute_current_map(
        self,
        count_mode: str,
        stage: str,
        expected_map_pattern: str | None = None,
        resource: str = "米饭",
        max_cost: int | None = None,
        checkpoint_name: str | None = None,
    ) -> str:
        self._status_set("快速狩猎当前阶段", stage)
        checkpoint = f"狩猎提交:{resource}:{checkpoint_name or stage.removesuffix('重试')}"
        scope = run_history.stage_scope(self)
        record = run_history.read_stage(self, checkpoint)
        pending = run_history.default_store().pending_stages(
            run_history.stage_owner(self), f"狩猎提交:{resource}:")
        if pending and checkpoint not in pending:
            self.log_info(f"快速狩猎：{resource}另一个目标存在待确认提交，不启动新的消费阶段。")
            return "failed"
        if record.get("state") == "pending":
            return self._quick_hunt_recover_submission(record, checkpoint, scope, stage, resource)
        if ((count_mode == "MIN" or checkpoint_name is not None) and record.get("state") == "done"
                and record.get("scope") == scope):
            self.log_info(f"快速狩猎：{stage}本周期 MIN 已确认，继续剩余阶段。")
            return "done"
        if not self._quick_hunt_open_confirmation(stage):
            return "failed"
        if expected_map_pattern is not None:
            map_state, map_text, actual_map = self._quick_hunt_wait_map_confirmation(
                expected_map_pattern,
                name=f"{stage}-地图确认",
            )
            if map_state != "matched":
                self.log_info(
                    f"快速狩猎：{stage}未确认目标地图 {expected_map_pattern}，"
                    f"当前={actual_map or map_text or '-'}，取消本次快速狩猎。"
                )
                self._save_flow_diagnostic("quick_hunt_wrong_map_failed")
                cancelled = self._quick_hunt_cancel_dialog(f"{stage}-取消错误地图")
                return "wrong_map" if cancelled else "failed"
        if count_mode not in {"MIN", "MAX"}:
            self.log_info(f"快速狩猎：不支持的次数模式：{count_mode}")
            return "failed"
        submission = self._quick_hunt_prepare_dialog(
            count_mode, stage, expected_map_pattern, resource, max_cost,
        )
        if submission == "budget_limited":
            return "budget_limited" if self._quick_hunt_cancel_dialog(stage) else "failed"
        if submission is None:
            self._status_set("快速狩猎提交", "未通过消耗前核验")
            self._save_flow_diagnostic("quick_hunt_confirmation_failed")
            self._quick_hunt_cancel_dialog(stage)
            return "failed"
        evidence = {"map": submission.map_name, "count": submission.count,
                    "cost": submission.cost, "pool": submission.pool, "resource": resource}
        run_history.save_stage(self, checkpoint, "pending", scope=scope, evidence=evidence)
        self._hunt_checkpoint = (checkpoint, scope, evidence)
        try:
            self._quick_vision().click_client(
                submission.start_point, submission.frame_shape, after_sleep=0.3,
            )
            return self._quick_hunt_wait_result(stage, submission=submission, resource=resource)
        finally:
            self._hunt_checkpoint = None

    def _quick_hunt_recover_submission(
        self, record: dict, checkpoint: str, scope: str, stage: str, resource: str,
    ) -> str:
        """Read pending consumption evidence without submitting another resource spend."""
        evidence = record.get("evidence", {})
        remaining = self._quick_hunt_free_resource(resource)
        if remaining == 0:
            run_history.save_stage(self, checkpoint, "done", scope=scope,
                                   evidence={**evidence, "result": "资源已耗尽"})
            return "depleted"
        if record.get("scope") == scope:
            self._hunt_checkpoint = (checkpoint, scope, evidence)
            try:
                result = self._quick_hunt_wait_result(stage, resource=resource, timeout=2.0)
                if result in {"done", "depleted"}:
                    return result
            finally:
                self._hunt_checkpoint = None
        self.log_info(f"快速狩猎：{stage}前次提交结果未知，已只读复核资源，本次不再次开始。")
        return "failed"

    def _quick_hunt_open_confirmation(self, stage: str) -> bool:
        dialog_timeout = min(
            QUICK_HUNT_DIALOG_CONFIRM_TIMEOUT,
            max(0.5, self._quick_hunt_ui_timeout()),
        )
        for attempt in range(1, QUICK_HUNT_DIALOG_CONFIRM_ATTEMPTS + 1):
            if not self._quick_hunt_click_ocr(
                [r"快速狩猎"], QUICK_HUNT_BUTTON_ROI, self._quick_hunt_ui_timeout(),
                name=f"{stage}-快速狩猎按钮", require_enabled=True,
            ):
                return False
            if self._quick_hunt_wait_confirmation_dialog(
                dialog_timeout, name=f"{stage}-次数弹窗确认",
            ):
                return True
            if (attempt >= QUICK_HUNT_DIALOG_CONFIRM_ATTEMPTS
                    or not self._quick_hunt_menu_still_open()):
                break
            self.log_info(
                f"快速狩猎：{stage}点击后未确认次数弹窗，"
                f"仍在菜单，补点（{attempt}/{QUICK_HUNT_DIALOG_CONFIRM_ATTEMPTS - 1}）。"
            )
        self.log_info(f"快速狩猎：{stage}点击后未打开次数确认弹窗。")
        return False

    def _quick_hunt_settlement_signals(self, frame, stage: str, resource: str) -> tuple[bool, bool]:
        reward_text = self._quick_hunt_ocr_text(frame, QUICK_HUNT_REWARD_ROI, name=f"{stage}-奖励")
        normalized = self._normalize_text(reward_text)
        is_reward_open = ("点击" in normalized and "返回" in normalized) or (
            "画面" in normalized and "即可" in normalized
        )
        if not is_reward_open:
            title_text = self._quick_hunt_ocr_text(
                frame, QUICK_HUNT_REWARD_TITLE_ROI, name=f"{stage}-奖励标题",
            )
            is_reward_open = "reward" in self._normalize_text(title_text)
        dialog_text = self._quick_hunt_ocr_text(
            frame, QUICK_HUNT_DIALOG_ROI, name=f"{stage}-异常",
        )
        is_depleted_open = bool(re.search(
            rf"{resource}.*(?:不足|耗尽)|(?:不足|耗尽).*{resource}",
            self._normalize_text(dialog_text),
        ))
        return is_reward_open, is_depleted_open

    def _quick_hunt_is_menu_restored(self, frame, stage: str) -> bool:
        menu_text = self._quick_hunt_ocr_text(
            frame, QUICK_HUNT_MENU_FOREGROUND_ROI, name=f"{stage}-结算返回菜单",
        )
        normalized = self._normalize_text(menu_text)
        menu_crop = crop_relative(self._to_gray(frame), QUICK_HUNT_MENU_FOREGROUND_ROI)
        p95 = float(np.percentile(menu_crop, 95)) if menu_crop.size else 0.0
        self._status_set(f"{stage}-结算返回菜单亮度", f"p95={p95:.0f}")
        return (all(keyword in normalized for keyword in ("狩猎场", "圣石洞"))
                and p95 >= QUICK_HUNT_MENU_FOREGROUND_P95_MINIMUM)

    def _quick_hunt_wait_result(
        self, stage: str, submission: HuntDialog | None = None, resource: str = "米饭",
        timeout: float | None = None,
    ) -> str:
        end_at = monotonic() + (float(self.config.get("快速狩猎结算等待秒数", 15.0))
                               if timeout is None else timeout)
        pending_result = None
        close_clicks = 0
        menu_hits = 0
        last_state = "等待结算"
        start_clicks = 1 if submission else 0
        next_start = monotonic() + QUICK_HUNT_RETRY_INTERVAL
        same_dialog_hits = 0
        may_have_started = False
        while monotonic() <= end_at:
            frame = self.capture_frame()
            is_reward_open, is_depleted_open = self._quick_hunt_settlement_signals(
                frame, stage, resource,
            )
            if is_reward_open or is_depleted_open:
                if pending_result is None:
                    pending_result = "done" if is_reward_open else "depleted"
                    checkpoint = getattr(self, "_hunt_checkpoint", None)
                    if checkpoint is not None:
                        key, scope, evidence = checkpoint
                        run_history.save_stage(self, key, "done", scope=scope,
                                               evidence={**evidence, "result": pending_result})
                    # Start the close deadline once, even when settlement used its entire allowance.
                    end_at = monotonic() + self._quick_hunt_ui_timeout()
                menu_hits = 0
                last_state = "奖励页未关闭" if is_reward_open else "资源不足弹窗未关闭"
                if close_clicks < 3:
                    if is_reward_open:
                        self._status_set("快速狩猎提交", "奖励已确认，正在返回菜单")
                        self.operate_click(
                            (QUICK_HUNT_REWARD_ROI[0] + QUICK_HUNT_REWARD_ROI[2]) / 2,
                            (QUICK_HUNT_REWARD_ROI[1] + QUICK_HUNT_REWARD_ROI[3]) / 2,
                            after_sleep=0.8,
                        )
                        close_clicks += 1
                    elif self._quick_hunt_dismiss_shortage(frame):
                        close_clicks += 1
            elif pending_result is not None:
                is_menu_restored = self._quick_hunt_is_menu_restored(frame, stage)
                menu_hits = menu_hits + 1 if is_menu_restored else 0
                last_state = "菜单恢复确认中" if is_menu_restored else "转场或页面未确认"
                if menu_hits >= 2:
                    return pending_result
            elif submission is not None:
                current = self._quick_hunt_dialog_snapshot(frame, stage, resource)
                if current is None or current.signature != submission.signature:
                    # Once the original dialog changed, a late response may have spent resources.
                    may_have_started = True
                    same_dialog_hits = 0
                    last_state = "提交结果待确认，禁止再次开始"
                elif not may_have_started:
                    same_dialog_hits += 1
                    last_state = "次数弹窗仍在，尚未确认开始"
                    if (same_dialog_hits >= 2 and monotonic() >= next_start
                            and start_clicks < QUICK_HUNT_ACTION_ATTEMPTS):
                        self._quick_vision().click_client(
                            current.start_point, current.frame_shape, after_sleep=0.3,
                        )
                        start_clicks += 1
                        same_dialog_hits = 0
                        next_start = monotonic() + QUICK_HUNT_RETRY_INTERVAL
                self._status_set("快速狩猎提交", f"{last_state}；开始点击={start_clicks}/3")
            self._status_set(f"{stage}-结算退出状态", f"{last_state}；关闭点击={close_clicks}/3")
            self.sleep(0.3)
        self.log_info(f"快速狩猎：{stage}结算退出超时，{last_state}；关闭点击={close_clicks}/3。")
        self._save_flow_diagnostic(
            "quick_hunt_result_close_failed" if pending_result else "quick_hunt_start_unconfirmed"
        )
        return "failed"

    def _quick_hunt_dismiss_shortage(self, frame) -> bool:
        boxes = self._quick_vision().ocr_boxes(
            frame, "快速狩猎不足提示关闭", relative_roi=QUICK_HUNT_DIALOG_ROI,
        )
        buttons = [box for box in boxes
                   if self._normalize_text(box.name) in ("确认", "确定")]
        if not buttons:
            buttons = [box for box in boxes if self._normalize_text(box.name) == "取消"]
        point = self._quick_hunt_box_center(buttons[0]) if len(buttons) == 1 else None
        if point is None:
            return False
        self._quick_vision().click_client(point, frame.shape, after_sleep=0.5)
        return True

    def _quick_hunt_stone_counts(self) -> dict[str, int] | None:
        end_at = monotonic() + self._quick_hunt_ui_timeout()
        previous = None
        while monotonic() <= end_at:
            frame = self.capture_frame()
            values = None
            for scale in (1.0, 2.0):
                boxes = self._quick_vision().ocr_boxes(
                    frame, "圣石属性数量", relative_roi=QUICK_HUNT_STONE_COUNT_ROI,
                    ocr_scale=scale,
                )
                self._status_set("圣石属性数量 OCR", " ".join(box.name for box in boxes) or "-")
                values = parse_stone_rows(boxes)
                if values is not None:
                    break
            if values is not None and values == previous:
                return dict(zip(QUICK_HUNT_STONE_ELEMENTS, values))
            previous = values
            self.sleep(0.3)
        self.log_info("快速狩猎：未确认五行圣石数量稳定一致，请检查资源列表是否展开。")
        self._save_flow_diagnostic("quick_hunt_stone_count_failed")
        return None

    def _quick_hunt_wait_ocr(
        self,
        patterns: list[str],
        roi: tuple[float, float, float, float] | None,
        timeout: float,
        name: str,
    ) -> tuple[str, object | None]:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            boxes = self._quick_vision().ocr_boxes(frame, name, relative_roi=roi)
            text = " ".join(str(getattr(box, "name", "")) for box in boxes)
            last_text = text
            self._status_set(f"{name} OCR", text or "-")
            normalized = self._normalize_text(text)
            for box in boxes:
                value = self._normalize_text(getattr(box, "name", ""))
                if any(pattern.search(value) for pattern in compiled):
                    return text, box
            if any(pattern.search(normalized) for pattern in compiled):
                return text, None
            self.sleep(0.4)
        self.log_info(
            f"快速狩猎：{name} 超时，最后一次 OCR={last_text or '-'}。"
        )
        return "", None

    def _quick_hunt_wait_map_confirmation(
        self,
        expected_pattern: str,
        name: str,
    ) -> tuple[str, str, str | None]:
        expected = re.compile(expected_pattern, re.IGNORECASE)
        end_at = monotonic() + self._quick_hunt_ui_timeout()
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            is_dialog_open, count_text, start_text = self._quick_hunt_dialog_ocr(
                frame,
                name,
            )
            text = " ".join(
                part for part in (count_text, start_text) if part
            )
            last_text = text
            self._status_set(f"{name} OCR", text or "-")
            if not is_dialog_open:
                self._status_set(f"{name} 门禁", "未确认次数弹窗")
                self.sleep(0.4)
                continue
            normalized = self._normalize_text(count_text)
            if expected.search(normalized):
                return "matched", text, None
            actual_map = next(
                (
                    label
                    for label, pattern in QUICK_HUNT_EXECUTION_MAP_PATTERNS
                    if pattern.search(normalized)
                ),
                None,
            )
            if actual_map is not None:
                self.log_info(
                    f"快速狩猎：{name}明确识别到错误地图 {actual_map}。"
                )
                return "wrong", text, actual_map
            self.sleep(0.4)
        self.log_info(
            f"快速狩猎：{name}超时，最后一次 OCR={last_text or '-'}。"
        )
        return "timeout", last_text, None

    def _quick_hunt_click_ocr(
        self,
        patterns: list[str],
        roi: tuple[float, float, float, float] | None,
        timeout: float,
        name: str,
        require_enabled: bool = False,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        vision = self._quick_vision()
        while monotonic() <= end_at:
            frame = self.capture_frame()
            for box in vision.ocr_boxes(frame, name, relative_roi=roi):
                value = self._normalize_text(getattr(box, "name", ""))
                if not any(pattern.search(value) for pattern in compiled):
                    continue
                if require_enabled and not self._quick_hunt_box_enabled(frame, box):
                    continue
                point = self._quick_hunt_box_center(box)
                if point is None:
                    continue
                vision.click_client(point, frame.shape, after_sleep=0.8)
                return True
            self.sleep(0.4)
        self.log_info(f"快速狩猎：未找到可点击 OCR 目标：{name}")
        self._save_flow_diagnostic("quick_hunt_button_failed")
        return False

    def _quick_hunt_ocr_text(
        self,
        frame,
        roi: tuple[float, float, float, float],
        name: str,
    ) -> str:
        return self._quick_vision().ocr_text(frame, name, relative_roi=roi)

    @staticmethod
    def _quick_hunt_box_center(box) -> tuple[int, int] | None:
        values = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            return None
        x, y, width, height = (float(value) for value in values)
        return round(x + width / 2), round(y + height / 2)

    @staticmethod
    def _quick_hunt_box_enabled(frame, box) -> bool:
        values = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            return False
        x, y, width, height = (round(float(value)) for value in values)
        frame_height, frame_width = frame.shape[:2]
        left, top = max(0, x), max(0, y)
        right, bottom = min(frame_width, x + width), min(frame_height, y + height)
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            return False
        gray = TaskVisionMixin._to_gray(crop)
        bright_ratio = float(np.mean(gray >= BUTTON_ENABLED_BRIGHT_GRAY_MINIMUM))
        return bright_ratio >= BUTTON_ENABLED_BRIGHT_MIN_RATIO

    def _quick_hunt_return_home(self) -> bool:
        self._status_set("快速狩猎当前阶段", "返回主页")
        end_at = monotonic() + max(
            self._quick_hunt_ui_timeout(), float(self.config.get("主页确认等待秒数", 10.0)),
        )
        back_clicks = reward_clicks = shortage_clicks = 0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            self._status_set(
                "快速狩猎返回位置 OCR",
                self._quick_hunt_current_map_context(frame),
            )
            home_ok, left_hits, p95_brightness, gacha_text = self._quick_hunt_home_signals(frame)
            self._status_set(
                "快速狩猎首页按钮",
                f"左列关键词 {left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}",
            )
            self._status_set(
                "快速狩猎主页亮度",
                f"p95={p95_brightness:.0f}/{self._home_p95_threshold():.0f}",
            )
            self._status_set("快速狩猎主页抽抽乐 OCR", gacha_text or "-")
            if home_ok:
                return True
            if self.clear_temporary_home_announcement_if_needed(
                left_hits=left_hits,
                required_left_hits=HOME_LEFT_COLUMN_REQUIRED_HITS,
                brightness=p95_brightness,
                brightness_threshold=self._home_p95_threshold(),
                gacha_ocr_text=gacha_text,
                context="快速狩猎返回主页",
            ):
                self.sleep(0.35)
                continue
            reward = self._normalize_text(self._quick_hunt_ocr_text(
                frame, QUICK_HUNT_REWARD_ROI, name="快速狩猎退出奖励",
            ))
            title = self._normalize_text(self._quick_hunt_ocr_text(
                frame, QUICK_HUNT_REWARD_TITLE_ROI, name="快速狩猎退出奖励标题",
            ))
            dialog_text = self._normalize_text(self._quick_hunt_ocr_text(
                frame, QUICK_HUNT_DIALOG_ROI, name="快速狩猎退出提示",
            ))
            if (("点击" in reward and "返回" in reward) or "reward" in title
                    or ("画面" in reward and "即可" in reward)):
                if reward_clicks >= QUICK_HUNT_ACTION_ATTEMPTS:
                    break
                self.operate_click(
                    (QUICK_HUNT_REWARD_ROI[0] + QUICK_HUNT_REWARD_ROI[2]) / 2,
                    (QUICK_HUNT_REWARD_ROI[1] + QUICK_HUNT_REWARD_ROI[3]) / 2,
                    after_sleep=0.8,
                )
                reward_clicks += 1
            elif re.search(
                r"(?:米饭|火把).*(?:不足|耗尽)|(?:不足|耗尽).*(?:米饭|火把)", dialog_text,
            ):
                if shortage_clicks >= QUICK_HUNT_ACTION_ATTEMPTS:
                    break
                if self._quick_hunt_dismiss_shortage(frame):
                    shortage_clicks += 1
                else:
                    self.sleep(0.3)
            elif self._quick_hunt_menu_still_open(frame):
                if back_clicks >= 4:
                    break
                self._click_reference(*QUICK_HUNT_RETURN_POINT, after_sleep=2.0)
                back_clicks += 1
            elif self._quick_hunt_dialog_ocr(frame, "快速狩猎退出次数窗")[0]:
                if not self._quick_hunt_cancel_dialog(
                    "快速狩猎退出次数窗", timeout=end_at - monotonic(),
                ):
                    return False
            else:
                self.sleep(0.3)
        if self._quick_hunt_home_signals(self.capture_frame())[0]:
            return True
        self._save_flow_diagnostic("quick_hunt_return_home_failed")
        return False

    def _quick_hunt_current_map_context(self, frame) -> str:
        boxes = self._quick_vision().ocr_boxes(
            frame,
            "快速狩猎返回位置",
            relative_roi=None,
        )
        matches: list[tuple[float, float, str]] = []
        for box in boxes:
            value = self._normalize_text(getattr(box, "name", ""))
            label = next(
                (
                    name
                    for name, pattern in QUICK_HUNT_RETURN_MAP_PATTERNS
                    if pattern.search(value)
                ),
                None,
            )
            if label is None:
                continue
            point = self._quick_hunt_box_center(box)
            if point is not None:
                matches.append((point[1], point[0], label))
        if not matches:
            return "-"
        _y, _x, label = min(matches)
        return label

    def _quick_spec(self, spec: TemplateSpec) -> TemplateSpec:
        return replace(
            spec,
            min_pixel_score=float(
                self.config.get("快速狩猎像素相似度阈值", spec.min_pixel_score or 0.72)
            ),
        )

    def _quick_hunt_ui_timeout(self) -> float:
        return float(self.config.get("快速狩猎界面等待秒数", 8.0))
