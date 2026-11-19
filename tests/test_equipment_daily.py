import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks import run_history
from src.tasks.DailyBatchTask import DAILY_BATCH_CHILDREN
from src.tasks.equipment_daily import (
    BACKPACK_BACK_POINT,
    CATEGORY_TAB_POINTS,
    DISMANTLE_GRADE_FILES,
    DISMANTLE_SKIPPED,
    DISMANTLE_STEP,
    GRID_CELL_ORIGIN,
    GRID_CELL_SIZE,
    REFINE_STEP,
    REFINEMENT_BACK_POINT,
    SELECTION_BACK_POINT,
    EquipmentScreen,
    dismantle_grade_points,
    equipment_daily_done,
    equipment_ready_for_season_pass,
    grid_blank_rows,
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

    def test_backpack_counts_as_open_on_any_category_tab(self):
        consumables = screen((("消耗品90/500", 0.20, 0.05),))
        self.assertTrue(consumables.bag_open)
        self.assertIsNone(consumables.inventory)
        self.assertTrue(screen((("装备28/500", 0.20, 0.05),)).bag_open)
        gear = screen((("装备100/500（ +", 0.20, 0.05),))
        self.assertTrue(gear.bag_open)
        self.assertEqual((100, 500), gear.inventory)
        self.assertFalse(screen((("背包", 0.20, 0.05),)).bag_open)

    def test_backpack_accepts_title_and_count_in_separate_ocr_boxes(self):
        title = ("消耗品", 0.171, 0.045)
        count = ("188/500", 0.219, 0.050)
        consumables = screen((title, count))
        self.assertTrue(consumables.bag_open)
        self.assertIsNone(consumables.inventory)
        gear = screen((("装备", 0.166, 0.045), ("100/500+", 0.210, 0.050)))
        self.assertEqual((100, 500), gear.inventory)
        self.assertFalse(screen((title, ("188/500", 0.219, 0.088))).bag_open)
        self.assertFalse(screen((count,)).bag_open)

    def test_dismantle_confirmation_reads_the_exact_count(self):
        for title_y, count_y, button_y in ((0.344, 0.394, 0.639), (0.419, 0.468, 0.589)):
            for count in (1, 2, 11):
                with self.subTest(button_y=button_y, count=count):
                    result = screen(
                        (
                            ("分解", 0.5, title_y),
                            (f"共分解{count}个装备。", 0.496, count_y),
                            ("取消", 0.455, button_y),
                            ("分解", 0.544, button_y),
                        )
                    ).dismantle_dialog
                    self.assertIsNotNone(result)
                    read_count, (x, y) = result
                    self.assertEqual(count, read_count)
                    self.assertAlmostEqual(0.544, x)
                    self.assertAlmostEqual(button_y, y)

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

    def test_grid_blank_rows_spot_empty_slots_but_not_equipment_cards(self):
        frame = np.full((1080, 1920, 3), 20, dtype=np.uint8)
        self.assertEqual([True] * 6, grid_blank_rows(frame))
        left = int((GRID_CELL_ORIGIN[0] + GRID_CELL_SIZE[0] * 0.4) * 1920)
        top = int((GRID_CELL_ORIGIN[1] + GRID_CELL_SIZE[1] * 2.4) * 1080)
        frame[top : top + 60, left : left + 60] = 200
        self.assertEqual([True, True, False, True, True, True], grid_blank_rows(frame))

    def test_selection_page_recognizes_dismantle_or_enhance_prompt(self):
        for prompt in ("请选择需要分解的装备。", "请选择需要强化的装备。"):
            self.assertIsNotNone(screen(((prompt, 0.372, 0.92),)).selection_page)
        self.assertIsNone(screen((("分享", 0.372, 0.92),)).selection_page)

    def test_refine_uses_the_single_button_and_detects_its_cost(self):
        labels = (
            ("精炼装备分数", 0.182, 0.044),
            ("194,542", 0.814, 0.052),
            ("2,572,068", 0.903, 0.052),
            ("装备分数●", 0.643, 0.258),
            ("所需材料", 0.75, 0.839),
            ("30", 0.806, 0.839),
            ("连续精炼", 0.676, 0.929),
            ("精炼80", 0.839, 0.93),
        )
        result = screen(labels)
        self.assertEqual((194542, 2572068), result.refine_balances)
        costs, point = result.refine_once
        self.assertEqual((30, 80), costs)
        self.assertAlmostEqual(0.839, point[0])
        self.assertIsNone(screen(labels[:-1]).refine_once)
        self.assertIsNone(screen(labels, fill=70).refine_once)

    def test_grid_excludes_24_point_gear_and_unmarked_numbers(self):
        labels = (("24", 0.65, 0.20), ("23", 0.71, 0.20), ("17", 0.77, 0.20), ("12", 0.83, 0.20))
        inventory = screen(labels)
        for _, x, y in labels[:3]:
            px, py = round(x * 1920), round(y * 1080)
            inventory.frame[py - 3 : py + 3, px - 3 : px + 3] = (80, 160, 220)
        self.assertEqual([17, 23], [score for score, _ in inventory.scored_gear()])

    def test_refine_result_reads_both_balances_without_a_header(self):
        result = screen(REFINEMENT_RESULT_LABELS)
        self.assertTrue(result.refinement_page)
        self.assertEqual((321581, 2820083), result.refine_balances)
        self.assertFalse(screen(REFINEMENT_RESULT_LABELS[:-1]).refinement_page)
        self.assertIsNone(screen(REFINEMENT_RESULT_LABELS[1:]).refine_balances)


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
        equipment, reward = DAILY_BATCH_CHILDREN[-2:]
        self.assertIs(equipment.task_class, EquipmentDailyTask)
        self.assertFalse(equipment.default_enabled)
        self.assertIs(reward.task_class, MissionRewardTask)

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

    def test_manual_run_starts_a_new_round_after_completed_legacy_stages(self):
        task = self.task()
        task._record_step(DISMANTLE_STEP, "1.0", "done", {"inventory": [107, 500]})
        task._record_step(REFINE_STEP, "1.0", "done", {"remaining": [321581, 2820083]})
        task._dismantle_once = Mock(return_value=True)
        task._refine_once = Mock(return_value=True)

        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="1.0"):
            self.assertTrue(task.run())

        attempt = task._dismantle_once.call_args.args[2]
        self.assertTrue(attempt)
        task._refine_once.assert_called_once_with(task._open_inventory.return_value, "1.0", attempt)

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

    def test_failed_dismantle_does_not_run_refinement(self):
        task = self.task()
        task._dismantle_once = Mock(return_value=False)
        task._refine_once = Mock()
        self.assertFalse(task.run())
        task._refine_once.assert_not_called()

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

    def test_dismantling_scans_the_current_list_without_touching_category_tabs(self):
        task = self.task()
        inventory = SimpleNamespace(inventory=(100, 500), dismantle_entry=(0.89, 0.93))
        selecting = SimpleNamespace(
            inventory=inventory.inventory,
            frame=np.zeros((1080, 1920, 3), dtype=np.uint8),
            selection_count=0,
        )
        selected = SimpleNamespace(
            inventory=inventory.inventory,
            frame=object(),
            selection_count=1,
        )
        dialog = SimpleNamespace(dismantle_dialog=(1, (0.53, 0.64)))
        result = SimpleNamespace(dismantle_result=(0.50, 0.92))
        updated_inventory = SimpleNamespace(inventory=(99, 500), dismantle_entry=(0.89, 0.93))
        task._wait_equipment = Mock(
            side_effect=[selecting, selected, dialog, result, updated_inventory],
        )
        task._read_equipment = Mock(return_value=selecting)
        # 选择页进来就是全量列表，点页签会被当成取消筛选，所以只扫一次当前列表。
        task._scroll_to_bottom = Mock(return_value=selecting)
        task._grade_points = Mock(return_value=[(0.599, 0.621)])
        task._record_step = Mock()

        with (
            patch("src.tasks.EquipmentDailyTask.dismantle_card_selected", return_value=True),
            patch("src.tasks.EquipmentDailyTask.dismantle_check_point", return_value=(0.93, 0.93)),
            patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="cycle"),
        ):
            self.assertTrue(task._dismantle_once(inventory, "cycle"))

        names = [call.kwargs.get("name") for call in task.operate_click.call_args_list]
        self.assertNotIn("切换分解分类", names)
        task._grade_points.assert_called_once()
        selection = next(
            call
            for call in task.operate_click.call_args_list
            if call.kwargs.get("name") == "选择一件非 UR 装备"
        )
        self.assertEqual((0.599, 0.621), tuple(round(value, 3) for value in selection.args))
        task._record_step.assert_called_once_with(
            DISMANTLE_STEP,
            "cycle",
            "pending",
            {"inventory": [100, 500]},
            attempt=None,
        )

    def test_leftover_dismantle_selection_page_is_reused(self):
        task = self.task()
        task._click_top_left_back = Mock()
        selecting = SimpleNamespace(
            inventory=(28, 500),
            frame=object(),
            selection_count=0,
            selection_page=(0.372, 0.92),
            dismantle_result=None,
            active_category=None,
        )
        task._read_equipment = Mock(return_value=selecting)
        self.assertIs(selecting, EquipmentDailyTask._open_inventory(task))
        task._click_top_left_back.assert_not_called()

    def test_leaving_a_foreign_selection_page_falls_back_to_the_fixed_back_button(self):
        task = self.task()
        selection = SimpleNamespace(frame=object(), selection_page=(0.372, 0.92))
        backpack = SimpleNamespace(dismantle_entry=(0.89, 0.93))
        task._click_top_left_back = Mock(return_value=False)
        task._wait_equipment = Mock(return_value=backpack)
        self.assertIs(backpack, task._leave_selection_page(selection))
        self.assertEqual(SELECTION_BACK_POINT, task.operate_click.call_args.args)
        self.assertEqual("退出选择页", task.operate_click.call_args.kwargs["name"])

    def test_leftover_selection_page_is_scanned_without_clicking_the_entry(self):
        task = self.task()
        selecting = SimpleNamespace(
            inventory=(28, 500),
            frame=object(),
            selection_count=0,
            selection_page=(0.372, 0.92),
        )
        selected = SimpleNamespace(inventory=(28, 500), frame=object(), selection_count=1)
        dialog = SimpleNamespace(dismantle_dialog=(1, (0.53, 0.64)))
        result = SimpleNamespace(dismantle_result=(0.50, 0.92))
        updated = SimpleNamespace(inventory=(27, 500), dismantle_entry=(0.89, 0.93))
        task._wait_equipment = Mock(side_effect=[dialog, result, updated])
        task._read_equipment = Mock(return_value=selecting)
        task._scan_selection_tab = Mock(return_value=selected)
        task._record_step = Mock()
        with (
            patch("src.tasks.EquipmentDailyTask.dismantle_check_point", return_value=(0.93, 0.93)),
            patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="cycle"),
        ):
            self.assertTrue(task._dismantle_once(selecting, "cycle"))
        names = [call.kwargs.get("name") for call in task.operate_click.call_args_list]
        self.assertNotIn("进入装备分解选择", names)
        self.assertIn("分解一件装备", names)

    def test_category_tabs_are_not_clicked_while_the_bag_shows_other_tabs(self):
        task = self.task()
        view = SimpleNamespace(inventory=None, frame=object())
        task._gear_tab = Mock(return_value=None)
        self.assertIsNone(task._open_all_equipment(view))
        task.operate_click.assert_not_called()

    def test_full_inventory_is_used_even_when_no_n_quality_card_is_visible(self):
        task = self.task()
        inventory = screen((("装备100/500", 0.20, 0.052), ("一键分解", 0.888, 0.928)))
        task._read_equipment = Mock(return_value=inventory)
        task._grade_points = Mock(return_value=[])

        self.assertIs(inventory, EquipmentDailyTask._open_inventory(task))
        task._grade_points.assert_not_called()
        task.operate_click.assert_not_called()

    def test_open_inventory_retries_once_after_confirmed_gift_shop_interruption(self):
        task = self.task()
        home = screen((("背包", 0.193, 0.944),))
        shop = screen((("特别礼包", 0.18, 0.05), ("内容物详情", 0.60, 0.50)))
        unrelated = screen((("特别礼包", 0.18, 0.05),))
        inventory = screen((("装备104/500", 0.20, 0.05), ("一键分解", 0.889, 0.928)))
        task._read_equipment = Mock(side_effect=[home, home])
        task._frame_confirms_home = Mock(return_value=True)
        task.recover_daily_home = Mock(return_value=True)
        targets = iter([shop, inventory])

        def wait_for_bag_or_shop(predicate, **_kwargs):
            self.assertFalse(predicate(unrelated))
            target = next(targets)
            self.assertTrue(predicate(target))
            return target

        task._wait_equipment = Mock(side_effect=wait_for_bag_or_shop)
        self.assertIs(inventory, EquipmentDailyTask._open_inventory(task))
        self.assertEqual(2, task.operate_click.call_count)
        task.recover_daily_home.assert_called_once_with()

    def test_leftover_refinement_is_exited_before_reopening_inventory(self):
        task = self.task()
        forge = screen(REFINEMENT_RESULT_LABELS)
        inventory = screen((("装备104/500", 0.20, 0.05), ("一键分解", 0.889, 0.928)))
        task._read_equipment = Mock(side_effect=[forge, forge])
        task._click_top_left_back = Mock(return_value=False)

        def wait_for_inventory(predicate, **_kwargs):
            self.assertFalse(predicate(forge))
            self.assertTrue(predicate(inventory))
            return inventory

        task._wait_equipment = Mock(side_effect=wait_for_inventory)
        self.assertIs(inventory, EquipmentDailyTask._open_inventory(task))
        task.operate_click.assert_called_once_with(
            *REFINEMENT_BACK_POINT,
            name="退出精炼页",
            after_sleep=0.7,
        )

    def test_leaving_forge_does_not_click_again_after_page_changes(self):
        task = self.task()
        forge = screen(REFINEMENT_RESULT_LABELS)
        inventory = screen((("装备104/500", 0.20, 0.05), ("一键分解", 0.889, 0.928)))
        task._click_top_left_back = Mock(return_value=True)
        task._wait_equipment = Mock(return_value=None)
        task._read_equipment = Mock(return_value=inventory)
        task.back = Mock()

        self.assertIs(inventory, task._leave_refinement(forge))
        task.operate_click.assert_not_called()
        task.back.assert_not_called()

    def test_only_the_active_category_is_clicked_to_restore_the_full_inventory(self):
        task = self.task()
        filtered = screen((("装备29/500", 0.20, 0.052), ("一键分解", 0.888, 0.928)))
        full = screen((("装备100/500", 0.20, 0.052), ("一键分解", 0.888, 0.928)))
        filtered.frame[100:120, 740:790] = (24, 13, 7)
        task._read_equipment = Mock(return_value=filtered)

        def wait_for_full_inventory(predicate):
            self.assertFalse(predicate(filtered))
            self.assertTrue(predicate(full))
            return full

        task._wait_equipment = Mock(side_effect=wait_for_full_inventory)
        self.assertIs(full, EquipmentDailyTask._open_inventory(task))
        task.operate_click.assert_called_once_with(
            *CATEGORY_TAB_POINTS[0],
            name="取消装备分类筛选",
            after_sleep=0.6,
        )

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

    def test_scan_skips_when_no_eligible_card_remains_at_top(self):
        task = self.task()
        view = SimpleNamespace(
            inventory=(6, 500),
            selection_count=0,
            frame=np.full((1080, 1920, 3), 90, dtype=np.uint8),
        )
        task._scroll_to_bottom = Mock(return_value=view)
        task._read_equipment = Mock(return_value=view)
        task._grade_points = Mock(return_value=[])
        task.scroll_client = Mock()

        self.assertIs(DISMANTLE_SKIPPED, task._scan_selection_tab(view, view.inventory))
        self.assertEqual(2, task.scroll_client.call_count)
        task.operate_click.assert_not_called()

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

    def test_scrolling_stops_as_soon_as_empty_slots_show_up(self):
        task = self.task()
        empty = np.full((1080, 1920, 3), 20, dtype=np.uint8)
        screen = SimpleNamespace(frame=empty)
        task.scroll_client = Mock()
        self.assertIs(screen, task._scroll_to_bottom(screen, lambda _view: True))
        task.scroll_client.assert_not_called()

    def test_does_not_submit_dismantle_when_dialog_count_is_not_one(self):
        task = self.task()
        inventory = SimpleNamespace(inventory=(100, 500), dismantle_entry=(0.89, 0.93))
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

    def test_single_refine_records_expected_debit_before_clicking(self):
        task = self.task()
        task._record_step = Mock()
        inventory = SimpleNamespace(scored_gear=lambda: [(23, (0.6, 0.3))])
        detail = SimpleNamespace(detail_refine=(0.71, 0.68), refine_once=None)
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
        task._read_refinement_badges = Mock(return_value=(23, 23))
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

    def test_refinement_does_not_submit_a_24_point_or_unverified_candidate(self):
        for badges in ((24, 24), (23, 24), (17, None), (None, None)):
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

    def test_agreeing_badges_recover_from_misread_inventory_and_full_screen_scores(self):
        task = self.task()
        inventory = SimpleNamespace(scored_gear=lambda: [(7, (0.6, 0.3))])
        detail = SimpleNamespace(detail_refine=(0.71, 0.68), refine_once=None)
        refine = screen(
            (
                ("精炼装备分数", 0.182, 0.044),
                ("100", 0.814, 0.052),
                ("300", 0.903, 0.052),
                ("装备分数●", 0.643, 0.258),
                ("11", 0.9, 0.258),
                ("所需材料", 0.75, 0.839),
                ("30", 0.806, 0.839),
                ("精炼80", 0.839, 0.93),
            )
        )
        task._wait_equipment = Mock(
            side_effect=lambda predicate: next(
                (view for view in (detail, refine) if predicate(view)),
                None,
            )
        )
        score = SimpleNamespace(name="17", x=50, y=50, width=24, height=16)
        task.ocr = Mock(side_effect=[[score], [score]])
        task._confirm_refinement = Mock(return_value=True)

        with patch("src.tasks.EquipmentDailyTask.equipment_daily_cycle", return_value="cycle"):
            self.assertTrue(task._refine_once(inventory, "cycle"))

        self.assertEqual(2, task.ocr.call_count)
        task._confirm_refinement.assert_called_once_with([70, 220])

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

    def test_leaving_equipment_uses_left_back_before_generic_home_recovery(self):
        task = self.task()
        inventory = screen(
            (("装备102/500", 0.20, 0.05), ("一键分解", 0.89, 0.93), ("比较", 0.89, 0.034))
        )
        task._read_equipment = Mock(return_value=inventory)

        def confirm_home():
            task.operate_click.assert_called_once_with(
                *BACKPACK_BACK_POINT,
                name="退出装备背包",
                after_sleep=0.8,
            )
            return True

        task.recover_daily_home = Mock(side_effect=confirm_home)
        self.assertTrue(EquipmentDailyTask._leave_equipment(task))
        task.recover_daily_home.assert_called_once_with()

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
