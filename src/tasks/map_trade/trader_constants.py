from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.tasks.map_trade.models import MatchResult, TemplateSpec

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CALENDAR_DIR = PROJECT_ROOT / "assets" / "map_trade"
SHOP_CARTRIDGE_SCROLL_REGION = (228 / 1920, 117 / 1080, 463 / 1920, 959 / 1080)
SHOP_CARTRIDGE_RECOGNITION_REGION = (200 / 1920, 70 / 1080, 500 / 1920, 1.0)
SHOP_CARTRIDGE_SCROLL_POINT = (
    (SHOP_CARTRIDGE_SCROLL_REGION[0] + SHOP_CARTRIDGE_SCROLL_REGION[2]) / 2,
    (SHOP_CARTRIDGE_SCROLL_REGION[1] + SHOP_CARTRIDGE_SCROLL_REGION[3]) / 2,
)
SHOP_CARTRIDGE_SCALE_RATIOS = (0.95, 1.0, 1.05)
SHOP_CARTRIDGE_OCR_ROI = (200, 70, 300, 1010)
SHOP_CARTRIDGE_CANDIDATE_SCORE = 0.70
SHOP_CARTRIDGE_CONFIRM_SCORE = 0.78
SHOP_CARTRIDGE_MIN_MARGIN = 0.08
SHOP_CARTRIDGE_OCR_MIN_CONFIDENCE = 0.85
SHOP_CARTRIDGE_NAME_MIN_SIMILARITY = 0.70
SHOP_CARTRIDGE_ROW_CLUSTER_RADIUS = 28
SHOP_CARTRIDGE_OCR_ROW_LINK_RADIUS = 42
SHOP_CARTRIDGE_CATEGORY_PATTERN = re.compile(r"(剧情|角色|活动)游戏卡\s*(\d+)")
SHOP_CARTRIDGE_CATEGORY_PREFIX = {"剧情": "S", "角色": "R", "活动": "E"}
SHOP_FIRST_PAGE_MAX_UP_SCROLLS = 40
SHOP_UP_SCROLL_RECOGNITION_INTERVAL = 0.5
SHOP_DOWN_SCROLL_INTERVAL = 0.1
STAR_TEMPLATE_FILE = "shop/cartridges/star_gray.png"
# 灰星搜索区域按 1920×1080 参考像素定义并转为相对比例，随客户端分辨率同比缩放。
# 720p 实机测量中灰星中心相对标定点偏移约 (-7,+13) 客户端像素，因此区域必须明显大于
# 旧 ±15 参考像素；4K 下同一相对偏移约 (-21,+38) 像素，相对比例可同时覆盖 720p 与 4K。
STAR_ROI_HALF_SIZE_X = 42
STAR_ROI_HALF_SIZE_Y = 52
STAR_TEMPLATE_THRESHOLD = 0.82
STAR_PIXEL_THRESHOLD = 0.90
STAR_VERIFY_ATTEMPTS = 5
STAR_VERIFY_INTERVAL = 0.25
STAR_POST_CLICK_DELAY = 1.0
STAR_REMOVE_TOAST_KEYWORD = "从收藏中移除"
STAR_ADD_TOAST_KEYWORD = "加入收藏"
BUY_ALL_FAVORITES_KEYWORD = "购买全部收藏"
BUY_ALL_FAVORITES_STABLE_HITS = 2
BUY_ALL_FAVORITES_TIMEOUT = 30.0
BUY_ALL_FAVORITES_INTERVAL = 0.25
BUY_CONFIRM_DIALOG_REGION = (
    701 / 1920,
    328 / 1080,
    1219 / 1920,
    753 / 1080,
)
BUY_CONFIRM_POINT = (1045 / 1920, 697 / 1080)
BUY_CONFIRM_KEYWORDS = (
    "一键购买全部收藏",
    "是否购买所有加入收藏的商品",
)
BUY_CONFIRM_TIMEOUT = 30.0
BUY_CONFIRM_INTERVAL = 0.25
BUY_CONFIRM_PRE_CLICK_DELAY = 0.8
BUY_CONFIRM_POST_CLICK_DELAY = 0.8
BUY_TO_SELL_SOLD_OUT_TEMPLATE = TemplateSpec(
    "买后售罄",
    "soled-out.png",
    0.84,
    min_pixel_score=0.93,
    min_zncc_score=0.84,
    minimum_safe_threshold=0.84,
    relative_roi=(360 / 1920, 140 / 1080, 1620 / 1920, 510 / 1080),
)
BUY_TO_SELL_SOLD_OUT_STABLE_HITS = 2
BUY_TO_SELL_TIMEOUT = 30.0
BUY_TO_SELL_INTERVAL = 0.25
BUY_TO_SELL_PRE_CLICK_DELAY = 0.5
BUY_TO_SELL_POST_CLICK_DELAY = 0.5
SHOP_MODE_TITLE_REGION = (
    226 / 1920,
    24 / 1080,
    359 / 1920,
    80 / 1080,
)
SELL_MODE_POINT = (173 / 1920, 250 / 1080)
SHOP_MODE_TIMEOUT = 4.0
SHOP_MODE_INTERVAL = 0.25
SHOP_MODE_SWITCH_MAX_CLICKS = 3
# 卖：在对应卡带页全画面 OCR 定位商品名与 120% 溢价，不再依赖排序后首格固定位置。
# 商品名识别框中心向左偏移量（1920×1080 参考像素，运行时按客户区宽度同比缩放）。
SALE_ITEM_NAME_LEFT_OFFSET_X = 115
# 全帧 OCR 目标高度：1920×1080 实测 720 缩放下 ↑120% 会被误读成 41209，
# 原生 1080 也误读为 4120%；900 可稳定读出 120%。
SALE_FULL_PAGE_OCR_TARGET_HEIGHT = 900
SALE_120_PERCENT_PATTERN = re.compile(r"120\s*%")
SALE_DIALOG_REGION = (
    470 / 1920,
    294 / 1080,
    1450 / 1920,
    785 / 1080,
)
SALE_MIN_POINT = (677 / 1920, 721 / 1080)
SALE_PLUS_TEN_POINT = (789 / 1920, 723 / 1080)
SALE_MAX_POINT = (903 / 1920, 724 / 1080)
SALE_CONFIRM_POINT = (1312 / 1920, 728 / 1080)
SALE_CLOSE_POINT = (1420 / 1920, 323 / 1080)
SALE_SLIDER_REGION = (
    552 / 1920,
    647 / 1080,
    912 / 1920,
    683 / 1080,
)
SALE_DIALOG_TITLE_REGION = (
    495 / 1920,
    310 / 1080,
    300 / 1920,
    80 / 1080,
)
SALE_DIALOG_TIMEOUT = 5.0
SALE_OCR_INTERVAL = 0.25
SALE_COMPLETION_TIMEOUT = 8.0
SALE_COMPLETION_INTERVAL = 0.25
SALE_COMPLETION_STABLE_HITS = 2
SALE_TOAST_ID_PATTERN = re.compile(r"交易差价\s*([0-9]+)\s*完成")
SALE_OWNED_PATTERN = re.compile(r"拥有[^0-9]{0,8}([0-9][0-9,，.]*)")
SALE_AVAILABLE_PATTERN = re.compile(r"可购买[^0-9]{0,8}([0-9][0-9,，.]*)")
COOK_SUBMENU_TEMPLATE = TemplateSpec(
    "料理子菜单",
    "image/green/UI_cooking_submenu.png",
    0.72,
    roi=(670, 540, 230, 180),
)


@dataclass(frozen=True)
class ShopCartridgeTemplateCandidate:
    shop_id: str
    result: MatchResult


@dataclass(frozen=True)
class ShopCartridgeOcrText:
    text: str
    confidence: float
    center: tuple[float, float]


@dataclass(frozen=True)
class ShopCartridgeOcrRow:
    shop_id: str
    category: ShopCartridgeOcrText
    chapter: ShopCartridgeOcrText | None
    name_similarity: float


@dataclass(frozen=True)
class ShopCartridgeDetection:
    best: ShopCartridgeTemplateCandidate
    runner_up: ShopCartridgeTemplateCandidate | None
    ocr: ShopCartridgeOcrRow | None

    @property
    def margin(self) -> float:
        if self.runner_up is None:
            return 1.0
        return self.best.result.score - self.runner_up.result.score


@dataclass(frozen=True)
class SaleItemCandidate:
    """One OCR-confirmed sale card on the current shop page."""

    center: tuple[int, int]
    name_box: object
    percent_box: object


def split_items(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        values = re.split(r"[,，;；\n]", value)
    else:
        values = value
    return tuple(str(item).strip() for item in values if str(item).strip())

