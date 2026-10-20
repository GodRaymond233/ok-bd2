"""任务下次运行时间与失败退避的持久化管理。

日常任务成功后等待北京时间04:00；跑图按今日额度或本周完成状态，
等待次日或下周一08:00。配置成功间隔时取更早的到期时间。
失败按配置间隔退避；到期后的执行由启动检查或任务结束事件触发。

调度门禁作用于自动执行与执行剩余模式，手动启动不受 next_run 限制。
mark_due_now 将任务设置为下次调度检查时立即可执行。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from ok import Logger
from ok.util.file import get_relative_path, read_json_file, write_json_file

from src.tasks.collection.cycle import COLLECTION_REFRESH_HOUR
from src.tasks.run_history import BEIJING_TZ, DAILY_REFRESH_HOUR

logger = Logger.get_logger(__name__)

STORE_VERSION = 1
DEFAULT_FILE = ("configs", "task_schedule.json")

# 任务失败后的默认退避分钟数。
DEFAULT_FAILURE_INTERVAL_MINUTES = 30.0


def _to_beijing(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=BEIJING_TZ)


def next_daily_anchor_ts(
    now: float | None = None, *, refresh_hour: int = DAILY_REFRESH_HOUR,
) -> float:
    """Return the next daily Beijing refresh strictly after ``now``."""
    moment = _to_beijing(time.time() if now is None else now)
    anchor = moment.replace(
        hour=refresh_hour, minute=0, second=0, microsecond=0
    )
    if anchor <= moment:
        anchor += timedelta(days=1)
    return anchor.timestamp()


def next_weekly_anchor_ts(
    now: float | None = None, *, refresh_hour: int = DAILY_REFRESH_HOUR,
) -> float:
    """Return the next Monday Beijing refresh strictly after ``now``."""
    moment = _to_beijing(time.time() if now is None else now)
    anchor = moment.replace(hour=refresh_hour, minute=0, second=0, microsecond=0)
    if anchor <= moment:
        anchor += timedelta(days=1)
    while anchor.weekday() != 0:
        anchor += timedelta(days=1)
    return anchor.timestamp()


@dataclass(frozen=True)
class SchedulePolicy:
    """How a task's next_run is postponed after a run.

    ``anchor``: ``"daily"`` -> next Beijing daily refresh; ``"weekly"`` -> next Monday
    refresh; ``None`` -> anchor-less tasks only use the interval fields.
    ``success_interval_minutes``: extra candidate applied on success (0 disables it).
    ``failure_interval_minutes``: backoff applied on failure.
    """

    anchor: str | None = "daily"
    success_interval_minutes: float = 0.0
    failure_interval_minutes: float = DEFAULT_FAILURE_INTERVAL_MINUTES
    refresh_hour: int = DAILY_REFRESH_HOUR


# 受调度任务注册表，键为任务显示名（run_history 同名）。
# 日常类锚定北京 04:00；跑图资源锚定周一 08:00，技能额度耗尽后等次日 08:00。
# 不注册“一键完成日常”自身：自动调度只消费子任务账本，批处理整体只在
# “仅执行今日未完成”模式下作为载体启动，其自身 next_run 无消费者。
TASK_POLICIES: dict[str, SchedulePolicy] = {
    "公会、小屋、酒馆": SchedulePolicy("daily"),
    "快速狩猎": SchedulePolicy("daily"),
    "白嫖抽抽乐": SchedulePolicy("daily"),
    "广场女神像": SchedulePolicy("daily"),
    "镜中之战": SchedulePolicy("daily"),
    "每日跑商": SchedulePolicy("daily"),
    "每周跑图": SchedulePolicy("weekly", refresh_hour=COLLECTION_REFRESH_HOUR),
}


def policy_for(task_name: str) -> SchedulePolicy | None:
    return TASK_POLICIES.get(str(task_name))


class TaskScheduleStore:
    """JSON-backed ``task name -> next_run`` ledger surviving restarts."""

    def __init__(self, path: str | None = None):
        self.path = path or get_relative_path(*DEFAULT_FILE)
        # 执行器线程（批处理子任务结算）与主线程（复查/记录器）可能并发
        # 读写账本；写侧互斥，避免交错时丢更新或写出交错内容。
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        data = read_json_file(self.path)
        if not isinstance(data, dict):
            if data is not None:
                logger.warning(f"schedule file is not a dict, reset: {self.path}")
            self._records = {}
            return
        records = data.get("tasks")
        if data.get("version") != STORE_VERSION or not isinstance(records, dict):
            self._records = {}
            return
        self._records = {
            str(name): record
            for name, record in records.items()
            if isinstance(record, dict) and isinstance(record.get("next_run"), (int, float))
        }

    def _save(self) -> None:
        try:
            write_json_file(
                self.path,
                {"version": STORE_VERSION, "tasks": self._records},
            )
        except Exception as exc:  # never let scheduling metadata break tasks
            logger.error(f"save schedule failed: {exc}")

    def next_run(self, task_name: str) -> float | None:
        record = self._records.get(str(task_name))
        if record is None:
            return None
        value = record.get("next_run")
        return float(value) if isinstance(value, (int, float)) else None

    def last_run_ok(self, task_name: str) -> bool | None:
        record = self._records.get(str(task_name))
        if record is None:
            return None
        return bool(record.get("ok"))

    def is_due(self, task_name: str, now: float | None = None, *, scope: str | None = None) -> bool:
        """No record or an expired next_run means the task is due."""
        record = self._records.get(str(task_name), {})
        if scope is not None and record.get("ok") and record.get("completion_scope") != scope:
            return True
        next_run = self.next_run(task_name)
        if next_run is None:
            return True
        return next_run <= (time.time() if now is None else now)

    def backoff_remaining_minutes(self, task_name: str, now: float | None = None) -> float:
        """Minutes until the task becomes due; 0 when it is due right now."""
        next_run = self.next_run(task_name)
        if next_run is None:
            return 0.0
        remaining = next_run - (time.time() if now is None else now)
        return max(0.0, remaining / 60.0)

    def delay_after_record(self, task_name: str, ok: bool, history,
                           now: float | None = None) -> float | None:
        """Schedule standalone and batch runs from the same completion evidence."""
        policy = None
        record = history.last_run(task_name) or {}
        if task_name == "每周跑图":
            weekly_complete = history.is_completed_this_week(task_name, now=now)
            if ok and record.get("collection_period") and not (
                weekly_complete or history.is_completed_today(task_name, now=now)
            ):
                self.mark_due_now(task_name, now=now)
                return self.next_run(task_name)
            if not weekly_complete:
                policy = SchedulePolicy("daily", refresh_hour=COLLECTION_REFRESH_HOUR)
        elif ok and record.get("daily_cycle") is not None and not history.is_completed_today(
            task_name, now=now,
        ):
            self.mark_due_now(task_name, now=now)
            return self.next_run(task_name)
        return self.delay_after_run(
            task_name, ok=ok, now=now, policy=policy,
            scope=record.get("completion_scope"),
        )

    def delay_after_run(
        self,
        task_name: str,
        ok: bool,
        now: float | None = None,
        policy: SchedulePolicy | None = None,
        *,
        scope: str | None = None,
    ) -> float | None:
        """按成功周期或失败退避时间推迟任务，并保存下次运行时间。

        Returns the written next_run timestamp, or ``None`` when the task has
        no schedule policy (unregistered tasks stay unscheduled).
        """
        resolved = policy if policy is not None else policy_for(task_name)
        if resolved is None:
            return None
        moment = time.time() if now is None else now

        candidates: list[float] = []
        if ok:
            if resolved.anchor == "daily":
                candidates.append(next_daily_anchor_ts(moment, refresh_hour=resolved.refresh_hour))
            elif resolved.anchor == "weekly":
                candidates.append(next_weekly_anchor_ts(moment, refresh_hour=resolved.refresh_hour))
            if resolved.success_interval_minutes > 0:
                candidates.append(
                    moment + resolved.success_interval_minutes * 60.0
                )
        else:
            candidates.append(moment + resolved.failure_interval_minutes * 60.0)

        if not candidates:
            return None
        next_run = min(candidates)
        with self._lock:
            self._records[str(task_name)] = {
                "next_run": next_run,
                "ok": bool(ok),
                "updated": moment,
                "completion_scope": scope if ok else None,
            }
            self._save()
        return next_run

    def mark_due_now(self, task_name: str, now: float | None = None) -> float:
        """将任务设为当前到期，在下一次调度检查时执行。"""
        moment = time.time() if now is None else now
        with self._lock:
            record = self._records.setdefault(str(task_name), {})
            record["next_run"] = moment
            record["updated"] = moment
            self._save()
        return moment


_default_store: TaskScheduleStore | None = None


def default_store() -> TaskScheduleStore:
    global _default_store
    if _default_store is None:
        _default_store = TaskScheduleStore()
    return _default_store


def set_default_store(store: TaskScheduleStore | None) -> None:
    """Override the process-wide store (tests); None restores lazy creation."""
    global _default_store
    _default_store = store
