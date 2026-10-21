from __future__ import annotations

import re
from time import monotonic

import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks.BaseBD2Task import (
    RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS,
    CartridgeSpecialPageResult,
)
from src.tasks.map_trade.action_icons import (
    ACTION_SLOT_RELATIVE_ROIS,
    SANDBOX_TELEPORT_ICON,
    ActionIconDetection,
    ActionIconDetector,
    ActionIconState,
)
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    CIRCLE_EXIT_WALKS,
    EVENT_WALK_EDGES,
    RESTART_NAV_ENTRIES,
    RESUMING_WALK_CARD_IDS,
    RESUMING_WALK_TIMEOUT,
    TOWN_NAV_ENTRIES,
    WALK_LABEL_EDGES,
    WALKED_EVENT_CARD_IDS,
    CardSpec,
    CollectionMapRole,
    CollectionMapTarget,
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.navigator_constants import (
    AREA_MAP_BACK_TEMPLATE,
    AREA_MAP_CHANGE_INTERVAL,
    AREA_MAP_CHANGE_TIMEOUT,
    AREA_MAP_CLICK_SETTLE_SECONDS,
    AREA_MAP_EXIT_ICON_OFFSETS,
    AREA_MAP_EXIT_LABEL_RELATIVE_ROI,
    AREA_MAP_TELEPORT_BRIGHT_MAXIMUM_SPREAD,
    AREA_MAP_TELEPORT_BRIGHT_MINIMUM_GRAY,
    AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
    AREA_MAP_TELEPORT_BRIGHT_RADIUS_RATIO,
    AREA_MAP_TELEPORT_CLUSTER_RADIUS,
    AUTO_MOVE_CANCEL_ATTEMPTS,
    AUTO_MOVE_CANCEL_REFERENCE_POINT,
    AUTO_MOVE_IDLE_READS,
    FIELD_EXTRA_KEYCAP_TEMPLATES,
    FIELD_MAP_CLOSE_TIMEOUT,
    HAND_TEMPLATE,
    HUNTING_GROUND_NAV_ENTRY,
    MAP_PAGE_MODE_STABLE_HITS,
    MERCHANT_NAV_CONFIRM_OCR_ROI,
    MERCHANT_NAV_GUIDE_TEMPLATE,
    MERCHANT_NAV_GUIDE_TIMEOUT,
    MERCHANT_NAV_MENU_OCR_INTERVAL,
    MERCHANT_NAV_MENU_OCR_ROI,
    MERCHANT_NAV_MENU_OCR_TIMEOUT,
    MERCHANT_NAV_OUTCOME_TIMEOUT,
    MERCHANT_NAV_TOAST_OCR_ROI,
    MERCHANT_NAV_UNREACHABLE_KEYWORD,
    MERCHANT_TRAVEL_DIALOG_KEYWORDS,
    MERCHANT_TRAVEL_DIALOG_OCR_ROI,
    MINIMAP_CENTER_REFERENCE,
    MINIMAP_LARGE_TEMPLATE,
    MINIMAP_SHRINK_ATTEMPTS,
    MINIMAP_SMALL_TEMPLATE,
    OVERLAP_ARROW_TEMPLATE,
    SANDBOX_INTERACTION_PROBE_INTERVAL,
    SANDBOX_INTERACTION_PROBE_TIMEOUT,
    SANDBOX_LARGE_MAP_RETURN_REFERENCE_POINT,
    SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT,
    SANDBOX_MAP_SETTLE_SECONDS,
    SANDBOX_MAP_TELEPORT_MIN_PIXEL,
    SANDBOX_MAP_TELEPORT_PIXEL_MARGIN,
    SANDBOX_MAP_TELEPORT_TEMPLATE,
    SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
    SANDBOX_NAVIGATION_MAP_TIMEOUT,
    SANDBOX_NAVIGATION_OCR_INTERVAL,
    SANDBOX_NAVIGATION_OPEN_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_OPEN_TEMPLATES,
    SANDBOX_NAVIGATION_OPEN_TIMEOUT,
    SANDBOX_NAVIGATION_TELEPORT_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_WALK_TIMEOUT,
    SANDBOX_SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO,
    SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
    SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_STATE_TEMPLATES,
    SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO,
    SANDBOX_TELEPORT_SKILL_FAILURE_GROUPS,
    SANDBOX_TELEPORT_SKILL_POLL_INTERVAL,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
    SANDBOX_TELEPORT_SKILL_TIMEOUT,
    TELEPORT_GENERATION_OCR_INTERVAL,
    TELEPORT_GENERATION_OCR_TIMEOUT,
    TELEPORT_INTERACTION_CLICK_DELAY,
    TELEPORT_LOADING_DARK_P95,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_HEADER_OCR_RELATIVE_ROI,
    TELEPORT_MAP_OPEN_TIMEOUT,
    TELEPORT_MAP_RETURN_REFERENCE_POINT,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_SEEK_LIMIT,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    TOWN_NPC_NAV_ENTRY,
    TRADE_CARD_SANDBOX_HITS,
    WALK_CAUGHT_KEYWORDS,
    WALK_CLICK_SETTLE_SECONDS,
    WALK_EXIT_ATTEMPTS,
    WALK_LABEL_READ_INTERVAL,
    WALK_LABEL_READ_TIMEOUT,
    WALK_MAP_OPEN_TIMEOUT,
    AreaMapContext,
)
from src.tasks.map_trade.vision import normalize_text
from src.utils.calibration import FHD_1080

FIELD_NAV_MENU_REFERENCE_POINT = (288, 112)


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

    @staticmethod
    def _sandbox_evidence_rank(candidate) -> tuple[float, ...]:
        result = getattr(candidate, "result", candidate)
        values = (
            float(getattr(result, "score", -1.0)),
            float(getattr(result, "pixel_score", -1.0)),
            float(getattr(result, "zncc_score", -1.0)),
            float(getattr(result, "gradient_zncc_score", -1.0)),
            float(getattr(result, "edge_score", -1.0)),
        )
        finite = tuple(value for value in values if np.isfinite(value) and value > -1.0)
        composite = sum(finite) / len(finite) if finite else -1.0
        return (composite, *values)

    def _sandbox_has_evidence_matcher(self) -> bool:
        return callable(getattr(self.vision, "match_evidence_all", None)) and callable(
            getattr(self.vision, "match_slot_evidence", None)
        )

    def _sandbox_frame_geometry(self, frame: np.ndarray):
        assess = getattr(self.vision, "assess_frame", None)
        if not callable(assess):
            return None
        try:
            return assess(
                frame,
                required_relative_rois=tuple(ACTION_SLOT_RELATIVE_ROIS.values()),
                purpose="箱庭技能栏",
            )
        except (AttributeError, TypeError, ValueError) as exc:
            self._status("箱庭画面几何", f"检查异常：{exc}")
            return False

    def _sandbox_evidence_spec_match(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        *,
        purpose: str,
        geometry=None,
    ) -> tuple[MatchResult, bool]:
        """Return the strongest retained candidate and apply the old spec gate."""

        matcher = getattr(self.vision, "match_evidence_all", None)
        candidates = ()
        if callable(matcher):
            try:
                candidates = tuple(
                    matcher(
                        frame,
                        spec,
                        minimum_score=0.30,
                        peak_radius=4,
                        max_results=6,
                        geometry=geometry,
                        purpose=purpose,
                    )
                )
            except (AttributeError, TypeError, ValueError):
                candidates = ()
        if candidates:
            selected = max(candidates, key=self._sandbox_evidence_rank)
            result = getattr(selected, "result", selected)
        else:
            try:
                result = self.vision.match(frame, spec)
            except (AttributeError, TypeError, ValueError):
                result = MatchResult(-1.0, (0, 0), (0, 0))
        try:
            passed = bool(self.vision.passes(result, spec))
        except (AttributeError, TypeError, ValueError):
            passed = False
        return result, passed

    def _sandbox_skill_group_evidence(self, frame: np.ndarray, geometry):
        skill_matches = tuple(
            (
                spec.name,
                *self._sandbox_evidence_spec_match(
                    frame,
                    spec,
                    purpose="箱庭技能组确认",
                    geometry=geometry,
                ),
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
                f"命中={skill_state_hits}/4；颜色状态={slot_states}；"
                f"状态={'技能组' + str(skill_group) if skill_group else '未知/冲突'}；"
                f"{self._format_sandbox_matches(skill_matches)}"
            ),
        )
        return skill_matches, skill_state_hits, skill_group

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

    def _wait_for_story_sandbox(
        self,
        target_number: int,
        timeout: float | None = None,
        interval: float = 0.5,
    ) -> NavigationResult:
        return self._wait_for_field_hud(
            timeout=self._loading_timeout() if timeout is None else float(timeout),
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
        """Confirm the current story field on consecutive frames (any skill layout)."""

        return self._wait_for_field_hud(
            timeout=timeout,
            interval=interval,
            success_message="已稳定确认剧情卡带箱庭",
            failure_message="未稳定确认当前剧情卡带箱庭",
        )

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
        except (TaskDisabledException, FinishedException):
            raise
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
            # 同帧同时命中“移动魔法阵”标题和直传图标，可直接确认页面。
            sure_direct = (
                detection.mode == MapPageMode.DIRECT_TELEPORT
                and "移动魔法阵" in normalize_text(getattr(detection, "header_text", ""))
                and any(
                    "交互直传页图标=pass" in item for item in getattr(detection, "evidence", ())
                )
            )
            if stable_hits >= MAP_PAGE_MODE_STABLE_HITS or (stable_hits and sure_direct):
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
            # 首次命中后缩短复核间隔，减少换图等待。
            self.task.sleep(0.15 if stable_hits else 0.5)
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
        # 蒙版模板在平坦地图背景上也可能高分命中，须用像素相似度筛选真实传送阵。
        passed = sorted(
            (
                result
                for result in candidates
                if self.vision.passes(result, SANDBOX_MAP_TELEPORT_TEMPLATE)
            ),
            key=lambda result: result.pixel_score,
            reverse=True,
        )
        best = passed[0].pixel_score if passed else -1.0
        second = passed[1].pixel_score if len(passed) > 1 else -1.0
        self._status(
            "箱庭徒步导航传送阵",
            f"candidates={len(passed)}, pixel best={best:.3f} second={second:.3f}",
        )
        if not passed:
            return None
        if len(passed) == 1:
            return passed[0]
        if (
            best >= SANDBOX_MAP_TELEPORT_MIN_PIXEL
            and best - second >= SANDBOX_MAP_TELEPORT_PIXEL_MARGIN
        ):
            return passed[0]
        if second >= SANDBOX_MAP_TELEPORT_MIN_PIXEL:
            # 同图可能有多个真实传送阵，均通过像素门限时可选最高分候选。
            return passed[0]
        return None

    def _click_sandbox_navigation_menu_teleport(self, frame: np.ndarray) -> bool:
        """Select a stacked navigation-map teleport submenu by OCR center."""

        try:
            boxes = self.vision.ocr_boxes(frame, "箱庭徒步导航传送阵菜单")
        except (TaskDisabledException, FinishedException):
            raise
        except Exception as exc:
            self._status("箱庭徒步导航传送阵菜单 OCR错误", str(exc))
            return False
        for box in boxes:
            label = normalize_text(
                self.vision.simplify(str(getattr(box, "name", "")))
            )
            # “自动移动中：魔法阵”是行走状态提示，不能作为菜单项再次点击。
            if "魔法阵" not in label or "自动移动" in label:
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
        except (TaskDisabledException, FinishedException):
            raise
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
        state = self.wait_state(
            {ScreenState.SANDBOX, ScreenState.SANDBOX_MAP},
            SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
        )
        if state not in {ScreenState.SANDBOX, ScreenState.SANDBOX_MAP}:
            return NavigationResult(
                False, state, "徒步回退前未确认箱庭或箱庭大地图，未执行点击",
            )
        # 优先点击小地图打开区域地图，避免邻近导航菜单的图标造成误识别。
        # 已打开的大地图直接复用，不能再次点击被地图覆盖的小地图位置。
        if (
            state == ScreenState.SANDBOX
            and self._open_field_map() is None
            and not self._click_sandbox_navigation_map()
        ):
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
            walk_text = self._walk_text(menu_frame)
            if walk_text is None:
                self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL)
                continue
            if self._walk_caught(walk_text):
                # 巡逻驱逐可能先于自动移动横幅读出，须立即处理。
                self._stop_caught_walk()
                return NavigationResult(
                    False,
                    ScreenState.SANDBOX,
                    "走向传送阵时被巡逻发现并驱逐，已停止自动移动",
                )
            if any(word in walk_text for word in AUTO_MOVE_QUESTION_KEYWORDS):
                if self._click_sandbox_navigation_destination_confirmation(menu_frame):
                    destination_confirmed = True
                    break
                self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL)
                continue
            if "移动中" in walk_text:
                # One click on the map icon already started the walk: no
                # menu and no confirm button follow.
                destination_confirmed = True
                break
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
        for look in range(3):
            if not self._sandbox_navigation_page_has_keyword(self.vision.capture()):
                break
            if look < 2:
                self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL * 2)
        else:
            closed = self._close_confirmed_map_page(
                {MapPageMode.SANDBOX_LARGE_MAP}, timeout=SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
            )
            if not closed.success:
                return closed
        # 交互按钮可能在行走结束前出现；先等移动停止，避免传送后仍沿旧路线行走。
        walk_state = self._wait_auto_move_finished(SANDBOX_NAVIGATION_WALK_TIMEOUT)
        if walk_state == "caught":
            # 被驱逐后停止当前路线，让调用方改经狩猎场或艾琳，避免反复触发巡逻。
            self._stop_caught_walk()
            return NavigationResult(
                False,
                ScreenState.SANDBOX,
                "走向传送阵时被巡逻发现并驱逐，已停止自动移动",
            )
        if walk_state != "done":
            self._cancel_walk()
            return NavigationResult(False, ScreenState.UNKNOWN, "走向传送阵的自动移动超时")
        if not self._click_sandbox_teleport_interaction(timeout=self._loading_timeout()):
            # A walk still going (resumed after an unseen catch) would fight
            # the caller's next step (the ≡ menu).
            self._cancel_walk()
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

    @staticmethod
    def _sandbox_same_action_identity(
        previous: ActionIconDetection,
        current: ActionIconDetection,
    ) -> bool:
        if previous.state is not current.state:
            return False
        first = previous.match
        second = current.match
        if first.size[0] <= 0 or second.size[0] <= 0:
            return False
        scale = max(0.2, min(float(first.scale), float(second.scale)))
        tolerance = max(3, round(6.0 * scale))
        return (
            abs(first.center[0] - second.center[0]) <= tolerance
            and abs(first.center[1] - second.center[1]) <= tolerance
            and abs(first.size[0] - second.size[0]) <= max(3, tolerance)
            and abs(first.size[1] - second.size[1]) <= max(3, tolerance)
        )

    def _click_sandbox_teleport_skill_with_evidence(
        self,
        timeout: float = SANDBOX_TELEPORT_SKILL_TIMEOUT,
    ) -> bool:
        """Require two consistent local evidence observations before clicking."""

        end_at = monotonic() + max(0.0, timeout)
        detector = ActionIconDetector(self.vision)
        previous = None
        stable_hits = 0
        last = ActionIconDetection(
            ActionIconState.ABSENT,
            MatchResult(-1.0, (0, 0), (0, 0)),
            reason="未执行识别",
            semantic_state="absent",
        )
        while monotonic() <= end_at:
            frame = self.vision.capture()
            geometry = self._sandbox_frame_geometry(frame)
            if geometry is False or (geometry is not None and not geometry.accepted):
                reason = (
                    "画面几何检查异常"
                    if geometry is False
                    else "画面几何拒绝：" + "|".join(geometry.rejection_reasons)
                )
                self._status("箱庭5号传送阵技能", reason)
                return False
            _skill_matches, _skill_hits, skill_group = self._sandbox_skill_group_evidence(
                frame,
                geometry,
            )
            if skill_group != 1:
                current = ActionIconDetection(
                    ActionIconState.ABSENT,
                    MatchResult(-1.0, (0, 0), (0, 0)),
                    reason=(
                        "错误技能组：" + str(skill_group)
                        if skill_group is not None
                        else "技能组无法唯一确认"
                    ),
                    semantic_state="wrong_group",
                )
            else:
                current = detector.detect(
                    frame,
                    SANDBOX_TELEPORT_ICON,
                    geometry=geometry,
                )
            last = current
            if current.clickable and (
                previous is not None
                and self._sandbox_same_action_identity(previous, current)
            ):
                stable_hits += 1
            elif current.clickable:
                stable_hits = 1
            else:
                stable_hits = 0
            self._status(
                "箱庭5号传送阵技能",
                (
                    f"{current.state.value}/{current.semantic_state or '-'}; "
                    f"center={current.match.center}; "
                    f"m={current.match.score:.3f},p={current.match.pixel_score:.3f},"
                    f"z={current.match.zncc_score:.3f},"
                    f"g={current.match.gradient_zncc_score:.3f},"
                    f"e={current.match.edge_score:.3f}; "
                    f"scale={current.match.scale:.3f}; "
                    f"margin={current.candidate_margin:.3f}; "
                    f"stable={stable_hits}/2; reason={current.reason or '-'}"
                ),
            )
            if current.clickable and stable_hits >= 2:
                self.vision.click_client(
                    current.match.center,
                    frame.shape,
                    after_sleep=SANDBOX_MAP_SETTLE_SECONDS,
                )
                return True
            previous = current if current.clickable else None
            self.task.sleep(SANDBOX_TELEPORT_SKILL_POLL_INTERVAL)
        self._status(
            "箱庭5号传送阵技能",
            (
                "局部多证据/稳定确认超时；"
                f"last={last.state.value}/{last.semantic_state or '-'}; "
                f"m={last.match.score:.3f},p={last.match.pixel_score:.3f},"
                f"z={last.match.zncc_score:.3f},"
                f"g={last.match.gradient_zncc_score:.3f},"
                f"e={last.match.edge_score:.3f}; reason={last.reason or '-'}"
            ),
        )
        return False

    def _click_sandbox_teleport_skill(
        self,
        timeout: float = SANDBOX_TELEPORT_SKILL_TIMEOUT,
    ) -> bool:
        if self._sandbox_has_evidence_matcher():
            return self._click_sandbox_teleport_skill_with_evidence(timeout)
        return self._click_sandbox_teleport_skill_legacy(timeout)

    def _click_sandbox_teleport_skill_legacy(
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
            except (TaskDisabledException, FinishedException):
                raise
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
        self.vision.click_client(teleport.center, frame_shape, after_sleep=0.0)
        self._wait_for_loading_screen(
            TELEPORT_MAP_TRAVEL_SETTLE_SECONDS
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
            round(
                AREA_MAP_TELEPORT_CLUSTER_RADIUS
                * min(width / FHD_1080.width, height / FHD_1080.height)
            ),
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
            if any(
                (excluded := normalize_text(title)) and excluded in normalized_text
                for title in target.excludes
            ):
                continue
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
        allow_unresolved: bool = False,
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
                if (
                    allow_unresolved
                    and bool(last.normalized_text)
                    and (last.left_arrow is not None or last.right_arrow is not None)
                ):
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
            end_at = monotonic() + 2.0
            while monotonic() <= end_at:
                self.task.sleep(AREA_MAP_CHANGE_INTERVAL)
                latest = self._capture_area_map_context(card)
                if (
                    latest.map_page_mode == context.map_page_mode
                    and latest.resolved_target_key == target.key
                ):
                    context = latest
                    teleport = self._select_map_teleport(context.teleports)
                    if teleport is not None:
                        break
        if teleport is None:
            overlap = context.overlap_arrow
            if overlap is not None:
                self.vision.click_client(
                    overlap.center, context.frame_shape, after_sleep=AREA_MAP_CLICK_SETTLE_SECONDS,
                )
                latest = self._capture_area_map_context(card)
                if (
                    latest.map_page_mode != context.map_page_mode
                    or latest.resolved_target_key != target.key
                ):
                    return NavigationResult(False, ScreenState.UNKNOWN, "展开传送阵后地图身份不符")
                context = latest
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

    def prepare_collection_main_area(
        self, card_id: str, *, via_hunting_ground: bool = False
    ) -> NavigationResult:
        """将卡带位置归一到采集路线起点。

        ``via_hunting_ground`` 启用失败恢复路径，经导航菜单或可用传送阵返回起点。
        """

        card = CARD_BY_ID.get(card_id)
        if card is None or not card.collectable:
            return NavigationResult(False, ScreenState.UNKNOWN, f"非跑图剧情卡带：{card_id}")
        if card.card_id in WALKED_EVENT_CARD_IDS:
            if (
                self._travel_via_nav_menu(RESTART_NAV_ENTRIES[card.card_id])
                and self.current_collection_target(card) == card.targets[0].key
            ):
                return NavigationResult(True, ScreenState.SANDBOX, f"已到达{card.targets[0].title}")
            return NavigationResult(False, self.classify(), "活动卡带起点导航未确认")
        if not self.ensure_small_minimap():
            return NavigationResult(False, ScreenState.SANDBOX, "小地图放大状态未能切回")
        if via_hunting_ground and (npc := RESTART_NAV_ENTRIES.get(card.card_id)):
            # 配有专用重启入口的卡带优先经 NPC 回到路线起点。
            self._travel_via_nav_menu(npc)
            if self.current_collection_target(card) == card.targets[0].key:
                return NavigationResult(
                    True, ScreenState.SANDBOX, f"经{npc}到达{card.targets[0].title}"
                )
            self._status("导航状态", f"经{npc}未到{card.targets[0].title}，改用原来的回城方式")
        if via_hunting_ground:
            entry = None
            if self._click_sandbox_teleport_interaction():
                # Already on a teleport circle: its map reaches the town
                # directly, no need for the hunting ground.
                opened = self._wait_for_sandbox_map_open(
                    "传送阵交互按钮",
                    expected_mode=MapPageMode.DIRECT_TELEPORT,
                    detect_skill_failure=False,
                )
            elif (entry := self._travel_to_hunting_ground()) == HUNTING_GROUND_NAV_ENTRY:
                opened = self.open_teleport_map_from_sandbox()
            elif entry is not None:
                # 导航到城镇 NPC 后可能已在路线起点，先确认位置。
                if self.current_collection_target(card) == card.targets[0].key:
                    return NavigationResult(
                        True, ScreenState.SANDBOX, f"经{entry}到达{card.targets[0].title}"
                    )
                opened = self._open_teleport_map_anywhere(card)
            else:
                # 菜单入口不可用时，通过当前地图的传送阵恢复。
                opened = self._open_teleport_map_anywhere(card)
        else:
            opened = self._open_teleport_map_anywhere(card)
        if not opened.success:
            return opened
        context = self._wait_for_collection_teleport_map(card, allow_unresolved=True)
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
        first = self._seek_collection_page(card, context, main_target)
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
        """按路线配置徒步、经 NPC 或按地图名传送至下一个采集目标。"""

        card = CARD_BY_ID.get(card_id)
        if card is None or not card.collectable:
            return NavigationResult(False, ScreenState.UNKNOWN, f"非跑图剧情卡带：{card_id}")
        edge = (card_id, current_target.key, next_target.key)
        if edge in WALK_LABEL_EDGES or edge in EVENT_WALK_EDGES:
            if card.card_id in WALKED_EVENT_CARD_IDS and next_target == card.targets[0]:
                npc = TOWN_NAV_ENTRIES[card.card_id]
                if (
                    self._travel_via_nav_menu(npc)
                    and self.current_collection_target(card) == next_target.key
                ):
                    return NavigationResult(
                        True, ScreenState.SANDBOX, f"经{npc}到达{next_target.title}",
                    )
            return self._walk_to_collection_map(card, current_target, next_target)
        town_npc = TOWN_NAV_ENTRIES.get(card.card_id)
        if town_npc and next_target.key == card.targets[0].key:
            # 返城优先走 NPC 导航以避开巡逻；未到达时再尝试传送阵地图。
            if (
                self._travel_via_nav_menu(town_npc)
                and self.current_collection_target(card) == next_target.key
            ):
                return NavigationResult(
                    True, ScreenState.SANDBOX, f"经{town_npc}到达{next_target.title}"
                )
            self._status("导航状态", f"经{town_npc}未到{next_target.title}，改用传送阵地图")
        opened = self._open_teleport_map_anywhere(card)
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
            self._status(
                "区域地图",
                f"传送阵地图停在{context.raw_text or '-'}"
                f"（预期{current_target.title}），按名称查找",
            )
        changed = self._seek_collection_page(card, context, next_target)
        if isinstance(changed, NavigationResult):
            return changed
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
            if (
                current.is_area_map
                and bool(current.normalized_text)
                and (
                    current.normalized_text != previous.normalized_text
                    or current.candidate_target_keys != previous.candidate_target_keys
                )
                and current.map_page_mode == previous.map_page_mode
            ):
                return current
            self.task.sleep(AREA_MAP_CHANGE_INTERVAL)
        return None

    def _move_area_map(
        self,
        card: CardSpec,
        context: AreaMapContext,
        direction: str,
        *,
        force: bool = False,
    ) -> AreaMapContext | None:
        if not context.map_page_mode.is_teleport_map:
            return None
        if not force and self._area_map_page_end(context, direction):
            state, fresh = self._area_map_page_state(card, context)
            if state == "moved":
                return fresh
            if state == "unknown" or self._area_map_page_end(fresh, direction):
                return None
            context = fresh
        for _attempt in range(2):
            arrow = context.right_arrow if direction == "right" else context.left_arrow
            if arrow is not None:
                center = arrow.center
            else:
                # 已确认传送页，白色箭头漏识别时使用本地标定位置，并核验实际翻页。
                x, y = TELEPORT_PAGE_ARROW_REFERENCE[direction]
                height, width = context.frame_shape[:2]
                center = (round(x * width / 1920), round(y * height / 1080))
            self.vision.click_client(
                center, context.frame_shape, after_sleep=AREA_MAP_CLICK_SETTLE_SECONDS,
            )
            changed = self._wait_for_area_map_change(card, context)
            if changed is not None:
                return changed
            state, fresh = self._area_map_page_state(card, context)
            if state == "moved":
                return fresh
            if state == "unknown" or (
                not force and self._area_map_page_end(fresh, direction)
            ):
                return None
            context = fresh
        return None

    def _area_map_page_state(self, card, previous) -> tuple[str, AreaMapContext]:
        current = self._capture_area_map_context(card)
        if (
            not current.is_area_map or not current.normalized_text
            or current.map_page_mode != previous.map_page_mode
        ):
            return "unknown", current
        if (
            current.normalized_text != previous.normalized_text
            or current.candidate_target_keys != previous.candidate_target_keys
        ):
            return "moved", current
        return "same", current

    @staticmethod
    def _area_map_page_end(context: AreaMapContext, direction: str) -> bool:
        if direction == "right":
            return context.right_arrow is None and context.left_arrow is not None
        return context.left_arrow is None and context.right_arrow is not None

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
        # 普通地图可直接核验实际落点，避免再次开启传送技能。
        if self.ensure_small_minimap():
            header = self._open_field_map()
            if header is not None:
                if not self._close_field_map():
                    return NavigationResult(False, ScreenState.SANDBOX_MAP, "到达后区域地图未关闭")
                if tuple(self._target_keys_in_text(card, header)) == (target.key,):
                    return NavigationResult(
                        True, ScreenState.SANDBOX, f"{card.card_id}/{target.key}/{target.title}",
                    )
                return NavigationResult(
                    False, ScreenState.SANDBOX,
                    f"到达后地图不符：目标={target.title}，实际={header}",
                )
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

    def wait_for_collection_field(self, frame=None) -> np.ndarray:
        """Recover encounters before accepting a stationary collection field."""
        frame = self.vision.capture() if frame is None else frame
        end_at = monotonic() + self._loading_timeout()
        battle_seen = False
        waiting = False
        idle = 0
        while monotonic() <= end_at:
            if self._black_frame(frame):
                idle = 0
            else:
                if self._field_battle_visible(frame):
                    if not battle_seen:
                        end_at = monotonic() + FIELD_BATTLE_TIMEOUT
                        battle_seen = True
                    idle = 0
                    return self._retreat_from_field_battle(frame, end_at=end_at, stop_walk=True)
                elif self.classify(frame) == ScreenState.SANDBOX:
                    if self._walk_state(frame, field_confirmed=True) == "idle":
                        idle += 1
                        if not waiting or idle >= AUTO_MOVE_IDLE_READS:
                            return frame
                    else:
                        idle = 0
                else:
                    idle = 0
                    # The first observed frame may already be the victory screen.
                    if self._leave_walk_battle_result(frame):
                        if not battle_seen:
                            end_at = monotonic() + FIELD_BATTLE_TIMEOUT
                            battle_seen = True
            waiting = True
            self.task.sleep(0.5)
            frame = self.vision.capture()
        message = "遭遇战恢复超时" if battle_seen else "采集前箱庭确认超时"
        raise RuntimeError(f"{message}，未确认返回静止箱庭，停止采集技能操作")

    def _field_battle_visible(self, frame: np.ndarray) -> bool:
        text = self.vision.ocr_text(
            frame, "采集遭遇战回合", relative_roi=FIELD_BATTLE_TURN_RELATIVE_ROI,
        )
        return re.search(r"\bTURN[\s.]*\d+\b", text, re.IGNORECASE) is not None

    def _field_retreat_action(self, boxes):
        """Resolve escape controls by their dialog/menu row, excluding headings."""
        text = normalize_text(" ".join(str(box.name) for box in boxes))
        controls = []
        for box in boxes:
            center = self._ocr_box_center(box)
            height = float(getattr(box, "height", 0) or 0)
            if center is not None and height > 0:
                controls.append((normalize_text(str(box.name)), center, height))
        escapes = [control for control in controls if control[0] == "逃跑"]
        cancels = [control for control in controls if control[0] == "取消"]

        def same_row(left, right):
            return (
                right[1][0] > left[1][0]
                and abs(right[1][1] - left[1][1]) <= max(left[2], right[2]) * 0.6
            )

        question = "放弃战斗逃跑" in text
        if question or cancels:
            candidates = [
                escape[1] for cancel in cancels for escape in escapes
                if question and same_row(cancel, escape)
            ]
            stage = "confirm"
        else:
            companions = [control for control in controls if control[0] in {"重新挑战", "点击继续"}]
            candidates = [
                escape[1] for escape in escapes
                if len(escapes) == 1 and any(control[0] == "暂停" for control in controls)
                and any(same_row(escape, companion) for companion in companions)
            ]
            stage = "escape"
        return (stage, candidates[0]) if len(candidates) == 1 else (None, None)

    def _retreat_from_field_battle(
        self, frame: np.ndarray, *, end_at: float, stop_walk: bool = False,
    ) -> np.ndarray:
        """Follow pause, escape and its confirmation using fresh observations."""
        end_at = min(end_at, monotonic() + FIELD_RETREAT_TIMEOUT)
        attempts = {"pause": 0, "escape": 0, "confirm": 0}
        confirmed = False
        field_hits = 0
        while monotonic() <= end_at:
            if not self._black_frame(frame):
                if confirmed and self.classify(frame) == ScreenState.SANDBOX:
                    walk = self._walk_state(frame, field_confirmed=True) if stop_walk else "idle"
                    field_hits = field_hits + 1 if walk == "idle" else 0
                    if field_hits >= AUTO_MOVE_IDLE_READS:
                        return frame
                    if walk == "moving":
                        self._cancel_walk()
                    self.task.sleep(0.15)
                    frame = self.vision.capture()
                    continue
                boxes = self.vision.ocr_boxes(frame, "采集战斗逃跑")
                exits = [
                    point for box in boxes
                    if normalize_text(str(box.name)) == "逃跑"
                    and (point := self._ocr_box_center(box)) is not None
                ]
                stage, point = self._field_retreat_action(boxes)
                if stage == "escape" and confirmed:
                    stage, point = None, None
                if (
                    stage is None and not exits and not confirmed
                    and self._field_battle_visible(frame)
                ):
                    stage = "pause"
                    point = (
                        round(frame.shape[1] * FIELD_BATTLE_PAUSE_POINT[0]),
                        round(frame.shape[0] * FIELD_BATTLE_PAUSE_POINT[1]),
                    )
                if stage is not None:
                    field_hits = 0
                    if attempts[stage] >= FIELD_RETREAT_ATTEMPTS:
                        raise RuntimeError(f"遭遇战退出失败：{stage}点击后未确认切页，停止采集")
                    attempts[stage] += 1
                    self._status("导航状态", {
                        "pause": "遭遇战：打开暂停菜单",
                        "escape": "遭遇战：点击逃跑",
                        "confirm": "遭遇战：确认逃跑",
                    }[stage])
                    self.vision.click_client(point, frame.shape, after_sleep=1.0)
                    if stage == "confirm":
                        confirmed = True
                elif not exits and self.classify(frame) == ScreenState.SANDBOX:
                    walk = self._walk_state(frame, field_confirmed=True) if stop_walk else "idle"
                    field_hits = field_hits + 1 if walk == "idle" else 0
                    if field_hits >= AUTO_MOVE_IDLE_READS:
                        return frame
                    if walk == "moving":
                        self._cancel_walk()
                else:
                    field_hits = 0
                    self._leave_walk_battle_result(frame)
            else:
                field_hits = 0
            self.task.sleep(0.5)
            frame = self.vision.capture()
        raise RuntimeError("遭遇战退出超时，未确认返回箱庭，停止采集")

    def _wait_for_field_hud(
        self,
        *,
        timeout: float,
        interval: float,
        success_message: str,
        failure_message: str,
        handle_intermediate: bool = False,
    ) -> NavigationResult:
        """通过连续箱庭 HUD 画面确认入场，不依赖玩家的技能分组。

        技能栏由采集步骤单独识别；本步骤只负责场景、加载和移动状态。
        """
        stall = max(0.0, timeout)
        end_at = monotonic() + stall
        # 自动移动期间延长场景等待，但不得超过行走总上限。
        walk_cap = end_at + SANDBOX_NAVIGATION_WALK_TIMEOUT
        hits = 0
        last_state = ScreenState.UNKNOWN
        while monotonic() <= end_at:
            frame = self.vision.capture()
            # 黑屏加载帧直接分类，省去主页和箱庭识别开销。
            last_state = (
                ScreenState.LOADING
                if frame is not None and self._black_frame(frame)
                else self.classify(frame)
            )
            self._status("导航状态", last_state.value)
            if last_state == ScreenState.SANDBOX:
                hits += 1
                if hits >= TRADE_CARD_SANDBOX_HITS:
                    return NavigationResult(True, last_state, success_message)
            else:
                hits = 0
                if (
                    last_state != ScreenState.LOADING
                    and frame is not None
                    and self._auto_moving(frame)
                ):
                    end_at = min(max(end_at, monotonic() + stall), walk_cap)
                if handle_intermediate and last_state != ScreenState.LOADING:
                    if self._handle_story_card_intermediate(frame):
                        continue
                    remaining = max(0.0, end_at - monotonic())
                    if remaining > 0:
                        reward = self.task._handle_recent_cartridge_special_pages(
                            timeout=min(RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS, remaining / 4),
                            allow_pvp_pages=False,
                        )
                        if reward is CartridgeSpecialPageResult.BLOCKED:
                            return NavigationResult(False, last_state, "卡带奖励弹窗关闭未确认")
                        if reward is CartridgeSpecialPageResult.HANDLED:
                            continue
            if interval > 0:
                self.task.sleep(interval)
        return NavigationResult(False, last_state, failure_message)

    def _walk_text(self, frame: np.ndarray) -> str | None:
        """Read the walk banner; None denotes an OCR failure rather than an empty banner."""
        try:
            text = self.vision.ocr_text(
                frame, "箱庭自动移动", relative_roi=AUTO_MOVE_TEXT_RELATIVE_ROI,
                raise_on_error=True,
            )
        except (TaskDisabledException, FinishedException):
            raise
        except Exception:
            return None
        return normalize_text(self.vision.simplify(str(text)))

    def _walk_caught(self, text: str) -> bool:
        caught = any(word in text for word in WALK_CAUGHT_KEYWORDS)
        if caught:
            self._status("导航状态", "自动移动被巡逻发现，角色被驱逐回入口")
        return caught

    def _walk_flags(self, frame: np.ndarray) -> tuple[bool, bool]:
        """(walking, caught): "自动移动中" shows / a field patrol's words
        (WALK_CAUGHT_KEYWORDS) show.  Both at once right after an expulsion:
        the toast is still up while the game walks on."""
        text = self._walk_text(frame)
        if text is None:
            return False, False
        return "移动中" in text, self._walk_caught(text)

    def _walk_state(self, frame: np.ndarray, *, field_confirmed: bool = False) -> str:
        """Return caught/moving/idle; unreadable or non-field observations stay unknown."""
        text = self._walk_text(frame)
        if text is None:
            return "unknown"
        if self._walk_caught(text):
            return "caught"
        if "移动中" in text:
            return "moving"
        if field_confirmed or self.classify(frame) == ScreenState.SANDBOX:
            return "idle"
        return "unknown"

    def _auto_moving(self, frame: np.ndarray) -> bool:
        """"自动移动中" shows: the game is walking the character somewhere."""
        return self._walk_flags(frame)[0]

    def _wait_auto_move_finished(self, timeout: float) -> str:
        """Wait while "自动移动中" shows: "done" once it is gone, "caught" as
        soon as a patrol stops the walk, "timeout" when it never ends."""
        end_at = monotonic() + max(0.0, timeout)
        idle = 0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            state = self._walk_state(frame)
            if state == "caught":
                return state
            if state == "idle":
                idle += 1
                if idle >= AUTO_MOVE_IDLE_READS:
                    return "done"
            else:
                idle = 0
                if state == "unknown":
                    if self._field_battle_visible(frame):
                        self._retreat_from_field_battle(frame, end_at=end_at)
                    else:
                        self._leave_walk_battle_result(frame)
                self._status("导航状态", "自动移动中，等待走到传送阵")
            self.task.sleep(0.5)
        return "timeout"

    def _wait_resuming_walk(self, timeout: float) -> bool:
        """等待可自动续走的路线结束，期间不发送额外点击。

        被驱逐后游戏会重新加载并继续移动；横幅、加载和巡逻提示均消失后再确认停止。
        """

        end_at = monotonic() + max(0.0, timeout)
        idle = 0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            if self._black_frame(frame):
                idle = 0
                self._status("导航状态", "加载中，等待自动移动继续")
            else:
                walk_state = self._walk_state(frame)
                if walk_state in {"moving", "caught"}:
                    idle = 0
                    if walk_state == "caught":
                        self._status("导航状态", "被巡逻发现，等游戏自己继续走")
                elif walk_state == "idle":
                    idle += 1
                    if idle >= AUTO_MOVE_IDLE_READS:
                        return True
                else:
                    # A loading screen with art (not black), a dialog...
                    idle = 0
                    if self._field_battle_visible(frame):
                        self._retreat_from_field_battle(frame, end_at=end_at)
                    else:
                        self._leave_walk_battle_result(frame)
            self.task.sleep(0.5)
        self._status("导航状态", f"自动移动{timeout:.0f}秒仍未结束，停止本次徒步")
        return False

    def _leave_walk_battle_result(self, frame: np.ndarray) -> bool:
        text = normalize_text(self.vision.ocr_text(frame, "徒步战斗结果"))
        if not any(word in text for word in ("victory", "胜利", "战斗胜利")):
            return False
        boxes = self.vision.ocr_boxes(
            frame, "徒步战斗离开", relative_roi=(0.25, 0.55, 1.0, 1.0),
        )
        exits = [box for box in boxes if normalize_text(str(box.name)) == "离开"]
        if len(exits) != 1 or (point := self._ocr_box_center(exits[0])) is None:
            return False
        self.vision.click_client(point, frame.shape, after_sleep=1.0)
        return True

    def _cancel_walk(self) -> bool:
        """Press the ✕ under "自动移动中" while the banner shows; True when a
        walk was stopped."""

        stopped = False
        for _attempt in range(AUTO_MOVE_CANCEL_ATTEMPTS):
            frame = self.vision.capture()
            if not self._walk_flags(frame)[0]:
                break
            self._status("导航状态", "取消自动移动")
            height, width = frame.shape[:2]
            x, y = AUTO_MOVE_CANCEL_REFERENCE_POINT
            self.vision.click_client(
                (round(x * width / 1920), round(y * height / 1080)),
                frame.shape,
                after_sleep=0.8,
            )
            stopped = True
        return stopped

    def _stop_caught_walk(self) -> None:
        """取消被驱逐后恢复的自动移动，避免沿同一路线反复遭遇巡逻。

        等待箱庭稳定且巡逻提示消失后，才允许调用方选择其他路线。
        """

        end_at = monotonic() + self._loading_timeout()
        calm = 0
        while monotonic() <= end_at:
            frame = self.vision.capture()
            walk_state = self._walk_state(frame)
            if walk_state == "moving":
                self._cancel_walk()
                calm = 0
                continue
            if walk_state == "idle":
                calm += 1
                if calm >= AUTO_MOVE_IDLE_READS:
                    return
            else:
                calm = 0
            self.task.sleep(0.5)

    @staticmethod
    def _black_frame(frame) -> bool:
        try:
            return float(np.percentile(frame[..., :3], 95)) < TELEPORT_LOADING_DARK_P95
        except (AttributeError, TypeError, IndexError, ValueError):
            return False

    def _wait_for_loading_screen(self, timeout: float) -> bool:
        """限时等待传送黑屏，避免把传送开始前的原地图当成到达画面。"""
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() < end_at:
            try:
                frame = self.vision.capture()
            except (TaskDisabledException, FinishedException):
                raise
            except Exception:
                return False
            if self._black_frame(frame):
                return True
            self.task.sleep(0.1)
        return False

    def _seek_collection_page(
        self,
        card: CardSpec,
        context: AreaMapContext,
        target: CollectionMapTarget,
    ) -> AreaMapContext | NavigationResult:
        """根据地图标题查找目标页，在限定次数内向两侧扫描。

        起始页可能是狩猎场或路线外地图；已知当前位置时按目标顺序决定优先方向。
        """

        if context.resolved_target_key == target.key:
            return context
        keys = [item.key for item in card.targets]
        if context.resolved_target_key in keys:
            ahead = keys.index(target.key) > keys.index(context.resolved_target_key)
            directions = ("right", "left") if ahead else ("left", "right")
        elif target.key == keys[0]:
            # 位置未知而目标是路线起点时，先向左查找，未命中再反向扫描。
            directions = ("left", "right")
        else:
            directions = ("right", "left")
        start = context
        for direction in directions:
            current = start
            for _ in range(TELEPORT_MAP_SEEK_LIMIT):
                changed = self._move_area_map(card, current, direction)
                if changed is None:
                    break  # no arrow or no change: this end of the list
                current = changed
                if current.resolved_target_key == target.key:
                    return current
            start = current
        # 单侧箭头漏识别可能被当作尽头；最后沿优先方向按标定点限次复核。
        for _ in range(TELEPORT_MAP_SEEK_LIMIT):
            changed = self._move_area_map(card, start, directions[0], force=True)
            if changed is None:
                break
            start = changed
            if start.resolved_target_key == target.key:
                return start
        return NavigationResult(
            False,
            ScreenState.AREA_MAP,
            f"传送阵地图左右翻页都未找到{target.title}：last={start.raw_text or '-'}",
        )

    def current_collection_target(self, card: CardSpec) -> str | None:
        """通过区域地图标题解析当前采集目标，无须站在传送阵旁。

        关闭地图并唯一匹配目标后才返回其键，否则返回 None。
        """
        if not self.ensure_small_minimap():
            return None
        header = self._open_field_map()
        if header is None:
            return None
        if not self._close_field_map():
            return None
        keys = self._target_keys_in_text(card, header)
        self._status("区域地图", f"当前位置 {header} -> {keys[0] if len(keys) == 1 else '-'}")
        return keys[0] if len(keys) == 1 else None

    def _open_teleport_map_anywhere(self, card: CardSpec | None = None) -> NavigationResult:
        """依次尝试原地开图、徒步到传送阵和导航菜单转移。"""
        opened = self.open_teleport_map_from_sandbox()
        if opened.success:
            return opened
        neighbour = self._walk_to_circle_neighbour(card)
        if neighbour is not None:
            return neighbour
        # 原地入口不可用时，先尝试徒步到当前地图的传送阵。
        self._status("导航状态", "不在传送阵旁，点地图上的传送门走过去")
        walked = self._walk_to_sandbox_teleport_interaction()
        if walked.success:
            return walked
        self._status("导航状态", "地图传送门不可用，前往狩猎场的传送阵")
        entry = self._travel_to_hunting_ground()
        if entry is None:
            return NavigationResult(
                False, walked.state,
                f"{opened.message}；徒步前往传送阵失败：{walked.message}；"
                "导航菜单也未能前往狩猎场或艾琳",
            )
        if entry == HUNTING_GROUND_NAV_ENTRY:
            return self.open_teleport_map_from_sandbox()
        # 艾琳 stands in the town: its circle, walking there if needed.
        if self._click_sandbox_teleport_interaction():
            return self._wait_for_sandbox_map_open(
                "传送阵交互按钮",
                expected_mode=MapPageMode.DIRECT_TELEPORT,
                detect_skill_failure=False,
            )
        walked = self._walk_to_sandbox_teleport_interaction()
        if walked.success:
            return walked
        return self._walk_to_circle_neighbour(card) or walked

    def _walk_to_circle_neighbour(self, card: CardSpec | None) -> NavigationResult | None:
        """从已确认的无传送阵地图走到相邻地图，再打开传送阵。"""
        edge = CIRCLE_EXIT_WALKS.get(card.card_id) if card is not None else None
        if edge is None:
            return None
        origin, destination = (
            next(target for target in card.targets if target.title == title) for title in edge
        )
        if self.current_collection_target(card) != origin.key:
            return None
        moved = self._walk_to_collection_map(card, origin, destination)
        if not moved.success:
            return moved
        return self.open_teleport_map_from_sandbox()

    def _travel_to_hunting_ground(self) -> str | None:
        """优先导航到狩猎场，其次艾琳；返回实际到达的入口，失败时返回 None。"""
        return self._travel_via_nav_menu(HUNTING_GROUND_NAV_ENTRY, TOWN_NPC_NAV_ENTRY)

    def _travel_via_nav_menu(self, *entries: str) -> str | None:
        """The minimap's ≡ menu -> the first of ``entries`` it lists (狩猎场,
        or a town NPC such as 艾琳) -> 确认, then the field once the trip (and
        any walk after it) ends.  The entry travelled to, or None."""

        wanted = "、".join(entries)
        if not self.vision.click_template(
            MERCHANT_NAV_GUIDE_TEMPLATE, timeout=MERCHANT_NAV_GUIDE_TIMEOUT, after_sleep=0.8
        ):
            frame = self.vision.capture()
            if not all(
                self.vision.passes(self.vision.match(frame, spec), spec)
                for spec in FIELD_EXTRA_KEYCAP_TEMPLATES
            ) and self.classify(frame) != ScreenState.SANDBOX:
                self.task.log_warning(f"跑图：未识别到小地图导航按钮，无法前往{wanted}。")
                return None
            self.task.operate_click(
                FIELD_NAV_MENU_REFERENCE_POINT[0] / FHD_1080.width,
                FIELD_NAV_MENU_REFERENCE_POINT[1] / FHD_1080.height,
                after_sleep=0.8,
            )
        choice = self._nav_menu_choice(entries)
        if choice is None:
            self.task.log_warning(f"跑图：导航菜单里没有{wanted}。")
            return None
        entry, point, shape = choice
        self.vision.click_client(point, shape, after_sleep=0.8)
        if self._confirm_travel() in {"stuck", "unreachable"}:
            return None
        arrived = self._wait_for_field_hud(
            timeout=self._loading_timeout(),
            interval=0.5,
            success_message=entry,
            failure_message=f"前往{entry}后未确认箱庭",
        ).success
        if not arrived:
            return None
        # An NPC entry walks on to the NPC after the loading screen.
        walk_state = self._wait_auto_move_finished(SANDBOX_NAVIGATION_WALK_TIMEOUT)
        if walk_state == "caught":
            self._stop_caught_walk()
        if walk_state != "done":
            return None
        return entry

    def _nav_menu_choice(
        self, entries: tuple[str, ...]
    ) -> tuple[str, tuple[int, int], tuple[int, ...]] | None:
        """The first of ``entries`` (in their order) the open ≡ menu lists,
        with its click point and the frame's shape.  Read again once an
        entry shows, so a menu still sliding in cannot hide 狩猎场."""

        end_at = monotonic() + MERCHANT_NAV_MENU_OCR_TIMEOUT
        seen = False
        while True:
            frame = self.vision.capture()
            listed: dict[str, tuple[int, int]] = {}
            texts = []
            for box in self.vision.ocr_boxes(frame, "导航菜单", MERCHANT_NAV_MENU_OCR_ROI):
                text = normalize_text(self.vision.simplify(str(getattr(box, "name", ""))))
                center = self._ocr_box_center(box)
                if not text or center is None:
                    continue
                texts.append(text)
                for entry in entries:
                    if entry in text:
                        listed.setdefault(entry, center)
            self._status("导航菜单", " ".join(texts) or "-")
            timed_out = monotonic() >= end_at
            if listed and (seen or timed_out):
                entry = next(item for item in entries if item in listed)
                return entry, listed[entry], frame.shape
            if timed_out:
                return None
            seen = bool(listed)
            self.task.sleep(MERCHANT_NAV_MENU_OCR_INTERVAL)

    def _field_map_header(self, frame=None) -> str:
        return normalize_text(
            self.vision.simplify(
                self.vision.ocr_text(
                    self.vision.capture() if frame is None else frame,
                    "区域地图标题",
                    relative_roi=TELEPORT_MAP_HEADER_OCR_RELATIVE_ROI,
                )
            )
        )

    def _open_field_map(self) -> str | None:
        """Open the field's area map (as M does); return its header text."""
        self.task.operate_click(
            MINIMAP_CENTER_REFERENCE[0] / 1920,
            MINIMAP_CENTER_REFERENCE[1] / 1080,
            after_sleep=1.0,
        )
        end_at = monotonic() + WALK_MAP_OPEN_TIMEOUT
        while True:
            header = self._field_map_header()
            if "战斗" in header or "安全" in header:
                return header
            if monotonic() >= end_at:
                return None
            self.task.sleep(0.4)

    def _close_field_map(self) -> bool:
        """Confirm the field after closing; retry once only on repeated map evidence."""
        self.task.operate_click(*SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT, after_sleep=1.0)
        end_at = monotonic() + FIELD_MAP_CLOSE_TIMEOUT
        open_hits = 0
        reclicked = False
        while True:
            frame = self.vision.capture()
            header = self._field_map_header(frame)
            is_map_open = "战斗" in header or "安全" in header
            if not is_map_open and self.classify(frame) == ScreenState.SANDBOX:
                return True
            open_hits = open_hits + 1 if is_map_open else 0
            if monotonic() >= end_at:
                self.task.log_warning(f"跑商：区域地图关闭后未确认箱庭：{header or '识别未知'}。")
                return False
            if not reclicked and open_hits >= 2:
                reclicked = True
                open_hits = 0
                self._status("区域地图", "关闭后标题仍在，补点一次返回")
                self.task.operate_click(*SANDBOX_LARGE_MAP_RETURN_RELATIVE_POINT, after_sleep=0.5)
                continue
            self.task.sleep(0.4)

    def _walk_to_collection_map(
        self,
        card: CardSpec,
        current: CollectionMapTarget,
        target: CollectionMapTarget,
    ) -> NavigationResult:
        """Walk through an area-map exit and confirm the destination by its title."""
        if not self.ensure_small_minimap():
            return NavigationResult(False, ScreenState.SANDBOX, "小地图放大状态未能切回")
        current_title = normalize_text(current.title)
        edge = (card.card_id, current.key, target.key)
        header = self._open_field_map()
        for attempt in range(WALK_EXIT_ATTEMPTS):
            if header is None:
                return NavigationResult(False, ScreenState.UNKNOWN, "未能打开区域地图")
            current_confirmed = (
                tuple(self._target_keys_in_text(card, header)) == (current.key,)
                if card.category == "event" else current_title in header
            )
            if not current_confirmed:
                self._close_field_map()
                return NavigationResult(
                    False, ScreenState.SANDBOX, f"区域地图不是{current.title}：{header}"
                )
            if card.category == "event":
                current_title = header
            if not self._click_walk_exit(edge, target, current_title):
                self._close_field_map()
                npc = RESTART_NAV_ENTRIES.get(card.card_id)
                if (
                    card.card_id not in WALKED_EVENT_CARD_IDS
                    or current != card.targets[0] or not npc
                    or not self._travel_via_nav_menu(npc)
                    or self.current_collection_target(card) != current.key
                ):
                    return NavigationResult(
                        False, ScreenState.SANDBOX, f"区域地图上点{target.title}出口没有反应",
                    )
                header = self._open_field_map()
                if (
                    header is None
                    or tuple(self._target_keys_in_text(card, header)) != (current.key,)
                    or not self._click_walk_exit(edge, target, current_title)
                ):
                    if header is not None:
                        self._close_field_map()
                    return NavigationResult(
                        False, ScreenState.SANDBOX, "活动出口复位后仍未确认移动",
                    )
            walk_timeout = (
                RESUMING_WALK_TIMEOUT
                if card.card_id in RESUMING_WALK_CARD_IDS
                else SANDBOX_NAVIGATION_WALK_TIMEOUT
            )
            if not self._wait_resuming_walk(walk_timeout):
                return NavigationResult(False, self.classify(), f"前往{target.title}徒步等待超时")
            arrived = self._wait_for_field_hud(
                timeout=self._loading_timeout(),
                interval=0.5,
                success_message=target.title,
                failure_message=f"前往{target.title}后未确认箱庭",
            )
            if not arrived.success:
                return arrived
            header = self._open_field_map()
            if (
                header is not None
                and tuple(self._target_keys_in_text(card, header)) == (target.key,)
            ):
                if not self._close_field_map():
                    return NavigationResult(False, ScreenState.SANDBOX_MAP, "到达后区域地图未关闭")
                return NavigationResult(True, ScreenState.SANDBOX, f"已走到{target.title}")
            if header is None or current_title not in header or attempt + 1 >= WALK_EXIT_ATTEMPTS:
                break
            # 确认仍在原地图时允许重试出口，应对巡逻驱逐或移动提前停止。
            self._status(
                "导航状态", f"仍在{current.title}（可能被巡逻驱逐），再点一次{target.title}出口"
            )
        if header is not None:
            self._close_field_map()
        return NavigationResult(
            False, ScreenState.SANDBOX, f"走到后地图不符：{header or '-'}（目标{target.title}）"
        )

    def _click_walk_exit(self, edge, target: CollectionMapTarget, current_title: str) -> bool:
        """Click the open area map's exit to ``target`` (calibrated spots or its
        OCR label) until the map closes; True once it did."""

        frame = self.vision.capture()
        end_at = monotonic() + WALK_LABEL_READ_TIMEOUT
        points = self._exit_label_points(frame, target)
        while not points and monotonic() < end_at:
            self.task.sleep(WALK_LABEL_READ_INTERVAL)
            frame = self.vision.capture()
            points = self._exit_label_points(frame, target)
        known = WALK_LABEL_EDGES.get(edge)
        if not points and known is not None:
            # 角色站在出口处可能遮挡标签，此时使用路线配置中的标定位置。
            scale = frame.shape[0] / 1080
            x = round(known[0] * frame.shape[1] / 1920)
            y = round(known[1] * scale)
            points = [(x, y + round(dy * scale)) for dy in AREA_MAP_EXIT_ICON_OFFSETS]
            self._status("区域地图出口", f"{target.title}: 标签被遮住，用记录位置 {points}")
        for x, y in EVENT_WALK_EDGES.get(edge, ()):
            point = (round(x * frame.shape[1] / 1920), round(y * frame.shape[0] / 1080))
            if point not in points:
                points.append(point)
        for point in points:
            self._status("导航状态", f"区域地图点击{target.title}出口 {point}")
            self.vision.click_client(point, frame.shape, after_sleep=WALK_CLICK_SETTLE_SECONDS)
            started = self._exit_walk_started(current_title)
            if started is not None:
                return started
            if edge in EVENT_WALK_EDGES and self._click_activity_exit_picker(target):
                started = self._exit_walk_started(current_title)
                if started is not None:
                    return started
        return False

    def _exit_walk_started(self, current_title: str) -> bool | None:
        """Return None only while the original map remains without a walk dialog."""
        frame = self.vision.capture()
        text = self._walk_text(frame)
        if text is None:
            return False
        if not any(word in text for word in AUTO_MOVE_QUESTION_KEYWORDS):
            if current_title in self._field_map_header(frame):
                return None
            return self._exit_walk_departed(frame, text)

        # An exit may ask for confirmation while its map title remains visible.
        # Once the question appears, no later exit coordinate may hit the dialog.
        if not self._click_auto_move_dialog_button(frame, "确认"):
            self._click_auto_move_dialog_button(self.vision.capture(), "取消")
            return False
        end_at = monotonic() + SANDBOX_NAVIGATION_CONFIRM_TIMEOUT
        while True:
            frame = self.vision.capture()
            text = self._walk_text(frame)
            if text is not None and not any(
                word in text for word in AUTO_MOVE_QUESTION_KEYWORDS
            ):
                if current_title in self._field_map_header(frame):
                    return self._close_field_map()
                if self._exit_walk_departed(frame, text):
                    return True
            if monotonic() >= end_at:
                self._click_auto_move_dialog_button(self.vision.capture(), "取消")
                return False
            self.task.sleep(SANDBOX_NAVIGATION_OCR_INTERVAL)

    def _exit_walk_departed(self, frame: np.ndarray, text: str) -> bool:
        """The map gave way to the walk: its banner, a patrol toast, a black
        transition, or a confirmed field/loading page.  Anything else stays unproven."""
        return (
            "移动中" in text
            or any(word in text for word in WALK_CAUGHT_KEYWORDS)
            or self._black_frame(frame)
            or self.classify(frame) in {ScreenState.SANDBOX, ScreenState.LOADING}
        )

    def _click_auto_move_dialog_button(self, frame: np.ndarray, label: str) -> bool:
        """Click a unique button paired with the walk question on the same frame."""
        try:
            boxes = self.vision.ocr_boxes(
                frame, "出口自动移动确认窗", relative_roi=AUTO_MOVE_TEXT_RELATIVE_ROI,
            )
        except (TaskDisabledException, FinishedException):
            raise
        except Exception as exc:
            self._status("出口自动移动确认窗 OCR错误", str(exc))
            return False
        normalized = [
            normalize_text(self.vision.simplify(str(getattr(box, "name", ""))))
            for box in boxes
        ]
        if not any(word in "".join(normalized) for word in AUTO_MOVE_QUESTION_KEYWORDS):
            return False
        matches = [
            box for box, text in zip(boxes, normalized, strict=True) if text == label
        ]
        if len(matches) != 1 or (center := self._ocr_box_center(matches[0])) is None:
            return False
        self._status("导航状态", f"出口自动移动确认窗：点击{label}")
        self.vision.click_client(center, frame.shape, after_sleep=0.8)
        return True

    def _click_activity_exit_picker(self, target: CollectionMapTarget) -> bool:
        frame = self.vision.capture()
        wanted = "安全区" if target.role is CollectionMapRole.MAIN_AREA else "战斗区"
        boxes = self.vision.ocr_boxes(
            frame, "活动出口列表", relative_roi=AREA_MAP_EXIT_LABEL_RELATIVE_ROI,
        )
        rows = [box for box in boxes if normalize_text(str(box.name)) == wanted]
        if len(rows) != 1 or (point := self._ocr_box_center(rows[0])) is None:
            return False
        self.vision.click_client(point, frame.shape, after_sleep=WALK_CLICK_SETTLE_SECONDS)
        return True

    def _exit_label_points(self, frame, target: CollectionMapTarget) -> list[tuple[int, int]]:
        """由目标名称的 OCR 中心生成出口点击候选，包含标签及其下方出口图标。"""

        wanted = tuple(normalize_text(title) for title in target.titles)
        scale = frame.shape[0] / 1080
        points = []
        for ocr_scale in (1.0, 2.0):
            try:
                boxes = self.vision.ocr_boxes(
                    frame, "区域地图出口", relative_roi=AREA_MAP_EXIT_LABEL_RELATIVE_ROI,
                    ocr_scale=ocr_scale, target_height=0,
                )
            except (TaskDisabledException, FinishedException):
                raise
            except Exception as exc:
                self._status("区域地图出口 OCR错误", str(exc))
                return []
            for box in boxes:
                text = normalize_text(self.vision.simplify(str(getattr(box, "name", ""))))
                if not text or not any(title in text for title in wanted):
                    continue
                center = self._ocr_box_center(box)
                if center is None:
                    continue
                for dy in AREA_MAP_EXIT_ICON_OFFSETS:
                    points.append((center[0], center[1] + round(dy * scale)))
            if points:
                break
        self._status(
            "区域地图出口",
            f"{target.title}: " + (", ".join(str(p) for p in points) or "未找到标签"),
        )
        return points

    def ensure_small_minimap(self) -> bool:
        """Switch an enlarged field minimap (⊖) back to small (⊕).

        True when the small minimap is confirmed or neither button shows
        (not in a field; nothing to do).  Only a matched ⊖ is clicked.
        """

        for _attempt in range(MINIMAP_SHRINK_ATTEMPTS):
            frame = self.vision.capture()
            large = self._optional_match(frame, MINIMAP_LARGE_TEMPLATE)
            if large is None:
                return True
            self._status("导航状态", "小地图为放大状态，切回小地图")
            self.vision.click_client(large.center, frame.shape, after_sleep=1.0)
            if self._optional_match(self.vision.capture(), MINIMAP_SMALL_TEMPLATE) is not None:
                return True
        self.task.log_warning("跑图：小地图放大状态未能切回。")
        return False

    def _confirm_travel(self) -> str:
        """Handle what follows a travel choice.

        Returns "moving" after pressing the optional 确认 (hidden when the
        user ticked 不再显示, then travel starts at once), "unreachable"
        when the game says the destination is the current area, and "stuck"
        when the dialog stayed up (it is cancelled, never left open).
        """

        end_at = monotonic() + MERCHANT_NAV_OUTCOME_TIMEOUT
        dialog_up = False
        while monotonic() <= end_at:
            frame = self.vision.capture()
            toast = self.vision.ocr_text(frame, "导航提示", roi=MERCHANT_NAV_TOAST_OCR_ROI)
            if MERCHANT_NAV_UNREACHABLE_KEYWORD in normalize_text(toast):
                return "unreachable"
            title = normalize_text(
                self.vision.ocr_text(frame, "前往确认", roi=MERCHANT_TRAVEL_DIALOG_OCR_ROI)
            )
            dialog_up = any(keyword in title for keyword in MERCHANT_TRAVEL_DIALOG_KEYWORDS)
            if dialog_up:
                if self.vision.click_ocr(
                    [r"确认"],
                    roi=MERCHANT_NAV_CONFIRM_OCR_ROI,
                    after_sleep=0.8,
                    name="前往确认按钮",
                ):
                    return "moving"
            self.task.sleep(MERCHANT_NAV_MENU_OCR_INTERVAL)
        if dialog_up:
            self.vision.click_ocr(
                [r"^取消$"], roi=MERCHANT_NAV_CONFIRM_OCR_ROI, after_sleep=0.8, name="前往取消按钮"
            )
            self.task.log_warning("跑商：前往确认弹窗的「确认」读不到，已取消。")
            return "stuck"
        return "moving"

AUTO_MOVE_TEXT_RELATIVE_ROI = (560 / 1920, 80 / 1080, 1360 / 1920, 980 / 1080)
AUTO_MOVE_QUESTION_KEYWORDS = ("是否自动移动", "前往所选目的地")
FIELD_BATTLE_TURN_RELATIVE_ROI = (0.40, 0.05, 0.60, 0.16)
FIELD_BATTLE_TIMEOUT = 180.0
# Pause button in the user's 1920x1080 battle capture.
FIELD_BATTLE_PAUSE_POINT = (1792 / 1920, 52 / 1080)
FIELD_RETREAT_TIMEOUT = 30.0
FIELD_RETREAT_ATTEMPTS = 3

TELEPORT_PAGE_ARROW_REFERENCE = {"left": (380, 520), "right": (1540, 520)}
