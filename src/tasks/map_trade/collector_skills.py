from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from functools import partial
from time import monotonic

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    ActionIconDetection,
    ActionIconSpec,
    ActionIconState,
)
from src.tasks.map_trade.collector_constants import (
    ABSORB_ACTION,
    ACTION_AFTER_CLICK_SECONDS,
    ACTION_FAILURE_FEEDBACK,
    ACTION_FEEDBACK_CHARACTER_RATIO,
    ACTION_FEEDBACK_RELATIVE_ROI,
    ACTION_FEEDBACK_RELEVANCE_RATIO,
    ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS,
    ACTION_FEEDBACK_TIMEOUT,
    ACTION_ICON_DETECTION_INTERVAL,
    ACTION_ICON_DETECTION_SAMPLES,
    ACTION_OCR_WINDOW_INTERVAL,
    ACTION_OCR_WINDOW_SAMPLES,
    ACTION_SUCCESS_FEEDBACK,
    PRESS_AGAIN_PAUSE_SECONDS,
    SEARCH_ACTION,
    SEARCH_BUFF_SECONDS,
    SEARCH_COUNTDOWN_INTERVAL,
    SEARCH_COUNTDOWN_PATTERN,
    SEARCH_COUNTDOWN_RELATIVE_ROI,
    SEARCH_COUNTDOWN_TIMEOUT,
    SEARCH_MISSED_FEEDBACK_COUNTDOWN_SECONDS,
    SEARCH_REUSE_MAX_AGE_SECONDS,
    SKILL_FAILURE_TEXT_LIMIT,
    SKILL_GROUP_RELATIVE_POINTS,
    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SKILL_OCR_FALLBACK_UPSCALE,
    SKILL_OCR_UPSCALE,
    SUMMON_ACTION,
    SUPPRESS_ACTION,
    CountWindowObservation,
    SearchCountdownSession,
    SkillAction,
    SkillExecutionResult,
    SkillFeedbackObservation,
)
from src.tasks.map_trade.models import (
    CollectionMapRole,
    MatchResult,
)
from src.tasks.map_trade.vision import parse_used_limit


class SkillExecutionMixin:
    def _capture_skill_field(self):
        captured = self.vision.capture()
        frame = self.navigator.wait_for_collection_field(captured)
        recovered = frame is not captured
        if recovered:
            self._skill_field_generation += 1
            self._forget_skill_evidence()
            if self._collection_target is not None:
                card, target = self._collection_target
                if self.navigator.current_collection_target(card) != target.key:
                    raise RuntimeError("遭遇战恢复后目标地图未确认，停止采集技能操作")
                captured = self.vision.capture()
                frame = self.navigator.wait_for_collection_field(captured)
                if frame is not captured:
                    raise RuntimeError("地图复核后场景再次变化，停止采集技能操作")
                if (
                    self._recovery_suppression_enabled
                    and target.role is not CollectionMapRole.MAIN_AREA
                ):
                    self._suppress_after_retreat(frame, card_id=card.card_id, map_role=target.role)
                    captured = self.vision.capture()
                    frame = self.navigator.wait_for_collection_field(captured)
                    if frame is not captured:
                        raise RuntimeError("退战压制后场景再次变化，停止采集")
        return frame, recovered

    def _suppress_after_retreat(self, frame, *, card_id, map_role):
        """Use the verified field frames for both icon and quota before an urgent cast."""
        action = SUPPRESS_ACTION
        existing = self.progress.get_action_record(card_id, map_role, action.name)
        early = self._existing_record_outcome(action, existing)
        if early is not None:
            if not early.completed:
                raise RuntimeError(early.message)
            return
        if self.progress.weekly_action_complete(card_id, map_role, action.name):
            return
        detections = []
        counts = []
        baseline = self.progress.trusted_action_baseline(action.name)
        unresolved = self.progress.has_unresolved_action(card_id, map_role, action.name)
        for sample in range(2):
            if sample:
                frame = self.vision.capture()
                if self.navigator.wait_for_collection_field(frame) is not frame:
                    raise RuntimeError("退战压制准备期间场景再次变化，停止采集")
            detection = self.action_icons.detect(frame, action.icon)
            detections.append(detection)
            if detection.state is ActionIconState.AVAILABLE and not unresolved:
                # First-use capacity needs agreement at both OCR scales as well as two frames.
                scales = (SKILL_OCR_UPSCALE,) if baseline else (
                    SKILL_OCR_UPSCALE, SKILL_OCR_FALLBACK_UPSCALE,
                )
                readings = [
                    self._read_count(
                        action, detection, frame=frame, ocr_scale=scale,
                        known_limit=baseline[1] if baseline else None,
                    ) for scale in scales
                ]
                counts.append(readings[0] if all(c == readings[0] for c in readings) else None)
        first, last = detections
        if not self._action_detection_is_stable(first, last):
            raise RuntimeError("退战后压制图标未稳定确认，未点击")
        detection = self._stable_detection(last, 2)
        self._report_icon_detection(action, detection)
        result = self._resume_pending_intent(
            action, existing, detection, card_id=card_id, map_role=map_role,
        )
        if result is None and detection.state is ActionIconState.USED:
            result = self._resolve_preexisting_used(
                action, detection, card_id=card_id, map_role=map_role,
            )
        if result is not None:
            if not result.completed:
                raise RuntimeError(result.message)
            return
        count = counts[0] if len(counts) == 2 and counts[0] == counts[1] else None
        before, failure = self._prepare_before_click(
            action, detection, card_id=card_id, map_role=map_role,
            observation=CountWindowObservation(count, count is not None),
        )
        if failure is not None:
            raise RuntimeError(failure.message)
        if not self.progress.arm_action(card_id, map_role, action.name, baseline=before):
            raise RuntimeError("退战压制已被每日额度或未决动作阻止")
        self._status("导航状态", "目标地图已确认，优先压制")
        self.vision.click_client(
            detection.match.center, frame.shape, after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        # The ordinary action loop settles this durable intent; recovery never replays it.
        self.progress.mark_action_clicked(card_id, map_role, action.name)

    @staticmethod
    def _action_detection_is_stable(
        previous: ActionIconDetection,
        current: ActionIconDetection,
    ) -> bool:
        """Require one state and physical slot identity across two frames."""

        if previous.state is not current.state:
            return False
        if previous.state not in {
            ActionIconState.AVAILABLE,
            ActionIconState.USED,
        }:
            return False
        if previous.semantic_state and current.semantic_state:
            if previous.semantic_state != current.semantic_state:
                return False
        first = previous.match
        second = current.match
        if min(*first.size, *second.size) <= 0:
            return False
        scale = max(
            0.2,
            min(
                abs(float(first.scale or 1.0)),
                abs(float(second.scale or 1.0)),
            ),
        )
        tolerance = max(2, round(6.0 * scale))
        return (
            abs(first.center[0] - second.center[0]) <= tolerance
            and abs(first.center[1] - second.center[1]) <= tolerance
            and abs(first.size[0] - second.size[0]) <= tolerance
            and abs(first.size[1] - second.size[1]) <= tolerance
        )

    @staticmethod
    def _stable_detection(
        detection: ActionIconDetection,
        sample_count: int,
    ) -> ActionIconDetection:
        reason = str(detection.reason or "")
        if "跨帧稳定" not in reason:
            reason = f"{reason}；跨帧稳定确认" if reason else "跨帧稳定确认"
        return replace(
            detection,
            reason=reason,
            stable=True,
            sample_count=max(2, int(sample_count)),
        )

    @staticmethod
    def _unstable_detection(
        detection: ActionIconDetection,
        sample_count: int,
    ) -> ActionIconDetection:
        reason = str(detection.reason or "")
        if "跨帧稳定" not in reason:
            reason = (
                f"{reason}；未达到跨帧稳定确认"
                if reason
                else "未达到跨帧稳定确认"
            )
        return replace(
            detection,
            reason=reason,
            stable=False,
            sample_count=max(1, int(sample_count)),
        )

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
        require_stable: bool = False,
    ) -> tuple[object, ActionIconDetection]:
        """Capture a short window so a transient HUD frame cannot cause a miss."""

        stable_required = require_stable
        best_frame = None
        best_detection = None
        previous_detection = None
        observed_samples = 0
        attempt = recoveries = 0
        while attempt < ACTION_ICON_DETECTION_SAMPLES:
            frame, recovered = self._capture_skill_field()
            if recovered:
                recoveries += 1
                if recoveries > 2:
                    raise RuntimeError("技能采样期间反复发生场景切换，停止采集")
                best_frame = best_detection = previous_detection = None
                observed_samples = attempt = 0
            detection = self.action_icons.detect(frame, icon)
            observed_samples += 1
            attempt += 1
            if (
                best_detection is None
                or self._action_detection_rank(detection)
                > self._action_detection_rank(best_detection)
            ):
                best_frame = frame
                best_detection = detection
            if stable_required:
                if (
                    detection.stable
                    and detection.sample_count >= 2
                    and detection.state
                    in {ActionIconState.AVAILABLE, ActionIconState.USED}
                ):
                    return frame, detection
                if (
                    previous_detection is not None
                    and self._action_detection_is_stable(previous_detection, detection)
                ):
                    return frame, self._stable_detection(
                        detection,
                        max(
                            2,
                            previous_detection.sample_count,
                            detection.sample_count,
                        ),
                    )
                if detection.state in {
                    ActionIconState.AVAILABLE,
                    ActionIconState.USED,
                }:
                    previous_detection = detection
                else:
                    previous_detection = None
                if attempt < ACTION_ICON_DETECTION_SAMPLES:
                    self.task.sleep(ACTION_ICON_DETECTION_INTERVAL)
                continue
            if detection.state not in {
                ActionIconState.ABSENT,
                ActionIconState.UNKNOWN,
            }:
                return frame, detection
            if attempt < ACTION_ICON_DETECTION_SAMPLES:
                self.task.sleep(ACTION_ICON_DETECTION_INTERVAL)
        if best_detection is None:
            best_detection = ActionIconDetection(
                ActionIconState.ABSENT,
                MatchResult(-1.0, (0, 0), (0, 0)),
                reason="未取得图标识别帧",
            )
        if stable_required and best_detection.state in {
            ActionIconState.AVAILABLE,
            ActionIconState.USED,
        }:
            best_detection = self._unstable_detection(
                best_detection,
                observed_samples,
            )
        return best_frame, best_detection

    def _forget_skill_evidence(self) -> None:
        """技能栏未确认时丢弃旧观察，避免失败转储误导排查。"""

        self._last_skill_observations = {}
        self._last_skill_geometry = {}

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
            attempt = recoveries = 0
            while attempt < ACTION_ICON_DETECTION_SAMPLES:
                frame, recovered = self._capture_skill_field()
                if recovered:
                    recoveries += 1
                    if recoveries > 2:
                        raise RuntimeError("技能栏确认期间反复发生场景切换，停止采集")
                    best_detections = None
                    attempt = 0
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
                attempt += 1
                if attempt < ACTION_ICON_DETECTION_SAMPLES:
                    self.task.sleep(ACTION_ICON_DETECTION_INTERVAL)
            best_detections = best_detections or ()
            return best_detections, all(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in best_detections
            )

        detections, opened = inspect_window()
        if opened or self._counts_prove_menu(expected_icons):
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

        # 采集技能可能配置在任意技能组，逐组识别并停留在包含目标技能的一组。
        for group, point in sorted(SKILL_GROUP_RELATIVE_POINTS.items()):
            frame, _recovered = self._capture_skill_field()
            detections, opened = inspect(frame)
            if opened:
                return True
            if any(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in detections
            ):
                self.task.log_warning("地图采集：技能栏部分识别，停止技能组切换。")
                return False
            self._status("技能组切换", f"采集技能未显示，切换到技能组{group}")
            self.task.operate_click(*point, after_sleep=SKILL_GROUP_SWITCH_SETTLE_SECONDS)
            detections, opened = inspect_window()
            if opened or self._counts_prove_menu(expected_icons):
                return True
        states = ", ".join(
            f"{icon.name}={value.state.value}"
            for icon, value in zip(expected_icons, detections, strict=True)
        )
        self.task.log_warning(f"地图采集：三个技能组都没有找到采集技能：{states}。")
        return False

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
            # OCR 只输出简体中文（繁体转换已取消）；引擎可能粘连相邻文本，
            # 先归一再打分，并让显式失败关键字在平分（或更强正向词）时获胜，
            # 确保 ``没有可以吸收`` 不会被当成成功。
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
            # 吸收的成功文案与探查横幅堆叠在同一区域：读到与当前动作无关
            # 的横幅（重合率低于相关性阈值）必须继续轮询，等真正的横幅
            # 出现（实测出现延迟<=0.3s、驻留约3s）。
            absorb_relevant = (
                action.name == "吸收"
                and bool(best.text)
                and max(best_failure[0], best_success[0])
                >= ACTION_FEEDBACK_RELEVANCE_RATIO
            )
            feedback_recognized = matched_outcome is not None or absorb_relevant
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
        expected_used: int | None = None,
        expected_limit: int | None = None,
    ) -> CountWindowObservation:
        """Return a compatible count; single-frame evidence requires allow_single explicitly."""
        if expected_limit is None:
            baseline = self.progress.trusted_action_baseline(action.name)
            expected_limit = baseline[1] if baseline is not None else None
        initial_count = None
        for ocr_scale in (None, SKILL_OCR_FALLBACK_UPSCALE):
            samples: list[tuple[int, int]] = []
            for attempt in range(ACTION_OCR_WINDOW_SAMPLES):
                count = self._read_count(
                    action, detection, ocr_scale=ocr_scale,
                    known_limit=expected_limit, expected_used=expected_used,
                )
                if count is not None:
                    samples.append(count)
                if attempt + 1 < ACTION_OCR_WINDOW_SAMPLES:
                    self.task.sleep(ACTION_OCR_WINDOW_INTERVAL)
            count, occurrences = Counter(samples).most_common(1)[0] if samples else (None, 0)
            compatible = count is not None and (
                expected_used is None or count[0] == expected_used
            ) and (expected_limit is None or count[1] == expected_limit)
            self._status(
                f"{action.name}次数窗口",
                f"{'稳定' if occurrences >= 2 else '不稳定'}={count}；"
                f"scale={ocr_scale or '2x→3x'}；samples={samples}；"
                f"expected={expected_used}/{expected_limit}",
            )
            if compatible and (occurrences >= 2 or (ocr_scale is not None and allow_single)):
                if expected_limit is None and ocr_scale is None:
                    initial_count = count
                    continue
                if initial_count is not None and count != initial_count:
                    self._status(f"{action.name}次数校验", "首次次数跨倍率读数冲突，暂不记账")
                    return CountWindowObservation(None, False)
                return CountWindowObservation(count, occurrences >= 2)
            self._status(
                f"{action.name}次数校验",
                f"候选={count}；稳定={occurrences >= 2}；"
                f"预期={expected_used}/{expected_limit}；"
                + ("以3x新帧复读" if ocr_scale is None else "未取得可信读数"),
            )
        return CountWindowObservation(None, False)

    def _reuse_recent_search(
        self,
        card_id: str | None,
        map_role: CollectionMapRole,
    ) -> SearchCountdownSession | None:
        """账本里有本卡带近期的探查施放，且图标不可读（被倒计时覆盖）时复用。

        重进卡带后倒计时数字的 OCR 不可靠（实测重进窗口内一致读空），
        因此以内存账本时间戳为准，不依赖数字；图标恢复 AVAILABLE 说明
        增益已结束，走正常施放。
        """

        if card_id is None:
            return None
        cast_at = self._search_cast_monotonic.get((card_id, map_role.value))
        if cast_at is None:
            return None
        elapsed = monotonic() - cast_at
        if elapsed >= SEARCH_REUSE_MAX_AGE_SECONDS:
            return None
        frame, detection = self._detect_action_icon(SEARCH_ACTION.icon)
        if detection.state is ActionIconState.AVAILABLE:
            self._search_cast_monotonic.pop((card_id, map_role.value), None)
            return None
        remaining = max(1, int(SEARCH_BUFF_SECONDS - elapsed))
        self._status(
            "探查倒计时",
            f"复用账本施放记录（{elapsed:.0f}秒前）；预估剩余{remaining}s",
        )
        return SearchCountdownSession(
            SEARCH_COUNTDOWN_RELATIVE_ROI,
            remaining,
        )

    def _start_search(
        self,
        *,
        map_role: CollectionMapRole,
        card_id: str | None = None,
        slot_fallback: bool = False,
    ) -> SearchCountdownSession | SkillExecutionResult:
        reused = self._reuse_recent_search(card_id, map_role)
        if reused is not None:
            return reused
        menu_confirmed = self._open_skill_menu(
            (ABSORB_ICON,) if slot_fallback else (SEARCH_ICON, ABSORB_ICON),
            allow_group_one_recovery=True,
        )
        if not menu_confirmed:
            return SkillExecutionResult(False, message="未确认安全区技能栏")
        search_action = SEARCH_ACTION
        frame, detection = self._detect_action_icon(
            search_action.icon,
            require_stable=True,
        )
        self._report_icon_detection(search_action, detection)
        can_click_search = detection.state is ActionIconState.AVAILABLE and detection.stable
        if not can_click_search and not slot_fallback:
            if detection.state is ActionIconState.ABSENT:
                message = "未识别到探查图标"
            elif not detection.stable:
                message = "探查图标未达到跨帧稳定确认"
            else:
                message = f"探查图标状态不可点击：{detection.state.value}"
            return SkillExecutionResult(False, message=message)
        countdown_roi = SEARCH_COUNTDOWN_RELATIVE_ROI
        if can_click_search:
            center = detection.match.center
        else:
            left, top, right, bottom = countdown_roi
            center = (
                round((left + right) / 2 * frame.shape[1]),
                round((top + bottom) / 2 * frame.shape[0]),
            )
        self.vision.click_client(
            center,
            frame.shape,
            after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        feedback = self._read_action_feedback(search_action)
        if feedback.outcome == "failure" or (
            feedback.outcome != "success" and not self._search_started_without_feedback()
        ):
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
                if card_id is not None:
                    self._search_cast_monotonic[(card_id, map_role.value)] = monotonic()
                return SearchCountdownSession(countdown_roi, int(countdown))
            self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)
        return SkillExecutionResult(
            False,
            message=f"探查点击后未确认倒计时：last_ocr={last_text or '-'}",
        )

    def _use_actions(
        self,
        actions: tuple[SkillAction, ...],
        *,
        card_id: str,
        map_role: CollectionMapRole,
        recovery_only: bool = False,
    ) -> SkillExecutionResult:
        if recovery_only:
            actions = tuple(
                action for action in actions
                if self.progress.has_unresolved_action(card_id, map_role, action.name)
            )
        self._forget_skill_evidence()
        for action in actions:
            for suffix in (
                "图标", "次数", "次数 OCR", "次数窗口", "执行反馈",
                "执行反馈 OCR", "下一步点击", "状态", "次数对账", "次数校验",
            ):
                self._status(f"{action.name}{suffix}", "-")
        remaining_icons = tuple(
            action.icon for action in actions
            if not self.progress.weekly_action_complete(card_id, map_role, action.name)
        )
        menu_confirmed = not remaining_icons or self._open_skill_menu(
            remaining_icons, allow_group_one_recovery=True,
        )
        if not menu_confirmed:
            return SkillExecutionResult(False, message="未确认采集技能栏")

        depleted = False
        pending_actions: list[str] = []
        index = 0
        while index < len(actions):
            action = actions[index]
            paired = (
                not recovery_only and actions[index:index + 2] == (SUMMON_ACTION, SUPPRESS_ACTION)
            )
            execute = self._use_summon_and_suppress if paired else partial(self._use_action, action)
            result = execute(card_id=card_id, map_role=map_role)
            if result.press_again and not recovery_only:
                self.task.sleep(PRESS_AGAIN_PAUSE_SECONDS)
                result = execute(card_id=card_id, map_role=map_role)
            if not result.completed:
                return SkillExecutionResult(
                    False,
                    result.depleted,
                    result.message,
                    tuple(pending_actions) + result.pending_actions,
                )
            depleted = depleted or result.depleted
            pending_actions.extend(result.pending_actions)
            index += 2 if paired else 1
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
        prepared = self._prepare_action(action, card_id=card_id, map_role=map_role)
        if isinstance(prepared, SkillExecutionResult):
            return prepared
        failure = self._click_prepared_action(action, prepared, card_id=card_id, map_role=map_role)
        if failure is not None:
            return failure
        feedback = self._read_action_feedback(action)
        self._wait_after_feedback_match(action, feedback)
        return self._verify_clicked_action(
            action, prepared, feedback, card_id=card_id, map_role=map_role,
        )

    def _use_summon_and_suppress(self, *, card_id, map_role) -> SkillExecutionResult:
        """Prepare both quotas before attracting enemies; verify after suppression."""
        kwargs = {"card_id": card_id, "map_role": map_role}
        summon = self._prepare_action(SUMMON_ACTION, **kwargs)
        if isinstance(summon, SkillExecutionResult):
            if not summon.completed:
                return summon
            suppress_result = self._use_action(SUPPRESS_ACTION, **kwargs)
            summon_result = summon
        else:
            suppress = self._prepare_action(SUPPRESS_ACTION, **kwargs)
            if isinstance(suppress, SkillExecutionResult) and not suppress.completed:
                return suppress
            failure = self._click_prepared_action(SUMMON_ACTION, summon, **kwargs)
            if failure is not None:
                return failure
            feedback = self._read_action_feedback(SUMMON_ACTION)
            if feedback.outcome != "success":
                summon_result = self._verify_clicked_action(
                    SUMMON_ACTION, summon, feedback, **kwargs,
                )
                if not summon_result.completed:
                    return summon_result
            if isinstance(suppress, SkillExecutionResult):
                suppress_result = suppress
            else:
                failure = self._click_prepared_action(SUPPRESS_ACTION, suppress, **kwargs)
                if failure is not None:
                    # A changed scene invalidates preparation, but has not armed this action.
                    suppress_result = (
                        self._use_action(SUPPRESS_ACTION, **kwargs)
                        if failure.press_again else failure
                    )
                else:
                    suppress_feedback = self._read_action_feedback(SUPPRESS_ACTION)
                    self._wait_after_feedback_match(SUPPRESS_ACTION, suppress_feedback)
                    suppress_result = self._verify_clicked_action(
                        SUPPRESS_ACTION, suppress, suppress_feedback, **kwargs,
                    )
            if feedback.outcome == "success":
                summon_result = self._verify_clicked_action(
                    SUMMON_ACTION, summon, feedback, **kwargs,
                )
        return SkillExecutionResult(
            summon_result.completed and suppress_result.completed,
            summon_result.depleted or suppress_result.depleted,
            "; ".join(r.message for r in (summon_result, suppress_result) if r.message),
            summon_result.pending_actions + suppress_result.pending_actions,
            press_again=summon_result.press_again or suppress_result.press_again,
        )

    def _prepare_action(self, action, *, card_id, map_role):
        existing = self.progress.get_action_record(card_id, map_role, action.name)
        # A process restart may leave an ARMED/CLICKED intent.  Even when the
        # icon is bright again we must not click a second time; a later USED
        # frame can safely reconcile the intent instead.
        early = self._existing_record_outcome(action, existing)
        if early is not None:
            return early
        if existing is None and self.progress.weekly_action_complete(
            card_id, map_role, action.name,
        ):
            self._status(f"{action.name}状态", "本周已有完成证据，跨日续跑跳过")
            return SkillExecutionResult(True)
        frame, detection = self._detect_action_icon(
            action.icon,
            require_stable=True,
        )
        existing = self.progress.get_action_record(card_id, map_role, action.name)
        early = self._existing_record_outcome(action, existing)
        if early is not None:
            return early
        self._report_icon_detection(action, detection)
        if (
            detection.state in {ActionIconState.AVAILABLE, ActionIconState.USED}
            and not detection.stable
        ):
            return SkillExecutionResult(
                False,
                message=f"{action.name}图标未达到跨帧稳定确认",
            )
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
            _frame, recovered = self._capture_skill_field()
            if recovered:
                return SkillExecutionResult(
                    False, message="次数读取期间场景变化，恢复后重新准备技能", press_again=True,
                )
            return failure
        assert before is not None
        return detection, before, self._skill_field_generation

    def _click_prepared_action(self, action, prepared, *, card_id, map_role):
        detection, before, generation = prepared
        frame, recovered = self._capture_skill_field()
        existing = self.progress.get_action_record(card_id, map_role, action.name)
        early = self._existing_record_outcome(action, existing)
        if early is not None:
            return early
        fresh = self.action_icons.detect(frame, action.icon)
        if (
            recovered or generation != self._skill_field_generation
            or not self._action_detection_is_stable(detection, fresh)
        ):
            return SkillExecutionResult(
                False, message="消费前场景或技能状态变化，重新读取次数和图标", press_again=True,
            )
        if not self.progress.arm_action(card_id, map_role, action.name, baseline=before):
            return SkillExecutionResult(
                False, True, f"{action.name}每日额度或未决动作已阻止新点击",
            )
        self.vision.click_client(
            fresh.match.center,
            frame.shape,
            after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        # CLICKED is durable only after the recognized-center click
        # returns successfully.  A crash/exception during the click
        # therefore leaves the pre-click ARMED intent for safe recovery.
        self.progress.mark_action_clicked(card_id, map_role, action.name)
        return None

    def _verify_clicked_action(self, action, prepared, feedback, *, card_id, map_role):
        detection, before, _generation = prepared
        post_frame, post_detection = self._detect_action_icon(
            action.icon,
            require_stable=True,
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
        if existing_state == "blocked" and "invalid_baseline" in existing:
            return SkillExecutionResult(False, message="动作基线损坏，需核对账本后恢复")
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
        if self.progress.mark_action_preexisting_used(card_id, map_role, action.name):
            return SkillExecutionResult(
                True, message="地图技能已不可用，记录周状态；本次未点击，不计今日消耗"
            )
        return SkillExecutionResult(False, message=f"{action.name}已有未决点击，需核验")

    def _prepare_before_click(
        self,
        action: SkillAction,
        detection: ActionIconDetection,
        *,
        card_id: str,
        map_role: CollectionMapRole,
        observation: CountWindowObservation | None = None,
    ) -> tuple[tuple[int, int] | None, SkillExecutionResult | None]:
        remaining_pending = self.progress.pending_count(action.name)
        # A fresh run may observe casts performed outside this task. During a
        # continuous run, changes must be covered by our recorded consumption.
        expected_used = (
            self.progress.effective_used(action.name)
            if action.name in self._count_actions_seen or remaining_pending else None
        )
        baseline = self.progress.trusted_action_baseline(action.name)
        if observation is None:
            observation = self._read_count_window(
                action, detection, expected_used=expected_used,
                expected_limit=baseline[1] if baseline is not None else None,
            )
        elif (
            observation.count is None or not observation.is_stable
            or (expected_used is not None and observation.count[0] != expected_used)
            or (baseline is not None and observation.count[1] != baseline[1])
        ):
            observation = CountWindowObservation(None, False)
        before = observation.count
        if before is None:
            return None, SkillExecutionResult(
                False,
                message=f"{action.name}未取得可信次数，未执行新点击",
            )
        self._status(f"{action.name}次数", f"{before[0]}/{before[1]}")
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
        if self.progress.trusted_action_baseline(action.name) != before:
            return None, SkillExecutionResult(
                False,
                message=f"{action.name}次数与账本记录冲突，未执行新点击",
            )
        if detection.state is ActionIconState.AVAILABLE and before[0] >= before[1]:
            return None, SkillExecutionResult(
                False,
                True,
                f"{action.name}次数已达到 {before[0]}/{before[1]}，当前地图未完成",
            )
        self._count_actions_seen.add(action.name)
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
        observation = self._read_count_window(
            action,
            count_detection,
            allow_single=True,
            expected_used=before[0] + 1,
            expected_limit=before[1],
        )
        after = observation.count
        post_window_stable = observation.is_stable
        icon_used = post_detection.state is ActionIconState.USED and post_detection.stable
        has_confirmed_increment = after is not None
        if after is not None:
            self._status(f"{action.name}次数", f"{after[0]}/{after[1]}")

        # Explicit failure wins even when the count or icon suggests success.
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
                False,
                f"{action.name}执行反馈明确失败：{feedback.text or '-'}",
            )

        confirmed_increment = (
            detection.state is ActionIconState.AVAILABLE
            and detection.stable
            and post_window_stable
            and has_confirmed_increment
            and (
                feedback.outcome == "success"
                or (post_detection.state is ActionIconState.AVAILABLE and post_detection.stable)
            )
        )
        confirmed_success = icon_used or confirmed_increment
        if confirmed_success:
            # Repeated dim OCR can agree on the wrong digit. Only a stable
            # single increment may update the trusted absolute count.
            trusted_post = post_window_stable and has_confirmed_increment
            if trusted_post:
                self.progress.mark_action_local_done(
                    card_id,
                    map_role,
                    action.name,
                    pending=False,
                    observed=after,
                )
                self.progress.reconcile_pending(action.name, after)
                pending = False
            else:
                self.progress.mark_action_local_done(
                    card_id,
                    map_role,
                    action.name,
                    pending=True,
                )
                pending = True
                self._status(
                    f"{action.name}次数对账",
                    f"动作已完成；次数待明亮帧确认；before={before}；candidate={after}；"
                    f"stable={post_window_stable}；icon={post_detection.state.value}",
                )
            return SkillExecutionResult(
                True,
                bool(trusted_post and after[0] >= after[1]),
                "次数待后续明亮帧对账" if pending else "",
                (action.name,) if pending else (),
            )

        if (
            feedback.outcome is None
            and post_detection.state is ActionIconState.AVAILABLE
            and post_detection.stable
        ):
            unchanged = self._read_count_window(
                action,
                post_detection,
                expected_used=before[0],
                expected_limit=before[1],
            )
            if unchanged.count is not None:
                self.progress.mark_action_void(
                    card_id, map_role, action.name, "图标仍亮且次数稳定未变"
                )
                return SkillExecutionResult(
                    False, message=f"{action.name}点击未生效", press_again=True
                )

        return SkillExecutionResult(
            False,
            False,
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
        contrast = (
            "-" if detection.relative_contrast is None else f"{detection.relative_contrast:.3f}"
        )
        geometry = getattr(self.vision, "last_frame_geometry", None)
        geometry_data = self._serialize_frame_geometry(geometry)
        candidates = detection.evidence_candidates
        if not candidates:
            evidence_by_name = getattr(self.vision, "last_candidate_evidence", {})
            try:
                candidates = tuple(evidence_by_name.get(action.template.name, ()))
            except AttributeError:
                candidates = ()
        candidate_data = tuple(
            self._serialize_candidate(candidate)
            for candidate in candidates[:8]
        )
        self._last_skill_geometry = geometry_data
        self._status(
            f"{action.name}图标",
            (
                f"{detection.state.value}; match={match.score:.3f}; "
                f"pixel={match.pixel_score:.3f}; zncc={match.zncc_score:.3f}; "
                f"gradient={match.gradient_zncc_score:.3f}; "
                f"edge={match.edge_score:.3f}; scale={match.scale:.3f}; "
                f"margin={detection.candidate_margin:.3f}; "
                f"stable={detection.stable}/{detection.sample_count}; "
                f"bright={brightness}; contrast={contrast}; reason={detection.reason or '-'}"
            ),
        )
        self._last_skill_observations[action.name] = {
            "state": detection.state.value,
            "semantic_state": str(detection.semantic_state or ""),
            "stable": bool(detection.stable),
            "sample_count": int(max(1, detection.sample_count)),
            "match": round(float(match.score), 4),
            "pixel": round(float(match.pixel_score), 4),
            "zncc": round(float(match.zncc_score), 4),
            "gradient": round(float(match.gradient_zncc_score), 4),
            "edge": round(float(match.edge_score), 4),
            "scale": round(float(match.scale), 4),
            "position": [int(value) for value in match.position],
            "size": [int(value) for value in match.size],
            "center": [int(value) for value in match.center],
            "candidate_margin": round(float(detection.candidate_margin), 4),
            "geometry": geometry_data,
            "candidates": list(candidate_data),
            "bright": (
                None
                if detection.bright_core_ratio is None
                else round(float(detection.bright_core_ratio), 4)
            ),
            "relative_contrast": (
                None if detection.relative_contrast is None
                else round(float(detection.relative_contrast), 4)
            ),
            "reason": str(detection.reason or "")[:SKILL_FAILURE_TEXT_LIMIT],
        }

    @staticmethod
    def _finite_number(value, default: float = -1.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if number == number and abs(number) != float("inf") else default

    @classmethod
    def _serialize_frame_geometry(cls, geometry) -> dict[str, object]:
        if geometry is None:
            return {}
        return {
            "frame_width": int(geometry.frame_width),
            "frame_height": int(geometry.frame_height),
            "content": [
                int(geometry.content_left),
                int(geometry.content_top),
                int(geometry.content_width),
                int(geometry.content_height),
            ],
            "scale_x": round(cls._finite_number(geometry.scale_x), 4),
            "scale_y": round(cls._finite_number(geometry.scale_y), 4),
            "client_scale": round(cls._finite_number(geometry.client_scale), 4),
            "aspect_ratio": round(cls._finite_number(geometry.aspect_ratio), 4),
            "effective_aspect_ratio": round(
                cls._finite_number(geometry.effective_aspect_ratio),
                4,
            ),
            "accepted": bool(geometry.accepted),
            "rejection_reasons": [
                str(value)[:SKILL_FAILURE_TEXT_LIMIT]
                for value in tuple(geometry.rejection_reasons)[:8]
            ],
        }

    @classmethod
    def _serialize_candidate(cls, candidate) -> dict[str, object]:
        result = getattr(candidate, "result", candidate)
        return {
            "position": [int(value) for value in result.position],
            "size": [int(value) for value in result.size],
            "center": [int(value) for value in result.center],
            "scale": round(cls._finite_number(getattr(result, "scale", 1.0)), 4),
            "interpolation": str(getattr(candidate, "interpolation", ""))[:32],
            "m": round(cls._finite_number(result.score), 4),
            "p": round(cls._finite_number(result.pixel_score), 4),
            "z": round(cls._finite_number(result.zncc_score), 4),
            "gradient": round(cls._finite_number(result.gradient_zncc_score), 4),
            "edge": round(cls._finite_number(result.edge_score), 4),
            "rejection_reasons": [
                str(value)[:SKILL_FAILURE_TEXT_LIMIT]
                for value in tuple(getattr(candidate, "rejection_reasons", ()))[:8]
            ],
        }

    def _read_count(
        self,
        action: SkillAction,
        detection: ActionIconDetection | None = None,
        *,
        ocr_scale: float | None = None,
        known_limit: int | None = None,
        expected_used: int | None = None,
        frame=None,
    ) -> tuple[int, int] | None:
        # The calibrated fixed ROI is intentionally reused at 3x after a 2x
        # miss.  This catches the reproduced dark ``2`` without widening the
        # region into adjacent UI text.
        scales = (ocr_scale,) if ocr_scale is not None else (
            (SKILL_OCR_UPSCALE, SKILL_OCR_FALLBACK_UPSCALE)
            if action.fixed_count_relative_roi is not None
            else (SKILL_OCR_UPSCALE,)
        )
        for index, scale in enumerate(scales):
            count_frame = self.vision.capture() if frame is None else frame
            if action.fixed_count_relative_roi is not None:
                text = self.vision.ocr_text(
                    count_frame,
                    f"{action.name}次数",
                    relative_roi=action.fixed_count_relative_roi,
                    target_height=1080,
                    ocr_scale=scale,
                )
            elif detection is not None:
                text = self.vision.ocr_text(
                    count_frame,
                    f"{action.name}次数",
                    relative_roi=self._action_text_relative_roi(
                        detection,
                        count_frame.shape,
                    ),
                    target_height=1080,
                    ocr_scale=scale,
                )
            else:
                return None
            count = parse_used_limit(text, known_limit, expected_used)
            if count is not None:
                return count
            if index + 1 < len(scales):
                # No long stability wait before the fallback; it is a
                # same-frame/next-frame best-effort read.
                continue
        return None

    def _counts_prove_menu(self, expected_icons: tuple[ActionIconSpec, ...]) -> bool:
        """通过各目标槽位的次数文本辅助确认采集技能组。

        明亮地面会干扰灰色技能图标；只有全部目标槽位均能读出次数才接受此证据。
        """
        actions = [COUNT_ACTIONS_BY_ICON.get(icon.name) for icon in expected_icons]
        if not actions or any(action is None for action in actions):
            return False
        try:
            readable = all(self._read_count(action) is not None for action in actions)
        except AttributeError:  # vision without OCR (tests, probes)
            return False
        if readable:
            self._status("技能组确认", "图标被背景干扰，以固定栏位次数确认技能栏")
            return True
        return False

    def _search_started_without_feedback(self) -> bool:
        """The short toast can be missed on a slow PC while 探查 did start:
        its countdown over the icon is the proof (never clicked again)."""
        end_at = monotonic() + SEARCH_MISSED_FEEDBACK_COUNTDOWN_SECONDS
        while True:
            if self._search_running():
                self.task.log_info("探查：没读到执行提示，但倒计时已出现，视为已开始。")
                return True
            if monotonic() >= end_at:
                return False
            self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)

    def _search_running(self) -> bool:
        text = self.vision.ocr_text(
            self.vision.capture(),
            "探查倒计时",
            relative_roi=SEARCH_COUNTDOWN_RELATIVE_ROI,
            target_height=1080,
            ocr_scale=SKILL_OCR_UPSCALE,
        )
        countdown = re.sub(r"\D", "", text)
        self._status("探查倒计时", countdown or "-")
        return bool(SEARCH_COUNTDOWN_PATTERN.fullmatch(countdown))

    def _absorb_used_up(
        self, *, card_id: str, map_role: CollectionMapRole
    ) -> SkillExecutionResult | None:
        """当前地图尚需吸收、但每日次数已耗尽时，停止本图后续技能。

        次数窗口无法确认时交由各技能检查；账本已有本图完成证据时继续剩余动作。
        """
        existing = self.progress.get_action_record(card_id, map_role, ABSORB_ACTION.name)
        if self.progress.weekly_action_complete(card_id, map_role, ABSORB_ACTION.name):
            return None
        if existing is not None and str(existing.get("state", "")) in LOCAL_DONE_STATES:
            return None
        if not self._open_skill_menu((ABSORB_ICON,), allow_group_one_recovery=True):
            return None
        count = self._read_count_window(ABSORB_ACTION).count
        if count is None or count[0] < count[1]:
            return None
        self.progress.reconcile_pending(ABSORB_ACTION.name, count)
        if self.progress.trusted_action_baseline(ABSORB_ACTION.name) != count:
            return None
        return SkillExecutionResult(
            False,
            True,
            f"吸收今日已用完（{count[0]}/{count[1]}），{map_role.label}不探查、不按召集/压制，留待次日",
        )

    def _ensure_search(self, *, card_id: str, map_role: CollectionMapRole):
        reused = self._reuse_recent_search(card_id, map_role)
        if reused is not None:
            return reused
        if not self._open_skill_menu((ABSORB_ICON,), allow_group_one_recovery=True):
            return SkillExecutionResult(False, message="未确认采集技能栏")
        if self._search_running():
            return None
        started = self._start_search(card_id=card_id, map_role=map_role, slot_fallback=True)
        if not isinstance(started, SkillExecutionResult):
            return started
        self.task.sleep(1.0)
        if self._search_running():
            return None
        return self._start_search(card_id=card_id, map_role=map_role, slot_fallback=True)

COUNT_ACTIONS_BY_ICON = {
    action.icon.name: action for action in (ABSORB_ACTION, SUMMON_ACTION, SUPPRESS_ACTION)
}

LOCAL_DONE_STATES = frozenset({"local_done", "pending", "settled", "preexisting_used"})
