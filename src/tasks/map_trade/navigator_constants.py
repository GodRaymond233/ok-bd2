from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    ACTION_SLOT_CENTER_RELATIVE_ROIS,
    ACTION_SLOT_CENTERS_REFERENCE,
    ACTION_SLOT_RELATIVE_ROIS,
    SEARCH_ICON,
    SKILL_GROUP_CENTERS_REFERENCE,
    SUBDUE_ICON,
    SUMMON_ICON,
)
from src.tasks.map_trade.card_status import StoryCardCompletion
from src.tasks.map_trade.models import CardSpec, MapPageMode, MatchResult, TemplateSpec
from src.utils.calibration import FHD_1080, reference_rect_to_relative_roi
from src.utils.cartridge_quick_switch import QUICK_SWITCH_PAGE_LABELS

HOME_TEMPLATES = (
    TemplateSpec("主页", "home.png", 0.72, min_pixel_score=0.80),
    TemplateSpec(
        "主页冰淇淋",
        "image/green/MainHomeIceGE.png",
        0.72,
        green_mask=True,
        min_pixel_score=0.80,
    ),
    TemplateSpec(
        "主页米饭",
        "image/green/MainHomeRIceGE.png",
        0.72,
        green_mask=True,
        min_pixel_score=0.80,
    ),
)
QUICK_SWITCH_TEMPLATE = TemplateSpec(
    "快速切换按钮",
    "image/green/QuickSwitchPlayIco.png",
    0.88,
    relative_roi=(0.25, 0.85, 0.65, 1.0),
    scale_ratios=(0.95, 0.975, 1.0, 1.025, 1.05),
    min_pixel_score=0.85,
    candidate_center_roi=(650 / 1920, 950 / 1080, 1050 / 1920, 1045 / 1080),
    minimum_safe_threshold=0.88,
    min_zncc_score=0.85,
)
Q_SP6_SHOP_PRIORITY_TIMEOUT = 3.0
# 折扣商店页专有页签 OCR 信号：在商店页整帧 OCR 中已验证稳定命中
# （“仓库”单独可能出现在 NPC 名“仓库管理石怪”里，必须与“严加管理”成对出现）。
SHOP_PAGE_OCR_KEYWORDS = ("仓库", "严加管理")
Q_SP6_SHOP_PAGE_KEYWORDS = SHOP_PAGE_OCR_KEYWORDS
Q_SP6_SHOP_PAGE_OCR_INTERVAL = 0.25
Q_SP6_BARGAIN_RECHECK_DELAY = 0.5
Q_SP6_BARGAIN_OCR_TIMEOUT = 10.0
# 共享分类的局部 OCR 区域（1920×1080 参考像素）。整帧 OCR 成本高，
# 这里只扫描各状态专有关键字所在的小区域；坐标来自实机截图标定。
CLASSIFY_LOADING_REFERENCE_ROI = (0, 0, 700, 150)
CLASSIFY_LOADING_RELATIVE_ROI = reference_rect_to_relative_roi(
    CLASSIFY_LOADING_REFERENCE_ROI,
    FHD_1080,
)
# 商店页签（购买/出售）位于左上，标题/仓库/严加管理位于中部标题牌。
CLASSIFY_SHOP_TABS_REFERENCE_ROI = (100, 120, 300, 220)
CLASSIFY_SHOP_TABS_RELATIVE_ROI = reference_rect_to_relative_roi(
    CLASSIFY_SHOP_TABS_REFERENCE_ROI,
    FHD_1080,
)
CLASSIFY_SHOP_TITLE_REFERENCE_ROI = (840, 240, 300, 170)
CLASSIFY_SHOP_TITLE_RELATIVE_ROI = reference_rect_to_relative_roi(
    CLASSIFY_SHOP_TITLE_REFERENCE_ROI,
    FHD_1080,
)
# 卡带页：顶部“游戏卡珍藏集”标题与底部类别页签/收藏页描述。
CLASSIFY_CARD_MENU_TITLE_REFERENCE_ROI = (0, 0, 700, 110)
CLASSIFY_CARD_MENU_TITLE_RELATIVE_ROI = reference_rect_to_relative_roi(
    CLASSIFY_CARD_MENU_TITLE_REFERENCE_ROI,
    FHD_1080,
)
CLASSIFY_CARD_MENU_CATEGORY_REFERENCE_ROI = (0, 840, 700, 240)
CLASSIFY_CARD_MENU_CATEGORY_RELATIVE_ROI = reference_rect_to_relative_roi(
    CLASSIFY_CARD_MENU_CATEGORY_REFERENCE_ROI,
    FHD_1080,
)
# 料理页：左侧标题与食谱/材料区域（配方模板搜索区同源）。
CLASSIFY_COOKING_TITLE_REFERENCE_ROI = (100, 0, 400, 110)
CLASSIFY_COOKING_TITLE_RELATIVE_ROI = reference_rect_to_relative_roi(
    CLASSIFY_COOKING_TITLE_REFERENCE_ROI,
    FHD_1080,
)
CLASSIFY_COOKING_MATERIALS_REFERENCE_ROI = (250, 70, 500, 300)
CLASSIFY_COOKING_MATERIALS_RELATIVE_ROI = reference_rect_to_relative_roi(
    CLASSIFY_COOKING_MATERIALS_REFERENCE_ROI,
    FHD_1080,
)
QUICK_SWITCH_PAGE_KEYWORDS = QUICK_SWITCH_PAGE_LABELS
STORY_CATEGORY_POINT = (557 / 1920, 877 / 1080)
STORY_CATEGORY_HIGHLIGHT_REGION = (
    445 / 1920,
    840 / 1080,
    670 / 1920,
    915 / 1080,
)
STORY_CATEGORY_HIGHLIGHT_MIN_RATIO = 0.05
QUICK_SWITCH_CARTRIDGE_REGION = (0.0, 908 / 1080, 1.0, 1.0)
QUICK_SWITCH_SCROLL_FOCUS_POINT = (43 / 1920, 974 / 1080)
QUICK_SWITCH_SCROLL_POINT = QUICK_SWITCH_SCROLL_FOCUS_POINT
QUICK_SWITCH_SCROLL_RESET_AMOUNT = -1
QUICK_SWITCH_SCROLL_RESET_COUNT = 24
QUICK_SWITCH_SCROLL_UP_AMOUNT = 1
QUICK_SWITCH_SCROLL_UP_COUNT = 2
QUICK_SWITCH_SCROLL_SCAN_STEPS = 16
QUICK_SWITCH_SCROLL_INTERVAL = 0.08
QUICK_SWITCH_SCROLL_SETTLE_SECONDS = 0.35
PROBE_QUICK_SWITCH_SCROLL_POINT = QUICK_SWITCH_SCROLL_FOCUS_POINT
PROBE_QUICK_SWITCH_SCROLL_AMOUNT = 1
PROBE_QUICK_SWITCH_SCROLL_COUNT = 5
PROBE_QUICK_SWITCH_SCROLL_STEPS = 30
PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS = 0.1
PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS = 0.5
PROBE_STORY_BADGE_CONFIRM_SECONDS = 0.4
STORY_BADGE_TEMPLATE_SCORE = 0.95
STORY_BADGE_PIXEL_SCORE = 0.95
STORY_BADGE_MIN_MARGIN = 0.05
# Tutorial-video frames can preserve a strong badge structure while H.264
# chroma/block compression slightly lowers whole-pixel similarity and the
# separation from the next number.  Keep the original strict path, and allow
# this narrower recovery path only when all structural scores stay high.
STORY_BADGE_ENCODED_TEMPLATE_SCORE = 0.98
STORY_BADGE_ENCODED_PIXEL_SCORE = 0.94
STORY_BADGE_ENCODED_ZNCC_SCORE = 0.88
STORY_BADGE_ENCODED_MIN_MARGIN = 0.04
STORY_BADGE_CANDIDATE_SCORE = 0.70
STORY_BADGE_CANDIDATE_PIXEL_SCORE = 0.70
STORY_BADGE_CANDIDATE_ZNCC_SCORE = 0.50
STORY_BADGE_OCR_MIN_CONFIDENCE = 0.75
STORY_BADGE_CENTER_REGION = (0.0, 919 / 1080, 1.0, 953 / 1080)
STORY_BADGE_OCR_INNER_RADIUS_RATIO = 12.5 / 29
STORY_BADGE_OCR_BINARY_THRESHOLD = 140
STORY_BADGE_OCR_INNER_HEIGHT = 208
STORY_BADGE_OCR_VERTICAL_BORDER = 32
STORY_BADGE_OCR_HORIZONTAL_BORDER = 40
STORY_BADGE_CLUSTER_RADIUS = 12
Q_SP6_STORY_NUMBER = 6
STORY_BADGE_SPECS = tuple(
    (
        number,
        TemplateSpec(
            name=f"剧情游戏卡{number}角标",
            file_name=(f"quick_switch_cartridges/story_cartridge_badge_{number:02d}.png"),
            threshold=STORY_BADGE_TEMPLATE_SCORE,
            relative_roi=QUICK_SWITCH_CARTRIDGE_REGION,
            min_pixel_score=STORY_BADGE_CANDIDATE_PIXEL_SCORE,
            min_zncc_score=STORY_BADGE_CANDIDATE_ZNCC_SCORE,
            candidate_center_roi=STORY_BADGE_CENTER_REGION,
        ),
    )
    for number in range(1, 21)
)
BARGAIN_POINT = (191 / 1920, 900 / 1080)
BARGAIN_CONFIRM_POINT = (1047 / 1920, 652 / 1080)
# 砍价确认后必须等待商店页 OCR 稳定出现，不能依赖固定延时。砍价弹窗未关闭时
# 整帧 OCR 仍会读到“仓库/严加管理”，因此同时排除砍价弹窗专有文字。
BARGAIN_SHOP_CONFIRM_POPUP_KEYWORD = "砍价成功率"
BARGAIN_SHOP_CONFIRM_STABLE_HITS = 2
DISCOUNT_SHOP_CLOSE_DIALOG_REGION = (
    700 / 1920,
    382 / 1080,
    1220 / 1920,
    694 / 1080,
)
DISCOUNT_SHOP_CLOSE_KEYWORDS = (
    "折扣商店结束",
    "是否关闭折扣商店",
)
DISCOUNT_SHOP_CLOSE_POINT = (1045 / 1920, 639 / 1080)
CHAPTER_HOME_POINT = (1797 / 1920, 63 / 1080)
DISCOUNT_SHOP_CLOSE_TIMEOUT = 5.0
RETURN_HOME_TIMEOUT = 10.0
RETURN_HOME_ANNOUNCEMENT_MAX_CLICKS = 3
RETURN_HOME_ANNOUNCEMENT_OCR_INTERVAL = 0.35
RETURN_HOME_ANNOUNCEMENT_OCR_REGION = (
    360 / 1920,
    180 / 1080,
    1560 / 1920,
    900 / 1080,
)
RETURN_HOME_ANNOUNCEMENT_KEYWORD_GROUPS = (
    ("更新", "抢先看"),
    ("7天内不再显示", "前往查看"),
)
SHOP_ENTRY_CLICK_RETRIES = 3
SHOP_ENTRY_CLICK_INTERVAL = 0.5
SHOP_CLOSE_CLICK_RETRIES = 2
SHOP_CLOSE_CLICK_INTERVAL = 0.3
# 关闭按钮与主页按钮是稳定 UI 控件：优先用模板命中后点击识别中心，
# 模板未通过时才回退到已标定的相对坐标（1920×1080 参考点）。
DISCOUNT_SHOP_CLOSE_CONTROL_REFERENCE_POINT = (82, 36)
DISCOUNT_SHOP_CLOSE_CONTROL_TEMPLATES = (
    TemplateSpec(
        "折扣商店关闭按钮",
        "image/EquipMenuQuit.png",
        0.85,
        min_pixel_score=0.85,
        min_zncc_score=0.85,
        relative_roi=(0.0, 0.0, 0.2, 0.18),
    ),
    TemplateSpec(
        "折扣商店关闭小按钮",
        "image/equipclose.png",
        0.85,
        min_pixel_score=0.85,
        min_zncc_score=0.85,
        relative_roi=(0.0, 0.0, 0.2, 0.18),
    ),
)
# Relative template ROIs are always fractional left/top/right/bottom bounds.
CHAPTER_HOME_RELATIVE_ROI = (0.86, 0.0, 1.0, 0.18)
CHAPTER_HOME_TEMPLATES = (
    TemplateSpec(
        "箱庭主页按钮",
        "image/MapUI_HomeBtum.png",
        0.85,
        min_pixel_score=0.85,
        min_zncc_score=0.85,
        relative_roi=CHAPTER_HOME_RELATIVE_ROI,
    ),
    TemplateSpec(
        "箱庭主页按钮E3",
        "image/UI_HomeButm_E3.png",
        0.85,
        min_pixel_score=0.85,
        min_zncc_score=0.85,
        relative_roi=CHAPTER_HOME_RELATIVE_ROI,
    ),
    TemplateSpec(
        "箱庭主页按钮GE",
        "image/UI_HomeButm_GE.png",
        0.85,
        min_pixel_score=0.85,
        min_zncc_score=0.85,
        relative_roi=CHAPTER_HOME_RELATIVE_ROI,
    ),
)
HOME_BRIGHTNESS_THRESHOLD = 0.75
STORY_SANDBOX_STABLE_HITS = 2
STORY_SANDBOX_SWITCH_WINDOW = 5
STORY_SANDBOX_SWITCH_WINDOW_HITS = 3
SANDBOX_TEMPLATES = (
    TemplateSpec(
        "箱庭小地图缩放按钮",
        "image/UI_miniMap_B.png",
        0.90,
        min_pixel_score=0.90,
        minimum_safe_threshold=0.90,
        min_zncc_score=0.90,
    ),
    QUICK_SWITCH_TEMPLATE,
)
SANDBOX_SKILL_GROUP_TEMPLATE_SCORE = 0.95
# Structural gates retain candidates in complex backgrounds; HSV semantics
# below decide whether a slot is selected or unselected.
SANDBOX_SKILL_GROUP_PIXEL_SCORE = 0.80
SANDBOX_SKILL_GROUP_ZNCC_SCORE = None
SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO = 0.20
SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO = 0.10
SANDBOX_SKILL_GROUP_SCALE_RATIOS = (0.90, 0.95, 1.0, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35)
SANDBOX_SKILL_GROUP_SEARCH_ROI = (0.80, 0.85, 0.96, 1.0)
SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER = SKILL_GROUP_CENTERS_REFERENCE[1]
SANDBOX_SKILL_SLOT_2_REFERENCE_CENTER = SKILL_GROUP_CENTERS_REFERENCE[2]
SANDBOX_SKILL_SLOT_1_RELATIVE_POINT = (
    SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER[0] / FHD_1080.width,
    SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER[1] / FHD_1080.height,
)
SANDBOX_SKILL_SLOT_1_CENTER_ROI = (
    1620 / FHD_1080.width,
    950 / FHD_1080.height,
    1710 / FHD_1080.width,
    1060 / FHD_1080.height,
)
SANDBOX_SKILL_SLOT_2_CENTER_ROI = (
    1710 / FHD_1080.width,
    950 / FHD_1080.height,
    1800 / FHD_1080.width,
    1060 / FHD_1080.height,
)


def _sandbox_skill_template(
    name: str,
    file_name: str,
    candidate_center_roi: tuple[float, float, float, float],
) -> TemplateSpec:
    return TemplateSpec(
        name,
        f"image/green/{file_name}",
        SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
        green_mask=True,
        relative_roi=SANDBOX_SKILL_GROUP_SEARCH_ROI,
        scale_ratios=SANDBOX_SKILL_GROUP_SCALE_RATIOS,
        candidate_center_roi=candidate_center_roi,
        min_pixel_score=SANDBOX_SKILL_GROUP_PIXEL_SCORE,
        minimum_safe_threshold=SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
        min_zncc_score=SANDBOX_SKILL_GROUP_ZNCC_SCORE,
    )


SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE = _sandbox_skill_template(
    "技能组1号选中",
    "SandboxSkillSlot1AvailableGE.png",
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
)
SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE = _sandbox_skill_template(
    "技能组2号未选中",
    "SandboxSkillSlot2UsedGE.png",
    SANDBOX_SKILL_SLOT_2_CENTER_ROI,
)
SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE = _sandbox_skill_template(
    "技能组2号选中",
    "SandboxSkillSlot2AvailableGE.png",
    SANDBOX_SKILL_SLOT_2_CENTER_ROI,
)
SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE = _sandbox_skill_template(
    "技能组1号未选中",
    "SandboxSkillSlot1UsedGE.png",
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
)
SANDBOX_SKILL_STATE_TEMPLATES = (
    SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
)
LOADING_TEMPLATE = TemplateSpec("加载页面", "image/UI_loading_black.png", 0.70)
TRADE_MERCHANT_CONTEXT_TEMPLATE = TemplateSpec(
    "商人对话", "image/Mer_Dialog_TalMed.png", 0.72, roi=(930, 15, 280, 70)
)
MERCHANT_CLICK_LOCATION_TEMPLATE = TemplateSpec(
    "商人点击位置",
    "MerchantClickLocation.png",
    0.90,
    green_mask=False,
    # This crop is calibrated at 1920x1080 and lives at the template-assets
    # root, whose baseline is resolution-aware at 1.0.
    scale_ratios=(0.90, 0.95, 1.0, 1.05, 1.10),
    min_pixel_score=0.90,
    minimum_safe_threshold=0.90,
    min_zncc_score=0.90,
)
MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE = "未识别到MerchantClickLocation.png"
MERCHANT_DIALOG_CONFIRM_TIMEOUT = 3.0
SANDBOX_NAVIGATION_PIN_TEMPLATE = TemplateSpec(
    "箱庭小地图图钉",
    "image/pin.png",
    0.72,
    candidate_center_roi=(100 / 1280, 70 / 720, 210 / 1280, 180 / 720),
)
SANDBOX_NAVIGATION_RUN_TEMPLATE = TemplateSpec(
    "箱庭小地图自动移动",
    "image/green/Run.png",
    0.72,
    green_mask=True,
    candidate_center_roi=(100 / 1280, 70 / 720, 210 / 1280, 180 / 720),
)
SANDBOX_NAVIGATION_OPEN_TEMPLATES = (
    SANDBOX_NAVIGATION_PIN_TEMPLATE,
    SANDBOX_NAVIGATION_RUN_TEMPLATE,
)
SANDBOX_NAVIGATION_PAGE_KEYWORDS = ("在战场中查看", "探索", "世界地图")
SANDBOX_TELEPORT_SKILL_FAILURE_GROUPS = (
    ("无法在", "魔法阵附近"),
    ("无法在", "该地点"),
    ("魔法阵附近", "天赋技能"),
    ("该地点", "天赋技能"),
)
SANDBOX_INTERACTION_PROBE_TIMEOUT = 1.5
SANDBOX_INTERACTION_PROBE_INTERVAL = 0.25
SANDBOX_NAVIGATION_OPEN_TIMEOUT = 5.0
SANDBOX_NAVIGATION_OPEN_SETTLE_SECONDS = 2.5
SANDBOX_NAVIGATION_MAP_TIMEOUT = 8.0
SANDBOX_NAVIGATION_TELEPORT_SETTLE_SECONDS = 3.0
SANDBOX_NAVIGATION_CONFIRM_TIMEOUT = 4.0
SANDBOX_NAVIGATION_WALK_TIMEOUT = 45.0
SANDBOX_NAVIGATION_OCR_INTERVAL = 0.25


@dataclass(frozen=True)
class StoryBadgeCandidate:
    number: int
    result: MatchResult

    @property
    def discrimination_score(self) -> float:
        if self.result.zncc_score > -1.0:
            return self.result.zncc_score
        return self.result.score


@dataclass(frozen=True)
class StoryBadgeDetection:
    best: StoryBadgeCandidate
    runner_up: StoryBadgeCandidate | None
    ocr_text: str = ""
    ocr_number: int | None = None

    @property
    def margin(self) -> float:
        if self.runner_up is None:
            return -1.0
        return self.best.discrimination_score - self.runner_up.discrimination_score


@dataclass(frozen=True)
class LocatedStoryCard:
    card: CardSpec
    frame: np.ndarray
    badge: StoryBadgeDetection


@dataclass(frozen=True)
class ProbedStoryCard:
    located: LocatedStoryCard
    completion: StoryCardCompletion


@dataclass(frozen=True)
class AreaMapContext:
    frame_shape: tuple[int, ...]
    raw_text: str
    normalized_text: str
    map_page_mode: MapPageMode
    candidate_target_keys: tuple[str, ...]
    resolved_target_key: str | None
    left_arrow: MatchResult | None
    right_arrow: MatchResult | None
    teleports: tuple[MatchResult, ...]
    overlap_arrow: MatchResult | None
    back_button: MatchResult | None
    confirmation_text: str = ""

    @property
    def is_area_map(self) -> bool:
        return self.map_page_mode.is_teleport_map


@dataclass(frozen=True)
class MapPageDetection:
    mode: MapPageMode
    header_text: str = ""
    footer_text: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxConfirmation:
    """One-frame evidence used to confirm a story-card sandbox."""

    map_signal_hits: int
    skill_state_hits: int
    action_hits: int
    skill_group: int | None

    @property
    def passed(self) -> bool:
        return (
            self.map_signal_hits >= 1
            and self.skill_state_hits >= 2
            and self.skill_group in {1, 2}
            and self.action_hits >= 3
        )


HAND_TEMPLATE = TemplateSpec(
    "传送阵交互按钮",
    "image/green/IcoHand.png",
    0.95,
    green_mask=True,
    min_pixel_score=0.90,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.85,
)
SANDBOX_TELEPORT_SKILL_TEMPLATE = TemplateSpec(
    "箱庭5号传送阵技能",
    "image/green/Skill3-4GE.png",
    0.95,
    green_mask=True,
    relative_roi=ACTION_SLOT_RELATIVE_ROIS["teleport"],
    candidate_center_roi=ACTION_SLOT_CENTER_RELATIVE_ROIS["teleport"],
    min_pixel_score=0.85,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.85,
)
SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER = ACTION_SLOT_CENTERS_REFERENCE["teleport"]
SANDBOX_TELEPORT_SKILL_RELATIVE_POINT = (
    SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER[0] / FHD_1080.width,
    SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER[1] / FHD_1080.height,
)
SANDBOX_CONFIRM_ACTION_TEMPLATES = (
    ("吸收", ABSORB_ICON.template),
    ("探查", SEARCH_ICON.template),
    ("召集", SUMMON_ICON.template),
    ("压制", SUBDUE_ICON.template),
    ("传送阵技能", SANDBOX_TELEPORT_SKILL_TEMPLATE),
)
SANDBOX_MAP_TELEPORT_TEMPLATE = TemplateSpec(
    "箱庭地图传送阵模板",
    "image/green/SandboxNviTpCircleMapGE.png",
    0.72,
)
TELEPORT_MAP_HEADER_OCR_RELATIVE_ROI = (180 / 1920, 0.0, 900 / 1920, 110 / 1080)
SANDBOX_LARGE_MAP_FOOTER_OCR_RELATIVE_ROI = (
    100 / 1920,
    900 / 1080,
    1850 / 1920,
    1070 / 1080,
)
TELEPORT_MAP_DIRECT_HEADER_TEMPLATE = TemplateSpec(
    "交互直传页图标",
    "TeleportMapDirectHeader.png",
    0.95,
    relative_roi=(0.09, 0.01, 0.15, 0.10),
    scale_ratios=(0.95, 1.0, 1.05),
    min_pixel_score=0.90,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.90,
)
TELEPORT_MAP_GENERATE_HEADER_TEMPLATE = TemplateSpec(
    "技能生成页图标",
    "image/Skill3-1.png",
    0.95,
    relative_roi=(0.09, 0.01, 0.15, 0.10),
    scale_ratios=(0.78, 0.79, 0.80, 0.81, 0.82),
    min_pixel_score=0.88,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.90,
)
SANDBOX_LARGE_MAP_LEFT_TEMPLATE = TemplateSpec(
    "箱庭大地图左箭头",
    "image/MapLeft.png",
    0.95,
    relative_roi=(0.07, 0.35, 0.20, 0.65),
    scale_ratios=(0.95, 0.975, 1.0, 1.025, 1.05),
    min_pixel_score=0.90,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.90,
)
SANDBOX_LARGE_MAP_RIGHT_TEMPLATE = TemplateSpec(
    "箱庭大地图右箭头",
    "image/MapRight.png",
    0.95,
    relative_roi=(0.35, 0.35, 0.55, 0.65),
    scale_ratios=(0.95, 0.975, 1.0, 1.025, 1.05),
    min_pixel_score=0.90,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.90,
)
TELEPORT_MAP_FORWARD_TEMPLATE = TemplateSpec(
    "传送阵地图向前",
    "image/green/TpMapLeft.png",
    0.95,
    min_pixel_score=0.85,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.90,
)
TELEPORT_MAP_BACKWARD_TEMPLATE = TemplateSpec(
    "传送阵地图向后",
    "image/green/TpMapRight.png",
    0.95,
    min_pixel_score=0.85,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.90,
)
# Two 1920×1080 enabled teleport-map samples bottomed out at
# 0.991/0.939/0.958 with the new inner-circle-only template. The template
# excludes the activated outer halo and is intentionally separate from the
# smaller Nvi marker used by the left-top navigation fallback.
TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE = TemplateSpec(
    "传送阵地图传送阵",
    "image/green/TpCircleMapNewGE.png",
    0.95,
    min_pixel_score=0.90,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.85,
)
TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES = (TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,)
TELEPORT_MAP_SKILL_TEMPLATE = TemplateSpec(
    "传送阵地图传送技能",
    "image/green/TpSkillMapGE.png",
    0.95,
    min_pixel_score=0.90,
    minimum_safe_threshold=0.95,
    min_zncc_score=0.85,
)
OVERLAP_ARROW_TEMPLATE = TemplateSpec(
    "传送阵重叠箭头",
    "image/green/map_tcArrowGE.png",
    0.72,
)
AREA_MAP_BACK_TEMPLATE = TemplateSpec(
    "传送阵地图返回",
    "image/green/BackButGe.png",
    0.88,
    relative_roi=(0.03, 0.0, 0.13, 0.12),
    scale_ratios=(0.70, 0.75, 0.80),
    min_pixel_score=0.85,
    minimum_safe_threshold=0.88,
)
AREA_MAP_SCAN_LIMIT = 24
AREA_MAP_CHANGE_TIMEOUT = 3.0
AREA_MAP_CHANGE_INTERVAL = 0.25
AREA_MAP_CLICK_SETTLE_SECONDS = 0.5
AREA_MAP_TELEPORT_CLUSTER_RADIUS = 24
SANDBOX_MAP_SETTLE_SECONDS = 0.5
SANDBOX_SKILL_GROUP_SWITCH_SETTLE_SECONDS = 0.5
SANDBOX_MAP_TELEPORT_TIMEOUT = 30.0
SANDBOX_TELEPORT_SKILL_TIMEOUT = 5.0
SANDBOX_TELEPORT_SKILL_POLL_INTERVAL = 0.25
TELEPORT_INTERACTION_TIMEOUT = 30.0
TELEPORT_INTERACTION_CLICK_DELAY = 0.5
TELEPORT_INTERACTION_POLL_INTERVAL = 0.4
TELEPORT_MAP_OPEN_TIMEOUT = 10.0
MAP_PAGE_MODE_STABLE_HITS = 2
TELEPORT_MAP_TRAVEL_SETTLE_SECONDS = 4.5
TELEPORT_GENERATION_OCR_KEYWORDS = ("生成魔法阵", "取消", "生成")
TELEPORT_GENERATION_OCR_TIMEOUT = 8.0
TELEPORT_GENERATION_OCR_INTERVAL = 0.25
TELEPORT_MAP_FIRST_PAGE_LIMIT = AREA_MAP_SCAN_LIMIT
AREA_MAP_REFERENCE_SIZE = FHD_1080.size
AREA_MAP_OPEN_REFERENCE_POINT = (289, 253)
AREA_MAP_OPEN_RELATIVE_POINT = (
    AREA_MAP_OPEN_REFERENCE_POINT[0] / AREA_MAP_REFERENCE_SIZE[0],
    AREA_MAP_OPEN_REFERENCE_POINT[1] / AREA_MAP_REFERENCE_SIZE[1],
)
TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI = (654, 946, 1268, 1021)
TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI = (
    TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI[0] / AREA_MAP_REFERENCE_SIZE[0],
    TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI[1] / AREA_MAP_REFERENCE_SIZE[1],
    TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI[2] / AREA_MAP_REFERENCE_SIZE[0],
    TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI[3] / AREA_MAP_REFERENCE_SIZE[1],
)
TELEPORT_MAP_RETURN_REFERENCE_POINT = (136, 52)
TELEPORT_MAP_RETURN_RELATIVE_POINT = (
    TELEPORT_MAP_RETURN_REFERENCE_POINT[0] / AREA_MAP_REFERENCE_SIZE[0],
    TELEPORT_MAP_RETURN_REFERENCE_POINT[1] / AREA_MAP_REFERENCE_SIZE[1],
)
SANDBOX_LARGE_MAP_RETURN_REFERENCE_POINT = (172, 50)
SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT = (
    SANDBOX_LARGE_MAP_RETURN_REFERENCE_POINT[0] / AREA_MAP_REFERENCE_SIZE[0],
    SANDBOX_LARGE_MAP_RETURN_REFERENCE_POINT[1] / AREA_MAP_REFERENCE_SIZE[1],
)
AREA_MAP_TELEPORT_BRIGHT_RADIUS_RATIO = 24 / 52
AREA_MAP_TELEPORT_BRIGHT_MINIMUM_GRAY = 200
AREA_MAP_TELEPORT_BRIGHT_MAXIMUM_SPREAD = 35
AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO = 0.10
FIRST_CARD_INSERT_REGION = (413, 481, 440, 132)
FIRST_CARD_SKIP_TEMPLATE = TemplateSpec(
    "首次卡带跳过",
    "image/UI_Skip.png",
    0.72,
    roi=(915, 9, 265, 68),
)
FIRST_CARD_CONFIRM_REGION = (626, 368, 186, 293)

