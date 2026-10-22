"""Today's daily progress, shared by the Qt daily board and the desktop client.

Qt-free so the desktop core can serve it (``GET /api/bd2/today``) with the same rules
the Qt banner and task cards use: a batch child counts as done for its daily or weekly
cycle under its current configuration, and 每周跑图 distinguishes "done this week" from
"today's quota used".
"""

from __future__ import annotations

from src.tasks.collection.cycle import COLLECTION_REFRESH_HOUR
from src.tasks.run_history import configuration_scope, default_store
from src.tasks.scheduler import next_daily_anchor_ts, next_weekly_anchor_ts

BATCH_TASK_NAME = "一键完成日常"

# Collection resources refresh Monday 08:00 Beijing; skill quotas refresh daily at 08:00.
WEEKLY_TASK_NAMES = frozenset({"每周跑图"})


def find_batch(executor):
    """The 一键完成日常 task, matched by name as the Qt daily board does."""
    for task in getattr(executor, "onetime_tasks", None) or []:
        if str(getattr(task, "name", "")) == BATCH_TASK_NAME:
            return task
    return None


def completion_state(task, store=None, now: float | None = None) -> str | None:
    """``week`` (done this week), ``quota`` (today's quota used), ``today`` or None."""
    store = store or default_store()
    name = str(getattr(task, "name", ""))
    scope = configuration_scope(task)
    if name in WEEKLY_TASK_NAMES:
        if store.is_completed(name, now=now, scope=scope) and store.is_completed_this_week(
            name, now=now
        ):
            return "week"
        return "quota" if store.is_completed_today(name, now=now, scope=scope) else None
    return "today" if store.is_completed_today(name, now=now, scope=scope) else None


def batch_items(executor, store=None, now: float | None = None) -> list[dict]:
    """The batch children in run order with participation, completion and last run."""
    batch = find_batch(executor)
    if batch is None:
        return []
    store = store or default_store()
    config = getattr(batch, "config", {}) or {}
    items = []
    for child in getattr(batch, "child_tasks", ()):
        try:
            task = executor.get_task_by_class(child.task_class)
        except Exception:
            task = None
        name = str(getattr(task, "name", None) or child.config_key)
        record = store.last_run(name)
        items.append(
            {
                "key": child.config_key,
                "name": name,
                "enabled": bool(config.get(child.config_key, True)),
                "done": bool(
                    store.is_completed(name, now=now, scope=configuration_scope(task))
                ),
                "completion": completion_state(task, store, now) if task is not None else None,
                "weekly": name in WEEKLY_TASK_NAMES,
                "last": None
                if record is None
                else {
                    "finished": record["finished"],
                    "duration": record.get("duration"),
                    "ok": bool(record.get("ok")),
                    "status": str(record.get("status") or ""),
                },
            }
        )
    return items


def refresh_times(now: float | None = None) -> dict:
    """Next Beijing-time refreshes: dailies, collection quotas and collection resources."""
    return {
        "daily": next_daily_anchor_ts(now),
        "collection_quota": next_daily_anchor_ts(now, refresh_hour=COLLECTION_REFRESH_HOUR),
        "collection_week": next_weekly_anchor_ts(now, refresh_hour=COLLECTION_REFRESH_HOUR),
    }


def today_summary(executor, store=None, now: float | None = None) -> dict:
    """Everything the desktop client's 今日 page shows besides live task state."""
    batch = find_batch(executor)
    config = getattr(batch, "config", {}) or {}
    skipped = getattr(batch, "skip_reasons", None)
    return {
        "batch": None
        if batch is None
        else {
            "name": str(batch.name),
            "enabled": bool(config.get("启用", True)),
            "auto_start": bool(config.get("启动自动执行日常", False)),
            "shutdown": bool(config.get("完成日常后自动关机", False)),
        },
        "items": batch_items(executor, store, now),
        "skipped": skipped() if callable(skipped) else {},
        "refresh": refresh_times(now),
    }
