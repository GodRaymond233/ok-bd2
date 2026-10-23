"""Map-trade collection tests (split from test_map_trade.py)."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks.BaseBD2Task import CartridgeSpecialPageResult
from src.tasks.map_trade.card_status import (
    CardActionDetection,
    CardActionState,
    CollectionCardSelectionOutcome,
    CollectionCardSelectionResult,
    StoryCardCompletion,
)
from src.tasks.map_trade.collector import Collector
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import (
    FIRST_CARD_CONFIRM_REGION,
    FIRST_CARD_INSERT_REGION,
    FIRST_CARD_SKIP_TEMPLATE,
    PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
    PROBE_QUICK_SWITCH_SCROLL_COUNT,
    PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_POINT,
    PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    PROBE_STORY_BADGE_CONFIRM_SECONDS,
    QUICK_SWITCH_SCROLL_POINT,
    STORY_CATEGORY_POINT,
    STORY_SANDBOX_STABLE_HITS,
    LocatedStoryCard,
    Navigator,
    ProbedStoryCard,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.navigator_constants import (
    CHARACTER_CATEGORY_HIGHLIGHT_REGION,
    CHARACTER_CATEGORY_POINT,
    EVENT_CATEGORY_HIGHLIGHT_REGION,
    EVENT_CATEGORY_POINT,
    STORY_CATEGORY_HIGHLIGHT_REGION,
)
from src.tasks.map_trade.progress import (
    UTC_PLUS_8,
    ProgressStore,
)
from src.tasks.map_trade.vision import normalize_text


class StoryBarScrollTest(unittest.TestCase):
    def test_scroll_requires_stable_page_and_movement_without_clicking(self):
        before = np.zeros((1080, 1920, 3), dtype=np.uint8)
        moved = before.copy()
        moved[924:1015, 200:500] = 100
        changing = moved.copy()
        changing[924:1015, 800:1400] = 255
        for label, frames, page, require_movement, expected in (
            ("moved", [before, moved, moved, moved], True, True, True),
            ("unchanged", [before] * 4, True, True, False),
            ("reset_at_edge", [before] * 4, True, False, True),
            ("page_lost", [before, moved, moved, moved], False, True, False),
            ("moving", [before] + [moved, changing] * 6, True, True, False),
            ("resized", [before, moved[:720]], True, True, False),
        ):
            with self.subTest(case=label):
                captures = iter(frames)
                scrolls = []
                task = SimpleNamespace(
                    info_set=lambda *_args: None,
                    sleep=lambda *_args: None,
                    scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
                    operate_click=lambda *_args, **_kwargs: self.fail("scroll selected a card"),
                )
                navigator = Navigator(task, SimpleNamespace(capture=lambda: next(captures)))
                navigator._wait_for_quick_switch_page = lambda **_kwargs: page
                result = navigator._scroll_cartridge_bar(
                    QUICK_SWITCH_SCROLL_POINT, 1, count=2, interval=0.08,
                    after_sleep=0.35, require_movement=require_movement,
                )
                self.assertEqual(expected, result)
                self.assertEqual(1, len(scrolls))


class CollectionCardTest(unittest.TestCase):
    def setUp(self):
        # Selection tests isolate the verified wheel input boundary. Its frame
        # stability and page-loss behavior are exercised by StoryBarScrollTest.
        def scroll(navigator, *args, require_movement=True, **kwargs):
            navigator.task.scroll_client(*args, **kwargs)
            return True

        patcher = patch.object(
            Navigator, "_scroll_cartridge_bar", autospec=True, side_effect=scroll,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        category = patch.object(Navigator, "_cartridge_category_confirmed", return_value=True)
        category.start()
        self.addCleanup(category.stop)

    def test_completion_policy_does_not_ignore_unrepresented_summon(self):
        completed = StoryCardCompletion(
            absorb=CardActionDetection(CardActionState.COMPLETED),
            suppress=CardActionDetection(CardActionState.COMPLETED),
            bounds=(0, 0, 100, 100),
            complete_region=True,
        )

        self.assertFalse(
            Navigator._completion_satisfies_actions(
                completed,
                required_actions=("吸收", "召集"),
            )
        )
        absorb_only = StoryCardCompletion(
            absorb=CardActionDetection(CardActionState.COMPLETED),
            suppress=CardActionDetection(CardActionState.UNKNOWN),
            bounds=(0, 0, 100, 100), complete_region=True,
        )
        self.assertTrue(
            Navigator._completion_satisfies_actions(absorb_only, card_id="Q_ep2"),
        )
        self.assertFalse(
            Navigator._completion_satisfies_actions(absorb_only, card_id="Q_ep3"),
        )
        self.assertFalse(
            Navigator._completion_satisfies_actions(
                absorb_only, required_actions=("吸收", "召集"), card_id="Q_ep2",
            ),
        )

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
                    ScreenState.SANDBOX if recovered[0] else
                    ScreenState.LOADING if clock[0] < .5 else ScreenState.UNKNOWN
                )
                navigator._handle_story_card_intermediate = lambda frame: False
                with patch("src.tasks.map_trade.navigator_sandbox.monotonic", lambda: clock[0]):
                    result = navigator._wait_for_story_sandbox(1, timeout=2.0, interval=.1)
                self.assertEqual(expected, result.success)
                self.assertTrue(task._handle_recent_cartridge_special_pages.called)
                if reward_result is not CartridgeSpecialPageResult.ABSENT:
                    task._handle_recent_cartridge_special_pages.assert_called_once()
                self.assertLessEqual(clock[0], 2.1)

    def test_collection_card_selection_uses_common_quick_switch_and_badge_center(self):
        clicks = []
        client_clicks = []
        template_clicks = []
        opened_callbacks = []
        badge_targets = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                1,
                MatchResult(0.99, (300, 930), (30, 28), pixel_score=0.98),
            ),
            runner_up=StoryBadgeCandidate(
                7,
                MatchResult(0.80, (301, 930), (31, 31), pixel_score=0.82),
            ),
        )

        def open_quick_switcher(**callbacks):
            opened_callbacks.append(tuple(callbacks))
            self.assertEqual(navigator._recent_field_ready, callbacks["entry_ready"])
            return (
                callbacks["ensure_home"]()
                and callbacks["click_quick_switch"]()
                and callbacks["confirm_quick_switch_page"]()
            )

        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep)),
            open_cartridge_quick_switcher=open_quick_switcher,
            log_warning=lambda *_args, **_kwargs: None,
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda: ScreenState.HOME
        navigator._click_quick_switch = lambda timeout, after_sleep: (
            template_clicks.append((timeout, after_sleep)) or True
        )
        navigator.return_home = lambda: NavigationResult(True, ScreenState.HOME)
        navigator._wait_for_cartridge_home = lambda: True
        navigator._wait_for_quick_switch_page = lambda: True
        navigator._wait_for_cartridge_category = lambda **_kwargs: True
        navigator._wait_for_story_badge_with_scroll = lambda number: (
            badge_targets.append(number) or (frame, badge)
        )
        navigator._find_story_badge = lambda _frame, _number: (badge, "")
        navigator._wait_for_story_sandbox = lambda number: NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"Q_sp{number}",
        )

        result = navigator.select_card("Q_sp1")

        self.assertTrue(result.success)
        self.assertEqual("Q_sp1", result.message)
        self.assertEqual([1], badge_targets)
        self.assertEqual(
            [("ensure_home", "click_quick_switch", "confirm_quick_switch_page", "entry_ready")],
            opened_callbacks,
        )
        self.assertEqual([(45.0, 1.0)], template_clicks)
        self.assertEqual([(*STORY_CATEGORY_POINT, 0.5)], clicks)
        self.assertEqual([(badge.best.result.center, frame.shape, 1.0)], client_clicks)

    def test_collection_card_visual_completion_is_checked_before_clicking(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                1,
                MatchResult(
                    0.99,
                    (80, 921),
                    (29, 29),
                    pixel_score=0.98,
                    zncc_score=0.97,
                ),
            ),
            runner_up=StoryBadgeCandidate(
                7,
                MatchResult(
                    0.80,
                    (81, 921),
                    (29, 29),
                    pixel_score=0.82,
                    zncc_score=0.70,
                ),
            ),
        )
        located = LocatedStoryCard(CARD_BY_ID["Q_sp1"], frame, badge)
        completion = StoryCardCompletion(
            absorb=CardActionDetection(CardActionState.COMPLETED),
            suppress=CardActionDetection(CardActionState.COMPLETED),
            bounds=(78, 1015, 258, 1065),
            complete_region=True,
        )
        statuses = []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *values: statuses.append(values),
                log_warning=lambda *_args: None,
            ),
            SimpleNamespace(),
        )
        navigator._locate_story_card = lambda _card_id: located
        navigator.vision.capture = lambda: frame
        navigator._find_story_badge = lambda *_args: (badge, "")
        navigator.card_status = SimpleNamespace(
            detect=lambda detected_frame, center: (
                self.assertIs(frame, detected_frame)
                or self.assertEqual(badge.best.result.center, center)
                or completion
            )
        )
        navigator._click_located_story_card = lambda _located: self.fail(
            "visually completed card must not be clicked"
        )

        result = navigator.select_collection_card("Q_sp1")

        self.assertTrue(result.success)
        self.assertEqual(
            CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
            result.outcome,
        )
        self.assertEqual(ScreenState.CARD_MENU, result.state)
        self.assertIs(completion, result.completion)
        self.assertIn(("卡带完成度", "completed"), statuses)

    def test_formal_collection_skips_card_when_preentry_status_is_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            selected = []
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )

            def select(card_id, *, enter_visually_complete):
                selected.append((card_id, enter_visually_complete))
                return CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    NavigationResult(True, ScreenState.CARD_MENU, "视觉完成"),
                )

            navigator = SimpleNamespace(
                select_collection_card=select,
                prepare_collection_main_area=lambda *_args: self.fail(
                    "preentry-complete card must not enter the sandbox"
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            with patch(
                "src.tasks.map_trade.collector.COLLECTABLE_CARDS",
                (CARD_BY_ID["Q_sp1"],),
            ):
                result = collector.run()

        self.assertTrue(result.success)
        self.assertEqual([("Q_sp1", False)], selected)
        self.assertEqual(
            {"main_area", "battle_area_1", "battle_area_2"},
            progress.state.completed_targets("Q_sp1"),
        )
        self.assertTrue(progress.state.card_verified("Q_sp1"))

    def test_formal_collection_skips_card_with_custom_policy_when_visually_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            progress = ProgressStore(
                Path(temp_dir) / "progress.json",
                lambda: datetime(2026, 8, 3, 12, tzinfo=UTC_PLUS_8),
            )
            progress.load()
            task = SimpleNamespace(
                config={"卡带单步重试次数": 1},
                log_warning=lambda *_args: None,
                info_set=lambda *_args: None,
            )
            navigator = SimpleNamespace(
                select_collection_card=lambda *args, **kwargs: CollectionCardSelectionResult(
                    CollectionCardSelectionOutcome.VISUALLY_COMPLETE,
                    NavigationResult(True, ScreenState.CARD_MENU, "视觉完成"),
                ),
            )
            collector = Collector(task, object(), navigator, progress)
            result = collector.collect_card(
                CARD_BY_ID["Q_sp1"],
                battle_actions=("压制",),
            )
            self.assertIsNone(result)
            self.assertTrue(
                progress.state.card_verified("Q_sp1", required_actions=("吸收", "压制"))
            )

    def test_collection_card_pending_or_unknown_status_still_enters(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                1,
                MatchResult(0.99, (80, 921), (29, 29), pixel_score=0.98),
            ),
            runner_up=None,
        )
        located = LocatedStoryCard(CARD_BY_ID["Q_sp1"], frame, badge)
        entered = []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *_args: None,
                log_warning=lambda *_args: None,
            ),
            SimpleNamespace(),
        )
        navigator._locate_story_card = lambda _card_id: located
        navigator.vision.capture = lambda: frame
        navigator._find_story_badge = lambda *_args: (badge, "")
        navigator._click_located_story_card = lambda value: (
            entered.append(value) or NavigationResult(True, ScreenState.SANDBOX, "Q_sp1")
        )

        for state in (CardActionState.PENDING, CardActionState.UNKNOWN):
            with self.subTest(state=state):
                completion = StoryCardCompletion(
                    absorb=CardActionDetection(state),
                    suppress=CardActionDetection(CardActionState.COMPLETED),
                    bounds=(78, 1015, 258, 1065),
                    complete_region=True,
                )
                navigator.card_status = SimpleNamespace(
                    detect=lambda *_args, value=completion: value
                )

                result = navigator.select_collection_card("Q_sp1")

                self.assertEqual(
                    CollectionCardSelectionOutcome.ENTERED,
                    result.outcome,
                )
        self.assertEqual([located, located], entered)

    def test_collection_completion_api_rejects_non_collection_story_cards(self):
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        navigator._locate_story_card = lambda _card_id: self.fail(
            "non-collection card must not be located"
        )

        result = navigator.select_collection_card("Q_sp20")

        self.assertFalse(result.success)
        self.assertEqual(CollectionCardSelectionOutcome.FAILED, result.outcome)
        self.assertIn("非跑图剧情卡带", result.message)

    def test_collection_decisions_bind_fresh_identity_structure_and_status(self):
        for scenario in (
            "complete", "pending", "became_complete", "lost_complete",
            "moved", "renumbered", "category", "resized",
        ):
            with self.subTest(scenario=scenario):
                first = np.zeros((1080, 1920, 3), dtype=np.uint8)
                fresh = np.ones((720, 1280, 3) if scenario == "resized" else first.shape,
                                dtype=np.uint8)

                def badge(number, x):
                    return StoryBadgeDetection(StoryBadgeCandidate(
                        number, MatchResult(0.99, (x, 922), (30, 28), 0.98, 0.97),
                    ), None)

                initial = badge(12, 300)
                updated = badge(13 if scenario == "renumbered" else 12,
                                480 if scenario == "moved" else 302)
                located = LocatedStoryCard(CARD_BY_ID["Q_sp12"], first, initial)
                readings = []

                def status(frame, center):
                    readings.append((frame, center))
                    self.assertEqual(initial.best.result.center if frame is first
                                     else updated.best.result.center, center)
                    if scenario == "became_complete":
                        state = (
                            CardActionState.PENDING if frame is first else CardActionState.COMPLETED
                        )
                    elif scenario == "lost_complete":
                        state = (
                            CardActionState.COMPLETED if frame is first else CardActionState.UNKNOWN
                        )
                    else:
                        state = (
                            CardActionState.PENDING if scenario == "pending"
                            else CardActionState.COMPLETED
                        )
                    return StoryCardCompletion(
                        CardActionDetection(state), CardActionDetection(state), (0, 0, 1, 1), True,
                    )

                navigator = Navigator(
                    SimpleNamespace(info_set=Mock(), log_warning=Mock(), sleep=Mock()),
                    SimpleNamespace(capture=lambda: fresh),
                )
                navigator._locate_story_card = lambda _: located
                navigator._find_story_badge = lambda *_: (updated, "")
                navigator._cartridge_category_confirmed = lambda *_: scenario != "category"
                navigator.card_status = SimpleNamespace(detect=status)
                navigator._click_located_story_card = Mock(
                    return_value=NavigationResult(True, ScreenState.SANDBOX),
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

    def test_collection_status_detection_error_stops_without_clicking(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        located = LocatedStoryCard(
            CARD_BY_ID["Q_sp1"],
            frame,
            StoryBadgeDetection(
                best=StoryBadgeCandidate(
                    1,
                    MatchResult(0.99, (80, 921), (29, 29), pixel_score=0.98),
                ),
                runner_up=None,
            ),
        )
        warnings = []
        navigator = Navigator(
            SimpleNamespace(
                info_set=lambda *_args: None,
                log_warning=lambda value: warnings.append(value),
            ),
            SimpleNamespace(),
        )
        navigator._locate_story_card = lambda _card_id: located
        navigator.card_status = SimpleNamespace(
            detect=lambda *_args: (_ for _ in ()).throw(RuntimeError("template missing"))
        )
        navigator._click_located_story_card = Mock()

        result = navigator.select_collection_card("Q_sp1")

        self.assertEqual(CollectionCardSelectionOutcome.FAILED, result.outcome)
        self.assertIsNone(result.completion)
        navigator._click_located_story_card.assert_not_called()

    def _story_window(self, offset=0, wheel_pixels=180):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        state = SimpleNamespace(
            offset=offset, scrolls=[], scans=[], omitted=set(), centers={},
            missing_reason="未达到双阈值", order=tuple(range(1, 21)),
        )

        def scroll(point, amount, *, count, **_kwargs):
            self.assertEqual(QUICK_SWITCH_SCROLL_POINT, point)
            self.assertLessEqual(count, 8)
            state.scrolls.append((amount, count))
            previous_offset = state.offset
            state.offset = min(1800, max(0, state.offset + amount * count * wheel_pixels))
            for number in state.centers:
                state.centers[number] += previous_offset - state.offset

        def inspect(captured, numbers, **_kwargs):
            self.assertIs(frame, captured)
            self.assertEqual(tuple(range(1, 21)), tuple(numbers))
            state.scans.append(state.offset)
            inspected = {}
            for number in range(1, 21):
                x = state.centers.get(number, 95 + 180 * state.order.index(number) - state.offset)
                if number in state.omitted or not 0 <= x < 1920:
                    inspected[number] = (None, state.missing_reason)
                    continue
                inspected[number] = (StoryBadgeDetection(
                    best=StoryBadgeCandidate(number, MatchResult(
                        0.99, (x - 15, 922), (30, 28), pixel_score=0.98, zncc_score=0.98,
                    )),
                    runner_up=StoryBadgeCandidate(number % 20 + 1, MatchResult(
                        0.75, (x - 15, 922), (30, 28), pixel_score=0.75, zncc_score=0.75,
                    )),
                ), "")
            return inspected

        navigator = Navigator(SimpleNamespace(
            scroll_client=scroll,
            operate_click=lambda *_args, **_kwargs: self.fail("locating selected a card"),
            log_warning=lambda *_args, **_kwargs: None,
            info_set=lambda *_args: None,
        ), SimpleNamespace(capture=lambda: frame))
        navigator.inspect_story_badges = inspect
        return navigator, state

    def test_collection_card_scroll_uses_current_visible_window(self):
        for offset, targets in ((0, (4,)), (1260, (14, 15))):
            with self.subTest(offset=offset):
                navigator, state = self._story_window(offset)
                for number in targets:
                    found = navigator._wait_for_story_badge_with_scroll(number)
                    self.assertIsNotNone(found)
                    self.assertEqual(number, found[1].best.number)
                self.assertEqual([], state.scrolls)
                self.assertEqual(len(targets), len(state.scans))

    def test_collection_card_scroll_learns_direction_and_distance(self):
        pinned_order = (
            6, 18, 20, *(number for number in range(1, 21) if number not in (6, 18, 20)),
        )
        for label, offset, target, wheel_pixels, budget, order in (
            ("later", 0, 14, 180, 5, None),
            ("earlier", 1440, 2, 180, 8, None),
            ("reversed", 360, 18, -180, 9, None),
            ("reversed_at_edge", 0, 14, -180, 6, None),
            ("pinned_cards", 0, 14, 180, 8, pinned_order),
            ("pinned_18_from_middle", 1080, 18, 180, 30, pinned_order),
            ("pinned_20_from_middle", 1080, 20, 180, 30, pinned_order),
            ("pinned_slow_wheel", 360, 6, 60, 64, pinned_order),
        ):
            with self.subTest(case=label):
                navigator, state = self._story_window(offset, wheel_pixels)
                if order is not None:
                    state.order = order
                found = navigator._wait_for_story_badge_with_scroll(target, scan_steps=budget)
                self.assertIsNotNone(found)
                self.assertEqual(target, found[1].best.number)
                if order is None:
                    self.assertEqual(1, state.scrolls[0][1])
                    self.assertLessEqual(len(state.scrolls), 3)
                self.assertLessEqual(sum(count for _, count in state.scrolls), budget)
                self.assertEqual(len(state.scrolls) + 1, len(state.scans))

    def test_collection_card_scroll_recovers_clipping_on_either_edge(self):
        for offset, target, direction, centers in (
            (0, 11, 1, {}),
            (1705, 10, -1, {}),
            (646, 14, 1, {14: 1792}),
            (1699, 10, -1, {10: 13}),
            (302, 12, 1, {12: 1773}),
            (319, 12, 1, {12: 1756}),
        ):
            with self.subTest(offset=offset):
                navigator, state = self._story_window(offset)
                state.centers = centers
                found = navigator._wait_for_story_badge_with_scroll(target, scan_steps=1)
                self.assertIsNotNone(found)
                self.assertEqual(target, found[1].best.number)
                self.assertEqual([(direction, 1)], state.scrolls)

    def test_collection_card_scroll_stops_without_consistent_identity_evidence(self):
        for label, omitted, centers, target in (
            ("no_anchors", set(range(1, 21)), {}, 14),
            ("two_anchors", set(range(3, 21)), {}, 14),
            ("ambiguous_target", {7}, {}, 7),
        ):
            with self.subTest(case=label):
                navigator, state = self._story_window()
                state.omitted, state.centers = omitted, centers
                if label == "ambiguous_target":
                    state.missing_reason = "候选分差不足：0.020<0.050"
                self.assertIsNone(navigator._wait_for_story_badge_with_scroll(target))
                self.assertEqual([], state.scrolls)

    def test_collection_card_scroll_obeys_wheel_budget_and_stops_when_stuck(self):
        for budget, wheel_pixels, target, omitted in (
            (0, 180, 14, set()),
            (1, 180, 14, set()),
            (3, 20, 14, set()),
            (16, 0, 14, set()),
            (16, 180, 7, {7}),
        ):
            with self.subTest(budget=budget, wheel_pixels=wheel_pixels):
                navigator, state = self._story_window(wheel_pixels=wheel_pixels)
                state.omitted = omitted
                self.assertIsNone(navigator._wait_for_story_badge_with_scroll(target, budget))
                self.assertLessEqual(sum(count for _, count in state.scrolls), budget)
                if wheel_pixels == 0:
                    self.assertLessEqual(len(state.scrolls), 2)
                elif omitted:
                    self.assertTrue(state.scrolls)
                else:
                    self.assertEqual(budget, sum(count for _, count in state.scrolls))

    def test_collection_card_scroll_stops_after_unverified_scroll(self):
        navigator, state = self._story_window()
        navigator._scroll_cartridge_bar = Mock(return_value=False)

        self.assertIsNone(navigator._wait_for_story_badge_with_scroll(14))

        navigator._scroll_cartridge_bar.assert_called_once()
        self.assertEqual([0], state.scans)

    def test_probe_story_card_scrolls_up_once_then_rechecks_same_frame_status(self):
        first_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        matched_frame = np.ones((1080, 1920, 3), dtype=np.uint8)
        confirmed_frame = np.full((1080, 1920, 3), 2, dtype=np.uint8)
        frames = iter((first_frame, matched_frame, confirmed_frame))
        detection = StoryBadgeDetection(
            best=StoryBadgeCandidate(
                10,
                MatchResult(
                    0.99,
                    (500, 930),
                    (30, 28),
                    pixel_score=0.98,
                    zncc_score=0.97,
                ),
            ),
            runner_up=StoryBadgeCandidate(
                12,
                MatchResult(
                    0.80,
                    (501, 930),
                    (31, 31),
                    pixel_score=0.82,
                    zncc_score=0.70,
                ),
            ),
        )
        completion = StoryCardCompletion(
            absorb=CardActionDetection(CardActionState.PENDING),
            suppress=CardActionDetection(CardActionState.COMPLETED),
            bounds=(485, 1010, 665, 1060),
            complete_region=True,
        )
        scrolls = []
        clicks = []
        sleeps = []
        task = SimpleNamespace(
            scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=sleeps.append,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: next(frames)))
        find_results = iter(
            (
                (None, "未达到角标双阈值"),
                (detection, ""),
                (detection, ""),
            )
        )
        navigator._find_story_badge = lambda *_args: next(find_results)
        detected_frames = []
        navigator.card_status = SimpleNamespace(
            detect=lambda frame, center: detected_frames.append((frame, center)) or completion
        )

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=5)

        self.assertIsNotNone(result)
        self.assertIs(confirmed_frame, result.located.frame)
        self.assertIs(completion, result.completion)
        self.assertEqual(
            [
                (
                    (PROBE_QUICK_SWITCH_SCROLL_POINT, PROBE_QUICK_SWITCH_SCROLL_AMOUNT),
                    {
                        "count": PROBE_QUICK_SWITCH_SCROLL_COUNT,
                        "interval": PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
                        "after_sleep": PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
                    },
                )
            ],
            scrolls,
        )
        self.assertEqual((43 / 1920, 974 / 1080), PROBE_QUICK_SWITCH_SCROLL_POINT)
        self.assertEqual(1, PROBE_QUICK_SWITCH_SCROLL_AMOUNT)
        self.assertEqual(5, PROBE_QUICK_SWITCH_SCROLL_COUNT)
        self.assertEqual(0.1, PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS)
        self.assertEqual(0.5, PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS)
        self.assertEqual(
            [],
            clicks,
        )
        self.assertEqual([PROBE_STORY_BADGE_CONFIRM_SECONDS], sleeps)
        self.assertEqual(
            [
                (matched_frame, detection.best.result.center),
                (confirmed_frame, detection.best.result.center),
            ],
            detected_frames,
        )

    def test_probe_story_card_scrolls_again_when_status_strip_is_clipped(self):
        edge_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        centered_frame = np.ones((1080, 1920, 3), dtype=np.uint8)
        confirmed_frame = np.full((1080, 1920, 3), 2, dtype=np.uint8)
        frames = iter((edge_frame, centered_frame, confirmed_frame))
        edge_badge = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (1850, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (1851, 930), (31, 31), 0.82, 0.70),
            ),
        )
        centered_badge = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (500, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (501, 930), (31, 31), 0.82, 0.70),
            ),
        )
        complete = StoryCardCompletion(
            CardActionDetection(CardActionState.COMPLETED),
            CardActionDetection(CardActionState.COMPLETED),
            (485, 1010, 665, 1060),
            complete_region=True,
        )
        scrolls = []
        clicks = []
        task = SimpleNamespace(
            scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=lambda *_args: None,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: next(frames)))
        badges = iter(((edge_badge, ""), (centered_badge, ""), (centered_badge, "")))
        navigator._find_story_badge = lambda *_args: next(badges)
        completions = iter((complete, complete))
        navigator.card_status = SimpleNamespace(detect=lambda *_args: next(completions))

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=1)

        self.assertIsNotNone(result)
        self.assertIs(centered_badge, result.located.badge)
        self.assertIs(complete, result.completion)
        self.assertEqual(1, len(scrolls))
        self.assertEqual(
            [],
            clicks,
        )

    def test_probe_story_card_ambiguity_stops_without_scrolling(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        warnings = []
        task = SimpleNamespace(
            scroll_client=lambda *_args, **_kwargs: self.fail(
                "ambiguous probe badge must not scroll"
            ),
            sleep=lambda *_args: self.fail("ambiguous probe badge must not sleep"),
            info_set=lambda *_args: None,
            log_warning=warnings.append,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        navigator._find_story_badge = lambda *_args: (
            None,
            "候选分差不足（ZNCC）：0.020<0.050",
        )
        navigator.card_status = SimpleNamespace(
            detect=lambda *_args: self.fail("ambiguous probe badge must not read status")
        )

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=3)

        self.assertIsNone(result)
        self.assertIn("存在歧义", warnings[0])

    def test_probe_story_card_recheck_failure_stops_without_scrolling_or_clicking(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        detection = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (500, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (501, 930), (31, 31), 0.82, 0.70),
            ),
        )
        completion = StoryCardCompletion(
            CardActionDetection(CardActionState.COMPLETED),
            CardActionDetection(CardActionState.COMPLETED),
            (485, 1010, 665, 1060),
            complete_region=True,
        )
        warnings = []
        task = SimpleNamespace(
            scroll_client=lambda *_args, **_kwargs: self.fail(
                "a vanished recheck must stop before scrolling"
            ),
            sleep=lambda *_args: None,
            info_set=lambda *_args: None,
            log_warning=warnings.append,
        )
        navigator = Navigator(task, SimpleNamespace(capture=lambda: frame))
        results = iter(((detection, ""), (None, "未识别")))
        navigator._find_story_badge = lambda *_args: next(results)
        navigator.card_status = SimpleNamespace(detect=lambda *_args: completion)

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=3)

        self.assertIsNone(result)
        self.assertIn("复核失败", warnings[0])

    def test_probe_story_card_scan_limit_is_bounded_without_reset(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        scrolls = []
        clicks = []
        captures = []
        task = SimpleNamespace(
            scroll_client=lambda *args, **kwargs: scrolls.append((args, kwargs)),
            operate_click=lambda *args, **kwargs: clicks.append((args, kwargs)),
            sleep=lambda *_args: None,
            info_set=lambda *_args: None,
            log_warning=lambda *_args: None,
        )
        navigator = Navigator(
            task,
            SimpleNamespace(capture=lambda: captures.append(True) or frame),
        )
        navigator._find_story_badge = lambda *_args: (None, "未识别")

        result = navigator.locate_probe_story_card("Q_sp10", scan_steps=12)

        self.assertIsNone(result)
        self.assertEqual(4, len(captures))
        self.assertEqual(3, len(scrolls))
        self.assertEqual([5, 5, 2], [call[1]["count"] for call in scrolls])
        self.assertEqual(
            [],
            clicks,
        )
        self.assertTrue(
            all(
                call[0] == (PROBE_QUICK_SWITCH_SCROLL_POINT, PROBE_QUICK_SWITCH_SCROLL_AMOUNT)
                for call in scrolls
            )
        )

    def test_enter_probe_story_card_uses_revalidated_located_card(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        badge = StoryBadgeDetection(
            StoryBadgeCandidate(
                10,
                MatchResult(0.99, (500, 930), (30, 28), 0.98, 0.97),
            ),
            StoryBadgeCandidate(
                12,
                MatchResult(0.80, (501, 930), (31, 31), 0.82, 0.70),
            ),
        )
        completion = StoryCardCompletion(
            CardActionDetection(CardActionState.COMPLETED),
            CardActionDetection(CardActionState.COMPLETED),
            (485, 1010, 665, 1060),
            complete_region=True,
        )
        located = LocatedStoryCard(CARD_BY_ID["Q_sp10"], frame, badge)
        probed = ProbedStoryCard(located, completion)
        entered = []
        navigator = Navigator(SimpleNamespace(), SimpleNamespace())
        expected = NavigationResult(True, ScreenState.SANDBOX, "Q_sp10")
        navigator._confirm_collection_completion = lambda value: value
        navigator._click_located_story_card = lambda value: entered.append(value) or expected

        result = navigator.enter_probe_story_card(probed)

        self.assertIs(expected, result)
        self.assertEqual([located], entered)

    def test_collection_card_entry_handles_insert_prompt_then_reconfirms_sandbox(self):
        frame = np.full((1080, 1920, 3), 100, dtype=np.uint8)
        clicks = []
        sleeps = []
        states = iter(
            (
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        task = SimpleNamespace(
            config={"加载页面等待秒数": 45.0},
            sleep=lambda seconds: sleeps.append(seconds),
        )

        def ocr_text(_frame, name, roi=None, **_kwargs):
            if name == "新卡带插入提示":
                self.assertEqual(FIRST_CARD_INSERT_REGION, roi)
                return "未插好游戏卡 插入"
            return ""

        vision = SimpleNamespace(
            capture=lambda: frame,
            simplify=lambda value: value,
            ocr_text=ocr_text,
            click_ocr=lambda patterns, roi, after_sleep, name: (
                clicks.append((tuple(patterns), roi, after_sleep, name)) or True
            ),
            match=lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0)),
            passes=lambda *_args: False,
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: next(states)

        result = navigator._wait_for_story_sandbox(12, timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual("Q_sp12", result.message)
        self.assertEqual(
            [
                (
                    (r"插入", r"未插好游戏卡"),
                    FIRST_CARD_INSERT_REGION,
                    0.8,
                    "新卡带插入",
                )
            ],
            clicks,
        )
        self.assertEqual([], sleeps)

    def test_collection_card_entry_handles_skip_and_confirmation_with_mouse(self):
        frame = np.full((1080, 1920, 3), 100, dtype=np.uint8)
        client_clicks = []
        ocr_clicks = []
        skip_result = MatchResult(0.99, (1500, 20), (100, 40), pixel_score=0.98)
        task = SimpleNamespace(config={}, sleep=lambda *_args: None)
        prompts = iter(("", ""))
        confirmations = iter(("确认",))

        def ocr_text(_frame, name, roi=None, **_kwargs):
            if name == "新卡带插入提示":
                return next(prompts)
            if name == "首次卡带确认":
                self.assertEqual(FIRST_CARD_CONFIRM_REGION, roi)
                return next(confirmations)
            return ""

        matches = iter(
            (
                skip_result,
                MatchResult(-1.0, (0, 0), (0, 0)),
                MatchResult(-1.0, (0, 0), (0, 0)),
            )
        )
        vision = SimpleNamespace(
            capture=lambda: frame,
            simplify=lambda value: value,
            ocr_text=ocr_text,
            click_ocr=lambda patterns, roi, after_sleep, name: (
                ocr_clicks.append((tuple(patterns), roi, after_sleep, name)) or True
            ),
            match=lambda _frame, spec: (
                self.assertEqual(FIRST_CARD_SKIP_TEMPLATE, spec) or next(matches)
            ),
            passes=lambda result, _spec: result.score >= 0.72,
            click_client=lambda point, shape, after_sleep=0: client_clicks.append(
                (point, shape, after_sleep)
            ),
        )
        navigator = Navigator(task, vision)
        states = iter(
            (
                ScreenState.UNKNOWN,
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        navigator.classify = lambda _frame=None: next(states)

        result = navigator._wait_for_story_sandbox(12, timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual([(skip_result.center, frame.shape, 0.8)], client_clicks)
        self.assertEqual(
            [((r"确认",), FIRST_CARD_CONFIRM_REGION, 0.8, "首次卡带确认")],
            ocr_clicks,
        )

    def test_collection_card_entry_requires_consecutive_stable_sandbox_frames(self):
        frame = np.full((1080, 1920, 3), 100, dtype=np.uint8)
        states = iter(
            (
                ScreenState.SANDBOX,
                ScreenState.LOADING,
                ScreenState.SANDBOX,
                ScreenState.UNKNOWN,
                ScreenState.SANDBOX,
                ScreenState.SANDBOX,
            )
        )
        captures = []
        task = SimpleNamespace(
            config={}, sleep=lambda *_args: None,
            _handle_recent_cartridge_special_pages=Mock(
                return_value=CartridgeSpecialPageResult.ABSENT,
            ),
        )
        vision = SimpleNamespace(
            capture=lambda: captures.append(frame) or frame, ocr_text=Mock(return_value=""),
            simplify=lambda value: value,
        )
        navigator = Navigator(task, vision)
        navigator.classify = lambda _frame=None: next(states)
        navigator._handle_story_card_intermediate = lambda _frame: False

        result = navigator._wait_for_story_sandbox(1, timeout=2.0, interval=0.0)

        self.assertTrue(result.success)
        self.assertEqual(STORY_SANDBOX_STABLE_HITS, 2)
        self.assertEqual(6, len(captures))


class CharacterMapTitleTest(unittest.TestCase):
    def test_a_longer_map_name_is_not_its_prefix(self):
        for card_id, town, annex in (
            ("Q_cp5", "童话镇", "童话镇旅馆"),
            ("Q_sp9", "雷瓦汀基地", "雷瓦汀基地救护站"),
        ):
            for suffix in ("", " 普通 ?"):
                with self.subTest(card=card_id, suffix=suffix):
                    card = CARD_BY_ID[card_id]
                    self.assertEqual(
                        ("main_area",), Navigator._target_keys_in_text(
                            card, normalize_text(f"安全 {town}{suffix}"),
                        ),
                    )
                    self.assertEqual(
                        (), Navigator._target_keys_in_text(
                            card, normalize_text(f"安全 {annex}{suffix}"),
                        ),
                    )

    def test_parentheses_misread_by_ocr_still_name_the_map(self):
        # Live 2K: "废墟地道（前段）" read "废墟地道道前段", "（后段）" "直后段".
        card = CARD_BY_ID["Q_cp2"]

        self.assertEqual(
            ("battle_area_1",),
            Navigator._target_keys_in_text(
                card, normalize_text("战斗废墟地道道前段s出现风属性怪物")
            ),
        )
        self.assertEqual(
            ("battle_area_2",),
            Navigator._target_keys_in_text(
                card, normalize_text("战斗废墟地道直后段s出现风属性怪物")
            ),
        )



class CharacterTabTest(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        patcher = patch("src.tasks.map_trade.navigator_story.monotonic", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _advance_clock(self, seconds):
        self.clock += seconds

    def _navigator(self, highlight):
        clicks = []
        frame = np.zeros((1080, 1920, 3), np.uint8)
        vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda _frame, _name: "最近 店长游戏卡 剧情游戏卡 角色游戏卡 活动游戏卡",
            simplify=lambda text: text,
            bright_neutral_ratio=lambda _frame, region: highlight.get(region, 0.0),
        )
        task = SimpleNamespace(
            operate_click=lambda x, y, after_sleep=0: clicks.append((x, y)),
            sleep=self._advance_clock,
            log_warning=lambda *_args: None,
        )
        return Navigator(task, vision), clicks

    def test_character_tab_is_clicked_and_confirmed_by_its_own_highlight(self):
        navigator, clicks = self._navigator({CHARACTER_CATEGORY_HIGHLIGHT_REGION: 0.084})

        self.assertTrue(navigator._select_cartridge_category("character"))
        self.assertEqual([CHARACTER_CATEGORY_POINT], clicks)
        event, event_clicks = self._navigator({EVENT_CATEGORY_HIGHLIGHT_REGION: 0.084})
        self.assertTrue(event._select_cartridge_category("event"))
        self.assertEqual([EVENT_CATEGORY_POINT], event_clicks)
        self.assertFalse(
            navigator._wait_for_cartridge_category(timeout=0.1, category="event", warn=False),
        )

    def test_story_highlight_does_not_confirm_the_character_tab(self):
        navigator, _clicks = self._navigator({STORY_CATEGORY_HIGHLIGHT_REGION: 0.079})

        self.assertFalse(
            navigator._wait_for_cartridge_category(timeout=0.2, category="character", warn=False)
        )
        self.assertTrue(navigator._wait_for_cartridge_category(timeout=0.2))
        self.assertEqual((725 / 1920, 877 / 1080), CHARACTER_CATEGORY_POINT)
        self.assertNotEqual(STORY_CATEGORY_POINT, CHARACTER_CATEGORY_POINT)
