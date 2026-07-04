from ok import Config
from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import QEvent
from qfluentwidgets import BodyLabel, FluentIcon, PrimaryPushButton, PushButton

from src.tasks.BD2OneTimeTask import BD2OneTimeTask
from src.ui.log_folder import open_log_folder


class BD2StatusTab(CustomTab):
    def __init__(self):
        super().__init__()
        self.config = Config(
            self.__class__.__name__,
            {
                "最近操作": "",
            },
        )
        self.icon = FluentIcon.FLAG
        self.label = BodyLabel("BD2 状态")
        self.add_widget(self.label)

        self.button = PrimaryPushButton("运行基础检查")
        self.button.clicked.connect(self.button_clicked)
        self.add_widget(self.button)

        self.open_logs_button = PushButton("打开日志文件夹")
        self.open_logs_button.setIcon(FluentIcon.FOLDER)
        self.open_logs_button.clicked.connect(self.open_logs_clicked)
        self.add_widget(self.open_logs_button)

    @property
    def name(self):
        return "BD2"

    def button_clicked(self):
        self.config["最近操作"] = "运行基础检查"
        self.get_task(BD2OneTimeTask).run()

    def open_logs_clicked(self):
        try:
            folder = open_log_folder()
        except RuntimeError as exc:
            self.config["最近操作"] = f"打开日志失败：{exc}"
            self.logger.error(f"打开日志文件夹失败：{exc}")
            return

        self.config["最近操作"] = f"已打开日志文件夹：{folder}"

    def showEvent(self, event):
        super().showEvent(event)
        if event.type() == QEvent.Show:
            self.logger.info(f"{self.__class__.__name__} shown")
