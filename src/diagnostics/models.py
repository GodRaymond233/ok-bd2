from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiagnosticSnapshot:
    """A bounded in-memory snapshot captured before the report dialog opens."""

    captured_at: str
    frame: Any | None = field(default=None, repr=False, compare=False)
    frame_age_seconds: float | None = None
    capture_method: str | None = None
    task: dict[str, Any] = field(default_factory=dict)
    executor_was_running: bool = False
    safe_point_reached: bool = True
    warnings: tuple[str, ...] = ()
    task_started_at: float | None = None


@dataclass(frozen=True)
class ReportResult:
    report_id: str
    archive_path: Path
    group_message: str
    manifest: dict[str, Any]
