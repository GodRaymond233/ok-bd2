from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from time import monotonic

import cv2
import numpy as np

from src.tasks.map_trade.card_status import (
    CardActionState,
    CollectionCardSelectionOutcome,
    CollectionCardSelectionResult,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    MatchResult,
    NavigationResult,
    ScreenState,
)
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
from src.tasks.map_trade.vision import normalize_text


class StoryCardNavigationMixin:
    def _wait_for_quick_switch_page(self, timeout: float = 10.0) -> bool:
        return self._wait_for_ocr_keywords(
            QUICK_SWITCH_PAGE_KEYWORDS,
            timeout,
            "卡带选择页",
        )

    def _open_story_quick_switcher(self) -> NavigationResult:
        opened = self.task.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self.vision.click_stable_template(
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

        if not self._select_story_category():
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

    def _select_story_category(self) -> bool:
        """Click and confirm the category before any quick-bar focus or scroll."""

        self._status("导航状态", "选择剧情游戏卡")
        self.task.operate_click(*STORY_CATEGORY_POINT, after_sleep=0.5)
        return self._wait_for_story_category()

    def _focus_quick_switch_for_scroll(self) -> None:
        """Focus the selected cartridge bar at the user-calibrated safe point."""

        self._status(
            "卡带滚轮聚焦",
            "已选择卡带类型，点击参考点(43,974)后开始滚动",
        )
        self.task.operate_click(*QUICK_SWITCH_SCROLL_FOCUS_POINT, after_sleep=0.0)

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
            key=lambda value: value.discrimination_score,
            reverse=True,
        ):
            for cluster in clusters:
                anchor = cluster[0].result.center
                center = candidate.result.center
                if (center[0] - anchor[0]) ** 2 + (center[1] - anchor[1]) ** 2 <= cluster_radius**2:
                    cluster.append(candidate)
                    break
            else:
                clusters.append([candidate])

        detections: list[StoryBadgeDetection] = []
        for cluster in clusters:
            best_by_number: dict[int, StoryBadgeCandidate] = {}
            for candidate in cluster:
                current = best_by_number.get(candidate.number)
                if current is None or candidate.discrimination_score > current.discrimination_score:
                    best_by_number[candidate.number] = candidate
            ranked = sorted(
                best_by_number.values(),
                key=lambda value: value.discrimination_score,
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
        return tuple(sorted(detections, key=lambda value: value.best.result.center[0]))

    @staticmethod
    def _story_badge_ocr_frame(
        frame: np.ndarray,
        result: MatchResult,
    ) -> np.ndarray:
        """Prepare one tiny badge as a padded text line for the shared OCR engine."""

        height, width = frame.shape[:2]
        left = max(0, result.position[0])
        top = max(0, result.position[1])
        right = min(width, result.position[0] + result.size[0])
        bottom = min(height, result.position[1] + result.size[1])
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            return np.empty((0, 0, 3), dtype=np.uint8)
        if crop.ndim == 2:
            gray = crop.copy()
        elif crop.shape[2] == 4:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGRA2GRAY)
        else:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        crop_height, crop_width = gray.shape[:2]
        center_x = (crop_width - 1) / 2
        center_y = (crop_height - 1) / 2
        inner_radius = max(
            2.0,
            min(crop_width, crop_height) * STORY_BADGE_OCR_INNER_RADIUS_RATIO,
        )
        y, x = np.mgrid[:crop_height, :crop_width]
        gray[(x - center_x) ** 2 + (y - center_y) ** 2 > inner_radius**2] = 0
        _threshold, crop = cv2.threshold(
            gray,
            STORY_BADGE_OCR_BINARY_THRESHOLD,
            255,
            cv2.THRESH_BINARY,
        )

        target_height = STORY_BADGE_OCR_INNER_HEIGHT
        target_width = max(
            1,
            round(crop.shape[1] * target_height / max(1, crop.shape[0])),
        )
        enlarged = cv2.resize(
            crop,
            (target_width, target_height),
            interpolation=cv2.INTER_CUBIC,
        )
        enlarged = cv2.cvtColor(enlarged, cv2.COLOR_GRAY2BGR)
        return cv2.copyMakeBorder(
            enlarged,
            STORY_BADGE_OCR_VERTICAL_BORDER,
            STORY_BADGE_OCR_VERTICAL_BORDER,
            STORY_BADGE_OCR_HORIZONTAL_BORDER,
            STORY_BADGE_OCR_HORIZONTAL_BORDER,
            cv2.BORDER_CONSTANT,
            value=(20, 20, 20),
        )

    def _story_badge_ocr_number(
        self,
        frame: np.ndarray,
        result: MatchResult,
    ) -> tuple[int | None, str]:
        prepared = self._story_badge_ocr_frame(frame, result)
        if prepared.size == 0:
            return None, ""
        text = self.vision.ocr_text(
            prepared,
            "剧情角标数字辅助",
            target_height=0,
            minimum_threshold=STORY_BADGE_OCR_MIN_CONFIDENCE,
        )
        numbers = {
            int(value)
            for value in re.findall(r"(?<!\d)\d{1,2}(?!\d)", str(text))
            if 1 <= int(value) <= 20
        }
        number = next(iter(numbers)) if len(numbers) == 1 else None
        self._status(
            "剧情角标 OCR",
            f"number={number if number is not None else '-'}, text={text or '-'}",
        )
        return number, text

    def _find_story_badge(
        self,
        frame: np.ndarray,
        target_number: int,
    ) -> tuple[StoryBadgeDetection | None, str]:
        detections = self._story_badge_detections(frame)
        return self._find_story_badge_from_detections(
            frame,
            target_number,
            detections,
        )

    def inspect_story_badges(
        self,
        frame: np.ndarray,
        target_numbers: Iterable[int],
    ) -> dict[int, tuple[StoryBadgeDetection | None, str]]:
        """Strictly inspect several badge numbers with one shared template scan."""

        detections = self._story_badge_detections(frame)
        return {
            int(number): self._find_story_badge_from_detections(
                frame,
                int(number),
                detections,
            )
            for number in dict.fromkeys(target_numbers)
        }

    def _find_story_badge_from_detections(
        self,
        frame: np.ndarray,
        target_number: int,
        detections: tuple[StoryBadgeDetection, ...],
    ) -> tuple[StoryBadgeDetection | None, str]:
        target_detections = [
            value
            for value in detections
            if value.best.number == target_number
            and value.best.result.score >= STORY_BADGE_TEMPLATE_SCORE
            and value.best.result.pixel_score >= STORY_BADGE_PIXEL_SCORE
        ]
        if not target_detections:
            return (
                None,
                (
                    "未达到角标双阈值："
                    f"match>={STORY_BADGE_TEMPLATE_SCORE:.3f}, "
                    f"pixel>={STORY_BADGE_PIXEL_SCORE:.3f}, "
                    f"检测目标数={len(detections)}"
                ),
            )
        if len(target_detections) > 1:
            return None, f"同一编号出现{len(target_detections)}个有效位置"
        detection = target_detections[0]
        if detection.runner_up is None:
            return None, "缺少同位置次优编号，无法检查歧义"
        if detection.margin < STORY_BADGE_MIN_MARGIN:
            return (
                None,
                (f"候选分差不足（ZNCC）：{detection.margin:.3f}<{STORY_BADGE_MIN_MARGIN:.3f}"),
            )
        ocr_number, ocr_text = self._story_badge_ocr_number(
            frame,
            detection.best.result,
        )
        detection = replace(
            detection,
            ocr_text=ocr_text,
            ocr_number=ocr_number,
        )
        if ocr_number is not None and ocr_number != target_number:
            return (
                None,
                (
                    "角标OCR数字冲突："
                    f"模板={target_number}, OCR={ocr_number}, text={ocr_text or '-'}"
                ),
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
                        f"zncc={detection.best.result.zncc_score:.3f}, "
                        f"margin={detection.margin:.3f}, "
                        f"ocr={detection.ocr_number if detection.ocr_number is not None else '-'}"
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
                "角标OCR数字冲突",
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
                    f"zncc={detection.best.result.zncc_score:.3f}, "
                    f"margin={detection.margin:.3f}, "
                    f"ocr={detection.ocr_number if detection.ocr_number is not None else '-'}"
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

        self._focus_quick_switch_for_scroll()
        # The quick selector runs horizontally. A downward wheel moves toward
        # larger card numbers, so first reset to that edge. Scanning then uses
        # the user-calibrated upward wheel: cards move right, large to small.
        self._status("卡带滚轮", "向下复位到大编号端")
        self.task.scroll_client(
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
            self.task.scroll_client(
                QUICK_SWITCH_SCROLL_POINT,
                QUICK_SWITCH_SCROLL_UP_AMOUNT,
                count=QUICK_SWITCH_SCROLL_UP_COUNT,
                interval=QUICK_SWITCH_SCROLL_INTERVAL,
                after_sleep=QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
            )

        self.task.log_warning(
            f"跑图跑商：滚动快速选择栏后仍未确认剧情游戏卡{target_number}角标：{last_reason}。"
        )
        return None

    def locate_probe_story_card(
        self,
        card_id: str,
        scan_steps: int = QUICK_SWITCH_SCROLL_SCAN_STEPS,
    ) -> ProbedStoryCard | None:
        """Locate and read one card in an already-open story quick selector.

        This probe-only path preserves the current horizontal viewport.  It
        advances toward later cards in short wheel batches, waits for the
        selector to settle, and never uses the formal selector's reset-to-edge
        operation. ``scan_steps`` is the total wheel-event limit, not the
        number of recognition attempts.
        """

        card = CARD_BY_ID.get(card_id)
        if card is None:
            self.task.log_warning(f"剧情卡带合并测试：未知卡带：{card_id}。")
            return None

        scroll_limit = max(0, int(scan_steps))
        scrolled = 0
        last_reason = "未执行识别"
        scroll_focused = False
        while True:
            frame = self.vision.capture()
            badge, last_reason = self._find_story_badge(frame, card.number)
            if badge is None:
                self._status("剧情角标", f"{card.number}: {last_reason}")
                if self._story_badge_reason_is_ambiguous(last_reason):
                    self.task.log_warning(
                        f"剧情卡带合并测试：剧情游戏卡{card.number}角标存在歧义：{last_reason}。"
                    )
                    return None
            else:
                try:
                    completion = self.card_status.detect(
                        frame,
                        badge.best.result.center,
                    )
                except (RuntimeError, ValueError, cv2.error) as error:
                    self.task.log_warning(f"剧情卡带合并测试：{card_id}完成度识别异常：{error}。")
                    return None
                if completion.complete_region:
                    self.task.sleep(PROBE_STORY_BADGE_CONFIRM_SECONDS)
                    confirmed_frame = self.vision.capture()
                    confirmed_badge, confirmed_reason = self._find_story_badge(
                        confirmed_frame,
                        card.number,
                    )
                    if confirmed_badge is None:
                        last_reason = f"等待0.4秒后角标复核失败：{confirmed_reason}"
                        self._status("剧情角标", f"{card.number}: {last_reason}")
                        self.task.log_warning(
                            f"剧情卡带合并测试：剧情游戏卡{card.number}"
                            f"角标在点击前复核失败：{confirmed_reason}。"
                        )
                        return None
                    else:
                        try:
                            confirmed_completion = self.card_status.detect(
                                confirmed_frame,
                                confirmed_badge.best.result.center,
                            )
                        except (RuntimeError, ValueError, cv2.error) as error:
                            self.task.log_warning(
                                f"剧情卡带合并测试：{card_id}复核帧完成度识别异常：{error}。"
                            )
                            return None
                        if confirmed_completion.complete_region:
                            located = LocatedStoryCard(
                                card,
                                confirmed_frame,
                                confirmed_badge,
                            )
                            self._status(
                                "目标卡带",
                                (
                                    f"{card_id}: center="
                                    f"({confirmed_badge.best.result.center[0]},"
                                    f"{confirmed_badge.best.result.center[1]}), "
                                    f"match={confirmed_badge.best.result.score:.3f}, "
                                    f"pixel={confirmed_badge.best.result.pixel_score:.3f}, "
                                    f"zncc={confirmed_badge.best.result.zncc_score:.3f}, "
                                    f"margin={confirmed_badge.margin:.3f}"
                                ),
                            )
                            return ProbedStoryCard(located, confirmed_completion)
                        last_reason = "角标复核帧的吸取/压制区域被客户区边缘截断"
                        self._status("卡带完成度", last_reason)
                else:
                    last_reason = "角标已命中，但吸取/压制区域被客户区边缘截断"
                    self._status("卡带完成度", last_reason)

            if scrolled >= scroll_limit:
                break
            if not scroll_focused:
                self._focus_quick_switch_for_scroll()
                scroll_focused = True
            batch_count = min(
                PROBE_QUICK_SWITCH_SCROLL_COUNT,
                scroll_limit - scrolled,
            )
            scrolled += batch_count
            self._status(
                "卡带滚轮",
                f"后续卡带滚动 {scrolled}/{scroll_limit}（本批{batch_count}次）",
            )
            self.task.scroll_client(
                PROBE_QUICK_SWITCH_SCROLL_POINT,
                PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
                count=batch_count,
                interval=PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
                after_sleep=PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
            )

        self.task.log_warning(
            f"剧情卡带合并测试：分批向上滚动后仍未确认剧情游戏卡{card.number}：{last_reason}。"
        )
        return None

    def enter_probe_story_card(self, probed: ProbedStoryCard) -> NavigationResult:
        """Click the revalidated probe center and confirm a stable sandbox."""

        return self._enter_located_story_card(probed.located)

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

    def ensure_card_menu(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.CARD_MENU:
            return NavigationResult(True, state)
        returned = self.return_home()
        if not returned.success:
            return returned
        opened = self.task.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self.vision.click_stable_template(
                QUICK_SWITCH_TEMPLATE,
                timeout=10.0,
                after_sleep=1.0,
            ),
            confirm_quick_switch_page=self._wait_for_quick_switch_page,
        )
        if opened:
            return NavigationResult(True, ScreenState.CARD_MENU)
        return NavigationResult(False, self.classify(), "无法从主页打开快速切换卡带页面")

    def _locate_story_card(
        self,
        card_id: str,
    ) -> LocatedStoryCard | NavigationResult:
        card = CARD_BY_ID.get(card_id)
        if card is None:
            return NavigationResult(False, ScreenState.UNKNOWN, f"未知卡带：{card_id}")
        if self.classify() == ScreenState.CARD_MENU:
            if not self._wait_for_quick_switch_page():
                return NavigationResult(
                    False,
                    ScreenState.CARD_MENU,
                    "当前快速切换卡带页面未通过三项 OCR 确认",
                )
            if not self._wait_for_story_category() and not self._select_story_category():
                return NavigationResult(
                    False,
                    ScreenState.CARD_MENU,
                    "当前快速切换页未确认剧情游戏卡类别",
                )
        else:
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
                f"zncc={badge.best.result.zncc_score:.3f}, "
                f"margin={badge.margin:.3f}, "
                f"ocr={badge.ocr_number if badge.ocr_number is not None else '-'}"
            ),
        )
        return LocatedStoryCard(card, badge_frame, badge)

    def _enter_located_story_card(
        self,
        located: LocatedStoryCard,
    ) -> NavigationResult:
        card = located.card
        badge_frame = located.frame
        badge = located.badge
        self._status(
            f"剧情游戏卡{card.number}角标点击中心",
            (
                f"center=({badge.best.result.center[0]},"
                f"{badge.best.result.center[1]}), "
                f"match={badge.best.result.score:.3f}, "
                f"pixel={badge.best.result.pixel_score:.3f}, "
                f"zncc={badge.best.result.zncc_score:.3f}, "
                f"margin={badge.margin:.3f}, "
                f"ocr={badge.ocr_number if badge.ocr_number is not None else '-'}"
            ),
        )
        self.vision.click_client(
            badge.best.result.center,
            badge_frame.shape,
            after_sleep=1.0,
        )
        arrival = self._wait_for_story_sandbox(card.number)
        if arrival.success:
            return NavigationResult(True, arrival.state, card.card_id)
        return arrival

    def select_card(self, card_id: str) -> NavigationResult:
        located = self._locate_story_card(card_id)
        if isinstance(located, NavigationResult):
            return located
        return self._enter_located_story_card(located)

    def select_collection_card(
        self,
        card_id: str,
        *,
        enter_visually_complete: bool = False,
    ) -> CollectionCardSelectionResult:
        card = CARD_BY_ID.get(card_id)
        if card is None or not card.collectable:
            navigation = NavigationResult(
                False,
                ScreenState.UNKNOWN,
                f"非跑图剧情卡带：{card_id}",
            )
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                navigation,
            )

        located = self._locate_story_card(card_id)
        if isinstance(located, NavigationResult):
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                located,
            )

        completion = None
        try:
            completion = self.card_status.detect(
                located.frame,
                located.badge.best.result.center,
            )
        except (RuntimeError, ValueError, cv2.error) as error:
            self.task.log_warning(f"地图采集：{card_id}完成度识别异常，按未知继续进入：{error}。")

        if completion is not None:
            self._status(
                "卡带吸取状态",
                f"{completion.absorb.state.value}: {completion.absorb.reason}",
            )
            self._status(
                "卡带压制状态",
                f"{completion.suppress.state.value}: {completion.suppress.reason}",
            )
            self._status("卡带完成度", completion.state.value)
            if completion.state == CardActionState.COMPLETED and not enter_visually_complete:
                navigation = NavigationResult(
                    True,
                    ScreenState.CARD_MENU,
                    f"{card_id}视觉确认吸取与压制均完成",
                )
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    navigation,
                    completion,
                )

        navigation = self._enter_located_story_card(located)
        outcome = (
            CollectionCardSelectionOutcome.ENTERED
            if navigation.success
            else CollectionCardSelectionOutcome.FAILED
        )
        return CollectionCardSelectionResult(
            outcome,
            navigation,
            completion,
        )

    def inspect_collection_card_completion(
        self,
        card_id: str,
    ) -> CollectionCardSelectionResult:
        """Read one card's absorb/suppress badges without entering the card."""

        card = CARD_BY_ID.get(card_id)
        if card is None or not card.collectable:
            navigation = NavigationResult(
                False,
                ScreenState.UNKNOWN,
                f"非跑图剧情卡带：{card_id}",
            )
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                navigation,
            )
        located = self._locate_story_card(card_id)
        if isinstance(located, NavigationResult):
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                located,
            )
        try:
            completion = self.card_status.detect(
                located.frame,
                located.badge.best.result.center,
            )
        except (RuntimeError, ValueError, cv2.error) as error:
            navigation = NavigationResult(
                False,
                ScreenState.CARD_MENU,
                f"{card_id}完成度识别异常：{error}",
            )
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                navigation,
            )
        self._status(
            "卡带吸取状态",
            f"{completion.absorb.state.value}: {completion.absorb.reason}",
        )
        self._status(
            "卡带压制状态",
            f"{completion.suppress.state.value}: {completion.suppress.reason}",
        )
        self._status("卡带完成度", completion.state.value)
        completed = completion.state == CardActionState.COMPLETED
        return CollectionCardSelectionResult(
            (
                CollectionCardSelectionOutcome.VISUALLY_COMPLETE
                if completed
                else CollectionCardSelectionOutcome.FAILED
            ),
            NavigationResult(
                completed,
                ScreenState.CARD_MENU,
                (
                    f"{card_id}视觉确认吸取与压制均完成"
                    if completed
                    else f"{card_id}未同时确认吸取与压制完成"
                ),
            ),
            completion,
        )

    def open_story_quick_switcher_from_sandbox(
        self,
        *,
        sandbox_already_confirmed: bool = False,
    ) -> NavigationResult:
        """Open the story quick-switch page without detouring through the global home."""

        if not sandbox_already_confirmed:
            sandbox = self._wait_for_current_sandbox()
            if not sandbox.success:
                return sandbox

        self._status("导航状态", "从卡带箱庭识别快速切换按钮")
        if not self.vision.click_stable_template(
            QUICK_SWITCH_TEMPLATE,
            timeout=10.0,
            after_sleep=1.0,
        ):
            return NavigationResult(
                False,
                ScreenState.SANDBOX,
                "卡带箱庭内未稳定识别到快速切换按钮",
            )
        if not self._wait_for_quick_switch_page():
            return NavigationResult(
                False,
                self.classify(),
                "点击快速切换按钮后未确认卡带选择页",
            )

        if not self._select_story_category():
            return NavigationResult(
                False,
                self.classify(),
                "点击后未确认剧情游戏卡类别高亮",
            )
        return NavigationResult(True, ScreenState.CARD_MENU, "剧情游戏卡类别已确认")

