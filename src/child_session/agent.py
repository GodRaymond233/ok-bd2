"""Restricted agent entry point, launched only in the Windows child session."""

import hmac
import json
import os
import sys
import threading
from pathlib import Path

# Startup-folder shortcuts invoke this file directly.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ok.child_session import protocol, win_api
from ok.child_session.agent_server import Agent, AgentLog, _single_instance

from src.child_session.input import MouseInput
from src.child_session.interaction import window_context


def private_json(path, data):
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, sid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    security = pywintypes.SECURITY_ATTRIBUTES()
    security.SECURITY_DESCRIPTOR = descriptor
    temporary = f"{path}.{os.getpid()}.tmp"
    handle = win32file.CreateFile(temporary, win32con.GENERIC_WRITE, 0, security,
                                 win32con.CREATE_ALWAYS, win32con.FILE_ATTRIBUTE_NORMAL, None)
    try:
        win32file.WriteFile(handle, json.dumps(data).encode("utf-8"))
    finally:
        handle.Close()
    os.replace(temporary, path)


class BD2Agent(Agent):
    def __init__(self, project_dir, log):
        if win_api.is_elevated():
            raise RuntimeError("子会话 agent 必须以普通权限运行")
        # The framework constructs its input implementation in Agent.__init__.
        from ok.child_session import agent_input

        agent_input.Input = MouseInput
        super().__init__(project_dir, log)
        self._command_lock = threading.Lock()
        self._connections = threading.BoundedSemaphore(4)
        self._launch = protocol.read_json(protocol.config_file(project_dir)).get("bd2_launch", {})

    def window_info(self):
        import win32process

        if self.window._hwnd and not self.window._matches(self.window._hwnd):
            self.window._hwnd = 0
        info = super().window_info()
        if info["hwnd"]:
            info["pid"] = win32process.GetWindowThreadProcessId(info["hwnd"])[1]
        return info

    def handle(self, cmd, req):
        with self._command_lock:
            if cmd in ("hello", "ping"):
                return {"ok": True, "bd2_agent": 1, "version": 1,
                        "session": self.session, "elevated": self.elevated}, None
            if cmd == "window_info":
                return self.window_info(), None
            if cmd == "frame":
                import numpy as np

                before = self.window_info()
                reply, payload = super().handle(cmd, req)
                after = self.window_info()
                if window_context(before) != window_context(after):
                    return {"ok": False}, None
                if payload and not np.any(np.frombuffer(payload, np.uint8)):
                    return {"ok": False}, None
                return dict(reply, context=list(window_context(after))), payload
            if cmd == "bd2_mouse":
                info = self.window_info()
                if (not info["exists"]
                        or tuple(req.get("context", ())) != window_context(info)):
                    return {"target_changed": True}, None
                operation = req.get("operation")
                allowed = {
                    "click": ("x", "y", "key", "down_time"),
                    "scroll": ("x", "y", "amount"),
                    "swipe": ("from_x", "from_y", "to_x", "to_y", "duration", "settle_time"),
                }
                if operation not in allowed:
                    raise ValueError("不支持的鼠标操作")
                getattr(self.input, operation)(
                    info["hwnd"], **{key: req[key] for key in allowed[operation] if key in req}
                )
                return {"ok": True}, None
            if cmd == "close_game":
                import psutil
                import win32con
                import win32gui

                info = self.window_info()
                if not info["exists"]:
                    return {"ok": True}, None
                if tuple(req.get("context", ())) != window_context(info):
                    raise ValueError("游戏窗口已变化，取消关闭")
                process = psutil.Process(info["pid"])
                if win_api.process_session_id(process.pid) != self.session:
                    raise ValueError("游戏不属于当前子会话")
                win32gui.PostMessage(info["hwnd"], win32con.WM_CLOSE, 0, 0)
                process.wait(timeout=8)
                return {"ok": True}, None
            if cmd == "launch":
                if (os.path.normcase(os.path.abspath(req.get("path") or ""))
                        != os.path.normcase(self._launch.get("path", ""))
                        or (req.get("args") or "") != self._launch.get("args", "")
                        or req.get("cwd") != self._launch.get("cwd")):
                    raise ValueError("只允许按已配置的参数启动 BD2 Starter")
                import psutil

                from src import GAME_EXE
                from src.game_path import get_launcher_exe_names

                names = {name.casefold() for name in get_launcher_exe_names()}
                names.update(name.casefold() for name in (
                    [GAME_EXE] if isinstance(GAME_EXE, str) else GAME_EXE
                ))
                for process in psutil.process_iter(["pid", "name"]):
                    if ((process.info["name"] or "").casefold() in names
                            and win_api.process_session_id(process.pid) != self.session):
                        raise RuntimeError("请先关闭其他会话中的 BD2 与 Starter，避免跨会话转交")
                from src.compat.starter_guard import starter_preflight_warning

                warning = starter_preflight_warning()
                if warning:
                    raise RuntimeError(warning)
                os.startfile(self._launch["path"], arguments=self._launch["args"],
                             cwd=self._launch["cwd"])
                return {"ok": True}, None
            if cmd == "starter_status":
                from src.compat.starter_guard import StarterWizardWatcher

                if not hasattr(self, "_starter_watcher"):
                    self._starter_watcher = StarterWizardWatcher()
                    self._starter_watcher.__enter__()
                from dataclasses import asdict

                return asdict(self._starter_watcher.latest), None
            if cmd == "quit":
                self.exit_event.set()
                return {"ok": True}, None
            raise ValueError("不支持的子会话命令")

    def serve_connection(self, conn):
        if not self._connections.acquire(blocking=False):
            conn.close()
            return
        try:
            conn.settimeout(15)
            with conn, conn.makefile("rb") as reader:
                authed = False
                while not self.exit_event.is_set():
                    line = reader.readline(65537)
                    if not line or len(line) > 65536:
                        return
                    req = json.loads(line)
                    cmd = req.pop("cmd", None)
                    if not authed:
                        if (cmd != "hello" or req.get("version") != protocol.PROTOCOL_VERSION
                                or not hmac.compare_digest(str(req.get("token", "")), self.token)):
                            return
                        authed = True
                    try:
                        reply, payload = self.handle(cmd, req)
                    except Exception:
                        # Request contents and exception strings may contain account information.
                        reply, payload = {"error": "子会话命令失败，请检查查看器及游戏状态"}, None
                    protocol.send_message(conn, reply, payload)
                    if cmd == "quit":
                        return
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self._connections.release()


def main():
    project = str(Path(sys.argv[sys.argv.index("--project") + 1]).resolve())
    os.chdir(project)
    if not win_api.in_child_session()[0] or win_api.is_elevated():
        return 1
    from src.compat.windows_graphics import enable_windows_10_wgc

    enable_windows_10_wgc()
    import ctypes
    from ctypes import wintypes

    ctypes.windll.kernel32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = _single_instance(project)
    if mutex is None:
        return 0
    agent = None
    try:
        protocol.write_json = private_json
        agent = BD2Agent(project, AgentLog(project))
        agent.serve()
    finally:
        if agent and hasattr(agent, "_starter_watcher"):
            agent._starter_watcher.__exit__(None, None, None)
        ctypes.windll.kernel32.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
