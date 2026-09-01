"""Persisted next-run scheduler (ALAS/NKAS 式调度内核的 ok-bd2 移植).

每个受调度任务在 ``configs/task_schedule.json`` 中持久化一个"下次运行时间"
（``next_run``），对应 ALAS/NKAS 中每个任务的 ``Scheduler.NextRun``：

- 任务成功后按策略推迟：日常任务推迟到下一次游戏日刷新（北京 04:00），
  周常任务推迟到下周一 04:00；配置了成功间隔的任务取锚点与间隔中更近者。
- 任务失败后按失败间隔退避（默认 30 分钟），不推进锚点，避免立即死循环
  重跑。注意：本调度没有常驻定时器，退避到期后的重试发生在下一次调度
  检查时（任务完成复查或下次应用启动），而不是退避一到期就自动拉起。
- 账本随配置文件落盘，重启脚本后原样读回；未到期的任务即视为"不必执行"，
  这就是"一天内重复打开脚本，已执行过的不会再执行一遍"的来源。
- ``mark_due_now`` 对应 ALAS 的 ``task_call``：把某任务的 next_run 拉回当前
  时刻，强制其在下一次调度检查时被执行。

调度门禁只作用于自动执行路径与"执行剩余"模式；用户手动点击任务视为强制
立即执行（对齐 ALAS 中手动把 NextRun 改到过去的语义），不受 next_run 限制。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from ok import Logger
from ok.util.file import get_relative_path, read_json_file, write_json_file

from src.tasks.run_history import BEIJING_TZ, DAILY_REFRESH_HOUR

logger = Logger.get_logger(__name__)

STORE_VERSION = 1
DEFAULT_FILE = ("configs", "task_schedule.json")

# 任务失败后的默认退避分钟数（ALAS Scheduler.FailureInterval 的默认语义）。
DEFAULT_FAILURE_INTERVAL_MINUTES = 30.0


def _to_beijing(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=BEIJING_TZ)


def next_daily_anchor_ts(now: float | None = None) -> float:
    """Return the next game-day refresh (Beijing 04:00) strictly after ``now``."""
    moment = _to_beijing(time.time() if now is None else now)
    anchor = moment.replace(
        hour=DAILY_REFRESH_HOUR, minute=0, second=0, microsecond=0
    )
    if anchor <= moment:
        anchor += timedelta(days=1)
    return anchor.timestamp()


def next_weekly_anchor_ts(now: float | None = None) -> float:
    """Return the next Monday 04:00 Beijing strictly after ``now``."""
    moment = _to_beijing(time.time() if now is None else now)
    anchor = moment.replace(hour=DAILY_REFRESH_HOUR, minute=0, second=0, microsecond=0)
    if anchor <= moment:
        anchor += timedelta(days=1)
    while anchor.weekday() != 0:
        anchor += timedelta(days=1)
    return anchor.timestamp()


@dataclass(frozen=True)
class SchedulePolicy:
    """How a task's next_run is postponed after a run.

    ``anchor``: ``"daily"`` -> next Beijing 04:00; ``"weekly"`` -> next Monday
    04:00; ``None`` -> anchor-less tasks only use the interval fields.
    ``success_interval_minutes``: extra candidate applied on success (0 disables
    it, matching ALAS tasks that only delay to the next server update).
    ``failure_interval_minutes``: backoff applied on failure.
    """

    anchor: str | None = "daily"
    success_interval_minutes: float = 0.0
    failure_interval_minutes: float = DEFAULT_FAILURE_INTERVAL_MINUTES


# 受调度任务注册表，键为任务显示名（run_history 同名）。
# 日常类锚定北京 04:00；每周跑图锚定周一 04:00。
# 不注册“一键完成日常”自身：自动调度只消费子任务账本，批处理整体只在
# “仅执行今日未完成”模式下作为载体启动，其自身 next_run 无消费者。
TASK_POLICIES: dict[str, SchedulePolicy] = {
    "公会、小屋、酒馆": SchedulePolicy("daily"),
    "快速狩猎": SchedulePolicy("daily"),
    "白嫖抽抽乐": SchedulePolicy("daily"),
    "广场女神像": SchedulePolicy("daily"),
    "镜中之战": SchedulePolicy("daily"),
    "每日跑商": SchedulePolicy("daily"),
    "每周跑图": SchedulePolicy("weekly"),
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

    def is_due(self, task_name: str, now: float | None = None) -> bool:
        """No record or an expired next_run means the task is due."""
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

    def delay_after_run(
        self,
        task_name: str,
        ok: bool,
        now: float | None = None,
        policy: SchedulePolicy | None = None,
    ) -> float | None:
        """Postpone the task per ALAS ``task_delay`` semantics and persist it.

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
                candidates.append(next_daily_anchor_ts(moment))
            elif resolved.anchor == "weekly":
                candidates.append(next_weekly_anchor_ts(moment))
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
            }
            self._save()
        return next_run

    def mark_due_now(self, task_name: str, now: float | None = None) -> float:
        """Force the task due at ``now`` (ALAS ``task_call`` equivalent)."""
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
