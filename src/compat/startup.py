"""Start Electron, its local service, or an explicitly headless task run.

Both automation paths take the session-wide executor lock before building the core.
"""

from __future__ import annotations

import argparse
import datetime
import faulthandler
import os
import sys
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


def parse_entry_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Entry flags read before ok-script parses its own (``-t``, ``-e``, ``-h``)."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--service", action="store_true")
    parser.add_argument("-h", "--headless", action="store_true")
    parser.add_argument("-t", dest="task")
    arguments, _ = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    return arguments


def entry_mode(arguments: argparse.Namespace, configure) -> str:
    if arguments.service:
        return "service"
    if arguments.headless or arguments.task is not None:
        return "headless"
    return "desktop"


def start_application(
    config_loader: Callable[[], dict],
    configure: Callable[[dict], object] | None = None,
) -> None:
    """Route graphical launches through Electron before loading the core."""
    arguments = parse_entry_arguments()
    if entry_mode(arguments, configure) == "desktop":
        from src.desktop.bootstrap import main

        forwarded = list(sys.argv[1:])
        if configure is not None and "--debug" not in forwarded:
            forwarded.append("--debug")
        raise SystemExit(main(forwarded))
    from src.ui_mode import UI_MODE_ENV

    os.environ[UI_MODE_ENV] = "service"
    from src.executor_guard import mark_current_process

    mark_current_process()

    crash_log = open_crash_log()
    fault_handler_enabled = False
    try:
        faulthandler.enable(file=crash_log, all_threads=True)
        fault_handler_enabled = True
        if arguments.service:
            from src.desktop.service import run_service

            exit_code = run_service(config_loader, configure)
            if exit_code:
                raise SystemExit(exit_code)
            return
        _run_headless(config_loader, configure)
    finally:
        if fault_handler_enabled:
            faulthandler.disable()
        crash_log.close()


def _run_headless(config_loader, configure) -> None:
    from src.compat.dependency_guard import ensure_core_dependencies
    from src.desktop.protocol import EXIT_EXECUTOR_LOCKED, EXIT_STARTUP_FAILED
    from src.executor_guard import (
        acquire_executor_lock,
        install_executor_guards,
        legacy_guard,
    )

    acquired, _holder = acquire_executor_lock("headless")
    if not acquired:
        raise SystemExit(EXIT_EXECUTOR_LOCKED)
    ensure_core_dependencies()

    import ok

    config = config_loader()
    if configure is not None:
        configure(config)
    config["use_gui"] = False
    from src.desktop.backups import prepare_installed_configs

    try:
        prepare_installed_configs(config)
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(EXIT_STARTUP_FAILED) from error
    # The session-wide executor lock replaces ok-script's per-directory mutex, which
    # would terminate a same-directory older instance after five seconds.
    config["check_mutex"] = False
    install_executor_guards()
    legacy_guard().blocking_message(force=True)
    ok.OK(config).start()
