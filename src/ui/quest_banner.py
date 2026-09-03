"""Daily-board banner and page status bar for the 日常/周常 tab (mockup V2).

The banner summarizes "今日日常": one ring with done/total over the enabled
一键完成日常 children, the names still missing, the Beijing 04:00 refresh
hint and actions for running all selected children or only today's incomplete
children. Daily commissions count a successful run from the run history.

The status bar is the page footer: executor state, capture method and the next
refresh anchor.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton

from src.tasks.DailyBatchTask import RUN_MODE_ALL, RUN_MODE_INCOMPLETE
from src.tasks.run_history import default_store
from src.ui.quest_theme import MONO_FONT, mix, on_theme_changed, palette

DAILY_BOARD_GROUP = "日常/周常"
BATCH_TASK_NAME = "一键完成日常"


def _set_label_text(label, text: str) -> None:
    """setText with a same-value guard so quiet ticks cost no relayout."""
    if label.text() != text:
        label.setText(text)


class ProgressRing(QWidget):
    """Mockup donut: track ring + accent arc + centered 'done/total'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._done = 0
        self._total = 0
        self.setFixedSize(72, 72)

    def set_progress(self, done: int, total: int) -> None:
        if (done, total) != (self._done, self._total):
            self._done, self._total = done, total
            self.update()

    def paintEvent(self, _event):
        tokens = palette()
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        side = 60
        offset = (self.width() - side) // 2
        rect = (offset, offset, side, side)

        pen = QPen(QColor(tokens["line"]), 6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(*rect, 0, 360 * 16)

        if self._total > 0 and self._done > 0:
            gradient = QConicalGradient(self.rect().center(), 90)
            gradient.setColorAt(0, QColor(tokens["accent_hi"]))
            gradient.setColorAt(1, QColor(tokens["accent_deep"]))
            pen = QPen(QBrush(gradient), 6)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            span = int(-360 * 16 * min(self._done, self._total) / self._total)
            painter.drawArc(*rect, 90 * 16, span)

        painter.setPen(QColor(tokens["accent"]))
        font = painter.font()
        font.setFamilies(["Cascadia Mono", "Cascadia Code", "Consolas"])
        font.setPointSize(14)
        font.setWeight(QFont.Weight.Black)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, f"{self._done}/{self._total}")
        painter.end()


class DailyBoardBanner(QFrame):
    """The '今日日常' banner card on top of the 日常/周常 page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dailyBoardBanner")

        self.ring = ProgressRing(self)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("dailyBoardTitle")
        self.sub_label = QLabel(self)
        self.sub_label.setObjectName("dailyBoardSub")
        self.remaining_button = PushButton(FluentIcon.PLAY, "执行剩余", self)
        self.remaining_button.clicked.connect(self._start_remaining)
        self.start_button = PrimaryPushButton(FluentIcon.PLAY, BATCH_TASK_NAME, self)
        self.start_button.clicked.connect(self._start_all)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(4)
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.sub_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(16)
        layout.addWidget(self.ring, 0, Qt.AlignVCenter)
        layout.addLayout(text_column, 1)
        layout.addWidget(self.remaining_button, 0, Qt.AlignVCenter)
        layout.addWidget(self.start_button, 0, Qt.AlignVCenter)

        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.refresh)

        self._apply_style()
        on_theme_changed(self._apply_style, self)
        self.refresh()

    def _apply_style(self):
        tokens = palette()
        glow = mix(tokens["card"], tokens["accent"], 0.06)
        self.setStyleSheet(
            f"QFrame#dailyBoardBanner {{ background: qradialgradient(spread:pad,"
            f" cx:0, cy:0, radius:1.2, fx:0, fy:0, stop:0 {glow},"
            f" stop:0.55 {tokens['card']}, stop:1 {tokens['card']});"
            f" border: 1px solid {tokens['line']}; border-radius: 14px; }}"
            f" QLabel#dailyBoardTitle {{ color: {tokens['ink']}; font-size: 15px;"
            " font-weight: 900; background: transparent; }"
            f" QLabel#dailyBoardSub {{ color: {tokens['ink_dim']}; font-size: 12px;"
            " background: transparent; }"
        )
        # Ring colors bake into its paintEvent; force a repaint on theme flips.
        self.ring.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _start_all(self):
        self._start_batch(RUN_MODE_ALL)

    def _start_remaining(self):
        self._start_batch(RUN_MODE_INCOMPLETE)

    def _start_batch(self, run_mode: str):
        from ok import og

        task = _find_batch_task()
        if task is not None and not og.executor.current_task:
            task.request_run_mode(run_mode)
            og.app.start_controller.start(task)

    def refresh(self, *_args):
        store = default_store()
        items = commission_items(store)
        done = sum(1 for item in items if item[1])
        total = len(items)
        remaining = [name for name, is_done in items if not is_done]

        self.ring.set_progress(done, total)
        if not items:
            # 与「全部完成」区分开：没有可统计的子任务不代表无事可做的
            # 完成态，给出可操作的指引文案。
            title = "今日日常 · 暂无已启用子任务"
            sub = "在一键完成日常卡片上勾选要执行的子任务 · 服务器 04:00 刷新(北京时间)"
        elif remaining:
            title = f"今日日常 · 还剩 {len(remaining)} 项"
            names = "、".join(remaining[:4])
            if len(remaining) > 4:
                names += f" 等 {len(remaining)} 项"
            sub = f"{names} 未完成 · 服务器 04:00 刷新(北京时间)"
        else:
            title = "今日日常已全部完成"
            sub = "服务器 04:00 刷新(北京时间),跑商库存 08:00 刷新"
        _set_label_text(self.title_label, title)
        _set_label_text(self.sub_label, sub)

        from ok import og

        executor = getattr(og, "executor", None)
        busy = bool(executor is not None and executor.current_task is not None)
        self.start_button.setEnabled(not busy and _find_batch_task() is not None)
        self.remaining_button.setEnabled(
            not busy and bool(remaining) and _find_batch_task() is not None
        )
        start_text = "执行中…" if busy else BATCH_TASK_NAME
        if self.start_button.text() != start_text:
            self.start_button.setText(start_text)


class QuestStatusBar(QWidget):
    """Footer line: executor · capture · next refresh."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("questStatusBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 8, 2, 0)
        layout.setSpacing(18)
        self._labels: list[QLabel] = []
        for _ in range(3):
            label = QLabel(self)
            label.setObjectName("questStatusItem")
            layout.addWidget(label)
            self._labels.append(label)
        layout.insertStretch(2, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)

        self._apply_style()
        on_theme_changed(self._apply_style, self)
        self.refresh()

    def _apply_style(self):
        tokens = palette()
        self.setStyleSheet(
            f"QWidget#questStatusBar {{ border-top: 1px solid {tokens['line']}; }}"
            f" QLabel#questStatusItem {{ color: {tokens['ink_faint']};"
            f" font-family: {MONO_FONT}; font-size: 11px; background: transparent; }}"
        )

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def refresh(self):
        from ok import og

        executor = getattr(og, "executor", None)
        current = getattr(executor, "current_task", None) if executor else None
        if current is not None:
            executor_text = f"执行器 运行中 · {current.name}"
        elif executor is not None and getattr(executor, "paused", False):
            executor_text = "执行器 已暂停"
        else:
            executor_text = "执行器 空闲"

        capture_text = "捕获 未连接"
        device_manager = getattr(og, "device_manager", None)
        capture = getattr(device_manager, "capture_method", None) if device_manager else None
        if capture is not None:
            capture_text = f"捕获 {type(capture).__name__} · 已连接"

        texts = (
            executor_text,
            capture_text,
            f"下个刷新 · {_next_refresh_text()}",
        )
        for label, text in zip(self._labels, texts):
            _set_label_text(label, text)


def _next_refresh_text(now: float | None = None) -> str:
    from datetime import datetime

    from src.tasks.run_history import BEIJING_TZ, day_start_ts

    now = time.time() if now is None else now
    next_day = datetime.fromtimestamp(day_start_ts(now) + 86400, tz=BEIJING_TZ)
    return f"明日 {next_day.hour:02d}:00"


def commission_items(store=None) -> list[tuple[str, bool]]:
    """[(display name, done)] for the daily board: enabled batch children."""
    from ok import og

    store = store or default_store()
    items: list[tuple[str, bool]] = []

    batch = _find_batch_task()
    if batch is not None:
        executor = getattr(og, "executor", None)
        for child in getattr(batch, "child_tasks", ()):
            if not bool(getattr(batch, "config", {}).get(child.config_key, True)):
                continue
            child_task = None
            if executor is not None:
                try:
                    child_task = executor.get_task_by_class(child.task_class)
                except Exception:
                    child_task = None
            name = str(getattr(child_task, "name", None) or child.config_key)
            items.append((name, store.is_completed_today(name)))
    return items


def _find_batch_task():
    from ok import og

    executor = getattr(og, "executor", None)
    for task in getattr(executor, "onetime_tasks", []) or []:
        if str(getattr(task, "name", "")) == BATCH_TASK_NAME:
            return task
    return None


def install_quest_tab_chrome() -> bool:
    """Mount banner + status bar on the 日常/周常 OneTimeTaskTab."""
    from ok.gui.tasks.OneTimeTaskTab import OneTimeTaskTab

    if getattr(OneTimeTaskTab, "_quest_chrome_installed", False):
        return False

    original_init = OneTimeTaskTab.__init__

    def quest_init(self, is_standalone=True, group_name=None):
        original_init(self, is_standalone=is_standalone, group_name=group_name)
        if group_name != DAILY_BOARD_GROUP:
            return
        self.quest_banner = DailyBoardBanner(self.view)
        self.vBoxLayout.insertWidget(1, self.quest_banner)
        self.quest_status_bar = QuestStatusBar(self.view)
        self.vBoxLayout.addWidget(self.quest_status_bar)
        from ok.gui.Communicate import communicate

        # Receiver is the banner (a UI-thread QObject), so the executor-thread
        # signal is delivered queued.
        communicate.task_done.connect(self.quest_banner.refresh)

    OneTimeTaskTab.__init__ = quest_init
    OneTimeTaskTab._quest_chrome_installed = True
    return True
