from collections.abc import Iterable

import cv2
import numpy as np


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
