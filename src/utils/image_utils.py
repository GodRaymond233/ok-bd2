from collections.abc import Iterable
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PixelValidMatch:
    """Highest template-score candidate that also passes pixel validation."""

    score: float
    pixel_score: float
    location: tuple[int, int]


def sanitize_template_response(response: np.ndarray) -> np.ndarray:
    """Reject non-finite and mathematically invalid normalized scores in place."""

    np.nan_to_num(response, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
    response[(response < -1.000001) | (response > 1.000001)] = -1.0
    return response


def template_match_response(
    search: np.ndarray,
    template: np.ndarray,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    """Run the project's normalized matcher and return a sanitized response."""

    method = cv2.TM_CCORR_NORMED if mask is not None else cv2.TM_CCOEFF_NORMED
    response = cv2.matchTemplate(search, template, method, mask=mask)
    return sanitize_template_response(response)


def binarize_bgr_by_brightness(image, threshold: int = 180):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def candidate_scales(
    base_scale: float,
    scale_ratios: Iterable[float] = (1.0,),
    minimum: float = 0.2,
) -> list[float]:
    """Build stable template scales from a calibrated client scale."""
    lower_bound = max(0.001, float(minimum))
    return [round(max(lower_bound, float(base_scale) * float(ratio)), 3) for ratio in scale_ratios]


def resize_template(template: np.ndarray, scale: float) -> np.ndarray:
    """Resize a template with interpolation suited to shrinking or enlarging."""
    if abs(float(scale) - 1.0) < 0.001:
        return template
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(template, None, fx=scale, fy=scale, interpolation=interpolation)


def resize_mask(mask: np.ndarray | None, scale: float) -> np.ndarray | None:
    """Resize a template mask without introducing interpolated mask values."""
    if mask is None or abs(float(scale) - 1.0) < 0.001:
        return mask
    return cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def to_gray(image: np.ndarray) -> np.ndarray:
    """Return a gray image from gray, BGR, or BGRA input."""
    if image.ndim == 2:
        return image
    if image.ndim != 3 or image.shape[2] not in {3, 4}:
        raise ValueError(f"Unsupported image shape for grayscale conversion: {image.shape}")
    conversion = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
    return cv2.cvtColor(image, conversion)


def pixel_similarity(
    region: np.ndarray,
    template: np.ndarray,
    mask: np.ndarray | None = None,
) -> float:
    """Return normalized absolute-difference similarity, optionally under a mask."""
    if region.shape != template.shape:
        return -1.0
    difference = np.abs(region.astype(np.float32) - template.astype(np.float32))
    if mask is not None:
        active = mask > 0
        if not np.any(active):
            return -1.0
        difference = difference[active]
    return float(1.0 - np.mean(difference) / 255.0)


def best_pixel_valid_match(
    response: np.ndarray,
    search: np.ndarray,
    template: np.ndarray,
    mask: np.ndarray | None,
    *,
    template_threshold: float,
    pixel_threshold: float,
    center_bounds: tuple[int, int, int, int] | None = None,
    max_independent_candidates: int = 128,
) -> PixelValidMatch | None:
    """Return the best independent peak that passes template and pixel gates.

    Invalid masked-match values are rejected rather than clamped. Candidates
    are examined in descending template-score order, making the first
    pixel-valid result equivalent to filtering all independent candidates by
    pixel similarity and sorting the survivors by template score.
    """

    matches = independent_pixel_valid_matches(
        response,
        search,
        template,
        mask,
        template_threshold=template_threshold,
        pixel_threshold=pixel_threshold,
        center_bounds=center_bounds,
        max_matches=1,
        max_independent_candidates=max_independent_candidates,
    )
    return matches[0] if matches else None


def independent_pixel_valid_matches(
    response: np.ndarray,
    search: np.ndarray,
    template: np.ndarray,
    mask: np.ndarray | None,
    *,
    template_threshold: float,
    pixel_threshold: float,
    center_bounds: tuple[int, int, int, int] | None = None,
    suppression_radius: int | tuple[int, int] | None = None,
    max_matches: int = 60,
    max_independent_candidates: int = 4096,
) -> tuple[PixelValidMatch, ...]:
    """Enumerate independent dual-valid candidates in template-score order."""

    if response.ndim != 2 or response.size == 0:
        return ()

    sanitize_template_response(response)
    working = response.copy()
    height, width = template.shape[:2]

    if center_bounds is not None:
        center_left, center_top, center_right, center_bottom = center_bounds
        x_start = max(0, int(np.ceil(center_left - width / 2)))
        y_start = max(0, int(np.ceil(center_top - height / 2)))
        x_stop = min(working.shape[1], int(np.ceil(center_right - width / 2)))
        y_stop = min(working.shape[0], int(np.ceil(center_bottom - height / 2)))
        if x_start >= x_stop or y_start >= y_stop:
            return ()
        allowed = np.full(working.shape, -1.0, dtype=working.dtype)
        allowed[y_start:y_stop, x_start:x_stop] = working[y_start:y_stop, x_start:x_stop]
        working = allowed

    if suppression_radius is None:
        suppression_x = max(2, width // 2)
        suppression_y = max(2, height // 2)
    elif isinstance(suppression_radius, tuple):
        suppression_x = max(1, int(suppression_radius[0]))
        suppression_y = max(1, int(suppression_radius[1]))
    else:
        suppression_x = suppression_y = max(1, int(suppression_radius))

    matches: list[PixelValidMatch] = []
    for _ in range(max(1, int(max_independent_candidates))):
        _minimum, score, _minimum_location, location = cv2.minMaxLoc(working)
        if not np.isfinite(score) or score < template_threshold or score > 1.000001:
            break

        x, y = int(location[0]), int(location[1])
        region = search[y : y + height, x : x + width]
        candidate_pixel_score = pixel_similarity(region, template, mask)
        if candidate_pixel_score >= pixel_threshold:
            matches.append(
                PixelValidMatch(
                    score=float(score),
                    pixel_score=float(candidate_pixel_score),
                    location=(x, y),
                )
            )
            if len(matches) >= max(1, int(max_matches)):
                break

        left = max(0, x - suppression_x)
        top = max(0, y - suppression_y)
        right = min(working.shape[1], x + suppression_x + 1)
        bottom = min(working.shape[0], y + suppression_y + 1)
        working[top:bottom, left:right] = -1.0

    return tuple(matches)


def crop_relative(
    image: np.ndarray,
    bounds: tuple[float, float, float, float],
) -> np.ndarray:
    """Crop fractional left/top/right/bottom bounds from an image."""
    _left, _top, crop = relative_roi_frame(image, bounds)
    return crop


def relative_roi_frame(
    image: np.ndarray,
    bounds: tuple[float, float, float, float],
) -> tuple[int, int, np.ndarray]:
    """Return a clamped relative ROI and its full-frame top-left offset."""
    height, width = image.shape[:2]
    left = max(0, min(width, round(width * bounds[0])))
    top = max(0, min(height, round(height * bounds[1])))
    right = max(left, min(width, round(width * bounds[2])))
    bottom = max(top, min(height, round(height * bounds[3])))
    return left, top, image[top:bottom, left:right]


def reference_roi_frame(
    image: np.ndarray,
    roi: tuple[int, int, int, int] | None,
    reference_size: tuple[int, int],
) -> tuple[int, int, np.ndarray]:
    """Scale an x/y/width/height reference ROI with its position and size."""
    if roi is None:
        return 0, 0, image

    reference_width, reference_height = reference_size
    if reference_width <= 0 or reference_height <= 0:
        raise ValueError("Reference dimensions must be positive.")

    height, width = image.shape[:2]
    x, y, roi_width, roi_height = roi
    left = max(0, min(width, round(x * width / reference_width)))
    top = max(0, min(height, round(y * height / reference_height)))
    right = max(left, min(width, round((x + roi_width) * width / reference_width)))
    bottom = max(top, min(height, round((y + roi_height) * height / reference_height)))
    return left, top, image[top:bottom, left:right]
