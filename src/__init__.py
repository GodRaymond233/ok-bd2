import os

from src.game_path import (
    get_configured_game_path,
    get_configured_install_dir,
    get_game_exe_config_value,
)


def _ensure_system32_in_path() -> None:
    system32 = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "System32")
    path_value = os.environ.get("PATH", "")
    entries = [entry for entry in path_value.split(os.pathsep) if entry]
    norm_system32 = os.path.normcase(os.path.normpath(system32))
    has_system32 = any(
        os.path.normcase(os.path.normpath(entry)) == norm_system32 for entry in entries
    )
    if os.path.isdir(system32) and not has_system32:
        os.environ["PATH"] = path_value + (os.pathsep if path_value else "") + system32


def _exe_from_env(name: str, default: str) -> str | list[str]:
    value = os.environ.get(name, default).strip()
    if "," not in value:
        return value
    return [part.strip() for part in value.split(",") if part.strip()]


_ensure_system32_in_path()

GAME_NAME = "BD2"
GAME_INSTALL_DIR = get_configured_install_dir()
GAME_EXE = get_game_exe_config_value()
GAME_PATH = get_configured_game_path()
LAUNCHER_EXE = _exe_from_env("OK_BD2_LAUNCHER_EXE", "BrownDust2Launcher.exe")
HWND_CLASS = os.environ.get("OK_BD2_HWND_CLASS", "UnityWndClass")
LAUNCHER_HWND_CLASS = os.environ.get("OK_BD2_LAUNCHER_HWND_CLASS", "")

text_white_color = {
    "r": (244, 255),
    "g": (244, 255),
    "b": (244, 255),
}

text_black_color = {
    "r": (0, 50),
    "g": (0, 50),
    "b": (0, 50),
}
