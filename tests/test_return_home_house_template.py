"""回归：返回主页房子模板必须只认「房子+齿轮」，不能认白色胶囊按钮。

背景（2026-11-16 15:55 实机日志）：旧模板 ``return_home_house_button.png``
是带白色背景的截图裁切（没有 alpha），在「我的小屋」里对右上角
「⚙ 分享设置」白色胶囊按钮误命中 0.973 @3562,84，点下去打开分享设置弹窗，
随后整屏被遮罩压暗且没有 ✕，自动返回主页 2 分 40 秒后中止整批日常。

新模板 ``return_home_house_button_alpha.png`` 只保留图标白色笔画的 alpha，
白底不参与匹配，因此在主页 1.000 命中、在白色胶囊上不再命中。
"""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.BaseBD2Task import TEMPLATE_DIR, BaseBD2Task
from src.utils.vision_models import MatchResult

WIDTH, HEIGHT = 3840, 2160
TEMPLATE_NAME = "return_home_house_button_alpha.png"
# 实机 4K 帧上的图标 bbox（房子+齿轮），模板即由此 1:2 下采样得到。
ICON_BOX = (3334, 76, 3422, 160)
# 「我的小屋 → 分享设置」按钮（白色胶囊，含深色齿轮图标与文字）。
PILL_BOX = (3205, 60, 3570, 142)


def _task():
    task = object.__new__(BaseBD2Task)
    task.config = {}
    task.info = {}
    task.info_set = lambda key, value=None: task.info.__setitem__(key, value)
    task.log_info = lambda message, **kwargs: None
    task.log_warning = lambda message, **kwargs: None
    return task


def _background():
    return np.full((HEIGHT, WIDTH, 3), 32, dtype=np.uint8)


def _frame_with_icon():
    frame = _background()
    template = cv2.imread(str(TEMPLATE_DIR / TEMPLATE_NAME), cv2.IMREAD_UNCHANGED)
    assert template is not None
    left, top, right, bottom = ICON_BOX
    scaled = cv2.resize(
        template, (right - left, bottom - top), interpolation=cv2.INTER_CUBIC
    )
    alpha = (scaled[:, :, 3:4].astype(np.float32) / 255.0)
    target = frame[top:bottom, left:right].astype(np.float32)
    frame[top:bottom, left:right] = (
        scaled[:, :, :3].astype(np.float32) * alpha + target * (1.0 - alpha)
    ).astype(np.uint8)
    return frame


def _frame_with_share_pill():
    """模拟「我的小屋」右上角：白色圆角胶囊 + 深色文字/齿轮。"""
    frame = _background()
    left, top, right, bottom = PILL_BOX
    cv2.rectangle(frame, (left + 30, top), (right - 30, bottom), (232, 232, 226), -1)
    cv2.circle(frame, (left + 30, (top + bottom) // 2), (bottom - top) // 2, (232, 232, 226), -1)
    cv2.circle(frame, (right - 30, (top + bottom) // 2), (bottom - top) // 2, (232, 232, 226), -1)
    # 左侧深色圆形里的白色双齿轮（旧模板就是在这里/胶囊白底上误命中）。
    cv2.circle(frame, (left + 36, (top + bottom) // 2), 26, (46, 46, 46), -1)
    for offset in (-10, 10):
        cv2.circle(
            frame, (left + 36 + offset, (top + bottom) // 2 + offset // 2), 11, (240, 240, 240), -1
        )
    for word_x in (150, 230, 310):
        cv2.rectangle(
            frame,
            (left + word_x, top + 26),
            (left + word_x + 56, bottom - 26),
            (60, 60, 60),
            -1,
        )
    return frame


class ReturnHomeHouseTemplateTest(unittest.TestCase):
    def test_matches_the_real_house_icon(self):
        task = _task()
        result = task._match_house_button(_frame_with_icon())
        self.assertIsNotNone(result)
        center_x = result.position[0] + result.size[0] // 2
        center_y = result.position[1] + result.size[1] // 2
        self.assertAlmostEqual((ICON_BOX[0] + ICON_BOX[2]) / 2, center_x, delta=6)
        self.assertAlmostEqual((ICON_BOX[1] + ICON_BOX[3]) / 2, center_y, delta=6)

    def test_does_not_match_the_share_settings_pill(self):
        task = _task()
        self.assertIsNone(task._match_house_button(_frame_with_share_pill()))

    def test_rejects_false_positive_over_equipment_compare(self):
        frame = _background()
        frame[36:126, 3270:3610] = (202, 211, 219)
        frame[58:96, 3405:3480] = 50
        match = MatchResult(.96, (3542, 6), (84, 106))
        with patch("src.utils.task_vision.match_template", return_value=match):
            self.assertIsNone(_task()._match_house_button(frame))


if __name__ == "__main__":
    unittest.main()
