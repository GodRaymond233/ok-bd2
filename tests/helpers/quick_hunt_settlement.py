from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from src.tasks.quick_hunt import (
    QUICK_HUNT_DIALOG_ROI,
    QUICK_HUNT_MENU_FOREGROUND_ROI,
    QUICK_HUNT_REWARD_ROI,
    QUICK_HUNT_REWARD_TITLE_ROI,
)
from src.tasks.QuickHuntTask import QuickHuntTask
from src.utils.image_utils import crop_relative
from tests.helpers import FakeClock


def settlement_task(frames, submission):
    task = object.__new__(QuickHuntTask)
    task.config = {"快速狩猎结算等待秒数": 5.0, "快速狩猎界面等待秒数": 4.0}
    clock = FakeClock()
    states = {}
    images = []
    for state in frames:
        image = np.zeros((108, 192, 3), dtype=np.uint8)
        if state in {"menu", "dim_menu", "bright_noise"}:
            crop_relative(image, QUICK_HUNT_MENU_FOREGROUND_ROI)[:] = (
                58 if state == "dim_menu" else 160
            )
        states[id(image)] = state
        images.append(image)
    sequence = iter(images)
    task.capture_frame = Mock(side_effect=lambda: next(sequence, images[-1]))
    task.info_set = Mock()
    task.log_info = Mock()
    task._save_flow_diagnostic = Mock()

    sleep = clock.sleep

    task.sleep = Mock(side_effect=sleep)
    task.operate_click = Mock(side_effect=lambda *_, after_sleep=0.0: sleep(after_sleep))
    vision = Mock()
    cancel = SimpleNamespace(name="取消", x=10, y=20, width=20, height=20)
    confirm = SimpleNamespace(name="确认", x=20, y=20, width=20, height=20)
    buttons = [cancel, SimpleNamespace(name="确定", x=80, y=70, width=30, height=12)]
    vision.ocr_boxes.side_effect = lambda frame, *_a, **_kw: {
        "ambiguous_depleted": [confirm, buttons[-1]],
        "missing_depleted": [],
    }.get(states[id(frame)], buttons)
    vision.click_client.side_effect = lambda *_, after_sleep=0: sleep(after_sleep)
    task._quick_vision = lambda: vision

    def snapshot(frame, *_):
        state = states[id(frame)]
        if state == "unchanged":
            return submission
        if state == "spent":
            return replace(submission, pool=84)
        if state == "changed_count":
            return replace(submission, count=2, cost=12)
        return None

    task._quick_hunt_dialog_snapshot = Mock(side_effect=snapshot)

    def ocr_text(frame, relative_roi, name):
        state = states[id(frame)]
        if state == "reward" and relative_roi == QUICK_HUNT_REWARD_ROI:
            return "点击画面即可返回。"
        if state == "reward_title" and relative_roi == QUICK_HUNT_REWARD_TITLE_ROI:
            return "REWARD 获得1种"
        if "depleted" in state and relative_roi == QUICK_HUNT_DIALOG_ROI:
            return "米饭不足"
        if state == "unknown_error" and relative_roi == QUICK_HUNT_DIALOG_ROI:
            return "无法进入"
        if state == "wrong_resource" and relative_roi == QUICK_HUNT_DIALOG_ROI:
            return "火把不足"
        if state in {"menu", "dim_menu"} and relative_roi == QUICK_HUNT_MENU_FOREGROUND_ROI:
            return "狩猎场 金币 史莱姆 圣石洞穴"
        return ""

    task._quick_hunt_ocr_text = Mock(side_effect=ocr_text)
    return task, vision, clock
