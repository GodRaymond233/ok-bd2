from __future__ import annotations

from time import monotonic

import numpy as np

from src.tasks.map_trade.card_status import CardStatusDetector
from src.tasks.map_trade.models import COLLECTABLE_CARDS, MapPageMode, ScreenState
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
    Q_SP6_BARGAIN_CLICK_DELAY,
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
    RETURN_HOME_ANNOUNCEMENT_KEYWORD_GROUPS,
    RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS,
    RETURN_HOME_ANNOUNCEMENT_OCR_INTERVAL,
    RETURN_HOME_ANNOUNCEMENT_OCR_REGION,
    RETURN_HOME_TIMEOUT,
    SANDBOX_CONFIRM_ACTION_TEMPLATES,
    SANDBOX_INTERACTION_PROBE_INTERVAL,
    SANDBOX_INTERACTION_PROBE_TIMEOUT,
    SANDBOX_LARGE_MAP_FOOTER_OCR_RELATIVE_ROI,
    SANDBOX_LARGE_MAP_LEFT_TEMPLATE,
    SANDBOX_LARGE_MAP_RIGHT_TEMPLATE,
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
    STORY_BADGE_ENCODED_MIN_MARGIN,
    STORY_BADGE_ENCODED_PIXEL_SCORE,
    STORY_BADGE_ENCODED_TEMPLATE_SCORE,
    STORY_BADGE_ENCODED_ZNCC_SCORE,
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
    TELEPORT_MAP_DIRECT_HEADER_TEMPLATE,
    TELEPORT_MAP_FIRST_PAGE_LIMIT,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_GENERATE_HEADER_TEMPLATE,
    TELEPORT_MAP_HEADER_OCR_RELATIVE_ROI,
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
    MapPageDetection,
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
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    HOME_GACHA_OCR_RELATIVE_ROI,
    home_confirmation_passes,
)


class Navigator(StoryCardNavigationMixin, SandboxNavigationMixin, TradeNavigationMixin):
    def __init__(self, task, vision: Vision) -> None:
        self.task = task
        self.vision = vision
        self.card_status = CardStatusDetector(vision)

    def _map_page_template_signal(
        self,
        frame: np.ndarray,
        spec,
    ) -> tuple[bool, str]:
        result = self.vision.match(frame, spec)
        passed = self.vision.passes(result, spec)
        return passed, (
            f"{spec.name}={'pass' if passed else 'miss'}"
            f"(m={result.score:.3f},p={result.pixel_score:.3f},z={result.zncc_score:.3f})"
        )

    @staticmethod
    def _known_story_map_title_in_text(normalized_text: str) -> bool:
        return any(
            normalize_text(title) in normalized_text
            for card in COLLECTABLE_CARDS
            for target in card.targets
            for title in target.titles
        )

    def _detect_map_page_mode(self, frame: np.ndarray) -> MapPageDetection:
        """Identify the three visually distinct map pages from one frame."""

        try:
            header_text = self.vision.simplify(
                self.vision.ocr_text(
                    frame,
                    "地图页面左上标题",
                    relative_roi=TELEPORT_MAP_HEADER_OCR_RELATIVE_ROI,
                )
            )
        except Exception as exc:
            self._status("地图页面左上标题 OCR错误", str(exc))
            return MapPageDetection(MapPageMode.UNKNOWN)

        normalized_header = normalize_text(header_text)
        evidence: list[str] = []
        mode = MapPageMode.UNKNOWN
        footer_text = ""
        if normalize_text("移动魔法阵") in normalized_header:
            direct, direct_evidence = self._map_page_template_signal(
                frame,
                TELEPORT_MAP_DIRECT_HEADER_TEMPLATE,
            )
            generated, generated_evidence = self._map_page_template_signal(
                frame,
                TELEPORT_MAP_GENERATE_HEADER_TEMPLATE,
            )
            left, left_evidence = self._map_page_template_signal(
                frame,
                TELEPORT_MAP_FORWARD_TEMPLATE,
            )
            right, right_evidence = self._map_page_template_signal(
                frame,
                TELEPORT_MAP_BACKWARD_TEMPLATE,
            )
            evidence.extend(
                (direct_evidence, generated_evidence, left_evidence, right_evidence)
            )
            has_legend = normalize_text("传说") in normalized_header
            has_teleport_structure = left or right
            if direct and not generated and not has_legend and has_teleport_structure:
                mode = MapPageMode.DIRECT_TELEPORT
            elif generated and not direct and has_legend and has_teleport_structure:
                mode = MapPageMode.GENERATE_TELEPORT
        elif self._known_story_map_title_in_text(normalized_header):
            try:
                footer_text = self.vision.simplify(
                    self.vision.ocr_text(
                        frame,
                        "箱庭大地图底部控件",
                        relative_roi=SANDBOX_LARGE_MAP_FOOTER_OCR_RELATIVE_ROI,
                    )
                )
            except Exception as exc:
                self._status("箱庭大地图底部控件 OCR错误", str(exc))
                footer_text = ""
            normalized_footer = normalize_text(footer_text)
            keyword_hits = sum(
                normalize_text(keyword) in normalized_footer
                for keyword in SANDBOX_NAVIGATION_PAGE_KEYWORDS
            )
            evidence.append(f"箱庭大地图关键词={keyword_hits}/3")
            if keyword_hits >= 2:
                left, left_evidence = self._map_page_template_signal(
                    frame,
                    SANDBOX_LARGE_MAP_LEFT_TEMPLATE,
                )
                right, right_evidence = self._map_page_template_signal(
                    frame,
                    SANDBOX_LARGE_MAP_RIGHT_TEMPLATE,
                )
                evidence.extend((left_evidence, right_evidence))
                if left and right:
                    mode = MapPageMode.SANDBOX_LARGE_MAP

        self._status(
            "地图页面模式",
            (
                f"mode={mode.value}; header={header_text or '-'}; "
                f"footer={footer_text or '-'}; {'; '.join(evidence) or 'no-evidence'}"
            ),
        )
        return MapPageDetection(
            mode,
            header_text=header_text,
            footer_text=footer_text,
            evidence=tuple(evidence),
        )

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
        map_page = self._detect_map_page_mode(frame)
        if map_page.mode.is_teleport_map:
            return ScreenState.AREA_MAP
        if map_page.mode == MapPageMode.SANDBOX_LARGE_MAP:
            return ScreenState.SANDBOX_MAP
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

    def _wait_for_cartridge_home(
        self,
        timeout: float = 10.0,
        interval: float = 0.35,
        *,
        allow_return_announcement_cleanup: bool = False,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        last_pixel_score = -1.0
        last_brightness = 0.0
        last_gacha_text = ""
        last_button_found = False
        announcement_clicks = 0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            (
                confirmed,
                last_score,
                last_pixel_score,
                last_brightness,
                last_gacha_text,
                last_button_found,
            ) = self._home_confirmation_signals(
                frame,
                clear_context=(
                    None
                    if allow_return_announcement_cleanup
                    else "跑商/跑图确认主页"
                ),
            )
            if confirmed:
                return True
            return_announcement_detected = (
                allow_return_announcement_cleanup
                and self._clear_return_home_announcement_if_needed(
                    frame,
                    brightness_ratio=last_brightness,
                    allow_click=(
                        announcement_clicks < RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS
                    ),
                )
            )
            if return_announcement_detected:
                if announcement_clicks < RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS:
                    announcement_clicks += 1
                self.task.sleep(RETURN_HOME_ANNOUNCEMENT_OCR_INTERVAL)
                continue
            if allow_return_announcement_cleanup:
                clear_announcement = getattr(
                    self.task,
                    "clear_temporary_home_announcement_if_needed",
                    None,
                )
                if callable(clear_announcement) and clear_announcement(
                    button_found=last_button_found,
                    brightness_ratio=last_brightness,
                    brightness_threshold=HOME_BRIGHTNESS_THRESHOLD,
                    gacha_ocr_text=last_gacha_text,
                    context="跑商/跑图确认主页",
                ):
                    self.task.sleep(interval)
                    continue
            self.task.sleep(interval)
        self.task.log_warning(
            "跑商：未同时确认主页按钮、亮度和抽抽乐文字，"
            f"button={last_score:.3f}/{last_pixel_score:.3f}, "
            f"brightness={last_brightness:.3f}, ocr={last_gacha_text or '-'}。"
        )
        return False

    def _clear_return_home_announcement_if_needed(
        self,
        frame: np.ndarray,
        *,
        brightness_ratio: float,
        allow_click: bool = True,
    ) -> bool:
        """Dismiss a verified update notice only inside an explicit home return."""

        if brightness_ratio >= HOME_BRIGHTNESS_THRESHOLD:
            return False
        text = self.vision.ocr_text(
            frame,
            "返回主页公告",
            relative_roi=RETURN_HOME_ANNOUNCEMENT_OCR_REGION,
        )
        normalized = normalize_text(self.vision.simplify(text))
        matched_group = next(
            (
                keywords
                for keywords in RETURN_HOME_ANNOUNCEMENT_KEYWORD_GROUPS
                if all(
                    normalize_text(self.vision.simplify(keyword)) in normalized
                    for keyword in keywords
                )
            ),
            None,
        )
        self._status("返回主页公告 OCR", text or "-")
        if matched_group is None:
            return False
        if not allow_click:
            self._status(
                "返回主页公告清理",
                f"已达到{RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS}次上限",
            )
            return True
        self.task.log_info(
            "跑商：返回主页时确认更新公告遮挡，点击公告清理位置后重新严格确认主页，"
            f"keywords={'+'.join(matched_group)}, brightness={brightness_ratio:.3f}。"
        )
        self.task.operate_click(
            *HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
            after_sleep=0.2,
        )
        return True

    def _home_confirmation_signals(
        self,
        frame: np.ndarray,
        clear_context: str | None = None,
    ) -> tuple[bool, float, float, float, str, bool]:
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
        return (
            confirmed,
            result.score,
            result.pixel_score,
            brightness,
            gacha_text,
            button_found,
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
