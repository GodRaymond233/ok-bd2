"""The real service layer behind desktop_probe.cjs, with the game start replaced.

Runs ``main.py --service``'s path against the configs in ``OKBD2_DESKTOP_FIXTURE``. The start
controller is a stand-in, so the probe can press start and pause without starting the game
or sending it any input. ``/api/probe/*`` lets the probe read and set what it checks.
"""

import os
import sys
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
        app = original(*args, **kwargs)
        runtime = app.state.runtime
        state = {"starts": 0, "running": False, "current_task": None}
        original_status = runtime.status

        def status():
            return dict(original_status(), running=state["running"],
                        current_task=state["current_task"])

        def safe_start(*args, **kwargs):
            state["starts"] += 1
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
            return {"ok": True}

        @app.post("/api/probe/crash")
        def crash():
            os._exit(77)

        return app

    desktop.create_app = create
    return dict(config, config_folder=os.environ["OKBD2_DESKTOP_FIXTURE"], update_pyappify=None)


start_application(load_config)
