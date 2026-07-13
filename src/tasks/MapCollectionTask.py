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
        "目标卡带",
        "采集进度",
        "探查次数",
        "吸取次数",
        "召集次数",
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
        self.description = "按周进度采集 18 张剧情卡带；通常在周初几天按每日额度完成。"
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
                "执行地图采集": "按周进度遍历 18 张可采集剧情卡带的三个小图。",
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
