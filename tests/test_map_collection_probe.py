import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.config import config
from src.tasks.BD2MapCollectionProbeTask import (
    ALL_COLLECTION_CARDS_OPTION,
    DEFAULT_COLLECTION_END,
    DEFAULT_COLLECTION_START,
    TELEPORT_MAP_ARROW_RECHECK_INTERVAL,
    TELEPORT_MAP_BACKWARD_CLICK_LIMIT,
    TELEPORT_MAP_FORWARD_CLICK_INTERVAL,
    TELEPORT_MAP_FORWARD_CLICK_LIMIT,
    TELEPORT_MAP_PAGE_SETTLE_SECONDS,
    BD2MapCollectionProbeTask,
    TeleportMapScanResult,
    merge_completion_observations,
    resolve_collection_map_titles,
)
from src.tasks.debug_registry import DEBUG_ONETIME_TASKS
from src.tasks.map_trade.audit import CollectionVisualAuditStore
from src.tasks.map_trade.card_status import (
    CardActionDetection,
    CardActionState,
    StoryCardCompletion,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
    TeleportScope,
)
from src.tasks.map_trade.navigator import (
    PROBE_QUICK_SWITCH_SCROLL_POINT,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    LocatedStoryCard,
    ProbedStoryCard,
    StoryBadgeCandidate,
    StoryBadgeDetection,
)
from src.tasks.map_trade.progress import UTC_PLUS_8


def _badge(number: int = 1, center: tuple[int, int] = (100, 920)) -> StoryBadgeDetection:
    best = MatchResult(
        0.99,
        (center[0] - 14, center[1] - 14),
        (29, 29),
        pixel_score=0.98,
        zncc_score=0.97,
    )
    runner_up = MatchResult(
        0.80,
        best.position,
        best.size,
        pixel_score=0.80,
        zncc_score=0.70,
    )
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


def _observation(
    absorb: str = CardActionState.COMPLETED.value,
    suppress: str = CardActionState.COMPLETED.value,
    *,
    complete_region: bool = True,
    score: float = 0.98,
) -> dict:
    return {
        "card_id": "Q_sp1",
        "number": 1,
        "name": "血骑士",
        "complete_region": complete_region,
        "badge": {
            "match_score": score,
            "pixel_score": score,
            "zncc_score": score,
        },
        "absorb": {
            "state": absorb,
            "reason": "test",
            "pending": [],
            "completed": [],
        },
        "suppress": {
            "state": suppress,
            "reason": "test",
            "pending": [],
            "completed": [],
        },
        "overall_state": CardActionState.COMPLETED.value,
        "conflict": False,
    }


class MapCollectionProbeTaskTest(unittest.TestCase):
    def test_page_scan_requires_cross_map_talent_or_a_confirmed_ground_page(self):
        card = CARD_BY_ID["Q_sp1"]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for scope, recalled, can_scan in (
            (TeleportScope.CURRENT_CARTRIDGE, False, True),
            (TeleportScope.CURRENT_MAP, True, True),
            (TeleportScope.CURRENT_MAP, False, False),
            (TeleportScope.UNKNOWN, False, False),
        ):
            with self.subTest(scope=scope, recalled=recalled):
                task = BD2MapCollectionProbeTask(SimpleNamespace(scene=None), SimpleNamespace())
                task.config = {**task.default_config, "保存完成度截图": False}
                task.info_set = Mock()
                task._save_visual_progress = Mock()
                task._scan_teleport_map_pages = Mock(return_value=TeleportMapScanResult(
                    True, (), 0, 0, "done",
                ))
                navigator = SimpleNamespace(
                    locate_probe_story_card=Mock(return_value=ProbedStoryCard(
                        LocatedStoryCard(card, frame, _badge(card.number)), _completion(),
                    )),
                    enter_probe_story_card=Mock(return_value=NavigationResult(
                        True, ScreenState.SANDBOX,
                    )),
                    _open_teleport_map_anywhere=Mock(return_value=NavigationResult(
                        True, ScreenState.AREA_MAP, map_page_mode=MapPageMode.GENERATE_TELEPORT,
                    )),
                    _capture_area_map_context=Mock(return_value=SimpleNamespace(teleport_scope=scope)),
                    _recall_current_circle=Mock(return_value=(
                        SimpleNamespace(map_page_mode=MapPageMode.DIRECT_TELEPORT) if recalled
                        else NavigationResult(False, ScreenState.AREA_MAP, "无可用阵")
                    )),
                    _restore_navigation_field=Mock(),
                    return_teleport_map_to_sandbox=Mock(return_value=NavigationResult(
                        True, ScreenState.SANDBOX,
                    )),
                )
                result = task._probe_card(
                    card, navigator, SimpleNamespace(), Mock(), {card.card_id: []}, "probe", 2, 30,
                )
                self.assertEqual(can_scan, result["status"] == "completed")
                self.assertEqual(int(can_scan), task._scan_teleport_map_pages.call_count)
                if not can_scan:
                    navigator._restore_navigation_field.assert_called_once()

    def test_only_merged_probe_task_is_registered_in_debug_section(self):
        registration = [
            "src.tasks.BD2MapCollectionProbeTask",
            "BD2MapCollectionProbeTask",
        ]
        self.assertNotIn(registration, config["onetime_tasks"])
        self.assertIn(registration, DEBUG_ONETIME_TASKS)
        self.assertNotIn(
            ["src.tasks.BD2MapCollectionProbeTask", "BD2CollectionStatusProbeTask"],
            DEBUG_ONETIME_TASKS,
        )
        self.assertNotIn(
            ["src.tasks.BD2MapCollectionProbeTask", "BD2MapLocationProbeTask"],
            DEBUG_ONETIME_TASKS,
        )

        task = BD2MapCollectionProbeTask(
            SimpleNamespace(scene=None),
            SimpleNamespace(),
        )
        self.assertEqual("剧情卡带完成度与地图读取测试", task.name)
        self.assertEqual("测试", task.group_name)
        self.assertTrue(task.visible)

    def test_collection_scope_and_all_new_reference_geometry(self):
        self.assertEqual(25, len(COLLECTABLE_CARDS))
        self.assertEqual(
            {6, 20},
            set(range(1, 21)) - {
                card.number for card in COLLECTABLE_CARDS if card.category == "story"
            },
        )
        self.assertEqual(
            (654 / 1920, 946 / 1080, 1268 / 1920, 1021 / 1080),
            TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
        )
        self.assertEqual((136 / 1920, 52 / 1080), TELEPORT_MAP_RETURN_RELATIVE_POINT)
        self.assertEqual((43 / 1920, 974 / 1080), PROBE_QUICK_SWITCH_SCROLL_POINT)

    def test_probe_can_select_one_card_or_an_inclusive_range(self):
        task = object.__new__(BD2MapCollectionProbeTask)
        task.config = {"测试起始卡带": "Q_sp14", "测试终止卡带": "Q_sp14"}
        self.assertEqual(("Q_sp14",), tuple(card.card_id for card in task._selected_cards()))

        task.config = {"测试起始卡带": "Q_sp8", "测试终止卡带": "Q_sp11"}
        self.assertEqual(
            ("Q_sp8", "Q_sp9", "Q_sp10", "Q_sp11"),
            tuple(card.card_id for card in task._selected_cards()),
        )

        task.config = {
            "测试起始卡带": DEFAULT_COLLECTION_START,
            "测试终止卡带": DEFAULT_COLLECTION_END,
        }
        self.assertEqual(25, len(task._selected_cards()))

        task.config = {"测试起始卡带": "Q_sp14", "测试终止卡带": "Q_sp8"}
        self.assertEqual((), task._selected_cards())

    def test_probe_reads_retired_single_range_configuration(self):
        task = object.__new__(BD2MapCollectionProbeTask)
        task.config = {"测试卡带范围": "Q_sp14"}
        self.assertEqual(("Q_sp14",), tuple(card.card_id for card in task._selected_cards()))
        task.config = {"测试卡带范围": ALL_COLLECTION_CARDS_OPTION}
        self.assertEqual(25, len(task._selected_cards()))

    def test_title_resolution_preserves_unknown_and_prefers_longest_known_title(self):
        self.assertEqual((), resolve_collection_map_titles("战斗Ⅰ 尚未建档的位置"))
        self.assertEqual(
            (
                {
                    "card_id": "Q_sp1",
                    "target_key": "battle_area_2",
                    "title": "卢戈森林深处",
                },
            ),
            resolve_collection_map_titles("战斗Ⅱ 卢戈森林深处"),
        )

    def test_clipped_and_conflicting_completion_observations_remain_conservative(self):
        self.assertIsNone(merge_completion_observations([_observation(complete_region=False)]))
        merged = merge_completion_observations(
            [
                _observation(absorb=CardActionState.COMPLETED.value),
                _observation(absorb=CardActionState.PENDING.value, score=0.99),
            ]
        )
        self.assertIsNotNone(merged)
        self.assertTrue(merged["conflict"])
        self.assertEqual(CardActionState.UNKNOWN.value, merged["absorb"]["state"])
        self.assertEqual(CardActionState.UNKNOWN.value, merged["overall_state"])

    def test_teleport_map_scan_moves_front_then_records_each_backward_page(self):
        task = object.__new__(BD2MapCollectionProbeTask)
        task.config = {"保存每张传送阵地图截图": False}
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep_calls = []
        task.sleep = lambda value: task.sleep_calls.append(value)
        task.save_frame = lambda *_args, **_kwargs: self.fail("screenshots disabled")

        frames = {
            name: np.full((1080, 1920, 3), index, dtype=np.uint8)
            for index, name in enumerate(
                (
                    "forward",
                    "forward_confirm",
                    "front",
                    "front_confirm",
                    "page1",
                    "page1_confirm",
                    "page2",
                    "page2_confirm",
                    "page3",
                    "page3_confirm",
                ),
                1,
            )
        }
        captures = iter(
            (
                frames["forward"],
                frames["forward_confirm"],
                frames["front"],
                frames["front_confirm"],
                frames["page1"],
                frames["page1_confirm"],
                frames["page2"],
                frames["page2_confirm"],
                frames["page3"],
                frames["page3_confirm"],
            )
        )
        forward = MatchResult(0.99, (360, 490), (40, 56), 0.96, 0.95)
        backward = MatchResult(0.99, (1520, 490), (40, 56), 0.93, 0.95)
        missing = MatchResult(-1.0, (0, 0), (0, 0))
        clicks = []
        ocr_calls = []

        class FakeVision:
            @staticmethod
            def capture():
                return next(captures)

            @staticmethod
            def match(frame, spec):
                marker = int(frame[0, 0, 0])
                if spec is TELEPORT_MAP_FORWARD_TEMPLATE:
                    return forward if marker in {1, 2} else missing
                if spec is TELEPORT_MAP_BACKWARD_TEMPLATE:
                    return backward if marker in {5, 6, 7, 8} else missing
                raise AssertionError(spec.name)

            @staticmethod
            def passes(result, _spec):
                return result.score >= 0.95

            @staticmethod
            def click_client(center, shape, *, after_sleep):
                clicks.append((center, shape, after_sleep))

            @staticmethod
            def simplify(value):
                return value

            @staticmethod
            def ocr_text(frame, name, *, relative_roi):
                self.assertEqual(TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI, relative_roi)
                marker = int(frame[0, 0, 0])
                ocr_calls.append((marker, name))
                return {
                    5: "主城 卢戈镇",
                    7: "战斗Ⅰ 卢戈森林",
                    9: "战斗Ⅱ 卢戈森林深处",
                }[marker]

        result = task._scan_teleport_map_pages(
            FakeVision(),
            CARD_BY_ID["Q_sp1"],
            "stamp",
            1,
        )

        self.assertTrue(result.success)
        self.assertEqual(1, result.forward_clicks)
        self.assertEqual(2, result.backward_clicks)
        self.assertEqual(
            ["主城 卢戈镇", "战斗Ⅰ 卢戈森林", "战斗Ⅱ 卢戈森林深处"],
            [page["ocr_text"] for page in result.pages],
        )
        self.assertEqual(
            [
                (forward.center, frames["forward"].shape, TELEPORT_MAP_FORWARD_CLICK_INTERVAL),
                (backward.center, frames["page1"].shape, TELEPORT_MAP_PAGE_SETTLE_SECONDS),
                (backward.center, frames["page2"].shape, TELEPORT_MAP_PAGE_SETTLE_SECONDS),
            ],
            clicks,
        )
        self.assertEqual(
            5,
            task.sleep_calls.count(TELEPORT_MAP_ARROW_RECHECK_INTERVAL),
        )
        self.assertEqual(
            [TELEPORT_MAP_PAGE_SETTLE_SECONDS],
            [value for value in task.sleep_calls if value == TELEPORT_MAP_PAGE_SETTLE_SECONDS],
        )
        self.assertEqual([5, 7, 9], [value[0] for value in ocr_calls])

    def test_forward_limit_stops_before_any_ocr_when_arrow_still_exists(self):
        task = object.__new__(BD2MapCollectionProbeTask)
        task.config = {"保存每张传送阵地图截图": False}
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args: None
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        arrow = MatchResult(0.99, (360, 490), (40, 56), 0.96, 0.95)
        clicks = []

        class FakeVision:
            @staticmethod
            def capture():
                return frame

            @staticmethod
            def match(_frame, spec):
                self.assertIs(TELEPORT_MAP_FORWARD_TEMPLATE, spec)
                return arrow

            @staticmethod
            def passes(_result, _spec):
                return True

            @staticmethod
            def click_client(*args, **kwargs):
                clicks.append((args, kwargs))

            @staticmethod
            def ocr_text(*_args, **_kwargs):
                self.fail("forward overflow must stop before OCR")

        result = task._scan_teleport_map_pages(
            FakeVision(),
            CARD_BY_ID["Q_sp1"],
            "stamp",
            1,
        )
        self.assertFalse(result.success)
        self.assertEqual(TELEPORT_MAP_FORWARD_CLICK_LIMIT, len(clicks))
        self.assertIn("向前7次", result.message)

    def test_backward_limit_records_last_page_then_reports_overflow(self):
        task = object.__new__(BD2MapCollectionProbeTask)
        task.config = {"保存每张传送阵地图截图": False}
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args: None
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        backward = MatchResult(0.99, (1520, 490), (40, 56), 0.93, 0.95)
        missing = MatchResult(-1.0, (0, 0), (0, 0))
        clicks = []

        class FakeVision:
            capture = staticmethod(lambda: frame)
            simplify = staticmethod(lambda value: value)

            @staticmethod
            def match(_frame, spec):
                if spec is TELEPORT_MAP_FORWARD_TEMPLATE:
                    return missing
                return backward

            @staticmethod
            def passes(result, _spec):
                return result.score >= 0.95

            @staticmethod
            def click_client(*args, **kwargs):
                clicks.append((args, kwargs))

            @staticmethod
            def ocr_text(*_args, **_kwargs):
                return "战斗 未知地图"

        result = task._scan_teleport_map_pages(
            FakeVision(),
            CARD_BY_ID["Q_sp1"],
            "stamp",
            1,
        )
        self.assertFalse(result.success)
        self.assertEqual(TELEPORT_MAP_BACKWARD_CLICK_LIMIT, result.backward_clicks)
        self.assertEqual(TELEPORT_MAP_BACKWARD_CLICK_LIMIT, len(clicks))
        self.assertEqual(TELEPORT_MAP_BACKWARD_CLICK_LIMIT + 1, len(result.pages))
        self.assertIn("向后点击7次", result.message)

    def test_unstable_arrow_cannot_confirm_first_or_last_map(self):
        for direction in ("forward", "backward"):
            with self.subTest(direction=direction):
                task = object.__new__(BD2MapCollectionProbeTask)
                task.config = {"保存每张传送阵地图截图": False}
                task.info_set = lambda *_args, **_kwargs: None
                task.sleep = lambda *_args: None
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                arrow = MatchResult(0.99, (360, 490), (40, 56), 0.96, 0.95)
                missing = MatchResult(-1.0, (0, 0), (0, 0))
                states = iter((True, False, True, False))

                def match(_frame, spec):
                    unstable = (
                        TELEPORT_MAP_FORWARD_TEMPLATE
                        if direction == "forward" else TELEPORT_MAP_BACKWARD_TEMPLATE
                    )
                    return arrow if spec is unstable and next(states, False) else missing

                vision = SimpleNamespace(
                    capture=lambda: frame,
                    match=match,
                    passes=lambda result, _spec: result.score > 0.95,
                    simplify=lambda value: value,
                    ocr_text=lambda *_args, **_kwargs: "主城 卢戈镇",
                    click_client=lambda *_args, **_kwargs: self.fail("unstable arrow clicked"),
                )
                result = task._scan_teleport_map_pages(
                    vision, CARD_BY_ID["Q_sp1"], "stamp", 1,
                )
                self.assertFalse(result.success)
                self.assertIn("箭头状态不稳定", result.message)
                self.assertEqual(int(direction == "backward"), len(result.pages))
                if result.pages:
                    self.assertIsNone(result.pages[0]["backward"]["passed"])

    def test_full_merged_run_processes_supported_cards_and_saves_each_completion(self):
        executor = SimpleNamespace(scene=None)
        task = BD2MapCollectionProbeTask(executor, SimpleNamespace())
        task.config = dict(task.default_config)
        task.config["保存完成度截图"] = False
        task.config["保存每张传送阵地图截图"] = False
        task.info_set = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args: None
        task.save_frame = lambda *_args, **_kwargs: self.fail("screenshots disabled")
        writes = {}

        def write_probe(name, lines, **_kwargs):
            writes[name] = "\n".join(lines)
            return Path(name)

        task.write_probe_text = write_probe
        task._scan_teleport_map_pages = lambda _vision, card, *_args: TeleportMapScanResult(
            True,
            (
                {
                    "index": 1,
                    "ocr_text": card.targets[0].title,
                    "normalized_text": card.targets[0].title,
                    "candidates": [],
                    "forward": {},
                    "backward": {},
                    "screenshot": "",
                },
            ),
            0,
            0,
            "done",
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        calls = {"locate": [], "enter": [], "return": [], "reopen": 0}

        class FakeVision:
            def __init__(self, _task):
                pass

        class FakeNavigator:
            def __init__(self, _task, _vision):
                pass

            @staticmethod
            def _open_cartridge_quick_switcher():
                return NavigationResult(True, ScreenState.CARD_MENU, "ready")

            @staticmethod
            def locate_probe_story_card(card_id, *, scan_steps):
                self.assertEqual(30, scan_steps)
                calls["locate"].append(card_id)
                card = CARD_BY_ID[card_id]
                return ProbedStoryCard(
                    LocatedStoryCard(card, frame, _badge(card.number)),
                    _completion(),
                )

            @staticmethod
            def enter_probe_story_card(probed):
                calls["enter"].append(probed.located.card.card_id)
                return NavigationResult(True, ScreenState.SANDBOX, "entered")

            @staticmethod
            def _open_teleport_map_anywhere(_card):
                return NavigationResult(
                    True,
                    ScreenState.AREA_MAP,
                    "map",
                    map_page_mode=MapPageMode.DIRECT_TELEPORT,
                )

            @staticmethod
            def return_teleport_map_to_sandbox(number):
                calls["return"].append(number)
                return NavigationResult(True, ScreenState.SANDBOX, "returned")

            @staticmethod
            def open_cartridge_quick_switcher_from_sandbox(
                *,
                sandbox_already_confirmed=False,
                category="story",
            ):
                self.assertTrue(sandbox_already_confirmed)
                calls["reopen"] += 1
                return NavigationResult(True, ScreenState.CARD_MENU, "reopened")

        class FakeStore:
            path = Path("visual.json")
            instance = None

            def __init__(self):
                self.calls = []
                FakeStore.instance = self

            def save_scan(self, cards, **kwargs):
                self.calls.append((dict(cards), dict(kwargs)))
                return {"weekly_key": "2026-07-27"}

        with (
            patch("src.tasks.BD2MapCollectionProbeTask.Vision", FakeVision),
            patch("src.tasks.BD2MapCollectionProbeTask.Navigator", FakeNavigator),
            patch(
                "src.tasks.BD2MapCollectionProbeTask.CollectionVisualAuditStore",
                FakeStore,
            ),
        ):
            self.assertTrue(task.run())

        expected = [card.card_id for card in COLLECTABLE_CARDS]
        self.assertEqual(expected, calls["locate"])
        self.assertEqual(expected, calls["enter"])
        self.assertEqual([card.number for card in COLLECTABLE_CARDS], calls["return"])
        self.assertEqual(24, calls["reopen"])
        self.assertEqual(26, len(FakeStore.instance.calls))
        self.assertEqual(25, len(FakeStore.instance.calls[-1][0]))
        self.assertTrue(FakeStore.instance.calls[-1][1]["completed"])
        payload = json.loads(writes["map_collection_probe_latest.json"])
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual(expected, payload["requested_card_ids"])
        self.assertTrue(all(value["status"] == "completed" for value in payload["cards"]))

    def test_failure_stops_before_next_card_and_keeps_first_card_weekly_data(self):
        executor = SimpleNamespace(scene=None)
        task = BD2MapCollectionProbeTask(executor, SimpleNamespace())
        task.config = dict(task.default_config)
        task.config["保存完成度截图"] = False
        task.config["保存每张传送阵地图截图"] = False
        task.info_set = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args: None
        task.write_probe_text = lambda name, *_args, **_kwargs: Path(name)
        task._scan_teleport_map_pages = lambda *_args: TeleportMapScanResult(True, (), 0, 0, "done")
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        located = []

        class FakeVision:
            def __init__(self, _task):
                pass

        class FakeNavigator:
            def __init__(self, _task, _vision):
                pass

            _open_cartridge_quick_switcher = staticmethod(
                lambda: NavigationResult(True, ScreenState.CARD_MENU, "ready")
            )

            @staticmethod
            def locate_probe_story_card(card_id, **_kwargs):
                located.append(card_id)
                if card_id == "Q_sp2":
                    return None
                card = CARD_BY_ID[card_id]
                return ProbedStoryCard(
                    LocatedStoryCard(card, frame, _badge(card.number)),
                    _completion(),
                )

            enter_probe_story_card = staticmethod(
                lambda _value: NavigationResult(True, ScreenState.SANDBOX, "entered")
            )
            _open_teleport_map_anywhere = staticmethod(
                lambda _card: NavigationResult(
                    True,
                    ScreenState.AREA_MAP,
                    "map",
                    map_page_mode=MapPageMode.DIRECT_TELEPORT,
                )
            )
            return_teleport_map_to_sandbox = staticmethod(
                lambda _number: NavigationResult(True, ScreenState.SANDBOX, "returned")
            )
            open_cartridge_quick_switcher_from_sandbox = staticmethod(
                lambda **_kwargs: NavigationResult(
                    True,
                    ScreenState.CARD_MENU,
                    "reopened",
                )
            )

        class FakeStore:
            path = Path("visual.json")
            last_cards = {}

            def save_scan(self, cards, **_kwargs):
                FakeStore.last_cards = dict(cards)
                return {"weekly_key": "2026-07-27"}

        with (
            patch("src.tasks.BD2MapCollectionProbeTask.Vision", FakeVision),
            patch("src.tasks.BD2MapCollectionProbeTask.Navigator", FakeNavigator),
            patch(
                "src.tasks.BD2MapCollectionProbeTask.CollectionVisualAuditStore",
                FakeStore,
            ),
        ):
            self.assertFalse(task.run())

        self.assertEqual(["Q_sp1", "Q_sp2"], located)
        self.assertEqual({"Q_sp1"}, set(FakeStore.last_cards))


class CollectionVisualAuditStoreTest(unittest.TestCase):
    def test_visual_table_resets_at_monday_eight_am(self):
        before = datetime(2026, 7, 13, 7, 59, tzinfo=UTC_PLUS_8)
        after = datetime(2026, 7, 13, 8, 0, tzinfo=UTC_PLUS_8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "visual.json"
            store = CollectionVisualAuditStore(path, now_provider=lambda: before)
            store.save_scan(
                {"Q_sp1": _observation()},
                completed=False,
            )
            current = store.load()
            self.assertIn("Q_sp1", current["cards"])
            self.assertEqual(
                ["Q_sp6", "Q_sp20"],
                current["excluded_card_ids"],
            )

            reset = CollectionVisualAuditStore(path, now_provider=lambda: after)
            state = reset.load()
            self.assertEqual({}, state["cards"])
            self.assertEqual({}, state["last_scan"])

    def test_visual_table_never_modifies_collection_progress_file(self):
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC_PLUS_8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            progress_path = root / "map_trade_progress.json"
            original = {"sentinel": "actual collection progress"}
            progress_path.write_text(
                json.dumps(original, ensure_ascii=False),
                encoding="utf-8",
            )
            visual = CollectionVisualAuditStore(
                root / "map_collection_visual_status.json",
                now_provider=lambda: now,
            )
            visual.save_scan({"Q_sp1": _observation()}, completed=False)

            self.assertEqual(
                original,
                json.loads(progress_path.read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
