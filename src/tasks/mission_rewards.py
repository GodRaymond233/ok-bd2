"""Read the mission screen from one frame; unknown views never prove exhaustion."""
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from src.utils.ocr_utils import normalize_ocr_text

SECTIONS = ("每日任务", "每周任务")
DISABLED_BUTTON_ROI = (1580, 982, 1790, 1028)
DISABLED_BUTTON_FILE = (
    Path(__file__).resolve().parents[2]
    / "recognition-assets/template-assets/mission_rewards/all_obtain_disabled.png"
)

# OCR 会把左栏页签前面的进度徽标或相邻笔画一起读进文本："2 每周任务"、
# "0每日任务"，实测还出现过 "日每周任务"；偶尔也会繁简混排。
# 只剥数字前缀不够，但噪声必须很短，避免把 "每日任务完成数1/1" 当成页签。
_SECTION_CHAR_FIXES = str.maketrans({"務": "务", "週": "周", "毎": "每", "曰": "日"})
_SECTION_NOISE_LIMIT = 2


def _clean_section_text(text):
    return text.translate(_SECTION_CHAR_FIXES)


def _tab_matches(text, section):
    cleaned = _clean_section_text(text)
    return section in cleaned and len(cleaned) - len(section) <= _SECTION_NOISE_LIMIT


@dataclass(frozen=True)
class RewardView:
    state: str = "unknown"
    section: str | None = None
    point: tuple[float, float] | None = None
    tabs: tuple[tuple[str, tuple[float, float]], ...] = ()
    diagnostics: str = ""


def _point(box, width, height):
    return ((box.x + box.width / 2) / width, (box.y + box.height / 2) / height)


def _in(box, width, height, bounds):
    if getattr(box, "width", 0) <= 0 or getattr(box, "height", 0) <= 0:
        return False
    x, y = _point(box, width, height)
    return bounds[0] < x < bounds[2] and bounds[1] < y < bounds[3]


def home_mission_entry(boxes, width, height):
    """The labelled mission item in the confirmed home's bottom navigation."""
    entries = [box for box in boxes
               if normalize_ocr_text(box.name, alnum_only=True) == "任务"
               and _in(box, width, height, (.28, .85, .42, .98))]
    return _point(entries[0], width, height) if len(entries) == 1 else None


def _reward_popup(labels, width, height):
    reward_titles = [box for box, text in labels if text == "reward"
                     and _in(box, width, height, (.4, .2, .6, .36))]
    reward_counts = [box for box, text in labels if re.fullmatch(r"获得[1-9]\d*种", text)
                     and _in(box, width, height, (.4, .30, .6, .43))]
    dismiss = [box for box, text in labels if text == "点击画面即可返回"
               and _in(box, width, height, (.4, .85, .6, .97))]
    if len(reward_titles) == len(reward_counts) == len(dismiss) == 1:
        return RewardView("popup", point=_point(dismiss[0], width, height))
    popup = [box for box, text in labels
             if text in ("获得奖励", "获得道具", "获得物品", "奖励获得")
             and _in(box, width, height, (.25, .1, .75, .65))]
    if len(popup) == 1:
        if any(any(word in text for word in ("选择", "购买", "消耗"))
               and _in(box, width, height, (.2, .15, .8, .9)) for box, text in labels):
            return RewardView(diagnostics="奖励弹窗含选择/购买/消耗字样")
        confirms = [box for box, text in labels if text in ("确认", "确定")
                    and _in(box, width, height, (.3, .5, .7, .95))]
        point = _point(confirms[0], width, height) if len(confirms) == 1 else (.5, .9)
        return RewardView("popup", point=point)
    if any(text in ("确认", "取消", "确定") for _, text in labels):
        return RewardView(diagnostics="页面含确认/取消/确定字样")
    return None


def reward_popup(frame, boxes):
    height, width = frame.shape[:2]
    labels = [(box, normalize_ocr_text(box.name, alnum_only=True)) for box in boxes]
    popup = _reward_popup(labels, width, height)
    return popup.point if popup is not None and popup.state == "popup" else None


def mission_reward_view(frame, boxes, disabled_template):
    """Require the foreground title, sidebar, interval header and bulk button."""
    height, width = frame.shape[:2]
    labels = [(box, normalize_ocr_text(box.name, alnum_only=True)) for box in boxes]

    def unknown(reason):
        return RewardView(diagnostics=reason)

    popup = _reward_popup(labels, width, height)
    if popup is not None:
        return popup
    titles = [(box, _clean_section_text(text)) for box, text in labels
              if _clean_section_text(text) in SECTIONS
              and _in(box, width, height, (.12, .01, .26, .10))]
    intervals = [box for box, text in labels if text == "区间奖励"
                 and _in(box, width, height, (.28, .01, .43, .1))]
    buttons = [box for box, text in labels if text == "全部获得"
               and _in(box, width, height, (.81, .89, .94, .96))]
    tabs = []
    for section in SECTIONS:
        # 页签允许短噪声前后缀；同一页签被拆成多个框时取最靠左上的一个，
        # 不再因为 OCR 抖动把整页判成 unknown。
        matches = [box for box, text in labels if _tab_matches(text, section)
                   and _in(box, width, height, (.10, .11, .25, .25))]
        if not matches:
            return unknown(f"页签未识别:{section}")
        best = min(matches, key=lambda box: (box.y, box.x))
        tabs.append((section, _point(best, width, height)))
    if len(titles) != 1 or len(intervals) != 1 or len(buttons) != 1:
        return unknown(f"标题/区间/按钮数量异常:"
                       f"{len(titles)}/{len(intervals)}/{len(buttons)}")
    title, section = titles[0]
    title_crop = frame[max(0, int(title.y)):min(height, int(title.y + title.height)),
                       max(0, int(title.x)):min(width, int(title.x + title.width))]
    if title_crop.size == 0 or np.percentile(title_crop, 95) < 180:
        return unknown("标题区域过暗或不可裁剪")
    x1, y1, x2, y2 = DISABLED_BUTTON_ROI
    button = frame[round(y1 * height / 1080):round(y2 * height / 1080),
                   round(x1 * width / 1920):round(x2 * width / 1920)]
    if button.size == 0 or disabled_template is None:
        return unknown("按钮区域为空或缺少禁用态素材")
    resized = cv2.resize(button, (x2 - x1, y2 - y1), interpolation=cv2.INTER_AREA)
    difference = np.abs(resized.astype(np.float32) - disabled_template.astype(np.float32))
    # A small bright label must not be averaged away by the much larger grey fill.
    text_mask = np.zeros(disabled_template.shape[:2], dtype=bool)
    text_mask[9:34, 65:150] = (
        cv2.cvtColor(disabled_template[9:34, 65:150], cv2.COLOR_BGR2GRAY) < 65)
    disabled = (float(difference.mean()) <= 6 and float(np.mean(difference > 20)) <= .08
                and bool(text_mask.any()) and float(difference[text_mask].mean()) <= 8)
    return RewardView("empty" if disabled else "claim", section,
                      _point(buttons[0], width, height), tuple(tabs))


def home_pass_entry(boxes, width, height):
    """Find the pass shortcut on a confirmed home frame."""
    entries = [box for box in boxes
               if normalize_ocr_text(box.name, alnum_only=True) == "通行证"
               and _in(box, width, height, (.78, .24, .91, .31))]
    return _point(entries[0], width, height) if len(entries) == 1 else None


def _pass_card_selected(frame, label_y):
    """The selected card has a gold outline above and beside its label."""
    height, width = frame.shape[:2]
    top = round((label_y - .070) * height)
    margin = max(1, round(.009 * height))
    header = frame[max(0, top - margin):top + margin,
                   round(.174 * width):round(.266 * width)]
    edge = frame[top + margin:round((label_y - .010) * height),
                 round(.164 * width):round(.172 * width)]

    def gold(pixels):
        blue, green, red = cv2.split(pixels.astype(np.int16))
        return (red - blue > 30) & (green - blue > 15) & (green > 125)

    return (header.size > 0 and edge.size > 0
            and gold(header).mean(axis=1).max() > .75
            and gold(edge).mean(axis=0).max() > .75)


def season_pass_view(frame, boxes):
    """Read season and character pass cards and the selected pass's reward control."""
    height, width = frame.shape[:2]
    labels = [(box, normalize_ocr_text(box.name, alnum_only=True)) for box in boxes]
    popup = _reward_popup(labels, width, height)
    if popup is not None:
        return popup

    titles = [box for box, text in labels if text.endswith("通行证")
              and _in(box, width, height, (.13, .015, .205, .08))]
    if len(titles) != 1:
        return RewardView()
    cards = [(box, text) for box, text in labels if text.endswith(("赛季通行证", "角色通行证"))
             and "指南" not in text and _in(box, width, height, (.165, .30, .276, .73))]
    if len({text for _, text in cards}) != len(cards):
        return RewardView()
    tabs = []
    for box, text in cards:
        x, y = _point(box, width, height)
        tabs.append((text, (x, y - .03)))
    tabs = tuple(tabs)
    selected = [text for box, text in cards
                if _pass_card_selected(frame, _point(box, width, height)[1])]
    if len(selected) != 1:
        guides = [box for box, text in labels if text.startswith("指南通行证")
                  and _in(box, width, height, (.165, .30, .276, .38))]
        if len(guides) == 1 and _pass_card_selected(frame, _point(guides[0], width, height)[1]):
            return RewardView("list", tabs=tabs)
        return RewardView()

    section = selected[0]
    level_labels = {text for box, text in labels
                    if _in(box, width, height, (.48, .26, .78, .33))}
    buttons = [box for box, text in labels if text == "全部获得"
               and _in(box, width, height, (.71, .675, .79, .74))]
    if not {"level", "基础"}.issubset(level_labels) or len(buttons) != 1:
        return RewardView("list", section=section, tabs=tabs)

    point = _point(buttons[0], width, height)
    x, y = point
    fill = frame[round((y - .018) * height):round((y - .013) * height),
                 round((x - .016) * width):round((x + .016) * width)]
    if fill.size == 0:
        return RewardView("list", section=section, tabs=tabs)
    brightness = float(np.median(cv2.cvtColor(fill, cv2.COLOR_BGR2GRAY)))
    if brightness >= 225:
        return RewardView("claim", section, point, tabs)
    if 105 <= brightness <= 160:
        return RewardView("empty", section, point, tabs)
    return RewardView("list", section=section, tabs=tabs)
