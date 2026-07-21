from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from time import monotonic

import numpy as np

from src.tasks.map_trade.calendar import (
    PURCHASE_STOCK_REFRESH_HOUR,
    SALE_PRICE_REFRESH_HOUR,
    PriceCalendarClient,
    purchase_stock_date,
    sale_price_calendar_date,
)
from src.tasks.map_trade.data import (
    ITEM_ALIASES,
    SHOP_CARTRIDGE_BRIGHTNESS,
    SHOP_CARTRIDGE_PAGES,
    SHOP_PURCHASE_REFERENCES,
    shop_purchase_reference,
)
from src.tasks.map_trade.models import (
    DEFAULT_RECIPES,
    DEFAULT_SALE_WHITELIST,
    MERCHANT_CARD_ID,
    RECIPE_TEMPLATES,
    CalendarEntry,
    MatchResult,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import UTC_PLUS_8, ProgressStore
from src.tasks.map_trade.vision import Vision, normalize_text

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
STAR_ROI_HALF_SIZE = 15
STAR_TEMPLATE_THRESHOLD = 0.82
STAR_PIXEL_THRESHOLD = 0.90
STAR_VERIFY_ATTEMPTS = 5
STAR_VERIFY_INTERVAL = 0.25
STAR_POST_CLICK_DELAY = 1.0
BUY_ALL_FAVORITES_POINT = (
    ((1324 + 1545) / 2) / 1920,
    ((982 + 1029) / 2) / 1080,
)
BUY_ALL_FAVORITES_REGION = (
    1324 / 1920,
    982 / 1080,
    1545 / 1920,
    1029 / 1080,
)
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
BUY_TO_SELL_SOLD_OUT_KEYWORD = "售罄"
BUY_TO_SELL_TIMEOUT = 30.0
BUY_TO_SELL_OCR_INTERVAL = 0.25
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
SELL_SORT_MODE_REGION = (
    1727 / 1920,
    29 / 1080,
    1800 / 1920,
    99 / 1080,
)
SELL_SORT_OPTION_POINT = (1578 / 1920, 145 / 1080)
FIRST_SALE_ITEM_REGION = (
    486 / 1920,
    121 / 1080,
    814 / 1920,
    231 / 1080,
)
FIRST_SALE_QUANTITY_REGION = (
    491 / 1920,
    190 / 1080,
    601 / 1920,
    230 / 1080,
)
FIRST_SALE_ITEM_POINT = (
    ((486 + 814) / 2) / 1920,
    ((121 + 231) / 2) / 1080,
)
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
SALE_SLIDER_REGION = (
    552 / 1920,
    647 / 1080,
    912 / 1920,
    683 / 1080,
)
SALE_SORT_MAX_CLICKS = 2
SALE_DIALOG_TIMEOUT = 5.0
SALE_OCR_INTERVAL = 0.25
COOK_SUBMENU_TEMPLATE = TemplateSpec(
    "料理子菜单",
    "image/green/UI_cooking_submenu.png",
    0.72,
    roi=(670, 540, 230, 180),
)
PREMIUM_RATE_TEMPLATE = TemplateSpec(
    "溢价率排序",
    "shop/premium_rate.png",
    0.85,
    green_mask=True,
    relative_roi=SELL_SORT_MODE_REGION,
    scale_ratios=SHOP_CARTRIDGE_SCALE_RATIOS,
    min_pixel_score=0.85,
)
PRICE_SORT_TEMPLATE = TemplateSpec(
    "价格排序",
    "shop/price.png",
    0.85,
    green_mask=True,
    relative_roi=SELL_SORT_MODE_REGION,
    scale_ratios=SHOP_CARTRIDGE_SCALE_RATIOS,
    min_pixel_score=0.85,
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


def split_items(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        values = re.split(r"[,，;；\n]", value)
    else:
        values = value
    return tuple(str(item).strip() for item in values if str(item).strip())


class Trader:
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

    def run_buy(self) -> bool:
        stock_date = purchase_stock_date(self._current_market_time())
        self._status("购买库存日期", stock_date.isoformat())
        self.task.log_info(
            f"买：按{stock_date.isoformat()}库存批次执行（每日"
            f"{PURCHASE_STOCK_REFRESH_HOUR:02d}:00刷新）。"
        )
        entered = self.navigator.enter_q_sp6_buy_flow()
        if not entered.success:
            self.task.log_warning(f"买：{entered.message}")
            return False
        rebuild_cycle = str(self.task.config.get("收藏重建周期", "每周"))
        every_run = rebuild_cycle == "每次"
        if rebuild_cycle == "永不":
            self.task.log_info("买：收藏重建周期设为永不，跳过收藏调整。")
        elif not self.progress.should_rebuild_favorites(every_run=every_run):
            self.task.log_info("买：本周收藏已经按本地表重建，跳过收藏调整。")
        else:
            if every_run:
                self.progress.clear_favorite_cards()
            if not self.rebuild_favorites():
                return False
        completed = self.buy_all_favorites()
        self._buy_completed_in_current_shop = completed
        return completed

    def run_sell(self) -> bool:
        if getattr(self, "_buy_completed_in_current_shop", False):
            self.task.log_info("卖：买卖同时执行，继续使用当前商店并等待购买结果。")
            if not self._switch_from_completed_buy_to_sell():
                return False
        else:
            entered = self.navigator.reach_merchant_shop()
            if not entered.success:
                self.task.log_warning(f"卖：{entered.message}")
                return False
            if not self._ensure_sell_page():
                return False
        self._buy_completed_in_current_shop = False
        return self.sell_max_price_items()

    def _switch_from_completed_buy_to_sell(
        self,
        timeout: float = BUY_TO_SELL_TIMEOUT,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        expected = normalize_text(self.vision.simplify(BUY_TO_SELL_SOLD_OUT_KEYWORD))
        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                "买后售罄确认",
            )
            last_text = text or last_text
            normalized = normalize_text(self.vision.simplify(text))
            if expected in normalized:
                self._status("买后售罄确认", "已命中")
                self.task.log_info(
                    "卖：整帧OCR命中售罄，等待0.5秒后点击出售入口。"
                )
                self.task.sleep(BUY_TO_SELL_PRE_CLICK_DELAY)
                self.task.operate_click(
                    *SELL_MODE_POINT,
                    after_sleep=BUY_TO_SELL_POST_CLICK_DELAY,
                )
                return self._ensure_sell_page(allow_switch=False)
            if monotonic() >= end_at:
                break
            self.task.sleep(BUY_TO_SELL_OCR_INTERVAL)
        self.task.log_warning(
            f"卖：买后等待售罄OCR超时，未切换出售页，OCR={last_text or '-'}。"
        )
        return False

    def _ensure_sell_page(
        self,
        timeout: float = SHOP_MODE_TIMEOUT,
        *,
        allow_switch: bool = True,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        switched = False
        last_text = ""
        while True:
            frame = self.vision.capture()
            text = self.vision.ocr_text(
                frame,
                "商店买卖页标题",
                relative_roi=SHOP_MODE_TITLE_REGION,
            )
            last_text = text or last_text
            normalized = normalize_text(self.vision.simplify(text))
            if "出售" in normalized:
                self._status("商店页面", "出售")
                return True
            if "购买" in normalized and allow_switch and not switched:
                self._status("商店页面", "购买→出售")
                self.task.operate_click(*SELL_MODE_POINT, after_sleep=0.5)
                switched = True
                continue
            if monotonic() >= end_at:
                break
            self.task.sleep(SHOP_MODE_INTERVAL)
        self.task.log_warning(
            f"卖：未能通过标题区域确认已切换到出售页面，OCR={last_text or '-'}。"
        )
        return False

    def rebuild_favorites(self) -> bool:
        if not self._reset_shop_to_first_page():
            return False

        for page in SHOP_CARTRIDGE_PAGES:
            if page.scroll_down_from_previous:
                self._scroll_shop_cartridges(
                    scroll_amount=-1,
                    count=page.scroll_down_from_previous,
                    interval=SHOP_DOWN_SCROLL_INTERVAL,
                    after_sleep=0.5,
                )
            if not self._wait_for_shop_page(page.confirmation_shop_ids):
                labels = "、".join(
                    SHOP_PURCHASE_REFERENCES[value].label
                    for value in page.confirmation_shop_ids
                )
                self.task.log_warning(
                    f"买：向下滚动后未确认第{page.page_number}页边界卡带：{labels}。"
                )
                return False

            for shop_id in page.shop_ids:
                reference = SHOP_PURCHASE_REFERENCES[shop_id]
                if self.progress.favorite_card_complete(shop_id):
                    self.task.log_info(f"买：{reference.label}已有本次完成记录，跳过。")
                    continue
                if not self._select_purchase_cartridge(shop_id):
                    self.task.log_warning(f"买：未能选择{reference.label}。")
                    return False
                if not self._align_unfavorited_points(shop_id):
                    self.task.log_warning(f"买：{reference.label}空收藏位置核对失败。")
                    return False
                self.progress.mark_favorite_card(shop_id)
                self._status("收藏重建进度", f"{reference.label} 已完成")

        self.progress.mark_favorites_built()
        self.task.log_info("买：31张商品卡带的空收藏位置已全部核对完成。")
        return True

    def _reset_shop_to_first_page(self) -> bool:
        for attempt in range(SHOP_FIRST_PAGE_MAX_UP_SCROLLS + 1):
            frame = self.vision.capture()
            if self._cartridge_visible("S1", frame):
                self._status("商品卡带页", "第1页")
                return True
            if attempt >= SHOP_FIRST_PAGE_MAX_UP_SCROLLS:
                break
            self._scroll_shop_cartridges(
                scroll_amount=1,
                count=1,
                interval=0.0,
                after_sleep=SHOP_UP_SCROLL_RECOGNITION_INTERVAL,
            )
        self.task.log_warning("买：向上逐格滚动后仍未识别到剧情游戏卡1。")
        return False

    def _scroll_shop_cartridges(
        self,
        scroll_amount: int,
        count: int,
        interval: float,
        after_sleep: float,
    ) -> None:
        self.task._scroll_client(
            SHOP_CARTRIDGE_SCROLL_POINT,
            scroll_amount,
            count=count,
            interval=interval,
            after_sleep=after_sleep,
        )

    def _wait_for_shop_page(
        self,
        confirmation_shop_ids: tuple[str, ...],
        timeout: float = 4.0,
        interval: float = 0.25,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.vision.capture()
            confirmed = self._confirmed_shop_cartridge_detections(frame)
            if all(shop_id in confirmed for shop_id in confirmation_shop_ids):
                self._status("商品卡带页确认", "、".join(confirmation_shop_ids))
                return True
            self.task.sleep(interval)
        return False

    def _cartridge_spec(self, shop_id: str) -> TemplateSpec:
        reference = SHOP_PURCHASE_REFERENCES[shop_id]
        return TemplateSpec(
            name=reference.label,
            file_name=reference.cartridge_templates[0],
            threshold=SHOP_CARTRIDGE_CONFIRM_SCORE,
            relative_roi=SHOP_CARTRIDGE_RECOGNITION_REGION,
            scale_ratios=SHOP_CARTRIDGE_SCALE_RATIOS,
        )

    @staticmethod
    def _shop_cartridge_chapter_name(shop_id: str) -> str:
        label = SHOP_PURCHASE_REFERENCES[shop_id].label
        return label.split(" ", 1)[1] if " " in label else ""

    @staticmethod
    def _shop_cartridge_text_similarity(actual: str, expected: str) -> float:
        actual_normalized = normalize_text(actual)
        expected_normalized = normalize_text(expected)
        if not actual_normalized or not expected_normalized:
            return 0.0
        return SequenceMatcher(None, actual_normalized, expected_normalized).ratio()

    @staticmethod
    def _shop_cartridge_ocr_confidence(box) -> float:
        raw = getattr(box, "confidence", getattr(box, "score", 0.0))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if value > 1.0:
            value /= 100.0
        return max(0.0, min(1.0, value))

    def _shop_cartridge_ocr_rows(
        self,
        frame: np.ndarray,
    ) -> tuple[ShopCartridgeOcrRow, ...]:
        height, width = frame.shape[:2]
        scale_x = width / 1920
        scale_y = height / 1080
        texts: list[ShopCartridgeOcrText] = []
        for box in self.vision.ocr_boxes(
            frame,
            "商品卡带竞争",
            roi=SHOP_CARTRIDGE_OCR_ROI,
        ):
            try:
                x = float(box.x)
                y = float(box.y)
                box_width = float(box.width)
                box_height = float(box.height)
            except (AttributeError, TypeError, ValueError):
                continue
            texts.append(
                ShopCartridgeOcrText(
                    text=str(getattr(box, "name", "")),
                    confidence=self._shop_cartridge_ocr_confidence(box),
                    center=(x + box_width / 2, y + box_height / 2),
                )
            )

        rows: list[ShopCartridgeOcrRow] = []
        for category in texts:
            match = SHOP_CARTRIDGE_CATEGORY_PATTERN.search(
                normalize_text(category.text)
            )
            if match is None:
                continue
            shop_id = (
                f"{SHOP_CARTRIDGE_CATEGORY_PREFIX[match.group(1)]}"
                f"{int(match.group(2))}"
            )
            if shop_id not in SHOP_PURCHASE_REFERENCES:
                continue
            expected_name = self._shop_cartridge_chapter_name(shop_id)
            chapter_candidates = [
                value
                for value in texts
                if 8 * scale_y
                <= value.center[1] - category.center[1]
                <= 45 * scale_y
                and abs(value.center[0] - category.center[0]) <= 80 * scale_x
                and SHOP_CARTRIDGE_CATEGORY_PATTERN.search(
                    normalize_text(value.text)
                )
                is None
            ]
            chapter = max(
                chapter_candidates,
                key=lambda value: (
                    self._shop_cartridge_text_similarity(value.text, expected_name),
                    value.confidence,
                ),
                default=None,
            )
            rows.append(
                ShopCartridgeOcrRow(
                    shop_id=shop_id,
                    category=category,
                    chapter=chapter,
                    name_similarity=(
                        self._shop_cartridge_text_similarity(
                            chapter.text,
                            expected_name,
                        )
                        if chapter is not None
                        else 0.0
                    ),
                )
            )
        return tuple(sorted(rows, key=lambda value: value.category.center[1]))

    def _shop_cartridge_template_candidates(
        self,
        frame: np.ndarray,
    ) -> tuple[ShopCartridgeTemplateCandidate, ...]:
        height, width = frame.shape[:2]
        peak_radius = max(5, round(20 * min(width / 1920, height / 1080)))
        candidates: list[ShopCartridgeTemplateCandidate] = []
        for shop_id in SHOP_PURCHASE_REFERENCES:
            matches = self.vision.match_all(
                frame,
                self._cartridge_spec(shop_id),
                minimum_score=SHOP_CARTRIDGE_CANDIDATE_SCORE,
                peak_radius=peak_radius,
                max_results=30,
            )
            candidates.extend(
                ShopCartridgeTemplateCandidate(shop_id, result)
                for result in matches
            )
        return tuple(candidates)

    def _shop_cartridge_competition(
        self,
        frame: np.ndarray,
    ) -> tuple[ShopCartridgeDetection, ...]:
        height = frame.shape[0]
        cluster_radius = max(
            5,
            round(SHOP_CARTRIDGE_ROW_CLUSTER_RADIUS * height / 1080),
        )
        ocr_link_radius = max(
            5,
            round(SHOP_CARTRIDGE_OCR_ROW_LINK_RADIUS * height / 1080),
        )
        ocr_rows = self._shop_cartridge_ocr_rows(frame)
        clusters: list[list[ShopCartridgeTemplateCandidate]] = []
        for candidate in sorted(
            self._shop_cartridge_template_candidates(frame),
            key=lambda value: value.result.score,
            reverse=True,
        ):
            for cluster in clusters:
                if (
                    abs(candidate.result.center[1] - cluster[0].result.center[1])
                    <= cluster_radius
                ):
                    cluster.append(candidate)
                    break
            else:
                clusters.append([candidate])

        detections: list[ShopCartridgeDetection] = []
        for cluster in clusters:
            best_by_shop: dict[str, ShopCartridgeTemplateCandidate] = {}
            for candidate in cluster:
                current = best_by_shop.get(candidate.shop_id)
                if current is None or candidate.result.score > current.result.score:
                    best_by_shop[candidate.shop_id] = candidate
            ranked = sorted(
                best_by_shop.values(),
                key=lambda value: value.result.score,
                reverse=True,
            )
            if not ranked or ranked[0].result.score < SHOP_CARTRIDGE_CONFIRM_SCORE:
                continue
            nearest_ocr = min(
                ocr_rows,
                key=lambda value: abs(
                    value.category.center[1] - ranked[0].result.center[1]
                ),
                default=None,
            )
            if nearest_ocr is not None and (
                abs(nearest_ocr.category.center[1] - ranked[0].result.center[1])
                > ocr_link_radius
            ):
                nearest_ocr = None
            detections.append(
                ShopCartridgeDetection(
                    best=ranked[0],
                    runner_up=ranked[1] if len(ranked) > 1 else None,
                    ocr=nearest_ocr,
                )
            )
        return tuple(
            sorted(detections, key=lambda value: value.best.result.center[1])
        )

    def _shop_cartridge_detection_passes(
        self,
        detection: ShopCartridgeDetection,
    ) -> bool:
        ocr = detection.ocr
        return (
            self._cartridge_match_passes(
                detection.best.result,
                self._cartridge_spec(detection.best.shop_id),
            )
            and detection.margin >= SHOP_CARTRIDGE_MIN_MARGIN
            and ocr is not None
            and ocr.shop_id == detection.best.shop_id
            and ocr.category.confidence >= SHOP_CARTRIDGE_OCR_MIN_CONFIDENCE
            and ocr.name_similarity >= SHOP_CARTRIDGE_NAME_MIN_SIMILARITY
        )

    def _confirmed_shop_cartridge_detections(
        self,
        frame: np.ndarray,
    ) -> dict[str, ShopCartridgeDetection]:
        candidates: dict[str, list[ShopCartridgeDetection]] = {}
        for detection in self._shop_cartridge_competition(frame):
            runner = detection.runner_up.shop_id if detection.runner_up else "-"
            ocr_id = detection.ocr.shop_id if detection.ocr else "-"
            name_similarity = detection.ocr.name_similarity if detection.ocr else 0.0
            self._status(
                f"卡带竞争 {detection.best.shop_id}",
                (
                    f"match={detection.best.result.score:.3f}, runner={runner}, "
                    f"margin={detection.margin:.3f}, OCR={ocr_id}, "
                    f"name={name_similarity:.3f}"
                ),
            )
            if self._shop_cartridge_detection_passes(detection):
                candidates.setdefault(detection.best.shop_id, []).append(detection)

        confirmed: dict[str, ShopCartridgeDetection] = {}
        for shop_id, detections in candidates.items():
            if len(detections) == 1:
                confirmed[shop_id] = detections[0]
            else:
                self.task.log_warning(
                    f"买：卡带竞争结果中{shop_id}出现{len(detections)}个有效位置，拒绝猜测。"
                )
        return confirmed

    def _cartridge_visible(self, shop_id: str, frame: np.ndarray) -> bool:
        return shop_id in self._confirmed_shop_cartridge_detections(frame)

    def _cartridge_match_passes(
        self,
        result: MatchResult,
        spec: TemplateSpec,
    ) -> bool:
        # Cartridge crops are tightly calibrated local references.  Keep their
        # dedicated floor even when the task-wide threshold is configured lower,
        # otherwise a cartridge that is only partly visible at a page edge can be
        # mistaken for a complete row entry.
        return result.score >= max(spec.threshold, self.vision.threshold_for(spec))

    def _wait_for_cartridge_match(
        self,
        shop_id: str,
        timeout: float = 3.0,
        interval: float = 0.25,
    ) -> tuple[np.ndarray, TemplateSpec, MatchResult] | None:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.vision.capture()
            detection = self._confirmed_shop_cartridge_detections(frame).get(shop_id)
            if detection is not None:
                return frame, self._cartridge_spec(shop_id), detection.best.result
            self.task.sleep(interval)
        return None

    def _select_purchase_cartridge(self, shop_id: str) -> bool:
        found = self._wait_for_cartridge_match(shop_id)
        if found is None:
            return False
        frame, spec, result = found
        self.vision.click_client(result.center, frame.shape, after_sleep=0.5)

        end_at = monotonic() + 4.0
        while monotonic() <= end_at:
            selected_frame = self.vision.capture()
            selected = self._confirmed_shop_cartridge_detections(selected_frame).get(
                shop_id
            )
            if selected is not None:
                brightness = self.vision.template_brightness_ratio(
                    selected_frame,
                    spec,
                    selected.best.result,
                    minimum_template_gray=SHOP_CARTRIDGE_BRIGHTNESS.foreground_min_gray,
                )
                self._status(f"卡带亮度 {shop_id}", f"{brightness:.3f}")
                if SHOP_CARTRIDGE_BRIGHTNESS.is_selected(brightness):
                    return True
            self.task.sleep(0.25)
        return False

    def _select_shop_cartridge_from_first_page(self, shop_id: str) -> bool:
        if shop_id not in SHOP_PURCHASE_REFERENCES:
            self.task.log_warning(f"卖：本地商品卡带表缺少 {shop_id}。")
            return False
        if not self._reset_shop_to_first_page():
            return False

        for page in SHOP_CARTRIDGE_PAGES:
            if page.scroll_down_from_previous:
                self._scroll_shop_cartridges(
                    scroll_amount=-1,
                    count=page.scroll_down_from_previous,
                    interval=SHOP_DOWN_SCROLL_INTERVAL,
                    after_sleep=0.5,
                )
            if not self._wait_for_shop_page(page.confirmation_shop_ids):
                labels = "、".join(
                    SHOP_PURCHASE_REFERENCES[value].label
                    for value in page.confirmation_shop_ids
                )
                self.task.log_warning(
                    f"卖：向下滚动后未确认第{page.page_number}页边界卡带：{labels}。"
                )
                return False
            if shop_id in page.shop_ids:
                return self._select_purchase_cartridge(shop_id)

        self.task.log_warning(f"卖：本地商品卡带分页表未覆盖 {shop_id}。")
        return False

    def _align_unfavorited_points(self, shop_id: str) -> bool:
        reference = SHOP_PURCHASE_REFERENCES[shop_id]
        for slot, point in reference.unfavorited_points:
            frame = self.vision.capture()
            if self._gray_star_present(frame, slot, point):
                self._status(f"{shop_id} 空收藏#{slot}", "已是灰星")
                continue
            self._status(f"{shop_id} 空收藏#{slot}", "点击取消收藏")
            self.task.operate_click(*point, after_sleep=STAR_POST_CLICK_DELAY)
            if not self._wait_for_gray_star(slot, point):
                self.task.log_warning(f"买：{reference.label} #{slot} 点击后仍未识别到灰星。")
                return False
        return True

    def _wait_for_gray_star(
        self,
        slot: int,
        point: tuple[float, float],
    ) -> bool:
        for attempt in range(STAR_VERIFY_ATTEMPTS):
            if self._gray_star_present(self.vision.capture(), slot, point):
                return True
            if attempt + 1 < STAR_VERIFY_ATTEMPTS:
                self.task.sleep(STAR_VERIFY_INTERVAL)
        return False

    def _gray_star_present(
        self,
        frame: np.ndarray,
        slot: int,
        point: tuple[float, float],
    ) -> bool:
        half_x = STAR_ROI_HALF_SIZE / 1920
        half_y = STAR_ROI_HALF_SIZE / 1080
        spec = TemplateSpec(
            name=f"灰星#{slot}",
            file_name=STAR_TEMPLATE_FILE,
            threshold=STAR_TEMPLATE_THRESHOLD,
            green_mask=True,
            relative_roi=(
                max(0.0, point[0] - half_x),
                max(0.0, point[1] - half_y),
                min(1.0, point[0] + half_x),
                min(1.0, point[1] + half_y),
            ),
            scale_ratios=SHOP_CARTRIDGE_SCALE_RATIOS,
            min_pixel_score=STAR_PIXEL_THRESHOLD,
        )
        result = self.vision.match(frame, spec)
        self._status(
            f"灰星#{slot}",
            f"match={result.score:.3f}, pixel={result.pixel_score:.3f}",
        )
        return self.vision.passes(result, spec) and not self.vision.star_is_yellow(
            frame, result
        )

    def select_shop_tab(self, shop: str) -> bool:
        try:
            reference = shop_purchase_reference(shop)
        except KeyError:
            self.task.log_warning(f"卖：价表商店没有本地商品卡带映射：{shop}。")
            return False
        return self._select_shop_cartridge_from_first_page(reference.shop_id)

    def buy_all_favorites(self) -> bool:
        if not self._wait_for_buy_all_favorites_button():
            self.task.log_warning("买：商店页面未稳定显示一键购买全部收藏按钮。")
            return False
        self.task.operate_click(*BUY_ALL_FAVORITES_POINT, after_sleep=0.3)
        if not self._wait_for_purchase_confirmation():
            self.task.log_warning(
                "买：点击一键购买全部收藏后，未同时识别到确认标题和询问文字。"
            )
            return False
        self.task.log_info(
            f"买：购买确认弹窗OCR完成，等待{BUY_CONFIRM_PRE_CLICK_DELAY:.1f}秒后点击确认。"
        )
        self.task.sleep(BUY_CONFIRM_PRE_CLICK_DELAY)
        self.task.operate_click(
            *BUY_CONFIRM_POINT,
            after_sleep=BUY_CONFIRM_POST_CLICK_DELAY,
        )
        self.task.log_info("买：已确认购买全部收藏商品。")
        return True

    def _wait_for_buy_all_favorites_button(
        self,
        timeout: float = BUY_ALL_FAVORITES_TIMEOUT,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        consecutive_hits = 0
        last_text = ""
        expected = normalize_text(self.vision.simplify(BUY_ALL_FAVORITES_KEYWORD))
        while True:
            frame = self.vision.capture()
            text = self.vision.ocr_text(
                frame,
                "一键购买全部收藏按钮",
                relative_roi=BUY_ALL_FAVORITES_REGION,
            )
            last_text = text or last_text
            normalized = normalize_text(self.vision.simplify(text))
            if expected in normalized:
                consecutive_hits += 1
            else:
                consecutive_hits = 0
            self._status(
                "一键购买全部收藏按钮 OCR稳定",
                f"{consecutive_hits}/{BUY_ALL_FAVORITES_STABLE_HITS}",
            )
            if consecutive_hits >= BUY_ALL_FAVORITES_STABLE_HITS:
                return True
            if monotonic() >= end_at:
                break
            self.task.sleep(BUY_ALL_FAVORITES_INTERVAL)
        self.task.log_warning(
            f"买：一键购买全部收藏按钮OCR超时，OCR={last_text or '-'}。"
        )
        return False

    def _wait_for_purchase_confirmation(
        self,
        timeout: float = BUY_CONFIRM_TIMEOUT,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while True:
            frame = self.vision.capture()
            text = self.vision.ocr_text(
                frame,
                "购买全部收藏确认",
                relative_roi=BUY_CONFIRM_DIALOG_REGION,
            )
            last_text = text or last_text
            normalized = normalize_text(self.vision.simplify(text))
            matched = sum(
                normalize_text(self.vision.simplify(keyword)) in normalized
                for keyword in BUY_CONFIRM_KEYWORDS
            )
            self._status(
                "购买全部收藏确认 OCR命中",
                f"{matched}/{len(BUY_CONFIRM_KEYWORDS)}",
            )
            if matched == len(BUY_CONFIRM_KEYWORDS):
                return True
            if monotonic() >= end_at:
                break
            self.task.sleep(BUY_CONFIRM_INTERVAL)
        self.task.log_warning(
            f"买：购买全部收藏确认OCR超时，OCR={last_text or '-'}。"
        )
        return False

    def sell_max_price_items(self) -> bool:
        try:
            market_now = self._current_market_time()
            calendar_date = sale_price_calendar_date(market_now)
            calendar = self.calendar_client.load(
                use_bundled=bool(
                    self.task.config.get("使用程序默认价表", True)
                ),
                use_online=bool(self.task.config.get("使用在线价表", True)),
                manual_text=str(self.task.config.get("自定义最高价表", "")),
            )
            self._status("价表来源", calendar.source)
            self._status("出售价表日期", calendar_date.isoformat())
            self.task.log_info(
                f"卖：当前北京时间{market_now.strftime('%Y-%m-%d %H:%M:%S')}，"
                f"按{calendar_date.isoformat()}最高价表执行（每日"
                f"{SALE_PRICE_REFRESH_HOUR:02d}:00刷新）。"
            )
            entries = list(calendar.entries_for(calendar_date.day))
        except Exception as exc:
            self.task.log_warning(f"价表加载失败，为避免误卖已停止出售：{exc}")
            return False

        sellable = []
        for entry in entries:
            if not entry.sell:
                self.task.log_info(f"卖：{entry.item}标记为不出售，跳过。")
                continue
            sellable.append(entry)

        whitelist = self._sale_whitelist()
        entries = [entry for entry in sellable if self._entry_allowed(entry, whitelist)]
        if not entries:
            self.task.log_info("跑商：今天没有白名单内的最高价物品。")
            return True

        failed = []
        unavailable: list[str] = []
        not_sold_details: list[str] = []
        selected_shop = ""
        for entry in entries:
            if entry.shop != selected_shop:
                if not self.select_shop_tab(entry.shop):
                    failed.append(entry)
                    not_sold_details.append(f"{entry.item}（商店卡带选择失败）")
                    self._status("未出售商品", "、".join(not_sold_details))
                    selected_shop = ""
                    continue
                selected_shop = entry.shop
            if not self._sell_selected_entry(entry):
                if getattr(self, "_last_sale_unavailable", False):
                    detail = f"{entry.item}（{self._last_sale_reason}）"
                    unavailable.append(detail)
                    not_sold_details.append(detail)
                    self._status("未出售商品", "、".join(not_sold_details))
                    continue
                failed.append(entry)
                not_sold_details.append(f"{entry.item}（出售执行失败）")
                self._status("未出售商品", "、".join(not_sold_details))
        if unavailable:
            self.task.log_warning("未出售商品：" + "、".join(unavailable))
        if not not_sold_details:
            self._status("未出售商品", "无")
        if failed:
            self.task.log_warning("最高价出售失败：" + "、".join(entry.item for entry in failed))
        return not failed

    def sell_entry(self, entry: CalendarEntry) -> bool:
        if not self.select_shop_tab(entry.shop):
            return False
        return self._sell_selected_entry(entry)

    def _sell_selected_entry(self, entry: CalendarEntry) -> bool:
        self._last_sale_unavailable = False
        self._last_sale_reason = ""
        list_quantity = self._prepare_first_sale_item(entry)
        if list_quantity is None:
            return False
        if entry.reserve and list_quantity <= entry.reserve:
            self.task.log_info(
                f"卖：{entry.item}当前{list_quantity}个，不超过保留量{entry.reserve}，跳过。"
            )
            return True

        self.task.operate_click(*FIRST_SALE_ITEM_POINT, after_sleep=0.5)
        self.task.operate_click(*FIRST_SALE_ITEM_POINT, after_sleep=0.5)
        owned = self._wait_owned_quantity()
        if owned is None:
            self.task.log_warning(f"卖：{entry.item}出售弹窗未识别到拥有数量。")
            return False
        self._status("出售弹窗库存", f"{entry.item}:{owned}")
        self.task.log_info(f"卖：{entry.item}出售弹窗记录库存{owned}个。")
        if entry.reserve and owned <= entry.reserve:
            self.task.log_warning(
                f"卖：{entry.item}弹窗库存{owned}不超过保留量{entry.reserve}，停止出售。"
            )
            return False
        if not self._choose_sale_quantity(entry, owned):
            return False
        self.task.operate_click(*SALE_CONFIRM_POINT, after_sleep=0.5)
        self.task.log_info(f"卖：{entry.item}已点击出售。")
        return True

    def _prepare_first_sale_item(self, entry: CalendarEntry) -> int | None:
        frame = self.vision.capture()
        premium = self.vision.match(frame, PREMIUM_RATE_TEMPLATE)
        price = self.vision.match(frame, PRICE_SORT_TEMPLATE)
        premium_passes = self.vision.passes(premium, PREMIUM_RATE_TEMPLATE)
        price_passes = self.vision.passes(price, PRICE_SORT_TEMPLATE)
        self._status(
            "出售排序",
            f"premium={premium.score:.3f}/{premium.pixel_score:.3f}, "
            f"price={price.score:.3f}/{price.pixel_score:.3f}",
        )

        sort_clicks = 0
        if premium_passes and (not price_passes or premium.score >= price.score):
            # Premium-rate mode is already correct.  The fixed option point is
            # only valid while switching away from the price menu.
            sort_clicks = SALE_SORT_MAX_CLICKS
        elif price_passes:
            self.vision.click_client(price.center, frame.shape, after_sleep=0.5)
            self.task.operate_click(*SELL_SORT_OPTION_POINT, after_sleep=0.5)
            sort_clicks = 1
        else:
            self.task.log_warning("卖：排序区域既未识别到溢价率，也未识别到价格。")
            return False

        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                "出售首格商品",
                relative_roi=FIRST_SALE_ITEM_REGION,
            )
            if self._first_sale_item_matches(text, entry):
                quantity = self._read_sale_list_quantity(entry)
                if quantity is not None:
                    return quantity
                self.task.log_warning(f"卖：{entry.item}首格库存数量OCR失败。")
                return None
            if sort_clicks >= SALE_SORT_MAX_CLICKS:
                break
            self.task.sleep(0.5)
            self.task.operate_click(*SELL_SORT_OPTION_POINT, after_sleep=0.5)
            sort_clicks += 1
        self.task.log_warning(
            f"卖：最多点击排序{SALE_SORT_MAX_CLICKS}次后，首格仍未同时识别到"
            f"120%和{entry.item}。"
        )
        self._last_sale_unavailable = True
        self._last_sale_reason = "未发现120%，可能无货或已经售出"
        return None

    def _first_sale_item_matches(self, text: str, entry: CalendarEntry) -> bool:
        normalized = self._normal(text)
        names = (entry.item, *entry.aliases, *ITEM_ALIASES.get(entry.item, ()))
        normalized_names = tuple(self._normal(value) for value in names)
        has_item = any(
            name and (name in normalized or normalized in name)
            for name in normalized_names
        )
        return has_item and bool(re.search(r"120\s*%", text))

    def _read_sale_list_quantity(self, entry: CalendarEntry) -> int | None:
        for attempt in range(3):
            text = self.vision.ocr_text(
                self.vision.capture(),
                "出售首格库存",
                relative_roi=FIRST_SALE_QUANTITY_REGION,
            )
            quantity = self._quantity_from_text(text)
            if quantity is not None:
                self._status("出售列表库存", f"{entry.item}:{quantity}")
                self.task.log_info(f"卖：{entry.item}列表记录库存{quantity}个。")
                return quantity
            if attempt < 2:
                self.task.sleep(SALE_OCR_INTERVAL)
        return None

    def _wait_owned_quantity(self, timeout: float = SALE_DIALOG_TIMEOUT) -> int | None:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                "出售弹窗库存",
                relative_roi=SALE_DIALOG_REGION,
            )
            normalized = self.vision.simplify(text)
            matched = re.search(r"拥有[^0-9]{0,8}([0-9][0-9,，.]*)", normalized)
            if matched is not None:
                quantity = self._quantity_from_text(matched.group(1))
                if quantity is not None:
                    return quantity
            if monotonic() >= end_at:
                return None
            self.task.sleep(SALE_OCR_INTERVAL)

    @staticmethod
    def _quantity_from_text(text: str) -> int | None:
        values = []
        for matched in re.findall(r"[0-9][0-9,，.]*", str(text)):
            digits = re.sub(r"\D", "", matched)
            if digits:
                values.append(int(digits))
        return max(values, default=None)

    def _choose_sale_quantity(self, entry: CalendarEntry, owned: int) -> bool:
        if entry.reserve > 0:
            slider_point = self._sale_slider_point(owned, entry.reserve)
            if slider_point is None:
                return False
            amount = owned - entry.reserve
            self.task.operate_click(*slider_point, after_sleep=0.5)
            self._status("出售保留量", f"{entry.item}:约{entry.reserve}")
            self.task.log_info(
                f"卖：{entry.item}拥有{owned}个，滑条选择出售约{amount}个，"
                f"目标保留约{entry.reserve}个。"
            )
            return True
        if bool(self.task.config.get("出售保险", False)):
            self.task.operate_click(*SALE_MIN_POINT, after_sleep=0.5)
        else:
            self.task.operate_click(*SALE_MAX_POINT, after_sleep=0.5)
        return True

    @staticmethod
    def _sale_slider_point(
        owned: int, reserve: int
    ) -> tuple[float, float] | None:
        """Map the desired sale amount onto the one-to-all sale slider."""

        if owned <= reserve or owned <= 0:
            return None
        amount = owned - reserve
        ratio = 0.0 if owned == 1 else (amount - 1) / (owned - 1)
        ratio = max(0.0, min(1.0, ratio))
        left, top, right, bottom = SALE_SLIDER_REGION
        return left + ((right - left) * ratio), (top + bottom) / 2

    def _confirm_sale(self) -> bool:
        """Compatibility wrapper for callers outside the strict first-slot flow."""

        if bool(self.task.config.get("出售保险", False)):
            self.task.operate_click(*SALE_MIN_POINT, after_sleep=0.5)
        else:
            self.task.operate_click(*SALE_MAX_POINT, after_sleep=0.5)
        self.task.operate_click(*SALE_CONFIRM_POINT, after_sleep=0.5)
        return True

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

    @staticmethod
    def _box_values(box) -> tuple[float, float, float, float] | None:
        values = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            raw_box = getattr(box, "box", None)
            if raw_box is not None and len(raw_box) >= 4:
                values = tuple(raw_box[:4])
        if any(value is None for value in values):
            return None
        return tuple(float(value) for value in values)

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
