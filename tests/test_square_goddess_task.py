import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.SquareGoddessTask import (
    FANTASIA_SQUARE_TEMPLATE,
    GAMEPLAY_CARTRIDGE_POINT,
    GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    QUICK_SWITCH_PAGE_PATTERNS,
    QUICK_SWITCH_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    SQUARE_CARTRIDGE_SLOT_POINT,
    SQUARE_DAILY_ICON_TEMPLATE,
    SQUARE_HOME_POINT,
    SQUARE_NOTICE_TEMPLATE,
    SquareGoddessTask,
    SquareMatchResult,
    SquareTemplateSpec,
)


class SquareGoddessEntryTest(unittest.TestCase):
    def test_entry_uses_quick_switch_gameplay_and_fixed_seventh_slot(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        stages = []

        task._wait_for_cartridge_home = lambda: stages.append("home") or True
        task._click_template_until = (
            lambda spec, **_kwargs: stages.append(("quick", spec)) or True
        )
        task._wait_for_quick_switch_page = lambda: stages.append("page") or True

        def open_quick_switcher(**callbacks):
            return (
                callbacks["ensure_home"]()
                and callbacks["click_quick_switch"]()
                and callbacks["confirm_quick_switch_page"]()
            )

        task.open_cartridge_quick_switcher = open_quick_switcher
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)
        clicks = []
        task.operate_click = (
            lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        )
        task._wait_for_gameplay_category = lambda: stages.append("highlight") or True
        task._wait_for_template = (
            lambda spec, **_kwargs: stages.append(("square", spec)) or True
        )

        self.assertTrue(SquareGoddessTask._enter_square_from_home(task))
        self.assertEqual(["home", ("quick", QUICK_SWITCH_TEMPLATE), "page"], stages[:3])
        self.assertEqual([0.5], sleeps)
        self.assertEqual(
            [
                (*GAMEPLAY_CARTRIDGE_POINT, 0.0),
                (*SQUARE_CARTRIDGE_SLOT_POINT, 0.0),
            ],
            clicks,
        )
        self.assertIn("highlight", stages)
        self.assertIn(("square", FANTASIA_SQUARE_TEMPLATE), stages)

    def test_fixed_points_are_relative_to_1920_by_1080(self):
        self.assertEqual(
            (989 / REFERENCE_WIDTH, 875 / REFERENCE_HEIGHT),
            GAMEPLAY_CARTRIDGE_POINT,
        )
        self.assertEqual(
            (1230 / REFERENCE_WIDTH, 970 / REFERENCE_HEIGHT),
            SQUARE_CARTRIDGE_SLOT_POINT,
        )

    def test_quick_switch_uses_green_template_and_pixel_threshold(self):
        self.assertEqual(
            "image/green/QuickCartGeadai.png",
            QUICK_SWITCH_TEMPLATE.file_name,
        )
        self.assertTrue(QUICK_SWITCH_TEMPLATE.green_mask)
        self.assertEqual(0.82, QUICK_SWITCH_TEMPLATE.min_pixel_score)
        self.assertEqual(0.90, QUICK_SWITCH_TEMPLATE.minimum_safe_threshold)
        self.assertIn(0.975, QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertNotIn(0.80, QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertIsNotNone(QUICK_SWITCH_TEMPLATE.candidate_center_roi)

    def test_quick_switch_click_uses_one_second_stable_center(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SquareMatchResult(
            score=0.95,
            pixel_score=0.90,
            position=(760, 960),
            size=(64, 60),
        )
        task._passes = lambda *_args, **_kwargs: True
        task._mf_offset_for_frame = lambda *_args: (0, 0)
        sleeps = []
        task.sleep = sleeps.append
        clicks = []
        task._click_client = lambda x, y, width, height, after_sleep=0.0: clicks.append(
            (x, y, width, height, after_sleep)
        )

        self.assertTrue(
            SquareGoddessTask._click_template_until(
                task,
                QUICK_SWITCH_TEMPLATE,
                timeout=0.01,
                name="快速切换按钮",
                stabilize=True,
            )
        )
        self.assertEqual([(792, 990, 1920, 1080, 0.0)], clicks)
        self.assertEqual(10, len(sleeps))
        self.assertTrue(all(seconds == 0.1 for seconds in sleeps))

    def test_masked_match_ignores_non_finite_scores_from_black_regions(self):
        task = object.__new__(SquareGoddessTask)
        task._match_pause_until = 0.0
        task._missing_template_names = set()
        task._match_error_names = set()
        task._load_template = lambda _spec: (
            np.ones((5, 5), dtype=np.uint8),
            np.full((5, 5), 255, dtype=np.uint8),
        )
        task._to_gray = lambda frame: frame
        task._roi_frame = lambda frame, _roi: (0, 0, frame)
        task._candidate_scales = lambda _base, _ratios: (1.0,)
        task._resize_template = lambda template, _scale: template
        task._resize_mask = lambda mask, _scale: mask
        task._pixel_similarity = lambda *_args: 0.8
        spec = SquareTemplateSpec(
            name="masked-test",
            file_name="masked-test.png",
            threshold_key="masked-test-threshold",
            default_threshold=0.7,
            roi=(0, 0, 6, 6),
        )
        response = np.array(
            [[np.inf, np.nan], [0.8, -np.inf]],
            dtype=np.float32,
        )

        with (
            patch(
                "src.tasks.SquareGoddessTask.offline_template_uses_main_region",
                return_value=False,
            ),
            patch(
                "src.tasks.SquareGoddessTask.offline_template_scale",
                return_value=1.0,
            ),
            patch("src.utils.image_utils.cv2.matchTemplate", return_value=response),
        ):
            result = SquareGoddessTask._match(
                task,
                np.zeros((6, 6), dtype=np.uint8),
                spec,
            )

        self.assertAlmostEqual(0.8, result.score)
        self.assertEqual((0, 1), result.position)
        self.assertTrue(np.isfinite(response).all())

    def test_quick_switch_page_requires_all_requested_labels_after_one_second(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"卡带选择页确认等待秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        text = {"value": "店长游戏卡 角色游戏卡 玩法游戏卡"}
        task._ocr_text = lambda *_args, **_kwargs: text["value"]
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)

        self.assertFalse(SquareGoddessTask._wait_for_quick_switch_page(task))
        text["value"] += " 活动游戏卡"
        self.assertTrue(SquareGoddessTask._wait_for_quick_switch_page(task))
        self.assertEqual(
            (r"店长游戏卡", r"角色游戏卡", r"玩法游戏卡", r"活动游戏卡"),
            QUICK_SWITCH_PAGE_PATTERNS,
        )
        self.assertEqual(2, sleeps.count(1.0))

    def test_gameplay_category_requires_ocr_and_visual_highlight(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"玩法类别高亮确认秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task._ocr_text = lambda *_args, **_kwargs: "玩法游戏卡"
        frame = {"value": np.zeros((1080, 1920, 3), dtype=np.uint8)}
        task.capture_frame = lambda: frame["value"]

        self.assertFalse(SquareGoddessTask._wait_for_gameplay_category(task))

        left = round(GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[0] * REFERENCE_WIDTH)
        top = round(GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[1] * REFERENCE_HEIGHT)
        right = round(GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[2] * REFERENCE_WIDTH)
        bottom = round(GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[3] * REFERENCE_HEIGHT)
        frame["value"][top:bottom, left:right] = 255

        self.assertTrue(SquareGoddessTask._wait_for_gameplay_category(task))
        self.assertEqual(0.05, GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO)

    def test_goddess_flow_checks_square_notice_before_pray_ocr(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"女神像许愿等待秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        stages = []
        task._click_square_notice_if_present = (
            lambda: stages.append("notice") or False
        )
        task._click_pray_until_gone = (
            lambda **_kwargs: stages.append("pray") or True
        )

        self.assertTrue(SquareGoddessTask._pray_at_goddess(task))
        self.assertEqual(["notice", "pray"], stages)

    def test_successful_run_returns_home_after_prayer(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"启用": True}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        stages = []
        task._enter_square_from_home = lambda: stages.append("enter") or True
        task._pray_at_goddess = lambda: stages.append("pray") or True
        task._return_home_from_square = lambda: stages.append("home") or True

        self.assertTrue(SquareGoddessTask.run(task))
        self.assertEqual(["enter", "pray", "home"], stages)

    def test_square_return_home_uses_relative_home_point_and_restores_timeout(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {
            "主页确认等待秒数": 10.0,
            "广场返回主页等待秒数": 15.0,
        }
        task.info_set = lambda *_args, **_kwargs: None
        clicks = []
        task.operate_click = lambda *args, **kwargs: clicks.append((args, kwargs))
        observed_timeouts = []
        task._wait_for_cartridge_home = (
            lambda **kwargs: observed_timeouts.append(kwargs["timeout"]) or True
        )

        self.assertTrue(SquareGoddessTask._return_home_from_square(task))
        self.assertEqual([((*SQUARE_HOME_POINT,), {"after_sleep": 1.0})], clicks)
        self.assertEqual([15.0], observed_timeouts)
        self.assertEqual(10.0, task.config["主页确认等待秒数"])

    def test_square_notice_uses_requested_region_and_clicks_match_center(self):
        self.assertEqual("image/green/tanhaoGE.png", SQUARE_NOTICE_TEMPLATE.file_name)
        self.assertEqual((1376, 862, 69, 51), SQUARE_NOTICE_TEMPLATE.roi)
        self.assertTrue(SQUARE_NOTICE_TEMPLATE.green_mask)
        self.assertEqual(0.72, SQUARE_NOTICE_TEMPLATE.min_pixel_score)

        task = object.__new__(SquareGoddessTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, spec: SquareMatchResult(
            score=0.90,
            pixel_score=0.90,
            position=(1380, 865),
            size=(60, 44),
        )
        task._passes = lambda _result, spec: spec is SQUARE_NOTICE_TEMPLATE
        clicks = []
        task._click_client = lambda *args, **kwargs: clicks.append((args, kwargs))

        self.assertTrue(SquareGoddessTask._click_square_notice_if_present(task))
        self.assertEqual(
            [((1410, 887, 1920, 1080), {"after_sleep": 1.0})],
            clicks,
        )

    def test_daily_navigation_icon_uses_requested_region(self):
        self.assertEqual("image/Square_DailyIco.png", SQUARE_DAILY_ICON_TEMPLATE.file_name)
        self.assertEqual((1548, 203, 26, 25), SQUARE_DAILY_ICON_TEMPLATE.roi)
        self.assertEqual(0.72, SQUARE_DAILY_ICON_TEMPLATE.min_pixel_score)


if __name__ == "__main__":
    unittest.main()
