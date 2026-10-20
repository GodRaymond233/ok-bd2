"""活动卡带封面定位；槽位序号随账号持有的限时卡变化，不能用作卡带身份。"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from src.tasks.map_trade.models import EVENT_CARD_NAMES, MatchResult
from src.utils.calibration import FHD_1080
from src.utils.image_utils import read_template_image, template_match_response

MIN_SCORE = 0.70
MIN_MARGIN = 0.15
SCALES = (0.97, 1.0, 1.03)
BAND_TOP = 895
BAND_BOTTOM = 1050
COMPETITOR_MARGIN_PIXELS = 54
BADGE_CENTER_OFFSET_X = 10
BADGE_CENTER_Y = 936
BADGE_SIDE = 29
# 截图失效常表现为纯色帧；封面带几乎没有纹理时不是有效画面。
MIN_BAND_STDDEV = 1.0
# 有效画面里目标封面低于门槛：唯一可能表示“未持有”的识别结果。
ABSENT_REASON_PREFIX = "未持有或未识别活动卡"


def is_absent_reason(reason: str) -> bool:
    return reason.startswith(ABSENT_REASON_PREFIX)


@dataclass(frozen=True)
class EventCardDetection:
    result: MatchResult | None
    competitor: int | None = None
    margin: float = 0.0
    reason: str = ""


@lru_cache(maxsize=32)
def _template(number: int, scale: float) -> np.ndarray | None:
    path = (
        Path(__file__).resolve().parents[3] / "recognition-assets" / "template-assets"
        / "quick_switch_cartridges" / f"event_cartridge_art_{number:02d}.png"
    )
    image = read_template_image(path)
    if image is None:
        return None
    image = image[:, :, :3]
    return image if scale == 1.0 else cv2.resize(
        image, None, fx=scale, fy=scale,
        interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
    )


def find_card(frame: np.ndarray, number: int) -> EventCardDetection:
    if frame is None or frame.ndim != 3 or frame.shape[2] < 3 or frame.shape[0] < 100:
        return EventCardDetection(None, reason="活动卡带画面无效")
    factor = frame.shape[0] / FHD_1080.height
    crop = frame[round(BAND_TOP * factor):round(BAND_BOTTOM * factor), :, :3]
    if float(crop.std()) < MIN_BAND_STDDEV:
        return EventCardDetection(None, reason="活动卡带画面无效")
    band = cv2.resize(
        crop, (round(frame.shape[1] / factor), BAND_BOTTOM - BAND_TOP),
        interpolation=cv2.INTER_AREA if factor > 1 else cv2.INTER_CUBIC,
    )

    def best(candidate, left=0, right=None):
        region = band[:, left:right]
        found = None
        for scale in SCALES:
            template = _template(candidate, scale)
            if template is None:
                return None
            height, width = template.shape[:2]
            if region.shape[0] < height or region.shape[1] < width:
                continue
            response = template_match_response(region, template)
            _, score, _, location = cv2.minMaxLoc(response)
            if found is None or score > found[0]:
                found = (float(score), left + location[0], location[1], width)
        return found

    target = best(number)
    if target is None:
        return EventCardDetection(None, reason=f"活动卡{number}封面素材不可用")
    score, x, _y, width = target
    if score < MIN_SCORE:
        return EventCardDetection(None, reason=f"{ABSENT_REASON_PREFIX}{number}：{score:.3f}")
    left = max(0, x - COMPETITOR_MARGIN_PIXELS)
    right = min(band.shape[1], x + width + COMPETITOR_MARGIN_PIXELS)
    ranked = []
    for candidate in EVENT_CARD_NAMES:
        if candidate == number:
            continue
        other = best(candidate, left, right)
        if other is None:
            return EventCardDetection(None, reason=f"活动卡{candidate}竞争识别素材不可用")
        ranked.append((other[0], candidate))
    runner_score, runner = max(ranked)
    margin = score - runner_score
    if margin < MIN_MARGIN:
        return EventCardDetection(
            None, runner, margin, f"活动卡{number}封面有歧义：{score:.3f}/{runner_score:.3f}",
        )
    # 完成图标位于卡片左上定位点下方；封面裁切从卡片左侧30像素开始。
    center = (round((x + BADGE_CENTER_OFFSET_X) * factor), round(BADGE_CENTER_Y * factor))
    side = max(1, round(BADGE_SIDE * factor))
    result = MatchResult(
        score, (center[0] - side // 2, center[1] - side // 2),
        (side, side), scale=factor, pixel_score=score, zncc_score=score,
    )
    return EventCardDetection(result, runner, margin)
