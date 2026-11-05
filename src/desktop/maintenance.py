"""Coordinate desktop maintenance; data replacement runs only after the core exits."""

import argparse
import logging
import os
import re
import time
from pathlib import Path

from fastapi import HTTPException

from src.desktop.backups import BackupStore, normalized_version

VERSION = re.compile(r"v?\d+\.\d+\.\d+")


def active_launcher():
    """PyAppify v1.2.3 forwards CLI requests to a machine-wide launcher window."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    import psutil

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    window = user32.FindWindowW("pyappify-sic", "pyappify-siw")
    if not window:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    try:
        return Path(psutil.Process(pid.value).exe()).resolve()
    except psutil.Error as error:
        raise RuntimeError("无法核对正在运行的启动器，请退出其他启动器后重试") from error


def ensure_launcher_matches(executable):
    active = active_launcher()
    if active is not None and active != Path(executable).resolve():
        raise ValueError("另一安装目录的启动器正在运行，请先退出它，避免更新到其他安装")


def install_maintenance(app, runtime, version, request):
    from src.desktop.config_store import config_lock

    folder = runtime.ok.config.get("config_folder", "configs")
    store = BackupStore(Path.cwd()) if folder == "configs" else None
    facade = runtime.ok.headless_app
    available = set()
    original_check = runtime.check_for_updates

    def check(release_only=True):
        ensure_launcher_matches(getattr(runtime.pyappify_module, "pyappify_executable", None) or "")
        result = original_check(release_only)
        available.clear()
        current = normalized_version(version)
        if VERSION.fullmatch(current):
            current_parts = tuple(map(int, current.split(".")))
            available.update(
                item["version"]
                for item in result["versions"]
                if VERSION.fullmatch(str(item["version"]))
                and tuple(map(int, normalized_version(item["version"]).split("."))) > current_parts
            )
        return result

    runtime.check_for_updates = check

    def require_store():
        if store is None:
            raise ValueError("自定义外部配置目录不支持客户端备份，请使用独立安装目录")
        return store

    def leave():
        from ok.core.events import communicate

        facade.maintenance = False
        communicate.executor_paused.emit(runtime.executor.paused)

    def enter(*, stop_tasks):
        with config_lock:
            if getattr(facade, "maintenance", False):
                raise ValueError("更新或备份操作正在进行")
            if (
                facade.start_controller.starting
                or getattr(facade, "diagnostic_pause", False)
                or getattr(facade, "window_resize_in_progress", False)
            ):
                raise ValueError("请先结束游戏启动、问题报告或窗口调整，再继续")
            if not stop_tasks and (
                runtime.executor.current_task is not None
                or any(
                    task.running or (task in runtime.executor.onetime_tasks and task.enabled)
                    for task in runtime.executor.get_all_tasks()
                )
            ):
                raise ValueError("请先停止任务，再创建备份")
            facade.maintenance = True
            runtime.prevent_report_resume()
            runtime.executor.pause()
            trigger = runtime.executor.current_task
            trigger_enabled = None
            if stop_tasks:
                if trigger in runtime.executor.trigger_tasks:
                    # Stop this invocation without persisting an automatic-login preference change.
                    trigger_enabled = trigger._enabled
                    trigger._enabled = False
                else:
                    runtime.stop_task()
                for task in runtime.executor.onetime_tasks:
                    if task.enabled:
                        runtime.task_action(task.name, "stop")
        try:
            deadline = time.monotonic() + 20
            while runtime.executor.current_task is not None or any(
                task.running for task in runtime.executor.get_all_tasks()
            ):
                if time.monotonic() >= deadline:
                    raise RuntimeError("任务未能安全停止，已取消更新；请检查任务状态")
                time.sleep(0.05)
        except Exception:
            leave()
            raise
        finally:
            if trigger_enabled is not None:
                trigger._enabled = trigger_enabled

    def send(operation, *, target="", backup_id=""):
        require_store()
        if request is None:
            raise ValueError("请先更新客户端，再进行这个操作")
        launcher = ""
        if operation == "update":
            if target not in available or not runtime.about()["update_supported"]:
                raise ValueError("请重新检查更新，并选择列表中的较新版本")
            launcher = str(runtime.pyappify_module.pyappify_executable)
            if not Path(launcher).is_file():
                raise ValueError("找不到当前安装的启动器")
            ensure_launcher_matches(launcher)
        elif operation == "restore":
            metadata = store.read(backup_id)
            if metadata["version"] != normalized_version(version):
                raise ValueError("这份备份来自其他版本，请先切换到对应版本再恢复")
        enter(stop_tasks=True)
        try:
            from ok import og

            evidence = getattr(getattr(og, "my_app", None), "diagnostic_evidence", None)
            if (
                evidence is not None
                and "evidence_worker_pending" in evidence.snapshot()["omissions"]
            ):
                raise RuntimeError("问题现场尚未保存完成，请稍后重试")
            for handler in logging.getLogger().handlers:
                handler.flush()
            request(
                operation=operation,
                version=str(version),
                target=target,
                backup_id=backup_id,
                launcher=launcher,
            )
            return {"accepted": True}
        except Exception:
            leave()
            raise

    runtime.update_to_version = lambda target: send("update", target=target)

    def call(action, *args):
        try:
            return action(*args)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/desktop/backups")
    def backups():
        return {
            "items": store.list() if store else [],
            "version": normalized_version(version),
            "supported": store is not None,
            "busy": bool(getattr(facade, "maintenance", False)),
        }

    @app.post("/api/desktop/backups")
    def create_backup():
        def create():
            require_store()
            enter(stop_tasks=False)
            try:
                with config_lock:
                    return store.create(version, "manual")
            finally:
                leave()

        return call(create)

    @app.post("/api/desktop/backups/restore")
    def restore(body: dict):
        if body.get("confirmed") is not True:
            raise HTTPException(400, "恢复会覆盖备份之后的进度与设置，请先确认")
        return call(lambda: send("restore", backup_id=body.get("id")))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline desktop backup and restore")
    parser.add_argument("action", choices=("backup", "restore", "update"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--backup-id")
    parser.add_argument("--launcher")
    arguments = parser.parse_args(argv)
    from src.executor_guard import acquire_executor_lock, legacy_guard, release_executor_lock

    acquired, _ = acquire_executor_lock("maintenance")
    if not acquired:
        raise RuntimeError("另一个 ok-bd2 正在运行，已取消这个操作")
    try:
        if legacy_guard().blocking_message(force=True):
            raise RuntimeError("旧版程序正在运行，请先退出再维护配置")
        store = BackupStore(Path.cwd())
        if arguments.action == "update":
            if not arguments.launcher:
                raise ValueError("更新维护缺少启动器路径")
            ensure_launcher_matches(arguments.launcher)
        if arguments.action == "restore":
            result = store.restore(arguments.backup_id, arguments.version)
        else:
            result = store.create(
                arguments.version, "before-update" if arguments.action == "update" else "manual"
            )
        print(result["id"])
    finally:
        release_executor_lock()


if __name__ == "__main__":
    main()
