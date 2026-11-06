"""Desktop diagnostics and the framework's optional local development tools."""

import asyncio
import threading
import traceback
from concurrent.futures import CancelledError
from contextlib import asynccontextmanager

import cv2
from fastapi import HTTPException
from fastapi.responses import Response
from ok import Config
from ok.core.code_runner import CodeSession, submit_call
from ok.core.events import communicate
from ok.util.GlobalConfig import ENABLE_MCP_SERVER

from src.desktop.config_store import config_lock

MAX_CODE_CHARS = 128_000
MAX_OUTPUT_CHARS = 100_000
TOOL_OWNER = "desktop-tools"


def executor_busy(executor):
    job = executor.current_job
    return (
        executor.exclusive_owner is not None or bool(job and not job.future.done())
        or bool(executor._job_queue)
    )


class DesktopTools:
    def __init__(self, runtime, legacy_check=None):
        self.runtime = runtime
        self.legacy_check = legacy_check
        self.lock = threading.RLock()
        self.mcp_lock = threading.RLock()
        self.session = None
        self.code_config = None
        self.job = None
        self.run_id = 0
        self.kind = None
        self.output = ""
        self.truncated = False
        self.stopping = False
        self.hide_result = False
        self.image = None
        self.mcp_error = None

    def guard(self, *, allow_tools=False, check_tasks=True):
        self.runtime.ensure_config_write(allow_tools=allow_tools)
        if self.runtime.ok.headless_app.start_controller.starting:
            raise ValueError("请先等待游戏启动结束，再使用工具")
        if check_tasks:
            self.runtime.ensure_task_slot()
        if self.legacy_check and (message := self.legacy_check()):
            raise ValueError(message)

    def preferences(self):
        if self.code_config is None:
            self.code_config = Config("run_code", {"code": "", "target_task": ""})
        return self.code_config

    def _output(self, text):
        with self.lock:
            remaining = MAX_OUTPUT_CHARS - len(self.output)
            self.output += text[:remaining]
            self.truncated |= len(text) > remaining

    def _clear_task(self, *_args):
        if self.session is not None:
            self.session.namespace.pop("task", None)

    def state(self):
        with self.lock:
            job = self.job
            active = job is not None and not job.future.done()
            result = None
            if job is not None and not active:
                try:
                    value = job.future.result()
                    result = {
                        "value": None if self.hide_result else value.value,
                        "error": None if self.hide_result else value.error,
                        "cancelled": value.cancelled,
                        "duration": value.duration,
                        "details": value.raw if self.kind in {"ocr", "check"} else None,
                    }
                except CancelledError:
                    result = {"value": None, "error": None, "cancelled": True,
                              "duration": 0, "details": None}
                except Exception as error:
                    result = {"value": None, "error": "".join(traceback.format_exception(error)),
                              "cancelled": False, "duration": 0, "details": None}
            preferences = self.preferences()
            return {
                "run_id": self.run_id, "kind": self.kind, "active": active,
                "stopping": active and self.stopping, "output": self.output,
                "truncated": self.truncated, "result": result,
                "code": preferences.get("code", ""),
                "task_class": preferences.get("target_task", ""),
                "image_url": f"/api/desktop/tools/ocr/image?run={self.run_id}"
                if self.image is not None else None,
            }

    def run(self, body):
        kind = body.get("kind")
        if kind not in ("code", "ocr", "check"):
            raise ValueError("未知的工具操作")
        with config_lock:
            self.guard()
            task = None
            if kind == "code":
                code = body.get("code")
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("请先输入要运行的代码")
                if len(code) > MAX_CODE_CHARS:
                    raise ValueError("代码太长，请改为脚本后运行")
                if not isinstance(body.get("task", ""), str):
                    raise ValueError("任务名称无效")
                if body.get("task"):
                    task, _ = self.runtime.ok.get_task(body["task"])
                if self.session is None:
                    self.session = CodeSession(self.runtime.executor, owner=TOOL_OWNER)
                    communicate.task_list_updated.connect(self._clear_task)
                self.session.namespace.pop("task", None)
                preferences = self.preferences()
                preferences["code"] = code
                preferences["target_task"] = type(task).__name__ if task else ""
            elif kind == "ocr":
                self._capture_method()
            executor = self.runtime.executor
            executor.acquire_exclusive(TOOL_OWNER)
            try:
                with self.lock:
                    self.run_id += 1
                    self.kind = kind
                    self.output = ""
                    self.truncated = self.stopping = self.hide_result = False
                    self.image = None
                    if kind == "code":
                        self.job = self.session.submit(code, task=task, on_output=self._output)
                    else:
                        self.job = submit_call(
                            executor, self.ocr if kind == "ocr" else self.basic_check,
                            name=f"desktop:{kind}", owner=TOOL_OWNER, on_output=self._output,
                        )
                self.job.future.add_done_callback(
                    lambda _: executor.release_exclusive(TOOL_OWNER)
                )
            except BaseException:
                executor.release_exclusive(TOOL_OWNER)
                raise
        return self.state()

    def cancel(self):
        with self.lock:
            if self.job is not None and not self.job.future.done():
                self.stopping = True
                self.job.cancel()
        return self.state()

    def clear(self):
        with self.lock:
            self.output = ""
            self.truncated = False
            self.hide_result = bool(self.job and self.job.future.done())
        return self.state()

    def reset(self):
        with config_lock:
            self.guard()
            if self.session is not None:
                self.session.reset()
        return self.state()

    def _capture_method(self):
        method = self.runtime.device_manager.capture_method
        if method is None or not method.connected():
            raise ValueError("请先连接游戏窗口和截图方式，再识别文字")
        return method

    def ocr(self):
        frame = self._capture_method().get_frame()
        if frame is None:
            raise ValueError("没有截到画面，请确认游戏窗口可见")
        tasks = self.runtime.executor.get_all_tasks()
        if not tasks:
            raise ValueError("没有可用的识别任务")
        boxes = tasks[0].ocr(frame=frame, log=True, screenshot=True) or []
        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            raise ValueError("画面编码失败")
        with self.lock:
            self.image = encoded.tobytes()
        return {
            "width": frame.shape[1], "height": frame.shape[0],
            "boxes": [{"text": str(box.name), "x": int(box.x), "y": int(box.y),
                       "width": int(box.width), "height": int(box.height),
                       "confidence": float(box.confidence)} for box in boxes],
        }

    def basic_check(self):
        from src.tasks.BD2OneTimeTask import BD2OneTimeTask

        executor = self.runtime.executor
        task = BD2OneTimeTask(executor=executor, app=self.runtime.ok.headless_app)
        task.after_init(executor=executor, scene=executor.scene)
        task.post_init()
        task.run()
        Config("BD2StatusTab", {"最近操作": ""})["最近操作"] = "运行基础检查"
        device = self.runtime.device_manager.get_preferred_device()
        method = self.runtime.device_manager.capture_method
        return {"checks": [
            {"name": "运行服务", "ok": True,
             "detail": str(self.runtime.ok.config.get("version", ""))},
            {"name": "任务列表", "ok": bool(executor.get_all_tasks()),
             "detail": str(len(executor.get_all_tasks()))},
            {"name": "游戏窗口", "ok": bool(device and device.get("connected")),
             "detail": "已连接" if device and device.get("connected") else "未连接游戏"},
            {"name": "截图方式", "ok": bool(method and method.connected()),
             "detail": type(method).__name__ if method else "尚未选择截图方式"},
        ]}

    def start_mcp(self):
        from ok.core.mcp_server import LocalMcpServer

        ok = self.runtime.ok
        config = ok.config.get("mcp") or {}
        if ok._mcp_server is not None or not (
            config.get("enabled", False) or self.runtime.executor.basic_options.get(
                ENABLE_MCP_SERVER, False
            )
        ):
            return
        tools = self

        class DesktopMcpServer(LocalMcpServer):
            def _prepare_device(self):
                # The runner already owns the executor for file runs. Recheck the
                # UI gates at the point where device preparation can perform input.
                with config_lock:
                    tools.guard(allow_tools=True, check_tasks=False)
                    return super()._prepare_device()

        server = DesktopMcpServer(ok, config)
        for name in ("run_task", "reload_tasks"):
            operation = getattr(server.runner, name)

            def guarded(*args, _operation=operation, **kwargs):
                with config_lock:
                    tools.guard()
                    return _operation(*args, **kwargs)

            setattr(server.runner, name, guarded)
        server.start()
        ok._mcp_server = server

    def mcp_state(self):
        server = self.runtime.ok._mcp_server
        running = bool(server and server._thread and server._thread.is_alive())
        return {
            "enabled": running,
            "requested": bool(self.runtime.executor.basic_options.get(ENABLE_MCP_SERVER)),
            "url": f"http://127.0.0.1:{server.port}/mcp" if running else None,
            # The token remains in the framework's local connection file.
            "connection_file": str(server._connection_file) if running else None,
            "error": self.mcp_error,
        }

    def set_mcp(self, enabled, confirmed=False):
        if type(enabled) is not bool:
            raise ValueError("MCP 开关应为布尔值")
        if enabled and confirmed is not True:
            raise ValueError("请先确认允许受信任的本机程序控制客户端")
        with self.mcp_lock:
            with config_lock:
                self.guard(allow_tools=not enabled, check_tasks=enabled)
            # Shutdown joins the server; its in-flight requests may need config_lock.
            try:
                self.runtime.ok.set_mcp_enabled(enabled)
                self.mcp_error = None
            except Exception as error:
                self.mcp_error = str(error)
                raise RuntimeError(f"MCP 设置失败：{error}") from error
        return self.mcp_state()

    def close(self):
        with self.lock:
            if self.job is not None and not self.job.future.done():
                self.job.cancel()
        if self.session is not None:
            communicate.task_list_updated.disconnect(self._clear_task)


def install_tools(app, runtime, legacy_check=None):
    tools = DesktopTools(runtime, legacy_check)
    app.state.tools = tools
    runtime.ok.start_mcp_server = tools.start_mcp
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            if (runtime.ok.config.get("mcp") or {}).get("enabled") or (
                runtime.executor.basic_options.get(ENABLE_MCP_SERVER)
            ):
                try:
                    await asyncio.to_thread(tools.start_mcp)
                except Exception as error:
                    tools.mcp_error = str(error)
            try:
                yield
            finally:
                tools.close()

    app.router.lifespan_context = lifespan
    original_tool = runtime.run_tool
    runtime.run_tool = lambda action: (
        tools.run({"kind": "ocr"}) if action == "ocr" else original_tool(action)
    )
    original_overlay = runtime.set_overlay

    def overlay(name, value):
        with config_lock:
            try:
                runtime.ensure_config_write(allow_tools=True)
            except RuntimeError as error:
                raise ValueError(str(error)) from error
            return original_overlay(name, value)

    runtime.set_overlay = overlay

    def call(operation, *args):
        try:
            return operation(*args)
        except (ValueError, RuntimeError, OSError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/desktop/tools")
    def state():
        return tools.state()

    @app.post("/api/desktop/tools/run")
    def run(body: dict):
        return call(tools.run, body)

    @app.post("/api/desktop/tools/stop")
    def stop():
        return tools.cancel()

    @app.post("/api/desktop/tools/clear")
    def clear():
        return tools.clear()

    @app.post("/api/desktop/tools/reset")
    def reset():
        return call(tools.reset)

    @app.get("/api/desktop/tools/ocr/image")
    def image():
        with tools.lock:
            data = tools.image
        if data is None:
            raise HTTPException(status_code=404, detail="请先运行文字识别")
        return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/desktop/mcp")
    def mcp():
        return tools.mcp_state()

    @app.post("/api/desktop/mcp")
    def set_mcp(body: dict):
        return call(tools.set_mcp, body.get("enabled"), body.get("confirmed", False))
