"""One report session per core, with explicit and guarded task resumption."""

import threading
import uuid
from dataclasses import replace
from pathlib import Path

from src.desktop.config_store import config_lock
from src.diagnostics.bundle import MAX_DESCRIPTION_CHARS
from src.diagnostics.service import DiagnosticsManager


class ReportSession:
    def __init__(self, runtime, preview, version, legacy_check=None):
        self.runtime = runtime
        self.preview = preview
        self.legacy_check = legacy_check
        self.manager = DiagnosticsManager(
            project_root=Path.cwd(),
            output_dir=Path("logs/reports"),
            app_version=version,
        )
        self._lock = threading.RLock()
        self.snapshot = None
        self.session_id = None
        self.result = None
        self._task = None
        self._resume_allowed = False
        self._archives = {}

    def _task_identity(self):
        task = self.runtime.executor.current_task
        return (id(task), getattr(task, "start_time", None), bool(getattr(task, "enabled", False)))

    def prevent_resume(self):
        # Called under config_lock; stopping must not wait for ZIP compression.
        self._resume_allowed = False

    def state(self):
        with self._lock:
            snapshot = self.snapshot
            return {
                "session_id": self.session_id,
                "snapshot": None
                if snapshot is None
                else {
                    "captured_at": snapshot.captured_at,
                    "task": snapshot.task,
                    "warnings": list(snapshot.warnings),
                    "was_running": snapshot.executor_was_running,
                },
                "can_resume": bool(
                    snapshot
                    and self._resume_allowed
                    and snapshot.executor_was_running
                    and self._task == self._task_identity()
                ),
                "result": self.result,
            }

    def prepare(self):
        with config_lock, self._lock:
            if self.snapshot is not None:
                return self.state()
            app = self.runtime.ok.headless_app
            if app.start_controller.starting or getattr(app, "window_resize_in_progress", False):
                raise ValueError("请先等待游戏启动或窗口调整完成，再记录问题现场")
            app.diagnostic_pause = True
            try:
                frame, age = self.preview.latest_frame()
                snapshot = self.manager.prepare(
                    executor=self.runtime.executor,
                    device_manager=self.runtime.device_manager,
                    preferred_frame=frame,
                    preferred_frame_age_seconds=age,
                )
            except Exception:
                app.diagnostic_pause = False
                raise
            self.snapshot = replace(snapshot, report_context={"source": "manual"})
            self._task = self._task_identity()
            self._resume_allowed = snapshot.executor_was_running
            self.session_id = uuid.uuid4().hex
            self.result = None
            return self.state()

    def _require(self, session_id):
        if self.snapshot is None or session_id != self.session_id:
            raise ValueError("问题现场已关闭或更换，请重新记录现场")

    def build(self, session_id, description):
        if (
            not isinstance(description, str)
            or not 5 <= len(description.strip()) <= MAX_DESCRIPTION_CHARS
        ):
            raise ValueError(f"请填写 5 至 {MAX_DESCRIPTION_CHARS} 字的问题描述")
        with self._lock:
            self._require(session_id)
            if self.result is None:
                # As in the Qt dialog: only failure screenshots saved automatically go out.
                report = self.manager.build_report(
                    self.snapshot,
                    description.strip(),
                    include_screenshot=False,
                )
                self._archives[report.report_id] = report.archive_path.resolve()
                self._archives = dict(list(self._archives.items())[-10:])
                self.result = {
                    "id": report.report_id,
                    "path": str(report.archive_path.resolve()),
                    "message": report.group_message,
                }
            return self.state()

    def finish(self, session_id, resume):
        if type(resume) is not bool:
            raise ValueError("恢复选项应为布尔值")
        with config_lock, self._lock:
            self._require(session_id)
            if resume:
                if not self.state()["can_resume"]:
                    raise ValueError("任务已改变或被手动暂停、停止，请关闭报告后手动操作")
                if self.legacy_check and (message := self.legacy_check()):
                    raise ValueError(message)
                self.manager.resume(self.snapshot, self.runtime.executor)
            self.snapshot = self.session_id = self.result = None
            self._resume_allowed = False
            self.runtime.ok.headless_app.diagnostic_pause = False
            return self.state()

    def reveal(self, report_id):
        from ok.util.explorer import reveal_in_explorer

        with self._lock:
            path = self._archives.get(report_id)
            if path is None or not path.is_file():
                raise ValueError("报告不存在，请重新生成")
            reveal_in_explorer(path)
        return {"ok": True}
