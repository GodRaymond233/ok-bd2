from __future__ import annotations

from typing import Callable

import cv2
import numpy as np

from src.utils import image_utils, template_resolution
from src.utils.vision_models import EMPTY_MATCH, MatchResult, TemplateSpec


def resolve_match_threshold(
    spec: TemplateSpec,
    config,
    *,
    for_matching: bool = False,
) -> float:
    """Resolve the configured threshold for one template specification."""

    if for_matching and spec.candidate_threshold is not None:
        threshold = spec.candidate_threshold
    elif spec.threshold_key is not None:
        fallback = (
            spec.default_threshold
            if spec.default_threshold is not None
            else spec.threshold
        )
        threshold = float(config.get(spec.threshold_key, fallback))
    else:
        threshold = spec.threshold
    if spec.minimum_safe_threshold is not None:
        threshold = max(threshold, spec.minimum_safe_threshold)
    return threshold


def load_template(
    template_dir,
    spec: TemplateSpec,
    cache: dict | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Load one gray template plus mask, honoring crop and green/alpha masks."""

    if cache is not None and spec.name in cache:
        return cache[spec.name]

    path = template_dir / spec.file_name
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise RuntimeError(f"任务模板不存在或无法读取：{path}")
    if spec.crop is not None:
        raw = image_utils.crop_relative(raw, spec.crop)

    use_green_mask = spec.green_mask or template_resolution.offline_template_requires_green_mask(
        spec.file_name
    )
    if use_green_mask:
        mask = image_utils.green_mask_from_template(raw)
    elif raw.ndim == 3 and raw.shape[2] >= 4:
        mask = np.where(raw[:, :, 3] > 0, 255, 0).astype(np.uint8)
    else:
        mask = None
    gray = image_utils.to_gray(raw)
    if mask is not None and np.count_nonzero(mask) == mask.size:
        mask = None

    loaded = (gray, mask)
    if cache is not None:
        cache[spec.name] = loaded
    return loaded


def _search_region(
    spec: TemplateSpec,
    frame: np.ndarray,
    roi_reference_size: tuple[int, int] | None,
) -> tuple[int, int, np.ndarray]:
    """Return the matching crop and its top-left offset for one spec."""

    gray = image_utils.to_gray(frame)
    frame_height, frame_width = gray.shape[:2]
    if spec.relative_roi is not None:
        return image_utils.relative_roi_frame(gray, spec.relative_roi)
    if (
        spec.roi is not None
        and not template_resolution.offline_template_uses_main_region(spec.file_name)
    ):
        return image_utils.reference_roi_frame(
            gray,
            spec.roi,
            roi_reference_size
            or template_resolution.OFFLINE_TEMPLATE_REFERENCE_RESOLUTIONS["root"],
        )
    left, top, right, bottom = template_resolution.offline_template_search_region(
        spec.file_name,
        frame_width,
        frame_height,
    )
    return left, top, gray[top:bottom, left:right]


def match_template(
    frame: np.ndarray,
    spec: TemplateSpec,
    config,
    template_dir,
    cache: dict | None = None,
    *,
    min_size: int = 8,
    skip_scale_errors: bool = False,
    template_threshold: float | None = None,
    roi_reference_size: tuple[int, int] | None = None,
    loader: Callable[[object, TemplateSpec], tuple[np.ndarray, np.ndarray | None]]
    | None = None,
) -> MatchResult:
    """Match one template against a frame using the shared pipeline."""

    if loader is None:
        loader = lambda _template_dir, spec: load_template(  # noqa: E731
            _template_dir,
            spec,
            cache=cache,
        )
    template, mask = loader(template_dir, spec)
    left, top, search = _search_region(spec, frame, roi_reference_size)
    if search.size == 0:
        return EMPTY_MATCH

    frame_height, frame_width = image_utils.to_gray(frame).shape[:2]
    if template_threshold is None:
        template_threshold = resolve_match_threshold(spec, config, for_matching=True)
    center_bounds = None
    if spec.candidate_center_roi is not None:
        center_left, center_top, center_right, center_bottom = spec.candidate_center_roi
        center_bounds = (
            round(frame_width * center_left) - left,
            round(frame_height * center_top) - top,
            round(frame_width * center_right) - left,
            round(frame_height * center_bottom) - top,
        )

    best = EMPTY_MATCH
    base_scale = template_resolution.offline_template_scale(
        spec.file_name,
        frame_width,
        frame_height,
        reference_scale=spec.reference_scale,
    )
    for scale in image_utils.candidate_scales(base_scale, spec.scale_ratios):
        scaled = image_utils.resize_template(template, scale)
        scaled_mask = image_utils.resize_mask(mask, scale)
        height, width = scaled.shape[:2]
        if (
            height < min_size
            or width < min_size
            or height > search.shape[0]
            or width > search.shape[1]
        ):
            continue
        try:
            response = image_utils.template_match_response(search, scaled, scaled_mask)
        except cv2.error:
            if not skip_scale_errors:
                raise
            continue
        candidate = image_utils.best_pixel_valid_match(
            response,
            search,
            scaled,
            scaled_mask,
            template_threshold=template_threshold,
            pixel_threshold=(spec.min_pixel_score or 0.0),
            zncc_threshold=spec.min_zncc_score,
            center_bounds=center_bounds,
        )
        if candidate is None or candidate.score <= best.score:
            continue
        best = MatchResult(
            score=candidate.score,
            position=(left + candidate.location[0], top + candidate.location[1]),
            size=(width, height),
            pixel_score=candidate.pixel_score,
            zncc_score=float(getattr(candidate, "zncc_score", -1.0)),
        )
    return best


def passes_match(
    result: MatchResult,
    spec: TemplateSpec,
    config,
    *,
    threshold: float | None = None,
) -> bool:
    """Apply template, pixel and structural thresholds to one match."""

    if threshold is None:
        threshold = resolve_match_threshold(spec, config)
    if result.score < threshold:
        return False
    if spec.min_pixel_score is not None and result.pixel_score < spec.min_pixel_score:
        return False
    if spec.min_zncc_score is not None and result.zncc_score < spec.min_zncc_score:
        return False
    return True


def brightness_ratio(
    frame: np.ndarray,
    spec: TemplateSpec,
    center_ratio: tuple[float, float],
    template_dir,
    cache: dict | None = None,
) -> float:
    """Measure the masked brightness ratio around a relative client center."""

    template, mask = load_template(template_dir, spec, cache=cache)
    frame_gray = image_utils.to_gray(frame)
    frame_height, frame_width = frame_gray.shape[:2]
    scale = template_resolution.offline_template_scale(
        spec.file_name,
        frame_width,
        frame_height,
        reference_scale=spec.reference_scale,
    )
    template_height, template_width = template.shape[:2]
    roi_width = max(8, round(template_width * scale))
    roi_height = max(8, round(template_height * scale))
    center_x = round(frame_width * center_ratio[0])
    center_y = round(frame_height * center_ratio[1])
    left = max(0, center_x - roi_width // 2)
    top = max(0, center_y - roi_height // 2)
    right = min(frame_width, left + roi_width)
    bottom = min(frame_height, top + roi_height)
    region = frame_gray[top:bottom, left:right]
    if region.size == 0:
        return 0.0

    scaled_template = image_utils.resize_template(template, scale)
    scaled_mask = image_utils.resize_mask(mask, scale)
    match_height = min(region.shape[0], scaled_template.shape[0])
    match_width = min(region.shape[1], scaled_template.shape[1])
    if match_height <= 0 or match_width <= 0:
        return 0.0
    region = region[:match_height, :match_width]
    scaled_template = scaled_template[:match_height, :match_width]
    if scaled_mask is not None:
        scaled_mask = scaled_mask[:match_height, :match_width]
        active = scaled_mask > 0
        if not np.any(active):
            return 0.0
        template_mean = float(np.mean(scaled_template[active]))
        region_mean = float(np.mean(region[active]))
    else:
        template_mean = float(np.mean(scaled_template))
        region_mean = float(np.mean(region))
    if template_mean <= 0:
        return 0.0
    return float(region_mean / template_mean)
