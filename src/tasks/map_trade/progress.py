from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.tasks.map_trade.models import COLLECTABLE_CARDS, DAILY_SUBMAP_LIMIT, SUBMAPS_PER_CARD

UTC_PLUS_8 = timezone(timedelta(hours=8), name="UTC+8")
STATE_SCHEMA_VERSION = 1
VALID_CARD_IDS = frozenset(card.card_id for card in COLLECTABLE_CARDS)


def _effective_time(now: datetime) -> datetime:
    localized = now.astimezone(UTC_PLUS_8)
    return localized - timedelta(hours=4)


def daily_cycle_key(now: datetime) -> str:
    return _effective_time(now).date().isoformat()


def weekly_cycle_key(now: datetime) -> str:
    effective = _effective_time(now)
    monday = effective.date() - timedelta(days=effective.weekday())
    return monday.isoformat()


@dataclass
class ProgressState:
    weekly_key: str
    daily_key: str
    cards: dict[str, list[int]] = field(default_factory=dict)
    daily_submaps: int = 0
    depleted_today: bool = False
    favorite_week: str = ""
    cooking_week: str = ""

    def completed_submaps(self, card_id: str) -> set[int]:
        completed = set()
        for value in self.cards.get(card_id, []):
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= number < SUBMAPS_PER_CARD:
                completed.add(number)
        return completed

    @property
    def weekly_submap_count(self) -> int:
        return sum(len(self.completed_submaps(card_id)) for card_id in self.cards)


class ProgressStore:
    def __init__(
        self,
        path: Path | str = Path("configs") / "map_trade_progress.json",
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.now_provider = now_provider or (lambda: datetime.now(UTC_PLUS_8))
        self.state: ProgressState | None = None

    def load(self) -> ProgressState:
        now = self.now_provider()
        week = weekly_cycle_key(now)
        day = daily_cycle_key(now)
        raw = self._read_json()

        if raw.get("schema_version") != STATE_SCHEMA_VERSION or raw.get("weekly_key") != week:
            self.state = ProgressState(weekly_key=week, daily_key=day)
            self.save()
            return self.state

        self.state = ProgressState(
            weekly_key=week,
            daily_key=str(raw.get("daily_key", day)),
            cards=self._sanitize_cards(raw.get("cards", {})),
            daily_submaps=self._safe_nonnegative_int(raw.get("daily_submaps", 0)),
            depleted_today=bool(raw.get("depleted_today", False)),
            favorite_week=str(raw.get("favorite_week", "")),
            cooking_week=str(raw.get("cooking_week", "")),
        )
        if self.state.daily_key != day:
            self.state.daily_key = day
            self.state.daily_submaps = 0
            self.state.depleted_today = False
            self.save()
        return self.state

    @staticmethod
    def _safe_nonnegative_int(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _sanitize_cards(cls, raw_cards) -> dict[str, list[int]]:
        if not isinstance(raw_cards, dict):
            return {}
        cards = {}
        for card, values in raw_cards.items():
            if str(card) not in VALID_CARD_IDS:
                continue
            if not isinstance(values, list):
                continue
            completed = set()
            for value in values:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= number < SUBMAPS_PER_CARD:
                    completed.add(number)
            cards[str(card)] = sorted(completed)
        return cards

    def _read_json(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = self.path.with_suffix(f".corrupt-{stamp}.json")
                shutil.copy2(self.path, backup)
            except OSError:
                pass
            return {}

    def save(self) -> None:
        if self.state is None:
            return
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "weekly_key": self.state.weekly_key,
            "daily_key": self.state.daily_key,
            "cards": self.state.cards,
            "daily_submaps": self.state.daily_submaps,
            "depleted_today": self.state.depleted_today,
            "favorite_week": self.state.favorite_week,
            "cooking_week": self.state.cooking_week,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def mark_submap(self, card_id: str, submap_index: int) -> bool:
        state = self._require_state()
        if card_id not in VALID_CARD_IDS:
            raise ValueError(f"invalid collection card: {card_id}")
        if not 0 <= submap_index < SUBMAPS_PER_CARD:
            raise ValueError(f"invalid submap index: {submap_index}")
        completed = state.completed_submaps(card_id)
        if submap_index in completed:
            return False
        if state.daily_submaps >= DAILY_SUBMAP_LIMIT:
            state.depleted_today = True
            self.save()
            raise RuntimeError("daily collection limit reached")
        completed.add(submap_index)
        state.cards[card_id] = sorted(completed)
        state.daily_submaps += 1
        if state.daily_submaps >= DAILY_SUBMAP_LIMIT:
            state.depleted_today = True
        self.save()
        return True

    def mark_depleted_today(self) -> None:
        self._require_state().depleted_today = True
        self.save()

    def mark_favorites_built(self) -> None:
        state = self._require_state()
        state.favorite_week = state.weekly_key
        self.save()

    def mark_cooking_complete(self) -> None:
        state = self._require_state()
        state.cooking_week = state.weekly_key
        self.save()

    def should_rebuild_favorites(self, every_run: bool = False) -> bool:
        state = self._require_state()
        return every_run or state.favorite_week != state.weekly_key

    def should_cook(self, every_run: bool = False) -> bool:
        state = self._require_state()
        return every_run or state.cooking_week != state.weekly_key

    def _require_state(self) -> ProgressState:
        if self.state is None:
            return self.load()
        return self.state
