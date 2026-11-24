"""主线「快速探索」自动刷取流程。

实机流程（1920x1080，界面用词取自实机截图）：

    关卡内：右上角任务面板下方有「进行快速探索」
        ↓ 点击
    关卡任务列表：标题形如「血骑士-极难」，右下角一个长条按钮
        ├ 按钮为「自动进行」→ 点击，游戏自动推进非战斗节点
        └ 按钮为「战斗」    → 点击，进入战斗
        ↓ 战斗内（没开自动战斗时右下角停在「TURN. N | BATTLE」）
           → 点「BATTLE」开这一回合，下一回合再点，直到打完
        ↓ 战斗结束
    结算页：「MISSION COMPLETED」+ 右下角「离开」
        ↓ 点击
    回到关卡内，继续下一轮

节点顺序不固定，中间还夹杂剧情、掉落等界面，因此每一步都以**当前帧的按钮文字**
为准，不做固定脚本；认不出来就等，等不到就停下来存证，绝不盲点。
"""

from __future__ import annotations

from enum import Enum
from time import monotonic

from ok.task.exceptions import FinishedException, TaskDisabledException

from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH
from src.utils.image_utils import crop_relative
from src.utils.ocr_utils import keyword_match_count, normalize_ocr_text
from src.utils.topmost_alert import show_topmost_alert


def _main_story_roi(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> tuple[float, float, float, float]:
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return (
        left / REFERENCE_WIDTH,
        top / REFERENCE_HEIGHT,
        right / REFERENCE_WIDTH,
        bottom / REFERENCE_HEIGHT,
    )


# 关卡内右上角「进行快速探索」按钮。实测文字框 (1575,192)-(1861,228)。

IN_STAGE_BUTTON_ROI = _main_story_roi(1555, 180, 1885, 242)

IN_STAGE_BUTTON_POINT = (1718, 210)

# 关卡任务列表右下角长条按钮。实测「自动进行」条 (1488,984)-(1791,1041)，
# 「战斗」条 (1493,980)-(1792,1037)，文字都落在中间这一段。
LIST_BUTTON_ROI = _main_story_roi(1580, 972, 1825, 1048)

LIST_BUTTON_POINT = (1655, 1010)

# 列表页左上角标题，形如「血骑士-极难」，用于关卡校验与难度核对。
# 左边留出 210 是为了避开返回箭头按钮，只框盾牌图标与标题文字。
LIST_TITLE_ROI = _main_story_roi(210, 12, 470, 60)

# 战斗结算页右下角「离开」按钮。实测 (1609,983)-(1825,1028)。
VICTORY_LEAVE_ROI = _main_story_roi(1588, 963, 1846, 1042)

VICTORY_LEAVE_POINT = (1717, 1004)

# 奖励弹窗：中部「可获得奖励」标题 + 底部「点击画面即可返回」提示。
# 实测文字框 (1192,789)-(1306,816) 与 (866,975)-(1040,1004)，取中间并稍放宽。
REWARD_POPUP_TITLE_ROI = _main_story_roi(1150, 760, 1350, 850)
REWARD_POPUP_HINT_ROI = _main_story_roi(800, 940, 1100, 1030)

# 关闭弹窗时点哪儿：优先点「点击画面即可返回」这行提示本身（弹窗的证据就是它，
# 且它落在屏幕中下部，左边是「前往当前探索」x≈170、右边是「自动进行」x≈1655，
# 中间这片空地怎么点都不会误触）。两行文字都定位不到坐标时退回这块提示框的中心。
REWARD_POPUP_HINT_POINT = (950, 985)

# 结算页顶部标题，用于区分「真结算」与其它带「离开」的弹窗。
VICTORY_TITLE_ROI = _main_story_roi(1250, 20, 1900, 210)

# 「完成主线任务 / 已完成XX难度的所有探索」终局提示。实测文字框
# (1174,374)-(1424,406) 与 (1180,413)-(1402,439)，取外扩后的合并区域。
# 2026-11-17 实机：该难度所有节点刷完后弹出此提示，画面上再无可点按钮，
# 旧实现把它归为「其他界面」，等满战斗等待 120 秒后按卡住报失败。
ALL_CLEAR_ROI = _main_story_roi(1100, 350, 1500, 470)

ALL_CLEAR_KEYWORDS = ("完成主线任务", "所有探索")

# ---- 从大厅自动开跑：任务追踪条 → 难度弹窗 → 跳过剧情 → 立刻前往 ----
# 全部按 2026-11-17 23:30 的实机截图量取，文字框都是 OCR 实测结果（参考坐标）。

# 右侧任务追踪条上的「开始极难难度 / 开始困难难度」，点它弹出难度确认框。
# 实测文字框 (1574,149)-(1706,175)；上面一行是「主线」，下面「每周」从 y=193 起，
# 所以框底收到 195 只留这一行。「开始」「难度」各命中一次才认，避免误点别的条目。
QUEST_START_ROI = _main_story_roi(1500, 132, 1790, 195)
QUEST_START_KEYWORDS = ("开始", "难度")
QUEST_START_POINT = (1626, 162)

# 难度确认弹窗：标题「极难难度」(874,303)-(1072,352) +
# 「难度仅适用于主线任务战斗。是否开始主线任务？」(736,361)-(1172,387)。
# 这句说明与难度无关，困难/极难通用。
DIFFICULTY_DIALOG_ROI = _main_story_roi(700, 285, 1200, 400)
DIFFICULTY_DIALOG_KEYWORDS = ("难度仅适用于主线任务战斗",)
# 「领取探索」按钮文字 (1008,741)-(1088,762)，下面还有一行「ENTER OR SPACE」。
DIFFICULTY_CONFIRM_ROI = _main_story_roi(950, 715, 1140, 800)
DIFFICULTY_CONFIRM_KEYWORDS = ("领取探索",)
DIFFICULTY_CONFIRM_POINT = (1048, 751)

# 「进行快速探索」确认弹窗：说明「完成普通难度后，可省略剧情快速进行探索。
# 是否前往快速探索界面？」(586,518)-(1226,538)，按钮「立刻前往」(994,595)-(1097,634)。
FAST_EXPLORE_DIALOG_ROI = _main_story_roi(560, 480, 1260, 555)
FAST_EXPLORE_DIALOG_KEYWORDS = ("是否前往快速探索界面",)
FAST_EXPLORE_GO_ROI = _main_story_roi(950, 580, 1140, 650)
FAST_EXPLORE_GO_KEYWORDS = ("立刻前往",)
FAST_EXPLORE_GO_POINT = (1045, 615)

# 剧情过场：HUD 全部隐藏，右上角只剩跳过图标和它下面的「F2」角标。
# 实测图标亮块 (1623,48)-(1658,72)，中心 (1640,60) 就是要点的位置；
# 「F2」文字框在 (1641,105) 附近，只用来确认「这是过场」，不点它
# （F2 是游戏内的跳过热键，本项目只有鼠标点击能力）。
CUTSCENE_SKIP_HINT_ROI = _main_story_roi(1590, 78, 1690, 125)
CUTSCENE_SKIP_HINT_KEYWORDS = ("F2",)
CUTSCENE_SKIP_POINT = (1640, 60)

# 过场里 HUD 会消失，上下留黑边。作为「没读到 F2」时的第二条判据：
# 上下各取 8% 高度，平均亮度都极低、而中间明显更亮，就算过场画面。
CUTSCENE_BAR_DARK = 20.0
CUTSCENE_BAR_BRIGHT_DELTA = 25.0
# 过场最多连点这么多次跳过，仍无变化就交回原本的等待/超时逻辑，不在这里停机。
CUTSCENE_SKIP_ATTEMPTS = 10

# 点了跳过之后，游戏会再问一次「跳过对话」确认框。它有两种位置/文案，都要认：
#   A 全黑过场（2026-11-17 23:44 卡住的那张）：
#     标题「跳过对话」(896,292)-(1021,326)；问句「是否结束剧情观看?」(869,700)-(1040,723)；
#     「确认」(1020,767)-(1067,792)。
#   B 剧情演出里点 F2 跳过（2026-11-17 23:49 实机截图）：
#     标题「跳过对话」(898,442)-(1022,477)；正文「跳过对话。/可于剧情菜单重新查看。」；
#     「确认」(1021,616)-(1070,642)。
# 两种位置都靠「跳过对话」这四个字 + 「确认」按钮，所以 ROI 取成能覆盖两者的竖向长条。
# 注意 B 里右上角还留着「F2」提示，判定必须让这个弹窗优先于过场，否则会一直去点跳过图标。
SKIP_CONFIRM_TITLE_ROI = _main_story_roi(860, 270, 1070, 480)
SKIP_CONFIRM_TITLE_KEYWORDS = ("跳过对话",)
SKIP_CONFIRM_BODY_ROI = _main_story_roi(820, 490, 1120, 740)
SKIP_CONFIRM_BODY_KEYWORDS = ("是否结束剧情观看", "可于剧情菜单重新查看")
# 只框「确认」：左边「取消」到 x≈900 为止，x 从 990 起模糊匹配也不会串过去。
SKIP_CONFIRM_OK_ROIS = (
    _main_story_roi(990, 600, 1100, 650),
    _main_story_roi(990, 750, 1100, 805),
)
SKIP_CONFIRM_OK_KEYWORDS = ("确认",)
SKIP_CONFIRM_OK_POINTS = ((1046, 629), (1044, 780))
# 点「确认」最多整轮重来几次（每轮内部还有 CLICK_RETRY_ATTEMPTS 次点击）。
SKIP_CONFIRM_ROUNDS = 6

# 剧情演出：没有跳过图标、底部一行对白 + 结尾一个小三角，只能点画面继续。
# 对白有两种位置，都要框住：
#   - 关卡里的演出：名字 (290,919)-(314,942)，正文 (291,952)-(902,978)；
#   - 全屏 CG 上的演出（角色立绘 + 居中字幕）：名字与正文实测 903~948，
#     比上面的高出一行多，2026-11-18 的实机截图里正文 903~948，
#     旧 ROI 从 925 起只切到字的下半截，OCR 读成 "["，任务因此判定「界面持续无法识别」。
# 所以纵向上沿抬到 880、下沿放到 1050；横向上三条 400 参考像素宽的横条从左铺到右，
# 每条 800 实际像素，不会因为 PaddleOCR 把长边缩到 960 而丢字。
STORY_SUBTITLE_ROIS = (
    _main_story_roi(260, 880, 660, 1050),
    _main_story_roi(660, 880, 1060, 1050),
    _main_story_roi(1060, 880, 1460, 1050),
)
# 一句对白至少这么多汉字才算「在演剧情」，避免把零星噪声当对白乱点。
STORY_SUBTITLE_MIN_CHARS = 4
# 没有「读不到就判定」这回事：一晚上剧情可能几十句，给足次数再交给等待逻辑兜底。
STORY_ADVANCE_CLICKS = 120
STORY_ADVANCE_POINT = (960, 990)

IN_STAGE_KEYWORDS = ("进行快速探索",)

AUTO_KEYWORDS = ("自动进行",)

BATTLE_KEYWORDS = ("战斗",)

VICTORY_KEYWORDS = ("离开",)

# 战斗内 HUD：右下角与结算按钮是同一块屏幕区域，2026-11-16 实机日志里
# 「结束自动战斗esc」被「战斗」关键字命中、随后「TURN.1 / BATTLE」被当成
# 未知列表按钮直接停机。战斗内必须先于按钮判定被识别出来。
# 简体客户端里 BATTLE 只在战斗内 HUD 出现，列表页那个按钮是中文「战斗」。
BATTLE_HUD_KEYWORDS = ("结束自动战斗", "battle")

# 战斗**没有自动跑起来**时，右下角会停在「TURN. N | BATTLE」长条上等玩家点：
# 回合不走、Boss 血量一动不动。2026-11-18 00:03 就是卡在这里白等满 120 秒。
# 它和列表页「战斗」按钮落在同一块 ROI（1580,972)-(1825,1048)，区别是多了
# 「TURN. N」——列表页永远读不到，自动战斗时那块 ROI 读到的也是「结束自动战斗」。
# 按 3840x2160 实机帧换算成参考坐标：整条 (1600,952)-(1841,1018)，
# 其中「BATTLE」文字 (1733,973)-(1812,993)，中心 (1772,983)。
BATTLE_TURN_KEYWORDS = ("turn",)
BATTLE_TURN_BUTTON_KEYWORDS = ("battle",)
BATTLE_TURN_POINT = (1772, 983)
# 一场战斗最多点多少次「BATTLE」。战斗回合数本来就有限（实测一回合 1.5 秒左右
# 就能开下一回合），给到 200 足够跑完整场；到上限也不判失败，退回去走超时兜底。
BATTLE_TURN_CLICKS = 200
# 点完 BATTLE 到下一回合按钮出现之间给一点时间，避免在同一帧上连点。
BATTLE_TURN_INTERVAL = 1.5

# 「可获得奖励」也出现在列表页常驻预览中，弹窗必须有返回提示。
REWARD_POPUP_KEYWORDS = ("点击画面即可返回",)

# 弹窗标题，只在算点击点时用：优先点返回提示，其次点标题。
REWARD_TITLE_KEYWORDS = ("可获得奖励",)

# 提示文字较长，OCR 偶尔错一字，允许一定模糊度。
REWARD_POPUP_FUZZY_RATIO = 0.7

# 「列表按钮未知」要持续这么久才真停机，避免单帧 OCR 噪声直接终止整轮。
LIST_OTHER_CONFIRM_SECONDS = 3.0

# 点击后界面应在这个时间内出现变化；超时视为输入被吞（游戏正在播动画、
# 刚关掉弹窗还没恢复输入），重试点击，而不是干等到战斗超时再停机。
CLICK_CONFIRM_SECONDS = 8.0

# 「自动进行」会连着推进一串节点，确认窗口给宽一些。
AUTO_CONFIRM_SECONDS = 20.0

# 单次点击最多尝试次数（含首次）。全部无效才存图停机。
CLICK_RETRY_ATTEMPTS = 3

# 开跑这三下（开始难度/领取探索/立刻前往）点不动时，整轮重来的次数与间隔。
# 一轮里已经含 CLICK_RETRY_ATTEMPTS 次点击，所以实际最多会点
# PREPARE_RETRY_ROUNDS × CLICK_RETRY_ATTEMPTS 次才判失败——宁可多试，不要一次就停。
PREPARE_RETRY_ROUNDS = 3
PREPARE_RETRY_SLEEP = 3.0

# 结算页失败文案。同一关卡连续失败到上限就停下来，避免反复消耗体力。
FAILED_KEYWORDS = ("mission failed", "战斗失败", "挑战失败")

MAX_CONSECUTIVE_BATTLE_FAILURES = 3


def _looks_like_battle_hud(*texts: object) -> bool:
    """判断这几块 ROI 的文字像不像战斗内 HUD（而非列表页按钮）。"""

    merged = "".join(normalize_ocr_text(text) for text in texts)
    if not merged:
        return False
    return any(keyword in merged for keyword in BATTLE_HUD_KEYWORDS)


def _looks_like_battle_turn(*texts: object) -> bool:
    """右下角是不是「TURN. N | BATTLE」——战斗停在开回合前，等玩家点 BATTLE。

    只认 TURN 这一个信号：列表页的按钮是中文「战斗」，自动战斗中那块 ROI 读到的是
    「结束自动战斗」，都不会有 TURN。OCR 对这四个字母不太稳（turm / tum / turn.），
    允许一字之差；再退化到去空格后的子串判断。

    按钮 ROI 与结算 ROI 有重叠，两个都传进来：实机那一帧是按钮=turn.battle、
    离开=turn.1battle，任一块读到都算。
    """

    merged = "".join(normalize_ocr_text(text) for text in texts)
    if not merged:
        return False
    if keyword_match_count(
        merged, BATTLE_TURN_KEYWORDS, fuzzy_ratio=0.75,
    ) >= 1:
        return True
    compact = "".join(merged.split()).lower()
    return "turn" in compact


def _looks_like_all_clear(text: object) -> bool:
    """判断是不是「该难度主线已全部探索完成」的终局提示。

    实机文案是「完成主线任务极难」+「已完成极难难度的所有探索。」
    两行一起出现；任一行单独命中都算，OCR 只读到一行时也能认出来。
    """

    merged = normalize_ocr_text(text)
    if not merged:
        return False
    return keyword_match_count(
        merged,
        ALL_CLEAR_KEYWORDS,
        fuzzy_ratio=BUTTON_FUZZY_RATIO,
    ) >= 1


def _looks_like_quest_start(text: object) -> bool:
    """任务追踪条上是不是「开始XX难度」。两个词都命中才算，避免误点别的条目。"""

    merged = normalize_ocr_text(text)
    if not merged:
        return False
    return keyword_match_count(
        merged, QUEST_START_KEYWORDS, fuzzy_ratio=BUTTON_FUZZY_RATIO,
    ) >= len(QUEST_START_KEYWORDS)


def _looks_like_difficulty_dialog(text: object) -> bool:
    """是不是「XX难度 / 难度仅适用于主线任务战斗。是否开始主线任务？」确认框。"""

    merged = normalize_ocr_text(text)
    if not merged:
        return False
    return keyword_match_count(
        merged, DIFFICULTY_DIALOG_KEYWORDS, fuzzy_ratio=BUTTON_FUZZY_RATIO,
    ) >= 1


def _looks_like_fast_explore_dialog(text: object) -> bool:
    """是不是「是否前往快速探索界面？」确认框。"""

    merged = normalize_ocr_text(text)
    if not merged:
        return False
    return keyword_match_count(
        merged, FAST_EXPLORE_DIALOG_KEYWORDS, fuzzy_ratio=BUTTON_FUZZY_RATIO,
    ) >= 1


def _looks_like_cutscene(text: object) -> bool:
    """右上角读到「F2」跳过热键＝正在播剧情过场。

    这块 ROI 只框「F2」两个字，但实测 OCR 会读成「f2」「Fz」「F 2」，
    所以先按关键字模糊匹配，再退一步认「f + 数字/z」这种残读。
    注意：这块区域紧贴追踪条，读到「主线」这类无关文字不算命中。
    """

    merged = normalize_ocr_text(text)
    if not merged:
        return False
    if keyword_match_count(merged, CUTSCENE_SKIP_HINT_KEYWORDS, fuzzy_ratio=0.9) >= 1:
        return True
    compact = "".join(merged.split()).lower()
    if len(compact) > 3:
        return False
    return "f" in compact and ("2" in compact or "z" in compact)


def _looks_like_skip_confirm(*texts: object) -> bool:
    """是不是「跳过对话」这个二次确认框。

    标题「跳过对话」和两种正文（「是否结束剧情观看」「可于剧情菜单重新查看」）
    任一条读到就够：这三句都只在这个弹窗里出现。
    """

    merged = "".join(normalize_ocr_text(text) for text in texts)
    if not merged:
        return False
    for keywords in (SKIP_CONFIRM_TITLE_KEYWORDS, SKIP_CONFIRM_BODY_KEYWORDS):
        if keyword_match_count(merged, keywords, fuzzy_ratio=BUTTON_FUZZY_RATIO) >= 1:
            return True
    return False


def _looks_like_story_subtitle(text: object) -> bool:
    """是不是剧情演出底部的那句对白（没有跳过图标时只能点画面继续）。

    这里不能按关键字认——对白每句都不一样。改数汉字：一行像样的对白至少有
    4 个汉字。战斗伤害数字、列表页的杂字都凑不出这个数。
    """

    merged = normalize_ocr_text(text)
    if not merged:
        return False
    han = sum(1 for char in merged if "一" <= char <= "鿿")
    return han >= STORY_SUBTITLE_MIN_CHARS


def _looks_like_reward_popup(*texts: object) -> bool:
    """判断这几块 ROI 的文字像不像奖励弹窗（只认弹窗独有的底部提示）。"""

    merged = "".join(normalize_ocr_text(text) for text in texts)
    if not merged:
        return False
    return keyword_match_count(
        merged,
        REWARD_POPUP_KEYWORDS,
        fuzzy_ratio=REWARD_POPUP_FUZZY_RATIO,
    ) >= 1


# OCR 文本短、字形相近，允许一定模糊度；0.75 对 4 字词只容忍 1 字差异。
BUTTON_FUZZY_RATIO = 0.75


class MainStoryState(str, Enum):
    IN_STAGE = "关卡内"
    LIST_AUTO = "列表-自动进行"
    LIST_BATTLE = "列表-战斗"
    LIST_OTHER_BUTTON = "列表-按钮未知"
    VICTORY = "战斗结算"
    IN_BATTLE = "战斗内"
    IN_BATTLE_TURN = "战斗-待开回合"
    REWARD_POPUP = "奖励弹窗"
    ALL_CLEAR = "主线已完成"
    QUEST_START = "追踪条-开始难度"
    DIFFICULTY_DIALOG = "难度确认弹窗"
    CUTSCENE = "剧情过场"
    SKIP_CONFIRM = "跳过对话确认"
    STORY_DIALOG = "剧情演出"
    FAST_EXPLORE_DIALOG = "快速探索确认弹窗"
    BUSY = "其他界面"


# 关掉「主线自动开始难度」时，这几个界面退回原来的「等待 / 超时停机」处理。
PREPARE_STATES = (
    MainStoryState.QUEST_START,
    MainStoryState.DIFFICULTY_DIALOG,
    MainStoryState.FAST_EXPLORE_DIALOG,
)

# 大厅开跑的三步：状态 → (ROI, OCR 名, 关键字, 兜底点击点, 按钮文字)。
PREPARE_ACTIONS = {
    MainStoryState.QUEST_START: (
        QUEST_START_ROI, "主线开始难度", QUEST_START_KEYWORDS,
        QUEST_START_POINT, "开始难度",
    ),
    MainStoryState.DIFFICULTY_DIALOG: (
        DIFFICULTY_CONFIRM_ROI, "主线领取探索", DIFFICULTY_CONFIRM_KEYWORDS,
        DIFFICULTY_CONFIRM_POINT, "领取探索",
    ),
    MainStoryState.FAST_EXPLORE_DIALOG: (
        FAST_EXPLORE_GO_ROI, "主线立刻前往", FAST_EXPLORE_GO_KEYWORDS,
        FAST_EXPLORE_GO_POINT, "立刻前往",
    ),
}


def classify_main_story_state(
    in_stage_text: object,
    button_text: object,
    victory_text: object,
    reward_text: object,
    all_clear_text: object = "",
    quest_start_text: object = "",
    difficulty_dialog_text: object = "",
    fast_explore_text: object = "",
    cutscene_text: object = "",
    skip_confirm_text: object = "",
    story_text: object = "",
) -> MainStoryState:
    """把几块 ROI 的 OCR 文本归到一个界面状态。

    结算页优先判定：战斗刚结束时列表页已经消失，「离开」只出现在结算页右下角，
    因此这一块 ROI 读到「离开」即可认定结算，不再要求标题同时命中——
    结算页的标题是艺术字，OCR 命中率远不如按钮文字稳定。

    奖励弹窗优先于按钮判定：弹窗出现时「自动进行」仍在列表页 ROI 内可见，
    若把「自动进行」当列表页处理，点击只会把弹窗点掉，不会真正推进。

    「主线已完成」放在最前：终局提示出现时画面上没有任何按钮文字，
    必须先在它落入 BUSY（等待-超时-报失败）之前认出来。

    剧情过场与两个确认弹窗也排在前面：过场里 HUD 全隐藏，弹窗是模态的
    （背后列表页文字会被压暗但仍可能被 OCR 到），落到按钮判定会点错地方。
    """

    # 跳过确认框排在过场之前：剧情演出里点 F2 跳过时，右上角的「F2」提示还留在
    # 弹窗背后，先认过场就会一直去点跳过图标，永远点不到「确认」。
    if _looks_like_skip_confirm(skip_confirm_text):
        return MainStoryState.SKIP_CONFIRM

    if _looks_like_cutscene(cutscene_text):
        return MainStoryState.CUTSCENE

    if _looks_like_fast_explore_dialog(fast_explore_text):
        return MainStoryState.FAST_EXPLORE_DIALOG

    if _looks_like_difficulty_dialog(difficulty_dialog_text):
        return MainStoryState.DIFFICULTY_DIALOG

    if _looks_like_all_clear(all_clear_text):
        return MainStoryState.ALL_CLEAR

    if keyword_match_count(victory_text, VICTORY_KEYWORDS):
        return MainStoryState.VICTORY

    if keyword_match_count(in_stage_text, IN_STAGE_KEYWORDS, fuzzy_ratio=BUTTON_FUZZY_RATIO):
        return MainStoryState.IN_STAGE

    # 战斗内的右下角 HUD 与列表按钮共用同一块区域，必须在按钮词表之前拦下，
    # 否则「结束自动战斗esc」会被当成「战斗」按钮、「TURN.1 BATTLE」会被
    # 当成未知按钮而触发停机。
    # 其中「TURN. N | BATTLE」表示这一回合还没开始，要单独认出来去点 BATTLE。
    if _looks_like_battle_turn(button_text, victory_text):
        return MainStoryState.IN_BATTLE_TURN

    if _looks_like_battle_hud(button_text, victory_text):
        return MainStoryState.IN_BATTLE

    # 奖励弹窗可能盖住列表页，必须在按钮词表之前拦下。
    if _looks_like_reward_popup(reward_text):
        return MainStoryState.REWARD_POPUP

    if keyword_match_count(button_text, AUTO_KEYWORDS, fuzzy_ratio=BUTTON_FUZZY_RATIO):
        return MainStoryState.LIST_AUTO

    if keyword_match_count(button_text, BATTLE_KEYWORDS, fuzzy_ratio=BUTTON_FUZZY_RATIO):
        return MainStoryState.LIST_BATTLE

    # 大厅里追踪条写着「开始XX难度」：这是「还没进关卡」的正常起点，
    # 不是认不出的界面。放在列表判定之后，避免抢走真正的推进动作。
    if _looks_like_quest_start(quest_start_text):
        return MainStoryState.QUEST_START

    # 剧情演出（底部有对白、没有跳过图标）：排在所有按钮之后，
    # 真正的推进按钮永远不会被它抢走。
    if _looks_like_story_subtitle(story_text):
        return MainStoryState.STORY_DIALOG

    if normalize_ocr_text(button_text):
        return MainStoryState.LIST_OTHER_BUTTON

    return MainStoryState.BUSY


class MainStoryVisionMixin:
    """主线界面识别与点击取点的公共工具：切 ROI 做 OCR、按关键字定位按钮中心。

    快速探索与野外推进共用：只依赖 self.ocr/info_set，不碰任何业务状态，
    所以两边都能继承它而不带进对方的状态机。
    """

    def _main_story_ocr(
        self,
        frame,
        bounds: tuple[float, float, float, float],
        name: str,
        threshold: float,
    ) -> str:
        crop = crop_relative(frame, bounds)
        if crop is None or crop.size == 0:
            return ""
        try:
            boxes = self.ocr(frame=crop, threshold=threshold, log=False, name=name)
        except (TaskDisabledException, FinishedException):
            raise
        except Exception as exc:
            self.info_set(f"{name} OCR错误", str(exc))
            return ""
        return normalize_ocr_text(" ".join(box.name for box in boxes if getattr(box, "name", "")))

    def _main_story_button_point(
        self,
        frame,
        bounds: tuple[float, float, float, float],
        name: str,
        keywords: tuple[str, ...],
        threshold: float,
        fallback: tuple[float, float],
    ) -> tuple[float, float]:
        """在按钮 ROI 里定位关键字文字框的中心；定位不到就用实测标定的兜底点。

        ROI 都取得很小（只框按钮文字本身），所以不存在大图缩放漏检的问题；
        兜底点是同一块 ROI 里的实测中心，命不命中都不至于点到别的按钮上。
        """

        boxes, crop = self._main_story_ocr_boxes(frame, bounds, name, threshold)
        for box in boxes:
            if not getattr(box, "name", ""):
                continue
            if not keyword_match_count(
                box.name, keywords, fuzzy_ratio=BUTTON_FUZZY_RATIO,
            ):
                continue
            return self._main_story_box_point(bounds, crop, box)
        return fallback

    def _main_story_skip_confirm_point(
        self,
        frame,
        threshold: float,
    ) -> tuple[float, float]:
        """「跳过对话」确认框的「确认」在哪儿。

        这个弹窗有两种位置（全黑过场里靠下、剧情演出里居中偏上），所以两块 ROI
        都扫一遍，谁先读到「确认」就点谁；都读不到时用剧情演出那种的实测点
        (1046,629)——它落在弹窗正文与「取消」之间，不会误点到别的按钮。
        """

        for bounds in SKIP_CONFIRM_OK_ROIS:
            boxes, crop = self._main_story_ocr_boxes(frame, bounds, "主线确认跳过", threshold)
            for box in boxes:
                if not keyword_match_count(
                    getattr(box, "name", ""), SKIP_CONFIRM_OK_KEYWORDS,
                    fuzzy_ratio=BUTTON_FUZZY_RATIO,
                ):
                    continue
                return self._main_story_box_point(bounds, crop, box)
        return SKIP_CONFIRM_OK_POINTS[0]

    def _main_story_battle_turn_point(
        self,
        frame,
        threshold: float,
    ) -> tuple[float, float]:
        """战斗里「BATTLE」按钮点哪儿：优先点 OCR 到的 BATTLE 文字框中心。

        ROI 就是列表按钮那块长条（战斗内这块读出来是「TURN. N BATTLE」），
        换算出来的点落在长条内；读不到 BATTLE 时用实测文字中心 (1772,983)，
        它同样在长条里，不会点到旁边的「切换视角 / 切换画面」。
        """

        boxes, crop = self._main_story_ocr_boxes(
            frame, LIST_BUTTON_ROI, "主线回合按钮", threshold,
        )
        for box in boxes:
            if not keyword_match_count(
                getattr(box, "name", ""), BATTLE_TURN_BUTTON_KEYWORDS,
                fuzzy_ratio=BUTTON_FUZZY_RATIO,
            ):
                continue
            return self._main_story_box_point(LIST_BUTTON_ROI, crop, box)
        return BATTLE_TURN_POINT

    def _main_story_subtitle_point(
        self,
        frame,
        threshold: float,
    ) -> tuple[float, float]:
        """剧情演出时点哪儿：优先点对白文字框本身，读不到才用实测字幕带中心。

        点对白是在「点画面继续」里最有依据的位置——它就画在那行字上，
        不会碰到别的东西；字幕带中心 (960,990) 是同一块区域实测的兜底。
        """

        for bounds in STORY_SUBTITLE_ROIS:
            boxes, crop = self._main_story_ocr_boxes(frame, bounds, "主线剧情字幕", threshold)
            for box in boxes:
                if normalize_ocr_text(getattr(box, "name", "")):
                    return self._main_story_box_point(bounds, crop, box)
        return STORY_ADVANCE_POINT

    @staticmethod
    def _main_story_cutscene_bars(frame) -> bool:
        """过场画面上下有黑边、中间有内容：没读到「F2」时的第二判据。"""

        try:
            height = frame.shape[0]
            if not height:
                return False
            band = max(1, int(height * 0.08))
            top = float(frame[:band].mean())
            bottom = float(frame[-band:].mean())
            middle = float(frame[band * 2:height - band * 2].mean())
        except Exception:
            return False
        if middle <= 0:
            return False
        return (
            top <= CUTSCENE_BAR_DARK
            and bottom <= CUTSCENE_BAR_DARK
            and middle - max(top, bottom) >= CUTSCENE_BAR_BRIGHT_DELTA
        )

    def _main_story_reward_click_point(
        self,
        frame,
        threshold: float,
    ) -> tuple[float, float]:
        """算奖励弹窗的关闭点击点。

        顺序：返回提示文字框中心 → 奖励标题文字框中心 → 提示框中心。

        2026-11-17 22:46 实机教训：这里原来在一张 1820x1120 的大图上找
        「可获得奖励」。PaddleOCR 会把大图缩边到 960 再做检测，小字直接漏检——
        同一帧小图读到「可获得奖励」0.998、「点击画面即可返回」0.993，大图只读到
        「1,040」「区回。」「自动进行」。于是每次都拿不到点，白等 20 秒后按卡住
        报失败。小图上算点就绕开了缩放，两行都读不到时退回的提示框中心点也
        有据可依（此刻提示框刚被 OCR 命中，点落在框内是确定的，不是盲点）。
        """

        for bounds, name, keywords in (
            (REWARD_POPUP_HINT_ROI, "主线奖励提示", REWARD_POPUP_KEYWORDS),
            (REWARD_POPUP_TITLE_ROI, "主线奖励标题", REWARD_TITLE_KEYWORDS),
        ):
            boxes, crop = self._main_story_ocr_boxes(frame, bounds, name, threshold)
            for box in boxes:
                if not getattr(box, "name", ""):
                    continue
                if not keyword_match_count(
                    box.name, keywords, fuzzy_ratio=BUTTON_FUZZY_RATIO,
                ):
                    continue
                return self._main_story_box_point(bounds, crop, box)
        return REWARD_POPUP_HINT_POINT

    def _main_story_ocr_boxes(
        self,
        frame,
        bounds: tuple[float, float, float, float],
        name: str,
        threshold: float,
    ) -> tuple[list, object]:
        """OCR 一块 ROI，同时把识别框和裁剪图交回去，方便换算点击坐标。"""

        crop = crop_relative(frame, bounds)
        if crop is None or crop.size == 0:
            return [], None
        try:
            boxes = self.ocr(frame=crop, threshold=threshold, log=False, name=name)
        except (TaskDisabledException, FinishedException):
            raise
        except Exception as exc:
            self.info_set(f"{name} OCR错误", str(exc))
            return [], crop
        return list(boxes), crop

    @staticmethod
    def _main_story_box_point(
        bounds: tuple[float, float, float, float],
        crop,
        box,
    ) -> tuple[float, float]:
        """把 crop 内的识别框中心换算成参考分辨率坐标。

        box 的 x/y 是以裁剪图左上角为原点的像素，先归一化再映射回 ROI 在参考
        分辨率里的位置；crop 为空时退回 ROI 中心，避免除零。
        """

        x1, y1, x2, y2 = bounds
        if crop is None or getattr(crop, "size", 0) == 0:
            return ((x1 + x2) / 2) * REFERENCE_WIDTH, ((y1 + y2) / 2) * REFERENCE_HEIGHT
        ch, cw = crop.shape[:2]
        cx = (x1 + (box.x + box.width / 2) / cw * (x2 - x1)) * REFERENCE_WIDTH
        cy = (y1 + (box.y + box.height / 2) / ch * (y2 - y1)) * REFERENCE_HEIGHT
        return cx, cy


class MainStoryConfigMixin:
    """主线刷取的配置项，键名统一以「主线」开头，避免与日常任务冲突。"""

    MAIN_STORY_CONFIG_KEYS = (
        "主线自动开始难度",
        "主线关卡标题校验",
        "主线战斗次数上限",
        "主线时间上限分钟",
        "主线等待秒数",
        "主线战斗等待秒数",
        "主线 OCR 阈值",
        "主线未知界面时停止",
    )

    def _install_main_story_config(self) -> None:
        self.default_config.update(
            {
                "启用": True,
                "主线自动开始难度": True,
                "主线关卡标题校验": "",
                "主线战斗次数上限": 0,
                "主线时间上限分钟": 0.0,
                "主线等待秒数": 20.0,
                "主线战斗等待秒数": 120.0,
                "主线 OCR 阈值": 0.2,
                "主线未知界面时停止": True,
            }
        )
        self.config_description.update(
            {
                "启用": "是否执行主线快速探索刷取。",
                "主线自动开始难度": (
                    "开启时，从大厅开始：点右侧追踪条的「开始困难/极难难度」→"
                    "「领取探索」→自动跳过剧情→「立刻前往」，然后进入刷取循环。"
                    "关闭则只处理已经打开的关卡/列表页。"
                ),
                "主线关卡标题校验": (
                    "关卡标题应包含的文字（如「血骑士-极难」）。留空则不校验；"
                    "填了但列表页标题对不上时会拒绝开始，避免刷错关卡。"
                ),
                "主线战斗次数上限": "完成多少次「战斗」后停止，0 表示不限。",
                "主线时间上限分钟": "运行多久后停止，0 表示不限。",
                "主线等待秒数": "每次点击后等待界面变化的最长秒数。",
                "主线战斗等待秒数": (
                    "进入战斗后等待结算的最长秒数；期间认不出界面属于正常，超时才会停止。"
                    "战斗停在「TURN. N / BATTLE」等玩家开回合时，任务会自己点 BATTLE，"
                    "这里只是点满次数仍打不完时的兜底。"
                ),
                "主线 OCR 阈值": "按钮与标题文字识别的最低置信度。",
                "主线未知界面时停止": (
                    "开启时，遇到既不是关卡内、也不是列表或结算的界面会停机并存图；"
                    "关闭则一直等待到超时。"
                ),
            }
        )
        self.config_type.update(
            {
                "主线战斗次数上限": {"min": 0, "max": 999, "step": 1},
                "主线时间上限分钟": {"min": 0.0, "max": 1440.0, "step": 5.0},
                "主线等待秒数": {"min": 5.0, "max": 120.0, "step": 1.0},
                "主线战斗等待秒数": {"min": 20.0, "max": 600.0, "step": 10.0},
                "主线 OCR 阈值": {"min": 0.05, "max": 0.95, "step": 0.01},
            }
        )


class MainStoryFeatureMixin(MainStoryVisionMixin):
    """主线刷取的状态机主体。"""

    def run_main_story_farm(self) -> bool:
        battle_limit = int(self.config.get("主线战斗次数上限", 0) or 0)
        minute_limit = float(self.config.get("主线时间上限分钟", 0.0) or 0.0)
        battle_wait = float(self.config.get("主线战斗等待秒数", 120.0))
        threshold = float(self.config.get("主线 OCR 阈值", 0.2))
        stop_on_unknown = bool(self.config.get("主线未知界面时停止", True))
        auto_start = bool(self.config.get("主线自动开始难度", True))

        deadline = monotonic() + minute_limit * 60 if minute_limit > 0 else None

        battles = 0
        autos = 0
        cycles = 0
        battle_failures = 0
        battle_turn_clicks = 0
        cutscene_clicks = 0
        story_clicks = 0
        skip_confirm_rounds = 0
        prepare_misses = 0
        busy_since: float | None = None
        busy_announced = False
        unknown_since: float | None = None
        last_evidence = ""

        self._main_story_set_stats(battles, autos, cycles)

        while True:
            if deadline is not None and monotonic() >= deadline:
                self.log_info("主线刷取：到达时间上限，结束。")
                self._main_story_set_stats(battles, autos, cycles)
                return True

            frame = self.capture_frame()
            state, evidence = self._main_story_state(frame, threshold)
            # 界面判定没变就不重复写状态，避免状态面板被同一行刷屏。
            if evidence != last_evidence:
                last_evidence = evidence
                self.info_set("主线当前界面", f"{state.value}｜{evidence}")

            if state is not MainStoryState.CUTSCENE:
                cutscene_clicks = 0
            if state is not MainStoryState.STORY_DIALOG:
                story_clicks = 0
            if state is not MainStoryState.SKIP_CONFIRM:
                skip_confirm_rounds = 0
            if state not in PREPARE_STATES:
                prepare_misses = 0
            if not auto_start and state in PREPARE_STATES:
                # 关掉自动开跑：这几个界面按未识别处理，交给下面的等待/超时逻辑。
                state = MainStoryState.BUSY

            if state is MainStoryState.CUTSCENE and cutscene_clicks < CUTSCENE_SKIP_ATTEMPTS:
                busy_since, busy_announced = None, False
                unknown_since = None
                cutscene_clicks += 1
                self.log_info(
                    f"主线刷取：剧情过场，点右上角跳过（第 {cutscene_clicks} 次）。",
                )
                self._click_reference(*CUTSCENE_SKIP_POINT)
                self.sleep(1.0)
                continue

            if state is MainStoryState.SKIP_CONFIRM:
                # 跳过图标点掉后游戏再问一次「跳过对话」——点「确认」。
                # 这里也走带重试的点击：弹窗刚出来时输入还可能被吃掉。
                if skip_confirm_rounds < SKIP_CONFIRM_ROUNDS:
                    skip_confirm_rounds += 1
                    busy_since, busy_announced = None, False
                    unknown_since = None
                    self.log_info(
                        f"主线刷取：跳过对话确认框，点「确认」"
                        f"（第 {skip_confirm_rounds}/{SKIP_CONFIRM_ROUNDS} 轮）。",
                    )
                    point = self._main_story_skip_confirm_point(frame, threshold)
                    if self._main_story_click_and_confirm(
                        state, point, CLICK_CONFIRM_SECONDS, threshold, "确认跳过对话",
                    ):
                        skip_confirm_rounds = 0
                        continue
                    # 一轮点不动也不判失败：稍等再整轮重来，交给下面的等待逻辑兜底。
                    self.log_info("主线刷取：跳过对话确认框点「确认」无响应，稍后重试。")
                    self.sleep(1.0)
                    continue
                if skip_confirm_rounds == SKIP_CONFIRM_ROUNDS:
                    skip_confirm_rounds += 1  # 只提示一次，之后交给等待逻辑
                    self.log_info(
                        f"主线刷取：跳过对话确认框已试 {SKIP_CONFIRM_ROUNDS} 轮仍点不动，"
                        "改为等待确认。",
                    )

            if state is MainStoryState.STORY_DIALOG:
                # 没有跳过图标的剧情演出：底部一句对白 + 一个小三角，点画面继续下一句。
                # 一句一次点击，整段剧情可能几十句，所以次数给得很宽；
                # 用完也不是直接判失败，而是交回下面的等待/超时逻辑。
                if story_clicks < STORY_ADVANCE_CLICKS:
                    busy_since, busy_announced = None, False
                    unknown_since = None
                    story_clicks += 1
                    point = self._main_story_subtitle_point(frame, threshold)
                    self.log_info(
                        f"主线刷取：剧情演出，点画面继续（第 {story_clicks} 次）。",
                    )
                    self._click_reference(*point)
                    self.sleep(1.0)
                    continue
                if story_clicks == STORY_ADVANCE_CLICKS:
                    story_clicks += 1  # 只提示一次，之后交给等待逻辑
                    self.log_info(
                        f"主线刷取：剧情演出已连点 {STORY_ADVANCE_CLICKS} 次还停在对话，"
                        "改为等待确认。",
                    )

            prepare = PREPARE_ACTIONS.get(state)
            if prepare is not None:
                # 大厅开跑的三步：追踪条「开始难度」→「领取探索」→「立刻前往」。
                # 点不动不立刻判失败：一轮点不动就歇几秒整轮重来，连续几轮才停机。
                busy_since, busy_announced = None, False
                unknown_since = None
                bounds, name, keywords, fallback, label = prepare
                self.log_info(f"主线刷取：{state.value}，点「{label}」。")
                point = self._main_story_button_point(
                    frame, bounds, name, keywords, threshold, fallback,
                )
                if self._main_story_click_and_confirm(
                    state, point, CLICK_CONFIRM_SECONDS, threshold, label,
                ):
                    prepare_misses = 0
                    continue
                prepare_misses += 1
                if prepare_misses < PREPARE_RETRY_ROUNDS:
                    self.log_info(
                        f"主线刷取：「{label}」这一轮点不动，"
                        f"第 {prepare_misses}/{PREPARE_RETRY_ROUNDS} 轮，"
                        f"等 {PREPARE_RETRY_SLEEP:.0f} 秒后重来。",
                    )
                    self.sleep(PREPARE_RETRY_SLEEP)
                    continue
                return self._main_story_stuck(
                    frame, f"点击「{label}」连续 {PREPARE_RETRY_ROUNDS} 轮无反应",
                )

            if state is MainStoryState.IN_STAGE:
                busy_since, busy_announced = None, False
                unknown_since = None
                # 关卡内看不到列表标题（那块区域是地图），此处不做关卡校验，
                # 校验统一放到列表页做。
                self.log_info("主线刷取：打开快速探索列表。")
                if self._main_story_click_and_confirm(
                    state, IN_STAGE_BUTTON_POINT, CLICK_CONFIRM_SECONDS, threshold,
                    "进行快速探索",
                ):
                    continue
                return self._main_story_stuck(frame, "点击「进行快速探索」后界面无变化")

            if state is MainStoryState.LIST_AUTO:
                busy_since, busy_announced = None, False
                unknown_since = None
                if not self._main_story_verify_title(frame, threshold):
                    return self._main_story_abort(frame, "关卡标题校验未通过")
                self.log_info("主线刷取：自动进行。")
                autos += 1
                self._main_story_set_stats(battles, autos, cycles)
                # 自动进行要跑完一串节点才回到列表或进战斗，确认窗口比普通点击宽。
                clicked = self._main_story_click_and_confirm(
                    state, LIST_BUTTON_POINT, AUTO_CONFIRM_SECONDS, threshold,
                    "自动进行",
                )
                if clicked and self._main_story_wait_change(state, battle_wait, threshold):
                    # 已离开列表，仍要继续等真正回到可操作界面。
                    continue
                if clicked:
                    return self._main_story_stuck(frame, "点击「自动进行」后界面长时间无变化")
                return self._main_story_stuck(frame, "点击「自动进行」后界面无变化")

            if state is MainStoryState.LIST_BATTLE:
                busy_since, busy_announced = None, False
                unknown_since = None
                if battle_limit and battles >= battle_limit:
                    self.log_info(f"主线刷取：已达战斗次数上限 {battle_limit}，结束。")
                    self._main_story_set_stats(battles, autos, cycles)
                    return True
                if not self._main_story_verify_title(frame, threshold):
                    return self._main_story_abort(frame, "关卡标题校验未通过")
                self.log_info(f"主线刷取：第 {battles + 1} 场战斗。")
                if not self._main_story_click_and_confirm(
                    state, LIST_BUTTON_POINT, CLICK_CONFIRM_SECONDS, threshold,
                    "战斗",
                ):
                    return self._main_story_stuck(
                        frame, "点击「战斗」多次后界面仍无变化（输入可能被游戏吞掉）",
                    )
                battles += 1
                # 新的一场战斗：这一场的「BATTLE」点击次数重新计。
                battle_turn_clicks = 0
                self._main_story_set_stats(battles, autos, cycles)
                # 已经离开列表页，接下来安心等战斗结算。
                if self._main_story_wait_change(state, battle_wait, threshold):
                    continue
                return self._main_story_stuck(frame, "点击「战斗」后未等到结算")

            if state is MainStoryState.IN_BATTLE_TURN:
                # 战斗没在自动跑：右下角停在「TURN. N | BATTLE」，回合不走、Boss 血量
                # 一动不动。点一下 BATTLE 等于替玩家开这一回合，下一回合再点。
                # 一场战斗的点击次数有上限，用完就交回下面的等待/超时逻辑，不在这里停机。
                unknown_since = None
                if battle_turn_clicks < BATTLE_TURN_CLICKS:
                    busy_since, busy_announced = None, False
                    battle_turn_clicks += 1
                    self.log_info(
                        f"主线刷取：战斗停在开回合前，点「BATTLE」"
                        f"（本场第 {battle_turn_clicks}/{BATTLE_TURN_CLICKS} 次）。",
                    )
                    point = self._main_story_battle_turn_point(frame, threshold)
                    self._click_reference(*point)
                    self.sleep(BATTLE_TURN_INTERVAL)
                    continue
                if battle_turn_clicks == BATTLE_TURN_CLICKS:
                    battle_turn_clicks += 1  # 只提示一次，之后交给等待逻辑
                    self.log_info(
                        f"主线刷取：本场战斗已点 {BATTLE_TURN_CLICKS} 次「BATTLE」还没打完，"
                        "改为等待确认。",
                    )

            if state is MainStoryState.VICTORY:
                busy_since, busy_announced = None, False
                unknown_since = None
                title = self._main_story_ocr(frame, VICTORY_TITLE_ROI, "主线结算标题", threshold)
                if keyword_match_count(title, FAILED_KEYWORDS):
                    battle_failures += 1
                    self.info_set("主线连续失败次数", battle_failures)
                    self.log_info(f"主线刷取：本场战斗失败（连续 {battle_failures} 次）。")
                    if battle_failures >= MAX_CONSECUTIVE_BATTLE_FAILURES:
                        return self._main_story_abort(
                            frame,
                            f"连续 {battle_failures} 场战斗失败，疑似打不过该节点，已停止",
                        )
                else:
                    battle_failures = 0
                    self.info_set("主线连续失败次数", 0)
                self.log_info("主线刷取：结算完成，离开战斗。")
                if self._main_story_click_and_confirm(
                    state, VICTORY_LEAVE_POINT, CLICK_CONFIRM_SECONDS, threshold,
                    "离开",
                ):
                    cycles += 1
                    self._main_story_set_stats(battles, autos, cycles)
                    continue
                return self._main_story_stuck(frame, "点击「离开」后界面无变化")

            if state is MainStoryState.REWARD_POPUP:
                busy_since, busy_announced = None, False
                unknown_since = None
                self.log_info("主线刷取：奖励弹窗，点击关闭。")
                point = self._main_story_reward_click_point(frame, threshold)
                if self._main_story_click_and_confirm(
                    state, point, CLICK_CONFIRM_SECONDS, threshold, "奖励弹窗",
                ):
                    continue
                return self._main_story_stuck(frame, "点击「奖励弹窗」后界面无变化")

            if state is MainStoryState.ALL_CLEAR:
                # 该难度所有节点都刷完了，游戏弹「完成主线任务 / 已完成…所有探索」。
                # 画面上没有可点按钮，这属于**正常终局**，不是卡住：
                # 存图留证后按成功结束，避免旧实现白等 120 秒再报失败。
                busy_since, busy_announced = None, False
                unknown_since = None
                self._main_story_set_stats(battles, autos, cycles)
                self.log_info("主线刷取：该难度主线已全部探索完成，任务正常结束。")
                self.save_frame("main_story_all_clear", frame)
                self.info_set("主线停止原因", "该难度主线已全部探索完成")
                return True

            if state is MainStoryState.LIST_OTHER_BUTTON:
                # 列表页还在，但按钮既不是自动进行也不是战斗：通常意味着本关已无
                # 可推进的节点，或是游戏新增了未知按钮。停下来比乱点安全。
                # 但单帧 OCR 噪声也会落进这里，所以要求持续确认一段时间才真停。
                if self.config.get("主线未知界面时停止", True):
                    if unknown_since is None:
                        unknown_since = monotonic()
                        busy_since, busy_announced = None, False
                        self.sleep(0.5)
                        continue
                    if monotonic() - unknown_since >= LIST_OTHER_CONFIRM_SECONDS:
                        return self._main_story_abort(
                            frame, "列表按钮文字未知（可能已无可刷节点）",
                        )
                    self.sleep(0.5)
                    continue
                unknown_since = None
                busy_since, busy_announced = None, False
                self.sleep(1.0)
                continue

            # BUSY / IN_BATTLE：战斗中、剧情中、掉落弹窗等。战斗期间认不出界面
            # 是正常的，用「主线战斗等待秒数」兜底，超时才认为真卡住了。
            # IN_BATTLE 特意不走独立分支，就是为了让它沿用同一套超时兜底；
            # IN_BATTLE_TURN 点满上限后也落到这里，同样交给超时兜底。
            unknown_since = None
            now = monotonic()
            if busy_since is None:
                busy_since = now
                if not busy_announced:
                    busy_announced = True
                    self.log_info("主线刷取：等待界面推进。")
            elif now - busy_since > battle_wait:
                if stop_on_unknown:
                    return self._main_story_stuck(
                        frame, f"界面无推进超过 {battle_wait:.0f} 秒",
                    )
                busy_since, busy_announced = now, False

            self.sleep(0.5)

    def _main_story_state(
        self,
        frame,
        threshold: float,
    ) -> tuple[MainStoryState, str]:
        in_stage = self._main_story_ocr(frame, IN_STAGE_BUTTON_ROI, "主线快速探索入口", threshold)
        button = self._main_story_ocr(frame, LIST_BUTTON_ROI, "主线列表按钮", threshold)
        victory = self._main_story_ocr(frame, VICTORY_LEAVE_ROI, "主线结算离开", threshold)
        reward_title = self._main_story_ocr(
            frame, REWARD_POPUP_TITLE_ROI, "主线奖励标题", threshold,
        )
        reward_hint = self._main_story_ocr(
            frame, REWARD_POPUP_HINT_ROI, "主线奖励提示", threshold,
        )
        reward = normalize_ocr_text(reward_title + " " + reward_hint)
        # 先只用主信号判一次。认成刷取过程中的界面就直接返回，一帧不多做 OCR；
        # 只有判成「其他界面 / 按钮未知」时才去认下面这几块——过场、两个开跑确认
        # 弹窗、大厅追踪条、终局提示都属于「屏幕上没有可点的推进按钮」的界面。
        state = classify_main_story_state(in_stage, button, victory, reward)
        cutscene = fast_explore = difficulty_dialog = quest_start = all_clear = ""
        skip_confirm = story = ""
        cutscene_bar = False
        if state in (MainStoryState.BUSY, MainStoryState.LIST_OTHER_BUTTON):
            # 按短路的顺序认，每块 ROI 都小（只框一行字），最多也就多花几次 OCR。
            # 注意短路条件是「关键字命中」而不是「读到文字」：这几块 ROI 会和
            # HUD 元素重叠（比如追踪条的「主线」就压在右上角 F2 那块上），
            # 读到不相干的字不能当成命中。剧情演出放最后，真实按钮永远优先。
            probes = (
                ("skip_confirm", (
                    (SKIP_CONFIRM_TITLE_ROI, "主线跳过对话标题"),
                    (SKIP_CONFIRM_BODY_ROI, "主线跳过对话正文"),
                ), _looks_like_skip_confirm),
                ("cutscene", ((CUTSCENE_SKIP_HINT_ROI, "主线跳过剧情"),), _looks_like_cutscene),
                ("fast", (
                    (FAST_EXPLORE_DIALOG_ROI, "主线快速探索确认"),
                ), _looks_like_fast_explore_dialog),
                ("difficulty", (
                    (DIFFICULTY_DIALOG_ROI, "主线难度确认"),
                ), _looks_like_difficulty_dialog),
                ("quest", ((QUEST_START_ROI, "主线开始难度"),), _looks_like_quest_start),
                ("all_clear", ((ALL_CLEAR_ROI, "主线完成提示"),), _looks_like_all_clear),
                ("story", (
                    (STORY_SUBTITLE_ROIS[0], "主线剧情字幕左"),
                    (STORY_SUBTITLE_ROIS[1], "主线剧情字幕"),
                    (STORY_SUBTITLE_ROIS[2], "主线剧情字幕右"),
                ), _looks_like_story_subtitle),
            )
            found: dict[str, str] = {}
            hit = False
            for key, chunks, predicate in probes:
                texts = [
                    self._main_story_ocr(frame, roi, label, threshold)
                    for roi, label in chunks
                ]
                merged = normalize_ocr_text(" ".join(texts))
                found[key] = merged
                if predicate(merged):
                    hit = True
                    break
            cutscene = found.get("cutscene", "")
            skip_confirm = found.get("skip_confirm", "")
            fast_explore = found.get("fast", "")
            difficulty_dialog = found.get("difficulty", "")
            quest_start = found.get("quest", "")
            all_clear = found.get("all_clear", "")
            story = found.get("story", "")
            if not hit:
                # 一个关键字都没命中：再看一眼上下黑边，过场即使没读到「F2」也认得出。
                cutscene_bar = self._main_story_cutscene_bars(frame)
            state = classify_main_story_state(
                in_stage, button, victory, reward, all_clear,
                quest_start, difficulty_dialog, fast_explore, cutscene,
                skip_confirm, story,
            )
            if state is MainStoryState.BUSY and cutscene_bar:
                state = MainStoryState.CUTSCENE
        # 标题只作存证，且仅在疑似结算时识别，省掉常态每帧一次 OCR。
        title = (
            self._main_story_ocr(frame, VICTORY_TITLE_ROI, "主线结算标题", threshold)
            if victory
            else ""
        )
        evidence = (
            f"入口={in_stage or '-'};按钮={button or '-'};"
            f"离开={victory or '-'};标题={title or '-'};奖励={reward or '-'};"
            f"完成={all_clear or '-'};追踪={quest_start or '-'};"
            f"难度框={difficulty_dialog or '-'};快速框={fast_explore or '-'};"
            f"过场={cutscene or ('黑边' if cutscene_bar else '-')};"
            f"跳确认={skip_confirm or '-'};字幕={story or '-'}"
        )
        return state, evidence

    def _main_story_title_text(self, frame, threshold: float) -> str:
        return self._main_story_ocr(frame, LIST_TITLE_ROI, "主线关卡标题", threshold)

    def _main_story_click_and_confirm(
        self,
        previous: MainStoryState,
        point: tuple[float, float],
        confirm_seconds: float,
        threshold: float,
        action: str,
    ) -> bool:
        """点击按钮并确认界面确实离开了 previous；输入被吞就重试。

        实机现象：刚关掉奖励弹窗的一两秒内点「战斗」，游戏还在播过场，
        点击直接丢失；旧实现只会干等「战斗等待秒数」分钟级超时后停机。
        这里改成短窗口确认 + 有限次重试，失败也只停一次并存图。
        """

        for attempt in range(1, CLICK_RETRY_ATTEMPTS + 1):
            self._click_reference(*point)
            if self._main_story_wait_change(previous, confirm_seconds, threshold):
                self.info_set("主线点击重试次数", attempt - 1)
                return True
            if attempt < CLICK_RETRY_ATTEMPTS:
                self.info_set("主线点击重试次数", attempt)
                self.log_info(
                    f"主线刷取：「{action}」点击后界面未变化，"
                    f"重试 {attempt}/{CLICK_RETRY_ATTEMPTS - 1}。",
                )
                self.sleep(1.0)
        self.log_info(f"主线刷取：「{action}」连续 {CLICK_RETRY_ATTEMPTS} 次点击均无响应。")
        return False

    def _main_story_verify_title(self, frame, threshold: float) -> bool:
        """只在列表页调用：关卡标题出现在列表页左上角，空白视为不认识。"""

        expected = normalize_ocr_text(self.config.get("主线关卡标题校验", ""))
        if not expected:
            return True
        actual = self._main_story_title_text(frame, threshold)
        # 允许一字之差：「士/土」「极/極」这类形近误识别不该导致整轮中止。
        if keyword_match_count(actual, [expected], fuzzy_ratio=0.8):
            return True
        self.info_set("主线关卡标题", actual or "<未识别>")
        return False

    def _main_story_wait_change(
        self,
        previous: MainStoryState,
        timeout: float,
        threshold: float,
    ) -> bool:
        """等界面离开 previous；超时返回 False。"""

        deadline = monotonic() + max(1.0, timeout)
        while monotonic() < deadline:
            self.sleep(0.5)
            try:
                frame = self.capture_frame()
            except Exception as exc:
                self.info_set("主线截图错误", str(exc))
                continue
            state, evidence = self._main_story_state(frame, threshold)
            if state is not previous:
                self.info_set("主线当前界面", f"{state.value}｜{evidence}")
                return True
        return False

    def _main_story_set_stats(self, battles: int, autos: int, cycles: int) -> None:
        self.info_set("主线战斗次数", battles)
        self.info_set("主线自动进行次数", autos)
        self.info_set("主线完成轮次", cycles)

    def _main_story_abort(self, frame, reason: str) -> bool:
        """安全的主动停止：存图存证，不点击。"""

        self.save_frame("main_story_abort", frame)
        self.info_set("主线停止原因", reason)
        self.log_info(f"主线刷取停止：{reason}")
        return False

    def _main_story_stuck(self, frame, reason: str) -> bool:
        """卡住出口：存图 → 托盘通知 → 置顶弹框，等玩家处理。"""

        path = self.save_frame("main_story_stuck", frame)
        self.info_set("主线停止原因", reason)
        self.log_error(
            f"主线刷取卡住：{reason}；已保存 probe_outputs/main_story_stuck.png",
            notify=True,
        )
        if bool(self.config.get("主线置顶弹框", True)):
            show_topmost_alert(
                f"主线快速探索已停止：{reason}。\n\n"
                "请在游戏里手动看一眼当前界面，处理完后可以重新启动本任务。\n"
                "如果一直找不到快速探索入口：请确认人物在已解锁「进行快速探索」的"
                "关卡里，或者把「主线模式」改成「自动」/「只推主线」改用野外推进。\n"
                f"截图已保存：{path}"
            )
        return False
