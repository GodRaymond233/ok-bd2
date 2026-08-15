from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, PushButton

from src.ui.home_dashboard import HomeDashboard


@dataclass(frozen=True)
class _PreviewChild:
    config_key: str


class _PreviewTask:
    def __init__(self, name: str, description: str, *, batch: bool = False):
        self.name = name
        self.description = description
        self.group_name = "日常/周常"
        self.visible = True
        self.enabled = False
        self.paused = False
        self.running = False
        self.info = {}
        self.child_tasks = (
            tuple(
                _PreviewChild(key)
                for key in (
                    "公会、小屋、酒馆",
                    "快速狩猎",
                    "免费抽抽乐",
                    "广场女神像",
                    "自动PVP",
                    "跑商",
                )
            )
            if batch
            else ()
        )
        self.config = {child.config_key: True for child in self.child_tasks}


class _PreviewCard:
    def __init__(self, task, controller):
        self.task = task
        self.controller = controller
        self.expanded = False

    def start_clicked(self):
        self.controller.start(self.task)

    def setExpand(self, expanded):
        self.expanded = expanded


class _PreviewController:
    def __init__(self, executor):
        self.executor = executor

    def start(self, task):
        if self.executor.current_task is task and self.executor.paused:
            self.executor.paused = False
            task.paused = False
            task.running = True
            return
        if self.executor.current_task is not None:
            self.executor.current_task.enabled = False
            self.executor.current_task.running = False
        self.executor.current_task = task
        self.executor.paused = False
        task.enabled = True
        task.running = True
        task.info = {"状态": "原型模拟：任务已交给现有执行器"}


class _PreviewWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ok-bd2 · 首页重设计原型（不会执行任务）")

        tasks = [
            _PreviewTask(
                "一键完成日常",
                "按既有顺序执行已开启的日常项目。",
                batch=True,
            ),
            _PreviewTask("公会、小屋、酒馆", "完成签到、小屋确认与酒馆收菜。"),
            _PreviewTask("快速狩猎", "调度米饭并补充数量最少的属性圣石。"),
            _PreviewTask("白嫖抽抽乐", "领取服装和装备的所有免费次数。"),
            _PreviewTask("广场女神像", "进入生活广场并完成女神像流程。"),
            _PreviewTask("镜中之战", "进入战斗玩法卡带并完成自动 PVP。"),
            _PreviewTask("每日跑商", "按配置执行料理、购买与出售。"),
        ]
        self.executor = SimpleNamespace(onetime_tasks=tasks, paused=True, current_task=None)
        controller = _PreviewController(self.executor)
        cards = [_PreviewCard(task, controller) for task in tasks]
        task_tab = SimpleNamespace(card_widgets=cards, ensureWidgetVisible=lambda _card: None)

        report_button = PushButton("生成问题报告")
        debug_widget = CaptionLabel("设备与调试工具（原型中不启动）")
        self.start_tab = SimpleNamespace(
            feedback_report_button=report_button,
            debug_widget=debug_widget,
            ensureWidgetVisible=lambda _widget: None,
        )
        self.grouped_task_tabs = [task_tab]
        self.onetime_tab = None
        self.imported_tabs = {}
        self.notification_tab = object()
        self.setting_tab = object()
        self.notification_manager = SimpleNamespace(
            system_enabled=True,
            external_provider_enabled=False,
        )
        self.app = SimpleNamespace(start_controller=controller)
        self.last_switched_tab = None

        dashboard = HomeDashboard(self)
        preview_note = CaptionLabel(
            "独立预览：按钮只切换本地模拟状态，不连接游戏、不执行任何任务逻辑。"
        )
        preview_note.setWordWrap(True)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 24)
        content_layout.setSpacing(8)
        content_layout.addWidget(dashboard)
        content_layout.addWidget(preview_note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)
        self.resize(1180, 820)

    def switchTo(self, tab):
        self.last_switched_tab = tab


def _save_screenshot(window, screenshot_path: Path, keep_open: bool) -> None:
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(screenshot_path)):
        raise RuntimeError(f"无法保存截图：{screenshot_path}")
    print(screenshot_path.resolve())
    if not keep_open:
        QApplication.instance().quit()


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 ok-bd2 首页局部界面原型。")
    parser.add_argument("--screenshot", type=Path, help="窗口显示后保存 PNG。")
    parser.add_argument("--keep-open", action="store_true", help="保存截图后继续显示窗口。")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    window = _PreviewWindow()
    window.show()
    if args.screenshot:
        QTimer.singleShot(
            700,
            lambda: _save_screenshot(window, args.screenshot, args.keep_open),
        )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
