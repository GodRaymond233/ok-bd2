from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.tasks.map_trade.models import KNOWN_SHOPS, CalendarEntry

CALENDAR_SCHEMA_VERSION = 1
MAX_CALENDAR_BYTES = 256 * 1024


@dataclass(frozen=True)
class LoadedCalendar:
    days: dict[int, tuple[CalendarEntry, ...]]
    source: str
    updated_at: str = ""

    def entries_for(self, day: int) -> tuple[CalendarEntry, ...]:
        return self.days.get(day, ())


def parse_calendar_payload(payload: str | bytes | dict, source: str = "payload") -> LoadedCalendar:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        data = json.loads(payload)
    else:
        data = payload
    if not isinstance(data, dict):
        raise ValueError("价表根节点必须是对象")
    if int(data.get("schema_version", -1)) != CALENDAR_SCHEMA_VERSION:
        raise ValueError("不支持的价表 schema_version")
    if str(data.get("timezone", "")) not in {"Asia/Shanghai", "UTC+8"}:
        raise ValueError("价表 timezone 必须是 Asia/Shanghai")
    updated_at = str(data.get("updated_at", "")).strip()
    if not updated_at:
        raise ValueError("价表缺少 updated_at")
    raw_days = data.get("days")
    if not isinstance(raw_days, dict):
        raise ValueError("价表缺少 days")

    days: dict[int, tuple[CalendarEntry, ...]] = {}
    for day in range(1, 32):
        raw_entries = raw_days.get(str(day))
        if not isinstance(raw_entries, list):
            raise ValueError(f"价表缺少第 {day} 日")
        entries = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise ValueError(f"第 {day} 日条目不是对象")
            item = str(raw.get("item", "")).strip()
            shop = str(raw.get("shop", "")).strip()
            raw_aliases = raw.get("aliases", [])
            if not isinstance(raw_aliases, list):
                raise ValueError(f"第 {day} 日 aliases 必须是数组")
            aliases = tuple(str(value).strip() for value in raw_aliases if str(value).strip())
            sell = raw.get("sell", True)
            if not isinstance(sell, bool):
                raise ValueError(f"第 {day} 日 sell 必须是布尔值")
            raw_reserve = raw.get("reserve", 0)
            if isinstance(raw_reserve, bool) or not isinstance(raw_reserve, int):
                raise ValueError(f"第 {day} 日 reserve 必须是非负整数")
            reserve = raw_reserve
            if reserve < 0:
                raise ValueError(f"第 {day} 日 reserve 必须是非负整数")
            if not item or not shop:
                raise ValueError(f"第 {day} 日条目缺少 item/shop")
            if shop not in KNOWN_SHOPS and shop not in KNOWN_SHOPS.values():
                raise ValueError(f"未知商店：{shop}")
            if shop in KNOWN_SHOPS:
                shop = KNOWN_SHOPS[shop]
            entries.append(
                CalendarEntry(
                    item=item,
                    shop=shop,
                    aliases=aliases,
                    sell=sell,
                    reserve=reserve,
                )
            )
        days[day] = tuple(entries)
    return LoadedCalendar(days=days, source=source, updated_at=updated_at)


def parse_manual_calendar(text: str) -> LoadedCalendar:
    raw_days: dict[str, list[dict[str, object]]] = {}
    seen: set[int] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"自定义价表第 {line_number} 行缺少 '='")
        day_text, values_text = line.split("=", 1)
        try:
            day = int(day_text.strip())
        except ValueError as exc:
            raise ValueError(f"自定义价表第 {line_number} 行日期无效") from exc
        if not 1 <= day <= 31 or day in seen:
            raise ValueError(f"自定义价表日期重复或越界：{day}")
        seen.add(day)
        entries = []
        for token in re.split(r"[,，]", values_text):
            token = token.strip()
            if not token:
                continue
            if "@" not in token:
                raise ValueError(f"第 {day} 日条目缺少 '@'：{token}")
            item, shop = (part.strip() for part in token.rsplit("@", 1))
            entries.append({"item": item, "shop": shop})
        raw_days[str(day)] = entries
    if seen != set(range(1, 32)):
        missing = sorted(set(range(1, 32)) - seen)
        raise ValueError(f"自定义价表必须覆盖 1-31 日，缺少：{missing}")
    return parse_calendar_payload(
        {
            "schema_version": CALENDAR_SCHEMA_VERSION,
            "updated_at": "manual",
            "timezone": "Asia/Shanghai",
            "days": raw_days,
        },
        source="manual",
    )


class PriceCalendarClient:
    def __init__(
        self,
        bundled_path: Path,
        cache_path: Path | str = Path("configs") / "map_trade_price_cache.json",
        sources_path: Path | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.bundled_path = bundled_path
        self.cache_path = Path(cache_path)
        self.sources_path = sources_path
        self.timeout = timeout

    def load(
        self,
        use_online: bool = True,
        manual_text: str = "",
        use_bundled: bool = True,
    ) -> LoadedCalendar:
        if use_bundled:
            return parse_calendar_payload(
                self.bundled_path.read_text(encoding="utf-8"), source="bundled"
            )
        if not use_online:
            return parse_manual_calendar(manual_text)

        cached_envelope = self._read_cache_envelope()
        try:
            sources = self._sources()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            sources = ()
        for url in sources:
            try:
                etag = ""
                if cached_envelope and cached_envelope.get("source") == url:
                    etag = str(cached_envelope.get("etag", ""))
                loaded, payload, response_etag = self._fetch(url, etag=etag)
                self._write_cache(payload, response_etag, url)
                return loaded
            except urllib.error.HTTPError as exc:
                if exc.code == 304 and cached_envelope and cached_envelope.get("source") == url:
                    cached = self._calendar_from_envelope(cached_envelope)
                    if cached is not None:
                        return cached
                    try:
                        loaded, payload, response_etag = self._fetch(url)
                        self._write_cache(payload, response_etag, url)
                        return loaded
                    except (OSError, ValueError, UnicodeError, urllib.error.URLError):
                        pass
                continue
            except (OSError, ValueError, UnicodeError, urllib.error.URLError, json.JSONDecodeError):
                continue

        cached = self._read_cache()
        if cached is not None:
            return cached
        raise RuntimeError("在线价表和本地缓存均不可用")

    def _sources(self) -> tuple[str, ...]:
        if self.sources_path is None or not self.sources_path.exists():
            return ()
        data = json.loads(self.sources_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("价表源配置必须是对象")
        values = []
        for group in ("china", "global"):
            group_values = data.get(group, [])
            if not isinstance(group_values, list):
                raise ValueError(f"价表源 {group} 必须是数组")
            for value in group_values:
                url = str(value).strip()
                if url and url not in values:
                    values.append(url)
        return tuple(values)

    def _fetch(self, url: str, etag: str = "") -> tuple[LoadedCalendar, bytes, str]:
        headers = {"User-Agent": "ok-bd2-price-calendar/1"}
        if etag:
            headers["If-None-Match"] = etag
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            content_type = str(response.headers.get("Content-Type", ""))
            if "text/html" in content_type.lower():
                raise ValueError("在线价表返回了 HTML")
            payload = response.read(MAX_CALENDAR_BYTES + 1)
            if len(payload) > MAX_CALENDAR_BYTES:
                raise ValueError("在线价表过大")
            loaded = parse_calendar_payload(payload, source=url)
            return loaded, payload, str(response.headers.get("ETag", ""))

    def _write_cache(self, payload: bytes, etag: str, source: str) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        parsed = json.loads(payload.decode("utf-8"))
        envelope = {
            "cached_at": datetime.now().isoformat(timespec="seconds"),
            "etag": etag,
            "source": source,
            "payload": parsed,
        }
        temp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.cache_path)

    def _read_cache(self) -> LoadedCalendar | None:
        envelope = self._read_cache_envelope()
        return self._calendar_from_envelope(envelope) if envelope is not None else None

    def _read_cache_envelope(self) -> dict | None:
        if not self.cache_path.exists():
            return None
        try:
            envelope = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return envelope if isinstance(envelope, dict) else None
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    @staticmethod
    def _calendar_from_envelope(envelope: dict | None) -> LoadedCalendar | None:
        if envelope is None:
            return None
        try:
            return parse_calendar_payload(envelope["payload"], source="cache")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
