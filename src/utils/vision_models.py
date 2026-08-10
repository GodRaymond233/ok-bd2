from __future__ import annotations

from dataclasses import dataclass


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
class MatchResult:
    score: float
    position: tuple[int, int]
    size: tuple[int, int]
    pixel_score: float = -1.0
    zncc_score: float = -1.0

    @property
    def center(self) -> tuple[int, int]:
        return (
            self.position[0] + self.size[0] // 2,
            self.position[1] + self.size[1] // 2,
        )


EMPTY_MATCH = MatchResult(-1.0, (0, 0), (0, 0))
