"""场景界面「返回主页」控件识别（H 方块 + 上方房子）的回归测试。

坐标全部来自 2026-11-16 真实「格鲁菲餐厅」帧实测，不使用由模板反推的合成截图
作为唯一证据：

* ``restaurant_720p.png``：餐厅真实帧（暗背景）。
* ``restaurant_bright_1080p.png``：同一场景、白色桌布铺满右上角的真实帧。这是
  2026-11-16 线上失败的那一帧——房子字形被亮桌布吞成 172px 宽的白斑，任何「按
  房子外接框量尺寸」的判据都会在这里失效，所以必须留着当反例。
* ``home_720p.png`` / ``my_home_720p.png``：右上角分别是「房子+齿轮」和「分享
  设置」胶囊，都不许被判成返回控件。

实时链路是 2560x1440 的窗口，720p 帧只当素材：控件在 720p 下只有 11~12px 高，
H 笔画已经读不出来，检测器按设计返回「没看到控件」而不是猜。因此正样本都放大到
1080p / 1440p 再喂给检测器，这与线上实际分辨率一致。
"""

import unittest
from pathlib import Path

import cv2
import numpy as np

from src.utils.home_confirmation import (
    scene_home_button_point,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "scene_home_button"
EXPECTED_POINT_1080P = (0.9365, 0.0555)
TOLERANCE = 0.006
def _load(name: str) -> np.ndarray:
    path = FIXTURE_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"missing fixture: {path}"
    return frame


def _scale(frame: np.ndarray, factor: float) -> np.ndarray:
    height, width = frame.shape[:2]
    return cv2.resize(
        frame,
        (round(width * factor), round(height * factor)),
        interpolation=cv2.INTER_CUBIC,
    )


class SceneHomeButtonDetectorTest(unittest.TestCase):
    def test_bright_tablecloth_frame_still_exposes_return_point(self):
        # 线上失败帧：白色桌布把房子字形糊成 172px 宽的白斑。
        frame = _load("restaurant_bright_1080p.png")
        point = scene_home_button_point(frame)
        self.assertIsNotNone(point)
        self.assertAlmostEqual(EXPECTED_POINT_1080P[0], point[0], delta=TOLERANCE)
        self.assertAlmostEqual(EXPECTED_POINT_1080P[1], point[1], delta=TOLERANCE)

    def test_my_home_share_settings_pill_is_not_a_return_control(self):
        # 「我的小屋」右上角是白色「分享设置」胶囊，曾经把旧房子模板骗到 0.973，
        # 也必须挡住「把胶囊的一小块当成 H 方块」这类几何误判。
        small = _load("my_home_720p.png")
        self.assertIsNone(scene_home_button_point(small))
        self.assertIsNone(scene_home_button_point(_scale(small, 1.5)))
        self.assertIsNone(scene_home_button_point(_scale(small, 2.0)))

    def test_rescaled_frame_keeps_the_same_relative_point(self):
        frame = _load("restaurant_720p.png")
        point_1080p = scene_home_button_point(_scale(frame, 1.5))
        point_1440p = scene_home_button_point(_scale(frame, 2.0))
        self.assertIsNotNone(point_1080p)
        self.assertIsNotNone(point_1440p)
        self.assertAlmostEqual(point_1080p[0], point_1440p[0], delta=0.004)
        self.assertAlmostEqual(point_1080p[1], point_1440p[1], delta=0.006)

    def test_low_resolution_frames_are_rejected_rather_than_guessed(self):
        # 720p 下 H 只有 4px 宽，读不出来就该返回 None，绝不能瞎点。
        self.assertIsNone(scene_home_button_point(_load("restaurant_720p.png")))


if __name__ == "__main__":
    unittest.main()
