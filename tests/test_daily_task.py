import json
import re
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from ok.task.exceptions import TaskDisabledException
from ok.util.config import Config

from src.tasks.DailyTask import (
    BUSINESS_GUEST_RELATIVE_ROI,
    GUILD_SIGNUP_SUCCESS_TEMPLATE,
    GUILD_SUCCESS_KEYWORDS,
    GUILD_TEMPLATE,
    MY_HOME_TEMPLATE,
    DailyTask,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.map_trade.vision import Vision
from src.tasks.quick_hunt import (
    QUICK_HUNT_COUNT_ROI,
    QUICK_HUNT_CRYSTAL_CLICK_ROI,
    QUICK_HUNT_CRYSTAL_ENTRY_PATTERN,
    QUICK_HUNT_CRYSTAL_TITLE_ROI,
    QUICK_HUNT_DIALOG_ROI,
    QUICK_HUNT_MENU_FOREGROUND_ROI,
    QUICK_HUNT_REWARD_ROI,
    QUICK_HUNT_REWARD_TITLE_ROI,
    QUICK_HUNT_STONE_COUNT_ROI,
    HuntDialog,
)
from src.tasks.quick_hunt_plan import PLAN_KEY
from src.tasks.QuickHuntTask import QuickHuntTask
from src.tasks.task_vision_mixin import (
    LOADING_TEMPLATE,
)
from src.utils.image_utils import crop_relative
from tests.helpers import FakeClock, StageHistoryTestCase


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
        # The repeat-visit probe polls on real time; these cases test leaving.
        task._wait_for_existing_guild_visit = lambda: False
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
        # The repeat-visit probe polls on real time; these cases test leaving.
        task._wait_for_existing_guild_visit = lambda: False
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
                if submission is not None:
                    from src.tasks import run_history
                    self.reset_stage_history()
                    scope = run_history.stage_scope(task)
                    evidence = {"phase": "attempted", "start_clicks": 1, "resource": "米饭"}
                    run_history.save_stage(task, "狩猎提交:米饭:金币 MAX", "pending",
                                           scope=scope, evidence=evidence)
                    task._hunt_checkpoint = ("狩猎提交:米饭:金币 MAX", scope, evidence)
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

    def test_quick_hunt_map_identity_preserves_cave_and_difficulty_across_count_modes(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for after, accepted in (
            ("黑暗洞穴·极难", True),
            (" 黑暗洞穴 · 极难 ", True),
            ("火之洞穴·极难", False),
            ("黑暗洞穴·普通", False),
            ("黑暗洞穴·极难2", False),
            ("· ，", False),
        ):
            with self.subTest(after=after):
                task = object.__new__(QuickHuntTask)
                task.config = {"快速狩猎界面等待秒数": 2.5}
                task.info_set = Mock()
                task.log_info = Mock()
                task._quick_hunt_free_resource = Mock(return_value=60)
                task._quick_hunt_box_enabled = Mock(return_value=True)
                vision = Mock()
                task._quick_vision = lambda: vision
                snapshots = []
                for text, count in (("黑暗洞穴，极难", 1), (after, 10)):
                    boxes = [
                        SimpleNamespace(name="仅使用免费火把", x=700, y=195,
                                        width=105, height=18),
                        SimpleNamespace(name=text, x=600, y=240, width=120, height=20),
                        SimpleNamespace(name=f"狩猎{count}次", x=600, y=300,
                                        width=90, height=20),
                        SimpleNamespace(name="MIN MAX 取消", x=600, y=400,
                                        width=180, height=20),
                    ]
                    start = SimpleNamespace(name=f"狩猎{count * 6}", x=900, y=600,
                                            width=80, height=20)
                    vision.ocr_boxes.side_effect = [boxes, [start]]
                    with patch("src.tasks.quick_hunt.read_free_switch",
                               return_value=("on", (830, 204))):
                        snapshots.append(task._quick_hunt_dialog_snapshot(frame, "圣石", "火把"))
                self.assertEqual("黑暗洞穴极难", snapshots[0].map_name)
                if accepted:
                    self.assertEqual(snapshots[0].map_name, snapshots[1].map_name)
                sequence = iter([snapshots[0]] * 4 + [snapshots[1]] * 2)
                task._quick_hunt_dialog_snapshot = Mock(
                    side_effect=lambda *_: next(sequence, snapshots[1]),
                )
                task.capture_frame = Mock(return_value=frame)
                task._quick_hunt_click_ocr = Mock(return_value=True)
                clock = {"now": 0.0}
                task.sleep = lambda seconds: clock.update(now=clock["now"] + seconds)
                with patch("src.tasks.quick_hunt.monotonic", side_effect=lambda: clock["now"]):
                    result = task._quick_hunt_prepare_dialog(
                        "MAX", "圣石", r"暗.?之?.?洞穴", "火把", 60,
                    )
                self.assertEqual(accepted, result is not None)
                if accepted:
                    self.assertEqual((10, 60, 60), (result.count, result.cost, result.pool))
                vision.click_client.assert_not_called()

    def test_quick_hunt_max_rechecks_after_input_wait_and_retries_only_with_safe_cost(self):
        for lost_first, valid_cost in ((False, True), (True, True), (True, False)):
            with self.subTest(lost_first=lost_first, valid_cost=valid_cost):
                task = object.__new__(QuickHuntTask)
                task.config = {"快速狩猎界面等待秒数": 5.0}
                task.info_set = Mock()
                task.log_info = Mock()
                now = [0.0]
                task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
                task._executor = SimpleNamespace(
                    check_enabled=lambda: None,
                    exit_event=SimpleNamespace(wait=task.sleep),
                    reset_scene=lambda **_: None,
                )
                initial = hunt_dialog(pool=60, resource="火把", map_name="黑暗洞穴极难")
                selected = replace(initial, count=10, cost=60 if valid_cost else 66)
                current = [initial]
                task.capture_frame = Mock(return_value=None)
                task._quick_hunt_dialog_snapshot = Mock(side_effect=lambda *_: current[0])
                clicks = []

                def click(*_args, **_kwargs):
                    clicks.append(now[0])
                    if len(clicks) == 1:
                        task._wait_for_input(15.0)
                    if not lost_first or len(clicks) > 1:
                        current[0] = selected
                    task.sleep(.8)
                    return True

                task._quick_hunt_click_ocr = click
                with (
                    patch("src.tasks.quick_hunt.monotonic", side_effect=lambda: now[0]),
                    patch("src.tasks.BaseBD2Task.monotonic", side_effect=lambda: now[0]),
                ):
                    result = task._quick_hunt_prepare_dialog("MAX", "圣石", "黑暗", "火把", 60)
                self.assertEqual(valid_cost, result is not None)
                self.assertGreaterEqual(len(clicks), 2 if lost_first else 1)
                self.assertLessEqual(len(clicks), 3)
                if result is not None:
                    self.assertEqual((10, 60, 60), (result.count, result.cost, result.pool))
                self.assertLess(now[0], 22)

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
                    ocr_boxes=lambda *_args, **_kwargs: task.ocr(),
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


    def test_guest_collection_clears_season_overlay_before_clicking_restaurant(self):
        for width, height in ((1280, 720), (1920, 1080)):
            for mode in (
                "early", "late", "stuck", "title_only", "dismiss_only", "split", "other_page",
            ):
                with self.subTest(size=(width, height), mode=mode):
                    def box(name, x, y):
                        return SimpleNamespace(name=name, x=(x - .01) * width,
                                               y=(y - .01) * height,
                                               width=.02 * width, height=.02 * height)

                    frame = np.full((height, width, 3), 230, dtype=np.uint8)
                    popup = self._business_controls(width, height)
                    popup.append(box("立刻前往", .7, .3))
                    restaurant = [box("格鲁菲餐厅", .1, .05), box("常客", .15, .3)]
                    title = box("魔兽追踪者赛季奖励", .65, .28)
                    dismiss = box("奖励已发放至背包。", .7, .8)
                    reads, after_close = [0], [0]

                    def screen(actions, _now):
                        if (.7, .3) not in actions:
                            return frame, popup, False
                        reads[0] += 1
                        if (.15, .3) in actions:
                            self.assertGreaterEqual(after_close[0], 4)
                            return frame, [box("火圣石×10", .5, .5)], False
                        if mode == "late" and reads[0] == 1:
                            return frame, restaurant[:1], False
                        if (.7, .8) in actions and mode != "stuck":
                            after_close[0] += 1
                            if mode == "other_page":
                                return frame, [box("其他页面", .5, .5)], False
                            # 可读清屏帧之间插入空白帧，不能拼接成连续关闭确认。
                            return frame, ([] if after_close[0] == 2 else restaurant), False
                        overlay = [title, dismiss]
                        if mode == "title_only":
                            overlay = [title]
                        elif mode == "dismiss_only":
                            overlay = [dismiss]
                        elif mode == "split":
                            overlay = [title] if reads[0] % 2 else [dismiss]
                        return frame, restaurant + overlay, False

                    task, statuses, clicks, _, clock = self._business_flow_task(screen)
                    del task._collect_business_guest_stones
                    task.config["loading 消失等待秒数"] = 5.0
                    task._quick_vision = Mock(return_value=SimpleNamespace(
                        ocr_boxes=lambda *_args, **_kwargs: task.ocr(),
                    ))
                    expected = mode in {"early", "late"}
                    self.assertEqual(expected, task._collect_business_guest_stones())
                    expected_clicks = [(.7, .3)]
                    if mode not in {"title_only", "dismiss_only", "split"}:
                        expected_clicks.append((.7, .8))
                    if expected:
                        expected_clicks.append((.15, .3))
                        self.assertEqual("已领取", statuses["常客圣石结果"])
                    self.assertEqual(expected_clicks, clicks)
                    self.assertLess(clock[0], 18.0)

    def test_guest_recheck_scales_crop_and_click_bounds_and_rejects_ambiguity(self):
        for height, width, roi in ((1080, 1920, (154, 205, 288, 173)),
                                   (720, 1280, (102, 137, 192, 115))):
            for ambiguous in (False, True):
                with self.subTest(size=(width, height), ambiguous=ambiguous):
                    frame = np.full((height, width, 3), 120, dtype=np.uint8)
                    clock, clicks, crops = [0.0], [], []
                    task = object.__new__(DailyTask)
                    task.config = {}
                    task.capture_frame = lambda: frame
                    task.info_set = Mock()
                    task.log_info = Mock()
                    task.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
                    task._save_flow_diagnostic = Mock()
                    task._click_business_bounds = lambda image, bounds, after_sleep: (
                        self.assertIs(image, frame), clicks.append(bounds), task.sleep(after_sleep),
                    )

                    def box(name, x=0, y=0, w=40, h=20):
                        return SimpleNamespace(name=name, x=x, y=y, width=w, height=h)

                    def ocr(*, frame, name, **kwargs):
                        if name == "餐馆入口":
                            return [box("餐馆营业额", 10, 10), box("立刻前往", 100, 10)]
                        if len(clicks) == 2:
                            return [box("火圣石×10")]
                        if name == "常客圣石":
                            return [box("格鲁菲餐厅"), box("常客笔记")]
                        self.assertEqual("常客按钮复核", name)
                        self.assertEqual(0, kwargs["target_height"])
                        left, top, crop_width, crop_height = roi
                        self.assertEqual((crop_height * 2, crop_width * 2, 3), frame.shape)
                        crops.append(frame.shape)
                        guest = box("常客", (width * .15 - left) * 2 - 20,
                                    (height * .3 - top) * 2 - 10)
                        return [guest] * (2 if ambiguous else 1)

                    task.ocr = ocr
                    task._quick_vision = lambda: Vision(task)
                    with patch("src.tasks.DailyTask.monotonic", side_effect=lambda: clock[0]):
                        self.assertEqual(not ambiguous, task._collect_business_guest_stones())
                    self.assertTrue(crops)
                    self.assertEqual(1 if ambiguous else 2, len(clicks))
                    if not ambiguous:
                        x, y, w, h = clicks[-1]
                        self.assertAlmostEqual(.15, (x + w / 2) / width)
                        self.assertAlmostEqual(.3, (y + h / 2) / height)
        self.assertEqual((.08, .19, .23, .35), BUSINESS_GUEST_RELATIVE_ROI)

    def test_business_collect_requires_restaurant_entry_and_arrival(self):
        for has_restaurant_go in (False, True):
            with self.subTest(go=has_restaurant_go):
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
                self.assertFalse(task.run_business_collect())
                self.assertEqual(3 if has_restaurant_go else 2, len(clicks))
                self.assertEqual("未确认", statuses["常客圣石结果"])
                task.recover_daily_home.assert_called_once()



class QuickHuntRecoveryTest(StageHistoryTestCase):
    def setUp(self):
        super().setUp()
        from src.tasks import run_history
        self.day = run_history.day_start_ts()
        self._reset_hunt()
        self.enterContext(patch("src.tasks.quick_hunt.time", lambda: self.wall + self.clock.now))
        self.enterContext(patch("src.tasks.run_history.time.time",
                                lambda: self.wall + self.clock.now))

    def _reset_hunt(self):
        self.reset_stage_history()
        self.clock, self.wall = FakeClock(), self.day + 3600
        task = self.task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎结算等待秒数": 1.0, "快速狩猎界面等待秒数": 1.0}
        task.info_set = task.log_info = Mock()
        task._active_time = lambda clock: self.clock.now
        task.sleep = self.clock.sleep
        task.capture_frame = Mock(return_value=object())
        task._quick_hunt_is_menu_restored = Mock(return_value=True)
        task._quick_hunt_free_resource = Mock(return_value=90)
        task._quick_hunt_settlement_signals = Mock(return_value=(False, False))
        task._quick_hunt_open_confirmation = Mock(return_value=True)
        task._quick_hunt_prepare_dialog = Mock(return_value=hunt_dialog())
        task._quick_hunt_dialog_snapshot = Mock(return_value=None)
        task._quick_vision = Mock(return_value=SimpleNamespace(click_client=Mock()))
        task._save_flow_diagnostic = Mock()
        task.operate_click = Mock(side_effect=lambda *_, after_sleep=0:
                                  self.clock.sleep(after_sleep))

    def _seed(self, step="金币 MIN", *, days=0, resource="米饭", at=None, evidence=None):
        from src.tasks import run_history
        cycle = self.day + days * 86400
        at = cycle + 1800 if at is None else at
        scope = f"{cycle}:{run_history.configuration_scope(self.task)}"
        data = {"map": "哥布林遗迹极难", "count": 1, "cost": 6, "pool": 90,
                "resource": resource, "phase": "attempted", "start_clicks": 1,
                "prepared_at": at, **(evidence or {})}
        key = f"狩猎提交:{resource}:{step}"
        with patch("src.tasks.run_history.time.time", return_value=at):
            self.history.save_stage("QuickHuntTask", key, "pending", scope, data)
        return key, self.history.stage("QuickHuntTask", key)

    def test_expired_or_unsent_pending_retires_only_its_resource_before_new_submission(self):
        for days, step, unsent in ((-1, "金币 MIN", False), (-7, "旧版金币 MAX", False),
                                   (0, "金币 MIN", True)):
            with self.subTest(days=days, step=step, unsent=unsent):
                self._reset_hunt()
                key, original = self._seed(step, days=days, evidence=(
                    {"phase": "prepared", "start_clicks": 0} if unsent else {}))
                other, torch = self._seed(resource="火把")
                self.assertEqual("failed", self.task._quick_hunt_execute_current_map(
                    "MIN", "金币 MIN"))
                self.task._quick_vision().click_client.assert_called_once()
                with open(self.history.path, encoding="utf-8") as stream:
                    retired = json.load(stream)["retired_stages"]
                self.assertEqual(original, retired[-1]["record"])
                self.assertEqual(torch, self.history.stage("QuickHuntTask", other))
                new = self.history.stage("QuickHuntTask", "狩猎提交:米饭:金币 MIN")
                self.assertTrue(new["scope"].startswith(str(self.day) + ":"))
                self.assertEqual(1, new["evidence"]["start_clicks"])
                self.assertEqual("transitioned", new["evidence"]["phase"])
                if key != "狩猎提交:米饭:金币 MIN":
                    self.assertFalse(self.history.stage("QuickHuntTask", key))

    def test_pending_retirement_rejects_uncertain_cycle_menu_balance_or_active_request(self):
        for case in ("future", "invalid_scope", "time_conflict", "unknown_balance",
                     "changing_balance", "not_menu", "active", "just_crossed", "wrong_resource",
                     "archive_write_failure", "ambiguous_rewards"):
            with self.subTest(case=case):
                self._reset_hunt()
                at = self.day - .5 if case == "just_crossed" else None
                if at is not None:
                    self.wall = self.day + .1
                days = 1 if case == "future" else (0 if case == "ambiguous_rewards" else -1)
                key, record = self._seed(days=days, at=at)
                if case == "ambiguous_rewards":
                    self._seed(resource="火把")
                    self.task._quick_hunt_settlement_signals.return_value = (True, False)
                if case in ("invalid_scope", "time_conflict", "wrong_resource"):
                    group = self.history._stages["QuickHuntTask"][key]
                    if case == "invalid_scope":
                        group["scope"] = "unknown"
                    elif case == "time_conflict":
                        group["evidence"]["prepared_at"] = self.day + 1
                    else:
                        group["evidence"]["resource"] = "火把"
                    self.history._save()
                    record = self.history.stage("QuickHuntTask", key)
                if case == "unknown_balance":
                    self.task._quick_hunt_free_resource.return_value = None
                if case == "changing_balance":
                    self.task._quick_hunt_free_resource.side_effect = [89, 90]
                if case == "not_menu":
                    self.task._quick_hunt_is_menu_restored.return_value = False
                if case == "active":
                    self.task._hunt_checkpoint = (key, record["scope"], record["evidence"])
                if case == "archive_write_failure":
                    with patch.object(self.history, "_save", return_value=False):
                        with self.assertRaises(RuntimeError):
                            self.task._quick_hunt_reconcile_pending("米饭")
                else:
                    self.assertFalse(self.task._quick_hunt_reconcile_pending("米饭"))
                self.assertEqual(record, self.history.stage("QuickHuntTask", key))
                self.task._quick_hunt_open_confirmation.assert_not_called()
                self.task._quick_vision.assert_not_called()

    def test_changed_configuration_recovers_read_only_and_preserves_original_scope(self):
        for reward in (False, True):
            with self.subTest(reward=reward):
                self._reset_hunt()
                key, original = self._seed(evidence={"start_clicks": 3})
                self.task.config["快速狩猎 OCR 阈值"] = .4
                self.task.config["快速狩猎界面等待秒数"] = 2.0
                self.task._quick_hunt_settlement_signals.side_effect = lambda *_: (
                    reward and self.task.operate_click.call_count == 0, False)
                expected = "done" if reward else "failed"
                self.assertEqual(expected, self.task._quick_hunt_execute_current_map(
                    "MIN", "金币 MIN"))
                from src.tasks.run_history import RunHistoryStore
                self.history_mock.return_value = RunHistoryStore(self.history.path)
                self.task._hunt_checkpoint = None
                self.assertEqual(expected, self.task._quick_hunt_execute_current_map(
                    "MIN", "金币 MIN"))
                record = self.history_mock.return_value.stage("QuickHuntTask", key)
                self.assertEqual(original["scope"], record["scope"])
                self.assertEqual(3, record["evidence"]["start_clicks"])
                self.task._quick_hunt_open_confirmation.assert_not_called()
                self.task._quick_vision.assert_not_called()

    def test_start_attempts_are_durable_and_failed_writes_never_dispatch(self):
        for failed_write, expected_clicks in ((None, 3), (1, 0), (2, 0), (3, 1)):
            with self.subTest(failed_write=failed_write):
                self._reset_hunt()
                self.task.config["快速狩猎结算等待秒数"] = 5.0
                self.task._quick_hunt_dialog_snapshot.return_value = hunt_dialog()
                writes = [0]
                save = self.history._save

                def persist():
                    writes[0] += 1
                    return False if writes[0] == failed_write else save()

                with patch.object(self.history, "_save", side_effect=persist):
                    if failed_write is not None:
                        with self.assertRaises(RuntimeError):
                            self.task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                    else:
                        self.assertEqual("failed", self.task._quick_hunt_execute_current_map(
                            "MIN", "金币 MIN"))
                self.assertEqual(expected_clicks, self.task._quick_vision().click_client.call_count)
                if expected_clicks:
                    from src.tasks.run_history import RunHistoryStore
                    store = RunHistoryStore(self.history.path)
                    self.history_mock.return_value = store
                    self.task._hunt_checkpoint = None
                    self.assertEqual(expected_clicks, store.stage(
                        "QuickHuntTask", "狩猎提交:米饭:金币 MIN")["evidence"]["start_clicks"])
                    self.assertEqual("failed", self.task._quick_hunt_execute_current_map(
                        "MIN", "金币 MIN"))
                    self.assertEqual(expected_clicks,
                                     self.task._quick_vision().click_client.call_count)

    def test_late_cleanup_settles_before_close_but_cancellation_does_not_invent_completion(self):
        for case in ("reward", "cross_cycle", "cancel", "unbound", "write_failure"):
            with self.subTest(case=case):
                self._reset_hunt()
                self.assertEqual("failed", self.task._quick_hunt_execute_current_map(
                    "MIN", "金币 MIN"))
                key = "狩猎提交:米饭:金币 MIN"
                original = self.history.stage("QuickHuntTask", key)
                if case == "cross_cycle":
                    self.wall += 86400
                if case == "unbound":
                    self.task._hunt_checkpoint = None
                home = [False]
                self.task._home_p95_threshold = lambda: 185
                self.task._quick_hunt_current_map_context = lambda frame: "哥布林遗迹"
                self.task._quick_hunt_home_signals = lambda frame: (home[0], 3, 255, "抽抽乐")
                self.task.clear_temporary_home_announcement_if_needed = Mock(return_value=False)
                self.task._quick_hunt_ocr_text = lambda frame, roi, name: (
                    "REWARD" if case != "cancel" and roi == QUICK_HUNT_REWARD_TITLE_ROI else "")
                self.task._quick_hunt_menu_still_open = lambda frame: False
                self.task._quick_hunt_dialog_ocr = lambda frame, name: (True, "", "")

                def close(*args, **kwargs):
                    state = self.history.stage("QuickHuntTask", key)["state"]
                    self.assertEqual("pending" if case in ("cancel", "unbound") else "done", state)
                    home[0] = True
                    return True

                self.task.operate_click = Mock(side_effect=close)
                self.task._quick_hunt_cancel_dialog = Mock(side_effect=close)
                if case == "write_failure":
                    with patch.object(self.history, "_save", return_value=False):
                        with self.assertRaises(RuntimeError):
                            self.task._quick_hunt_return_home()
                    self.task.operate_click.assert_not_called()
                else:
                    self.assertTrue(self.task._quick_hunt_return_home())
                record = self.history.stage("QuickHuntTask", key)
                self.assertEqual(original["scope"], record["scope"])
                self.assertEqual("done" if case in ("reward", "cross_cycle") else "pending",
                                 record["state"])
                self.assertIsNone(self.task._hunt_checkpoint)

    def test_cycle_change_before_dispatch_stops_without_creating_a_new_submission(self):
        from src.tasks import run_history
        for during_dialog in (False, True):
            with self.subTest(during_dialog=during_dialog):
                self._reset_hunt()
                if during_dialog:
                    def prepare(*args):
                        self.wall += 86400
                        return hunt_dialog()
                    self.task._quick_hunt_prepare_dialog.side_effect = prepare
                    self.task._quick_hunt_cancel_dialog = Mock(return_value=True)
                else:
                    self.task._hunt_plan_scope = (
                        f"{self.day - 86400}:{run_history.configuration_scope(self.task)}")
                self.assertEqual("failed", self.task._quick_hunt_execute_current_map(
                    "MIN", "金币 MIN"))
                self.task._quick_vision.assert_not_called()
                self.assertFalse(self.history.pending_stages("QuickHuntTask", "狩猎提交:"))
                if during_dialog:
                    self.task._quick_hunt_cancel_dialog.assert_called_once()
                else:
                    self.task._quick_hunt_open_confirmation.assert_not_called()

    def test_plan_cycle_validation_and_late_finish_preserve_the_original_allowance(self):
        from src.tasks import run_history
        plan = {"name": "一次", "reserve": 0, "rules": [],
                "fallback": [{"target": "hunt", "mode": "count", "count": 1}]}
        for scope in ("invalid", f"{self.day + 86400}:0123456789abcdef"):
            with self.subTest(scope=scope):
                self._reset_hunt()
                self.task.config[PLAN_KEY] = json.dumps(
                    {"version": 1, "active": 0, "plans": [plan]})
                run_history.save_stage(self.task, "狩猎方案", "pending", scope=scope,
                                       evidence={"allowance": 6})
                original = run_history.read_stage(self.task, "狩猎方案")
                self.assertFalse(self.task._quick_hunt_run_rice_scheduler())
                self.assertEqual(original, run_history.read_stage(self.task, "狩猎方案"))
                self.task._quick_hunt_free_resource.assert_not_called()
        self._reset_hunt()
        self.task.config[PLAN_KEY] = json.dumps({"version": 1, "active": 0, "plans": [plan]})
        self.task._quick_hunt_select_plan_target = Mock(return_value=True)

        def finish(*args, **kwargs):
            self.wall += 86400
            self.task._quick_hunt_settle_submission("done")
            return "done"

        self.task._quick_hunt_wait_result = Mock(side_effect=finish)
        for days in (0, 1):
            self.assertTrue(self.task._quick_hunt_run_rice_scheduler())
            record = run_history.read_stage(self.task, "狩猎方案")
            self.assertEqual("done", record["state"])
            self.assertEqual(self.day + days * 86400, self.task._quick_hunt_record_cycle(record))
            self.assertEqual(90, record["evidence"]["allowance"])
        self.assertEqual(2, self.task._quick_vision().click_client.call_count)


if __name__ == "__main__":
    unittest.main()
