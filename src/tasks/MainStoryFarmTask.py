from time import monotonic

from src.icons import TaskIcon as FluentIcon
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.main_story import (
    PREPARE_STATES,
    MainStoryConfigMixin,
    MainStoryFeatureMixin,
    MainStoryState,
)
from src.tasks.main_story_mode import (
    AUTO_MODE,
    FARM_MODE,
    FARM_ONLY_MODE,
    MODE_DETECT_POLL_SECONDS,
    MODE_DETECT_SECONDS,
    MODE_LABELS,
    MODE_OPTIONS,
    WALK_MODE,
    WALK_ONLY_MODE,
    mode_from_evidence,
)
from src.tasks.main_story_walk import (
    MainStoryWalkConfigMixin,
    MainStoryWalkFeatureMixin,
    MainStoryWalkState,
)
from src.tasks.task_vision_mixin import TaskVisionMixin


class MainStoryFarmTask(
    MainStoryFeatureMixin,
    MainStoryWalkFeatureMixin,
    MainStoryConfigMixin,
    MainStoryWalkConfigMixin,
    TaskVisionMixin,
    BaseBD2Task,
):
    """主线任务：优先刷「快速探索」，找不到入口就转为野外自动推进。

    快速探索刷的是主线卡带里已解锁的高难度关卡：进关卡后点「进行快速探索」，
    游戏会自动推进节点、遇战斗自动接手，本任务只负责在正确的时候点
    「自动进行 / 战斗 / 离开」，并在认不出界面时停下来存证。

    推主线刷的是主线剧情本身：点右上角任务追踪条让游戏自动寻路，遇战斗让它
    自己打（回合停住就点 BATTLE），遇剧情点 F2 跳过，画面上出现「F」交互键帽
    就自己点；目标文字与界面状态很久没有变化时，判定需要玩家手动处理
    （解谜/机关/对话选项），存图、发系统通知并弹一个置顶消息框后停机。

    跑哪条由「主线模式」决定：默认「自动」——开跑前看几秒画面，认得出快速探索
    的界面就刷快速探索，否则交给推主线（它自己会判断在不在野外并给出提示）。
    """

    status_keys = [
        "启用",
        "状态",
        "主线当前模式",
        "主线当前界面",
        "主线关卡标题",
        "主线战斗次数",
        "主线自动进行次数",
        "主线完成轮次",
        "主线点击重试次数",
        "主线连续失败次数",
        "主线停止原因",
        "主线快速探索入口 OCR错误",
        "主线列表按钮 OCR错误",
        "主线回合按钮 OCR错误",
        "主线结算离开 OCR错误",
        "主线结算标题 OCR错误",
        "主线关卡标题 OCR错误",
        "主线完成提示 OCR错误",
        "主线奖励标题 OCR错误",
        "主线奖励提示 OCR错误",
        "主线开始难度 OCR错误",
        "主线难度确认 OCR错误",
        "主线快速探索确认 OCR错误",
        "主线跳过剧情 OCR错误",
        "主线跳过对话标题 OCR错误",
        "主线跳过对话正文 OCR错误",
        "主线确认跳过 OCR错误",
        "主线剧情字幕 OCR错误",
        "主线剧情字幕左 OCR错误",
        "主线剧情字幕右 OCR错误",
        "主线截图错误",
        "推主线当前界面",
        "推主线当前目标",
        "推主线战斗次数",
        "推主线推进次数",
        "推主线交互次数",
        "推主线失败次数",
        "推主线静止秒数",
        "推主线画面差值",
        "推主线停止原因",
        "推主线追踪条标题 OCR错误",
        "推主线当前目标 OCR错误",
        "推主线奖励提示 OCR错误",
        "推主线奖励标题 OCR错误",
        "推主线结算离开 OCR错误",
        "推主线战斗按钮 OCR错误",
        "推主线追踪条开始难度 OCR错误",
        "推主线完成提示 OCR错误",
        "推主线结算标题 OCR错误",
        "推主线跳过对话标题 OCR错误",
        "推主线跳过对话正文 OCR错误",
        "推主线跳过剧情 OCR错误",
        "推主线剧情字幕 OCR错误",
        "推主线重试按钮 OCR错误",
        "推主线交互匹配错误",
        "推主线截图错误",
        "推主线存证文件",
        "推主线调试文件",
        "截图文件",
        "鼠标点击",
        "Log",
        "Warning",
        "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_vision_state()
        self._install_main_story_config()
        self._install_main_story_walk_config()
        self._install_main_story_mode_config()
        self.visible = True
        self.group_name = "自动刷级"
        self.group_icon = FluentIcon.CALENDAR
        self.name = "主线快速探索"
        self.description = (
            "主线自动推进：优先刷已通关关卡的「快速探索」；找不到快速探索入口时"
            "自动转为「推主线」——点右上角追踪条让游戏自己寻路，战斗自己打、"
            "剧情自己跳过、「F」交互自己点，很久没有推进就弹置顶框停机。"
            "用法：刷快速探索请先进到右上角有「进行快速探索」的关卡里"
            "（或在大厅并开启「主线自动开始难度」）；推主线请让人物站在主线野外场景里。"
        )
        self.icon = FluentIcon.GAME

        farm_keys = list(MainStoryConfigMixin.MAIN_STORY_CONFIG_KEYS)
        walk_keys = list(MainStoryWalkConfigMixin.MAIN_STORY_WALK_CONFIG_KEYS)
        mode_fields = ["主线模式", *farm_keys, *walk_keys]
        self.config_type["启用"] = {"sub_configs": {True: mode_fields}}
        # 模式只影响「跑哪条路」，两套参数都保留在配置里；切模式时只显示用得到的那部分。
        self.config_type["主线模式"] = {
            "type": "drop_down",
            "options": list(MODE_OPTIONS),
            "sub_configs": {
                AUTO_MODE: [*farm_keys, *walk_keys],
                FARM_ONLY_MODE: farm_keys,
                WALK_ONLY_MODE: walk_keys,
            },
        }
        ordered = {
            key: self.default_config[key]
            for key in ("启用", *mode_fields)
            if key in self.default_config
        }
        ordered.update(
            {
                key: value
                for key, value in self.default_config.items()
                if key not in ordered
            }
        )
        self.default_config = ordered

    def _install_main_story_mode_config(self) -> None:
        """「主线模式」以及合并后重写的「启用」说明。"""

        self.default_config["主线模式"] = AUTO_MODE
        self.config_description["启用"] = (
            "是否执行主线自动任务。开跑前请把人物放到对应位置，两种起点：\n"
            "① 快速探索：站在已解锁「进行快速探索」的主线高难关卡里；"
            "或站在大厅并开启「主线自动开始难度」，任务会自己进关卡。\n"
            "② 推主线：站在主线野外场景里（右上角追踪条写着「主线」和目标）。"
            "任务会自己寻路、打战斗、跳过剧情、点「F」交互。\n"
            "选「自动」时不用自己判断：任务会先找快速探索入口，找不到就走推主线。"
        )
        self.config_description["主线模式"] = (
            "怎么跑这一轮：\n"
            "「自动（优先快速探索）」——开跑前看几秒画面，认出「进行快速探索」"
            "或大厅追踪条就刷快速探索；认出主线野外追踪条就推主线；都认不出时"
            "按推主线处理（它会提示你该站在哪儿）。\n"
            "「只刷快速探索」——只跑已通关关卡的循环刷取，认不出界面就停机。\n"
            "「只推主线（野外）」——不做快速探索检查，直接点追踪条推进剧情；"
            "人物必须已经在主线野外场景里。"
        )

    def _main_story_pick_mode(self) -> tuple[str, str]:
        """开跑前看几秒画面，决定这轮跑快速探索还是推主线。

        返回 (模式, 依据)。每帧先认快速探索的界面，再认野外的追踪条与「F」键帽；
        都没有就继续看下一帧（加载、过场切换都靠这个窗口等过去）。窗口内一直
        认不出来就交给推主线——它自己会区分「在野外 / 在大厅 / 认不出」并给出
        带操作提示的错误，比在这里猜更稳。
        """

        farm_threshold = float(self.config.get("主线 OCR 阈值", 0.2))
        walk_threshold = float(self.config.get("推主线 OCR 阈值", 0.2))
        interact = bool(self.config.get("推主线自动交互", True))
        auto_start = bool(self.config.get("主线自动开始难度", True))
        deadline = monotonic() + MODE_DETECT_SECONDS

        while True:
            farm_state = None
            walk_state = None
            try:
                frame = self.capture_frame()
            except Exception as exc:
                self.info_set("主线截图错误", str(exc))
                frame = None
            if frame is not None:
                farm_state, farm_evidence = self._main_story_state(frame, farm_threshold)
                if not auto_start and farm_state in PREPARE_STATES:
                    # 关掉自动开始难度时，大厅那几步不再算「能刷快速探索」。
                    farm_state = MainStoryState.BUSY
                walk_state, walk_evidence, *_rest = self._walk_state(
                    frame, walk_threshold,
                )
                interact_visible = (
                    interact and self._walk_interact_target(frame) is not None
                )
                mode = mode_from_evidence(farm_state, walk_state, interact_visible)
                if mode is not None:
                    if mode == FARM_MODE:
                        return mode, f"{farm_state.value}｜{farm_evidence}"
                    # 认出的可能是追踪条，也可能是没有追踪条时画面上的「F」键帽。
                    label = (
                        walk_state.value
                        if walk_state is MainStoryWalkState.FIELD
                        else "野外F键帽"
                    )
                    return mode, f"{label}｜{walk_evidence}"
            if monotonic() >= deadline:
                return WALK_MODE, "未认出快速探索入口，按推主线处理"
            self.sleep(MODE_DETECT_POLL_SECONDS)

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "主线任务已禁用。")
            self.log_info("主线任务已禁用。")
            return True

        setting = str(self.config.get("主线模式", AUTO_MODE) or AUTO_MODE)
        if setting == FARM_ONLY_MODE:
            mode, reason = FARM_MODE, "配置指定只刷快速探索"
        elif setting == WALK_ONLY_MODE:
            mode, reason = WALK_MODE, "配置指定只推主线"
        else:
            self.info_set("状态", "主线任务启动，正在判断从哪儿开始。")
            mode, reason = self._main_story_pick_mode()

        label = MODE_LABELS.get(mode, mode)
        self.info_set("主线当前模式", f"{label}｜{reason}")
        self.log_info(f"主线任务：本轮跑「{label}」（{reason}）。")
        self.info_set("状态", f"主线任务运行中（{label}）。")

        if mode == FARM_MODE:
            success = self.run_main_story_farm()
        else:
            success = self.run_main_story_walk()

        self.info_set("状态", f"主线任务{'完成' if success else '停止'}（{label}）。")
        if success:
            self.log_completion(f"主线任务：已到达停止条件（{label}）。")
        return success
