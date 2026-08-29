import unittest

from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT,
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    HOME_GACHA_OCR_REFERENCE_ROI,
    HOME_GACHA_OCR_RELATIVE_ROI,
    home_confirmation_passes,
    home_gacha_ocr_matches,
    home_temporary_announcement_detected,
)


class HomeConfirmationTest(unittest.TestCase):
    def test_shared_roi_matches_1920_by_1080_reference(self):
        self.assertEqual((110, 993, 95, 54), HOME_GACHA_OCR_REFERENCE_ROI)
        self.assertEqual(
            (110 / 1920, 993 / 1080, 205 / 1920, 1047 / 1080),
            HOME_GACHA_OCR_RELATIVE_ROI,
        )
        self.assertEqual((169, 615), HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT)
        self.assertEqual(
            (169 / 1920, 615 / 1080),
            HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
        )

    def test_ocr_match_normalizes_spacing(self):
        self.assertTrue(home_gacha_ocr_matches("抽 抽 乐"))
        self.assertTrue(home_gacha_ocr_matches("抽抽樂"))
        self.assertFalse(home_gacha_ocr_matches("启动游戏"))

    def test_confirmation_requires_all_three_signals(self):
        complete = {
            "button_found": True,
            "brightness_ratio": 0.8,
            "brightness_threshold": 0.75,
            "gacha_ocr_text": "抽抽乐",
        }
        self.assertTrue(home_confirmation_passes(**complete))

        for key, value in (
            ("button_found", False),
            ("brightness_ratio", 0.74),
            ("gacha_ocr_text", ""),
        ):
            signals = dict(complete)
            signals[key] = value
            self.assertFalse(home_confirmation_passes(**signals), key)

    def test_temporary_announcement_requires_only_brightness_to_fail(self):
        announcement = {
            "button_found": True,
            "brightness_ratio": 0.419,
            "brightness_threshold": 0.75,
            "gacha_ocr_text": "抽抽乐",
        }
        self.assertTrue(home_temporary_announcement_detected(**announcement))

        for key, value in (
            ("button_found", False),
            ("brightness_ratio", 0.75),
            ("gacha_ocr_text", ""),
        ):
            signals = dict(announcement)
            signals[key] = value
            self.assertFalse(home_temporary_announcement_detected(**signals), key)


if __name__ == "__main__":
    unittest.main()
