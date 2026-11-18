"""格鲁TALK 视觉判定与安全红线回归。

界面常量已用真实 3840x2160 GLU TALK 帧（2026-11-15 17:59）校准，
另验证「有证据才动作、无证据必停止」的契约与仅探测模式绝不点击游戏。
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.glutalk_vision import (
    AVAILABLE_TASK_DEFAULT_KEYWORDS,
    available_task_point,
    classify_talk_page,
    close_button_point,
    dialogue_decision,
    dialogue_ended,
    episode_number,
    home_talk_entry,
    is_exhausted,
    matched_keywords,
    remaining_encounters,
    reply_button_point,
    unread_badge_point,
    unread_count,
)
from src.tasks.GlutalkTask import GlutalkTask

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
    return SimpleNamespace(
        name=name, x=x, y=y, width=w, height=h, confidence=confidence,
    )


class GlutalkVisionTest(unittest.TestCase):
    def test_home_entry_full_chinese_label(self):
        boxes = [
            box("我的小屋", 120, 180),
            box("格鲁TALK", 300, 250),
            box("街机游戏", 480, 260),
        ]
        point = home_talk_entry(boxes, WIDTH, HEIGHT)
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point[0], (300 + 60) / WIDTH)
        self.assertAlmostEqual(point[1], (250 + 16) / HEIGHT - 0.025)

    def test_real_home_ocr_merged_label_clicks_on_glu_talk_icon(self):
        # Real 2560x1440 home frame: "经营管理格鲁TALK" is ONE OCR box.
        real = box("经营管理格鲁TALK", 248, 580, 379, 45)
        point = home_talk_entry([real], 2560, 1440)
        self.assertIsNotNone(point)
        # Box center x is 0.171 (经营管理); alias offset moves it to 格鲁TALK.
        self.assertGreater(point[0], 0.19)
        self.assertLess(point[0], 0.22)
        self.assertGreater(point[1], 0.36)
        self.assertLess(point[1], 0.42)

    def test_home_entry_bare_talk_is_weak_but_usable(self):
        point = home_talk_entry([box("TALK", 300, 250)], WIDTH, HEIGHT)
        self.assertIsNotNone(point)

    def test_home_entry_ignores_out_of_roi_and_unrelated_text(self):
        self.assertIsNone(home_talk_entry([box("格鲁TALK", 1500, 900)], WIDTH, HEIGHT))
        self.assertIsNone(home_talk_entry([box("商店", 300, 250)], WIDTH, HEIGHT))
        self.assertIsNone(home_talk_entry([], WIDTH, HEIGHT))

    def test_list_page_requires_two_inner_keywords(self):
        view = classify_talk_page("格鲁TALK 联系方式 好感度 会面")
        self.assertEqual("list", view.page)
        self.assertEqual("", view.diagnostics)

    def test_home_screen_alone_never_looks_like_list(self):
        view = classify_talk_page("我的小屋 好友 公会 经营管理 格鲁TALK 亲密度 街机游戏")
        self.assertEqual("unknown", view.page)
        self.assertIn("未命中", view.diagnostics)

    def test_single_list_keyword_is_not_enough(self):
        view = classify_talk_page("联系方式")
        self.assertEqual("unknown", view.page)
        self.assertIn("证据不足", view.diagnostics)

    def test_dialogue_and_popup_and_empty(self):
        self.assertEqual("dialogue", classify_talk_page("剧情 点击继续 跳过").page)
        self.assertEqual("popup", classify_talk_page("获得物品 点击画面即可返回").page)
        empty = classify_talk_page("")
        self.assertEqual("unknown", empty.page)
        self.assertIn("OCR 为空", empty.diagnostics)

    def test_real_glutalk_frame_is_list_not_story_dialogue(self):
        view = classify_talk_page(REAL_GLUTALK_TEXT)
        self.assertEqual("list", view.page)
        self.assertIn("glutalk", [word.lower() for word in view.evidence])

    def test_bottom_navigation_plot_word_is_not_dialogue_evidence(self):
        view = classify_talk_page("角色 背包 剧情 珍藏集 任务 成就 活动 付费商店 返回战场")
        self.assertEqual("unknown", view.page)

    def test_real_counters_and_exhausted_detection(self):
        self.assertEqual(0, remaining_encounters(REAL_GLUTALK_TEXT))
        self.assertEqual(1, unread_count(REAL_GLUTALK_TEXT))
        self.assertTrue(is_exhausted(REAL_GLUTALK_TEXT))
        self.assertTrue(is_exhausted("今日会面次数已用尽 0/3"))
        self.assertFalse(is_exhausted("联系方式 好感度 可对话"))
        self.assertIsNone(remaining_encounters("我的小屋 好友 公会"))
        self.assertIsNone(unread_count("我的小屋 好友 公会"))

    def test_dialogue_end_marker_and_episode_number(self):
        end_text = "GLU TALK 未读消息0 绝对不会放过她！ EP2.对话结束 魔兽追踪者解锁"
        self.assertTrue(dialogue_ended(end_text))
        self.assertEqual(2, episode_number(end_text))
        self.assertTrue(dialogue_ended("对话结束"))
        self.assertTrue(dialogue_ended("EP3．对话结来"))
        self.assertFalse(dialogue_ended("GLU TALK 未读消息1 对不起… 发送消息"))
        self.assertIsNone(episode_number("对话结束"))

    def test_reply_button_ignores_chapter_separator_and_character_name(self):
        # Real 18:10:58 frame: the task wrongly clicked these three times.
        boxes = [
            box("EP2.对话结束", 2450, 1634, 180, 56),
            box("代号O 爱丽洁", 2276, 1542, 180, 56),
            box("发送消息", 2080, 1504, 212, 56),
        ]
        point, evidence = reply_button_point(boxes, REAL_W, REAL_H)
        self.assertIsNone(point)
        self.assertIn("没有回复按钮", evidence)

    def test_close_button_point_uses_title_bar_x(self):
        point = close_button_point([box("X", 2964, 520, 56, 52)], REAL_W, REAL_H)
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point[0], (2964 + 28) / REAL_W, places=3)
        self.assertAlmostEqual(point[1], (520 + 26) / REAL_H, places=3)
        self.assertIsNone(close_button_point([box("X", 100, 100)], REAL_W, REAL_H))

    def test_unread_badge_targets_only_the_conversation_row(self):
        point, evidence = unread_badge_point(
            REAL_GLUTALK_BOXES, REAL_W, REAL_H, unread=1,
        )
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point[0], (1916 + 34) / REAL_W, places=3)
        self.assertAlmostEqual(point[1], (1192 + 33) / REAL_H, places=3)
        self.assertIn("未读消息1", evidence)
        self.assertIsNone(
            unread_badge_point(REAL_GLUTALK_BOXES, REAL_W, REAL_H, unread=0)[0],
        )
        self.assertIsNone(
            unread_badge_point(REAL_GLUTALK_BOXES, REAL_W, REAL_H, unread=None)[0],
        )

    def test_reply_button_is_lower_right_and_excludes_send_message(self):
        point, evidence = reply_button_point(REAL_GLUTALK_BOXES, REAL_W, REAL_H)
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point[0], (2444 + 90) / REAL_W, places=3)
        self.assertAlmostEqual(point[1], (1600 + 28) / REAL_H, places=3)
        self.assertIn("对不起", evidence)
        self.assertIsNone(reply_button_point([box("发送消息", 2080, 1504)], REAL_W, REAL_H)[0])

    def test_available_task_ignores_unread_summary_header(self):
        point, _ = available_task_point(
            [box("未读消息1", 1008, 652, 220, 50)], REAL_W, REAL_H,
            AVAILABLE_TASK_DEFAULT_KEYWORDS,
        )
        self.assertIsNone(point)

    def test_available_task_uses_topmost_positive_row(self):
        boxes = [
            box("会面 可对话", 600, 600),
            box("新剧情 可对话", 600, 300),
            box("已完成 可对话", 600, 200),
        ]
        point, evidence = available_task_point(
            boxes, WIDTH, HEIGHT, AVAILABLE_TASK_DEFAULT_KEYWORDS,
        )
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point[1], (300 + 16) / HEIGHT)
        self.assertIn("可对话", evidence)

    def test_available_task_excludes_negative_rows(self):
        boxes = [box("可对话 已完成", 600, 300), box("未解锁 会面", 600, 400)]
        point, evidence = available_task_point(
            boxes, WIDTH, HEIGHT, AVAILABLE_TASK_DEFAULT_KEYWORDS,
        )
        self.assertIsNone(point)
        self.assertIn("没有", evidence)

    def test_available_task_requires_configured_keywords(self):
        point, evidence = available_task_point(
            [box("可对话", 600, 300)], WIDTH, HEIGHT, (),
        )
        self.assertIsNone(point)
        self.assertIn("未配置", evidence)

    def test_available_task_ignores_out_of_list_roi(self):
        point, _ = available_task_point(
            [box("可对话", 60, 40)], WIDTH, HEIGHT, AVAILABLE_TASK_DEFAULT_KEYWORDS,
        )
        self.assertIsNone(point)

    def test_dialogue_skip_and_dismiss_are_evidence_backed(self):
        skip = dialogue_decision("跳过", [box("跳过", 1700, 60)], WIDTH, HEIGHT)
        self.assertEqual("skip", skip.kind)
        dismiss = dialogue_decision(
            "点击画面即可返回", [box("点击画面即可返回", 900, 980)], WIDTH, HEIGHT,
        )
        self.assertEqual("dismiss", dismiss.kind)

    def test_choice_requires_opt_in_and_lower_screen(self):
        lower = [box("选择", 900, 900)]
        self.assertIsNone(dialogue_decision("选择", lower, WIDTH, HEIGHT))
        self.assertEqual(
            "choice",
            dialogue_decision("选择", lower, WIDTH, HEIGHT, allow_choice=True).kind,
        )
        upper = [box("选择", 900, 200)]
        self.assertIsNone(
            dialogue_decision("选择", upper, WIDTH, HEIGHT, allow_choice=True),
        )

    def test_no_evidence_returns_no_action(self):
        self.assertIsNone(dialogue_decision(
            "普通剧情文本", [box("角色名", 300, 800)], WIDTH, HEIGHT))
        self.assertEqual((), matched_keywords("", ("跳过",)))


def make_probe_task(**overrides):
    task = object.__new__(GlutalkTask)
    task.config = {
        "启用": True, "仅探测模式": True, "保存探测截图": True,
        "格鲁TALK OCR 阈值": 0.35, "可对话关键词": "可对话",
        "回主页返回键最多次数": 3,
    }
    task.config.update(overrides)
    task.info = {}
    task.info_set = lambda key, value: task.info.__setitem__(key, value)
    task.log_completion = lambda *_args, **_kwargs: None
    task.log_warning = lambda *_args, **_kwargs: None
    task.sleep = lambda *_args, **_kwargs: None
    task.operate_click = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("探测模式不应点击游戏")
    )
    task.save_frame = lambda *_args, **_kwargs: Path("probe_outputs/fake.png")
    task.write_probe_text = lambda name, lines: (
        task.info.__setitem__("probe_lines", lines) or Path(name)
    )
    task._last_diagnostic = ""
    return task


class GlutalkTaskSafetyTest(unittest.TestCase):
    def setUp(self):
        # capture_method_name is a read-only property on BaseBD2Task.
        self.capture_patch = patch.object(
            BaseBD2Task, "capture_method_name", "unit-test",
        )
        self.capture_patch.start()

    def tearDown(self):
        self.capture_patch.stop()

    def test_probe_mode_never_clicks_and_writes_evidence(self):
        import numpy as np
        task = make_probe_task()
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        with patch.object(GlutalkTask, "_read", return_value=(
            frame, [box("联系方式", 600, 300), box("好感度", 600, 400)],
            "联系方式 好感度",
        )):
            self.assertTrue(task.run())
        self.assertEqual("list", task.info["页面判断"])
        self.assertTrue(any("OCR TEXT" in line for line in task.info["probe_lines"]))
        self.assertTrue(any("remaining_encounters=" in line for line in task.info["probe_lines"]))
        self.assertIn("仅探测模式", task.info["状态"])

    def test_missing_home_entry_fails_safely_and_returns_home(self):
        import numpy as np
        task = make_probe_task(仅探测模式=False)
        task._wait_for_home_confirmation = lambda *_a, **_k: True
        task._fail = lambda stage: (
            task.info.__setitem__("状态", f"失败:{stage}") or False
        )
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        with patch.object(GlutalkTask, "_read",
                          return_value=(frame, [box("商店", 600, 300)], "商店")):
            self.assertFalse(task.run())
        self.assertIn("未识别到格鲁TALK入口", task.info["状态"])

    def test_auto_mode_skips_home_and_entry_when_already_in_glutalk(self):
        import numpy as np
        task = make_probe_task(仅探测模式=False)
        task._wait_for_home_confirmation = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("已在格鲁TALK页面时不应回主页")
        )
        task._scan = lambda: (task.info.__setitem__("scan_called", True) or True)
        task._safe_return_home = lambda: True
        frame = np.zeros((REAL_H, REAL_W, 3), dtype=np.uint8)
        with patch.object(GlutalkTask, "_read", return_value=(
            frame, REAL_GLUTALK_BOXES, REAL_GLUTALK_TEXT,
        )):
            self.assertTrue(task.run())
        self.assertEqual("list", task.info["页面判断"])
        self.assertTrue(task.info.get("scan_called"))
        self.assertEqual("已在格鲁TALK页面，直接检查", task.info["当前阶段"])
        self.assertEqual("格鲁TALK 检查完成。", task.info["状态"])
        self.assertEqual("0", task.info["剩余相遇"])
        self.assertEqual("1", task.info["未读消息"])

    def test_no_configured_keywords_means_no_entry_click(self):
        import numpy as np
        task = make_probe_task(仅探测模式=False, 可对话关键词="")
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        point, evidence = available_task_point(
            [box("可对话", 600, 300)], WIDTH, HEIGHT, task._keywords(),
        )
        self.assertIsNone(point)
        self.assertIn("未配置", evidence)
        self.assertEqual((), task._keywords())
        self.assertEqual((HEIGHT, WIDTH), frame.shape[:2])

    def test_episode_end_stops_clicking_immediately(self):
        import numpy as np
        task = make_probe_task(仅探测模式=False)
        # make_probe_task raises if operate_click is ever called.
        end_text = "GLU TALK 未读消息0 今日的剩余相遇0 绝对不会放过她！ EP2.对话结束"
        frame = np.zeros((REAL_H, REAL_W, 3), dtype=np.uint8)
        with patch.object(GlutalkTask, "_read", return_value=(frame, [], end_text)):
            result = task._advance_stage(1)
        self.assertEqual("ended", result)
        self.assertEqual(2, task._last_episode)
        self.assertIn("停止点击", task.info["状态"])

    def test_stage_loop_reopens_glutalk_only_after_an_ended_episode(self):
        import numpy as np
        task = make_probe_task(仅探测模式=False)

        def advance(stage):
            if stage == 1:
                # 真的推进过才会把「对话结束」算进完成数，这里模拟这一段的点击。
                task._click_total += 3
                return "ended"
            return "idle"

        task._advance_stage = advance
        calls = {"restart": 0}

        def restart(*_args, **_kwargs):
            # _scan 现在会用 already_home= 告诉重启流程「游戏自己关了窗口」。
            calls["restart"] += 1
            return True

        task._restart_from_home = restart
        # 重进后没有下一段：循环应当在这一次重进后结束，只统计 1 段。
        task._wait_for_next_stage = lambda: False
        task._read = lambda: (
            np.zeros((REAL_H, REAL_W, 3), dtype=np.uint8), [], "",
        )
        self.assertTrue(task._scan())
        self.assertEqual(1, calls["restart"])
        self.assertEqual(1, task.info["完成对话数"])

    def test_same_episode_history_is_not_a_new_stage(self):
        task = make_probe_task(仅探测模式=False)
        text = "GLU TALK 未读消息0 今日的剩余相遇0 EP2.对话结束 发送消息"
        self.assertFalse(task._has_new_stage(text, [], REAL_W, REAL_H, 2))
        self.assertTrue(
            task._has_new_stage("GLU TALK 未读消息1 发送消息", [], REAL_W, REAL_H, 2),
        )


if __name__ == "__main__":
    unittest.main()
