import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.utils import task_vision
from src.utils.calibration import FHD_1080, HD_720, QHD_1440
from src.utils.home_confirmation import (
    HOME_GACHA_OCR_REFERENCE_ROI,
    home_confirmation_passes,
)
from src.utils.image_utils import (
    best_pixel_valid_match,
    masked_zncc,
    pixel_similarity,
    reference_roi_frame,
    relative_roi_frame,
    resize_mask,
    resize_template,
    stabilize_template_match,
    template_match_response,
    to_gray,
)
from src.utils.ocr_utils import normalize_ocr_text
from src.utils.template_resolution import offline_template_scale

REFERENCE_WIDTH = FHD_1080.width
REFERENCE_HEIGHT = FHD_1080.height
HD720_REFERENCE_WIDTH = HD_720.width
HD720_REFERENCE_HEIGHT = HD_720.height
ENTRY_REFERENCE_WIDTH = QHD_1440.width
ENTRY_REFERENCE_HEIGHT = QHD_1440.height
RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS = 3.0
RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS = 3
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
RECENT_PVP_CARTRIDGE_TEMPLATE_FILE = "cartridge-image2-left-lower-cutout.png"
RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD = 0.85
FREE_AP_SWITCH_SCREEN_ROI = (1680, 535, 120, 55)
PVP_RESULT_SCREEN_ROI = (932, 368, 699, 704)
PVP_RESULT_CLOSE_SCREEN_POINT = (1585, 410)
PVP_FAILURE_LEAVE_REFERENCE_ROI = (696, 952, 535, 87)
PVP_SUCCESS_LEAVE_REFERENCE_ROI = (1594, 987, 240, 66)
PVP_BACK_HOME_REFERENCE_POINT = (100, 54)
PVP_HUB_NOTICE_SCREEN_ROI = (1381, 865, 62, 45)
GAMEPLAY_CARTRIDGE_POINT = (988 / REFERENCE_WIDTH, 876 / REFERENCE_HEIGHT)
PVP_CARTRIDGE_SLOT_POINT = (152 / REFERENCE_WIDTH, 970 / REFERENCE_HEIGHT)
PVP_AUTO_BATTLE_SCREEN_ROI = (1470, 910, 170, 150)
PVP_AUTO_BATTLE_CLICK_REFERENCE = (2026, 1291)
PVP_STAGE_CLICK_REFERENCE_OFFSET = (0, -75)
PVP_RESULT_BASE_MINUTES = 20.0
PVP_SEASON_REWARD_AFTER_CLICK_SECONDS = 3.0
PVP_RANK_PAGE_AFTER_CLICK_SECONDS = 2.0
PVP_HUB_SPECIAL_PAGE_GRACE_SECONDS = 2.0
QUICK_SWITCH_PAGE_PATTERNS = (r"最近", r"剧情游戏卡", r"玩法游戏卡")
HOME_GACHA_OCR_ROI = HOME_GACHA_OCR_REFERENCE_ROI
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"


@dataclass(frozen=True)
class RecentPvpCartridgeMatch:
    score: float = -1.0
    pixel_score: float = -1.0
    zncc_score: float = -1.0
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)

    @property
    def passed(self) -> bool:
        return (
            self.score >= RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD
            and self.pixel_score >= RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD
            and self.zncc_score >= RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD
        )


class PVPTask(BaseBD2Task):
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "目标倍率",
        "主页小屋按钮",
        "主页亮度",
        "主页抽抽乐 OCR",
        "快速切换按钮",
        "卡带选择页 OCR",
        "卡带选择页 OCR 命中",
        "PVP 箱庭",
        "PVP 入场特殊页面模式",
        "PVP 入场特殊页面 OCR",
        "PVP 箱庭感叹号",
        "PVP 舞台",
        "PVP 自动战斗 OCR",
        "PVP 自动战斗点击",
        "PVP 免费AP",
        "PVP 倍率 OCR",
        "PVP 开始战斗 OCR",
        "PVP 战斗中 OCR",
        "PVP 结算 OCR",
        "PVP 结算命中",
        "PVP 离开 OCR",
        "PVP 离开点击",
        "PVP 升降级确认 OCR",
        "PVP 升降级确认",
        "PVP 返回主页",
        "PVP AP不足 OCR",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]

    status_key_labels = {
        "快速切换按钮": "快速切换按钮模板",
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
        self._recent_pvp_cartridge_template_cache: (
            tuple[np.ndarray, np.ndarray] | None
        ) = None
        self.default_config.update(
            {
                "启用": True,
                "竞技场战斗倍数": 1,
                "最多战斗轮次": 12,
                "加载页面阈值": 0.72,
                "主页亮度比例阈值": 0.75,
                "PVP OCR 阈值": 0.2,
                "主页确认等待秒数": 10.0,
                "快速卡带等待秒数": 10.0,
                "卡带选择页确认等待秒数": 10.0,
                "PVP 入场等待秒数": 30.0,
                "PVP 菜单等待秒数": 12.0,
                "PVP 战斗开始等待秒数": 30.0,
                "PVP 结算基准等待分钟": PVP_RESULT_BASE_MINUTES,
                "PVP 离开等待秒数": 20.0,
                "PVP 返回箱庭等待秒数": 10.0,
                "PVP 返回主页等待秒数": 20.0,
                "主页小屋按钮阈值": 0.70,
                "快速切换按钮阈值": 0.88,
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
                "主页小屋按钮阈值": "进入卡带前确认主页小屋按钮存在的模板匹配阈值。",
                "快速切换按钮阈值": "识别 QuickSwitchPlayIco.png 快速切换按钮的模板匹配阈值。",
                "卡带选择页确认等待秒数": (
                    "点击快速切换按钮后，等待 OCR 同时识别最近、剧情游戏卡和玩法游戏卡的时限。"
                ),
                "PVP 箱庭感叹号阈值": "进入 PVP 箱庭后识别 tanhaoGE.png 的模板匹配阈值。",
            }
        )

    def _recent_cartridge_is_pvp(self) -> bool:
        frame = self.capture_frame()
        result = self._match_recent_pvp_cartridge(frame)
        verdict = "PVP" if result.passed else "非 PVP"
        self.info_set(
            "最近卡带 PVP 模板",
            (
                f"{verdict} m={result.score:.3f} p={result.pixel_score:.3f} "
                f"z={result.zncc_score:.3f} box={result.position}+{result.size}"
            ),
        )
        return result.passed

    def _match_recent_pvp_cartridge(
        self,
        frame: np.ndarray,
    ) -> RecentPvpCartridgeMatch:
        template, mask = self._load_recent_pvp_cartridge_template()
        frame_gray = to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = offline_template_scale(
            RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
            frame_width,
            frame_height,
        )
        scaled_template = resize_template(template, scale)
        scaled_mask = resize_mask(mask, scale)
        height, width = scaled_template.shape[:2]
        if (
            height < 5
            or width < 5
            or height > frame_height
            or width > frame_width
        ):
            return RecentPvpCartridgeMatch(size=(width, height))

        try:
            response = template_match_response(
                frame_gray,
                scaled_template,
                scaled_mask,
            )
        except cv2.error as exc:
            raise RuntimeError(f"最近卡带 PVP 模板匹配失败：{exc}") from exc

        candidate = best_pixel_valid_match(
            response,
            frame_gray,
            scaled_template,
            scaled_mask,
            template_threshold=RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD,
            pixel_threshold=RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
            zncc_threshold=RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
        )
        if candidate is not None:
            return RecentPvpCartridgeMatch(
                score=candidate.score,
                pixel_score=candidate.pixel_score,
                zncc_score=candidate.zncc_score,
                position=candidate.location,
                size=(width, height),
            )

        _minimum, score, _minimum_location, location = cv2.minMaxLoc(response)
        x, y = location
        region = frame_gray[y : y + height, x : x + width]
        return RecentPvpCartridgeMatch(
            score=float(score),
            pixel_score=pixel_similarity(region, scaled_template, scaled_mask),
            zncc_score=masked_zncc(region, scaled_template, scaled_mask),
            position=(int(x), int(y)),
            size=(width, height),
        )

    def _load_recent_pvp_cartridge_template(
        self,
    ) -> tuple[np.ndarray, np.ndarray]:
        cached = getattr(self, "_recent_pvp_cartridge_template_cache", None)
        if cached is not None:
            return cached

        path = TEMPLATE_DIR / RECENT_PVP_CARTRIDGE_TEMPLATE_FILE
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise RuntimeError(f"最近卡带 PVP 模板不存在或无法读取：{path}")
        if raw.ndim != 3 or raw.shape[2] < 4:
            raise RuntimeError(f"最近卡带 PVP 模板缺少 Alpha 通道：{path}")

        mask = np.where(raw[:, :, 3] > 0, 255, 0).astype(np.uint8)
        active_pixels = int(np.count_nonzero(mask))
        if active_pixels <= 0 or active_pixels >= mask.size:
            raise RuntimeError(f"最近卡带 PVP 模板 Alpha 遮罩无效：{path}")

        cached = (to_gray(raw), mask)
        self._recent_pvp_cartridge_template_cache = cached
        return cached

    def _handle_recent_cartridge_special_pages(
        self,
        timeout: float = RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS,
        interval: float = 0.25,
        allow_season_reward: bool | None = None,
    ) -> bool:
        """OCR and dismiss PVP promotion, demotion, and season reward pages."""
        if allow_season_reward is None:
            allow_season_reward = self._is_beijing_monday()
        end_at = monotonic() + max(0.0, float(timeout))
        handled: set[str] = set()
        action_count = 0

        while True:
            boxes = self._recent_cartridge_ocr_boxes()
            text, action_name, target_box = self._pvp_special_page_action(
                boxes,
                allow_season_reward=allow_season_reward,
            )
            self.info_set("最近卡带特殊页面 OCR", text or "-")

            if action_name and action_name not in handled and target_box is not None:
                point = self._ocr_box_center(target_box)
                if point is not None:
                    frame_width = max(1, int(self.width))
                    frame_height = max(1, int(self.height))
                    self.info_set("当前阶段", f"处理最近卡带{action_name}")
                    self.operate_click(
                        max(0.0, min(1.0, point[0] / frame_width)),
                        max(0.0, min(1.0, point[1] / frame_height)),
                        after_sleep=0.5,
                    )
                    handled.add(action_name)
                    action_count += 1

            if (
                monotonic() >= end_at
                or action_count >= RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS
            ):
                break
            self.sleep(max(0.0, float(interval)))

        return bool(handled)

    @classmethod
    def _pvp_special_page_action(
        cls,
        boxes: list,
        *,
        allow_season_reward: bool,
    ) -> tuple[str, str, object | None]:
        """Return a strict paired PVP special-page action from one OCR frame."""
        text = " ".join(
            str(getattr(box, "name", ""))
            for box in boxes
            if getattr(box, "name", "")
        )
        normalized = normalize_ocr_text(text)
        if (
            allow_season_reward
            and "赛季奖励" in normalized
            and "点击画面即可返回" in normalized
        ):
            return text, "赛季奖励", cls._find_ocr_box(boxes, "点击画面即可返回")
        if "恭喜晋级" in normalized and "确认" in normalized:
            return text, "恭喜晋级", cls._find_ocr_box(boxes, "确认")
        if "段位下滑" in normalized and "确认" in normalized:
            return text, "段位下滑", cls._find_ocr_box(boxes, "确认")
        return text, "", None

    @staticmethod
    def _is_beijing_monday(now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        return now.astimezone(BEIJING_TIMEZONE).weekday() == 0

    def _pvp_special_page_ocr_boxes(
        self,
        frame: np.ndarray | None = None,
        *,
        name: str = "PVP 特殊页面",
    ) -> list:
        try:
            if frame is None:
                frame = self.capture_frame()
            config = getattr(self, "config", {})
            threshold = next(
                (
                    float(config[key])
                    for key in (
                        "PVP OCR 阈值",
                        "广场 OCR 阈值",
                        "跑商 OCR 阈值",
                        "跑图 OCR 阈值",
                    )
                    if key in config
                ),
                0.2,
            )
            boxes = self.ocr(
                frame=frame,
                threshold=threshold,
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return []
        return list(boxes)

    def _recent_cartridge_ocr_boxes(self) -> list:
        return self._pvp_special_page_ocr_boxes(name="最近卡带特殊页面")

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
        self.info_set("当前阶段", "打开卡带快速切换")
        if not self.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self._click_template_until(
                QUICK_PACK_TEMPLATE,
                timeout=float(self.config.get("快速卡带等待秒数", 10.0)),
                name="快速切换按钮",
                after_sleep=0.0,
                stabilize=True,
            ),
            confirm_quick_switch_page=self._wait_for_quick_switch_page,
        ):
            self.log_info("镜中之战：未能从主页打开卡带快速切换页面。")
            return False

        self.info_set("当前阶段", "选择玩法游戏卡")
        self.sleep(0.5)
        self.operate_click(*GAMEPLAY_CARTRIDGE_POINT, after_sleep=0.5)

        self.info_set("当前阶段", "选择 PVP 卡带1号位")
        self.operate_click(*PVP_CARTRIDGE_SLOT_POINT, after_sleep=0.0)

        if self._wait_for_pvp_hub_after_cart(
            timeout=float(self.config.get("PVP 入场等待秒数", 30.0)),
        ):
            self._clear_pvp_hub_notice_if_present()
            return True

        return False

    def _wait_for_quick_switch_page(self, interval: float = 0.5) -> bool:
        self.info_set("当前阶段", "确认卡带选择页")
        end_at = monotonic() + float(self.config.get("卡带选择页确认等待秒数", 10.0))
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name="卡带选择页")
            last_text = text or last_text
            match_count = self._ocr_pattern_match_count(text, list(QUICK_SWITCH_PAGE_PATTERNS))
            self.info_set("卡带选择页 OCR", text or "-")
            self.info_set(
                "卡带选择页 OCR 命中",
                f"{match_count}/{len(QUICK_SWITCH_PAGE_PATTERNS)}",
            )
            if match_count == len(QUICK_SWITCH_PAGE_PATTERNS):
                return True
            self.sleep(interval)

        self.info_set("卡带选择页 OCR", last_text or "-")
        self.log_info(
            "镜中之战：点击快速切换按钮后未确认卡带选择页，"
            f"OCR={last_text or '-'}。"
        )
        return False

    def _wait_for_cartridge_home(self, interval: float = 0.35) -> bool:
        self.info_set("当前阶段", "确认主页")
        end_at = monotonic() + float(self.config.get("主页确认等待秒数", 10.0))
        last_button_score = -1.0
        last_ratio = 0.0
        last_gacha_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            home_ok, last_button_score, last_ratio, gacha_text = (
                self._home_confirmation_signals(frame)
            )
            last_gacha_text = gacha_text or last_gacha_text
            self.info_set("主页小屋按钮", f"{last_button_score:.3f}")
            self.info_set("主页亮度", f"{last_ratio:.3f}")
            self.info_set("主页抽抽乐 OCR", gacha_text or "-")
            if home_ok:
                return True
            self.clear_temporary_home_announcement_if_needed(
                button_found=last_button_score
                >= float(self.config.get("主页小屋按钮阈值", 0.70)),
                brightness_ratio=last_ratio,
                brightness_threshold=self._home_ratio_threshold(),
                gacha_ocr_text=gacha_text,
                context="镜中之战确认主页",
            )
            self.sleep(interval)

        self.log_info(
            "镜中之战：未联合确认主页按钮、亮度和抽抽乐文字，"
            f"button={last_button_score:.3f}, ratio={last_ratio:.3f}, "
            f"ocr={last_gacha_text or '-'}。"
        )
        return False

    def _wait_for_pvp_hub_after_cart(
        self,
        timeout: float,
        interval: float = 0.5,
    ) -> bool:
        """Handle every PVP entry special page before accepting the PVP hub."""
        end_at = monotonic() + max(0.0, timeout)
        handled: set[str] = set()
        action_count = 0
        last_text = ""
        last_hub_score = -1.0
        hub_candidate_at: float | None = None
        allow_season_reward = self._is_beijing_monday()
        self.info_set(
            "PVP 入场特殊页面模式",
            "周一：赛季奖励及升降级" if allow_season_reward else "非周一：仅升降级",
        )

        while True:
            now = monotonic()
            if now > end_at:
                break
            frame = self.capture_frame()
            boxes = self._pvp_special_page_ocr_boxes(
                frame,
                name="PVP 入场特殊页面",
            )
            text, action_name, target_box = self._pvp_special_page_action(
                boxes,
                allow_season_reward=allow_season_reward,
            )
            last_text = text or last_text
            self.info_set("PVP 入场特殊页面 OCR", text or "-")
            if action_name:
                hub_candidate_at = None
                if (
                    action_name not in handled
                    and target_box is not None
                    and action_count < RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS
                ):
                    point = self._ocr_box_center(target_box)
                    if point is not None:
                        frame_height, frame_width = frame.shape[:2]
                        self.info_set("当前阶段", f"处理 PVP 入场{action_name}")
                        after_sleep = (
                            PVP_SEASON_REWARD_AFTER_CLICK_SECONDS
                            if action_name == "赛季奖励"
                            else PVP_RANK_PAGE_AFTER_CLICK_SECONDS
                        )
                        self.operate_click(
                            max(0.0, min(1.0, point[0] / max(1, frame_width))),
                            max(0.0, min(1.0, point[1] / max(1, frame_height))),
                            after_sleep=after_sleep,
                        )
                        handled.add(action_name)
                        action_count += 1
                        continue
                # The hub artwork can remain visible behind these overlays. Never
                # accept its template from a frame that contains a special page.
                self.sleep(interval)
                continue

            self.info_set("当前阶段", "确认 PVP 箱庭")
            hub = self._match(frame, PVP_MEDALS_TEMPLATE)
            last_hub_score = hub.score
            self.info_set("PVP 箱庭", f"{hub.score:.3f}")
            if self._passes(hub, PVP_MEDALS_TEMPLATE):
                if hub_candidate_at is None:
                    hub_candidate_at = now
                stable_seconds = now - hub_candidate_at
                self.info_set(
                    "当前阶段",
                    (
                        "确认 PVP 箱庭并观察特殊页面 "
                        f"{stable_seconds:.1f}/{PVP_HUB_SPECIAL_PAGE_GRACE_SECONDS:.1f}秒"
                    ),
                )
                if stable_seconds >= PVP_HUB_SPECIAL_PAGE_GRACE_SECONDS:
                    return True
            else:
                hub_candidate_at = None

            self.sleep(interval)

        self.info_set("PVP 入场特殊页面 OCR", last_text or "-")
        self.info_set("PVP 箱庭", f"{last_hub_score:.3f}")
        self.log_info(
            "镜中之战：点击 PVP 卡带后未确认进入 PVP 箱庭，"
            f"hub={last_hub_score:.3f}, special_page_ocr={last_text or '-'}, "
            f"handled={','.join(sorted(handled)) or '-'}。"
        )
        return False

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
            target_reference_offset=PVP_STAGE_CLICK_REFERENCE_OFFSET,
            after_sleep=3.0,
        ):
            self._recover_stage_position()
            if not self._click_template_until(
                PVP_STAGE_TEMPLATE,
                timeout=8.0,
                name="PVP 舞台",
                target_reference_offset=PVP_STAGE_CLICK_REFERENCE_OFFSET,
                after_sleep=3.0,
            ):
                self.log_info("镜中之战：未找到 PVP 舞台物件。")
                return "failed"

        self.info_set("当前阶段", "打开自动战斗")
        found_auto, text = self._wait_for_ocr_patterns(
            [r"自动战斗", r"自动"],
            timeout=float(self.config.get("PVP 菜单等待秒数", 12.0)),
            name="PVP 自动战斗",
            roi=PVP_AUTO_BATTLE_SCREEN_ROI,
        )
        self.info_set("PVP 自动战斗 OCR", text or "-")
        if not found_auto:
            return "failed"

        if not self._click_ocr_pattern_center(
            [r"自动战斗", r"自动"],
            name="PVP 自动战斗",
            roi=PVP_AUTO_BATTLE_SCREEN_ROI,
            after_sleep=1.0,
        ):
            self.info_set("PVP 自动战斗点击", "OCR框中心不可用，使用相对比例回退")
            self._click_screen_reference(
                *PVP_AUTO_BATTLE_CLICK_REFERENCE,
                after_sleep=1.0,
            )
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
            targets = (
                (
                    "失败页",
                    "pvp_leave_failure",
                    PVP_FAILURE_LEAVE_REFERENCE_ROI,
                ),
                (
                    "成功页",
                    "pvp_leave_success",
                    PVP_SUCCESS_LEAVE_REFERENCE_ROI,
                ),
            )
            ocr_results = []
            matched_point: tuple[float, float] | None = None
            for page_name, ocr_name, roi in targets:
                boxes = self._ocr_boxes(frame, ocr_name, roi=roi)
                text = " ".join(
                    str(getattr(box, "name", ""))
                    for box in boxes
                    if getattr(box, "name", "")
                )
                ocr_results.append((page_name, text))
                leave_box = self._find_ocr_box(boxes, "离开")
                if matched_point is None and leave_box is not None:
                    local_point = self._ocr_box_center(leave_box)
                    if local_point is not None:
                        roi_left, roi_top, _roi_frame = self._roi_frame(frame, roi)
                        matched_point = (
                            roi_left + local_point[0],
                            roi_top + local_point[1],
                        )

            combined_text = " | ".join(
                f"{page_name}:{text or '-'}" for page_name, text in ocr_results
            )
            last_text = combined_text or last_text
            self.info_set("PVP 离开 OCR", combined_text or "-")
            if matched_point is not None:
                self.info_set(
                    "PVP 离开点击",
                    f"OCR中心=({matched_point[0]:.0f},{matched_point[1]:.0f})",
                )
                self._click_frame_point(frame, matched_point, after_sleep=2.0)
                return True

            self.sleep(0.5)

        self.info_set("PVP 离开 OCR", last_text or "-")
        return False

    def _ensure_pvp_hub_after_leave(self) -> bool:
        self.info_set("当前阶段", "确认离开结果")
        timeout = float(self.config.get("PVP 返回箱庭等待秒数", 10.0))
        state, text, point = self._wait_for_pvp_hub_or_confirm(timeout=timeout)
        if state == "hub":
            return True

        self.info_set("PVP 升降级确认 OCR", text or "-")
        if state != "confirm" or point is None:
            self.info_set("PVP 返回主页", "未检测到 PVP 箱庭或升降级确认按钮")
            return False

        frame = self.capture_frame()
        self.info_set(
            "PVP 升降级确认",
            f"OCR中心=({point[0]:.0f},{point[1]:.0f})",
        )
        self._click_frame_point(frame, point, after_sleep=1.0)
        return self._wait_for_template(
            PVP_MEDALS_TEMPLATE,
            timeout=timeout,
            name="PVP 箱庭",
        )

    def _wait_for_pvp_hub_or_confirm(
        self,
        timeout: float,
        interval: float = 0.5,
    ) -> tuple[str, str, tuple[float, float] | None]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        last_hub_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()

            hub = self._match(frame, PVP_MEDALS_TEMPLATE)
            last_hub_score = hub.score
            self.info_set("PVP 箱庭", f"{hub.score:.3f}")
            if self._passes(hub, PVP_MEDALS_TEMPLATE):
                self.info_set("PVP 返回主页", "已回到 PVP 箱庭")
                return "hub", last_text, None

            boxes = self._ocr_boxes(frame, "PVP 升降级确认")
            text = " ".join(
                str(getattr(box, "name", ""))
                for box in boxes
                if getattr(box, "name", "")
            )
            last_text = text or last_text
            self.info_set("PVP 升降级确认 OCR", text or "-")
            confirm_box = self._find_first_ocr_box(boxes, ("确认", "确定"))
            point = self._ocr_box_center(confirm_box) if confirm_box is not None else None
            if point is not None:
                return "confirm", text, point

            self.sleep(interval)

        self.info_set("PVP 箱庭", f"{last_hub_score:.3f}")
        return "timeout", last_text, None

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
            frame = self.capture_frame()
            home_ok, button_score, ratio, gacha_text = self._home_confirmation_signals(frame)
            self.info_set("主页小屋按钮", f"{button_score:.3f}")
            self.info_set("主页亮度", f"{ratio:.3f}")
            self.info_set("主页抽抽乐 OCR", gacha_text or "-")
            if home_ok:
                return True
            self.clear_temporary_home_announcement_if_needed(
                button_found=button_score
                >= float(self.config.get("主页小屋按钮阈值", 0.70)),
                brightness_ratio=ratio,
                brightness_threshold=self._home_ratio_threshold(),
                gacha_ocr_text=gacha_text,
                context="PVP 返回主页",
            )
            self.sleep(interval)
        return False

    def _home_confirmation_signals(self, frame) -> tuple[bool, float, float, str]:
        home_button = max(
            (self._match(frame, spec) for spec in HOME_TEMPLATES),
            key=lambda result: result.score,
        )
        ratio = self._home_brightness_ratio(frame)
        gacha_text = self._ocr_text(
            frame,
            name="主页抽抽乐",
            roi=HOME_GACHA_OCR_REFERENCE_ROI,
        )
        confirmed = home_confirmation_passes(
            button_found=home_button.score
            >= float(self.config.get("主页小屋按钮阈值", 0.70)),
            brightness_ratio=ratio,
            brightness_threshold=self._home_ratio_threshold(),
            gacha_ocr_text=gacha_text,
        )
        return confirmed, home_button.score, ratio, gacha_text

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
            self.drag_client(
                (round(frame_width * 0.5), round(frame_height * 0.72)),
                (round(frame_width * 0.5), round(frame_height * 0.35)),
                duration=0.6,
                after_sleep=1.0,
            )

    def _click_template_until(
        self,
        spec: TemplateSpec,
        timeout: float,
        name: str,
        target: tuple[int, int] | None = None,
        target_offset: tuple[int, int] = (0, 0),
        target_reference_offset: tuple[int, int] = (0, 0),
        after_sleep: float = 0.0,
        interval: float = 0.35,
        stabilize: bool = False,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}/{result.pixel_score:.3f}")
            if self._passes(result, spec):
                stable_center = None
                if stabilize:

                    def sample_match():
                        sampled_frame = self.capture_frame()
                        return self._match(sampled_frame, spec), sampled_frame.shape

                    stabilized = stabilize_template_match(
                        result,
                        frame.shape,
                        sample_match=sample_match,
                        passes=lambda candidate: self._passes(candidate, spec),
                        sleep=self.sleep,
                        on_sample=lambda candidate: self.info_set(
                            name,
                            f"{candidate.score:.3f}/{candidate.pixel_score:.3f}",
                        ),
                    )
                    if stabilized is None:
                        self.info_set(f"{name}稳定识别", "未形成稳定位置")
                        return False
                    consensus, frame_shape = stabilized
                    frame_height, frame_width = frame_shape[:2]
                    stable_center = consensus.center
                    self.info_set(
                        f"{name}稳定识别",
                        (
                            f"center=({stable_center[0]},{stable_center[1]}), "
                            f"hits={consensus.hit_count}/{consensus.sample_count}, "
                            f"match={consensus.average_score:.3f}, "
                            f"pixel={consensus.average_pixel_score:.3f}, "
                            f"spread={consensus.center_spread:.1f}"
                        ),
                    )
                if target is not None:
                    self._click_reference(target[0], target[1], after_sleep=after_sleep)
                else:
                    reference_offset_x = round(
                        target_reference_offset[0] * frame_width / REFERENCE_WIDTH
                    )
                    reference_offset_y = round(
                        target_reference_offset[1] * frame_height / REFERENCE_HEIGHT
                    )
                    center_x, center_y = (
                        stable_center
                        if stable_center is not None
                        else (
                            result.position[0] + result.size[0] // 2,
                            result.position[1] + result.size[1] // 2,
                        )
                    )
                    x = (
                        center_x
                        + target_offset[0]
                        + reference_offset_x
                    )
                    y = (
                        center_y
                        + target_offset[1]
                        + reference_offset_y
                    )
                    self._click_client(x, y, frame_width, frame_height, after_sleep=after_sleep)
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _find_template_until(
        self,
        spec: TemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> tuple[MatchResult | None, tuple[int, int] | None]:
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
        spec: TemplateSpec,
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

    def _match(self, frame, spec: TemplateSpec) -> MatchResult:
        empty = MatchResult(-1.0, (0, 0), (0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            return task_vision.match_template(
                frame,
                spec,
                self.config,
                TEMPLATE_DIR,
                cache=self._templates,
                min_size=5,
                loader=lambda _template_dir, spec: self._load_template(spec),
            )
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty
        except (cv2.error, MemoryError) as exc:
            self._match_pause_until = monotonic() + 2.0
            message = f"图像匹配内存不足，暂停识别2秒：{spec.name}"
            self.info_set("匹配错误", message)
            if spec.name not in self._match_error_names:
                self._match_error_names.add(spec.name)
                self.log_warning(f"{message}；{exc}", notify=True)
            return empty

    def _home_brightness_ratio(self, frame) -> float:
        return max(self._home_brightness_ratio_for_template(frame, spec) for spec in HOME_TEMPLATES)

    def _home_brightness_ratio_for_template(self, frame, spec: TemplateSpec) -> float:
        return task_vision.brightness_ratio(
            frame,
            spec,
            (222 / ENTRY_REFERENCE_WIDTH, 211 / ENTRY_REFERENCE_HEIGHT),
            TEMPLATE_DIR,
            cache=self._templates,
        )

    def _load_template(self, spec: TemplateSpec) -> tuple[np.ndarray, np.ndarray | None]:
        return task_vision.load_template(TEMPLATE_DIR, spec, cache=self._templates)

    def _passes(self, result: MatchResult, spec: TemplateSpec) -> bool:
        return task_vision.passes_match(result, spec, self.config)

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

    def _click_ocr_pattern_center(
        self,
        patterns: list[str],
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        after_sleep: float = 0.0,
    ) -> bool:
        """Click the center of a matching OCR box in the current frame."""
        frame = self.capture_frame()
        boxes = self._ocr_boxes(frame, name=name, roi=roi)
        left, top, _ = self._roi_frame(frame, roi)
        frame_height, frame_width = frame.shape[:2]
        for box in boxes:
            label = str(getattr(box, "name", ""))
            if not self._matches_any(label, patterns):
                continue
            local_point = self._ocr_box_center(box)
            if local_point is None:
                continue
            point = (left + local_point[0], top + local_point[1])
            self.info_set(
                "PVP 自动战斗点击",
                f"OCR中心=({point[0]:.0f},{point[1]:.0f})",
            )
            self._click_client(
                int(round(point[0])),
                int(round(point[1])),
                frame_width,
                frame_height,
                after_sleep=after_sleep,
            )
            return True
        return False

    def _click_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _click_frame_point(
        self,
        frame: np.ndarray,
        point: tuple[float, float],
        after_sleep: float = 0.0,
    ) -> None:
        frame_height, frame_width = frame.shape[:2]
        self.operate_click(
            max(0.0, min(1.0, point[0] / max(1, frame_width))),
            max(0.0, min(1.0, point[1] / max(1, frame_height))),
            after_sleep=after_sleep,
        )

    @classmethod
    def _find_first_ocr_box(cls, boxes: list, keywords: tuple[str, ...]):
        for keyword in keywords:
            box = cls._find_ocr_box(boxes, keyword)
            if box is not None:
                return box
        return None

    @staticmethod
    def _mf_point(x: int, y: int) -> tuple[int, int]:
        return (
            round(x * REFERENCE_WIDTH / HD720_REFERENCE_WIDTH),
            round(y * REFERENCE_HEIGHT / HD720_REFERENCE_HEIGHT),
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
        self.drag_client(start_client, end_client, duration=duration, after_sleep=after_sleep)

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

    _normalize_text = staticmethod(normalize_ocr_text)

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
    def _roi_frame(
        frame: np.ndarray,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, np.ndarray]:
        return reference_roi_frame(frame, roi, (REFERENCE_WIDTH, REFERENCE_HEIGHT))

    _relative_roi_frame = staticmethod(relative_roi_frame)

    @staticmethod
    def _crop_reference(frame, roi: tuple[int, int, int, int] | None):
        return reference_roi_frame(frame, roi, (REFERENCE_WIDTH, REFERENCE_HEIGHT))[2]

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


LOADING_TEMPLATE = TemplateSpec(
    name="loading",
    file_name="image/UI_loading_black.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

HOME_TEMPLATE = TemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
)

HOME_ICE_TEMPLATE = TemplateSpec(
    name="home_ice",
    file_name="image/green/MainHomeIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_RICE_TEMPLATE = TemplateSpec(
    name="home_rice",
    file_name="image/green/MainHomeRIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_TEMPLATES = (HOME_TEMPLATE, HOME_ICE_TEMPLATE, HOME_RICE_TEMPLATE)

QUICK_PACK_TEMPLATE = TemplateSpec(
    name="quick_pack",
    file_name="image/green/QuickSwitchPlayIco.png",
    threshold_key="快速切换按钮阈值",
    default_threshold=0.88,
    relative_roi=(0.25, 0.85, 0.65, 1.0),
    green_mask=True,
    scale_ratios=(0.95, 0.975, 1.0, 1.025, 1.05),
    min_pixel_score=0.85,
    candidate_center_roi=(650 / 1920, 950 / 1080, 1050 / 1920, 1045 / 1080),
    minimum_safe_threshold=0.88,
    min_zncc_score=0.85,
)

PVP_MEDALS_TEMPLATE = TemplateSpec(
    name="pvp_medals",
    file_name="image/pvp-medals.png",
    threshold_key="PVP 箱庭阈值",
    default_threshold=0.78,
    roi=(793, 39, 340, 35),
    scale_ratios=(0.944, 0.96, 0.976, 1.0, 1.04),
    min_pixel_score=0.88,
)

PVP_HUB_NOTICE_TEMPLATE = TemplateSpec(
    name="pvp_hub_notice",
    file_name="image/green/tanhaoGE.png",
    threshold_key="PVP 箱庭感叹号阈值",
    default_threshold=0.72,
    roi=PVPTask._screen_reference_roi_to_reference_roi(PVP_HUB_NOTICE_SCREEN_ROI),
)

PVP_STAGE_TEMPLATE = TemplateSpec(
    name="pvp_stage",
    file_name="image/pvp-stage.png",
    threshold_key="PVP 舞台阈值",
    default_threshold=0.72,
    roi=(190, 238, 900, 620),
)

PVP_LOC_RESET_TEMPLATE = TemplateSpec(
    name="pvp_loc_reset",
    file_name="image/pvp-loc-reset.png",
    threshold_key="PVP 定位修正阈值",
    default_threshold=0.76,
)

PVP_NO_FIND_TEMPLATES = [
    TemplateSpec(
        name="pvp_nofind_UT_bk",
        file_name="image/pvp-nofind-UT-bk.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    TemplateSpec(
        name="pvp_nofind_ut_bk2",
        file_name="image/pvp-nofind-ut-bk2.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    TemplateSpec(
        name="pvp_nofind_UT_ft",
        file_name="image/pvp-nofind-UT-ft.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    TemplateSpec(
        name="pvp_nofind_UT_Rt",
        file_name="image/pvp-nofind-UT-Rt.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    TemplateSpec(
        name="pvp_nofind_twoaudience",
        file_name="image/pvp-nofind-twoaudience.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    TemplateSpec(
        name="pvp_nofind_waiter_fr",
        file_name="image/pvp-nofind-waiter-fr.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
    TemplateSpec(
        name="pvp_nofind_aman_sit",
        file_name="image/pvp-nofind-aman-sit.png",
        threshold_key="PVP 定位修正阈值",
        default_threshold=0.76,
    ),
]
