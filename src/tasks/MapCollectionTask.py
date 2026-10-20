from __future__ import annotations

from qfluentwidgets import FluentIcon

from src.tasks.map_trade.collection_selection import (
    COLLECTION_CARD_NAMES,
    resolve_collection_cards,
)
from src.tasks.map_trade.collector import Collector
from src.tasks.map_trade.collector_constants import BATTLE_ACTIONS, SUPPRESS_ACTION
from src.tasks.map_trade.navigator import Navigator
from src.tasks.map_trade.progress import ProgressStore
from src.tasks.map_trade.vision import Vision
from src.tasks.MapTradeTask import (
    MAP_OCR_THRESHOLD_KEY,
    MAP_VISION_THRESHOLD_KEY,
    MapAutomationTaskBase,
)


class MapCollectionTask(MapAutomationTaskBase):
    """Weekly map collection card with its own UI and configuration."""

    vision_threshold_key = MAP_VISION_THRESHOLD_KEY
    ocr_threshold_key = MAP_OCR_THRESHOLD_KEY
    task_log_name = "跑图"
    diagnostic_prefix = "map_collection"
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "导航状态",
        "主页小屋按钮",
        "主页亮度",
        "主页抽抽乐 OCR",
        "目标卡带",
        "剧情角标",
        "卡带滚轮",
        "卡带吸取状态",
        "卡带压制状态",
        "卡带完成度",
        "箱庭确认信号",
        "箱庭技能组状态",
        "箱庭技能组切换",
        "箱庭进一步确认",
        "箱庭复合确认",
        "箱庭稳定确认",
        "箱庭交互按钮",
        "箱庭地图传送阵候选",
        "箱庭地图传送阵模板",
        "传送阵地图传送阵候选",
        "传送阵地图传送阵点击中心",
        "传送阵地图返回按钮",
        "采集进度",
        "采集未完成卡带",
        "未找到的活动卡带",
        "区域地图",
        "探查倒计时",
        "探查图标",
        "吸收图标",
        "召集图标",
        "压制图标",
        "吸取次数",
        "召集次数",
        "压制次数",
        "吸收状态",
        "召集状态",
        "压制状态",
        "每日技能进度",
        "跑图完成范围",
        "跑图周期",
        "完成",
        "失败",
        "跳过",
        "Log",
        "Warning",
        "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "每周跑图"
        self.description = (
            "按周进度依次采集剧情、角色与活动卡带；剧情第6章不参与采集，"
            "可自选卡带、屏蔽卡带或只完成一张。"
            "资源周一08:00、技能次数每日08:00刷新（北京时间）。"
            "按各卡带路线执行吸收、召集、压制，剩余次数以游戏内识别结果为准。"
        )
        self.icon = FluentIcon.GLOBE
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True

        self.default_config.update(
            {
                "启用": True,
                "执行地图采集": True,
                MAP_VISION_THRESHOLD_KEY: 0.72,
                MAP_OCR_THRESHOLD_KEY: 0.20,
                "加载页面等待秒数": 45.0,
                "卡带单步重试次数": 2,
                "自定义采集关卡": False,
                "采集关卡": list(COLLECTION_CARD_NAMES),
                "单章模式": False,
                "采集卡带屏蔽": "",
                "启用压制技能": True,
            }
        )
        self.config_description.update(
            {
                "执行地图采集": (
                    "按周进度依次处理剧情、角色与活动卡带；剧情第6章不参与采集。"
                    "活动游戏卡页找不到的活动卡带视为未持有，跳过且不影响本周完成。"
                    "采集技能的剩余次数以游戏内识别结果为准。"
                ),
                "自定义采集关卡": (
                    "开启后只采集下方勾选的卡带；关闭时采集全部剧情、角色与活动卡带。"
                ),
                "采集关卡": "可勾选一张或多张卡带，仍遵守卡带屏蔽、本周进度和技能额度。",
                "单章模式": "本次运行新完成一张卡带后即停止。",
                "采集卡带屏蔽": (
                    "逗号分隔的卡带名或“类别_编号”短前缀，"
                    "如“Story_01_血骑士, Story_3, Character_2, Event_4”；被屏蔽的卡带不采集。"
                ),
                "启用压制技能": "关闭后只执行吸收与召集；已完成的部分动作可在后续运行继续核验。",
                MAP_VISION_THRESHOLD_KEY: "卡带、导航与采集技能模板的最低匹配可信度。",
                MAP_OCR_THRESHOLD_KEY: "技能次数和按钮识别的最低可信度。",
                "加载页面等待秒数": "进入卡带或传送后等待加载完成的最长秒数。",
                "卡带单步重试次数": "单张卡带进入或单步操作失败时的尝试次数。",
            }
        )
        self.config_type.update(
            {
                "执行地图采集": {
                    "sub_configs": {
                        True: [
                            "自定义采集关卡",
                            "单章模式",
                            "采集卡带屏蔽",
                            "启用压制技能",
                            MAP_VISION_THRESHOLD_KEY,
                            MAP_OCR_THRESHOLD_KEY,
                            "加载页面等待秒数",
                            "卡带单步重试次数",
                        ]
                    }
                },
                "自定义采集关卡": {"sub_configs": {True: ["采集关卡"]}},
                "采集关卡": {"type": "multi_selection", "options": list(COLLECTION_CARD_NAMES)},
                MAP_VISION_THRESHOLD_KEY: {"min": 0.50, "max": 0.95, "step": 0.01},
                MAP_OCR_THRESHOLD_KEY: {"min": 0.05, "max": 0.95, "step": 0.01},
                "加载页面等待秒数": {"min": 10.0, "max": 120.0, "step": 1.0},
                "卡带单步重试次数": {"min": 1, "max": 5},
            }
        )

    def run(self):
        self.info_set("跑图完成范围", "-")
        self.info_set("跑图周期", "")
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "跑图已禁用。")
            return True
        cards = ()
        if bool(self.config.get("执行地图采集", True)):
            cards, error = resolve_collection_cards(self.config)
            if error:
                self.info_set("状态", f"跑图未执行：{error}。")
                self.log_warning(f"跑图：{error}。")
                return False
        battle_actions = BATTLE_ACTIONS
        if not bool(self.config.get("启用压制技能", True)):
            battle_actions = tuple(
                action for action in BATTLE_ACTIONS if action is not SUPPRESS_ACTION
            )
        only_once = bool(self.config.get("单章模式", False))

        vision = Vision(self)
        navigator = Navigator(self, vision)
        with ProgressStore() as progress:
            progress.load()
            collector = Collector(self, vision, navigator, progress)

            def collect():
                result = collector.run(
                    cards, battle_actions=battle_actions, only_once=only_once,
                )
                if result.success:
                    period = (
                        "本周" if collector.scope_complete()
                        else "今日" if result.depleted else "-"
                    )
                    self.info_set("跑图完成范围", period)
                    self.info_set(
                        "跑图周期", progress.state.weekly_key if period == "本周"
                        else progress.state.daily_key,
                    )
                return result

            success = self._run_phases(
                navigator,
                (("地图采集", "执行地图采集", collect),),
            )
            if success:
                period = self.info.get("跑图完成范围")
                if period == "本周":
                    self.info_set("状态", "本周跑图已全部完成。")
                elif period == "今日":
                    self.info_set("状态", "今日跑图额度已用尽，等待北京时间08:00刷新后继续。")
            return success
