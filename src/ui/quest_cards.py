"""Quest-style task card chrome (mockup V2, 2026-08-18).

Adds two things on top of the framework's ``TaskCard`` (and Codex's
responsive patch):

* a painted status seal dot on the left of the header (running / done-today /
  idle), replacing the always-hidden icon slot;
* a mono "meta" line under the description with the last-run summary from
  ``src.tasks.run_history`` and the live stage while running.

The batch card (一键完成日常) keeps its child on/off switches inside the normal
expand view: like every other task card, they appear only after clicking the
card to expand it (2026-08-18 user correction — an earlier always-visible
sub panel under the header was a misreading of the mockup).

Badge chips are recolored to the mockup token palette (合辑=accent, 日常=ok,
跑商=info, PVP=warn, 内测=beta, neutral gray for 刷级/测试) and refreshed with
the qfluentwidgets theme.

The shared 1s heartbeat is dirty-checked: a visible card whose seal state,
meta text, width and expand state are all unchanged costs only two small
string computations — no ``setText``, no stylesheet writes and no
height-for-width layout walks, so the tick never triggers a page relayout.

Expand/collapse animation rule (2026-08-18, user-reported jank + collapse
flashback): while ``expandAni`` is running it is the ONLY writer of the
card's total height.  The resize chain (``_adjustViewSize`` /
``apply_quest_chrome``) must not write the height mid-flight — otherwise
every animation frame is immediately overwritten by the final height, which
reads as a two-frame expand and a flickering collapse.  Content height is
cached per view width so a frame costs no height-for-width walk either.
"""

from __future__ import annotations

import time
from weakref import ref

from PySide6.QtCore import QAbstractAnimation, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QWidget

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


def _animation_running(card) -> bool:
    """True while the expand/collapse animation owns the card's height."""
    ani = getattr(card, "expandAni", None)
    return bool(ani is not None and ani.state() == QAbstractAnimation.Running)


def _measure_content_height(card) -> int:
    """The height-for-width walk over the card's config rows (expensive)."""
    width = max(0, card.view.width())
    if card.viewLayout.hasHeightForWidth():
        height = card.viewLayout.heightForWidth(width)
        if height >= 0:
            return height
    return card.viewLayout.sizeHint().height()


def _content_height(card) -> int:
    """Cached content height; the walk only runs on width/content changes.

    Content changes funnel through ``_adjustViewSize`` (framework contract:
    sub-config sync, config updates, initial build), which invalidates the
    cache; width changes are caught by the width key.
    """
    width = max(0, card.view.width())
    cache = getattr(card, "_quest_content_cache", None)
    if cache is not None and cache[0] == width:
        return cache[1]
    height = _measure_content_height(card)
    card._quest_content_cache = (width, height)
    return height


def apply_quest_chrome(card) -> None:
    """Sync header height, viewport margins and total height.

    Every write is guarded by a difference check so repeated calls reach a
    fixed point instead of re-triggering resizeEvent forever; a reentrancy
    guard covers the synchronous resizeEvent -> adjust -> chrome cycle.
    While the expand animation runs it owns the total height — chrome only
    keeps the header and margins in sync and leaves the height alone.
    """
    if getattr(card, "_quest_chrome_busy", False):
        return
    card._quest_chrome_busy = True
    try:
        meta = getattr(card, "_quest_meta", None)
        meta_visible = bool(meta is not None and meta.text())
        header_height = HEADER_HEIGHT_WITH_META if meta_visible else HEADER_HEIGHT_PLAIN

        if card.card.height() != header_height:
            card.card.setFixedHeight(header_height)
        if card.viewportMargins().top() != header_height:
            card.setViewportMargins(0, header_height, 0, 0)

        if _animation_running(card):
            return
        target = header_height + _content_height(card) if card.isExpand else header_height
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
                # Force one chrome recompute per card after a theme flip.
                card._quest_refresh_key = None
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
    """Dirty-checked per-card refresh.

    The seal state and meta text are recomputed every tick (cheap, in-memory),
    but widgets are only touched when one of the values that can change the
    layout actually changed; content-height changes from config edits reach
    ``apply_quest_chrome`` through the resize/config-sync paths instead.
    """
    task = card.task
    onetime = getattr(card, "_quest_onetime", True)
    state = seal_state(task, onetime=onetime)
    text = meta_text(task) if onetime else ""

    key = (state, text, card.width(), card.isExpand)
    if key == getattr(card, "_quest_refresh_key", None):
        return
    card._quest_refresh_key = key

    card._quest_seal.set_state(state)
    meta = card._quest_meta
    if text != meta.text():
        meta.setText(text)
    meta.setVisible(bool(text))
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
    seal = getattr(card, "_quest_seal", None)
    if seal is not None:
        seal.update()


def _chain_config_card_methods() -> None:
    from ok.gui.tasks.ConfigCard import ConfigCard

    if getattr(ConfigCard, "_quest_chrome_chained", False):
        return

    original_resize = ConfigCard.resizeEvent

    def quest_adjust_view_size(self):
        # Content changes funnel through here (sub-config sync, config
        # updates, resize).  The cache is always dropped — a funnel call is
        # event-driven, so at most one frame re-walks and re-caches — but the
        # total-height write still waits for the animation to release
        # ownership, and the timing driver aborts onto the fresh geometry.
        self._quest_content_cache = None
        content_height = _content_height(self)
        self.spaceWidget.setFixedHeight(content_height)
        if self.isExpand and not _animation_running(self):
            self.setFixedHeight(self.card.height() + content_height)

    def quest_expand_value_changed(self):
        # Sole height writer while the animation runs; cache hit, no walk.
        content_height = _content_height(self)
        header_height = self.card.height()
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
        _install_seal_and_meta(self)
        _apply_quest_theme(self)
        refresh_quest_card(self)
        _get_refresher().register(self)

    TaskCard.__init__ = quest_task_card_init
    TaskCard._quest_cards_installed = True
    return True
