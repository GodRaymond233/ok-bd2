"""Home-page navigation, pass rewards and arcade browsing for the PC client."""
from time import monotonic

import numpy as np

from src.icons import TaskIcon
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.mission_rewards import reward_popup
from src.tasks.task_vision_mixin import TaskVisionMixin
from src.utils.ocr_utils import normalize_ocr_text
from src.utils.vision_models import TemplateSpec

PASS_CARD_BADGE = TemplateSpec(
    name="pass_card_badge", file_name="pass_reward_badge.png",
    relative_roi=(.24, .18, .29, .79), threshold=.88,
)
PASS_TASK_BADGE = TemplateSpec(
    name="pass_task_badge", file_name="pass_task_badge.png",
    relative_roi=(.82, .23, .87, .74), threshold=.88,
)
CLAIM_LABELS = {"全部领取", "全部获得"}


class HomePageTask(TaskVisionMixin, BaseBD2Task):
    title = ""
    description_text = ""
    task_icon = TaskIcon.MAIL
    title_labels = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.title
        self.description = self.description_text
        self.icon = self.task_icon
        self.group_name = "上游扩展"
        self.group_icon = TaskIcon.CALENDAR
        self.visible = True
        self._init_vision_state()

    def _read(self, frame=None):
        if frame is None:
            frame = self.capture_frame()
        height, width = frame.shape[:2]
        labels = [
            (normalize_ocr_text(box.name, alnum_only=True),
             (box.x + box.width / 2) / width, (box.y + box.height / 2) / height, box)
            for box in self.ocr(frame=frame, threshold=.55, target_height=1080, log=False)
            if box.width > 0 and box.height > 0
        ]
        return frame, labels

    def _on_page(self, labels):
        return any(text in self.title_labels and x < .40 and y < .12
                   for text, x, y, _ in labels)

    def _home_entry(self, frame, labels):
        points = [(x, y) for text, x, y, _ in labels if text in self.title_labels]
        return points[0] if len(points) == 1 else None

    def _wait_page(self):
        deadline = monotonic() + 10
        while monotonic() < deadline:
            frame, labels = self._read()
            if self._on_page(labels):
                return frame, labels
            self.sleep(.4)
        return None

    def _start(self):
        frame, labels = self._read()
        if self._on_page(labels):
            return frame, labels
        if not self._frame_confirms_home(frame, self.name):
            if not self.prepare_home(self.name):
                return None
            frame, labels = self._read()
            if not self._frame_confirms_home(frame, self.name):
                return None
        point = self._home_entry(frame, labels)
        if point is None:
            return None
        self.operate_click(*point, after_sleep=.8)
        return self._wait_page()

    def _fail(self, stage):
        self.info_set("状态", f"{self.name}：{stage}，请检查游戏画面。")
        return False

    def _finish(self):
        if not self.auto_return_main_home(notify_failure=False):
            return self._fail("未确认返回主页")
        self.log_completion(f"{self.name}完成。")
        return True


def _button_brightness(frame, box):
    height, width = frame.shape[:2]
    # Sample the fill just below the label so that the dark glyphs do not
    # make a white claim button read as disabled.
    x0 = max(0, round(box.x + box.width * .15))
    x1 = min(width, round(box.x + box.width * .85))
    y0 = max(0, round(box.y + box.height))
    y1 = min(height, round(y0 + box.height * .35))
    fill = frame[y0:y1, x0:x1, :3]
    return float(np.median(fill)) if fill.size else None


class _BulkRewardTask(HomePageTask):
    waits_for_login = True

    claim_box = (.53, .5, .96, .99)

    def _claim_current(self):
        deadline = monotonic() + 12
        empty_frames = 0
        clicked = False
        dismissed = False
        while monotonic() < deadline:
            frame, labels = self._read()
            popup = reward_popup(frame, [box for _, _, _, box in labels])
            if popup is not None and clicked and not dismissed:
                self.operate_click(*popup, after_sleep=.7)
                dismissed = True
                empty_frames = 0
            elif self._on_page(labels):
                if any(text in ("确认", "取消", "确定") for text, _, _, _ in labels):
                    return False
                left, top, right, bottom = self.claim_box
                buttons = [(x, y, box) for text, x, y, box in labels
                           if text in CLAIM_LABELS and left < x < right and top < y < bottom]
                if len(buttons) == 1:
                    x, y, box = buttons[0]
                    brightness = _button_brightness(frame, box)
                    if brightness is not None and 65 <= brightness <= 152:
                        empty_frames += 1
                        if empty_frames == 2:
                            return True
                    else:
                        empty_frames = 0
                    if brightness is not None and brightness >= 180 and not clicked:
                        self.operate_click(x, y, after_sleep=.8)
                        clicked = True
                elif not buttons and dismissed:
                    return True
                else:
                    empty_frames = 0
            else:
                empty_frames = 0
            self.sleep(.4)
        return False


class PassRewardTask(_BulkRewardTask):
    title = "领取通行证"
    description_text = "逐张检查带红点的通行证，领取奖励页中的全部可领奖励。"
    title_labels = ("通行证",)
    task_icon = TaskIcon.CERTIFICATE

    def _claim_pass(self):
        claimed = self._claim_current()
        current = self._wait_page()
        if current is None:
            return False
        frame, _ = current
        badge = self._match(frame, PASS_TASK_BADGE)
        if not self._passes(badge, PASS_TASK_BADGE):
            return claimed
        height, width = frame.shape[:2]
        x, y = badge.center
        self.operate_click(x / width, y / height, after_sleep=.8)
        return self._claim_current()

    def run(self):
        page = self._start()
        if page is None:
            return self._fail("未识别到通行证")

        scrolls = 0
        processed = set()
        for _ in range(12):
            frame, labels = page
            cards = [(text, x, y) for text, x, y, _ in labels
                     if text.endswith("通行证") and "购买" not in text
                     and .12 < x < .32 and .18 < y < .83]
            if not cards:
                return self._fail("未识别到通行证列表")
            badge = self._match(frame, PASS_CARD_BADGE)
            if self._passes(badge, PASS_CARD_BADGE):
                badge_y = badge.center[1] / frame.shape[0]
                candidates = [card for card in cards if 0 < card[2] - badge_y < .095]
                if len(candidates) != 1:
                    return self._fail("未确认红点对应的通行证")
                name, x, y = candidates[0]
                if name in processed:
                    return self._fail(f"「{name}」仍有红点，请检查任务页或奖励条件")
                self.info_set("当前通行证", name)
                self.operate_click(x, y, after_sleep=.8)
                opened = self._wait_page()
                if opened is None or not any(
                    text in ("基础", "基本") and x > .4 for text, x, _, _ in opened[1]
                ):
                    return self._fail(f"未确认「{name}」的奖励面板")
                if not self._claim_pass():
                    return self._fail(f"未确认「{name}」的奖励已领完")
                processed.add(name)
            elif scrolls < 2:
                _, x, y = cards[-1]
                self.scroll_client((x, y), -1, count=3, after_sleep=.8)
                scrolls += 1
            else:
                self.info_set("已检查通行证", "、".join(sorted(processed)) or "无带红点的卡片")
                return self._finish()
            page = self._wait_page()
            if page is None:
                return self._fail("未确认通行证页面")
        return self._fail("已达到本轮检查上限")


class ArcadeBrowseTask(HomePageTask):
    waits_for_login = True

    title = "浏览街机菜单"
    description_text = "访问街机游戏页面并返回主页。"
    title_labels = ("街机游戏",)
    task_icon = TaskIcon.GAME

    def run(self):
        if self._start() is None:
            return self._fail("未确认街机游戏页面")
        return self._finish()
