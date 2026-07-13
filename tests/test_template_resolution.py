import unittest
from pathlib import Path

from src.utils.template_resolution import (
    offline_template_reference_resolution,
    offline_template_scale,
)


class OfflineTemplateResolutionTest(unittest.TestCase):
    def test_image_subfolder_uses_1280_by_720_source_resolution(self):
        self.assertEqual(
            (1280, 720),
            offline_template_reference_resolution("image/UI_loading_black.png"),
        )
        self.assertEqual(
            (1280, 720),
            offline_template_reference_resolution(
                Path("offline-train/train-source-screenshots/image/UI_loading_black.png")
            ),
        )

    def test_root_template_uses_1920_by_1080_source_resolution(self):
        self.assertEqual(
            (1920, 1080),
            offline_template_reference_resolution("loading.png"),
        )
        self.assertEqual(
            (1920, 1080),
            offline_template_reference_resolution(
                Path("offline-train/train-source-screenshots/loading.png")
            ),
        )

    def test_scale_uses_captured_client_width_and_height(self):
        self.assertAlmostEqual(
            1078 / 720,
            offline_template_scale("image/UI_loading_black.png", 1918, 1078),
        )
        self.assertAlmostEqual(
            1078 / 1080,
            offline_template_scale("loading.png", 1918, 1078),
        )


if __name__ == "__main__":
    unittest.main()
