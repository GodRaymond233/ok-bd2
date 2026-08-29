from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from time import monotonic

from src.tasks.map_trade.action_icons import COOKING_ICON, SKILL_GROUP_CENTERS_REFERENCE
from src.tasks.map_trade.models import (
    DEFAULT_RECIPES,
    MERCHANT_CARD_ID,
    RECIPE_TEMPLATES,
    MatchResult,
    TemplateSpec,
)
from src.tasks.map_trade.trader_constants import COOK_SUBMENU_TEMPLATE, split_items
from src.tasks.map_trade.vision import normalize_text
from src.utils.calibration import FHD_1080

# The supplied 1920x1080 PC recording shows the complete recipe grid on one
# page.  Every fixed control below is stored as a ratio; recipe and action
# targets themselves are still clicked at their detected centers.
COOKING_SKILL_GROUP_POINT = (
    SKILL_GROUP_CENTERS_REFERENCE[2][0] / FHD_1080.width,
    SKILL_GROUP_CENTERS_REFERENCE[2][1] / FHD_1080.height,
)
COOKING_LIST_GRID_ROI = (
    700 / FHD_1080.width,
    85 / FHD_1080.height,
    1795 / FHD_1080.width,
    1015 / FHD_1080.height,
)
COOKING_HEADER_ROI = (
    210 / FHD_1080.width,
    15 / FHD_1080.height,
    470 / FHD_1080.width,
    95 / FHD_1080.height,
)
COOKING_DETAIL_NAME_ROI = (
    210 / FHD_1080.width,
    80 / FHD_1080.height,
    680 / FHD_1080.width,
    175 / FHD_1080.height,
)
COOKING_QUANTITY_CHOICES_ROI = (
    205 / FHD_1080.width,
    825 / FHD_1080.height,
    820 / FHD_1080.width,
    930 / FHD_1080.height,
)
COOKING_START_SEARCH_ROI = (
    1000 / FHD_1080.width,
    890 / FHD_1080.height,
    1600 / FHD_1080.width,
    1.0,
)
COOKING_START_BRIGHT_ROI = (
    1090 / FHD_1080.width,
    970 / FHD_1080.height,
    1540 / FHD_1080.width,
    1035 / FHD_1080.height,
)
COOKING_PROGRESS_ROI = (
    720 / FHD_1080.width,
    300 / FHD_1080.height,
    1240 / FHD_1080.width,
    760 / FHD_1080.height,
)
COOKING_RESULT_ROI = (
    20 / FHD_1080.width,
    420 / FHD_1080.height,
    660 / FHD_1080.width,
    575 / FHD_1080.height,
)
COOKING_BACK_ROI = (
    120 / FHD_1080.width,
    10 / FHD_1080.height,
    215 / FHD_1080.width,
    95 / FHD_1080.height,
)
COOKING_BACK_POINT = (175 / FHD_1080.width, 50 / FHD_1080.height)

COOKING_RECIPE_TEMPLATE_SCORE = 0.90
COOKING_RECIPE_PIXEL_SCORE = 0.65
COOKING_START_ENABLED_PIXEL_SCORE = 0.90
COOKING_START_ENABLED_BRIGHT_RATIO = 0.30
COOKING_TEXT_CHARACTER_COVERAGE = 0.75
COOKING_PAGE_TIMEOUT = 10.0
COOKING_START_TIMEOUT = 6.0
COOKING_COMPLETION_TIMEOUT = 40.0
COOKING_EXIT_TIMEOUT = 12.0
COOKING_POLL_INTERVAL = 0.25

COOKING_RECIPE_SPECS = {
    recipe: TemplateSpec(
        f"料理-{recipe}",
        RECIPE_TEMPLATES[recipe],
        COOKING_RECIPE_TEMPLATE_SCORE,
        relative_roi=COOKING_LIST_GRID_ROI,
        scale_ratios=(0.95, 1.0, 1.05),
        min_pixel_score=COOKING_RECIPE_PIXEL_SCORE,
        minimum_safe_threshold=COOKING_RECIPE_TEMPLATE_SCORE,
    )
    for recipe in DEFAULT_RECIPES
}
COOKING_DETAIL_TEMPLATE = TemplateSpec(
    "料理详情开始控件",
    COOK_SUBMENU_TEMPLATE.file_name,
    COOKING_RECIPE_TEMPLATE_SCORE,
    relative_roi=COOKING_START_SEARCH_ROI,
    scale_ratios=(0.95, 1.0, 1.05),
    min_pixel_score=COOKING_RECIPE_PIXEL_SCORE,
    minimum_safe_threshold=COOKING_RECIPE_TEMPLATE_SCORE,
)
COOKING_BACK_TEMPLATE = TemplateSpec(
    "料理返回按钮",
    "image/green/BackButGe.png",
    0.90,
    relative_roi=COOKING_BACK_ROI,
    minimum_safe_threshold=0.90,
    min_zncc_score=0.80,
)


class CookingRecipeOutcome(str, Enum):
    COOKED = "cooked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class CookingListSnapshot:
    frame: object
    recipe_match: MatchResult


@dataclass(frozen=True)
class CookingDetailSnapshot:
    frame: object
    start_match: MatchResult
    enabled: bool
    bright_ratio: float


def _character_coverage(expected: str, observed: str) -> float:
    expected_text = normalize_text(expected)
    observed_text = normalize_text(observed)
    if not expected_text:
        return 0.0
    available = Counter(observed_text)
    matched = 0
    for character in expected_text:
        if available[character] <= 0:
            continue
        available[character] -= 1
        matched += 1
    return matched / len(expected_text)


def _has_positive_quantity(text: str) -> bool:
    return any(int(value) > 0 for value in re.findall(r"\d+", normalize_text(text)))


class CookingFlowMixin:
    """Mouse-only Q_sp6 cooking flow proven one state transition at a time."""

    def run_cooking(self) -> bool:
        every_run = str(self.task.config.get("料理制作周期", "每周")) == "每次"
        selected = self._selected_cooking_recipes()
        if not selected:
            self.task.log_info("料理：未选择料理，跳过制作。")
            return True

        unsupported = tuple(
            recipe for recipe in selected if recipe not in COOKING_RECIPE_SPECS
        )
        if unsupported:
            self.task.log_warning(f"料理：配置包含不支持的料理：{'、'.join(unsupported)}。")
            return False

        if not self.progress.should_cook(every_run=every_run, recipes=selected):
            self.task.log_info("料理：本周所选料理均已完成，跳过制作。")
            return True
        pending = (
            selected
            if every_run
            else tuple(
                recipe
                for recipe in selected
                if not self.progress.cooking_recipe_complete(recipe)
            )
        )
        if not pending:
            self.task.log_info("料理：没有待制作的料理。")
            return True

        self._cooking_opened = False
        flow_success = False
        unavailable: list[str] = []
        cooked: list[str] = []
        try:
            if self._enter_cooking_list():
                flow_success = True
                insurance = bool(self.task.config.get("料理保险", True))
                for recipe in pending:
                    outcome = self._cook_one_recipe(recipe, insurance=insurance)
                    if outcome is CookingRecipeOutcome.COOKED:
                        self.progress.mark_cooking_recipe_complete(recipe)
                        cooked.append(recipe)
                        self._status("料理进度", f"已完成：{'、'.join(cooked)}")
                        continue
                    if outcome is CookingRecipeOutcome.UNAVAILABLE:
                        unavailable.append(recipe)
                        continue
                    self.task.log_warning(f"料理：{recipe} 未完成，停止后续料理。")
                    flow_success = False
                    break
        finally:
            if self._cooking_opened:
                exited = self._leave_cooking_to_q_sp6()
                flow_success = flow_success and exited
            if unavailable:
                self.task.log_info(
                    "料理：材料不足或按钮不可用，保留为下次重试："
                    f"{'、'.join(unavailable)}。"
                )
            if cooked:
                self.task.log_info(f"料理：本次已完成 {'、'.join(cooked)}。")
            if not flow_success:
                self._status("料理状态", "失败")
        return flow_success

    def _selected_cooking_recipes(self) -> tuple[str, ...]:
        raw = self.task.config.get("5星料理", list(DEFAULT_RECIPES))
        values = (
            split_items(raw)
            if isinstance(raw, str)
            else split_items(tuple(raw or ()))
        )
        return tuple(dict.fromkeys(values))

    def _enter_cooking_list(self) -> bool:
        entered = self.navigator.select_trade_card(MERCHANT_CARD_ID)
        if not entered.success:
            self.task.log_warning(f"料理：{entered.message}")
            return False
        if not self.navigator.wait_for_q_sp6_sandbox(COOKING_EXIT_TIMEOUT):
            self.task.log_warning("料理：进入第六章后未严格确认商人前的 Q_sp6 箱庭。")
            return False

        self._status("料理状态", "切换技能组2")
        self.task.operate_click(*COOKING_SKILL_GROUP_POINT, after_sleep=0.0)
        self.task.sleep(0.5)
        if not self.vision.click_stable_template(
            COOKING_ICON.template,
            timeout=6.0,
            after_sleep=0.0,
        ):
            self.task.log_warning("料理：技能组2中未稳定识别到制作料理技能。")
            return False
        self._cooking_opened = True
        if self._wait_for_cooking_list(COOKING_PAGE_TIMEOUT) is None:
            self.task.log_warning("料理：点击制作料理技能后未确认一页式料理列表。")
            return False
        self._status("料理状态", "料理列表已确认")
        return True

    def _cook_one_recipe(
        self,
        recipe: str,
        *,
        insurance: bool,
    ) -> CookingRecipeOutcome:
        list_snapshot = self._wait_for_cooking_list(2.0)
        if list_snapshot is None:
            self.task.log_warning(f"料理：选择 {recipe} 前料理列表未确认。")
            return CookingRecipeOutcome.FAILED

        spec = COOKING_RECIPE_SPECS[recipe]
        recipe_match = self.vision.match(list_snapshot.frame, spec)
        if not self.vision.passes(recipe_match, spec):
            self.task.log_warning(f"料理：一页料理列表中未识别到 {recipe}。")
            return CookingRecipeOutcome.FAILED
        self._status(
            f"料理-{recipe}点击中心",
            (
                f"center={recipe_match.center}, match={recipe_match.score:.3f}, "
                f"pixel={recipe_match.pixel_score:.3f}"
            ),
        )
        self.vision.click_client(recipe_match.center, list_snapshot.frame.shape, after_sleep=0.0)

        detail = self._wait_for_cooking_detail(recipe, COOKING_PAGE_TIMEOUT)
        if detail is None:
            self.task.log_warning(f"料理：点击 {recipe} 后未确认对应详情页。")
            self._recover_cooking_list()
            return CookingRecipeOutcome.FAILED
        if not detail.enabled:
            self.task.log_info(f"料理：{recipe} 当前材料不足或制作按钮不可用。")
            if not self._return_from_detail_to_list(recipe):
                return CookingRecipeOutcome.FAILED
            return CookingRecipeOutcome.UNAVAILABLE

        quantity = "MIN" if insurance else "MAX"
        if not self._click_quantity_choice(recipe, quantity):
            self.task.log_warning(f"料理：{recipe} 未识别到数量选项 {quantity}。")
            self._recover_cooking_list()
            return CookingRecipeOutcome.FAILED

        ready = self._wait_for_enabled_detail(recipe, 3.0)
        if ready is None:
            self.task.log_warning(f"料理：选择 {quantity} 后 {recipe} 制作按钮不可用。")
            self._recover_cooking_list()
            return CookingRecipeOutcome.FAILED
        self._status(
            f"料理-{recipe}开始点击中心",
            (
                f"center={ready.start_match.center}, "
                f"match={ready.start_match.score:.3f}, "
                f"pixel={ready.start_match.pixel_score:.3f}"
            ),
        )
        self.vision.click_client(
            ready.start_match.center,
            ready.frame.shape,
            after_sleep=0.0,
        )
        if not self._wait_for_cooking_started(recipe, COOKING_START_TIMEOUT):
            self.task.log_warning(f"料理：{recipe} 点击后未确认制作动画开始。")
            self._recover_cooking_list()
            return CookingRecipeOutcome.FAILED
        if self._wait_for_cooking_result(recipe, COOKING_COMPLETION_TIMEOUT) is None:
            self.task.log_warning(f"料理：{recipe} 制作超时，未确认结果条。")
            self._recover_cooking_list()
            return CookingRecipeOutcome.FAILED
        if not self._return_from_detail_to_list(recipe):
            self.task.log_warning(f"料理：{recipe} 结果已确认，但未恢复料理列表。")
            return CookingRecipeOutcome.FAILED
        return CookingRecipeOutcome.COOKED

    def _wait_for_cooking_list(self, timeout: float) -> CookingListSnapshot | None:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            snapshot = self._cooking_list_snapshot()
            if snapshot is not None:
                return snapshot
            if monotonic() >= end_at:
                return None
            self.task.sleep(COOKING_POLL_INTERVAL)

    def _cooking_list_snapshot(self, frame=None) -> CookingListSnapshot | None:
        frame = self.vision.capture() if frame is None else frame
        candidates: list[MatchResult] = []
        for spec in COOKING_RECIPE_SPECS.values():
            result = self.vision.match(frame, spec)
            if self.vision.passes(result, spec):
                candidates.append(result)
        if not candidates:
            return None
        header = self.vision.ocr_text(
            frame,
            "料理列表标题",
            relative_roi=COOKING_HEADER_ROI,
            target_height=900,
        )
        if "料理" not in normalize_text(self.vision.simplify(header)):
            return None
        return CookingListSnapshot(frame, max(candidates, key=lambda result: result.score))

    def _wait_for_cooking_detail(
        self,
        recipe: str,
        timeout: float,
    ) -> CookingDetailSnapshot | None:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            snapshot = self._cooking_detail_snapshot(recipe)
            if snapshot is not None:
                return snapshot
            if monotonic() >= end_at:
                return None
            self.task.sleep(COOKING_POLL_INTERVAL)

    def _wait_for_enabled_detail(
        self,
        recipe: str,
        timeout: float,
    ) -> CookingDetailSnapshot | None:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            snapshot = self._cooking_detail_snapshot(recipe)
            if snapshot is not None and snapshot.enabled:
                return snapshot
            if monotonic() >= end_at:
                return None
            self.task.sleep(COOKING_POLL_INTERVAL)

    def _cooking_detail_snapshot(
        self,
        recipe: str,
        frame=None,
    ) -> CookingDetailSnapshot | None:
        frame = self.vision.capture() if frame is None else frame
        start = self.vision.match(frame, COOKING_DETAIL_TEMPLATE)
        if not self.vision.passes(start, COOKING_DETAIL_TEMPLATE):
            return None
        header = self.vision.ocr_text(
            frame,
            "料理详情标题",
            relative_roi=COOKING_HEADER_ROI,
            target_height=900,
        )
        if "料理" not in normalize_text(self.vision.simplify(header)):
            return None
        name = self.vision.ocr_text(
            frame,
            f"料理详情-{recipe}",
            relative_roi=COOKING_DETAIL_NAME_ROI,
            target_height=900,
        )
        if (
            _character_coverage(recipe, self.vision.simplify(name))
            < COOKING_TEXT_CHARACTER_COVERAGE
        ):
            return None
        bright_ratio = self.vision.bright_neutral_ratio(
            frame,
            COOKING_START_BRIGHT_ROI,
        )
        enabled = (
            start.pixel_score >= COOKING_START_ENABLED_PIXEL_SCORE
            and bright_ratio >= COOKING_START_ENABLED_BRIGHT_RATIO
        )
        self._status(
            f"料理-{recipe}制作按钮",
            (
                f"{'可用' if enabled else '不可用'}; match={start.score:.3f}; "
                f"pixel={start.pixel_score:.3f}; bright={bright_ratio:.3f}"
            ),
        )
        return CookingDetailSnapshot(frame, start, enabled, bright_ratio)

    def _generic_cooking_detail_snapshot(self, frame=None) -> CookingDetailSnapshot | None:
        frame = self.vision.capture() if frame is None else frame
        start = self.vision.match(frame, COOKING_DETAIL_TEMPLATE)
        if not self.vision.passes(start, COOKING_DETAIL_TEMPLATE):
            return None
        header = self.vision.ocr_text(
            frame,
            "料理详情恢复标题",
            relative_roi=COOKING_HEADER_ROI,
            target_height=900,
        )
        if "料理" not in normalize_text(self.vision.simplify(header)):
            return None
        bright_ratio = self.vision.bright_neutral_ratio(frame, COOKING_START_BRIGHT_ROI)
        return CookingDetailSnapshot(
            frame,
            start,
            (
                start.pixel_score >= COOKING_START_ENABLED_PIXEL_SCORE
                and bright_ratio >= COOKING_START_ENABLED_BRIGHT_RATIO
            ),
            bright_ratio,
        )

    def _click_quantity_choice(self, recipe: str, choice: str) -> bool:
        detail = self._cooking_detail_snapshot(recipe)
        if detail is None or not detail.enabled:
            return False
        wanted = normalize_text(choice)
        boxes = self.vision.ocr_boxes(
            detail.frame,
            f"料理-{recipe}数量选项",
            relative_roi=COOKING_QUANTITY_CHOICES_ROI,
            target_height=900,
        )
        for box in boxes:
            text = normalize_text(self.vision.simplify(str(getattr(box, "name", ""))))
            if text != wanted:
                continue
            attrs = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
            if any(value is None for value in attrs):
                continue
            x, y, width, height = (float(value) for value in attrs)
            center = (round(x + width / 2), round(y + height / 2))
            self._status(f"料理-{recipe}数量点击中心", f"{choice}: center={center}")
            self.vision.click_client(center, detail.frame.shape, after_sleep=0.0)
            self.task.sleep(0.25)
            return True
        return False

    def _wait_for_cooking_started(self, recipe: str, timeout: float) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            frame = self.vision.capture()
            detail = self._cooking_detail_snapshot(recipe, frame)
            if detail is not None and not detail.enabled:
                self._status("料理状态", f"{recipe} 制作已开始")
                return True
            text = self.vision.ocr_text(
                frame,
                f"料理-{recipe}制作中",
                relative_roi=COOKING_PROGRESS_ROI,
                target_height=900,
            )
            if "制作中" in normalize_text(self.vision.simplify(text)):
                self._status("料理状态", f"{recipe} 制作已开始")
                return True
            if monotonic() >= end_at:
                return False
            self.task.sleep(COOKING_POLL_INTERVAL)

    def _wait_for_cooking_result(self, recipe: str, timeout: float):
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while True:
            frame = self.vision.capture()
            text = self.vision.ocr_text(
                frame,
                f"料理-{recipe}结果",
                relative_roi=COOKING_RESULT_ROI,
                target_height=900,
            )
            last_text = text or last_text
            simplified = self.vision.simplify(text)
            result_confirmed = (
                _has_positive_quantity(simplified)
                and _character_coverage(recipe, simplified)
                >= COOKING_TEXT_CHARACTER_COVERAGE
            )
            if result_confirmed and self._cooking_detail_snapshot(recipe, frame) is not None:
                self._status("料理状态", f"{recipe} 结果已确认：{text}")
                return frame
            if monotonic() >= end_at:
                self.task.log_warning(
                    f"料理：{recipe} 未识别到含料理名和数量的结果条，OCR={last_text or '-'}。"
                )
                return None
            self.task.sleep(COOKING_POLL_INTERVAL)

    def _return_from_detail_to_list(self, recipe: str) -> bool:
        detail = self._wait_for_cooking_detail(recipe, 2.0)
        if detail is None:
            return False
        self._click_cooking_back(detail.frame, context=f"{recipe}详情")
        return self._wait_for_cooking_list(COOKING_PAGE_TIMEOUT) is not None

    def _recover_cooking_list(self) -> bool:
        if self._wait_for_cooking_list(1.0) is not None:
            return True
        detail = self._generic_cooking_detail_snapshot()
        if detail is None:
            return False
        self._click_cooking_back(detail.frame, context="料理详情恢复")
        return self._wait_for_cooking_list(COOKING_PAGE_TIMEOUT) is not None

    def _click_cooking_back(self, frame, *, context: str) -> None:
        back = self.vision.match(frame, COOKING_BACK_TEMPLATE)
        if self.vision.passes(back, COOKING_BACK_TEMPLATE):
            self._status(
                "料理返回按钮",
                (
                    f"{context}: center={back.center}, match={back.score:.3f}, "
                    f"pixel={back.pixel_score:.3f}, zncc={back.zncc_score:.3f}"
                ),
            )
            self.vision.click_client(back.center, frame.shape, after_sleep=0.0)
            return
        self._status("料理返回按钮", f"{context}: 模板未通过，使用已标定相对点")
        self.task.operate_click(*COOKING_BACK_POINT, after_sleep=0.0)

    def _leave_cooking_to_q_sp6(self) -> bool:
        if self.navigator.wait_for_q_sp6_sandbox(0.0):
            self._cooking_opened = False
            return True
        if not self._recover_cooking_list():
            self.task.log_warning("料理：未确认详情页或列表页，未执行盲目返回点击。")
            return False
        list_snapshot = self._wait_for_cooking_list(1.0)
        if list_snapshot is None:
            return False
        self._click_cooking_back(list_snapshot.frame, context="料理列表")
        if not self.navigator.wait_for_q_sp6_sandbox(COOKING_EXIT_TIMEOUT):
            self.task.log_warning("料理：退出后未严格确认商人前的 Q_sp6 箱庭。")
            return False
        self._cooking_opened = False
        self._status("料理状态", "已退出到 Q_sp6 商人前")
        return True
