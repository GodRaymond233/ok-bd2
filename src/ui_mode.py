"""The Electron client runs its core through the windowless local service."""

import os

UI_MODE_ENV = "OK_BD2_UI"
UI_MODES = ("service",)


def ui_mode() -> str:
    value = os.environ.get(UI_MODE_ENV, "service").strip().lower()
    if value not in UI_MODES:
        raise ValueError(f"{UI_MODE_ENV} must be one of {', '.join(UI_MODES)}")
    return value
