from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.tasks.map_trade.vision import Vision

ACTION_ICON_TEMPLATE_SCORE = 0.95
ACTION_ICON_ZNCC_SCORE = 0.80
ACTION_ICON_USED_ZNCC_SCORE = 0.64
SEARCH_ICON_TEMPLATE_SCORE = 0.93
ACTION_ICON_SCALE_RATIOS = (1.10, 1.15, 1.20, 1.25, 1.30)
COOKING_ICON_SCALE_RATIOS = (*ACTION_ICON_SCALE_RATIOS, 1.35, 1.40)
ACTION_ICON_BRIGHT_CORE_GRAY = 180
ACTION_ICON_USED_MAX_BRIGHTNESS = 0.78
ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS = 0.85


class ActionIconState(str, Enum):
    ABSENT = "absent"
    AVAILABLE = "available"
    USED = "used"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionIconSpec:
    name: str
    template: TemplateSpec
    dimmed_means_used: bool = False
    available_min_zncc: float = ACTION_ICON_ZNCC_SCORE
    used_min_zncc: float | None = None


@dataclass(frozen=True)
class ActionIconDetection:
    state: ActionIconState
    match: MatchResult
    bright_core_ratio: float | None = None
    reason: str = ""

    @property
    def present(self) -> bool:
        return self.state is not ActionIconState.ABSENT


def _template(
    name: str,
    file_name: str,
    *,
    roi=None,
    scale_ratios: tuple[float, ...] = ACTION_ICON_SCALE_RATIOS,
    template_score: float = ACTION_ICON_TEMPLATE_SCORE,
    min_zncc_score: float = ACTION_ICON_ZNCC_SCORE,
) -> TemplateSpec:
    return TemplateSpec(
        name,
        f"image/green/{file_name}",
        template_score,
        roi=roi,
        scale_ratios=scale_ratios,
        minimum_safe_threshold=template_score,
        min_zncc_score=min_zncc_score,
    )


SEARCH_ICON = ActionIconSpec(
    "探查",
    _template(
        "探查图标",
        "SearchIcoGE.png",
        roi=(930, 590, 140, 120),
        template_score=SEARCH_ICON_TEMPLATE_SCORE,
    ),
)
ABSORB_ICON = ActionIconSpec(
    "吸收",
    _template(
        "吸收图标",
        "AbsorbIcoGE.png",
        roi=(900, 500, 150, 120),
        min_zncc_score=ACTION_ICON_USED_ZNCC_SCORE,
    ),
    dimmed_means_used=True,
    used_min_zncc=ACTION_ICON_USED_ZNCC_SCORE,
)
SUMMON_ICON = ActionIconSpec(
    "召集",
    _template(
        "召集图标",
        "SummonIcoGE.png",
        roi=(930, 405, 150, 125),
        min_zncc_score=ACTION_ICON_USED_ZNCC_SCORE,
    ),
    dimmed_means_used=True,
    used_min_zncc=ACTION_ICON_USED_ZNCC_SCORE,
)
SUBDUE_ICON = ActionIconSpec(
    "制服",
    _template(
        "制服图标",
        "SubdueIcoGE.png",
        min_zncc_score=ACTION_ICON_USED_ZNCC_SCORE,
    ),
    dimmed_means_used=True,
    used_min_zncc=ACTION_ICON_USED_ZNCC_SCORE,
)
INTERACT_ICON = ActionIconSpec(
    "交互",
    _template("交互图标", "InteractIcoGE.png"),
)
COOKING_ICON = ActionIconSpec(
    "制作料理",
    _template(
        "制作料理图标",
        "CookingIcoGE.png",
        scale_ratios=COOKING_ICON_SCALE_RATIOS,
    ),
)
ACTION_ICONS = (
    SEARCH_ICON,
    ABSORB_ICON,
    SUMMON_ICON,
    SUBDUE_ICON,
    INTERACT_ICON,
    COOKING_ICON,
)


class ActionIconDetector:
    """Separate icon identity from the dimmed state of limited actions."""

    def __init__(self, vision: Vision) -> None:
        self.vision = vision

    def detect(self, frame: np.ndarray, icon: ActionIconSpec) -> ActionIconDetection:
        match = self.vision.match(frame, icon.template)
        if not self.vision.passes(match, icon.template):
            return ActionIconDetection(
                ActionIconState.ABSENT,
                match,
                reason="形状身份门槛未通过",
            )

        bright_core_ratio = self.vision.template_brightness_ratio(
            frame,
            icon.template,
            match,
            minimum_template_gray=ACTION_ICON_BRIGHT_CORE_GRAY,
        )
        if not icon.dimmed_means_used:
            return ActionIconDetection(
                ActionIconState.AVAILABLE,
                match,
                bright_core_ratio,
                "身份已确认；该图标不使用亮度推断已使用状态",
            )
        if bright_core_ratio <= ACTION_ICON_USED_MAX_BRIGHTNESS:
            if match.zncc_score < (icon.used_min_zncc or icon.available_min_zncc):
                return ActionIconDetection(
                    ActionIconState.ABSENT,
                    match,
                    bright_core_ratio,
                    "暗态形状身份门槛未通过",
                )
            return ActionIconDetection(
                ActionIconState.USED,
                match,
                bright_core_ratio,
                "身份已确认且亮核心变暗",
            )
        if bright_core_ratio >= ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS:
            if match.zncc_score < icon.available_min_zncc:
                return ActionIconDetection(
                    ActionIconState.UNKNOWN,
                    match,
                    bright_core_ratio,
                    "亮核心正常，但可点击形状门槛未通过",
                )
            return ActionIconDetection(
                ActionIconState.AVAILABLE,
                match,
                bright_core_ratio,
                "身份已确认且亮核心亮度正常",
            )
        return ActionIconDetection(
            ActionIconState.UNKNOWN,
            match,
            bright_core_ratio,
            "身份已确认，但亮核心处于状态缓冲区",
        )


__all__ = [
    "ABSORB_ICON",
    "ACTION_ICONS",
    "ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS",
    "ACTION_ICON_BRIGHT_CORE_GRAY",
    "ACTION_ICON_SCALE_RATIOS",
    "ACTION_ICON_TEMPLATE_SCORE",
    "ACTION_ICON_USED_MAX_BRIGHTNESS",
    "ACTION_ICON_USED_ZNCC_SCORE",
    "ACTION_ICON_ZNCC_SCORE",
    "COOKING_ICON",
    "COOKING_ICON_SCALE_RATIOS",
    "INTERACT_ICON",
    "SEARCH_ICON",
    "SEARCH_ICON_TEMPLATE_SCORE",
    "SUBDUE_ICON",
    "SUMMON_ICON",
    "ActionIconDetection",
    "ActionIconDetector",
    "ActionIconSpec",
    "ActionIconState",
]
