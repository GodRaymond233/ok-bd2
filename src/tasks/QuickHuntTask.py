from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.quick_hunt import (
    QUICK_HUNT_CHILD_CONFIG_KEYS,
    QuickHuntConfigMixin,
    QuickHuntFeatureMixin,
)
from src.tasks.task_vision_mixin import TaskVisionMixin

DAILY_ONLY_CONFIG_KEYS = (
    "执行公会签到",
    "执行小屋签到",
    "执行一键收菜",
    "公会入口阈值",
    "公会签到成功阈值",
    "小屋页面阈值",
    "加载页面阈值",
    "loading 出现等待秒数",
    "loading 消失等待秒数",
    "公会签到成功等待秒数",
    "小屋页面等待秒数",
    "一键收菜菜单等待秒数",
)


class QuickHuntTask(
    QuickHuntFeatureMixin,
    QuickHuntConfigMixin,
    TaskVisionMixin,
    BaseBD2Task,
):
    """Standalone quick-hunt task backed by the migrated  flow."""

    include_quick_hunt_config = True
    ocr_threshold_key = "快速狩猎 OCR 阈值"
    status_keys = [
        "启用",
        "状态",
        "当前任务",
        "快速狩猎入口",
        "快速狩猎菜单",
        "快速狩猎米饭",
        "快速狩猎火把",
        "快速狩猎当前阶段",
        "快速狩猎结果",
        "快速狩猎测试状态",
        "快速狩猎首页按钮",
        "快速狩猎红点识别",
        "快速狩猎主页亮度",
        "快速狩猎主页抽抽乐 OCR",
        "快速狩猎返回位置 OCR",
        "快速狩猎菜单 OCR",
        "快速狩猎资源 OCR",
        "快速狩猎按钮 OCR",
        "快速狩猎次数 OCR",
        "快速狩猎开始 OCR",
        "快速狩猎奖励 OCR",
        "快速狩猎异常 OCR",
        "快速狩猎地图 OCR",
        "快速狩猎圣石 OCR",
        "快速狩猎圣石数量",
        "快速狩猎收起模板",
        "快速狩猎双倍识别",
        "快速狩猎模板阈值",
        "快速狩猎像素相似度阈值",
        "快速狩猎 OCR 阈值",
        "主页亮度比例阈值",
        "主页确认等待秒数",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_vision_state()
        self._install_quick_hunt_config()
        self.visible = True
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.name = "快速狩猎"
        self.description = "从首页进入快速狩猎，调度米饭并补充数量最少的属性圣石。"
        self.icon = FluentIcon.GAME

        for key in DAILY_ONLY_CONFIG_KEYS:
            self.default_config.pop(key, None)
            self.config_description.pop(key, None)
            self.config_type.pop(key, None)

        self.default_config.pop("执行快速狩猎", None)
        self.config_description.pop("执行快速狩猎", None)
        self.config_type.pop("执行快速狩猎", None)

        ocr_threshold = self.default_config.pop("日常 OCR 阈值", 0.2)
        self.config_description.pop("日常 OCR 阈值", None)
        self.config_type.pop("日常 OCR 阈值", None)
        self.default_config["快速狩猎 OCR 阈值"] = ocr_threshold
        self.config_description["快速狩猎 OCR 阈值"] = (
            "快速狩猎界面文字识别的最低置信度。"
        )
        self.config_type["快速狩猎 OCR 阈值"] = {
            "min": 0.05,
            "max": 0.95,
            "step": 0.01,
        }
        self.default_config.update(
            {
                "主页亮度比例阈值": 0.75,
                "主页确认等待秒数": 10.0,
            }
        )
        self.config_description.update(
            {
                "主页亮度比例阈值": "确认已返回主页所需的最低亮色像素比例。",
                "主页确认等待秒数": "点击主页按钮后确认已返回主页的最长等待时间。",
            }
        )
        self.config_type.update(
            {
                "主页亮度比例阈值": {"min": 0.5, "max": 0.95, "step": 0.01},
                "主页确认等待秒数": {"min": 2.0, "max": 30.0, "step": 1.0},
            }
        )

        visible_keys = ["识别成功后等待秒数", *QUICK_HUNT_CHILD_CONFIG_KEYS]
        ocr_index = visible_keys.index("快速狩猎像素相似度阈值") + 1
        visible_keys.insert(ocr_index, "快速狩猎 OCR 阈值")
        visible_keys.extend(("主页亮度比例阈值", "主页确认等待秒数"))
        self.default_config["启用"] = True
        self.config_description["启用"] = "是否执行独立的快速狩猎任务。"
        self.config_type["启用"] = {"sub_configs": {True: visible_keys}}
        ordered_keys = ("启用", *visible_keys)
        self.default_config = {
            **{
                key: self.default_config[key]
                for key in ordered_keys
                if key in self.default_config
            },
            **{
                key: value
                for key, value in self.default_config.items()
                if key not in ordered_keys
            },
        }

    def run(self):
        test_action = getattr(self, "_quick_hunt_test_action", None)
        if test_action:
            return self._run_quick_hunt_test(test_action)

        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "快速狩猎已禁用。")
            self.log_info("快速狩猎已禁用。")
            return True

        self.info_set("状态", "快速狩猎启动。")
        success = self.run_quick_hunt()
        self.info_set("状态", "快速狩猎完成。" if success else "快速狩猎失败。")
        if success:
            self.log_completion("快速狩猎：流程完成并返回主页。")
        return success
