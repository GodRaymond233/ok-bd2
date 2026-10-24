import re
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from ok.task.exceptions import TaskDisabledException
from ok.util.config import Config

from src.tasks.DailyTask import (
    BUSINESS_COLLECT_KEYWORDS,
    GUILD_FINISHED_TEMPLATE,
    GUILD_MAIN_ACTIVE_TEMPLATE,
    GUILD_MAIN_FINISHED_TEMPLATE,
    GUILD_PAGE_KEYWORDS,
    GUILD_SIGNUP_SUCCESS_TEMPLATE,
    GUILD_SUCCESS_KEYWORDS,
    GUILD_TEMPLATE,
    MY_HOME_TEMPLATE,
    MY_HOME_TITLE_RELATIVE_ROI,
    BD2BusinessCollectTestTask,
    DailyTask,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.map_trade.vision import Vision
from src.tasks.quick_hunt import (
    QUICK_HUNT_ADVENTURE_LABEL_PATTERNS,
    QUICK_HUNT_ADVENTURE_LIST_ROI,
    QUICK_HUNT_ADVENTURE_MAP_PATTERNS,
    QUICK_HUNT_BUTTON_ROI,
    QUICK_HUNT_COUNT_ROI,
    QUICK_HUNT_CRYSTAL_CLICK_ROI,
    QUICK_HUNT_CRYSTAL_ENTRY_PATTERN,
    QUICK_HUNT_CRYSTAL_POINT,
    QUICK_HUNT_CRYSTAL_TITLE_ROI,
    QUICK_HUNT_DIALOG_ROI,
    QUICK_HUNT_DOUBLE_ROI,
    QUICK_HUNT_DOUBLE_TEMPLATE,
    QUICK_HUNT_ENTRY_POINT,
    QUICK_HUNT_MAP_SCAN_ROI,
    QUICK_HUNT_MENU_FOREGROUND_ROI,
    QUICK_HUNT_RED_POINT,
    QUICK_HUNT_RESOURCE_CAPACITIES,
    QUICK_HUNT_RESOURCE_ROI,
    QUICK_HUNT_RETURN_POINT,
    QUICK_HUNT_REWARD_ROI,
    QUICK_HUNT_REWARD_TITLE_ROI,
    QUICK_HUNT_START_ROI,
    QUICK_HUNT_STONE_COUNT_ROI,
    QUICK_HUNT_STONE_LIST_ROI,
    HuntDialog,
)
from src.tasks.quick_hunt_plan import legacy_plan, select_steps
from src.tasks.QuickHuntTask import QuickHuntTask
from src.tasks.task_vision_mixin import (
    LOADING_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
)
from src.utils.image_utils import crop_relative
from tests.helpers import StageHistoryTestCase


def hunt_dialog(**changes):
    return replace(HuntDialog(
        "哥布林遗迹极难", 1, 6, 90, "米饭", "on", (1140, 350),
        (1050, 785), (1080, 1920, 3),
    ), **changes)


class DailyTaskHelperTest(StageHistoryTestCase):
    def test_member_page_without_new_reward_is_accepted_but_other_pages_are_not(self):
        normal = "公告事项 公会商店 进入公会联合战 公会成员 退出 请检查网络连接"
        for text, expected in (
            (normal, True), ("我的小屋 公会 抽抽乐", False),
            ("搜索公会 公会商店 公告事项 申请加入", False),
            (normal + " 确认", False),
        ):
            with self.subTest(text=text):
                task = object.__new__(DailyTask)
                task.capture_frame = Mock(return_value=None)
                task._ocr_text = Mock(return_value=text)
                task._status_set = Mock()
                task.sleep = Mock()
                with patch("src.tasks.DailyTask.monotonic", side_effect=[0, 0, 4]):
                    self.assertEqual(expected, task._wait_for_existing_guild_visit())

    def test_daily_failure_keeps_failed_item_and_continues_from_confirmed_home(self):
        task = object.__new__(DailyTask)
        task.config = {}
        task.info_set = Mock()
        task.log_info = Mock()
        task.log_error = Mock()
        task.log_completion = Mock()
        task.run_guild_sign_in = Mock(return_value=False)
        task.run_my_home_sign_in = Mock(return_value=True)
        task.run_business_collect = Mock(return_value=True)
        task.recover_daily_home = Mock(return_value=True)
        task._save_flow_diagnostic = Mock(
            side_effect=lambda _name: task.recover_daily_home.assert_not_called(),
        )
        self.assertFalse(task.run())
        task.run_my_home_sign_in.assert_called_once()
        task.run_business_collect.assert_called_once()
        task.info_set.assert_any_call("失败", "['公会签到']")

    def test_stop_preserves_done_substep_for_incomplete_resume(self):
        from ok.task.exceptions import TaskDisabledException

        from src.tasks import run_history
        task = object.__new__(DailyTask)
        task.config = {"_仅执行未完成步骤": True}
        task.info_set = Mock()
        task.log_info = Mock()
        task.log_error = Mock()
        task.log_completion = Mock()
        task.recover_daily_home = Mock(return_value=True)
        task.run_guild_sign_in = Mock(return_value=True)
        task.run_my_home_sign_in = Mock(side_effect=TaskDisabledException())
        task.run_business_collect = Mock(return_value=True)
        with self.assertRaises(TaskDisabledException):
            task.run()
        self.assertEqual("done", run_history.read_stage(task, "公会签到")["state"])
        task.recover_daily_home.return_value = False
        self.assertFalse(task.run())
        task.run_guild_sign_in.assert_called_once()
        task.recover_daily_home.return_value = True
        task.run_my_home_sign_in = Mock(return_value=True)
        self.assertTrue(task.run())
        task.run_guild_sign_in.assert_called_once()

    def test_hunt_pending_submission_never_starts_again_on_unknown_result(self):
        from src.tasks import run_history
        task = object.__new__(QuickHuntTask)
        task.config = {}
        task._status_set = Mock()
        task.log_info = Mock()
        task._quick_hunt_free_resource = Mock(return_value=84)
        task._quick_hunt_wait_result = Mock(return_value="failed")
        task._quick_vision = Mock()
        run_history.save_stage(task, "狩猎提交:米饭:狩猎场", "pending",
                               evidence={"pool": 90, "cost": 6})
        self.assertEqual("failed", task._quick_hunt_execute_current_map("MIN", "狩猎场"))
        task._quick_vision.assert_not_called()
        self.assertEqual("pending", run_history.read_stage(task, "狩猎提交:米饭:狩猎场")["state"])
        self.assertEqual("failed", task._quick_hunt_execute_current_map("MAX", "冒险航线"))
        task._quick_vision.assert_not_called()
        task._quick_hunt_free_resource.return_value = 0
        self.assertEqual("depleted", task._quick_hunt_execute_current_map("MIN", "狩猎场"))
        self.assertEqual("done", run_history.read_stage(task, "狩猎提交:米饭:狩猎场")["state"])

    def test_confirmed_hunt_min_survives_downstream_failure_without_replay(self):
        from src.tasks import run_history
        task = object.__new__(QuickHuntTask)
        task.config = {}
        task._status_set = Mock()
        task.log_info = Mock()
        task._quick_vision = Mock()
        run_history.save_stage(task, "狩猎提交:米饭:狩猎场", "done")
        self.assertEqual("done", task._quick_hunt_execute_current_map("MIN", "狩猎场"))
        task._quick_vision.assert_not_called()

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
            "快速狩猎刷取方案",
            "快速狩猎圣石洞穴",
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
            def ocr_text(self, _frame, name, relative_roi=None, **_kwargs):
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
        text = "签到 成功\n奖励已发放至邮箱"

        self.assertEqual(
            2,
            DailyTask._keyword_match_count(
                text,
                [*GUILD_SUCCESS_KEYWORDS, "不存在"],
            ),
        )

    def test_business_collect_keywords_ignore_traditional_frames(self):
        # BUG-20260829-06 的繁体服帧：2026-08-29 起取消繁体识别，
        # 仅「取消」繁简同形可命中（1 < minimum_matches=2，判定失败为预期）。
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
                1,
                DailyTask._keyword_match_count(frame, BUSINESS_COLLECT_KEYWORDS),
            )

    def test_business_collect_keywords_match_simplified_client_ocr_frames(self):
        # BUG-20260829-011：RPT-20260829-190031 国服简体客户端两帧失败 OCR 的弹窗相关
        # 原文。「一键获得」被 OCR 漏读首字成「键获得」，靠其余弹窗文案命中。
        cn_frame_one = (
            "永远DE刹那 X269K 124,565 39,784,979 154,429 ！ Q D-4 我的小屋 好友 公会 "
            "24:00:00 经营管理 格鲁TALK D-24 X Lv.30餐馆营业额现状 亲 立刻前往 战术教材 "
            "通行证 快速狩 ● 累计奖励 24:00:00 99360 2592 结算 心契之约 "
            "LV.3 渔笼收获情况 立刻前往 LV.28 钓鱼 5782/17700(32%) 获得经验值+1027 "
            "● 累计奖励 D-4 24:00:00 回收 助手工作情况 立刻前往 •• ©累计奖励 0 D-11 "
            "24:00:00 12 16 获得 TAME 取消 键获得 普通战斗解锁 Evil Castle"
        )
        cn_frame_two = cn_frame_one.replace(
            "12 16 获得 TAME 取消 键获得", "获得 VITTAMER 取消 键获得"
        )

        for frame in (cn_frame_one, cn_frame_two):
            count = DailyTask._keyword_match_count(frame, BUSINESS_COLLECT_KEYWORDS)
            self.assertEqual(4, count)
            # run_business_collect 的 minimum_matches=2 门槛。
            self.assertGreaterEqual(count, 2)

    def test_business_collect_keywords_do_not_match_home_screen(self):
        # BUG-20260829-011：主页画面（含经营管理入口文字）不得误命中弹窗关键字。
        home_frame = (
            "D-4 我的小屋 好友 公会联合战 0 0 24:00:00 经营管理 格鲁TALK 亲密度 街机 儿游戏"
        )

        self.assertEqual(
            0,
            DailyTask._keyword_match_count(home_frame, BUSINESS_COLLECT_KEYWORDS),
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
        task._save_flow_diagnostic = Mock()
        task.recover_daily_home = lambda: False
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

        self.assertFalse(DailyTask.run_guild_sign_in(task))
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

        self.assertFalse(DailyTask.run_guild_sign_in(task))
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

        self.assertFalse(DailyTask.run_guild_sign_in(task))
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

        self.assertFalse(DailyTask.run_guild_sign_in(task))
        self.assertEqual([(370, 155), (100, 50)], clicks)

    def test_guild_success_template_on_home_frame_is_rejected(self):
        # BUG-20260901-02：RPT-20260901-233554 中 0.771 的签到成功模板在
        # 主页帧越过 0.76 阈值，脚本未进公会就判定签到成功；主页三信号
        # 通过的帧不得算模板命中。
        task = object.__new__(DailyTask)
        task.config = {"loading 出现等待秒数": 0.5}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: object()
        task._ocr_text = lambda _frame, name: ""
        task._home_confirmation_signals = lambda _frame, _name: (True, 3, 255.0, "抽抽乐")
        task.sleep = lambda *_args, **_kwargs: None

        def fake_match(_frame, spec):
            if spec is GUILD_SIGNUP_SUCCESS_TEMPLATE:
                return MatchResult(0.771, (0, 0), (1, 1), pixel_score=0.771)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match

        with patch(
            "src.tasks.task_vision_mixin.monotonic",
            side_effect=[0.0, 0.1, 10.0],
        ):
            state, found, _text = DailyTask._wait_loading_or_template_or_ocr(
                task,
                "公会签到",
                GUILD_SIGNUP_SUCCESS_TEMPLATE,
                GUILD_SUCCESS_KEYWORDS,
                name="guild_sign_in_early",
                reject_template_on_home=True,
            )

        self.assertEqual("none", state)
        self.assertFalse(found)

    def test_guild_success_template_on_guild_popup_frame_is_accepted(self):
        # 同一模板在非主页帧（真实弹窗）必须照常命中。
        task = object.__new__(DailyTask)
        task.config = {"loading 出现等待秒数": 0.5}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: object()
        task._ocr_text = lambda _frame, name: ""
        task._home_confirmation_signals = lambda _frame, _name: (False, 0, 120.0, "-")
        task.sleep = lambda *_args, **_kwargs: None

        def fake_match(_frame, spec):
            if spec is GUILD_SIGNUP_SUCCESS_TEMPLATE:
                return MatchResult(0.771, (0, 0), (1, 1), pixel_score=0.771)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match

        with patch(
            "src.tasks.task_vision_mixin.monotonic",
            side_effect=[0.0, 0.1],
        ):
            state, found, _text = DailyTask._wait_loading_or_template_or_ocr(
                task,
                "公会签到",
                GUILD_SIGNUP_SUCCESS_TEMPLATE,
                GUILD_SUCCESS_KEYWORDS,
                name="guild_sign_in_early",
                reject_template_on_home=True,
            )

        self.assertEqual("target", state)
        self.assertTrue(found)

    def test_guild_return_home_retries_back_click_while_guild_page_detected(self):
        # BUG-20260901-02：返回键被切页动画吞掉时，只要全帧 OCR 仍读到公会
        # 页面关键字就补点返回键，最多 3 次。
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: object()
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task._save_flow_diagnostic = lambda _name: self.assertEqual(1, len(clicks))

        def fake_match(_frame, spec):
            if spec is GUILD_TEMPLATE:
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._ocr_text = lambda _frame, name: "公告事项 进入公会联合战 公会商店"
        task._keyword_match_count = lambda text, keywords: 3
        # 依次为：入口前主页确认、返回主页第 1/2/3 次确认。
        wait_results = iter([True, False, False, True])
        waited_names = []
        task._wait_for_home_confirmation = (
            lambda name, **_kwargs: waited_names.append(name) or next(wait_results)
        )

        self.assertFalse(DailyTask.run_guild_sign_in(task))
        self.assertEqual(
            [(370, 155), (100, 50), (100, 50), (100, 50)],
            clicks,
        )
        # 入口前 1 次 + 返回主页 3 次。
        self.assertEqual(4, len(waited_names))

    def test_guild_return_home_stops_retrying_without_guild_keywords(self):
        # 返回后既不是主页也没有公会页面关键字（未知页面）时不得盲点返回键，
        # 避免在别的页面上误点。
        task = object.__new__(DailyTask)
        task.config = {"公会入口阈值": 0.78}
        task.capture_frame = lambda: object()
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task._save_flow_diagnostic = lambda _name: self.assertEqual(1, len(clicks))

        def fake_match(_frame, spec):
            if spec is GUILD_TEMPLATE:
                return MatchResult(0.9, (0, 0), (1, 1), pixel_score=0.9)
            return MatchResult(-1.0, (0, 0), (0, 0))

        task._match = fake_match
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template_or_ocr = lambda *_args, **_kwargs: ("none", False, "")
        task._wait_for_template_or_ocr = lambda *_args, **_kwargs: (False, "")
        task._ocr_text = lambda _frame, name: ""
        task._keyword_match_count = lambda text, keywords: 0
        # 依次为：入口前主页确认（通过）、返回主页第 1 次确认（失败）。
        confirm_results = iter([True, False])
        task._wait_for_home_confirmation = lambda name, **_kwargs: next(confirm_results)

        self.assertFalse(DailyTask.run_guild_sign_in(task))
        self.assertEqual([(370, 155), (100, 50)], clicks)

    def test_guild_page_keywords_match_report_page_ocr(self):
        # RPT-20260901-233554 AutoLoginTask 实测公会整页 OCR。
        guild_page = (
            "公会？ 57,652 公会商店 灵魂挽歌 聊天 √ 自动翻译 8 公会成员 "
            "230/30 立即加入 咱虽然养老， 但是还活着，定期 公告事项 "
            "首领防御战进行中。 进入公会联合战 请输入消息。"
        )
        self.assertEqual(3, DailyTask._keyword_match_count(guild_page, GUILD_PAGE_KEYWORDS))
        home_frame = (
            "D-4 我的小屋 好友 公会联合战 0 0 24:00:00 经营管理 格鲁TALK 亲密度 街机 儿游戏"
        )
        self.assertEqual(
            0,
            DailyTask._keyword_match_count(home_frame, GUILD_PAGE_KEYWORDS),
        )

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
        task.config = {"小屋页面等待秒数": 12.0, "小屋进入最大点击次数": 3}
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template = lambda *_args, **_kwargs: ("none", False)
        task._wait_for_template = lambda *_args, **_kwargs: True
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task._frame_confirms_home = lambda *_args, **_kwargs: True

        self.assertTrue(DailyTask.run_my_home_sign_in(task))
        # 转场可能吞点击：小屋入口会补点 3 次（默认上限），确认进入后再返回主页。
        self.assertEqual([(166, 158)] * 3 + [(100, 50)], clicks)

    def test_my_home_retry_stops_when_capture_fails(self):
        task = object.__new__(DailyTask)
        task.config = {"小屋页面等待秒数": 1.0, "小屋进入最大点击次数": 3}
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template = lambda *_args, **_kwargs: ("none", False)
        task._wait_for_template = lambda *_args, **_kwargs: False
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task._frame_confirms_home = lambda *_args, **_kwargs: True

        def boom_capture():
            raise RuntimeError("capture failed")

        task.capture_frame = boom_capture

        self.assertFalse(DailyTask.run_my_home_sign_in(task))
        # 截图失败 -> 不补点，只点了一次入口
        self.assertEqual([(166, 158)], clicks)

    def test_my_home_retry_stops_when_no_longer_on_home(self):
        task = object.__new__(DailyTask)
        task.config = {"小屋页面等待秒数": 1.0, "小屋进入最大点击次数": 3}
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
        task._wait_loading_or_template = lambda *_args, **_kwargs: ("none", False)
        task._wait_for_template = lambda *_args, **_kwargs: False
        task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
        task._frame_confirms_home = lambda *_args, **_kwargs: False
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)

        self.assertFalse(DailyTask.run_my_home_sign_in(task))
        # 已离开主页 -> 不补点
        self.assertEqual([(166, 158)], clicks)

    def test_my_home_sign_in_recovers_from_template_miss_with_header_ocr(self):
        for title, expected in (("我的小屋", True), ("其他页面", False)):
            with self.subTest(title=title):
                task = object.__new__(DailyTask)
                task.config = {"小屋页面等待秒数": 12.0}
                task.log_info = lambda *_args, **_kwargs: None
                task._status_set = lambda *_args, **_kwargs: None
                task._sleep_after_recognition = lambda: None
                task.sleep = lambda *_args, **_kwargs: None
                task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
                task._wait_loading_or_template = lambda *_args, **_kwargs: ("none", False)
                task._wait_for_template = lambda *_args, **_kwargs: False
                task._wait_for_home_confirmation = lambda *_args, **_kwargs: True
                task._frame_confirms_home = lambda *_args, **_kwargs: False
                clicks = []
                task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
                ocr_calls = []

                def ocr_text(_frame, _name, **kwargs):
                    ocr_calls.append(kwargs)
                    return title

                task._quick_vision = lambda: SimpleNamespace(ocr_text=ocr_text)

                self.assertIs(expected, DailyTask.run_my_home_sign_in(task))
                self.assertEqual(
                    [(166, 158), (100, 50)] if expected else [(166, 158)], clicks
                )
                self.assertEqual(1 if expected else 3, len(ocr_calls))
                self.assertEqual((0.11, 0.01, 0.25, 0.10), ocr_calls[0]["relative_roi"])

    def test_my_home_title_ocr_region_scales_with_client(self):
        for width, height, expected in (
            (1920, 1080, (211, 11, 269, 97)),
            (1280, 720, (141, 7, 179, 65)),
        ):
            with self.subTest(size=(width, height)):
                crops = []

                def ocr(*, frame, **_kwargs):
                    crops.append(frame.shape[:2])
                    return [SimpleNamespace(name="我的小屋", x=0, y=0, width=10, height=10)]

                task = SimpleNamespace(
                    config={"日常 OCR 阈值": 0.2},
                    ocr_threshold_key="日常 OCR 阈值",
                    ocr=ocr,
                    info_set=lambda *_args, **_kwargs: None,
                )
                frame = np.zeros((height, width, 3), dtype=np.uint8)
                boxes = Vision(task).ocr_boxes(
                    frame, "小屋页面标题", relative_roi=MY_HOME_TITLE_RELATIVE_ROI,
                    target_height=0,
                )

                self.assertEqual([(expected[3], expected[2])], crops)
                self.assertEqual((expected[0], expected[1]), (boxes[0].x, boxes[0].y))

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

    def test_loading_wait_keeps_home_template_rejection_after_loading(self):
        task = object.__new__(DailyTask)
        task.config = {"loading 出现等待秒数": 1.0, "loading 消失等待秒数": 1.0}
        task.capture_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task._ocr_text = lambda *_args, **_kwargs: ""
        task._frame_confirms_home = lambda *_args, **_kwargs: True
        task._passes = lambda result, _spec: result.score >= 0.8
        calls = []

        def fake_match(_frame, spec):
            calls.append(spec.name)
            if spec is LOADING_TEMPLATE:
                loading_seen = calls.count(LOADING_TEMPLATE.name)
                return MatchResult(0.9 if loading_seen == 1 else -1.0, (0, 0), (1, 1))
            target_seen = calls.count(GUILD_SIGNUP_SUCCESS_TEMPLATE.name)
            return MatchResult(0.9 if target_seen == 2 else -1.0, (0, 0), (1, 1))

        task._match = fake_match
        with patch(
            "src.tasks.task_vision_mixin.monotonic",
            side_effect=[0.0, 0.1, 0.2, 0.3],
        ):
            state, found, _text = DailyTask._wait_loading_or_template_or_ocr(
                task,
                "公会签到",
                GUILD_SIGNUP_SUCCESS_TEMPLATE,
                GUILD_SUCCESS_KEYWORDS,
                name="guild_sign_in_after_loading",
                reject_template_on_home=True,
            )

        self.assertEqual(("loading", False), (state, found))
        self.assertEqual(
            [
                GUILD_SIGNUP_SUCCESS_TEMPLATE.name,
                LOADING_TEMPLATE.name,
                GUILD_SIGNUP_SUCCESS_TEMPLATE.name,
                LOADING_TEMPLATE.name,
            ],
            calls,
        )

    @staticmethod
    def _business_controls(width=1280, height=720, row=0.8, claim=True, cancel=True):
        def box(name, x, y):
            return SimpleNamespace(
                name=name, x=(x - 0.015) * width, y=(y - 0.015) * height,
                width=0.03 * width, height=0.03 * height,
            )
        boxes = [box("餐馆营业额现状", 0.45, 0.3), box("助手工作情况", 0.45, 0.6)]
        if claim:
            boxes.append(box("一键获得", 0.6, row))
        if cancel:
            boxes.append(box("取消", 0.43, row))
        return boxes

    def _business_flow_task(self, frames):
        task = object.__new__(DailyTask)
        task.config = {"一键收菜菜单等待秒数": 8.0}
        statuses, clicks, reference_clicks, clock = {}, [], [], [0.0]
        task.info_set = lambda key, value: statuses.__setitem__(key, value)
        task.log_info = Mock()
        task._wait_for_ocr_keywords = lambda *_args, **_kwargs: (True, "一键获得 取消")
        task._save_flow_diagnostic = Mock()
        task._collect_business_guest_stones = Mock(return_value=True)
        task.recover_daily_home = Mock(return_value=True)
        task._click_reference = lambda x, y, **_kwargs: reference_clicks.append((x, y))
        task.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        task.operate_click = lambda x, y, after_sleep=0: (
            clicks.append((round(x, 6), round(y, 6))), task.sleep(after_sleep)
        )
        current = [None]
        pending = list(frames) if not callable(frames) else None

        def capture():
            if callable(frames):
                current[0] = frames(clicks, clock[0])
            elif pending:
                current[0] = pending.pop(0)
            return current[0][0]

        task.capture_frame = capture
        task._wait_for_home_confirmation = lambda name: (
            True if "入口前" in name else current[0][2]
        )
        task.ocr = lambda **_kwargs: current[0][1]
        task._frame_confirms_home = lambda *_args: current[0][2]
        timer = patch("src.tasks.DailyTask.monotonic", side_effect=lambda: clock[0])
        timer.start()
        self.addCleanup(timer.stop)
        return task, statuses, clicks, reference_clicks, clock

    def test_business_collect_dismisses_reward_and_keeps_menu_for_restaurant(self):
        for width, height in ((1280, 720), (1920, 1080)):
            with self.subTest(resolution=(width, height)):
                frame = np.full((height, width, 3), 230, dtype=np.uint8)
                reward = SimpleNamespace(
                    name="点击画面即可返回", x=0.45 * width, y=0.88 * height,
                    width=0.1 * width, height=0.04 * height,
                )
                task, statuses, clicks, refs, _ = self._business_flow_task([
                    (frame, self._business_controls(width, height, row=0.85), False),
                    (frame, [reward], False),
                    (frame, self._business_controls(width, height, row=0.72), False),
                    (frame, self._business_controls(width, height, row=0.76), False),
                    (frame, [], True),
                ])
                self.assertTrue(task.run_business_collect())
                self.assertEqual([(0.6, 0.85), (0.5, 0.9)], clicks)
                self.assertEqual([(165, 260)], refs)
                self.assertEqual("通过", statuses["一键收菜返回主页结果"])
                task._collect_business_guest_stones.assert_called_once()
                task.recover_daily_home.assert_called_once()

    def test_business_collect_unread_disabled_button_uses_cancel_row(self):
        frame = np.full((720, 1280, 3), 220, dtype=np.uint8)
        boxes = self._business_controls(row=0.79, claim=False)
        task, _, clicks, _, _ = self._business_flow_task([
            (frame, boxes, False), (frame, boxes, False), (frame, boxes, False),
            (frame, [], True),
        ])
        cancel = boxes[-1]
        bounds = task._business_claim_bounds(frame, boxes, cancel)
        x, y, width, height = bounds
        frame[int(y):int(y + height), int(x):int(x + width)] = (80, 83, 81)
        self.assertTrue(task.run_business_collect())
        self.assertEqual([(round(0.43 + (1090 - 832) / 1920, 6), 0.79)], clicks)

    def test_business_collect_lost_claim_does_not_become_success_after_closing(self):
        frame = np.full((720, 1280, 3), 230, dtype=np.uint8)
        popup = self._business_controls()
        task, statuses, clicks, _, _ = self._business_flow_task(
            lambda actions, _time: (frame, [], True) if (0.43, 0.8) in actions
            else (frame, popup, False)
        )
        self.assertFalse(task.run_business_collect())
        self.assertEqual([(0.6, 0.8), (0.6, 0.8), (0.43, 0.8)], clicks)
        self.assertEqual("未确认", statuses["一键收菜领取结果"])
        self.assertEqual("通过", statuses["一键收菜返回主页结果"])
        task._collect_business_guest_stones.assert_not_called()
        self.assertEqual(
            ["business_collect_claim_failed", "business_collect_failed"],
            [call.args[0] for call in task._save_flow_diagnostic.call_args_list],
        )

    def test_business_collect_late_reward_prevents_second_claim(self):
        frame = np.full((720, 1280, 3), 230, dtype=np.uint8)
        popup = self._business_controls()
        reward = SimpleNamespace(name="点击画面即可返回", x=576, y=633.6, width=128, height=28.8)

        def screen(actions, now):
            if (0.43, 0.8) in actions:
                return frame, [], True
            if (0.5, 0.9) in actions:
                return frame, popup, False
            return (frame, [reward], False) if now >= 4.0 else (frame, popup, False)

        task, _, clicks, _, _ = self._business_flow_task(screen)
        self.assertTrue(task.run_business_collect())
        self.assertEqual([(0.6, 0.8), (0.5, 0.9)], clicks)

    def test_business_collect_unknown_screen_has_no_blind_clicks(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        task, _, clicks, refs, clock = self._business_flow_task([(frame, [], False)])
        self.assertFalse(task.run_business_collect())
        self.assertEqual([], clicks)
        self.assertEqual([(165, 260)], refs)
        self.assertLess(clock[0], 21)

    def test_business_collect_caps_popup_close_attempts(self):
        frame = np.full((720, 1280, 3), 230, dtype=np.uint8)
        popup = self._business_controls()
        reward = SimpleNamespace(name="点击画面即可返回", x=576, y=633.6, width=128, height=28.8)
        task, _, clicks, _, _ = self._business_flow_task([
            (frame, popup, False), (frame, [reward], False),
        ])
        self.assertFalse(task.run_business_collect())
        self.assertEqual(9, len(clicks))  # 一次领取，奖励层最多八次关闭。
        self.assertEqual(8, clicks.count((0.5, 0.9)))
        task._collect_business_guest_stones.assert_not_called()

    def test_business_collect_limits_fixed_close_to_confirmed_popup(self):
        frame = np.full((720, 1280, 3), 230, dtype=np.uint8)
        task, _, clicks, _, _ = self._business_flow_task([
            (frame, self._business_controls(), False),
            (frame, self._business_controls(cancel=False), False),
        ])
        self.assertFalse(task.run_business_collect())
        self.assertEqual(2, clicks.count((round(832 / 1920, 6), round(814 / 1080, 6))))

    def test_business_collect_dim_or_coloured_button_does_not_prove_claim(self):
        for background, button in ((90, (35, 35, 35)), (230, (40, 100, 130))):
            with self.subTest(background=background, button=button):
                frame = np.full((720, 1280, 3), background, dtype=np.uint8)
                popup = self._business_controls()
                claim = popup[-2]
                frame[int(claim.y):int(claim.y + claim.height),
                      int(claim.x):int(claim.x + claim.width)] = button
                task, _, _, _, _ = self._business_flow_task(
                    lambda actions, _time: (frame, [], True) if (0.43, 0.8) in actions
                    else (frame, popup, False)
                )
                self.assertFalse(task.run_business_collect())

    def test_quick_hunt_regions_preserve_all_supplied_1920_calibrations(self):
        self.assertEqual({"米饭": 90, "火把": 60}, QUICK_HUNT_RESOURCE_CAPACITIES)
        self.assertEqual(
            (1580 / 1920, 38 / 1080, 1800 / 1920, 80 / 1080),
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
        task = QuickHuntTask(SimpleNamespace(scene=None), None)
        task.config = {"主页压暗阈值": 185.0}
        left_text = ["我的小屋 经营管理格鲁TALK 街机游戏"]
        gacha_text = ["抽抽乐"]

        class FakeVision:
            def ocr_text(self, _frame, name, relative_roi=None, **_kwargs):
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
        self.assertEqual([([r"^快速狩猎$"], None, 2.0, "主页快速狩猎入口")], click_ocr_calls)
        self.assertEqual([], clicks)
        self.assertEqual([r"狩猎场"], ocr_calls[0][0])
        self.assertIsNone(ocr_calls[0][1])
        self.assertEqual(4.0, ocr_calls[0][2])
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
        plan = legacy_plan({
            "快速狩猎双倍策略": "优先双倍",
            "快速狩猎资源倾向": "经验",
        })
        _, steps = select_steps(plan, {"金币": True, "经验": True})
        self.assertEqual("gold", steps[-1]["target"])

    def test_quick_hunt_ignore_double_always_selects_gold(self):
        plan = legacy_plan({
            "快速狩猎双倍策略": "忽视双倍",
            "快速狩猎资源倾向": "经验",
        })
        _, steps = select_steps(plan, {})
        self.assertEqual("gold", steps[-1]["target"])

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
        task._quick_hunt_free_rice = lambda: 90
        task._quick_hunt_plan_double_states = lambda: {"金币": False, "经验": False}
        task._quick_hunt_select_hunting_ground = lambda: self.fail(
            "current default hunting ground must not be changed"
        )
        task._quick_hunt_select_plan_target = lambda target: calls.append(target) or True
        task._quick_hunt_execute_current_map = (
            lambda mode, stage, **kwargs: calls.append(
                (mode, kwargs.get("expected_map_pattern"))
            ) or "done"
        )

        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(
            [
                "hunt", ("MIN", None),
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
        task._quick_hunt_free_rice = lambda: 6
        task._quick_hunt_plan_double_states = lambda: {"金币": True, "经验": False}
        calls = []
        task._quick_hunt_click_adventure = (
            lambda resource: calls.append(("reselect", resource)) or True
        )
        results = iter(("wrong_map", "done"))
        task._quick_hunt_execute_current_map = (
            lambda mode, stage, expected_map_pattern=None, **kwargs: calls.append(
                (mode, expected_map_pattern)
            )
            or next(results)
        )

        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(
            [
                ("reselect", "金币"),
                ("MAX", r"哥布林遗迹"),
                ("reselect", "金币"),
                ("MAX", r"哥布林遗迹"),
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
        task._quick_hunt_free_rice = lambda: 12
        task._quick_hunt_select_plan_target = lambda target: target == "hunt"
        task._quick_hunt_select_hunting_ground = lambda: self.fail(
            "current default hunting ground must not be changed"
        )
        calls = []
        task._quick_hunt_execute_current_map = (
            lambda mode, stage, **kwargs: calls.append(mode) or "done"
        )
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(["MAX"], calls)

    def test_quick_hunt_stone_counts_follow_top_to_bottom_element_order(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 1.0}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.sleep = Mock()
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
            def ocr_boxes(self, _frame, _name, relative_roi=None, ocr_scale=1.0):
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
        task._quick_hunt_free_resource = Mock(return_value=60)
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
            lambda mode, stage, **kwargs: executions.append((mode, stage, kwargs)) or "done"
        )

        self.assertTrue(task._quick_hunt_run_crystal_cave())
        self.assertEqual([], clicks)
        self.assertEqual(
            ([QUICK_HUNT_CRYSTAL_ENTRY_PATTERN], QUICK_HUNT_CRYSTAL_CLICK_ROI, "圣石洞穴入口"),
            selected[0],
        )
        self.assertIn("光", selected[1][0][0])
        self.assertEqual(QUICK_HUNT_CRYSTAL_TITLE_ROI, selected[1][1])
        self.assertEqual(("MAX", "光属性圣石"), executions[0][:2])
        self.assertEqual("火把", executions[0][2]["resource"])
        self.assertIsNone(executions[0][2].get("max_cost"))
        pattern = executions[0][2]["expected_map_pattern"]
        self.assertRegex("光之洞穴", pattern)
        self.assertIsNone(re.search(pattern, "火之洞穴"))

    def test_quick_hunt_crystal_entry_falls_back_to_reference_point(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: object()
        task._quick_hunt_ocr_text = lambda _frame, _roi, name: "圣石洞空"
        clicks = []
        task._click_reference = lambda x, y, **kwargs: clicks.append((x, y, kwargs))
        task._quick_hunt_click_ocr = lambda *_args, **_kwargs: False
        confirm_calls = []

        def fake_wait_ocr(patterns, roi, timeout, name):
            confirm_calls.append((patterns, roi, timeout, name))
            return ("火之洞穴 水之洞穴 风之洞穴 光之洞穴 暗之洞穴", SimpleNamespace())

        task._quick_hunt_wait_ocr = fake_wait_ocr
        task._quick_hunt_free_resource = lambda _resource: 0
        task._quick_hunt_menu_still_open = lambda: True

        self.assertTrue(task._quick_hunt_run_crystal_cave())
        self.assertEqual([(177, 449, {"after_sleep": 0.8})], clicks)
        self.assertEqual(1, len(confirm_calls))
        self.assertEqual(
            ([r"[火水风光暗].?洞穴"], QUICK_HUNT_CRYSTAL_TITLE_ROI), confirm_calls[0][:2]
        )

    def test_quick_hunt_crystal_unknown_torches_cannot_start(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎圣石目标": "水"}
        task.info_set = Mock()
        task.log_info = Mock()
        task._quick_hunt_enter_crystal_cave = Mock(return_value=True)
        task._quick_hunt_free_resource = Mock(return_value=None)
        task._quick_hunt_click_ocr = Mock()
        task._quick_hunt_execute_current_map = Mock()

        self.assertFalse(task._quick_hunt_run_crystal_cave())
        task._quick_hunt_free_resource.assert_called_once_with("火把")
        task._quick_hunt_click_ocr.assert_not_called()
        task._quick_hunt_execute_current_map.assert_not_called()

    def test_quick_hunt_crystal_retries_when_panel_confirmation_fails(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        logs = []
        task.log_info = lambda message, *_args, **_kwargs: logs.append(message)
        entry_clicks = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, _timeout, name: entry_clicks.append((patterns, roi))
            or True
        )
        confirm_results = iter([("", None), ("", None), ("火之洞穴 水之洞穴", None)])
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: next(confirm_results)
        task._quick_hunt_free_resource = lambda _resource: 0

        self.assertTrue(task._quick_hunt_run_crystal_cave())
        self.assertEqual(3, len(entry_clicks))
        self.assertTrue(
            all(roi == QUICK_HUNT_CRYSTAL_CLICK_ROI for _patterns, roi in entry_clicks)
        )
        self.assertEqual(2, sum("重试" in message for message in logs))

    def test_quick_hunt_crystal_fails_after_exhausted_retries_without_torch_check(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: object()
        task._quick_hunt_ocr_text = lambda _frame, _roi, name: ""
        task._quick_hunt_click_ocr = lambda *_args, **_kwargs: False
        task._click_reference = lambda *args, **kwargs: None
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: ("", None)
        task._save_flow_diagnostic = lambda *_args, **_kwargs: None
        resource_checks = []
        task._quick_hunt_free_resource = lambda resource: resource_checks.append(resource) or 0
        task._quick_hunt_menu_still_open = lambda: True

        self.assertFalse(task._quick_hunt_run_crystal_cave())
        self.assertEqual([], resource_checks)

    def test_quick_hunt_crystal_entry_falls_back_to_full_frame_ocr(self):
        # RPT-20260902-225925：实机 CLICK_ROI 条带 OCR 连续全空但整屏菜单
        # 可读到"圣石洞穴"，入口可能偏移到条带外；条带未命中应整屏兜底，
        # 命中后不得再回退固定参考点。
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        entry_attempts = []

        def click_ocr(_patterns, roi, _timeout, **_kwargs):
            entry_attempts.append(roi)
            return roi is None

        task._quick_hunt_click_ocr = click_ocr
        task._click_reference = lambda *_args, **_kwargs: self.fail(
            "full-frame OCR hit must not use the fixed-coordinate fallback"
        )
        task._quick_hunt_wait_ocr = (
            lambda *_args, **_kwargs: ("火之洞穴 水之洞穴 光之洞穴", None)
        )
        task._quick_hunt_free_resource = lambda _resource: 0

        self.assertTrue(task._quick_hunt_run_crystal_cave())
        self.assertEqual(
            [QUICK_HUNT_CRYSTAL_CLICK_ROI, None], entry_attempts,
        )

    def test_quick_hunt_crystal_entry_pattern_matches_garbled_ocr_last_char(self):
        # BUG-20260901-03：实机与 20260724 录屏帧按任务管线复现，CLICK_ROI
        # 裁剪 OCR 稳定把"圣石洞穴"末字读成"空/究"，四字全匹配从未命中，
        # 识别点击路径实机从未生效；正则须放行前三字。
        for seen in ("圣石洞空", "圣石洞究", "圣石洞 究", "圣石洞穴"):
            value = QuickHuntTask._normalize_text(seen)
            self.assertTrue(
                re.search(QUICK_HUNT_CRYSTAL_ENTRY_PATTERN, value),
                f"entry pattern should match OCR text {seen!r}",
            )

    def test_quick_hunt_open_menu_retries_swallowed_entrance_click(self):
        # RPT-20260901-233554 23:32 一轮：入口点击被主页动画吞掉后单击定
        # 胜负直接失败；仍在主页时应补点入口，最多 3 次。
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task._wait_for_quick_hunt_home = lambda: True
        task.capture_frame = lambda: object()
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "OCR hit must not use the fixed-coordinate fallback"
        )
        entrance_clicks = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, timeout, name: entrance_clicks.append(name) or True
        )
        task._quick_hunt_home_signals = lambda _frame: (True, 3, 255.0, "抽抽乐")
        confirm_results = iter([("", None), ("", None), ("狩猎场 野猪洞穴", None)])
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: next(confirm_results)

        self.assertEqual("opened", task._quick_hunt_open_menu())
        self.assertEqual(3, len(entrance_clicks))

    def test_quick_hunt_open_menu_stops_retrying_when_left_home(self):
        # 点击后若已离开主页（导航到未知页面），不得盲点固定入口坐标。
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task._wait_for_quick_hunt_home = lambda: True
        task.capture_frame = lambda: object()
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "must not blind-click the fixed entry when not on home"
        )
        entrance_clicks = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, timeout, name: entrance_clicks.append(name) or True
        )
        task._quick_hunt_home_signals = lambda _frame: (False, 0, 120.0, "-")
        task._quick_hunt_wait_ocr = lambda *_args, **_kwargs: ("", None)

        self.assertEqual("failed", task._quick_hunt_open_menu())
        self.assertEqual(1, len(entrance_clicks))

    def test_quick_hunt_reward_must_close_and_return_to_menu_before_finishing(self):
        for case in (
            (("reward", "menu", "menu"), "done", 1),
            (("reward", "reward", "transition", "menu", "menu"), "done", 2),
            (("reward_title", "menu", "menu"), "done", 1),
            (("reward", "dim_menu"), "failed", 1),
            (("reward", "bright_noise"), "failed", 1),
            (("reward", "menu", "transition"), "failed", 1),
            (("reward", "menu", "transition", "menu", "menu"), "done", 1),
            (("reward",), "failed", 3),
            (("reward", "transition"), "failed", 1),
            (("depleted", "menu", "menu"), "depleted", 1),
            (("depleted",), "failed", 3),
            (("depleted", "dim_menu"), "failed", 1),
            (("menu",), "failed", 0),
            ((*(["transition"] * 16), "reward", "menu", "menu"), "done", 1),
            ((*(["unchanged"] * 9), "reward", "menu", "menu"), "done", 1, 1),
            (("unchanged",), "failed", 0, 2),
            (("transition", "unchanged"), "failed", 0, 0),
            (("spent", "unchanged"), "failed", 0, 0),
            (("changed_count", "unchanged"), "failed", 0, 0),
            (("transition", "reward", "menu", "menu"), "done", 1, 0),
            (("reward", "unchanged"), "failed", 1, 0),
            (("unknown_error",), "failed", 0, 0),
            (("wrong_resource",), "failed", 0, 0),
        ):
            frames, expected, click_count = case[:3]
            submission = hunt_dialog() if len(case) == 4 else None
            expected_start_retries = case[3] if submission else 0
            with self.subTest(frames=frames):
                task = object.__new__(QuickHuntTask)
                task.config = {"快速狩猎结算等待秒数": 5.0, "快速狩猎界面等待秒数": 4.0}
                clock = {"now": 0.0}
                states = {}
                images = []
                for state in frames:
                    image = np.zeros((108, 192, 3), dtype=np.uint8)
                    if state in {"menu", "dim_menu", "bright_noise"}:
                        crop_relative(image, QUICK_HUNT_MENU_FOREGROUND_ROI)[:] = (
                            58 if state == "dim_menu" else 160
                        )
                    states[id(image)] = state
                    images.append(image)
                sequence = iter(images)
                task.capture_frame = Mock(side_effect=lambda: next(sequence, images[-1]))
                task.info_set = Mock()
                task.log_info = Mock()
                task._save_flow_diagnostic = Mock()

                def sleep(seconds):
                    clock["now"] += seconds

                task.sleep = Mock(side_effect=sleep)
                task.operate_click = Mock(
                    side_effect=lambda *_, after_sleep=0.0: sleep(after_sleep),
                )
                vision = Mock()
                vision.ocr_boxes.return_value = [
                    SimpleNamespace(name="确定", x=80, y=70, width=30, height=12),
                ]
                vision.click_client.side_effect = lambda *_, after_sleep=0: sleep(after_sleep)
                task._quick_vision = lambda: vision

                def snapshot(frame, *_):
                    state = states[id(frame)]
                    if state == "unchanged":
                        return submission
                    if state == "spent":
                        return replace(submission, pool=84)
                    if state == "changed_count":
                        return replace(submission, count=2, cost=12)
                    return None

                task._quick_hunt_dialog_snapshot = Mock(side_effect=snapshot)

                def ocr_text(frame, relative_roi, name):
                    state = states[id(frame)]
                    if state == "reward" and relative_roi == QUICK_HUNT_REWARD_ROI:
                        return "点击画面即可返回。"
                    if state == "reward_title" and relative_roi == QUICK_HUNT_REWARD_TITLE_ROI:
                        return "REWARD 获得1种"
                    if state == "depleted" and relative_roi == QUICK_HUNT_DIALOG_ROI:
                        return "米饭不足"
                    if state == "unknown_error" and relative_roi == QUICK_HUNT_DIALOG_ROI:
                        return "无法进入"
                    if state == "wrong_resource" and relative_roi == QUICK_HUNT_DIALOG_ROI:
                        return "火把不足"
                    if (
                        state in {"menu", "dim_menu"}
                        and relative_roi == QUICK_HUNT_MENU_FOREGROUND_ROI
                    ):
                        return "狩猎场 金币 史莱姆 圣石洞穴"
                    return ""

                task._quick_hunt_ocr_text = Mock(side_effect=ocr_text)
                with patch("src.tasks.quick_hunt.monotonic", side_effect=lambda: clock["now"]):
                    self.assertEqual(
                        expected, task._quick_hunt_wait_result("金币 MAX", submission=submission),
                    )
                start_clicks = [
                    call for call in vision.click_client.call_args_list
                    if call.args[0] == hunt_dialog().start_point
                ]
                self.assertEqual(expected_start_retries, len(start_clicks))
                if "depleted" in frames:
                    self.assertEqual(click_count, vision.click_client.call_count)
                    self.assertTrue(all(call.args[0] == (95, 76)
                                        for call in vision.click_client.call_args_list))
                    task.operate_click.assert_not_called()
                else:
                    self.assertEqual(click_count, task.operate_click.call_count)
                for click in task.operate_click.call_args_list:
                    self.assertAlmostEqual(956 / 1920, click.args[0])
                    self.assertAlmostEqual(992 / 1080, click.args[1])
                if expected == "failed":
                    task._save_flow_diagnostic.assert_called_once()
                    self.assertLessEqual(clock["now"], 5.3)
                else:
                    task._save_flow_diagnostic.assert_not_called()

    def test_quick_hunt_shortage_uses_unique_confirmation_above_background_cancel(self):
        for labels, expected_point in (
            (("取消", "确定"), (70, 30)),
            (("确认", "确定"), None),
            ((), None),
        ):
            with self.subTest(labels=labels):
                task = object.__new__(QuickHuntTask)
                frame = np.zeros((108, 192, 3), dtype=np.uint8)
                vision = Mock()
                vision.ocr_boxes.return_value = [
                    SimpleNamespace(name=name, x=10 + index * 50, y=20, width=20, height=20)
                    for index, name in enumerate(labels)
                ]
                task._quick_vision = lambda: vision

                self.assertEqual(
                    expected_point is not None, task._quick_hunt_dismiss_shortage(frame),
                )
                if expected_point is None:
                    vision.click_client.assert_not_called()
                else:
                    vision.click_client.assert_called_once()
                    self.assertEqual(
                        (expected_point, frame.shape), vision.click_client.call_args.args,
                    )

    def test_quick_hunt_dialog_label_omission_preserves_resource_and_switch_checks(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for resource in ("火把", "米饭"):
            for labels, accepted in (
                ((f"仅使用免费{resource}",), True),
                ((f"仅使免费{resource}",), True),
                ((f"仅使免费{resource}", f"仅使用免费{resource}"), False),
                ((f"仅使用{resource}",), False),
                ((f"使用免费{resource}",), False),
                (("仅使免费" + ("米饭" if resource == "火把" else "火把"),), False),
            ):
                for state in ("on", "off", "unknown"):
                    with self.subTest(resource=resource, labels=labels, state=state):
                        task = object.__new__(QuickHuntTask)
                        task.info_set = Mock()
                        task._quick_hunt_free_resource = Mock(return_value=60)
                        task._quick_hunt_box_enabled = Mock(return_value=True)
                        boxes = [
                            SimpleNamespace(name=text, x=700, y=195, width=105, height=18)
                            for text in labels
                        ] + [
                            SimpleNamespace(
                                name="风之洞穴极难", x=600, y=240, width=120, height=20,
                            ),
                            SimpleNamespace(name="狩猎1次", x=600, y=300, width=90, height=20),
                            SimpleNamespace(
                                name="MIN MAX 取消", x=600, y=400, width=180, height=20,
                            ),
                        ]
                        start = SimpleNamespace(name="狩猎6", x=900, y=600, width=80, height=20)
                        vision = Mock()
                        vision.ocr_boxes.side_effect = [boxes, [start]]
                        task._quick_vision = lambda: vision
                        with patch("src.tasks.quick_hunt.read_free_switch",
                                   return_value=(state, (830, 204))) as switch:
                            snapshot = task._quick_hunt_dialog_snapshot(frame, "圣石", resource)
                        if accepted:
                            self.assertEqual((resource, state, 1, 6, 60), (
                                snapshot.resource, snapshot.free_state, snapshot.count,
                                snapshot.cost, snapshot.pool,
                            ))
                            switch.assert_called_once_with(frame, boxes[0])
                            task._quick_hunt_free_resource.assert_called_once_with(resource, frame)
                        else:
                            self.assertIsNone(snapshot)
                            switch.assert_not_called()

    def test_quick_hunt_submission_requires_free_switch_and_stable_affordable_cost(self):
        rice = hunt_dialog()
        torch = hunt_dialog(map_name="火之洞穴极难", pool=60, resource="火把")
        rice_max = replace(rice, count=15, cost=90)
        cases = (
            ("免费开关未知", "MIN", [replace(rice, free_state="unknown")], 90, "failed"),
            ("免费开关关闭后开启", "MIN",
             [replace(rice, free_state="off")] * 2 + [rice] * 4, 90, "done"),
            ("米饭单次成本错误", "MIN", [replace(rice, cost=5)], 90, "failed"),
            ("超出本轮额度", "MIN", [rice], 5, "failed"),
            ("MAX点击未生效", "MAX", [rice], 90, "failed"),
            ("MAX余额与成本未稳定", "MAX",
             [rice] * 4 + [rice_max, replace(rice_max, pool=89)] * 8, 90, "failed"),
            ("MAX弹窗米饭83到84", "MAX",
             [replace(rice, pool=83)] * 4 + [replace(rice, pool=84)], 83, "budget_limited"),
            ("米饭MAX", "MAX", [rice] * 4 + [rice_max] * 2, 90, "done"),
            ("火把极难单次6", "MIN", [torch], 60, "done"),
            ("火把极难MAX十次", "MAX",
             [torch] * 4 + [replace(torch, count=10, cost=60)] * 2, 60, "done"),
        )
        for name, mode, snapshots, max_cost, expected in cases:
            with self.subTest(name=name):
                self.reset_stage_history()
                task = object.__new__(QuickHuntTask)
                task.config = {"快速狩猎界面等待秒数": 2.5}
                task.info_set = Mock()
                task.log_info = Mock()
                task._save_flow_diagnostic = Mock()
                task.capture_frame = Mock(return_value=object())
                clock = {"now": 0.0}

                def sleep(seconds):
                    clock["now"] += seconds

                task.sleep = Mock(side_effect=sleep)
                sequence = iter(snapshots)
                task._quick_hunt_dialog_snapshot = Mock(
                    side_effect=lambda *_: next(sequence, snapshots[-1]),
                )
                task._quick_hunt_click_ocr = Mock(return_value=True)
                task._quick_hunt_wait_confirmation_dialog = Mock(return_value=True)
                task._quick_hunt_cancel_dialog = Mock(return_value=True)
                task._quick_hunt_wait_result = Mock(return_value="done")
                vision = Mock()
                vision.click_client.side_effect = lambda *_, after_sleep=0: sleep(after_sleep)
                task._quick_vision = lambda: vision

                with patch("src.tasks.quick_hunt.monotonic", side_effect=lambda: clock["now"]):
                    result = task._quick_hunt_execute_current_map(
                        mode, name, resource=snapshots[0].resource, max_cost=max_cost,
                    )
                self.assertEqual(expected, result)
                start_clicks = [
                    call for call in vision.click_client.call_args_list
                    if call.args[0] == rice.start_point
                ]
                self.assertEqual(int(expected == "done"), len(start_clicks))
                if expected == "done":
                    submission = task._quick_hunt_wait_result.call_args.kwargs["submission"]
                    self.assertLessEqual(submission.cost, max_cost)
                    self.assertEqual(snapshots[-1].cost, submission.cost)
                else:
                    task._quick_hunt_wait_result.assert_not_called()
                    task._quick_hunt_cancel_dialog.assert_called_once()

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
        task._quick_hunt_wait_confirmation_dialog = lambda *_args, **_kwargs: True
        map_calls = []
        task._quick_hunt_wait_map_confirmation = (
            lambda pattern, name: map_calls.append((pattern, name))
            or ("matched", "哥布林遗迹极难", None)
        )
        snapshot = hunt_dialog(count=15, cost=90)
        task._quick_hunt_prepare_dialog = Mock(return_value=snapshot)
        task._quick_hunt_wait_result = Mock(return_value="done")
        vision = Mock()
        task._quick_vision = lambda: vision
        from src.tasks import run_history

        def check_pending_before_start(*_args, **_kwargs):
            record = run_history.read_stage(task, "狩猎提交:米饭:冒险航线")
            self.assertEqual("pending", record["state"])
            self.assertEqual(90, record["evidence"]["cost"])

        vision.click_client.side_effect = check_pending_before_start

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
        self.assertEqual(["冒险航线-快速狩猎按钮"], [call[3] for call in click_calls])
        vision.click_client.assert_called_once()
        self.assertEqual(
            (snapshot.start_point, snapshot.frame_shape), vision.click_client.call_args.args,
        )
        self.assertIs(snapshot, task._quick_hunt_wait_result.call_args.kwargs["submission"])
        self.reset_stage_history()
        vision.click_client.reset_mock()
        with patch("src.tasks.run_history.write_json_file", side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError):
                task._quick_hunt_execute_current_map("MAX", "冒险航线")
        vision.click_client.assert_not_called()

    def test_quick_hunt_retries_button_when_count_dialog_was_swallowed(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 8.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        button_clicks = []
        task._quick_hunt_click_ocr = (
            lambda patterns, roi, timeout, name, **kwargs: button_clicks.append(name)
            or True
        )
        dialog_results = iter((False, True))
        task._quick_hunt_wait_confirmation_dialog = (
            lambda *_args, **_kwargs: next(dialog_results)
        )
        task._quick_hunt_menu_still_open = lambda: True
        task._quick_hunt_prepare_dialog = Mock(return_value=hunt_dialog())
        task._quick_hunt_wait_result = Mock(return_value="done")
        vision = Mock()
        task._quick_vision = lambda: vision

        self.assertEqual(
            "done",
            task._quick_hunt_execute_current_map("MIN", "狩猎场"),
        )
        self.assertEqual(["狩猎场-快速狩猎按钮"] * 2, button_clicks[:2])
        vision.click_client.assert_called_once()

    def test_quick_hunt_adventure_map_mismatch_cancels_before_consuming_rice(self):
        for target, wrong_map, menu_restored in (
            ("冒险航线", "野猪洞穴", True),
            ("冒险航线", "野猪洞穴", False),
            ("水属性圣石", "火之洞穴", True),
        ):
            with self.subTest(target=target, menu_restored=menu_restored):
                task = object.__new__(QuickHuntTask)
                task.config = {"快速狩猎界面等待秒数": 3.0, "快速狩猎圣石目标": "水"}
                task.info_set = Mock()
                task.log_info = Mock()
                task._save_flow_diagnostic = Mock()
                task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
                clock = {"now": 0.0}

                def sleep(seconds):
                    clock["now"] += seconds

                task.sleep = Mock(side_effect=sleep)
                menu_frames = iter((False, True, True) if menu_restored else (False,))
                task._quick_hunt_menu_still_open = Mock(
                    side_effect=lambda _frame: next(menu_frames, menu_restored),
                )
                task._quick_hunt_dialog_ocr = Mock(return_value=(True, "MIN MAX 取消", "狩猎6"))
                cancel = SimpleNamespace(name="取消", x=760, y=770, width=80, height=30)
                vision = Mock()
                vision.ocr_boxes.return_value = [cancel]
                vision.click_client.side_effect = lambda *_, after_sleep=0: sleep(after_sleep)
                task._quick_vision = lambda: vision
                click_calls = []
                task._quick_hunt_click_ocr = (
                    lambda patterns, roi, timeout, name, **kwargs: click_calls.append(
                        (patterns, name)
                    )
                    or True
                )
                task._quick_hunt_enter_crystal_cave = Mock(return_value=True)
                task._quick_hunt_free_resource = Mock(return_value=60)
                task._quick_hunt_wait_confirmation_dialog = Mock(return_value=True)
                task._quick_hunt_wait_map_confirmation = Mock(
                    return_value=("wrong", wrong_map, wrong_map)
                )
                task._quick_hunt_wait_result = Mock(
                    side_effect=AssertionError("错误地图不得开始狩猎")
                )

                with patch("src.tasks.quick_hunt.monotonic", side_effect=lambda: clock["now"]):
                    if target == "冒险航线":
                        self.assertEqual(
                            "wrong_map" if menu_restored else "failed",
                            task._quick_hunt_execute_current_map(
                                "MAX", target, expected_map_pattern=r"哥布林遗迹",
                            ),
                        )
                    else:
                        self.assertFalse(task._quick_hunt_run_crystal_cave())
                        pattern = task._quick_hunt_wait_map_confirmation.call_args.args[0]
                        self.assertRegex("水之洞穴", pattern)
                        self.assertIsNone(re.search(pattern, wrong_map))
                self.assertGreater(vision.click_client.call_count, 0)
                for call in vision.click_client.call_args_list:
                    self.assertEqual((800, 785), call.args[0])
                self.assertFalse(any(
                    name in (f"{target}-MAX", f"{target}-开始狩猎")
                    for _patterns, name in click_calls
                ))
                task._quick_hunt_wait_result.assert_not_called()

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
                return [
                    SimpleNamespace(name="MIN"),
                    SimpleNamespace(name="野猪洞穴极难"),
                ]

        vision = FakeVision()
        task._quick_vision = lambda: vision

        state, text, actual_map = task._quick_hunt_wait_map_confirmation(
            r"哥布林遗迹",
            "冒险航线-地图确认",
        )
        self.assertEqual("wrong", state)
        self.assertEqual("野猪洞穴", actual_map)
        self.assertIn("野猪洞穴极难", text)
        self.assertEqual(QUICK_HUNT_COUNT_ROI, vision.relative_roi)

    def test_quick_hunt_map_confirmation_ignores_background_map_without_dialog(self):
        task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎界面等待秒数": 0.01}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda _seconds: None

        class FakeVision:
            def ocr_boxes(self, _frame, _name, relative_roi=None):
                return [SimpleNamespace(name=label) for label in labels]

        task._quick_vision = lambda: FakeVision()

        for labels in (("1.野猪洞穴",), ("1.野猪洞穴", "取消")):
            with self.subTest(labels=labels):
                state, text, actual_map = task._quick_hunt_wait_map_confirmation(
                    r"野猪洞穴", "背景地图确认"
                )
                self.assertEqual("timeout", state)
                self.assertIn("1.野猪洞穴", text)
                self.assertIsNone(actual_map)

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
        from ok.task.exceptions import FinishedException

        for error in (None, RuntimeError, TaskDisabledException, FinishedException):
            with self.subTest(error=error):
                task = object.__new__(QuickHuntTask)
                task.config = {"快速狩猎圣石洞穴": True}
                task.info_set = Mock()
                task._quick_hunt_open_menu = Mock(return_value="opened")
                task._quick_hunt_run_rice_scheduler = Mock(return_value=False, side_effect=error)
                task._quick_hunt_run_crystal_cave = Mock(return_value=True)
                task._quick_hunt_return_home = Mock(return_value=True)
                task._save_flow_diagnostic = Mock(
                    side_effect=lambda _name: task._quick_hunt_return_home.assert_not_called(),
                )

                if error is None:
                    self.assertFalse(task.run_quick_hunt())
                else:
                    with self.assertRaises(error):
                        task.run_quick_hunt()
                task._quick_hunt_run_crystal_cave.assert_not_called()
                self.assertEqual(
                    error not in (TaskDisabledException, FinishedException),
                    bool(task._quick_hunt_return_home.call_count),
                )
                self.assertEqual(
                    error not in (TaskDisabledException, FinishedException),
                    bool(task._save_flow_diagnostic.call_count),
                )

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
        task._quick_hunt_ocr_text = Mock(return_value="")
        task._quick_hunt_menu_still_open = Mock(return_value=True)
        task.clear_temporary_home_announcement_if_needed = Mock(return_value=False)
        clicks = []
        task._click_reference = lambda x, y, **kwargs: clicks.append((x, y, kwargs))

        self.assertTrue(task._quick_hunt_return_home())
        self.assertEqual([(101, 55, {"after_sleep": 2.0})], clicks)

    def test_quick_hunt_return_home_unwinds_known_layers_and_waits_on_unknown(self):
        for states, expected, actions in (
            (("reward", "dialog", "menu", "home"), True, ["reward", "cancel", "back"]),
            (("shortage_wait",) * 4 + ("shortage", "menu", "home"),
             True, ["shortage", "back"]),
            (("unknown",), False, []),
        ):
            with self.subTest(states=states):
                task = object.__new__(QuickHuntTask)
                task.config = {"快速狩猎界面等待秒数": 4.0, "主页确认等待秒数": 4.0}
                task.info_set = Mock()
                task._save_flow_diagnostic = Mock()
                task._home_p95_threshold = Mock(return_value=185)
                task._quick_hunt_current_map_context = Mock(return_value="-")
                task.clear_temporary_home_announcement_if_needed = Mock(return_value=False)
                frames = iter(states)
                task.capture_frame = Mock(side_effect=lambda: next(frames, states[-1]))
                task._quick_hunt_home_signals = lambda frame: (frame == "home", 0, 0, "")
                def ocr_text(frame, roi, **_):
                    if frame == "reward" and roi == QUICK_HUNT_REWARD_TITLE_ROI:
                        return "REWARD"
                    if frame.startswith("shortage") and roi == QUICK_HUNT_DIALOG_ROI:
                        return "米饭不足"
                    return ""

                task._quick_hunt_ocr_text = ocr_text
                task._quick_hunt_menu_still_open = lambda frame: frame == "menu"
                task._quick_hunt_dialog_ocr = lambda frame, _: (
                    frame in {"dialog", "shortage", "shortage_wait"}, "MIN MAX", "",
                )
                seen = []
                clock = {"now": 0.0}

                def sleep(seconds):
                    clock["now"] += seconds

                task.sleep = Mock(side_effect=sleep)
                task.operate_click = lambda *_, **kwargs: (
                    seen.append("reward") or sleep(kwargs["after_sleep"])
                )
                task._click_reference = lambda *_, **kwargs: (
                    seen.append("back") or sleep(kwargs["after_sleep"])
                )
                task._quick_hunt_cancel_dialog = Mock(
                    side_effect=lambda _, **__: seen.append("cancel") or True,
                )
                task._quick_hunt_dismiss_shortage = Mock(
                    side_effect=lambda frame: (
                        False if frame == "shortage_wait" else seen.append("shortage") or True
                    ),
                )

                with patch("src.tasks.quick_hunt.monotonic", side_effect=lambda: clock["now"]):
                    self.assertEqual(expected, task._quick_hunt_return_home())
                self.assertEqual(actions, seen)
                self.assertLessEqual(clock["now"], 4.3)
                if "shortage" in states:
                    self.assertEqual(5, task._quick_hunt_dismiss_shortage.call_count)
                    task._quick_hunt_cancel_dialog.assert_not_called()

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

    def test_daily_run_stops_when_failed_step_cannot_recover_home(self):
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
        task._save_flow_diagnostic = Mock()
        task.recover_daily_home = lambda: False
        calls = []
        task.run_guild_sign_in = lambda: calls.append("guild") or False
        task.run_my_home_sign_in = lambda: calls.append("home") or True
        task.run_business_collect = lambda: calls.append("business") or True
        task.run_quick_hunt = lambda: calls.append("hunt") or True

        self.assertFalse(DailyTask.run(task))
        self.assertEqual(["guild"], calls)

    def test_free_rice_reader_does_not_use_stored_rice_or_ambiguous_ocr(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        task = object.__new__(QuickHuntTask)
        task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        task.sleep = Mock()
        task._quick_vision = Mock(return_value=SimpleNamespace(ocr_boxes=Mock(return_value=[])))
        for text, expected in (
            ("0/90 +1.77K", 0),
            ("84/90", 84),
            ("1.77K", None),
            ("0/90 84/90", None),
            ("990/90", None),
        ):
            task._quick_hunt_ocr_text = Mock(return_value=text)
            self.assertEqual(expected, task._quick_hunt_free_rice())
        task._quick_hunt_ocr_text = Mock(side_effect=["84/90", "78/90"])
        self.assertIsNone(task._quick_hunt_free_rice())

    def test_free_rice_reader_filters_yellow_counter_before_ocr(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        task = object.__new__(QuickHuntTask)
        task.sleep = Mock()
        task._quick_vision = Mock(return_value=SimpleNamespace(ocr_boxes=Mock(return_value=[])))
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[43:70, 1621:1689] = (245, 245, 245)
        frame[50:60, 1726:1736] = (0, 200, 255)
        task.capture_frame = Mock(return_value=frame)

        def read_resource(image, _roi, name):
            self.assertEqual("免费米饭数量", name)
            self.assertFalse(image[12:22, 141:151].any())
            return "90/90" if image[5:32, 36:104].any() else ""

        task._quick_hunt_ocr_text = Mock(side_effect=read_resource)
        self.assertEqual(90, task._quick_hunt_free_rice())
        frame[43:70, 1621:1689] = (0, 200, 255)
        self.assertIsNone(task._quick_hunt_free_rice())

    def test_free_resource_raw_fraction_fallback_keeps_capacity_and_two_frame_guards(self):
        task = object.__new__(QuickHuntTask)
        task.info_set = Mock()
        task.sleep = Mock()
        task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        task._quick_hunt_ocr_text = Mock(return_value="0 9 0")
        boxes = Mock()
        task._quick_vision = Mock(return_value=SimpleNamespace(ocr_boxes=boxes))
        for resource, texts, expected in (
            ("米饭", ["0/90", "+3.39K"], 0),
            ("米饭", ["84/90", "2"], 84),
            ("火把", ["0/60", "+100"], 0),
            ("米饭", ["0 9 0"], None),
            ("米饭", ["0/90 3.39K"], None),
            ("米饭", ["0/90", "84/90"], None),
            ("米饭", ["0/90", "0/60"], None),
            ("米饭", ["91/90"], None),
            ("米饭", ["0/60"], None),
            ("米饭", ["+3.39K"], None),
        ):
            with self.subTest(resource=resource, texts=texts):
                boxes.return_value = [SimpleNamespace(name=text) for text in texts]
                self.assertEqual(expected, task._quick_hunt_free_resource(resource))
        boxes.side_effect = [[SimpleNamespace(name="0/90")], [SimpleNamespace(name="6/90")]]
        self.assertIsNone(task._quick_hunt_free_resource("米饭"))
        boxes.reset_mock(side_effect=True)
        task._quick_hunt_ocr_text.return_value = "90/90"
        self.assertEqual(90, task._quick_hunt_free_resource("米饭"))
        boxes.assert_not_called()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[43:70, 1760:1790] = 245
        task.capture_frame.return_value = frame
        task._quick_hunt_ocr_text.side_effect = lambda image, *_a, **_k: (
            "60/60" if image[:, 80:].any() else ""
        )
        self.assertEqual(60, task._quick_hunt_free_resource("火把"))



    def test_quick_hunt_cancellation_does_not_click_home(self):
        from ok.task.exceptions import FinishedException, TaskDisabledException

        task = object.__new__(QuickHuntTask)
        task.config = {}
        task.info_set = Mock()
        task.log_info = Mock()
        task._quick_hunt_open_menu = Mock(return_value="opened")
        task._quick_hunt_return_home = Mock()
        task._save_flow_diagnostic = Mock()
        for exception in (TaskDisabledException, FinishedException):
            task._quick_hunt_run_rice_scheduler = Mock(side_effect=exception)
            with self.assertRaises(exception):
                task.run_quick_hunt()
        task._quick_hunt_return_home.assert_not_called()
        task._save_flow_diagnostic.assert_not_called()

    def test_daily_completion_keeps_business_cycle_across_refresh(self):
        from datetime import datetime

        from src.tasks import run_history, scheduler

        before = datetime(2026, 10, 20, 3, 59, tzinfo=run_history.BEIJING_TZ).timestamp()
        after = before + 120
        clock = [before]
        task = object.__new__(DailyTask)
        task.name = "公会、小屋、酒馆"
        task.config = {"_仅执行未完成步骤": True}
        task.info = {}
        task.info_set = lambda key, value: task.info.__setitem__(key, value)
        task.info_snapshot = lambda: dict(task.info)
        task.log_info = task.log_completion = Mock()
        task.recover_daily_home = Mock(return_value=True)
        task.run_guild_sign_in = Mock(return_value=True)
        task.run_my_home_sign_in = Mock(return_value=True)
        task.run_business_collect = Mock(
            side_effect=lambda: clock.__setitem__(0, after) or True,
        )
        schedule = scheduler.TaskScheduleStore(str(self.history.path) + ".schedule")
        with patch("time.time", side_effect=lambda: clock[0]):
            self.assertTrue(task.run())
            self.history.record_task_done(task, started=before, ok=True)
            schedule.delay_after_record(task.name, True, self.history)
            self.assertFalse(self.history.is_completed_today(task.name))
            self.assertTrue(schedule.is_due(task.name, scope=run_history.configuration_scope(task)))
            self.assertTrue(task.run())
            self.history.record_task_done(task, started=after, ok=True)
            self.assertTrue(self.history.is_completed_today(task.name))
        self.assertEqual(2, task.run_guild_sign_in.call_count)


    def test_business_test_entry_runs_only_collection_and_copies_results(self):
        for outcome in (True, False, TaskDisabledException()):
            with self.subTest(outcome=outcome):
                executor = SimpleNamespace(scene=None, get_task_by_class=Mock())
                daily = Mock(spec=DailyTask)
                daily.info_snapshot.return_value = {"常客圣石结果": "未确认"}
                daily.run_business_collect.side_effect = (
                    outcome if isinstance(outcome, Exception) else lambda: outcome
                )
                executor.get_task_by_class.return_value = daily
                task = BD2BusinessCollectTestTask(executor, SimpleNamespace())
                self.assertEqual("测试", task.group_name)
                if isinstance(outcome, Exception):
                    with self.assertRaises(TaskDisabledException):
                        task.run()
                else:
                    self.assertIs(outcome, task.run())
                daily.run_business_collect.assert_called_once()
                daily.run.assert_not_called()
                daily.run_guild_sign_in.assert_not_called()
                daily.run_my_home_sign_in.assert_not_called()
                self.assertEqual(int(outcome is False), daily.recover_daily_home.call_count)
                self.assertEqual("未确认", task.info_snapshot()["常客圣石结果"])


    def test_business_collect_visits_restaurant_and_confirms_guest_rewards(self):
        for reward_press, delay, guest_name, home_ok, moved, expected, presses in (
            (1, 0.2, "常客", True, False, True, 1),
            (1, 3.0, "常客", True, False, True, 1),
            (1, 4.0, "常客", True, False, True, 1),
            (2, 2.0, "常客", True, False, True, 2),
            (0, 0.0, "常客", True, False, True, 2),
            (0, 0.0, "常客", True, True, False, 1),
            (0, 0.0, "常客笔记", True, False, False, 0),
            (1, 3.0, "常客", False, False, False, 1),
        ):
            with self.subTest(reward_press=reward_press, delay=delay, guest=guest_name,
                              home=home_ok, moved=moved):
                frame = np.full((720, 1280, 3), 230, dtype=np.uint8)

                def box(name, x, y):
                    return SimpleNamespace(name=name, x=x - 20, y=y - 10, width=40, height=20)

                popup = self._business_controls()
                # 餐馆行收菜后上移；列表故意先放其他行的同名按钮。
                popup[0].y = 169.2
                popup += [box("立刻前往", 896, 432), box("立刻前往", 896, 180)]
                restaurant = [box("格鲁菲餐厅", 130, 40), box(guest_name, 192, 216)]
                reward = box("点击画面即可返回", 640, 648)
                reward_text = ("暗圣石×10", "光圣石×10", "火圣石×10", "水圣石×10", "风圣石×5")
                press_state = [0, 0.0]

                def screen(actions, now):
                    if (0.6, 0.8) not in actions:
                        return frame, self._business_controls(), False
                    if (0.5, 0.9) not in actions:
                        return frame, [reward], False
                    if (0.7, 0.25) not in actions:
                        return frame, popup, False
                    count = actions.count((0.15, 0.3))
                    if count != press_state[0]:
                        press_state[:] = [count, now - 0.8]
                    if moved and count:
                        return frame, [box("加载中", 640, 360)], False
                    boxes = restaurant + [box("常客赠送圣石", 400, 400)]
                    if reward_press and count == reward_press and now - press_state[1] >= delay:
                        boxes += [box(text, 400, 500 + i * 25)
                                  for i, text in enumerate(reward_text)]
                    return frame, boxes, False

                task, statuses, clicks, _, _ = self._business_flow_task(screen)
                del task._collect_business_guest_stones
                task._quick_vision = Mock(return_value=SimpleNamespace(
                    ocr_boxes=lambda *_args: task.ocr(),
                ))
                task.recover_daily_home.return_value = home_ok
                self.assertEqual(expected, task.run_business_collect())
                self.assertEqual([(0.6, 0.8), (0.5, 0.9), (0.7, 0.25)]
                                 + [(0.15, 0.3)] * presses, clicks)
                task.recover_daily_home.assert_called_once()
                if expected:
                    self.assertEqual("已领取" if reward_press else "无待领取圣石",
                                     statuses["常客圣石结果"])
                    self.assertEqual("、".join(reward_text) if reward_press else "-",
                                     statuses["常客圣石奖励"])
                else:
                    task._save_flow_diagnostic.assert_called_with("business_collect_failed")


    def test_business_collect_requires_restaurant_entry_and_arrival(self):
        for has_restaurant_go, arrived in ((False, True), (True, False)):
            with self.subTest(go=has_restaurant_go, arrived=arrived):
                frame = np.full((720, 1280, 3), 230, dtype=np.uint8)
                popup = self._business_controls()
                popup.append(SimpleNamespace(
                    name="立刻前往", x=880, y=206 if has_restaurant_go else 422,
                    width=32, height=20,
                ))
                reward = SimpleNamespace(
                    name="点击画面即可返回", x=620, y=638, width=40, height=20,
                )
                task, statuses, clicks, _, _ = self._business_flow_task([
                    (frame, popup, False), (frame, [reward], False), (frame, popup, False),
                ])
                del task._collect_business_guest_stones
                task._quick_vision = Mock(return_value=SimpleNamespace(
                    ocr_boxes=lambda *_args: task.ocr(),
                ))
                task._wait_for_ocr_keywords = Mock(side_effect=[(True, ""), (arrived, "")])
                self.assertFalse(task.run_business_collect())
                self.assertEqual(3 if has_restaurant_go else 2, len(clicks))
                self.assertEqual("未确认", statuses["常客圣石结果"])
                task.recover_daily_home.assert_called_once()


    def test_quick_hunt_keeps_default_hunting_ground_tab(self):
        task = object.__new__(QuickHuntTask)
        task._quick_hunt_click_ocr = lambda *_args, **_kwargs: self.fail(
            "the current default hunting ground tab is already selected"
        )

        self.assertTrue(task._quick_hunt_select_plan_target("hunt"))



if __name__ == "__main__":
    unittest.main()
