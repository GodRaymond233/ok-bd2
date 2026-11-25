"""Run one Last Night challenge from the home, collection or challenge screens."""

from time import monotonic

from src.icons import TaskIcon
from src.tasks.CartridgeChallengeTask import CartridgeChallengeTask, red_silhouette
from src.tasks.HomeRewardTasks import _button_brightness
from src.utils.vision_models import TemplateSpec

DOOM_CARD = TemplateSpec(
    "doom_card_cover", "doom_card_cover.png", .85,
    relative_roi=(.045, .10, .99, .92), min_zncc_score=.72,
)
FIELD_SWORDS = TemplateSpec(
    "doom_field_swords", "doom_field_swords.png", .86,
    relative_roi=(.16, .15, .93, .84),
)


class DoomBookTask(CartridgeChallengeTask):
    title = "末日之书挑战"
    description_text = "用当前阵容运行一次末日之书战斗。"
    title_labels = ("末日之书",)
    task_icon = TaskIcon.FLAG
    status_keys = ["状态", "Log", "Warning", "Error"]

    def _on_doom_cartridge(self, frame, labels):
        return self._on_field(labels) or self._on_page(labels)

    @staticmethod
    def _on_field(labels):
        hud = [text for text, x, y, _ in labels if .12 < x < .36 and .06 < y < .16]
        return all(any(text.startswith(word) for text in hud) for word in ("战斗分布", "最高伤害"))

    @staticmethod
    def _record_banner(labels):
        damage = any(text == "最高伤害" and .55 < x < .82 and .44 < y < .57
                     for text, x, y, _ in labels)
        titles = [(x, y) for text, x, y, _ in labels
                  if text == "创造新纪录" and .55 < x < .82 and .29 < y < .44]
        return titles[0] if damage and len(titles) == 1 else None

    def _open_panel(self, frame, labels):
        for _ in range(2):
            if not self._on_field(labels):
                return None
            marker = self._match(red_silhouette(frame), FIELD_SWORDS)
            if not self._passes(marker, FIELD_SWORDS):
                return None
            height, width = frame.shape[:2]
            x, y = marker.center
            self.operate_click(x / width, y / height, after_sleep=.5)
            page = self._wait_page()
            if page is not None:
                return page
            frame, labels = self._read()
        return None

    def _start_button(self, frame, labels):
        if not self._on_page(labels) or not any(
            text.startswith("最高伤害") and .43 < x < .68 and .08 < y < .18
            for text, x, y, _ in labels
        ):
            return None
        if any(text in {"确认", "确定", "取消", "购买"} for text, _, _, _ in labels):
            return None
        buttons = [(x, y, box) for text, x, y, box in labels
                   if text == "去战斗" and .76 < x < .94 and .88 < y < .96]
        if len(buttons) != 1:
            return None
        x, y, box = buttons[0]
        brightness = _button_brightness(frame, box)
        return (x, y) if brightness is not None and brightness >= 175 else None

    def _finish_battle(self):
        deadline = monotonic() + 120
        battle_seen = False
        record_clicked = False
        leave_clicked = False
        skip_clicked = False
        returned_frames = 0
        while monotonic() < deadline:
            frame, labels = self._read()
            if (battle_seen or record_clicked or leave_clicked) and (
                self._on_page(labels) or self._on_field(labels)
            ):
                returned_frames += 1
                if returned_frames == 2:
                    return self._finish()
            else:
                returned_frames = 0

            record = self._record_banner(labels)
            if record is not None and not record_clicked:
                self.operate_click(*record, after_sleep=.8)
                record_clicked = True
                continue

            result_title = any(
                text in {"result", "战斗结果"} and y < .65 for text, _, y, _ in labels
            )
            leave = [(x, y) for text, x, y, _ in labels
                     if text == "离开" and x > .72 and y > .88]
            if result_title and len(leave) == 1 and not leave_clicked:
                self.operate_click(*leave[0], after_sleep=.8)
                leave_clicked = True
                continue

            battle_on_frame = any(
                ("攻击中" in text or text.startswith("还剩")) and x < .32 and y > .8
                for text, x, y, _ in labels
            )
            battle_seen |= battle_on_frame
            if battle_on_frame and not skip_clicked:
                skip = [(x, y, box) for text, x, y, box in labels
                        if text == "跳过" and x > .7 and y < .14]
                if len(skip) == 1:
                    x, y, box = skip[0]
                    brightness = _button_brightness(frame, box)
                    if brightness is not None and brightness >= 180:
                        self.operate_click(x, y, after_sleep=.5)
                        skip_clicked = True
                        continue
            self.sleep(.5)
        return self._fail("未确认战斗结算")

    def run(self):
        frame, labels = self._read()
        if not self._on_page(labels) and not self._on_field(labels):
            cartridge = self._enter_cartridge(frame, labels, DOOM_CARD, self._on_doom_cartridge)
            if cartridge is None:
                return self._fail("未确认末日之书卡带入场")
            frame, labels = cartridge
        if not self._on_page(labels):
            page = self._open_panel(frame, labels)
            if page is None:
                return self._fail("未确认末日之书挑战面板")
            frame, labels = page
        point = self._start_button(frame, labels)
        if point is None:
            return self._fail("未确认可用的「去战斗」按钮")
        self.info_set("状态", "正在挑战末日之书")
        self.operate_click(*point, after_sleep=1.0)
        return self._finish_battle()
