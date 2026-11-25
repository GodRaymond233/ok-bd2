"""Open the quick-battle dialog for the stage already selected in the client."""

import re
from time import monotonic

from src.icons import TaskIcon
from src.tasks.HomeRewardTasks import HomePageTask, _button_brightness

STAGE = re.compile(r"(普通战斗|普通戰鬥|挑战战斗|挑戰戰鬥)(5|10|15)")
QUICK = {"快速战斗", "快速戰鬥"}
FREE_AP = {"仅使用免费活动ap", "僅使用免費活動ap"}
CANCEL = {"取消"}


class EventQuickBattlePreviewTask(HomePageTask):
    return_home_after_run = False
    title = "赛季活动快速战斗预览"
    description_text = "打开当前活动关卡的快速战斗弹窗。"
    task_icon = TaskIcon.CALENDAR
    status_keys = ["状态", "活动关卡", "Log", "Warning", "Error"]

    @staticmethod
    def _dialog(labels):
        texts = {text for text, x, y, _ in labels if .25 < x < .75 and .12 < y < .88}
        return bool(texts & QUICK and texts & FREE_AP and texts & CANCEL and "max" in texts)

    def _stage_button(self, frame, labels):
        if any(text in FREE_AP or text in CANCEL for text, _, _, _ in labels):
            return None
        stages = [(text, y) for text, _, y, _ in labels if STAGE.fullmatch(text)]
        buttons = [(x, y, box) for text, x, y, box in labels if text in QUICK and y > .70]
        if len(stages) != 1 or len(buttons) != 1:
            return None
        (stage, stage_y), = stages
        (x, y, box), = buttons
        if stage_y >= y - .15 or box is None:
            return None
        row = [(text, label_x) for text, label_x, label_y, _ in labels
               if abs(label_y - y) < .027]
        if not any(text == "角色" and label_x < x for text, label_x in row):
            return None
        if not any(text.startswith(("战斗", "戰鬥")) and label_x > x for text, label_x in row):
            return None
        fill = _button_brightness(frame, box)
        return (stage, x, y) if fill is not None and fill >= 165 else None

    def run(self):
        frame, labels = self._read()
        if self._dialog(labels):
            self.info_set("状态", "快速战斗弹窗已打开。")
            return True
        first = self._stage_button(frame, labels)
        if first is None:
            return self._fail("未确认当前关卡的快速战斗按钮")
        self.sleep(.35)
        frame, labels = self._read()
        current = self._stage_button(frame, labels)
        if current is None or current[0] != first[0] or max(
            abs(a - b) for a, b in zip(current[1:], first[1:], strict=True)
        ) > .012:
            return self._fail("快速战斗关卡或按钮发生变化")
        stage, x, y = current
        self.info_set("活动关卡", stage)
        self.operate_click(x, y, after_sleep=.7)
        deadline = monotonic() + 6
        while monotonic() < deadline:
            _, labels = self._read()
            if self._dialog(labels):
                self.info_set("状态", "快速战斗弹窗已打开。")
                return True
            self.sleep(.25)
        return self._fail("未确认快速战斗弹窗")
