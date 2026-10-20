"""Map-trade collector tests (split from test_map_trade.py)."""

import tempfile
import unittest
from datetime import datetime
from itertools import cycle
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    SUMMON_ICON,
    ActionIconDetection,
    ActionIconState,
)
from src.tasks.map_trade.card_status import (
    CollectionCardSelectionOutcome,
    CollectionCardSelectionResult,
)
from src.tasks.map_trade.collector import (
    Collector,
)
from src.tasks.map_trade.collector_constants import (
    ABSORB_ACTION,
    ACTION_FEEDBACK_CHARACTER_RATIO,
    ACTION_FEEDBACK_RELATIVE_ROI,
    ACTION_FEEDBACK_RELEVANCE_RATIO,
    ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS,
    ACTION_FEEDBACK_TIMEOUT,
    ACTION_ICON_DETECTION_INTERVAL,
    ACTION_OCR_WINDOW_INTERVAL,
    BATTLE_ACTIONS,
    SEARCH_ACTION,
    SEARCH_BUFF_SECONDS,
    SEARCH_COUNTDOWN_REFERENCE_ROI,
    SEARCH_COUNTDOWN_RELATIVE_ROI,
    SEARCH_REUSE_MAX_AGE_SECONDS,
    SKILL_FAILURE_EVIDENCE_LIMIT,
    SKILL_FIXED_COUNT_REFERENCE_ROIS,
    SKILL_GROUP_REFERENCE_POINTS,
    SKILL_GROUP_RELATIVE_POINTS,
    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SKILL_OCR_FALLBACK_UPSCALE,
    SKILL_OCR_UPSCALE,
    SUMMON_ACTION,
    SUPPRESS_ACTION,
    CountWindowObservation,
    SearchCountdownSession,
    SkillExecutionResult,
    SkillFeedbackObservation,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    CollectionActionState,
    CollectionMapRole,
    CollectionResult,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.progress import (
    UTC_PLUS_8,
    ProgressStore,
)
from tests.helpers.map_trade import _seed_action_records, _seed_battle_supplements


class CollectionRunTest(unittest.TestCase):
    def test_exhausted_absorb_does_not_block_other_cards_remaining_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(
                Path(directory) / "progress.json",
                lambda: datetime(2026, 10, 20, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            card = CARD_BY_ID["Q_sp2"]
            for target in card.targets:
                _seed_action_records(progress, card.card_id, target.key, ("吸收",))
            progress.mark_target(card.card_id, "main_area")
            progress.reconcile_pending("吸收", (3, 3))
            progress.reconcile_pending("召集", (0, 10))
            progress.reconcile_pending("压制", (0, 10))
            sandbox = NavigationResult(True, ScreenState.SANDBOX)
            menu = NavigationResult(True, ScreenState.CARD_MENU)
            navigator = SimpleNamespace(
                select_collection_card=Mock(return_value=CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED, sandbox,
                )),
                current_collection_target=lambda _card: "main_area",
                advance_collection_map=Mock(return_value=sandbox),
                open_cartridge_quick_switcher_from_sandbox=Mock(return_value=menu),
                inspect_collection_card_completion=Mock(return_value=menu),
            )
            collector = Collector(SimpleNamespace(
                config={}, info_set=Mock(), log_warning=Mock(), log_error=Mock(),
            ), SimpleNamespace(), navigator, progress)
            collector._start_search = Mock(side_effect=AssertionError("absorb already complete"))
            collector._open_skill_menu = Mock(return_value=True)
            performed = []

            def use(action, *, card_id, map_role):
                if not progress.weekly_action_complete(card_id, map_role, action.name):
                    performed.append(action.name)
                    _seed_action_records(progress, card_id, map_role.value, (action.name,))
                return SkillExecutionResult(True)

            collector._use_action = use
            result = collector.run((CARD_BY_ID["Q_sp1"], card))
            self.assertTrue(result.success)
            self.assertTrue(result.depleted)
            self.assertTrue(progress.state.card_verified(card.card_id))
            self.assertFalse(progress.state.card_verified("Q_sp1"))
            navigator.select_collection_card.assert_called_once_with(
                card.card_id, enter_visually_complete=False,
            )
            self.assertEqual(["召集", "压制", "召集", "压制"], performed)

    def setUp(self):
        search = patch.object(
            Collector,
            "_ensure_search",
            autospec=True,
            side_effect=lambda collector, **kwargs: collector._start_search(**kwargs),
        )
        guard = patch.object(Collector, "_absorb_used_up", return_value=None)
        search.start()
        guard.start()
        self.addCleanup(search.stop)
        self.addCleanup(guard.stop)

    def test_last_quota_recovery_resumes_persisted_actions_without_casts(self):
        for phase in ("clicked", "observed", "local_done", "bright", "later_summon"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                progress = ProgressStore(
                    Path(directory) / "progress.json",
                    lambda: datetime(2026, 10, 6, 12, tzinfo=UTC_PLUS_8),
                )
                progress.load()
                card = CARD_BY_ID["Q_sp2"]
                target = card.targets[-1]
                for completed in card.targets[:2]:
                    _seed_action_records(progress, card.card_id, completed.key)
                    progress.mark_target(card.card_id, completed.key)
                action = SUMMON_ACTION if phase == "later_summon" else ABSORB_ACTION
                _seed_action_records(
                    progress, card.card_id, target.key,
                    actions=("压制",) if phase == "later_summon" else ("召集", "压制"),
                )
                progress.reconcile_pending(action.name, (20, 21))
                progress.arm_action(card.card_id, target.role, action.name, baseline=(20, 21))
                progress.mark_action_clicked(card.card_id, target.role, action.name)
                if phase in {"observed", "local_done", "later_summon"}:
                    progress.reconcile_pending(action.name, (21, 21))
                if phase == "later_summon":
                    progress.reconcile_pending("吸收", (21, 21))
                if phase == "local_done":
                    progress.mark_action_local_done(card.card_id, target.role, action.name)
                progress.load()
                before_counts = progress.effective_daily_counts()
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                vision = SimpleNamespace(capture=lambda: frame, click_client=Mock())
                sandbox = NavigationResult(True, ScreenState.SANDBOX)
                card_menu = NavigationResult(True, ScreenState.CARD_MENU)
                navigator = SimpleNamespace(
                    select_collection_card=Mock(return_value=CollectionCardSelectionResult(
                        CollectionCardSelectionOutcome.ENTERED, sandbox,
                    )),
                    current_collection_target=lambda card: card.targets[0].key,
                    prepare_collection_main_area=Mock(return_value=sandbox),
                    advance_collection_map=Mock(return_value=sandbox),
                    open_cartridge_quick_switcher_from_sandbox=Mock(return_value=card_menu),
                    inspect_collection_card_completion=Mock(return_value=card_menu),
                )
                collector = Collector(
                    SimpleNamespace(
                        config={}, sleep=Mock(), info_set=Mock(),
                        log_error=Mock(), log_warning=Mock(),
                    ),
                    vision, navigator, progress,
                )
                collector._start_search = Mock()
                collector._open_skill_menu = Mock(return_value=True)
                collector.action_icons = SimpleNamespace(
                    detect=Mock(return_value=ActionIconDetection(
                        ActionIconState.AVAILABLE if phase == "bright" else ActionIconState.USED,
                        MatchResult(0.98, (100, 100), (40, 40)), stable=True, sample_count=2,
                    )),
                )
                with patch(
                    "src.tasks.map_trade.collector.COLLECTABLE_CARDS", (CARD_BY_ID["Q_sp1"], card),
                ):
                    result = collector.run()
                collector._start_search.assert_not_called()
                vision.click_client.assert_not_called()
                navigator.select_collection_card.assert_called_once()
                self.assertEqual(card.card_id, navigator.select_collection_card.call_args.args[0])
                self.assertTrue(
                    navigator.select_collection_card.call_args.kwargs["enter_visually_complete"],
                )
                progress.load()
                self.assertTrue(progress.state.depleted_today)
                self.assertEqual(before_counts, progress.effective_daily_counts())
                self.assertEqual(phase != "bright", result.success)
                self.assertEqual(
                    phase not in {"bright", "later_summon"},
                    progress.state.card_verified(card.card_id),
                )
                if phase == "later_summon":
                    self.assertTrue(
                        progress.weekly_action_complete(card.card_id, target.role, "召集"),
                    )
                    self.assertFalse(
                        progress.weekly_action_complete(card.card_id, target.role, "吸收"),
                    )
                    self.assertNotIn(target.key, progress.state.completed_targets(card.card_id))
                    self.assertTrue(result.depleted)

    def test_collection_retries_first_card_and_stops_when_home_recovery_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            attempts = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 3},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                attempts.append((card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.FAILED,
                    NavigationResult(False, ScreenState.UNKNOWN, "failed"),
                )

            navigator = SimpleNamespace(
                select_collection_card=select,
                return_home=Mock(return_value=NavigationResult(
                    False, ScreenState.UNKNOWN, "未确认主页",
                )),
            )
            result = Collector(task, object(), navigator, progress).run()

        self.assertFalse(result.success)
        self.assertFalse(result.partial)
        self.assertIn("未能进入卡带 Q_sp1：failed", result.message)
        self.assertIn("返回主页失败", result.message)
        self.assertEqual([("Q_sp1", False)] * 3, attempts)

    def test_recoverable_card_failure_continues_but_never_marks_run_complete(self):
        for retryable, subsequent, partial in (
            (True, None, True),
            (True, CollectionResult(True, depleted=True), True),
            (True, CollectionResult(False, message="技能操作失败", retryable=False), False),
            (False, None, False),
        ):
            with self.subTest(retryable=retryable, subsequent=subsequent), \
                    tempfile.TemporaryDirectory() as directory:
                progress = ProgressStore(Path(directory) / "progress.json")
                task = SimpleNamespace(config={}, info_set=Mock(), log_warning=Mock())
                navigator = SimpleNamespace(return_home=Mock(return_value=NavigationResult(
                    True, ScreenState.HOME,
                )))
                collector = Collector(task, object(), navigator, progress)
                collector.collect_card = Mock(side_effect=[
                    CollectionResult(False, message="入口未确认", retryable=retryable), subsequent,
                ])
                with patch("src.tasks.map_trade.collector.COLLECTABLE_CARDS", (
                    CARD_BY_ID["Q_sp18"], CARD_BY_ID["Q_cp7"],
                )):
                    result = collector.run()
                self.assertFalse(result.success)
                self.assertEqual(partial, result.partial)
                if partial:
                    self.assertIn("第18章未完成，已跳过", result.message)
                    if subsequent is None:
                        self.assertIn("其余卡带采集成功", result.message)
                    else:
                        self.assertTrue(result.depleted)
                        self.assertIn("技能额度不足", result.message)
                        self.assertNotIn("其余卡带采集成功", result.message)
                    task.info_set.assert_any_call("采集未完成卡带", "第18章：入口未确认")
                else:
                    self.assertIn("入口未确认", result.message)
                self.assertEqual(2 if retryable else 1, collector.collect_card.call_count)
                self.assertEqual(int(retryable), navigator.return_home.call_count)
                self.assertEqual({}, progress.state.cards)

    def test_collector_run_converts_runtime_error_to_collection_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            task = SimpleNamespace(
                config={},
                log_error=lambda *_args, **_kwargs: None,
                log_warning=lambda *_args, **_kwargs: None,
                info_set=lambda *_args: None,
            )
            collector = Collector(task, object(), object(), progress)

            def boom(*_args, **_kwargs):
                raise RuntimeError("click interrupted")

            collector._run_collection = boom

            result = collector.run()

        self.assertFalse(result.success)
        self.assertIn("地图采集流程异常", result.message)
        self.assertIn("click interrupted", result.message)

    def test_formal_collection_runs_safe_battle_one_battle_two_then_verifies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.state.daily_submaps = 18
            progress.state.daily_summons = 12
            progress.state.daily_suppressions = 12
            progress.reconcile_pending("吸收", (18, 21))
            progress.save()
            events = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                events.append(("select", card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED,
                    NavigationResult(True, ScreenState.SANDBOX),
                )

            navigator = SimpleNamespace(
                select_collection_card=select,
                current_collection_target=lambda card: card.targets[0].key,
                prepare_collection_main_area=lambda card_id: (
                    events.append(("prepare", card_id))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                advance_collection_map=lambda card_id, current, target: (
                    events.append(("advance", card_id, current.key, target.key))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                open_cartridge_quick_switcher_from_sandbox=lambda **_kwargs: (
                    events.append(("quick",)) or NavigationResult(True, ScreenState.CARD_MENU)
                ),
                inspect_collection_card_completion=lambda card_id: (
                    events.append(("inspect", card_id))
                    or CollectionCardSelectionResult(
                        CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                        NavigationResult(True, ScreenState.CARD_MENU),
                    )
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            search = SearchCountdownSession((0.1, 0.2, 0.3, 0.4), 87)
            collector._start_search = lambda **_kwargs: events.append(("search",)) or search

            def fake_use_actions(actions, *, card_id, map_role):
                events.append(("actions", tuple(action.name for action in actions)))
                _seed_action_records(progress, card_id, map_role.value)
                return SkillExecutionResult(True)

            collector._use_actions = fake_use_actions

            result = collector.run()

        self.assertTrue(result.success)
        self.assertTrue(result.depleted)
        self.assertEqual(3, result.completed_submaps)
        self.assertEqual(
            [
                ("select", "Q_sp1", False),
                ("search",),
                ("actions", ("吸收",)),
                ("advance", "Q_sp1", "main_area", "battle_area_1"),
                ("search",),
                ("actions", tuple(action.name for action in BATTLE_ACTIONS)),
                ("advance", "Q_sp1", "battle_area_1", "battle_area_2"),
                ("search",),
                ("actions", tuple(action.name for action in BATTLE_ACTIONS)),
                ("quick",),
                ("inspect", "Q_sp1"),
            ],
            events,
        )
        self.assertEqual(21, progress.state.daily_absorbs)
        self.assertEqual(14, progress.state.daily_summons)
        self.assertEqual(14, progress.state.daily_suppressions)
        self.assertTrue(progress.state.card_verified("Q_sp1"))

    def test_collection_never_starts_a_card_that_cannot_fit_enabled_actions(self):
        for action, count, reserved in (
            ("吸收", (19, 21), False), ("压制", (74, 75), False), ("压制", (73, 75), True),
        ):
            with (
                self.subTest(action=action, reserved=reserved),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                progress = ProgressStore(
                    Path(temp_dir) / "progress.json",
                    lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
                )
                progress.load()
                progress.reconcile_pending(action, count)
                if reserved:
                    self.assertTrue(progress.arm_action(
                        "Q_sp2", "battle_area_1", action, baseline=count,
                    ))
                task = SimpleNamespace(
                    config={"卡带单步重试次数": 1},
                    log_warning=lambda *_args: None,
                    info_set=lambda *_args: None,
                )
                navigator = SimpleNamespace(
                    select_collection_card=Mock(side_effect=AssertionError(
                        "an incomplete card must not be started",
                    )),
                )
                result = Collector(task, object(), navigator, progress).run((CARD_BY_ID["Q_sp1"],))
                self.assertTrue(result.success)
                self.assertTrue(result.depleted)
                self.assertEqual(0, result.completed_submaps)
                navigator.select_collection_card.assert_not_called()
                progress.load()
                self.assertEqual(
                    {action: 3 if action == "吸收" else 2}, progress.state.depleted_requirements,
                )
                self.assertTrue(progress.state.depleted_today)
                self.assertEqual(
                    action == "吸收", progress.collection_budget_exhausted(("吸收", "召集")),
                )
                if reserved:
                    progress.mark_action_void("Q_sp2", "battle_area_1", action)
                    progress.load()
                    self.assertFalse(progress.state.depleted_today)
                    self.assertTrue(progress.can_plan_collection(CARD_BY_ID["Q_sp1"].targets))

    def test_formal_collection_runs_chapter_fourteen_before_quota_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.state.daily_submaps = 18
            progress.state.daily_summons = 12
            progress.state.daily_suppressions = 12
            progress.reconcile_pending("吸收", (18, 21))
            progress.save()
            selected = []
            warnings = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda value: warnings.append(value),
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                selected.append((card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED,
                    NavigationResult(True, ScreenState.SANDBOX),
                )

            navigator = SimpleNamespace(
                select_collection_card=select,
                current_collection_target=lambda card: card.targets[0].key,
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                advance_collection_map=lambda *_args: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                open_cartridge_quick_switcher_from_sandbox=lambda **_kwargs: NavigationResult(
                    True,
                    ScreenState.CARD_MENU,
                ),
                inspect_collection_card_completion=lambda _card_id: CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    NavigationResult(True, ScreenState.CARD_MENU),
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            collector._start_search = lambda **_kwargs: SearchCountdownSession(
                (0.1, 0.2, 0.3, 0.4),
                80,
            )

            def fake_use_actions(_actions, *, card_id, map_role):
                _seed_action_records(progress, card_id, map_role.value)
                return SkillExecutionResult(True)

            collector._use_actions = fake_use_actions
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (CARD_BY_ID["Q_sp14"], CARD_BY_ID["Q_sp15"]),
            ):
                result = collector.run()

        self.assertTrue(result.success)
        self.assertEqual([("Q_sp14", False)], selected)
        self.assertEqual(
            {target.key for target in CARD_BY_ID["Q_sp14"].targets},
            progress.state.completed_targets("Q_sp14"),
        )
        self.assertTrue(progress.state.card_verified("Q_sp14"))
        self.assertEqual(set(), progress.state.completed_targets("Q_sp15"))
        self.assertEqual([], warnings)

    def test_observed_skill_limit_finishes_current_battle_actions_then_stops(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )
            events = []
            navigator = SimpleNamespace(
                select_collection_card=lambda *_args, **_kwargs: CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED,
                    NavigationResult(True, ScreenState.SANDBOX),
                ),
                current_collection_target=lambda card: card.targets[0].key,
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                advance_collection_map=lambda _card_id, current, target: (
                    events.append(("advance", current.key, target.key))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                open_cartridge_quick_switcher_from_sandbox=lambda **_kwargs: self.fail(
                    "battle two must be left for the next daily cycle"
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            collector._start_search = lambda **_kwargs: SearchCountdownSession(
                (0.1, 0.2, 0.3, 0.4),
                80,
            )
            action_results = iter(
                (
                    SkillExecutionResult(True),
                    SkillExecutionResult(True, depleted=True),
                )
            )

            def fake_use_actions(_actions, *, card_id, map_role):
                _seed_action_records(progress, card_id, map_role.value)
                if map_role == CollectionMapRole.BATTLE_AREA_1:
                    progress.reconcile_pending("吸收", (21, 21))
                return next(action_results)

            collector._use_actions = fake_use_actions
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (CARD_BY_ID["Q_sp1"],),
            ):
                result = collector.run()

        self.assertTrue(result.success)
        self.assertTrue(result.depleted)
        self.assertEqual(2, result.completed_submaps)
        self.assertEqual(
            {"main_area", "battle_area_1"},
            progress.state.completed_targets("Q_sp1"),
        )
        self.assertEqual(
            [("advance", "main_area", "battle_area_1")],
            events,
        )

    def test_final_map_pending_is_success_warning_and_completed_target_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            card = CARD_BY_ID["Q_sp1"]
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                info_set=lambda *_args: None,
                log_warning=lambda *_args: None,
            )
            selected = []
            navigator = SimpleNamespace(
                select_collection_card=lambda card_id, **_kwargs: (
                    selected.append(card_id)
                    or CollectionCardSelectionResult(
                        CollectionCardSelectionOutcome.ENTERED,
                        NavigationResult(True, ScreenState.SANDBOX),
                    )
                ),
                current_collection_target=lambda card: card.targets[0].key,
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True, ScreenState.SANDBOX
                ),
                advance_collection_map=lambda *_args: NavigationResult(True, ScreenState.SANDBOX),
                open_cartridge_quick_switcher_from_sandbox=lambda **_kwargs: NavigationResult(
                    True, ScreenState.CARD_MENU
                ),
                inspect_collection_card_completion=lambda _card_id: NavigationResult(
                    True, ScreenState.CARD_MENU
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            collector._start_search = lambda **_kwargs: SearchCountdownSession(
                (0.1, 0.2, 0.3, 0.4), 80
            )

            def use_actions(actions, *, card_id, map_role):
                for action in actions:
                    limit = {"吸收": 21, "召集": 21, "压制": 60}[action.name]
                    progress.arm_action(
                        card_id,
                        map_role,
                        action.name,
                        baseline=(0, limit),
                    )
                    progress.mark_action_local_done(
                        card_id,
                        map_role,
                        action.name,
                        pending=True,
                    )
                return SkillExecutionResult(
                    True,
                    pending_actions=tuple(action.name for action in actions),
                )

            collector._use_actions = use_actions
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (card,),
            ):
                result = collector.run()

            self.assertTrue(result.success)
            self.assertIn("末图有", result.message)
            self.assertTrue(progress.state.card_complete(card.card_id))
            self.assertEqual(7, progress.pending_count())

            # A rerun sees the durable verified target and must not select or
            # click it merely because count settlement remains pending.
            selected.clear()
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (card,),
            ):
                resumed = Collector(task, object(), navigator, progress).run()
            self.assertTrue(resumed.success)
            self.assertEqual([], selected)

    def test_enabling_suppression_revisits_completed_battle_maps_only(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [datetime(2026, 10, 6, 12, tzinfo=UTC_PLUS_8)]
            progress = ProgressStore(Path(directory) / "progress.json", lambda: now[0])
            progress.load()
            card = CARD_BY_ID["Q_sp1"]
            for target in card.targets:
                _seed_action_records(progress, card.card_id, target.key, actions=("吸收", "召集"))
                progress.mark_target(card.card_id, target.key, required_actions=("吸收", "召集"))
            progress.mark_card_verified(card.card_id, required_actions=("吸收", "召集"))
            now[0] = now[0].replace(day=7)
            progress.load()
            selected = []
            actions_used = []
            navigator = SimpleNamespace(
                select_collection_card=lambda card_id, **_kwargs: (
                    selected.append(card_id) or CollectionCardSelectionResult(
                        CollectionCardSelectionOutcome.ENTERED,
                        NavigationResult(True, ScreenState.SANDBOX),
                    )
                ),
                current_collection_target=lambda card: card.targets[0].key,
                prepare_collection_main_area=lambda *_args: NavigationResult(
                    True, ScreenState.SANDBOX,
                ),
                advance_collection_map=lambda *_args: NavigationResult(True, ScreenState.SANDBOX),
                open_cartridge_quick_switcher_from_sandbox=lambda **_kwargs: NavigationResult(
                    True, ScreenState.CARD_MENU,
                ),
                inspect_collection_card_completion=lambda *_args: NavigationResult(
                    True, ScreenState.CARD_MENU,
                ),
            )
            collector = Collector(
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                object(), navigator, progress,
            )
            collector._start_search = lambda **_kwargs: SearchCountdownSession((0, 0, 1, 1), 80)

            def use_action(action, *, card_id, map_role):
                if not progress.weekly_action_complete(card_id, map_role, action.name):
                    actions_used.append((map_role.value, action.name))
                    _seed_action_records(progress, card_id, map_role.value, actions=(action.name,))
                return SkillExecutionResult(True)

            collector._open_skill_menu = lambda *_args, **_kwargs: True
            collector._use_action = use_action
            self.assertIsNone(collector.collect_card(card))
            self.assertEqual([card.card_id], selected)
            self.assertEqual(
                [("battle_area_1", "压制"), ("battle_area_2", "压制")], actions_used,
            )
            self.assertEqual(0, collector._completed_this_run)
            self.assertEqual({"吸收": 0, "召集": 0, "压制": 2}, progress.effective_daily_counts())
            self.assertTrue(progress.state.card_verified(card.card_id))
            progress.load()
            self.assertIsNone(collector.collect_card(card))
            self.assertEqual([card.card_id], selected)


    def test_route_starts_from_the_current_first_or_last_map(self):
        for card_id in (
            "Q_sp14", "Q_sp18", *(f"Q_cp{number}" for number in range(1, 8)),
            "Q_ep1", "Q_ep2", "Q_ep3", "Q_ep5", "Q_ep7",
        ):
            card = CARD_BY_ID[card_id]
            for at_last in (False, True):
                with (
                    self.subTest(card_id=card_id, at_last=at_last),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    progress = ProgressStore(Path(directory) / "progress.json")
                    progress.load()
                    actions = []
                    sandbox = NavigationResult(True, ScreenState.SANDBOX)
                    current = card.targets[-1] if at_last else card.targets[0]
                    navigator = SimpleNamespace(
                        current_collection_target=lambda _: current.key,
                        select_collection_card=Mock(
                            return_value=CollectionCardSelectionResult(
                                CollectionCardSelectionOutcome.ENTERED, sandbox
                            )
                        ),
                        prepare_collection_main_area=Mock(
                            side_effect=AssertionError("route already has a confirmed start")
                        ),
                        advance_collection_map=Mock(return_value=sandbox),
                        open_cartridge_quick_switcher_from_sandbox=Mock(return_value=sandbox),
                        inspect_collection_card_completion=Mock(return_value=sandbox),
                    )
                    collector = Collector(
                        SimpleNamespace(config={}, info_set=Mock()), object(), navigator, progress
                    )
                    collector._start_search = Mock(return_value=None)

                    def use(selected, *, card_id, map_role):
                        actions.append((map_role, tuple(action.name for action in selected)))
                        _seed_action_records(progress, card_id, map_role.value)
                        return SkillExecutionResult(True)

                    collector._use_actions = use
                    self.assertIsNone(collector.collect_card(card))
                    order = list(card.targets)
                    if at_last:
                        order = (
                            order[-1:] + order[:-1] if card_id == "Q_cp3" else list(reversed(order))
                        )
                    self.assertEqual([item.role for item in order], [role for role, _ in actions])
                    self.assertEqual(
                        [
                            ("吸收",)
                            if item.role is CollectionMapRole.MAIN_AREA
                            else ("吸收", "召集", "压制")
                            for item in order
                        ],
                        [names for _, names in actions],
                    )
                    self.assertTrue(progress.state.card_verified(card_id))

    def test_resumed_route_passes_through_the_completed_middle_map(self):
        card = CARD_BY_ID["Q_sp14"]
        navigator = SimpleNamespace(
            advance_collection_map=Mock(return_value=NavigationResult(True, ScreenState.SANDBOX))
        )
        collector = Collector(SimpleNamespace(), object(), navigator, SimpleNamespace())
        result = collector._go_to_collection_target(card, card.targets[0], card.targets[-1])
        self.assertTrue(result.success)
        self.assertEqual(
            [
                (card.card_id, card.targets[0], card.targets[1]),
                (card.card_id, card.targets[1], card.targets[2]),
            ],
            [call.args for call in navigator.advance_collection_map.call_args_list],
        )


class CollectorSkillTest(unittest.TestCase):
    def test_missed_press_retries_only_with_a_stable_available_icon_and_unchanged_count(self):
        cases = (
            (None, True, True, True),
            (None, False, True, False),
            (None, True, False, False),
            ("failure", True, True, False),
        )
        for feedback, icon_stable, count_stable, retry in cases:
            with (
                self.subTest(feedback=feedback, icon_stable=icon_stable, count_stable=count_stable),
                tempfile.TemporaryDirectory() as directory,
            ):
                progress = ProgressStore(Path(directory) / "progress.json")
                progress.load()
                frame = np.full((1080, 1920, 3), 100, dtype=np.uint8)
                match = MatchResult(0.98, (100, 100), (40, 40))
                available = ActionIconDetection(
                    ActionIconState.AVAILABLE, match, stable=True, sample_count=2
                )
                post = ActionIconDetection(
                    ActionIconState.AVAILABLE, match, stable=icon_stable, sample_count=2
                )
                used = ActionIconDetection(ActionIconState.USED, match, stable=True, sample_count=2)
                task = SimpleNamespace(config={}, sleep=Mock(), info_set=Mock())
                vision = SimpleNamespace(capture=lambda: frame, click_client=Mock())
                collector = Collector(task, vision, SimpleNamespace(), progress)
                collector._open_skill_menu = Mock(return_value=True)
                collector._detect_action_icon = Mock(
                    side_effect=[
                        (frame, available),
                        (frame, post),
                        (frame, available),
                        (frame, used),
                    ]
                )
                windows = iter(
                    [
                        ((0, 21), True),
                        (None, False),
                        ((0, 21), count_stable),
                        ((0, 21), True),
                        ((1, 21), True),
                    ]
                )

                def read_window(*args, **kwargs):
                    count, stable = next(windows)
                    return CountWindowObservation(count if stable else None, stable)

                collector._read_count_window = read_window
                collector._read_action_feedback = Mock(
                    side_effect=[
                        SkillFeedbackObservation("", feedback),
                        SkillFeedbackObservation("", "success"),
                    ]
                )
                result = collector._use_actions(
                    (ABSORB_ACTION,), card_id="Q_sp1", map_role=CollectionMapRole.MAIN_AREA
                )
                self.assertEqual(retry, result.completed)
                self.assertEqual(2 if retry else 1, vision.click_client.call_count)
                if retry:
                    record = progress.get_action_record(
                        "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收"
                    )
                    self.assertEqual(2, record["attempts"])
                    self.assertEqual("settled", record["state"])

    def test_late_search_countdown_prevents_a_second_press(self):
        collector = Collector(SimpleNamespace(sleep=Mock()), object(), object(), object())
        collector._reuse_recent_search = Mock(return_value=None)
        collector._open_skill_menu = Mock(return_value=True)
        collector._search_running = Mock(side_effect=[False, True])
        collector._start_search = Mock(return_value=SkillExecutionResult(False))
        self.assertIsNone(
            collector._ensure_search(card_id="Q_sp1", map_role=CollectionMapRole.MAIN_AREA)
        )
        collector._start_search.assert_called_once()

    def _skill_collector(self, states, counts):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        statuses = []
        task = SimpleNamespace(
            config={},
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda key, value: statuses.append((key, value)),
        )
        vision = SimpleNamespace(
            click_reference=lambda *_args, **_kwargs: None,
            wait_template=lambda *_args, **_kwargs: MatchResult(
                0.99,
                (100, 100),
                (40, 40),
                pixel_score=0.90,
                zncc_score=0.95,
            ),
            capture=lambda: frame,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
            ocr_text=lambda _frame, name, **_kwargs: "87" if name == "探查倒计时" else "",
            click_client=lambda center, shape, after_sleep=0: clicks.append(
                (center, shape, after_sleep)
            ),
        )
        progress = ProgressStore(
            Path(self.enterContext(tempfile.TemporaryDirectory())) / "progress.json",
            lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
        )
        progress.load()
        collector = Collector(
            task, vision, SimpleNamespace(wait_for_collection_field=lambda frame: frame), progress,
        )
        executed_icons = set()

        def detect(_frame, icon):
            if icon.name in executed_icons:
                state = ActionIconState.ABSENT if icon is SEARCH_ICON else ActionIconState.USED
            else:
                state = states[icon.name]
            return ActionIconDetection(
                state,
                MatchResult(
                    0.98,
                    (100, 100),
                    (40, 40),
                    pixel_score=0.70 if state is ActionIconState.USED else 0.95,
                    zncc_score=0.90,
                ),
                0.65 if state is ActionIconState.USED else 1.0,
                stable=True,
                sample_count=2,
            )

        collector.action_icons = SimpleNamespace(detect=detect)
        count_iters = {name: iter(values) for name, values in counts.items()}
        def read_count_window(action, detection=None, **kwargs):
            with patch.object(
                collector, "_read_count", return_value=next(count_iters[action.name]),
            ):
                return Collector._read_count_window(collector, action, detection, **kwargs)

        collector._read_count_window = read_count_window

        def feedback(action):
            executed_icons.add(action.icon.name)
            was_used = states[action.icon.name] is ActionIconState.USED
            return SkillFeedbackObservation(
                "失败反馈" if was_used else "成功反馈",
                "failure" if was_used else "success",
                1.0,
            )

        collector._read_action_feedback = feedback
        return collector, clicks, statuses, progress

    def test_summon_missing_post_icon_requires_stable_increment_and_success_to_continue(self):
        cases = (
            ((2, 21), True, "success", True),
            ((2, 21), False, "success", False),
            ((1, 21), True, "success", False),
            ((3, 21), True, "success", False),
            ((2, 21), True, "failure", False),
            ((2, 21), True, None, False),
        )
        for after, stable, outcome, expected in cases:
            with self.subTest(after=after, stable=stable, outcome=outcome):
                collector, clicks, statuses, progress = self._skill_collector(
                    {action.icon.name: ActionIconState.AVAILABLE for action in BATTLE_ACTIONS},
                    {
                        "吸收": ((2, 21), (3, 21)),
                        "召集": ((1, 21), after),
                        "压制": ((1, 70), (2, 70)),
                    },
                )
                detect = collector.action_icons.detect

                def detect_post(frame, icon):
                    result = detect(frame, icon)
                    if icon is SUMMON_ICON and result.state is ActionIconState.USED:
                        return ActionIconDetection(ActionIconState.ABSENT, result.match)
                    return result

                collector.action_icons.detect = detect_post
                read_count = collector._read_count_window

                def read_window(action, detection=None, **kwargs):
                    observation = read_count(action, detection, **kwargs)
                    is_stable = (
                        stable if action is SUMMON_ACTION and kwargs.get("allow_single") else True
                    )
                    return CountWindowObservation(observation.count, is_stable)

                collector._read_count_window = read_window
                feedback = collector._read_action_feedback

                def read_feedback(action):
                    result = feedback(action)
                    if action is SUMMON_ACTION:
                        return SkillFeedbackObservation("召集带奖励的战场怪物。", outcome, 1.0)
                    return result

                collector._read_action_feedback = read_feedback
                collector._last_skill_observations["压制"] = {"state": "used"}
                result = collector._use_actions(
                    BATTLE_ACTIONS,
                    card_id="Q_sp1",
                    map_role=CollectionMapRole.BATTLE_AREA_2,
                )
                self.assertEqual(expected, result.completed)
                self.assertEqual(3 if expected else 2, len(clicks))
                self.assertIn(("压制执行反馈", "-"), statuses)
                record = progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "压制"
                )
                if expected:
                    self.assertEqual(CollectionActionState.SETTLED.value, record["state"])
                    for action in BATTLE_ACTIONS:
                        resumed = collector._use_action(
                            action,
                            card_id="Q_sp1",
                            map_role=CollectionMapRole.BATTLE_AREA_2,
                        )
                        self.assertTrue(resumed.completed)
                    self.assertEqual(3, len(clicks))
                else:
                    self.assertIsNone(record)
                    self.assertNotIn("压制", collector._last_skill_observations)
                    if outcome == "failure":
                        self.assertEqual((1, 21), progress.trusted_action_baseline("召集"))
                        self.assertFalse(result.depleted)

    def test_cross_day_resume_only_recognizes_and_clicks_unfinished_action(self):
        collector, clicks, _statuses, progress = self._skill_collector(
            {action.icon.name: ActionIconState.AVAILABLE for action in BATTLE_ACTIONS},
            {"压制": ((0, 60), (1, 60))},
        )
        role = CollectionMapRole.BATTLE_AREA_1
        for action in ("吸收", "召集"):
            progress.arm_action("Q_sp1", role, action, baseline=(0, 21))
            progress.mark_action_clicked("Q_sp1", role, action)
            progress.mark_action_local_done("Q_sp1", role, action)
        progress.now_provider = lambda: datetime(2026, 8, 12, 12, tzinfo=UTC_PLUS_8)
        progress.load()
        with patch.object(collector, "_open_skill_menu", return_value=True) as menu:
            result = collector._use_actions(BATTLE_ACTIONS, card_id="Q_sp1", map_role=role)
        menu.assert_called_once_with((SUPPRESS_ACTION.icon,), allow_group_one_recovery=True)
        self.assertTrue(result.completed)
        self.assertEqual(1, len(clicks))
        self.assertEqual({"吸收": 0, "召集": 0, "压制": 1}, progress.effective_daily_counts())
        self.assertTrue(progress.mark_target("Q_sp1", role.value))

    def test_skill_failure_evidence_is_bounded_and_replayable(self):
        warnings = []
        collector = Collector(
            SimpleNamespace(
                config={},
                log_warning=warnings.append,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector._last_skill_observations["召集"] = {
            "state": "unknown",
            "match": 0.9499,
            "pixel": 0.78,
            "zncc": 0.6387,
            "phase": "battle_area_1",
        }
        for index in range(SKILL_FAILURE_EVIDENCE_LIMIT + 5):
            collector._record_skill_failure(
                f"Q_sp{index + 1}",
                "战斗区域1",
                SkillExecutionResult(False, message="召集图标状态未知"),
            )

        evidence = collector.skill_failure_evidence
        self.assertEqual(SKILL_FAILURE_EVIDENCE_LIMIT, len(evidence))
        self.assertEqual("Q_sp6", evidence[0]["card"])
        self.assertEqual("战斗区域1", evidence[-1]["phase"])
        self.assertEqual(0.6387, evidence[-1]["observations"]["召集"]["zncc"])
        self.assertEqual(SKILL_FAILURE_EVIDENCE_LIMIT + 5, len(warnings))

    def test_action_icon_detection_retries_a_transient_miss(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.969, (1500, 850), (50, 45), 0.87, 0.739),
            1.0,
        )
        sleeps = []
        detections = iter((missing, available))
        task = SimpleNamespace(
            config={},
            sleep=lambda seconds: sleeps.append(seconds),
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(capture=lambda: frame)
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector.action_icons = SimpleNamespace(
            detect=lambda *_args: next(detections),
        )

        selected_frame, selected = collector._detect_action_icon(SUMMON_ICON)

        self.assertIs(frame, selected_frame)
        self.assertIs(available, selected)
        self.assertEqual([ACTION_ICON_DETECTION_INTERVAL], sleeps)

    def test_skill_menu_can_merge_each_icon_from_short_stable_window(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.969, (1500, 850), (50, 45), 0.87, 0.739),
            1.0,
        )
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        sleeps = []
        call_number = [0]
        task = SimpleNamespace(
            config={},
            sleep=lambda seconds: sleeps.append(seconds),
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        vision = SimpleNamespace(capture=lambda: frame)
        collector = Collector(
            task, vision, SimpleNamespace(wait_for_collection_field=lambda frame: frame),
            SimpleNamespace(),
        )

        def detect(_frame, icon):
            frame_number = call_number[0] // 2
            call_number[0] += 1
            if frame_number == 0:
                return available if icon is ABSORB_ICON else missing
            if frame_number == 1:
                return missing if icon is ABSORB_ICON else available
            return missing

        collector.action_icons = SimpleNamespace(detect=detect)

        self.assertTrue(collector._open_skill_menu((ABSORB_ICON, SUMMON_ICON)))
        self.assertEqual(6, call_number[0])
        self.assertEqual(
            [ACTION_ICON_DETECTION_INTERVAL] * 2,
            sleeps,
        )

    def test_action_feedback_region_and_character_threshold_follow_calibration(self):
        self.assertEqual(
            (735 / 1920, 140 / 1080, 1182 / 1920, 270 / 1080),
            ACTION_FEEDBACK_RELATIVE_ROI,
        )
        self.assertEqual(0.80, ACTION_FEEDBACK_CHARACTER_RATIO)
        self.assertEqual(0.40, ACTION_FEEDBACK_RELEVANCE_RATIO)
        self.assertEqual(5.0, ACTION_FEEDBACK_TIMEOUT)
        self.assertEqual(90.0, SEARCH_BUFF_SECONDS)
        self.assertEqual(75.0, SEARCH_REUSE_MAX_AGE_SECONDS)
        self.assertEqual((1535, 960, 82, 60), SEARCH_COUNTDOWN_REFERENCE_ROI)
        self.assertEqual(
            {
                "吸收": (1498, 890, 66, 37),
                "召集": (1542, 790, 66, 33),
                "压制": (1645, 743, 75, 33),
            },
            SKILL_FIXED_COUNT_REFERENCE_ROIS,
        )
        self.assertEqual(
            {
                1: (1671, 1011),
                2: (1749, 1011),
                3: (1824, 1011),
            },
            SKILL_GROUP_REFERENCE_POINTS,
        )
        self.assertEqual(
            {group: (x / 1920, y / 1080) for group, (x, y) in SKILL_GROUP_REFERENCE_POINTS.items()},
            SKILL_GROUP_RELATIVE_POINTS,
        )
        self.assertEqual(0.8, SKILL_GROUP_SWITCH_SETTLE_SECONDS)
        self.assertEqual(
            1.0,
            Collector._feedback_character_ratio(
                "在74秒内确认隐藏物品的位置。",
                "在秒内确认隐藏物品的位置",
            ),
        )
        self.assertGreaterEqual(
            Collector._feedback_character_ratio(
                "周围没有可以吸收的拾取勿。",
                "周围没有可以吸收的拾取物",
            ),
            ACTION_FEEDBACK_CHARACTER_RATIO,
        )
        self.assertLess(
            Collector._feedback_character_ratio(
                "召集带奖励的战场怪物",
                "没有可制伏的怪物",
            ),
            ACTION_FEEDBACK_CHARACTER_RATIO,
        )

    def test_action_feedback_window_matches_absorb_failure(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        sleeps = []
        feedbacks = iter(("", "周围没有可以吸收的拾取物。"))
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda seconds: sleeps.append(seconds),
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda _frame, name, **kwargs: (
                    calls.append((name, kwargs)) or next(feedbacks)
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        feedback = collector._read_action_feedback(ABSORB_ACTION)

        self.assertEqual("failure", feedback.outcome)
        self.assertEqual(1.0, feedback.ratio)
        self.assertEqual(2, len(calls))
        self.assertEqual([ACTION_OCR_WINDOW_INTERVAL], sleeps)
        self.assertTrue(all(name == "吸收执行反馈" for name, _kwargs in calls))
        self.assertTrue(
            all(
                kwargs["relative_roi"] == ACTION_FEEDBACK_RELATIVE_ROI
                and kwargs["target_height"] == 1080
                for _name, kwargs in calls
            )
        )

    def test_suppress_feedback_accepts_video_wording_with_or_without_de(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for text in (
            "已制伏地图内所有怪物。",
            "已制伏地图内所有的怪物。",
        ):
            with self.subTest(text=text):
                collector = Collector(
                    SimpleNamespace(
                        config={},
                        sleep=lambda *_args: None,
                        info_set=lambda *_args: None,
                    ),
                    SimpleNamespace(
                        capture=lambda: frame,
                        ocr_text=lambda *_args, **_kwargs: text,
                        simplify=lambda value: value,
                    ),
                    SimpleNamespace(),
                    SimpleNamespace(),
                )

                feedback = collector._read_action_feedback(SUPPRESS_ACTION)

                self.assertEqual("success", feedback.outcome)

    def test_absorb_failure_feedback_has_precedence_over_positive_text(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None, info_set=lambda *_args: None),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda *_args, **_kwargs: "吸收周围的拾取物 周围没有可以吸收的拾取物",
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        feedback = collector._read_action_feedback(ABSORB_ACTION)

        self.assertEqual("failure", feedback.outcome)

    def test_action_feedback_window_runs_until_timeout_when_ocr_stays_empty(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        sleeps = []
        clock = [0.0]
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda seconds: (
                    sleeps.append(seconds),
                    clock.__setitem__(0, clock[0] + seconds),
                ),
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda _frame, name, **kwargs: calls.append((name, kwargs)) or "",
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        with patch(
            "src.tasks.map_trade.collector_skills.monotonic",
            side_effect=lambda: clock[0],
        ):
            feedback = collector._read_action_feedback(SEARCH_ACTION)

        self.assertIsNone(feedback.outcome)
        self.assertEqual(int(ACTION_FEEDBACK_TIMEOUT / ACTION_OCR_WINDOW_INTERVAL) + 1, len(calls))
        self.assertEqual(ACTION_FEEDBACK_TIMEOUT, sum(sleeps))
        self.assertEqual(ACTION_FEEDBACK_TIMEOUT, clock[0])

    def test_absorb_feedback_polls_past_foreign_banner_until_own_token(self):
        """探查横幅先出现时不得提前收工，须轮询到吸收横幅（RPT-20260906-170401）。"""

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        sleeps = []
        clock = [0.0]
        texts = iter(
            (
                "在90秒内确认隐藏物品的位置。",
                "在90秒内确认隐藏物品的位置。",
                "吸收周围的拾取物。",
            )
        )
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda seconds: (
                    sleeps.append(seconds),
                    clock.__setitem__(0, clock[0] + seconds),
                ),
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda _frame, name, **kwargs: (
                    calls.append((name, kwargs)) or next(texts)
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        with patch(
            "src.tasks.map_trade.collector_skills.monotonic",
            side_effect=lambda: clock[0],
        ):
            feedback = collector._read_action_feedback(ABSORB_ACTION)

        self.assertEqual("success", feedback.outcome)
        self.assertGreaterEqual(feedback.ratio, ACTION_FEEDBACK_CHARACTER_RATIO)
        self.assertEqual(3, len(calls))

    def test_absorb_feedback_relevant_garbled_text_still_stops_early(self):
        """相关性门只挡无关横幅：与吸收文案重合足够高的文本仍立即接受。"""

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                ocr_text=lambda _frame, name, **kwargs: (
                    calls.append((name, kwargs)) or "吸收拾取物"
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )

        feedback = collector._read_action_feedback(ABSORB_ACTION)

        self.assertEqual(1, len(calls))
        self.assertIsNone(feedback.outcome)
        self.assertEqual("吸收拾取物", feedback.text)
        self.assertGreaterEqual(feedback.ratio, ACTION_FEEDBACK_RELEVANCE_RATIO)

    def test_absorb_used_icon_and_count_increment_settle_despite_polluted_feedback(self):
        """图标已使用且次数稳定递增时，探查横幅噪声不影响吸收结果。"""

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        clock = [0.0]
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1509, 861), (44, 38), pixel_score=0.95, zncc_score=0.90),
            1.0,
            stable=True,
            sample_count=2,
        )
        used = ActionIconDetection(
            ActionIconState.USED,
            MatchResult(0.98, (1509, 861), (44, 38), pixel_score=0.70, zncc_score=0.90),
            0.65,
            stable=True,
            sample_count=2,
        )
        detections = iter((available, used))
        count_values = iter(((0, 21), (1, 21)))
        task = SimpleNamespace(
            config={},
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **_kwargs: "在90秒内确认隐藏物品的位置。",
            click_client=lambda center, shape, after_sleep=0: clicks.append(center),
        )
        progress = ProgressStore(
            Path(self.enterContext(tempfile.TemporaryDirectory())) / "progress.json",
            lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
        )
        progress.load()
        collector = Collector(task, vision, SimpleNamespace(), progress)
        collector.action_icons = SimpleNamespace(detect=lambda _frame, _icon: next(detections))

        def read_count(action, _detection=None, **_kwargs):
            return CountWindowObservation(next(count_values), True)

        collector._read_count_window = read_count

        with patch(
            "src.tasks.map_trade.collector_skills.monotonic",
            side_effect=lambda: clock[0],
        ):
            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp2",
                map_role=CollectionMapRole.MAIN_AREA,
            )

        self.assertTrue(result.completed)
        self.assertEqual("", result.message)
        self.assertEqual(1, len(clicks))
        record = progress.get_action_record(
            "Q_sp2", CollectionMapRole.MAIN_AREA, "吸收"
        )
        self.assertEqual(CollectionActionState.SETTLED.value, record["state"])

    def test_stable_single_increment_confirms_event_action_without_icon_dimming(self):
        for increment, stable, outcome, expected in (
            (1, True, None, True), (2, True, None, False),
            (1, False, None, False), (1, True, "failure", False),
        ):
            with self.subTest(increment=increment, stable=stable, outcome=outcome):
                with tempfile.TemporaryDirectory() as directory:
                    progress = ProgressStore(
                        Path(directory) / "progress.json",
                        lambda: datetime(2026, 10, 13, 12, tzinfo=UTC_PLUS_8),
                    )
                    progress.load()
                    available = ActionIconDetection(
                        ActionIconState.AVAILABLE, MatchResult(0.98, (1509, 861), (44, 38)),
                        stable=True, sample_count=2,
                    )
                    collector = Collector(
                        SimpleNamespace(
                            config={}, info_set=Mock(), log_warning=Mock(), sleep=Mock(),
                        ),
                        object(), object(), progress,
                    )
                    collector._read_count = (
                        Mock(return_value=(increment, 30)) if stable
                        else Mock(side_effect=cycle(((increment, 30), None, None)))
                    )
                    progress.arm_action("Q_ep3", CollectionMapRole.MAIN_AREA,
                                        "吸收", baseline=(0, 30))
                    progress.mark_action_clicked("Q_ep3", CollectionMapRole.MAIN_AREA, "吸收")
                    result = collector._finish_after_click(
                        ABSORB_ACTION, card_id="Q_ep3", map_role=CollectionMapRole.MAIN_AREA,
                        before=(0, 30), detection=available, post_detection=available,
                        feedback=SkillFeedbackObservation("", outcome, 0),
                    )
                    self.assertEqual(expected, result.completed)
                    self.assertEqual(int(expected), progress.state.daily_absorbs)

    def test_absorb_failure_feedback_vetoes_used_post_icon(self):
        """失败文案否决仍最高优先：failure+post used → blocked，不得转成功。"""

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1509, 861), (44, 38), pixel_score=0.95, zncc_score=0.90),
            1.0,
            stable=True,
            sample_count=2,
        )
        used = ActionIconDetection(
            ActionIconState.USED,
            MatchResult(0.98, (1509, 861), (44, 38), pixel_score=0.70, zncc_score=0.90),
            0.65,
            stable=True,
            sample_count=2,
        )
        progress = ProgressStore(
            Path(self.enterContext(tempfile.TemporaryDirectory())) / "progress.json",
            lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
        )
        progress.load()
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(capture=lambda: frame),
            SimpleNamespace(),
            progress,
        )
        collector._read_count_window = Mock(return_value=CountWindowObservation((0, 21), True))
        progress.arm_action("Q_sp2", CollectionMapRole.MAIN_AREA, "吸收")
        progress.mark_action_clicked("Q_sp2", CollectionMapRole.MAIN_AREA, "吸收")

        result = collector._finish_after_click(
            ABSORB_ACTION,
            card_id="Q_sp2",
            map_role=CollectionMapRole.MAIN_AREA,
            before=(0, 21),
            detection=available,
            post_detection=used,
            feedback=SkillFeedbackObservation(
                "周围没有可以吸收的拾取物。", "failure", 1.0
            ),
        )

        self.assertFalse(result.completed)
        self.assertIn("明确失败", result.message)
        record = progress.get_action_record(
            "Q_sp2", CollectionMapRole.MAIN_AREA, "吸收"
        )
        self.assertEqual(CollectionActionState.BLOCKED.value, record["state"])

    def test_search_reuses_recent_ledger_cast_when_icon_unreadable(self):
        """重进卡带：账本有近期施放且探查图标被倒计时覆盖 → 不点击直接复用。"""

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                click_client=lambda center, shape, after_sleep=0: clicks.append(center),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        unknown = ActionIconDetection(
            ActionIconState.UNKNOWN,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        collector.action_icons = SimpleNamespace(detect=lambda _frame, _icon: unknown)
        collector._search_cast_monotonic[
            ("Q_sp2", CollectionMapRole.MAIN_AREA.value)
        ] = 990.0

        with patch(
            "src.tasks.map_trade.collector_skills.monotonic",
            side_effect=lambda: 1000.0,
        ):
            result = collector._start_search(
                map_role=CollectionMapRole.MAIN_AREA,
                card_id="Q_sp2",
            )

        self.assertIsInstance(result, SearchCountdownSession)
        self.assertEqual(SEARCH_COUNTDOWN_RELATIVE_ROI, result.relative_roi)
        self.assertGreaterEqual(result.value, 1)
        self.assertEqual([], clicks)
        self.assertIn(
            ("Q_sp2", CollectionMapRole.MAIN_AREA.value),
            collector._search_cast_monotonic,
        )

    def test_search_cast_records_ledger_for_later_reuse(self):
        """正常施放成功后写入账本，供同卡带失败重进时复用。"""

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1550, 969), (52, 44), pixel_score=0.95, zncc_score=0.90),
            1.0,
            stable=True,
            sample_count=2,
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda *_args, **_kwargs: None,
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **_kwargs: (
                "在44秒内确认隐藏物品的位置。" if name == "探查执行反馈" else "44"
            ),
            click_client=lambda center, shape, after_sleep=0: clicks.append(center),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector._open_skill_menu = lambda *_args, **_kwargs: True
        collector.action_icons = SimpleNamespace(detect=lambda *_args: available)

        result = collector._start_search(
            map_role=CollectionMapRole.MAIN_AREA,
            card_id="Q_sp2",
        )

        self.assertIsInstance(result, SearchCountdownSession)
        self.assertEqual(1, len(clicks))
        self.assertIn(
            ("Q_sp2", CollectionMapRole.MAIN_AREA.value),
            collector._search_cast_monotonic,
        )

    def test_start_search_without_ledger_still_requires_skill_bar(self):
        """无账本记录时保持原判定：技能栏未确认 → 原失败文案。"""

        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector._open_skill_menu = lambda *_args, **_kwargs: False

        result = collector._start_search(
            map_role=CollectionMapRole.MAIN_AREA,
            card_id="Q_sp2",
        )

        self.assertIsInstance(result, SkillExecutionResult)
        self.assertIn("未确认安全区技能栏", result.message)

    def test_search_waits_after_feedback_match_before_countdown(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        sleeps = []
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1550, 969), (52, 44)),
        )
        absent = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        detections = iter((available, available, absent))
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda seconds: sleeps.append(seconds),
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: (
                "在44秒内确认隐藏物品的位置。" if name == "探查执行反馈" else "44"
            ),
            click_client=lambda *_args, **_kwargs: None,
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector._open_skill_menu = lambda *_args, **_kwargs: True
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))

        result = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)

        self.assertIsInstance(result, SearchCountdownSession)
        self.assertEqual(
            [ACTION_ICON_DETECTION_INTERVAL, ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS],
            sleeps,
        )

    def test_search_countdown_uses_manual_region_after_icon_match(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        clicks = []
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1550, 969), (52, 44), 0.95, 0.92),
            1.0,
        )
        absent = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        detections = iter((available, available, absent))
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: frame,
                click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ocr_text=lambda _frame, name, **kwargs: (
                    ocr_calls.append((name, kwargs))
                    or ("在48秒内确认隐藏物品的位置。" if name == "探查执行反馈" else "48")
                ),
            ),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector._open_skill_menu = lambda *_args, **_kwargs: True
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))

        result = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)

        self.assertEqual(SEARCH_COUNTDOWN_RELATIVE_ROI, result.relative_roi)
        self.assertEqual(available.match.center, clicks[0][0][0])
        countdown_kwargs = next(kwargs for name, kwargs in ocr_calls if name == "探查倒计时")
        self.assertEqual(SEARCH_COUNTDOWN_RELATIVE_ROI, countdown_kwargs["relative_roi"])
        self.assertEqual(SKILL_OCR_UPSCALE, countdown_kwargs["ocr_scale"])

    def test_dimmed_absorb_and_summon_are_preexisting_used_without_click(self):
        collector, clicks, _statuses, _progress = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.USED,
                "召集": ActionIconState.USED,
            },
            {
                "探查": ((1, 40), (2, 40)),
                "吸收": ((2, 21), (2, 21)),
                "召集": ((1, 21), (1, 21)),
            },
        )

        result = collector._use_actions(
            (ABSORB_ACTION, SUMMON_ACTION),
            card_id="Q_sp1",
            map_role=CollectionMapRole.MAIN_AREA,
        )

        self.assertTrue(result.completed)
        self.assertFalse(result.depleted)
        self.assertEqual(0, len(clicks))
        self.assertEqual((), result.pending_actions)

    def test_skill_ocr_failure_is_not_reported_as_completed(self):
        collector, clicks, _statuses, _progress = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {"吸收": (None,)},
        )

        search = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)
        self.assertIsInstance(search, SearchCountdownSession)
        result = collector._use_actions(
            (ABSORB_ACTION, SUMMON_ACTION),
            card_id="Q_sp1",
            map_role=CollectionMapRole.MAIN_AREA,
        )

        self.assertFalse(result.completed)
        self.assertFalse(result.depleted)
        self.assertIn("未取得可信次数", result.message)
        self.assertEqual(1, len(clicks))

    def test_pre_exhausted_available_skill_does_not_complete_current_map(self):
        collector, clicks, _statuses, _progress = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {"吸收": ((21, 21),)},
        )

        search = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)
        self.assertIsInstance(search, SearchCountdownSession)
        result = collector._use_actions(
            (ABSORB_ACTION, SUMMON_ACTION),
            card_id="Q_sp1",
            map_role=CollectionMapRole.MAIN_AREA,
        )

        self.assertFalse(result.completed)
        self.assertTrue(result.depleted)
        self.assertEqual(1, len(clicks))

    def test_mid_sequence_exhaustion_preserves_first_skill_and_stops_before_second_click(self):
        collector, clicks, _statuses, progress = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {
                "吸收": ((0, 21), (1, 21)),
                "召集": ((21, 21),),
            },
        )

        search = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)
        self.assertIsInstance(search, SearchCountdownSession)
        result = collector._use_actions(
            (ABSORB_ACTION, SUMMON_ACTION),
            card_id="Q_sp1",
            map_role=CollectionMapRole.MAIN_AREA,
        )

        self.assertFalse(result.completed)
        self.assertTrue(result.depleted)
        self.assertEqual(2, len(clicks))
        self.assertTrue(progress.weekly_action_complete("Q_sp1", "main_area", "吸收"))
        self.assertIsNone(progress.get_action_record("Q_sp1", "main_area", "召集"))

    def test_all_three_completed_can_report_depleted_after_completion(self):
        collector, clicks, _statuses, _progress = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {
                "吸收": ((20, 21), (21, 21)),
                "召集": ((20, 21), (21, 21)),
            },
        )

        search = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)
        self.assertIsInstance(search, SearchCountdownSession)
        result = collector._use_actions(
            (ABSORB_ACTION, SUMMON_ACTION),
            card_id="Q_sp1",
            map_role=CollectionMapRole.MAIN_AREA,
        )

        self.assertTrue(result.completed)
        self.assertTrue(result.depleted)
        self.assertEqual(3, len(clicks))

    def test_failure_after_last_absorb_quota_remains_failure(self):
        collector, clicks, _statuses, progress = self._skill_collector(
            {"吸收": ActionIconState.AVAILABLE, "召集": ActionIconState.AVAILABLE},
            {"吸收": ((20, 21), (21, 21)), "召集": (None,)},
        )
        result = collector._use_actions(
            (ABSORB_ACTION, SUMMON_ACTION), card_id="Q_sp1",
            map_role=CollectionMapRole.BATTLE_AREA_1,
        )
        self.assertFalse(result.completed)
        self.assertFalse(result.depleted)
        self.assertFalse(collector._skill_failure("Q_sp1", "战斗区域1", result).success)
        self.assertTrue(progress.weekly_action_complete("Q_sp1", "battle_area_1", "吸收"))
        self.assertEqual(1, len(clicks))

    def test_battle_flow_executes_absorb_summon_and_suppression(self):
        collector, clicks, _statuses, _progress = self._skill_collector(
            {
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
                "制服": ActionIconState.AVAILABLE,
            },
            {
                "吸收": ((4, 21), (5, 21)),
                "召集": ((2, 21), (3, 21)),
                "压制": ((6, 60), (7, 60)),
            },
        )

        result = collector._use_actions(
            BATTLE_ACTIONS,
            card_id="Q_sp1",
            map_role=CollectionMapRole.BATTLE_AREA_1,
        )

        self.assertTrue(result.completed)
        self.assertFalse(result.depleted)
        self.assertEqual(3, len(clicks))

    def test_event_and_character_battles_suppress_after_resources_are_used(self):
        for card_id in ("Q_ep5", "Q_ep7", "Q_cp7"):
            with self.subTest(card=card_id):
                collector, clicks, _statuses, progress = self._skill_collector(
                    {"吸收": ActionIconState.USED, "召集": ActionIconState.USED,
                     "制服": ActionIconState.AVAILABLE},
                    {"吸收": ((4, 21), (4, 21)), "召集": ((2, 21), (2, 21)),
                     "压制": ((6, 60), (7, 60))},
                )
                result = collector._use_actions(
                    BATTLE_ACTIONS, card_id=card_id, map_role=CollectionMapRole.BATTLE_AREA_1,
                )
                self.assertTrue(result.completed)
                self.assertEqual(1, len(clicks))
                self.assertTrue(progress.weekly_action_complete(card_id, "battle_area_1", "压制"))

    def test_suppression_count_roi_uses_manual_fixed_region(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = []
        detection = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1440, 700), (80, 80), 0.95, 0.92),
            0.95,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: calls.append((name, kwargs)) or "0/75",
        )
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )

        count = collector._read_count(BATTLE_ACTIONS[-1], detection)

        self.assertEqual((0, 75), count)
        self.assertEqual("压制次数", calls[0][0])
        self.assertNotIn("roi", calls[0][1])
        self.assertEqual(1080, calls[0][1]["target_height"])
        self.assertEqual(
            BATTLE_ACTIONS[-1].fixed_count_relative_roi,
            calls[0][1]["relative_roi"],
        )
        self.assertEqual(SKILL_OCR_UPSCALE, calls[0][1]["ocr_scale"])

    def test_post_click_count_ocr_uses_manual_region_after_icon_refresh(self):
        before_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        after_frame = np.ones((1080, 1920, 3), dtype=np.uint8)
        before = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
            0.95,
        )
        after = ActionIconDetection(
            ActionIconState.USED,
            MatchResult(0.98, (960, 420), (52, 48), 0.95, 0.92),
            0.65,
        )
        frames = iter((before_frame, before_frame, after_frame, after_frame))
        detections = iter((before, before, after, after))
        count_detections = []
        clicks = []
        progress = ProgressStore(
            Path(self.enterContext(tempfile.TemporaryDirectory())) / "progress.json",
            lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
        )
        progress.load()
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                info_set=lambda *_args: None,
            ),
            SimpleNamespace(
                capture=lambda: next(frames),
                click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
            ),
            SimpleNamespace(),
            progress,
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
        counts = iter(((0, 21), (1, 21)))
        def read_count_window(_action, detection, **_kwargs):
            count_detections.append(detection)
            return CountWindowObservation(next(counts), True)

        collector._read_count_window = read_count_window
        collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
            "吸收周围的拾取物",
            "success",
            1.0,
        )

        result = collector._use_action(
            ABSORB_ACTION,
            card_id="Q_sp1",
            map_role=CollectionMapRole.MAIN_AREA,
        )

        self.assertTrue(result.completed)
        self.assertEqual(
            [before.state, after.state],
            [value.state for value in count_detections],
        )
        self.assertTrue(all(value.stable for value in count_detections))
        self.assertEqual(
            [before.match.center, after.match.center],
            [value.match.center for value in count_detections],
        )
        self.assertEqual(
            (before.match.center, before_frame.shape, 0.0),
            (clicks[0][0][0], clicks[0][0][1], clicks[0][1]["after_sleep"]),
        )

    def test_absorb_and_summon_count_rois_use_manual_fixed_regions(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        calls = []
        detection = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
            0.95,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: calls.append((name, kwargs)) or "7/21",
        )
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )

        for action in (ABSORB_ACTION, SUMMON_ACTION):
            with self.subTest(action=action.name):
                self.assertEqual((7, 21), collector._read_count(action, detection))

        self.assertEqual(2, len(calls))
        for name, kwargs in calls:
            self.assertIn(name, {"吸收次数", "召集次数"})
            self.assertNotIn("roi", kwargs)
            self.assertEqual(1080, kwargs["target_height"])
            self.assertEqual(
                next(
                    action.fixed_count_relative_roi
                    for action in (ABSORB_ACTION, SUMMON_ACTION)
                    if f"{action.name}次数" == name
                ),
                kwargs["relative_roi"],
            )
            self.assertEqual(SKILL_OCR_UPSCALE, kwargs["ocr_scale"])

    def test_search_start_confirms_countdown_in_fixed_roi(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        vision = SimpleNamespace(
            capture=lambda: frame,
            click_client=Mock(),
            ocr_text=lambda _frame, name, **kwargs: ocr_calls.append((name, kwargs)) or "44",
        )
        collector = Collector(
            SimpleNamespace(
                config={},
                info_set=Mock(),
                sleep=lambda *_args: None,
                log_warning=lambda *_args: None,
            ),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector._open_skill_menu = Mock(return_value=True)
        collector._detect_action_icon = Mock(return_value=(frame, ActionIconDetection(
            ActionIconState.AVAILABLE, MatchResult(0.99, (900, 420), (44, 43)),
            stable=True, sample_count=2,
        )))
        collector._read_action_feedback = Mock(return_value=SkillFeedbackObservation(
            "探查", "success", 1.0,
        ))
        session = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)
        self.assertIsInstance(session, SearchCountdownSession)
        self.assertEqual(44, session.value)
        vision.click_client.assert_called_once()
        self.assertEqual(
            [
                (
                    "探查倒计时",
                    {
                        "relative_roi": session.relative_roi,
                        "target_height": 1080,
                        "ocr_scale": SKILL_OCR_UPSCALE,
                    },
                )
            ],
            ocr_calls,
        )

    def test_skill_menu_recovery_clicks_group_one_and_retries_icon_detection(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        group_clicks = []
        action_clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        available = ActionIconDetection(
            ActionIconState.AVAILABLE,
            MatchResult(0.98, (1550, 969), (52, 44), 0.95, 0.92),
            1.0,
        )
        detections = iter(
            [missing] * 8 + [available, available, available, available, missing]
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: group_clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: (
                "在44秒内确认隐藏物品的位置。" if name == "探查执行反馈" else "44"
            ),
            click_client=lambda center, shape, after_sleep=0: action_clicks.append(
                (center, shape, after_sleep)
            ),
            match=lambda *_args: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        collector = Collector(
            task, vision, SimpleNamespace(wait_for_collection_field=lambda frame: frame),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))

        result = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)

        self.assertIsInstance(result, SearchCountdownSession)
        self.assertEqual(
            [
                (
                    SKILL_GROUP_RELATIVE_POINTS[1][0],
                    SKILL_GROUP_RELATIVE_POINTS[1][1],
                    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
                )
            ],
            group_clicks,
        )
        self.assertEqual([(available.match.center, frame.shape, 0.0)], action_clicks)
        self.assertEqual(SEARCH_COUNTDOWN_RELATIVE_ROI, result.relative_roi)

    def test_skill_menu_rechecks_restored_field_before_clicking_a_group(self):
        field = np.full((8, 8, 3), 100, dtype=np.uint8)
        battle = np.full_like(field, 120)
        restored = np.full_like(field, 140)
        missing = ActionIconDetection(
            ActionIconState.ABSENT, MatchResult(-1.0, (0, 0), (0, 0)),
        )
        available = ActionIconDetection(
            ActionIconState.AVAILABLE, MatchResult(0.98, (1550, 969), (52, 44)),
        )
        for initial_battle in (True, False):
            with self.subTest(initial_battle=initial_battle):
                current = [battle if initial_battle else field]
                missing_reads = [0]
                task = SimpleNamespace(
                    config={}, sleep=Mock(), info_set=Mock(), log_warning=Mock(),
                    operate_click=Mock(),
                )
                vision = SimpleNamespace(capture=lambda: current[0], click_client=Mock())

                def wait_for_field(frame):
                    if frame is battle:
                        current[0] = restored
                        return restored
                    return frame

                def detect(frame, _icon):
                    self.assertIsNot(frame, battle)
                    if frame is restored:
                        return available
                    missing_reads[0] += 1
                    if missing_reads[0] == 3:
                        current[0] = battle
                    return missing

                navigator = SimpleNamespace(
                    wait_for_collection_field=Mock(side_effect=wait_for_field),
                )
                collector = Collector(task, vision, navigator, SimpleNamespace())
                collector.action_icons = SimpleNamespace(detect=detect)
                collector._counts_prove_menu = Mock(return_value=False)

                self.assertTrue(collector._open_skill_menu(
                    (ABSORB_ICON,), allow_group_one_recovery=True,
                ))
                task.operate_click.assert_not_called()
                vision.click_client.assert_not_called()
                self.assertIs(restored, current[0])

    def test_skill_menu_stops_without_clicks_when_field_recovery_times_out(self):
        task = SimpleNamespace(config={}, operate_click=Mock())
        vision = SimpleNamespace(capture=Mock(return_value=object()), click_client=Mock())
        navigator = SimpleNamespace(
            wait_for_collection_field=Mock(side_effect=RuntimeError("遭遇战恢复超时")),
        )
        collector = Collector(task, vision, navigator, SimpleNamespace())
        collector.action_icons = SimpleNamespace(detect=Mock())

        with self.assertRaisesRegex(RuntimeError, "遭遇战恢复超时"):
            collector._open_skill_menu((ABSORB_ICON,), allow_group_one_recovery=True)

        task.operate_click.assert_not_called()
        vision.click_client.assert_not_called()
        collector.action_icons.detect.assert_not_called()

    def test_skill_menu_recovery_stops_after_all_three_groups(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
        )
        collector = Collector(
            task,
            vision,
            SimpleNamespace(wait_for_collection_field=lambda frame: frame),
            SimpleNamespace(weekly_action_complete=lambda *_args: False),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._use_actions(
            (ABSORB_ACTION,),
            card_id="Q_sp1",
            map_role=CollectionMapRole.BATTLE_AREA_1,
        )

        self.assertEqual(
            [
                (*point, SKILL_GROUP_SWITCH_SETTLE_SECONDS)
                for point in SKILL_GROUP_RELATIVE_POINTS.values()
            ],
            clicks,
        )
        self.assertFalse(result.completed)
        self.assertIn("未确认采集技能栏", result.message)

    def test_skill_menu_rechecks_all_groups_after_the_layout_changes(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        warnings = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=warnings.append,
            info_set=lambda *_args: None,
        )
        collector = Collector(
            task,
            SimpleNamespace(capture=lambda: frame),
            SimpleNamespace(wait_for_collection_field=lambda frame: frame),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        self.assertFalse(
            collector._open_skill_menu(
                (ABSORB_ICON,),
                allow_group_one_recovery=True,
            )
        )
        self.assertFalse(
            collector._open_skill_menu(
                (ABSORB_ICON,),
                allow_group_one_recovery=True,
            )
        )
        self.assertEqual(6, len(clicks))
        self.assertTrue(all("三个技能组" in value for value in warnings))

    def test_start_search_stops_when_no_group_has_the_skills(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        collector = Collector(
            task,
            SimpleNamespace(capture=lambda: frame),
            SimpleNamespace(wait_for_collection_field=lambda frame: frame),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)

        self.assertFalse(result.completed)
        self.assertEqual(3, len(clicks))
        self.assertIn("未确认安全区技能栏", result.message)

    def test_missing_action_after_menu_confirmation_never_uses_fixed_action_point(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        missing = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
        )
        task = SimpleNamespace(
            config={},
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=lambda *_args: None,
            log_warning=lambda *_args: None,
            info_set=lambda *_args: None,
        )
        progress = ProgressStore(
            Path(self.enterContext(tempfile.TemporaryDirectory())) / "progress.json",
            lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
        )
        progress.load()
        collector = Collector(
            task,
            SimpleNamespace(capture=lambda: frame),
            SimpleNamespace(),
            progress,
        )
        collector._open_skill_menu = lambda *_args, **_kwargs: True
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._use_action(
            ABSORB_ACTION,
            card_id="Q_sp1",
            map_role=CollectionMapRole.BATTLE_AREA_1,
        )

        self.assertFalse(result.completed)
        self.assertIn("未识别到吸收图标", result.message)
        self.assertEqual([], clicks)

    def test_fixed_count_ocr_uses_immediate_three_x_fallback(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for action in BATTLE_ACTIONS:
            limit = 75 if action is SUPPRESS_ACTION else 30
            for invalid in ("2", "4/3", "1/0"):
                for recovered in (False, True):
                    with self.subTest(action=action.name, invalid=invalid, recovered=recovered):
                        scales = []
                        texts = iter((invalid, f"1/{limit}" if recovered else invalid))
                        collector = Collector(
                            SimpleNamespace(config={}, sleep=lambda *_args: None),
                            SimpleNamespace(
                                capture=lambda: frame,
                                ocr_text=lambda _frame, _name, **kwargs: (
                                    scales.append(kwargs["ocr_scale"]) or next(texts)
                                ),
                            ),
                            SimpleNamespace(),
                            SimpleNamespace(),
                        )
                        self.assertEqual(
                            (1, limit) if recovered else None,
                            collector._read_count(action),
                        )
                        self.assertEqual(
                            [SKILL_OCR_UPSCALE, SKILL_OCR_FALLBACK_UPSCALE],
                            scales,
                        )

    def test_preclick_requires_stable_hud_count_and_persists_its_limit(self):
        for action, limit in zip(BATTLE_ACTIONS, (30, 28, 75), strict=True):
            for scenario in ("stable", "unstable", "conflict", "cross_scale_conflict"):
                with (
                    self.subTest(action=action.name, scenario=scenario),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    progress = ProgressStore(Path(directory) / "progress.json")
                    progress.load()
                    if scenario == "conflict":
                        progress.reconcile_pending(action.name, (0, limit - 5))
                        progress.arm_action(
                            "Q_sp2", "battle_area_1", action.name, baseline=(0, limit - 5),
                        )
                    texts = cycle(
                        (f"0/{limit}",) * 3 if scenario != "unstable"
                        else (f"0/{limit}", f"0/{limit + 1}", f"0/{limit + 2}") * 2
                    )
                    collector = Collector(
                        SimpleNamespace(config={}, sleep=lambda *_: None, info_set=lambda *_: None),
                        SimpleNamespace(
                            capture=lambda: None,
                            ocr_text=lambda *_, **kwargs: (
                                f"0/{limit + 100}"
                                if scenario == "cross_scale_conflict"
                                and kwargs["ocr_scale"] == SKILL_OCR_FALLBACK_UPSCALE
                                else next(texts)
                            ),
                        ),
                        SimpleNamespace(),
                        progress,
                    )
                    before, failure = collector._prepare_before_click(
                        action,
                        SimpleNamespace(state=ActionIconState.AVAILABLE),
                        card_id="Q_sp1",
                        map_role=CollectionMapRole.BATTLE_AREA_1,
                    )
                    progress.load()
                    record = progress.get_action_record("Q_sp1", "battle_area_1", action.name)
                    if scenario == "stable":
                        self.assertIsNone(failure)
                        self.assertEqual((0, limit), before)
                        self.assertEqual((0, limit), progress.trusted_action_baseline(action.name))
                        self.assertEqual([0, limit], record["baseline"])
                        self.assertEqual(CollectionActionState.ARMED.value, record["state"])
                    else:
                        self.assertIsNone(before)
                        self.assertFalse(failure.completed)
                        self.assertIsNone(record)
                        self.assertEqual(
                            limit - 5 if scenario == "conflict" else None,
                            progress.action_limit(action.name),
                        )

    def test_count_limit_conflict_rereads_before_arming_a_new_action(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json")
            progress.load()
            progress.reconcile_pending("吸收", (7, 21))
            scales = []

            def ocr_text(*_args, **kwargs):
                scale = kwargs["ocr_scale"]
                scales.append(scale)
                return "7/121" if scale == SKILL_OCR_UPSCALE else "7/21"

            collector = Collector(
                SimpleNamespace(config={}, sleep=Mock(), info_set=Mock()),
                SimpleNamespace(capture=lambda: None, ocr_text=ocr_text),
                SimpleNamespace(), progress,
            )
            before, failure = collector._prepare_before_click(
                ABSORB_ACTION, SimpleNamespace(state=ActionIconState.AVAILABLE),
                card_id="Q_cp5", map_role=CollectionMapRole.MAIN_AREA,
            )
            self.assertIsNone(failure)
            self.assertEqual((7, 21), before)
            self.assertIn(SKILL_OCR_FALLBACK_UPSCALE, scales)
            record = progress.get_action_record("Q_cp5", "main_area", "吸收")
            self.assertEqual([7, 21], record["baseline"])
            self.assertEqual((7, 21), progress.trusted_action_baseline("吸收"))

    def test_transient_used_after_click_keeps_intent_unresolved(self):
        collector, clicks, _statuses, progress = self._skill_collector(
            {ABSORB_ACTION.icon.name: ActionIconState.AVAILABLE},
            {"吸收": ((0, 21), None)},
        )
        frame = collector.vision.capture()
        available = collector.action_icons.detect(frame, ABSORB_ACTION.icon)
        used = ActionIconDetection(ActionIconState.USED, available.match)
        missing = ActionIconDetection(ActionIconState.ABSENT, available.match)
        detections = iter((available, used, missing, used))
        collector.action_icons.detect = lambda *_args: next(detections)
        collector._read_action_feedback = lambda _action: SkillFeedbackObservation("", None)

        result = collector._use_action(
            ABSORB_ACTION, card_id="Q_sp1", map_role=CollectionMapRole.MAIN_AREA,
        )

        self.assertFalse(result.completed)
        self.assertEqual(1, len(clicks))
        self.assertFalse(progress.weekly_action_complete("Q_sp1", "main_area", "吸收"))
        self.assertEqual(0, progress.state.daily_absorbs)
        self.assertEqual(
            CollectionActionState.CLICKED.value,
            progress.get_action_record("Q_sp1", "main_area", "吸收")["state"],
        )
        progress.load()
        collector.action_icons.detect = lambda *_args: available
        resumed = collector._use_action(
            ABSORB_ACTION, card_id="Q_sp1", map_role=CollectionMapRole.MAIN_AREA,
        )
        self.assertFalse(resumed.completed)
        self.assertEqual(1, len(clicks))

    def test_formal_bare_post_count_keeps_local_success_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            clicks = []
            detections = iter(
                (
                    ActionIconDetection(
                        ActionIconState.AVAILABLE,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.95,
                    ),
                    ActionIconDetection(
                        ActionIconState.AVAILABLE,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.95,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                )
            )
            task = SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                info_set=lambda *_args: None,
                log_warning=lambda *_args: None,
            )
            vision = SimpleNamespace(
                capture=lambda: frame,
                click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
            )
            collector = Collector(task, vision, SimpleNamespace(), progress)
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((0, 21), None))
            def read_count_window(action, detection, **_kwargs):
                count = next(count_values)
                return CountWindowObservation(count, count is not None)

            collector._read_count_window = read_count_window
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual(("吸收",), result.pending_actions)
            self.assertEqual(1, len(clicks))
            record = progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            self.assertEqual(CollectionActionState.PENDING.value, record["state"])

    def test_formal_single_outlier_post_count_stays_pending_without_absolute_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            clicks = []
            detections = iter(
                (
                    ActionIconDetection(
                        ActionIconState.AVAILABLE,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.95,
                    ),
                    ActionIconDetection(
                        ActionIconState.AVAILABLE,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.95,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                    ActionIconDetection(
                        ActionIconState.USED,
                        MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                        0.65,
                    ),
                )
            )
            collector = Collector(
                SimpleNamespace(
                    config={},
                    sleep=lambda *_args: None,
                    info_set=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((0, 21), (17, 21)))
            def read_count_window(_action, _detection=None, **kwargs):
                return CountWindowObservation(next(count_values), not kwargs.get("allow_single"))

            collector._read_count_window = read_count_window
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual({"吸收": (0, 21)}, progress.state.observed_counts)
            self.assertFalse(progress.state.depleted_today)
            self.assertEqual(1, progress.effective_used("吸收"))
            record = progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            self.assertEqual(CollectionActionState.PENDING.value, record["state"])
            self.assertNotIn("observed", record)

    def test_formal_used_icon_completes_without_click_or_count_ocr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            clicks = []
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: used)
            collector._read_count_window = lambda *_args, **_kwargs: self.fail(
                "pre-existing USED must not read or click"
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual([], clicks)
            self.assertEqual(
                CollectionActionState.PREEXISTING_USED.value,
                progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")["state"],
            )

    def test_preexisting_map_state_commits_without_consumption_and_allows_next_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            first = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(capture=lambda: frame),
                SimpleNamespace(),
                progress,
            )
            first.action_icons = SimpleNamespace(detect=lambda *_args: used)

            result = first._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertTrue(result.completed)
            self.assertEqual((), first.skill_failure_evidence)
            record = progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            self.assertNotIn("baseline", record)
            self.assertEqual(CollectionActionState.PREEXISTING_USED.value, record["state"])
            progress.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)
            self.assertEqual(0, progress.effective_used("吸收"))
            self.assertEqual(0, progress.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, progress.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(CollectionActionState.PREEXISTING_USED.value, record["state"])
            self.assertEqual((1, 21), progress.state.observed_counts["吸收"])

            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            detections = iter((available, available, used, used))
            clicks = []
            second = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            second.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((1, 21), (2, 21)))

            def stable_count_window(_action, _detection=None, **_kwargs):
                return CountWindowObservation(next(count_values), True)

            second._read_count_window = stable_count_window
            second._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )
            next_result = second._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_1,
            )

            self.assertTrue(next_result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual(2, progress.effective_used("吸收"))
            self.assertEqual(
                CollectionActionState.SETTLED.value,
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收")[
                    "state"
                ],
            )

    def test_preexisting_used_keeps_older_pending_for_a_bright_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            _seed_battle_supplements(
                progress,
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
            )
            progress.mark_target("Q_sp1", CollectionMapRole.BATTLE_AREA_1.value)
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(capture=lambda: frame),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: used)

            def stable_checkpoint(_action, _detection=None, **_kwargs):
                self.fail("dim counts must not reconcile older pending actions")

            collector._read_count_window = stable_checkpoint
            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertEqual((), result.pending_actions)
            self.assertIn("本次未点击", result.message)
            self.assertEqual(1, progress.pending_count("吸收"))
            self.assertNotIn("吸收", progress.state.observed_counts)
            old = progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收")
            current = progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收")
            self.assertEqual(CollectionActionState.PENDING.value, old["state"])
            self.assertEqual(CollectionActionState.PREEXISTING_USED.value, current["state"])
            self.assertNotIn("baseline", current)
            self.assertFalse(current["pending"])
            self.assertEqual(1, progress.effective_used("吸收"))
            _seed_battle_supplements(
                progress,
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_2,
            )
            progress.mark_target("Q_sp1", CollectionMapRole.BATTLE_AREA_2.value)
            self.assertEqual(1, progress.effective_used("吸收"))
            self.assertEqual(1, progress.reconcile_pending("吸收", (2, 21)))
            self.assertEqual(0, progress.reconcile_pending("吸收", (2, 21)))

    def test_dim_count_jump_retries_and_recovers_next_map_after_restart(self):
        for action, limit in zip(BATTLE_ACTIONS, (21, 21, 75), strict=True):
            for bad_count, bad_limit, fallback_recovers in (
                (8, limit, False), (limit, limit, False),
                (6, limit + 1, False), (8, limit, True),
            ):
                with (
                    self.subTest(action=action.name, bad=(bad_count, bad_limit),
                                 recovered=fallback_recovers),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    path = Path(directory) / "progress.json"
                    progress = ProgressStore(path)
                    progress.load()
                    progress.reconcile_pending(action.name, (5, limit))
                    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                    clicks = []
                    scales = []
                    hud = {"used": False, "before": 5, "after": bad_count, "limit": bad_limit}

                    def click(*_args, **_kwargs):
                        clicks.append(1)
                        hud["used"] = True

                    def ocr(_frame, _name, *, ocr_scale, **_kwargs):
                        scales.append(ocr_scale)
                        value = hud["after"] if hud["used"] else hud["before"]
                        if hud["used"] and fallback_recovers and ocr_scale == 3:
                            value = hud["before"] + 1
                        return f"{value}/{hud['limit'] if hud['used'] else limit}"

                    task = SimpleNamespace(
                        config={}, sleep=lambda *_: None, info_set=lambda *_: None,
                    )
                    vision = SimpleNamespace(
                        capture=lambda: frame, ocr_text=ocr, click_client=click,
                    )

                    def collector_for(store):
                        collector = Collector(task, vision, SimpleNamespace(), store)
                        collector._detect_action_icon = lambda *_a, **_k: (
                            frame,
                            ActionIconDetection(
                                ActionIconState.USED if hud["used"] else ActionIconState.AVAILABLE,
                                MatchResult(0.99, (900, 420), (44, 43), 0.95, 0.92),
                                stable=True, sample_count=2,
                            ),
                        )
                        collector._read_action_feedback = lambda _a: SkillFeedbackObservation(
                            "成功反馈", "success", 1.0,
                        )
                        return collector

                    first = collector_for(progress)
                    result = first._use_action(
                        action, card_id="Q_sp3", map_role=CollectionMapRole.BATTLE_AREA_2,
                    )
                    self.assertTrue(result.completed)
                    self.assertFalse(result.depleted)
                    self.assertIn(SKILL_OCR_FALLBACK_UPSCALE, scales)
                    self.assertLessEqual(len(scales), 12)
                    self.assertEqual((6 if fallback_recovers else 5, limit),
                                     progress.trusted_action_baseline(action.name))
                    record = progress.get_action_record("Q_sp3", "battle_area_2", action.name)
                    self.assertEqual(not fallback_recovers, record["pending"])
                    if not fallback_recovers:
                        self.assertNotIn("observed", record)
                    self.assertEqual(6, progress.effective_used(action.name))
                    self.assertEqual(1, len(clicks))

                    resumed = ProgressStore(path)
                    resumed.load()
                    second = collector_for(resumed)
                    self.assertTrue(second._use_action(
                        action, card_id="Q_sp3", map_role=CollectionMapRole.BATTLE_AREA_2,
                    ).completed)
                    self.assertEqual(1, len(clicks))
                    hud.update(used=False, before=6, after=7, limit=limit)
                    self.assertTrue(second._use_action(
                        action, card_id="Q_sp4", map_role=CollectionMapRole.BATTLE_AREA_1,
                    ).completed)
                    self.assertEqual(2, len(clicks))
                    self.assertEqual(0, resumed.pending_count(action.name))
                    self.assertEqual((7, limit), resumed.trusted_action_baseline(action.name))
                    self.assertEqual(0, resumed.reconcile_pending(action.name, (7, limit)))
                    hud.update(used=False, before=10)
                    self.assertFalse(second._use_action(
                        action, card_id="Q_sp5", map_role=CollectionMapRole.BATTLE_AREA_1,
                    ).completed)
                    self.assertEqual(2, len(clicks))
                    self.assertEqual((7, limit), resumed.trusted_action_baseline(action.name))

    def test_restart_armed_intent_blocks_repeat_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.MAIN_AREA,
            )

            self.assertFalse(result.completed)
            self.assertIn("禁止重复点击", result.message)
            self.assertEqual([], clicks)
            record = progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            record["baseline"] = []
            progress.save()
            progress.load()
            result = collector._use_action(
                ABSORB_ACTION, card_id="Q_sp1", map_role=CollectionMapRole.MAIN_AREA,
            )
            self.assertFalse(result.completed)
            self.assertIn("基线损坏", result.message)
            self.assertEqual([], clicks)

    def test_battle_two_bright_checkpoint_settles_battle_one_before_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                pending=True,
            )
            _seed_battle_supplements(
                progress,
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
            )
            progress.mark_target("Q_sp1", CollectionMapRole.BATTLE_AREA_1.value)

            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            detections = iter((available, available, used, used))
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((1, 21), (2, 21)))

            def read_count_window(_action, _detection=None, **_kwargs):
                return CountWindowObservation(next(count_values), True)

            collector._read_count_window = read_count_window
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual(
                "settled",
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收")[
                    "state"
                ],
            )
            self.assertEqual(
                "settled",
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收")[
                    "state"
                ],
            )

    def test_battle_two_stable_observed_after_local_lower_bound_allows_new_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )
            self.assertEqual(1, progress.reconcile_pending("吸收", (1, 21)))
            progress.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)

            progress.arm_action("Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", baseline=(1, 21))
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            _seed_battle_supplements(
                progress,
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
            )
            progress.mark_target("Q_sp1", CollectionMapRole.BATTLE_AREA_1.value)
            self.assertEqual(2, progress.state.daily_absorbs)

            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            detections = iter((available, available, used, used))
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda center, _shape, after_sleep=0: clicks.append(center),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: next(detections))
            count_values = iter(((2, 21), (3, 21)))

            def stable_count_window(_action, _detection=None, **_kwargs):
                return CountWindowObservation(next(count_values), True)

            collector._read_count_window = stable_count_window
            collector._read_action_feedback = lambda _action: SkillFeedbackObservation(
                "吸收周围的拾取物", "success", 1.0
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertEqual(1, len(clicks))
            self.assertEqual(
                CollectionActionState.SETTLED.value,
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收")[
                    "state"
                ],
            )
            self.assertEqual(
                CollectionActionState.SETTLED.value,
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收")[
                    "state"
                ],
            )
            self.assertEqual((3, 21), progress.state.observed_counts["吸收"])

    def test_battle_two_bright_zero_blocks_click_when_battle_one_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            _seed_battle_supplements(
                progress,
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
            )
            progress.mark_target("Q_sp1", CollectionMapRole.BATTLE_AREA_1.value)
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)
            def read_count_window(*_args, **_kwargs):
                return CountWindowObservation((0, 21), True)

            collector._read_count_window = read_count_window

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertFalse(result.completed)
            self.assertIn("待对账", result.message)
            self.assertEqual([], clicks)
            self.assertIsNone(
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收")
            )

    def test_used_single_checkpoint_preserves_pending_and_records_only_map_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            before_observed = dict(progress.state.observed_counts)
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            used = ActionIconDetection(
                ActionIconState.USED,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.65,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: used)

            collector._read_count_window = Mock(
                side_effect=AssertionError("USED does not read counts"),
            )

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertIn("本次未点击", result.message)
            self.assertEqual([], clicks)
            self.assertEqual(before_observed, progress.state.observed_counts)
            self.assertEqual(1, progress.pending_count("吸收"))
            self.assertFalse(
                progress.get_action_record(
                    "Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收",
                )["pending"]
            )

    def test_available_single_checkpoint_blocks_new_click_and_global_reconcile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.arm_action(
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
                "吸收",
                baseline=(0, 21),
            )
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            clicks = []
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)

            collector._read_count = Mock(side_effect=[(17, 21), None, None] * 2)

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertFalse(result.completed)
            self.assertIn("未取得可信次数", result.message)
            self.assertEqual([], clicks)
            self.assertEqual({}, progress.state.observed_counts)
            self.assertEqual(1, progress.pending_count("吸收"))
            self.assertIsNone(
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收")
            )

    def test_previous_observed_delta_ignores_new_local_lower_bound(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()

            progress.arm_action("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", baseline=(0, 21))
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.MAIN_AREA, "吸收", pending=True
            )
            self.assertEqual(1, progress.reconcile_pending("吸收", (1, 21)))
            progress.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)

            progress.arm_action("Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", baseline=(1, 21))
            progress.mark_action_local_done(
                "Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收", pending=True
            )
            _seed_battle_supplements(
                progress,
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_1,
            )
            progress.mark_target("Q_sp1", CollectionMapRole.BATTLE_AREA_1.value)

            self.assertEqual(2, progress.state.daily_absorbs)
            self.assertEqual(1, progress.reconcile_pending("吸收", (2, 21)))
            self.assertEqual(0, progress.pending_count("吸收"))
            self.assertEqual((2, 21), progress.state.observed_counts["吸收"])

    def test_click_exception_leaves_formal_action_armed_not_clicked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            collector = Collector(
                SimpleNamespace(
                    config={},
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                ),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        RuntimeError("click interrupted")
                    ),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)
            def read_count_window(*_args, **_kwargs):
                return CountWindowObservation((0, 21), True)

            collector._read_count_window = read_count_window

            with self.assertRaisesRegex(RuntimeError, "click interrupted"):
                collector._use_action(
                    ABSORB_ACTION,
                    card_id="Q_sp1",
                    map_role=CollectionMapRole.MAIN_AREA,
                )

            self.assertEqual(
                "armed",
                progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")["state"],
            )
