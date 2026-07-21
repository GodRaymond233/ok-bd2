from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import numpy as np

from src.tasks.map_trade.models import (
    CARD_BY_ID,
    MERCHANT_CARD_ID,
    MatchResult,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.vision import Vision, normalize_text

HOME_TEMPLATES = (
    TemplateSpec("主页", "home.png", 0.72),
    TemplateSpec("主页冰淇淋", "image/green/MainHomeIceGE.png", 0.72, green_mask=True),
    TemplateSpec("主页米饭", "image/green/MainHomeRIceGE.png", 0.72, green_mask=True),
)
QUICK_SWITCH_TEMPLATE = TemplateSpec(
    "快速切换按钮",
    "image/green/BusinQuickIcoGE.png",
    0.78,
    relative_roi=(0.25, 0.85, 0.65, 1.0),
    scale_ratios=(0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0, 1.05, 1.10),
    min_pixel_score=0.72,
)
Q_SP6_SHOP_TEMPLATE = TemplateSpec(
    "Q_sp6商店按钮",
    "Q_sp6_shop.png",
    0.78,
    scale_ratios=(0.90, 0.95, 1.0, 1.05, 1.10),
    min_pixel_score=0.72,
)
Q_SP6_SHOP_VERTICAL_OFFSET = 150 / 1080
Q_SP6_SHOP_PRIORITY_TIMEOUT = 3.0
Q_SP6_SHOP_PAGE_KEYWORDS = ("仓库", "严加管理")
Q_SP6_SHOP_PAGE_OCR_INTERVAL = 0.25
Q_SP6_BARGAIN_RECHECK_DELAY = 0.5
Q_SP6_BARGAIN_OCR_TIMEOUT = 10.0
QUICK_SWITCH_PAGE_KEYWORDS = (
    "店长游戏卡",
    "剧情游戏卡",
    "角色游戏卡",
    "玩法游戏卡",
    "最近",
    "活动游戏卡",
)
STORY_CATEGORY_POINT = (557 / 1920, 877 / 1080)
STORY_CATEGORY_HIGHLIGHT_REGION = (
    445 / 1920,
    840 / 1080,
    670 / 1920,
    915 / 1080,
)
STORY_CATEGORY_HIGHLIGHT_MIN_RATIO = 0.05
QUICK_SWITCH_CARTRIDGE_REGION = (0.0, 908 / 1080, 1.0, 1.0)
QUICK_SWITCH_SCROLL_POINT = (960 / 1920, 970 / 1080)
QUICK_SWITCH_SCROLL_RESET_AMOUNT = -1
QUICK_SWITCH_SCROLL_RESET_COUNT = 24
QUICK_SWITCH_SCROLL_UP_AMOUNT = 1
QUICK_SWITCH_SCROLL_UP_COUNT = 2
QUICK_SWITCH_SCROLL_SCAN_STEPS = 16
QUICK_SWITCH_SCROLL_INTERVAL = 0.08
QUICK_SWITCH_SCROLL_SETTLE_SECONDS = 0.35
STORY_BADGE_TEMPLATE_SCORE = 0.95
STORY_BADGE_PIXEL_SCORE = 0.95
STORY_BADGE_MIN_MARGIN = 0.05
STORY_BADGE_CANDIDATE_SCORE = 0.70
STORY_BADGE_CLUSTER_RADIUS = 12
Q_SP6_STORY_NUMBER = 6
STORY_BADGE_SPECS = tuple(
    (
        number,
        TemplateSpec(
            name=f"剧情游戏卡{number}角标",
            file_name=(
                f"quick_switch_cartridges/story_cartridge_badge_{number:02d}.png"
            ),
            threshold=STORY_BADGE_TEMPLATE_SCORE,
            relative_roi=QUICK_SWITCH_CARTRIDGE_REGION,
            min_pixel_score=STORY_BADGE_PIXEL_SCORE,
        ),
    )
    for number in range(1, 21)
)
BARGAIN_POINT = (191 / 1920, 900 / 1080)
BARGAIN_CONFIRM_POINT = (1047 / 1920, 652 / 1080)
Q_SP6_BARGAIN_CONFIRM_DELAY = 1.0
DISCOUNT_SHOP_CLOSE_DIALOG_REGION = (
    700 / 1920,
    382 / 1080,
    1220 / 1920,
    694 / 1080,
)
DISCOUNT_SHOP_CLOSE_KEYWORDS = (
    "折扣商店结束",
    "是否关闭折扣商店",
)
DISCOUNT_SHOP_CLOSE_POINT = (1045 / 1920, 639 / 1080)
CHAPTER_HOME_POINT = (1797 / 1920, 63 / 1080)
DISCOUNT_SHOP_CLOSE_TIMEOUT = 5.0
RETURN_HOME_TIMEOUT = 10.0
HOME_BRIGHTNESS_THRESHOLD = 0.75
SANDBOX_TEMPLATES = (
    TemplateSpec("箱庭图钉", "image/pin.png", 0.76, roi=(1080, 610, 170, 100)),
    TemplateSpec(
        "箱庭奔跑",
        "image/green/Run.png",
        0.72,
        roi=(1120, 590, 150, 125),
        green_mask=True,
    ),
)
LOADING_TEMPLATE = TemplateSpec("加载页面", "image/UI_loading_black.png", 0.70)
MERCHANT_DIALOG_TEMPLATE = TemplateSpec(
    "商人对话", "image/Mer_Dialog_TalMed.png", 0.72, roi=(930, 15, 280, 70)
)
MERCHANT_ICON_TEMPLATE = TemplateSpec(
    "商人交互", "image/green/Merchant_IcoGE.png", 0.74, green_mask=True
)
MAP_MERCHANT_TEMPLATE = TemplateSpec(
    "小地图商人", "image/Map_Merchant.png", 0.72, roi=(130, 110, 210, 190)
)
NAV_GUIDE_TEMPLATE = TemplateSpec(
    "小地图导航", "image/Nvi_SandGuideButt.png", 0.72, roi=(180, 45, 210, 110)
)
AUTO_NAV_TEMPLATE = TemplateSpec(
    "自动移动", "image/AutoNvi_ico.png", 0.72, roi=(570, 580, 140, 120)
)


@dataclass(frozen=True)
class StoryBadgeCandidate:
    number: int
    result: MatchResult


@dataclass(frozen=True)
class StoryBadgeDetection:
    best: StoryBadgeCandidate
    runner_up: StoryBadgeCandidate | None

    @property
    def margin(self) -> float:
        if self.runner_up is None:
            return -1.0
        return self.best.result.score - self.runner_up.result.score


HAND_TEMPLATE = TemplateSpec(
    "传送阵交互",
    "image/green/IcoHand.png",
    0.74,
    roi=(790, 470, 190, 180),
    green_mask=True,
)
NAV_TELEPORT_TEMPLATE = TemplateSpec(
    "导航图传送阵", "image/green/Nvi_TpCircleMap.png", 0.72, roi=(150, 45, 570, 620)
)
MAP_LEFT_TEMPLATE = TemplateSpec(
    "区域图左箭头", "image/green/TpMapLeft.png", 0.72, roi=(230, 270, 160, 130)
)
MAP_RIGHT_TEMPLATE = TemplateSpec(
    "区域图右箭头", "image/green/TpMapRight.png", 0.72, roi=(800, 270, 470, 140)
)
TELEPORT_MAP_TEMPLATES = (
    TemplateSpec("区域图传送阵绿幕", "image/TpCircleMapGE.png", 0.72, roi=(100, 60, 1080, 560)),
    TemplateSpec("区域图传送阵", "image/TpCircleMap.png", 0.72, roi=(100, 60, 1080, 560)),
)
OVERLAP_ARROW_TEMPLATE = TemplateSpec(
    "传送阵重叠箭头",
    "image/green/map_tcArrowGE.png",
    0.72,
    roi=(80, 50, 1160, 580),
)
FIRST_CARD_INSERT_REGION = (413, 481, 440, 132)
FIRST_CARD_SKIP_TEMPLATE = TemplateSpec(
    "首次卡带跳过",
    "image/UI_Skip.png",
    0.72,
    roi=(915, 9, 265, 68),
)
FIRST_CARD_CONFIRM_REGION = (626, 368, 186, 293)


class Navigator:
    def __init__(self, task, vision: Vision) -> None:
        self.task = task
        self.vision = vision

    def classify(self, frame=None) -> ScreenState:
        frame = self.vision.capture() if frame is None else frame
        for spec in HOME_TEMPLATES:
            if self.vision.match(frame, spec).score >= self.vision.threshold_for(spec):
                return ScreenState.HOME
        if self.vision.match(frame, MERCHANT_DIALOG_TEMPLATE).score >= self.vision.threshold_for(
            MERCHANT_DIALOG_TEMPLATE
        ):
            return ScreenState.MERCHANT_DIALOG
        if self.vision.match(frame, LOADING_TEMPLATE).score >= self.vision.threshold_for(
            LOADING_TEMPLATE
        ):
            return ScreenState.LOADING
        for spec in SANDBOX_TEMPLATES:
            if self.vision.match(frame, spec).score >= self.vision.threshold_for(spec):
                return ScreenState.SANDBOX

        text = normalize_text(self.vision.simplify(self.vision.ocr_text(frame, "界面分类")))
        if "购买" in text and "出售" in text:
            return ScreenState.SHOP
        if "移动魔法阵" in text:
            return ScreenState.AREA_MAP
        if "游戏卡珍藏" in text or "剧情游戏卡" in text:
            return ScreenState.CARD_MENU
        if "所需材料" in text and "料理" in text:
            return ScreenState.COOKING
        return ScreenState.UNKNOWN

    def wait_state(self, wanted: set[ScreenState], timeout: float) -> ScreenState:
        end_at = monotonic() + max(0.0, timeout)
        last = ScreenState.UNKNOWN
        while monotonic() <= end_at:
            last = self.classify()
            self._status("导航状态", last.value)
            if last in wanted:
                return last
            self.task.sleep(0.5)
        return last

    def _loading_timeout(self) -> float:
        return max(10.0, float(self.task.config.get("加载页面等待秒数", 45.0)))

    def enter_q_sp6_buy_flow(self) -> NavigationResult:
        """Run the user-confirmed buy entry flow and stop after bargain confirmation."""

        self._status("导航状态", "优先识别Q_sp6商店按钮")
        shop_opened = self._enter_q_sp6_shop(
            Q_SP6_SHOP_PRIORITY_TIMEOUT,
            log_timeout=False,
        )
        if not shop_opened:
            self._status("导航状态", "确认主页")
            story_menu = self._open_story_quick_switcher()
            if not story_menu.success:
                return story_menu
            self._status("导航状态", "识别剧情游戏卡6角标")
            badge_match = self._wait_for_story_badge(Q_SP6_STORY_NUMBER)
            if badge_match is None:
                return NavigationResult(False, self.classify(), "未唯一确认剧情游戏卡6角标")
            badge_frame, badge = badge_match
            self._status(
                "剧情游戏卡6角标点击中心",
                (
                    f"center=({badge.best.result.center[0]},"
                    f"{badge.best.result.center[1]}), "
                    f"match={badge.best.result.score:.3f}, "
                    f"pixel={badge.best.result.pixel_score:.3f}, "
                    f"margin={badge.margin:.3f}"
                ),
            )
            self.vision.click_client(
                badge.best.result.center,
                badge_frame.shape,
                after_sleep=0.0,
            )

            self._status("导航状态", "持续识别Q_sp6商店按钮")
            shop_opened = self._enter_q_sp6_shop(
                self._loading_timeout(),
                log_timeout=True,
            )
            if not shop_opened:
                return NavigationResult(False, self.classify(), "未识别到Q_sp6_shop.png")

        self.task.sleep(Q_SP6_BARGAIN_RECHECK_DELAY)
        if not self._wait_for_ocr_keywords(
            ("砍价",),
            Q_SP6_BARGAIN_OCR_TIMEOUT,
            "砍价入口",
        ):
            return NavigationResult(False, self.classify(), "商店页面未识别到砍价入口")
        self.task.operate_click(*BARGAIN_POINT, after_sleep=0.0)

        bargain_tip = "使用砍价技能后可享受商店折扣价"
        if not self._wait_for_ocr_keywords((bargain_tip,), 10.0, "砍价说明"):
            return NavigationResult(False, self.classify(), "未识别到砍价技能折扣说明")
        self.task.operate_click(
            *BARGAIN_CONFIRM_POINT,
            after_sleep=Q_SP6_BARGAIN_CONFIRM_DELAY,
        )
        return NavigationResult(True, ScreenState.MERCHANT_DIALOG, "已点击砍价确认")

    def _enter_q_sp6_shop(
        self,
        timeout: float,
        *,
        log_timeout: bool,
        interval: float = 0.5,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        click_count = 0
        last_score = -1.0
        last_pixel_score = -1.0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            shop_match = self.vision.match(frame, Q_SP6_SHOP_TEMPLATE)
            last_score = shop_match.score
            last_pixel_score = shop_match.pixel_score
            self._status(
                Q_SP6_SHOP_TEMPLATE.name,
                f"{last_score:.3f}/{last_pixel_score:.3f}",
            )
            if self.vision.passes(shop_match, Q_SP6_SHOP_TEMPLATE):
                shop_click_point = self._q_sp6_shop_click_point(
                    shop_match,
                    frame.shape,
                )
                click_count += 1
                self._status(
                    "Q_sp6商店点击",
                    f"第{click_count}次 "
                    f"{shop_match.center[0]},{shop_match.center[1]} -> "
                    f"{shop_click_point[0]},{shop_click_point[1]}",
                )
                self.vision.click_client(
                    shop_click_point,
                    frame.shape,
                    after_sleep=0.0,
                )
                if self._wait_for_ocr_keywords(
                    Q_SP6_SHOP_PAGE_KEYWORDS,
                    self._loading_timeout(),
                    "商店页面",
                    interval=Q_SP6_SHOP_PAGE_OCR_INTERVAL,
                ):
                    self._status("Q_sp6商店进入", f"成功，点击{click_count}次")
                    return True
                break
            self.task.sleep(interval)

        if log_timeout:
            self.task.log_warning(
                "跑商：未在限定时间内进入Q_sp6商店，"
                f"点击{click_count}次，最后匹配="
                f"{last_score:.3f}/{last_pixel_score:.3f}。"
            )
        return False

    @staticmethod
    def _q_sp6_shop_click_point(
        match: MatchResult,
        frame_shape: tuple[int, ...],
    ) -> tuple[int, int]:
        height, width = frame_shape[:2]
        center_x, center_y = match.center
        return (
            max(0, min(width - 1, center_x)),
            max(
                0,
                min(
                    height - 1,
                    center_y + round(height * Q_SP6_SHOP_VERTICAL_OFFSET),
                ),
            ),
        )

    def _wait_for_cartridge_home(self, timeout: float = 10.0, interval: float = 0.35) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        last_brightness = 0.0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            candidates = [(spec, self.vision.match(frame, spec)) for spec in HOME_TEMPLATES]
            spec, result = max(candidates, key=lambda value: value[1].score)
            last_score = result.score
            last_brightness = self.vision.template_brightness_ratio(frame, spec, result)
            self._status("主页小屋按钮", f"{last_score:.3f}")
            self._status("主页亮度", f"{last_brightness:.3f}")
            if self.vision.passes(result, spec) and last_brightness >= HOME_BRIGHTNESS_THRESHOLD:
                return True
            self.task.sleep(interval)
        self.task.log_warning(
            "跑商：未确认主页小屋按钮或亮度不足，"
            f"button={last_score:.3f}, brightness={last_brightness:.3f}。"
        )
        return False

    def _wait_for_quick_switch_page(self, timeout: float = 10.0) -> bool:
        return self._wait_for_ocr_keywords(
            QUICK_SWITCH_PAGE_KEYWORDS,
            timeout,
            "卡带选择页",
        )

    def _open_story_quick_switcher(self) -> NavigationResult:
        opened = self.task.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self.vision.click_template(
                QUICK_SWITCH_TEMPLATE,
                timeout=10.0,
                after_sleep=1.0,
            ),
            confirm_quick_switch_page=self._wait_for_quick_switch_page,
        )
        if not opened:
            return NavigationResult(
                False,
                self.classify(),
                "无法从主页打开快速切换卡带页面",
            )

        self._status("导航状态", "选择剧情游戏卡")
        self.task.operate_click(*STORY_CATEGORY_POINT, after_sleep=0.5)
        if not self._wait_for_story_category():
            return NavigationResult(
                False,
                self.classify(),
                "点击后未确认剧情游戏卡类别高亮",
            )
        return NavigationResult(True, ScreenState.CARD_MENU, "剧情游戏卡类别已确认")

    def _wait_for_story_category(self, timeout: float = 3.0, interval: float = 0.5) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        last_highlight_ratio = 0.0
        story_label = normalize_text("剧情游戏卡")
        while monotonic() <= end_at:
            frame = self.vision.capture()
            text = self.vision.simplify(self.vision.ocr_text(frame, "剧情游戏卡类别"))
            last_text = text or last_text
            last_highlight_ratio = self.vision.bright_neutral_ratio(
                frame,
                STORY_CATEGORY_HIGHLIGHT_REGION,
            )
            self._status("剧情类别高亮", f"{last_highlight_ratio:.3f}")
            if (
                story_label in normalize_text(text)
                and last_highlight_ratio >= STORY_CATEGORY_HIGHLIGHT_MIN_RATIO
            ):
                return True
            self.task.sleep(interval)
        self.task.log_warning(
            "跑商：未确认剧情游戏卡类别高亮，"
            f"highlight={last_highlight_ratio:.3f}, OCR={last_text or '-'}。"
        )
        return False

    def _story_badge_detections(
        self,
        frame: np.ndarray,
    ) -> tuple[StoryBadgeDetection, ...]:
        height, width = frame.shape[:2]
        client_scale = min(width / 1920, height / 1080)
        peak_radius = max(2, round(5 * client_scale))
        cluster_radius = max(4, round(STORY_BADGE_CLUSTER_RADIUS * client_scale))
        candidates: list[StoryBadgeCandidate] = []
        for number, spec in STORY_BADGE_SPECS:
            matches = self.vision.match_all(
                frame,
                spec,
                minimum_score=STORY_BADGE_CANDIDATE_SCORE,
                peak_radius=peak_radius,
            )
            candidates.extend(StoryBadgeCandidate(number, result) for result in matches)

        clusters: list[list[StoryBadgeCandidate]] = []
        for candidate in sorted(
            candidates,
            key=lambda value: value.result.score,
            reverse=True,
        ):
            for cluster in clusters:
                anchor = cluster[0].result.center
                center = candidate.result.center
                if (
                    (center[0] - anchor[0]) ** 2 + (center[1] - anchor[1]) ** 2
                    <= cluster_radius**2
                ):
                    cluster.append(candidate)
                    break
            else:
                clusters.append([candidate])

        detections: list[StoryBadgeDetection] = []
        for cluster in clusters:
            best_by_number: dict[int, StoryBadgeCandidate] = {}
            for candidate in cluster:
                current = best_by_number.get(candidate.number)
                if current is None or candidate.result.score > current.result.score:
                    best_by_number[candidate.number] = candidate
            ranked = sorted(
                best_by_number.values(),
                key=lambda value: value.result.score,
                reverse=True,
            )
            if not ranked:
                continue
            detections.append(
                StoryBadgeDetection(
                    best=ranked[0],
                    runner_up=ranked[1] if len(ranked) > 1 else None,
                )
            )
        return tuple(
            sorted(detections, key=lambda value: value.best.result.center[0])
        )

    def _find_story_badge(
        self,
        frame: np.ndarray,
        target_number: int,
    ) -> tuple[StoryBadgeDetection | None, str]:
        detections = self._story_badge_detections(frame)
        target_detections = [
            value
            for value in detections
            if value.best.number == target_number
            and value.best.result.score >= STORY_BADGE_TEMPLATE_SCORE
            and value.best.result.pixel_score >= STORY_BADGE_PIXEL_SCORE
        ]
        if not target_detections:
            return None, f"未达到双阈值，检测目标数={len(detections)}"
        if len(target_detections) > 1:
            return None, f"同一编号出现{len(target_detections)}个有效位置"
        detection = target_detections[0]
        if detection.runner_up is None:
            return None, "缺少同位置次优编号，无法检查歧义"
        if detection.margin < STORY_BADGE_MIN_MARGIN:
            return (
                None,
                f"候选分差不足：{detection.margin:.3f}<{STORY_BADGE_MIN_MARGIN:.3f}",
            )
        return detection, ""

    def _wait_for_story_badge(
        self,
        target_number: int,
        timeout: float = 3.0,
        interval: float = 0.25,
    ) -> tuple[np.ndarray, StoryBadgeDetection] | None:
        end_at = monotonic() + max(0.0, timeout)
        last_reason = "未执行识别"
        while monotonic() <= end_at:
            frame = self.vision.capture()
            detection, last_reason = self._find_story_badge(frame, target_number)
            if detection is not None:
                self._status(
                    "剧情角标",
                    (
                        f"{target_number}: match={detection.best.result.score:.3f}, "
                        f"pixel={detection.best.result.pixel_score:.3f}, "
                        f"margin={detection.margin:.3f}"
                    ),
                )
                return frame, detection
            self._status("剧情角标", f"{target_number}: {last_reason}")
            self.task.sleep(interval)
        self.task.log_warning(f"跑商：剧情游戏卡{target_number}角标识别失败：{last_reason}。")
        return None

    @staticmethod
    def _story_badge_reason_is_ambiguous(reason: str) -> bool:
        return reason.startswith(
            (
                "同一编号出现",
                "缺少同位置次优编号",
                "候选分差不足",
            )
        )

    def _wait_for_story_badge_with_scroll(
        self,
        target_number: int,
        scan_steps: int = QUICK_SWITCH_SCROLL_SCAN_STEPS,
    ) -> tuple[np.ndarray, StoryBadgeDetection] | None:
        """Find one story badge, using the quick bar's mouse-wheel direction."""

        last_reason = "未执行识别"

        def scan_current_page() -> tuple[np.ndarray, StoryBadgeDetection] | None:
            nonlocal last_reason
            frame = self.vision.capture()
            detection, last_reason = self._find_story_badge(frame, target_number)
            if detection is None:
                self._status("剧情角标", f"{target_number}: {last_reason}")
                return None
            self._status(
                "剧情角标",
                (
                    f"{target_number}: match={detection.best.result.score:.3f}, "
                    f"pixel={detection.best.result.pixel_score:.3f}, "
                    f"margin={detection.margin:.3f}"
                ),
            )
            return frame, detection

        found = scan_current_page()
        if found is not None:
            return found
        if self._story_badge_reason_is_ambiguous(last_reason):
            self.task.log_warning(
                f"跑图跑商：剧情游戏卡{target_number}角标存在歧义：{last_reason}。"
            )
            return None

        # The quick selector runs horizontally. A downward wheel moves toward
        # larger card numbers, so first reset to that edge. Scanning then uses
        # the user-calibrated upward wheel: cards move right, large to small.
        self._status("卡带滚轮", "向下复位到大编号端")
        self.task._scroll_client(
            QUICK_SWITCH_SCROLL_POINT,
            QUICK_SWITCH_SCROLL_RESET_AMOUNT,
            count=QUICK_SWITCH_SCROLL_RESET_COUNT,
            interval=QUICK_SWITCH_SCROLL_INTERVAL,
            after_sleep=QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
        )

        steps = max(0, int(scan_steps))
        for step in range(steps + 1):
            found = scan_current_page()
            if found is not None:
                return found
            if self._story_badge_reason_is_ambiguous(last_reason):
                self.task.log_warning(
                    f"跑图跑商：剧情游戏卡{target_number}角标存在歧义：{last_reason}。"
                )
                return None
            if step >= steps:
                break
            self._status("卡带滚轮", f"向上扫描 {step + 1}/{steps}")
            self.task._scroll_client(
                QUICK_SWITCH_SCROLL_POINT,
                QUICK_SWITCH_SCROLL_UP_AMOUNT,
                count=QUICK_SWITCH_SCROLL_UP_COUNT,
                interval=QUICK_SWITCH_SCROLL_INTERVAL,
                after_sleep=QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
            )

        self.task.log_warning(
            f"跑图跑商：滚动快速选择栏后仍未确认剧情游戏卡{target_number}角标："
            f"{last_reason}。"
        )
        return None

    def _handle_story_card_intermediate(self, frame: np.ndarray) -> bool:
        prompt = normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "新卡带插入提示",
                    roi=FIRST_CARD_INSERT_REGION,
                )
            )
        )
        if "未插好游戏卡" in prompt:
            clicked = self.vision.click_ocr(
                [r"插入", r"未插好游戏卡"],
                roi=FIRST_CARD_INSERT_REGION,
                after_sleep=0.8,
                name="新卡带插入",
            )
            if clicked:
                self._status("导航状态", "处理未插好游戏卡")
                return True

        skip = self.vision.match(frame, FIRST_CARD_SKIP_TEMPLATE)
        if self.vision.passes(skip, FIRST_CARD_SKIP_TEMPLATE):
            self.vision.click_client(skip.center, frame.shape, after_sleep=0.8)
            self._status("导航状态", "跳过首次卡带对话")
            return True

        confirmation = normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "首次卡带确认",
                    roi=FIRST_CARD_CONFIRM_REGION,
                )
            )
        )
        if "确认" in confirmation and self.vision.click_ocr(
            [r"确认"],
            roi=FIRST_CARD_CONFIRM_REGION,
            after_sleep=0.8,
            name="首次卡带确认",
        ):
            self._status("导航状态", "确认首次卡带对话")
            return True
        return False

    def _wait_for_story_sandbox(
        self,
        target_number: int,
        timeout: float | None = None,
        interval: float = 0.5,
    ) -> NavigationResult:
        end_at = monotonic() + max(
            0.0,
            self._loading_timeout() if timeout is None else float(timeout),
        )
        last_state = ScreenState.UNKNOWN
        while monotonic() <= end_at:
            frame = self.vision.capture()
            last_state = self.classify(frame)
            self._status("导航状态", last_state.value)
            if last_state == ScreenState.SANDBOX:
                return NavigationResult(True, last_state, f"Q_sp{target_number}")
            if last_state != ScreenState.LOADING and self._handle_story_card_intermediate(frame):
                continue
            self.task.sleep(max(0.0, interval))
        return NavigationResult(
            False,
            last_state,
            f"剧情游戏卡{target_number}入场确认超时",
        )

    def _wait_for_ocr_keywords(
        self,
        keywords: tuple[str, ...],
        timeout: float,
        name: str,
        interval: float = 0.5,
        relative_roi: tuple[float, float, float, float] | None = None,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.vision.capture()
            matched, text = self._ocr_keywords_in_frame(
                frame,
                keywords,
                name,
                relative_roi=relative_roi,
            )
            last_text = text or last_text
            if matched:
                return True
            self.task.sleep(interval)
        self.task.log_warning(f"跑商：{name} OCR确认超时，OCR={last_text or '-'}。")
        return False

    def _ocr_keywords_in_frame(
        self,
        frame: np.ndarray,
        keywords: tuple[str, ...],
        name: str,
        relative_roi: tuple[float, float, float, float] | None = None,
    ) -> tuple[bool, str]:
        required = tuple(
            normalize_text(self.vision.simplify(value)) for value in keywords
        )
        if relative_roi is None:
            text = self.vision.ocr_text(frame, name)
        else:
            text = self.vision.ocr_text(
                frame,
                name,
                relative_roi=relative_roi,
            )
        normalized = normalize_text(self.vision.simplify(text))
        matched = sum(value in normalized for value in required)
        self._status(f"{name} OCR命中", f"{matched}/{len(required)}")
        return matched == len(required), text

    def ensure_card_menu(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.CARD_MENU:
            return NavigationResult(True, state)
        returned = self.return_home()
        if not returned.success:
            return returned
        opened = self.task.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self.vision.click_template(
                QUICK_SWITCH_TEMPLATE,
                timeout=10.0,
                after_sleep=1.0,
            ),
            confirm_quick_switch_page=self._wait_for_quick_switch_page,
        )
        if opened:
            return NavigationResult(True, ScreenState.CARD_MENU)
        return NavigationResult(False, self.classify(), "无法从主页打开快速切换卡带页面")

    def select_card(self, card_id: str) -> NavigationResult:
        card = CARD_BY_ID.get(card_id)
        if card is None:
            return NavigationResult(False, ScreenState.UNKNOWN, f"未知卡带：{card_id}")
        returned = self.return_home()
        if not returned.success:
            return returned
        menu = self._open_story_quick_switcher()
        if not menu.success:
            return menu

        self._status("导航状态", f"识别剧情游戏卡{card.number}角标")
        badge_match = self._wait_for_story_badge_with_scroll(card.number)
        if badge_match is None:
            return NavigationResult(
                False,
                self.classify(),
                f"未唯一确认剧情游戏卡{card.number}角标",
            )
        badge_frame, badge = badge_match
        self._status(
            "目标卡带",
            (
                f"{card_id}: match={badge.best.result.score:.3f}, "
                f"pixel={badge.best.result.pixel_score:.3f}, "
                f"margin={badge.margin:.3f}"
            ),
        )
        self._status(
            f"剧情游戏卡{card.number}角标点击中心",
            (
                f"center=({badge.best.result.center[0]},"
                f"{badge.best.result.center[1]}), "
                f"match={badge.best.result.score:.3f}, "
                f"pixel={badge.best.result.pixel_score:.3f}, "
                f"margin={badge.margin:.3f}"
            ),
        )
        self.vision.click_client(
            badge.best.result.center,
            badge_frame.shape,
            after_sleep=1.0,
        )
        arrival = self._wait_for_story_sandbox(card.number)
        if arrival.success:
            return NavigationResult(True, arrival.state, card_id)
        return arrival

    def ensure_sandbox(self, card_id: str | None = None) -> NavigationResult:
        if self.classify() == ScreenState.SANDBOX and card_id is None:
            return NavigationResult(True, ScreenState.SANDBOX)
        if card_id is not None:
            return self.select_card(card_id)
        menu = self.ensure_card_menu()
        return menu

    def reach_merchant_shop(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.SHOP:
            return NavigationResult(True, state)
        if state not in {ScreenState.SANDBOX, ScreenState.MERCHANT_DIALOG}:
            entered = self.select_card(MERCHANT_CARD_ID)
            if not entered.success:
                return entered

        if self.classify() == ScreenState.MERCHANT_DIALOG:
            return self._bargain_and_enter_shop()
        if self.vision.click_template(MERCHANT_ICON_TEMPLATE, timeout=2.0, after_sleep=1.2):
            if self.wait_state({ScreenState.MERCHANT_DIALOG}, 8) == ScreenState.MERCHANT_DIALOG:
                return self._bargain_and_enter_shop()

        if self.vision.wait_template(MAP_MERCHANT_TEMPLATE, 3) is None:
            return NavigationResult(False, self.classify(), "小地图未找到商人")
        if not self.vision.click_template(NAV_GUIDE_TEMPLATE, timeout=3, after_sleep=0.8):
            return NavigationResult(False, self.classify(), "未找到小地图导航按钮")
        if not self.vision.click_ocr([r"商店"], roi=(220, 40, 360, 340), name="商店导航"):
            return NavigationResult(False, self.classify(), "导航菜单未识别到商店")
        self.task.sleep(0.8)
        self.vision.click_ocr([r"确认"], roi=(620, 350, 230, 240), name="商店导航确认")
        self._wait_auto_navigation(timeout=90)
        if not self.vision.click_template(MERCHANT_ICON_TEMPLATE, timeout=10, after_sleep=1.2):
            return NavigationResult(False, self.classify(), "到达商店后未找到商人交互图标")
        if self.wait_state({ScreenState.MERCHANT_DIALOG}, 10) != ScreenState.MERCHANT_DIALOG:
            return NavigationResult(False, self.classify(), "未进入商人对话")
        return self._bargain_and_enter_shop()

    def _bargain_and_enter_shop(self) -> NavigationResult:
        bargained = self.vision.click_ocr([r"砍价"], roi=(80, 520, 1000, 150), name="砍价")
        if bargained:
            self.task.sleep(0.5)
            if not self.vision.click_ocr(
                [r"砍价", r"确认"], roi=(500, 300, 380, 280), name="砍价确认"
            ):
                self.task.log_warning("跑商：砍价确认未出现，可能缺少砍价药，继续进入商店。")
        else:
            self.task.log_warning("跑商：未找到砍价选项，尝试直接进入商店。")
        self.task.sleep(0.7)
        if not self.vision.click_ocr(
            [r"商店", r"进入商店"], roi=(60, 400, 1040, 260), name="商店入口"
        ):
            # Only used after the merchant dialogue template was positively identified.
            self.vision.click_reference(130, 447, after_sleep=0.5)
        state = self.wait_state({ScreenState.SHOP}, 12)
        if state == ScreenState.SHOP:
            return NavigationResult(True, state)
        # Dialogue animation occasionally needs one harmless central click.
        self.vision.click_reference(640, 620, after_sleep=0.6)
        state = self.wait_state({ScreenState.SHOP}, 6)
        return NavigationResult(state == ScreenState.SHOP, state, "商店进入超时")

    def _wait_auto_navigation(self, timeout: float) -> None:
        end_at = monotonic() + timeout
        seen = False
        while monotonic() <= end_at:
            frame = self.vision.capture()
            active = self.vision.match(frame, AUTO_NAV_TEMPLATE).score >= self.vision.threshold_for(
                AUTO_NAV_TEMPLATE
            )
            seen = seen or active
            if seen and not active:
                return
            if not seen and self.vision.match(
                frame, MERCHANT_ICON_TEMPLATE
            ).score >= self.vision.threshold_for(MERCHANT_ICON_TEMPLATE):
                return
            self.task.sleep(0.5)

    def ensure_area_map(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.AREA_MAP:
            return NavigationResult(True, state)
        if state != ScreenState.SANDBOX:
            return NavigationResult(False, state, "不在箱庭，无法打开传送地图")
        if self.vision.click_template(HAND_TEMPLATE, timeout=2.0, after_sleep=0.8):
            if self.wait_state({ScreenState.AREA_MAP}, 8) == ScreenState.AREA_MAP:
                return NavigationResult(True, ScreenState.AREA_MAP)

        self.vision.click_reference(148, 119, after_sleep=0.8)
        if self.vision.click_template(NAV_TELEPORT_TEMPLATE, timeout=5, after_sleep=0.7):
            self.vision.click_ocr([r"确认", r"生成"], roi=(550, 300, 320, 260), name="传送阵导航")
            self._wait_auto_navigation(90)
            if self.vision.click_template(HAND_TEMPLATE, timeout=10, after_sleep=0.8):
                if self.wait_state({ScreenState.AREA_MAP}, 8) == ScreenState.AREA_MAP:
                    return NavigationResult(True, ScreenState.AREA_MAP)
        return NavigationResult(False, self.classify(), "无法打开传送地图")

    def enter_collection_submap(self, submap_index: int) -> NavigationResult:
        area = self.ensure_area_map()
        if not area.success:
            return area
        for _ in range(5):
            if not self.vision.click_template(MAP_LEFT_TEMPLATE, timeout=0.7, after_sleep=0.25):
                break
        for _ in range(submap_index + 1):
            if not self.vision.click_template(MAP_RIGHT_TEMPLATE, timeout=2.0, after_sleep=0.35):
                return NavigationResult(False, self.classify(), "区域图无法向右切换")

        clicked = any(
            self.vision.click_template(spec, timeout=1.5, after_sleep=0.5)
            for spec in TELEPORT_MAP_TEMPLATES
        )
        if not clicked and self.vision.click_template(
            OVERLAP_ARROW_TEMPLATE, timeout=1.0, after_sleep=0.4
        ):
            clicked = any(
                self.vision.click_template(spec, timeout=1.5, after_sleep=0.5)
                for spec in TELEPORT_MAP_TEMPLATES
            )
        if not clicked:
            return NavigationResult(False, self.classify(), "区域图未找到目标传送阵")
        self.vision.click_ocr([r"确认", r"生成"], roi=(540, 300, 350, 280), name="传送确认")
        state = self.wait_state({ScreenState.SANDBOX, ScreenState.LOADING}, 8)
        if state == ScreenState.LOADING:
            state = self.wait_state({ScreenState.SANDBOX}, self._loading_timeout())
        return NavigationResult(state == ScreenState.SANDBOX, state, "传送小图超时")

    def return_home(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.HOME:
            return NavigationResult(True, state)
        if state == ScreenState.SHOP:
            return self._return_home_from_discount_shop()

        for _ in range(6):
            state = self.classify()
            if state == ScreenState.HOME:
                return NavigationResult(True, state)
            if state == ScreenState.LOADING:
                self.wait_state(
                    {ScreenState.HOME, ScreenState.SANDBOX, ScreenState.CARD_MENU},
                    self._loading_timeout(),
                )
                continue
            self.vision.click_reference(82, 36, after_sleep=0.8)
        state = self.classify()
        return NavigationResult(state == ScreenState.HOME, state, "返回章节主页失败")

    def _return_home_from_discount_shop(self) -> NavigationResult:
        self.vision.click_reference(82, 36, after_sleep=0.0)
        if not self._wait_for_ocr_keywords(
            DISCOUNT_SHOP_CLOSE_KEYWORDS,
            DISCOUNT_SHOP_CLOSE_TIMEOUT,
            "折扣商店关闭确认",
            interval=0.25,
            relative_roi=DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
        ):
            return NavigationResult(
                False,
                self.classify(),
                "点击返回后未识别到折扣商店关闭确认",
            )

        self.task.operate_click(*DISCOUNT_SHOP_CLOSE_POINT, after_sleep=0.8)
        self.vision.click_reference(82, 36, after_sleep=0.8)
        self.task.operate_click(*CHAPTER_HOME_POINT, after_sleep=0.0)
        if self._wait_for_cartridge_home(timeout=RETURN_HOME_TIMEOUT):
            return NavigationResult(True, ScreenState.HOME, "已关闭折扣商店并返回主页")
        return NavigationResult(False, self.classify(), "关闭折扣商店后未在10秒内返回主页")

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
