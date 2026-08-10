from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.tasks.map_trade.data import SHOP_PURCHASE_REFERENCES
from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    DAILY_ABSORB_LIMIT,
    DAILY_SUBMAP_LIMIT,
    DAILY_SUMMON_LIMIT,
    DAILY_SUPPRESS_LIMIT,
    CollectionActionState,
    CollectionMapRole,
)

UTC_PLUS_8 = timezone(timedelta(hours=8), name="UTC+8")
# Version 3 introduced role-specific weekly cards.  Version 4 adds the
# daily/card/role/action ledger while retaining every existing field.
STATE_SCHEMA_VERSION = 4
VALID_CARD_IDS = frozenset(card.card_id for card in COLLECTABLE_CARDS)
VALID_TARGET_KEYS = {
    card.card_id: frozenset(target.key for target in card.targets) for card in COLLECTABLE_CARDS
}
VALID_FAVORITE_SHOP_IDS = frozenset(SHOP_PURCHASE_REFERENCES)
VALID_ACTION_NAMES = frozenset({"吸收", "召集", "压制"})
VALID_MAP_ROLE_NAMES = frozenset(role.value for role in CollectionMapRole)


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
    cards: dict[str, list[str]] = field(default_factory=dict)
    daily_submaps: int = 0
    daily_summons: int = 0
    daily_suppressions: int = 0
    depleted_today: bool = False
    verified_cards: list[str] = field(default_factory=list)
    favorite_week: str = ""
    favorite_cards: list[str] = field(default_factory=list)
    cooking_week: str = ""
    # One record per ``daily_key/card/map_role/action``.  Values are kept as
    # JSON-compatible dictionaries deliberately; this lets a user inspect or
    # repair a stuck action without importing application classes.
    action_records: dict[str, dict[str, object]] = field(default_factory=dict)
    archived_action_records: dict[str, dict[str, object]] = field(default_factory=dict)
    # Last trusted absolute HUD snapshot per action name (used, limit).
    observed_counts: dict[str, tuple[int, int]] = field(default_factory=dict)

    def completed_targets(self, card_id: str) -> set[str]:
        allowed = VALID_TARGET_KEYS.get(card_id, frozenset())
        return {str(value) for value in self.cards.get(card_id, []) if str(value) in allowed}

    @property
    def weekly_submap_count(self) -> int:
        return sum(len(self.completed_targets(card_id)) for card_id in self.cards)

    @property
    def daily_absorbs(self) -> int:
        return self.daily_submaps

    def card_complete(self, card_id: str) -> bool:
        return self.completed_targets(card_id) == VALID_TARGET_KEYS.get(
            card_id,
            frozenset(),
        )

    def card_verified(self, card_id: str) -> bool:
        return card_id in self.verified_cards and self.card_complete(card_id)

    @property
    def completed_favorite_cards(self) -> set[str]:
        return {shop_id for shop_id in self.favorite_cards if shop_id in VALID_FAVORITE_SHOP_IDS}


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

        if raw.get("weekly_key") != week:
            # Weekly rollover must retain unresolved action evidence for
            # auditability instead of discarding it with the old card state.
            archived = self._archive_payload_records(raw)
            self.state = ProgressState(
                weekly_key=week,
                daily_key=day,
                archived_action_records=archived,
            )
            self.save()
            return self.state

        schema_version = self._safe_nonnegative_int(raw.get("schema_version", 0))
        # Schema 1/2 stored collection progress in incompatible forms.  Keep
        # the historical safe reset for those files, but migrate schema 3
        # losslessly: cards, verified cards, daily counters, favorites and
        # cooking state all survive while the new ledger starts empty.
        if schema_version not in {3, STATE_SCHEMA_VERSION}:
            self.state = ProgressState(
                weekly_key=week,
                daily_key=day,
                favorite_week=str(raw.get("favorite_week", "")),
                favorite_cards=self._sanitize_favorite_cards(raw.get("favorite_cards", [])),
                cooking_week=str(raw.get("cooking_week", "")),
            )
            self.save()
            return self.state

        quarantine: dict[str, dict[str, object]] = {}
        cards = self._sanitize_cards(raw.get("cards", {}))
        active_records = self._sanitize_action_records(
            raw.get("action_records", {}),
            day,
            quarantine,
        )
        archived_records = self._sanitize_action_records(
            raw.get("archived_action_records", {}),
            None,
            quarantine,
        )
        archived_records.update(quarantine)
        self.state = ProgressState(
            weekly_key=week,
            daily_key=str(raw.get("daily_key", day)),
            cards=cards,
            daily_submaps=self._safe_nonnegative_int(raw.get("daily_submaps", 0)),
            daily_summons=self._safe_nonnegative_int(raw.get("daily_summons", 0)),
            daily_suppressions=self._safe_nonnegative_int(raw.get("daily_suppressions", 0)),
            depleted_today=bool(raw.get("depleted_today", False)),
            verified_cards=self._sanitize_verified_cards(
                raw.get("verified_cards", []),
                cards,
            ),
            favorite_week=str(raw.get("favorite_week", "")),
            favorite_cards=self._sanitize_favorite_cards(raw.get("favorite_cards", [])),
            cooking_week=str(raw.get("cooking_week", "")),
            action_records=active_records,
            archived_action_records=archived_records,
            observed_counts=self._sanitize_observed_counts(raw.get("observed_counts", {})),
        )
        if self.state.daily_key != day:
            self._archive_daily_actions()
            self.state.daily_key = day
            self.state.daily_submaps = 0
            self.state.daily_summons = 0
            self.state.daily_suppressions = 0
            self.state.depleted_today = False
            self.state.observed_counts = {}
            self.save()
        elif schema_version != STATE_SCHEMA_VERSION:
            # Persist the schema-4 shape immediately after a schema-3 load.
            self.save()
        return self.state

    @staticmethod
    def _safe_nonnegative_int(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _sanitize_cards(cls, raw_cards) -> dict[str, list[str]]:
        if not isinstance(raw_cards, dict):
            return {}
        cards = {}
        for card, values in raw_cards.items():
            if str(card) not in VALID_CARD_IDS:
                continue
            if not isinstance(values, list):
                continue
            allowed = VALID_TARGET_KEYS[str(card)]
            completed = {str(value) for value in values if str(value) in allowed}
            cards[str(card)] = sorted(completed)
        return cards

    @staticmethod
    def _sanitize_favorite_cards(raw_cards) -> list[str]:
        if not isinstance(raw_cards, list):
            return []
        return sorted({str(value) for value in raw_cards} & VALID_FAVORITE_SHOP_IDS)

    @staticmethod
    def _sanitize_verified_cards(raw_cards, cards: dict[str, list[str]]) -> list[str]:
        if not isinstance(raw_cards, list):
            return []
        # Preserve the user's explicit verification ledger across schema 3→4
        # migration.  ``card_verified`` still requires all role targets, so a
        # stale/incomplete entry cannot authorize a run; it is merely retained
        # instead of silently discarded during migration.
        return sorted({str(value) for value in raw_cards} & VALID_CARD_IDS)

    @classmethod
    def _sanitize_action_records(
        cls,
        raw_records,
        daily_key: str | None,
        quarantine: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, dict[str, object]]:
        if not isinstance(raw_records, dict):
            return {}
        valid_states = {state.value for state in CollectionActionState}
        records: dict[str, dict[str, object]] = {}
        for raw_key, raw_value in raw_records.items():
            key = str(raw_key)
            if not isinstance(raw_value, dict):
                if quarantine is not None:
                    quarantine[f"quarantine|{key}"] = {
                        "raw": repr(raw_value)[:320],
                        "key": key,
                        "state": CollectionActionState.ARCHIVED.value,
                        "status": CollectionActionState.ARCHIVED.value,
                        "reservation": False,
                    }
                continue
            record = dict(raw_value)
            record_daily = str(record.get("daily_key", ""))
            card_id = str(record.get("card_id", ""))
            role = str(record.get("map_role", ""))
            action = str(record.get("action", ""))
            canonical = "|".join((record_daily, card_id, role, action))
            state = str(record.get("state", record.get("status", "")))
            valid = (
                bool(record_daily)
                and card_id in VALID_CARD_IDS
                and role in VALID_MAP_ROLE_NAMES
                and action in VALID_ACTION_NAMES
                and state in valid_states
                and key == canonical
                and (daily_key is None or record_daily == daily_key)
            )
            if not valid:
                if quarantine is not None:
                    quarantined = dict(record)
                    quarantined["state"] = CollectionActionState.ARCHIVED.value
                    quarantined["status"] = CollectionActionState.ARCHIVED.value
                    quarantined["reservation"] = False
                    quarantine[f"quarantine|{key}"] = quarantined
                continue
            record["state"] = state
            record["status"] = state
            record["card_id"] = card_id
            record["map_role"] = role
            record["action"] = action
            record["daily_key"] = record_daily
            record["covered"] = bool(record.get("covered", False))
            record["local_done"] = bool(record.get("local_done", False))
            record["reservation"] = bool(
                record.get("reservation", state in {
                    CollectionActionState.ARMED.value,
                    CollectionActionState.CLICKED.value,
                    CollectionActionState.LOCAL_DONE.value,
                    CollectionActionState.PENDING.value,
                    CollectionActionState.BLOCKED.value,
                })
            )
            records[canonical] = record
        return records

    @classmethod
    def _archive_payload_records(cls, raw: dict) -> dict[str, dict[str, object]]:
        quarantine: dict[str, dict[str, object]] = {}
        archived = cls._sanitize_action_records(
            raw.get("action_records", {}),
            None,
            quarantine,
        )
        archived.update(
            cls._sanitize_action_records(
                raw.get("archived_action_records", {}),
                None,
                quarantine,
            )
        )
        for key, record in archived.items():
            record["state"] = CollectionActionState.ARCHIVED.value
            record["status"] = CollectionActionState.ARCHIVED.value
            record["reservation"] = False
            archived[key] = record
        return archived

    @classmethod
    def _sanitize_observed_counts(cls, raw_counts) -> dict[str, tuple[int, int]]:
        if not isinstance(raw_counts, dict):
            return {}
        counts: dict[str, tuple[int, int]] = {}
        for action, raw_value in raw_counts.items():
            action_name = str(action)
            limit = cls._action_limit(action_name)
            if limit is None:
                continue
            if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 2:
                continue
            try:
                used, observed_limit = int(raw_value[0]), int(raw_value[1])
            except (TypeError, ValueError):
                continue
            if used < 0 or observed_limit != limit or used > observed_limit:
                continue
            counts[action_name] = (used, observed_limit)
        return counts

    def _archive_daily_actions(self) -> None:
        """Move unresolved records out of the active day at the 04:00 edge."""

        state = self._require_state()
        for key, record in state.action_records.items():
            archived = dict(record)
            archived["state"] = CollectionActionState.ARCHIVED.value
            archived["status"] = CollectionActionState.ARCHIVED.value
            archived["reservation"] = False
            state.archived_action_records[str(key)] = archived
        state.action_records = {}

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
            "daily_summons": self.state.daily_summons,
            "daily_suppressions": self.state.daily_suppressions,
            "depleted_today": self.state.depleted_today,
            "verified_cards": sorted(
                set(self.state.verified_cards) & VALID_CARD_IDS
            ),
            "favorite_week": self.state.favorite_week,
            "favorite_cards": sorted(self.state.completed_favorite_cards),
            "cooking_week": self.state.cooking_week,
            "action_records": self.state.action_records,
            "archived_action_records": self.state.archived_action_records,
            "observed_counts": {
                action: list(value)
                for action, value in self.state.observed_counts.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    @staticmethod
    def _action_name(action: str) -> str:
        value = getattr(action, "value", action)
        return str(value)

    @staticmethod
    def _role_name(map_role: CollectionMapRole | str) -> str:
        value = getattr(map_role, "value", map_role)
        return str(value)

    @staticmethod
    def _action_limit(action: str) -> int | None:
        return {
            "吸收": DAILY_ABSORB_LIMIT,
            "召集": DAILY_SUMMON_LIMIT,
            "压制": DAILY_SUPPRESS_LIMIT,
        }.get(str(action))

    def _daily_lower_bound(self, action: str) -> int:
        state = self._require_state()
        return {
            "吸收": state.daily_absorbs,
            "召集": state.daily_summons,
            "压制": state.daily_suppressions,
        }.get(self._action_name(action), 0)

    def trusted_action_baseline(self, action: str) -> tuple[int, int] | None:
        """Return the trusted count immediately before a local action."""

        action_name = self._action_name(action)
        limit = self._action_limit(action_name)
        if limit is None:
            return None
        state = self._require_state()
        observed = state.observed_counts.get(action_name)
        try:
            if (
                observed is not None
                and len(observed) == 2
                and int(observed[1]) == limit
                and 0 <= int(observed[0]) <= limit
            ):
                return (int(observed[0]), limit)
        except (TypeError, ValueError):
            pass
        return (self._daily_lower_bound(action_name), limit)

    def action_key(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
    ) -> str:
        state = self._require_state()
        return "|".join(
            (
                state.daily_key,
                str(card_id),
                self._role_name(map_role),
                self._action_name(action),
            )
        )

    def get_action_record(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
    ) -> dict[str, object] | None:
        return self._require_state().action_records.get(
            self.action_key(card_id, map_role, action)
        )

    def _effective_used(self, action: str) -> int:
        state = self._require_state()
        action_name = self._action_name(action)
        lower_bound = {
            "吸收": state.daily_absorbs,
            "召集": state.daily_summons,
            "压制": state.daily_suppressions,
        }.get(action_name, 0)
        observed = state.observed_counts.get(action_name, (0, 0))[0]
        reservations = sum(
            1
            for record in state.action_records.values()
            if str(record.get("action", "")) == action_name
            and bool(record.get("reservation", False))
            and not bool(record.get("covered", False))
            and str(record.get("state", ""))
            not in {
                CollectionActionState.SETTLED.value,
                CollectionActionState.VOID.value,
                CollectionActionState.ARCHIVED.value,
            }
        )
        return max(lower_bound, observed) + reservations

    def effective_daily_counts(self) -> dict[str, int]:
        return {
            "吸收": self._effective_used("吸收"),
            "召集": self._effective_used("召集"),
            "压制": self._effective_used("压制"),
        }

    def effective_used(self, action: str) -> int:
        """Public read-only view of the safe effective daily count."""

        return self._effective_used(action)

    def uncovered_reservations(self, action: str | None = None) -> int:
        records = self._require_state().action_records.values()
        action_name = self._action_name(action) if action is not None else None
        return sum(
            1
            for record in records
            if bool(record.get("reservation", False))
            and not bool(record.get("covered", False))
            and (
                action_name is None
                or str(record.get("action", "")) == action_name
            )
        )

    def pending_action_records(self) -> tuple[dict[str, object], ...]:
        state = self._require_state()
        return tuple(
            dict(record)
            for record in state.action_records.values()
            if str(record.get("state", ""))
            in {
                CollectionActionState.PREEXISTING_USED.value,
                CollectionActionState.PENDING.value,
                CollectionActionState.LOCAL_DONE.value,
            }
            and bool(record.get("local_done", False))
        )

    def can_reserve_action(self, action: str, amount: int = 1) -> bool:
        limit = self._action_limit(self._action_name(action))
        if limit is None:
            return False
        return self._effective_used(action) + max(0, int(amount)) <= limit

    def can_plan_collection(self, remaining_targets: list[object] | tuple[object, ...]) -> bool:
        """Check a complete card against lower bounds and uncovered reserves."""

        absorb = len(remaining_targets)
        battle = sum(
            getattr(target, "role", None)
            in {CollectionMapRole.BATTLE_AREA_1, CollectionMapRole.BATTLE_AREA_2}
            for target in remaining_targets
        )
        return (
            self._effective_used("吸收") + absorb <= DAILY_ABSORB_LIMIT
            and self._effective_used("召集") + battle <= DAILY_SUMMON_LIMIT
            and self._effective_used("压制") + battle <= DAILY_SUPPRESS_LIMIT
        )

    def arm_action(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
        *,
        baseline: tuple[int, int] | None = None,
    ) -> bool:
        """Persist ARMED before a recognized-center click.

        Existing terminal or unresolved records are never re-armed.  A
        caller must reconcile an unresolved record from a later bright frame;
        this is the crash/restart no-repeat-click guard.
        """

        state = self._require_state()
        if card_id not in VALID_CARD_IDS:
            raise ValueError(f"invalid collection card: {card_id}")
        role = self._role_name(map_role)
        action_name = self._action_name(action)
        key = self.action_key(card_id, role, action_name)
        existing = state.action_records.get(key)
        if existing is not None:
            if str(existing.get("state", "")) == CollectionActionState.VOID.value:
                if not self.can_reserve_action(action_name):
                    state.depleted_today = True
                    self.save()
                    return False
                existing["state"] = CollectionActionState.ARMED.value
                existing["status"] = CollectionActionState.ARMED.value
                existing["local_done"] = False
                existing["pending"] = False
                existing["covered"] = False
                existing["reservation"] = True
                if baseline is not None:
                    existing["baseline"] = list(baseline)
                existing["attempts"] = self._safe_nonnegative_int(
                    existing.get("attempts", 0)
                ) + 1
                self.save()
                return True
            return False
        if not self.can_reserve_action(action_name):
            state.depleted_today = True
            self.save()
            return False
        record = {
            "daily_key": state.daily_key,
            "card_id": str(card_id),
            "map_role": role,
            "action": action_name,
            "state": CollectionActionState.ARMED.value,
            "status": CollectionActionState.ARMED.value,
            "local_done": False,
            "pending": False,
            "covered": False,
            "reservation": True,
            "attempts": 1,
        }
        if baseline is not None:
            record["baseline"] = list(baseline)
        state.action_records[key] = record
        self.save()
        return True

    def mark_action_clicked(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
    ) -> bool:
        record = self.get_action_record(card_id, map_role, action)
        if record is None:
            return False
        state = str(record.get("state", ""))
        if state in {
            CollectionActionState.ARMED.value,
            CollectionActionState.CLICKED.value,
        }:
            record["state"] = CollectionActionState.CLICKED.value
            record["status"] = CollectionActionState.CLICKED.value
            record["reservation"] = True
            self.save()
            return True
        return False

    def mark_action_preexisting_used(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
        *,
        baseline: tuple[int, int] | None = None,
        covered_observed: tuple[int, int] | None = None,
    ) -> bool:
        trusted_baseline = None
        limit = self._action_limit(self._action_name(action))
        if baseline is not None and limit is not None:
            try:
                candidate = (int(baseline[0]), int(baseline[1]))
                if candidate[1] == limit and 0 <= candidate[0] <= limit:
                    trusted_baseline = candidate
            except (TypeError, ValueError, IndexError):
                pass
        if trusted_baseline is None:
            trusted_baseline = self.trusted_action_baseline(action)
        if trusted_baseline is None:
            return False
        if not self.arm_action(
            card_id,
            map_role,
            action,
            baseline=trusted_baseline,
        ):
            return False
        record = self.get_action_record(card_id, map_role, action)
        if record is None:
            return False
        if covered_observed is not None:
            limit = self._action_limit(self._action_name(action))
            try:
                covered_valid = (
                    limit is not None
                    and len(covered_observed) == 2
                    and int(covered_observed[1]) == limit
                    and 0 <= int(covered_observed[0]) <= limit
                )
            except (TypeError, ValueError, IndexError):
                covered_valid = False
            if covered_valid:
                record["state"] = CollectionActionState.SETTLED.value
                record["status"] = CollectionActionState.SETTLED.value
                record["local_done"] = True
                record["pending"] = False
                record["covered"] = True
                record["reservation"] = False
                record["observed"] = list(covered_observed)
                self.save()
                return True
        record["state"] = CollectionActionState.PREEXISTING_USED.value
        record["status"] = CollectionActionState.PREEXISTING_USED.value
        record["local_done"] = True
        record["pending"] = True
        record["reservation"] = True
        self.save()
        return True

    def mark_action_local_done(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
        *,
        pending: bool = True,
        observed: tuple[int, int] | None = None,
    ) -> bool:
        record = self.get_action_record(card_id, map_role, action)
        if record is None:
            return False
        state = str(record.get("state", ""))
        if state in {
            CollectionActionState.SETTLED.value,
            CollectionActionState.VOID.value,
            CollectionActionState.ARCHIVED.value,
        }:
            return False
        next_state = (
            CollectionActionState.PENDING.value
            if pending
            else (
                CollectionActionState.SETTLED.value
                if observed is not None
                else CollectionActionState.LOCAL_DONE.value
            )
        )
        record["state"] = next_state
        record["status"] = next_state
        record["local_done"] = True
        record["pending"] = bool(pending)
        record["reservation"] = bool(pending) and not bool(record.get("covered", False))
        if observed is not None:
            record["observed"] = list(observed)
        self.save()
        return True

    def mark_action_blocked(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
        message: str = "",
    ) -> bool:
        record = self.get_action_record(card_id, map_role, action)
        if record is None:
            return False
        record["state"] = CollectionActionState.BLOCKED.value
        record["status"] = CollectionActionState.BLOCKED.value
        record["local_done"] = False
        record["pending"] = False
        record["reservation"] = True
        if message:
            record["message"] = str(message)[:320]
        self.save()
        return True

    def mark_action_void(
        self,
        card_id: str,
        map_role: CollectionMapRole | str,
        action: str,
        message: str = "",
    ) -> bool:
        record = self.get_action_record(card_id, map_role, action)
        if record is None:
            return False
        record["state"] = CollectionActionState.VOID.value
        record["status"] = CollectionActionState.VOID.value
        record["local_done"] = False
        record["pending"] = False
        record["reservation"] = False
        if message:
            record["message"] = str(message)[:320]
        self.save()
        return True

    def reconcile_pending(self, action: str, observed: tuple[int, int] | None) -> int:
        """Settle pending records from a trusted later absolute snapshot.

        Invalid, bare, wrong-denominator, stale/lower snapshots are ignored.
        Equal snapshots are accepted only when they are at least the local
        lower bound, making repeated reconciliation idempotent.
        """

        state = self._require_state()
        action_name = self._action_name(action)
        limit = self._action_limit(action_name)
        if observed is None or limit is None or len(observed) != 2:
            return 0
        try:
            used, observed_limit = int(observed[0]), int(observed[1])
        except (TypeError, ValueError):
            return 0
        if (
            observed_limit != limit
            or used < 0
            or used > observed_limit
        ):
            return 0
        previous = state.observed_counts.get(action_name)
        if previous is not None and (
            previous[1] != observed_limit or used < previous[0]
        ):
            return 0
        lower_bound = self._daily_lower_bound(action_name)
        if used < lower_bound:
            return 0
        pending_records = sorted(
            (
                (key, record)
                for key, record in state.action_records.items()
                if str(record.get("action", "")) == action_name
                and str(record.get("state", ""))
                in {
                    CollectionActionState.PENDING.value,
                    CollectionActionState.LOCAL_DONE.value,
                    CollectionActionState.PREEXISTING_USED.value,
                }
                and bool(record.get("local_done", False))
            ),
            key=lambda item: item[0],
        )
        baselines = []
        for _key, record in pending_records:
            baseline = record.get("baseline")
            if isinstance(baseline, (list, tuple)) and baseline:
                try:
                    baselines.append(max(0, int(baseline[0])))
                except (TypeError, ValueError):
                    continue
            else:
                baselines.append(lower_bound)
        if previous is None:
            # Before any trusted absolute snapshot, the oldest eligible
            # pending baseline is the conservative origin for a positive
            # delta.  The persisted local lower bound remains a validity
            # floor above, but must not inflate this origin.
            trusted_base = min(baselines, default=lower_bound)
        else:
            # Once an absolute snapshot is trusted, every later delta is
            # measured from that snapshot.  Local target commits may raise
            # the lower bound in between maps; using it as a delta base would
            # hide the +1 needed to settle the previous map's pending action.
            trusted_base = previous[0]
        positive_delta = max(0, used - trusted_base)
        # Equal-to-baseline evidence is not a new trusted snapshot.  In
        # particular, a first preexisting USED frame at 0/21 must not create
        # a zero observation that later masks its one-unit consumption.
        update_observed = (
            previous is not None
            or positive_delta > 0
            or used >= observed_limit
        )
        if update_observed:
            state.observed_counts[action_name] = (used, observed_limit)
            if used >= observed_limit:
                state.depleted_today = True
        settled = 0
        for _key, record in pending_records:
            if positive_delta <= 0:
                break
            baseline = record.get("baseline")
            if isinstance(baseline, (list, tuple)) and baseline:
                try:
                    if used <= int(baseline[0]):
                        continue
                except (TypeError, ValueError):
                    continue
            elif used <= lower_bound:
                continue
            record["state"] = CollectionActionState.SETTLED.value
            record["status"] = CollectionActionState.SETTLED.value
            record["pending"] = False
            record["reservation"] = False
            record["observed"] = [used, observed_limit]
            settled += 1
            positive_delta -= 1
        self.save()
        return settled

    def pending_count(self, action: str | None = None) -> int:
        records = self.pending_action_records()
        if action is None:
            return len(records)
        action_name = self._action_name(action)
        return sum(str(record.get("action", "")) == action_name for record in records)

    def mark_target(
        self,
        card_id: str,
        target_key: str,
    ) -> bool:
        """Commit one collection target for a card.

        Returns True when the target was newly committed.  A duplicate target
        or a daily quota that is already exhausted returns False; quota
        failure also persists ``depleted_today`` instead of raising, so the
        task scheduler can turn it into a clean depleted result.
        """

        state = self._require_state()
        if card_id not in VALID_CARD_IDS:
            raise ValueError(f"invalid collection card: {card_id}")
        target_key = str(target_key)
        if target_key not in VALID_TARGET_KEYS[card_id]:
            raise ValueError(f"invalid collection target: {card_id}/{target_key}")
        completed = state.completed_targets(card_id)
        if target_key in completed:
            self._cover_action_records(card_id, target_key)
            return False
        if not self._target_actions_ready(card_id, target_key):
            raise RuntimeError(
                f"{card_id}/{target_key} requires durable local action records"
            )
        is_battle = target_key in {
            CollectionMapRole.BATTLE_AREA_1.value,
            CollectionMapRole.BATTLE_AREA_2.value,
        }
        absorb_reservation = self._target_reservations(card_id, target_key, "吸收")
        if self._effective_used("吸收") - absorb_reservation + 1 > DAILY_ABSORB_LIMIT:
            state.depleted_today = True
            self.save()
            return False
        summon_reservation = self._target_reservations(card_id, target_key, "召集")
        if is_battle and (
            self._effective_used("召集") - summon_reservation + 1 > DAILY_SUMMON_LIMIT
        ):
            state.depleted_today = True
            self.save()
            return False
        suppress_reservation = self._target_reservations(card_id, target_key, "压制")
        if is_battle and (
            self._effective_used("压制") - suppress_reservation + 1 > DAILY_SUPPRESS_LIMIT
        ):
            state.depleted_today = True
            self.save()
            return False
        completed.add(target_key)
        state.cards[card_id] = sorted(completed)
        state.daily_submaps += 1
        if is_battle:
            state.daily_summons += 1
            state.daily_suppressions += 1
        if (
            state.daily_submaps >= DAILY_SUBMAP_LIMIT
            or state.daily_summons >= DAILY_SUMMON_LIMIT
            or state.daily_suppressions >= DAILY_SUPPRESS_LIMIT
        ):
            state.depleted_today = True
        self._cover_action_records(card_id, target_key)
        self.save()
        return True

    def _target_actions_ready(self, card_id: str, target_key: str) -> bool:
        required = {"吸收"}
        if target_key in {
            CollectionMapRole.BATTLE_AREA_1.value,
            CollectionMapRole.BATTLE_AREA_2.value,
        }:
            required = {"吸收", "召集", "压制"}
        state = self._require_state()
        accepted_states = {
            CollectionActionState.PREEXISTING_USED.value,
            CollectionActionState.LOCAL_DONE.value,
            CollectionActionState.PENDING.value,
            CollectionActionState.SETTLED.value,
        }
        for action in required:
            record = state.action_records.get(
                self.action_key(card_id, target_key, action)
            )
            if record is None:
                return False
            if (
                str(record.get("state", "")) not in accepted_states
                or not bool(record.get("local_done", False))
                or str(record.get("state", ""))
                in {
                    CollectionActionState.BLOCKED.value,
                    CollectionActionState.VOID.value,
                }
            ):
                return False
        return True

    def _cover_action_records(self, card_id: str, target_key: str) -> None:
        state = self._require_state()
        for record in state.action_records.values():
            if (
                str(record.get("card_id", "")) == str(card_id)
                and str(record.get("map_role", "")) == str(target_key)
                and bool(record.get("local_done", False))
            ):
                record["covered"] = True
                record["reservation"] = False

    def _target_reservations(self, card_id: str, target_key: str, action: str) -> int:
        state = self._require_state()
        return sum(
            1
            for record in state.action_records.values()
            if str(record.get("card_id", "")) == str(card_id)
            and str(record.get("map_role", "")) == str(target_key)
            and str(record.get("action", "")) == str(action)
            and bool(record.get("reservation", False))
            and not bool(record.get("covered", False))
        )

    def mark_card_verified(self, card_id: str) -> bool:
        if card_id not in VALID_CARD_IDS:
            raise ValueError(f"invalid collection card: {card_id}")
        state = self._require_state()
        if not state.card_complete(card_id):
            raise RuntimeError("collection card targets are incomplete")
        if card_id in state.verified_cards:
            return False
        state.verified_cards.append(card_id)
        state.verified_cards.sort()
        self.save()
        return True

    def mark_depleted_today(self) -> None:
        self._require_state().depleted_today = True
        self.save()

    def mark_favorites_built(self) -> None:
        state = self._require_state()
        if state.completed_favorite_cards != VALID_FAVORITE_SHOP_IDS:
            raise RuntimeError("favorite cartridge rebuild is incomplete")
        state.favorite_week = state.weekly_key
        self.save()

    def mark_favorite_card(self, shop_id: str) -> bool:
        if shop_id not in VALID_FAVORITE_SHOP_IDS:
            raise ValueError(f"invalid favorite shop: {shop_id}")
        state = self._require_state()
        completed = state.completed_favorite_cards
        if shop_id in completed:
            return False
        completed.add(shop_id)
        state.favorite_cards = sorted(completed)
        self.save()
        return True

    def clear_favorite_cards(self) -> None:
        state = self._require_state()
        state.favorite_cards = []
        state.favorite_week = ""
        self.save()

    def favorite_card_complete(self, shop_id: str) -> bool:
        return shop_id in self._require_state().completed_favorite_cards

    def mark_cooking_complete(self) -> None:
        state = self._require_state()
        state.cooking_week = state.weekly_key
        self.save()

    def should_rebuild_favorites(self, every_run: bool = False) -> bool:
        state = self._require_state()
        return (
            every_run
            or state.favorite_week != state.weekly_key
            or state.completed_favorite_cards != VALID_FAVORITE_SHOP_IDS
        )

    def should_cook(self, every_run: bool = False) -> bool:
        state = self._require_state()
        return every_run or state.cooking_week != state.weekly_key

    def _require_state(self) -> ProgressState:
        if self.state is None:
            return self.load()
        return self.state
