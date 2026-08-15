from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

_SUPPRESSION_DEPTH_ATTRIBUTE = "_completion_notification_suppression_depth"


def log_task_completion(task, message: str) -> None:
    """Log a task completion and notify only for a standalone execution."""

    suppression_depth = int(getattr(task, _SUPPRESSION_DEPTH_ATTRIBUTE, 0) or 0)
    task.log_info(message, notify=suppression_depth == 0)


@contextmanager
def suppress_task_completion_notifications(task) -> Iterator[None]:
    """Temporarily silence completion popups from a directly invoked child task."""

    previous_depth = int(getattr(task, _SUPPRESSION_DEPTH_ATTRIBUTE, 0) or 0)
    setattr(task, _SUPPRESSION_DEPTH_ATTRIBUTE, previous_depth + 1)
    try:
        yield
    finally:
        if previous_depth:
            setattr(task, _SUPPRESSION_DEPTH_ATTRIBUTE, previous_depth)
        else:
            try:
                delattr(task, _SUPPRESSION_DEPTH_ATTRIBUTE)
            except AttributeError:
                pass
