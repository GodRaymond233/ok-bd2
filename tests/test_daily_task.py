import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks.DailyTask import (
    BUSINESS_GUEST_RELATIVE_ROI,
    GUILD_SIGN_IN_ENTRY_REFERENCE_POINT,
    GUILD_SIGNUP_SUCCESS_TEMPLATE,
    GUILD_SUCCESS_KEYWORDS,
    GUILD_TEMPLATE,
    MY_HOME_BACK_REFERENCE_POINT,
    DailyTask,
)
from src.tasks.map_trade.models import MatchResult
from src.tasks.map_trade.vision import Vision
from tests.helpers import StageHistoryTestCase


class DailyTaskHelperTest(StageHistoryTestCase):
    @staticmethod
    def _business_controls(width=1280, height=720, row=0.8, claim=True, cancel=True):
        def box(name, x, y):
            return SimpleNamespace(
                name=name,
                x=(x - 0.015) * width,
                y=(y - 0.015) * height,
                width=0.03 * width,
                height=0.03 * height,
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
            clicks.append((round(x, 6), round(y, 6))),
            task.sleep(after_sleep),
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
        task.prepare_home = lambda name: True if "入口前" in name else current[0][2]
        task.ocr = lambda **_kwargs: current[0][1]
        task._frame_confirms_home = lambda *_args: current[0][2]
        timer = patch("src.tasks.DailyTask.monotonic", side_effect=lambda: clock[0])
        timer.start()
        self.addCleanup(timer.stop)
        return task, statuses, clicks, reference_clicks, clock

    def test_guild_entry_requires_home_and_a_matching_trigger(self):
        for at_home, matched, stayed, expected in (
            (False, True, True, []),
            (True, False, True, []),
            (True, True, False, [GUILD_SIGN_IN_ENTRY_REFERENCE_POINT]),
            (True, True, True, [GUILD_SIGN_IN_ENTRY_REFERENCE_POINT, (0.3, 0.3), (0.3, 0.3)]),
        ):
            with self.subTest(home=at_home, matched=matched, stayed=stayed):
                task = object.__new__(DailyTask)
                task.config = {"公会进入最大点击次数": 3}
                task.info_set = task.log_info = task.sleep = Mock()
                task.prepare_home = Mock(return_value=at_home)
                task.capture_frame = Mock(return_value=np.zeros((10, 10, 3), np.uint8))
                task._match_best = Mock(
                    return_value=(MatchResult(0.95, (2, 2), (2, 2)), GUILD_TEMPLATE)
                )
                task._passes = Mock(return_value=matched)
                task._frame_confirms_home = Mock(return_value=stayed)
                task._wait_loading_or_template_or_ocr = Mock(return_value=("none", False, ""))
                task._wait_for_template_or_ocr = Mock(return_value=(False, ""))
                task._wait_for_existing_guild_visit = Mock(return_value=False)
                task._return_home_from_guild = Mock(return_value=True)
                task._save_flow_diagnostic = Mock()
                clicks = []
                task._click_reference = task.operate_click = lambda x, y, **_: clicks.append((x, y))
                self.assertFalse(task.run_guild_sign_in())
                self.assertEqual(expected, clicks)
                if not at_home:
                    task.capture_frame.assert_not_called()

    def test_guild_success_needs_a_popup_or_unobscured_member_page(self):
        member = "公告事项 公会商店 进入公会联合战 公会成员 退出 请检查网络连接"
        for home, text, template, visited in (
            (True, "我的小屋 公会 抽抽乐", False, False),
            (False, "", True, False),
            (False, member, True, True),
            (False, member + " 确认", True, False),
            (False, "搜索公会 公会商店 公告事项 申请加入", True, False),
        ):
            with self.subTest(home=home, text=text):
                task = object.__new__(DailyTask)
                task.config = {"loading 出现等待秒数": 0.5}
                task.capture_frame = Mock(return_value=object())
                task.info_set = task.sleep = task.log_info = Mock()
                task._ocr_text = Mock(return_value=text)
                task._read_home_confirmation_signals = Mock(return_value=(home, 3, 255.0, "抽抽乐"))
                task._match = lambda _, spec: MatchResult(
                    0.771 if spec is GUILD_SIGNUP_SUCCESS_TEMPLATE else -1,
                    (0, 0),
                    (1, 1),
                    pixel_score=0.771,
                )
                with patch("src.tasks.task_vision_mixin.monotonic", side_effect=[0.0, 0.1, 10.0]):
                    state, hit, _ = task._wait_loading_or_template_or_ocr(
                        "公会签到",
                        GUILD_SIGNUP_SUCCESS_TEMPLATE,
                        GUILD_SUCCESS_KEYWORDS,
                        name="guild_sign_in_early",
                        reject_template_on_home=True,
                    )
                self.assertEqual(("target", True) if template else ("none", False), (state, hit))
                with patch("src.tasks.DailyTask.monotonic", side_effect=[0, 0, 4]):
                    self.assertEqual(visited, task._wait_for_existing_guild_visit())

    def test_guild_return_retries_only_from_a_recognized_guild_page(self):
        for keywords, backs in (("", 0), ("公告事项 进入公会联合战 公会商店", 3)):
            with self.subTest(keywords=keywords):
                task = object.__new__(DailyTask)
                task.config = {}
                task.capture_frame = Mock(return_value=object())
                task.info_set = task.log_info = task.sleep = Mock()
                task._frame_confirms_home = Mock(return_value=False)
                task._ocr_text = Mock(return_value=keywords)
                task.prepare_home = Mock(side_effect=[False, False, True])
                task._click_reference = Mock()
                task._return_home_from_guild()
                self.assertEqual(
                    [(100, 50)] * backs, [c.args for c in task._click_reference.call_args_list]
                )

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
                task.prepare_home = lambda *_args, **_kwargs: True
                task._frame_confirms_home = lambda *_args, **_kwargs: False
                clicks = []
                task._click_reference = lambda x, y, **_kwargs: clicks.append((x, y))
                ocr_calls = []

                def ocr_text(_frame, _name, **kwargs):
                    ocr_calls.append(kwargs)
                    return title

                task._quick_vision = lambda: SimpleNamespace(ocr_text=ocr_text)

                self.assertIs(expected, DailyTask.run_my_home_sign_in(task))
                # 小屋的返回条比自己更宽，箭头在参考 (172,48)；
                # 旧值 (100,50) 点在返回条左侧空白上，实机点不动。
                self.assertEqual(
                    [(166, 158), MY_HOME_BACK_REFERENCE_POINT] if expected else [(166, 158)], clicks
                )
                self.assertEqual(1 if expected else 3, len(ocr_calls))
                self.assertEqual((0.11, 0.01, 0.25, 0.10), ocr_calls[0]["relative_roi"])

    def test_business_claim_and_popup_close_require_visible_evidence(self):
        claim, close = (0.6, 0.8), (0.5, 0.9)
        for scene, expected, actions in (
            ("unknown", False, []),
            ("stuck_reward", False, [claim] + [close] * 8),
            ("claimed", True, [claim, close]),
            ("lost", False, [claim, claim, (0.43, 0.8)]),
        ):
            with self.subTest(scene=scene):
                frame = np.full((720, 1280, 3), 230, np.uint8)
                popup = self._business_controls()
                reward = SimpleNamespace(
                    name="点击画面即可返回", x=576, y=633.6, width=128, height=28.8
                )

                def screen(actions, now):
                    if scene == "unknown" or (0.43, 0.8) in actions:
                        return frame, [], bool(actions)
                    show_reward = claim in actions and (
                        scene == "stuck_reward"
                        or (scene == "claimed" and now >= 4 and close not in actions)
                    )
                    return frame, [reward] if show_reward else popup, False

                task, _, clicks, refs, _ = self._business_flow_task(screen)
                self.assertEqual(expected, task.run_business_collect())
                self.assertEqual(actions, clicks)
                self.assertEqual([(165, 260)], refs)
                self.assertEqual(int(expected), task._collect_business_guest_stones.call_count)

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
            with self.subTest(
                reward_press=reward_press, delay=delay, guest=guest_name, home=home_ok, moved=moved
            ):
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
                        boxes += [
                            box(text, 400, 500 + i * 25) for i, text in enumerate(reward_text)
                        ]
                    return frame, boxes, False

                task, statuses, clicks, _, _ = self._business_flow_task(screen)
                del task._collect_business_guest_stones
                task._quick_vision = Mock(
                    return_value=SimpleNamespace(ocr_boxes=lambda *_args, **_kwargs: task.ocr())
                )
                task.recover_daily_home.return_value = home_ok
                self.assertEqual(expected, task.run_business_collect())
                self.assertEqual(
                    [(0.6, 0.8), (0.5, 0.9), (0.7, 0.25)] + [(0.15, 0.3)] * presses, clicks
                )
                task.recover_daily_home.assert_called_once()
                if expected:
                    self.assertEqual(
                        "已领取" if reward_press else "无待领取圣石", statuses["常客圣石结果"]
                    )
                    self.assertEqual(
                        "、".join(reward_text) if reward_press else "-", statuses["常客圣石奖励"]
                    )
                else:
                    task._save_flow_diagnostic.assert_called_with("business_collect_failed")

    def test_guest_collection_clears_season_overlay_before_clicking_restaurant(self):
        for width, height in ((1280, 720), (1920, 1080)):
            for mode in (
                "early",
                "late",
                "stuck",
                "title_only",
                "dismiss_only",
                "split",
                "other_page",
            ):
                with self.subTest(size=(width, height), mode=mode):

                    def box(name, x, y):
                        return SimpleNamespace(
                            name=name,
                            x=(x - 0.01) * width,
                            y=(y - 0.01) * height,
                            width=0.02 * width,
                            height=0.02 * height,
                        )

                    frame = np.full((height, width, 3), 230, dtype=np.uint8)
                    popup = self._business_controls(width, height)
                    popup.append(box("立刻前往", 0.7, 0.3))
                    restaurant = [box("格鲁菲餐厅", 0.1, 0.05), box("常客", 0.15, 0.3)]
                    title = box("魔兽追踪者赛季奖励", 0.65, 0.28)
                    dismiss = box("奖励已发放至背包。", 0.7, 0.8)
                    reads, after_close = [0], [0]

                    def screen(actions, _now):
                        if (0.7, 0.3) not in actions:
                            return frame, popup, False
                        reads[0] += 1
                        if (0.15, 0.3) in actions:
                            self.assertGreaterEqual(after_close[0], 4)
                            return frame, [box("火圣石×10", 0.5, 0.5)], False
                        if mode == "late" and reads[0] == 1:
                            return frame, restaurant[:1], False
                        if (0.7, 0.8) in actions and mode != "stuck":
                            after_close[0] += 1
                            if mode == "other_page":
                                return frame, [box("其他页面", 0.5, 0.5)], False
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
                    task._quick_vision = Mock(
                        return_value=SimpleNamespace(ocr_boxes=lambda *_args, **_kwargs: task.ocr())
                    )
                    expected = mode in {"early", "late"}
                    self.assertEqual(expected, task._collect_business_guest_stones())
                    expected_clicks = [(0.7, 0.3)]
                    if mode not in {"title_only", "dismiss_only", "split"}:
                        expected_clicks.append((0.7, 0.8))
                    if expected:
                        expected_clicks.append((0.15, 0.3))
                        self.assertEqual("已领取", statuses["常客圣石结果"])
                    self.assertEqual(expected_clicks, clicks)
                    self.assertLess(clock[0], 18.0)

    def test_guest_recheck_scales_crop_and_click_bounds_and_rejects_ambiguity(self):
        for height, width, roi in (
            (1080, 1920, (154, 205, 288, 173)),
            (720, 1280, (102, 137, 192, 115)),
        ):
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
                        self.assertIs(image, frame),
                        clicks.append(bounds),
                        task.sleep(after_sleep),
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
                        guest = box(
                            "常客", (width * 0.15 - left) * 2 - 20, (height * 0.3 - top) * 2 - 10
                        )
                        return [guest] * (2 if ambiguous else 1)

                    task.ocr = ocr
                    task._quick_vision = lambda: Vision(task)
                    with patch("src.tasks.DailyTask.monotonic", side_effect=lambda: clock[0]):
                        self.assertEqual(not ambiguous, task._collect_business_guest_stones())
                    self.assertTrue(crops)
                    self.assertEqual(1 if ambiguous else 2, len(clicks))
                    if not ambiguous:
                        x, y, w, h = clicks[-1]
                        self.assertAlmostEqual(0.15, (x + w / 2) / width)
                        self.assertAlmostEqual(0.3, (y + h / 2) / height)
        self.assertEqual((0.08, 0.19, 0.23, 0.35), BUSINESS_GUEST_RELATIVE_ROI)

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
        task.run_business_collect = Mock(side_effect=lambda: clock.__setitem__(0, after) or True)
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


if __name__ == "__main__":
    unittest.main()
