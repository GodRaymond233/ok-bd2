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
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.trigger.AutoLoginTask import AutoLoginTask
from src.utils.home_confirmation import (
    modal_close_glyph,
    modal_close_point,
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
    task._quick_vision = lambda: SimpleNamespace(ocr_text=lambda *_args, **_kwargs: "")
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
    task._quick_vision = lambda: SimpleNamespace(ocr_text=lambda *_args, **_kwargs: "")
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

    def test_glyph_detection_ignores_corner_artifact(self):
        """面板右上角圆角会被中值差分检出，但不能被当成关闭按钮。"""
        frame = _popup_frame(with_glyph=False)
        left, top, width, _ = REAL_PANEL
        corner_x = left + width - 4
        corner_y = top + 3
        frame[corner_y : corner_y + 12, corner_x - 12 : corner_x] = 90
        self.assertIsNone(modal_close_glyph(frame, REAL_PANEL))

    def test_dark_header_close_glyph_outside_bright_body(self):
        body = (516, 316, 948, 512)
        frame = _popup_frame(bbox=body, with_glyph=False)
        # A pair of horizontal text strokes and two legs at the old click point.
        frame[326:329, 1398:1416] = 65
        frame[332:335, 1398:1416] = 65
        for step in range(11):
            frame[328 + step, 1405 - step // 2:1409 - step // 2] = 65
            frame[334 + step, 1405 + step:1409 + step] = 65
        self.assertIsNone(modal_close_glyph(frame, body))
        _draw_close_glyph(frame, (1480, 288), value=250)
        x, y = modal_close_glyph(frame, body)
        self.assertAlmostEqual(1480 / WIDTH, x, delta=0.002)
        self.assertAlmostEqual(288 / HEIGHT, y, delta=0.002)


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

    def test_panel_without_glyph_never_clicks(self):
        task = _base_task()
        self.assertFalse(task.dismiss_home_modal_popup(_popup_frame(with_glyph=False)))
        self.assertEqual([], task.clicks)
        self.assertIn("未定位到关闭叉号", task.info["主页弹窗"])
        self.assertTrue(task._home_modal_unlocated)

    def test_stops_after_panel_survives_repeated_clicks(self):
        task = _base_task()
        frame = _popup_frame()
        ticks = iter((10, 15, 20, 25, 65))

        with patch("src.tasks.BaseBD2Task.monotonic", side_effect=lambda: next(ticks)):
            results = [task.dismiss_home_modal_popup(frame) for _ in range(5)]

        self.assertEqual([True, True, True, True, False], results)
        self.assertEqual(4, len(task.clicks))
        self.assertIn("停止点击", task.info["主页弹窗"])

    def test_announcement_clear_skips_fixed_point_when_glyph_missing(self):
        """面板在、叉号找不到时不能再点固定点，否则会点开公告形成循环。"""
        task = _base_task()
        cleared = task.dismiss_home_announcement(
            _popup_frame(with_glyph=False), (False, 2, 126.0, "抽抽乐"), "自动返回主页",
        )
        self.assertTrue(cleared)
        self.assertEqual([], task.clicks)
        self.assertIn("等待可识别", task.info["主页临时公告"])

    def test_auto_login_skips_fixed_point_when_glyph_missing(self):
        task = _login_task()
        task.dismiss_home_announcement(
            _popup_frame(with_glyph=False), (False, 2, 126.0, "抽抽乐"), "自动登录",
        )
        self.assertEqual([], task.clicks)

    def test_auto_login_waits_when_close_control_is_absent(self):
        task = _login_task()
        task.dismiss_home_announcement(_dimmed_frame(), (False, 2, 126.0, "抽抽乐"), "自动登录")
        self.assertEqual([], task.clicks)


if __name__ == "__main__":
    unittest.main()
