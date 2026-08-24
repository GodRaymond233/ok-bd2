"""Fluent (WinUI) motion for page switches and the quest board entrance.

Replicates the motion language of the Windows Terminal settings UI and the
Microsoft Store (user request, 2026-08-24):

* every navigation switch replaces the hard cut with a transition — the
  incoming page rises from below while fading in (300 ms, the WinUI entrance
  easing ``cubic-bezier(0.1, 0.9, 0.2, 1)``); the previous page is hidden by
  the stack at the switch itself — no old-page overlay exists, because even
  a fading underlay left a readable double-exposure window that read as
  ghosting (user feedback, two rounds);
* the 日常/周常 board (banner, cards, footer) enters staggered on its first
  appearance — on that one arrival the content stagger replaces the
  whole-page rise (WinUI semantics: a first load entrances the content,
  navigation transitions the page); later switches use the page transition;
* the start page's list columns (选择窗口 / 截图方式 / 交互方式) get a
  selection indicator that slides between rows like the sidebar navigation,
  instead of the delegate's static per-row bar.

Every animation is driven by a precise timer at the display's refresh
cadence (``_RefreshDriver``), not Qt's unified ~60 fps animation clock — on
high-refresh screens the unified clock beats against the compositor and
reads as uneven motion (the same reason ``expand_timing`` exists for the
expand animation).

Real widgets only.  No snapshots are taken, so no snapshot→widget handoff
frame exists — the DWM partial-update ghosting documented for the retired
expand-transition overlay cannot occur by construction.  Widget heights are
never written: the expand animation's sole-writer invariants are untouched.

Easing/duration reference: the WinUI motion timing tiers (300 ms standard
duration; decelerate ``cubic-bezier(0.1, 0.9, 0.2, 1)`` for entrances,
accelerate ``cubic-bezier(0.7, 0, 1, 0.5)`` for exits), which qfluentwidgets
1.11 mirrors in its own WinUI-modeled transition widgets.

Kill switch: ``OK_BD2_FLUENT_MOTION=0`` (or ``set_fluent_motion_enabled(
False)``) disables both motions; switches then behave exactly as before.
"""

from __future__ import annotations

import os
from weakref import ref

from ok import Logger
from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

logger = Logger.get_logger(__name__)

_ENV_SWITCH = "OK_BD2_FLUENT_MOTION"

# WinUI motion timing: 300 ms standard duration, entrance easing.
PAGE_IN_MS = 300
ENTRANCE_OFFSET_RATIO = 0.08
ENTRANCE_OFFSET_MIN_PX = 40
ENTRANCE_OFFSET_MAX_PX = 110

# Store-style list entrance: every item rises slightly later than the last.
STAGGER_IN_MS = 280
STAGGER_STEP_MS = 35
STAGGER_MAX_STEPS = 8
STAGGER_OFFSET_PX = 28

# Sliding selection indicator for the start page's list columns: the
# qfluentwidgets delegate draws a static 3px accent bar on the selected row;
# this replaces it with a widget that slides between rows like the sidebar.
SELECTION_IN_MS = 200
SELECTION_BAR_WIDTH = 3

# Refresh-paced driving (see _RefreshDriver).
_MIN_INTERVAL_MS = 4
_MAX_INTERVAL_MS = 16
_FALLBACK_INTERVAL_MS = 8


def _bezier(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


# WinUI entrance (decelerate) curve.
_ENTRANCE_CURVE = _bezier(0.1, 0.9, 0.2, 1.0)


def _env_disables() -> bool:
    value = os.environ.get(_ENV_SWITCH, "").strip().lower()
    return value in {"0", "false", "off", "no"}


# Evaluated at import so every installer honors the env switch even if the
# page-transition attach bails early.
_enabled = not _env_disables()


def set_fluent_motion_enabled(flag: bool) -> None:
    """Runtime kill switch; disabling also finishes any motion in flight."""
    global _enabled
    _enabled = bool(flag)
    if not _enabled:
        for manager_ref in list(_MANAGERS):
            manager = manager_ref()
            if manager is not None:
                try:
                    manager.finish_active()
                except RuntimeError:
                    pass
        for run_ref in list(_STAGGER_RUNS):
            run = run_ref()
            if run is not None:
                try:
                    run.abort(land=True)
                except RuntimeError:
                    pass
        for selection_ref in list(_SELECTIONS):
            selection = selection_ref()
            if selection is not None:
                try:
                    selection.set_active(False)
                except RuntimeError:
                    pass
    else:
        for selection_ref in list(_SELECTIONS):
            selection = selection_ref()
            if selection is not None:
                try:
                    selection.set_active(True)
                except RuntimeError:
                    pass


def fluent_motion_enabled() -> bool:
    return _enabled


def _page_offset_px(page: QWidget) -> int:
    return max(
        ENTRANCE_OFFSET_MIN_PX,
        min(ENTRANCE_OFFSET_MAX_PX, int(page.height() * ENTRANCE_OFFSET_RATIO)),
    )


def _refresh_interval_ms() -> int:
    rate = 0.0
    try:
        window = QApplication.activeWindow()
        handle = window.windowHandle() if window is not None else None
        screen = handle.screen() if handle is not None else None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is not None:
            rate = float(screen.refreshRate())
    except (RuntimeError, AttributeError, TypeError):
        rate = 0.0
    if not 30.0 <= rate <= 1000.0:
        return _FALLBACK_INTERVAL_MS
    return max(_MIN_INTERVAL_MS, min(_MAX_INTERVAL_MS, round(1000.0 / rate)))


class _RefreshDriver(QObject):
    """Samples an eased progress on a precise timer at the display's refresh
    cadence.  Qt's unified animation timer ticks at ~60 fps regardless of
    the display; on high-refresh screens that beat against the compositor
    and reads as uneven motion — the same reason ``expand_timing`` exists.
    """

    def __init__(self, owner, duration_ms, on_tick, on_finished):
        super().__init__(owner)
        self._duration_ms = max(1, int(duration_ms))
        self._on_tick = on_tick
        self._on_finished = on_finished
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._elapsed.start()
        self._running = True
        self._timer.start(_refresh_interval_ms())
        self._tick()

    def finish(self) -> None:
        """Stop and land the final frame synchronously."""
        if not self._running:
            return
        self._running = False
        try:
            self._timer.stop()
        except RuntimeError:
            return
        try:
            self._on_tick(1.0)
            self._on_finished()
        except RuntimeError:
            pass

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    def _tick(self):
        if not self._running:
            return
        progress = min(1.0, self._elapsed.elapsed() / self._duration_ms)
        try:
            self._on_tick(progress)
        except RuntimeError:
            self.stop()
            return
        if progress >= 1.0:
            self._running = False
            self._timer.stop()
            try:
                self._on_finished()
            except RuntimeError:
                pass


def _delayed_progress(progress: float, delay_ms: int, duration_ms: int) -> float:
    """Map a run-level progress onto an item's animation that starts
    ``delay_ms`` into the run."""
    elapsed = progress * (delay_ms + duration_ms)
    if elapsed <= delay_ms:
        return 0.0
    return min(1.0, (elapsed - delay_ms) / duration_ms)


class _PageTransition:
    """One page switch: the incoming page rises + fades in over the page
    background.  The previous page is hidden by the stack at the switch
    itself — no old-page overlay exists: even a fading underlay left a
    readable double-exposure window that read as ghosting (user feedback,
    two rounds).  Every pixel is a real widget — no snapshots, no handoff."""

    def __init__(self, incoming: QWidget):
        self._incoming_ref = ref(incoming)
        self._cleaned = False
        self._offset = _page_offset_px(incoming)
        self._effect = QGraphicsOpacityEffect(incoming)
        incoming.setGraphicsEffect(self._effect)
        self._driver = _RefreshDriver(incoming, PAGE_IN_MS, self._apply, self._land)

    def start(self) -> bool:
        incoming = self._incoming_ref()
        if incoming is None:
            return False
        self._apply(0.0)
        self._driver.start()
        return True

    def _apply(self, progress: float) -> None:
        incoming = self._incoming_ref()
        if incoming is None:
            raise RuntimeError("incoming page destroyed")
        eased = _ENTRANCE_CURVE.valueForProgress(progress)
        y = round(self._offset * (1.0 - eased))
        if incoming.pos().y() != y:
            incoming.move(0, y)
        self._effect.setOpacity(eased)

    def _land(self) -> None:
        self._cleanup()

    def finish_now(self) -> None:
        """Land immediately — a newer switch, the kill switch or destruction
        interrupted the transition."""
        if self._driver.running:
            self._driver.finish()

    def _cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        incoming = self._incoming_ref()
        if incoming is not None:
            try:
                incoming.setGraphicsEffect(None)
                if incoming.pos() != QPoint(0, 0):
                    incoming.move(0, 0)
            except RuntimeError:
                pass


class _PageTransitionManager(QObject):
    """Animates the stacked widget's currentChanged, whichever code switched."""

    def __init__(self, stack, window: QWidget):
        super().__init__(stack)
        self._stack = stack
        self._window_ref = ref(window)
        self._current = stack.currentWidget()
        self._active: _PageTransition | None = None
        stack.currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, index: int) -> None:
        incoming = self._stack.widget(index)
        if incoming is None:
            return
        previous = self._current
        self._current = incoming
        if previous is incoming:
            return
        self.finish_active()
        window = self._window_ref()
        if not _enabled or window is None or not window.isVisible():
            return
        if bool(incoming.property("_fluent_entrance_pending")):
            # The board's first appearance plays its content stagger instead
            # of the whole-page rise (WinUI semantics: a first load entrances
            # the content, navigation transitions the page).
            return
        transition = None
        try:
            transition = _PageTransition(incoming)
            started = transition.start()
        except Exception as exc:  # a failed transition must never block the switch
            logger.info(f"fluent page transition skipped: {exc!r}")
            started = False
        if not started:
            if transition is not None:
                transition.finish_now()
            return
        self._active = transition
        # The entrance stagger yields to a switch transition covering the tab.
        incoming.setProperty("_fluent_transitioned", True)

    def finish_active(self) -> None:
        transition = self._active
        if transition is None:
            return
        self._active = None
        try:
            transition.finish_now()
        except RuntimeError:
            # The page (and with it the transition's driver) was destroyed
            # mid-flight; there is nothing left to land.
            pass


# Weak registries so the kill switch can land in-flight motions without
# keeping dead windows or tabs alive.
_MANAGERS: list = []
_STAGGER_RUNS: list = []
_SELECTIONS: list = []


def _attach_page_transition(stack, window: QWidget) -> bool:
    _MANAGERS.append(ref(_PageTransitionManager(stack, window)))
    return True


def install_fluent_page_transition(main_window) -> bool:
    """Hook the main window's stacked widget so every tab switch transitions.

    Called from ``Globals.on_show_main_window`` (before ``show()``); switches
    fired while the window is still invisible — the startup ones — stay
    instant.
    """
    global _enabled
    stack = getattr(main_window, "stackedWidget", None)
    if stack is None:
        logger.warning("fluent motion: main window has no stackedWidget, skip")
        return False
    if getattr(stack, "_fluent_motion_attached", False):
        return False
    if not hasattr(stack, "currentChanged"):
        logger.info("fluent motion: stacked widget lacks currentChanged, skip")
        return False
    _enabled = not _env_disables()
    _attach_page_transition(stack, main_window)
    stack._fluent_motion_attached = True
    logger.info(f"fluent page transition installed: enabled={_enabled}")
    return True


class _SelectionBar(QWidget):
    """The 3px accent bar, painted to match the delegate's indicator exactly
    (rounded 1.5, vertically inset by 0.257 of the row height)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _event):
        from qfluentwidgets import themeColor

        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(themeColor()))
        painter.drawRoundedRect(self.rect().toRectF(), 1.5, 1.5)
        painter.end()


class _SlidingSelection(QObject):
    """Slides the selection indicator between rows of a qfluentwidgets
    ListWidget, like the sidebar navigation indicator.

    The delegate's static per-row indicator is suppressed (an instance-level
    ``_drawIndicator`` override — the paint path is Python) while this is
    active; the selected-row translucent background stays with the delegate.
    The kill switch restores the static indicator."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._bar = _SelectionBar(view.viewport())
        self._driver: _RefreshDriver | None = None
        view.itemSelectionChanged.connect(self._on_selection_changed)
        view.viewport().installEventFilter(self)
        scroll_bar = view.verticalScrollBar()
        if scroll_bar is not None:
            # Scrolling moves the row rects; the bar must track the content.
            scroll_bar.valueChanged.connect(self._on_scrolled)
        from qfluentwidgets import qconfig

        qconfig.themeChanged.connect(self._bar.update)
        _SELECTIONS.append(ref(self))
        self.set_active(_enabled, sync=True)

    def set_active(self, active: bool, sync: bool = False) -> None:
        delegate = getattr(self._view, "delegate", None)
        if active:
            if delegate is not None:
                delegate._drawIndicator = self._no_indicator
        else:
            if delegate is not None and "_drawIndicator" in vars(delegate):
                del delegate._drawIndicator
            self._stop_slide()
            self._bar.hide()
        if sync or active:
            self._sync(snap=True)

    @staticmethod
    def _no_indicator(_painter, _option, _index):
        pass

    def _on_selection_changed(self):
        self._sync(snap=not _enabled)

    def _on_scrolled(self, *_args):
        self._sync(snap=True)

    def _stop_slide(self) -> None:
        if self._driver is not None:
            self._driver.stop()
            self._driver.deleteLater()
            self._driver = None

    def _start_slide(self, target: QRect) -> None:
        self._stop_slide()
        start = self._bar.geometry()
        bar = self._bar

        def on_tick(progress):
            eased = _ENTRANCE_CURVE.valueForProgress(progress)
            y = round(start.y() + (target.y() - start.y()) * eased)
            height = round(start.height() + (target.height() - start.height()) * eased)
            bar.setGeometry(0, y, SELECTION_BAR_WIDTH, height)

        self._driver = _RefreshDriver(bar, SELECTION_IN_MS, on_tick, lambda: None)
        self._driver.start()

    def _sync(self, snap: bool) -> None:
        if not _enabled:
            self._stop_slide()
            self._bar.hide()
            return
        row = self._view.currentRow()
        if row < 0 or row >= self._view.count():
            self._stop_slide()
            self._bar.hide()
            return
        rect = self._view.visualItemRect(self._view.item(row))
        if not rect.isValid():
            self._stop_slide()
            self._bar.hide()
            return
        inset = round(0.257 * rect.height())
        geometry = QRect(
            0,
            rect.y() + inset,
            SELECTION_BAR_WIDTH,
            max(2, rect.height() - 2 * inset),
        )
        if snap or not self._bar.isVisible():
            self._stop_slide()
            self._bar.setGeometry(geometry)
            self._bar.show()
            self._bar.raise_()
            return
        if geometry == self._bar.geometry():
            return
        self._start_slide(geometry)

    def eventFilter(self, watched, event):
        # Scrolling and viewport resizes move the row rects: snap, don't
        # animate — the bar must track the content, not travel to it.
        if event.type() in (QEvent.Type.Scroll, QEvent.Type.Resize):
            self._sync(snap=True)
        return False


def install_start_list_motion(start_tab) -> bool:
    """Slide the selection indicator of the start page's three list columns
    (选择窗口 / 截图方式 / 交互方式) instead of jumping it."""
    installed = 0
    for name in ("device_list", "capture_list", "interaction_list"):
        view = getattr(start_tab, name, None)
        if view is None or getattr(view, "_fluent_selection_installed", False):
            continue
        if not hasattr(view, "delegate") or not hasattr(view, "viewport"):
            logger.info(f"fluent motion: {name} is not a qfluentwidgets list, skip")
            continue
        try:
            _SlidingSelection(view)
        except Exception as exc:
            logger.info(f"fluent selection motion skipped on {name}: {exc!r}")
            continue
        view._fluent_selection_installed = True
        installed += 1
    if installed:
        logger.info(f"fluent selection motion installed on {installed} list(s)")
    return installed > 0


class _StaggerRun(QObject):
    """Store-style entrance for the board's rows: banner, cards, footer rise
    one after another (35 ms apart, capped) and fade in.

    Any resize of a member aborts the run and snaps everyone to full opacity
    at wherever the layout placed them — an expand or re-layout during the
    entrance must not end with cards parked at stale start positions."""

    def __init__(self, tab, widgets: list[QWidget]):
        super().__init__()
        self._done = False
        self._tab_ref = ref(tab)
        self._items: list[tuple] = []
        for index, widget in enumerate(widgets):
            item = self._make_item(widget, min(index, STAGGER_MAX_STEPS))
            if item is not None:
                self._items.append(item)
        if self._items:
            # A resize of the container (window resize, card list rebuild) or
            # of a member (expand, meta line appearing) means the layout has
            # new positions — the captured animation targets are stale.
            tab.installEventFilter(self)
        _STAGGER_RUNS.append(ref(self))

    def _make_item(self, widget: QWidget, step: int):
        try:
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            target = widget.pos()
            start = QPoint(target.x(), target.y() + STAGGER_OFFSET_PX)
            effect.setOpacity(0.0)
            widget.move(start.x(), start.y())
            delay_ms = step * STAGGER_STEP_MS

            def on_tick(
                progress,
                _widget=widget,
                _effect=effect,
                _start=start,
                _target=target,
                _delay=delay_ms,
            ):
                eased = _ENTRANCE_CURVE.valueForProgress(
                    _delayed_progress(progress, _delay, STAGGER_IN_MS)
                )
                y = round(_start.y() + (_target.y() - _start.y()) * eased)
                if _widget.pos().y() != y:
                    _widget.move(_start.x(), y)
                _effect.setOpacity(eased)

            item_holder: list = []

            def on_finished(_holder=item_holder):
                if _holder:
                    self._item_finished(_holder[0])

            driver = _RefreshDriver(widget, delay_ms + STAGGER_IN_MS, on_tick, on_finished)
            item = (widget, effect, driver)
            item_holder.append(item)
            widget.installEventFilter(self)
            driver.start()
            return item
        except RuntimeError:
            return None

    def eventFilter(self, watched, event):
        # Resize and LayoutRequest, never Move: the animations themselves
        # move the members, and both watched types mean the layout has (or is
        # about to re-)place fresh geometry — the captured animation targets
        # are stale.  LayoutRequest also catches move-only re-layouts (a
        # sibling shown/hidden or inserted, spacing changes) that resize
        # nothing.
        if not self._done and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.LayoutRequest,
        ):
            # Defer: aborting inside the layout pass that sent the event
            # would re-enter widget painting mid-layout.
            def deferred_abort():
                try:
                    self.abort()
                except RuntimeError:
                    # The run was already destroyed between the event and
                    # this timer; nothing left to abort.
                    pass

            QTimer.singleShot(0, deferred_abort)
        return False

    def _detach_filter(self) -> None:
        for owner in (self._tab_ref(), *(item[0] for item in self._items)):
            if owner is None:
                continue
            try:
                owner.removeEventFilter(self)
            except RuntimeError:
                continue

    def _item_finished(self, item) -> None:
        widget, _effect, driver = item
        try:
            widget.setGraphicsEffect(None)
            widget.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            driver.deleteLater()
        except RuntimeError:
            pass
        if self._done:
            return
        self._items = [existing for existing in self._items if existing[0] is not widget]
        if not self._items:
            self._retire()

    def _retire(self) -> None:
        self._done = True
        self._detach_filter()
        for index, run_ref in enumerate(_STAGGER_RUNS):
            if run_ref() is self:
                del _STAGGER_RUNS[index]
                break
        self.deleteLater()

    def abort(self, land: bool = False) -> None:
        """Stop the run.  ``land=True`` (kill switch) finishes every item at
        its captured target first — without a layout change those targets are
        the layout positions, so the board ends exactly where it started.
        ``land=False`` (layout-driven abort) only stops: the layout already
        placed fresh geometry and animating a final frame toward stale
        targets would fight it."""
        if self._done:
            return
        self._done = True
        self._detach_filter()
        for widget, _effect, driver in self._items:
            try:
                if land:
                    driver.finish()
                else:
                    driver.stop()
            except RuntimeError:
                pass
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            try:
                driver.deleteLater()
            except RuntimeError:
                pass
        self._items = []
        if land:
            # The layout is the authority: force one activation so a race
            # with a concurrent layout change still ends on layout positions.
            tab = self._tab_ref()
            layout = tab.layout() if tab is not None else None
            if layout is not None:
                try:
                    layout.invalidate()
                    layout.activate()
                except RuntimeError:
                    pass
        for index, run_ref in enumerate(_STAGGER_RUNS):
            if run_ref() is self:
                del _STAGGER_RUNS[index]
                break
        self.deleteLater()


def _stagger_widgets(tab) -> list[QWidget]:
    widgets: list[QWidget] = []
    banner = getattr(tab, "quest_banner", None)
    if banner is not None:
        widgets.append(banner)
    widgets.extend(getattr(tab, "card_widgets", None) or [])
    footer = getattr(tab, "quest_status_bar", None)
    if footer is not None:
        widgets.append(footer)
    return [widget for widget in widgets if widget is not None]


def _maybe_stagger(tab) -> None:
    if not _enabled or not tab.isVisible():
        return
    if bool(tab.property("_fluent_transitioned")):
        # A page transition just covered this tab's entrance; consume the
        # marker so the next first-show (if any) can stagger.
        tab.setProperty("_fluent_transitioned", False)
        return
    widgets = _stagger_widgets(tab)
    if widgets:
        _StaggerRun(tab, widgets)


class _FirstShowFilter(QObject):
    def __init__(self, tab: QWidget):
        super().__init__(tab)
        self._tab_ref = ref(tab)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            watched.removeEventFilter(self)
            tab = self._tab_ref()
            if tab is not None:

                def deferred():
                    try:
                        # Consume the pending flag whether or not the
                        # stagger actually plays (e.g. motion disabled).
                        tab.setProperty("_fluent_entrance_pending", False)
                        if tab.isVisible():
                            _maybe_stagger(tab)
                    except RuntimeError:
                        # The tab was destroyed before this timer fired.
                        pass

                QTimer.singleShot(0, deferred)
        return False


def _install_first_show_hook(tab) -> None:
    if getattr(tab, "_fluent_entrance_hooked", False):
        return
    tab._fluent_entrance_hooked = True
    # While pending, the page transition yields to this tab's one-shot
    # content stagger on its first appearance (see _on_current_changed).
    tab.setProperty("_fluent_entrance_pending", True)
    tab.installEventFilter(_FirstShowFilter(tab))


def install_fluent_tab_entrance() -> bool:
    """Stagger the 日常/周常 board's first show (banner → cards → footer)."""
    from ok.gui.tasks.OneTimeTaskTab import OneTimeTaskTab

    if getattr(OneTimeTaskTab, "_fluent_entrance_installed", False):
        return False
    from src.ui.quest_banner import DAILY_BOARD_GROUP

    original_init = OneTimeTaskTab.__init__

    def fluent_entrance_init(self, is_standalone=True, group_name=None):
        original_init(self, is_standalone=is_standalone, group_name=group_name)
        if group_name == DAILY_BOARD_GROUP:
            _install_first_show_hook(self)

    OneTimeTaskTab.__init__ = fluent_entrance_init
    OneTimeTaskTab._fluent_entrance_installed = True
    return True
