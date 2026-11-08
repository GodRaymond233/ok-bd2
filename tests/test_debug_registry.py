import unittest

from src.config import config
from src.tasks.debug_registry import (
    DEBUG_ONETIME_TASKS,
    DEBUG_TRIGGER_TASKS,
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


if __name__ == "__main__":
    unittest.main()
