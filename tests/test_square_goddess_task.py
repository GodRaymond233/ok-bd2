import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.tasks.SquareGoddessTask import (
    FANTASIA_SQUARE_TEMPLATE,
    GODDESS_ALREADY_COMPLETE,
    GODDESS_NAVIGATION_CLICKED,
    QUICK_SWITCH_PAGE_PATTERNS,
    QUICK_SWITCH_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    SQUARE_CARTRIDGE_SLOT_POINT,
    SQUARE_HOME_POINT,
    SQUARE_NOTICE_TEMPLATE,
    SquareGoddessTask,
)
from src.utils.cartridge_quick_switch import (
    FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS,
    GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    LIFE_GAMEPLAY_CATEGORY_LABEL,
    LIFE_GAMEPLAY_CATEGORY_OCR_ROI,
    LIFE_GAMEPLAY_CATEGORY_POINT,
)


class SquareGoddessEntryTest(unittest.TestCase):
    def test_home_requires_keyword_votes_brightness_and_gacha_ocr(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {
            "主页确认等待秒数": 0.0,
            "主页压暗阈值": 185.0,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        bright_frame = np.full((1080, 1920, 3), 255, dtype=np.uint8)
        dimmed_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame = {"value": bright_frame}
        task.capture_frame = lambda: frame["value"]
        left_text = {"value": "我的小屋 格鲁TALK 街机游戏"}
        gacha_text = {"value": ""}

        def fake_ocr(_frame, name, roi=None, **_kwargs):
            if name == "主页左列":
                return left_text["value"]
            return gacha_text["value"]

        task._ocr_text = fake_ocr
        task._sleep_after_recognition = lambda: None
        announcement_clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: announcement_clicks.append(
            (x, y, after_sleep)
        )

        self.assertFalse(SquareGoddessTask._wait_for_cartridge_home(task))
        gacha_text["value"] = "抽抽乐"
        self.assertTrue(SquareGoddessTask._wait_for_cartridge_home(task))
        frame["value"] = dimmed_frame
        self.assertFalse(SquareGoddessTask._wait_for_cartridge_home(task))
        frame["value"] = bright_frame
        left_text["value"] = "我的小屋"
        self.assertFalse(SquareGoddessTask._wait_for_cartridge_home(task))
        self.assertEqual([(169 / 1920, 615 / 1080, 0.2)], announcement_clicks)

    def test_entry_uses_quick_switch_life_gameplay_and_fixed_second_slot(self):
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
        task._wait_for_life_gameplay_category = lambda: stages.append("highlight") or True
        task._wait_for_template = (
            lambda spec, **_kwargs: stages.append(("square", spec)) or True
        )

        self.assertTrue(SquareGoddessTask._enter_square_from_home(task))
        self.assertEqual(["home", ("quick", QUICK_SWITCH_TEMPLATE), "page"], stages[:3])
        self.assertEqual(
            [0.5, FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS],
            sleeps,
        )
        self.assertEqual(
            [
                (*LIFE_GAMEPLAY_CATEGORY_POINT, 0.0),
                (*SQUARE_CARTRIDGE_SLOT_POINT, 0.0),
            ],
            clicks,
        )
        self.assertIn("highlight", stages)
        self.assertIn(("square", FANTASIA_SQUARE_TEMPLATE), stages)

    def test_fixed_points_are_relative_to_1920_by_1080(self):
        self.assertEqual(
            (1126 / REFERENCE_WIDTH, 875 / REFERENCE_HEIGHT),
            LIFE_GAMEPLAY_CATEGORY_POINT,
        )
        self.assertEqual(
            (331 / REFERENCE_WIDTH, 970 / REFERENCE_HEIGHT),
            SQUARE_CARTRIDGE_SLOT_POINT,
        )

    def test_entry_stops_before_slot_when_life_category_is_not_confirmed(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.open_cartridge_quick_switcher = lambda **_kwargs: True
        task.sleep = lambda *_args, **_kwargs: None
        task._wait_for_life_gameplay_category = lambda: False
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append(
            (x, y, after_sleep)
        )
        task._wait_for_template = lambda *_args, **_kwargs: self.fail(
            "square slot must not be clicked before the life category is confirmed"
        )

        self.assertFalse(SquareGoddessTask._enter_square_from_home(task))
        self.assertEqual([(*LIFE_GAMEPLAY_CATEGORY_POINT, 0.0)], clicks)

    def test_quick_switch_uses_green_template_and_pixel_threshold(self):
        self.assertEqual(
            "image/green/QuickSwitchPlayIco.png",
            QUICK_SWITCH_TEMPLATE.file_name,
        )
        self.assertTrue(QUICK_SWITCH_TEMPLATE.green_mask)
        self.assertEqual(0.85, QUICK_SWITCH_TEMPLATE.min_pixel_score)
        self.assertEqual(0.88, QUICK_SWITCH_TEMPLATE.minimum_safe_threshold)
        # BUG-20260902-06：广场内暗色圆底按钮 1600x901 实测 zncc 最高 0.838，
        # 误检位置最高 0.43；0.78 在两者之间有足够余量。
        self.assertEqual(0.78, QUICK_SWITCH_TEMPLATE.min_zncc_score)
        self.assertIn(0.975, QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertNotIn(0.80, QUICK_SWITCH_TEMPLATE.scale_ratios)
        self.assertEqual(
            ((0.15, 0.85, 0.65, 1.0), (0.16, 0.08, 0.24, 0.19)),
            QUICK_SWITCH_TEMPLATE.relative_rois,
        )
        self.assertIsNone(QUICK_SWITCH_TEMPLATE.candidate_center_roi)

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
        text = {"value": "店长游戏卡 角色游戏卡 生活玩法游戏卡带"}
        task._ocr_text = lambda *_args, **_kwargs: text["value"]
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)

        self.assertFalse(SquareGoddessTask._wait_for_quick_switch_page(task))
        text["value"] += " 活动游戏卡"
        self.assertTrue(SquareGoddessTask._wait_for_quick_switch_page(task))
        self.assertEqual(
            ("店长游戏卡", "角色游戏卡", "生活玩法游戏卡带", "活动游戏卡"),
            QUICK_SWITCH_PAGE_PATTERNS,
        )
        self.assertEqual(2, sleeps.count(1.0))

    def test_life_gameplay_category_requires_ocr_and_visual_highlight(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"玩法类别高亮确认秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task._ocr_text = lambda *_args, **_kwargs: LIFE_GAMEPLAY_CATEGORY_LABEL
        frame = {"value": np.zeros((1080, 1920, 3), dtype=np.uint8)}
        task.capture_frame = lambda: frame["value"]

        self.assertFalse(SquareGoddessTask._wait_for_life_gameplay_category(task))

        left = round(LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[0] * REFERENCE_WIDTH)
        top = round(LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[1] * REFERENCE_HEIGHT)
        right = round(LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[2] * REFERENCE_WIDTH)
        bottom = round(LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[3] * REFERENCE_HEIGHT)
        frame["value"][top:bottom, left:right] = 255

        self.assertTrue(SquareGoddessTask._wait_for_life_gameplay_category(task))
        self.assertEqual((1025, 840, 204, 75), LIFE_GAMEPLAY_CATEGORY_OCR_ROI)
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
            lambda **_kwargs: stages.append("navigation") or GODDESS_NAVIGATION_CLICKED
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
                task._click_goddess_daily_navigation_until = (
                    lambda **_kwargs: GODDESS_NAVIGATION_CLICKED
                )
                task._wait_for_goddess_prayer_completion = lambda **_kwargs: True

                self.assertTrue(SquareGoddessTask._pray_at_goddess(task))
                self.assertEqual([3.0, 5.0], notice_calls)

    def test_missing_navigation_fails_even_after_dispatch_notice(self):
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

        self.assertFalse(SquareGoddessTask._pray_at_goddess(task))
        self.assertEqual(
            [("notice", 3.0), "navigation"],
            stages,
        )

    def test_explicit_completed_task_skips_navigation_and_prayer(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        messages = []
        task.log_info = lambda message, **_kwargs: messages.append(message)
        notice_calls = []
        task._click_square_notice_if_present = (
            lambda **kwargs: notice_calls.append(kwargs["timeout"]) or True
        )
        task._click_goddess_daily_navigation_until = (
            lambda **_kwargs: GODDESS_ALREADY_COMPLETE
        )
        task._wait_for_goddess_prayer_completion = (
            lambda **_kwargs: self.fail("No prayer wait for completed task")
        )

        self.assertTrue(SquareGoddessTask._pray_at_goddess(task))
        self.assertEqual([3.0], notice_calls)
        self.assertEqual(["广场女神像：已确认女神像许愿任务完成。"], messages)

    def test_successful_run_returns_home_after_prayer(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"启用": True}
        task.info_set = lambda *_args, **_kwargs: None
        notifications = []
        task.log_info = lambda message, notify=False: notifications.append(
            (message, notify)
        )
        stages = []
        task._enter_square_from_home = lambda: stages.append("enter") or True
        task._pray_at_goddess = lambda: stages.append("pray") or True
        task._return_home_from_square = lambda: stages.append("home") or True

        self.assertTrue(SquareGoddessTask.run(task))
        self.assertEqual(["enter", "pray", "home"], stages)
        self.assertEqual(
            [
                ("广场女神像：开始从主页进入梦幻广场。", False),
                ("广场女神像：许愿完成并返回主页。", True),
            ],
            notifications,
        )

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

    def test_square_return_home_retries_when_quick_switch_confirms_still_in_square(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"主页压暗阈值": 185.0}
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        logs = []
        task.log_info = logs.append
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.full((1080, 1920, 3), 255, dtype=np.uint8)
        ocr_texts = iter(("", "", "", "抽抽乐"))

        def fake_ocr(_frame, name, roi=None, **_kwargs):
            if name == "主页左列":
                return "我的小屋 格鲁TALK 街机游戏"
            return next(ocr_texts)

        task._ocr_text = fake_ocr
        task.clear_temporary_home_announcement_if_needed = (
            lambda **_kwargs: False
        )
        task._match = lambda _frame, _spec: MatchResult(
            score=0.95,
            pixel_score=0.90,
            position=(760, 960),
            size=(64, 60),
            zncc_score=0.90,
        )
        task._passes = lambda *_args, **_kwargs: True
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

    def test_square_return_home_does_not_retry_without_quick_switch_signal(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {"主页压暗阈值": 185.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.full((1080, 1920, 3), 255, dtype=np.uint8)

        def fake_ocr(_frame, name, roi=None, **_kwargs):
            if name == "主页左列":
                return "我的小屋 格鲁TALK 街机游戏"
            return ""

        task._ocr_text = fake_ocr
        task.clear_temporary_home_announcement_if_needed = (
            lambda **_kwargs: False
        )
        task._match = lambda _frame, _spec: MatchResult(
            score=0.50,
            pixel_score=0.40,
            position=(760, 960),
            size=(64, 60),
            zncc_score=0.30,
        )
        task._passes = lambda *_args, **_kwargs: False
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
        task.config = {"主页压暗阈值": 185.0}
        task.info_set = lambda *_args, **_kwargs: None
        logs = []
        task.log_info = logs.append
        task.sleep = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.full((1080, 1920, 3), 255, dtype=np.uint8)

        def fake_ocr(_frame, name, roi=None, **_kwargs):
            if name == "主页左列":
                return "我的小屋 格鲁TALK 街机游戏"
            return "输入"

        task._ocr_text = fake_ocr
        task.clear_temporary_home_announcement_if_needed = (
            lambda **_kwargs: False
        )
        task._match = lambda _frame, _spec: MatchResult(
            score=0.95,
            pixel_score=0.90,
            position=(760, 960),
            size=(64, 60),
            zncc_score=0.90,
        )
        task._passes = lambda *_args, **_kwargs: True
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

    def test_prayer_prefers_ocr_center_and_confirms_navigation_disappeared(self):
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

    def test_prayer_without_prompt_never_clicks_or_completes(self):
        task = object.__new__(SquareGoddessTask)
        task.config = {}
        task.info_set = lambda *args: None
        task.sleep = lambda *args: None
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        task._ocr_pattern_click_point = lambda *args, **kwargs: (None, "")
        task._click_client = lambda *args, **kwargs: self.fail("No prayer prompt")
        task.operate_click = lambda *args, **kwargs: self.fail("No fixed click")
        with patch("src.tasks.SquareGoddessTask.monotonic", side_effect=[0, 0, 2]):
            self.assertFalse(task._wait_for_goddess_prayer_completion(timeout=1))


if __name__ == "__main__":
    unittest.main()
