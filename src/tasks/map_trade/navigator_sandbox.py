from __future__ import annotations

from time import monotonic

import numpy as np

from src.tasks.map_trade.models import (
    CARD_BY_ID,
    CardSpec,
    CollectionMapRole,
    CollectionMapTarget,
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.navigator_constants import (  # noqa: F401
    AREA_MAP_BACK_TEMPLATE,
    AREA_MAP_CHANGE_INTERVAL,
    AREA_MAP_CHANGE_TIMEOUT,
    AREA_MAP_CLICK_SETTLE_SECONDS,
    AREA_MAP_OPEN_REFERENCE_POINT,
    AREA_MAP_OPEN_RELATIVE_POINT,
    AREA_MAP_REFERENCE_SIZE,
    AREA_MAP_SCAN_LIMIT,
    AREA_MAP_TELEPORT_BRIGHT_MAXIMUM_SPREAD,
    AREA_MAP_TELEPORT_BRIGHT_MINIMUM_GRAY,
    AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
    AREA_MAP_TELEPORT_BRIGHT_RADIUS_RATIO,
    AREA_MAP_TELEPORT_CLUSTER_RADIUS,
    BARGAIN_CONFIRM_POINT,
    BARGAIN_POINT,
    BARGAIN_SHOP_CONFIRM_POPUP_KEYWORD,
    BARGAIN_SHOP_CONFIRM_STABLE_HITS,
    CHAPTER_HOME_POINT,
    DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
    DISCOUNT_SHOP_CLOSE_KEYWORDS,
    DISCOUNT_SHOP_CLOSE_POINT,
    DISCOUNT_SHOP_CLOSE_TIMEOUT,
    FIRST_CARD_CONFIRM_REGION,
    FIRST_CARD_INSERT_REGION,
    FIRST_CARD_SKIP_TEMPLATE,
    HAND_TEMPLATE,
    LOADING_TEMPLATE,
    MAP_PAGE_MODE_STABLE_HITS,
    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    MERCHANT_DIALOG_CONFIRM_TIMEOUT,
    OVERLAP_ARROW_TEMPLATE,
    PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
    PROBE_QUICK_SWITCH_SCROLL_COUNT,
    PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_POINT,
    PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_STEPS,
    PROBE_STORY_BADGE_CONFIRM_SECONDS,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PAGE_KEYWORDS,
    Q_SP6_SHOP_PAGE_OCR_INTERVAL,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_STORY_NUMBER,
    QUICK_SWITCH_CARTRIDGE_REGION,
    QUICK_SWITCH_PAGE_KEYWORDS,
    QUICK_SWITCH_SCROLL_FOCUS_POINT,
    QUICK_SWITCH_SCROLL_INTERVAL,
    QUICK_SWITCH_SCROLL_POINT,
    QUICK_SWITCH_SCROLL_RESET_AMOUNT,
    QUICK_SWITCH_SCROLL_RESET_COUNT,
    QUICK_SWITCH_SCROLL_SCAN_STEPS,
    QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    QUICK_SWITCH_SCROLL_UP_AMOUNT,
    QUICK_SWITCH_SCROLL_UP_COUNT,
    QUICK_SWITCH_TEMPLATE,
    RETURN_HOME_TIMEOUT,
    SANDBOX_CONFIRM_ACTION_TEMPLATES,
    SANDBOX_INTERACTION_PROBE_INTERVAL,
    SANDBOX_INTERACTION_PROBE_TIMEOUT,
    SANDBOX_LARGE_MAP_RETURN_REFERENCE_POINT,
    SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT,
    SANDBOX_MAP_SETTLE_SECONDS,
    SANDBOX_MAP_TELEPORT_TEMPLATE,
    SANDBOX_MAP_TELEPORT_TIMEOUT,
    SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
    SANDBOX_NAVIGATION_MAP_TIMEOUT,
    SANDBOX_NAVIGATION_OCR_INTERVAL,
    SANDBOX_NAVIGATION_OPEN_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_OPEN_TEMPLATES,
    SANDBOX_NAVIGATION_OPEN_TIMEOUT,
    SANDBOX_NAVIGATION_PIN_TEMPLATE,
    SANDBOX_NAVIGATION_RUN_TEMPLATE,
    SANDBOX_NAVIGATION_TELEPORT_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_WALK_TIMEOUT,
    SANDBOX_SKILL_GROUP_PIXEL_SCORE,
    SANDBOX_SKILL_GROUP_SCALE_RATIOS,
    SANDBOX_SKILL_GROUP_SEARCH_ROI,
    SANDBOX_SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
    SANDBOX_SKILL_GROUP_ZNCC_SCORE,
    SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO,
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
    SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
    SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_CENTER_ROI,
    SANDBOX_SKILL_SLOT_2_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_STATE_TEMPLATES,
    SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO,
    SANDBOX_TELEPORT_SKILL_FAILURE_GROUPS,
    SANDBOX_TELEPORT_SKILL_POLL_INTERVAL,
    SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER,
    SANDBOX_TELEPORT_SKILL_RELATIVE_POINT,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
    SANDBOX_TELEPORT_SKILL_TIMEOUT,
    SANDBOX_TEMPLATES,
    SHOP_PAGE_OCR_KEYWORDS,
    STORY_BADGE_CANDIDATE_PIXEL_SCORE,
    STORY_BADGE_CANDIDATE_SCORE,
    STORY_BADGE_CANDIDATE_ZNCC_SCORE,
    STORY_BADGE_CENTER_REGION,
    STORY_BADGE_CLUSTER_RADIUS,
    STORY_BADGE_MIN_MARGIN,
    STORY_BADGE_OCR_BINARY_THRESHOLD,
    STORY_BADGE_OCR_HORIZONTAL_BORDER,
    STORY_BADGE_OCR_INNER_HEIGHT,
    STORY_BADGE_OCR_INNER_RADIUS_RATIO,
    STORY_BADGE_OCR_MIN_CONFIDENCE,
    STORY_BADGE_OCR_VERTICAL_BORDER,
    STORY_BADGE_PIXEL_SCORE,
    STORY_BADGE_SPECS,
    STORY_BADGE_TEMPLATE_SCORE,
    STORY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    STORY_CATEGORY_HIGHLIGHT_REGION,
    STORY_CATEGORY_POINT,
    STORY_SANDBOX_STABLE_HITS,
    STORY_SANDBOX_SWITCH_WINDOW,
    STORY_SANDBOX_SWITCH_WINDOW_HITS,
    TELEPORT_GENERATION_OCR_INTERVAL,
    TELEPORT_GENERATION_OCR_KEYWORDS,
    TELEPORT_GENERATION_OCR_TIMEOUT,
    TELEPORT_INTERACTION_CLICK_DELAY,
    TELEPORT_INTERACTION_POLL_INTERVAL,
    TELEPORT_INTERACTION_TIMEOUT,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FIRST_PAGE_LIMIT,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_OPEN_TIMEOUT,
    TELEPORT_MAP_RETURN_REFERENCE_POINT,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_SKILL_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
    TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    TRADE_MERCHANT_CONTEXT_TEMPLATE,
    AreaMapContext,
    LocatedStoryCard,
    ProbedStoryCard,
    SandboxConfirmation,
    StoryBadgeCandidate,
    StoryBadgeDetection,
    _sandbox_skill_template,
)
from src.tasks.map_trade.vision import normalize_text


class SandboxNavigationMixin:
    @staticmethod
    def _format_sandbox_matches(
        matches: tuple[tuple[str, MatchResult, bool], ...],
    ) -> str:
        return "; ".join(
            (
                f"{name}={'pass' if passed else 'miss'}"
                f"(m={result.score:.3f},p={result.pixel_score:.3f},"
                f"z={result.zncc_score:.3f})"
            )
            for name, result, passed in matches
        )

    def _match_story_sandbox_signals(
        self,
        frame: np.ndarray,
    ) -> SandboxConfirmation:
        """Match all story-sandbox confirmation signals on one frame."""

        map_matches = tuple(
            (
                spec.name,
                result := self.vision.match(frame, spec),
                self.vision.passes(result, spec),
            )
            for spec in SANDBOX_TEMPLATES
        )
        map_signal_hits = sum(1 for _name, _result, passed in map_matches if passed)
        self._status(
            "箱庭确认信号",
            (
                f"命中={map_signal_hits}/2；"
                f"{self._format_sandbox_matches(map_matches)}"
            ),
        )

        skill_matches = tuple(
            (
                spec.name,
                result := self.vision.match(frame, spec),
                self.vision.passes(result, spec),
            )
            for spec in SANDBOX_SKILL_STATE_TEMPLATES
        )
        skill_state_hits = sum(1 for _name, _result, passed in skill_matches if passed)
        slot_states = (
            self._sandbox_skill_slot_state(
                frame,
                SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
                skill_matches[0][1],
                skill_matches[0][2],
                SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
                skill_matches[3][1],
                skill_matches[3][2],
            ),
            self._sandbox_skill_slot_state(
                frame,
                SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
                skill_matches[2][1],
                skill_matches[2][2],
                SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
                skill_matches[1][1],
                skill_matches[1][2],
            ),
        )
        skill_group = None
        if slot_states == ("selected", "unselected"):
            skill_group = 1
        elif slot_states == ("unselected", "selected"):
            skill_group = 2
        self._status(
            "箱庭技能组状态",
            (
                f"命中={skill_state_hits}/4；"
                f"颜色状态={slot_states}；"
                f"状态={'技能组' + str(skill_group) if skill_group else '未知/冲突'}；"
                f"{self._format_sandbox_matches(skill_matches)}"
            ),
        )

        action_matches = tuple(
            (
                name,
                result := self.vision.match(frame, spec),
                self.vision.passes(result, spec),
            )
            for name, spec in SANDBOX_CONFIRM_ACTION_TEMPLATES
        )
        action_hits = sum(1 for _name, _result, passed in action_matches if passed)
        self._status(
            "箱庭进一步确认",
            (
                f"动作图标命中={action_hits}/5；"
                f"{self._format_sandbox_matches(action_matches)}"
            ),
        )
        confirmation = SandboxConfirmation(
            map_signal_hits=map_signal_hits,
            skill_state_hits=skill_state_hits,
            action_hits=action_hits,
            skill_group=skill_group,
        )
        self._status(
            "箱庭复合确认",
            (
                f"{'pass' if confirmation.passed else 'miss'}；"
                f"地图={map_signal_hits}/2(至少1)，"
                f"技能组={skill_state_hits}/4(至少2)，"
                f"动作={action_hits}/5(至少3)"
            ),
        )
        return confirmation

    def _sandbox_skill_slot_state(
        self,
        frame: np.ndarray,
        selected_spec: TemplateSpec,
        selected_result: MatchResult,
        selected_passed: bool,
        unselected_spec: TemplateSpec,
        unselected_result: MatchResult,
        unselected_passed: bool,
    ) -> str:
        """Classify one skill slot from structure plus masked HSV semantics."""

        color_ratios = self.vision.template_hsv_color_ratios
        candidates = (
            (
                selected_spec,
                selected_result,
                selected_passed,
            ),
            (
                unselected_spec,
                unselected_result,
                unselected_passed,
            ),
        )
        colors = tuple(
            (
                spec,
                color_ratios(frame, spec, result),
            )
            for spec, result, passed in candidates
            if passed
        )

        selected_colors = next(
            (values for spec, values in colors if spec is selected_spec),
            None,
        )
        unselected_colors = next(
            (values for spec, values in colors if spec is unselected_spec),
            None,
        )
        def format_colors(values: tuple[float, float, float] | None) -> str:
            if values is None:
                return "missing"
            return f"y={values[0]:.3f},n={values[1]:.3f},b={values[2]:.3f}"
        self._status(
            "箱庭技能槽颜色",
            (
                f"{selected_spec.name}[{format_colors(selected_colors)}]；"
                f"{unselected_spec.name}[{format_colors(unselected_colors)}]"
            ),
        )
        selected_is_yellow = any(
            values[0] >= SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO
            for _spec, values in colors
            if values is not None
        )
        unselected_is_gray = any(
            values[0] <= SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO
            and values[1] >= 0.20
            for _spec, values in colors
            if values is not None
        )
        if selected_is_yellow and not unselected_is_gray:
            return "selected"
        if unselected_is_gray and not selected_is_yellow:
            return "unselected"
        return "unknown"

    def _click_sandbox_skill_group_1(self) -> None:
        """Switch to the first sandbox skill group using its calibrated center."""

        self._status(
            "箱庭技能组切换",
            (
                "技能组2已选中，点击技能组1预设中心"
                f"({SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER[0]}"
                f",{SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER[1]})"
            ),
        )
        self.task.operate_click(
            *SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
            after_sleep=SANDBOX_SKILL_GROUP_SWITCH_SETTLE_SECONDS,
        )

    def _wait_for_confirmed_sandbox(
        self,
        *,
        timeout: float,
        interval: float,
        success_message: str,
        failure_message: str,
        handle_intermediate: bool = False,
    ) -> NavigationResult:
        end_at = monotonic() + max(0.0, timeout)
        last_state = ScreenState.UNKNOWN
        sandbox_hits = 0
        skill_group_switch_attempted = False
        switch_group_one_history: list[bool] = []
        group_two_streak = 0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            last_state = self.classify(frame)
            self._status("导航状态", last_state.value)
            if last_state == ScreenState.SANDBOX:
                confirmation = self._match_story_sandbox_signals(frame)
                if (
                    confirmation.skill_group == 2
                    and not skill_group_switch_attempted
                ):
                    self._click_sandbox_skill_group_1()
                    skill_group_switch_attempted = True
                    sandbox_hits = 0
                    switch_group_one_history.clear()
                    continue
                if skill_group_switch_attempted and confirmation.skill_group != 1:
                    switch_group_one_history.append(False)
                    del switch_group_one_history[:-STORY_SANDBOX_SWITCH_WINDOW]
                    self._status(
                        "箱庭技能组切换",
                        (
                            "点击技能组1后仍未确认技能组1状态，"
                            f"切换窗口={sum(switch_group_one_history)}"
                            f"/{STORY_SANDBOX_SWITCH_WINDOW}"
                        ),
                    )
                    sandbox_hits = 0
                    if confirmation.skill_group == 2:
                        group_two_streak += 1
                        if group_two_streak >= STORY_SANDBOX_SWITCH_WINDOW:
                            self._status(
                                "箱庭技能组切换",
                                (
                                    f"点击技能组1后连续{group_two_streak}帧"
                                    "仍识别为技能组2，切换失败"
                                ),
                            )
                            return NavigationResult(
                                False,
                                last_state,
                                (
                                    f"点击技能组1后连续"
                                    f"{STORY_SANDBOX_SWITCH_WINDOW}"
                                    "帧仍识别为技能组2，切换失败"
                                ),
                            )
                    else:
                        group_two_streak = 0
                elif skill_group_switch_attempted:
                    group_two_streak = 0
                    switch_group_one_history.append(confirmation.passed)
                    del switch_group_one_history[:-STORY_SANDBOX_SWITCH_WINDOW]
                    window_hits = sum(switch_group_one_history)
                    self._status(
                        "箱庭稳定确认",
                        (
                            f"切换窗口={window_hits}"
                            f"/{STORY_SANDBOX_SWITCH_WINDOW_HITS}"
                            f"（{len(switch_group_one_history)}帧）"
                        ),
                    )
                    if (
                        len(switch_group_one_history) >= STORY_SANDBOX_SWITCH_WINDOW
                        and window_hits >= STORY_SANDBOX_SWITCH_WINDOW_HITS
                    ):
                        return NavigationResult(True, last_state, success_message)
                elif confirmation.passed:
                    sandbox_hits += 1
                    self._status(
                        "箱庭稳定确认",
                        f"{sandbox_hits}/{STORY_SANDBOX_STABLE_HITS}",
                    )
                    if sandbox_hits >= STORY_SANDBOX_STABLE_HITS:
                        return NavigationResult(True, last_state, success_message)
                else:
                    sandbox_hits = 0
            else:
                sandbox_hits = 0
            if (
                handle_intermediate
                and last_state not in {ScreenState.LOADING, ScreenState.SANDBOX}
                and self._handle_story_card_intermediate(frame)
            ):
                continue
            if interval > 0:
                self.task.sleep(interval)
        return NavigationResult(False, last_state, failure_message)

    def _wait_for_story_sandbox(
        self,
        target_number: int,
        timeout: float | None = None,
        interval: float = 0.5,
    ) -> NavigationResult:
        return self._wait_for_confirmed_sandbox(
            timeout=(
                self._loading_timeout() if timeout is None else float(timeout)
            ),
            interval=interval,
            success_message=f"Q_sp{target_number}",
            failure_message=f"剧情游戏卡{target_number}入场确认超时",
            handle_intermediate=True,
        )

    def _wait_for_current_sandbox(
        self,
        timeout: float = 3.0,
        interval: float = 0.5,
    ) -> NavigationResult:
        """Confirm the current story sandbox on consecutive composite frames."""

        return self._wait_for_confirmed_sandbox(
            timeout=timeout,
            interval=interval,
            success_message="已稳定确认剧情卡带箱庭",
            failure_message="未稳定确认当前剧情卡带箱庭",
        )

    def ensure_sandbox(self, card_id: str | None = None) -> NavigationResult:
        if card_id is not None:
            return self.select_card(card_id)
        state = self.classify()
        if state == ScreenState.SANDBOX:
            return self._wait_for_current_sandbox(timeout=self._loading_timeout())
        return self.ensure_card_menu()

    @staticmethod
    def _sandbox_teleport_skill_failure_matches(text: str) -> bool:
        normalized = normalize_text(text)
        return any(
            all(normalize_text(keyword) in normalized for keyword in group)
            for group in SANDBOX_TELEPORT_SKILL_FAILURE_GROUPS
        )

    def _sandbox_teleport_skill_failure_text(self, frame: np.ndarray) -> str:
        try:
            text = self.vision.simplify(
                self.vision.ocr_text(frame, "箱庭5号传送阵技能失败")
            )
        except Exception as exc:
            self._status("箱庭5号传送阵技能失败 OCR错误", str(exc))
            return ""
        if not self._sandbox_teleport_skill_failure_matches(text):
            return ""
        self._status("箱庭5号传送阵技能失败 OCR", text)
        return text

    def _click_sandbox_teleport_interaction(
        self,
        timeout: float = SANDBOX_INTERACTION_PROBE_TIMEOUT,
    ) -> bool:
        """Click the portal interaction prompt when the character is already nearby."""

        end_at = monotonic() + max(0.0, timeout)
        last = MatchResult(-1.0, (0, 0), (0, 0))
        while monotonic() <= end_at:
            frame = self.vision.capture()
            last = self.vision.match(frame, HAND_TEMPLATE)
            passed = self.vision.passes(last, HAND_TEMPLATE)
            self._status(
                "传送阵交互按钮",
                (
                    f"{'pass' if passed else 'miss'}; center={last.center}, "
                    f"match={last.score:.3f}, pixel={last.pixel_score:.3f}, "
                    f"zncc={last.zncc_score:.3f}"
                ),
            )
            if passed:
                self.vision.click_client(
                    last.center,
                    frame.shape,
                    after_sleep=TELEPORT_INTERACTION_CLICK_DELAY,
                )
                return True
            self.task.sleep(SANDBOX_INTERACTION_PROBE_INTERVAL)
        self._status(
            "传送阵交互按钮",
            (
                "探测超时；"
                f"last_match={last.score:.3f}, pixel={last.pixel_score:.3f}, "
                f"zncc={last.zncc_score:.3f}"
            ),
        )
        return False

    def _wait_for_sandbox_map_open(
        self,
        trigger_name: str,
        *,
        expected_mode: MapPageMode,
        timeout: float = TELEPORT_MAP_OPEN_TIMEOUT,
        detect_skill_failure: bool = False,
    ) -> NavigationResult:
        """Confirm a stable visual mode and reject entry/page semantic conflicts."""

        end_at = monotonic() + max(0.0, timeout)
        last_state = ScreenState.UNKNOWN
        stable_hits = 0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            detection = self._detect_map_page_mode(frame)
            if detection.mode.is_teleport_map:
                last_state = ScreenState.AREA_MAP
            elif detection.mode == MapPageMode.SANDBOX_LARGE_MAP:
                last_state = ScreenState.SANDBOX_MAP
            else:
                last_state = ScreenState.UNKNOWN
            if detection.mode == expected_mode:
                stable_hits += 1
            else:
                stable_hits = 0
            self._status(
                "传送阵地图确认",
                (
                    f"trigger={trigger_name}; expected={expected_mode.value}; "
                    f"observed={detection.mode.value}; "
                    f"stable={stable_hits}/{MAP_PAGE_MODE_STABLE_HITS}"
                ),
            )
            if stable_hits >= MAP_PAGE_MODE_STABLE_HITS:
                return NavigationResult(
                    True,
                    ScreenState.AREA_MAP,
                    f"{trigger_name}后确认{expected_mode.value}",
                    map_page_mode=detection.mode,
                )
            if detection.mode != MapPageMode.UNKNOWN and detection.mode != expected_mode:
                return NavigationResult(
                    False,
                    last_state,
                    (
                        f"{trigger_name}入口与实际地图页面不一致："
                        f"expected={expected_mode.value}, observed={detection.mode.value}"
                    ),
                    map_page_mode=detection.mode,
                )
            if detect_skill_failure:
                failure_text = self._sandbox_teleport_skill_failure_text(frame)
                if failure_text:
                    return NavigationResult(
                        False,
                        ScreenState.SANDBOX,
                        f"{trigger_name}失败 OCR命中：{failure_text}",
                    )
            self.task.sleep(0.5)
        return NavigationResult(
            False,
            last_state,
            f"{trigger_name}后未稳定确认{expected_mode.value}",
        )

    def _click_sandbox_navigation_map(
        self,
        timeout: float = SANDBOX_NAVIGATION_OPEN_TIMEOUT,
    ) -> bool:
        """Open the upper-left sandbox navigation map from a recognized icon."""

        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.vision.capture()
            for spec in SANDBOX_NAVIGATION_OPEN_TEMPLATES:
                result = self.vision.match(frame, spec)
                passed = self.vision.passes(result, spec)
                self._status(
                    spec.name,
                    (
                        f"{'pass' if passed else 'miss'}; center={result.center}, "
                        f"match={result.score:.3f}, pixel={result.pixel_score:.3f}, "
                        f"zncc={result.zncc_score:.3f}"
                    ),
                )
                if passed:
                    self.vision.click_client(
                        result.center,
                        frame.shape,
                        after_sleep=SANDBOX_NAVIGATION_OPEN_SETTLE_SECONDS,
                    )
                    return True
            self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL)
        return False

    def _sandbox_navigation_page_has_keyword(self, frame: np.ndarray) -> bool:
        return self._detect_map_page_mode(frame).mode == MapPageMode.SANDBOX_LARGE_MAP

    def _sandbox_navigation_teleport(
        self,
        frame: np.ndarray,
    ) -> MatchResult | None:
        try:
            candidates = self.vision.match_all(
                frame,
                SANDBOX_MAP_TELEPORT_TEMPLATE,
                minimum_score=self.vision.threshold_for(SANDBOX_MAP_TELEPORT_TEMPLATE),
                peak_radius=12,
            )
        except (AttributeError, TypeError, ValueError):
            candidates = ()
        passed = tuple(
            result
            for result in candidates
            if self.vision.passes(result, SANDBOX_MAP_TELEPORT_TEMPLATE)
        )
        self._status(
            "箱庭徒步导航传送阵",
            f"candidates={len(passed)}",
        )
        if len(passed) != 1:
            return None
        return passed[0]

    def _click_sandbox_navigation_menu_teleport(self, frame: np.ndarray) -> bool:
        """Select a stacked navigation-map teleport submenu by OCR center."""

        try:
            boxes = self.vision.ocr_boxes(frame, "箱庭徒步导航传送阵菜单")
        except Exception as exc:
            self._status("箱庭徒步导航传送阵菜单 OCR错误", str(exc))
            return False
        for box in boxes:
            label = normalize_text(
                self.vision.simplify(str(getattr(box, "name", "")))
            )
            if "魔法阵" not in label:
                continue
            center = self._ocr_box_center(box)
            if center is None:
                continue
            self.vision.click_client(
                center,
                frame.shape,
                after_sleep=SANDBOX_NAVIGATION_OCR_INTERVAL,
            )
            self._status("箱庭徒步导航传送阵菜单", f"点击魔法阵中心={center}")
            return True
        return False

    def _click_sandbox_navigation_destination_confirmation(
        self,
        frame: np.ndarray,
    ) -> bool:
        """Confirm the selected navigation destination when its OCR button appears."""

        try:
            boxes = self.vision.ocr_boxes(frame, "箱庭徒步导航传送阵确认")
        except Exception as exc:
            self._status("箱庭徒步导航传送阵确认 OCR错误", str(exc))
            return False
        for box in boxes:
            label = normalize_text(
                self.vision.simplify(str(getattr(box, "name", "")))
            )
            if "生成魔法阵" in label:
                continue
            if not (
                label == "确认"
                or label.startswith("确认")
                or label == "生成"
                or label.startswith("生成")
            ):
                continue
            center = self._ocr_box_center(box)
            if center is None:
                continue
            self.vision.click_client(
                center,
                frame.shape,
                after_sleep=SANDBOX_NAVIGATION_OCR_INTERVAL,
            )
            self._status("箱庭徒步导航传送阵确认", f"点击{label}中心={center}")
            return True
        return False

    def _walk_to_sandbox_teleport_interaction(self) -> NavigationResult:
        """Use the sandbox navigation map to walk back to a portal interaction prompt."""

        self._status("导航状态", "传送阵技能失败，转入导航/徒步回退")
        if not self._click_sandbox_navigation_map():
            return NavigationResult(
                False,
                ScreenState.SANDBOX,
                "未识别到左上导航地图入口，无法转入徒步回退",
            )

        end_at = monotonic() + SANDBOX_NAVIGATION_MAP_TIMEOUT
        teleport = None
        teleport_frame = None
        while monotonic() <= end_at:
            frame = self.vision.capture()
            page_confirmed = self._sandbox_navigation_page_has_keyword(frame)
            teleport = self._sandbox_navigation_teleport(frame) if page_confirmed else None
            if page_confirmed and teleport is not None:
                teleport_frame = frame
                break
            self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL)
        if teleport is None:
            self._close_confirmed_map_page(
                {MapPageMode.SANDBOX_LARGE_MAP},
                timeout=SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
            )
            return NavigationResult(
                False,
                ScreenState.SANDBOX_MAP,
                "未在已确认箱庭大地图同帧识别到唯一传送阵图标",
                map_page_mode=MapPageMode.SANDBOX_LARGE_MAP,
            )

        self.vision.click_client(
            teleport.center,
            teleport_frame.shape,
            after_sleep=SANDBOX_NAVIGATION_TELEPORT_SETTLE_SECONDS,
        )
        menu_end_at = monotonic() + SANDBOX_NAVIGATION_CONFIRM_TIMEOUT
        destination_confirmed = False
        while monotonic() <= menu_end_at:
            menu_frame = self.vision.capture()
            if self._click_sandbox_navigation_menu_teleport(menu_frame):
                self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL)
                continue
            if self._click_sandbox_navigation_destination_confirmation(menu_frame):
                destination_confirmed = True
                break
            self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL)
        if not destination_confirmed:
            frame = self.vision.capture()
            if self._sandbox_navigation_page_has_keyword(frame):
                self._close_confirmed_map_page(
                    {MapPageMode.SANDBOX_LARGE_MAP},
                    timeout=SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
                )
            return NavigationResult(
                False,
                ScreenState.UNKNOWN,
                "箱庭大地图选择传送阵后未确认目的地按钮，已停止等待交互",
            )

        self._status(
            "导航状态",
            "已选择导航地图传送阵，等待自动移动后重新识别交互按钮",
        )
        if not self._click_sandbox_teleport_interaction(
            timeout=SANDBOX_NAVIGATION_WALK_TIMEOUT,
        ):
            return NavigationResult(
                False,
                ScreenState.SANDBOX,
                "徒步回退后仍未识别到传送阵交互按钮",
            )
        return self._wait_for_sandbox_map_open(
            "徒步回退交互",
            expected_mode=MapPageMode.DIRECT_TELEPORT,
            detect_skill_failure=False,
        )

    def _click_sandbox_teleport_skill(
        self,
        timeout: float = SANDBOX_TELEPORT_SKILL_TIMEOUT,
    ) -> bool:
        """Click the sandbox's fifth teleport skill from a strict match center."""

        end_at = monotonic() + max(0.0, timeout)
        last = MatchResult(-1.0, (0, 0), (0, 0))
        while monotonic() <= end_at:
            frame = self.vision.capture()
            last = self.vision.match(frame, SANDBOX_TELEPORT_SKILL_TEMPLATE)
            passed = self.vision.passes(last, SANDBOX_TELEPORT_SKILL_TEMPLATE)
            self._status(
                "箱庭5号传送阵技能",
                (
                    f"{'pass' if passed else 'miss'}; center={last.center}, "
                    f"match={last.score:.3f}, pixel={last.pixel_score:.3f}, "
                    f"zncc={last.zncc_score:.3f}"
                ),
            )
            if passed:
                self.vision.click_client(
                    last.center,
                    frame.shape,
                    after_sleep=SANDBOX_MAP_SETTLE_SECONDS,
                )
                return True
            self.task.sleep(SANDBOX_TELEPORT_SKILL_POLL_INTERVAL)
        self._status(
            "箱庭5号传送阵技能",
            (
                "超时未通过严格识别；"
                f"last_match={last.score:.3f}, pixel={last.pixel_score:.3f}, "
                f"zncc={last.zncc_score:.3f}"
            ),
        )
        # A timeout is an explicit recognition failure.  Never turn the
        # calibrated slot center into a blind action click; the caller will
        # continue with the existing safe interaction/navigation fallback.
        return False

    def open_teleport_map_from_sandbox(self) -> NavigationResult:
        """Open the teleport map through interaction first, then skill fallback."""

        self._status("导航状态", "优先识别箱庭传送阵交互按钮")
        if self._click_sandbox_teleport_interaction():
            opened = self._wait_for_sandbox_map_open(
                "传送阵交互按钮",
                expected_mode=MapPageMode.DIRECT_TELEPORT,
                detect_skill_failure=False,
            )
            if opened.success:
                return opened
            return NavigationResult(
                False,
                opened.state,
                f"点击传送阵交互按钮后未确认传送阵地图：{opened.message}",
                map_page_mode=opened.map_page_mode,
            )

        self._status("导航状态", "未识别交互按钮，回退识别箱庭5号传送阵技能")
        if not self._click_sandbox_teleport_skill():
            return NavigationResult(
                False,
                ScreenState.SANDBOX,
                "未可靠识别箱庭5号传送阵技能，已停止打开传送阵地图",
            )

        opened = self._wait_for_sandbox_map_open(
            "箱庭5号传送阵技能",
            expected_mode=MapPageMode.GENERATE_TELEPORT,
            detect_skill_failure=True,
        )
        if opened.success:
            return opened
        if not self._sandbox_teleport_skill_failure_matches(opened.message):
            return opened

        fallback = self._walk_to_sandbox_teleport_interaction()
        if fallback.success:
            return NavigationResult(
                True,
                fallback.state,
                f"{opened.message}；{fallback.message}",
                map_page_mode=fallback.map_page_mode,
            )
        return NavigationResult(
            False,
            fallback.state,
            f"{opened.message}；导航/徒步回退失败：{fallback.message}",
            map_page_mode=fallback.map_page_mode,
        )

    def _teleport_generation_boxes(
        self,
        frame: np.ndarray,
    ) -> tuple[tuple[int, int] | None, frozenset[str], str]:
        boxes = self.vision.ocr_boxes(frame, "传送阵生成确认")
        generate_centers: list[tuple[int, int]] = []
        matched: set[str] = set()
        labels = []
        for box in boxes:
            label = normalize_text(self.vision.simplify(str(getattr(box, "name", ""))))
            if not label:
                continue
            labels.append(label)
            center = self._ocr_box_center(box)
            if "生成魔法阵" in label:
                matched.add("生成魔法阵")
            if "取消" in label:
                matched.add("取消")
            if label == "生成" or (label.startswith("生成") and "魔法阵" not in label):
                matched.add("生成")
                if center is not None:
                    generate_centers.append(center)
        return (
            generate_centers[0] if len(matched) == 3 and len(generate_centers) == 1 else None,
            frozenset(matched),
            "|".join(labels),
        )

    def _click_teleport_generation(
        self,
        teleport: MatchResult,
        frame_shape: tuple[int, ...],
        timeout: float = TELEPORT_GENERATION_OCR_TIMEOUT,
    ) -> bool:
        """Select one white map teleport and confirm its three-keyword dialog."""

        self.vision.click_client(
            teleport.center,
            frame_shape,
            after_sleep=AREA_MAP_CLICK_SETTLE_SECONDS,
        )
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.vision.capture()
            try:
                generate_center, matched_keywords, last_text = self._teleport_generation_boxes(
                    frame
                )
            except Exception as exc:
                self._status("传送阵生成弹窗 OCR错误", str(exc))
                return False
            self._status(
                "传送阵生成弹窗",
                f"matched={len(matched_keywords)}/3, text={last_text or '-'}",
            )
            if generate_center is not None:
                self.vision.click_client(
                    generate_center,
                    frame.shape,
                    after_sleep=TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
                )
                return True
            self.task.sleep(TELEPORT_GENERATION_OCR_INTERVAL)
        self._status(
            "传送阵生成弹窗",
            f"超时未同时命中三关键词，OCR={last_text or '-'}",
        )
        return False

    def _click_teleport_map_destination(
        self,
        teleport: MatchResult,
        frame_shape: tuple[int, ...],
        *,
        page_mode: MapPageMode,
    ) -> bool:
        """Click a teleport-map destination using the correct entry semantics."""

        if page_mode == MapPageMode.GENERATE_TELEPORT:
            return self._click_teleport_generation(teleport, frame_shape)
        if page_mode != MapPageMode.DIRECT_TELEPORT:
            self._status("传送阵地图传送阵", f"拒绝未知页面模式={page_mode.value}")
            return False
        self.vision.click_client(
            teleport.center,
            frame_shape,
            after_sleep=TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
        )
        self._status(
            "传送阵地图传送阵",
            f"交互入口直接点击中心={teleport.center}，等待传送完成",
        )
        return True

    @staticmethod
    def _screen_state_for_map_page_mode(mode: MapPageMode) -> ScreenState:
        if mode.is_teleport_map:
            return ScreenState.AREA_MAP
        if mode == MapPageMode.SANDBOX_LARGE_MAP:
            return ScreenState.SANDBOX_MAP
        return ScreenState.UNKNOWN

    def _close_confirmed_map_page(
        self,
        expected_modes: set[MapPageMode],
        *,
        card_number: int | None = None,
        timeout: float | None = 8.0,
    ) -> NavigationResult:
        """Close one strictly identified map page, then re-confirm the sandbox."""

        frame = self.vision.capture()
        detection = self._detect_map_page_mode(frame)
        if detection.mode not in expected_modes:
            expected = ",".join(sorted(mode.value for mode in expected_modes))
            return NavigationResult(
                False,
                self._screen_state_for_map_page_mode(detection.mode),
                (
                    "关闭前地图页面身份不符，未执行点击："
                    f"expected={expected}, observed={detection.mode.value}"
                ),
                map_page_mode=detection.mode,
            )

        back = self.vision.match(frame, AREA_MAP_BACK_TEMPLATE)
        if self.vision.passes(back, AREA_MAP_BACK_TEMPLATE):
            self._status(
                "地图页面返回按钮",
                (
                    f"mode={detection.mode.value}; center={back.center}; "
                    f"match={back.score:.3f}, pixel={back.pixel_score:.3f}, "
                    f"zncc={back.zncc_score:.3f}"
                ),
            )
            self.vision.click_client(
                back.center,
                frame.shape,
                after_sleep=SANDBOX_MAP_SETTLE_SECONDS,
            )
        else:
            if detection.mode == MapPageMode.SANDBOX_LARGE_MAP:
                reference = SANDBOX_LARGE_MAP_RETURN_REFERENCE_POINT
                relative = SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT
            else:
                reference = TELEPORT_MAP_RETURN_REFERENCE_POINT
                relative = TELEPORT_MAP_RETURN_RELATIVE_POINT
            self._status(
                "地图页面返回按钮",
                (
                    f"mode={detection.mode.value}; 模板未通过，仅在严格身份确认后使用"
                    f"标定点({reference[0]},{reference[1]})"
                ),
            )
            self.task.operate_click(
                *relative,
                after_sleep=SANDBOX_MAP_SETTLE_SECONDS,
            )

        if card_number is None:
            confirmed = self._wait_for_current_sandbox(timeout=float(timeout or 8.0))
        elif timeout is None:
            confirmed = self._wait_for_story_sandbox(int(card_number))
        else:
            confirmed = self._wait_for_story_sandbox(int(card_number), timeout=timeout)
        if confirmed.success:
            return NavigationResult(True, ScreenState.SANDBOX, "地图页面已关闭并稳定确认箱庭")
        return NavigationResult(
            False,
            confirmed.state,
            f"关闭地图页面后未确认箱庭：{confirmed.message}",
            map_page_mode=detection.mode,
        )

    def return_teleport_map_to_sandbox(self, card_number: int) -> NavigationResult:
        """Close a confirmed teleport page and re-confirm the requested story sandbox."""

        self._status("导航状态", "从传送阵地图返回卡带箱庭")
        return self._close_confirmed_map_page(
            {
                MapPageMode.DIRECT_TELEPORT,
                MapPageMode.GENERATE_TELEPORT,
            },
            card_number=int(card_number),
            timeout=None,
        )

    def ensure_area_map(self) -> NavigationResult:
        frame = self.vision.capture()
        detection = self._detect_map_page_mode(frame)
        if detection.mode.is_teleport_map:
            return NavigationResult(
                True,
                ScreenState.AREA_MAP,
                "当前传送阵地图已按视觉模式确认",
                map_page_mode=detection.mode,
            )
        state = self.classify(frame)
        if state != ScreenState.SANDBOX:
            return NavigationResult(False, state, "不在箱庭，无法打开传送地图")
        opened = self.open_teleport_map_from_sandbox()
        if not opened.success:
            return opened
        if opened.state == ScreenState.AREA_MAP and opened.map_page_mode.is_teleport_map:
            return opened
        return NavigationResult(False, opened.state, "打开后未得到严格传送地图视觉模式")

    def _optional_match(self, frame: np.ndarray, spec: TemplateSpec) -> MatchResult | None:
        result = self.vision.match(frame, spec)
        return result if self.vision.passes(result, spec) else None

    @staticmethod
    def _area_map_teleport_bright_neutral_ratio(
        frame: np.ndarray,
        result: MatchResult,
    ) -> float:
        left, top = result.position
        width, height = result.size
        right = left + width
        bottom = top + height
        if (
            width <= 0
            or height <= 0
            or left < 0
            or top < 0
            or right > frame.shape[1]
            or bottom > frame.shape[0]
        ):
            return 0.0
        crop = frame[top:bottom, left:right]
        if crop.ndim == 2:
            color = np.repeat(crop[:, :, None], 3, axis=2)
        else:
            color = crop[:, :, :3]
        pixels = color.astype(np.int16)
        channel_min = np.min(pixels, axis=2)
        channel_spread = np.max(pixels, axis=2) - channel_min
        center_x = (width - 1) / 2
        center_y = (height - 1) / 2
        radius = min(width, height) * AREA_MAP_TELEPORT_BRIGHT_RADIUS_RATIO
        y, x = np.ogrid[:height, :width]
        circle = (x - center_x) ** 2 + (y - center_y) ** 2 < radius**2
        if not np.any(circle):
            return 0.0
        bright_neutral = (channel_min >= AREA_MAP_TELEPORT_BRIGHT_MINIMUM_GRAY) & (
            channel_spread <= AREA_MAP_TELEPORT_BRIGHT_MAXIMUM_SPREAD
        )
        return float(np.mean(bright_neutral[circle]))

    def _map_teleports(
        self,
        frame: np.ndarray,
        templates: tuple[TemplateSpec, ...],
        *,
        status_name: str,
    ) -> tuple[MatchResult, ...]:
        height, width = frame.shape[:2]
        cluster_radius = max(
            6,
            round(AREA_MAP_TELEPORT_CLUSTER_RADIUS * min(width / 1920, height / 1080)),
        )
        candidates: list[MatchResult] = []
        for spec in templates:
            for result in self.vision.match_all(
                frame,
                spec,
                minimum_score=self.vision.threshold_for(spec),
                peak_radius=cluster_radius,
            ):
                bright_ratio = self._area_map_teleport_bright_neutral_ratio(
                    frame,
                    result,
                )
                accepted = (
                    self.vision.passes(result, spec)
                    and bright_ratio >= AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO
                )
                self._status(
                    status_name,
                    (
                        f"center={result.center}, match={result.score:.3f}, "
                        f"pixel={result.pixel_score:.3f}, "
                        f"zncc={result.zncc_score:.3f}, "
                        f"bright={bright_ratio:.3f}, "
                        f"accepted={accepted}"
                    ),
                )
                if accepted:
                    candidates.append(result)
        independent: list[MatchResult] = []
        for candidate in sorted(candidates, key=lambda value: value.score, reverse=True):
            if any(
                (candidate.center[0] - kept.center[0]) ** 2
                + (candidate.center[1] - kept.center[1]) ** 2
                <= cluster_radius**2
                for kept in independent
            ):
                continue
            independent.append(candidate)
        return tuple(sorted(independent, key=lambda value: value.center))

    def _teleport_map_teleports(self, frame: np.ndarray) -> tuple[MatchResult, ...]:
        return self._map_teleports(
            frame,
            TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
            status_name="传送阵地图传送阵候选",
        )

    @staticmethod
    def _select_map_teleport(teleports: tuple[MatchResult, ...]) -> MatchResult | None:
        """Choose the strongest already-validated teleport when several are visible."""
        if not teleports:
            return None
        return max(
            teleports,
            key=lambda value: (value.score, value.pixel_score, value.zncc_score),
        )

    @staticmethod
    def _target_keys_in_text(card: CardSpec, normalized_text: str) -> tuple[str, ...]:
        matches: list[tuple[int, str]] = []
        for target in card.targets:
            title_lengths = [
                len(normalized_title)
                for title in target.titles
                if (normalized_title := normalize_text(title))
                and normalized_title in normalized_text
            ]
            if title_lengths:
                matches.append((max(title_lengths), target.key))
        if not matches:
            return ()
        longest = max(length for length, _key in matches)
        return tuple(sorted({key for length, key in matches if length == longest}))

    def _area_map_context(self, frame: np.ndarray, card: CardSpec) -> AreaMapContext:
        detection = self._detect_map_page_mode(frame)
        if not detection.mode.is_teleport_map:
            context = AreaMapContext(
                frame_shape=frame.shape,
                raw_text="",
                normalized_text="",
                map_page_mode=detection.mode,
                candidate_target_keys=(),
                resolved_target_key=None,
                left_arrow=None,
                right_arrow=None,
                teleports=(),
                overlap_arrow=None,
                back_button=None,
                confirmation_text=detection.header_text,
            )
            self._status(
                "区域地图",
                f"拒绝非传送页面模式={detection.mode.value}",
            )
            return context
        raw_text = self.vision.simplify(
            self.vision.ocr_text(
                frame,
                "传送阵地图名",
                relative_roi=TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
            )
        )
        normalized_text = normalize_text(raw_text)
        target_keys = self._target_keys_in_text(card, normalized_text)
        context = AreaMapContext(
            frame_shape=frame.shape,
            raw_text=raw_text,
            normalized_text=normalized_text,
            map_page_mode=detection.mode,
            candidate_target_keys=target_keys,
            resolved_target_key=target_keys[0] if len(target_keys) == 1 else None,
            left_arrow=self._optional_match(frame, TELEPORT_MAP_FORWARD_TEMPLATE),
            right_arrow=self._optional_match(frame, TELEPORT_MAP_BACKWARD_TEMPLATE),
            teleports=self._teleport_map_teleports(frame),
            overlap_arrow=self._optional_match(frame, OVERLAP_ARROW_TEMPLATE),
            back_button=self._optional_match(frame, AREA_MAP_BACK_TEMPLATE),
            confirmation_text=detection.header_text,
        )
        self._status(
            "区域地图",
            (
                f"target={context.resolved_target_key or '-'}, "
                f"candidates={','.join(context.candidate_target_keys) or '-'}, "
                f"left={context.left_arrow is not None}, "
                f"right={context.right_arrow is not None}, "
                f"teleports={len(context.teleports)}, "
                f"mode={context.map_page_mode.value}, "
                f"title_ocr={context.raw_text or '-'}, "
                f"confirmation_ocr={context.confirmation_text or '-'}"
            ),
        )
        return context

    def _capture_area_map_context(self, card: CardSpec) -> AreaMapContext:
        return self._area_map_context(self.vision.capture(), card)

    def _wait_for_collection_teleport_map(
        self,
        card: CardSpec,
        timeout: float = TELEPORT_MAP_OPEN_TIMEOUT,
    ) -> AreaMapContext | NavigationResult:
        end_at = monotonic() + max(0.0, timeout)
        last = None
        while monotonic() <= end_at:
            last = self._capture_area_map_context(card)
            if last.is_area_map:
                if len(last.candidate_target_keys) > 1:
                    return NavigationResult(
                        False,
                        ScreenState.AREA_MAP,
                        "传送阵地图标题同时命中多个目标",
                    )
                if last.resolved_target_key is not None:
                    return last
            self.task.sleep(AREA_MAP_CHANGE_INTERVAL)
        return NavigationResult(
            False,
            ScreenState.UNKNOWN,
            (
                "未在限定时间内通过移动魔法阵与地图名 OCR 确认传送阵地图："
                f"last_title={last.raw_text if last is not None else '-'}"
            ),
        )

    def _reset_collection_teleport_map_to_main(
        self,
        card: CardSpec,
        context: AreaMapContext,
    ) -> AreaMapContext | NavigationResult:
        current = context
        clicks = 0
        while current.left_arrow is not None:
            if clicks >= TELEPORT_MAP_FIRST_PAGE_LIMIT:
                return NavigationResult(
                    False,
                    ScreenState.AREA_MAP,
                    (f"向前点击{TELEPORT_MAP_FIRST_PAGE_LIMIT}次后仍未确认到达安全区第一页"),
                )
            changed = self._move_area_map(card, current, "left")
            if changed is None:
                return NavigationResult(
                    False,
                    ScreenState.AREA_MAP,
                    "向前翻页后未确认地图名发生变化",
                )
            current = changed
            clicks += 1
        expected = CollectionMapRole.MAIN_AREA.value
        if current.resolved_target_key != expected:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    "已到达传送阵地图最前页，但 OCR 未确认安全区："
                    f"expected={card.targets[0].title}, actual={current.raw_text or '-'}"
                ),
            )
        return current

    def _click_collection_destination(
        self,
        card: CardSpec,
        target: CollectionMapTarget,
        context: AreaMapContext,
    ) -> NavigationResult:
        if not context.map_page_mode.is_teleport_map:
            return NavigationResult(
                False,
                self._screen_state_for_map_page_mode(context.map_page_mode),
                f"页面模式{context.map_page_mode.value}不允许传送点击",
                map_page_mode=context.map_page_mode,
            )
        if context.resolved_target_key != target.key:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (f"传送前地图名不符：expected={target.title}, actual={context.raw_text or '-'}"),
            )
        teleport = self._select_map_teleport(context.teleports)
        if teleport is None:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    f"{target.title}未识别到传送阵地图传送阵，"
                    "无法安全传送"
                ),
            )
        self._status(
            "传送阵地图传送阵点击中心",
            (
                f"target={target.title}, candidates={len(context.teleports)}, "
                f"selected=center={teleport.center}, "
                f"match={teleport.score:.3f}, pixel={teleport.pixel_score:.3f}, "
                f"zncc={teleport.zncc_score:.3f}"
            ),
        )
        if not self._click_teleport_map_destination(
            teleport,
            context.frame_shape,
            page_mode=context.map_page_mode,
        ):
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    f"传送到{target.title}前未可靠确认生成魔法阵弹窗"
                    if context.map_page_mode == MapPageMode.GENERATE_TELEPORT
                    else f"传送到{target.title}前未完成传送阵地图传送"
                ),
            )
        arrived = self._wait_for_story_sandbox(card.number)
        if not arrived.success:
            return NavigationResult(
                False,
                arrived.state,
                f"传送到{target.title}后未确认剧情箱庭：{arrived.message}",
            )
        return self._confirm_collection_arrival(card, target)

    def prepare_collection_main_area(self, card_id: str) -> NavigationResult:
        """Normalize a newly entered story card to its safe/main area."""

        card = CARD_BY_ID.get(card_id)
        if card is None or not card.collectable:
            return NavigationResult(False, ScreenState.UNKNOWN, f"非跑图剧情卡带：{card_id}")
        opened = self.open_teleport_map_from_sandbox()
        if not opened.success:
            return opened
        context = self._wait_for_collection_teleport_map(card)
        if isinstance(context, NavigationResult):
            return context
        if context.map_page_mode != opened.map_page_mode:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    "打开入口与后续传送页模式不一致："
                    f"opened={opened.map_page_mode.value}, current={context.map_page_mode.value}"
                ),
                map_page_mode=context.map_page_mode,
            )
        main_target = card.targets[0]
        if context.resolved_target_key == main_target.key:
            return self.return_teleport_map_to_sandbox(card.number)
        first = self._reset_collection_teleport_map_to_main(card, context)
        if isinstance(first, NavigationResult):
            return first
        return self._click_collection_destination(
            card,
            main_target,
            first,
        )

    def advance_collection_map(
        self,
        card_id: str,
        current_target: CollectionMapTarget,
        next_target: CollectionMapTarget,
    ) -> NavigationResult:
        """Open the current teleport, move backward exactly one page, and travel."""

        card = CARD_BY_ID.get(card_id)
        if card is None or not card.collectable:
            return NavigationResult(False, ScreenState.UNKNOWN, f"非跑图剧情卡带：{card_id}")
        opened = self.open_teleport_map_from_sandbox()
        if not opened.success:
            return NavigationResult(
                False,
                opened.state,
                f"{current_target.title}无法打开传送阵地图：{opened.message}",
                map_page_mode=opened.map_page_mode,
            )
        context = self._wait_for_collection_teleport_map(card)
        if isinstance(context, NavigationResult):
            return context
        if context.map_page_mode != opened.map_page_mode:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    "打开入口与后续传送页模式不一致："
                    f"opened={opened.map_page_mode.value}, current={context.map_page_mode.value}"
                ),
                map_page_mode=context.map_page_mode,
            )
        if context.resolved_target_key != current_target.key:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    f"打开传送阵地图后初始位置不符：expected={current_target.title}, "
                    f"actual={context.raw_text or '-'}"
                ),
            )
        if context.right_arrow is None:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                f"{current_target.title}未识别到向后翻页按钮",
            )
        changed = self._move_area_map(card, context, "right")
        if changed is None:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                f"从{current_target.title}向后翻页后未确认页面变化",
            )
        if changed.resolved_target_key != next_target.key:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    f"向后翻一页未到达目标地图：expected={next_target.title}, "
                    f"actual={changed.raw_text or '-'}"
                ),
            )
        return self._click_collection_destination(
            card,
            next_target,
            changed,
        )

    def _wait_for_area_map_change(
        self,
        card: CardSpec,
        previous: AreaMapContext,
    ) -> AreaMapContext | None:
        end_at = monotonic() + AREA_MAP_CHANGE_TIMEOUT
        while monotonic() <= end_at:
            current = self._capture_area_map_context(card)
            if current.is_area_map and (
                current.normalized_text != previous.normalized_text
                or current.candidate_target_keys != previous.candidate_target_keys
            ) and current.map_page_mode == previous.map_page_mode:
                return current
            self.task.sleep(AREA_MAP_CHANGE_INTERVAL)
        return None

    def _move_area_map(
        self,
        card: CardSpec,
        context: AreaMapContext,
        direction: str,
    ) -> AreaMapContext | None:
        arrow = context.right_arrow if direction == "right" else context.left_arrow
        if arrow is None:
            return None
        self.vision.click_client(
            arrow.center,
            context.frame_shape,
            after_sleep=AREA_MAP_CLICK_SETTLE_SECONDS,
        )
        return self._wait_for_area_map_change(card, context)

    def _scan_area_map_direction(
        self,
        card: CardSpec,
        target: CollectionMapTarget,
        context: AreaMapContext,
        direction: str,
    ) -> tuple[AreaMapContext, bool, bool]:
        current = context
        moved = False
        visited = {current.normalized_text}
        for _step in range(AREA_MAP_SCAN_LIMIT):
            if current.resolved_target_key == target.key:
                return current, moved, False
            arrow = current.right_arrow if direction == "right" else current.left_arrow
            if arrow is None:
                return current, moved, False
            changed = self._move_area_map(card, current, direction)
            if changed is None or changed.normalized_text in visited:
                return current, moved, True
            current = changed
            moved = True
            visited.add(current.normalized_text)
            if len(current.candidate_target_keys) > 1:
                return current, moved, True
        return current, moved, True

    def _locate_collection_target(
        self,
        card: CardSpec,
        target: CollectionMapTarget,
        initial: AreaMapContext,
    ) -> tuple[AreaMapContext | None, bool, str]:
        if not initial.is_area_map:
            return None, False, "未在同一帧确认移动魔法阵区域地图"
        if len(initial.candidate_target_keys) > 1:
            return None, False, "当前地图标题同时命中多个目标"
        if initial.resolved_target_key == target.key:
            return initial, False, ""

        current, moved_right, failed = self._scan_area_map_direction(
            card,
            target,
            initial,
            "right",
        )
        if current.resolved_target_key == target.key:
            return current, moved_right, ""
        if failed:
            return None, moved_right, "向右翻页后未确认页面变化或出现标题歧义"

        current, moved_left, failed = self._scan_area_map_direction(
            card,
            target,
            current,
            "left",
        )
        if current.resolved_target_key == target.key:
            return current, moved_right or moved_left, ""
        if failed:
            return None, moved_right or moved_left, "向左复位时未确认页面变化或出现标题歧义"

        current, moved_again, failed = self._scan_area_map_direction(
            card,
            target,
            current,
            "right",
        )
        if current.resolved_target_key == target.key:
            return current, moved_right or moved_left or moved_again, ""
        if failed:
            return None, True, "从最左页扫描时未确认页面变化或出现标题歧义"
        return (
            None,
            moved_right or moved_left or moved_again,
            (f"到达区域图边界仍未找到{target.title}"),
        )

    def _close_area_map(self, context: AreaMapContext) -> NavigationResult:
        if not context.map_page_mode.is_teleport_map:
            return NavigationResult(
                False,
                self._screen_state_for_map_page_mode(context.map_page_mode),
                f"非传送页面模式{context.map_page_mode.value}不允许关闭区域地图",
                map_page_mode=context.map_page_mode,
            )
        return self._close_confirmed_map_page(
            {context.map_page_mode},
            timeout=8.0,
        )

    def _confirm_collection_arrival(
        self,
        card: CardSpec,
        target: CollectionMapTarget,
    ) -> NavigationResult:
        area = self.ensure_area_map()
        if not area.success:
            return NavigationResult(False, area.state, f"到达后无法复核区域地图：{area.message}")
        context = self._capture_area_map_context(card)
        if context.map_page_mode != area.map_page_mode:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    "到达复核时页面模式发生变化："
                    f"opened={area.map_page_mode.value}, current={context.map_page_mode.value}"
                ),
                map_page_mode=context.map_page_mode,
            )
        if context.resolved_target_key != target.key:
            actual = context.resolved_target_key or context.raw_text or "未知"
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                f"到达后地图不符：目标={target.title}，实际={actual}",
            )
        closed = self._close_area_map(context)
        if not closed.success:
            return closed
        return NavigationResult(
            True,
            ScreenState.SANDBOX,
            f"{card.card_id}/{target.key}/{target.title}",
        )

    def _click_collection_teleport(
        self,
        card: CardSpec,
        target: CollectionMapTarget,
        context: AreaMapContext,
    ) -> NavigationResult:
        if not context.map_page_mode.is_teleport_map:
            return NavigationResult(
                False,
                self._screen_state_for_map_page_mode(context.map_page_mode),
                f"页面模式{context.map_page_mode.value}不允许传送点击",
                map_page_mode=context.map_page_mode,
            )
        if not context.teleports and context.overlap_arrow is not None:
            self.vision.click_client(
                context.overlap_arrow.center,
                context.frame_shape,
                after_sleep=AREA_MAP_CLICK_SETTLE_SECONDS,
            )
            expanded = self._capture_area_map_context(card)
            if (
                expanded.resolved_target_key != target.key
                or expanded.map_page_mode != context.map_page_mode
            ):
                return NavigationResult(
                    False,
                    ScreenState.AREA_MAP,
                    "展开传送阵后目标地图标题发生变化",
                )
            context = expanded
        teleport = self._select_map_teleport(context.teleports)
        if teleport is None:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                f"{target.title}未识别到传送阵，无法安全传送",
            )

        if not self._click_teleport_map_destination(
            teleport,
            context.frame_shape,
            page_mode=context.map_page_mode,
        ):
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    f"传送到{target.title}前未可靠确认生成魔法阵弹窗"
                    if context.map_page_mode == MapPageMode.GENERATE_TELEPORT
                    else f"传送到{target.title}前未完成传送阵地图传送"
                ),
            )
        arrived = self._wait_for_story_sandbox(
            card.number,
            timeout=8.0 + self._loading_timeout(),
        )
        if not arrived.success:
            return NavigationResult(
                False,
                arrived.state,
                f"传送到{target.title}后未确认剧情箱庭：{arrived.message}",
            )
        return self._confirm_collection_arrival(card, target)

    def enter_collection_map(
        self,
        card_id: str,
        target: CollectionMapTarget,
    ) -> NavigationResult:
        card = CARD_BY_ID.get(card_id)
        if card is None or target.key not in {value.key for value in card.targets}:
            return NavigationResult(
                False,
                ScreenState.UNKNOWN,
                f"未知采集目标：{card_id}/{target.key}",
            )
        area = self.ensure_area_map()
        if not area.success:
            return area
        initial = self._capture_area_map_context(card)
        if initial.map_page_mode != area.map_page_mode:
            return NavigationResult(
                False,
                ScreenState.AREA_MAP,
                (
                    "区域地图确认前后页面模式不一致："
                    f"opened={area.map_page_mode.value}, current={initial.map_page_mode.value}"
                ),
                map_page_mode=initial.map_page_mode,
            )
        located, moved, reason = self._locate_collection_target(card, target, initial)
        if located is None:
            return NavigationResult(False, ScreenState.AREA_MAP, reason)
        if not moved:
            closed = self._close_area_map(located)
            if not closed.success:
                return closed
            return NavigationResult(
                True,
                ScreenState.SANDBOX,
                f"{card.card_id}/{target.key}/{target.title}",
            )
        return self._click_collection_teleport(
            card,
            target,
            located,
        )

