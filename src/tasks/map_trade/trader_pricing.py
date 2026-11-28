from __future__ import annotations

import re
from datetime import datetime

from src.tasks.map_trade.data import TRADE_ITEM_VARIANT_CHAR_TABLE
from src.tasks.map_trade.models import DEFAULT_SALE_WHITELIST
from src.tasks.map_trade.progress import UTC_PLUS_8
from src.tasks.map_trade.trader_constants import split_items
from src.tasks.map_trade.vision import normalize_text


def normalize_trade_name(value: str) -> str:
    return normalize_text(value).translate(TRADE_ITEM_VARIANT_CHAR_TABLE)


def sale_item_identity(name: str, catalog: dict[str, set[str]]) -> str:
    key = normalize_trade_name(name)
    owners = catalog.get(key, {key})
    if not key or len(owners) != 1:
        raise ValueError(f"出售商品身份无法唯一确认：{name}")
    return next(iter(owners))


def parse_sale_reserves(text: str, catalog: dict[str, set[str]] | None = None) -> dict[str, int]:
    """Read item=quantity overrides; -1 excludes an item from sale."""
    result = {}
    for entry in re.split(r"[,，;；\n]", text):
        if not entry.strip():
            continue
        name, separator, raw = entry.partition("=")
        name, raw = name.strip(), raw.strip()
        if not name or not separator or not re.fullmatch(r"-1|[0-9]+", raw):
            raise ValueError("商品保留量请按「物品=非负整数」填写；-1表示不出售。")
        key = sale_item_identity(name, catalog or {})
        quantity = int(raw)
        if key in result and result[key] != quantity:
            raise ValueError(f"商品保留量冲突：{name}")
        result[key] = quantity
    return result


class PriceDiscoveryMixin:
    def _sale_whitelist(self, catalog: dict[str, set[str]]) -> set[str]:
        raw = self.task.config.get("出售白名单", ",".join(DEFAULT_SALE_WHITELIST))
        configured = (
            set(DEFAULT_SALE_WHITELIST)
            | set(self._selected_cooking_recipes())
            | set(split_items(raw))
        )
        return {sale_item_identity(value, catalog) for value in configured}

    _normal = staticmethod(normalize_trade_name)

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

