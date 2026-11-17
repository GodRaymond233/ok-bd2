"""回归：「新闻/公告」整页卡死时点左上角返回退出。

背景：2026-11-16 13:46 实机停在游戏内「新闻／公告」整页（主页公告入口点开后
的整页列表），自动登录每轮都走 `_maybe_return_home`：
- 房子按钮识别不到（页面不是主页）；
- 左上角「返回条」模板匹配不到（这页只有一个细箭头 + 灰底胶囊）；
- 固定清理点 (8.8%, 56.9%) 落在左侧分类栏，只会切换分类；
3 次「返回主页」用尽后永久停在提示上，日志刷 `多次自动返回主页失败`。
这里锁定：OCR 认出新闻/公告页 → 点左上角返回箭头 → 不消耗返回主页次数。
"""

import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.trigger.AutoLoginTask import AutoLoginTask
from src.utils.home_confirmation import (
    NEWS_BACK_REFERENCE_POINT,
    NEWS_BACK_REFERENCE_SIZE,
    NEWS_BACK_RELATIVE_POINT,
    news_back_point,
    news_page_detected,
)

HEIGHT, WIDTH = 1080, 1920
# 实机 2560x1440 抓帧的 OCR 结果：标题框 (461,58,420,80)，箭头中心 (342,100)。
REAL_TITLE_BOX = (461, 58, 420, 80)
REAL_NEWS_TEXT = (
    "新闻／公告 全部 公告 2026年11月13日 背叛之夜斐杰血气叠加相关追加公告 "
    "公告事项 更新 活动 开发者笔记 查看详情"
)
HOME_TEXT = (
    "Guest_31071717 61,700 2,320 我的小屋 好友 公会 经营管理格鲁TALK "
    "亲密度 街机游戏 心契之约 抽抽乐"
)


class _Box:
    def __init__(self, name, x, y, width, height):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height


def _title_box():
    return _Box("新闻／公告", *REAL_TITLE_BOX)


def _base_task():
    task = object.__new__(BaseBD2Task)
    task.config = {}
    task.info = {}
    task.clicks = []
    task.back_calls = []
    task.info_set = lambda key, value=None: task.info.__setitem__(key, value)
    task.log_info = lambda message, **kwargs: None
    task.log_warning = lambda message, **kwargs: None
    task.operate_click = lambda x, y, **kwargs: task.clicks.append((x, y))
    task.back = lambda **kwargs: task.back_calls.append(kwargs)
    return task


def _login_task(ocr_boxes):
    task = object.__new__(AutoLoginTask)
    task.config = {
        "公告清理点击 X 百分比": 8.8020833333,
        "公告清理点击 Y 百分比": 56.9444444444,
        "日常 OCR 阈值": 0.2,
    }
    task.info = {}
    task.clicks = []
    task.stages = []
    task.actions = []
    task.info_set = lambda key, value=None: task.info.__setitem__(key, value)
    task.log_info = lambda message, **kwargs: None
    task.log_warning = lambda message, **kwargs: None
    task._set_stage = lambda stage: task.stages.append(stage)
    task._set_action = lambda action: task.actions.append(action)
    task._sleep_after_recognition = lambda: None
    task.operate_click = lambda x, y, **kwargs: task.clicks.append((x, y))
    task.ocr = lambda **kwargs: list(ocr_boxes)
    task._last_clear_click_at = 0.0
    task._home_bright_since = 1.0
    task._state = "clearing"
    task._esc_home_attempts = 0
    return task


def _blank_frame(value: int = 250):
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def _news_frame(value: int = 250):
    """实机抓帧尺寸（2560x1440），与 REAL_TITLE_BOX 的像素坐标一致。"""
    height, width = NEWS_BACK_REFERENCE_SIZE[1], NEWS_BACK_REFERENCE_SIZE[0]
    return np.full((height, width, 3), value, dtype=np.uint8)


class NewsPageDetectionTest(unittest.TestCase):
    def test_detects_real_ocr_text(self):
        self.assertTrue(news_page_detected(REAL_NEWS_TEXT))

    def test_does_not_fire_on_home_or_partial_text(self):
        self.assertFalse(news_page_detected(HOME_TEXT))
        self.assertFalse(news_page_detected("公告事项 更新 活动"))
        self.assertFalse(news_page_detected(""))
        self.assertFalse(news_page_detected(None))

    def test_back_point_prefers_title_box(self):
        point = news_back_point([_title_box()], 2560, 1440)
        px = (round(point[0] * 2560), round(point[1] * 1440))
        # 实测箭头中心 (342,100)，由标题框反推应在几像素内。
        self.assertAlmostEqual(NEWS_BACK_REFERENCE_POINT[0], px[0], delta=4)
        self.assertAlmostEqual(NEWS_BACK_REFERENCE_POINT[1], px[1], delta=4)

    def test_back_point_falls_back_to_calibrated_constant(self):
        self.assertEqual(
            NEWS_BACK_RELATIVE_POINT, news_back_point(None, WIDTH, HEIGHT),
        )
        self.assertEqual(
            NEWS_BACK_RELATIVE_POINT, news_back_point([], WIDTH, HEIGHT),
        )
        # 标题不在顶部（例如正文里出现「公告」）时不参与反推。
        body = _Box("公告", 1152, 800, 136, 66)
        self.assertEqual(
            NEWS_BACK_RELATIVE_POINT, news_back_point([body], WIDTH, HEIGHT),
        )

    def test_calibrated_point_matches_real_frame(self):
        width, height = NEWS_BACK_REFERENCE_SIZE
        self.assertAlmostEqual(
            NEWS_BACK_REFERENCE_POINT[0] / width, NEWS_BACK_RELATIVE_POINT[0],
            places=6,
        )
        self.assertAlmostEqual(
            NEWS_BACK_REFERENCE_POINT[1] / height, NEWS_BACK_RELATIVE_POINT[1],
            places=6,
        )


class NewsPageClickTest(unittest.TestCase):
    def test_clicks_back_arrow_once(self):
        task = _base_task()
        self.assertTrue(
            task.dismiss_fullscreen_news_page(
                _news_frame(), boxes=[_title_box()], ocr_text=REAL_NEWS_TEXT,
            )
        )
        self.assertEqual(1, len(task.clicks))
        self.assertAlmostEqual(
            NEWS_BACK_RELATIVE_POINT[0], task.clicks[0][0], delta=0.004,
        )
        self.assertAlmostEqual(
            NEWS_BACK_RELATIVE_POINT[1], task.clicks[0][1], delta=0.004,
        )
        self.assertIn("新闻/公告页", task.info["整页退出"])

    def test_ignores_other_pages_without_clicking(self):
        task = _base_task()
        self.assertFalse(
            task.dismiss_fullscreen_news_page(
                _blank_frame(), ocr_text=HOME_TEXT,
            )
        )
        self.assertEqual([], task.clicks)
        self.assertIn("未检测到新闻/公告页", task.info["整页退出"])

    def test_throttles_repeated_clicks(self):
        task = _base_task()
        ticks = iter(range(10, 200, 5))
        with patch(
            "src.tasks.BaseBD2Task.monotonic", side_effect=lambda: next(ticks),
        ):
            results = [
                task.dismiss_fullscreen_news_page(
                    _blank_frame(), ocr_text=REAL_NEWS_TEXT,
                )
                for _ in range(3)
            ]
        # 5 秒步进 > 1.5 秒节流，三次都应真实点击。
        self.assertEqual([True, True, True], results)
        self.assertEqual(3, len(task.clicks))

    def test_switches_to_back_key_after_three_failed_clicks(self):
        task = _base_task()
        ticks = iter(range(10, 200, 5))
        with patch(
            "src.tasks.BaseBD2Task.monotonic", side_effect=lambda: next(ticks),
        ):
            for _ in range(4):
                task.dismiss_fullscreen_news_page(
                    _blank_frame(), ocr_text=REAL_NEWS_TEXT,
                )
        self.assertEqual(3, len(task.clicks))
        self.assertEqual(1, len(task.back_calls))

    def test_stops_after_repeated_popup_news_cycles(self):
        """复现 14:00 的死循环：弹窗 → 点开公告 → 退出 → 又回到弹窗。"""
        task = _base_task()
        ticks = iter(range(10, 900, 5))
        results = []
        messages = []
        with patch(
            "src.tasks.BaseBD2Task.monotonic", side_effect=lambda: next(ticks),
        ):
            for _ in range(4):
                results.append(
                    task.dismiss_fullscreen_news_page(
                        _blank_frame(), ocr_text=REAL_NEWS_TEXT,
                    )
                )
                messages.append(task.info.get("整页退出", ""))
                # 退出后回到遮挡主页的弹窗：不是新闻页，attempts 归零，
                # 下一次再进公告详情页就是「新的一轮」。
                task.dismiss_fullscreen_news_page(
                    _blank_frame(), ocr_text=HOME_TEXT,
                )
        self.assertEqual([True, True, True, False], results)
        self.assertEqual(3, len(task.clicks))
        self.assertTrue(task._news_page_loop_detected)
        self.assertIn("循环", messages[-1])

    def test_ocr_is_skipped_when_text_supplied(self):
        task = _base_task()
        task.ocr = lambda **kwargs: self.fail("已提供 OCR 文本时不应再 OCR")
        self.assertFalse(
            task.dismiss_fullscreen_news_page(_blank_frame(), ocr_text=""),
        )


class AutoLoginNewsPageTest(unittest.TestCase):
    def test_clear_home_popup_uses_back_arrow_not_fixed_point(self):
        task = _login_task([_title_box()])
        task._clear_home_popup(0.51, _news_frame())
        self.assertEqual(1, len(task.clicks))
        self.assertAlmostEqual(
            NEWS_BACK_RELATIVE_POINT[0], task.clicks[0][0], delta=0.004,
        )
        self.assertAlmostEqual(
            NEWS_BACK_RELATIVE_POINT[1], task.clicks[0][1], delta=0.004,
        )
        self.assertIn("整页退出", task.info)
        self.assertNotIn(
            (8.8020833333 / 100.0, 56.9444444444 / 100.0), task.clicks,
        )

    def test_fixed_point_is_skipped_right_after_leaving_news_page(self):
        task = _login_task([_Box("我的小屋", 200, 300, 120, 40)])
        task._last_clear_click_at = -10.0
        task._news_page_back_at = 1e18  # 视作「刚刚退出新闻页」
        task._clear_home_popup(0.51, _blank_frame())
        self.assertEqual([], task.clicks)
        self.assertIn("等待主页刷新", task.actions[-1])

    def test_escape_news_page_resets_return_home_attempt_budget(self):
        task = _login_task([_title_box()])
        task._esc_home_attempts = 3
        self.assertTrue(task._escape_news_page(_blank_frame()))
        self.assertEqual(0, task._esc_home_attempts)
        self.assertEqual(1, len(task.clicks))
        self.assertIn("退出新闻/公告页", task.stages)

    def test_escape_news_page_keeps_budget_when_repeated(self):
        task = _login_task([_title_box()])
        task._esc_home_attempts = 3
        task._news_page_back_attempts = 3
        self.assertTrue(task._escape_news_page(_blank_frame()))
        self.assertEqual(3, task._esc_home_attempts)


if __name__ == "__main__":
    unittest.main()
