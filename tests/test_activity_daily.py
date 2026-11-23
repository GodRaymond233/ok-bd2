import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from ok.task.exceptions import TaskDisabledException

from src.tasks import run_history
from src.tasks.ActivityDailyTask import ACKNOWLEDGED_AP_DAY, ActivityDailyTask
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.DailyBatchTask import DAILY_BATCH_CHILDREN
from src.tasks.scheduler import policy_for
from src.utils.activity_screen import (
    ACTIVITY_DAILY_NAME,
    ACTIVITY_DAILY_OWNER,
    BATTLE_BACK_POINT,
    MENU_BACK_POINT,
    MODES,
    QUICK_BATTLE_STEP,
    STAGES,
    ActivityScreen,
)

CHALLENGE, NORMAL = MODES
CYCLE = "1.0"
MENU_LABELS = (
    ("普通战斗", 0.352, 0.886),
    ("挑战战斗", 0.450, 0.886),
    ("活动商店", 0.744, 0.886),
    ("帮助", 0.180, 0.044),
    ("行程", 0.264, 0.044),
)


def screen(labels, fill=0):
    frame = np.full((1080, 1920, 3), fill, dtype=np.uint8)
    boxes = [
        SimpleNamespace(name=text, x=x * 1920 - 10, y=y * 1080 - 8, width=20, height=16)
        for text, x, y in labels
    ]
    return ActivityScreen(frame, boxes)


def event_menu(ribbon_offset=0):
    labels = [(text, x, y + ribbon_offset if y > 0.8 else y) for text, x, y in MENU_LABELS]
    menu = screen(labels, fill=255)
    menu.frame[44:55, 165:170] = 70
    return menu


def challenge_list(selected, rows):
    return screen(
        (
            (CHALLENGE, 0.174, 0.038),
            ("活动服装", 0.636, 0.038),
            (f"挑战战斗{selected}", 0.508, 0.122),
            *((str(stage), 0.14, y) for stage, y in rows),
        )
    )


class EventRecognitionTest(unittest.TestCase):
    def test_stage_page_accepts_the_help_mark_joined_to_the_mode_title(self):
        for mode in MODES:
            for suffix in ("", "？", "?"):
                with self.subTest(mode=mode, suffix=suffix):
                    view = screen((
                        (mode + suffix, 0.174, 0.073),
                        ("活动服装", 0.636, 0.073),
                        (f"{mode}10", 0.508, 0.155),
                    ))
                    self.assertEqual((mode, 10), view.stage_page)

        unknown = screen((
            ("挑战战斗说明", 0.174, 0.073),
            ("活动服装", 0.636, 0.073),
            ("挑战战斗10", 0.508, 0.155),
        ))
        self.assertIsNone(unknown.stage_page)

    def test_rewards_footer_accepts_only_the_overlapping_role_label(self):
        for text in ("点击画面即可返回。", "角点击画面即可返回。", "角色点击画面即可返回"):
            with self.subTest(text=text):
                view = screen((("REWARD", 0.50, 0.31), (text, 0.49, 0.916)))
                self.assertEqual((True, (0.49, 0.916)), view.result)

        unknown = screen((
            ("REWARD", 0.50, 0.31), ("设置点击画面即可返回", 0.49, 0.916),
        ))
        self.assertEqual((True, None), unknown.result)
        no_reward = screen((("角点击画面即可返回。", 0.49, 0.916),))
        self.assertEqual((True, None), no_reward.result)

    def test_dialog_requires_both_reported_costs_to_match(self):
        labels = (
            ("快速战斗", 0.499, 0.266),
            ("普通战斗15", 0.500, 0.297),
            ("仅使用免费活动AP", 0.386, 0.332),
            ("战斗5次奖励", 0.500, 0.416),
            ("MAX", 0.609, 0.549),
        )
        matched = screen((*labels, ("战斗5", 0.541, 0.751)), fill=215)
        self.assertEqual((NORMAL, 15), matched.dialog)
        self.assertEqual(5, matched.submission()[0])
        mismatched = screen((*labels, ("战斗1", 0.541, 0.751)), fill=215)
        self.assertIsNone(mismatched.submission())


class ActivityFlowTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        run_history.set_default_store(run_history.RunHistoryStore(Path(directory.name) / "runs"))
        self.addCleanup(run_history.set_default_store, None)
        clock = patch("src.tasks.ActivityDailyTask.run_history.day_start_ts", return_value=1.0)
        clock.start()
        self.addCleanup(clock.stop)

    def task(self):
        task = object.__new__(ActivityDailyTask)
        task.config = {}
        task.info_set = Mock()
        task.log_warning = Mock()
        task.log_completion = Mock()
        task.operate_click = Mock()
        task.recover_daily_home = Mock(return_value=True)
        return task

    def test_activity_is_a_daily_opt_in_batch_child(self):
        (child,) = (item for item in DAILY_BATCH_CHILDREN if item.task_class is ActivityDailyTask)
        self.assertEqual(ACTIVITY_DAILY_NAME, child.config_key)
        self.assertFalse(child.default_enabled)
        self.assertEqual("daily", policy_for(child.config_key).anchor)

    def test_does_not_click_mode_again_after_a_late_transition(self):
        task = self.task()
        menu = event_menu()
        battle = SimpleNamespace(stage_page=(CHALLENGE, 15), menu_back=None)
        reads = iter(((menu,), (menu,), (battle,)))

        def wait(predicate, **_kwargs):
            frames = next(reads)
            return next((view for view in frames if predicate(view)), None)

        task._wait_activity = Mock(side_effect=wait)
        self.assertIs(battle, task._open_mode(menu, CHALLENGE))
        self.assertEqual(1, task.operate_click.call_count)

    def test_searches_down_and_drags_when_wheel_leaves_the_same_rows_visible(self):
        task = self.task()
        top = challenge_list(10, ((1, 0.128), (5, 0.498), (10, 0.958)))
        bottom = challenge_list(10, ((10, 0.498), (14, 0.866), (15, 0.958)))
        selected = challenge_list(15, ((10, 0.498), (14, 0.866), (15, 0.958)))
        actions = Mock()
        task.scroll_client, task.drag_client, task.operate_click = (
            actions.scroll,
            actions.drag,
            actions.click,
        )
        task._region = Mock(return_value=screen(()))
        task._read_activity = Mock(side_effect=[top, bottom])
        task._wait_activity = Mock(
            side_effect=lambda predicate, **_: selected if predicate(selected) else None
        )

        self.assertIs(selected, task._select_stage(top, CHALLENGE, 15))
        self.assertEqual(["scroll", "drag", "click"], [call[0] for call in actions.mock_calls])
        self.assertEqual(-1, actions.scroll.call_args.args[1])
        drag_from, drag_to = actions.drag.call_args.args
        self.assertGreater(drag_from[1], drag_to[1])
        self.assertEqual(bottom.stage_rows()[15], actions.click.call_args.args)

    def test_searches_up_for_5_and_rejects_a_different_mode_after_tapping(self):
        task = self.task()
        bottom = challenge_list(10, ((6, 0.128), (10, 0.498), (15, 0.958)))
        top = challenge_list(10, ((1, 0.128), (5, 0.498), (10, 0.958)))
        wrong = SimpleNamespace(stage_page=(NORMAL, 5))
        task._region = Mock(return_value=screen(()))
        task.scroll_client = Mock()
        task._read_activity = Mock(side_effect=[top, wrong])
        task._wait_activity = Mock(
            side_effect=lambda predicate, **_: wrong if predicate(wrong) else None
        )

        self.assertIsNone(task._select_stage(bottom, CHALLENGE, 5))
        task.scroll_client.assert_called_once_with((0.155, 0.53), 1, count=3, after_sleep=0.55)
        task.operate_click.assert_called_once()
        self.assertEqual(top.stage_rows()[5], task.operate_click.call_args.args)

    def test_checks_visible_5_with_single_line_ocr_when_the_detector_only_finds_10(self):
        for matched_header in (True, False):
            with self.subTest(matched_header=matched_header):
                task = self.task()
                initial = challenge_list(10, ((10, 0.961),))
                selected = challenge_list(5, ((5, 0.504),))
                next_view = selected if matched_header else SimpleNamespace(stage_page=(NORMAL, 5))
                digit_ocr = Mock(return_value=[[('5', .97), ('5》', .96)] + [('', .0)] * 8])
                task._executor = SimpleNamespace(ocr_lib=lambda: SimpleNamespace(ocr=digit_ocr))
                task._region = Mock(return_value=initial)
                task._wait_activity = Mock(
                    side_effect=lambda predicate, **_: next_view if predicate(next_view) else None,
                )
                task._read_activity = Mock(return_value=next_view)
                task.scroll_client = Mock()

                self.assertIs(
                    selected if matched_header else None,
                    task._select_stage(initial, CHALLENGE, 5),
                )
                task.operate_click.assert_called_once()
                task._wait_activity.assert_called_once()
                task.scroll_client.assert_not_called()
                x, y = task.operate_click.call_args.args
                self.assertAlmostEqual(.142, x)
                self.assertAlmostEqual(.504, y, delta=.002)
                self.assertTrue(task.operate_click.call_args.kwargs['require_fresh_target'])
                digit_ocr.assert_called_once()
                self.assertEqual(dict(det=False, cls=False), digit_ocr.call_args.kwargs)

    def test_rejects_unconfirmed_digits_and_does_not_read_an_offscreen_row(self):
        task = self.task()
        view = challenge_list(10, ((10, .961),))
        digit_ocr = Mock(return_value=[[('5', .97), ('15', .99)] * 5])
        task._executor = SimpleNamespace(ocr_lib=lambda: SimpleNamespace(ocr=digit_ocr))
        self.assertIsNone(task._read_stage_digit(view, view.stage_rows(), 5))
        self.assertIsNone(task._read_stage_digit(view, view.stage_rows(), 15))
        digit_ocr.assert_called_once()

    def test_stage_five_verification_crop_isolates_the_digit_at_client_scale(self):
        task = self.task()
        digit_ocr = Mock(return_value=[[("5", .97)] * 10])
        task._executor = SimpleNamespace(ocr_lib=lambda: SimpleNamespace(ocr=digit_ocr))
        for width, height, x, y, w, h in (
            (1920, 1080, 263, 522, 19, 41),
            (1280, 720, 175, 348, 13, 28),
        ):
            with self.subTest(size=(width, height)):
                frame = np.zeros((height, width, 3), dtype=np.uint8)
                frame[y:y + h, x:x + w] = 255
                view = ActivityScreen(frame, [])
                point = task._read_stage_digit(view, {10: (.14, 1037 / 1080)}, 5)
                self.assertIsNotNone(point)
                verify = digit_ocr.call_args.args[0][1]
                self.assertEqual((h, w), verify.shape[:2])
                self.assertTrue((verify == 255).all())

    def home_task(self, views, home=None):
        task = object.__new__(BaseBD2Task)
        task.config = {}
        task.info_set = Mock()
        task.log_info = Mock()
        task.log_warning = Mock()
        task.sleep = Mock()
        task.back = Mock()
        task.operate_click = Mock()
        view_by_frame = {id(view.frame): view for view in views}
        task.capture_frame = Mock(side_effect=[view.frame for view in views])
        task._read_home_confirmation_signals = lambda frame, _: (
            home is not None and frame is home.frame, 0, 255.0, "",
        )
        task._home_scan = lambda frame, signals=None: (signals[0], False)
        task._activity_return_control = Mock(
            side_effect=lambda frame: view_by_frame[id(frame)].home_return_control
        )
        return task

    def test_shared_home_return_uses_the_dialog_cancel_and_not_the_battle_button(self):
        dialog = screen(
            (
                (NORMAL, 0.174, 0.038),
                ("活动服装", 0.636, 0.038),
                ("普通战斗15", 0.508, 0.122),
                ("快速战斗", 0.499, 0.266),
                ("普通战斗15", 0.500, 0.297),
                ("仅使用免费活动AP", 0.386, 0.332),
                ("取消", 0.419, 0.751),
                ("战斗5", 0.541, 0.751),
            ),
            fill=215,
        )
        battle = challenge_list(15, ((15, 0.958),))
        battle.frame[38:52, 157:164] = 220
        menu = event_menu()
        home = screen(())
        task = self.home_task((dialog, dialog, battle, battle, menu, menu, home), home)
        self.assertTrue(task.auto_return_main_home())
        self.assertEqual(
            [dialog.dialog_cancel, BATTLE_BACK_POINT, MENU_BACK_POINT],
            [call.args for call in task.operate_click.call_args_list],
        )

    def test_shared_home_return_dismisses_a_result_only_with_an_activity_header(self):
        result_labels = (
            ("RESULT", 0.50, 0.30),
            ("获得1种", 0.50, 0.36),
            ("角点击画面即可返回。", 0.49, 0.916),
        )
        self.assertIsNone(screen(result_labels).home_return_control)
        result = screen(((NORMAL, 0.174, 0.038), ("活动服装", 0.636, 0.038), *result_labels))
        menu = event_menu()
        home = screen(())
        task = self.home_task((result, result, menu, menu, home), home)
        self.assertTrue(task.auto_return_main_home())
        self.assertEqual(
            [result.result[1], MENU_BACK_POINT],
            [call.args for call in task.operate_click.call_args_list],
        )

    def test_shared_home_return_skips_the_click_if_home_appears_during_confirmation(
        self,
    ):
        menu = event_menu()
        home = screen(())
        task = self.home_task((menu, home, home), home)
        self.assertTrue(task.auto_return_main_home())
        task.operate_click.assert_not_called()

    def test_shared_home_return_propagates_a_user_stop_during_the_activity_click(self):
        menu = event_menu()
        task = self.home_task((menu, menu))
        task.operate_click.side_effect = TaskDisabledException
        with self.assertRaises(TaskDisabledException):
            task.auto_return_main_home()
        task.back.assert_not_called()
        task.log_warning.assert_not_called()

    def test_shared_home_return_preserves_pending_activity(self):
        run_history.default_store().save_stage(
            ACTIVITY_DAILY_OWNER, QUICK_BATTLE_STEP, "pending", CYCLE
        )
        menu = event_menu()
        task = self.home_task((menu,))
        self.assertFalse(task.auto_return_main_home(notify_failure=False))
        task.operate_click.assert_not_called()
        task.back.assert_not_called()

    def setup_menu(self, task, eligible=None):
        menu = object()
        pages = {}
        for mode in MODES:
            for stage in STAGES:
                target = (mode, stage)
                pages[target] = SimpleNamespace(
                    stage_page=target,
                    quick_button=Mock(return_value=(target == eligible, (0.58, 0.92))),
                )
        task._read_activity = Mock(return_value=menu)
        task._open_menu = Mock(return_value=menu)
        task._open_mode = Mock(side_effect=lambda _, mode: pages[mode, 15])
        task._back_to_menu = Mock(return_value=menu)
        task._ap = Mock(return_value=(5, 5))
        task._select_stage = Mock(side_effect=lambda _, mode, stage: pages[mode, stage])

        def quick_stage(target):
            page = pages[target]
            return page, page.quick_button()

        task._quick_stage = Mock(side_effect=quick_stage)
        task._fight = Mock(return_value=True)
        return pages

    def test_uses_first_available_challenge_stage(self):
        task = self.task()
        pages = self.setup_menu(task, eligible=(CHALLENGE, 10))
        self.assertTrue(task.run())
        self.assertEqual(
            [(CHALLENGE, 15), (CHALLENGE, 10)],
            [call.args[0] for call in task._quick_stage.call_args_list],
        )
        task._fight.assert_called_once_with(pages[CHALLENGE, 10], (0.58, 0.92), (5, 5), CYCLE)
        task._back_to_menu.assert_not_called()

    def test_falls_back_to_normal_and_skips_if_all_six_stages_are_disabled(self):
        for eligible in ((NORMAL, 5), None):
            with self.subTest(eligible=eligible):
                task = self.task()
                pages = self.setup_menu(task, eligible=eligible)
                self.assertTrue(task.run())
                self.assertEqual(
                    [(mode, stage) for mode in MODES for stage in STAGES],
                    [call.args[0] for call in task._quick_stage.call_args_list],
                )
                if eligible:
                    task._fight.assert_called_once_with(
                        pages[eligible], (0.58, 0.92), (5, 5), CYCLE
                    )
                else:
                    task._fight.assert_not_called()
                task.operate_click.assert_not_called()

    def test_unknown_stage_state_stops_before_any_battle(self):
        task = self.task()
        self.setup_menu(task, eligible=(NORMAL, 15))
        task._quick_stage.return_value = None
        task._quick_stage.side_effect = None
        self.assertFalse(task.run())
        task._fight.assert_not_called()
        task._back_to_menu.assert_not_called()

    def test_quick_stage_requires_two_matching_states_and_can_use_cropped_ocr(self):
        task = self.task()
        target = (CHALLENGE, 15)
        grey = (False, (0.58, 0.92))
        lit = (True, (0.58, 0.92))
        views = [
            SimpleNamespace(stage_page=target, quick_button=Mock(return_value=state))
            for state in (grey, lit, None)
        ]
        task._region = Mock(return_value=SimpleNamespace(quick_button=Mock(return_value=lit)))
        task._wait_activity = Mock(
            side_effect=lambda predicate, **_kwargs: next(
                (view for view in views if predicate(view)), None
            )
        )
        self.assertEqual((views[-1], lit), task._quick_stage(target))
        task._region.assert_called_once()

    def test_max_and_free_ap_are_verified_before_a_single_submission(self):
        for displayed, free, ap, accepted in (
            (5, True, (5, 5), True),
            (1, True, (5, 5), False),
            (5, False, (5, 5), False),
            (5, True, (4, 5), False),
        ):
            with self.subTest(displayed=displayed, free=free, ap=ap):
                task = self.task()
                target = (NORMAL, 15)
                dialog = SimpleNamespace(
                    dialog=target,
                    free_only=Mock(return_value=free),
                    max_button=Mock(return_value=(0.61, 0.55)),
                    submission=Mock(return_value=(displayed, (0.54, 0.75))),
                )
                task._battle_dialog = Mock(return_value=dialog)
                task._ap = Mock(return_value=ap)
                task._confirm_spend = Mock(return_value=False)
                task._wait_activity = Mock(
                    side_effect=lambda predicate, dialog=dialog: (
                        dialog if predicate(dialog) else None
                    )
                )

                def on_click(*point, task=task, **_kwargs):
                    if point == (0.54, 0.75):
                        record = run_history.read_stage(task, QUICK_BATTLE_STEP)
                        self.assertEqual("pending", record["state"])
                        self.assertEqual(5, record["evidence"]["amount"])

                task.operate_click.side_effect = on_click
                self.assertFalse(
                    task._fight(SimpleNamespace(stage_page=target), (0.58, 0.92), (5, 5), CYCLE)
                )
                self.assertEqual(2 if accepted else 1, task.operate_click.call_count)
                self.assertEqual(int(accepted), task._confirm_spend.call_count)

    def test_pending_submission_only_reconciles_and_never_clicks_battle_again(self):
        task = self.task()
        evidence = {
            "mode": NORMAL,
            "stage": 15,
            "before": 5,
            "capacity": 5,
            "amount": 5,
        }
        run_history.save_stage(task, QUICK_BATTLE_STEP, "pending", scope=CYCLE, evidence=evidence)
        task._read_activity = Mock(
            return_value=SimpleNamespace(battle=(NORMAL, 15), result=(False, None))
        )
        task._confirm_spend = Mock(side_effect=[False, True])
        self.assertFalse(task.run())
        self.assertEqual("pending", run_history.read_stage(task, QUICK_BATTLE_STEP)["state"])
        self.assertTrue(task.run())
        self.assertEqual("done", run_history.read_stage(task, QUICK_BATTLE_STEP)["state"])
        task.operate_click.assert_not_called()

    def test_pending_challenge_reward_clicks_footer_then_reconciles_ap(self):
        task = self.task()
        evidence = {
            "mode": CHALLENGE, "stage": 10, "before": 5, "capacity": 5, "amount": 5,
        }
        run_history.save_stage(
            task, QUICK_BATTLE_STEP, "pending", scope=CYCLE, evidence=evidence
        )
        labels = (
            (CHALLENGE, 0.174, 0.046), ("活动服装", 0.64, 0.046),
            ("挑战战斗10", 0.51, 0.13), ("0/5", 0.91, 0.046),
        )
        reward = screen((
            *labels, ("REWARD", 0.50, 0.31), ("角点击画面即可返回。", 0.49, 0.916),
        ))
        battle = screen(labels)
        task._read_activity = Mock(side_effect=[reward, reward, reward, battle, battle])
        task.sleep = Mock()

        self.assertTrue(task.run())
        self.assertEqual(5, task._read_activity.call_count)
        task.operate_click.assert_called_once_with(
            0.49, 0.916, name="关闭活动战斗结果", after_sleep=1.2,
        )
        self.assertEqual("done", run_history.read_stage(task, QUICK_BATTLE_STEP)["state"])

    def test_only_the_acknowledged_older_cycle_can_be_archived(self):
        task = self.task()
        prior = datetime(2026, 11, 22, 4, tzinfo=run_history.BEIJING_TZ).timestamp()
        today = prior + 86400
        evidence = {"mode": NORMAL, "stage": 15, "before": 5, "capacity": 5, "amount": 5}
        run_history.save_stage(task, QUICK_BATTLE_STEP, "pending",
                               scope=str(prior), evidence=evidence)
        self.setup_menu(task, eligible=(CHALLENGE, 15))
        with patch.object(run_history, "day_start_ts",
                          side_effect=lambda stamp=None: stamp or today):
            for date in ("", "2026-11-21", "2026-11-23"):
                task.config[ACKNOWLEDGED_AP_DAY] = date
                self.assertFalse(task.waits_for_login)
                self.assertFalse(task.run())
                task._read_activity.assert_not_called()
                task._fight.assert_not_called()
                self.assertEqual(evidence,
                                 run_history.read_stage(task, QUICK_BATTLE_STEP)["evidence"])
            task.config[ACKNOWLEDGED_AP_DAY] = "2026-11-22"
            self.assertTrue(task.waits_for_login)
            menu, home = event_menu(), screen(())
            login = self.home_task((menu, menu, home), home)
            login._executor = SimpleNamespace(get_task_by_class=lambda _: task)
            self.assertTrue(login.auto_return_main_home())
            self.assertEqual("pending", run_history.read_stage(task, QUICK_BATTLE_STEP)["state"])
            self.assertTrue(task.run())
            task._fight.assert_called_once()
            self.assertEqual(str(today), task._fight.call_args.args[-1])
            self.assertEqual({}, run_history.read_stage(task, QUICK_BATTLE_STEP))
            store = run_history.default_store()
            self.assertEqual(evidence, store._retired_stages[-1]["record"]["evidence"])
            run_history.save_stage(task, QUICK_BATTLE_STEP, "pending", scope=str(today),
                                   evidence=evidence)
            task.config[ACKNOWLEDGED_AP_DAY] = "2026-11-23"
            task._read_activity.return_value = SimpleNamespace(
                battle=(NORMAL, 15), result=(False, None))
            self.assertFalse(task.waits_for_login)
            task._confirm_spend = Mock(return_value=False)
            self.assertFalse(task.run())
            self.assertEqual("pending", run_history.read_stage(task, QUICK_BATTLE_STEP)["state"])
            task._fight.assert_called_once()

    def test_spend_confirmation_requires_two_consecutive_ap_reads(self):
        task = self.task()
        evidence = {
            "mode": NORMAL,
            "stage": 15,
            "before": 5,
            "capacity": 5,
            "amount": 5,
        }
        battle = SimpleNamespace(stage_page=(NORMAL, 15), result=(False, None))
        task._read_activity = Mock(return_value=battle)
        task._ap = Mock(side_effect=[(0, 5), (1, 5), (0, 5), (0, 5)])
        task.sleep = Mock()
        self.assertTrue(task._confirm_spend(evidence))
        self.assertEqual(4, task._read_activity.call_count)


if __name__ == "__main__":
    unittest.main()
