from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.tasks.map_trade.models import COLLECTABLE_CARDS
from src.tasks.map_trade.progress import UTC_PLUS_8, weekly_cycle_key

COLLECTION_VISUAL_AUDIT_SCHEMA_VERSION = 1
DEFAULT_COLLECTION_VISUAL_AUDIT_PATH = Path("configs") / "map_collection_visual_status.json"
VALID_COLLECTION_CARD_IDS = frozenset(card.card_id for card in COLLECTABLE_CARDS)
EXPECTED_COLLECTION_CARD_IDS = tuple(card.card_id for card in COLLECTABLE_CARDS)
EXCLUDED_COLLECTION_CARD_IDS = ("Q_sp6", "Q_sp18", "Q_sp20")


class CollectionVisualAuditStore:
    """Keep visual card-status observations separate from collection progress."""

    def __init__(
        self,
        path: Path | str = DEFAULT_COLLECTION_VISUAL_AUDIT_PATH,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.now_provider = now_provider or (lambda: datetime.now(UTC_PLUS_8))
        self.state: dict | None = None

    def load(self) -> dict:
        now = self.now_provider()
        week = weekly_cycle_key(now)
        raw = self._read_json()
        if (
            raw.get("schema_version") != COLLECTION_VISUAL_AUDIT_SCHEMA_VERSION
            or raw.get("weekly_key") != week
        ):
            self.state = self._empty_state(week)
            self._save()
            return self.state

        raw_cards = raw.get("cards", {})
        cards = (
            {
                str(card_id): value
                for card_id, value in raw_cards.items()
                if str(card_id) in VALID_COLLECTION_CARD_IDS and isinstance(value, dict)
            }
            if isinstance(raw_cards, dict)
            else {}
        )
        last_scan = raw.get("last_scan", {})
        self.state = {
            "schema_version": COLLECTION_VISUAL_AUDIT_SCHEMA_VERSION,
            "weekly_key": week,
            "expected_card_ids": list(EXPECTED_COLLECTION_CARD_IDS),
            "excluded_card_ids": list(EXCLUDED_COLLECTION_CARD_IDS),
            "cards": cards,
            "last_scan": last_scan if isinstance(last_scan, dict) else {},
        }
        return self.state

    def save_scan(
        self,
        cards: dict[str, dict],
        *,
        missing_card_ids: list[str] | tuple[str, ...] = (),
        conflict_card_ids: list[str] | tuple[str, ...] = (),
        completed: bool,
    ) -> dict:
        state = self.load()
        sanitized = {
            str(card_id): value
            for card_id, value in cards.items()
            if str(card_id) in VALID_COLLECTION_CARD_IDS and isinstance(value, dict)
        }
        state["cards"].update(sanitized)
        state["last_scan"] = {
            "observed_at": self.now_provider().astimezone(UTC_PLUS_8).isoformat(),
            "completed": bool(completed),
            "observed_card_ids": sorted(sanitized),
            "missing_card_ids": sorted(
                {str(value) for value in missing_card_ids} & VALID_COLLECTION_CARD_IDS
            ),
            "conflict_card_ids": sorted(
                {str(value) for value in conflict_card_ids} & VALID_COLLECTION_CARD_IDS
            ),
        }
        self._save()
        return state

    @staticmethod
    def _empty_state(week: str) -> dict:
        return {
            "schema_version": COLLECTION_VISUAL_AUDIT_SCHEMA_VERSION,
            "weekly_key": week,
            "expected_card_ids": list(EXPECTED_COLLECTION_CARD_IDS),
            "excluded_card_ids": list(EXCLUDED_COLLECTION_CARD_IDS),
            "cards": {},
            "last_scan": {},
        }

    def _read_json(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, TypeError, ValueError):
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = self.path.with_suffix(f".corrupt-{stamp}.json")
                shutil.copy2(self.path, backup)
            except OSError:
                pass
            return {}

    def _save(self) -> None:
        if self.state is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)
