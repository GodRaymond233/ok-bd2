from __future__ import annotations

import re
from dataclasses import replace
from math import ceil
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Iterable

import cv2
import numpy as np

from src.tasks.map_trade.models import (
    MAP_TRADE_REFERENCE,
    MatchResult,
    TemplateSpec,
)
from src.utils import task_vision
from src.utils.image_utils import (
    candidate_scales,
    independent_match_candidates,
    independent_pixel_valid_matches,
    pixel_similarity,
    relative_roi_frame,
    resize_mask,
    resize_template,
    resize_template_with_interpolation,
    scale_reference_roi,
    stabilize_template_match,
    template_match_response,
    to_gray,
)
from src.utils.template_resolution import (
    offline_template_scale,
    offline_template_search_region,
    offline_template_uses_main_region,
)
from src.utils.vision_models import FrameGeometry, MatchCandidateEvidence

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"
EMPTY_MATCH = MatchResult(-1.0, (0, 0), (0, 0))
COUNT_PATTERN = re.compile(r"(?<!\d)(\d+)\s*[/：:|\-~]\s*(\d+)(?!\d)")
# New evidence matching uses the FHD template calibration.  Keep
# ``reference_point``/``reference_roi`` on the historical HD calibration for
# callers that still pass their explicit 1280x720 reference coordinates.
FRAME_REFERENCE_WIDTH = 1920
FRAME_REFERENCE_HEIGHT = 1080
FRAME_EXPECTED_ASPECT = 16 / 9
FRAME_MINIMUM_WIDTH = 960
FRAME_MINIMUM_HEIGHT = 540
FRAME_ASPECT_TOLERANCE = 0.02
FRAME_DARK_BORDER_MAX_GRAY = 12
FRAME_DARK_BORDER_MAX_STD = 8.0
FRAME_DARK_BORDER_MIN_FRACTION = 0.995
FRAME_DARK_BORDER_MIN_PIXELS = 8
FRAME_DARK_BORDER_MIN_FRACTION_OF_AXIS = 0.01
# A compact replay fixture may intentionally retain only a few UI ROIs and
# leave the rest black.  Treating that sparse diagnostic background as
# letterboxing would suppress the recognizer before it can inspect the ROIs.
# Real black bars are only actionable when the frame also contains a
# substantial visible scene.
FRAME_DARK_BORDER_MIN_SCENE_FRACTION = 0.30


def normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff%]", "", str(value).lower())


def parse_used_limit(text: str) -> tuple[int, int] | None:
    match = COUNT_PATTERN.search(str(text))
    if match is None:
        return None
    used, limit = int(match.group(1)), int(match.group(2))
    if limit <= 0 or used < 0 or used > limit:
        return None
    return used, limit


class Vision:
    def __init__(self, task) -> None:
        self.task = task
        self._templates: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
        self._last_frame_geometry: FrameGeometry | None = None
        self._last_candidate_evidence: dict[str, tuple[MatchCandidateEvidence, ...]] = {}

    @staticmethod
    def _dark_border_run(gray: np.ndarray, *, from_start: bool) -> int:
        """Measure a contiguous uniform-dark border without mistaking scenery for it."""

        values = gray if from_start else gray[::-1]
        maximum = max(1, min(values.shape[0] // 4, 160))
        run = 0
        for index in range(maximum):
            strip = values[index]
            dark_fraction = float(np.mean(strip <= FRAME_DARK_BORDER_MAX_GRAY))
            if (
                dark_fraction < FRAME_DARK_BORDER_MIN_FRACTION
                or float(np.std(strip)) > FRAME_DARK_BORDER_MAX_STD
            ):
                break
            run += 1
        return run

    @staticmethod
    def _meaningful_border_run(run: int, axis_length: int) -> int:
        """Ignore a one-pixel capture seam while retaining real letterboxing."""

        minimum = max(
            FRAME_DARK_BORDER_MIN_PIXELS,
            ceil(max(1, int(axis_length)) * FRAME_DARK_BORDER_MIN_FRACTION_OF_AXIS),
        )
        return int(run) if int(run) >= minimum else 0

    @classmethod
    def evaluate_frame_geometry(
        cls,
        frame: np.ndarray,
        *,
        minimum_size: tuple[int, int] = (FRAME_MINIMUM_WIDTH, FRAME_MINIMUM_HEIGHT),
        required_relative_rois: tuple[tuple[float, float, float, float], ...] = (),
    ) -> FrameGeometry:
        """Assess aspect, effective content bounds, scale and unsafe frame geometry."""

        if not isinstance(frame, np.ndarray) or frame.ndim not in {2, 3}:
            raise ValueError(f"Unsupported frame shape: {getattr(frame, 'shape', None)}")
        gray = to_gray(frame)
        frame_height, frame_width = gray.shape[:2]
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError("Frame dimensions must be positive")

        scene_fraction = float(
            np.mean(gray > FRAME_DARK_BORDER_MAX_GRAY)
        )
        detect_borders = scene_fraction >= FRAME_DARK_BORDER_MIN_SCENE_FRACTION
        measured = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        if detect_borders:
            measured["top"] = cls._meaningful_border_run(
                cls._dark_border_run(gray, from_start=True),
                frame_height,
            )
            measured["bottom"] = cls._meaningful_border_run(
                cls._dark_border_run(gray, from_start=False),
                frame_height,
            )
            measured["left"] = cls._meaningful_border_run(
                cls._dark_border_run(gray.T, from_start=True),
                frame_width,
            )
            measured["right"] = cls._meaningful_border_run(
                cls._dark_border_run(gray.T, from_start=False),
                frame_width,
            )
        # Genuine letterboxing pulls the content region back toward 16:9.
        # A dark scene vignette (pure-black scenery touching the frame edge)
        # does not, so cutting it would corrupt every content-relative ROI.
        # Each measured run is therefore only accepted as a black bar when
        # cutting it moves the content aspect ratio closer to the target.
        content_left, content_top = 0, 0
        content_right, content_bottom = frame_width, frame_height
        top = bottom = left = right = 0
        for side in ("top", "bottom", "left", "right"):
            run = measured[side]
            if run <= 0:
                continue
            if side == "top":
                trial = (content_left, content_top + run, content_right, content_bottom)
            elif side == "bottom":
                trial = (content_left, content_top, content_right, content_bottom - run)
            elif side == "left":
                trial = (content_left + run, content_top, content_right, content_bottom)
            else:
                trial = (content_left, content_top, content_right - run, content_bottom)
            trial_width = max(1, trial[2] - trial[0])
            trial_height = max(1, trial[3] - trial[1])
            current_delta = abs(
                (content_right - content_left) / max(1, content_bottom - content_top)
                - FRAME_EXPECTED_ASPECT
            )
            trial_delta = abs(
                trial_width / trial_height - FRAME_EXPECTED_ASPECT
            )
            if trial_delta <= current_delta:
                content_left, content_top, content_right, content_bottom = trial
                if side == "top":
                    top = run
                elif side == "bottom":
                    bottom = run
                elif side == "left":
                    left = run
                else:
                    right = run
        content_width = max(1, content_right - content_left)
        content_height = max(1, content_bottom - content_top)

        aspect_ratio = frame_width / frame_height
        effective_aspect_ratio = content_width / content_height
        minimum_width, minimum_height = minimum_size
        reasons: list[str] = []
        if content_width < int(minimum_width) or content_height < int(minimum_height):
            reasons.append(
                f"画面物理尺寸过小：{content_width}x{content_height}，"
                f"最低{int(minimum_width)}x{int(minimum_height)}"
            )
        if abs(aspect_ratio - FRAME_EXPECTED_ASPECT) > FRAME_ASPECT_TOLERANCE:
            reasons.append(f"画面比例不是16:9：{aspect_ratio:.4f}")
        has_black_bars = any((top, bottom, left, right))
        if has_black_bars:
            reasons.append(
                "检测到黑边："
                f"content=({content_left},{content_top},{content_width},{content_height})"
            )
        if abs(effective_aspect_ratio - FRAME_EXPECTED_ASPECT) > FRAME_ASPECT_TOLERANCE:
            reasons.append(f"有效内容比例异常：{effective_aspect_ratio:.4f}")
        if not has_black_bars and (
            content_left
            or content_top
            or content_right != frame_width
            or content_bottom != frame_height
        ):
            reasons.append("画面边界疑似裁切")

        for roi in required_relative_rois:
            roi_left, roi_top, roi_right, roi_bottom = roi
            required_left = round(frame_width * max(0.0, min(1.0, roi_left)))
            required_top = round(frame_height * max(0.0, min(1.0, roi_top)))
            required_right = round(frame_width * max(0.0, min(1.0, roi_right)))
            required_bottom = round(frame_height * max(0.0, min(1.0, roi_bottom)))
            if (
                required_left < content_left
                or required_top < content_top
                or required_right > content_right
                or required_bottom > content_bottom
            ):
                reasons.append("目标槽位超出有效内容区域，画面疑似裁切")
                break

        return FrameGeometry(
            frame_width=frame_width,
            frame_height=frame_height,
            content_left=content_left,
            content_top=content_top,
            content_width=content_width,
            content_height=content_height,
            scale_x=content_width / FRAME_REFERENCE_WIDTH,
            scale_y=content_height / FRAME_REFERENCE_HEIGHT,
            client_scale=min(
                content_width / FRAME_REFERENCE_WIDTH,
                content_height / FRAME_REFERENCE_HEIGHT,
            ),
            aspect_ratio=aspect_ratio,
            effective_aspect_ratio=effective_aspect_ratio,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )

    def assess_frame(
        self,
        frame: np.ndarray,
        *,
        minimum_size: tuple[int, int] = (FRAME_MINIMUM_WIDTH, FRAME_MINIMUM_HEIGHT),
        required_relative_rois: tuple[tuple[float, float, float, float], ...] = (),
        purpose: str = "画面",
    ) -> FrameGeometry:
        geometry = self.evaluate_frame_geometry(
            frame,
            minimum_size=minimum_size,
            required_relative_rois=required_relative_rois,
        )
        self._last_frame_geometry = geometry
        geometry_status = (
            "通过"
            if geometry.accepted
            else "拒绝：" + "|".join(geometry.rejection_reasons)
        )
        self._status(
            f"{purpose}几何",
            (
                f"frame={geometry.frame_width}x{geometry.frame_height}; "
                f"content={geometry.content_rect}; "
                f"scale=({geometry.scale_x:.4f},{geometry.scale_y:.4f}); "
                f"aspect={geometry.aspect_ratio:.4f}; "
                f"{geometry_status}"
            ),
        )
        return geometry

    @property
    def last_frame_geometry(self) -> FrameGeometry | None:
        return self._last_frame_geometry

    @property
    def last_candidate_evidence(self) -> dict[str, tuple[MatchCandidateEvidence, ...]]:
        return {name: tuple(values) for name, values in self._last_candidate_evidence.items()}

    @staticmethod
    def reference_point(x: float, y: float, width: int, height: int) -> tuple[int, int]:
        return (
            round(width * x / MAP_TRADE_REFERENCE.width),
            round(height * y / MAP_TRADE_REFERENCE.height),
        )

    @staticmethod
    def reference_roi(
        roi: tuple[int, int, int, int], width: int, height: int
    ) -> tuple[int, int, int, int]:
        left, top, roi_width, roi_height = scale_reference_roi(
            roi,
            (width, height),
            MAP_TRADE_REFERENCE.size,
        )
        return left, top, max(1, roi_width), max(1, roi_height)

    def capture(self):
        return self.task.capture_frame()

    def threshold_for(self, spec: TemplateSpec) -> float:
        key = getattr(self.task, "vision_threshold_key", "跑图跑商识图阈值")
        try:
            value = float(
                self.task.config.get(
                    key,
                    self.task.config.get("跑图跑商识图阈值", spec.threshold),
                )
            )
        except (TypeError, ValueError):
            return spec.threshold
        value = max(0.05, min(0.99, value))
        if spec.minimum_safe_threshold is not None:
            value = max(value, spec.minimum_safe_threshold)
        return value

    def click_reference(self, x: float, y: float, after_sleep: float = 0.0) -> None:
        self.task.operate_click(
            max(0.0, min(1.0, x / MAP_TRADE_REFERENCE.width)),
            max(0.0, min(1.0, y / MAP_TRADE_REFERENCE.height)),
            after_sleep=after_sleep,
        )

    def click_client(
        self, point: tuple[int, int], frame_shape: tuple[int, ...], after_sleep: float = 0.0
    ) -> None:
        height, width = frame_shape[:2]
        self.task.operate_click(
            max(0.0, min(1.0, point[0] / max(1, width))),
            max(0.0, min(1.0, point[1] / max(1, height))),
            after_sleep=after_sleep,
        )

    def drag_reference(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        frame = self.capture()
        height, width = frame.shape[:2]
        self.task.drag_client(
            self.reference_point(*start, width, height),
            self.reference_point(*end, width, height),
            duration=duration,
            after_sleep=after_sleep,
        )

    def _load(self, spec: TemplateSpec) -> tuple[np.ndarray, np.ndarray | None]:
        return task_vision.load_template(TEMPLATE_DIR, spec, cache=self._templates)

    _gray = staticmethod(to_gray)

    @staticmethod
    def _candidate_scales(
        base_scale: float, scale_ratios: tuple[float, ...] = (1.0,)
    ) -> tuple[float, ...]:
        return tuple(candidate_scales(base_scale, scale_ratios))

    _relative_roi = staticmethod(relative_roi_frame)

    @staticmethod
    def bright_neutral_ratio(
        frame: np.ndarray,
        relative_roi: tuple[float, float, float, float],
        minimum_gray: int = 170,
        maximum_channel_spread: int = 35,
    ) -> float:
        """Measure white/gray highlight pixels inside a relative client region."""

        _left, _top, region = Vision._relative_roi(frame, relative_roi)
        if region.size == 0:
            return 0.0
        if region.ndim == 2:
            return float(np.mean(region >= minimum_gray))
        color = region[..., :3].astype(np.int16)
        channel_min = np.min(color, axis=2)
        channel_spread = np.max(color, axis=2) - channel_min
        highlighted = (channel_min >= minimum_gray) & (
            channel_spread <= maximum_channel_spread
        )
        return float(np.mean(highlighted))

    _pixel_similarity = staticmethod(pixel_similarity)
    _resize_template = staticmethod(resize_template)
    _resize_mask = staticmethod(resize_mask)

    @staticmethod
    def _candidate_rank(result: MatchResult) -> tuple[float, ...]:
        """Rank retained candidates without allowing one metric to dominate."""

        values = (
            result.score,
            result.zncc_score,
            result.gradient_zncc_score,
            result.edge_score,
            result.pixel_score,
        )
        finite = [value for value in values if np.isfinite(value) and value > -1.0]
        composite = sum(finite) / len(finite) if finite else -1.0
        return (
            composite,
            result.score,
            result.gradient_zncc_score,
            result.zncc_score,
            result.edge_score,
            result.pixel_score,
        )

    @staticmethod
    def _interpolation_modes(scale: float) -> tuple[tuple[int, str], ...]:
        """Return a small deterministic interpolation set for one template scale."""

        if abs(float(scale) - 1.0) < 0.001:
            return ((cv2.INTER_NEAREST, "native"),)
        if scale < 1.0:
            return (
                (cv2.INTER_AREA, "area"),
                (cv2.INTER_LINEAR, "linear"),
                (cv2.INTER_CUBIC, "cubic"),
            )
        return (
            (cv2.INTER_LINEAR, "linear"),
            (cv2.INTER_CUBIC, "cubic"),
        )

    def _matching_region(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        *,
        search_roi: tuple[int, int, int, int] | None = None,
        gray_frame: np.ndarray | None = None,
        geometry: FrameGeometry | None = None,
    ) -> tuple[int, int, np.ndarray, int, int]:
        gray = gray_frame if gray_frame is not None else self._gray(frame)
        frame_height, frame_width = gray.shape[:2]
        left = top = 0
        search = gray
        if search_roi is not None:
            roi_x, roi_y, roi_width, roi_height = search_roi
            left = max(0, min(frame_width, int(roi_x)))
            top = max(0, min(frame_height, int(roi_y)))
            right = max(left, min(frame_width, left + max(0, int(roi_width))))
            bottom = max(top, min(frame_height, top + max(0, int(roi_height))))
            search = gray[top:bottom, left:right]
        elif geometry is not None:
            content_left = max(0, min(frame_width, geometry.content_left))
            content_top = max(0, min(frame_height, geometry.content_top))
            content_right = max(
                content_left,
                min(frame_width, content_left + geometry.content_width),
            )
            content_bottom = max(
                content_top,
                min(frame_height, content_top + geometry.content_height),
            )
            content = gray[content_top:content_bottom, content_left:content_right]
            if offline_template_uses_main_region(spec.file_name):
                region_left, region_top, region_right, region_bottom = (
                    offline_template_search_region(
                        spec.file_name,
                        max(1, geometry.content_width),
                        max(1, geometry.content_height),
                    )
                )
                left = content_left + region_left
                top = content_top + region_top
                search = gray[top : content_top + region_bottom, left : content_left + region_right]
            elif spec.relative_roi is not None:
                roi_left, roi_top, roi = self._relative_roi(content, spec.relative_roi)
                left = content_left + roi_left
                top = content_top + roi_top
                search = roi
            elif spec.roi is not None:
                roi_left, roi_top, roi_width, roi_height = self.reference_roi(
                    spec.roi,
                    max(1, geometry.content_width),
                    max(1, geometry.content_height),
                )
                left = content_left + roi_left
                top = content_top + roi_top
                search = gray[top : top + roi_height, left : left + roi_width]
            else:
                left, top, search = content_left, content_top, content
        elif offline_template_uses_main_region(spec.file_name):
            left, top, right, bottom = offline_template_search_region(
                spec.file_name,
                frame_width,
                frame_height,
            )
            search = gray[top:bottom, left:right]
        elif spec.relative_roi is not None:
            left, top, search = self._relative_roi(gray, spec.relative_roi)
        elif spec.roi is not None:
            left, top, width, height = self.reference_roi(
                spec.roi,
                frame_width,
                frame_height,
            )
            search = gray[top : top + height, left : left + width]
        return left, top, search, frame_width, frame_height

    @staticmethod
    def _center_bounds(
        spec: TemplateSpec,
        *,
        frame_width: int,
        frame_height: int,
        left: int,
        top: int,
        geometry: FrameGeometry | None = None,
    ) -> tuple[int, int, int, int] | None:
        if spec.candidate_center_roi is None:
            return None
        center_left, center_top, center_right, center_bottom = spec.candidate_center_roi
        coordinate_width = geometry.content_width if geometry is not None else frame_width
        coordinate_height = geometry.content_height if geometry is not None else frame_height
        coordinate_left = geometry.content_left if geometry is not None else 0
        coordinate_top = geometry.content_top if geometry is not None else 0
        return (
            coordinate_left + round(coordinate_width * center_left) - left,
            coordinate_top + round(coordinate_height * center_top) - top,
            coordinate_left + round(coordinate_width * center_right) - left,
            coordinate_top + round(coordinate_height * center_bottom) - top,
        )

    def match_evidence_all(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        *,
        minimum_score: float = 0.35,
        peak_radius: int = 5,
        max_results: int = 30,
        search_roi: tuple[int, int, int, int] | None = None,
        gray_frame: np.ndarray | None = None,
        geometry: FrameGeometry | None = None,
        purpose: str = "专用识别",
    ) -> tuple[MatchCandidateEvidence, ...]:
        """Collect response peaks before applying pixel or ZNCC identity gates."""

        if geometry is None:
            required = (spec.relative_roi,) if spec.relative_roi is not None else ()
            geometry = self.assess_frame(
                frame,
                required_relative_rois=required,
                purpose=purpose,
            )
        if not geometry.accepted:
            self._last_candidate_evidence[spec.name] = ()
            return ()

        template, mask = self._load(spec)
        left, top, search, frame_width, frame_height = self._matching_region(
            frame,
            spec,
            search_roi=search_roi,
            gray_frame=gray_frame,
            geometry=geometry,
        )
        if search.size == 0:
            self._last_candidate_evidence[spec.name] = ()
            return ()
        center_bounds = self._center_bounds(
            spec,
            frame_width=frame_width,
            frame_height=frame_height,
            left=left,
            top=top,
            geometry=geometry,
        )
        base_scale = offline_template_scale(
            spec.file_name,
            geometry.content_width if geometry is not None else frame_width,
            geometry.content_height if geometry is not None else frame_height,
            reference_scale=spec.reference_scale,
        )
        peak_radius = max(1, int(peak_radius))
        limit = max(1, int(max_results))
        candidates: list[MatchCandidateEvidence] = []
        for scale in self._candidate_scales(base_scale, spec.scale_ratios):
            for interpolation, interpolation_name in self._interpolation_modes(scale):
                scaled = resize_template_with_interpolation(template, scale, interpolation)
                scaled_mask = resize_mask(mask, scale)
                scaled_height, scaled_width = scaled.shape[:2]
                if (
                    scaled_height < 4
                    or scaled_width < 4
                    or scaled_height > search.shape[0]
                    or scaled_width > search.shape[1]
                ):
                    continue
                try:
                    response = template_match_response(search, scaled, scaled_mask)
                except cv2.error:
                    continue
                raw_candidates = independent_match_candidates(
                    response,
                    search,
                    scaled,
                    scaled_mask,
                    template_threshold=max(-1.0, min(1.0, float(minimum_score))),
                    center_bounds=center_bounds,
                    suppression_radius=peak_radius,
                    max_matches=limit,
                )
                for candidate in raw_candidates:
                    result = MatchResult(
                        score=candidate.score,
                        position=(left + candidate.location[0], top + candidate.location[1]),
                        size=(scaled_width, scaled_height),
                        pixel_score=candidate.pixel_score,
                        zncc_score=candidate.zncc_score,
                        gradient_zncc_score=candidate.gradient_zncc_score,
                        edge_score=candidate.edge_score,
                        scale=float(scale),
                    )
                    candidates.append(
                        MatchCandidateEvidence(
                            result=result,
                            scale=float(scale),
                            interpolation=interpolation_name,
                        )
                    )

        independent: list[MatchCandidateEvidence] = []
        for candidate in sorted(
            candidates,
            key=lambda value: self._candidate_rank(value.result),
            reverse=True,
        ):
            if any(
                (candidate.result.center[0] - kept.result.center[0]) ** 2
                + (candidate.result.center[1] - kept.result.center[1]) ** 2
                <= peak_radius**2
                for kept in independent
            ):
                continue
            independent.append(candidate)
            if len(independent) >= limit:
                break
        retained = tuple(independent)
        self._last_candidate_evidence[spec.name] = retained
        summary = (
            "; ".join(
                (
                    f"s={value.scale:.3f}/{value.interpolation or '-'}"
                    f"@{value.result.position}:"
                    f"m={value.result.score:.3f},p={value.result.pixel_score:.3f},"
                    f"z={value.result.zncc_score:.3f},"
                    f"g={value.result.gradient_zncc_score:.3f},"
                    f"e={value.result.edge_score:.3f}"
                )
                for value in retained[:8]
            )
            if retained
            else "-"
        )
        self._status(f"{purpose}候选证据", f"{spec.name}: {summary}")
        return retained

    def match_slot_evidence(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        center: tuple[int, int],
        *,
        radius: tuple[int, int] | int,
        geometry: FrameGeometry | None = None,
        minimum_score: float = 0.35,
        max_results: int = 12,
        purpose: str = "固定槽位",
    ) -> tuple[MatchCandidateEvidence, ...]:
        """Search a compact client-space rectangle around one calibrated slot."""

        if isinstance(radius, tuple):
            radius_x, radius_y = radius
        else:
            radius_x = radius_y = radius
        radius_x = max(2, int(radius_x))
        radius_y = max(2, int(radius_y))
        return self.match_evidence_all(
            frame,
            replace(spec, candidate_center_roi=None),
            minimum_score=minimum_score,
            peak_radius=max(1, min(radius_x, radius_y) // 3),
            max_results=max_results,
            search_roi=(
                int(center[0] - radius_x),
                int(center[1] - radius_y),
                int(radius_x * 2 + 1),
                int(radius_y * 2 + 1),
            ),
            geometry=geometry,
            purpose=purpose,
        )

    def match(self, frame: np.ndarray, spec: TemplateSpec) -> MatchResult:
        return task_vision.match_template(
            frame,
            spec,
            self.task.config,
            TEMPLATE_DIR,
            cache=self._templates,
            min_size=4,
            skip_scale_errors=True,
            template_threshold=self.threshold_for(spec),
            roi_reference_size=MAP_TRADE_REFERENCE.size,
            loader=lambda _template_dir, spec: self._load(spec),
        )

    def match_all(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        minimum_score: float,
        peak_radius: int = 5,
        max_results: int = 60,
        search_roi: tuple[int, int, int, int] | None = None,
        gray_frame: np.ndarray | None = None,
    ) -> tuple[MatchResult, ...]:
        """Return independent template peaks in full-client coordinates.

        ``search_roi`` is an optional x/y/width/height crop supplied by a
        higher-level recognizer. Template scaling still uses the full frame,
        while returned positions remain full-frame coordinates. ``gray_frame``
        lets one polling pass reuse a single full-frame conversion across ROIs.
        """

        template, mask = self._load(spec)
        gray = gray_frame if gray_frame is not None else self._gray(frame)
        frame_height, frame_width = gray.shape[:2]
        left = top = 0
        search = gray
        if search_roi is not None:
            roi_x, roi_y, roi_width, roi_height = search_roi
            roi_right = int(roi_x) + max(0, int(roi_width))
            roi_bottom = int(roi_y) + max(0, int(roi_height))
            left = max(0, min(frame_width, int(roi_x)))
            top = max(0, min(frame_height, int(roi_y)))
            right = max(left, min(frame_width, roi_right))
            bottom = max(top, min(frame_height, roi_bottom))
            search = gray[top:bottom, left:right]
        elif offline_template_uses_main_region(spec.file_name):
            left, top, right, bottom = offline_template_search_region(
                spec.file_name,
                frame_width,
                frame_height,
            )
            search = gray[top:bottom, left:right]
        elif spec.relative_roi is not None:
            left, top, search = self._relative_roi(gray, spec.relative_roi)
        elif spec.roi is not None:
            left, top, width, height = self.reference_roi(
                spec.roi,
                frame_width,
                frame_height,
            )
            search = gray[top : top + height, left : left + width]
        if search.size == 0:
            return ()

        radius = max(1, int(peak_radius))
        limit = max(1, int(max_results))
        score_floor = max(-1.0, min(1.0, float(minimum_score)))
        center_bounds = None
        if spec.candidate_center_roi is not None:
            center_left, center_top, center_right, center_bottom = (
                spec.candidate_center_roi
            )
            center_bounds = (
                round(frame_width * center_left) - left,
                round(frame_height * center_top) - top,
                round(frame_width * center_right) - left,
                round(frame_height * center_bottom) - top,
            )
        candidates: list[MatchResult] = []
        base_scale = offline_template_scale(
            spec.file_name,
            frame_width,
            frame_height,
            reference_scale=spec.reference_scale,
        )
        for scale in self._candidate_scales(base_scale, spec.scale_ratios):
            scaled = self._resize_template(template, scale)
            scaled_mask = self._resize_mask(mask, scale)
            height, width = scaled.shape[:2]
            if height < 4 or width < 4 or height > search.shape[0] or width > search.shape[1]:
                continue
            try:
                response = template_match_response(search, scaled, scaled_mask)
            except cv2.error:
                continue
            scale_candidates = independent_pixel_valid_matches(
                response,
                search,
                scaled,
                scaled_mask,
                template_threshold=score_floor,
                pixel_threshold=(spec.min_pixel_score or 0.0),
                zncc_threshold=spec.min_zncc_score,
                center_bounds=center_bounds,
                suppression_radius=radius,
                max_matches=limit,
            )
            for candidate in scale_candidates:
                x, y = candidate.location
                candidates.append(
                    MatchResult(
                        score=candidate.score,
                        position=(left + x, top + y),
                        size=(width, height),
                        pixel_score=candidate.pixel_score,
                        zncc_score=float(getattr(candidate, "zncc_score", -1.0)),
                    )
                )

        independent: list[MatchResult] = []
        for candidate in sorted(candidates, key=lambda value: value.score, reverse=True):
            if any(
                (candidate.center[0] - kept.center[0]) ** 2
                + (candidate.center[1] - kept.center[1]) ** 2
                <= radius**2
                for kept in independent
            ):
                continue
            independent.append(candidate)
            if len(independent) >= limit:
                break
        return tuple(independent)

    def passes(self, result: MatchResult, spec: TemplateSpec) -> bool:
        return task_vision.passes_match(
            result,
            spec,
            self.task.config,
            threshold=self.threshold_for(spec),
        )

    def template_color_ratios(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        result: MatchResult,
    ) -> tuple[float, float, float] | None:
        """Measure green, red, and neutral pixels under a template mask."""

        left, top = result.position
        width, height = result.size
        right = left + width
        bottom = top + height
        if (
            width <= 0
            or height <= 0
            or left < 0
            or top < 0
            or right > frame.shape[1]
            or bottom > frame.shape[0]
        ):
            return None
        crop = frame[top:bottom, left:right]
        if crop.ndim == 2:
            color = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        elif crop.shape[2] == 4:
            color = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
        else:
            color = crop[:, :, :3]

        _template, mask = self._load(spec)
        if mask is None:
            active = np.ones((height, width), dtype=bool)
        else:
            active = cv2.resize(
                mask,
                (width, height),
                interpolation=cv2.INTER_NEAREST,
            ) > 0
        if not np.any(active):
            return None

        pixels = color[active].astype(np.int16)
        blue, green, red = pixels.T
        green_pixels = (
            (green - np.maximum(blue, red) >= 8)
            & (green >= 60)
        )
        red_pixels = (
            (red - np.maximum(blue, green) >= 8)
            & (red >= 60)
        )
        neutral_pixels = (
            np.max(pixels, axis=1) - np.min(pixels, axis=1) <= 10
        )
        return (
            float(np.mean(green_pixels)),
            float(np.mean(red_pixels)),
            float(np.mean(neutral_pixels)),
        )

    def template_hsv_color_ratios(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        result: MatchResult,
    ) -> tuple[float, float, float] | None:
        """Measure yellow, neutral, and bright pixels under a match mask."""

        left, top = result.position
        width, height = result.size
        right = left + width
        bottom = top + height
        if (
            width <= 0
            or height <= 0
            or left < 0
            or top < 0
            or right > frame.shape[1]
            or bottom > frame.shape[0]
        ):
            return None
        crop = frame[top:bottom, left:right]
        if crop.ndim == 2:
            color = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        elif crop.shape[2] == 4:
            color = cv2.cvtColor(crop, cv2.COLOR_BGRA2BGR)
        else:
            color = crop[:, :, :3]

        _template, mask = self._load(spec)
        if mask is None:
            active = np.ones((height, width), dtype=bool)
        else:
            active = cv2.resize(
                mask,
                (width, height),
                interpolation=cv2.INTER_NEAREST,
            ) > 0
        if not np.any(active):
            return None

        hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
        hue, saturation, value = hsv[active].T
        yellow = (
            (hue >= 8)
            & (hue <= 38)
            & (saturation >= 60)
            & (value >= 75)
        )
        neutral = (saturation <= 55) & (value >= 50)
        bright = value >= 130
        return (
            float(np.mean(yellow)),
            float(np.mean(neutral)),
            float(np.mean(bright)),
        )

    def template_brightness_ratio(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        result: MatchResult,
        minimum_template_gray: int = 0,
    ) -> float:
        template, mask = self._load(spec)
        width, height = result.size
        if width <= 0 or height <= 0:
            return 0.0
        scaled = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
        scaled_mask = (
            cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
            if mask is not None
            else None
        )
        x, y = result.position
        gray = self._gray(frame)
        region = gray[max(0, y) : y + height, max(0, x) : x + width]
        if region.shape != scaled.shape:
            return 0.0
        return self.foreground_brightness_ratio(
            scaled,
            region,
            minimum_reference_gray=minimum_template_gray,
            mask=scaled_mask,
        )

    @classmethod
    def foreground_brightness_ratio(
        cls,
        reference: np.ndarray,
        sample: np.ndarray,
        minimum_reference_gray: int = 0,
        mask: np.ndarray | None = None,
    ) -> float:
        reference_gray = cls._gray(reference)
        sample_gray = cls._gray(sample)
        if reference_gray.shape != sample_gray.shape or reference_gray.size == 0:
            return 0.0
        active = reference_gray >= max(0, min(255, int(minimum_reference_gray)))
        if mask is not None:
            if mask.shape != reference_gray.shape:
                return 0.0
            active &= mask > 0
        if not np.any(active):
            return 0.0
        template_mean = float(np.mean(reference_gray[active]))
        region_mean = float(np.mean(sample_gray[active]))
        return region_mean / template_mean if template_mean > 0 else 0.0

    def find_all(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        threshold: float | None = None,
        max_results: int = 30,
    ) -> list[MatchResult]:
        template, mask = self._load(spec)
        gray = self._gray(frame)
        frame_height, frame_width = gray.shape[:2]
        left = top = 0
        search = gray
        if spec.roi is not None:
            left, top, width, height = self.reference_roi(spec.roi, frame_width, frame_height)
            search = gray[top : top + height, left : left + width]
        scale = offline_template_scale(
            spec.file_name,
            frame_width,
            frame_height,
            reference_scale=spec.reference_scale,
        )
        scaled = self._resize_template(template, scale)
        scaled_mask = self._resize_mask(mask, scale)
        height, width = scaled.shape[:2]
        if search.size == 0 or height > search.shape[0] or width > search.shape[1]:
            return []
        result = template_match_response(search, scaled, scaled_mask)
        wanted = self.threshold_for(spec) if threshold is None else threshold
        candidates = independent_pixel_valid_matches(
            result,
            search,
            scaled,
            scaled_mask,
            template_threshold=wanted,
            pixel_threshold=(spec.min_pixel_score or 0.0),
            zncc_threshold=spec.min_zncc_score,
            suppression_radius=(round(width * 0.65), round(height * 0.65)),
            max_matches=max_results,
        )
        matches = [
            MatchResult(
                candidate.score,
                (left + candidate.location[0], top + candidate.location[1]),
                (width, height),
                pixel_score=candidate.pixel_score,
                zncc_score=float(getattr(candidate, "zncc_score", -1.0)),
            )
            for candidate in candidates
        ]
        return matches

    def wait_template(
        self, spec: TemplateSpec, timeout: float, interval: float = 0.4
    ) -> MatchResult | None:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.capture()
            result = self.match(frame, spec)
            self._status(spec.name, f"{result.score:.3f}/{result.pixel_score:.3f}")
            if self.passes(result, spec):
                return result
            self.task.sleep(interval)
        return None

    def click_template(
        self,
        spec: TemplateSpec,
        timeout: float = 2.0,
        after_sleep: float = 0.8,
    ) -> bool:
        match = self.wait_template(spec, timeout)
        if match is None:
            return False
        self._status(
            f"{spec.name}点击中心",
            (
                f"center=({match.center[0]},{match.center[1]}), "
                f"match={match.score:.3f}, pixel={match.pixel_score:.3f}"
            ),
        )
        frame = self.capture()
        self.click_client(match.center, frame.shape, after_sleep=after_sleep)
        return True

    def click_stable_template(
        self,
        spec: TemplateSpec,
        timeout: float = 2.0,
        after_sleep: float = 0.8,
    ) -> bool:
        """Click a template only after its center stabilizes across about one second."""

        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.capture()
            result = self.match(frame, spec)
            if not self.passes(result, spec):
                self._status(spec.name, f"{result.score:.3f}/{result.pixel_score:.3f}")
                self.task.sleep(0.35)
                continue

            def sample_match():
                sampled_frame = self.capture()
                return self.match(sampled_frame, spec), sampled_frame.shape

            stabilized = stabilize_template_match(
                result,
                frame.shape,
                sample_match=sample_match,
                passes=lambda candidate: self.passes(candidate, spec),
                sleep=self.task.sleep,
                on_sample=lambda candidate: self._status(
                    spec.name,
                    f"{candidate.score:.3f}/{candidate.pixel_score:.3f}",
                ),
            )
            if stabilized is None:
                self._status(f"{spec.name}稳定识别", "未形成稳定位置")
                return False
            consensus, frame_shape = stabilized
            self._status(
                f"{spec.name}稳定识别",
                (
                    f"center=({consensus.center[0]},{consensus.center[1]}), "
                    f"hits={consensus.hit_count}/{consensus.sample_count}, "
                    f"match={consensus.average_score:.3f}, "
                    f"pixel={consensus.average_pixel_score:.3f}, "
                    f"spread={consensus.center_spread:.1f}"
                ),
            )
            self.click_client(consensus.center, frame_shape, after_sleep=after_sleep)
            return True
        return False

    def ocr_boxes(
        self,
        frame: np.ndarray,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        relative_roi: tuple[float, float, float, float] | None = None,
        target_height: int = 720,
        minimum_threshold: float | None = None,
        ocr_scale: float = 1.0,
    ) -> list:
        offset_x = offset_y = 0
        target = frame
        if roi is not None and relative_roi is not None:
            raise ValueError("roi and relative_roi cannot be used together")
        if relative_roi is not None:
            offset_x, offset_y, target = self._relative_roi(frame, relative_roi)
        elif roi is not None:
            height, width = frame.shape[:2]
            offset_x, offset_y, roi_width, roi_height = self.reference_roi(roi, width, height)
            target = frame[offset_y : offset_y + roi_height, offset_x : offset_x + roi_width]
        if target.size == 0:
            height, width = frame.shape[:2]
            region = relative_roi if relative_roi is not None else roi
            self._status(
                f"{name} OCR错误",
                f"识别区域超出画面：roi={region}, frame={width}x{height}",
            )
            return []
        if ocr_scale <= 0:
            raise ValueError("ocr_scale must be positive")
        if ocr_scale != 1.0:
            target = cv2.resize(
                target,
                None,
                fx=ocr_scale,
                fy=ocr_scale,
                interpolation=cv2.INTER_CUBIC,
            )
        try:
            key = getattr(self.task, "ocr_threshold_key", "跑图跑商 OCR 阈值")
            configured_threshold = float(
                self.task.config.get(
                    key,
                    self.task.config.get("跑图跑商 OCR 阈值", 0.2),
                )
            )
            if minimum_threshold is not None:
                configured_threshold = max(
                    configured_threshold,
                    float(minimum_threshold),
                )
            boxes = self.task.ocr(
                frame=target,
                threshold=configured_threshold,
                target_height=max(0, int(target_height)),
                log=False,
                name=name,
            )
        except Exception as exc:
            self._status(f"{name} OCR错误", str(exc))
            return []
        if not offset_x and not offset_y and ocr_scale == 1.0:
            return list(boxes)
        adjusted = []
        for box in boxes:
            values = {
                "name": getattr(box, "name", ""),
                "confidence": getattr(box, "confidence", getattr(box, "score", None)),
                "x": getattr(box, "x", None),
                "y": getattr(box, "y", None),
                "width": getattr(box, "width", None),
                "height": getattr(box, "height", None),
            }
            raw_box = getattr(box, "box", None)
            if any(values[key] is None for key in ("x", "y", "width", "height")):
                if raw_box is not None and len(raw_box) >= 4:
                    values["x"], values["y"], values["width"], values["height"] = raw_box[:4]
            if values["x"] is not None:
                values["x"] = float(values["x"]) / ocr_scale + offset_x
            if values["y"] is not None:
                values["y"] = float(values["y"]) / ocr_scale + offset_y
            if values["width"] is not None:
                values["width"] = float(values["width"]) / ocr_scale
            if values["height"] is not None:
                values["height"] = float(values["height"]) / ocr_scale
            if all(values[key] is not None for key in ("x", "y", "width", "height")):
                values["box"] = (
                    values["x"],
                    values["y"],
                    values["width"],
                    values["height"],
                )
            adjusted.append(SimpleNamespace(**values))
        return adjusted

    def ocr_text(
        self,
        frame: np.ndarray,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        relative_roi: tuple[float, float, float, float] | None = None,
        target_height: int = 720,
        minimum_threshold: float | None = None,
        ocr_scale: float = 1.0,
    ) -> str:
        values = [
            str(getattr(box, "name", ""))
            for box in self.ocr_boxes(
                frame,
                name,
                roi,
                relative_roi=relative_roi,
                target_height=target_height,
                minimum_threshold=minimum_threshold,
                ocr_scale=ocr_scale,
            )
        ]
        text = " ".join(value for value in values if value)
        self._status(f"{name} OCR", text or "-")
        return text

    def wait_ocr(
        self,
        patterns: Iterable[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> str | None:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            text = self.ocr_text(self.capture(), name, roi)
            normalized = self.simplify(text)
            if any(pattern.search(normalized) for pattern in compiled):
                return text
            self.task.sleep(interval)
        return None

    def click_ocr(
        self,
        patterns: Iterable[str],
        roi: tuple[int, int, int, int] | None = None,
        after_sleep: float = 0.8,
        name: str = "跑图跑商",
    ) -> bool:
        frame = self.capture()
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        for box in self.ocr_boxes(frame, name, roi):
            text = self.simplify(str(getattr(box, "name", "")))
            if not any(pattern.search(text) for pattern in compiled):
                continue
            attrs = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
            if any(value is None for value in attrs):
                continue
            x, y, width, height = (float(value) for value in attrs)
            self.click_client(
                (round(x + width / 2), round(y + height / 2)), frame.shape, after_sleep
            )
            return True
        return False

    def simplify(self, text: str) -> str:
        # OCR 只识别简体中文（2026-08-29 取消繁体识别）：不再做繁转简转换，
        # 仅保留 str 归一以稳定既有调用面（测试桩同为恒等）。
        return str(text)

    @staticmethod
    def star_is_yellow(image: np.ndarray, match: MatchResult) -> bool:
        x, y = match.position
        width, height = match.size
        crop = image[max(0, y) : y + height, max(0, x) : x + width]
        if crop.size == 0:
            return False
        if crop.shape[2] == 4:
            crop = crop[:, :, :3]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        saturation_ratio = float(np.count_nonzero(hsv[:, :, 1] > 77)) / hsv[:, :, 1].size
        return saturation_ratio >= 0.15

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
