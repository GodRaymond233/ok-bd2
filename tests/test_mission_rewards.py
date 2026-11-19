import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2

from src.tasks.mission_rewards import (
    DISABLED_BUTTON_FILE,
    DISABLED_BUTTON_ROI,
    SECTIONS,
    RewardView,
    home_mission_entry,
    mission_reward_view,
)
from src.tasks.MissionRewardTask import MissionRewardTask
from tests.helpers import FakeClock

MODULE = importlib.import_module("src.tasks.MissionRewardTask")
FIXTURES = Path(__file__).parent / "fixtures/mission_rewards"


def fixture(name):
    frame = cv2.imread(str(FIXTURES / f"{name}.png"))
    records = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    boxes = []
    for item in records:
        xs, ys = zip(*item["polygon"])
        boxes.append(SimpleNamespace(name=item["text"], confidence=item["confidence"],
                                     x=min(xs), y=min(ys), width=max(xs) - min(xs),
                                     height=max(ys) - min(ys)))
    return frame, boxes


class MissionRewardVisionTest(unittest.TestCase):
    def setUp(self):
        self.template = cv2.imread(str(DISABLED_BUTTON_FILE))

    def test_observed_home_entry_is_unique_and_in_bottom_navigation(self):
        boxes = [SimpleNamespace(name="任务", x=658, y=1005, width=44, height=26)]
        self.assertEqual((680 / 1920, 1018 / 1080), home_mission_entry(boxes, 1920, 1080))
        self.assertIsNone(home_mission_entry(boxes * 2, 1920, 1080))
        boxes[0].y = 200
        self.assertIsNone(home_mission_entry(boxes, 1920, 1080))

    def test_real_claimed_daily_and_partial_weekly_are_both_empty(self):
        for name, section in zip(("daily", "weekly"), SECTIONS):
            frame, boxes = fixture(name)
            view = mission_reward_view(frame, boxes, self.template)
            self.assertEqual(("empty", section), (view.state, view.section))
            self.assertEqual(set(SECTIONS), set(dict(view.tabs)))
            self.assertTrue(.81 < view.point[0] < .94)

    def test_real_available_reward_and_observed_result_popup(self):
        frame, boxes = fixture("weekly_available")
        view = mission_reward_view(frame, boxes, self.template)
        self.assertEqual(("claim", SECTIONS[1]), (view.state, view.section))
        frame, boxes = fixture("reward_popup")
        view = mission_reward_view(frame, boxes, self.template)
        self.assertEqual("popup", view.state)
        self.assertTrue(.85 < view.point[1] < .97)
        for missing in ("REWARD", "获得1种", "点击画面即可返回。"):
            self.assertNotEqual("popup", mission_reward_view(
                frame, [b for b in boxes if b.name != missing], self.template).state)

    def test_supported_resolutions_preserve_disabled_state(self):
        frame, boxes = fixture("weekly")
        for width, height in ((1280, 720), (1366, 768), (2560, 1440), (3840, 2160)):
            sx, sy = width / 1920, height / 1080
            scaled = [SimpleNamespace(name=b.name, x=b.x * sx, y=b.y * sy,
                                      width=b.width * sx, height=b.height * sy) for b in boxes]
            with self.subTest(size=(width, height)):
                view = mission_reward_view(
                    cv2.resize(frame, (width, height)), scaled, self.template)
                self.assertEqual("empty", view.state)

    def test_unreadable_incomplete_or_darkened_page_is_never_empty(self):
        frame, boxes = fixture("daily")
        for missing in ("全部获得", "区间奖励", "每周任务", "每日任务"):
            remaining = [b for b in boxes if b.name != missing]
            self.assertEqual("unknown", mission_reward_view(frame, remaining, self.template).state)
        self.assertEqual("unknown", mission_reward_view(frame // 2, boxes, self.template).state)
        self.assertEqual("unknown", mission_reward_view(frame, boxes, None).state)

    def test_non_disabled_button_only_permits_bulk_claim(self):
        frame, boxes = fixture("daily")
        x1, y1, x2, y2 = DISABLED_BUTTON_ROI
        frame[y1:y2, x1:x2] = 230
        view = mission_reward_view(frame, boxes, self.template)
        self.assertEqual("claim", view.state)
        expected = next(b for b in boxes if b.name == "全部获得")
        self.assertEqual(((expected.x + expected.width / 2) / 1920,
                          (expected.y + expected.height / 2) / 1080), view.point)

    def test_bright_button_text_cannot_be_averaged_into_disabled_state(self):
        frame, boxes = fixture("daily")
        x1, y1, _, _ = DISABLED_BUTTON_ROI
        label = frame[y1 + 9:y1 + 34, x1 + 65:x1 + 150]
        label[cv2.cvtColor(label, cv2.COLOR_BGR2GRAY) < 65] = 230
        self.assertEqual("claim", mission_reward_view(frame, boxes, self.template).state)

    def test_rewards_popup_does_not_accept_selection_or_purchase_dialog(self):
        frame, _ = fixture("daily")
        title = SimpleNamespace(name="获得奖励", x=850, y=350, width=180, height=40)
        view = mission_reward_view(frame, [title], self.template)
        self.assertEqual("popup", view.state)
        for text in ("请选择奖励", "消耗钻石", "购买"):
            danger = SimpleNamespace(name=text, x=850, y=500, width=150, height=30)
            self.assertEqual("unknown", mission_reward_view(frame, [title, danger],
                                                            self.template).state)

    def test_sidebar_tab_ocr_noise_no_longer_fails_whole_page(self):
        """Regression for 2026-11-13: 左栏「每周任务」被读成「日每周任务」。

        旧代码只剥数字前缀，页签匹配数为 0 就把整页判成 unknown，
        等待 12 秒后一键日常报「进入任务页」失败。
        """
        frame, boxes = fixture("daily")
        tab = next(b for b in boxes if b.name == "每周任务")
        for noisy in ("日每周任务", "2 每周任务", "0每周任务",
                      "每週任務", "@每周任务", "每周任务0"):
            with self.subTest(tab=noisy):
                tab.name = noisy
                view = mission_reward_view(frame, boxes, self.template)
                self.assertEqual(("empty", SECTIONS[0]), (view.state, view.section))
                self.assertEqual(set(SECTIONS), set(dict(view.tabs)))
                self.assertEqual("", view.diagnostics)
        tab.name = "每周任务"

    def test_duplicated_tab_boxes_pick_one_without_failing(self):
        frame, boxes = fixture("daily")
        tab = next(b for b in boxes if b.name == "每周任务")
        duplicate = SimpleNamespace(name="每周任务", x=tab.x, y=tab.y + 3,
                                    width=tab.width, height=tab.height)
        view = mission_reward_view(frame, boxes + [duplicate], self.template)
        self.assertEqual(("empty", SECTIONS[0]), (view.state, view.section))
        self.assertEqual(set(SECTIONS), set(dict(view.tabs)))

    def test_long_mission_text_is_never_mistaken_for_a_tab(self):
        frame, boxes = fixture("daily")
        tab = next(b for b in boxes if b.name == "每周任务")
        tab.name = "每周任务完成数11"
        view = mission_reward_view(frame, boxes, self.template)
        self.assertEqual("unknown", view.state)
        self.assertIn("页签未识别", view.diagnostics)

    def test_unknown_view_reports_which_evidence_was_missing(self):
        frame, boxes = fixture("daily")
        view = mission_reward_view(frame, [b for b in boxes if b.name != "区间奖励"],
                                   self.template)
        self.assertEqual("unknown", view.state)
        self.assertIn("数量异常", view.diagnostics)
        view = mission_reward_view(frame, boxes, None)
        self.assertEqual("unknown", view.state)
        self.assertIn("禁用态素材", view.diagnostics)


class MissionRewardFlowTest(unittest.TestCase):
    def make_task(self, views, **config):
        task = object.__new__(MissionRewardTask)
        task.config = {"领奖等待秒数": 4, "最多领取次数": 2, **config}
        task.info = {}
        task.info_set = task.info.__setitem__
        task.operate_click = Mock()
        task._read_view = Mock(side_effect=[(view, [], None) for view in views])
        self.clock = FakeClock()
        task.sleep = self.clock.sleep
        return task

    def claim(self, task):
        with patch.object(MODULE, "monotonic", self.clock):
            return task._claim_section(SECTIONS[0])

    def test_claim_popup_then_two_empty_frames_proves_success(self):
        section = SECTIONS[0]
        task = self.make_task([RewardView("claim", section, (.88, .93)),
                               *[RewardView("popup", point=(.5, .9))] * 3,
                               RewardView("empty", section), RewardView("empty", section)])
        self.assertTrue(self.claim(task))
        self.assertEqual(2, task.operate_click.call_count)
        task.operate_click.assert_any_call(.88, .93, after_sleep=.6)
        task.operate_click.assert_any_call(.5, .9, after_sleep=.6)

    def test_popup_can_be_dismissed_but_never_proves_success_alone(self):
        task = self.make_task([RewardView("popup", point=(.5, .9))] * 10)
        self.assertFalse(self.claim(task))
        self.assertGreater(task.operate_click.call_count, 0)

    def test_unreadable_frame_resets_exhaustion_and_never_clicks(self):
        section = SECTIONS[0]
        task = self.make_task([RewardView("empty", section), RewardView()] * 5)
        self.assertFalse(self.claim(task))
        task.operate_click.assert_not_called()

    def test_wrong_section_cannot_be_claimed_or_count_as_empty(self):
        task = self.make_task([RewardView("claim", SECTIONS[1], (.88, .93))] * 10)
        self.assertFalse(self.claim(task))
        task.operate_click.assert_not_called()

    def test_claims_are_bounded_when_button_never_changes(self):
        task = self.make_task([RewardView("claim", SECTIONS[0], (.88, .93))] * 10,
                              领奖等待秒数=12, 最多领取次数=2)
        self.assertFalse(self.claim(task))
        self.assertEqual(2, task.operate_click.call_count)

    def test_return_home_failure_does_not_report_completion(self):
        view = RewardView("empty", SECTIONS[1])
        task = self.make_task([view], 领取每日奖励=False)
        task._disabled_button = object()
        task._wait_page = Mock(return_value=view)
        task._claim_section = Mock(return_value=True)
        task.auto_return_main_home = Mock(return_value=False)
        task.log_completion = Mock()
        self.assertFalse(task.run())
        task._claim_section.assert_called_once_with(SECTIONS[1])
        task.log_completion.assert_not_called()

    def test_both_sections_are_checked_in_order_and_then_return_home(self):
        daily = RewardView("empty", SECTIONS[0], tabs=((SECTIONS[1], (.14, .20)),))
        weekly = RewardView("empty", SECTIONS[1])
        task = self.make_task([daily])
        task._disabled_button = object()
        task._wait_page = Mock(side_effect=[daily, daily, weekly])
        task._claim_section = Mock(return_value=True)
        task.auto_return_main_home = Mock(return_value=True)
        task.log_completion = Mock()
        self.assertTrue(task.run())
        self.assertEqual(list(SECTIONS), [call.args[0] for call in
                                          task._claim_section.call_args_list])
        task.operate_click.assert_called_once_with(.14, .20, after_sleep=.6)
        task.auto_return_main_home.assert_called_once_with(notify_failure=False)

    def test_starting_from_home_uses_the_observed_label_and_rechecks_home(self):
        task = self.make_task([])
        task._disabled_button = object()
        entry = SimpleNamespace(name="任务", x=658, y=1005, width=44, height=26)
        frame = SimpleNamespace(shape=(1080, 1920, 3))
        task._read_view = Mock(return_value=(RewardView(), [entry], frame))
        task._wait_for_home_confirmation = Mock(return_value=True)
        task._frame_confirms_home = Mock(return_value=True)
        task._wait_page = Mock(return_value=RewardView("empty", SECTIONS[0],
                                                      tabs=((SECTIONS[1], (.14, .20)),)))
        task._claim_section = Mock(return_value=True)
        task.auto_return_main_home = Mock(return_value=True)
        task.log_completion = Mock()
        self.assertTrue(task.run())
        task.operate_click.assert_any_call(680 / 1920, 1018 / 1080, after_sleep=.8)
        task.operate_click.reset_mock()
        task._frame_confirms_home.return_value = False
        self.assertFalse(task.run())
        task.operate_click.assert_not_called()

    def test_unknown_view_diagnostics_are_logged_only_when_reason_changes(self):
        frame, boxes = fixture("daily")
        task = object.__new__(MissionRewardTask)
        task.config = {"奖励 OCR 阈值": .55}
        task.info_set = Mock()
        task.capture_frame = Mock(return_value=frame)
        task.ocr = Mock(return_value=boxes)
        task._disabled_button = None
        task._last_view_diagnostic = ""
        for _ in range(3):
            task._read_view()
        reasons = [call.args[1] for call in task.info_set.call_args_list
                   if call.args[0] == "页面判定诊断"]
        self.assertEqual(["按钮区域为空或缺少禁用态素材"], reasons)
        task.ocr = Mock(return_value=[])
        task._read_view()
        reasons = [call.args[1] for call in task.info_set.call_args_list
                   if call.args[0] == "页面判定诊断"]
        self.assertEqual(2, len(reasons))
        self.assertIn("页签未识别", reasons[1])


if __name__ == "__main__":
    unittest.main()
