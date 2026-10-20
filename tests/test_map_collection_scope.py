import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from src.tasks import MapCollectionTask as map_collection_task_module
from src.tasks.map_trade import event_card_art, navigator_story
from src.tasks.map_trade.card_status import (
    CollectionCardSelectionOutcome,
    CollectionCardSelectionResult,
)
from src.tasks.map_trade.collection_selection import (
    COLLECTION_CARD_CATALOG,
    COLLECTION_CARD_NAMES,
    parse_card_blacklist,
    resolve_collection_cards,
)
from src.tasks.map_trade.collector import Collector
from src.tasks.map_trade.collector_constants import (
    BATTLE_ACTIONS,
    SUPPRESS_ACTION,
    SkillExecutionResult,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    EVENT_CARDS,
    CollectionResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import UTC_PLUS_8, ProgressStore
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.run_history import is_successful_run
from tests.helpers.map_trade import _seed_action_records
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


class CollectionCatalogTest(unittest.TestCase):
    def test_catalog_runs_story_character_then_event_cards(self):
        cards = tuple(card for _name, card in COLLECTION_CARD_CATALOG)
        self.assertEqual(COLLECTABLE_CARDS + EVENT_CARDS, cards)
        self.assertNotIn("Q_sp6", {card.card_id for card in cards})
        self.assertEqual("Story_01_血骑士", COLLECTION_CARD_NAMES[0])
        self.assertIn("Character_1_杰登之门", COLLECTION_CARD_NAMES)
        self.assertEqual(
            ["Event_1_夏日骑士", "Event_2_恶梦之冬", "Event_3_海滨天使",
             "Event_4_记忆边缘", "Event_5_戏水女王"],
            [name for name in COLLECTION_CARD_NAMES if name.startswith("Event_")],
        )

    def test_selected_cards_keep_their_named_routes(self):
        self.assertEqual("剑之神殿左侧回廊", CARD_BY_ID["Q_sp14"].targets[1].title)
        for card in EVENT_CARDS:
            with self.subTest(card=card.card_id):
                self.assertEqual("event", card.category)
                self.assertEqual(3 if card.number == 2 else 2, len(card.targets))

    def test_selection_respects_custom_cards_and_blacklist(self):
        role = "Character_2_火晶片"
        event = "Event_3_海滨天使"
        story = COLLECTION_CARD_NAMES[0]
        for config, expected in (
            ({}, COLLECTION_CARD_NAMES),
            ({"自定义采集关卡": True}, COLLECTION_CARD_NAMES),
            ({"自定义采集关卡": True, "采集关卡": [event, role, story]}, (story, role, event)),
            ({"自定义采集关卡": True, "采集关卡": [role, event],
              "采集卡带屏蔽": "Character_2"}, (event,)),
            ({"采集卡带屏蔽": "Story_3, Event_4"}, tuple(
                name for name in COLLECTION_CARD_NAMES
                if not name.startswith(("Story_03_", "Event_4_"))
            )),
        ):
            with self.subTest(config=config):
                cards, error = resolve_collection_cards(config)
                self.assertEqual("", error)
                names = dict((card.card_id, name) for name, card in COLLECTION_CARD_CATALOG)
                self.assertEqual(expected, tuple(names[card.card_id] for card in cards))

    def test_short_story_prefix_does_not_block_later_chapters(self):
        blacklist = parse_card_blacklist("Story_1")
        cards, error = resolve_collection_cards({"采集卡带屏蔽": "Story_1"})
        self.assertIn("Story_01", blacklist)
        self.assertEqual("", error)
        self.assertNotIn("Q_sp1", {card.card_id for card in cards})
        self.assertIn("Q_sp10", {card.card_id for card in cards})

    def test_invalid_or_fully_blocked_selection_is_rejected(self):
        role = "Character_1_杰登之门"
        for config in (
            {"自定义采集关卡": True, "采集关卡": []},
            {"自定义采集关卡": True, "采集关卡": ["Unknown_1"]},
            {"自定义采集关卡": True, "采集关卡": ["Character_8_妖魔讨伐团"]},
            {"自定义采集关卡": True, "采集关卡": role},
            {"自定义采集关卡": True, "采集关卡": [role], "采集卡带屏蔽": "Character_1"},
            {"采集卡带屏蔽": ", ".join(COLLECTION_CARD_NAMES)},
        ):
            with self.subTest(config=config):
                cards, error = resolve_collection_cards(config)
                self.assertEqual((), cards)
                self.assertTrue(error)


class MapCollectionTaskScopeTest(unittest.TestCase):
    def make_task(self):
        task = object.__new__(MapCollectionTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch.object(map_collection_task_module.MapAutomationTaskBase, "__init__",
                          return_value=None):
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

    def test_selection_controls_default_to_every_card(self):
        task = self.make_task()
        self.assertFalse(task.default_config["自定义采集关卡"])
        self.assertEqual(list(COLLECTION_CARD_NAMES), task.default_config["采集关卡"])
        self.assertFalse(task.default_config["单章模式"])
        self.assertTrue(task.default_config["启用压制技能"])
        self.assertEqual(
            {"type": "multi_selection", "options": list(COLLECTION_CARD_NAMES)},
            task.config_type["采集关卡"],
        )
        self.assertEqual(
            {"sub_configs": {True: ["采集关卡"]}}, task.config_type["自定义采集关卡"],
        )
        nested = task.config_type["执行地图采集"]["sub_configs"][True]
        for key in ("自定义采集关卡", "单章模式", "采集卡带屏蔽", "启用压制技能"):
            self.assertIn(key, nested)

    def test_invalid_selection_fails_before_opening_the_ledger(self):
        task = self.make_task()
        task.config.update({"自定义采集关卡": True, "采集关卡": []})
        with (
            patch.object(map_collection_task_module, "ProgressStore") as store,
            patch.object(map_collection_task_module, "Navigator") as navigator,
        ):
            self.assertFalse(task.run())
        store.assert_not_called()
        navigator.assert_not_called()
        self.assertIn("未执行", task.info["状态"])
        self.assertFalse(is_successful_run(task.info))

    def test_run_passes_selected_scope_and_switches_to_collector(self):
        for suppress, only_once in ((True, False), (False, True)):
            with self.subTest(suppress=suppress, only_once=only_once):
                task = self.make_task()
                task.config.update({
                    "自定义采集关卡": True,
                    "采集关卡": ["Event_1_夏日骑士", "Story_02_苍蓝魔女"],
                    "启用压制技能": suppress,
                    "单章模式": only_once,
                })
                calls = []

                class FakeCollector:
                    def __init__(self, *_args):
                        pass

                    def run(self, cards, **kwargs):
                        calls.append((cards, kwargs))
                        return CollectionResult(True)

                    def scope_complete(self):
                        return True

                progress = SimpleNamespace(
                    load=lambda: None,
                    state=SimpleNamespace(weekly_key="2026-10-12", daily_key="2026-10-13"),
                )
                store = MagicMock()
                store.return_value.__enter__.return_value = progress
                store.return_value.__exit__.return_value = False
                navigator = SimpleNamespace(
                    return_home=lambda: NavigationResult(True, ScreenState.HOME),
                )
                with (
                    patch.object(map_collection_task_module, "Vision", lambda *_args: object()),
                    patch.object(map_collection_task_module, "ProgressStore", store),
                    patch.object(map_collection_task_module, "Navigator",
                                 lambda *_args: navigator),
                    patch.object(map_collection_task_module, "Collector", FakeCollector),
                ):
                    self.assertTrue(task.run())
                cards, kwargs = calls[0]
                self.assertEqual(["Q_sp2", "Q_ep1"], [card.card_id for card in cards])
                self.assertEqual(only_once, kwargs["only_once"])
                self.assertEqual(suppress, SUPPRESS_ACTION in kwargs["battle_actions"])
                self.assertEqual("本周", task.info["跑图完成范围"])
                self.assertEqual("2026-10-12", task.info["跑图周期"])


class CollectorScopeTest(unittest.TestCase):
    def make_collector(self, progress, navigator=None):
        task = SimpleNamespace(
            config={"卡带单步重试次数": 1},
            info_set=Mock(), log_info=Mock(), log_warning=Mock(), log_error=Mock(),
        )
        navigator = navigator or SimpleNamespace(
            return_home=Mock(return_value=NavigationResult(True, ScreenState.HOME)),
        )
        return Collector(task, object(), navigator, progress)

    def test_only_event_cards_missing_from_their_tab_are_marked_missing(self):
        for card_id, not_found, expected in (
            ("Q_ep1", True, True),
            ("Q_ep1", False, False),
            ("Q_sp1", True, False),
        ):
            with self.subTest(card_id=card_id, not_found=not_found), \
                    tempfile.TemporaryDirectory() as directory:
                progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
                progress.load()
                navigator = SimpleNamespace(
                    select_collection_card=Mock(return_value=CollectionCardSelectionResult(
                        CollectionCardSelectionOutcome.FAILED,
                        NavigationResult(
                            False, ScreenState.CARD_MENU, "未唯一确认", card_not_found=not_found,
                        ),
                    )),
                )
                result = self.make_collector(progress, navigator).collect_card(
                    CARD_BY_ID[card_id], card_retries=1,
                )
                self.assertFalse(result.success)
                self.assertEqual(expected, result.card_missing)

    def test_missing_event_card_is_skipped_without_failing_or_blocking_the_week(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
            progress.load()
            story, event = CARD_BY_ID["Q_sp1"], CARD_BY_ID["Q_ep1"]
            collector = self.make_collector(progress)

            def collect(card, **_kwargs):
                if card is event:
                    return CollectionResult(False, message="未唯一确认", card_missing=True)
                _complete_card(progress, card)
                return None

            collector.collect_card = Mock(side_effect=collect)
            result = collector.run((story, event))

        self.assertTrue(result.success)
        self.assertFalse(result.partial)
        self.assertIn("活动卡1·夏日骑士未在活动游戏卡页找到，已跳过", result.message)
        self.assertEqual([event], collector.missing_cards)
        self.assertTrue(collector.scope_complete())
        collector.navigator.return_home.assert_not_called()
        collector.task.log_warning.assert_called_once()

    def test_unfinished_selected_card_keeps_the_week_open(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
            progress.load()
            collector = self.make_collector(progress)
            collector.collect_card = Mock(return_value=CollectionResult(True, depleted=True))
            result = collector.run((CARD_BY_ID["Q_sp1"], CARD_BY_ID["Q_ep1"]))
        self.assertTrue(result.depleted)
        self.assertFalse(collector.scope_complete())

    def test_only_once_stops_after_the_first_newly_completed_card(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
            progress.load()
            done, fresh, later = (CARD_BY_ID[card_id] for card_id in ("Q_sp1", "Q_sp2", "Q_sp3"))
            _complete_card(progress, done)
            collector = self.make_collector(progress)

            def collect(card, **_kwargs):
                if card is fresh:
                    _complete_card(progress, card)
                return None

            collector.collect_card = Mock(side_effect=collect)
            result = collector.run((done, fresh, later), only_once=True)

        self.assertTrue(result.success)
        self.assertIn("单章模式", result.message)
        self.assertEqual(
            [done, fresh], [call.args[0] for call in collector.collect_card.call_args_list],
        )

    def test_only_once_keeps_a_failure_from_before_the_finished_card(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
            progress.load()
            failed, fresh, later = (
                CARD_BY_ID[card_id] for card_id in ("Q_sp1", "Q_sp2", "Q_sp3")
            )
            collector = self.make_collector(progress)

            def collect(card, **_kwargs):
                if card is failed:
                    return CollectionResult(False, message="进入失败")
                _complete_card(progress, card)
                return None

            collector.collect_card = Mock(side_effect=collect)
            result = collector.run((failed, fresh, later), only_once=True)

        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(
            "第1章未完成，已跳过；单章模式：第2章已完成，本次运行结束。", result.message,
        )
        self.assertEqual(
            [failed, fresh], [call.args[0] for call in collector.collect_card.call_args_list],
        )
        collector.navigator.return_home.assert_called_once()

    def test_selected_scope_already_complete_skips_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
            progress.load()
            card = CARD_BY_ID["Q_sp1"]
            reduced = tuple(action for action in BATTLE_ACTIONS if action is not SUPPRESS_ACTION)
            _complete_card(progress, card, required_actions=("吸收", "召集"))
            collector = self.make_collector(progress)
            collector.collect_card = Mock()
            result = collector.run((card,), battle_actions=reduced)
            self.assertTrue(result.success)
            collector.collect_card.assert_not_called()
            self.assertTrue(collector.scope_complete())
            # The full policy still requires the suppress action on the same card.
            collector.collect_card = Mock(return_value=None)
            collector.run((card,))
            collector.collect_card.assert_called_once()

    def test_event_visual_completion_follows_the_suppress_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
            progress.load()
            card = CARD_BY_ID["Q_ep2"]
            progress.mark_card_visual_complete(card.card_id)
            progress.reconcile_pending("压制", (75, 75))
            for suppress_enabled in (True, False):
                with self.subTest(suppress_enabled=suppress_enabled):
                    sandbox = NavigationResult(True, ScreenState.SANDBOX)
                    navigator = SimpleNamespace(
                        select_collection_card=Mock(return_value=CollectionCardSelectionResult(
                            CollectionCardSelectionOutcome.ENTERED, sandbox,
                        )),
                        current_collection_target=lambda _card: card.targets[0].key,
                        advance_collection_map=Mock(return_value=sandbox),
                        open_cartridge_quick_switcher_from_sandbox=Mock(
                            return_value=NavigationResult(True, ScreenState.CARD_MENU),
                        ),
                        return_home=Mock(return_value=NavigationResult(True, ScreenState.HOME)),
                    )
                    collector = self.make_collector(progress, navigator)
                    collector._absorb_used_up = Mock(return_value=None)
                    collector._ensure_search = Mock(return_value=None)

                    def use_actions(actions, *, card_id, map_role):
                        _seed_action_records(
                            progress, card_id, map_role.value,
                            actions=tuple(action.name for action in actions),
                        )
                        return SkillExecutionResult(True)

                    collector._use_actions = Mock(side_effect=use_actions)
                    battle_actions = BATTLE_ACTIONS if suppress_enabled else tuple(
                        action for action in BATTLE_ACTIONS if action is not SUPPRESS_ACTION
                    )
                    result = collector.run((card,), battle_actions=battle_actions)
                    self.assertTrue(result.success, result.message)
                    self.assertTrue(collector.scope_complete())
                    self.assertEqual(
                        0 if suppress_enabled else 3, collector._use_actions.call_count,
                    )
            self.assertTrue(progress.state.card_verified(card.card_id, ("吸收", "召集")))
            self.assertEqual((75, 75), progress.state.observed_counts["压制"])


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
            info_set=Mock(), log_warning=Mock(),
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )
        vision = SimpleNamespace(
            capture=lambda: frames.pop(0) if len(frames) > 1 else frames[0],
        )
        navigator = Navigator(task, vision)
        navigator.classify = Mock(return_value=ScreenState.CARD_MENU)
        navigator._wait_for_quick_switch_page = Mock(side_effect=[True, page_after])
        navigator._wait_for_cartridge_category = Mock(side_effect=[True, tab_after])
        navigator.return_home = Mock(return_value=NavigationResult(True, ScreenState.HOME))
        patcher = patch.object(navigator_story, "monotonic", lambda: clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        return navigator

    def test_cover_reasons_separate_absence_from_invalid_frames(self):
        self.assertTrue(event_card_art.is_absent_reason(
            event_card_art.find_card(_event_bar_frame(held=False), 1).reason,
        ))
        for frame in (None, np.full((1080, 1920, 3), 40, dtype=np.uint8)):
            with self.subTest(frame=None if frame is None else "solid"):
                reason = event_card_art.find_card(frame, 1).reason
                self.assertEqual("活动卡带画面无效", reason)
                self.assertFalse(event_card_art.is_absent_reason(reason))

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
                navigator = self.make_navigator(
                    frames, page_after=page_after, tab_after=tab_after,
                )
                located = navigator._locate_story_card("Q_ep1")
                self.assertIsInstance(located, NavigationResult)
                self.assertFalse(located.success)
                self.assertEqual(expected, located.card_not_found)
                self.assertEqual(
                    2 if rechecked else 1, navigator._wait_for_quick_switch_page.call_count,
                )
                if expected:
                    navigator._wait_for_cartridge_category.assert_called_with(category="event")

    def test_held_cover_is_located_and_story_misses_are_never_absence(self):
        navigator = self.make_navigator([_event_bar_frame()])
        self.assertNotIsInstance(navigator._locate_story_card("Q_ep1"), NavigationResult)

        navigator = self.make_navigator([_event_bar_frame()])
        navigator._event_card_scan_absent = True
        navigator._wait_for_story_badge_with_scroll = Mock(return_value=None)
        located = navigator._locate_story_card("Q_sp1")
        self.assertFalse(located.card_not_found)

    def test_invalid_screenshot_keeps_the_event_card_in_this_week(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = ProgressStore(Path(directory) / "progress.json", lambda: NOW)
            progress.load()
            story, event = CARD_BY_ID["Q_sp1"], CARD_BY_ID["Q_ep1"]
            _complete_card(progress, story)
            navigator = self.make_navigator([None])
            states = iter([ScreenState.CARD_MENU])
            navigator.classify = lambda: next(states, ScreenState.UNKNOWN)
            collector = Collector(
                SimpleNamespace(
                    config={"卡带单步重试次数": 1},
                    info_set=Mock(), log_info=Mock(), log_warning=Mock(), log_error=Mock(),
                ),
                navigator.vision, navigator, progress,
            )
            result = collector.run((story, event))

        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual([], collector.missing_cards)
        self.assertFalse(collector.scope_complete())
        navigator.return_home.assert_called_once()


if __name__ == "__main__":
    unittest.main()
