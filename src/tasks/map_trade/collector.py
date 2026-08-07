from __future__ import annotations

import re
from collections import Counter
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

SKILL_MENU_TEMPLATE = SEARCH_ICON.template
ABSORB_SKILL_TEMPLATE = ABSORB_ICON.template
SUMMON_SKILL_TEMPLATE = SUMMON_ICON.template
SKILL_NOTHING_TEMPLATE = TemplateSpec(
    "空技能", "image/Skill-Nothing.png", 0.72, roi=(900, 390, 180, 230)
)
SEARCH_COUNTDOWN_TIMEOUT = 3.0
SEARCH_COUNTDOWN_INTERVAL = 0.25
SEARCH_COUNTDOWN_PATTERN = re.compile(r"^\d{1,3}$")
SKILL_MENU_OPEN_RELATIVE_POINT = (1203 / 1280, 664 / 720)
ACTION_AFTER_CLICK_SECONDS = 0.0
ACTION_OCR_WINDOW_SAMPLES = 3
ACTION_OCR_WINDOW_INTERVAL = 0.25
ACTION_FEEDBACK_CHARACTER_RATIO = 0.80
UNSUPPORTED_COLLECTION_CARD_NUMBERS = frozenset({14})
SKILL_REFERENCE_SIZE = (1920, 1080)


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


# These are 1920x1080 calibrations supplied for the action bar. They are
# stored as relative coordinates so the fixed fallback remains resolution
# independent at runtime.
SKILL_FALLBACK_REFERENCE_POINTS = {
    "探查": (1575, 993),
    "吸收": (1529, 883),
    "召集": (1576, 782),
    "压制": (1682, 739),
}
SKILL_FALLBACK_POINTS = {
    name: _relative_reference_point(point)
    for name, point in SKILL_FALLBACK_REFERENCE_POINTS.items()
}
SKILL_FIXED_COUNT_REFERENCE_ROIS = {
    # These boxes cover the n/n text observed below each action in the
    # current 1920x1080 client. Normal recognition still derives a tighter
    # box from the matched icon; these are only used by the fixed fallback.
    "吸收": (1496, 884, 67, 59),
    "召集": (1525, 779, 103, 68),
    "压制": (1644, 734, 74, 59),
}
SKILL_FIXED_COUNT_RELATIVE_ROIS = {
    name: _relative_reference_roi(roi)
    for name, roi in SKILL_FIXED_COUNT_REFERENCE_ROIS.items()
}
SEARCH_COUNTDOWN_FALLBACK_REFERENCE_ROI = (1534, 940, 82, 140)
SEARCH_COUNTDOWN_FALLBACK_RELATIVE_ROI = _relative_reference_roi(
    SEARCH_COUNTDOWN_FALLBACK_REFERENCE_ROI
)
ACTION_FEEDBACK_REFERENCE_ROI = (735, 210, 1182 - 735, 270 - 210)
ACTION_FEEDBACK_RELATIVE_ROI = _relative_reference_roi(ACTION_FEEDBACK_REFERENCE_ROI)
ACTION_SUCCESS_FEEDBACK = {
    "探查": ("在秒内确认隐藏物品的位置",),
    "吸收": (),
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
    fallback_point: tuple[float, float] | None = None
    fixed_count_relative_roi: tuple[float, float, float, float] | None = None

    @property
    def template(self) -> TemplateSpec:
        return self.icon.template


@dataclass(frozen=True)
class SkillExecutionResult:
    completed: bool
    depleted: bool = False
    message: str = ""


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
    fallback_point=SKILL_FALLBACK_POINTS["探查"],
    fixed_count_relative_roi=SEARCH_COUNTDOWN_FALLBACK_RELATIVE_ROI,
)
ABSORB_ACTION = SkillAction(
    "吸收",
    ABSORB_ICON,
    fallback_point=SKILL_FALLBACK_POINTS["吸收"],
    fixed_count_relative_roi=SKILL_FIXED_COUNT_RELATIVE_ROIS["吸收"],
)
SUMMON_ACTION = SkillAction(
    "召集",
    SUMMON_ICON,
    fallback_point=SKILL_FALLBACK_POINTS["召集"],
    fixed_count_relative_roi=SKILL_FIXED_COUNT_RELATIVE_ROIS["召集"],
)
SUPPRESS_ACTION = SkillAction(
    "压制",
    SUBDUE_ICON,
    fallback_point=SKILL_FALLBACK_POINTS["压制"],
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

    def run(self) -> CollectionResult:
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

            search = self._start_search()
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
                    map_role=CollectionMapRole.MAIN_AREA,
                )
                if not main_result.completed:
                    return self._skill_failure(
                        card.card_id,
                        main_target.role.label,
                        main_result,
                        completed_this_run,
                    )
                self.progress.mark_target(card.card_id, main_target.key)
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
                    map_role=CollectionMapRole.BATTLE_AREA_1,
                )
                if not battle_result.completed:
                    return self._skill_failure(
                        card.card_id,
                        battle_one.role.label,
                        battle_result,
                        completed_this_run,
                    )
                self.progress.mark_target(card.card_id, battle_one.key)
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
                    map_role=CollectionMapRole.BATTLE_AREA_2,
                )
                if not battle_result.completed:
                    return self._skill_failure(
                        card.card_id,
                        battle_two.role.label,
                        battle_result,
                        completed_this_run,
                    )
                self.progress.mark_target(card.card_id, battle_two.key)
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
            self._status(
                "每日技能进度",
                (
                    f"吸取 {state.daily_absorbs}/{DAILY_ABSORB_LIMIT}；"
                    f"召集 {state.daily_summons}/{DAILY_SUMMON_LIMIT}；"
                    f"压制 {state.daily_suppressions}/{DAILY_SUPPRESS_LIMIT}"
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
                    ),
                )

        return CollectionResult(
            True,
            completed_submaps=completed_this_run,
            message="本周已支持的可采集卡带已经处理完毕；第14章等待专用流程",
        )

    def _can_finish_card_today(self, card, completed: set[str]) -> bool:
        state = self.progress.state
        remaining = [target for target in card.targets if target.key not in completed]
        battle_count = sum(
            target.role in {CollectionMapRole.BATTLE_AREA_1, CollectionMapRole.BATTLE_AREA_2}
            for target in remaining
        )
        return (
            state.daily_absorbs + len(remaining) <= DAILY_ABSORB_LIMIT
            and state.daily_summons + battle_count <= DAILY_SUMMON_LIMIT
            and state.daily_suppressions + battle_count <= DAILY_SUPPRESS_LIMIT
        )

    def _skill_failure(
        self,
        card_id: str,
        stage: str,
        result: SkillExecutionResult,
        completed_this_run: int,
    ) -> CollectionResult:
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

    def _open_skill_menu(
        self,
        expected_icons: tuple[ActionIconSpec, ...],
        *,
        allow_unconfirmed: bool = False,
    ) -> bool:
        def inspect(frame):
            detections = tuple(self.action_icons.detect(frame, icon) for icon in expected_icons)
            return detections, all(
                value.state not in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}
                for value in detections
            )

        frame = self.vision.capture()
        detections, opened = inspect(frame)
        if opened:
            return True
        self.task.operate_click(
            *SKILL_MENU_OPEN_RELATIVE_POINT,
            after_sleep=0.8,
        )
        frame = self.vision.capture()
        detections, opened = inspect(frame)
        if not opened:
            states = ", ".join(
                f"{icon.name}={value.state.value}"
                for icon, value in zip(expected_icons, detections, strict=True)
            )
            self.task.log_warning(f"地图采集：技能栏未确认：{states}。")
        if not opened and allow_unconfirmed:
            self.task.log_warning("地图采集：地图角色已确认，允许使用固定技能中心回退。")
            return True
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
    def _fixed_fallback_allowed(
        action: SkillAction,
        map_role: CollectionMapRole | None,
    ) -> bool:
        if map_role is None or action.fallback_point is None:
            return False
        if action.name == "探查":
            return map_role is CollectionMapRole.MAIN_AREA
        if action.name == "吸收":
            return map_role in {
                CollectionMapRole.MAIN_AREA,
                CollectionMapRole.BATTLE_AREA_1,
                CollectionMapRole.BATTLE_AREA_2,
            }
        return map_role in {
            CollectionMapRole.BATTLE_AREA_1,
            CollectionMapRole.BATTLE_AREA_2,
        }

    def _click_fixed_action(self, action: SkillAction) -> bool:
        if action.fallback_point is None:
            return False
        self._status(
            f"{action.name}图标",
            (
                "识别失败，使用已确认地图的固定中心回退；"
                f"relative=({action.fallback_point[0]:.6f},"
                f"{action.fallback_point[1]:.6f})"
            ),
        )
        self.task.operate_click(
            *action.fallback_point,
            after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        return True

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
        for attempt in range(ACTION_OCR_WINDOW_SAMPLES):
            text = self.vision.ocr_text(
                self.vision.capture(),
                f"{action.name}执行反馈",
                relative_roi=ACTION_FEEDBACK_RELATIVE_ROI,
                target_height=1080,
            )
            for outcome, keyword in keywords:
                ratio = self._feedback_character_ratio(text, keyword)
                if ratio > best.ratio:
                    best = SkillFeedbackObservation(text, outcome, ratio, keyword)
            if text and not best.text:
                best = SkillFeedbackObservation(text, None)
            if attempt + 1 < ACTION_OCR_WINDOW_SAMPLES:
                self.task.sleep(ACTION_OCR_WINDOW_INTERVAL)
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

    def _read_count_window(
        self,
        action: SkillAction,
        detection: ActionIconDetection | None = None,
    ) -> tuple[int, int] | None:
        samples: list[tuple[int, int]] = []
        for attempt in range(ACTION_OCR_WINDOW_SAMPLES):
            count = self._read_count(action, detection)
            if count is not None:
                samples.append(count)
            if attempt + 1 < ACTION_OCR_WINDOW_SAMPLES:
                self.task.sleep(ACTION_OCR_WINDOW_INTERVAL)
        if not samples:
            return None
        count, occurrences = Counter(samples).most_common(1)[0]
        if occurrences < 2:
            self._status(
                f"{action.name}次数窗口",
                f"不稳定：{samples}",
            )
            return None
        self._status(
            f"{action.name}次数窗口",
            f"稳定={count[0]}/{count[1]}；samples={samples}",
        )
        return count

    def _start_search(
        self,
        *,
        map_role: CollectionMapRole | None = CollectionMapRole.MAIN_AREA,
    ) -> SearchCountdownSession | SkillExecutionResult:
        menu_confirmed = self._open_skill_menu(
            (SEARCH_ICON, ABSORB_ICON),
            allow_unconfirmed=map_role is CollectionMapRole.MAIN_AREA,
        )
        if not menu_confirmed:
            return SkillExecutionResult(False, message="未确认安全区技能栏")
        frame = self.vision.capture()
        search_action = SEARCH_ACTION
        detection = self.action_icons.detect(frame, search_action.icon)
        self._report_icon_detection(search_action, detection)
        fallback_used = detection.state is not ActionIconState.AVAILABLE
        if fallback_used:
            if not self._fixed_fallback_allowed(search_action, map_role):
                return SkillExecutionResult(False, message="探查图标不可点击")
            self._click_fixed_action(search_action)
            countdown_roi = SEARCH_COUNTDOWN_FALLBACK_RELATIVE_ROI
        else:
            countdown_roi = self._action_text_relative_roi(detection, frame.shape)
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
        end_at = monotonic() + SEARCH_COUNTDOWN_TIMEOUT
        last_text = ""
        while monotonic() <= end_at:
            frame = self.vision.capture()
            post_detection = self.action_icons.detect(frame, search_action.icon)
            self._report_icon_detection(search_action, post_detection)
            last_text = self.vision.ocr_text(
                frame,
                "探查倒计时",
                relative_roi=countdown_roi,
                target_height=1080,
            )
            countdown = re.sub(r"\D", "", last_text)
            self._status("探查倒计时", countdown or "-")
            if (
                (
                    post_detection.state is ActionIconState.ABSENT
                    or fallback_used
                )
                and SEARCH_COUNTDOWN_PATTERN.fullmatch(countdown)
            ):
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
        map_role: CollectionMapRole | None = None,
    ) -> SkillExecutionResult:
        menu_confirmed = self._open_skill_menu(
            tuple(action.icon for action in actions),
            allow_unconfirmed=any(
                self._fixed_fallback_allowed(action, map_role) for action in actions
            ),
        )
        if not menu_confirmed:
            return SkillExecutionResult(False, message="未确认采集技能栏")
        frame = self.vision.capture()
        empty_match = self.vision.match(frame, SKILL_NOTHING_TEMPLATE)
        if self.vision.passes(empty_match, SKILL_NOTHING_TEMPLATE):
            return SkillExecutionResult(False, message="技能栏存在空技能")

        depleted = False
        for action in actions:
            result = self._use_action(action, map_role=map_role)
            if not result.completed:
                return SkillExecutionResult(False, depleted or result.depleted, result.message)
            depleted = depleted or result.depleted
        return SkillExecutionResult(True, depleted)

    def _use_action(
        self,
        action: SkillAction,
        *,
        map_role: CollectionMapRole | None = None,
    ) -> SkillExecutionResult:
        frame = self.vision.capture()
        detection = self.action_icons.detect(frame, action.icon)
        self._report_icon_detection(action, detection)
        if detection.state in {ActionIconState.ABSENT, ActionIconState.UNKNOWN}:
            if self._fixed_fallback_allowed(action, map_role):
                return self._use_action_fixed(action)
            if detection.state is ActionIconState.ABSENT:
                return SkillExecutionResult(False, message=f"未识别到{action.name}图标")
            return SkillExecutionResult(False, message=f"{action.name}图标状态未知")

        before = self._read_count_window(action, detection)
        if before is None:
            return SkillExecutionResult(False, message=f"{action.name}次数 OCR 失败")
        self._status(f"{action.name}次数", f"{before[0]}/{before[1]}")
        if detection.state is ActionIconState.AVAILABLE and before[0] >= before[1]:
            return SkillExecutionResult(
                False,
                True,
                f"{action.name}次数已达到 {before[0]}/{before[1]}，当前地图未完成",
            )

        self.vision.click_client(
            detection.match.center,
            frame.shape,
            after_sleep=ACTION_AFTER_CLICK_SECONDS,
        )
        feedback = self._read_action_feedback(action)
        post_frame = self.vision.capture()
        post_detection = self.action_icons.detect(post_frame, action.icon)
        self._report_icon_detection(action, post_detection)
        count_detection = post_detection if post_detection.present else detection
        after = self._read_count_window(action, count_detection)
        if after is None:
            return SkillExecutionResult(False, message=f"{action.name}次数 OCR 失败")
        self._status(f"{action.name}次数", f"{after[0]}/{after[1]}")
        count_increased = after[1] == before[1] and after[0] == before[0] + 1
        count_unchanged = after == before
        icon_used = post_detection.state is ActionIconState.USED
        feedback_success = feedback.outcome == "success"
        if action.name == "吸收" and feedback.outcome is None and feedback.text:
            feedback_success = True
            self._status("吸收成功反馈待标定", feedback.text)
        if count_increased and icon_used and feedback_success:
            return SkillExecutionResult(True, after[0] >= after[1])
        if (
            detection.state is ActionIconState.USED
            and count_unchanged
            and icon_used
            and feedback.outcome == "failure"
        ):
            self._status(f"{action.name}状态", "当前地图已使用，失败反馈已确认")
            return SkillExecutionResult(True, after[0] >= after[1])
        return SkillExecutionResult(
            False,
            after[0] >= after[1],
            (
                f"{action.name}执行证据不一致："
                f"count={before[0]}/{before[1]}->{after[0]}/{after[1]}, "
                f"icon={detection.state.value}->{post_detection.state.value}, "
                f"feedback={feedback.outcome or 'unknown'}, "
                f"text={feedback.text or '-'}"
            ),
        )

    def _use_action_fixed(self, action: SkillAction) -> SkillExecutionResult:
        """Click a known map-role action even when its icon identity is absent."""

        before = self._read_count_window(action)
        if before is not None:
            self._status(f"{action.name}次数", f"{before[0]}/{before[1]}（固定框点击前）")
        if not self._click_fixed_action(action):
            return SkillExecutionResult(False, message=f"{action.name}没有固定回退中心")
        feedback = self._read_action_feedback(action)
        post_frame = self.vision.capture()
        post_detection = self.action_icons.detect(post_frame, action.icon)
        self._report_icon_detection(action, post_detection)
        after = self._read_count_window(action)
        if after is None:
            return SkillExecutionResult(False, message=f"{action.name}次数 OCR 失败")
        self._status(f"{action.name}次数", f"{after[0]}/{after[1]}（固定框点击后）")
        if before is not None and before[0] >= before[1] and after[0] >= after[1]:
            return SkillExecutionResult(
                False,
                True,
                f"{action.name}固定框 OCR 显示次数已达到 {after[0]}/{after[1]}",
            )
        feedback_success = feedback.outcome == "success"
        if action.name == "吸收" and feedback.outcome is None and feedback.text:
            feedback_success = True
            self._status("吸收成功反馈待标定", feedback.text)
        if (
            before is not None
            and after[1] == before[1]
            and after[0] == before[0] + 1
            and post_detection.state is ActionIconState.USED
            and feedback_success
        ):
            return SkillExecutionResult(True, after[0] >= after[1])
        return SkillExecutionResult(
            False,
            after[0] >= after[1],
            (
                f"{action.name}固定框执行证据不一致："
                f"count={before or '-'}->{after}, "
                f"icon={post_detection.state.value}, "
                f"feedback={feedback.outcome or 'unknown'}, "
                f"text={feedback.text or '-'}"
            ),
        )

    def _use_skills(self) -> SkillExecutionResult:
        """Compatibility helper for focused skill tests; formal flow is role-specific."""

        search = self._start_search(map_role=CollectionMapRole.MAIN_AREA)
        if isinstance(search, SkillExecutionResult):
            return search
        return self._use_actions((ABSORB_ACTION, SUMMON_ACTION))

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
                f"bright={brightness}"
            ),
        )

    def _read_count(
        self,
        action: SkillAction,
        detection: ActionIconDetection | None = None,
    ) -> tuple[int, int] | None:
        for _attempt in range(2):
            frame = self.vision.capture()
            if action.count_roi is not None:
                text = self.vision.ocr_text(
                    frame,
                    f"{action.name}次数",
                    roi=action.count_roi,
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
            elif action.fixed_count_relative_roi is not None:
                text = self.vision.ocr_text(
                    frame,
                    f"{action.name}次数",
                    relative_roi=action.fixed_count_relative_roi,
                    target_height=1080,
                )
            else:
                return None
            count = parse_used_limit(text)
            if count is not None:
                return count
            self.task.sleep(0.25)
        return None

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
