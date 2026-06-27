from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QGridLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FluentIcon, SubtitleLabel

from src.tasks.trigger.AutoLoginTask import AutoLoginTask

REFRESH_INTERVAL_MS = 500


class AutoLoginStatusTab(CustomTab):
    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.ROBOT
        self.title_label = SubtitleLabel("自动登录状态")
        self.add_widget(self.title_label)

        self.status_container = QWidget(self.view)
        self.status_layout = QGridLayout(self.status_container)
        self.status_layout.setContentsMargins(0, 8, 0, 0)
        self.status_layout.setHorizontalSpacing(16)
        self.status_layout.setVerticalSpacing(10)
        self.add_widget(self.status_container)

        self.value_labels = {}
        for row, key in enumerate(
            [
                "阶段",
                "内部状态",
                "最后动作",
                "BrownDustX",
                "BrownDustX 像素",
                "BrownDustX OCR",
                "BrownDustX Confirm",
                "BrownDustX Confirm 像素",
                "BrownDustX Confirm OCR",
                "TOUCH TO START",
                "加载页面",
                "小屋按钮",
                "小屋亮度比例",
                "Log",
                "Warning",
            ]
        ):
            key_label = CaptionLabel(key)
            value_label = BodyLabel("等待数据")
            value_label.setWordWrap(True)
            self.status_layout.addWidget(key_label, row, 0)
            self.status_layout.addWidget(value_label, row, 1)
            self.value_labels[key] = value_label

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh_status)

    @property
    def name(self):
        return "自动登录状态"

    def showEvent(self, event):
        super().showEvent(event)
        if event.type() == QEvent.Show:
            self.refresh_status()
            if not self.timer.isActive():
                self.timer.start()

    def hideEvent(self, event):
        if self.timer.isActive():
            self.timer.stop()
        super().hideEvent(event)

    def refresh_status(self):
        task = self.get_task(AutoLoginTask) if self.executor is not None else None
        info = getattr(task, "info", {}) if task is not None else {}

        if task is None:
            self.value_labels["阶段"].setText("自动登录任务未加载")
            for key, label in self.value_labels.items():
                if key != "阶段":
                    label.setText("-")
            return

        for key, label in self.value_labels.items():
            value = info.get(key, "")
            if value == "":
                value = "-"
            label.setText(str(value))
