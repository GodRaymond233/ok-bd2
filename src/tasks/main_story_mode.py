"""主线任务的运行模式选择：优先「快速探索」，找不到入口就转为野外自动推进。

原来「主线快速探索」和「自动推主线」是两个任务，起任务前要自己想清楚站在哪。
现在合成一个：开跑前先看几秒画面，能认出快速探索的界面就走快速探索；
认不出、但认得到主线野外追踪条（或野外「F」交互键帽）就走自动推主线；
两者都认不出时也交给自动推主线——它自己会区分「在野外 / 在大厅 / 界面认不出」
并给出对应提示，比在这里瞎猜更稳。

这条规则只依赖两套识别结果，不碰业务状态，所以可以单独测。
"""

from __future__ import annotations

from src.tasks.main_story import MainStoryState
from src.tasks.main_story_walk import MainStoryWalkState

# 开跑前最多看这么久；等加载画面、过场切换都够用。
MODE_DETECT_SECONDS = 10.0
MODE_DETECT_POLL_SECONDS = 0.5

AUTO_MODE = "自动（优先快速探索）"
FARM_ONLY_MODE = "只刷快速探索"
WALK_ONLY_MODE = "只推主线（野外）"
MODE_OPTIONS = (AUTO_MODE, FARM_ONLY_MODE, WALK_ONLY_MODE)

FARM_MODE = "farm"
WALK_MODE = "walk"

MODE_LABELS = {
    FARM_MODE: "主线快速探索",
    WALK_MODE: "自动推主线",
}

# 只有这些界面能证明「现在就在快速探索的流程里」。
# 战斗、结算、奖励弹窗、「按钮未知」都不算：野外推主线时同样会出现这些画面，
# 拿它们当证据会把野外误判成刷取。
# 「开始XX难度」是大厅追踪条，正是快速探索的正常起点（自动开始难度）。
FARM_EVIDENCE_STATES = frozenset({
    MainStoryState.IN_STAGE,
    MainStoryState.LIST_AUTO,
    MainStoryState.LIST_BATTLE,
    MainStoryState.DIFFICULTY_DIALOG,
    MainStoryState.FAST_EXPLORE_DIALOG,
    MainStoryState.QUEST_START,
    MainStoryState.ALL_CLEAR,
})


def mode_from_evidence(
    farm_state: MainStoryState | None,
    walk_state: MainStoryWalkState | None,
    interact_visible: bool = False,
) -> str | None:
    """一帧证据能定下模式就返回 FARM_MODE / WALK_MODE，定不下来返回 None。

    快速探索排前面：关卡内右上角同时可能有任务追踪条，但「进行快速探索」
    这几个字只在快速探索的关卡里出现，是更硬的证据。
    """

    if farm_state in FARM_EVIDENCE_STATES:
        return FARM_MODE
    if walk_state is MainStoryWalkState.FIELD:
        return WALK_MODE
    if interact_visible:
        return WALK_MODE
    return None
