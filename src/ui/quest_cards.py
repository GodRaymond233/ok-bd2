"""Quest-style task card chrome (mockup V2, 2026-08-18).

Adds three things on top of the framework's ``TaskCard`` (and Codex's
responsive patch):

* a painted status seal dot on the left of the header (running / done-today /
  idle), replacing the always-hidden icon slot;
* a mono "meta" line under the description with the last-run summary from
  ``src.tasks.run_history`` and the live stage while running;
* for the batch card (一键完成日常), the child on/off switches are re-homed
  from the collapsed expand view into an always-visible sub panel attached
  under the header, one widget per config key so there is no state to sync.

Badge chips are recolored to the mockup token palette (合辑=accent, 日常=ok,
跑商=info, PVP=warn, 内测=beta, neutral gray for 刷级/测试) and refreshed with
the qfluentwidgets theme.
"""

from __future__ import annotations

import time
from weakref import ref

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.tasks.run_history import day_start_ts, default_store
from src.ui.quest_theme import MONO_FONT, palette

# Tasks with weekly (Monday 04:00 Beijing) instead of daily refresh semantics.
WEEKLY_TASK_NAMES = {"每周跑图"}

HEADER_HEIGHT_PLAIN = 50
HEADER_HEIGHT_WITH_META = 68

_BADGE_KIND_COLORS = {
    "日常合辑": ("accent", "accent_soft"),
    "日常": ("ok", "ok_soft"),
    "跑商": ("info", "info_soft"),
    "PVP": ("warn", "warn_soft"),
    "内测功能": ("beta", "beta_soft"),
}


def _badge_kind(task) -> str:
    """Classify a task into a badge chip (text kept from the base mapping)."""
    name = str(getattr(task, "name", ""))
    group = str(getattr(task, "group_name", ""))
    if name == "一键完成日常":
        return "日常合辑"
    if "PVP" in name or "镜中之战" in name:
        return "PVP"
    if "跑商" in name or "砍价" in name:
        return "跑商"
    if "内测" in group or "跑图" in name:
        return "内测功能"
    if group == "日常/周常":
        return "日常"
    if group == "自动刷级":
        return "自动刷级"
    if group == "测试":
        return "测试"
    return "任务"


def format_duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _when_text(finished: float, now: float | None = None) -> str:
    """'今天 09:12' / '昨天 23:40' / '8月5日 18:02', all in Beijing time."""
    now = time.time() if now is None else now
    from datetime import datetime

    from src.tasks.run_history import BEIJING_TZ

    moment = datetime.fromtimestamp(finished, tz=BEIJING_TZ)
    hm = moment.strftime("%H:%M")
    if finished >= day_start_ts(now):
        return f"今天 {hm}"
    if finished >= day_start_ts(now - 86400):
        return f"昨天 {hm}"
    return f"{moment.month}月{moment.day}日 {hm}"


def seal_state(task, store=None, onetime=True) -> str:
    """One of run / ok / idle for the seal dot.

    Trigger tasks are long-lived: enabled means "on duty", not "running", so
    they map to ok instead of the animated run state.
    """
    if getattr(task, "enabled", False):
        return "run" if onetime else "ok"
    if not onetime:
        return "idle"
    store = store or default_store()
    name = str(getattr(task, "name", ""))
    if name in WEEKLY_TASK_NAMES:
        return "ok" if store.is_completed_this_week(name) else "idle"
    return "ok" if store.is_completed_today(name) else "idle"


def meta_text(task, store=None) -> str:
    """Live stage while running, otherwise the last-run summary line."""
    if getattr(task, "enabled", False):
        info = getattr(task, "info", {}) or {}
        stage = info.get("当前子任务") or info.get("状态") or ""
        prefix = "已暂停" if getattr(task, "paused", False) else "进行中"
        return f"{prefix} · {stage}" if stage else prefix
    store = store or default_store()
    record = store.last_run(str(getattr(task, "name", "")))
    if not record:
        return ""
    when = _when_text(record["finished"])
    duration = format_duration(record.get("duration"))
    if record.get("ok"):
        text = f"上次完成 · {when}"
        if duration:
            text += f" · 耗时 {duration}"
        return text
    status = record.get("status") or "未成功"
    return f"上次运行 · {when} · {status}"


class QuestSealDot(QWidget):
    """A 9px status dot, theme-aware, with an accent halo while running."""

    SIZE = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self.setFixedSize(self.SIZE, self.SIZE)

    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, _event):
        tokens = palette()
        color = {
            "run": tokens["accent"],
            "ok": tokens["ok"],
        }.get(self._state, tokens["seal_idle"])
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        center = self.SIZE / 2
        if self._state == "run":
            halo = QColor(tokens["accent_soft"])
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(int(center - 10), int(center - 10), 20, 20)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(int(center - 4.5), int(center - 4.5), 9, 9)
        painter.end()


def quest_sub_height(card) -> int:
    panel = getattr(card, "_quest_sub_panel", None)
    if panel is None:
        return 0
    rows = [row for row in getattr(panel, "_quest_rows", []) if not row.isHidden()]
    if not rows:
        return 0
    layout = panel.layout()
    width = max(card.width(), 200)
    if layout.hasHeightForWidth():
        height = layout.heightForWidth(width)
        if height >= 0:
            return height
    return layout.sizeHint().height()


def _content_height(card) -> int:
    """Same height-for-width math as the responsive patch's content sizing."""
    width = max(0, card.view.width())
    if card.viewLayout.hasHeightForWidth():
        height = card.viewLayout.heightForWidth(width)
        if height >= 0:
            return height
    return card.viewLayout.sizeHint().height()


def apply_quest_chrome(card) -> None:
    """Sync header height, viewport margins, sub panel and total height.

    Every write is guarded by a difference check so repeated calls reach a
    fixed point instead of re-triggering resizeEvent forever; a reentrancy
    guard covers the synchronous resizeEvent -> adjust -> chrome cycle.
    """
    if getattr(card, "_quest_chrome_busy", False):
        return
    card._quest_chrome_busy = True
    try:
        meta = getattr(card, "_quest_meta", None)
        meta_visible = bool(meta is not None and meta.text())
        header_height = HEADER_HEIGHT_WITH_META if meta_visible else HEADER_HEIGHT_PLAIN

        panel = getattr(card, "_quest_sub_panel", None)
        if panel is not None and 0 < card.width() != panel.width():
            # Give the rows a realistic width before asking their height-for-width.
            panel.resize(card.width(), panel.height())
        sub_height = quest_sub_height(card)

        if card.card.height() != header_height:
            card.card.setFixedHeight(header_height)
        top = header_height + sub_height
        if card.viewportMargins().top() != top:
            card.setViewportMargins(0, top, 0, 0)
        if panel is not None:
            panel.setGeometry(0, header_height, max(card.width(), 0), sub_height)
            panel.setVisible(sub_height > 0)

        target = top + _content_height(card) if card.isExpand else top
        if card.height() != target:
            card.setFixedHeight(target)
    finally:
        card._quest_chrome_busy = False


class _CardRefresher(QObject):
    """Shared 1s heartbeat + framework signals driving card chrome updates.

    Lives on the UI thread (created lazily by the first TaskCard) so framework
    signals emitted from the executor thread are queued, not run inline.
    """

    def __init__(self):
        super().__init__()
        self._cards: list[ref] = []
        self._timer: QTimer | None = None
        from qfluentwidgets import isDarkTheme

        self._dark = isDarkTheme()

    def register(self, card) -> None:
        self._cards.append(ref(card))
        self._ensure_timer()
        self._connect_signals()

    def _ensure_timer(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self.refresh_all)
        if not self._timer.isActive():
            self._timer.start()

    def _connect_signals(self) -> None:
        if getattr(self, "_signals_connected", False):
            return
        from ok.gui.Communicate import communicate

        communicate.task.connect(self._on_task_signal)
        communicate.task_done.connect(self._on_task_signal)
        self._signals_connected = True

    def _on_task_signal(self, *_args) -> None:
        self.refresh_all()

    def refresh_all(self) -> None:
        from qfluentwidgets import isDarkTheme

        dark = isDarkTheme()
        theme_flipped = dark != self._dark
        self._dark = dark

        alive = []
        for card_ref in self._cards:
            card = card_ref()
            if card is None:
                continue
            alive.append(card_ref)
            if theme_flipped:
                _apply_quest_theme(card)
            if card.isVisible():
                refresh_quest_card(card)
        self._cards = alive


_refresher: _CardRefresher | None = None


def _get_refresher() -> _CardRefresher:
    global _refresher
    if _refresher is None:
        _refresher = _CardRefresher()
    return _refresher


def refresh_quest_card(card) -> None:
    task = card.task
    onetime = getattr(card, "_quest_onetime", True)
    state = seal_state(task, onetime=onetime)
    card._quest_seal.set_state(state)

    text = meta_text(task) if onetime else ""
    meta = card._quest_meta
    meta.setVisible(bool(text))
    if text:
        meta.setText(text)
    apply_quest_chrome(card)


def _restyle_badge(card, tokens) -> None:
    badge = getattr(card, "badge_label", None)
    if badge is None:
        return
    from src.ui.quest_theme import chip_qss

    color_key, soft_key = _BADGE_KIND_COLORS.get(_badge_kind(card.task), ("ink_faint", "line"))
    badge.setStyleSheet(
        f"QLabel#bd2CategoryBadge {{{chip_qss(tokens[color_key], tokens[soft_key])}}}"
    )


def _install_seal_and_meta(card) -> None:
    card._quest_seal = QuestSealDot(card.card)
    card.card.hBoxLayout.insertWidget(1, card._quest_seal, 0, Qt.AlignVCenter)

    meta = QLabel(card.card)
    meta.setObjectName("questMetaLabel")
    meta.hide()
    card._quest_meta = meta
    card.card.vBoxLayout.addWidget(meta)


def _style_sub_panel(card, tokens) -> None:
    panel = getattr(card, "_quest_sub_panel", None)
    if panel is None:
        return
    rows = panel._quest_rows
    panel.setStyleSheet(
        f"QWidget#questSubPanel {{ background-color: {tokens['card']};"
        f" border-top: 1px solid {tokens['line']}; }}"
    )
    for row in rows:
        row.setStyleSheet(
            f"QWidget[questSubRow=true] {{ border-bottom: 1px solid {tokens['line']};"
            " background: transparent; }"
        )
    if rows:
        rows[-1].setStyleSheet(
            "QWidget[questSubRow=true] { border: none; background: transparent; }"
        )


def _apply_quest_theme(card) -> None:
    """Single per-card theme refresh (keeps one themeChanged receiver)."""
    tokens = palette()
    meta = getattr(card, "_quest_meta", None)
    if meta is not None:
        meta.setStyleSheet(
            f"QLabel#questMetaLabel {{ color: {tokens['ink_faint']};"
            f" font-family: {MONO_FONT}; font-size: 11px; background: transparent; }}"
        )
    _restyle_badge(card, tokens)
    _style_sub_panel(card, tokens)
    seal = getattr(card, "_quest_seal", None)
    if seal is not None:
        seal.update()


def _install_sub_panel(card, task) -> None:
    """Re-home the batch child switches into an always-visible panel."""
    child_keys = [child.config_key for child in getattr(task, "child_tasks", ())]
    widgets = [
        card.config_widget_by_key[key] for key in child_keys if key in card.config_widget_by_key
    ]
    if not widgets:
        return

    panel = QWidget(card)
    panel.setObjectName("questSubPanel")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 6)
    layout.setSpacing(0)
    for widget in widgets:
        card.viewLayout.removeWidget(widget)
        widget.setParent(panel)
        widget.setProperty("questSubRow", True)
        layout.addWidget(widget)
    card._quest_sub_panel = panel
    panel._quest_rows = widgets

    # Re-home the rows again whenever the framework re-syncs sub-config
    # visibility/order (it re-inserts sub-config widgets into the view layout).
    def _rehome(*_args):
        for widget in widgets:
            if widget.parentWidget() is not panel:
                card.viewLayout.removeWidget(widget)
                widget.setParent(panel)
                layout.addWidget(widget)
        apply_quest_chrome(card)

    master = card.config_widget_by_key.get("启用")
    master_switch = getattr(master, "switch_button", None)
    if master_switch is not None:
        master_switch.checkedChanged.connect(_rehome)


def _chain_config_card_methods() -> None:
    from ok.gui.tasks.ConfigCard import ConfigCard

    if getattr(ConfigCard, "_quest_chrome_chained", False):
        return

    original_resize = ConfigCard.resizeEvent

    def quest_adjust_view_size(self):
        # Sub-panel-aware replacement for the responsive patch's version; for
        # cards without a sub panel quest_sub_height() is 0 and the behavior
        # is identical (space widget + expanded full height in one writer).
        content_height = _content_height(self)
        self.spaceWidget.setFixedHeight(content_height)
        if self.isExpand:
            self.setFixedHeight(self.card.height() + quest_sub_height(self) + content_height)

    def quest_expand_value_changed(self):
        content_height = _content_height(self)
        header_height = self.card.height() + quest_sub_height(self)
        self.setFixedHeight(
            max(
                header_height + content_height - self.verticalScrollBar().value(),
                header_height,
            )
        )

    def quest_resize_event(self, event):
        original_resize(self, event)
        apply_quest_chrome(self)

    ConfigCard._adjustViewSize = quest_adjust_view_size
    ConfigCard._onExpandValueChanged = quest_expand_value_changed
    ConfigCard.resizeEvent = quest_resize_event
    ConfigCard._quest_chrome_chained = True


def install_quest_cards() -> bool:
    """Chain the quest chrome onto TaskCard after the responsive patch."""
    from ok.gui.tasks.TaskCard import TaskCard

    if getattr(TaskCard, "_quest_cards_installed", False):
        return False

    _chain_config_card_methods()
    original_task_card_init = TaskCard.__init__

    def quest_task_card_init(self, task, onetime):
        original_task_card_init(self, task, onetime)
        self._quest_onetime = bool(onetime)
        self._quest_sub_panel = None
        _install_seal_and_meta(self)
        _install_sub_panel(self, task)
        _apply_quest_theme(self)
        refresh_quest_card(self)
        _get_refresher().register(self)

    TaskCard.__init__ = quest_task_card_init
    TaskCard._quest_cards_installed = True
    return True
