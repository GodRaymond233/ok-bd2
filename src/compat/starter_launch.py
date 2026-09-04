from __future__ import annotations

import importlib
import ntpath
from functools import wraps

from src.game_path import get_launch_game_id, get_launcher_exe_names

_PATCH_MARKER = "_ok_bd2_starter_uri_enabled"


def starter_launch_uri() -> str:
    """The URI used by the official Brown Dust 2 desktop shortcut for the installed build."""
    return f"browndust2:games/{get_launch_game_id()}?usn=0"


def starter_launch_arguments(game_cmd: object, arguments: str | None = None) -> str | None:
    """Add the URI used by the official Brown Dust 2 desktop shortcut."""
    command_path = str(game_cmd or "").strip().strip('"')
    launcher_names = {name.casefold() for name in get_launcher_exe_names()}
    if ntpath.basename(command_path).casefold() not in launcher_names:
        return arguments

    launch_uri = f'"{starter_launch_uri()}"'
    return f"{launch_uri} {arguments}" if arguments else launch_uri


def enable_starter_launch_uri() -> None:
    """Teach ok-script to pass the required URI to the Neowiz Starter."""
    start_controller = importlib.import_module("ok.core.start_controller")
    current_execute = start_controller.execute
    if getattr(current_execute, _PATCH_MARKER, False):
        return

    start_controller.execute = wrap_starter_execute(current_execute)


def wrap_starter_execute(current_execute):
    """Wrap ok-script's execute function without changing non-Starter launches."""

    @wraps(current_execute)
    def execute_with_starter_uri(game_cmd, arguments=None, start_method="start"):
        return current_execute(
            game_cmd,
            arguments=starter_launch_arguments(game_cmd, arguments),
            start_method=start_method,
        )

    setattr(execute_with_starter_uri, _PATCH_MARKER, True)
    return execute_with_starter_uri
