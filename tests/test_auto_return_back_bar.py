"""回归：返回条模板走统一 task_vision 的跨分辨率缩放。

注意：fixture 是“真实资产 + 合成帧”的回归，验证统一缩放在 2560/1920/1280
下都能命中并点中；真实低分辨率实机正/负样本仍需在对应分辨率客户端采集。
"""

import unittest

import cv2
import numpy as np

from src.tasks.BaseBD2Task import TEMPLATE_DIR, BaseBD2Task

RESOLUTIONS = ((2560, 1440), (1920, 1080), (1280, 720))


def _make_frame(width: int, height: int, with_bar: bool):
    frame = np.full((height, width, 3), (36, 34, 30), dtype=np.uint8)
    template = cv2.imread(str(TEMPLATE_DIR / "back_return_bar_arrow.png"))
    if template is None:
        raise AssertionError("missing back_return_bar_arrow.png asset")
    if not with_bar:
        rng = np.random.default_rng(7)
        for _ in range(12):
            x0 = int(rng.integers(0, width - 60))
            y0 = int(rng.integers(0, height - 40))
            w = int(rng.integers(20, 90))
            h = int(rng.integers(14, 46))
            color = tuple(int(c) for c in rng.integers(80, 240, size=3))
            cv2.rectangle(frame, (x0, y0), (x0 + w, y0 + h), color, -1)
        return frame, None

    scale = width / 1920.0
    t_width = max(8, round(template.shape[1] * scale))
    t_height = max(8, round(template.shape[0] * scale))
    display = cv2.resize(template, (t_width, t_height), interpolation=cv2.INTER_AREA)
    left, top = 20, 18
    frame[top : top + t_height, left : left + t_width] = display
    return frame, (left + t_width // 2, top + t_height // 2)


class AutoReturnBackBarResolutionTest(unittest.TestCase):
    def _task(self):
        task = object.__new__(BaseBD2Task)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y))
        task.__dict__.setdefault("_central_template_cache", {})
        return task, clicks

    def test_finds_and_clicks_back_bar_at_2560_1920_1280(self):
        for width, height in RESOLUTIONS:
            with self.subTest(resolution=f"{width}x{height}"):
                frame, expected_center = _make_frame(width, height, with_bar=True)
                self.assertIsNotNone(expected_center)
                task, clicks = self._task()
                self.assertTrue(BaseBD2Task._click_top_left_back(task, frame, 1))
                self.assertEqual(1, len(clicks))
                click_x, click_y = clicks[0]
                center_x, center_y = expected_center
                tol = max(3.0, center_x * 0.08)
                self.assertAlmostEqual(click_x * width, center_x, delta=tol)
                self.assertAlmostEqual(click_y * height, center_y, delta=tol)

    def test_no_bar_does_not_match(self):
        frame, _ = _make_frame(1920, 1080, with_bar=False)
        task, clicks = self._task()
        self.assertFalse(BaseBD2Task._click_top_left_back(task, frame, 1))
        self.assertEqual([], clicks)


if __name__ == "__main__":
    unittest.main()
