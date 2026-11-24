import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.tasks import main_story_walk as walk
from src.tasks.main_story import (
    LIST_BUTTON_ROI,
    SKIP_CONFIRM_OK_ROIS,
    MainStoryFeatureMixin,
    MainStoryState,
    classify_main_story_state,
)
from src.tasks.main_story_mode import FARM_MODE, WALK_MODE, mode_from_evidence
from src.tasks.main_story_walk import MainStoryWalkState as State
from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH
from tests.helpers import FakeClock

FIXTURES = Path(__file__).parent / "fixtures"
PROMPT_CLICK = (1421 + walk.INTERACT_PROMPT_OFFSET[0], 723)


def read_fixture(folder, name):
    data = np.frombuffer((FIXTURES / folder / name).read_bytes(), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class MainStoryFarmTest(unittest.TestCase):
    def test_battle_turn_limit_waits_for_progress_with_a_bounded_timeout(self):
        turn = MainStoryState.IN_BATTLE_TURN
        for tail, expected in (([MainStoryState.ALL_CLEAR], True), ([turn] * 5, False)):
            with self.subTest(expected=expected):
                task = Mock()
                task.config = {}
                task._main_story_state.side_effect = [
                    (state, state.value) for state in [turn] * 200 + tail
                ]
                task._main_story_battle_turn_point.return_value = (0, 0)
                task._main_story_stuck.return_value = False
                now = [0.0]

                def advance(seconds):
                    now[0] += max(61, seconds)

                task.sleep.side_effect = advance
                with patch("src.tasks.main_story.monotonic", side_effect=lambda: now[0]):
                    self.assertIs(MainStoryFeatureMixin.run_main_story_farm(task), expected)
                self.assertEqual(200, task._click_reference.call_count)
                self.assertEqual(int(not expected), task._main_story_stuck.call_count)

    def test_farm_state_resolves_overlapping_ocr_regions(self):
        state = MainStoryState
        difficulty = "极难难度?难度仅适用于主线任务战斗。是否开始主线任务？"
        explore = "完成普通难度后，可省略剧情快速进行探索。是否前往快速探索界面？"
        empty = dict(in_stage_text="", button_text="", victory_text="", reward_text="")
        for regions, expected in (
            ({"victory_text": "离 开"}, state.VICTORY),
            ({"button_text": "战斗"}, state.LIST_BATTLE),
            ({"button_text": "自动逬行"}, state.LIST_AUTO),
            ({"button_text": "领取奖励"}, state.LIST_OTHER_BUTTON),
            (
                {"button_text": "结束自动战斗esc", "victory_text": "结束自动战斗esc"},
                state.IN_BATTLE,
            ),
            (
                {"button_text": "turn.battle", "victory_text": "turn.1」battle"},
                state.IN_BATTLE_TURN,
            ),
            ({"victory_text": "turn.2 battle"}, state.IN_BATTLE_TURN),
            ({"button_text": "自动进行", "reward_text": "可获得奖励"}, state.LIST_AUTO),
            (
                {"button_text": "自动进行", "reward_text": "可获得奖励 点击画面即可返回"},
                state.REWARD_POPUP,
            ),
            ({"reward_text": "点击画面即可返凹"}, state.REWARD_POPUP),
            ({}, state.BUSY),
            ({"all_clear_text": "完成主线任务 极 难"}, state.ALL_CLEAR),
            ({"quest_start_text": "主线 开始困难难度"}, state.QUEST_START),
            ({"quest_start_text": "主线"}, state.BUSY),
            (
                {"difficulty_dialog_text": difficulty, "skip_confirm_text": "极难难度"},
                state.DIFFICULTY_DIALOG,
            ),
            ({"fast_explore_text": explore}, state.FAST_EXPLORE_DIALOG),
            ({"cutscene_text": "主线"}, state.BUSY),
            (
                {"cutscene_text": "F2", "skip_confirm_text": "跳过对话 可于剧情菜单重新查看 确认"},
                state.SKIP_CONFIRM,
            ),
            ({"story_text": "都以为自己能全身而退"}, state.STORY_DIALOG),
            ({"story_text": "主线"}, state.BUSY),
        ):
            with self.subTest(regions=regions):
                self.assertIs(classify_main_story_state(**(empty | regions)), expected)

    def test_click_targets_use_the_recognized_button_in_its_own_region(self):
        task = MainStoryFeatureMixin()
        for method, roi, word in (
            (task._main_story_battle_turn_point, LIST_BUTTON_ROI, "BATTLE"),
            *((task._main_story_skip_confirm_point, roi, "确认") for roi in SKIP_CONFIRM_OK_ROIS),
        ):
            with self.subTest(word=word, roi=roi):
                crop = np.zeros((120, 490, 3), np.uint8)
                box = SimpleNamespace(name=word, x=300, y=40, width=120, height=30)
                task._main_story_ocr_boxes = lambda _, bounds, *_args: (
                    [box] if bounds == roi else [],
                    crop,
                )
                x, y = method(None, 0.2)
                left, top, right, bottom = roi
                self.assertAlmostEqual((left + 360 / 490 * (right - left)) * REFERENCE_WIDTH, x)
                self.assertAlmostEqual((top + 55 / 120 * (bottom - top)) * REFERENCE_HEIGHT, y)
        self.assertTrue(all(roi[0] * REFERENCE_WIDTH > 900 for roi in SKIP_CONFIRM_OK_ROIS))

    def test_walk_evidence_selects_a_mode_only_on_unambiguous_screens(self):
        farm = MainStoryState
        for regions, state, farm_state, mode in (
            (
                {"skip_confirm_text": "跳过对话", "cutscene_text": "F2"},
                State.SKIP_CONFIRM,
                farm.BUSY,
                None,
            ),
            (
                {"reward_text": "点击画面即可返回", "tracker_header_text": "主线"},
                State.REWARD,
                farm.REWARD_POPUP,
                None,
            ),
            (
                {"battle_text": "TURN.1 BATTLE", "victory_text": "TURN.1 BATTLE"},
                State.BATTLE_TURN,
                farm.IN_BATTLE,
                None,
            ),
            (
                {"tracker_start_text": "开始困难难度", "tracker_header_text": "主线"},
                State.LOBBY,
                farm.BUSY,
                None,
            ),
            ({"objective_text": "前往丢弃铁假面的地方"}, State.FIELD, farm.BUSY, WALK_MODE),
            ({"objective_text": "前往丢弃铁假面的地方"}, State.FIELD, farm.IN_STAGE, FARM_MODE),
            ({}, State.UNKNOWN, farm.BUSY, None),
        ):
            with self.subTest(regions=regions, farm=farm_state):
                observed = walk.classify_main_story_walk_state(**regions)
                self.assertIs(observed, state)
                self.assertEqual(mode_from_evidence(farm_state, observed), mode)


class StoryControlVisionTest(unittest.TestCase):
    def setUp(self):
        self.task = walk.MainStoryWalkFeatureMixin()
        self.task.config = {}
        self.task._walk_templates = {}
        self.text = {
            "推主线跳过剧情": "f",
            "推主线中央对白": "怎么劝都留不住最后还是走到了这一步",
        }
        self.task._main_story_ocr = self.ocr

    def ocr(self, frame, bounds, name, threshold):
        if name == "推主线剧情字幕" and bounds != walk.STORY_SUBTITLE_ROIS[-1]:
            return ""
        return self.text.get(name, "")

    def scene(self, name):
        return read_fixture("main_story_navigation", f"{name}.webp")

    def test_skip_icon_requires_its_keycap_and_yields_to_modal_dialogs(self):
        for name in ("secretary-dialogue", "hot-spring-dialogue"):
            with self.subTest(scene=name):
                state, evidence, _, _, action = self.task._walk_state(self.scene(name), 0.2)
                self.assertIs(state, State.CUTSCENE)
                self.assertIn("跳过图标+F2键帽", evidence)
                self.assertAlmostEqual(1640, action.point[0], delta=3)
                self.assertAlmostEqual(60, action.point[1], delta=3)

        for title, expected in (("跳过对话", State.SKIP_CONFIRM), ("请选择回答", State.UNKNOWN)):
            with self.subTest(title=title):
                self.text["推主线跳过对话标题"] = title
                state, _, _, _, action = self.task._walk_state(
                    self.scene("secretary-dialogue"), 0.2
                )
                self.assertIs(state, expected)
                self.assertIsNone(action)

        self.text.pop("推主线跳过对话标题")
        field = self.scene("snowy-field")
        self.assertIsNone(self.task._walk_story_skip_point(field))
        self.assertIsNone(self.task._walk_story_bubble_action(field, 0.2))
        self.assertIsNone(self.task._walk_state(field, 0.2)[-1])
        self.text["推主线追踪条标题"] = "主线"
        state, _, _, _, action = self.task._walk_state(field, 0.2)
        self.assertIs(state, State.FIELD)
        self.assertIsNone(action)

    def test_bubble_needs_text_and_arrow_and_hands_off_to_a_changed_layout(self):
        frame = self.scene("secretary-dialogue")
        frame[43:65, 799:847] = 40  # F2 键帽缺失，图标依然可见。
        state, _, _, _, action = self.task._walk_state(frame, 0.2)
        self.assertIs(state, State.STORY_DIALOG)
        self.assertAlmostEqual(1293, action.point[0], delta=3)
        self.assertAlmostEqual(431, action.point[1], delta=3)

        original = self.text.pop("推主线中央对白")
        self.assertIsNone(self.task._walk_state(frame, 0.2)[-1])
        self.text["推主线中央对白"] = original
        frame[204:225, 635:666] = (214, 225, 234)
        self.assertIsNone(self.task._walk_state(frame, 0.2)[-1])

        self.text.pop("推主线中央对白")
        old = self.task._walk_dialogue_before_skip(self.scene("secretary-dialogue"), 0.2)
        self.assertEqual("", old.line)

        same_layout = walk.StoryAction(
            State.STORY_DIALOG,
            old.point,
            "旧对白",
        )
        self.assertFalse(same_layout.follows_skip(old))

        self.text["推主线剧情字幕"] = "抱歉，但这千载难逢的机会，我绝对不能放过"
        new = self.task._walk_state(self.scene("bottom-dialogue"), 0.2)[-1]
        self.assertTrue(new.follows_skip(old))

    def test_fullscreen_dialogue_tracks_arrow_and_line_across_frames(self):
        frame = self.scene("bottom-dialogue")
        self.text["推主线剧情字幕"] = "抱歉，但这千载难逢的机会，我绝对不能放过"
        for size in ((960, 540), (1920, 1080)):
            with self.subTest(size=size):
                resized = cv2.resize(frame, size, interpolation=cv2.INTER_CUBIC)
                state, _, _, _, action = self.task._walk_state(resized, 0.2)
                self.assertIs(state, State.STORY_DIALOG)
                self.assertAlmostEqual(960, action.point[0], delta=4)
                self.assertAlmostEqual(997, action.point[1], delta=4)
        frame[489:508, 469:492] = 35
        action = self.task._walk_state(frame, 0.2)[-1]
        self.assertIsNone(action.point)
        self.assertFalse(action.follows_skip(None))

        frame = self.scene("dragon-heart-short-dialogue")
        actions = []
        for text, line in (("呃呃…！l", "呃呃"), ("等等！", "等等"), ("", "")):
            with self.subTest(text=text):
                self.text["推主线剧情字幕"] = text
                state, _, _, _, action = self.task._walk_state(frame, 0.2)
                self.assertIs(state, State.STORY_DIALOG)
                self.assertEqual(line, action.line)
                self.assertAlmostEqual(960, action.point[0], delta=4)
                self.assertAlmostEqual(997, action.point[1], delta=4)
                self.assertTrue(action.follows_skip(None))
                actions.append(action)
        self.assertTrue(actions[1].confirms(actions[0]))
        self.assertTrue(actions[2].confirms(actions[1]))
        self.text["推主线跳过对话标题"] = "请选择回答"
        state, _, _, _, action = self.task._walk_state(frame, 0.2)
        self.assertIs(state, State.UNKNOWN)
        self.assertIsNone(action)

        self.text.pop("推主线跳过对话标题")
        frame = self.scene("centered-black-dialogue")
        self.text["推主线黑底旁白"] = "找到了。"
        state, _, _, _, action = self.task._walk_state(frame, 0.2)
        self.assertIs(state, State.STORY_DIALOG)
        self.assertEqual("找到了", action.line)
        self.assertAlmostEqual(960, action.point[0], delta=4)
        self.assertAlmostEqual(998, action.point[1], delta=4)
        self.assertTrue(action.follows_skip(None))
        before_skip = self.task._walk_dialogue_before_skip(frame, 0.2)
        self.assertTrue(action.same_step(before_skip))
        without_arrow = frame.copy()
        without_arrow[489:508, 469:492] = 0
        state, _, _, _, action = self.task._walk_state(without_arrow, 0.2)
        self.assertIs(state, State.UNKNOWN)
        self.assertIsNone(action)

        bright = frame.copy()
        bright[:200, :] = 80
        action = self.task._walk_state(bright, 0.2)[-1]
        self.assertTrue(action.subtitle_arrow)
        self.assertEqual("", action.line)

        self.text["推主线黑底旁白"] = "100%"
        self.assertEqual("", self.task._walk_state(frame, 0.2)[-1].line)

    def test_world_f_keycap_is_distinct_from_tracker_and_hud(self):
        for folder, width, sizes, point in (
            ("main_story_helmet", 3837, [(3837, 2158), (2560, 1440), (1920, 1080)], (905, 368)),
            ("main_story_whip", 3838, [(3838, 2158)], (908, 366)),
        ):
            frame = np.zeros((2158, width, 3), np.uint8)
            for name, x, y in (
                ("world_f.png", 1780, 695),
                ("tracker_e.png", 3090, 290),
                ("hud_f.png", 2700, 1690),
            ):
                crop = read_fixture(folder, name)
                frame[y : y + crop.shape[0], x : x + crop.shape[1]] = crop
            self.task.info_set = Mock()
            self.task.log_warning = Mock()
            for size in sizes:
                with self.subTest(folder=folder, size=size):
                    target = self.task._walk_interact_target(cv2.resize(frame, size))
                    self.assertIsNotNone(target)
                    self.assertAlmostEqual(point[0], target[0][0], delta=5)
                    self.assertAlmostEqual(point[1], target[0][1], delta=5)
            with self.subTest(folder=folder):
                self.assertIsNotNone(self.task._walk_hud_interact_target(frame))
                frame[695:795, 1780:2190] = 0
                self.assertIsNone(self.task._walk_interact_target(frame))


class WalkTask(walk.MainStoryWalkFeatureMixin):
    def __init__(self, states, *, frames=None, **config):
        self.config = {
            "推主线时间上限分钟": 0.3,
            "推主线推进间隔秒数": 10,
            "推主线停滞秒数": 120,
            "推主线战斗失败重试次数": 1,
            **config,
        }
        self.clock = FakeClock(1000)
        self.sleep = self.clock.sleep
        self.states = list(states)
        self.index = 0
        self.capture_index = 0
        frame = np.zeros((1080, 1920, 3), np.uint8)
        self.frames = frames or (lambda _: frame)
        self.clicks = []
        self.infos = {}
        self.info_set = self.infos.__setitem__
        self.log_info = Mock()
        self.log_error = Mock()
        self.save_frame = Mock(return_value=Path("stuck.png"))
        self.write_probe_text = Mock()
        self.alert = Mock()
        self._main_story_ocr = Mock(return_value="")
        self.interact_score = -1
        self.consumed = False

    def capture_frame(self):
        frame = self.frames(self.capture_index)
        self.capture_index += 1
        return frame

    def _walk_state(self, frame, threshold):
        state, objective = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        # 目标文字就是「追踪条露出来了」的证据：空目标等于追踪条没显示。
        return state, f"脚本:{state.value}", objective, bool(objective), None

    def _click_reference(self, x, y, after_sleep=0):
        self.clicks.append((x, y))
        if self.consumed and (x, y) == PROMPT_CLICK:
            self.interact_score = -1
            self.states = [(State.BATTLE, "")]

    def _walk_match_spec(self, frame, spec):
        if spec is walk.INTERACT_HUD_KEYCAP or self.interact_score < 0:
            return walk.MatchResult(-1, (0, 0), (0, 0))
        return walk.MatchResult(self.interact_score, (1400, 700), (42, 46), pixel_score=0.9)

    def run(self):
        with (
            patch.object(walk, "monotonic", self.clock),
            patch.object(walk, "show_topmost_alert", self.alert),
        ):
            return self.run_main_story_walk()


class WalkLoopTest(unittest.TestCase):
    def test_castle_lighting_flicker_keeps_the_stationary_navigation_retry(self):
        frames = []
        for name in ("castle-1.png", "castle-2.png"):
            frame = np.zeros((540, 960, 3), np.uint8)
            frame[150:430, 100:850] = read_fixture("main_story_navigation", name)
            frames.append(frame)
        first, second = map(walk.screen_signature, frames)
        pan = walk.screen_signature(np.roll(frames[0], 4, axis=1))
        self.assertGreater(walk.screen_delta(first, second), 6)
        self.assertLess(walk.scene_motion_delta(first, second), 6)
        self.assertGreaterEqual(walk.scene_motion_delta(first, pan), 6)
        task = WalkTask(
            [(State.FIELD, "进入王城内部"), (State.FIELD, "日进人主城内部")] * 40,
            frames=lambda index: frames[index % 2],
        )
        self.assertTrue(task.run())
        self.assertEqual([walk.TRACKER_OBJECTIVE_POINT] * 2, task.clicks)

    def test_dialogue_needs_stable_frames_and_retries_only_without_progress(self):
        skip = walk.StoryAction(State.CUTSCENE, (1640, 60))
        old = walk.StoryAction(State.STORY_DIALOG, (1293, 431), "还是同一句对白")
        new = walk.StoryAction(State.STORY_DIALOG, (960, 997), "这个机会我绝对不能放过")
        no_arrow = walk.StoryAction(new.state, None, new.line)
        arrow = walk.StoryAction(new.state, new.point, subtitle_arrow=True)
        for sequence, before_skip, clicks, expected, reason in (
            (
                [skip, None, skip, skip, old, old, None] + [skip] * 30,
                old,
                [skip.point] * 2,
                False,
                "跳过剧情后剧情未推进",
            ),
            (
                [skip, skip, old, old, no_arrow, no_arrow, new, new],
                old,
                [skip.point, new.point],
                True,
                "",
            ),
            (
                [skip, skip] + [arrow] * 30,
                arrow,
                [skip.point, arrow.point, arrow.point],
                False,
                "推进对白后剧情未推进",
            ),
            ([old, old, new, new], None, [old.point, new.point], True, ""),
        ):
            with self.subTest(clicks=clicks):
                task = WalkTask([(State.UNKNOWN, "")])
                task._walk_dialogue_before_skip = Mock(return_value=before_skip)
                observations = [
                    (action.state, "剧情", "", False, action)
                    if action
                    else (State.UNKNOWN, "加载", "", False, None)
                    for action in sequence
                ]
                task._walk_state = Mock(
                    side_effect=observations + [(State.ALL_CLEAR, "", "", False, None)]
                )
                self.assertIs(task.run(), expected)
                self.assertEqual(clicks, task.clicks)
                self.assertEqual(int(skip in sequence), task._walk_dialogue_before_skip.call_count)
                if reason:
                    self.assertIn(reason, task.infos["推主线停止原因"])

    def test_field_interaction_requires_a_stable_target_and_observed_effect(self):
        target = walk.TRACKER_OBJECTIVE_POINT
        for scenario, expected, clicks, interactions in (
            ("consumed", True, [PROMPT_CLICK], 1),
            ("animated", True, [PROMPT_CLICK], 1),
            ("moving", True, [], 0),
            ("ineffective", False, [PROMPT_CLICK] * 3, 0),
            ("unknown", False, [PROMPT_CLICK] * 3, 0),
            ("disabled", True, [target] * 2, 0),
            ("objective progress", True, [target], 0),
        ):
            with self.subTest(scenario=scenario):
                state, objective = (
                    (State.UNKNOWN, "")
                    if scenario == "unknown"
                    else (State.FIELD, "寻找魔法工具0/2")
                )
                frames = None
                if scenario in {"animated", "moving"}:
                    pair = [np.full((1080, 1920, 3), v, np.uint8) for v in (0, 200)]

                    def frames(index):
                        return pair[index % 2]
                task = WalkTask(
                    [(state, objective)],
                    frames=frames,
                    **{
                        "推主线自动交互": scenario != "disabled",
                        "推主线等待秒数": 5,
                    },
                )
                task.interact_score = 0.98
                task.consumed = scenario in {"animated", "consumed"}
                if scenario == "moving":
                    task._walk_interact_target = lambda _: (
                        (task.capture_index * 10, 723),
                        "F",
                        0.95,
                    )
                if scenario == "objective progress":
                    start = task.clock()
                    task.config.update(
                        {
                            "推主线停滞秒数": 15,
                            "推主线时间上限分钟": 0.4,
                            "推主线推进间隔秒数": 1000,
                        }
                    )
                    task.interact_score = -1
                    task._walk_state = lambda *_: (
                        state,
                        "目标",
                        f"寻找魔法工具{int((task.clock() - start) // 10)}/2",
                        True,
                        None,
                    )
                self.assertIs(task.run(), expected)
                self.assertEqual(clicks, task.clicks)
                self.assertEqual(interactions, task.infos["推主线交互次数"])
                self.assertEqual(int(not expected), task.alert.call_count)

    def test_defeat_retries_once_then_alerts(self):
        task = WalkTask([(State.VICTORY, "")])
        task._main_story_ocr.return_value = "mission failed"
        task._walk_retry_point = Mock(return_value=(1500.0, 1000.0))
        task._walk_click_and_confirm = Mock(return_value=True)
        self.assertFalse(task.run())
        self.assertEqual(
            ["重试"], [c.args[-1] for c in task._walk_click_and_confirm.call_args_list]
        )
        task.alert.assert_called_once()
        self.assertIn("连续 2 场", task.alert.call_args.args[0])
