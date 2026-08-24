"""Refresh-paced native expand animation (2026-08-22).

Why: qfluentwidgets drives ``expandAni`` — the animation of the card's inner
scroll-bar value that produces every visual effect through
``_onExpandValueChanged`` — from Qt's unified animation timer, a fixed ~16 ms
tick.  On 120/180 Hz displays that beat against the compositor (62 content
fps alternating between one and two vsync intervals), which reads as judder.
The 2026-08-19 snapshot-replay overlay solved the pacing by compositing two
pixmaps, but its handoff back to the real widgets could not be made atomic on
the Windows compositor (residual ghosts, thickened borders and a measured
one-tick band of pre-collapse pixels; retired 2026-08-22).

This module keeps the REAL widgets as the only presentation and re-times the
exact native chain: per tick it writes the intermediate bar value and calls
the card's own value-changed handler.  Timing comes from a precise timer at
the screen's refresh interval (120 Hz -> 8 ms), and the easing/duration are
read from ``expandAni`` itself, so per-frame content matches the native
animation exactly — only the clock differs.

Sole-writer discipline (2026-08-18 fix): every deferral gate keys on
``expandAni.state() == Running``.  While the driver runs, ``state`` is
shadowed on the animation instance to report ``Running``, so the quest
chrome and responsive layers defer exactly as with the native animation.

Opt-in: this re-timing is off unless ``OK_BD2_EXPAND_TIMING=1`` (or
``set_expand_timing_enabled(True)``) — real-display presentation forensics
have not yet shown it beats the native clock, so the untouched native
animation stays the default path.
"""

from __future__ import annotations

import os
import time
import weakref

from ok import Logger
from PySide6.QtCore import QAbstractAnimation, QElapsedTimer, Qt, QTimer
from PySide6.QtWidgets import QApplication

logger = Logger.get_logger(__name__)

_ENV_SWITCH = "OK_BD2_EXPAND_TIMING"
_MIN_INTERVAL_MS = 4
_MAX_INTERVAL_MS = 16
_FALLBACK_INTERVAL_MS = 8

# Diagnostics: when OK_BD2_EXPAND_TIMING_TRACE=1, log QPC timestamps of every
# _tick call and _onExpandValueChanged invocation to measure generated frame rate.
_TRACE_ENABLED = os.environ.get("OK_BD2_EXPAND_TIMING_TRACE") == "1"
_TRACE_LOG = []  # [(t_ns, event_type, card_id), ...]

_enabled = False
_previous_set_expand = None
# card -> driver; weak so a destroyed card drops out instead of leaking a
# zombie wrapper that would raise on the next global disable.
_DRIVERS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _env_enables() -> bool:
    value = os.environ.get(_ENV_SWITCH, "").strip().lower()
    return value in {"1", "true", "on", "yes"}


def set_expand_timing_enabled(flag: bool) -> None:
    """Runtime kill switch; disabling also finishes any drive in flight."""
    global _enabled
    _enabled = bool(flag)
    if not _enabled:
        for card in list(_DRIVERS):
            driver = _DRIVERS.get(card)
            if driver is not None:
                driver.finish()


def expand_timing_enabled() -> bool:
    return _enabled


def dump_trace_log() -> list:
    """Return collected trace log and clear it. Format: [(t_ns, event, card_id), ...]"""
    global _TRACE_LOG
    result = list(_TRACE_LOG)
    _TRACE_LOG.clear()
    return result


def get_driver_trace() -> list[dict]:
    """Get detailed trace with timing and geometry from all active drivers.

    Returns list of dicts with keys: t_mono_ns, event, card_id, progress, height, bar_value
    """
    global _TRACE_LOG
    result = list(_TRACE_LOG)
    return result


def clear_driver_trace() -> None:
    """Clear the trace log without returning it."""
    global _TRACE_LOG
    _TRACE_LOG.clear()


def _suppressed_start(*_args, **_kwargs):
    """Instance-level shadow for ``expandAni.start`` inside the wrapper."""


class _ExpandTimingDriver:
    """Drives one expand/collapse through the native value chain."""

    def __init__(self, card):
        self._card = card
        self._bar = card.verticalScrollBar()
        self._ani = card.expandAni
        self._start = int(self._ani.startValue())
        self._end = int(self._ani.endValue())
        self._duration_ms = max(1, int(self._ani.duration()))
        self._easing = self._ani.easingCurve()
        self._done = False
        self._geometry_key = None
        self._timer = QTimer(card)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()

    @property
    def running(self) -> bool:
        return not self._done

    def start(self) -> None:
        # The sole-writer gates call ani.state() from Python; an instance
        # attribute shadows the bound method for exactly the drive's duration.
        self._ani.state = lambda: QAbstractAnimation.Running
        self._geometry_key = self._capture_geometry_key()
        self._elapsed.start()
        self._timer.start(_refresh_interval_ms(self._card))

    def _capture_geometry_key(self):
        # Cheap O(1) probe for the two inputs that invalidate the cached bar
        # endpoints: the view width (resize/DPI) and the spacer height, which
        # only changes when _adjustViewSize re-measures after a content edit.
        try:
            return (self._card.view.width(), self._card.spaceWidget.height())
        except (RuntimeError, AttributeError):
            return None

    def _apply(self, progress: float) -> None:
        eased = self._easing.valueForProgress(progress)
        value = self._start + (self._end - self._start) * eased
        self._bar.setValue(int(round(value)))
        handler = getattr(self._card, "_onExpandValueChanged", None)
        if callable(handler):
            if _TRACE_ENABLED:
                try:
                    # Record the TaskCard's outer height, not the inner card
                    height = self._card.height() if hasattr(self._card, "height") else 0
                    _TRACE_LOG.append({
                        "t_mono_ns": time.monotonic_ns(),
                        "event": "handler",
                        "card_id": id(self._card),
                        "progress": progress,
                        "height": height,
                        "bar_value": int(round(value)),
                    })
                except (RuntimeError, AttributeError):
                    pass
            handler()

    def _tick(self) -> None:
        if self._done:
            return

        key = self._capture_geometry_key()
        if (
            key is not None
            and self._geometry_key is not None
            and key != self._geometry_key
        ):
            self._abort_on_geometry_change()
            return

        elapsed_ms = self._elapsed.elapsed()
        progress = min(1.0, elapsed_ms / self._duration_ms) if self._duration_ms > 0 else 0

        if _TRACE_ENABLED:
            try:
                # Record the TaskCard's outer height, not the inner card
                height = self._card.height() if hasattr(self._card, "height") else 0
                bar_value = self._bar.value()
                _TRACE_LOG.append({
                    "t_mono_ns": time.monotonic_ns(),
                    "event": "tick",
                    "card_id": id(self._card),
                    "progress": progress,
                    "height": height,
                    "bar_value": bar_value,
                    "elapsed_ms": elapsed_ms,
                })
            except (RuntimeError, AttributeError):
                pass

        try:
            self._apply(progress)
        except RuntimeError:
            # The card was destroyed mid-drive; the timer dies with it.
            self._done = True
            return
        if progress >= 1.0:
            self.finish()

    def finish(self) -> None:
        """Snap to the exact terminal state and clear every shadow."""
        if self._done:
            return
        self._done = True
        self._timer.stop()
        try:
            self._unshadow()
            self._apply(1.0)
            _apply_terminal_chrome(self._card)
        except RuntimeError:
            pass
        finally:
            self._unshadow()
            try:
                _DRIVERS.pop(self._card, None)
            except TypeError:
                pass

    def _abort_on_geometry_change(self) -> None:
        """Width or content height changed mid-drive (resize, DPI move, a
        config row appearing): the cached bar endpoints are stale, so stop
        driving and land on the terminal state recomputed from the live
        geometry instead of animating toward the old target."""
        self._done = True
        self._timer.stop()
        self._unshadow()
        try:
            if getattr(self._card, "isExpand", False):
                # Expand terminal (bar 0) is geometry-independent; the value
                # handler re-measures the content height on the way in.
                self._apply(1.0)
            else:
                from src.ui.quest_cards import _content_height

                self._bar.setValue(max(0, _content_height(self._card)))
                handler = getattr(self._card, "_onExpandValueChanged", None)
                if callable(handler):
                    handler()
            _apply_terminal_chrome(self._card)
        except RuntimeError:
            pass
        finally:
            self._unshadow()
            try:
                _DRIVERS.pop(self._card, None)
            except TypeError:
                pass

    def cancel_without_snap(self) -> None:
        """Stop driving but leave the height where it is — a reversal takes
        over from the current visual value, like the native path."""
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self._unshadow()
        try:
            _DRIVERS.pop(self._card, None)
        except TypeError:
            pass

    def _unshadow(self) -> None:
        try:
            del self._ani.state
        except (AttributeError, TypeError):
            pass


def _apply_terminal_chrome(card) -> None:
    if not hasattr(card, "_quest_meta"):
        return
    try:
        from src.ui.quest_cards import apply_quest_chrome

        apply_quest_chrome(card)
    except Exception as exc:  # chrome must never break the toggle semantics
        logger.info(f"expand timing terminal chrome skipped: {exc!r}")


def _refresh_interval_ms(card) -> int:
    rate = 0.0
    try:
        window = card.window()
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


def _timed_set_expand(card, is_expand, previous) -> None:
    active = _DRIVERS.get(card)
    if active is not None:
        active.cancel_without_snap()

    ani = card.expandAni
    ani.stop()
    was_expand = getattr(card, "isExpand", None)
    original_start = ani.start
    ani.start = _suppressed_start
    try:
        previous(card, is_expand)
    finally:
        try:
            del ani.start
        except (AttributeError, TypeError):
            ani.start = original_start

    if getattr(card, "isExpand", None) == was_expand:
        # The chain's own guards (e.g. empty-config cards) declined the
        # toggle; semantics already match the native early return.
        return

    driver = _ExpandTimingDriver(card)
    _DRIVERS[card] = driver
    driver.start()


def _set_expand_entry(card, is_expand) -> None:
    previous = _previous_set_expand
    if previous is None:
        return
    if not expand_timing_enabled():
        previous(card, is_expand)
        return
    try:
        _timed_set_expand(card, is_expand, previous)
    except Exception as exc:
        # The native path must survive any timing failure untouched.
        logger.warning(f"expand timing fell back to native animation: {exc!r}")
        driver = _DRIVERS.get(card)
        if driver is not None:
            driver.cancel_without_snap()
        ani = getattr(card, "expandAni", None)
        if ani is not None and getattr(card, "isExpand", None) == is_expand:
            ani.start()


def install_expand_timing() -> bool:
    """Wrap ``ConfigCard.setExpand`` (outermost) with the refresh-paced drive.

    Contract-probed and idempotent; when the probe fails the native
    animation stays exactly as it is.
    """
    global _enabled, _previous_set_expand
    from ok.gui.tasks.ConfigCard import ConfigCard

    if getattr(ConfigCard, "_expand_timing_installed", False):
        return False
    if not all(
        hasattr(ConfigCard, name)
        for name in ("setExpand", "_onExpandValueChanged", "toggleExpand", "verticalScrollBar")
    ):
        logger.info("expand timing not installed: upstream contract probe failed")
        return False
    _enabled = _env_enables()
    _previous_set_expand = ConfigCard.setExpand

    def timing_set_expand(self, is_expand):
        _set_expand_entry(self, is_expand)

    ConfigCard.setExpand = timing_set_expand
    ConfigCard._expand_timing_installed = True
    logger.info(f"expand timing installed: enabled={_enabled}")
    return True
