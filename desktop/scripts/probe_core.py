"""Desktop core for desktop/scripts/lifecycle_probe.py: the real service layer, no game.

``service`` (default) runs ``src.desktop.service.run_service`` as ``main.py --service``
does, except that the ok-script engine is a stand-in (no game window, no OCR) and the
executor lock, legacy scan and dependency check are skipped.  As in ``main.py``, the
configuration step imports ``src.config`` and with it the native modules (cv2, numpy) while
the shell's commands are already being read; the stand-in engine is built after it.  Every
exit request reaching ``ShutdownController.request`` is appended to PROBE_MARKER as a JSON
line.  Options:

    PROBE_GAME=1               start a sleeping child first, the way ok-script starts the game
    PROBE_SLOW_START=<seconds> stall the configuration step (a slow first start)
    PROBE_CRASH_AFTER_READY=1  end the process abruptly right after ``ready``
    PROBE_SITE=<dir>           extra site directory (a standalone interpreter using the venv)

``mutex-holder`` / ``mutex-contender`` (Windows): the holder takes ok-script's per-directory
mutex like an older ok-bd2 would; the contender builds the service's ok-script runtime in the
same directory and reports whether ok-script's directory mutex check ran.
"""

import json
import os
import site
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
GAME_TOKEN = "okbd2-probe-game"
os.environ["OK_BD2_UI"] = "service"
if os.environ.get("PROBE_SITE"):
    site.addsitedir(os.environ["PROBE_SITE"])
sys.path.insert(0, str(ROOT))


def record(**fields):
    marker = os.environ.get("PROBE_MARKER")
    if marker:
        with open(marker, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": time.time(), "pid": os.getpid(), **fields}) + "\n")


def create_time(pid):
    import psutil

    return psutil.Process(pid).create_time()


def stand_in_engine():
    from ok.task.TaskExecutor import TaskExecutor

    executor = TaskExecutor.__new__(TaskExecutor)
    executor.__dict__.update(
        lock=threading.Lock(),
        onetime_tasks=[],
        onetime_task_queue=[],
        trigger_tasks=[],
        current_task=None,
        thread=None,
        paused=False,
        basic_options={},
    )
    exit_event = threading.Event()
    devices = SimpleNamespace(
        get_preferred_device=lambda: None,
        get_devices=lambda: [],
        get_preferred_capture=lambda: "windows",
        refresh=lambda: True,
    )
    return SimpleNamespace(
        config={},
        task_executor=executor,
        device_manager=devices,
        headless_app=SimpleNamespace(
            start_controller=SimpleNamespace(start=Mock(), starting=False),
            overlay_state=lambda: {},
            tr=lambda text: text,
        ),
        start_runtime=Mock(),
        quit=exit_event.set,
        exit_event=exit_event,
        get_task=lambda name: (_ for _ in ()).throw(ValueError(name)),
    )


def run_service_probe():
    from src.desktop import service

    record(event="start", create_time=create_time(os.getpid()))
    original_request = service.ShutdownController.request

    def recorded_request(controller, reason):
        record(event="request", reason=reason)
        return original_request(controller, reason)

    service.ShutdownController.request = recorded_request
    if os.environ.get("PROBE_CRASH_AFTER_READY") == "1":
        original_send = service.ProtocolChannel.send

        def send_then_crash(channel, message_type, **fields):
            original_send(channel, message_type, **fields)
            if message_type == "ready":
                record(event="crash")
                threading.Timer(0.3, os._exit, args=(3,)).start()

        service.ProtocolChannel.send = send_then_crash

    def load_config():
        record(event="config", native_preloaded="numpy" in sys.modules)
        import src.config  # noqa: F401  the first native imports, as in main.py (F5)

        if os.environ.get("PROBE_GAME") == "1":
            game = subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep(300)  # {GAME_TOKEN}"]
            )
            record(event="game", game_pid=game.pid, create_time=create_time(game.pid))
        delay = float(os.environ.get("PROBE_SLOW_START") or 0)
        if delay:
            record(event="slow-start", seconds=delay)
            time.sleep(delay)
        return {"version": "probe"}

    def app_factory(config, **kw):
        from src.desktop.app import create_app

        ui_dist = ROOT / "src" / "desktop" / "ui_dist"
        return create_app(config, ok_instance=stand_in_engine(), ui_dist=ui_dist, **kw)

    return service.run_service(
        load_config,
        dependency_check=lambda: None,
        lock_acquirer=lambda mode: (True, None),
        guard_installer=lambda: None,
        app_factory=app_factory,
        legacy=SimpleNamespace(
            instances=lambda force=False: [], blocking_message=lambda force=False: None
        ),
    )


def mutex_holder(entry):
    sys.argv[0] = entry
    from ok.util import process as process_tools

    if not process_tools.check_mutex(wait_time=0.1):
        raise SystemExit(2)
    print("PROBE_HOLDER_READY", flush=True)
    time.sleep(60)


def mutex_contender(entry):
    sys.argv[0] = entry
    import ok

    from src.desktop.okweb.app import WebRuntime
    from src.desktop.service import service_config

    class ReachedAfterMutex(Exception):
        pass

    calls = []

    def check_mutex(*args, **kwargs):
        calls.append(True)
        from ok.util import process as process_tools

        # Same behaviour as ok-script, with the waits shortened for the probe.
        return process_tools.check_mutex(wait_time=0.1, kill_wait_time=2)

    def stop_here(*args, **kwargs):
        raise ReachedAfterMutex

    ok.check_mutex = check_mutex
    ok.config_logger = stop_here
    reached = False
    try:
        WebRuntime(service_config({"debug": False}))
    except ReachedAfterMutex:
        reached = True
    print("PROBE_CONTENDER " + json.dumps({"check_mutex_called": bool(calls), "reached": reached}))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "service"
    if mode == "mutex-holder":
        mutex_holder(sys.argv[2])
    elif mode == "mutex-contender":
        mutex_contender(sys.argv[2])
    else:
        sys.exit(run_service_probe())
