from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from src.tasks.map_trade.calendar import PriceCalendarClient
from src.tasks.map_trade.data import ITEM_ALIASES, SHOP_BUY_LISTS, SHOP_OCR_EXCLUDES
from src.tasks.map_trade.models import (
    DEFAULT_RECIPES,
    DEFAULT_SALE_WHITELIST,
    MERCHANT_CARD_ID,
    RECIPE_TEMPLATES,
    CalendarEntry,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import UTC_PLUS_8, ProgressStore
from src.tasks.map_trade.vision import Vision, normalize_text

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CALENDAR_DIR = PROJECT_ROOT / "assets" / "map_trade"
STAR_TEMPLATE = TemplateSpec("收藏星标", "image/Shop/star_gray.png", 0.70, roi=(390, 93, 735, 529))
COOK_SUBMENU_TEMPLATE = TemplateSpec(
    "料理子菜单", "image/UI_cooking_submenu.png", 0.72, roi=(670, 540, 230, 180)
)
SELL_TYPE_TEMPLATE = TemplateSpec("出售排序类型", "image/UI_Selltype_0.png", 0.72)


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
        self.started_at = progress.now_provider().astimezone(UTC_PLUS_8)
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
        entered = self.navigator.reach_merchant_shop()
        if not entered.success:
            self.task.log_warning(f"跑商：{entered.message}")
            return False

        success = True
        if bool(self.task.config.get("低价进货", True)):
            every_run = str(self.task.config.get("收藏重建周期", "每周")) == "每次"
            if self.progress.should_rebuild_favorites(every_run=every_run):
                success = self.rebuild_favorites() and success
            success = self.buy_all_favorites() and success
        if bool(self.task.config.get("最高价出售", True)):
            success = self.sell_max_price_items() and success
        return success

    def rebuild_favorites(self) -> bool:
        failed = []
        for shop, targets in SHOP_BUY_LISTS.items():
            if not self.select_shop_tab(shop):
                failed.append(shop)
                continue
            if not self.align_favorites(set(targets)):
                failed.append(shop)
        if failed:
            self.task.log_warning("收藏对齐未完成：" + "、".join(failed[:8]))
            return False
        self.progress.mark_favorites_built()
        return True

    def select_shop_tab(self, shop: str) -> bool:
        label = shop.split(":", 1)[-1]
        patterns = [re.escape(label), re.escape(shop)]
        for _ in range(4):
            self.vision.drag_reference((210, 170), (210, 590), duration=0.35, after_sleep=0.1)
        for attempt in range(7):
            if self.vision.click_ocr(patterns, roi=(20, 70, 350, 570), name="商店卡带"):
                return True
            if attempt < 6:
                self.vision.drag_reference((210, 580), (210, 170), duration=0.45, after_sleep=0.25)
        return False

    def align_favorites(self, targets: set[str]) -> bool:
        frame = self.vision.capture()
        boxes = self.vision.ocr_boxes(frame, "商品名称", roi=(390, 93, 735, 529))
        names = []
        for box in boxes:
            raw = self.vision.simplify(str(getattr(box, "name", "")))
            cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", raw)
            if not cleaned or cleaned.isdigit() or cleaned in SHOP_OCR_EXCLUDES or len(cleaned) > 8:
                continue
            attrs = self._box_values(box)
            if attrs is None:
                continue
            x, y, width, height = attrs
            names.append({"name": cleaned, "left": x, "cy": y + height / 2})

        stars = self.vision.find_all(frame, STAR_TEMPLATE, max_results=40)
        scale = frame.shape[1] / 1280
        entities = []
        for star in stars:
            sx, sy = star.position
            sw, sh = star.size
            right = sx + sw
            cy = sy + sh / 2
            candidates = [
                item
                for item in names
                if 5 * scale <= item["left"] - right <= 45 * scale
                and abs(item["cy"] - cy) <= 18 * scale
            ]
            if not candidates:
                continue
            item = min(candidates, key=lambda value: abs(value["cy"] - cy))
            entities.append(
                {
                    "name": item["name"],
                    "match": star,
                    "yellow": self.vision.star_is_yellow(frame, star),
                }
            )

        normalized_targets = {self._normal(item) for item in targets}
        actions = []
        for entity in entities:
            wanted = self._normal(entity["name"]) in normalized_targets
            if wanted != bool(entity["yellow"]):
                actions.append(entity)
        for entity in actions:
            self.vision.click_client(entity["match"].center, frame.shape, after_sleep=1.5)
        if not targets:
            return True
        recognized = {self._normal(entity["name"]) for entity in entities}
        return bool(recognized & normalized_targets)

    def buy_all_favorites(self) -> bool:
        if not self.vision.click_ocr([r"购买全部收藏"], roi=(760, 580, 260, 120), name="购买收藏"):
            self.task.log_warning("跑商：未找到“购买全部收藏”按钮。")
            return False
        self.task.sleep(0.5)
        if not self.vision.click_ocr([r"确认"], roi=(540, 340, 300, 250), name="购买确认"):
            self.task.log_warning("跑商：购买收藏确认框未出现，可能没有可购买物品。")
        return True

    def sell_max_price_items(self) -> bool:
        try:
            calendar = self.calendar_client.load(
                use_online=bool(self.task.config.get("使用在线价表", True)),
                manual_text=str(self.task.config.get("自定义最高价表", "")),
            )
            self._status("价表来源", calendar.source)
            entries = list(calendar.entries_for(self.started_at.day))
        except Exception as exc:
            self.task.log_warning(f"价表加载失败，转用界面价目表 OCR：{exc}")
            entries = self.discover_max_price_items()

        whitelist = self._sale_whitelist()
        entries = [entry for entry in entries if self._entry_allowed(entry, whitelist)]
        if not entries:
            self.task.log_info("跑商：今天没有白名单内的最高价物品。")
            return True

        failed = []
        for entry in entries:
            if not self.sell_entry(entry):
                failed.append(entry)
        if failed:
            discovered = self.discover_max_price_items()
            fallback = [entry for entry in discovered if self._entry_allowed(entry, whitelist)]
            for entry in fallback:
                if any(
                    self._normal(entry.item) == self._normal(done.item)
                    for done in entries
                    if done not in failed
                ):
                    continue
                if self.sell_entry(entry):
                    failed = [
                        item
                        for item in failed
                        if self._normal(item.item) != self._normal(entry.item)
                    ]
        if failed:
            self.task.log_warning("最高价出售失败：" + "、".join(entry.item for entry in failed))
        return not failed

    def sell_entry(self, entry: CalendarEntry) -> bool:
        if not self.select_shop_tab(entry.shop):
            return False
        self.vision.click_ocr([r"出售"], roi=(30, 70, 180, 210), name="出售页")
        self._ensure_sell_sort()
        names = (entry.item, *entry.aliases, *ITEM_ALIASES.get(entry.item, ()))
        for attempt in range(5):
            frame = self.vision.capture()
            if self._row_has_120(frame, names):
                if self.vision.click_ocr(
                    [re.escape(value) for value in names],
                    roi=(330, 80, 850, 560),
                    name="出售物品",
                ):
                    return self._confirm_sale()
            if attempt < 4:
                self.vision.drag_reference((1100, 580), (1100, 200), duration=0.45, after_sleep=0.3)
        return False

    def _ensure_sell_sort(self) -> None:
        frame = self.vision.capture()
        if self.vision.match(frame, SELL_TYPE_TEMPLATE).score >= self.vision.threshold_for(
            SELL_TYPE_TEMPLATE
        ):
            return
        self.vision.click_reference(1152, 45, after_sleep=0.4)
        self.vision.click_ocr([r"溢价率"], roi=(650, 30, 520, 300), name="出售排序")
        self.vision.click_ocr([r"从高到低"], roi=(650, 30, 520, 300), name="出售降序")

    def _row_has_120(self, frame: np.ndarray, names: tuple[str, ...]) -> bool:
        boxes = self.vision.ocr_boxes(frame, "出售行校验", roi=(330, 80, 850, 560))
        item_boxes = []
        price_boxes = []
        normalized_names = {self._normal(value) for value in names}
        for box in boxes:
            attrs = self._box_values(box)
            if attrs is None:
                continue
            x, y, width, height = attrs
            text = self.vision.simplify(str(getattr(box, "name", "")))
            normalized_text = self._normal(text)
            if any(
                name and normalized_text and (name in normalized_text or normalized_text in name)
                for name in normalized_names
            ):
                item_boxes.append((y + height / 2, x))
            if re.search(r"120\s*%", text):
                price_boxes.append(y + height / 2)
        scale = frame.shape[0] / 720
        return any(
            abs(item_y - price_y) <= 36 * scale
            for item_y, _x in item_boxes
            for price_y in price_boxes
        )

    def _confirm_sale(self) -> bool:
        if self.vision.wait_ocr([r"出售", r"MAX", r"MIX"], 5, "出售确认") is None:
            return False
        point = (318, 502) if bool(self.task.config.get("出售保险", True)) else (596, 506)
        self.vision.click_reference(*point, after_sleep=0.3)
        if not self.vision.click_ocr([r"出售"], roi=(780, 400, 300, 220), name="确认出售"):
            return False
        self.task.sleep(0.8)
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
        configured = set(split_items(selected_recipes)) | set(split_items(raw))
        expanded = set(configured)
        for item in configured:
            expanded.update(ITEM_ALIASES.get(item, ()))
        return {self._normal(value) for value in expanded}

    def _entry_allowed(self, entry: CalendarEntry, whitelist: set[str]) -> bool:
        names = (entry.item, *entry.aliases, *ITEM_ALIASES.get(entry.item, ()))
        return any(self._normal(value) in whitelist for value in names)

    def _normal(self, value: str) -> str:
        return normalize_text(self.vision.simplify(value))

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
