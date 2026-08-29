"""Quick-hunt configuration and feature mixins for daily-style tasks."""

from __future__ import annotations

import re
from dataclasses import replace
from time import monotonic

import cv2
import numpy as np

from src.tasks.map_trade.models import TemplateSpec
from src.tasks.task_vision_mixin import (
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    TaskVisionMixin,
)
from src.utils.home_confirmation import HOME_LEFT_COLUMN_REQUIRED_HITS

QUICK_HUNT_CHILD_CONFIG_KEYS = (
    "快速狩猎冒险航线",
    "快速狩猎狩猎场",
    "快速狩猎圣石洞穴",
    "快速狩猎双倍策略",
    "快速狩猎资源倾向",
    "快速狩猎米饭分配",
    "快速狩猎模板阈值",
    "快速狩猎像素相似度阈值",
    "快速狩猎界面等待秒数",
    "快速狩猎结算等待秒数",
    "快速狩猎入口测试",
    "快速狩猎菜单测试",
    "快速狩猎圣石测试",
    "快速狩猎完整测试",
)

QUICK_HUNT_CONFIG_KEYS = ("执行快速狩猎", *QUICK_HUNT_CHILD_CONFIG_KEYS)

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

QUICK_HUNT_RESOURCE_CAPACITIES = {"米饭": 90}

QUICK_HUNT_RESOURCE_ROI = _quick_hunt_relative_roi(1724, 80, 1602, 38)

QUICK_HUNT_BUTTON_ROI = _quick_hunt_relative_roi(1720, 1018, 1599, 963)

QUICK_HUNT_COUNT_ROI = _quick_hunt_relative_roi(1298, 826, 623, 257)

QUICK_HUNT_START_ROI = _quick_hunt_relative_roi(1136, 805, 963, 764)

QUICK_HUNT_REWARD_ROI = _quick_hunt_relative_roi(1055, 1019, 857, 965)

QUICK_HUNT_DIALOG_ROI = _quick_hunt_relative_roi(750, 630, 1200, 915)

QUICK_HUNT_MAP_SCAN_ROI = _quick_hunt_relative_roi(1528, 865, 330, 165)

QUICK_HUNT_CRYSTAL_TITLE_ROI = _quick_hunt_relative_roi(340, 452, 235, 128)

QUICK_HUNT_STONE_LIST_ROI = QUICK_HUNT_CRYSTAL_TITLE_ROI

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


class QuickHuntConfigMixin:
    include_quick_hunt_config = False

    quick_hunt_default_config = {
        '执行快速狩猎': True,
        '快速狩猎冒险航线': True,
        '快速狩猎狩猎场': True,
        '快速狩猎圣石洞穴': True,
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
        '执行快速狩猎': "按  路径消耗免费米饭和火把。",
        '快速狩猎冒险航线': "消耗米饭扫荡金币或经验冒险航线。",
        '快速狩猎狩猎场': "不切换关卡，直接消耗米饭扫荡游戏当前默认狩猎场。",
        '快速狩猎圣石洞穴': "读取五种圣石数量并扫荡当前最少的属性洞穴。",
        '快速狩猎双倍策略': "优先双倍会检测金币和经验并在都双倍时选择金币；"
        "强制双倍优先配置资源；忽视双倍固定选择金币。",
        '快速狩猎资源倾向': "强制双倍策略下优先检查金币或经验。",
        '快速狩猎米饭分配': "狩猎场 MIN 时双倍冒险航线使用 MAX；"
        "狩猎场 MAX 时跳过冒险航线。",
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
                        "快速狩猎冒险航线",
                        "快速狩猎狩猎场",
                        "快速狩猎圣石洞穴",
                        "快速狩猎双倍策略",
                        "快速狩猎资源倾向",
                        "快速狩猎米饭分配",
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
        if not self.include_quick_hunt_config:
            for key in QUICK_HUNT_CONFIG_KEYS:
                self.default_config.pop(key, None)
                self.config_description.pop(key, None)
                self.config_type.pop(key, None)


class QuickHuntFeatureMixin:
    def _queue_quick_hunt_test(self, action: str) -> None:
        labels = {
            "inspect_entry": "只读检查入口",
            "open_menu": "打开狩猎菜单",
            "inspect_menu": "只读检查菜单",
            "rice": "执行米饭流程",
            "crystal": "执行圣石洞穴",
            "home": "返回主页",
            "full": "完整快速狩猎",
        }
        label = labels.get(action)
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
        labels = {
            "inspect_entry": "只读检查入口",
            "open_menu": "打开狩猎菜单",
            "inspect_menu": "只读检查菜单",
            "rice": "执行米饭流程",
            "crystal": "执行圣石洞穴",
            "home": "返回主页",
            "full": "完整快速狩猎",
        }
        label = labels.get(action, action)
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
        """Run the  quick-hunt scheduler using PC-safe mouse input."""

        opened = self._quick_hunt_open_menu()
        if opened != "opened":
            self._status_set("快速狩猎结果", "无法进入狩猎菜单")
            return False

        success = True
        try:
            rice_ok = self._quick_hunt_run_rice_scheduler()
            success = success and rice_ok
            if rice_ok and bool(self.config.get("快速狩猎圣石洞穴", True)):
                crystal_ok = self._quick_hunt_run_crystal_cave()
                success = success and crystal_ok
        finally:
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
            (hue <= 10 or hue >= 170)
            and saturation >= 140
            and value >= 150
        )
        return is_red, (x, y), bgr, hsv

    def _quick_hunt_open_menu(self) -> str:
        self._status_set("快速狩猎当前阶段", "确认首页并打开狩猎菜单")
        if not self._wait_for_quick_hunt_home():
            self._status_set("快速狩猎入口", "未确认首页")
            return "failed"

        self._status_set("快速狩猎入口", "首页已确认")
        clicked_by_ocr = self._quick_hunt_click_ocr(
            [r"^快速狩猎$"],
            None,
            self._quick_hunt_ui_timeout(),
            name="主页快速狩猎入口",
        )
        if clicked_by_ocr:
            self._status_set("快速狩猎入口", "已点击 OCR 文字框中心")
        else:
            self.operate_click(*QUICK_HUNT_ENTRY_POINT, after_sleep=1.0)
            self._status_set("快速狩猎入口", "OCR 未命中，已点击固定入口中心")

        # User requirement:  coordinates must not be inferred or converted.
        # The menu is confirmed by full-frame OCR unless an ok-bd2 ROI is supplied.
        text, _box = self._quick_hunt_wait_ocr(
            [r"狩猎场"],
            None,
            self._quick_hunt_ui_timeout(),
            name="快速狩猎菜单确认",
        )
        if text:
            self._status_set("快速狩猎入口", "已进入")
            self._status_set("快速狩猎菜单", "狩猎场")
            return "opened"

        self._status_set("快速狩猎入口", "点击后未确认菜单")
        return "failed"

    def _quick_hunt_run_rice_scheduler(self) -> bool:
        """Run hunting ground first, then an optional MAX adventure route."""

        self._status_set("快速狩猎当前阶段", "米饭调度")
        if self._quick_hunt_resource_empty("米饭"):
            self._status_set("快速狩猎米饭", "0，跳过")
            return True

        hunting_enabled = bool(self.config.get("快速狩猎狩猎场", True))
        adventure_enabled = bool(self.config.get("快速狩猎冒险航线", True))
        hunting_mode, adventure_mode = self._quick_hunt_count_modes()

        if hunting_enabled:
            self.log_info("快速狩猎：狩猎场使用游戏当前默认关卡，不切换章节。")
            result = self._quick_hunt_execute_current_map(hunting_mode, "狩猎场")
            if result == "failed":
                return False
            if result == "depleted" or self._quick_hunt_resource_empty("米饭"):
                self._status_set("快速狩猎米饭", "已耗尽")
                return True

        adventure_selected: str | None = None
        if adventure_enabled and adventure_mode is not None:
            adventure_selected = self._quick_hunt_select_adventure_route()
            if adventure_selected:
                expected_map_pattern = QUICK_HUNT_ADVENTURE_MAP_PATTERNS[
                    adventure_selected
                ]
                result = self._quick_hunt_execute_current_map(
                    adventure_mode,
                    "冒险航线",
                    expected_map_pattern=expected_map_pattern,
                )
                if result == "wrong_map":
                    self.log_info(
                        f"快速狩猎：{adventure_selected}航线首次选择未生效，"
                        "重新 OCR 选择一次。"
                    )
                    if not self._quick_hunt_click_adventure(adventure_selected):
                        return False
                    result = self._quick_hunt_execute_current_map(
                        adventure_mode,
                        "冒险航线重试",
                        expected_map_pattern=expected_map_pattern,
                    )
                if result in {"failed", "wrong_map"}:
                    return False
                if result == "depleted" or self._quick_hunt_resource_empty("米饭"):
                    self._status_set("快速狩猎米饭", "已耗尽")
                    return True

        if not hunting_enabled and not adventure_selected:
            self.log_info("快速狩猎：狩猎场已关闭且没有可执行的冒险航线，保留米饭。")
        elif adventure_enabled and adventure_mode is None:
            self.log_info("快速狩猎：狩猎场使用 MAX，按配置跳过金币和经验航线。")
        self._status_set("快速狩猎米饭", "调度结束")
        return True

    def _quick_hunt_run_crystal_cave(self) -> bool:
        self._status_set("快速狩猎当前阶段", "圣石洞穴")
        self._click_reference(*QUICK_HUNT_CRYSTAL_POINT, after_sleep=0.8)
        text, _box = self._quick_hunt_wait_ocr(
            [r"[火水风光暗].?洞穴"],
            QUICK_HUNT_CRYSTAL_TITLE_ROI,
            self._quick_hunt_ui_timeout(),
            name="圣石洞穴确认",
        )
        if not text:
            self.log_info("快速狩猎：点击圣石洞穴后未确认属性洞穴列表。")
            return False
        if self._quick_hunt_resource_empty("火把"):
            self._status_set("快速狩猎火把", "0，跳过")
            return True

        stone_counts = self._quick_hunt_stone_counts()
        if stone_counts is None:
            return False
        element = min(QUICK_HUNT_STONE_ELEMENTS, key=stone_counts.__getitem__)
        self._status_set(
            "快速狩猎圣石数量",
            "、".join(f"{name}={stone_counts[name]}" for name in QUICK_HUNT_STONE_ELEMENTS)
            + f"；选择={element}",
        )
        clicked = self._quick_hunt_click_ocr(
            [rf"{re.escape(element)}.?之?.?洞穴"],
            QUICK_HUNT_STONE_LIST_ROI,
            self._quick_hunt_ui_timeout(),
            name=f"选择{element}属性洞穴",
        )
        if not clicked:
            return False
        result = self._quick_hunt_execute_current_map("MAX", f"{element}属性圣石")
        if result == "failed":
            return False
        self._status_set("快速狩猎火把", "已耗尽" if result == "depleted" else "完成")
        return True

    def _quick_hunt_select_adventure_route(self) -> str | None:
        preferred = str(self.config.get("快速狩猎资源倾向", "金币"))
        if preferred not in QUICK_HUNT_ADVENTURE_LABEL_PATTERNS:
            self.log_info(f"快速狩猎：不支持的冒险航线资源：{preferred}")
            return None
        strategy = str(self.config.get("快速狩猎双倍策略", "优先双倍"))
        if strategy == "忽视双倍":
            return "金币" if self._quick_hunt_click_adventure("金币") else None
        states = self._quick_hunt_double_states()
        if strategy == "优先双倍":
            selected = "金币" if states["金币"] else "经验" if states["经验"] else None
            if selected is None:
                self.log_info("快速狩猎：金币和经验均未识别到双倍，跳过冒险航线。")
                return None
            return selected if self._quick_hunt_click_adventure(selected) else None
        if strategy == "强制双倍":
            alternate = "经验" if preferred == "金币" else "金币"
            for resource in (preferred, alternate):
                if states[resource]:
                    return resource if self._quick_hunt_click_adventure(resource) else None
            self.log_info("快速狩猎：首选和备选资源均未识别到双倍，跳过冒险航线。")
            return None
        self.log_info(f"快速狩猎：不支持的双倍策略：{strategy}")
        return None

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

    def _quick_hunt_execute_current_map(
        self,
        count_mode: str,
        stage: str,
        expected_map_pattern: str | None = None,
    ) -> str:
        self._status_set("快速狩猎当前阶段", stage)
        if not self._quick_hunt_click_ocr(
            [r"快速狩猎"],
            QUICK_HUNT_BUTTON_ROI,
            self._quick_hunt_ui_timeout(),
            name=f"{stage}-快速狩猎按钮",
            require_enabled=True,
        ):
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
                cancelled = self._quick_hunt_click_ocr(
                    [r"取消"],
                    QUICK_HUNT_COUNT_ROI,
                    min(2.0, self._quick_hunt_ui_timeout()),
                    name=f"{stage}-取消错误地图",
                )
                return "wrong_map" if cancelled else "failed"
        if count_mode not in {"MIN", "MAX"}:
            self.log_info(f"快速狩猎：不支持的次数模式：{count_mode}")
            return "failed"
        if not self._quick_hunt_click_ocr(
            [rf"^{count_mode}$"],
            QUICK_HUNT_COUNT_ROI,
            self._quick_hunt_ui_timeout(),
            name=f"{stage}-{count_mode}",
        ):
            return "failed"
        if not self._quick_hunt_click_ocr(
            [r"狩猎"],
            QUICK_HUNT_START_ROI,
            self._quick_hunt_ui_timeout(),
            name=f"{stage}-开始狩猎",
            require_enabled=True,
        ):
            return "failed"
        return self._quick_hunt_wait_result(stage)

    def _quick_hunt_wait_result(self, stage: str) -> str:
        end_at = monotonic() + float(self.config.get("快速狩猎结算等待秒数", 15.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            reward_text = self._quick_hunt_ocr_text(
                frame,
                QUICK_HUNT_REWARD_ROI,
                name=f"{stage}-奖励",
            )
            normalized = self._normalize_text(reward_text)
            if ("点击" in normalized and "返回" in normalized) or (
                "画面" in normalized and "即可" in normalized
            ):
                self._click_mf_reference(1, 1, after_sleep=0.8)
                return "done"

            dialog_text = self._quick_hunt_ocr_text(
                frame,
                QUICK_HUNT_DIALOG_ROI,
                name=f"{stage}-异常",
            )
            if any(
                keyword in self._normalize_text(dialog_text)
                for keyword in ("不足", "无法", "耗尽")
            ):
                self._click_mf_reference(1, 1, after_sleep=0.5)
                return "depleted"
            self.sleep(0.5)
        self.log_info(f"快速狩猎：{stage}等待结算超时。")
        return "failed"

    def _quick_hunt_stone_counts(self) -> dict[str, int] | None:
        frame = self.capture_frame()
        boxes = self._quick_vision().ocr_boxes(
            frame,
            "圣石属性数量",
            relative_roi=QUICK_HUNT_STONE_COUNT_ROI,
        )
        values: list[tuple[float, int]] = []
        for box in boxes:
            text = str(getattr(box, "name", ""))
            digits = re.sub(r"\D", "", text)
            if not digits:
                continue
            center = self._quick_hunt_box_center(box)
            if center is None:
                continue
            values.append((center[1], int(digits)))
        values.sort(key=lambda item: item[0])
        if len(values) != len(QUICK_HUNT_STONE_ELEMENTS):
            self._status_set(
                "快速狩猎圣石数量",
                f"需要5个数字，实际识别{len(values)}个",
            )
            self.log_info(
                "快速狩猎：圣石数量区域未从上到下识别出火、水、风、光、暗5个数字。"
            )
            return None
        return {
            element: value
            for element, (_center_y, value) in zip(QUICK_HUNT_STONE_ELEMENTS, values)
        }

    def _quick_hunt_resource_empty(self, resource: str) -> bool:
        frame = self.capture_frame()
        text = self._quick_hunt_ocr_text(frame, QUICK_HUNT_RESOURCE_ROI, name=f"{resource}数量")
        normalized = self._normalize_text(text).replace("：", ":")
        expected_capacity = QUICK_HUNT_RESOURCE_CAPACITIES.get(resource)
        if expected_capacity is None:
            pattern = r"(?:^|\D)0[/：:|\-~][1-9]\d*(?:\D|$)"
        else:
            pattern = (
                rf"(?:^|\D)0[/：:|\-~]{expected_capacity}"
                rf"(?:\D|$)"
            )
        empty = re.search(pattern, normalized) is not None
        self._status_set(f"快速狩猎{resource}", text or "未识别")
        return empty

    def _quick_hunt_count_modes(self) -> tuple[str, str | None]:
        allocation = str(
            self.config.get("快速狩猎米饭分配", "狩猎场x1 / 双倍图MAX")
        )
        if allocation in {"狩猎场MAX / 跳过冒险航线", "狩猎场MAX / 双倍图x1"}:
            return "MAX", None
        return "MIN", "MAX"

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
            boxes = self._quick_vision().ocr_boxes(
                frame,
                name,
                relative_roi=QUICK_HUNT_COUNT_ROI,
            )
            text = " ".join(str(getattr(box, "name", "")) for box in boxes)
            last_text = text
            self._status_set(f"{name} OCR", text or "-")
            normalized = self._normalize_text(text)
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
        return float(np.mean(gray >= 170)) >= 0.02

    def _quick_hunt_return_home(self) -> bool:
        self._status_set("快速狩猎当前阶段", "返回主页")
        for _attempt in range(4):
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
            self._click_reference(*QUICK_HUNT_RETURN_POINT, after_sleep=2.0)
        frame = self.capture_frame()
        return self._quick_hunt_home_signals(frame)[0]

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

    def _click_mf_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / 1280)),
            max(0.0, min(1.0, y / 720)),
            after_sleep=after_sleep,
        )
