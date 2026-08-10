from __future__ import annotations

import re
from collections import Counter
from time import monotonic

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    ActionIconDetection,
    ActionIconSpec,
    ActionIconState,
)
from src.tasks.map_trade.collector_constants import (  # noqa: F401
    ABSORB_ACTION,
    ACTION_AFTER_CLICK_SECONDS,
    ACTION_FAILURE_FEEDBACK,
    ACTION_FEEDBACK_CHARACTER_RATIO,
    ACTION_FEEDBACK_REFERENCE_ROI,
    ACTION_FEEDBACK_RELATIVE_ROI,
    ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS,
    ACTION_FEEDBACK_TIMEOUT,
    ACTION_ICON_DETECTION_INTERVAL,
    ACTION_ICON_DETECTION_SAMPLES,
    ACTION_OCR_WINDOW_INTERVAL,
    ACTION_OCR_WINDOW_SAMPLES,
    ACTION_SUCCESS_FEEDBACK,
    BATTLE_ACTIONS,
    SEARCH_ACTION,
    SEARCH_COUNTDOWN_INTERVAL,
    SEARCH_COUNTDOWN_PATTERN,
    SEARCH_COUNTDOWN_REFERENCE_ROI,
    SEARCH_COUNTDOWN_RELATIVE_ROI,
    SEARCH_COUNTDOWN_TIMEOUT,
    SKILL_FAILURE_EVIDENCE_LIMIT,
    SKILL_FAILURE_TEXT_LIMIT,
    SKILL_FIXED_COUNT_REFERENCE_ROIS,
    SKILL_FIXED_COUNT_RELATIVE_ROIS,
    SKILL_GROUP_REFERENCE_POINTS,
    SKILL_GROUP_RELATIVE_POINTS,
    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SKILL_OCR_FALLBACK_UPSCALE,
    SKILL_OCR_UPSCALE,
    SKILL_REFERENCE_SIZE,
    SUMMON_ACTION,
    SUPPRESS_ACTION,
    UNSUPPORTED_COLLECTION_CARD_NUMBERS,
    SearchCountdownSession,
    SkillAction,
    SkillExecutionResult,
    SkillFeedbackObservation,
    _relative_reference_point,
    _relative_reference_roi,
)
from src.tasks.map_trade.models import (
    CollectionMapRole,
)
from src.tasks.map_trade.vision import parse_used_limit


class SkillExecutionMixin:
    @staticmethod
    def _action_detection_rank(detection: ActionIconDetection) -> tuple:
        state_rank = {
            ActionIconState.AVAILABLE: 3,
            ActionIconState.USED: 2,
            ActionIconState.UNKNOWN: 1,
            ActionIconState.ABSENT: 0,
        }
        return (
            state_rank[detection.state],
            detection.match.score,
            detection.match.zncc_score,
            detection.match.pixel_score,
            detection.bright_core_ratio or -1.0,
        )

    def _detect_action_icon(
        self,
        icon: ActionIconSpec,
        *,
        require_used_stable: bool = False,
    ) -> tuple[object, ActionIconDetection]:
        """Capture a short window so a transient HUD frame cannot cause a miss."""

        best_frame = None
        best_detection = None
        for attempt in range(ACTION_ICON_DETECTION_SAMPLES):
            frame = self.vision.capture()
            detection = self.action_icons.detect(frame, icon)
            if (
                best_detection is None
                or self._action_detection_rank(detection)
                > self._action_detection_rank(best_detection)
            ):
                best_frame = frame
                best_detection = detection
            if require_used_stable and detection.state is ActionIconState.USED:
                # A single dim frame can be a transition animation.  Require
                # one immediate confirming USED frame for formal action
                # evidence; any other state is returned and therefore cannot
                # authorize a click or local success.
                confirm_frame = self.vision.capture()
                confirm = self.action_icons.detect(confirm_frame, icon)
                if confirm.state is ActionIconState.USED:
                    return confirm_frame, confirm
                return confirm_frame, confirm
            if detection.state not in {
                ActionIconState.ABSENT,
                ActionIconState.UNKNOWN,
            }:
                return frame, detection
            if attempt + 1 < ACTION_ICON_DETECTION_SAMPLES:
                self.task.sleep(ACTION_ICON_DETECTION_INTERVAL)
        return best_frame, best_detection

    def _open_skill_menu(
        self,
        expected_icons: tuple[ActionIconSpec, ...],
        *,
        allow_group_one_recovery: bool = False,
    ) -> bool:
        def inspect(frame):
            detections = tuple(self.action_icons.detect(frame, icon) for icon in expected_icons)
            return detections, all(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in detections
            )

        def inspect_window():
            best_detections = None
            for attempt in range(ACTION_ICON_DETECTION_SAMPLES):
                frame = self.vision.capture()
                current, opened = inspect(frame)
                if opened:
                    return current, True
                if best_detections is None:
                    best_detections = current
                else:
                    best_detections = tuple(
                        max(
                            (previous, candidate),
                            key=self._action_detection_rank,
                        )
                        for previous, candidate in zip(
                            best_detections,
                            current,
                            strict=True,
                        )
                    )
                if attempt + 1 < ACTION_ICON_DETECTION_SAMPLES:
                    self.task.sleep(ACTION_ICON_DETECTION_INTERVAL)
            best_detections = best_detections or ()
            return best_detections, all(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in best_detections
            )

        detections, opened = inspect_window()
        if opened:
            return True

        states = ", ".join(
            f"{icon.name}={value.state.value}"
            for icon, value in zip(expected_icons, detections, strict=True)
        )
        if not allow_group_one_recovery:
            self.task.log_warning(f"地图采集：技能栏未确认：{states}。")
            return False

        # A partial match is not evidence that the wrong group is selected.
        # Never click a fixed group center merely because one action template
        # blinked; recovery is reserved for an all-missing menu in a confirmed
        # story-map context.
        if not all(
            value.state in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
            for value in detections
        ):
            self.task.log_warning(
                f"地图采集：技能栏部分识别但未确认，不执行技能组1回退：{states}。"
            )
            return False

        if self._group_one_recovery_attempted:
            self.task.log_warning(
                "地图采集：技能组1回退本次运行已尝试，不再重复点击。"
            )
            return False

        group_one = SKILL_GROUP_RELATIVE_POINTS[1]
        self._group_one_recovery_attempted = True
        self._status(
            "技能组切换",
            (
                "动作图标识别失败，点击技能组1固定中心；"
                f"relative=({group_one[0]:.6f},{group_one[1]:.6f})"
            ),
        )
        self.task.operate_click(
            *group_one,
            after_sleep=SKILL_GROUP_SWITCH_SETTLE_SECONDS,
        )
        detections, opened = inspect_window()
        if not opened:
            states = ", ".join(
                f"{icon.name}={value.state.value}"
                for icon, value in zip(expected_icons, detections, strict=True)
            )
            self.task.log_warning(f"地图采集：切换技能组1后仍未确认技能栏：{states}。")
        return opened

    @staticmethod
    def _action_text_relative_roi(
        detection: ActionIconDetection,
        frame_shape: tuple[int, ...],
    ) -> tuple[float, float, float, float]:
        height, width = frame_shape[:2]
        left, top = detection.match.position
        icon_width, icon_height = detection.match.size
        return (
            max(0.0, (left - icon_width * 0.25) / max(1, width)),
            max(0.0, (top + icon_height * 0.65) / max(1, height)),
            min(1.0, (left + icon_width * 1.25) / max(1, width)),
            min(1.0, (top + icon_height * 2.15) / max(1, height)),
        )

    @staticmethod
    def _feedback_character_ratio(text: str, keyword: str) -> float:
        actual = Counter(character for character in text if character.isalnum())
        expected = Counter(character for character in keyword if character.isalnum())
        expected_count = sum(expected.values())
        if expected_count <= 0:
            return 0.0
        return sum((actual & expected).values()) / expected_count

    def _read_action_feedback(self, action: SkillAction) -> SkillFeedbackObservation:
        best = SkillFeedbackObservation("", None)
        keywords = (
            *(('success', value) for value in ACTION_SUCCESS_FEEDBACK[action.name]),
            *(('failure', value) for value in ACTION_FAILURE_FEEDBACK.get(action.name, ())),
        )
        end_at = monotonic() + ACTION_FEEDBACK_TIMEOUT
        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                f"{action.name}执行反馈",
                relative_roi=ACTION_FEEDBACK_RELATIVE_ROI,
                target_height=1080,
            )
            # OpenCC/engine variants may emit traditional characters or join
            # neighbouring text.  Normalize before scoring, and make the
            # explicit failure keyword win ties (or a stronger positive
            # token) so ``没有可以吸收`` can never be treated as success.
            try:
                normalized = self.vision.simplify(text)
            except AttributeError:
                normalized = str(text)
            failure_matches = [
                (self._feedback_character_ratio(normalized, keyword), keyword)
                for outcome, keyword in keywords
                if outcome == "failure"
            ]
            success_matches = [
                (self._feedback_character_ratio(normalized, keyword), keyword)
                for outcome, keyword in keywords
                if outcome == "success"
            ]
            best_failure = max(failure_matches, default=(0.0, ""))
            best_success = max(success_matches, default=(0.0, ""))
            if best_failure[0] >= ACTION_FEEDBACK_CHARACTER_RATIO:
                best = SkillFeedbackObservation(
                    text,
                    "failure",
                    best_failure[0],
                    best_failure[1],
                )
            elif best_success[0] > best.ratio:
                best = SkillFeedbackObservation(
                    text,
                    "success",
                    best_success[0],
                    best_success[1],
                )
            if text and not best.text:
                best = SkillFeedbackObservation(text, None)
            matched_outcome = (
                best.outcome
                if best.ratio >= ACTION_FEEDBACK_CHARACTER_RATIO
                else None
            )
            feedback_recognized = matched_outcome is not None or (
                action.name == "吸收" and bool(best.text)
            )
            if feedback_recognized:
                break
            remaining = end_at - monotonic()
            if remaining <= 0:
                break
            self.task.sleep(min(ACTION_OCR_WINDOW_INTERVAL, remaining))
        matched_outcome = (
            best.outcome
            if best.ratio >= ACTION_FEEDBACK_CHARACTER_RATIO
            else None
        )
        observation = SkillFeedbackObservation(
            best.text,
            matched_outcome,
            best.ratio,
            best.keyword,
        )
        self._status(
            f"{action.name}执行反馈",
            (
                f"outcome={observation.outcome or 'unknown'}; "
                f"ratio={observation.ratio:.3f}; "
                f"text={observation.text or '-'}"
            ),
        )
        return observation

    def _wait_after_feedback_match(
        self,
        action: SkillAction,
        feedback: SkillFeedbackObservation,
    ) -> None:
        if feedback.outcome is None and not (
            action.name == "吸收" and feedback.text
        ):
            return
        self._status(
            f"{action.name}下一步点击",
            f"反馈已识别，等待{ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS:.1f}秒",
        )
        self.task.sleep(ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS)

    def _read_count_window(
        self,
        action: SkillAction,
        detection: ActionIconDetection | None = None,
        *,
        allow_single: bool = False,
    ) -> tuple[int, int] | None:
        samples: list[tuple[int, int]] = []
        for attempt in range(ACTION_OCR_WINDOW_SAMPLES):
            count = self._read_count(action, detection)
            if count is not None:
                samples.append(count)
            if attempt + 1 < ACTION_OCR_WINDOW_SAMPLES:
                self.task.sleep(ACTION_OCR_WINDOW_INTERVAL)
        if not samples:
            self._last_count_window_stable = False
            return None
        count, occurrences = Counter(samples).most_common(1)[0]
        self._last_count_window_stable = occurrences >= 2
        if occurrences < 2 and not allow_single:
            self._status(
                f"{action.name}次数窗口",
                f"不稳定：{samples}",
            )
            return None
        self._status(
            f"{action.name}次数窗口",
            (
                f"{'稳定' if self._last_count_window_stable else '单帧'}="
                f"{count[0]}/{count[1]}；samples={samples}"
            ),
        )
        return count

    def _start_search(
        self,
        *,
        map_role: CollectionMapRole,
    ) -> SearchCountdownSession | SkillExecutionResult:
        menu_confirmed = self._open_skill_menu(
            (SEARCH_ICON, ABSORB_ICON),
            allow_group_one_recovery=True,
        )
        if not menu_confirmed:
            return SkillExecutionResult(False, message="未确认安全区技能栏")
        search_action = SEARCH_ACTION
        frame, detection = self._detect_action_icon(search_action.icon)
        self._report_icon_detection(search_action, detection)
        if detection.state is not ActionIconState.AVAILABLE:
            if detection.state is ActionIconState.ABSENT:
                message = "未识别到探查图标"
            else:
                message = f"探查图标状态不可点击：{detection.state.value}"
            return SkillExecutionResult(False, message=message)
        countdown_roi = SEARCH_COUNTDOWN_RELATIVE_ROI
        self.vision.click_client(
            detection.match.center,
            frame.shape,
            after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        feedback = self._read_action_feedback(search_action)
        if feedback.outcome != "success":
            return SkillExecutionResult(
                False,
                message=(
                    "探查点击后未确认执行反馈："
                    f"ratio={feedback.ratio:.3f}, text={feedback.text or '-'}"
                ),
            )
        self._wait_after_feedback_match(search_action, feedback)
        end_at = monotonic() + SEARCH_COUNTDOWN_TIMEOUT
        last_text = ""
        while monotonic() <= end_at:
            frame = self.vision.capture()
            # The normal search glyph is covered by the countdown digits as
            # soon as the action starts.  Its absence (or a transient
            # ``unknown`` state) is expected and must not veto the fixed OCR
            # countdown evidence.
            last_text = self.vision.ocr_text(
                frame,
                "探查倒计时",
                relative_roi=countdown_roi,
                target_height=1080,
                ocr_scale=SKILL_OCR_UPSCALE,
            )
            countdown = re.sub(r"\D", "", last_text)
            self._status("探查倒计时", countdown or "-")
            if SEARCH_COUNTDOWN_PATTERN.fullmatch(countdown):
                return SearchCountdownSession(countdown_roi, int(countdown))
            self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)
        return SkillExecutionResult(
            False,
            message=f"探查点击后未确认倒计时：last_ocr={last_text or '-'}",
        )

    def _verify_search_countdown(self, session: SearchCountdownSession) -> bool:
        end_at = monotonic() + SEARCH_COUNTDOWN_TIMEOUT
        last_text = ""
        while monotonic() <= end_at:
            last_text = self.vision.ocr_text(
                self.vision.capture(),
                "战斗区域1探查倒计时",
                relative_roi=session.relative_roi,
                target_height=1080,
                ocr_scale=SKILL_OCR_UPSCALE,
            )
            countdown = re.sub(r"\D", "", last_text)
            self._status("探查倒计时", countdown or "-")
            if SEARCH_COUNTDOWN_PATTERN.fullmatch(countdown):
                return True
            self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)
        self.task.log_warning(
            f"地图采集：进入战斗区域1后未持续识别到探查倒计时，last_ocr={last_text or '-'}。"
        )
        return False

    def _use_actions(
        self,
        actions: tuple[SkillAction, ...],
        *,
        card_id: str,
        map_role: CollectionMapRole,
    ) -> SkillExecutionResult:
        menu_confirmed = self._open_skill_menu(
            tuple(action.icon for action in actions),
            allow_group_one_recovery=True,
        )
        if not menu_confirmed:
            return SkillExecutionResult(False, message="未确认采集技能栏")

        depleted = False
        pending_actions: list[str] = []
        for action in actions:
            result = self._use_action(action, card_id=card_id, map_role=map_role)
            if not result.completed:
                return SkillExecutionResult(
                    False,
                    depleted or result.depleted,
                    result.message,
                    tuple(pending_actions) + result.pending_actions,
                )
            depleted = depleted or result.depleted
            pending_actions.extend(result.pending_actions)
        message = ""
        if pending_actions:
            message = "动作已完成；次数待后续明亮帧对账：" + "、".join(pending_actions)
        return SkillExecutionResult(
            True,
            depleted,
            message,
            tuple(pending_actions),
        )

    def _use_action(
        self,
        action: SkillAction,
        *,
        card_id: str,
        map_role: CollectionMapRole,
    ) -> SkillExecutionResult:
        existing = self.progress.get_action_record(card_id, map_role, action.name)
        # A process restart may leave an ARMED/CLICKED intent.  Even when the
        # icon is bright again we must not click a second time; a later USED
        # frame can safely reconcile the intent instead.
        early = self._existing_record_outcome(action, existing)
        if early is not None:
            return early
        frame, detection = self._detect_action_icon(
            action.icon,
            require_used_stable=True,
        )
        self._report_icon_detection(action, detection)
        resumed = self._resume_pending_intent(
            action,
            existing,
            detection,
            card_id=card_id,
            map_role=map_role,
        )
        if resumed is not None:
            return resumed
        if detection.state in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}:
            if detection.state is ActionIconState.ABSENT:
                return SkillExecutionResult(
                    False,
                    message=f"未识别到{action.name}图标",
                )
            return SkillExecutionResult(False, message=f"{action.name}图标状态未知")
        if detection.state is ActionIconState.USED:
            return self._resolve_preexisting_used(
                action,
                detection,
                card_id=card_id,
                map_role=map_role,
            )
        before, failure = self._prepare_before_click(
            action,
            detection,
            card_id=card_id,
            map_role=map_role,
        )
        if failure is not None:
            return failure
        assert before is not None
        self.vision.click_client(
            detection.match.center,
            frame.shape,
            after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        # CLICKED is durable only after the recognized-center click
        # returns successfully.  A crash/exception during the click
        # therefore leaves the pre-click ARMED intent for safe recovery.
        self.progress.mark_action_clicked(card_id, map_role, action.name)
        feedback = self._read_action_feedback(action)
        self._wait_after_feedback_match(action, feedback)
        post_frame, post_detection = self._detect_action_icon(
            action.icon,
            require_used_stable=True,
        )
        self._report_icon_detection(action, post_detection)
        return self._finish_after_click(
            action,
            card_id=card_id,
            map_role=map_role,
            before=before,
            detection=detection,
            post_detection=post_detection,
            feedback=feedback,
        )

    def _existing_record_outcome(
        self,
        action: SkillAction,
        existing: dict[str, object] | None,
    ) -> SkillExecutionResult | None:
        if existing is None:
            return None
        existing_state = str(existing.get("state", existing.get("status", "")))
        if existing_state in {
            "local_done",
            "pending",
            "settled",
            "preexisting_used",
        }:
            pending = bool(existing.get("pending", False))
            self._status(
                f"{action.name}状态",
                "已本地完成，待次数对账" if pending else "已本地完成",
            )
            return SkillExecutionResult(
                True,
                pending_actions=(action.name,) if pending else (),
                message=("次数待后续明亮帧对账" if pending else ""),
            )
        return None

    def _resume_pending_intent(
        self,
        action: SkillAction,
        existing: dict[str, object] | None,
        detection: ActionIconDetection,
        *,
        card_id: str,
        map_role: CollectionMapRole,
    ) -> SkillExecutionResult | None:
        if existing is None:
            return None
        existing_state = str(existing.get("state", existing.get("status", "")))
        if existing_state not in {"armed", "clicked", "blocked"}:
            return None
        if detection.state is ActionIconState.USED:
            self.progress.mark_action_local_done(
                card_id,
                map_role,
                action.name,
                pending=True,
            )
            return SkillExecutionResult(
                True,
                message="重启后由稳定已使用状态完成；次数待后续明亮帧对账",
                pending_actions=(action.name,),
            )
        self.progress.mark_action_blocked(
            card_id,
            map_role,
            action.name,
            "上次点击意图未决，禁止重复点击",
        )
        return SkillExecutionResult(
            False,
            message=f"{action.name}上次点击意图未决，禁止重复点击",
        )

    def _resolve_preexisting_used(
        self,
        action: SkillAction,
        detection: ActionIconDetection,
        *,
        card_id: str,
        map_role: CollectionMapRole,
    ) -> SkillExecutionResult:
        # Capture the baseline before a checkpoint can settle older
        # records or a later target commit raises the local lower bound.
        preexisting_baseline = self.progress.trusted_action_baseline(action.name)
        pending_before = self.progress.pending_count(action.name)
        pending_records_before = tuple(
            record
            for record in self.progress.pending_action_records()
            if str(record.get("action", "")) == action.name
        )
        baseline_from_observed = bool(
            preexisting_baseline is not None
            and action.name in self.progress.state.observed_counts
            and tuple(self.progress.state.observed_counts[action.name])
            == preexisting_baseline
        )
        covered_observed: tuple[int, int] | None = None
        if pending_before:
            # A single allow_single sample is diagnostic only.  It may
            # describe this frame, but it is not trusted evidence for
            # settling an earlier pending action or updating the global
            # absolute baseline.
            self._last_count_window_stable = False
            checkpoint = self._read_count_window(
                action,
                detection,
                allow_single=True,
            )
            checkpoint_stable = bool(self._last_count_window_stable)
            if checkpoint is not None and checkpoint_stable:
                settled = self.progress.reconcile_pending(action.name, checkpoint)
                if settled:
                    self._status(
                        f"{action.name}次数对账",
                        f"明亮帧结算 {settled} 条待对账动作",
                    )
                if (
                    self.progress.pending_count(action.name) == 0
                    and preexisting_baseline is not None
                    and len(checkpoint) == 2
                    and checkpoint[1] == preexisting_baseline[1]
                    and checkpoint[0]
                    - preexisting_baseline[0]
                    - (
                        settled
                        if baseline_from_observed
                        else max(
                            0,
                            pending_before
                            - sum(
                                bool(record.get("covered", False))
                                for record in pending_records_before
                            ),
                        )
                    )
                    > 0
                ):
                    # The trusted delta contained all older pending
                    # actions plus one extra unit.  Attribute that extra
                    # unit to the current stable USED map action so it
                    # cannot wait forever for an impossible next count.
                    covered_observed = checkpoint
            elif checkpoint is not None:
                self._status(
                    f"{action.name}次数对账",
                    f"单帧诊断 {checkpoint[0]}/{checkpoint[1]}，不结算既有待对账",
                )
            if self.progress.pending_count(action.name):
                return SkillExecutionResult(
                    False,
                    message=(
                        f"{action.name}仍有{self.progress.pending_count(action.name)}条"
                        "待对账动作，安全停止当前动作"
                    ),
                )
        if not self.progress.mark_action_preexisting_used(
            card_id,
            map_role,
            action.name,
            baseline=preexisting_baseline,
            covered_observed=covered_observed,
        ):
            return SkillExecutionResult(
                False,
                message=f"{action.name}已使用但今日额度无法安全保留",
            )
        return SkillExecutionResult(
            True,
            message=(
                "动作已使用，跳过点击；次数已由明亮帧对账"
                if covered_observed is not None
                else "动作已使用，跳过点击；次数待后续明亮帧对账"
            ),
            pending_actions=()
            if covered_observed is not None
            else (action.name,),
        )

    def _prepare_before_click(
        self,
        action: SkillAction,
        detection: ActionIconDetection,
        *,
        card_id: str,
        map_role: CollectionMapRole,
    ) -> tuple[tuple[int, int] | None, SkillExecutionResult | None]:
        self._last_count_window_stable = False
        before = self._read_count_window(action, detection)
        if before is None:
            return None, SkillExecutionResult(
                False,
                message=f"{action.name}次数 OCR 失败",
            )
        self._status(f"{action.name}次数", f"{before[0]}/{before[1]}")
        remaining_pending = self.progress.pending_count(action.name)
        if remaining_pending:
            checkpoint_stable = bool(self._last_count_window_stable)
            if not checkpoint_stable:
                return None, SkillExecutionResult(
                    False,
                    message=(
                        f"{action.name}次数窗口不稳定，"
                        f"仍有{remaining_pending}条待对账动作，本次不执行新点击"
                    ),
                )
            settled = self.progress.reconcile_pending(action.name, before)
            if settled:
                self._status(
                    f"{action.name}次数对账",
                    f"明亮帧结算 {settled} 条待对账动作",
                )
            remaining_pending = self.progress.pending_count(action.name)
        if remaining_pending:
            return None, SkillExecutionResult(
                False,
                message=(
                    f"{action.name}仍有{remaining_pending}条待对账动作，"
                    "本次不执行新点击"
                ),
            )
        if detection.state is ActionIconState.AVAILABLE and before[0] >= before[1]:
            return None, SkillExecutionResult(
                False,
                True,
                f"{action.name}次数已达到 {before[0]}/{before[1]}，当前地图未完成",
            )
        if not self.progress.arm_action(
            card_id,
            map_role,
            action.name,
            baseline=before,
        ):
            return None, SkillExecutionResult(
                False,
                True,
                f"{action.name}每日额度或未决动作已阻止新点击",
            )
        return before, None

    def _finish_after_click(
        self,
        action: SkillAction,
        *,
        card_id: str,
        map_role: CollectionMapRole,
        before: tuple[int, int],
        detection: ActionIconDetection,
        post_detection: ActionIconDetection,
        feedback: SkillFeedbackObservation,
    ) -> SkillExecutionResult:
        count_detection = post_detection if post_detection.present else detection
        self._last_count_window_stable = False
        after = self._read_count_window(
            action,
            count_detection,
            allow_single=True,
        )
        post_window_stable = bool(self._last_count_window_stable)
        icon_used = post_detection.state is ActionIconState.USED
        exact_single_increment = bool(
            after is not None
            and after[1] == before[1]
            and after[0] == before[0] + 1
        )
        if after is not None:
            self._status(f"{action.name}次数", f"{after[0]}/{after[1]}")
            if post_window_stable:
                self.progress.reconcile_pending(action.name, after)

        # Explicit failure wins over a stale/bright post frame.  Absorb's
        # positive token is accepted only with a stable USED post state; a
        # negative token is never converted into success.
        if feedback.outcome == "failure":
            if post_detection.state is ActionIconState.AVAILABLE:
                self.progress.mark_action_void(
                    card_id,
                    map_role,
                    action.name,
                    feedback.text,
                )
            else:
                self.progress.mark_action_blocked(
                    card_id,
                    map_role,
                    action.name,
                    feedback.text or "失败反馈与图标状态冲突",
                )
            return SkillExecutionResult(
                False,
                (after[0] >= after[1]) if after is not None else False,
                f"{action.name}执行反馈明确失败：{feedback.text or '-'}",
            )

        feedback_success = feedback.outcome == "success"
        if action.name == "吸收" and feedback.outcome is None and feedback.text:
            # A meaningful positive-token overlap is still required when the
            # engine does not set ``outcome=success``.  The exact
            # ``吸收周围的拾取物`` token normally sets ``outcome=success``.
            overlap = self._feedback_character_ratio(
                feedback.text,
                ACTION_SUCCESS_FEEDBACK["吸收"][0],
            )
            feedback_success = overlap >= 0.50
            if feedback_success:
                self._status("吸收成功反馈待标定", feedback.text)
        if icon_used and feedback_success:
            pending = after is None
            # A valid absolute snapshot is settled immediately; any
            # dim/bare/invalid post OCR remains a durable pending action.
            trusted_post = post_window_stable or exact_single_increment
            if (
                after is not None
                and after[1] == before[1]
                and after[0] >= before[0] + 1
                and trusted_post
            ):
                self.progress.mark_action_local_done(
                    card_id,
                    map_role,
                    action.name,
                    pending=False,
                    observed=after,
                )
                pending = False
            else:
                self.progress.mark_action_local_done(
                    card_id,
                    map_role,
                    action.name,
                    pending=True,
                    observed=after,
                )
                pending = True
            return SkillExecutionResult(
                True,
                (after[0] >= after[1]) if after is not None else False,
                "次数待后续明亮帧对账" if pending else "",
                (action.name,) if pending else (),
            )

        return SkillExecutionResult(
            False,
            (after[0] >= after[1]) if after is not None else False,
            (
                f"{action.name}执行证据不一致："
                f"count={before[0]}/{before[1]}->"
                f"{(f'{after[0]}/{after[1]}' if after is not None else '待对账')}, "
                f"icon={detection.state.value}->{post_detection.state.value}, "
                f"feedback={feedback.outcome or 'unknown'}, "
                f"text={feedback.text or '-'}"
            ),
        )

    def _report_icon_detection(
        self,
        action: SkillAction,
        detection: ActionIconDetection,
    ) -> None:
        match = detection.match
        brightness = (
            "-" if detection.bright_core_ratio is None else f"{detection.bright_core_ratio:.3f}"
        )
        self._status(
            f"{action.name}图标",
            (
                f"{detection.state.value}; match={match.score:.3f}; "
                f"pixel={match.pixel_score:.3f}; zncc={match.zncc_score:.3f}; "
                f"bright={brightness}; reason={detection.reason or '-'}"
            ),
        )
        self._last_skill_observations[action.name] = {
            "state": detection.state.value,
            "match": round(float(match.score), 4),
            "pixel": round(float(match.pixel_score), 4),
            "zncc": round(float(match.zncc_score), 4),
            "bright": (
                None
                if detection.bright_core_ratio is None
                else round(float(detection.bright_core_ratio), 4)
            ),
            "reason": str(detection.reason or "")[:SKILL_FAILURE_TEXT_LIMIT],
        }

    def _read_count(
        self,
        action: SkillAction,
        detection: ActionIconDetection | None = None,
    ) -> tuple[int, int] | None:
        # The calibrated fixed ROI is intentionally reused at 3x after a 2x
        # miss.  This catches the reproduced dark ``2`` without widening the
        # region into adjacent UI text.
        scales = (
            (SKILL_OCR_UPSCALE, SKILL_OCR_FALLBACK_UPSCALE)
            if action.fixed_count_relative_roi is not None
            else (SKILL_OCR_UPSCALE,)
        )
        for index, scale in enumerate(scales):
            frame = self.vision.capture()
            if action.count_roi is not None:
                text = self.vision.ocr_text(
                    frame,
                    f"{action.name}次数",
                    roi=action.count_roi,
                )
            elif action.fixed_count_relative_roi is not None:
                text = self.vision.ocr_text(
                    frame,
                    f"{action.name}次数",
                    relative_roi=action.fixed_count_relative_roi,
                    target_height=1080,
                    ocr_scale=scale,
                )
            elif detection is not None:
                text = self.vision.ocr_text(
                    frame,
                    f"{action.name}次数",
                    relative_roi=self._action_text_relative_roi(
                        detection,
                        frame.shape,
                    ),
                    target_height=1080,
                )
            else:
                return None
            count = parse_used_limit(text)
            if count is not None:
                return count
            if index + 1 < len(scales):
                # No long stability wait before the fallback; it is a
                # same-frame/next-frame best-effort read.
                continue
        return None

