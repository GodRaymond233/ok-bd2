from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from math import ceil
from statistics import median
from time import monotonic

import cv2
import numpy as np

from src.tasks.map_trade import event_card_art
from src.tasks.map_trade.card_status import (
    CardActionState,
    CollectionCardSelectionOutcome,
    CollectionCardSelectionResult,
    card_structure,
)
from src.tasks.map_trade.models import (
    ABSORB_ONLY_VERIFIED_CARD_IDS,
    CARD_BY_ID,
    SUPPRESS_ONLY_VERIFIED_CARD_IDS,
    MatchResult,
    NavigationResult,
    ScreenState,
    collection_action_name,
)
from src.tasks.map_trade.navigator_constants import (
    CHARACTER_BADGE_FIRST_X,
    CHARACTER_BADGE_MIN_MARGIN,
    CHARACTER_BADGE_MIN_SCORE,
    CHARACTER_BADGE_SPACING,
    CHARACTER_BADGE_SPECS,
    CHARACTER_BADGE_WINDOW,
    CHARACTER_BADGE_Y,
    CHARACTER_CATEGORY_HIGHLIGHT_REGION,
    CHARACTER_CATEGORY_POINT,
    EVENT_CARD_SCAN_SECONDS,
    EVENT_CATEGORY_HIGHLIGHT_REGION,
    EVENT_CATEGORY_POINT,
    FIRST_CARD_CONFIRM_REGION,
    FIRST_CARD_INSERT_REGION,
    FIRST_CARD_SKIP_TEMPLATE,
    PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
    PROBE_QUICK_SWITCH_SCROLL_COUNT,
    PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_POINT,
    PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    PROBE_STORY_BADGE_CONFIRM_SECONDS,
    QUICK_SWITCH_CARTRIDGE_REGION,
    QUICK_SWITCH_PAGE_KEYWORDS,
    QUICK_SWITCH_SCROLL_INTERVAL,
    QUICK_SWITCH_SCROLL_POINT,
    QUICK_SWITCH_SCROLL_RESET_AMOUNT,
    QUICK_SWITCH_SCROLL_RESET_COUNT,
    QUICK_SWITCH_SCROLL_SCAN_STEPS,
    QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    QUICK_SWITCH_SCROLL_UP_AMOUNT,
    QUICK_SWITCH_SCROLL_UP_COUNT,
    QUICK_SWITCH_TEMPLATE,
    STORY_BADGE_CANDIDATE_SCORE,
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
    LocatedStoryCard,
    ProbedStoryCard,
    StoryBadgeCandidate,
    StoryBadgeDetection,
    StoryBadgeGrid,
)
from src.tasks.map_trade.vision import normalize_text
from src.utils.calibration import FHD_1080
from src.utils.image_utils import stabilize_template_match

# 从箱庭点击快速切换按钮的统一识别窗口与点击后停顿。
QUICK_SWITCH_CLICK_TIMEOUT = 10.0
QUICK_SWITCH_CLICK_AFTER_SLEEP = 1.0
STORY_BAR_SCROLL_MAX_EVENTS = 64
CARTRIDGE_SCROLL_OBSERVATIONS = 12
CARTRIDGE_SCROLL_STABLE_DIFFERENCE = 1.5
CARTRIDGE_SCROLL_MOVEMENT_DIFFERENCE = 2.0
CARTRIDGE_SCROLL_POLL_SECONDS = 0.15


class StoryCardNavigationMixin:
    @staticmethod
    def _completion_satisfies_actions(completion, required_actions=None, card_id=None) -> bool:
        """Return whether card-menu evidence covers the enabled actions."""

        enabled = {"吸收", "压制"} if required_actions is None else {
            collection_action_name(action)
            for action in required_actions
        }
        if "吸收" not in enabled:
            enabled.add("吸收")
        if required_actions is None and card_id in SUPPRESS_ONLY_VERIFIED_CARD_IDS:
            enabled.discard("吸收")
        if required_actions is None and card_id in ABSORB_ONLY_VERIFIED_CARD_IDS:
            enabled.discard("压制")
        checks = {"吸收": completion.absorb, "压制": completion.suppress}
        # The card menu has no summon badge.  An action requested by the
        # caller but absent from the visual evidence must remain unknown;
        # silently filtering it out can mark a summon-disabled run as a full
        # production completion.
        if any(action not in checks for action in enabled):
            return False
        return bool(completion.complete_region) and all(
            checks[action].state == CardActionState.COMPLETED
            for action in enabled
        )

    def _collection_observation(self, located: LocatedStoryCard) -> ProbedStoryCard | None:
        """Bind resources to the identified card and its complete structure in one frame."""
        if not located.structure.complete_region:
            self._status("卡带完成度复核", "卡带结构被客户区边缘截断")
            return None
        try:
            completion = self.card_status.detect(located.frame, located.badge.best.result.center)
        except (RuntimeError, ValueError, cv2.error) as exc:
            self._status("卡带完成度复核", f"资源状态识别异常：{exc}")
            return None
        if not completion.complete_region:
            self._status("卡带完成度复核", "资源状态条不完整")
            return None
        return ProbedStoryCard(located, completion)

    def _confirm_collection_completion(self, observed: ProbedStoryCard) -> ProbedStoryCard | None:
        located = observed.located
        confirmed = self._confirm_story_badge_before_click(located.frame, located.badge)
        if confirmed is None:
            return None
        frame, badge = confirmed
        return self._collection_observation(LocatedStoryCard(located.card, frame, badge))

    def _wait_for_quick_switch_page(self, timeout: float = 10.0) -> bool:
        return self._wait_for_ocr_keywords(
            QUICK_SWITCH_PAGE_KEYWORDS,
            timeout,
            "卡带选择页",
        )

    def _find_quick_switch_button(self, frame: np.ndarray) -> MatchResult | None:
        """Identify the translucent navigation icon independently of its brightness."""

        candidates = []
        for roi in QUICK_SWITCH_TEMPLATE.relative_rois:
            candidates.extend(self.vision.match_evidence_all(
                frame,
                replace(QUICK_SWITCH_TEMPLATE, relative_rois=(), relative_roi=roi),
                minimum_score=0.88,
                peak_radius=max(8, round(22 * frame.shape[0] / FHD_1080.height)),
                max_results=8,
                purpose="快速切换按钮",
            ))
        if not candidates:
            self._status("快速切换按钮", "搜索区域内无可信候选")
            return None
        candidates.sort(key=lambda candidate: candidate.result.zncc_score, reverse=True)
        best = candidates[0].result
        runner = candidates[1].result.zncc_score if len(candidates) > 1 else -1.0
        # The button is translucent: its background and absolute gray values
        # change between fields. Identity comes from shape, not pixel brightness.
        accepted = (
            best.score >= 0.92
            and best.zncc_score >= 0.78
            and best.gradient_zncc_score >= 0.50
            and best.edge_score >= 0.60
            and best.zncc_score - runner >= 0.10
        )
        self._status(
            "快速切换按钮",
            f"{'通过' if accepted else '拒绝'}; center={best.center}; "
            f"m={best.score:.3f}, pixel={best.pixel_score:.3f}(仅诊断), "
            f"z={best.zncc_score:.3f}, gradient={best.gradient_zncc_score:.3f}, "
            f"edge={best.edge_score:.3f}, margin={best.zncc_score - runner:.3f}",
        )
        return best if accepted else None

    def _click_quick_switch(self, timeout: float, after_sleep: float) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.vision.capture()
            result = self._find_quick_switch_button(frame)
            if result is None:
                if not self._black_frame(frame) and self._field_battle_visible(frame):
                    return False
                self.task.sleep(0.35)
                continue

            def sample_match():
                sampled = self.vision.capture()
                match = (
                    self._find_quick_switch_button(sampled)
                    if sampled.shape == frame.shape else None
                )
                return match, sampled.shape

            stable = stabilize_template_match(
                result, frame.shape, sample_match=sample_match,
                passes=lambda match: match is not None, sleep=self.task.sleep,
                window_samples=2, maximum_samples=3,
                minimum_hits=2, required_trailing_hits=2,
            )
            if stable is None:
                self._status("快速切换按钮稳定识别", "候选位置或画面尺寸不稳定")
                return False
            consensus, shape = stable
            self._status(
                "快速切换按钮稳定识别",
                f"center={consensus.center}; hits={consensus.hit_count}/"
                f"{consensus.sample_count}; spread={consensus.center_spread:.1f}",
            )
            self.vision.click_client(consensus.center, shape, after_sleep=after_sleep)
            return True
        return False

    def _recent_field_ready(self) -> bool:
        frame = self.vision.capture()
        if self._black_frame(frame):
            return False
        if self._field_battle_visible(frame):
            self._retreat_from_field_battle(frame, stop_walk=True)
            return True
        return self.classify(frame) == ScreenState.SANDBOX

    def _open_cartridge_quick_switcher(self, category: str = "story") -> NavigationResult:
        opened = self.task.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self._click_quick_switch(
                # 入口点击可能落在主页「返回战场」上（存在挂起会话时），
                # 直接触发整图加载；快速切换按钮要在加载完成后的卡带
                # 页面上才出现，搜索窗口按加载超时给足。
                timeout=self._loading_timeout(),
                after_sleep=QUICK_SWITCH_CLICK_AFTER_SLEEP,
            ),
            confirm_quick_switch_page=self._wait_for_quick_switch_page,
            entry_ready=self._recent_field_ready,
        )
        if not opened:
            return NavigationResult(
                False,
                self.classify(),
                "无法从主页打开快速切换卡带页面",
            )

        if not self._select_cartridge_category(category):
            return NavigationResult(
                False,
                self.classify(),
                "点击后未确认剧情游戏卡类别高亮",
            )
        return NavigationResult(True, ScreenState.CARD_MENU, "剧情游戏卡类别已确认")

    def _wait_for_cartridge_category(
        self,
        timeout: float = 3.0,
        interval: float = 0.5,
        *,
        category: str = "story",
        warn: bool = True,
    ) -> bool:
        label, _point, _region = self._card_category(category)
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.vision.capture()
            if self._cartridge_category_confirmed(frame, category):
                self._menu_confirmed = (monotonic(), category)
                return True
            self.task.sleep(interval)
        if warn:
            self.task.log_warning(
                f"跑商：未确认{label}类别文字与高亮。"
            )
        return False

    def _cartridge_category_confirmed(self, frame, category: str) -> bool:
        label, _point, region = self._card_category(category)
        text = self.vision.simplify(self.vision.ocr_text(frame, f"{label}类别"))
        ratio = self.vision.bright_neutral_ratio(frame, region)
        self._status(f"{label}类别高亮", f"{ratio:.3f}")
        return (
            normalize_text(label) in normalize_text(text)
            and ratio >= STORY_CATEGORY_HIGHLIGHT_MIN_RATIO
        )

    def _select_cartridge_category(self, category: str = "story") -> bool:
        """Click and confirm the category before any quick-bar focus or scroll."""

        label, point, _region = self._card_category(category)
        self._status("导航状态", f"选择{label}")
        self.task.operate_click(*point, after_sleep=0.5)
        return self._wait_for_cartridge_category(category=category)

    def _scroll_cartridge_bar(
        self, point, amount, *, count, interval, after_sleep, require_movement=True,
    ) -> bool:
        """Scroll without selecting a card, then confirm a stable selector."""

        def sample(frame):
            height = frame.shape[0]
            # Restrict comparison to the badges and card faces, away from the
            # animated sandbox and completion icons below the cards.
            band = frame[round(height * 0.855):round(height * 0.94), :, :3]
            return cv2.resize(band, (480, 48)).astype(np.int16)

        before = self.vision.capture()
        baseline = sample(before)
        # scroll_client activates the window and positions the cursor itself.
        # Clicking this point can select the leftmost visible cartridge.
        self.task.scroll_client(
            point, amount, count=count, interval=interval, after_sleep=after_sleep,
        )
        previous = None
        stable_hits = 0
        for _ in range(CARTRIDGE_SCROLL_OBSERVATIONS):
            frame = self.vision.capture()
            if frame.shape != before.shape:
                self._status("卡带滚轮", "窗口尺寸变化，停止本次定位")
                return False
            current = sample(frame)
            difference = (
                float(np.mean(np.abs(current - previous)))
                if previous is not None else float("inf")
            )
            stable_hits = stable_hits + 1 if difference < CARTRIDGE_SCROLL_STABLE_DIFFERENCE else 0
            if stable_hits >= 2:
                if not self._wait_for_quick_switch_page(timeout=1.0):
                    self._status("卡带滚轮", "滚动后卡带选择页已消失，停止本次定位")
                    return False
                has_moved = (
                    float(np.mean(np.abs(current - baseline)))
                    >= CARTRIDGE_SCROLL_MOVEMENT_DIFFERENCE
                )
                self._status(
                    "卡带滚轮", "列表已稳定且发生位移" if has_moved else "列表已稳定但未位移",
                )
                return has_moved or not require_movement
            previous = current
            self.task.sleep(CARTRIDGE_SCROLL_POLL_SECONDS)
        self._status("卡带滚轮", "列表稳定确认超时，停止本次定位")
        return False

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
            else min(width / FHD_1080.width, height / FHD_1080.height)
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
            detections.append(self._rank_badge_candidates(cluster))
        return tuple(sorted(detections, key=lambda value: value.best.result.center[0]))

    @staticmethod
    def _rank_badge_candidates(candidates, *, recovery_mode="") -> StoryBadgeDetection:
        """Choose distinct-number winner and runner-up from one occupied physical slot."""
        def rank(candidate):
            return candidate.combined_score, candidate.discrimination_score

        best_by_number: dict[int, StoryBadgeCandidate] = {}
        for candidate in candidates:
            current = best_by_number.get(candidate.number)
            if current is None or rank(candidate) > rank(current):
                best_by_number[candidate.number] = candidate
        ranked = sorted(best_by_number.values(), key=rank, reverse=True)
        return StoryBadgeDetection(
            best=ranked[0],
            runner_up=ranked[1] if len(ranked) > 1 else None,
            recovery_mode=recovery_mode,
        )

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
        row = self._dominant_badge_row(detections, badge_width)
        if len(row) < STORY_BADGE_GRID_MIN_ANCHORS:
            return None
        x_values = sorted(float(value.best.result.center[0]) for value in row)
        y_values = [float(value.best.result.center[1]) for value in row]
        prior = width * STORY_BADGE_GRID_SPACING_RATIO
        spacing_candidates = self._badge_spacing_candidates(x_values, width, badge_width)
        trials = []
        for spacing in spacing_candidates:
            seeds = tuple(value % spacing for value in x_values)
            for seed in seeds:
                phase, aligned, tolerance = self._fit_badge_grid_phase(
                    row, spacing, seed, badge_width,
                )
                if not aligned:
                    continue
                expected_visible_slots = max(1, ceil(width / spacing))
                required_anchors = max(
                    STORY_BADGE_GRID_MIN_ANCHORS,
                    ceil(expected_visible_slots * STORY_BADGE_GRID_MIN_VISIBLE_FRACTION),
                )
                residual = sum(
                    self._badge_phase_error(float(value.best.result.center[0]), phase, spacing)
                    for value in aligned.values()
                )
                quality = sum(max(0.0, value.best.combined_score) for value in aligned.values())
                trials.append((
                    len(aligned), len(aligned) / expected_visible_slots, quality,
                    -residual, -abs(spacing - prior), spacing, phase, aligned,
                    tolerance, required_anchors,
                ))
        if not trials:
            return None
        (_count, _fraction, _quality, _residual, _prior_distance,
         spacing, phase, aligned, _tolerance, required) = max(trials, key=lambda value: value[:5])
        if len(aligned) < required:
            return None
        center_y = float(median(y_values))
        vertical_tolerance = max(
            3.0, height * STORY_BADGE_GRID_VERTICAL_TOLERANCE_RATIO, badge_width * 0.85,
        )
        aligned = {
            slot_index: value for slot_index, value in aligned.items()
            if abs(value.best.result.center[1] - center_y) <= vertical_tolerance
        }
        if len(aligned) < required:
            return None
        return StoryBadgeGrid(
            spacing=float(spacing), phase=float(phase),
            center_y=float(median(value.best.result.center[1] for value in aligned.values())),
            anchors=len(aligned),
        )

    @staticmethod
    def _dominant_badge_row(detections, badge_width):
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

        return max(rows, key=row_rank)

    @staticmethod
    def _badge_spacing_candidates(x_values, width, badge_width) -> set[float]:
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
        return spacing_candidates

    @staticmethod
    def _badge_phase_error(value: float, phase: float, spacing: float) -> float:
        remainder = (value - phase) % spacing
        return min(remainder, spacing - remainder)

    def _fit_badge_grid_phase(self, row, spacing, seed, badge_width):
        tolerance = max(
            2.0,
            min(spacing * max(0.02, STORY_BADGE_GRID_ALIGNMENT_TOLERANCE_RATIO),
                max(3.0, badge_width * 0.85)),
        )
        phase = seed % spacing
        aligned: dict[int, StoryBadgeDetection] = {}
        for _ in range(3):
            aligned = {}
            for detection in row:
                center_x = float(detection.best.result.center[0])
                slot_index = round((center_x - phase) / spacing)
                if self._badge_phase_error(center_x, phase, spacing) > tolerance:
                    continue
                current = aligned.get(slot_index)
                if current is None or (
                    detection.best.combined_score, detection.best.discrimination_score,
                ) > (
                    current.best.combined_score, current.best.discrimination_score,
                ):
                    aligned[slot_index] = detection
            if not aligned:
                break
            signed_errors = tuple(
                ((float(value.best.result.center[0])
                  - (phase + slot_index * spacing) + spacing / 2) % spacing) - spacing / 2
                for slot_index, value in aligned.items()
            )
            phase = (phase + median(signed_errors)) % spacing
        return phase, aligned, tolerance

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
            else min(width / FHD_1080.width, height / FHD_1080.height)
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
            detections.append(
                (
                    grid.phase + slot_index * grid.spacing,
                    self._rank_badge_candidates(candidates, recovery_mode="slot_grid"),
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
        if getattr(self, "_badge_category", "story") == "character":
            return self._find_character_badge(frame, target_number)
        if getattr(self, "_badge_category", "story") == "event":
            check = event_card_art.find_card(frame, target_number)
            self._status(
                "活动卡带封面", check.reason or f"{target_number}: margin={check.margin:.3f}",
            )
            if check.result is None:
                return None, check.reason
            best = StoryBadgeCandidate(target_number, check.result)
            runner = StoryBadgeCandidate(
                check.competitor,
                replace(
                    check.result, score=check.result.score - check.margin,
                    zncc_score=check.result.score - check.margin,
                ),
            )
            return StoryBadgeDetection(best, runner, recovery_mode="event_cover"), ""
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
        *,
        priority_number: int | None = None,
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
        self._recover_ordered_story_badges(frame, inspected, detections)
        recoverable = {
            number
            for number, (detection, reason) in inspected.items()
            if detection is None
            and not reason.startswith(("同一编号出现", "角标OCR数字冲突"))
        }
        viewport = self._story_bar_viewport(frame, inspected)
        if priority_number is not None and viewport is not None:
            recoverable.intersection_update((priority_number,))
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
            detection, reason = inspected[number]
            if (
                detection is None
                and not self._story_badge_reason_is_ambiguous(reason)
                and not any(value.best.number == number for value in grid_detections)
            ):
                runners = self._story_badge_grid_runner_detections(number, grid_detections)
                if runners:
                    inspected[number] = self._find_story_badge_from_detections(
                        frame, number, runners,
                    )
        self._recover_ordered_story_badges(frame, inspected, (*detections, *grid_detections))
        return inspected

    def _recover_ordered_story_badges(self, frame, inspected, detections):
        """Use the ordered row to choose an OCR region, never to infer its digit."""

        viewport = self._story_bar_viewport(frame, inspected)
        if viewport is None:
            return
        origin, spacing = viewport
        anchors = {
            number: badge.best.result.center
            for number, (badge, _reason) in inspected.items() if badge is not None
        }
        row_y = median(center[1] for center in anchors.values())
        for number, (badge, reason) in tuple(inspected.items()):
            if (
                badge is not None
                or not min(anchors) - 1 <= number <= max(anchors) + 1
                or reason.startswith(("同一编号出现", "角标OCR数字冲突"))
            ):
                continue
            predicted_x = origin + (number - 1) * spacing
            candidates = []
            for detection in detections:
                for candidate, runner in (
                    (detection.best, detection.runner_up),
                    (detection.runner_up, detection.best),
                ):
                    if candidate is None or runner is None or candidate.number != number:
                        continue
                    result = candidate.result
                    if (
                        result.score >= STORY_BADGE_GRID_TEMPLATE_SCORE
                        and result.pixel_score >= STORY_BADGE_GRID_PIXEL_SCORE
                        and result.zncc_score >= STORY_BADGE_GRID_ZNCC_SCORE
                        and abs(result.center[0] - predicted_x)
                        <= spacing * STORY_BADGE_GRID_LOCAL_TOLERANCE_RATIO
                        and abs(result.center[1] - row_y)
                        <= frame.shape[0] * STORY_BADGE_GRID_VERTICAL_TOLERANCE_RATIO
                    ):
                        candidates.append((candidate, runner))
            if not candidates:
                continue
            tried_regions = set()
            for best, runner in sorted(candidates, key=lambda pair: -pair[0].combined_score):
                region = best.result.position, best.result.size
                if region in tried_regions:
                    continue
                tried_regions.add(region)
                ocr_number, text = self._story_badge_ocr_number(frame, best.result)
                if ocr_number == number:
                    inspected[number] = (StoryBadgeDetection(
                        best, runner, ocr_number=ocr_number, ocr_text=text,
                        recovery_mode="ordered_slot",
                    ), "")
                    self._status("剧情角标", f"顺序槽位与OCR共同确认{number}")
                    break
                prefix = "角标OCR数字冲突" if ocr_number is not None else "顺序槽位OCR未确认"
                inspected[number] = (None, f"{prefix}：目标={number}, OCR={text or '-'}")
                if ocr_number is not None:
                    break

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
            and (
                self._badge_identity_margins(value)
                or value.recovery_mode == "slot_grid_runner"
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
        return self._resolve_badge_identity(frame, target_number, detection)

    def _resolve_badge_identity(
        self, frame: np.ndarray, target_number: int, detection: StoryBadgeDetection,
    ) -> tuple[StoryBadgeDetection | None, str]:
        """Resolve one physical slot using its runner-up margin and independent digit evidence."""
        if detection.runner_up is None:
            return None, "缺少同位置次优编号，无法检查歧义"
        if detection.recovery_mode == "slot_grid_runner":
            # The promoted runner-up lost its slot's template vote, so the
            # digit OCR is the deciding independent vote and is mandatory
            # regardless of how the symmetric ZNCC difference came out.
            detection = self._read_badge_ocr(frame, detection)
            ocr_number, ocr_text = detection.ocr_number, detection.ocr_text
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
        required_margin = min(self._badge_identity_margins(detection))
        if detection.margin < required_margin:
            if detection.recovery_mode == "slot_grid":
                # OCR is an auxiliary discriminator only after the grid's
                # structural gates and a non-trivial score margin pass.
                if (
                    detection.margin >= STORY_BADGE_GRID_OCR_MARGIN
                    and detection.combined_margin >= STORY_BADGE_GRID_MIN_COMBINED_MARGIN
                ):
                    detection = self._read_badge_ocr(frame, detection)
                    ocr_number, ocr_text = detection.ocr_number, detection.ocr_text
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
        detection = self._read_badge_ocr(frame, detection)
        ocr_number, ocr_text = detection.ocr_number, detection.ocr_text
        if ocr_number is not None and ocr_number != target_number:
            return (
                None,
                (
                    "角标OCR数字冲突："
                    f"模板={target_number}, OCR={ocr_number}, text={ocr_text or '-'}"
                ),
            )
        return detection, ""

    @staticmethod
    def _badge_identity_margins(detection: StoryBadgeDetection) -> tuple[float, ...]:
        """Return the ambiguity margins for every structural tier the badge passes."""
        result = detection.best.result
        tiers = (
            (not detection.recovery_mode
             and result.score >= STORY_BADGE_TEMPLATE_SCORE
             and result.pixel_score >= STORY_BADGE_PIXEL_SCORE, STORY_BADGE_MIN_MARGIN),
            (not detection.recovery_mode
             and result.score >= STORY_BADGE_ENCODED_TEMPLATE_SCORE
             and result.pixel_score >= STORY_BADGE_ENCODED_PIXEL_SCORE
             and result.zncc_score >= STORY_BADGE_ENCODED_ZNCC_SCORE,
             STORY_BADGE_ENCODED_MIN_MARGIN),
            (detection.recovery_mode == "slot_grid"
             and result.score >= STORY_BADGE_GRID_TEMPLATE_SCORE
             and result.pixel_score >= STORY_BADGE_GRID_PIXEL_SCORE
             and result.zncc_score >= STORY_BADGE_GRID_ZNCC_SCORE, STORY_BADGE_GRID_MIN_MARGIN),
        )
        return tuple(margin for passed, margin in tiers if passed)

    def _read_badge_ocr(
        self, frame: np.ndarray, detection: StoryBadgeDetection,
    ) -> StoryBadgeDetection:
        if detection.ocr_number is not None:
            return detection
        number, text = self._story_badge_ocr_number(frame, detection.best.result)
        return replace(detection, ocr_number=number, ocr_text=text)

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
                "顺序槽位OCR未确认",
            )
        )

    def _wait_for_story_badge_with_scroll(
        self,
        target_number: int,
        scan_steps: int | None = None,
    ) -> tuple[np.ndarray, StoryBadgeDetection] | None:
        """Locate a confirmed card in its category's quick-switch row."""

        self._event_card_scan_absent = False
        if getattr(self, "_badge_category", "story") == "story":
            return self._locate_story_badge_in_viewport(
                target_number,
                STORY_BAR_SCROLL_MAX_EVENTS if scan_steps is None else scan_steps,
            )
        if scan_steps is None:
            scan_steps = QUICK_SWITCH_SCROLL_SCAN_STEPS

        last_reason = "未执行识别"

        def scan_current_page() -> tuple[np.ndarray, StoryBadgeDetection] | None:
            nonlocal last_reason
            frame = self.vision.capture()
            detection, last_reason = self._find_story_badge(frame, target_number)
            if detection is None:
                self._status("剧情角标", f"{target_number}: {last_reason}")
                return None
            if not self._story_card_target_visible(frame, detection):
                last_reason = "角标已命中，但卡带框架被客户区边缘截断"
                self._status("卡带完成度", last_reason)
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

        if getattr(self, "_badge_category", "story") == "event":
            # 只有扫描窗口内每一帧都是有效画面里的封面低分，才可能表示未持有。
            self._event_card_scan_absent = True
            end_at = monotonic() + EVENT_CARD_SCAN_SECONDS
            while True:
                found = scan_current_page()
                if found is None and not event_card_art.is_absent_reason(last_reason):
                    self._event_card_scan_absent = False
                if found is not None or monotonic() >= end_at:
                    return found
                self.task.sleep(PROBE_STORY_BADGE_CONFIRM_SECONDS)

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
        if not self._scroll_cartridge_bar(
            QUICK_SWITCH_SCROLL_POINT,
            QUICK_SWITCH_SCROLL_RESET_AMOUNT,
            count=QUICK_SWITCH_SCROLL_RESET_COUNT,
            interval=QUICK_SWITCH_SCROLL_INTERVAL,
            after_sleep=QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
            require_movement=False,
        ):
            return None

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
            if not self._scroll_cartridge_bar(
                QUICK_SWITCH_SCROLL_POINT,
                QUICK_SWITCH_SCROLL_UP_AMOUNT,
                count=QUICK_SWITCH_SCROLL_UP_COUNT,
                interval=QUICK_SWITCH_SCROLL_INTERVAL,
                after_sleep=QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
            ):
                return None

        self.task.log_warning(
            f"跑图跑商：滚动快速选择栏后仍未确认剧情游戏卡{target_number}角标：{last_reason}。"
        )
        return None

    def _story_card_structure(self, frame, number, center):
        category = getattr(self, "_badge_category", "story")
        return card_structure(
            center, frame.shape, short_resource_row=category == "story" and number == 20,
        )

    def _story_card_target_visible(self, frame, detection) -> bool:
        height, width = frame.shape[:2]
        result = detection.best.result
        return (
            result.position[0] >= 0 and result.position[1] >= 0
            and result.position[0] + result.size[0] <= width
            and result.position[1] + result.size[1] <= height
            and self._story_card_structure(
                frame, detection.best.number, result.center,
            ).complete_region
        )

    @staticmethod
    def _story_bar_viewport(frame, inspected) -> tuple[float, float] | None:
        """Fit the ordered chapter row using independently identified badges."""

        anchors = sorted(
            (number, detection.best.result.center)
            for number, (detection, _reason) in inspected.items()
            if detection is not None
        )
        if len(anchors) < 3:
            return None
        spacings = [
            (right[1][0] - left[1][0]) / (right[0] - left[0])
            for left, right in zip(anchors, anchors[1:])
        ]
        spacing = median(spacings)
        expected = frame.shape[1] * STORY_BADGE_GRID_SPACING_RATIO
        tolerance = expected * STORY_BADGE_GRID_LOCAL_TOLERANCE_RATIO
        if abs(spacing - expected) > tolerance:
            return None
        origin = median(x - (number - 1) * spacing for number, (x, _y) in anchors)
        row_y = median(y for _number, (_x, y) in anchors)
        if any(
            abs(x - (origin + (number - 1) * spacing)) > tolerance
            or abs(y - row_y) > frame.shape[0] * STORY_BADGE_GRID_VERTICAL_TOLERANCE_RATIO
            for number, (x, y) in anchors
        ):
            return None
        return origin, spacing

    def _locate_story_badge_in_viewport(
        self, target_number: int, scroll_limit: int,
    ) -> tuple[np.ndarray, StoryBadgeDetection] | None:
        """Observe the window after each move; predictions never authorize a click."""

        remaining = max(0, int(scroll_limit))
        previous = None
        pixels_per_tick = None
        reversed_at_edge = False
        initial_shape = None
        reason = "未执行识别"
        while True:
            frame = self.vision.capture()
            if initial_shape is not None and frame.shape != initial_shape:
                reason = "窗口尺寸变化"
                break
            initial_shape = frame.shape
            inspected = self.inspect_story_badges(
                frame, range(1, 21), priority_number=target_number,
            )
            detection, reason = inspected.get(target_number, (None, "目标编号无效"))
            if detection is not None:
                if self._story_card_target_visible(frame, detection):
                    self._status("剧情角标", f"{target_number}: 当前窗口已完整识别")
                    return frame, detection
            elif self._story_badge_reason_is_ambiguous(reason):
                reason = f"目标角标存在歧义：{reason}"
                break

            viewport = self._story_bar_viewport(frame, inspected)
            if viewport is None:
                return self._scan_unordered_story_bar(
                    frame, inspected, target_number, remaining,
                )
            origin, spacing = viewport
            target_x = (
                detection.best.result.center[0] if detection is not None
                else origin + (target_number - 1) * spacing
            )
            bounds = self._story_card_structure(frame, target_number, (0, 0)).bounds
            left, right = -bounds[0], frame.shape[1] - bounds[2]
            if left <= target_x <= right:
                if detection is None:
                    return self._scan_unordered_story_bar(
                        frame, inspected, target_number, remaining,
                    )
                reason = f"目标预计已在当前窗口，但未确认完整卡带：{reason}"
                break

            # Keep the entire observed card inside the same viewport used by the gate.
            destination = left + spacing / 2 if target_x < left else right - spacing / 2
            distance = destination - target_x
            amount = 1 if distance < 0 else -1
            count = 1
            centers = {
                number: badge.best.result.center[0]
                for number, (badge, _reason) in inspected.items()
                if badge is not None
            }
            if previous is not None:
                old_origin, old_spacing, old_centers, last_amount, last_count = previous
                if (
                    abs(spacing - old_spacing)
                    > old_spacing * STORY_BADGE_GRID_LOCAL_TOLERANCE_RATIO
                ):
                    reason = "滚动前后卡槽间距不一致"
                    break
                common = centers.keys() & old_centers.keys()
                movement = (
                    median(centers[number] - old_centers[number] for number in common)
                    if len(common) >= 2 else origin - old_origin
                )
                if abs(movement) < spacing * 0.05:
                    if pixels_per_tick is not None:
                        return self._scan_unordered_story_bar(
                            frame, inspected, target_number, remaining, amount=-last_amount,
                        )
                    if reversed_at_edge:
                        reason = "卡带窗口未继续移动"
                        break
                    # The first trial can point out of an endpoint. Try the
                    # opposite direction once, then require measured movement.
                    reversed_at_edge = True
                    amount = -last_amount
                else:
                    pixels_per_tick = movement / (last_amount * last_count)

            if pixels_per_tick is not None:
                amount = 1 if distance * pixels_per_tick > 0 else -1
                count = min(8, max(1, ceil(abs(distance / pixels_per_tick))))
            count = min(count, remaining)
            if count <= 0:
                reason = "已用尽卡带滚动次数"
                break
            visible = sorted(centers)
            self._status(
                "卡带窗口定位",
                f"可见{visible}；目标{target_number}预计x={target_x:.0f}；"
                f"滚轮{amount:+d}×{count}；剩余{remaining - count}",
            )
            if not self._scroll_cartridge_bar(
                QUICK_SWITCH_SCROLL_POINT, amount, count=count,
                interval=QUICK_SWITCH_SCROLL_INTERVAL,
                after_sleep=QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
                require_movement=False,
            ):
                reason = "滚动后未确认稳定的卡带选择页"
                break
            remaining -= count
            previous = origin, spacing, centers, amount, count

        self.task.log_warning(f"跑图跑商：剧情游戏卡{target_number}窗口定位停止：{reason}。")
        return None

    def _scan_unordered_story_bar(
        self, frame, inspected, target_number, remaining, *, amount=1,
    ):
        """Search overlapping views when chapter order cannot locate the target."""

        reversed_at_edge = False
        batch_count = 2
        reason = "已用尽卡带滚动次数"
        while remaining > 0:
            centers = {
                number: badge.best.result.center[0]
                for number, (badge, _reason) in inspected.items()
                if badge is not None
            }
            if len(centers) < 3:
                reason = "可信章节不足，无法确定卡带窗口"
                break
            count = min(batch_count, remaining)
            self._status("卡带窗口定位", f"编号排列不连续，局部扫描{amount:+d}×{count}")
            if not self._scroll_cartridge_bar(
                QUICK_SWITCH_SCROLL_POINT, amount, count=count,
                interval=QUICK_SWITCH_SCROLL_INTERVAL,
                after_sleep=QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
                require_movement=False,
            ):
                reason = "滚动后未确认稳定的卡带选择页"
                break
            remaining -= count
            next_frame = self.vision.capture()
            if next_frame.shape != frame.shape:
                reason = "窗口尺寸变化"
                break
            inspected = self.inspect_story_badges(
                next_frame, range(1, 21), priority_number=target_number,
            )
            detection, reason = inspected.get(target_number, (None, "目标未识别"))
            if detection is not None:
                if self._story_card_target_visible(next_frame, detection):
                    return next_frame, detection
                reason = "目标已识别，但卡面仍被窗口边缘截断"
            elif self._story_badge_reason_is_ambiguous(reason):
                break
            shifts = [
                badge.best.result.center[0] - centers[number]
                for number, (badge, _reason) in inspected.items()
                if badge is not None and number in centers
            ]
            if len(shifts) < 2:
                reason = "滚动前后缺少共同章节，无法确认位移"
                break
            movement = abs(median(shifts))
            spacing = frame.shape[1] * STORY_BADGE_GRID_SPACING_RATIO
            if movement < spacing * 0.05:
                if reversed_at_edge:
                    reason = "两个方向均已到达边界"
                    break
                reversed_at_edge = True
                amount = -amount
            else:
                batch_count = min(8, max(1, round(2 * spacing * count / movement)))
            frame = next_frame
        if remaining <= 0:
            reason = "已用尽卡带滚动次数"
        self.task.log_warning(f"跑图跑商：剧情游戏卡{target_number}局部扫描停止：{reason}。")
        return None

    def locate_probe_story_card(
        self,
        card_id: str,
        scan_steps: int = QUICK_SWITCH_SCROLL_SCAN_STEPS,
    ) -> ProbedStoryCard | None:
        """Locate one supported cartridge while preserving the current selector viewport.

        Advance in short wheel batches and wait for a stable selector.
        ``scan_steps`` bounds total wheel events rather than recognition attempts.
        """

        card = CARD_BY_ID.get(card_id)
        if card is None:
            self.task.log_warning(f"剧情卡带合并测试：未知卡带：{card_id}。")
            return None
        self._badge_category = card.category
        if card.category == "character" and not self._select_cartridge_category(card.category):
            return None

        scroll_limit = max(0, int(scan_steps))
        scrolled = 0
        last_reason = "未执行识别"
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
                located = LocatedStoryCard(card, frame, badge)
                if located.structure.complete_region:
                    observed = self._collection_observation(located)
                    confirmed = (
                        self._confirm_collection_completion(observed)
                        if observed is not None else None
                    )
                    if confirmed is None:
                        self.task.log_warning(f"剧情卡带合并测试：{card_id}完整结构复核失败。")
                        return None
                    return confirmed
                last_reason = "编号已命中，但卡带完整结构被客户区边缘截断"
                self._status("卡带完成度", last_reason)

            if scrolled >= scroll_limit:
                break
            batch_count = min(
                PROBE_QUICK_SWITCH_SCROLL_COUNT,
                scroll_limit - scrolled,
            )
            scrolled += batch_count
            self._status(
                "卡带滚轮",
                f"后续卡带滚动 {scrolled}/{scroll_limit}（本批{batch_count}次）",
            )
            if not self._scroll_cartridge_bar(
                PROBE_QUICK_SWITCH_SCROLL_POINT,
                PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
                count=batch_count,
                interval=PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
                after_sleep=PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
            ):
                return None

        self.task.log_warning(
            f"剧情卡带合并测试：分批向上滚动后仍未确认剧情游戏卡{card.number}：{last_reason}。"
        )
        return None

    def enter_probe_story_card(self, probed: ProbedStoryCard) -> NavigationResult:
        """Click the revalidated probe center and confirm a stable sandbox."""

        confirmed = self._confirm_collection_completion(probed)
        if confirmed is None:
            return NavigationResult(False, ScreenState.CARD_MENU, "探针卡带完整结构复核失败")
        return self._click_located_story_card(confirmed.located)

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
            click_quick_switch=lambda: self._click_quick_switch(
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
        category = card.category
        self._badge_category = category
        if self.classify() == ScreenState.CARD_MENU:
            if not self._wait_for_quick_switch_page():
                return NavigationResult(
                    False,
                    ScreenState.CARD_MENU,
                    "当前快速切换卡带页面未通过三项 OCR 确认",
                )
            if not self._wait_for_cartridge_category(
                category=category, warn=False,
            ) and not self._select_cartridge_category(category):
                return NavigationResult(
                    False,
                    ScreenState.CARD_MENU,
                    "当前快速切换页未确认剧情游戏卡类别",
                )
        else:
            returned = self.return_home()
            if not returned.success:
                return returned
            menu = self._open_cartridge_quick_switcher(category)
            if not menu.success:
                return menu

        self._status("导航状态", f"识别剧情游戏卡{card.number}角标")
        badge_match = self._wait_for_story_badge_with_scroll(card.number)
        if badge_match is None:
            return NavigationResult(
                False,
                self.classify(),
                f"未唯一确认剧情游戏卡{card.number}角标",
                card_not_found=category == "event" and self._event_card_confirmed_absent(),
            )
        badge_frame, badge = badge_match
        if not self._story_card_target_visible(badge_frame, badge):
            reason = f"剧情游戏卡{card.number}卡带框架被客户区边缘截断"
            self._status("卡带完成度", reason)
            return NavigationResult(False, ScreenState.CARD_MENU, reason)
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

    def _event_card_confirmed_absent(self) -> bool:
        """Treat a missed event card as not held only on positive evidence.

        Every scanned frame must be a valid quick-switch frame whose target
        cover scored below the threshold, and the quick-switch page and the
        event tab must still be confirmed after the scan; an invalid capture,
        an ambiguous cover or a page that changed stays an ordinary failure.
        """
        if not getattr(self, "_event_card_scan_absent", False):
            return False
        return self._wait_for_quick_switch_page() and self._wait_for_cartridge_category(
            category="event",
        )

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
        return self._click_located_story_card(LocatedStoryCard(card, badge_frame, badge))

    def _click_located_story_card(self, located: LocatedStoryCard) -> NavigationResult:
        if not located.structure.complete_region:
            return NavigationResult(False, ScreenState.CARD_MENU, "卡带结构不完整，停止点击")
        card, badge_frame, badge = located.card, located.frame, located.badge
        self._entered_card_id = None
        self._teleport_skill_origin = None
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
            self._entered_card_id = card.card_id
            self._navigation_menu_open = False
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
            category = getattr(self, "_badge_category", "story")
            if (
                confirmed_frame.shape != frame.shape
                or not self._cartridge_category_confirmed(confirmed_frame, category)
            ):
                self._status("剧情角标点击复核", "窗口尺寸或卡带类别变化，停止点击")
                return None
            if getattr(self, "_badge_category", "story") == "character":
                current, _ = self._find_character_badge(confirmed_frame, detection.best.number)
                if current is not None and self._story_badge_identity_is_stable(detection, current):
                    if self._story_card_target_visible(confirmed_frame, current):
                        return confirmed_frame, current
                return None
            if detection.recovery_mode == "ordered_slot":
                confirmed, reason = self.inspect_story_badges(
                    confirmed_frame, range(1, 21), priority_number=detection.best.number,
                )[detection.best.number]
            else:
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
        if not self._story_card_target_visible(confirmed_frame, confirmed):
            reason = "点击前复核的卡带框架被客户区边缘截断"
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
        """Locate, recheck and click one cartridge, then confirm its field page.

        Args:
            card_id: A supported story, character or event cartridge identifier.

        Returns:
            The confirmed navigation state, or a failed result with its reason.
            Task cancellation and executor shutdown propagate to the caller.
        """
        located = self._locate_story_card(card_id)
        if isinstance(located, NavigationResult):
            return located
        return self._enter_located_story_card(located)

    def select_collection_card(
        self,
        card_id: str,
        *,
        enter_visually_complete: bool = False,
        required_actions=None,
    ) -> CollectionCardSelectionResult:
        """Inspect collection completion before deciding whether to enter a cartridge.

        Args:
            card_id: A collectable cartridge identifier.
            enter_visually_complete: Enter even if menu evidence proves completion,
                allowing pending actions to be reconciled from field observations.
            required_actions: Enabled action policy; only actions the menu can prove
                may authorize a visual-completion result.

        Returns:
            FAILED, ENTERED or VISUALLY_COMPLETE together with navigation evidence.
            This method does not commit collection progress or consume field skills.
        """
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

        observed = self._collection_observation(located)
        confirmed = self._confirm_collection_completion(observed) if observed is not None else None
        if confirmed is None:
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                NavigationResult(False, ScreenState.CARD_MENU, f"{card_id}完整卡带复核失败"),
            )
        was_done = self._completion_satisfies_actions(
            observed.completion, required_actions, card_id,
        )
        done = self._completion_satisfies_actions(confirmed.completion, required_actions, card_id)
        if done and not was_done:
            confirmed = self._confirm_collection_completion(confirmed)
            done = confirmed is not None and self._completion_satisfies_actions(
                confirmed.completion, required_actions, card_id,
            )
            if not done:
                confirmed = None
        if confirmed is None or (was_done and not done):
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                NavigationResult(False, ScreenState.CARD_MENU, f"{card_id}完成状态复核不一致"),
            )
        completion = confirmed.completion
        self._status("卡带吸取状态", f"{completion.absorb.state.value}: {completion.absorb.reason}")
        self._status(
            "卡带压制状态", f"{completion.suppress.state.value}: {completion.suppress.reason}",
        )
        self._status("卡带完成度", completion.state.value)
        if done and not enter_visually_complete:
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                NavigationResult(True, ScreenState.CARD_MENU, f"{card_id}视觉确认已启用动作完成"),
                completion,
            )
        navigation = self._click_located_story_card(confirmed.located)
        return CollectionCardSelectionResult(
            CollectionCardSelectionOutcome.ENTERED if navigation.success
            else CollectionCardSelectionOutcome.FAILED,
            navigation, completion,
        )

    def inspect_collection_card_completion(
        self,
        card_id: str,
        *,
        required_actions=None,
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
        observed = self._collection_observation(located)
        confirmed = self._confirm_collection_completion(observed) if observed is not None else None
        if confirmed is None:
            return CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.FAILED,
                NavigationResult(False, ScreenState.CARD_MENU, f"{card_id}完整卡带复核失败"),
            )
        completion = confirmed.completion
        if (
            self._completion_satisfies_actions(completion, required_actions, card_id)
            and not self._completion_satisfies_actions(
                observed.completion, required_actions, card_id,
            )
        ):
            confirmed = self._confirm_collection_completion(confirmed)
            if confirmed is None:
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.FAILED,
                    NavigationResult(False, ScreenState.CARD_MENU, f"{card_id}完成状态复核失败"),
                )
            completion = confirmed.completion
        self._status(
            "卡带吸取状态",
            f"{completion.absorb.state.value}: {completion.absorb.reason}",
        )
        self._status(
            "卡带压制状态",
            f"{completion.suppress.state.value}: {completion.suppress.reason}",
        )
        self._status("卡带完成度", completion.state.value)
        completed = self._completion_satisfies_actions(completion, required_actions, card_id)
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
                    (
                        f"{card_id}视觉确认吸取完成"
                        if card_id in ABSORB_ONLY_VERIFIED_CARD_IDS
                        else f"{card_id}视觉确认吸取与压制均完成"
                    )
                    if completed
                    else f"{card_id}未同时确认吸取与压制完成"
                ),
            ),
            completion,
        )

    def open_cartridge_quick_switcher_from_sandbox(
        self,
        *,
        sandbox_already_confirmed: bool = False,
        category: str = "story",
    ) -> NavigationResult:
        """Open and confirm the requested cartridge category from its field page.

        ``sandbox_already_confirmed`` reuses the caller's immediately preceding
        field confirmation; otherwise this method confirms the field before clicking.
        Returns a navigation result and propagates task cancellation or shutdown.
        """

        if not sandbox_already_confirmed:
            sandbox = self._wait_for_current_sandbox()
            if not sandbox.success:
                return sandbox

        self._status("导航状态", "从卡带箱庭识别快速切换按钮")
        if not self._click_quick_switch(
            timeout=QUICK_SWITCH_CLICK_TIMEOUT,
            after_sleep=QUICK_SWITCH_CLICK_AFTER_SLEEP,
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

        if not self._select_cartridge_category(category):
            return NavigationResult(
                False,
                self.classify(),
                "点击后未确认剧情游戏卡类别高亮",
            )
        return NavigationResult(True, ScreenState.CARD_MENU, "剧情游戏卡类别已确认")

    @staticmethod
    def _card_category(category: str) -> tuple[str, tuple[float, float], tuple[float, ...]]:
        """(tab label, click point, highlight region) of a quick bar tab."""
        if category == "character":
            return "角色游戏卡", CHARACTER_CATEGORY_POINT, CHARACTER_CATEGORY_HIGHLIGHT_REGION
        if category == "event":
            return "活动游戏卡", EVENT_CATEGORY_POINT, EVENT_CATEGORY_HIGHLIGHT_REGION
        return "剧情游戏卡", STORY_CATEGORY_POINT, STORY_CATEGORY_HIGHLIGHT_REGION

    def _find_character_badge(
        self,
        frame: np.ndarray,
        target_number: int,
    ) -> tuple[StoryBadgeDetection | None, str]:
        """The 角色游戏卡 tab: card n's number circle in its own fixed slot."""

        x = (
            CHARACTER_BADGE_FIRST_X + CHARACTER_BADGE_SPACING * (target_number - 1)
        ) / FHD_1080.width
        y = CHARACTER_BADGE_Y / FHD_1080.height
        half_x = CHARACTER_BADGE_WINDOW / FHD_1080.width
        half_y = CHARACTER_BADGE_WINDOW / FHD_1080.height
        window = (
            max(0.0, x - half_x),
            max(0.0, y - half_y),
            min(1.0, x + half_x),
            min(1.0, y + half_y),
        )
        ranked = sorted(
            (
                StoryBadgeCandidate(
                    number, self.vision.match(frame, replace(spec, relative_roi=window))
                )
                for number, spec in CHARACTER_BADGE_SPECS
            ),
            key=lambda candidate: candidate.result.score,
            reverse=True,
        )
        best, runner_up = ranked[0], ranked[1]
        gap = best.result.score - runner_up.result.score
        self._status(
            "角色角标",
            (
                f"{target_number}: 最像{best.number} m={best.result.score:.3f}, "
                f"p={best.result.pixel_score:.3f}, z={best.result.zncc_score:.3f}; "
                f"次像{runner_up.number} m={runner_up.result.score:.3f}"
            ),
        )
        if best.number != target_number:
            return None, f"角色卡{target_number}位置上最像的是{best.number}"
        if best.result.score < CHARACTER_BADGE_MIN_SCORE or gap < CHARACTER_BADGE_MIN_MARGIN:
            return None, (
                f"角色卡{target_number}角标不够确定：m={best.result.score:.3f}"
                f"（需>={CHARACTER_BADGE_MIN_SCORE:.2f}），领先{gap:.3f}"
                f"（需>={CHARACTER_BADGE_MIN_MARGIN:.2f}）"
            )
        return StoryBadgeDetection(best, runner_up), ""
