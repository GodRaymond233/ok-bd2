from __future__ import annotations

from collections import deque

from src.tasks.map_trade.action_icons import ActionIconDetector
from src.tasks.map_trade.card_status import CollectionCardSelectionOutcome
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
from src.tasks.map_trade.collector_skills import SkillExecutionMixin
from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    DAILY_ABSORB_LIMIT,
    DAILY_SUBMAP_LIMIT,
    DAILY_SUMMON_LIMIT,
    DAILY_SUPPRESS_LIMIT,
    CollectionMapRole,
    CollectionResult,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.vision import Vision


class Collector(SkillExecutionMixin):
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
        try:
            return self._run_collection()
        except RuntimeError as exc:
            self.task.log_error("地图采集流程异常", exc)
            return CollectionResult(
                False,
                message=f"地图采集流程异常：{exc}",
            )

    def _run_collection(self) -> CollectionResult:
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
                committed = self.progress.mark_target(
                    card.card_id,
                    main_target.key,
                )
                if not committed:
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message=(
                            f"{card.card_id}{main_target.role.label}提交失败："
                            "今日技能额度已用尽，剩余地图留待次日"
                        ),
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
                committed = self.progress.mark_target(
                    card.card_id,
                    battle_one.key,
                )
                if not committed:
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message=(
                            f"{card.card_id}{battle_one.role.label}提交失败："
                            "今日技能额度已用尽，战斗区域2留待次日"
                        ),
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
                committed = self.progress.mark_target(
                    card.card_id,
                    battle_two.key,
                )
                if not committed:
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message=(
                            f"{card.card_id}{battle_two.role.label}提交失败："
                            "今日技能额度已用尽"
                        ),
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

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
