"""Local developer-tool profile helpers.

The production entry point never imports this module.  ``main_debug.py`` uses
it to derive the next development version from the latest GitHub Release and
to apply an exact developer-build window title.
"""

from __future__ import annotations

import json
import re
import subprocess
from urllib.request import Request, urlopen

from src.globals import Globals

LATEST_RELEASE_URL = (
    "https://api.github.com/repos/GodRaymond233/ok-bd2/releases/latest"
)
RELEASE_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def release_version(tag: str) -> tuple[int, int, int]:
    """Parse a strict release tag such as ``v0.1.21``."""

    match = RELEASE_VERSION_PATTERN.fullmatch(str(tag).strip())
    if match is None:
        raise ValueError(f"Unsupported release tag: {tag!r}")
    return tuple(int(part) for part in match.groups())


def next_development_version(tag: str) -> str:
    """Increment only the patch component of a release tag."""

    major, minor, patch = release_version(tag)
    return f"{major}.{minor}.{patch + 1}"


def fetch_latest_release_tag(*, timeout: float = 5.0, opener=None) -> str:
    """Read the latest published Release tag from GitHub."""

    request = Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ok-bd2-local-developer-tool",
        },
    )
    open_request = opener or urlopen
    with open_request(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tag = payload.get("tag_name")
    release_version(tag)
    return tag


def latest_local_release_tag() -> str | None:
    """Return the highest local release tag as an offline development fallback."""

    try:
        result = subprocess.run(
            ["git", "tag", "--list", "v*"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    versions = []
    for raw_tag in result.stdout.splitlines():
        tag = raw_tag.strip()
        try:
            versions.append((release_version(tag), tag))
        except ValueError:
            continue
    return max(versions)[1] if versions else None


def resolve_latest_release_tag() -> str:
    """Prefer the live GitHub Release and fail closed only if no tag is available."""

    try:
        return fetch_latest_release_tag()
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        local_tag = latest_local_release_tag()
        if local_tag is not None:
            return local_tag
        raise RuntimeError(
            "无法读取 GitHub 最新 Release，且本地没有可用的正式版本标签。"
        ) from None


def configure_debug_profile(config: dict, *, release_tag: str | None = None) -> str:
    """Configure the exact next-release title used only by ``main_debug.py``."""

    current_release = release_tag or resolve_latest_release_tag()
    development_version = next_development_version(current_release)
    title = f"{config.get('gui_title', 'ok-bd2')} {development_version} 开发版"
    config.update(
        {
            "debug": True,
            "version": development_version,
            "debug_window_title": title,
            "my_app": ["src.debug_profile", "DebugGlobals"],
        }
    )
    return development_version


class DebugGlobals(Globals):
    """Apply the developer title after ok-script constructs its main window."""

    def on_show_main_window(self, main_window):
        super().on_show_main_window(main_window)

        from ok import og

        if title := og.config.get("debug_window_title"):
            main_window.setWindowTitle(title)
