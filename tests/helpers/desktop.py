import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from ok.task.TaskExecutor import TaskExecutor
from ok.util.GlobalConfig import create_basic_options, create_notification_options

from src.config import config as bd2_config
from src.desktop.app import create_app

TOKEN = "a" * 43


ORIGIN = "http://127.0.0.1:32123"


GAME_WINDOW = {
    "imei": "pc_660888",
    "device": "windows",
    "nick": "BrownDust II",
    "address": "",
    "connected": True,
    "resolution": "1920x1080",
}


def make_executor():
    executor = TaskExecutor.__new__(TaskExecutor)
    executor.__dict__.update(
        scene=None,
        lock=threading.Lock(),
        _wake_condition=threading.Condition(),
        _wake_version=0,
        onetime_tasks=[],
        onetime_task_queue=[],
        trigger_tasks=[],
        current_task=None,
        current_job=None,
        _exclusive_owner=None,
        _job_queue=[],
        thread=None,
        paused=False,
        debug_mode=False,
        basic_options={},
        start=Mock(),
        reset_scene=Mock(),
    )
    return executor


class FakeDeviceManager:
    """The parts of ok-script's DeviceManager that capture_ui reads, with BD2's methods."""

    def __init__(self, device, capture="windows", capture_method=None):
        self.device = device
        self.config = {"capture": capture, "interaction": ""}
        self.capture_method = capture_method

    def get_preferred_device(self):
        return self.device

    def get_devices(self):
        return [self.device]

    def get_preferred_capture(self):
        available = self.available_capture_methods()
        return self.config["capture"] if self.config["capture"] in available else available[0]

    def available_capture_methods(self, device=None):
        return list(bd2_config["windows"]["capture_method"])

    def available_interaction_methods(self, device=None):
        return ["BD2Interaction"]

    def set_capture(self, capture):
        self.config["capture"] = capture


class FakeGlobalConfig:
    """ok-script's GlobalConfig with the two groups BD2 shows, held in memory."""

    def __init__(self):
        self.options = [create_basic_options(enable_blur=True), create_notification_options()]
        self.configs = {option.name: dict(option.default_config) for option in self.options}

    def get_all_visible_configs(self):
        return [(option.name, self.configs[option.name], option) for option in self.options]

    def get_config(self, name):
        return self.configs[name]


def make_client(test, device=GAME_WINDOW, window=None, launcher_version=None):
    test.enterContext(patch("ok.core.events.communicate.task.emit"))
    manager = FakeDeviceManager(device)
    manager.hwnd_window = window
    executor = make_executor()
    executor.global_config = FakeGlobalConfig()
    headless = SimpleNamespace(
        start_controller=SimpleNamespace(start=Mock(), cancel_start=Mock(), starting=False),
        overlay_state=lambda: {},
        tr=lambda text: text,
    )
    engine = SimpleNamespace(
        config={},
        task_executor=executor,
        device_manager=manager,
        headless_app=headless,
        start_runtime=Mock(),
        quit=Mock(),
        exit_event=threading.Event(),
    )
    app = create_app({}, token=TOKEN, origin=ORIGIN, ok_instance=engine, ui_dist=".")
    app.state.runtime._pyappify_module = SimpleNamespace(pyappify_version=launcher_version)
    test.manager, test.executor, test.headless = manager, executor, headless
    return test.enterContext(
        TestClient(app, base_url=ORIGIN, headers={"Authorization": f"Bearer {TOKEN}"})
    )
