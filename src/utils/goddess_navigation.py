"""Locate a goddess task row using text and its adjacent daily icon."""

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

import cv2

from src.utils.vision_models import TemplateSpec


@dataclass(frozen=True)
class TextBox:
    text: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self):
        return round(self.x + self.width / 2), round(self.y + self.height / 2)


@dataclass(frozen=True)
class NavigationObservation:
    state: str
    text: str
    navigation: TextBox | None = None


def is_daily_title(text):
    normalized = re.sub(r"\s", "", text)
    return normalized.startswith("每日") and len(normalized) >= 3 and (
        "奖励" in normalized or normalized.startswith("每日奖")
    )


def is_goddess_destination(text):
    text = re.sub(r"[\s.。…·]", "", text)
    # The action prefix may contain one OCR substitution, as in 多动至.
    destination = text[text.find("艾"):] if "艾" in text else text
    action = text[: text.find("艾")] if "艾" in text else text
    return (
        len(destination) >= 4
        and ("移" in action or "动" in action or "多" in action)
        and SequenceMatcher(None, "艾力克史温女", destination).ratio() >= 0.55
    )


def scan_navigation(frame, *, ocr, normalize, match, passes, icon_specs):
    height, width = frame.shape[:2]
    broad = (int(width * 0.50), 0, width, height)
    texts = []
    saw_task = False

    def read(rect, scale):
        left, top, right, bottom = rect
        crop = frame[top:bottom, left:right]
        if scale != 1:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        boxes = ocr(frame=crop, threshold=0.65, target_height=0, log=False)
        result = [
            TextBox(
                box.name, float(box.confidence),
                box.x / scale + left, box.y / scale + top,
                box.width / scale, box.height / scale,
            )
            for box in boxes if box.width > 0 and box.height > 0
        ]
        texts.extend(box.text for box in result)
        return result

    def icon_present(title):
        h = title.height
        left, top = max(0, int(title.x - 2 * h)), max(0, int(title.y - 0.6 * h))
        right = min(width, int(title.x + 0.2 * h))
        bottom = min(height, int(title.y + 1.6 * h))
        roi = (
            round(left * 1920 / width), round(top * 1080 / height),
            round((right - left) * 1920 / width), round((bottom - top) * 1080 / height),
        )
        text_scale = min(h, title.width / 4 * 1.15) / (24 * height / 1080)
        scales = tuple(text_scale * ratio for ratio in (0.85, 0.9, 0.95, 1, 1.05, 1.1, 1.15))
        for original in icon_specs:
            spec = replace(original, roi=roi, scale_ratios=scales)
            result = match(frame, spec)
            if passes(result, spec):
                return True
        return False

    for scale in (1, 2):
        boxes = read(broad, scale)
        anchors = [b for b in boxes if is_daily_title(normalize(b.text))]
        anchors += [b for b in boxes if is_goddess_destination(normalize(b.text))]
        saw_task = saw_task or bool(anchors)
        candidates = []
        for anchor in anchors:
            h = anchor.height
            is_title = is_daily_title(normalize(anchor.text))
            rect = (
                max(0, int(anchor.x - 3 * h)),
                max(0, int(anchor.y - (h if is_title else 3 * h))),
                min(width, int(anchor.x + max(anchor.width, 12 * h) + 3 * h)),
                min(height, int(anchor.y + (5 * h if is_title else 2 * h))),
            )
            local = read(rect, min(4, max(1, 36 / h)))
            titles = [b for b in local if is_daily_title(normalize(b.text))]
            navigations = [b for b in local if is_goddess_destination(normalize(b.text))]
            for title in titles:
                for nav in navigations:
                    if not (0.4 * nav.height <= nav.y - title.y <= 3 * nav.height
                            and abs(title.x - nav.x) <= 2 * nav.height):
                        continue
                    if not icon_present(title):
                        continue
                    # Both text anchors can rediscover the same physical row.
                    if not any(abs(nav.center[0] - old.center[0]) <= nav.height
                               and abs(nav.center[1] - old.center[1]) <= nav.height
                               for old in candidates):
                        candidates.append(nav)
        text = " | ".join(dict.fromkeys(texts))
        if len(candidates) > 1:
            return NavigationObservation("ambiguous", text)
        if candidates:
            return NavigationObservation("ready", text, candidates[0])
    # Absence only describes this frame; it never means the task is complete.
    state = "unknown" if saw_task or not texts else "absent"
    return NavigationObservation(state, " | ".join(dict.fromkeys(texts)))


NEW_DAILY_ICON = TemplateSpec(
    name="goddess_daily_new",
    file_name="goddess_daily_new.png",
    threshold=0.76,
    min_pixel_score=0.85,
)
