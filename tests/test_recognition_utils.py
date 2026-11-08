import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.utils import image_utils, task_vision
from src.utils.image_utils import (
    StableMatchObservation,
    best_pixel_valid_match,
    crop_relative,
    independent_pixel_valid_matches,
    masked_zncc,
    pixel_similarity,
    reference_roi_frame,
    stabilize_template_match,
    stable_match_consensus,
    to_gray,
)
from src.utils.ocr_utils import (
    keyword_match_count,
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

    def test_resized_templates_preserve_pixels_after_input_and_output_changes(self):
        for dtype in (np.uint8, np.float32):
            source = np.arange(144, dtype=dtype).reshape(12, 12)
            for template in (source, source[::2, 1::2]):
                for scale in (0.75, 1.25, 2.5):
                    for interpolation in (cv2.INTER_AREA, cv2.INTER_CUBIC, cv2.INTER_NEAREST):
                        with self.subTest(shape=template.shape, scale=scale,
                                          interpolation=interpolation, dtype=dtype):
                            def resize():
                                return image_utils.resize_template_with_interpolation(
                                    template, scale, interpolation,
                                )

                            expected = cv2.resize(template, None, fx=scale, fy=scale,
                                                  interpolation=interpolation)
                            np.testing.assert_array_equal(resize(), expected)
                            resized = resize()
                            resized[:] = 0
                            np.testing.assert_array_equal(resize(), expected)
                            template[:] = template + 1
                            expected = cv2.resize(template, None, fx=scale, fy=scale,
                                                  interpolation=interpolation)
                            np.testing.assert_array_equal(resize(), expected)

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


class OcrUtilsTest(unittest.TestCase):
    def test_task_ocr_boundaries_propagate_cancellation(self):
        from ok.task.exceptions import TaskDisabledException

        from src.tasks.BargainLevelTask import BargainLevelTask
        from src.tasks.FreeGachaTask import FreeGachaTask
        from src.tasks.PVPTask import PVPTask
        from src.tasks.QuickSuppressionTask import QuickSuppressionTask
        from src.tasks.SquareGoddessTask import SquareGoddessTask
        from src.tasks.task_vision_mixin import TaskVisionMixin
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        boundaries = (
            (BargainLevelTask._ocr_text, {"name": "取消"}),
            (FreeGachaTask._gacha_ocr_boxes, {"name": "取消"}),
            (PVPTask._bd2_ocr_boxes, {"name": "取消"}),
            (QuickSuppressionTask._ocr_text, {"name": "取消"}),
            (SquareGoddessTask._bd2_ocr_boxes, {"name": "取消"}),
            (SquareGoddessTask._observe_goddess_navigation, {}),
            (TaskVisionMixin._ocr_text, {"name": "取消"}),
            (AutoLoginTask._login_page_ocr, {}),
            (AutoLoginTask._home_gacha_ocr_text, {}),
            (AutoLoginTask._ocr_match_region_text, {
                "name": "取消", "result": MatchResult(1.0, (0, 0), (100, 100)),
            }),
        )
        for method, kwargs in boundaries:
            with self.subTest(boundary=method.__qualname__):
                task = SimpleNamespace(
                    config={}, ocr=Mock(side_effect=TaskDisabledException()),
                    info_set=Mock(), _normalize_text=str, _match=Mock(), _passes=Mock(),
                    _crop_relative=crop_relative,
                )
                with self.assertRaises(TaskDisabledException):
                    method(task, frame, **kwargs)
                task.info_set.assert_not_called()

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

    def test_keyword_count_matches_simplified_only(self):
        # 2026-08-29 取消繁体识别：关键字按简体精确匹配，繁体/混简繁读数不再归一命中。
        self.assertEqual(
            keyword_match_count("取消 一键获得", ("一键获得", "取消")),
            2,
        )
        self.assertEqual(
            keyword_match_count("取消 一鍵獲得", ("一键获得", "取消")),
            1,
        )
        self.assertEqual(
            keyword_match_count("餐廳營業额現狀", ("餐厅营业额现状",)),
            0,
        )
        self.assertEqual(
            keyword_match_count("一键獲得", ("一键获得",)),
            0,
        )


class TemplateLoadingTest(unittest.TestCase):
    def _loaders(self, path):
        from src.tasks.BaseBD2Task import BaseBD2Task
        from src.tasks.QuickSuppressionTask import QuickSuppressionTask

        self.enterContext(patch("src.tasks.BaseBD2Task.TEMPLATE_DIR", path.parent))
        self.enterContext(patch(
            "src.tasks.BaseBD2Task.RECENT_PVP_CARTRIDGE_TEMPLATE_FILE", path.name,
        ))
        self.enterContext(patch("src.tasks.QuickSuppressionTask.TEMPLATE_PATH", path))
        recent_pvp = object.__new__(BaseBD2Task)
        loading = object.__new__(QuickSuppressionTask)
        loading._loading_template = None
        cache = {}
        spec = TemplateSpec("unicode", path.name)
        return (
            ("task_vision", lambda: task_vision.load_template(path.parent, spec, cache)),
            ("recent_pvp", recent_pvp._load_recent_pvp_cartridge_template),
            ("loading", loading._load_loading_template),
        )

    def test_unicode_template_paths_preserve_channels_and_cache(self):
        gray = np.arange(12, dtype=np.uint8).reshape(3, 4)
        alpha = np.full_like(gray, 255)
        alpha[0, 0] = 0
        bgra = np.dstack([gray, gray + 20, gray + 40, alpha])
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory) / "模板目录"
            template_dir.mkdir()
            path = template_dir / "模板.png"
            for raw in (gray, bgra):
                encoded_ok, encoded = cv2.imencode(".png", raw)
                self.assertTrue(encoded_ok)
                payload = encoded.tobytes()
                for name, load in self._loaders(path):
                    with self.subTest(loader=name, channels=raw.shape):
                        path.write_bytes(payload)
                        if name == "recent_pvp" and raw.ndim == 2:
                            with self.assertRaisesRegex(RuntimeError, "缺少 Alpha 通道"):
                                load()
                            continue
                        loaded = load()
                        template, mask = loaded if isinstance(loaded, tuple) else (loaded, None)
                        np.testing.assert_array_equal(template, to_gray(raw))
                        if raw.ndim == 3 and name != "loading":
                            np.testing.assert_array_equal(mask, alpha)
                        else:
                            self.assertIsNone(mask)
                        path.unlink()
                        self.assertIs(load(), loaded)

    def test_unreadable_template_files_keep_task_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "模板.png"
            for payload in (None, b"", b"invalid image"):
                for name, load in self._loaders(path):
                    with self.subTest(loader=name, payload=payload):
                        if payload is not None:
                            path.write_bytes(payload)
                        with self.assertRaisesRegex(RuntimeError, "模板不存在或无法读取"):
                            load()


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


if __name__ == "__main__":
    unittest.main()
