import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.glutalk_vision import (
    AVAILABLE_TASK_DEFAULT_KEYWORDS,
    available_task_point,
    classify_talk_page,
    close_button_point,
    dialogue_decision,
    home_talk_entry,
    is_exhausted,
    remaining_encounters,
    reply_button_point,
    unread_badge_point,
    unread_count,
)
from src.tasks.GlutalkTask import GlutalkTask
from src.tasks.MailRewardTask import MAIL_ICON, MailRewardTask
from src.tasks.mission_rewards import (
    DISABLED_BUTTON_FILE,
    DISABLED_BUTTON_ROI,
    SECTIONS,
    RewardView,
    mission_reward_view,
    season_pass_view,
)
from src.tasks.MissionRewardTask import MissionRewardTask
from src.utils.task_vision import match_template
from tests.helpers import FakeClock

MODULE = importlib.import_module("src.tasks.MissionRewardTask")
FIXTURES = Path(__file__).parent / "fixtures/mission_rewards"


def fixture(name):
    frame = cv2.imread(str(FIXTURES / f"{name}.png"))
    records = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    boxes = []
    for item in records:
        xs, ys = zip(*item["polygon"])
        boxes.append(
            SimpleNamespace(
                name=item["text"],
                confidence=item["confidence"],
                x=min(xs),
                y=min(ys),
                width=max(xs) - min(xs),
                height=max(ys) - min(ys),
            )
        )
    return frame, boxes


PASS_WIDTH, PASS_HEIGHT = 1280, 720
GUIDE = "指南通行证Ⅱ"
SPECIAL = "孤注一掷特别赛季通行证"
REGULAR = "Blood Royale赛季通行证"
CHARACTER = "沙漠之花席比雅角色通行证"
PASS_LABELS = (
    ("爱通行证", 0.16, 0.048),
    (GUIDE, 0.20, 0.324),
    (SPECIAL, 0.219, 0.422),
    (REGULAR, 0.219, 0.521),
    (CHARACTER, 0.219, 0.620),
    ("LEVEL", 0.503, 0.294),
    ("基础", 0.593, 0.294),
    ("购买 ￥3,390", 0.728, 0.372),
    ("购买等级500", 0.38, 0.706),
    ("全部获得", 0.749, 0.707),
)


def boxes(labels):
    return [
        SimpleNamespace(
            name=text, x=x * PASS_WIDTH - 15, y=y * PASS_HEIGHT - 7, width=30, height=14
        )
        for text, x, y in labels
    ]


def pass_frame(selected_top, brightness=255):
    frame = np.zeros((PASS_HEIGHT, PASS_WIDTH, 3), dtype=np.uint8)
    frame[selected_top : selected_top + 2, 214:350] = (133, 205, 230)
    frame[selected_top : selected_top + 60, 214:216] = (133, 205, 230)
    frame[493:531, 884:1018] = brightness
    return frame


WIDTH, HEIGHT = 1920, 1080
REAL_W, REAL_H = 3840, 2160

# Real OCR text from probe_outputs/glutalk_probe_latest.txt, trimmed to the
# meaningful boxes. Note the global bottom navigation word 剧情 is included:
# an earlier build wrongly treated it as an in-progress story dialogue.
REAL_GLUTALK_TEXT = (
    "我的小屋 好友 公会 经营管理 格鲁TALK 亲密度 GLU TALK "
    "© 今日的剩余相遇0 X D-3 未读消息1 代号O 爱丽洁 "
    "澄心无邪兔女郎 提尔 MAX 1 …嘿嘿，我喜欢你，店长。 "
    "② 代号0爱丽洁 然后一次都不露面呢? 1 怎么能那么无耻地行骗， "
    "发送消息 对不起… 角色 背包 剧情 珍藏集 任务 成就 活动 付费商店 返回战场"
)
REAL_GLUTALK_BOXES = [
    SimpleNamespace(name="GLU TALK", x=976, y=512, width=304, height=68, confidence=0.98),
    SimpleNamespace(name="© 今日的剩余相遇0", x=2424, y=504, width=512, height=80, confidence=0.87),
    SimpleNamespace(name="未读消息1", x=1008, y=652, width=220, height=50, confidence=0.99),
    SimpleNamespace(name="1", x=900, y=854, width=52, height=52, confidence=0.69),
    SimpleNamespace(name="② 代号0爱丽洁", x=1186, y=1146, width=360, height=78, confidence=0.85),
    SimpleNamespace(name="1", x=1916, y=1192, width=68, height=66, confidence=0.91),
    SimpleNamespace(name="发送消息", x=2080, y=1504, width=212, height=56, confidence=0.99),
    SimpleNamespace(name="对不起…", x=2444, y=1600, width=180, height=56, confidence=0.98),
    SimpleNamespace(name="剧情", x=904, y=2018, width=84, height=46, confidence=1.0),
]


def box(name, x, y, w=120, h=32, confidence=0.9):
    return SimpleNamespace(name=name, x=x, y=y, width=w, height=h, confidence=confidence)


def make_probe_task(**overrides):
    task = object.__new__(GlutalkTask)
    task.config = {
        "启用": True,
        "仅探测模式": True,
        "保存探测截图": True,
        "格鲁TALK OCR 阈值": 0.35,
        "可对话关键词": "可对话",
    }
    task.config.update(overrides)
    task.info = {}
    task.info_set = lambda key, value: task.info.__setitem__(key, value)
    task.log_completion = lambda *_args, **_kwargs: None
    task.log_warning = lambda *_args, **_kwargs: None
    task.sleep = lambda *_args, **_kwargs: None
    task.operate_click = Mock(side_effect=AssertionError("探测模式不应点击游戏"))
    task.save_frame = lambda *_args, **_kwargs: Path("probe_outputs/fake.png")
    task.write_probe_text = lambda name, lines: (
        task.info.__setitem__("probe_lines", lines) or Path(name)
    )
    task._last_diagnostic = ""
    return task


def scaled_reward_fixture(name, size):
    frame, boxes = fixture(name)
    w, h = size
    sx, sy = w / frame.shape[1], h / frame.shape[0]
    scaled = [SimpleNamespace(name=b.name, x=b.x * sx, y=b.y * sy,
                              width=b.width * sx, height=b.height * sy) for b in boxes]
    return cv2.resize(frame, size), scaled


class MissionRewardVisionTest(unittest.TestCase):
    def setUp(self):
        self.template = cv2.imread(str(DISABLED_BUTTON_FILE))

    def test_captured_reward_pages_require_section_and_button_evidence(self):
        for name, section, expected, sizes in (
            ("daily", SECTIONS[0], "empty", [(1920, 1080)]),
            ("weekly", SECTIONS[1], "empty",
             [(1920, 1080), (1280, 720), (1366, 768), (2560, 1440), (3840, 2160)]),
            ("weekly_available", SECTIONS[1], "claim", [(1920, 1080)]),
        ):
            for size in sizes:
                with self.subTest(name=name, size=size):
                    frame, boxes = scaled_reward_fixture(name, size)
                    view = mission_reward_view(frame, boxes, self.template)
                    self.assertEqual((expected, section), (view.state, view.section))
                    self.assertEqual(set(SECTIONS), set(dict(view.tabs)))
                    self.assertTrue(.81 < view.point[0] < .94)
        frame, boxes = fixture("daily")
        for missing in ("全部获得", "区间奖励", "每周任务", "每日任务"):
            view = mission_reward_view(
                frame, [b for b in boxes if b.name != missing], self.template
            )
            self.assertEqual("unknown", view.state)
            if missing == "区间奖励":
                self.assertIn("数量异常", view.diagnostics)
        self.assertEqual("unknown", mission_reward_view(frame // 2, boxes, self.template).state)
        view = mission_reward_view(frame, boxes, None)
        self.assertEqual("unknown", view.state)
        self.assertIn("禁用态素材", view.diagnostics)
        tab = next(b for b in boxes if b.name == "每周任务")
        for noisy in ("日每周任务", "2 每周任务", "0每周任务", "每週任務", "@每周任务",
                      "每周任务0", "duplicate"):
            with self.subTest(noisy=noisy):
                tab.name = "每周任务" if noisy == "duplicate" else noisy
                duplicate = SimpleNamespace(**{**vars(tab), "y": tab.y + 3})
                view = mission_reward_view(
                    frame, boxes + ([duplicate] if noisy == "duplicate" else []), self.template
                )
                self.assertEqual(
                    ("empty", SECTIONS[0], ""), (view.state, view.section, view.diagnostics)
                )
                self.assertEqual(set(SECTIONS), set(dict(view.tabs)))
        tab.name = "每周任务完成数11"
        view = mission_reward_view(frame, boxes, self.template)
        self.assertEqual("unknown", view.state)
        self.assertIn("页签未识别", view.diagnostics)
        tab.name = "每周任务"
        expected = next(b for b in boxes if b.name == "全部获得")
        for brighten_label_only in (False, True):
            candidate = frame.copy()
            x, y, right, bottom = DISABLED_BUTTON_ROI
            if brighten_label_only:
                label = candidate[y + 9 : y + 34, x + 65 : x + 150]
                label[cv2.cvtColor(label, cv2.COLOR_BGR2GRAY) < 65] = 230
            else:
                candidate[y:bottom, x:right] = 230
            view = mission_reward_view(candidate, boxes, self.template)
            self.assertEqual("claim", view.state)
            self.assertEqual(
                (
                    (expected.x + expected.width / 2) / 1920,
                    (expected.y + expected.height / 2) / 1080,
                ),
                view.point,
            )


    def test_reward_popup_requires_current_evidence_and_rejects_purchase_controls(self):
        frame, boxes = fixture("reward_popup")
        for missing in (None, "REWARD", "获得1种", "点击画面即可返回。"):
            with self.subTest(missing=missing):
                view = mission_reward_view(
                    frame, [b for b in boxes if b.name != missing], self.template
                )
                self.assertEqual(missing is None, view.state == "popup")
                if missing is None:
                    self.assertTrue(0.85 < view.point[1] < 0.97)
        frame, _ = fixture("daily")
        title = box("获得奖励", 850, 350, 180, 40)
        for danger in ("", "请选择奖励", "消耗钻石", "购买"):
            labels = [title] + ([box(danger, 850, 500, 150, 30)] if danger else [])
            view = mission_reward_view(frame, labels, self.template)
            self.assertEqual("unknown" if danger else "popup", view.state)


class MissionRewardFlowTest(unittest.TestCase):
    def make_task(self, views, **config):
        task = object.__new__(MissionRewardTask)
        task.config = {"领奖等待秒数": 4, "最多领取次数": 2, "领取赛季通行证奖励": False, **config}
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

    def test_mail_only_claims_both_tabs_and_requires_home_before_completion(self):
        fixtures = FIXTURES.parent / "scene_home_button"
        templates = DISABLED_BUTTON_FILE.parent.parent
        home = cv2.imread(str(fixtures / "home_720p.png"))
        house = cv2.imread(str(fixtures / "my_home_720p.png"))
        found = match_template(home, MAIL_ICON, {}, templates)
        self.assertGreaterEqual(found.score, MAIL_ICON.threshold)
        self.assertEqual(found.center, (1071, 39))
        self.assertLess(match_template(house, MAIL_ICON, {}, templates).score, MAIL_ICON.threshold)
        for start, products, ignore_switch, reaches_home, expected_claims in (
            (0, True, False, True, [0, 1]),
            (1, True, False, False, [0, 1]),
            (1, False, False, True, [0]),
            (1, False, True, True, []),
        ):
            with self.subTest(start=start, products=products, ignored=ignore_switch):
                task = self.make_task([], 领取每日奖励=False, 领取每周奖励=False)
                if not products:
                    task = object.__new__(MailRewardTask)
                    task.config = {"领取商品邮箱": False}
                    task.info_set = Mock()
                    task.sleep = self.clock.sleep
                task.name = "邮件测试"
                task.auto_return_main_home = Mock(return_value=reaches_home)
                task.log_completion = Mock()
                selected = start
                claimed = []

                def capture():
                    frame = np.full((100, 200, 3), 100, np.uint8)
                    top = (13, 23)[selected]
                    frame[top : top + 6, 16:24] = 235
                    if selected not in claimed:
                        frame[91:96, 150:170] = 230
                    return frame

                def click(center_x, center_y, **_):
                    nonlocal selected
                    if (center_x, center_y) == (0.8, 0.87):
                        claimed.append(selected)
                    elif not ignore_switch:
                        selected = [(0.10, 0.16), (0.10, 0.26)].index((center_x, center_y))

                def ocr(**_):
                    title = ("o普通邮箱", "商品邮箱")[selected] if products else "邮箱"
                    rows = (
                        (title, 12, 3, 8, 6),
                        ("普通邮箱", 16, 13, 8, 6),
                        ("商品邮箱", 16, 23, 8, 6),
                        ("全部领取", 150, 83, 20, 8),
                    )
                    return [
                        SimpleNamespace(name=name, x=left, y=top, width=width, height=height)
                        for name, left, top, width, height in rows
                    ]

                task.ocr = ocr
                task.capture_frame = capture
                task.operate_click = click
                with patch("src.tasks.MailRewardTask.monotonic", self.clock):
                    self.assertIs(task.run(), reaches_home and not ignore_switch)
                self.assertEqual(expected_claims, claimed)
                self.assertEqual(int(not ignore_switch), task.auto_return_main_home.call_count)
                self.assertEqual(
                    int(reaches_home and not ignore_switch), task.log_completion.call_count
                )

    def test_claim_section_requires_consecutive_empty_frames_and_limits_clicks(self):
        section = SECTIONS[0]
        claim = RewardView("claim", section, (0.88, 0.93))
        popup = RewardView("popup", point=(0.5, 0.9))
        empty = RewardView("empty", section)
        for views, ok, actions in (
            ([claim, popup, popup, popup, empty, empty], True, [claim.point, popup.point]),
            ([claim] * 20, False, [claim.point] * 2),
            ([empty, RewardView()] * 10, False, []),
            ([RewardView("claim", SECTIONS[1], claim.point)] * 20, False, []),
        ):
            with self.subTest(views=views):
                task = self.make_task(views, 领奖等待秒数=8)
                self.assertEqual(ok, self.claim(task))
                self.assertEqual(actions, [c.args for c in task.operate_click.call_args_list])

        # Repeated unreadable frames report a new diagnostic only when the evidence changes.
        frame, boxes = fixture("daily")
        task = self.make_task([])
        task.info_set = Mock()
        task.capture_frame = Mock(return_value=frame)
        task.ocr = Mock(return_value=boxes)
        task._disabled_button = None
        task._last_view_diagnostic = ""
        del task._read_view
        for _ in range(3):
            self.assertEqual("unknown", task._read_view()[0].state)
        task.ocr.return_value = []
        self.assertEqual("unknown", task._read_view()[0].state)
        reasons = [c.args[1] for c in task.info_set.call_args_list if c.args[0] == "页面判定诊断"]
        self.assertEqual(2, len(reasons))
        self.assertEqual("按钮区域为空或缺少禁用态素材", reasons[0])
        self.assertIn("页签未识别", reasons[1])



class SeasonPassViewTest(unittest.TestCase):
    def test_each_card_requires_its_own_selected_border(self):
        selections = ((254, SPECIAL), (325, "bloodroyale赛季通行证"), (396, CHARACTER))
        for selected_top, name in selections:
            with self.subTest(name=name):
                view = season_pass_view(pass_frame(selected_top), boxes(PASS_LABELS))
                self.assertEqual(("claim", name), (view.state, view.section))
                self.assertEqual((0.749, 0.707), view.point)
        labels = PASS_LABELS + (("购买等级", 0.50, 0.45), ("确认", 0.58, 0.72))
        view = season_pass_view(pass_frame(254), boxes(labels))
        self.assertEqual("unknown", view.state)
        self.assertIsNone(view.point)

    def test_visits_season_and_character_pass_and_confirms_disabled_button(self):
        task = object.__new__(MissionRewardTask)
        task.config = {"领取每日奖励": False, "领取每周奖励": False, "领取邮件": False}
        clock = [0.0]
        section_one = SPECIAL
        pass_boxes = boxes([label for label in PASS_LABELS if label[0] != REGULAR])
        card_one, card_two = (0.219, 0.392), (0.219, 0.590)
        button, dismiss = (0.749, 0.707), (0.50, 0.91)
        guide = season_pass_view(pass_frame(183), pass_boxes)
        tabs = guide.tabs
        available = RewardView("claim", section_one, button, tabs)
        cleared = RewardView("empty", section_one, button, tabs)
        other = season_pass_view(pass_frame(396, 128), pass_boxes)
        popup = RewardView("popup", point=dismiss)
        frames = iter(
            [
                guide,
                guide,
                guide,
                available,
                available,
                popup,
                popup,
                popup,
                cleared,
                cleared,
                cleared,
                cleared,
                other,
                other,
                other,
                other,
                RewardView(),
            ]
        )
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


class GlutalkTest(unittest.TestCase):
    def test_observed_home_and_talk_list_only_target_the_unread_conversation(self):
        point = home_talk_entry([box("经营管理格鲁TALK", 248, 580, 379, 45)], 2560, 1440)
        self.assertIsNotNone(point)
        self.assertTrue(0.19 < point[0] < 0.22 and 0.36 < point[1] < 0.42)
        view = classify_talk_page(REAL_GLUTALK_TEXT)
        self.assertEqual("list", view.page)
        self.assertIn("glutalk", [word.lower() for word in view.evidence])
        self.assertEqual(0, remaining_encounters(REAL_GLUTALK_TEXT))
        self.assertTrue(is_exhausted(REAL_GLUTALK_TEXT))
        self.assertEqual(1, unread_count(REAL_GLUTALK_TEXT))
        for unread in (None, 0, 1):
            with self.subTest(unread=unread):
                point, evidence = unread_badge_point(
                    REAL_GLUTALK_BOXES, REAL_W, REAL_H, unread=unread
                )
                if unread == 1:
                    np.testing.assert_allclose(
                        point, ((1916 + 34) / REAL_W, (1192 + 33) / REAL_H), atol=0.0005
                    )
                    self.assertIn("未读消息1", evidence)
                else:
                    self.assertIsNone(point)
        for enabled, y, expected in ((False, 900, None), (True, 200, None), (True, 900, "choice")):
            decision = dialogue_decision(
                "选择", [box("选择", 900, y)], WIDTH, HEIGHT, allow_choice=enabled
            )
            self.assertEqual(expected, decision.kind if decision else None)
        entries = [box("可对话 已完成", 600, 300), box("未解锁 会面", 600, 400)]
        self.assertIsNone(
            available_task_point(entries, WIDTH, HEIGHT, AVAILABLE_TASK_DEFAULT_KEYWORDS)[0]
        )

    def test_unread_badge_skips_malformed_digits_and_keeps_valid_candidate(self):
        boxes = [box("⑨1454289", 1950, 1030), box("１", 1950, 1190)]
        point, evidence = unread_badge_point(boxes, REAL_W, REAL_H, unread=1)
        self.assertIsNotNone(point)
        self.assertIn("角标 1", evidence)
        self.assertIsNone(unread_badge_point(boxes[:1], REAL_W, REAL_H, unread=1)[0])

    def test_reply_stops_after_five_stalled_attempts_or_finishes_a_long_dialogue(self):
        module = importlib.import_module("src.tasks.GlutalkTask")
        frame = np.zeros((1000, 1000, 3), np.uint8)
        for progressing, expected, clicks in ((False, "failed", 5), (True, "ended", 30)):
            with self.subTest(progressing=progressing):
                task = make_probe_task()
                task.log_warning = Mock()
                task.operate_click = Mock()
                missed_message = False
                seen_counts = []

                def read():
                    nonlocal missed_message
                    count = task.operate_click.call_count
                    seen_counts.append(count)
                    if count == 30:
                        return frame, [], "GLU TALK 未读消息0 EP2.对话结束"
                    message = f"新消息{count}" if progressing else "同一条消息"
                    boxes = [box("发送消息", 650, 690), box("好的", 660, 750)]
                    text = "GLU TALK 今日的剩余相遇0 未读消息1"
                    if count == 1 and not missed_message:
                        missed_message = True
                        return frame, boxes, text
                    boxes.append(box(message, 620, 540))
                    return frame, boxes, text

                task._read = read
                with patch.object(module, "monotonic", return_value=1.0):
                    self.assertEqual(expected, task._advance_stage(1))
                self.assertEqual(clicks, task.operate_click.call_count)
                self.assertEqual([0, 1, 1], seen_counts[:3])

    def test_scan_reopens_only_once_between_zero_click_stages(self):
        for stages, expected, reopens, completed in (
            ((("ended", 0), ("ended", 0)), False, 1, 0),
            ((("home", 0), ("home", 0)), False, 1, 0),
            ((("home", 0), ("idle", 0)), True, 1, 0),
            ((("ended", 0), ("ended", 1), ("home", 0), ("idle", 0)), True, 3, 1),
            ((("ended", 1),) * 4 + (("idle", 0), ("idle", 0)), True, 5, 4),
        ):
            with self.subTest(stages=stages):
                task = make_probe_task()
                remaining = iter(stages)

                def advance(_stage, _deadline):
                    result, clicks = next(remaining)
                    task._click_total += clicks
                    return result

                task._advance_stage = Mock(side_effect=advance)
                task._restart_from_home = Mock(return_value=True)
                task._wait_for_next_stage = Mock(return_value=True)
                self.assertEqual(expected, task._scan())
                self.assertEqual(reopens, task._restart_from_home.call_count)
                self.assertEqual(completed, task.info["完成对话数"])
                if not expected:
                    self.assertIn("重进后仍未推进", task.info["状态"])

    def test_scan_stays_incomplete_when_deadline_expires_after_a_completed_episode(self):
        task = make_probe_task(整轮最长秒数=10.0)
        clock = FakeClock()

        def advance(_stage, _deadline):
            task._click_total += 1
            return "ended"

        task._advance_stage = Mock(side_effect=advance)
        task._restart_from_home = Mock(return_value=True)
        task._wait_for_next_stage = Mock(side_effect=lambda _: clock.sleep(10.0))
        with patch("src.tasks.GlutalkTask.monotonic", clock):
            self.assertFalse(task._scan())
        self.assertEqual(1, task.info["完成对话数"])
        self.assertIn("整轮对话等待超时", task.info["状态"])

    def test_reply_button_accepts_ellipsis_ocr_and_sky_blue_pill_fallback(self):
        send_box = [box("发送消息", int(0.56 * WIDTH), int(0.70 * HEIGHT))]
        # Layer 1: pure punctuation reply ("...") recognized by OCR below 发送消息.
        ellipsis_boxes = [*send_box, box("...", int(0.66 * WIDTH), int(0.76 * HEIGHT))]
        point, evidence = reply_button_point(
            ellipsis_boxes, WIDTH, HEIGHT, require_send_control=True,
        )
        self.assertIsNotNone(point)
        self.assertIn("...", evidence)

        # Layer 2: OCR misses "..." completely, but sky-blue reply pill is visible below 发送消息.
        frame = np.full((HEIGHT, WIDTH, 3), (198, 184, 172), dtype=np.uint8)
        frame[int(HEIGHT * 0.74) : int(HEIGHT * 0.79), int(WIDTH * 0.54) : int(WIDTH * 0.78)] = (
            255, 231, 177,
        )
        pill_point, pill_evidence = reply_button_point(
            send_box, WIDTH, HEIGHT, require_send_control=True, frame=frame,
        )
        self.assertIsNotNone(pill_point)
        self.assertAlmostEqual(0.66, pill_point[0], delta=0.02)
        self.assertAlmostEqual(0.765, pill_point[1], delta=0.02)
        self.assertEqual("回复按钮：浅蓝回复条", pill_evidence)

        # Safety gate: without 发送消息, the same sky-blue pill must NOT be clicked.
        blocked_point, _ = reply_button_point(
            [], WIDTH, HEIGHT, require_send_control=True, frame=frame,
        )
        self.assertIsNone(blocked_point)

        # Unread badge visual fallback: cyan circle at right edge of row when OCR misses "1".
        list_frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        cv2.circle(
            list_frame,
            (int(WIDTH * 0.508), int(HEIGHT * 0.394)),
            int(HEIGHT * 0.012),
            (193, 158, 33),
            -1,
        )
        badge_pt, badge_ev = unread_badge_point([], WIDTH, HEIGHT, unread=1, frame=list_frame)
        self.assertIsNotNone(badge_pt)
        self.assertAlmostEqual(0.508, badge_pt[0], delta=0.01)
        self.assertAlmostEqual(0.394, badge_pt[1], delta=0.01)
        self.assertIn("会话蓝色未读角标", badge_ev)
        self.assertIsNone(unread_badge_point([], WIDTH, HEIGHT, unread=0, frame=list_frame)[0])

        # Close X visual fallback on blue GLU TALK header bar when OCR misses "X".
        header_frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        y1, y2 = int(HEIGHT * 0.22), int(HEIGHT * 0.29)
        x1, x2 = int(WIDTH * 0.20), int(WIDTH * 0.80)
        header_frame[y1:y2, x1:x2] = (180, 110, 40)
        cx, cy = int(WIDTH * 0.780), int(HEIGHT * 0.253)
        cv2.line(header_frame, (cx - 10, cy - 10), (cx + 10, cy + 10), (255, 255, 255), 3)
        cv2.line(header_frame, (cx - 10, cy + 10), (cx + 10, cy - 10), (255, 255, 255), 3)
        close_pt = close_button_point([], WIDTH, HEIGHT, frame=header_frame)
        self.assertIsNotNone(close_pt)
        self.assertAlmostEqual(0.780, close_pt[0], delta=0.01)
        self.assertAlmostEqual(0.253, close_pt[1], delta=0.01)
        # Non-blue background must not produce a visual close click.
        self.assertIsNone(close_button_point([], WIDTH, HEIGHT, frame=list_frame))

    def test_report_counters_recovers_remaining_quota_with_crop_ocr(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                task = make_probe_task(仅探测模式=False)
                task.config["格鲁TALK OCR 阈值"] = 0.42
                frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
                task.ocr = Mock(
                    return_value=[] if fallback else [box("今日的剩余相遇|3 +", 10, 10)]
                )
                digit_ocr = Mock(return_value=[[("｜3＋", 0.96)]])
                task._executor = SimpleNamespace(ocr_lib=lambda: SimpleNamespace(ocr=digit_ocr))
                remaining, unread = task._report_counters(
                    "GLU TALK 今日的剩余相遇 未读消息1", frame=frame
                )
                self.assertEqual((3, 1), (remaining, unread))
                self.assertEqual(0.42, task.ocr.call_args.kwargs["threshold"])
                self.assertEqual(int(fallback), digit_ocr.call_count)
                if fallback:
                    self.assertEqual({"det": False, "cls": False}, digit_ocr.call_args.kwargs)

    def test_restart_from_home_retries_close_and_reopen(self):
        task = make_probe_task(仅探测模式=False, 重进后等待秒数=0.0, 页面等待秒数=12.0)
        task.operate_click = Mock()
        clock = FakeClock()
        task.sleep = clock.sleep
        task.prepare_home = Mock(return_value=True)
        task._frame_confirms_home = Mock(side_effect=[True, False, True])
        frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        list_text = "GLU TALK 未读消息0 今日的剩余相遇1"
        header_frame = frame.copy()
        header_frame[int(.22 * HEIGHT):int(.29 * HEIGHT), int(.20 * WIDTH):int(.80 * WIDTH)] = (
            180, 110, 40,
        )
        cx, cy = int(.78 * WIDTH), int(.253 * HEIGHT)
        cv2.line(header_frame, (cx - 10, cy - 10), (cx + 10, cy + 10), (255, 255, 255), 3)
        cv2.line(header_frame, (cx - 10, cy + 10), (cx + 10, cy - 10), (255, 255, 255), 3)
        home_boxes = [box("格鲁TALK", int(0.205 * WIDTH) - 60, int(0.35 * HEIGHT) - 16)]
        reads = iter(
            [
                (frame, [], list_text),
                (header_frame, [], list_text),
                (frame, [], "主页"),
                (frame, home_boxes, "主页"),
                (frame, home_boxes, "GLU TALK"),
                (frame, home_boxes, "主页"),
            ]
        )
        task._read = Mock(side_effect=lambda: next(reads))
        task._wait_for_page = Mock(side_effect=[False, True])
        with patch("src.tasks.GlutalkTask.monotonic", clock):
            self.assertTrue(task._restart_from_home())
        names = [c.kwargs["name"] for c in task.operate_click.call_args_list]
        self.assertEqual(["格鲁TALK关闭", "格鲁TALK入口", "格鲁TALK入口"], names)
        self.assertEqual([call("list", 12.0, float("inf"))] * 2, task._wait_for_page.call_args_list)

    def test_probe_and_completed_dialogue_never_dispatch_clicks(self):
        frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        frame[int(HEIGHT * .74):int(HEIGHT * .79), int(WIDTH * .54):int(WIDTH * .78)] = (
            255, 231, 177,
        )
        boxes = [box("发送消息", int(.56 * WIDTH), int(.70 * HEIGHT))]
        for probe, text in (
            (True, "联系方式 好感度"),
            (False, "GLU TALK 未读消息0 今日的剩余相遇0 EP2.对话结束"),
        ):
            with (
                self.subTest(probe=probe),
                patch.object(BaseBD2Task, "capture_method_name", "unit-test"),
            ):
                task = make_probe_task(仅探测模式=probe)
                task._read = Mock(return_value=(frame, [] if probe else boxes, text))
                if probe:
                    self.assertTrue(task.run())
                    self.assertEqual("list", task.info["页面判断"])
                    for field in ("OCR TEXT", "remaining_encounters="):
                        self.assertTrue(any(field in line for line in task.info["probe_lines"]))
                    self.assertIn("仅探测模式", task.info["状态"])
                else:
                    self.assertEqual("ended", task._advance_stage(1))
                    self.assertEqual(2, task._last_episode)
                    self.assertFalse(task._has_new_stage(text, boxes, WIDTH, HEIGHT, 0))
                task.operate_click.assert_not_called()

    def test_click_skip_template_falls_back_to_story_skip_icon(self):
        from src.tasks.GlutalkTask import SKIP_TEMPLATE, STORY_SKIP_TEMPLATE

        task = make_probe_task()
        frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        called = []

        def fake_click(_frame, spec):
            called.append(spec)
            return spec == STORY_SKIP_TEMPLATE

        task._click_template = fake_click
        self.assertTrue(task._click_skip_template(frame))
        self.assertEqual([SKIP_TEMPLATE, STORY_SKIP_TEMPLATE], called)


if __name__ == "__main__":
    unittest.main()
