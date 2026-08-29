import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from ok.util.config import Config

from src.tasks.DailyTask import (
    BUSINESS_COLLECT_KEYWORDS,
    GUILD_FINISHED_TEMPLATE,
    GUILD_MAIN_ACTIVE_TEMPLATE,
    GUILD_MAIN_FINISHED_TEMPLATE,
    GUILD_SIGNUP_SUCCESS_TEMPLATE,
    GUILD_SUCCESS_KEYWORDS,
    GUILD_TEMPLATE,
    MY_HOME_TEMPLATE,
    DailyTask,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.quick_hunt import (
    QUICK_HUNT_ADVENTURE_LABEL_PATTERNS,
    QUICK_HUNT_ADVENTURE_LIST_ROI,
    QUICK_HUNT_ADVENTURE_MAP_PATTERNS,
    QUICK_HUNT_BUTTON_ROI,
    QUICK_HUNT_COUNT_ROI,
    QUICK_HUNT_CRYSTAL_POINT,
    QUICK_HUNT_CRYSTAL_TITLE_ROI,
    QUICK_HUNT_DIALOG_ROI,
    QUICK_HUNT_DOUBLE_ROI,
    QUICK_HUNT_DOUBLE_TEMPLATE,
    QUICK_HUNT_ENTRY_POINT,
    QUICK_HUNT_MAP_SCAN_ROI,
    QUICK_HUNT_RED_POINT,
    QUICK_HUNT_RESOURCE_CAPACITIES,
    QUICK_HUNT_RESOURCE_ROI,
    QUICK_HUNT_RETURN_POINT,
    QUICK_HUNT_REWARD_ROI,
    QUICK_HUNT_START_ROI,
    QUICK_HUNT_STONE_COUNT_ROI,
    QUICK_HUNT_STONE_LIST_ROI,
)
from src.tasks.QuickHuntTask import QuickHuntTask
from src.tasks.task_vision_mixin import (
    LOADING_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
)
from src.utils.image_utils import crop_relative


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
        self.assertTrue(task.default_config["启用"])
        self.assertIn("快速狩猎 OCR 阈值", task.default_config)
        self.assertNotIn("快速狩猎章节图", task.default_config)

        test_keys = (
            "快速狩猎入口测试",
            "快速狩猎菜单测试",
            "快速狩猎圣石测试",
            "快速狩猎完整测试",
        )
        visible_keys = task.config_type["启用"]["sub_configs"][True]
        for key in (
            "识别成功后等待秒数",
            "快速狩猎冒险航线",
            "快速狩猎狩猎场",
            "快速狩猎圣石洞穴",
            "快速狩猎双倍策略",
            "快速狩猎资源倾向",
            "快速狩猎米饭分配",
        ):
            self.assertIn(key, visible_keys)
        self.assertNotIn("快速狩猎章节图", visible_keys)
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

    def test_quick_hunt_home_confirmation_config_survives_hydration(self):
        task = object.__new__(QuickHuntTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch("src.tasks.DailyTask.BaseBD2Task.__init__", return_value=None):
            QuickHuntTask.__init__(task)

        expected_configs = {
            "主页压暗阈值": (
                185.0,
                "主页左列灰度 p95 低于该值视为被公告压暗（0-255）。",
                {"min": 100.0, "max": 250.0, "step": 5.0},
            ),
            "主页确认等待秒数": (
                10.0,
                "点击主页按钮后确认已返回主页的最长等待时间。",
                {"min": 2.0, "max": 30.0, "step": 1.0},
            ),
        }
        for key, (default, description, config_type) in expected_configs.items():
            with self.subTest(key=key):
                self.assertEqual(default, task.default_config[key])
                self.assertEqual(description, task.config_description[key])
                self.assertEqual(config_type, task.config_type[key])

        persisted_config = dict(task.default_config)
        persisted_config.update(
            {
                "主页压暗阈值": 210.0,
                "主页确认等待秒数": 27.0,
            }
        )
        hydrated_config = Config.__new__(Config)
        hydrated_config.validator = None

        self.assertFalse(
            hydrated_config.verify_config(persisted_config, task.default_config)
        )
        self.assertEqual(210.0, hydrated_config["主页压暗阈值"])
        self.assertEqual(27.0, hydrated_config["主页确认等待秒数"])

    def test_quick_hunt_legacy_config_restores_branches_and_drops_chapter(self):
        task = object.__new__(QuickHuntTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch("src.tasks.DailyTask.BaseBD2Task.__init__", return_value=None):
            QuickHuntTask.__init__(task)

        hydrated_config = Config.__new__(Config)
        hydrated_config.validator = None
        modified = hydrated_config.verify_config(
            {
                "识别成功后等待秒数": 1.0,
                "快速狩猎章节图": "低练度·章节1",
            },
            task.default_config,
        )

        self.assertTrue(modified)
        self.assertTrue(hydrated_config["启用"])
        self.assertTrue(hydrated_config["快速狩猎狩猎场"])
        self.assertTrue(hydrated_config["快速狩猎冒险航线"])
        self.assertTrue(hydrated_config["快速狩猎圣石洞穴"])
        self.assertNotIn("快速狩猎章节图", hydrated_config)

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

    def test_quick_hunt_success_emits_standalone_completion_notification(self):
        task = object.__new__(QuickHuntTask)
        task._quick_hunt_test_action = None
        task.config = {"启用": True}
        task.info_set = lambda *_args, **_kwargs: None
        notifications = []
        task.log_info = lambda message, notify=False: notifications.append(
            (message, notify)
        )
        task.run_quick_hunt = lambda: True

        self.assertTrue(QuickHuntTask.run(task))
        self.assertEqual(
            [("快速狩猎：流程完成并返回主页。", True)],
            notifications,
        )

    def test_quick_hunt_entry_inspection_does_not_click(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎模板阈值": 0.78,
            "快速狩猎像素相似度阈值": 0.72,
            "主页压暗阈值": 185.0,
        }
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        task._quick_hunt_home_signals = lambda _frame: (
            True,
            3,
            255.0,
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
        self.assertEqual("p95=255/185", statuses["快速狩猎主页亮度"])
        self.assertEqual("抽抽乐", statuses["快速狩猎主页抽抽乐 OCR"])

    def test_quick_hunt_menu_inspection_reports_ocr_and_templates_without_clicking(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎模板阈值": 0.78,
            "快速狩猎像素相似度阈值": 0.72,
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
        self.assertEqual(10, len(ocr_calls))
        self.assertEqual("测试-菜单标题", statuses["快速狩猎菜单 OCR"])
        self.assertIn("通过", statuses["快速狩猎收起模板"])
        self.assertIn("金币=非双倍", statuses["快速狩猎双倍识别"])

    def test_keyword_match_count_ignores_spaces_and_case(self):
        text = "签到 成功\n獎勵已發送至信箱"

        self.assertEqual(
            2,
            DailyTask._keyword_match_count(
                text,
                [*GUILD_SUCCESS_KEYWORDS, "不存在"],
            ),
        )

    def test_business_collect_keywords_match_reported_ocr_frames(self):
        # BUG-20260829-06：RPT-20260829-143029 两帧失败 OCR 的弹窗相关原文。
        traditional_frame = (
            "Lv.30 餐廳營業額現狀 - 立即前往 累計獎勵 結算 "
            "Lv.3 魚籠捕獲現狀 立即前往 LV.21釣魚 907/10000(9%) "
            "回收 助手工作現況 可於夢幻廣場遊戲卡帶>领地中配置助手工作後解鎖 "
            "取消 一鍵獲得"
        )
        mixed_script_frame = traditional_frame.replace(
            "餐廳營業額現狀", "餐廳營業额現狀"
        ).replace("一鍵獲得", "一键獲得")

        for frame in (traditional_frame, mixed_script_frame):
            self.assertEqual(
                5,
                DailyTask._keyword_match_count(frame, BUSINESS_COLLECT_KEYWORDS),
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

        crop = crop_relative(image, (0.2, 0.3, 0.6, 0.8))

        np.testing.assert_array_equal(crop, image[3:8, 2:6])

    def test_guild_sign_in_does_not_click_without_guild_trigger(self):
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task._match = lambda _frame, _spec: MatchResult(-1.0, (0, 0), (0, 0))
        task._click_reference = lambda *_args, **_kwargs: self.fail("should not click")

        self.assertFalse(DailyTask.run_guild_sign_in(task))

    def _guard_task(self, config):
        task = object.__new__(DailyTask)
        task.config = config
        confirmations = []
        task._wait_for_home_confirmation = (
            lambda name, *_args, **_kwargs: confirmations.append(name) or False
        )
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: self.fail(
            "入口主页确认失败时不得先抓帧做模板搜索"
        )
        task._click_reference = lambda *_args, **_kwargs: self.fail(
            "入口主页确认失败时不得点击"
        )
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "入口主页确认失败时不得点击"
        )
        return task, confirmations

    def test_guild_sign_in_requires_home_confirmation_before_entry(self):
        task, confirmations = self._guard_task({"公会入口阈值": 0.78})

        self.assertFalse(DailyTask.run_guild_sign_in(task))
        self.assertEqual(["公会签到入口前主页确认"], confirmations)

    def test_my_home_sign_in_requires_home_confirmation_before_entry(self):
        task, confirmations = self._guard_task({"小屋页面等待秒数": 12.0})

        self.assertFalse(DailyTask.run_my_home_sign_in(task))
        self.assertEqual(["小屋签到入口前主页确认"], confirmations)

    def test_business_collect_requires_home_confirmation_before_entry(self):
        task, confirmations = self._guard_task({"一键收菜菜单等待秒数": 8.0})

        self.assertFalse(DailyTask.run_business_collect(task))
        self.assertEqual(["一键收菜入口前主页确认"], confirmations)

    def test_daily_run_counts_home_confirmation_failure_without_clicks(self):
        task = object.__new__(DailyTask)
        task.config = {
            "启用": True,
            "执行公会签到": True,
            "执行小屋签到": False,
            "执行一键收菜": False,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_error = lambda *_args, **_kwargs: None
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: False
        actions = []
        task.capture_frame = lambda: actions.append("capture")
        task._click_reference = lambda *_args, **_kwargs: actions.append("click")
        task.operate_click = lambda *_args, **_kwargs: actions.append("click")

        self.assertFalse(DailyTask.run(task))
        self.assertEqual([], actions)

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
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            if spec is GUILD_TEMPLATE:
                return MatchResult(0.7, (0, 0), (1, 1), pixel_score=0.7)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True

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
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True

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
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

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
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True

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
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True

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
                return MatchResult(0.967, (0, 0), (1, 1), pixel_score=0.967)
            if spec is GUILD_MAIN_FINISHED_TEMPLATE:
                return MatchResult(0.978, (0, 0), (1, 1), pixel_score=0.978)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_guild_sign_in(task))
        self.assertEqual([(370, 155), (100, 50)], clicks)

    def test_new_main_templates_use_720p_assets_and_green_mask(self):
        task = object.__new__(DailyTask)
        task._templates = {}
        task._template_masks = {}

        for original_spec in (
            GUILD_MAIN_ACTIVE_TEMPLATE,
            GUILD_MAIN_FINISHED_TEMPLATE,
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
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True

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
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            if spec is MY_HOME_TEMPLATE and calls.count(MY_HOME_TEMPLATE.name) >= 2:
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

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
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

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
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
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

    def test_quick_hunt_rice_zero_uses_new_90_capacity_and_calibrated_roi(self):
        task = object.__new__(QuickHuntTask)
        task.info_set = lambda *_args, **_kwargs: None
        calls = []
        text = ["0 / 90"]

        class FakeVision:
            def ocr_text(self, _frame, name, relative_roi=None):
                calls.append((name, relative_roi))
                return text[0]

        task._quick_vision = lambda: FakeVision()
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)

        self.assertEqual({"米饭": 90}, QUICK_HUNT_RESOURCE_CAPACITIES)
        self.assertTrue(task._quick_hunt_resource_empty("米饭"))
        text[0] = "0 / 60"
        self.assertFalse(task._quick_hunt_resource_empty("米饭"))
        text[0] = "18 / 90"
        self.assertFalse(task._quick_hunt_resource_empty("米饭"))
        self.assertEqual(
            [("米饭数量", QUICK_HUNT_RESOURCE_ROI)] * 3,
            calls,
        )

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
        self.assertEqual(
            (128 / 1920, 116 / 1080, 228 / 1920, 504 / 1080),
            QUICK_HUNT_ADVENTURE_LIST_ROI,
        )
        self.assertEqual(
            {"金币": r"^金币$", "经验": r"^史莱姆$"},
            QUICK_HUNT_ADVENTURE_LABEL_PATTERNS,
        )
        self.assertEqual(
            {"金币": r"哥布林遗迹", "经验": r"史莱姆王国"},
            QUICK_HUNT_ADVENTURE_MAP_PATTERNS,
        )
        self.assertEqual((177, 449), QUICK_HUNT_CRYSTAL_POINT)
        self.assertEqual((101, 55), QUICK_HUNT_RETURN_POINT)
        self.assertEqual(QUICK_HUNT_CRYSTAL_TITLE_ROI, QUICK_HUNT_STONE_LIST_ROI)
        self.assertEqual("Double.png", QUICK_HUNT_DOUBLE_TEMPLATE.file_name)
        self.assertEqual(QUICK_HUNT_DOUBLE_ROI, QUICK_HUNT_DOUBLE_TEMPLATE.relative_roi)

    def test_quick_hunt_red_diagnostic_uses_scaled_1920_reference_point(self):
        task = object.__new__(QuickHuntTask)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[158, 1188] = (0, 0, 255)

        is_red, point, bgr, hsv = task._quick_hunt_entry_red_state(frame)

        self.assertEqual((1782 / 1920, 237 / 1080), QUICK_HUNT_RED_POINT)
        self.assertTrue(is_red)
        self.assertEqual((1188, 158), point)
        self.assertEqual((0, 0, 255), bgr)
        self.assertEqual((0, 255, 255), hsv)

    def test_quick_hunt_home_requires_keyword_votes_brightness_and_gacha_ocr(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"主页压暗阈值": 185.0}
        left_text = ["我的小屋 经营管理格鲁TALK 街机游戏"]
        gacha_text = ["抽抽乐"]

        class FakeVision:
            def ocr_text(self, _frame, name, relative_roi=None):
                self.relative_roi = relative_roi
                return left_text[0] if "左列" in name else gacha_text[0]

        task._quick_vision = lambda: FakeVision()
        bright_frame = np.full((1080, 1920, 3), 255, dtype=np.uint8)
        dimmed_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        self.assertTrue(task._quick_hunt_home_signals(bright_frame)[0])

        left_text[0] = "我的小屋"
        self.assertFalse(task._quick_hunt_home_signals(bright_frame)[0])

        left_text[0] = "我的小屋 格鲁TALK 街机游戏"
        self.assertFalse(task._quick_hunt_home_signals(dimmed_frame)[0])

        gacha_text[0] = ""
        self.assertFalse(task._quick_hunt_home_signals(bright_frame)[0])

    def test_quick_hunt_open_menu_prefers_home_ocr_center(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task._wait_for_quick_hunt_home = lambda: True
        clicks = []
        click_ocr_calls = []
        ocr_calls = []
        statuses = {}
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        task._quick_hunt_click_ocr = lambda patterns, roi, timeout, name: (
            click_ocr_calls.append((patterns, roi, timeout, name)) or True
        )

        def wait_ocr(patterns, roi, timeout, name):
            ocr_calls.append((patterns, roi, timeout, name))
            return "狩猎场", SimpleNamespace()

        task._quick_hunt_wait_ocr = wait_ocr

        self.assertEqual("opened", task._quick_hunt_open_menu())
        self.assertEqual([([r"^快速狩猎$"], None, 8.0, "主页快速狩猎入口")], click_ocr_calls)
        self.assertEqual([], clicks)
        self.assertEqual([r"狩猎场"], ocr_calls[0][0])
        self.assertIsNone(ocr_calls[0][1])
        self.assertEqual(8.0, ocr_calls[0][2])
        self.assertEqual("快速狩猎菜单确认", ocr_calls[0][3])
        self.assertEqual("已进入", statuses["快速狩猎入口"])
        self.assertEqual("狩猎场", statuses["快速狩猎菜单"])

    def test_quick_hunt_open_menu_uses_reference_center_when_ocr_misses(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task._wait_for_quick_hunt_home = lambda: True
        task._quick_hunt_click_ocr = lambda *_args, **_kwargs: False
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: (
            "狩猎场",
            SimpleNamespace(),
        )
        clicks = []
        statuses = {}
        task.operate_click = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        task.info_set = lambda key, value: statuses.__setitem__(key, value)

        self.assertEqual("opened", task._quick_hunt_open_menu())
        self.assertEqual((1756 / 1920, 262 / 1080), QUICK_HUNT_ENTRY_POINT)
        self.assertEqual(
            [(*QUICK_HUNT_ENTRY_POINT, {"after_sleep": 1.0})],
            clicks,
        )
        self.assertEqual("已进入", statuses["快速狩猎入口"])

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

    def test_quick_hunt_open_menu_does_not_use_red_pixel_as_gate(self):
        task = object.__new__(QuickHuntTask)
        task.config = {}
        task._wait_for_quick_hunt_home = lambda: True
        task._quick_hunt_entry_red_state = lambda _frame: self.fail(
            "normal flow must not inspect the unreliable red pixel"
        )
        task._quick_hunt_click_ocr = lambda *_args, **_kwargs: True
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: (
            "狩猎场",
            SimpleNamespace(),
        )
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "OCR success must not use the fixed-coordinate fallback"
        )
        task.info_set = lambda *_args, **_kwargs: None

        self.assertEqual("opened", task._quick_hunt_open_menu())

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
        task._quick_hunt_click_adventure = (
            lambda resource: clicks.append(resource) or True
        )
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
        task._quick_hunt_click_adventure = (
            lambda resource: clicks.append(resource) or True
        )
        task.log_info = lambda *_args, **_kwargs: None

        self.assertTrue(task._quick_hunt_select_adventure_route())
        self.assertEqual(["金币"], clicks)

    def test_quick_hunt_scheduler_uses_current_default_hunting_ground(self):
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
        task._quick_hunt_select_hunting_ground = lambda: self.fail(
            "current default hunting ground must not be changed"
        )
        task._quick_hunt_select_adventure_route = (
            lambda: calls.append("adventure-no-double") or False
        )
        task._quick_hunt_execute_current_map = (
            lambda mode, stage: calls.append((stage, mode)) or "done"
        )

        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(
            [
                ("狩猎场", "MIN"),
                "adventure-no-double",
            ],
            calls,
        )

    def test_quick_hunt_adventure_wrong_map_reselects_by_ocr_once(self):
        task = object.__new__(QuickHuntTask)
        task.config = {
            "快速狩猎狩猎场": False,
            "快速狩猎冒险航线": True,
            "快速狩猎米饭分配": "狩猎场x1 / 双倍图MAX",
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._quick_hunt_resource_empty = lambda _resource: False
        task._quick_hunt_select_adventure_route = lambda: "金币"
        calls = []
        task._quick_hunt_click_adventure = (
            lambda resource: calls.append(("reselect", resource)) or True
        )
        results = iter(("wrong_map", "done"))
        task._quick_hunt_execute_current_map = (
            lambda mode, stage, expected_map_pattern=None: calls.append(
                (stage, mode, expected_map_pattern)
            )
            or next(results)
        )

        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(
            [
                ("冒险航线", "MAX", r"哥布林遗迹"),
                ("reselect", "金币"),
                ("冒险航线重试", "MAX", r"哥布林遗迹"),
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
        task._quick_hunt_select_hunting_ground = lambda: self.fail(
            "current default hunting ground must not be changed"
        )
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
        map_calls = []
        task._quick_hunt_wait_map_confirmation = (
            lambda pattern, name: map_calls.append((pattern, name))
            or ("matched", "哥布林遗迹极难", None)
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
            [(r"哥布林遗迹", "冒险航线-地图确认")],
            map_calls,
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
        task._quick_hunt_wait_map_confirmation = (
            lambda *_args, **_kwargs: (
                "wrong",
                "野猪洞穴极难",
                "野猪洞穴",
            )
        )
        task._quick_hunt_wait_result = lambda _stage: self.fail(
            "错误地图不得开始狩猎"
        )

        self.assertEqual(
            "wrong_map",
            task._quick_hunt_execute_current_map(
                "MAX",
                "冒险航线",
                expected_map_pattern=r"哥布林遗迹",
            ),
        )
        self.assertEqual("冒险航线-取消错误地图", click_calls[1][3])
        self.assertEqual([r"取消"], click_calls[1][0])

    def test_quick_hunt_adventure_click_uses_requested_ocr_region_and_center(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        calls = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, timeout, name: calls.append(
                (patterns, roi, timeout, name)
            )
            or True
        )

        self.assertTrue(task._quick_hunt_click_adventure("金币"))
        self.assertEqual(
            [
                (
                    [r"^金币$"],
                    QUICK_HUNT_ADVENTURE_LIST_ROI,
                    8.0,
                    "选择金币航线",
                )
            ],
            calls,
        )

    def test_quick_hunt_map_confirmation_rejects_known_wrong_map_immediately(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda _seconds: self.fail("明确错误地图不应继续等待")

        class FakeVision:
            def ocr_boxes(self, _frame, _name, relative_roi=None):
                self.relative_roi = relative_roi
                return [SimpleNamespace(name="野猪洞穴极难")]

        vision = FakeVision()
        task._quick_vision = lambda: vision

        self.assertEqual(
            ("wrong", "野猪洞穴极难", "野猪洞穴"),
            task._quick_hunt_wait_map_confirmation(
                r"哥布林遗迹",
                "冒险航线-地图确认",
            ),
        )
        self.assertEqual(QUICK_HUNT_COUNT_ROI, vision.relative_roi)

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

    def test_quick_hunt_rice_failure_skips_crystal_and_returns_home(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎圣石洞穴": True}
        task.info_set = lambda *_args, **_kwargs: None
        calls = []
        task._quick_hunt_open_menu = lambda: "opened"
        task._quick_hunt_run_rice_scheduler = lambda: calls.append("rice-failed") or False
        task._quick_hunt_run_crystal_cave = lambda: calls.append("crystal") or True
        task._quick_hunt_return_home = lambda: calls.append("home") or True

        self.assertFalse(task.run_quick_hunt())
        self.assertEqual(["rice-failed", "home"], calls)

    def test_quick_hunt_return_home_uses_fixed_point_and_three_signal_confirmation(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"主页压暗阈值": 185.0}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        signals = iter(
            (
                (False, 1, 120.0, "-"),
                (True, 3, 253.0, "抽抽乐"),
            )
        )
        task._quick_hunt_home_signals = lambda _frame: next(signals)
        task._quick_hunt_current_map_context = lambda _frame: "野猪洞穴"
        clicks = []
        task._click_reference = lambda x, y, **kwargs: clicks.append((x, y, kwargs))

        self.assertTrue(task._quick_hunt_return_home())
        self.assertEqual([(101, 55, {"after_sleep": 2.0})], clicks)

    def test_quick_hunt_return_home_clears_announcement_before_clicking_back(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"主页压暗阈值": 185.0}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        signals = iter(
            (
                (False, 3, 126.0, "抽抽乐"),
                (True, 3, 253.0, "抽抽乐"),
            )
        )
        task._quick_hunt_home_signals = lambda _frame: next(signals)
        task._quick_hunt_current_map_context = lambda _frame: "野猪洞穴"
        task._home_p95_threshold = lambda: 185.0
        announcement_signals = []
        task.clear_temporary_home_announcement_if_needed = (
            lambda **values: announcement_signals.append(values) or True
        )
        task._click_reference = lambda *_args, **_kwargs: self.fail(
            "temporary announcement must be cleared before another back click"
        )
        task.sleep = lambda *_args, **_kwargs: None

        self.assertTrue(task._quick_hunt_return_home())
        self.assertEqual(1, len(announcement_signals))
        self.assertEqual("快速狩猎返回主页", announcement_signals[0]["context"])

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
