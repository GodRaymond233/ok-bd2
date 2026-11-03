import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.tasks.FreeGachaTask import (
    BACK_PAGE_KEYWORDS,
    FREE_ENTRY_RETRY_BLOCKED,
    FREE_ENTRY_RETRY_DIALOG,
    FREE_ENTRY_RETRY_LOADING,
    FREE_ENTRY_RETRY_READY,
    FREE_ENTRY_RETRY_UNKNOWN,
    LOADING_TEMPLATE,
    FreeDrawConfirmation,
    FreeGachaTask,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH
from tests.helpers import StageHistoryTestCase

free_gacha_module = importlib.import_module("src.tasks.FreeGachaTask")


def pool_boxes(pool="装备", free=False, scale=1, merged_header=False):
    rows = [
        (pool + "抽抽乐", 120, 20, 110, 30),
        ("抽抽乐记录", 240, 22, 100, 25),
        ("免费61,401", 760, 22, 120, 25),
        ("抽1次", 760, 700, 60, 20),
        ("抽10次.", 900, 700, 70, 20),
    ]
    if merged_header:
        rows[:2] = [(pool + "抽抽乐 抽抽乐记录", 120, 20, 220, 30)]
    if free:
        rows.extend([("所有免费抽抽乐", 100, 720, 200, 30),
                     ("免费1次", 760, 730, 70, 20)])
    return [SimpleNamespace(name=name, x=x * scale, y=y * scale,
                            width=w * scale, height=h * scale) for name, x, y, w, h in rows]


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
    def test_current_free_batch_can_be_claimed_on_repeat_runs(self):
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
                    task._confirm_dialog_submission = Mock(return_value=False)
                    task._handle_result_until_back = Mock(return_value=True)
                    checkpoint = f"免费抽:{pool}"
                    run_history.save_stage(task, checkpoint, "pending", scope="old-cycle")
                    self.history_mock.return_value = run_history.RunHistoryStore(self.history.path)
                    with patch.object(run_history, "day_start_ts", return_value=days * 86400):
                        self.assertFalse(task._run_free_section(pool, True))
                        task._confirm_dialog_submission.assert_called_once_with(pool)
                        self.assertEqual(
                            "old-cycle", run_history.read_stage(task, checkpoint)["scope"]
                        )
                        task._wait_for_free_gacha.side_effect = [
                            (True, "所有免费抽抽乐", True), (False, "当前卡池无免费入口", True)]
                        task._confirm_dialog_submission.return_value = True
                        self.assertTrue(task._run_free_section(pool, True))

    def test_free_submission_requires_confirmation_and_result_before_completion(self):
        for failure in ("submission", "result"):
            with self.subTest(failure=failure):
                self.reset_stage_history()
                task = object.__new__(FreeGachaTask)
                task.config = {}
                task.info_set = Mock()
                task.log_info = Mock()
                task._wait_for_free_gacha = Mock(return_value=(True, "所有免费抽抽乐", True))
                task._open_confirm_dialog_with_retry = Mock(return_value=True)
                task._confirm_dialog_submission = Mock(return_value=failure != "submission")
                task._handle_result_until_back = Mock(return_value=failure != "result")
                self.assertFalse(task._run_free_section("装备抽抽乐", True))
                task._open_confirm_dialog_with_retry.assert_called_once()
                task._confirm_dialog_submission.assert_called_once_with("装备抽抽乐")
                task.info_set.assert_any_call(
                    "装备抽抽乐 结果", "未完成：装备抽抽乐" +
                    ("确认提交" if failure == "submission" else "结果处理")
                )
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
        from src.tasks.run_history import is_successful_run

        task = object.__new__(FreeGachaTask)
        task.config = {"启用": True}
        info = {}
        task.info_set = info.__setitem__
        task.log_info = lambda *_args, **_kwargs: None
        task._click_reference = Mock()
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task._wait_loading_or_gacha_page = lambda *_args, **_kwargs: (
            "target",
            True,
            "抽抽乐",
        )
        task._sleep_after_recognition = lambda: None
        task._wait_loading_or_home_confirmation = lambda *_args, **_kwargs: True
        task._wait_for_free_gacha = Mock(side_effect=[
            (False, "服装池无免费", True),
            (True, "所有免费抽抽乐", True),
            (False, "装备池无免费", True),
        ])
        task._open_confirm_dialog_with_retry = Mock(return_value=True)
        task._confirm_dialog_submission = Mock(return_value=True)
        task._handle_result_until_back = Mock(return_value=True)
        pool_waits = []
        task._wait_for_clothing_pool_page = (
            lambda name: pool_waits.append(("clothing", name)) or True
        )
        task._wait_for_equipment_pool_page = (
            lambda name: pool_waits.append(("equipment", name)) or True
        )

        self.assertTrue(FreeGachaTask.run(task))
        task._open_confirm_dialog_with_retry.assert_called_once_with("装备抽抽乐")
        task._confirm_dialog_submission.assert_called_once_with("装备抽抽乐")
        task._handle_result_until_back.assert_called_once_with("装备抽抽乐")
        self.assertEqual("本轮无免费入口，已跳过", info["服装抽抽乐 结果"])
        self.assertEqual("本轮免费抽已领取", info["装备抽抽乐 结果"])
        self.assertTrue(is_successful_run(info))
        self.assertEqual(
            [("clothing", "确认服装池"), ("equipment", "切换装备抽卡")],
            pool_waits,
        )

    def test_unresolved_clothing_allows_equipment_only_on_recovered_pool_page(self):
        from src.tasks.run_history import is_successful_run

        for recovered in (True, False):
            with self.subTest(recovered=recovered):
                task = object.__new__(FreeGachaTask)
                task.config = {}
                info = {}
                task.info_set = info.__setitem__
                task.log_info = Mock()
                task.log_completion = Mock()
                task._click_reference = Mock()
                task._sleep_after_recognition = Mock()
                task._wait_for_home_confirmation = Mock(return_value=True)
                task._wait_loading_or_gacha_page = Mock(return_value=("target", True, ""))
                task._wait_for_clothing_pool_page = Mock(side_effect=[True, recovered])
                task._wait_for_equipment_pool_page = Mock(return_value=True)
                task._wait_loading_or_home_confirmation = Mock(return_value=True)
                task._wait_for_free_gacha = Mock(side_effect=[
                    (False, "免费1次", False),
                    (True, "所有免费抽抽乐", True),
                    (False, "装备池无免费", True),
                ])
                task._open_confirm_dialog_with_retry = Mock(return_value=True)
                task._confirm_dialog_submission = Mock(return_value=True)
                task._handle_result_until_back = Mock(return_value=True)
                self.assertFalse(task.run())
                self.assertFalse(is_successful_run(info))
                self.assertIn("未完成", info["服装抽抽乐 结果"])
                task.log_completion.assert_not_called()
                if recovered:
                    task._confirm_dialog_submission.assert_called_once_with("装备抽抽乐")
                    self.assertEqual("本轮免费抽已领取", info["装备抽抽乐 结果"])
                    task._wait_loading_or_home_confirmation.assert_called_once()
                else:
                    task._click_reference.assert_called_once_with(162, 986, after_sleep=0.5)
                    task._wait_for_equipment_pool_page.assert_not_called()
                    task._confirm_dialog_submission.assert_not_called()
                    self.assertEqual("待检查", info["装备抽抽乐 结果"])

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

    def test_result_skip_clicks_for_duration_then_polls_ocr_until_ticket_page(self):
        task = object.__new__(FreeGachaTask)
        task.config = {
            "跳过点击间隔秒数": 0.2,
            "结果跳过连续点击秒数": 3.0,
            "结果页 OCR 等待秒数": 1.0,
            "结果页 OCR 间隔秒数": 0.1,
            "结果页关键词最低命中数": 1,
        }
        clicks = []
        sleeps = []
        ocr_calls = []
        captures = []
        now = {"value": 0.0}

        def fake_click(x, y, after_sleep=0):
            clicks.append((x, y, after_sleep))

        def fake_capture():
            captures.append(len(clicks))
            return np.zeros((10, 10, 3), dtype=np.uint8)

        def fake_ocr(_frame, keywords, minimum_matches, name):
            ocr_calls.append((keywords, minimum_matches, name))
            if len(ocr_calls) == 1:
                return False, "试玩抽抽乐1/1完成"
            return True, "抽抽乐券 可免费抽1次的抽抽乐券 查看获取途径"

        task._click_reference = fake_click
        task.capture_frame = fake_capture
        task._ocr_keywords_in_frame = fake_ocr
        task.sleep = lambda seconds: (
            sleeps.append(seconds),
            now.__setitem__("value", now["value"] + seconds),
        )

        old_monotonic = free_gacha_module.monotonic
        try:
            free_gacha_module.monotonic = lambda: now["value"]
            found, text = FreeGachaTask._click_skip_until_back_page(task, "服装抽抽乐")
        finally:
            free_gacha_module.monotonic = old_monotonic

        self.assertTrue(found)
        self.assertEqual("抽抽乐券 可免费抽1次的抽抽乐券 查看获取途径", text)
        self.assertEqual([(1770, 60, 0.0)] * 15, clicks)
        self.assertEqual([15, 15], captures)
        self.assertEqual(2, len(ocr_calls))
        self.assertAlmostEqual(3.1, sum(sleeps), places=6)
        self.assertTrue(all(seconds <= 0.2 for seconds in sleeps))
        self.assertEqual(BACK_PAGE_KEYWORDS, ocr_calls[0][0])
        self.assertEqual(1, ocr_calls[0][1])

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
        task.operate_click = lambda x, y, **_kwargs: clicks.append((x, y))
        dialog_waits = {"value": 0}

        def fake_wait_confirm(_section_name, **_kwargs):
            dialog_waits["value"] += 1
            return dialog_waits["value"] >= 3

        task._wait_for_confirm_dialog = fake_wait_confirm
        states = iter(
            [(FREE_ENTRY_RETRY_READY, (.2, .9)),
             (FREE_ENTRY_RETRY_READY, (.21, .91)),
             (FREE_ENTRY_RETRY_READY, (.22, .92))]
        )
        task._free_entry_retry_state = lambda _section_name: next(states)

        self.assertTrue(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual([(.2, .9), (.21, .91), (.22, .92)], clicks)

    def test_open_confirm_dialog_stops_retry_when_entry_gone(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task.operate_click = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_confirm_dialog = lambda *_args, **_kwargs: False
        now = [0.0]
        task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        task._free_entry_retry_state = Mock(side_effect=lambda _: (
            (FREE_ENTRY_RETRY_READY, (.2, .9)) if not clicks
            else (FREE_ENTRY_RETRY_UNKNOWN, None)))

        with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
            self.assertFalse(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual(1, len(clicks))
        self.assertEqual(3.0, now[0])

    def test_open_confirm_dialog_accepts_dialog_found_before_retry(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task.operate_click = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_confirm_dialog = lambda *_args, **_kwargs: False
        task._free_entry_retry_state = Mock(side_effect=[
            (FREE_ENTRY_RETRY_READY, (.2, .9)), (FREE_ENTRY_RETRY_DIALOG, None)])

        self.assertTrue(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual(1, len(clicks))

    def test_open_confirm_dialog_exhausts_retries_and_fails(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认弹窗重试次数": 1}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task.operate_click = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_confirm_dialog = lambda *_args, **_kwargs: False
        task._free_entry_retry_state = lambda *_args, **_kwargs: (
            FREE_ENTRY_RETRY_READY, (.2, .9)
        )

        self.assertFalse(FreeGachaTask._open_confirm_dialog_with_retry(task, "装备抽抽乐"))
        self.assertEqual(2, len(clicks))

    def test_free_entry_retry_state_ready_requires_pool_and_free_entry(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task._passes = lambda _result, _spec: False
        task._sleep_after_recognition = Mock()
        task.log_info = Mock()
        task.operate_click = Mock()
        task._wait_for_confirm_dialog = Mock(return_value=True)
        for pool in ("服装", "装备"):
            for merged in (False, True):
                for scale in (1, .72, 2):
                    with self.subTest(pool=pool, merged=merged, scale=scale):
                        task.capture_frame = lambda: np.zeros(
                            (round(800 * scale), round(1000 * scale), 3), dtype=np.uint8)
                        task._gacha_ocr_boxes = Mock(return_value=pool_boxes(
                            pool, free=True, scale=scale, merged_header=merged))
                        task.operate_click.reset_mock()
                        self.assertTrue(task._open_confirm_dialog_with_retry(pool + "抽抽乐"))
                        task.operate_click.assert_called_once()
                        x, y = task.operate_click.call_args.args
                        self.assertAlmostEqual(.2, x)
                        self.assertAlmostEqual(.91875, y)
                        self.assertEqual({"after_sleep": 0.5}, task.operate_click.call_args.kwargs)
        task._gacha_ocr_boxes.return_value = pool_boxes(
            pool="服装", free=True, merged_header=True)
        task.operate_click.reset_mock()
        self.assertFalse(task._open_confirm_dialog_with_retry("装备抽抽乐"))
        task.operate_click.assert_not_called()

    def test_free_entry_retry_state_flags_dialog_over_retry(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task._passes = lambda _result, _spec: False
        task._gacha_ocr_boxes = Mock(return_value=confirmation_boxes() + pool_boxes(free=True))

        self.assertEqual(
            (FREE_ENTRY_RETRY_DIALOG, None),
            FreeGachaTask._free_entry_retry_state(task, "装备抽抽乐"),
        )

    def test_free_entry_retry_state_reports_loading_and_page_change(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._gacha_ocr_boxes = Mock(return_value=[])
        task._passes = lambda _result, _spec: loading_seen["value"]

        loading_seen = {"value": True}
        task._match = lambda _frame, _spec: MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
        self.assertEqual(
            (FREE_ENTRY_RETRY_LOADING, None),
            FreeGachaTask._free_entry_retry_state(task, "装备抽抽乐"),
        )

        loading_seen["value"] = False
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        self.assertEqual(
            (FREE_ENTRY_RETRY_UNKNOWN, None),
            FreeGachaTask._free_entry_retry_state(task, "装备抽抽乐"),
        )

    def test_entry_recovers_observations_with_fixed_budgets_and_current_click_point(self):
        ready = (FREE_ENTRY_RETRY_READY, (.23, .89))
        for states, expected, elapsed in [
            ([(FREE_ENTRY_RETRY_UNKNOWN, None), ready], True, .25),
            ([(FREE_ENTRY_RETRY_LOADING, None), (FREE_ENTRY_RETRY_UNKNOWN, None), ready], True, .5),
            ([(FREE_ENTRY_RETRY_UNKNOWN, None)], False, 1.0),
            ([(free_gacha_module.FREE_ENTRY_RETRY_NO_FREE, None)], False, 1.0),
            ([(FREE_ENTRY_RETRY_LOADING, None)], False, 2.0),
            ([(FREE_ENTRY_RETRY_BLOCKED, None)], False, 0.0),
        ]:
            with self.subTest(states=states):
                task = object.__new__(FreeGachaTask)
                task.config = {"免费抽按钮等待秒数": 1.0, "loading 消失等待秒数": 2.0,
                               "确认弹窗重试次数": 0}
                task._sleep_after_recognition = Mock()
                task.log_info = Mock()
                task.operate_click = Mock()
                task._wait_for_confirm_dialog = Mock(return_value=True)
                now = [0.0]
                pending = list(states)
                task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
                task._free_entry_retry_state = Mock(side_effect=lambda _: (
                    pending.pop(0) if len(pending) > 1 else pending[0]))
                with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
                    self.assertEqual(expected, task._open_confirm_dialog_with_retry("服装抽抽乐"))
                self.assertEqual(elapsed, now[0])
                if expected:
                    task.operate_click.assert_called_once_with(.23, .89, after_sleep=.5)
                else:
                    task.operate_click.assert_not_called()

    def test_entry_budget_expiring_during_ocr_prevents_click(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"免费抽按钮等待秒数": 1.0, "loading 消失等待秒数": 2.0}
        task._sleep_after_recognition = Mock()
        task.log_info = Mock()
        task.operate_click = Mock()
        task._wait_for_confirm_dialog = Mock()
        for duration in (1.1, 2.1):
            now = [0.0]
            def slow_ocr(_):
                now[0] += duration
                return FREE_ENTRY_RETRY_READY, (.2, .9)
            task._free_entry_retry_state = slow_ocr
            with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
                self.assertFalse(task._open_confirm_dialog_with_retry("服装抽抽乐"))
        task.operate_click.assert_not_called()
        task._wait_for_confirm_dialog.assert_not_called()

    def test_pool_scale_recheck_is_same_frame_and_cannot_erase_free_or_modal_evidence(self):
        for scale in (1, .72, 2):
            task = object.__new__(FreeGachaTask)
            task.config = {}
            task.info_set = Mock()
            task._match = Mock(return_value=None)
            task._passes = Mock(return_value=False)
            frame = np.zeros((round(800 * scale), round(1000 * scale), 3), dtype=np.uint8)
            task.capture_frame = Mock(return_value=frame)
            ready = pool_boxes(free=True, scale=scale)
            missing_record = ready[:1] + ready[2:]
            no_free = pool_boxes(scale=scale)
            for first, second, expected in [
                (missing_record, ready, FREE_ENTRY_RETRY_READY),
                (missing_record, ready[1:], FREE_ENTRY_RETRY_UNKNOWN),
                (missing_record, no_free, FREE_ENTRY_RETRY_UNKNOWN),
                (no_free, ready, FREE_ENTRY_RETRY_READY),
                (no_free, no_free, free_gacha_module.FREE_ENTRY_RETRY_NO_FREE),
                (no_free[:3], no_free[3:], FREE_ENTRY_RETRY_UNKNOWN),
                (no_free, pool_boxes("服装", scale=scale), FREE_ENTRY_RETRY_BLOCKED),
            ]:
                with self.subTest(scale=scale, expected=expected, first=[b.name for b in first]):
                    task.capture_frame.reset_mock()
                    task._gacha_ocr_boxes = Mock(side_effect=[first, second])
                    observed = task._observe_free_pool("装备抽抽乐", "test")
                    self.assertEqual(expected, observed.state)
                    task.capture_frame.assert_called_once()
                    self.assertTrue(all(c.args[0] is frame
                                        for c in task._gacha_ocr_boxes.call_args_list))
                    if observed.point:
                        self.assertAlmostEqual(.2, observed.point[0])
                        self.assertAlmostEqual(.91875, observed.point[1])
            for boxes in (ready + confirmation_boxes(cost="消耗钻石90", scale=scale),
                          ready + [SimpleNamespace(name="查看获取途径")],
                          pool_boxes("服装", free=True, scale=scale)):
                task._gacha_ocr_boxes = Mock(side_effect=[boxes, ready])
                self.assertNotEqual(FREE_ENTRY_RETRY_READY,
                                    task._observe_free_pool("装备抽抽乐", "test").state)
                task._gacha_ocr_boxes.assert_called_once()

    def test_exhaustion_finishes_after_settling_before_later_ocr_noise(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"免费抽按钮等待秒数": 3.0}
        task.info_set = Mock()
        no_free = free_gacha_module.FreePoolObservation(
            free_gacha_module.FREE_ENTRY_RETRY_NO_FREE, "complete", "pool")
        now = [0.0]
        task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        task._observe_free_pool = Mock(return_value=no_free)
        with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
            self.assertEqual((False, "pool", True), task._wait_for_free_gacha("装备抽抽乐"))
        self.assertEqual(3.5, now[0])
        self.assertEqual(8, task._observe_free_pool.call_count)

    def test_failed_pool_keeps_exact_frame_and_box_reasons_before_recovery(self):
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = Mock()
        task._match = Mock(return_value=None)
        task._passes = Mock(return_value=False)
        frame = np.zeros((800, 1000, 3), dtype=np.uint8)
        task.capture_frame = Mock(return_value=frame)
        boxes = pool_boxes(free=True)
        boxes[1].y = 400
        task._gacha_ocr_boxes = Mock(return_value=boxes)
        observation = task._observe_free_pool("装备抽抽乐", "test")
        self.assertEqual(FREE_ENTRY_RETRY_UNKNOWN, observation.state)
        with patch.object(free_gacha_module, "capture_failure_frame") as save:
            self.assertFalse(task._fail_run("装备抽抽乐领取复核", "装备抽抽乐"))
        self.assertIs(frame, save.call_args.args[2])
        self.assertTrue(save.call_args.kwargs["lossless"])
        evidence = save.call_args.kwargs["recognition"]
        self.assertIn("布局", evidence["720判断"])
        self.assertIn("抽抽乐记录@(240,400,100,25)", evidence["1080"])
        task.capture_frame.assert_called_once()

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
        task._wait_for_free_gacha = Mock(side_effect=[
            (True, "所有免费抽抽乐", True), (False, "", False)])
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
        task.config = {"免费抽按钮等待秒数": 1.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((800, 1000, 3), dtype=np.uint8)
        task._match = lambda *_args: None
        task._passes = lambda *_args: False
        task.sleep = lambda seconds: now.__setitem__("value", now["value"] + seconds)
        now = {"value": 0.0}
        controls = pool_boxes()
        for boxes, expected in [([], (False, False)),
                                ([SimpleNamespace(name="正在加载")], (False, False)),
                                (controls, (False, True)),
                                (pool_boxes(merged_header=True), (False, True)),
                                (pool_boxes("服装"), (False, False)),
                                (controls[:-1], (False, True)),
                                (controls[:3], (False, False)),
                                (controls + [controls[-2]], (False, False)),
                                (controls + confirmation_boxes(), (False, False)),
                                ([SimpleNamespace(name="查看获取途径")], (False, False)),
                                (controls + [SimpleNamespace(name="免费", x=760, y=730,
                                                            width=30, height=20)], (False, False)),
                                (pool_boxes(free=True), (True, True))]:
            with self.subTest(text=[b.name for b in boxes]):
                now["value"] = 0
                task._gacha_ocr_boxes = Mock(return_value=boxes)
                with patch.object(free_gacha_module, "monotonic", lambda: now["value"]):
                    available, _, judged = task._wait_for_free_gacha("装备抽抽乐")
                self.assertEqual(expected, (available, judged))
                if expected == (False, True):
                    self.assertEqual(8, task._gacha_ocr_boxes.call_count)
                    self.assertEqual(1.5, now["value"])

        for scale in (.72, 2):
            task.capture_frame = lambda: np.zeros(
                (round(800 * scale), round(1000 * scale), 3), dtype=np.uint8)
            task._gacha_ocr_boxes = Mock(return_value=pool_boxes(scale=scale))
            now["value"] = 0
            with patch.object(free_gacha_module, "monotonic", lambda: now["value"]):
                self.assertEqual((False, True), task._wait_for_free_gacha("装备抽抽乐")[::2])

    def test_free_state_recheck_observes_delayed_entry_and_resets_on_unknown(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"免费抽按钮等待秒数": 1.0}
        task.info_set = Mock()
        task.capture_frame = lambda: np.zeros((800, 1000, 3), dtype=np.uint8)
        task._match = Mock()
        now = [0.0]
        task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        ready = pool_boxes()
        hint = ready + [SimpleNamespace(name="免费1次", x=760, y=730, width=80, height=20)]
        for frames, loading, expected in [
            ([ready, ready, ready, pool_boxes(free=True)], [False] * 4, (True, True)),
            ([ready, ready, [], ready], [False] * 4, (False, False)),
            ([ready, ready, ready[:-1], ready], [False] * 4, (False, True)),
            ([ready, ready, hint, ready], [False] * 4, (False, False)),
            ([ready] * 4, [False, False, True, False], (False, False)),
        ]:
            with self.subTest(expected=expected, loading=loading):
                now[0] = 0
                observations = [
                    free_gacha_module.FreePoolObservation(FREE_ENTRY_RETRY_LOADING, "loading", "")
                    if is_loading else task._pool_observation(
                        np.zeros((800, 1000, 3), dtype=np.uint8), boxes, "装备抽抽乐")
                    for boxes, is_loading in zip(frames, loading)]
                task._observe_free_pool = Mock(side_effect=observations)
                with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
                    self.assertEqual(expected, task._wait_for_free_gacha("装备抽抽乐")[::2])

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

    def test_pool_wait_requires_loaded_matching_page_and_excludes_overlays(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"抽卡页面等待秒数": .5}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = Mock()
        task.capture_frame = Mock()
        task._match = Mock()
        now = [0.0]
        task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        for pool, wait in (("服装", task._wait_for_clothing_pool_page),
                           ("装备", task._wait_for_equipment_pool_page)):
            other = "装备" if pool == "服装" else "服装"
            wrong_layout = pool_boxes(pool)
            wrong_layout[1].y = 400
            merged = pool_boxes(pool, merged_header=True)
            low_header = pool_boxes(pool, merged_header=True)
            low_header[0].y = 700
            reversed_header = pool_boxes(pool, merged_header=True)
            reversed_header[0].name = "抽抽乐记录 " + pool + "抽抽乐"
            for boxes, loading, expected in [
                (pool_boxes(pool), False, True),
                (merged, False, True),
                (pool_boxes(other, merged_header=True), False, False),
                (merged + [pool_boxes(other, merged_header=True)[0]], False, False),
                (merged + [pool_boxes(pool)[0]], False, False),
                (merged + [merged[0]], False, False),
                (merged + confirmation_boxes(pool), False, False),
                (merged, True, False),
                (low_header, False, False),
                (reversed_header, False, False),
                (pool_boxes(other), False, False),
                (pool_boxes(pool)[:1], False, False),
                (pool_boxes(pool) + confirmation_boxes(pool), False, False),
                (pool_boxes(pool), True, False),
                (wrong_layout, False, False),
            ]:
                with self.subTest(pool=pool, loading=loading, expected=expected):
                    now[0] = 0
                    task._passes = Mock(return_value=loading)
                    task._gacha_ocr_boxes = Mock(return_value=boxes)
                    with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
                        self.assertEqual(expected, wait("确认卡池"))

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

        self.assertTrue(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐"))
        self.assertEqual(1, len(clicks))

    def test_confirm_submission_retries_until_dialog_closes(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"确认提交重试次数": 2}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        clicks = []
        task.operate_click = lambda *_args, **_kwargs: clicks.append(1)
        task._wait_for_free_confirmation = Mock(return_value=FreeDrawConfirmation(3, (.7, .6)))
        checks = iter([True, True, False])

        def open_then_submitted(_section_name):
            return next(checks)

        task._confirm_dialog_still_open = open_then_submitted

        self.assertTrue(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐"))
        self.assertEqual(3, len(clicks))
        self.assertEqual([call("服装抽抽乐", None), call("服装抽抽乐", 3),
                          call("服装抽抽乐", 3)], task._wait_for_free_confirmation.call_args_list)
        task._wait_for_free_confirmation = lambda *_args: None
        self.assertFalse(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐"))
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

        self.assertFalse(FreeGachaTask._confirm_dialog_submission(task, "服装抽抽乐"))
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
