"""Leave ok-bd2's shortcuts to the PyAppify launcher.

Since 3.0.5, ok-script asks pyappify to create the app's Start Menu and Desktop
shortcuts whenever it starts without its Qt window, and announces each one it writes.
For ok-bd2 that is the explicitly headless run (``main.py -h``). The launcher already
writes ok-bd2's shortcuts each time it starts the app, and a background run should
not add a Desktop shortcut or show a notification, so ok-script's step is skipped.
The desktop client does not show the two new shortcut settings either
(``src.desktop.settings_text``).
"""

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)


def _skip_app_shortcut(basic_config=None):
    logger.info("skip creating shortcuts: the PyAppify launcher writes ok-bd2's shortcuts")
    return None


def disable_framework_shortcuts() -> None:
    import ok.util.shortcut as shortcut

    # ``OK.start`` imports the function when it runs, so replacing it here is enough.
    shortcut.create_app_shortcut = _skip_app_shortcut
