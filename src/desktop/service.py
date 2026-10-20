"""Desktop core service: the automation core behind a loopback API for the Electron shell.

Started by the shell as ``python main.py --service`` (or ``main_debug.py --service``).
Lifecycle travels over stdin/stdout (see ``src.desktop.protocol``); business traffic
goes over HTTP and one WebSocket on ``127.0.0.1``.

Order of startup: handshake (token within 10 s) -> executor lock -> dependency check ->
configuration -> loopback socket -> uvicorn (ok-script runtime starts in the lifespan) ->
``ready`` line.  Shutdown starts on a ``shutdown`` command, on stdin EOF (the shell is
gone) or when the parent process disappears; the core then starts exiting within
``PARENT_POLL_SECONDS`` + scheduling slack and a watchdog forces the exit after
``EXIT_WATCHDOG_SECONDS``.  The core never terminates other processes.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass

from src.desktop import protocol

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class HandshakeError(Exception):
    pass


@dataclass(frozen=True)
class Handshake:
    token: str
    parent_pid: int | None


def parse_handshake(line: str | None) -> Handshake:
    if not line or not line.strip():
        raise HandshakeError("外壳没有发送握手信息")
    try:
        data = json.loads(line)
    except ValueError as error:
        raise HandshakeError("握手信息不是有效的 JSON") from error
    if not isinstance(data, dict) or data.get("v") != protocol.PROTOCOL_VERSION:
        raise HandshakeError("外壳与核心的协议版本不一致")
    token = data.get("token")
    if (
        not isinstance(token, str)
        or not protocol.TOKEN_MIN_LENGTH <= len(token) <= protocol.TOKEN_MAX_LENGTH
        or not TOKEN_PATTERN.fullmatch(token)
    ):
        raise HandshakeError("启动凭据无效")
    parent_pid = data.get("parent_pid")
    if parent_pid is not None and (
        isinstance(parent_pid, bool) or not isinstance(parent_pid, int) or parent_pid <= 0
    ):
        raise HandshakeError("父进程编号无效")
    return Handshake(token=token, parent_pid=parent_pid)


def read_handshake(stream, timeout: float = protocol.HANDSHAKE_TIMEOUT_SECONDS) -> Handshake:
    box = {}

    def read():
        try:
            box["line"] = stream.readline()
        except Exception as error:  # closed or broken pipe
            box["error"] = error

    reader = threading.Thread(target=read, name="HandshakeReader", daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        raise HandshakeError(f"{timeout:g} 秒内没有收到握手信息")
    if "error" in box:
        raise HandshakeError(f"读取握手信息失败：{box['error']}")
    return parse_handshake(box.get("line"))


class ProtocolChannel:
    """Writes prefixed lifecycle messages for the shell; never raises."""

    def __init__(self, stream):
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, message_type: str, **fields) -> None:
        line = protocol.LINE_PREFIX + json.dumps({"type": message_type, **fields}) + "\n"
        with self._lock:
            try:
                self._stream.write(line)
                self._stream.flush()
            except (OSError, ValueError):
                pass  # the shell is gone; parent supervision handles the rest

    def fatal(self, code: str, message: str, **fields) -> None:
        self.send("fatal", code=code, message=message, **fields)


class ShutdownController:
    """Starts the safe exit exactly once, whatever asked for it first."""

    def __init__(
        self, channel: ProtocolChannel, quit_runtime, logger, watchdog=None, clock=time.monotonic
    ):
        self._channel = channel
        self._quit_runtime = quit_runtime
        self._logger = logger
        self._watchdog = watchdog
        self._clock = clock
        self._lock = threading.Lock()
        self.reason: str | None = None
        self.started_at: float | None = None

    @property
    def started(self) -> bool:
        return self.reason is not None

    def request(self, reason: str) -> bool:
        with self._lock:
            if self.reason is not None:
                return False
            self.reason = reason
            self.started_at = self._clock()
        self._logger.info(f"开始安全退出（{reason}）")
        self._channel.send("exiting", reason=reason)
        if self._watchdog is not None:
            self._watchdog(grace_period=protocol.EXIT_WATCHDOG_SECONDS, exit_code=0)
        try:
            self._quit_runtime()
        except Exception as error:
            self._logger.error(f"退出时停止运行时失败：{error}")
        return True


class CommandReader:
    """Reads shell commands after the handshake; EOF means the shell is gone."""

    def __init__(self, stream, on_shutdown, logger):
        self._stream = stream
        self._on_shutdown = on_shutdown
        self._logger = logger
        self.thread = threading.Thread(target=self._run, name="ShellCommands", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        while True:
            try:
                line = self._stream.readline()
            except Exception as error:
                self._on_shutdown(f"stdin-error: {error}")
                return
            if not line:
                self._on_shutdown("stdin-closed")
                return
            try:
                command = json.loads(line)
            except ValueError:
                self._logger.warning(f"忽略无法解析的外壳指令：{line[:80]!r}")
                continue
            if isinstance(command, dict) and command.get("cmd") == "shutdown":
                self._on_shutdown(f"shell:{command.get('reason') or 'shutdown'}")
            else:
                self._logger.warning(f"忽略未知的外壳指令：{command!r:.80}")


def _process_identity(pid: int):
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:
        return None


class ParentWatch:
    """Polls the parent process; a vanished or replaced parent starts the exit."""

    def __init__(
        self,
        parent_pid: int,
        on_lost,
        interval=protocol.PARENT_POLL_SECONDS,
        identity=_process_identity,
    ):
        self.parent_pid = parent_pid
        self._on_lost = on_lost
        self._interval = interval
        self._identity = identity
        self._expected = identity(parent_pid)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ParentWatch", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self._interval):
            current = self._identity(self.parent_pid)
            if current is None or (self._expected is not None and current != self._expected):
                self._on_lost("parent-exited")
                return


class CoreServer:
    """uvicorn on an already bound loopback socket, running in its own thread."""

    def __init__(self, app, server_socket):
        import uvicorn

        self.socket = server_socket
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_config=None,
                access_log=False,
                ws="wsproto",
                lifespan="on",
                timeout_graceful_shutdown=protocol.GRACEFUL_SERVER_SHUTDOWN_SECONDS,
            )
        )
        self.failures = []
        self.thread = threading.Thread(target=self._serve, name="CoreServer", daemon=True)

    def _serve(self):
        try:
            self.server.run(sockets=[self.socket])
        except BaseException as error:  # SystemExit from a failed lifespan included
            self.failures.append(error)

    def start(self, timeout: float) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive():
                cause = self.failures[0] if self.failures else None
                raise RuntimeError("本机服务启动失败") from cause
            if time.monotonic() >= deadline:
                raise RuntimeError(f"本机服务 {timeout:g} 秒内没有就绪")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True

    def wait(self) -> None:
        while self.thread.is_alive():
            self.thread.join(timeout=0.5)


def route_console_logs(original, replacement) -> int:
    """Point logging handlers created before the redirect away from the protocol stream."""
    import logging

    moved = 0
    loggers = [logging.getLogger()] + [
        item
        for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    ]
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is original:
                handler.setStream(replacement)
                moved += 1
    return moved


def bind_loopback():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    return server_socket


def service_config(config: dict) -> dict:
    """ok-script's Web runtime pops ``gui`` itself; keep the Qt entry flags out."""
    prepared = dict(config)
    prepared.pop("gui", None)
    prepared["use_gui"] = False
    return prepared


def run_service(
    config_loader,
    configure=None,
    *,
    stdin=None,
    stdout=None,
    ready_timeout=protocol.READY_TIMEOUT_SECONDS,
    dependency_check=None,
    lock_acquirer=None,
    guard_installer=None,
    app_factory=None,
    watchdog=None,
    legacy=None,
):
    """Run the core service; returns the process exit code (see ``src.desktop.protocol``)."""
    from ok import Logger

    from src.executor_guard import (
        acquire_executor_lock,
        install_executor_guards,
        legacy_guard,
        refusal_message,
        release_executor_lock,
    )

    logger = Logger.get_logger("desktop_service")
    stdin = stdin if stdin is not None else sys.stdin
    if stdout is None:
        # Keep stdout for lifecycle messages only; library prints go to stderr.
        stdout = sys.stdout
        sys.stdout = sys.stderr
        route_console_logs(stdout, sys.stderr)
    channel = ProtocolChannel(stdout)

    try:
        handshake = read_handshake(stdin)
    except HandshakeError as error:
        logger.error(f"握手失败：{error}")
        channel.fatal("handshake", str(error))
        return protocol.EXIT_HANDSHAKE_FAILED

    acquired, holder = (lock_acquirer or acquire_executor_lock)("service")
    if not acquired:
        message = refusal_message(holder)
        channel.fatal("executor-locked", message, holder_pid=holder.pid if holder else None)
        return protocol.EXIT_EXECUTOR_LOCKED

    server = None
    try:
        if dependency_check is None:
            from src.compat.dependency_guard import ensure_core_dependencies

            dependency_check = ensure_core_dependencies
        try:
            dependency_check()
        except SystemExit as exit_request:
            channel.fatal("dependencies", "核心依赖缺失或已修复，需要重新启动 ok-bd2")
            return int(exit_request.code or protocol.EXIT_STARTUP_FAILED)

        config = config_loader()
        if configure is not None:
            configure(config)
        (guard_installer or install_executor_guards)()
        guard = legacy if legacy is not None else legacy_guard()
        legacy = guard.instances(force=True)
        if legacy:
            logger.warning(guard.blocking_message())

        server_socket = bind_loopback()
        port = server_socket.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        controller_box = {}

        def request_shutdown(reason):
            controller = controller_box.get("controller")
            if controller is not None:
                controller.request(reason)

        if app_factory is None:
            from src.desktop.app import create_app as app_factory
        app = app_factory(
            service_config(config),
            token=handshake.token,
            origin=origin,
            legacy_check=lambda: guard.blocking_message(force=True),
            legacy_instances=lambda: guard.instances(),
            request_shutdown=request_shutdown,
        )
        runtime = app.state.runtime
        try:
            from ok.ui.web.server import _configure_server_logging

            _configure_server_logging()
        except Exception as error:
            logger.warning(f"could not route server logs: {error}")
        server = CoreServer(app, server_socket)

        if watchdog is None:
            from ok.util.process import start_exit_watchdog as watchdog
        controller = ShutdownController(channel, runtime.ok.quit, logger, watchdog=watchdog)
        controller_box["controller"] = controller

        def stop_server_when_runtime_exits():
            runtime.ok.exit_event.wait()
            controller.request("runtime-exit")
            server.stop()

        threading.Thread(
            target=stop_server_when_runtime_exits, name="RuntimeExit", daemon=True
        ).start()
        server.start(ready_timeout)
    except Exception as error:
        logger.error(f"核心服务启动失败：{error}")
        channel.fatal("startup", f"核心服务启动失败：{error}")
        if server is not None:
            server.stop()
        release_executor_lock()
        return protocol.EXIT_STARTUP_FAILED

    channel.send(
        "ready",
        port=port,
        pid=os.getpid(),
        api=protocol.API_VERSION,
        core_version=str(config.get("version") or ""),
    )
    logger.info(f"核心服务就绪：{origin}")
    CommandReader(stdin, controller.request, logger).start()
    parent_pid = handshake.parent_pid or os.getppid()
    watch = ParentWatch(parent_pid, controller.request).start()
    server.wait()
    watch.stop()
    controller.request("server-stopped")
    release_executor_lock()
    return protocol.EXIT_OK
