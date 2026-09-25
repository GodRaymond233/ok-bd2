"""Shared process startup for the production and debug entry points."""

from __future__ import annotations

import datetime
import faulthandler
from collections.abc import Callable
from pathlib import Path

CRASH_LOG_PATTERN = "crash-*.log"
MAX_CRASH_LOGS = 10


def _prune_crash_logs(
    log_dir: Path,
    current_path: Path,
    *,
    max_logs: int = MAX_CRASH_LOGS,
) -> None:
    """Keep the current crash log and the newest bounded set of older logs."""
    max_logs = max(1, int(max_logs))
    try:
        candidates = [
            path
            for path in log_dir.glob(CRASH_LOG_PATTERN)
            if path.is_file() and path != current_path
        ]
    except OSError:
        return

    def sort_key(path: Path):
        try:
            return path.stat().st_mtime, path.name
        except OSError:
            return 0, path.name

    candidates.sort(key=sort_key)
    keep_old = max_logs - 1
    for path in candidates[: max(0, len(candidates) - keep_old)]:
        try:
            path.unlink()
        except OSError:
            pass


def open_crash_log(
    log_dir: Path | str = "logs",
    *,
    max_logs: int = MAX_CRASH_LOGS,
    now: datetime.datetime | None = None,
):
    """Open the current crash log and prune older logs without touching it."""
    directory = Path(log_dir)
    directory.mkdir(exist_ok=True)
    timestamp = now or datetime.datetime.now()
    current_path = directory / f"crash-{timestamp:%Y%m%d-%H%M%S}.log"
    crash_log = current_path.open("a", encoding="utf-8")
    _prune_crash_logs(directory, current_path, max_logs=max_logs)
    return crash_log


def start_application(
    config_loader: Callable[[], dict],
    configure: Callable[[dict], object] | None = None,
) -> None:
    """Run an ok-script application after diagnostics and dependency checks."""
    from src.compat.dependency_guard import ensure_core_dependencies

    crash_log = open_crash_log()
    fault_handler_enabled = False
    try:
        faulthandler.enable(file=crash_log, all_threads=True)
        fault_handler_enabled = True
        ensure_core_dependencies()

        import ok

        config = config_loader()
        if configure is not None:
            configure(config)
        ok.OK(config).start()
    finally:
        if fault_handler_enabled:
            faulthandler.disable()
        crash_log.close()
