"""Start Electron, its local service, or an explicitly headless task run.

Both automation paths take the session-wide executor lock before building the core.
"""

from __future__ import annotations

import argparse
import datetime
import faulthandler
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

CRASH_LOG_PATTERN = "crash-*.log"
MAX_CRASH_LOGS = 10
SCHEDULED_FAILURE_DIR = Path("logs/scheduled-starts")
MAX_SCHEDULED_FAILURES = 50


def scheduled_start_failures() -> list[dict]:
    records = []
    for path in sorted(SCHEDULED_FAILURE_DIR.glob("*.json"), reverse=True)[:MAX_SCHEDULED_FAILURES]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(record, dict) and isinstance(record.get("finished"), (int, float))
                    and isinstance(record.get("task"), str) and record.get("ok") is False):
                records.append(record)
        except (OSError, ValueError):
            continue
    return records


def _report_headless_failure(arguments, message: str, code: int, *, notify=True) -> None:
    print(message, file=sys.stderr)
    if arguments.task is not None:
        # A refused process must not overwrite the active executor's task/stage ledger.
        temporary = None
        try:
            SCHEDULED_FAILURE_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=SCHEDULED_FAILURE_DIR,
                prefix=f"{time.time_ns()}-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump({
                    "task": f"计划任务（{arguments.task}）", "started": None,
                    "finished": time.time(), "duration": None, "ok": False,
                    "status": message, "details": {"Error": message, "退出码": str(code)},
                }, stream, ensure_ascii=False)
            temporary.replace(temporary.with_suffix(".json"))
            paths = sorted(SCHEDULED_FAILURE_DIR.glob("*.json"), reverse=True)
            for path in paths[MAX_SCHEDULED_FAILURES:]:
                path.unlink(missing_ok=True)
        except OSError as error:
            print(f"计划任务失败记录写入失败：{error}", file=sys.stderr)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    if notify:
        _notify_headless_failure(arguments, message)


def _notify_headless_failure(arguments, message):
    if arguments.task is not None:
        if os.name == "nt":
            try:
                import base64
                import subprocess

                script = """
[Windows.UI.Notifications.ToastNotificationManager,
 Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument,
 Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$binding = '<binding template="ToastGeneric"><text/><text/></binding>'
$xml.LoadXml('<toast><visual>' + $binding + '</visual></toast>')
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode('ok-bd2 计划任务启动失败')) > $null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:OKBD2_START_FAILURE)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$manager = [Windows.UI.Notifications.ToastNotificationManager]
$manager::CreateToastNotifier('io.github.godraymond233.okbd2.desktop').Show($toast)
"""
                notice = subprocess.Popen(
                    [str(Path(os.environ["SystemRoot"]) /
                         "System32/WindowsPowerShell/v1.0/powershell.exe"),
                     "-NoProfile", "-EncodedCommand",
                     base64.b64encode(script.encode("utf-16le")).decode()],
                    env=dict(os.environ, OKBD2_START_FAILURE=message),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                import threading

                threading.Thread(target=notice.wait, daemon=True).start()
            except Exception as error:
                print(f"计划任务通知失败：{error}", file=sys.stderr)
    else:
        from src.executor_guard import show_refusal_dialog

        show_refusal_dialog(message)


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
    from src.executor_guard import mark_current_process

    mark_current_process()
    arguments = parse_entry_arguments()
    if entry_mode(arguments, configure) == "desktop":
        from src.desktop.bootstrap import main

        forwarded = list(sys.argv[1:])
        if configure is not None and "--debug" not in forwarded:
            forwarded.append("--debug")
        raise SystemExit(main(forwarded))
    from src.ui_mode import UI_MODE_ENV

    os.environ[UI_MODE_ENV] = "service"
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
        refusal_message,
        release_executor_lock,
    )

    arguments = parse_entry_arguments()
    acquired, holder = acquire_executor_lock("headless")
    if not acquired:
        _report_headless_failure(arguments, refusal_message(holder), EXIT_EXECUTOR_LOCKED)
        raise SystemExit(EXIT_EXECUTOR_LOCKED)
    engine = None
    failure = None
    try:
        ensure_core_dependencies()
        import ok

        config = config_loader()
        if configure is not None:
            configure(config)
        config["use_gui"] = False
        from src.desktop.backups import prepare_installed_configs

        prepare_installed_configs(config)
        # The session lock replaces ok-script's mutex, which can kill an older instance.
        config["check_mutex"] = False
        install_executor_guards()
        if message := legacy_guard().blocking_message(force=True):
            raise RuntimeError(message)
        engine = ok.OK(config)
        if arguments.task is None:
            engine.start()
        else:
            # OK.start logs and swallows run_task errors, hiding them from Task Scheduler.
            engine.start_mcp_server()
            engine.run_task(engine.args["task"], exit_after=engine.args.get("exit", False))
    except Exception as error:
        failure = f"任务启动失败：{error}"
        _report_headless_failure(arguments, failure, EXIT_STARTUP_FAILED, notify=False)
    finally:
        try:
            if engine is not None:
                engine.quit()
        finally:
            release_executor_lock()
    if failure is not None:
        _notify_headless_failure(arguments, failure)
        raise SystemExit(EXIT_STARTUP_FAILED)
