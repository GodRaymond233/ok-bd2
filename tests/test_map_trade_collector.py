"""Map-trade collector tests (split from test_map_trade.py)."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    ABSORB_ACTION,
    ACTION_FEEDBACK_CHARACTER_RATIO,
    ACTION_FEEDBACK_RELATIVE_ROI,
    ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS,
    ACTION_FEEDBACK_TIMEOUT,
    ACTION_ICON_DETECTION_INTERVAL,
    ACTION_OCR_WINDOW_INTERVAL,
    BATTLE_ACTIONS,
    SEARCH_ACTION,
    SEARCH_COUNTDOWN_REFERENCE_ROI,
    SEARCH_COUNTDOWN_RELATIVE_ROI,
    SKILL_FAILURE_EVIDENCE_LIMIT,
    SKILL_FIXED_COUNT_REFERENCE_ROIS,
    SKILL_GROUP_REFERENCE_POINTS,
    SKILL_GROUP_RELATIVE_POINTS,
    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SKILL_OCR_FALLBACK_UPSCALE,
    SKILL_OCR_UPSCALE,
    SUMMON_ACTION,
    SUPPRESS_ACTION,
    Collector,
    SearchCountdownSession,
    SkillExecutionResult,
    SkillFeedbackObservation,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    CollectionActionState,
    CollectionMapRole,
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
    def test_collection_retries_the_same_first_card_then_stops(self):
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

            navigator = SimpleNamespace(select_collection_card=select)
            result = Collector(task, object(), navigator, progress).run()

        self.assertFalse(result.success)
        self.assertEqual("未能进入卡带 Q_sp1", result.message)
        self.assertEqual([("Q_sp1", False)] * 3, attempts)

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
                prepare_collection_main_area=lambda card_id: (
                    events.append(("prepare", card_id))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                advance_collection_map=lambda card_id, current, target: (
                    events.append(("advance", card_id, current.key, target.key))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                open_story_quick_switcher_from_sandbox=lambda: (
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
            collector._verify_search_countdown = lambda value: (
                events.append(("countdown", value.value)) or True
            )

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
                ("prepare", "Q_sp1"),
                ("search",),
                ("actions", ("吸收",)),
                ("advance", "Q_sp1", "main_area", "battle_area_1"),
                ("countdown", 87),
                ("actions", tuple(action.name for action in BATTLE_ACTIONS)),
                ("advance", "Q_sp1", "battle_area_1", "battle_area_2"),
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

    def test_collection_never_starts_a_card_that_cannot_fit_daily_absorbs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.state.daily_submaps = 19
            progress.save()
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )
            navigator = SimpleNamespace(
                select_collection_card=lambda *_args, **_kwargs: self.fail(
                    "an incomplete card must not be started"
                )
            )

            result = Collector(task, object(), navigator, progress).run()

        self.assertTrue(result.success)
        self.assertTrue(result.depleted)
        self.assertEqual(0, result.completed_submaps)
        self.assertTrue(progress.state.depleted_today)

    def test_formal_collection_skips_chapter_fourteen_without_progress(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            progress.state.daily_submaps = 18
            progress.state.daily_summons = 12
            progress.state.daily_suppressions = 12
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
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                advance_collection_map=lambda *_args: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                open_story_quick_switcher_from_sandbox=lambda: NavigationResult(
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
            collector._verify_search_countdown = lambda _session: True

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
        self.assertEqual([("Q_sp15", False)], selected)
        self.assertEqual(set(), progress.state.completed_targets("Q_sp14"))
        self.assertTrue(progress.state.card_verified("Q_sp15"))
        self.assertTrue(any("第14章" in value for value in warnings))

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
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True,
                    ScreenState.SANDBOX,
                ),
                advance_collection_map=lambda _card_id, current, target: (
                    events.append(("advance", current.key, target.key))
                    or NavigationResult(True, ScreenState.SANDBOX)
                ),
                open_story_quick_switcher_from_sandbox=lambda: self.fail(
                    "battle two must be left for the next daily cycle"
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            collector._start_search = lambda **_kwargs: SearchCountdownSession(
                (0.1, 0.2, 0.3, 0.4),
                80,
            )
            collector._verify_search_countdown = lambda _session: True
            action_results = iter(
                (
                    SkillExecutionResult(True),
                    SkillExecutionResult(True, depleted=True),
                )
            )

            def fake_use_actions(_actions, *, card_id, map_role):
                _seed_action_records(progress, card_id, map_role.value)
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
                prepare_collection_main_area=lambda _card_id: NavigationResult(
                    True, ScreenState.SANDBOX
                ),
                advance_collection_map=lambda *_args: NavigationResult(True, ScreenState.SANDBOX),
                open_story_quick_switcher_from_sandbox=lambda: NavigationResult(
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
            collector._verify_search_countdown = lambda _session: True

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


class CollectorSkillTest(unittest.TestCase):
    @staticmethod
    def _skill_collector(states, counts):
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
            Path(tempfile.mkdtemp(prefix="ok-bd2-skill-")) / "progress.json",
            lambda: datetime(2026, 8, 11, 12, tzinfo=UTC_PLUS_8),
        )
        progress.load()
        collector = Collector(task, vision, SimpleNamespace(), progress)
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
            )

        collector.action_icons = SimpleNamespace(detect=detect)
        count_iters = {name: iter(values) for name, values in counts.items()}
        collector._read_count_window = lambda action, _detection=None, **_kwargs: next(
            count_iters[action.name]
        )

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
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())

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
            (735 / 1920, 210 / 1080, 1182 / 1920, 270 / 1080),
            ACTION_FEEDBACK_RELATIVE_ROI,
        )
        self.assertEqual(0.80, ACTION_FEEDBACK_CHARACTER_RATIO)
        self.assertEqual((1550, 969, 52, 44), SEARCH_COUNTDOWN_REFERENCE_ROI)
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
        self.assertEqual(13, len(calls))
        self.assertEqual(ACTION_FEEDBACK_TIMEOUT, sum(sleeps))
        self.assertEqual(ACTION_FEEDBACK_TIMEOUT, clock[0])

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
        detections = iter((available, absent))
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
        self.assertEqual([ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS], sleeps)

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
        detections = iter((available, absent))
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
        self.assertEqual({"吸收", "召集"}, set(result.pending_actions))

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
        self.assertIn("OCR 失败", result.message)
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

    def test_mid_sequence_exhaustion_waits_for_all_three_skills(self):
        collector, clicks, _statuses, _progress = self._skill_collector(
            {
                "探查": ActionIconState.AVAILABLE,
                "吸收": ActionIconState.AVAILABLE,
                "召集": ActionIconState.AVAILABLE,
            },
            {
                "吸收": ((21, 21),),
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
        self.assertEqual(1, len(clicks))

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
            ocr_text=lambda _frame, name, **kwargs: calls.append((name, kwargs)) or "7/60",
        )
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )

        count = collector._read_count(BATTLE_ACTIONS[-1], detection)

        self.assertEqual((7, 60), count)
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
        frames = iter((before_frame, after_frame, after_frame))
        detections = iter((before, after, after))
        count_detections = []
        clicks = []
        progress = ProgressStore(
            Path(tempfile.mkdtemp(prefix="ok-bd2-post-click-")) / "progress.json",
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
        collector._read_count_window = lambda _action, detection, **_kwargs: (
            count_detections.append(detection) or next(counts)
        )
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
        self.assertEqual([before, after], count_detections)
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
                self.assertIsNone(action.count_roi)
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

    def test_battle_arrival_checks_search_countdown_without_matching_search_icon(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: ocr_calls.append((name, kwargs)) or "44",
        )
        collector = Collector(
            SimpleNamespace(
                config={},
                sleep=lambda *_args: None,
                log_warning=lambda *_args: None,
            ),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(
            detect=lambda *_args: self.fail(
                "active search must not be template-matched after map travel"
            )
        )
        session = SearchCountdownSession((0.4, 0.5, 0.6, 0.7), 45)

        self.assertTrue(collector._verify_search_countdown(session))
        self.assertEqual(
            [
                (
                    "战斗区域1探查倒计时",
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
        detections = iter([missing] * 6 + [available, available, available, missing])
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
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
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

    def test_skill_menu_recovery_fails_after_one_group_one_click(self):
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
        collector = Collector(task, vision, SimpleNamespace(), SimpleNamespace())
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._use_actions(
            (ABSORB_ACTION,),
            card_id="Q_sp1",
            map_role=CollectionMapRole.BATTLE_AREA_1,
        )

        self.assertEqual(
            [
                (
                    SKILL_GROUP_RELATIVE_POINTS[1][0],
                    SKILL_GROUP_RELATIVE_POINTS[1][1],
                    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
                )
            ],
            clicks,
        )
        self.assertFalse(result.completed)
        self.assertIn("未确认采集技能栏", result.message)

    def test_skill_menu_recovery_latch_allows_at_most_one_click_across_calls(self):
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
            SimpleNamespace(),
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
        self.assertEqual(1, len(clicks))
        self.assertTrue(any("不再重复点击" in value for value in warnings))

    def test_start_search_recovers_group_one_once_then_fails(self):
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
            SimpleNamespace(),
            SimpleNamespace(),
        )
        collector.action_icons = SimpleNamespace(detect=lambda *_args: missing)

        result = collector._start_search(map_role=CollectionMapRole.MAIN_AREA)

        self.assertFalse(result.completed)
        self.assertEqual(1, len(clicks))
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
            Path(tempfile.mkdtemp(prefix="ok-bd2-missing-action-")) / "progress.json",
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
        calls = []
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, name, **kwargs: (
                calls.append((name, kwargs))
                or ("2" if kwargs.get("ocr_scale") == SKILL_OCR_UPSCALE else "1/21")
            ),
        )
        collector = Collector(
            SimpleNamespace(config={}, sleep=lambda *_args: None),
            vision,
            SimpleNamespace(),
            SimpleNamespace(),
        )

        self.assertEqual((1, 21), collector._read_count(ABSORB_ACTION))
        self.assertEqual(
            [SKILL_OCR_UPSCALE, SKILL_OCR_FALLBACK_UPSCALE],
            [kwargs["ocr_scale"] for _name, kwargs in calls],
        )

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
            collector._read_count_window = lambda action, detection, **_kwargs: next(count_values)
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
            collector._read_count_window = lambda _action, _detection=None, **_kwargs: next(
                count_values
            )
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
            self.assertEqual({}, progress.state.observed_counts)
            self.assertFalse(progress.state.depleted_today)
            self.assertEqual(1, progress.effective_used("吸收"))
            record = progress.get_action_record("Q_sp1", CollectionMapRole.MAIN_AREA, "吸收")
            self.assertEqual(CollectionActionState.PENDING.value, record["state"])
            self.assertEqual([17, 21], record["observed"])

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

    def test_first_day_preexisting_baseline_settles_after_target_and_allows_next_click(self):
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
                SimpleNamespace(config={}, info_set=lambda *_args: None),
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
            self.assertEqual([0, 21], record["baseline"])
            self.assertEqual(CollectionActionState.PREEXISTING_USED.value, record["state"])
            progress.mark_target("Q_sp1", CollectionMapRole.MAIN_AREA.value)
            self.assertEqual(1, progress.effective_used("吸收"))
            self.assertEqual(1, progress.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(0, progress.reconcile_pending("吸收", (1, 21)))
            self.assertEqual(CollectionActionState.SETTLED.value, record["state"])
            self.assertEqual((1, 21), progress.state.observed_counts["吸收"])

            available = ActionIconDetection(
                ActionIconState.AVAILABLE,
                MatchResult(0.98, (900, 420), (44, 43), 0.95, 0.92),
                0.95,
            )
            detections = iter((available, used, used))
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
                second._last_count_window_stable = True
                return next(count_values)

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

    def test_preexisting_used_checkpoint_covers_older_pending_without_second_increment(self):
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
                collector._last_count_window_stable = True
                return (2, 21)

            collector._read_count_window = stable_checkpoint
            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertTrue(result.completed)
            self.assertEqual((), result.pending_actions)
            self.assertIn("已由明亮帧对账", result.message)
            self.assertEqual(0, progress.pending_count("吸收"))
            self.assertEqual((2, 21), progress.state.observed_counts["吸收"])
            old = progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_1, "吸收")
            current = progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收")
            self.assertEqual(CollectionActionState.SETTLED.value, old["state"])
            self.assertEqual(CollectionActionState.SETTLED.value, current["state"])
            self.assertEqual([1, 21], current["baseline"])
            self.assertTrue(current["covered"])
            self.assertEqual(2, progress.effective_used("吸收"))
            _seed_battle_supplements(
                progress,
                "Q_sp1",
                CollectionMapRole.BATTLE_AREA_2,
            )
            progress.mark_target("Q_sp1", CollectionMapRole.BATTLE_AREA_2.value)
            self.assertEqual(2, progress.effective_used("吸收"))
            self.assertEqual(0, progress.reconcile_pending("吸收", (2, 21)))

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
            detections = iter((available, used, used))
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
                collector._last_count_window_stable = True
                return next(count_values)

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
            detections = iter((available, used, used))
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
                collector._last_count_window_stable = True
                return next(count_values)

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
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)
            collector._read_count_window = lambda *_args, **_kwargs: (0, 21)

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

    def test_used_single_checkpoint_does_not_settle_old_pending_or_create_current_record(self):
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
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: used)

            def single_checkpoint(_action, _detection=None, **_kwargs):
                collector._last_count_window_stable = False
                return (17, 21)

            collector._read_count_window = single_checkpoint

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertFalse(result.completed)
            self.assertIn("待对账", result.message)
            self.assertEqual([], clicks)
            self.assertEqual(before_observed, progress.state.observed_counts)
            self.assertEqual(1, progress.pending_count("吸收"))
            self.assertIsNone(
                progress.get_action_record("Q_sp1", CollectionMapRole.BATTLE_AREA_2, "吸收")
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
                SimpleNamespace(config={}, info_set=lambda *_args: None),
                SimpleNamespace(
                    capture=lambda: frame,
                    click_client=lambda *args, **kwargs: clicks.append((args, kwargs)),
                ),
                SimpleNamespace(),
                progress,
            )
            collector.action_icons = SimpleNamespace(detect=lambda *_args: available)

            def single_checkpoint(_action, _detection=None, **_kwargs):
                collector._last_count_window_stable = False
                return (17, 21)

            collector._read_count_window = single_checkpoint

            result = collector._use_action(
                ABSORB_ACTION,
                card_id="Q_sp1",
                map_role=CollectionMapRole.BATTLE_AREA_2,
            )

            self.assertFalse(result.completed)
            self.assertIn("窗口不稳定", result.message)
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
                SimpleNamespace(config={}, info_set=lambda *_args: None),
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
            collector._read_count_window = lambda *_args, **_kwargs: (0, 21)

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
