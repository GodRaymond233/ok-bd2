import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.utils import task_vision
from src.utils.image_utils import (
    StableMatchObservation,
    best_pixel_valid_match,
    candidate_scales,
    crop_relative,
    independent_pixel_valid_matches,
    masked_zncc,
    pixel_similarity,
    reference_roi_frame,
    resize_mask,
    scale_reference_roi,
    stabilize_template_match,
    stable_match_consensus,
    to_gray,
)
from src.utils.ocr_utils import (
    fuzzy_substring_match,
    keyword_match_count,
    normalize_ocr_text,
)
from src.utils.vision_models import MatchResult, TemplateSpec


class ImageRecognitionUtilsTest(unittest.TestCase):
    def test_stable_match_consensus_prefers_persistent_cluster_over_peak_score(self):
        observations = [
            StableMatchObservation(0, (500, 500), 0.99, 0.99),
            StableMatchObservation(1, (100, 100), 0.90, 0.91),
            StableMatchObservation(2, (501, 500), 0.98, 0.98),
        ]
        observations.extend(
            StableMatchObservation(index, (100 + index % 2, 99 + index % 3), 0.91, 0.92)
            for index in range(3, 11)
        )

        consensus = stable_match_consensus(
            observations,
            sample_count=11,
            cluster_radius=24,
            maximum_center_spread=12,
        )

        self.assertIsNotNone(consensus)
        self.assertEqual((100, 100), consensus.center)
        self.assertEqual(9, consensus.hit_count)

    def test_stabilize_template_match_observes_one_second_before_returning(self):
        class Match:
            def __init__(self, center):
                self.score = 0.91
                self.pixel_score = 0.92
                self.position = (center[0] - 5, center[1] - 5)
                self.size = (10, 10)

        sleeps = []
        centers = iter((101 + index % 2, 200 + index % 3) for index in range(20))

        stabilized = stabilize_template_match(
            Match((101, 200)),
            (1080, 1920, 3),
            sample_match=lambda: (Match(next(centers)), (1080, 1920, 3)),
            passes=lambda _match: True,
            sleep=sleeps.append,
        )

        self.assertIsNotNone(stabilized)
        consensus, frame_shape = stabilized
        self.assertEqual((101, 201), consensus.center)
        self.assertEqual((1080, 1920, 3), frame_shape)
        self.assertEqual(10, len(sleeps))
        self.assertTrue(all(seconds == 0.1 for seconds in sleeps))

    def test_source_template_matching_is_centralized(self):
        source_root = Path(__file__).resolve().parents[1] / "src"
        violations = []
        for path in source_root.rglob("*.py"):
            if path.name == "image_utils.py":
                continue
            if "cv2.matchTemplate" in path.read_text(encoding="utf-8"):
                violations.append(str(path.relative_to(source_root)))
        self.assertEqual([], violations)

    def test_candidate_scales_applies_ratios_and_lower_bound(self):
        self.assertEqual(candidate_scales(0.5, (0.5, 1.0, 2.0)), [0.25, 0.5, 1.0])
        self.assertEqual(candidate_scales(0.1), [0.2])

    def test_resize_mask_preserves_binary_values(self):
        mask = np.array([[0, 255], [255, 0]], dtype=np.uint8)
        resized = resize_mask(mask, 2.0)
        self.assertEqual(set(np.unique(resized)), {0, 255})

    def test_to_gray_accepts_gray_bgr_and_bgra(self):
        gray = np.array([[10, 20]], dtype=np.uint8)
        self.assertIs(to_gray(gray), gray)
        self.assertEqual(to_gray(np.dstack([gray, gray, gray])).shape, gray.shape)
        alpha = np.full_like(gray, 255)
        self.assertEqual(to_gray(np.dstack([gray, gray, gray, alpha])).shape, gray.shape)

    def test_pixel_similarity_honors_mask_and_shape(self):
        template = np.array([[0, 100]], dtype=np.uint8)
        region = np.array([[255, 100]], dtype=np.uint8)
        mask = np.array([[0, 255]], dtype=np.uint8)
        self.assertEqual(pixel_similarity(region, template, mask), 1.0)
        self.assertEqual(pixel_similarity(region[:, :1], template), -1.0)

    def test_masked_zncc_ignores_background_and_brightness_offset(self):
        template = np.array([[255, 50, 100], [0, 150, 200]], dtype=np.uint8)
        region = np.array([[0, 80, 130], [255, 180, 230]], dtype=np.uint8)
        mask = np.array([[0, 255, 255], [0, 255, 255]], dtype=np.uint8)

        self.assertAlmostEqual(1.0, masked_zncc(region, template, mask))
        self.assertLess(pixel_similarity(region, template, mask), 0.89)

    def test_masked_zncc_rejects_inverse_and_degenerate_inputs(self):
        template = np.array([[0, 50], [100, 150]], dtype=np.uint8)
        inverse = np.array([[150, 100], [50, 0]], dtype=np.uint8)
        uniform = np.full((2, 2), 100, dtype=np.uint8)
        empty_mask = np.zeros((2, 2), dtype=np.uint8)

        self.assertAlmostEqual(-1.0, masked_zncc(inverse, template))
        self.assertEqual(-1.0, masked_zncc(uniform, template))
        self.assertEqual(-1.0, masked_zncc(template, template, empty_mask))
        self.assertEqual(-1.0, masked_zncc(template[:, :1], template))

    def test_pixel_valid_match_skips_higher_template_score_with_bad_pixels(self):
        template = np.zeros((2, 2), dtype=np.uint8)
        search = np.array(
            [[255, 255, 127, 0, 0], [255, 255, 127, 0, 0]],
            dtype=np.uint8,
        )
        response = np.array([[0.99, np.inf, 1.2, 0.90]], dtype=np.float32)

        candidate = best_pixel_valid_match(
            response,
            search,
            template,
            None,
            template_threshold=0.78,
            pixel_threshold=0.80,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual((3, 0), candidate.location)
        self.assertAlmostEqual(0.90, candidate.score)
        self.assertEqual(1.0, candidate.pixel_score)
        self.assertTrue(np.isfinite(response).all())
        self.assertEqual(-1.0, float(response[0, 1]))
        self.assertEqual(-1.0, float(response[0, 2]))

    def test_pixel_valid_match_honors_candidate_center_bounds(self):
        template = np.zeros((2, 2), dtype=np.uint8)
        search = np.zeros((2, 5), dtype=np.uint8)
        response = np.array([[0.99, 0.80, 0.80, 0.90]], dtype=np.float32)

        candidate = best_pixel_valid_match(
            response,
            search,
            template,
            None,
            template_threshold=0.78,
            pixel_threshold=0.80,
            center_bounds=(3, 0, 5, 2),
        )

        self.assertIsNotNone(candidate)
        self.assertEqual((3, 0), candidate.location)

    def test_pixel_valid_match_can_require_masked_zncc(self):
        template = np.array([[0, 50], [100, 150]], dtype=np.uint8)
        search = np.array(
            [[150, 100, 127, 0, 50], [50, 0, 127, 100, 150]],
            dtype=np.uint8,
        )
        response = np.array([[0.99, 0.80, 0.79, 0.90]], dtype=np.float32)

        candidate = best_pixel_valid_match(
            response,
            search,
            template,
            np.full((2, 2), 255, dtype=np.uint8),
            template_threshold=0.78,
            pixel_threshold=0.0,
            zncc_threshold=0.85,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual((3, 0), candidate.location)
        self.assertAlmostEqual(1.0, candidate.zncc_score)

    def test_independent_matches_filter_pixels_before_final_score_order(self):
        template = np.zeros((2, 2), dtype=np.uint8)
        search = np.array(
            [[255, 255, 127, 0, 0, 127, 0, 0], [255, 255, 127, 0, 0, 127, 0, 0]],
            dtype=np.uint8,
        )
        response = np.array([[0.99, 0.80, 0.79, 0.95, 0.81, 0.80, 0.90]], dtype=np.float32)

        matches = independent_pixel_valid_matches(
            response,
            search,
            template,
            None,
            template_threshold=0.78,
            pixel_threshold=0.80,
            suppression_radius=1,
            max_matches=2,
        )

        self.assertEqual([(3, 0), (6, 0)], [match.location for match in matches])
        self.assertEqual([0.95, 0.90], [round(match.score, 2) for match in matches])

    def test_relative_and_reference_crops_scale_position_and_size(self):
        image = np.arange(100 * 200, dtype=np.int32).reshape(100, 200)
        relative = crop_relative(image, (0.2, 0.3, 0.6, 0.8))
        self.assertEqual(relative.shape, (50, 80))

        left, top, reference = reference_roi_frame(
            image,
            (960, 540, 192, 108),
            (1920, 1080),
        )
        self.assertEqual((left, top), (100, 50))
        self.assertEqual(reference.shape, (10, 20))

    def test_reference_roi_scaling_moves_origin_and_resizes_both_axes(self):
        self.assertEqual(
            (1031, 133, 207, 42),
            scale_reference_roi(
                (1546, 199, 311, 63),
                (1280, 720),
                (1920, 1080),
            ),
        )


class OcrUtilsTest(unittest.TestCase):
    def test_normalize_can_keep_regex_or_strip_to_alphanumeric(self):
        self.assertEqual(normalize_ocr_text(" P V P .* 确认 "), "pvp.*确认")
        self.assertEqual(normalize_ocr_text("折扣-商店！", alnum_only=True), "折扣商店")

    def test_keyword_count_supports_exact_and_fuzzy_matching(self):
        self.assertEqual(
            keyword_match_count(
                "最近 剧情游戏卡",
                ("最近", "战斗玩法游戏卡带"),
            ),
            1,
        )
        self.assertEqual(
            keyword_match_count("返回抽卡页靣", ("返回抽卡页面",), fuzzy_ratio=0.9),
            1,
        )

    def test_keyword_count_folds_traditional_and_simplified_scripts(self):
        self.assertEqual(
            keyword_match_count("取消 一鍵獲得", ("一键获得", "取消")),
            2,
        )
        # OCR 混简繁输出（同一弹窗两次读数各翻转了部分字形）也必须命中。
        self.assertEqual(
            keyword_match_count("餐廳營業额現狀", ("餐厅营业额现状",)),
            1,
        )
        self.assertEqual(
            keyword_match_count("一键獲得", ("一键获得",)),
            1,
        )

    def test_fuzzy_match_rejects_empty_values(self):
        self.assertFalse(fuzzy_substring_match("", "确认", 0.9))
        self.assertFalse(fuzzy_substring_match("确认", "", 0.9))


class TaskVisionMatchTemplateTest(unittest.TestCase):
    FRAME_HEIGHT = 48
    FRAME_WIDTH = 64
    # The frame below keeps 30 * min(width/1920, height/1080) exactly at 1.0,
    # so a root-group spec with this reference scale matches unscaled.
    REFERENCE_SCALE = 30.0
    PATCH_TOP = 20
    PATCH_LEFT = 10

    def _probe_pattern(self):
        return np.fromfunction(
            lambda row, col: 20 + 4 * col + 7 * row,
            (10, 16),
            dtype=float,
        ).astype(np.uint8)

    def _probe_template(self):
        pattern = self._probe_pattern()
        return np.dstack([pattern, pattern, pattern])

    def _make_frame(self, channels):
        frame = np.full(
            (self.FRAME_HEIGHT, self.FRAME_WIDTH, channels),
            60,
            dtype=np.uint8,
        )
        patch = self._probe_pattern()
        frame[self.PATCH_TOP : self.PATCH_TOP + 10, self.PATCH_LEFT : self.PATCH_LEFT + 16] = (
            np.dstack([patch] * channels)
        )
        return frame

    def _write_template(self, directory):
        path = Path(directory) / "match-template-probe.png"
        self.assertTrue(cv2.imwrite(str(path), self._probe_template()))
        return path.name

    def _make_spec(self, name, file_name):
        return TemplateSpec(
            name,
            file_name,
            threshold=0.9,
            reference_scale=self.REFERENCE_SCALE,
        )

    def test_match_template_grayscales_the_frame_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            file_name = self._write_template(directory)
            spec = self._make_spec("gray probe", file_name)
            frame = self._make_frame(3)
            gray_template = to_gray(self._probe_template())

            def loader(_template_dir, _spec):
                return gray_template, None

            converted_shapes = []
            original_to_gray = task_vision.image_utils.to_gray

            def counting_to_gray(image):
                converted_shapes.append(image.shape)
                return original_to_gray(image)

            with patch.object(task_vision.image_utils, "to_gray", counting_to_gray):
                result = task_vision.match_template(
                    frame,
                    spec,
                    {},
                    Path(directory),
                    loader=loader,
                )

            self.assertEqual([frame.shape], converted_shapes)
            self.assertGreater(result.score, 0.95)
            self.assertEqual(
                (self.PATCH_LEFT, self.PATCH_TOP),
                result.position,
            )
            self.assertEqual((16, 10), result.size)

    def test_match_template_accepts_bgra_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            file_name = self._write_template(directory)
            spec = self._make_spec("bgra probe", file_name)

            result = task_vision.match_template(
                self._make_frame(4),
                spec,
                {},
                Path(directory),
            )

            self.assertIsInstance(result, MatchResult)
            self.assertGreater(result.score, 0.95)
            self.assertEqual(
                (self.PATCH_LEFT, self.PATCH_TOP),
                result.position,
            )


if __name__ == "__main__":
    unittest.main()
