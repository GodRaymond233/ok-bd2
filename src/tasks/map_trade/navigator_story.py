from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from math import ceil
from statistics import median
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
    STORY_BADGE_ENCODED_MIN_MARGIN,
    STORY_BADGE_ENCODED_PIXEL_SCORE,
    STORY_BADGE_ENCODED_TEMPLATE_SCORE,
    STORY_BADGE_ENCODED_ZNCC_SCORE,
    STORY_BADGE_GRID_ALIGNMENT_TOLERANCE_RATIO,
    STORY_BADGE_GRID_LOCAL_TOLERANCE_RATIO,
    STORY_BADGE_GRID_MAX_PAIR_GAP,
    STORY_BADGE_GRID_MAX_SPACING_RATIO,
    STORY_BADGE_GRID_MIN_ANCHORS,
    STORY_BADGE_GRID_MIN_COMBINED_MARGIN,
    STORY_BADGE_GRID_MIN_MARGIN,
    STORY_BADGE_GRID_MIN_SPACING_BADGE_RATIO,
    STORY_BADGE_GRID_MIN_SPACING_RATIO,
    STORY_BADGE_GRID_MIN_VISIBLE_FRACTION,
    STORY_BADGE_GRID_OCR_MARGIN,
    STORY_BADGE_GRID_PIXEL_SCORE,
    STORY_BADGE_GRID_REFERENCE_SCALE,
    STORY_BADGE_GRID_ROW_TOLERANCE_RATIO,
    STORY_BADGE_GRID_SPACING_RATIO,
    STORY_BADGE_GRID_STRONG_COMBINED_SCORE,
    STORY_BADGE_GRID_TEMPLATE_SCORE,
    STORY_BADGE_GRID_VERTICAL_TOLERANCE_RATIO,
    STORY_BADGE_GRID_ZNCC_SCORE,
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
    StoryBadgeGrid,
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

    def _story_badge_geometry(self, frame: np.ndarray):
        """Assess the quick-switch frame before using any badge as a target."""

        assess = getattr(self.vision, "assess_frame", None)
        self._last_story_badge_geometry = None
        self._last_story_badge_geometry_reason = ""
        if not callable(assess):
            return None
        try:
            geometry = assess(
                frame,
                required_relative_rois=(QUICK_SWITCH_CARTRIDGE_REGION,),
                purpose="剧情角标",
            )
        except (AttributeError, TypeError, ValueError) as exc:
            self._last_story_badge_geometry_reason = f"画面几何检查异常：{exc}"
            self._status("剧情角标几何", self._last_story_badge_geometry_reason)
            return False
        self._last_story_badge_geometry = geometry
        self._last_story_badge_geometry_reason = ""
        if not geometry.accepted:
            self._last_story_badge_geometry_reason = (
                "画面几何拒绝：" + "|".join(geometry.rejection_reasons)
            )
            self._status("剧情角标几何", self._last_story_badge_geometry_reason)
        return geometry

    def _story_badge_template_candidates(
        self,
        frame: np.ndarray,
        spec,
        *,
        peak_radius: int,
        geometry=None,
    ) -> tuple[MatchResult, ...]:
        """Collect weak response peaks while retaining the legacy adapter path."""

        evidence_matcher = getattr(self.vision, "match_evidence_all", None)
        if callable(evidence_matcher):
            try:
                candidates = tuple(
                    evidence_matcher(
                        frame,
                        spec,
                        minimum_score=0.25,
                        peak_radius=peak_radius,
                        max_results=12,
                        geometry=geometry,
                        purpose="剧情角标候选",
                    )
                )
            except (AttributeError, TypeError, ValueError):
                candidates = ()
            if candidates:
                return tuple(
                    getattr(candidate, "result", candidate)
                    for candidate in candidates
                )

        matcher = getattr(self.vision, "match_all", None)
        if not callable(matcher):
            return ()
        try:
            return tuple(
                matcher(
                    frame,
                    spec,
                    minimum_score=STORY_BADGE_CANDIDATE_SCORE,
                    peak_radius=peak_radius,
                    max_results=12,
                )
            )
        except (AttributeError, TypeError, ValueError):
            return ()

    def _story_badge_detections(
        self,
        frame: np.ndarray,
    ) -> tuple[StoryBadgeDetection, ...]:
        geometry = self._story_badge_geometry(frame)
        if geometry is False or (geometry is not None and not geometry.accepted):
            return ()
        height, width = frame.shape[:2]
        client_scale = (
            geometry.client_scale
            if geometry is not None
            else min(width / 1920, height / 1080)
        )
        peak_radius = max(2, round(5 * client_scale))
        cluster_radius = max(4, round(STORY_BADGE_CLUSTER_RADIUS * client_scale))
        candidates: list[StoryBadgeCandidate] = []
        for number, spec in STORY_BADGE_SPECS:
            matches = self._story_badge_template_candidates(
                frame,
                spec,
                peak_radius=peak_radius,
                geometry=geometry,
            )
            candidates.extend(
                StoryBadgeCandidate(number, result)
                for result in matches
                if result.size[0] > 0
                and result.size[1] > 0
                and 0 <= result.center[0] <= width
                and 0 <= result.center[1] <= height
            )

        if candidates:
            median_width = median(
                max(1, candidate.result.size[0]) for candidate in candidates
            )
            cluster_radius = max(
                cluster_radius,
                round(max(4.0, median_width * 0.70)),
            )

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
                if current is None or (
                    candidate.combined_score,
                    candidate.discrimination_score,
                ) > (
                    current.combined_score,
                    current.discrimination_score,
                ):
                    best_by_number[candidate.number] = candidate
            ranked = sorted(
                best_by_number.values(),
                key=lambda value: (
                    value.combined_score,
                    value.discrimination_score,
                ),
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

    def _story_badge_grid(
        self,
        frame: np.ndarray,
        detections: tuple[StoryBadgeDetection, ...],
    ) -> StoryBadgeGrid | None:
        """Fit the low-resolution quick-bar lattice from independent badge peaks."""

        height, width = frame.shape[:2]
        if len(detections) < STORY_BADGE_GRID_MIN_ANCHORS:
            return None

        badge_width = median(
            max(1, value.best.result.size[0]) for value in detections
        )
        row_tolerance = max(
            3.0,
            min(8.0, badge_width * STORY_BADGE_GRID_ROW_TOLERANCE_RATIO),
        )

        # Matching every number deliberately retains weak peaks for diagnosis,
        # but those peaks often form parallel anti-aliasing rows.  Fit the
        # lattice only from the horizontal band with the strongest structural
        # evidence; a weak row cannot outvote a real row by sheer peak count.
        rows: list[list[StoryBadgeDetection]] = []
        for detection in sorted(
            detections,
            key=lambda value: value.best.result.center[1],
        ):
            center_y = float(detection.best.result.center[1])
            for row in rows:
                row_center = median(
                    float(value.best.result.center[1]) for value in row
                )
                if abs(center_y - row_center) <= row_tolerance:
                    row.append(detection)
                    break
            else:
                rows.append([detection])
        if not rows:
            return None

        def row_rank(row: list[StoryBadgeDetection]) -> tuple[float, ...]:
            strong = [
                value
                for value in row
                if value.best.combined_score >= STORY_BADGE_GRID_STRONG_COMBINED_SCORE
            ]
            quality = sum(max(0.0, value.best.combined_score) for value in row)
            strong_quality = sum(
                max(0.0, value.best.combined_score) for value in strong
            )
            return (
                float(len(strong)),
                strong_quality,
                float(len(row)),
                quality,
            )

        row = max(rows, key=row_rank)
        if len(row) < STORY_BADGE_GRID_MIN_ANCHORS:
            return None
        x_values = sorted(
            float(value.best.result.center[0]) for value in row
        )
        y_values = [float(value.best.result.center[1]) for value in row]
        minimum_spacing = max(
            8.0,
            badge_width * STORY_BADGE_GRID_MIN_SPACING_BADGE_RATIO,
            width * STORY_BADGE_GRID_MIN_SPACING_RATIO,
        )
        maximum_spacing = max(
            minimum_spacing + 1.0,
            width * STORY_BADGE_GRID_MAX_SPACING_RATIO,
        )
        differences = {
            round(x_values[right] - x_values[left], 3)
            for left in range(len(x_values))
            for right in range(left + 1, len(x_values))
            if x_values[right] > x_values[left]
        }
        spacing_candidates: set[float] = set()
        for difference in differences:
            for divisor in range(1, STORY_BADGE_GRID_MAX_PAIR_GAP + 1):
                spacing = difference / divisor
                if minimum_spacing <= spacing <= maximum_spacing:
                    spacing_candidates.add(round(spacing, 3))
        # The historical ratio is only a seed for sparse/noisy differences;
        # acceptance still requires the observed lattice to fit dynamically.
        prior = width * STORY_BADGE_GRID_SPACING_RATIO
        if minimum_spacing <= prior <= maximum_spacing:
            spacing_candidates.add(round(prior, 3))
        if not spacing_candidates:
            return None

        def circular_error(value: float, phase: float, spacing: float) -> float:
            remainder = (value - phase) % spacing
            return min(remainder, spacing - remainder)

        def fit_phase(spacing: float, seed: float):
            tolerance = max(
                2.0,
                min(
                    spacing * max(0.02, STORY_BADGE_GRID_ALIGNMENT_TOLERANCE_RATIO),
                    max(3.0, badge_width * 0.85),
                ),
            )
            phase = seed % spacing
            aligned: dict[int, StoryBadgeDetection] = {}
            for _ in range(3):
                aligned = {}
                for detection in row:
                    center_x = float(detection.best.result.center[0])
                    slot_index = round((center_x - phase) / spacing)
                    if circular_error(center_x, phase, spacing) > tolerance:
                        continue
                    current = aligned.get(slot_index)
                    if current is None or (
                        detection.best.combined_score,
                        detection.best.discrimination_score,
                    ) > (
                        current.best.combined_score,
                        current.best.discrimination_score,
                    ):
                        aligned[slot_index] = detection
                if not aligned:
                    break
                signed_errors = tuple(
                    ((
                        float(value.best.result.center[0])
                        - (phase + slot_index * spacing)
                        + spacing / 2
                    ) % spacing)
                    - spacing / 2
                    for slot_index, value in aligned.items()
                )
                phase = (phase + median(signed_errors)) % spacing
            return phase, aligned, tolerance

        trials = []
        for spacing in spacing_candidates:
            seeds = tuple(value % spacing for value in x_values)
            for seed in seeds:
                phase, aligned, tolerance = fit_phase(spacing, seed)
                if not aligned:
                    continue
                expected_visible_slots = max(1, ceil(width / spacing))
                required_anchors = max(
                    STORY_BADGE_GRID_MIN_ANCHORS,
                    ceil(expected_visible_slots * STORY_BADGE_GRID_MIN_VISIBLE_FRACTION),
                )
                residual = sum(
                    circular_error(
                        float(value.best.result.center[0]),
                        phase,
                        spacing,
                    )
                    for value in aligned.values()
                )
                quality = sum(
                    max(0.0, value.best.combined_score)
                    for value in aligned.values()
                )
                trials.append(
                    (
                        len(aligned),
                        len(aligned) / expected_visible_slots,
                        quality,
                        -residual,
                        -abs(spacing - prior),
                        spacing,
                        phase,
                        aligned,
                        tolerance,
                        required_anchors,
                    )
                )
        if not trials:
            return None
        (
            _count,
            _fraction,
            _quality,
            _residual,
            _prior_distance,
            spacing,
            phase,
            aligned,
            _tolerance,
            required,
        ) = max(
            trials,
            key=lambda value: value[:5],
        )
        if len(aligned) < required:
            return None

        center_y = float(median(y_values))
        vertical_tolerance = max(
            3.0,
            height * STORY_BADGE_GRID_VERTICAL_TOLERANCE_RATIO,
            badge_width * 0.85,
        )
        aligned = {
            slot_index: value
            for slot_index, value in aligned.items()
            if abs(value.best.result.center[1] - center_y) <= vertical_tolerance
        }
        if len(aligned) < required:
            return None
        center_y = float(median(value.best.result.center[1] for value in aligned.values()))
        return StoryBadgeGrid(
            spacing=float(spacing),
            phase=float(phase),
            center_y=center_y,
            anchors=len(aligned),
        )

    def _story_badge_grid_detections(
        self,
        frame: np.ndarray,
        anchor_detections: tuple[StoryBadgeDetection, ...],
        target_numbers: Iterable[int],
    ) -> tuple[StoryBadgeDetection, ...]:
        """Compare every number template only inside fitted visible slots."""

        grid = self._story_badge_grid(frame, anchor_detections)
        if grid is None:
            return ()

        height, width = frame.shape[:2]
        geometry = getattr(self, "_last_story_badge_geometry", None)
        client_scale = (
            geometry.client_scale
            if geometry is not None
            else min(width / 1920, height / 1080)
        )
        peak_radius = max(2, round(5 * client_scale))
        local_tolerance = max(
            4.0,
            grid.spacing * STORY_BADGE_GRID_LOCAL_TOLERANCE_RATIO,
        )
        vertical_tolerance = max(
            3.0,
            height * STORY_BADGE_GRID_VERTICAL_TOLERANCE_RATIO,
        )

        def aligned_slot(result: MatchResult) -> int | None:
            center_x, center_y = result.center
            slot_index = round((center_x - grid.phase) / grid.spacing)
            predicted_x = grid.phase + slot_index * grid.spacing
            if abs(center_x - predicted_x) > local_tolerance:
                return None
            if abs(center_y - grid.center_y) > vertical_tolerance:
                return None
            return slot_index

        anchor_slots = {
            slot_index
            for detection in anchor_detections
            if (slot_index := aligned_slot(detection.best.result)) is not None
        }
        if not anchor_slots:
            return ()

        specs_by_number = dict(STORY_BADGE_SPECS)
        radius_x = max(8, round(min(grid.spacing * 0.38, width * 0.08)))
        radius_y = max(
            8,
            round(
                min(
                    max(grid.spacing * 0.30, height * 0.025),
                    height * 0.08,
                )
            ),
        )

        def local_candidates(
            number: int,
            slot_index: int,
        ) -> tuple[MatchResult, ...]:
            spec = specs_by_number[number]
            grid_spec = replace(
                spec,
                reference_scale=STORY_BADGE_GRID_REFERENCE_SCALE,
                candidate_center_roi=None,
            )
            center = (
                round(grid.phase + slot_index * grid.spacing),
                round(grid.center_y),
            )
            slot_matcher = getattr(self.vision, "match_slot_evidence", None)
            if callable(slot_matcher):
                try:
                    candidates = tuple(
                        slot_matcher(
                            frame,
                            grid_spec,
                            center,
                            radius=(radius_x, radius_y),
                            geometry=geometry,
                            minimum_score=0.20,
                            max_results=4,
                            purpose="剧情角标槽位编号",
                        )
                    )
                except (AttributeError, TypeError, ValueError):
                    candidates = ()
                if candidates:
                    return tuple(
                        getattr(candidate, "result", candidate)
                        for candidate in candidates
                    )

            matcher = getattr(self.vision, "match_all", None)
            if not callable(matcher):
                return ()
            try:
                candidates = tuple(
                    matcher(
                        frame,
                        grid_spec,
                        minimum_score=0.20,
                        peak_radius=peak_radius,
                        max_results=4,
                        search_roi=(
                            center[0] - radius_x,
                            center[1] - radius_y,
                            radius_x * 2 + 1,
                            radius_y * 2 + 1,
                        ),
                    )
                )
            except (AttributeError, TypeError, ValueError):
                return ()
            return tuple(
                result
                for result in candidates
                if abs(result.center[0] - center[0]) <= local_tolerance
                and abs(result.center[1] - center[1]) <= vertical_tolerance
            )

        candidates_by_slot: dict[int, list[StoryBadgeCandidate]] = {}
        requested_numbers = tuple(
            dict.fromkeys(int(value) for value in target_numbers if int(value) in specs_by_number)
        )
        # The target number is always included, while all numbers are examined
        # for the runner-up margin at that same physical slot.
        numbers_to_compare = tuple(specs_by_number)
        for slot_index in sorted(anchor_slots):
            for number in numbers_to_compare:
                for result in local_candidates(number, slot_index):
                    if aligned_slot(result) != slot_index:
                        continue
                    candidates_by_slot.setdefault(slot_index, []).append(
                        StoryBadgeCandidate(number, result)
                    )

        detections: list[tuple[float, StoryBadgeDetection]] = []
        for slot_index, candidates in candidates_by_slot.items():
            best_by_number: dict[int, StoryBadgeCandidate] = {}
            for candidate in candidates:
                current = best_by_number.get(candidate.number)
                if current is None or (
                    candidate.combined_score,
                    candidate.discrimination_score,
                ) > (
                    current.combined_score,
                    current.discrimination_score,
                ):
                    best_by_number[candidate.number] = candidate
            ranked = sorted(
                best_by_number.values(),
                key=lambda value: (
                    value.combined_score,
                    value.discrimination_score,
                ),
                reverse=True,
            )
            if not ranked:
                continue
            detections.append(
                (
                    grid.phase + slot_index * grid.spacing,
                    StoryBadgeDetection(
                        best=ranked[0],
                        runner_up=ranked[1] if len(ranked) > 1 else None,
                        recovery_mode="slot_grid",
                    ),
                )
            )

        self._status(
            "剧情角标栅格",
            (
                f"anchors={grid.anchors}, spacing={grid.spacing:.1f}, "
                f"slots={len(anchor_slots)}, requested={requested_numbers}, "
                f"detections={len(detections)}"
            ),
        )
        return tuple(value for _center, value in sorted(detections))

    @staticmethod
    def _story_badge_ocr_frame(
        frame: np.ndarray,
        result: MatchResult,
        *,
        binary: bool = True,
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
        # An even-sized crop puts the inner-circle mask on a half-pixel center,
        # which measurably breaks digit readability at tiny badge sizes (the
        # mask then clips asymmetrically before the upscale).  Trim to odd so
        # the mask stays centered on a real pixel; the trimmed edge is ring
        # pixels that the mask would suppress anyway.
        if crop_width > 1 and crop_width % 2 == 0:
            gray = gray[:, : crop_width - 1]
            crop_width -= 1
        if crop_height > 1 and crop_height % 2 == 0:
            gray = gray[: crop_height - 1, :]
            crop_height -= 1
        center_x = (crop_width - 1) / 2
        center_y = (crop_height - 1) / 2
        inner_radius = max(
            2.0,
            min(crop_width, crop_height) * STORY_BADGE_OCR_INNER_RADIUS_RATIO,
        )
        y, x = np.mgrid[:crop_height, :crop_width]
        gray[(x - center_x) ** 2 + (y - center_y) ** 2 > inner_radius**2] = 0
        if binary:
            _threshold, gray = cv2.threshold(
                gray,
                STORY_BADGE_OCR_BINARY_THRESHOLD,
                255,
                cv2.THRESH_BINARY,
            )

        target_height = STORY_BADGE_OCR_INNER_HEIGHT
        target_width = max(
            1,
            round(gray.shape[1] * target_height / max(1, gray.shape[0])),
        )
        enlarged = cv2.resize(
            gray,
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
        text = ""
        if prepared.size:
            text = self.vision.ocr_text(
                prepared,
                "剧情角标数字辅助",
                target_height=0,
                minimum_threshold=STORY_BADGE_OCR_MIN_CONFIDENCE,
            )
        number = self._story_badge_ocr_text_number(text)
        if number is None:
            # Hard binarization can disconnect anti-aliased digits on small
            # clients (measured: 1280x720 badge 8).  One raw-grayscale retry
            # keeps the single-digit rec model in its trained contrast range.
            prepared = self._story_badge_ocr_frame(frame, result, binary=False)
            if prepared.size:
                text = self.vision.ocr_text(
                    prepared,
                    "剧情角标数字辅助",
                    target_height=0,
                    minimum_threshold=STORY_BADGE_OCR_MIN_CONFIDENCE,
                )
                number = self._story_badge_ocr_text_number(text)
        self._status(
            "剧情角标 OCR",
            f"number={number if number is not None else '-'}, text={text or '-'}",
        )
        return number, text

    @staticmethod
    def _story_badge_ocr_text_number(text: str) -> int | None:
        numbers = {
            int(value)
            for value in re.findall(r"(?<!\d)\d{1,2}(?!\d)", str(text))
            if 1 <= int(value) <= 20
        }
        return next(iter(numbers)) if len(numbers) == 1 else None

    def _find_story_badge(
        self,
        frame: np.ndarray,
        target_number: int,
    ) -> tuple[StoryBadgeDetection | None, str]:
        detections = self._story_badge_detections(frame)
        detection, reason = self._find_story_badge_from_detections(
            frame,
            target_number,
            detections,
        )
        strict_reason = reason
        if detection is not None:
            return detection, reason
        grid_detections = self._story_badge_grid_detections(
            frame,
            detections,
            (target_number,),
        )
        if grid_detections:
            grid_detection, grid_reason = self._find_story_badge_from_detections(
                frame,
                target_number,
                grid_detections,
            )
            if grid_detection is not None:
                return grid_detection, grid_reason
            if self._story_badge_reason_is_ambiguous(grid_reason):
                return None, grid_reason
            if any(
                value.best.number == target_number for value in grid_detections
            ):
                return None, grid_reason
            reason = strict_reason
            runner_detections = self._story_badge_grid_runner_detections(
                target_number,
                grid_detections,
            )
            if runner_detections:
                runner_detection, runner_reason = (
                    self._find_story_badge_from_detections(
                        frame,
                        target_number,
                        runner_detections,
                    )
                )
                if runner_detection is not None:
                    return runner_detection, runner_reason
                return None, runner_reason
        if not grid_detections and getattr(
            self,
            "_last_story_badge_geometry_reason",
            "",
        ):
            return None, self._last_story_badge_geometry_reason
        if grid_detections and not any(
            value.best.number == target_number for value in grid_detections
        ):
            # A fitted row is useful evidence that the selector is present, but
            # it does not prove that the requested number is visible.
            return None, strict_reason
        if self._story_badge_reason_is_ambiguous(reason):
            return None, reason
        return None, reason

    @staticmethod
    def _story_badge_grid_runner_detections(
        target_number: int,
        grid_detections: tuple[StoryBadgeDetection, ...],
    ) -> tuple[StoryBadgeDetection, ...]:
        """Promote grid runner-up slots whose full structural evidence holds.

        On heavily degraded small clients the target's template can lose its
        own slot by a hair to a visually adjacent digit.  The slot evidence of
        that runner-up is still complete; the digit OCR becomes the deciding
        independent vote inside the regular selector.
        """

        promoted: list[StoryBadgeDetection] = []
        for value in grid_detections:
            runner = value.runner_up
            if runner is None or runner.number != target_number:
                continue
            result = runner.result
            if (
                result.score < STORY_BADGE_GRID_TEMPLATE_SCORE
                or result.pixel_score < STORY_BADGE_GRID_PIXEL_SCORE
                or result.zncc_score < STORY_BADGE_GRID_ZNCC_SCORE
            ):
                continue
            promoted.append(
                StoryBadgeDetection(
                    best=StoryBadgeCandidate(number=target_number, result=result),
                    runner_up=value.best,
                    recovery_mode="slot_grid_runner",
                )
            )
        return tuple(promoted)

    def inspect_story_badges(
        self,
        frame: np.ndarray,
        target_numbers: Iterable[int],
    ) -> dict[int, tuple[StoryBadgeDetection | None, str]]:
        """Strictly inspect several badge numbers with one shared template scan."""

        detections = self._story_badge_detections(frame)
        inspected = {
            int(number): self._find_story_badge_from_detections(
                frame,
                int(number),
                detections,
            )
            for number in dict.fromkeys(target_numbers)
        }
        recoverable = {
            number
            for number, (detection, reason) in inspected.items()
            if detection is None
            and not reason.startswith(("同一编号出现", "角标OCR数字冲突"))
        }
        if not recoverable:
            return inspected
        grid_detections = self._story_badge_grid_detections(
            frame,
            detections,
            recoverable,
        )
        if not grid_detections:
            return inspected
        for number in recoverable:
            inspected[number] = self._find_story_badge_from_detections(
                frame,
                number,
                grid_detections,
            )
        return inspected

    def _find_story_badge_from_detections(
        self,
        frame: np.ndarray,
        target_number: int,
        detections: tuple[StoryBadgeDetection, ...],
    ) -> tuple[StoryBadgeDetection | None, str]:
        def strict_identity(value: StoryBadgeDetection) -> bool:
            return (
                not value.recovery_mode
                and value.best.result.score >= STORY_BADGE_TEMPLATE_SCORE
                and value.best.result.pixel_score >= STORY_BADGE_PIXEL_SCORE
            )

        def encoded_identity(value: StoryBadgeDetection) -> bool:
            return (
                not value.recovery_mode
                and value.best.result.score >= STORY_BADGE_ENCODED_TEMPLATE_SCORE
                and value.best.result.pixel_score >= STORY_BADGE_ENCODED_PIXEL_SCORE
                and value.best.result.zncc_score >= STORY_BADGE_ENCODED_ZNCC_SCORE
            )

        def grid_identity(value: StoryBadgeDetection) -> bool:
            return (
                value.recovery_mode == "slot_grid"
                and value.best.result.score >= STORY_BADGE_GRID_TEMPLATE_SCORE
                and value.best.result.pixel_score >= STORY_BADGE_GRID_PIXEL_SCORE
                and value.best.result.zncc_score >= STORY_BADGE_GRID_ZNCC_SCORE
            )

        def runner_identity(value: StoryBadgeDetection) -> bool:
            # Structural floors were already enforced when the runner-up was
            # promoted; the digit OCR is the deciding vote for this tier.
            return value.recovery_mode == "slot_grid_runner"

        target_detections = [
            value
            for value in detections
            if value.best.number == target_number
            and (
                strict_identity(value)
                or encoded_identity(value)
                or grid_identity(value)
                or runner_identity(value)
            )
        ]
        if not target_detections:
            if any(value.recovery_mode == "slot_grid" for value in detections):
                return (
                    None,
                    (
                        "未达到角标栅格恢复门槛："
                        f"match>={STORY_BADGE_GRID_TEMPLATE_SCORE:.3f}, "
                        f"pixel>={STORY_BADGE_GRID_PIXEL_SCORE:.3f}, "
                        f"zncc>={STORY_BADGE_GRID_ZNCC_SCORE:.3f}, "
                        f"margin>={STORY_BADGE_GRID_MIN_MARGIN:.3f}, "
                        f"检测目标数={len(detections)}"
                    ),
                )
            return (
                None,
                (
                    "未达到角标严格或编码恢复门槛："
                    f"match>={STORY_BADGE_TEMPLATE_SCORE:.3f}, "
                    f"pixel>={STORY_BADGE_PIXEL_SCORE:.3f}, "
                    "或 "
                    f"match>={STORY_BADGE_ENCODED_TEMPLATE_SCORE:.3f}, "
                    f"pixel>={STORY_BADGE_ENCODED_PIXEL_SCORE:.3f}, "
                    f"zncc>={STORY_BADGE_ENCODED_ZNCC_SCORE:.3f}, "
                    f"margin>={STORY_BADGE_ENCODED_MIN_MARGIN:.3f}, "
                    f"检测目标数={len(detections)}"
                ),
            )
        if len(target_detections) > 1:
            # Several positions claim the target number (small clients can let
            # one number's template win on a neighbour's slot).  The digit OCR
            # is the independent discriminator: keep the selection only when
            # exactly one position reads back the target number.
            confirmed: list[StoryBadgeDetection] = []
            for value in target_detections:
                ocr_number, ocr_text = self._story_badge_ocr_number(
                    frame,
                    value.best.result,
                )
                if ocr_number == target_number:
                    confirmed.append(
                        replace(value, ocr_number=ocr_number, ocr_text=ocr_text)
                    )
            if len(confirmed) != 1:
                return None, f"同一编号出现{len(target_detections)}个有效位置"
            detection = confirmed[0]
        else:
            detection = target_detections[0]
        if detection.runner_up is None:
            return None, "缺少同位置次优编号，无法检查歧义"
        if runner_identity(detection):
            # The promoted runner-up lost its slot's template vote, so the
            # digit OCR is the deciding independent vote and is mandatory
            # regardless of how the symmetric ZNCC difference came out.
            if detection.ocr_number is not None:
                # Already digit-confirmed while resolving duplicate positions.
                ocr_number, ocr_text = detection.ocr_number, detection.ocr_text
            else:
                ocr_number, ocr_text = self._story_badge_ocr_number(
                    frame,
                    detection.best.result,
                )
                detection = replace(
                    detection,
                    ocr_text=ocr_text,
                    ocr_number=ocr_number,
                )
            if ocr_number == target_number:
                self._status(
                    "剧情角标",
                    (
                        f"栅格次优候选由OCR辅助确认：zncc={detection.margin:.3f}, "
                        f"number={ocr_number}"
                    ),
                )
                return detection, ""
            return (
                None,
                (
                    "角标次优候选OCR未确认："
                    f"模板={target_number}, OCR="
                    f"{ocr_number if ocr_number is not None else '-'}, "
                    f"text={ocr_text or '-'}"
                ),
            )
        required_margin = min(
            threshold
            for passed, threshold in (
                (strict_identity(detection), STORY_BADGE_MIN_MARGIN),
                (encoded_identity(detection), STORY_BADGE_ENCODED_MIN_MARGIN),
                (grid_identity(detection), STORY_BADGE_GRID_MIN_MARGIN),
            )
            if passed
        )
        if detection.margin < required_margin:
            if grid_identity(detection):
                # OCR is an auxiliary discriminator only after the grid's
                # structural gates and a non-trivial score margin pass.
                if (
                    detection.margin >= STORY_BADGE_GRID_OCR_MARGIN
                    and detection.combined_margin >= STORY_BADGE_GRID_MIN_COMBINED_MARGIN
                ):
                    if detection.ocr_number is not None:
                        # Already digit-confirmed during duplicate selection.
                        ocr_number, ocr_text = (
                            detection.ocr_number,
                            detection.ocr_text,
                        )
                    else:
                        ocr_number, ocr_text = self._story_badge_ocr_number(
                            frame,
                            detection.best.result,
                        )
                        detection = replace(
                            detection,
                            ocr_text=ocr_text,
                            ocr_number=ocr_number,
                        )
                    if ocr_number == target_number:
                        self._status(
                            "剧情角标",
                            (
                                f"栅格候选分差由OCR辅助确认：zncc={detection.margin:.3f}, "
                                f"combined={detection.combined_margin:.3f}, number={ocr_number}"
                            ),
                        )
                        return detection, ""
                    if ocr_number is not None:
                        return (
                            None,
                            (
                                "角标OCR数字冲突："
                                f"模板={target_number}, OCR={ocr_number}, "
                                f"text={ocr_text or '-'}"
                            ),
                        )
            return (
                None,
                (
                    f"候选分差不足（ZNCC）：{detection.margin:.3f}"
                    f"<{required_margin:.3f}；"
                    f"combined={detection.combined_margin:.3f}"
                ),
            )
        if detection.ocr_number is not None:
            # Digit already confirmed while resolving duplicate positions.
            ocr_number, ocr_text = detection.ocr_number, detection.ocr_text
        else:
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
        confirmed = self._confirm_story_badge_before_click(
            located.frame,
            located.badge,
        )
        if confirmed is None:
            return NavigationResult(
                False,
                ScreenState.CARD_MENU,
                f"剧情游戏卡{card.number}点击前角标复核失败，已停止点击",
            )
        badge_frame, badge = confirmed
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

    @staticmethod
    def _story_badge_identity_is_stable(
        previous: StoryBadgeDetection,
        current: StoryBadgeDetection,
    ) -> bool:
        """Require the same numbered slot before a card-selection click."""

        if previous.best.number != current.best.number:
            return False
        first = previous.best.result
        second = current.best.result
        if (
            first.size[0] <= 0
            or first.size[1] <= 0
            or second.size[0] <= 0
            or second.size[1] <= 0
        ):
            return False
        scale = max(0.2, min(float(first.scale), float(second.scale)))
        center_tolerance = max(
            3,
            round(8.0 * scale),
            round(max(first.size[0], second.size[0]) * 0.55),
        )
        size_tolerance = max(2, round(4.0 * scale))
        return (
            abs(first.center[0] - second.center[0]) <= center_tolerance
            and abs(first.center[1] - second.center[1]) <= center_tolerance
            and abs(first.size[0] - second.size[0]) <= size_tolerance
            and abs(first.size[1] - second.size[1]) <= size_tolerance
        )

    def _confirm_story_badge_before_click(
        self,
        frame: np.ndarray,
        detection: StoryBadgeDetection,
    ) -> tuple[np.ndarray, StoryBadgeDetection] | None:
        """Re-read the numbered slot immediately before sending a mouse click."""

        sleeper = getattr(self.task, "sleep", None)
        if callable(sleeper):
            sleeper(PROBE_STORY_BADGE_CONFIRM_SECONDS)
        capture = getattr(self.vision, "capture", None)
        if not callable(capture):
            reason = "视觉适配器不支持点击前复核捕获"
            self._status("剧情角标点击复核", reason)
            return None
        try:
            confirmed_frame = capture()
            confirmed, reason = self._find_story_badge(
                confirmed_frame,
                detection.best.number,
            )
        except (AttributeError, TypeError, ValueError, cv2.error) as exc:
            reason = f"复核异常：{exc}"
            confirmed = None
            confirmed_frame = None
        if confirmed is None:
            self._status("剧情角标点击复核", reason or "未重新确认目标编号")
            warning = getattr(self.task, "log_warning", None)
            if callable(warning):
                warning(
                    f"跑图跑商：剧情游戏卡{detection.best.number}点击前角标复核失败："
                    f"{reason or '未命中'}。"
                )
            return None
        if not self._story_badge_identity_is_stable(detection, confirmed):
            first = detection.best.result
            second = confirmed.best.result
            reason = (
                "复核编号或槽位不稳定："
                f"first={detection.best.number}@{first.center}/{first.size}; "
                f"second={confirmed.best.number}@{second.center}/{second.size}"
            )
            self._status("剧情角标点击复核", reason)
            warning = getattr(self.task, "log_warning", None)
            if callable(warning):
                warning(f"跑图跑商：剧情游戏卡点击前{reason}，已停止点击。")
            return None
        self._status(
            "剧情角标点击复核",
            (
                f"稳定确认{confirmed.best.number}；"
                f"center={confirmed.best.result.center}; "
                f"size={confirmed.best.result.size}; "
                f"m={confirmed.best.result.score:.3f},"
                f"p={confirmed.best.result.pixel_score:.3f},"
                f"z={confirmed.best.result.zncc_score:.3f}"
            ),
        )
        assert confirmed_frame is not None
        return confirmed_frame, confirmed

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
