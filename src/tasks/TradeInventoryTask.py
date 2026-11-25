"""Record verified trade stock from a sale dialog or visible cards."""

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from time import time

import cv2

from src.icons import TaskIcon
from src.tasks.HomeRewardTasks import HomePageTask
from src.tasks.map_trade.calendar import BUNDLED_CALENDAR_HISTORY, parse_calendar_payload
from src.tasks.map_trade.data import ITEM_ALIASES
from src.tasks.map_trade.trader_constants import (
    CALENDAR_DIR,
    SALE_AVAILABLE_PATTERN,
    SALE_DIALOG_REGION,
    SALE_DIALOG_TITLE_REGION,
    SALE_GRID_FOOD_KEYWORD,
    SALE_ITEMS_GRID_REGION,
    SALE_OWNED_PATTERN,
    SHOP_MODE_TITLE_REGION,
)
from src.tasks.map_trade.trader_sell import SellFlowMixin
from src.tasks.run_history import BEIJING_TZ, day_start_ts
from src.utils.image_utils import relative_roi_frame
from src.utils.ocr_utils import normalize_ocr_text

INVENTORY_PATH = Path("configs/trade-inventory.sqlite3")
VISIBLE_LIST = "盘点可见出售列表"
PAGE_LIMIT = "出售列表扫描页数上限"
QUANTITY = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:[,，][0-9]{3})+)")


def read_inventory_report(day: str | None = None) -> dict:
    if not INVENTORY_PATH.is_file():
        return {"days": [], "day": day, "comparison_day": None, "items": []}
    with closing(sqlite3.connect(f"{INVENTORY_PATH.resolve().as_uri()}?mode=ro",
                                 uri=True)) as database:
        database.row_factory = sqlite3.Row
        database.execute("BEGIN")
        days = [row["day"] for row in database.execute(
            "SELECT DISTINCT day FROM trade_stock ORDER BY day DESC"
        )]
        selected = day or (days[0] if days else None)
        comparison_day = next((value for value in days if selected and value < selected), None)
        items = [dict(row) for row in database.execute(
            "SELECT current.item, current.name, current.quantity, current.observed_at, "
            "current.quantity - previous.quantity AS quantity_change "
            "FROM trade_stock AS current LEFT JOIN trade_stock AS previous "
            "ON previous.item = current.item AND previous.day = ? "
            "WHERE current.day = ? ORDER BY current.item",
            (comparison_day, selected),
        )]
    return {"days": days, "day": selected, "comparison_day": comparison_day, "items": items}


class TradeInventoryTask(HomePageTask):
    title = "跑商库存盘点"
    task_icon = TaskIcon.SHOPPING_CART
    status_keys = ["状态", "结果"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config["盘点商品名称"] = ""
        self.default_config[VISIBLE_LIST] = False
        self.default_config[PAGE_LIMIT] = 1
        self.config_type[PAGE_LIMIT] = {"min": 1, "max": 10, "step": 1}

    @staticmethod
    def _item_catalog():
        names = {name: set(aliases) for name, aliases in ITEM_ALIASES.items()}
        for filename in ("price_calendar.v1.json", *(name for _, name in BUNDLED_CALENDAR_HISTORY)):
            calendar = parse_calendar_payload((CALENDAR_DIR / filename).read_text(encoding="utf-8"))
            for entries in calendar.days.values():
                for entry in entries:
                    names.setdefault(entry.item, set()).update(entry.aliases)
        catalog = {}
        for name, aliases in names.items():
            for alias in (name, *aliases):
                key = normalize_ocr_text(alias, alnum_only=True)
                catalog.setdefault(key, set()).add(name)
        return {alias: next(iter(candidates)) for alias, candidates in catalog.items()
                if len(candidates) == 1}

    @staticmethod
    def _visible_rows(labels, catalog):
        left, top, right, bottom = SALE_ITEMS_GRID_REGION
        grid = [(text, x, y, box) for text, x, y, box in labels
                if left < x < right and top < y < bottom and box.confidence >= .70]
        observations = {}
        points = {}
        for text, x, y, _ in grid:
            name = catalog.get(text)
            if name is None:
                continue
            item = normalize_ocr_text(name, alnum_only=True)
            categories = [(cx, cy) for category, cx, cy, _ in grid
                          if category == SALE_GRID_FOOD_KEYWORD
                          and -.025 < x - cx < .035 and .018 < cy - y < .041]
            counts = []
            if len(categories) == 1:
                cx, cy = categories[0]
                counts = [int(box.name.replace(",", "").replace("，", ""))
                          for _, qx, qy, box in grid if .022 < cx - qx < .052
                          and .019 < qy - cy < .046 and QUANTITY.fullmatch(box.name)]
            value = (name, counts[0]) if len(counts) == 1 and counts[0] < 2**53 else None
            observations[item] = value if item not in observations else None
            points[item] = (x, y)
        rows = {item: value for item, value in observations.items() if value is not None}
        return rows, {item: points[item] for item in rows}

    def _visible_snapshot(self, catalog):
        frame = self.capture_frame()
        height, width = frame.shape[:2]
        if height < 1080:
            size = (round(width * 1080 / height), 1080)
            frame = cv2.resize(frame, size, interpolation=cv2.INTER_LINEAR)
        _, labels = self._read(frame)
        left, top, right, bottom = SHOP_MODE_TITLE_REGION
        if not any(text == "出售" and left < x < right and top < y < bottom
                   for text, x, y, _ in labels):
            return {}, {}
        left, top, right, bottom = SALE_DIALOG_REGION
        if any(text.startswith("拥有") and left < x < right and top < y < bottom
               for text, x, y, _ in labels):
            return {}, {}
        return self._visible_rows(labels, catalog)

    def _observation(self, frame, item):
        def texts_in(region):
            _, _, crop = relative_roi_frame(frame, region)
            return [str(box.name).strip() for box in self.ocr(
                frame=crop, threshold=.65, log=False,
            )]

        title = normalize_ocr_text(" ".join(texts_in(SALE_DIALOG_TITLE_REGION)), alnum_only=True)
        if title != item:
            return None
        texts = texts_in(SALE_DIALOG_REGION)
        if not any(re.fullmatch(r"出售[0-9]*", normalize_ocr_text(text, alnum_only=True))
                   for text in texts):
            return None
        body = normalize_ocr_text(" ".join(texts))
        owned = SALE_OWNED_PATTERN.findall(body)
        available = SALE_AVAILABLE_PATTERN.findall(body)
        if len(owned) != 1 or len(available) != 1:
            return None
        count = SellFlowMixin._quantity_from_text(owned[0])
        return count if count is not None and count < 2**63 else None

    def run(self):
        if self.config.get(VISIBLE_LIST, False):
            return self._scan_visible_inventory()
        name = str(self.config.get("盘点商品名称", "")).strip()
        item = normalize_ocr_text(name, alnum_only=True)
        if not item:
            return self._fail("请填写出售弹窗显示的商品名称")
        first = self._observation(self.capture_frame(), item)
        if first is None:
            return self._fail("未读取到该商品的出售弹窗库存")
        self.sleep(.35)
        count = self._observation(self.capture_frame(), item)
        if count != first:
            return self._fail("商品标题或库存复核不一致")
        observed = time()
        day = datetime.fromtimestamp(day_start_ts(observed), BEIJING_TZ).date().isoformat()
        try:
            self._save_inventory(day, item, name, count, observed)
        except (OSError, sqlite3.Error) as error:
            self.log_warning(f"库存保存失败：{error}")
            return self._fail("库存记录保存失败")
        self.info_set("结果", f"{day} {name} × {count}")
        self.info_set("状态", "已记录跑商库存。")
        return True

    def _scan_visible_inventory(self):
        limit = min(10, max(1, int(self.config.get(PAGE_LIMIT, 1))))
        observations = {}
        pages = 0
        reason = ""
        observed = time()
        anchor = day_start_ts(observed)
        try:
            catalog = self._item_catalog()
            for index in range(limit):
                first, _ = self._visible_snapshot(catalog)
                if not first:
                    reason = "未确认出售列表或可见商品"
                    break
                self.sleep(.35)
                current, points = self._visible_snapshot(catalog)
                verified = {item: value for item, value in first.items()
                            if current.get(item) == value}
                if not verified:
                    reason = "没有通过双帧复核的可见库存"
                    break
                if day_start_ts() != anchor:
                    reason = "盘点日已刷新"
                    break
                if not verified.keys() - observations.keys():
                    reason = "未确认滚动后出现新商品"
                    break
                observations.update(verified)
                pages += 1
                if index + 1 == limit:
                    break
                point = points[next(iter(verified))]
                try:
                    self.scroll_client(point, -2, after_sleep=.85)
                except RuntimeError as error:
                    self.log_warning(f"库存列表滚动失败：{error}")
                    reason = "列表滚动失败"
                    break
            if not observations:
                return self._fail(reason)
            day = datetime.fromtimestamp(anchor, BEIJING_TZ).date().isoformat()
            rows = [(item, name, count) for item, (name, count) in observations.items()]
            self._save_observations(day, rows, observed)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.log_warning(f"库存盘点失败：{error}")
            return self._fail("未能记录可见库存")
        self.info_set("结果", "、".join(f"{name} × {count}" for _, name, count in rows))
        status = f"已盘点 {pages} 页，记录 {len(rows)} 项可见库存。"
        self.info_set("状态", f"{status}{reason}。" if reason else status)
        return True

    @staticmethod
    def _save_inventory(day, item, name, count, observed):
        TradeInventoryTask._save_observations(day, [(item, name, count)], observed)

    @staticmethod
    def _save_observations(day, rows, observed):
        INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(INVENTORY_PATH)) as database, database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS trade_stock ("
                "day TEXT NOT NULL, item TEXT NOT NULL, name TEXT NOT NULL, "
                "quantity INTEGER NOT NULL, observed_at REAL NOT NULL, PRIMARY KEY (day, item))"
            )
            database.executemany(
                "INSERT INTO trade_stock VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (day, item) DO UPDATE SET name=excluded.name, "
                "quantity=excluded.quantity, observed_at=excluded.observed_at "
                "WHERE excluded.observed_at > trade_stock.observed_at",
                [(day, item, name, count, observed) for item, name, count in rows],
            )
