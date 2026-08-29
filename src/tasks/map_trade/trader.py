from __future__ import annotations

from src.tasks.map_trade.calendar import (
    PriceCalendarClient,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.trader_buy import BuyFlowMixin
from src.tasks.map_trade.trader_cartridge import ShopCartridgeNavigationMixin
from src.tasks.map_trade.trader_constants import (  # noqa: F401
    BUY_ALL_FAVORITES_INTERVAL,
    BUY_ALL_FAVORITES_KEYWORD,
    BUY_ALL_FAVORITES_STABLE_HITS,
    BUY_ALL_FAVORITES_TIMEOUT,
    BUY_CONFIRM_DIALOG_REGION,
    BUY_CONFIRM_INTERVAL,
    BUY_CONFIRM_KEYWORDS,
    BUY_CONFIRM_POINT,
    BUY_CONFIRM_POST_CLICK_DELAY,
    BUY_CONFIRM_PRE_CLICK_DELAY,
    BUY_CONFIRM_TIMEOUT,
    BUY_TO_SELL_INTERVAL,
    BUY_TO_SELL_POST_CLICK_DELAY,
    BUY_TO_SELL_PRE_CLICK_DELAY,
    BUY_TO_SELL_SOLD_OUT_STABLE_HITS,
    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
    BUY_TO_SELL_TIMEOUT,
    CALENDAR_DIR,
    COOK_SUBMENU_TEMPLATE,
    PROJECT_ROOT,
    SALE_120_PERCENT_MARKER_BETA_TEMPLATE,
    SALE_120_PERCENT_MARKER_MAX_RESULTS,
    SALE_120_PERCENT_MARKER_PEAK_RADIUS,
    SALE_120_PERCENT_MARKER_TEMPLATE,
    SALE_AVAILABLE_PATTERN,
    SALE_CLOSE_POINT,
    SALE_COMPLETION_INTERVAL,
    SALE_COMPLETION_STABLE_HITS,
    SALE_COMPLETION_TIMEOUT,
    SALE_CONFIRM_POINT,
    SALE_DIALOG_REGION,
    SALE_DIALOG_TIMEOUT,
    SALE_DIALOG_TITLE_REGION,
    SALE_FULL_PAGE_OCR_TARGET_HEIGHT,
    SALE_FULL_PAGE_OCR_TARGET_HEIGHTS,
    SALE_MARKER_MIN_MARGIN,
    SALE_MARKER_SEARCH_WIDTH,
    SALE_MARKER_VERTICAL_PADDING,
    SALE_MAX_POINT,
    SALE_MIN_POINT,
    SALE_OCR_INTERVAL,
    SALE_OWNED_PATTERN,
    SALE_PLUS_TEN_POINT,
    SALE_SLIDER_REGION,
    SALE_TOAST_ID_PATTERN,
    SELL_MODE_POINT,
    SHOP_CARTRIDGE_CANDIDATE_SCORE,
    SHOP_CARTRIDGE_CATEGORY_PATTERN,
    SHOP_CARTRIDGE_CATEGORY_PREFIX,
    SHOP_CARTRIDGE_CONFIRM_SCORE,
    SHOP_CARTRIDGE_MIN_MARGIN,
    SHOP_CARTRIDGE_NAME_MIN_SIMILARITY,
    SHOP_CARTRIDGE_OCR_MIN_CONFIDENCE,
    SHOP_CARTRIDGE_OCR_ROW_LINK_RADIUS,
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_ROW_CLUSTER_RADIUS,
    SHOP_CARTRIDGE_SCALE_RATIOS,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    SHOP_DOWN_SCROLL_INTERVAL,
    SHOP_FIRST_PAGE_MAX_UP_SCROLLS,
    SHOP_MODE_INTERVAL,
    SHOP_MODE_SWITCH_MAX_CLICKS,
    SHOP_MODE_TIMEOUT,
    SHOP_MODE_TITLE_REGION,
    SHOP_UP_SCROLL_RECOGNITION_INTERVAL,
    STAR_ADD_TOAST_KEYWORD,
    STAR_PIXEL_THRESHOLD,
    STAR_POST_CLICK_DELAY,
    STAR_REMOVE_TOAST_KEYWORD,
    STAR_ROI_HALF_SIZE_X,
    STAR_ROI_HALF_SIZE_Y,
    STAR_TEMPLATE_FILE,
    STAR_TEMPLATE_THRESHOLD,
    STAR_VERIFY_ATTEMPTS,
    STAR_VERIFY_INTERVAL,
    SaleItemCandidate,
    ShopCartridgeDetection,
    ShopCartridgeOcrRow,
    ShopCartridgeOcrText,
    ShopCartridgeTemplateCandidate,
    split_items,
)
from src.tasks.map_trade.trader_cooking import CookingFlowMixin
from src.tasks.map_trade.trader_pricing import PriceDiscoveryMixin
from src.tasks.map_trade.trader_sell import SellFlowMixin
from src.tasks.map_trade.vision import Vision


class Trader(
    CookingFlowMixin,
    BuyFlowMixin,
    SellFlowMixin,
    ShopCartridgeNavigationMixin,
    PriceDiscoveryMixin,
):
    def __init__(
        self,
        task,
        vision: Vision,
        navigator: Navigator,
        progress: ProgressStore,
    ) -> None:
        self.task = task
        self.vision = vision
        self.navigator = navigator
        self.progress = progress
        self.now_provider = progress.now_provider
        self.started_at = self._current_market_time()
        self._buy_completed_in_current_shop = False
        self._last_sale_unavailable = False
        self._last_sale_reason = ""
        self._last_sale_toast_id: int | None = None
        self.calendar_client = PriceCalendarClient(
            bundled_path=CALENDAR_DIR / "price_calendar.v1.json",
            sources_path=CALENDAR_DIR / "calendar_sources.json",
        )

    def run_trade(self) -> bool:
        phases = (("买", self.run_buy), ("卖", self.run_sell))
        for key, action in phases:
            if bool(self.task.config.get(key, True)) and not action():
                return False
        return True

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
