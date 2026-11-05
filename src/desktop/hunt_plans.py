"""Desktop plan editing uses the runner's validation and pure preview functions."""

import hashlib
import json

from fastapi import HTTPException

from src.desktop.config_store import config_lock, task_config_busy
from src.tasks.quick_hunt_plan import (
    CONDITIONS,
    LEGACY_KEYS,
    MODES,
    PLAN_KEY,
    TARGETS,
    load_book,
    preset_plans,
    preview_plan,
)


def revision(config):
    relevant = {key: config.get(key) for key in (PLAN_KEY, *LEGACY_KEYS)}
    return hashlib.sha256(
        json.dumps(relevant, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def validate_book(book):
    raw = json.dumps(book, ensure_ascii=False, allow_nan=False)
    return load_book({PLAN_KEY: raw}), raw


def install_hunt_plans(app, runtime):
    def find_task(name):
        try:
            task, _ = runtime.ok.get_task(name)
        except (StopIteration, KeyError, ValueError) as error:
            raise HTTPException(404, "任务不存在") from error
        if task is None or PLAN_KEY not in task.config:
            raise HTTPException(400, "此任务没有快速狩猎方案")
        return task

    @app.get("/api/bd2/hunt-plans/{name}")
    def read(name: str):
        with config_lock:
            task = find_task(name)
            try:
                book, error = load_book(task.config), None
            except ValueError as exc:
                book, error = None, str(exc)
            facade = runtime.ok.headless_app
            return {
                "book": book,
                "load_error": error,
                "inherited": not bool(task.config.get(PLAN_KEY)),
                "revision": revision(task.config),
                "presets": preset_plans(),
                "conditions": CONDITIONS,
                "targets": TARGETS,
                "modes": MODES,
                "busy": bool(task_config_busy(task) or facade.start_controller.starting
                             or getattr(facade, "maintenance", False)
                             or getattr(facade, "diagnostic_pause", False)
                             or getattr(facade, "window_resize_in_progress", False)),
            }

    @app.post("/api/bd2/hunt-plans/{name}/preview")
    def preview(name: str, body: dict):
        find_task(name)
        try:
            book, _ = validate_book(body.get("book"))
            rice, states = body.get("rice"), body.get("states")
            if type(rice) is not int or not 0 <= rice <= 9999:
                raise ValueError("免费米饭应为 0 至 9999 的整数")
            if not isinstance(states, dict) or any(
                key not in ("金币", "经验") or (value is not None and type(value) is not bool)
                for key, value in states.items()
            ):
                raise ValueError("双倍状态应为双倍、非双倍或无法确认")
        except (TypeError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        try:
            text = preview_plan(book["plans"][book["active"]], states, rice)
            return {"text": text, "runnable": True}
        except ValueError as error:
            # An unconfirmed bonus blocks execution, not saving a valid plan.
            return {"text": str(error), "runnable": False}

    @app.post("/api/bd2/hunt-plans/{name}")
    def save(name: str, body: dict):
        with config_lock:
            task = find_task(name)
            if body.get("revision") != revision(task.config):
                raise HTTPException(409, "配置已发生变化，草稿已保留。请重新读取后核对再保存。")
            try:
                _, raw = validate_book(body.get("book"))
                return runtime.set_task_config(name, PLAN_KEY, raw)
            except (TypeError, ValueError, RuntimeError) as error:
                raise HTTPException(400, str(error)) from error
