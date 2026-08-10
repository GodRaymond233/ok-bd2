from __future__ import annotations

from src.tasks.map_trade.calendar import (
    PriceCalendarClient,
)
from src.tasks.map_trade.models import (
    DEFAULT_RECIPES,
    MERCHANT_CARD_ID,
    RECIPE_TEMPLATES,
    TemplateSpec,
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
    BUY_TO_SELL_OCR_INTERVAL,
    BUY_TO_SELL_POST_CLICK_DELAY,
    BUY_TO_SELL_PRE_CLICK_DELAY,
    BUY_TO_SELL_SOLD_OUT_KEYWORD,
    BUY_TO_SELL_TIMEOUT,
    CALENDAR_DIR,
    COOK_SUBMENU_TEMPLATE,
    PROJECT_ROOT,
    SALE_120_PERCENT_PATTERN,
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
    SALE_ITEM_NAME_LEFT_OFFSET_X,
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
    SHOP_CARTRIDGE_OCR_ROI,
    SHOP_CARTRIDGE_OCR_ROW_LINK_RADIUS,
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_ROW_CLUSTER_RADIUS,
    SHOP_CARTRIDGE_SCALE_RATIOS,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    SHOP_DOWN_SCROLL_INTERVAL,
    SHOP_FIRST_PAGE_MAX_UP_SCROLLS,
    SHOP_MODE_INTERVAL,
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
from src.tasks.map_trade.trader_pricing import PriceDiscoveryMixin
from src.tasks.map_trade.trader_sell import SellFlowMixin
from src.tasks.map_trade.vision import Vision


class Trader(
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

    def run_cooking(self) -> bool:
        every_run = str(self.task.config.get("料理制作周期", "每周")) == "每次"
        if not self.progress.should_cook(every_run=every_run):
            self.task.log_info("跑商：本周利润料理已经制作，跳过。")
            return True
        configured_recipes = self.task.config.get("5星料理", list(DEFAULT_RECIPES))
        selected = (
            split_items(configured_recipes)
            if isinstance(configured_recipes, str)
            else tuple(configured_recipes)
        )
        if not selected:
            self.task.log_info("跑商：未选择利润料理，跳过制作。")
            self.progress.mark_cooking_complete()
            return True

        entered = self.navigator.select_card(MERCHANT_CARD_ID)
        if not entered.success:
            self.task.log_warning(f"料理：{entered.message}")
            return False
        self.vision.click_reference(1203, 664, after_sleep=0.8)
        if not self.vision.click_ocr([r"料理"], roi=(80, 540, 1100, 120), name="料理入口"):
            self.task.log_warning("料理：技能菜单未识别到料理入口。")
            return False
        if self.vision.wait_ocr([r"料理"], 8, "料理菜单", roi=(150, 0, 300, 100)) is None:
            return False

        insurance = bool(self.task.config.get("料理保险", True))
        completed = 0
        for recipe in selected:
            file_name = RECIPE_TEMPLATES.get(str(recipe))
            if not file_name:
                continue
            spec = TemplateSpec(f"料理-{recipe}", file_name, 0.70, roi=(250, 70, 750, 560))
            match = None
            for attempt in range(3):
                frame = self.vision.capture()
                candidate = self.vision.match(frame, spec)
                if candidate.score >= self.vision.threshold_for(spec):
                    match = candidate
                    self.vision.click_client(candidate.center, frame.shape, after_sleep=0.7)
                    break
                if attempt == 0:
                    self.vision.drag_reference(
                        (780, 560), (780, 170), duration=0.55, after_sleep=0.4
                    )
                else:
                    self.vision.drag_reference(
                        (780, 170), (780, 560), duration=0.55, after_sleep=0.4
                    )
            if match is None:
                self.task.log_warning(f"料理：未找到 {recipe}，跳过。")
                continue
            if self.vision.wait_template(COOK_SUBMENU_TEMPLATE, 5) is None:
                continue
            if not insurance:
                self.vision.click_reference(576, 563, after_sleep=0.3)
            self.vision.click_reference(930, 630, after_sleep=0.8)
            self.vision.wait_ocr([r"制作中"], 3, f"{recipe}制作状态", roi=(480, 300, 320, 180))
            self.vision.wait_template(COOK_SUBMENU_TEMPLATE, 20)
            self.vision.click_reference(82, 36, after_sleep=0.8)
            completed += 1

        if completed:
            self.progress.mark_cooking_complete()
        return completed > 0

    def run_trade(self) -> bool:
        success = True
        if bool(self.task.config.get("买", True)):
            success = self.run_buy() and success
        if bool(self.task.config.get("卖", True)):
            success = self.run_sell() and success
        return success

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
