"""回归：主页被模态弹窗遮挡时点它右上角的关闭叉号。

背景：
1. 2026-11-16 实机停在「登录加成」弹窗时，旧代码只会按固定坐标 (8.8%, 56.9%)
   点主页背景（弹窗左侧的压暗区域），点不掉也回不到主页，自动登录循环到超时。
2. 同一天又发现「公告板」弹窗（面板 1600x1220，X 在面板内 y≈0.058）与
   「登录加成」（面板 1081x641，X 在面板内 y≈0.160）头部高度不同。用同一个
   固定比例取点会点到第一条公告上，点开后又退回同一个弹窗，形成死循环。
   所以关闭点改成在面板右上角直接找叉号字形，找不到就绝不点击。
"""

import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.trigger.AutoLoginTask import AutoLoginTask
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    MODAL_CLOSE_RELATIVE_IN_PANEL,
    modal_close_glyph,
    modal_close_point,
    modal_panel_bbox,
)

HEIGHT, WIDTH = 1080, 1920
# 实机「登录加成」帧在 1920x1080 下的面板 bbox（X 中心实测 1480,285）。
REAL_PANEL = (424, 178, 1081, 641)
# 实机「公告板」帧在 3840x2160 下的面板 bbox（X 中心换算 2656,531）。
BIG_HEIGHT, BIG_WIDTH = 2160, 3840
BOARD_PANEL = (1120, 460, 1600, 1220)
BOARD_CLOSE = (2656, 531)


def _dimmed_frame(value: int = 70, size=(HEIGHT, WIDTH)):
    return np.full((size[0], size[1], 3), value, dtype=np.uint8)


def _draw_close_glyph(frame, center, size=27, value=60):
    """在 frame 上画一个叉号，模拟弹窗右上角的关闭按钮。"""
    x, y = center
    half = size // 2
    thickness = max(2, size // 5)
    for offset in range(-half, half + 1):
        for shift in range(-(thickness // 2), thickness // 2 + 1):
            row = min(frame.shape[0] - 1, max(0, y + offset))
            left = min(frame.shape[1] - 1, max(0, x + offset + shift))
            right = min(frame.shape[1] - 1, max(0, x - offset + shift))
            frame[row, left] = value
            frame[row, right] = value
    return frame


def _popup_frame(bbox=REAL_PANEL, close=(1480, 285), with_glyph=True):
    frame = _dimmed_frame()
    left, top, width, height = bbox
    frame[top : top + height, left : left + width] = 255
    if with_glyph:
        _draw_close_glyph(frame, close)
    return frame


def _board_frame(with_glyph=True):
    frame = _dimmed_frame(size=(BIG_HEIGHT, BIG_WIDTH))
    left, top, width, height = BOARD_PANEL
    frame[top : top + height, left : left + width] = 245
    if with_glyph:
        _draw_close_glyph(frame, BOARD_CLOSE, size=39)
    return frame


def _base_task():
    task = object.__new__(BaseBD2Task)
    task.config = {}
    task.info = {}
    task.clicks = []
    task.info_set = lambda key, value=None: task.info.__setitem__(key, value)
    task.log_info = lambda message, **kwargs: None
    task.log_warning = lambda message, **kwargs: None
    task.sleep = lambda *args, **kwargs: None
    task.operate_click = lambda x, y, **kwargs: task.clicks.append((x, y))
    task._sleep_after_recognition = lambda: None
    return task


def _login_task():
    task = object.__new__(AutoLoginTask)
    task.config = {
        "公告清理点击 X 百分比": 8.8020833333,
        "公告清理点击 Y 百分比": 56.9444444444,
    }
    task.info = {}
    task.clicks = []
    task.stages = []
    task.info_set = lambda key, value=None: task.info.__setitem__(key, value)
    task.log_info = lambda message, **kwargs: None
    task._set_stage = lambda stage: task.stages.append(stage)
    task._set_action = lambda action: None
    task._sleep_after_recognition = lambda: None
    task.operate_click = lambda x, y, **kwargs: task.clicks.append((x, y))
    task._last_clear_click_at = 0.0
    task._home_bright_since = 1.0
    task._state = "clearing"
    return task


class ModalPanelDetectionTest(unittest.TestCase):
    def test_detects_centred_panel_and_maps_close_point(self):
        frame = _popup_frame()
        bbox = modal_panel_bbox(frame)
        self.assertIsNotNone(bbox)
        left, top, width, height = bbox
        self.assertAlmostEqual(REAL_PANEL[0], left, delta=6)
        self.assertAlmostEqual(REAL_PANEL[1], top, delta=6)
        self.assertAlmostEqual(REAL_PANEL[2], width, delta=6)
        self.assertAlmostEqual(REAL_PANEL[3], height, delta=6)

        point = modal_close_point(bbox, WIDTH, HEIGHT)
        self.assertIsNotNone(point)
        expected_x = (left + width * MODAL_CLOSE_RELATIVE_IN_PANEL[0]) / WIDTH
        expected_y = (top + height * MODAL_CLOSE_RELATIVE_IN_PANEL[1]) / HEIGHT
        self.assertAlmostEqual(expected_x, point[0], places=6)
        self.assertAlmostEqual(expected_y, point[1], places=6)

    def test_glyph_detection_finds_login_popup_close_button(self):
        point = modal_close_point(REAL_PANEL, WIDTH, HEIGHT, frame=_popup_frame())
        self.assertIsNotNone(point)
        # 实测 X 中心 (1480,285)。
        self.assertAlmostEqual(1480 / WIDTH, point[0], delta=0.004)
        self.assertAlmostEqual(285 / HEIGHT, point[1], delta=0.004)

    def test_glyph_detection_finds_announcement_board_close_button(self):
        """公告板的 X 在面板内 y≈0.058，固定比例 0.167 会点到第一条公告上。"""
        point = modal_close_point(
            BOARD_PANEL, BIG_WIDTH, BIG_HEIGHT, frame=_board_frame(),
        )
        self.assertIsNotNone(point)
        self.assertAlmostEqual(BOARD_CLOSE[0] / BIG_WIDTH, point[0], delta=0.004)
        self.assertAlmostEqual(BOARD_CLOSE[1] / BIG_HEIGHT, point[1], delta=0.004)
        # 关键：关闭点必须落在面板顶部标题带里，而不是第一条公告上。
        _, top, _, height = BOARD_PANEL
        close_y = point[1] * BIG_HEIGHT
        self.assertLess(close_y, top + height * 0.10)

    def test_glyph_detection_returns_none_without_glyph(self):
        self.assertIsNone(
            modal_close_point(
                REAL_PANEL, WIDTH, HEIGHT, frame=_popup_frame(with_glyph=False),
            )
        )
        self.assertIsNone(modal_close_glyph(_dimmed_frame(), REAL_PANEL))

    def test_glyph_detection_ignores_corner_artifact(self):
        """面板右上角圆角会被中值差分检出，但不能被当成关闭按钮。"""
        frame = _popup_frame(with_glyph=False)
        left, top, width, _ = REAL_PANEL
        corner_x = left + width - 4
        corner_y = top + 3
        frame[corner_y : corner_y + 12, corner_x - 12 : corner_x] = 90
        self.assertIsNone(modal_close_glyph(frame, REAL_PANEL))

    def test_rejects_full_bright_frame_and_tiny_blobs(self):
        self.assertIsNone(modal_panel_bbox(np.full((HEIGHT, WIDTH, 3), 255, np.uint8)))
        self.assertIsNone(modal_panel_bbox(_dimmed_frame()))
        tiny = _dimmed_frame()
        tiny[500:600, 900:1000] = 255
        self.assertIsNone(modal_panel_bbox(tiny))

    def test_rejects_panel_that_does_not_cover_the_centre(self):
        side = _dimmed_frame()
        side[200:900, 0:300] = 255
        self.assertIsNone(modal_panel_bbox(side))

    def test_close_point_requires_a_panel(self):
        self.assertIsNone(modal_close_point(None, WIDTH, HEIGHT))
        self.assertIsNone(modal_close_point((10, 10, 0, 0), WIDTH, HEIGHT))


class ModalPopupClickTest(unittest.TestCase):
    def test_dismiss_clicks_the_panel_close_button(self):
        task = _base_task()
        frame = _popup_frame()
        self.assertTrue(task.dismiss_home_modal_popup(frame))
        self.assertEqual(1, len(task.clicks))
        point = task.clicks[0]
        self.assertAlmostEqual(1480 / WIDTH, point[0], delta=0.004)
        self.assertAlmostEqual(285 / HEIGHT, point[1], delta=0.004)
        self.assertIn("面板", task.info["主页弹窗"])

    def test_announcement_board_click_stays_in_header(self):
        """复现死循环：旧比例点 (0.699,0.307) 落在公告条目上。"""
        task = _base_task()
        self.assertTrue(task.dismiss_home_modal_popup(_board_frame()))
        self.assertEqual(1, len(task.clicks))
        x, y = task.clicks[0]
        self.assertAlmostEqual(BOARD_CLOSE[0] / BIG_WIDTH, x, delta=0.004)
        self.assertAlmostEqual(BOARD_CLOSE[1] / BIG_HEIGHT, y, delta=0.004)
        self.assertLess(y, 0.28)  # 旧值 0.307 会点进第一条公告

    def test_unreadable_frame_never_clicks(self):
        task = _base_task()
        self.assertFalse(task.dismiss_home_modal_popup(_dimmed_frame()))
        self.assertEqual([], task.clicks)
        self.assertIn("未检测到居中亮面板", task.info["主页弹窗"])

    def test_panel_without_glyph_never_clicks(self):
        task = _base_task()
        self.assertFalse(task.dismiss_home_modal_popup(_popup_frame(with_glyph=False)))
        self.assertEqual([], task.clicks)
        self.assertIn("未定位到关闭叉号", task.info["主页弹窗"])
        self.assertTrue(task._home_modal_unlocated)

    def test_stops_after_panel_survives_repeated_clicks(self):
        task = _base_task()
        frame = _popup_frame()
        ticks = iter(range(10, 200, 5))

        with patch("src.tasks.BaseBD2Task.monotonic", side_effect=lambda: next(ticks)):
            results = [task.dismiss_home_modal_popup(frame) for _ in range(5)]

        self.assertEqual([True, True, True, True, False], results)
        self.assertEqual(4, len(task.clicks))
        self.assertIn("停止点击", task.info["主页弹窗"])

    def test_announcement_clear_prefers_modal_close_over_fixed_point(self):
        task = _base_task()
        cleared = task.clear_temporary_home_announcement_if_needed(
            left_hits=2,
            required_left_hits=2,
            brightness=126.0,
            brightness_threshold=185.0,
            gacha_ocr_text="抽抽乐",
            context="自动返回主页",
            frame=_popup_frame(),
        )
        self.assertTrue(cleared)
        self.assertEqual(1, len(task.clicks))
        self.assertNotEqual(HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT, task.clicks[0])

    def test_announcement_clear_skips_fixed_point_when_glyph_missing(self):
        """面板在、叉号找不到时不能再点固定点，否则会点开公告形成循环。"""
        task = _base_task()
        cleared = task.clear_temporary_home_announcement_if_needed(
            left_hits=2,
            required_left_hits=2,
            brightness=126.0,
            brightness_threshold=185.0,
            gacha_ocr_text="抽抽乐",
            context="自动返回主页",
            frame=_popup_frame(with_glyph=False),
        )
        self.assertTrue(cleared)
        self.assertEqual([], task.clicks)
        self.assertIn("跳过固定清理点", task.info["主页临时公告"])

    def test_announcement_clear_falls_back_to_fixed_point(self):
        task = _base_task()
        cleared = task.clear_temporary_home_announcement_if_needed(
            left_hits=2,
            required_left_hits=2,
            brightness=126.0,
            brightness_threshold=185.0,
            gacha_ocr_text="抽抽乐",
            context="自动返回主页",
            frame=_dimmed_frame(),
        )
        self.assertTrue(cleared)
        self.assertEqual([HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT], task.clicks)

    def test_announcement_clear_without_frame_keeps_legacy_behaviour(self):
        task = _base_task()
        cleared = task.clear_temporary_home_announcement_if_needed(
            left_hits=2,
            required_left_hits=2,
            brightness=126.0,
            brightness_threshold=185.0,
            gacha_ocr_text="抽抽乐",
            context="自动返回主页",
        )
        self.assertTrue(cleared)
        self.assertEqual([HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT], task.clicks)

    def test_auto_login_clear_popup_uses_modal_close(self):
        task = _login_task()
        task._clear_home_popup(0.234, _popup_frame())
        self.assertEqual(1, len(task.clicks))
        self.assertAlmostEqual(1480 / WIDTH, task.clicks[0][0], delta=0.004)
        self.assertAlmostEqual(285 / HEIGHT, task.clicks[0][1], delta=0.004)
        self.assertIn("清理弹窗", task.stages)

    def test_auto_login_skips_fixed_point_when_glyph_missing(self):
        task = _login_task()
        task._clear_home_popup(0.234, _popup_frame(with_glyph=False))
        self.assertEqual([], task.clicks)

    def test_auto_login_clears_without_frame_keeps_legacy_behaviour(self):
        task = _login_task()
        task._clear_home_popup(0.234)
        self.assertEqual(1, len(task.clicks))
        self.assertAlmostEqual(8.8020833333 / 100.0, task.clicks[0][0], places=6)
        self.assertAlmostEqual(56.9444444444 / 100.0, task.clicks[0][1], places=6)


if __name__ == "__main__":
    unittest.main()
