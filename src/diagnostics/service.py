from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.diagnostics.bundle import ReportBundleBuilder
from src.diagnostics.models import DiagnosticSnapshot, ReportResult

_TASK_INFO_KEYS = (
    "状态",
    "当前阶段",
    "当前子任务",
    "完成",
    "失败",
    "跳过",
    "鼠标点击",
    "截图方式",
    "游戏分辨率",
    "匹配错误",
)


class DiagnosticsManager:
    def __init__(
        self,
        *,
        project_root: Path,
        output_dir: Path,
        app_version: str,
    ):
        self.builder = ReportBundleBuilder(
            project_root=project_root,
            output_dir=output_dir,
            app_version=app_version,
        )

    def prepare(
        self,
        *,
        executor=None,
        device_manager=None,
        preferred_frame=None,
        preferred_frame_age_seconds: float | None = None,
    ) -> DiagnosticSnapshot:
        warnings: list[str] = []
        frame, frame_age = _capture_frame(
            executor,
            warnings,
            preferred_frame=preferred_frame,
            preferred_frame_age_seconds=preferred_frame_age_seconds,
        )
        method = _capture_method(executor, device_manager)
        method_name = _capture_method_name(method)

        executor_was_running = bool(executor is not None and not executor.paused)
        if executor_was_running:
            executor.pause()

        safe_point_reached = _wait_for_interaction_idle(executor, device_manager)
        if not safe_point_reached:
            warnings.append("未能在时限内确认鼠标操作已经结束")

        return DiagnosticSnapshot(
            captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            frame=frame,
            frame_age_seconds=frame_age,
            capture_method=method_name,
            task=_task_snapshot(executor),
            executor_was_running=executor_was_running,
            safe_point_reached=safe_point_reached,
            warnings=tuple(warnings),
        )

    def build_report(
        self,
        snapshot: DiagnosticSnapshot,
        description: str,
        *,
        include_screenshot: bool,
    ) -> ReportResult:
        return self.builder.build(
            snapshot,
            description,
            include_screenshot=include_screenshot,
        )

    @staticmethod
    def resume(snapshot: DiagnosticSnapshot, executor=None) -> bool:
        if not snapshot.executor_was_running or executor is None or not executor.paused:
            return False
        executor.start()
        return True


def _capture_frame(
    executor,
    warnings: list[str],
    *,
    preferred_frame=None,
    preferred_frame_age_seconds: float | None = None,
):
    frame = None
    frame_age = None
    if preferred_frame is not None:
        try:
            frame = preferred_frame.copy()
            frame_age = preferred_frame_age_seconds
        except Exception as exc:
            warnings.append(f"复制实时预览画面失败：{type(exc).__name__}")
            frame = None

    if frame is None and executor is not None:
        try:
            candidate = executor.nullable_frame()
            if candidate is not None:
                frame = candidate.copy()
                last_frame_time = float(getattr(executor, "_last_frame_time", 0.0))
                if last_frame_time > 0:
                    frame_age = max(0.0, time.time() - last_frame_time)
        except Exception as exc:
            warnings.append(f"读取最近游戏画面失败：{type(exc).__name__}")
            frame = None

    if frame is not None and frame_age is not None and frame_age > 1.0:
        warnings.append("最近游戏画面已超过 1 秒，报告中会标记帧龄")
    return frame, frame_age


def _capture_method(executor, device_manager):
    method = getattr(device_manager, "capture_method", None)
    if method is not None or executor is None:
        return method
    try:
        return executor.method
    except Exception:
        return None


def _capture_method_name(method) -> str | None:
    if method is None:
        return None
    try:
        if hasattr(method, "get_name"):
            return str(method.get_name())
    except Exception:
        pass
    return type(method).__name__


def _wait_for_interaction_idle(executor, device_manager, timeout: float = 2.0) -> bool:
    interaction = None
    if executor is not None:
        try:
            interaction = executor.interaction
        except Exception:
            interaction = None
    if interaction is None:
        interaction = getattr(device_manager, "interaction", None)
    if interaction is None:
        return True
    wait_until_idle = getattr(interaction, "wait_until_idle", None)
    if not callable(wait_until_idle):
        return False
    try:
        return bool(wait_until_idle(timeout=timeout))
    except Exception:
        return False


def _task_snapshot(executor) -> dict[str, Any]:
    task = getattr(executor, "current_task", None) if executor is not None else None
    if task is None:
        return {"class": "未运行任务"}

    result: dict[str, Any] = {
        "class": type(task).__name__,
        "paused": bool(getattr(task, "paused", False)),
    }
    info = getattr(task, "info", {})
    try:
        current_info = dict(info)
    except (TypeError, RuntimeError):
        current_info = {}
    for key in _TASK_INFO_KEYS:
        if key in current_info:
            result[key] = current_info[key]
    return result
