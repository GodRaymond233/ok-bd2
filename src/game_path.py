from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

DEFAULT_GAME_EXE = "BrownDust II.exe"
DEFAULT_INSTALL_DIR = Path(r"D:\Neowiz\Browndust2\Browndust2_10000001")
ENV_GAME_EXE = "OK_BD2_GAME_EXE"
ENV_GAME_PATH = "OK_BD2_GAME_PATH"
ENV_INSTALL_DIR = "OK_BD2_INSTALL_DIR"

REGISTRY_MATCH_TOKENS = ("browndust", "brown dust", "neowiz")
REGISTRY_VALUE_NAMES = (
    "DisplayIcon",
    "InstallLocation",
    "InstallSource",
    "UninstallString",
    "QuietUninstallString",
)


def get_game_exe_names(env: dict[str, str] | None = None) -> list[str]:
    value = _env(env, ENV_GAME_EXE, DEFAULT_GAME_EXE)
    names = [_clean_path_part(part) for part in value.split(",")]
    names = [name for name in names if name]
    return names or [DEFAULT_GAME_EXE]


def get_game_exe_config_value(env: dict[str, str] | None = None) -> str | list[str]:
    names = get_game_exe_names(env)
    return names[0] if len(names) == 1 else names


def get_configured_install_dir(env: dict[str, str] | None = None) -> str:
    return _env(env, ENV_INSTALL_DIR, str(DEFAULT_INSTALL_DIR))


def get_configured_game_path(env: dict[str, str] | None = None) -> str:
    configured = _env(env, ENV_GAME_PATH, "")
    if configured:
        return configured
    return str(Path(get_configured_install_dir(env)) / get_game_exe_names(env)[0])


def find_running_game_path(env: dict[str, str] | None = None) -> str:
    exe_names = {name.lower() for name in get_game_exe_names(env)}
    try:
        import psutil
    except Exception:
        return ""

    for proc in psutil.process_iter(["name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            exe_path = proc.info.get("exe") or ""
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

        if name not in exe_names and Path(exe_path).name.lower() not in exe_names:
            continue
        path = Path(exe_path)
        if path.is_file():
            return str(path)
    return ""


def resolve_game_exe_path(
    running_path: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    exe_names = get_game_exe_names(env)
    explicit_game_path = _env(env, ENV_GAME_PATH, "")
    if explicit_game_path:
        env_game_path = _existing_game_path([explicit_game_path], exe_names)
        if env_game_path:
            return env_game_path

    running_game_path = find_running_game_path(env)
    if running_game_path:
        return running_game_path

    candidates: list[Path] = []
    if running_path:
        candidates.extend(_candidate_paths_from_source(running_path, exe_names))

    candidates.append(Path(get_configured_game_path(env)))
    candidates.extend(_common_install_candidates(env, exe_names))
    candidates.extend(_registry_candidate_paths(exe_names))

    return _existing_game_path(candidates, exe_names)


def calculate_pc_exe_path(running_path: str | os.PathLike[str] | None) -> str:
    return resolve_game_exe_path(running_path) or str(running_path or "")


def seed_device_manager_game_path(device_manager, env: dict[str, str] | None = None) -> str:
    path = resolve_game_exe_path(env=env)
    if path and getattr(device_manager, "config", None) is not None:
        if device_manager.config.get("pc_full_path") != path:
            device_manager.config["pc_full_path"] = path
    return path


def _env(env: dict[str, str] | None, key: str, default: str) -> str:
    return (env if env is not None else os.environ).get(key, default).strip()


def _clean_path_part(value: object) -> str:
    text = str(value or "").strip().strip("\"'")
    if text.lower().endswith(".exe,0"):
        text = text[:-2]
    return text.strip()


def _existing_game_path(candidates: Iterable[Path | str], exe_names: Iterable[str]) -> str:
    allowed_names = {name.lower() for name in exe_names}
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.name.lower() in allowed_names and path.is_file():
            return str(path)
    return ""


def _candidate_paths_from_source(
    source: str | os.PathLike[str],
    exe_names: Iterable[str],
) -> list[Path]:
    path = Path(_clean_path_part(source)).expanduser()
    candidates: list[Path] = []
    if path.suffix.lower() == ".exe":
        candidates.append(path)
        roots = [path.parent, *list(path.parents)[:5]]
    else:
        roots = [path, *list(path.parents)[:5]]

    for root in roots:
        candidates.extend(_candidate_paths_from_root(root, exe_names))
    return candidates


def _candidate_paths_from_root(root: Path, exe_names: Iterable[str]) -> list[Path]:
    candidates: list[Path] = []
    for name in exe_names:
        candidates.append(root / name)
        candidates.append(root / "Browndust2_10000001" / name)
    return candidates


def _common_install_candidates(
    env: dict[str, str] | None,
    exe_names: Iterable[str],
) -> list[Path]:
    roots = [
        Path(get_configured_install_dir(env)),
        DEFAULT_INSTALL_DIR,
    ]

    for drive in _windows_drive_roots():
        roots.append(drive / "Neowiz" / "Browndust2" / "Browndust2_10000001")

    candidates: list[Path] = []
    for root in roots:
        candidates.extend(_candidate_paths_from_root(root, exe_names))
    return candidates


def _windows_drive_roots() -> list[Path]:
    roots: list[Path] = []
    if os.name != "nt":
        return roots

    for letter in "CDEFG":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(drive)
    return roots


def _registry_candidate_paths(exe_names: Iterable[str]) -> list[Path]:
    candidates: list[Path] = []
    for raw_value in _registry_install_values():
        extracted = _extract_registry_path(raw_value)
        if extracted:
            candidates.extend(_candidate_paths_from_source(extracted, exe_names))
    return candidates


def _registry_install_values() -> Iterable[str]:
    if os.name != "nt":
        return []

    try:
        import winreg
    except Exception:
        return []

    hives = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    subkeys = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",)
    views = (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)

    values: list[str] = []
    for hive in hives:
        for subkey in subkeys:
            for view in views:
                try:
                    with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as root_key:
                        count = winreg.QueryInfoKey(root_key)[0]
                        for index in range(count):
                            try:
                                app_key_name = winreg.EnumKey(root_key, index)
                                app_values = _read_registry_app_values(
                                    winreg,
                                    root_key,
                                    app_key_name,
                                )
                            except OSError:
                                continue

                            searchable = " ".join(app_values.values()).lower()
                            if not any(token in searchable for token in REGISTRY_MATCH_TOKENS):
                                continue
                            values.extend(app_values.get(name, "") for name in REGISTRY_VALUE_NAMES)
                except OSError:
                    continue
    return [value for value in values if value]


def _read_registry_app_values(winreg, root_key, app_key_name: str) -> dict[str, str]:
    with winreg.OpenKey(root_key, app_key_name) as app_key:
        values: dict[str, str] = {}
        for value_name in ("DisplayName", *REGISTRY_VALUE_NAMES):
            try:
                value, _ = winreg.QueryValueEx(app_key, value_name)
            except OSError:
                continue
            values[value_name] = str(value)
        return values


def _extract_registry_path(value: str) -> str:
    text = _clean_path_part(value)
    if not text:
        return ""

    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return text[1:end]

    lower_text = text.lower()
    exe_index = lower_text.find(".exe")
    if exe_index >= 0:
        return text[: exe_index + 4].strip()
    return text
