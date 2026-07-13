from ok import DiagnosisTask
from qfluentwidgets import FluentIcon


class BD2DiagnosisTask(DiagnosisTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "诊断"
        self.description = "性能测试"
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
