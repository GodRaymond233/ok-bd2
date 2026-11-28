import ast
import json
import tempfile
import threading
import unittest
import urllib.error
from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException
from ok.task.TaskExecutor import TaskExecutor

from src.tasks.BargainLevelTask import (
    BARGAIN_CONFIRM_POINT,
    BARGAIN_ENTRY_POINT,
    BARGAIN_PROMPT,
    BUY_ALL_COLLECTION_PROMPT,
    CLOSE_SHOP_CONFIRM_POINT,
    CLOSE_SHOP_PROMPTS,
    COLLECTION_BACK_POINT,
    POST_RECOGNITION_DELAY_SECONDS,
    BargainLevelTask,
)
from src.tasks.collection.cycle import CooldownStore
from src.tasks.map_trade.calendar import (
    PURCHASE_STOCK_REFRESH_HOUR,
    SALE_PRICE_REFRESH_HOUR,
    PriceCalendarClient,
    parse_calendar_payload,
    purchase_stock_date,
    sale_price_calendar_date,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    COOKING_GROUPS,
    DEFAULT_RECIPES,
    CalendarEntry,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import (
    STATE_SCHEMA_VERSION,
    UTC_PLUS_8,
    VALID_FAVORITE_SHOP_IDS,
    ProgressStore,
    daily_cycle_key,
    weekly_cycle_key,
)
from src.tasks.map_trade.trader import Trader
from src.tasks.map_trade.vision import Vision
from src.tasks.MapTradeTask import (
    MapTradeTask,
    _cooking_migration_values,
    _trade_section_migration_values,
)
from tests.helpers.map_trade import _seed_action_records, create_trader
from tests.helpers.trade_progress import TradeLedger, legacy_payload

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_CALENDAR = ROOT / "assets" / "map_trade" / "price_calendar.v1.json"
ABSORB = ("Q_sp1", "main_area", "吸收")


class ProgressTest(unittest.TestCase):
    def ledger(self, now=None):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return TradeLedger(directory / "progress.json", now)

    def test_retry_budget_persists_the_original_and_second_attempt(self):
        for end, baseline, allowed in (
            ("settled", (4, 21), True),
            ("full", (20, 21), False),
            ("unsent", (4, 21), True),
            ("void", (4, 21), True),
        ):
            with self.subTest(outcome=end):
                ledger = self.ledger()
                store = ledger.store
                store.reconcile_pending("吸收", baseline)
                ledger.reserve(ABSORB, baseline, "blocked")
                if end in ("unsent", "void"):
                    with patch.object(store, "save", side_effect=OSError("full")):
                        with self.assertRaises(OSError):
                            store.arm_action_retry(*ABSORB, baseline)
                    self.assertEqual(baseline[0] + 1, store.effective_used("吸收"))
                    self.assertEqual({}, store.state.archived_action_records)
                self.assertEqual(allowed, store.arm_action_retry(*ABSORB, baseline))
                store = ledger.reopen()
                self.assertEqual(baseline[0] + 1 + allowed, store.effective_used("吸收"))
                self.assertFalse(store.arm_action_retry(*ABSORB, baseline))
                if end == "settled":
                    store.mark_action_local_done(*ABSORB, pending=False, observed=(5, 21))
                    store.reconcile_pending("吸收", (5, 21))
                    store = ledger.reopen()
                    self.assertEqual(
                        (6, 1), (store.effective_used("吸收"), store.pending_count("吸收"))
                    )
                    self.assertTrue(store.can_reserve_action("吸收"))
                    self.assertTrue(store.weekly_action_complete(*ABSORB))
                elif end in ("unsent", "void"):
                    if end == "unsent":
                        store.mark_action_unsent(*ABSORB)
                    else:
                        store.mark_action_void(*ABSORB, "明确失败")
                    store = ledger.reopen()
                    self.assertEqual(5, store.effective_used("吸收"))
                    self.assertTrue(store.has_unresolved_action(*ABSORB))
                    self.assertEqual(end == "unsent", store.arm_action_retry(*ABSORB, baseline))

    def test_unresolved_increment_requires_unique_matching_spend(self):
        for extra, external, used, recover in (
            (False, False, 5, True),
            (False, False, 4, False),
            (True, False, 5, False),
            (False, True, 5, False),
        ):
            with self.subTest(extra=extra, external=external, used=used):
                ledger = self.ledger()
                store = ledger.store
                store.reconcile_pending("吸收", (4, 21))
                ledger.reserve(ABSORB, (4, 21), "blocked")
                if extra:
                    ledger.reserve(("Q_sp2", "main_area", "吸收"), (4, 21))
                if external:
                    store.reconcile_pending("吸收", (5, 21))
                self.assertEqual(recover, store.recover_action_increment(*ABSORB, (used, 21)))
                self.assertEqual(recover, ledger.reopen().weekly_action_complete(*ABSORB))

    def test_confirmed_count_epoch_preserves_history_and_rejects_stale_confirmation(self):
        ledger = self.ledger()
        store = ledger.store
        addr = ("Q_sp7", "battle_area_1", "压制")
        store.reconcile_pending("压制", (6, 50))
        ledger.reserve(addr, (6, 50), "settled")
        self.assertEqual(0, store.reconcile_pending("压制", (0, 55), confirmed_limit=True))
        token = store.request_count_reset("压制", (0, 55))
        store.load()
        self.assertFalse(store.confirm_count_reset("压制", (1, 55), token))
        self.assertFalse(store.confirm_count_reset("压制", (0, 55), "wrong"))
        with patch.object(store, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.confirm_count_reset("压制", (0, 55), token)
        self.assertEqual((7, 50), store.trusted_action_baseline("压制"))
        self.assertTrue(store.confirm_count_reset("压制", (0, 55), token))
        store.load()
        self.assertEqual(0, store.effective_used("压制"))
        self.assertEqual(1, store.state.daily_suppressions)
        self.assertTrue(store.weekly_action_complete("Q_sp7", "battle_area_1", "压制"))
        self.assertEqual(1, len(store.state.archived_action_records))
        self.assertFalse(store.confirm_count_reset("压制", (0, 55), token))
        store.arm_action("Q_sp7", "battle_area_2", "压制", baseline=(0, 55))
        store.mark_action_local_done(
            "Q_sp7", "battle_area_2", "压制", pending=False, observed=(1, 55)
        )
        store.reconcile_pending("压制", (1, 55))
        self.assertEqual(1, store.effective_used("压制"))
        self.assertEqual(2, store.state.daily_suppressions)

    def test_card_review_retains_consumption_and_only_rearms_resolved_actions(self):
        card = CARD_BY_ID["Q_sp9"]
        addr = (card.card_id, "main_area", "吸收")
        for phase in (
            "observed",
            "next_day",
            "armed",
            "clicked",
            "settled",
            "history",
            "pending click",
            "settled click",
        ):
            with self.subTest(phase=phase):
                ledger = self.ledger()
                store = ledger.store
                for target in card.targets[1:]:
                    _seed_action_records(store, card.card_id, target.key)
                    store.mark_target(card.card_id, target.key)
                if phase in ("pending click", "settled click"):
                    count = store.effective_used("吸收")
                    store.reconcile_pending("吸收", (count, 21))
                    ledger.reserve(
                        addr, (count, 21), "pending" if phase == "pending click" else "settled"
                    )
                else:
                    store.mark_action_preexisting_used(*addr)
                store.mark_target(*addr[:2])
                key = store.action_key(*addr)
                if phase in {"armed", "clicked", "settled"}:
                    store.state.action_records[key].update(state=phase, attempts=1)
                if phase == "history":
                    store.state.archived_action_records["prior"] = dict(
                        store.state.action_records[key],
                        state="settled",
                        source="click",
                        attempts=1,
                        daily_key=daily_cycle_key(ledger.now - timedelta(days=1)),
                    )
                if phase == "next_day":
                    store.save()
                    ledger.now += timedelta(days=1)
                    store = ledger.reopen()
                before = store.effective_daily_counts()
                record = store.get_action_record(*addr)
                old_state = record["state"] if record else None
                self.assertEqual(
                    {t.key for t in card.targets}, store.review_card_actions(card.card_id, {"吸收"})
                )
                self.assertEqual(before, store.effective_daily_counts())
                self.assertFalse(store.weekly_action_complete(*addr))
                self.assertFalse(
                    store.weekly_action_complete(card.card_id, "battle_area_1", "吸收")
                )
                self.assertTrue(store.weekly_action_complete(card.card_id, "battle_area_2", "压制"))
                self.assertEqual(set(), ledger.reopen().state.completed_targets(card.card_id))
                store = ledger.store
                record = store.get_action_record(*addr)
                self.assertEqual(old_state, record["state"] if record else None)
                if phase in ("armed", "clicked", "pending click"):
                    self.assertFalse(store.prepare_supplement(*addr))
                    if phase == "pending click":
                        self.assertTrue(record["reservation"])
                elif phase == "settled click":
                    self.assertTrue(store.prepare_supplement(*addr))
                    self.assertTrue(store.arm_action(*addr, baseline=(count + 1, 21)))
                    store.mark_action_local_done(*addr, pending=False, observed=(count + 2, 21))
                    store.reconcile_pending("吸收", (count + 2, 21))
                    store = ledger.reopen()
                    self.assertEqual(count + 2, store.state.daily_absorbs)
                    (archived,) = store.state.archived_action_records.values()
                    self.assertEqual(
                        ("settled", [count + 1, 21]),
                        (archived["previous_state"], archived["observed"]),
                    )

    def test_daily_and_weekly_rollover_archive_spending_at_beijing_eight(self):
        # 北京时间每天 08:00 归档当日预留；周内保留已完成的地图动作，周一 08:00 清空周进度。
        for day, weekly in ((13, False), (19, True)):
            with self.subTest(day=day):
                ledger = self.ledger(datetime(2026, 10, day, 7, 59, 59, tzinfo=UTC_PLUS_8))
                # 从 v8 迁移后，活动卡的视觉完成标记和实际施放仍按各自的策略跨日。
                raw = legacy_payload(ledger, 8)
                ledger.path.write_text(json.dumps(raw), encoding="utf-8")
                store = ledger.reopen()
                _seed_action_records(store, "Q_ep3", "battle_area_1")
                store.mark_target("Q_ep3", "battle_area_1")
                verified = list(store.state.verified_cards)
                store.mark_card_visual_complete("Q_ep2")
                self.assertEqual(verified, store.state.verified_cards)
                self.assertNotIn("Q_ep2", store.state.cards)
                self.assertFalse(store.state.card_verified("Q_ep2"))
                recorded_cards = {r["card_id"] for r in store.state.action_records.values()}
                self.assertNotIn("Q_ep2", recorded_cards)
                role = ("Q_sp2", "battle_area_1")
                for action in ("吸收", "召集", "压制"):
                    addr = (*role, action)
                    limit = 60 if action == "压制" else 21
                    ledger.reserve(addr, (0, limit), "clicked" if action == "压制" else "pending")
                    self.assertFalse(store.arm_action(*addr))
                self.assertEqual(2, store.pending_count())
                active_keys = set(store.state.action_records)
                cooldown = CooldownStore(progress=store)
                cooldown.mark_complete("Story_02", now=ledger.now)
                store.mark_cooking_recipe_complete(DEFAULT_RECIPES[0])
                store.mark_depleted_today()
                ledger.now += timedelta(seconds=1)
                store = ledger.reopen()
                self.assertEqual(0, store.pending_count())
                self.assertEqual({}, store.state.action_records)
                self.assertEqual({}, store.state.observed_counts)
                self.assertEqual({"吸收": 0, "召集": 0, "压制": 0}, store.effective_daily_counts())
                self.assertFalse(store.state.depleted_today)
                self.assertEqual(len(active_keys), len(store.state.archived_action_records))
                for record in store.state.archived_action_records.values():
                    self.assertEqual(("archived", False), (record["state"], record["reservation"]))
                self.assertEqual(not weekly, store.weekly_action_complete(*role, "吸收"))
                self.assertEqual(not weekly, store.weekly_action_complete(*role, "召集"))
                self.assertFalse(store.weekly_action_complete(*role, "压制"))
                self.assertEqual(not weekly, store.cooking_recipe_complete(DEFAULT_RECIPES[0]))
                available, _ = CooldownStore(progress=store).check_available(
                    "Story_02", now=ledger.now
                )
                self.assertEqual(weekly, available)
                self.assertEqual(not weekly, store.state.card_visually_complete("Q_ep2"))
                self.assertFalse(store.state.card_visually_complete("Q_ep2", ("吸收", "召集")))
                self.assertEqual(set() if weekly else {"battle_area_1"},
                                 store.state.completed_targets("Q_ep3"))
                if weekly:
                    self.assertEqual({}, store.state.weekly_actions)
                else:
                    store.mark_action_preexisting_used(*role, "压制")
                    self.assertTrue(store.mark_target(*role))
                    self.assertEqual(
                        {"吸收": 0, "召集": 0, "压制": 0}, store.effective_daily_counts()
                    )

    def test_weekly_trade_checklists_resume_from_the_last_saved_item(self):
        for name, items in (
            ("favorite", sorted(VALID_FAVORITE_SHOP_IDS)),
            ("recipe", DEFAULT_RECIPES[:2]),
        ):
            with self.subTest(checklist=name):
                ledger = self.ledger()
                store = ledger.store

                def operations(s):
                    if name == "favorite":
                        return (
                            s.mark_favorite_card,
                            s.favorite_card_complete,
                            s.should_rebuild_favorites,
                        )
                    return (
                        s.mark_cooking_recipe_complete,
                        s.cooking_recipe_complete,
                        lambda: s.should_cook(recipes=items),
                    )

                mark, complete, due = operations(store)
                self.assertTrue(due())
                with store:
                    with self.assertRaisesRegex(RuntimeError, "账本正被另一实例使用"):
                        with ProgressStore(ledger.path):
                            self.fail("duplicate ledger writer acquired the lock")
                    self.assertTrue(mark(items[0]))
                self.assertFalse(mark(items[0]))
                self.assertTrue(complete(items[0]))
                self.assertFalse(complete(items[1]))
                self.assertTrue(due())
                if name == "favorite":
                    with self.assertRaisesRegex(RuntimeError, "rebuild is incomplete"):
                        store.mark_favorites_built()
                else:
                    self.assertFalse(store.should_cook(recipes=items[:1]))
                    self.assertTrue(store.should_cook(every_run=True, recipes=items[:1]))
                store = ledger.reopen()
                mark, complete, due = operations(store)
                self.assertTrue(complete(items[0]))
                self.assertFalse(complete(items[1]))
                self.assertTrue(due())
                for item in items[1:]:
                    self.assertTrue(mark(item))
                if name == "favorite":
                    store.mark_favorites_built()
                self.assertFalse(due())
                if name == "favorite":
                    raw = json.loads(ledger.path.read_bytes())
                    raw["favorite_plan_version"] = 1
                    ledger.path.write_text(json.dumps(raw), encoding="utf-8")
                    migrated = ledger.reopen()
                    self.assertTrue(migrated.should_rebuild_favorites())
                    self.assertFalse(migrated.favorite_card_complete(items[0]))
                    store.clear_favorite_cards()
                    self.assertTrue(store.should_rebuild_favorites())
                    self.assertEqual(set(), store.state.completed_favorite_cards)

    def test_skill_budgets_follow_persisted_hud_limits_and_reset_each_day(self):
        for action, limit in (("吸收", 30), ("召集", 28), ("压制", 75)):
            with self.subTest(action=action):
                ledger = self.ledger()
                store, path = ledger.store, ledger.path
                self.assertIsNone(store.action_limit(action))
                self.assertIsNone(store.trusted_action_baseline(action))
                self.assertFalse(store.collection_budget_exhausted())
                self.assertTrue(store.can_plan_collection(CARD_BY_ID["Q_sp1"].targets))
                store.reconcile_pending(action, (limit - 1, limit))
                self.assertTrue(store.can_reserve_action(action))
                self.assertFalse(store.can_reserve_action(action, 2))
                self.assertFalse(store.can_plan_collection(CARD_BY_ID["Q_sp1"].targets))
                store.load()
                self.assertEqual((limit - 1, limit), store.trusted_action_baseline(action))
                store.reconcile_pending(action, (limit, limit))
                self.assertTrue(store.collection_budget_exhausted())
                self.assertFalse(store.can_reserve_action(action))
                # Schema 8/9 writers recorded only an aggregate depleted flag.
                raw = json.loads(path.read_text(encoding="utf-8"))
                raw.pop("depleted_requirements")
                path.write_text(json.dumps(raw), encoding="utf-8")
                store.load()
                self.assertEqual(
                    action != "压制",
                    store.collection_budget_exhausted(("吸收", "召集")),
                )
                self.assertEqual(
                    action == "压制",
                    store.can_plan_collection(
                        CARD_BY_ID["Q_sp1"].targets,
                        required_actions=("吸收", "召集"),
                    ),
                )
                store.save()
                store.reconcile_pending(action, (limit, limit + 5))
                self.assertEqual(limit + 5, store.action_limit(action))
                self.assertFalse(store.collection_budget_exhausted())
                store.mark_depleted_today()
                store.load()
                self.assertTrue(store.collection_budget_exhausted(("吸收", "召集")))
                self.assertTrue(store.can_reserve_action(action))
                ledger.now += timedelta(days=1)
                store.load()
                self.assertIsNone(store.action_limit(action))
                self.assertFalse(store.collection_budget_exhausted())

    def test_collection_plan_reuses_only_its_own_last_reservation(self):
        for phase in ("armed", "clicked", "blocked"):
            with self.subTest(phase=phase):
                ledger = self.ledger()
                store = ledger.store
                card = CARD_BY_ID["Q_sp2"]
                target = card.targets[-1]
                _seed_action_records(store, card.card_id, target.key, actions=("召集", "压制"))
                store.reconcile_pending("吸收", (20, 21))
                ledger.reserve((card.card_id, target.role, "吸收"), (20, 21), phase)
                store.load()
                self.assertEqual(21, store.effective_used("吸收"))
                self.assertTrue(store.can_plan_collection((target,), card_id=card.card_id))
                self.assertFalse(store.can_plan_collection((target,), card_id="Q_sp1"))
                self.assertFalse(store.arm_action("Q_sp1", "main_area", "吸收"))
                store.reconcile_pending("压制", (1, 75))
                store.load()
                self.assertEqual({"吸收": 1}, store.state.depleted_requirements)
                store.mark_action_void(card.card_id, target.role, "吸收")
                store.load()
                self.assertFalse(store.collection_budget_exhausted())
                self.assertTrue(store.can_plan_collection((target,), card_id=card.card_id))
                self.assertTrue(
                    store.arm_action(
                        card.card_id,
                        target.role,
                        "吸收",
                        baseline=(20, 21),
                    )
                )
                store.mark_depleted_today()
                self.assertTrue(store.can_plan_collection((target,), card_id=card.card_id))
                self.assertFalse(store.can_reserve_action("吸收"))

    def test_hud_reconciliation_accepts_only_covered_local_reservations(self):
        addr = ("Q_cp5", "main_area", "吸收")
        # Each observation lists (HUD count, confirmed new limit, newly settled records).
        for phase, baseline, reads, final_baseline, final_state in (
            ("armed", (7, 121), [((8, 21), True, 0)], (7, 121), "armed"),
            ("clicked", (7, 121), [((8, 21), True, 0)], (7, 121), "clicked"),
            ("pending", (7, 121), [((8, 21), False, 0)], (7, 121), "pending"),
            ("pending", (7, 121), [((7, 21), True, 0), ((9, 21), True, 0)], (7, 121), "pending"),
            ("pending", (7, 121), [((8, 21), True, 1), ((8, 21), True, 0)], (8, 21), "settled"),
            (
                "pending",
                (0, 21),
                [
                    ((0, 21), False, 0),
                    ((1, 20), False, 0),
                    ((1, 21), False, 1),
                    ((1, 21), False, 0),
                ],
                (1, 21),
                "settled",
            ),
            (
                "preexisting",
                None,
                [
                    ((0, 21), False, 0),
                    ((1, 20), False, 0),
                    ((1, 21), False, 0),
                    ((0, 21), False, 0),
                    ((1, 21), False, 0),
                ],
                (1, 21),
                "preexisting_used",
            ),
        ):
            with self.subTest(phase=phase, baseline=baseline, reads=reads):
                ledger = self.ledger()
                store = ledger.store
                if baseline is None:
                    self.assertTrue(store.mark_action_preexisting_used(*addr))
                    record = store.get_action_record(*addr)
                    self.assertNotIn("baseline", record)
                    self.assertFalse(record["pending"])
                    self.assertEqual(0, store.effective_used("吸收"))
                    self.assertEqual({}, store.state.observed_counts)
                else:
                    store.reconcile_pending("吸收", baseline)
                    ledger.reserve(addr, baseline, phase)
                if baseline == (0, 21):
                    self.assertEqual(1, store.effective_used("吸收"))
                    self.assertTrue(store.mark_target(*addr[:2]))
                    self.assertEqual(1, store.state.daily_submaps)
                    self.assertEqual(1, store.effective_used("吸收"))
                    self.assertFalse(store.mark_target(*addr[:2]))
                for count, confirmed, settled in reads:
                    self.assertEqual(
                        settled, store.reconcile_pending("吸收", count, confirmed_limit=confirmed)
                    )
                    if baseline is None and count[0] == 1:
                        self.assertEqual(count, store.state.observed_counts["吸收"])
                store = ledger.reopen()
                self.assertEqual(final_baseline, store.trusted_action_baseline("吸收"))
                self.assertEqual(final_state, store.get_action_record(*addr)["state"])
                if final_state == "settled":
                    self.assertEqual(0, store.pending_count())

    def test_delayed_reservations_share_one_persisted_hud_snapshot(self):
        # BUG-20260906-02: the HUD advances before the clicked record is eligible for settlement.
        for roles, pending_before, next_used in (
            (("main_area",), False, 1),
            *(
                (("main_area", "battle_area_1", "battle_area_2"), pending, used)
                for pending in (False, True)
                for used in (1, 2)
            ),
        ):
            with self.subTest(roles=roles, pending_before=pending_before, next_used=next_used):
                ledger = self.ledger()
                store = ledger.store
                addresses = [("Q_sp2", role, "吸收") for role in roles]
                for addr in addresses:
                    ledger.reserve(addr, (0, 21), "clicked")
                if pending_before:
                    store.mark_action_local_done(*addresses[0], pending=True)
                self.assertEqual(int(pending_before), store.reconcile_pending("吸收", (1, 21)))
                self.assertEqual((1, 21), store.state.observed_counts["吸收"])
                for addr in addresses:
                    store.mark_action_local_done(*addr, pending=True)
                for newly_settled in (next_used - int(pending_before), 0):
                    store = ledger.reopen()
                    self.assertEqual(
                        newly_settled, store.reconcile_pending("吸收", (next_used, 21))
                    )
                    self.assertEqual(len(roles) - next_used, store.pending_count("吸收"))
                    self.assertEqual((next_used, 21), store.state.observed_counts["吸收"])
                if len(roles) == 1:
                    record = store.get_action_record(*addresses[0])
                    self.assertEqual(
                        ("settled", False, False, [1, 21]),
                        (
                            record["state"],
                            record["pending"],
                            record["reservation"],
                            record["observed"],
                        ),
                    )

    def test_loading_damaged_records_quarantines_bad_keys_and_holds_uncertain_spend(self):
        for phase in ("armed", "clicked", "blocked", "local_done", "pending"):
            with self.subTest(phase=phase):
                ledger = self.ledger()
                store, path = ledger.store, ledger.path
                ledger.reserve(ABSORB, (0, 21))
                raw = json.loads(path.read_bytes())
                (key,) = raw["action_records"]
                record = raw["action_records"][key]
                record.update(baseline=[], state=phase)
                path.write_text(json.dumps(raw), encoding="utf-8")
                isolated = ledger.reopen()
                self.assertEqual(1, isolated.effective_used("吸收"))
                self.assertFalse(isolated.arm_action(*ABSORB, baseline=(0, 21)))
                isolated.save()
                self.assertFalse(ledger.reopen().arm_action(*ABSORB))
                self.assertEqual(1, ledger.store.effective_used("吸收"))
                role = "battle_area_1"
                valid = dict(
                    record,
                    map_role=role,
                    baseline=[0, 21],
                    state="pending",
                    pending=True,
                    local_done=True,
                )

                def key_for(r):
                    return "|".join(r[k] for k in ("daily_key", "card_id", "map_role", "action"))

                bad_action = dict(valid, action="探查")
                invalid = [
                    dict(valid, daily_key=daily_cycle_key(ledger.now - timedelta(days=1))),
                    bad_action,
                    dict(valid, map_role="main"),
                ]
                raw["action_records"].update({key_for(r): r for r in [valid, *invalid]})
                raw["action_records"].update(
                    {"not-canonical": valid, "broken-value": "not-a-record"}
                )
                raw["observed_counts"] = {"吸收": [1, 21], "探查": [99, 99]}
                path.write_text(json.dumps(raw), encoding="utf-8")
                store = ledger.reopen()
                self.assertEqual({key, key_for(valid)}, set(store.state.action_records))
                held = store.state.action_records[key]
                self.assertNotIn("baseline", held)
                self.assertEqual(
                    ("blocked", [], phase),
                    (held["state"], held["invalid_baseline"], held["previous_state"]),
                )
                self.assertFalse(store.arm_action(*ABSORB, baseline=(0, 21)))
                self.assertEqual({"吸收": (1, 21)}, store.state.observed_counts)
                self.assertGreaterEqual(len(store.state.archived_action_records), 5)
                for archived in store.state.archived_action_records.values():
                    self.assertEqual(
                        ("archived", False), (archived["state"], archived["reservation"])
                    )
                self.assertEqual(2, store.effective_used("吸收"))
                store.save()
                store = ledger.reopen()
                self.assertFalse(store.arm_action(*ABSORB))
                self.assertEqual(2, store.effective_used("吸收"))
                ledger.now += timedelta(days=7)
                rolled = ledger.reopen().state
                self.assertIn("quarantine|broken-value", rolled.archived_action_records)
                self.assertIn("quarantine|" + key_for(bad_action), rolled.archived_action_records)
                evidence = dict(rolled.archived_action_records)
                self.assertEqual(evidence, ledger.reopen().state.archived_action_records)
                self.assertEqual(0, ledger.store.effective_used("吸收"))

    def test_excluded_cards_are_archived_without_local_consumption(self):
        ledger = self.ledger()
        store, path = ledger.store, ledger.path
        _seed_action_records(store, "Q_sp1", "main_area")
        store.mark_target("Q_sp1", "main_area")
        raw = json.loads(path.read_text(encoding="utf-8"))
        sample = next(iter(raw["action_records"].values()))
        roles = ("main_area", "battle_area_1", "battle_area_2")
        for role in roles:
            record = dict(sample, card_id="Q_sp6", map_role=role)
            key = "|".join((record["daily_key"], "Q_sp6", role, "吸收"))
            raw["action_records"][key] = record
            raw["weekly_actions"][f"Q_sp6|{role}|吸收"] = {
                "source": "click",
                "daily_key": record["daily_key"],
            }
        raw["cards"]["Q_sp6"] = list(roles)
        raw["verified_cards"].append("Q_sp6")
        raw.update(daily_submaps=4, depleted_today=True)
        raw["observed_counts"] = {"吸收": [4, 21]}
        path.write_text(json.dumps(raw), encoding="utf-8")

        state = store.load()
        self.assertNotIn("Q_sp6", state.cards)
        self.assertNotIn("Q_sp6", state.verified_cards)
        self.assertFalse(any(key.startswith("Q_sp6|") for key in state.weekly_actions))
        self.assertFalse(
            any(record["card_id"] == "Q_sp6" for record in state.action_records.values())
        )
        self.assertEqual(1, state.daily_submaps)
        self.assertEqual(4, store.effective_used("吸收"))
        self.assertFalse(state.depleted_today)
        archived = set(state.archived_action_records)
        self.assertEqual(3, len(archived))
        state = store.load()
        store.save()
        self.assertEqual(archived, set(state.archived_action_records))
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("Q_sp6", persisted["cards"])
        self.assertEqual(1, persisted["daily_submaps"])

    def test_supported_submaps_persist_across_daily_budgets_until_verified(self):
        ledger = self.ledger(datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8))
        store = ledger.store
        self.assertFalse(store.state.weekly_collection_complete)
        for index, card in enumerate(COLLECTABLE_CARDS):
            if index and index % 7 == 0:
                previous = COLLECTABLE_CARDS[index - 1]
                ledger.now += timedelta(days=1)
                store = ledger.reopen()
                self.assertTrue(store.state.card_verified(previous.card_id))
                self.assertEqual(
                    {t.key for t in previous.targets},
                    store.state.completed_targets(previous.card_id),
                )
                self.assertFalse(store.state.depleted_today)
                self.assertEqual({"吸收": 0, "召集": 0, "压制": 0}, store.effective_daily_counts())
            if index % 7 == 0:
                store.reconcile_pending("吸收", (0, 21))
            for target in card.targets:
                _seed_action_records(store, card.card_id, target.key)
                self.assertTrue(store.mark_target(card.card_id, target.key))
                self.assertTrue(ledger.path.exists())
                self.assertFalse(ledger.path.with_suffix(".json.tmp").exists())
            self.assertTrue(store.mark_card_verified(card.card_id))
            if index == 6:
                self.assertEqual(
                    (21, 14, 14),
                    (
                        store.state.daily_absorbs,
                        store.state.daily_summons,
                        store.state.daily_suppressions,
                    ),
                )
                self.assertTrue(store.state.depleted_today)
                self.assertEqual(21, store.state.weekly_submap_count)
                for completed in COLLECTABLE_CARDS[:7]:
                    self.assertTrue(store.state.card_verified(completed.card_id))
                self.assertFalse(
                    store.arm_action(COLLECTABLE_CARDS[7].card_id, "main_area", "吸收")
                )
        state = ledger.reopen().state
        self.assertEqual(72, state.weekly_submap_count)
        self.assertTrue(state.weekly_collection_complete)
        state.verified_cards.remove(COLLECTABLE_CARDS[0].card_id)
        self.assertFalse(state.weekly_collection_complete)

    def test_legacy_schema_migrations(self):
        for version in range(1, 10):
            with self.subTest(version=version):
                ledger = self.ledger()
                path = ledger.path
                raw = legacy_payload(ledger, version)
                before = json.dumps(raw).encode()
                path.write_bytes(before)
                store = ledger.reopen()
                state = store.state
                self.assertEqual(
                    before, path.with_suffix(f".schema-{version}.backup.json").read_bytes()
                )
                saved = json.loads(path.read_bytes())
                self.assertEqual(STATE_SCHEMA_VERSION, saved["schema_version"])
                self.assertEqual(list(DEFAULT_RECIPES), saved["cooking_recipes"])
                self.assertEqual(weekly_cycle_key(ledger.now), state.cooking_week)
                self.assertEqual(set(DEFAULT_RECIPES), state.completed_cooking_recipes)
                self.assertFalse(store.should_cook(recipes=DEFAULT_RECIPES))
                self.assertEqual(set(), state.completed_favorite_cards)
                self.assertFalse(state.card_verified("Q_sp18"))
                cooldowns = {"event": "current"}
                if version < 6:
                    cooldowns["legacy"] = "retained"
                self.assertEqual(cooldowns, state.cooldown_records)
                self.assertEqual(
                    set() if version < 3 else {"main_area"}, state.completed_targets("Q_sp1")
                )
                self.assertEqual(
                    set() if version < 3 else {"battle_area_1", "battle_area_2"},
                    state.completed_targets("Q_sp18"),
                )
                self.assertFalse(store.weekly_action_complete("Q_sp18", "main_area", "吸收"))
                self.assertEqual(version >= 8, state.depleted_today)
                self.assertEqual(0, store.pending_count("吸收"))
                if version <= 3:
                    self.assertEqual(
                        (0, 0, 0),
                        (state.daily_submaps, state.daily_summons, state.daily_suppressions),
                    )
                if version < 3:
                    self.assertEqual({}, state.cards)
                    self.assertEqual([], state.verified_cards)
                else:
                    summon = store.get_action_record("Q_sp1", "battle_area_1", "召集")
                    self.assertEqual("clicked", summon["state"])
                if version == 5:
                    self.assertFalse(store.weekly_action_complete(*ABSORB))
                    self.assertEqual(0, store.effective_used("吸收"))
                    self.assertEqual(1, store.effective_used("召集"))
                if version == 7:
                    self.assertFalse(store.collection_budget_exhausted())
                    self.assertEqual(60, state.daily_suppressions)
                    self.assertEqual(75, store.action_limit("压制"))
                    suppression = store.get_action_record("Q_sp1", "battle_area_1", "压制")
                    self.assertEqual("preexisting_used", suppression["state"])
                migrated = path.read_bytes()
                ledger.reopen()
                self.assertEqual(migrated, path.read_bytes())

        for original, message, backups in (
            (b"{broken", None, 1),
            (b'{"schema_version":10}', "兼容迁移", 0),
            (json.dumps({"schema_version": STATE_SCHEMA_VERSION + 1}).encode(), "高于", 0),
        ):
            with self.subTest(original=original):
                ledger = self.ledger()
                ledger.path.write_bytes(original)
                with self.assertRaisesRegex(RuntimeError, message or "."):
                    ledger.reopen()
                self.assertEqual(original, ledger.path.read_bytes())
                files = list(ledger.path.parent.glob("progress.corrupt-*.json"))
                self.assertEqual(backups, len(files))
                if backups:
                    self.assertEqual(original, files[0].read_bytes())


class CalendarTest(unittest.TestCase):
    def test_item_reserves_preserve_price_flags_and_blacklist(self):
        trader = create_trader()
        trader.task = SimpleNamespace(
            config={
                "商品保留量": "杏仁=5000\n豆子=-1\n白糖=0",
                "使用出售白名单": False,
                "使用出售黑名单": True,
                "出售黑名单": "小麦",
            },
            info_set=lambda *_args: None,
            log_info=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        trader.vision = SimpleNamespace(simplify=lambda value: value)
        trader.now_provider = lambda: datetime(2026, 11, 16, tzinfo=UTC_PLUS_8)
        entries = (
            CalendarEntry("杏仁", "S1", reserve=2000),
            CalendarEntry("豆子", "S1"),
            CalendarEntry("小麦", "S1"),
            CalendarEntry("白糖", "S1", sell=False),
            CalendarEntry("蜂蜜", "S1", reserve=1000),
        )
        trader.calendar_client = SimpleNamespace(
            load=lambda **_kwargs: SimpleNamespace(
                source="manual",
                entries_for=lambda _day: entries,
            )
        )
        self.assertEqual(
            [("杏仁", 5000), ("蜂蜜", 1000)],
            [(entry.item, entry.reserve) for entry in trader._resolve_sale_entries()],
        )
        self.assertEqual(3, len(trader._sale_not_sold))
        trader.task.config["商品保留量"] = "杏仁=-2"
        self.assertIsNone(trader._resolve_sale_entries())

        entries = (CalendarEntry("禿鷲羽毛", "S9", aliases=("Vulture Feather",)),)
        trader.task.config.update({"使用出售白名单": True, "出售白名单": "秃鹫羽毛"})
        for reserve, expected in (
            ("秃鹫羽毛=-1", []),
            ("秃鹫羽毛=25", [25]),
            ("Vulture Feather=30", [30]),
            ("禿鷲羽毛=25\n秃鹫羽毛=25\nVulture Feather=25", [25]),
            ("禿鷲羽毛=25\n秃鹫羽毛=-1", None),
            ("Vulture Feather=25\n秃鹫羽毛=-1", None),
        ):
            with self.subTest(reserve=reserve):
                trader.task.config["商品保留量"] = reserve
                result = trader._resolve_sale_entries()
                actual = None if result is None else [entry.reserve for entry in result]
                self.assertEqual(expected, actual)

        entries = (CalendarEntry("蜂蜜黃油杏仁", "S9"),)
        trader.task.config["商品保留量"] = ""
        for blocked in ("蜂蜜奶油杏仁", "Honey Butter Almond", "蜂蜜黄油杏仁", "豆子"):
            with self.subTest(blocked=blocked):
                trader.task.config["出售黑名单"] = blocked
                actual = trader._resolve_sale_entries()
                self.assertEqual(list(entries) if blocked == "豆子" else [], actual)
        entries = (CalendarEntry("本地杏仁别称", "S9", aliases=("蜂蜜奶油杏仁",)),)
        trader.task.config["出售黑名单"] = "Honey Butter Almond"
        self.assertEqual([], trader._resolve_sale_entries())
        trader.task.config["出售黑名单"] = ""
        self.assertEqual(list(entries), trader._resolve_sale_entries())

    def test_business_dates_use_the_current_beijing_refresh_for_each_operation(self):
        self.assertEqual((23, 8), (SALE_PRICE_REFRESH_HOUR, PURCHASE_STOCK_REFRESH_HOUR))
        # Prices advance at 23:00. Stock and collection quotas advance at 08:00,
        # including the weekly collection reset on Monday. 04:00 is still the prior cycle.
        for local, sale, stock, week in (
            ("2026-07-12T08:00", "2026-07-12", "2026-07-12", "2026-07-06"),
            ("2026-07-13T04:00", "2026-07-13", "2026-07-12", "2026-07-06"),
            ("2026-07-13T07:59:59", "2026-07-13", "2026-07-12", "2026-07-06"),
            ("2026-07-13T08:00", "2026-07-13", "2026-07-13", "2026-07-13"),
            ("2026-07-19T22:59:59", "2026-07-19", "2026-07-19", "2026-07-13"),
            ("2026-07-19T23:00", "2026-07-20", "2026-07-19", "2026-07-13"),
            ("2026-07-31T23:30", "2026-08-01", "2026-07-31", "2026-07-27"),
            ("2026-08-31T22:59:59", "2026-08-31", "2026-08-31", "2026-08-31"),
            ("2026-08-31T23:00", "2026-09-01", "2026-08-31", "2026-08-31"),
        ):
            moment = datetime.fromisoformat(local).replace(tzinfo=UTC_PLUS_8)
            for zone in (UTC_PLUS_8, timezone(timedelta(hours=9)), timezone.utc):
                with self.subTest(local=local, zone=zone):
                    stamp = moment.astimezone(zone)
                    self.assertEqual(date.fromisoformat(sale), sale_price_calendar_date(stamp))
                    self.assertEqual(date.fromisoformat(stock), purchase_stock_date(stamp))
                    self.assertEqual(
                        (stock, week), (daily_cycle_key(stamp), weekly_cycle_key(stamp))
                    )
        trader = create_trader()
        trader.started_at = datetime(2026, 7, 19, 22, 50, tzinfo=UTC_PLUS_8)
        trader.now_provider = lambda: trader.started_at.replace(hour=23, minute=30)
        lookup = Mock(return_value=())
        trader.calendar_client = SimpleNamespace(
            load=lambda **_: SimpleNamespace(source="bundled", entries_for=lookup)
        )
        trader.task = SimpleNamespace(config={}, info_set=Mock(), log_info=Mock())
        self.assertTrue(trader.sell_max_price_items())
        lookup.assert_called_once_with(20)
        self.assertIn(call("出售价表日期", "2026-07-20"), trader.task.info_set.call_args_list)
        self.assertIn(
            call(
                "卖：当前北京时间2026-07-19 23:30:00，按2026-07-20最高价表执行（每日23:00刷新）。"
            ),
            trader.task.log_info.call_args_list,
        )

    def test_bundled_revisions_select_the_expected_sale_routes(self):
        client = create_trader().calendar_client
        for moment, expected in (
            (datetime(2026, 11, 18, 22, 59, 59, tzinfo=UTC_PLUS_8), "土豆"),
            (datetime(2026, 11, 18, 23, 0, 0, tzinfo=UTC_PLUS_8), "水果冰沙"),
            (datetime(2027, 1, 1, tzinfo=UTC_PLUS_8), "水果冰沙"),
        ):
            with self.subTest(moment=moment):
                client.now_provider = lambda: moment
                loaded = client.load()
                self.assertIn(expected, {entry.item for entry in loaded.entries_for(17)})
                self.assertEqual(set(range(1, 32)), set(loaded.days))
        earlier = client.load(market_date=date(2026, 11, 18))
        self.assertIn("土豆", {entry.item for entry in earlier.entries_for(17)})
        manual = "\n".join(f"{day}=" for day in range(1, 32))
        manual_calendar = client.load(use_bundled=False, use_online=False, manual_text=manual)
        self.assertEqual("manual", manual_calendar.source)
        calendar = client.load(market_date=date(2026, 11, 19))
        trader = create_trader()
        trader.task = SimpleNamespace(config={})
        trader.vision = SimpleNamespace(simplify=lambda value: value)
        catalog = trader._sale_title_catalog(calendar.entries_for(17)[0])
        whitelist = trader._sale_whitelist(catalog)
        additions = (
            ("水果冰沙", 17, "S5"),
            ("凯洛养生汤", 17, "E1"),
            ("土豆汤", 11, "S8"),
            ("黑啤酒炖锅", 3, "R6"),
            ("藏红花甜茶", 2, "S19"),
        )
        new_entries = set()
        for name, day, shop in additions:
            with self.subTest(name=name):
                matches = [
                    (sale_day, entry)
                    for sale_day, entries in calendar.days.items()
                    for entry in entries
                    if name in (entry.item, *entry.aliases)
                ]
                self.assertEqual([(day, shop)], [(d, e.shop.split(":")[0]) for d, e in matches])
                entry = matches[0][1]
                self.assertTrue(entry.sell)
                self.assertLessEqual(catalog[entry.item], whitelist)
                new_entries.add((day, entry.item))
        removed = {
            (22, "凯洛药材"),
            (18, "藏红花"),
            (24, "啤酒花"),
            (2, "水果罐头"),
            (24, "吐司"),
            (11, "牛奶"),
        }
        original = earlier
        for day, entries in original.days.items():
            with self.subTest(day=day):
                self.assertEqual(
                    [e for e in entries if (day, e.item) not in removed],
                    [e for e in calendar.entries_for(day) if (day, e.item) not in new_entries],
                )
        for day in (29, 30, 31):
            self.assertEqual((), original.entries_for(day))
        client.now_provider = lambda: datetime(2026, 11, 23, 22, 59, 59, tzinfo=UTC_PLUS_8)
        self.assertEqual(calendar, client.load())
        client.now_provider = lambda: datetime(2026, 11, 23, 23, 0, tzinfo=UTC_PLUS_8)
        latest = client.load()
        self.assertEqual(PriceCalendarClient(BUNDLED_CALENDAR).load(), latest)
        self.assertEqual(set(range(1, 32)), set(latest.days))
        def routes_for(table):
            return {
                (day, e.shop.split(":")[0], e.item)
                for day, entries in table.days.items() for e in entries
            }

        routes, previous = routes_for(latest), routes_for(calendar)
        self.assertEqual(63, sum(map(len, latest.days.values())))
        self.assertEqual({(8, "S13", "海苔"), (11, "S7", "牛奶"), (22, "S5", "三文鱼芥末寿司"),
                          (25, "E1", "巧克力面包")}, routes - previous)
        self.assertEqual({(14, "S8", "洋葱"), (17, "S16", "土豆"), (18, "R3", "萝卜缨")},
                         previous - routes)
        targets = set(trader._selected_cooking_recipes()) | {"海苔", "牛奶"}
        for entries in latest.days.values():
            for entry in entries:
                if entry.item in targets:
                    self.assertTrue(entry.sell)
                    self.assertIn(entry.item, whitelist)
                    self.assertEqual({entry.item}, trader._sale_title_catalog(entry)[entry.item])
                    targets.remove(entry.item)
        self.assertFalse(targets)

    def test_conditional_fetch_and_offline_cache_expiry(self):
        url = "https://example.test/calendar.json"
        now = datetime(2026, 7, 19, 12, tzinfo=UTC_PLUS_8)
        payload = BUNDLED_CALENDAR.read_bytes()
        for age, online, expected in (
            (1, True, "cache"),
            (25, True, url),
            (1, False, "cache"),
            (25, False, None),
            (None, False, None),
        ):
            with self.subTest(age=age, online=online), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                sources, cache = directory / "sources.json", directory / "cache.json"
                sources.write_text(json.dumps({"global": [url]}), "utf-8")
                envelope = {"source": url, "etag": "old", "payload": json.loads(payload)}
                if age is not None:
                    envelope["cached_at"] = (now - timedelta(hours=age)).isoformat()
                cache.write_text(json.dumps(envelope), "utf-8")
                client = PriceCalendarClient(
                    BUNDLED_CALENDAR, cache, sources, now_provider=lambda: now
                )
                calls = []

                def fetch(source, etag=""):
                    calls.append((source, etag))
                    if not online:
                        raise OSError("offline")
                    if etag:
                        raise urllib.error.HTTPError(source, 304, "Not Modified", None, None)
                    return parse_calendar_payload(payload, source=source), payload, "new"

                with patch.object(client, "_fetch", side_effect=fetch):
                    if expected is None:
                        with self.assertRaisesRegex(RuntimeError, "本地缓存已过期"):
                            client.load(use_bundled=False)
                    else:
                        self.assertEqual(expected, client.load(use_bundled=False).source)
                self.assertEqual(
                    [(url, "old"), (url, "")] if online and age == 25 else [(url, "old")], calls
                )
                if expected == url:
                    self.assertEqual("new", json.loads(cache.read_bytes())["etag"])


class MapTradeConfigTest(unittest.TestCase):
    def test_legacy_trade_config_migration_preserves_recipe_selection_and_switches(self):
        yellow, cyan, blue, red = COOKING_GROUPS
        none_selected = dict.fromkeys(COOKING_GROUPS, [])
        for migrate, original, expected in (
            (_cooking_migration_values, {"5星料理": [], "额外料理": []}, {}),
            (
                _cooking_migration_values,
                {"料理清单": ["泰瑞丝派"]},
                {**none_selected, blue: ["泰瑞丝派"], red: ["三文鱼芥末寿司"]},
            ),
            (
                _cooking_migration_values,
                {"料理清单": [], "红框料理": [], "额外料理": "土豆汤"},
                {**none_selected, red: ["土豆汤", "三文鱼芥末寿司"]},
            ),
            (_cooking_migration_values, {"黄框料理": [], "红框料理": [], "蓝框料理": []},
             none_selected),
            (_cooking_migration_values, {"黄框料理": list(DEFAULT_RECIPES)}, {}),
            (_cooking_migration_values,
             {"黄框料理": [], "红框料理": ["三明治便当", "橄榄油意面"], "蓝框料理": [], yellow: []},
             {cyan: ["三明治便当"], blue: [], red: ["三文鱼芥末寿司"]}),
            (
                _trade_section_migration_values,
                {"执行跑商": True, "低价进货": False, "最高价出售": True, "制作利润料理": False},
                {"买": False, "卖": True},
            ),
            (
                _trade_section_migration_values,
                {"执行跑商": False, "低价进货": True, "最高价出售": True, "制作利润料理": True},
                {"买": False, "卖": False},
            ),
        ):
            with self.subTest(migrate=migrate.__name__, original=original):
                self.assertEqual(expected, migrate(original))


class MapTradeSafetyTest(unittest.TestCase):
    def _cancellable_task(self, control_error=TaskDisabledException):
        task = object.__new__(MapTradeTask)
        task.config = {}
        for name in ("info_set", "log_info", "log_warning", "log_error", "log_completion"):
            setattr(task, name, lambda *_args, **_kwargs: None)
        executor = object.__new__(TaskExecutor)
        executor.paused = False
        executor.debug_mode = False
        executor.thread = threading.current_thread()
        executor.current_job = None
        executor._exclusive_owner = None
        executor.current_task = SimpleNamespace(_enabled=True)
        clicks = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        def check_enabled():
            if control_error is FinishedException and not executor.current_task._enabled:
                executor.current_task = None
                raise FinishedException("executor finished")
            return TaskExecutor.check_enabled(executor)

        def click(*_args, **_kwargs):
            check_enabled()
            clicks.append(executor.current_task is None)

        def cancel(*_args, **_kwargs):
            executor.current_task._enabled = False
            check_enabled()

        task.operate_click = click
        task.capture_frame = lambda: check_enabled() or frame
        task.sleep = lambda *_args: check_enabled()
        task.save_frame = lambda *_args: None
        return task, executor, frame, clicks, cancel

    def test_map_trade_sources_do_not_call_keyboard_interfaces(self):
        sources = [
            ROOT / "src" / "tasks" / "MapTradeTask.py",
            ROOT / "src" / "tasks" / "MapCollectionTask.py",
        ]
        sources.extend((ROOT / "src" / "tasks" / "map_trade").glob("*.py"))
        forbidden_calls = {
            "send_key",
            "send_key_down",
            "send_key_up",
            "key_down",
            "key_up",
            "press_key",
        }
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            with self.subTest(source=source.name):
                self.assertTrue(forbidden_calls.isdisjoint(called))

    def _interrupted_phase(self, task, executor, navigator, method, boundary, cancel):
        if method == "cooking":
            trader = object.__new__(Trader)
            trader.task = task
            trader._selected_cooking_recipes = lambda: ("巧克力鸡尾酒",)

            def fail():
                if boundary == "ordinary_error":
                    raise RuntimeError("料理识别失败")
                executor.current_task._enabled = False
                getattr(task, "capture_frame" if boundary == "entry" else boundary)()

            def enter():
                trader._cooking_opened = True
                if boundary == "entry":
                    fail()
                return True

            trader._enter_cooking_list = enter
            trader._cook_one_recipe = lambda _: fail()
            trader._leave_cooking_to_q_sp6 = lambda: task.operate_click(0.5, 0.5) or True
            return trader.run_cooking()
        if method == "phase":
            if boundary == "return_home":
                return True
            executor.current_task._enabled = False
            if boundary == "diagnostic":
                return False
            return getattr(task, boundary)()

        def ocr(*_, name, **kwargs):
            if name == boundary:
                cancel()
            return [SimpleNamespace(name=COLLECTABLE_CARDS[0].targets[0].titles[0])]

        task.ocr = ocr
        frame = task.capture_frame()
        if method == "ocr_boxes":
            return navigator.vision.ocr_boxes(frame, boundary)
        if method == "_click_teleport_generation":
            return navigator._click_teleport_generation(
                MatchResult(1.0, (20, 20), (10, 10)), frame.shape
            )
        return getattr(navigator, method)(frame)

    def test_phase_interruptions_preserve_the_input_boundary_and_cleanup_policy(self):
        cases = (
            *(
                ("phase", name)
                for name in ("capture_frame", "operate_click", "sleep", "diagnostic", "return_home")
            ),
            *(("cooking", name) for name in ("entry", "operate_click", "sleep")),
            ("ocr_boxes", "直接OCR"),
            ("_detect_map_page_mode", "地图页面左上标题"),
            ("_detect_map_page_mode", "箱庭大地图底部控件"),
            ("_sandbox_teleport_skill_failure_text", "箱庭5号传送阵技能失败"),
            ("_click_sandbox_navigation_menu_teleport", "箱庭徒步导航传送阵菜单"),
            ("_click_sandbox_navigation_destination_confirmation", "箱庭徒步导航传送阵确认"),
            ("_click_teleport_generation", "传送阵生成确认"),
        )
        scenarios = [
            (error, method, boundary)
            for error in (TaskDisabledException, FinishedException)
            for method, boundary in cases
        ]
        scenarios.append((RuntimeError, "cooking", "ordinary_error"))
        for error, method, boundary in scenarios:
            with self.subTest(error=error, method=method, boundary=boundary):
                task, executor, frame, clicks, cancel = self._cancellable_task(error)
                nav = Navigator(task, Vision(task))
                task.save_frame = Mock()

                def return_home():
                    if boundary == "return_home":
                        executor.current_task._enabled = False
                    task.operate_click(0.5, 0.5)
                    return NavigationResult(True, ScreenState.HOME)

                nav.return_home = Mock(side_effect=return_home)

                def action():
                    return self._interrupted_phase(task, executor, nav, method, boundary, cancel)

                if error is RuntimeError:
                    with self.assertRaisesRegex(RuntimeError, "料理识别失败"):
                        action()
                    self.assertEqual([False], clicks)
                else:
                    with self.assertRaises(error):
                        task._run_phases(nav, (("导航", "启用", action),))
                    self.assertIsNone(executor.current_task)
                    self.assertEqual(
                        [False] if method == "_click_teleport_generation" else [], clicks
                    )
                    self.assertEqual(int(boundary == "return_home"), nav.return_home.call_count)
                    task.save_frame.assert_not_called()


class BargainLevelTaskTest(unittest.TestCase):
    def test_bargain_run_clicks_only_confirmed_steps_and_stops_at_upgrade(self):
        prompts = (
            ([BARGAIN_PROMPT], 1),
            ([BUY_ALL_COLLECTION_PROMPT], 1),
            (list(CLOSE_SHOP_PROMPTS), 2),
        )
        points = [
            BARGAIN_ENTRY_POINT,
            BARGAIN_CONFIRM_POINT,
            COLLECTION_BACK_POINT,
            CLOSE_SHOP_CONFIRM_POINT,
        ]
        for gates, cycles, success in (
            (0, 0, False),
            (1, 0, False),
            (2, 0, False),
            (3, 1, True),
            (3, 2, True),
        ):
            with self.subTest(gates=gates, cycles=cycles):
                task = object.__new__(BargainLevelTask)
                task.config = {"启用": True, "步骤 OCR 等待秒数": 20.0}
                info = {}
                task.info_set = info.__setitem__
                task.log_info = Mock()
                task.sleep = Mock()
                task.operate_click = Mock()
                steps = []

                def wait(words, timeout, minimum_matches, name):
                    if name == "bargain_upgrade":
                        self.assertEqual(1.0, timeout)
                        return len(steps) == 3 * cycles, "升星"
                    steps.append((words, minimum_matches))
                    return (len(steps) - 1) % 3 < gates, " ".join(words)

                task._wait_for_ocr_keywords = wait
                self.assertEqual(success, task.run())
                self.assertEqual(cycles, info["完成循环数"])
                self.assertEqual("已可以升星" if success else "失败", info["结果"])
                self.assertEqual(
                    list(prompts) * cycles if success else list(prompts[: gates + 1]), steps
                )
                self.assertEqual(
                    points * cycles if success else points[: gates + 1],
                    [c.args for c in task.operate_click.call_args_list],
                )
                self.assertEqual(
                    [call(POST_RECOGNITION_DELAY_SECONDS)] * (cycles * 3 if success else gates),
                    task.sleep.call_args_list,
                )
                if success:
                    self.assertTrue(task.log_info.call_args.kwargs["notify"])
