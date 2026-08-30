from dataclasses import dataclass
from typing import Callable

import numpy as np

from src.utils.image_utils import relative_roi_frame, to_gray
from src.utils.ocr_utils import normalize_ocr_text

HOME_GACHA_OCR_REFERENCE_ROI = (110, 993, 95, 54)
HOME_GACHA_OCR_RELATIVE_ROI = (
    110 / 1920,
    993 / 1080,
    205 / 1920,
    1047 / 1080,
)
HOME_GACHA_OCR_KEYWORD = "抽抽乐"
HOME_GACHA_OCR_SCALES = (1.0, 2.0, 3.0)
HOME_GACHA_OCR_ALIASES = (HOME_GACHA_OCR_KEYWORD,)
HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT = (169, 615)
HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT = (
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[0] / 1920,
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[1] / 1080,
)

# 左上图标列整列大 ROI（我的小屋/格鲁TALK/街机游戏）。检测模型需要上下文，
# 禁止单独拆出单标签小 ROI（实测丢字/返回空）。来源：BUG-20260829-01 实测标定。
HOME_LEFT_COLUMN_OCR_REFERENCE_ROI = (110, 165, 430, 155)
HOME_LEFT_COLUMN_OCR_RELATIVE_ROI = (
    110 / 1920,
    165 / 1080,
    540 / 1920,
    320 / 1080,
)
# 每组关键词为简体（OCR 只识别简体中文，2026-08-29 取消繁体识别别名）；
# 子串命中即该组计 1 票。
HOME_LEFT_COLUMN_KEYWORD_GROUPS = (
    ("我的小屋",),
    ("格鲁TALK",),
    ("街机游戏",),
)
HOME_LEFT_COLUMN_REQUIRED_HITS = 2

# 左列灰度 p95：未压暗实测定标 253、0.5x 公告压暗 126，阈值 185 干净分离，
# 且不随主页背景场景变化（旧模板相对亮度比值依赖采集场景，已废弃）。
HOME_DIMMED_P95_THRESHOLD_DEFAULT = 185.0


@dataclass(frozen=True)
class HomeGachaOcrResult:
    text: str
    selected_scale: float | None
    attempts: tuple[tuple[float, str], ...]

    @property
    def matched(self) -> bool:
        return self.selected_scale is not None

    @property
    def trace(self) -> str:
        attempts = ", ".join(
            f"x{scale:g}={text or '-'}" for scale, text in self.attempts
        )
        selected = (
            f"采用x{self.selected_scale:g}"
            if self.selected_scale is not None
            else "未命中"
        )
        return f"{selected}; {attempts}"


def home_gacha_ocr_with_fallback(
    read_text: Callable[[float], object],
    scales: tuple[float, ...] = HOME_GACHA_OCR_SCALES,
) -> HomeGachaOcrResult:
    """Read the fixed gacha ROI with bounded same-frame upscale fallbacks."""
    attempts: list[tuple[float, str]] = []
    first_nonempty = ""
    for scale in scales:
        text = str(read_text(float(scale)) or "").strip()
        attempts.append((float(scale), text))
        if text and not first_nonempty:
            first_nonempty = text
        if home_gacha_ocr_matches(text):
            return HomeGachaOcrResult(text, float(scale), tuple(attempts))
    return HomeGachaOcrResult(first_nonempty, None, tuple(attempts))


def home_gacha_ocr_matches(text: object) -> bool:
    normalized_text = normalize_ocr_text(text)
    return any(
        normalize_ocr_text(keyword) in normalized_text
        for keyword in HOME_GACHA_OCR_ALIASES
    )


def home_left_column_hits(text: object) -> int:
    """Count left-column keyword groups present in the same-frame OCR text."""
    normalized_text = normalize_ocr_text(text)
    hits = 0
    for aliases in HOME_LEFT_COLUMN_KEYWORD_GROUPS:
        if any(
            normalize_ocr_text(alias) in normalized_text
            for alias in aliases
        ):
            hits += 1
    return hits


def home_left_column_p95_brightness(frame) -> float:
    """Scene-independent 95th-percentile grayscale of the left-column ROI."""
    if frame is None:
        return 0.0
    _left, _top, crop = relative_roi_frame(frame, HOME_LEFT_COLUMN_OCR_RELATIVE_ROI)
    if crop.size == 0:
        return 0.0
    return float(np.percentile(to_gray(crop), 95))


def home_confirmation_passes(
    *,
    left_hits: int,
    required_left_hits: int,
    brightness: float,
    brightness_threshold: float,
    gacha_ocr_text: object,
) -> bool:
    """Require all three same-frame signals before confirming the global home page.

    brightness is a scene-independent dimming metric compared against
    brightness_threshold: the OCR pipeline passes the left-column grayscale
    p95 (0-255, threshold 185); the legacy template bridge passes the
    template-relative brightness ratio (0-1, threshold 0.75).
    """
    return (
        int(left_hits) >= int(required_left_hits)
        and float(brightness) >= float(brightness_threshold)
        and home_gacha_ocr_matches(gacha_ocr_text)
    )


def home_temporary_announcement_detected(
    *,
    left_hits: int,
    required_left_hits: int,
    brightness: float,
    brightness_threshold: float,
    gacha_ocr_text: object,
) -> bool:
    """Detect a dimmed global home page covered by a temporary announcement."""
    return (
        int(left_hits) >= int(required_left_hits)
        and float(brightness) < float(brightness_threshold)
        and home_gacha_ocr_matches(gacha_ocr_text)
    )
