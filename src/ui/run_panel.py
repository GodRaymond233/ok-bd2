"""Run summary panel replacing the raw 信息/值 table (mockup V2, window 2).

One panel per state: a status pill (运行中/已暂停/已完成/已中止), task name and
elapsed time, contextual ops (暂停/停止 while running, 查看日志/关闭 when done),
and — for the batch task — a segmented progress bar plus a per-child status
grid.  Everything below the header is derived from ``task.info``, so any task
that fills the standard keys (状态/当前子任务/完成/失败/跳过/Log) renders
correctly without task-specific UI code.

``render`` runs on the tab's 1s timer and the done panel stays visible, so
every section is dirty-checked: unchanged content never re-creates widgets,
re-applies stylesheets or re-enters layout — the per-second tick collapses to
a few string comparisons plus the one elapsed-time label that actually ticks.
"""

from __future__ import annotations

import time

from ok import Logger
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, PushButton, ToolButton

from src.tasks.BaseBD2Task import task_info_snapshot
from src.tasks.run_history import contains_joined_name
from src.ui.quest_theme import MONO_FONT, mix, on_theme_changed, palette, rgba

logger = Logger.get_logger(__name__)

_BATCH_DONE_KEY = "完成"
_BATCH_FAIL_KEY = "失败"
_BATCH_SKIP_KEY = "跳过"
_CURRENT_KEY = "当前子任务"
_STATUS_KEY = "状态"
_STANDARD_KEYS = {
    _STATUS_KEY,
    _CURRENT_KEY,
    _BATCH_DONE_KEY,
    _BATCH_FAIL_KEY,
    _BATCH_SKIP_KEY,
    "Log",
    "Warning",
    "Error",
}


def _elapsed_text(task, now: float | None = None) -> str:
    started = getattr(task, "start_time", 0) or 0
    if not started:
        return ""
    seconds = max(0, int((time.time() if now is None else now) - started))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    duration = f"{hours}h {minutes}m {secs}s" if hours else f"{minutes}m {secs:02d}s"
    return f"已运行 {duration}" if getattr(task, "enabled", False) else f"用时 {duration}"


def run_state(task) -> str:
    """One of run / pause / done / abort / fail for the status pill."""
    info = task_info_snapshot(task)
    if getattr(task, "enabled", False):
        return "pause" if getattr(task, "paused", False) else "run"
    status = str(info.get(_STATUS_KEY, ""))
    if "中止" in status:
        return "abort"
    if info.get("Error"):
        return "fail"
    return "done"


_PILL_TEXT = {
    "run": "运行中",
    "pause": "已暂停",
    "done": "已完成",
    "abort": "已中止",
    "fail": "已中止",
}


class SegmentedBar(QWidget):
    """Mockup segbar: colored segments sized by child-task counts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[tuple[int, str] | tuple[int, str, str]] = []
        self.setFixedHeight(8)

    def set_segments(self, segments: list[tuple[int, str] | tuple[int, str, str]]) -> None:
        if segments != self._segments:
            self._segments = list(segments)
            self.update()

    def paintEvent(self, _event):
        total = sum(segment[0] for segment in self._segments)
        if total <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        x = 0.0
        gap = 2
        width = self.width()
        for index, segment in enumerate(self._segments):
            count, color = segment[0], segment[1]
            gradient_to = segment[2] if len(segment) > 2 else None
            if count <= 0:
                continue
            seg_width = max(2.0, width * count / total - (gap if index else 0))
            painter.setPen(Qt.NoPen)
            if gradient_to is not None:
                gradient = QLinearGradient(x, 0, x + seg_width, 0)
                gradient.setColorAt(0, QColor(color))
                gradient.setColorAt(1, QColor(gradient_to))
                painter.setBrush(QBrush(gradient))
            else:
                painter.setBrush(QColor(color))
            painter.drawRoundedRect(int(x), 0, int(seg_width), self.height(), 4, 4)
            x += seg_width + gap
        painter.end()


class LinkLabel(QLabel):
    """A plain-text label that acts as a link (mockup's 日志 → line)."""

    def __init__(self, text, callback, parent=None):
        super().__init__(text, parent)
        self._callback = callback
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and callable(self._callback):
            self._callback()
        super().mouseReleaseEvent(event)


class RunPanel(QFrame):
    """The full run-summary card shown instead of the info table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("runPanel")
        self._task = None
        self.on_close = None
        self._grid_signature = None
        self._rows_signature = None
        self._pill_kind = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # ---- header: pill + name + time + ops ----
        header = QHBoxLayout()
        header.setSpacing(10)
        self.pill = QLabel(self)
        self.pill.setObjectName("runPanelPill")
        self.name_label = QLabel(self)
        self.name_label.setObjectName("runPanelName")
        self.time_label = QLabel(self)
        self.time_label.setObjectName("runPanelTime")
        header.addWidget(self.pill, 0, Qt.AlignVCenter)
        header.addWidget(self.name_label, 0, Qt.AlignVCenter)
        header.addWidget(self.time_label, 0, Qt.AlignVCenter)
        header.addStretch(1)

        self.pause_button = PushButton(FluentIcon.PAUSE, "暂停", self)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button = PushButton(FluentIcon.CLOSE, "停止", self)
        self.stop_button.clicked.connect(self._stop_task)
        self.logs_button = PushButton(FluentIcon.DOCUMENT, "查看日志", self)
        self.logs_button.clicked.connect(self._open_logs)
        self.close_button = ToolButton(FluentIcon.CLOSE, self)
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self._close_clicked)
        for button in (
            self.pause_button,
            self.stop_button,
            self.logs_button,
            self.close_button,
        ):
            header.addWidget(button, 0, Qt.AlignVCenter)
        root.addLayout(header)

        # ---- batch progress: segbar + legend + child grid ----
        self.segbar = SegmentedBar(self)
        self.legend = QLabel(self)
        self.legend.setObjectName("runPanelLegend")
        root.addWidget(self.segbar)
        root.addWidget(self.legend)

        self.grid_container = QWidget(self)
        self.grid = QGridLayout(self.grid_container)
        self.grid.setContentsMargins(0, 2, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        root.addWidget(self.grid_container)

        # ---- core key/value rows ----
        self.rows_container = QWidget(self)
        self.rows = QVBoxLayout(self.rows_container)
        self.rows.setContentsMargins(0, 2, 0, 0)
        self.rows.setSpacing(6)
        root.addWidget(self.rows_container)

        self.more_label = LinkLabel("完整判断细节见日志 →", self._open_logs, self)
        self.more_label.setObjectName("runPanelMore")
        root.addWidget(self.more_label, 0, Qt.AlignLeft)

        self._apply_style()
        on_theme_changed(self._apply_style, self)

    def _apply_style(self):
        tokens = palette()
        wash = mix(tokens["card"], tokens["accent"], 0.07)
        self.setStyleSheet(
            f"QFrame#runPanel {{ background: qradialgradient(spread:pad, cx:1, cy:0,"
            f" radius:1.3, fx:1, fy:0, stop:0 {wash}, stop:0.5 {tokens['card']},"
            f" stop:1 {tokens['card']});"
            f" border: 1px solid {rgba(tokens['accent'], 0.35)}; border-radius: 14px; }}"
            f" QLabel#runPanelName {{ color: {tokens['ink']}; font-size: 15px;"
            " font-weight: 900; background: transparent; }"
            f" QLabel#runPanelTime {{ color: {tokens['ink_dim']};"
            f" font-family: {MONO_FONT}; font-size: 11px; background: transparent; }}"
            f" QLabel#runPanelLegend {{ color: {tokens['ink_dim']}; font-size: 12px;"
            " background: transparent; }"
            f" QLabel#runPanelMore {{ color: {tokens['accent']}; font-size: 12px;"
            " background: transparent; }"
        )
        self._restyle_pill()

    def _restyle_pill(self):
        tokens = palette()
        colors = {
            "run": (tokens["accent"], tokens["accent_soft"]),
            "pause": (tokens["warn_ink"], tokens["warn_soft"]),
            "done": (tokens["ok"], tokens["ok_soft"]),
            "abort": (tokens["warn_ink"], tokens["warn_soft"]),
            "fail": (tokens["warn"], tokens["warn_soft"]),
        }
        color, soft = colors.get(self._pill_kind, colors["done"])
        self.pill.setStyleSheet(
            f"color: {color}; background-color: {soft}; border: 1px solid {color};"
            " border-radius: 11px; padding: 2px 10px; font-size: 11px; font-weight: 700;"
        )

    _pill_kind = None

    # ---------------- ops ----------------

    def _toggle_pause(self):
        task = self._task
        if task is None:
            return
        if getattr(task, "paused", False):
            task.unpause()
        else:
            task.pause()
        self.render(task)

    def _stop_task(self):
        task = self._task
        if task is None:
            return
        task.disable()
        task.unpause()

    def _open_logs(self, *_args):
        try:
            from src.ui.log_folder import open_log_folder

            open_log_folder()
        except Exception as exc:
            logger.error(f"打开日志目录失败: {exc}")

    def _close_clicked(self):
        if callable(self.on_close):
            self.on_close()

    # ---------------- rendering ----------------

    def render(self, task) -> None:
        self._task = task
        if task is None:
            return
        info = task_info_snapshot(task)
        state = run_state(task)
        if state != self._pill_kind:
            self._pill_kind = state
            self.pill.setText(_PILL_TEXT[state])
            self._restyle_pill()

        from ok import og

        name = (
            og.app.tr(str(getattr(task, "name", "")))
            if getattr(og, "app", None)
            else str(task.name)
        )
        if name != self.name_label.text():
            self.name_label.setText(name)
        elapsed = _elapsed_text(task)
        if elapsed != self.time_label.text():
            self.time_label.setText(elapsed)

        running = state in ("run", "pause")
        pause_text = "继续" if state == "pause" else "暂停"
        if pause_text != self.pause_button.text():
            self.pause_button.setText(pause_text)
        for button, visible in (
            (self.pause_button, running),
            (self.stop_button, running),
            (self.logs_button, not running),
            (self.close_button, not running),
        ):
            if button.isHidden() == visible:
                button.setVisible(visible)

        self._render_batch(task, info, running)
        self._render_rows(task, info, state)

    def _render_batch(self, task, info, running: bool) -> None:
        children = [child.config_key for child in getattr(task, "child_tasks", ()) or ()]
        if not children:
            self.segbar.hide()
            self.legend.hide()
            self.grid_container.hide()
            return

        done = [name for name in children if contains_joined_name(info.get(_BATCH_DONE_KEY), name)]
        failed = [
            name for name in children if contains_joined_name(info.get(_BATCH_FAIL_KEY), name)
        ]
        skipped = [
            name for name in children if contains_joined_name(info.get(_BATCH_SKIP_KEY), name)
        ]
        current = str(info.get(_CURRENT_KEY, "") or "").strip()
        doing = [current] if running and current in children else []
        settled = set(done) | set(failed) | set(skipped) | set(doing)
        todo = [name for name in children if name not in settled]

        tokens = palette()
        self.segbar.set_segments(
            [
                (len(done), tokens["ok"]),
                (len(failed), tokens["warn"]),
                (len(skipped), tokens["seg_skip"]),
                (len(doing), tokens["accent_deep"], tokens["accent_hi"]),
                (len(todo), tokens["line_strong"]),
            ]
        )
        legend_parts = [f"完成 {len(done)}"]
        if failed:
            legend_parts.append(f"失败 {len(failed)}")
        if skipped:
            legend_parts.append(f"跳过 {len(skipped)}")
        if doing:
            legend_parts.append(f"执行中 {current}")
        if todo:
            legend_parts.append(f"待执行 {len(todo)}")
        legend_text = "　".join(legend_parts)
        if legend_text != self.legend.text():
            self.legend.setText(legend_text)
        self.segbar.show()
        self.legend.show()

        from qfluentwidgets import isDarkTheme

        signature = (
            tuple(done),
            tuple(failed),
            tuple(skipped),
            tuple(doing),
            tuple(todo),
            # Cell styles bake in the palette; rebuild when the theme flips.
            isDarkTheme(),
        )
        if signature != self._grid_signature:
            self._grid_signature = signature
            self._rebuild_grid(children, done, failed, skipped, doing)
        self.grid_container.show()

    def _rebuild_grid(self, children, done, failed, skipped, doing) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        tokens = palette()
        for index, name in enumerate(children):
            if name in done:
                icon, color = "✓", tokens["ok"]
            elif name in failed:
                icon, color = "✗", tokens["warn"]
            elif name in skipped:
                icon, color = "⊘", tokens["ink_faint"]
            elif name in doing:
                icon, color = "▶", tokens["accent"]
            else:
                icon, color = "·", tokens["ink_faint"]
            if name in doing:
                cell_bg, cell_border = tokens["accent_soft"], rgba(tokens["accent"], 0.5)
            elif name in failed:
                cell_bg, cell_border = tokens["warn_soft"], rgba(tokens["warn"], 0.45)
            else:
                cell_bg, cell_border = tokens["inset"], tokens["line"]
            cell = QLabel(self.grid_container)
            cell.setStyleSheet(
                f"QLabel {{ color: {tokens['ink_dim']}; background-color: {cell_bg};"
                f" border: 1px solid {cell_border}; border-radius: 10px;"
                " padding: 8px 12px; font-size: 12px; }"
            )
            cell.setText(f"<span style='color:{color}'>{icon}</span>&nbsp;&nbsp;{name}")
            self.grid.addWidget(cell, index // 3, index % 3)

    def _render_rows(self, task, info, state: str) -> None:
        rows: list[tuple[str, str, str]] = []  # (key, value, tone)
        status = str(info.get(_STATUS_KEY, "") or "")
        if status:
            tone = "warn" if state in ("abort", "fail") else "strong"
            rows.append((_STATUS_KEY, status, tone))
        current = str(info.get(_CURRENT_KEY, "") or "")
        if current and current != "-" and state in ("run", "pause"):
            rows.append((_CURRENT_KEY, current, "strong"))
        children = [child.config_key for child in getattr(task, "child_tasks", ()) or ()]
        failed = [
            name for name in children if contains_joined_name(info.get(_BATCH_FAIL_KEY), name)
        ]
        if failed:
            rows.append(("失败子任务", "、".join(failed), "warn"))
        warning = info.get("Warning")
        if warning and state not in ("run", "pause"):
            rows.append(("Warning", str(warning), "warn"))
        error = info.get("Error")
        if error:
            rows.append(("Error", str(error), "warn"))
        extra_keys = [
            key for key in info if key not in _STANDARD_KEYS and str(info[key]) not in ("", "-")
        ]
        for key in extra_keys[:2]:
            rows.append((str(key), str(info[key]), ""))
        log_line = str(info.get("Log", "") or "")
        if log_line:
            rows.append(("最近日志", log_line, ""))

        from qfluentwidgets import isDarkTheme

        # Row colors bake in the palette; rebuild when the theme flips.
        signature = (tuple(rows), isDarkTheme())
        if signature == self._rows_signature:
            return
        self._rows_signature = signature

        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        tokens = palette()
        tone_colors = {
            "strong": tokens["ink"],
            "warn": tokens["warn_ink"],
            "": tokens["ink_dim"],
        }
        for key, value, tone in rows:
            row = QWidget(self.rows_container)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(12)
            key_label = QLabel(key, row)
            key_label.setFixedWidth(76)
            key_label.setStyleSheet(
                f"color: {tokens['ink_faint']}; font-size: 11px; background: transparent;"
            )
            value_label = QLabel(value, row)
            value_label.setWordWrap(True)
            value_label.setStyleSheet(
                f"color: {tone_colors[tone]}; font-size: 12px; background: transparent;"
            )
            row_layout.addWidget(key_label, 0, Qt.AlignTop)
            row_layout.addWidget(value_label, 1)
            self.rows.addWidget(row)


def _install_run_panel_for(tab_class) -> bool:
    if getattr(tab_class, "_run_panel_installed", False):
        return False

    original_init = tab_class.__init__

    def quest_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.run_panel = RunPanel(self.view)
        self.run_panel.on_close = self.close_task_info
        self.run_panel.hide()
        anchor = getattr(self, "task_info_container", None)
        index = self.vBoxLayout.indexOf(anchor) if anchor is not None else 0
        self.vBoxLayout.insertWidget(max(index, 0), self.run_panel)
        if anchor is not None:
            anchor.hide()

    def quest_update_info_table(self):
        from ok import og

        run_panel = getattr(self, "run_panel", None)
        if run_panel is None:
            # TaskTab.__init__ calls update_info_table before the chained tab
            # init creates the panel.
            return

        executor = getattr(og, "executor", None)
        if executor is None:
            run_panel.hide()
            return

        current_task = executor.current_task
        if current_task is not None and self.in_current_list(current_task):
            current_info_run = (id(current_task), getattr(current_task, "start_time", None))
            if current_info_run != self.current_info_run:
                self.dismissed_info_run = None
            self.current_info_run = current_info_run
            self.last_task = current_task
        if self.current_info_run == self.dismissed_info_run and self.last_task is not None:
            run_panel.hide()
            return
        if executor.current_task is None and not self.keep_info_when_done:
            run_panel.hide()
        elif self.last_task is not None:
            if not run_panel.isVisible():
                run_panel.show()
            run_panel.render(self.last_task)

    def quest_close_task_info(self):
        self.dismissed_info_run = self.current_info_run
        run_panel = getattr(self, "run_panel", None)
        if run_panel is not None:
            run_panel.hide()

    def quest_update_task_info(self, task):
        # The framework renders the raw info table here; the run panel renders
        # from quest_update_info_table instead. Kept as a no-op so the 1s
        # timer path stays intact.
        if task is None:
            run_panel = getattr(self, "run_panel", None)
            if run_panel is not None:
                run_panel.hide()

    tab_class.__init__ = quest_init
    tab_class.update_info_table = quest_update_info_table
    tab_class.close_task_info = quest_close_task_info
    tab_class.update_task_info = quest_update_task_info
    tab_class._run_panel_installed = True
    return True


def install_run_panel() -> bool:
    """Swap one-time and trigger task info tables for the shared run panel."""
    from ok.ui.qt.tasks.OneTimeTaskTab import OneTimeTaskTab
    from ok.ui.qt.tasks.TriggerTaskTab import TriggerTaskTab

    installed_onetime = _install_run_panel_for(OneTimeTaskTab)
    installed_trigger = _install_run_panel_for(TriggerTaskTab)
    return installed_onetime or installed_trigger
