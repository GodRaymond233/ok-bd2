from __future__ import annotations

from functools import wraps
from html import escape

_PATCH_MARKER = "_ok_bd2_starter_wait_ui"


def show_starter_wait_status(window, message, cancel_event):
    if cancel_event.is_set():
        return
    dialog = window.emulator_starting_dialog
    if dialog is None:
        return
    from qfluentwidgets import PushButton

    dialog._bd2_cancel_event = cancel_event
    if not hasattr(dialog, "_bd2_cancel_button"):
        button = PushButton("取消等待", dialog.widget)
        button.clicked.connect(lambda: dialog._bd2_cancel_event.set())
        dialog.widget.layout().addWidget(button)
        dialog._bd2_cancel_button = button
    dialog._bd2_cancel_button.setVisible(True)
    if message:
        dialog.restart_countdown(0)
        dialog.loading_label.setWordWrap(True)
        width = max(160, min(650, dialog.width() - 200))
        dialog.loading_label.setFixedWidth(width)
        dialog.loading_label.setText(escape(message).replace("\n", "<br>"))
        dialog.loading_label.setMinimumHeight(dialog.loading_label.heightForWidth(width))


def install_starter_wait_ui():
    from ok.ui.qt.MainWindow import MainWindow

    from src.compat.starter_guard import starter_wait_status

    if getattr(MainWindow, _PATCH_MARKER, False):
        return
    original_init = MainWindow.__init__
    original_starting = MainWindow.starting_emulator

    @wraps(original_init)
    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        def status(message, cancel_event):
            show_starter_wait_status(self, message, cancel_event)

        starter_wait_status.connect(status)
        self.destroyed.connect(lambda: starter_wait_status.disconnect(status))

    @wraps(original_starting)
    def starting(self, done, error, seconds_left):
        dialog = self.emulator_starting_dialog
        if dialog is not None and (done or seconds_left > 0):
            dialog.loading_label.setMinimumSize(0, 0)
            dialog.loading_label.setMaximumWidth(16777215)
            dialog.loading_label.setWordWrap(False)
        original_starting(self, done, error, seconds_left)
        dialog = self.emulator_starting_dialog
        if dialog is not None and hasattr(dialog, "_bd2_cancel_button"):
            dialog._bd2_cancel_button.setVisible(False)

    MainWindow.__init__ = init
    MainWindow.starting_emulator = starting
    setattr(MainWindow, _PATCH_MARKER, True)
