import unittest
from dataclasses import replace
from types import SimpleNamespace

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
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    DailyMatchResult,
    DailyTask,
)


class DailyTaskHelperTest(unittest.TestCase):
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
        task = object.__new__(DailyTask)
        calls = []
        task.operate_click = lambda x, y, **kwargs: calls.append((x, y, kwargs))

        task._click_mf_reference(640, 360, after_sleep=0.5)

        self.assertEqual([(0.5, 0.5, {"after_sleep": 0.5})], calls)

    def test_quick_hunt_resource_zero_uses_mf_top_right_roi(self):
        task = object.__new__(DailyTask)
        task.info_set = lambda *_args, **_kwargs: None
        calls = []

        class FakeVision:
            def ocr_text(self, _frame, name, roi):
                calls.append((name, roi))
                return "0 / 60"

        task._quick_vision = lambda: FakeVision()
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)

        self.assertTrue(task._quick_hunt_resource_empty("米饭"))
        self.assertEqual([("米饭数量", (1040, 33, 111, 27))], calls)

    def test_quick_hunt_red_dot_returns_scaled_component_center(self):
        task = object.__new__(DailyTask)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[172:180, 116:124] = (0, 0, 255)

        point = task._quick_hunt_red_dot(frame)

        self.assertIsNotNone(point)
        self.assertTrue(116 <= point[0] <= 123)
        self.assertTrue(167 <= point[1] <= 174)

    def test_quick_hunt_mf_scheduler_falls_back_to_hunting_ground(self):
        task = object.__new__(DailyTask)
        task.config = {
            "快速狩猎狩猎场": True,
            "快速狩猎冒险航线": True,
            "快速狩猎米饭分配": "狩猎场x1 / 双倍图MAX",
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        calls = []
        task._quick_hunt_resource_empty = lambda _resource: False
        task._quick_hunt_reset_map = lambda: calls.append("reset") or True
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
                "reset",
                "adventure-no-double",
                "reset",
                "hunt",
                ("狩猎场兜底", "MAX"),
            ],
            calls,
        )

    def test_quick_hunt_run_dispatches_rice_then_crystal_and_returns_home(self):
        task = object.__new__(DailyTask)
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

    def test_quick_hunt_box_enabled_rejects_dark_text(self):
        box = SimpleNamespace(x=2, y=2, width=6, height=6)
        dark = np.zeros((10, 10, 3), dtype=np.uint8)
        light = dark.copy()
        light[2:8, 2:8] = 255

        self.assertFalse(DailyTask._quick_hunt_box_enabled(dark, box))
        self.assertTrue(DailyTask._quick_hunt_box_enabled(light, box))

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
