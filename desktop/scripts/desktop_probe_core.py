"""The real service layer behind desktop_probe.cjs, with the game start replaced.

Runs ``main.py --service``'s path against the configs in ``OKBD2_DESKTOP_FIXTURE``. The start
controller is a stand-in, so the probe can press start and pause without starting the game
or sending it any input. ``/api/probe/*`` lets the probe read and set what it checks.
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.compat.startup import start_application  # noqa: E402


def load_config():
    from ok.core.events import communicate

    from src.config import config
    from src.desktop import app as desktop

    original = desktop.create_app

    def create(*args, **kwargs):
        from src.tasks.run_history import RunHistoryStore, set_default_store

        if fixture := os.environ.get("OKBD2_DESKTOP_FIXTURE"):
            set_default_store(RunHistoryStore(str(Path(fixture) / "task_run_history.json")))
        app = original(*args, **kwargs)
        runtime = app.state.runtime
        if os.environ.get("OKBD2_MAINTENANCE_PROBE"):
            from types import SimpleNamespace

            from pyappify import is_greater_version

            runtime._pyappify_module = SimpleNamespace(
                pyappify_executable=str(Path.cwd().parents[3] / "ok-bd2.exe"),
                pyappify_version="1.2.3", app_version="v0.9.7",
                get_version_list=lambda **kwargs: [{"version": "v0.9.8", "update_note": ["probe"]}],
                calculate_update_notes=lambda *args: ["probe"], hide_pyappify=lambda: None,
                is_greater_version=is_greater_version,
            )
        state = {"starts": 0, "running": False, "current_task": None}
        original_status = runtime.status

        def status():
            return dict(original_status(), running=state["running"],
                        current_task=state["current_task"])

        def safe_start(task=None, exit_after=False):
            state["starts"] += 1
            state["last_start_task"] = type(task).__name__ if task else None
            state["running"] = True
            runtime.executor.paused = False
            communicate.executor_paused.emit(False)
            return True

        runtime.status = status
        runtime.ok.headless_app.start_controller.start = safe_start

        @app.get("/api/probe/state")
        def read():
            manager = runtime.ok.headless_app.notification_manager
            return dict(state, paused=runtime.executor.paused,
                        notifier=type(manager.system_notifier).__name__)

        @app.post("/api/probe/guard")
        def guard(body: dict):
            runtime.ok.headless_app.diagnostic_pause = body["active"]
            return {"ok": True}

        @app.post("/api/probe/current-task")
        def current_task(body: dict):
            state["current_task"] = body["name"]
            state["running"] = body.get("running", state["running"])
            # The page refreshes on this event, as it does when a real run starts or ends.
            communicate.executor_paused.emit(runtime.executor.paused)
            return {"ok": True}

        @app.post("/api/probe/batch-progress")
        def batch_progress(body: dict):
            from src.tasks.DailyBatchTask import DailyBatchTask

            batch = runtime.executor.get_task_by_class(DailyBatchTask)
            if body.get("stop"):
                from src.compat.executor_cleanup import _record_interrupted

                _record_interrupted(batch, False, "已中止")
                state.update(current_task=None, running=False)
                communicate.task.emit(batch)
                communicate.notification.emit("Stopped", batch.name, False, False, None, None, None)
                return {"ok": True}
            step = body["step"]
            if step == 0:
                batch._enabled = True
                batch.start_time = time.time()
                batch.info_clear()
                state["current_task"] = batch.name
            child = batch.child_tasks[step]
            batch.info_set("当前子任务", child.config_key)
            batch.info_set("完成", "、".join(item.config_key for item in batch.child_tasks[:step]))
            child_task = runtime.executor.get_task_by_class(child.task_class)
            child_task.info_set("状态", "探针阶段")
            child_task.info_set("目标卡带", "Q_cp7: match=0.981, pixel=0.976, zncc=-1.000")
            child_task.info_set("Warning", "探针警告")
            if step == 0:
                communicate.task.emit(batch)
            # Advancing info alone matches real batch execution; there is no task event.
            return {"name": runtime.executor.get_task_by_class(child.task_class).name}

        @app.post("/api/probe/crash")
        def crash():
            os._exit(77)

        @app.post("/api/probe/failure")
        def failure(body: dict):
            evidence = app.state.reports.evidence
            assert evidence.failure_notifier is not None
            evidence._notify_failure({
                "event": "failed", "parent": body.get("parent"), "run": body["run"],
                "task_name": "探针日常", "info": {"失败": "探针子任务"},
            })
            return {"ok": True}

        return app

    desktop.create_app = create
    config = dict(config, windows=dict(
        config["windows"], exe=f"okbd2-probe-{os.getpid()}-absent.exe",
        hwnd_class=f"okbd2-probe-{os.getpid()}-absent", start_exe=False,
        calculate_pc_exe_path=lambda: "",
    ))
    if os.environ.get("OKBD2_MAINTENANCE_PROBE"):
        return dict(config, config_folder="configs", version="v0.9.7", update_pyappify=None)
    return dict(config, config_folder=os.environ["OKBD2_DESKTOP_FIXTURE"], update_pyappify=None)


start_application(load_config)
