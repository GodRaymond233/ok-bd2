from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

import numpy as np

from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.tasks.map_trade.vision import Vision
from src.utils.calibration import FHD_1080
from src.utils.image_utils import (
    edge_consistency,
    resize_mask,
    resize_template_with_interpolation,
    template_candidate_evidence,
    template_match_response,
)
from src.utils.vision_models import FrameGeometry, MatchCandidateEvidence

ACTION_ICON_TEMPLATE_SCORE = 0.95
ACTION_ICON_ZNCC_SCORE = 0.80
ACTION_ICON_USED_ZNCC_SCORE = 0.64
SEARCH_ICON_TEMPLATE_SCORE = 0.93
ACTION_ICON_SCALE_RATIOS = (1.10, 1.15, 1.20, 1.25, 1.30)
COOKING_ICON_SCALE_RATIOS = (*ACTION_ICON_SCALE_RATIOS, 1.35, 1.40)
ACTION_ICON_BRIGHT_CORE_GRAY = 180
ACTION_ICON_REFINEMENT_SEEDS = 6
ACTION_ICON_RETAINED_CANDIDATES = 8
ACTION_ICON_DISTINCT_DISTANCE_SQUARED = 25
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
ACTION_ICON_CONSENSUS_PIXEL_FLOOR = 0.10
# These floors apply to calibrated HUD slots. Icons without a slot use their template gates.
ACTION_ICON_EVIDENCE_MIN_MARGIN = 0.02
ACTION_ICON_EVIDENCE_MIN_PIXEL = 0.68
ACTION_ICON_EVIDENCE_MIN_ZNCC = 0.68
ACTION_ICON_EVIDENCE_MIN_GRADIENT = 0.35
ACTION_ICON_EVIDENCE_MIN_EDGE = 0.72
ACTION_ICON_EVIDENCE_MIN_COMPOSITE = 0.76
ACTION_ICON_MIN_PHYSICAL_SIZE = 8
LIMITED_ACTION_EVIDENCE_MIN_PIXEL = 0.66
LIMITED_ACTION_EVIDENCE_MIN_GRADIENT = 0.30
LIMITED_ACTION_EVIDENCE_MIN_EDGE = 0.82
LIMITED_ACTION_USED_MAX_CONTRAST = 0.65
LIMITED_ACTION_AVAILABLE_MIN_CONTRAST = 0.80
ABSORB_USED_MIN_ZNCC = 0.55
ABSORB_USED_MIN_PIXEL = 0.75
ABSORB_USED_MIN_GRADIENT = 0.50
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
    relative_contrast: float | None = None

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
        slot_name: str,
        geometry: FrameGeometry,
    ) -> tuple[int, int]:
        reference_x, reference_y = ACTION_SLOT_CENTERS_REFERENCE[slot_name]
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
    ) -> tuple[MatchResult, tuple[MatchCandidateEvidence, ...], float, float | None] | None:
        """Find one icon inside its calibrated HUD slot before identity gating."""

        if icon.slot_name is None:
            return None
        if geometry is None:
            geometry = self.vision.assess_frame(
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
                    None,
                )
        client_scale = geometry.client_scale
        radius = tuple(
            max(6, round(float(value) * client_scale))
            for value in icon.search_radius_reference
        )
        center = self._runtime_slot_center(icon.slot_name, geometry)
        search_spec = icon.detection_template or icon.template
        try:
            candidates = tuple(
                self.vision.match_slot_evidence(
                    frame,
                    search_spec,
                    center,
                    radius=radius,
                    geometry=geometry,
                    minimum_score=0.35,
                    max_results=8,
                    purpose=f"{icon.name}图标",
                    preserve_variants=icon.dimmed_means_used,
                )
            )
        except (AttributeError, TypeError, ValueError) as exc:
            return (
                MatchResult(-1.0, (0, 0), (0, 0), rejection_reasons=(
                    f"技能候选识别失败：{type(exc).__name__}",
                )),
                (), -1.0, None,
            )
        if not candidates:
            return MatchResult(-1.0, (0, 0), (0, 0)), (), -1.0, None
        if icon.dimmed_means_used:
            return self._limited_evidence_match(
                frame, icon, candidates, geometry, center, radius, client_scale,
            )
        ranked = tuple(sorted(candidates, key=self._evidence_rank, reverse=True))
        best = ranked[0]
        margin = (
            self._evidence_rank(best)[0] - self._evidence_rank(ranked[1])[0]
            if len(ranked) > 1
            else -1.0
        )
        return best.result, ranked, float(margin), None

    def _limited_evidence_match(
        self,
        frame: np.ndarray,
        icon: ActionIconSpec,
        candidates: tuple[MatchCandidateEvidence, ...],
        geometry: FrameGeometry,
        center: tuple[int, int],
        radius: tuple[int, int],
        client_scale: float,
    ) -> tuple[MatchResult, tuple[MatchCandidateEvidence, ...], float, float | None]:
        """Validate each scale/interpolation before merging candidates at one location."""

        gray = self.vision._gray(frame)
        template, mask = self.vision._load(icon.template)
        spec = icon.detection_template or icon.template
        bounds = self.vision._center_bounds(
            spec, frame_width=gray.shape[1], frame_height=gray.shape[0],
            left=0, top=0, geometry=geometry,
        )
        content = geometry.content_rect
        left = max(0, content[0], center[0] - radius[0])
        top = max(0, content[1], center[1] - radius[1])
        right = min(gray.shape[1], content[0] + content[2], center[0] + radius[0] + 1)
        bottom = min(gray.shape[0], content[1] + content[3], center[1] + radius[1] + 1)
        scaled_templates = {}

        def in_bounds(result: MatchResult) -> bool:
            x, y = result.position
            width, height = result.size
            return (
                width >= ACTION_ICON_MIN_PHYSICAL_SIZE
                and height >= ACTION_ICON_MIN_PHYSICAL_SIZE
                and left <= x and top <= y and x + width <= right and y + height <= bottom
                and (bounds is None or (
                    bounds[0] <= x + width / 2 < bounds[2]
                    and bounds[1] <= y + height / 2 < bounds[3]
                ))
            )

        def scaled_for(candidate):
            key = (candidate.scale, candidate.interpolation)
            if key not in scaled_templates:
                scaled_templates[key] = self._scale_limited_template(template, mask, candidate)
            return scaled_templates[key]

        def measure(candidate):
            result = candidate.result
            if (
                not in_bounds(result)
                or not all(np.isfinite(value) for value in (
                    result.score, result.pixel_score, result.zncc_score,
                    result.gradient_zncc_score, result.edge_score, candidate.scale,
                ))
                or result.score < icon.identity_min_score
                or result.zncc_score < icon.identity_min_zncc
            ):
                return None
            scaled_data = scaled_for(candidate)
            if scaled_data is None:
                return None
            return self._measure_limited_candidate(gray, icon, candidate, scaled_data, client_scale)

        measured = [value for candidate in candidates if (value := measure(candidate)) is not None]
        if measured and not any(value.accepted for value, _ in measured):
            # Small glyphs can peak one pixel away from the strongest structural
            # alignment. Refine only plausible peaks, and keep all slot bounds.
            seeds = sorted(
                measured, key=lambda value: self._evidence_rank(value[0]), reverse=True,
            )[:ACTION_ICON_REFINEMENT_SEEDS]
            for original, _ in seeds:
                scaled, scaled_mask, *_ = scaled_for(original)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == dy == 0:
                            continue
                        result = replace(original.result, position=(
                            original.result.position[0] + dx, original.result.position[1] + dy,
                        ))
                        if not in_bounds(result):
                            continue
                        x, y = result.position
                        width, height = result.size
                        patch = gray[y:y + height, x:x + width]
                        score = float(template_match_response(patch, scaled, scaled_mask)[0, 0])
                        pixel, zncc, gradient, edge = template_candidate_evidence(
                            patch, scaled, scaled_mask,
                        )
                        result = replace(
                            result, score=score, pixel_score=pixel, zncc_score=zncc,
                            gradient_zncc_score=gradient, edge_score=edge,
                        )
                        refined = measure(replace(original, result=result))
                        if refined is not None:
                            measured.append(refined)
        return self._rank_limited_candidates(measured)

    @staticmethod
    def _normalize_icon_patch(image, active):
        low, high = np.percentile(image[active], (10, 90))
        normalized = (image.astype(float) - low) * 255 / max(1.0, high - low)
        return np.clip(normalized, 0, 255).astype(np.uint8)

    def _scale_limited_template(self, template, mask, candidate):
        interpolation = {
            name: mode for mode, name in self.vision._interpolation_modes(candidate.scale)
        }
        if candidate.interpolation not in interpolation or mask is None:
            return None
        scaled = resize_template_with_interpolation(
            template, candidate.scale, interpolation[candidate.interpolation],
        )
        scaled_mask = resize_mask(mask, candidate.scale)
        active = scaled_mask > 0
        foreground = active & (scaled >= ACTION_ICON_BRIGHT_CORE_GRAY)
        background = ~active
        if not foreground.any() or not background.any():
            return None
        return (
            scaled, scaled_mask, foreground, background,
            self._normalize_icon_patch(scaled, active),
        )

    def _measure_limited_candidate(self, gray, icon, candidate, scaled_data, client_scale):
        scaled, scaled_mask, foreground, background, normalized_template = scaled_data
        result = candidate.result
        x, y = result.position
        width, height = result.size
        patch = gray[y:y + height, x:x + width]
        if patch.shape != scaled.shape:
            return None
        foreground_gray = float(np.median(patch[foreground]))
        background_gray = float(np.median(patch[background]))
        contrast = (foreground_gray - background_gray) / max(1.0, 255.0 - background_gray)
        normalized_edge = edge_consistency(
            self._normalize_icon_patch(patch, scaled_mask > 0), normalized_template, scaled_mask,
            tolerance=max(1, round(client_scale)),
        )
        result = replace(result, edge_score=normalized_edge)
        passed, reason = self._evidence_identity_passes(
            result, icon, 1.0, relative_contrast=contrast,
        )
        return (
            replace(candidate, result=result, rejection_reasons=() if passed else (reason,)),
            contrast,
        )

    def _rank_limited_candidates(self, measured):
        if not measured:
            return MatchResult(-1.0, (0, 0), (0, 0)), (), -1.0, None
        measured.sort(
            key=lambda value: (value[0].accepted, self._evidence_rank(value[0])), reverse=True,
        )
        best, contrast = measured[0]
        # Variants at the same physical position do not compete for the margin.
        independent = [value for value, _ in measured[1:] if (
            (value.result.center[0] - best.result.center[0]) ** 2
            + (value.result.center[1] - best.result.center[1]) ** 2
            > ACTION_ICON_DISTINCT_DISTANCE_SQUARED
        )]
        margin = self._evidence_rank(best)[0] - max(
            (self._evidence_rank(value)[0] for value in independent),
            default=self._evidence_rank(best)[0] - 1.0,
        )
        retained = [best]
        for candidate in independent:
            if any(
                (candidate.result.center[0] - kept.result.center[0]) ** 2
                + (candidate.result.center[1] - kept.result.center[1]) ** 2
                <= ACTION_ICON_DISTINCT_DISTANCE_SQUARED
                for kept in retained
            ):
                continue
            retained.append(candidate)
            if len(retained) >= ACTION_ICON_RETAINED_CANDIDATES:
                break
        return best.result, tuple(retained), float(margin), contrast

    @staticmethod
    def _evidence_identity_passes(
        match: MatchResult,
        icon: ActionIconSpec,
        margin: float,
        relative_contrast: float | None = None,
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
                return False, "识别证据拒绝：" + "|".join(match.rejection_reasons)
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
        # Dimmed icons lose pixel intensity while retaining their shape. Only
        # confirmed dimming may use the low pixel floor; bright/unknown states
        # keep the stricter pixel gate before becoming clickable.
        is_dimmed = (
            icon.dimmed_means_used
            and relative_contrast is not None
            and relative_contrast <= LIMITED_ACTION_USED_MAX_CONTRAST
        )
        # The translucent used absorb glyph needs stronger gradient/pixel evidence
        # when the scene beneath it reduces raw correlation. This cannot admit a cast.
        used_absorb_shape = (
            icon.slot_name == "absorb" and is_dimmed
            and match.zncc_score >= ABSORB_USED_MIN_ZNCC
            and match.pixel_score >= ABSORB_USED_MIN_PIXEL
            and match.gradient_zncc_score >= ABSORB_USED_MIN_GRADIENT
            and match.edge_score >= LIMITED_ACTION_EVIDENCE_MIN_EDGE
        )
        if match.zncc_score < icon.evidence_min_zncc and not used_absorb_shape:
            return False, f"原始ZNCC过低：{match.zncc_score:.3f}<{icon.evidence_min_zncc:.3f}"
        if is_dimmed:
            if match.pixel_score < ACTION_ICON_CONSENSUS_PIXEL_FLOOR:
                return False, (
                    f"变暗图标像素证据低于保底：{match.pixel_score:.3f}"
                    f"<{ACTION_ICON_CONSENSUS_PIXEL_FLOOR:.3f}"
                )
        elif match.pixel_score < icon.evidence_min_pixel:
            return False, f"局部像素证据过低：{match.pixel_score:.3f}<{icon.evidence_min_pixel:.3f}"

        minimum_gradient = (
            LIMITED_ACTION_EVIDENCE_MIN_GRADIENT
            if relative_contrast is not None else icon.evidence_min_gradient
        )
        minimum_edge = (
            LIMITED_ACTION_EVIDENCE_MIN_EDGE
            if relative_contrast is not None else icon.evidence_min_edge
        )
        if match.gradient_zncc_score < minimum_gradient:
            return False, (
                f"梯度ZNCC过低：{match.gradient_zncc_score:.3f}"
                f"<{minimum_gradient:.3f}"
            )
        if match.edge_score < minimum_edge:
            return False, f"边缘一致性过低：{match.edge_score:.3f}<{minimum_edge:.3f}"
        if (
            relative_contrast is None
            and match.composite_evidence_score < icon.evidence_min_composite
        ):
            return False, (
                f"组合证据过低：{match.composite_evidence_score:.3f}"
                f"<{icon.evidence_min_composite:.3f}"
            )
        if (
            (relative_contrast is not None or margin >= 0.0)
            and margin < ACTION_ICON_EVIDENCE_MIN_MARGIN
        ):
            return False, f"局部候选分差不足：{margin:.3f}<{ACTION_ICON_EVIDENCE_MIN_MARGIN:.3f}"
        return True, "局部多尺度多证据通过"

    def detect(
        self,
        frame: np.ndarray,
        icon: ActionIconSpec,
        *,
        geometry=None,
    ) -> ActionIconDetection:
        evidence_match = self._evidence_match(frame, icon, geometry)
        relative_contrast = None
        if evidence_match is not None:
            match, evidence_candidates, candidate_margin, relative_contrast = evidence_match
            identity_passed, identity_reason = self._evidence_identity_passes(
                match,
                icon,
                candidate_margin,
                relative_contrast=relative_contrast,
            )
        else:
            match = self.vision.match(frame, icon.template)
            evidence_candidates = ()
            candidate_margin = -1.0
            identity_passed = self.vision.passes(match, icon.template)
            identity_reason = "身份门槛通过" if identity_passed else "形状身份门槛未通过"

        if not identity_passed:
            return ActionIconDetection(
                ActionIconState.ABSENT,
                match,
                reason=identity_reason,
                evidence_candidates=evidence_candidates,
                candidate_margin=candidate_margin,
                semantic_state="absent",
                relative_contrast=relative_contrast,
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
        state = (
            ActionIconState.USED if relative_contrast <= LIMITED_ACTION_USED_MAX_CONTRAST
            else ActionIconState.AVAILABLE
            if relative_contrast >= LIMITED_ACTION_AVAILABLE_MIN_CONTRAST
            else ActionIconState.UNKNOWN
        )
        return ActionIconDetection(
            state, match, bright_core_ratio,
            f"身份已确认；相对底色对比度={relative_contrast:.3f}，状态={state.value}",
            evidence_candidates, candidate_margin, state.value,
            relative_contrast=relative_contrast,
        )


__all__ = [
    "ABSORB_ICON",
    "ACTION_ICONS",
    "ACTION_ICON_BRIGHT_CORE_GRAY",
    "ACTION_ICON_CONSENSUS_PIXEL_FLOOR",
    "ACTION_ICON_CONSENSUS_TEMPLATE_FLOOR",
    "ACTION_ICON_CONSENSUS_ZNCC_FLOOR",
    "ACTION_ICON_SCALE_RATIOS",
    "ACTION_ICON_TEMPLATE_SCORE",
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
