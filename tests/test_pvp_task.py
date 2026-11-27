import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks import run_history
from src.tasks.BaseBD2Task import (
    FIEND_HUNT_REWARD_DISMISS_TEXT,
    FIEND_HUNT_REWARD_TITLE,
    RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
    RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD,
    RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
    CartridgeSpecialPageResult,
)
from src.tasks.BD2MapCollectionProbeTask import BD2MapCollectionProbeTask
from src.tasks.map_trade.models import MatchResult, ScreenState
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.navigator_constants import CHAPTER_HOME_POINT
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.MapTradeTask import MapTradeTask
from src.tasks.PVPTask import (
    PVP_AUTO_BATTLE_CANCEL_SCREEN_ROI,
    PVP_AUTO_BATTLE_CLICK_REFERENCE,
    PVP_AUTO_BATTLE_MENU_VERIFY_SECONDS,
    PVP_AUTO_BATTLE_SCREEN_ROI,
    PVP_BACK_HOME_REFERENCE_POINT,
    PVP_BATTLE_START_SCREEN_POINT,
    PVP_CARTRIDGE_SLOT_POINT,
    PVP_CLICK_VERIFY_ATTEMPTS,
    PVP_FREE_AP_SWITCH_SCREEN_POINT,
    PVP_MEDALS_TEMPLATE,
    PVP_MULTIPLIER_1_OPTION_SCREEN_POINT,
    PVP_MULTIPLIER_40_OPTION_SCREEN_POINT,
    PVP_MULTIPLIER_BUTTON_SCREEN_POINT,
    PVP_MULTIPLIER_CONFIRM_SCREEN_POINT,
    PVP_MULTIPLIER_PLUS_SCREEN_POINT,
    PVP_RANK_CONFIRM_SETTLE_SECONDS,
    PVP_RESULT_CLOSE_AFTER_SECONDS,
    PVP_RESULT_CLOSE_SCREEN_POINT,
    PVP_RESULT_SCREEN_ROI,
    PVP_STAGE_CLICK_REFERENCE_OFFSET,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    TEMPLATE_DIR,
    PVPTask,
)
from src.tasks.SquareGoddessTask import SquareGoddessTask
from src.utils import task_vision
from src.utils.cartridge_quick_switch import (
    BATTLE_GAMEPLAY_CATEGORY_POINT,
    FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS,
)
from tests.helpers import FakeClock


def pvp_task(**config):
    task = object.__new__(PVPTask)
    task.config = config
    task.info = {}
    task.info_set = task.info.__setitem__
    task.log_info = Mock()
    task.log_warning = Mock()
    task.log_completion = Mock()
    task.sleep = Mock()
    task._save_flow_diagnostic = Mock()
    return task


def auto_battle_harness(**config):
    task = pvp_task(**config)
    task._click_template_until = Mock(return_value=True)
    task._wait_for_ocr_patterns = Mock(return_value=(True, "自动战斗 鲜血鸡尾酒"))
    task._click_screen_reference = Mock()
    task._click_ocr_pattern_center = Mock(return_value=True)
    task.capture_frame = Mock()
    task._ocr_text = Mock(return_value="40/40")
    task._bd2_ocr_boxes = Mock(return_value=boxes("40/40"))
    task._ensure_free_ap_enabled = Mock(return_value=True)
    task._ensure_free_ap_disabled = Mock(return_value=True)
    task._ensure_multiplier = Mock(return_value=True)
    task._select_max_battle_count = Mock()
    task._wait_battle_start_or_ap_shortage = Mock(return_value="started")
    task._leave_unstarted_battle = Mock(return_value=True)
    return task


def boxes(*texts):
    return [SimpleNamespace(name=text, x=930, y=990, width=60, height=30) for text in texts]


class RecentCartridgeTest(unittest.TestCase):
    def make_task(self, screens, *, size=(1920, 1080), recent_pvp=False):
        task = object.__new__(SquareGoddessTask)
        task._executor = SimpleNamespace(method=SimpleNamespace(width=size[0], height=size[1]))
        task.info_set = Mock()
        task._sleep_after_recognition = lambda: None
        task._recent_cartridge_is_pvp = lambda: recent_pvp
        task._save_flow_diagnostic = Mock()
        self.now = 0.0
        self.clicks = []
        self.screen_index = 0

        def sleep(seconds):
            self.now += seconds

        def click(x, y, after_sleep=0.0):
            self.clicks.append((x, y))
            sleep(after_sleep)

        def ocr_boxes():
            screen = screens[min(self.screen_index, len(screens) - 1)]
            self.screen_index += 1
            return screen

        task.sleep = sleep
        task.operate_click = click
        task._recent_cartridge_ocr_boxes = ocr_boxes
        self.enterContext(patch("src.tasks.BaseBD2Task.monotonic", side_effect=lambda: self.now))
        return task

    def test_reward_pair_and_stable_closure_gate_the_quick_switch(self):
        normal = boxes("酒馆")
        for title, dismiss in (
            (FIEND_HUNT_REWARD_TITLE, FIEND_HUNT_REWARD_DISMISS_TEXT),
            ("黄金竞技场体验赛季42奖励", "奖励已通过邮件发放。"),
        ):
            reward = boxes(title, dismiss)
            for screens, handled, ready in (
                ([reward[:1]], 0, False),
                ([reward[1:]], 0, False),
                ([reward[:1], reward[1:]], 0, False),
                ([reward], 1, False),
                ([reward, []], 1, False),
                ([reward, normal, reward], 1, False),
                ([reward, normal, normal], 1, True),
            ):
                with self.subTest(title=title, screens=screens):
                    task = self.make_task(screens)
                    quick = Mock(return_value=True)
                    page = Mock(return_value=True)
                    self.assertIs(
                        task.open_cartridge_quick_switcher(lambda: True, quick, page), ready
                    )
                    self.assertEqual(1 + handled, len(self.clicks))
                    self.assertEqual([(0.5, 1005 / 1080)] * handled, self.clicks[1:])
                    self.assertEqual(int(ready), quick.call_count)
                    self.assertEqual(int(ready), page.call_count)
                    self.assertEqual(int(not ready), task._save_flow_diagnostic.call_count)

    def test_late_rank_dismissal_gets_fresh_reads_and_still_requires_closure(self):
        season = boxes("赛季奖励", "点击画面即可返回")
        rank = boxes("段位下滑", "确认")
        normal = boxes("酒馆")
        for ending, expected in (
            ([normal, normal], CartridgeSpecialPageResult.HANDLED),
            ([rank], CartridgeSpecialPageResult.BLOCKED),
            ([[]], CartridgeSpecialPageResult.BLOCKED),
        ):
            with self.subTest(expected=expected, ending=ending):
                task = self.make_task(
                    [season, normal, normal, boxes("段位下滑"), rank, *ending],
                    recent_pvp=True,
                )
                original_read = task._recent_cartridge_ocr_boxes
                schedule = iter([0.08, 0.99, 1.6, 2.55, 3.9, 4.5, 4.85])

                def read():
                    self.now = max(self.now, next(schedule, self.now))
                    return original_read()

                task._recent_cartridge_ocr_boxes = read
                self.assertIs(
                    expected,
                    task._handle_recent_cartridge_special_pages(
                        timeout=3,
                        allow_season_reward=True,
                    ),
                )
                self.assertEqual(2, len(self.clicks))
                self.assertGreaterEqual(self.screen_index, 7)
                self.assertLess(self.now, 8)

    def test_shared_entry_scopes_pvp_pages_and_bounds_retries(self):
        absent = CartridgeSpecialPageResult.ABSENT
        handled = CartridgeSpecialPageResult.HANDLED
        for cls in (
            PVPTask,
            SquareGoddessTask,
            MapTradeTask,
            MapCollectionTask,
            BD2MapCollectionProbeTask,
        ):
            for pvp, home, overlays, quick, pages, ok, entries in (
                (True, [False], [], [], [], False, 0),
                (RuntimeError("封面缺失"), [True], [], [], [], False, 0),
                (True, [True], [handled], [True], [True], True, 1),
                (True, [True], [absent, handled], [False, True], [True], True, 1),
                (False, [True, True], [absent] * 3, [False, True], [True], True, 2),
                (False, [True, False], [absent] * 2, [False], [], False, 1),
                (False, [True], [absent], [True, False], [False], False, 1),
                (False, [True], [absent], [True, True], [False, True], True, 1),
            ):
                with self.subTest(cls=cls.__name__, pvp=pvp, home=home, quick=quick, pages=pages):
                    task = object.__new__(cls)
                    task.info_set = Mock()
                    task.log_warning = Mock()
                    task.operate_click = Mock()
                    task._sleep_after_recognition = Mock()
                    task._save_flow_diagnostic = Mock()
                    task._recent_cartridge_is_pvp = Mock(side_effect=[pvp])
                    task._handle_recent_cartridge_special_pages = Mock(side_effect=overlays)
                    ensure, click, confirm = (Mock(side_effect=seq) for seq in (home, quick, pages))
                    field = Mock()
                    self.assertIs(
                        task.open_cartridge_quick_switcher(
                            ensure, click, confirm, entry_ready=field
                        ),
                        ok,
                    )
                    self.assertEqual(entries, task.operate_click.call_count)
                    self.assertEqual(len(home), ensure.call_count)
                    self.assertEqual(len(quick), click.call_count)
                    self.assertEqual(len(pages), confirm.call_count)
                    options = {} if pvp else {"entry_ready": field}
                    self.assertEqual(
                        [call(allow_pvp_pages=pvp, **options)] * len(overlays),
                        task._handle_recent_cartridge_special_pages.call_args_list,
                    )

    def make_story_entry(self, scene, *, ambush=False, pvp=False, retreat_error=None):
        task = self.make_task([boxes("酒馆")], recent_pvp=pvp)
        frame = np.full((90, 160, 3), 120, dtype=np.uint8)
        events = []
        reads = 0

        def find(_frame):
            nonlocal reads, scene
            reads += 1
            if ambush and reads == 2:
                scene = "battle"
            return MatchResult(0.98, (60, 70), (10, 10)) if scene == "field" else None

        def retreat(_frame, *, stop_walk):
            nonlocal scene
            self.assertTrue(stop_walk)
            events.append("retreat")
            if retreat_error:
                raise retreat_error
            scene = "field"
            return frame

        def click(_point, _shape, after_sleep=0):
            self.assertEqual("field", scene)
            self.assertLess(self.now, 0.5)
            events.append("quick")
            task.sleep(after_sleep)

        nav = Navigator(task, SimpleNamespace(capture=lambda: frame, click_client=click))
        nav._loading_timeout = lambda: 1
        nav._black_frame = lambda _: scene == "black"
        nav._field_battle_visible = lambda _: scene == "battle"
        nav.classify = lambda *_: ScreenState.SANDBOX if scene == "field" else ScreenState.UNKNOWN
        nav._find_quick_switch_button = find
        nav._retreat_from_field_battle = retreat
        nav._wait_for_cartridge_home = Mock(side_effect=[True, False])
        nav._wait_for_quick_switch_page = Mock(return_value=True)
        nav._select_cartridge_category = Mock(return_value=True)
        return nav, events

    def test_story_recent_entry_confirms_field_and_recovers_before_quick_switch(self):
        for scene, options, expected_events, outcome in (
            ("field", {}, ["quick"], True),
            ("battle", {}, ["retreat", "quick"], True),
            ("field", {"ambush": True}, ["retreat", "quick"], True),
            ("unknown", {}, [], False),
            ("black", {}, [], False),
            ("battle", {"pvp": True}, [], False),
            (
                "battle",
                {"retreat_error": RuntimeError("遭遇战退出超时")},
                ["retreat"],
                RuntimeError,
            ),
            (
                "battle",
                {"retreat_error": TaskDisabledException()},
                ["retreat"],
                TaskDisabledException,
            ),
            ("battle", {"retreat_error": FinishedException()}, ["retreat"], FinishedException),
        ):
            with self.subTest(scene=scene, options=options):
                nav, events = self.make_story_entry(scene, **options)
                with patch("src.tasks.map_trade.navigator_story.monotonic", lambda: self.now):
                    if "retreat_error" in options:
                        with self.assertRaises(outcome) as caught:
                            nav._open_cartridge_quick_switcher()
                        self.assertIs(caught.exception, options["retreat_error"])
                    else:
                        self.assertIs(nav._open_cartridge_quick_switcher().success, outcome)
                self.assertEqual(1, len(self.clicks))
                self.assertEqual(expected_events, events)
                self.assertEqual(int(outcome is True), nav._wait_for_quick_switch_page.call_count)
                if outcome is True:
                    nav._wait_for_cartridge_home.assert_called_once()

    def test_recent_pvp_template_matches_live_home_fixture(self):
        fixture = Path(__file__).resolve().parent / "fixtures" / "pvp" / "recent_pvp_home_fhd.png"
        frame = cv2.imread(str(fixture), cv2.IMREAD_COLOR)
        self.assertIsNotNone(frame)
        self.assertEqual((1080, 1920, 3), frame.shape)

        task = object.__new__(SquareGoddessTask)
        task._recent_pvp_cartridge_template_cache = None
        result = task._match_recent_pvp_cartridge(frame)

        self.assertTrue(result.passed)
        self.assertEqual((1659, 935), result.position)
        self.assertEqual((94, 82), result.size)
        self.assertGreaterEqual(result.score, RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD)
        self.assertGreaterEqual(
            result.pixel_score,
            RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
        )
        self.assertGreaterEqual(
            result.zncc_score,
            RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
        )


class PVPHubMedalsFixtureTest(unittest.TestCase):
    """BUG-20260905-08（RPT-20260905-195025）：实机箱庭顶栏整体上移约 4px，
    旧 ROI 上边界零余量致勋章模板峰值 0.726<0.78、箱庭确认 30 秒超时。
    夹具为该上报诊断帧的黑底画布，仅按原始坐标保留模板搜索条带，
    匹配路径与线上完全一致（含 ROI 与像素门禁）。"""

    def test_shifted_top_bar_still_passes_medals_spec(self):
        frame = cv2.imread(
            str(Path(__file__).parent / "fixtures" / "pvp" / "pvp_hub_top_bar_shifted_fhd.png"),
            cv2.IMREAD_COLOR,
        )
        self.assertIsNotNone(frame)
        self.assertEqual((REFERENCE_HEIGHT, REFERENCE_WIDTH), frame.shape[:2])

        result = task_vision.match_template(frame, PVP_MEDALS_TEMPLATE, {}, TEMPLATE_DIR)
        self.assertTrue(
            task_vision.passes_match(result, PVP_MEDALS_TEMPLATE, {}),
            f"顶栏上移后应仍识别箱庭勋章图标，得到 score={result.score:.3f} "
            f"pixel={result.pixel_score:.3f}",
        )


class PVPTaskTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("src.tasks.PVPTask.pvp_settlement_end_ts", return_value=None))

    def test_slot_one_requires_battle_category_and_confirmed_hub(self):
        category = call(*BATTLE_GAMEPLAY_CATEGORY_POINT, after_sleep=0.0)
        slot = call(*PVP_CARTRIDGE_SLOT_POINT, after_sleep=0.0)
        for highlighted, hub, ok, clicks in (
            (False, False, False, [category]),
            (True, False, False, [category, slot]),
            (True, True, True, [category, slot]),
        ):
            with self.subTest(highlighted=highlighted, hub=hub):
                task = pvp_task()
                task.open_cartridge_quick_switcher = Mock(return_value=True)
                task._wait_for_battle_gameplay_category = Mock(return_value=highlighted)
                task._wait_for_pvp_hub_after_cart = Mock(return_value=hub)
                task._clear_pvp_hub_notice_if_present = Mock()
                task.operate_click = Mock()
                self.assertIs(task._enter_pvp_from_home(), ok)
                self.assertEqual(clicks, task.operate_click.call_args_list)
                self.assertEqual(
                    [call(0.5)]
                    + [call(FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS)] * int(highlighted),
                    task.sleep.call_args_list,
                )
                self.assertEqual(int(highlighted), task._wait_for_pvp_hub_after_cart.call_count)
                self.assertEqual(int(ok), task._clear_pvp_hub_notice_if_present.call_count)

    def test_pvp_entry_retries_same_page_once_and_observes_late_close(self):
        from src.interaction.BD2Interaction import InputTargetChanged

        for scenario in ("second", "late", "stuck", "changed", "unknown"):
            with self.subTest(scenario=scenario):
                task = object.__new__(PVPTask)
                task.info_set = Mock()
                task.log_info = Mock()
                task._save_flow_diagnostic = Mock()
                task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
                rank_page = [
                    SimpleNamespace(name="段位下滑", x=820, y=700, width=180, height=50),
                    SimpleNamespace(name="确认", x=930, y=990, width=60, height=30),
                ]
                now = [0.0]
                clicks = []
                invalidated = []

                def page(*_args, **_kwargs):
                    closed = len(clicks) >= (2 if scenario == "second" else 1)
                    if scenario == "stuck":
                        return rank_page
                    return [] if closed else rank_page

                def click(*_args, after_sleep=0, **_kwargs):
                    if scenario == "changed" and not invalidated:
                        invalidated.append(True)
                        raise InputTargetChanged("input changed")
                    clicks.append(now[0])
                    now[0] += after_sleep

                task._pvp_special_page_ocr_boxes = page
                task.operate_click = click
                task._match = lambda *_args: SimpleNamespace(score=0.99)
                task._passes = lambda _, spec: spec is PVP_MEDALS_TEMPLATE and scenario != "unknown"
                task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
                with patch("src.tasks.PVPTask.monotonic", side_effect=lambda: now[0]):
                    result = task._wait_for_pvp_hub_after_cart(timeout=10)
                self.assertEqual(scenario not in {"stuck", "unknown"}, result)
                self.assertEqual(2 if scenario in {"stuck", "second"} else 1, len(clicks))
                self.assertLessEqual(now[0], 16.5)

    def test_return_from_hub_clears_paired_rank_pages_before_home(self):
        rank = boxes("段位下滑。", "确认")
        clear = boxes("胜利分2532", "确认")
        for screens, expected_clicks, succeeds, timeout in (
            ([rank, rank, clear], 1, True, 3),
            ([rank[:1]], 0, False, 3),
            ([clear], 0, True, 3),
            ([clear, clear, rank, rank, clear], 1, True, 3),
            ([rank], 2, False, 3),
            ([rank[:1]] * 5 + [rank, rank, clear], 1, True, 3),
        ):
            with self.subTest(screens=screens):
                task = pvp_task(**{"PVP 返回箱庭等待秒数": timeout})
                clock = FakeClock()
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                task.capture_frame = Mock(return_value=frame)
                task._frame_confirms_home = Mock(return_value=False)
                task._pvp_special_page_ocr_boxes = Mock(
                    side_effect=screens + [screens[-1]] * 40,
                )
                task._match = Mock(return_value=SimpleNamespace(score=0.99))
                task._passes = Mock(return_value=True)
                task.sleep = clock.sleep
                task.operate_click = Mock(
                    side_effect=lambda *a, **kw: clock.sleep(kw["after_sleep"]),
                )
                task._click_pvp_home = Mock(return_value=True)
                task.prepare_home = Mock(
                    side_effect=lambda *a, **kw: bool(kw["exit_scene"] and kw["exit_scene"](frame)),
                )
                with patch("src.tasks.PVPTask.monotonic", clock):
                    self.assertIs(task._return_home_from_pvp_hub(), succeeds)
                self.assertEqual(expected_clicks, task.operate_click.call_count)
                for click in task.operate_click.call_args_list:
                    self.assertEqual((0.5, 1005 / 1080), click.args)
                    self.assertTrue(click.kwargs["require_fresh_target"])
                self.assertEqual(int(succeeds), task._click_pvp_home.call_count)
                if not succeeds:
                    task._save_flow_diagnostic.assert_called_once_with("pvp_hub_return_failed")

    def test_run_observes_settlement_from_sunday_2300_until_monday_0800(self):
        task = pvp_task(**{"竞技场战斗倍数": 1})
        task._ensure_pvp_hub = Mock(return_value=True)
        task._start_auto_battle = Mock(return_value=("started", 1))
        task._wait_result_and_leave = Mock(return_value=True)
        for moment, closed in (
            ("2026-11-01T22:59:59", False),
            ("2026-11-01T23:00:00", True),
            ("2026-11-02T07:59:59", True),
            ("2026-11-02T08:00:00", False),
        ):
            with self.subTest(time=moment):
                now = datetime.fromisoformat(moment + "+08:00").timestamp()
                with patch(
                    "src.tasks.PVPTask.pvp_settlement_end_ts",
                    lambda: run_history.pvp_settlement_end_ts(now),
                ):
                    self.assertTrue(task.run())
                self.assertIs(task.info["PVP 结算休战"], closed)
                self.assertEqual(closed, "2026-11-02 08:00" in task.info["状态"])
                self.assertEqual(int(not closed), task._ensure_pvp_hub.call_count)
                self.assertEqual(
                    [call(1)] * int(not closed), task._start_auto_battle.call_args_list
                )
                self.assertEqual(
                    [call(1)] * int(not closed), task._wait_result_and_leave.call_args_list
                )
                task._start_auto_battle.reset_mock()
                task._wait_result_and_leave.reset_mock()
                task._ensure_pvp_hub.reset_mock()

    def test_run_crossing_settlement_handles_entry_and_ap_retry(self):
        from src.tasks.run_history import is_successful_run

        opens = datetime(2026, 11, 2, 0, tzinfo=timezone.utc).timestamp()
        cases = [
            (entry_failed, ap_retry, recovered)
            for entry_failed, ap_retry in ((True, False), (False, False), (False, True))
            for recovered in (True, False)
        ]
        for entry_failed, ap_retry, recovered in cases:
            with self.subTest(entry_failed=entry_failed, ap_retry=ap_retry, recovered=recovered):
                task = object.__new__(PVPTask)
                task.config = {"竞技场战斗倍数": 40}
                task.info = {}
                task.info_set = task.info.__setitem__
                task.log_info = Mock()
                task._ensure_pvp_hub = Mock(return_value=not entry_failed)
                task.auto_return_main_home = Mock(return_value=recovered)
                task._wait_result_and_leave = Mock()
                if ap_retry:

                    def start(multiplier):
                        if multiplier == 40:
                            return "ap_shortage", 40
                        return PVPTask._start_auto_battle(task, 1)

                    task._start_auto_battle = start
                with patch("src.tasks.PVPTask.pvp_settlement_end_ts", side_effect=[None, opens]):
                    self.assertEqual(recovered, task.run())
                self.assertEqual(recovered, task.info["PVP 结算休战"])
                self.assertFalse(is_successful_run(task.info))
                task._wait_result_and_leave.assert_not_called()
                task.auto_return_main_home.assert_called_once_with(notify_failure=False)

    def test_auto_battle_uses_verified_menu_before_start(self):
        start = call(*PVP_BATTLE_START_SCREEN_POINT, after_sleep=2.0)
        icon = call(*PVP_AUTO_BATTLE_CLICK_REFERENCE, after_sleep=1.0)
        for menu_attempt in (1, 2):
            with self.subTest(menu_attempt=menu_attempt):
                task = auto_battle_harness()
                reads = (
                    [(True, "自动战斗")]
                    + [(False, "")] * (menu_attempt - 1)
                    + [(True, "鲜血鸡尾酒")]
                )
                task._wait_for_ocr_patterns.side_effect = reads
                self.assertEqual(("started", 1), task._start_auto_battle(1))
                self.assertEqual(
                    PVP_STAGE_CLICK_REFERENCE_OFFSET,
                    task._click_template_until.call_args.kwargs["target_reference_offset"],
                )
                self.assertEqual([icon, start], task._click_screen_reference.call_args_list)
                fallback = call(
                    ["自动战斗", "自动"],
                    name="PVP 自动战斗",
                    roi=PVP_AUTO_BATTLE_SCREEN_ROI,
                    after_sleep=1.0,
                )
                self.assertEqual(
                    [fallback] * (menu_attempt - 1), task._click_ocr_pattern_center.call_args_list
                )
                waits = task._wait_for_ocr_patterns.call_args_list[1:]
                self.assertEqual(
                    [PVP_AUTO_BATTLE_MENU_VERIFY_SECONDS] * menu_attempt,
                    [c.kwargs["timeout"] for c in waits],
                )

    def test_auto_battle_exhausts_retries_or_stops_before_an_unsafe_start(self):
        miss = (False, "")
        hit = (True, "自动战斗 鲜血鸡尾酒")
        for scenario, reads, selection, result, icons, diagnostics in (
            (
                "stage",
                [miss] * PVP_CLICK_VERIFY_ATTEMPTS,
                0,
                "failed",
                0,
                ["pvp_auto_battle_failed"],
            ),
            (
                "menu",
                [hit] + [miss] * PVP_CLICK_VERIFY_ATTEMPTS,
                0,
                "failed",
                2,
                ["pvp_auto_battle_failed"],
            ),
            ("multiplier", [hit, hit], 0, "failed", 1, []),
            ("settlement", [hit, hit], 1, "settlement", 1, []),
        ):
            with self.subTest(scenario=scenario):
                task = auto_battle_harness()
                task._wait_for_ocr_patterns.side_effect = reads
                task._ensure_multiplier.return_value = scenario != "multiplier"
                times = [None, 1793577600 if scenario == "settlement" else None]
                with patch("src.tasks.PVPTask.pvp_settlement_end_ts", side_effect=times):
                    self.assertEqual((result, 10), task._start_auto_battle(10))
                self.assertEqual(len(reads), task._wait_for_ocr_patterns.call_count)
                self.assertEqual(
                    [call(*PVP_AUTO_BATTLE_CLICK_REFERENCE, after_sleep=1.0)] * icons,
                    task._click_screen_reference.call_args_list,
                )
                self.assertEqual(selection, task._select_max_battle_count.call_count)
                self.assertEqual(
                    [call(d) for d in diagnostics], task._save_flow_diagnostic.call_args_list
                )
                task._wait_battle_start_or_ap_shortage.assert_not_called()

    def test_auto_battle_uses_only_confirmed_free_balance_unless_paid_is_enabled(self):
        for paid, readings, switch_ok, state, multiplier, enable, skip in (
            (
                False,
                ["0/40 +7", "0 / 40"],
                True,
                "free_ap_unavailable",
                40,
                0,
                "免费鲜血鸡尾酒已耗尽",
            ),
            (False, ["0/40", "7/40", "无法识别"], True, "started", 40, 1, ""),
            (False, ["7/40", "7/40"], True, "started", 1, 1, ""),
            (
                False,
                ["40/40", "40/40"],
                False,
                "free_ap_unavailable",
                40,
                1,
                "未能开启仅用免费鸡尾酒",
            ),
            (True, [], True, "started", 40, 0, ""),
        ):
            with self.subTest(paid=paid, readings=readings, switch_ok=switch_ok):
                task = auto_battle_harness(**({"允许使用非免费鸡尾酒": True} if paid else {}))
                task._bd2_ocr_boxes.side_effect = [boxes(text) for text in readings]
                task._ensure_free_ap_enabled.return_value = switch_ok
                self.assertEqual((state, multiplier), task._start_auto_battle(40))
                self.assertEqual(len(readings), task.capture_frame.call_count)
                self.assertEqual(enable, task._ensure_free_ap_enabled.call_count)
                self.assertEqual(int(paid), task._ensure_free_ap_disabled.call_count)
                starts = [call(multiplier)] if state == "started" else []
                self.assertEqual(starts, task._ensure_multiplier.call_args_list)
                self.assertEqual(starts, task._wait_battle_start_or_ap_shortage.call_args_list)
                self.assertEqual(int(bool(skip)), task._leave_unstarted_battle.call_count)
                self.assertIn(
                    skip or "战斗开始", task.info["状态"] if skip else task.info["当前阶段"]
                )
                clicks = [call(*PVP_AUTO_BATTLE_CLICK_REFERENCE, after_sleep=1.0)]
                if starts:
                    clicks.append(call(*PVP_BATTLE_START_SCREEN_POINT, after_sleep=2.0))
                self.assertEqual(clicks, task._click_screen_reference.call_args_list)

    def test_free_balance_requires_one_complete_ocr_box_and_scales_its_roi(self):
        for width, height in ((1920, 1080), (1280, 720), (2560, 1440)):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            left, top = round(1530 * width / 1920), round(36 * height / 1080)
            right, bottom = round(1815 * width / 1920), round(81 * height / 1080)
            frame[top:bottom, left:right] = 123
            task = pvp_task()
            task.capture_frame = Mock(return_value=frame)
            task.ocr = Mock(return_value=boxes("40/40"))
            self.assertEqual(40, task._confirm_free_ap_balance())
            crop = task.ocr.call_args.kwargs["frame"]
            self.assertEqual((bottom - top, right - left, 3), crop.shape)
            self.assertTrue(np.all(crop == 123))

        clipped = SimpleNamespace(name="0/40", x=2, height=20)
        for readings, expected in (
            (boxes("3", "40/40"), 40),
            (boxes("3", "0/40"), 0),
            ([clipped], None),
            (boxes("7/40", "40/40"), None),
            (boxes("40／40"), 40),
            (boxes("41/40"), None),
            (boxes("340/40"), None),
            (boxes("0/40 7/40"), None),
        ):
            with self.subTest(readings=readings):
                task = pvp_task()
                task.capture_frame = Mock()
                task._bd2_ocr_boxes = Mock(return_value=readings)
                self.assertEqual(expected, task._confirm_free_ap_balance())

    def test_free_switch_requires_readback_and_has_a_bounded_click_budget(self):
        on = np.full((1440, 2560, 3), (60, 140, 200), dtype=np.uint8)
        off = np.full_like(on, (200, 140, 60))
        for frame, expected, ratio in ((on, True, "1.000"), (off, False, "0.000")):
            with self.subTest(yellow_ratio=ratio):
                task = pvp_task()
                task.capture_frame = Mock(return_value=frame)
                self.assertIs(task._free_ap_switch_on(), expected)
                self.assertEqual(f"开关黄色占比 {ratio}", task.info["PVP 免费AP"])

        for enabled in (True, False):
            target, initial = (on, off) if enabled else (off, on)
            for clicks, flips in (
                (0, True),
                (1, True),
                (PVP_CLICK_VERIFY_ATTEMPTS, True),
                (PVP_CLICK_VERIFY_ATTEMPTS, False),
            ):
                with self.subTest(enabled=enabled, clicks=clicks, flips=flips):
                    task = pvp_task()
                    task._click_screen_reference = Mock()
                    task.capture_frame = Mock(
                        side_effect=[initial] * clicks + [target if flips else initial]
                    )
                    set_switch = (
                        task._ensure_free_ap_enabled if enabled else task._ensure_free_ap_disabled
                    )
                    self.assertIs(set_switch(), flips)
                    self.assertEqual(
                        [call(*PVP_FREE_AP_SWITCH_SCREEN_POINT, after_sleep=1.0)] * clicks,
                        task._click_screen_reference.call_args_list,
                    )

    def test_skipping_cocktails_closes_menu_before_formation_and_confirms_home(self):
        menu_text = "自动战斗 设置鲜血鸡尾酒消耗量"
        for faded, recovered in ((True, True), (True, False), (False, False)):
            with self.subTest(faded=faded, recovered=recovered):
                task = pvp_task()
                menu, formation, hub = object(), object(), object()
                task.capture_frame = Mock(side_effect=[menu, formation, hub])
                task._ocr_text = Mock(side_effect=[menu_text, "", ""] if faded else [menu_text] * 3)
                task._click_ocr_pattern_center = Mock(return_value=True)
                task._formation_return_arrow = Mock(return_value=(150, 74))
                task._click_frame_point = Mock()
                task._frame_confirms_home = Mock(return_value=False)
                task._match = Mock()
                task._passes = Mock(return_value=True)
                task._return_home_from_pvp_hub = Mock(return_value=recovered)
                task.recover_daily_home = Mock(return_value=False)
                task.back = Mock()
                self.assertEqual(
                    "free_ap_unavailable" if recovered else "home_recovery_failed",
                    task._skip_cocktail_battle("免费鸡尾酒不足"),
                )
                cancel_roi = task._screen_reference_roi_to_reference_roi(
                    PVP_AUTO_BATTLE_CANCEL_SCREEN_ROI
                )
                cancel = call(["^取消$"], "PVP 取消自动战斗", cancel_roi, after_sleep=0.8)
                self.assertEqual(
                    [cancel] * (1 if faded else 2), task._click_ocr_pattern_center.call_args_list
                )
                self.assertEqual(
                    [call(formation)] * int(faded), task._formation_return_arrow.call_args_list
                )
                self.assertEqual(
                    [call(formation, (150, 74), after_sleep=1.0)] * int(faded),
                    task._click_frame_point.call_args_list,
                )
                self.assertEqual(int(not recovered), task.recover_daily_home.call_count)
                self.assertEqual(0.0, task._auto_return_home_cooldown_until)
                task.back.assert_not_called()

    def test_run_retries_shortage_at_one_and_requires_home_before_recording_skip(self):
        for recovery, expected in (
            (True, True),
            (False, False),
            (RuntimeError("capture unavailable"), False),
            (TaskDisabledException(), TaskDisabledException),
            (FinishedException(), FinishedException),
        ):
            with self.subTest(recovery=recovery):
                task = auto_battle_harness(**{"竞技场战斗倍数": 10})
                task._ensure_pvp_hub = Mock(return_value=True)
                task._leave_unstarted_battle.side_effect = [recovery]
                del task._wait_battle_start_or_ap_shortage
                task._ocr_text.side_effect = lambda _, name, **kw: {
                    "PVP AP不足": "鲜血鸡尾酒不足",
                    "PVP 战斗中": "",
                    "PVP 免费鸡尾酒余额": "40/40",
                }[name]
                task._wait_result_and_leave = Mock()
                if isinstance(expected, type):
                    with self.assertRaises(expected):
                        task.run()
                else:
                    self.assertIs(task.run(), expected)
                self.assertEqual([call(10), call(1)], task._ensure_multiplier.call_args_list)
                self.assertEqual("鲜血鸡尾酒不足", task.info["PVP AP不足 OCR"])
                task._wait_result_and_leave.assert_not_called()
                task.log_completion.assert_not_called()

    def _make_multiplier_harness(
        self, swallow_button=False, swallow_option=False, swallow_step=False
    ):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        state = {"clicks": [], "dialog_open": False, "setting": 40, "main": 40}

        def fake_click(x, y, after_sleep=0.0):
            state["clicks"].append((x, y))
            if (x, y) == PVP_MULTIPLIER_BUTTON_SCREEN_POINT:
                needed = 2 if swallow_button else 1
                state["dialog_open"] = state["clicks"].count((x, y)) >= needed
            elif (x, y) == PVP_MULTIPLIER_1_OPTION_SCREEN_POINT:
                needed = 2 if swallow_option else 1
                if state["clicks"].count((x, y)) >= needed:
                    state["setting"] = 1
            elif (x, y) == PVP_MULTIPLIER_40_OPTION_SCREEN_POINT:
                state["setting"] = 40
            elif (x, y) == PVP_MULTIPLIER_PLUS_SCREEN_POINT:
                if not swallow_step or state["clicks"].count((x, y)) > 1:
                    state["setting"] = min(40, state["setting"] + 1)
            elif (x, y) == PVP_MULTIPLIER_CONFIRM_SCREEN_POINT:
                state["main"] = state["setting"]
                state["dialog_open"] = False

        task._click_screen_reference = fake_click
        task._multiplier_matches = lambda multiplier, timeout=2.0: state["main"] == multiplier
        task._setting_multiplier_matches = lambda multiplier: (
            state["dialog_open"] and state["setting"] == multiplier
        )
        task._setting_multiplier_value = lambda: state["setting"]
        task._wait_for_ocr_patterns = lambda *args, **kwargs: (
            state["dialog_open"],
            "设置鲜血鸡尾酒消耗量",
        )
        return task, state

    def test_multiplier_selects_each_integer_and_recovers_lost_clicks(self):
        for target, swallow in [(i, False) for i in range(1, 41)] + [(1, True)]:
            with self.subTest(target=target, swallow=swallow):
                task, state = self._make_multiplier_harness(
                    swallow_button=swallow, swallow_option=swallow
                )
                state["main"] = 0
                self.assertTrue(task._ensure_multiplier(target))
                self.assertEqual(target, state["main"])
                self.assertEqual(
                    0 if target == 40 else target - 1,
                    state["clicks"].count(PVP_MULTIPLIER_PLUS_SCREEN_POINT),
                )
                self.assertEqual(
                    2 if swallow else 1, state["clicks"].count(PVP_MULTIPLIER_BUTTON_SCREEN_POINT)
                )
                self.assertEqual(1, state["clicks"].count(PVP_MULTIPLIER_CONFIRM_SCREEN_POINT))
                self.assertEqual(
                    2 if swallow else int(target != 40),
                    state["clicks"].count(PVP_MULTIPLIER_1_OPTION_SCREEN_POINT),
                )

    def test_multiplier_stops_on_unconfirmed_open_or_main_panel_readback(self):
        for dialog, main_readable, expected, confirm, plus in (
            (False, False, False, 0, 0),
            (True, False, False, 1, 1),
            (True, True, True, 1, 1),
        ):
            with self.subTest(dialog=dialog, main_readable=main_readable):
                task, state = self._make_multiplier_harness()
                task._setting_multiplier_value = lambda: None
                task._setting_multiplier_matches = lambda _: False
                task._multiplier_matches = lambda target, **_: (
                    main_readable and state["main"] == target
                )
                if not dialog:
                    task._wait_for_ocr_patterns = Mock(return_value=(False, ""))
                self.assertIs(task._ensure_multiplier(2), expected)
                self.assertEqual(plus, state["clicks"].count(PVP_MULTIPLIER_PLUS_SCREEN_POINT))
                self.assertEqual(
                    confirm, state["clicks"].count(PVP_MULTIPLIER_CONFIRM_SCREEN_POINT)
                )
                self.assertEqual(
                    1 if dialog else PVP_CLICK_VERIFY_ATTEMPTS,
                    state["clicks"].count(PVP_MULTIPLIER_BUTTON_SCREEN_POINT),
                )

    def test_rank_confirmation_requires_a_fresh_button_after_settling(self):
        for state, fresh, expected, hub_reads in (
            ("confirm", (970.0, 1040.0), True, 1),
            ("confirm", None, True, 0),
            ("timeout", None, False, 0),
        ):
            with self.subTest(state=state, fresh=fresh):
                task = pvp_task()
                task._wait_for_pvp_hub_or_confirm = Mock(
                    side_effect=[
                        (state, "恭喜晋级", (960.0, 1030.0)),
                        ("hub", "", None),
                    ]
                )
                task.capture_frame = Mock()
                task._confirm_button_ocr = Mock(return_value=("确认", fresh))
                task._click_frame_point = Mock()
                task._wait_for_template = Mock(return_value=True)
                self.assertIs(task._ensure_pvp_hub_after_leave(), expected)
                self.assertEqual(
                    [call(PVP_RANK_CONFIRM_SETTLE_SECONDS)] * int(state == "confirm"),
                    task.sleep.call_args_list,
                )
                self.assertEqual(int(state == "confirm"), task.capture_frame.call_count)
                self.assertEqual(
                    [call(task.capture_frame.return_value, fresh, after_sleep=1.0)]
                    * int(fresh is not None),
                    task._click_frame_point.call_args_list,
                )
                self.assertEqual(
                    [call(PVP_MEDALS_TEMPLATE, timeout=10.0, name="PVP 箱庭")] * hub_reads,
                    task._wait_for_template.call_args_list,
                )

    def test_departure_retries_visible_leave_once_within_the_original_deadline(self):
        for fresh, ending, expected in (
            ((1714.0, 1017.0), "hub", True),
            ((1714.0, 1017.0), "leave", False),
            (None, "hub", True),
        ):
            with self.subTest(fresh=fresh, ending=ending):
                task = pvp_task()
                task._wait_for_pvp_hub_or_confirm = Mock(
                    side_effect=[
                        ("leave", "成功页:离开", (1728.0, 1008.0)),
                        (ending, "", None),
                    ]
                )
                task.capture_frame = Mock()
                task._leave_button_ocr = Mock(return_value=("成功页:离开", fresh))
                task._click_frame_point = Mock()
                with patch("src.tasks.PVPTask.monotonic", side_effect=[100, 100, 102]):
                    self.assertIs(task._ensure_pvp_hub_after_leave(), expected)
                self.assertEqual(
                    [
                        call(timeout=10.0, return_on_leave=True),
                        call(timeout=8.0, return_on_leave=False),
                    ],
                    task._wait_for_pvp_hub_or_confirm.call_args_list,
                )
                self.assertEqual(
                    [call(task.capture_frame.return_value, fresh, after_sleep=2.0)]
                    * int(fresh is not None),
                    task._click_frame_point.call_args_list,
                )

    def test_result_majority_gates_leave_and_success_requires_confirmed_home(self):
        for visible, home in ((False, False), (True, False), (True, True)):
            with self.subTest(visible=visible, home=home):
                task = pvp_task()
                task._wait_for_ocr_pattern_majority = Mock(return_value=(visible, "反复战斗结果"))
                task._click_screen_reference = Mock()
                task._click_leave_button = Mock(return_value=True)
                task._ensure_pvp_hub_after_leave = Mock(return_value=True)
                task._return_home_from_pvp_hub = Mock(return_value=home)
                self.assertEqual(visible and home, task._wait_result_and_leave(4))
                wait = task._wait_for_ocr_pattern_majority.call_args
                self.assertEqual(4, wait.kwargs["min_matches"])
                self.assertEqual(300, wait.kwargs["timeout"])
                self.assertEqual(
                    task._screen_reference_roi_to_reference_roi(PVP_RESULT_SCREEN_ROI),
                    wait.kwargs["roi"],
                )
                self.assertEqual(
                    [
                        call(
                            *PVP_RESULT_CLOSE_SCREEN_POINT,
                            after_sleep=PVP_RESULT_CLOSE_AFTER_SECONDS,
                        )
                    ]
                    * int(visible),
                    task._click_screen_reference.call_args_list,
                )
                self.assertEqual(int(visible), task._click_leave_button.call_count)
                self.assertEqual(int(visible), task._return_home_from_pvp_hub.call_count)

    def test_return_home_retries_only_while_pvp_hub_remains_visible(self):
        self.assertEqual(
            CHAPTER_HOME_POINT,
            (
                PVP_BACK_HOME_REFERENCE_POINT[0] / REFERENCE_WIDTH,
                PVP_BACK_HOME_REFERENCE_POINT[1] / REFERENCE_HEIGHT,
            ),
        )
        for home_at, hub_visible_until, expected_clicks in ((1, 1, 1), (12, 12, 2), (12, 1, 1)):
            with self.subTest(home_at=home_at, hub_visible_until=hub_visible_until):
                clock = FakeClock()
                task = object.__new__(PVPTask)
                task.config = {}
                task.info_set = Mock()
                task.capture_frame = Mock()
                task._read_home_confirmation_signals = lambda *_: (clock() >= home_at, 0, 0, "")
                task.dismiss_home_announcement = Mock(return_value=False)
                task._wait_for_pvp_hub_clearing_overlays = Mock(return_value=True)
                task._match = Mock()
                task._passes = lambda *_: clock() < hub_visible_until
                task._click_reference = Mock()
                task._wait_loading_if_present = Mock()
                task.sleep = clock.sleep
                with patch("src.tasks.BaseBD2Task.monotonic", clock):
                    self.assertTrue(task._return_home_from_pvp_hub())
                task._wait_for_pvp_hub_clearing_overlays.assert_called_once_with(
                    10.0, phase="PVP 退出", diagnostic="pvp_hub_return_failed",
                )
                home_click = call(*PVP_BACK_HOME_REFERENCE_POINT, after_sleep=2.0)
                self.assertEqual(
                    [home_click] * expected_clicks, task._click_reference.call_args_list
                )
                self.assertEqual(
                    [call("PVP 返回主页")] * expected_clicks,
                    task._wait_loading_if_present.call_args_list,
                )
