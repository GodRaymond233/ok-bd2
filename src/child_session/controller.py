"""Electron-facing setup and lifecycle of the experimental child session."""

import atexit
import os
import subprocess
import sys
import threading
from pathlib import Path

from ok.child_session import host, protocol, win_api

from src.child_session.agent import private_json
from src.child_session.compat import BD2AgentClient
from src.compat.starter_launch import starter_launch_arguments


class ChildSessionController:
    def __init__(self, manager):
        self.manager = manager
        self.project = str(Path.cwd())
        self.client = BD2AgentClient(self.project)
        self.viewer = None
        self.lock = threading.RLock()
        atexit.register(self.close)

    def state(self):
        ready = self.client.try_call("ping") is not None
        return {
            "enabled": win_api.is_enabled(),
            "selected": self.manager.config.get("capture") == "ChildSession",
            "ready": ready,
            "viewer": self.viewer is not None and self.viewer.poll() is None,
            "session": win_api.child_session_id(),
            "resolution": [1920, 1080],
        }

    def enable(self):
        if win_api.is_enabled():
            return self.state()
        import ctypes

        arguments = subprocess.list2cmdline(["-m", "src.child_session.viewer", "--enable"])
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, arguments, self.project, 0
        )
        if result <= 32:
            raise ValueError("未能开启子会话，请完成 Windows 管理员授权")
        return self.state()

    def connect(self, view_only=True):
        with self.lock:
            if not win_api.is_enabled():
                raise ValueError("请先开启 Windows 子会话并完成管理员授权")
            if win_api.in_child_session()[0]:
                raise ValueError("请从宿主 Windows 桌面启动 ok-bd2")
            if self.viewer is not None and self.viewer.poll() is None:
                raise ValueError("子会话查看器已经打开，请在该窗口中继续")
            device = self.manager.get_preferred_device()
            if not device or device.get("device") != "windows":
                raise ValueError("请先选择 PC 游戏设备")
            path = self.manager.get_exe_path(device)
            if not path or not Path(path).is_file():
                raise ValueError("请先配置正确的 BD2 Starter 位置")
            path = str(Path(path).resolve())
            data = {
                "window": host.window_spec(self.manager.windows_capture_config),
                "close_apps": [], "elevate": False,
                "bd2_launch": {"path": path, "args": starter_launch_arguments(path) or "",
                               "cwd": os.path.dirname(path)},
            }
            private_json(protocol.config_file(self.project), data)
            host.agent_script = lambda: str(Path(__file__).with_name("agent.py"))
            host.install_autostart(self.project)
            command = [sys.executable, "-m", "src.child_session.viewer"]
            if view_only:
                command.append("--view-only")
            try:
                self.viewer = subprocess.Popen(command, cwd=self.project)
            except Exception:
                host.remove_autostart(self.project)
                raise
            self.manager.set_capture("ChildSession")
            return self.state()

    def disconnect(self):
        with self.lock:
            if self.viewer is not None and self.viewer.poll() is None:
                self.viewer.terminate()
                self.viewer.wait(timeout=5)
            self.viewer = None
            host.remove_autostart(self.project)

    def logoff(self):
        with self.lock:
            self.disconnect()
            session = win_api.child_session_id()
            if session is not None and not win_api.logoff(session, wait=False):
                raise RuntimeError("子会话注销失败")
            self.client.close()
            self.manager.set_capture("windows")
            return self.state()

    def close(self):
        self.disconnect()
        self.client.close()
