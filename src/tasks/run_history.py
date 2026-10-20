"""Persisted per-task run records backing the quest-style UI metadata.

The task cards and the daily board banner need "上次完成 · 今天 09:12" style
information, which the framework does not track.  This module records the most
recent finished run of every one-time task into a small JSON file and derives
"completed today / this week" from the game's Beijing-time refresh anchors
(daily tasks 04:00; collection quotas daily 08:00 and resources Monday 08:00).

Batch tasks (一键完成日常) record their children immediately after each run,
using each child's actual timestamps.  Finishing the batch only records the
batch itself, so a run crossing the daily refresh cannot move earlier child
completions into the new game day.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ok import Logger
from ok.util.file import get_relative_path, read_json_file, write_json_file

from src.tasks.BaseBD2Task import task_info_snapshot
from src.tasks.collection.cycle import COLLECTION_REFRESH_HOUR

logger = Logger.get_logger(__name__)

# Daily task anchors use Beijing time (UTC+8, no DST); collection uses its own 08:00 anchor.
BEIJING_TZ = timezone(timedelta(hours=8))
DAILY_REFRESH_HOUR = 4

STORE_VERSION = 1
DEFAULT_FILE = ("configs", "task_run_history.json")

# Status texts containing any of these markers never count as a completion.
# Match explicit failure/safety wording instead of the bare ``停止`` token:
# some successful bounded tasks mention that they stopped after reaching their
# configured round limit.
_FAILURE_MARKERS = (
    "中止",
    "失败",
    "已停止",
    "安全停止",
    "停止点击",
    "停止后续",
    "未执行",
    "未完成",
    "无法判断",
)

# The 失败 field uses list strings, joined stage names, or empty placeholders.
_NO_FAILURE_VALUES = frozenset({"", "-", "0", "无", "[]", "none", "false"})


def _to_beijing(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=BEIJING_TZ)


def day_start_ts(now: float | None = None, *, refresh_hour: int = DAILY_REFRESH_HOUR) -> float:
    """Return the current daily refresh anchor in Beijing time."""
    moment = _to_beijing(time.time() if now is None else now)
    anchor = moment.replace(hour=refresh_hour, minute=0, second=0, microsecond=0)
    if moment < anchor:
        anchor -= timedelta(days=1)
    return anchor.timestamp()


def week_start_ts(now: float | None = None, *, refresh_hour: int = DAILY_REFRESH_HOUR) -> float:
    """Return the current Monday refresh anchor in Beijing time."""
    day_start = _to_beijing(day_start_ts(now, refresh_hour=refresh_hour))
    monday = day_start - timedelta(days=day_start.weekday())
    return monday.timestamp()


def _is_successful_run(info: dict) -> bool:
    # An empty snapshot is a concurrent-copy failure/unknown state, not proof
    # that a task completed.  Treating it as success suppresses retries for the
    # whole refresh window.
    if not info or info.get("Error"):
        return False
    status = str(info.get("状态", ""))
    if any(marker in status for marker in _FAILURE_MARKERS):
        return False
    # Several tasks return False with a status text that carries no failure
    # marker (e.g. DailyTask's "公会、小屋、酒馆结束。"), so the structured
    # 失败/结果 keys must be consulted too, or failed runs get recorded as
    # completions and the scheduler skips them for the whole day.
    failure = str(info.get("失败", "")).strip()
    if failure and failure.casefold() not in _NO_FAILURE_VALUES:
        return False
    result = str(info.get("结果", ""))
    return not any(marker in result for marker in _FAILURE_MARKERS)


def is_successful_run(info: dict) -> bool:
    """Public wrapper so the scheduler can reuse the same success rule."""
    return _is_successful_run(info)


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
        self._lock = threading.RLock()
        self._records: dict[str, dict] = {}
        self._stages: dict[str, dict] = {}
        self._stage_error = False
        self._load()

    def _load(self) -> None:
        data = read_json_file(self.path)
        if not isinstance(data, dict):
            self._stage_error = os.path.exists(self.path)
            if data is not None:
                logger.warning(f"run history file is not a dict, reset: {self.path}")
            self._records = {}
            return
        records = data.get("tasks")
        if data.get("version") != STORE_VERSION or not isinstance(records, dict):
            self._stage_error = True
            self._records = {}
            return
        self._records = {
            str(name): record
            for name, record in records.items()
            if isinstance(record, dict) and isinstance(record.get("finished"), (int, float))
        }
        stages = data.get("stages", {})
        self._stage_error = not isinstance(stages, dict)
        self._stages = stages if isinstance(stages, dict) else {}

    def _save(self) -> bool:
        if self._stage_error:
            logger.error("任务记录损坏，保留原文件供核对")
            return False
        temporary = None
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
            os.close(descriptor)
            if write_json_file(
                temporary,
                {"version": STORE_VERSION, "tasks": self._records, "stages": self._stages},
            ) is False:
                raise OSError("运行记录写入失败")
            with open(temporary, "rb+") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            return True
        except Exception as exc:  # never let UI metadata break task execution
            logger.error(f"save run history failed: {exc}")
            return False
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def stage(self, owner: str, step: str) -> dict:
        """Read durable work evidence; unknown storage must never authorize spending."""
        with self._lock:
            if self._stage_error:
                raise RuntimeError("任务阶段记录损坏，停止消费，请先核对运行记录")
            group = self._stages.get(owner, {})
            if not isinstance(group, dict):
                raise RuntimeError("任务阶段分组损坏，停止消费")
            value = group.get(step, {})
            if not isinstance(value, dict) or (
                value and (value.get("state") not in {"done", "pending"}
                           or not isinstance(value.get("scope"), str)
                           or not isinstance(value.get("evidence"), dict))
            ):
                raise RuntimeError("任务阶段状态无效，停止消费")
            return copy.deepcopy(value)

    def save_stage(self, owner: str, step: str, state: str, scope: str, evidence=None) -> None:
        if state not in {"done", "pending"}:
            raise ValueError(state)
        with self._lock:
            self.stage(owner, step)
            previous = copy.deepcopy(self._stages)
            self._stages.setdefault(owner, {})[step] = {
                "state": state, "scope": scope, "time": time.time(),
                "evidence": evidence or {},
            }
            if not self._save():
                self._stages = previous
                raise RuntimeError("任务阶段记录保存失败，停止消费")

    def pending_stages(self, owner: str, prefix: str) -> dict[str, dict]:
        with self._lock:
            self.stage(owner, "")
            return {key: self.stage(owner, key) for key in self._stages.get(owner, {})
                    if key.startswith(prefix) and self.stage(owner, key).get("state") == "pending"}

    def last_run(self, task_name: str) -> dict | None:
        with self._lock:
            return self._records.get(task_name)

    def is_completed_today(self, task_name: str, now: float | None = None,
                           *, scope: str | None = None) -> bool:
        record = self.last_run(task_name)
        if not record or (scope is not None and record.get("completion_scope", scope) != scope):
            return False
        completed = record.get("completed", record["finished"] if record.get("ok") else 0)
        if task_name == "每周跑图":
            anchor = day_start_ts(now, refresh_hour=COLLECTION_REFRESH_HOUR)
            return bool(
                record.get("collection_period") == "daily" and completed
                and record.get("collection_cycle") == _to_beijing(anchor).date().isoformat()
            )
        cycle = record.get("daily_cycle")
        return bool(completed and (
            cycle == day_start_ts(now) if cycle is not None else completed >= day_start_ts(now)
        ))

    def is_completed_this_week(self, task_name: str, now: float | None = None) -> bool:
        record = self.last_run(task_name)
        completed = (record.get("completed", record["finished"] if record.get("ok") else 0)
                     if record else 0)
        if task_name == "每周跑图":
            anchor = week_start_ts(now, refresh_hour=COLLECTION_REFRESH_HOUR)
            return bool(
                record and record.get("collection_period") == "weekly" and completed
                and record.get("collection_cycle") == _to_beijing(anchor).date().isoformat()
            )
        return bool(completed and completed >= week_start_ts(now))

    def is_completed(self, task_name: str, now: float | None = None,
                     *, scope: str | None = None) -> bool:
        """Whether the task is complete for its current daily or weekly cycle."""
        record = self.last_run(task_name)
        if scope is not None and record and record.get("completion_scope", scope) != scope:
            return False
        return self.is_completed_today(task_name, now=now, scope=scope) or (
            task_name == "每周跑图" and self.is_completed_this_week(task_name, now=now)
        )

    def record_task_done(
        self,
        task,
        finished: float | None = None,
        *,
        started: float | None = None,
        ok: bool | None = None,
    ) -> None:
        """Record a finished run, accepting explicit timing/results for batch children."""
        if task is None or not getattr(task, "name", None):
            return
        finished = time.time() if finished is None else finished
        if started is None:
            started = getattr(task, "start_time", 0) or 0
        duration = max(0.0, finished - started) if started else None
        info = task_info_snapshot(task)

        with self._lock:
            previous = self._records.get(str(task.name), {})
            successful = _is_successful_run(info) if ok is None else bool(ok)
            completed = previous.get(
                "completed", previous.get("finished", 0) if previous.get("ok") else 0)
            self._records[str(task.name)] = {
                "finished": finished,
                "duration": duration,
                "status": str(info.get("状态", "")),
                "ok": successful,
                "completed": max(completed, finished) if successful else completed,
                "completion_scope": (configuration_scope(task) if successful
                                     else previous.get("completion_scope")),
                "daily_cycle": (info.get("日常周期") if successful
                                else previous.get("daily_cycle")),
            }
            if task.name == "每周跑图":
                self._records[str(task.name)]["collection_period"] = (
                    {"今日": "daily", "本周": "weekly"}.get(info.get("跑图完成范围"))
                    if successful else previous.get("collection_period")
                )
                self._records[str(task.name)]["collection_cycle"] = (
                    info.get("跑图周期") if successful else previous.get("collection_cycle")
                )
            self._save()


_default_store: RunHistoryStore | None = None
_default_store_lock = threading.RLock()


def stage_owner(task) -> str:
    return type(task).__name__


def configuration_scope(task) -> str:
    config = {key: value for key, value in dict(getattr(task, "config", {}) or {}).items()
              if key != "启用" and not key.startswith("_") and not key.endswith("测试")}
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()[:16]


def stage_scope(task) -> str:
    # Configuration changes invalidate completed work, but never release pending spending.
    return f"{day_start_ts()}:{configuration_scope(task)}"


def read_stage(task, step: str) -> dict:
    return default_store().stage(stage_owner(task), step)


def save_stage(task, step: str, state: str, *, scope=None, evidence=None) -> None:
    default_store().save_stage(stage_owner(task), step, state,
                               stage_scope(task) if scope is None else scope, evidence)


def default_store() -> RunHistoryStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = RunHistoryStore()
        return _default_store


def set_default_store(store: RunHistoryStore | None) -> None:
    """Override the process-wide store (tests); None restores lazy creation."""
    global _default_store
    with _default_store_lock:
        _default_store = store


def install_run_history_recorder() -> bool:
    """Record every finished one-time task into the default store."""

    from ok.core.events import communicate
    from PySide6.QtCore import QObject

    if getattr(install_run_history_recorder, "_installed", False):
        return False

    class _Recorder(QObject):
        def on_task_done(self, task):
            if task is None or not getattr(task, "name", None):
                return
            name = str(task.name)
            try:
                default_store().record_task_done(task)
                record = default_store().last_run(name)
            except Exception as exc:
                logger.error(f"record run history failed: {exc}")
                record = None
            try:
                # 记录本次完成状态后，再计算并保存下次运行时间。
                from src.tasks import scheduler as task_scheduler

                if record is not None:
                    ok = bool(record.get("ok"))
                else:
                    ok = is_successful_run(task_info_snapshot(task))
                task_scheduler.default_store().delay_after_record(
                    name, ok=ok, history=default_store(),
                )
            except Exception as exc:
                logger.error(f"record schedule failed: {exc}")

    recorder = _Recorder()
    communicate.task_done.connect(recorder.on_task_done)
    # Keep the receiver alive for the app's lifetime.
    install_run_history_recorder._recorder = recorder
    install_run_history_recorder._installed = True
    return True
