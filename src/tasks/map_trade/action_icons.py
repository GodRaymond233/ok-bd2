from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.tasks.map_trade.vision import Vision
from src.utils.calibration import FHD_1080
from src.utils.vision_models import MatchCandidateEvidence

ACTION_ICON_TEMPLATE_SCORE = 0.95
ACTION_ICON_ZNCC_SCORE = 0.80
ACTION_ICON_USED_ZNCC_SCORE = 0.64
SEARCH_ICON_TEMPLATE_SCORE = 0.93
ACTION_ICON_SCALE_RATIOS = (1.10, 1.15, 1.20, 1.25, 1.30)
COOKING_ICON_SCALE_RATIOS = (*ACTION_ICON_SCALE_RATIOS, 1.35, 1.40)
ACTION_ICON_BRIGHT_CORE_GRAY = 180
ACTION_ICON_USED_MAX_BRIGHTNESS = 0.78
ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS = 0.85
# The action HUD is anchored to the lower-right corner of the client.  Keep
# the calibration in reference pixels and derive all runtime bounds as
# relative coordinates so the same matcher works at 720p through 4K.
SKILL_REFERENCE_WIDTH = FHD_1080.width
SKILL_REFERENCE_HEIGHT = FHD_1080.height
ACTION_SLOT_CENTERS_REFERENCE = {
    "search": (1575, 994),
    "absorb": (1530, 880),
    "summon": (1577, 774),
    "subdue": (1682, 729),
    "teleport": (1795, 788),
}
# The three bottom-right skill group switch buttons.  This is the single
# source of truth for skill group centers; collector and sandbox navigation
# derive their own constants from this map instead of re-calibrating the
# same buttons independently.
SKILL_GROUP_CENTERS_REFERENCE = {
    1: (1671, 1011),
    2: (1749, 1011),
    3: (1824, 1011),
}
ACTION_SLOT_SEARCH_RADII_REFERENCE = {
    "search": (82, 72),
    "absorb": (82, 72),
    "summon": (82, 72),
    "subdue": (82, 72),
    "teleport": (82, 72),
}
ACTION_SLOT_CENTER_RADII_REFERENCE = {
    "search": (42, 42),
    "absorb": (42, 42),
    "summon": (48, 45),
    "subdue": (44, 44),
    "teleport": (46, 46),
}


def _relative_slot_roi(
    name: str,
    radii: dict[str, tuple[int, int]],
) -> tuple[float, float, float, float]:
    center_x, center_y = ACTION_SLOT_CENTERS_REFERENCE[name]
    radius_x, radius_y = radii[name]
    return (
        max(0.0, (center_x - radius_x) / SKILL_REFERENCE_WIDTH),
        max(0.0, (center_y - radius_y) / SKILL_REFERENCE_HEIGHT),
        min(1.0, (center_x + radius_x) / SKILL_REFERENCE_WIDTH),
        min(1.0, (center_y + radius_y) / SKILL_REFERENCE_HEIGHT),
    )


ACTION_SLOT_RELATIVE_ROIS = {
    name: _relative_slot_roi(name, ACTION_SLOT_SEARCH_RADII_REFERENCE)
    for name in ACTION_SLOT_CENTERS_REFERENCE
}
ACTION_SLOT_CENTER_RELATIVE_ROIS = {
    name: _relative_slot_roi(name, ACTION_SLOT_CENTER_RADII_REFERENCE)
    for name in ACTION_SLOT_CENTERS_REFERENCE
}

# A small template-score dip is normal during HUD interpolation.  Matching is
# still location-constrained and requires a second structural metric, so this
# floor does not make a wrong-group or empty slot clickable.
ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR = 0.94
ACTION_ICON_CONSENSUS_ZNCC_FLOOR = 0.50
ACTION_ICON_CONSENSUS_PIXEL_VOTE = 0.60
ACTION_ICON_CONSENSUS_PIXEL_FLOOR = 0.10
ACTION_ICON_CONSENSUS_ZNCC_VOTE = 0.60
# These floors apply only to the local multi-evidence path.  The strict
# compatibility matcher remains unchanged for generic callers and for icons
# without a calibrated HUD slot.
ACTION_ICON_EVIDENCE_MIN_MARGIN = 0.02
ACTION_ICON_EVIDENCE_MIN_PIXEL = 0.68
ACTION_ICON_EVIDENCE_MIN_ZNCC = 0.68
ACTION_ICON_EVIDENCE_MIN_GRADIENT = 0.35
ACTION_ICON_EVIDENCE_MIN_EDGE = 0.72
ACTION_ICON_EVIDENCE_MIN_COMPOSITE = 0.76
ACTION_ICON_MIN_PHYSICAL_SIZE = 8
LIMITED_ACTION_EVIDENCE_MIN_PIXEL = 0.66
SEARCH_ICON_EVIDENCE_MIN_PIXEL = 0.68
SEARCH_ICON_EVIDENCE_MIN_ZNCC = 0.55
SEARCH_ICON_EVIDENCE_MIN_GRADIENT = 0.30
SEARCH_ICON_EVIDENCE_MIN_EDGE = 0.72
TELEPORT_ICON_EVIDENCE_MIN_PIXEL = 0.72
TELEPORT_ICON_EVIDENCE_MIN_ZNCC = 0.70
TELEPORT_ICON_EVIDENCE_MIN_GRADIENT = 0.40
TELEPORT_ICON_EVIDENCE_MIN_EDGE = 0.74


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
    # Limited-action icons use a slightly relaxed search spec so a marginal
    # interpolation frame can still be examined by the multi-metric identity
    # gate below.  ``template`` remains the strict production spec used by
    # scene confirmation and external callers.
    detection_template: TemplateSpec | None = None
    identity_min_score: float = ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR
    identity_min_zncc: float = ACTION_ICON_CONSENSUS_ZNCC_FLOOR
    slot_name: str | None = None
    search_radius_reference: tuple[int, int] | None = None
    evidence_min_pixel: float = ACTION_ICON_EVIDENCE_MIN_PIXEL
    evidence_min_zncc: float = ACTION_ICON_EVIDENCE_MIN_ZNCC
    evidence_min_gradient: float = ACTION_ICON_EVIDENCE_MIN_GRADIENT
    evidence_min_edge: float = ACTION_ICON_EVIDENCE_MIN_EDGE
    evidence_min_composite: float = ACTION_ICON_EVIDENCE_MIN_COMPOSITE


@dataclass(frozen=True)
class ActionIconDetection:
    state: ActionIconState
    match: MatchResult
    bright_core_ratio: float | None = None
    reason: str = ""
    evidence_candidates: tuple[MatchCandidateEvidence, ...] = ()
    candidate_margin: float = -1.0
    semantic_state: str = ""
    # Temporal confirmation is owned by the caller because the detector only
    # sees one frame at a time.  A raw detector result therefore starts as
    # unconfirmed; the short-window recognizers mark it stable after matching
    # the same state and physical slot in consecutive captures.
    stable: bool = False
    sample_count: int = 1

    @property
    def present(self) -> bool:
        return self.state is not ActionIconState.ABSENT

    @property
    def clickable(self) -> bool:
        """Whether this observation is safe to use as a mouse-click target."""
        return self.state is ActionIconState.AVAILABLE and self.semantic_state not in {
            "countdown",
            "empty",
            "wrong_group",
        }


def _template(
    name: str,
    file_name: str,
    *,
    roi=None,
    scale_ratios: tuple[float, ...] = ACTION_ICON_SCALE_RATIOS,
    template_score: float = ACTION_ICON_TEMPLATE_SCORE,
    min_zncc_score: float = ACTION_ICON_ZNCC_SCORE,
    relative_roi: tuple[float, float, float, float] | None = None,
    candidate_center_roi: tuple[float, float, float, float] | None = None,
) -> TemplateSpec:
    return TemplateSpec(
        name,
        f"image/green/{file_name}",
        template_score,
        roi=roi,
        relative_roi=relative_roi,
        scale_ratios=scale_ratios,
        candidate_center_roi=candidate_center_roi,
        minimum_safe_threshold=template_score,
        min_zncc_score=min_zncc_score,
    )


def _limited_action(
    name: str,
    file_name: str,
    slot_name: str,
) -> ActionIconSpec:
    relative_roi = ACTION_SLOT_RELATIVE_ROIS[slot_name]
    candidate_center_roi = ACTION_SLOT_CENTER_RELATIVE_ROIS[slot_name]
    template_name = f"{name}图标"
    strict = _template(
        template_name,
        file_name,
        relative_roi=relative_roi,
        candidate_center_roi=candidate_center_roi,
        min_zncc_score=ACTION_ICON_USED_ZNCC_SCORE,
    )
    detection = _template(
        template_name,
        file_name,
        template_score=ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR,
        relative_roi=relative_roi,
        candidate_center_roi=candidate_center_roi,
        # Keep a structural floor while allowing one correlated metric to be
        # marginal during a scaled/animated frame.  ActionIconDetector still
        # requires a second strong metric and the calibrated location.
        min_zncc_score=ACTION_ICON_CONSENSUS_ZNCC_FLOOR,
    )
    return ActionIconSpec(
        name,
        strict,
        dimmed_means_used=True,
        available_min_zncc=ACTION_ICON_USED_ZNCC_SCORE,
        used_min_zncc=ACTION_ICON_USED_ZNCC_SCORE,
        detection_template=detection,
        slot_name=slot_name,
        search_radius_reference=ACTION_SLOT_SEARCH_RADII_REFERENCE[slot_name],
        evidence_min_pixel=LIMITED_ACTION_EVIDENCE_MIN_PIXEL,
    )


SEARCH_ICON = ActionIconSpec(
    "探查",
    _template(
        "探查图标",
        "SearchIcoGE.png",
        template_score=SEARCH_ICON_TEMPLATE_SCORE,
        relative_roi=ACTION_SLOT_RELATIVE_ROIS["search"],
        candidate_center_roi=ACTION_SLOT_CENTER_RELATIVE_ROIS["search"],
    ),
    slot_name="search",
    search_radius_reference=ACTION_SLOT_SEARCH_RADII_REFERENCE["search"],
    evidence_min_pixel=SEARCH_ICON_EVIDENCE_MIN_PIXEL,
    evidence_min_zncc=SEARCH_ICON_EVIDENCE_MIN_ZNCC,
    evidence_min_gradient=SEARCH_ICON_EVIDENCE_MIN_GRADIENT,
    evidence_min_edge=SEARCH_ICON_EVIDENCE_MIN_EDGE,
)
ABSORB_ICON = _limited_action("吸收", "AbsorbIcoGE.png", "absorb")
SUMMON_ICON = _limited_action("召集", "SummonIcoGE.png", "summon")
SUBDUE_ICON = _limited_action("制服", "SubdueIcoGE.png", "subdue")
SANDBOX_TELEPORT_ICON = ActionIconSpec(
    "传送阵技能",
    _template(
        "箱庭5号传送阵技能",
        "Skill3-4GE.png",
        scale_ratios=(0.85, 0.90, 0.95, 1.0, 1.05, 1.10),
        template_score=0.95,
        min_zncc_score=0.85,
        relative_roi=ACTION_SLOT_RELATIVE_ROIS["teleport"],
        candidate_center_roi=ACTION_SLOT_CENTER_RELATIVE_ROIS["teleport"],
    ),
    slot_name="teleport",
    search_radius_reference=ACTION_SLOT_SEARCH_RADII_REFERENCE["teleport"],
    identity_min_score=0.90,
    identity_min_zncc=0.65,
    evidence_min_pixel=TELEPORT_ICON_EVIDENCE_MIN_PIXEL,
    evidence_min_zncc=TELEPORT_ICON_EVIDENCE_MIN_ZNCC,
    evidence_min_gradient=TELEPORT_ICON_EVIDENCE_MIN_GRADIENT,
    evidence_min_edge=TELEPORT_ICON_EVIDENCE_MIN_EDGE,
)

# This is deliberately separate from ``ACTION_ICONS``.  The latter is the
# generic action set used by collection and cooking; this tuple is the
# story-sandbox HUD contract and includes the fifth teleport slot.
SANDBOX_ACTION_ICONS = (
    SEARCH_ICON,
    ABSORB_ICON,
    SUMMON_ICON,
    SUBDUE_ICON,
    SANDBOX_TELEPORT_ICON,
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

    @staticmethod
    def _identity_passes(
        match: MatchResult,
        icon: ActionIconSpec,
    ) -> tuple[bool, str]:
        """Apply a location-constrained, multi-evidence identity gate.

        The template score, raw pixel score, and masked ZNCC are correlated,
        and one of them can dip by a few thousandths during interpolation or
        animation.  Keep a conservative absolute floor, then require at least
        two independent-looking votes.  This preserves hard negatives such as
        a wrong skill group, an empty slot, or a countdown overlay while
        avoiding an all-metrics ``AND`` veto on a marginal frame.
        """

        metrics = (match.score, match.pixel_score, match.zncc_score)
        if not all(np.isfinite(value) for value in metrics):
            return False, "多证据包含非有限分数"
        if match.score < icon.identity_min_score:
            return False, f"模板身份分过低：{match.score:.3f}<{icon.identity_min_score:.3f}"
        if match.zncc_score < icon.identity_min_zncc:
            return False, f"结构ZNCC过低：{match.zncc_score:.3f}<{icon.identity_min_zncc:.3f}"
        if match.pixel_score < ACTION_ICON_CONSENSUS_PIXEL_FLOOR:
            return False, (
                f"像素证据过低：{match.pixel_score:.3f}"
                f"<{ACTION_ICON_CONSENSUS_PIXEL_FLOOR:.3f}"
            )
        # The hard floors above are safety guards only.  Votes use stricter,
        # independent thresholds so a marginal template or ZNCC score can be
        # offset by the other two metrics, while zero/NaN pixel evidence can
        # never pass merely because the correlated scores look plausible.
        pixel_score = match.pixel_score
        votes = (
            match.score >= icon.template.threshold,
            pixel_score >= ACTION_ICON_CONSENSUS_PIXEL_VOTE,
            match.zncc_score >= ACTION_ICON_CONSENSUS_ZNCC_VOTE,
        )
        if sum(votes) < 2:
            return False, f"多证据票数不足：{sum(votes)}/3"
        return True, "身份多证据通过"

    @staticmethod
    def _evidence_rank(value: MatchCandidateEvidence) -> tuple[float, ...]:
        result = value.result
        return (
            result.composite_evidence_score,
            result.score,
            result.edge_score,
            result.gradient_zncc_score,
            result.zncc_score,
            result.pixel_score,
        )

    @staticmethod
    def _runtime_slot_center(
        frame: np.ndarray,
        slot_name: str,
        geometry=None,
    ) -> tuple[int, int]:
        height, width = frame.shape[:2]
        reference_x, reference_y = ACTION_SLOT_CENTERS_REFERENCE[slot_name]
        if geometry is None:
            return (
                round(width * reference_x / SKILL_REFERENCE_WIDTH),
                round(height * reference_y / SKILL_REFERENCE_HEIGHT),
            )
        return (
            geometry.content_left
            + round(geometry.content_width * reference_x / SKILL_REFERENCE_WIDTH),
            geometry.content_top
            + round(geometry.content_height * reference_y / SKILL_REFERENCE_HEIGHT),
        )

    def _evidence_match(
        self,
        frame: np.ndarray,
        icon: ActionIconSpec,
        geometry=None,
    ) -> tuple[MatchResult, tuple[MatchCandidateEvidence, ...], float] | None:
        """Find one icon inside its calibrated HUD slot before identity gating."""

        if icon.slot_name is None or icon.search_radius_reference is None:
            return None
        matcher = getattr(self.vision, "match_slot_evidence", None)
        if not callable(matcher):
            return None

        assess = getattr(self.vision, "assess_frame", None)
        if geometry is None and callable(assess):
            geometry = assess(
                frame,
                required_relative_rois=(ACTION_SLOT_RELATIVE_ROIS[icon.slot_name],),
                purpose=f"{icon.name}图标",
            )
            if not geometry.accepted:
                return (
                    MatchResult(
                        -1.0,
                        (0, 0),
                        (0, 0),
                        rejection_reasons=geometry.rejection_reasons,
                    ),
                    (),
                    -1.0,
                )
        height, width = frame.shape[:2]
        client_scale = (
            geometry.client_scale
            if geometry is not None
            else min(width / SKILL_REFERENCE_WIDTH, height / SKILL_REFERENCE_HEIGHT)
        )
        radius = tuple(
            max(6, round(float(value) * client_scale))
            for value in icon.search_radius_reference
        )
        center = self._runtime_slot_center(frame, icon.slot_name, geometry)
        search_spec = icon.detection_template or icon.template
        try:
            candidates = tuple(
                matcher(
                    frame,
                    search_spec,
                    center,
                    radius=radius,
                    geometry=geometry,
                    minimum_score=0.35,
                    max_results=8,
                    purpose=f"{icon.name}图标",
                )
            )
        except (AttributeError, TypeError, ValueError):
            # Older test doubles and downstream adapters may expose only the
            # strict matcher signature.  They retain the compatibility path.
            return None
        if not candidates:
            return MatchResult(-1.0, (0, 0), (0, 0)), (), -1.0
        ranked = tuple(sorted(candidates, key=self._evidence_rank, reverse=True))
        best = ranked[0]
        margin = (
            self._evidence_rank(best)[0] - self._evidence_rank(ranked[1])[0]
            if len(ranked) > 1
            else -1.0
        )
        return best.result, ranked, float(margin)

    @staticmethod
    def _evidence_identity_passes(
        match: MatchResult,
        icon: ActionIconSpec,
        margin: float,
    ) -> tuple[bool, str]:
        values = (
            match.score,
            match.pixel_score,
            match.zncc_score,
            match.gradient_zncc_score,
            match.edge_score,
        )
        if not all(np.isfinite(value) and value > -1.0 for value in values):
            if match.rejection_reasons:
                return False, "画面几何拒绝：" + "|".join(match.rejection_reasons)
            return False, "局部证据包含非有限或缺失分数"
        if (
            match.size[0] < ACTION_ICON_MIN_PHYSICAL_SIZE
            or match.size[1] < ACTION_ICON_MIN_PHYSICAL_SIZE
        ):
            return False, (
                f"图标物理尺寸过小：{match.size[0]}x{match.size[1]}"
                f"<{ACTION_ICON_MIN_PHYSICAL_SIZE}"
            )
        if match.score < icon.identity_min_score:
            return False, f"模板身份分过低：{match.score:.3f}<{icon.identity_min_score:.3f}"
        if match.pixel_score < icon.evidence_min_pixel:
            return False, f"局部像素证据过低：{match.pixel_score:.3f}<{icon.evidence_min_pixel:.3f}"
        if match.zncc_score < icon.evidence_min_zncc:
            return False, f"原始ZNCC过低：{match.zncc_score:.3f}<{icon.evidence_min_zncc:.3f}"
        if match.gradient_zncc_score < icon.evidence_min_gradient:
            return False, (
                f"梯度ZNCC过低：{match.gradient_zncc_score:.3f}"
                f"<{icon.evidence_min_gradient:.3f}"
            )
        if match.edge_score < icon.evidence_min_edge:
            return False, f"边缘一致性过低：{match.edge_score:.3f}<{icon.evidence_min_edge:.3f}"
        if match.composite_evidence_score < icon.evidence_min_composite:
            return False, (
                f"组合证据过低：{match.composite_evidence_score:.3f}"
                f"<{icon.evidence_min_composite:.3f}"
            )
        if margin >= 0.0 and margin < ACTION_ICON_EVIDENCE_MIN_MARGIN:
            return False, f"局部候选分差不足：{margin:.3f}<{ACTION_ICON_EVIDENCE_MIN_MARGIN:.3f}"
        return True, "局部多尺度多证据通过"

    def detect(
        self,
        frame: np.ndarray,
        icon: ActionIconSpec,
        *,
        geometry=None,
    ) -> ActionIconDetection:
        search_spec = icon.detection_template or icon.template
        evidence_match = self._evidence_match(frame, icon, geometry)
        if evidence_match is not None:
            match, evidence_candidates, candidate_margin = evidence_match
            identity_passed, identity_reason = self._evidence_identity_passes(
                match,
                icon,
                candidate_margin,
            )
        else:
            match = self.vision.match(frame, search_spec)
            evidence_candidates = ()
            candidate_margin = -1.0
            if icon.detection_template is None:
                identity_passed = self.vision.passes(match, icon.template)
                identity_reason = "身份门槛通过" if identity_passed else "形状身份门槛未通过"
            else:
                identity_passed, identity_reason = self._identity_passes(match, icon)
        if not identity_passed:
            return ActionIconDetection(
                ActionIconState.ABSENT,
                match,
                reason=identity_reason,
                evidence_candidates=evidence_candidates,
                candidate_margin=candidate_margin,
                semantic_state="absent",
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
                evidence_candidates,
                candidate_margin,
                "available",
            )
        if bright_core_ratio <= ACTION_ICON_USED_MAX_BRIGHTNESS:
            if icon.detection_template is None and match.zncc_score < (
                icon.used_min_zncc or icon.available_min_zncc
            ):
                return ActionIconDetection(
                    ActionIconState.ABSENT,
                    match,
                    bright_core_ratio,
                    "暗态形状身份门槛未通过",
                    evidence_candidates,
                    candidate_margin,
                    "absent",
                )
            return ActionIconDetection(
                ActionIconState.USED,
                match,
                bright_core_ratio,
                "身份已确认且亮核心变暗",
                evidence_candidates,
                candidate_margin,
                "used",
            )
        if bright_core_ratio >= ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS:
            if icon.detection_template is None and match.zncc_score < icon.available_min_zncc:
                return ActionIconDetection(
                    ActionIconState.UNKNOWN,
                    match,
                    bright_core_ratio,
                    "亮核心正常，但可点击形状门槛未通过",
                    evidence_candidates,
                    candidate_margin,
                    "unknown",
                )
            return ActionIconDetection(
                ActionIconState.AVAILABLE,
                match,
                bright_core_ratio,
                "身份已确认且亮核心亮度正常",
                evidence_candidates,
                candidate_margin,
                "available",
            )
        return ActionIconDetection(
            ActionIconState.UNKNOWN,
            match,
            bright_core_ratio,
            "身份已确认，但亮核心处于状态缓冲区",
            evidence_candidates,
            candidate_margin,
            "unknown",
        )


__all__ = [
    "ABSORB_ICON",
    "ACTION_ICONS",
    "ACTION_ICON_AVAILABLE_MIN_BRIGHTNESS",
    "ACTION_ICON_BRIGHT_CORE_GRAY",
    "ACTION_ICON_CONSENSUS_PIXEL_FLOOR",
    "ACTION_ICON_CONSENSUS_PIXEL_VOTE",
    "ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR",
    "ACTION_ICON_CONSENSUS_ZNCC_FLOOR",
    "ACTION_ICON_CONSENSUS_ZNCC_VOTE",
    "ACTION_ICON_SCALE_RATIOS",
    "ACTION_ICON_TEMPLATE_SCORE",
    "ACTION_ICON_USED_MAX_BRIGHTNESS",
    "ACTION_ICON_USED_ZNCC_SCORE",
    "ACTION_ICON_ZNCC_SCORE",
    "ACTION_ICON_EVIDENCE_MIN_COMPOSITE",
    "ACTION_ICON_EVIDENCE_MIN_EDGE",
    "ACTION_ICON_EVIDENCE_MIN_GRADIENT",
    "ACTION_ICON_EVIDENCE_MIN_MARGIN",
    "ACTION_ICON_MIN_PHYSICAL_SIZE",
    "LIMITED_ACTION_EVIDENCE_MIN_PIXEL",
    "ACTION_ICON_EVIDENCE_MIN_PIXEL",
    "ACTION_ICON_EVIDENCE_MIN_ZNCC",
    "ACTION_SLOT_CENTER_RELATIVE_ROIS",
    "ACTION_SLOT_CENTER_RADII_REFERENCE",
    "ACTION_SLOT_CENTERS_REFERENCE",
    "ACTION_SLOT_RELATIVE_ROIS",
    "ACTION_SLOT_SEARCH_RADII_REFERENCE",
    "COOKING_ICON",
    "COOKING_ICON_SCALE_RATIOS",
    "INTERACT_ICON",
    "SEARCH_ICON",
    "SEARCH_ICON_TEMPLATE_SCORE",
    "SEARCH_ICON_EVIDENCE_MIN_EDGE",
    "SEARCH_ICON_EVIDENCE_MIN_GRADIENT",
    "SEARCH_ICON_EVIDENCE_MIN_PIXEL",
    "SEARCH_ICON_EVIDENCE_MIN_ZNCC",
    "SKILL_GROUP_CENTERS_REFERENCE",
    "SUBDUE_ICON",
    "SUMMON_ICON",
    "SANDBOX_TELEPORT_ICON",
    "SANDBOX_ACTION_ICONS",
    "ActionIconDetection",
    "ActionIconDetector",
    "ActionIconSpec",
    "ActionIconState",
    "SKILL_REFERENCE_HEIGHT",
    "SKILL_REFERENCE_WIDTH",
    "TELEPORT_ICON_EVIDENCE_MIN_EDGE",
    "TELEPORT_ICON_EVIDENCE_MIN_GRADIENT",
    "TELEPORT_ICON_EVIDENCE_MIN_PIXEL",
    "TELEPORT_ICON_EVIDENCE_MIN_ZNCC",
]
