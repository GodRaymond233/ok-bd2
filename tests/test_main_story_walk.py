import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from src.tasks import main_story_walk
from src.tasks.main_story_walk import (
    INTERACT_KEYCAP_TEMPLATES,
    INTERACT_PROMPT_OFFSET,
    INTERACT_PROMPT_ROI,
    TRACKER_E_POINT,
    TRACKER_OBJECTIVE_POINT,
    TRACKER_OBJECTIVE_ROI,
    MainStoryWalkState,
    _looks_like_retry_button,
    _looks_like_tracker_header,
    classify_main_story_walk_state,
    objective_changed,
    objective_signature,
    scene_motion_delta,
    screen_delta,
    screen_signature,
    stuck_due,
)
from src.tasks.task_vision_mixin import (
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    TEMPLATE_DIR,
)
from src.utils import task_vision, template_resolution
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

    def test_skip_confirm_beats_cutscene(self):
        """点 F2 后「F2」提示还压在二次确认框背后，必须先认确认框。"""

        self.assertIs(
            classify_main_story_walk_state(
                skip_confirm_text="跳过对话", cutscene_text="F2",
            ),
            MainStoryWalkState.SKIP_CONFIRM,
        )

    def test_cutscene_on_skip_hint(self):
        self.assertIs(
            classify_main_story_walk_state(cutscene_text="F2"),
            MainStoryWalkState.CUTSCENE,
        )

    def test_reward_popup_beats_field(self):
        """奖励弹窗盖在野外画面上时追踪条还看得见，不能被当成野外继续点。"""

        self.assertIs(
            classify_main_story_walk_state(
                reward_text="点击画面即可返回", tracker_header_text="主线",
            ),
            MainStoryWalkState.REWARD,
        )

    def test_victory_on_leave_button(self):
        self.assertIs(
            classify_main_story_walk_state(victory_text="离开"),
            MainStoryWalkState.VICTORY,
        )

    def test_battle_turn_beats_battle_hud(self):
        """「TURN.1 BATTLE」要先去点开回合，而不是当成自动战斗中干等。"""

        self.assertIs(
            classify_main_story_walk_state(
                battle_text="TURN.1 BATTLE", victory_text="TURN.1 BATTLE",
            ),
            MainStoryWalkState.BATTLE_TURN,
        )

    def test_battle_hud(self):
        self.assertIs(
            classify_main_story_walk_state(battle_text="结束自动战斗esc"),
            MainStoryWalkState.BATTLE,
        )

    def test_lobby_start_difficulty_is_not_field(self):
        """大厅追踪条也读得到「主线」标题，但目标是「开始困难难度」＝起点不对。"""

        self.assertIs(
            classify_main_story_walk_state(
                tracker_start_text="开始困难难度", tracker_header_text="主线",
            ),
            MainStoryWalkState.LOBBY,
        )

    def test_all_clear(self):
        self.assertIs(
            classify_main_story_walk_state(all_clear_text="完成主线任务"),
            MainStoryWalkState.ALL_CLEAR,
        )

    def test_field_on_tracker_header(self):
        self.assertIs(
            classify_main_story_walk_state(tracker_header_text="主线"),
            MainStoryWalkState.FIELD,
        )

    def test_field_on_objective_text_only(self):
        """标题漏读时，目标行本身（≥2 字）也能证明在野外场景。"""

        self.assertIs(
            classify_main_story_walk_state(objective_text="前往丢弃铁假面的地方"),
            MainStoryWalkState.FIELD,
        )

    def test_story_dialog_when_tracker_absent(self):
        self.assertIs(
            classify_main_story_walk_state(story_text="这是很长的一句对白"),
            MainStoryWalkState.STORY_DIALOG,
        )

    def test_unknown_on_empty_screen(self):
        self.assertIs(
            classify_main_story_walk_state(),
            MainStoryWalkState.UNKNOWN,
        )


class ObjectiveSignatureTest(unittest.TestCase):

    def test_strips_e_key_hint(self):
        """目标行最左边的 E 是热键提示，不是目标内容。"""

        self.assertEqual(
            objective_signature("E 前往丢弃铁假面的地方"),
            objective_signature("前往丢弃铁假面的地方"),
        )

    def test_keeps_pure_objective(self):
        self.assertEqual(
            objective_signature("前往丢弃铁假面的地方"),
            "前往丢弃铁假面的地方",
        )

    def test_empty_text(self):
        self.assertEqual(objective_signature("   "), "")


class ObjectiveChangedTest(unittest.TestCase):

    def test_same_objective_unchanged(self):
        self.assertFalse(
            objective_changed("前往丢弃铁假面的地方", "前往丢弃铁假面的地方"),
        )

    def test_one_char_ocr_error_tolerated(self):
        """OCR 形近误读（地万/地方）不该被当成推进。"""

        self.assertFalse(
            objective_changed("前往丢弃铁假面的地方", "前往丢弃铁假面的地万"),
        )

    def test_new_objective_changed(self):
        self.assertTrue(
            objective_changed("前往丢弃铁假面的地方", "和铁假面交谈"),
        )

    def test_empty_value_is_not_progress(self):
        self.assertFalse(objective_changed("", "前往丢弃铁假面的地方"))
        self.assertFalse(objective_changed("前往丢弃铁假面的地方", ""))


class ScreenDeltaTest(unittest.TestCase):

    @staticmethod
    def _frame(value=0):
        return np.full((1080, 1920, 3), value, dtype=np.uint8)

    def test_identical_frames_have_no_delta(self):
        signature = screen_signature(self._frame(30))
        self.assertEqual(screen_delta(signature, screen_signature(self._frame(30))), 0.0)

    def test_changed_scene_moves_delta(self):
        """寻路时镜头在走，中间场景区会明显变化。"""

        first = self._frame(0)
        second = self._frame(0)
        second[400:800, 400:1400] = 255
        self.assertGreater(
            screen_delta(screen_signature(first), screen_signature(second)), 10.0,
        )

    def test_missing_or_mismatched_frames_are_zero(self):
        self.assertEqual(screen_delta(None, None), 0.0)
        self.assertEqual(
            screen_delta(screen_signature(self._frame(0)), np.zeros((4, 4))), 0.0,
        )
        self.assertIsNone(screen_signature(None))

    def test_stationary_castle_lighting_is_distinct_from_camera_pan(self):
        first, second = castle_entrance_frames()
        baseline = screen_signature(first)
        stationary = screen_signature(second)
        panned = screen_signature(np.roll(first, 4, axis=1))
        self.assertGreater(screen_delta(baseline, stationary), 6.0)
        self.assertLess(scene_motion_delta(baseline, stationary), 6.0)
        self.assertGreaterEqual(scene_motion_delta(baseline, panned), 6.0)


class RetryButtonTest(unittest.TestCase):

    def test_retry_words(self):
        for text in ("重试", "重新挑战", "再次挑战", "retry"):
            with self.subTest(text=text):
                self.assertTrue(_looks_like_retry_button(text))

    def test_leave_is_not_retry(self):
        self.assertFalse(_looks_like_retry_button("离开"))


class TrackerGeometryTest(unittest.TestCase):

    def test_objective_click_point_inside_roi(self):
        """点击点必须在实测目标行 ROI 内，且 ROI 右边界不碰「+」按钮。"""

        left, top, right, bottom = TRACKER_OBJECTIVE_ROI
        x, y = TRACKER_OBJECTIVE_POINT
        self.assertLessEqual(left * REFERENCE_WIDTH, x)
        self.assertGreaterEqual(right * REFERENCE_WIDTH, x)
        self.assertLessEqual(top * REFERENCE_HEIGHT, y)
        self.assertGreaterEqual(bottom * REFERENCE_HEIGHT, y)
        self.assertLessEqual(right * REFERENCE_WIDTH, 1800)

    def test_header_keyword(self):
        self.assertTrue(_looks_like_tracker_header("主线"))
        self.assertFalse(_looks_like_tracker_header("开始困难难度"))


class InteractTemplateTest(unittest.TestCase):

    def test_keycap_template_file_exists(self):
        """模板文件是实机截下来的，缺了匹配器只会一直匹配不到。"""

        repo = Path(main_story_walk.__file__).resolve().parents[2]
        for spec in INTERACT_KEYCAP_TEMPLATES:
            with self.subTest(spec=spec.name):
                path = repo / "recognition-assets" / "template-assets" / spec.file_name
                self.assertTrue(path.is_file(), str(path))

    def test_keycap_scale_matches_client_resolution(self):
        """模板按客户端分辨率换算后必须和实机键帽一样大（实测 35x39 像素）。

        缩放算错时匹配器一直匹配不到，任务就停在原地反复点追踪条——2026-11-18
        实机就是这个症状，原因是模板取了 4K 原尺寸却按 1440p 标了比例。
        """

        spec = INTERACT_KEYCAP_TEMPLATES[0]
        template, _mask = task_vision.load_template(TEMPLATE_DIR, spec)
        scale = template_resolution.offline_template_scale(
            spec.file_name, 3840, 2160, reference_scale=spec.reference_scale,
        )
        width = round(template.shape[1] * scale)
        self.assertGreaterEqual(width, 26, f"键帽内部字形缩到 {width} 像素")
        self.assertLessEqual(width, 36, f"键帽内部字形放大到 {width} 像素")

    def test_prompt_roi_inside_frame(self):
        """搜索区要在画面内，且不覆盖顶部货币栏与底部技能区。"""

        left, top, right, bottom = INTERACT_PROMPT_ROI
        self.assertLess(left, right)
        self.assertLess(top, bottom)
        self.assertGreaterEqual(left, 0.0)
        self.assertLessEqual(right, 1.0)
        self.assertGreaterEqual(top, 0.0)
        self.assertLessEqual(bottom, 1.0)
        self.assertGreaterEqual(top * REFERENCE_HEIGHT, 120)
        self.assertLessEqual(bottom * REFERENCE_HEIGHT, 900)

    def test_prompt_roi_excludes_tracker_key_hint(self):
        """追踪条目标行左边的「E」和 F 是同一套皮肤（实测 0.90 分），搜索区不能盖住它。"""

        _left, _top, right, _bottom = INTERACT_PROMPT_ROI
        self.assertLess(right * REFERENCE_WIDTH, 1550)

    def test_keycap_threshold_beats_tracker_false_positive(self):
        """真提示实测 1.00、追踪条那个 E 只有 0.90，阈值必须卡在中间。"""

        for spec in INTERACT_KEYCAP_TEMPLATES:
            with self.subTest(spec=spec.name):
                self.assertGreater(spec.threshold, 0.9)
                self.assertLess(spec.threshold, 1.0)


class StuckDueTest(unittest.TestCase):

    def test_below_timeout(self):
        self.assertFalse(stuck_due(100.0, 160.0, 240.0))

    def test_at_timeout(self):
        self.assertTrue(stuck_due(100.0, 340.0, 240.0))

    def test_above_timeout(self):
        self.assertTrue(stuck_due(100.0, 400.0, 240.0))

    def test_zero_timeout_disables(self):
        self.assertFalse(stuck_due(0.0, 10_000.0, 0.0))


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
        return state, f"脚本:{state.value}", objective, bool(objective)

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

    def test_presses_tracker_when_screen_is_still(self):
        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
        )
        self.assertTrue(self._run(task))
        self.assertGreaterEqual(len(task.clicks), 2)
        self.assertEqual(task.clicks[0], TRACKER_OBJECTIVE_POINT)
        self.assertEqual(task.alerts, [])

    def test_moving_screen_does_not_press(self):
        """自动寻路时画面在动，这时候不点，免得把寻路打断。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
            frames=lambda index: np.full(
                (1080, 1920, 3), 200 if index % 2 else 0, dtype=np.uint8,
            ),
        )
        self.assertTrue(self._run(task))
        self.assertEqual(task.clicks, [])

    def test_castle_navigation_retries_while_lighting_and_ocr_flicker(self):
        frames = list(castle_entrance_frames())
        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "进入王城内部"),
             (MainStoryWalkState.FIELD, "日进人主城内部")] * 40,
            frames=lambda index: frames[index % len(frames)],
        )
        self.assertTrue(self._run(task))
        self.assertEqual(task.clicks, [TRACKER_OBJECTIVE_POINT, TRACKER_OBJECTIVE_POINT])

    def test_stuck_alerts_and_stops(self):
        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
            config={"推主线停滞秒数": 60.0, "推主线时间上限分钟": 5.0},
        )
        self.assertFalse(self._run(task))
        self.assertEqual(len(task.alerts), 1)
        self.assertIn("没有推进", task.alerts[0])
        message, notify = task.errors[-1]
        self.assertIn("卡住", message)
        self.assertTrue(notify, "卡住必须同时发系统通知")
        self.assertIn("main_story_walk_stuck", task.saved)
        self.assertIn("推主线停止原因", task.infos)

    def test_defeat_without_retry_button_alerts(self):
        """失败页找不到「重试」就报错，不盲点。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.BATTLE, ""), (MainStoryWalkState.VICTORY, "")],
        )
        task.title_text = "mission failed"
        self.assertFalse(self._run(task))
        self.assertEqual(len(task.alerts), 1)
        self.assertIn("重试", task.alerts[0])

    def test_defeat_retries_once_then_alerts(self):
        task = _FakeWalkTask([(MainStoryWalkState.VICTORY, "")])
        task.title_text = "mission failed"
        task.retry_point = (1500.0, 1000.0)
        task.confirm_result = True
        self.assertFalse(self._run(task))
        self.assertEqual(task.confirmed, ["重试"])
        self.assertEqual(len(task.alerts), 1)
        self.assertIn("连续 2 场", task.alerts[0])

    def test_clicks_prompt_when_interact_keycap_visible(self):
        """画面上有「F」键帽就点提示条，而不是去点追踪条。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
        )
        task.interact_score = 0.95
        task.interact_works = True
        task.interact_result_state = MainStoryWalkState.BATTLE
        self.assertTrue(self._run(task))
        self.assertEqual(task.clicks, [PROMPT_CLICK])
        self.assertEqual(task.infos.get("推主线交互次数"), 1)
        self.assertEqual(task.alerts, [])
        self.assertEqual(task.keys, [], "这个客户端不吃注入按键，不该再按 F")

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

    def test_interact_falls_back_to_hud_button(self):
        """点提示条没反应时，才去点右下角那个圆形交互按钮（而且要先看见它）。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
            config={"推主线时间上限分钟": 0.2},
        )
        task.interact_score = 0.95
        task.hud_score = 0.95
        self.assertTrue(self._run(task))
        self.assertIn(PROMPT_CLICK, task.clicks)
        self.assertIn(HUD_CLICK, task.clicks)
        self.assertLess(
            task.clicks.index(PROMPT_CLICK), task.clicks.index(HUD_CLICK),
        )
        self.assertEqual(task.infos.get("推主线交互次数"), 0)

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

    def test_e_advance_mode_clicks_the_e_keycap(self):
        """「按 E 键」也实现成点那个 E 键帽：注入按键这个客户端收不到。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.FIELD, "前往丢弃铁假面的地方")],
            config={"推主线推进方式": "按 E 键"},
        )
        self.assertTrue(self._run(task))
        self.assertEqual(task.clicks[0], TRACKER_E_POINT)
        self.assertEqual(task.keys, [])

    def test_keycap_rescues_missing_tracker(self):
        """有的野外场景不显示追踪条；画面上有 F 键帽就说明人在野外，别当认不出报错。"""

        task = _FakeWalkTask(
            [(MainStoryWalkState.UNKNOWN, "")],
            config={"推主线等待秒数": 5.0, "推主线时间上限分钟": 5.0},
        )
        task.interact_score = 0.95
        task.interact_works = True
        task.interact_result_state = MainStoryWalkState.BATTLE
        self.assertTrue(self._run(task))
        self.assertIn(PROMPT_CLICK, task.clicks)
        self.assertEqual(task.alerts, [])

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

    def test_lobby_start_alerts(self):
        task = _FakeWalkTask(
            [(MainStoryWalkState.LOBBY, "")],
            config={"推主线等待秒数": 5.0, "推主线时间上限分钟": 5.0},
        )
        self.assertFalse(self._run(task))
        self.assertEqual(len(task.alerts), 1)
        self.assertIn("野外", task.alerts[0])


if __name__ == "__main__":
    unittest.main()
