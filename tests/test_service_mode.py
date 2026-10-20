"""Service mode (desktop client core) must run the automation core without Qt."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

# Runs in a child interpreter so the Qt import blocker cannot leak into other tests.
NO_QT_TASKS_SCRIPT = r"""
import importlib.abc
import os
import sys
import tempfile
import threading
from types import SimpleNamespace

class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6', 'qfluentwidgets', 'shiboken6'}:
            raise ImportError('Qt is unavailable in service mode: ' + fullname)

sys.meta_path.insert(0, NoQt())
sys.path.insert(0, os.getcwd())

from ok import Config, og
from ok.task.TaskExecutor import TaskExecutor
from ok.util.clazz import init_class_by_name
from ok.util.handler import ExitEvent, Handler

from src.config import config
from src.debug_profile import configure_debug_profile
from src.tasks.debug_registry import install_debug_tasks

configure_debug_profile(config, release_tag='v0.1.1')
install_debug_tasks(config)
Config.config_folder = tempfile.mkdtemp()
og.config = config
executor = TaskExecutor.__new__(TaskExecutor)
executor.__dict__.update(
    scene=None, lock=threading.Lock(), _wake_condition=threading.Condition(), _wake_version=0,
    onetime_tasks=[], onetime_task_queue=[], trigger_tasks=[], current_task=None, thread=None,
    paused=False, debug_mode=False, basic_options={}, config_folder=Config.config_folder,
    global_config=None,
)
app = SimpleNamespace(debug=False, locale='zh_CN', tr=lambda text: text)
scene = init_class_by_name(*config['scene'])
created = []
for module, name in config['onetime_tasks'] + config['trigger_tasks']:
    task = init_class_by_name(module, name, executor=executor, app=app)
    task.after_init(executor=executor, scene=scene)
    created.append(type(task).__name__)

exit_event = ExitEvent()
og.handler = Handler(exit_event, 'service-mode-test')
og.device_manager = SimpleNamespace(config={}, get_preferred_device=lambda: None)
from src.globals import Globals
from src.tasks import auto_scheduler, run_history
Globals(exit_event)
assert auto_scheduler.install_auto_scheduler._installed
assert run_history.install_run_history_recorder._installed
exit_event.set()

loaded = sorted(m for m in sys.modules if m.split('.')[0] in {'PySide6', 'qfluentwidgets'})
assert not loaded, loaded
print('CREATED', len(created), ','.join(created))
"""


DESKTOP_IMPORTS_SCRIPT = r"""
import importlib
import importlib.abc
import pkgutil
import sys

class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6', 'qfluentwidgets', 'shiboken6', 'webview'}:
            raise ImportError('not allowed in service mode: ' + fullname)

sys.meta_path.insert(0, NoQt())
sys.path.insert(0, '.')
import src.desktop

names = ['src.executor_guard', 'src.ui_mode', 'src.icons']
names += [f'src.desktop.{info.name}' for info in pkgutil.iter_modules(src.desktop.__path__)]
for name in names:
    importlib.import_module(name)
print('IMPORTED', ','.join(sorted(names)))
"""


def run_isolated(script, **env):
    return subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=ROOT,
        env=dict(os.environ, PYTHONUTF8="1", **env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


class ServiceModeWithoutQtTest(unittest.TestCase):
    def test_every_registered_task_is_created_without_importing_qt(self):
        result = run_isolated(NO_QT_TASKS_SCRIPT, OK_BD2_UI="service")
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        line = next(line for line in result.stdout.splitlines() if line.startswith("CREATED "))
        _, count, names = line.split(" ", 2)
        names = names.split(",")
        self.assertEqual(int(count), len(names))
        for expected in (
            "DailyBatchTask",
            "DailyTask",
            "QuickHuntTask",
            "SquareGoddessTask",
            "MapTradeTask",
            "MapCollectionTask",
            "FreeGachaTask",
            "PVPTask",
            "AutoLoginTask",
            "BD2DiagnosisTask",
        ):
            self.assertIn(expected, names)

    def test_desktop_modules_import_without_qt_or_pywebview(self):
        result = run_isolated(DESKTOP_IMPORTS_SCRIPT, OK_BD2_UI="service")
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        line = next(line for line in result.stdout.splitlines() if line.startswith("IMPORTED "))
        imported = line.split(" ", 1)[1].split(",")
        for expected in ("app", "config_store", "events", "protocol", "security", "service"):
            self.assertIn(f"src.desktop.{expected}", imported)

    def test_qt_mode_stays_the_default(self):
        from src.ui_mode import ui_mode

        environment = dict(os.environ)
        environment.pop("OK_BD2_UI", None)
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(ui_mode(), "qt")
        with patch.dict(os.environ, {"OK_BD2_UI": "web"}):
            with self.assertRaises(ValueError):
                ui_mode()


if __name__ == "__main__":
    unittest.main()
