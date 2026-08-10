from __future__ import annotations

import re
from datetime import datetime

import numpy as np

from src.tasks.map_trade.data import (
    ITEM_ALIASES,
)
from src.tasks.map_trade.models import (
    DEFAULT_RECIPES,
    DEFAULT_SALE_WHITELIST,
    CalendarEntry,
)
from src.tasks.map_trade.progress import UTC_PLUS_8
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
from src.tasks.map_trade.vision import normalize_text


class PriceDiscoveryMixin:
    def discover_max_price_items(self) -> list[CalendarEntry]:
        if not self.vision.click_ocr([r"价目表"], roi=(800, 60, 300, 200), name="价目表"):
            return []
        self.task.sleep(0.8)
        self.vision.click_reference(991, 138, after_sleep=0.3)
        self.vision.click_ocr([r"溢价率"], roi=(650, 40, 500, 280), name="价目表排序")

        found: dict[tuple[str, str], CalendarEntry] = {}
        previous_keys: set[tuple[str, str]] = set()
        for page in range(8):
            current = self._parse_price_page(self.vision.capture())
            for entry in current:
                found[(self._normal(entry.item), entry.shop)] = entry
            keys = set(found)
            if page > 0 and keys == previous_keys:
                break
            previous_keys = keys
            self.vision.drag_reference((1060, 590), (1060, 220), duration=0.45, after_sleep=0.35)
        self.vision.click_reference(82, 36, after_sleep=0.6)
        return list(found.values())

    def _parse_price_page(self, frame: np.ndarray) -> list[CalendarEntry]:
        boxes = self.vision.ocr_boxes(frame, "价目表内容")
        rows = []
        for box in boxes:
            attrs = self._box_values(box)
            if attrs is None:
                continue
            x, y, width, height = attrs
            text = self.vision.simplify(str(getattr(box, "name", "")))
            rows.append(
                {
                    "text": text,
                    "cx": (x + width / 2) * 1280 / frame.shape[1],
                    "cy": (y + height / 2) * 720 / frame.shape[0],
                }
            )
        names = [
            row for row in rows if 300 <= row["cx"] < 730 and not re.search(r"\d+%", row["text"])
        ]
        prices = [
            row for row in rows if 840 <= row["cx"] < 990 and re.search(r"120\s*%", row["text"])
        ]
        shops = [row for row in rows if row["cx"] >= 950 and not re.search(r"\d+%", row["text"])]
        result = []
        for price in prices:
            name_candidates = [row for row in names if abs(row["cy"] - price["cy"]) < 45]
            shop_candidates = [row for row in shops if abs(row["cy"] - price["cy"]) < 55]
            if not name_candidates or not shop_candidates:
                continue
            name = min(name_candidates, key=lambda row: abs(row["cy"] - price["cy"]))["text"]
            shop_text = min(shop_candidates, key=lambda row: abs(row["cy"] - price["cy"]))["text"]
            shop = self._resolve_shop(shop_text)
            if shop:
                result.append(CalendarEntry(item=name, shop=shop))
        return result

    def _resolve_shop(self, value: str) -> str | None:
        from src.tasks.map_trade.models import KNOWN_SHOPS

        normalized = self._normal(value)
        if not normalized:
            return None
        for shop in KNOWN_SHOPS.values():
            if self._normal(shop.split(":", 1)[-1]) in normalized or normalized in self._normal(
                shop
            ):
                return shop
        return None

    def _sale_whitelist(self) -> set[str]:
        raw = self.task.config.get("出售白名单", ",".join(DEFAULT_SALE_WHITELIST))
        selected_recipes = self.task.config.get("5星料理", list(DEFAULT_RECIPES))
        configured = (
            set(DEFAULT_SALE_WHITELIST)
            | set(split_items(selected_recipes))
            | set(split_items(raw))
        )
        expanded = set(configured)
        for item in configured:
            expanded.update(ITEM_ALIASES.get(item, ()))
        return {self._normal(value) for value in expanded}

    def _sale_blacklist(self) -> set[str]:
        configured = set(split_items(self.task.config.get("出售黑名单", "")))
        expanded = set(configured)
        for item in configured:
            expanded.update(ITEM_ALIASES.get(item, ()))
        return {self._normal(value) for value in expanded}

    def _entry_allowed(self, entry: CalendarEntry, whitelist: set[str]) -> bool:
        names = (entry.item, *entry.aliases, *ITEM_ALIASES.get(entry.item, ()))
        return any(self._normal(value) in whitelist for value in names)

    def _normal(self, value: str) -> str:
        return normalize_text(self.vision.simplify(value))

    def _current_market_time(self) -> datetime:
        provider = getattr(self, "now_provider", None)
        if callable(provider):
            current = provider()
        else:
            current = getattr(self, "started_at", None)
            if current is None:
                current = self.progress.now_provider()
        if current.tzinfo is None:
            return current.replace(tzinfo=UTC_PLUS_8)
        return current.astimezone(UTC_PLUS_8)

