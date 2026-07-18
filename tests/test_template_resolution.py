import unittest
from pathlib import Path

from src.utils.template_resolution import (
    offline_template_reference_resolution,
    offline_template_requires_green_mask,
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
            offline_template_reference_resolution("image/green/BusinQuickIcoGE.png"),
        )

    def test_green_subfolder_marks_templates_for_masking(self):
        self.assertTrue(
            offline_template_requires_green_mask("image/green/BusinQuickIcoGE.png")
        )
        self.assertTrue(
            offline_template_requires_green_mask(
                Path(
                    "offline-train/train-source-screenshots/image/green/BusinQuickIcoGE.png"
                )
            )
        )
        self.assertFalse(offline_template_requires_green_mask("image/UI_loading_black.png"))
        self.assertFalse(offline_template_requires_green_mask("home.png"))
        self.assertEqual(
            (1280, 720),
            offline_template_reference_resolution(
                Path("offline-train/train-source-screenshots/image/UI_loading_black.png")
            ),
        )

    def test_root_template_uses_1920_by_1080_source_resolution(self):
        self.assertEqual(
            (1920, 1080),
            offline_template_reference_resolution("home.png"),
        )
        self.assertEqual(
            (1920, 1080),
            offline_template_reference_resolution(
                Path("offline-train/train-source-screenshots/home.png")
            ),
        )

    def test_scale_uses_calibrated_baseline_and_client_size(self):
        self.assertAlmostEqual(
            1.25 * (1078 / 1080),
            offline_template_scale("image/UI_loading_black.png", 1918, 1078),
        )
        self.assertAlmostEqual(
            1.25,
            offline_template_scale("image/green/BusinQuickIcoGE.png", 1920, 1080),
        )
        self.assertAlmostEqual(
            1078 / 1080,
            offline_template_scale("home.png", 1918, 1078),
        )
        self.assertAlmostEqual(
            720 / 1080,
            offline_template_scale("home.png", 1280, 720),
        )

    def test_image_scale_cannot_override_unified_baseline(self):
        self.assertAlmostEqual(
            1.25,
            offline_template_scale(
                "image/pvp-medals.png",
                1920,
                1080,
                reference_scale=1.22,
            ),
        )
        self.assertAlmostEqual(
            1.25 * (1078 / 1080),
            offline_template_scale(
                "image/pvp-medals.png",
                1918,
                1078,
                reference_scale=1.22,
            ),
        )


if __name__ == "__main__":
    unittest.main()
