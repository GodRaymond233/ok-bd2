from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass
from time import monotonic

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    SUBDUE_ICON,
    SUMMON_ICON,
    ActionIconDetection,
    ActionIconDetector,
    ActionIconSpec,
    ActionIconState,
)
from src.tasks.map_trade.card_status import CollectionCardSelectionOutcome
from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    DAILY_ABSORB_LIMIT,
    DAILY_SUBMAP_LIMIT,
    DAILY_SUMMON_LIMIT,
    DAILY_SUPPRESS_LIMIT,
    CollectionMapRole,
    CollectionResult,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.vision import Vision, parse_used_limit

SEARCH_COUNTDOWN_TIMEOUT = 3.0
SEARCH_COUNTDOWN_INTERVAL = 0.25
SEARCH_COUNTDOWN_PATTERN = re.compile(r"^\d{1,3}$")
ACTION_AFTER_CLICK_SECONDS = 0.0
ACTION_OCR_WINDOW_SAMPLES = 3
ACTION_OCR_WINDOW_INTERVAL = 0.25
ACTION_FEEDBACK_TIMEOUT = 3.0
ACTION_FEEDBACK_SUCCESS_DELAY_SECONDS = 0.8
ACTION_FEEDBACK_CHARACTER_RATIO = 0.80
SKILL_OCR_UPSCALE = 2.0
SKILL_OCR_FALLBACK_UPSCALE = 3.0
ACTION_ICON_DETECTION_SAMPLES = 3
ACTION_ICON_DETECTION_INTERVAL = 0.15
SKILL_FAILURE_EVIDENCE_LIMIT = 12
SKILL_FAILURE_TEXT_LIMIT = 320
UNSUPPORTED_COLLECTION_CARD_NUMBERS = frozenset({14})
SKILL_REFERENCE_SIZE = (1920, 1080)
SKILL_GROUP_SWITCH_SETTLE_SECONDS = 0.8


def _relative_reference_point(point: tuple[int, int]) -> tuple[float, float]:
    width, height = SKILL_REFERENCE_SIZE
    return point[0] / width, point[1] / height


def _relative_reference_roi(
    roi: tuple[int, int, int, int],
) -> tuple[float, float, float, float]:
    left, top, width, height = roi
    reference_width, reference_height = SKILL_REFERENCE_SIZE
    return (
        left / reference_width,
        top / reference_height,
        (left + width) / reference_width,
        (top + height) / reference_height,
    )


SKILL_GROUP_REFERENCE_POINTS = {
    1: (1671, 1011),
    2: (1749, 1011),
    3: (1824, 1011),
}
SKILL_GROUP_RELATIVE_POINTS = {
    group: _relative_reference_point(point)
    for group, point in SKILL_GROUP_REFERENCE_POINTS.items()
}
SKILL_FIXED_COUNT_REFERENCE_ROIS = {
    "吸收": (1498, 890, 66, 37),
    "召集": (1542, 790, 66, 33),
    "压制": (1645, 743, 75, 33),
}
SKILL_FIXED_COUNT_RELATIVE_ROIS = {
    name: _relative_reference_roi(roi)
    for name, roi in SKILL_FIXED_COUNT_REFERENCE_ROIS.items()
}
SEARCH_COUNTDOWN_REFERENCE_ROI = (1550, 969, 52, 44)
SEARCH_COUNTDOWN_RELATIVE_ROI = _relative_reference_roi(
    SEARCH_COUNTDOWN_REFERENCE_ROI
)
ACTION_FEEDBACK_REFERENCE_ROI = (735, 210, 1182 - 735, 270 - 210)
ACTION_FEEDBACK_RELATIVE_ROI = _relative_reference_roi(ACTION_FEEDBACK_REFERENCE_ROI)
ACTION_SUCCESS_FEEDBACK = {
    "探查": ("在秒内确认隐藏物品的位置",),
    "吸收": ("吸收周围的拾取物",),
    "召集": ("召集带奖励的战场怪物",),
    "压制": ("已制伏地图内所有的怪物",),
}
ACTION_FAILURE_FEEDBACK = {
    "吸收": ("周围没有可以吸收的拾取物",),
    "召集": ("无可召集的战场怪物",),
    "压制": ("没有可制伏的怪物",),
}


@dataclass(frozen=True)
class SkillAction:
    name: str
    icon: ActionIconSpec
    count_roi: tuple[int, int, int, int] | None = None
    fixed_count_relative_roi: tuple[float, float, float, float] | None = None

    @property
    def template(self) -> TemplateSpec:
        return self.icon.template


@dataclass(frozen=True)
class SkillExecutionResult:
    completed: bool
    depleted: bool = False
    message: str = ""
    # Local map success can be durable even while daily absolute OCR is
    # pending.  Keep the action names visible to the caller for status and
    # final-map warnings without changing the existing positional interface.
    pending_actions: tuple[str, ...] = ()
    # True only when the formal collector persisted every role-required
    # action record before returning success.  Legacy test/integration
    # shims can leave this false and explicitly use ProgressStore's
    # compatibility mark_target path.
    durable_actions: bool = False


@dataclass(frozen=True)
class SearchCountdownSession:
    relative_roi: tuple[float, float, float, float]
    value: int


@dataclass(frozen=True)
class SkillFeedbackObservation:
    text: str
    outcome: str | None
    ratio: float = 0.0
    keyword: str = ""


# The counter is positioned relative to the icon on the live client. Keeping
# a fixed 1920x1080 ROI here can miss it after UI scaling or when the skill
# row shifts, so all limited-action counters use the current match geometry.
SEARCH_ACTION = SkillAction(
    "探查",
    SEARCH_ICON,
    fixed_count_relative_roi=SEARCH_COUNTDOWN_RELATIVE_ROI,
)
ABSORB_ACTION = SkillAction(
    "吸收",
    ABSORB_ICON,
    fixed_count_relative_roi=SKILL_FIXED_COUNT_RELATIVE_ROIS["吸收"],
)
SUMMON_ACTION = SkillAction(
    "召集",
    SUMMON_ICON,
    fixed_count_relative_roi=SKILL_FIXED_COUNT_RELATIVE_ROIS["召集"],
)
SUPPRESS_ACTION = SkillAction(
    "压制",
    SUBDUE_ICON,
    fixed_count_relative_roi=SKILL_FIXED_COUNT_RELATIVE_ROIS["压制"],
)
BATTLE_ACTIONS = (ABSORB_ACTION, SUMMON_ACTION, SUPPRESS_ACTION)


class Collector:
    def __init__(
        self,
        task,
        vision: Vision,
        navigator: Navigator,
        progress: ProgressStore,
    ) -> None:
        self.task = task
        self.vision = vision
        self.navigator = navigator
        self.progress = progress
        self.action_icons = ActionIconDetector(vision)
        # Keep only a small in-memory replay tail.  It is emitted when a
        # recognition failure reaches the caller; normal successful runs do
        # not write images or grow an on-disk directory indefinitely.
        self._skill_failure_evidence: deque[dict[str, object]] = deque(
            maxlen=SKILL_FAILURE_EVIDENCE_LIMIT
        )
        self._last_skill_observations: dict[str, dict[str, object]] = {}
        self._group_one_recovery_attempted = False
        self._last_count_window_stable = False

    def run(self) -> CollectionResult:
        # A Collector instance can be reused by the task scheduler.  Recovery
        # is bounded once per formal run, while direct helper calls retain the
        # latch until the next run invocation.
        self._group_one_recovery_attempted = False
        state = self.progress.load()
        if state.depleted_today or state.daily_submaps >= DAILY_SUBMAP_LIMIT:
            return CollectionResult(True, depleted=True, message="今日采集技能额度已用尽")
        supported_cards = tuple(
            card
            for card in COLLECTABLE_CARDS
            if card.number not in UNSUPPORTED_COLLECTION_CARD_NUMBERS
        )
        if all(state.card_verified(card.card_id) for card in supported_cards):
            return CollectionResult(
                True,
                message="本周已支持剧情卡带均已采集并完成视觉复核",
            )

        completed_this_run = 0
        card_retries = max(1, int(self.task.config.get("卡带单步重试次数", 2)))
        for card in COLLECTABLE_CARDS:
            if card.number in UNSUPPORTED_COLLECTION_CARD_NUMBERS:
                self._status("跳过", f"{card.card_id}：第14章等待专用流程")
                self.task.log_warning("地图采集：第14章需要专用流程，本轮跳过且不写任何采集进度。")
                continue

            state = self.progress.state
            completed = state.completed_targets(card.card_id)
            if len(completed) >= len(card.targets):
                if state.card_verified(card.card_id):
                    continue
                verified = self.navigator.inspect_collection_card_completion(card.card_id)
                if not verified.success:
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message=(
                            f"{card.card_id}三张地图已有进度，但完成度复核失败："
                            f"{verified.message or '-'}"
                        ),
                    )
                self.progress.mark_card_verified(card.card_id)
                continue

            if not self._can_finish_card_today(card, completed):
                self.progress.mark_depleted_today()
                return CollectionResult(
                    True,
                    depleted=True,
                    completed_submaps=completed_this_run,
                    message="今日剩余吸取/召集/压制次数不足以安全完成下一张卡带",
                )

            selected = None
            for _attempt in range(card_retries):
                selected = self.navigator.select_collection_card(
                    card.card_id,
                    enter_visually_complete=False,
                )
                if selected.success:
                    break
            if selected is None or not selected.success:
                return CollectionResult(
                    False,
                    completed_submaps=completed_this_run,
                    message=f"未能进入卡带 {card.card_id}",
                )
            if selected.outcome == CollectionCardSelectionOutcome.VISUALLY_COMPLETE:
                self._status(
                    "卡带完成度",
                    f"{card.card_id}进入前确认吸取与压制均已完成，本轮跳过",
                )
                continue

            prepared = self.navigator.prepare_collection_main_area(card.card_id)
            if not prepared.success:
                return CollectionResult(
                    False,
                    completed_submaps=completed_this_run,
                    message=f"{card.card_id}安全区初始化失败：{prepared.message}",
                )

            search = self._start_search(map_role=CollectionMapRole.MAIN_AREA)
            if isinstance(search, SkillExecutionResult):
                return self._skill_failure(card.card_id, "安全区探查", search, completed_this_run)

            main_target, battle_one, battle_two = card.targets
            observed_depleted = False
            if main_target.key not in completed:
                self._status(
                    "采集进度",
                    f"{card.card_id} {main_target.role.label}：{main_target.title}",
                )
                main_result = self._use_actions(
                    (ABSORB_ACTION,),
                    card_id=card.card_id,
                    map_role=CollectionMapRole.MAIN_AREA,
                )
                if not main_result.completed:
                    return self._skill_failure(
                        card.card_id,
                        main_target.role.label,
                        main_result,
                        completed_this_run,
                    )
                self.progress.mark_target(
                    card.card_id,
                    main_target.key,
                    require_actions=main_result.durable_actions,
                )
                completed.add(main_target.key)
                completed_this_run += 1
                observed_depleted = main_result.depleted
                if observed_depleted and (
                    battle_one.key not in completed or battle_two.key not in completed
                ):
                    self.progress.mark_depleted_today()
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message=(
                            f"{card.card_id}{main_target.role.label}已完成，"
                            "但实机技能次数已到上限，剩余地图留待次日"
                        ),
                    )

            if battle_one.key not in completed or battle_two.key not in completed:
                arrived = self.navigator.advance_collection_map(
                    card.card_id,
                    main_target,
                    battle_one,
                )
                if not arrived.success:
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message=f"{card.card_id}进入战斗区域1失败：{arrived.message}",
                    )
                if not self._verify_search_countdown(search):
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message=f"{card.card_id}进入战斗区域1后探查倒计时未持续出现",
                    )

            if battle_one.key not in completed:
                self._status(
                    "采集进度",
                    f"{card.card_id} {battle_one.role.label}：{battle_one.title}",
                )
                battle_result = self._use_actions(
                    BATTLE_ACTIONS,
                    card_id=card.card_id,
                    map_role=CollectionMapRole.BATTLE_AREA_1,
                )
                if not battle_result.completed:
                    return self._skill_failure(
                        card.card_id,
                        battle_one.role.label,
                        battle_result,
                        completed_this_run,
                    )
                self.progress.mark_target(
                    card.card_id,
                    battle_one.key,
                    require_actions=battle_result.durable_actions,
                )
                completed.add(battle_one.key)
                completed_this_run += 1
                observed_depleted = observed_depleted or battle_result.depleted
                if observed_depleted and battle_two.key not in completed:
                    self.progress.mark_depleted_today()
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message=(
                            f"{card.card_id}{battle_one.role.label}已完成，"
                            "但实机技能次数已到上限，战斗区域2留待次日"
                        ),
                    )

            if battle_two.key not in completed:
                arrived = self.navigator.advance_collection_map(
                    card.card_id,
                    battle_one,
                    battle_two,
                )
                if not arrived.success:
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message=f"{card.card_id}进入战斗区域2失败：{arrived.message}",
                    )
                self._status(
                    "采集进度",
                    f"{card.card_id} {battle_two.role.label}：{battle_two.title}",
                )
                battle_result = self._use_actions(
                    BATTLE_ACTIONS,
                    card_id=card.card_id,
                    map_role=CollectionMapRole.BATTLE_AREA_2,
                )
                if not battle_result.completed:
                    return self._skill_failure(
                        card.card_id,
                        battle_two.role.label,
                        battle_result,
                        completed_this_run,
                    )
                self.progress.mark_target(
                    card.card_id,
                    battle_two.key,
                    require_actions=battle_result.durable_actions,
                )
                completed.add(battle_two.key)
                completed_this_run += 1
                observed_depleted = observed_depleted or battle_result.depleted

            reopened = self.navigator.open_story_quick_switcher_from_sandbox()
            if not reopened.success:
                return CollectionResult(
                    False,
                    completed_submaps=completed_this_run,
                    message=f"{card.card_id}完成后无法打开快速切换页：{reopened.message}",
                )
            verified = self.navigator.inspect_collection_card_completion(card.card_id)
            if not verified.success:
                return CollectionResult(
                    False,
                    completed_submaps=completed_this_run,
                    message=(f"{card.card_id}吸取/压制完成度复核失败：{verified.message or '-'}"),
                )
            self.progress.mark_card_verified(card.card_id)

            if observed_depleted and not self.progress.state.depleted_today:
                self.progress.mark_depleted_today()

            state = self.progress.state
            effective = self.progress.effective_daily_counts()
            pending = self.progress.pending_count()
            self._status(
                "每日技能进度",
                (
                    f"吸取 {effective['吸收']}/{DAILY_ABSORB_LIMIT}（本地{state.daily_absorbs}）；"
                    f"召集 {effective['召集']}/{DAILY_SUMMON_LIMIT}（本地{state.daily_summons}）；"
                    f"压制 {effective['压制']}/{DAILY_SUPPRESS_LIMIT}"
                    f"（本地{state.daily_suppressions}）"
                    + (f"；待对账{pending}条" if pending else "")
                ),
            )
            if state.depleted_today:
                return CollectionResult(
                    True,
                    depleted=True,
                    completed_submaps=completed_this_run,
                    message=(
                        "当前卡带已完成并通过复核；实机技能次数显示已到上限"
                        if observed_depleted
                        else "已完成今日7张卡带，达到每日21次吸取上限"
                    )
                    + (
                        f"；另有{self.progress.pending_count()}条动作次数待后续明亮帧对账"
                        if self.progress.pending_count()
                        else ""
                    ),
                )

        pending = self.progress.pending_count()
        return CollectionResult(
            True,
            completed_submaps=completed_this_run,
            message=(
                "本周已支持的可采集卡带已经处理完毕；第14章等待专用流程"
                + (f"；末图有{pending}条动作次数待后续明亮帧对账" if pending else "")
            ),
        )

    def _can_finish_card_today(self, card, completed: set[str]) -> bool:
        remaining = [target for target in card.targets if target.key not in completed]
        return self.progress.can_plan_collection(remaining)

    def _skill_failure(
        self,
        card_id: str,
        stage: str,
        result: SkillExecutionResult,
        completed_this_run: int,
    ) -> CollectionResult:
        self._record_skill_failure(card_id, stage, result)
        if result.depleted:
            self.progress.mark_depleted_today()
            return CollectionResult(
                True,
                depleted=True,
                completed_submaps=completed_this_run,
                message=result.message or f"{card_id}{stage}技能次数已用尽",
            )
        return CollectionResult(
            False,
            completed_submaps=completed_this_run,
            message=(
                f"{card_id}{stage}技能操作失败" + (f"：{result.message}" if result.message else "")
            ),
        )

    def _record_skill_failure(
        self,
        card_id: str,
        stage: str,
        result: SkillExecutionResult,
    ) -> None:
        """Retain bounded, replayable evidence for a final skill miss."""

        message = str(result.message or "")[:SKILL_FAILURE_TEXT_LIMIT]
        evidence = {
            "card": str(card_id),
            "phase": str(stage),
            "completed": bool(result.completed),
            "depleted": bool(result.depleted),
            "message": message,
            "observations": {
                name: dict(values)
                for name, values in self._last_skill_observations.items()
            },
        }
        self._skill_failure_evidence.append(evidence)
        try:
            self.task.log_warning(
                f"地图采集：技能识别失败证据（最近{len(self._skill_failure_evidence)}条）"
                f" {evidence}"
            )
        except AttributeError:
            pass

    @property
    def skill_failure_evidence(self) -> tuple[dict[str, object], ...]:
        """Return a copy of the bounded failure replay tail for diagnostics."""

        return tuple(dict(item) for item in self._skill_failure_evidence)

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
        map_role: CollectionMapRole | None = None,
    ) -> SearchCountdownSession | SkillExecutionResult:
        menu_confirmed = self._open_skill_menu(
            (SEARCH_ICON, ABSORB_ICON),
            allow_group_one_recovery=isinstance(map_role, CollectionMapRole),
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
        card_id: str | None = None,
        map_role: CollectionMapRole | None = None,
    ) -> SkillExecutionResult:
        menu_confirmed = self._open_skill_menu(
            tuple(action.icon for action in actions),
            allow_group_one_recovery=isinstance(map_role, CollectionMapRole),
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
                    result.durable_actions,
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
            bool(card_id and isinstance(map_role, CollectionMapRole)),
        )

    def _use_action(
        self,
        action: SkillAction,
        *,
        card_id: str | None = None,
        map_role: CollectionMapRole | None = None,
    ) -> SkillExecutionResult:
        formal = bool(card_id and isinstance(map_role, CollectionMapRole))
        existing = (
            self.progress.get_action_record(card_id, map_role, action.name)
            if formal
            else None
        )
        # A process restart may leave an ARMED/CLICKED intent.  Even when the
        # icon is bright again we must not click a second time; a later USED
        # frame can safely reconcile the intent instead.
        if formal and existing is not None:
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
        frame, detection = self._detect_action_icon(
            action.icon,
            require_used_stable=formal,
        )
        self._report_icon_detection(action, detection)
        if formal and existing is not None and existing_state in {
            "armed",
            "clicked",
            "blocked",
        }:
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

        if detection.state in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}:
            if detection.state is ActionIconState.ABSENT:
                return SkillExecutionResult(False, message=f"未识别到{action.name}图标")
            return SkillExecutionResult(False, message=f"{action.name}图标状态未知")

        # In the formal card flow, a stable dimmed icon is proof that this
        # map action was already consumed.  Complete locally without a click.
        # Compatibility helpers that omit ``card_id`` retain the historical
        # feedback-based path used by focused unit tests.
        if formal and detection.state is ActionIconState.USED:
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
                try:
                    checkpoint = self._read_count_window(
                        action,
                        detection,
                        allow_single=True,
                    )
                except TypeError:
                    checkpoint = self._read_count_window(action, detection)
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

        self._last_count_window_stable = False
        before = self._read_count_window(action, detection)
        if before is None:
            return SkillExecutionResult(False, message=f"{action.name}次数 OCR 失败")
        self._status(f"{action.name}次数", f"{before[0]}/{before[1]}")
        if formal:
            remaining_pending = self.progress.pending_count(action.name)
            if remaining_pending:
                checkpoint_stable = bool(self._last_count_window_stable)
                if not checkpoint_stable:
                    return SkillExecutionResult(
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
                return SkillExecutionResult(
                    False,
                    message=(
                        f"{action.name}仍有{remaining_pending}条待对账动作，"
                        "本次不执行新点击"
                    ),
                )
        if detection.state is ActionIconState.AVAILABLE and before[0] >= before[1]:
            return SkillExecutionResult(
                False,
                True,
                f"{action.name}次数已达到 {before[0]}/{before[1]}，当前地图未完成",
            )

        if formal and not self.progress.arm_action(
            card_id,
            map_role,
            action.name,
            baseline=before,
        ):
            return SkillExecutionResult(
                False,
                True,
                f"{action.name}每日额度或未决动作已阻止新点击",
            )

        self.vision.click_client(
            detection.match.center,
            frame.shape,
            after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        if formal:
            # CLICKED is durable only after the recognized-center click
            # returns successfully.  A crash/exception during the click
            # therefore leaves the pre-click ARMED intent for safe recovery.
            self.progress.mark_action_clicked(card_id, map_role, action.name)
        feedback = self._read_action_feedback(action)
        self._wait_after_feedback_match(action, feedback)
        post_frame, post_detection = self._detect_action_icon(
            action.icon,
            require_used_stable=formal,
        )
        self._report_icon_detection(action, post_detection)
        count_detection = post_detection if post_detection.present else detection
        self._last_count_window_stable = False
        try:
            after = self._read_count_window(
                action,
                count_detection,
                allow_single=True,
            )
        except TypeError:
            # Focused integrations may replace the legacy two-argument
            # helper; preserve that interface while retaining best-effort
            # semantics in the production implementation.
            after = self._read_count_window(action, count_detection)
        post_window_stable = bool(self._last_count_window_stable)
        icon_used = post_detection.state is ActionIconState.USED
        exact_single_increment = bool(
            after is not None
            and after[1] == before[1]
            and after[0] == before[0] + 1
        )
        if after is not None:
            self._status(f"{action.name}次数", f"{after[0]}/{after[1]}")
            if formal and post_window_stable:
                self.progress.reconcile_pending(action.name, after)

        # Explicit failure wins over a stale/bright post frame.  Absorb's
        # positive token is accepted only with a stable USED post state; a
        # negative token is never converted into success.
        if (
            not formal
            and detection.state is ActionIconState.USED
            and after is not None
            and after == before
            and icon_used
            and feedback.outcome == "failure"
        ):
            self._status(f"{action.name}状态", "当前地图已使用，失败反馈已确认")
            return SkillExecutionResult(True, after[0] >= after[1])

        if feedback.outcome == "failure":
            if formal:
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
            # Keep legacy OCR-tolerant behavior for frames whose positive
            # token is not yet fully calibrated, while still requiring a
            # meaningful positive-token overlap in formal runs.  The exact
            # ``吸收周围的拾取物`` token normally sets ``outcome=success``;
            # compatibility helpers may still pass short legacy text.
            overlap = self._feedback_character_ratio(
                feedback.text,
                ACTION_SUCCESS_FEEDBACK["吸收"][0],
            )
            feedback_success = (not formal) or overlap >= 0.50
            if feedback_success:
                self._status("吸收成功反馈待标定", feedback.text)
        if icon_used and feedback_success:
            pending = after is None
            if formal:
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

    def _use_skills(self) -> SkillExecutionResult:
        """Compatibility helper for focused skill tests; formal flow is role-specific."""

        search = self._start_search(map_role=CollectionMapRole.MAIN_AREA)
        if isinstance(search, SkillExecutionResult):
            return search
        return self._use_actions(
            (ABSORB_ACTION, SUMMON_ACTION),
            map_role=CollectionMapRole.MAIN_AREA,
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

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
