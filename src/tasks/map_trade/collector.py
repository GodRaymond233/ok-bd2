from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import replace

from src.tasks.map_trade.action_icons import ActionIconDetector
from src.tasks.map_trade.card_status import CardActionState, CollectionCardSelectionOutcome
from src.tasks.map_trade.collector_constants import (
    ABSORB_ACTION,
    BATTLE_ACTIONS,
    SKILL_FAILURE_EVIDENCE_LIMIT,
    SKILL_FAILURE_TEXT_LIMIT,
    SkillExecutionResult,
)
from src.tasks.map_trade.collector_skills import SkillExecutionMixin
from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    COLLECTION_ACTION_NAMES,
    LAST_MAP_THEN_TOWN_CARD_IDS,
    CollectionMapRole,
    CollectionResult,
    ScreenState,
    collection_action_policy,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.vision import Vision

# "卡带单步重试次数"未配置时的默认值。
CARD_RETRY_DEFAULT = 2


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
        self._last_skill_geometry: dict[str, object] = {}
        self._count_actions_seen: set[str] = set()
        self._quota_checks: set[str] = set()
        self._completed_this_run = 0
        self._collection_target = None
        self._skill_field_generation = 0
        self._recovery_suppression_enabled = False
        # (card_id, map_role) -> 探查施放时刻（monotonic）：重进卡带时
        # 倒计时数字 OCR 不可靠，靠账本判定增益仍在进行并复用。
        self._search_cast_monotonic: dict[tuple[str, str], float] = {}
        self._scope_cards = COLLECTABLE_CARDS
        self._scope_actions = collection_action_policy(BATTLE_ACTIONS)
        # 本次运行中在已确认的活动卡类别页里未找到的卡带：跳过且不计入本周范围。
        self.missing_cards: list = []

    def run(
        self,
        cards=None,
        *,
        battle_actions=BATTLE_ACTIONS,
        only_once: bool = False,
    ) -> CollectionResult:
        """按顺序采集 ``cards``（默认剧情与角色卡）；单章模式在新完成一张卡带后停止。"""
        self._completed_this_run = 0
        self._count_actions_seen.clear()
        self._quota_checks.clear()
        self._scope_cards = tuple(COLLECTABLE_CARDS if cards is None else cards)
        self._scope_actions = collection_action_policy(battle_actions)
        self.missing_cards = []
        try:
            return self._run_collection(battle_actions, only_once=only_once)
        except RuntimeError as exc:
            self.task.log_error("地图采集流程异常", exc)
            return CollectionResult(
                False,
                message=f"地图采集流程异常：{exc}",
            )

    def scope_complete(self) -> bool:
        """本次范围内的卡带（未找到的活动卡除外）是否都已按启用动作核验完成。"""
        missing = {card.card_id for card in self.missing_cards}
        return all(
            self._card_complete(card)
            for card in self._scope_cards
            if card.card_id not in missing
        )

    def _card_complete(self, card) -> bool:
        state = self.progress.state
        return state.card_verified(
            card.card_id, required_actions=self._scope_actions,
        ) or state.card_visually_complete(card.card_id, self._scope_actions)

    def _run_collection(
        self, battle_actions=BATTLE_ACTIONS, *, only_once=False,
    ) -> CollectionResult:
        self._status("采集未完成卡带", "-")
        self._status("未找到的活动卡带", "-")
        self.progress.load()
        quota_limited = False
        if self.scope_complete():
            return CollectionResult(
                True,
                message="本周所选卡带均已采集并完成视觉复核",
            )

        card_retries = max(
            1, int(self.task.config.get("卡带单步重试次数", CARD_RETRY_DEFAULT))
        )
        failures: list[str] = []
        skipped_cards: list[str] = []
        finished = None
        for card in self._scope_cards:
            was_complete = only_once and self._card_complete(card)
            outcome = self.collect_card(
                card, card_retries=card_retries, battle_actions=battle_actions,
            )
            if outcome is None:
                if only_once and not was_complete and self._card_complete(card):
                    # 停止前已跳过的失败卡带仍要计入本次结果。
                    finished = card
                    break
                continue
            if getattr(outcome, "card_missing", False):
                self.missing_cards.append(card)
                labels = "、".join(item.label for item in self.missing_cards)
                self._status("未找到的活动卡带", labels)
                self.task.log_warning(
                    f"地图采集：{card.label}未在活动游戏卡页找到，可能未持有，已跳过。"
                )
                continue
            if outcome.success and outcome.depleted:
                quota_limited = True
                continue
            if not outcome.success and outcome.retryable:
                reason = f"{card.label}：{outcome.message}"
                failures.append(reason)
                self._status("采集未完成卡带", "；".join(failures))
                self.task.log_warning(f"地图采集：{reason}，返回主页后继续其余卡带。")
                returned = self.navigator.return_home()
                if returned.success and returned.state == ScreenState.HOME:
                    skipped_cards.append(card.label)
                    continue
                failures.append(f"返回主页失败：{returned.message}")
            if failures:
                if outcome.success:
                    return replace(
                        outcome, success=False, partial=True,
                        message=(
                            f"{'、'.join(skipped_cards)}未完成，已跳过；"
                            + ("后续采集因今日技能额度不足结束，剩余地图留待次日。"
                               if outcome.depleted else "其余卡带采集成功。")
                            + self._missing_cards_note()
                        ),
                    )
                return replace(
                    outcome, success=False,
                    message="；".join(failures + (
                        [outcome.message] if not outcome.retryable else []
                    )) + self._missing_cards_note(),
                )
            if self.missing_cards:
                return replace(outcome, message=outcome.message + self._missing_cards_note())
            return outcome

        missing_note = self._missing_cards_note()
        finished_note = (
            f"单章模式：{finished.label}已完成，本次运行结束" if finished is not None else ""
        )
        if failures:
            return CollectionResult(
                False, completed_submaps=self._completed_this_run, partial=True,
                depleted=quota_limited,
                message=(
                    f"{'、'.join(skipped_cards)}未完成，已跳过；"
                    + (f"{finished_note}。" if finished is not None
                       else "后续采集因今日技能额度不足结束，剩余地图留待次日。"
                       if quota_limited else "其余卡带采集成功。")
                    + missing_note
                ),
            )
        if finished is not None:
            return CollectionResult(
                True,
                completed_submaps=self._completed_this_run,
                message=finished_note + missing_note,
            )
        pending = self.progress.pending_count()
        if quota_limited and not self.scope_complete():
            return CollectionResult(
                True,
                depleted=True,
                completed_submaps=self._completed_this_run,
                message=(
                    "今日采集技能额度已用尽；已有动作恢复核验结束，未完成地图留待次日"
                    + missing_note
                ),
            )
        return CollectionResult(
            True,
            completed_submaps=self._completed_this_run,
            message=(
                "本周已支持的可采集卡带已经处理完毕"
                + (f"；末图有{pending}条动作次数待后续明亮帧对账" if pending else "")
                + missing_note
            ),
        )

    def _missing_cards_note(self) -> str:
        if not self.missing_cards:
            return ""
        labels = "、".join(card.label for card in self.missing_cards)
        return f"；{labels}未在活动游戏卡页找到，已跳过"

    def collect_card(
        self,
        card,
        *,
        card_retries: int = 2,
        battle_actions=BATTLE_ACTIONS,
    ) -> CollectionResult | None:
        """按已启用动作采集一张卡带并持久化核验结果。

        调用前须加载 progress；每个消费动作先写入意图，再点击和结算。
        返回 None 表示整卡核验完成，CollectionResult 表达停止、额度或失败原因。
        """
        self._collection_target = None
        required_actions = collection_action_policy(battle_actions)

        state = self.progress.state
        if state.card_visually_complete(card.card_id, required_actions):
            return None
        if state.card_verified(card.card_id, required_actions=required_actions):
            return None
        completed = {
            target_key
            for target_key in state.completed_targets(card.card_id)
            if self.progress._target_actions_ready(
                card.card_id,
                target_key,
                required_actions=required_actions,
            )
        }
        if len(completed) >= len(card.targets):
            failure = self._verify_collection_card(card, required_actions)
            remaining_completed = completed & state.completed_targets(card.card_id)
            if failure is None or remaining_completed == completed:
                return failure
            completed = remaining_completed

        remaining, recovery_only = self._plan_collection_work(card, completed, required_actions)
        needed = self.progress.collection_plan_requirements(
            [target for target in card.targets if target.key not in completed],
            card_id=card.card_id, required_actions=required_actions,
        )
        refresh_quota = tuple(
            action for action in BATTLE_ACTIONS
            if action.name in required_actions and needed.get(action.name)
            and action.name not in self._quota_checks
            and not self.progress.can_reserve_action(action.name, needed[action.name])
        )
        if recovery_only and not remaining and not refresh_quota:
            return CollectionResult(
                True,
                depleted=True,
                completed_submaps=self._completed_this_run,
                message="今日剩余吸取/召集/压制次数不足，未完成地图留待次日",
            )
        selected = None
        selection_errors = []
        for _attempt in range(card_retries):
            selection_kwargs = {"enter_visually_complete": recovery_only}
            if set(required_actions) != COLLECTION_ACTION_NAMES:
                selection_kwargs["required_actions"] = required_actions
            selected = self.navigator.select_collection_card(
                card.card_id,
                **selection_kwargs,
            )
            if selected.success:
                break
            if selected.message and selected.message not in selection_errors:
                selection_errors.append(selected.message)
        if selected is None or not selected.success:
            return CollectionResult(
                False,
                completed_submaps=self._completed_this_run,
                message=f"未能进入卡带 {card.card_id}：{'；'.join(selection_errors) or '-'}",
                card_missing=(
                    card.category == "event"
                    and selected is not None
                    and selected.navigation.card_not_found
                ),
            )
        if selected.outcome == CollectionCardSelectionOutcome.VISUALLY_COMPLETE:
            return self._record_visual_completion(card, required_actions)

        if refresh_quota:
            self._refresh_collection_quota(refresh_quota)
            remaining, recovery_only = self._plan_collection_work(card, completed, required_actions)
            if recovery_only and not remaining:
                return CollectionResult(
                    True, depleted=True, completed_submaps=self._completed_this_run,
                    message="当前技能额度不足，未完成地图留待次日",
                )
        outcome, observed_depleted = self._collect_card_targets(
            card, completed, remaining, required_actions, battle_actions,
            recovery_only=recovery_only,
        )
        if outcome is not None:
            return outcome
        if recovery_only and len(completed) < len(card.targets):
            return CollectionResult(
                True,
                depleted=True,
                completed_submaps=self._completed_this_run,
                message=f"{card.card_id}已有动作已核验，仍缺新动作的地图留待次日",
            )
        reopened = self.navigator.open_cartridge_quick_switcher_from_sandbox(category=card.category)
        if not reopened.success:
            return CollectionResult(
                False,
                completed_submaps=self._completed_this_run,
                message=f"{card.card_id}完成后无法打开快速切换页：{reopened.message}",
            )
        failure = self._verify_collection_card(card, required_actions)
        if failure is not None:
            return failure
        return self._report_collection_card(required_actions, observed_depleted)

    def _plan_collection_work(self, card, completed, required_actions) -> tuple[set[str], bool]:
        needed = self.progress.collection_plan_requirements(
            [target for target in card.targets if target.key not in completed],
            card_id=card.card_id, required_actions=required_actions,
        )
        recovery_only = not any(needed.values()) or not self._can_finish_card_today(
            card, completed, required_actions=required_actions,
        )
        if recovery_only:
            deficits = {
                action: amount for action, amount in needed.items()
                if amount and not self.progress.can_reserve_action(action, amount)
            }
            if deficits:
                self.progress.mark_depleted_today(deficits)
        remaining = {target.key for target in card.targets} - completed
        if recovery_only:
            remaining &= self.progress.collection_recovery_targets(card.card_id, required_actions)
        return remaining, recovery_only

    def _record_visual_completion(self, card, required_actions) -> CollectionResult | None:
        try:
            self.progress.mark_card_visual_complete(card.card_id, required_actions=required_actions)
        except (RuntimeError, ValueError) as exc:
            self.task.log_warning(f"地图采集：{card.card_id}视觉完成证据写入失败：{exc}")
            return CollectionResult(
                False, completed_submaps=self._completed_this_run,
                message=f"{card.card_id}视觉完成证据未写入账本", retryable=False,
            )
        self._status(
            "卡带完成度",
            f"{card.card_id}进入前确认周常吸取完成，已保存卡带视觉证据"
            if self.progress.state.card_visually_complete(card.card_id)
            else f"{card.card_id}进入前确认卡带完成，已保存验证标记",
        )
        return None

    def _collect_card_targets(
        self, card, completed, remaining, required_actions, battle_actions, *, recovery_only,
    ) -> tuple[CollectionResult | None, bool]:
        """Follow the named route, allowing one restart while preserving completed targets."""
        self._recovery_suppression_enabled = (
            not recovery_only and "压制" in required_actions
        )
        action_kwargs = {"recovery_only": True} if recovery_only else {}
        current_target_key = self.navigator.current_collection_target(card)
        keys = [target.key for target in card.targets]
        order = [target for target in card.targets if target.key in remaining]
        if current_target_key == keys[-1]:
            if card.card_id in LAST_MAP_THEN_TOWN_CARD_IDS:
                order = [item for item in order if item.key == current_target_key] + [
                    item for item in order if item.key != current_target_key
                ]
            else:
                order.reverse()
        current = next(
            (target for target in card.targets if target.key == current_target_key), None,
        )
        if current_target_key not in (keys[0], keys[-1]):
            reset = self.navigator.prepare_collection_main_area(
                card.card_id,
                via_hunting_ground=True,
            )
            if not reset.success:
                return CollectionResult(
                    False,
                    completed_submaps=self._completed_this_run,
                    message=f"{card.card_id}安全区初始化失败：{reset.message}",
                ), False
            current = card.targets[0]
        self._status("采集进度", f"{card.card_id} 顺序=" + "→".join(t.title for t in order))
        observed_depleted = False
        restarted = False
        index = 0
        while index < len(order):
            target = order[index]
            arrived = self._go_to_collection_target(card, current, target)
            if arrived is not None and not arrived.success:
                if restarted:
                    return CollectionResult(
                        False,
                        completed_submaps=self._completed_this_run,
                        message=f"{card.card_id}前往{target.title}失败：{arrived.message}",
                    ), observed_depleted
                restarted = True
                reset = self.navigator.prepare_collection_main_area(
                    card.card_id,
                    via_hunting_ground=True,
                )
                if not reset.success:
                    return CollectionResult(
                        False,
                        completed_submaps=self._completed_this_run,
                        message=(
                            f"{card.card_id}前往{target.title}失败：{arrived.message}；"
                            f"回起点失败：{reset.message}"
                        ),
                    ), observed_depleted
                current = card.targets[0]
                order = [
                    item
                    for item in card.targets
                    if item.key in remaining and item.key not in completed
                ]
                index = 0
                continue
            current = target
            self._collection_target = (card, target)
            if not recovery_only and not self.progress.weekly_action_complete(
                card.card_id, target.role, ABSORB_ACTION.name,
            ):
                used_up = self._absorb_used_up(card_id=card.card_id, map_role=target.role)
                if used_up is not None:
                    return self._skill_failure(
                        card.card_id, target.role.label, used_up, required_actions=required_actions,
                    ), observed_depleted
                search = self._ensure_search(card_id=card.card_id, map_role=target.role)
                if isinstance(search, SkillExecutionResult):
                    return self._skill_failure(
                        card.card_id, f"{target.role.label}探查", search,
                    ), observed_depleted
            actions = (
                (ABSORB_ACTION,) if target.role is CollectionMapRole.MAIN_AREA else battle_actions
            )
            result = self._use_actions(
                actions,
                card_id=card.card_id,
                map_role=target.role,
                **action_kwargs,
            )
            if not result.completed:
                return self._skill_failure(
                    card.card_id, target.role.label, result, required_actions=required_actions,
                ), observed_depleted
            if self.progress._target_actions_ready(
                card.card_id,
                target.key,
                required_actions=required_actions,
            ):
                committed = self.progress.mark_target(
                    card.card_id,
                    target.key,
                    required_actions=required_actions,
                )
                completed.add(target.key)
                self._completed_this_run += int(committed)
            observed_depleted = observed_depleted or result.depleted
            if (
                not recovery_only
                and observed_depleted
                and any(key not in completed for key in keys)
                and not self._can_finish_card_today(
                    card, completed, required_actions=required_actions,
                )
            ):
                return CollectionResult(
                    True,
                    depleted=True,
                    completed_submaps=self._completed_this_run,
                    message=f"{card.card_id}{target.role.label}已完成，但技能次数已到上限，剩余地图留待次日",
                ), observed_depleted
            index += 1
        return None, observed_depleted

    def _verify_collection_card(self, card, required_actions) -> CollectionResult | None:
        if set(required_actions) == COLLECTION_ACTION_NAMES:
            verified = self.navigator.inspect_collection_card_completion(card.card_id)
            if not verified.success:
                completion = getattr(verified, "completion", None)
                pending = {
                    action for action, detection in (
                        ("吸收", completion.absorb), ("压制", completion.suppress),
                    ) if detection.state is CardActionState.PENDING
                } if completion is not None and completion.complete_region else set()
                if pending:
                    reopened = self.progress.invalidate_observed_completion(card.card_id, pending)
                    if reopened:
                        self._status(
                            "采集进度", f"{card.card_id}未完成，重新核验未点击的观察记录："
                            + "、".join(sorted(reopened)),
                        )
                return CollectionResult(
                    False,
                    completed_submaps=self._completed_this_run,
                    message=(f"{card.card_id}吸取/压制完成度复核失败：{verified.message or '-'}"),
                    retryable=False,
                )
            self.progress.mark_card_verified(card.card_id)
        else:
            try:
                self.progress.mark_card_verified(
                    card.card_id,
                    required_actions=required_actions,
                )
            except (RuntimeError, ValueError) as exc:
                return CollectionResult(
                    False,
                    completed_submaps=self._completed_this_run,
                    message=f"{card.card_id}策略动作完成度复核失败：{exc}",
                    retryable=False,
                )

        return None

    def _report_collection_card(
        self, required_actions, observed_depleted,
    ) -> CollectionResult | None:
        state = self.progress.state
        effective = self.progress.effective_daily_counts()
        pending = self.progress.pending_count()
        self._status(
            "每日技能进度",
            (
                f"吸取 {effective['吸收']}/{self.progress.action_limit('吸收') or '?'}"
                f"（本地{state.daily_absorbs}）；"
                f"召集 {effective['召集']}/{self.progress.action_limit('召集') or '?'}"
                f"（本地{state.daily_summons}）；"
                f"压制 {effective['压制']}/{self.progress.action_limit('压制') or '?'}"
                f"（本地{state.daily_suppressions}）" + (f"；待对账{pending}条" if pending else "")
            ),
        )
        return None

    def _can_finish_card_today(
        self,
        card,
        completed: set[str],
        *,
        required_actions=None,
    ) -> bool:
        remaining = [target for target in card.targets if target.key not in completed]
        return self.progress.can_plan_collection(
            remaining,
            card_id=card.card_id,
            required_actions=required_actions,
        )

    def _skill_failure(
        self,
        card_id: str,
        stage: str,
        result: SkillExecutionResult,
        *,
        required_actions=None,
    ) -> CollectionResult:
        self._record_skill_failure(card_id, stage, result)
        if result.depleted:
            if not self.progress.collection_budget_exhausted(required_actions):
                self.progress.mark_depleted_today()
            return CollectionResult(
                True,
                depleted=True,
                completed_submaps=self._completed_this_run,
                message=result.message or f"{card_id}{stage}技能次数已用尽",
            )
        return CollectionResult(
            False,
            completed_submaps=self._completed_this_run,
            message=(
                f"{card_id}{stage}技能操作失败" + (f"：{result.message}" if result.message else "")
            ),
            retryable=False,
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
            "geometry": deepcopy(self._last_skill_geometry),
            "observations": deepcopy(self._last_skill_observations),
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

        return tuple(deepcopy(item) for item in self._skill_failure_evidence)

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass

    def _go_to_collection_target(self, card, current, target):
        if current is not None and current.key == target.key:
            return None
        if current is None:
            arrived = self.navigator.prepare_collection_main_area(card.card_id)
            if not arrived.success or target.key == card.targets[0].key:
                return arrived
            current = card.targets[0]
        if (
            card.card_id in LAST_MAP_THEN_TOWN_CARD_IDS
            and current == card.targets[-1]
            and target == card.targets[0]
        ):
            return self.navigator.advance_collection_map(card.card_id, current, target)
        keys = [item.key for item in card.targets]
        start, end = keys.index(current.key), keys.index(target.key)
        step = 1 if end > start else -1
        for position in range(start + step, end + step, step):
            next_target = card.targets[position]
            arrived = self.navigator.advance_collection_map(card.card_id, current, next_target)
            if not arrived.success:
                return arrived
            current = next_target
        return arrived
