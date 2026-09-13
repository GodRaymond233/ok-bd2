import importlib
import unittest

import numpy as np

from src.tasks.FreeGachaTask import (
    BACK_PAGE_KEYWORDS,
    FREE_ENTRY_RETRY_CHANGED,
    FREE_ENTRY_RETRY_DIALOG,
    FREE_ENTRY_RETRY_LOADING,
    FREE_ENTRY_RETRY_READY,
    LOADING_TEMPLATE,
    FreeGachaTask,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH

free_gacha_module = importlib.import_module("src.tasks.FreeGachaTask")


class FreeGachaTaskHelperTest(unittest.TestCase):
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
        task._wait_for_equipment_pool_page = (
            lambda name: pool_waits.append(name) or True
        )

        self.assertTrue(FreeGachaTask.run(task))
        self.assertEqual(
            [("服装抽抽乐", True), ("装备抽抽乐", True)],
            sections,
        )
        self.assertEqual(["切换装备抽卡"], pool_waits)

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
        task.config = {"免费抽按钮等待秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.sleep = lambda seconds: now.__setitem__("value", now["value"] + seconds)
        responses = iter([(False, ""), (True, "所有免费抽抽乐")])

        def fake_ocr_in_frame(_frame, _keywords, _minimum, _name):
            return next(responses)

        task._ocr_keywords_in_frame = fake_ocr_in_frame
        now = {"value": 0.0}

        old_monotonic = free_gacha_module.monotonic
        try:
            free_gacha_module.monotonic = lambda: now["value"]
            self.assertEqual(
                (False, "", False),
                FreeGachaTask._wait_for_free_gacha(task, "装备抽抽乐"),
            )
            self.assertEqual(
                (True, "所有免费抽抽乐", True),
                FreeGachaTask._wait_for_free_gacha(task, "装备抽抽乐"),
            )
        finally:
            free_gacha_module.monotonic = old_monotonic

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


if __name__ == "__main__":
    unittest.main()
