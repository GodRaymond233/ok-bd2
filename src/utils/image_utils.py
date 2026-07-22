from collections.abc import Callable, Iterable
from dataclasses import dataclass
from statistics import fmean, median

import cv2
import numpy as np


@dataclass(frozen=True)
class PixelValidMatch:
    """Highest template-score candidate that also passes pixel validation."""

    score: float
    pixel_score: float
    location: tuple[int, int]


@dataclass(frozen=True)
class StableMatchObservation:
    """One valid template hit within a temporal stabilization window."""

    sample_index: int
    center: tuple[int, int]
    score: float
    pixel_score: float


@dataclass(frozen=True)
class StableMatchConsensus:
    """A spatially stable cluster selected from multiple captured frames."""

    center: tuple[int, int]
    hit_count: int
    sample_count: int
    average_score: float
    average_pixel_score: float
    center_spread: float


STABLE_MATCH_SAMPLE_INTERVAL = 0.1
STABLE_MATCH_WINDOW_SAMPLES = 11
STABLE_MATCH_MAX_SAMPLES = 21
STABLE_MATCH_MINIMUM_HITS = 6
STABLE_MATCH_TRAILING_HITS = 3
STABLE_MATCH_CLUSTER_RADIUS_REFERENCE = 24.0
STABLE_MATCH_MAXIMUM_SPREAD_REFERENCE = 12.0


def stable_match_consensus(
    observations: Iterable[StableMatchObservation],
    *,
    sample_count: int,
    cluster_radius: float,
    maximum_center_spread: float,
    minimum_hits: int = 6,
    required_trailing_hits: int = 3,
) -> StableMatchConsensus | None:
    """Choose a persistent, high-confidence and spatially stable match cluster."""

    total_samples = max(0, int(sample_count))
    values = tuple(observations)
    if total_samples <= 0 or not values:
        return None

    clusters: list[list[StableMatchObservation]] = []
    for observation in sorted(values, key=lambda value: value.sample_index):
        nearest = None
        nearest_distance = float("inf")
        for cluster in clusters:
            center_x = float(median(value.center[0] for value in cluster))
            center_y = float(median(value.center[1] for value in cluster))
            distance = max(
                abs(observation.center[0] - center_x),
                abs(observation.center[1] - center_y),
            )
            if distance <= cluster_radius and distance < nearest_distance:
                nearest = cluster
                nearest_distance = distance
        if nearest is None:
            clusters.append([observation])
        else:
            nearest.append(observation)

    trailing_start = max(0, total_samples - max(0, int(required_trailing_hits)))
    required_indexes = set(range(trailing_start, total_samples))
    candidates: list[tuple[tuple[float, ...], StableMatchConsensus]] = []
    for cluster in clusters:
        if len(cluster) < max(1, int(minimum_hits)):
            continue
        indexes = {value.sample_index for value in cluster}
        if not required_indexes.issubset(indexes):
            continue
        center_x = float(median(value.center[0] for value in cluster))
        center_y = float(median(value.center[1] for value in cluster))
        spread = max(
            max(abs(value.center[0] - center_x), abs(value.center[1] - center_y))
            for value in cluster
        )
        if spread > maximum_center_spread:
            continue
        average_score = fmean(value.score for value in cluster)
        pixel_scores = [value.pixel_score for value in cluster if value.pixel_score >= 0]
        average_pixel = fmean(pixel_scores) if pixel_scores else average_score
        consensus = StableMatchConsensus(
            center=(round(center_x), round(center_y)),
            hit_count=len(cluster),
            sample_count=total_samples,
            average_score=average_score,
            average_pixel_score=average_pixel,
            center_spread=float(spread),
        )
        candidates.append(
            (
                (
                    float(consensus.hit_count),
                    (average_score + average_pixel) / 2,
                    -consensus.center_spread,
                ),
                consensus,
            )
        )

    if not candidates:
        return None
    return max(candidates, key=lambda value: value[0])[1]


def stabilize_template_match(
    initial_match,
    initial_frame_shape: tuple[int, ...],
    *,
    sample_match: Callable[[], tuple[object, tuple[int, ...]]],
    passes: Callable[[object], bool],
    sleep: Callable[[float], None],
    on_sample: Callable[[object], None] | None = None,
    sample_interval: float = STABLE_MATCH_SAMPLE_INTERVAL,
    window_samples: int = STABLE_MATCH_WINDOW_SAMPLES,
    maximum_samples: int = STABLE_MATCH_MAX_SAMPLES,
) -> tuple[StableMatchConsensus, tuple[int, ...]] | None:
    """Observe a first valid match until a stable rolling one-second window exists."""

    wanted_window = max(2, int(window_samples))
    wanted_maximum = max(wanted_window, int(maximum_samples))
    samples: list[tuple[object, tuple[int, ...]] | None] = [
        (initial_match, initial_frame_shape)
    ]
    last_shape = initial_frame_shape
    if on_sample is not None:
        on_sample(initial_match)

    for _sample_index in range(1, wanted_maximum):
        sleep(max(0.0, float(sample_interval)))
        result, frame_shape = sample_match()
        last_shape = frame_shape
        if on_sample is not None:
            on_sample(result)
        samples.append((result, frame_shape) if passes(result) else None)
        if len(samples) < wanted_window:
            continue

        window = samples[-wanted_window:]
        observations = []
        for index, value in enumerate(window):
            if value is None:
                continue
            match, _shape = value
            center = (
                int(match.position[0] + match.size[0] // 2),
                int(match.position[1] + match.size[1] // 2),
            )
            observations.append(
                StableMatchObservation(
                    sample_index=index,
                    center=center,
                    score=float(match.score),
                    pixel_score=float(getattr(match, "pixel_score", -1.0)),
                )
            )

        frame_height, frame_width = last_shape[:2]
        client_scale = min(frame_width / 1920, frame_height / 1080)
        consensus = stable_match_consensus(
            observations,
            sample_count=len(window),
            cluster_radius=max(2.0, STABLE_MATCH_CLUSTER_RADIUS_REFERENCE * client_scale),
            maximum_center_spread=max(
                1.0,
                STABLE_MATCH_MAXIMUM_SPREAD_REFERENCE * client_scale,
            ),
            minimum_hits=STABLE_MATCH_MINIMUM_HITS,
            required_trailing_hits=STABLE_MATCH_TRAILING_HITS,
        )
        if consensus is not None:
            return consensus, last_shape

    return None


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
