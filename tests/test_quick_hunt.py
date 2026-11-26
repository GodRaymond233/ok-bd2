import json
import re
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np
from ok.task.exceptions import TaskDisabledException
from ok.util.config import Config

from src.interaction.BD2Interaction import InputTargetChanged
from src.tasks import run_history
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.quick_hunt import (
    QUICK_HUNT_CRYSTAL_CLICK_ROI,
    QUICK_HUNT_CRYSTAL_ENTRY_PATTERN,
    QUICK_HUNT_REWARD_TITLE_ROI,
    HuntDialog,
    QuickHuntFeatureMixin,
)
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
from src.tasks.quick_hunt_vision import parse_free_pool, parse_stone_rows, read_free_switch
from src.tasks.QuickHuntTask import QuickHuntTask
from src.utils.home_confirmation import modal_panel_bbox
from tests.helpers import FakeClock, StageHistoryTestCase
from tests.helpers.quick_hunt_settlement import settlement_task


def hunt_dialog(**changes):
    return replace(
        HuntDialog(
            "哥布林遗迹极难", 1, 6, 90, "米饭", "on", (1140, 350), (1050, 785), (1080, 1920, 3)
        ),
        **changes,
    )


def box(text, x=100, y=100, width=40, height=18):
    return SimpleNamespace(name=text, x=x, y=y, width=width, height=height)


def switch_frame(state="on", *, height=720, channels=3, second=False):
    frame = np.full((720, 1280, 3), 40, dtype=np.uint8)
    color = (43, 133, 171) if state == "on" else (88, 88, 88)
    for offset in (0, 42) if second else (0,):
        cv2.rectangle(frame, (823 + offset, 196), (839 + offset, 212), color, -1)
        for x in (823, 839):
            cv2.circle(frame, (x + offset, 204), 8, color, -1, cv2.LINE_AA)
        knob_x = 839 if state == "on" else 823
        cv2.circle(frame, (knob_x + offset, 204), 7, (206, 219, 219), -1, cv2.LINE_AA)
    scale = height / 720
    frame = cv2.resize(frame, None, fx=scale, fy=scale)
    if channels == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    return frame, box("仅使用免费米饭", 704 * scale, 195 * scale, 105 * scale, 18 * scale)


def hunt_task(**config):
    task = object.__new__(QuickHuntTask)
    task.config = config
    task.info_set = Mock()
    task.log_info = Mock()
    task.sleep = Mock()
    task._save_flow_diagnostic = Mock()
    return task


def plan_task(steps, balances, *, reserve=0):
    plan = {"name": "分配", "reserve": reserve, "rules": [], "fallback": steps}
    task = hunt_task()
    task.config[PLAN_KEY] = json.dumps({"version": 1, "active": 0, "plans": [plan]})
    sequence = iter(balances)
    task._quick_hunt_free_rice = Mock(side_effect=lambda: next(sequence, balances[-1]))
    task._quick_hunt_click_adventure = Mock(return_value=True)
    task._quick_hunt_click_ocr = Mock(return_value=True)
    task._quick_hunt_wait_ocr = Mock(return_value=("野猪洞穴", None))
    task._quick_hunt_execute_current_map = Mock(return_value="done")
    return task


def dialog_snapshot(task, frame, labels, resource, state, title="风之洞穴极难", count=1):
    vision = task._quick_vision()
    controls = [box(label, 700, 195, 105) for label in labels]
    controls.extend(
        (
            box(title, 600, 240, 120),
            box(f"狩猎{count}次", 600, 300, 90),
            box("MIN MAX 取消", 600, 400, 180),
        )
    )
    vision.ocr_boxes.side_effect = [controls, [box(f"狩猎{count * 6}", 900, 600, 80)]]
    with patch("src.tasks.quick_hunt.read_free_switch", return_value=(state, (830, 204))):
        return task._quick_hunt_dialog_snapshot(frame, "圣石", resource)


def stone_boxes(texts):
    return [box(text, y=100 + index * 40) for index, text in enumerate(texts)]


def crystal_menu(entered, balance):
    task = hunt_task()
    task._quick_hunt_wait_ocr = Mock(return_value=("火之洞穴" if entered else "", None))
    task._quick_hunt_menu_still_open = Mock(return_value=True)
    task._quick_hunt_free_resource = Mock(return_value=balance)
    task.capture_frame = Mock(return_value=object())
    task._quick_vision = Mock()
    task._quick_vision().ocr_boxes.side_effect = [
        stone_boxes((first, "689", "683", "406", "2120"))
        for first in ("1.808", "1.807", "1,807")
    ]
    task._quick_hunt_click_ocr = Mock(return_value=True)
    task._quick_hunt_execute_current_map = Mock(return_value="done")
    return task


def currency_frame(w, h, left, right, yellow):
    reference = np.zeros((1080, 1920, 3), np.uint8)
    reference[43:70, left:right] = 245
    if yellow is not None:
        reference[50:60, yellow:yellow + 10] = (0, 200, 255)
    return cv2.resize(reference, (w, h), interpolation=cv2.INTER_NEAREST)


def with_dialog_clock(task, frame):
    clock = FakeClock()
    task.sleep = clock.sleep
    task.capture_frame = Mock(return_value=frame)
    vision = Mock()
    vision.click_client.side_effect = lambda *_, after_sleep=0: clock.sleep(after_sleep)
    task._quick_vision = Mock(return_value=vision)
    task._quick_hunt_wait_confirmation_dialog = Mock(return_value=True)
    return clock, vision


def rice_plans():
    gold = step("gold", "budget", 13)
    hunt = step("hunt")
    return (
        ([], [None], 0, [], "", True, [], [], []),
        ([hunt], [0], 0, [], "", True, [], [], []),
        ([gold, hunt], [36], 12, ["done"] * 4, "野猪洞穴", True,
         ["gold", "gold", "hunt", "hunt"], ["MIN"] * 4, [6] * 4),
        ([gold, hunt], [36], 12, ["done"] * 2, "", False,
         ["gold", "gold", "hunt"], ["MIN"] * 2, [6] * 2),
        ([hunt], [12, 18], 0, ["done"] * 2, "", True, ["hunt"] * 2, ["MIN"] * 2, [6] * 2),
        ([hunt], [12], 0, ["done"], "", True, ["hunt"], ["MAX"], [12]),
        ([step("gold")], [6], 0, ["wrong_map", "done"], "", True,
         ["gold"] * 2, ["MAX"] * 2, [6] * 2),
        ([step("hunt", "count", 2), gold], [90], 0, ["failed"], "", False, ["hunt"], ["MIN"], [6]),
    )


class QuickHuntTest(StageHistoryTestCase):
    def test_legacy_configuration_and_saved_plan_validation(self):
        task = hunt_task()
        task.default_config, task.config_description, task.config_type = {}, {}, {}
        with patch("src.tasks.DailyTask.BaseBD2Task.__init__", return_value=None):
            QuickHuntTask.__init__(task)
        config = Config.__new__(Config)
        config.validator = None
        self.assertTrue(
            config.verify_config(
                {"主页压暗阈值": 210.0, "主页确认等待秒数": 27.0, "快速狩猎章节图": "低练度·章节1"},
                task.default_config,
            )
        )
        self.assertNotIn("快速狩猎章节图", config)
        self.assertEqual((210.0, 27.0), (config["主页压暗阈值"], config["主页确认等待秒数"]))
        for key in ("启用", "快速狩猎狩猎场", "快速狩猎冒险航线", "快速狩猎圣石洞穴"):
            self.assertTrue(config[key])
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
        plan = preset_plans()[0]
        plan["fallback"].append(step("gold"))
        with self.assertRaisesRegex(ValueError, "最后一步"):
            validate_plan(plan)
        for raw in ("{", '{"version": 2}', '{"version": 1, "active": 3, "plans": []}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_book({PLAN_KEY: raw})
        book = {"version": 1, "active": 0, "plans": preset_plans()[:2]}
        book["plans"][1]["rules"].insert(0, {"condition": "always", "steps": []})
        with self.assertRaisesRegex(ValueError, "方案 2.*规则 1.*最后"):
            load_book({PLAN_KEY: json.dumps(book)})
        book["plans"][1] = preset_plans()[1]
        book["plans"][1]["reserve"] = 10000
        with self.assertRaisesRegex(ValueError, "方案 2.*免费米饭.*付费米饭无需填写"):
            load_book({PLAN_KEY: json.dumps(book)})

    def test_dialog_reads_only_unique_free_resource_labels_and_complete_switch_tracks(self):
        for resource in ("米饭", "火把"):
            for state in ("on", "off"):
                for height in (720, 1080, 2160):
                    for channels in (3, 4):
                        with self.subTest(
                            resource=resource, state=state, height=height, channels=channels
                        ):
                            frame, label = switch_frame(state, height=height, channels=channels)
                            actual, center = read_free_switch(frame, label)
                            self.assertEqual(state, actual)
                            np.testing.assert_allclose(
                                center,
                                np.array([831, 204]) * height / 720,
                                atol=2 * height / 720,
                                rtol=0,
                            )
        frame, label = switch_frame()
        task = hunt_task()
        task._quick_vision = Mock()
        task._quick_hunt_free_resource = Mock(return_value=60)
        task._quick_hunt_box_enabled = Mock(return_value=True)
        for resource in ("米饭", "火把"):
            for labels, accepted in (
                ((f"仅使用免费{resource}",), True),
                ((f"仅使免费{resource}",), True),
                ((f"仅使免费{resource}", f"仅使用免费{resource}"), False),
                ((f"仅使用{resource}",), False),
                ((f"使用免费{resource}",), False),
                (("仅使用免费" + ("米饭" if resource == "火把" else "火把"),), False),
            ):
                for state in ("on", "off", "unknown"):
                    with self.subTest(labels=labels, state=state):
                        snap = dialog_snapshot(task, frame, labels, resource, state)
                        observed = (
                            (snap.resource, snap.free_state, snap.count, snap.cost, snap.pool)
                            if snap
                            else None
                        )
                        self.assertEqual(
                            (resource, state, 1, 6, 60) if accepted else None, observed
                        )
        no_track = np.full_like(frame, 40)
        cv2.circle(no_track, (839, 204), 7, (206, 219, 219), -1, cv2.LINE_AA)
        occluded = frame.copy()
        occluded[194:215, 821:832] = 40
        no_knob = frame.copy()
        no_knob[194:215, 830:850] = 40
        for name, candidate in (
            ("blank", np.full_like(frame, 40)),
            ("dim", (frame * 0.3).astype(np.uint8)),
            ("no_track", no_track),
            ("occluded", occluded),
            ("no_knob", no_knob),
            ("truncated", frame[:, :842]),
            ("two_candidates", switch_frame(second=True)[0]),
            ("grayscale", cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)),
        ):
            with self.subTest(name=name):
                self.assertEqual(("unknown", None), read_free_switch(candidate, label))

    def test_free_balance_requires_two_equal_fractions_and_excludes_stored_currency(self):
        task = hunt_task()
        task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), np.uint8))
        task._quick_vision = Mock()
        raw = task._quick_vision().ocr_boxes
        for resource, text, fallback, expected in (
            ("米饭", "0/90 +1.77K", [], 0), ("米饭", "84/90", [], 84),
            ("米饭", "1.77K", [], None), ("米饭", "0/90 84/90", [], None),
            ("米饭", "990/90", [], None),
            ("米饭", "0 9 0", ["0/90", "+3.39K"], 0),
            ("米饭", "", ["84/90", "2"], 84),
            ("火把", "", ["0/60", "+100"], 0),
            ("米饭", "", ["0 9 0"], None),
            ("米饭", "", ["0/90 3.39K"], None),
            ("米饭", "", ["0/90", "84/90"], None),
            ("米饭", "", ["0/90", "0/60"], None),
            ("米饭", "", ["91/90"], None),
            ("米饭", "", ["0/60"], None),
            ("米饭", "", ["+3.39K"], None),
        ):
            with self.subTest(resource=resource, text=text, fallback=fallback):
                task._quick_hunt_ocr_text = Mock(return_value=text)
                raw.return_value = [box(t) for t in fallback]
                self.assertEqual(expected, task._quick_hunt_free_resource(resource))
        task._quick_hunt_ocr_text.side_effect = ["84/90", "78/90"]
        self.assertIsNone(task._quick_hunt_free_rice())
        raw.side_effect = [[box("0/90")], [box("6/90")]]
        task._quick_hunt_ocr_text = Mock(return_value="")
        self.assertIsNone(task._quick_hunt_free_rice())
        valid = (
            ("90/90 +20.5K", 90, (90, 90)), ("免费米饭 0 / 90", 90, (0, 90)),
            ("６０／６０", 60, (60, 60)), ("12：90", 90, (12, 90)), ("0|60", None, (0, 60)),
            ("6-60", 60, (6, 60)), ("6~60", 60, (6, 60)),
        )
        invalid = (
            ("90", 90), ("90/", 90), ("/90", 90), ("90/90 60/60", 90), ("90/90/90", 90),
            ("0/90 0/90", 90), ("1.2K/90", 90), ("100/90", 90), ("0/0", None),
            ("12/9000", None), ("12/60", 90), ("90/90.5", 90), ("-1/90", 90),
            ("0/90K", 90), ("1,000/90", 90),
        )
        for text, capacity, value in (*valid, *((s, cap, None) for s, cap in invalid)):
            with self.subTest(text=text, capacity=capacity):
                self.assertEqual(value, parse_free_pool(text, capacity))

        for resource, capacity, left, right, yellow in (
            ("米饭", 90, 1621, 1689, 1726),
            ("米饭", 90, 1734, 1787, None),
            ("火把", 60, 1622, 1689, 1705),
            ("火把", 60, 1642, 1707, 1728),
            ("火把", 60, 1720, 1787, None),
        ):
            for h, w, x, y, cw, ch in (
                (720, 1280, 1053, 25, 147, 28),
                (1080, 1920, 1580, 38, 220, 42),
                (1081, 1920, 1580, 38, 220, 42),
            ):
                with self.subTest(resource=resource, left=left, size=(w, h)):
                    frame = currency_frame(w, h, left, right, yellow)
                    task.capture_frame.return_value = frame
                    task._quick_vision().ocr_boxes = Mock(return_value=[])

                    def read(image, _, name):
                        self.assertEqual(f"免费{resource}数量", name)
                        self.assertEqual((ch, cw, 3), image.shape)
                        expected = frame[y : y + ch, x : x + cw].copy()
                        expected[np.any(expected != 245, axis=2)] = 0
                        np.testing.assert_array_equal(expected, image)
                        self.assertEqual(np.count_nonzero(frame == 245), np.count_nonzero(image))
                        return f"{capacity}/{capacity}" if image.any() else ""

                    task._quick_hunt_ocr_text = read
                    self.assertEqual(capacity, task._quick_hunt_free_resource(resource))
                    task._quick_vision().ocr_boxes.assert_not_called()
                    frame[np.all(frame == 245, axis=2)] = (0, 200, 255)
                    self.assertIsNone(task._quick_hunt_free_resource(resource))

    def test_crystal_selects_the_lowest_stable_inventory_with_free_torches(self):
        baseline = stone_boxes(map(str, range(5)))
        boxes = [
            box("1,", y=100, width=15),
            box("834", x=117, y=100, width=28),
            box("1.2", y=140, width=25),
            box("K", x=127, y=141, width=12),
            box("0.5M", y=180),
            box("2.5万", y=220),
            box("0", y=260),
        ]
        self.assertEqual((1834, 1200, 500000, 25000, 0), parse_stone_rows(reversed(boxes)))
        for texts, expected in (
            (("1.807", "689", "683", "406", "2,120"), (1807, 689, 683, 406, 2120)),
            (("11,074", "10,206", "9.149", "10,273", "10,235"), (11074, 10206, 9149, 10273, 10235)),
            (("3.715", "1.001", "1.057", "3,706", "2,049"), (3715, 1001, 1057, 3706, 2049)),
            (("71.568", "5.735", "4,671", "4,719", "4,601"), (71568, 5735, 4671, 4719, 4601)),
            (
                ("１．８０７", "1.234.567", "1,234.567", "1.234,567", "1.807K"),
                (1807, 1234567, 1234567, 1234567, 1807),
            ),
        ):
            with self.subTest(texts=texts):
                self.assertEqual(expected, parse_stone_rows(stone_boxes(texts)))
        stone_counts = ("3,437", "2,066", "1,971", "1,370", "1,495")
        for name, noisy_boxes in (
            ("stray_chinese_row", [box("处", y=60)] + stone_boxes(stone_counts)),
            ("stray_chinese_same_row", [box("处", x=20, y=100)] + stone_boxes(stone_counts)),
        ):
            with self.subTest(name=name):
                self.assertEqual((3437, 2066, 1971, 1370, 1495), parse_stone_rows(noisy_boxes))
        for name, boxes in (
            ("missing_row", baseline[:-1]),
            ("extra_row", baseline + [box("6", y=300)]),
            ("stray_unit_row", baseline + [box("K", y=300)]),
            ("duplicate", baseline + [baseline[0]]),
            ("overlap", baseline + [box("00", x=130)]),
            ("separate_column", baseline + [box("00", x=200)]),
            ("uncertain_decimal", [box("1.94")] + baseline[1:]),
            ("zero_decimal", [box("0.001")] + baseline[1:]),
            ("ungrouped_decimal", [box("1234.567")] + baseline[1:]),
            ("bad_dot_grouping", [box("1.234.56")] + baseline[1:]),
            ("decimal_suffix", [box("1.234.567K")] + baseline[1:]),
            ("embedded_separator", [box("1:807")] + baseline[1:]),
            ("bad_grouping", [box("1,23")] + baseline[1:]),
            ("unknown_text", [box("库存100")] + baseline[1:]),
            ("fraction", [box("1/2")] + baseline[1:]),
            ("fractional_item", [box("0.0001K")] + baseline[1:]),
            ("invalid_geometry", [box("1", width=0)] + baseline[1:]),
        ):
            with self.subTest(name=name):
                self.assertIsNone(parse_stone_rows(boxes))
        for entered, balance, expected in (
            (True, 60, True),
            (True, None, False),
            (False, 60, False),
        ):
            with self.subTest(entered=entered, balance=balance):
                task = crystal_menu(entered, balance)
                vision = task._quick_vision()
                self.assertEqual(expected, task._quick_hunt_run_crystal_cave())
                self.assertEqual(
                    [call("火把")] if entered else [], task._quick_hunt_free_resource.call_args_list
                )
                self.assertEqual(3 if expected else 0, vision.ocr_boxes.call_count)
                self.assertEqual(
                    1 + int(expected) if entered else 3, task._quick_hunt_click_ocr.call_count
                )
                self.assertEqual(
                    ([QUICK_HUNT_CRYSTAL_ENTRY_PATTERN], QUICK_HUNT_CRYSTAL_CLICK_ROI),
                    task._quick_hunt_click_ocr.call_args_list[0].args[:2],
                )
                self.assertEqual(int(expected), task._quick_hunt_execute_current_map.call_count)
                if expected:
                    submitted = task._quick_hunt_execute_current_map.call_args
                    self.assertEqual(("MAX", "光属性圣石"), submitted.args)
                    self.assertEqual("火把", submitted.kwargs["resource"])
                    self.assertRegex("光之洞穴", submitted.kwargs["expected_map_pattern"])
                    self.assertNotRegex("火之洞穴", submitted.kwargs["expected_map_pattern"])

    def test_rice_scheduler_respects_initial_allowance_and_reselects_wrong_maps(self):
        for steps, balances, reserve, results, map_name, ok, targets, modes, costs in rice_plans():
            with self.subTest(steps=steps, balances=balances, results=results):
                self.reset_stage_history()
                task = plan_task(steps, balances, reserve=reserve)
                task._quick_hunt_wait_ocr.return_value = (map_name, None)
                task._quick_hunt_select_plan_target = Mock(
                    wraps=task._quick_hunt_select_plan_target
                )
                task._quick_hunt_execute_current_map.side_effect = results
                self.assertEqual(ok, task._quick_hunt_run_rice_scheduler())
                self.assertEqual(
                    targets, [c.args[0] for c in task._quick_hunt_select_plan_target.call_args_list]
                )
                calls = task._quick_hunt_execute_current_map.call_args_list
                self.assertEqual(modes, [c.args[0] for c in calls])
                self.assertEqual(costs, [c.kwargs["max_cost"] for c in calls])
                if not steps:
                    task._quick_hunt_free_rice.assert_not_called()
                if map_name:
                    self.assertRegex(map_name, calls[-1].kwargs["expected_map_pattern"])
                    self.assertNotRegex("哥布林遗迹", calls[-1].kwargs["expected_map_pattern"])
        for item, amount, reserve, expected in (
            (step("gold", "budget", 23), 90, 12, 3),
            (step(), 17, 12, 0),
            (step(), 90, 12, 13),
            (step("exp", "count", 2), 90, 0, 2),
        ):
            self.assertEqual(expected, step_count(item, amount, reserve))
        for gold_bonus, exp_bonus, targets in (
            (True, True, ["hunt", "gold"]),
            (True, False, ["hunt", "gold"]),
            (False, True, ["hunt"]),
            (False, False, ["hunt"]),
        ):
            task = hunt_task()
            vision = Mock()
            task._quick_vision = Mock(return_value=vision)
            task.capture_frame = Mock(return_value=np.zeros((1080, 1920, 3), np.uint8))
            vision.threshold_for.return_value = 0.78
            vision.match_all.return_value = [
                SimpleNamespace(center=(150, y), score=0.95, pixel_score=0.94)
                for y, present in ((240, gold_bonus), (310, exp_bonus))
                if present
            ]
            observed = task._quick_hunt_double_states()
            self.assertEqual({"金币": gold_bonus, "经验": exp_bonus}, observed)
            self.assertEqual("Double.png", vision.match_all.call_args.args[1].file_name)
            _, selected = select_steps(preset_plans()[0], observed)
            self.assertEqual(targets, [s["target"] for s in selected])
        with self.assertRaisesRegex(ValueError, "无法确认"):
            select_steps(preset_plans()[0], {"金币": None, "经验": False})

    def test_scheduler_continues_only_current_step_after_budget_cancel_is_confirmed(self):
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                self.reset_stage_history()
                task = plan_task([step("hunt", "count", 1), step("gold")], [89, 89, 83, 84])
                task._quick_hunt_select_plan_target = Mock(return_value=True)
                task._quick_hunt_click_ocr = Mock(return_value=True)
                task._quick_hunt_wait_confirmation_dialog = Mock(return_value=True)
                task._quick_hunt_wait_map_confirmation = Mock(
                    return_value=("matched", "哥布林遗迹极难", "哥布林遗迹")
                )
                task._quick_hunt_prepare_dialog = Mock(
                    side_effect=lambda mode, *_: (
                        "budget_limited"
                        if mode == "MAX"
                        else SimpleNamespace(
                            start_point=(1050, 785),
                            frame_shape=(1080, 1920, 3),
                            map_name="哥布林遗迹极难",
                            count=1,
                            cost=6,
                            pool=90,
                        )
                    )
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
                    wraps=QuickHuntTask._quick_hunt_execute_current_map.__get__(task)
                )
                self.assertEqual(cancelled, task._quick_hunt_run_rice_scheduler())
                calls = task._quick_hunt_execute_current_map.call_args_list
                self.assertEqual(
                    ["MIN", "MAX"] + (["MIN"] * 13 if cancelled else []),
                    [call.args[0] for call in calls],
                )
                self.assertEqual(83, calls[1].kwargs["max_cost"])
                self.assertEqual(14 if cancelled else 1, vision.click_client.call_count)
                selected = task._quick_hunt_select_plan_target.call_args_list
                self.assertEqual(
                    ["hunt"] + ["gold"] * (14 if cancelled else 1),
                    [call.args[0] for call in selected],
                )
                task._quick_hunt_cancel_dialog.assert_called_once()

    def test_plan_resume_keeps_step_identity_and_does_not_repeat_uncertain_spending(self):
        plan = {
            "name": "接续",
            "reserve": 0,
            "rules": [],
            "fallback": [step("hunt", "count", 2), step("hunt", "count", 1), step("gold")],
        }
        task = object.__new__(QuickHuntTask)
        task.config = {PLAN_KEY: json.dumps({"version": 1, "active": 0, "plans": [plan]})}
        task.info_set = task.log_info = Mock()
        balance = [90]
        task._quick_hunt_free_resource = Mock(side_effect=lambda resource: balance[0])
        task._quick_hunt_free_rice = Mock(side_effect=lambda: balance[0])
        task._quick_hunt_select_plan_target = Mock(return_value=True)
        task._quick_hunt_click_ocr = Mock(return_value=True)
        task._quick_hunt_wait_confirmation_dialog = Mock(return_value=True)
        task.capture_frame = Mock(return_value=object())
        task._quick_hunt_dialog_ocr = Mock(return_value=(True, "MIN MAX 哥布林遗迹", "狩猎6"))
        task._quick_hunt_wait_map_confirmation = Mock(wraps=task._quick_hunt_wait_map_confirmation)
        submission = []

        def prepare(mode, *args):
            cost = balance[0] if mode == "MAX" else 6
            value = hunt_dialog(map_name="目标", count=cost // 6, cost=cost, pool=balance[0])
            submission[:] = [value]
            return value

        task._quick_hunt_prepare_dialog = Mock(side_effect=prepare)
        clicks = []

        def spend(*args, **kwargs):
            key, _, _ = task._hunt_checkpoint
            durable = run_history.RunHistoryStore(self.history.path).stage("QuickHuntTask", key)
            self.assertEqual("pending", durable["state"])
            self.assertEqual(("米饭", submission[0].cost), (
                durable["evidence"]["resource"], durable["evidence"]["cost"]))
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
        original_scope = run_history.read_stage(task, "狩猎方案")["scope"]
        task.config[PLAN_KEY] = json.dumps(
            {
                "version": 1,
                "active": 0,
                "plans": [
                    {"name": "新方案", "reserve": 0, "rules": [], "fallback": [step("gold")]}
                ],
            }
        )
        task.config["快速狩猎 OCR 阈值"] = 0.4
        self.assertFalse(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([6, 6], clicks)
        unknown[0] = False
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([6, 6, 6, 72], clicks)
        self.assertTrue(task._quick_hunt_run_rice_scheduler())
        self.assertEqual([6, 6, 6, 72], clicks)
        self.assertEqual(0, balance[0])
        task._quick_hunt_wait_map_confirmation.assert_called_once()
        self.assertEqual(r"哥布林遗迹", task._quick_hunt_wait_map_confirmation.call_args.args[0])
        self.assertEqual(original_scope, run_history.read_stage(task, "狩猎方案")["scope"])

    def test_map_identity_and_cost_are_rechecked_after_quantity_clicks(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        for after, cost, lost, accepted in (
            ("黑暗洞穴·极难", 60, False, True),
            (" 黑暗洞穴 · 极难 ", 60, True, True),
            ("火之洞穴·极难", 60, False, False),
            ("黑暗洞穴·普通", 60, False, False),
            ("黑暗洞穴·极难2", 60, False, False),
            ("· ，", 60, False, False),
            ("黑暗洞穴·极难", 66, True, False),
        ):
            with self.subTest(after=after, cost=cost, lost=lost):
                task = hunt_task(快速狩猎界面等待秒数=5.0)
                task._quick_vision = Mock()
                task._quick_hunt_free_resource = Mock(return_value=60)
                task._quick_hunt_box_enabled = Mock(return_value=True)
                initial = dialog_snapshot(
                    task, frame, ["仅使用免费火把"], "火把", "on", "黑暗洞穴，极难"
                )
                final = dialog_snapshot(task, frame, ["仅使用免费火把"], "火把", "on", after, 10)
                if final is not None:
                    final = replace(final, cost=cost)
                self.assertEqual("黑暗洞穴极难", initial.map_name)
                clock, current = FakeClock(), [initial]
                task.sleep = clock.sleep
                task._executor = SimpleNamespace(
                    check_enabled=lambda: None,
                    reset_scene=Mock(),
                    exit_event=SimpleNamespace(wait=clock.sleep),
                )
                task.capture_frame = Mock(return_value=frame)
                task._quick_hunt_dialog_snapshot = lambda *_: current[0]

                def select(*args, **kwargs):
                    if task._quick_hunt_click_ocr.call_count == 1:
                        task._wait_for_input(15.0)
                    if not lost or task._quick_hunt_click_ocr.call_count > 1:
                        current[0] = final
                    clock.sleep(0.8)
                    return True

                task._quick_hunt_click_ocr = Mock(side_effect=select)
                with (
                    patch("src.tasks.quick_hunt.monotonic", clock),
                    patch("src.tasks.BaseBD2Task.monotonic", clock),
                ):
                    result = task._quick_hunt_prepare_dialog(
                        "MAX", "圣石", r"暗.?之?.?洞穴", "火把", 60
                    )
                self.assertEqual(
                    (10, 60, 60) if accepted else None,
                    (result.count, result.cost, result.pool) if result else None,
                )
                self.assertLessEqual(task._quick_hunt_click_ocr.call_count, 3)
                self.assertGreaterEqual(task._quick_hunt_click_ocr.call_count, 2 if lost else 1)
                task._quick_vision().click_client.assert_not_called()
                self.assertLess(clock.now, 22)

    def test_quick_hunt_submission_requires_free_switch_and_stable_affordable_cost(self):
        rice = hunt_dialog()
        torch = hunt_dialog(map_name="火之洞穴极难", pool=60, resource="火把")
        rice_max = replace(rice, count=15, cost=90)
        cases = (
            ("免费开关未知", "MIN", [replace(rice, free_state="unknown")], 90, "failed"),
            (
                "免费开关关闭后开启",
                "MIN",
                [replace(rice, free_state="off")] * 2 + [rice] * 4,
                90,
                "done",
            ),
            ("米饭单次成本错误", "MIN", [replace(rice, cost=5)], 90, "failed"),
            ("超出本轮额度", "MIN", [rice], 5, "failed"),
            ("MAX点击未生效", "MAX", [rice], 90, "failed"),
            (
                "MAX余额与成本未稳定",
                "MAX",
                [rice] * 4 + [rice_max, replace(rice_max, pool=89)] * 8,
                90,
                "failed",
            ),
            (
                "MAX弹窗米饭83到84",
                "MAX",
                [replace(rice, pool=83)] * 4 + [replace(rice, pool=84)],
                83,
                "budget_limited",
            ),
            ("米饭MAX", "MAX", [rice] * 4 + [rice_max] * 2, 90, "done"),
            ("火把极难单次6", "MIN", [torch], 60, "done"),
            (
                "火把极难MAX十次",
                "MAX",
                [torch] * 4 + [replace(torch, count=10, cost=60)] * 2,
                60,
                "done",
            ),
        )
        for name, mode, snapshots, max_cost, expected in cases:
            with self.subTest(name=name):
                self.reset_stage_history()
                task = hunt_task(快速狩猎界面等待秒数=2.5)
                clock, vision = with_dialog_clock(task, object())
                sequence = iter(snapshots)
                task._quick_hunt_dialog_snapshot = Mock(
                    side_effect=lambda *_: next(sequence, snapshots[-1])
                )
                task._quick_hunt_click_ocr = Mock(return_value=True)
                task._quick_hunt_cancel_dialog = Mock(return_value=True)
                task._quick_hunt_wait_result = Mock(return_value="done")

                with patch("src.tasks.quick_hunt.monotonic", new=clock):
                    result = task._quick_hunt_execute_current_map(
                        mode, name, resource=snapshots[0].resource, max_cost=max_cost
                    )
                self.assertEqual(expected, result)
                start_clicks = [
                    call
                    for call in vision.click_client.call_args_list
                    if call.args[0] == rice.start_point
                ]
                self.assertEqual(int(expected == "done"), len(start_clicks))
                if expected == "done":
                    submission = task._quick_hunt_wait_result.call_args.kwargs["submission"]
                    self.assertLessEqual(submission.cost, max_cost)
                    self.assertEqual(snapshots[-1].cost, submission.cost)
                else:
                    task._quick_hunt_wait_result.assert_not_called()
                    task._quick_hunt_cancel_dialog.assert_called_once()

    def test_quick_hunt_adventure_map_mismatch_cancels_before_consuming_rice(self):
        for target, wrong_map, menu_restored in (
            ("冒险航线", "野猪洞穴", True),
            ("冒险航线", "野猪洞穴", False),
            ("水属性圣石", "火之洞穴", True),
        ):
            with self.subTest(target=target, menu_restored=menu_restored):
                task = hunt_task(快速狩猎界面等待秒数=3., 快速狩猎圣石目标="水")
                clock, vision = with_dialog_clock(task, np.zeros((1080, 1920, 3), np.uint8))
                menu_frames = iter((False, True, True) if menu_restored else (False,))
                task._quick_hunt_menu_still_open = Mock(
                    side_effect=lambda _frame: next(menu_frames, menu_restored)
                )
                task._quick_hunt_dialog_ocr = Mock(
                    return_value=(True, f"MIN MAX 取消 {wrong_map}极难", "狩猎6")
                )
                cancel = SimpleNamespace(name="取消", x=760, y=770, width=80, height=30)
                vision.ocr_boxes.return_value = [cancel]
                click_calls = []
                task._quick_hunt_click_ocr = lambda patterns, roi, timeout, name, **kwargs: (
                    click_calls.append((patterns, name)) or True
                )
                task._quick_hunt_enter_crystal_cave = Mock(return_value=True)
                task._quick_hunt_free_resource = Mock(return_value=60)
                task._quick_hunt_wait_map_confirmation = Mock(
                    wraps=task._quick_hunt_wait_map_confirmation
                )
                task._quick_hunt_wait_result = Mock(
                    side_effect=AssertionError("错误地图不得开始狩猎")
                )

                with patch("src.tasks.quick_hunt.monotonic", clock):
                    if target == "冒险航线":
                        self.assertEqual(
                            "wrong_map" if menu_restored else "failed",
                            task._quick_hunt_execute_current_map(
                                "MAX", target, expected_map_pattern=r"哥布林遗迹"
                            ),
                        )
                    else:
                        self.assertFalse(task._quick_hunt_run_crystal_cave())
                        pattern = task._quick_hunt_wait_map_confirmation.call_args.args[0]
                        self.assertRegex("水之洞穴", pattern)
                        self.assertIsNone(re.search(pattern, wrong_map))
                self.assertGreater(vision.click_client.call_count, 0)
                for call in vision.click_client.call_args_list:
                    self.assertEqual((800, 785), call.args[0])
                self.assertFalse(
                    any(
                        name in (f"{target}-MAX", f"{target}-开始狩猎")
                        for _patterns, name in click_calls
                    )
                )
                task._quick_hunt_wait_result.assert_not_called()

    def test_quick_hunt_reward_must_close_and_return_to_menu_before_finishing(self):
        for case in (
            (("reward", "menu", "menu"), "done", 1),
            (("reward", "reward", "transition", "menu", "menu"), "done", 2),
            (("reward_title", "menu", "menu"), "done", 1),
            (("reward", "dim_menu"), "failed", 1),
            (("reward", "bright_noise"), "failed", 1),
            (("reward", "menu", "transition"), "failed", 1),
            (("reward", "menu", "transition", "menu", "menu"), "done", 1),
            (("reward",), "failed", 3),
            (("reward", "transition"), "failed", 1),
            (("depleted", "menu", "menu"), "depleted", 1),
            (("depleted",), "failed", 3),
            (("ambiguous_depleted",), "failed", 0),
            (("missing_depleted",), "failed", 0),
            (("depleted", "dim_menu"), "failed", 1),
            (("menu",), "failed", 0),
            ((*(["transition"] * 16), "reward", "menu", "menu"), "done", 1),
            ((*(["unchanged"] * 9), "reward", "menu", "menu"), "done", 1, 1),
            (("unchanged",), "failed", 0, 2),
            (("transition", "unchanged"), "failed", 0, 0),
            (("spent", "unchanged"), "failed", 0, 0),
            (("changed_count", "unchanged"), "failed", 0, 0),
            (("transition", "reward", "menu", "menu"), "done", 1, 0),
            (("reward", "unchanged"), "failed", 1, 0),
            (("unknown_error",), "failed", 0, 0),
            (("wrong_resource",), "failed", 0, 0),
        ):
            frames, expected, click_count = case[:3]
            submission = hunt_dialog() if len(case) == 4 else None
            expected_start_retries = case[3] if submission else 0
            with self.subTest(frames=frames):
                task, vision, clock = settlement_task(frames, submission)
                if submission is not None:
                    from src.tasks import run_history

                    self.reset_stage_history()
                    scope = run_history.stage_scope(task)
                    evidence = {"phase": "attempted", "start_clicks": 1, "resource": "米饭"}
                    run_history.save_stage(
                        task, "狩猎提交:米饭:金币 MAX", "pending", scope=scope, evidence=evidence
                    )
                    task._hunt_checkpoint = ("狩猎提交:米饭:金币 MAX", scope, evidence)
                with patch("src.tasks.quick_hunt.monotonic", side_effect=clock):
                    self.assertEqual(
                        expected, task._quick_hunt_wait_result("金币 MAX", submission=submission)
                    )
                start_clicks = [
                    call
                    for call in vision.click_client.call_args_list
                    if call.args[0] == hunt_dialog().start_point
                ]
                self.assertEqual(expected_start_retries, len(start_clicks))
                if any("depleted" in frame for frame in frames):
                    self.assertEqual(click_count, vision.click_client.call_count)
                    self.assertEqual(
                        [(95, 76)] * click_count,
                        [c.args[0] for c in vision.click_client.call_args_list],
                    )
                    task.operate_click.assert_not_called()
                else:
                    self.assertEqual(click_count, task.operate_click.call_count)
                for click in task.operate_click.call_args_list:
                    self.assertAlmostEqual(956 / 1920, click.args[0])
                    self.assertAlmostEqual(992 / 1080, click.args[1])
                if expected == "failed":
                    task._save_flow_diagnostic.assert_called_once()
                    self.assertLessEqual(clock.now, 5.3)
                else:
                    task._save_flow_diagnostic.assert_not_called()

    def test_run_starts_at_home_and_unwinds_after_rice_and_crystal(self):
        from ok.task.exceptions import FinishedException

        for at_home, opens, rice, expected in (
            (False, False, True, False),
            (True, False, True, False),
            (True, True, True, True),
            (True, True, False, False),
            (True, True, TaskDisabledException, None),
            (True, True, FinishedException, None),
            (True, True, RuntimeError, None),
        ):
            with self.subTest(home=at_home, opens=opens, rice=rice):
                task = hunt_task(快速狩猎圣石洞穴=True)
                task._wait_for_quick_hunt_home = Mock(return_value=at_home)
                task.capture_frame = Mock(return_value=np.zeros((720, 1280, 3), np.uint8))
                task._quick_hunt_home_signals = Mock(return_value=(False, 0, 120.0, "-"))
                task._quick_hunt_click_ocr = Mock(return_value=True)
                task._quick_hunt_wait_ocr = Mock(return_value=("狩猎场" if opens else "", None))
                task.operate_click = Mock()
                task._quick_hunt_entry_red_state = Mock(
                    return_value=(True, (1188, 158), (0, 0, 255), (0, 255, 255))
                )
                self.assertTrue(task._quick_hunt_inspect_entry())
                task.operate_click.assert_not_called()
                task._quick_hunt_click_ocr.assert_not_called()
                task._quick_hunt_run_rice_scheduler = Mock(
                    return_value=rice, side_effect=rice if expected is None else None
                )
                task._quick_hunt_run_crystal_cave = Mock(return_value=True)
                task._quick_hunt_return_home = Mock(return_value=True)
                if expected is None:
                    with self.assertRaises(rice):
                        task.run_quick_hunt()
                else:
                    self.assertEqual(expected, task.run_quick_hunt())
                self.assertEqual(int(at_home), task._quick_hunt_click_ocr.call_count)
                self.assertEqual(int(opens), task._quick_hunt_run_rice_scheduler.call_count)
                self.assertEqual(
                    int(opens and rice is True), task._quick_hunt_run_crystal_cave.call_count
                )
                self.assertEqual(
                    opens and rice not in (FinishedException, TaskDisabledException),
                    task._quick_hunt_return_home.called,
                )
                task.operate_click.assert_not_called()


class QuickHuntRecoveryTest(StageHistoryTestCase):
    def setUp(self):
        super().setUp()
        from src.tasks import run_history

        self.day = run_history.day_start_ts()
        self._reset_hunt()
        self.enterContext(patch("src.tasks.quick_hunt.time", lambda: self.wall + self.clock.now))
        self.enterContext(
            patch("src.tasks.run_history.time.time", lambda: self.wall + self.clock.now)
        )

    def _reset_hunt(self):
        self.reset_stage_history()
        self.clock, self.wall = FakeClock(), self.day + 3600
        task = self.task = object.__new__(QuickHuntTask)
        task.config = {"快速狩猎结算等待秒数": 1.0, "快速狩猎界面等待秒数": 1.0}
        task.info_set = task.log_info = Mock()
        task._active_time = lambda clock: self.clock.now
        task.sleep = self.clock.sleep
        task.capture_frame = Mock(return_value=object())
        task._quick_hunt_is_menu_restored = Mock(return_value=True)
        task._quick_hunt_free_resource = Mock(return_value=90)
        task._quick_hunt_settlement_signals = Mock(return_value=(False, False))
        task._quick_hunt_open_confirmation = Mock(return_value=True)
        task._quick_hunt_prepare_dialog = Mock(return_value=hunt_dialog())
        task._quick_hunt_dialog_snapshot = Mock(return_value=None)
        task._quick_vision = Mock(return_value=SimpleNamespace(click_client=Mock()))
        task._save_flow_diagnostic = Mock()
        task.operate_click = Mock(
            side_effect=lambda *_, after_sleep=0: self.clock.sleep(after_sleep)
        )

    def _seed(self, step="金币 MIN", *, days=0, resource="米饭", at=None, evidence=None):
        from src.tasks import run_history

        cycle = self.day + days * 86400
        at = cycle + 1800 if at is None else at
        scope = f"{cycle}:{run_history.configuration_scope(self.task)}"
        data = {
            "map": "哥布林遗迹极难",
            "count": 1,
            "cost": 6,
            "pool": 90,
            "resource": resource,
            "phase": "attempted",
            "start_clicks": 1,
            "prepared_at": at,
            **(evidence or {}),
        }
        key = f"狩猎提交:{resource}:{step}"
        with patch("src.tasks.run_history.time.time", return_value=at):
            self.history.save_stage("QuickHuntTask", key, "pending", scope, data)
        return key, self.history.stage("QuickHuntTask", key)

    def _retirement_record(self, case):
        self._reset_hunt()
        at = self.day - .5 if case == "just_crossed" else None
        if at is not None:
            self.wall = self.day + .1
        days = {"future": 1, "ambiguous_rewards": 0, "unsent": 0}.get(case, -1)
        key, record = self._seed(
            "旧版金币 MAX" if case == "legacy" else "金币 MIN", days=days, at=at,
            evidence={"phase": "prepared", "start_clicks": 0} if case == "unsent" else {},
        )
        if case == "ambiguous_rewards":
            self._seed(resource="火把")
            self.task._quick_hunt_settlement_signals.return_value = (True, False)
        if case in ("invalid_scope", "time_conflict", "wrong_resource"):
            group = self.history._stages["QuickHuntTask"][key]
            if case == "invalid_scope":
                group["scope"] = "unknown"
            elif case == "time_conflict":
                group["evidence"]["prepared_at"] = self.day + 1
            else:
                group["evidence"]["resource"] = "火把"
            self.history._save()
            record = self.history.stage("QuickHuntTask", key)
        if case == "unknown_balance":
            self.task._quick_hunt_free_resource.return_value = None
        elif case == "changing_balance":
            self.task._quick_hunt_free_resource.side_effect = [89, 90]
        elif case == "not_menu":
            self.task._quick_hunt_is_menu_restored.return_value = False
        elif case == "active":
            self.task._hunt_checkpoint = (key, record["scope"], record["evidence"])
        return key, record

    def test_pending_retirement_requires_unambiguous_resource_cycle_and_menu(self):
        for case in (
            "future", "invalid_scope", "time_conflict", "unknown_balance", "changing_balance",
            "not_menu", "active", "just_crossed", "wrong_resource", "archive_write_failure",
            "ambiguous_rewards", "expired", "legacy", "unsent",
        ):
            with self.subTest(case=case):
                key, record = self._retirement_record(case)
                if case in {"expired", "legacy", "unsent"}:
                    other, torch = self._seed(resource="火把")
                    self.assertEqual(
                        "failed", self.task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                    )
                    self.task._quick_vision().click_client.assert_called_once()
                    with open(self.history.path, encoding="utf-8") as stream:
                        self.assertEqual(record, json.load(stream)["retired_stages"][-1]["record"])
                    self.assertEqual(torch, self.history.stage("QuickHuntTask", other))
                    new = self.history.stage("QuickHuntTask", "狩猎提交:米饭:金币 MIN")
                    self.assertTrue(new["scope"].startswith(str(self.day) + ":"))
                    self.assertEqual(
                        (1, "transitioned"),
                        (new["evidence"]["start_clicks"], new["evidence"]["phase"]),
                    )
                    if case == "legacy":
                        self.assertFalse(self.history.stage("QuickHuntTask", key))
                    continue
                if case == "archive_write_failure":
                    with patch.object(self.history, "_save", return_value=False):
                        with self.assertRaises(RuntimeError):
                            self.task._quick_hunt_reconcile_pending("米饭")
                else:
                    self.assertFalse(self.task._quick_hunt_reconcile_pending("米饭"))
                self.assertEqual(record, self.history.stage("QuickHuntTask", key))
                self.task._quick_hunt_open_confirmation.assert_not_called()
                self.task._quick_vision.assert_not_called()

        for balance, reward, expected in (
            (84, False, "failed"),
            (84, True, "done"),
            (0, False, "depleted"),
        ):
            with self.subTest(balance=balance, reward=reward):
                self._reset_hunt()
                key, original = self._seed(evidence={"start_clicks": 3})
                self.task.config["快速狩猎 OCR 阈值"] = 0.4
                self.task.config["快速狩猎界面等待秒数"] = 2.0
                self.task._quick_hunt_free_resource.return_value = balance
                self.task._quick_hunt_settlement_signals.side_effect = lambda *_: (
                    reward and not self.task.operate_click.called,
                    False,
                )
                self.assertEqual(
                    expected, self.task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                )
                self.history_mock.return_value = run_history.RunHistoryStore(self.history.path)
                self.task._hunt_checkpoint = None
                again = self.task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                self.assertEqual("done" if expected == "depleted" else expected, again)
                record = self.history_mock.return_value.stage("QuickHuntTask", key)
                self.assertEqual(original["scope"], record["scope"])
                self.assertEqual(3, record["evidence"]["start_clicks"])
                self.task._quick_hunt_open_confirmation.assert_not_called()
                self.task._quick_vision.assert_not_called()

    def test_dispatch_failures_reload_only_durable_click_attempts(self):
        from src.tasks.run_history import RunHistoryStore

        for failure, attempt, unsent, expected_clicks, stored in (
            ("none", 0, False, 3, 3),
            ("write", 1, False, 0, None),
            ("write", 2, False, 0, 0),
            ("write", 3, False, 1, 1),
            ("input", 1, True, 1, 0),
            ("input", 1, False, 1, 1),
            ("input", 2, True, 2, 2),
        ):
            with self.subTest(failure=failure, attempt=attempt, unsent=unsent):
                self._reset_hunt()
                task = self.task
                task.config["快速狩猎结算等待秒数"] = 5.0
                task._quick_hunt_dialog_snapshot.return_value = hunt_dialog()
                click = task._quick_vision().click_client
                save = self.history._save
                writes = 0

                def persist():
                    nonlocal writes
                    writes += 1
                    return False if failure == "write" and writes == attempt else save()

                def send(*args, **kwargs):
                    record = RunHistoryStore(self.history.path).stage(
                        "QuickHuntTask", "狩猎提交:米饭:金币 MIN"
                    )
                    self.assertEqual(
                        ("pending", click.call_count, 6),
                        (
                            record["state"],
                            record["evidence"]["start_clicks"],
                            record["evidence"]["cost"],
                        ),
                    )
                    if failure == "input" and click.call_count == attempt:
                        error = RuntimeError("input failed")
                        error.input_not_dispatched = unsent
                        raise error

                click.side_effect = send
                with patch.object(self.history, "_save", side_effect=persist):
                    if failure == "none":
                        self.assertEqual(
                            "failed", task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                        )
                    else:
                        with self.assertRaises(RuntimeError):
                            task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                self.assertEqual(expected_clicks, click.call_count)
                store = RunHistoryStore(self.history.path)
                record = store.stage("QuickHuntTask", "狩猎提交:米饭:金币 MIN")
                self.assertEqual(stored, record.get("evidence", {}).get("start_clicks"))
                if record:
                    self.history_mock.return_value = store
                    task._hunt_checkpoint = None
                    task._quick_hunt_open_confirmation.reset_mock()
                    click.reset_mock(side_effect=True)
                    task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                    self.assertEqual(stored == 0, click.called)
                    self.assertEqual(stored == 0, task._quick_hunt_open_confirmation.called)

    def test_late_cleanup_settles_before_close_but_cancellation_does_not_invent_completion(self):
        for case in ("reward", "cross_cycle", "cancel", "unbound", "write_failure", "unknown"):
            with self.subTest(case=case):
                self._reset_hunt()
                self.assertEqual(
                    "failed", self.task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                )
                key = "狩猎提交:米饭:金币 MIN"
                original = self.history.stage("QuickHuntTask", key)
                if case == "cross_cycle":
                    self.wall += 86400
                if case == "unbound":
                    self.task._hunt_checkpoint = None
                home = [False]
                self.task._home_p95_threshold = lambda: 185
                self.task._quick_hunt_current_map_context = lambda frame: "哥布林遗迹"
                self.task._quick_hunt_home_signals = lambda frame: (home[0], 3, 255, "抽抽乐")
                self.task.dismiss_home_announcement = Mock(return_value=False)
                self.task._quick_hunt_ocr_text = lambda frame, roi, name: (
                    "REWARD"
                    if case not in {"cancel", "unknown"} and roi == QUICK_HUNT_REWARD_TITLE_ROI
                    else ""
                )
                self.task._quick_hunt_menu_still_open = lambda frame: False
                self.task._quick_hunt_dialog_ocr = lambda frame, name: (case != "unknown", "", "")

                def close(*args, **kwargs):
                    state = self.history.stage("QuickHuntTask", key)["state"]
                    self.assertEqual("pending" if case in ("cancel", "unbound") else "done", state)
                    home[0] = True
                    return True

                self.task.operate_click = Mock(side_effect=close)
                self.task._quick_hunt_cancel_dialog = Mock(side_effect=close)
                if case == "write_failure":
                    with patch.object(self.history, "_save", return_value=False):
                        with self.assertRaises(RuntimeError):
                            self.task._quick_hunt_return_home()
                    self.task.operate_click.assert_not_called()
                else:
                    self.assertEqual(case != "unknown", self.task._quick_hunt_return_home())
                    if case == "unknown":
                        self.task.operate_click.assert_not_called()
                        self.task._quick_hunt_cancel_dialog.assert_not_called()
                record = self.history.stage("QuickHuntTask", key)
                self.assertEqual(original["scope"], record["scope"])
                self.assertEqual(
                    "done" if case in ("reward", "cross_cycle") else "pending", record["state"]
                )
                self.assertIsNone(self.task._hunt_checkpoint)

    def test_plan_cycle_validation_and_late_finish_preserve_the_original_allowance(self):
        from src.tasks import run_history

        plan = {
            "name": "一次",
            "reserve": 0,
            "rules": [],
            "fallback": [{"target": "hunt", "mode": "count", "count": 1}],
        }
        for scope in ("invalid", f"{self.day + 86400}:0123456789abcdef"):
            with self.subTest(scope=scope):
                self._reset_hunt()
                self.task.config[PLAN_KEY] = json.dumps(
                    {"version": 1, "active": 0, "plans": [plan]}
                )
                run_history.save_stage(
                    self.task, "狩猎方案", "pending", scope=scope, evidence={"allowance": 6}
                )
                original = run_history.read_stage(self.task, "狩猎方案")
                self.assertFalse(self.task._quick_hunt_run_rice_scheduler())
                self.assertEqual(original, run_history.read_stage(self.task, "狩猎方案"))
                self.task._quick_hunt_free_resource.assert_not_called()
        self._reset_hunt()
        self.task.config[PLAN_KEY] = json.dumps({"version": 1, "active": 0, "plans": [plan]})
        self.task._quick_hunt_select_plan_target = Mock(return_value=True)

        def finish(*args, **kwargs):
            self.wall += 86400
            self.task._quick_hunt_settle_submission("done")
            return "done"

        self.task._quick_hunt_wait_result = Mock(side_effect=finish)
        for days in (0, 1):
            self.assertTrue(self.task._quick_hunt_run_rice_scheduler())
            record = run_history.read_stage(self.task, "狩猎方案")
            self.assertEqual("done", record["state"])
            self.assertEqual(self.day + days * 86400, self.task._quick_hunt_record_cycle(record))
            self.assertEqual(90, record["evidence"]["allowance"])
        self.assertEqual(2, self.task._quick_vision().click_client.call_count)
        for during_dialog in (False, True):
            with self.subTest(during_dialog=during_dialog):
                self._reset_hunt()
                if during_dialog:

                    def prepare(*args):
                        self.wall += 86400
                        return hunt_dialog()

                    self.task._quick_hunt_prepare_dialog.side_effect = prepare
                    self.task._quick_hunt_cancel_dialog = Mock(return_value=True)
                else:
                    self.task._hunt_plan_scope = (
                        f"{self.day - 86400}:{run_history.configuration_scope(self.task)}"
                    )
                self.assertEqual(
                    "failed", self.task._quick_hunt_execute_current_map("MIN", "金币 MIN")
                )
                self.task._quick_vision.assert_not_called()
                self.assertFalse(self.history.pending_stages("QuickHuntTask", "狩猎提交:"))
                if during_dialog:
                    self.task._quick_hunt_cancel_dialog.assert_called_once()
                else:
                    self.task._quick_hunt_open_confirmation.assert_not_called()


class HomePopupRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.task = object.__new__(BaseBD2Task)
        self.task.info_set = Mock()
        self.task.log_info = Mock()
        self.task.operate_click = Mock()
        self.task.sleep = Mock()
        self.frame = np.full((540, 960, 3), 24, dtype=np.uint8)
        self.frame[130:420, 240:750] = 210
        self.assertIsNotNone(modal_panel_bbox(self.frame))

    def test_cancelled_click_leaves_room_to_retry_using_a_fresh_frame(self):
        self.task.operate_click.side_effect = [InputTargetChanged(), None]
        with patch(
            "src.tasks.BaseBD2Task.modal_close_point", side_effect=[(0.75, 0.25), (0.76, 0.26)]
        ):
            self.assertTrue(self.task.dismiss_home_modal_popup(self.frame))
            self.assertTrue(self.task.dismiss_home_modal_popup(self.frame.copy()))
        self.assertEqual(
            [
                call(0.75, 0.25, name="主页弹窗关闭", after_sleep=0.6, require_fresh_target=True),
                call(0.76, 0.26, name="主页弹窗关闭", after_sleep=0.6, require_fresh_target=True),
            ],
            self.task.operate_click.call_args_list,
        )
        self.assertEqual(1, self.task._home_modal_panel_repeats)


class QuickHuntInputRecoveryTest(unittest.TestCase):
    @staticmethod
    def box(x):
        return SimpleNamespace(name="光之洞穴", x=x, y=10, width=20, height=10)

    def setUp(self):
        self.task = QuickHuntFeatureMixin()
        self.vision = SimpleNamespace(ocr_boxes=Mock(), click_client=Mock())
        self.task._quick_vision = lambda: self.vision
        self.task._normalize_text = lambda text: text
        self.task._save_flow_diagnostic = Mock()
        self.task.log_info = Mock()
        self.frames = [np.zeros((300, 600, 3), dtype=np.uint8) for _ in range(2)]
        self.task.capture_frame = Mock(side_effect=self.frames)
        self.task._active_time = Mock(side_effect=[0.0, 0.0, 0.4, 1.1])
        self.task.sleep = Mock()

    def test_changed_target_requires_a_new_frame_and_a_current_control(self):
        for visible in (True, False):
            with self.subTest(visible=visible):
                frames = [np.zeros((300, 600, 3), np.uint8) for _ in range(2)]
                self.task.capture_frame = Mock(side_effect=frames)
                self.task._active_time = Mock(side_effect=[0.0, 0.0, 0.4, 1.1])
                self.vision.ocr_boxes.side_effect = [
                    [self.box(40), self.box(90)],
                    [self.box(120)] if visible else [],
                ]
                self.vision.click_client = Mock(side_effect=[InputTargetChanged(), None])
                self.assertFalse(QuickHuntTask._quick_hunt_box_enabled(frames[0], self.box(40)))
                frames[0][10:20, 40:60] = 255
                self.assertTrue(QuickHuntTask._quick_hunt_box_enabled(frames[0], self.box(40)))
                self.assertEqual(
                    visible, self.task._quick_hunt_click_ocr(["光之洞穴"], None, 1, "洞穴入口")
                )
                self.assertEqual(2, self.task.capture_frame.call_count)
                points = [(50, 15)] + ([(130, 15)] if visible else [])
                self.assertEqual(
                    [call(pt, frames[0].shape, after_sleep=0.8) for pt in points],
                    self.vision.click_client.call_args_list,
                )
                self.assertEqual(int(not visible), self.task._save_flow_diagnostic.call_count)


if __name__ == "__main__":
    unittest.main()
