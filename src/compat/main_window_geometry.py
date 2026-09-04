from __future__ import annotations

MAIN_WINDOW_GEOMETRY_DEBOUNCE_MS = 500
_PATCH_MARKER = "_ok_bd2_geometry_debounce_enabled"


def patch_main_window_geometry_events(
    main_window_class: type,
    qtimer: type,
) -> None:
    """Save top-level geometry once after a burst of move/resize events."""

    if getattr(main_window_class, _PATCH_MARKER, False):
        return

    original_move_event = main_window_class.moveEvent
    original_resize_event = main_window_class.resizeEvent
    original_close_event = main_window_class.closeEvent

    def schedule_geometry_save(self) -> None:
        timer = getattr(self, "_ok_bd2_geometry_timer", None)
        if timer is None:
            timer = qtimer(self)
            timer.setSingleShot(True)
            timer.setInterval(MAIN_WINDOW_GEOMETRY_DEBOUNCE_MS)
            timer.timeout.connect(self.update_ok_config)
            self._ok_bd2_geometry_timer = timer
        timer.start()

    def flush_geometry_save(self) -> None:
        timer = getattr(self, "_ok_bd2_geometry_timer", None)
        if timer is None or not timer.isActive():
            return
        timer.stop()
        self.update_ok_config()

    def stable_move_event(self, event):
        result = original_move_event(self, event)
        schedule_geometry_save(self)
        return result

    def stable_resize_event(self, event):
        result = original_resize_event(self, event)
        schedule_geometry_save(self)
        return result

    def stable_close_event(self, event):
        flush_geometry_save(self)
        return original_close_event(self, event)

    main_window_class.moveEvent = stable_move_event
    main_window_class.resizeEvent = stable_resize_event
    main_window_class.closeEvent = stable_close_event
    setattr(main_window_class, _PATCH_MARKER, True)


def install_main_window_geometry_debounce() -> None:
    from ok.ui.qt.MainWindow import MainWindow
    from PySide6.QtCore import QTimer

    patch_main_window_geometry_events(MainWindow, QTimer)
