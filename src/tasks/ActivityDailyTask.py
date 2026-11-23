"""Run one daily batch of event quick battles."""

from datetime import datetime
from time import monotonic

from src.diagnostics.runtime import capture_failure_frame
from src.icons import TaskIcon
from src.tasks import run_history
from src.tasks.BaseBD2Task import BaseBD2Task
from src.utils.activity_screen import (
    ACTIVITY_DAILY_NAME,
    ACTIVITY_DAILY_OWNER,
    AP_COUNTER,
    FREE_AP_SWITCH,
    MODES,
    QUICK_BATTLE_STEP,
    QUICK_BUTTON_BOUNDS,
    STAGE_LIST,
    STAGES,
    ActivityScreen,
)
from src.utils.calibration import FHD_1080
from src.utils.image_utils import crop_relative
from src.utils.ocr_utils import normalize_ocr_text

STAGE_ROW_SPACING = 0.0915
STAGE_ROW_OFFSETS = (0.0, -0.006, 0.006, -0.012, 0.012)
STAGE_NUMBER_CROP = (0.1275, 0.1495, 0.021)
STAGE_SINGLE_DIGIT_CROP = (0.137, 0.147, 0.019)
STAGE_TWO_DIGIT_CROP = (0.128, 0.1535, 0.023)
STAGE_DIGIT_MIN_CONFIDENCE = 0.88
ACKNOWLEDGED_AP_DAY = "已人工核对的活动AP日期"


def _older_ap_day(record, current_cycle):
    try:
        previous = float(record["scope"])
        if previous < current_cycle and previous == run_history.day_start_ts(previous):
            return datetime.fromtimestamp(previous, run_history.BEIJING_TZ).date().isoformat()
    except (OverflowError, OSError, ValueError):
        pass
    return None


def activity_ap_needs_review(config):
    try:
        record = run_history.default_store().stage(ACTIVITY_DAILY_OWNER, QUICK_BATTLE_STEP)
    except RuntimeError:
        return True
    if record.get("state") != "pending":
        return False
    old_day = _older_ap_day(record, run_history.day_start_ts())
    return not old_day or str(config.get(ACKNOWLEDGED_AP_DAY, "")).strip() != old_day


class ActivityDailyTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = ACTIVITY_DAILY_NAME
        self.description = "优先挑战战斗，按15、10、5关查找可快速战斗的关卡并使用免费活动AP。"
        self.icon = TaskIcon.FLAG
        self.group_name = "日常/周常"
        self.group_icon = TaskIcon.CALENDAR
        self.visible = True
        self.default_config.update({"启用": True, ACKNOWLEDGED_AP_DAY: ""})
        self.config_description[ACKNOWLEDGED_AP_DAY] = (
            "跨日仍有待核对扣款时，在游戏中核对后填写提示的旧日期（YYYY-MM-DD）。"
            "下次运行会归档该日记录，再按当天免费 AP 执行。"
        )

    def _read_activity(self):
        frame = self.capture_frame()
        return ActivityScreen(frame, self.ocr(frame=frame, threshold=0.55, target_height=1080))

    def _wait_activity(self, predicate, timeout=6.0):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            view = self._read_activity()
            if predicate(view):
                return view
            self.sleep(0.35)
        return None

    def _region(self, view, bounds, name):
        x, y, to_x, to_y = bounds
        boxes = self.ocr(
            x=x,
            y=y,
            to_x=to_x,
            to_y=to_y,
            frame=view.frame,
            threshold=0.55,
            name=name,
        )
        return ActivityScreen(view.frame, boxes)

    def _ap(self, view):
        return view.ap or self._region(view, AP_COUNTER, "活动AP").ap

    def _back_to_menu(self, view):
        for attempt in range(2):
            if view.menu:
                return view
            if view.stage_page is None:
                return None
            if view.battle_back:
                self.operate_click(
                    *view.battle_back,
                    name="退出活动战斗列表",
                    after_sleep=0.7,
                    require_fresh_target=True,
                )
            elif not self._click_top_left_back(view.frame, attempt + 1):
                return None
            view = self._wait_activity(lambda screen: screen.menu, timeout=3.5)
            if view:
                return view
            view = self._read_activity()
        return view if view.menu else None

    def _open_menu(self, view):
        if view.menu:
            return view
        if view.stage_page:
            return self._back_to_menu(view)
        if view.dialog:
            cancel = view.dialog_cancel
            if cancel is None:
                return None
            self.operate_click(*cancel, name="关闭活动快速战斗弹窗", after_sleep=0.65)
            battle = self._wait_activity(lambda screen: screen.stage_page == view.dialog)
            return self._back_to_menu(battle) if battle else None
        if not (self._frame_confirms_home(view.frame, "每日活动入口") or self.recover_daily_home()):
            return None
        for _ in range(2):
            home = self._wait_activity(lambda screen: screen.banner or screen.menu)
            if home is None or home.menu:
                return home
            self.operate_click(*home.banner, name="打开每日活动", after_sleep=0.8)
            menu = self._wait_activity(lambda screen: screen.menu)
            if menu:
                return menu
        return None

    def _open_mode(self, menu, mode):
        def entered(view):
            return view.stage_page is not None and view.stage_page[0] == mode

        last_point = menu.menu[mode] if menu.menu_back else None

        def ready(view):
            nonlocal last_point
            if entered(view):
                return True
            point = view.menu[mode] if view.menu_back else None
            stable = (
                point
                and last_point
                and all(
                    abs(current - previous) < 0.005 for current, previous in zip(point, last_point)
                )
            )
            last_point = point
            return bool(stable)

        for attempt in range(1, 3):
            current = self._wait_activity(ready, timeout=4)
            if current is None:
                break
            if entered(current):
                return current
            self.info_set("活动战斗入口", f"打开{mode}（{attempt}/2）")
            self.operate_click(
                *current.menu[mode],
                name=mode,
                down_time=0.12,
                after_sleep=0.8,
                require_fresh_target=True,
            )
            last_point = None
            battle = self._wait_activity(entered, timeout=4)
            if battle:
                return battle
        return None

    def _stage_rows(self, view, stage):
        rows = view.stage_rows()
        if stage not in rows:
            cropped = self._region(view, STAGE_LIST, "活动关卡列表").stage_rows()
            rows.update({number: point for number, point in cropped.items() if number not in rows})
        if not rows and view.height < FHD_1080.height and view.stage_page:
            boxes = self._quick_vision().ocr_boxes(
                view.frame, "活动关号放大", relative_roi=STAGE_LIST,
                ocr_scale=2, target_height=0, minimum_threshold=.55,
            )
            enlarged = ActivityScreen(view.frame, boxes).stage_rows()
            selected = view.stage_page[1]
            if selected in enlarged:
                rows[selected] = enlarged[selected]
        return rows

    def _read_stage_digit(self, view, rows, stage):
        if not rows:
            return None
        # Anchor the search to a recognized row, including when it is the only one.
        anchor = min(rows, key=lambda number: abs(number - stage))
        expected_y = rows[anchor][1] + (stage - anchor) * STAGE_ROW_SPACING
        _, top, _, bottom = STAGE_LIST
        centers = [
            expected_y + offset for offset in STAGE_ROW_OFFSETS
            if top + 0.023 <= expected_y + offset <= bottom - 0.023
        ]
        if not centers:
            return None
        # A single digit leaves the adjacent chevron exposed in the wider crop.
        verify_crop = STAGE_SINGLE_DIGIT_CROP if stage < 10 else STAGE_TWO_DIGIT_CROP
        digit_crops = (STAGE_NUMBER_CROP, verify_crop)
        crops = [
            crop_relative(view.frame, (left, y - radius, right, y + radius))
            for y in centers
            for left, right, radius in digit_crops
        ]
        # Run recognition on the row crops directly. The text detector can omit
        # unselected digits even when given a small crop of the correct row.
        reads = self.executor.ocr_lib().ocr(crops, det=False, cls=False)[0]
        if len(reads) != len(crops):
            return None
        count = len(digit_crops)
        for index, y in enumerate(centers):
            row_reads = reads[index * count:(index + 1) * count]
            if all(
                confidence >= STAGE_DIGIT_MIN_CONFIDENCE
                and normalize_ocr_text(text).rstrip(">»〉》+") == str(stage)
                for text, confidence in row_reads
            ):
                return 0.142, y
        return None

    @staticmethod
    def _stage_point(rows, stage):
        if stage in rows:
            return rows[stage]
        if len(rows) < 2:
            return None
        # The visible rows are evenly spaced. A tap is checked against the detail title.
        first, last = min(rows), max(rows)
        if not first - 1 <= stage <= last + 1:
            return None
        first_x, first_y = rows[first]
        spacing = (rows[last][1] - first_y) / (last - first)
        y = first_y + (stage - first) * spacing
        if (
            0.081 <= spacing <= 0.105
            and 0.108 < y < 0.97
            and all(
                abs(row_y - (first_y + (number - first) * spacing)) < 0.026
                for number, (_, row_y) in rows.items()
            )
        ):
            return first_x, y
        return None

    def _select_stage(self, view, mode, stage):
        target = (mode, stage)
        natural_direction = -1 if stage == 15 else 1
        last_scroll = None
        for attempt in range(7):
            if view.stage_page is None or view.stage_page[0] != mode:
                return None
            rows = self._stage_rows(view, stage)
            point = self._stage_point(rows, stage)
            if point is None:
                point = self._read_stage_digit(view, rows, stage)
                if point:
                    self.info_set("活动找关", f"{mode}{stage}关 · 局部关号复核")
            if point:
                self.operate_click(
                    *point,
                    name=f"查看{mode}{stage}关",
                    down_time=0.10,
                    after_sleep=0.65,
                    require_fresh_target=True,
                )
                selected = self._wait_activity(
                    lambda screen: screen.stage_page == target, timeout=3
                )
                if selected:
                    return selected
                view = self._read_activity()
                if view.stage_page == target:
                    return view
                if view.stage_page is None or view.stage_page[0] != mode:
                    return None
                rows = self._stage_rows(view, stage)
            if attempt == 6:
                break
            if rows and stage > max(rows):
                direction = -1
            elif rows and stage < min(rows):
                direction = 1
            else:
                direction = natural_direction if attempt < 3 else -natural_direction
            row_positions = tuple(sorted((number, round(y, 2)) for number, (_, y) in rows.items()))
            self.info_set("活动找关", f"{mode}{stage}关 · 左列可见 {sorted(rows)}")
            if last_scroll == (direction, row_positions):
                # Some scroll views only move after dragging inside the left column.
                top, bottom = round(view.height * 0.32), round(view.height * 0.76)
                start_y, end_y = (bottom, top) if direction < 0 else (top, bottom)
                x = round(view.width * 0.155)
                self.drag_client((x, start_y), (x, end_y), duration=0.45, after_sleep=0.55)
                last_scroll = None
            else:
                self.scroll_client((0.155, 0.53), direction, count=3, after_sleep=0.55)
                last_scroll = (direction, row_positions)
            view = self._read_activity()
        return None

    def _quick_stage(self, target):
        previous_button = None

        def stable(view):
            nonlocal previous_button
            button = None
            if view.stage_page == target:
                button = (
                    view.quick_button()
                    or self._region(view, QUICK_BUTTON_BOUNDS, "活动快速战斗按钮").quick_button()
                )
            confirmed = (
                button is not None
                and previous_button is not None
                and button[0] == previous_button[0]
            )
            previous_button = button
            return confirmed

        view = self._wait_activity(stable, timeout=4)
        return (view, previous_button) if view else None

    def _battle_dialog(self, target, point):
        self.operate_click(*point, name="打开活动快速战斗", after_sleep=0.65)
        view = self._wait_activity(lambda screen: screen.dialog == target)
        if view and view.free_only() is False:
            self.operate_click(*FREE_AP_SWITCH, name="仅使用免费活动AP", after_sleep=0.55)
            view = self._wait_activity(
                lambda screen: screen.dialog == target and screen.free_only() is True
            )
        return view if view and view.free_only() is True else None

    def _confirm_spend(self, evidence):
        mode, before, capacity, amount = (
            evidence.get(key) for key in ("mode", "before", "capacity", "amount")
        )
        if mode not in MODES or not (
            all(type(number) is int for number in (before, capacity, amount))
            and 0 < amount <= before <= capacity
        ):
            return False
        expected = (before - amount, capacity)
        matching_frames = result_frames = clicks = 0
        deadline = monotonic() + 16
        while monotonic() < deadline:
            view = self._read_activity()
            if view.stage_page and view.stage_page[0] == mode and self._ap(view) == expected:
                matching_frames += 1
                if matching_frames >= 2:
                    self.info_set("活动AP复核", f"{before} → {expected[0]}，已扣除 {amount}")
                    return True
            else:
                matching_frames = 0
            _, dismiss = view.result
            result_frames = result_frames + 1 if dismiss else 0
            if dismiss and result_frames >= 2 and clicks < 3:
                self.operate_click(*dismiss, name="关闭活动战斗结果", after_sleep=1.2)
                result_frames = 0
                clicks += 1
            self.sleep(0.4)
        return False

    def _fight(self, view, point, ap, cycle):
        target = view.stage_page
        dialog = self._battle_dialog(target, point)
        if dialog is None or dialog.max_button() is None:
            return self._stop("快速战斗弹窗或免费活动AP开关未确认")
        self.operate_click(*dialog.max_button(), name="活动快速战斗 MAX", after_sleep=0.6)
        amount, capacity = ap

        def verified(screen):
            if screen.dialog != target or screen.free_only() is not True:
                return False
            submission = screen.submission()
            return submission is not None and submission[0] == amount and self._ap(screen) == ap

        ready = self._wait_activity(verified)
        if ready is None or str(run_history.day_start_ts()) != cycle:
            return self._stop("MAX 次数、AP费用或日常周期未通过提交复核")
        evidence = {
            "mode": target[0],
            "stage": target[1],
            "before": amount,
            "capacity": capacity,
            "amount": amount,
        }
        run_history.save_stage(self, QUICK_BATTLE_STEP, "pending", scope=cycle, evidence=evidence)
        self.operate_click(
            *ready.submission()[1],
            name=f"快速战斗 {amount} 次",
            after_sleep=1.0,
            require_fresh_target=True,
        )
        if not self._confirm_spend(evidence) or str(run_history.day_start_ts()) != cycle:
            return self._stop("已提交快速战斗，活动AP扣除仍待核对")
        run_history.save_stage(self, QUICK_BATTLE_STEP, "done", scope=cycle, evidence=evidence)
        return self._complete(f"{target[0]}{target[1]}关快速战斗 {amount} 次完成")

    def _stop(self, reason):
        self.info_set("状态", f"每日活动未完成：{reason}。")
        self.log_warning(reason)
        capture_failure_frame(self, "activity_daily_failed")
        return False

    def _complete(self, reason):
        self.info_set("状态", reason)
        self.log_completion(reason)
        return True

    @property
    def waits_for_login(self):
        return not activity_ap_needs_review(self.config)

    def recover_daily_home(self) -> bool:
        return not activity_ap_needs_review(self.config) and super().recover_daily_home()

    def run(self):
        if not bool(self.config.get("启用", True)):
            return False
        completed = self._run_activity()
        at_home = self.recover_daily_home()
        if completed and not at_home:
            return self._stop("活动结束后未确认回到游戏主页")
        return completed

    def _run_activity(self):
        cycle = str(run_history.day_start_ts())
        self.info_set("日常周期", float(cycle))
        record = run_history.read_stage(self, QUICK_BATTLE_STEP)
        if record.get("state") == "done" and record["scope"] == cycle:
            return self._complete("今日活动快速战斗已完成")
        if record.get("state") == "pending" and record["scope"] != cycle:
            day = _older_ap_day(record, float(cycle))
            if not day:
                return self._stop("待核对的活动AP日期异常，请检查运行记录")
            if str(self.config.get(ACKNOWLEDGED_AP_DAY, "")).strip() != day:
                return self._stop(
                    f"请在游戏中核对 {day} 的活动AP扣款，再将「{ACKNOWLEDGED_AP_DAY}」填为 {day}"
                )
            run_history.default_store().retire_stages(
                run_history.stage_owner(self), {QUICK_BATTLE_STEP: record},
                f"已人工核对 {day} 活动AP扣款",
            )
            self.info_set("上期活动AP", f"{day} 的扣款记录已归档")
            record = {}
        view = self._read_activity()
        if record.get("state") == "pending":
            evidence = record["evidence"]
            mode = evidence.get("mode")
            if mode not in MODES:
                return self._stop("上次活动AP扣除记录异常，仍待核对")
            if not view.result[0] and (view.battle is None or view.battle[0] != mode):
                menu = self._open_menu(view)
                if menu is None or self._open_mode(menu, mode) is None:
                    return self._stop("无法返回上次提交的活动战斗页")
            if not self._confirm_spend(evidence) or str(run_history.day_start_ts()) != cycle:
                return self._stop("上次快速战斗的活动AP扣除仍待核对")
            run_history.save_stage(self, QUICK_BATTLE_STEP, "done", scope=cycle, evidence=evidence)
            return self._complete("上次快速战斗的活动AP扣除已确认")
        menu = self._open_menu(view)
        if menu is None:
            return self._stop("未确认每日活动入口")
        for mode in MODES:
            stage_page = self._open_mode(menu, mode)
            if stage_page is None:
                return self._stop(f"未确认{mode}列表")
            ap = self._ap(stage_page)
            if ap is None:
                return self._stop(f"未读到{mode}活动AP")
            if ap[0] > 0:
                for stage in STAGES:
                    selected = self._select_stage(stage_page, mode, stage)
                    if selected is None:
                        return self._stop(f"未确认{mode}{stage}关")
                    confirmed = self._quick_stage((mode, stage))
                    if confirmed is None:
                        return self._stop(f"{mode}{stage}关快速战斗状态不明")
                    stage_page, (enabled, point) = confirmed
                    self.info_set(
                        "活动关卡",
                        f"{mode}{stage}关 · {'可快速战斗' if enabled else '不可快速战斗'}",
                    )
                    if enabled:
                        current_ap = self._ap(stage_page)
                        if current_ap is None or current_ap != ap:
                            return self._stop("活动AP在选择关卡时发生变化或读数不明")
                        return self._fight(stage_page, point, ap, cycle)
            menu = self._back_to_menu(stage_page)
            if menu is None:
                return self._stop(f"退出{mode}后未确认活动主页")
        return self._complete("挑战战斗和普通战斗均无可执行的快速战斗，已跳过")
