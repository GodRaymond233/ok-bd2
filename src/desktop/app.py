"""Adapt ok-script's Web API to BD2's persistence and task semantics for the desktop shell.

Ported from ``codex/webui-refactor`` (``src/webui/app.py`` at ``b8f623d``) without the
pywebview window, the ok-script page injection and the quick-hunt plan editor (main has
no ``quick_hunt_plan`` yet).  The React client is served from ``src/desktop/ui_dist``.
"""

import os
import threading
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from ok.core.config_schema import build_config_fields
from ok.core.events import communicate
from ok.ui.web import app as framework

from src.compat.update_card_ui import launcher_supports_update_check
from src.desktop.config_store import config_lock, save_config, task_config_busy
from src.desktop.events import install_events
from src.desktop.protocol import API_VERSION
from src.desktop.security import LocalSession
from src.task_guides import GUIDES
from src.tasks.BaseBD2Task import task_info_snapshot
from src.tasks.DailyBatchTask import RUN_MODE_ALL, RUN_MODE_INCOMPLETE, DailyBatchTask
from src.tasks.quick_hunt_plan import PLAN_KEY
from src.tasks.run_history import default_store, run_state
from src.tasks.today import find_batch, today_summary
from src.ui.hide_config_rows import mark_hidden_config_keys

UI_DIST = Path(__file__).with_name("ui_dist")
# MiSans ships with the app for the Qt window too (src/ui/quest_theme.py).
FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"
HISTORY_LIMIT = 100
# Settings that only a dedicated editor may change; the client shows them read-only until
# it has that editor (the quick-hunt plan is JSON written by the Qt plan editor).
DEDICATED_EDITORS = {PLAN_KEY: "hunt_plan"}


class LegacyInstanceRunning(RuntimeError):
    """An older ok-bd2 that ignores the executor lock is running."""


MISSING_UI_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>ok-bd2</title></head>
<body><h1>界面尚未构建</h1>
<p>核心服务已启动，但没有找到界面文件。开发时请在 desktop/ui 目录运行 npm ci 和 npm run build。</p>
</body></html>"""

DEVICE_KINDS = {"windows": "PC", "adb": "安卓", "browser": "浏览器"}

# ok-script's capture classes by the identifiers that the windows config and
# select_capture use. BitBltCaptureMethod serves both BitBlt and BitBlt_RenderFull;
# ok-script keeps that choice in bitblt.render_full.
CAPTURE_IDS = {
    "WindowsGraphicsCaptureMethod": "WGC",
    "ForegroundBitBltCaptureMethod": "ForegroundBitBlt",
    "DesktopDuplicationCaptureMethod": "DXGI",
    "BrowserCaptureMethod": "browser",
    "ADBCaptureMethod": "adb",
    "NemuIpcCaptureMethod": "ipc",
}


def device_payload(device, preferred_id):
    """ok-script's device entry with a Chinese label (ok-script labels it in English)."""
    payload = framework._device_payload(device, preferred_id)
    kind = DEVICE_KINDS.get(payload["kind"], payload["kind"] or "设备")
    state = "已连接" if payload["connected"] else "未连接"
    details = " ".join(
        str(value)
        for value in (device.get("nick"), device.get("address"), device.get("resolution"))
        if value
    )
    payload["label"] = f"{kind} {state}：{details}" if details else f"{kind} {state}"
    return payload


def capture_in_use(manager):
    """Identifier of the capture method running for the connected window, if any.

    The config may hold ``windows`` (pick automatically, in the configured order) instead
    of a method, and a preferred method that fails falls back to the next one, so the
    config value does not say which method runs.
    """
    method = getattr(manager, "capture_method", None)
    preferred = manager.get_preferred_device()
    if method is None or not (preferred and preferred.get("connected")):
        return None
    for cls in type(method).__mro__:
        if cls.__name__ == "BitBltCaptureMethod":
            from ok.device.capture_methods import bitblt

            return "BitBlt_RenderFull" if bitblt.render_full else "BitBlt"
        if cls.__name__ in CAPTURE_IDS:
            return CAPTURE_IDS[cls.__name__]
    return type(method).__name__


def task_payload(task):
    with config_lock:
        waiting = (
            task.executor.waiting_for_task(task) if task in task.executor.onetime_tasks else None
        )
        info = task_info_snapshot(task)
        fields = build_config_fields(
            task.config,
            task.config_description,
            task.config_type,
            task.default_config,
            serialize=framework._json_value,
        )
        for field in fields:
            if field["key"] in DEDICATED_EDITORS:
                field["editor"] = DEDICATED_EDITORS[field["key"]]
        return {
            "name": task.name,
            "class_name": type(task).__name__,
            "enabled": bool(task.enabled),
            "running": bool(task.running),
            "paused": bool(task.paused),
            "trigger": task in task.executor.trigger_tasks,
            "description": str(task.description or ""),
            "visible": bool(task.visible),
            "group_name": task.group_name,
            "instructions": getattr(task, "instructions", None),
            "guide": GUIDES.get(type(task).__name__, (None, None, None))[2],
            "waiting_for": waiting.name if waiting else None,
            "start_time": float(task.start_time or 0),
            "info": framework._json_value(info),
            "run_state": run_state(task, info),
            "config_busy": task_config_busy(task),
            "config": fields,
            "hidden_config_count": hidden_config_count(task),
        }


def hidden_config_count(task):
    """How many maintenance settings the form leaves out (see hide_config_rows)."""
    config_type = getattr(task, "config_type", None) or {}
    return sum(
        1
        for key in task.config
        if not str(key).startswith("_")
        and isinstance(config_type.get(key), dict)
        and config_type[key].get("hidden")
    )


def adapt_runtime(runtime, legacy_check=None):
    """Wrap ok-script's runtime operations with BD2's start, stop and save rules.

    ``legacy_check`` returns a refusal message while an older ok-bd2 runs (see
    ``src.executor_guard``); start and resume requests are refused synchronously so the
    client sees the reason instead of a silent no-op.
    """
    for task in runtime.executor.get_all_tasks():
        mark_hidden_config_keys(task)
    original_setting = runtime.set_setting
    original_reset_settings = runtime.reset_settings
    original_about = runtime.about
    controller = runtime.ok.headless_app.start_controller
    original_start = controller.start

    def refuse_legacy():
        message = legacy_check() if legacy_check is not None else None
        if message:
            raise LegacyInstanceRunning(message)

    def start_controller(task=None, exit_after=False):
        with config_lock:
            if getattr(runtime.ok.headless_app, "diagnostic_pause", False):
                return False
            if getattr(runtime.ok.headless_app, "window_resize_in_progress", False):
                return False
            if controller.starting:
                return False
            return original_start(task, exit_after=exit_after)

    controller.start = start_controller

    def pause():
        runtime.executor.pause()
        return runtime.status()

    def stop_task():
        with config_lock:
            if task := runtime.executor.current_task:
                stop(task)
            return runtime.status()

    def stop(task):
        task._bd2_stopped_run = (id(task), getattr(task, "start_time", 0))
        if isinstance(task, DailyBatchTask):
            with task.lock:
                task._start_after_login = False
                task._enabled = False
        task.disable()
        if task.paused:
            # disable wakes sleep/check_enabled even while the executor is paused.
            # unpause would also start the executor and undo a global pause.
            task._paused = False
            communicate.task.emit(task)

    def resume():
        with config_lock:
            ensure_config_write()
            refuse_legacy()
            if controller.start() is False:
                raise RuntimeError("正在启动游戏，请等待或取消启动")
            return runtime.status()

    def ensure_config_write():
        if getattr(runtime.ok.headless_app, "diagnostic_pause", False):
            raise RuntimeError("请先完成或取消问题报告，再修改配置或启动任务")
        if getattr(runtime.ok.headless_app, "window_resize_in_progress", False):
            raise RuntimeError("正在调整游戏窗口，请等待调整完成")

    def about():
        result = original_about()
        launcher = runtime.pyappify_module
        result["update_supported"] = bool(
            result["update_supported"]
            and getattr(launcher, "pyappify_executable", None)
            and getattr(launcher, "pyappify_version", None)
            and launcher_supports_update_check(launcher.pyappify_version) is not False
        )
        return result

    def task_config(name, key, value):
        with config_lock:
            ensure_config_write()
            task, _ = runtime.ok.get_task(name)
            if task_config_busy(task) or controller.starting:
                raise ValueError("请先停止正在运行或排队的任务，再修改配置")
            save_config(task.config, {key: value}, specifications=task.config_type)
            return task_payload(task)

    def reset_task_config(name):
        with config_lock:
            ensure_config_write()
            task, _ = runtime.ok.get_task(name)
            if task_config_busy(task) or controller.starting:
                raise ValueError("请先停止任务，再重置配置")
            save_config(
                task.config,
                task.config.default,
                allow_private=True,
                specifications=task.config_type,
            )
            return task_payload(task)

    def setting(group, key, value):
        with config_lock:
            ensure_config_write()
            config = runtime.executor.global_config.get_config(group)
            if not hasattr(config, "config_file"):
                return original_setting(group, key, value)
            specifications = next(
                (
                    option.config_type
                    for name, _, option in runtime.executor.global_config.get_all_visible_configs()
                    if name == group
                ),
                {},
            )
            save_config(config, {key: value}, specifications=specifications)
            return next(item for item in runtime.settings() if item["name"] == group)

    def reset_settings(group):
        with config_lock:
            ensure_config_write()
            config = runtime.executor.global_config.get_config(group)
            if not hasattr(config, "config_file"):
                return original_reset_settings(group)
            save_config(config, config.default, allow_private=True)
            return next(item for item in runtime.settings() if item["name"] == group)

    def start(name):
        with config_lock:
            ensure_config_write()
            task, _ = runtime.ok.get_task(name)
            if controller.starting:
                raise ValueError("正在启动游戏，请等待或取消启动")
            if task.enabled and not task.paused:
                raise ValueError("任务已在运行或排队")
            refuse_legacy()
            if controller.start(task) is False:
                raise ValueError("任务未启动，请等待启动流程结束")
            return task_payload(task)

    def task_action(name, operation):
        with config_lock:
            task, trigger = runtime.ok.get_task(name)
            if operation in {"enable", "resume"} and getattr(
                runtime.ok.headless_app, "diagnostic_pause", False
            ):
                raise ValueError("请先完成或取消问题报告，再恢复任务")
            if operation in {"enable", "resume"} and getattr(
                runtime.ok.headless_app, "window_resize_in_progress", False
            ):
                raise ValueError("请先等待游戏窗口调整完成，再恢复任务")
            if operation in {"enable", "resume"}:
                refuse_legacy()
            if operation == "enable" and trigger:
                task.enable()
            elif operation == "disable" and trigger:
                task.disable()
            elif operation == "pause" and not trigger:
                if runtime.executor.current_task is not task:
                    raise ValueError("任务尚未开始，排队中的任务可以停止但不能暂停")
                task.pause()
            elif operation == "resume" and not trigger and task.enabled and task.paused:
                task.unpause()
            elif operation == "stop" and not trigger:
                stop(task)
            else:
                raise ValueError("此任务不支持该操作")
            return task_payload(task)

    def start_daily(mode):
        """Run 一键完成日常 for the remaining dailies or all of them; never queue it."""
        if mode not in (RUN_MODE_ALL, RUN_MODE_INCOMPLETE):
            raise ValueError("未知的运行方式")
        with config_lock:
            ensure_config_write()
            batch = find_batch(runtime.executor)
            if batch is None:
                raise ValueError("没有一键完成日常任务")
            if controller.starting:
                raise ValueError("正在启动游戏，请等待或取消启动")
            if (
                runtime.executor.current_task is not None
                or batch.enabled
                or getattr(batch, "_start_after_login", False)
            ):
                raise ValueError("已有任务在运行或排队，请先停止")
            refuse_legacy()
            batch.request_run_mode(mode)
            if controller.start(batch) is False:
                raise ValueError("任务未启动，请等待启动流程结束")
            return task_payload(batch)

    runtime.start_daily = start_daily
    runtime.tasks = lambda: [task_payload(task) for task in runtime.executor.get_all_tasks()]
    runtime.set_task_config = task_config
    runtime.reset_task_config = reset_task_config
    runtime.set_setting = setting
    runtime.reset_settings = reset_settings
    runtime.start_task = start
    runtime.task_action = task_action
    runtime.about = about
    runtime.pause = pause
    runtime.resume = resume
    runtime.stop_task = stop_task

    def device_command(operation):
        def change(value):
            with config_lock:
                ensure_config_write()
                if controller.starting or runtime.executor.current_task is not None:
                    raise ValueError("请先停止任务和游戏启动流程，再切换窗口或截图方式")
                return operation(value)

        return change

    runtime.select_device = device_command(runtime.select_device)
    runtime.select_capture = device_command(runtime.select_capture)
    runtime.select_interaction = device_command(runtime.select_interaction)

    original_capture_ui = runtime.capture_ui

    def capture_ui():
        ui = original_capture_ui()
        manager = runtime.device_manager
        preferred = manager.get_preferred_device()
        preferred_id = preferred.get("imei") if preferred else None
        ui["devices"] = [device_payload(device, preferred_id) for device in manager.get_devices()]
        preference = manager.get_preferred_capture()
        methods = {item["id"] for item in ui["capture_methods"]}
        # None: no method chosen, the device manager tries capture_methods in order.
        ui["capture_preference"] = preference if preference in methods else None
        ui["capture_in_use"] = capture_in_use(manager)
        return ui

    # The routes, the select_* operations and the event stream all go through this.
    runtime.capture_ui = capture_ui


def frontend_ready(config, runtime):
    """Hide the PyAppify window and upgrade the launcher once the client page is up."""
    launcher = runtime.pyappify_module
    launcher.hide_pyappify()
    update = config.get("update_pyappify")
    if update:
        launcher.upgrade(
            update.get("to_version"),
            update.get("sha256"),
            [update.get("zip_url")],
            exit_event=runtime.ok.exit_event,
        )


def create_app(
    config,
    *,
    token,
    origin,
    ok_instance=None,
    ui_dist=None,
    legacy_check=None,
    legacy_instances=None,
    request_shutdown=None,
    core_version=None,
):
    """Build the loopback API for the desktop shell on top of ok-script's Web runtime."""
    ui_dist = Path(ui_dist or UI_DIST)
    app = framework.create_web_app(config, ok_instance=ok_instance)
    runtime = app.state.runtime
    adapt_runtime(runtime, legacy_check=legacy_check)
    install_events(app, runtime)
    app.add_middleware(LocalSession, token=token, origin=origin)
    frontend_once = threading.Event()

    # ok-script registers its own page at "/" and a ready hook at "/api/ui/ready".
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in {"/", "/api/ui/ready"}
    ]

    @app.get("/")
    def index():
        page = ui_dist / "index.html"
        if page.is_file():
            return FileResponse(page, media_type="text/html")
        return HTMLResponse(MISSING_UI_PAGE)

    if (ui_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=ui_dist / "assets"), name="desktop-assets")
    if FONTS.is_dir():
        app.mount("/fonts", StaticFiles(directory=FONTS), name="desktop-fonts")

    @app.post("/api/ui/ready")
    def ui_ready():
        if frontend_once.is_set():
            return {"accepted": False}
        frontend_once.set()

        def run():
            try:
                frontend_ready(config, runtime)
            except Exception as error:
                from ok import Logger

                Logger.get_logger(__name__).error(f"启动器就绪处理失败：{error}")

        threading.Thread(target=run, daemon=True, name="FrontendReady").start()
        return {"accepted": True}

    @app.get("/api/desktop/info")
    def desktop_info():
        instances = legacy_instances() if legacy_instances is not None else []
        return {
            "api": API_VERSION,
            "core_version": core_version or str(config.get("version") or ""),
            "pid": os.getpid(),
            "mode": "service",
            "ui_built": (ui_dist / "index.html").is_file(),
            "legacy_instances": [
                {"pid": item.pid, "location": item.location, "description": item.describe()}
                for item in instances
            ],
        }

    @app.get("/api/bd2/today")
    def today():
        return today_summary(runtime.executor)

    @app.post("/api/bd2/daily/start")
    def start_daily(body: dict):
        try:
            return runtime.start_daily(str(body.get("mode", "")))
        except (ValueError, RuntimeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/bd2/history")
    def history(task: str | None = None, limit: int = 20):
        return default_store().recent_runs(task, min(max(limit, 1), HISTORY_LIMIT))

    @app.post("/api/desktop/shutdown")
    def desktop_shutdown():
        if request_shutdown is None:
            raise HTTPException(409, "当前运行方式不支持从界面退出")
        request_shutdown("client-request")
        return {"accepted": True}

    return app
