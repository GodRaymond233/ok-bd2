from __future__ import annotations

from time import monotonic

import numpy as np

from src.tasks.map_trade.card_status import CardStatusDetector
from src.tasks.map_trade.models import ScreenState
from src.tasks.map_trade.navigator_constants import (  # noqa: F401
    AREA_MAP_BACK_TEMPLATE,
    AREA_MAP_CHANGE_INTERVAL,
    AREA_MAP_CHANGE_TIMEOUT,
    AREA_MAP_CLICK_SETTLE_SECONDS,
    AREA_MAP_OPEN_REFERENCE_POINT,
    AREA_MAP_OPEN_RELATIVE_POINT,
    AREA_MAP_REFERENCE_SIZE,
    AREA_MAP_SCAN_LIMIT,
    AREA_MAP_TELEPORT_BRIGHT_MAXIMUM_SPREAD,
    AREA_MAP_TELEPORT_BRIGHT_MINIMUM_GRAY,
    AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
    AREA_MAP_TELEPORT_BRIGHT_RADIUS_RATIO,
    AREA_MAP_TELEPORT_CLUSTER_RADIUS,
    BARGAIN_CONFIRM_POINT,
    BARGAIN_POINT,
    BARGAIN_SHOP_CONFIRM_POPUP_KEYWORD,
    BARGAIN_SHOP_CONFIRM_STABLE_HITS,
    CHAPTER_HOME_POINT,
    CLASSIFY_CARD_MENU_CATEGORY_RELATIVE_ROI,
    CLASSIFY_CARD_MENU_TITLE_RELATIVE_ROI,
    CLASSIFY_COOKING_MATERIALS_RELATIVE_ROI,
    CLASSIFY_COOKING_TITLE_RELATIVE_ROI,
    CLASSIFY_LOADING_RELATIVE_ROI,
    CLASSIFY_SHOP_TABS_RELATIVE_ROI,
    CLASSIFY_SHOP_TITLE_RELATIVE_ROI,
    DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
    DISCOUNT_SHOP_CLOSE_KEYWORDS,
    DISCOUNT_SHOP_CLOSE_POINT,
    DISCOUNT_SHOP_CLOSE_TIMEOUT,
    FIRST_CARD_CONFIRM_REGION,
    FIRST_CARD_INSERT_REGION,
    FIRST_CARD_SKIP_TEMPLATE,
    HAND_TEMPLATE,
    HOME_BRIGHTNESS_THRESHOLD,
    HOME_TEMPLATES,
    LOADING_TEMPLATE,
    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    MERCHANT_DIALOG_CONFIRM_TIMEOUT,
    OVERLAP_ARROW_TEMPLATE,
    PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
    PROBE_QUICK_SWITCH_SCROLL_COUNT,
    PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_POINT,
    PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_STEPS,
    PROBE_STORY_BADGE_CONFIRM_SECONDS,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PAGE_KEYWORDS,
    Q_SP6_SHOP_PAGE_OCR_INTERVAL,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_STORY_NUMBER,
    QUICK_SWITCH_CARTRIDGE_REGION,
    QUICK_SWITCH_PAGE_KEYWORDS,
    QUICK_SWITCH_SCROLL_FOCUS_POINT,
    QUICK_SWITCH_SCROLL_INTERVAL,
    QUICK_SWITCH_SCROLL_POINT,
    QUICK_SWITCH_SCROLL_RESET_AMOUNT,
    QUICK_SWITCH_SCROLL_RESET_COUNT,
    QUICK_SWITCH_SCROLL_SCAN_STEPS,
    QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    QUICK_SWITCH_SCROLL_UP_AMOUNT,
    QUICK_SWITCH_SCROLL_UP_COUNT,
    QUICK_SWITCH_TEMPLATE,
    RETURN_HOME_TIMEOUT,
    SANDBOX_CONFIRM_ACTION_TEMPLATES,
    SANDBOX_INTERACTION_PROBE_INTERVAL,
    SANDBOX_INTERACTION_PROBE_TIMEOUT,
    SANDBOX_MAP_SETTLE_SECONDS,
    SANDBOX_MAP_TELEPORT_TEMPLATE,
    SANDBOX_MAP_TELEPORT_TIMEOUT,
    SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
    SANDBOX_NAVIGATION_MAP_TIMEOUT,
    SANDBOX_NAVIGATION_OCR_INTERVAL,
    SANDBOX_NAVIGATION_OPEN_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_OPEN_TEMPLATES,
    SANDBOX_NAVIGATION_OPEN_TIMEOUT,
    SANDBOX_NAVIGATION_PAGE_KEYWORDS,
    SANDBOX_NAVIGATION_PIN_TEMPLATE,
    SANDBOX_NAVIGATION_RUN_TEMPLATE,
    SANDBOX_NAVIGATION_TELEPORT_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_WALK_TIMEOUT,
    SANDBOX_SKILL_GROUP_PIXEL_SCORE,
    SANDBOX_SKILL_GROUP_SCALE_RATIOS,
    SANDBOX_SKILL_GROUP_SEARCH_ROI,
    SANDBOX_SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
    SANDBOX_SKILL_GROUP_ZNCC_SCORE,
    SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO,
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
    SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
    SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_CENTER_ROI,
    SANDBOX_SKILL_SLOT_2_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_STATE_TEMPLATES,
    SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO,
    SANDBOX_TELEPORT_SKILL_FAILURE_GROUPS,
    SANDBOX_TELEPORT_SKILL_POLL_INTERVAL,
    SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER,
    SANDBOX_TELEPORT_SKILL_RELATIVE_POINT,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
    SANDBOX_TELEPORT_SKILL_TIMEOUT,
    SANDBOX_TEMPLATES,
    SHOP_PAGE_OCR_KEYWORDS,
    STORY_BADGE_CANDIDATE_PIXEL_SCORE,
    STORY_BADGE_CANDIDATE_SCORE,
    STORY_BADGE_CANDIDATE_ZNCC_SCORE,
    STORY_BADGE_CENTER_REGION,
    STORY_BADGE_CLUSTER_RADIUS,
    STORY_BADGE_MIN_MARGIN,
    STORY_BADGE_OCR_BINARY_THRESHOLD,
    STORY_BADGE_OCR_HORIZONTAL_BORDER,
    STORY_BADGE_OCR_INNER_HEIGHT,
    STORY_BADGE_OCR_INNER_RADIUS_RATIO,
    STORY_BADGE_OCR_MIN_CONFIDENCE,
    STORY_BADGE_OCR_VERTICAL_BORDER,
    STORY_BADGE_PIXEL_SCORE,
    STORY_BADGE_SPECS,
    STORY_BADGE_TEMPLATE_SCORE,
    STORY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    STORY_CATEGORY_HIGHLIGHT_REGION,
    STORY_CATEGORY_POINT,
    STORY_SANDBOX_STABLE_HITS,
    STORY_SANDBOX_SWITCH_WINDOW,
    STORY_SANDBOX_SWITCH_WINDOW_HITS,
    TELEPORT_GENERATION_OCR_INTERVAL,
    TELEPORT_GENERATION_OCR_KEYWORDS,
    TELEPORT_GENERATION_OCR_TIMEOUT,
    TELEPORT_INTERACTION_CLICK_DELAY,
    TELEPORT_INTERACTION_POLL_INTERVAL,
    TELEPORT_INTERACTION_TIMEOUT,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FIRST_PAGE_LIMIT,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_OPEN_TIMEOUT,
    TELEPORT_MAP_RETURN_REFERENCE_POINT,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_SKILL_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
    TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    TRADE_MERCHANT_CONTEXT_TEMPLATE,
    AreaMapContext,
    LocatedStoryCard,
    ProbedStoryCard,
    SandboxConfirmation,
    StoryBadgeCandidate,
    StoryBadgeDetection,
    _sandbox_skill_template,
)
from src.tasks.map_trade.navigator_sandbox import SandboxNavigationMixin
from src.tasks.map_trade.navigator_story import StoryCardNavigationMixin
from src.tasks.map_trade.navigator_trade import TradeNavigationMixin
from src.tasks.map_trade.vision import Vision, normalize_text
from src.utils.home_confirmation import (
    HOME_GACHA_OCR_RELATIVE_ROI,
    home_confirmation_passes,
)


class Navigator(StoryCardNavigationMixin, SandboxNavigationMixin, TradeNavigationMixin):
    def __init__(self, task, vision: Vision) -> None:
        self.task = task
        self.vision = vision
        self.card_status = CardStatusDetector(vision)

    def classify(self, frame=None) -> ScreenState:
        """Classify shared map states without trade- or PVP-specific templates."""

        started = monotonic()
        state = self._classify_frame(frame)
        self._status("界面分类耗时", f"{monotonic() - started:.3f}s")
        return state

    def _classify_frame(self, frame) -> ScreenState:
        frame = self.vision.capture() if frame is None else frame
        if self._home_confirmation_signals(frame)[0]:
            return ScreenState.HOME
        if self.vision.match(frame, LOADING_TEMPLATE).score >= self.vision.threshold_for(
            LOADING_TEMPLATE
        ):
            return ScreenState.LOADING
        sandbox_signals = []
        sandbox_confirmed = False
        for spec in SANDBOX_TEMPLATES:
            result = self.vision.match(frame, spec)
            passed = self.vision.passes(result, spec)
            sandbox_confirmed = sandbox_confirmed or passed
            sandbox_signals.append(
                (
                    f"{spec.name}={'pass' if passed else 'miss'}"
                    f"(m={result.score:.3f},p={result.pixel_score:.3f},"
                    f"z={result.zncc_score:.3f})"
                )
            )
        self._status("箱庭确认信号", "; ".join(sandbox_signals))
        if sandbox_confirmed:
            return ScreenState.SANDBOX

        loading_text = normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "界面分类加载",
                    relative_roi=CLASSIFY_LOADING_RELATIVE_ROI,
                )
            )
        )
        if "browndust" in loading_text:
            return ScreenState.LOADING
        shop_text = normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "界面分类商店页",
                    relative_roi=CLASSIFY_SHOP_TABS_RELATIVE_ROI,
                )
                + " "
                + self.vision.ocr_text(
                    frame,
                    "界面分类商店标题",
                    relative_roi=CLASSIFY_SHOP_TITLE_RELATIVE_ROI,
                )
            )
        )
        if self._shop_page_text(shop_text):
            return ScreenState.SHOP
        area_map_text = normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "界面分类传送阵",
                    relative_roi=TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
                )
            )
        )
        if "移动魔法阵" in area_map_text:
            return ScreenState.AREA_MAP
        card_text = normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "界面分类卡带标题",
                    relative_roi=CLASSIFY_CARD_MENU_TITLE_RELATIVE_ROI,
                )
                + " "
                + self.vision.ocr_text(
                    frame,
                    "界面分类卡带页",
                    relative_roi=CLASSIFY_CARD_MENU_CATEGORY_RELATIVE_ROI,
                )
            )
        )
        if "游戏卡珍藏" in card_text or "剧情游戏卡" in card_text:
            return ScreenState.CARD_MENU
        cooking_text = normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "界面分类料理标题",
                    relative_roi=CLASSIFY_COOKING_TITLE_RELATIVE_ROI,
                )
                + " "
                + self.vision.ocr_text(
                    frame,
                    "界面分类料理材料",
                    relative_roi=CLASSIFY_COOKING_MATERIALS_RELATIVE_ROI,
                )
            )
        )
        if "所需材料" in cooking_text and "料理" in cooking_text:
            return ScreenState.COOKING
        return ScreenState.UNKNOWN

    def classify_trade(self, frame=None) -> ScreenState:
        """Classify trade-only merchant context before falling back to shared states."""

        started = monotonic()
        state = self._classify_trade_frame(frame)
        self._status("跑商界面分类耗时", f"{monotonic() - started:.3f}s")
        return state

    def _classify_trade_frame(self, frame) -> ScreenState:
        frame = self.vision.capture() if frame is None else frame
        if self._home_confirmation_signals(frame)[0]:
            return ScreenState.HOME

        merchant = self.vision.match(frame, TRADE_MERCHANT_CONTEXT_TEMPLATE)
        merchant_passed = merchant.score >= self.vision.threshold_for(
            TRADE_MERCHANT_CONTEXT_TEMPLATE
        )
        self._status(
            "跑商商人模板",
            (
                f"{'pass' if merchant_passed else 'miss'}; "
                f"match={merchant.score:.3f}; pixel={merchant.pixel_score:.3f}; "
                f"zncc={merchant.zncc_score:.3f}"
            ),
        )
        if merchant_passed:
            # 折扣商店页的标题牌与商人对话共用 UI 框架，模板会同时命中。
            # 该判断只属于跑商流程；剧情箱庭和 PVP 均不得调用本方法。
            shop_text = normalize_text(
                self.vision.simplify(
                    self.vision.ocr_text(
                        frame,
                        "跑商界面分类商店页",
                        relative_roi=CLASSIFY_SHOP_TABS_RELATIVE_ROI,
                    )
                    + " "
                    + self.vision.ocr_text(
                        frame,
                        "跑商界面分类商店标题",
                        relative_roi=CLASSIFY_SHOP_TITLE_RELATIVE_ROI,
                    )
                )
            )
            if self._shop_page_text(shop_text):
                return ScreenState.SHOP
            return ScreenState.MERCHANT_DIALOG
        return self.classify(frame)

    @staticmethod
    def _shop_page_text(text: str) -> bool:
        """Judge the discount shop page from one frame's normalized OCR text."""
        return all(keyword in text for keyword in SHOP_PAGE_OCR_KEYWORDS) or (
            "购买" in text and "出售" in text
        )

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

    def wait_trade_state(self, wanted: set[ScreenState], timeout: float) -> ScreenState:
        """Wait for a running-trade state; merchant templates stay scoped here."""

        end_at = monotonic() + max(0.0, timeout)
        last = ScreenState.UNKNOWN
        while monotonic() <= end_at:
            last = self.classify_trade()
            self._status("跑商导航状态", last.value)
            if last in wanted:
                return last
            self.task.sleep(0.5)
        return last

    def _loading_timeout(self) -> float:
        return max(10.0, float(self.task.config.get("加载页面等待秒数", 45.0)))

    def _wait_for_cartridge_home(self, timeout: float = 10.0, interval: float = 0.35) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        last_pixel_score = -1.0
        last_brightness = 0.0
        last_gacha_text = ""
        while monotonic() <= end_at:
            frame = self.vision.capture()
            (
                confirmed,
                last_score,
                last_pixel_score,
                last_brightness,
                last_gacha_text,
            ) = self._home_confirmation_signals(
                frame,
                clear_context="跑商/跑图确认主页",
            )
            if confirmed:
                return True
            self.task.sleep(interval)
        self.task.log_warning(
            "跑商：未同时确认主页按钮、亮度和抽抽乐文字，"
            f"button={last_score:.3f}/{last_pixel_score:.3f}, "
            f"brightness={last_brightness:.3f}, ocr={last_gacha_text or '-'}。"
        )
        return False

    def _home_confirmation_signals(
        self,
        frame: np.ndarray,
        clear_context: str | None = None,
    ) -> tuple[bool, float, float, float, str]:
        candidates = [(spec, self.vision.match(frame, spec)) for spec in HOME_TEMPLATES]
        spec, result = max(candidates, key=lambda value: value[1].score)
        brightness = self.vision.template_brightness_ratio(frame, spec, result)
        button_found = self.vision.passes(result, spec)
        gacha_text = ""
        if button_found:
            gacha_text = self.vision.ocr_text(
                frame,
                "主页抽抽乐",
                relative_roi=HOME_GACHA_OCR_RELATIVE_ROI,
            )
        self._status(
            "主页小屋按钮",
            f"{result.score:.3f}/{result.pixel_score:.3f}",
        )
        self._status("主页亮度", f"{brightness:.3f}")
        self._status("主页抽抽乐 OCR", gacha_text or "-")
        confirmed = home_confirmation_passes(
            button_found=button_found,
            brightness_ratio=brightness,
            brightness_threshold=HOME_BRIGHTNESS_THRESHOLD,
            gacha_ocr_text=gacha_text,
        )
        clear_announcement = getattr(
            self.task,
            "clear_temporary_home_announcement_if_needed",
            None,
        )
        if clear_context is not None and not confirmed and callable(clear_announcement):
            clear_announcement(
                button_found=button_found,
                brightness_ratio=brightness,
                brightness_threshold=HOME_BRIGHTNESS_THRESHOLD,
                gacha_ocr_text=gacha_text,
                context=clear_context,
            )
        return confirmed, result.score, result.pixel_score, brightness, gacha_text

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
        required = tuple(normalize_text(self.vision.simplify(value)) for value in keywords)
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

    @staticmethod
    def _ocr_box_center(box) -> tuple[int, int] | None:
        values = [getattr(box, key, None) for key in ("x", "y", "width", "height")]
        raw_box = getattr(box, "box", None)
        if any(value is None for value in values) and raw_box is not None and len(raw_box) >= 4:
            values = list(raw_box[:4])
        if any(value is None for value in values):
            return None
        x, y, width, height = (float(value) for value in values)
        if width <= 0 or height <= 0:
            return None
        return round(x + width / 2), round(y + height / 2)

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
