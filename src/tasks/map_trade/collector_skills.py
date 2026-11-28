from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
from functools import partial
from time import monotonic

import cv2
import numpy as np

from src.interaction.BD2Interaction import InputTargetChanged
from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    SUBDUE_ICON,
    SUMMON_ICON,
    ActionIconDetection,
    ActionIconSpec,
    ActionIconState,
)
from src.tasks.map_trade.collector_constants import (
    ABSORB_ACTION,
    ABSORB_SEARCH_MARGIN_SECONDS,
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
    SEARCH_COUNTDOWN_INTERVAL,
    SEARCH_COUNTDOWN_PATTERN,
    SEARCH_COUNTDOWN_RELATIVE_ROI,
    SEARCH_COUNTDOWN_TIMEOUT,
    SEARCH_MISSED_FEEDBACK_COUNTDOWN_SECONDS,
    SEARCH_RENEW_TIMEOUT,
    SKILL_FAILURE_TEXT_LIMIT,
    SKILL_GROUP_RELATIVE_POINTS,
    SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SKILL_OCR_FALLBACK_UPSCALE,
    SKILL_OCR_UPSCALE,
    SUMMON_ACTION,
    SUMMON_ANIMATION_MIN_SECONDS,
    SUMMON_SETTLE_TIMEOUT,
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
from src.utils.image_utils import relative_roi_frame


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
                if (
                    self._recovery_suppression_enabled
                    and target.role is not CollectionMapRole.MAIN_AREA
                ):
                    self._suppress_after_retreat(
                        captured, card_id=card.card_id, map_role=target.role,
                    )
                    captured = self.vision.capture()
                frame = self.navigator.wait_for_collection_field(captured)
                if frame is not captured:
                    raise RuntimeError("地图复核后场景再次变化，停止采集技能操作")
        return frame, recovered

    def _suppress_after_retreat(self, frame, *, card_id, map_role):
        """Select group one after map confirmation, then verify an urgent cast locally."""
        action = SUPPRESS_ACTION
        existing = self.progress.get_action_record(card_id, map_role, action.name)
        early = self._existing_record_outcome(action, existing)
        if early is not None:
            if not early.completed:
                raise RuntimeError(early.message)
            return
        if self.progress.weekly_action_complete(card_id, map_role, action.name):
            return
        if self.navigator._black_frame(frame) or self.navigator._field_battle_visible(frame):
            raise RuntimeError("退战压制准备期间场景再次变化，停止采集")
        self._status("技能组切换", "目标地图已确认，切换第一组并优先压制")
        self.task.operate_click(*SKILL_GROUP_RELATIVE_POINTS[1], after_sleep=0.0)
        detections = []
        counts = []
        baseline = self.progress.trusted_action_baseline(action.name)
        unresolved = self.progress.has_unresolved_action(card_id, map_role, action.name)
        for _sample in range(ACTION_ICON_DETECTION_SAMPLES):
            frame = self.vision.capture()
            if self.navigator._black_frame(frame) or self.navigator._field_battle_visible(frame):
                raise RuntimeError("退战压制准备期间场景再次变化，停止采集")
            detection = self.action_icons.detect(frame, action.icon)
            if detection.state not in {ActionIconState.AVAILABLE, ActionIconState.USED}:
                detections.clear()
                counts.clear()
                continue
            detections.append(detection)
            if detection.state is ActionIconState.AVAILABLE and not unresolved:
                count = self._read_count(
                    action, detection, frame=frame, ocr_scale=SKILL_OCR_UPSCALE,
                    known_limit=baseline[1] if baseline else None,
                )
                # 新容量交叉核对普通与留白读数；已确认容量保留快速双帧路径。
                if baseline is None or (count is not None and count[1] != baseline[1]):
                    confirmed = self._read_count(
                        action, detection, frame=frame, ocr_scale=SKILL_OCR_FALLBACK_UPSCALE,
                        bordered=True,
                    )
                    if confirmed != count:
                        count = None
                counts.append(count)
            if len(detections) == 2:
                break
        if len(detections) != 2:
            raise RuntimeError("退战后第一组压制图标未稳定确认，未点击")
        first, last = detections
        if not self._action_detection_is_stable(first, last):
            raise RuntimeError("退战后压制图标未稳定确认，未点击")
        detection = self._stable_detection(last, 2)
        self._report_icon_detection(action, detection)
        result = self._resume_pending_intent(
            action, existing, detection, card_id=card_id, map_role=map_role,
            read_counts=False,
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
            observation=CountWindowObservation(
                count, count is not None,
                count is not None and (baseline is None or count[1] != baseline[1]),
            ),
        )
        if failure is not None:
            raise RuntimeError(failure.message)
        if not self.progress.arm_action(card_id, map_role, action.name, baseline=before):
            raise RuntimeError("退战压制已被每日额度或未决动作阻止")
        self._status("导航状态", "目标地图已确认，优先压制")
        try:
            self.vision.click_client(
                detection.match.center, frame.shape, after_sleep=ACTION_AFTER_CLICK_SECONDS,
            )
        except Exception as error:
            if getattr(error, "input_not_dispatched", False):
                self.progress.mark_action_unsent(card_id, map_role, action.name)
            raise
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
        self._skill_layout_checked = False

    def _check_collection_skill_layout(self) -> None:
        if self._skill_layout_checked:
            return
        expected = (SEARCH_ICON, ABSORB_ICON, SUMMON_ICON, SUBDUE_ICON)
        previous = ()
        for sample in range(2):
            frame, recovered = self._capture_skill_field()
            missing = tuple(
                icon for icon in expected
                if self.action_icons.detect(frame, icon).state is ActionIconState.ABSENT
            )
            conflicts = self.action_icons.layout_conflicts(frame, missing)
            if conflicts and conflicts == previous and not recovered:
                raise RuntimeError("采集技能栏位置不符：" + "；".join(conflicts))
            previous = conflicts
            if sample == 0:
                self.task.sleep(ACTION_ICON_DETECTION_INTERVAL)
        self._skill_layout_checked = True

    def _open_skill_menu(
        self,
        expected_icons: tuple[ActionIconSpec, ...],
        *,
        allow_group_one_recovery: bool = False,
        warn_on_failure: bool = True,
    ) -> bool:
        def inspect(frame):
            detections = tuple(self.action_icons.detect(frame, icon) for icon in expected_icons)
            return detections, all(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in detections
            )

        def inspect_window():
            best_detections = None
            partial_sample = None
            attempt = recoveries = 0
            while attempt < ACTION_ICON_DETECTION_SAMPLES:
                frame, recovered = self._capture_skill_field()
                if recovered:
                    recoveries += 1
                    if recoveries > 2:
                        raise RuntimeError("技能栏确认期间反复发生场景切换，停止采集")
                    best_detections = None
                    partial_sample = None
                    attempt = 0
                current, opened = inspect(frame)
                if opened:
                    return current, True, None
                if any(value.state in {ActionIconState.AVAILABLE, ActionIconState.USED}
                       for value in current):
                    partial_sample = (frame, current)
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
            opened = all(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in best_detections
            )
            return best_detections, opened, partial_sample

        def reject_partial(frame, current):
            reasons = []
            for icon, detection in zip(expected_icons, current, strict=True):
                action = SEARCH_ACTION if icon == SEARCH_ICON else COUNT_ACTIONS_BY_ICON[icon.name]
                self._report_icon_detection(action, detection)
                reason = detection.reason or "无详情"
                reasons.append(f"{icon.name}={detection.state.value}（{reason}）")
            log_failure("地图采集：技能栏部分识别，停止切组：" + "，".join(reasons))
            try:
                self.task.save_frame("collection_skill_menu_unconfirmed", frame)
            except OSError as error:
                self.task.log_warning(f"地图采集：技能栏诊断图保存失败：{error}")
            return False

        detections, opened, partial = inspect_window()
        if opened or self._counts_prove_menu(expected_icons):
            self._check_collection_skill_layout()
            return True

        log_failure = self.task.log_warning if warn_on_failure else self.task.log_info
        states = ", ".join(
            f"{icon.name}={value.state.value}"
            for icon, value in zip(expected_icons, detections, strict=True)
        )
        if partial is not None:
            return reject_partial(*partial)
        if not allow_group_one_recovery:
            log_failure(f"地图采集：技能栏未确认：{states}。")
            return False

        # A partial match is not evidence that the wrong group is selected.
        # Never click a fixed group center merely because one action template
        # blinked; recovery is reserved for an all-missing menu in a confirmed
        # story-map context.
        # 采集技能可能配置在任意技能组，逐组识别并停留在包含目标技能的一组。
        for group, point in sorted(SKILL_GROUP_RELATIVE_POINTS.items()):
            frame, _recovered = self._capture_skill_field()
            detections, opened = inspect(frame)
            if opened:
                self._check_collection_skill_layout()
                return True
            if any(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in detections
            ):
                return reject_partial(frame, detections)
            self._status("技能组切换", f"采集技能未显示，切换到技能组{group}")
            self._forget_skill_evidence()
            self.task.operate_click(*point, after_sleep=SKILL_GROUP_SWITCH_SETTLE_SECONDS)
            detections, opened, partial = inspect_window()
            if opened or self._counts_prove_menu(expected_icons):
                self._check_collection_skill_layout()
                return True
            if partial is not None:
                return reject_partial(*partial)
        states = ", ".join(
            f"{icon.name}={value.state.value}"
            for icon, value in zip(expected_icons, detections, strict=True)
        )
        log_failure(f"地图采集：三个技能组都没有找到采集技能：{states}。")
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

    @staticmethod
    def _feedback_failure_ratio(text: str, keyword: str) -> float:
        negative = re.search(r"没有|无可", keyword)
        if negative is None:
            return 0.0
        best = 0.0
        for sentence in re.split(r"[。.!！？?；;\r\n]+", text):
            compact = "".join(character for character in sentence if character.isalnum())
            for match in re.finditer(negative.group(), compact):
                start = max(0, match.start() - negative.start())
                end = match.start() + len(keyword) - negative.start() + 1
                best = max(best, SequenceMatcher(
                    None, keyword, compact[start:end], autojunk=False,
                ).ratio())
        return best

    def _sample_action_feedback(self, action, frame):
        text = self.vision.ocr_text(
            frame, f"{action.name}执行反馈", relative_roi=ACTION_FEEDBACK_RELATIVE_ROI,
            target_height=1080,
        )
        simplify = getattr(self.vision, "simplify", str)
        normalized = simplify(text)
        # 失败需在同一语句中匹配否定短语和词序，避免横幅堆叠时跨句凑字。
        failure = max(
            ((self._feedback_failure_ratio(normalized, word), word)
             for word in ACTION_FAILURE_FEEDBACK.get(action.name, ())), default=(0.0, ""),
        )
        success = max(
            (self._feedback_character_ratio(normalized, word), word)
            for word in ACTION_SUCCESS_FEEDBACK[action.name]
        )
        return text, failure, success

    def _read_action_feedback(self, action: SkillAction) -> SkillFeedbackObservation:
        best = SkillFeedbackObservation("", None)
        end_at = monotonic() + ACTION_FEEDBACK_TIMEOUT
        while True:
            text, best_failure, best_success = self._sample_action_feedback(
                action, self.vision.capture(),
            )
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
        require_fraction: bool = False,
    ) -> CountWindowObservation:
        """Return a compatible count; single-frame evidence requires allow_single explicitly."""
        baseline = self.progress.trusted_action_baseline(action.name)
        known_limit = expected_limit
        if known_limit is None:
            known_limit = baseline[1] if baseline is not None else None
        all_samples: list[tuple[int, int]] = []
        for ocr_scale in (SKILL_OCR_UPSCALE, SKILL_OCR_FALLBACK_UPSCALE):
            samples: list[tuple[int, int]] = []
            for attempt in range(ACTION_OCR_WINDOW_SAMPLES):
                count = self._read_count(
                    action, detection, ocr_scale=ocr_scale,
                    known_limit=known_limit,
                    require_fraction=require_fraction,
                )
                if count is not None:
                    samples.append(count)
                    all_samples.append(count)
                if attempt + 1 < ACTION_OCR_WINDOW_SAMPLES:
                    self.task.sleep(ACTION_OCR_WINDOW_INTERVAL)
            count, occurrences = Counter(samples).most_common(1)[0] if samples else (None, 0)
            compatible = count is not None and (
                expected_used is None or count[0] == expected_used
            ) and (expected_limit is None or count[1] == expected_limit)
            self._status(
                f"{action.name}次数窗口",
                f"{'稳定' if occurrences >= 2 else '不稳定'}={count}；"
                f"scale={ocr_scale}；samples={samples}；"
                f"expected={expected_used}/{expected_limit}；上次容量={known_limit}",
            )
            self._last_skill_observations.setdefault(action.name, {}).update(
                count_samples=list(samples), expected_count=(expected_used, expected_limit),
            )
            if (
                compatible and count[1] == known_limit
                and (baseline is None or count[0] >= baseline[0])
                and all(value[1] == known_limit for value in all_samples)
                and (
                    occurrences >= 2
                    or (ocr_scale == SKILL_OCR_FALLBACK_UPSCALE and allow_single)
                )
            ):
                return CountWindowObservation(count, occurrences >= 2)
            self._status(
                f"{action.name}次数校验",
                f"候选={count}；稳定={occurrences >= 2}；"
                f"预期={expected_used}/{expected_limit}；"
                + ("以3x新帧复读" if ocr_scale == SKILL_OCR_UPSCALE else "未取得可信读数"),
            )
        # A repeated wrong denominator survives resizing. Keep the calibrated
        # crop but change its surrounding background for an independent read.
        samples = []
        for _attempt in range(ACTION_OCR_WINDOW_SAMPLES):
            count = self._read_count(
                action, detection, ocr_scale=SKILL_OCR_FALLBACK_UPSCALE,
                known_limit=known_limit,
                require_fraction=require_fraction, bordered=True,
            )
            if count is not None:
                samples.append(count)
        count, occurrences = Counter(samples).most_common(1)[0] if samples else (None, 0)
        compatible = count is not None and (
            expected_used is None or count[0] == expected_used
        ) and (expected_limit is None or count[1] == expected_limit)
        consistent = occurrences >= 2 and len(set(samples)) == 1
        confirmed = consistent and all_samples.count(count) >= 2 and set(all_samples) == {count}
        self._status(
            f"{action.name}次数校验",
            f"背景留白复读={count}；samples={samples}；容量确认={confirmed}",
        )
        if compatible and consistent and (count[1] == known_limit or confirmed):
            return CountWindowObservation(count, True, confirmed)
        return CountWindowObservation(None, False)

    def _refresh_collection_quota(self, actions: tuple[SkillAction, ...]):
        """旧额度不足时，入场复读HUD，避免技能升级后仍按旧容量跳过。"""
        self._quota_checks.update(action.name for action in actions)
        if not self._open_skill_menu(
            tuple(action.icon for action in actions), allow_group_one_recovery=True,
        ):
            return
        for action in actions:
            observation = self._read_count_window(action)
            if observation.count is not None and observation.is_stable:
                failure = self._check_count_epoch(action, observation)
                if failure is not None:
                    return failure
                self.progress.reconcile_pending(
                    action.name, observation.count, confirmed_limit=observation.limit_confirmed,
                )

    def _reuse_recent_search(
        self,
        card_id: str | None,
        map_role: CollectionMapRole,
    ) -> SearchCountdownSession | None:
        """复用本次施放实际读到的有效期；吸收提交仍须新帧OCR确认。"""

        if card_id is None:
            return None
        key = (card_id, map_role.value)
        expires_at = self._search_expires_at.get(key)
        if expires_at is None:
            return None
        if expires_at <= monotonic():
            self._search_expires_at.pop(key, None)
            return None
        frame, detection = self._detect_action_icon(SEARCH_ACTION.icon)
        remaining = int(expires_at - monotonic())
        if detection.state is ActionIconState.AVAILABLE or remaining < ABSORB_SEARCH_MARGIN_SECONDS:
            self._search_expires_at.pop(key, None)
            return None
        self._status(
            "探查倒计时",
            f"复用本次实读有效期；预估剩余{remaining}s（吸收前复核）",
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
    ) -> SearchCountdownSession | SkillExecutionResult:
        reused = self._reuse_recent_search(card_id, map_role)
        if reused is not None:
            return reused
        menu_confirmed = self._open_skill_menu(
            (SEARCH_ICON, ABSORB_ICON),
            allow_group_one_recovery=True,
            warn_on_failure=False,
        )
        if not menu_confirmed:
            return SkillExecutionResult(
                False, message="未确认采集技能栏，请检查1号探查、2号吸收及当前技能组",
            )
        search_action = SEARCH_ACTION
        frame, detection = self._detect_action_icon(
            search_action.icon,
            require_stable=True,
        )
        self._report_icon_detection(search_action, detection)
        can_click_search = detection.state is ActionIconState.AVAILABLE and detection.stable
        if not can_click_search:
            if detection.state is ActionIconState.ABSENT:
                message = "未识别到1号槽探查，请配置探查后重新运行"
            elif not detection.stable:
                message = "探查图标未达到跨帧稳定确认"
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
        while monotonic() <= end_at:
            observed_at = monotonic()
            frame = self.vision.capture()
            # The normal search glyph is covered by the countdown digits as
            # soon as the action starts.  Its absence (or a transient
            # ``unknown`` state) is expected and must not veto the fixed OCR
            # countdown evidence.
            countdown = self._read_search_countdown(frame)
            if countdown is not None:
                if card_id is not None:
                    self._search_expires_at[(card_id, map_role.value)] = observed_at + countdown - 1
                return SearchCountdownSession(countdown_roi, countdown)
            self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)
        return SkillExecutionResult(
            False,
            message="探查点击后未确认有效倒计时",
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
            if recovery_only:
                execute = partial(self._use_action, action, recovery_only=True)
            for preparation in range(3):
                result = execute(card_id=card_id, map_role=map_role)
                if not result.press_again or recovery_only or preparation == 2:
                    break
                self.task.sleep(PRESS_AGAIN_PAUSE_SECONDS)
            if not result.completed:
                return SkillExecutionResult(
                    False,
                    result.depleted,
                    result.message,
                    tuple(pending_actions) + result.pending_actions,
                    review_required=result.review_required,
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
        recovery_only: bool = False,
    ) -> SkillExecutionResult:
        prepared = self._prepare_action(action, card_id=card_id, map_role=map_role)
        if isinstance(prepared, SkillExecutionResult):
            return prepared
        if recovery_only:
            return SkillExecutionResult(
                False, message=f"{action.name}只读复核尚未结算，未执行补点", review_required=True,
            )
        failure = self._click_prepared_action(action, prepared, card_id=card_id, map_role=map_role)
        if failure is not None:
            return failure
        feedback = self._read_action_feedback(action)
        self._wait_after_feedback_match(action, feedback)
        return self._verify_clicked_action(
            action, prepared, feedback, card_id=card_id, map_role=map_role,
        )

    def _wait_summon_settled(self, clicked_at):
        """Observe the cast and its banner together, within one post-click budget."""
        feedback = SkillFeedbackObservation("", None)
        clear_frames = 0
        deadline = clicked_at + SUMMON_SETTLE_TIMEOUT
        while monotonic() < deadline:
            sampled_at = monotonic()
            frame, recovered = self._capture_skill_field()
            if recovered:
                return feedback, SkillExecutionResult(
                    False, message="召集期间遭遇战已恢复，重新核验本图动作", press_again=True,
                )
            if monotonic() >= deadline:
                break
            text, failure, success = self._sample_action_feedback(SUMMON_ACTION, frame)
            if failure[0] >= ACTION_FEEDBACK_CHARACTER_RATIO:
                return SkillFeedbackObservation(text, "failure", *failure), None
            if success[0] > feedback.ratio:
                outcome = "success" if success[0] >= ACTION_FEEDBACK_CHARACTER_RATIO else None
                feedback = SkillFeedbackObservation(text, outcome, *success)
            elapsed = sampled_at - clicked_at
            if (elapsed >= SUMMON_ANIMATION_MIN_SECONDS
                    and success[0] < ACTION_FEEDBACK_RELEVANCE_RATIO):
                clear_frames += 1
            else:
                clear_frames = 0
            self._status(
                "召集演出",
                f"点击后={elapsed:.2f}s；横幅匹配={success[0]:.3f}；退场={clear_frames}/2",
            )
            remaining = deadline - monotonic()
            if clear_frames >= 2 and remaining >= 0:
                return feedback, None
            if remaining > 0:
                self.task.sleep(min(ACTION_OCR_WINDOW_INTERVAL, remaining))
        return feedback, SkillExecutionResult(
            False, message="召集演出退场超时，跳过本卡压制", review_required=True,
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
            if isinstance(suppress, SkillExecutionResult):
                if not suppress.completed:
                    return suppress
                _before, failure = self._prepare_before_click(SUPPRESS_ACTION, None, **kwargs)
                if failure is not None:
                    return failure
            failure = self._click_prepared_action(SUMMON_ACTION, summon, **kwargs)
            if failure is not None:
                return failure
            clicked_at = monotonic()
            feedback, stop = self._wait_summon_settled(clicked_at)
            self._status(
                "召集执行反馈", f"outcome={feedback.outcome or 'unknown'}; "
                f"ratio={feedback.ratio:.3f}; text={feedback.text or '-'}",
            )
            if stop is not None and stop.press_again:
                return stop
            if feedback.outcome != "success" or stop is not None:
                summon_result = self._verify_clicked_action(
                    SUMMON_ACTION, summon, feedback, **kwargs,
                )
                if not summon_result.completed:
                    return summon_result
                if stop is not None:
                    return stop
            if isinstance(suppress, SkillExecutionResult):
                self.progress.review_card_actions(
                    card_id, (SUPPRESS_ACTION.name,), roles={map_role.value},
                )
                suppress_result = self._use_action(SUPPRESS_ACTION, **kwargs)
            else:
                failure = self._click_prepared_action(SUPPRESS_ACTION, suppress, **kwargs)
                if failure is not None:
                    # A changed scene invalidates preparation, but has not armed this action.
                    suppress_result = (
                        self._use_action(SUPPRESS_ACTION, **kwargs)
                        if failure.press_again else failure
                    )
                else:
                    self._status("召集到压制点击间隔", f"{monotonic() - clicked_at:.2f}s")
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
            review_required=summon_result.review_required or suppress_result.review_required,
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
        if self.progress.action_needs_review(card_id, map_role, action.name):
            if not self.progress.prepare_supplement(card_id, map_role, action.name):
                return SkillExecutionResult(
                    False, message=f"{action.name}原施放仍待对账，未执行补采",
                )
        return detection, before, self._skill_field_generation

    def _click_prepared_action(self, action, prepared, *, card_id, map_role):
        detection, before, generation = prepared
        observed_at = monotonic()
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
        if action is ABSORB_ACTION:
            seconds = self._read_search_countdown(frame)
            search = self.action_icons.detect(frame, SEARCH_ICON)
            remaining = (seconds or 0) - (monotonic() - observed_at)
            if (
                search.state is ActionIconState.AVAILABLE
                or remaining < ABSORB_SEARCH_MARGIN_SECONDS
            ):
                return self._renew_search_for_absorb(card_id=card_id, map_role=map_role)
        retrying = existing is not None and existing.get("state") in {"armed", "clicked", "blocked"}
        if retrying:
            observation = self._read_count_window(action, fresh, require_fraction=True)
            if not observation.is_stable or observation.count != before:
                return SkillExecutionResult(
                    False, message="补点前次数变化，保留原意图等待复核", review_required=True,
                )
            frame, recovered = self._capture_skill_field()
            fresh = self.action_icons.detect(frame, action.icon)
            if recovered or not self._action_detection_is_stable(detection, fresh):
                return SkillExecutionResult(False, message="补点前现场变化", press_again=True)
            if action is ABSORB_ACTION and (
                self._read_search_countdown(frame) or 0
            ) < ABSORB_SEARCH_MARGIN_SECONDS:
                return self._renew_search_for_absorb(card_id=card_id, map_role=map_role)
            armed = bool(self.task.config.get("未决吸收补点测试", False)) and (
                self.progress.arm_action_retry(card_id, map_role, action.name, before)
            )
        else:
            armed = self.progress.arm_action(card_id, map_role, action.name, baseline=before)
        if not armed:
            return SkillExecutionResult(
                False, message=f"{action.name}额度或补点预算不足，当前动作待核验",
                review_required=True,
            )
        try:
            self.vision.click_client(
                fresh.match.center,
                frame.shape,
                after_sleep=ACTION_AFTER_CLICK_SECONDS,
            )
        except Exception as error:
            if getattr(error, "input_not_dispatched", False):
                self.progress.mark_action_unsent(card_id, map_role, action.name)
            if isinstance(error, InputTargetChanged):
                return SkillExecutionResult(
                    False, message="输入等待后重新定位技能，消费尚未发送", press_again=True,
                )
            raise
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
        if existing.get("needs_review"):
            return None
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
        read_counts: bool = True,
    ) -> SkillExecutionResult | None:
        if existing is None:
            return None
        existing_state = str(existing.get("state", existing.get("status", "")))
        if existing_state not in {"armed", "clicked", "blocked"}:
            return None
        if read_counts and self._collection_target is not None:
            card, target = self._collection_target
            if (
                card.card_id != card_id or target.role != map_role
                or self.navigator.current_collection_target(card) != target.key
            ):
                return SkillExecutionResult(
                    False, message=f"{action.name}未决复核时目标地图不符", review_required=True,
                )
        if detection.state is ActionIconState.USED and detection.stable:
            self.progress.mark_action_local_done(
                card_id, map_role, action.name, pending=True,
            )
            return SkillExecutionResult(
                True,
                message="稳定已使用状态已确认；次数待后续明亮帧对账",
                pending_actions=(action.name,),
            )
        observation = CountWindowObservation(None, False)
        if read_counts and detection.stable and detection.state is ActionIconState.AVAILABLE:
            observation = self._read_count_window(action, detection, require_fraction=True)
            if observation.count is not None and observation.is_stable:
                failure = self._check_count_epoch(action, observation)
                if failure is not None:
                    return failure
                if self.progress.get_action_record(card_id, map_role, action.name) is None:
                    return None
                if self.progress.recover_action_increment(
                    card_id, map_role, action.name, observation.count,
                ):
                    return SkillExecutionResult(True, message="旧意图的唯一增量已确认，未重新点击")
        if (
            action is ABSORB_ACTION
            and bool(self.task.config.get("未决吸收补点测试", False))
            and detection.state is ActionIconState.AVAILABLE and detection.stable
            and observation.is_stable and observation.count is not None
            and existing.get("baseline") == list(observation.count)
            and not existing.get("retry_used") and existing.get("attempts") == 1
            and self.progress.can_reserve_action(action.name)
        ):
            self._status(f"{action.name}状态", "原次数稳定未变，补点前再次核验现场和额度")
            return None
        count_text = (
            f"{observation.count[0]}/{observation.count[1]}"
            if observation.count is not None else "未读清"
        )
        baseline = existing.get("baseline")
        baseline_text = (
            f"{baseline[0]}/{baseline[1]}"
            if isinstance(baseline, (list, tuple)) and len(baseline) == 2 else "未知"
        )
        message = (
            f"{action.name}旧点击待核验；点击前={baseline_text}，当前次数={count_text}，"
            "保留预留并跳过本卡；次日额度重置后重新核验"
        )
        self.progress.mark_action_blocked(
            card_id,
            map_role,
            action.name,
            message,
        )
        return SkillExecutionResult(
            False,
            message=message, review_required=True,
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
        detection: ActionIconDetection | None,
        *,
        card_id: str,
        map_role: CollectionMapRole,
        observation: CountWindowObservation | None = None,
    ) -> tuple[tuple[int, int] | None, SkillExecutionResult | None]:
        fresh_pending = (
            action.name not in self._count_actions_seen
            and self.progress.pending_count(action.name) > 0
        )
        if observation is None or fresh_pending:
            observation = self._read_count_window(
                action, detection, require_fraction=True,
            )
        elif (
            observation.count is None or not observation.is_stable
        ):
            observation = CountWindowObservation(None, False)
        before = observation.count
        if before is None:
            return None, SkillExecutionResult(
                False,
                message=f"{action.name}未取得可信次数，未执行新点击",
            )
        self._status(f"{action.name}次数", f"{before[0]}/{before[1]}")
        failure = self._check_count_epoch(action, observation)
        if failure is not None:
            return None, failure
        settled = self.progress.reconcile_pending(
            action.name, before, confirmed_limit=observation.limit_confirmed,
        )
        if settled:
            self._status(
                f"{action.name}次数对账",
                f"明亮帧结算 {settled} 条待对账动作",
            )
        remaining_pending = self.progress.pending_count(action.name)
        if remaining_pending and not self.progress.can_reserve_action(action.name):
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
        if before[0] >= before[1]:
            return None, SkillExecutionResult(
                False,
                True,
                f"{action.name}次数已达到 {before[0]}/{before[1]}，当前地图未完成",
            )
        self._count_actions_seen.add(action.name)
        return before, None

    def _check_count_epoch(self, action, observation):
        before = observation.count
        previous = self.progress.trusted_action_baseline(action.name)
        if previous is not None and before[0] < previous[0]:
            token = (
                self.progress.request_count_reset(action.name, before)
                if observation.limit_confirmed else ""
            )
            confirmation = str(self.task.config.get("次数重置确认码", "")).strip()
            if not token or not self.progress.confirm_count_reset(
                action.name, before, confirmation,
            ):
                message = (
                    f"{action.name}次数由{previous[0]}/{previous[1]}变为{before[0]}/{before[1]}，"
                    "需确认技能升级或重置"
                    + (f"；确认后在跑图设置填写次数重置确认码：{token}"
                       if token else "；请复核技能与账号")
                )
                self._status("次数周期核验", message)
                return SkillExecutionResult(False, message=message)
            self._status("次数周期核验", f"{action.name}已按确认建立新周期，历史施放保留")
        return None

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
                pending = bool(self.progress.get_action_record(
                    card_id, map_role, action.name,
                ).get("pending"))
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
                self.progress.mark_action_blocked(
                    card_id, map_role, action.name,
                    "点击已发送，图标仍亮且暂未见次数变化；保留未知结果",
                )
                return SkillExecutionResult(
                    False,
                    message=f"{action.name}稳定读到{before[0]}/{before[1]}，未见增量，待核验",
                    press_again=bool(action is ABSORB_ACTION and self.task.config.get(
                        "未决吸收补点测试", False,
                    )),
                    review_required=True,
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
        require_fraction: bool = False,
        bordered: bool = False,
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
                relative_roi = action.fixed_count_relative_roi
            elif detection is not None:
                relative_roi = self._action_text_relative_roi(
                    detection, count_frame.shape,
                )
            else:
                return None
            if bordered:
                _left, _top, crop = relative_roi_frame(count_frame, relative_roi)
                if not crop.size:
                    return None
                color = np.median(crop, axis=(0, 1)).tolist()
                margin = max(1, crop.shape[0] // 2)
                count_frame = cv2.copyMakeBorder(
                    crop, margin, margin, margin, margin,
                    cv2.BORDER_CONSTANT, value=color,
                )
                relative_roi = None
            text = self.vision.ocr_text(
                count_frame, f"{action.name}次数", relative_roi=relative_roi,
                target_height=1080, ocr_scale=scale,
            )
            self._status(f"{action.name}原始次数", text or "未读清")
            self._last_skill_observations.setdefault(action.name, {})["raw_count"] = text[:80]
            if require_fraction and not re.fullmatch(r"\s*\d+\s*[/YyVv]\s*\d+\s*", text):
                continue
            # 完整分数携带当前技能容量；旧容量只帮助恢复丢失的分隔符。
            count = parse_used_limit(text, expected_used=expected_used)
            if count is None:
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

    def _read_search_countdown(self, frame) -> int | None:
        text = self.vision.ocr_text(
            frame,
            "探查倒计时",
            relative_roi=SEARCH_COUNTDOWN_RELATIVE_ROI,
            target_height=1080,
            ocr_scale=SKILL_OCR_UPSCALE,
        )
        countdown = text.strip()
        self._status("探查倒计时", countdown or "-")
        match = SEARCH_COUNTDOWN_PATTERN.fullmatch(countdown)
        if match and (seconds := int(match.group(1))) > 0:
            return seconds
        return None

    def _search_running(self) -> bool:
        return self._read_search_countdown(self.vision.capture()) is not None

    def _renew_search_for_absorb(self, *, card_id, map_role) -> SkillExecutionResult:
        """等低余量探查结束并重开；重开后丢弃旧吸收准备，重新核对次数和图标。"""
        self._search_expires_at.pop((card_id, map_role.value), None)
        self._status("吸收下一步点击", "探查剩余时间未确认充足，等待恢复后重新准备吸收")
        end_at = monotonic() + SEARCH_RENEW_TIMEOUT
        while monotonic() < end_at:
            observed_at = monotonic()
            frame, _recovered = self._capture_skill_field()
            seconds = self._read_search_countdown(frame)
            search = self.action_icons.detect(frame, SEARCH_ICON)
            remaining = (seconds or 0) - (monotonic() - observed_at)
            if monotonic() >= end_at:
                break
            if search.state is ActionIconState.AVAILABLE:
                started = self._start_search(card_id=card_id, map_role=map_role)
                if isinstance(started, SkillExecutionResult):
                    return SkillExecutionResult(
                        False, message=f"吸收前重新探查失败，未点击吸收：{started.message}",
                    )
                return SkillExecutionResult(
                    False, message="探查已重新施放，重新准备吸收", press_again=True,
                )
            if remaining >= ABSORB_SEARCH_MARGIN_SECONDS:
                return SkillExecutionResult(
                    False, message="探查剩余时间已复核，重新准备吸收", press_again=True,
                )
            self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)
        return SkillExecutionResult(False, message="吸收前未确认探查有效剩余时间，未点击吸收")

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
        # 倒计时会覆盖探查图标，短暂漏读须先复核，避免误进入补施探查流程。
        for attempt in range(ACTION_OCR_WINDOW_SAMPLES):
            if self._search_running():
                return None
            if attempt + 1 < ACTION_OCR_WINDOW_SAMPLES:
                self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)
        now = monotonic()
        if any(
            cached_card == card_id and expires_at > now
            for (cached_card, _role), expires_at in self._search_expires_at.items()
        ):
            self._status("探查倒计时", "近期探查倒计时漏读，等待新帧确认或图标恢复")
            end_at = now + SEARCH_RENEW_TIMEOUT
            while monotonic() < end_at:
                observed_at = monotonic()
                frame, _recovered = self._capture_skill_field()
                seconds = self._read_search_countdown(frame)
                search = self.action_icons.detect(frame, SEARCH_ICON)
                remaining = (seconds or 0) - (monotonic() - observed_at)
                if monotonic() >= end_at:
                    break
                if search.state is ActionIconState.AVAILABLE:
                    return self._start_search(card_id=card_id, map_role=map_role)
                if remaining >= ABSORB_SEARCH_MARGIN_SECONDS:
                    return SearchCountdownSession(SEARCH_COUNTDOWN_RELATIVE_ROI, int(remaining))
                self.task.sleep(SEARCH_COUNTDOWN_INTERVAL)
            return SkillExecutionResult(
                False, message="近期探查倒计时持续漏读且图标未恢复，未执行吸收",
            )
        started = self._start_search(card_id=card_id, map_role=map_role)
        if not isinstance(started, SkillExecutionResult):
            return started
        self.task.sleep(1.0)
        if self._search_running():
            return None
        return self._start_search(card_id=card_id, map_role=map_role)

COUNT_ACTIONS_BY_ICON = {
    action.icon.name: action for action in (ABSORB_ACTION, SUMMON_ACTION, SUPPRESS_ACTION)
}

LOCAL_DONE_STATES = frozenset({"local_done", "pending", "settled", "preexisting_used"})
