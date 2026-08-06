from __future__ import annotations

from qfluentwidgets import FluentIcon

from src.tasks.map_trade.collector import Collector
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
            "按周进度跑剧情卡带；每天最多7张，每张安全区吸收1次，"
            "战斗区域1、2各执行吸收、召集、压制。第14章暂时跳过。"
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
            }
        )
        self.config_description.update(
            {
                "执行地图采集": (
                    "按周进度处理剧情卡带；每日吸收上限21次，因此最多完成7张。"
                    "第14章在专用流程完成前安全跳过。"
                ),
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
                            MAP_VISION_THRESHOLD_KEY,
                            MAP_OCR_THRESHOLD_KEY,
                            "加载页面等待秒数",
                            "卡带单步重试次数",
                        ]
                    }
                },
                MAP_VISION_THRESHOLD_KEY: {"min": 0.50, "max": 0.95, "step": 0.01},
                MAP_OCR_THRESHOLD_KEY: {"min": 0.05, "max": 0.95, "step": 0.01},
                "加载页面等待秒数": {"min": 10.0, "max": 120.0, "step": 1.0},
                "卡带单步重试次数": {"min": 1, "max": 5},
            }
        )

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "跑图已禁用。")
            return True

        vision = Vision(self)
        navigator = Navigator(self, vision)
        progress = ProgressStore()
        progress.load()
        collector = Collector(self, vision, navigator, progress)
        return self._run_phases(
            navigator,
            (("地图采集", "执行地图采集", collector.run),),
        )
