"""Map-trade vision tests (split from test_map_trade.py)."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.BaseBD2Task import (
    BaseBD2Task,
    green_mask_from_template,
)
from src.tasks.map_trade.action_icons import (
    ACTION_SLOT_CENTER_RELATIVE_ROIS,
    ACTION_SLOT_CENTERS_REFERENCE,
    ACTION_SLOT_RELATIVE_ROIS,
)
from src.tasks.map_trade.data import SHOP_CARTRIDGE_BRIGHTNESS
from src.tasks.map_trade.models import (
    MatchResult,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import (
    QUICK_SWITCH_CARTRIDGE_REGION,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
)
from src.tasks.map_trade.trader import BUY_CONFIRM_DIALOG_REGION
from src.tasks.map_trade.vision import (
    Vision,
    parse_used_limit,
)
from src.utils.template_resolution import offline_template_scale
from tests.helpers.map_trade import FakeTask

ROOT = Path(__file__).resolve().parents[1]


class SaveFrameTest(unittest.TestCase):
    def test_save_frame_preserves_bgr_bgra_and_grayscale_channels(self):
        from src.tasks import BaseBD2Task as base_task_module

        task = object.__new__(BaseBD2Task)
        task.info_set = lambda *_args: None
        frames = (
            np.array([[[3, 2, 1]]], dtype=np.uint8),
            np.array([[[3, 2, 1, 4]]], dtype=np.uint8),
            np.array([[7]], dtype=np.uint8),
        )

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(base_task_module, "PROBE_OUTPUT_DIR", Path(directory)):
                for index, frame in enumerate(frames):
                    with self.subTest(channels=frame.shape):
                        path = task.save_frame(f"channel_{index}", frame)
                        saved = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                        np.testing.assert_array_equal(saved, frame)


class VisionTest(unittest.TestCase):
    def test_reference_conversion_for_all_supported_resolutions(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(resolution=(width, height)):
                self.assertEqual(
                    (width // 2, height // 2), Vision.reference_point(640, 360, width, height)
                )
                self.assertEqual(
                    (width // 4, height // 4, width // 2, height // 2),
                    Vision.reference_roi((320, 180, 640, 360), width, height),
                )

    def test_sandbox_teleport_template_uses_tight_shared_slot_at_all_resolutions(self):
        self.assertEqual(
            ACTION_SLOT_RELATIVE_ROIS["teleport"],
            SANDBOX_TELEPORT_SKILL_TEMPLATE.relative_roi,
        )
        self.assertEqual(
            ACTION_SLOT_CENTER_RELATIVE_ROIS["teleport"],
            SANDBOX_TELEPORT_SKILL_TEMPLATE.candidate_center_roi,
        )

        source_template = np.random.default_rng(42).integers(
            0,
            256,
            (20, 20),
            dtype=np.uint8,
        )
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(resolution=(width, height)):
                task = FakeTask()
                vision = Vision(task)
                vision._load = lambda _spec: (source_template, None)
                scale = offline_template_scale(
                    SANDBOX_TELEPORT_SKILL_TEMPLATE.file_name,
                    width,
                    height,
                    reference_scale=SANDBOX_TELEPORT_SKILL_TEMPLATE.reference_scale,
                )
                scaled = vision._resize_template(source_template, scale)
                center_x = round(ACTION_SLOT_CENTERS_REFERENCE["teleport"][0] * width / 1920)
                center_y = round(ACTION_SLOT_CENTERS_REFERENCE["teleport"][1] * height / 1080)
                radius_x = round(55 * min(width / 1920, height / 1080))
                frame = np.random.default_rng(width).integers(
                    0,
                    20,
                    (height, width),
                    dtype=np.uint8,
                )

                def paste(center):
                    left = center[0] - scaled.shape[1] // 2
                    top = center[1] - scaled.shape[0] // 2
                    frame[top : top + scaled.shape[0], left : left + scaled.shape[1]] = scaled

                # This candidate remains inside the search crop but outside
                # the center gate and must therefore be rejected.
                paste((center_x + radius_x, center_y))
                outside = vision.match(frame, SANDBOX_TELEPORT_SKILL_TEMPLATE)
                self.assertFalse(vision.passes(outside, SANDBOX_TELEPORT_SKILL_TEMPLATE))

                frame = np.random.default_rng(width + 1).integers(
                    0,
                    20,
                    (height, width),
                    dtype=np.uint8,
                )
                paste((center_x, center_y))
                inside = vision.match(frame, SANDBOX_TELEPORT_SKILL_TEMPLATE)
                self.assertTrue(vision.passes(inside, SANDBOX_TELEPORT_SKILL_TEMPLATE))
                self.assertLessEqual(abs(inside.center[0] - center_x), 2)
                self.assertLessEqual(abs(inside.center[1] - center_y), 2)

    def test_template_click_uses_match_center(self):
        task = FakeTask()
        vision = Vision(task)
        vision.wait_template = lambda *_args, **_kwargs: MatchResult(0.9, (100, 200), (40, 20))

        self.assertTrue(vision.click_template(TemplateSpec("test", "unused.png")))
        self.assertEqual((120 / 1280, 210 / 720, 0.8), task.clicks[-1])
        self.assertIn(
            (
                "test点击中心",
                "center=(120,210), match=0.900, pixel=-1.000",
            ),
            task.infos,
        )

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
        self.assertEqual(10, len(sleeps))
        self.assertTrue(all(seconds == 0.1 for seconds in sleeps))
        self.assertTrue(any(key == "stable稳定识别" for key, _value in task.infos))

    def test_operate_click_log_converts_relative_target_to_client_pixels(self):
        self.assertEqual(
            ("快速切换按钮: client=(959,539), relative=(0.500000,0.500000)"),
            BaseBD2Task._click_log_message(
                0.5,
                0.5,
                1918,
                1079,
                "快速切换按钮",
            ),
        )

    def test_configured_threshold_overrides_template_default(self):
        task = FakeTask()
        task.config["跑图跑商识图阈值"] = 0.81

        self.assertEqual(0.81, Vision(task).threshold_for(TemplateSpec("test", "unused.png", 0.7)))

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

    def test_match_all_returns_multiple_peaks_in_full_frame_coordinates(self):
        task = FakeTask()
        vision = Vision(task)
        rng = np.random.default_rng(7)
        template = rng.integers(0, 256, (20, 20), dtype=np.uint8)
        frame = np.zeros((1080, 1920), dtype=np.uint8)
        frame[930:950, 100:120] = template
        frame[930:950, 500:520] = template
        spec = TemplateSpec(
            "multi",
            "quick_switch_cartridges/unused.png",
            relative_roi=QUICK_SWITCH_CARTRIDGE_REGION,
        )
        vision._load = lambda _spec: (template, None)

        matches = vision.match_all(
            frame,
            spec,
            minimum_score=0.95,
            peak_radius=5,
        )

        self.assertEqual([(110, 940), (510, 940)], sorted(value.center for value in matches))
        self.assertTrue(all(value.pixel_score == 1.0 for value in matches))

    def test_story_cartridge_brightness_calibration_separates_selected_state(self):
        template_root = ROOT / "recognition-assets" / "template-assets"
        normal = cv2.imread(
            str(template_root / SHOP_CARTRIDGE_BRIGHTNESS.normal_template),
            cv2.IMREAD_GRAYSCALE,
        )
        unselected = cv2.imread(
            str(template_root / SHOP_CARTRIDGE_BRIGHTNESS.unselected_template),
            cv2.IMREAD_GRAYSCALE,
        )
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

    def test_template_hsv_color_ratios_only_measure_alpha_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            template = np.zeros((2, 2, 4), dtype=np.uint8)
            template[:, :, 3] = np.array([[255, 255], [255, 0]], dtype=np.uint8)
            path = Path(directory) / "hsv-color-mask.png"
            self.assertTrue(cv2.imwrite(str(path), template))
            frame = np.array(
                [
                    [
                        [0, 200, 255],
                        [160, 160, 160],
                    ],
                    [
                        [20, 20, 20],
                        [0, 200, 255],
                    ],
                ],
                dtype=np.uint8,
            )
            with patch(
                "src.tasks.map_trade.vision.TEMPLATE_DIR",
                Path(directory),
            ):
                ratios = Vision(FakeTask()).template_hsv_color_ratios(
                    frame,
                    TemplateSpec("hsv colors", path.name),
                    MatchResult(1.0, (0, 0), (2, 2)),
                )

        self.assertIsNotNone(ratios)
        self.assertAlmostEqual(1 / 3, ratios[0])
        self.assertAlmostEqual(1 / 3, ratios[1])
        self.assertAlmostEqual(2 / 3, ratios[2])

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

        boxes = vision.ocr_boxes(task.capture_frame(), "roi", roi=(100, 200, 300, 100))

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
            roi=(1400, 800, 100, 100),
        )

        self.assertEqual([], boxes)

    def test_relative_ocr_roi_coordinates_are_returned_in_full_frame_space(self):
        task = FakeTask()
        task.ocr = lambda **_kwargs: [SimpleNamespace(name="确认", x=10, y=20, width=30, height=10)]
        vision = Vision(task)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        boxes = vision.ocr_boxes(
            frame,
            "relative-roi",
            relative_roi=BUY_CONFIRM_DIALOG_REGION,
        )

        self.assertEqual(
            (711, 348, 30, 10),
            (boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height),
        )

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
