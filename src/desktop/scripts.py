"""The script editor and template pages: what ok-script's Qt tabs do, by this client's rules.

Three things differ from those tabs.  Changes wait for running tasks and maintenance like every
other change here.  A script starts through BD2's start rules.  Recording follows the mouse only,
because the BD2 client receives no keyboard input (see AGENTS.md).
"""

import asyncio
import re
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException, Request
from ok import Config
from ok.core.events import communicate
from ok.util.explorer import reveal_in_explorer

from src.desktop.config_store import config_lock
from src.desktop.okweb.app import SCRIPT_NAME_PATTERN
from src.ui.hide_config_rows import mark_hidden_config_keys

LOAD_TIMEOUT = 30
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
ENUM_PATH = "ok_tasks/LabelEnum.py"
LOAD_TIMED_OUT = "脚本已保存，但加载超时。请稍后再试，或重启客户端"
# ok-script's template store reports these in English.
TEMPLATE_ERRORS = {
    "Invalid template image": "模板图片名称无效",
    "Invalid template path": "模板图片名称无效",
    "Template not found": "模板不存在，可能已被删除",
    "Invalid annotations": "标注内容无效",
    "Invalid bounding box": "标注框无效",
    "Failed to save template image": "模板图片保存失败",
}


def template_error(error):
    return ValueError(TEMPLATE_ERRORS.get(str(error), str(error)))


def error_line(error):
    match = re.search(r"line (\d+)", error or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def mouse_recorder():
    """ok-script's recorder without its keyboard listener and its Qt alerts."""
    from ok import og
    from ok.ui.qt.tasks.RecordScript import Recorder
    from pynput import mouse

    class MouseRecorder(Recorder):
        def start(self, target_title):
            self.is_recording = True
            self.events = []
            self.last_event_time = self.inactive_start_time = time.time()
            self.target_title = target_title
            window = getattr(og.device_manager, "hwnd_window", None)
            self.target_hwnd = window.hwnd if window else None
            self.is_active = False
            self.mouse_listener = mouse.Listener(on_click=self.on_click)
            self.mouse_listener.start()
            communicate.window.connect(self.on_window)

        def on_window(self, visible, *args):
            if not self.is_recording or visible == self.is_active:
                return
            self.is_active = visible
            if not visible:
                self.drop_pending_inputs()
                self.inactive_start_time = time.time()
                return
            window = getattr(og.device_manager, "hwnd_window", None)
            if not self.target_hwnd and window:
                self.target_hwnd = window.hwnd
            # The time away from the game is not a pause between two clicks.
            self.last_event_time += time.time() - self.inactive_start_time

    return MouseRecorder()


def install_scripts(app, runtime):
    recording = threading.Lock()
    recorder = {"current": None}

    def ensure_idle():
        runtime.ensure_config_write()
        if runtime.ok.headless_app.start_controller.starting:
            raise ValueError("正在启动游戏，请等待或取消启动")
        runtime.ensure_task_slot()

    def script_path(name):
        name = str(name or "")
        if not SCRIPT_NAME_PATTERN.fullmatch(name):
            raise ValueError("脚本文件名无效")
        return runtime._script_folder() / name

    def existing_script(name):
        path = script_path(name)
        if not path.is_file():
            raise ValueError("脚本不存在，可能已被删除")
        return path

    def task_of(path):
        manager = runtime.task_manager
        return next(
            (
                task
                for task, data in manager.task_map.items()
                if Path(data[0]).resolve() == path.resolve()
            ),
            None,
        )

    def stored_error(path):
        return next(
            (
                str(error)
                for file, error in runtime.task_manager.task_errors.items()
                if Path(file).resolve() == path.resolve()
            ),
            None,
        )

    def document(path, error=None):
        error = error or stored_error(path)
        task = task_of(path)
        return {
            "name": path.name,
            "code": path.read_text(encoding="utf-8"),
            "modified": path.stat().st_mtime,
            "task": task.name if task else None,
            "error": error,
            "error_line": error_line(error),
        }

    def on_executor(operation, name):
        return runtime.executor.submit(operation, name=name)

    def load_job(path):
        manager = runtime.task_manager
        task = task_of(path)
        if task is not None:
            return manager.reload_task_code(task)
        return on_executor(lambda: manager.load_single_user_task(str(path)), "load_script")

    def finish(job):
        """Wait for the executor outside the config lock; returns what went wrong."""
        try:
            job.future.result(timeout=LOAD_TIMEOUT)
        except TimeoutError as error:
            raise RuntimeError(LOAD_TIMED_OUT) from error
        except Exception as error:
            return str(error)
        finally:
            for task in runtime.executor.get_all_tasks():
                mark_hidden_config_keys(task)
        return None

    def scripts():
        result = []
        for path in sorted(
            runtime._script_folder().glob("*.py"), key=lambda item: item.name.lower()
        ):
            task = task_of(path)
            result.append(
                {
                    "name": path.name,
                    "modified": path.stat().st_mtime,
                    "task": task.name if task else None,
                    "error": stored_error(path) is not None,
                }
            )
        return result

    def read_script(name):
        return document(existing_script(name))

    def save_script(name, code):
        if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise ValueError("脚本太大，无法保存")
        with config_lock:
            ensure_idle()
            path = existing_script(name)
            path.write_text(code, encoding="utf-8")
            job = load_job(path)
        return document(path, finish(job))

    def create_script(class_name, task_name, description=""):
        class_name = str(class_name or "").strip()
        task_name = str(task_name or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", class_name):
            raise ValueError("类名只能用英文字母、数字和下划线，且不能以数字开头")
        if not task_name:
            raise ValueError("请填写任务名称")
        with config_lock:
            ensure_idle()
            path = script_path(f"{class_name}.py")
            if path.exists():
                raise ValueError("已有同名脚本")
            path.write_text(
                "from ok import BaseTask\n\n"
                f"class {class_name}(BaseTask):\n"
                "    def __init__(self, *args, **kwargs):\n"
                "        super().__init__(*args, **kwargs)\n"
                f"        self.name = {task_name!r}\n"
                f"        self.description = {str(description or '')!r}\n\n"
                "    def run(self):\n"
                "        pass\n",
                encoding="utf-8",
            )
            job = load_job(path)
        return document(path, finish(job))

    def copy_script(name):
        with config_lock:
            ensure_idle()
            source = existing_script(name)
            destination = script_path(f"{source.stem}_copy.py")
            counter = 1
            while destination.exists():
                destination = script_path(f"{source.stem}_copy_{counter}.py")
                counter += 1
            destination.write_text(
                re.sub(
                    r"(self\.name\s*=\s*)([\"'])(.*?)(\2)",
                    lambda match: (
                        f"{match.group(1)}{match.group(2)}{match.group(3)}_copy{match.group(4)}"
                    ),
                    source.read_text(encoding="utf-8"),
                    count=1,
                ),
                encoding="utf-8",
            )
            job = load_job(destination)
        return document(destination, finish(job))

    def delete_script(name):
        manager = runtime.task_manager
        with config_lock:
            ensure_idle()
            path = existing_script(name)
            task = task_of(path)
            job = None
            if task is not None:
                job = on_executor(lambda: manager.delete_task(task), "delete_script")
            else:
                path.unlink()
            for file in [
                file for file in manager.task_errors if Path(file).resolve() == path.resolve()
            ]:
                del manager.task_errors[file]
        if job is not None and (error := finish(job)):
            raise RuntimeError(error)
        return {"deleted": path.name}

    def run_script(name, code):
        result = save_script(name, code)
        if result["error"]:
            return result
        if not result["task"]:
            raise ValueError("脚本里没有找到任务类，无法运行")
        runtime.start_task(result["task"])
        return result

    def script_export_options():
        from ok.core.script_packager import load_manifest

        return {
            "tasks": [item["name"] for item in scripts()],
            "manifest": load_manifest(str(runtime._script_folder())),
        }

    def export_scripts(selected, file_name, script_name, version):
        from ok.core.script_packager import export_script, validate_filename

        selected = [str(name) for name in selected or []]
        available = {item["name"] for item in scripts()}
        if not selected or any(name not in available for name in selected):
            raise ValueError("请至少选择一个脚本")
        if not validate_filename(str(file_name or "")):
            raise ValueError("文件名只能用英文字母、数字、下划线和连字符")
        if not str(script_name or "").strip():
            raise ValueError("请填写脚本名称")
        with config_lock:
            runtime.ensure_config_write()
            success, message, output = export_script(
                selected, str(file_name), str(script_name).strip(), str(version or "1.0.0")
            )
        if not success:
            raise RuntimeError(f"导出失败：{message}")
        reveal_in_explorer(output)
        return {"path": str(output), "name": Path(output).name}

    def import_scripts(file_name, content):
        from ok.core.plugin_staging import install_or_stage_plugin

        if not str(file_name or "").lower().endswith(".okscript"):
            raise ValueError("请选择 .okscript 文件")
        if not content or len(content) > MAX_PACKAGE_BYTES:
            raise ValueError("脚本包为空或太大")
        manager = runtime.task_manager
        with tempfile.NamedTemporaryFile(suffix=".okscript", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        try:
            with config_lock:
                ensure_idle()
                try:
                    result = install_or_stage_plugin(str(temporary))
                except (ValueError, OSError, zipfile.BadZipFile) as error:
                    raise ValueError(f"导入失败：{error}") from error
                job = None
                if not result["restart_required"]:
                    folder = result["folder"]
                    job = on_executor(lambda: manager.load_import_folder(folder), "import_script")
        finally:
            temporary.unlink(missing_ok=True)
        if job is not None and (error := finish(job)):
            raise RuntimeError(f"导入失败：{error}")
        return {"restart_required": result["restart_required"]}

    def recording_state():
        current = recorder["current"]
        active = bool(current and current.is_recording)
        return {
            "recording": active,
            "active": bool(active and current.is_active),
            "clicks": sum(event["type"] == "click" for event in current.events) if active else 0,
            "target": current.target_title if active else None,
        }

    def start_script_recording():
        with recording:
            if not (recorder["current"] and recorder["current"].is_recording):
                with config_lock:
                    ensure_idle()
                target = runtime.device_manager.get_hwnd_name()
                if not target:
                    raise ValueError("还没有连接游戏窗口，无法录制")
                try:
                    recorder["current"] = mouse_recorder()
                    recorder["current"].start(target)
                except ImportError as error:
                    raise RuntimeError("这台电脑上无法录制鼠标操作") from error
            return recording_state()

    def stop_script_recording(source="", loop="none", count=1):
        from ok.core.script_editing import merge_recorded_code

        with recording:
            current = recorder["current"]
            if not (current and current.is_recording):
                raise ValueError("当前没有在录制")
            init_code, run_code = current.stop()
            return {
                **recording_state(),
                "code": merge_recorded_code(source, init_code, run_code, loop, count),
            }

    original_capture = runtime.capture_template
    original_delete = runtime.delete_template
    original_annotations = runtime.template_annotations
    original_save_annotations = runtime.save_template_annotations

    def capture_template():
        runtime.ensure_config_write()
        method = getattr(runtime.ok.device_manager, "capture_method", None)
        if method is None or not method.connected():
            raise ValueError("还没有连接游戏窗口，无法截图")
        try:
            return original_capture()
        except RuntimeError as error:
            raise ValueError("没有截到画面，请确认游戏窗口可见") from error

    def delete_template(name):
        runtime.ensure_config_write()
        try:
            return original_delete(name)
        except ValueError as error:
            raise template_error(error) from error

    def template_annotations(name):
        try:
            return original_annotations(name)
        except ValueError as error:
            raise template_error(error) from error

    def save_template_annotations(name, annotations):
        runtime.ensure_config_write()
        try:
            return original_save_annotations(name, annotations)
        except ValueError as error:
            raise template_error(error) from error

    def label_enum_module(path):
        normalized = str(path or "").strip().replace("\\", "/").removesuffix(".py").strip("/")
        parts = normalized.split("/")
        if not normalized or any(not part.isidentifier() for part in parts):
            raise ValueError(f"相对路径无效，例如 {ENUM_PATH}")
        # Anywhere else it could overwrite the program's own files, such as src/Labels.py.
        if parts[0] != "ok_tasks" and not runtime.ok.config.get("debug"):
            raise ValueError(f"枚举文件只能放在 ok_tasks 文件夹里，例如 {ENUM_PATH}")
        return ".".join(parts)

    def template_options():
        # The Qt tab's own file: both windows remember the same choices.
        return Config(
            "template_tab", {"generate_label_enum": False, "label_enum_relative_path": ENUM_PATH}
        )

    def save_templates(body):
        from ok.feature.FeatureSet import compress_copy_coco, generate_templates

        root = runtime.template_store.folder
        coco = root / "coco_annotations.json"
        if not coco.is_file():
            raise ValueError("还没有标注，无需保存")
        destination = body.get("destination", "tasks")
        if destination == "assets" and not runtime.ok.config.get("debug"):
            raise ValueError("只有调试模式可以保存到 assets")
        target = (
            Path.cwd() / ("assets" if destination == "assets" else "ok_tasks/assets")
        ).resolve()
        split = body.get("format", "split") != "compress"
        if split:
            target = target / "templates"
        enum_module = (
            label_enum_module(body.get("enum_path")) if body.get("generate_label_enum") else None
        )
        with config_lock:
            ensure_idle()
            try:
                generate = generate_templates if split else compress_copy_coco
                generate(str(coco), str(target), str(root), generate_label_enmu=enum_module)
            except (ValueError, OSError, KeyError) as error:
                raise ValueError(f"保存失败：{error}") from error
            feature_set = getattr(runtime.executor, "feature_set", None)
            if feature_set is not None:
                feature_set.process_data()
            options = template_options()
            options["generate_label_enum"] = bool(body.get("generate_label_enum"))
            if body.get("enum_path"):
                options["label_enum_relative_path"] = str(body["enum_path"])
        return {"folder": str(target)}

    runtime.scripts = scripts
    runtime.read_script = read_script
    runtime.save_script = save_script
    runtime.create_script = create_script
    runtime.copy_script = copy_script
    runtime.delete_script = delete_script
    runtime.run_script = run_script
    runtime.script_export_options = script_export_options
    # The copied Web layer's own routes for these follow the same rules.
    runtime.export_scripts = lambda *args: Path(export_scripts(*args)["path"])
    runtime.import_script_archive = import_scripts
    runtime.save_templates = lambda destination, generate_label_enum, enum_path: save_templates(
        {
            "destination": destination,
            "generate_label_enum": generate_label_enum,
            "enum_path": enum_path,
        }
    )
    runtime.start_script_recording = start_script_recording
    runtime.stop_script_recording = stop_script_recording
    runtime.capture_template = capture_template
    runtime.delete_template = delete_template
    runtime.template_annotations = template_annotations
    runtime.save_template_annotations = save_template_annotations

    def bad_request(error):
        return HTTPException(status_code=400, detail=str(error))

    @app.post("/api/bd2/scripts/export")
    def export_route(body: dict):
        try:
            return export_scripts(
                body.get("selected"),
                body.get("file_name"),
                body.get("script_name"),
                body.get("version"),
            )
        except (ValueError, RuntimeError, OSError) as error:
            raise bad_request(error) from error

    @app.post("/api/bd2/scripts/import")
    async def import_route(request: Request, name: str = ""):
        content = await request.body()
        try:
            return await asyncio.to_thread(import_scripts, unquote(name), content)
        except (ValueError, RuntimeError, OSError) as error:
            raise bad_request(error) from error

    @app.get("/api/bd2/scripts/recording")
    def recording_route():
        return recording_state()

    @app.get("/api/bd2/templates/info")
    def templates_info():
        options = template_options()
        return {
            "debug": bool(runtime.ok.config.get("debug")),
            "generate_label_enum": bool(options.get("generate_label_enum")),
            "enum_path": options.get("label_enum_relative_path") or ENUM_PATH,
        }

    @app.post("/api/bd2/templates/save")
    def save_templates_route(body: dict):
        try:
            return save_templates(body)
        except (ValueError, RuntimeError, OSError) as error:
            raise bad_request(error) from error
