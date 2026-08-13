"""Map-trade progress tests (split from test_map_trade.py)."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    DAILY_ABSORB_LIMIT,
    DAILY_SUMMON_LIMIT,
    DAILY_SUPPRESS_LIMIT,
    DEFAULT_RECIPES,
    CollectionActionState,
    CollectionMapRole,
)
from src.tasks.map_trade.progress import (
    STATE_SCHEMA_VERSION,
    UTC_PLUS_8,
    VALID_FAVORITE_SHOP_IDS,
    ProgressStore,
    daily_cycle_key,
    weekly_cycle_key,
)
from tests.helpers.map_trade import _seed_action_records


class ProgressTest(unittest.TestCase):
    def test_daily_cycle_changes_at_four_am(self):
        before = datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8)
        after = datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)

        self.assertEqual("2026-07-12", daily_cycle_key(before))
        self.assertEqual("2026-07-13", daily_cycle_key(after))

    def test_weekly_cycle_changes_monday_at_four_am(self):
        sunday = datetime(2026, 7, 12, 4, 0, tzinfo=UTC_PLUS_8)
        monday_before = datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8)
        monday_after = datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)

        self.assertEqual("2026-07-06", weekly_cycle_key(sunday))
        self.assertEqual("2026-07-06", weekly_cycle_key(monday_before))
        self.assertEqual("2026-07-13", weekly_cycle_key(monday_after))

    def test_favorite_cartridge_progress_saves_each_card_and_requires_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"

            def now():
                return datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8)

            store = ProgressStore(path, now)
            store.load()

            self.assertTrue(store.should_rebuild_favorites())
            self.assertTrue(store.mark_favorite_card("S1"))
            self.assertFalse(store.mark_favorite_card("S1"))
            self.assertTrue(store.favorite_card_complete("S1"))
            with self.assertRaisesRegex(RuntimeError, "rebuild is incomplete"):
                store.mark_favorites_built()

            resumed = ProgressStore(path, now)
            resumed.load()
            self.assertTrue(resumed.favorite_card_complete("S1"))
            for shop_id in sorted(VALID_FAVORITE_SHOP_IDS - {"S1"}):
                self.assertTrue(resumed.mark_favorite_card(shop_id))
            resumed.mark_favorites_built()
            self.assertFalse(resumed.should_rebuild_favorites())

            resumed.clear_favorite_cards()
            self.assertTrue(resumed.should_rebuild_favorites())
            self.assertEqual(set(), resumed.state.completed_favorite_cards)

    def test_progress_saves_each_submap_and_stops_at_twenty_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            store = ProgressStore(path, lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8))
            store.load()
            # The 22nd absorb would exceed the daily limit; keep its durable
            # records ready so the limit check, not the readiness check, fires.
            _seed_action_records(
                store,
                COLLECTABLE_CARDS[7].card_id,
                COLLECTABLE_CARDS[7].targets[0].key,
            )
            for card in COLLECTABLE_CARDS[:7]:
                for target in card.targets:
                    _seed_action_records(store, card.card_id, target.key)
                    self.assertTrue(store.mark_target(card.card_id, target.key))
                    self.assertTrue(path.exists())
                    self.assertFalse(path.with_suffix(".json.tmp").exists())
                self.assertTrue(store.mark_card_verified(card.card_id))

            self.assertEqual(DAILY_ABSORB_LIMIT, store.state.daily_absorbs)
            self.assertEqual(14, store.state.daily_summons)
            self.assertEqual(14, store.state.daily_suppressions)
            self.assertTrue(store.state.depleted_today)
            self.assertEqual(21, store.state.weekly_submap_count)
            self.assertTrue(
                all(store.state.card_verified(card.card_id) for card in COLLECTABLE_CARDS[:7])
            )
            next_card = COLLECTABLE_CARDS[7]
            self.assertFalse(
                store.mark_target(
                    next_card.card_id,
                    next_card.targets[0].key,
                )
            )
            self.assertTrue(store.state.depleted_today)

    def test_collection_skill_limits_match_three_two_two_per_card(self):
        self.assertEqual(21, DAILY_ABSORB_LIMIT)
        self.assertEqual(21, DAILY_SUMMON_LIMIT)
        self.assertEqual(60, DAILY_SUPPRESS_LIMIT)

    def test_progress_rejects_pinned_collection_cards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()

            with self.assertRaisesRegex(ValueError, "invalid collection card"):
                store.mark_target("Q_sp6", CollectionMapRole.MAIN_AREA.value)

    def test_card_visual_verification_requires_all_three_map_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            _seed_action_records(
                store,
                "Q_sp1",
                CollectionMapRole.MAIN_AREA.value,
            )
            store.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)

            with self.assertRaisesRegex(RuntimeError, "targets are incomplete"):
                store.mark_card_verified("Q_sp1")

            self.assertFalse(store.state.card_verified("Q_sp1"))

    def test_daily_reset_preserves_weekly_submaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 7, 12, 3, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            for target in CARD_BY_ID["Q_sp1"].targets:
                _seed_action_records(store, "Q_sp1", target.key)
                store.mark_target("Q_sp1", target.key)
            store.mark_card_verified("Q_sp1")
            now[0] = datetime(2026, 7, 12, 4, 0, tzinfo=UTC_PLUS_8)

            state = ProgressStore(path, lambda: now[0]).load()

            self.assertEqual(
                {target.key for target in CARD_BY_ID["Q_sp1"].targets},
                state.completed_targets("Q_sp1"),
            )
            self.assertEqual(0, state.daily_submaps)
            self.assertEqual(0, state.daily_summons)
            self.assertEqual(0, state.daily_suppressions)
            self.assertFalse(state.depleted_today)
            self.assertTrue(state.card_verified("Q_sp1"))

    def test_weekly_reset_clears_submaps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            store = ProgressStore(path, lambda: datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8))
            store.load()
            _seed_action_records(
                store,
                "Q_sp1",
                CollectionMapRole.MAIN_AREA.value,
            )
            store.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)

            state = ProgressStore(
                path, lambda: datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)
            ).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.weekly_submap_count)
            self.assertEqual([], state.verified_cards)

    def test_all_seventeen_cards_make_fifty_one_weekly_targets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: now[0],
            )
            store.load()
            for card_index, card in enumerate(COLLECTABLE_CARDS):
                if card_index in {7, 14}:
                    now[0] = now[0].replace(day=now[0].day + 1)
                    store.load()
                for target in card.targets:
                    _seed_action_records(store, card.card_id, target.key)
                    store.mark_target(card.card_id, target.key)

            self.assertEqual(51, store.state.weekly_submap_count)

    def test_schema_one_collection_progress_resets_without_losing_other_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "weekly_key": weekly_cycle_key(now),
                        "daily_key": daily_cycle_key(now),
                        "cards": {"Q_sp1": [0, 1]},
                        "daily_submaps": 5,
                        "depleted_today": False,
                        "favorite_week": weekly_cycle_key(now),
                        "favorite_cards": ["S1"],
                        "cooking_week": weekly_cycle_key(now),
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.daily_submaps)
            self.assertEqual(0, state.daily_summons)
            self.assertEqual(0, state.daily_suppressions)
            self.assertEqual({"S1"}, state.completed_favorite_cards)
            self.assertEqual(weekly_cycle_key(now), state.cooking_week)
            self.assertEqual(set(DEFAULT_RECIPES), state.completed_cooking_recipes)
            self.assertEqual(STATE_SCHEMA_VERSION, saved["schema_version"])

    def test_schema_two_collection_progress_resets_for_role_specific_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "weekly_key": weekly_cycle_key(now),
                        "daily_key": daily_cycle_key(now),
                        "cards": {
                            "Q_sp1": [
                                "main_area",
                                "battle_area_1",
                                "battle_area_2",
                            ]
                        },
                        "daily_submaps": 3,
                        "depleted_today": False,
                        "favorite_week": weekly_cycle_key(now),
                        "favorite_cards": ["S1"],
                        "cooking_week": weekly_cycle_key(now),
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.daily_absorbs)
            self.assertEqual([], state.verified_cards)
            self.assertEqual({"S1"}, state.completed_favorite_cards)

    def test_corrupt_file_recovers_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            path.write_text("{broken", encoding="utf-8")

            state = ProgressStore(path, lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8)).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(1, len(list(path.parent.glob("progress.corrupt-*.json"))))

    def test_schema_three_migrates_without_losing_collection_or_trade_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8)
            week = weekly_cycle_key(now)
            day = daily_cycle_key(now)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "weekly_key": week,
                        "daily_key": day,
                        "cards": {"Q_sp1": ["main_area"]},
                        "daily_submaps": 4,
                        "daily_summons": 2,
                        "daily_suppressions": 2,
                        "depleted_today": False,
                        "verified_cards": [],
                        "favorite_week": week,
                        "favorite_cards": ["S1"],
                        "cooking_week": week,
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(STATE_SCHEMA_VERSION, saved["schema_version"])
            self.assertEqual({"main_area"}, state.completed_targets("Q_sp1"))
            self.assertEqual(
                (4, 2, 2),
                (state.daily_submaps, state.daily_summons, state.daily_suppressions),
            )
            self.assertEqual({"S1"}, state.completed_favorite_cards)
            self.assertEqual(week, state.cooking_week)
            self.assertEqual(set(DEFAULT_RECIPES), state.completed_cooking_recipes)

    def test_cooking_progress_is_per_recipe_and_partial_runs_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8)
            first, second = DEFAULT_RECIPES[:2]
            store = ProgressStore(path, lambda: now)
            store.load()

            self.assertTrue(store.should_cook(recipes=(first, second)))
            self.assertTrue(store.mark_cooking_recipe_complete(first))
            self.assertFalse(store.mark_cooking_recipe_complete(first))
            self.assertTrue(store.cooking_recipe_complete(first))
            self.assertFalse(store.cooking_recipe_complete(second))
            self.assertTrue(store.should_cook(recipes=(first, second)))
            self.assertFalse(store.should_cook(recipes=(first,)))
            self.assertTrue(store.should_cook(every_run=True, recipes=(first,)))

            resumed = ProgressStore(path, lambda: now)
            state = resumed.load()
            self.assertEqual({first}, state.completed_cooking_recipes)
            self.assertTrue(resumed.should_cook(recipes=(first, second)))
            self.assertTrue(resumed.mark_cooking_recipe_complete(second))
            self.assertFalse(resumed.should_cook(recipes=(first, second)))

    def test_schema_four_whole_week_cooking_marker_migrates_to_all_recipes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8)
            week = weekly_cycle_key(now)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "weekly_key": week,
                        "daily_key": daily_cycle_key(now),
                        "cooking_week": week,
                    }
                ),
                encoding="utf-8",
            )

            store = ProgressStore(path, lambda: now)
            state = store.load()
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(set(DEFAULT_RECIPES), state.completed_cooking_recipes)
            self.assertFalse(store.should_cook(recipes=DEFAULT_RECIPES))
            self.assertEqual(list(DEFAULT_RECIPES), saved["cooking_recipes"])
            self.assertEqual(STATE_SCHEMA_VERSION, saved["schema_version"])

    def test_weekly_rollover_clears_per_recipe_cooking_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 8, 16, 12, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            store.mark_cooking_recipe_complete(DEFAULT_RECIPES[0])

            now[0] = datetime(2026, 8, 17, 4, 0, tzinfo=UTC_PLUS_8)
            state = ProgressStore(path, lambda: now[0]).load()

            self.assertEqual(set(), state.completed_cooking_recipes)

    def test_action_ledger_is_idempotent_and_archives_at_daily_rollover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 8, 11, 3, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()

            self.assertTrue(store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"))
            self.assertFalse(store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"))
            store.mark_action_clicked("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            store.mark_action_local_done("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True)
            self.assertEqual(1, store.pending_count())

            now[0] = datetime(2026, 8, 11, 4, 0, tzinfo=UTC_PLUS_8)
            resumed = ProgressStore(path, lambda: now[0])
            state = resumed.load()

            self.assertEqual({}, state.action_records)
            self.assertEqual(1, len(state.archived_action_records))
            self.assertEqual(
                CollectionActionState.ARCHIVED.value,
                next(iter(state.archived_action_records.values()))["state"],
            )
            self.assertEqual(0, resumed.pending_count())

    def test_pending_reconciliation_rejects_stale_or_wrong_denominator_and_settles_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            store.mark_action_local_done("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True)

            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 20)))
            self.assertEqual(1, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, store.pending_count())

    def test_preexisting_baseline_rejects_equal_invalid_and_lower_observations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            self.assertTrue(
                store.mark_action_preexisting_used("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            )
            record = store.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            self.assertEqual([0, 21], record["baseline"])
            self.assertEqual({}, store.state.observed_counts)
            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 20)))
            self.assertEqual({}, store.state.observed_counts)
            self.assertEqual(1, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual((1, 21), store.state.observed_counts["吸收"])
            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(CollectionActionState.SETTLED.value, record["state"])

    def test_target_commit_covers_reservation_without_double_counting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            store.mark_action_local_done("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True)
            self.assertEqual(1, store.effective_daily_counts()["吸收"])
            self.assertTrue(store.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value))
            self.assertEqual(1, store.state.daily_submaps)
            self.assertEqual(1, store.effective_daily_counts()["吸收"])
            self.assertFalse(store.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value))

    def test_mark_target_requires_role_actions_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()

            with self.assertRaisesRegex(RuntimeError, "requires durable local action"):
                store.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)
            self.assertEqual(set(), store.state.completed_targets("Q_sp1"))

    def test_reconcile_pending_uses_positive_delta_deterministically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            for role in (
                CollectionMapRole.MAIN_AREA,
                CollectionMapRole.BATTLE_AREA_1,
            ):
                store.arm_action("Q_sp1", role, "吸收", baseline=(0, 21))
                store.mark_action_local_done("Q_sp1", role, "吸收", pending=True)

            self.assertEqual(1, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(1, store.pending_count("吸收"))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(1, store.reconcile_pending("吸收", (2, 21)))
            self.assertEqual(0, store.pending_count("吸收"))
            self.assertEqual((2, 21), store.state.observed_counts["吸收"])

    def test_schema_four_sanitizes_action_keys_and_quarantines_stale_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8)
            day = daily_cycle_key(now)
            week = weekly_cycle_key(now)

            def record(daily, card, role, action, *, key=None):
                canonical = "|".join((daily, card, role, action))
                return key or canonical, {
                    "daily_key": daily,
                    "card_id": card,
                    "map_role": role,
                    "action": action,
                    "state": "pending",
                    "local_done": True,
                    "reservation": True,
                }

            valid_key, valid = record(day, "Q_sp1", CollectionMapRole.MAIN_AREA.value, "吸收")
            stale_key, stale = record(
                "2026-08-09", "Q_sp1", CollectionMapRole.MAIN_AREA.value, "吸收"
            )
            malformed_key, malformed = record(
                day, "Q_sp1", CollectionMapRole.MAIN_AREA.value, "吸收", key="not-canonical"
            )
            invalid_action_key, invalid_action = record(
                day, "Q_sp1", CollectionMapRole.MAIN_AREA.value, "探查"
            )
            invalid_role_key, invalid_role = record(day, "Q_sp1", "main", "吸收")
            path.write_text(
                json.dumps(
                    {
                        "schema_version": STATE_SCHEMA_VERSION,
                        "weekly_key": week,
                        "daily_key": day,
                        "action_records": {
                            valid_key: valid,
                            stale_key: stale,
                            malformed_key: malformed,
                            invalid_action_key: invalid_action,
                            invalid_role_key: invalid_role,
                            "broken-value": "not-a-record",
                        },
                        "observed_counts": {
                            "吸收": [1, 21],
                            "探查": [99, 99],
                        },
                    }
                ),
                encoding="utf-8",
            )

            state = ProgressStore(path, lambda: now).load()

            self.assertEqual({valid_key}, set(state.action_records))
            self.assertEqual((1, 21), state.observed_counts["吸收"])
            self.assertNotIn("探查", state.observed_counts)
            self.assertGreaterEqual(len(state.archived_action_records), 5)
            self.assertTrue(
                all(
                    record.get("state") == CollectionActionState.ARCHIVED.value
                    and record.get("reservation") is False
                    for record in state.archived_action_records.values()
                )
            )
            resumed_store = ProgressStore(path, lambda: now)
            resumed_store.load()
            self.assertEqual(2, resumed_store.effective_used("吸收"))

    def test_weekly_rollover_archives_unresolved_actions_and_resets_absolute_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 7, 13, 3, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            store.mark_action_local_done("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True)
            now[0] = datetime(2026, 7, 13, 4, 0, tzinfo=UTC_PLUS_8)

            state = ProgressStore(path, lambda: now[0]).load()

            self.assertEqual("2026-07-13", state.weekly_key)
            self.assertEqual({}, state.action_records)
            self.assertEqual({}, state.observed_counts)
            self.assertEqual(
                (0, 0, 0),
                (state.daily_submaps, state.daily_summons, state.daily_suppressions),
            )
            archived = next(iter(state.archived_action_records.values()))
            self.assertEqual(CollectionActionState.ARCHIVED.value, archived["state"])
            self.assertFalse(archived["reservation"])


if __name__ == "__main__":
    unittest.main()
