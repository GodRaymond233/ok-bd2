"""格鲁TALK：检查可对话/未读会话，自动点开并推进聊天剧情。

真实界面（2026-11-15 实测）：GLU TALK 是一个聊天窗口，蓝色标题栏右侧显示
「今日的剩余相遇 N」，左上有「未读消息 N」，左栏是会话列表（未读行右端有蓝色
数字角标），右栏是消息与右下角的回复按钮。进入会话后点回复按钮即可推进剧情，
全屏剧情则靠「跳过 / SKIP」推进。

设计要点：
* 默认自动执行；「仅探测模式」可随时打开，只抓屏 + 记录 OCR 证据，不点击。
* 所有点击都要当帧的正面证据（未读汇总 + 角标 / 回复按钮 / 跳过文字或模板），
  证据不足一律安全停止，绝不靠坐标盲点。
* 运行时若已经在 GLU TALK 页面，直接开始检查，不再回主页重复点入口。
* 结束（成功、无任务、失败）都会尝试回主页，避免卡在子界面拖累后续任务。
"""

from __future__ import annotations

from datetime import datetime
from logging import getLogger
from time import monotonic

import cv2
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.icons import TaskIcon
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.glutalk_vision import (
    AVAILABLE_TASK_DEFAULT_KEYWORDS,
    TalkAction,
    available_task_point,
    classify_talk_page,
    close_button_point,
    conversation_tab_point,
    dialogue_decision,
    dialogue_ended,
    encounter_button_point,
    episode_number,
    home_talk_entry,
    new_fate_character_point,
    remaining_encounters,
    reply_button_point,
    reply_message_state,
    unavailable_contact,
    unread_badge_point,
    unread_count,
)
from src.tasks.task_vision_mixin import TaskVisionMixin
from src.utils.repeated_action import RepeatedAction
from src.utils.vision_models import TemplateSpec

logger = getLogger(__name__)

# UI_Skip.png 的 ROI 沿用「首次卡带跳过」的实测值；UI_talk.png 为双箭头快进，
# 只在画面下半部搜索，避免把其他 UI 装饰误判为推进按钮。
SKIP_TEMPLATE = TemplateSpec(
    "格鲁TALK跳过", "image/UI_Skip.png", 0.72, roi=(1372, 14, 398, 102),
)
STORY_SKIP_TEMPLATE = TemplateSpec(
    "格鲁TALK剧情跳过图标",
    "field/story_skip_icon.png",
    0.72,
    relative_roi=(0.78, 0.0, 0.94, 0.14),
    reference_scale=0.5,
    scale_ratios=(0.9, 1.0, 1.1),
    min_zncc_score=0.72,
    minimum_safe_threshold=0.72,
)
TALK_TEMPLATE = TemplateSpec(
    "格鲁TALK快进", "image/UI_talk.png", 0.72,
    relative_roi=(0.45, 0.68, 1.0, 1.0),
)
UNLOCK_BACK_TEMPLATE = TemplateSpec(
    "格鲁TALK特效解锁返回", "back_return_bar_arrow.png", .78,
    relative_roi=(0.0, 0.0, .15, .12),
)

# 2026-11-17 实机测得的另一种插图页：右侧中部的关闭「×」（OCR 读作 C，
# 中心约 (0.924, 0.564)），只在没有左上返回条时使用。
UNLOCK_CLOSE_POINT = (0.924, 0.564)

UNLOCK_ATTEMPTS = 3


class GlutalkTask(TaskVisionMixin, BaseBD2Task):
    status_keys = [
        "启用", "状态", "当前阶段", "格鲁TALK OCR", "页面判断", "页面判定诊断",
        "剩余相遇", "未读消息", "可对话证据", "回复证据", "对话阶段", "完成对话数",
        "推进点击数", "探测结果", "Log", "Warning", "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "格鲁TALK 对话"
        self.description = (
            "检查格鲁TALK 的未读/可对话会话并自动推进聊天剧情；"
            "需要采集界面证据时可临时打开「仅探测模式」。"
        )
        self.icon = TaskIcon.BOOK_SHELF
        self.group_name = "日常/周常"
        self.group_icon = TaskIcon.CALENDAR
        self.visible = True
        self._init_vision_state()
        self.default_config.update({
            "启用": True,
            "仅探测模式": False,
            "保存探测截图": True,
            "格鲁TALK OCR 阈值": 0.35,
            "主页确认等待秒数": 10.0,
            "页面等待秒数": 12.0,
            "每段对话最长秒数": 90.0,
            "整轮最长秒数": 300.0,
            "回复点击间隔秒数": 0.8,
            "每段结束后回主页重进": True,
            "重进后等待秒数": 2.0,
            "重进后检查秒数": 4.0,
            "无动作判定轮数": 6,
            "可对话关键词": "、".join(AVAILABLE_TASK_DEFAULT_KEYWORDS),
            "使用跳过按钮": True,
            "自动发送回复": True,
            "点击对话选项": False,
            "允许无证据点击推进": False,
        })
        self.config_description.update({
            "仅探测模式": (
                "开启时只抓屏并记录 OCR 文本到 probe_outputs/glutalk_probe_latest.txt，"
                "不点击游戏。默认关闭，即自动检查并推进对话。"
            ),
            "保存探测截图": "仅探测模式下同时保存一张当前游戏画面。",
            "每段结束后回主页重进": (
                "出现「EPn. 对话结束」后，先返回主界面、再重新打开格鲁TALK，"
                "才会触发下一阶段对话（游戏机制）。关闭后只处理当前一段。"
            ),
            "重进后等待秒数": "重新打开格鲁TALK 后等待界面刷新的时间。",
            "重进后检查秒数": "重新打开后持续检查新消息或相遇入口的时间，避免过早结束。",
            "无动作判定轮数": "连续多少帧找不到可点击元素就判定没有更多对话。",
            "可对话关键词": (
                "逗号/顿号分隔。只有条目文字命中其中之一且未命中「已完成」等否定词才会点击。"
                "留空则安全停止、不点击任何条目。"
            ),
            "使用跳过按钮": "识别到 UI_Skip / UI_talk 或「跳过」文字时点击以快进剧情。",
            "整轮最长秒数": "整轮对话推进的时间上限，到期后返回主页。",
            "自动发送回复": (
                "格鲁TALK 聊天页右下角出现回复按钮时自动点击以推进剧情。"
                "关闭后只会点开未读会话，不会替你发送回复。"
            ),
            "点击对话选项": "对话出现选择/确认按钮时点击（默认关闭，避免误选剧情分支）。",
            "允许无证据点击推进": (
                "关闭时只按正面证据点击；开启后才允许无证据时点画面下半部推进。"
                "默认保持关闭。"
            ),
        })
        self._last_diagnostic = ""

    # ------------------------------------------------------------------ utils
    def _threshold(self) -> float:
        return max(0.05, min(0.95, float(self.config.get("格鲁TALK OCR 阈值", 0.35))))

    def _read(self):
        frame = self.capture_frame()
        if frame is None:
            return None, [], ""
        boxes = list(self.ocr(
            frame=frame,
            threshold=self._threshold(),
            target_height=1080,
            log=False,
            name="格鲁TALK",
        ))
        text = " ".join(
            str(box.name) for box in boxes if getattr(box, "name", "")
        )
        self.info_set("格鲁TALK OCR", text[:600] or "-")
        return frame, boxes, text

    def _diagnose(self, reason: str) -> None:
        if reason and reason != self._last_diagnostic:
            self.info_set("页面判定诊断", reason)
            self._last_diagnostic = reason

    def _keywords(self) -> tuple[str, ...]:
        raw = str(self.config.get("可对话关键词", ""))
        return tuple(
            part.strip() for part in raw.replace("、", ",").replace(";", ",").split(",")
            if part.strip()
        )

    def _read_remaining_encounters_crop(self, frame) -> int | None:
        if frame is None or getattr(frame, "ndim", 0) != 3 or frame.shape[2] < 3:
            return None
        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            return None
        crop = frame[
            int(height * 0.22) : int(height * 0.29),
            int(width * 0.62) : int(width * 0.77),
        ]
        if not crop.size:
            return None
        # Normalize crop height to ~68px so both 1080p and 4K frames hit OCR's sweet spot.
        scale = 68.0 / max(1, crop.shape[0])
        normalized = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        try:
            boxes = self.ocr(
                frame=normalized,
                threshold=self._threshold(),
                name="格鲁TALK剩余相遇",
            )
        except (FinishedException, TaskDisabledException):
            raise
        except Exception:
            logger.debug("格鲁TALK 相遇次数标题 OCR 失败", exc_info=True)
            boxes = []
        text = " ".join(str(getattr(box, "name", "")) for box in (boxes or []))
        parsed = remaining_encounters(text)
        if parsed is not None:
            return parsed
        # Fallback: direct single-digit recognition (det=False) between '|' and '+'.
        digit_crop = frame[
            int(height * 0.236) : int(height * 0.272),
            int(width * 0.733) : int(width * 0.747),
        ]
        if not digit_crop.size:
            return None
        try:
            ocr_lib = self.executor.ocr_lib()
            reads = ocr_lib.ocr([digit_crop], det=False, cls=False)[0]
        except (FinishedException, TaskDisabledException):
            raise
        except Exception:
            logger.debug("格鲁TALK 相遇次数单字 OCR 失败", exc_info=True)
            return None
        if reads:
            raw, confidence = reads[0]
            cleaned = str(raw).strip().strip("|｜丨+＋").strip()
            if confidence >= 0.80 and cleaned in ("0", "1", "2", "3"):
                return int(cleaned)
        return None

    def _report_counters(self, text: str, frame=None) -> tuple[int | None, int | None]:
        remaining = remaining_encounters(text)
        if remaining is None and frame is not None and "剩余相遇" in str(text):
            remaining = self._read_remaining_encounters_crop(frame)
        unread = unread_count(text)
        self.info_set("剩余相遇", "-" if remaining is None else str(remaining))
        self.info_set("未读消息", "-" if unread is None else str(unread))
        return remaining, unread

    @property
    def waits_for_login(self):
        return not self.config.get("仅探测模式", False)

    @property
    def return_home_after_run(self):
        return self.waits_for_login

    def _close_talk_window(self, deadline: float = float("inf"), max_attempts: int = 3) -> bool:
        """Close the GLU TALK overlay with bounded retries before home recovery."""
        for _ in range(max_attempts):
            if monotonic() >= deadline:
                return False
            frame, boxes, text = self._read()
            if frame is None:
                self.sleep(min(0.4, max(0.0, deadline - monotonic())))
                continue
            if classify_talk_page(text).page != "list":
                return True
            height, width = frame.shape[:2]
            close = close_button_point(boxes, width, height, frame=frame)
            if close is None:
                self.sleep(min(0.4, max(0.0, deadline - monotonic())))
                continue
            if monotonic() >= deadline:
                return False
            self.operate_click(*close, name="格鲁TALK关闭")
            self.sleep(min(0.6, max(0.0, deadline - monotonic())))
            end_at = min(deadline, monotonic() + 3.0)
            while monotonic() < end_at:
                current_frame, _, current_text = self._read()
                if current_frame is not None and classify_talk_page(current_text).page != "list":
                    return True
                self.sleep(min(0.4, max(0.0, end_at - monotonic())))
        return False

    def _safe_return_home(self) -> bool:
        self.info_set("当前阶段", "返回主页")
        if getattr(self, "_close_retries_exhausted", False):
            return False
        if not self._close_talk_window(max_attempts=2):
            return False
        return self.auto_return_main_home()

    def _fail(self, stage: str) -> bool:
        self.info_set("状态", f"格鲁TALK 失败：{stage}。")
        self.log_warning(f"格鲁TALK：{stage}，尝试返回主页。")
        self._safe_return_home()
        return False

    # ------------------------------------------------------------------ probe
    def _probe(self) -> bool:
        self.info_set("状态", "仅探测模式：只采集界面证据，不点击。")
        frame, boxes, text = self._read()
        if frame is None:
            self.info_set("状态", "格鲁TALK 探测未取得截图。")
            return False
        height, width = frame.shape[:2]
        view = classify_talk_page(text)
        self.info_set("页面判断", view.page)
        self._diagnose(view.diagnostics)
        remaining, unread = self._report_counters(text, frame=frame)
        reply, reply_evidence = reply_button_point(boxes, width, height, frame=frame)
        badge, badge_evidence = unread_badge_point(
            boxes, width, height, unread=unread, frame=frame,
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if bool(self.config.get("保存探测截图", True)):
            self.save_frame(f"glutalk_probe_{timestamp}", frame)
        lines = [
            f"timestamp={timestamp}",
            f"capture_method={self.capture_method_name}",
            f"resolution={width}x{height}",
            f"text_count={len(boxes)}",
            f"page={view.page}",
            f"diagnostics={view.diagnostics}",
            f"remaining_encounters={remaining}",
            f"unread_messages={unread}",
            f"reply_button={reply} ({reply_evidence})",
            f"unread_badge={badge} ({badge_evidence})",
            f"available_keywords={self._keywords()}",
            "",
            "== OCR TEXT ==",
            text or "-",
            "",
            "== BOXES (name / confidence / x,y,w,h) ==",
        ]
        for box in boxes:
            lines.append(
                f"{getattr(box, 'name', '')}\t{getattr(box, 'confidence', 0):.3f}\t"
                f"{getattr(box, 'x', 0)},{getattr(box, 'y', 0)},"
                f"{getattr(box, 'width', 0)},{getattr(box, 'height', 0)}"
            )
        output = self.write_probe_text("glutalk_probe_latest.txt", lines)
        self.info_set("探测结果", str(output))
        self.log_completion(
            f"格鲁TALK 仅探测完成：页面={view.page}，剩余相遇={remaining}，"
            f"未读消息={unread}，证据文件={output}。关闭「仅探测模式」即可自动执行。"
        )
        return True

    # ------------------------------------------------------------------ flow
    def _wait_for_page(self, page: str, timeout: float, deadline: float = float("inf")) -> bool:
        end_at = min(deadline, monotonic() + max(1.0, timeout))
        while monotonic() < end_at:
            frame, _, text = self._read()
            if frame is not None:
                view = classify_talk_page(text)
                self.info_set("页面判断", view.page)
                self._diagnose(view.diagnostics)
                if view.page == page:
                    return True
            self.sleep(min(0.5, max(0.0, end_at - monotonic())))
        return False

    def _click_template(self, frame, spec: TemplateSpec) -> bool:
        result = self._match(frame, spec)
        if not self._passes(result, spec):
            return False
        height, width = frame.shape[:2]
        x, y = result.center
        self.operate_click(
            max(0.0, min(1.0, x / width)),
            max(0.0, min(1.0, y / height)),
            name=spec.name,
            after_sleep=0.2,
        )
        return True

    def _click_skip_template(self, frame) -> bool:
        return self._click_template(frame, SKIP_TEMPLATE) or self._click_template(
            frame, STORY_SKIP_TEMPLATE,
        )

    def _advance_stage(self, stage: int, deadline: float = float("inf")) -> str:
        """Advance one EP.

        Returns ``ended`` / ``home`` / ``idle`` / ``limit`` / ``failed``.

        The chapter separator "EPn. 对话结束" has the highest priority: once it
        appears we stop clicking immediately, because the game only unlocks the
        next episode after leaving GLU TALK and re-opening it from the home screen.

        2026-11-17 实机：点完最后一条回复后，游戏有时不显示任何结束提示，
        直接把格鲁TALK 窗口关掉退回主页；这种帧返回 ``home``，由上层重开窗口，
        不再当成「页面判定持续未知」停机。
        """
        self.info_set("对话阶段", stage)
        self.info_set("当前阶段", f"第 {stage} 段对话推进中")
        if not hasattr(self, "_click_total"):
            self._click_total = 0
        timeout = max(10.0, float(self.config.get("每段对话最长秒数", 90.0)))
        idle_limit = max(1, int(self.config.get("无动作判定轮数", 6)))
        interval = max(0.2, float(self.config.get("回复点击间隔秒数", 0.8)))
        end_at = min(deadline, monotonic() + timeout)
        clicks = 0
        replies = RepeatedAction()
        idle = 0
        home_frames = 0
        character_attempts: dict[str, int] = {}
        tab_attempts = 0
        encounter_attempts = 0
        encounter_confirm_attempts = 0
        unreadable_quota = 0
        unlock_attempts = 0
        conversation_attempts: dict[tuple[float, float], int] = {}

        while monotonic() < end_at:
            frame, boxes, text = self._read()
            if frame is None:
                self.sleep(interval)
                continue
            height, width = frame.shape[:2]
            ended = dialogue_ended(text)
            current_reply, reply_evidence = reply_button_point(
                boxes, width, height, require_send_control=True, frame=None if ended else frame,
            )
            encounter, encounter_evidence = encounter_button_point(boxes, width, height)
            view = classify_talk_page(text)
            if (
                ended
                and view.page != "popup"
                and current_reply is None
                and encounter is None
                and not (unread_count(text) or 0)
            ):
                episode = episode_number(text)
                previous = getattr(self, "_last_episode", None)
                if (
                    stage > 1 and clicks == 0 and episode is not None
                    and previous is not None and episode <= previous
                ):
                    self.info_set("状态", "重进后仍是上一段对话记录，没有新阶段。")
                    return "idle"
                self._last_episode = episode
                self.info_set("状态", f"第 {stage} 段对话结束，停止点击。")
                return "ended"
            if monotonic() >= end_at:
                break
            self.info_set("页面判断", view.page)
            self._diagnose(view.diagnostics)
            remaining, unread = self._report_counters(text, frame=frame)
            action: TalkAction | None = None

            if view.page == "unlock":
                # 特效插图可能隐藏返回条，右侧有关闭位。每次点击后重新判定页面。
                if unlock_attempts >= UNLOCK_ATTEMPTS:
                    self.info_set("状态", "好感度特效解锁页多次尝试仍无法退出，停止。")
                    return "failed"
                unlock_attempts += 1
                replies.reset()
                clicks += 1
                self._click_total += 1
                idle = 0
                self.info_set("推进点击数", self._click_total)
                if self._click_template(frame, UNLOCK_BACK_TEMPLATE):
                    self.info_set("解锁页处理", f"第 {unlock_attempts} 次：点左上返回条")
                elif unlock_attempts == 1:
                    self.info_set("解锁页处理", "第 1 次：点击右侧关闭位")
                    self.operate_click(
                        *UNLOCK_CLOSE_POINT, name="格鲁TALK特效页关闭", after_sleep=0.3,
                    )
                else:
                    self.info_set("解锁页处理", f"第 {unlock_attempts} 次：点击画面中央")
                    self.operate_click(
                        0.5, 0.5, name="格鲁TALK特效页中央", after_sleep=0.3,
                    )
                self.sleep(interval)
                continue

            # Full-screen encounters use an icon-only skip control. OCR can
            # read only its F2 hint; require the existing template to advance.
            if (
                view.page == "unknown"
                and bool(self.config.get("使用跳过按钮", True))
                and self._click_skip_template(frame)
            ):
                replies.reset()
                clicks += 1
                self._click_total += 1
                idle = 0
                self.info_set("推进点击数", self._click_total)
                self.sleep(interval)
                continue

            if view.page == "popup":
                action = dialogue_decision(
                    text, boxes, width, height,
                    allow_skip=bool(self.config.get("使用跳过按钮", True)),
                    allow_choice=bool(self.config.get("点击对话选项", False)),
                )
                if action is not None and action.kind == "encounter_confirm":
                    if encounter_confirm_attempts >= 2:
                        self.info_set("状态", "相遇确认后未进入剧情，停止重复确认。")
                        return "failed"
            elif view.page == "dialogue":
                if bool(self.config.get("使用跳过按钮", True)):
                    if self._click_skip_template(frame) or self._click_template(
                        frame, TALK_TEMPLATE,
                    ):
                        replies.reset()
                        clicks += 1
                        self._click_total += 1
                        idle = 0
                        self.info_set("推进点击数", self._click_total)
                        self.sleep(interval)
                        continue
                action = dialogue_decision(
                    text, boxes, width, height,
                    allow_skip=bool(self.config.get("使用跳过按钮", True)),
                    allow_choice=bool(self.config.get("点击对话选项", False)),
                )
            elif view.page == "list":
                if encounter is not None:
                    self.info_set("可对话证据", encounter_evidence)
                    if remaining is None:
                        unreadable_quota += 1
                        if unreadable_quota < 3:
                            self.sleep(interval)
                            continue
                        self.info_set("状态", "相遇入口已出现，但未确认有剩余相遇次数，停止点击。")
                        return "failed"
                    unreadable_quota = 0
                    if remaining <= 0:
                        self.info_set("状态", "相遇入口已出现，但未确认有剩余相遇次数，停止点击。")
                        return "failed"
                    if encounter_attempts >= 2:
                        self.info_set(
                            "状态", "相遇入口点击后未进入，请检查相遇条件；停止重复选择角色。",
                        )
                        return "failed"
                    action = TalkAction("encounter", encounter, encounter_evidence)
                if action is None:
                    tab, evidence = conversation_tab_point(frame, boxes)
                    if tab is not None and tab_attempts < 2:
                        action = TalkAction("conversation_tab", tab, evidence)
                blocked_contact = unavailable_contact(text)
                if blocked_contact:
                    character_attempts = {name: 2 for name in character_attempts}
                if action is None and not blocked_contact and bool(
                    self.config.get("自动发送回复", True)
                ):
                    self.info_set("回复证据", reply_evidence)
                    if current_reply is not None:
                        action = TalkAction("reply", current_reply, reply_evidence)
                if action is None and remaining is not None and remaining > 0:
                    target, character = new_fate_character_point(
                        boxes, width, height,
                        excluded={name for name, count in character_attempts.items() if count >= 2},
                    )
                    self.info_set("可对话证据", character)
                    if target is not None:
                        action = TalkAction("character", target, character)
                if action is None:
                    badge, badge_evidence = unread_badge_point(
                        boxes, width, height, unread=unread, frame=frame,
                    )
                    self.info_set("可对话证据", badge_evidence)
                    if badge is not None:
                        key = tuple(round(value, 2) for value in badge)
                        if conversation_attempts.get(key, 0) < 2:
                            action = TalkAction("conversation", badge, badge_evidence)
                    else:
                        target, evidence = available_task_point(
                            boxes, width, height, self._keywords(),
                        )
                        if target is not None and unread != 0:
                            self.info_set("可对话证据", evidence)
                            action = TalkAction("conversation", target, evidence)
            else:
                # 既不是列表页也不是对话页：可能游戏已把窗口关掉退回主页。
                # 主页三信号（左列关键词 / 亮度 / 抽抽乐文字）连续两帧确认后
                # 按「本段对话结束」处理，避免把主页当成未知页停机。
                if self._frame_confirms_home(frame, "格鲁TALK未知页"):
                    home_frames += 1
                    if home_frames >= 2:
                        self.info_set(
                            "状态", f"第 {stage} 段对话结束：界面已退回主页。",
                        )
                        return "home"
                    self.sleep(0.5)
                    continue
                home_frames = 0
                idle += 1
                if idle >= idle_limit:
                    self._fail("页面判定持续未知")
                    return "failed"
                self.sleep(0.5)
                continue

            if action is None:
                if (
                    remaining == 0 and (unread == 0 or unread is None)
                    and not character_attempts and not conversation_attempts
                    and not encounter_attempts and not encounter_confirm_attempts
                ):
                    self.info_set("状态", "今日剩余相遇 0，且没有未读对话。")
                    return "idle"
                idle += 1
                if idle >= idle_limit:
                    if (
                        character_attempts or conversation_attempts
                        or encounter_attempts or encounter_confirm_attempts
                    ):
                        # 2026-11-17 实机：最后一条回复点完后，游戏不再显示
                        # 「EPn.对话结束」标记，聊天窗口留在原地、右下角「发送
                        # 消息」控件消失，而顶部「未读消息」计数还没更新。旧逻辑
                        # 在这里直接判失败，整轮白跑。本轮已经点过回复按钮且现在
                        # 读不到回复按钮，就按「本段结束」收尾：上层会关窗回主页
                        # 再重开，由 _has_new_stage 判断是否还有下一段。
                        if clicks > 0 and current_reply is None:
                            self.info_set(
                                "状态",
                                f"第 {stage} 段对话已无回复按钮，按本段结束处理。",
                            )
                            return "ended"
                        self.info_set("状态", "已尝试选择角色，但未确认本段对话完成。")
                        return "failed"
                    self.info_set("状态", "没有可对话的任务。")
                    return "idle"
                self.sleep(0.6)
                continue

            reply_state = None
            if action.kind == "reply":
                reply_state = (action.evidence, reply_message_state(boxes, width, height))
                if (replies.pending is not None and replies.pending[0] == reply_state[0]
                        and replies.pending[1] and not reply_state[1]):
                    self.sleep(interval)
                    continue
                if replies.observe(reply_state):
                    self.info_set("状态", f"第 {stage} 段对话卡住：同一回复连续 5 次无进展。")
                    self.log_warning(
                        f"格鲁TALK 第 {stage} 段：{action.evidence}，连续 5 次无进展。"
                    )
                    return "failed"
            if monotonic() >= end_at:
                break
            dispatched = self.operate_click(
                *action.point, name=f"格鲁TALK{action.kind}", after_sleep=0.3,
            )
            if dispatched is False:
                self.sleep(interval)
                continue
            if reply_state is None:
                replies.reset()
            else:
                replies.dispatched(reply_state)
            if action.kind == "character":
                character_attempts[action.evidence] = character_attempts.get(action.evidence, 0) + 1
            elif action.kind == "conversation_tab":
                tab_attempts += 1
            elif action.kind == "encounter":
                encounter_attempts += 1
            elif action.kind == "encounter_confirm":
                encounter_confirm_attempts += 1
            elif action.kind == "conversation":
                key = tuple(round(value, 2) for value in action.point)
                conversation_attempts[key] = conversation_attempts.get(key, 0) + 1
            clicks += 1
            self._click_total += 1
            idle = 0
            self.info_set("推进点击数", self._click_total)
            self.sleep(interval)

        if monotonic() >= deadline:
            reason = "整轮对话等待超时，尚未完成全部对话"
        else:
            reason = "本段对话等待超时，尚未确认结束"
        self.info_set("状态", f"格鲁TALK 未完成：{reason}。")
        self.log_warning(f"格鲁TALK：{reason}，停止本次运行。")
        return "limit"

    def _restart_from_home(
        self, already_home: bool = False, deadline: float = float("inf"),
    ) -> bool:
        """Confirm the home screen, then open GLU TALK again."""
        if already_home:
            self.info_set("当前阶段", "已回到主界面：重新打开格鲁TALK")
        else:
            self.info_set("当前阶段", "对话结束：返回主界面并重新打开格鲁TALK")
            if not self._close_talk_window(deadline=deadline):
                self._close_retries_exhausted = True
                return False
        home_name = "重进格鲁TALK前主页确认"
        home_wait = min(float(self.config.get("主页确认等待秒数", 10.0)), deadline - monotonic())
        if home_wait <= 0 or not self.prepare_home(home_name, timeout=home_wait, recover=False):
            return False
        reopen_wait = float(self.config.get("重进后等待秒数", 2.0))
        page_wait = float(self.config.get("页面等待秒数", 12.0))
        for _ in range(3):
            if monotonic() >= deadline:
                return False
            frame, boxes, text = self._read()
            if frame is None:
                self.sleep(min(0.5, max(0.0, deadline - monotonic())))
                continue
            if classify_talk_page(text).page == "list":
                return True
            if not self._frame_confirms_home(frame, home_name):
                self.sleep(min(0.5, max(0.0, deadline - monotonic())))
                continue
            entry = home_talk_entry(boxes, frame.shape[1], frame.shape[0])
            if entry is None:
                self.sleep(min(0.5, max(0.0, deadline - monotonic())))
                continue
            if monotonic() >= deadline:
                return False
            self.operate_click(*entry, name="格鲁TALK入口")
            self.sleep(min(1.0 + max(0.0, reopen_wait), max(0.0, deadline - monotonic())))
            if self._wait_for_page("list", page_wait, deadline):
                return True
        return False

    def _has_new_stage(self, text, boxes, width: int, height: int, remaining: int | None) -> bool:
        """Look for an actionable chat or encounter, not just unused quota."""
        encounter, _ = encounter_button_point(boxes, width, height)
        if encounter is not None and (remaining or 0) > 0:
            return True
        reply, _ = reply_button_point(
            boxes, width, height, require_send_control=True,
        )
        if reply is not None or (unread_count(text) or 0) > 0:
            return True
        if (remaining or 0) > 0:
            character, _ = new_fate_character_point(boxes, width, height)
            if character is not None:
                return True
        target, _ = available_task_point(boxes, width, height, self._keywords())
        return target is not None and unread_count(text) != 0

    def _wait_for_next_stage(self, deadline: float = float("inf")) -> bool:
        """Allow newly reopened chats to populate before deciding to leave."""
        end_at = min(deadline, monotonic() + max(
            .5, float(self.config.get("重进后检查秒数", 4.0)),
        ))
        while monotonic() < end_at:
            frame, boxes, text = self._read()
            if frame is None:
                self.sleep(min(0.5, max(0.0, end_at - monotonic())))
                continue
            remaining, _ = self._report_counters(text, frame=frame)
            if self._has_new_stage(
                text, boxes, frame.shape[1], frame.shape[0], remaining,
            ):
                return True
            tab, _ = conversation_tab_point(frame, boxes)
            if tab is not None:
                return True
            self.sleep(min(0.5, max(0.0, end_at - monotonic())))
        return False

    def _scan(self) -> bool:
        """Episode loop: one EP ends -> back to home -> reopen GLU TALK."""
        deadline = monotonic() + max(10.0, float(self.config.get("整轮最长秒数", 300.0)))
        self._click_total = 0
        self._last_episode = None
        self.info_set("完成对话数", 0)
        self.info_set("推进点击数", 0)
        completed = 0
        stage = 0
        rechecked_without_progress = False

        while True:
            stage += 1
            before_clicks = self._click_total
            result = self._advance_stage(stage, deadline)
            if result in ("failed", "limit"):
                return False
            already_home = result == "home"
            if self._click_total > before_clicks:
                rechecked_without_progress = False
                if result in ("ended", "home"):
                    completed += 1
                    self.info_set("完成对话数", completed)
            elif rechecked_without_progress:
                if result == "idle":
                    break
                message = "格鲁TALK 未完成：重进后仍未推进对话。"
                self.info_set("状态", message)
                self.log_warning(message)
                return False
            else:
                rechecked_without_progress = True
            if not bool(self.config.get("每段结束后回主页重进", True)):
                break
            if monotonic() >= deadline:
                self.info_set("状态", "格鲁TALK 未完成：整轮对话等待超时。")
                return False
            reopened = self._restart_from_home(already_home=already_home, deadline=deadline)
            next_stage = reopened and self._wait_for_next_stage(deadline)
            if monotonic() >= deadline:
                self.info_set("状态", "格鲁TALK 未完成：整轮对话等待超时。")
                return False
            if not reopened:
                message = "格鲁TALK 未完成：对话结束后未能回到主界面并重进。"
                self.info_set("状态", message)
                self.log_warning(message)
                return False
            if not next_stage:
                self.info_set("状态", "没有下一阶段对话。")
                break
        return True

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "格鲁TALK 已禁用。")
            return True
        if bool(self.config.get("仅探测模式", False)):
            return self._probe()

        self._close_retries_exhausted = False
        self.info_set("状态", "开始检查格鲁TALK 可对话会话。")
        frame, _, text = self._read()
        view = classify_talk_page(text)
        self.info_set("页面判断", view.page)
        self._diagnose(view.diagnostics)
        self._report_counters(text, frame=frame)
        if view.page in ("list", "unlock"):
            self.info_set(
                "当前阶段", "退出好感度特效解锁页" if view.page == "unlock"
                else "已在格鲁TALK页面，直接检查",
            )
        else:
            self.info_set("当前阶段", "主页确认")
            if (
                not self.prepare_home("格鲁TALK入口前主页确认")
                and not self._safe_return_home()
            ):
                self.info_set("状态", "格鲁TALK 失败：未确认主页。")
                self.log_warning("格鲁TALK：未确认主页。")
                return False
            frame, boxes, _ = self._read()
            if frame is None:
                return self._fail("主页入口复核未取得截图")
            if not self._frame_confirms_home(frame, "格鲁TALK入口复核"):
                return self._fail("未确认仍在主页")
            entry = home_talk_entry(boxes, frame.shape[1], frame.shape[0])
            if entry is None:
                return self._fail("主页左列未识别到格鲁TALK入口")
            self.info_set("当前阶段", "进入格鲁TALK")
            self.operate_click(*entry, name="格鲁TALK入口", after_sleep=1.0)
            if not self._wait_for_page(
                "list", float(self.config.get("页面等待秒数", 12.0)),
            ):
                return self._fail("未确认进入格鲁TALK页面")

        if not self._scan():
            status = (getattr(self, "info", {}) or {}).get("状态", "格鲁TALK 未完成。")
            self._safe_return_home()
            self.info_set("状态", status)
            return False
        if not self._safe_return_home():
            self.info_set("状态", "格鲁TALK 对话检查结束，但未确认返回主页。")
            return False
        info = getattr(self, "info", {}) or {}
        completed = info.get("完成对话数", 0)
        clicks = info.get("推进点击数", 0)
        self.info_set("状态", "格鲁TALK 检查完成。")
        self.log_completion(
            f"格鲁TALK 检查完成：点开 {completed} 个会话、推进 {clicks} 次，已返回主页。"
        )
        return True
