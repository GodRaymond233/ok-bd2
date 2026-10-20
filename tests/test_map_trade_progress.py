"""Map-trade progress tests (split from test_map_trade.py)."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    DEFAULT_RECIPES,
    CollectionActionState,
    CollectionMapRole,
)
from src.tasks.map_trade.progress import (
    STATE_SCHEMA_VERSION,
    UTC_PLUS_8,
    VALID_FAVORITE_SHOP_IDS,
    ProgressStore,
    configure_development_ledger,
    daily_cycle_key,
    ledger_path,
    weekly_cycle_key,
)
from tests.helpers.map_trade import _seed_action_records


class ProgressTest(unittest.TestCase):
    def test_chapter_eighteen_schema_nine_progress_keeps_battles_and_requires_town(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            now = datetime(2026, 10, 18, 12, tzinfo=UTC_PLUS_8)
            store = ProgressStore(path, lambda: now)
            store.load()
            card = CARD_BY_ID["Q_sp18"]
            for target in card.targets[1:]:
                _seed_action_records(store, card.card_id, target.key)
                store.mark_target(card.card_id, target.key)
            store.state.verified_cards.append(card.card_id)
            store.save()
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["schema_version"] = 9
            path.write_text(json.dumps(raw), encoding="utf-8")
            migrated = ProgressStore(path, lambda: now)
            state = migrated.load()
            self.assertTrue(path.with_suffix(".schema-9.backup.json").exists())
            self.assertEqual(
                {"battle_area_1", "battle_area_2"}, state.completed_targets(card.card_id),
            )
            self.assertFalse(state.card_verified(card.card_id))
            self.assertFalse(migrated.weekly_action_complete(card.card_id, "main_area", "吸收"))
            self.assertEqual(
                STATE_SCHEMA_VERSION,
                json.loads(path.read_text(encoding="utf-8"))["schema_version"],
            )
            raw["schema_version"] = 10
            path.write_text(json.dumps(raw), encoding="utf-8")
            unchanged = path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "兼容迁移"):
                ProgressStore(path, lambda: now).load()
            self.assertEqual(unchanged, path.read_bytes())

    def test_event_records_survive_schema_eight_migration_and_daily_rollover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            now = datetime(2026, 10, 13, 12, tzinfo=UTC_PLUS_8)
            store = ProgressStore(path, lambda: now)
            store.load()
            _seed_action_records(store, "Q_sp1", "main_area")
            store.mark_target("Q_sp1", "main_area")
            store.mark_depleted_today()
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["schema_version"] = 8
            raw["cooldown_records"] = {"Event_1": "2026-10-13T12:00:00+08:00"}
            (path.parent / "collection_cooldown.json").write_text(json.dumps({
                "Event_1": "2026-10-06T12:00:00+08:00", "removed": "old",
            }), encoding="utf-8")
            path.write_text(json.dumps(raw), encoding="utf-8")
            migrated = ProgressStore(path, lambda: now)
            migrated.load()
            self.assertTrue(path.with_suffix(".schema-8.backup.json").exists())
            self.assertTrue(migrated.state.depleted_today)
            self.assertEqual(raw["cooldown_records"], migrated.state.cooldown_records)
            self.assertEqual({"main_area"}, migrated.state.completed_targets("Q_sp1"))
            _seed_action_records(migrated, "Q_ep3", "battle_area_1")
            migrated.mark_target("Q_ep3", "battle_area_1")
            migrated.mark_card_visual_complete("Q_ep2")
            self.assertEqual([], migrated.state.verified_cards)
            self.assertNotIn("Q_ep2", migrated.state.cards)
            self.assertFalse(migrated.state.card_verified("Q_ep2"))
            self.assertFalse(any(r["card_id"] == "Q_ep2"
                                 for r in migrated.state.action_records.values()))
            resumed = ProgressStore(path, lambda: now + timedelta(days=1))
            resumed.load()
            self.assertTrue(resumed.state.card_visually_complete("Q_ep2"))
            self.assertFalse(resumed.state.card_visually_complete("Q_ep2", ("吸收", "召集")))
            self.assertEqual({"battle_area_1"}, resumed.state.completed_targets("Q_ep3"))
            next_week = ProgressStore(path, lambda: now + timedelta(days=7)).load()
            self.assertFalse(next_week.card_visually_complete("Q_ep2"))

    def test_external_development_path_is_shared_and_explicit_test_path_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / "shared"
            configure_development_ledger(str(shared))
            self.addCleanup(configure_development_ledger, None)
            def now():
                return datetime(2026, 9, 17, 12, tzinfo=UTC_PLUS_8)
            first = ProgressStore(now_provider=now)
            self.assertEqual((shared / "map_trade_progress.json").resolve(), first.path)
            with first:
                first.load()
                first.mark_action_preexisting_used("Q_sp1", "main_area", "吸收")
                first.mark_target("Q_sp1", "main_area")
                with self.assertRaises(RuntimeError):
                    with ProgressStore(now_provider=now):
                        self.fail("another writer must not acquire the same ledger")
            with ProgressStore(now_provider=now) as second:
                second.load()
                self.assertEqual({"main_area"}, second.state.completed_targets("Q_sp1"))
                self.assertEqual(0, second.effective_used("吸收"))
            self.assertEqual(Path(directory) / "unit.json", ProgressStore(
                Path(directory) / "unit.json", now,
            ).path)
            configure_development_ledger(None)
            with patch("ok.Config.config_folder", str(Path(directory) / "production")):
                self.assertEqual(
                    Path(directory) / "production" / "map_trade_progress.json",
                    ledger_path("map_trade_progress.json"),
                )
            with self.assertRaises(ValueError):
                configure_development_ledger("relative-ledger")

    def test_visual_audit_default_uses_runtime_config_path(self):
        from src.tasks.map_trade.audit import CollectionVisualAuditStore

        with tempfile.TemporaryDirectory() as directory:
            config_root = Path(directory) / "production"
            with patch("ok.Config.config_folder", str(config_root)):
                self.assertEqual(
                    config_root / "map_collection_visual_status.json",
                    CollectionVisualAuditStore().path,
                )

    def test_collection_target_can_omit_suppression_when_policy_disables_it(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProgressStore(
                Path(directory) / "progress.json",
                lambda: datetime(2026, 9, 17, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            for action in ("吸收", "召集"):
                store.mark_action_preexisting_used("Q_sp1", "battle_area_1", action)
            self.assertTrue(
                store.mark_target(
                    "Q_sp1",
                    "battle_area_1",
                    required_actions=("吸收", "召集"),
                )
            )

    def test_nonproduction_card_verification_is_policy_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProgressStore(
                Path(directory) / "progress.json",
                lambda: datetime(2026, 9, 17, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            for target in CARD_BY_ID["Q_sp1"].targets:
                _seed_action_records(
                    store,
                    "Q_sp1",
                    target.key,
                    actions=("吸收", "召集"),
                )
                store.mark_target(
                    "Q_sp1",
                    target.key,
                    required_actions=("吸收", "召集"),
                )
            self.assertTrue(
                store.mark_card_verified("Q_sp1", required_actions=("吸收", "召集"))
            )
            self.assertFalse(store.state.card_verified("Q_sp1"))
            self.assertTrue(
                store.state.card_verified(
                    "Q_sp1",
                    required_actions=("吸收", "召集"),
                )
            )
            self.assertNotIn("Q_sp1", store.state.verified_cards)
            resumed = ProgressStore(
                Path(directory) / "progress.json",
                lambda: datetime(2026, 9, 17, 12, tzinfo=UTC_PLUS_8),
            )
            resumed.load()
            self.assertFalse(resumed.state.card_verified("Q_sp1"))
            self.assertTrue(
                resumed.state.card_verified(
                    "Q_sp1",
                    required_actions=("吸收", "召集"),
                )
            )
            with self.assertRaisesRegex(RuntimeError, "requires durable local action"):
                resumed.mark_target("Q_sp1", "battle_area_1")
            resumed.mark_action_preexisting_used("Q_sp1", "battle_area_1", "压制")
            self.assertFalse(resumed.mark_target("Q_sp1", "battle_area_1"))
            resumed.load()
            self.assertTrue(resumed.get_action_record("Q_sp1", "battle_area_1", "压制")["covered"])

    def test_malformed_unresolved_baseline_preserves_reservation_and_blocks_rearming(self):
        for action_state in ("armed", "clicked", "blocked", "local_done", "pending"):
            with self.subTest(state=action_state), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "progress.json"
                store = ProgressStore(
                    path,
                    lambda: datetime(2026, 9, 17, 12, tzinfo=UTC_PLUS_8),
                )
                store.load()
                store.arm_action("Q_sp1", "battle_area_1", "吸收", baseline=(0, 21))
                raw = json.loads(path.read_text(encoding="utf-8"))
                key = next(iter(raw["action_records"]))
                raw["action_records"][key]["baseline"] = []
                raw["action_records"][key]["state"] = action_state
                path.write_text(json.dumps(raw), encoding="utf-8")

                resumed = ProgressStore(path, lambda: datetime(2026, 9, 17, 12, tzinfo=UTC_PLUS_8))
                resumed.load()
                self.assertNotIn("baseline", resumed.state.action_records[key])
                self.assertEqual("blocked", resumed.state.action_records[key]["state"])
                self.assertEqual([], resumed.state.action_records[key]["invalid_baseline"])
                self.assertEqual(action_state, resumed.state.action_records[key]["previous_state"])
                self.assertEqual(1, resumed.effective_used("吸收"))
                self.assertFalse(
                    resumed.arm_action("Q_sp1", "battle_area_1", "吸收", baseline=(0, 21))
                )
                resumed.save()
                resumed.load()
                self.assertFalse(resumed.arm_action("Q_sp1", "battle_area_1", "吸收"))
                self.assertEqual(1, resumed.effective_used("吸收"))

    def test_partial_weekly_actions_and_cooldown_survive_daily_reset(self):
        from src.tasks.collection.cycle import CooldownStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            now = [datetime(2026, 9, 17, 23, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            for action in ("吸收", "召集"):
                store.arm_action("Q_sp1", "battle_area_1", action, baseline=(0, 21))
                store.mark_action_clicked("Q_sp1", "battle_area_1", action)
                store.mark_action_local_done("Q_sp1", "battle_area_1", action, pending=True)
            store.arm_action("Q_sp1", "battle_area_1", "压制", baseline=(0, 60))
            store.mark_action_clicked("Q_sp1", "battle_area_1", "压制")
            CooldownStore(progress=store).mark_complete("Story_02", now=now[0])
            now[0] = datetime(2026, 9, 18, 12, tzinfo=UTC_PLUS_8)
            resumed = ProgressStore(path, lambda: now[0])
            resumed.load()
            self.assertEqual(0, resumed.effective_used("吸收"))
            self.assertEqual(0, resumed.pending_count())
            self.assertTrue(resumed.weekly_action_complete("Q_sp1", "battle_area_1", "吸收"))
            self.assertTrue(resumed.weekly_action_complete("Q_sp1", "battle_area_1", "召集"))
            self.assertFalse(resumed.weekly_action_complete("Q_sp1", "battle_area_1", "压制"))
            self.assertEqual(3, len(resumed.state.archived_action_records))
            self.assertFalse(CooldownStore(progress=resumed).check_available(
                "Story_02", now=now[0],
            )[0])
            resumed.mark_action_preexisting_used("Q_sp1", "battle_area_1", "压制")
            self.assertTrue(resumed.mark_target("Q_sp1", "battle_area_1"))
            self.assertEqual({"吸收": 0, "召集": 0, "压制": 0}, resumed.effective_daily_counts())
            now[0] = datetime(2026, 9, 21, 12, tzinfo=UTC_PLUS_8)
            self.assertEqual({}, resumed.load().weekly_actions)

    def test_schema_five_migration_preserves_clicks_and_removes_phantom_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            def now():
                return datetime(2026, 9, 17, 12, tzinfo=UTC_PLUS_8)
            store = ProgressStore(path, now)
            store.load()
            store.mark_action_preexisting_used("Q_sp1", "main_area", "吸收")
            store.mark_target("Q_sp1", "main_area")
            store.arm_action("Q_sp1", "battle_area_1", "召集", baseline=(0, 21))
            store.mark_action_clicked("Q_sp1", "battle_area_1", "召集")
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.update(schema_version=5, daily_submaps=1, depleted_today=True)
            raw["cooldown_records"] = {"shared": "current"}
            (path.parent / "collection_cooldown.json").write_text(
                json.dumps({"shared": "old", "legacy": "retained"}), encoding="utf-8",
            )
            raw.pop("weekly_actions")
            record = next(r for r in raw["action_records"].values() if r["action"] == "吸收")
            record.update(pending=True, reservation=True)
            record.pop("source")
            path.write_text(json.dumps(raw), encoding="utf-8")
            before = path.read_bytes()
            resumed = ProgressStore(path, now)
            resumed.load()
            self.assertEqual(
                {"shared": "current", "legacy": "retained"}, resumed.state.cooldown_records,
            )
            self.assertEqual(before, path.with_suffix(".schema-5.backup.json").read_bytes())
            self.assertEqual({"main_area"}, resumed.state.completed_targets("Q_sp1"))
            self.assertFalse(resumed.weekly_action_complete("Q_sp1", "main_area", "吸收"))
            self.assertEqual(0, resumed.pending_count("吸收"))
            self.assertEqual(0, resumed.effective_used("吸收"))
            self.assertEqual(1, resumed.effective_used("召集"))
            self.assertEqual("clicked", resumed.get_action_record(
                "Q_sp1", "battle_area_1", "召集",
            )["state"])
            after = path.read_bytes()
            resumed.load()
            self.assertEqual(after, path.read_bytes())
            raw = json.loads(after)
            raw["schema_version"] = STATE_SCHEMA_VERSION + 1
            path.write_text(json.dumps(raw), encoding="utf-8")
            future = path.read_bytes()
            with self.assertRaises(RuntimeError):
                resumed.load()
            self.assertEqual(future, path.read_bytes())

    def test_weekly_collection_completion_uses_verified_supported_cards(self):
        from src.tasks.map_trade.progress import ProgressState

        state = ProgressState(weekly_key="2026-08-17", daily_key="2026-08-18")
        self.assertFalse(state.weekly_collection_complete)

        for card in COLLECTABLE_CARDS:
            state.cards[card.card_id] = [target.key for target in card.targets]
            state.verified_cards.append(card.card_id)

        self.assertTrue(state.weekly_collection_complete)

        supported = COLLECTABLE_CARDS[0]
        state.verified_cards.remove(supported.card_id)
        self.assertFalse(state.weekly_collection_complete)

    def test_daily_cycle_changes_at_eight_am(self):
        before = datetime(2026, 7, 13, 7, 59, tzinfo=UTC_PLUS_8)
        after = datetime(2026, 7, 13, 8, 0, tzinfo=UTC_PLUS_8)

        self.assertEqual("2026-07-12", daily_cycle_key(before))
        self.assertEqual("2026-07-13", daily_cycle_key(after))
        self.assertEqual(
            "2026-07-12", daily_cycle_key(datetime(2026, 7, 13, 4, tzinfo=UTC_PLUS_8)),
        )
        japan = timezone(timedelta(hours=9))
        self.assertEqual("2026-07-12", daily_cycle_key(before.astimezone(japan)))
        self.assertEqual("2026-07-13", daily_cycle_key(after.astimezone(japan)))

    def test_weekly_cycle_changes_monday_at_eight_am(self):
        sunday = datetime(2026, 7, 12, 8, 0, tzinfo=UTC_PLUS_8)
        monday_before = datetime(2026, 7, 13, 7, 59, tzinfo=UTC_PLUS_8)
        monday_after = datetime(2026, 7, 13, 8, 0, tzinfo=UTC_PLUS_8)

        self.assertEqual("2026-07-06", weekly_cycle_key(sunday))
        self.assertEqual("2026-07-06", weekly_cycle_key(monday_before))
        self.assertEqual("2026-07-13", weekly_cycle_key(monday_after))
        self.assertEqual(
            "2026-07-06", weekly_cycle_key(datetime(2026, 7, 13, 4, tzinfo=UTC_PLUS_8)),
        )

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

    def test_progress_saves_each_submap_and_stops_at_observed_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            store = ProgressStore(path, lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8))
            store.load()
            store.reconcile_pending("吸收", (0, 21))
            for card in COLLECTABLE_CARDS[:7]:
                for target in card.targets:
                    _seed_action_records(store, card.card_id, target.key)
                    self.assertTrue(store.mark_target(card.card_id, target.key))
                    self.assertTrue(path.exists())
                    self.assertFalse(path.with_suffix(".json.tmp").exists())
                self.assertTrue(store.mark_card_verified(card.card_id))

            self.assertEqual(21, store.state.daily_absorbs)
            self.assertEqual(14, store.state.daily_summons)
            self.assertEqual(14, store.state.daily_suppressions)
            self.assertTrue(store.state.depleted_today)
            self.assertEqual(21, store.state.weekly_submap_count)
            self.assertTrue(
                all(store.state.card_verified(card.card_id) for card in COLLECTABLE_CARDS[:7])
            )
            next_card = COLLECTABLE_CARDS[7]
            self.assertFalse(
                store.arm_action(
                    next_card.card_id,
                    next_card.targets[0].key,
                    "吸收",
                )
            )
            self.assertTrue(store.state.depleted_today)

    def test_skill_budgets_follow_persisted_hud_limits_and_reset_each_day(self):
        for action, limit in (("吸收", 30), ("召集", 28), ("压制", 75)):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "progress.json"
                now = [datetime(2026, 10, 8, 12, tzinfo=UTC_PLUS_8)]
                store = ProgressStore(path, lambda: now[0])
                store.load()
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
                    action != "压制", store.collection_budget_exhausted(("吸收", "召集")),
                )
                self.assertEqual(
                    action == "压制", store.can_plan_collection(
                        CARD_BY_ID["Q_sp1"].targets, required_actions=("吸收", "召集"),
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
                now[0] += timedelta(days=1)
                store.load()
                self.assertIsNone(store.action_limit(action))
                self.assertFalse(store.collection_budget_exhausted())

    def test_schema_seven_rechecks_exhaustion_and_preserves_action_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            now = datetime(2026, 10, 8, 12, tzinfo=UTC_PLUS_8)
            store = ProgressStore(path, lambda: now)
            store.load()
            store.mark_action_preexisting_used("Q_sp1", "battle_area_1", "压制")
            store.reconcile_pending("压制", (60, 75))
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.update(schema_version=7, depleted_today=True, daily_suppressions=60)
            path.write_text(json.dumps(raw), encoding="utf-8")
            state = store.load()
            self.assertFalse(store.collection_budget_exhausted())
            self.assertEqual(75, store.action_limit("压制"))
            self.assertEqual(60, state.daily_suppressions)
            self.assertEqual(
                CollectionActionState.PREEXISTING_USED.value,
                store.get_action_record("Q_sp1", "battle_area_1", "压制")["state"],
            )
            self.assertEqual(STATE_SCHEMA_VERSION, json.loads(path.read_text())["schema_version"])

    def test_progress_rejects_cards_with_no_collection_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()

            for card_id in ("Q_sp6", "Q_sp20"):
                with (
                    self.subTest(card_id=card_id),
                    self.assertRaisesRegex(ValueError, "invalid collection card"),
                ):
                    store.mark_target(card_id, CollectionMapRole.MAIN_AREA.value)

    def test_excluded_cards_are_archived_without_local_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            now = datetime(2026, 10, 9, 12, tzinfo=UTC_PLUS_8)
            store = ProgressStore(path, lambda: now)
            store.load()
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
                    "source": "click", "daily_key": record["daily_key"],
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
            self.assertFalse(any(
                record["card_id"] == "Q_sp6" for record in state.action_records.values()
            ))
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
            now = [datetime(2026, 7, 12, 7, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            for target in CARD_BY_ID["Q_sp1"].targets:
                _seed_action_records(store, "Q_sp1", target.key)
                store.mark_target("Q_sp1", target.key)
            store.mark_card_verified("Q_sp1")
            now[0] = datetime(2026, 7, 12, 8, 0, tzinfo=UTC_PLUS_8)

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
            store = ProgressStore(path, lambda: datetime(2026, 7, 13, 7, 59, tzinfo=UTC_PLUS_8))
            store.load()
            _seed_action_records(
                store,
                "Q_sp1",
                CollectionMapRole.MAIN_AREA.value,
            )
            store.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)

            state = ProgressStore(
                path, lambda: datetime(2026, 7, 13, 8, 0, tzinfo=UTC_PLUS_8)
            ).load()

            self.assertEqual({}, state.cards)
            self.assertEqual(0, state.weekly_submap_count)
            self.assertEqual([], state.verified_cards)

    def test_supported_story_and_character_maps_persist_across_daily_rollover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            now = [datetime(2026, 7, 13, 12, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: now[0],
            )
            store.load()
            for card_index, card in enumerate(COLLECTABLE_CARDS):
                if card_index and card_index % 7 == 0:
                    now[0] = now[0].replace(day=now[0].day + 1)
                    store.load()
                for target in card.targets:
                    _seed_action_records(store, card.card_id, target.key)
                    store.mark_target(card.card_id, target.key)

            self.assertEqual(72, store.state.weekly_submap_count)

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

    def test_corrupt_file_stops_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            path.write_text("{broken", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                ProgressStore(path, lambda: datetime(2026, 7, 12, 12, tzinfo=UTC_PLUS_8)).load()
            self.assertEqual("{broken", path.read_text(encoding="utf-8"))
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
                (0, 0, 0),
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

            now[0] = datetime(2026, 8, 17, 8, 0, tzinfo=UTC_PLUS_8)
            state = ProgressStore(path, lambda: now[0]).load()

            self.assertEqual(set(), state.completed_cooking_recipes)

    def test_action_ledger_is_idempotent_and_archives_at_daily_rollover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 8, 11, 7, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()

            self.assertTrue(store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"))
            self.assertFalse(store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"))
            store.mark_action_clicked("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            store.mark_action_local_done("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True)
            self.assertEqual(1, store.pending_count())

            now[0] = datetime(2026, 8, 11, 8, 0, tzinfo=UTC_PLUS_8)
            resumed = ProgressStore(path, lambda: now[0])
            state = resumed.load()

            self.assertEqual({}, state.action_records)
            self.assertEqual(1, len(state.archived_action_records))
            self.assertEqual(
                CollectionActionState.ARCHIVED.value,
                next(iter(state.archived_action_records.values()))["state"],
            )
            self.assertEqual(0, resumed.pending_count())

    def test_collection_plan_reuses_own_intent_at_last_quota(self):
        for intent_state in ("armed", "clicked", "blocked"):
            with (
                self.subTest(intent_state=intent_state),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = Path(directory) / "progress.json"
                now = datetime(2026, 10, 6, 12, tzinfo=UTC_PLUS_8)
                store = ProgressStore(path, lambda: now)
                store.load()
                card = CARD_BY_ID["Q_sp2"]
                target = card.targets[-1]
                _seed_action_records(store, card.card_id, target.key, actions=("召集", "压制"))
                store.reconcile_pending("吸收", (20, 21))
                self.assertTrue(
                    store.arm_action(card.card_id, target.role, "吸收", baseline=(20, 21)),
                )
                if intent_state == "clicked":
                    store.mark_action_clicked(card.card_id, target.role, "吸收")
                elif intent_state == "blocked":
                    store.mark_action_blocked(card.card_id, target.role, "吸收")
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
                self.assertTrue(store.arm_action(
                    card.card_id, target.role, "吸收", baseline=(20, 21),
                ))
                store.mark_depleted_today()
                self.assertTrue(store.can_plan_collection((target,), card_id=card.card_id))
                self.assertFalse(store.can_reserve_action("吸收"))

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

    def test_preexisting_map_state_does_not_claim_daily_consumption(self):
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
            self.assertNotIn("baseline", record)
            self.assertFalse(record["pending"])
            self.assertEqual(0, store.effective_used("吸收"))
            self.assertEqual({}, store.state.observed_counts)
            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 20)))
            self.assertEqual((1, 20), store.state.observed_counts["吸收"])
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual((1, 21), store.state.observed_counts["吸收"])
            self.assertEqual(0, store.reconcile_pending("吸收", (0, 21)))
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(CollectionActionState.PREEXISTING_USED.value, record["state"])

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

    def test_reconcile_settles_record_covered_by_snapshot_taken_while_armed(self):
        """BUG-20260906-02 回归：快照在记录尚无结算资格时先持久化（点击后被
        判定失败、用户停止或崩溃打断），记录转挂账后等额读数必须按覆盖结算，
        否则该技能被守卫拒绝点击直到次日重置。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8),
            )
            store.load()
            store.arm_action("Q_sp2", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            store.mark_action_clicked("Q_sp2", CollectionMapRole.MAIN_AREA, "吸收")
            # 点击后、判定前的稳定读数：记录仍为 CLICKED，结不清但快照已持久化。
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual((1, 21), store.state.observed_counts["吸收"])
            store.mark_action_local_done("Q_sp2", CollectionMapRole.MAIN_AREA, "吸收", pending=True)
            # 恢复后的等额稳定读数按覆盖结算，解除死锁。
            self.assertEqual(1, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, store.pending_count("吸收"))
            record = store.get_action_record("Q_sp2", CollectionMapRole.MAIN_AREA, "吸收")
            self.assertEqual(CollectionActionState.SETTLED.value, record["state"])
            self.assertFalse(record["pending"])
            self.assertFalse(record["reservation"])
            self.assertEqual([1, 21], record["observed"])
            # 结算幂等。
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual((1, 21), store.state.observed_counts["吸收"])

    def test_reconcile_coverage_skips_record_already_offered_the_snapshot(self):
        """覆盖结算只针对快照持久化时尚无结算资格的记录；已按增量获得过
        机会的挂账不得被等额快照重复入账。"""

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
            # 未发生新消耗：覆盖结算不得把第二条挂账一并结清。
            self.assertEqual(0, store.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(1, store.pending_count("吸收"))
            self.assertEqual(1, store.reconcile_pending("吸收", (2, 21)))
            self.assertEqual(0, store.pending_count("吸收"))
            self.assertEqual((2, 21), store.state.observed_counts["吸收"])

    def test_delayed_pending_records_share_snapshot_budget_after_reload(self):
        for already_pending in (False, True):
            for next_used in (1, 2):
                with self.subTest(already_pending=already_pending, next_used=next_used):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        path = Path(temp_dir) / "progress.json"
                        def now():
                            return datetime(2026, 8, 10, 12, tzinfo=UTC_PLUS_8)
                        store = ProgressStore(path, now)
                        store.load()
                        roles = (
                            CollectionMapRole.MAIN_AREA,
                            CollectionMapRole.BATTLE_AREA_1,
                            CollectionMapRole.BATTLE_AREA_2,
                        )
                        for role in roles:
                            store.arm_action("Q_sp1", role, "吸收", baseline=(0, 21))
                            store.mark_action_clicked("Q_sp1", role, "吸收")
                        if already_pending:
                            store.mark_action_local_done("Q_sp1", roles[0], "吸收")
                        self.assertEqual(
                            int(already_pending), store.reconcile_pending("吸收", (1, 21))
                        )
                        for role in roles:
                            store.mark_action_local_done("Q_sp1", role, "吸收")
                        store = ProgressStore(path, now)
                        store.load()
                        self.assertEqual(
                            next_used - int(already_pending),
                            store.reconcile_pending("吸收", (next_used, 21)),
                        )
                        self.assertEqual(3 - next_used, store.pending_count("吸收"))
                        store = ProgressStore(path, now)
                        store.load()
                        self.assertEqual(0, store.reconcile_pending("吸收", (next_used, 21)))
                        self.assertEqual(3 - next_used, store.pending_count("吸收"))

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
            # Both raw malformed entries and already isolated evidence survive rollover.
            before_rollover = ProgressStore(
                path, lambda: datetime(2026, 8, 17, 12, tzinfo=UTC_PLUS_8),
            )
            rolled = before_rollover.load()
            self.assertIn("quarantine|broken-value", rolled.archived_action_records)
            self.assertIn(f"quarantine|{invalid_action_key}", rolled.archived_action_records)
            evidence = dict(rolled.archived_action_records)
            self.assertEqual(evidence, before_rollover.load().archived_action_records)
            self.assertEqual(0, before_rollover.effective_used("吸收"))

    def test_weekly_rollover_archives_unresolved_actions_and_resets_absolute_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "progress.json"
            now = [datetime(2026, 7, 13, 7, 59, tzinfo=UTC_PLUS_8)]
            store = ProgressStore(path, lambda: now[0])
            store.load()
            store.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            store.mark_action_local_done("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True)
            now[0] = datetime(2026, 7, 13, 8, 0, tzinfo=UTC_PLUS_8)

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
