import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.tasks.DailyTask import (
    GUILD_FINISHED_TEMPLATE,
    GUILD_MAIN_ACTIVE_TEMPLATE,
    GUILD_MAIN_FINISHED_TEMPLATE,
    GUILD_SIGNUP_SUCCESS_TEMPLATE,
    GUILD_SUCCESS_KEYWORDS,
    GUILD_TEMPLATE,
    HOME_ICE_TEMPLATE,
    HOME_RICE_TEMPLATE,
    LOADING_TEMPLATE,
    MY_HOME_TEMPLATE,
    QUICK_HUNT_ADVENTURE_MAP_PATTERNS,
    QUICK_HUNT_ADVENTURE_POINTS,
    QUICK_HUNT_BUTTON_ROI,
    QUICK_HUNT_COUNT_ROI,
    QUICK_HUNT_CRYSTAL_POINT,
    QUICK_HUNT_CRYSTAL_TITLE_ROI,
    QUICK_HUNT_DIALOG_ROI,
    QUICK_HUNT_DOUBLE_ROI,
    QUICK_HUNT_DOUBLE_TEMPLATE,
    QUICK_HUNT_ENTRY_POINT,
    QUICK_HUNT_MAP_SCAN_ROI,
    QUICK_HUNT_RESOURCE_ROI,
    QUICK_HUNT_RETURN_POINT,
    QUICK_HUNT_REWARD_ROI,
    QUICK_HUNT_START_ROI,
    QUICK_HUNT_STONE_COUNT_ROI,
    QUICK_HUNT_STONE_LIST_ROI,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    DailyMatchResult,
    DailyTask,
)
from src.tasks.QuickHuntTask import QuickHuntTask


class DailyTaskHelperTest(unittest.TestCase):
    def test_daily_task_is_renamed_and_has_no_quick_hunt_configuration(self):
        task = object.__new__(DailyTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch("src.tasks.DailyTask.BaseBD2Task.__init__", return_value=None):
            DailyTask.__init__(task)

        self.assertEqual("公会、小屋、酒馆", task.name)
        self.assertNotIn("执行快速狩猎", task.default_config)
        self.assertNotIn("快速狩猎冒险航线", task.default_config)
        self.assertNotIn("执行快速狩猎", task.status_keys)

    def test_quick_hunt_config_exposes_safe_and_consuming_test_buttons(self):
        task = object.__new__(QuickHuntTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch("src.tasks.DailyTask.BaseBD2Task.__init__", return_value=None):
            QuickHuntTask.__init__(task)

        self.assertEqual("快速狩猎", task.name)
        self.assertNotIn("执行公会签到", task.default_config)
        self.assertNotIn("执行快速狩猎", task.default_config)
        self.assertNotIn("快速狩猎圣石属性", task.default_config)
        self.assertIn("快速狩猎 OCR 阈值", task.default_config)

        test_keys = (
            "快速狩猎入口测试",
            "快速狩猎菜单测试",
            "快速狩猎圣石测试",
            "快速狩猎完整测试",
        )
        visible_keys = task.config_type["启用"]["sub_configs"][True]
        for key in test_keys:
            with self.subTest(key=key):
                self.assertIn(key, visible_keys)
                self.assertEqual("button", task.config_type[key]["type"])

        entry_buttons = task.config_type["快速狩猎入口测试"]["buttons"]
        menu_buttons = task.config_type["快速狩猎菜单测试"]["buttons"]
        stone_buttons = task.config_type["快速狩猎圣石测试"]["buttons"]
        full_button = task.config_type["快速狩猎完整测试"]
        self.assertEqual(["只读检查入口", "打开狩猎菜单"], [b["text"] for b in entry_buttons])
        self.assertEqual(["只读检查菜单", "执行米饭(消耗)"], [b["text"] for b in menu_buttons])
        self.assertEqual(["执行圣石(消耗)", "返回主页"], [b["text"] for b in stone_buttons])
        self.assertEqual("完整执行(消耗)", full_button["text"])
        for button in (*entry_buttons, *menu_buttons, *stone_buttons, full_button):
            self.assertTrue(callable(button["callback"]))

    def test_quick_hunt_button_queues_selected_test_action(self):
        task = object.__new__(QuickHuntTask)
        task._enabled = False
        task.running = False
        task._quick_hunt_test_action = None
        starts = []
        task.start = lambda: starts.append("start")
        task.info_set = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task.log_error = lambda *_args, **_kwargs: None

        task._queue_quick_hunt_test("inspect_entry")

        self.assertEqual("inspect_entry", task._quick_hunt_test_action)
        self.assertEqual(["start"], starts)

    def test_quick_hunt_run_dispatches_pending_test_only(self):
        task = object.__new__(QuickHuntTask)
        task._quick_hunt_test_action = "rice"
        task.config = {"启用": True}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_error = lambda *_args, **_kwargs: None
        calls = []
        task._quick_hunt_run_rice_scheduler = lambda: calls.append("rice") or True
        task.run_quick_hunt = lambda: self.fail("normal daily run must not start")

        self.assertTrue(task.run())
        self.assertEqual(["rice"], calls)
        self.assertIsNone(task._quick_hunt_test_action)

    def test_quick_hunt_entry_inspection_does_not_click(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎模板阈值": 0.78,
            "快速狩猎像素相似度阈值": 0.72,
            "主页亮度比例阈值": 0.75,
        }
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        task._quick_hunt_home_signals = lambda _frame: (
            True,
            DailyMatchResult(0.9, 0.85, (0, 0), (10, 10)),
            HOME_RICE_TEMPLATE,
            0.8,
            "抽抽乐",
        )
        task._quick_hunt_entry_red_state = lambda _frame: (
            True,
            (1188, 158),
            (0, 0, 255),
            (0, 255, 255),
        )
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "read-only inspection must not click"
        )

        self.assertTrue(task._quick_hunt_inspect_entry())
        self.assertIn("通过", statuses["快速狩猎首页按钮"])
        self.assertIn("point=(1188, 158)", statuses["快速狩猎红点识别"])
        self.assertIn("红色", statuses["快速狩猎红点识别"])
        self.assertEqual("0.800/0.750", statuses["快速狩猎主页亮度"])
        self.assertEqual("抽抽乐", statuses["快速狩猎主页抽抽乐 OCR"])

    def test_quick_hunt_menu_inspection_reports_ocr_and_templates_without_clicking(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎模板阈值": 0.78,
            "快速狩猎像素相似度阈值": 0.72,
            "快速狩猎章节图": "低练度·章节1",
        }
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        ocr_calls = []

        class FakeVision:
            def ocr_text(self, _frame, name, relative_roi=None):
                ocr_calls.append((name, relative_roi))
                return name

            def match(self, _frame, _spec):
                return SimpleNamespace(score=0.9, pixel_score=0.85)

            def passes(self, _match, _spec):
                return True

            def threshold_for(self, _spec):
                return 0.78

            def match_all(self, _frame, _spec, minimum_score):
                self.minimum_score = minimum_score
                return ()

            def click_client(self, *_args, **_kwargs):
                raise AssertionError("read-only inspection must not click")

            def click_template(self, *_args, **_kwargs):
                raise AssertionError("read-only inspection must not click")

        task._quick_vision = lambda: FakeVision()

        self.assertTrue(task._quick_hunt_inspect_menu())
        self.assertEqual(11, len(ocr_calls))
        self.assertEqual("测试-菜单标题", statuses["快速狩猎菜单 OCR"])
        self.assertIn("通过", statuses["快速狩猎收起模板"])
        self.assertIn("金币=非双倍", statuses["快速狩猎双倍识别"])

    def test_keyword_match_count_ignores_spaces_and_case(self):
        text = "签到 成功\n奖励已发放至邮箱"

        self.assertEqual(
            2,
            DailyTask._keyword_match_count(
                text,
                ["签到成功", "奖励已发放至邮箱", "不存在"],
            ),
        )

    def test_reference_click_uses_1920_by_1080_ratios(self):
        task = object.__new__(DailyTask)
        calls = {}

        def fake_operate_click(x, y, after_sleep=0):
            calls["x"] = x
            calls["y"] = y
            calls["after_sleep"] = after_sleep

        task.operate_click = fake_operate_click

        task._click_reference(960, 540, after_sleep=0.5)

        self.assertEqual(960 / REFERENCE_WIDTH, calls["x"])
        self.assertEqual(540 / REFERENCE_HEIGHT, calls["y"])
        self.assertEqual(0.5, calls["after_sleep"])

    def test_crop_relative_uses_fractional_bounds(self):
        image = np.arange(100).reshape((10, 10))

        crop = DailyTask._crop_relative(image, (0.2, 0.3, 0.6, 0.8))

        np.testing.assert_array_equal(crop, image[3:8, 2:6])

    def test_guild_sign_in_does_not_click_without_guild_trigger(self):
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._match = lambda _frame, _spec: DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))
        task._click_reference = lambda *_args, **_kwargs: self.fail("should not click")

        self.assertFalse(DailyTask.run_guild_sign_in(task))

    def test_guild_finished_template_still_enters_guild(self):
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []

        def fake_match(_frame, spec):
            if spec is GUILD_FINISHED_TEMPLATE:
                return DailyMatchResult(0.9, 0.9, (0, 0), (1, 1))
            if spec is GUILD_TEMPLATE:
                return DailyMatchResult(0.7, 0.7, (0, 0), (1, 1))
            return DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_home_brightness = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_guild_sign_in(task))
        self.assertEqual([(370, 155), (100, 50)], clicks)

    def test_guild_sign_in_continues_when_loading_is_missing(self):
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []

        def fake_match(_frame, spec):
            if spec is GUILD_TEMPLATE:
                return DailyMatchResult(0.9, 0.9, (0, 0), (1, 1))
            return DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_home_brightness = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_guild_sign_in(task))
        self.assertEqual([(370, 155), (100, 50)], clicks)

    def test_guild_sign_in_waits_before_clicking_success_prompt(self):
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        sleeps = []

        def fake_match(_frame, spec):
            if spec is GUILD_TEMPLATE:
                return DailyMatchResult(0.9, 0.9, (0, 0), (1, 1))
            return DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task.sleep = lambda seconds: sleeps.append(seconds)
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: (
            "target",
            True,
            "签到成功",
        )
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: self.fail(
            "success already found"
        )
        task._wait_home_brightness = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_guild_sign_in(task))
        self.assertEqual([1.0, 1.0], sleeps)
        self.assertEqual([(370, 155), (450, 650), (100, 50)], clicks)

    def test_guild_sign_in_accepts_main_active_template(self):
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []

        def fake_match(_frame, spec):
            if spec is GUILD_MAIN_ACTIVE_TEMPLATE:
                return DailyMatchResult(0.9, 0.9, (0, 0), (1, 1))
            return DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_home_brightness = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_guild_sign_in(task))
        self.assertEqual([(370, 155), (100, 50)], clicks)

    def test_guild_entry_uses_best_template_without_finished_skip(self):
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []

        def fake_match(_frame, spec):
            if spec is GUILD_MAIN_ACTIVE_TEMPLATE:
                return DailyMatchResult(0.967, 0.967, (0, 0), (1, 1))
            if spec is GUILD_MAIN_FINISHED_TEMPLATE:
                return DailyMatchResult(0.978, 0.978, (0, 0), (1, 1))
            return DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_home_brightness = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_guild_sign_in(task))
        self.assertEqual([(370, 155), (100, 50)], clicks)

    def test_new_main_templates_use_720p_assets_and_green_mask(self):
        task = object.__new__(DailyTask)
        task._templates = {}
        task._template_masks = {}

        for original_spec in (
            GUILD_MAIN_ACTIVE_TEMPLATE,
            GUILD_MAIN_FINISHED_TEMPLATE,
            HOME_ICE_TEMPLATE,
            HOME_RICE_TEMPLATE,
        ):
            spec = replace(original_spec, green_mask=False)
            self.assertTrue(spec.file_name.startswith("image/green/"))
            template = DailyTask._load_template(task, spec)
            mask = DailyTask._load_template_mask(task, spec)
            self.assertEqual(template.shape, mask.shape)
            self.assertGreater(mask.size, int(np.count_nonzero(mask)))

    def test_my_home_sign_in_continues_when_loading_is_missing(self):
        task = object.__new__(DailyTask)
        task.config = {"小屋页面等待秒数": 12.0}
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template = lambda *_args, **_kwargs: ("none", False)
        task._wait_for_template = lambda *_args, **_kwargs: True
        task._wait_home_brightness = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_my_home_sign_in(task))
        self.assertEqual([(166, 158), (100, 50)], clicks)

    def test_loading_wait_prioritizes_next_template(self):
        task = object.__new__(DailyTask)
        task.config = {"loading 出现等待秒数": 1.0, "loading 消失等待秒数": 1.0}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            if spec is LOADING_TEMPLATE:
                return DailyMatchResult(0.9, 0.9, (0, 0), (1, 1))
            if spec is MY_HOME_TEMPLATE and calls.count(MY_HOME_TEMPLATE.name) >= 2:
                return DailyMatchResult(0.9, 0.9, (0, 0), (1, 1))
            return DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match

        self.assertEqual(
            ("target", True),
            DailyTask._wait_loading_or_template(task, "小屋签到", MY_HOME_TEMPLATE, "my_home"),
        )
        self.assertEqual(
            [MY_HOME_TEMPLATE.name, LOADING_TEMPLATE.name, MY_HOME_TEMPLATE.name],
            calls,
        )

    def test_loading_wait_prioritizes_next_ocr(self):
        task = object.__new__(DailyTask)
        task.config = {"loading 出现等待秒数": 1.0, "loading 消失等待秒数": 1.0}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            if spec is LOADING_TEMPLATE:
                return DailyMatchResult(0.9, 0.9, (0, 0), (1, 1))
            return DailyMatchResult(-1.0, -1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._ocr_text = lambda *_args, **_kwargs: (
            "签到成功" if calls.count(LOADING_TEMPLATE.name) >= 1 else ""
        )

        self.assertEqual(
            ("target", True, "签到成功"),
            DailyTask._wait_loading_or_template_or_ocr(
                task,
                "公会签到",
                GUILD_SIGNUP_SUCCESS_TEMPLATE,
                GUILD_SUCCESS_KEYWORDS,
                "guild_sign_in",
            ),
        )

    def test_business_collect_uses_q_script_click_timing(self):
        task = object.__new__(DailyTask)
        task.config = {"一键收菜菜单等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._wait_for_ocr_keywords = lambda *_args, **_kwargs: (True, "一键获得 取消")
        task._wait_home_brightness = lambda *_args, **_kwargs: True
        clicks = []
        sleeps = []
        task._click_reference = lambda x, y, after_sleep=0.0: clicks.append((x, y, after_sleep))
        task.sleep = lambda seconds: sleeps.append(seconds)

        self.assertTrue(DailyTask.run_business_collect(task))
        self.assertEqual([0.5], sleeps)
        self.assertEqual(
            [
                (165, 260, 1.0),
                (1090, 814, 2.0),
                (832, 814, 1.0),
                (832, 814, 0.0),
            ],
            clicks,
        )

    def test_mf_reference_click_uses_1280_by_720_ratios(self):
        task = object.__new__(QuickHuntTask)
        calls = []
        task.operate_click = lambda x, y, **kwargs: calls.append((x, y, kwargs))

        task._click_mf_reference(640, 360, after_sleep=0.5)

        self.assertEqual([(0.5, 0.5, {"after_sleep": 0.5})], calls)

    def test_quick_hunt_resource_zero_uses_calibrated_1920_roi(self):
        task = object.__new__(QuickHuntTask)
        task.info_set = lambda *_args, **_kwargs: None
        calls = []

        class FakeVision:
            def ocr_text(self, _frame, name, relative_roi=None):
                calls.append((name, relative_roi))
                return "0 / 60"

        task._quick_vision = lambda: FakeVision()
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)

        self.assertTrue(task._quick_hunt_resource_empty("米饭"))
        self.assertEqual([("米饭数量", QUICK_HUNT_RESOURCE_ROI)], calls)

    def test_quick_hunt_regions_preserve_all_supplied_1920_calibrations(self):
        self.assertEqual(
            (1602 / 1920, 38 / 1080, 1724 / 1920, 80 / 1080),
            QUICK_HUNT_RESOURCE_ROI,
        )
        self.assertEqual(
            (1599 / 1920, 963 / 1080, 1720 / 1920, 1018 / 1080),
            QUICK_HUNT_BUTTON_ROI,
        )
        self.assertEqual(
            (623 / 1920, 257 / 1080, 1298 / 1920, 826 / 1080),
            QUICK_HUNT_COUNT_ROI,
        )
        self.assertEqual(
            (963 / 1920, 764 / 1080, 1136 / 1920, 805 / 1080),
            QUICK_HUNT_START_ROI,
        )
        self.assertEqual(
            (857 / 1920, 965 / 1080, 1055 / 1920, 1019 / 1080),
            QUICK_HUNT_REWARD_ROI,
        )
        self.assertEqual(
            (750 / 1920, 630 / 1080, 1200 / 1920, 915 / 1080),
            QUICK_HUNT_DIALOG_ROI,
        )
        self.assertEqual(
            (330 / 1920, 165 / 1080, 1528 / 1920, 865 / 1080),
            QUICK_HUNT_MAP_SCAN_ROI,
        )
        self.assertEqual(
            (135 / 1920, 205 / 1080, 168 / 1920, 337 / 1080),
            QUICK_HUNT_DOUBLE_ROI,
        )
        self.assertEqual(
            (235 / 1920, 128 / 1080, 340 / 1920, 452 / 1080),
            QUICK_HUNT_CRYSTAL_TITLE_ROI,
        )
        self.assertEqual(
            (1689 / 1920, 80 / 1080, 1794 / 1920, 288 / 1080),
            QUICK_HUNT_STONE_COUNT_ROI,
        )
        self.assertEqual({"金币": (177, 255), "经验": (176, 354)}, QUICK_HUNT_ADVENTURE_POINTS)
        self.assertEqual(
            {"金币": r"哥布林遗迹", "经验": r"史莱姆王国"},
            QUICK_HUNT_ADVENTURE_MAP_PATTERNS,
        )
        self.assertEqual((177, 449), QUICK_HUNT_CRYSTAL_POINT)
        self.assertEqual((101, 55), QUICK_HUNT_RETURN_POINT)
        self.assertEqual(QUICK_HUNT_CRYSTAL_TITLE_ROI, QUICK_HUNT_STONE_LIST_ROI)
        self.assertEqual("Double.png", QUICK_HUNT_DOUBLE_TEMPLATE.file_name)
        self.assertEqual(QUICK_HUNT_DOUBLE_ROI, QUICK_HUNT_DOUBLE_TEMPLATE.relative_roi)

    def test_quick_hunt_entry_pixel_uses_scaled_1920_reference_point(self):
        task = object.__new__(QuickHuntTask)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[158, 1188] = (0, 0, 255)

        is_red, point, bgr, hsv = task._quick_hunt_entry_red_state(frame)

        self.assertEqual((1782 / 1920, 237 / 1080), QUICK_HUNT_ENTRY_POINT)
        self.assertTrue(is_red)
        self.assertEqual((1188, 158), point)
        self.assertEqual((0, 0, 255), bgr)
        self.assertEqual((0, 255, 255), hsv)

    def test_quick_hunt_home_requires_button_and_brightness(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"主页亮度比例阈值": 0.75}
        match = DailyMatchResult(0.9, 0.85, (0, 0), (10, 10))
        task._match_best = lambda _frame, _specs: (match, HOME_RICE_TEMPLATE)
        task._passes = lambda _match, _spec: True
        task._home_brightness_ratio = lambda _frame: 0.7
        gacha_text = ["抽抽乐"]

        class FakeVision:
            def ocr_text(self, _frame, _name, relative_roi=None):
                self.relative_roi = relative_roi
                return gacha_text[0]

        task._quick_vision = lambda: FakeVision()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        self.assertFalse(task._quick_hunt_home_signals(frame)[0])

        task._home_brightness_ratio = lambda _frame: 0.8
        self.assertTrue(task._quick_hunt_home_signals(frame)[0])

        task._passes = lambda _match, _spec: False
        self.assertFalse(task._quick_hunt_home_signals(frame)[0])

        task._passes = lambda _match, _spec: True
        gacha_text[0] = ""
        self.assertFalse(task._quick_hunt_home_signals(frame)[0])

    def test_quick_hunt_open_menu_follows_home_red_click_and_ocr_sequence(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task._wait_for_quick_hunt_home = lambda: True
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._quick_hunt_entry_red_state = lambda _frame: (
            True,
            (1782, 237),
            (0, 0, 255),
            (0, 255, 255),
        )
        sleeps = []
        clicks = []
        ocr_calls = []
        statuses = {}
        task.sleep = lambda seconds: sleeps.append(seconds)
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        task.info_set = lambda key, value: statuses.__setitem__(key, value)

        def wait_ocr(patterns, roi, timeout, name):
            ocr_calls.append((patterns, roi, timeout, name))
            return "狩猎场", SimpleNamespace()

        task._quick_hunt_wait_ocr = wait_ocr

        self.assertEqual("opened", task._quick_hunt_open_menu())
        self.assertEqual([0.5, 0.5], sleeps)
        self.assertEqual(
            [(QUICK_HUNT_ENTRY_POINT[0], QUICK_HUNT_ENTRY_POINT[1], {"after_sleep": 1.0})],
            clicks,
        )
        self.assertEqual([r"狩猎场"], ocr_calls[0][0])
        self.assertIsNone(ocr_calls[0][1])
        self.assertEqual(8.0, ocr_calls[0][2])
        self.assertEqual("快速狩猎菜单确认", ocr_calls[0][3])
        self.assertEqual("狩猎场", statuses["快速狩猎菜单"])

    def test_quick_hunt_wait_ocr_scans_full_frame_and_reports_text(self):
        task = object.__new__(QuickHuntTask)
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.sleep = lambda _seconds: None
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        seen_rois = []

        class FakeVision:
            def ocr_boxes(self, _frame, _name, relative_roi=None):
                seen_rois.append(relative_roi)
                return [SimpleNamespace(name="狩猎场")]

        task._quick_vision = lambda: FakeVision()

        text, box = task._quick_hunt_wait_ocr(
            [r"狩猎场"],
            None,
            1.0,
            "快速狩猎菜单确认",
        )

        self.assertEqual("狩猎场", text)
        self.assertEqual("狩猎场", box.name)
        self.assertEqual([None], seen_rois)
        self.assertEqual(
            "狩猎场",
            statuses["快速狩猎菜单确认 OCR"],
        )

    def test_quick_hunt_open_menu_stops_when_home_is_not_confirmed(self):
        task = object.__new__(QuickHuntTask)
        task._wait_for_quick_hunt_home = lambda: False
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: self.fail(
            "home confirmation failure must stop before waiting"
        )
        task.capture_frame = lambda: self.fail(
            "home confirmation failure must stop before reading the entry pixel"
        )
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "home confirmation failure must not click"
        )

        self.assertEqual("failed", task._quick_hunt_open_menu())

    def test_quick_hunt_open_menu_skips_when_entry_pixel_is_not_red(self):
        task = object.__new__(QuickHuntTask)
        task.config = {}
        task._wait_for_quick_hunt_home = lambda: True
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._quick_hunt_entry_red_state = lambda _frame: (
            False,
            (1782, 237),
            (40, 40, 40),
            (0, 0, 40),
        )
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "non-red entry must not be clicked"
        )
        task.info_set = lambda *_args, **_kwargs: None

        self.assertEqual("skip", task._quick_hunt_open_menu())
        self.assertEqual([0.5], sleeps)

    def test_quick_hunt_map_scan_scrolls_down_at_most_six_times_and_clicks_ocr_center(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎章节图": "矿石·章节7"}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        scrolls = []
        task._quick_hunt_scroll_map_once = lambda: scrolls.append("down")
        target = SimpleNamespace(
            name="蜥蜴人祭坛",
            x=400,
            y=300,
            width=100,
            height=50,
        )

        class FakeVision:
            def __init__(self):
                self.calls = 0
                self.clicks = []

            def ocr_boxes(self, _frame, _name, relative_roi=None):
                self.calls += 1
                self.relative_roi = relative_roi
                return [target] if self.calls == 3 else []

            def click_client(self, point, _shape, after_sleep=0.0):
                self.clicks.append((point, after_sleep))

        vision = FakeVision()
        task._quick_vision = lambda: vision

        self.assertTrue(task._quick_hunt_select_hunting_ground())
        self.assertEqual(["down", "down"], scrolls)
        self.assertEqual(QUICK_HUNT_MAP_SCAN_ROI, vision.relative_roi)
        self.assertEqual([((450, 325), 0.8)], vision.clicks)

    def test_quick_hunt_map_scan_stops_after_exactly_six_downward_scrolls(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎章节图": "木材·章节9"}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        scrolls = []
        task._quick_hunt_scroll_map_once = lambda: scrolls.append("down")

        class FakeVision:
            def ocr_boxes(self, _frame, _name, relative_roi=None):
                self.relative_roi = relative_roi
                return []

        task._quick_vision = lambda: FakeVision()

        self.assertFalse(task._quick_hunt_select_hunting_ground())
        self.assertEqual(["down"] * 6, scrolls)

    def test_quick_hunt_double_scan_accepts_upper_and_lower_matches_together(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎模板阈值": 0.78,
            "快速狩猎像素相似度阈值": 0.72,
        }
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        statuses = {}
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        upper = SimpleNamespace(center=(150, 240), score=0.95, pixel_score=0.94)
        lower = SimpleNamespace(center=(150, 310), score=0.96, pixel_score=0.93)

        class FakeVision:
            def threshold_for(self, _spec):
                return 0.78

            def match_all(self, _frame, spec, minimum_score):
                self.spec = spec
                self.minimum_score = minimum_score
                return (upper, lower)

        vision = FakeVision()
        task._quick_vision = lambda: vision

        self.assertEqual(
            {"金币": True, "经验": True},
            task._quick_hunt_double_states(),
        )
        self.assertEqual("Double.png", vision.spec.file_name)
        self.assertEqual(0.78, vision.minimum_score)
        self.assertIn("金币=双倍", statuses["快速狩猎双倍识别"])
        self.assertIn("经验/史莱姆=双倍", statuses["快速狩猎双倍识别"])

    def test_quick_hunt_double_scan_upper_match_means_gold_only(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎模板阈值": 0.78,
            "快速狩猎像素相似度阈值": 0.72,
        }
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        upper = SimpleNamespace(center=(150, 240), score=0.95, pixel_score=0.94)

        class FakeVision:
            def threshold_for(self, _spec):
                return 0.78

            def match_all(self, _frame, _spec, minimum_score):
                self.minimum_score = minimum_score
                return (upper,)

        task._quick_vision = lambda: FakeVision()

        self.assertEqual(
            {"金币": True, "经验": False},
            task._quick_hunt_double_states(),
        )

    def test_quick_hunt_prefer_double_uses_gold_when_both_routes_are_double(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎双倍策略": "优先双倍",
            "快速狩猎资源倾向": "经验",
        }
        task._quick_hunt_double_states = lambda: {"金币": True, "经验": True}
        clicks = []
        task._quick_hunt_click_adventure = lambda resource: clicks.append(resource)
        task.log_info = lambda *_args, **_kwargs: None

        self.assertTrue(task._quick_hunt_select_adventure_route())
        self.assertEqual(["金币"], clicks)

    def test_quick_hunt_ignore_double_always_selects_gold(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎双倍策略": "忽视双倍",
            "快速狩猎资源倾向": "经验",
        }
        task._quick_hunt_double_states = lambda: self.fail(
            "ignore-double mode must not inspect the template"
        )
        clicks = []
        task._quick_hunt_click_adventure = lambda resource: clicks.append(resource)
        task.log_info = lambda *_args, **_kwargs: None

        self.assertTrue(task._quick_hunt_select_adventure_route())
        self.assertEqual(["金币"], clicks)

    def test_quick_hunt_mf_scheduler_has_no_reset_or_leftover_fallback(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎狩猎场": True,
            "快速狩猎冒险航线": True,
            "快速狩猎米饭分配": "狩猎场x1 / 双倍图MAX",
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        calls = []
        task._quick_hunt_resource_empty = lambda _resource: False
        task._quick_hunt_select_hunting_ground = lambda: calls.append("hunt") or True
        task._quick_hunt_select_adventure_route = (
            lambda: calls.append("adventure-no-double") or False
        )
        task._quick_hunt_execute_current_map = (
            lambda mode, stage: calls.append((stage, mode)) or "done"
        )

        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(
            [
                "hunt",
                ("狩猎场", "MIN"),
                "adventure-no-double",
            ],
            calls,
        )

    def test_quick_hunt_max_hunting_mode_skips_adventure_route(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎狩猎场": True,
            "快速狩猎冒险航线": True,
            "快速狩猎米饭分配": "狩猎场MAX / 跳过冒险航线",
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._quick_hunt_resource_empty = lambda _resource: False
        task._quick_hunt_select_hunting_ground = lambda: True
        calls = []
        task._quick_hunt_execute_current_map = (
            lambda mode, stage: calls.append((stage, mode)) or "done"
        )
        task._quick_hunt_select_adventure_route = lambda: self.fail(
            "MAX hunting allocation must skip adventure"
        )

        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([("狩猎场", "MAX")], calls)

    def test_quick_hunt_stone_counts_follow_top_to_bottom_element_order(self):
        task = object.__new__(QuickHuntTask)
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        boxes = [
            SimpleNamespace(name="90", x=1700, y=240, width=40, height=20),
            SimpleNamespace(name="300", x=1700, y=90, width=40, height=20),
            SimpleNamespace(name="180", x=1700, y=200, width=40, height=20),
            SimpleNamespace(name="250", x=1700, y=130, width=40, height=20),
            SimpleNamespace(name="200", x=1700, y=165, width=40, height=20),
        ]

        class FakeVision:
            def ocr_boxes(self, _frame, _name, relative_roi=None):
                self.relative_roi = relative_roi
                return boxes

        vision = FakeVision()
        task._quick_vision = lambda: vision

        self.assertEqual(
            {"火": 300, "水": 250, "风": 200, "光": 180, "暗": 90},
            task._quick_hunt_stone_counts(),
        )
        self.assertEqual(QUICK_HUNT_STONE_COUNT_ROI, vision.relative_roi)

    def test_quick_hunt_crystal_selects_lowest_stone_and_runs_max(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        task._click_reference = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: (
            "火之洞穴 水之洞穴 风之洞穴 光之洞穴 暗之洞穴",
            SimpleNamespace(),
        )
        task._quick_hunt_resource_empty = lambda _resource: False
        task._quick_hunt_stone_counts = lambda: {
            "火": 50,
            "水": 40,
            "风": 30,
            "光": 10,
            "暗": 20,
        }
        selected = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, _timeout, name: selected.append((patterns, roi, name))
            or True
        )
        executions = []
        task._quick_hunt_execute_current_map = (
            lambda mode, stage: executions.append((mode, stage)) or "done"
        )

        self.assertTrue(task._quick_hunt_run_crystal_cave())
        self.assertEqual([(177, 449, {"after_sleep": 0.8})], clicks)
        self.assertIn("光", selected[0][0][0])
        self.assertEqual(QUICK_HUNT_CRYSTAL_TITLE_ROI, selected[0][1])
        self.assertEqual([("MAX", "光属性圣石")], executions)

    def test_quick_hunt_adventure_map_is_verified_before_consuming_rice(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        click_calls = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, timeout, name, **kwargs: click_calls.append(
                (patterns, roi, timeout, name, kwargs)
            )
            or True
        )
        wait_calls = []
        task._quick_hunt_wait_ocr = (
            lambda patterns, roi, timeout, name: wait_calls.append(
                (patterns, roi, timeout, name)
            )
            or ("哥布林遗迹极难", SimpleNamespace())
        )
        task._quick_hunt_wait_result = lambda _stage: "done"

        self.assertEqual(
            "done",
            task._quick_hunt_execute_current_map(
                "MAX",
                "冒险航线",
                expected_map_pattern=r"哥布林遗迹",
            ),
        )
        self.assertEqual(
            [([r"哥布林遗迹"], QUICK_HUNT_COUNT_ROI, 8.0, "冒险航线-地图确认")],
            wait_calls,
        )
        self.assertEqual("冒险航线-MAX", click_calls[1][3])
        self.assertEqual("冒险航线-开始狩猎", click_calls[2][3])

    def test_quick_hunt_adventure_map_mismatch_cancels_before_consuming_rice(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        click_calls = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, timeout, name, **kwargs: click_calls.append(
                (patterns, roi, timeout, name, kwargs)
            )
            or True
        )
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: ("", None)
        task._quick_hunt_wait_result = lambda _stage: self.fail(
            "错误地图不得开始狩猎"
        )

        self.assertEqual(
            "failed",
            task._quick_hunt_execute_current_map(
                "MAX",
                "冒险航线",
                expected_map_pattern=r"哥布林遗迹",
            ),
        )
        self.assertEqual("冒险航线-取消错误地图", click_calls[1][3])
        self.assertEqual([r"取消"], click_calls[1][0])

    def test_quick_hunt_run_dispatches_rice_then_crystal_and_returns_home(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎圣石洞穴": True}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        calls = []
        task._quick_hunt_open_menu = lambda: calls.append("open") or "opened"
        task._quick_hunt_run_rice_scheduler = lambda: calls.append("rice") or True
        task._quick_hunt_run_crystal_cave = lambda: calls.append("crystal") or True
        task._quick_hunt_return_home = lambda: calls.append("home") or True

        self.assertTrue(task.run_quick_hunt())
        self.assertEqual(["open", "rice", "crystal", "home"], calls)

    def test_quick_hunt_rice_failure_still_runs_crystal_before_returning_home(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎圣石洞穴": True}
        task.info_set = lambda *_args, **_kwargs: None
        calls = []
        task._quick_hunt_open_menu = lambda: "opened"
        task._quick_hunt_run_rice_scheduler = lambda: calls.append("rice-failed") or False
        task._quick_hunt_run_crystal_cave = lambda: calls.append("crystal") or True
        task._quick_hunt_return_home = lambda: calls.append("home") or True

        self.assertFalse(task.run_quick_hunt())
        self.assertEqual(["rice-failed", "crystal", "home"], calls)

    def test_quick_hunt_return_home_uses_fixed_point_and_three_signal_confirmation(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"主页亮度比例阈值": 0.75}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        signals = iter(
            (
                (
                    False,
                    DailyMatchResult(0.9, 0.85, (0, 0), (10, 10)),
                    HOME_RICE_TEMPLATE,
                    0.8,
                    "-",
                ),
                (
                    True,
                    DailyMatchResult(0.9, 0.85, (0, 0), (10, 10)),
                    HOME_RICE_TEMPLATE,
                    0.8,
                    "抽抽乐",
                ),
            )
        )
        task._quick_hunt_home_signals = lambda _frame: next(signals)
        task._quick_hunt_current_map_context = lambda _frame: "野猪洞穴"
        clicks = []
        task._click_reference = lambda x, y, **kwargs: clicks.append((x, y, kwargs))

        self.assertTrue(task._quick_hunt_return_home())
        self.assertEqual([(101, 55, {"after_sleep": 2.0})], clicks)

    def test_quick_hunt_return_context_uses_full_frame_and_top_left_match(self):
        task = object.__new__(QuickHuntTask)
        task.info_set = lambda *_args, **_kwargs: None
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        seen_rois = []

        class FakeVision:
            def ocr_boxes(self, _frame, _name, relative_roi=None):
                seen_rois.append(relative_roi)
                return [
                    SimpleNamespace(
                        name="1.野猪洞穴",
                        x=700,
                        y=500,
                        width=100,
                        height=20,
                    ),
                    SimpleNamespace(
                        name="暗之洞穴",
                        x=200,
                        y=80,
                        width=100,
                        height=20,
                    ),
                ]

        task._quick_vision = lambda: FakeVision()

        self.assertEqual("属性洞穴", task._quick_hunt_current_map_context(frame))
        self.assertEqual([None], seen_rois)

    def test_quick_hunt_box_enabled_rejects_dark_text(self):
        box = SimpleNamespace(x=2, y=2, width=6, height=6)
        dark = np.zeros((10, 10, 3), dtype=np.uint8)
        light = dark.copy()
        light[2:8, 2:8] = 255

        self.assertFalse(QuickHuntTask._quick_hunt_box_enabled(dark, box))
        self.assertTrue(QuickHuntTask._quick_hunt_box_enabled(light, box))

    def test_daily_run_stops_after_failed_step(self):
        task = object.__new__(DailyTask)
        task.config = {
            "启用": True,
            "执行公会签到": True,
            "执行小屋签到": True,
            "执行一键收菜": True,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_error = lambda *_args, **_kwargs: None
        calls = []
        task.run_guild_sign_in = lambda: calls.append("guild") or False
        task.run_my_home_sign_in = lambda: calls.append("home") or True
        task.run_business_collect = lambda: calls.append("business") or True
        task.run_quick_hunt = lambda: calls.append("hunt") or True

        self.assertFalse(DailyTask.run(task))
        self.assertEqual(["guild"], calls)


if __name__ == "__main__":
    unittest.main()
