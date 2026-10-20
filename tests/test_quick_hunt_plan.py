import json
import logging
import os
import re
import tempfile
import unittest
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.tasks import run_history
from src.tasks.quick_hunt_plan import (
    PLAN_KEY,
    legacy_plan,
    load_book,
    preset_plans,
    select_steps,
    step,
    step_count,
    validate_plan,
)
from src.tasks.QuickHuntTask import QuickHuntTask
from tests.helpers import StageHistoryTestCase


class HuntPlanTest(StageHistoryTestCase):
    def test_gold_preset_ignores_experience_bonus_and_falls_back_to_materials(self):
        plan = preset_plans()[0]
        for gold, exp, targets in (
            (True, True, ["hunt", "gold"]),
            (True, False, ["hunt", "gold"]),
            (False, True, ["hunt"]),
            (False, False, ["hunt"]),
        ):
            with self.subTest(gold=gold, exp=exp):
                _, steps = select_steps(plan, {"金币": gold, "经验": exp})
                self.assertEqual(targets, [s["target"] for s in steps])
        with self.assertRaisesRegex(ValueError, "无法确认"):
            select_steps(plan, {"金币": None, "经验": False})

    def test_budget_and_reserve_round_down_to_whole_runs(self):
        self.assertEqual(3, step_count(step("gold", "budget", 23), 90, 12))
        self.assertEqual(0, step_count(step(), 17, 12))
        self.assertEqual(13, step_count(step(), 90, 12))
        self.assertEqual(2, step_count(step("exp", "count", 2), 90))

    def test_legacy_settings_keep_allocation_and_resource_preference(self):
        for hunt in (False, True):
            for adventure in (False, True):
                config = {
                    "快速狩猎狩猎场": hunt,
                    "快速狩猎冒险航线": adventure,
                    "快速狩猎双倍策略": "强制双倍",
                    "快速狩猎资源倾向": "经验",
                }
                plan = legacy_plan(config)
                _, steps = select_steps(plan, {"金币": True, "经验": True})
                self.assertEqual(
                    (["hunt"] if hunt else []) + (["exp"] if adventure else []),
                    [s["target"] for s in steps],
                )
                _, steps = select_steps(plan, {"金币": False, "经验": False})
                self.assertEqual([step(mode="count")] if hunt else [], steps)

    def test_invalid_plan_is_not_silently_replaced(self):
        plan = preset_plans()[0]
        plan["fallback"].append(step("gold"))
        with self.assertRaisesRegex(ValueError, "最后一步"):
            validate_plan(plan)
        for raw in ("{", '{"version": 2}', '{"version": 1, "active": 3, "plans": []}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_book({PLAN_KEY: raw})

    def test_scheduler_honors_budget_reserve_and_execution_order(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        plan = {
            "name": "分配",
            "reserve": 12,
            "rules": [],
            "fallback": [step("gold", "budget", 13), step("hunt")],
        }
        task = object.__new__(QuickHuntTask)
        task.config = {PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})}
        task.info_set = Mock()
        task.log_info = Mock()
        task._quick_hunt_free_rice = Mock(return_value=36)
        task._quick_hunt_click_adventure = Mock(return_value=True)
        task._quick_hunt_click_ocr = Mock(return_value=True)
        task._quick_hunt_wait_ocr = Mock(return_value=("野猪洞穴", None))
        task._quick_hunt_select_plan_target = Mock(wraps=task._quick_hunt_select_plan_target)
        task._quick_hunt_execute_current_map = Mock(return_value="done")
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(
            ["gold", "gold", "hunt", "hunt"],
            [c.args[0] for c in task._quick_hunt_select_plan_target.call_args_list],
        )
        self.assertEqual(
            [6] * 4,
            [c.kwargs["max_cost"] for c in task._quick_hunt_execute_current_map.call_args_list],
        )
        task._quick_hunt_click_ocr.assert_called_once()
        task._quick_hunt_wait_ocr.assert_called_once()
        for call in task._quick_hunt_execute_current_map.call_args_list[2:]:
            pattern = call.kwargs["expected_map_pattern"]
            self.assertTrue(re.search(pattern, "野猪洞穴"))
            self.assertFalse(re.search(pattern, "哥布林遗迹"))

        task._quick_hunt_wait_ocr.return_value = ("", None)
        self.reset_stage_history()
        task._quick_hunt_execute_current_map.reset_mock()
        self.assertFalse(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(2, task._quick_hunt_execute_current_map.call_count)

        task._quick_hunt_wait_ocr.return_value = ("野猪洞穴", None)
        self.reset_stage_history()
        task._quick_hunt_click_ocr.reset_mock()
        task._quick_hunt_execute_current_map.side_effect = [
            "done", "done", "wrong_map", "done", "done",
        ]
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual(2, task._quick_hunt_click_ocr.call_count)
        task._quick_hunt_free_rice = Mock(return_value=0)
        task._quick_hunt_execute_current_map.reset_mock()
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        task._quick_hunt_execute_current_map.assert_not_called()

    def test_empty_plan_preserves_rice_without_reading_its_balance(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        plan = {"name": "保留米饭", "reserve": 0, "rules": [], "fallback": []}
        task = object.__new__(QuickHuntTask)
        task.config = {PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})}
        task.info_set = Mock()
        task.log_info = Mock()
        task._quick_hunt_free_rice = Mock(return_value=None)
        task._quick_hunt_execute_current_map = Mock()

        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        task._quick_hunt_free_rice.assert_not_called()
        task._quick_hunt_execute_current_map.assert_not_called()

    def test_scheduler_leaves_regenerated_rice_outside_initial_allowance(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        for steps, current, modes in (
            ([step("gold", "count", 1), step("hunt")], 18, ["MIN", "MIN"]),
            ([step("hunt")], 18, ["MIN", "MIN"]),
            ([step("hunt")], 12, ["MAX"]),
        ):
            with self.subTest(steps=steps, current=current):
                self.reset_stage_history()
                plan = {"name": "本轮额度", "reserve": 0, "rules": [], "fallback": steps}
                task = object.__new__(QuickHuntTask)
                task.config = {
                    PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})
                }
                task.info_set = Mock()
                task.log_info = Mock()
                balances = iter((12,))
                task._quick_hunt_free_rice = Mock(side_effect=lambda: next(balances, current))
                task._quick_hunt_select_plan_target = Mock(return_value=True)
                task._quick_hunt_execute_current_map = Mock(return_value="done")

                self.assertTrue(task._quick_hunt_run_rice_scheduler())
                calls = task._quick_hunt_execute_current_map.call_args_list
                self.assertEqual(modes, [call.args[0] for call in calls])
                self.assertEqual(12, sum(call.kwargs["max_cost"] for call in calls))

    def test_scheduler_skips_unspendable_rice_before_reading_bonus(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        for rice, reserve in ((0, 0), (5, 0), (17, 12)):
            with self.subTest(rice=rice, reserve=reserve):
                plan = preset_plans()[0]
                plan["reserve"] = reserve
                task = object.__new__(QuickHuntTask)
                task.config = {
                    PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})
                }
                task.info_set = Mock()
                task.log_info = Mock()
                task._quick_hunt_free_rice = Mock(return_value=rice)
                task._quick_hunt_plan_double_states = Mock(
                    return_value={"金币": None, "经验": None},
                )
                task._quick_hunt_execute_current_map = Mock()

                self.assertTrue(task._quick_hunt_run_rice_scheduler())
                task._quick_hunt_plan_double_states.assert_not_called()
                task._quick_hunt_execute_current_map.assert_not_called()

    def test_scheduler_continues_only_current_step_after_budget_cancel_is_confirmed(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                self.reset_stage_history()
                plan = {
                    "name": "本轮额度", "reserve": 0, "rules": [],
                    "fallback": [step("hunt", "count", 1), step("gold")],
                }
                task = object.__new__(QuickHuntTask)
                task.config = {
                    PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})
                }
                task.info_set = Mock()
                task.log_info = Mock()
                balances = iter((89, 89, 83))
                task._quick_hunt_free_rice = Mock(side_effect=lambda: next(balances, 84))
                task._quick_hunt_select_plan_target = Mock(return_value=True)
                task._quick_hunt_click_ocr = Mock(return_value=True)
                task._quick_hunt_wait_confirmation_dialog = Mock(return_value=True)
                task._quick_hunt_wait_map_confirmation = Mock(
                    return_value=("matched", "哥布林遗迹极难", "哥布林遗迹"),
                )
                task._quick_hunt_prepare_dialog = Mock(
                    side_effect=lambda mode, *_: "budget_limited" if mode == "MAX" else
                    SimpleNamespace(start_point=(1050, 785), frame_shape=(1080, 1920, 3),
                                    map_name="哥布林遗迹极难", count=1, cost=6, pool=90),
                )
                task._quick_hunt_cancel_dialog = Mock(return_value=cancelled)
                def confirmed_result(*args, **kwargs):
                    key, scope, evidence = task._hunt_checkpoint
                    run_history.save_stage(task, key, "done", scope=scope, evidence=evidence)
                    return "done"

                task._quick_hunt_wait_result = Mock(side_effect=confirmed_result)
                vision = Mock()
                task._quick_vision = lambda: vision
                task._quick_hunt_execute_current_map = Mock(
                    wraps=task._quick_hunt_execute_current_map,
                )

                self.assertEqual(cancelled, task._quick_hunt_run_rice_scheduler())
                calls = task._quick_hunt_execute_current_map.call_args_list
                self.assertEqual(["MIN", "MAX"] + (["MIN"] * 13 if cancelled else []),
                                 [call.args[0] for call in calls])
                self.assertEqual(83, calls[1].kwargs["max_cost"])
                self.assertEqual(14 if cancelled else 1, vision.click_client.call_count)
                selected = task._quick_hunt_select_plan_target.call_args_list
                self.assertEqual(["hunt"] + ["gold"] * (14 if cancelled else 1),
                                 [call.args[0] for call in selected])
                task._quick_hunt_cancel_dialog.assert_called_once()

    def test_scheduler_stops_after_uncertain_submission_without_rerunning_step(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        plan = {
            "name": "指定次数", "reserve": 0, "rules": [],
            "fallback": [step("hunt", "count", 2), step("gold")],
        }
        task = object.__new__(QuickHuntTask)
        task.config = {PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})}
        task.info_set = Mock()
        task.log_info = Mock()
        task._quick_hunt_free_rice = Mock(return_value=90)
        task._quick_hunt_select_plan_target = Mock(return_value=True)
        task._quick_hunt_execute_current_map = Mock(return_value="failed")

        self.assertFalse(task._quick_hunt_run_rice_scheduler())
        task._quick_hunt_select_plan_target.assert_called_once_with("hunt")
        task._quick_hunt_execute_current_map.assert_called_once()

    def test_plan_resume_keeps_step_identity_and_does_not_repeat_uncertain_spending(self):
        plan = {"name": "接续", "reserve": 0, "rules": [], "fallback": [
            step("hunt", "count", 2), step("hunt", "count", 1), step("gold"),
        ]}
        task = object.__new__(QuickHuntTask)
        task.config = {PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})}
        task.info_set = task.log_info = Mock()
        balance = [90]
        task._quick_hunt_free_resource = Mock(side_effect=lambda resource: balance[0])
        task._quick_hunt_free_rice = Mock(side_effect=lambda: balance[0])
        task._quick_hunt_select_plan_target = Mock(return_value=True)
        task._quick_hunt_click_ocr = Mock(return_value=True)
        task._quick_hunt_wait_confirmation_dialog = Mock(return_value=True)
        task._quick_hunt_wait_map_confirmation = Mock(
            return_value=("matched", "哥布林遗迹", "金币"),
        )
        submission = []

        def prepare(mode, *args):
            cost = balance[0] if mode == "MAX" else 6
            value = SimpleNamespace(map_name="目标", count=cost // 6, cost=cost,
                                    pool=balance[0], start_point=(1, 1),
                                    frame_shape=(1080, 1920, 3))
            submission[:] = [value]
            return value

        task._quick_hunt_prepare_dialog = Mock(side_effect=prepare)
        clicks = []

        def spend(*args, **kwargs):
            clicks.append(submission[0].cost)
            balance[0] -= submission[0].cost

        task._quick_vision = Mock(return_value=SimpleNamespace(click_client=spend))
        unknown = [True]

        def result(*args, **kwargs):
            key, scope, evidence = task._hunt_checkpoint
            if key.endswith("0:hunt:1") and unknown[0]:
                return "failed"
            run_history.save_stage(task, key, "done", scope=scope, evidence=evidence)
            return "done"

        task._quick_hunt_wait_result = Mock(side_effect=result)
        self.assertFalse(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([6, 6], clicks)
        self.assertFalse(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([6, 6], clicks)
        unknown[0] = False
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([6, 6, 6, 72], clicks)
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([6, 6, 6, 72], clicks)
        self.assertEqual(0, balance[0])

    def test_free_rice_reader_does_not_use_stored_rice_or_ambiguous_ocr(self):
        from src.tasks.QuickHuntTask import QuickHuntTask

        task = object.__new__(QuickHuntTask)
        task._info_lock = RLock()
        task.info = {}
        task.logger = logging.getLogger(__name__)
        task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        task.sleep = Mock()
        task._quick_vision = Mock(return_value=SimpleNamespace(ocr_boxes=Mock(return_value=[])))
        for text, expected in (
            ("0/90 +1.77K", 0),
            ("84/90", 84),
            ("1.77K", None),
            ("0/90 84/90", None),
            ("990/90", None),
        ):
            task._quick_hunt_ocr_text = Mock(return_value=text)
            self.assertEqual(expected, task._quick_hunt_free_rice())
        task._quick_hunt_ocr_text = Mock(side_effect=["84/90", "78/90"])
        self.assertIsNone(task._quick_hunt_free_rice())

    def test_free_resource_raw_fraction_fallback_keeps_capacity_and_two_frame_guards(self):
        task = object.__new__(QuickHuntTask)
        task.info_set = Mock()
        task.sleep = Mock()
        task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        task._quick_hunt_ocr_text = Mock(return_value="0 9 0")
        boxes = Mock()
        task._quick_vision = Mock(return_value=SimpleNamespace(ocr_boxes=boxes))
        for resource, texts, expected in (
            ("米饭", ["0/90", "+3.39K"], 0),
            ("米饭", ["84/90", "2"], 84),
            ("火把", ["0/60", "+100"], 0),
            ("米饭", ["0 9 0"], None),
            ("米饭", ["0/90 3.39K"], None),
            ("米饭", ["0/90", "84/90"], None),
            ("米饭", ["0/90", "0/60"], None),
            ("米饭", ["91/90"], None),
            ("米饭", ["0/60"], None),
            ("米饭", ["+3.39K"], None),
        ):
            with self.subTest(resource=resource, texts=texts):
                boxes.return_value = [SimpleNamespace(name=text) for text in texts]
                self.assertEqual(expected, task._quick_hunt_free_resource(resource))
        boxes.side_effect = [[SimpleNamespace(name="0/90")], [SimpleNamespace(name="6/90")]]
        self.assertIsNone(task._quick_hunt_free_resource("米饭"))
        boxes.reset_mock(side_effect=True)
        task._quick_hunt_ocr_text.return_value = "90/90"
        self.assertEqual(90, task._quick_hunt_free_resource("米饭"))
        boxes.assert_not_called()

    def test_free_resource_reader_preserves_full_balance_and_filters_yellow_before_ocr(self):
        for resource, capacity, left, right, yellow_left in (
            ("米饭", 90, 1621, 1689, 1726),
            ("火把", 60, 1720, 1785, 1680),
        ):
            for height, width in ((720, 1280), (1080, 1920), (1081, 1920)):
                with self.subTest(resource=resource, resolution=(width, height)):
                    task = object.__new__(QuickHuntTask)
                    task._info_lock = RLock()
                    task.info = {}
                    task.logger = logging.getLogger(__name__)
                    task.sleep = Mock()
                    task._quick_vision = Mock(
                        return_value=SimpleNamespace(ocr_boxes=Mock(return_value=[])),
                    )
                    reference = np.zeros((1080, 1920, 3), dtype=np.uint8)
                    reference[43:70, left:right] = (245, 245, 245)
                    reference[50:60, yellow_left:yellow_left + 10] = (0, 200, 255)
                    frame = cv2.resize(reference, (width, height), interpolation=cv2.INTER_NEAREST)
                    task.capture_frame = Mock(return_value=frame)
                    expected_pixels = np.count_nonzero(np.all(frame == 245, axis=2))

                    def read_resource(image, _roi, name):
                        self.assertEqual(f"免费{resource}数量", name)
                        self.assertEqual(expected_pixels * 3, np.count_nonzero(image))
                        return f"{capacity}/{capacity}" if image.any() else ""

                    task._quick_hunt_ocr_text = Mock(side_effect=read_resource)
                    self.assertEqual(capacity, task._quick_hunt_free_resource(resource))
                    frame[np.all(frame == 245, axis=2)] = (0, 200, 255)
                    expected_pixels = 0
                    self.assertIsNone(task._quick_hunt_free_resource(resource))


class HuntEditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_config_widget_releases_editors_after_save_cancel_and_error(self):
        from PySide6.QtCore import QCoreApplication, QEvent, QTimer
        from PySide6.QtWidgets import QDialog

        from src.ui.quick_hunt_plan_editor import HuntPlanConfigWidget, HuntPlanDialog

        widget = HuntPlanConfigWidget({PLAN_KEY: ""}, None)
        self.addCleanup(widget.deleteLater)
        for result in (QDialog.Accepted, QDialog.Rejected, RuntimeError("editor failed")):
            for _ in range(2):
                with self.subTest(result=result):
                    if isinstance(result, Exception):
                        with patch.object(HuntPlanDialog, "exec", side_effect=result):
                            with self.assertRaisesRegex(RuntimeError, "editor failed"):
                                widget.edit()
                    else:
                        with patch.object(HuntPlanDialog, "exec", return_value=result):
                            widget.edit()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    self.assertEqual([], widget.findChildren(HuntPlanDialog))

        closing_widget = HuntPlanConfigWidget({PLAN_KEY: ""}, None)
        QTimer.singleShot(0, closing_widget.deleteLater)
        closing_widget.edit()

    def test_editor_save_cancel_reset_and_running_guard(self):
        from ok.util.config import Config

        from src.ui.quick_hunt_plan_editor import HuntPlanConfigWidget, HuntPlanDialog

        with tempfile.TemporaryDirectory() as folder:
            config = Config("hunt", {PLAN_KEY: ""}, folder=folder)
            original = deepcopy(dict(config))
            dialog = HuntPlanDialog(config)
            dialog.add_preset(1)
            dialog.reserve.setValue(12)
            self.assertIn("剩余免费米饭：12", dialog.preview_label.text())
            dialog.reject()
            self.assertEqual(original, dict(config))
            dialog.save()
            saved = Config("hunt", {PLAN_KEY: ""}, folder=folder)
            book = load_book(saved)
            self.assertEqual(12, book["plans"][book["active"]]["reserve"])
            for owner in ("task", "batch"):
                for running, enabled in ((True, False), (False, True)):
                    with self.subTest(owner=owner, running=running, enabled=enabled):
                        batch = SimpleNamespace(
                            running=owner == "batch" and running,
                            enabled=owner == "batch" and enabled,
                        )
                        task = SimpleNamespace(
                            running=owner == "task" and running,
                            enabled=owner == "task" and enabled,
                            executor=SimpleNamespace(get_task_by_class=Mock(return_value=batch)),
                        )
                        locked = HuntPlanDialog(saved, task)
                        changed_name = f"修改方案-{owner}-{running}-{enabled}"
                        locked.rename(changed_name)
                        before = saved[PLAN_KEY]
                        locked.save()
                        self.assertEqual(before, saved[PLAN_KEY])
                        self.assertIn("停止", locked.error.text())
                        task.running = task.enabled = batch.running = batch.enabled = False
                        locked.save()
                        self.assertEqual(changed_name, load_book(saved)["plans"][-1]["name"])
                        locked.close()
                        locked.deleteLater()
            widget = HuntPlanConfigWidget(saved, None)
            saved.reset_to_default()
            widget.update_value()
            self.assertIn("原有刷取设置", widget.title.text())
            for item in (dialog, widget):
                item.close()
                item.deleteLater()


if __name__ == "__main__":
    unittest.main()
