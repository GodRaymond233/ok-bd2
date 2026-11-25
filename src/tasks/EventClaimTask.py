"""Claim completed rewards on red-badged PC event cards."""

import re
from time import monotonic

import cv2
import numpy as np

from src.icons import TaskIcon
from src.tasks import run_history
from src.tasks.HomeRewardTasks import HomePageTask, _button_brightness
from src.tasks.mission_rewards import reward_popup
from src.utils.image_utils import (
    independent_pixel_valid_matches,
    relative_roi_frame,
    template_match_response,
    to_gray,
)
from src.utils.vision_models import TemplateSpec

LIST_REGION = (.16, .24, .28, .76)
LIST_SCROLL_POINT = (.22, .50)
BULK_LABELS = {"全部领取", "全部获得", "全部領取", "全部獲得"}
PAID_LABELS = ("付费", "付費", "购买", "購買")
ROW_HAND = TemplateSpec("event_reward_hand", "event_reward_hand.png", .84)
ROW_REWARD_REGION = (.75, .235, .845, .785)
CONFIRM_LABELS = {"确认", "确定", "取消", "確認", "確定"}
FREE_SPIN = re.compile(r"(?:旋)?[转轉]1次(?:1次免[费費])?")
FREE_COST = {"1次免费", "1次免費"}
WHEEL_TITLE = {"转盘活动", "轉盤活動"}
SPINS_TAKEN = re.compile(r"(?:累积参与|累计参与|累積參與|累計參與)(\d{1,5})次")


class EventClaimTask(HomePageTask):
    waits_for_login = True

    title = "领取活动奖励"
    description_text = "检查活动红点并领取已完成的活动奖励。"
    title_labels = ("活动", "活動")
    task_icon = TaskIcon.CERTIFICATE
    status_keys = [
        "状态", "当前活动", "活动领奖", "免费转盘", "待检查活动", "Log", "Warning", "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config["领取免费转盘"] = False

    def _home_entry(self, frame, labels):
        points = [(x, y) for text, x, y, _ in labels
                  if text in self.title_labels and .38 < x < .54 and y > .88]
        return points[0] if len(points) == 1 else None

    @staticmethod
    def _list_captions(labels):
        return [(text, x, y) for text, x, y, _ in labels
                if .16 < x < .275 and .24 < y < .77 and len(text) >= 3]

    def _badged_cards(self, frame, labels):
        h, w = frame.shape[:2]
        left, right = round(w * .257), round(w * .278)
        top, bottom = round(h * .243), round(h * .755)
        b, g, r = cv2.split(frame[top:bottom, left:right, :3])
        red = cv2.bitwise_and(
            cv2.inRange(r, 160, 255),
            cv2.inRange(cv2.subtract(r, cv2.max(b, g)), 80, 255),
        )
        _, _, dimensions, centers = cv2.connectedComponentsWithStats(red)
        captions = self._list_captions(labels)
        candidates = []
        for (_, _, bw, bh, area), (_, cy) in zip(dimensions[1:], centers[1:]):
            size = bh / h
            if not .010 <= size <= .027 or not .60 <= bw / bh <= 1.50:
                continue
            if area < .36 * bw * bh:
                continue
            badge_y = (top + cy) / h
            matches = [caption for caption in captions if .025 < caption[2] - badge_y < .079]
            if len(matches) == 1:
                candidates.append(matches[0])
        return sorted(set(candidates), key=lambda card: card[2])

    @staticmethod
    def _list_image(frame):
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = LIST_REGION
        image = frame[round(y0 * h):round(y1 * h), round(x0 * w):round(x1 * w), :3]
        return cv2.resize(image, (60, 120), interpolation=cv2.INTER_AREA)

    def _row_points(self, frame):
        reference = cv2.resize(frame[:, :, :3], (1920, 1080))
        left, top, region = relative_roi_frame(reference, ROW_REWARD_REGION)
        search = to_gray(region)
        template = self._load_template(ROW_HAND)
        mask = self._load_template_mask(ROW_HAND)
        response = template_match_response(search, template, mask)
        matches = independent_pixel_valid_matches(
            response, search, template, mask, template_threshold=ROW_HAND.threshold,
            pixel_threshold=.83, zncc_threshold=.78, max_matches=12,
        )
        height, width = template.shape[:2]
        points = []
        for match in matches:
            x, y = match.location
            blue, green, red = cv2.split(region[y:y + height, x:x + width])
            blue_ring = (blue > 170) & (cv2.subtract(blue, red) > 45)
            blue_ring &= cv2.subtract(blue, green) > 12
            if float(blue_ring.mean()) >= .07:
                points.append(((left + x + width / 2) / 1920, (top + y + height / 2) / 1080))
        return sorted(points, key=lambda point: point[1])

    def _claim_event(self, name):
        spin = self._claim_free_spin(name) if self.config.get("领取免费转盘", False) else None
        if spin not in {None, "已转盘"}:
            return spin
        bulk = self._claim_bulk(name)
        if bulk not in {"已领取", "暂无可领奖励", "逐条领取", "未识别到批量领取按钮"}:
            return bulk
        if spin == "已转盘":
            bulk = "已领取"
        return self._claim_rows(name, bulk)

    @staticmethod
    def _spin_count(labels):
        counts = [int(match[1]) for text, x, y, _ in labels if .67 < x < .86 and .22 < y < .33
                  and (match := SPINS_TAKEN.fullmatch(text))]
        return counts[0] if len(counts) == 1 else None

    def _free_spin_control(self, frame, labels, name):
        if not self._on_page(labels) or not any(
            text == name and x > .29 and y < .80 for text, x, y, _ in labels
        ):
            return None
        if any(
            x > .29 and (text in CONFIRM_LABELS or any(word in text for word in PAID_LABELS))
            for text, x, _, _ in labels
        ):
            return None
        if not any(text in WHEEL_TITLE and .52 < x < .71 and .39 < y < .53
                   for text, x, y, _ in labels):
            return None
        buttons = [(text, x, y, box) for text, x, y, box in labels
                   if .60 < x < .71 and .65 < y < .74 and FREE_SPIN.fullmatch(text)]
        if len(buttons) != 1:
            return None
        text, x, y, box = buttons[0]
        costs = [label for label, cx, cy, _ in labels
                 if abs(cx - x) < .04 and .008 < cy - y < .045]
        signed_free = text.endswith(tuple(FREE_COST))
        if (costs and (len(costs) != 1 or costs[0] not in FREE_COST)) or (
            not costs and not signed_free
        ):
            return None
        value = frame[:, :, :3].max(axis=2)
        white = max(176, float(np.percentile(value[::5, ::5], 99)))
        brightness = _button_brightness(value[:, :, None], box)
        count = self._spin_count(labels)
        if brightness is None or brightness / white < .77 or count is None:
            return None
        return (x, y), count

    def _claim_free_spin(self, name):
        day = str(run_history.day_start_ts())
        step = f"免费转盘:{day}:{name}"
        prior = run_history.read_stage(self, step).get("state")
        if prior == "pending":
            return "免费转盘结果待核对"
        if prior == "done":
            return "已转盘"
        frame, labels = self._read()
        first = self._free_spin_control(frame, labels, name)
        if first is None:
            return None
        self.sleep(.35)
        frame, labels = self._read()
        second = self._free_spin_control(frame, labels, name)
        if second is None or second[1] != first[1] or not self._same_point(first[0], second[0]):
            return None
        point, before = second
        if day != str(run_history.day_start_ts()):
            return "免费转盘跨日待复核"
        evidence = {"previous_spins": before, "count": 1}
        run_history.save_stage(self, step, "pending", scope=day, evidence=evidence)
        self.operate_click(*point, after_sleep=.7)

        deadline = monotonic() + 20
        confirmed = 0
        dismissed = False
        while monotonic() < deadline:
            frame, labels = self._read()
            panel = [(text, x, y, box) for text, x, y, box in labels if x > .29]
            if any(word in text for text, _, _, _ in panel for word in PAID_LABELS):
                return "免费转盘出现付费提示"
            popup = reward_popup(frame, [box for _, _, _, box in panel if box is not None])
            if popup is not None and not dismissed:
                self.operate_click(*popup, after_sleep=.7)
                dismissed = True
                confirmed = 0
                continue
            if popup is None and any(text in CONFIRM_LABELS for text, _, _, _ in panel):
                return "免费转盘出现确认弹窗"
            on_card = self._on_page(labels) and any(name == text for text, _, _, _ in panel)
            increased = on_card and popup is None and self._spin_count(labels) == before + 1
            confirmed = confirmed + 1 if increased else 0
            if confirmed == 2:
                run_history.save_stage(self, step, "done", scope=day, evidence=evidence)
                self.info_set("免费转盘", f"{name}：已确认")
                return "已转盘"
            self.sleep(.5)
        return "免费转盘结果待核对"

    @staticmethod
    def _same_point(first, second):
        return max(abs(a - b) for a, b in zip(first, second)) <= .01

    def _claim_rows(self, name, bulk):
        deadline = monotonic() + 25
        previous = pending = None
        absent_frames = claimed = settled = 0
        closed = False
        while monotonic() < deadline:
            frame, labels = self._read()
            panel = [(text, x, y, box) for text, x, y, box in labels if x > .29]
            if any(word in text for text, _, _, _ in panel for word in PAID_LABELS):
                return "付费页面"
            if pending is not None and not closed:
                popup = reward_popup(frame, [box for _, _, _, box in panel if box is not None])
                if popup is not None:
                    self.operate_click(*popup, after_sleep=.7)
                    closed = True
                    continue
            if any(text in CONFIRM_LABELS for text, _, _, _ in panel):
                return "出现确认弹窗"
            if not self._on_page(labels) or not any(
                text == name and y < .80 for text, _, y, _ in panel
            ):
                previous = None
                absent_frames = settled = 0
                self.sleep(.4)
                continue

            points = self._row_points(frame)
            if pending is not None:
                gone = not any(self._same_point(point, pending) for point in points)
                settled = settled + 1 if gone else 0
                if settled >= 2:
                    claimed += 1
                    pending = previous = None
                    settled = 0
                    closed = False
            elif not points:
                previous = None
                absent_frames += 1
                if absent_frames >= 2:
                    return "已领取" if claimed else bulk.replace("逐条领取", "未确认逐条领取按钮")
            else:
                absent_frames = 0
                point = points[0]
                if previous is not None and self._same_point(point, previous):
                    self.operate_click(*point, after_sleep=.7)
                    pending = point
                previous = point
            self.sleep(.4)
        return "未确认逐条领取结果" if pending is not None else "逐条领取检查超时"

    def _claim_bulk(self, name):
        deadline = monotonic() + 12
        expected = None
        disabled_frames = 0
        submitted = False
        closed = False
        while monotonic() < deadline:
            frame, labels = self._read()
            panel = [(text, x, y, box) for text, x, y, box in labels if x > .29]
            if any(word in text for text, _, _, _ in panel for word in PAID_LABELS):
                return "付费页面"
            if submitted and not closed:
                popup = reward_popup(frame, [box for _, _, _, box in panel if box is not None])
                if popup is not None:
                    self.operate_click(*popup, after_sleep=.7)
                    closed = True
                    continue
            modal = any(text in CONFIRM_LABELS for text, _, _, _ in panel)
            if modal:
                return "出现确认弹窗"
            on_card = any(name == text and y < .80 for text, _, y, _ in panel)
            if not self._on_page(labels) or not on_card:
                expected = None
                disabled_frames = 0
                self.sleep(.4)
                continue

            buttons = [(x, y, box) for text, x, y, box in panel
                       if text in BULK_LABELS and .28 < y < .82]
            if not buttons and closed:
                return "已领取"
            if not buttons and not submitted and self._row_points(frame):
                return "逐条领取"
            if len(buttons) != 1:
                expected = None
                disabled_frames = 0
                self.sleep(.4)
                continue
            x, y, box = buttons[0]
            value = frame[:, :, :3].max(axis=2)
            brightness = _button_brightness(value[:, :, None], box)
            white = max(176, float(np.percentile(value[::5, ::5], 99)))
            ratio = brightness / white if brightness is not None else 0
            if .32 <= ratio <= .63:
                disabled_frames += 1
                if disabled_frames >= 2:
                    return "已领取" if submitted else "暂无可领奖励"
            else:
                disabled_frames = 0
            point = (x, y) if ratio >= .77 else None
            if point is not None and expected is not None and not submitted:
                if self._same_point(point, expected):
                    self.operate_click(*point, after_sleep=.7)
                    submitted = True
            expected = point
            self.sleep(.4)
        return "未确认领取结果" if submitted else "未识别到批量领取按钮"

    def run(self):
        page = self._start()
        if page is None:
            return self._fail("未确认活动页面")
        frame, labels = page
        if not self._list_captions(labels):
            return self._fail("未确认活动列表")
        self.scroll_client(LIST_SCROLL_POINT, 3, count=5, after_sleep=1)
        checked = {}
        stationary = 0
        for _ in range(32):
            frame, labels = self._read()
            if not self._on_page(labels) or not self._list_captions(labels):
                return self._fail("活动列表识别中断")
            candidates = [
                card for card in self._badged_cards(frame, labels) if card[0] not in checked
            ]
            if candidates:
                name, _, y = candidates[0]
                self.sleep(.35)
                frame, labels = self._read()
                if not self._on_page(labels):
                    return self._fail("活动页面识别中断")
                stable = [card for card in self._badged_cards(frame, labels)
                          if card[0] == name and abs(card[2] - y) < .012]
                if len(stable) == 1:
                    _, x, y = stable[0]
                    self.info_set("当前活动", name)
                    self.operate_click(x, y, after_sleep=.6)
                    checked[name] = self._claim_event(name)
                    stationary = 0
                continue

            before = self._list_image(frame)
            self.scroll_client(LIST_SCROLL_POINT, -3, count=1, after_sleep=1)
            frame, labels = self._read()
            if not self._on_page(labels) or not self._list_captions(labels):
                return self._fail("滚动后未确认活动列表")
            difference = cv2.absdiff(before, self._list_image(frame)).mean()
            stationary = stationary + 1 if difference < 1.3 else 0
            if stationary == 2:
                break
        else:
            return self._fail("已达到活动列表检查上限")

        self.info_set("活动领奖", sum(result == "已领取" for result in checked.values()))
        unchecked = [f"{name}（{result}）" for name, result in checked.items()
                     if result not in {"已领取", "暂无可领奖励"}]
        self.info_set("待检查活动", "、".join(unchecked) if unchecked else "无")
        return self._finish()
