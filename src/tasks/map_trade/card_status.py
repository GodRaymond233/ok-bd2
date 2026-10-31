from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

import numpy as np

from src.tasks.map_trade.models import (
    MatchResult,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.vision import Vision
from src.utils.calibration import FHD_1080

CARD_ICON_LEFT_OFFSET = -15
CARD_ICON_RIGHT_OFFSET = 165
CARD_ICON_TOP_OFFSET = 80
CARD_ICON_BOTTOM_OFFSET = 130
CARD_BODY_OFFSETS = (-15, -15, 130, 90)


@dataclass(frozen=True)
class CardStructure:
    body_bounds: tuple[int, int, int, int]
    resource_bounds: tuple[int, int, int, int]
    bounds: tuple[int, int, int, int]
    complete_region: bool


def card_structure(badge_center, frame_shape, *, short_resource_row: bool = False) -> CardStructure:
    height, width = frame_shape[:2]
    center_x, center_y = badge_center

    def bounds(offsets):
        left, top, right, bottom = offsets
        return (
            round(center_x + left * width / FHD_1080.width),
            round(center_y + top * height / FHD_1080.height),
            round(center_x + right * width / FHD_1080.width),
            round(center_y + bottom * height / FHD_1080.height),
        )

    body = bounds(CARD_BODY_OFFSETS)
    resources = bounds((
        CARD_ICON_LEFT_OFFSET, CARD_ICON_TOP_OFFSET,
        CARD_BODY_OFFSETS[2] if short_resource_row else CARD_ICON_RIGHT_OFFSET,
        CARD_ICON_BOTTOM_OFFSET,
    ))
    regions = (body, resources)
    whole = (
        min(region[0] for region in regions), min(region[1] for region in regions),
        max(region[2] for region in regions), max(region[3] for region in regions),
    )
    return CardStructure(
        body, resources, whole,
        whole[0] >= 0 and whole[1] >= 0 and whole[2] <= width and whole[3] <= height,
    )

ABSORB_PENDING_TEMPLATE = TemplateSpec(
    "剧情卡带未吸取",
    "image/green/StoryAbsorbAvailableGE.png",
    threshold=0.98,
    minimum_safe_threshold=0.98,
    min_pixel_score=0.93,
    min_zncc_score=0.87,
)
ABSORB_COMPLETED_TEMPLATE = TemplateSpec(
    "剧情卡带已吸取",
    "image/green/StoryAbsorbCompletedGE.png",
    threshold=0.98,
    minimum_safe_threshold=0.98,
    min_pixel_score=0.95,
    min_zncc_score=0.96,
)
SUPPRESS_PENDING_TEMPLATE = TemplateSpec(
    "剧情卡带未压制",
    "image/green/StorySuppressAvailableGE.png",
    threshold=0.98,
    minimum_safe_threshold=0.98,
    min_pixel_score=0.92,
    min_zncc_score=0.85,
)
SUPPRESS_COMPLETED_TEMPLATE = TemplateSpec(
    "剧情卡带已压制",
    "image/green/StorySuppressCompletedGE.png",
    threshold=0.98,
    minimum_safe_threshold=0.98,
    min_pixel_score=0.95,
    min_zncc_score=0.95,
    scale_ratios=(0.98, 0.99, 1.0, 1.01, 1.02),
)


class CardActionState(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class CollectionCardSelectionOutcome(str, Enum):
    ENTERED = "entered"
    VISUALLY_COMPLETE = "visually_complete"
    FAILED = "failed"


@dataclass(frozen=True)
class CardIconEvidence:
    result: MatchResult
    green_ratio: float
    red_ratio: float
    neutral_ratio: float


@dataclass(frozen=True)
class CardActionDetection:
    state: CardActionState
    pending: tuple[CardIconEvidence, ...] = ()
    completed: tuple[CardIconEvidence, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class StoryCardCompletion:
    absorb: CardActionDetection
    suppress: CardActionDetection
    bounds: tuple[int, int, int, int]
    complete_region: bool

    @property
    def state(self) -> CardActionState:
        states = (self.absorb.state, self.suppress.state)
        if CardActionState.PENDING in states:
            return CardActionState.PENDING
        if all(state == CardActionState.COMPLETED for state in states):
            return CardActionState.COMPLETED
        return CardActionState.UNKNOWN


@dataclass(frozen=True)
class CollectionCardSelectionResult:
    outcome: CollectionCardSelectionOutcome
    navigation: NavigationResult
    completion: StoryCardCompletion | None = None

    @property
    def success(self) -> bool:
        return self.outcome != CollectionCardSelectionOutcome.FAILED

    @property
    def state(self) -> ScreenState:
        return self.navigation.state

    @property
    def message(self) -> str:
        return self.navigation.message


@dataclass(frozen=True)
class _ActionSpec:
    pending: TemplateSpec
    completed: TemplateSpec
    pending_green_minimum: float = 0.0
    pending_red_minimum: float = 0.0
    completed_green_maximum: float = 1.0
    completed_red_maximum: float = 1.0
    completed_neutral_minimum: float = 0.0


ABSORB_ACTION = _ActionSpec(
    pending=ABSORB_PENDING_TEMPLATE,
    completed=ABSORB_COMPLETED_TEMPLATE,
    pending_green_minimum=0.12,
    completed_green_maximum=0.03,
    completed_neutral_minimum=0.78,
)
SUPPRESS_ACTION = _ActionSpec(
    pending=SUPPRESS_PENDING_TEMPLATE,
    completed=SUPPRESS_COMPLETED_TEMPLATE,
    pending_red_minimum=0.20,
    completed_red_maximum=0.03,
    completed_neutral_minimum=0.85,
)


def card_icon_region(
    badge_center: tuple[int, int],
    frame_shape: tuple[int, ...],
) -> tuple[tuple[int, int, int, int], bool]:
    """Return the full card-width icon strip relative to one numeric badge."""

    height, width = frame_shape[:2]
    left, top, right, bottom = card_structure(badge_center, frame_shape).resource_bounds
    complete = left >= 0 and top >= 0 and right <= width and bottom <= height
    return (
        (
            max(0, left),
            max(0, top),
            min(width, right),
            min(height, bottom),
        ),
        complete,
    )


def _unknown(reason: str) -> CardActionDetection:
    return CardActionDetection(CardActionState.UNKNOWN, reason=reason)


class CardStatusDetector:
    """Classify absorb and suppression icons in the same frame as a badge."""

    def __init__(self, vision: Vision) -> None:
        self.vision = vision

    def detect(
        self,
        frame: np.ndarray,
        badge_center: tuple[int, int],
    ) -> StoryCardCompletion:
        bounds, complete_region = card_icon_region(badge_center, frame.shape)
        if not complete_region:
            reason = "卡带图标区域被客户区边缘截断"
            return StoryCardCompletion(
                absorb=_unknown(reason),
                suppress=_unknown(reason),
                bounds=bounds,
                complete_region=False,
            )
        return StoryCardCompletion(
            absorb=self._detect_action(frame, bounds, ABSORB_ACTION),
            suppress=self._detect_action(frame, bounds, SUPPRESS_ACTION),
            bounds=bounds,
            complete_region=True,
        )

    def _matches(
        self,
        frame: np.ndarray,
        bounds: tuple[int, int, int, int],
        spec: TemplateSpec,
    ) -> tuple[CardIconEvidence, ...]:
        height, width = frame.shape[:2]
        left, top, right, bottom = bounds
        relative_roi = (
            left / max(1, width),
            top / max(1, height),
            right / max(1, width),
            bottom / max(1, height),
        )
        search_spec = replace(
            spec,
            roi=None,
            relative_roi=relative_roi,
        )
        client_scale = min(width / FHD_1080.width, height / FHD_1080.height)
        matches = self.vision.match_all(
            frame,
            search_spec,
            minimum_score=self.vision.threshold_for(spec),
            peak_radius=max(4, round(14 * client_scale)),
            max_results=4,
        )
        return tuple(
            CardIconEvidence(
                result=result,
                green_ratio=ratios[0],
                red_ratio=ratios[1],
                neutral_ratio=ratios[2],
            )
            for result in matches
            if (
                ratios := self.vision.template_color_ratios(
                    frame,
                    spec,
                    result,
                )
            )
            is not None
        )

    def _detect_action(
        self,
        frame: np.ndarray,
        bounds: tuple[int, int, int, int],
        action: _ActionSpec,
    ) -> CardActionDetection:
        pending = tuple(
            evidence
            for evidence in self._matches(frame, bounds, action.pending)
            if (
                evidence.green_ratio >= action.pending_green_minimum
                and evidence.red_ratio >= action.pending_red_minimum
            )
        )
        completed = tuple(
            evidence
            for evidence in self._matches(frame, bounds, action.completed)
            if (
                evidence.green_ratio <= action.completed_green_maximum
                and evidence.red_ratio <= action.completed_red_maximum
                and evidence.neutral_ratio
                >= action.completed_neutral_minimum
            )
        )
        if len(pending) == 1 and not completed:
            return CardActionDetection(
                CardActionState.PENDING,
                pending=pending,
                reason="待完成图标唯一命中",
            )
        if len(completed) == 1 and not pending:
            return CardActionDetection(
                CardActionState.COMPLETED,
                completed=completed,
                reason="完成图标唯一命中",
            )
        return CardActionDetection(
            CardActionState.UNKNOWN,
            pending=pending,
            completed=completed,
            reason=(
                "双向确证不唯一："
                f"pending={len(pending)}, completed={len(completed)}"
            ),
        )
