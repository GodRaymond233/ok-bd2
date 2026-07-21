import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.models import TemplateSpec
from src.tasks.map_trade.vision import Vision
from src.utils.image_utils import (
    candidate_scales,
    crop_relative,
    pixel_similarity,
    resize_mask,
    resize_template,
    to_gray,
)
from src.utils.ocr_utils import keyword_match_count, normalize_ocr_text
from src.utils.template_resolution import (
    offline_template_requires_green_mask,
    offline_template_scale,
    offline_template_search_region,
)

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "offline-train" / "train-source-screenshots"


@dataclass(frozen=True)
class DailyTemplateSpec:
    name: str
    file_name: str
    threshold_key: str
    default_threshold: float
    crop: tuple[float, float, float, float] | None = None
    green_mask: bool = False


@dataclass(frozen=True)
class DailyMatchResult:
    score: float
    pixel_score: float
    position: tuple[int, int]
    size: tuple[int, int]


class DailyTask(BaseBD2Task):
    vision_threshold_key = "快速狩猎模板阈值"
    ocr_threshold_key = "日常 OCR 阈值"

    status_keys = [
        "启用",
        "状态",
        "当前任务",
        "执行公会签到",
        "公会判断",
        "公会入口",
        "公会入口模板",
        "公会入口阈值",
        "公会签到 loading 状态",
        "公会签到_loading_appear",
        "公会签到_loading_gone",
        "guild_sign_in_early 模板",
        "guild_sign_in 模板",
        "公会签到成功",
        "公会签到成功阈值",
        "公会签到 OCR",
        "公会签到返回主页 亮度",
        "公会签到返回主页结果",
        "执行小屋签到",
        "小屋签到 loading 状态",
        "小屋签到_loading_appear",
        "小屋签到_loading_gone",
        "my_home_early",
        "my_home",
        "小屋页面检测",
        "小屋页面阈值",
        "小屋签到返回主页 亮度",
        "小屋签到返回主页结果",
        "执行一键收菜",
        "business_collect 关键字",
        "一键收菜弹窗",
        "一键收菜 OCR",
        "一键收菜返回主页 亮度",
        "一键收菜返回主页结果",
        "执行快速狩猎",
        "快速狩猎入口",
        "快速狩猎菜单",
        "快速狩猎米饭",
        "快速狩猎火把",
        "快速狩猎当前阶段",
        "快速狩猎结果",
        "加载页面阈值",
        "主页亮度比例阈值",
        "日常 OCR 阈值",
        "loading 出现等待秒数",
        "loading 消失等待秒数",
        "公会签到成功等待秒数",
        "小屋页面等待秒数",
        "一键收菜菜单等待秒数",
        "主页确认等待秒数",
        "完成",
        "失败",
        "跳过",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]
    status_key_labels = {
        "公会签到_loading_appear": "公会 loading 出现",
        "公会签到_loading_gone": "公会 loading 消失",
        "guild_sign_in_early 模板": "公会签到成功早期模板",
        "guild_sign_in 模板": "公会签到成功模板",
        "小屋签到_loading_appear": "小屋 loading 出现",
        "小屋签到_loading_gone": "小屋 loading 消失",
        "my_home_early": "小屋页面早期检测",
        "my_home": "小屋页面检测分数",
        "business_collect 关键字": "一键收菜关键字命中",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.description = "执行公会签到、小屋签到、一键收菜和快速狩猎。"
        self.icon = FluentIcon.CAR
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._templates: dict[str, np.ndarray] = {}
        self._template_masks: dict[str, np.ndarray | None] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0
        self._quick_hunt_vision: Vision | None = None
        self.default_config.update(
            {
                "启用": True,
                "执行公会签到": True,
                "执行小屋签到": True,
                "执行一键收菜": True,
                "执行快速狩猎": True,
                "快速狩猎冒险航线": True,
                "快速狩猎狩猎场": True,
                "快速狩猎圣石洞穴": True,
                "快速狩猎双倍策略": "优先双倍",
                "快速狩猎资源倾向": "金币",
                "快速狩猎米饭分配": "狩猎场x1 / 双倍图MAX",
                "快速狩猎章节图": "低练度·章节1",
                "快速狩猎圣石属性": "火",
                "快速狩猎模板阈值": 0.78,
                "快速狩猎像素相似度阈值": 0.72,
                "快速狩猎界面等待秒数": 8.0,
                "快速狩猎结算等待秒数": 15.0,
                "公会入口阈值": 0.78,
                "公会签到成功阈值": 0.76,
                "小屋页面阈值": 0.76,
                "加载页面阈值": 0.72,
                "主页亮度比例阈值": 0.75,
                "日常 OCR 阈值": 0.2,
                "loading 出现等待秒数": 6.0,
                "loading 消失等待秒数": 35.0,
                "公会签到成功等待秒数": 8.0,
                "小屋页面等待秒数": 12.0,
                "一键收菜菜单等待秒数": 8.0,
                "主页确认等待秒数": 10.0,
            }
        )
        self.config_description.update(
            {
                "执行公会签到": "从主页进入公会，领取每日签到奖励。",
                "执行小屋签到": "从主页进入小屋，确认到达后返回主页。",
                "执行一键收菜": "打开经营管理弹窗并执行一键获得。",
                "执行快速狩猎": "按 MFABD2 路径消耗免费米饭和火把。",
                "快速狩猎冒险航线": "消耗米饭扫荡金币或经验冒险航线。",
                "快速狩猎狩猎场": "消耗米饭扫荡配置的章节狩猎场。",
                "快速狩猎圣石洞穴": "消耗免费火把扫荡固定属性圣石洞穴。",
                "快速狩猎双倍策略": (
                    "优先双倍只检查首选资源；强制双倍会再检查备选资源；"
                    "忽视双倍直接扫荡首选资源。"
                ),
                "快速狩猎资源倾向": "冒险航线首选金币或经验。",
                "快速狩猎米饭分配": "控制狩猎场与双倍冒险航线分别使用一次或 MAX。",
                "快速狩猎章节图": "选择章节1、章节7或章节9狩猎场。",
                "快速狩猎圣石属性": "选择固定的火、水、风、光或暗属性。",
                "快速狩猎模板阈值": "快速狩猎模板匹配最低分数。",
                "快速狩猎像素相似度阈值": "快速狩猎模板还必须达到的像素相似度。",
                "快速狩猎界面等待秒数": "等待狩猎菜单、地图和按钮出现的最长时间。",
                "快速狩猎结算等待秒数": "点击狩猎后等待奖励页或资源不足提示的最长时间。",
            }
        )
        self.config_type.update(
            {
                "执行快速狩猎": {
                    "sub_configs": {
                        True: [
                            "快速狩猎冒险航线",
                            "快速狩猎狩猎场",
                            "快速狩猎圣石洞穴",
                            "快速狩猎双倍策略",
                            "快速狩猎资源倾向",
                            "快速狩猎米饭分配",
                            "快速狩猎章节图",
                            "快速狩猎圣石属性",
                            "快速狩猎模板阈值",
                            "快速狩猎像素相似度阈值",
                            "快速狩猎界面等待秒数",
                            "快速狩猎结算等待秒数",
                        ]
                    }
                },
                "快速狩猎双倍策略": {
                    "type": "drop_down",
                    "options": ["优先双倍", "强制双倍", "忽视双倍"],
                },
                "快速狩猎资源倾向": {
                    "type": "drop_down",
                    "options": ["金币", "经验"],
                },
                "快速狩猎米饭分配": {
                    "type": "drop_down",
                    "options": ["狩猎场x1 / 双倍图MAX", "狩猎场MAX / 双倍图x1"],
                },
                "快速狩猎章节图": {
                    "type": "drop_down",
                    "options": ["低练度·章节1", "矿石·章节7", "木材·章节9"],
                },
                "快速狩猎圣石属性": {
                    "type": "drop_down",
                    "options": ["火", "水", "风", "光", "暗"],
                },
                "快速狩猎模板阈值": {"min": 0.5, "max": 0.95, "step": 0.01},
                "快速狩猎像素相似度阈值": {
                    "min": 0.5,
                    "max": 0.95,
                    "step": 0.01,
                },
                "快速狩猎界面等待秒数": {"min": 2.0, "max": 30.0, "step": 1.0},
                "快速狩猎结算等待秒数": {"min": 5.0, "max": 60.0, "step": 1.0},
            }
        )

    def _status_set(self, key: str, value) -> None:
        try:
            self.info_set(key, value)
        except AttributeError:
            pass

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "日常任务已禁用。")
            self.log_info("日常任务已禁用。")
            return True

        self.info_set("状态", "日常任务启动。")
        steps = [
            ("公会签到", "执行公会签到", self.run_guild_sign_in),
            ("小屋签到", "执行小屋签到", self.run_my_home_sign_in),
            ("一键收菜", "执行一键收菜", self.run_business_collect),
            ("快速狩猎", "执行快速狩猎", self.run_quick_hunt),
        ]

        success = []
        failed = []
        skipped = []
        stop_remaining = False
        for name, config_key, func in steps:
            if not bool(self.config.get(config_key, True)):
                skipped.append(name)
                continue
            if stop_remaining:
                skipped.append(name)
                continue

            self.info_set("当前任务", name)
            self.log_info(f"开始日常子任务：{name}")
            try:
                if func():
                    success.append(name)
                else:
                    failed.append(name)
                    stop_remaining = True
                    self.log_info(f"{name} 未满足后续触发条件，停止剩余日常任务。")
            except Exception as exc:
                failed.append(name)
                stop_remaining = True
                self.log_error(f"日常子任务失败：{name}", exc)

        self.info_set("完成", str(success))
        self.info_set("失败", str(failed))
        self.info_set("跳过", str(skipped))
        self.info_set("状态", "日常任务结束。")
        self.log_info(
            f"日常任务结束：完成={success}, 失败={failed}, 跳过={skipped}",
            notify=True,
        )
        return not failed

    def run_guild_sign_in(self) -> bool:
        frame = self.capture_frame()
        guild, guild_spec = self._match_best(frame, GUILD_ENTRY_TEMPLATES)
        self.info_set("公会入口", f"{guild.score:.3f}")
        self.info_set("公会入口模板", guild_spec.file_name)

        guild_ready = self._passes(guild, guild_spec)
        if not guild_ready:
            self._status_set("公会判断", "未识别到公会入口")
            self._status_set("公会签到成功", "否")
            self.log_info("公会签到：未检测到公会入口模板，不点击公会按钮。")
            return False

        self._status_set("公会判断", "已识别入口，进入公会")
        self._sleep_after_recognition()
        self._click_reference(370, 155, after_sleep=0.5)
        loading_state, success_found, text = self._wait_loading_or_template_or_ocr(
            "公会签到",
            GUILD_SIGNUP_SUCCESS_TEMPLATE,
            GUILD_SUCCESS_KEYWORDS,
            name="guild_sign_in_early",
        )
        self._status_set("公会签到 loading 状态", loading_state)
        if loading_state == "stuck":
            self._status_set("公会签到成功", "否")
            return False

        if not success_found:
            if loading_state == "none":
                self.log_info("公会签到：未检测到 UI_loading_black.png，继续检测签到结果。")
            success_found, text = self._wait_for_template_or_ocr(
                GUILD_SIGNUP_SUCCESS_TEMPLATE,
                GUILD_SUCCESS_KEYWORDS,
                timeout=float(self.config.get("公会签到成功等待秒数", 8.0)),
                name="guild_sign_in",
            )
        self.info_set("公会签到 OCR", text or "-")
        self._status_set("公会签到成功", "是" if success_found else "否")
        if success_found:
            self.log_info("公会签到：检测到签到成功提示。")
            self._sleep_after_recognition()
            self._click_reference(450, 650, after_sleep=0.5)
        else:
            self.log_info("公会签到：未检测到签到成功提示，按流程返回主页。")

        self._click_reference(100, 50, after_sleep=1.0)
        home_ok = self._wait_home_brightness("公会签到返回主页")
        self._status_set("公会签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_my_home_sign_in(self) -> bool:
        self._click_reference(166, 158, after_sleep=0.5)
        loading_state, found = self._wait_loading_or_template(
            "小屋签到",
            MY_HOME_TEMPLATE,
            name="my_home_early",
        )
        self._status_set("小屋签到 loading 状态", loading_state)
        if loading_state == "stuck":
            self._status_set("小屋页面检测", "否")
            return False
        if loading_state == "loading":
            self.sleep(1.0)
        elif loading_state == "none":
            self.log_info("小屋签到：未检测到 UI_loading_black.png，继续检测 my-home.png。")

        if not found:
            found = self._wait_for_template(
                MY_HOME_TEMPLATE,
                timeout=float(self.config.get("小屋页面等待秒数", 12.0)),
                name="my_home",
            )
        self._status_set("小屋页面检测", "是" if found else "否")
        if found:
            self.log_info("小屋签到：已进入小屋页面，返回主页。")
            self._sleep_after_recognition()
            self._click_reference(100, 50, after_sleep=1.0)
        else:
            self.log_info("小屋签到：未检测到 my-home.png，不执行返回点击。")
            self._status_set("小屋签到返回主页结果", "未执行")
            return False

        home_ok = self._wait_home_brightness("小屋签到返回主页")
        self._status_set("小屋签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_business_collect(self) -> bool:
        self._click_reference(165, 260, after_sleep=1.0)
        found, text = self._wait_for_ocr_keywords(
            [
                "餐厅营业额现状",
                "鱼笼收获情况",
                "助手工作情况",
                "取消",
                "一键获得",
            ],
            timeout=float(self.config.get("一键收菜菜单等待秒数", 8.0)),
            minimum_matches=2,
            name="business_collect",
        )
        self.info_set("一键收菜 OCR", text or "-")
        self._status_set("一键收菜弹窗", "是" if found else "否")
        if not found:
            self.log_info("一键收菜：未检测到经营管理弹窗关键字，跳过点击。")
            self._status_set("一键收菜返回主页结果", "未执行")
            return False

        self.sleep(0.5)
        self._click_reference(1090, 814, after_sleep=2.0)
        self._click_reference(832, 814, after_sleep=1.0)
        self._click_reference(832, 814)
        home_ok = self._wait_home_brightness("一键收菜返回主页")
        self._status_set("一键收菜返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_quick_hunt(self) -> bool:
        """Run the MFABD2 quick-hunt scheduler using PC-safe mouse input."""

        opened = self._quick_hunt_open_menu()
        if opened == "skip":
            self._status_set("快速狩猎结果", "入口无红点，按已完成跳过")
            return True
        if opened != "opened":
            self._status_set("快速狩猎结果", "无法进入狩猎菜单")
            return False

        success = True
        try:
            if not self._quick_hunt_run_rice_scheduler():
                success = False
            elif bool(self.config.get("快速狩猎圣石洞穴", True)):
                success = self._quick_hunt_run_crystal_cave()
        finally:
            home_ok = self._quick_hunt_return_home()
            success = success and home_ok

        self._status_set("快速狩猎结果", "完成" if success else "失败")
        return success

    def _quick_hunt_open_menu(self) -> str:
        self._status_set("快速狩猎当前阶段", "确认箱庭并打开狩猎菜单")
        vision = self._quick_vision()
        frame = self.capture_frame()
        sandbox_found = False
        for original_spec in QUICK_HUNT_SANDBOX_TEMPLATES:
            spec = self._quick_spec(original_spec)
            result = vision.match(frame, spec)
            self._status_set(
                spec.name,
                f"{result.score:.3f}/{result.pixel_score:.3f}",
            )
            if vision.passes(result, spec):
                sandbox_found = True
                break
        if not sandbox_found:
            self._status_set("快速狩猎入口", "未确认箱庭")
            self.log_info("快速狩猎：未识别到箱庭图钉或奔跑按钮，不点击入口。")
            return "failed"

        timeout = self._quick_hunt_ui_timeout()
        for attempt in range(3):
            frame = self.capture_frame()
            red_dot = self._quick_hunt_red_dot(frame)
            if red_dot is None:
                self._status_set("快速狩猎入口", "未发现红点")
                return "skip" if attempt == 0 else "failed"
            vision.click_client(red_dot, frame.shape, after_sleep=1.0)
            text, _box = self._quick_hunt_wait_ocr(
                [r"狩猎场"],
                QUICK_HUNT_MENU_TITLE_ROI,
                min(timeout, 3.0),
                name=f"快速狩猎菜单确认{attempt + 1}",
            )
            if text:
                collapse = self._quick_spec(QUICK_HUNT_LIST_COLLAPSE_TEMPLATE)
                vision.click_template(collapse, timeout=0.8, after_sleep=0.5)
                self._status_set("快速狩猎入口", "已进入")
                self._status_set("快速狩猎菜单", "狩猎场")
                return "opened"

        self._status_set("快速狩猎入口", "点击后未确认菜单")
        return "failed"

    def _quick_hunt_run_rice_scheduler(self) -> bool:
        """Mirror MFABD2: hunting ground -> adventure -> rice recheck."""

        self._status_set("快速狩猎当前阶段", "米饭调度")
        if self._quick_hunt_resource_empty("米饭"):
            self._status_set("快速狩猎米饭", "0，跳过")
            return True

        hunting_enabled = bool(self.config.get("快速狩猎狩猎场", True))
        adventure_enabled = bool(self.config.get("快速狩猎冒险航线", True))
        hunting_mode, adventure_mode = self._quick_hunt_count_modes()

        if hunting_enabled:
            if not self._quick_hunt_select_hunting_ground():
                return False
            result = self._quick_hunt_execute_current_map(hunting_mode, "狩猎场")
            if result == "failed":
                return False
            if result == "depleted" or self._quick_hunt_resource_empty("米饭"):
                self._status_set("快速狩猎米饭", "已耗尽")
                return True

        adventure_selected = False
        if adventure_enabled:
            if not self._quick_hunt_reset_map():
                return False
            adventure_selected = self._quick_hunt_select_adventure_route()
            if adventure_selected:
                result = self._quick_hunt_execute_current_map(adventure_mode, "冒险航线")
                if result == "failed":
                    return False
                if result == "depleted" or self._quick_hunt_resource_empty("米饭"):
                    self._status_set("快速狩猎米饭", "已耗尽")
                    return True

        # MFABD2 RiceRecheck: consume any rice left after a skipped/non-double route.
        if hunting_enabled and not self._quick_hunt_resource_empty("米饭"):
            self._status_set("快速狩猎当前阶段", "剩余米饭回收")
            if not self._quick_hunt_reset_map():
                return False
            if not self._quick_hunt_select_hunting_ground():
                return False
            result = self._quick_hunt_execute_current_map("MAX", "狩猎场兜底")
            if result == "failed":
                return False

        if not hunting_enabled and not adventure_selected:
            self.log_info("快速狩猎：狩猎场已关闭且没有可执行的冒险航线，保留米饭。")
        self._status_set("快速狩猎米饭", "调度结束")
        return True

    def _quick_hunt_run_crystal_cave(self) -> bool:
        self._status_set("快速狩猎当前阶段", "圣石洞穴")
        if not self._quick_hunt_reset_map():
            return False
        self._click_mf_reference(138, 355, after_sleep=0.8)
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

        element = str(self.config.get("快速狩猎圣石属性", "火"))
        if element not in QUICK_HUNT_STONE_ELEMENTS:
            self.log_info(f"快速狩猎：不支持的圣石属性配置：{element}")
            return False
        clicked = self._quick_hunt_click_ocr(
            [rf"{re.escape(element)}.?洞穴"],
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

    def _quick_hunt_select_hunting_ground(self) -> bool:
        chapter = str(self.config.get("快速狩猎章节图", "低练度·章节1"))
        entry = QUICK_HUNT_HUNTING_GROUNDS.get(chapter)
        if entry is None:
            self.log_info(f"快速狩猎：缺少狩猎场映射：{chapter}")
            return False
        patterns, roi, swipe = entry
        if swipe is not None:
            self._quick_hunt_drag_mf(*swipe, after_sleep=2.0)
        clicked = self._quick_hunt_click_ocr(
            patterns,
            roi,
            self._quick_hunt_ui_timeout(),
            name=f"选择狩猎场-{chapter}",
        )
        if not clicked:
            self.log_info(f"快速狩猎：未识别到目标狩猎场：{chapter}")
        return clicked

    def _quick_hunt_select_adventure_route(self) -> bool:
        preferred = str(self.config.get("快速狩猎资源倾向", "金币"))
        if preferred not in QUICK_HUNT_ADVENTURE_POINTS:
            self.log_info(f"快速狩猎：不支持的冒险航线资源：{preferred}")
            return False
        strategy = str(self.config.get("快速狩猎双倍策略", "优先双倍"))
        self._click_mf_reference(*QUICK_HUNT_ADVENTURE_POINTS[preferred], after_sleep=0.8)
        if strategy == "忽视双倍":
            return True

        state = self._quick_hunt_double_state(preferred)
        if state is True:
            return True
        if strategy == "优先双倍":
            self.log_info(f"快速狩猎：首选{preferred}不是双倍图，回收米饭到狩猎场。")
            return False
        if strategy != "强制双倍":
            self.log_info(f"快速狩猎：不支持的双倍策略：{strategy}")
            return False

        alternate = "经验" if preferred == "金币" else "金币"
        self._click_mf_reference(*QUICK_HUNT_ADVENTURE_POINTS[alternate], after_sleep=0.8)
        if self._quick_hunt_double_state(alternate) is True:
            return True
        self.log_info("快速狩猎：金币和经验航线均未确认双倍，回收米饭到狩猎场。")
        return False

    def _quick_hunt_double_state(self, resource: str) -> bool | None:
        vision = self._quick_vision()
        frame = self.capture_frame()
        positive = self._quick_spec(QUICK_HUNT_DOUBLE_TEMPLATES[resource])
        positive_match = vision.match(frame, positive)
        if vision.passes(positive_match, positive):
            return True
        negative = self._quick_spec(QUICK_HUNT_NO_DOUBLE_TEMPLATES[resource])
        negative_match = vision.match(frame, negative)
        if vision.passes(negative_match, negative):
            return False
        self.log_info(
            f"快速狩猎：{resource}双倍状态不明确，"
            f"double={positive_match.score:.3f}/{positive_match.pixel_score:.3f}, "
            f"normal={negative_match.score:.3f}/{negative_match.pixel_score:.3f}"
        )
        return None

    def _quick_hunt_execute_current_map(self, count_mode: str, stage: str) -> str:
        self._status_set("快速狩猎当前阶段", stage)
        if not self._quick_hunt_click_ocr(
            [r"快速狩猎"],
            QUICK_HUNT_BUTTON_ROI,
            self._quick_hunt_ui_timeout(),
            name=f"{stage}-快速狩猎按钮",
            require_enabled=True,
        ):
            return "failed"
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

    def _quick_hunt_reset_map(self) -> bool:
        for attempt in range(3):
            self._click_mf_reference(139, 197, after_sleep=0.8)
            text, _box = self._quick_hunt_wait_ocr(
                [r"哥布林遗迹"],
                QUICK_HUNT_MAP_CENTER_ROI,
                1.5,
                name=f"快速狩猎地图复位{attempt + 1}",
            )
            if text:
                return True
        self.log_info("快速狩猎：有限次数内无法复位到哥布林遗迹。")
        return False

    def _quick_hunt_resource_empty(self, resource: str) -> bool:
        frame = self.capture_frame()
        text = self._quick_hunt_ocr_text(frame, QUICK_HUNT_RESOURCE_ROI, name=f"{resource}数量")
        normalized = self._normalize_text(text).replace("：", ":")
        empty = re.search(r"(?:^|\D)0[/：:|\-~][1-9]\d*", normalized) is not None
        self._status_set(f"快速狩猎{resource}", text or "未识别")
        return empty

    def _quick_hunt_count_modes(self) -> tuple[str, str]:
        allocation = str(
            self.config.get("快速狩猎米饭分配", "狩猎场x1 / 双倍图MAX")
        )
        if allocation == "狩猎场MAX / 双倍图x1":
            return "MAX", "MIN"
        return "MIN", "MAX"

    def _quick_hunt_wait_ocr(
        self,
        patterns: list[str],
        roi: tuple[int, int, int, int],
        timeout: float,
        name: str,
    ) -> tuple[str, object | None]:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.capture_frame()
            boxes = self._quick_vision().ocr_boxes(frame, name, roi)
            text = " ".join(str(getattr(box, "name", "")) for box in boxes)
            normalized = self._normalize_text(text)
            for box in boxes:
                value = self._normalize_text(getattr(box, "name", ""))
                if any(pattern.search(value) for pattern in compiled):
                    return text, box
            if any(pattern.search(normalized) for pattern in compiled):
                return text, None
            self.sleep(0.4)
        return "", None

    def _quick_hunt_click_ocr(
        self,
        patterns: list[str],
        roi: tuple[int, int, int, int],
        timeout: float,
        name: str,
        require_enabled: bool = False,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        vision = self._quick_vision()
        while monotonic() <= end_at:
            frame = self.capture_frame()
            for box in vision.ocr_boxes(frame, name, roi):
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
        roi: tuple[int, int, int, int],
        name: str,
    ) -> str:
        return self._quick_vision().ocr_text(frame, name, roi)

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
        gray = DailyTask._to_gray(crop)
        return float(np.mean(gray >= 170)) >= 0.02

    def _quick_hunt_red_dot(self, frame) -> tuple[int, int] | None:
        height, width = frame.shape[:2]
        left, top, roi_width, roi_height = Vision.reference_roi(
            QUICK_HUNT_RED_DOT_ROI,
            width,
            height,
        )
        crop = frame[top : top + roi_height, left : left + roi_width]
        if crop.size == 0:
            return None
        color = crop[:, :, :3] if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array((0, 140, 150)), np.array((10, 255, 255)))
        mask |= cv2.inRange(hsv, np.array((170, 140, 150)), np.array((180, 255, 255)))
        count, _labels, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
        candidates = [
            index
            for index in range(1, count)
            if int(stats[index, cv2.CC_STAT_AREA]) >= 3
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda index: int(stats[index, cv2.CC_STAT_AREA]))
        center_x, center_y = centers[best]
        y_offset = round(height * -5 / 720)
        return round(left + center_x), round(top + center_y + y_offset)

    def _quick_hunt_return_home(self) -> bool:
        self._status_set("快速狩猎当前阶段", "返回主页")
        for _attempt in range(4):
            frame = self.capture_frame()
            if self._home_brightness_ratio(frame) >= self._home_ratio_threshold():
                return True
            self._click_mf_reference(135, 37, after_sleep=1.0)
        frame = self.capture_frame()
        return self._home_brightness_ratio(frame) >= self._home_ratio_threshold()

    def _quick_vision(self) -> Vision:
        vision = getattr(self, "_quick_hunt_vision", None)
        if vision is None:
            vision = Vision(self)
            self._quick_hunt_vision = vision
        return vision

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

    def _quick_hunt_drag_mf(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        after_sleep: float = 0.0,
    ) -> None:
        frame = self.capture_frame()
        frame_height, frame_width = frame.shape[:2]
        start_client = (
            round(frame_width * start[0] / 1280),
            round(frame_height * start[1] / 720),
        )
        end_client = (
            round(frame_width * end[0] / 1280),
            round(frame_height * end[1] / 720),
        )

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

            start_abs = to_screen(start_client)
            end_abs = to_screen(end_client)
            steps = 27
            win32api.SetCursorPos(start_abs)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            try:
                for index in range(1, steps + 1):
                    ratio = index / steps
                    win32api.SetCursorPos(
                        (
                            round(start_abs[0] + (end_abs[0] - start_abs[0]) * ratio),
                            round(start_abs[1] + (end_abs[1] - start_abs[1]) * ratio),
                        )
                    )
                    time.sleep(0.8 / steps)
                time.sleep(0.6)
            finally:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(after_sleep)

    def _click_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _wait_loading_or_template(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template(
                    task_name,
                    spec,
                    name,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False

    def _wait_loading_gone_or_template(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False

    def _wait_loading_or_template_or_ocr(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        keywords: list[str],
        name: str,
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template_or_ocr(
                    task_name,
                    spec,
                    keywords,
                    name,
                    last_text=last_text,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False, last_text

    def _wait_loading_gone_or_template_or_ocr(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        keywords: list[str],
        name: str,
        last_text: str = "",
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False, last_text
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False, last_text

    def _wait_for_template(
        self,
        spec: DailyTemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _wait_for_template_or_ocr(
        self,
        spec: DailyTemplateSpec,
        keywords: list[str],
        timeout: float,
        name: str,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_for_ocr_keywords(
        self,
        keywords: list[str],
        timeout: float,
        minimum_matches: int,
        name: str,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name)
            last_text = text
            count = self._keyword_match_count(text, keywords)
            self.info_set(f"{name} 关键字", f"{count}/{len(keywords)}")
            if count >= minimum_matches:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_home_brightness(
        self,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + float(self.config.get("主页确认等待秒数", 10.0))
        last_ratio = 0.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            last_ratio = self._home_brightness_ratio(frame)
            self.info_set(f"{name} 亮度", f"{last_ratio:.3f}")
            if last_ratio >= self._home_ratio_threshold():
                return True
            self.sleep(interval)

        self.log_info(f"{name}：主页亮度未达到阈值，ratio={last_ratio:.3f}")
        return False

    def _home_brightness_ratio(self, frame) -> float:
        return max(self._home_brightness_ratio_for_template(frame, spec) for spec in HOME_TEMPLATES)

    def _home_brightness_ratio_for_template(
        self,
        frame,
        spec: DailyTemplateSpec,
    ) -> float:
        template = self._load_template(spec)
        mask = self._load_template_mask(spec)
        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = offline_template_scale(spec.file_name, frame_width, frame_height)
        template_height, template_width = template.shape[:2]
        roi_width = max(8, round(template_width * scale))
        roi_height = max(8, round(template_height * scale))
        center_x = round(frame_width * (166 / REFERENCE_WIDTH))
        center_y = round(frame_height * (158 / REFERENCE_HEIGHT))
        left = max(0, center_x - roi_width // 2)
        top = max(0, center_y - roi_height // 2)
        right = min(frame_width, left + roi_width)
        bottom = min(frame_height, top + roi_height)
        region = frame_gray[top:bottom, left:right]
        if region.size == 0:
            return 0.0

        scaled_template = self._resize_template(template, scale)
        scaled_mask = self._resize_mask(mask, scale)
        match_height = min(region.shape[0], scaled_template.shape[0])
        match_width = min(region.shape[1], scaled_template.shape[1])
        if match_height <= 0 or match_width <= 0:
            return 0.0
        region = region[:match_height, :match_width]
        scaled_template = scaled_template[:match_height, :match_width]
        if scaled_mask is not None:
            scaled_mask = scaled_mask[:match_height, :match_width]
            valid = scaled_mask > 0
            if not np.any(valid):
                return 0.0
            template_mean = float(np.mean(scaled_template[valid]))
            region_mean = float(np.mean(region[valid]))
        else:
            template_mean = float(np.mean(scaled_template))
            region_mean = float(np.mean(region))
        if template_mean <= 0:
            return 0.0
        return float(region_mean / template_mean)

    @staticmethod
    def _empty_match() -> DailyMatchResult:
        return DailyMatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))

    def _match_best(
        self,
        frame,
        specs: tuple[DailyTemplateSpec, ...],
    ) -> tuple[DailyMatchResult, DailyTemplateSpec]:
        best = self._empty_match()
        best_spec = specs[0]
        for spec in specs:
            result = self._match(frame, spec)
            if result.score > best.score:
                best = result
                best_spec = spec
        return best, best_spec

    def _match(self, frame, spec: DailyTemplateSpec) -> DailyMatchResult:
        empty = DailyMatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            template = self._load_template(spec)
            mask = self._load_template_mask(spec)
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty

        try:
            frame_gray = self._to_gray(frame)
            full_height, full_width = frame_gray.shape[:2]
            roi_left, roi_top, roi_right, roi_bottom = offline_template_search_region(
                spec.file_name,
                full_width,
                full_height,
            )
            search = frame_gray[roi_top:roi_bottom, roi_left:roi_right]
            frame_height, frame_width = search.shape[:2]
            base_scale = offline_template_scale(spec.file_name, full_width, full_height)
            best = empty

            for scale in self._candidate_scales(base_scale):
                scaled_template = self._resize_template(template, scale)
                scaled_mask = self._resize_mask(mask, scale)
                height, width = scaled_template.shape[:2]
                if height < 8 or width < 8 or height > frame_height or width > frame_width:
                    continue

                if scaled_mask is None:
                    result = cv2.matchTemplate(search, scaled_template, cv2.TM_CCOEFF_NORMED)
                else:
                    result = cv2.matchTemplate(
                        search,
                        scaled_template,
                        cv2.TM_CCORR_NORMED,
                        mask=scaled_mask,
                    )
                _, max_value, _, max_location = cv2.minMaxLoc(result)
                if not np.isfinite(max_value):
                    continue
                if max_value > best.score:
                    x, y = int(max_location[0]), int(max_location[1])
                    region = search[y : y + height, x : x + width]
                    best = DailyMatchResult(
                        score=float(max_value),
                        pixel_score=self._pixel_similarity(region, scaled_template, scaled_mask),
                        position=(roi_left + x, roi_top + y),
                        size=(int(width), int(height)),
                    )
        except (cv2.error, MemoryError) as exc:
            self._match_pause_until = monotonic() + 2.0
            message = f"图像匹配内存不足，暂停识别2秒：{spec.name}"
            self.info_set("匹配错误", message)
            if spec.name not in self._match_error_names:
                self._match_error_names.add(spec.name)
                self.log_warning(f"{message}；{exc}", notify=True)
            return empty

        return best

    def _load_template(self, spec: DailyTemplateSpec) -> np.ndarray:
        if spec.name in self._templates:
            return self._templates[spec.name]

        template, mask = self._read_template_and_mask(spec)
        self._templates[spec.name] = template
        self._template_masks[spec.name] = mask
        return template

    def _load_template_mask(self, spec: DailyTemplateSpec) -> np.ndarray | None:
        if spec.name not in self._templates:
            self._load_template(spec)
        return self._template_masks.get(spec.name)

    def _read_template_and_mask(
        self,
        spec: DailyTemplateSpec,
    ) -> tuple[np.ndarray, np.ndarray | None]:
        path = TEMPLATE_DIR / spec.file_name
        source = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if source is None:
            raise RuntimeError(f"日常任务模板不存在或无法读取：{path}")

        if spec.crop is not None:
            source = self._crop_relative(source, spec.crop)

        mask = None
        if len(source.shape) == 2:
            template = source
        else:
            if source.shape[2] == 4:
                template = cv2.cvtColor(source, cv2.COLOR_BGRA2GRAY)
            else:
                template = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)

            if spec.green_mask or offline_template_requires_green_mask(spec.file_name):
                color = source[:, :, :3]
                green_pixels = (
                    (color[:, :, 0] <= 4)
                    & (color[:, :, 1] >= 251)
                    & (color[:, :, 2] <= 4)
                )
                if source.shape[2] == 4:
                    green_pixels |= source[:, :, 3] == 0
                mask = np.where(green_pixels, 0, 255).astype(np.uint8)

        return template, mask

    def _passes(self, result: DailyMatchResult, spec: DailyTemplateSpec) -> bool:
        threshold = float(self.config.get(spec.threshold_key, spec.default_threshold))
        return result.score >= threshold

    def _ocr_text(self, frame, name: str) -> str:
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("日常 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return ""

        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    @staticmethod
    def _keyword_match_count(text: str, keywords: list[str]) -> int:
        return keyword_match_count(text, keywords)

    _normalize_text = staticmethod(normalize_ocr_text)

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("主页亮度比例阈值", 0.75))

    _candidate_scales = staticmethod(candidate_scales)
    _resize_template = staticmethod(resize_template)
    _crop_relative = staticmethod(crop_relative)
    _to_gray = staticmethod(to_gray)
    _resize_mask = staticmethod(resize_mask)
    _pixel_similarity = staticmethod(pixel_similarity)


QUICK_HUNT_RED_DOT_ROI = (100, 163, 45, 44)
QUICK_HUNT_MENU_TITLE_ROI = (108, 138, 61, 27)
QUICK_HUNT_RESOURCE_ROI = (1040, 33, 111, 27)
QUICK_HUNT_BUTTON_ROI = (1029, 632, 89, 33)
QUICK_HUNT_COUNT_ROI = (408, 301, 462, 64)
QUICK_HUNT_START_ROI = (622, 492, 161, 111)
QUICK_HUNT_REWARD_ROI = (457, 600, 413, 110)
QUICK_HUNT_DIALOG_ROI = (500, 420, 300, 190)
QUICK_HUNT_MAP_CENTER_ROI = (538, 372, 107, 30)
QUICK_HUNT_CRYSTAL_TITLE_ROI = (595, 372, 86, 28)
QUICK_HUNT_STONE_LIST_ROI = (178, 94, 126, 283)

QUICK_HUNT_ADVENTURE_POINTS = {
    "金币": (138, 200),
    "经验": (138, 280),
}
QUICK_HUNT_STONE_ELEMENTS = frozenset({"火", "水", "风", "光", "暗"})
QUICK_HUNT_HUNTING_GROUNDS = {
    "低练度·章节1": ([r"野猪洞穴"], (847, 156, 80, 23), None),
    "矿石·章节7": (
        [r"(?=.*蜥)(?=.*蜴)(?=.*祭坛).{5,}"],
        (298, 369, 134, 88),
        ((556, 32), (624, 580)),
    ),
    "木材·章节9": (
        [r"守山人休息处"],
        (448, 409, 167, 74),
        ((974, 26), (161, 570)),
    ),
}

QUICK_HUNT_SANDBOX_PIN_TEMPLATE = TemplateSpec(
    "快速狩猎箱庭图钉",
    "image/pin.png",
    threshold=0.78,
    roi=(1122, 646, 40, 38),
    min_pixel_score=0.72,
)
QUICK_HUNT_SANDBOX_RUN_TEMPLATE = TemplateSpec(
    "快速狩猎箱庭奔跑",
    "image/green/Run.png",
    threshold=0.78,
    green_mask=True,
    min_pixel_score=0.72,
)
QUICK_HUNT_SANDBOX_TEMPLATES = (
    QUICK_HUNT_SANDBOX_PIN_TEMPLATE,
    QUICK_HUNT_SANDBOX_RUN_TEMPLATE,
)
QUICK_HUNT_LIST_COLLAPSE_TEMPLATE = TemplateSpec(
    "快速狩猎资源列表收起",
    "image/green/Battle_ListCollapseGE.png",
    threshold=0.78,
    roi=(1067, 130, 123, 256),
    green_mask=True,
    min_pixel_score=0.72,
)
QUICK_HUNT_DOUBLE_TEMPLATES = {
    "金币": TemplateSpec(
        "金币双倍",
        "image/green/DoubleGold.png",
        threshold=0.8,
        roi=(413, 592, 159, 110),
        green_mask=True,
        min_pixel_score=0.72,
    ),
    "经验": TemplateSpec(
        "经验双倍",
        "image/green/DoubleExp.png",
        threshold=0.8,
        roi=(413, 592, 159, 110),
        green_mask=True,
        min_pixel_score=0.72,
    ),
}
QUICK_HUNT_NO_DOUBLE_TEMPLATES = {
    "金币": TemplateSpec(
        "金币非双倍",
        "image/DoubleNoGold.png",
        threshold=0.8,
        roi=(413, 592, 159, 110),
        min_pixel_score=0.72,
    ),
    "经验": TemplateSpec(
        "经验非双倍",
        "image/DoubleNoExp.png",
        threshold=0.8,
        roi=(413, 592, 159, 110),
        min_pixel_score=0.72,
    ),
}


GUILD_TEMPLATE = DailyTemplateSpec(
    name="guild",
    file_name="guild.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
)

GUILD_MAIN_ACTIVE_TEMPLATE = DailyTemplateSpec(
    name="guild_main_active",
    file_name="image/green/MainBotmUnionAcGE.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
    green_mask=True,
)

GUILD_FINISHED_TEMPLATE = DailyTemplateSpec(
    name="guild_finished",
    file_name="guild-finished.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
)

GUILD_MAIN_FINISHED_TEMPLATE = DailyTemplateSpec(
    name="guild_main_finished",
    file_name="image/green/MainBotmUnionGE.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
    green_mask=True,
)

GUILD_ENTRY_TEMPLATES = (
    GUILD_TEMPLATE,
    GUILD_MAIN_ACTIVE_TEMPLATE,
    GUILD_FINISHED_TEMPLATE,
    GUILD_MAIN_FINISHED_TEMPLATE,
)

GUILD_SIGNUP_SUCCESS_TEMPLATE = DailyTemplateSpec(
    name="guild_signup_success",
    file_name="guild-singup-success.png",
    threshold_key="公会签到成功阈值",
    default_threshold=0.76,
)

MY_HOME_TEMPLATE = DailyTemplateSpec(
    name="my_home",
    file_name="my-home.png",
    threshold_key="小屋页面阈值",
    default_threshold=0.76,
)

LOADING_TEMPLATE = DailyTemplateSpec(
    name="ui_loading_black",
    file_name="image/UI_loading_black.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

HOME_TEMPLATE = DailyTemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
)

HOME_ICE_TEMPLATE = DailyTemplateSpec(
    name="home_ice",
    file_name="image/green/MainHomeIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_RICE_TEMPLATE = DailyTemplateSpec(
    name="home_rice",
    file_name="image/green/MainHomeRIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_TEMPLATES = (HOME_TEMPLATE, HOME_ICE_TEMPLATE, HOME_RICE_TEMPLATE)

GUILD_SUCCESS_KEYWORDS = ["签到成功", "奖励已发放至邮箱"]
