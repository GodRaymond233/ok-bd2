import unittest

import numpy as np

from src.utils.image_utils import (
    candidate_scales,
    crop_relative,
    pixel_similarity,
    reference_roi_frame,
    resize_mask,
    to_gray,
)
from src.utils.ocr_utils import (
    fuzzy_substring_match,
    keyword_match_count,
    normalize_ocr_text,
)


class ImageRecognitionUtilsTest(unittest.TestCase):
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
    def test_normalize_can_keep_regex_or_strip_to_alphanumeric(self):
        self.assertEqual(normalize_ocr_text(" P V P .* 确认 "), "pvp.*确认")
        self.assertEqual(normalize_ocr_text("折扣-商店！", alnum_only=True), "折扣商店")

    def test_keyword_count_supports_exact_and_fuzzy_matching(self):
        self.assertEqual(keyword_match_count("最近 剧情游戏卡", ("最近", "玩法游戏卡")), 1)
        self.assertEqual(
            keyword_match_count("返回抽卡页靣", ("返回抽卡页面",), fuzzy_ratio=0.9),
            1,
        )

    def test_fuzzy_match_rejects_empty_values(self):
        self.assertFalse(fuzzy_substring_match("", "确认", 0.9))
        self.assertFalse(fuzzy_substring_match("确认", "", 0.9))


if __name__ == "__main__":
    unittest.main()
