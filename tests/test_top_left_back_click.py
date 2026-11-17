"""回归：左上角「返回条」要点右端的 ‹ 箭头，不能点整条模板的几何中心。

背景（2026-11-16 14:53 实机日志）：模板 ``back_return_bar_arrow.png`` 是
整条返回条（218x76 @1080p），左边大片是深色空白，只有右端约 79% 处是箭头。
旧代码点 ``position + size // 2``，在「我的小屋」里落到 (218,99) 的空白处，
点多少次都毫无反应，于是「小屋签到」原地打转、整批日常中止。
实机实测箭头在 4K (344,97)，按 79% 偏移点下去立刻回到主页。
"""

import unittest

import cv2
import numpy as np

from src.tasks.BaseBD2Task import TEMPLATE_DIR, TOP_LEFT_BACK_CHEVRON_RATIO, BaseBD2Task

WIDTH, HEIGHT = 3840, 2160
TEMPLATE_NAME = "back_return_bar_arrow.png"
# 模板匹配在 4K 下的位置（实机日志：模板中心 218,99 -> 起点 0,23，尺寸 436,152）。
PASTE_AT = (0, 23)


def _frame_with_back_bar():
    frame = np.full((HEIGHT, WIDTH, 3), 26, dtype=np.uint8)
    template = cv2.imread(str(TEMPLATE_DIR / TEMPLATE_NAME))
    assert template is not None
    left, top = PASTE_AT
    scaled = cv2.resize(
        template,
        (template.shape[1] * 2, template.shape[0] * 2),
        interpolation=cv2.INTER_CUBIC,
    )
    frame[top : top + scaled.shape[0], left : left + scaled.shape[1]] = scaled
    return frame


def _task():
    task = object.__new__(BaseBD2Task)
    task.config = {}
    task.info = {}
    task.clicks = []
    task.info_set = lambda key, value=None: task.info.__setitem__(key, value)
    task.log_info = lambda message, **kwargs: None
    task.log_warning = lambda message, **kwargs: None
    task.operate_click = lambda x, y, **kwargs: task.clicks.append((x, y))
    return task


class TopLeftBackClickTest(unittest.TestCase):
    def test_clicks_the_chevron_end_not_the_template_centre(self):
        task = _task()
        frame = _frame_with_back_bar()
        self.assertTrue(task._click_top_left_back(frame, 1))
        self.assertEqual(1, len(task.clicks))
        x, y = task.clicks[0]
        # 模板 436x152 贴于 (0,23)：箭头 = (0 + 436*0.79, 23 + 152*0.49) ≈ (344, 97)。
        self.assertAlmostEqual(344 / WIDTH, x, delta=0.012)
        self.assertAlmostEqual(97 / HEIGHT, y, delta=0.012)
        # 几何中心 (218,99) 是深色空白，旧实现就卡在这里。
        self.assertGreater(abs(x - 218 / WIDTH), 0.02)

    def test_chevron_ratio_stays_inside_the_bar(self):
        ratio_x, ratio_y = TOP_LEFT_BACK_CHEVRON_RATIO
        self.assertGreater(ratio_x, 0.5)
        self.assertLess(ratio_x, 1.0)
        self.assertTrue(0.3 < ratio_y < 0.7)

    def test_missing_template_does_not_click(self):
        task = _task()
        frame = np.full((HEIGHT, WIDTH, 3), 26, dtype=np.uint8)
        self.assertFalse(task._click_top_left_back(frame, 1))
        self.assertEqual([], task.clicks)


if __name__ == "__main__":
    unittest.main()
