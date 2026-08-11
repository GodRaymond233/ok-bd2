import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.config import config
from src.tasks.debug_registry import DEBUG_ONETIME_TASKS, install_debug_tasks


class DebugRegistryTest(unittest.TestCase):
    def test_probe_tasks_are_not_registered_in_formal_config(self):
        registered = {tuple(item) for item in config["onetime_tasks"]}
        for registration in DEBUG_ONETIME_TASKS:
            with self.subTest(registration=registration):
                self.assertNotIn(tuple(registration), registered)

    def test_debug_registry_contains_only_probe_and_diagnosis_tasks(self):
        self.assertEqual(
            [
                ["src.tasks.BD2ProbeTask", "BD2ProbeTask"],
                ["src.tasks.BD2MapCollectionProbeTask", "BD2MapCollectionProbeTask"],
                ["src.tasks.BD2OneTimeTask", "BD2OneTimeTask"],
                ["src.tasks.BD2DiagnosisTask", "BD2DiagnosisTask"],
            ],
            DEBUG_ONETIME_TASKS,
        )

    def test_install_debug_tasks_is_idempotent(self):
        cfg = {"onetime_tasks": [["src.tasks.DailyBatchTask", "DailyBatchTask"]]}
        install_debug_tasks(cfg)
        self.assertEqual(5, len(cfg["onetime_tasks"]))
        install_debug_tasks(cfg)
        self.assertEqual(5, len(cfg["onetime_tasks"]))
        for registration in DEBUG_ONETIME_TASKS:
            self.assertIn(registration, cfg["onetime_tasks"])


class StatusTabBasicCheckTest(unittest.TestCase):
    def _make_tab(self, executor):
        from src.ui.BD2StatusTab import BD2StatusTab

        tab = BD2StatusTab.__new__(BD2StatusTab)
        tab.config = {"最近操作": ""}
        tab.executor = executor
        return tab

    def test_button_clicked_creates_task_when_not_registered(self):
        from src.ui import BD2StatusTab as status_tab_module

        created = []
        ran = []

        class _FakeCheckTask:
            def __init__(self, **kwargs):
                created.append(kwargs)
                self.after_init_called = False
                self.post_init_called = False

            def after_init(self, **kwargs):
                self.after_init_called = True
                self.after_init_kwargs = kwargs

            def post_init(self):
                self.post_init_called = True

            def run(self):
                ran.append(self)

        scene = object()
        executor = SimpleNamespace(
            get_task_by_class=lambda cls: None,
            scene=scene,
        )
        tab = self._make_tab(executor)
        with (
            patch.object(status_tab_module, "BD2OneTimeTask", _FakeCheckTask),
            patch.object(status_tab_module.og, "app", object()),
        ):
            tab.button_clicked()

        self.assertEqual("运行基础检查", tab.config["最近操作"])
        self.assertEqual(1, len(created))
        self.assertIs(executor, created[0]["executor"])
        self.assertEqual(1, len(ran))
        self.assertTrue(ran[0].after_init_called)
        self.assertIs(executor, ran[0].after_init_kwargs["executor"])
        self.assertIs(scene, ran[0].after_init_kwargs["scene"])
        self.assertTrue(ran[0].post_init_called)

    def test_button_clicked_reuses_registered_task(self):
        from src.ui import BD2StatusTab as status_tab_module

        class _RegisteredTask:
            def run(self):
                self.ran = True

        registered = _RegisteredTask()
        executor = SimpleNamespace(get_task_by_class=lambda cls: registered)
        tab = self._make_tab(executor)

        def _unexpected_init(**kwargs):
            self.fail("registered task must not be recreated")

        with patch.object(status_tab_module, "BD2OneTimeTask", _unexpected_init):
            tab.button_clicked()

        self.assertEqual("运行基础检查", tab.config["最近操作"])
        self.assertTrue(registered.ran)

    def test_button_clicked_reports_unready_app_without_constructing_task(self):
        from src.ui import BD2StatusTab as status_tab_module

        class _Logger:
            def error(self, *_args):
                pass

        executor = SimpleNamespace(
            get_task_by_class=lambda cls: None,
            scene=object(),
        )
        tab = self._make_tab(executor)
        tab.logger = _Logger()

        with (
            patch.object(status_tab_module.og, "app", None),
            patch.object(status_tab_module, "BD2OneTimeTask") as task_class,
        ):
            self.assertFalse(tab.button_clicked())

        task_class.assert_not_called()
        self.assertEqual("基础检查不可用：应用尚未就绪", tab.config["最近操作"])


if __name__ == "__main__":
    unittest.main()
