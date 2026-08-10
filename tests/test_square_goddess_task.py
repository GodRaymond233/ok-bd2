import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.tasks.SquareGoddessTask import (
    FANTASIA_SQUARE_TEMPLATE,
    GAMEPLAY_CARTRIDGE_POINT,
    GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    GODDESS_DAILY_REGION,
    GODDESS_NAVIGATION_MINIMUM_HITS,
    GODDESS_NAVIGATION_TARGET,
    GODDESS_PRAY_FALLBACK_POINT,
    QUICK_SWITCH_PAGE_PATTERNS,
    QUICK_SWITCH_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    SQUARE_CARTRIDGE_SLOT_POINT,
    SQUARE_DAILY_ICON_TEMPLATE,
    SQUARE_HOME_POINT,
    SQUARE_NOTICE_TEMPLATE,
    SquareGoddessTask,
)


class SquareGoddessEntryTest(unittest.TestCase):
    def test_home_requires_button_brightness_and_gacha_ocr(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {
            "主页确认等待秒数": 0.0,
            "主页亮度比例阈值": 0.75,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(0.9, (0, 0), (10, 10), pixel_score=0.9)
        task._match = lambda *_args, **_kwargs: match
        task._passes = lambda *_args, **_kwargs: True
        task._home_brightness_ratio = lambda _frame: 0.8
        gacha_text = {"value": ""}
        task._ocr_text = lambda *_args, **_kwargs: gacha_text["value"]
        task._sleep_after_recognition = lambda: None
        announcement_clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: announcement_clicks.append(
            (x, y, after_sleep)
        )

        self.assertFalse(SquareGoddessTask._wait_for_cartridge_home(task))
        gacha_text["value"] = "抽抽乐"
        self.assertTrue(SquareGoddessTask._wait_for_cartridge_home(task))
        task._home_brightness_ratio = lambda _frame: 0.74
        self.assertFalse(SquareGoddessTask._wait_for_cartridge_home(task))
        task._home_brightness_ratio = lambda _frame: 0.8
        task._passes = lambda *_args, **_kwargs: False
        self.assertFalse(SquareGoddessTask._wait_for_cartridge_home(task))
        self.assertEqual([(169 / 1920, 615 / 1080, 0.2)], announcement_clicks)

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
            "image/green/QuickSwitchPlayIco.png",
            QUICK_SWITCH_TEMPLATE.file_name,
        )
        self.assertTrue(QUICK_SWITCH_TEMPLATE.green_mask)
        self.assertEqual(0.85, QUICK_SWITCH_TEMPLATE.min_pixel_score)
        self.assertEqual(0.88, QUICK_SWITCH_TEMPLATE.minimum_safe_threshold)
        self.assertEqual(0.85, QUICK_SWITCH_TEMPLATE.min_zncc_score)
        self.assertIn(0.975, QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertNotIn(0.80, QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertIsNotNone(QUICK_SWITCH_TEMPLATE.candidate_center_roi)

    def test_quick_switch_click_uses_one_second_stable_center(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: MatchResult(
            score=0.95,
            pixel_score=0.90,
            position=(760, 960),
            size=(64, 60),
            zncc_score=0.90,
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
        task.config = {"masked-test-threshold": 0.7}
        task._match_pause_until = 0.0
        task._missing_template_names = set()
        task._match_error_names = set()
        task._templates = {}
        task._load_template = lambda _spec: (
            np.ones((5, 5), dtype=np.uint8),
            np.full((5, 5), 255, dtype=np.uint8),
        )
        spec = TemplateSpec(
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
                "src.utils.template_resolution.offline_template_uses_main_region",
                return_value=False,
            ),
            patch(
                "src.utils.template_resolution.offline_template_scale",
                return_value=1.0,
            ),
            patch(
                "src.utils.image_utils.reference_roi_frame",
                side_effect=lambda frame, _roi, _reference: (0, 0, frame),
            ),
            patch("src.utils.image_utils.candidate_scales", return_value=[1.0]),
            patch(
                "src.utils.image_utils.resize_template",
                side_effect=lambda template, _scale: template,
            ),
            patch(
                "src.utils.image_utils.resize_mask",
                side_effect=lambda mask, _scale: mask,
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

    def test_goddess_flow_uses_notice_joint_daily_signal_then_prayer(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        stages = []
        task._click_square_notice_if_present = lambda **kwargs: stages.append(
            ("notice", kwargs["timeout"])
        ) or False
        task._click_goddess_daily_navigation_until = (
            lambda **_kwargs: stages.append("navigation") or True
        )
        task._wait_for_goddess_prayer_completion = (
            lambda **_kwargs: stages.append("pray") or True
        )

        self.assertTrue(SquareGoddessTask._pray_at_goddess(task))
        self.assertEqual(
            [("notice", 3.0), "navigation", "pray", ("notice", 5.0)],
            stages,
        )

    def test_post_prayer_notice_hit_or_timeout_both_continue_to_home(self):
        for post_notice_found in (False, True):
            with self.subTest(post_notice_found=post_notice_found):
                task = object.__new__(SquareGoddessTask)
                task.config = {"祈祷完成后感叹号等待秒数": 5.0}
                task.info_set = lambda *_args, **_kwargs: None
                task.log_info = lambda *_args, **_kwargs: None
                notice_calls = []

                def click_notice(**kwargs):
                    notice_calls.append(kwargs["timeout"])
                    return len(notice_calls) == 2 and post_notice_found

                task._click_square_notice_if_present = click_notice
                task._click_goddess_daily_navigation_until = lambda **_kwargs: True
                task._wait_for_goddess_prayer_completion = lambda **_kwargs: True

                self.assertTrue(SquareGoddessTask._pray_at_goddess(task))
                self.assertEqual([3.0, 5.0], notice_calls)

    def test_missing_joint_daily_signal_is_treated_as_already_completed(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        stages = []
        task._click_square_notice_if_present = lambda **kwargs: stages.append(
            ("notice", kwargs["timeout"])
        ) or False
        task._click_goddess_daily_navigation_until = (
            lambda **_kwargs: stages.append("navigation") or False
        )
        task._wait_for_goddess_prayer_completion = (
            lambda **_kwargs: stages.append("pray") or True
        )

        self.assertTrue(SquareGoddessTask._pray_at_goddess(task))
        self.assertEqual(
            [("notice", 3.0), "navigation", ("notice", 5.0)],
            stages,
        )

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
            "广场返回主页最多点击次数": 3,
            "广场返回主页重试间隔秒数": 2.0,
        }
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        clicks = []
        task.operate_click = lambda *args, **kwargs: clicks.append((args, kwargs))
        observed_waits = []
        task._wait_for_cartridge_home = (
            lambda **kwargs: observed_waits.append(kwargs) or True
        )

        self.assertTrue(SquareGoddessTask._return_home_from_square(task))
        self.assertEqual([((*SQUARE_HOME_POINT,), {"after_sleep": 1.0})], clicks)
        self.assertEqual(
            [
                {
                    "timeout": 15.0,
                    "retry_home_clicks": 2,
                    "retry_interval": 2.0,
                    "total_home_clicks": 3,
                }
            ],
            observed_waits,
        )
        self.assertEqual("1/3", statuses["广场主页点击次数"])
        self.assertEqual(10.0, task.config["主页确认等待秒数"])

    def test_square_return_home_retries_when_chat_input_confirms_click_was_ignored(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"主页亮度比例阈值": 0.75}
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        logs = []
        task.log_info = logs.append
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(0.9, (0, 0), (10, 10), pixel_score=0.9)
        task._match = lambda *_args, **_kwargs: match
        task._passes = lambda *_args, **_kwargs: True
        task._home_brightness_ratio = lambda _frame: 0.8
        ocr_texts = iter(("输入", "抽抽乐"))
        task._ocr_text = lambda *_args, **_kwargs: next(ocr_texts)
        task.clear_temporary_home_announcement_if_needed = (
            lambda **_kwargs: False
        )
        clicks = []
        task.operate_click = lambda *args, **kwargs: clicks.append((args, kwargs))

        with patch(
            "src.tasks.SquareGoddessTask.monotonic",
            side_effect=[0.0] * 10,
        ):
            self.assertTrue(
                SquareGoddessTask._wait_for_cartridge_home(
                    task,
                    timeout=10.0,
                    retry_home_clicks=2,
                    retry_interval=0.0,
                    total_home_clicks=3,
                )
            )

        self.assertEqual(
            [((*SQUARE_HOME_POINT,), {"after_sleep": 1.0})],
            clicks,
        )
        self.assertEqual("2/3", statuses["广场主页点击次数"])
        self.assertTrue(any("执行第2次点击" in message for message in logs))

    def test_square_return_home_does_not_retry_without_square_chat_signal(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"主页亮度比例阈值": 0.75}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(0.9, (0, 0), (10, 10), pixel_score=0.9)
        task._match = lambda *_args, **_kwargs: match
        task._passes = lambda *_args, **_kwargs: True
        task._home_brightness_ratio = lambda _frame: 0.8
        task._ocr_text = lambda *_args, **_kwargs: ""
        task.clear_temporary_home_announcement_if_needed = (
            lambda **_kwargs: False
        )
        clicks = []
        task.operate_click = lambda *args, **kwargs: clicks.append((args, kwargs))
        clock = {"value": 0.0}

        def advance_clock():
            clock["value"] += 0.1
            return clock["value"]

        with patch(
            "src.tasks.SquareGoddessTask.monotonic",
            side_effect=advance_clock,
        ):
            self.assertFalse(
                SquareGoddessTask._wait_for_cartridge_home(
                    task,
                    timeout=0.5,
                    retry_home_clicks=2,
                    retry_interval=0.0,
                    total_home_clicks=3,
                )
            )

        self.assertEqual([], clicks)

    def test_square_return_home_stops_after_retry_budget_is_exhausted(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"主页亮度比例阈值": 0.75}
        task.info_set = lambda *_args, **_kwargs: None
        logs = []
        task.log_info = logs.append
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(0.9, (0, 0), (10, 10), pixel_score=0.9)
        task._match = lambda *_args, **_kwargs: match
        task._passes = lambda *_args, **_kwargs: True
        task._home_brightness_ratio = lambda _frame: 0.8
        task._ocr_text = lambda *_args, **_kwargs: "输入"
        task.clear_temporary_home_announcement_if_needed = (
            lambda **_kwargs: False
        )
        clicks = []
        task.operate_click = lambda *args, **kwargs: clicks.append((args, kwargs))
        clock = {"value": 0.0}

        def advance_clock():
            clock["value"] += 0.1
            return clock["value"]

        with patch(
            "src.tasks.SquareGoddessTask.monotonic",
            side_effect=advance_clock,
        ):
            self.assertFalse(
                SquareGoddessTask._wait_for_cartridge_home(
                    task,
                    timeout=1.0,
                    retry_home_clicks=2,
                    retry_interval=0.0,
                    total_home_clicks=3,
                )
            )

        self.assertEqual(
            [
                ((*SQUARE_HOME_POINT,), {"after_sleep": 1.0}),
                ((*SQUARE_HOME_POINT,), {"after_sleep": 1.0}),
            ],
            clicks,
        )
        self.assertEqual(1, sum("执行第2次点击" in message for message in logs))
        self.assertEqual(1, sum("执行第3次点击" in message for message in logs))

    def test_square_notice_uses_requested_region_and_clicks_match_center(self):
        self.assertEqual("image/green/tanhaoGE.png", SQUARE_NOTICE_TEMPLATE.file_name)
        self.assertEqual((1376, 862, 66, 51), SQUARE_NOTICE_TEMPLATE.roi)
        self.assertTrue(SQUARE_NOTICE_TEMPLATE.green_mask)
        self.assertEqual(0.72, SQUARE_NOTICE_TEMPLATE.min_pixel_score)

        task = object.__new__(SquareGoddessTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, spec: MatchResult(
            score=0.90,
            pixel_score=0.90,
            position=(1376, 862),
            size=(66, 51),
        )
        task._passes = lambda _result, spec: spec is SQUARE_NOTICE_TEMPLATE
        clicks = []
        task._click_client = lambda *args, **kwargs: clicks.append((args, kwargs))

        self.assertTrue(SquareGoddessTask._click_square_notice_if_present(task))
        self.assertEqual(
            [((1409, 887, 1920, 1080), {"after_sleep": 1.0})],
            clicks,
        )

    def test_daily_navigation_uses_requested_joint_region(self):
        self.assertEqual("image/Square_DailyIco.png", SQUARE_DAILY_ICON_TEMPLATE.file_name)
        self.assertEqual((1546, 199, 311, 63), GODDESS_DAILY_REGION)
        self.assertEqual(GODDESS_DAILY_REGION, SQUARE_DAILY_ICON_TEMPLATE.roi)
        self.assertEqual(0.72, SQUARE_DAILY_ICON_TEMPLATE.min_pixel_score)

        task = object.__new__(SquareGoddessTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: MatchResult(
            score=0.90,
            pixel_score=0.90,
            position=(1550, 203),
            size=(24, 24),
        )
        task._passes = lambda *_args, **_kwargs: True
        ocr_calls = []

        def find_navigation(_frame, name):
            ocr_calls.append((name, GODDESS_DAILY_REGION))
            return (1700, 230), "移动至艾力克史温女"

        task._goddess_navigation_click_point = find_navigation
        clicks = []
        task._click_client = lambda *args, **kwargs: clicks.append((args, kwargs))

        self.assertTrue(
            SquareGoddessTask._click_goddess_daily_navigation_until(
                task,
                timeout=0.1,
            )
        )
        self.assertEqual([("广场导航文本", GODDESS_DAILY_REGION)], ocr_calls)
        self.assertEqual(
            [((1700, 230, 1920, 1080), {"after_sleep": 2.0})],
            clicks,
        )

    def test_navigation_ocr_clicks_only_the_matching_text_union_center(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        task.info_set = lambda *_args, **_kwargs: None
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="每日奖励", x=0, y=0, width=60, height=18),
            SimpleNamespace(name="移动至", x=40, y=20, width=50, height=20),
            SimpleNamespace(name="艾力克史温女", x=90, y=20, width=100, height=20),
        ]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        point, text = SquareGoddessTask._goddess_navigation_click_point(
            task,
            frame,
            name="广场导航文本",
        )

        self.assertEqual((1661, 229), point)
        self.assertIn("每日奖励", text)
        self.assertIn("移动至", text)

    def test_navigation_ocr_accepts_name_missing_li_and_trailing_characters(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        task.info_set = lambda *_args, **_kwargs: None
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="每日奖励", x=0, y=0, width=60, height=18),
            SimpleNamespace(name="移动至艾克史温", x=40, y=20, width=150, height=20),
        ]
        frame = np.zeros((1079, 1918, 3), dtype=np.uint8)

        point, text = SquareGoddessTask._goddess_navigation_click_point(
            task,
            frame,
            name="广场导航文本",
        )

        self.assertEqual((1659, 229), point)
        self.assertIn("移动至艾克史温", text)

    def test_navigation_ocr_accepts_exactly_seven_target_characters(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        task.info_set = lambda *_args, **_kwargs: None
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="移动至艾力克史", x=40, y=20, width=140, height=20),
        ]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        point, _text = SquareGoddessTask._goddess_navigation_click_point(
            task,
            frame,
            name="广场导航文本",
        )

        self.assertEqual((1656, 229), point)

    def test_navigation_ocr_accepts_six_of_nine_target_characters(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="移动至艾力克", x=40, y=20, width=120, height=20),
        ]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        point, _text = SquareGoddessTask._goddess_navigation_click_point(
            task,
            frame,
            name="广场导航文本",
        )

        self.assertEqual("移动至艾力克史温女", GODDESS_NAVIGATION_TARGET)
        self.assertEqual(6, GODDESS_NAVIGATION_MINIMUM_HITS)
        self.assertEqual("6/9", statuses["广场导航文字命中"])
        self.assertEqual((1646, 229), point)

    def test_navigation_ocr_accepts_traditional_wen_from_live_log(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(
                name="每日奖励 E 移动至艾力克史溫女 +",
                x=0,
                y=0,
                width=240,
                height=20,
            ),
        ]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        point, text = SquareGoddessTask._goddess_navigation_click_point(
            task,
            frame,
            name="广场导航文本",
        )

        self.assertEqual("8/9", statuses["广场导航文字命中"])
        self.assertEqual((1666, 209), point)
        self.assertIn("艾力克史溫女", text)

    def test_navigation_ocr_rejects_only_five_target_characters(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="移动至艾力", x=40, y=20, width=100, height=20),
        ]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        point, _text = SquareGoddessTask._goddess_navigation_click_point(
            task,
            frame,
            name="广场导航文本",
        )

        self.assertEqual("5/9", statuses["广场导航文字命中"])
        self.assertIsNone(point)

    def test_navigation_ocr_rejects_another_destination(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        task.info_set = lambda *_args, **_kwargs: None
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="移动至其他任务", x=40, y=20, width=150, height=20),
        ]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        point, text = SquareGoddessTask._goddess_navigation_click_point(
            task,
            frame,
            name="广场导航文本",
        )

        self.assertIsNone(point)
        self.assertEqual("移动至其他任务", text)

    def test_navigation_ocr_roi_scales_position_and_size_with_client(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"广场 OCR 阈值": 0.2}
        task.info_set = lambda *_args, **_kwargs: None
        observed_shapes = []
        task.ocr = lambda **kwargs: observed_shapes.append(kwargs["frame"].shape) or []
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        left, top, crop = SquareGoddessTask._roi_frame(
            frame,
            GODDESS_DAILY_REGION,
        )
        SquareGoddessTask._ocr_boxes(
            task,
            frame,
            name="广场导航文本",
            roi=GODDESS_DAILY_REGION,
        )

        self.assertEqual((1031, 133), (left, top))
        self.assertEqual((42, 207, 3), crop.shape)
        self.assertEqual([(42, 207, 3)], observed_shapes)

    def test_prayer_prefers_ocr_center_and_confirms_navigation_disappeared(self):
        self.assertEqual(
            (1412 / REFERENCE_WIDTH, 884 / REFERENCE_HEIGHT),
            GODDESS_PRAY_FALLBACK_POINT,
        )

        task = object.__new__(SquareGoddessTask)
        task.config = {
            "女神像许愿等待秒数": 8.0,
            "女神像许愿最多点击次数": 3,
            "女神像完成确认等待秒数": 8.0,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._ocr_pattern_click_point = (
            lambda _frame, _patterns, name, roi: (
                ((1410, 880), "向女神像许愿") if name == "女神像许愿" else (None, "")
            )
        )
        clicks = []
        task._click_client = lambda *args, **kwargs: clicks.append((args, kwargs))
        task.operate_click = lambda *_args, **_kwargs: self.fail("不应使用固定点")
        task._wait_for_daily_navigation_to_disappear = (
            lambda **_kwargs: True
        )

        self.assertTrue(
            SquareGoddessTask._wait_for_goddess_prayer_completion(
                task,
                timeout=0.1,
            )
        )
        self.assertEqual(
            [((1410, 880, 1920, 1080), {"after_sleep": 2.0})],
            clicks,
        )

    def test_prayer_uses_relative_fallback_when_ocr_is_not_found(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {
            "女神像许愿等待秒数": 0.5,
            "女神像许愿最多点击次数": 1,
            "女神像完成确认等待秒数": 8.0,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._ocr_pattern_click_point = (
            lambda _frame, _patterns, name, roi: (None, "")
        )
        task._click_client = lambda *_args, **_kwargs: self.fail("没有 OCR 中心可点击")
        clicks = []
        task.operate_click = lambda *args, **kwargs: clicks.append((args, kwargs))
        task._wait_for_daily_navigation_to_disappear = lambda **_kwargs: True

        with patch(
            "src.tasks.SquareGoddessTask.monotonic",
            side_effect=(0.0, 0.0, 0.5, 0.5),
        ):
            self.assertTrue(
                SquareGoddessTask._wait_for_goddess_prayer_completion(
                    task,
                    timeout=10.0,
                )
            )

        self.assertEqual(
            [((*GODDESS_PRAY_FALLBACK_POINT,), {"after_sleep": 2.0})],
            clicks,
        )


if __name__ == "__main__":
    unittest.main()
