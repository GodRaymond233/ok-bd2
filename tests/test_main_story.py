import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks.main_story import (
    BATTLE_TURN_CLICKS,
    LIST_BUTTON_ROI,
    REWARD_POPUP_HINT_ROI,
    SKIP_CONFIRM_OK_ROIS,
    MainStoryFeatureMixin,
    MainStoryState,
    classify_main_story_state,
)
from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH


class MainStoryTest(unittest.TestCase):
    def test_battle_turn_limit_waits_for_progress_with_a_bounded_timeout(self):
        turn = MainStoryState.IN_BATTLE_TURN
        for tail, expected in (([MainStoryState.ALL_CLEAR], True), ([turn] * 5, False)):
            with self.subTest(expected=expected):
                task = Mock()
                task.config = {}
                task._main_story_state.side_effect = [
                    (state, state.value) for state in [turn] * BATTLE_TURN_CLICKS + tail
                ]
                task._main_story_battle_turn_point.return_value = (0, 0)
                task._main_story_stuck.return_value = False
                now = [0.0]

                def advance(seconds):
                    now[0] += max(61, seconds)

                task.sleep.side_effect = advance
                with patch("src.tasks.main_story.monotonic", side_effect=lambda: now[0]):
                    self.assertIs(MainStoryFeatureMixin.run_main_story_farm(task), expected)
                self.assertEqual(BATTLE_TURN_CLICKS, task._click_reference.call_count)
                self.assertEqual(int(not expected), task._main_story_stuck.call_count)

    def test_classifies_overlapping_battle_list_and_reward_regions(self):
        state = MainStoryState
        for button, victory, reward, expected in (
            ("", "离 开", "", state.VICTORY),
            ("战斗", "", "", state.LIST_BATTLE),
            ("自动逬行", "", "", state.LIST_AUTO),
            ("领取奖励", "", "", state.LIST_OTHER_BUTTON),
            ("结束自动战斗esc", "结束自动战斗esc", "", state.IN_BATTLE),
            ("turn.battle", "turn.1」battle", "", state.IN_BATTLE_TURN),
            ("", "turn.2 battle", "", state.IN_BATTLE_TURN),
            ("自动进行", "自动进行", "可获得奖励", state.LIST_AUTO),
            ("自动进行", "自动进行", "可获得奖励 点击画面即可返回", state.REWARD_POPUP),
            ("", "", "点击画面即可返凹", state.REWARD_POPUP),
            ("", "", "", state.BUSY),
        ):
            with self.subTest(button=button, victory=victory, reward=reward):
                self.assertIs(classify_main_story_state("", button, victory, reward), expected)

    def test_classifies_story_prompts_using_their_own_regions(self):
        state = MainStoryState
        difficulty = "极难难度?难度仅适用于主线任务战斗。是否开始主线任务？"
        explore = "完成普通难度后，可省略剧情快速进行探索。是否前往快速探索界面？"
        for regions, expected in (
            ({"all_clear_text": "完成主线任务 极 难"}, state.ALL_CLEAR),
            ({"quest_start_text": "主线 开始困难难度"}, state.QUEST_START),
            ({"quest_start_text": "主线"}, state.BUSY),
            ({"difficulty_dialog_text": difficulty, "skip_confirm_text": "极难难度"},
             state.DIFFICULTY_DIALOG),
            ({"fast_explore_text": explore}, state.FAST_EXPLORE_DIALOG),
            ({"cutscene_text": "主线"}, state.BUSY),
            ({"cutscene_text": "F2", "skip_confirm_text": "跳过对话 可于剧情菜单重新查看 确认"},
             state.SKIP_CONFIRM),
            ({"story_text": "都以为自己能全身而退"}, state.STORY_DIALOG),
            ({"story_text": "主线"}, state.BUSY),
        ):
            with self.subTest(regions=regions):
                self.assertIs(classify_main_story_state("", "", "", "", **regions), expected)

    def test_skip_confirmation_excludes_the_cancel_button(self):
        for bounds in SKIP_CONFIRM_OK_ROIS:
            self.assertGreater(bounds[0] * REFERENCE_WIDTH, 900)

    def test_cutscene_bars_require_visible_content_between_dark_edges(self):
        for edge, middle, expected in ((5, 120, True), (120, 120, False), (5, 5, False)):
            with self.subTest(edge=edge, middle=middle):
                frame = np.full((100, 40, 3), middle, dtype=np.uint8)
                frame[:8] = frame[-8:] = edge
                self.assertEqual(MainStoryFeatureMixin._main_story_cutscene_bars(frame), expected)

    def test_reward_box_center_scales_to_reference_coordinates(self):
        crop = np.empty((180, 600, 3), dtype=np.uint8)
        box = SimpleNamespace(x=0, y=0, width=600, height=180)
        point = MainStoryFeatureMixin._main_story_box_point(REWARD_POPUP_HINT_ROI, crop, box)
        x1, y1, x2, y2 = REWARD_POPUP_HINT_ROI
        self.assertAlmostEqual(point[0], (x1 + x2) / 2 * REFERENCE_WIDTH)
        self.assertAlmostEqual(point[1], (y1 + y2) / 2 * REFERENCE_HEIGHT)

    def test_battle_turn_click_targets_the_detected_text(self):
        class BattleTask(MainStoryFeatureMixin):
            def _main_story_ocr_boxes(self, frame, bounds, name, threshold):
                box = SimpleNamespace(x=300, y=40, width=120, height=30, name="BATTLE")
                return [box], np.empty((120, 490, 3), dtype=np.uint8)

        point = BattleTask()._main_story_battle_turn_point(None, 0.2)
        x1, y1, x2, y2 = LIST_BUTTON_ROI
        self.assertAlmostEqual(point[0], (x1 + 360 / 490 * (x2 - x1)) * REFERENCE_WIDTH)
        self.assertAlmostEqual(point[1], (y1 + 55 / 120 * (y2 - y1)) * REFERENCE_HEIGHT)
