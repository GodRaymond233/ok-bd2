from __future__ import annotations

import re
from datetime import datetime

from src.tasks.map_trade.data import (
    ITEM_ALIASES,
)
from src.tasks.map_trade.models import (
    DEFAULT_SALE_WHITELIST,
    CalendarEntry,
)
from src.tasks.map_trade.progress import UTC_PLUS_8
from src.tasks.map_trade.trader_constants import split_items
from src.tasks.map_trade.vision import normalize_text


def parse_sale_reserves(text: str) -> dict[str, int]:
    """Read item=quantity overrides; -1 excludes an item from sale."""
    result = {}
    for entry in re.split(r"[,，;；\n]", text):
        if not entry.strip():
            continue
        name, separator, raw = entry.partition("=")
        name, raw = name.strip(), raw.strip()
        if not name or not separator or not re.fullmatch(r"-1|[0-9]+", raw):
            raise ValueError("商品保留量请按「物品=非负整数」填写；-1表示不出售。")
        if name in result:
            raise ValueError(f"商品保留量重复：{name}")
        result[name] = int(raw)
    return result


class PriceDiscoveryMixin:
    def _sale_whitelist(self) -> set[str]:
        raw = self.task.config.get("出售白名单", ",".join(DEFAULT_SALE_WHITELIST))
        configured = (
            set(DEFAULT_SALE_WHITELIST)
            | set(self._selected_cooking_recipes())
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

