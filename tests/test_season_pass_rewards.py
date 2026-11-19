import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks.mission_rewards import SECTIONS, RewardView, home_pass_entry, season_pass_view
from src.tasks.MissionRewardTask import MissionRewardTask

WIDTH, HEIGHT = 1280, 720
GUIDE = "指南通行证Ⅱ"
SPECIAL = "孤注一掷特别赛季通行证"
REGULAR = "Blood Royale赛季通行证"
CHARACTER = "沙漠之花席比雅角色通行证"
PASS_LABELS = (
    ("爱通行证", .16, .048),
    (GUIDE, .20, .324),
    (SPECIAL, .219, .422),
    (REGULAR, .219, .521),
    (CHARACTER, .219, .620),
    ("LEVEL", .503, .294),
    ("基础", .593, .294),
    ("购买 ￥3,390", .728, .372),
    ("购买等级500", .38, .706),
    ("全部获得", .749, .707),
)


def boxes(labels):
    return [SimpleNamespace(name=text, x=x * WIDTH - 15, y=y * HEIGHT - 7,
                            width=30, height=14) for text, x, y in labels]


def pass_frame(selected_top, brightness=255):
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[selected_top:selected_top + 2, 214:350] = (133, 205, 230)
    frame[selected_top:selected_top + 60, 214:216] = (133, 205, 230)
    frame[493:531, 884:1018] = brightness
    return frame


class SeasonPassViewTest(unittest.TestCase):
    def test_home_shortcut_excludes_navigation_titles(self):
        labels = (("通行证", .17, .05), ("通行证", .864, .281))
        x, y = home_pass_entry(boxes(labels), WIDTH, HEIGHT)
        self.assertAlmostEqual(.864, x)
        self.assertAlmostEqual(.281, y)

    def test_guide_card_does_not_offer_a_claim(self):
        view = season_pass_view(pass_frame(183), boxes(PASS_LABELS))
        self.assertEqual("list", view.state)
        self.assertIsNone(view.section)
        self.assertEqual(
            [SPECIAL, "bloodroyale赛季通行证", CHARACTER],
            [name for name, _ in view.tabs],
        )

    def test_each_card_requires_its_own_selected_border(self):
        selections = ((254, SPECIAL), (325, "bloodroyale赛季通行证"), (396, CHARACTER))
        for selected_top, name in selections:
            with self.subTest(name=name):
                view = season_pass_view(pass_frame(selected_top), boxes(PASS_LABELS))
                self.assertEqual(("claim", name), (view.state, view.section))
                self.assertEqual((.749, .707), view.point)

    def test_grey_claim_button_proves_empty(self):
        for selected_top, name in ((254, SPECIAL), (396, CHARACTER)):
            with self.subTest(name=name):
                view = season_pass_view(pass_frame(selected_top, 128), boxes(PASS_LABELS))
                self.assertEqual(("empty", name), (view.state, view.section))

    def test_confirm_dialog_blocks_claim_click(self):
        labels = PASS_LABELS + (("购买等级", .50, .45), ("确认", .58, .72))
        view = season_pass_view(pass_frame(254), boxes(labels))
        self.assertEqual("unknown", view.state)
        self.assertIsNone(view.point)

    def test_recognizes_reward_result_overlay(self):
        labels = (("REWARD", .50, .27), ("获得2种", .50, .37), ("点击画面即可返回", .50, .91))
        view = season_pass_view(pass_frame(254), boxes(PASS_LABELS + labels))
        self.assertEqual(("popup", (.50, .91)), (view.state, view.point))


class SeasonPassTaskTest(unittest.TestCase):
    def test_default_flow_checks_season_passes_after_missions(self):
        task = object.__new__(MissionRewardTask)
        task.config = {}
        events = []
        task._claim_missions = lambda sections: events.append(tuple(sections)) or True
        task._claim_season_passes = lambda: events.append("season passes") or True
        task.auto_return_main_home = lambda **_kwargs: events.append("home") or True
        task._return_from_pass = lambda: events.append("pass back") or True
        task.info_set = lambda *_args: None
        task.log_completion = lambda *_args: None

        self.assertTrue(task.run())

        self.assertEqual([SECTIONS, "home", "season passes", "pass back"], events)

    def test_visits_season_and_character_pass_and_confirms_disabled_button(self):
        task = object.__new__(MissionRewardTask)
        task.config = {"领取每日奖励": False, "领取每周奖励": False}
        clock = [0.0]
        section_one = SPECIAL
        pass_boxes = boxes([label for label in PASS_LABELS if label[0] != REGULAR])
        card_one, card_two = (.219, .392), (.219, .590)
        button, dismiss = (.749, .707), (.50, .91)
        guide = season_pass_view(pass_frame(183), pass_boxes)
        tabs = guide.tabs
        available = RewardView("claim", section_one, button, tabs)
        cleared = RewardView("empty", section_one, button, tabs)
        other = season_pass_view(pass_frame(396, 128), pass_boxes)
        popup = RewardView("popup", point=dismiss)
        frames = iter([guide, guide, guide, available, available,
                       popup, popup, popup, cleared, cleared,
                       cleared, cleared, other, other, other, other, RewardView()])
        events = []
        task._read_pass_view = lambda: (next(frames), [], None)
        task.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)

        def click(x, y, *, after_sleep=0, **_kwargs):
            events.append((x, y))
            task.sleep(after_sleep)

        task.operate_click = click
        task.info_set = lambda *_args: None
        task.log_completion = lambda *_args: None
        task.auto_return_main_home = Mock()
        task._click_top_left_back = lambda _frame, _attempt: events.append("pass back") or True
        task._frame_confirms_home = Mock(return_value=True)
        with patch("src.tasks.MissionRewardTask.monotonic", side_effect=lambda: clock[0]):
            self.assertTrue(task.run())
        self.assertEqual([card_one, button, dismiss, card_two, "pass back"], events)
        task.auto_return_main_home.assert_not_called()
        task._frame_confirms_home.assert_called_once()

    def test_return_retries_at_most_twice_while_pass_stays_open(self):
        task = object.__new__(MissionRewardTask)
        task.config = {"页面等待秒数": 5}
        clock = [0.0]
        frame = object()
        task._read_pass_view = Mock(return_value=(RewardView("empty", SPECIAL), [], frame))
        task.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        task._click_top_left_back = Mock(return_value=True)
        task._frame_confirms_home = Mock(return_value=True)

        with patch("src.tasks.MissionRewardTask.monotonic", side_effect=lambda: clock[0]):
            self.assertFalse(task._return_from_pass())

        self.assertEqual([(frame, 1), (frame, 2)],
                         [call.args for call in task._click_top_left_back.call_args_list])
        task._frame_confirms_home.assert_not_called()

    def test_daily_rewards_can_start_with_pass_already_open(self):
        task = object.__new__(MissionRewardTask)
        task._disabled_button = object()
        daily = RewardView("empty", SECTIONS[0])
        task._read_view = Mock(side_effect=[
            (RewardView(), boxes(PASS_LABELS), pass_frame(254)),
            (RewardView(), boxes((("任务", .35, .94),)), pass_frame(254)),
        ])
        task._return_from_pass = Mock(return_value=True)
        task._wait_for_home_confirmation = Mock()
        task._frame_confirms_home = Mock(return_value=True)
        task._wait_page = Mock(return_value=daily)
        task._claim_section = Mock(return_value=True)
        task.info_set = Mock()
        task.operate_click = Mock()

        self.assertTrue(task._claim_missions((SECTIONS[0],)))

        task._return_from_pass.assert_called_once()
        task._wait_for_home_confirmation.assert_not_called()
        task._claim_section.assert_called_once_with(SECTIONS[0])
