import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from src.tasks import main_story_walk
from src.tasks.main_story_walk import (
    INTERACT_PROMPT_OFFSET,
    TRACKER_E_POINT,
    TRACKER_OBJECTIVE_POINT,
    MainStoryWalkState,
    classify_main_story_walk_state,
    scene_motion_delta,
    screen_delta,
    screen_signature,
)
from src.utils.vision_models import MatchResult

# _FakeWalkTask 造的假帧是 1920x1080，所以帧坐标就是参考坐标：
# 键帽中心 (1421,723)，标签点在它右边 INTERACT_PROMPT_OFFSET 处。
PROMPT_CLICK = (1421 + INTERACT_PROMPT_OFFSET[0], 723)
# 右下角那个键帽中心 (1421,923)，圆形按钮在它上面 45 像素。
HUD_CLICK = (1421, 923 - 45)


def castle_entrance_frames():
    root = Path(__file__).parent / "fixtures" / "main_story_navigation"
    for name in ("castle-1.png", "castle-2.png"):
        crop = cv2.imdecode(np.frombuffer((root / name).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        frame = np.zeros((540, 960, 3), np.uint8)
        frame[150:430, 100:850] = crop
        yield frame


class ClassifyMainStoryWalkStateTest(unittest.TestCase):
    def test_distinguishes_actionable_overlays_from_the_background(self):
        state = MainStoryWalkState
        for regions, expected in (
            ({"skip_confirm_text": "跳过对话", "cutscene_text": "F2"}, state.SKIP_CONFIRM),
            ({"reward_text": "点击画面即可返回", "tracker_header_text": "主线"}, state.REWARD),
            ({"battle_text": "TURN.1 BATTLE", "victory_text": "TURN.1 BATTLE"}, state.BATTLE_TURN),
            ({"tracker_start_text": "开始困难难度", "tracker_header_text": "主线"}, state.LOBBY),
            ({"objective_text": "前往丢弃铁假面的地方"}, state.FIELD),
        ):
            with self.subTest(regions=regions):
                self.assertIs(classify_main_story_walk_state(**regions), expected)


class StoryControlVisionTest(unittest.TestCase):
    def setUp(self):
        self.task = main_story_walk.MainStoryWalkFeatureMixin()
        self.task.config = {}
        self.task._walk_templates = {}
        self.text = {
            "推主线跳过剧情": "f",
            "推主线中央对白": "怎么劝都留不住最后还是走到了这一步",
        }
        self.task._main_story_ocr = self.ocr

    def ocr(self, frame, bounds, name, threshold):
        if name == "推主线剧情字幕" and bounds != main_story_walk.STORY_SUBTITLE_ROIS[-1]:
            return ""
        return self.text.get(name, "")

    @staticmethod
    def frame(name):
        path = Path(__file__).parent / "fixtures" / "main_story_navigation" / f"{name}.webp"
        return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)

    def test_icon_and_keycap_recover_truncated_ocr_on_two_real_scenes(self):
        for name in ("secretary-dialogue", "hot-spring-dialogue"):
            with self.subTest(scene=name):
                state, evidence, _, _, action = self.task._walk_state(self.frame(name), .2)
                self.assertIs(state, MainStoryWalkState.CUTSCENE)
                self.assertIn("跳过图标+F2键帽", evidence)
                self.assertAlmostEqual(1640, action.point[0], delta=3)
                self.assertAlmostEqual(60, action.point[1], delta=3)

    def test_bubble_fallback_requires_arrow_and_readable_text(self):
        frame = self.frame("secretary-dialogue")
        frame[43:65, 799:847] = 40  # F2 键帽缺失，图标依然可见。
        state, _, _, _, action = self.task._walk_state(frame, .2)
        self.assertIs(state, MainStoryWalkState.STORY_DIALOG)
        self.assertAlmostEqual(1293, action.point[0], delta=3)
        self.assertAlmostEqual(431, action.point[1], delta=3)

        original = self.text.pop("推主线中央对白")
        self.assertIsNone(self.task._walk_state(frame, .2)[-1])
        self.text["推主线中央对白"] = original
        frame[204:225, 635:666] = (214, 225, 234)
        self.assertIsNone(self.task._walk_state(frame, .2)[-1])

    def test_confirmation_and_choice_take_priority_over_visible_skip_control(self):
        frame = self.frame("secretary-dialogue")
        self.text["推主线跳过对话标题"] = "跳过对话"
        state, _, _, _, action = self.task._walk_state(frame, .2)
        self.assertIs(state, MainStoryWalkState.SKIP_CONFIRM)
        self.assertIsNone(action)

        self.text["推主线跳过对话标题"] = "请选择回答"
        state, _, _, _, action = self.task._walk_state(frame, .2)
        self.assertIs(state, MainStoryWalkState.UNKNOWN)
        self.assertIsNone(action)

    def test_real_field_cannot_match_either_story_control(self):
        frame = self.frame("snowy-field")
        self.assertIsNone(self.task._walk_story_skip_point(frame))
        self.assertIsNone(self.task._walk_story_bubble_action(frame, .2))
        self.assertIsNone(self.task._walk_state(frame, .2)[-1])
        self.text["推主线追踪条标题"] = "主线"
        state, _, _, _, action = self.task._walk_state(frame, .2)
        self.assertIs(state, MainStoryWalkState.FIELD)
        self.assertIsNone(action)

    def test_full_screen_dialogue_finds_continue_arrow_below_subtitles(self):
        frame = self.frame("bottom-dialogue")
        self.text["推主线剧情字幕"] = "抱歉，但这千载难逢的机会，我绝对不能放过"
        for width, height in ((960, 540), (1920, 1080)):
            with self.subTest(size=(width, height)):
                resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_CUBIC)
                state, _, _, _, action = self.task._walk_state(resized, .2)
                self.assertIs(state, MainStoryWalkState.STORY_DIALOG)
                self.assertAlmostEqual(960, action.point[0], delta=4)
                self.assertAlmostEqual(997, action.point[1], delta=4)

        frame[489:508, 469:492] = 35
        action = self.task._walk_state(frame, .2)[-1]
        self.assertIsNone(action.point)
        self.assertFalse(action.follows_skip(None))

    def test_short_centered_line_with_continue_arrow_is_dialogue(self):
        frame = self.frame("centered-black-dialogue")
        self.text["推主线黑底旁白"] = "找到了。"
        state, _, _, _, action = self.task._walk_state(frame, .2)
        self.assertIs(state, MainStoryWalkState.STORY_DIALOG)
        self.assertEqual("找到了", action.line)
        self.assertAlmostEqual(960, action.point[0], delta=4)
        self.assertAlmostEqual(998, action.point[1], delta=4)
        self.assertTrue(action.follows_skip(None))
        before_skip = self.task._walk_dialogue_before_skip(frame, .2)
        self.assertTrue(action.same_step(before_skip))

    def test_centered_text_tracking_uses_dark_backdrop_and_continue_arrow(self):
        frame = self.frame("centered-black-dialogue")
        self.text["推主线黑底旁白"] = "找到了。"
        without_arrow = frame.copy()
        without_arrow[489:508, 469:492] = 0
        state, _, _, _, action = self.task._walk_state(without_arrow, .2)
        self.assertIs(state, MainStoryWalkState.UNKNOWN)
        self.assertIsNone(action)

        bright = frame.copy()
        bright[:200, :] = 80
        action = self.task._walk_state(bright, .2)[-1]
        self.assertTrue(action.subtitle_arrow)
        self.assertEqual("", action.line)

        self.text["推主线黑底旁白"] = "100%"
        self.assertEqual("", self.task._walk_state(frame, .2)[-1].line)

    def test_full_screen_arrow_advances_short_or_unreadable_dialogue(self):
        frame = self.frame("dragon-heart-short-dialogue")
        previous = None
        for text, expected_line in (("呃呃…！l", "呃呃"), ("等等！", "等等"), ("", "")):
            with self.subTest(text=text):
                self.text["推主线剧情字幕"] = text
                state, _, _, _, action = self.task._walk_state(frame, .2)
                self.assertIs(state, MainStoryWalkState.STORY_DIALOG)
                self.assertEqual(expected_line, action.line)
                self.assertAlmostEqual(960, action.point[0], delta=4)
                self.assertAlmostEqual(997, action.point[1], delta=4)
                self.assertTrue(action.follows_skip(None))
                if previous is not None:
                    self.assertTrue(action.confirms(previous))
                previous = action

        self.text["推主线跳过对话标题"] = "请选择回答"
        state, _, _, _, action = self.task._walk_state(frame, .2)
        self.assertIs(state, MainStoryWalkState.UNKNOWN)
        self.assertIsNone(action)

    def test_unreadable_old_bubble_requires_layout_change(self):
        self.text.pop("推主线中央对白")
        old = self.task._walk_dialogue_before_skip(self.frame("secretary-dialogue"), .2)
        self.assertEqual("", old.line)

        same_layout = main_story_walk.StoryAction(
            MainStoryWalkState.STORY_DIALOG, old.point, "旧对白",
        )
        self.assertFalse(same_layout.follows_skip(old))

        self.text["推主线剧情字幕"] = "抱歉，但这千载难逢的机会，我绝对不能放过"
        new = self.task._walk_state(self.frame("bottom-dialogue"), .2)[-1]
        self.assertTrue(new.follows_skip(old))


class ScreenDeltaTest(unittest.TestCase):

    @staticmethod
    def _frame(value=0):
        return np.full((1080, 1920, 3), value, dtype=np.uint8)

    def test_stationary_castle_lighting_is_distinct_from_camera_pan(self):
        first, second = castle_entrance_frames()
        baseline = screen_signature(first)
        stationary = screen_signature(second)
        panned = screen_signature(np.roll(first, 4, axis=1))
        self.assertGreater(screen_delta(baseline, stationary), 6.0)
        self.assertLess(scene_motion_delta(baseline, stationary), 6.0)
        self.assertGreaterEqual(scene_motion_delta(baseline, panned), 6.0)


class _FakeClock:
    """可注入的时钟：sleep 推进时间，测试不用真的等。"""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeWalkTask(main_story_walk.MainStoryWalkFeatureMixin):
    """只实现主循环用到的接口，界面状态由测试脚本逐轮给出。"""

    def __init__(self, states, config=None, frames=None):
        self.config = {
            "推主线推进方式": "点击追踪条",
            "推主线自动交互": True,
            "推主线停滞秒数": 120.0,
            "推主线推进间隔秒数": 10.0,
            "推主线静止阈值": 6.0,
            "推主线等待秒数": 20.0,
            "推主线战斗等待秒数": 300.0,
            "推主线战斗失败重试次数": 1,
            "推主线时间上限分钟": 0.3,
            "推主线 OCR 阈值": 0.2,
            "主线置顶弹框": True,
            "推主线调试记录": False,
        }
        self.config.update(config or {})
        self.clock = _FakeClock()
        self.states = list(states)
        self.frames = frames
        self.index = 0
        self.capture_index = 0
        self.clicks = []
        self.keys = []
        self.confirmed = []
        self.retry_points = 0
        self.retry_point = None
        self.confirm_result = True
        self.title_text = ""
        # 交互键帽匹配分：默认 -1 表示画面里没有「F」提示。
        self.interact_score = -1.0
        # 右下角那个「F」的匹配分：默认 -1 表示按钮没画出来。
        self.hud_score = -1.0
        # 点提示条之后游戏给的反应：True＝提示被吃掉（交互生效）。
        self.interact_works = False
        # 交互生效后脚本切到的界面（None＝还是野外，等提示自然消失）。
        self.interact_result_state = None
        self.infos = {}
        self.errors = []
        self.logs = []
        self.saved = []
        self.probes = []
        self.alerts = []

    def capture_frame(self):
        if self.frames is None:
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        else:
            frame = self.frames(self.capture_index)
        self.capture_index += 1
        return frame

    def _walk_state(self, frame, threshold):
        state, objective = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        # 目标文字就是「追踪条露出来了」的证据：空目标等于追踪条没显示。
        return state, f"脚本:{state.value}", objective, bool(objective), None

    def _main_story_ocr(self, frame, bounds, name, threshold):
        return self.title_text if name == "推主线结算标题" else ""

    def _walk_retry_point(self, frame, threshold):
        self.retry_points += 1
        return self.retry_point

    def _walk_click_and_confirm(self, previous, point, threshold, action):
        self.confirmed.append(action)
        return self.confirm_result

    def _click_reference(self, x, y, after_sleep=0.0):
        self.clicks.append((x, y))
        if self.interact_works and self.interact_score > 0:
            # 点到交互入口后游戏给的反应：提示条被吃掉，后面再也匹配不到。
            self.interact_score = -1.0
            if self.interact_result_state is not None:
                self.states[self.index:] = [(self.interact_result_state, "")]

    def send_key(self, key, *args, **kwargs):
        self.keys.append(key)
        return True

    def _walk_match_spec(self, frame, spec):
        if spec is main_story_walk.INTERACT_HUD_KEYCAP:
            score, position = self.hud_score, (1400, 900)
        else:
            score, position = self.interact_score, (1400, 700)
        if score <= 0:
            return MatchResult(-1.0, (0, 0), (0, 0))
        return MatchResult(score, position, (42, 46), pixel_score=0.9)

    def sleep(self, seconds):
        self.clock.advance(seconds)
        return True

    def info_set(self, key, value):
        self.infos[key] = value

    def log_info(self, message, *args, **kwargs):
        self.logs.append(message)

    def log_error(self, message, *args, notify=False, **kwargs):
        self.errors.append((message, notify))

    def save_frame(self, name, frame):
        self.saved.append(name)
        return Path("probe_outputs") / f"{name}.png"

    def write_probe_text(self, name, lines, info_label="OCR 文本文件"):
        self.probes.append((name, lines))
        return Path(name)


class WalkLoopTest(unittest.TestCase):
    """主循环的行为：什么时候推、什么时候停、卡住怎么报。"""

    def _run(self, task):
        with mock.patch.object(main_story_walk, "monotonic", task.clock), \
                mock.patch.object(
                    main_story_walk, "show_topmost_alert",
                    lambda message, *a, **kw: task.alerts.append(message),
                ):
            return task.run_main_story_walk()

    def test_same_story_requires_two_frames_and_allows_one_retry(self):
        task = _FakeWalkTask([(MainStoryWalkState.UNKNOWN, "")])
        action = main_story_walk.StoryAction(MainStoryWalkState.CUTSCENE, (1640, 60))
        skip = (action.state, "跳过", "", False, action)
        unknown = (MainStoryWalkState.UNKNOWN, "加载", "", False, None)
        line = main_story_walk.StoryAction(
            MainStoryWalkState.STORY_DIALOG, (1293, 431), "还是同一句对白",
        )
        task._walk_dialogue_before_skip = mock.Mock(return_value=line)
        bubble = (line.state, "对白", "", False, line)
        frames = iter([skip, unknown, skip, skip, bubble, bubble, unknown] + [skip] * 30)

        def read_story(*_args):
            if task.capture_index <= 4:
                self.assertEqual([], task.clicks)
            return next(frames)

        task._walk_state = read_story
        self.assertFalse(self._run(task))
        self.assertEqual([action.point, action.point], task.clicks)
        self.assertIn("跳过剧情后剧情未推进", task.infos["推主线停止原因"])
        task._walk_dialogue_before_skip.assert_called_once()

    def test_skip_hands_off_to_confirmed_new_dialogue_with_continue_arrow(self):
        task = _FakeWalkTask([(MainStoryWalkState.UNKNOWN, "")])
        skip = main_story_walk.StoryAction(MainStoryWalkState.CUTSCENE, (1640, 60))
        old = main_story_walk.StoryAction(
            MainStoryWalkState.STORY_DIALOG, (1293, 431), "我们就到此为止了吧",
        )
        new = main_story_walk.StoryAction(
            MainStoryWalkState.STORY_DIALOG, (960, 997), "抱歉但这个机会我绝对不能放过",
        )
        new_without_arrow = main_story_walk.StoryAction(new.state, None, new.line)
        task._walk_dialogue_before_skip = mock.Mock(return_value=old)
        actions = (skip, skip, old, old, new_without_arrow, new_without_arrow, new, new)
        frames = iter([(action.state, "剧情", "", False, action) for action in actions]
                      + [(MainStoryWalkState.ALL_CLEAR, "完成", "", False, None)])
        task._walk_state = lambda *_args: next(frames)

        self.assertTrue(self._run(task))
        self.assertEqual([skip.point, new.point], task.clicks)

    def test_skip_hands_off_to_an_arrow_without_ocr_and_retries_are_bounded(self):
        task = _FakeWalkTask([(MainStoryWalkState.UNKNOWN, "")])
        skip = main_story_walk.StoryAction(MainStoryWalkState.CUTSCENE, (1640, 60))
        arrow = main_story_walk.StoryAction(
            MainStoryWalkState.STORY_DIALOG, (960, 997), subtitle_arrow=True,
        )
        task._walk_dialogue_before_skip = mock.Mock(return_value=arrow)
        actions = iter([skip, skip] + [arrow] * 30)

        def read_story(*_args):
            action = next(actions)
            return action.state, "剧情", "", False, action

        task._walk_state = read_story
        self.assertFalse(self._run(task))
        self.assertEqual([skip.point, arrow.point, arrow.point], task.clicks)
        self.assertIn("推进对白后剧情未推进", task.infos["推主线停止原因"])

    def test_dialogue_change_is_verified_even_when_screen_state_is_unchanged(self):
        task = _FakeWalkTask([(MainStoryWalkState.UNKNOWN, "")])
        state = MainStoryWalkState.STORY_DIALOG
        first = main_story_walk.StoryAction(state, (1290, 430), "最后还是走到了这一步")
        second = main_story_walk.StoryAction(state, (1260, 395), "我会告诉你发生了什么")
        frames = iter([(state, "对白", "", False, line)
                       for line in (first, first, second, second)]
                      + [(MainStoryWalkState.ALL_CLEAR, "完成", "", False, None)])
        task._walk_state = lambda *_args: next(frames)

        self.assertTrue(self._run(task))
        self.assertEqual([first.point, second.point], task.clicks)

    def test_castle_navigation_retries_while_lighting_and_ocr_flicker(self):
        frames = list(castle_entrance_frames())
        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "进入王城内部"),
             (MainStoryWalkState.FIELD, "日进人主城内部")] * 40,
            frames=lambda index: frames[index % len(frames)],
        )
        self.assertTrue(self._run(task))
        self.assertEqual(task.clicks, [TRACKER_OBJECTIVE_POINT, TRACKER_OBJECTIVE_POINT])

    def test_defeat_retries_once_then_alerts(self):
        task = _FakeWalkTask([(MainStoryWalkState.VICTORY, "")])
        task.title_text = "mission failed"
        task.retry_point = (1500.0, 1000.0)
        task.confirm_result = True
        self.assertFalse(self._run(task))
        self.assertEqual(task.confirmed, ["重试"])
        self.assertEqual(len(task.alerts), 1)
        self.assertIn("连续 2 场", task.alerts[0])

    def test_moving_prompt_does_not_interact(self):
        """镜头移动时提示位置也在变，不打断寻路。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
            frames=lambda index: np.full(
                (1080, 1920, 3), 200 if index % 2 else 0, dtype=np.uint8,
            ),
        )
        task.interact_score = 0.95
        task.interact_works = True
        task._walk_interact_target = lambda _frame: (
            (task.capture_index * 10, 723), "F", .95,
        )
        self.assertTrue(self._run(task))
        self.assertEqual(task.clicks, [])

    def test_hud_button_needs_its_own_keycap(self):
        """右下角那个键没画出来就不点——宁可不动，也不能点到技能上去。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
            config={"推主线时间上限分钟": 0.2},
        )
        task.interact_score = 0.95
        self.assertFalse(self._run(task))
        self.assertNotIn(HUD_CLICK, task.clicks)

    def test_interact_without_effect_does_not_restart_navigation(self):
        """提示还在时保留交互优先级，不能又点追踪条打断。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
        )
        task.interact_score = 0.95
        self.assertFalse(self._run(task))
        self.assertEqual(3, task.clicks.count(PROMPT_CLICK))
        self.assertIn(PROMPT_CLICK, task.clicks)
        self.assertNotIn(TRACKER_OBJECTIVE_POINT, task.clicks)
        self.assertEqual(task.infos.get("推主线交互次数"), 0)

    def test_missing_tracker_does_not_click_empty_ground(self):
        """追踪条没显示时目标行那个点是空地面：点下去会打断寻路，一律不点。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.UNKNOWN, "")],
            config={"推主线等待秒数": 5.0, "推主线时间上限分钟": 0.3},
        )
        task.interact_score = 0.95
        self.assertFalse(self._run(task))
        self.assertNotIn(TRACKER_OBJECTIVE_POINT, task.clicks)
        self.assertNotIn(TRACKER_E_POINT, task.clicks)

    def test_interact_disabled_does_not_click_prompt(self):
        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
            config={"推主线自动交互": False},
        )
        task.interact_score = 0.95
        task.interact_works = True
        self.assertTrue(self._run(task))
        self.assertNotIn(PROMPT_CLICK, task.clicks)


if __name__ == "__main__":
    unittest.main()
