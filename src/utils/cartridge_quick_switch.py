from __future__ import annotations

import numpy as np

from src.utils.image_utils import relative_roi_frame

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080

RECENT_CATEGORY_LABEL = "最近"
SHOPKEEPER_CATEGORY_LABEL = "店长游戏卡"
STORY_CATEGORY_LABEL = "剧情游戏卡"
CHARACTER_CATEGORY_LABEL = "角色游戏卡"
BATTLE_GAMEPLAY_CATEGORY_LABEL = "战斗玩法游戏卡带"
LIFE_GAMEPLAY_CATEGORY_LABEL = "生活玩法游戏卡带"
EVENT_CATEGORY_LABEL = "活动游戏卡"

QUICK_SWITCH_PAGE_LABELS = (
    RECENT_CATEGORY_LABEL,
    SHOPKEEPER_CATEGORY_LABEL,
    STORY_CATEGORY_LABEL,
    CHARACTER_CATEGORY_LABEL,
    BATTLE_GAMEPLAY_CATEGORY_LABEL,
    LIFE_GAMEPLAY_CATEGORY_LABEL,
    EVENT_CATEGORY_LABEL,
)

# 2026-08-14 PC UI calibration. Points and regions are relative to the client,
# not the outer window, and therefore remain valid when the client is scaled.
BATTLE_GAMEPLAY_CATEGORY_POINT = (
    923 / REFERENCE_WIDTH,
    875 / REFERENCE_HEIGHT,
)
BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION = (
    826 / REFERENCE_WIDTH,
    840 / REFERENCE_HEIGHT,
    1025 / REFERENCE_WIDTH,
    915 / REFERENCE_HEIGHT,
)
BATTLE_GAMEPLAY_CATEGORY_OCR_ROI = (826, 840, 199, 75)

LIFE_GAMEPLAY_CATEGORY_POINT = (
    1126 / REFERENCE_WIDTH,
    875 / REFERENCE_HEIGHT,
)
LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION = (
    1025 / REFERENCE_WIDTH,
    840 / REFERENCE_HEIGHT,
    1229 / REFERENCE_WIDTH,
    915 / REFERENCE_HEIGHT,
)
LIFE_GAMEPLAY_CATEGORY_OCR_ROI = (1025, 840, 204, 75)

GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO = 0.05
# Only fixed slot selections wait here; recognition-derived center clicks do not.
FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS = 0.8


def category_highlight_ratio(
    frame: np.ndarray,
    relative_roi: tuple[float, float, float, float],
    minimum_gray: int = 170,
    maximum_channel_spread: int = 35,
) -> float:
    """Measure the neutral-white selected state of one quick-switch category."""

    _left, _top, region = relative_roi_frame(frame, relative_roi)
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
