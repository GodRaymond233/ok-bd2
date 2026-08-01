from __future__ import annotations

from dataclasses import dataclass

from src.tasks.map_trade.action_icons import (
    ABSORB_ICON,
    SEARCH_ICON,
    SUMMON_ICON,
    ActionIconDetection,
    ActionIconDetector,
    ActionIconSpec,
    ActionIconState,
)
from src.tasks.map_trade.card_status import CollectionCardSelectionOutcome
from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    DAILY_SUBMAP_LIMIT,
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


@dataclass(frozen=True)
class SkillAction:
    name: str
    icon: ActionIconSpec
    count_roi: tuple[int, int, int, int]

    @property
    def template(self) -> TemplateSpec:
        return self.icon.template


@dataclass(frozen=True)
class SkillExecutionResult:
    completed: bool
    depleted: bool = False
    message: str = ""


SKILL_ACTIONS = (
    SkillAction("探查", SEARCH_ICON, (958, 645, 82, 55)),
    SkillAction("吸收", ABSORB_ICON, (930, 548, 85, 55)),
    SkillAction("召集", SUMMON_ICON, (960, 465, 92, 55)),
)


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
        weekly_target_count = sum(len(card.targets) for card in COLLECTABLE_CARDS)
        if state.weekly_submap_count >= weekly_target_count:
            return CollectionResult(
                True,
                message=f"本周 {weekly_target_count} 张目标地图已经全部完成",
            )

        completed_this_run = 0
        consecutive_card_failures = 0
        card_retries = max(1, int(self.task.config.get("卡带单步重试次数", 2)))
        for card in COLLECTABLE_CARDS:
            completed = state.completed_targets(card.card_id)
            if len(completed) >= len(card.targets):
                continue
            if state.depleted_today or state.daily_submaps >= DAILY_SUBMAP_LIMIT:
                self.progress.mark_depleted_today()
                return CollectionResult(
                    True,
                    depleted=True,
                    completed_submaps=completed_this_run,
                    message="达到每日 21 个小图保护上限",
                )

            selected = None
            for _attempt in range(card_retries):
                selected = self.navigator.select_collection_card(card.card_id)
                if selected.success:
                    break
            if selected is None or not selected.success:
                consecutive_card_failures += 1
                self.task.log_warning(f"地图采集：跳过未能进入的卡带 {card.card_id}。")
                if consecutive_card_failures >= 3:
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message="连续三张卡带进入失败",
                    )
                continue
            if selected.outcome == CollectionCardSelectionOutcome.VISUALLY_COMPLETE:
                consecutive_card_failures = 0
                self._status(
                    "卡带完成度",
                    f"{card.card_id}：吸取与压制均已完成，本轮跳过",
                )
                continue

            card_failed = False
            for target in card.targets:
                if target.key in completed:
                    continue
                if state.daily_submaps >= DAILY_SUBMAP_LIMIT:
                    self.progress.mark_depleted_today()
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message="达到每日 21 个小图保护上限",
                    )
                self._status(
                    "采集进度",
                    f"{card.card_id} {target.role.label}：{target.title}",
                )
                arrived = self.navigator.enter_collection_map(card.card_id, target)
                if not arrived.success:
                    self.task.log_warning(
                        f"地图采集：{card.card_id} {target.role.label}"
                        f"（{target.title}）进入失败：{arrived.message or '-'}。"
                    )
                    card_failed = True
                    break
                skill_result = self._use_skills()
                if not skill_result.completed:
                    if skill_result.depleted:
                        self.progress.mark_depleted_today()
                        return CollectionResult(
                            True,
                            depleted=True,
                            completed_submaps=completed_this_run,
                            message=skill_result.message or "采集技能已达到上限",
                        )
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message=(
                            f"{card.card_id} 技能操作失败"
                            + (f"：{skill_result.message}" if skill_result.message else "")
                        ),
                    )
                self.progress.mark_target(card.card_id, target.key)
                state = self.progress.state
                completed_this_run += 1
                if skill_result.depleted:
                    self.progress.mark_depleted_today()
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message="采集技能显示已达到上限",
                    )

            if card_failed:
                consecutive_card_failures += 1
                if consecutive_card_failures >= 3:
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message="连续三张卡带采集失败",
                    )
            else:
                consecutive_card_failures = 0

        return CollectionResult(
            True,
            completed_submaps=completed_this_run,
            message="本周可采集卡带已经处理完毕",
        )

    def _use_skills(self) -> SkillExecutionResult:
        self.vision.click_reference(1203, 664, after_sleep=0.8)
        if self.vision.wait_template(SKILL_MENU_TEMPLATE, 5) is None:
            return SkillExecutionResult(False, message="未识别到探查图标，技能栏未确认")
        frame = self.vision.capture()
        empty_match = self.vision.match(frame, SKILL_NOTHING_TEMPLATE)
        if self.vision.passes(empty_match, SKILL_NOTHING_TEMPLATE):
            self.task.log_warning("地图采集：技能栏存在空技能，停止以避免误点。")
            return SkillExecutionResult(False, message="技能栏存在空技能")

        depleted = False
        for action in SKILL_ACTIONS:
            frame = self.vision.capture()
            detection = self.action_icons.detect(frame, action.icon)
            self._report_icon_detection(action, detection)
            if detection.state is ActionIconState.ABSENT:
                return SkillExecutionResult(False, depleted, f"未识别到{action.name}图标")
            if detection.state is ActionIconState.UNKNOWN:
                return SkillExecutionResult(
                    False,
                    depleted,
                    f"{action.name}图标亮度处于未知区间",
                )

            before = self._read_count(action)
            if before is None:
                return SkillExecutionResult(False, depleted, f"{action.name}次数 OCR 失败")
            self._status(f"{action.name}次数", f"{before[0]}/{before[1]}")
            if detection.state is ActionIconState.USED:
                self._status(f"{action.name}状态", "已使用")
                depleted = depleted or before[0] >= before[1]
                continue
            if before[0] >= before[1]:
                return SkillExecutionResult(
                    False,
                    True,
                    f"{action.name}次数已达到 {before[0]}/{before[1]}，当前小图未完成",
                )

            self.vision.click_client(detection.match.center, frame.shape, after_sleep=2.0)
            after = self._read_count(action)
            if after is None:
                return SkillExecutionResult(False, depleted, f"{action.name}次数 OCR 失败")
            self._status(f"{action.name}次数", f"{after[0]}/{after[1]}")
            if after[0] <= before[0]:
                if not action.icon.dimmed_means_used:
                    return SkillExecutionResult(
                        False,
                        depleted,
                        f"{action.name}次数未增加，无法确认执行成功",
                    )
                post_frame = self.vision.capture()
                post_detection = self.action_icons.detect(post_frame, action.icon)
                self._report_icon_detection(action, post_detection)
                if post_detection.state is not ActionIconState.USED:
                    return SkillExecutionResult(
                        False,
                        depleted,
                        f"{action.name}次数未增加且图标未变为已使用",
                    )
            depleted = depleted or after[0] >= after[1]
        return SkillExecutionResult(True, depleted)

    def _report_icon_detection(
        self,
        action: SkillAction,
        detection: ActionIconDetection,
    ) -> None:
        match = detection.match
        brightness = (
            "-"
            if detection.bright_core_ratio is None
            else f"{detection.bright_core_ratio:.3f}"
        )
        self._status(
            f"{action.name}图标",
            (
                f"{detection.state.value}; match={match.score:.3f}; "
                f"pixel={match.pixel_score:.3f}; zncc={match.zncc_score:.3f}; "
                f"bright={brightness}"
            ),
        )

    def _read_count(self, action: SkillAction) -> tuple[int, int] | None:
        for _attempt in range(2):
            text = self.vision.ocr_text(
                self.vision.capture(),
                f"{action.name}次数",
                roi=action.count_roi,
            )
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
