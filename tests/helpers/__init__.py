"""Test helper package."""

import tempfile
import unittest
from unittest.mock import patch

from src.tasks.run_history import RunHistoryStore


class StageHistoryTestCase(unittest.TestCase):
    """Isolate persistent task evidence for every simulated game run."""

    def setUp(self):
        super().setUp()
        self.history_patch = patch("src.tasks.run_history.default_store")
        self.history_mock = self.history_patch.start()
        self.addCleanup(self.history_patch.stop)
        self.reset_stage_history()

    def reset_stage_history(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = RunHistoryStore(f"{directory.name}/history.json")
        self.history_mock.return_value = self.history
