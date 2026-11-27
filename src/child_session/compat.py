"""Bind the framework's optional child-session path to the BD2 adapters."""

import os

import numpy as np
from ok.child_session import protocol, win_api
from ok.child_session.client import AgentClient
from ok.device.capture_methods.child_session import ChildSessionCaptureMethod

from src.child_session.interaction import BD2ChildSessionInteraction


class BD2AgentClient(AgentClient):
    def _connect(self):
        info = self.agent_info() or {}
        if (info.get("elevated") or info.get("session") != win_api.child_session_id()
                or win_api.process_session_id(int(info.get("pid") or 0)) != info.get("session")
                or info.get("session") is None):
            self.close()
            raise protocol.AgentError("没有可用的普通权限 BD2 子会话 agent")
        previous_socket = self._sock
        super()._connect()
        if self._sock is not previous_socket:
            protocol.send_message(self._sock, {"cmd": "ping"})
            reply, _ = protocol.read_message(self._reader)
            if reply.get("bd2_agent") != 1 or reply.get("elevated"):
                self.close()
                raise protocol.AgentError("子会话运行的不是 BD2 专用 agent")

    def request(self, cmd, **kwargs):
        # A lost reply cannot prove whether a click or launch already happened.
        with self._lock:
            try:
                self._connect()
                protocol.send_message(self._sock, dict(kwargs, cmd=cmd))
                reply, payload = protocol.read_message(self._reader)
                if reply.get("error"):
                    raise protocol.AgentError(reply["error"])
                return reply, payload
            except (OSError, ValueError, ConnectionError, protocol.AgentError) as error:
                self.close()
                raise protocol.AgentError("子会话连接或命令失败，请检查查看器") from error


class BD2ChildSessionCapture(ChildSessionCaptureMethod):
    input_context = None

    def do_get_frame(self):
        self.input_context = None
        try:
            reply, payload = self.client.request("frame")
            if not payload:
                return None
            width, height = int(reply["w"]), int(reply["h"])
            context = tuple(reply.get("context", ()))
            if len(context) != 5 or context[-2:] != (width, height):
                return None
            frame = np.frombuffer(payload, np.uint8).reshape(height, width, 3)
            self.input_context = context
            return frame
        except (protocol.AgentError, ValueError, KeyError):
            return None

    def get_abs_cords(self, x, y):
        raise RuntimeError("子会话坐标不得转换为宿主桌面坐标")


def enable_child_session():
    from ok import og
    from ok.core import start_controller
    from ok.device import DeviceManager as module
    from ok.device.capture_methods import child_session as capture_module

    if getattr(module.DeviceManager, "_bd2_child_session", False):
        return
    module.DeviceManager._bd2_child_session = True
    module.ChildSessionInteraction = BD2ChildSessionInteraction
    capture_module.ChildSessionCaptureMethod = BD2ChildSessionCapture

    def child_client(self):
        if self._child_session_client is None:
            self._child_session_client = BD2AgentClient(os.getcwd())
        return self._child_session_client

    def detach(self):
        client = self._child_session_client
        if client is not None:
            client.close()

    module.DeviceManager.child_session_client = child_client
    module.DeviceManager.end_child_session = detach
    original_stop = module.DeviceManager.stop_hwnd

    def stop_game(self):
        if self.config.get("capture") != "ChildSession":
            return original_stop(self)
        from src.child_session.interaction import window_context

        client = self.child_session_client()
        return client.call("close_game", context=list(window_context(client.call("window_info"))))

    module.DeviceManager.stop_hwnd = stop_game
    original_interaction = module.DeviceManager.set_interaction

    def set_interaction(self, interaction):
        child = self.config.get("capture") == "ChildSession"
        if child != (interaction == "ChildSession"):
            raise ValueError("子会话截图与输入必须成对选择")
        return original_interaction(self, interaction)

    module.DeviceManager.set_interaction = set_interaction
    original_args = start_controller.StartController._launch_arguments

    def arguments(self, device):
        args = original_args(self, device)
        if og.device_manager.uses_child_session():
            from src.compat.starter_launch import starter_launch_arguments

            args = starter_launch_arguments(og.device_manager.get_exe_path(device), args)
        return args

    start_controller.StartController._launch_arguments = arguments
    original_start = start_controller.StartController._start_child_session

    def start(self, device, initial_refresh_done=False):
        from ok.core.events import communicate

        client = og.device_manager.child_session_client()
        if client.try_call("ping") is None:
            communicate.starting_emulator.emit(
                True, "请先在设备页连接子会话，完成 Windows 登录和游戏设置", 0
            )
            return False
        return original_start(self, device, initial_refresh_done)

    start_controller.StartController._start_child_session = start


class RemoteStarterWatcher:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    @property
    def latest(self):
        from time import monotonic

        from src.compat.starter_update import StarterObservation

        try:
            return StarterObservation(**self.client.call("starter_status"))
        except (protocol.AgentError, TypeError):
            return StarterObservation(state="unknown", observed_at=monotonic())
