"""The client's interface language and the text it can translate."""

import ast
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from src.desktop import language as language_module
from src.desktop.app import create_app
from src.desktop.language import save_language, stored_language, task_messages
from tests.test_desktop_app import ORIGIN, TOKEN, make_executor

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "desktop" / "ui" / "src" / "locales" / "en_US.json"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
CJK = re.compile(r"[\u4e00-\u9fff]")
# They run before there is a page, or never reach one: the shell shows them or the log keeps them.
NO_PAGE = {"service.py", "bootstrap.py", "shell_install.py"}
# What the core sends on its own: a test message, and the page for a missing interface.
SENT_AS_IS = ("ok-bd2 测试消息", "这是一条测试消息", "<!doctype html>")


def display_text(path):
    """Chinese strings in a module, with f-string values as placeholders; docstrings left out."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    inner = {
        id(value)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for value in node.values
    }
    for node in ast.walk(tree):
        if id(node) in docstrings or id(node) in inner:
            continue
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and CJK.search(node.value)
        ):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                str(part.value) if isinstance(part, ast.Constant) else "{}" for part in node.values
            )
            if CJK.search(text):
                yield text


class StoredLanguageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "configs" / "ui_config.json"

    def write(self, value):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(
            json.dumps({"MainWindow": {"Language": value, "DpiScale": 1}}), encoding="utf-8"
        )

    def test_reads_what_the_qt_window_stored(self):
        for stored, expected in (
            ("en_US", "en_US"),
            ("zh_CN", "zh_CN"),
            ("Auto", "Auto"),
            ("zh_TW", "zh_CN"),
            ("ja_JP", "Auto"),
        ):
            self.write(stored)
            self.assertEqual(stored_language(self.path), expected, stored)

    def test_saving_keeps_the_qt_window_settings(self):
        self.write("zh_CN")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["QFluentWidgets"] = {"ThemeColor": "#fff"}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        save_language(self.path, "en_US")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["MainWindow"], {"Language": "en_US", "DpiScale": 1})
        self.assertEqual(saved["QFluentWidgets"], {"ThemeColor": "#fff"})
        self.assertEqual(
            sorted(item.name for item in self.path.parent.iterdir()), ["ui_config.json"]
        )


class LanguageRouteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ui_config.json"
        patcher = patch.object(language_module, "preference_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        executor = make_executor()
        executor.global_config = SimpleNamespace(get_all_visible_configs=lambda: [])
        self.engine = SimpleNamespace(
            config={"version": "0.1.1"},
            task_executor=executor,
            headless_app=SimpleNamespace(
                start_controller=SimpleNamespace(start=Mock(), starting=False)
            ),
            start_runtime=Mock(),
            quit=Mock(),
            exit_event=threading.Event(),
        )
        app = create_app(
            {"version": "0.1.1"},
            token=TOKEN,
            origin=ORIGIN,
            ok_instance=self.engine,
            ui_dist=Path(self.temporary.name),
        )
        self.client = TestClient(app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_reads_saves_and_refuses(self):
        state = self.client.get("/api/desktop/language", headers=HEADERS).json()
        self.assertEqual(state["language"], "Auto")
        self.assertEqual(state["catalogs"]["en_US"]["一键完成日常"], "Complete dailies")
        saved = self.client.post(
            "/api/desktop/language", json={"language": "en_US"}, headers=HEADERS
        )
        self.assertEqual(saved.json()["language"], "en_US")
        self.assertEqual(stored_language(self.path), "en_US")
        refused = self.client.post(
            "/api/desktop/language", json={"language": "ja_JP"}, headers=HEADERS
        )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(stored_language(self.path), "en_US")


class CatalogTest(unittest.TestCase):
    def setUp(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.known = {re.sub(r"\{\w*\}", "{}", key) for key in catalog} | set(
            task_messages("en_US")
        )

    def test_the_task_catalog_ships_and_has_no_metadata(self):
        messages = task_messages("en_US")
        self.assertEqual(messages["快速狩猎"], "Quick Hunt")
        self.assertNotIn("", messages)
        self.assertEqual(task_messages("ja_JP"), {})

    def test_every_message_from_the_core_has_a_translation(self):
        missing = []
        for path in sorted((ROOT / "src" / "desktop").glob("*.py")):
            if path.name not in NO_PAGE:
                missing += [
                    f"{path.name}: {text}"
                    for text in display_text(path)
                    if text not in self.known and not text.startswith(SENT_AS_IS)
                ]
        self.assertEqual(missing, [])

    def test_hunt_plan_text_has_a_translation(self):
        from src.tasks.quick_hunt_plan import CONDITIONS, MODES, TARGETS

        for text in [*CONDITIONS.values(), *MODES.values(), *TARGETS.values()]:
            self.assertIn(text, self.known)


if __name__ == "__main__":
    unittest.main()
