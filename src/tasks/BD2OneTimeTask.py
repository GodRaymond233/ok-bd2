from qfluentwidgets import FluentIcon

from src.Labels import Labels
from src.tasks.BaseBD2Task import HOTKEY_CONFIG_NAME, BaseBD2Task


class BD2OneTimeTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 基础检查"
        self.description = "检查 BD2 项目基础结构和共享配置。"
        self.icon = FluentIcon.SYNC
        self.visible = True
        self.default_config.update(
            {
                "启用": True,
                HOTKEY_CONFIG_NAME: {},
            }
        )
        self.config_description.update(
            {
                HOTKEY_CONFIG_NAME: "打开共享的游戏按键设置。",
            }
        )
        self.config_type.update(
            {
                HOTKEY_CONFIG_NAME: {"type": "global"},
            }
        )

    def run(self):
        self.info_set("状态", "BD2 基础检查任务已就绪。")
        self.log_info("BD2 基础检查已执行。", notify=True)
        return True

    def find_confirm_button(self):
        return self.find_one(Labels.confirm_button)
