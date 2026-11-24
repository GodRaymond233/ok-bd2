import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks import run_history
from src.tasks.DailyBatchTask import DAILY_BATCH_CHILDREN
from src.tasks.equipment_daily import (
    DISMANTLE_GRADE_FILES,
    DISMANTLE_SKIPPED,
    DISMANTLE_STEP,
    REFINE_STEP,
    EquipmentScreen,
    RefinementConflict,
    dismantle_grade_points,
    equipment_daily_done,
    equipment_ready_for_season_pass,
)
from src.tasks.EquipmentDailyTask import EquipmentDailyTask
from src.tasks.MissionRewardTask import MissionRewardTask
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


def screen(labels, *, fill=220):
    frame = np.full((1080, 1920, 3), fill, dtype=np.uint8)
    boxes = [
        SimpleNamespace(name=text, x=x * 1920 - 12, y=y * 1080 - 8, width=24, height=16)
        for text, x, y in labels
    ]
    return EquipmentScreen(frame, boxes)


class EquipmentRecognitionTest(unittest.TestCase):
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

    def test_dismantle_confirmation_requires_readable_count_and_aligned_buttons(self):
        title = ("分解", 0.5, 0.419)
        count = ("共分解1个装备。", 0.496, 0.468)
        cancel = ("取消", 0.455, 0.589)
        confirm = ("分解", 0.544, 0.589)
        examples = (
            (title, ("共分解?个装备。", 0.496, 0.468), cancel, confirm),
            (title, count, cancel, ("分解", 0.544, 0.63)),
            (title, count, ("共分解2个装备。", 0.496, 0.49), cancel, confirm),
        )
        for labels in examples:
            with self.subTest(labels=labels):
                self.assertIsNone(screen(labels).dismantle_dialog)

    def test_refine_entry_recognizes_standard_and_exclusive_details(self):
        marker, _, button = EXCLUSIVE_DETAIL_LABELS
        standard = (marker, ("基本能力+9", 0.284, 0.51), button)
        for labels in (standard, EXCLUSIVE_DETAIL_LABELS):
            with self.subTest(labels=labels):
                x, y = screen(labels).detail_refine
                self.assertAlmostEqual(button[1], x)
                self.assertAlmostEqual(button[2], y)
                self.assertIsNone(screen(labels, fill=70).detail_refine)

    def test_refine_entry_requires_exclusive_label_in_detail_and_custom_marker(self):
        marker, exclusive, button = EXCLUSIVE_DETAIL_LABELS
        for labels in (
            (exclusive, button),
            (marker, ("专用装备", 0.61, 0.54), button),
            (marker, ("基本能力+8", 0.284, 0.51), button),
        ):
            with self.subTest(labels=labels):
                self.assertIsNone(screen(labels).detail_refine)

    def test_refinement_reads_joined_or_separate_material_cost(self):
        base = (*REFINEMENT_RESULT_LABELS[:3], REFINEMENT_RESULT_LABELS[-1],
                ("精炼80", 0.84, 0.93))
        separate = (("所需材料", 0.75, 0.839), ("30", 0.807, 0.839))
        joined = (("所需材料 30", 0.77, 0.839),)
        for material in (separate, joined):
            with self.subTest(material=material):
                self.assertEqual((30, 80), screen((*base, *material)).refine_once[0])

        uncertain = screen((*base, *joined, ("80", 0.807, 0.839)))
        self.assertIsNone(uncertain.refine_once)

    def test_grid_excludes_24_point_gear_and_unmarked_numbers(self):
        labels = (("24", 0.65, 0.20), ("23", 0.71, 0.20), ("17", 0.77, 0.20), ("12", 0.83, 0.20))
        inventory = screen(labels)
        for _, x, y in labels[:3]:
            px, py = round(x * 1920), round(y * 1080)
            inventory.frame[py - 3 : py + 3, px - 3 : px + 3] = (80, 160, 220)
        self.assertEqual([17, 23], [score for score, _ in inventory.scored_gear()])


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

    def test_batch_lists_equipment_before_rewards_and_requires_opt_in(self):
        tasks = [child.task_class for child in DAILY_BATCH_CHILDREN]
        equipment_index = tasks.index(EquipmentDailyTask)
        equipment = DAILY_BATCH_CHILDREN[equipment_index]
        self.assertFalse(equipment.default_enabled)
        self.assertLess(equipment_index, tasks.index(MissionRewardTask))

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

    def test_runs_dismantle_before_refine_and_resumes_without_repeating_it(self):
        task = self.task()
        steps = []
        task._dismantle_once = lambda *_: steps.append(DISMANTLE_STEP) or True
        task._refine_once = lambda *_: steps.append(REFINE_STEP) or False
        self.assertFalse(task.run())
        self.assertFalse(equipment_daily_done())

        task._refine_once = lambda *_: steps.append(REFINE_STEP) or True
        self.assertTrue(task.run())
        self.assertTrue(task.run())
        self.assertTrue(equipment_daily_done())
        self.assertEqual(
            [DISMANTLE_STEP, REFINE_STEP, REFINE_STEP, DISMANTLE_STEP, REFINE_STEP], steps
        )

    def test_previous_rounds_refinement_does_not_complete_a_new_partial_round(self):
        task = self.task()
        task._dismantle_once = Mock(return_value=True)
        task._refine_once = Mock(side_effect=[True, False, True])

        self.assertTrue(task.run())
        self.assertFalse(task.run())
        self.assertTrue(task.run())

        first, second = [call.args[2] for call in task._dismantle_once.call_args_list]
        self.assertNotEqual(first, second)
        self.assertEqual(
            [first, second, second],
            [call.args[2] for call in task._refine_once.call_args_list],
        )

    def test_only_incomplete_mode_preserves_completed_legacy_stages(self):
        task = self.task()
        task.config["_仅执行未完成步骤"] = True
        task._record_step(DISMANTLE_STEP, "1.0", "done")
        task._record_step(REFINE_STEP, "1.0", "done")
        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="1.0"):
            self.assertTrue(task.run())
        task._open_inventory.assert_not_called()
        task.operate_click.assert_not_called()

    def test_unconfirmed_older_refinement_blocks_a_new_dismantle(self):
        task = self.task()
        evidence = {"remaining": [321581, 2820083]}
        task._record_step(DISMANTLE_STEP, "1.0", "done")
        task._record_step(REFINE_STEP, "1.0", "pending", evidence)
        task._confirm_refinement = Mock(return_value=False)
        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="2.0"):
            self.assertFalse(task.run())
        task._confirm_refinement.assert_called_once_with(evidence["remaining"])
        task._open_inventory.assert_not_called()
        task.operate_click.assert_not_called()

    def test_skipped_dismantle_continues_refinement_and_resumes_without_rescanning(self):
        task = self.task()
        task._dismantle_once = Mock(return_value=DISMANTLE_SKIPPED)
        task._refine_once = Mock(side_effect=[False, True])
        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="1.0"):
            self.assertFalse(task.run())
            self.assertTrue(task.run())
        task._dismantle_once.assert_called_once()
        self.assertEqual(2, task._refine_once.call_count)
        skip = run_history.default_store().stage("EquipmentDailyTask", DISMANTLE_STEP)
        self.assertEqual("done", skip["state"])
        self.assertEqual("no_eligible_equipment", skip["evidence"]["skipped"])
        task.operate_click.assert_not_called()

    def test_dismantle_result_stops_clicking_when_the_page_never_closes(self):
        title, count, dismiss = DISMANTLE_RESULT_LABELS
        for labels, point in (
            ((("ESULT", *title[1:]), count, dismiss), dismiss[1:]),
            ((title, count), (0.30, 0.15)),
            ((dismiss,), None),
        ):
            with self.subTest(labels=labels):
                task = self.task()
                result = screen(labels)
                task._wait_equipment = Mock(
                    side_effect=lambda predicate, **_kwargs: next(
                        (view for view in (result, result) if predicate(view)), None
                    )
                )
                self.assertIsNone(task._close_dismantle_result(expected_inventory=(738, 814)))
                self.assertEqual(3 if point else 0, task.operate_click.call_count)
                for call in task.operate_click.call_args_list:
                    self.assertEqual("关闭分解结果", call.kwargs["name"])
                    self.assertEqual(point, call.args)

    def test_dismantle_result_requires_two_consecutive_frames_of_the_expected_count(self):
        task = self.task()
        entry = ("键分解", 0.889, 0.928)
        expected = screen((("装备738/814", 0.20, 0.05), entry))
        unexpected = screen((("装备737/814", 0.20, 0.05), entry))
        observations = iter((expected, unexpected, unexpected, expected))
        task._wait_equipment = Mock(
            side_effect=lambda predicate, **_kwargs: next(
                (view for view in observations if predicate(view)), None
            )
        )
        self.assertIsNone(task._close_dismantle_result(expected_inventory=(738, 814)))
        task.operate_click.assert_not_called()

    def test_pending_dismantle_recovers_a_leftover_result_before_refinement(self):
        task = self.task()
        cycle = "1.0"
        task._open_inventory = EquipmentDailyTask._open_inventory.__get__(task)
        task._record_step(DISMANTLE_STEP, cycle, "pending", {"inventory": [739, 814]})
        task._dismantle_once = Mock()
        task._refine_once = Mock(return_value=True)
        inventory = screen((("装备738/814", 0.20, 0.05), ("键分解", 0.889, 0.928)))
        result = screen(DISMANTLE_RESULT_LABELS)
        task._read_equipment = Mock(side_effect=[result, inventory])
        observations = iter((result, result, inventory, inventory, inventory))
        task._wait_equipment = Mock(
            side_effect=lambda predicate, **_kwargs: next(
                (view for view in observations if predicate(view)), None
            )
        )
        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value=cycle):
            self.assertTrue(task.run())
        task._dismantle_once.assert_not_called()
        task._refine_once.assert_called_once_with(inventory, cycle, None)
        self.assertEqual("关闭分解结果", task.operate_click.call_args.kwargs["name"])
        self.assertEqual(1, task.operate_click.call_count)
        stage = run_history.default_store().stage("EquipmentDailyTask", DISMANTLE_STEP)
        self.assertEqual("done", stage["state"])
        self.assertEqual({"inventory": [739, 814]}, stage["evidence"])

    def test_dismantle_search_scrolls_up_past_empty_screens_in_the_full_inventory(self):
        task = self.task()
        inventory = (100, 500)
        empty = np.full((1080, 1920, 3), 20, dtype=np.uint8)
        empty_view = SimpleNamespace(frame=empty, inventory=inventory, selection_count=0)
        occupied_view = SimpleNamespace(frame=empty + 70, inventory=inventory, selection_count=0)
        selected = SimpleNamespace(
            frame=occupied_view.frame, inventory=inventory, selection_count=1
        )
        task._scroll_to_bottom = Mock(return_value=empty_view)
        task._grade_points = Mock(side_effect=[[], [], [], [(0.55, 0.72)]])
        task._read_equipment = Mock(side_effect=[empty_view, empty_view, occupied_view])
        task._wait_equipment = Mock(return_value=selected)
        task.scroll_client = Mock()

        with patch("src.tasks.EquipmentDailyTask.dismantle_card_selected", return_value=True):
            self.assertIs(selected, task._scan_selection_tab(empty_view, inventory))
        self.assertEqual([1, 1, 1], [call.args[1] for call in task.scroll_client.call_args_list])
        self.assertEqual((0.55, 0.72), task.operate_click.call_args.args)

    def test_unselectable_candidate_is_bypassed_but_wrong_gold_outline_stops(self):
        task = self.task()
        view = SimpleNamespace(
            inventory=(6, 500),
            selection_count=0,
            frame=np.full((1080, 1920, 3), 90, dtype=np.uint8),
        )
        selected = SimpleNamespace(inventory=view.inventory, selection_count=1, frame=view.frame)
        task._scroll_to_bottom = Mock(return_value=view)
        task._read_equipment = Mock(return_value=view)
        task._grade_points = Mock(return_value=[(0.65, 0.50), (0.55, 0.50)])
        task._wait_equipment = Mock(side_effect=[None, selected])
        with patch(
            "src.tasks.EquipmentDailyTask.dismantle_card_selected", return_value=False
        ) as outline:
            self.assertIsNone(task._scan_selection_tab(view, view.inventory))
        self.assertEqual(
            [(0.65, 0.50), (0.55, 0.50)], [call.args for call in task.operate_click.call_args_list]
        )
        outline.assert_called_once_with(selected.frame, (0.55, 0.50))

    def test_dismantle_entry_retries_only_when_the_same_equipment_bag_is_still_open(self):
        title = ("装备100/500", 0.20, 0.05)
        entry = ("键分解", 0.889, 0.928)
        prompt = ("请选择需要分解的装备。", 0.45, 0.92)
        inventory = screen((title, entry))
        selecting = screen((title, prompt, ("0/2000", 0.39, 0.96)))
        for labels, retry in (
            ((title, entry), True),
            ((title, entry, prompt), False),
            ((("装备99/500", 0.20, 0.05), entry), False),
        ):
            with self.subTest(labels=labels):
                task = self.task()
                task._read_equipment = Mock(return_value=screen(labels))
                task._wait_equipment = Mock(side_effect=[None, selecting])

                actual = task._enter_dismantle_selection(inventory)
                self.assertIs(actual, selecting if retry else None)
                self.assertEqual(2 if retry else 1, task.operate_click.call_count)

    def test_does_not_submit_dismantle_when_dialog_count_is_not_one(self):
        task = self.task()
        inventory = screen((("装备100/500", 0.20, 0.05), ("一键分解", 0.89, 0.93)))
        selecting = SimpleNamespace(
            inventory=(100, 500),
            frame=np.zeros((1080, 1920, 3), dtype=np.uint8),
            selection_count=0,
        )
        selected = SimpleNamespace(inventory=(100, 500), frame=object(), selection_count=1)
        dialog = SimpleNamespace(dismantle_dialog=(2, (0.53, 0.64)))
        task._wait_equipment = Mock(side_effect=[selecting, selected, dialog])
        task._read_equipment = Mock(return_value=selecting)
        task._scroll_to_bottom = Mock(return_value=selecting)
        task._grade_points = Mock(return_value=[(0.54, 0.79)])
        task._record_step = Mock()
        with (
            patch("src.tasks.EquipmentDailyTask.dismantle_card_selected", return_value=True),
            patch("src.tasks.EquipmentDailyTask.dismantle_check_point", return_value=(0.93, 0.93)),
        ):
            self.assertFalse(task._dismantle_once(inventory, "cycle"))
        task._record_step.assert_not_called()
        self.assertEqual(3, task.operate_click.call_count)

    def test_exclusive_gear_refine_records_expected_debit_before_clicking(self):
        task = self.task()
        task._record_step = Mock()
        inventory = SimpleNamespace(scored_gear=lambda: [(19, (0.6, 0.3))])
        detail = screen(EXCLUSIVE_DETAIL_LABELS)
        refine = SimpleNamespace(
            detail_refine=None,
            refine_once=((30, 80), (0.84, 0.93)),
            refine_balances=(100, 300),
        )
        task._wait_equipment = Mock(
            side_effect=lambda predicate: next(
                (view for view in (detail, refine) if predicate(view)),
                None,
            )
        )
        task._read_refinement_badges = Mock(return_value=(19, 19))
        task._confirm_refinement = Mock(return_value=True)

        def click(*_args, name, **_kwargs):
            if name == "精炼一次":
                task._record_step.assert_called_once_with(
                    REFINE_STEP,
                    "cycle",
                    "pending",
                    {"remaining": [70, 220]},
                    attempt=None,
                )

        task.operate_click.side_effect = click
        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="cycle"):
            self.assertTrue(task._refine_once(inventory, "cycle"))
        self.assertEqual(
            ["查看带分数的装备", "打开精炼装备分数", "精炼一次"],
            [call.kwargs["name"] for call in task.operate_click.call_args_list],
        )
        task._confirm_refinement.assert_called_once_with([70, 220])

    def test_refinement_does_not_submit_an_out_of_range_or_unverified_candidate(self):
        for badges in ((5, 5), (24, 24), (23, 24), (17, None), (None, None)):
            with self.subTest(badges=badges):
                task = self.task()
                task._record_step = Mock()
                task._read_refinement_badges = Mock(return_value=badges)
                inventory = SimpleNamespace(scored_gear=lambda: [(23, (0.6, 0.3))])
                detail = SimpleNamespace(detail_refine=(0.71, 0.68), refine_once=None)
                refine = SimpleNamespace(
                    detail_refine=None,
                    refine_once=((30, 80), (0.84, 0.93)),
                )
                task._wait_equipment = Mock(
                    side_effect=lambda predicate, views=(detail, refine): next(
                        (view for view in views if predicate(view)),
                        None,
                    )
                )

                self.assertFalse(task._refine_once(inventory, "cycle"))

                task._record_step.assert_not_called()
                self.assertEqual(2, task.operate_click.call_count)

    def test_refinement_conflict_ends_attempt_before_the_next_clean_frame(self):
        task = self.task()
        task._record_step = Mock()
        inventory = SimpleNamespace(scored_gear=lambda: [(22, (0.6, 0.3))])
        view = screen((*REFINEMENT_RESULT_LABELS, ("30", 0.807, 0.839),
                       ("精炼80", 0.84, 0.93), ("22", 0.902, 0.258), ("22", 0.772, 0.745)))
        detail = screen(EXCLUSIVE_DETAIL_LABELS)
        task._wait_equipment = Mock(side_effect=lambda predicate: next(
            (v for v in (detail, view, view) if predicate(v)), None))
        def box(value):
            return SimpleNamespace(name=value, x=40, y=40, width=20, height=20)
        task.ocr = Mock(side_effect=[[box("22"), box("24")], [box("22")], [], []])
        task._confirm_refinement = Mock(return_value=True)
        task.info_set = Mock()
        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="cycle"):
            self.assertFalse(task._refine_once(inventory, "cycle"))
        task._record_step.assert_not_called()
        self.assertEqual(2, task.operate_click.call_count)
        self.assertIn("24", str(task.info_set.call_args_list))

    def test_refinement_uses_aligned_frame_scores_when_a_badge_crop_is_unreadable(self):
        task = self.task()
        score_22 = SimpleNamespace(name="22", x=40, y=40, width=20, height=20)
        score_21 = SimpleNamespace(name="21", x=40, y=40, width=20, height=20)
        for upper_x, upper_crop, expected in (
            (0.902, [], (22, 22)),
            (0.902, [score_21], None),
            (0.850, [], (None, 22)),
        ):
            with self.subTest(upper_x=upper_x, upper_crop=upper_crop):
                view = screen((("22", upper_x, 0.258), ("22", 0.772, 0.745)))
                task.ocr = Mock(side_effect=[upper_crop, [score_22]])
                if expected is None:
                    with self.assertRaises(RefinementConflict):
                        task._read_refinement_badges(view, 22)
                else:
                    self.assertEqual(expected, task._read_refinement_badges(view, 22))
        task.ocr = Mock(return_value=[])
        view = screen((("22", 0.902, 0.258), ("22", 0.772, 0.745)))
        self.assertEqual((22, 22), task._read_refinement_badges(view, 22))
        with self.assertRaises(RefinementConflict):
            task._read_refinement_badges(view, 21)

    def test_refinement_rechecks_missing_amounts_and_requires_two_matching_frames(self):
        task = self.task()
        result = screen(REFINEMENT_RESULT_LABELS)
        missing = screen(REFINEMENT_RESULT_LABELS[1:])
        wrong = screen((("321,580", 0.815, 0.053), *REFINEMENT_RESULT_LABELS[1:]))
        task._read_equipment = Mock(side_effect=[result, wrong, missing, result])
        task.sleep = Mock()
        task.ocr = Mock(
            return_value=[
                SimpleNamespace(
                    name="321,581", x=0.815 * 1920 - 12, y=0.053 * 1080 - 8, width=24, height=16
                ),
                SimpleNamespace(
                    name="2,820,083", x=0.903 * 1920 - 12, y=0.053 * 1080 - 8, width=24, height=16
                ),
            ]
        )

        self.assertTrue(task._confirm_refinement([321581, 2820083]))

        self.assertEqual(4, task._read_equipment.call_count)
        task.ocr.assert_called_once()
        self.assertIs(missing.frame, task.ocr.call_args.kwargs["frame"])
        task.operate_click.assert_not_called()

    def test_pending_refinement_reconciles_existing_balance_without_spending_again(self):
        task = self.task()
        cycle = "1.0"
        task._record_step(DISMANTLE_STEP, cycle, "done")
        evidence = {"remaining": [321581, 2820083]}
        task._record_step(REFINE_STEP, cycle, "pending", evidence)
        task._refine_once = Mock()
        task._read_equipment = Mock(return_value=screen(REFINEMENT_RESULT_LABELS))
        task.sleep = Mock()
        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value=cycle):
            self.assertTrue(task.run())

        record = run_history.default_store().stage("EquipmentDailyTask", REFINE_STEP)
        self.assertEqual(("done", evidence), (record["state"], record["evidence"]))
        task._open_inventory.assert_not_called()
        task._refine_once.assert_not_called()
        task.operate_click.assert_not_called()

    def test_end_of_task_preserves_forge_while_refinement_debit_is_pending(self):
        task = self.task()
        task._record_step(REFINE_STEP, "1.0", "pending", {"remaining": [321581, 2820083]})
        with patch("src.tasks.BaseBD2Task.BaseBD2Task.recover_daily_home") as recover:
            self.assertFalse(task.recover_daily_home())
        recover.assert_not_called()

    def test_home_cleanup_failure_preserves_confirmed_equipment_result(self):
        task = self.task()
        task._leave_equipment = Mock(return_value=False)
        task._dismantle_once = Mock(return_value=True)
        task._refine_once = Mock(return_value=True)

        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="1.0"):
            self.assertTrue(task.run())

        store = run_history.default_store()
        for step in (DISMANTLE_STEP, REFINE_STEP):
            self.assertEqual("done", store.stage("EquipmentDailyTask", step)["state"])
