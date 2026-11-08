"""Launcher version checks shared by the desktop update status."""

from __future__ import annotations

# pyappify.get_version_list requires launcher PYAPPIFY_VERSION >= 1.2.2.
MIN_CHECK_UPDATES_LAUNCHER_VERSION = (1, 2, 2)


def parse_launcher_version(version) -> tuple[int, ...] | None:
    if not isinstance(version, str):
        return None
    parts = version.lstrip("v").split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def launcher_supports_update_check(version) -> bool | None:
    """Return None when the launcher version is unavailable or unparseable."""
    parsed = parse_launcher_version(version)
    if parsed is None:
        return None
    return parsed >= MIN_CHECK_UPDATES_LAUNCHER_VERSION
