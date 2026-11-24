import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.tasks.FreeGachaTask import (
    BACK_PAGE_KEYWORDS,
    FREE_ENTRY_RETRY_BLOCKED,
    FREE_ENTRY_RETRY_LOADING,
    FREE_ENTRY_RETRY_READY,
    FREE_ENTRY_RETRY_STEP_UP,
    FREE_ENTRY_RETRY_UNKNOWN,
    RESULT_BACK_TEMPLATE,
    FreeGachaTask,
)
from src.tasks.map_trade.models import MatchResult
from tests.helpers import FakeClock, StageHistoryTestCase

free_gacha_module = importlib.import_module("src.tasks.FreeGachaTask")


def pool_boxes(pool="装备", free=False, scale=1, merged_header=False):
    rows = [
        (pool + "抽抽乐", 120, 20, 110, 30),
        ("抽抽乐记录", 240, 22, 100, 25),
        ("免费61,401", 760, 22, 120, 25),
        ("抽1次", 760, 700, 60, 20),
        ("抽10次.", 900, 700, 70, 20),
    ]
    if merged_header:
        rows[:2] = [(pool + "抽抽乐 抽抽乐记录", 120, 20, 220, 30)]
    if free:
        rows.extend([("所有免费抽抽乐", 100, 720, 200, 30), ("免费1次", 760, 730, 70, 20)])
    return [
        SimpleNamespace(name=name, x=x * scale, y=y * scale, width=w * scale, height=h * scale)
        for name, x, y, w, h in rows
    ]


def confirmation_boxes(pool="装备", count=3, cost=None, scale=1):
    rows = [
        ("确认抽抽乐", 420, 200, 160, 30),
        (f"目前有{count}次免费{pool}抽抽乐可以抽取。", 300, 270, 550, 30),
        ("是否全部进行？", 410, 330, 220, 30),
        (cost or f"免费{count}次", 550, 380, 90, 30),
        ("取消", 400, 430, 90, 30),
        ("确认", 650, 430, 90, 30),
        ("付费0 免费587143", 700, 20, 200, 30),
        ("付费抽抽乐90", 700, 700, 200, 30),
    ]
    return [
        SimpleNamespace(name=name, x=x * scale, y=y * scale, width=w * scale, height=h * scale)
        for name, x, y, w, h in rows
    ]


def step_up_boxes(scale=1, ownership=("未拥有",)):
    rows = [
        ("服装抽抽乐", 200, 20, 110, 30),
        ("step1抽抽乐", 840, 680, 80, 30),
        ("付费钻石90", 840, 710, 80, 20),
    ]
    rows.extend((label, 175 + index * 15, 305, 50, 30) for index, label in enumerate(ownership))
    return [
        SimpleNamespace(name=name, x=x * scale, y=y * scale, width=w * scale, height=h * scale)
        for name, x, y, w, h in rows
    ]


def gacha_task(**config):
    task = object.__new__(FreeGachaTask)
    task.config = config
    task.info_set = Mock()
    task.log_info = Mock()
    task.log_completion = Mock()
    task.operate_click = Mock()
    task._click_reference = Mock()
    task._sleep_after_recognition = Mock()
    task._passes = Mock(return_value=False)
    task._match = Mock(return_value=None)
    return task


class FreeGachaTaskHelperTest(StageHistoryTestCase):
    def test_paid_event_pool_uses_only_the_free_batch_entry(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        boxes = pool_boxes("服装")[:2]
        entry = SimpleNamespace(name="所有免费抽抽乐", x=242, y=948, width=194, height=36)
        for action in ("STEP1抽抽乐", "付费2,000"):
            paid = SimpleNamespace(name=action, x=1620, y=938, width=146, height=33)
            for footer, expected in [
                ([entry], FREE_ENTRY_RETRY_READY),
                ([entry, paid], FREE_ENTRY_RETRY_READY),
                ([paid], free_gacha_module.FREE_ENTRY_RETRY_NO_FREE),
                (
                    [entry, paid] + confirmation_boxes("服装", cost="付费2,000"),
                    free_gacha_module.FREE_ENTRY_RETRY_DIALOG,
                ),
            ]:
                with self.subTest(action=action, expected=expected):
                    observed = FreeGachaTask._pool_observation(frame, boxes + footer, "服装抽抽乐")
                    self.assertEqual(expected, observed.state)
                    self.assertEqual(
                        (339 / 1920, 966 / 1080) if expected == FREE_ENTRY_RETRY_READY else None,
                        observed.point,
                    )
        for pool in ("服装", "装备"):
            for scale in (0.72, 1, 2):
                for free in (False, True):
                    for merged in (False, True):
                        with self.subTest(pool=pool, scale=scale, free=free, merged=merged):
                            boxes = pool_boxes(pool, free, scale, merged)
                            single = next(box for box in boxes if box.name == "抽1次")
                            single.name = "。拍1次"
                            frame = np.zeros(
                                (round(800 * scale), round(1000 * scale), 3), dtype=np.uint8
                            )
                            observed = FreeGachaTask._pool_observation(
                                frame, boxes, pool + "抽抽乐"
                            )
                            self.assertEqual(
                                FREE_ENTRY_RETRY_READY
                                if free
                                else free_gacha_module.FREE_ENTRY_RETRY_NO_FREE,
                                observed.state,
                            )
                            if free:
                                self.assertAlmostEqual(0.2, observed.point[0])
                                self.assertAlmostEqual(0.91875, observed.point[1])
        frame = np.zeros((800, 1000, 3), dtype=np.uint8)
        for scenario in ("missing_ten", "misplaced_ten", "wrong_pool", "dialog", "duplicate"):
            with self.subTest(scenario=scenario):
                boxes = pool_boxes("服装", free=True)
                single = next(box for box in boxes if box.name == "抽1次")
                single.name = "拍1次"
                if scenario == "missing_ten":
                    boxes = [box for box in boxes if box.name != "抽10次."]
                elif scenario == "misplaced_ten":
                    next(box for box in boxes if box.name == "抽10次.").y = 100
                elif scenario == "dialog":
                    boxes += confirmation_boxes("服装", cost="消耗钻石90")
                elif scenario == "duplicate":
                    boxes.append(SimpleNamespace(name="抽1次", x=760, y=700, width=60, height=20))
                observed = FreeGachaTask._pool_observation(
                    frame, boxes, "装备抽抽乐" if scenario == "wrong_pool" else "服装抽抽乐"
                )
                self.assertNotIn(
                    observed.state,
                    (FREE_ENTRY_RETRY_READY, free_gacha_module.FREE_ENTRY_RETRY_NO_FREE),
                )

    def test_paid_step_up_banner_locates_the_free_clothing_banner(self):
        frame = np.zeros((800, 1000, 3), dtype=np.uint8)
        boxes = step_up_boxes()
        observed = FreeGachaTask._step_up_observation(frame, boxes, [box.name for box in boxes], "")
        self.assertEqual(FREE_ENTRY_RETRY_STEP_UP, observed.state)
        self.assertAlmostEqual(0.165, observed.point[0])
        self.assertAlmostEqual(0.373, observed.point[1])
        self.assertEqual(
            FREE_ENTRY_RETRY_STEP_UP,
            FreeGachaTask._pool_observation(frame, boxes, "服装抽抽乐").state,
        )
        self.assertNotEqual(
            FREE_ENTRY_RETRY_STEP_UP,
            FreeGachaTask._pool_observation(frame, boxes, "装备抽抽乐").state,
        )
        for dropped in ("服装抽抽乐", "step1抽抽乐", "付费钻石90"):
            with self.subTest(dropped=dropped):
                boxes = [box for box in step_up_boxes() if box.name != dropped]
                self.assertIsNone(
                    FreeGachaTask._step_up_observation(
                        frame, boxes, [box.name for box in boxes], ""
                    )
                )
        for ownership in ((), ("未拥有", "已拥有")):
            with self.subTest(ownership=ownership):
                boxes = step_up_boxes(ownership=ownership)
                observed = FreeGachaTask._step_up_observation(
                    frame, boxes, [box.name for box in boxes], ""
                )
                self.assertEqual(FREE_ENTRY_RETRY_STEP_UP, observed.state)
                self.assertIsNone(observed.point)
        frame = np.zeros((800, 1000, 3), dtype=np.uint8)
        boxes = step_up_boxes()
        step_up = FreeGachaTask._step_up_observation(frame, boxes, [box.name for box in boxes], "")
        task = object.__new__(FreeGachaTask)
        task.config = {}
        task.info_set = Mock()
        task.log_info = Mock()
        task.sleep = Mock()
        task.operate_click = Mock()
        task._observe_free_pool = Mock(
            side_effect=[step_up, step_up, SimpleNamespace(pool_confirmed=True, text="")]
        )
        self.assertTrue(task._wait_for_pool_page("服装抽抽乐", "确认服装池"))
        task.operate_click.assert_called_once()
        args, kwargs = task.operate_click.call_args
        self.assertAlmostEqual(0.165, args[0])
        self.assertAlmostEqual(0.373, args[1])
        self.assertEqual("切换普通服装横幅", kwargs.get("name"))
        self.assertEqual(0.8, kwargs.get("after_sleep"))

    def test_pool_scale_recheck_is_same_frame_and_cannot_erase_free_or_modal_evidence(self):
        for scale in (1, 0.72, 2):
            task = object.__new__(FreeGachaTask)
            task.config = {}
            task.info_set = Mock()
            task._match = Mock(return_value=None)
            task._passes = Mock(return_value=False)
            frame = np.zeros((round(800 * scale), round(1000 * scale), 3), dtype=np.uint8)
            task.capture_frame = Mock(return_value=frame)
            ready = pool_boxes(free=True, scale=scale)
            missing_record = ready[:1] + ready[2:]
            no_free = pool_boxes(scale=scale)
            for first, second, expected in [
                (missing_record, ready, FREE_ENTRY_RETRY_READY),
                (missing_record, ready[1:], FREE_ENTRY_RETRY_UNKNOWN),
                (missing_record, no_free, FREE_ENTRY_RETRY_UNKNOWN),
                (no_free, ready, FREE_ENTRY_RETRY_READY),
                (no_free, no_free, free_gacha_module.FREE_ENTRY_RETRY_NO_FREE),
                (no_free[:3], no_free[3:], FREE_ENTRY_RETRY_UNKNOWN),
                (no_free, pool_boxes("服装", scale=scale), FREE_ENTRY_RETRY_BLOCKED),
            ]:
                with self.subTest(scale=scale, expected=expected, first=[b.name for b in first]):
                    task.capture_frame.reset_mock()
                    task._gacha_ocr_boxes = Mock(side_effect=[first, second])
                    observed = task._observe_free_pool("装备抽抽乐", "test")
                    self.assertEqual(expected, observed.state)
                    task.capture_frame.assert_called_once()
                    self.assertTrue(
                        all(c.args[0] is frame for c in task._gacha_ocr_boxes.call_args_list)
                    )
                    if expected == FREE_ENTRY_RETRY_READY:
                        self.assertAlmostEqual(0.2, observed.point[0])
                        self.assertAlmostEqual(0.91875, observed.point[1])
            for boxes in (
                ready + confirmation_boxes(cost="消耗钻石90", scale=scale),
                ready + [SimpleNamespace(name="查看获取途径")],
                pool_boxes("服装", free=True, scale=scale),
            ):
                task._gacha_ocr_boxes = Mock(side_effect=[boxes, ready])
                self.assertNotEqual(
                    FREE_ENTRY_RETRY_READY, task._observe_free_pool("装备抽抽乐", "test").state
                )
                task._gacha_ocr_boxes.assert_called_once()

    def test_entry_recovers_observations_with_fixed_budgets_and_current_click_point(self):
        ready = (FREE_ENTRY_RETRY_READY, (0.23, 0.89))
        for states, expected, elapsed in [
            ([(FREE_ENTRY_RETRY_UNKNOWN, None), ready], True, 0.25),
            (
                [(FREE_ENTRY_RETRY_LOADING, None), (FREE_ENTRY_RETRY_UNKNOWN, None), ready],
                True,
                0.5,
            ),
            ([(FREE_ENTRY_RETRY_UNKNOWN, None)], False, 1.0),
            ([(free_gacha_module.FREE_ENTRY_RETRY_NO_FREE, None)], False, 1.0),
            ([(FREE_ENTRY_RETRY_LOADING, None)], False, 2.0),
            ([(FREE_ENTRY_RETRY_BLOCKED, None)], False, 0.0),
            ([("slow", None)], False, 1.1),
        ]:
            with self.subTest(states=states):
                task = object.__new__(FreeGachaTask)
                task.config = {
                    "免费抽按钮等待秒数": 1.0,
                    "loading 消失等待秒数": 2.0,
                    "确认弹窗重试次数": 0,
                }
                task._sleep_after_recognition = Mock()
                task.log_info = Mock()
                task.operate_click = Mock()
                task._wait_for_confirm_dialog = Mock(return_value=True)
                now = [0.0]
                pending = list(states)
                task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)

                def observe(_):
                    state = pending.pop(0) if len(pending) > 1 else pending[0]
                    if state[0] == "slow":
                        now[0] += 1.1
                        return ready
                    return state

                task._free_entry_retry_state = observe
                with patch.object(free_gacha_module, "monotonic", lambda: now[0]):
                    self.assertEqual(expected, task._open_confirm_dialog_with_retry("服装抽抽乐"))
                self.assertEqual(elapsed, now[0])
                if expected:
                    task.operate_click.assert_called_once_with(0.23, 0.89, after_sleep=0.5)
                else:
                    task.operate_click.assert_not_called()

    def test_submit_requires_two_fresh_equal_free_quotes_for_every_click(self):
        for pool in ("服装", "装备"):
            for scale in (0.72, 1, 2):
                good = confirmation_boxes(pool, scale=scale)
                other = "装备" if pool == "服装" else "服装"
                for scenario, sequence, still_open, success, clicks in (
                    (
                        "closes",
                        [good, [], confirmation_boxes(pool, 2, scale=scale), good, good],
                        False,
                        True,
                        1,
                    ),
                    ("stuck", [good] * 4, True, False, 2),
                    (
                        "changed",
                        [good, good, confirmation_boxes(pool, 2, scale=scale)],
                        True,
                        False,
                        1,
                    ),
                    ("wrong_pool", [confirmation_boxes(other, scale=scale)], False, False, 0),
                    ("zero", [confirmation_boxes(pool, 0, scale=scale)], False, False, 0),
                    (
                        "wrong_count",
                        [confirmation_boxes(pool, cost="免费2次", scale=scale)],
                        False,
                        False,
                        0,
                    ),
                    (
                        "paid",
                        [confirmation_boxes(pool, cost="消耗钻石90", scale=scale)],
                        False,
                        False,
                        0,
                    ),
                    ("missing", [good[:-4]], False, False, 0),
                    ("duplicate", [good + [good[-3]]], False, False, 0),
                ):
                    with self.subTest(pool=pool, scale=scale, scenario=scenario):
                        task = gacha_task(确认弹窗等待秒数=1.0, 确认提交重试次数=1)
                        clock = FakeClock()
                        task.sleep = clock.sleep
                        task.capture_frame = Mock(
                            return_value=np.zeros(
                                (round(800 * scale), round(1000 * scale), 3), np.uint8
                            )
                        )
                        reads = iter(sequence)
                        task._gacha_ocr_boxes = Mock(
                            side_effect=lambda *_: next(reads, sequence[-1])
                        )
                        task._confirm_dialog_still_open = Mock(return_value=still_open)
                        with patch.object(free_gacha_module, "monotonic", clock):
                            self.assertEqual(
                                success, task._confirm_dialog_submission(pool + "抽抽乐")
                            )
                        self.assertEqual(clicks, task.operate_click.call_count)
                        for click in task.operate_click.call_args_list:
                            np.testing.assert_allclose(click.args, (0.695, 0.55625))
                        if scenario == "closes":
                            self.assertEqual(5, task.capture_frame.call_count)

    def test_result_return_recovers_lost_clicks_and_stops_at_pool_or_budget(self):
        for case, expected, counts in (
            ("lost_clicks", True, (2, 2)),
            ("late_detail", True, (1, 1)),
            ("already_pool", True, (0, 0)),
            ("unknown", False, (0, 0)),
            ("loading", False, (0, 0)),
            ("blocked", False, (0, 0)),
            ("detail_stuck", False, (3, 0)),
            ("result_stuck", False, (0, 3)),
            ("ocr_over_budget", False, (0, 0)),
        ):
            with self.subTest(case=case):
                task = object.__new__(FreeGachaTask)
                task.config = {}
                task.info_set = Mock()
                task.log_info = Mock()
                task._save_flow_diagnostic = Mock()
                task._click_skip_until_back_page = Mock(return_value=(case != "late_detail", ""))
                now, clicks = [0.0], []
                task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
                task.capture_frame = Mock(return_value=np.zeros((10, 10, 3), dtype=np.uint8))
                task.operate_click = lambda x, y, **_: clicks.append((x, y))

                def observe(_frame, _pool):
                    if case == "ocr_over_budget":
                        now[0] += 20
                        return "detail", (0.74, 0.30)
                    if case == "already_pool":
                        return "pool", None
                    if case in ("unknown", "loading", "blocked"):
                        return case, None
                    if case == "late_detail" and now[0] < 0.75:
                        return "unknown", None
                    close_count = clicks.count((0.74, 0.30))
                    back_count = clicks.count((0.09, 0.05))
                    needed = 2 if case == "lost_clicks" else 1
                    if case == "detail_stuck" or (case != "result_stuck" and close_count < needed):
                        return "detail", (0.74, 0.30)
                    if case == "result_stuck" or back_count < needed:
                        return "result", (0.09, 0.05)
                    return "pool", None

                if case == "lost_clicks":
                    del task._click_skip_until_back_page
                    task.config.update({"跳过点击间隔秒数": .2, "结果跳过连续点击秒数": 3.,
                                        "结果页 OCR 等待秒数": 1., "结果页 OCR 间隔秒数": .1})
                    task._click_reference = Mock()
                    task._ocr_keywords_in_frame = Mock(
                        side_effect=[(False, ""), (True, "抽抽乐券")]
                    )
                task._result_return_state = observe
                with patch.object(free_gacha_module, "monotonic", side_effect=lambda: now[0]):
                    self.assertEqual(expected, task._handle_result_until_back("装备抽抽乐"))
                self.assertEqual(counts, (clicks.count((0.74, 0.30)), clicks.count((0.09, 0.05))))
                self.assertLessEqual(now[0], 20 if case == "ocr_over_budget" else 12)
                if case == "lost_clicks":
                    self.assertEqual(15, task._click_reference.call_count)
                    self.assertEqual([(1770, 60)] * 15,
                                     [c.args for c in task._click_reference.call_args_list])
                    self.assertEqual(2, task._ocr_keywords_in_frame.call_count)
                if expected:
                    task._save_flow_diagnostic.assert_not_called()
                else:
                    task._save_flow_diagnostic.assert_called_once_with("gacha_result_return_failed")

    def test_result_return_requires_result_evidence_and_current_back_position(self):
        for scale in (1, 2 / 3, 2):
            task = object.__new__(FreeGachaTask)
            task.config = {}
            task.info_set = Mock()
            frame = np.zeros((round(1080 * scale), round(1920 * scale), 3), dtype=np.uint8)
            miss = MatchResult(-1.0, (0, 0), (0, 0))
            back = MatchResult(
                0.99,
                (160 * scale, 30 * scale),
                (20 * scale, 40 * scale),
                pixel_score=0.94,
                zncc_score=0.94,
            )
            task._match = Mock(
                side_effect=lambda _f, spec: back if spec == RESULT_BACK_TEMPLATE else miss
            )
            cases = (
                (["付费4025", "免费137312", "160", "UR0/100"], "result"),
                (["付费4025", "免费137312", "160", "0/100"], "result"),
                (BACK_PAGE_KEYWORDS, "detail"),
                (["付费4025", "免费137312", "0/100", "抽抽乐券"], "unknown"),
                (["付费4025", "免费137312", "0/100", "抽抽乐记录"], "unknown"),
                (["付费4025", "免费137312", "0/100", "抽1次"], "unknown"),
                (["付费4025", "免费137312", "0/100", "取消"], "unknown"),
                (["付费4025", "免费137312", "101/100"], "unknown"),
                (["付费4025", "免费137312", "0/100", "1/100"], "unknown"),
                (["付费4025", "免费137312"], "unknown"),
                (["0/100"], "unknown"),
                (["服装抽抽乐"], "blocked"),
                (["确认抽抽乐"], "blocked"),
            )
            for labels, expected in cases:
                with self.subTest(scale=scale, labels=labels):
                    task._gacha_ocr_boxes = Mock(
                        return_value=[
                            SimpleNamespace(
                                name=label,
                                x=100 * scale,
                                y=950 * scale,
                                width=100 * scale,
                                height=30 * scale,
                            )
                            for label in labels
                        ]
                    )
                    state, point = task._result_return_state(frame, "装备抽抽乐")
                    self.assertEqual(expected, state)
                    if state == "result":
                        self.assertAlmostEqual(170 / 1920, point[0])
                        self.assertAlmostEqual(50 / 1080, point[1])
                    elif state == "detail":
                        self.assertEqual((1420 / 1920, 326 / 1080), point)
                    else:
                        self.assertIsNone(point)
            task._gacha_ocr_boxes = Mock(return_value=pool_boxes("装备", scale=scale))
            self.assertEqual(("pool", None), task._result_return_state(frame, "装备抽抽乐"))
            result_boxes = [
                SimpleNamespace(
                    name=label, x=100 * scale, y=700 * scale, width=100 * scale, height=30 * scale
                )
                for label in ("付费4025", "免费137312", "0/100")
            ]
            task._gacha_ocr_boxes.return_value = result_boxes
            self.assertEqual(("unknown", None), task._result_return_state(frame, "装备抽抽乐"))
            result_boxes[-1].y = 950 * scale
            task._match = Mock(return_value=miss)
            self.assertEqual(("unknown", None), task._result_return_state(frame, "装备抽抽乐"))
            task._match = Mock(return_value=MatchResult(0.99, (0, 0), (20, 20), pixel_score=0.99))
            task._gacha_ocr_boxes.reset_mock()
            self.assertEqual(("loading", None), task._result_return_state(frame, "装备抽抽乐"))
            task._gacha_ocr_boxes.assert_not_called()

    def test_current_free_batch_can_be_claimed_on_repeat_runs(self):
        from src.tasks import run_history

        for pool in ("服装抽抽乐", "装备抽抽乐"):
            for days in (0, 1, 7):
                with self.subTest(pool=pool, days=days):
                    self.reset_stage_history()
                    task = object.__new__(FreeGachaTask)
                    task.config = {}
                    task.info_set = Mock()
                    task.log_info = Mock()
                    task._wait_for_free_gacha = Mock(return_value=(True, "所有免费抽抽乐", True))
                    task._open_confirm_dialog_with_retry = Mock(return_value=True)
                    task._confirm_dialog_submission = Mock(return_value=False)
                    task._handle_result_until_back = Mock(return_value=True)
                    checkpoint = f"免费抽:{pool}"
                    run_history.save_stage(task, checkpoint, "pending", scope="old-cycle")
                    self.history_mock.return_value = run_history.RunHistoryStore(self.history.path)
                    with patch.object(run_history, "day_start_ts", return_value=days * 86400):
                        self.assertFalse(task._run_free_section(pool, True))
                        task._confirm_dialog_submission.assert_called_once_with(pool)
                        self.assertEqual(
                            "old-cycle", run_history.read_stage(task, checkpoint)["scope"]
                        )
                        task._wait_for_free_gacha.side_effect = [
                            (True, "所有免费抽抽乐", True),
                            (False, "当前卡池无免费入口", True),
                        ]
                        task._confirm_dialog_submission.return_value = True
                        self.assertTrue(task._run_free_section(pool, True))

    def test_run_checks_home_and_both_pool_titles_before_claiming(self):
        from src.tasks.run_history import is_successful_run

        for home, clothing, judged, recovered, expected, claims in (
            (False, True, True, True, False, []),
            (True, False, True, True, False, []),
            (True, True, True, True, True, ["装备抽抽乐"]),
            (True, True, False, True, False, ["装备抽抽乐"]),
            (True, True, False, False, False, []),
        ):
            with self.subTest(home=home, clothing=clothing, judged=judged, recovered=recovered):
                task = gacha_task()
                info = {}
                task.info_set = info.__setitem__
                task.prepare_home = Mock(return_value=home)
                task._wait_loading_or_gacha_page = Mock(return_value=("target", True, "抽抽乐"))
                task._wait_for_clothing_pool_page = Mock(side_effect=[clothing, recovered])
                task._wait_for_equipment_pool_page = Mock(return_value=True)
                task._wait_loading_or_home_confirmation = Mock(return_value=True)
                task._wait_for_free_gacha = Mock(
                    side_effect=[
                        (False, "服装池", judged),
                        (True, "所有免费抽抽乐", True),
                        (False, "无免费入口", True),
                    ]
                )
                task._open_confirm_dialog_with_retry = Mock(return_value=True)
                task._confirm_dialog_submission = Mock(return_value=True)
                task._handle_result_until_back = Mock(return_value=True)
                self.assertEqual(expected, task.run())
                self.assertEqual(expected, is_successful_run(info))
                for action in (
                    task._open_confirm_dialog_with_retry,
                    task._confirm_dialog_submission,
                    task._handle_result_until_back,
                ):
                    self.assertEqual(claims, [c.args[0] for c in action.call_args_list])
                if not home:
                    task._click_reference.assert_not_called()
                    task.operate_click.assert_not_called()
                if claims:
                    self.assertEqual("本轮免费抽已领取", info["装备抽抽乐 结果"])

    def test_free_section_requires_confirmation_result_and_an_exhausted_recheck(self):
        for confirmed, result, judged, expected in (
            (False, True, True, False),
            (True, False, True, False),
            (True, True, False, False),
            (True, True, True, True),
        ):
            with self.subTest(confirmed=confirmed, result=result, judged=judged):
                self.reset_stage_history()
                task = gacha_task()
                task._wait_for_free_gacha = Mock(
                    side_effect=[(True, "所有免费抽抽乐", True), (False, "无免费入口", judged)]
                )
                task._open_confirm_dialog_with_retry = Mock(return_value=True)
                task._confirm_dialog_submission = Mock(return_value=confirmed)
                task._handle_result_until_back = Mock(return_value=result)
                self.assertEqual(expected, task._run_free_section("装备抽抽乐", True))
                task._confirm_dialog_submission.assert_called_once_with("装备抽抽乐")
                self.assertEqual(int(confirmed), task._handle_result_until_back.call_count)
                self.assertEqual(
                    1 + int(confirmed and result), task._wait_for_free_gacha.call_count
                )


if __name__ == "__main__":
    unittest.main()
