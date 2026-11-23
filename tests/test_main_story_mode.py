import unittest
from types import SimpleNamespace

from ok.core.config_schema import build_config_fields

from src.tasks.main_story import MainStoryState
from src.tasks.main_story_mode import (
    AUTO_MODE,
    FARM_MODE,
    FARM_ONLY_MODE,
    WALK_MODE,
    WALK_ONLY_MODE,
    mode_from_evidence,
)
from src.tasks.main_story_walk import MainStoryWalkState
from src.tasks.MainStoryFarmTask import MainStoryFarmTask


class ModeFromEvidenceTest(unittest.TestCase):

    def test_desktop_offers_enable_switch_and_options_for_the_selected_mode(self):
        task = MainStoryFarmTask(SimpleNamespace(scene=None), SimpleNamespace())
        for enabled, mode in ((False, AUTO_MODE), (True, AUTO_MODE), (True, FARM_ONLY_MODE),
                              (True, WALK_ONLY_MODE)):
            with self.subTest(enabled=enabled, mode=mode):
                config = {**task.default_config, "启用": enabled, "主线模式": mode}
                keys = {field["key"] for field in build_config_fields(
                    config, task.config_description, task.config_type, task.default_config)}
                self.assertIn("启用", keys)
                self.assertEqual(enabled, "主线模式" in keys)
                self.assertEqual(enabled and mode != WALK_ONLY_MODE, "主线自动开始难度" in keys)
                self.assertEqual(enabled and mode != FARM_ONLY_MODE, "推主线自动交互" in keys)

    def test_mode_requires_evidence_specific_to_farming_or_walking(self):
        for farm_state, walk_state, expected in (
            (MainStoryState.IN_STAGE, MainStoryWalkState.FIELD, FARM_MODE),
            (MainStoryState.BUSY, MainStoryWalkState.FIELD, WALK_MODE),
            (MainStoryState.IN_BATTLE, MainStoryWalkState.BATTLE, None),
            (MainStoryState.VICTORY, MainStoryWalkState.VICTORY, None),
            (MainStoryState.REWARD_POPUP, MainStoryWalkState.REWARD, None),
            (MainStoryState.BUSY, MainStoryWalkState.UNKNOWN, None),
        ):
            with self.subTest(farm_state=farm_state, walk_state=walk_state):
                self.assertEqual(mode_from_evidence(farm_state, walk_state), expected)


if __name__ == "__main__":
    unittest.main()
