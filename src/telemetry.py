"""Lightweight, anonymous version & hot-update telemetry for installed ok-bd2 clients.

Design goals:
- Strictly anonymous: only a locally generated random UUIDv4 plus version/channel metadata.
  No hardware IDs, IP addresses, paths, usernames, or game account data are collected.
- Non-blocking & silent: runs once per startup in a daemon thread with a short timeout;
  any network or I/O error is silently ignored and never impacts automation or UI.
- Opt-out friendly: controlled by a visible toggle in Basic Options (and env var override).
- Source/dev checkouts never report unless ``OK_BD2_TELEMETRY_FORCE=1`` is explicitly set.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import re
import sys
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.desktop.backups import MARKER as CONFIG_VERSION_MARKER
from src.desktop.backups import installed_base, normalized_version
from src.desktop.bootstrap import REQUIRED_SHELL_VERSION

TELEMETRY_OPTION = "匿名版本与活跃统计"
TELEMETRY_OPTION_DESCRIPTION = (
    "仅在启动时发送匿名版本与更新方式（不含账号、路径或硬件信息），用于统计各版本活跃与热更新情况"
)

# 部署 telemetry-worker 并绑定自定义域名后，填入完整上报地址，例如：
# "https://stats.your-domain.com/v1/ping"
# 留空时客户端不会发出任何网络请求；也可通过 OK_BD2_TELEMETRY_URL 环境变量临时指定。
TELEMETRY_ENDPOINT = "https://ok-bd2-telemetry.ok-bd2-telemetry-worker.workers.dev/v1/ping"

TELEMETRY_URL_ENV = "OK_BD2_TELEMETRY_URL"
TELEMETRY_DISABLED_ENV = "OK_BD2_TELEMETRY_DISABLED"
TELEMETRY_FORCE_ENV = "OK_BD2_TELEMETRY_FORCE"
TELEMETRY_TIMEOUT_SECONDS = 3.0

_BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-].*)?$")


@dataclass(frozen=True)
class TelemetryContext:
    working_dir: Path
    config_folder: str
    had_existing_configs: bool
    legacy_version: str


def _parse_version_tuple(version: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.match(normalized_version(version))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def beijing_today(now: datetime.datetime | None = None) -> str:
    current = now or datetime.datetime.now(datetime.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=datetime.timezone.utc)
    return current.astimezone(_BEIJING_TZ).strftime("%Y-%m-%d")


def default_state_path(env: dict[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    local_app_data = environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ok-bd2" / "telemetry.json"
    return Path.home() / ".local" / "share" / "ok-bd2" / "telemetry.json"


def resolve_endpoint(env: dict[str, str] | None = None) -> str:
    environ = os.environ if env is None else env
    override = environ.get(TELEMETRY_URL_ENV, "").strip()
    return override or TELEMETRY_ENDPOINT.strip()


def snapshot_telemetry_context(
    config: dict,
    *,
    working_dir: Path | None = None,
) -> TelemetryContext:
    """Capture pre-startup config state before ``prepare_installed_configs`` writes markers."""
    working = (working_dir or Path.cwd()).resolve()
    config_folder = str(config.get("config_folder", "configs") or "configs")
    configs_dir = working / config_folder
    had_existing = False
    legacy_version = ""
    try:
        if configs_dir.is_dir():
            marker = configs_dir / CONFIG_VERSION_MARKER
            if marker.is_file():
                raw = normalized_version(marker.read_text(encoding="utf-8"))
                if _parse_version_tuple(raw) is not None:
                    legacy_version = raw
                had_existing = True
            else:
                had_existing = any(
                    entry.is_file() and entry.suffix.lower() == ".json"
                    for entry in configs_dir.iterdir()
                )
    except OSError:
        pass
    return TelemetryContext(
        working_dir=working,
        config_folder=config_folder,
        had_existing_configs=had_existing,
        legacy_version=legacy_version,
    )


def is_telemetry_enabled(
    context: TelemetryContext,
    *,
    env: dict[str, str] | None = None,
) -> bool:
    environ = os.environ if env is None else env
    disabled = environ.get(TELEMETRY_DISABLED_ENV, "").strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    basic_options_path = context.working_dir / context.config_folder / "Basic Options.json"
    try:
        if basic_options_path.is_file():
            data = json.loads(basic_options_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and TELEMETRY_OPTION in data:
                return bool(data[TELEMETRY_OPTION])
    except (OSError, ValueError):
        pass
    return True


def detect_channel(working_dir: Path) -> str:
    """Infer update channel ('china' | 'global' | 'unknown') from PyAppify app.json."""
    base = installed_base(working_dir)
    candidates = [working_dir / "app.json"]
    if base is not None:
        candidates.extend(
            [base / "app.json", base.parent / "app.json", base.parent.parent / "app.json"]
        )
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="ignore").lower()
            if "cnb.cool" in text:
                return "china"
            if "github.com" in text:
                return "global"
        except OSError:
            continue
    return "unknown"


def detect_launcher_sig(working_dir: Path) -> str:
    """Return size:mtime_ns of the outer PyAppify launcher EXE to distinguish installer runs."""
    base = installed_base(working_dir)
    if base is None:
        return ""
    # Layout: <install_root>/data/apps/ok-bd2/working -> base is .../data/apps/ok-bd2
    install_root = base.parent.parent.parent
    for name in ("ok-bd2.exe", "ok-bd2-launcher.exe"):
        launcher = install_root / name
        try:
            if launcher.is_file():
                stat = launcher.stat()
                return f"{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            continue
    return ""


def detect_os_label() -> str:
    if os.name != "nt":
        return "unknown"
    try:
        build = sys.getwindowsversion().build  # type: ignore[attr-defined]
        if build >= 22000:
            return "win11"
        if build >= 10240:
            return "win10"
        return "win"
    except Exception:
        release = platform.release()
        if release == "11":
            return "win11"
        if release == "10":
            return "win10"
        return "win"


def load_state(state_path: Path) -> dict | None:
    try:
        if not state_path.is_file():
            return None
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        cid = data.get("cid")
        if not isinstance(cid, str) or not _UUID_V4_RE.match(cid):
            return None
        return data
    except (OSError, ValueError):
        return None


def save_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = state_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(state_path)
    except OSError:
        pass


def build_telemetry_ping(
    state: dict | None,
    *,
    current_version: str,
    today: str,
    channel: str,
    launcher_sig: str,
    had_existing_configs: bool,
    legacy_version: str = "",
    shell_version: str = REQUIRED_SHELL_VERSION,
    os_label: str = "win",
    uuid_factory: Callable[[], str] | None = None,
) -> tuple[dict | None, dict]:
    """Compute at most one telemetry ping payload and the updated local state.

    Returns ``(payload_or_None, next_state)``. Caller should persist ``next_state``
    only after the ping succeeds (or immediately when ``payload is None``).
    """
    ver = normalized_version(current_version)
    if _parse_version_tuple(ver) is None:
        return None, dict(state or {})

    make_uuid = uuid_factory or (lambda: str(uuid.uuid4()))

    if state is None:
        cid = make_uuid().lower()
        via = "legacy_migration" if had_existing_configs else "new_install"
        prev_ver = (
            normalized_version(legacy_version)
            if (had_existing_configs and legacy_version)
            else ""
        )
        if prev_ver and _parse_version_tuple(prev_ver) is None:
            prev_ver = ""
        payload = {
            "cid": cid,
            "event": "install",
            "ver": ver,
            "prev_ver": prev_ver,
            "upgrade_via": via,
            "channel": channel,
            "shell_ver": shell_version,
            "os": os_label,
        }
        next_state = {
            "cid": cid,
            "first_seen_at": today,
            "last_version": ver,
            "last_active_date": today,
            "last_launcher_sig": launcher_sig,
        }
        return payload, next_state

    cid = state["cid"].lower()
    last_ver = normalized_version(str(state.get("last_version") or ""))
    last_active_date = str(state.get("last_active_date") or "")
    last_launcher_sig = str(state.get("last_launcher_sig") or "")
    first_seen_at = str(state.get("first_seen_at") or today)

    if last_ver != ver:
        cur_tuple = _parse_version_tuple(ver)
        prev_tuple = _parse_version_tuple(last_ver)
        if cur_tuple is not None and prev_tuple is not None and cur_tuple < prev_tuple:
            via = "rollback"
        elif last_launcher_sig and launcher_sig and launcher_sig != last_launcher_sig:
            via = "installer_upgrade"
        else:
            via = "hot_update"
        payload = {
            "cid": cid,
            "event": "upgrade",
            "ver": ver,
            "prev_ver": last_ver if prev_tuple is not None else "",
            "upgrade_via": via,
            "channel": channel,
            "shell_ver": shell_version,
            "os": os_label,
        }
        next_state = {
            "cid": cid,
            "first_seen_at": first_seen_at,
            "last_version": ver,
            "last_active_date": today,
            "last_launcher_sig": launcher_sig or last_launcher_sig,
        }
        return payload, next_state

    if last_active_date != today:
        payload = {
            "cid": cid,
            "event": "active",
            "ver": ver,
            "prev_ver": "",
            "upgrade_via": "none",
            "channel": channel,
            "shell_ver": shell_version,
            "os": os_label,
        }
        next_state = {
            "cid": cid,
            "first_seen_at": first_seen_at,
            "last_version": ver,
            "last_active_date": today,
            "last_launcher_sig": launcher_sig or last_launcher_sig,
        }
        return payload, next_state

    return None, state


def send_telemetry_ping(
    endpoint: str,
    payload: dict,
    *,
    timeout: float = TELEMETRY_TIMEOUT_SECONDS,
) -> bool:
    if not endpoint.startswith(("https://", "http://")):
        return False
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"ok-bd2/{payload.get('ver', '0.0.0')}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 0)) < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def run_startup_telemetry(
    config: dict,
    context: TelemetryContext,
    *,
    state_path: Path | None = None,
    endpoint: str | None = None,
    env: dict[str, str] | None = None,
    sender: Callable[[str, dict], bool] = send_telemetry_ping,
    now: datetime.datetime | None = None,
) -> bool:
    """Synchronously evaluate and send at most one startup ping; returns True if sent."""
    environ = os.environ if env is None else env
    force = environ.get(TELEMETRY_FORCE_ENV, "").strip().lower() in {"1", "true", "yes"}
    if not force and installed_base(context.working_dir) is None:
        return False
    if not is_telemetry_enabled(context, env=environ):
        return False
    target = resolve_endpoint(environ) if endpoint is None else endpoint.strip()
    if not target:
        return False

    path = state_path or default_state_path(environ)
    state = load_state(path)
    payload, next_state = build_telemetry_ping(
        state,
        current_version=str(config.get("version") or ""),
        today=beijing_today(now),
        channel=detect_channel(context.working_dir),
        launcher_sig=detect_launcher_sig(context.working_dir),
        had_existing_configs=context.had_existing_configs,
        legacy_version=context.legacy_version,
        shell_version=REQUIRED_SHELL_VERSION,
        os_label=detect_os_label(),
    )
    if payload is None:
        return False
    if sender(target, payload):
        save_state(path, next_state)
        return True
    return False


def schedule_startup_telemetry(
    config: dict,
    context: TelemetryContext,
) -> threading.Thread | None:
    """Fire startup telemetry in a daemon thread so startup is never delayed."""
    force = os.environ.get(TELEMETRY_FORCE_ENV, "").strip().lower() in {"1", "true", "yes"}
    if not force and installed_base(context.working_dir) is None:
        return None
    if not resolve_endpoint():
        return None

    def _worker() -> None:
        try:
            run_startup_telemetry(config, context)
        except Exception:
            pass

    thread = threading.Thread(target=_worker, name="TelemetryPing", daemon=True)
    thread.start()
    return thread
