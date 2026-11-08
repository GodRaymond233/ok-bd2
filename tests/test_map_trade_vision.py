"""Map-trade vision tests (split from test_map_trade.py)."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.BaseBD2Task import (
    green_mask_from_template,
)
from src.tasks.map_trade.data import SHOP_CARTRIDGE_BRIGHTNESS
from src.tasks.map_trade.models import (
    MatchResult,
    TemplateSpec,
)
from src.tasks.map_trade.trader import BUY_CONFIRM_DIALOG_REGION
from src.tasks.map_trade.vision import (
    Vision,
    parse_used_limit,
)
from src.utils.image_utils import to_gray
from tests.helpers.map_trade import FakeTask
from tests.helpers.recognition_fixtures import load_bgr

ROOT = Path(__file__).resolve().parents[1]


class VisionTest(unittest.TestCase):

    def test_stable_template_click_waits_for_temporal_consensus(self):
        task = FakeTask()
        sleeps = []
        task.sleep = sleeps.append
        vision = Vision(task)
        result = MatchResult(0.91, (100, 200), (40, 20), pixel_score=0.92)
        vision.match = lambda *_args, **_kwargs: result
        vision.passes = lambda *_args, **_kwargs: True

        self.assertTrue(
            vision.click_stable_template(
                TemplateSpec("stable", "unused.png"),
                timeout=0.01,
            )
        )
        self.assertEqual((120 / 1280, 210 / 720, 0.8), task.clicks[-1])
        self.assertAlmostEqual(1.0, sum(sleeps))
        self.assertTrue(any(key == "stable稳定识别" for key, _value in task.infos))

    def test_template_pass_requires_pixel_similarity_when_configured(self):
        task = FakeTask()
        task.config["跑图跑商识图阈值"] = 0.72
        vision = Vision(task)
        spec = TemplateSpec("test", "unused.png", 0.72, min_pixel_score=0.80)

        self.assertFalse(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), pixel_score=0.79),
                spec,
            )
        )
        self.assertTrue(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), pixel_score=0.81),
                spec,
            )
        )

    def test_template_pass_requires_zncc_when_configured(self):
        task = FakeTask()
        task.config["跑图跑商识图阈值"] = 0.72
        vision = Vision(task)
        spec = TemplateSpec("test", "unused.png", 0.72, min_zncc_score=0.85)

        self.assertFalse(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), zncc_score=0.84),
                spec,
            )
        )
        self.assertTrue(
            vision.passes(
                MatchResult(0.90, (0, 0), (10, 10), zncc_score=0.86),
                spec,
            )
        )

    def test_story_cartridge_brightness_calibration_separates_selected_state(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        normal = to_gray(load_bgr(template_root / SHOP_CARTRIDGE_BRIGHTNESS.normal_template))
        unselected = to_gray(load_bgr(
            template_root / SHOP_CARTRIDGE_BRIGHTNESS.unselected_template,
        ))
        self.assertIsNotNone(normal)
        self.assertIsNotNone(unselected)

        correlation = cv2.matchTemplate(unselected, normal, cv2.TM_CCOEFF_NORMED)
        _minimum, score, _minimum_point, point = cv2.minMaxLoc(correlation)
        x, y = point
        aligned_unselected = unselected[y : y + normal.shape[0], x : x + normal.shape[1]]
        unselected_ratio = Vision.foreground_brightness_ratio(
            normal,
            aligned_unselected,
            minimum_reference_gray=SHOP_CARTRIDGE_BRIGHTNESS.foreground_min_gray,
        )
        normal_ratio = Vision.foreground_brightness_ratio(
            normal,
            normal,
            minimum_reference_gray=SHOP_CARTRIDGE_BRIGHTNESS.foreground_min_gray,
        )

        self.assertEqual((2, 3), point)
        self.assertGreater(score, 0.99)
        self.assertAlmostEqual(1.0, normal_ratio, places=6)
        self.assertAlmostEqual(
            SHOP_CARTRIDGE_BRIGHTNESS.unselected_reference_ratio,
            unselected_ratio,
            delta=0.02,
        )
        self.assertTrue(SHOP_CARTRIDGE_BRIGHTNESS.is_selected(normal_ratio))
        self.assertFalse(SHOP_CARTRIDGE_BRIGHTNESS.is_selected(unselected_ratio))

    def test_green_screen_mask_excludes_only_pure_green(self):
        template = np.array([[[0, 255, 0], [1, 254, 1], [20, 30, 40]]], dtype=np.uint8)

        mask = green_mask_from_template(template)

        np.testing.assert_array_equal(mask, np.array([[0, 255, 255]], dtype=np.uint8))

    def test_root_rgba_template_uses_alpha_without_masking_opaque_green(self):
        with tempfile.TemporaryDirectory() as directory:
            template = np.array(
                [
                    [
                        [0, 255, 0, 255],
                        [20, 30, 40, 0],
                    ]
                ],
                dtype=np.uint8,
            )
            path = Path(directory) / "root-alpha.png"
            self.assertTrue(cv2.imwrite(str(path), template))
            with patch(
                "src.tasks.map_trade.vision.TEMPLATE_DIR",
                Path(directory),
            ):
                _gray, mask = Vision(FakeTask())._load(TemplateSpec("root alpha", path.name))

        np.testing.assert_array_equal(mask, np.array([[255, 0]], dtype=np.uint8))

    def test_template_color_ratios_only_measure_alpha_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            template = np.zeros((2, 2, 4), dtype=np.uint8)
            template[:, :, 3] = np.array([[255, 255], [255, 0]], dtype=np.uint8)
            path = Path(directory) / "color-mask.png"
            self.assertTrue(cv2.imwrite(str(path), template))
            frame = np.array(
                [
                    [
                        [0, 100, 0],
                        [0, 0, 100],
                    ],
                    [
                        [80, 80, 80],
                        [0, 255, 0],
                    ],
                ],
                dtype=np.uint8,
            )
            with patch(
                "src.tasks.map_trade.vision.TEMPLATE_DIR",
                Path(directory),
            ):
                ratios = Vision(FakeTask()).template_color_ratios(
                    frame,
                    TemplateSpec("colors", path.name),
                    MatchResult(1.0, (0, 0), (2, 2)),
                )

        self.assertIsNotNone(ratios)
        self.assertAlmostEqual(1 / 3, ratios[0])
        self.assertAlmostEqual(1 / 3, ratios[1])
        self.assertAlmostEqual(1 / 3, ratios[2])

    def test_star_color_uses_saturation(self):
        match = MatchResult(0.9, (0, 0), (20, 20))
        yellow = np.full((20, 20, 3), (0, 255, 255), dtype=np.uint8)
        gray = np.full((20, 20, 3), (128, 128, 128), dtype=np.uint8)

        self.assertTrue(Vision.star_is_yellow(yellow, match))
        self.assertFalse(Vision.star_is_yellow(gray, match))

    def test_ocr_roi_coordinates_are_returned_in_full_frame_space(self):
        task = FakeTask()
        task.ocr = lambda **_kwargs: [SimpleNamespace(name="确认", x=10, y=20, width=30, height=10)]
        vision = Vision(task)

        boxes = vision.ocr_boxes(task.capture_frame(), "roi", roi=(150, 300, 450, 150))

        self.assertEqual(
            (110, 220, 30, 10), (boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height)
        )

    def test_ocr_roi_outside_frame_is_rejected_before_ocr(self):
        task = FakeTask()
        task.ocr = lambda **_kwargs: self.fail("空裁剪区域不得送入OCR")
        vision = Vision(task)

        boxes = vision.ocr_boxes(
            task.capture_frame(),
            "outside",
            roi=(2100, 1200, 150, 150),
        )

        self.assertEqual([], boxes)

    def test_ocr_scale_resizes_input_and_restores_box_coordinates(self):
        task = FakeTask()
        captured_shapes = []

        def ocr(**kwargs):
            captured_shapes.append(kwargs["frame"].shape)
            return [SimpleNamespace(name="确认", x=20, y=30, width=40, height=10)]

        task.ocr = ocr
        vision = Vision(task)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        left, top, region = Vision._relative_roi(frame, BUY_CONFIRM_DIALOG_REGION)

        boxes = vision.ocr_boxes(
            frame,
            "scaled-roi",
            relative_roi=BUY_CONFIRM_DIALOG_REGION,
            ocr_scale=2.0,
        )

        self.assertEqual((region.shape[0] * 2, region.shape[1] * 2, 3), captured_shapes[0])
        self.assertEqual(
            (left + 10, top + 15, 20, 5),
            (boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height),
        )

    def test_skill_count_parser(self):
        self.assertEqual((3, 5), parse_used_limit("3 / 5"))
        self.assertEqual((10, 10), parse_used_limit("次数 10:10"))
        self.assertIsNone(parse_used_limit("11/10"))
        self.assertIsNone(parse_used_limit("次数未知"))
        for text, limit, used, expected in (
            ("7/121", 21, 7, None),
            ("7|21", 21, 7, (7, 21)),
            ("7:21", 21, 7, (7, 21)),
            ("7121", 21, 7, (7, 21)),
            ("721", 21, None, (7, 21)),
            ("7175", 75, 7, (7, 75)),
            ("11775", 75, 11, None),
            ("1121", 21, None, None),
            ("1121", 21, 11, (11, 21)),
            ("1121", 21, 1, (1, 21)),
            ("2121", 21, None, None),
            ("2121", 21, 21, (21, 21)),
            ("2121", 21, 2, (2, 21)),
            ("121/121", 21, None, None),
            ("7/21 8/21", 21, 7, None),
            ("提示7121", 21, 7, None),
            ("2121", None, None, None),
            ("1Y21", 21, 1, (1, 21)),
            ("1V21", 21, None, (1, 21)),
            (" 1 y 21 ", 21, 1, (1, 21)),
            ("1v21", 21, 1, (1, 21)),
            ("1Y21", None, None, None),
            ("1Y21", 70, 1, None),
            ("1Y21", 21, 2, None),
            ("22Y21", 21, None, None),
            ("0Y0", 0, None, None),
            ("提示1Y21", 21, 1, None),
            ("1V21 extra", 21, 1, None),
            ("1Y21 2V21", 21, None, None),
            ("1X21", 21, 1, None),
        ):
            with self.subTest(text=text, limit=limit, used=used):
                self.assertEqual(expected, parse_used_limit(text, limit, used))

    def test_find_all_returns_all_peaks_in_full_frame_coordinates(self):
        task = FakeTask()
        vision = Vision(task)
        rng = np.random.default_rng(11)
        template = rng.integers(0, 256, (20, 20), dtype=np.uint8)
        frame = np.zeros((1080, 1920), dtype=np.uint8)
        frame[400:420, 300:320] = template
        frame[400:420, 700:720] = template
        spec = TemplateSpec(
            "find-all",
            "quick_switch_cartridges/unused.png",
        )
        vision._load = lambda _spec: (template, None)

        matches = vision.find_all(frame, spec, threshold=0.95)

        self.assertEqual(2, len(matches))
        self.assertEqual([(310, 410), (710, 410)], sorted(value.center for value in matches))
        self.assertTrue(all(value.score >= 0.95 for value in matches))

    def test_wait_ocr_returns_first_matching_poll_and_stops(self):
        task = FakeTask()
        vision = Vision(task)
        polls = iter(["无关内容", "生成 魔法阵 确认"])

        def fake_ocr_text(_frame, name, _roi=None, **_kwargs):
            self.assertEqual("传送生成弹窗", name)
            return next(polls)

        sleeps = []
        vision.ocr_text = fake_ocr_text
        vision.capture = lambda: None
        task.sleep = lambda seconds: sleeps.append(seconds)

        text = vision.wait_ocr(["生成"], timeout=5.0, name="传送生成弹窗", interval=0.5)

        self.assertEqual("生成 魔法阵 确认", text)
        self.assertEqual([0.5], sleeps)


class FrameGeometryTest(unittest.TestCase):
    @staticmethod
    def _scene(width: int, height: int) -> np.ndarray:
        rng = np.random.default_rng(7)
        return rng.integers(40, 220, size=(height, width, 3), dtype=np.uint8)

    def test_regular_16_9_frame_is_accepted(self):
        geometry = Vision.evaluate_frame_geometry(self._scene(1280, 720))

        self.assertTrue(geometry.accepted)
        self.assertEqual((0, 0, 1280, 720), geometry.content_rect)

    def test_true_letterbox_bars_are_rejected(self):
        frame = self._scene(1200, 675)
        letterboxed = np.zeros((800, 1200, 3), dtype=np.uint8)
        letterboxed[62:737, :] = frame

        geometry = Vision.evaluate_frame_geometry(letterboxed)

        self.assertFalse(geometry.accepted)
        self.assertTrue(geometry.has_black_bars)
        self.assertEqual((0, 62, 1200, 675), geometry.content_rect)

    def test_dark_scene_edge_is_not_letterbox(self):
        # User captures at irregular window sizes keep a pure-black scenery
        # strip on the right.  Cutting it would break the 16:9 content ratio,
        # so the frame must stay accepted with the full-frame content rect.
        frame = self._scene(1886, 1100)
        dark_scened = np.zeros((1100, 1956, 3), dtype=np.uint8)
        dark_scened[:, :1886] = frame

        geometry = Vision.evaluate_frame_geometry(dark_scened)

        self.assertTrue(geometry.accepted, tuple(geometry.rejection_reasons))
        self.assertEqual((0, 0, 1956, 1100), geometry.content_rect)

    def test_non_16_9_frame_is_rejected(self):
        geometry = Vision.evaluate_frame_geometry(self._scene(1000, 600))

        self.assertFalse(geometry.accepted)
        self.assertIn("画面比例不是16:9", geometry.rejection_reasons[0])

    def test_below_minimum_frame_is_rejected(self):
        geometry = Vision.evaluate_frame_geometry(self._scene(800, 450))

        self.assertFalse(geometry.accepted)
        self.assertIn("画面物理尺寸过小", geometry.rejection_reasons[0])
