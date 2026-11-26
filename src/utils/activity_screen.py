"""Shared recognition of event battle screens and navigation controls."""

import re
from functools import cached_property

import numpy as np

from src.utils.ocr_utils import normalize_ocr_text

ACTIVITY_DAILY_NAME = "每日活动"
ACTIVITY_DAILY_OWNER = "ActivityDailyTask"
MODES = ("挑战战斗", "普通战斗")
STAGES = (15, 10, 5)
QUICK_BATTLE_STEP = "活动快速战斗"
HOME_BANNER_ROI = (0.68, 0.42, 0.99, 0.85)
STAGE_LIST = (0.122, 0.09, 0.158, 0.995)
AP_COUNTER = (0.891, 0.016, 0.956, 0.082)
QUICK_BUTTON_BOUNDS = (0.49, 0.88, 0.67, 0.96)
FREE_AP_SWITCH = (0.650, 0.351)
MENU_BACK_POINT = (0.090, 0.046)
BATTLE_BACK_POINT = (0.087, 0.045)
RESULT_FOOTER = (0.38, 0.85, 0.62, 0.98)


class ActivityScreen:
    def __init__(self, frame, boxes):
        self.frame = frame
        self.height, self.width = frame.shape[:2]
        self.labels = [
            (normalize_ocr_text(box.name), self.point(box))
            for box in boxes
            if box.width > 0 and box.height > 0
        ]

    def point(self, box):
        return (
            (box.x + box.width / 2) / self.width,
            (box.y + box.height / 2) / self.height,
        )

    def find(self, pattern, bounds):
        left, top, right, bottom = bounds
        found = []
        for text, (x, y) in self.labels:
            if left < x < right and top < y < bottom:
                match = re.fullmatch(pattern, text)
                if match:
                    found.append((match, (x, y)))
        return found[0] if len(found) == 1 else (None, None)

    def brightness(self, x, y, dx=0.009, dy=0.004):
        patch = self.frame[
            round((y - dy) * self.height) : round((y + dy) * self.height),
            round((x - dx) * self.width) : round((x + dx) * self.width),
        ]
        return float(np.median(patch)) if patch.size else 0.0

    @cached_property
    def banner(self):
        unlock_phrases = ("普通战斗解锁", "挑战战斗解锁", "魔兽追踪者解锁", "魔兽追蹤者解锁")
        countdown_pattern = r"d-(?:[0-9]{1,3}|day)(?!\d)"
        banners = [
            (x, (y + countdown_y) / 2)
            for text, (x, y) in self.labels
            if any(phrase in text for phrase in unlock_phrases)
            for label, (countdown_x, countdown_y) in self.labels
            if re.search(countdown_pattern, label)
            and abs(countdown_x - x) < 0.08
            and 0.025 < y - countdown_y < 0.18
        ]
        if not banners:
            banners = [
                (x, y)
                for text, (x, y) in self.labels
                if any(phrase in text for phrase in unlock_phrases)
                and re.search(countdown_pattern, text)
            ]
        if not banners:
            banners = [
                (x, max(0.40, y - 0.055))
                for text, (x, y) in self.labels
                if any(phrase in text for phrase in unlock_phrases)
                and x > 0.60
                and 0.40 < y < 0.88
            ]
        return banners[0] if len(banners) == 1 else None

    @cached_property
    def menu(self):
        _, shop = self.find("活动商店", (0.68, 0.83, 0.81, 0.94))
        _, normal = self.find(MODES[1], (0.29, 0.83, 0.41, 0.94))
        _, challenge = self.find(MODES[0], (0.40, 0.83, 0.51, 0.94))
        if (
            shop
            and normal
            and challenge
            and max(abs(normal[1] - shop[1]), abs(challenge[1] - shop[1])) < 0.025
        ):
            # The battle icons are directly above the labelled ribbons.
            return {
                MODES[0]: (challenge[0], challenge[1] - 0.09),
                MODES[1]: (normal[0], normal[1] - 0.09),
            }
        return None

    @cached_property
    def menu_header(self):
        _, help_button = self.find("帮助", (0.135, 0.024, 0.217, 0.072))
        _, schedule = self.find("行程", (0.228, 0.024, 0.298, 0.072))
        return bool(help_button and schedule)

    @cached_property
    def menu_back(self):
        if not (self.menu and self.menu_header):
            return None
        _, y = MENU_BACK_POINT
        # The event menu uses a white circle with a dark chevron at the left.
        if (
            min(self.brightness(x, y, dx=0.0013, dy=0.003) for x in (0.080, 0.098)) >= 205
            and min(self.brightness(x, y, dx=0.0013, dy=0.003) for x in (0.085, 0.087, 0.089)) < 150
        ):
            return MENU_BACK_POINT
        return None

    @cached_property
    def activity_mode(self):
        title, _ = self.find(r".{0,2}(挑战战斗|普通战斗)[?？]?", (0.105, 0.012, 0.25, 0.09))
        _, clothes = self.find(r".{0,2}活动服装", (0.56, 0.014, 0.76, 0.09))
        _, countdown = self.find(r".{0,2}活动还剩\d+(?:天|小时|分钟).*", (0.68, 0.014, 0.87, 0.09))
        return title[1] if title and (clothes or countdown) else None

    @cached_property
    def battle(self):
        stage, _ = self.find(r"(挑战战斗|普通战斗)([1-9]|1[0-5])", (0.42, 0.085, 0.63, 0.165))
        if stage and self.activity_mode == stage[1]:
            return stage[1], int(stage[2])
        return None

    @cached_property
    def dialog(self):
        _, title = self.find("快速战斗", (0.43, 0.23, 0.57, 0.29))
        _, free = self.find(r"仅使用免费活动ap", (0.33, 0.309, 0.48, 0.354))
        stage, _ = self.find(r"(挑战战斗|普通战斗)([1-9]|1[0-5])", (0.44, 0.283, 0.56, 0.323))
        return (stage[1], int(stage[2])) if title and free and stage else None

    @cached_property
    def result(self):
        _, title = self.find(
            r"result|reward|快速战斗结果|战斗结果|获得奖励", (0.36, 0.17, 0.64, 0.45)
        )
        _, count = self.find(r"获得[1-9]\d*种", (0.40, 0.29, 0.60, 0.48))
        # The footer overlaps the dimmed 角色 button; OCR can join its leading text.
        _, footer = self.find(r"(?:角(?:色)?)?点击画面即可返回[。.]?", RESULT_FOOTER)
        return bool(title or count or footer), footer if title or count else None

    @cached_property
    def stage_page(self):
        _, modal = self.find(r"取消|确认|确定", (0.34, 0.32, 0.68, 0.81))
        return self.battle if not (self.dialog or self.result[0] or modal) else None

    @cached_property
    def battle_back(self):
        if not self.stage_page:
            return None
        patch = self.frame[
            round(0.027 * self.height) : round(0.064 * self.height),
            round(0.077 * self.width) : round(0.094 * self.width),
        ]
        if patch.size:
            white = (np.min(patch, axis=2) > 185) & (np.ptp(patch, axis=2) < 28)
            if np.median(patch) < 135 and 0.025 <= white.mean() <= 0.32:
                return BATTLE_BACK_POINT
        return None

    @cached_property
    def dialog_cancel(self):
        if self.dialog:
            _, cancel = self.find("取消", (0.37, 0.72, 0.46, 0.78))
            if cancel and self.brightness(cancel[0], cancel[1] - 0.012) >= 160:
                return cancel
        return None

    @cached_property
    def home_return_control(self):
        """Return the recognized activity page and its safe exit control, if visible."""
        if self.dialog:
            return "取消活动快速战斗", self.dialog_cancel
        if self.activity_mode:
            result_visible, dismiss = self.result
            if result_visible:
                return "关闭活动战斗结果", dismiss
            return f"退出活动{self.activity_mode}列表", self.battle_back
        if self.menu:
            return "退出活动主页", self.menu_back
        return None

    def quick_button(self):
        _, point = self.find("快速战斗", QUICK_BUTTON_BOUNDS)
        if point:
            fill = self.brightness(point[0], point[1] - 0.015)
            if fill >= 165:
                return True, point
            if 65 <= fill <= 125:
                return False, point
        return None

    @cached_property
    def ap(self):
        number, _ = self.find(r"(\d{1,3})/(\d{1,3})", AP_COUNTER)
        if number:
            count, capacity = map(int, number.groups())
            if 0 <= count <= capacity and capacity > 0:
                return count, capacity
        return None

    def free_only(self):
        left = self.brightness(0.644, FREE_AP_SWITCH[1], dx=0.0025)
        right = self.brightness(0.656, FREE_AP_SWITCH[1], dx=0.0025)
        if max(left, right) >= 165 and abs(left - right) >= 55:
            return right > left
        return None

    def max_button(self):
        _, point = self.find("max", (0.57, 0.52, 0.65, 0.58))
        return point

    def submission(self):
        count, _ = self.find(r"战斗([1-9]\d*)次奖励", (0.42, 0.38, 0.58, 0.45))
        cost, point = self.find(r"战斗[^\d]{0,3}([1-9]\d*)", (0.47, 0.72, 0.61, 0.78))
        if (
            count
            and cost
            and count[1] == cost[1]
            and self.brightness(point[0], point[1] - 0.012) >= 165
        ):
            return int(count[1]), point
        return None

    def stage_rows(self):
        left, top, right, bottom = STAGE_LIST
        rows = {}
        for text, (x, y) in self.labels:
            if left < x < right and top < y < bottom:
                digit = re.fullmatch(r"([1-9]|1[0-5])[>»〉》+]*", text)
                if digit:
                    rows.setdefault(int(digit[1]), []).append((x, y))
        return {stage: points[0] for stage, points in rows.items() if len(points) == 1}
