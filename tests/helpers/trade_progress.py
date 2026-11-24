"""Disk-backed collection ledger fixtures with a controllable Beijing clock."""

import json
from datetime import datetime

from src.tasks.map_trade.models import CARD_BY_ID
from src.tasks.map_trade.progress import UTC_PLUS_8, ProgressStore, weekly_cycle_key
from tests.helpers.map_trade import _seed_action_records


class TradeLedger:
    def __init__(self, path, now=None):
        self.path = path
        self.now = now or datetime(2026, 10, 13, 12, tzinfo=UTC_PLUS_8)
        self.reopen()

    def reopen(self):
        self.store = ProgressStore(self.path, lambda: self.now)
        self.store.load()
        return self.store

    def reserve(self, address, baseline, phase="armed"):
        store = self.store
        if not store.arm_action(*address, baseline=baseline):
            raise AssertionError(f"failed to reserve {address} at {baseline}")
        if phase in ("clicked", "pending", "settled"):
            store.mark_action_clicked(*address)
        if phase == "blocked":
            store.mark_action_blocked(*address)
        elif phase == "pending":
            store.mark_action_local_done(*address, pending=True)
        elif phase == "settled":
            observed = baseline[0] + 1, baseline[1]
            store.mark_action_local_done(*address, pending=False, observed=observed)
            store.reconcile_pending(address[-1], observed)
        elif phase not in ("armed", "clicked"):
            raise ValueError(phase)


def legacy_payload(ledger, version):
    store = ledger.store
    store.mark_action_preexisting_used("Q_sp1", "main_area", "吸收")
    store.mark_target("Q_sp1", "main_area")
    ledger.reserve(("Q_sp1", "battle_area_1", "召集"), (0, 21), "clicked")
    for target in CARD_BY_ID["Q_sp18"].targets[1:]:
        _seed_action_records(store, "Q_sp18", target.key)
        store.mark_target("Q_sp18", target.key)
    if version == 7:
        store.mark_action_preexisting_used("Q_sp1", "battle_area_1", "压制")
        store.reconcile_pending("压制", (60, 75))
    raw = json.loads(ledger.path.read_bytes())
    week = weekly_cycle_key(ledger.now)
    raw.update(
        schema_version=version,
        depleted_today=True,
        favorite_week=week,
        favorite_cards=["S1"],
        cooking_week=week,
        cooldown_records={"event": "current"},
        verified_cards=["Q_sp18"],
    )
    for field in (
        "favorite_plan_version",
        "cooking_recipes",
        "weekly_actions",
        "depleted_requirements",
    ):
        raw.pop(field, None)
    if version <= 3 or version == 5:
        raw["action_records"] = {
            k: r for k, r in raw["action_records"].items() if r["card_id"] == "Q_sp1"
        }
    if version <= 3:
        raw.update(daily_submaps=5, daily_summons=2, daily_suppressions=2)
        if version < 3:
            raw["cards"] = {
                "Q_sp1": [0, 1] if version == 1 else ["main_area", "battle_area_1", "battle_area_2"]
            }
    elif version == 5:
        observed = next(r for r in raw["action_records"].values() if r["action"] == "吸收")
        observed.update(pending=True, reservation=True)
        observed.pop("source")
    elif version == 7:
        raw["daily_suppressions"] = 60
    (ledger.path.parent / "collection_cooldown.json").write_text(
        json.dumps({"event": "old", "legacy": "retained"}), encoding="utf-8"
    )
    return raw
