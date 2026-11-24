import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.tasks.BaseBD2Task import green_mask_from_template
from src.tasks.map_trade import action_icons, collector_constants, navigator_constants
from src.utils import task_vision
from src.utils.calibration import FHD_1080
from src.utils.image_utils import (
    crop_relative,
    masked_zncc,
    pixel_similarity,
    stabilize_template_match,
    to_gray,
)
from src.utils.ocr_utils import keyword_match_count
from src.utils.template_resolution import offline_template_scale, offline_template_search_region
from src.utils.vision_models import MatchResult, TemplateSpec


class TemplateRecognitionTest(unittest.TestCase):
    def test_masks_exclude_green_and_transparency_from_pixel_and_structure_scores(self):
        for pixels, expected in (
            ([[[0, 255, 0], [255, 255, 255]], [[1, 255, 0], [0, 254, 0]]], [[0, 255], [255, 255]]),
            (
                [[[10, 20, 30, 0], [0, 255, 0, 255]], [[10, 20, 30, 255], [255] * 4]],
                [[0, 0], [255, 255]],
            ),
        ):
            with self.subTest(channels=len(pixels[0][0])):
                template = np.array(pixels, np.uint8)
                mask = green_mask_from_template(template)
                np.testing.assert_array_equal(mask, expected)
                gray = to_gray(template)
                changed = gray.copy()
                changed[mask == 0] = 0
                self.assertEqual(1.0, pixel_similarity(changed, gray, mask))
                self.assertAlmostEqual(1.0, masked_zncc(changed, gray, mask))

        template = np.array([[0, 50], [100, 150]], np.uint8)
        for region, mask, expected in (
            (template + 30, None, 1),
            (150 - template, None, -1),
            (np.full_like(template, 50), None, -1),
            (template, np.zeros_like(template), -1),
            (template[:, :1], None, -1),
        ):
            with self.subTest(region=region.tolist(), expected=expected):
                self.assertAlmostEqual(expected, masked_zncc(region, template, mask))
        self.assertLess(pixel_similarity(template + 30, template), 0.89)
        self.assertEqual(-1, pixel_similarity(template[:, :1], template))

    def test_stabilization_favors_persistent_cluster_over_peak(self):
        def match(x, score):
            return SimpleNamespace(
                position=(x - 5, 95), size=(10, 10), score=score, pixel_score=score
            )

        shape = (1080, 1920, 3)
        samples = iter([match(100, 0.91), match(500, 0.99)] + [match(100, 0.91)] * 8)
        sleeps = []
        result, sampled_shape = stabilize_template_match(
            match(500, 0.99),
            shape,
            sample_match=lambda: (next(samples), shape),
            passes=lambda _: True,
            sleep=sleeps.append,
        )
        self.assertEqual((100, 100), result.center)
        self.assertEqual(9, result.hit_count)
        self.assertEqual(shape, sampled_shape)
        self.assertEqual([0.1] * 10, sleeps)

    def test_offline_assets_scale_and_restrict_the_search_region(self):
        for name, size, scale, region in (
            ("image/green/MainHomeIceGE.png", (1920, 1080), 1.25, (120, 117, 419, 326)),
            ("IMAGE/GREEN/mainBotmUnionAcGE.PNG", (1280, 720), 1.25 * 2 / 3, (80, 78, 279, 217)),
            ("image/green/UI_QuickPack_GE.png", (1920, 1080), 1.25, (0, 0, 1920, 1080)),
            ("image/pvp-medals.png", (1918, 1078), 1.25 * 1078 / 1080, (0, 0, 1918, 1078)),
            ("home.png", (1918, 1078), 1078 / 1080, (0, 0, 1918, 1078)),
            ("home.png", (1280, 720), 2 / 3, (0, 0, 1280, 720)),
        ):
            with self.subTest(asset=name, size=size):
                override = 1.22 if name.startswith("image/") else None
                self.assertAlmostEqual(
                    scale, offline_template_scale(name, *size, reference_scale=override)
                )
                self.assertEqual(region, offline_template_search_region(name, *size))

    def test_matching_grayscales_the_frame_once(self):
        pattern = np.fromfunction(lambda row, col: 20 + 4 * col + 7 * row, (10, 16)).astype(
            np.uint8
        )
        frame = np.full((48, 64, 3), 60, np.uint8)
        frame[20:30, 10:26] = pattern[:, :, None]
        spec = TemplateSpec("probe", "probe.png", threshold=0.9, reference_scale=30.0)
        with patch.object(task_vision.image_utils, "to_gray", wraps=to_gray) as convert:
            result = task_vision.match_template(
                frame, spec, {}, Path("."), loader=lambda *_: (pattern, None)
            )
        convert.assert_called_once_with(frame)
        self.assertGreater(result.score, 0.95)
        self.assertEqual(((10, 20), (16, 10)), (result.position, result.size))

    def test_skill_coordinates_share_single_source_of_truth(self):
        self.assertEqual(
            {
                1: (1671, 1011),
                2: (1749, 1011),
                3: (1824, 1011),
            },
            action_icons.SKILL_GROUP_CENTERS_REFERENCE,
        )
        self.assertIs(
            action_icons.SKILL_GROUP_CENTERS_REFERENCE,
            collector_constants.SKILL_GROUP_REFERENCE_POINTS,
        )
        self.assertEqual(
            action_icons.SKILL_GROUP_CENTERS_REFERENCE[1],
            navigator_constants.SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER,
        )
        self.assertEqual(
            action_icons.SKILL_GROUP_CENTERS_REFERENCE[2],
            navigator_constants.SANDBOX_SKILL_SLOT_2_REFERENCE_CENTER,
        )
        self.assertEqual(
            action_icons.ACTION_SLOT_CENTERS_REFERENCE["teleport"],
            navigator_constants.SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER,
        )
        self.assertEqual(
            (1671 / 1920, 1011 / 1080),
            navigator_constants.SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
        )
        self.assertEqual(
            (1795 / 1920, 788 / 1080),
            navigator_constants.SANDBOX_TELEPORT_SKILL_RELATIVE_POINT,
        )
        self.assertEqual(FHD_1080.width, action_icons.SKILL_REFERENCE_WIDTH)
        self.assertEqual(FHD_1080.height, action_icons.SKILL_REFERENCE_HEIGHT)
        self.assertEqual(FHD_1080.size, collector_constants.SKILL_REFERENCE_SIZE)
        self.assertEqual(FHD_1080.size, navigator_constants.AREA_MAP_REFERENCE_SIZE)


class OcrUtilsTest(unittest.TestCase):
    def test_task_ocr_boundaries_propagate_cancellation(self):
        from ok.task.exceptions import TaskDisabledException

        from src.tasks.BargainLevelTask import BargainLevelTask
        from src.tasks.FreeGachaTask import FreeGachaTask
        from src.tasks.map_trade.vision import Vision
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
            (AutoLoginTask._read_home_confirmation_signals, {"ocr_name": "登录主页"}),
            (
                AutoLoginTask._ocr_match_region_text,
                {
                    "name": "取消",
                    "result": MatchResult(1.0, (0, 0), (100, 100)),
                },
            ),
        )
        for method, kwargs in boundaries:
            with self.subTest(boundary=method.__qualname__):
                task = SimpleNamespace(
                    config={},
                    ocr=Mock(side_effect=TaskDisabledException()),
                    info_set=Mock(),
                    _normalize_text=str,
                    _match=Mock(),
                    _passes=Mock(),
                    _crop_relative=crop_relative,
                )
                task._quick_vision = lambda: Vision(task)
                task._home_p95_threshold = lambda: 185.0
                with self.assertRaises(TaskDisabledException):
                    method(task, frame, **kwargs)
                task.info_set.assert_not_called()

    def test_simplified_and_fuzzy_ocr_keywords(self):
        for text, words, options, count in (
            ("最近 剧情游戏卡", ("最近", "战斗玩法游戏卡带"), {}, 1),
            ("返回抽卡页靣", ("返回抽卡页面",), {"fuzzy_ratio": 0.9}, 1),
            ("取消 一键获得", ("一键获得", "取消"), {}, 2),
            ("取消 一鍵獲得", ("一键获得", "取消"), {}, 1),
            ("餐廳營業额現狀", ("餐厅营业额现状",), {}, 0),
            ("一键獲得", ("一键获得",), {}, 0),
        ):
            with self.subTest(text=text):
                self.assertEqual(count, keyword_match_count(text, words, **options))


class TemplateLoadingTest(unittest.TestCase):
    def _loaders(self, path):
        from src.tasks.BaseBD2Task import BaseBD2Task
        from src.tasks.QuickSuppressionTask import QuickSuppressionTask

        self.enterContext(patch("src.tasks.BaseBD2Task.TEMPLATE_DIR", path.parent))
        self.enterContext(
            patch(
                "src.tasks.BaseBD2Task.RECENT_PVP_CARTRIDGE_TEMPLATE_FILE",
                path.name,
            )
        )
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
