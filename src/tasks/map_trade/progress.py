from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from src.tasks.collection.cycle import COLLECTION_REFRESH_HOUR
from src.tasks.map_trade.data import SHOP_PURCHASE_REFERENCES
from src.tasks.map_trade.models import (
    ABSORB_ONLY_VERIFIED_CARD_IDS,
    COLLECTABLE_CARDS,
    COLLECTION_ACTION_NAMES,
    DEFAULT_RECIPES,
    REGISTERED_COLLECTION_CARDS,
    CollectionActionState,
    CollectionMapRole,
    collection_action_name,
    collection_action_policy,
)

UTC_PLUS_8 = timezone(timedelta(hours=8), name="UTC+8")
# Version 3 introduced role-specific weekly cards.  Version 4 added the
# daily/card/role/action ledger.  Version 5 records cooking per recipe so a
# partial cooking run remains safely resumable. Version 6 separates weekly
# map evidence from actual daily casts and includes cartridge cooldowns.
# Version 7 scopes card verification to the action policy that produced it.
# Version 8 derives skill budgets from trusted HUD counts.
# Version 11 adds the chapter-18 safe area; older readers would discard that role.
STATE_SCHEMA_VERSION = 11
SUPPORTED_STATE_SCHEMA_VERSIONS = frozenset({3, 4, 5, 6, 7, 8, 9, STATE_SCHEMA_VERSION})
_development_directory: Path | None = None


def configure_development_ledger(directory: str | None) -> None:
    """Called only by the development entry point; explicit paths bypass it."""
    global _development_directory
    if directory is None:
        _development_directory = None
        return
    path = Path(directory).expanduser()
    if not path.is_absolute():
        raise ValueError("开发账本目录必须是绝对路径")
    path.mkdir(parents=True, exist_ok=True)
    _development_directory = path.resolve()


def ledger_path(filename: str) -> Path:
    if _development_directory is not None:
        return _development_directory / filename
    from ok import Config
    from ok.util.file import get_relative_path

    return Path(get_relative_path(Config.config_folder, filename))


VALID_CARD_IDS = frozenset(card.card_id for card in REGISTERED_COLLECTION_CARDS)
VALID_TARGET_KEYS = {
    card.card_id: frozenset(target.key for target in card.targets)
    for card in REGISTERED_COLLECTION_CARDS
}
VALID_FAVORITE_SHOP_IDS = frozenset(SHOP_PURCHASE_REFERENCES)
VALID_ACTION_NAMES = COLLECTION_ACTION_NAMES
VALID_MAP_ROLE_NAMES = frozenset(role.value for role in CollectionMapRole)


def _normalize_action_policy(required_actions: Iterable[str] | None) -> frozenset[str]:
    return frozenset(collection_action_policy(required_actions))


def _action_policy_key(required_actions: Iterable[str] | None) -> str:
    return "|".join(sorted(_normalize_action_policy(required_actions)))


def _effective_time(now: datetime) -> datetime:
    localized = now.astimezone(UTC_PLUS_8)
    return localized - timedelta(hours=COLLECTION_REFRESH_HOUR)


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
    # Required free casts when planning stopped; None retains an unexplained stop.
    depleted_requirements: dict[str, int] | None = field(default_factory=dict)
    verified_cards: list[str] = field(default_factory=list)
    # Non-default collection policies (for example, development runs with
    # suppression disabled) must not share the production verification marker.
    verified_card_policies: dict[str, list[str]] = field(default_factory=dict)
    visual_completions: dict[str, str] = field(default_factory=dict)
    favorite_week: str = ""
    favorite_cards: list[str] = field(default_factory=list)
    cooking_week: str = ""
    cooking_recipes: list[str] = field(default_factory=list)
    # One record per ``daily_key/card/map_role/action``.  Values are kept as
    # JSON-compatible dictionaries deliberately; this lets a user inspect or
    # repair a stuck action without importing application classes.
    action_records: dict[str, dict[str, object]] = field(default_factory=dict)
    archived_action_records: dict[str, dict[str, object]] = field(default_factory=dict)
    # Last trusted absolute HUD snapshot per action name (used, limit).
    observed_counts: dict[str, tuple[int, int]] = field(default_factory=dict)
    weekly_actions: dict[str, dict[str, object]] = field(default_factory=dict)
    cooldown_records: dict[str, str] = field(default_factory=dict)

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

    def card_verified(
        self,
        card_id: str,
        required_actions: Iterable[str] | None = None,
    ) -> bool:
        if not self.card_complete(card_id):
            return False
        policy = _normalize_action_policy(required_actions)
        if policy == VALID_ACTION_NAMES:
            return card_id in self.verified_cards
        policy_key = "|".join(sorted(policy))
        return card_id in self.verified_card_policies.get(policy_key, [])

    def card_visually_complete(
        self, card_id: str, required_actions: Iterable[str] | None = None,
    ) -> bool:
        # The default policy follows this card's absorb-only menu contract;
        # a reduced policy must still verify every explicitly enabled action.
        required = _normalize_action_policy(required_actions)
        return (
            card_id in ABSORB_ONLY_VERIFIED_CARD_IDS
            and self.visual_completions.get(card_id) == "吸收"
            and (
                required == VALID_ACTION_NAMES or required == {"吸收"}
            )
        )

    @property
    def weekly_collection_complete(self) -> bool:
        """Whether every currently supported weekly card is complete and verified."""
        return all(self.card_verified(card.card_id) for card in COLLECTABLE_CARDS)

    @property
    def completed_favorite_cards(self) -> set[str]:
        return {shop_id for shop_id in self.favorite_cards if shop_id in VALID_FAVORITE_SHOP_IDS}

    @property
    def completed_cooking_recipes(self) -> set[str]:
        return {recipe for recipe in self.cooking_recipes if recipe in DEFAULT_RECIPES}


class ProgressStore:
    def __init__(
        self,
        path: Path | str | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else ledger_path("map_trade_progress.json")
        self.now_provider = now_provider or (lambda: datetime.now(UTC_PLUS_8))
        self.state: ProgressState | None = None
        self._lock_file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.with_suffix(self.path.suffix + ".lock").open("a+b", buffering=0)
        try:
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RuntimeError(f"账本正被另一实例使用：{self.path}") from exc
        self._lock_file = stream
        return self

    def __exit__(self, *_args):
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def load(self) -> ProgressState:
        now = self.now_provider()
        week = weekly_cycle_key(now)
        day = daily_cycle_key(now)
        raw = self._read_json()
        schema_version = self._safe_nonnegative_int(raw.get("schema_version", 0))
        if schema_version > STATE_SCHEMA_VERSION:
            raise RuntimeError(f"账本版本 {schema_version} 高于程序支持版本，请使用兼容的工作树")
        if schema_version >= 3 and schema_version not in SUPPORTED_STATE_SCHEMA_VERSIONS:
            raise RuntimeError(f"账本版本 {schema_version} 尚无兼容迁移，请使用兼容的工作树")
        if raw and schema_version < STATE_SCHEMA_VERSION:
            backup = self.path.with_suffix(f".schema-{schema_version}.backup.json")
            if not backup.exists():
                shutil.copy2(self.path, backup)
        cooldown_records = raw.get("cooldown_records", {})
        legacy_cooldown = self.path.parent / "collection_cooldown.json"
        cooldown_records = (
            {str(k): str(v) for k, v in cooldown_records.items() if isinstance(v, str)}
            if isinstance(cooldown_records, dict) else {}
        )
        if schema_version < 6 and legacy_cooldown.exists():
            legacy_records = json.loads(legacy_cooldown.read_text(encoding="utf-8"))
            if isinstance(legacy_records, dict):
                cooldown_records = {
                    **{str(k): v for k, v in legacy_records.items() if isinstance(v, str)},
                    **cooldown_records,
                }

        if raw.get("weekly_key") != week:
            # Weekly rollover must retain unresolved action evidence for
            # auditability instead of discarding it with the old card state.
            archived = self._archive_payload_records(raw)
            self.state = ProgressState(
                weekly_key=week,
                daily_key=day,
                archived_action_records=archived,
                cooldown_records=cooldown_records,
            )
            self.save()
            return self.state

        # Schema 1/2 stored incompatible map identities. Compatible schemas
        # preserve map/trade state; daily consumption is rebuilt from casts.
        if schema_version not in SUPPORTED_STATE_SCHEMA_VERSIONS:
            cooking_week = str(raw.get("cooking_week", ""))
            self.state = ProgressState(
                weekly_key=week,
                daily_key=day,
                favorite_week=str(raw.get("favorite_week", "")),
                favorite_cards=self._sanitize_favorite_cards(raw.get("favorite_cards", [])),
                cooking_week=cooking_week,
                cooking_recipes=self._migrate_cooking_recipes(
                    raw,
                    cooking_week=cooking_week,
                    weekly_key=week,
                ),
                cooldown_records=cooldown_records,
            )
            self.save()
            return self.state

        return self._restore_current_week(raw, week, day, schema_version, cooldown_records)

    def _restore_current_week(
        self, raw: dict, week: str, day: str, schema_version: int, cooldown_records: dict,
    ) -> ProgressState:
        """Restore compatible records before applying daily rollover and schema migrations."""
        quarantine: dict[str, dict[str, object]] = {}
        raw_actions = raw.get("action_records", {})
        excluded_actions = isinstance(raw_actions, dict) and any(
            isinstance(record, dict) and str(record.get("card_id", "")) not in VALID_CARD_IDS
            for record in raw_actions.values()
        )
        cards = self._sanitize_cards(raw.get("cards", {}))
        active_records = self._sanitize_action_records(
            raw.get("action_records", {}),
            str(raw.get("daily_key", day)),
            quarantine,
        )
        archived_records = self._sanitize_action_records(
            raw.get("archived_action_records", {}),
            None,
            quarantine,
        )
        archived_records.update(quarantine)
        cooking_week = str(raw.get("cooking_week", ""))
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
            verified_card_policies=self._sanitize_verified_card_policies(
                raw.get("verified_card_policies", {}),
                cards,
            ),
            visual_completions={
                card_id: "吸收" for card_id in ABSORB_ONLY_VERIFIED_CARD_IDS
                if isinstance(raw.get("visual_completions"), dict)
                and raw["visual_completions"].get(card_id) == "吸收"
            },
            favorite_week=str(raw.get("favorite_week", "")),
            favorite_cards=self._sanitize_favorite_cards(raw.get("favorite_cards", [])),
            cooking_week=cooking_week,
            cooking_recipes=self._migrate_cooking_recipes(
                raw,
                cooking_week=cooking_week,
                weekly_key=week,
            ),
            action_records=active_records,
            cooldown_records=cooldown_records,
            archived_action_records=archived_records,
            observed_counts=self._sanitize_observed_counts(raw.get("observed_counts", {})),
        )
        return self._migrate_loaded_state(raw, schema_version, excluded_actions, day)

    def _migrate_loaded_state(
        self, raw: dict, schema_version: int, excluded_actions: bool, day: str,
    ) -> ProgressState:
        raw_depleted_requirements = raw.get("depleted_requirements")
        if isinstance(raw_depleted_requirements, dict):
            self.state.depleted_requirements = (
                dict(raw_depleted_requirements)
                if all(action in VALID_ACTION_NAMES and type(amount) is int and amount > 0
                       for action, amount in raw_depleted_requirements.items())
                else None
            )
        elif self.state.depleted_today:
            # Older writers only stored the aggregate flag. Trusted exhausted
            # counts explain it; an unexplained stop remains policy-independent.
            self.state.depleted_requirements = (
                {} if "depleted_requirements" not in raw and self._exhausted_actions() else None
            )
        weekly = raw.get("weekly_actions", {})
        if isinstance(weekly, dict):
            for key, record in weekly.items():
                if not isinstance(record, dict):
                    continue
                parts = str(key).split("|")
                if (len(parts) == 3 and parts[0] in VALID_CARD_IDS
                        and parts[1] in VALID_TARGET_KEYS[parts[0]]
                        and parts[2] in VALID_ACTION_NAMES
                        and record.get("source") in {"click", "observed"}):
                    self.state.weekly_actions[str(key)] = dict(record)
        if schema_version < 7:
            for record in self.state.action_records.values():
                if record["state"] == CollectionActionState.PREEXISTING_USED.value:
                    record.update(state="void", status="void", local_done=False,
                                  pending=False, reservation=False, source="observed")
                elif record.get("local_done"):
                    record["source"] = "click"
                    self._remember_weekly_action(record, "click")
        if schema_version < 7 or excluded_actions:
            self._refresh_daily_counters()
        if schema_version < 8 or excluded_actions:
            # Older ledgers could stop at a fixed budget without HUD evidence.
            self.state.depleted_today = False
            self.state.depleted_requirements = {}
        if self.state.daily_key != day:
            self._archive_daily_actions()
            self.state.daily_key = day
            self.state.daily_submaps = 0
            self.state.daily_summons = 0
            self.state.daily_suppressions = 0
            self.state.depleted_today = False
            self.state.depleted_requirements = {}
            self.state.observed_counts = {}
            self.save()
        elif schema_version != STATE_SCHEMA_VERSION or excluded_actions:
            # Persist the current shape immediately after an older compatible load.
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
    def _sanitize_cooking_recipes(raw_recipes) -> list[str]:
        if not isinstance(raw_recipes, list):
            return []
        completed = {str(value) for value in raw_recipes if str(value) in DEFAULT_RECIPES}
        return [recipe for recipe in DEFAULT_RECIPES if recipe in completed]

    @classmethod
    def _migrate_cooking_recipes(
        cls,
        raw: dict,
        *,
        cooking_week: str,
        weekly_key: str,
    ) -> list[str]:
        if "cooking_recipes" in raw:
            return cls._sanitize_cooking_recipes(raw.get("cooking_recipes"))
        # Before schema 5, cooking had only one whole-week marker.  Preserve
        # that meaning by treating all formerly supported recipes as complete.
        if cooking_week == weekly_key:
            return list(DEFAULT_RECIPES)
        return []

    @staticmethod
    def _sanitize_verified_cards(raw_cards, cards: dict[str, list[str]]) -> list[str]:
        if not isinstance(raw_cards, list):
            return []
        # Preserve the user's explicit verification ledger across schema 3→4
        # migration.  ``card_verified`` still requires all role targets, so a
        # stale/incomplete entry cannot authorize a run; it is merely retained
        # instead of silently discarded during migration.
        return sorted({str(value) for value in raw_cards} & VALID_CARD_IDS)

    @staticmethod
    def _sanitize_verified_card_policies(
        raw_policies,
        cards: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        if not isinstance(raw_policies, dict):
            return {}
        policies: dict[str, list[str]] = {}
        for raw_key, raw_cards in raw_policies.items():
            key = str(raw_key)
            parts = key.split("|")
            if (
                not parts
                or any(part not in VALID_ACTION_NAMES for part in parts)
                or "吸收" not in parts
                or key != "|".join(sorted(set(parts)))
                or set(parts) == VALID_ACTION_NAMES
                or not isinstance(raw_cards, list)
            ):
                continue
            policies[key] = sorted(
                {
                    str(card_id)
                    for card_id in raw_cards
                    if str(card_id) in VALID_CARD_IDS
                    and set(cards.get(str(card_id), ()))
                    == set(VALID_TARGET_KEYS.get(str(card_id), ()))
                }
            )
        return policies

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
            quarantine_key = key if key.startswith("quarantine|") else f"quarantine|{key}"
            if not isinstance(raw_value, dict):
                if quarantine is not None:
                    quarantine[quarantine_key] = {
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
                    quarantine[quarantine_key] = quarantined
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
            baseline_present = "baseline" in record
            baseline = record.get("baseline")
            invalid_baseline = False
            if not isinstance(baseline, (list, tuple)) or len(baseline) != 2:
                record.pop("baseline", None)
                invalid_baseline = baseline_present
            else:
                try:
                    baseline_used, baseline_limit = int(baseline[0]), int(baseline[1])
                except (TypeError, ValueError):
                    record.pop("baseline", None)
                    invalid_baseline = True
                else:
                    if (
                        baseline_used < 0
                        or baseline_limit <= 0
                        or baseline_used > baseline_limit
                    ):
                        record.pop("baseline", None)
                        invalid_baseline = True
                    else:
                        record["baseline"] = [baseline_used, baseline_limit]
            if invalid_baseline:
                record["invalid_baseline"] = baseline
                if state in {
                    CollectionActionState.ARMED.value,
                    CollectionActionState.CLICKED.value,
                    CollectionActionState.BLOCKED.value,
                    CollectionActionState.LOCAL_DONE.value,
                    CollectionActionState.PENDING.value,
                }:
                    # A damaged count cannot prove that a persisted click did not consume.
                    record.update(
                        previous_state=state,
                        state=CollectionActionState.BLOCKED.value,
                        status=CollectionActionState.BLOCKED.value,
                        local_done=False,
                        pending=False,
                        reservation=True,
                        covered=False,
                        message="动作基线损坏，保留消费预留，需核对账本后恢复",
                    )
                elif state == CollectionActionState.ARCHIVED.value:
                    record["reservation"] = False
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
        archived.update(quarantine)
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
            if action_name not in VALID_ACTION_NAMES:
                continue
            if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 2:
                continue
            try:
                used, observed_limit = int(raw_value[0]), int(raw_value[1])
            except (TypeError, ValueError):
                continue
            if used < 0 or observed_limit <= 0 or used > observed_limit:
                continue
            counts[action_name] = (used, observed_limit)
        return counts

    def _archive_daily_actions(self) -> None:
        """Move unresolved records out of the active day at 08:00 Beijing."""

        state = self._require_state()
        for key, record in state.action_records.items():
            archived = dict(record)
            archived["previous_state"] = record["state"]
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
            if not isinstance(value, dict):
                raise ValueError("账本根节点必须是对象")
            return value
        except (OSError, ValueError, TypeError):
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = self.path.with_suffix(f".corrupt-{stamp}.json")
                shutil.copy2(self.path, backup)
            except OSError:
                pass
            raise RuntimeError(f"账本无法读取，已尝试备份；不会新建空进度覆盖：{self.path}")

    def save(self) -> None:
        if self.state is None:
            return
        if self.state.depleted_requirements is not None:
            self.state.depleted_requirements = {
                action: amount for action, amount in self.state.depleted_requirements.items()
                if not self.can_reserve_action(action, amount)
            }
        self.state.depleted_today = self.collection_budget_exhausted()
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "weekly_key": self.state.weekly_key,
            "daily_key": self.state.daily_key,
            "cards": self.state.cards,
            "visual_completions": self.state.visual_completions,
            "daily_submaps": self.state.daily_submaps,
            "daily_summons": self.state.daily_summons,
            "daily_suppressions": self.state.daily_suppressions,
            "depleted_today": self.state.depleted_today,
            "depleted_requirements": self.state.depleted_requirements,
            "verified_cards": sorted(
                set(self.state.verified_cards) & VALID_CARD_IDS
            ),
            "verified_card_policies": {
                key: sorted(set(cards) & VALID_CARD_IDS)
                for key, cards in self.state.verified_card_policies.items()
                if key
                and set(key.split("|")) < VALID_ACTION_NAMES
                and "吸收" in set(key.split("|"))
            },
            "favorite_week": self.state.favorite_week,
            "favorite_cards": sorted(self.state.completed_favorite_cards),
            "cooking_week": self.state.cooking_week,
            "cooking_recipes": [
                recipe
                for recipe in DEFAULT_RECIPES
                if recipe in self.state.completed_cooking_recipes
            ],
            "action_records": self.state.action_records,
            "weekly_actions": self.state.weekly_actions,
            "cooldown_records": self.state.cooldown_records,
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

    def _remember_weekly_action(self, record: dict, source: str) -> None:
        key = "|".join(str(record[name]) for name in ("card_id", "map_role", "action"))
        self.state.weekly_actions[key] = {"source": source, "daily_key": record["daily_key"]}

    def weekly_action_complete(self, card_id: str, map_role, action: str) -> bool:
        key = "|".join((card_id, self._role_name(map_role), action))
        return key in self._require_state().weekly_actions

    def _refresh_daily_counters(self) -> None:
        state = self._require_state()
        counts = {action: sum(
            record.get("action") == action and bool(record.get("local_done"))
            and record.get("source", "click") == "click"
            and record.get("state") not in {"void", "archived", "preexisting_used"}
            for record in state.action_records.values()
        ) for action in VALID_ACTION_NAMES}
        state.daily_submaps = counts["吸收"]
        state.daily_summons = counts["召集"]
        state.daily_suppressions = counts["压制"]

    @staticmethod
    def _action_name(action: str) -> str:
        return collection_action_name(action)

    @staticmethod
    def _role_name(map_role: CollectionMapRole | str) -> str:
        value = getattr(map_role, "value", map_role)
        return str(value)

    def action_limit(self, action: str) -> int | None:
        """Return the last trusted HUD limit, or None until one is observed."""
        observed = self._require_state().observed_counts.get(self._action_name(action))
        return observed[1] if observed is not None else None

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
        limit = self.action_limit(action_name)
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
        return None

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
        reservations = 0
        for record in state.action_records.values():
            if (
                str(record.get("action", "")) != action_name
                or not bool(record.get("reservation", False))
                or str(record.get("state", ""))
                in {
                    CollectionActionState.SETTLED.value,
                    CollectionActionState.VOID.value,
                    CollectionActionState.ARCHIVED.value,
                }
            ):
                continue
            baseline = record.get("baseline")
            if "baseline" in record and (
                not isinstance(baseline, (list, tuple)) or len(baseline) < 1
            ):
                continue
            if "baseline" not in record:
                baseline_used = observed
            else:
                try:
                    baseline_used = int(baseline[0])
                except (TypeError, ValueError, IndexError):
                    continue
            if baseline_used >= observed:
                reservations += 1
        return max(lower_bound, observed + reservations)

    def effective_daily_counts(self) -> dict[str, int]:
        return {
            "吸收": self._effective_used("吸收"),
            "召集": self._effective_used("召集"),
            "压制": self._effective_used("压制"),
        }

    def effective_used(self, action: str) -> int:
        """Public read-only view of the safe effective daily count."""

        return self._effective_used(action)

    def _exhausted_actions(self) -> set[str]:
        state = self._require_state()
        return {
            action
            for action, count in state.observed_counts.items()
            if self._effective_used(action) >= count[1]
        }

    def collection_budget_exhausted(
        self, required_actions: Iterable[str] | None = None,
    ) -> bool:
        state = self._require_state()
        if state.depleted_today and state.depleted_requirements is None:
            return True
        required = _normalize_action_policy(required_actions)
        return any(
            not self.can_reserve_action(action, (state.depleted_requirements or {}).get(action, 1))
            for action in required
        )

    def has_unresolved_action(self, card_id: str, map_role, action: str) -> bool:
        record = self.get_action_record(card_id, map_role, action)
        return record is not None and record.get("state") in {
            CollectionActionState.ARMED.value,
            CollectionActionState.CLICKED.value,
            CollectionActionState.BLOCKED.value,
        }

    def collection_recovery_targets(
        self, card_id: str, required_actions: Iterable[str] | None = None,
    ) -> set[str]:
        """Find durable work that can be reconciled without another cast."""
        state = self._require_state()
        if state.card_verified(card_id, required_actions):
            return set()
        completed = state.completed_targets(card_id)
        required = _normalize_action_policy(required_actions)
        all_actions_ready = self.card_actions_complete(card_id, required)
        return {
            target_key
            for target_key in VALID_TARGET_KEYS.get(card_id, ())
            if all_actions_ready
            or (
                target_key not in completed
                and self._target_actions_ready(card_id, target_key, required_actions=required)
            )
            or any(
                self.has_unresolved_action(card_id, target_key, action)
                for action in (
                    ("吸收",) if target_key == CollectionMapRole.MAIN_AREA.value else required
                )
            )
        }

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
            and bool(record.get("pending", False))
        )

    def can_reserve_action(self, action: str, amount: int = 1) -> bool:
        action_name = self._action_name(action)
        if action_name not in VALID_ACTION_NAMES:
            return False
        limit = self.action_limit(action_name)
        return limit is None or self._effective_used(action) + max(0, int(amount)) <= limit

    def collection_plan_requirements(
        self,
        remaining_targets: list[object] | tuple[object, ...],
        *,
        card_id: str | None = None,
        required_actions: Iterable[str] | None = None,
    ) -> dict[str, int]:
        needed = dict.fromkeys(VALID_ACTION_NAMES, 0)
        enabled = _normalize_action_policy(required_actions)
        for target in remaining_targets:
            actions = (
                ("吸收",) if target.role is CollectionMapRole.MAIN_AREA else VALID_ACTION_NAMES
            )
            for action in actions:
                if action not in enabled:
                    continue
                if card_id is not None and (
                    self.weekly_action_complete(card_id, target.role, action)
                    or self.has_unresolved_action(card_id, target.role, action)
                ):
                    continue
                needed[action] += 1
        return needed

    def can_plan_collection(
        self,
        remaining_targets: list[object] | tuple[object, ...],
        *,
        card_id: str | None = None,
        required_actions: Iterable[str] | None = None,
    ) -> bool:
        """Check enabled actions against trusted counts and uncovered reserves."""
        needed = self.collection_plan_requirements(
            remaining_targets, card_id=card_id, required_actions=required_actions,
        )
        state = self._require_state()
        if state.depleted_today and state.depleted_requirements is None:
            return not any(needed.values())
        return all(
            self.can_reserve_action(action, count)
            for action, count in needed.items()
            if count
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

        Args:
            card_id: A supported collection cartridge identifier.
            map_role: The target map's durable role or key.
            action: The enabled collection action to reserve.
            baseline: Trusted pre-click (used, limit) count, when available.

        Returns:
            True once the reservation is saved; False if an existing record
            or the daily budget prevents another click. Only an explicitly
            VOID record may be re-armed; unresolved consumption stays blocked.

        Raises:
            ValueError: The cartridge identifier is unsupported.
            OSError: Persistence fails; the caller must not issue the click.
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
                    self.mark_depleted_today({action_name: 1})
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
            self.mark_depleted_today({action_name: 1})
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
    ) -> bool:
        state = self._require_state()
        role = self._role_name(map_role)
        if (card_id not in VALID_CARD_IDS or role not in VALID_TARGET_KEYS[card_id]
                or action not in VALID_ACTION_NAMES):
            raise ValueError("invalid preexisting action")
        existing = self.get_action_record(card_id, role, action)
        if existing is not None and existing.get("state") in {"armed", "clicked", "blocked"}:
            return False
        record = dict(
            daily_key=state.daily_key, card_id=card_id, map_role=role, action=action,
            state="preexisting_used", status="preexisting_used", source="observed",
            local_done=True, pending=False, reservation=False, covered=False, attempts=0,
        )
        state.action_records[self.action_key(card_id, role, action)] = record
        self._remember_weekly_action(record, "observed")
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
        first_completion = not bool(record.get("local_done", False))
        record["state"] = next_state
        record["status"] = next_state
        record["local_done"] = True
        record["pending"] = bool(pending)
        record["reservation"] = bool(pending)
        record["source"] = "click"
        if observed is not None:
            record["observed"] = list(observed)
        self._remember_weekly_action(record, "click")
        if first_completion:
            field_name = {
                "吸收": "daily_submaps", "召集": "daily_summons", "压制": "daily_suppressions",
            }[action]
            setattr(self.state, field_name, getattr(self.state, field_name) + 1)
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

        Invalid, bare and stale/lower snapshots are ignored. A changed HUD
        limit is accepted only when no unresolved cast depends on the old one.
        Equal snapshots are accepted only when they are at least the local
        lower bound, making repeated reconciliation idempotent.

        A pending record whose baseline sits below the previously trusted
        snapshot is settled from coverage when that snapshot never saw the
        record eligible: the snapshot may have been persisted from a stable
        read taken right after the click while the record was still
        ARMED/CLICKED (verdict failure, user stop or crash), and from then
        on no snapshot can ever produce a fresh positive delta for it.
        Records the snapshot already offered a delta chance stay on the
        delta path, keeping one consumed unit per settlement.

        Args:
            action: The collection action whose pending records are reconciled.
            observed: A trusted absolute (used, limit) count, or None if unknown.

        Returns:
            The number of records settled. Zero also covers rejected evidence.
            Accepted evidence and resulting settlements are persisted before return.

        Raises:
            OSError: Saving the updated count or settlement records fails.
        """

        state = self._require_state()
        action_name = self._action_name(action)
        lower_bound = max(0, self._daily_lower_bound(action_name) - self.pending_count(action_name))
        accepted = self._admit_count_snapshot(action_name, observed, lower_bound)
        if accepted is None:
            return 0
        used, observed_limit = accepted
        previous = state.observed_counts.get(action_name)
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
                and bool(record.get("pending", False))
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
        state.observed_counts[action_name] = (used, observed_limit)
        settled = self._settle_covered_pending(action_name, pending_records, previous)
        for _key, record in pending_records:
            if positive_delta <= 0:
                break
            if record.get("state") == CollectionActionState.SETTLED.value:
                continue
            baseline = record.get("baseline")
            if isinstance(baseline, (list, tuple)) and baseline:
                try:
                    if used <= int(baseline[0]):
                        continue
                except (TypeError, ValueError):
                    continue
            elif used <= lower_bound:
                continue
            self._settle_action_record(record, (used, observed_limit))
            settled += 1
            positive_delta -= 1
        if previous is None or used > previous[0]:
            # Keep the delta opportunity so later snapshots cannot credit it twice.
            for _key, record in pending_records:
                if record.get("state") != CollectionActionState.SETTLED.value:
                    record["snapshot_seen"] = int(used)
        self.save()
        return settled

    def _admit_count_snapshot(self, action_name, observed, lower_bound) -> tuple[int, int] | None:
        """Validate external count evidence against outstanding reservations and trusted history."""
        if observed is None or action_name not in VALID_ACTION_NAMES or len(observed) != 2:
            return None
        try:
            used, observed_limit = int(observed[0]), int(observed[1])
        except (TypeError, ValueError):
            return None
        if observed_limit <= 0 or used < lower_bound or used < 0 or used > observed_limit:
            return None
        state = self._require_state()
        previous = state.observed_counts.get(action_name)
        if previous is not None and used < previous[0]:
            return None
        for record in state.action_records.values():
            if record.get("action") != action_name or not record.get("reservation"):
                continue
            baseline = record.get("baseline")
            if baseline is not None and baseline[1] != observed_limit:
                return None
            if baseline is None and previous is not None and previous[1] != observed_limit:
                return None
        return used, observed_limit

    @staticmethod
    def _settle_action_record(record: dict, observed: tuple[int, int]) -> None:
        record.update(
            state=CollectionActionState.SETTLED.value,
            status=CollectionActionState.SETTLED.value,
            pending=False,
            reservation=False,
            observed=list(observed),
        )

    def _settle_covered_pending(self, action_name, pending_records, previous) -> int:
        """Recover delayed local verdicts using distinct units from an earlier trusted count."""
        state = self._require_state()
        settled = 0
        if previous is not None:
            for _key, record in pending_records:
                seen = record.get("snapshot_seen")
                try:
                    if seen is not None and int(seen) >= previous[0]:
                        continue
                except (TypeError, ValueError):
                    continue
                baseline = record.get("baseline")
                if not (isinstance(baseline, (list, tuple)) and baseline):
                    continue
                try:
                    baseline_used = int(baseline[0])
                    if baseline_used >= previous[0]:
                        continue
                except (TypeError, ValueError):
                    continue
                # Fit each settlement to a distinct consumed unit between its
                # baseline and observation. Earliest deadlines first preserve
                # capacity for records with later baselines.
                claims = [(previous[0], baseline_used)]
                for other in state.action_records.values():
                    if (
                        other.get("action") != action_name
                        or other.get("state") != CollectionActionState.SETTLED.value
                    ):
                        continue
                    evidence = other.get("observed")
                    origin = other.get("baseline")
                    if isinstance(evidence, (list, tuple)) and len(evidence) == 2:
                        try:
                            start = (
                                int(origin[0])
                                if isinstance(origin, (list, tuple)) and origin else 0
                            )
                            claims.append((int(evidence[0]), start))
                        except (TypeError, ValueError, IndexError):
                            continue
                occupied = set()
                for end, start in sorted(claims):
                    unit = next(
                        (value for value in range(max(0, start) + 1, end + 1)
                         if value not in occupied),
                        None,
                    )
                    if unit is None:
                        break
                    occupied.add(unit)
                if len(occupied) != len(claims):
                    continue
                self._settle_action_record(record, previous)
                settled += 1
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
        *,
        required_actions: Iterable[str] | None = None,
    ) -> bool:
        """Commit one collection target for a card.

        Returns True for a newly completed map, False for a duplicate.
        Daily consumption belongs to action records, not map completion.
        """

        state = self._require_state()
        if card_id not in VALID_CARD_IDS:
            raise ValueError(f"invalid collection card: {card_id}")
        target_key = str(target_key)
        if target_key not in VALID_TARGET_KEYS[card_id]:
            raise ValueError(f"invalid collection target: {card_id}/{target_key}")
        completed = state.completed_targets(card_id)
        if not self._target_actions_ready(
            card_id,
            target_key,
            required_actions=required_actions,
        ):
            raise RuntimeError(
                f"{card_id}/{target_key} requires durable local action records"
            )
        newly_completed = target_key not in completed
        completed.add(target_key)
        state.cards[card_id] = sorted(completed)
        self._cover_action_records(card_id, target_key)
        self.save()
        return newly_completed

    def _target_actions_ready(
        self,
        card_id: str,
        target_key: str,
        *,
        required_actions: Iterable[str] | None = None,
    ) -> bool:
        required = {"吸收"}
        if target_key in {
            CollectionMapRole.BATTLE_AREA_1.value,
            CollectionMapRole.BATTLE_AREA_2.value,
        }:
            required = set(_normalize_action_policy(required_actions))
        state = self._require_state()
        accepted_states = {
            CollectionActionState.PREEXISTING_USED.value,
            CollectionActionState.LOCAL_DONE.value,
            CollectionActionState.PENDING.value,
            CollectionActionState.SETTLED.value,
        }
        for action in required:
            if self.weekly_action_complete(card_id, target_key, action):
                continue
            record = state.action_records.get(
                self.action_key(card_id, target_key, action)
            )
            if record is None:
                return False
            # BLOCKED/VOID 不在 accepted_states 中，已由状态检查排除，无需重复黑名单。
            if (
                str(record.get("state", "")) not in accepted_states
                or not bool(record.get("local_done", False))
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
                record["reservation"] = bool(record.get("pending", False))

    def card_actions_complete(
        self,
        card_id: str,
        required_actions: Iterable[str] | None = None,
    ) -> bool:
        if card_id not in VALID_CARD_IDS:
            return False
        required = _normalize_action_policy(required_actions)
        return all(
            self._target_actions_ready(
                card_id,
                target_key,
                required_actions=required,
            )
            for target_key in VALID_TARGET_KEYS[card_id]
        )

    def mark_card_verified(
        self,
        card_id: str,
        required_actions: Iterable[str] | None = None,
    ) -> bool:
        if card_id not in VALID_CARD_IDS:
            raise ValueError(f"invalid collection card: {card_id}")
        state = self._require_state()
        if not state.card_complete(card_id):
            raise RuntimeError("collection card targets are incomplete")
        required = _normalize_action_policy(required_actions)
        if not self.card_actions_complete(card_id, required):
            raise RuntimeError("collection card action evidence is incomplete")
        if required == VALID_ACTION_NAMES:
            if card_id in state.verified_cards:
                return False
            state.verified_cards.append(card_id)
            state.verified_cards.sort()
        else:
            policy_key = _action_policy_key(required)
            cards = state.verified_card_policies.setdefault(policy_key, [])
            if card_id in cards:
                return False
            cards.append(card_id)
            cards.sort()
        self.save()
        return True

    def mark_card_visual_complete(
        self,
        card_id: str,
        required_actions: Iterable[str] | None = None,
    ) -> bool:
        """Persist a card-menu visual completion as explicit evidence.

        The card-menu probe proves the card is complete but cannot associate
        its icons with durable per-map action records.  Keep that provenance
        explicit instead of fabricating consumed actions; the three target
        keys and the verified-card marker are persisted atomically.
        """

        if card_id not in VALID_CARD_IDS:
            raise ValueError(f"invalid collection card: {card_id}")
        state = self._require_state()
        if card_id in ABSORB_ONLY_VERIFIED_CARD_IDS:
            changed = not state.card_visually_complete(card_id)
            state.visual_completions[card_id] = "吸收"
            self.save()
            return changed
        required = _normalize_action_policy(required_actions)
        target_keys = VALID_TARGET_KEYS[card_id]
        state.cards[card_id] = sorted(target_keys)
        if required == VALID_ACTION_NAMES:
            changed = card_id not in state.verified_cards
            if card_id not in state.verified_cards:
                state.verified_cards.append(card_id)
                state.verified_cards.sort()
        else:
            policy_key = _action_policy_key(required)
            cards = state.verified_card_policies.setdefault(policy_key, [])
            changed = card_id not in cards
            if card_id not in cards:
                cards.append(card_id)
                cards.sort()
        self.save()
        return changed

    def mark_depleted_today(self, requirements: dict[str, int] | None = None) -> None:
        state = self._require_state()
        if not requirements:
            state.depleted_requirements = None
        elif not state.depleted_today or state.depleted_requirements is not None:
            if state.depleted_requirements is None:
                state.depleted_requirements = {}
            for action, amount in requirements.items():
                state.depleted_requirements[action] = max(
                    amount, state.depleted_requirements.get(action, 0),
                )
        state.depleted_today = True
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
        state.cooking_recipes = list(DEFAULT_RECIPES)
        self.save()

    def mark_cooking_recipe_complete(self, recipe: str) -> bool:
        recipe = str(recipe)
        if recipe not in DEFAULT_RECIPES:
            raise ValueError(f"invalid cooking recipe: {recipe}")
        state = self._require_state()
        completed = state.completed_cooking_recipes
        if recipe in completed:
            return False
        completed.add(recipe)
        state.cooking_recipes = [value for value in DEFAULT_RECIPES if value in completed]
        state.cooking_week = (
            state.weekly_key if completed == set(DEFAULT_RECIPES) else ""
        )
        self.save()
        return True

    def cooking_recipe_complete(self, recipe: str) -> bool:
        return str(recipe) in self._require_state().completed_cooking_recipes

    def should_rebuild_favorites(self, every_run: bool = False) -> bool:
        state = self._require_state()
        return (
            every_run
            or state.favorite_week != state.weekly_key
            or state.completed_favorite_cards != VALID_FAVORITE_SHOP_IDS
        )

    def should_cook(
        self,
        every_run: bool = False,
        recipes: list[str] | tuple[str, ...] | None = None,
    ) -> bool:
        state = self._require_state()
        selected = tuple(DEFAULT_RECIPES if recipes is None else recipes)
        if not selected:
            return False
        return every_run or any(
            str(recipe) not in state.completed_cooking_recipes
            for recipe in selected
        )

    def _require_state(self) -> ProgressState:
        if self.state is None:
            return self.load()
        return self.state
