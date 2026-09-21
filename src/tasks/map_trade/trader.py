from __future__ import annotations

from src.tasks.map_trade.calendar import (
    PriceCalendarClient,
)
from src.tasks.map_trade.models import CalendarEntry
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.trader_buy import BuyFlowMixin
from src.tasks.map_trade.trader_cartridge import ShopCartridgeNavigationMixin
from src.tasks.map_trade.trader_constants import (
    BUY_CONFIRM_DIALOG_REGION,
    CALENDAR_DIR,
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    STAR_PIXEL_THRESHOLD,
    STAR_POST_CLICK_DELAY,
    STAR_ROI_HALF_SIZE_X,
    STAR_ROI_HALF_SIZE_Y,
    STAR_TEMPLATE_THRESHOLD,
)
from src.tasks.map_trade.trader_cooking import CookingFlowMixin
from src.tasks.map_trade.trader_pricing import PriceDiscoveryMixin
from src.tasks.map_trade.trader_sell import SellFlowMixin
from src.tasks.map_trade.vision import Vision

__all__ = [
    "BUY_CONFIRM_DIALOG_REGION",
    "SHOP_CARTRIDGE_RECOGNITION_REGION",
    "SHOP_CARTRIDGE_SCROLL_POINT",
    "SHOP_CARTRIDGE_SCROLL_REGION",
    "STAR_PIXEL_THRESHOLD",
    "STAR_POST_CLICK_DELAY",
    "STAR_ROI_HALF_SIZE_X",
    "STAR_ROI_HALF_SIZE_Y",
    "STAR_TEMPLATE_THRESHOLD",
]


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
        self._last_sale_name_seen = False
        self._last_sale_ocr_output = False
        self._last_sale_page_empty = False
        self._sale_entries_override: list[CalendarEntry] | None = None
        self._last_sale_toast_id: int | None = None
        self._sale_title_catalog_cache = None
        self.calendar_client = PriceCalendarClient(
            bundled_path=CALENDAR_DIR / "price_calendar.v1.json",
            sources_path=CALENDAR_DIR / "calendar_sources.json",
        )

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
