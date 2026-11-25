import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.tasks import run_history
from src.tasks.ActivityDailyTask import ACKNOWLEDGED_AP_DAY, ActivityDailyTask
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.equipment_daily import (
    DISMANTLE_GRADE_FILES,
    DISMANTLE_SKIPPED,
    DISMANTLE_STEP,
    REFINE_SKIPPED,
    REFINE_STEP,
    EquipmentScreen,
    dismantle_grade_points,
    equipment_ready_for_season_pass,
)
from src.tasks.EquipmentDailyTask import EquipmentDailyTask
from src.tasks.QuickSuppressionTask import (
    CHAPTER_ONE_POINT,
    CHAPTER_TWO_POINT,
    NEW_CARTRIDGE_NOTICE,
    QUICK_SWITCH_POINT,
    SUPPRESSION_POINTS,
    QuickSuppressionTask,
)
from src.utils.activity_screen import (
    ACTIVITY_DAILY_OWNER,
    BATTLE_BACK_POINT,
    MENU_BACK_POINT,
    MODES,
    QUICK_BATTLE_STEP,
    STAGES,
    ActivityScreen,
)
from src.utils.image_utils import read_template_image

REFINEMENT_RESULT_LABELS = (
    ("321,581", 0.815, 0.053),
    ("2,820,083", 0.903, 0.053),
    ("装备分数●", 0.643, 0.258),
    ("所需材料", 0.75, 0.839),
    ("精炼痕迹", 0.127, 0.932),
)

DISMANTLE_RESULT_LABELS = (
    ("RESULT", 0.50, 0.30),
    ("获得1种", 0.50, 0.37),
    ("点击画面即可返回。", 0.495, 0.915),
)

# OCR centers from PR #23 report RPT-20261121-000627-57FE8241.
EXCLUSIVE_DETAIL_LABELS = (
    ("设置自定义标记", 0.336, 0.449),
    ("专用装备", 0.284, 0.540),
    ("精炼", 0.708, 0.679),
)


def equipment_screen(labels, *, fill=220):
    frame = np.full((1080, 1920, 3), fill, dtype=np.uint8)
    boxes = [
        SimpleNamespace(name=text, x=x * 1920 - 12, y=y * 1080 - 8, width=24, height=16)
        for text, x, y in labels
    ]
    return EquipmentScreen(frame, boxes)


CHALLENGE, NORMAL = MODES
CYCLE = "1.0"
MENU_LABELS = (
    ("普通战斗", 0.352, 0.886),
    ("挑战战斗", 0.450, 0.886),
    ("活动商店", 0.744, 0.886),
    ("帮助", 0.180, 0.044),
    ("行程", 0.264, 0.044),
)


def activity_screen(labels, fill=0):
    frame = np.full((1080, 1920, 3), fill, dtype=np.uint8)
    boxes = [
        SimpleNamespace(name=text, x=x * 1920 - 10, y=y * 1080 - 8, width=20, height=16)
        for text, x, y in labels
    ]
    return ActivityScreen(frame, boxes)


def event_menu(ribbon_offset=0):
    labels = [(text, x, y + ribbon_offset if y > 0.8 else y) for text, x, y in MENU_LABELS]
    menu = activity_screen(labels, fill=255)
    menu.frame[44:55, 165:170] = 70
    return menu


def challenge_list(selected, rows):
    return activity_screen(
        (
            (CHALLENGE, 0.174, 0.038),
            ("活动服装", 0.636, 0.038),
            (f"挑战战斗{selected}", 0.508, 0.122),
            *((str(stage), 0.14, y) for stage, y in rows),
        )
    )


@contextmanager
def isolated_stage_history():
    with tempfile.TemporaryDirectory() as directory:
        store = run_history.RunHistoryStore(Path(directory) / "runs")
        with patch.object(run_history, "default_store", return_value=store):
            yield store


def activity_battle(mode, stage, ap, *, reward=False):
    labels = [
        (mode, .174, .046), ("活动服装", .64, .046),
        (f"{mode}{stage}", .51, .13), (f"{ap}/5", .91, .046),
    ]
    if reward:
        labels.extend((("REWARD", .50, .31), ("角点击画面即可返回。", .49, .916)))
    return activity_screen(labels)


def activity_dialog(mode, stage, runs, cost, free, ap):
    view = activity_screen((
        ("快速战斗", .499, .266), (f"{mode}{stage}", .5, .297),
        ("仅使用免费活动AP", .386, .332), (f"战斗{runs}次奖励", .5, .416),
        ("MAX", .609, .549), (f"战斗{cost}", .541, .751), (f"{ap}/5", .91, .046),
        ("取消", .419, .751),
    ), fill=215)
    if free is not None:
        x = round((.644 if free else .656) * view.width)
        y = round(.351 * view.height)
        view.frame[y-6:y+6, x-7:x+7] = 40
    return view


class EquipmentFlowTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        run_history.set_default_store(run_history.RunHistoryStore(Path(directory.name) / "runs"))
        self.addCleanup(run_history.set_default_store, None)

    def task(self):
        task = object.__new__(EquipmentDailyTask)
        task.config = {}
        task.name = "装备分解与精炼"
        task.info_set = Mock()
        task.log_completion = Mock()
        task.operate_click = Mock()
        task.log_warning = Mock()
        task._grade_templates = {grade: object() for grade in DISMANTLE_GRADE_FILES}
        task.capture_frame = Mock(side_effect=RuntimeError("no frame"))
        task._open_inventory = Mock(return_value=object())
        task._leave_equipment = Mock(return_value=True)
        return task

    def test_dismantle_recognizes_all_three_eligible_grades_and_skips_dim_badges(self):
        frame = np.full((1080, 1920, 3), 65, dtype=np.uint8)
        templates = {
            grade: read_template_image(path) for grade, path in DISMANTLE_GRADE_FILES.items()
        }
        for grade, x, y, lit in (
            ("SR", 821, 690, True),
            ("N", 821, 815, True),
            ("R", 947, 815, True),
            ("SR", 1073, 815, False),
        ):
            template = templates[grade]
            height, width = template.shape[:2]
            left, top = x - width // 2, y - height // 2
            frame[top : top + height, left : left + width] = template if lit else template // 2
        points = dismantle_grade_points(frame, templates)
        self.assertEqual(
            [(0.473, 0.795), (0.407, 0.795), (0.407, 0.680)],
            [(round(x, 3), round(y, 3)) for x, y in points],
        )
        inventory = equipment_screen((("装备100/500", 0.20, 0.05), ("一键分解", 0.89, 0.93)))
        title, cancel = ("分解", .5, .419), ("取消", .455, .589)
        confirm = ("分解", .544, .589)
        one, two = ("共分解1个装备。", .496, .468), ("共分解2个装备。", .496, .49)
        for labels, expected_count in (
            ((title, ("共分解?个装备。", .496, .468), cancel, confirm), None),
            ((title, one, cancel, ("分解", .544, .63)), None),
            ((title, one, two, cancel, confirm), None),
            ((title, two, cancel, confirm), 2),
        ):
            with self.subTest(labels=labels):
                task = self.task()
                selecting = SimpleNamespace(inventory=(100, 500), frame=frame, selection_count=0)
                selected = SimpleNamespace(inventory=(100, 500), frame=frame, selection_count=1)
                dialog = equipment_screen(labels)
                parsed = dialog.dismantle_dialog
                if expected_count is None:
                    self.assertIsNone(parsed)
                else:
                    self.assertEqual(expected_count, parsed[0])
                observations = iter((selecting, selected, dialog))
                task._wait_equipment = Mock(side_effect=lambda predicate, **_: next(
                    (view for view in observations if predicate(view)), None))
                task._read_equipment = Mock(return_value=selecting)
                task._scroll_to_bottom = Mock(return_value=selecting)
                task._grade_points = Mock(return_value=points)
                task._record_step = Mock()
                with (
                    patch(
                        "src.tasks.EquipmentDailyTask.dismantle_card_selected", return_value=True
                    ),
                    patch(
                        "src.tasks.EquipmentDailyTask.dismantle_check_point",
                        return_value=(0.93, 0.93),
                    ),
                ):
                    self.assertFalse(task._dismantle_once(inventory, "cycle"))
                task._record_step.assert_not_called()
                self.assertEqual(3, task.operate_click.call_count)

    def test_refine_once_uses_filter_dialog_and_handles_empty_maxed_and_conflict(self):
        inventory = equipment_screen((("装备766/814", 0.20, 0.05), ("一键分解", 0.89, 0.93)))
        empty_inventory = equipment_screen(
            (("装备766/814", 0.20, 0.05), ("一键分解", 0.89, 0.93)), fill=220
        )
        # Make grid cells dark (empty) while keeping the bottom button light.
        empty_inventory.frame[130:920, 720:1750] = 30
        filter_open = equipment_screen(
            (
                ("装备筛选", 0.50, 0.25),
                ("强化阶段", 0.54, 0.29),
                ("+94", 0.714, 0.327),
                ("强化分数", 0.54, 0.39),
                ("17V", 0.633, 0.436),
                ("保存", 0.633, 0.750),
                ("确认", 0.715, 0.750),
            ),
            fill=220,
        )
        good = equipment_screen(
            (
                *REFINEMENT_RESULT_LABELS,
                ("30", 0.807, 0.839),
                ("精炼80", 0.84, 0.93),
            )
        )
        maxed = equipment_screen(
            (
                *REFINEMENT_RESULT_LABELS,
                ("达到最大强化级别", 0.75, 0.93),
            )
        )
        conflict = equipment_screen(
            (
                *REFINEMENT_RESULT_LABELS[:3],
                REFINEMENT_RESULT_LABELS[-1],
                ("所需材料30", 0.77, 0.839),
                ("50", 0.808, 0.839),
                ("80", 0.822, 0.839),
                ("精炼80", 0.84, 0.93),
            )
        )
        marker, exclusive, button = EXCLUSIVE_DETAIL_LABELS
        for detail_labels, filtered_view, refine_view, expected in (
            ((marker, exclusive, button), inventory, good, True),
            ((marker, ("基本能力+9", 0.284, 0.51), button), inventory, good, True),
            ((marker, exclusive, button), empty_inventory, good, REFINE_SKIPPED),
            ((exclusive, button), inventory, good, False),
            ((marker, ("专用装备", 0.61, 0.54), button), inventory, good, False),
            ((marker, ("基本能力+8", 0.284, 0.51), button), inventory, good, False),
            ((marker, exclusive, button), inventory, maxed, False),
            ((marker, exclusive, button), inventory, conflict, False),
        ):
            with self.subTest(
                detail=detail_labels,
                empty=filtered_view is empty_inventory,
                maxed=refine_view is maxed,
                conflict=refine_view is conflict,
            ):
                task = self.task()
                detail = equipment_screen(detail_labels)
                observations = iter((filter_open, filter_open, filtered_view, detail, refine_view))
                task._wait_equipment = Mock(
                    side_effect=lambda predicate, **_: next(
                        (v for v in observations if predicate(v)), None
                    )
                )
                task._record_step = Mock()
                task._confirm_refinement = Mock(return_value=True)

                def click(*_, name, **kwargs):
                    if name == "精炼一次":
                        task._record_step.assert_called_once_with(
                            REFINE_STEP,
                            "cycle",
                            "pending",
                            {"remaining": [321551, 2820003]},
                            attempt=None,
                        )

                task.operate_click.side_effect = click
                with patch(
                    "src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="cycle"
                ):
                    result = task._refine_once(inventory, "cycle")
                self.assertIs(expected, result)
                actions = [c.kwargs["name"] for c in task.operate_click.call_args_list]
                self.assertNotIn("保存", actions)
                accepted = expected is True
                self.assertEqual(int(accepted), actions.count("精炼一次"))
                self.assertEqual(int(accepted), task._record_step.call_count)
                self.assertEqual(int(accepted), task._confirm_refinement.call_count)

    def test_pass_gate_requires_actual_dismantling_before_refinement_in_same_round(self):
        task = self.task()
        scope = "1.0"
        with patch("src.tasks.equipment_daily.equipment_daily_cycle", return_value=scope):
            task._record_step(REFINE_STEP, scope, "done", {"remaining": [10, 20]}, attempt="a")
            task._record_step(DISMANTLE_STEP, scope, "done", {"inventory": [99, 500]}, attempt="a")
            self.assertFalse(equipment_ready_for_season_pass())
            task._record_step(REFINE_STEP, scope, "done", {"remaining": [10, 20]}, attempt="b")
            self.assertFalse(equipment_ready_for_season_pass())
            task._record_step(DISMANTLE_STEP, scope, "done", {"skipped": True}, attempt="b")
            self.assertFalse(equipment_ready_for_season_pass())
            task._record_step(DISMANTLE_STEP, scope, "done", {"inventory": [99, 500]}, attempt="c")
            task._record_step(REFINE_STEP, scope, "done", {"remaining": [10, 20]}, attempt="c")
            self.assertTrue(equipment_ready_for_season_pass())

    def test_pending_dismantle_requires_result_and_inventory_delta_before_refinement(self):
        task = self.task()
        cycle = "1.0"
        task._open_inventory = EquipmentDailyTask._open_inventory.__get__(task)
        task._dismantle_once = Mock()
        task._refine_once = Mock(return_value=True)
        inventory = equipment_screen((("装备738/814", 0.20, 0.05), ("键分解", 0.889, 0.928)))
        wrong_count = equipment_screen((("装备737/814", .20, .05), ("键分解", .889, .928)))
        for labels, reads, ok, expected_clicks in (
            (DISMANTLE_RESULT_LABELS, [inventory] * 3, True, 1),
            (DISMANTLE_RESULT_LABELS[:2], [inventory] * 3, True, 1),
            (DISMANTLE_RESULT_LABELS, [inventory, wrong_count, wrong_count, inventory], False, 1),
            (DISMANTLE_RESULT_LABELS[1:2], [], False, 0),
            (DISMANTLE_RESULT_LABELS[2:], [], False, 0),
            (DISMANTLE_RESULT_LABELS, [], False, 3),
        ):
            with (
                self.subTest(labels=labels, stable=bool(reads)),
                isolated_stage_history(),
            ):
                task._record_step(DISMANTLE_STEP, cycle, "pending", {"inventory": [739, 814]})
                result = equipment_screen(labels)
                task._read_equipment = Mock(side_effect=[result, inventory])
                observations = iter([result, result, *reads] if reads else [result] * 12)
                task._wait_equipment = Mock(
                    side_effect=lambda predicate, **_: next(
                        (v for v in observations if predicate(v)), None
                    )
                )
                task.operate_click.reset_mock()
                task._refine_once.reset_mock()
                with patch(
                    "src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value=cycle
                ):
                    self.assertEqual(ok, task.run())
                task._dismantle_once.assert_not_called()
                self.assertEqual(int(ok), task._refine_once.call_count)
                if ok:
                    task._refine_once.assert_called_once_with(inventory, cycle, None)
                self.assertEqual(
                    ["关闭分解结果"] * expected_clicks,
                    [c.kwargs["name"] for c in task.operate_click.call_args_list],
                )
                stage = run_history.default_store().stage("EquipmentDailyTask", DISMANTLE_STEP)
                self.assertEqual("done" if ok else "pending", stage["state"])
                self.assertEqual({"inventory": [739, 814]}, stage["evidence"])

    def test_pending_refinement_reconciles_two_balances_before_opening_a_new_round(self):
        from tests.helpers import FakeClock

        evidence = {"remaining": [321581, 2820083]}
        good = equipment_screen(REFINEMENT_RESULT_LABELS)
        missing = equipment_screen(REFINEMENT_RESULT_LABELS[1:])
        wrong = equipment_screen((("321,580", .815, .053), *REFINEMENT_RESULT_LABELS[1:]))
        for cycle, views, expected in (
            ("1.0", [good, wrong, missing, good], True), ("2.0", [wrong], False),
        ):
            with self.subTest(cycle=cycle):
                task = self.task()
                task._record_step(DISMANTLE_STEP, "1.0", "done")
                task._record_step(REFINE_STEP, "1.0", "pending", evidence)
                with patch.object(BaseBD2Task, "recover_daily_home") as leave:
                    self.assertFalse(task.recover_daily_home())
                    leave.assert_not_called()
                task._refine_once = Mock()
                task._read_equipment = Mock(
                    side_effect=iter(views) if expected else None, return_value=wrong)
                task.ocr = Mock(return_value=[
                    SimpleNamespace(name=text, x=x*1920-12, y=y*1080-8, width=24, height=16)
                    for text, x, y in REFINEMENT_RESULT_LABELS[:2]
                ])
                clock = FakeClock()
                task.sleep = clock.sleep
                with (
                    patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value=cycle),
                    patch("src.tasks.EquipmentDailyTask.monotonic", clock),
                ):
                    self.assertEqual(expected, task.run())
                record = run_history.default_store().stage("EquipmentDailyTask", REFINE_STEP)
                self.assertEqual(("done" if expected else "pending", evidence),
                                 (record["state"], record["evidence"]))
                if expected:
                    self.assertEqual(4, task._read_equipment.call_count)
                    task.ocr.assert_called_once()
                    self.assertIs(missing.frame, task.ocr.call_args.kwargs["frame"])
                else:
                    self.assertGreaterEqual(task._read_equipment.call_count, 2)
                    task.ocr.assert_not_called()
                task._open_inventory.assert_not_called()
                task._refine_once.assert_not_called()
                task.operate_click.assert_not_called()

    def test_equipment_round_resumes_only_the_unfinished_step(self):
        for case in ("completed_round", "no_eligible_equipment", "legacy_done"):
            with self.subTest(case=case), isolated_stage_history() as store:
                task = self.task()
                task._dismantle_once = Mock(
                    return_value=DISMANTLE_SKIPPED if case == "no_eligible_equipment" else True)
                task._refine_once = Mock(side_effect=[True, False, True])
                with patch(
                    "src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="1.0"
                ):
                    if case == "legacy_done":
                        task.config["_仅执行未完成步骤"] = True
                        task._record_step(DISMANTLE_STEP, "1.0", "done")
                        task._record_step(REFINE_STEP, "1.0", "done")
                        self.assertTrue(task.run())
                        task._open_inventory.assert_not_called()
                        task._dismantle_once.assert_not_called()
                        task._refine_once.assert_not_called()
                    elif case == "no_eligible_equipment":
                        task._refine_once.side_effect = [False, True]
                        self.assertFalse(task.run())
                        self.assertTrue(task.run())
                        task._dismantle_once.assert_called_once()
                        self.assertEqual(2, task._refine_once.call_count)
                        record = store.stage("EquipmentDailyTask", DISMANTLE_STEP)
                        self.assertEqual(("done", "no_eligible_equipment"),
                                         (record["state"], record["evidence"]["skipped"]))
                    else:
                        self.assertTrue(task.run())
                        self.assertFalse(task.run())
                        self.assertTrue(task.run())
                        first, second = [c.args[2] for c in task._dismantle_once.call_args_list]
                        self.assertNotEqual(first, second)
                        self.assertEqual([first, second, second],
                                         [c.args[2] for c in task._refine_once.call_args_list])
                task.operate_click.assert_not_called()


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
            home is not None and frame is home.frame,
            0,
            255.0,
            "",
        )
        task._home_scan = lambda frame, signals=None: (signals[0], False)
        task._activity_return_control = Mock(
            side_effect=lambda frame: view_by_frame[id(frame)].home_return_control
        )
        return task

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

    def test_stage_five_verification_crop_isolates_the_digit_at_client_scale(self):
        task = self.task()
        digit_ocr = Mock(return_value=[[("5", 0.97)] * 10])
        task._executor = SimpleNamespace(ocr_lib=lambda: SimpleNamespace(ocr=digit_ocr))
        for width, height, x, y, w, h in (
            (1920, 1080, 263, 522, 19, 41),
            (1280, 720, 175, 348, 13, 28),
        ):
            with self.subTest(size=(width, height)):
                frame = np.zeros((height, width, 3), dtype=np.uint8)
                frame[y : y + h, x : x + w] = 255
                view = ActivityScreen(frame, [])
                point = task._read_stage_digit(view, {10: (0.14, 1037 / 1080)}, 5)
                self.assertIsNotNone(point)
                verify = digit_ocr.call_args.args[0][1]
                self.assertEqual((h, w), verify.shape[:2])
                self.assertTrue((verify == 255).all())
        task = self.task()
        view = challenge_list(10, ((10, 0.961),))
        digit_ocr = Mock(return_value=[[("5", 0.97), ("15", 0.99)] * 5])
        task._executor = SimpleNamespace(ocr_lib=lambda: SimpleNamespace(ocr=digit_ocr))
        self.assertIsNone(task._read_stage_digit(view, view.stage_rows(), 5))
        self.assertIsNone(task._read_stage_digit(view, view.stage_rows(), 15))
        digit_ocr.assert_called_once()

    def test_falls_back_to_normal_and_skips_if_all_six_stages_are_disabled(self):
        for eligible in ((CHALLENGE, 10), (NORMAL, 5), None):
            with self.subTest(eligible=eligible):
                task = self.task()
                pages = self.setup_menu(task, eligible=eligible)
                self.assertTrue(task.run())
                self.assertEqual(
                    (
                        [(CHALLENGE, 15), (CHALLENGE, 10)]
                        if eligible == (CHALLENGE, 10)
                        else [(mode, stage) for mode in MODES for stage in STAGES]
                    ),
                    [call.args[0] for call in task._quick_stage.call_args_list],
                )
                if eligible:
                    task._fight.assert_called_once_with(
                        pages[eligible], (0.58, 0.92), (5, 5), CYCLE
                    )
                else:
                    task._fight.assert_not_called()
                task.operate_click.assert_not_called()

    def test_shared_home_return_uses_the_dialog_cancel_and_not_the_battle_button(self):
        dialog = activity_dialog(NORMAL, 15, 5, 5, True, 5)
        battle = challenge_list(15, ((15, 0.958),))
        battle.frame[38:52, 157:164] = 220
        menu = event_menu()
        home = activity_screen(())
        task = self.home_task((dialog, dialog, battle, battle, menu, menu, home), home)
        self.assertTrue(task.auto_return_main_home())
        self.assertEqual(
            [dialog.dialog_cancel, BATTLE_BACK_POINT, MENU_BACK_POINT],
            [call.args for call in task.operate_click.call_args_list],
        )
        run_history.default_store().save_stage(
            ACTIVITY_DAILY_OWNER, QUICK_BATTLE_STEP, "pending", CYCLE
        )
        menu = event_menu()
        task = self.home_task((menu,))
        self.assertFalse(task.auto_return_main_home(notify_failure=False))
        task.operate_click.assert_not_called()
        task.back.assert_not_called()

    def test_max_and_free_ap_are_verified_before_a_single_submission(self):
        for mode, runs, cost, free, ap, accepted in (
            (NORMAL, 5, 5, True, 5, True), (CHALLENGE, 5, 5, True, 5, True),
            (NORMAL, 5, 1, True, 5, False), (NORMAL, 1, 1, True, 5, False),
            (NORMAL, 5, 5, False, 5, False), (NORMAL, 5, 5, None, 5, False),
            (NORMAL, 5, 5, True, 4, False),
        ):
            with self.subTest(mode=mode, runs=runs, cost=cost, free=free, ap=ap):
                with isolated_stage_history() as store:
                    task = self.task()
                    stage = 10 if mode == CHALLENGE else 15
                    dialog = activity_dialog(mode, stage, runs, cost, free, ap)
                    self.assertEqual((mode, stage), dialog.dialog)
                    parsed = dialog.submission()
                    if cost == runs:
                        self.assertEqual(runs, parsed[0])
                    else:
                        self.assertIsNone(parsed)
                    task._wait_activity = Mock(
                        side_effect=lambda pred: dialog if pred(dialog) else None)
                    task._confirm_spend = Mock(return_value=False)
                    evidence = dict(mode=mode, stage=stage, before=5, capacity=5, amount=5)
                    submit_name = "快速战斗 5 次"
                    def click(*point, name, **kwargs):
                        if name == submit_name:
                            pending = run_history.RunHistoryStore(store.path).stage(
                                ACTIVITY_DAILY_OWNER, QUICK_BATTLE_STEP)
                            actual = pending["state"], pending["scope"], pending["evidence"]
                            self.assertEqual(("pending", CYCLE, evidence), actual)
                            self.assertTrue(kwargs["require_fresh_target"])
                            np.testing.assert_allclose(point, (.541, .751))
                    task.operate_click.side_effect = click
                    battle = activity_battle(mode, stage, 5)
                    self.assertFalse(task._fight(battle, (.58, .92), (5, 5), CYCLE))
                    actions = [c.kwargs["name"] for c in task.operate_click.call_args_list]
                    expected = ["打开活动快速战斗"]
                    if free is False:
                        expected.append("仅使用免费活动AP")
                    if free is True:
                        expected.append("活动快速战斗 MAX")
                    self.assertEqual(expected + ([submit_name] if accepted else []), actions)
                    self.assertEqual(int(accepted), task._confirm_spend.call_count)
                    if not accepted:
                        self.assertEqual({}, run_history.read_stage(task, QUICK_BATTLE_STEP))
                        continue
                    task = self.task()
                    reward = activity_battle(mode, stage, 0, reward=True)
                    spent = activity_battle(mode, stage, 0)
                    views = ([reward, reward, reward, spent, spent] if mode == CHALLENGE
                             else [battle, spent, battle, spent, spent])
                    task._read_activity = Mock(side_effect=views)
                    task.sleep = Mock()
                    self.assertTrue(task.run())
                    self.assertEqual(5, task._read_activity.call_count)
                    if mode == CHALLENGE:
                        task.operate_click.assert_called_once_with(
                            .49, .916, name="关闭活动战斗结果", after_sleep=1.2)
                    else:
                        task.operate_click.assert_not_called()
                    record = run_history.read_stage(task, QUICK_BATTLE_STEP)
                    self.assertEqual(("done", evidence), (record["state"], record["evidence"]))

    def test_only_the_acknowledged_older_cycle_can_be_archived(self):
        task = self.task()
        prior = datetime(2026, 11, 22, 4, tzinfo=run_history.BEIJING_TZ).timestamp()
        today = prior + 86400
        evidence = {"mode": NORMAL, "stage": 15, "before": 5, "capacity": 5, "amount": 5}
        run_history.save_stage(
            task, QUICK_BATTLE_STEP, "pending", scope=str(prior), evidence=evidence
        )
        self.setup_menu(task, eligible=(CHALLENGE, 15))
        with patch.object(
            run_history, "day_start_ts", side_effect=lambda stamp=None: stamp or today
        ):
            for date in ("", "2026-11-21", "2026-11-23"):
                task.config[ACKNOWLEDGED_AP_DAY] = date
                self.assertFalse(task.waits_for_login)
                self.assertFalse(task.run())
                task._read_activity.assert_not_called()
                task._fight.assert_not_called()
                self.assertEqual(
                    evidence, run_history.read_stage(task, QUICK_BATTLE_STEP)["evidence"]
                )
            task.config[ACKNOWLEDGED_AP_DAY] = "2026-11-22"
            self.assertTrue(task.waits_for_login)
            menu, home = event_menu(), activity_screen(())
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
            run_history.save_stage(
                task, QUICK_BATTLE_STEP, "pending", scope=str(today), evidence=evidence
            )
            task.config[ACKNOWLEDGED_AP_DAY] = "2026-11-23"
            task._read_activity.return_value = SimpleNamespace(
                battle=(NORMAL, 15), result=(False, None)
            )
            self.assertFalse(task.waits_for_login)
            task._confirm_spend = Mock(return_value=False)
            self.assertFalse(task.run())
            self.assertEqual("pending", run_history.read_stage(task, QUICK_BATTLE_STEP)["state"])
            task._fight.assert_called_once()


class QuickSuppressionTaskTest(unittest.TestCase):
    def test_slot_cycle_waits_for_each_confirmed_chapter_transition(self):
        for slot in ("4号位", "5号位"):
            for loaded in (True, False):
                with self.subTest(slot=slot, loaded=loaded):
                    task = object.__new__(QuickSuppressionTask)
                    task.info_set = task.log_info = Mock()
                    task.sleep = Mock()
                    task.capture_frame = Mock(return_value=np.zeros((10, 10, 3), np.uint8))
                    task._ocr_text = Mock(return_value=NEW_CARTRIDGE_NOTICE)
                    task._wait_for_loading_cycle = Mock(return_value=loaded)
                    task.operate_click = Mock()
                    self.assertEqual(loaded, task._run_cycle(slot))
                    first = [SUPPRESSION_POINTS[slot], QUICK_SWITCH_POINT, CHAPTER_TWO_POINT]
                    second = [SUPPRESSION_POINTS[slot], QUICK_SWITCH_POINT, CHAPTER_ONE_POINT]
                    self.assertEqual(
                        first + (second if loaded else []),
                        [c.args for c in task.operate_click.call_args_list],
                    )
                    self.assertEqual(2 if loaded else 0, task._ocr_text.call_count)
                    self.assertEqual([call(2.0)] * (2 if loaded else 0), task.sleep.call_args_list)


if __name__ == "__main__":
    unittest.main()
