"""Regression coverage for the Q_sp6 cooking-before-trade flow."""

import inspect
import unittest
from types import SimpleNamespace

import numpy as np

from src.tasks.map_trade.action_icons import COOKING_ICON
from src.tasks.map_trade.models import (
    DEFAULT_RECIPES,
    MERCHANT_CARD_ID,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator import MERCHANT_CLICK_LOCATION_TEMPLATE, Navigator
from src.tasks.map_trade.trader import Trader
from src.tasks.map_trade.trader_cooking import (
    COOKING_BACK_POINT,
    COOKING_BACK_TEMPLATE,
    COOKING_DETAIL_TEMPLATE,
    COOKING_LIST_GRID_ROI,
    COOKING_QUANTITY_CHOICES_ROI,
    COOKING_RECIPE_SPECS,
    COOKING_SKILL_GROUP_POINT,
    CookingDetailSnapshot,
    CookingFlowMixin,
    CookingListSnapshot,
    CookingRecipeOutcome,
    _character_coverage,
)


class CookingTask:
    def __init__(self, **config):
        self.config = {
            "料理制作周期": "每周",
            "料理保险": True,
            "5星料理": list(DEFAULT_RECIPES),
            **config,
        }
        self.clicks = []
        self.logs = []
        self.infos = []

    def operate_click(self, x, y, after_sleep=0.0):
        self.clicks.append((x, y, after_sleep))

    def sleep(self, _seconds):
        return None

    def log_info(self, message):
        self.logs.append(("info", message))

    def log_warning(self, message):
        self.logs.append(("warning", message))

    def info_set(self, key, value):
        self.infos.append((key, value))


class CookingProgress:
    def __init__(self, completed=()):
        self.completed = set(completed)
        self.marked = []

    def should_cook(self, *, every_run=False, recipes=None):
        return every_run or any(recipe not in self.completed for recipe in recipes or ())

    def cooking_recipe_complete(self, recipe):
        return recipe in self.completed

    def mark_cooking_recipe_complete(self, recipe):
        if recipe in self.completed:
            return False
        self.completed.add(recipe)
        self.marked.append(recipe)
        return True


class CookingFlowTest(unittest.TestCase):
    def _orchestrated_trader(self, *, selected, completed=(), outcomes=(), exit_ok=True):
        task = CookingTask(**{"5星料理": list(selected)})
        progress = CookingProgress(completed)
        trader = object.__new__(Trader)
        trader.task = task
        trader.progress = progress
        calls = []

        def enter():
            calls.append("enter")
            trader._cooking_opened = True
            return True

        pending_outcomes = iter(outcomes)
        trader._enter_cooking_list = enter
        trader._cook_one_recipe = lambda recipe, insurance: (
            calls.append(("cook", recipe, insurance)) or next(pending_outcomes)
        )
        trader._leave_cooking_to_q_sp6 = lambda: calls.append("exit") or exit_ok
        return trader, progress, calls

    def test_weekly_flow_resumes_only_pending_recipes_and_keeps_unavailable_retryable(self):
        first, second, third = DEFAULT_RECIPES[:3]
        trader, progress, calls = self._orchestrated_trader(
            selected=(first, second, third),
            completed=(first,),
            outcomes=(CookingRecipeOutcome.COOKED, CookingRecipeOutcome.UNAVAILABLE),
        )

        self.assertTrue(trader.run_cooking())
        self.assertEqual([second], progress.marked)
        self.assertEqual(
            [
                "enter",
                ("cook", second, True),
                ("cook", third, True),
                "exit",
            ],
            calls,
        )
        self.assertNotIn(third, progress.completed)

    def test_partial_failure_persists_proven_recipe_and_stops_later_recipes(self):
        first, second, third = DEFAULT_RECIPES[:3]
        trader, progress, calls = self._orchestrated_trader(
            selected=(first, second, third),
            outcomes=(CookingRecipeOutcome.COOKED, CookingRecipeOutcome.FAILED),
        )

        self.assertFalse(trader.run_cooking())
        self.assertEqual([first], progress.marked)
        self.assertEqual(
            [
                "enter",
                ("cook", first, True),
                ("cook", second, True),
                "exit",
            ],
            calls,
        )

    def test_exit_to_q_sp6_is_required_for_success(self):
        trader, progress, calls = self._orchestrated_trader(
            selected=(DEFAULT_RECIPES[0],),
            outcomes=(CookingRecipeOutcome.COOKED,),
            exit_ok=False,
        )

        self.assertFalse(trader.run_cooking())
        self.assertEqual([DEFAULT_RECIPES[0]], progress.marked)
        self.assertEqual("exit", calls[-1])

    def test_completed_selected_recipes_skip_navigation(self):
        recipe = DEFAULT_RECIPES[0]
        trader = object.__new__(Trader)
        trader.task = CookingTask(**{"5星料理": [recipe]})
        trader.progress = CookingProgress((recipe,))
        trader._enter_cooking_list = lambda: self.fail("navigation must be skipped")

        self.assertTrue(trader.run_cooking())

    def test_entry_uses_relative_skill_group_two_and_detected_cooking_icon(self):
        task = CookingTask()
        stable_calls = []
        navigator = SimpleNamespace(
            select_trade_card=lambda card_id: NavigationResult(
                card_id == MERCHANT_CARD_ID,
                ScreenState.SANDBOX,
            ),
            wait_for_q_sp6_sandbox=lambda timeout: timeout > 0,
        )
        vision = SimpleNamespace(
            click_stable_template=lambda spec, timeout, after_sleep: (
                stable_calls.append((spec, timeout, after_sleep)) or True
            )
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.navigator = navigator
        trader.vision = vision
        trader._cooking_opened = False
        trader._wait_for_cooking_list = lambda _timeout: object()

        self.assertTrue(trader._enter_cooking_list())
        self.assertEqual([(*COOKING_SKILL_GROUP_POINT, 0.0)], task.clicks)
        self.assertIs(COOKING_ICON.template, stable_calls[0][0])
        self.assertTrue(trader._cooking_opened)

    def test_recipe_state_machine_requires_result_and_list_restoration(self):
        recipe = DEFAULT_RECIPES[0]
        task = CookingTask(**{"料理保险": False})
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        recipe_match = MatchResult(0.97, (1000, 650), (80, 80), pixel_score=0.94)
        start_match = MatchResult(0.98, (1110, 975), (40, 40), pixel_score=0.96)
        detail = CookingDetailSnapshot(frame, start_match, True, 0.72)
        events = []
        vision = SimpleNamespace(
            match=lambda _frame, _spec: events.append("recipe recognized") or recipe_match,
            passes=lambda *_args: True,
            click_client=lambda point, _shape, after_sleep=0.0: events.append(
                ("click", point, after_sleep)
            ),
        )
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = vision
        trader._wait_for_cooking_list = lambda _timeout: (
            events.append("list confirmed") or CookingListSnapshot(frame, recipe_match)
        )
        trader._wait_for_cooking_detail = lambda _recipe, _timeout: (
            events.append("detail confirmed") or detail
        )
        trader._click_quantity_choice = lambda _recipe, choice: (
            events.append(f"quantity {choice}") or True
        )
        trader._wait_for_enabled_detail = lambda _recipe, _timeout: (
            events.append("start recognized") or detail
        )
        trader._wait_for_cooking_started = lambda _recipe, _timeout: (
            events.append("animation started") or True
        )
        trader._wait_for_cooking_result = lambda _recipe, _timeout: (
            events.append("result confirmed") or frame
        )
        trader._return_from_detail_to_list = lambda _recipe: (
            events.append("list restored") or True
        )

        self.assertIs(
            CookingRecipeOutcome.COOKED,
            trader._cook_one_recipe(recipe, insurance=False),
        )
        self.assertLess(events.index("quantity MAX"), events.index("start recognized"))
        self.assertLess(events.index("animation started"), events.index("result confirmed"))
        self.assertLess(events.index("result confirmed"), events.index("list restored"))

    def test_disabled_recipe_is_nonfatal_and_never_starts(self):
        recipe = DEFAULT_RECIPES[0]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        recipe_match = MatchResult(0.97, (1000, 650), (80, 80), pixel_score=0.94)
        detail = CookingDetailSnapshot(
            frame,
            MatchResult(0.96, (1110, 975), (40, 40), pixel_score=0.74),
            False,
            0.004,
        )
        trader = object.__new__(Trader)
        trader.task = CookingTask()
        trader.vision = SimpleNamespace(
            match=lambda *_args: recipe_match,
            passes=lambda *_args: True,
            click_client=lambda *_args, **_kwargs: None,
        )
        trader._wait_for_cooking_list = lambda _timeout: CookingListSnapshot(
            frame,
            recipe_match,
        )
        trader._wait_for_cooking_detail = lambda *_args: detail
        trader._return_from_detail_to_list = lambda _recipe: True
        trader._click_quantity_choice = lambda *_args: self.fail(
            "disabled recipe must not select quantity"
        )

        self.assertIs(
            CookingRecipeOutcome.UNAVAILABLE,
            trader._cook_one_recipe(recipe, insurance=False),
        )

    def test_result_timeout_is_failure_not_success(self):
        recipe = DEFAULT_RECIPES[0]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(0.97, (1000, 650), (80, 80), pixel_score=0.94)
        detail = CookingDetailSnapshot(frame, match, True, 0.72)
        recovered = []
        trader = object.__new__(Trader)
        trader.task = CookingTask()
        trader.vision = SimpleNamespace(
            match=lambda *_args: match,
            passes=lambda *_args: True,
            click_client=lambda *_args, **_kwargs: None,
        )
        trader._wait_for_cooking_list = lambda _timeout: CookingListSnapshot(frame, match)
        trader._wait_for_cooking_detail = lambda *_args: detail
        trader._click_quantity_choice = lambda *_args: True
        trader._wait_for_enabled_detail = lambda *_args: detail
        trader._wait_for_cooking_started = lambda *_args: True
        trader._wait_for_cooking_result = lambda *_args: None
        trader._recover_cooking_list = lambda: recovered.append(True) or True

        self.assertIs(
            CookingRecipeOutcome.FAILED,
            trader._cook_one_recipe(recipe, insurance=True),
        )
        self.assertEqual([True], recovered)

    def test_cooking_implementation_has_no_scroll_or_resolution_pixel_clicks(self):
        source = inspect.getsource(CookingFlowMixin)

        self.assertNotIn("drag_reference", source)
        self.assertNotIn("click_reference", source)
        self.assertNotIn("1203", source)
        self.assertEqual((1749 / 1920, 1011 / 1080), COOKING_SKILL_GROUP_POINT)
        for spec in COOKING_RECIPE_SPECS.values():
            self.assertEqual(COOKING_LIST_GRID_ROI, spec.relative_roi)
            self.assertIsNone(spec.roi)


class CookingRecognitionTest(unittest.TestCase):
    def test_detail_enabled_gate_separates_video_bright_and_disabled_states(self):
        recipe = DEFAULT_RECIPES[0]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        current = {
            "match": MatchResult(0.995, (1110, 975), (40, 40), pixel_score=0.966),
            "bright": 0.73,
        }
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda *_args: current["match"],
            passes=lambda *_args: True,
            ocr_text=lambda _frame, name, **_kwargs: (
                "料理" if "标题" in name else recipe
            ),
            simplify=lambda value: value,
            bright_neutral_ratio=lambda *_args: current["bright"],
        )
        trader = object.__new__(Trader)
        trader.task = CookingTask()
        trader.vision = vision

        self.assertTrue(trader._cooking_detail_snapshot(recipe).enabled)
        current["match"] = MatchResult(
            0.956,
            (1110, 975),
            (40, 40),
            pixel_score=0.747,
        )
        current["bright"] = 0.004
        self.assertFalse(trader._cooking_detail_snapshot(recipe).enabled)

    def test_result_requires_recipe_name_and_positive_quantity_evidence(self):
        recipe = DEFAULT_RECIPES[0]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        text = {"value": f"{recipe}×260"}
        trader = object.__new__(Trader)
        trader.task = CookingTask()
        trader.vision = SimpleNamespace(
            capture=lambda: frame,
            ocr_text=lambda *_args, **_kwargs: text["value"],
            simplify=lambda value: value,
        )
        trader._cooking_detail_snapshot = lambda *_args: object()

        self.assertIs(frame, trader._wait_for_cooking_result(recipe, 0.0))
        text["value"] = recipe
        self.assertIsNone(trader._wait_for_cooking_result(recipe, 0.0))
        text["value"] = "其他料理×60"
        self.assertIsNone(trader._wait_for_cooking_result(recipe, 0.0))

    def test_quantity_choice_clicks_ocr_box_center(self):
        recipe = DEFAULT_RECIPES[0]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicked = []
        detail = CookingDetailSnapshot(
            frame,
            MatchResult(0.98, (1110, 975), (40, 40), pixel_score=0.96),
            True,
            0.72,
        )
        trader = object.__new__(Trader)
        trader.task = CookingTask()
        trader.vision = SimpleNamespace(
            ocr_boxes=lambda _frame, _name, **kwargs: [
                SimpleNamespace(name="MAX", x=650, y=850, width=80, height=50)
            ],
            simplify=lambda value: value,
            click_client=lambda point, shape, after_sleep: clicked.append(
                (point, shape, after_sleep)
            ),
        )
        trader._cooking_detail_snapshot = lambda _recipe: detail

        self.assertTrue(trader._click_quantity_choice(recipe, "MAX"))
        self.assertEqual(((690, 875), frame.shape, 0.0), clicked[0])

    def test_back_button_prefers_detected_center_then_relative_fallback(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        detected = MatchResult(
            0.96,
            (160, 35),
            (30, 30),
            pixel_score=0.94,
            zncc_score=0.90,
        )
        task = CookingTask()
        clicked = []
        passed = {"value": True}
        trader = object.__new__(Trader)
        trader.task = task
        trader.vision = SimpleNamespace(
            match=lambda _frame, spec: (
                detected if spec is COOKING_BACK_TEMPLATE else self.fail(spec.name)
            ),
            passes=lambda *_args: passed["value"],
            click_client=lambda point, _shape, after_sleep: clicked.append(
                (point, after_sleep)
            ),
        )

        trader._click_cooking_back(frame, context="test")
        self.assertEqual([(detected.center, 0.0)], clicked)
        passed["value"] = False
        trader._click_cooking_back(frame, context="test")
        self.assertEqual([(*COOKING_BACK_POINT, 0.0)], task.clicks)

    def test_character_coverage_tolerates_one_ocr_character_but_not_wrong_recipe(self):
        self.assertGreaterEqual(_character_coverage("地狱火紫菜包饭", "地狱火紫菜包反260"), 0.75)
        self.assertLess(_character_coverage("地狱火紫菜包饭", "透明沙拉60"), 0.75)

    def test_all_cooking_geometry_is_fractional_and_detail_template_is_scoped(self):
        for point in (*COOKING_SKILL_GROUP_POINT, *COOKING_BACK_POINT):
            self.assertGreaterEqual(point, 0.0)
            self.assertLessEqual(point, 1.0)
        for roi in (COOKING_LIST_GRID_ROI, COOKING_QUANTITY_CHOICES_ROI):
            self.assertTrue(all(0.0 <= value <= 1.0 for value in roi))
            self.assertLess(roi[0], roi[2])
            self.assertLess(roi[1], roi[3])
        self.assertIsNotNone(COOKING_DETAIL_TEMPLATE.relative_roi)
        self.assertIsNone(COOKING_DETAIL_TEMPLATE.roi)


class CookingSandboxConfirmationTest(unittest.TestCase):
    def test_q_sp6_confirmation_requires_sandbox_and_merchant_location_same_frame(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        match = MatchResult(
            0.96,
            (1200, 700),
            (80, 120),
            pixel_score=0.93,
            zncc_score=0.88,
        )
        matched_specs = []
        task = CookingTask()
        vision = SimpleNamespace(
            capture=lambda: frame,
            match=lambda _frame, spec: matched_specs.append(spec) or match,
            passes=lambda result, spec: (
                result is match and spec is MERCHANT_CLICK_LOCATION_TEMPLATE
            ),
        )
        navigator = Navigator(task, vision)
        navigator._classify_trade_frame = lambda _frame: ScreenState.SANDBOX

        self.assertTrue(navigator.wait_for_q_sp6_sandbox(0.0))
        self.assertEqual([MERCHANT_CLICK_LOCATION_TEMPLATE], matched_specs)

        navigator._classify_trade_frame = lambda _frame: ScreenState.HOME
        self.assertFalse(navigator.wait_for_q_sp6_sandbox(0.0))


if __name__ == "__main__":
    unittest.main()
