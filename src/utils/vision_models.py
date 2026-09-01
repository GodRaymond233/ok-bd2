from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class TemplateSpec:
    """Shared offline-template declaration for every task's matcher."""

    name: str
    file_name: str
    threshold: float = 0.76
    roi: tuple[int, int, int, int] | None = None
    green_mask: bool = False
    relative_roi: tuple[float, float, float, float] | None = None
    reference_scale: float | None = None
    scale_ratios: tuple[float, ...] = (1.0,)
    min_pixel_score: float | None = None
    candidate_center_roi: tuple[float, float, float, float] | None = None
    minimum_safe_threshold: float | None = None
    min_zncc_score: float | None = None
    # Per-task configuration keys.  ``threshold_key``/``default_threshold``
    # make the runtime threshold configurable; ``candidate_threshold`` is an
    # optional stricter floor used only by the matcher (not by ``passes``).
    threshold_key: str | None = None
    default_threshold: float | None = None
    candidate_threshold: float | None = None
    crop: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class FrameGeometry:
    """Resolution and effective-content geometry for a captured client frame."""

    frame_width: int
    frame_height: int
    content_left: int
    content_top: int
    content_width: int
    content_height: int
    scale_x: float
    scale_y: float
    client_scale: float
    aspect_ratio: float
    effective_aspect_ratio: float
    rejection_reasons: tuple[str, ...] = ()

    @property
    def content_rect(self) -> tuple[int, int, int, int]:
        return (
            self.content_left,
            self.content_top,
            self.content_width,
            self.content_height,
        )

    @property
    def accepted(self) -> bool:
        return not self.rejection_reasons

    @property
    def has_black_bars(self) -> bool:
        return any("黑边" in reason for reason in self.rejection_reasons)

    @property
    def may_be_cropped(self) -> bool:
        return any("裁切" in reason for reason in self.rejection_reasons)


@dataclass(frozen=True)
class MatchCandidateEvidence:
    """One candidate retained for a specialized multi-evidence recognizer."""

    result: "MatchResult"
    scale: float
    interpolation: str = ""
    rejection_reasons: tuple[str, ...] = ()

    @property
    def metrics(self) -> dict[str, float]:
        return {
            "m": self.result.score,
            "p": self.result.pixel_score,
            "z": self.result.zncc_score,
            "gradient": self.result.gradient_zncc_score,
            "edge": self.result.edge_score,
        }

    @property
    def accepted(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True)
class MatchResult:
    score: float
    position: tuple[int, int]
    size: tuple[int, int]
    pixel_score: float = -1.0
    zncc_score: float = -1.0
    gradient_zncc_score: float = -1.0
    edge_score: float = -1.0
    scale: float = 1.0
    rejection_reasons: tuple[str, ...] = ()

    @property
    def center(self) -> tuple[int, int]:
        return (
            self.position[0] + self.size[0] // 2,
            self.position[1] + self.size[1] // 2,
        )

    @property
    def composite_evidence_score(self) -> float:
        """Return the finite evidence average used for candidate ranking."""
        values = (
            self.score,
            self.pixel_score,
            self.zncc_score,
            self.gradient_zncc_score,
            self.edge_score,
        )
        finite = [
            value
            for value in values
            if value > -1.0
            and isfinite(float(value))
        ]
        if not finite:
            return -1.0
        return sum(finite) / len(finite)


EMPTY_MATCH = MatchResult(-1.0, (0, 0), (0, 0))
