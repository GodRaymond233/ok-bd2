import unittest

from src.utils.home_confirmation import (
    HOME_DIMMED_P95_THRESHOLD_DEFAULT,
    HOME_GACHA_OCR_SCALES,
    HOME_LEFT_COLUMN_KEYWORD_GROUPS,
    HOME_LEFT_COLUMN_REQUIRED_HITS,
    home_confirmation_passes,
    home_gacha_ocr_with_fallback,
    home_left_column_hits,
    home_temporary_announcement_detected,
)


def _passing_kwargs(**overrides):
    kwargs = {
        "left_hits": HOME_LEFT_COLUMN_REQUIRED_HITS,
        "required_left_hits": HOME_LEFT_COLUMN_REQUIRED_HITS,
        "brightness": 253.0,
        "brightness_threshold": HOME_DIMMED_P95_THRESHOLD_DEFAULT,
        "gacha_ocr_text": "抽抽乐",
    }
    kwargs.update(overrides)
    return kwargs


class HomeConfirmationTest(unittest.TestCase):

    def test_gacha_ocr_uses_bounded_upscale_fallback(self):
        calls = []

        def read_text(scale):
            calls.append(scale)
            return {1.0: "", 2.0: "抽 抽 乐", 3.0: "不应调用"}[scale]

        result = home_gacha_ocr_with_fallback(read_text)

        self.assertEqual((1.0, 2.0, 3.0), HOME_GACHA_OCR_SCALES)
        self.assertTrue(result.matched)
        self.assertEqual(2.0, result.selected_scale)
        self.assertEqual("抽 抽 乐", result.text)
        self.assertEqual([1.0, 2.0], calls)
        self.assertEqual("采用x2; x1=-, x2=抽 抽 乐", result.trace)

    def test_gacha_ocr_failure_reports_all_attempts(self):
        result = home_gacha_ocr_with_fallback(
            lambda scale: "其他文字" if scale == 1.0 else ""
        )

        self.assertFalse(result.matched)
        self.assertIsNone(result.selected_scale)
        self.assertEqual("其他文字", result.text)
        self.assertEqual(
            ((1.0, "其他文字"), (2.0, ""), (3.0, "")),
            result.attempts,
        )

    def test_left_column_hits_counts_keyword_groups(self):
        self.assertEqual(
            3,
            home_left_column_hits("我的小屋 经营管理格鲁TALK 街机游戏"),
        )
        # 粘框子串命中计 1 票；繁体读数不再命中（2026-08-29 取消繁体识别）。
        self.assertEqual(1, home_left_column_hits("经营管理格鲁TALK 街機遊戲"))
        self.assertEqual(1, home_left_column_hits("我的小屋"))
        self.assertEqual(0, home_left_column_hits("设置 公告"))
        self.assertEqual(len(HOME_LEFT_COLUMN_KEYWORD_GROUPS), 3)
        self.assertEqual(2, HOME_LEFT_COLUMN_REQUIRED_HITS)

    def test_confirmation_requires_all_three_signals(self):
        self.assertTrue(home_confirmation_passes(**_passing_kwargs()))

        for key, value in (
            ("left_hits", HOME_LEFT_COLUMN_REQUIRED_HITS - 1),
            ("brightness", HOME_DIMMED_P95_THRESHOLD_DEFAULT - 1),
            ("gacha_ocr_text", ""),
        ):
            signals = _passing_kwargs(**{key: value})
            self.assertFalse(home_confirmation_passes(**signals), key)

    def test_legacy_template_anchor_bridge_requires_one_vote(self):
        signals = _passing_kwargs(
            left_hits=1,
            required_left_hits=1,
            brightness=0.8,
            brightness_threshold=0.75,
        )
        self.assertTrue(home_confirmation_passes(**signals))
        self.assertFalse(
            home_confirmation_passes(**_passing_kwargs(left_hits=0, required_left_hits=1))
        )

    def test_temporary_announcement_requires_only_brightness_to_fail(self):
        announcement = _passing_kwargs(brightness=126.0)
        self.assertTrue(home_temporary_announcement_detected(**announcement))

        for key, value in (
            ("left_hits", HOME_LEFT_COLUMN_REQUIRED_HITS - 1),
            ("brightness", HOME_DIMMED_P95_THRESHOLD_DEFAULT),
            ("gacha_ocr_text", ""),
        ):
            signals = _passing_kwargs(**{key: value})
            self.assertFalse(home_temporary_announcement_detected(**signals), key)


if __name__ == "__main__":
    unittest.main()
