import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks.FreeGachaTask import (
    BACK_PAGE_KEYWORDS,
    FREE_ENTRY_RETRY_CHANGED,
    FREE_ENTRY_RETRY_DIALOG,
    FREE_ENTRY_RETRY_LOADING,
    FREE_ENTRY_RETRY_READY,
    LOADING_TEMPLATE,
    FreeDrawConfirmation,
    FreeGachaTask,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH
from tests.helpers import StageHistoryTestCase

free_gacha_module = importlib.import_module("src.tasks.FreeGachaTask")


def confirmation_boxes(pool="装备", count=3, cost=None, scale=1):
    rows = [
        ("确认抽抽乐", 420, 200, 160, 30),
        (f"目前有{count}次免费{pool}抽抽乐可以抽取。", 300, 270, 550, 30),
        ("是否全部进行？", 410, 330, 220, 30),
        (cost or f"免费{count}次", 550, 380, 90, 30),
        ("取消", 400, 430, 90, 30),
        ("确认", 650, 430, 90, 30),
        ("付费0 免费587143", 700, 20, 200, 30),
        ("付费抽抽乐90", 700, 700, 200, 30),
    ]
    return [SimpleNamespace(name=name, x=x * scale, y=y * scale,
                            width=w * scale, height=h * scale) for name, x, y, w, h in rows]


class FreeGachaTaskHelperTest(StageHistoryTestCase):
    def test_pending_free_draw_recovers_only_after_current_free_confirmation(self):
        from src.tasks import run_history

        for pool in ("服装抽抽乐", "装备抽抽乐"):
            for days in (0, 1, 7):
                with self.subTest(pool=pool, days=days):
                    self.reset_stage_history()
                    task = object.__new__(FreeGachaTask)
                    task.config = {}
                    task.info_set = Mock()
                    task.log_info = Mock()
                    task._wait_for_free_gacha = Mock(return_value=(True, "所有免费抽抽乐", True))
                    task._open_confirm_dialog_with_retry = Mock(return_value=True)
                    task._wait_for_free_confirmation = Mock(return_value=None)
                    task._confirm_dialog_submission = Mock(return_value=True)
                    task._handle_result_until_back = Mock(return_value=True)
                    checkpoint = f"免费抽:{pool}"
                    run_history.save_stage(task, checkpoint, "pending", scope="old-cycle")
                    self.history_mock.return_value = run_history.RunHistoryStore(self.history.path)
                    with patch.object(run_history, "day_start_ts", return_value=days * 86400):
                        self.assertFalse(task._run_free_section(pool, True))
                        task._confirm_dialog_submission.assert_not_called()
                        self.assertEqual(
                            "old-cycle", run_history.read_stage(task, checkpoint)["scope"]
                        )
                        task._wait_for_free_confirmation.return_value = FreeDrawConfirmation(
                            3, (.7, .6)
                        )
                        task._wait_for_free_gacha.side_effect = [
                            (True, "所有免费抽抽乐", True), (False, "单抽消耗已确认", True)]

                        def submit(name, count):
                            record = run_history.read_stage(task, checkpoint)
                            self.assertEqual((pool, 3, "pending"), (name, count, record["state"]))
                            self.assertEqual(3, record["evidence"]["count"])
                            return True

                        task._confirm_dialog_submission.side_effect = submit
                        self.assertTrue(task._run_free_section(pool, True))
                        self.assertEqual("done", run_history.read_stage(task, checkpoint)["state"])

    def test_free_submission_waits_for_durable_history_and_result_confirmation(self):
        from src.tasks import run_history

        for failure in ("save", "submission", "result"):
            with self.subTest(failure=failure):
                self.reset_stage_history()
                task = object.__new__(FreeGachaTask)
                task.config = {}
                task.info_set = Mock()
                task.log_info = Mock()
                task._wait_for_free_gacha = Mock(return_value=(True, "所有免费抽抽乐", True))
                task._open_confirm_dialog_with_retry = Mock(return_value=True)
                task._wait_for_free_confirmation = Mock(
                    return_value=FreeDrawConfirmation(3, (.7, .6))
                )
                task._confirm_dialog_submission = Mock(return_value=failure != "submission")
                task._handle_result_until_back = Mock(return_value=failure != "result")
                if failure == "save":
                    with patch.object(self.history, "_save", return_value=False):
                        with self.assertRaises(RuntimeError):
                            task._run_free_section("装备抽抽乐", True)
                    task._confirm_dialog_submission.assert_not_called()
                else:
                    self.assertFalse(task._run_free_section("装备抽抽乐", True))
                    self.assertEqual(1, task._confirm_dialog_submission.call_count)
                    record = run_history.read_stage(task, "免费抽:装备抽抽乐")
                    self.assertEqual("pending", record["state"])
                    if failure == "submission":
                        task._handle_result_until_back.assert_not_called()

    def test_successful_run_emits_standalone_completion_notification(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"启用": True}
        task.info_set = lambda *_args, **_kwargs: None
        notifications = []
        task.log_info = lambda message, notify=False: notifications.append(
            (message, notify)
        )
        task._click_reference = lambda *_args, **_kwargs: None
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task._wait_loading_or_gacha_page = lambda *_args, **_kwargs: (
            "target",
            True,
            "抽抽乐",
        )
        task._wait_for_gacha_page = lambda *_args, **_kwargs: True
        task._wait_for_clothing_pool_page = lambda *_args, **_kwargs: True
        task._wait_for_equipment_pool_page = lambda *_args, **_kwargs: True
        task._run_free_section = lambda *_args, **_kwargs: True
        task._sleep_after_recognition = lambda: None
        task._wait_loading_or_home_confirmation = lambda *_args, **_kwargs: True

        self.assertTrue(FreeGachaTask.run(task))
        self.assertEqual(
            [("白嫖抽抽乐：流程完成。", True)],
            notifications,
        )

    def test_run_verifies_equipment_pool_title_and_claim(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"启用": True}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._click_reference = lambda *_args, **_kwargs: None
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task._wait_loading_or_gacha_page = lambda *_args, **_kwargs: (
            "target",
            True,
            "抽抽乐",
        )
        task._sleep_after_recognition = lambda: None
        task._wait_loading_or_home_confirmation = lambda *_args, **_kwargs: True
        sections = []
        task._run_free_section = (
            lambda name, verify_finished: sections.append((name, verify_finished))
            or True
        )
        pool_waits = []
        task._wait_for_clothing_pool_page = (
            lambda name: pool_waits.append(("clothing", name)) or True
        )
        task._wait_for_equipment_pool_page = (
            lambda name: pool_waits.append(("equipment", name)) or True
        )

        self.assertTrue(FreeGachaTask.run(task))
        self.assertEqual(
            [("服装抽抽乐", True), ("装备抽抽乐", True)],
            sections,
        )
        self.assertEqual(
            [("clothing", "确认服装池"), ("equipment", "切换装备抽卡")],
            pool_waits,
        )

    def test_run_requires_home_confirmation_before_entry_click(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"启用": True}
        confirmations = []
        task._wait_for_home_confirmation = (
            lambda name, *_args, **_kwargs: confirmations.append(name) or False
        )
        statuses = []
        task.info_set = lambda key, value: statuses.append((key, value))
        task.log_info = lambda *_args, **_kwargs: None
        task._click_reference = lambda *_args, **_kwargs: self.fail(
            "入口主页确认失败时不得点击"
        )
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "入口主页确认失败时不得点击"
        )

        self.assertFalse(FreeGachaTask.run(task))
        self.assertEqual(["白嫖抽抽乐入口前主页确认"], confirmations)
        self.assertIn(("状态", "白嫖抽抽乐入口前主页确认失败。"), statuses)

    def test_run_failure_paths_write_failure_status(self):
        # 中途失败的路径必须把"失败"写进状态，否则 run_history 会把本次
        # 运行记成成功、调度账本当天不再补跑（BUG-20260912-01）。
        cases = [
            ("stuck", True, "白嫖抽抽乐进入抽卡页失败。"),
            ("target", False, "白嫖抽抽乐进入抽卡页失败。"),
        ]
        for loading_state, gacha_found, expected_status in cases:
            with self.subTest(loading_state=loading_state):
                task = object.__new__(FreeGachaTask)
                task.config = {"启用": True}
                statuses = []
                task.info_set = lambda key, value: statuses.append((key, value))
                task.log_info = lambda *_args, **_kwargs: None
                task._click_reference = lambda *_args, **_kwargs: None
                task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
                task._wait_loading_or_gacha_page = lambda *_args, **_kwargs: (
                    loading_state,
                    gacha_found,
                    "",
                )
                task._wait_for_gacha_page = lambda *_args, **_kwargs: False

                self.assertFalse(FreeGachaTask.run(task))
                self.assertIn(("状态", expected_status), statuses)

    def test_keyword_match_count_ignores_spaces_and_case(self):
        text = "So PERFECT！ 简直是无可挑剔的 masterpiece…！"

        self.assertEqual(
            2,
            FreeGachaTask._keyword_match_count(
                text,
                ["so perfect", "无可挑剔的masterpiece", "不存在"],
            ),
        )

    def test_keyword_match_count_accepts_90_percent_similarity(self):
        text = "抽抽乐券 可免费抽1欠的抽抽乐券 查看获取途径"

        self.assertEqual(
            3,
            FreeGachaTask._keyword_match_count(text, BACK_PAGE_KEYWORDS),
        )

    def test_reference_click_uses_1920_by_1080_ratios(self):
        task = object.__new__(FreeGachaTask)
        calls = {}

        def fake_operate_click(x, y, after_sleep=0):
            calls["x"] = x
            calls["y"] = y
            calls["after_sleep"] = after_sleep

        task.operate_click = fake_operate_click

        task._click_reference(347, 973, after_sleep=0.5)

        self.assertEqual(347 / REFERENCE_WIDTH, calls["x"])
        self.assertEqual(973 / REFERENCE_HEIGHT, calls["y"])
        self.assertEqual(0.5, calls["after_sleep"])

    def test_loading_wait_prioritizes_gacha_page_ocr(self):
        task = object.__new__(FreeGachaTask)
        task.config = {
            "加载页面阈值": 0.72,
            "loading 出现等待秒数": 1.0,
            "loading 消失等待秒数": 1.0,
            "抽卡页面关键词最低命中数": 1,
        }
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        loading_seen = {"value": False}

        def fake_match(_frame, spec):
            if spec is LOADING_TEMPLATE:
                loading_seen["value"] = True
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._ocr_text = lambda *_args, **_kwargs: "服装" if loading_seen["value"] else ""

        self.assertEqual(
            ("target", True, "服装"),
            FreeGachaTask._wait_loading_or_gacha_page(task, "进入抽卡页"),
        )

    def test_loading_wait_prioritizes_complete_home_confirmation(self):
        task = object.__new__(FreeGachaTask)
        task.config = {
            "加载页面阈值": 0.72,
            "loading 出现等待秒数": 1.0,
            "loading 消失等待秒数": 1.0,
            "主页压暗阈值": 185.0,
        }
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        loading_seen = {"value": False}

        def fake_match(_frame, spec):
            if spec is LOADING_TEMPLATE:
                loading_seen["value"] = True
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._home_confirmation_ok = (
            lambda _frame, _name: loading_seen["value"]
        )
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: self.fail(
            "home should be accepted before waiting for loading to disappear"
        )

        self.assertTrue(
            FreeGachaTask._wait_loading_or_home_confirmation(task, "返回主页")
        )

    def test_home_confirmation_requires_keyword_votes_brightness_and_gacha_ocr(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"主页压暗阈值": 185.0}
        task.info_set = lambda *_args, **_kwargs: None
        left_text = {"value": "我的小屋 经营管理格鲁TALK 街机游戏"}
        gacha_text = {"value": "抽抽乐"}

        class FakeVision:
            def ocr_text(self, _frame, name, relative_roi=None, **_kwargs):
                self.last_relative_roi = relative_roi
                return left_text["value"] if "左列" in name else gacha_text["value"]

        vision = FakeVision()
        task._quick_vision = lambda: vision
        bright_frame = np.full((1080, 1920, 3), 255, dtype=np.uint8)
        dimmed_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        confirmed, left_hits, p95_brightness, text = task._home_confirmation_signals(
            bright_frame,
            "返回主页",
        )
        self.assertTrue(confirmed)
        self.assertEqual(3, left_hits)
        self.assertEqual(255.0, p95_brightness)
        self.assertEqual("抽抽乐", text)

        left_text["value"] = "我的小屋"
        self.assertFalse(task._home_confirmation_signals(bright_frame, "返回主页")[0])

        left_text["value"] = "我的小屋 格鲁TALK 街机游戏"
        self.assertFalse(task._home_confirmation_signals(dimmed_frame, "返回主页")[0])

        gacha_text["value"] = ""
        self.assertFalse(task._home_confirmation_signals(bright_frame, "返回主页")[0])
        self.assertIsNotNone(vision.last_relative_roi)

    def test_result_wait_handles_late_results_without_pressing_unknown_screens(self):
        result = "抽抽乐券 可免费抽1次的抽抽乐券 查看获取途径"
        for screen in ("跳过", "", "确认抽抽乐", "购买", "装备抽抽乐"):
            with self.subTest(screen=screen):
                task = object.__new__(FreeGachaTask)
                task.config = {"跳过点击间隔秒数": .2}
                task.info_set = Mock()
                now, clicks = [0.0], []
                task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
                task.capture_frame = lambda: np.zeros((800, 1000, 3), dtype=np.uint8)
                task._match = lambda *_args: None
                task._passes = lambda *_args: False
                task.operate_click = lambda *point, **kwargs: clicks.append(point)

                def boxes(*_args):
                    text = result if now[0] >= 10 else screen
                    return [SimpleNamespace(name=text, x=850, y=20, width=80, height=30)]

                task._gacha_ocr_boxes = boxes
                with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
                    found, _ = task._click_skip_until_back_page("装备抽抽乐")
                self.assertEqual(screen in ("跳过", ""), found)
                self.assertEqual(screen == "跳过", bool(clicks))
                self.assertTrue(all(0 < x < 1 and 0 < y < 1 for x, y in clicks))

    def test_result_handler_clicks_ticket_detail_then_backs_and_verifies_gacha_page(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        sleeps = []
        waits = []

        task._click_skip_until_back_page = lambda _section_name: (True, "付费 免费")
        task._click_reference = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task.sleep = lambda seconds: sleeps.append(seconds)
        task._wait_for_gacha_page = lambda name: waits.append(name) or True

        self.assertTrue(FreeGachaTask._handle_result_until_back(task, "服装抽抽乐"))
        self.assertEqual([1.0], sleeps)
        self.assertEqual([(1420, 326, 1.0), (105, 51, 0.0)], clicks)
        self.assertEqual(["服装抽抽乐 返回抽卡页"], waits)

    def test_open_confirm_dialog_retries_while_entry_still_ready(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task._click_reference = lambda *_args, **_kwargs: clicks.append(1)
        dialog_waits = {"value": 0}

        def fake_wait_confirm(_section_name):
            dialog_waits["value"] += 1
            return dialog_waits["value"] >= 3

        task._wait_for_confirm_dialog = fake_wait_confirm
        states = iter(
            [FREE_ENTRY_RETRY_READY, FREE_ENTRY_RETRY_READY]
        )
        task._free_entry_retry_state = lambda _section_name: next(states)

        self.assertTrue(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual(3, len(clicks))

    def test_open_confirm_dialog_stops_retry_when_entry_gone(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task._click_reference = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_confirm_dialog = lambda *_args, **_kwargs: False
        task._free_entry_retry_state = lambda *_args, **_kwargs: (
            FREE_ENTRY_RETRY_CHANGED
        )

        self.assertFalse(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual(1, len(clicks))

    def test_open_confirm_dialog_accepts_dialog_found_before_retry(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task._click_reference = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_confirm_dialog = lambda *_args, **_kwargs: False
        task._free_entry_retry_state = lambda *_args, **_kwargs: (
            FREE_ENTRY_RETRY_DIALOG
        )

        self.assertTrue(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual(1, len(clicks))

    def test_open_confirm_dialog_exhausts_retries_and_fails(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗重试次数": 1}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task._click_reference = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_confirm_dialog = lambda *_args, **_kwargs: False
        task._free_entry_retry_state = lambda *_args, **_kwargs: (
            FREE_ENTRY_RETRY_READY
        )

        self.assertFalse(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual(2, len(clicks))

    def test_free_entry_retry_state_ready_requires_pool_and_free_entry(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task._passes = lambda _result, _spec: False
        task._ocr_text = lambda _frame, name: (
            "装备抽抽乐 抽抽乐记录 所有免费抽抽乐 "
            "角色和装备在未来可能会通过其他方式重新贩售或发放 "
            "本抽抽乐的Pickup对象及日程可能会在后续有所变动"
        )

        self.assertEqual(
            FREE_ENTRY_RETRY_READY,
            FreeGachaTask._free_entry_retry_state(task, "装备抽抽乐"),
        )

    def test_free_entry_retry_state_flags_dialog_over_retry(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task._passes = lambda _result, _spec: False
        task._ocr_text = lambda _frame, name: (
            "确认抽抽乐 是否全部进行 装备抽抽乐 所有免费抽抽乐"
        )

        self.assertEqual(
            FREE_ENTRY_RETRY_DIALOG,
            FreeGachaTask._free_entry_retry_state(task, "装备抽抽乐"),
        )

    def test_free_entry_retry_state_reports_loading_and_page_change(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._ocr_text = lambda _frame, name: "抽抽乐记录 付费 免费"
        task._passes = lambda _result, _spec: loading_seen["value"]

        loading_seen = {"value": True}
        task._match = lambda _frame, _spec: MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
        self.assertEqual(
            FREE_ENTRY_RETRY_LOADING,
            FreeGachaTask._free_entry_retry_state(task, "装备抽抽乐"),
        )

        loading_seen["value"] = False
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        self.assertEqual(
            FREE_ENTRY_RETRY_CHANGED,
            FreeGachaTask._free_entry_retry_state(task, "装备抽抽乐"),
        )

    def test_run_free_section_fails_when_free_entry_ocr_is_empty(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        statuses = []
        task.info_set = lambda key, value: statuses.append((key, value))
        task.log_info = lambda *_args, **_kwargs: None
        task._wait_for_free_gacha = lambda *_args, **_kwargs: (False, "", False)
        task._open_confirm_dialog_with_retry = lambda *_args, **_kwargs: self.fail(
            "OCR 全程无文本时不得进入免费入口点击"
        )

        self.assertFalse(FreeGachaTask._run_free_section(task, "装备抽抽乐", True))
        self.assertIn(("状态", "白嫖抽抽乐装备抽抽乐免费入口判断失败。"), statuses)

    def test_run_free_section_fails_when_completion_recheck_is_unjudged(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        statuses = []
        task.info_set = lambda key, value: statuses.append((key, value))
        task.log_info = lambda *_args, **_kwargs: None
        task._wait_for_free_gacha = lambda *_args, **kwargs: (
            (False, "", False)
            if kwargs.get("timeout") == 1.5
            else (True, "所有免费抽抽乐", True)
        )
        task._open_confirm_dialog_with_retry = lambda *_args, **_kwargs: True
        task._wait_for_free_confirmation = lambda *_args: FreeDrawConfirmation(3, (.7, .6))
        task._confirm_dialog_submission = lambda *_args, **_kwargs: True
        task._handle_result_until_back = lambda *_args, **_kwargs: True

        self.assertFalse(FreeGachaTask._run_free_section(task, "装备抽抽乐", True))
        self.assertIn(("状态", "白嫖抽抽乐装备抽抽乐领取复核失败。"), statuses)

    def test_run_free_section_skips_when_ocr_confirms_no_free_entry(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        statuses = []
        task.info_set = lambda key, value: statuses.append((key, value))
        logs = []
        task.log_info = lambda message, **_kwargs: logs.append(message)
        task._wait_for_free_gacha = lambda *_args, **_kwargs: (False, "付费 抽1次", True)
        task._open_confirm_dialog_with_retry = lambda *_args, **_kwargs: self.fail(
            "确认无免费时不得点击免费入口"
        )

        self.assertTrue(FreeGachaTask._run_free_section(task, "装备抽抽乐", True))
        self.assertIn(("装备抽抽乐 免费抽", "无"), statuses)
        self.assertTrue(any("未检测到所有免费抽抽乐" in message for message in logs))

    def test_wait_for_free_gacha_distinguishes_empty_ocr_from_no_free(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"免费抽按钮等待秒数": 0.6}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((800, 1000, 3), dtype=np.uint8)
        task._match = lambda *_args: None
        task._passes = lambda *_args: False
        task.sleep = lambda seconds: now.__setitem__("value", now["value"] + seconds)
        now = {"value": 0.0}
        controls = [SimpleNamespace(name=name, x=x, y=y, width=w, height=h)
                    for name, x, y, w, h in [
                        ("装备抽抽乐 服装 装备 角色和装备在未来可能会通过其他方式重新贩售或发放",
                         30, 20, 200, 30),
                        ("抽1次", 760, 700, 60, 20), ("抽10次", 900, 700, 60, 20),
                        ("1", 775, 730, 30, 20)]]
        for boxes, expected in [([], (False, False)),
                                ([SimpleNamespace(name="正在加载")], (False, False)),
                                (controls, (False, True)),
                                (controls + [SimpleNamespace(name="免费", x=760, y=730,
                                                            width=30, height=20)], (False, False)),
                                (controls + [SimpleNamespace(name="所有免费抽抽乐", x=100, y=720,
                                                            width=200, height=30)], (True, True))]:
            with self.subTest(text=[b.name for b in boxes]):
                now["value"] = 0
                task._gacha_ocr_boxes = Mock(return_value=boxes)
                with patch.object(free_gacha_module, "monotonic", lambda: now["value"]):
                    available, _, judged = task._wait_for_free_gacha("装备抽抽乐")
                self.assertEqual(expected, (available, judged))
                if expected == (False, True):
                    self.assertEqual(2, task._gacha_ocr_boxes.call_count)

    def test_free_confirmation_requires_matching_pool_count_and_modal_controls(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = Mock()
        task._match = lambda *_args: None
        task._passes = lambda *_args: False
        for scale in (1, .72, 2):
            frame = np.zeros((round(800 * scale), round(1000 * scale), 3), dtype=np.uint8)
            for pool in ("服装", "装备"):
                task._gacha_ocr_boxes = Mock(return_value=confirmation_boxes(pool, scale=scale))
                observed = task._free_confirmation_in_frame(frame, pool + "抽抽乐")
                self.assertEqual(3, observed.count)
                self.assertAlmostEqual(.695, observed.point[0])
                self.assertAlmostEqual(.55625, observed.point[1])
        frame = np.zeros((800, 1000, 3), dtype=np.uint8)
        for boxes in (confirmation_boxes("服装"), confirmation_boxes(count=0),
                      confirmation_boxes(cost="免费2次"), confirmation_boxes(cost="消耗钻石90"),
                      confirmation_boxes()[:-4],
                      confirmation_boxes() + [SimpleNamespace(name="确认", x=50, y=50,
                                                               width=30, height=20)]):
            task._gacha_ocr_boxes = Mock(return_value=boxes)
            self.assertIsNone(task._free_confirmation_in_frame(frame, "装备抽抽乐"))

    def test_free_confirmation_requires_consecutive_new_observations(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗等待秒数": 1}
        task.info_set = Mock()
        now = [0.0]
        task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        task.capture_frame = Mock()
        three = FreeDrawConfirmation(3, (.7, .6))
        two = FreeDrawConfirmation(2, (.7, .6))
        task._free_confirmation_in_frame = Mock(side_effect=[three, None, two, three, three])
        with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
            self.assertEqual(three, task._wait_for_free_confirmation("装备抽抽乐", 3))
        self.assertEqual(5, task.capture_frame.call_count)

    def test_wait_for_equipment_pool_page_requires_equipment_title(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        logs = []
        task.log_info = lambda message, **_kwargs: logs.append(message)
        calls = []

        def fake_wait(keywords, timeout, minimum_matches, name, interval=0.5):
            calls.append((tuple(keywords), minimum_matches))
            return (minimum_matches == 1 and keywords == ["装备抽抽乐"], "服装抽抽乐")

        task._wait_for_ocr_keywords = fake_wait

        self.assertTrue(FreeGachaTask._wait_for_equipment_pool_page(task, "切换装备抽卡"))
        self.assertEqual((("装备抽抽乐",), 1), calls[0])

        task._wait_for_ocr_keywords = lambda *_args, **_kwargs: (False, "服装抽抽乐")
        self.assertFalse(
            FreeGachaTask._wait_for_equipment_pool_page(task, "切换装备抽卡")
        )
        self.assertTrue(any("可能仍停留在服装池" in message for message in logs))

    def test_wait_for_clothing_pool_page_requires_clothing_title(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        logs = []
        task.log_info = lambda message, **_kwargs: logs.append(message)
        calls = []

        def fake_wait(keywords, timeout, minimum_matches, name, interval=0.5):
            calls.append((tuple(keywords), minimum_matches))
            return (minimum_matches == 1 and keywords == ["服装抽抽乐"], "装备抽抽乐")

        task._wait_for_ocr_keywords = fake_wait

        self.assertTrue(FreeGachaTask._wait_for_clothing_pool_page(task, "确认服装池"))
        self.assertEqual((("服装抽抽乐",), 1), calls[0])

        task._wait_for_ocr_keywords = lambda *_args, **_kwargs: (False, "装备抽抽乐")
        self.assertFalse(FreeGachaTask._wait_for_clothing_pool_page(task, "确认服装池"))
        self.assertTrue(
            any("可能停留在装备池" in message for message in logs),
        )

    def test_run_fails_before_clothing_section_when_pool_not_confirmed(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"启用": True}
        statuses = []
        task.info_set = lambda key, value: statuses.append((key, value))
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        task._click_reference = lambda x, y, after_sleep=0: clicks.append((x, y))
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task._wait_loading_or_gacha_page = lambda *_args, **_kwargs: (
            "target",
            True,
            "抽抽乐",
        )
        task._wait_for_clothing_pool_page = lambda *_args, **_kwargs: False
        task._run_free_section = lambda *_args, **_kwargs: self.fail(
            "服装池未确认时不得进入免费抽流程"
        )

        self.assertFalse(FreeGachaTask.run(task))
        self.assertIn(("状态", "白嫖抽抽乐确认服装池失败。"), statuses)
        self.assertNotIn((175, 432), clicks)

    def test_confirm_submission_stops_when_dialog_closes(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认提交重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task.operate_click = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_free_confirmation = lambda *_args: FreeDrawConfirmation(3, (.7, .6))
        task._confirm_dialog_still_open = lambda _section_name: False

        self.assertTrue(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐", 3))
        self.assertEqual(1, len(clicks))

    def test_confirm_submission_retries_until_dialog_closes(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认提交重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task.operate_click = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_free_confirmation = lambda *_args: FreeDrawConfirmation(3, (.7, .6))
        checks = iter([True, True, False])

        def open_then_submitted(_section_name):
            return next(checks)

        task._confirm_dialog_still_open = open_then_submitted

        self.assertTrue(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐", 3))
        self.assertEqual(3, len(clicks))
        task._wait_for_free_confirmation = lambda *_args: None
        self.assertFalse(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐", 3))
        self.assertEqual(3, len(clicks))

    def test_confirm_submission_exhausts_retries_and_fails(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认提交重试次数": 1}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task.operate_click = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_free_confirmation = lambda *_args: FreeDrawConfirmation(3, (.7, .6))
        task._confirm_dialog_still_open = lambda *_args, **_kwargs: True

        self.assertFalse(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐", 3))
        self.assertEqual(2, len(clicks))

    def test_confirm_dialog_still_open_reads_loading_and_dialog_keywords(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        ocr_calls = []

        def fake_ocr(_frame, name):
            ocr_calls.append(name)
            return "确认抽抽乐 是否全部进行"

        task._ocr_text = fake_ocr
        task._passes = lambda _result, _spec: loading_seen["value"]

        loading_seen = {"value": True}
        task._match = lambda _frame, _spec: MatchResult(
            0.9, (0, 0), (1, 1), pixel_score=0.9
        )
        self.assertFalse(
            FreeGachaTask._confirm_dialog_still_open(task, "服装抽抽乐")
        )
        self.assertEqual([], ocr_calls)

        loading_seen["value"] = False
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        self.assertTrue(
            FreeGachaTask._confirm_dialog_still_open(task, "服装抽抽乐")
        )

        task._ocr_text = lambda _frame, name: "确认抽抽乐 所有免费抽抽乐"
        self.assertFalse(
            FreeGachaTask._confirm_dialog_still_open(task, "服装抽抽乐")
        )
        task._ocr_text = lambda _frame, name: "抽抽乐券 可免费抽1次的抽抽乐券 查看获取途径"
        self.assertFalse(task._confirm_dialog_still_open("服装抽抽乐"))

    def test_run_free_section_fails_when_confirm_submission_exhausted(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        statuses = []
        task.info_set = lambda key, value: statuses.append((key, value))
        task.log_info = lambda *_args, **_kwargs: None
        task._wait_for_free_gacha = lambda *_args, **_kwargs: (
            True,
            "所有免费抽抽乐",
            True,
        )
        task._open_confirm_dialog_with_retry = lambda *_args, **_kwargs: True
        task._wait_for_free_confirmation = lambda *_args: FreeDrawConfirmation(3, (.7, .6))
        task._confirm_dialog_submission = lambda *_args, **_kwargs: False
        task._handle_result_until_back = lambda *_args, **_kwargs: self.fail(
            "确认提交失败时不得进入结果处理"
        )

        self.assertFalse(FreeGachaTask._run_free_section(task, "服装抽抽乐", True))
        self.assertIn(("状态", "白嫖抽抽乐服装抽抽乐确认提交失败。"), statuses)


if __name__ == "__main__":
    unittest.main()
