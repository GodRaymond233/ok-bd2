from __future__ import annotations

from dataclasses import dataclass

from src.tasks.map_trade.models import (
    COLLECTABLE_CARDS,
    DAILY_SUBMAP_LIMIT,
    CollectionResult,
    TemplateSpec,
)
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.vision import Vision, parse_used_limit

SKILL_MENU_TEMPLATE = TemplateSpec("探查技能", "image/Skill1-1.png", 0.72, roi=(930, 590, 140, 120))
ABSORB_SKILL_TEMPLATE = TemplateSpec(
    "吸取技能", "image/Skill1-2~5.png", 0.70, roi=(900, 500, 150, 120)
)
SUMMON_SKILL_TEMPLATE = TemplateSpec(
    "召集技能", "image/Skill2-1-2.png", 0.70, roi=(930, 405, 150, 125)
)
SKILL_NOTHING_TEMPLATE = TemplateSpec(
    "空技能", "image/Skill-Nothing.png", 0.72, roi=(900, 390, 180, 230)
)


@dataclass(frozen=True)
class SkillAction:
    name: str
    template: TemplateSpec
    fallback_point: tuple[int, int]
    count_roi: tuple[int, int, int, int]


SKILL_ACTIONS = (
    SkillAction("探查", SKILL_MENU_TEMPLATE, (1002, 651), (958, 645, 82, 55)),
    SkillAction("吸取", ABSORB_SKILL_TEMPLATE, (970, 562), (930, 548, 85, 55)),
    SkillAction("召集", SUMMON_SKILL_TEMPLATE, (1006, 480), (960, 465, 92, 55)),
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

    def run(self) -> CollectionResult:
        state = self.progress.load()
        if state.depleted_today or state.daily_submaps >= DAILY_SUBMAP_LIMIT:
            return CollectionResult(True, depleted=True, message="今日采集技能额度已用尽")
        if state.weekly_submap_count >= len(COLLECTABLE_CARDS) * 3:
            return CollectionResult(True, message="本周 54 个小图已经全部完成")

        completed_this_run = 0
        consecutive_card_failures = 0
        card_retries = max(1, int(self.task.config.get("卡带单步重试次数", 2)))
        for card in COLLECTABLE_CARDS:
            completed = state.completed_submaps(card.card_id)
            if len(completed) >= 3:
                continue
            if state.depleted_today or state.daily_submaps >= DAILY_SUBMAP_LIMIT:
                self.progress.mark_depleted_today()
                return CollectionResult(
                    True,
                    depleted=True,
                    completed_submaps=completed_this_run,
                    message="达到每日 21 个小图保护上限",
                )

            entered = None
            for _attempt in range(card_retries):
                entered = self.navigator.select_card(card.card_id)
                if entered.success:
                    break
            if entered is None or not entered.success:
                consecutive_card_failures += 1
                self.task.log_warning(f"地图采集：跳过未能进入的卡带 {card.card_id}。")
                if consecutive_card_failures >= 3:
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message="连续三张卡带进入失败",
                    )
                continue

            card_failed = False
            for submap_index in range(3):
                if submap_index in completed:
                    continue
                if state.daily_submaps >= DAILY_SUBMAP_LIMIT:
                    self.progress.mark_depleted_today()
                    return CollectionResult(
                        True,
                        depleted=True,
                        completed_submaps=completed_this_run,
                        message="达到每日 21 个小图保护上限",
                    )
                self._status("采集进度", f"{card.card_id} 小图{submap_index + 1}/3")
                arrived = self.navigator.enter_collection_submap(submap_index)
                if not arrived.success:
                    self.task.log_warning(
                        f"地图采集：{card.card_id} 小图{submap_index + 1} 传送失败。"
                    )
                    card_failed = True
                    break
                skill_success, depleted = self._use_skills()
                if not skill_success:
                    return CollectionResult(
                        False,
                        completed_submaps=completed_this_run,
                        message=f"{card.card_id} 技能操作失败",
                    )
                self.progress.mark_submap(card.card_id, submap_index)
                state = self.progress.state
                completed_this_run += 1
                if depleted:
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

    def _use_skills(self) -> tuple[bool, bool]:
        self.vision.click_reference(1203, 664, after_sleep=0.8)
        if self.vision.wait_template(SKILL_MENU_TEMPLATE, 5) is None:
            return False, False
        frame = self.vision.capture()
        if self.vision.match(frame, SKILL_NOTHING_TEMPLATE).score >= self.vision.threshold_for(
            SKILL_NOTHING_TEMPLATE
        ):
            self.task.log_warning("地图采集：技能栏存在空技能，停止以避免误点。")
            return False, False

        for action in SKILL_ACTIONS:
            before = self._read_count(action)
            if before is not None and before[0] >= before[1]:
                return True, True
            frame = self.vision.capture()
            match = self.vision.match(frame, action.template)
            if match.score >= self.vision.threshold_for(action.template):
                self.vision.click_client(match.center, frame.shape, after_sleep=2.0)
            else:
                # The fallback remains proportional and is only used after the
                # skill menu itself was positively identified.
                self.vision.click_reference(*action.fallback_point, after_sleep=2.0)
            after = self._read_count(action)
            if after is not None:
                self._status(f"{action.name}次数", f"{after[0]}/{after[1]}")
                if after[0] >= after[1]:
                    return True, True
        return True, False

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
