"""Persisted per-task run records backing the quest-style UI metadata.

The task cards and the daily board banner need "上次完成 · 今天 09:12" style
information, which the framework does not track.  This module records the most
recent finished run of every one-time task into a small JSON file and derives
"completed today / this week" from the game's Beijing-time refresh anchors
(daily 04:00, weekly Monday 04:00).

Batch tasks (一键完成日常) run their children through ``task.run()`` directly,
so the executor never emits ``task_done`` for them.  ``record_task_done``
therefore fans a batch record out into per-child records using the batch's
``info`` lists (完成/失败/跳过), making a child finished inside the batch count
the same as running it standalone.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ok import Logger
from ok.util.file import get_relative_path, read_json_file, write_json_file

logger = Logger.get_logger(__name__)

# Brown Dust II refreshes daily content at 04:00 Beijing time (UTC+8, no DST).
BEIJING_TZ = timezone(timedelta(hours=8))
DAILY_REFRESH_HOUR = 4

STORE_VERSION = 1
DEFAULT_FILE = ("configs", "task_run_history.json")

# Status texts containing any of these markers never count as a completion.
_FAILURE_MARKERS = ("中止", "失败")


def _to_beijing(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=BEIJING_TZ)


def day_start_ts(now: float | None = None) -> float:
    """Return the start of the current game day (today 04:00 Beijing)."""
    moment = _to_beijing(time.time() if now is None else now)
    anchor = moment.replace(hour=DAILY_REFRESH_HOUR, minute=0, second=0, microsecond=0)
    if moment < anchor:
        anchor -= timedelta(days=1)
    return anchor.timestamp()


def week_start_ts(now: float | None = None) -> float:
    """Return the start of the current game week (Monday 04:00 Beijing)."""
    day_start = _to_beijing(day_start_ts(now))
    monday = day_start - timedelta(days=day_start.weekday())
    return monday.timestamp()


def _is_successful_run(info: dict) -> bool:
    if info.get("Error"):
        return False
    status = str(info.get("状态", ""))
    return not any(marker in status for marker in _FAILURE_MARKERS)


def contains_joined_name(joined: Any, name: str) -> bool:
    """Boundary-aware membership test for '、'-joined info lists.

    Child names may themselves contain '、' (e.g. 公会、小屋、酒馆), so plain
    split('、') corrupts them.  Elements of these lists are always whole
    config keys, so an exact substring bounded by start/end or '、' is a
    reliable membership test.
    """
    if not isinstance(joined, str) or not name:
        return False
    start = joined.find(name)
    while start >= 0:
        end = start + len(name)
        before_ok = start == 0 or joined[start - 1] == "、"
        after_ok = end == len(joined) or joined[end] == "、"
        if before_ok and after_ok:
            return True
        start = joined.find(name, start + 1)
    return False


class RunHistoryStore:
    """JSON-backed ``task name -> last finished run`` records."""

    def __init__(self, path: str | None = None):
        self.path = path or get_relative_path(*DEFAULT_FILE)
        self._records: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        data = read_json_file(self.path)
        if not isinstance(data, dict):
            if data is not None:
                logger.warning(f"run history file is not a dict, reset: {self.path}")
            self._records = {}
            return
        records = data.get("tasks")
        if data.get("version") != STORE_VERSION or not isinstance(records, dict):
            self._records = {}
            return
        self._records = {
            str(name): record
            for name, record in records.items()
            if isinstance(record, dict) and isinstance(record.get("finished"), (int, float))
        }

    def _save(self) -> None:
        try:
            write_json_file(
                self.path,
                {"version": STORE_VERSION, "tasks": self._records},
            )
        except Exception as exc:  # never let UI metadata break task execution
            logger.error(f"save run history failed: {exc}")

    def last_run(self, task_name: str) -> dict | None:
        return self._records.get(task_name)

    def is_completed_today(self, task_name: str, now: float | None = None) -> bool:
        record = self._records.get(task_name)
        return bool(record and record.get("ok") and record["finished"] >= day_start_ts(now))

    def is_completed_this_week(self, task_name: str, now: float | None = None) -> bool:
        record = self._records.get(task_name)
        return bool(record and record.get("ok") and record["finished"] >= week_start_ts(now))

    def record_task_done(self, task, finished: float | None = None) -> None:
        """Record a finished run, fanning batch results out to children."""
        finished = time.time() if finished is None else finished
        started = getattr(task, "start_time", 0) or 0
        duration = max(0.0, finished - started) if started else None
        info = getattr(task, "info", {}) or {}

        self._records[str(task.name)] = {
            "finished": finished,
            "duration": duration,
            "status": str(info.get("状态", "")),
            "ok": _is_successful_run(info),
        }

        child_tasks = getattr(task, "child_tasks", None)
        if child_tasks:
            self._record_batch_children(task, child_tasks, info, finished, duration)

        self._save()

    def _record_batch_children(self, task, child_tasks, info, finished, duration) -> None:
        done_text = info.get("完成")
        fail_text = info.get("失败")
        name_by_config_key = self._child_display_names(task, child_tasks)
        recorded = False
        for child in child_tasks:
            config_key = child.config_key
            completed = contains_joined_name(done_text, config_key)
            failed = contains_joined_name(fail_text, config_key)
            if completed == failed:
                # Skipped (in neither list) or ambiguous; only the unambiguous
                # states become records.
                continue
            recorded = True
            self._records[name_by_config_key.get(config_key, config_key)] = {
                "finished": finished,
                "duration": duration,
                "status": "随一键完成日常完成" if completed else "随一键完成日常失败",
                "ok": completed,
            }
        if not recorded:
            return

    @staticmethod
    def _child_display_names(task, child_tasks) -> dict[str, str]:
        """Map batch config keys to the child tasks' display names."""
        names: dict[str, str] = {}
        executor = getattr(task, "executor", None)
        for child in child_tasks:
            child_task = None
            if executor is not None:
                try:
                    child_task = executor.get_task_by_class(child.task_class)
                except Exception:
                    child_task = None
            names[child.config_key] = str(getattr(child_task, "name", None) or child.config_key)
        return names


_default_store: RunHistoryStore | None = None


def default_store() -> RunHistoryStore:
    global _default_store
    if _default_store is None:
        _default_store = RunHistoryStore()
    return _default_store


def set_default_store(store: RunHistoryStore | None) -> None:
    """Override the process-wide store (tests); None restores lazy creation."""
    global _default_store
    _default_store = store


def install_run_history_recorder() -> bool:
    """Record every finished one-time task into the default store."""

    from ok.gui.Communicate import communicate
    from PySide6.QtCore import QObject

    if getattr(install_run_history_recorder, "_installed", False):
        return False

    class _Recorder(QObject):
        def on_task_done(self, task):
            try:
                default_store().record_task_done(task)
            except Exception as exc:
                logger.error(f"record run history failed: {exc}")

    recorder = _Recorder()
    communicate.task_done.connect(recorder.on_task_done)
    # Keep the receiver alive for the app's lifetime.
    install_run_history_recorder._recorder = recorder
    install_run_history_recorder._installed = True
    return True
