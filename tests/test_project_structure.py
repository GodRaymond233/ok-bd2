import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTest(unittest.TestCase):
    def test_expected_files_exist(self):
        expected = [
            "main.py",
            "main_debug.py",
            "pyproject.toml",
            "requirements.txt",
            "src/config.py",
            "src/tasks/DailyBatchTask.py",
            "src/tasks/DailyTask.py",
            "src/tasks/QuickHuntTask.py",
            "src/tasks/BargainLevelTask.py",
            "src/tasks/QuickSuppressionTask.py",
            "src/tasks/MapTradeTask.py",
            "src/tasks/MapCollectionTask.py",
            "src/tasks/FreeGachaTask.py",
            "src/tasks/BD2ProbeTask.py",
            "src/tasks/BD2OneTimeTask.py",
            "src/tasks/BaseBD2Task.py",
            "src/tasks/trigger/BD2TriggerTask.py",
            "src/scene/BD2Scene.py",
            "src/interaction/BD2Interaction.py",
            "main_desktop.py",
            "src/desktop/service.py",
            "assets/coco_annotations.json",
            "assets/map_trade/calendar_sources.json",
            "assets/map_trade/price_calendar.v1.json",
        ]
        for relative_path in expected:
            with self.subTest(path=relative_path):
                self.assertTrue((ROOT / relative_path).exists())


if __name__ == "__main__":
    unittest.main()
