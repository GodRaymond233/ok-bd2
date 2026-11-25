"""Sweep the highest confirmed cleared PC tower level in one batch."""

import re
from dataclasses import replace
from time import monotonic

from src.icons import TaskIcon
from src.tasks import run_history
from src.tasks.CartridgeChallengeTask import CartridgeChallengeTask, red_silhouette
from src.tasks.HomeRewardTasks import _button_brightness
from src.utils.vision_models import TemplateSpec

REWARD_COUNT = re.compile(r"(?:战斗|戰鬥)([1-9]|10)次(?:奖励|獎勵)")
KEY_COUNT = re.compile(r"([0-9]|10)\s*[/／]\s*10")
CASTLE_CARD = TemplateSpec(
    "tower_cartridge_cover", "tower_cartridge_cover.png", .84,
    relative_roi=(.045, .10, .99, .92), min_zncc_score=.70,
)
CASTLE_CARD_LEFT = replace(CASTLE_CARD, name="tower_card_left", crop=(0, 0, .32, 1))
CASTLE_CARD_RIGHT = replace(CASTLE_CARD, name="tower_card_right", crop=(.68, 0, 1, 1))
ALTAR = TemplateSpec(
    "tower_altar_marker", "tower_altar_marker.png", .85,
    relative_roi=(.28, .10, .72, .65),
)
SPAWN = TemplateSpec(
    "tower_spawn_steps", "tower_spawn_steps.png", .87,
    relative_roi=(.33, .065, .67, .24), min_zncc_score=.82,
)
CLEARED_TICK = TemplateSpec("tower_level_tick", "tower_level_tick.png", .73, min_zncc_score=.72)
LEVEL_ROWS = tuple((.139 + row * .0917) for row in range(10))
TOWER_NAMES = ("救赎之塔", "救贖之塔")
SWEEP_MAX_POINT = (.609, .503)
SWEEP_MINUS_POINT = (.364, .566)


class TowerSweepTask(CartridgeChallengeTask):
    title = "救赎之塔快速战斗"
    description_text = "选择最高已确认通关的难度，按场数上限和钥匙预留快速战斗。"
    title_labels = TOWER_NAMES
    task_icon = TaskIcon.FLAG
    status_keys = ["状态", "已选难度", "救赎钥匙", "Log", "Warning", "Error"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config["预留救赎钥匙"] = 0
        self.config_type["预留救赎钥匙"] = {"min": 0, "max": 10, "step": 1}
        self.default_config["单次速刷上限"] = 1
        self.config_type["单次速刷上限"] = {"min": 1, "max": 10, "step": 1}

    def _on_page(self, labels):
        return any(text.endswith(TOWER_NAMES) and len(text) <= 5 and .09 < x < .28 and y < .10
                   for text, x, y, _ in labels)

    @staticmethod
    def _castle_menu(labels):
        return any(text.endswith(("恶魔城", "惡魔城")) and len(text) <= 4
                   and .09 < x < .28 and y < .10 for text, x, y, _ in labels)

    def _altar_route(self, frame, labels):
        if not any(text in {"space", "tab"} and x > .82 and y > .84 for text, x, y, _ in labels):
            return None
        marker = self._card_center(red_silhouette(frame), ALTAR)
        if marker is not None:
            return marker
        steps = self._card_center(frame, SPAWN)
        if steps is None:
            return None
        x, y = steps
        return x, y + .12

    def _castle_arrived(self, frame, labels):
        return self._on_page(labels) or self._castle_menu(labels) or (
            self._altar_route(frame, labels) is not None
        )

    def _card_center(self, frame, card):
        center = super()._card_center(frame, card)
        if center is not None or card is not CASTLE_CARD:
            return center
        left = super()._card_center(frame, CASTLE_CARD_LEFT)
        right = super()._card_center(frame, CASTLE_CARD_RIGHT)
        if left is None or right is None:
            return None
        if not .037 < right[0] - left[0] < .053 or abs(right[1] - left[1]) > .008:
            return None
        return (left[0] + right[0]) / 2, (left[1] + right[1]) / 2

    def _enter_tower(self, frame, labels):
        if not self._castle_arrived(frame, labels):
            arrived = self._enter_cartridge(frame, labels, CASTLE_CARD, self._castle_arrived)
            if arrived is None:
                return None
            frame, labels = arrived
        for _ in range(5):
            if self._on_page(labels):
                return frame, labels
            if self._castle_menu(labels):
                card = [(x, y) for text, x, y, _ in labels
                        if text in TOWER_NAMES and .40 < x < .60 and .48 < y < .67]
                if len(card) != 1:
                    return None
                self.operate_click(*card[0], after_sleep=.5)
                return self._wait_page()
            point = self._altar_route(frame, labels)
            if point is None:
                return None
            self.operate_click(*point, after_sleep=2)
            frame, labels = self._read()
        return (frame, labels) if self._on_page(labels) else None

    def _highest_cleared(self, frame):
        for level, y in reversed(list(enumerate(LEVEL_ROWS, 1))):
            band = replace(CLEARED_TICK, relative_roi=(.16, y - .024, .22, y + .024))
            tick = self._match(frame, band)
            if self._passes(tick, band):
                return level, (.146, y)
        return None

    @staticmethod
    def _selected_level(labels):
        levels = [int(text.removeprefix("lv")) for text, x, y, _ in labels
                  if .40 < x < .46 and .105 < y < .18
                  and re.fullmatch(r"(?:lv)?(?:[1-9]|10)", text)]
        return levels[0] if len(levels) == 1 else None

    @staticmethod
    def _keys(labels):
        counts = [int(match.group(1)) for _, x, y, box in labels
                  if .89 < x < .98 and .025 < y < .073
                  and (match := KEY_COUNT.fullmatch(str(box.name).strip()))]
        return counts[0] if len(counts) == 1 else None

    def _sweep_dialog(self, labels):
        return (
            any(text in {"快速战斗", "快速戰鬥"} and .30 < x < .70 and .12 < y < .42
                for text, x, y, _ in labels)
            and any(REWARD_COUNT.fullmatch(text) and .25 < x < .75 and .18 < y < .60
                    for text, x, y, _ in labels)
        )

    def _dialog_count(self, labels):
        if any(text in {"购买", "購買", "付费", "付費"} for text, _, _, _ in labels):
            return None
        counts = [int(REWARD_COUNT.fullmatch(text)[1]) for text, x, y, _ in labels
                  if .25 < x < .75 and .18 < y < .60 and REWARD_COUNT.fullmatch(text)]
        return counts[0] if self._sweep_dialog(labels) and len(counts) == 1 else None

    def _configured_action(self, frame, labels, count=1):
        if self._dialog_count(labels) != count:
            return None
        buttons = [(x, y, box) for text, x, y, box in labels
                   if text in {"战斗", "戰鬥"} and .48 < x < .81 and .58 < y < .91]
        if len(buttons) == 1:
            x, y, box = buttons[0]
            brightness = _button_brightness(frame, box)
            if brightness is not None and brightness >= 180:
                return x, y
        return None

    def _read_sweep_count(self, expected=None):
        deadline = monotonic() + 5
        last = None
        while monotonic() < deadline:
            _, labels = self._read()
            count = self._dialog_count(labels)
            if count is not None and count == last and (expected is None or count == expected):
                return count
            last = count
            self.sleep(.3)
        return None

    def _set_sweep_count(self, limit, keys):
        count = self._read_sweep_count()
        if count is None or count > keys:
            return None
        if count < limit:
            self.operate_click(*SWEEP_MAX_POINT, after_sleep=.5)
            count = self._read_sweep_count()
            if count is None or count > keys:
                return None
        while count > limit:
            self.operate_click(*SWEEP_MINUS_POINT, after_sleep=.4)
            count = self._read_sweep_count(expected=count - 1)
            if count is None:
                return None
        return count

    def _wait_selected_level(self, level):
        deadline = monotonic() + 6
        while monotonic() < deadline:
            frame, labels = self._read()
            if self._on_page(labels) and self._selected_level(labels) == level:
                return frame, labels
            self.sleep(.35)
        return None

    def run(self):
        day = str(run_history.day_start_ts())
        step = f"快速战斗:{day}"
        prior = run_history.read_stage(self, step)
        if prior.get("state") == "done":
            self.info_set("状态", "今日快速战斗已确认完成。")
            return True
        if prior.get("state") == "pending":
            return self._fail("今日已有一次快速战斗提交待核对")

        frame, labels = self._read()
        if not self._on_page(labels):
            entered = self._enter_tower(frame, labels)
            if entered is None:
                return self._fail("未确认救赎之塔入口")
            frame, labels = entered
        if self._sweep_dialog(labels):
            return self._fail("请关闭速刷弹窗以复核难度与钥匙")
        cleared = self._highest_cleared(frame)
        if cleared is None:
            return self._fail("未确认已通关的难度")
        level, point = cleared
        if self._selected_level(labels) != level:
            self.operate_click(*point, after_sleep=.6)
        page = self._wait_selected_level(level)
        if page is None:
            return self._fail("未确认切换到已通关难度")
        frame, labels = page
        keys = self._keys(labels)
        limit = self.config.get("单次速刷上限", 1)
        reserve = self.config.get("预留救赎钥匙", 0)
        if type(limit) is not int or not 1 <= limit <= 10:
            return self._fail("单次速刷上限必须在 1 到 10 场之间")
        if type(reserve) is not int or not 0 <= reserve <= 10:
            return self._fail("预留救赎钥匙必须在 0 到 10 把之间")
        reserve = max(reserve, int(limit > 1 and level < 10))
        if keys is None:
            return self._fail("未确认可用的救赎钥匙")
        if keys <= reserve:
            self.info_set("状态", f"剩余 {keys} 把救赎钥匙，本次全部预留。")
            return self._finish()
        buttons = [(x, y, box) for text, x, y, box in labels
                   if text in {"快速战斗", "快速戰鬥"} and .67 < x < .82 and y > .85]
        if len(buttons) != 1:
            return self._fail("未识别到快速战斗按钮")
        x, y, box = buttons[0]
        brightness = _button_brightness(frame, box)
        if brightness is None or brightness < 180:
            return self._fail("快速战斗按钮未启用")
        self.info_set("已选难度", level)
        self.info_set("救赎钥匙", keys)
        self.operate_click(x, y, after_sleep=.8)
        count = self._set_sweep_count(min(limit, keys - reserve), keys)
        if count is None:
            return self._fail("未能确认速刷场数")

        confirmed = None
        deadline = monotonic() + 6
        while monotonic() < deadline:
            frame, labels = self._read()
            point = self._configured_action(frame, labels, count)
            if point is not None and confirmed is not None and max(
                abs(a - b) for a, b in zip(point, confirmed)
            ) <= .01:
                if day != str(run_history.day_start_ts()):
                    return self._fail("日常已刷新，请重新核对钥匙")
                evidence = {"level": level, "keys_before": keys, "count": count}
                run_history.save_stage(self, step, "pending", scope=day, evidence=evidence)
                self.operate_click(*point, after_sleep=1)
                break
            confirmed = point
            self.sleep(.35)
        else:
            return self._fail(f"未确认场数为 {count}，未提交战斗")

        deadline = monotonic() + 60
        while monotonic() < deadline:
            _, labels = self._read()
            if any(text in {"攻略完毕", "攻略完畢"} and .5 < x < .85 and y < .28
                   for text, x, y, _ in labels) and any(
                       text in {f"快速战斗{count}", f"快速戰鬥{count}"}
                       and .5 < x < .85 and .17 < y < .32
                       for text, x, y, _ in labels
                   ):
                run_history.save_stage(self, step, "done", scope=day, evidence=evidence)
                for text, x, y, _ in labels:
                    if text in {"点击画面继续", "點擊畫面繼續"} and .5 < x < .85 and y > .78:
                        self.operate_click(x, y, after_sleep=.5)
                        return self._finish()
                self.log_completion("救赎之塔快速战斗完成，停在结算页。")
                return True
            self.sleep(.6)
        return self._fail("战斗已提交，但未读到攻略完毕")
