import re
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
    best_pixel_valid_match,
    candidate_scales,
    crop_relative,
    pixel_similarity,
    resize_mask,
    resize_template,
    template_match_response,
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
QUICK_HUNT_CHILD_CONFIG_KEYS = (
    "快速狩猎冒险航线",
    "快速狩猎狩猎场",
    "快速狩猎圣石洞穴",
    "快速狩猎双倍策略",
    "快速狩猎资源倾向",
    "快速狩猎米饭分配",
    "快速狩猎章节图",
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
    include_quick_hunt_config = False
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
        self.name = "公会、小屋、酒馆"
        self.description = "执行公会签到、小屋签到和酒馆一键收菜。"
        self.icon = FluentIcon.CAR
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._templates: dict[str, np.ndarray] = {}
        self._template_masks: dict[str, np.ndarray | None] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0
        if self.include_quick_hunt_config:
            self._quick_hunt_vision: Vision | None = None
            self._quick_hunt_test_action: str | None = None
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
                "快速狩猎模板阈值": 0.78,
                "快速狩猎像素相似度阈值": 0.72,
                "快速狩猎界面等待秒数": 8.0,
                "快速狩猎结算等待秒数": 15.0,
                "快速狩猎入口测试": "",
                "快速狩猎菜单测试": "",
                "快速狩猎圣石测试": "",
                "快速狩猎完整测试": "",
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
                "快速狩猎圣石洞穴": "读取五种圣石数量并扫荡当前最少的属性洞穴。",
                "快速狩猎双倍策略": (
                    "优先双倍会检测金币和经验并在都双倍时选择金币；"
                    "强制双倍优先配置资源；忽视双倍固定选择金币。"
                ),
                "快速狩猎资源倾向": "强制双倍策略下优先检查金币或经验。",
                "快速狩猎米饭分配": (
                    "狩猎场 MIN 时双倍冒险航线使用 MAX；"
                    "狩猎场 MAX 时跳过冒险航线。"
                ),
                "快速狩猎章节图": "选择章节1、章节7或章节9狩猎场。",
                "快速狩猎模板阈值": "快速狩猎模板匹配最低分数。",
                "快速狩猎像素相似度阈值": "快速狩猎模板还必须达到的像素相似度。",
                "快速狩猎界面等待秒数": "等待狩猎菜单、地图和按钮出现的最长时间。",
                "快速狩猎结算等待秒数": "点击狩猎后等待奖励页或资源不足提示的最长时间。",
                "快速狩猎入口测试": (
                    "只读检查不会点击；打开菜单会先确认首页，再检查并点击"
                    "1920×1080参考点(1782,237)。测试前停留在首页。"
                ),
                "快速狩猎菜单测试": (
                    "只读检查不会点击；执行米饭会按当前配置实际消耗米饭。"
                    "测试前需要已经打开快速狩猎菜单。"
                ),
                "快速狩猎圣石测试": (
                    "执行圣石会按当前配置实际消耗火把；返回主页仅测试返回流程。"
                    "测试前需要已经打开快速狩猎菜单。"
                ),
                "快速狩猎完整测试": (
                    "从首页执行完整快速狩猎流程，会实际消耗米饭和火把。"
                ),
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
                    "options": ["狩猎场x1 / 双倍图MAX", "狩猎场MAX / 跳过冒险航线"],
                },
                "快速狩猎章节图": {
                    "type": "drop_down",
                    "options": ["低练度·章节1", "矿石·章节7", "木材·章节9"],
                },
                "快速狩猎模板阈值": {"min": 0.5, "max": 0.95, "step": 0.01},
                "快速狩猎像素相似度阈值": {
                    "min": 0.5,
                    "max": 0.95,
                    "step": 0.01,
                },
                "快速狩猎界面等待秒数": {"min": 2.0, "max": 30.0, "step": 1.0},
                "快速狩猎结算等待秒数": {"min": 5.0, "max": 60.0, "step": 1.0},
                "快速狩猎入口测试": {
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
                "快速狩猎菜单测试": {
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
                "快速狩猎圣石测试": {
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
                "快速狩猎完整测试": {
                    "type": "button",
                    "callback": lambda _checked=False: self._queue_quick_hunt_test("full"),
                    "text": "完整执行(消耗)",
                },
            }
        )
        if not self.include_quick_hunt_config:
            for key in QUICK_HUNT_CONFIG_KEYS:
                self.default_config.pop(key, None)
                self.config_description.pop(key, None)
                self.config_type.pop(key, None)

    def _status_set(self, key: str, value) -> None:
        try:
            self.info_set(key, value)
        except AttributeError:
            pass

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "公会、小屋、酒馆已禁用。")
            self.log_info("公会、小屋、酒馆已禁用。")
            return True

        self.info_set("状态", "公会、小屋、酒馆启动。")
        steps = [
            ("公会签到", "执行公会签到", self.run_guild_sign_in),
            ("小屋签到", "执行小屋签到", self.run_my_home_sign_in),
            ("一键收菜", "执行一键收菜", self.run_business_collect),
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
                    self.log_info(f"{name} 未满足后续触发条件，停止剩余子任务。")
            except Exception as exc:
                failed.append(name)
                stop_remaining = True
                self.log_error(f"日常子任务失败：{name}", exc)

        self.info_set("完成", str(success))
        self.info_set("失败", str(failed))
        self.info_set("跳过", str(skipped))
        self.info_set("状态", "公会、小屋、酒馆结束。")
        self.log_info(
            f"公会、小屋、酒馆结束：完成={success}, 失败={failed}, 跳过={skipped}",
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
                success = opened in {"opened", "skip"}
                detail = {
                    "opened": "菜单已打开",
                    "skip": "未发现入口红点",
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
            self.log_info(f"快速狩猎测试结束：{label}，{result}，{detail}", notify=True)
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
        home_ok, home_button, home_spec, home_ratio, gacha_text = (
            self._quick_hunt_home_signals(frame)
        )
        self._status_set(
            "快速狩猎首页按钮",
            f"{home_spec.file_name}={home_button.score:.3f}/{home_button.pixel_score:.3f}"
            f"({'通过' if home_ok else '未通过'})",
        )
        is_red, point, bgr, hsv = self._quick_hunt_entry_red_state(frame)
        self._status_set(
            "快速狩猎红点识别",
            f"point={point}, BGR={bgr}, HSV={hsv}, {'红色' if is_red else '非红色'}",
        )
        self._status_set(
            "快速狩猎主页亮度",
            f"{home_ratio:.3f}/{self._home_ratio_threshold():.3f}",
        )
        self._status_set("快速狩猎主页抽抽乐 OCR", gacha_text or "-")
        return True

    def _quick_hunt_inspect_menu(self) -> bool:
        """Inspect menu OCR and templates using one frame without clicking."""

        vision = self._quick_vision()
        frame = self.capture_frame()
        ocr_regions = (
            ("快速狩猎菜单 OCR", "测试-菜单标题", QUICK_HUNT_MENU_TITLE_ROI),
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

        chapter = str(self.config.get("快速狩猎章节图", "低练度·章节1"))
        chapter_entry = QUICK_HUNT_HUNTING_GROUNDS.get(chapter)
        if chapter_entry is None:
            self._status_set("快速狩猎章节 OCR", f"缺少映射：{chapter}")
        else:
            chapter_text = vision.ocr_text(
                frame,
                f"测试-{chapter}",
                relative_roi=QUICK_HUNT_MAP_SCAN_ROI,
            )
            self._status_set("快速狩猎章节 OCR", chapter_text or "-")

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
            rice_ok = self._quick_hunt_run_rice_scheduler()
            success = success and rice_ok
            if bool(self.config.get("快速狩猎圣石洞穴", True)):
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
    ) -> tuple[bool, DailyMatchResult, DailyTemplateSpec, float, str]:
        home_button, home_spec = self._match_best(frame, HOME_TEMPLATES)
        home_ratio = self._home_brightness_ratio(frame)
        gacha_text = self._quick_vision().ocr_text(
            frame,
            "快速狩猎主页抽抽乐",
            relative_roi=QUICK_HUNT_HOME_GACHA_ROI,
        )
        confirmed = (
            self._passes(home_button, home_spec)
            and home_ratio >= self._home_ratio_threshold()
            and "抽抽乐" in self._normalize_text(gacha_text)
        )
        return confirmed, home_button, home_spec, home_ratio, gacha_text

    def _wait_for_quick_hunt_home(self, interval: float = 0.35) -> bool:
        end_at = monotonic() + float(self.config.get("主页确认等待秒数", 10.0))
        last_button = self._empty_match()
        last_spec = HOME_TEMPLATE
        last_ratio = 0.0
        last_gacha_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            home_ok, last_button, last_spec, last_ratio, last_gacha_text = (
                self._quick_hunt_home_signals(frame)
            )
            self._status_set(
                "快速狩猎首页按钮",
                f"{last_spec.file_name}={last_button.score:.3f}/{last_button.pixel_score:.3f}",
            )
            self._status_set(
                "快速狩猎主页亮度",
                f"{last_ratio:.3f}/{self._home_ratio_threshold():.3f}",
            )
            self._status_set(
                "快速狩猎主页抽抽乐 OCR",
                last_gacha_text or "-",
            )
            if home_ok:
                return True
            self.sleep(interval)

        self.log_info(
            "快速狩猎：未同时确认首页小屋按钮、亮度和抽抽乐文字，"
            f"template={last_spec.file_name}, "
            f"button={last_button.score:.3f}/{last_button.pixel_score:.3f}, "
            f"ratio={last_ratio:.3f}, ocr={last_gacha_text or '-'}。"
        )
        return False

    @staticmethod
    def _quick_hunt_entry_red_state(
        frame,
    ) -> tuple[bool, tuple[int, int], tuple[int, int, int], tuple[int, int, int]]:
        height, width = frame.shape[:2]
        x = max(0, min(width - 1, round(width * QUICK_HUNT_ENTRY_POINT[0])))
        y = max(0, min(height - 1, round(height * QUICK_HUNT_ENTRY_POINT[1])))
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
        self.sleep(0.5)
        frame = self.capture_frame()
        is_red, point, bgr, hsv = self._quick_hunt_entry_red_state(frame)
        self._status_set(
            "快速狩猎红点识别",
            f"point={point}, BGR={bgr}, HSV={hsv}, {'红色' if is_red else '非红色'}",
        )
        if not is_red:
            self._status_set("快速狩猎入口", "入口点不是红色，按已完成跳过")
            return "skip"

        self.sleep(0.5)
        self.operate_click(*QUICK_HUNT_ENTRY_POINT, after_sleep=1.0)
        text, _box = self._quick_hunt_wait_ocr(
            [r"狩猎场"],
            QUICK_HUNT_MENU_TITLE_ROI,
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
            if not self._quick_hunt_select_hunting_ground():
                return False
            result = self._quick_hunt_execute_current_map(hunting_mode, "狩猎场")
            if result == "failed":
                return False
            if result == "depleted" or self._quick_hunt_resource_empty("米饭"):
                self._status_set("快速狩猎米饭", "已耗尽")
                return True

        adventure_selected = False
        if adventure_enabled and adventure_mode is not None:
            adventure_selected = self._quick_hunt_select_adventure_route()
            if adventure_selected:
                result = self._quick_hunt_execute_current_map(adventure_mode, "冒险航线")
                if result == "failed":
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

    def _quick_hunt_select_hunting_ground(self) -> bool:
        chapter = str(self.config.get("快速狩猎章节图", "低练度·章节1"))
        target_pattern = QUICK_HUNT_HUNTING_GROUNDS.get(chapter)
        if target_pattern is None:
            self.log_info(f"快速狩猎：缺少狩猎场映射：{chapter}")
            return False
        vision = self._quick_vision()
        for scroll_index in range(7):
            frame = self.capture_frame()
            boxes = vision.ocr_boxes(
                frame,
                f"选择狩猎场-{chapter}-滚轮{scroll_index}",
                relative_roi=QUICK_HUNT_MAP_SCAN_ROI,
            )
            recognized = []
            target_box = None
            for box in boxes:
                value = self._normalize_text(getattr(box, "name", ""))
                for label, pattern in QUICK_HUNT_HUNTING_GROUNDS.items():
                    if pattern.search(value):
                        recognized.append(label)
                if target_pattern.search(value):
                    target_box = box
            self._status_set("快速狩猎章节 OCR", "、".join(dict.fromkeys(recognized)) or "-")
            if target_box is not None:
                point = self._quick_hunt_box_center(target_box)
                if point is not None:
                    vision.click_client(point, frame.shape, after_sleep=0.8)
                    return True
            if scroll_index < 6:
                self._quick_hunt_scroll_map_once()
        self.log_info(f"快速狩猎：滚轮向下最多6次仍未识别到目标狩猎场：{chapter}")
        return False

    def _quick_hunt_select_adventure_route(self) -> bool:
        preferred = str(self.config.get("快速狩猎资源倾向", "金币"))
        if preferred not in QUICK_HUNT_ADVENTURE_POINTS:
            self.log_info(f"快速狩猎：不支持的冒险航线资源：{preferred}")
            return False
        strategy = str(self.config.get("快速狩猎双倍策略", "优先双倍"))
        if strategy == "忽视双倍":
            self._quick_hunt_click_adventure("金币")
            return True
        states = self._quick_hunt_double_states()
        if strategy == "优先双倍":
            selected = "金币" if states["金币"] else "经验" if states["经验"] else None
            if selected is None:
                self.log_info("快速狩猎：金币和经验均未识别到双倍，跳过冒险航线。")
                return False
            self._quick_hunt_click_adventure(selected)
            return True
        if strategy == "强制双倍":
            alternate = "经验" if preferred == "金币" else "金币"
            for resource in (preferred, alternate):
                if states[resource]:
                    self._quick_hunt_click_adventure(resource)
                    return True
            self.log_info("快速狩猎：首选和备选资源均未识别到双倍，跳过冒险航线。")
            return False
        self.log_info(f"快速狩猎：不支持的双倍策略：{strategy}")
        return False

    def _quick_hunt_click_adventure(self, resource: str) -> None:
        self._click_reference(*QUICK_HUNT_ADVENTURE_POINTS[resource], after_sleep=0.8)

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
        empty = re.search(r"(?:^|\D)0[/：:|\-~][1-9]\d*", normalized) is not None
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
        roi: tuple[float, float, float, float],
        timeout: float,
        name: str,
    ) -> tuple[str, object | None]:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.capture_frame()
            boxes = self._quick_vision().ocr_boxes(frame, name, relative_roi=roi)
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
        roi: tuple[float, float, float, float],
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
        gray = DailyTask._to_gray(crop)
        return float(np.mean(gray >= 170)) >= 0.02

    def _quick_hunt_return_home(self) -> bool:
        self._status_set("快速狩猎当前阶段", "返回主页")
        for _attempt in range(4):
            frame = self.capture_frame()
            home_ok, button, spec, ratio, gacha_text = self._quick_hunt_home_signals(frame)
            self._status_set(
                "快速狩猎首页按钮",
                f"{spec.file_name}={button.score:.3f}/{button.pixel_score:.3f}",
            )
            self._status_set(
                "快速狩猎主页亮度",
                f"{ratio:.3f}/{self._home_ratio_threshold():.3f}",
            )
            self._status_set("快速狩猎主页抽抽乐 OCR", gacha_text or "-")
            if home_ok:
                return True
            self._click_reference(*QUICK_HUNT_RETURN_POINT, after_sleep=1.0)
        frame = self.capture_frame()
        return self._quick_hunt_home_signals(frame)[0]

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

    def _quick_hunt_scroll_map_once(self) -> None:
        frame = self.capture_frame()
        frame_height, frame_width = frame.shape[:2]
        left, top, right, bottom = QUICK_HUNT_MAP_SCAN_ROI
        point = (
            round(frame_width * (left + right) / 2),
            round(frame_height * (top + bottom) / 2),
        )
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
            screen_point = point
            if capture is not None and hasattr(capture, "get_abs_cords"):
                screen_point = capture.get_abs_cords(point[0], point[1])
            win32api.SetCursorPos(screen_point)
            interaction.scroll(point[0], point[1], -1)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(0.35)

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
            template_threshold = float(
                getattr(self, "config", {}).get(spec.threshold_key, spec.default_threshold)
            )

            for scale in self._candidate_scales(base_scale):
                scaled_template = self._resize_template(template, scale)
                scaled_mask = self._resize_mask(mask, scale)
                height, width = scaled_template.shape[:2]
                if height < 8 or width < 8 or height > frame_height or width > frame_width:
                    continue

                result = template_match_response(search, scaled_template, scaled_mask)
                candidate = best_pixel_valid_match(
                    result,
                    search,
                    scaled_template,
                    scaled_mask,
                    template_threshold=template_threshold,
                    pixel_threshold=0.0,
                )
                if candidate is None or candidate.score <= best.score:
                    continue
                x, y = candidate.location
                best = DailyMatchResult(
                    score=candidate.score,
                    pixel_score=candidate.pixel_score,
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
            raise RuntimeError(f"任务模板不存在或无法读取：{path}")

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


QUICK_HUNT_ENTRY_POINT = (1782 / REFERENCE_WIDTH, 237 / REFERENCE_HEIGHT)
QUICK_HUNT_MENU_TITLE_ROI = _quick_hunt_relative_roi(162, 207, 254, 248)
QUICK_HUNT_RESOURCE_ROI = _quick_hunt_relative_roi(1724, 80, 1602, 38)
QUICK_HUNT_BUTTON_ROI = _quick_hunt_relative_roi(1720, 1018, 1599, 963)
QUICK_HUNT_COUNT_ROI = _quick_hunt_relative_roi(1298, 826, 623, 257)
QUICK_HUNT_START_ROI = _quick_hunt_relative_roi(1136, 805, 963, 764)
QUICK_HUNT_REWARD_ROI = _quick_hunt_relative_roi(1055, 1019, 857, 965)
# The exception dialog could not be reproduced in-game. Keep the previous
# 1280x720-to-1920x1080 conversion until a real screenshot is available.
QUICK_HUNT_DIALOG_ROI = _quick_hunt_relative_roi(750, 630, 1200, 915)
QUICK_HUNT_MAP_SCAN_ROI = _quick_hunt_relative_roi(1528, 865, 330, 165)
QUICK_HUNT_CRYSTAL_TITLE_ROI = _quick_hunt_relative_roi(340, 452, 235, 128)
QUICK_HUNT_STONE_LIST_ROI = _quick_hunt_relative_roi(267, 141, 456, 566)
QUICK_HUNT_STONE_COUNT_ROI = _quick_hunt_relative_roi(1794, 288, 1689, 80)
QUICK_HUNT_DOUBLE_ROI = _quick_hunt_relative_roi(168, 337, 135, 205)
QUICK_HUNT_HOME_GACHA_ROI = _quick_hunt_relative_roi(110, 993, 205, 1047)

QUICK_HUNT_ADVENTURE_POINTS = {
    "金币": (207, 300),
    "经验": (207, 420),
}
QUICK_HUNT_CRYSTAL_POINT = (177, 449)
QUICK_HUNT_RETURN_POINT = (101, 55)
QUICK_HUNT_STONE_ELEMENTS = ("火", "水", "风", "光", "暗")
QUICK_HUNT_HUNTING_GROUNDS = {
    "低练度·章节1": re.compile(r"野猪洞穴"),
    "矿石·章节7": re.compile(r"蜥.?蜴.?人.?祭坛"),
    "木材·章节9": re.compile(r"守山人休息处"),
}

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
