"""Complete one dismantle and one refinement in the current game day."""

from time import monotonic
from uuid import uuid4

import cv2
import numpy as np

from src.icons import TaskIcon
from src.tasks import run_history
from src.tasks.BaseBD2Task import TEMPLATE_DIR, BaseBD2Task
from src.tasks.equipment_daily import (
    BACKPACK_BACK_POINT,
    DISMANTLE_GRADE_FILES,
    DISMANTLE_SKIPPED,
    DISMANTLE_STEP,
    EQUIPMENT_DAILY_NAME,
    EQUIPMENT_DAILY_OWNER,
    FILTER_BUTTON_POINT,
    GRID_CELL_SIZE,
    GRID_COLUMNS,
    MAX_REFINABLE_SCORE,
    REFINE_BALANCE_BOUNDS,
    REFINE_MODE_KEY,
    REFINE_MODE_OPTIONS,
    REFINE_MODE_RANDOM,
    REFINE_MODE_UNDER_17,
    REFINE_SCORE_TEXT_BOUNDS,
    REFINE_SKIPPED,
    REFINE_STEP,
    REFINEMENT_BACK_POINT,
    SELECTION_BACK_POINT,
    EquipmentScreen,
    RefinementConflict,
    dismantle_card_selected,
    dismantle_check_point,
    dismantle_grade_points,
    equipment_daily_cycle,
    grid_blank_rows,
    refinement_value,
)
from src.tasks.task_vision_mixin import TaskVisionMixin
from src.utils import task_vision
from src.utils.image_utils import read_template_image
from src.utils.vision_models import TemplateSpec


class EquipmentDailyTask(TaskVisionMixin, BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = EQUIPMENT_DAILY_NAME
        self.description = "从背包底部找一件可分解的非 UR 装备，有则分解，再精炼一次。"
        self.icon = TaskIcon.TILES
        self.group_name = "日常/周常"
        self.group_icon = TaskIcon.CALENDAR
        self.visible = True
        self.default_config.update({"启用": True, REFINE_MODE_KEY: REFINE_MODE_RANDOM})
        self.config_description.update(
            {
                REFINE_MODE_KEY: (
                    "随机精炼（24分以下）：筛选 +9▲ 后选取 24 分以下装备；"
                    "仅精炼17分以下：筛选 +9▲ 与 17▼，无匹配装备时跳过。"
                )
            }
        )
        self.config_type[REFINE_MODE_KEY] = {
            "type": "drop_down",
            "options": list(REFINE_MODE_OPTIONS),
        }
        self._init_vision_state()
        self._equipment_templates = {}
        self._grade_templates = {
            grade: read_template_image(path) for grade, path in DISMANTLE_GRADE_FILES.items()
        }

    def _read_equipment(self):
        frame = self.capture_frame()
        boxes = self.ocr(frame=frame, threshold=0.55, target_height=1080, log=False)
        self.info_set("装备页面 OCR", " ".join(box.name for box in boxes))
        return EquipmentScreen(frame, boxes)

    def _grade_points(self, frame):
        return dismantle_grade_points(frame, self._grade_templates)

    def _wait_equipment(self, predicate, timeout=8.0):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            screen = self._read_equipment()
            if predicate(screen):
                return screen
            self.sleep(0.4)
        return None

    def _gear_tab(self, screen):
        bag, _ = screen.find(r"(?:道具|消耗品|背包)(?:\d+/\d+)?", (0.11, 0.01, 0.30, 0.095))
        if not bag:
            return None
        spec = TemplateSpec(
            name="equipment_daily_tab",
            file_name="image/UI_Backpack_Equip.png",
            relative_roi=(0.068, 0.19, 0.118, 0.27),
            default_threshold=0.74,
            scale_ratios=(0.85, 0.95, 1.0, 1.08, 1.2),
        )
        match = task_vision.match_template(
            screen.frame,
            spec,
            self.config,
            TEMPLATE_DIR,
            cache=self._equipment_templates,
        )
        if match.score < 0.74:
            return None
        height, width = screen.frame.shape[:2]
        return match.center[0] / width, match.center[1] / height

    def _open_inventory(self):
        screen = self._read_equipment()
        if screen.filter_overlay:
            screen = self._close_equipment_filter(screen)
            if screen is None:
                return None
        if screen.dismantle_result_visible:
            screen = self._close_dismantle_result()
            return self._open_all_equipment(screen) if screen is not None else None
        if screen.selection_page is not None and screen.selection_count is not None:
            if screen.active_category is not None:
                return self._open_all_equipment(screen)
            self.info_set(DISMANTLE_STEP, "继续上次遗留的分解选择页")
            return screen
        if screen.selection_page is not None:
            # 强化/制作等别的选择页布局相同，但分解流程用不了，先退出去。
            self.info_set(DISMANTLE_STEP, "退出上次遗留的其他选择页")
            screen = self._leave_selection_page(screen)
            if screen is None:
                return None
        if screen.refinement_page:
            screen = self._leave_refinement(screen)
            if screen is None:
                return None
        if screen.detail_close:
            screen = self._close_equipment_detail(screen)
            if screen is None:
                return None
        if screen.bag_open:
            return self._open_all_equipment(screen)
        if not self._frame_confirms_home(screen.frame, "装备日常主页确认"):
            # 可能是背包以外的子页面：先按返回键逐级退出，比直接判失败安全。
            if not self.recover_daily_home():
                return None
            screen = self._read_equipment()
            if not self._frame_confirms_home(screen.frame, "装备日常主页复核"):
                return None
        for attempt in range(2):
            _, bag = screen.find(r"背包", (0.15, 0.86, 0.23, 0.98))
            if bag is None:
                return None
            self.operate_click(*bag, name="打开背包", after_sleep=0.7)
            screen = self._wait_equipment(
                lambda view: view.bag_open or view.special_offer_shop,
                timeout=6.0,
            )
            if screen is None:
                return None
            if screen.bag_open:
                return self._open_all_equipment(screen)
            if attempt == 1:
                break
            if not self.recover_daily_home():
                return None
            screen = self._read_equipment()
            if not self._frame_confirms_home(screen.frame, "装备日常主页复核"):
                return None
        return None

    def _open_all_equipment(self, screen):
        """Open the equipment section and clear an active category filter."""
        if screen.inventory is None:
            point = self._gear_tab(screen)
            if point is None:
                return None
            self.operate_click(*point, name="打开装备背包", after_sleep=0.6)
            screen = self._wait_equipment(lambda view: view.dismantle_entry is not None)
            if screen is None:
                return None
        category = screen.active_category
        if category is None:
            return screen if screen.dismantle_entry is not None else None
        self.operate_click(*category, name="取消装备分类筛选", after_sleep=0.6)
        return self._wait_equipment(
            lambda view: view.dismantle_entry is not None and view.active_category is None,
        )

    @staticmethod
    def _grid_sample(frame):
        height, width = frame.shape[:2]
        grid = frame[
            round(0.16 * height) : round(0.88 * height), round(0.38 * width) : round(0.89 * width)
        ]
        return cv2.resize(grid, (160, 120), interpolation=cv2.INTER_AREA).astype(np.int16)

    def _scroll_to_bottom(self, screen, is_expected_page):
        stable_frames = 0
        scrolled = False
        previous = self._grid_sample(screen.frame)
        for _ in range(24):
            rows = grid_blank_rows(screen.frame)
            if rows and rows[-1]:
                # 可见的最后一行已经是空格子：装备列表到底了（整页空也一样）。
                if not scrolled:
                    return screen
                # ScrollRect 触底可能先过冲再回弹，等网格画面稳定后再返回。
                for _ in range(4):
                    self.sleep(0.35)
                    settled = self._read_equipment()
                    if not is_expected_page(settled):
                        return None
                    current = self._grid_sample(settled.frame)
                    settled_now = float(np.abs(current - previous).mean()) < 0.7
                    screen = settled
                    previous = current
                    if settled_now:
                        return screen
                return screen
            self.scroll_client((0.70, 0.62), -4, count=4, after_sleep=0.5)
            scrolled = True
            screen = self._read_equipment()
            if not is_expected_page(screen):
                return None
            current = self._grid_sample(screen.frame)
            if float(np.abs(current - previous).mean()) < 0.7:
                stable_frames += 1
                if stable_frames >= 2:
                    return screen
            else:
                stable_frames = 0
            previous = current
        return None

    def _scan_selection_tab(self, screen, inventory):
        """Select the first eligible card, or skip after confirming the top of the list."""

        def unselected_page(view):
            return view.selection_count == 0 and view.inventory == inventory

        if not unselected_page(screen):
            return None
        screen = self._scroll_to_bottom(screen, unselected_page)
        if screen is None:
            return None
        rows = (inventory[1] + GRID_COLUMNS - 1) // GRID_COLUMNS
        max_scrolls = rows * 4
        stable_scrolls = 0
        saw_equipment = False
        for scrolls in range(max_scrolls + 1):
            grid = self._grid_sample(screen.frame)
            saw_equipment = saw_equipment or not all(grid_blank_rows(screen.frame))
            for target in self._grade_points(screen.frame):
                self.operate_click(*target, name="选择一件非 UR 装备", after_sleep=0.45)
                selected = self._wait_equipment(
                    lambda view: view.inventory == inventory and view.selection_count == 1,
                    timeout=2.5,
                )
                if selected is not None:
                    if any(
                        dismantle_card_selected(selected.frame, (target[0], target[1] + dy))
                        for dy in (0.0, 0.03, 0.06)
                    ):
                        return selected
                    return None
                screen = self._read_equipment()
                if (
                    not unselected_page(screen)
                    or float(np.abs(self._grid_sample(screen.frame) - grid).mean()) >= 0.7
                ):
                    return None
            if saw_equipment and stable_scrolls >= 2:
                return DISMANTLE_SKIPPED
            if scrolls == max_scrolls:
                break
            self.scroll_client((0.70, 0.62), 1, after_sleep=0.5)
            screen = self._read_equipment()
            if not unselected_page(screen):
                return None
            stable = float(np.abs(self._grid_sample(screen.frame) - grid).mean()) < 0.7
            stable_scrolls = stable_scrolls + 1 if stable else 0
        return None

    def _record_step(self, step, cycle, state, evidence=None, *, attempt=None):
        if attempt is not None:
            evidence = {**(evidence or {}), "attempt": attempt}
        run_history.default_store().save_stage(
            EQUIPMENT_DAILY_OWNER,
            step,
            state,
            cycle,
            evidence=evidence,
        )

    def _reconcile_submission(self, step, evidence):
        if step == REFINE_STEP:
            return self._confirm_refinement(evidence.get("remaining"))
        before = evidence.get("inventory")
        if (
            not isinstance(before, list)
            or len(before) != 2
            or any(type(count) is not int for count in before)
            or not 0 < before[0] <= before[1]
        ):
            return False
        expected = (before[0] - 1, before[1])
        inventory = self._open_inventory()

        def matches(view):
            return view.dismantle_entry is not None and view.inventory == expected

        return (
            inventory is not None
            and matches(inventory)
            and self._wait_equipment(matches) is not None
        )

    def _leave_selection_page(self, screen, attempts=3):
        """退出「请选择需要…的装备」选择页，直到背包的「一键分解」重新出现。

        这个页面的返回图标和通用返回条模板不是同一张图，实测模板分数 -1.0，
        所以第一次走模板，后面改点页面固定的左上角返回按钮。
        """
        for attempt in range(attempts):
            if attempt == 0:
                if not self._click_top_left_back(screen.frame, 1):
                    continue
            else:
                self.operate_click(*SELECTION_BACK_POINT, name="退出选择页", after_sleep=0.8)
            found = self._wait_equipment(
                lambda view: view.dismantle_entry is not None,
                timeout=4.0,
            )
            if found is not None:
                return found
            screen = self._read_equipment()
            if screen.selection_page is None:
                return screen
        return None

    def _dismantle_once(self, inventory, cycle, attempt=None):
        before = inventory.inventory
        if before[0] == 0:
            return DISMANTLE_SKIPPED
        if any(template is None for template in self._grade_templates.values()):
            self.info_set(DISMANTLE_STEP, "缺少装备品质识别素材")
            return False
        selecting = self._enter_dismantle_selection(inventory)
        if selecting is None or selecting.inventory != before:
            return False
        selected = self._scan_selection_tab(selecting, before)
        if selected is DISMANTLE_SKIPPED:
            return DISMANTLE_SKIPPED
        if selected is None or selected.inventory != before:
            return False
        dialog = self._open_dismantle_dialog(selected, before)
        if dialog is None or dialog.dismantle_dialog[0] != 1 or equipment_daily_cycle() != cycle:
            return False
        self._record_step(
            DISMANTLE_STEP, cycle, "pending", {"inventory": list(before)}, attempt=attempt
        )
        self.operate_click(*dialog.dismantle_dialog[1], name="分解一件装备", after_sleep=0.8)
        expected = (before[0] - 1, before[1])
        return self._close_dismantle_result(expected_inventory=expected) is not None

    def _open_dismantle_dialog(self, selected, before):
        screen = selected
        for attempt in range(2):
            check = dismantle_check_point(screen.frame)
            if check is None:
                return None
            self.operate_click(*check, name="核对分解选择", after_sleep=0.5)
            dialog = self._wait_equipment(lambda view: view.dismantle_dialog is not None)
            if dialog is not None:
                return dialog
            if attempt == 0:
                screen = self._read_equipment()
                if screen.inventory != before or screen.selection_count != 1:
                    return None
        return None

    def _enter_dismantle_selection(self, inventory):
        if inventory.selection_page is not None:
            return inventory
        before = inventory.inventory
        screen = inventory
        for attempt in range(2):
            entry = screen.dismantle_entry
            if entry is None or screen.inventory != before:
                return None
            self.operate_click(*entry, name="进入装备分解选择", after_sleep=0.6)
            selecting = self._wait_equipment(lambda view: view.selection_count == 0)
            if selecting is not None:
                return selecting
            if attempt == 0:
                screen = self._read_equipment()
        return None

    def _close_dismantle_result(self, *, expected_inventory=None):
        max_clicks = 3
        previous_inventory = None
        result_frames = 0

        def ready(view):
            nonlocal previous_inventory, result_frames
            inventory = view.inventory if view.dismantle_entry is not None else None
            backpack_confirmed = (
                inventory is not None
                and inventory == previous_inventory
                and (expected_inventory is None or inventory == expected_inventory)
            )
            previous_inventory = inventory
            result_frames = result_frames + 1 if view.dismantle_result is not None else 0
            return backpack_confirmed or (attempt < max_clicks and result_frames >= 2)

        for attempt in range(max_clicks + 1):
            screen = self._wait_equipment(ready, timeout=8.0 if attempt == 0 else 5.0)
            if screen is None:
                break
            if screen.dismantle_entry is not None:
                return screen
            self.info_set(DISMANTLE_STEP, f"关闭分解结果（{attempt + 1}/{max_clicks}）")
            self.operate_click(*screen.dismantle_result, name="关闭分解结果", after_sleep=1.2)
            result_frames = 0
        self.info_set(DISMANTLE_STEP, "分解结果退出或背包数量仍待确认")
        return None

    def _refine_once(self, inventory, cycle, attempt=None):
        if getattr(inventory, "selection_page", None) is not None:
            inventory = self._leave_selection_page(inventory)
            if inventory is None:
                return False
        under_17_only = self.config.get(REFINE_MODE_KEY) == REFINE_MODE_UNDER_17
        max_score = 17 if under_17_only else MAX_REFINABLE_SCORE
        if not self._apply_refinement_filter(inventory, under_17_only):
            return False
        empty_frames = 0

        def filtered_grid(screen):
            nonlocal empty_frames
            if not screen.equipment_grid_visible:
                empty_frames = 0
                return False
            if not all(grid_blank_rows(screen.frame)):
                empty_frames = 0
                return True
            empty_frames += 1
            return empty_frames >= 2

        filtered = self._wait_equipment(filtered_grid, timeout=5.0)
        if filtered is None:
            self.info_set(REFINE_STEP, "未确认筛选后的装备列表")
            self._close_equipment_filter(self._read_equipment())
            return False
        if empty_frames >= 2:
            return REFINE_SKIPPED

        tried = []
        for _ in range(3):
            candidates = [
                (score, (x, y)) for score, (x, y) in filtered.scored_gear()
                if score <= max_score and not any(
                    abs(x - old_x) < GRID_CELL_SIZE[0] / 2
                    and abs(y - old_y) < GRID_CELL_SIZE[1] / 2
                    for old_x, old_y in tried
                )
            ]
            if candidates:
                _, gear_point = candidates[0]
            elif under_17_only and not tried and filtered.first_equipment_cell:
                gear_point = filtered.first_equipment_cell
            else:
                self.info_set(REFINE_STEP, f"筛选列表未识别到 6～{max_score} 分的可用装备")
                return False
            tried.append(gear_point)
            self.operate_click(*gear_point, name="查看筛选后的装备", after_sleep=0.5)
            detail = self._wait_equipment(lambda screen: screen.detail_refine is not None)
            if detail is None:
                return False
            self.operate_click(*detail.detail_refine, name="打开精炼装备分数", after_sleep=0.6)
            try:
                refine = self._wait_equipment(
                    lambda screen: screen.refine_maxed or screen.refine_once is not None
                )
            except RefinementConflict as error:
                self.info_set(REFINE_STEP, str(error))
                return False
            if refine is None:
                self.info_set(REFINE_STEP, "未确认精炼页面状态")
                return False
            if not refine.refine_maxed:
                break
            self.info_set(REFINE_STEP, "精炼页显示已达到最大强化级别，换下一件")
            filtered = self._leave_refinement(refine)
            if filtered is not None and filtered.detail_close:
                filtered = self._close_equipment_detail(filtered)
            if filtered is None or not filtered.equipment_grid_visible:
                return False
        else:
            return False
        if equipment_daily_cycle() != cycle:
            return False
        costs, point = refine.refine_once
        remaining = [balance - cost for balance, cost in zip(refine.refine_balances, costs)]
        if min(remaining) < 0:
            self.info_set(REFINE_STEP, "精炼粉末或金币不足")
            return False
        self._record_step(REFINE_STEP, cycle, "pending", {"remaining": remaining}, attempt=attempt)
        self.operate_click(*point, name="精炼一次", after_sleep=0.8)
        return self._confirm_refinement(remaining)

    def _apply_refinement_filter(self, inventory, under_17_only):
        if not inventory.equipment_grid_visible:
            self.info_set(REFINE_STEP, "未确认装备背包筛选入口")
            return False
        self.operate_click(*FILTER_BUTTON_POINT, name="打开装备筛选", after_sleep=0.5)
        dialog = self._wait_equipment(lambda screen: screen.filter_dialog, timeout=5.0)
        for score, wanted, action in (
            (False, True, "筛选+9以上装备"),
            (True, under_17_only, "筛选17分以下装备" if under_17_only else "取消17分筛选"),
        ):
            if dialog is None:
                break
            point, selected = dialog.filter_choice(score=score)
            if selected is None:
                dialog = None
                break
            if selected is not wanted:
                self.operate_click(*point, name=action, after_sleep=0.35)
                dialog = self._wait_equipment(
                    lambda screen: screen.filter_choice(score=score)[1] is wanted, timeout=4.0
                )
        if (
            dialog is None or dialog.filter_choice(score=False)[1] is not True
            or dialog.filter_choice(score=True)[1] is not under_17_only
            or dialog.filter_confirm is None
        ):
            self.info_set(REFINE_STEP, "未确认装备筛选条件或确认按钮")
            self._close_equipment_filter(self._read_equipment())
            return False
        # 「确认」本次筛选，保留游戏的已保存偏好。
        self.operate_click(*dialog.filter_confirm, name="确认装备筛选", after_sleep=0.6)
        return True

    def _close_equipment_filter(self, screen):
        if not screen.filter_overlay:
            return None
        if screen.filter_cancel:
            self.operate_click(*screen.filter_cancel, name="关闭装备筛选", after_sleep=0.6)
        elif not self.dismiss_blocking_modal(screen.frame, context="装备筛选"):
            return None
        return self._wait_equipment(lambda view: view.equipment_grid_visible)

    def _read_refinement_badges(self, screen, expected_score):
        scores = []
        has_crop = False
        number = r"[0-9]{1,2}"
        badges = screen.refinement_score_badges()
        for bounds, badge in zip(REFINE_SCORE_TEXT_BOUNDS, badges, strict=True):
            # The stylized badge can be detected at one image scale only.
            full = screen.matches(number, bounds)
            boxes = self.ocr(frame=badge, threshold=0.55, name="精炼装备分数徽章")
            crop = EquipmentScreen(badge, boxes).matches(number, (0, 0, 1, 1))
            has_crop |= bool(crop)
            scores.append(refinement_value("精炼徽章", (int(m[0]) for m, _ in full + crop)))
        if not has_crop:
            refinement_value("背包与精炼徽章", [expected_score, *scores])
        return tuple(scores)

    def _confirm_refinement(self, remaining):
        if (
            not isinstance(remaining, list)
            or len(remaining) != 2
            or any(type(amount) is not int or amount < 0 for amount in remaining)
        ):
            return False
        expected = tuple(remaining)
        matching_frames = 0

        def check(screen):
            nonlocal matching_frames
            balances = screen.refine_balances
            if balances is None and screen.refinement_page:
                left, top, right, bottom = REFINE_BALANCE_BOUNDS
                boxes = self.ocr(
                    x=left,
                    y=top,
                    to_x=right,
                    to_y=bottom,
                    frame=screen.frame,
                    threshold=0.55,
                    name="精炼扣款余额复核",
                )
                balances = EquipmentScreen(screen.frame, boxes).refine_currency_amounts()
            matching_frames = matching_frames + 1 if balances == expected else 0
            return matching_frames >= 2

        verified = self._wait_equipment(check, timeout=12.0) is not None
        if verified:
            self.info_set("精炼扣款复核", f"已复核粉末余量 {expected[0]}、金币余量 {expected[1]}")
        return verified

    def _leave_refinement(self, screen):
        def left_forge(view):
            return not view.refinement_page and (view.detail_close or view.dismantle_entry)

        if self._click_top_left_back(screen.frame, 1):
            returned = self._wait_equipment(left_forge, timeout=3.0)
            if returned is not None:
                return returned
        screen = self._read_equipment()
        if left_forge(screen):
            return screen
        if not screen.refinement_page:
            return None
        self.operate_click(*REFINEMENT_BACK_POINT, name="退出精炼页", after_sleep=0.7)
        returned = self._wait_equipment(left_forge, timeout=4.0)
        if returned is not None:
            return returned
        return self._wait_equipment(left_forge, timeout=2.0)

    def _close_equipment_detail(self, screen):
        self.operate_click(*screen.detail_close, name="关闭装备详情", after_sleep=0.6)
        return self._wait_equipment(lambda view: view.equipment_grid_visible)

    def _leave_equipment(self):
        screen = self._read_equipment()
        if screen.refinement_page:
            screen = self._leave_refinement(screen)
            if screen is None:
                return False
        if screen.detail_close:
            screen = self._close_equipment_detail(screen)
            if screen is None:
                return False
        if screen.dismantle_entry or (screen.bag_open and screen.inventory is None):
            self.operate_click(*BACKPACK_BACK_POINT, name="退出装备背包", after_sleep=0.8)
        return self.recover_daily_home()

    def _needs_reconciliation(self) -> bool:
        try:
            record = run_history.default_store().stage(EQUIPMENT_DAILY_OWNER, REFINE_STEP)
            return record.get("state") == "pending"
        except RuntimeError:
            return True

    @property
    def waits_for_login(self):
        return not self._needs_reconciliation()

    def recover_daily_home(self) -> bool:
        return not self._needs_reconciliation() and super().recover_daily_home()

    def _fail(self, stage):
        # 失败时留一张现场图，方便下次按图校准（实机识别问题很难只靠日志定位）。
        try:
            path = self.save_frame("equipment_daily_failed", self.capture_frame())
            self.log_warning(f"装备日常失败截图：{path}")
        except Exception as exc:
            self.log_warning(f"装备日常失败截图保存失败：{exc}")
        self.info_set("状态", f"{self.name}未完成：{stage}。")
        return False

    def run(self):
        if not bool(self.config.get("启用", True)):
            return self._fail("任务未启用")
        cycle = equipment_daily_cycle()
        self.info_set("日常周期", float(cycle))
        steps = ((DISMANTLE_STEP, self._dismantle_once), (REFINE_STEP, self._refine_once))
        store = run_history.default_store()
        records = {step: store.stage(EQUIPMENT_DAILY_OWNER, step) for step, _ in steps}
        pending = [step for step, record in records.items() if record.get("state") == "pending"]
        if len(pending) > 1:
            return self._fail("多笔装备操作仍待确认")
        recovered = False
        if pending:
            step = pending[0]
            record = records[step]
            dismantle = records[DISMANTLE_STEP]
            if step == REFINE_STEP and (
                dismantle.get("state") != "done"
                or dismantle.get("scope") != record["scope"]
                or dismantle["evidence"].get("attempt") != record["evidence"].get("attempt")
            ):
                return self._fail("上次提交的分解和精炼记录不一致")
            self.info_set("状态", f"核对上次提交：{step}")
            if not self._reconcile_submission(step, record["evidence"]):
                return self._fail(f"上次提交的{step}仍待确认")
            self._record_step(step, record["scope"], "done", record["evidence"])
            records[step] = {**record, "state": "done"}
            recovered = record["scope"] == cycle

        completed = {
            step: record["evidence"].get("attempt")
            for step, record in records.items()
            if record.get("state") == "done" and record.get("scope") == cycle
        }
        both_done = (
            len(completed) == len(steps) and completed[DISMANTLE_STEP] == completed[REFINE_STEP]
        )
        only_incomplete = bool(self.config.get("_仅执行未完成步骤", False))
        # Keep a partially completed attempt together. A full run requested again
        # after completion must start a distinct attempt, even in the same cycle.
        if DISMANTLE_STEP in completed and (not both_done or only_incomplete or recovered):
            attempt = completed[DISMANTLE_STEP]
        else:
            attempt = uuid4().hex

        for step, perform in steps:
            if step in completed and completed[step] == attempt:
                skipped = records[step]["evidence"].get("skipped") == "no_eligible_equipment"
                status = "已确认完成" if recovered or not both_done else "本日已完成"
                self.info_set(step, "没有可分解的非 UR 装备，已跳过" if skipped else status)
                continue
            self.info_set("状态", f"装备日常：{step}")
            inventory = self._open_inventory()
            outcome = perform(inventory, cycle, attempt) if inventory is not None else False
            if not outcome:
                return self._fail(f"未能确认{step}")
            if outcome is DISMANTLE_SKIPPED:
                self._record_step(
                    step, cycle, "done", {"skipped": "no_eligible_equipment"}, attempt=attempt
                )
                self.info_set(step, "没有可分解的非 UR 装备，已跳过")
                continue
            if outcome is REFINE_SKIPPED:
                self._record_step(
                    step, cycle, "done", {"skipped": "no_refinable_equipment"}, attempt=attempt
                )
                self.info_set(step, "当前筛选条件下没有装备，已跳过")
                continue
            submission = store.stage(EQUIPMENT_DAILY_OWNER, step)
            evidence = submission.get("evidence") if submission.get("state") == "pending" else None
            self._record_step(step, cycle, "done", evidence, attempt=attempt)
            self.info_set(step, "已确认完成")
        self._leave_equipment()
        self.info_set("状态", f"{self.name}完成。")
        self.log_completion("装备日常：分解和精炼流程已结束。")
        return True
