"""回归：深色模态弹窗（我的小屋 → 分享设置）关不掉，导致一键日常整批中止。

背景（2026-11-16 14:55 实机日志）：
1. 自动返回主页在「我的小屋」里对右上角「⚙ 分享设置」白色胶囊按钮误命中
   房子模板 0.973 @3562,84，点下去打开了「分享设置」弹窗。
2. 该弹窗面板底色只有 gray≈45，``modal_panel_bbox`` 的绝对亮度判据直接不成立；
   面板上也没有右上角 ✕，只有底部「取消 / 确认」。于是房子按钮、左上角返回、
   返回键、H 快捷键全部无效，2 分 40 秒后一键日常中止，后面 6 项全跳过。
3. 修法：新增「整屏被遮罩压暗 + OCR 明确读到取消类按钮」的关闭路径，点完由
   调用方复核画面；确认类文案一律不点，避免提交未知操作。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task
from src.utils.home_confirmation import (
    MODAL_CANCEL_RELATIVE_REGION,
    modal_cancel_point,
    modal_overlay_dimmed,
    modal_overlay_p95,
)

WIDTH, HEIGHT = 3840, 2160
# 实机 4K「分享设置」帧的 OCR 结果（1280x720 缩图读到后按 3 倍换算）。
CANCEL_BOX = (1644, 1362, 115, 71)
CONFIRM_BOX = (2078, 1362, 104, 60)
TITLE_BOX = (1797, 719, 238, 67)


def _box(name, geometry, confidence=0.99):
    x, y, width, height = geometry
    return SimpleNamespace(
        name=name,
        confidence=confidence,
        x=x,
        y=y,
        width=width,
        height=height,
    )


def _cancel_boxes():
    return [
        _box("取消", CANCEL_BOX),
        _box("确认", CONFIRM_BOX),
    ]


def _dim_frame(value=30):
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def _base_task(boxes):
    task = object.__new__(BaseBD2Task)
    task.config = {}
    task.info = {}
    task.clicks = []
    task.info_set = lambda key, value=None: task.info.__setitem__(key, value)
    task.log_info = lambda message, **kwargs: None
    task.log_warning = lambda message, **kwargs: None
    vision = SimpleNamespace(
        ocr_boxes=lambda frame, name, **kwargs: list(boxes),
    )
    task._quick_vision = lambda: vision
    task.operate_click = lambda x, y, **kwargs: task.clicks.append((x, y))
    return task


class ModalOverlayDetectionTest(unittest.TestCase):
    def test_dark_mask_is_dimmed_and_bright_page_is_not(self):
        self.assertGreater(modal_overlay_p95(_dim_frame(240)), 150.0)
        self.assertFalse(modal_overlay_dimmed(_dim_frame(240)))
        self.assertTrue(modal_overlay_dimmed(_dim_frame(30)))

    def test_empty_frame_is_not_treated_as_dimmed(self):
        self.assertEqual(0.0, modal_overlay_p95(None))
        self.assertFalse(modal_overlay_dimmed(np.zeros((0, 0, 3), np.uint8)))


class ModalCancelPointTest(unittest.TestCase):
    def test_picks_the_cancel_button_and_ignores_confirm(self):
        point = modal_cancel_point(_cancel_boxes(), WIDTH, HEIGHT)
        self.assertIsNotNone(point)
        expected_x = (CANCEL_BOX[0] + CANCEL_BOX[2] / 2) / WIDTH
        expected_y = (CANCEL_BOX[1] + CANCEL_BOX[3] / 2) / HEIGHT
        self.assertAlmostEqual(expected_x, point[0], places=6)
        self.assertAlmostEqual(expected_y, point[1], places=6)

    def test_confirm_only_dialog_is_never_clicked(self):
        self.assertIsNone(modal_cancel_point([_box("确认", CONFIRM_BOX)], WIDTH, HEIGHT))
        self.assertIsNone(modal_cancel_point([_box("确定", CONFIRM_BOX)], WIDTH, HEIGHT))

    def test_state_changing_cancel_variants_are_rejected(self):
        """「取消分享」是撤销共享的按钮，不是关闭弹窗，点了会改游戏状态。"""
        self.assertIsNone(modal_cancel_point([_box("取消分享", CANCEL_BOX)], WIDTH, HEIGHT))

    def test_title_and_low_confidence_text_are_rejected(self):
        self.assertIsNone(modal_cancel_point([_box("分享设置", TITLE_BOX)], WIDTH, HEIGHT))
        self.assertIsNone(
            modal_cancel_point([_box("取消", CANCEL_BOX, confidence=0.30)], WIDTH, HEIGHT)
        )

    def test_lowest_cancel_wins_and_left_column_is_ignored(self):
        top = _box("取消", (1644, 400, 115, 71))
        low = _box("关闭", (1644, 1700, 115, 71))
        left_column = _box("取消", (40, 1700, 115, 71))
        point = modal_cancel_point([top, low, left_column], WIDTH, HEIGHT)
        self.assertIsNotNone(point)
        expected_y = (1700 + 71 / 2) / HEIGHT
        self.assertAlmostEqual(expected_y, point[1], delta=0.005)

    def test_region_bounds_are_documented(self):
        left, top, right, bottom = MODAL_CANCEL_RELATIVE_REGION
        self.assertTrue(0.0 < left < right < 1.0)
        self.assertTrue(0.0 < top < bottom < 1.0)


class BlockingModalDismissTest(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        patcher = patch("src.tasks.BaseBD2Task.monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clicks_cancel_when_the_screen_is_dimmed(self):
        task = _base_task(_cancel_boxes())
        self.assertTrue(task.dismiss_blocking_modal(_dim_frame()))
        self.assertEqual(1, len(task.clicks))
        x, y = task.clicks[0]
        self.assertAlmostEqual((CANCEL_BOX[0] + CANCEL_BOX[2] / 2) / WIDTH, x, delta=0.002)
        self.assertAlmostEqual((CANCEL_BOX[1] + CANCEL_BOX[3] / 2) / HEIGHT, y, delta=0.002)
        self.assertIn("取消", task.info["主页弹窗"])

    def test_bright_page_never_clicks(self):
        task = _base_task(_cancel_boxes())
        self.assertFalse(task.dismiss_blocking_modal(_dim_frame(240)))
        self.assertEqual([], task.clicks)

    def test_dimmed_page_without_cancel_text_never_clicks(self):
        task = _base_task([_box("确认", CONFIRM_BOX)])
        self.assertFalse(task.dismiss_blocking_modal(_dim_frame()))
        self.assertEqual([], task.clicks)

    def test_same_button_is_throttled_within_one_second(self):
        task = _base_task(_cancel_boxes())
        self.assertTrue(task.dismiss_blocking_modal(_dim_frame()))
        self.clock[0] += 0.2
        self.assertFalse(task.dismiss_blocking_modal(_dim_frame()))
        self.assertEqual(1, len(task.clicks))

    def test_stops_clicking_after_four_failed_attempts(self):
        task = _base_task(_cancel_boxes())
        clicks = 0
        for _ in range(6):
            if task.dismiss_blocking_modal(_dim_frame()):
                clicks += 1
            self.clock[0] += 2.0
        self.assertEqual(4, clicks)
        self.assertIn("停止点击", task.info["主页弹窗"])

    def test_missing_frame_is_ignored(self):
        task = _base_task(_cancel_boxes())
        self.assertFalse(task.dismiss_blocking_modal(None))
        self.assertEqual([], task.clicks)


if __name__ == "__main__":
    unittest.main()
