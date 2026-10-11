"""Map-trade safety tests (split from test_map_trade.py)."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from ok.task.exceptions import TaskDisabledException
from ok.task.TaskExecutor import TaskExecutor

from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    DEFAULT_COOKING_RECIPES,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.trader import Trader
from src.tasks.map_trade.vision import Vision
from src.tasks.MapTradeTask import MapTradeTask

ROOT = Path(__file__).resolve().parents[1]


class MapTradeSafetyTest(unittest.TestCase):
    def _cancellable_task(self):
        task = object.__new__(MapTradeTask)
        task.config = {}
        for name in ("info_set", "log_info", "log_warning", "log_error", "log_completion"):
            setattr(task, name, lambda *_args, **_kwargs: None)
        executor = SimpleNamespace(paused=False, current_task=SimpleNamespace(_enabled=True))
        clicks = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        def click(*_args, **_kwargs):
            TaskExecutor.check_enabled(executor)
            clicks.append(executor.current_task is None)

        def cancel(*_args, **_kwargs):
            executor.current_task._enabled = False
            TaskExecutor.check_enabled(executor)

        task.operate_click = click
        task.capture_frame = lambda: TaskExecutor.check_enabled(executor) or frame
        task.sleep = lambda *_args: TaskExecutor.check_enabled(executor)
        task.save_frame = lambda *_args: None
        return task, executor, frame, clicks, cancel

    def test_map_trade_sources_do_not_call_keyboard_interfaces(self):
        sources = [
            ROOT / "src" / "tasks" / "MapTradeTask.py",
            ROOT / "src" / "tasks" / "MapCollectionTask.py",
        ]
        sources.extend((ROOT / "src" / "tasks" / "map_trade").glob("*.py"))
        forbidden_calls = {"send_key", "key_down", "key_up", "press_key"}
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            with self.subTest(source=source.name):
                self.assertTrue(forbidden_calls.isdisjoint(called))

    def test_ocr_cancellation_reaches_executor_without_followup_clicks(self):
        cases = (
            ("ocr_boxes", "直接OCR"),
            ("_detect_map_page_mode", "地图页面左上标题"),
            ("_detect_map_page_mode", "箱庭大地图底部控件"),
            ("_sandbox_teleport_skill_failure_text", "箱庭5号传送阵技能失败"),
            ("_click_sandbox_navigation_menu_teleport", "箱庭徒步导航传送阵菜单"),
            ("_click_sandbox_navigation_destination_confirmation", "箱庭徒步导航传送阵确认"),
            ("_click_teleport_generation", "传送阵生成确认"),
        )
        for method_name, cancel_name in cases:
            with self.subTest(method=method_name, boundary=cancel_name):
                task, executor, frame, clicks, cancel = self._cancellable_task()

                def ocr(*_args, name, **_kwargs):
                    if name == cancel_name:
                        cancel()
                    return [SimpleNamespace(name=COLLECTABLE_CARDS[0].targets[0].titles[0])]

                task.ocr = ocr
                vision = Vision(task)
                navigator = Navigator(task, vision)
                navigator.return_home = lambda: (
                    task.operate_click(0.5, 0.5) or NavigationResult(True, ScreenState.HOME)
                )
                def action():
                    if method_name == "ocr_boxes":
                        return vision.ocr_boxes(frame, cancel_name)
                    if method_name == "_click_teleport_generation":
                        return navigator._click_teleport_generation(
                            MatchResult(1.0, (20, 20), (10, 10)), frame.shape,
                        )
                    return getattr(navigator, method_name)(frame)

                with self.assertRaises(TaskDisabledException):
                    task._run_phases(navigator, (("导航", "启用", action),))
                self.assertIsNone(executor.current_task)
                self.assertNotIn(True, clicks)

    def test_phase_cancellation_skips_ui_cleanup_and_diagnostics(self):
        for boundary in ("capture_frame", "operate_click", "sleep", "diagnostic", "return_home"):
            with self.subTest(boundary=boundary):
                task, executor, _frame, clicks, _cancel = self._cancellable_task()
                diagnostics = []
                task.save_frame = lambda *_args: diagnostics.append(True)

                def phase():
                    if boundary == "return_home":
                        return True
                    executor.current_task._enabled = False
                    if boundary == "diagnostic":
                        return False
                    getattr(task, boundary)()

                def return_home():
                    if boundary == "return_home":
                        executor.current_task._enabled = False
                    task.operate_click(0.5, 0.5)
                    return NavigationResult(True, ScreenState.HOME)

                with self.assertRaises(TaskDisabledException):
                    task._run_phases(
                        SimpleNamespace(return_home=return_home),
                        (("买", "买", phase),),
                    )
                self.assertIsNone(executor.current_task)
                self.assertEqual([], clicks)
                self.assertEqual([], diagnostics)

    def test_cooking_cleanup_runs_for_failure_and_stops_for_cancellation(self):
        for stage, boundary in (
            ("entry", "capture_frame"),
            ("recipe", "operate_click"),
            ("recipe", "sleep"),
            ("recipe", "ordinary_error"),
        ):
            with self.subTest(stage=stage, boundary=boundary):
                task, executor, _frame, clicks, _cancel = self._cancellable_task()
                trader = object.__new__(Trader)
                trader.task = task
                trader._selected_cooking_recipes = lambda: DEFAULT_COOKING_RECIPES[:1]

                def fail():
                    if boundary == "ordinary_error":
                        raise RuntimeError("料理识别失败")
                    executor.current_task._enabled = False
                    getattr(task, boundary)()

                def enter():
                    trader._cooking_opened = True
                    if stage == "entry":
                        fail()
                    return True

                trader._enter_cooking_list = enter
                trader._cook_one_recipe = lambda _recipe: fail()
                trader._leave_cooking_to_q_sp6 = lambda: task.operate_click(0.5, 0.5) or True
                error = RuntimeError if boundary == "ordinary_error" else TaskDisabledException
                with self.assertRaises(error):
                    trader.run_cooking()
                self.assertEqual([False] if boundary == "ordinary_error" else [], clicks)
