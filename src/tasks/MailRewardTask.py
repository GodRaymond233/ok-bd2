"""Claim ordinary and product mail from the PC mailbox."""
import re
from time import monotonic

import numpy as np

from src.icons import TaskIcon
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.mission_rewards import reward_popup
from src.tasks.task_vision_mixin import TaskVisionMixin
from src.utils.ocr_utils import normalize_ocr_text
from src.utils.vision_models import TemplateSpec

MAIL_ICON = TemplateSpec(
    name="home_mail_icon", file_name="home_mail_icon.png",
    relative_roi=(.70, 0, 1, .13), threshold=.86, reference_scale=1.5,
)
MAIL_TABS = (("一般", "普通", "普通邮箱", "一般邮件"), ("商品", "商品邮箱", "商品邮件"))


def _button_brightness(frame, box):
    height, width = frame.shape[:2]
    left = max(0, round(box.x + box.width * .15))
    right = min(width, round(box.x + box.width * .85))
    top = max(0, round(box.y + box.height))
    bottom = min(height, round(top + box.height * .35))
    fill = frame[top:bottom, left:right, :3]
    return float(np.median(fill)) if fill.size else None


def _mail_tabs(frame, labels):
    if any(text in ("确认", "取消", "确定") for text, _, _, _ in labels):
        return None
    points, levels = [], []
    for tab in MAIL_TABS:
        matches = [(center_x, center_y, box) for text, center_x, center_y, box in labels
                   if text in tab and center_x < .32 and .09 < center_y < .38]
        if len(matches) != 1:
            return None
        center_x, center_y, box = matches[0]
        pixels = frame[max(0, round(box.y)):round(box.y + box.height),
                       max(0, round(box.x)):round(box.x + box.width), :3]
        if not pixels.size:
            return None
        points.append((center_x, center_y))
        levels.append(np.percentile(pixels, (15, 50, 85)))
    return points, np.array(levels)


class MailboxTaskBase(TaskVisionMixin, BaseBD2Task):
    waits_for_login = True
    title = "领取邮件"
    description_text = "领取普通邮箱和商品邮箱的奖励。"
    task_icon = TaskIcon.MAIL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.title
        self.description = self.description_text
        self.icon = self.task_icon
        self.group_name = "日常/周常"
        self.group_icon = TaskIcon.CALENDAR
        self.visible = True
        self._init_vision_state()

    def _read_mailbox(self):
        frame = self.capture_frame()
        height, width = frame.shape[:2]
        labels = [
            (normalize_ocr_text(box.name, alnum_only=True),
             (box.x + box.width / 2) / width, (box.y + box.height / 2) / height, box)
            for box in self.ocr(frame=frame, threshold=.55, target_height=1080, log=False)
            if box.width > 0 and box.height > 0
        ]
        return frame, labels

    @staticmethod
    def _on_mailbox(labels):
        # OCR can include the envelope icon as a leading Latin letter.
        return any(
            center_x < .40 and center_y < .12
            and re.fullmatch(r"[a-z]?(?:(?:普通|商品)?邮箱|信箱)", text)
            for text, center_x, center_y, _ in labels
        )

    def _wait_mailbox(self):
        deadline = monotonic() + 10
        while monotonic() < deadline:
            frame, labels = self._read_mailbox()
            if self._on_mailbox(labels):
                return frame, labels
            self.sleep(.4)
        return None

    def _open_mailbox(self):
        frame, labels = self._read_mailbox()
        if self._on_mailbox(labels):
            return frame, labels
        if not self._frame_confirms_home(frame, self.name):
            if not self.prepare_home(self.name):
                return None
            frame, _ = self._read_mailbox()
            if not self._frame_confirms_home(frame, self.name):
                return None
        match = self._match(frame, MAIL_ICON)
        if not self._passes(match, MAIL_ICON):
            return None
        height, width = frame.shape[:2]
        self.operate_click(
            match.center[0] / width, match.center[1] / height, name="打开邮箱", after_sleep=.8,
        )
        return self._wait_mailbox()

    def _fail(self, stage):
        self.info_set("状态", f"{self.name}：{stage}，请检查游戏画面。")
        return False

    def _select_mail_tab(self, page, index):
        for target in (index, 1 - index, index):
            previous = _mail_tabs(*page)
            if previous is None:
                return None
            shape = page[0].shape
            self.operate_click(*previous[0][target], after_sleep=1.2)
            page = self._wait_mailbox()
            current = _mail_tabs(*page) if page else None
            if current is None or page[0].shape != shape:
                return None
            if target != index:
                continue
            before, after = previous[1], current[1]
            contrast = after[index] - after[1 - index]
            direction = np.sign(contrast)
            evidence = np.minimum.reduce((
                np.abs(contrast), direction * (before[1 - index] - before[index]),
                direction * (after[index] - before[index]),
                direction * (before[1 - index] - after[1 - index]),
            ))
            band = int(evidence.argmax())
            if evidence[band] >= 20:
                return band, direction[band]
        return None

    def _claim_mail_tab(self, index, selection):
        deadline = monotonic() + 12
        empty_frames = 0
        clicked = dismissed = False
        while monotonic() < deadline:
            frame, labels = self._read_mailbox()
            popup = reward_popup(frame, [box for _, _, _, box in labels])
            if popup is not None:
                empty_frames = 0
                if not clicked or dismissed:
                    self.sleep(.4)
                    continue
                self.operate_click(*popup, after_sleep=.7)
                dismissed = True
            elif self._on_mailbox(labels):
                tabs = _mail_tabs(frame, labels)
                band, direction = selection
                if tabs is None:
                    return False
                contrast = tabs[1][index, band] - tabs[1][1 - index, band]
                if contrast * direction < 20:
                    return False
                buttons = [(center_x, center_y, box) for text, center_x, center_y, box in labels
                           if text in ("全部领取", "全部获得") and .65 < center_x < .97
                           and .79 < center_y < .99]
                if len(buttons) == 1:
                    center_x, center_y, box = buttons[0]
                    brightness = _button_brightness(frame, box)
                    if brightness is not None and 65 <= brightness <= 152:
                        empty_frames += 1
                        if empty_frames == 2:
                            return True
                    else:
                        empty_frames = 0
                    if brightness is not None and brightness >= 180 and not clicked:
                        self.operate_click(center_x, center_y, after_sleep=.8)
                        clicked = True
                elif not buttons and dismissed:
                    return True
                else:
                    empty_frames = 0
            else:
                empty_frames = 0
            self.sleep(.4)
        return False

    def _claim_mail(self, *, include_products=True):
        page = self._open_mailbox()
        if page is None:
            return self._fail("未识别到邮箱")
        tabs = MAIL_TABS if include_products else MAIL_TABS[:1]
        for index, tab in enumerate(tabs):
            selection = self._select_mail_tab(page, index)
            if selection is None:
                return self._fail(f"未确认选中「{tab[0]}」邮箱")
            if not self._claim_mail_tab(index, selection):
                return self._fail(f"未确认「{tab[0]}」邮件已领完")
            page = self._wait_mailbox()
            if page is None:
                return self._fail("未确认邮箱页面")
        if not self.auto_return_main_home(notify_failure=False):
            return self._fail("从邮箱返回主页")
        return True


class MailRewardTask(MailboxTaskBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config["领取商品邮箱"] = True
        self.config_description["领取商品邮箱"] = "同时检查商品邮箱的可领取奖励。"

    def run(self):
        if not self._claim_mail(include_products=self.config.get("领取商品邮箱", True)):
            return False
        self.log_completion(f"{self.name}完成。")
        return True
