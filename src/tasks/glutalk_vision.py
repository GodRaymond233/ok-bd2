"""Pure screen-reading helpers for the 格鲁TALK (GLU TALK) dialogue task.

Calibration evidence (real 3840x2160 WGC frame, 2026-11-15 17:59):
* Home screen left column: "我的小屋 好友 公会 / 经营管理 格鲁TALK 亲密度 / 街机游戏".
* GLU TALK window: blue "GLU TALK" header, "? 今日的剩余相遇 | 0 +",
  "未读消息 1", a conversation list with a blue "1" badge on the unread row,
  a message pane and a reply button ("对不起…") at the lower right.
* Bottom global navigation contains 角色/背包/剧情/珍藏集/任务/… — these words
  are NOT dialogue-page evidence (an earlier version misclassified the whole
  GLU TALK list as an in-progress story dialogue because of "剧情").

Safety contract: no function returns a click target without positive evidence
in the current frame. Unknown pages resolve to ``unknown`` with a diagnostic
string. All geometry is relative to the frame so 2560x1440 / 3840x2160 both work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2

from src.utils.image_utils import relative_roi_frame
from src.utils.ocr_utils import normalize_ocr_text

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080

# Home-screen left icon column. Real OCR merges "经营管理格鲁TALK" into ONE box
# whose center points at 经营管理 (~0.171); the 格鲁TALK icon sits near 0.205.
HOME_ENTRY_ALIASES = ("格鲁talk", "格鲁talk入口", "格鲁菲talk", "talk")
HOME_ENTRY_RELATIVE_ROI = (0.05, 0.15, 0.32, 0.48)
ENTRY_CLICK_Y_OFFSET = -0.025

# Words that only appear INSIDE the GLU TALK window. The home screen also shows
# 经营管理/格鲁TALK/亲密度, so those deliberately are NOT in this tuple.
INNER_PAGE_KEYWORDS = (
    "glutalk", "未读消息", "今日的剩余相遇", "剩余相遇", "发送消息", "回放相遇事件",
)
INNER_PAGE_MIN_HITS = 2
# Legacy fallbacks for a different GLU TALK layout; still needs 2 independent hints.
LIST_PAGE_KEYWORDS = (
    "联系方式", "会面", "好感度", "缘分", "对话", "聊天", "讯息", "消息", "联络",
)
LIST_PAGE_MIN_HITS = 2

# Only words that cannot come from the global bottom navigation bar.
DIALOGUE_KEYWORDS = (
    "跳过", "skip", "点击继续", "自动播放", "continue", "tap",
)
EXHAUSTED_KEYWORDS = (
    "次数已用尽", "今日已用完", "今日次数", "0/3", "没有可对话", "全部完成",
    "无可进行", "明日", "已全部阅读", "剩余相遇0", "今日的剩余相遇0",
)
POPUP_KEYWORDS = (
    "获得物品", "获得奖励", "获得道具", "点击画面即可返回", "奖励获得",
    "是否要进行相遇事件",
    "获得好感度奖励",
)

# Positive evidence that a list row can be talked to. Deliberately narrow:
# "对话" alone is excluded because it also appears in headers and buttons.
AVAILABLE_TASK_DEFAULT_KEYWORDS = (
    "可对话", "开始对话", "进行对话", "未读", "new", "新剧情", "会面", "未确认",
)
AVAILABLE_TASK_BLOCK_KEYWORDS = (
    "已完成", "已读", "已结束", "不可", "次数已用尽", "冷却", "未解锁",
    # "未读消息 1" is a summary/header, never a clickable conversation row.
    "未读消息",
)
# Character rows are confined to the left pane. The right pane's empty-state
# text "开始对话吧" is an instruction, not a conversation button.
AVAILABLE_TASK_RELATIVE_ROI = (0.25, 0.28, 0.52, 0.84)

# Blue numeric badge at the right edge of an unread conversation row.
# Measured: "1" at (1916,1192,68,66) -> center (0.508, 0.567) on 3840x2160.
UNREAD_BADGE_RELATIVE_ROI = (0.42, 0.28, 0.60, 0.80)
UNREAD_BADGE_MIN = 1
UNREAD_BADGE_MAX = 9

# Lower-right reply button of the selected conversation.
# Measured: "对不起…" at (2444,1600,180,56) -> center (0.661, 0.754).
# IMPORTANT: the chapter separator "EP2.对话结束" (center ~0.661,0.769) and
# character names in message bubbles (~0.616,0.727) also fall in this ROI.
# Both MUST be excluded, otherwise the task keeps clicking after the EP ends.
REPLY_RELATIVE_ROI = (0.50, 0.71, 0.82, 0.84)
REPLY_EXCLUDE_KEYWORDS = (
    "发送消息", "未读消息", "跳过", "skip", "auto", "返回", "关闭", "取消",
    "剩余相遇", "glutalk", "回放相遇事件", "对话结束", "对话结来", "对话结東",
    "代号", "心契之约", "max", "相遇事件",
    "开始对话吧",
    "服装信息", "联系方式", "未拥有",
)

# The "X" at the right of the blue GLU TALK title bar, measured at
# (2964,520,56,52) -> center (0.779, 0.253) on 3840x2160.
CLOSE_RELATIVE_ROI = (0.72, 0.20, 0.84, 0.32)

CHOICE_KEYWORDS = ("选择", "选项", "确认", "确定", "下一步")
DISMISS_KEYWORDS = ("点击画面即可返回", "关闭", "返回")

_REMAINING_RE = re.compile(r"剩余相遇\s*[|｜丨:：]*\s*([0-3０-３])(?!\d)")
_UNREAD_RE = re.compile(r"未读消息\s*(\d+)")
_DIALOGUE_END_RE = re.compile(
    r"(?:ep\d+)?[.．、]?\s*对话结[束来東]|对话完[成毕]",
)
_EPISODE_RE = re.compile(r"ep(\d+)[.．、]?\s*对话结[束来東]")
_CHAPTER_MARKER_RE = re.compile(r"^ep\d+")
_NEW_FATE_RE = re.compile(r"^新的缘分([1-9]\d*)$")
_FATE_HEADER_RE = re.compile(r"^(?:新的)?缘分(?:列表)?\d*$")
CONVERSATION_TAB_BADGE_ROI = (.21, .36, .25, .45)


@dataclass(frozen=True)
class TalkView:
    page: str = "unknown"  # list | dialogue | popup | unlock | unknown
    diagnostics: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class TalkAction:
    kind: str  # skip | choice | dismiss | reply | conversation
    point: tuple[float, float]
    evidence: str


def _norm(value: object) -> str:
    return normalize_ocr_text(value, alnum_only=True)


def _plain(value: object) -> str:
    """Lowercase + strip whitespace, keep digits for the count regexes."""
    return normalize_ocr_text(value)


def _point(box, width: int, height: int) -> tuple[float, float] | None:
    if width <= 0 or height <= 0:
        return None
    x = getattr(box, "x", None)
    y = getattr(box, "y", None)
    w = getattr(box, "width", 0) or 0
    h = getattr(box, "height", 0) or 0
    if x is None or y is None:
        return None
    return ((float(x) + float(w) / 2) / width, (float(y) + float(h) / 2) / height)


def _inside(point: tuple[float, float], roi: tuple[float, float, float, float]) -> bool:
    x, y = point
    return roi[0] <= x <= roi[2] and roi[1] <= y <= roi[3]


def matched_keywords(text: object, keywords) -> tuple[str, ...]:
    """Return the configured keywords present in normalized OCR text."""
    normalized = _norm(text)
    if not normalized:
        return ()
    return tuple(
        keyword for keyword in keywords
        if _norm(keyword) and _norm(keyword) in normalized
    )


def remaining_encounters(text: object) -> int | None:
    """Parse "今日的剩余相遇0" -> 0. None when OCR did not read the counter."""
    # Keep OCR box separators: "剩余相遇0 36:18" must not become quota 36.
    match = _REMAINING_RE.search(str(text))
    return int(match.group(1)) if match else None


def unread_count(text: object) -> int | None:
    """Parse "未读消息 1" -> 1. None when OCR did not read the summary."""
    match = _UNREAD_RE.search(str(text))
    return int(match.group(1)) if match else None


def dialogue_ended(text: object) -> bool:
    """True once the "EP2. 对话结束" chapter separator appears.

    At that point the game requires leaving GLU TALK and re-opening it from
    the home screen before the next episode becomes available.
    """
    return bool(_DIALOGUE_END_RE.search(_plain(text)))


def episode_number(text: object) -> int | None:
    """Parse "EP2.对话结束" -> 2, used to tell a new episode from the old one."""
    match = _EPISODE_RE.search(_plain(text))
    return int(match.group(1)) if match else None


def close_button_point(
    boxes,
    width: int,
    height: int,
    roi=CLOSE_RELATIVE_ROI,
) -> tuple[float, float] | None:
    """Locate the GLU TALK title-bar X button by positive OCR evidence."""
    candidates = []
    for box in boxes:
        name = _norm(getattr(box, "name", ""))
        if name not in ("x", "close", "关闭"):
            continue
        point = _point(box, width, height)
        if point is None or not _inside(point, roi):
            continue
        candidates.append((point[0], point[1], point))
    if not candidates:
        return None
    return min(candidates)[2]


def _alias_center_x(name: str, alias: str, box, point, width: int) -> float:
    """Shift the click from a merged OCR box to the alias' own label.

    Real OCR merges "经营管理格鲁TALK" into one box whose center points at
    the *first* icon. Linear interpolation on character offsets moves the
    click onto 格鲁TALK (measured center x ~0.205 instead of ~0.171).
    """
    index = name.find(alias)
    if index < 0 or width <= 0:
        return point[0]
    ratio = (index + len(alias) / 2) / max(1, len(name))
    box_width_ratio = (getattr(box, "width", 0) or 0) / width
    return max(0.0, min(1.0, point[0] + (ratio - 0.5) * box_width_ratio))


def home_talk_entry(boxes, width: int, height: int) -> tuple[float, float] | None:
    """Locate the 格鲁TALK icon in the confirmed home's left column."""
    candidates = []
    for box in boxes:
        name = _norm(getattr(box, "name", ""))
        if not name:
            continue
        alias = next(
            (item for item in HOME_ENTRY_ALIASES if _norm(item) in name), None,
        )
        if alias is None:
            continue
        point = _point(box, width, height)
        if point is None or not _inside(point, HOME_ENTRY_RELATIVE_ROI):
            continue
        x = _alias_center_x(name, _norm(alias), box, point, width)
        y = max(0.0, min(1.0, point[1] + ENTRY_CLICK_Y_OFFSET))
        # A bare "talk" is a weak match; prefer the full Chinese label.
        rank = 0 if "格鲁" in name else 1
        candidates.append((rank, y, x, (x, y)))
    if not candidates:
        return None
    return min(candidates)[3]


def classify_talk_page(text: object) -> TalkView:
    """Classify the current frame without ever assuming an unknown page is safe.

    The GLU TALK header and counters identify the chat window even when a
    message contains "跳过" (e.g. "我从来没跳过舞"). The global bottom navigation
    items (剧情/任务/背包/…) are intentionally NOT dialogue keywords.
    """
    normalized = _norm(text)
    if not normalized:
        return TalkView("unknown", "OCR 为空，未取得任何页面证据")
    if "获得好感度特效" in normalized and "主界面设置" in normalized:
        return TalkView("unlock", "识别到好感度特效解锁页", ("获得好感度特效", "主界面设置"))

    popup_hits = matched_keywords(normalized, POPUP_KEYWORDS)
    if popup_hits:
        return TalkView("popup", "识别到奖励/确认弹窗", popup_hits)
    if all(word in normalized for word in (
        "跳过对话", "可通过格鲁talk简介相遇事件", "取消", "确认",
    )):
        return TalkView("popup", "识别到相遇剧情跳过确认窗", ("跳过对话",))

    inner_hits = matched_keywords(normalized, INNER_PAGE_KEYWORDS)
    if len(inner_hits) >= INNER_PAGE_MIN_HITS:
        return TalkView("list", "", inner_hits)
    dialogue_hits = matched_keywords(normalized, DIALOGUE_KEYWORDS)
    if dialogue_hits:
        return TalkView("dialogue", "", dialogue_hits)
    list_hits = matched_keywords(normalized, LIST_PAGE_KEYWORDS)
    if len(list_hits) >= LIST_PAGE_MIN_HITS:
        return TalkView("list", "", list_hits)
    if inner_hits or list_hits:
        return TalkView(
            "unknown",
            f"列表页证据不足：只有 {len(inner_hits) + len(list_hits)}/"
            f"{INNER_PAGE_MIN_HITS} 组（{'、'.join(inner_hits + list_hits)}）",
            inner_hits + list_hits,
        )
    return TalkView("unknown", "未命中列表页/对话页任何关键词")


def is_exhausted(text: object) -> bool:
    return bool(matched_keywords(text, EXHAUSTED_KEYWORDS))


def available_task_point(
    boxes,
    width: int,
    height: int,
    keywords=AVAILABLE_TASK_DEFAULT_KEYWORDS,
    roi=AVAILABLE_TASK_RELATIVE_ROI,
) -> tuple[tuple[float, float] | None, str]:
    """Find the topmost list row that positively says it can be talked to."""
    configured = tuple(k.strip() for k in keywords if k and k.strip())
    if not configured:
        return None, "未配置可对话关键词（安全停止，不盲点）"
    candidates = []
    for box in boxes:
        name = _norm(getattr(box, "name", ""))
        if not name:
            continue
        hits = tuple(k for k in configured if _norm(k) in name)
        if not hits:
            continue
        if any(_norm(block) in name for block in AVAILABLE_TASK_BLOCK_KEYWORDS):
            continue
        point = _point(box, width, height)
        if point is None or not _inside(point, roi):
            continue
        candidates.append((point[1], point[0], point, "、".join(hits)))
    if not candidates:
        return None, "本帧没有带可对话证据的条目"
    _, _, point, evidence = min(candidates, key=lambda item: (item[0], item[1]))
    return point, evidence


def unavailable_contact(text: object) -> bool:
    return bool(matched_keywords(text, ("未拥有", "未持有", "持有该服装时")))


def conversation_tab_point(frame, boxes) -> tuple[tuple[float, float] | None, str]:
    """Enter the chat tab when a cyan unread badge is visible beside contacts."""
    text = _norm(" ".join(str(getattr(box, "name", "")) for box in boxes))
    if "glutalk" not in text or "缘分列表" not in text:
        return None, "不是角色缘分列表"
    if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
        return None, "没有可识别的聊天标签画面"
    height, width = frame.shape[:2]
    left, top, crop = relative_roi_frame(frame, CONVERSATION_TAB_BADGE_ROI)
    if not crop.size:
        return None, "聊天标签区域为空"
    blue, green, red = (crop[..., index] for index in range(3))
    mask = ((blue >= 140) & (green >= 100) & (red <= 100)).astype("uint8")
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    candidates = []
    for x, y, w, h, area in stats[1:count]:
        if not (.012 * height <= w <= .04 * height and .012 * height <= h <= .04 * height):
            continue
        if not (.65 <= w / h <= 1.45) or area < .4 * w * h:
            continue
        # The badge itself does not forward clicks to the tab. Its associated
        # speech-bubble button sits immediately below and to its left.
        candidates.append((
            (left + x + w / 2 - .8 * w) / width,
            (top + y + h / 2 + .55 * h) / height,
        ))
    if len(candidates) != 1:
        return None, "未识别到唯一的未读聊天标签角标"
    return candidates[0], "角色列表左侧有未读聊天角标，切换到会话列表"


def new_fate_character_point(
    boxes, width: int, height: int, *, excluded=(),
) -> tuple[tuple[float, float] | None, str]:
    """Choose an observed character name below the positive 新的缘分 header.

    Stop at the following section header, so completed/MAX characters in the
    ordinary fate section cannot be selected as new encounters.
    """
    rows = []
    for box in boxes:
        point = _point(box, width, height)
        name = _norm(getattr(box, "name", ""))
        if point is not None and _inside(point, AVAILABLE_TASK_RELATIVE_ROI) and name:
            rows.append((point, name))
    headers = sorted(
        (point for point, name in rows if _NEW_FATE_RE.fullmatch(name)),
        key=lambda point: point[1],
    )
    if not headers:
        return None, "未发现有角色的新的缘分分组"
    top = headers[0][1]
    bottom = min(
        (point[1] for point, name in rows
         if point[1] > top and _FATE_HEADER_RE.fullmatch(name)),
        default=AVAILABLE_TASK_RELATIVE_ROI[3],
    )
    candidates = []
    for point, name in rows:
        if not (top + .015 < point[1] < bottom - .01):
            continue
        if len(name) < 2 or name in excluded or _FATE_HEADER_RE.fullmatch(name):
            continue
        if any(word in name for word in ("max", "已完成", "未解锁", "开始对话", "剩余相遇")):
            continue
        if any("max" in other and abs(other_point[1] - point[1]) < .02
               for other_point, other in rows):
            continue
        candidates.append((point[1], point[0], point, name))
    if not candidates:
        return None, "新的缘分分组没有尚未尝试的角色"
    _, _, point, name = min(candidates)
    return point, name


def unread_badge_point(
    boxes,
    width: int,
    height: int,
    *,
    unread: int | None = None,
    roi=UNREAD_BADGE_RELATIVE_ROI,
) -> tuple[tuple[float, float] | None, str]:
    """Click target for the unread conversation row (blue numeric badge).

    Requires a positively read "未读消息N>0" summary; a 0 or an unreadable
    summary never produces a click. Bare digit boxes outside the conversation
    list (e.g. the chat-tab badge at x~0.24) are ignored.
    """
    if unread is None:
        return None, "未读到「未读消息N」汇总，安全停止"
    if unread <= 0:
        return None, "未读消息为 0，没有可对话会话"
    candidates = []
    for box in boxes:
        point = _point(box, width, height)
        if point is None or not _inside(point, roi):
            continue
        name = str(getattr(box, "name", "")).strip()
        if not re.fullmatch(r"[1-9１-９]", name):
            continue
        value = int(name)
        candidates.append((point[1], point[0], point, value))
    if not candidates:
        return None, "未找到会话列表中的未读角标"
    _, _, point, value = min(candidates)
    return point, f"未读消息{unread}：会话角标 {value}"



def reply_message_state(boxes, width, height):
    """Read message-pane text above the reply controls in visual order."""
    messages = []
    for box in boxes:
        point = _point(box, width, height)
        if point is not None and _inside(point, (.54, .34, .79, .70)):
            text = _norm(getattr(box, "name", ""))
            if len(text) >= 2:
                messages.append((point[1], point[0], text))
    return tuple(text for _, _, text in sorted(messages))


def encounter_button_point(
    boxes, width: int, height: int,
) -> tuple[tuple[float, float] | None, str]:
    """Locate the current encounter card's start button, never its replay link."""
    labels = [(box, _norm(getattr(box, "name", ""))) for box in boxes]
    cards = []
    buttons = []
    for box, name in labels:
        point = _point(box, width, height)
        if point is None or not _inside(point, (.52, .30, .80, .84)):
            continue
        prefix = name.removesuffix("进行相遇事件")
        if name.endswith("进行相遇事件") and (not prefix or prefix.isdigit()):
            buttons.append(point)
        elif "相遇事件" in name or re.fullmatch(r"第\d+次相遇", name):
            if "回放" not in name:
                cards.append(point)
    if len(buttons) != 1:
        return None, "未识别到唯一的进行相遇事件按钮"
    point = buttons[0]
    if not any(card[1] < point[1] and abs(card[0] - point[0]) < .16 for card in cards):
        return None, "相遇按钮缺少同列相遇卡片证据"
    return point, "当前会话的相遇卡片：进行相遇事件"


def reply_button_point(
    boxes,
    width: int,
    height: int,
    roi=REPLY_RELATIVE_ROI,
    *,
    require_send_control: bool = False,
) -> tuple[tuple[float, float] | None, str]:
    """Click target for the lower-right reply button of the selected chat."""
    if require_send_control and not any(
        _norm(getattr(box, "name", "")) == "发送消息"
        and (point := _point(box, width, height)) is not None
        and _inside(point, (.50, .65, .80, .82))
        for box in boxes
    ):
        return None, "尚未出现发送消息控件，等待聊天消息显示完成"
    candidates = []
    for box in boxes:
        name = _norm(getattr(box, "name", ""))
        if len(name) < 2 and not (
            require_send_control and len(name) == 1 and "\u4e00" <= name <= "\u9fff"
        ):
            continue
        # "ep2对话结束" is a chapter separator, never a reply button.
        if _CHAPTER_MARKER_RE.match(name):
            continue
        if any(_norm(word) in name for word in REPLY_EXCLUDE_KEYWORDS):
            continue
        point = _point(box, width, height)
        if point is None or not _inside(point, roi):
            continue
        candidates.append((point[1], point[0], point, name))
    if not candidates:
        return None, "本帧没有回复按钮证据"
    _, _, point, name = min(candidates)
    return point, f"回复按钮：{name}"


def encounter_confirmation_point(
    boxes, width: int, height: int,
) -> tuple[tuple[float, float] | None, str]:
    """Confirm only the observed free encounter dialog with a positive quota."""
    prompt = False
    quota = None
    confirms = []
    for box in boxes:
        name = _norm(getattr(box, "name", ""))
        point = _point(box, width, height)
        if point is None:
            continue
        if name == "是否要进行相遇事件" and _inside(point, (.35, .40, .65, .55)):
            prompt = True
        match = re.fullmatch(r"可进行次数(\d+)次", name)
        if match and _inside(point, (.35, .45, .65, .60)):
            quota = int(match.group(1))
        if name in ("确认", "确定") and _inside(point, (.50, .58, .64, .68)):
            confirms.append(point)
    if not prompt or quota is None or not 1 <= quota <= 3 or len(confirms) != 1:
        return None, "相遇确认窗缺少明确提示、免费次数或唯一确认按钮"
    return confirms[0], f"确认相遇事件，可进行次数{quota}次"


def dialogue_decision(
    text: object,
    boxes,
    width: int,
    height: int,
    *,
    allow_skip: bool = True,
    allow_choice: bool = False,
) -> TalkAction | None:
    """Decide the next *evidence-backed* click inside a dialogue."""
    normalized = _norm(text)
    if not normalized:
        return None
    if "获得好感度奖励" in normalized and re.search(r"获得.+的好感度奖励", normalized):
        for box in boxes:
            point = _point(box, width, height)
            if (
                _norm(getattr(box, "name", "")) == "获得好感度奖励"
                and point is not None and _inside(point, (.35, .25, .65, .45))
            ):
                return TalkAction("dismiss_reward", point, "相遇好感度奖励已显示，点击关闭奖励画面")
        return None
    if "是否要进行相遇事件" in normalized:
        point, evidence = encounter_confirmation_point(boxes, width, height)
        return TalkAction("encounter_confirm", point, evidence) if point is not None else None
    if "跳过对话" in normalized and (
        "可通过格鲁talk" in normalized or "取消" in normalized
    ):
        # This is a modal title, not the top-right story skip control.
        if not allow_skip or "可通过格鲁talk简介相遇事件" not in normalized:
            return None
        confirms = [
            point for box in boxes
            if _norm(getattr(box, "name", "")) in ("确认", "确定")
            and (point := _point(box, width, height)) is not None
            and _inside(point, (.50, .60, .65, .80))
        ]
        if len(confirms) == 1:
            return TalkAction("skip_confirm", confirms[0], "确认跳过当前相遇剧情")
        return None

    for box in boxes:
        name = _norm(getattr(box, "name", ""))
        if not name:
            continue
        point = _point(box, width, height)
        if point is None:
            continue
        if (
            allow_skip and name in ("跳过", "跳过对话", "skip")
            and _inside(point, (.65, .0, 1.0, .20))
        ):
            return TalkAction("skip", point, name)
        if any(_norm(word) == name for word in DISMISS_KEYWORDS):
            return TalkAction("dismiss", point, name)
    if allow_choice:
        for box in boxes:
            name = _norm(getattr(box, "name", ""))
            point = _point(box, width, height)
            if not name or point is None:
                continue
            if any(_norm(word) in name for word in CHOICE_KEYWORDS):
                # Only lower-screen controls count as dialogue choices.
                if point[1] >= 0.55:
                    return TalkAction("choice", point, name)
    return None
