"""主线野外「自动推主线」流程。

实机流程（1920x1080，坐标取自 2026-11-18 用户提供的野外截图 3837x2158）：

    野外：右上角任务追踪条
        「主线」标题
        「E 前往丢弃铁假面的地方」 ← 目标行左侧的 E 就是游戏自己的推进热键
        ↓ 点目标行（或点那个 E 键帽），角色自动寻路
    途中：战斗 → 让它自己打（回合停住就点 BATTLE）；剧情 → F2 跳过并确认
        ↓ 目标文字换成下一句
    继续推进，直到整章完成
        ↓ 很久都没有任何推进（多半是解谜/机关/对话选项）
    存图 → 托盘通知 → 置顶弹框 → 停任务等玩家处理

这是「主线快速探索」任务在找不到快速探索入口时走的备用模式（由
``main_story_mode`` 决定）。这里没有固定按钮序列，每一步都以**当前帧的证据**为准。
判定「有没有在推进」只认两个信号：目标文字变化、界面状态变化。画面活动只用来
决定「现在能不能推」——自动寻路时镜头在走，这时候不去点，免得打断寻路。
点击本身**不算**推进，否则卡在解谜上永远报不出来。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from enum import Enum
from time import monotonic

import cv2
import numpy as np

from src.tasks.main_story import (
    ALL_CLEAR_ROI,
    BATTLE_TURN_CLICKS,
    BATTLE_TURN_INTERVAL,
    CLICK_CONFIRM_SECONDS,
    CLICK_RETRY_ATTEMPTS,
    CUTSCENE_SKIP_ATTEMPTS,
    CUTSCENE_SKIP_HINT_ROI,
    CUTSCENE_SKIP_POINT,
    FAILED_KEYWORDS,
    LIST_BUTTON_ROI,
    QUEST_START_ROI,
    REWARD_POPUP_HINT_ROI,
    REWARD_POPUP_TITLE_ROI,
    SKIP_CONFIRM_BODY_ROI,
    SKIP_CONFIRM_ROUNDS,
    SKIP_CONFIRM_TITLE_ROI,
    STORY_ADVANCE_CLICKS,
    STORY_SUBTITLE_ROIS,
    VICTORY_KEYWORDS,
    VICTORY_LEAVE_POINT,
    VICTORY_LEAVE_ROI,
    VICTORY_TITLE_ROI,
    MainStoryVisionMixin,
    _looks_like_all_clear,
    _looks_like_battle_hud,
    _looks_like_battle_turn,
    _looks_like_cutscene,
    _looks_like_quest_start,
    _looks_like_reward_popup,
    _looks_like_skip_confirm,
    _looks_like_story_subtitle,
    _main_story_roi,
)
from src.tasks.task_vision_mixin import (
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    TEMPLATE_DIR,
)
from src.utils import task_vision
from src.utils.ocr_utils import keyword_match_count, normalize_ocr_text
from src.utils.topmost_alert import show_topmost_alert
from src.utils.vision_models import EMPTY_MATCH, MatchResult, TemplateSpec

# 追踪条标题「主线」：实测在参考分辨率 y≈129–150、x≈1600 起。
TRACKER_HEADER_ROI = _main_story_roi(1580, 120, 1720, 154)

# 追踪条当前目标行：实测文字 (1580,153)-(1785,179)，左边 (1551,152)-(1577,179)
# 是按键提示「E」，右边 x≥1809 是展开按钮「+」——ROI 两头都留了余量。
TRACKER_OBJECTIVE_ROI = _main_story_roi(1550, 150, 1800, 188)

# 点这一行＝让游戏重新寻路到当前目标；实测行中心，避开「+」。
TRACKER_OBJECTIVE_POINT = (1682, 166)

# 目标行左边那个「E」键帽的中心：手机版点它和按 E 是同一件事。
TRACKER_E_POINT = (1564, 166)

TRACKER_HEADER_KEYWORDS = ("主线",)

# 战斗失败结算页的按钮条：实机未截到失败页，只按文字找「重试」，不设兜底点。
RETRY_BUTTON_ROI = _main_story_roi(1150, 930, 1900, 1060)
RETRY_KEYWORDS = ("重试", "重新挑战", "再次挑战", "retry")

# 画面活动采样区：避开右上追踪条与右下技能轮盘，只留中间的游戏场景。
ACTIVITY_ROI = _main_story_roi(200, 300, 1700, 860)
ACTIVITY_STRIDE = 8

# 野外交互提示的搜索区：走到 NPC/机关旁边时游戏会在物件旁画一个「F」键帽
# （2026-11-18 实机「F 可疑物品」截图）。去掉顶部货币栏、左上小地图与队伍栏、
# 底部技能区，也不搜右上角追踪条——追踪条目标行左边的「E」键帽和 F 是同一套
# 皮肤，实测能到 0.90 分，混进来就会把「点追踪条」错当成「交互」。
INTERACT_PROMPT_ROI = _main_story_roi(200, 150, 1500, 850)

# 右下角那个圆形「!」按钮是手机版的同一个交互入口，键帽「F」画在它下方。
INTERACT_HUD_ROI = _main_story_roi(1300, 780, 1520, 960)
# 圆形按钮中心在键帽上方约 45 参考像素处。
INTERACT_HUD_BUTTON_OFFSET = (0, -45)

# 模板是 2026-11-18 实机 3840x2160 截图里「F」键帽的原尺寸裁切（键帽 35x39）。
# 客户端实际是 4K 画面，所以它的宽在 1080p 参考尺寸下是 42 * 1920/3840 ≈ 21 像素，
# 即 reference_scale=0.5；匹配器再按帧尺寸换算到客户端分辨率。
# 阈值给到 0.93：真提示实测 1.00，追踪条那个「E」键帽只有 0.90，正好卡开。
INTERACT_WORLD_KEYCAP = TemplateSpec(
    "野外交互键F",
    "field/field_keycap_F.png",
    0.93,
    relative_roi=INTERACT_PROMPT_ROI,
    reference_scale=0.5,
    scale_ratios=(0.95, 1.0, 1.05),
    min_pixel_score=0.85,
)
INTERACT_HUD_KEYCAP = TemplateSpec(
    "右下角交互键F",
    "field/field_keycap_F.png",
    0.93,
    relative_roi=INTERACT_HUD_ROI,
    reference_scale=0.5,
    scale_ratios=(0.95, 1.0, 1.05),
    min_pixel_score=0.85,
)
INTERACT_KEYCAP_TEMPLATES = (INTERACT_WORLD_KEYCAP, INTERACT_HUD_KEYCAP)

# 点提示条时落在键帽右侧的标签按钮里（实测标签从键帽右边一直延伸出去）。
INTERACT_PROMPT_OFFSET = (60, 0)

# 站定多久后才去点提示（太早会打扰还在走路的角色）。
INTERACT_SETTLE_SECONDS = 1.0
# 两次交互之间至少隔这么久，免得同一个提示被反复点。
INTERACT_COOLDOWN_SECONDS = 3.0
# 点完提示后等「提示消失/界面切换」的秒数；没消失说明这一下没被接受。
INTERACT_CONFIRM_SECONDS = 3.0
# 点完先停一下再取帧，否则截到的还是点之前那帧。
INTERACT_CLICK_SETTLE_SECONDS = 0.8
# 最后一场确认：提示条上的键帽在这个分数以下＝提示被吃掉了。
INTERACT_GONE_SCORE = 0.9

# 目标文字允许一字之差（OCR 形近误读），低于这个相似度才算「目标变了」。
OBJECTIVE_MATCH_RATIO = 0.8

WALK_POLL_SECONDS = 0.5
FIRST_ADVANCE_SECONDS = 1.5
PROBE_FLUSH_EVERY = 20
WALK_ADVANCE_MODES = ("点击追踪条", "按 E 键", "两者都试")


class MainStoryWalkState(str, Enum):
    FIELD = "野外推进"
    BATTLE = "战斗内"
    BATTLE_TURN = "战斗-待开回合"
    VICTORY = "战斗结算"
    DEFEAT = "战斗失败"
    REWARD = "奖励弹窗"
    CUTSCENE = "剧情过场"
    SKIP_CONFIRM = "跳过对话确认"
    STORY_DIALOG = "剧情演出"
    ALL_CLEAR = "主线已完成"
    LOBBY = "不在野外场景"
    UNKNOWN = "无法识别"


BATTLE_STATES = (MainStoryWalkState.BATTLE, MainStoryWalkState.BATTLE_TURN)


def _looks_like_tracker_header(text: object) -> bool:
    """右上角追踪条标题是不是「主线」。"""

    return keyword_match_count(
        text, TRACKER_HEADER_KEYWORDS, fuzzy_ratio=0.75,
    ) >= 1


def objective_signature(text: object) -> str:
    """把目标行 OCR 文本归一成可比较的签名。

    目标行最左边是按键提示「E」，OCR 常常和正文连成一个字符串（实机读作
    「E 前往丢弃铁假面的地方」）。E 是游戏热键提示、不是目标内容，去掉它，
    否则「E 出现/消失」会被误当成目标变化，把卡住计时一直刷掉。
    """

    merged = normalize_ocr_text(text)
    if merged.startswith("e") and len(merged) > 1:
        merged = merged[1:]
    return merged


def objective_changed(
    previous: str,
    current: str,
    ratio: float = OBJECTIVE_MATCH_RATIO,
) -> bool:
    """目标是不是换了新的一句。OCR 漏读（空串）不参与判断。"""

    if not previous or not current:
        return False
    if previous == current:
        return False
    return SequenceMatcher(None, previous, current).ratio() < ratio


def screen_signature(frame) -> np.ndarray | None:
    """画面活动采样：中间场景区灰度缩略图。"""

    if frame is None:
        return None
    array = np.asarray(frame)
    if array.ndim < 2 or array.size == 0:
        return None
    height, width = array.shape[:2]
    x1 = int(ACTIVITY_ROI[0] * width)
    y1 = int(ACTIVITY_ROI[1] * height)
    x2 = int(ACTIVITY_ROI[2] * width)
    y2 = int(ACTIVITY_ROI[3] * height)
    crop = array[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    if crop.ndim == 3:
        crop = crop[..., :3].mean(axis=2)
    return crop[::ACTIVITY_STRIDE, ::ACTIVITY_STRIDE].astype("float32", copy=False)


def screen_delta(previous, current) -> float:
    """两帧采样图的平均绝对差；拿不到可比数据时按「没动」处理。"""

    if previous is None or current is None:
        return 0.0
    left = np.asarray(previous, dtype=float)
    right = np.asarray(current, dtype=float)
    if left.shape != right.shape or left.size == 0:
        return 0.0
    return float(np.abs(left - right).mean())


def _looks_like_retry_button(text: object) -> bool:
    """失败结算页的「重试」按钮。"""

    return keyword_match_count(text, RETRY_KEYWORDS, fuzzy_ratio=0.75) >= 1


def stuck_due(last_progress_at: float, now: float, timeout: float) -> bool:
    """从最后一次推进到现在是否已经超过卡住门槛。"""

    if timeout <= 0:
        return False
    return now - last_progress_at >= timeout


def classify_main_story_walk_state(
    *,
    skip_confirm_text: object = "",
    cutscene_text: object = "",
    reward_text: object = "",
    victory_text: object = "",
    battle_text: object = "",
    tracker_start_text: object = "",
    all_clear_text: object = "",
    tracker_header_text: object = "",
    objective_text: object = "",
    story_text: object = "",
) -> MainStoryWalkState:
    """把各 ROI 的 OCR 文本归到一个界面状态。

    判定顺序的每一条都有实机依据：

    - 跳过确认排在过场之前：点 F2 后右上角「F2」提示还压在弹窗背后，
      先认过场就会一直点跳过图标、永远点不到「确认」。
    - 结算「离开」/战斗 HUD 排在追踪条之前：野外战斗时追踪条仍可能可见，
      先认追踪条会把战斗当成野外、在战斗中乱点追踪条。
    - 「开始XX难度」先于野外判定：大厅的追踪条也在右上角，文字是
      「开始困难难度」——那是「起点不对」，不是野外目标。
    - 野外判定在剧情字幕之前：野外 HUD 上的技能名/杂字（≥4 个汉字的可能）
      不该把正常野外帧抢成剧情演出；万一是野外误判，点 (960,990) 也只落在
      空地上，没有副作用。
    """

    if _looks_like_skip_confirm(skip_confirm_text):
        return MainStoryWalkState.SKIP_CONFIRM

    if _looks_like_cutscene(cutscene_text):
        return MainStoryWalkState.CUTSCENE

    if _looks_like_reward_popup(reward_text):
        return MainStoryWalkState.REWARD

    if keyword_match_count(victory_text, VICTORY_KEYWORDS):
        return MainStoryWalkState.VICTORY

    if _looks_like_battle_turn(battle_text, victory_text):
        return MainStoryWalkState.BATTLE_TURN

    if _looks_like_battle_hud(battle_text, victory_text):
        return MainStoryWalkState.BATTLE

    if _looks_like_quest_start(tracker_start_text):
        return MainStoryWalkState.LOBBY

    if _looks_like_all_clear(all_clear_text):
        return MainStoryWalkState.ALL_CLEAR

    if _looks_like_tracker_header(tracker_header_text):
        return MainStoryWalkState.FIELD

    if len(objective_signature(objective_text)) >= 2:
        return MainStoryWalkState.FIELD

    if _looks_like_story_subtitle(story_text):
        return MainStoryWalkState.STORY_DIALOG

    return MainStoryWalkState.UNKNOWN


class MainStoryWalkConfigMixin:
    """自动推主线的配置项，键名统一以「推主线」开头，避免与其它任务冲突。"""

    MAIN_STORY_WALK_CONFIG_KEYS = (
        "推主线推进方式",
        "推主线自动交互",
        "推主线停滞秒数",
        "推主线推进间隔秒数",
        "推主线静止阈值",
        "推主线等待秒数",
        "推主线战斗等待秒数",
        "推主线战斗失败重试次数",
        "推主线时间上限分钟",
        "推主线 OCR 阈值",
        "主线置顶弹框",
        "推主线调试记录",
    )

    def _install_main_story_walk_config(self) -> None:
        self.default_config.update(
            {
                "启用": True,
                "推主线推进方式": "点击追踪条",
                "推主线自动交互": True,
                "推主线停滞秒数": 240.0,
                "推主线推进间隔秒数": 10.0,
                "推主线静止阈值": 6.0,
                "推主线等待秒数": 20.0,
                "推主线战斗等待秒数": 300.0,
                "推主线战斗失败重试次数": 1,
                "推主线时间上限分钟": 0.0,
                "推主线 OCR 阈值": 0.2,
                "主线置顶弹框": True,
                "推主线调试记录": False,
            }
        )
        self.config_description.update(
            {
                "启用": "是否执行主线野外自动推进。",
                "推主线推进方式": (
                    "怎么让游戏继续走：点右上角追踪条的目标行，或点目标行左边的 E "
                    "键帽（这个客户端不响应脚本注入的按键，两种方式都是点击）。"
                    "「两者都试」会先点目标行再点 E 键帽。"
                ),
                "推主线自动交互": (
                    "野外画面出现「F」交互键帽（走到 NPC、可疑物品、机关旁边时游戏会"
                    "显示）就自己点提示条，并确认提示真的消失才算成功；关掉后只会点"
                    "追踪条。"
                ),
                "推主线停滞秒数": (
                    "目标文字与界面状态都不变化超过这么久，就判定卡住：存图、托盘通知、"
                    "弹置顶框并停止任务。自动寻路途中目标本来就不变，所以要给足走完一段"
                    "路的时间（默认 240 秒）；解谜/机关/对话选项多半靠它兜底。"
                ),
                "推主线推进间隔秒数": (
                    "画面静止多久后再「推」一次。正在寻路时（画面在动）不会去点，"
                    "避免把自动寻路打断。"
                ),
                "推主线静止阈值": (
                    "画面活动量（0–255 的平均像素差）低于它算静止、高于它算「还在动」。"
                    "打开「推主线调试记录」可按实际分布校准。"
                ),
                "推主线等待秒数": (
                    "认不出界面（加载中、过场切换）时连续等待的秒数，超过才报错。"
                ),
                "推主线战斗等待秒数": (
                    "单场战斗从进战场到结算的兜底秒数；战斗停在「TURN. N / BATTLE」"
                    "时任务会自己点 BATTLE，这里只是点满次数仍打不完时的上限。"
                ),
                "推主线战斗失败重试次数": (
                    "战斗失败后在失败结算页找「重试」再打几次；用完后弹框停机。"
                ),
                "推主线时间上限分钟": "运行多久后停止，0 表示不限。",
                "推主线 OCR 阈值": "追踪条与按钮文字识别的最低置信度。",
                "主线置顶弹框": (
                    "卡住时是否弹一个置顶消息框压在游戏之上（同时总会发系统通知）。"
                ),
                "推主线调试记录": (
                    "把每轮的状态、目标文字、画面差值写进 "
                    "probe_outputs/main_story_walk_probe.txt，用于校准静止阈值。"
                ),
            }
        )
        self.config_type.update(
            {
                "推主线推进方式": {
                    "type": "drop_down",
                    "options": list(WALK_ADVANCE_MODES),
                },
                "推主线停滞秒数": {"min": 60.0, "max": 1200.0, "step": 30.0},
                "推主线推进间隔秒数": {"min": 3.0, "max": 60.0, "step": 1.0},
                "推主线静止阈值": {"min": 0.0, "max": 30.0, "step": 0.5},
                "推主线等待秒数": {"min": 5.0, "max": 120.0, "step": 1.0},
                "推主线战斗等待秒数": {"min": 30.0, "max": 900.0, "step": 10.0},
                "推主线战斗失败重试次数": {"min": 0, "max": 3, "step": 1},
                "推主线时间上限分钟": {"min": 0.0, "max": 1440.0, "step": 5.0},
                "推主线 OCR 阈值": {"min": 0.05, "max": 0.95, "step": 0.01},
            }
        )


class MainStoryWalkFeatureMixin(MainStoryVisionMixin):
    """自动推主线的状态机主体。"""

    def run_main_story_walk(self) -> bool:
        advance_mode = str(self.config.get("推主线推进方式", "点击追踪条") or "")
        interact = bool(self.config.get("推主线自动交互", True))
        stuck_seconds = float(self.config.get("推主线停滞秒数", 240.0))
        advance_interval = float(self.config.get("推主线推进间隔秒数", 10.0))
        still_threshold = float(self.config.get("推主线静止阈值", 6.0))
        confirm_seconds = float(self.config.get("推主线等待秒数", 20.0))
        battle_wait = float(self.config.get("推主线战斗等待秒数", 300.0))
        max_retries = int(self.config.get("推主线战斗失败重试次数", 1) or 0)
        minute_limit = float(self.config.get("推主线时间上限分钟", 0.0) or 0.0)
        threshold = float(self.config.get("推主线 OCR 阈值", 0.2))
        probe = bool(self.config.get("推主线调试记录", False))

        deadline = monotonic() + minute_limit * 60 if minute_limit > 0 else None
        started_at = monotonic()

        last_progress_at = started_at
        last_state: MainStoryWalkState | None = None
        last_objective = ""
        objective_readable = False
        last_evidence = ""
        last_frame_signature = None
        still_since: float | None = None
        battle_since: float | None = None
        waiting_since: float | None = None
        battle_turn_clicks = 0
        last_delta_reported = -1
        last_still_reported = -1
        cutscene_clicks = 0
        story_clicks = 0
        skip_confirm_rounds = 0
        battles = 0
        advances = 0
        failures = 0
        interactions = 0
        last_interact_at = 0.0
        cycles = 0
        probe_lines: list[str] = []

        self._walk_set_stats(battles, advances, failures, interactions)

        while True:
            if deadline is not None and monotonic() >= deadline:
                self.log_info("自动推主线：到达时间上限，结束。")
                return True

            frame = self.capture_frame()
            state, evidence, objective_text, tracker_seen = self._walk_state(
                frame, threshold,
            )
            if state is MainStoryWalkState.UNKNOWN and interact:
                # 有些野外场景右上角根本不显示追踪条（只有小地图、队伍栏和货币栏），
                # 这时画面上的「F」键帽就是最硬的「人站在野外」证据。
                target = self._walk_interact_target(frame)
                if target is not None:
                    state = MainStoryWalkState.FIELD
                    evidence = f"野外F键帽({target[1]})"
            if evidence != last_evidence:
                last_evidence = evidence
                self.info_set("推主线当前界面", f"{state.value}｜{evidence}")
            if normalize_ocr_text(objective_text):
                self.info_set("推主线当前目标", objective_text)

            now = monotonic()
            frame_signature = screen_signature(frame)
            delta = screen_delta(last_frame_signature, frame_signature)
            last_frame_signature = frame_signature
            # 画面差值每轮都变，写状态面板会刷屏；整数量级变化才写一次。
            if int(delta) != last_delta_reported:
                last_delta_reported = int(delta)
                self.info_set("推主线画面差值", f"{delta:.1f}")
            moving = delta >= still_threshold

            previous_state = last_state
            if state is not last_state:
                last_state = state
                waiting_since = None
                still_since = None
                if state in BATTLE_STATES:
                    # 只有"从别的界面走进战斗"才算一场新战斗；BATTLE↔BATTLE_TURN
                    # 之间来回切不重置计时与回合点击次数。
                    if previous_state not in BATTLE_STATES:
                        battle_since = now
                        battle_turn_clicks = 0
                        battles += 1
                        last_progress_at = now
                        self._walk_set_stats(battles, advances, failures, interactions)
                        self.log_info(f"自动推主线：进入战斗（第 {battles} 场），让它自己打。")
                elif state is not MainStoryWalkState.FIELD:
                    # 野外之外的任何界面切换都是一次推进（走进战斗、开始结算…）。
                    last_progress_at = now
                if state is not MainStoryWalkState.CUTSCENE:
                    cutscene_clicks = 0
                if state is not MainStoryWalkState.STORY_DIALOG:
                    story_clicks = 0
                if state is not MainStoryWalkState.SKIP_CONFIRM:
                    skip_confirm_rounds = 0

            signature = objective_signature(objective_text)
            if signature:
                objective_readable = True
            if objective_changed(last_objective, signature):
                last_objective = signature
                last_progress_at = now
                still_since = None
                self.log_info(f"自动推主线：目标更新为「{objective_text}」。")

            # 画面在动只说明"游戏自己在走"，不算目标推进：否则一段循环动画
            # （NPC 抖动、粒子特效）会把真正的卡死一直遮住。推进只认目标文字
            # 和界面状态的改变，所以停滞秒数要给得足够走完一段寻路。
            # 例外：目标行一次都没读出来时没有别的推进信号，只能退回画面活动，
            # 否则正常的寻路也会被误判成卡住。
            if moving:
                still_since = None
                if not objective_readable:
                    last_progress_at = now
            elif still_since is None:
                still_since = now

            if state is MainStoryWalkState.FIELD:
                battle_since = None
                needed = advance_interval if advances else FIRST_ADVANCE_SECONDS
                still_seconds = now - still_since if still_since is not None else 0.0
                if int(still_seconds) != last_still_reported:
                    last_still_reported = int(still_seconds)
                    self.info_set("推主线静止秒数", f"{still_seconds:.0f}")
                # 走到 NPC/机关旁边时游戏会停下来等一个「F」，这时点追踪条没用：
                # 先看画面上有没有 F 键帽，有就点提示条并确认提示真的消失了。
                if (
                    interact
                    and still_seconds >= INTERACT_SETTLE_SECONDS
                    and now - last_interact_at >= INTERACT_COOLDOWN_SECONDS
                ):
                    last_interact_at = now
                    if self._walk_try_interact(frame, threshold):
                        interactions += 1
                        self._walk_set_stats(
                            battles, advances, failures, interactions,
                        )
                        now = monotonic()
                        last_progress_at = now
                        still_since = now
                        last_still_reported = 0
                if (
                    tracker_seen
                    and still_since is not None
                    and now - still_since >= needed
                ):
                    advances += 1
                    self._walk_set_stats(battles, advances, failures, interactions)
                    self.log_info(
                        f"自动推主线：画面静止 {still_seconds:.0f} 秒，"
                        f"推进一次（第 {advances} 次，方式={advance_mode}）。",
                    )
                    self._walk_advance(advance_mode)
                    still_since = now
                    last_still_reported = 0

            elif state is MainStoryWalkState.BATTLE_TURN:
                if battle_turn_clicks < BATTLE_TURN_CLICKS:
                    battle_turn_clicks += 1
                    last_progress_at = now
                    self.log_info(
                        f"自动推主线：战斗停在开回合前，点「BATTLE」"
                        f"（本场第 {battle_turn_clicks}/{BATTLE_TURN_CLICKS} 次）。",
                    )
                    point = self._main_story_battle_turn_point(frame, threshold)
                    self._click_reference(*point)
                    self.sleep(BATTLE_TURN_INTERVAL)
                if battle_since is not None and now - battle_since > battle_wait:
                    return self._main_story_walk_stuck(
                        frame,
                        f"战斗超过 {battle_wait:.0f} 秒仍未结算",
                        evidence,
                        objective_text,
                    )

            elif state is MainStoryWalkState.BATTLE:
                if battle_since is not None and now - battle_since > battle_wait:
                    return self._main_story_walk_stuck(
                        frame,
                        f"战斗超过 {battle_wait:.0f} 秒仍未结算",
                        evidence,
                        objective_text,
                    )

            elif state is MainStoryWalkState.VICTORY:
                battle_since = None
                title = self._main_story_ocr(
                    frame, VICTORY_TITLE_ROI, "推主线结算标题", threshold,
                )
                if keyword_match_count(title, FAILED_KEYWORDS):
                    failures += 1
                    self._walk_set_stats(battles, advances, failures, interactions)
                    self.log_info(f"自动推主线：本场战斗失败（连续 {failures} 次）。")
                    if failures <= max_retries:
                        point = self._walk_retry_point(frame, threshold)
                        if point is None:
                            return self._main_story_walk_stuck(
                                frame,
                                "战斗失败，但失败结算页没读到「重试」按钮",
                                evidence,
                                objective_text,
                            )
                        self.log_info("自动推主线：点「重试」再打一次。")
                        if not self._walk_click_and_confirm(
                            state, point, threshold, "重试",
                        ):
                            return self._main_story_walk_stuck(
                                frame,
                                "点击「重试」后失败结算页没有变化",
                                evidence,
                                objective_text,
                            )
                        last_progress_at = now
                    else:
                        return self._main_story_walk_stuck(
                            frame,
                            f"连续 {failures} 场战斗失败，疑似打不过该节点",
                            evidence,
                            objective_text,
                        )
                else:
                    failures = 0
                    self._walk_set_stats(battles, advances, failures, interactions)
                    self.log_info("自动推主线：结算完成，点「离开」。")
                    if not self._walk_click_and_confirm(
                        state, VICTORY_LEAVE_POINT, threshold, "离开",
                    ):
                        return self._main_story_walk_stuck(
                            frame, "点击「离开」后界面无变化", evidence, objective_text,
                        )

            elif state is MainStoryWalkState.REWARD:
                self.log_info("自动推主线：奖励弹窗，点击关闭。")
                point = self._main_story_reward_click_point(frame, threshold)
                if not self._walk_click_and_confirm(
                    state, point, threshold, "奖励弹窗",
                ):
                    return self._main_story_walk_stuck(
                        frame, "点击「奖励弹窗」后界面无变化", evidence, objective_text,
                    )

            elif state is MainStoryWalkState.CUTSCENE:
                if cutscene_clicks < CUTSCENE_SKIP_ATTEMPTS:
                    cutscene_clicks += 1
                    last_progress_at = now
                    self.log_info(
                        f"自动推主线：剧情过场，点右上角跳过（第 {cutscene_clicks} 次）。",
                    )
                    self._click_reference(*CUTSCENE_SKIP_POINT)
                    self.sleep(1.0)

            elif state is MainStoryWalkState.SKIP_CONFIRM:
                if skip_confirm_rounds < SKIP_CONFIRM_ROUNDS:
                    skip_confirm_rounds += 1
                    last_progress_at = now
                    self.log_info("自动推主线：「跳过对话」确认框，点「确认」。")
                    point = self._main_story_skip_confirm_point(frame, threshold)
                    self._click_reference(*point)
                    self.sleep(1.0)

            elif state is MainStoryWalkState.STORY_DIALOG:
                if story_clicks < STORY_ADVANCE_CLICKS:
                    story_clicks += 1
                    last_progress_at = now
                    point = self._main_story_subtitle_point(frame, threshold)
                    self._click_reference(*point)
                    self.sleep(0.8)

            elif state is MainStoryWalkState.ALL_CLEAR:
                self.save_frame("main_story_walk_all_clear", frame)
                self.info_set("推主线停止原因", "主线已完成")
                self.log_info("自动推主线：主线已全部完成，任务正常结束。")
                return True

            elif state is MainStoryWalkState.LOBBY:
                if waiting_since is None:
                    waiting_since = now
                if now - waiting_since >= confirm_seconds:
                    return self._main_story_walk_stuck(
                        frame,
                        "未识别到主线野外场景（追踪条显示「开始难度」＝还站在大厅，"
                        "请先进入主线野外场景再启动）",
                        evidence,
                        objective_text,
                    )

            elif state is MainStoryWalkState.UNKNOWN:
                if waiting_since is None:
                    waiting_since = now
                if now - waiting_since >= confirm_seconds:
                    return self._main_story_walk_stuck(
                        frame, "界面持续无法识别", evidence, objective_text,
                    )

            if state not in BATTLE_STATES and stuck_due(
                last_progress_at, now, stuck_seconds,
            ):
                return self._main_story_walk_stuck(
                    frame,
                    f"主线目标 {stuck_seconds:.0f} 秒没有推进",
                    evidence,
                    objective_text,
                )

            cycles += 1
            if probe:
                probe_lines.append(
                    f"{monotonic() - started_at:8.1f}s {state.value} "
                    f"delta={delta:5.1f} 目标={signature or '-'}",
                )
                if cycles % PROBE_FLUSH_EVERY == 0:
                    self.write_probe_text(
                        "main_story_walk_probe.txt",
                        probe_lines[-200:],
                        info_label="推主线调试文件",
                    )

            self.sleep(WALK_POLL_SECONDS)

    def _walk_state(
        self,
        frame,
        threshold: float,
    ) -> tuple[MainStoryWalkState, str, str, bool]:
        """识别当前界面，返回 (状态, 存证文本, 追踪条目标文字, 追踪条是否显示)。"""

        header = self._main_story_ocr(
            frame, TRACKER_HEADER_ROI, "推主线追踪条标题", threshold,
        )
        objective = self._main_story_ocr(
            frame, TRACKER_OBJECTIVE_ROI, "推主线当前目标", threshold,
        )
        reward_hint = self._main_story_ocr(
            frame, REWARD_POPUP_HINT_ROI, "推主线奖励提示", threshold,
        )
        reward_title = self._main_story_ocr(
            frame, REWARD_POPUP_TITLE_ROI, "推主线奖励标题", threshold,
        )
        victory = self._main_story_ocr(
            frame, VICTORY_LEAVE_ROI, "推主线结算离开", threshold,
        )
        battle = self._main_story_ocr(
            frame, LIST_BUTTON_ROI, "推主线战斗按钮", threshold,
        )
        tracker_start = self._main_story_ocr(
            frame, QUEST_START_ROI, "推主线追踪条开始难度", threshold,
        )
        all_clear = self._main_story_ocr(
            frame, ALL_CLEAR_ROI, "推主线完成提示", threshold,
        )

        skip_confirm = cutscene = story = ""
        cutscene_bars = False
        state = classify_main_story_walk_state(
            reward_text=f"{reward_title} {reward_hint}",
            victory_text=victory,
            battle_text=battle,
            tracker_start_text=tracker_start,
            all_clear_text=all_clear,
            tracker_header_text=header,
            objective_text=objective,
        )
        if state is MainStoryWalkState.UNKNOWN:
            # 过场/跳过确认/剧情字幕只在认不出界面时才补测：野外每轮都测
            # 等于十几次 OCR，而且字幕 ROI 在野外会读到 HUD 上的杂字。
            skip_confirm = self._main_story_ocr(
                frame, SKIP_CONFIRM_TITLE_ROI, "推主线跳过对话标题", threshold,
            ) + self._main_story_ocr(
                frame, SKIP_CONFIRM_BODY_ROI, "推主线跳过对话正文", threshold,
            )
            cutscene = self._main_story_ocr(
                frame, CUTSCENE_SKIP_HINT_ROI, "推主线跳过剧情", threshold,
            )
            story = " ".join(
                self._main_story_ocr(frame, bounds, "推主线剧情字幕", threshold)
                for bounds in STORY_SUBTITLE_ROIS
            )
            cutscene_bars = self._main_story_cutscene_bars(frame)
            state = classify_main_story_walk_state(
                skip_confirm_text=skip_confirm,
                cutscene_text=cutscene,
                reward_text=f"{reward_title} {reward_hint}",
                victory_text=victory,
                battle_text=battle,
                tracker_start_text=tracker_start,
                all_clear_text=all_clear,
                tracker_header_text=header,
                objective_text=objective,
                story_text=story,
            )
            if state is MainStoryWalkState.UNKNOWN and cutscene_bars:
                state = MainStoryWalkState.CUTSCENE

        evidence = (
            f"标题={header or '-'};目标={objective or '-'};离开={victory or '-'};"
            f"战斗={battle or '-'};奖励={reward_hint or '-'};开始={tracker_start or '-'};"
            f"完成={all_clear or '-'};跳确认={skip_confirm or '-'};"
            f"过场={cutscene or ('黑边' if cutscene_bars else '-')};字幕={story or '-'}"
        )
        tracker_seen = bool(
            normalize_ocr_text(header) or normalize_ocr_text(objective),
        )
        return state, evidence, objective, tracker_seen

    def _walk_interact_target(self, frame):
        """找画面里的「F」交互键帽：找到返回 (参考坐标, 模板名, 匹配分)。

        键帽是游戏自己在物件旁画的，位置随物件走，所以只能整块场景搜；
        分数不过阈值就当作没有提示，绝不点「大概位置」。
        """

        spec = INTERACT_WORLD_KEYCAP
        return self._walk_keycap_target(frame, spec)

    def _walk_hud_interact_target(self, frame):
        """右下角圆形「!」按钮下面那个「F」：只在它真的画出来时才算数。"""

        spec = INTERACT_HUD_KEYCAP
        return self._walk_keycap_target(frame, spec)

    def _walk_keycap_target(self, frame, spec):
        """跑一次键帽模板，过阈值就换算成参考坐标返回 (点, 名字, 分数)。"""

        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        best = self._walk_match_spec(frame, spec)
        if best is None or best.score <= 0:
            return None
        if not task_vision.passes_match(
            best, spec, getattr(self, "config", {}),
        ):
            return None
        return self._walk_reference_point(frame, best), spec.name, best.score

    def _walk_reference_point(self, frame, match) -> tuple[float, float]:
        """匹配结果的中心点换算成 1080p 参考坐标。"""

        height, width = frame.shape[:2]
        x, y = match.position
        box_width, box_height = match.size
        return (
            (x + box_width / 2) * REFERENCE_WIDTH / max(1, width),
            (y + box_height / 2) * REFERENCE_HEIGHT / max(1, height),
        )

    def _walk_interact_score(self, frame) -> float:
        """场景里「F」键帽最像的那一处得分；没有帧或不认识返回 -1。"""

        if frame is None or getattr(frame, "size", 0) == 0:
            return -1.0
        return float(self._walk_match_spec(frame, INTERACT_WORLD_KEYCAP).score)

    def _walk_match_spec(self, frame, spec) -> MatchResult:
        """跑一次模板匹配；模板缺失/内存不足都只记一次，不打断主循环。"""

        try:
            return task_vision.match_template(
                frame,
                spec,
                getattr(self, "config", {}),
                TEMPLATE_DIR,
                cache=getattr(self, "_walk_templates", None),
                min_size=8,
                skip_scale_errors=True,
            )
        except RuntimeError as exc:
            if not getattr(self, "_walk_missing_template", False):
                self._walk_missing_template = True
                self.log_warning(str(exc), notify=True)
            return EMPTY_MATCH
        except (cv2.error, MemoryError, ValueError) as exc:
            self.info_set("推主线交互匹配错误", str(exc))
            return EMPTY_MATCH

    def _walk_try_interact(self, frame, threshold: float) -> bool:
        """画面上有「F」提示就用鼠标点它。

        这个客户端不处理 PostMessage 注入的按键（实测 F/E/ESC 发了都不动），
        鼠标消息却照收，所以交互一律走点击——手机版本来就是点这几个按钮。
        点完必须看到「提示条消失」或「界面离开野外」才算成立，否则换下一个
        入口再点一次；两个入口都没反应就交回主循环，不盲试。
        """

        target = self._walk_interact_target(frame)
        if target is None:
            return False
        point, name, score = target
        self.log_info(
            f"自动推主线：检测到交互提示「{name}」（匹配 {score:.2f}，"
            f"位于参考坐标 {point[0]:.0f},{point[1]:.0f}），点提示条交互。",
        )
        attempts = [
            (
                "提示条",
                (
                    point[0] + INTERACT_PROMPT_OFFSET[0],
                    point[1] + INTERACT_PROMPT_OFFSET[1],
                ),
            ),
        ]
        hud = self._walk_hud_interact_target(frame)
        if hud is not None:
            hud_point = hud[0]
            attempts.append(
                (
                    "右下角交互按钮",
                    (
                        hud_point[0] + INTERACT_HUD_BUTTON_OFFSET[0],
                        hud_point[1] + INTERACT_HUD_BUTTON_OFFSET[1],
                    ),
                ),
            )
        for action, (click_x, click_y) in attempts:
            self._click_reference(click_x, click_y)
            self.sleep(INTERACT_CLICK_SETTLE_SECONDS)
            if self._walk_prompt_consumed(threshold):
                self.log_info(f"自动推主线：点「{action}」后交互生效。")
                return True
            self.log_info(f"自动推主线：点「{action}」后提示还在，换下一个入口。")
        return False

    def _walk_prompt_consumed(self, threshold: float) -> bool:
        """点完交互入口后的确认：提示条消失或者界面离开野外才算真的点到了。"""

        deadline = monotonic() + max(1.0, INTERACT_CONFIRM_SECONDS)
        while monotonic() < deadline:
            self.sleep(0.5)
            try:
                frame = self.capture_frame()
            except Exception as exc:
                self.info_set("推主线截图错误", str(exc))
                continue
            state, *_rest = self._walk_state(frame, threshold)
            if state is not MainStoryWalkState.FIELD:
                return True
            if self._walk_interact_score(frame) < INTERACT_GONE_SCORE:
                return True
        return False

    def _walk_advance(self, mode: str) -> None:
        """让游戏继续走：点追踪条目标行（行左边的「E」也是点这一行）。

        这个客户端不吃注入的按键，所以「按 E 键」也只实现成点那个 E 键帽——
        手机版点它和按 E 触发的是同一段逻辑。坐标取实测点，避开「+」。
        """

        if mode in ("点击追踪条", "两者都试", ""):
            self._click_reference(*TRACKER_OBJECTIVE_POINT)
            if mode == "两者都试":
                self.sleep(0.5)
        if mode in ("按 E 键", "两者都试"):
            self._click_reference(*TRACKER_E_POINT)

    def _walk_retry_point(self, frame, threshold: float) -> tuple[float, float] | None:
        """失败结算页「重试」在哪儿：只认 OCR 到的重试文字，没有兜底点。"""

        boxes, crop = self._main_story_ocr_boxes(
            frame, RETRY_BUTTON_ROI, "推主线重试按钮", threshold,
        )
        for box in boxes:
            if not _looks_like_retry_button(getattr(box, "name", "")):
                continue
            return self._main_story_box_point(RETRY_BUTTON_ROI, crop, box)
        return None

    def _walk_click_and_confirm(
        self,
        previous: MainStoryWalkState,
        point: tuple[float, float],
        threshold: float,
        action: str,
    ) -> bool:
        """点一下并确认界面离开了 previous；输入被吞就重试，不一次定生死。"""

        for attempt in range(1, CLICK_RETRY_ATTEMPTS + 1):
            self._click_reference(*point)
            if self._walk_wait_change(previous, CLICK_CONFIRM_SECONDS, threshold):
                return True
            if attempt < CLICK_RETRY_ATTEMPTS:
                self.log_info(
                    f"自动推主线：「{action}」点击后界面未变化，"
                    f"重试 {attempt}/{CLICK_RETRY_ATTEMPTS - 1}。",
                )
                self.sleep(1.0)
        self.log_info(
            f"自动推主线：「{action}」连续 {CLICK_RETRY_ATTEMPTS} 次点击均无响应。",
        )
        return False

    def _walk_wait_change(
        self,
        previous: MainStoryWalkState,
        timeout: float,
        threshold: float,
    ) -> bool:
        deadline = monotonic() + max(1.0, timeout)
        while monotonic() < deadline:
            self.sleep(0.5)
            try:
                frame = self.capture_frame()
            except Exception as exc:
                self.info_set("推主线截图错误", str(exc))
                continue
            state, *_rest = self._walk_state(frame, threshold)
            if state is not previous:
                return True
        return False

    def _walk_set_stats(
        self,
        battles: int,
        advances: int,
        failures: int,
        interactions: int = 0,
    ) -> None:
        self.info_set("推主线战斗次数", battles)
        self.info_set("推主线推进次数", advances)
        self.info_set("推主线失败次数", failures)
        self.info_set("推主线交互次数", interactions)

    def _main_story_walk_stuck(
        self,
        frame,
        reason: str,
        evidence: str = "",
        objective: str = "",
    ) -> bool:
        """卡住出口：存图 → 托盘通知 → 置顶弹框 → 停任务等玩家处理。"""

        path = self.save_frame("main_story_walk_stuck", frame)
        self.write_probe_text(
            "main_story_walk_stuck.txt",
            [
                f"原因：{reason}",
                f"目标：{objective or '<未识别>'}",
                f"界面：{evidence or '<无>'}",
                f"截图：{path}",
            ],
            info_label="推主线存证文件",
        )
        self.info_set("推主线停止原因", reason)
        message = (
            f"自动推主线已停止：{reason}。\n\n"
            f"当前目标：{objective or '未识别'}\n"
            "可能需要在游戏里手动处理（解谜、机关、对话选项等），"
            "处理完后可以重新启动本任务。\n"
            "如果这是刚启动就停：请确认人物已经站在主线野外场景里"
            "（右上角追踪条写着「主线」和当前目标），"
            "或者改用「主线模式 = 快速探索」进已通关关卡刷取。\n"
            f"截图已保存：{path}"
        )
        self.log_error(
            f"自动推主线卡住：{reason}；已保存 probe_outputs/main_story_walk_stuck.png",
            notify=True,
        )
        if bool(self.config.get("主线置顶弹框", True)):
            show_topmost_alert(message)
        return False
