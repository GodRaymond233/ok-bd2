import unittest

from src.config import config
from src.tasks.debug_registry import (
    DEBUG_ONETIME_TASKS,
    DEBUG_TRIGGER_TASKS,
    install_debug_tasks,
)


class DebugRegistryTest(unittest.TestCase):
    def test_probe_tasks_are_not_registered_in_formal_config(self):
        registered = {tuple(item) for item in config["onetime_tasks"]}
        for registration in DEBUG_ONETIME_TASKS:
            with self.subTest(registration=registration):
                self.assertNotIn(tuple(registration), registered)
        registered_triggers = {tuple(item) for item in config["trigger_tasks"]}
        for registration in DEBUG_TRIGGER_TASKS:
            with self.subTest(registration=registration):
                self.assertNotIn(tuple(registration), registered_triggers)

    def test_debug_registry_contains_probe_tasks(self):
        self.assertEqual(
            [
                ["src.tasks.BD2ProbeTask", "BD2ProbeTask"],
                ["src.tasks.BD2MapCollectionProbeTask", "BD2MapCollectionProbeTask"],
                ["src.tasks.MapTradeTask", "BD2MapTradeSellTestTask"],
                ["src.tasks.DailyTask", "BD2BusinessCollectTestTask"],
                ["src.tasks.BD2OneTimeTask", "BD2OneTimeTask"],
                ["src.tasks.BD2DiagnosisTask", "BD2DiagnosisTask"],
                [
                    "src.tasks.BD2InputTestTask",
                    "BD2BackgroundMouseClickInputTestTask",
                ],
            ],
            DEBUG_ONETIME_TASKS,
        )

    def test_install_debug_tasks_is_idempotent(self):
        formal_trigger = ["src.tasks.trigger.AutoLoginTask", "AutoLoginTask"]
        cfg = {
            "onetime_tasks": [["src.tasks.DailyBatchTask", "DailyBatchTask"]],
            "trigger_tasks": [formal_trigger],
        }
        install_debug_tasks(cfg)
        expected_count = 1 + len(DEBUG_ONETIME_TASKS)
        self.assertEqual(expected_count, len(cfg["onetime_tasks"]))
        self.assertEqual(2, len(cfg["trigger_tasks"]))
        install_debug_tasks(cfg)
        self.assertEqual(expected_count, len(cfg["onetime_tasks"]))
        self.assertEqual(2, len(cfg["trigger_tasks"]))
        for registration in DEBUG_ONETIME_TASKS:
            self.assertIn(registration, cfg["onetime_tasks"])
        self.assertEqual(DEBUG_TRIGGER_TASKS + [formal_trigger], cfg["trigger_tasks"])


if __name__ == "__main__":
    unittest.main()
