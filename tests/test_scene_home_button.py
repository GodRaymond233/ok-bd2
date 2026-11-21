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
    SCENE_HOME_BUTTON_RELATIVE_ROI,
    scene_home_button_point,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "scene_home_button"
# 1440p（= 线上窗口分辨率）实测命中点。
EXPECTED_POINT_1440P = (0.9363, 0.0543)
EXPECTED_POINT_1080P = (0.9365, 0.0555)
TOLERANCE = 0.006
# 4K 实测几何：H 方块 36x38、房子 76x69、两者同一条竖中轴、房子中心比方块中心
# 高 71.8px（= 1.89 个方块高）。
PLATE_SIZE = (36, 38)
HOUSE_SIZE = (76, 69)
HOUSE_OFFSET = 1.89


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


def _canvas(height: int = 2160, width: int = 3840) -> np.ndarray:
    """深色背景，保证白色字形与阈值的分界清楚。"""
    return np.full((height, width, 3), 60, dtype=np.uint8)


def _draw_plate(frame: np.ndarray, left: int, top: int) -> None:
    width, height = PLATE_SIZE
    cv2.rectangle(
        frame,
        (left, top),
        (left + width - 1, top + height - 1),
        (255, 255, 255),
        -1,
    )
    # 方块中间挖一个 H：两竖一横的深色笔画，占方块面积约 1/5（真实帧实测 0.16）。
    bar = max(2, width // 9)
    inset_x = width // 3
    inset_y = height // 6
    color = (20, 20, 20)
    left_x = left + inset_x
    right_x = left + width - inset_x - bar
    bottom_y = top + height - inset_y
    cv2.rectangle(frame, (left_x, top + inset_y), (left_x + bar - 1, bottom_y), color, -1)
    cv2.rectangle(frame, (right_x, top + inset_y), (right_x + bar - 1, bottom_y), color, -1)
    mid_y = top + height // 2 - bar // 2
    cv2.rectangle(frame, (left_x, mid_y), (right_x + bar - 1, mid_y + bar - 1), color, -1)


def _draw_house(frame: np.ndarray, center_x: int, bottom: int) -> None:
    width, height = HOUSE_SIZE
    left = center_x - width // 2
    top = bottom - height
    roof_height = height // 2
    cv2.rectangle(
        frame,
        (left, top + roof_height),
        (left + width - 1, bottom),
        (255, 255, 255),
        -1,
    )
    cv2.fillPoly(
        frame,
        [
            np.array(
                [
                    [left, top + roof_height],
                    [center_x, top],
                    [left + width - 1, top + roof_height],
                ],
                dtype=np.int32,
            )
        ],
        (255, 255, 255),
    )
    # 门洞：让房子和真实控件一样是空心结构。
    door_width = max(6, width // 5)
    cv2.rectangle(
        frame,
        (center_x - door_width // 2, bottom - height // 2),
        (center_x + door_width // 2, bottom),
        (60, 60, 60),
        -1,
    )


def _frame_with_control(*, with_house: bool = True, with_plate: bool = True):
    """按实测几何在 4K 帧右上角画一套控件。"""
    frame = _canvas()
    left = round(SCENE_HOME_BUTTON_RELATIVE_ROI[0] * frame.shape[1])
    plate_left = left + 142
    plate_top = round(SCENE_HOME_BUTTON_RELATIVE_ROI[1] * frame.shape[0]) + 148
    center_x = plate_left + PLATE_SIZE[0] // 2
    if with_plate:
        _draw_plate(frame, plate_left, plate_top)
    if with_house:
        _draw_house(frame, center_x, plate_top - 18)
    return frame


class SceneHomeButtonDetectorTest(unittest.TestCase):
    def test_live_resolution_restaurant_frame_exposes_return_point(self):
        # 720p 夹具放大到线上窗口分辨率（2560x1440）后必须稳定命中。
        point = scene_home_button_point(_scale(_load("restaurant_720p.png"), 2.0))
        self.assertIsNotNone(point)
        self.assertAlmostEqual(EXPECTED_POINT_1440P[0], point[0], delta=TOLERANCE)
        self.assertAlmostEqual(EXPECTED_POINT_1440P[1], point[1], delta=TOLERANCE)

    def test_bright_tablecloth_frame_still_exposes_return_point(self):
        # 线上失败帧：白色桌布把房子字形糊成 172px 宽的白斑。
        frame = _load("restaurant_bright_1080p.png")
        point = scene_home_button_point(frame)
        self.assertIsNotNone(point)
        self.assertAlmostEqual(EXPECTED_POINT_1080P[0], point[0], delta=TOLERANCE)
        self.assertAlmostEqual(EXPECTED_POINT_1080P[1], point[1], delta=TOLERANCE)

    def test_point_lands_on_the_white_house_glyph(self):
        frame = _scale(_load("restaurant_720p.png"), 2.0)
        point = scene_home_button_point(frame)
        self.assertIsNotNone(point)
        x = round(point[0] * frame.shape[1])
        y = round(point[1] * frame.shape[0])
        patch = frame[y - 3 : y + 4, x - 3 : x + 4]
        self.assertGreater(float(patch.mean()), 140.0)

    def test_home_frame_has_no_scene_return_point(self):
        self.assertIsNone(scene_home_button_point(_load("home_720p.png")))
        self.assertIsNone(scene_home_button_point(_scale(_load("home_720p.png"), 2.0)))

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

    def test_blank_and_tiny_frames_are_rejected(self):
        self.assertIsNone(scene_home_button_point(None))
        self.assertIsNone(scene_home_button_point(np.zeros((0, 0, 3), dtype=np.uint8)))
        self.assertIsNone(scene_home_button_point(_canvas(height=120, width=200)))

    def test_synthetic_control_is_detected(self):
        frame = _frame_with_control()
        point = scene_home_button_point(frame)
        self.assertIsNotNone(point)
        self.assertGreater(point[0], SCENE_HOME_BUTTON_RELATIVE_ROI[0])
        self.assertLess(point[1], 0.12)
        # 点击点应落在画出来的房子中心（bottom = 方块顶 - 18，房子高 69）。
        plate_top = round(SCENE_HOME_BUTTON_RELATIVE_ROI[1] * frame.shape[0]) + 148
        expected_y = (plate_top - 18 - HOUSE_SIZE[1] / 2) / frame.shape[0]
        self.assertAlmostEqual(expected_y, point[1], delta=0.004)

    def test_plate_without_house_is_rejected(self):
        self.assertIsNone(
            scene_home_button_point(_frame_with_control(with_house=False))
        )

    def test_house_without_plate_is_rejected(self):
        self.assertIsNone(
            scene_home_button_point(_frame_with_control(with_plate=False))
        )

    def test_plate_with_a_letter_that_is_not_h_is_rejected(self):
        # 方块里的字如果是实心「O」而不是 H，竖谷判据必须挡住它。
        frame = _frame_with_control()
        self.assertIsNotNone(scene_home_button_point(frame))
        left = round(SCENE_HOME_BUTTON_RELATIVE_ROI[0] * frame.shape[1]) + 142
        top = round(SCENE_HOME_BUTTON_RELATIVE_ROI[1] * frame.shape[0]) + 148
        width, height = PLATE_SIZE
        cv2.rectangle(frame, (left, top), (left + width - 1, top + height - 1), (255, 255, 255), -1)
        cv2.ellipse(
            frame,
            (left + width // 2, top + height // 2),
            (width // 3, height // 3),
            0,
            0,
            360,
            (20, 20, 20),
            3,
        )
        self.assertIsNone(scene_home_button_point(frame))


if __name__ == "__main__":
    unittest.main()
