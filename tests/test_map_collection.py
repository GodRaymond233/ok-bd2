import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import cv2
import numpy as np

from src.interaction.BD2Interaction import InputTargetChanged
from src.tasks import MapCollectionTask as map_collection_task_module
from src.tasks.BaseBD2Task import CartridgeSpecialPageResult
from src.tasks.BD2MapCollectionProbeTask import BD2MapCollectionProbeTask, TeleportMapScanResult
from src.tasks.map_trade import navigator_story
from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    ACTION_SLOT_CENTERS_REFERENCE,
    SEARCH_ICON,
    ActionIconDetection,
    ActionIconState,
)
from src.tasks.map_trade.audit import CollectionVisualAuditStore
from src.tasks.map_trade.card_status import (
    CardActionDetection,
    CardActionState,
    CollectionCardSelectionOutcome,
    CollectionCardSelectionResult,
    StoryCardCompletion,
)
from src.tasks.map_trade.collection_selection import (
    COLLECTION_CARD_CATALOG,
    resolve_collection_cards,
)
from src.tasks.map_trade.collector import Collector
from src.tasks.map_trade.collector_constants import (
    ABSORB_ACTION,
    BATTLE_ACTIONS,
    SEARCH_COUNTDOWN_RELATIVE_ROI,
    SKILL_OCR_FALLBACK_UPSCALE,
    SUMMON_ACTION,
    SUPPRESS_ACTION,
    CountWindowObservation,
    SearchCountdownSession,
    SkillExecutionResult,
    SkillFeedbackObservation,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    EVENT_CARDS,
    CollectionMapRole,
    CollectionResult,
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    LocatedStoryCard,
    Navigator,
    ProbedStoryCard,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.progress import UTC_PLUS_8, ProgressStore
from src.tasks.map_trade.vision import Vision
from src.tasks.MapCollectionTask import MapCollectionTask
from tests.helpers.map_trade import _seed_action_records, _seed_battle_supplements
from tests.helpers.recognition_fixtures import load_bgr

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC_PLUS_8)
ROOT = Path(__file__).resolve().parents[1]


def _complete_card(progress, card, required_actions=None):
    """Seed every target and the card verification for the given policy."""

    for target in card.targets:
        actions = None
        if required_actions is not None:
            actions = ("吸收",) if target is card.targets[0] else tuple(required_actions)
        _seed_action_records(progress, card.card_id, target.key, actions=actions)
        progress.mark_target(card.card_id, target.key, required_actions=required_actions)
    progress.mark_card_verified(card.card_id, required_actions=required_actions)


def _badge(number: int = 1, center: tuple[int, int] = (100, 920)) -> StoryBadgeDetection:
    best = MatchResult(
        0.99, (center[0] - 14, center[1] - 14), (29, 29), pixel_score=0.98, zncc_score=0.97
    )
    runner_up = MatchResult(0.80, best.position, best.size, pixel_score=0.80, zncc_score=0.70)
    return StoryBadgeDetection(
        StoryBadgeCandidate(number, best),
        StoryBadgeCandidate(20 if number != 20 else 19, runner_up),
        ocr_text=str(number),
        ocr_number=number,
    )


def _completion(
    absorb: CardActionState = CardActionState.COMPLETED,
    suppress: CardActionState = CardActionState.COMPLETED,
    *,
    complete_region: bool = True,
) -> StoryCardCompletion:
    return StoryCardCompletion(
        absorb=CardActionDetection(absorb, reason="test"),
        suppress=CardActionDetection(suppress, reason="test"),
        bounds=(90, 1000, 260, 1070),
        complete_region=complete_region,
    )


def collection_navigator():
    field = NavigationResult(True, ScreenState.SANDBOX)
    menu = NavigationResult(True, ScreenState.CARD_MENU)
    return SimpleNamespace(
        select_collection_card=Mock(
            return_value=CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.ENTERED, field
            )
        ),
        current_collection_target=lambda card: card.targets[0].key,
        prepare_collection_main_area=Mock(return_value=field),
        advance_collection_map=Mock(return_value=field),
        open_cartridge_quick_switcher_from_sandbox=Mock(return_value=menu),
        inspect_collection_card_completion=Mock(
            return_value=CollectionCardSelectionResult(
                CollectionCardSelectionOutcome.VISUALLY_COMPLETE, menu
            )
        ),
        return_home=Mock(return_value=NavigationResult(True, ScreenState.HOME)),
    )


class CollectionRunTest(unittest.TestCase):
    def make_collector(self, progress, navigator=None):
        task = SimpleNamespace(
            config={"卡带单步重试次数": 1},
            info_set=Mock(),
            log_info=Mock(),
            log_warning=Mock(),
            log_error=Mock(),
        )
        navigator = navigator or SimpleNamespace(
            return_home=Mock(return_value=NavigationResult(True, ScreenState.HOME))
        )
        return Collector(task, object(), navigator, progress)

    def test_card_route_verifies_after_both_battles_and_resumes_only_missing_actions(self):
        card = CARD_BY_ID["Q_sp1"]
        for prior_policy in ("fresh", "without_suppression", "pending_final"):
            with self.subTest(policy=prior_policy), tempfile.TemporaryDirectory() as directory:
                progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
                progress.load()
                if prior_policy == "without_suppression":
                    _complete_card(progress, card, required_actions=("吸收", "召集"))
                nav = collection_navigator()
                collector = self.make_collector(progress, nav)
                events = []
                collector._ensure_search = Mock(
                    return_value=SearchCountdownSession((0, 0, 1, 1), 80)
                )
                collector._absorb_used_up = Mock(return_value=None)
                collector._refresh_collection_quota = Mock(return_value=None)
                collector._check_collection_skill_layout = Mock(return_value=None)

                def advance(_, current, target):
                    events.append(("advance", current.key, target.key))
                    return NavigationResult(True, ScreenState.SANDBOX)

                def execute(actions, *, card_id, map_role):
                    self.assertEqual({}, collector._last_skill_observations)
                    collector._last_skill_observations["吸收"] = {"map": map_role.value}
                    pending = []
                    for action in actions:
                        if progress.weekly_action_complete(card_id, map_role, action.name):
                            continue
                        events.append((map_role.value, action.name))
                        if prior_policy == "pending_final":
                            progress.arm_action(card_id, map_role, action.name, baseline=(0, 21))
                            progress.mark_action_local_done(
                                card_id, map_role, action.name, pending=True
                            )
                            pending.append(action.name)
                        else:
                            _seed_action_records(progress, card_id, map_role.value, (action.name,))
                    return SkillExecutionResult(True, pending_actions=tuple(pending))

                nav.advance_collection_map.side_effect = advance

                def verify(*args, **kwargs):
                    events.append(("verify",))
                    return nav.inspect_collection_card_completion.return_value

                nav.inspect_collection_card_completion.side_effect = verify
                collector._use_actions = execute
                result = collector.run((card,))
                self.assertTrue(result.success, result.message)
                first, second = "battle_area_1", "battle_area_2"
                expected = [] if prior_policy == "without_suppression" else [("main_area", "吸收")]
                for previous, role in (("main_area", first), (first, second)):
                    expected.append(("advance", previous, role))
                    expected.extend(
                        (role, name)
                        for name in (
                            ("压制",)
                            if prior_policy == "without_suppression"
                            else ("吸收", "召集", "压制")
                        )
                    )
                self.assertEqual(expected + [("verify",)], events)
                progress.load()
                self.assertTrue(progress.state.card_verified(card.card_id))
                self.assertEqual(
                    7 if prior_policy == "pending_final" else 0, progress.pending_count()
                )
                nav.select_collection_card.reset_mock()
                self.assertTrue(collector.run((card,)).success)
                nav.select_collection_card.assert_not_called()

    def test_insufficient_historical_quota_is_refreshed_after_entry_before_skipping(self):
        for limit in (40, 45, 75, 130):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:
                progress = ProgressStore(Path(directory) / "progress.json")
                progress.load()
                progress.reconcile_pending("压制", (40, 40))
                entered = CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.ENTERED,
                    NavigationResult(True, ScreenState.SANDBOX),
                )
                collector = Collector(
                    SimpleNamespace(config={}, sleep=Mock(), info_set=Mock()),
                    SimpleNamespace(
                        capture=lambda: np.zeros((1080, 1920, 3), dtype=np.uint8),
                        ocr_text=lambda *a, **k: f"40/{limit}",
                    ),
                    SimpleNamespace(select_collection_card=Mock(return_value=entered)),
                    progress,
                )
                collector._open_skill_menu = Mock(return_value=True)
                collector._refresh_collection_quota = Collector._refresh_collection_quota.__get__(
                    collector
                )
                collector._collect_card_targets = Mock(
                    return_value=(CollectionResult(False, message="target boundary"), False)
                )
                collector._check_collection_skill_layout = Mock(return_value=None)
                result = collector.collect_card(
                    CARD_BY_ID["Q_sp1"], battle_actions=(SUPPRESS_ACTION,)
                )
                collector.navigator.select_collection_card.assert_called_once()
                self.assertEqual((40, limit), progress.trusted_action_baseline("压制"))
                self.assertEqual(int(limit > 40), collector._collect_card_targets.call_count)
                self.assertEqual(limit == 40, result.depleted)
                if limit > 40:
                    self.assertFalse(
                        collector._collect_card_targets.call_args.kwargs["recovery_only"]
                    )
                self.assertEqual({}, progress.state.action_records)

    def test_input_change_restarts_from_confirmed_home_once_and_keeps_ledger(self):
        for scenario in ("recover", "repeat", "unsafe_home"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                progress = ProgressStore(Path(directory) / "progress.json")
                progress.load()
                progress.mark_action_preexisting_used("Q_sp1", "main_area", "吸收")
                collector = Collector(
                    SimpleNamespace(config={}, info_set=Mock(), log_warning=Mock()),
                    SimpleNamespace(),
                    SimpleNamespace(
                        return_home=Mock(
                            return_value=NavigationResult(
                                scenario != "unsafe_home",
                                ScreenState.HOME
                                if scenario != "unsafe_home"
                                else ScreenState.UNKNOWN,
                            )
                        )
                    ),
                    progress,
                )
                collector._run_collection = Mock(
                    side_effect=[
                        InputTargetChanged("held mouse"),
                        InputTargetChanged("held again")
                        if scenario == "repeat"
                        else CollectionResult(True),
                    ]
                )
                result = collector.run((CARD_BY_ID["Q_sp1"],))
                self.assertEqual(scenario == "recover", result.success)
                self.assertEqual(
                    1 if scenario == "unsafe_home" else 2, collector._run_collection.call_count
                )
                collector.navigator.return_home.assert_called_once()
                progress.load()
                self.assertTrue(progress.weekly_action_complete("Q_sp1", "main_area", "吸收"))


class MapCollectionTaskScopeTest(unittest.TestCase):
    def make_task(self):
        task = object.__new__(MapCollectionTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch.object(
            map_collection_task_module.MapAutomationTaskBase, "__init__", return_value=None
        ):
            MapCollectionTask.__init__(task)
        task.config = dict(task.default_config)
        task.info = {}
        task.info_set = lambda key, value: task.info.__setitem__(key, value)
        task.log_info = Mock()
        task.log_warning = Mock()
        task.log_error = Mock()
        task.log_completion = Mock()
        task._save_diagnostic = Mock()
        return task

    def test_config_selects_only_eligible_cards_and_passes_the_action_policy(self):
        catalog = dict((card.card_id, name) for name, card in COLLECTION_CARD_CATALOG)
        self.assertEqual(
            COLLECTABLE_CARDS + EVENT_CARDS, tuple(card for _, card in COLLECTION_CARD_CATALOG)
        )
        self.assertNotIn("Q_sp6", catalog)
        for selected, blocked, expected in (
            (None, "", tuple(catalog)),
            (["Event_1_夏日骑士", "Story_02_苍蓝魔女"], "", ("Q_sp2", "Q_ep1")),
            (["Character_2_火晶片", "Event_3_海滨天使"], "Character_2", ("Q_ep3",)),
            ([], "", ()),
            (["Unknown_1"], "", ()),
            (["Character_8_妖魔讨伐团"], "", ()),
            (["Character_1_杰登之门"], "Character_1", ()),
        ):
            with self.subTest(selected=selected, blocked=blocked):
                task = self.make_task()
                task.config.update(
                    {
                        "自定义采集关卡": selected is not None,
                        "采集卡带屏蔽": blocked,
                        "启用压制技能": not blocked,
                        "单章模式": bool(blocked),
                    }
                )
                if selected is not None:
                    task.config["采集关卡"] = selected
                cards, error = resolve_collection_cards(task.config)
                self.assertEqual(expected, tuple(c.card_id for c in cards))
                self.assertEqual(not bool(expected), bool(error))
                runner = Mock()
                runner.run.return_value = CollectionResult(True)
                runner.scope_complete.return_value = True
                store = MagicMock()
                store.return_value.__enter__.return_value.state = SimpleNamespace(
                    weekly_key="2026-10-12", daily_key="2026-10-13"
                )
                with (
                    patch.object(map_collection_task_module, "Vision"),
                    patch.object(map_collection_task_module, "ProgressStore", store),
                    patch.object(
                        map_collection_task_module, "Navigator", return_value=collection_navigator()
                    ),
                    patch.object(map_collection_task_module, "Collector", return_value=runner),
                ):
                    self.assertEqual(bool(expected), task.run())
                self.assertEqual(int(bool(expected)), runner.run.call_count)
                if expected:
                    args, opts = runner.run.call_args
                    self.assertEqual(expected, tuple(c.card_id for c in args[0]))
                    self.assertEqual(not blocked, SUPPRESS_ACTION in opts["battle_actions"])
                    self.assertEqual(bool(blocked), opts["only_once"])
                    self.assertEqual(
                        ("本周", "2026-10-12"), (task.info["跑图完成范围"], task.info["跑图周期"])
                    )


def _event_bar_frame(*, held=True):
    """A 1080p quick-switch frame whose event row holds every cover, or lacks Q_ep1."""

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[895:1050] = load_bgr(ROOT / "tests/fixtures/quick_switch/event_bar_video_1080.png")
    if not held:
        frame[:, 90:250] = 0
    return frame


class EventCardAbsenceTest(unittest.TestCase):
    """Only valid low-score covers on a reconfirmed event tab mean "not held"."""

    def make_navigator(self, frames, *, page_after=True, tab_after=True):
        clock = [0.0]
        frames = list(frames)
        task = SimpleNamespace(
            info_set=Mock(),
            log_warning=Mock(),
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )
        vision = SimpleNamespace(capture=lambda: frames.pop(0) if len(frames) > 1 else frames[0])
        navigator = Navigator(task, vision)
        navigator.classify = Mock(return_value=ScreenState.CARD_MENU)
        navigator._wait_for_quick_switch_page = Mock(side_effect=[True, page_after])
        navigator._wait_for_cartridge_category = Mock(side_effect=[True, tab_after])
        navigator.return_home = Mock(return_value=NavigationResult(True, ScreenState.HOME))
        patcher = patch.object(navigator_story, "monotonic", lambda: clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        return navigator

    def test_absence_requires_valid_low_score_frames_and_a_reconfirmed_tab(self):
        absent, solid = _event_bar_frame(held=False), np.full((1080, 1920, 3), 40, np.uint8)
        # The page is rechecked only after every scanned frame read as absent.
        for name, frames, page_after, tab_after, expected, rechecked in (
            ("confirmed absence", [absent], True, True, True, True),
            ("invalid capture", [None], True, True, False, False),
            ("solid capture", [solid], True, True, False, False),
            ("invalid frame inside the window", [None, absent], True, True, False, False),
            ("page lost after the scan", [absent], False, True, False, True),
            ("event tab lost after the scan", [absent], True, False, False, True),
        ):
            with self.subTest(name):
                navigator = self.make_navigator(frames, page_after=page_after, tab_after=tab_after)
                located = navigator._locate_story_card("Q_ep1")
                self.assertIsInstance(located, NavigationResult)
                self.assertFalse(located.success)
                self.assertEqual(expected, located.card_not_found)
                self.assertEqual(
                    2 if rechecked else 1, navigator._wait_for_quick_switch_page.call_count
                )
                if expected:
                    navigator._wait_for_cartridge_category.assert_called_with(category="event")

        # A held event and a missing story card both stay in the weekly scope.
        navigator = self.make_navigator([_event_bar_frame()])
        self.assertNotIsInstance(navigator._locate_story_card("Q_ep1"), NavigationResult)

        navigator = self.make_navigator([_event_bar_frame()])
        navigator._event_card_scan_absent = True
        navigator._wait_for_story_badge_with_scroll = Mock(return_value=None)
        located = navigator._locate_story_card("Q_sp1")
        self.assertFalse(located.card_not_found)

    def test_confirmed_event_absence_is_the_only_weekly_skip(self):
        for held, success in ((None, False), (False, True)):
            with self.subTest(held=held), tempfile.TemporaryDirectory() as directory:
                progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
                progress.load()
                story, event = CARD_BY_ID["Q_sp1"], CARD_BY_ID["Q_ep1"]
                _complete_card(progress, story)
                navigator = self.make_navigator(
                    [None if held is None else _event_bar_frame(held=False)]
                )
                states = iter([ScreenState.CARD_MENU])
                navigator.classify = lambda: next(states, ScreenState.UNKNOWN)
                task = SimpleNamespace(
                    config={"卡带单步重试次数": 1},
                    info_set=Mock(),
                    log_info=Mock(),
                    log_warning=Mock(),
                    log_error=Mock(),
                )
                collector = Collector(task, navigator.vision, navigator, progress)
                result = collector.run((story, event))
                self.assertEqual(success, result.success)
                self.assertEqual(not success, result.partial)
                self.assertEqual([event] if success else [], collector.missing_cards)
                self.assertEqual(success, collector.scope_complete())
                self.assertEqual(int(not success), navigator.return_home.call_count)


class CollectionSelectionTest(unittest.TestCase):
    def test_collection_decisions_bind_fresh_identity_structure_and_status(self):
        for scenario in (
            "complete",
            "pending",
            "became_complete",
            "lost_complete",
            "moved",
            "renumbered",
            "category",
            "resized",
        ):
            with self.subTest(scenario=scenario):
                first = np.zeros((1080, 1920, 3), dtype=np.uint8)
                fresh = np.ones(
                    (720, 1280, 3) if scenario == "resized" else first.shape, dtype=np.uint8
                )

                initial = _badge(12, (315, 936))
                updated = _badge(13 if scenario == "renumbered" else 12,
                                 (495 if scenario == "moved" else 317, 936))
                located = LocatedStoryCard(CARD_BY_ID["Q_sp12"], first, initial)
                readings = []

                def status(frame, center):
                    readings.append((frame, center))
                    self.assertEqual(
                        initial.best.result.center
                        if frame is first
                        else updated.best.result.center,
                        center,
                    )
                    complete = CardActionState.COMPLETED
                    pending = CardActionState.PENDING
                    first_state, fresh_state = {
                        "became_complete": (pending, complete),
                        "lost_complete": (complete, CardActionState.UNKNOWN),
                        "pending": (pending, pending),
                    }.get(scenario, (complete, complete))
                    state = first_state if frame is first else fresh_state
                    return _completion(state, state)
                navigator = Navigator(
                    SimpleNamespace(info_set=Mock(), log_warning=Mock(), sleep=Mock()),
                    SimpleNamespace(capture=lambda: fresh),
                )
                navigator._locate_story_card = lambda _: located
                navigator._find_story_badge = lambda *_: (updated, "")
                navigator._cartridge_category_confirmed = lambda *_: scenario != "category"
                navigator.card_status = SimpleNamespace(detect=status)
                navigator._click_located_story_card = Mock(
                    return_value=NavigationResult(True, ScreenState.SANDBOX)
                )
                result = navigator.select_collection_card("Q_sp12")
                expected = {
                    "complete": CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    "became_complete": CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    "pending": CollectionCardSelectionOutcome.ENTERED,
                }.get(scenario, CollectionCardSelectionOutcome.FAILED)
                self.assertEqual(expected, result.outcome)
                if scenario == "pending":
                    clicked = navigator._click_located_story_card.call_args.args[0]
                    self.assertIs(fresh, clicked.frame)
                    self.assertIs(updated, clicked.badge)
                else:
                    navigator._click_located_story_card.assert_not_called()
                if scenario == "became_complete":
                    self.assertEqual(3, len(readings))

    def test_story_entry_retries_after_late_fiend_reward(self):
        for reward_result, expected in (
            (CartridgeSpecialPageResult.HANDLED, True),
            (CartridgeSpecialPageResult.ABSENT, False),
            (CartridgeSpecialPageResult.BLOCKED, False),
        ):
            with self.subTest(reward_result=reward_result):
                clock, recovered = [0.0], [False]

                def handle_reward(*, timeout, allow_pvp_pages):
                    self.assertFalse(allow_pvp_pages)
                    self.assertGreater(timeout, 0)
                    self.assertLessEqual(timeout, 2.0 - clock[0])
                    clock[0] += timeout
                    recovered[0] = reward_result is CartridgeSpecialPageResult.HANDLED
                    return reward_result

                task = SimpleNamespace(
                    _handle_recent_cartridge_special_pages=Mock(side_effect=handle_reward),
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                    info_set=lambda *args: None,
                )
                navigator = Navigator(task, SimpleNamespace(capture=lambda: None))
                navigator.classify = lambda frame: (
                    ScreenState.SANDBOX
                    if recovered[0]
                    else ScreenState.LOADING
                    if clock[0] < 0.5
                    else ScreenState.UNKNOWN
                )
                navigator._handle_story_card_intermediate = lambda frame: False
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic", lambda: clock[0]):
                    result = navigator._wait_for_story_sandbox(1, timeout=2.0, interval=0.1)
                self.assertEqual(expected, result.success)
                self.assertTrue(task._handle_recent_cartridge_special_pages.called)
                if reward_result is not CartridgeSpecialPageResult.ABSENT:
                    task._handle_recent_cartridge_special_pages.assert_called_once()
                self.assertLessEqual(clock[0], 2.1)


class CollectionSkillTest(unittest.TestCase):
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
                0.99, (100, 100), (40, 40), pixel_score=0.90, zncc_score=0.95
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
            task, vision, SimpleNamespace(wait_for_collection_field=lambda frame: frame), progress
        )
        executed_icons = set()

        def detect(_frame, icon):
            if icon.name in executed_icons:
                state = ActionIconState.ABSENT if icon is SEARCH_ICON else ActionIconState.USED
            else:
                state = states.get(icon.name, ActionIconState.ABSENT)
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
                collector, "_read_count", return_value=next(count_iters[action.name])
            ):
                return Collector._read_count_window(collector, action, detection, **kwargs)

        collector._read_count_window = read_count_window

        def feedback(action):
            executed_icons.add(action.icon.name)
            was_used = states[action.icon.name] is ActionIconState.USED
            return SkillFeedbackObservation(
                "失败反馈" if was_used else "成功反馈", "failure" if was_used else "success", 1.0
            )

        collector._read_action_feedback = feedback
        return collector, clicks, statuses, progress

    def _with_running_search(self, collector):
        """Counter/feedback fixtures have an active search in the separate first slot."""
        ocr = getattr(collector.vision, "ocr_text", None)
        detect = collector.action_icons.detect
        collector.vision.ocr_text = lambda frame, name, **kwargs: (
            "87" if name == "探查倒计时" else ocr(frame, name, **kwargs)
        )
        collector.action_icons.detect = lambda frame, icon: (
            ActionIconDetection(ActionIconState.ABSENT, MatchResult(-1.0, (0, 0), (0, 0)))
            if icon is SEARCH_ICON
            else detect(frame, icon)
        )

    def test_collection_layout_checks_all_slots_before_actions_and_requires_two_frames(self):
        source = cv2.imread(
            str(
                Path(__file__).resolve().parents[1]
                / "tests/fixtures/map_trade/video_fhd/story_sandbox_ready.png"
            )
        )
        swapped = source.copy()
        a, b = (ACTION_SLOT_CENTERS_REFERENCE[key] for key in ("summon", "subdue"))
        radius = 40
        first = source[a[1] - radius : a[1] + radius, a[0] - radius : a[0] + radius].copy()
        second = source[b[1] - radius : b[1] + radius, b[0] - radius : b[0] + radius].copy()
        swapped[a[1] - radius : a[1] + radius, a[0] - radius : a[0] + radius] = second
        swapped[b[1] - radius : b[1] + radius, b[0] - radius : b[0] + radius] = first
        for height in (720, 1080):
            for scenario in ("normal", "swapped", "transient", "unknown"):
                with self.subTest(height=height, scenario=scenario):
                    base = np.zeros_like(source) if scenario == "unknown" else source
                    frames = iter(
                        cv2.resize(f, (height * 16 // 9, height))
                        for f in (
                            swapped if scenario in {"swapped", "transient"} else base,
                            swapped if scenario == "swapped" else base,
                        )
                    )
                    task = SimpleNamespace(config={}, info_set=Mock(), sleep=Mock())
                    vision = Vision(task)
                    vision.capture = lambda: next(frames)
                    vision.click_client = Mock()
                    collector = Collector(
                        task,
                        vision,
                        SimpleNamespace(wait_for_collection_field=lambda frame: frame),
                        SimpleNamespace(),
                    )
                    if scenario == "swapped":
                        with self.assertRaisesRegex(RuntimeError, "第3号槽"):
                            collector._check_collection_skill_layout()
                    else:
                        collector._check_collection_skill_layout()
                    vision.click_client.assert_not_called()

    def test_cross_map_search_countdown_loss_recovers_only_with_recent_same_card_evidence(self):
        for scenario in ("readable", "expired", "timeout", "other_card", "stale"):
            with self.subTest(scenario=scenario):
                clock = [1000.0]
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

                def sleep(seconds):
                    clock[0] += seconds

                def countdown(*_args, **_kwargs):
                    if scenario == "readable" and clock[0] >= 1003.0:
                        return "8）"
                    return "1Q"

                def detect(_frame, _icon):
                    available = scenario == "expired" and clock[0] >= 1006.0
                    return ActionIconDetection(
                        ActionIconState.AVAILABLE if available else ActionIconState.ABSENT,
                        MatchResult(0.99, (100, 100), (40, 40)),
                    )

                collector = Collector(
                    SimpleNamespace(config={}, sleep=sleep, info_set=Mock()),
                    SimpleNamespace(capture=lambda: frame, ocr_text=countdown, click_client=Mock()),
                    SimpleNamespace(),
                    SimpleNamespace(),
                )
                collector._open_skill_menu = Mock(return_value=True)
                collector._capture_skill_field = Mock(return_value=(frame, False))
                collector.action_icons = SimpleNamespace(detect=detect)
                collector._search_expires_at[
                    (
                        "Q_sp1" if scenario == "other_card" else "Q_sp2",
                        CollectionMapRole.MAIN_AREA.value,
                    )
                ] = 999.0 if scenario == "stale" else 1006.0
                collector._start_search = Mock(
                    return_value=(
                        SearchCountdownSession(SEARCH_COUNTDOWN_RELATIVE_ROI, 89)
                        if scenario == "expired"
                        else SkillExecutionResult(False)
                    )
                )
                with patch(
                    "src.tasks.map_trade.collector_skills.monotonic", side_effect=lambda: clock[0]
                ):
                    result = collector._ensure_search(
                        card_id="Q_sp2", map_role=CollectionMapRole.BATTLE_AREA_2
                    )
                if scenario in ("readable", "expired"):
                    self.assertIsInstance(result, SearchCountdownSession)
                    self.assertEqual(8 if scenario == "readable" else 89, result.value)
                    if scenario == "readable":
                        collector._start_search.assert_not_called()
                    else:
                        collector._start_search.assert_called_once()
                        self.assertGreaterEqual(clock[0], 1006.0)
                else:
                    self.assertFalse(result.completed)
                    if scenario == "timeout":
                        self.assertIn("倒计时", result.message)
                        collector._start_search.assert_not_called()
                        self.assertLessEqual(clock[0], 1015.5)
                    else:
                        collector._capture_skill_field.assert_not_called()
                collector.vision.click_client.assert_not_called()

    def test_countdown_accepts_only_a_positive_timer_with_optional_closing_arc(self):
        collector, *_ = self._skill_collector({}, {})
        for text, expected in (
            ("201)", 201), ("200）", 200), (" 1 ", 1), ("999", 999), ("0)", None),
            ("1Q", None), ("1012", None), ("10/21", None), ("2011)", None),
            ("20)1", None), (")", None), ("201))", None),
        ):
            with self.subTest(text=text):
                collector.vision.ocr_text = Mock(return_value=text)
                self.assertEqual(expected, collector._read_search_countdown(None))

    def test_partial_menu_saves_the_observed_frame_and_its_rejection(self):
        collector, clicks, _, _ = self._skill_collector({}, {})
        frames = [np.full((16, 16, 3), i, np.uint8) for i in range(3)]
        collector.vision.capture = Mock(side_effect=frames)
        collector.task.save_frame = Mock()
        collector.task.operate_click = Mock()

        def detect(frame, icon):
            present = frame is frames[1] and icon is ABSORB_ICON
            return ActionIconDetection(
                ActionIconState.AVAILABLE if present else ActionIconState.ABSENT,
                MatchResult(0.98 if present else 0.938, (2, 2), (4, 4)),
                reason="亮态" if present else "模板身份分过低：0.938<0.940",
            )

        collector.action_icons.detect = detect
        self.assertFalse(collector._open_skill_menu(
            (SEARCH_ICON, ABSORB_ICON), allow_group_one_recovery=True,
        ))
        self.assertIs(frames[1], collector.task.save_frame.call_args.args[1])
        self.assertIn("0.938<0.940", collector._last_skill_observations["探查"]["reason"])
        self.assertEqual("available", collector._last_skill_observations["吸收"]["state"])
        collector.task.operate_click.assert_not_called()
        self.assertEqual([], clicks)

    def capacity_hud(self, action, baseline):
        collector, clicks, _, progress = self._skill_collector(
            {action.icon.name: ActionIconState.AVAILABLE}, {})
        progress.reconcile_pending(action.name, baseline)
        del collector._read_count_window
        return collector, clicks, progress

    def test_capacity_changes_require_consistent_full_readings_before_a_cast(self):
        role = CollectionMapRole.BATTLE_AREA_1
        for action in BATTLE_ACTIONS:
            for mode, limit in (
                ("valid", 30), ("valid", 45), ("valid", 75), ("valid", 130),
                ("bare", 75), ("disagree", 75), ("lower", 75), ("pending", 75),
            ):
                with self.subTest(action=action.name, mode=mode, limit=limit):
                    collector, clicks, progress = self.capacity_hud(action, (5, 40))
                    if mode == "pending":
                        progress.arm_action("Q_sp2", role, action.name, baseline=(5, 40))
                        progress.mark_action_clicked("Q_sp2", role, action.name)
                        progress.mark_action_local_done("Q_sp2", role, action.name, pending=True)

                    def read(_, name, **kwargs):
                        if mode == "bare":
                            return "575"
                        if mode == "disagree" and kwargs["ocr_scale"] == SKILL_OCR_FALLBACK_UPSCALE:
                            return "5/80"
                        return f"{4 if mode == 'lower' else 5 + len(clicks)}/{limit}"

                    collector.vision.ocr_text = read
                    self._with_running_search(collector)
                    result = collector._use_action(action, card_id="Q_sp1", map_role=role)
                    ok = mode == "valid"
                    self.assertEqual(ok, result.completed, result.message)
                    self.assertEqual(int(ok), len(clicks))
                    progress.load()
                    self.assertEqual(
                        (6, limit) if ok else (5, 40), progress.trusted_action_baseline(action.name)
                    )
                    self.assertEqual(int(mode == "pending"), progress.pending_count(action.name))
                    if ok:
                        self.assertTrue(
                            collector._use_action(action, card_id="Q_sp1", map_role=role).completed
                        )
                        self.assertEqual(1, len(clicks))
                    else:
                        self.assertIsNone(progress.get_action_record("Q_sp1", role, action.name))
                    if mode == "lower":
                        request = progress.state.count_reset_requests[action.name]
                        token = request["token"]
                        self.assertFalse(
                            progress.confirm_count_reset(action.name, (3, limit), token)
                        )
                        collector.task.config["次数重置确认码"] = token
                        collector.vision.ocr_text = lambda _, name, **kw: (
                            "87" if name == "探查倒计时" else f"{4 + len(clicks)}/{limit}"
                        )
                        retried = collector._use_action(action, card_id="Q_sp1", map_role=role)
                        self.assertTrue(retried.completed, retried.message)
                        progress.load()
                        self.assertEqual((5, limit), progress.trusted_action_baseline(action.name))
                        self.assertEqual(1, len(clicks))
                        self.assertFalse(
                            progress.confirm_count_reset(action.name, (4, limit), token)
                        )

        for bordered in ("7/21", "7/121", ""):
            with self.subTest(bordered=bordered):
                collector, clicks, progress = self.capacity_hud(ABSORB_ACTION, (7, 21))
                collector._count_actions_seen.add("吸收")
                raw = iter(("7/121",) * 4 + ("7/21", "7/121"))
                collector.vision.ocr_text = lambda _frame, _name, **kw: (
                    bordered if kw.get("relative_roi") is None else next(raw))
                before, failure = collector._prepare_before_click(
                    ABSORB_ACTION, collector.action_icons.detect(None, ABSORB_ICON),
                    card_id="Q_cp5", map_role=CollectionMapRole.MAIN_AREA)
                self.assertEqual((7, 21) if bordered == "7/21" else None, before)
                self.assertEqual(bordered == "7/21", failure is None)
                self.assertEqual((7, 21), progress.trusted_action_baseline("吸收"))
                self.assertIsNone(progress.get_action_record("Q_cp5", "main_area", "吸收"))
                self.assertEqual([], clicks)

    def test_input_failure_preserves_only_uncertain_skill_reservations(self):
        role = CollectionMapRole.BATTLE_AREA_2
        for case, state, expected in (
            ("changed", "settled", 1),
            ("unsent", "void", 0),
            ("uncertain", "armed", 0),
        ):
            with self.subTest(case=case):
                collector, clicks, _, progress = self._skill_collector(
                    {SUPPRESS_ACTION.icon.name: ActionIconState.AVAILABLE},
                    {"压制": ((0, 55), (0, 55), (1, 55))},
                )
                collector._open_skill_menu = Mock(return_value=True)
                send = collector.vision.click_client

                def dispatch(*args, **kwargs):
                    progress.load()
                    record = progress.get_action_record("Q_sp7", role, "压制")
                    self.assertEqual("armed", record["state"])
                    self.assertTrue(record["reservation"])
                    if collector.vision.click_client.call_count == 1:
                        if case == "changed":
                            raise InputTargetChanged("window changed")
                        error = RuntimeError("input failed")
                        error.input_not_dispatched = case == "unsent"
                        raise error
                    send(*args, **kwargs)

                collector.vision.click_client = Mock(side_effect=dispatch)
                if case == "changed":
                    self.assertTrue(
                        collector._use_actions(
                            (SUPPRESS_ACTION,), card_id="Q_sp7", map_role=role
                        ).completed
                    )
                else:
                    with self.assertRaisesRegex(RuntimeError, "input failed"):
                        collector._use_action(SUPPRESS_ACTION, card_id="Q_sp7", map_role=role)
                self.assertEqual(expected, len(clicks))
                progress.load()
                record = progress.get_action_record("Q_sp7", role, "压制")
                self.assertEqual(state, record["state"])
                self.assertEqual(case == "uncertain", record["reservation"])
                self.assertEqual(int(case != "unsent"), progress.effective_used("压制"))

    def test_settlement_checks_feedback_before_accepting_a_used_icon(self):
        role = CollectionMapRole.BATTLE_AREA_2
        for action, text, outcome in (
            (ABSORB_ACTION, "周围没有可以吸收的拾取勿。", "failure"),
            (ABSORB_ACTION, "周围没有可以吸收拾取物。", "failure"),
            (SUPPRESS_ACTION, "没有可制伏的怪物。", "failure"),
            (SUPPRESS_ACTION, "已制伏地图内所有怪物。没有可制服的怪物。", "failure"),
            (SUMMON_ACTION, "无 可召集的战场怪物。", "failure"),
            (SUMMON_ACTION, "召集带奖励的战场怪物。升星天赋技能后，可获得经验值。", "success"),
            (SUPPRESS_ACTION, "召集带奖励的战场怪物。已制伏地图内所有怪物。", "success"),
            (SUPPRESS_ACTION, "已制伏地图内所有的怪物。", "success"),
        ):
            with self.subTest(action=action.name, text=text):
                collector, clicks, _, progress = self._skill_collector(
                    {action.icon.name: ActionIconState.USED}, {}
                )
                before = (4, 21)
                progress.arm_action("Q_sp2", role, action.name, baseline=before)
                progress.mark_action_clicked("Q_sp2", role, action.name)
                collector.vision.ocr_text = Mock(return_value=text)
                observed = Collector._read_action_feedback(collector, action)
                self.assertEqual(outcome, observed.outcome)
                used = collector.action_icons.detect(None, action.icon)
                count = (5, 21) if outcome == "success" else before
                collector._read_count_window = Mock(
                    return_value=CountWindowObservation(count, True)
                )
                result = collector._finish_after_click(
                    action,
                    card_id="Q_sp2",
                    map_role=role,
                    before=before,
                    detection=replace(used, state=ActionIconState.AVAILABLE),
                    post_detection=used,
                    feedback=observed,
                )
                self.assertEqual(outcome == "success", result.completed)
                progress.load()
                self.assertEqual(
                    "settled" if result.completed else "blocked",
                    progress.get_action_record("Q_sp2", role, action.name)["state"],
                )
                self.assertEqual([], clicks)

    def test_next_map_settles_an_older_reservation_only_from_a_trusted_bright_count(self):
        old, new = CollectionMapRole.BATTLE_AREA_1, CollectionMapRole.BATTLE_AREA_2
        for mode in ("settle", "reserved", "single", "used", "outlier"):
            with self.subTest(mode=mode):
                state = ActionIconState.USED if mode == "used" else ActionIconState.AVAILABLE
                collector, clicks, _, progress = self._skill_collector(
                    {ABSORB_ICON.name: state}, {}
                )
                progress.arm_action("Q_sp1", old, "吸收", baseline=(0, 21))
                progress.mark_action_local_done("Q_sp1", old, "吸收", pending=True)
                _seed_battle_supplements(progress, "Q_sp1", old)
                progress.mark_target("Q_sp1", old.value)
                self._with_running_search(collector)
                observations = {
                    "settle": [((1, 21), True), ((2, 21), True)],
                    "reserved": [((0, 21), True)],
                    "single": [(None, False)],
                    "used": [],
                    "outlier": [((1, 21), True), ((17, 21), False)],
                }[mode]
                windows = iter(observations)
                collector._read_count_window = Mock(
                    side_effect=lambda *_a, **_k: CountWindowObservation(*next(windows))
                )
                if mode == "reserved":
                    before, failure = collector._prepare_before_click(
                        ABSORB_ACTION,
                        collector.action_icons.detect(None, ABSORB_ICON),
                        card_id="Q_sp1",
                        map_role=new,
                    )
                    self.assertIsNone(failure)
                    self.assertEqual((0, 21), before)
                    self.assertTrue(progress.arm_action("Q_sp1", new, "吸收", baseline=before))
                else:
                    result = collector._use_action(ABSORB_ACTION, card_id="Q_sp1", map_role=new)
                    self.assertEqual(mode != "single", result.completed)
                progress.load()
                self.assertEqual(int(mode in {"settle", "outlier"}), len(clicks))
                if mode == "reserved":
                    self.assertEqual(2, progress.effective_used("吸收"))
                self.assertEqual(0 if mode == "settle" else 1, progress.pending_count("吸收"))
                record = progress.get_action_record("Q_sp1", new, "吸收")
                expected = {
                    "single": None,
                    "reserved": "armed",
                    "settle": "settled",
                    "used": "preexisting_used",
                    "outlier": "pending",
                }[mode]
                self.assertEqual(expected, record["state"] if record else None)
                if mode == "used":
                    collector._read_count_window.assert_not_called()
                self.assertNotIn((17, 21), progress.state.observed_counts.values())

    def test_absorb_pending_review_retry_and_late_increment(self):
        for scenario in (
            "default",
            "enabled",
            "late",
            "last",
            "unknown",
            "readonly",
            "wrong_map",
            "two_unknown",
        ):
            with self.subTest(scenario=scenario):
                baseline = 20 if scenario == "last" else 4
                collector, clicks, statuses, progress = self._skill_collector(
                    {ABSORB_ICON.name: ActionIconState.AVAILABLE}, {}
                )
                collector.task.config["未决吸收补点测试"] = scenario in {
                    "enabled",
                    "last",
                    "readonly",
                    "two_unknown",
                }
                progress.reconcile_pending("吸收", (baseline, 21))
                progress.arm_action("Q_sp1", "main_area", "吸收", baseline=(baseline, 21))
                progress.mark_action_blocked("Q_sp1", "main_area", "吸收")

                def ocr(_frame, name, **_kwargs):
                    if name == "探查倒计时":
                        return "87"
                    if scenario == "unknown":
                        return "?"
                    used = baseline + int(scenario == "late")
                    if scenario != "two_unknown":
                        used += len(clicks)
                    return f"{used}/21"

                collector.vision.ocr_text = ocr
                if scenario == "wrong_map":
                    card = CARD_BY_ID["Q_sp1"]
                    collector._collection_target = (card, card.targets[0])
                    collector.navigator.current_collection_target = lambda _: "battle_area_2"
                collector._read_count_window = Collector._read_count_window.__get__(collector)
                collector._read_action_feedback = lambda _: SkillFeedbackObservation("", None)
                result = collector._use_action(
                    ABSORB_ACTION,
                    card_id="Q_sp1",
                    map_role=CollectionMapRole.MAIN_AREA,
                    recovery_only=scenario == "readonly",
                )
                self.assertEqual(scenario in {"enabled", "late"}, result.completed, result.message)
                self.assertEqual(int(scenario in {"enabled", "two_unknown"}), len(clicks))
                if not result.completed:
                    self.assertTrue(result.review_required)
                    self.assertFalse(result.depleted)
                progress.load()
                if scenario in {"enabled", "two_unknown"}:
                    self.assertEqual(6, progress.effective_used("吸收"))
                    self.assertEqual(int(scenario == "enabled"), progress.pending_count("吸收"))
                    again = collector._use_action(
                        ABSORB_ACTION, card_id="Q_sp1", map_role=CollectionMapRole.MAIN_AREA
                    )
                    self.assertEqual(scenario == "enabled", again.completed)
                    self.assertEqual(1, len(clicks))
                if scenario != "wrong_map":
                    self.assertTrue(any(key == "吸收原始次数" for key, _ in statuses))

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
        progress.arm_action("Q_sp2", role, "压制", baseline=(1, 60))
        progress.mark_action_clicked("Q_sp2", role, "压制")
        progress.now_provider = lambda: datetime(2026, 8, 17, 8, tzinfo=UTC_PLUS_8)
        progress.load()
        self.assertFalse(progress.weekly_action_complete("Q_sp1", role, "吸收"))
        self.assertIsNone(progress.get_action_record("Q_sp2", role, "压制"))
        self.assertEqual(0, progress.effective_used("压制"))
        archived = [
            r
            for r in progress.state.archived_action_records.values()
            if r["card_id"] == "Q_sp2" and r["action"] == "压制"
        ]
        self.assertEqual([("archived", [1, 60])], [(r["state"], r["baseline"]) for r in archived])
        self.assertFalse(archived[0]["reservation"])

    def test_summon_and_suppression_require_both_quotas_before_the_first_click(self):
        role = CollectionMapRole.BATTLE_AREA_1
        for case, free, expected in (
            ("both", True, 2),
            ("exhausted", False, 0),
            ("unresolved", False, 0),
            ("failed_summon", False, 1),
            ("stuck_banner", False, 1),
            ("recovered", True, 1),
        ):
            with self.subTest(case=case):
                collector, clicks, _, progress = self._skill_collector(
                    {a.icon.name: ActionIconState.AVAILABLE for a in BATTLE_ACTIONS},
                    {
                        "召集": ((0, 21), (1, 21)),
                        "压制": ((21, 21),) if case == "exhausted" else ((0, 21), (1, 21)),
                    },
                )
                collector._open_skill_menu = Mock(return_value=True)
                now = 0.0
                click_times = []
                clear_samples = []

                def sleep(seconds):
                    nonlocal now
                    now += seconds

                collector.task.sleep = sleep
                if case == "unresolved":
                    progress.reconcile_pending("压制", (0, 21))
                    progress.arm_action("Q_sp1", role, "压制", baseline=(0, 21))
                count, feedback = collector._read_count_window, collector._read_action_feedback
                events = []

                def read(action, *args, **kwargs):
                    events.append(
                        ("post" if kwargs.get("allow_single") else "prepare", action.name)
                    )
                    return count(action, *args, **kwargs)

                def observe(action):
                    events.append(("feedback", action.name))
                    return feedback(action)

                def banner(_frame, _name, **_kwargs):
                    elapsed = now - click_times[0]
                    observe(SUMMON_ACTION)
                    if case == "failed_summon":
                        return "无可召集的战场怪物。"
                    if case == "stuck_banner" or elapsed < 3 or 3.25 <= elapsed < 3.5:
                        clear_samples.clear()
                        return "召集带奖励的战场怪物。"
                    clear_samples.append(elapsed)
                    return ""

                capture = collector._capture_skill_field

                def field():
                    frame, _ = capture()
                    if (case == "recovered" and len(click_times) == 1
                        and not progress.weekly_action_complete("Q_sp1", role, "压制")
                    ):
                        collector._skill_field_generation += 1
                        _seed_action_records(progress, "Q_sp1", role.value, ("压制",))
                        return frame, True
                    return frame, False

                send = collector.vision.click_client

                def dispatch(*args, **kwargs):
                    for action in ("召集", "压制"):
                        self.assertIn(("prepare", action), events)
                    if clicks:
                        self.assertEqual(2, len(clear_samples))
                        self.assertGreaterEqual(clear_samples[0], 3.5)
                        self.assertIn(("feedback", "召集"), events)
                        self.assertNotIn(("post", "召集"), events)
                        self.assertEqual(
                            "clicked", progress.get_action_record("Q_sp1", role, "召集")["state"]
                        )
                    click_times.append(now)
                    send(*args, **kwargs)

                collector._read_count_window = read
                collector._read_action_feedback = observe
                collector._capture_skill_field = field
                collector.vision.ocr_text = banner
                collector.vision.click_client = dispatch
                with patch("src.tasks.map_trade.collector_skills.monotonic", lambda: now):
                    result = collector._use_actions(
                        (SUMMON_ACTION, SUPPRESS_ACTION), card_id="Q_sp1", map_role=role
                    )
                self.assertEqual(free, result.completed)
                self.assertEqual(expected, len(clicks))
                progress.load()
                if case == "stuck_banner":
                    self.assertIsNone(progress.get_action_record("Q_sp1", role, "压制"))
                    self.assertTrue(result.review_required)
                if case == "both":
                    self.assertLess(
                        events.index(("feedback", "压制")), events.index(("post", "召集"))
                    )
                    self.assertTrue(
                        collector._use_actions(
                            (SUMMON_ACTION, SUPPRESS_ACTION), card_id="Q_sp1", map_role=role
                        ).completed
                    )
                    self.assertEqual(2, len(clicks))
                    self.assertEqual(
                        [1, 1], [progress.effective_used(name) for name in ("召集", "压制")]
                    )


class CollectionProbeTest(unittest.TestCase):
    def test_scan_persists_each_visual_observation_in_its_own_weekly_file(self):
        for failed_card in (None, "Q_sp2"):
            with self.subTest(failed=failed_card), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                progress = root / "map_trade_progress.json"
                progress.write_bytes(b'{"sentinel":"collection progress"}')
                before = progress.read_bytes()
                task = BD2MapCollectionProbeTask(SimpleNamespace(scene=None), SimpleNamespace())
                task.config = {
                    **task.default_config,
                    "保存完成度截图": False,
                    "保存每张传送阵地图截图": False,
                }
                task.info_set = task.log_warning = task.log_info = task.sleep = Mock()
                task.save_frame = Mock()
                task.write_probe_text = Mock(side_effect=lambda name, *a, **kw: root / name)
                task._scan_teleport_map_pages = Mock(
                    return_value=TeleportMapScanResult(True, (), 0, 0, "done")
                )
                frame = np.zeros((1080, 1920, 3), np.uint8)
                menu = NavigationResult(True, ScreenState.CARD_MENU)
                field = NavigationResult(True, ScreenState.SANDBOX)
                nav = Mock()
                nav._open_cartridge_quick_switcher.return_value = menu
                nav.open_cartridge_quick_switcher_from_sandbox.return_value = menu
                nav.enter_probe_story_card.return_value = field
                nav._open_teleport_map_anywhere.return_value = NavigationResult(
                    True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.DIRECT_TELEPORT
                )
                nav.return_teleport_map_to_sandbox.return_value = field
                audit = CollectionVisualAuditStore(root / "map_collection_visual_status.json")
                observed = []

                def locate(card_id, *, scan_steps):
                    self.assertEqual(30, scan_steps)
                    self.assertEqual(set(observed), set(audit.load()["cards"]))
                    if card_id == failed_card:
                        return None
                    observed.append(card_id)
                    card = CARD_BY_ID[card_id]
                    return ProbedStoryCard(
                        LocatedStoryCard(card, frame, _badge(card.number)), _completion()
                    )

                nav.locate_probe_story_card.side_effect = locate
                module = "src.tasks.BD2MapCollectionProbeTask"
                with (
                    patch(module + ".Vision"),
                    patch(module + ".Navigator", return_value=nav),
                    patch(
                        "src.tasks.map_trade.audit.ledger_path",
                        side_effect=lambda name: root / name,
                    ),
                ):
                    self.assertEqual(failed_card is None, task.run())
                cards = list(COLLECTABLE_CARDS) if failed_card is None else [CARD_BY_ID["Q_sp1"]]
                self.assertEqual([c.card_id for c in cards], observed)
                self.assertEqual(
                    [c.number for c in cards],
                    [c.args[0] for c in nav.return_teleport_map_to_sandbox.call_args_list],
                )
                self.assertEqual(len(cards), task._scan_teleport_map_pages.call_count)
                self.assertEqual(
                    len(cards) + int(failed_card is not None),
                    nav.locate_probe_story_card.call_count,
                )
                state = audit.load()
                self.assertEqual(set(observed), set(state["cards"]))
                self.assertEqual(failed_card is None, state["last_scan"]["completed"])
                self.assertEqual(before, progress.read_bytes())
                task.save_frame.assert_not_called()


if __name__ == "__main__":
    unittest.main()
