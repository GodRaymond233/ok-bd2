import re
import time
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task, green_mask_from_template

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
MFABD2_REFERENCE_WIDTH = 1280
MFABD2_REFERENCE_HEIGHT = 720
ENTRY_REFERENCE_WIDTH = 2560
ENTRY_REFERENCE_HEIGHT = 1440
FREE_AP_SWITCH_SCREEN_ROI = (1680, 535, 120, 55)
PVP_RESULT_SCREEN_ROI = (932, 368, 699, 704)
PVP_RESULT_CLOSE_SCREEN_POINT = (1585, 410)
PVP_LEAVE_SCREEN_ROI = (928, 1269, 713, 116)
PVP_LEAVE_BUTTON_SCREEN_POINT = (1411, 1328)
PVP_CONFIRM_BUTTON_SCREEN_ROI = (1108, 1297, 349, 92)
PVP_BACK_HOME_REFERENCE_POINT = (100, 54)
PVP_RANK_DROP_CONFIRM_SCREEN_POINT = (960, 1006)
PVP_HUB_NOTICE_SCREEN_ROI = (1381, 865, 62, 45)
PVP_CARD_LIST_SWIPE_COUNT = 1
PVP_RESULT_BASE_MINUTES = 20.0
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "offline-train" / "train-source-screenshots"


@dataclass(frozen=True)
class PVPTemplateSpec:
    name: str
    file_name: str
    threshold_key: str
    default_threshold: float
    roi: tuple[int, int, int, int] | None = None
    green_mask: bool = False


@dataclass(frozen=True)
class PVPMatchResult:
    score: float
    pixel_score: float
    position: tuple[int, int]
    size: tuple[int, int]


class PVPTask(BaseBD2Task):
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "目标倍率",
        "主页亮度",
        "PVP 选关 OCR",
        "PVP 左侧滑动 OCR",
        "PVP 恶魔城",
        "PVP 入口卡带",
        "PVP 标签 OCR",
        "PVP 快速卡带",
        "PVP 箱庭",
        "PVP 段位下滑 OCR",
        "PVP 箱庭感叹号",
        "PVP 舞台",
        "PVP 自动战斗 OCR",
        "PVP 免费AP",
        "PVP 倍率 OCR",
        "PVP 开始战斗 OCR",
        "PVP 战斗中 OCR",
        "PVP 结算 OCR",
        "PVP 结算命中",
        "PVP 离开 OCR",
        "PVP 升降级确认 OCR",
        "PVP 返回主页",
        "PVP AP不足 OCR",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]

    status_key_labels = {
        "PVP 快速卡带": "PVP 卡带模板",
        "PVP 箱庭": "PVP 箱庭模板",
        "PVP 舞台": "PVP 舞台模板",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "镜中之战"
        self.description = "进行pvp自动战斗。"
        self.icon = FluentIcon.GAME
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._templates: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0
        self.default_config.update(
            {
                "启用": True,
                "竞技场战斗倍数": 1,
                "最多战斗轮次": 12,
                "加载页面阈值": 0.72,
                "主页亮度比例阈值": 0.75,
                "PVP OCR 阈值": 0.2,
                "PVP 选关页等待秒数": 12.0,
                "PVP 左侧滑动确认秒数": 6.0,
                "快速卡带等待秒数": 10.0,
                "PVP 入场等待秒数": 30.0,
                "PVP 菜单等待秒数": 12.0,
                "PVP 战斗开始等待秒数": 30.0,
                "PVP 结算基准等待分钟": PVP_RESULT_BASE_MINUTES,
                "PVP 离开等待秒数": 20.0,
                "PVP 返回箱庭等待秒数": 10.0,
                "PVP 返回主页等待秒数": 20.0,
                "PVP 快速卡带阈值": 0.78,
                "PVP 恶魔城阈值": 0.70,
                "PVP 入口卡带阈值": 0.78,
                "PVP 箱庭阈值": 0.78,
                "PVP 箱庭感叹号阈值": 0.72,
                "PVP 舞台阈值": 0.72,
                "PVP 定位修正阈值": 0.76,
                "loading 出现等待秒数": 6.0,
                "loading 消失等待秒数": 35.0,
            }
        )
        self.config_description.update(
            {
                "竞技场战斗倍数": (
                    "目标鸡尾酒消耗倍率，支持 1、4、5、10、20、40。"
                    "AP 不足时会临时降到 1。"
                ),
                "最多战斗轮次": "防止识别异常导致无限循环的最大战斗轮次。",
                "PVP OCR 阈值": "镜中之战流程 OCR 使用的最低可信度。",
                "PVP 结算基准等待分钟": "1 倍自动战斗结算最长等待时间，实际等待为该值除以倍率。",
                "PVP 返回箱庭等待秒数": "离开结算后等待回到 PVP 箱庭的最长时间。",
                "PVP 返回主页等待秒数": "从 PVP 箱庭返回主页后的主页确认最长时间。",
                "PVP 恶魔城阈值": "游戏卡珍藏集里定位恶魔城卡带的模板匹配阈值。",
                "PVP 箱庭感叹号阈值": "进入 PVP 箱庭后识别 tanhaoGE.png 的模板匹配阈值。",
            }
        )

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "镜中之战已禁用。")
            self.log_info("镜中之战已禁用。")
            return True

        target_multiplier = self._target_multiplier()
        self.info_set("状态", "镜中之战启动。")
        self.info_set("目标倍率", target_multiplier)
        self.log_info(f"镜中之战：目标倍率 {target_multiplier}。")

        if not self._ensure_pvp_hub():
            self.info_set("状态", "未能进入 PVP 箱庭。")
            return False

        current_multiplier = target_multiplier
        max_rounds = max(1, int(self.config.get("最多战斗轮次", 12)))
        for round_index in range(1, max_rounds + 1):
            self.info_set("当前阶段", f"第 {round_index} 轮")
            start_state = self._start_auto_battle(current_multiplier)
            if start_state == "ap_depleted":
                self.info_set("状态", "免费 AP 已耗尽。")
                self.log_info("镜中之战：免费 AP 已耗尽，流程结束。", notify=True)
                return True
            if start_state == "ap_shortage":
                if current_multiplier != 1:
                    self.log_info("镜中之战：当前倍率 AP 不足，降到 1 倍重试。")
                    current_multiplier = 1
                    self.info_set("目标倍率", current_multiplier)
                    continue
                self.info_set("状态", "1 倍仍 AP 不足。")
                return True
            if start_state != "started":
                self.info_set("状态", "未能开始战斗。")
                return False

            if not self._wait_result_and_leave(current_multiplier):
                self.info_set("状态", "战斗结算或离开失败。")
                return False

            self.info_set("状态", "镜中之战完成并返回主页。")
            self.log_info("镜中之战：自动战斗完成并返回主页。", notify=True)
            return True

        self.info_set("状态", "达到最多战斗轮次。")
        self.log_info(f"镜中之战：达到最多战斗轮次 {max_rounds}，停止。")
        return True

    def _ensure_pvp_hub(self) -> bool:
        self.info_set("当前阶段", "确认镜中之战")
        if self._wait_for_template(
            PVP_MEDALS_TEMPLATE,
            timeout=2.0,
            name="PVP 箱庭",
        ):
            self._clear_pvp_hub_notice_if_present()
            return True

        return self._enter_pvp_from_home()

    def _enter_pvp_from_home(self) -> bool:
        self.info_set("当前阶段", "确认主页")
        ratio = self._home_brightness_ratio(self.capture_frame())
        self.info_set("主页亮度", f"{ratio:.3f}")
        if ratio < self._home_ratio_threshold():
            self.log_info(f"镜中之战：当前不在主页或主页亮度不足，ratio={ratio:.3f}。")
            return False

        self.info_set("当前阶段", "打开游戏卡珍藏集")
        self._click_entry_reference(2258, 1307, after_sleep=1.0)
        found_cards, text = self._wait_for_ocr_requirements(
            [
                (r"游戏卡珍藏[集级]", 0.90),
                (r"角色游戏卡", 0.70),
                (r"玩法游戏卡", 0.70),
            ],
            timeout=float(self.config.get("PVP 选关页等待秒数", 12.0)),
            name="PVP 选关",
        )
        self.info_set("PVP 选关 OCR", text or "-")
        if not found_cards:
            self.log_info("镜中之战：未确认进入游戏卡选关页面。")
            return False

        self.info_set("当前阶段", "滑动到玩法游戏卡区域")
        for _ in range(PVP_CARD_LIST_SWIPE_COUNT):
            self._drag_entry_reference((94, 1067), (94, 333), duration=0.7, after_sleep=0.5)

        cleared, text = self._wait_for_ocr_absent(
            [r"店长游戏卡\s*\d+\s*/\s*\d+", r"剧情游戏卡\s*\d+\s*/\s*20"],
            timeout=float(self.config.get("PVP 左侧滑动确认秒数", 6.0)),
            name="PVP 左侧滑动",
        )
        self.info_set("PVP 左侧滑动 OCR", text or "-")
        if not cleared:
            self.log_info("镜中之战：左侧列表仍检测到店长游戏卡或剧情游戏卡分类标题。")
            return False

        self.info_set("当前阶段", "定位恶魔城卡带")
        evilcastle, frame_shape = self._find_template_until(
            EVILCASTLE_CARD_TEMPLATE,
            timeout=10.0,
            name="PVP 恶魔城",
        )
        if evilcastle is None or frame_shape is None:
            self.log_info("镜中之战：未检测到 Q_evilcastle.png。")
            return False

        frame_height, frame_width = frame_shape
        center_x = evilcastle.position[0] + evilcastle.size[0] // 2
        center_y = evilcastle.position[1] + evilcastle.size[1] // 2
        target_x = min(frame_width - 2, center_x + frame_width // 2)
        self._drag_client((center_x, center_y), (target_x, center_y), duration=0.8, after_sleep=1.0)

        self.info_set("当前阶段", "选择 PVP 卡带")
        pvp_card, frame_shape = self._find_template_until(
            PVP_ENTRY_CARD_TEMPLATE,
            timeout=10.0,
            name="PVP 入口卡带",
        )
        if pvp_card is None or frame_shape is None:
            pvp_label, frame_shape = self._find_pvp_label_until(timeout=5.0, name="PVP 标签")
            if pvp_label is None or frame_shape is None:
                self.log_info("镜中之战：未检测到 Q_pvp.png，也未通过 OCR 定位 PvP 标签。")
                return False

            frame_height, frame_width = frame_shape
            self._click_client(
                pvp_label[0],
                pvp_label[1],
                frame_width,
                frame_height,
                after_sleep=2.0,
            )
        else:
            frame_height, frame_width = frame_shape
            self._click_client(
                pvp_card.position[0] + pvp_card.size[0] // 2,
                pvp_card.position[1] + pvp_card.size[1] // 2,
                frame_width,
                frame_height,
                after_sleep=2.0,
            )

        self._wait_loading_if_present("PVP 入场")
        self._confirm_rank_drop_if_present()
        if self._wait_for_template(
            PVP_MEDALS_TEMPLATE,
            timeout=float(self.config.get("PVP 入场等待秒数", 30.0)),
            name="PVP 箱庭",
        ):
            self._clear_pvp_hub_notice_if_present()
            return True

        return False

    def _confirm_rank_drop_if_present(self) -> None:
        frame = self.capture_frame()
        text = self._ocr_text(frame, "PVP 段位下滑")
        self.info_set("PVP 段位下滑 OCR", text or "-")
        if self._ocr_pattern_match_count(text, [r"段位下滑", r"确认"]) < 2:
            return

        self.sleep(2.0)
        self._click_screen_reference(*PVP_RANK_DROP_CONFIRM_SCREEN_POINT, after_sleep=0.0)

    def _clear_pvp_hub_notice_if_present(self) -> None:
        frame = self.capture_frame()
        result = self._match(frame, PVP_HUB_NOTICE_TEMPLATE)
        self.info_set("PVP 箱庭感叹号", f"{result.score:.3f}")
        if not self._passes(result, PVP_HUB_NOTICE_TEMPLATE):
            return

        self.sleep(1.0)
        self._click_screen_reference(
            *self._screen_reference_roi_center(PVP_HUB_NOTICE_SCREEN_ROI),
            after_sleep=5.0,
        )

    def _start_auto_battle(self, multiplier: int) -> str:
        self.info_set("当前阶段", "寻找 PVP 舞台")
        if not self._click_template_until(
            PVP_STAGE_TEMPLATE,
            timeout=12.0,
            name="PVP 舞台",
            after_sleep=3.0,
        ):
            self._recover_stage_position()
            if not self._click_template_until(
                PVP_STAGE_TEMPLATE,
                timeout=8.0,
                name="PVP 舞台",
                after_sleep=3.0,
            ):
                self.log_info("镜中之战：未找到 PVP 舞台物件。")
                return "failed"

        self.info_set("当前阶段", "打开自动战斗")
        found_auto, text = self._wait_for_ocr_patterns(
            [r"自动战斗", r"自动"],
            timeout=float(self.config.get("PVP 菜单等待秒数", 12.0)),
            name="PVP 自动战斗",
            roi=(1470, 910, 170, 150),
        )
        self.info_set("PVP 自动战斗 OCR", text or "-")
        if not found_auto:
            return "failed"

        self._click_screen_reference(2026, 1291, after_sleep=1.0)
        found_menu, menu_text = self._wait_for_ocr_patterns(
            [r"鲜血鸡尾酒"],
            timeout=8.0,
            name="PVP 自动战斗菜单",
            roi=self._mf_roi(327, 165, 417, 156),
        )
        self.info_set("PVP 自动战斗 OCR", menu_text or "-")
        if not found_menu:
            return "failed"

        if not self._ensure_free_ap_enabled():
            return "failed"
        self._ensure_multiplier(multiplier)
        self._select_max_battle_count()

        self.info_set("当前阶段", "点击战斗开始")
        self.info_set("PVP 开始战斗 OCR", "跳过前置 OCR，按固定比例点击")
        self._click_screen_reference(1381, 1061, after_sleep=10.0)
        return "started"

    def _ensure_free_ap_enabled(self) -> bool:
        self.info_set("当前阶段", "确认仅用免费鸡尾酒")
        if self._free_ap_switch_on():
            self.info_set("PVP 免费AP", "已开启")
            return True

        self._click_screen_reference(1732, 557, after_sleep=1.0)
        if self._free_ap_switch_on():
            self.info_set("PVP 免费AP", "已开启")
            return True

        self.info_set("PVP 免费AP", "未确认")
        self.log_info("镜中之战：未能确认仅用免费鸡尾酒开关。")
        return False

    def _free_ap_switch_on(self) -> bool:
        frame = self.capture_frame()
        crop = self._crop_screen_reference(frame, FREE_AP_SWITCH_SCREEN_ROI)
        if crop.size == 0:
            return False
        b, g, r = cv2.split(crop)
        yellow = (r > 150) & (g > 110) & (b < 90)
        yellow_ratio = float(np.mean(yellow))
        self.info_set("PVP 免费AP", f"开关黄色占比 {yellow_ratio:.3f}")
        return yellow_ratio > 0.05

    def _ensure_multiplier(self, multiplier: int) -> bool:
        self.info_set("当前阶段", "确认战斗倍率")
        if self._multiplier_matches(multiplier):
            return True

        self._click_screen_reference(1719, 465, after_sleep=0.8)
        if not self._wait_for_ocr_patterns(
            [r"设置.*鲜血鸡尾酒.*消耗量|鲜血鸡尾酒.*消耗量"],
            timeout=8.0,
            name="PVP 倍率设置",
            roi=self._mf_roi(451, 101, 379, 184),
        )[0]:
            self.log_info("镜中之战：未能打开倍率设置。")
            return False

        if multiplier == 40:
            self._click_screen_reference(1584, 715, after_sleep=0.5)
        else:
            self._click_screen_reference(980, 712, after_sleep=0.5)

        for _ in range(10):
            if self._setting_multiplier_matches(multiplier):
                break
            if multiplier == 1:
                break
            self._click_screen_reference(1657, 850, after_sleep=0.5)

        if not self._setting_multiplier_matches(multiplier):
            self.info_set("PVP 倍率 OCR", "未确认")
            return False

        self._click_screen_reference(1383, 1007, after_sleep=1.0)
        return self._multiplier_matches(multiplier, timeout=4.0)

    def _select_max_battle_count(self) -> None:
        self.info_set("当前阶段", "选择最大战斗次数")
        self._click_screen_reference(1650, 850, after_sleep=0.8)

    def _multiplier_matches(self, multiplier: int, timeout: float = 2.0) -> bool:
        found, text = self._wait_for_ocr_patterns(
            [rf"^{multiplier}$", rf"^{multiplier}倍$"],
            timeout=timeout,
            name="PVP 倍率",
            roi=self._mf_roi(844, 186, 96, 36),
            normalize_multiplier=True,
        )
        self.info_set("PVP 倍率 OCR", text or "-")
        return found

    def _setting_multiplier_matches(self, multiplier: int) -> bool:
        found, text = self._wait_for_ocr_patterns(
            [rf"^{multiplier}$", rf"^{multiplier}倍$"],
            timeout=0.8,
            name="PVP 倍率设置值",
            roi=self._mf_roi(596, 372, 105, 50),
            normalize_multiplier=True,
        )
        self.info_set("PVP 倍率 OCR", text or "-")
        return found

    def _wait_result_and_leave(self, multiplier: int) -> bool:
        self.info_set("当前阶段", "等待战斗结算")
        result_timeout = self._result_wait_timeout(multiplier)
        result_found, result_text = self._wait_for_ocr_pattern_majority(
            self._pvp_result_patterns(multiplier),
            min_matches=4,
            timeout=result_timeout,
            name="PVP 结算",
            roi=self._screen_reference_roi_to_reference_roi(PVP_RESULT_SCREEN_ROI),
            extra_wait_patterns=[(r"正在进行", self._mf_roi(50, 576, 203, 69), "PVP 战斗中 OCR")],
        )
        self.info_set("PVP 结算 OCR", result_text or "-")
        if not result_found:
            return False

        self._close_result_page()
        if not self._click_leave_button():
            return False
        if not self._ensure_pvp_hub_after_leave():
            return False
        return self._return_home_from_pvp_hub()

    def _close_result_page(self) -> None:
        self.info_set("当前阶段", "关闭战斗结算")
        self.sleep(1.0)
        self._click_screen_reference(*PVP_RESULT_CLOSE_SCREEN_POINT, after_sleep=0.0)

    def _result_wait_timeout(self, multiplier: int) -> float:
        base_minutes = float(
            self.config.get("PVP 结算基准等待分钟", PVP_RESULT_BASE_MINUTES)
        )
        safe_multiplier = max(1, int(multiplier))
        return base_minutes * 60.0 / safe_multiplier

    def _pvp_result_patterns(self, multiplier: int) -> list[str]:
        safe_multiplier = max(1, int(multiplier))
        completed_count = max(1, round(40 / safe_multiplier))
        return [
            r"反复战斗结果",
            r"胜利分",
            rf"已完成.*{completed_count}.*次.*战斗",
            r"攻击成绩",
            r"积分变化",
            r"斗魂奖牌.*获得量",
        ]

    def _click_leave_button(self) -> bool:
        end_at = monotonic() + float(self.config.get("PVP 离开等待秒数", 20.0))
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(
                frame,
                "pvp_leave",
                roi=self._screen_reference_roi_to_reference_roi(PVP_LEAVE_SCREEN_ROI),
            )
            last_text = text or last_text
            self.info_set("PVP 离开 OCR", text or "-")
            if self._matches_any(text, [r"离开"]):
                self._click_screen_reference(*PVP_LEAVE_BUTTON_SCREEN_POINT, after_sleep=2.0)
                return True

            fail_text = self._ocr_text(
                frame,
                "pvp_leave_fail",
                roi=self._mf_roi(650, 600, 180, 110),
            )
            if self._matches_any(fail_text, [r"离开"]):
                self.info_set("PVP 离开 OCR", fail_text)
                self._click_screen_reference(1440, 1309, after_sleep=2.0)
                return True

            self.sleep(0.5)

        self.info_set("PVP 离开 OCR", last_text or "-")
        return False

    def _ensure_pvp_hub_after_leave(self) -> bool:
        self.info_set("当前阶段", "确认离开结果")
        timeout = float(self.config.get("PVP 返回箱庭等待秒数", 10.0))
        state, text = self._wait_for_pvp_hub_or_confirm(timeout=timeout)
        if state == "hub":
            return True

        if state == "confirm":
            self.info_set("PVP 升降级确认 OCR", text or "-")
        else:
            self.info_set("PVP 升降级确认 OCR", text or "-")
            self.info_set("PVP 返回主页", "未确认 PVP 箱庭，尝试确认")

        self._click_screen_reference(
            *self._screen_reference_roi_center(PVP_CONFIRM_BUTTON_SCREEN_ROI),
            after_sleep=1.0,
        )
        return self._wait_for_template(
            PVP_MEDALS_TEMPLATE,
            timeout=timeout,
            name="PVP 箱庭",
        )

    def _wait_for_pvp_hub_or_confirm(
        self,
        timeout: float,
        interval: float = 0.5,
    ) -> tuple[str, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        last_hub_score = -1.0
        confirm_roi = self._screen_reference_roi_to_reference_roi(PVP_CONFIRM_BUTTON_SCREEN_ROI)
        while monotonic() <= end_at:
            frame = self.capture_frame()

            hub = self._match(frame, PVP_MEDALS_TEMPLATE)
            last_hub_score = hub.score
            self.info_set("PVP 箱庭", f"{hub.score:.3f}")
            if self._passes(hub, PVP_MEDALS_TEMPLATE):
                self.info_set("PVP 返回主页", "已回到 PVP 箱庭")
                return "hub", last_text

            text = self._ocr_text(frame, "PVP 升降级确认", roi=confirm_roi)
            last_text = text or last_text
            self.info_set("PVP 升降级确认 OCR", text or "-")
            if self._matches_any(text, [r"确认"]):
                return "confirm", text

            self.sleep(interval)

        self.info_set("PVP 箱庭", f"{last_hub_score:.3f}")
        return "timeout", last_text

    def _return_home_from_pvp_hub(self) -> bool:
        self.info_set("当前阶段", "返回主页")
        in_pvp_hub = self._wait_for_template(
            PVP_MEDALS_TEMPLATE,
            timeout=float(self.config.get("PVP 返回箱庭等待秒数", 10.0)),
            name="PVP 箱庭",
        )
        if not in_pvp_hub:
            self.info_set("PVP 返回主页", "未确认 PVP 箱庭")
            return self._wait_for_home(
                timeout=float(self.config.get("PVP 返回主页等待秒数", 20.0))
            )

        self.info_set("PVP 返回主页", "已确认 PVP 箱庭")
        self._click_reference(*PVP_BACK_HOME_REFERENCE_POINT, after_sleep=2.0)
        self._wait_loading_if_present("PVP 返回主页")
        home_ok = self._wait_for_home(
            timeout=float(self.config.get("PVP 返回主页等待秒数", 20.0))
        )
        self.info_set("PVP 返回主页", "通过" if home_ok else "失败")
        return home_ok

    def _wait_for_home(self, timeout: float, interval: float = 0.5) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            ratio = self._home_brightness_ratio(self.capture_frame())
            self.info_set("主页亮度", f"{ratio:.3f}")
            if ratio >= self._home_ratio_threshold():
                return True
            self.sleep(interval)
        return False

    def _recover_stage_position(self) -> None:
        if self._click_template_until(
            PVP_LOC_RESET_TEMPLATE,
            timeout=2.0,
            name="PVP 定位修正",
            target_offset=(0, 100),
            after_sleep=6.0,
        ):
            return

        for spec in PVP_NO_FIND_TEMPLATES:
            if self._click_template_until(
                spec,
                timeout=0.8,
                name="PVP 舞台搜索",
                after_sleep=5.0,
            ):
                return

    def _try_pass_workaround(self) -> None:
        self.log_info("镜中之战：快速卡带入场失败，尝试通行证路径兜底。")
        self._click_reference(1063, 210, after_sleep=1.0)
        if not self._wait_for_ocr_patterns(
            [r"通行证"],
            timeout=5.0,
            name="PVP 通行证",
            roi=(217, 14, 101, 49),
        )[0]:
            return

        for _ in range(4):
            frame = self.capture_frame()
            text = self._ocr_text(frame, "pvp_pass_list", roi=(610, 151, 492, 362))
            if self._matches_any(text, [r"镜中之战|PVP|战斗"]):
                self._click_reference(1030, 320, after_sleep=2.0)
                self._wait_loading_if_present("通行证进入 PVP")
                return
            frame_height, frame_width = frame.shape[:2]
            self._drag_client(
                (round(frame_width * 0.5), round(frame_height * 0.72)),
                (round(frame_width * 0.5), round(frame_height * 0.35)),
                duration=0.6,
                after_sleep=1.0,
            )

    def _click_template_until(
        self,
        spec: PVPTemplateSpec,
        timeout: float,
        name: str,
        target: tuple[int, int] | None = None,
        target_offset: tuple[int, int] = (0, 0),
        after_sleep: float = 0.0,
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
                if target is not None:
                    self._click_reference(target[0], target[1], after_sleep=after_sleep)
                else:
                    x = result.position[0] + result.size[0] // 2 + target_offset[0]
                    y = result.position[1] + result.size[1] // 2 + target_offset[1]
                    frame_height, frame_width = frame.shape[:2]
                    self._click_client(x, y, frame_width, frame_height, after_sleep=after_sleep)
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _find_template_until(
        self,
        spec: PVPTemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> tuple[PVPMatchResult | None, tuple[int, int] | None]:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                frame_height, frame_width = frame.shape[:2]
                return result, (frame_height, frame_width)
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return None, None

    def _find_pvp_label_until(
        self,
        timeout: float,
        name: str,
        interval: float = 0.5,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            boxes = self._ocr_boxes(frame, name=name)
            text = " ".join(getattr(box, "name", "") for box in boxes if getattr(box, "name", ""))
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            point = self._pvp_label_click_point(boxes, frame_width, frame_height)
            if point is not None:
                return point, (frame_height, frame_width)
            self.sleep(interval)

        self.info_set(f"{name} OCR", last_text or "-")
        return None, None

    def _wait_for_template(
        self,
        spec: PVPTemplateSpec,
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

    def _wait_for_ocr_patterns(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
        normalize_multiplier: bool = False,
        extra_wait_patterns: list[tuple[str, tuple[int, int, int, int], str]] | None = None,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name, roi=roi)
            if normalize_multiplier:
                text = self._normalize_multiplier_text(text)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if self._matches_any(text, patterns):
                return True, text

            for pattern, extra_roi, info_key in extra_wait_patterns or []:
                extra_text = self._ocr_text(frame, name=info_key, roi=extra_roi)
                if self._matches_any(extra_text, [pattern]):
                    self.info_set(info_key, extra_text)
                    break
            self.sleep(interval)

        return False, last_text

    def _wait_for_ocr_pattern_majority(
        self,
        patterns: list[str],
        min_matches: int,
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
        extra_wait_patterns: list[tuple[str, tuple[int, int, int, int], str]] | None = None,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name, roi=roi)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            match_count = self._ocr_pattern_match_count(text, patterns)
            self.info_set("PVP 结算命中", f"{match_count}/{len(patterns)}")
            if match_count >= min_matches:
                return True, text

            for pattern, extra_roi, info_key in extra_wait_patterns or []:
                extra_text = self._ocr_text(frame, name=info_key, roi=extra_roi)
                if self._matches_any(extra_text, [pattern]):
                    self.info_set(info_key, extra_text)
                    break
            self.sleep(interval)

        return False, last_text

    def _wait_for_ocr_requirements(
        self,
        requirements: list[tuple[str, float]],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            entries = self._ocr_entries(frame, name=name, roi=roi)
            text = " ".join(label for label, _confidence in entries)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if self._ocr_requirements_met(entries, requirements):
                return True, text
            self.sleep(interval)

        return False, last_text

    def _wait_for_ocr_absent(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name, roi=roi)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if text and not self._matches_any(text, patterns):
                return True, text
            self.sleep(interval)

        return False, last_text

    def _wait_loading_if_present(self, name: str, interval: float = 0.5) -> None:
        found_loading = self._wait_for_template(
            LOADING_TEMPLATE,
            timeout=float(self.config.get("loading 出现等待秒数", 6.0)),
            name=f"{name}_loading_appear",
            interval=interval,
        )
        if not found_loading:
            return

        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{name}_loading_gone", f"{result.score:.3f}")
            if not self._passes(result, LOADING_TEMPLATE):
                return
            self.sleep(interval)

    def _match(self, frame, spec: PVPTemplateSpec) -> PVPMatchResult:
        empty = PVPMatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            template, mask = self._load_template(spec)
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty

        try:
            frame_gray = self._to_gray(frame)
            roi_left, roi_top, roi_frame = self._roi_frame(frame_gray, spec.roi)
            frame_height, frame_width = roi_frame.shape[:2]
            base_scale = frame_gray.shape[1] / REFERENCE_WIDTH
            best = empty

            for scale in self._candidate_scales(base_scale):
                scaled_template = self._resize_template(template, scale)
                scaled_mask = self._resize_mask(mask, scale) if mask is not None else None
                height, width = scaled_template.shape[:2]
                if height < 5 or width < 5 or height > frame_height or width > frame_width:
                    continue

                method = cv2.TM_CCORR_NORMED if scaled_mask is not None else cv2.TM_CCOEFF_NORMED
                result = cv2.matchTemplate(roi_frame, scaled_template, method, mask=scaled_mask)
                _, max_value, _, max_location = cv2.minMaxLoc(result)
                if max_value > best.score:
                    x, y = int(max_location[0]), int(max_location[1])
                    region = roi_frame[y : y + height, x : x + width]
                    best = PVPMatchResult(
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

    def _home_brightness_ratio(self, frame) -> float:
        return max(self._home_brightness_ratio_for_template(frame, spec) for spec in HOME_TEMPLATES)

    def _home_brightness_ratio_for_template(self, frame, spec: PVPTemplateSpec) -> float:
        template, mask = self._load_template(spec)
        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = frame_width / ENTRY_REFERENCE_WIDTH
        template_height, template_width = template.shape[:2]
        roi_width = max(8, round(template_width * scale))
        roi_height = max(8, round(template_height * scale))
        center_x = round(frame_width * (222 / ENTRY_REFERENCE_WIDTH))
        center_y = round(frame_height * (211 / ENTRY_REFERENCE_HEIGHT))
        left = max(0, center_x - roi_width // 2)
        top = max(0, center_y - roi_height // 2)
        right = min(frame_width, left + roi_width)
        bottom = min(frame_height, top + roi_height)
        region = frame_gray[top:bottom, left:right]
        if region.size == 0:
            return 0.0

        scaled_template = self._resize_template(template, scale)
        scaled_mask = self._resize_mask(mask, scale) if mask is not None else None
        match_height = min(region.shape[0], scaled_template.shape[0])
        match_width = min(region.shape[1], scaled_template.shape[1])
        if match_height <= 0 or match_width <= 0:
            return 0.0
        region = region[:match_height, :match_width]
        scaled_template = scaled_template[:match_height, :match_width]
        if scaled_mask is not None:
            scaled_mask = scaled_mask[:match_height, :match_width]
            active = scaled_mask > 0
            if not np.any(active):
                return 0.0
            template_mean = float(np.mean(scaled_template[active]))
            region_mean = float(np.mean(region[active]))
        else:
            template_mean = float(np.mean(scaled_template))
            region_mean = float(np.mean(region))
        if template_mean <= 0:
            return 0.0
        return float(region_mean / template_mean)

    def _load_template(self, spec: PVPTemplateSpec) -> tuple[np.ndarray, np.ndarray | None]:
        if spec.name in self._templates:
            return self._templates[spec.name]

        path = TEMPLATE_DIR / spec.file_name
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise RuntimeError(f"PVP 模板不存在或无法读取：{path}")

        mask = green_mask_from_template(raw) if spec.green_mask else None
        template = self._to_gray(raw)
        if mask is not None and np.count_nonzero(mask) == mask.size:
            mask = None

        self._templates[spec.name] = (template, mask)
        return self._templates[spec.name]

    def _passes(self, result: PVPMatchResult, spec: PVPTemplateSpec) -> bool:
        threshold = float(self.config.get(spec.threshold_key, spec.default_threshold))
        return result.score >= threshold

    def _ocr_text(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> str:
        return " ".join(label for label, _confidence in self._ocr_entries(frame, name, roi))

    def _ocr_entries(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> list[tuple[str, float]]:
        boxes = self._ocr_boxes(frame, name=name, roi=roi)
        entries = []
        for box in boxes:
            label = getattr(box, "name", "")
            if not label:
                continue
            confidence = float(getattr(box, "confidence", 1.0))
            if confidence > 1.0:
                confidence /= 100.0
            entries.append((label, confidence))
        return entries

    def _ocr_boxes(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ):
        ocr_frame = self._crop_reference(frame, roi) if roi is not None else frame
        try:
            return self.ocr(
                frame=ocr_frame,
                threshold=float(self.config.get("PVP OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return []

    def _click_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    @staticmethod
    def _mf_point(x: int, y: int) -> tuple[int, int]:
        return (
            round(x * REFERENCE_WIDTH / MFABD2_REFERENCE_WIDTH),
            round(y * REFERENCE_HEIGHT / MFABD2_REFERENCE_HEIGHT),
        )

    @staticmethod
    def _mf_roi(x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
        left, top = PVPTask._mf_point(x, y)
        right, bottom = PVPTask._mf_point(x + width, y + height)
        return left, top, max(1, right - left), max(1, bottom - top)

    def _click_mf_reference(self, x: int, y: int, after_sleep: float = 0.0):
        scaled_x, scaled_y = self._mf_point(x, y)
        self._click_reference(scaled_x, scaled_y, after_sleep=after_sleep)

    def _click_entry_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / ENTRY_REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / ENTRY_REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _click_screen_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / ENTRY_REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / ENTRY_REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    @staticmethod
    def _screen_roi_frame(
        frame: np.ndarray,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, np.ndarray]:
        if roi is None:
            return 0, 0, frame
        height, width = frame.shape[:2]
        x, y, w, h = roi
        scale_x = width / ENTRY_REFERENCE_WIDTH
        scale_y = height / ENTRY_REFERENCE_HEIGHT
        left = max(0, round(x * scale_x))
        top = max(0, round(y * scale_y))
        right = min(width, round((x + w) * scale_x))
        bottom = min(height, round((y + h) * scale_y))
        return left, top, frame[top:bottom, left:right]

    @staticmethod
    def _crop_screen_reference(frame, roi: tuple[int, int, int, int] | None):
        if roi is None:
            return frame
        _, _, crop = PVPTask._screen_roi_frame(frame, roi)
        return crop

    def _click_client(
        self,
        x: int,
        y: int,
        frame_width: int,
        frame_height: int,
        after_sleep: float = 0.0,
    ):
        self.operate_click(
            max(0.0, min(1.0, x / max(1, frame_width))),
            max(0.0, min(1.0, y / max(1, frame_height))),
            after_sleep=after_sleep,
        )

    def _drag_entry_reference(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        frame = self.capture_frame()
        frame_height, frame_width = frame.shape[:2]
        start_client = (
            round(frame_width * start[0] / ENTRY_REFERENCE_WIDTH),
            round(frame_height * start[1] / ENTRY_REFERENCE_HEIGHT),
        )
        end_client = (
            round(frame_width * end[0] / ENTRY_REFERENCE_WIDTH),
            round(frame_height * end[1] / ENTRY_REFERENCE_HEIGHT),
        )
        self._drag_client(start_client, end_client, duration=duration, after_sleep=after_sleep)

    def _drag_client(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
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

    def _post_drag_client(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
    ) -> bool:
        interaction = getattr(getattr(self, "executor", None), "interaction", None)
        if interaction is None or not hasattr(interaction, "post"):
            return False

        try:
            import win32api
            import win32con
        except ImportError:
            return False

        steps = max(6, round(duration / 0.03))

        def post_drag_messages() -> None:
            start_pos = win32api.MAKELONG(int(start[0]), int(start[1]))
            interaction.post(win32con.WM_MOUSEMOVE, 0, start_pos)
            interaction.post(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, start_pos)
            try:
                for index in range(1, steps + 1):
                    ratio = index / steps
                    x = round(start[0] + (end[0] - start[0]) * ratio)
                    y = round(start[1] + (end[1] - start[1]) * ratio)
                    move_pos = win32api.MAKELONG(x, y)
                    interaction.post(win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, move_pos)
                    time.sleep(duration / steps if duration > 0 else 0)
            finally:
                end_pos = win32api.MAKELONG(int(end[0]), int(end[1]))
                interaction.post(win32con.WM_LBUTTONUP, 0, end_pos)

        lock = getattr(interaction, "_input_lock", None)
        if lock is None:
            post_drag_messages()
        else:
            with lock:
                post_drag_messages()
        return True

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("主页亮度比例阈值", 0.75))

    def _ocr_requirements_met(
        self,
        entries: list[tuple[str, float]],
        requirements: list[tuple[str, float]],
    ) -> bool:
        combined_text = " ".join(label for label, _confidence in entries)
        for pattern, min_confidence in requirements:
            if not self._ocr_requirement_met(entries, combined_text, pattern, min_confidence):
                return False
        return True

    def _ocr_requirement_met(
        self,
        entries: list[tuple[str, float]],
        combined_text: str,
        pattern: str,
        min_confidence: float,
    ) -> bool:
        normalized_pattern = self._normalize_text(pattern)
        for label, confidence in entries:
            if re.search(normalized_pattern, self._normalize_text(label), flags=re.IGNORECASE):
                return confidence >= min_confidence

        if not re.search(
            normalized_pattern,
            self._normalize_text(combined_text),
            flags=re.IGNORECASE,
        ):
            return False
        return any(confidence >= min_confidence for _label, confidence in entries)

    @staticmethod
    def _matches_any(text: str, patterns: list[str]) -> bool:
        normalized = PVPTask._normalize_text(text)
        for pattern in patterns:
            normalized_pattern = PVPTask._normalize_text(pattern)
            if re.search(normalized_pattern, normalized, flags=re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _ocr_pattern_match_count(text: str, patterns: list[str]) -> int:
        return sum(1 for pattern in patterns if PVPTask._matches_any(text, [pattern]))

    @staticmethod
    def _pvp_label_click_point(
        boxes,
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int] | None:
        candidates = []
        for box in boxes:
            if PVPTask._normalize_text(getattr(box, "name", "")) != "pvp":
                continue
            x = getattr(box, "x", None)
            y = getattr(box, "y", None)
            width = getattr(box, "width", None)
            height = getattr(box, "height", None)
            if None in (x, y, width, height):
                continue

            center_x = int(round(float(x) + float(width) / 2))
            center_y = int(round(float(y) + float(height) / 2))
            if center_y < frame_height * 0.50:
                continue
            candidates.append((center_x, center_y, float(x)))

        if not candidates:
            return None

        center_x, center_y, _left = min(candidates, key=lambda item: item[2])
        click_y = int(round(center_y - frame_height * 0.085))
        return center_x, max(0, click_y)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(str(text).lower().split())

    @staticmethod
    def _normalize_multiplier_text(text: str) -> str:
        normalized = PVPTask._normalize_text(text)
        return normalized.replace("倍", "").replace("o", "0")

    def _target_multiplier(self) -> int:
        raw = str(self.config.get("竞技场战斗倍数", 1)).replace("倍", "")
        try:
            multiplier = int(raw)
        except ValueError:
            multiplier = 1
        return multiplier if multiplier in {1, 4, 5, 10, 20, 40} else 1

    @staticmethod
    def _candidate_scales(base_scale: float) -> list[float]:
        offsets = (0.0, -0.08, 0.08, -0.16, 0.16)
        candidates = [1.0]
        candidates.extend(max(0.2, base_scale + offset) for offset in offsets)

        unique = []
        for scale in candidates:
            rounded = round(scale, 3)
            if rounded not in unique:
                unique.append(rounded)
        return unique

    @staticmethod
    def _resize_template(template: np.ndarray, scale: float) -> np.ndarray:
        if abs(scale - 1.0) < 0.001:
            return template
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        return cv2.resize(template, None, fx=scale, fy=scale, interpolation=interpolation)

    @staticmethod
    def _resize_mask(mask: np.ndarray | None, scale: float) -> np.ndarray | None:
        if mask is None:
            return None
        if abs(scale - 1.0) < 0.001:
            return mask
        resized = cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        return np.where(resized > 0, 255, 0).astype(np.uint8)

    @staticmethod
    def _to_gray(frame) -> np.ndarray:
        if len(frame.shape) == 2:
            return frame
        if frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _pixel_similarity(
        region: np.ndarray,
        template: np.ndarray,
        mask: np.ndarray | None = None,
    ) -> float:
        if region.shape != template.shape:
            return -1.0
        diff = np.abs(region.astype(np.float32) - template.astype(np.float32))
        if mask is not None:
            active = mask > 0
            if not np.any(active):
                return -1.0
            diff = diff[active]
        return float(1.0 - np.mean(diff) / 255.0)

    @staticmethod
    def _roi_frame(
        frame: np.ndarray,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, np.ndarray]:
        if roi is None:
            return 0, 0, frame
        height, width = frame.shape[:2]
        x, y, w, h = roi
        scale_x = width / REFERENCE_WIDTH
        scale_y = height / REFERENCE_HEIGHT
        left = max(0, round(x * scale_x))
        top = max(0, round(y * scale_y))
        right = min(width, round((x + w) * scale_x))
        bottom = min(height, round((y + h) * scale_y))
        return left, top, frame[top:bottom, left:right]

    @staticmethod
    def _crop_reference(frame, roi: tuple[int, int, int, int] | None):
        if roi is None:
            return frame
        _, _, crop = PVPTask._roi_frame(frame, roi)
        return crop

    @staticmethod
    def _screen_reference_roi_to_reference_roi(
        roi: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        x, y, width, height = roi
        left = round(x * REFERENCE_WIDTH / ENTRY_REFERENCE_WIDTH)
        top = round(y * REFERENCE_HEIGHT / ENTRY_REFERENCE_HEIGHT)
        right = round((x + width) * REFERENCE_WIDTH / ENTRY_REFERENCE_WIDTH)
        bottom = round((y + height) * REFERENCE_HEIGHT / ENTRY_REFERENCE_HEIGHT)
        return left, top, max(1, right - left), max(1, bottom - top)

    @staticmethod
    def _screen_reference_roi_center(roi: tuple[int, int, int, int]) -> tuple[int, int]:
        x, y, width, height = roi
        return int(x + width / 2 + 0.5), int(y + height / 2 + 0.5)


LOADING_TEMPLATE = PVPTemplateSpec(
    name="loading",
    file_name="loading.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

HOME_TEMPLATE = PVPTemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
)

HOME_ICE_TEMPLATE = PVPTemplateSpec(
    name="home_ice",
    file_name="MainHomeIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_RICE_TEMPLATE = PVPTemplateSpec(
    name="home_rice",
    file_name="MainHomeRIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_TEMPLATES = (HOME_TEMPLATE, HOME_ICE_TEMPLATE, HOME_RICE_TEMPLATE)

EVILCASTLE_CARD_TEMPLATE = PVPTemplateSpec(
    name="evilcastle_card",
    file_name="Q_evilcastle.png",
    threshold_key="PVP 恶魔城阈值",
    default_threshold=0.70,
)

PVP_ENTRY_CARD_TEMPLATE = PVPTemplateSpec(
    name="pvp_entry_card",
    file_name="Q_pvp.png",
    threshold_key="PVP 入口卡带阈值",
    default_threshold=0.78,
)

QUICK_CART_PVP_TEMPLATE = PVPTemplateSpec(
    name="quick_cart_pvp",
    file_name="pvp-quick-cart.png",
    threshold_key="PVP 快速卡带阈值",
    default_threshold=0.78,
    roi=(51, 582, 873, 133),
    green_mask=True,
)

PVP_MEDALS_TEMPLATE = PVPTemplateSpec(
    name="pvp_medals",
    file_name="pvp-medals.png",
    threshold_key="PVP 箱庭阈值",
    default_threshold=0.78,
    roi=(612, 29, 173, 32),
)

PVP_HUB_NOTICE_TEMPLATE = PVPTemplateSpec(
    name="pvp_hub_notice",
    file_name="tanhaoGE.png",
    threshold_key="PVP 箱庭感叹号阈值",
    default_threshold=0.72,
    roi=PVPTask._screen_reference_roi_to_reference_roi(PVP_HUB_NOTICE_SCREEN_ROI),
)

PVP_STAGE_TEMPLATE = PVPTemplateSpec(
    name="pvp_stage",
    file_name="pvp-stage.png",
    threshold_key="PVP 舞台阈值",
    default_threshold=0.72,
    roi=(190, 238, 900, 620),
)

PVP_LOC_RESET_TEMPLATE = PVPTemplateSpec(
    name="pvp_loc_reset",
    file_name="pvp-loc-reset.png",
    threshold_key="PVP 定位修正阈值",
    default_threshold=0.76,
)

PVP_NO_FIND_TEMPLATES = [
    PVPTemplateSpec(
        name="pvp_nofind_UT_bk",
        file_name="pvp-nofind-UT-bk.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    PVPTemplateSpec(
        name="pvp_nofind_ut_bk2",
        file_name="pvp-nofind-ut-bk2.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    PVPTemplateSpec(
        name="pvp_nofind_UT_ft",
        file_name="pvp-nofind-UT-ft.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    PVPTemplateSpec(
        name="pvp_nofind_UT_Rt",
        file_name="pvp-nofind-UT-Rt.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    PVPTemplateSpec(
        name="pvp_nofind_twoaudience",
        file_name="pvp-nofind-twoaudience.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    PVPTemplateSpec(
        name="pvp_nofind_waiter_fr",
        file_name="pvp-nofind-waiter-fr.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    PVPTemplateSpec(
        name="pvp_nofind_aman_sit",
        file_name="pvp-nofind-aman-sit.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
]
