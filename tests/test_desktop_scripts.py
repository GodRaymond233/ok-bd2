"""The script and template pages' rules on top of ok-script's task manager and template store."""

import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from ok.core.template_store import CocoTemplateStore

from src.desktop import scripts as scripts_module
from src.desktop.app import create_app
from tests.test_desktop_app import ORIGIN, TOKEN, make_executor

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
GOOD = "from ok import BaseTask\n\nclass A(BaseTask):\n    pass\n"


def done_job(error=None, result=None):
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)
    return SimpleNamespace(future=future)


class FakeTask:
    enabled = False
    running = False
    paused = False

    def __init__(self, name):
        self.name = name


class FakeManager:
    """ok-script's custom task manager: files load into tasks, broken ones into task_errors."""

    has_custom = True

    def __init__(self, folder, executor):
        self.task_folder = str(folder)
        self.task_map = {}
        self.task_errors = {}
        self.executor = executor
        self.reloaded = []
        self.deleted = []

    def _load(self, path):
        source = Path(path).read_text(encoding="utf-8")
        if "broken" in source:
            self.task_errors[path] = "invalid syntax (A.py, line 3)"
            raise SyntaxError(self.task_errors[path])
        self.task_errors.pop(path, None)
        task = next((item for item, data in self.task_map.items() if data[0] == path), None)
        if task is None:
            task = FakeTask(f"任务 {Path(path).stem}")
            self.task_map[task] = [path, ""]
        return task

    def load_single_user_task(self, path):
        try:
            return self._load(path)
        except SyntaxError:
            return None

    def reload_task_code(self, task):
        self.reloaded.append(task)
        try:
            return done_job(result=[self._load(self.task_map[task][0])])
        except SyntaxError as error:
            return done_job(error=error)

    def delete_task(self, task):
        path = self.task_map.pop(task)[0]
        self.deleted.append(path)
        os.remove(path)

    def load_import_folder(self, folder):
        self.imported = folder


class ScriptCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.folder = self.root / "ok_tasks"
        self.folder.mkdir()
        executor = make_executor()
        executor.submit = lambda operation, name=None: self.run_now(operation)
        executor.global_config = SimpleNamespace(get_all_visible_configs=lambda: [])
        executor.feature_set = Mock()
        self.manager = FakeManager(self.folder, executor)
        self.starts = []
        controller = SimpleNamespace(
            start=lambda task=None, exit_after=False: self.starts.append(task), starting=False
        )
        self.capture = SimpleNamespace(connected=lambda: True, get_frame=lambda: None)
        self.engine = SimpleNamespace(
            config={"version": "0.1.1"},
            task_executor=executor,
            task_manager=self.manager,
            device_manager=SimpleNamespace(
                capture_method=self.capture, get_hwnd_name=lambda: "BrownDust II"
            ),
            headless_app=SimpleNamespace(start_controller=controller),
            start_runtime=Mock(),
            quit=Mock(),
            exit_event=threading.Event(),
            get_task=lambda name: (
                next(task for task in self.manager.task_map if task.name == name),
                None,
            ),
        )
        self.controller = controller
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.addCleanup(self.temporary.cleanup)
        self.app = create_app(
            {"version": "0.1.1"},
            token=TOKEN,
            origin=ORIGIN,
            ok_instance=self.engine,
            ui_dist=self.root,
        )
        self.runtime = self.app.state.runtime
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def run_now(self, operation):
        try:
            return done_job(result=operation())
        except Exception as error:
            return done_job(error=error)

    def post(self, path, body=None):
        return self.client.post(path, json=body or {}, headers=HEADERS)

    def get(self, path):
        return self.client.get(path, headers=HEADERS)

    def script(self, name="A", code=GOOD):
        path = self.folder / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        self.manager.load_single_user_task(str(path))
        return path


class ScriptRoutesTest(ScriptCase):
    def test_says_when_the_tools_are_available(self):
        self.assertTrue(self.get("/api/desktop/info").json()["custom_scripts"])

    def test_lists_scripts_with_their_task_and_errors(self):
        self.script()
        self.script("Broken", "broken")
        listed = {item["name"]: item for item in self.get("/api/scripts").json()}
        self.assertEqual(listed["A.py"]["task"], "任务 A")
        self.assertFalse(listed["A.py"]["error"])
        self.assertIsNone(listed["Broken.py"]["task"])
        self.assertTrue(listed["Broken.py"]["error"])

    def test_a_script_that_does_not_load_is_saved_and_says_where(self):
        path = self.script()
        response = self.post("/api/scripts/A.py", {"code": "broken"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(path.read_text(encoding="utf-8"), "broken")
        self.assertEqual(response.json()["error"], "invalid syntax (A.py, line 3)")
        self.assertEqual(response.json()["error_line"], 3)
        fixed = self.post("/api/scripts/A.py", {"code": GOOD}).json()
        self.assertIsNone(fixed["error"])
        self.assertIsNone(fixed["error_line"])
        self.assertEqual(fixed["task"], "任务 A")

    def test_waits_for_the_executor_before_answering(self):
        self.script()
        pending = Future()
        self.manager.reload_task_code = lambda task: SimpleNamespace(future=pending)
        with patch.object(scripts_module, "LOAD_TIMEOUT", 0.01):
            response = self.post("/api/scripts/A.py", {"code": GOOD})
        self.assertEqual(response.status_code, 400)
        self.assertIn("加载超时", response.json()["detail"])

    def test_changes_wait_for_running_tasks_starts_and_maintenance(self):
        self.script()
        task = SimpleNamespace(enabled=True, running=False)
        self.engine.task_executor.onetime_tasks = [task]
        for path, body in (
            ("/api/scripts/A.py", {"code": "x"}),
            ("/api/scripts", {"class_name": "B", "task_name": "b"}),
            ("/api/scripts/A.py/copy", {}),
            ("/api/scripts/A.py/delete", {}),
            ("/api/scripts/A.py/run", {"code": GOOD}),
        ):
            response = self.post(path, body)
            self.assertEqual(response.status_code, 400, path)
            self.assertIn("已有任务在运行或排队", response.json()["detail"], path)
        self.assertEqual((self.folder / "A.py").read_text(encoding="utf-8"), GOOD)
        self.assertEqual(sorted(item.name for item in self.folder.glob("*.py")), ["A.py"])

        self.engine.task_executor.onetime_tasks = []
        self.controller.starting = True
        self.assertIn(
            "正在启动游戏", self.post("/api/scripts/A.py", {"code": "x"}).json()["detail"]
        )
        self.controller.starting = False
        self.engine.headless_app.maintenance = True
        self.assertIn(
            "正在准备更新", self.post("/api/scripts/A.py", {"code": "x"}).json()["detail"]
        )

    def test_running_saves_and_starts_through_the_client_rules(self):
        self.script()
        with patch("src.desktop.app.task_payload", return_value={}):
            response = self.post("/api/scripts/A.py/run", {"code": GOOD + "# edited\n"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.starts, [next(iter(self.manager.task_map))])
        self.assertIn("# edited", (self.folder / "A.py").read_text(encoding="utf-8"))

    def test_a_script_that_does_not_load_does_not_start(self):
        self.script()
        response = self.post("/api/scripts/A.py/run", {"code": "broken"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["error"])
        self.assertEqual(self.starts, [])

    def test_create_copy_delete(self):
        for body, reason in (
            ({"class_name": "1bad", "task_name": "x"}, "类名"),
            ({"class_name": "../evil", "task_name": "x"}, "类名"),
            ({"class_name": "Good", "task_name": " "}, "任务名称"),
        ):
            self.assertIn(reason, self.post("/api/scripts", body).json()["detail"])
        created = self.post(
            "/api/scripts", {"class_name": "New", "task_name": "新任务", "description": "说明"}
        ).json()
        self.assertEqual(created["name"], "New.py")
        self.assertIn("self.name = '新任务'", created["code"])
        self.assertIn(
            "已有同名",
            self.post("/api/scripts", {"class_name": "New", "task_name": "x"}).json()["detail"],
        )

        copied = self.post("/api/scripts/New.py/copy").json()
        self.assertEqual(copied["name"], "New_copy.py")
        self.assertIn("self.name = '新任务_copy'", copied["code"])
        self.assertEqual(self.post("/api/scripts/New.py/copy").json()["name"], "New_copy_1.py")

        self.assertEqual(self.post("/api/scripts/New.py/delete").json(), {"deleted": "New.py"})
        self.assertFalse((self.folder / "New.py").exists())
        self.assertEqual(self.get("/api/scripts/New.py").status_code, 400)

    def test_a_script_that_never_loaded_can_still_be_deleted(self):
        self.script("Broken", "broken")
        self.assertEqual(self.post("/api/scripts/Broken.py/delete").status_code, 200)
        self.assertFalse((self.folder / "Broken.py").exists())
        self.assertEqual(self.manager.task_errors, {})

    def test_refuses_names_outside_the_scripts_folder(self):
        for name in ("..%2Fevil.py", "a.txt", "a%20b.py"):
            response = self.get(f"/api/scripts/{name}")
            self.assertIn(response.status_code, (400, 404), name)
        self.assertEqual(self.post("/api/scripts/a.txt", {"code": ""}).status_code, 400)

    def test_refuses_a_huge_script(self):
        self.script()
        with patch.object(scripts_module, "MAX_SCRIPT_BYTES", 4):
            self.assertIn(
                "太大", self.post("/api/scripts/A.py", {"code": "12345"}).json()["detail"]
            )

    def test_export_reports_the_file_and_shows_it(self):
        self.script()
        for body, reason in (
            ({"selected": [], "file_name": "p", "script_name": "s"}, "至少选择"),
            ({"selected": ["Z.py"], "file_name": "p", "script_name": "s"}, "至少选择"),
            ({"selected": ["A.py"], "file_name": "a b", "script_name": "s"}, "文件名"),
            ({"selected": ["A.py"], "file_name": "p", "script_name": " "}, "脚本名称"),
        ):
            self.assertIn(reason, self.post("/api/bd2/scripts/export", body).json()["detail"])
        downloads = self.root / "downloads"
        downloads.mkdir()
        with (
            patch("ok.core.script_packager.get_downloads_folder", return_value=str(downloads)),
            patch.object(scripts_module, "reveal_in_explorer") as reveal,
        ):
            result = self.post(
                "/api/bd2/scripts/export",
                {
                    "selected": ["A.py"],
                    "file_name": "pack",
                    "script_name": "包",
                    "version": "1.2.3",
                },
            ).json()
        self.assertEqual(result["name"], "pack.okscript")
        self.assertTrue((downloads / "pack.okscript").is_file())
        reveal.assert_called_once_with(str(downloads / "pack.okscript"))
        options = self.get("/api/scripts-export/options").json()
        self.assertEqual(options["tasks"], ["A.py"])
        self.assertEqual(options["manifest"]["script_name"], "包")

    def import_package(self, name="pack.okscript", content=b"zip"):
        return self.client.post(
            f"/api/bd2/scripts/import?name={name}",
            content=content,
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
        )

    def test_import_installs_a_new_package_or_waits_for_a_restart(self):
        with patch(
            "ok.core.plugin_staging.install_or_stage_plugin",
            return_value={"folder": "f", "restart_required": False},
        ):
            self.assertEqual(self.import_package().json(), {"restart_required": False})
        self.assertEqual(self.manager.imported, "f")
        self.manager.imported = None
        with patch(
            "ok.core.plugin_staging.install_or_stage_plugin",
            return_value={"folder": "f", "restart_required": True},
        ):
            self.assertEqual(self.import_package().json(), {"restart_required": True})
        self.assertIsNone(self.manager.imported)

    def test_import_refuses_other_files_and_reports_bad_packages(self):
        self.assertIn(".okscript", self.import_package("a.zip").json()["detail"])
        self.assertIn("为空", self.import_package(content=b"").json()["detail"])
        self.assertIn("导入失败", self.import_package(content=b"not a zip").json()["detail"])
        self.engine.task_executor.onetime_tasks = [SimpleNamespace(enabled=True, running=False)]
        self.assertIn("已有任务", self.import_package().json()["detail"])

    def test_the_web_layers_own_routes_follow_the_same_rules(self):
        self.script()
        self.engine.task_executor.onetime_tasks = [SimpleNamespace(enabled=True, running=False)]
        imported = self.client.post(
            "/api/scripts-import",
            content=b"zip",
            headers={**HEADERS, "x-file-name": "pack.okscript"},
        )
        self.assertIn("已有任务", imported.json()["detail"])
        saved = self.post(
            "/api/templates/save", {"generate_label_enum": True, "enum_path": "src/Labels"}
        )
        self.assertEqual(saved.status_code, 400)
        self.engine.task_executor.onetime_tasks = []
        refused = self.post(
            "/api/scripts-export", {"selected": [], "file_name": "p", "script_name": "s"}
        )
        self.assertIn("至少选择", refused.json()["detail"])

    def test_recording_listens_to_the_mouse_only(self):
        listener = Mock()
        mouse = SimpleNamespace(
            Listener=Mock(return_value=listener), Button=SimpleNamespace(right="r", middle="m")
        )
        keyboard = SimpleNamespace(Listener=Mock())
        pynput = ModuleType("pynput")
        pynput.mouse = mouse
        pynput.keyboard = keyboard
        modules = {"pynput": pynput, "pynput.mouse": mouse, "pynput.keyboard": keyboard}
        sys.modules.pop("ok.ui.qt.tasks.RecordScript", None)
        with (
            patch.dict(sys.modules, modules),
            patch("ok.og.device_manager", SimpleNamespace(hwnd_window=None), create=True),
        ):
            state = self.post("/api/scripts-record/start").json()
            self.assertTrue(state["recording"])
            self.assertFalse(state["active"])
            self.assertEqual(state["target"], "BrownDust II")
            keyboard.Listener.assert_not_called()
            listener.start.assert_called_once()
            self.assertNotIn(
                "PySide6.QtWidgets.QMessageBox",
                sys.modules.get("ok.ui.qt.tasks.RecordScript").__dict__,
            )
            recorder = mouse.Listener.call_args.kwargs["on_click"].__self__
            recorder.on_window(True)
            self.assertTrue(self.get("/api/bd2/scripts/recording").json()["active"])
            recorder.events.append(
                {
                    "type": "click",
                    "x": 0.5,
                    "y": 0.25,
                    "button": "left",
                    "time": 1,
                    "latency": 0,
                    "down_time": 0.1,
                }
            )
            self.assertEqual(self.get("/api/bd2/scripts/recording").json()["clicks"], 1)
            recorder.generate_code = lambda: ("", "self.click_relative(0.5, 0.25)")
            source = "class A:\n    def run(self):\n        pass\n"
            stopped = self.post(
                "/api/scripts-record/stop", {"code": source, "loop": "count", "count": 2}
            ).json()
        self.assertFalse(stopped["recording"])
        self.assertIn(
            "for _ in range(2):\n            self.click_relative(0.5, 0.25)", stopped["code"]
        )
        self.assertEqual(self.post("/api/scripts-record/stop", {}).status_code, 400)

    def test_recording_says_so_when_the_game_is_not_connected(self):
        self.engine.device_manager.get_hwnd_name = lambda: ""
        self.assertIn("没有连接游戏窗口", self.post("/api/scripts-record/start").json()["detail"])


class TemplateRoutesTest(ScriptCase):
    def setUp(self):
        super().setUp()
        self.store = CocoTemplateStore(self.root / "ok_templates")
        self.runtime._template_store = self.store
        self.image = self.store.folder / "0.png"
        cv2.imwrite(str(self.image), np.zeros((20, 40, 3), dtype=np.uint8))

    def test_lists_marks_and_deletes(self):
        self.assertEqual([item["name"] for item in self.get("/api/templates").json()], ["0.png"])
        saved = self.post(
            "/api/templates/0.png/annotations",
            {"annotations": [{"category": "ok", "bbox": [1, 2, 3, 4]}]},
        ).json()
        self.assertEqual(saved["annotations"][0]["category"], "ok")
        self.assertEqual((saved["width"], saved["height"]), (40, 20))
        self.assertEqual(self.get("/api/templates").json()[0]["categories"], ["ok"])
        self.assertEqual(
            self.client.get("/api/templates/image/0.png", headers=HEADERS).status_code, 200
        )
        self.assertEqual(self.post("/api/templates/0.png/delete").json(), {"deleted": "0.png"})
        self.assertFalse(self.image.exists())

    def test_reports_problems_in_chinese(self):
        self.assertIn("模板不存在", self.post("/api/templates/9.png/delete").json()["detail"])
        self.assertIn("名称无效", self.get("/api/templates/a.txt/annotations").json()["detail"])
        bad = self.post(
            "/api/templates/0.png/annotations",
            {"annotations": [{"category": "", "bbox": [0, 0, 1, 1]}]},
        )
        self.assertIn("标注框无效", bad.json()["detail"])

    def test_changes_wait_for_maintenance(self):
        self.engine.headless_app.maintenance = True
        for path, body in (
            ("/api/templates/0.png/delete", {}),
            ("/api/templates/capture", {}),
            ("/api/templates/0.png/annotations", {"annotations": []}),
        ):
            response = self.post(path, body)
            self.assertEqual(response.status_code, 400, path)
            self.assertIn("正在准备更新", response.json()["detail"], path)
        self.assertTrue(self.image.exists())

    def test_capture_needs_a_connected_game(self):
        self.capture.connected = lambda: False
        self.assertIn("没有连接游戏窗口", self.post("/api/templates/capture").json()["detail"])
        self.capture.connected = lambda: True
        self.assertIn("没有截到画面", self.post("/api/templates/capture").json()["detail"])
        self.capture.get_frame = lambda: np.zeros((10, 10, 3), dtype=np.uint8)
        self.assertEqual(len(self.post("/api/templates/capture").json()), 2)

    def test_save_generates_templates_and_reloads_features(self):
        self.post(
            "/api/templates/0.png/annotations",
            {"annotations": [{"category": "ok", "bbox": [1, 2, 3, 4]}]},
        )
        result = self.post(
            "/api/bd2/templates/save", {"format": "split", "destination": "tasks"}
        ).json()
        folder = Path(result["folder"])
        self.assertEqual(folder, (self.root / "ok_tasks" / "assets" / "templates").resolve())
        self.assertEqual(len(list(folder.glob("ok*.png"))), 1)
        self.engine.task_executor.feature_set.process_data.assert_called_once()

    def test_save_keeps_to_the_scripts_folder_outside_debug(self):
        self.post(
            "/api/templates/0.png/annotations",
            {"annotations": [{"category": "ok", "bbox": [1, 2, 3, 4]}]},
        )
        for body, reason in (
            ({"destination": "assets"}, "调试模式"),
            ({"generate_label_enum": True, "enum_path": "src/Labels"}, "ok_tasks"),
            ({"generate_label_enum": True, "enum_path": "../evil"}, "相对路径无效"),
        ):
            self.assertIn(reason, self.post("/api/bd2/templates/save", body).json()["detail"])
        self.assertFalse((self.root / "src").exists())
        info = self.get("/api/bd2/templates/info").json()
        self.assertEqual(
            info,
            {"debug": False, "generate_label_enum": False, "enum_path": scripts_module.ENUM_PATH},
        )

    def test_save_says_when_there_is_nothing(self):
        self.store.coco_path.unlink(missing_ok=True)
        self.assertIn("还没有标注", self.post("/api/bd2/templates/save", {}).json()["detail"])


if __name__ == "__main__":
    unittest.main()
