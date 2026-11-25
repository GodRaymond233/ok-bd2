"""Refine the equipment already selected on the PC client's refine page."""

import re
from time import monotonic

from src.icons import TaskIcon
from src.tasks import run_history
from src.tasks.HomeRewardTasks import HomePageTask, _button_brightness


class GearRefineTask(HomePageTask):
    title = "装备单次精炼"
    description_text = "在已选装备的精炼分数页逐次提交并核对精炼。"
    title_labels = ("精炼装备分数",)
    task_icon = TaskIcon.SETTING
    status_keys = ["状态", "精炼材料消耗", "Log", "Warning", "Error"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config["单次精炼材料上限"] = 80
        self.config_type["单次精炼材料上限"] = {"min": 1, "step": 1}
        self.default_config["每日精炼次数上限"] = 1
        self.config_type["每日精炼次数上限"] = {"min": 1, "max": 10, "step": 1}

    @staticmethod
    def _result_seen(labels):
        return any(text == "精炼成功" and .18 < x < .82 and y < .16 for text, x, y, _ in labels)

    def _single_refine(self, frame, labels):
        if not self._on_page(labels) or self._result_seen(labels):
            return None
        if any(text in {"确认", "确定", "取消", "购买"} for text, _, _, _ in labels):
            return None
        if not any(text == "连续精炼" and x > .5 and y > .88 for text, x, y, _ in labels):
            return None

        buttons = [(text, x, y, box) for text, x, y, box in labels
                   if x > .7 and y > .88 and re.fullmatch(r"精炼(?:[1-9]\d*)?", text)]
        if len(buttons) != 1:
            return None
        text, x, y, box = buttons[0]
        prices = [text[2:]] if text != "精炼" else [
            label for label, cost_x, cost_y, _ in labels
            if 0 < cost_x - x < .12 and abs(cost_y - y) <= .02 and re.fullmatch(r"[1-9]\d*", label)
        ]
        brightness = _button_brightness(frame, box)
        if len(prices) != 1 or brightness is None or brightness < 180:
            return None
        return x, y, int(prices[0])

    def run(self):
        day = str(run_history.day_start_ts())
        step = f"单次精炼:{day}"
        prior = run_history.read_stage(self, step)
        if prior.get("state") == "pending":
            return self._fail("今日已有精炼提交待核对")
        completed = prior["evidence"].get("completed", 1) if prior.get("state") == "done" else 0
        if type(completed) is not int or not 0 <= completed <= 10:
            return self._fail("今日精炼记录待核对")
        limit = min(10, max(1, int(self.config.get("每日精炼次数上限", 1))))
        if completed >= limit:
            self.info_set("状态", f"今日已确认精炼 {completed} 次。")
            return True

        for index in range(completed, limit):
            if index > completed and not self._wait_for_refine_page():
                return self._fail("已确认上一笔精炼，精炼页尚未就绪")
            if not self._refine_once(day, step, index):
                return False
        self.info_set("状态", f"今日已确认精炼 {limit} 次。")
        self.log_completion("装备精炼完成。")
        return True

    def _wait_for_refine_page(self):
        deadline = monotonic() + 6
        while monotonic() < deadline:
            frame, labels = self._read()
            if self._single_refine(frame, labels) is not None:
                return True
            self.sleep(.25)
        return False

    def _refine_once(self, day, step, completed):
        frame, labels = self._read()
        first = self._single_refine(frame, labels)
        if first is None:
            return self._fail("未确认到单次精炼按钮")
        self.sleep(.35)
        frame, labels = self._read()
        second = self._single_refine(frame, labels)
        if second is None:
            return self._fail("复核时未确认到单次精炼按钮")
        x, y, cost = second
        if cost != first[2] or abs(x - first[0]) > .025 or abs(y - first[1]) > .02:
            return self._fail("两帧的精炼位置或消耗不一致")
        if cost > self.config.get("单次精炼材料上限", 80):
            return self._fail(f"当前精炼消耗 {cost}，超过设置上限")
        if day != str(run_history.day_start_ts()):
            return self._fail("日常已刷新，请重新确认装备")

        evidence = {"cost": cost, "completed": completed}
        run_history.save_stage(self, step, "pending", scope=day, evidence=evidence)
        self.info_set("精炼材料消耗", cost)
        self.operate_click(x, y, after_sleep=.2)
        deadline = monotonic() + 6
        while monotonic() < deadline:
            _, labels = self._read()
            if self._result_seen(labels):
                evidence = {"cost": cost, "completed": completed + 1}
                run_history.save_stage(self, step, "done", scope=day, evidence=evidence)
                return True
            self.sleep(.25)
        return self._fail("精炼已提交，未确认结果，请核对游戏内任务进度")
