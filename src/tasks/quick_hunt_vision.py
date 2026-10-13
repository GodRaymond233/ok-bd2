"""Conservative visual readings for quick-hunt resource controls."""

from __future__ import annotations

import math
import re
import unicodedata
from decimal import Decimal

import cv2
import numpy as np


def _bounds(box):
    try:
        x, y, width, height = (
            float(getattr(box, key)) for key in ("x", "y", "width", "height")
        )
    except (AttributeError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        return None
    return (x, y, width, height) if width > 0 and height > 0 else None


def read_free_switch(frame, label_box) -> tuple[str, tuple[float, float] | None]:
    """Read a complete track and knob beside the current frame's OCR label."""
    bounds = _bounds(label_box)
    if (
        bounds is None or not isinstance(frame, np.ndarray)
        or frame.ndim != 3 or frame.shape[2] < 3 or frame.dtype != np.uint8
    ):
        return "unknown", None
    x, y, width, height = bounds
    label_right, center_y = x + width, y + height / 2
    frame_height, frame_width = frame.shape[:2]
    if x < 0 or y < 0 or label_right >= frame_width or y + height > frame_height:
        return "unknown", None
    left = max(0, math.floor(label_right))
    right = min(frame_width, math.ceil(label_right + 5.5 * height))
    top = max(0, math.floor(center_y - 1.25 * height))
    bottom = min(frame_height, math.ceil(center_y + 1.25 * height))
    crop = frame[top:bottom, left:right, :3]
    if crop.size == 0:
        return "unknown", None
    pixels = crop.astype(np.int16)
    b, g, r = (pixels[..., channel] for channel in range(3))
    neutral = pixels.max(axis=2) - pixels.min(axis=2) <= 45
    yellow = (r > 150) & (g > 110) & (b < 90)
    value = pixels.max(axis=2)
    border = np.concatenate((value[0], value[-1], value[:, 0], value[:, -1]))
    foreground = yellow | (neutral & (value >= max(65, float(np.median(border)) + 25)))
    bright_knob = neutral & (pixels.min(axis=2) >= 155) & (value >= 180)
    kernel_size = max(1, round(height * 0.1)) | 1
    foreground = cv2.morphologyEx(
        foreground.astype(np.uint8), cv2.MORPH_CLOSE,
        np.ones((kernel_size, kernel_size), dtype=np.uint8),
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(foreground)
    candidates = []
    track_count = 0
    for component in range(1, count):
        tx, ty, tw, th, area = stats[component]
        if (
            not 0.65 * height <= th <= 1.5 * height
            or not 1.65 <= tw / th <= 2.65
            or abs(top + ty + th / 2 - center_y) > 0.5 * height
            or tx > 3.3 * height
            or tx == 0 or ty == 0 or tx + tw >= crop.shape[1] or ty + th >= crop.shape[0]
            or not 0.70 <= area / (tw * th) <= 0.97
        ):
            continue
        track = labels[ty:ty + th, tx:tx + tw] == component
        yy, xx = np.ogrid[:th, :tw]
        radius = (th - 1) / 2
        capsule = (
            (xx - np.clip(xx, radius, tw - 1 - radius)) ** 2
            + (yy - radius) ** 2 <= (radius + 0.5) ** 2
        )
        if np.count_nonzero(track & capsule) / np.count_nonzero(track | capsule) < 0.80:
            continue
        track_count += 1
        if track_count > 1:
            return "unknown", None
        knob_mask = bright_knob[ty:ty + th, tx:tx + tw] & track
        knob_count, _, knob_stats, knob_centers = cv2.connectedComponentsWithStats(
            knob_mask.astype(np.uint8)
        )
        knobs = []
        for index in range(1, knob_count):
            kx, ky, kw, kh, ka = knob_stats[index]
            if (
                0.65 * th <= kw <= 1.1 * th and 0.65 * th <= kh <= 1.1 * th
                and 0.80 <= kw / kh <= 1.25 and 0.65 <= ka / (kw * kh) <= 0.91
            ):
                kcx, kcy = knob_centers[index]
                disk = (xx - kcx) ** 2 + (yy - kcy) ** 2 <= ((kw + kh) / 4) ** 2
                if np.count_nonzero(knob_mask & disk) / np.count_nonzero(knob_mask | disk) >= 0.82:
                    knobs.append((kcx, kcy))
        if len(knobs) != 1:
            continue
        kcx, kcy = knobs[0]
        if abs(kcy - radius) > 0.18 * th:
            continue
        exposed = track & ~knob_mask
        if np.count_nonzero(exposed) < 0.25 * area:
            continue
        yellow_ratio = np.mean(yellow[ty:ty + th, tx:tx + tw][exposed])
        neutral_ratio = np.mean(neutral[ty:ty + th, tx:tx + tw][exposed])
        state = "unknown"
        if abs(kcx - (tw - 1 - radius)) <= 0.20 * th and yellow_ratio >= 0.65:
            state = "on"
        elif abs(kcx - radius) <= 0.20 * th and yellow_ratio <= 0.02 and neutral_ratio >= 0.95:
            state = "off"
        candidates.append((state, (float(left + tx + tw / 2), float(top + ty + th / 2))))
    if len(candidates) != 1 or candidates[0][0] == "unknown":
        return "unknown", None
    return candidates[0]


def parse_free_pool(text, expected_capacity=None) -> tuple[int, int] | None:
    """Read one bounded free-resource fraction, never a neighbouring reserve."""
    normalized = unicodedata.normalize("NFKC", str(text))
    matches = list(re.finditer(r"[0-9]+\s*[/：:|\-~]\s*[0-9]+", normalized))
    if len(matches) != 1:
        return None
    match = matches[0]
    before = normalized[:match.start()].rstrip()
    after = normalized[match.end():].lstrip()
    if (before and re.search(r"[\d.,/：:|\-~+kKmM万]$", before)) or (
        after and re.match(r"[\d.,/：:|\-~kKmM万]", after)
    ):
        return None
    free, capacity = (int(part) for part in re.split(r"\s*[/：:|\-~]\s*", match.group()))
    if not 0 <= free <= capacity <= 999 or capacity == 0:
        return None
    if expected_capacity is not None and capacity != expected_capacity:
        return None
    return free, capacity


def _stock_count(text):
    text = unicodedata.normalize("NFKC", text)
    match = re.fullmatch(r"([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(\.[0-9]+)?([kKmM万])?", text)
    if match is None or (match[2] and not match[3]):
        return None
    unit = match[3].lower() if match[3] else None
    multiplier = {None: 1, "k": 1000, "m": 1000000, "万": 10000}[unit]
    value = Decimal(match[1].replace(",", "") + (match[2] or "")) * multiplier
    return int(value) if value == value.to_integral_value() else None


def parse_stone_rows(boxes) -> tuple[int, ...] | None:
    """Join adjacent OCR fragments within exactly five ordered inventory rows."""
    entries = []
    for box in boxes:
        bounds = _bounds(box)
        text = str(getattr(box, "name", "")).strip()
        if bounds is None or not text:
            return None
        entries.append((*bounds, text))
    entries.sort(key=lambda entry: entry[1] + entry[3] / 2)
    rows = []
    for entry in entries:
        x, y, width, height, text = entry
        if rows and (
            abs(y + height / 2 - rows[-1][0][1] - rows[-1][0][3] / 2)
            <= 0.45 * max(height, rows[-1][0][3])
        ):
            rows[-1].append(entry)
        else:
            rows.append([entry])
    if len(rows) != 5:
        return None
    counts = []
    previous_bottom = None
    for row in rows:
        row.sort(key=lambda entry: entry[0])
        row_top = min(entry[1] for entry in row)
        row_bottom = max(entry[1] + entry[3] for entry in row)
        if previous_bottom is not None and row_top < previous_bottom:
            return None
        previous_bottom = row_bottom
        for previous, current in zip(row, row[1:]):
            gap = current[0] - previous[0] - previous[2]
            if gap < 0 or gap > max(previous[3], current[3]):
                return None
        value = _stock_count("".join(entry[4] for entry in row))
        if value is None:
            return None
        counts.append(value)
    return tuple(counts)
