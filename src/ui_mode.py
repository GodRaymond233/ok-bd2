"""Select the presentation adapter before importing application configuration.

``qt`` keeps the existing ok-script Qt window and stays the default.  ``service``
runs the automation core behind the loopback API used by the desktop shell and
must not import any Qt module.
"""

import os

UI_MODE_ENV = "OK_BD2_UI"
UI_MODES = ("qt", "service")


def ui_mode() -> str:
    value = os.environ.get(UI_MODE_ENV, "qt").strip().lower()
    if value not in UI_MODES:
        raise ValueError(f"{UI_MODE_ENV} must be one of {', '.join(UI_MODES)}")
    return value


def is_qt_mode() -> bool:
    return ui_mode() == "qt"
