import importlib
import unittest

import numpy as np

from src.tasks.FreeGachaTask import (
    BACK_PAGE_KEYWORDS,
    LOADING_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    FreeGachaTask,
    GachaMatchResult,
)

free_gacha_module = importlib.import_module("src.tasks.FreeGachaTask")


class FreeGachaTaskHelperTest(unittest.TestCase):
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

    def test_home_brightness_ratio_uses_home_button_region(self):
        task = object.__new__(FreeGachaTask)
        task.config = {"主页亮度比例阈值": 0.75}
        template = np.full((20, 20), 100, dtype=np.uint8)
        task._load_template = lambda _spec: template

        frame = np.zeros((REFERENCE_HEIGHT, REFERENCE_WIDTH, 3), dtype=np.uint8)
        center_x = 166
        center_y = 158
        frame[center_y - 10 : center_y + 10, center_x - 10 : center_x + 10] = 100

        self.assertAlmostEqual(1.0, task._home_brightness_ratio(frame), places=2)

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
                return GachaMatchResult(0.9, 0.9, (0, 0), (1, 1))
            return GachaMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._ocr_text = lambda *_args, **_kwargs: "服装" if loading_seen["value"] else ""

        self.assertEqual(
            ("target", True, "服装"),
            FreeGachaTask._wait_loading_or_gacha_page(task, "进入抽卡页"),
        )

    def test_loading_wait_prioritizes_home_brightness(self):
        task = object.__new__(FreeGachaTask)
        task.config = {
            "加载页面阈值": 0.72,
            "loading 出现等待秒数": 1.0,
            "loading 消失等待秒数": 1.0,
            "主页亮度比例阈值": 0.75,
        }
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        loading_seen = {"value": False}

        def fake_match(_frame, spec):
            if spec is LOADING_TEMPLATE:
                loading_seen["value"] = True
                return GachaMatchResult(0.9, 0.9, (0, 0), (1, 1))
            return GachaMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._home_brightness_ratio = lambda _frame: 1.0 if loading_seen["value"] else 0.0
        task._wait_home_brightness = lambda *_args, **_kwargs: self.fail(
            "home should be accepted before waiting for loading to disappear"
        )

        self.assertTrue(FreeGachaTask._wait_loading_or_home_brightness(task, "返回主页"))

    def test_result_skip_clicks_for_duration_then_checks_ocr_once(self):
        task = object.__new__(FreeGachaTask)
        task.config = {
            "跳过点击间隔秒数": 0.2,
            "结果跳过连续点击秒数": 3.0,
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
        self.assertEqual([15], captures)
        self.assertEqual(1, len(ocr_calls))
        self.assertAlmostEqual(3.0, sum(sleeps), places=6)
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
        self.assertEqual([2.0], sleeps)
        self.assertEqual([(1420, 326, 2.0), (105, 51, 0.0)], clicks)
        self.assertEqual(["服装抽抽乐 返回抽卡页"], waits)


if __name__ == "__main__":
    unittest.main()
