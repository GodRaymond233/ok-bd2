import unittest

from src.tasks.main_story import MainStoryState
from src.tasks.main_story_mode import (
    FARM_EVIDENCE_STATES,
    FARM_MODE,
    MODE_OPTIONS,
    WALK_MODE,
    mode_from_evidence,
)
from src.tasks.main_story_walk import MainStoryWalkState


class ModeFromEvidenceTest(unittest.TestCase):

    def test_quick_explore_entry_is_farm(self):
        self.assertEqual(
            mode_from_evidence(MainStoryState.IN_STAGE, MainStoryWalkState.UNKNOWN),
            FARM_MODE,
        )

    def test_quick_explore_beats_field_tracker(self):
        """关卡内右上角也可能有追踪条，但「进行快速探索」是更硬的证据。"""

        self.assertEqual(
            mode_from_evidence(MainStoryState.IN_STAGE, MainStoryWalkState.FIELD),
            FARM_MODE,
        )

    def test_task_list_buttons_are_farm(self):
        for state in (MainStoryState.LIST_AUTO, MainStoryState.LIST_BATTLE):
            with self.subTest(state=state):
                self.assertEqual(
                    mode_from_evidence(state, MainStoryWalkState.UNKNOWN),
                    FARM_MODE,
                )

    def test_lobby_start_difficulty_is_farm(self):
        """大厅追踪条「开始XX难度」是快速探索的正常起点。"""

        self.assertEqual(
            mode_from_evidence(MainStoryState.QUEST_START, MainStoryWalkState.LOBBY),
            FARM_MODE,
        )

    def test_dialogs_are_farm(self):
        for state in (
            MainStoryState.DIFFICULTY_DIALOG,
            MainStoryState.FAST_EXPLORE_DIALOG,
            MainStoryState.ALL_CLEAR,
        ):
            with self.subTest(state=state):
                self.assertIn(state, FARM_EVIDENCE_STATES)
                self.assertEqual(
                    mode_from_evidence(state, MainStoryWalkState.UNKNOWN),
                    FARM_MODE,
                )

    def test_field_is_walk(self):
        self.assertEqual(
            mode_from_evidence(MainStoryState.BUSY, MainStoryWalkState.FIELD),
            WALK_MODE,
        )

    def test_interact_keycap_is_walk(self):
        """有些野外场景没有追踪条，画面上的「F」键帽同样能证明在野外。"""

        self.assertEqual(
            mode_from_evidence(
                MainStoryState.BUSY, MainStoryWalkState.UNKNOWN, interact_visible=True,
            ),
            WALK_MODE,
        )

    def test_battle_and_reward_are_ambiguous(self):
        """战斗、结算、奖励弹窗两边都会出现，不能拿它们定模式。"""

        for farm_state, walk_state in (
            (MainStoryState.IN_BATTLE, MainStoryWalkState.BATTLE),
            (MainStoryState.IN_BATTLE_TURN, MainStoryWalkState.BATTLE_TURN),
            (MainStoryState.VICTORY, MainStoryWalkState.VICTORY),
            (MainStoryState.REWARD_POPUP, MainStoryWalkState.REWARD),
            (MainStoryState.LIST_OTHER_BUTTON, MainStoryWalkState.UNKNOWN),
            (MainStoryState.BUSY, MainStoryWalkState.CUTSCENE),
            (MainStoryState.BUSY, MainStoryWalkState.UNKNOWN),
        ):
            with self.subTest(farm_state=farm_state, walk_state=walk_state):
                self.assertIsNone(
                    mode_from_evidence(farm_state, walk_state),
                )

    def test_mode_options_include_auto_first(self):
        self.assertTrue(MODE_OPTIONS[0].startswith("自动"))
        self.assertIn("只刷快速探索", MODE_OPTIONS)
        self.assertIn("只推主线（野外）", MODE_OPTIONS)


class MergedTaskTest(unittest.TestCase):

    def test_task_class_has_both_modes(self):
        """合并后的任务要能同时跑两套状态机，状态面板也要两边的计数。"""

        from src.tasks.main_story import MainStoryFeatureMixin
        from src.tasks.main_story_walk import (
            MainStoryWalkConfigMixin,
            MainStoryWalkFeatureMixin,
        )
        from src.tasks.MainStoryFarmTask import MainStoryFarmTask

        for mixin in (
            MainStoryFeatureMixin,
            MainStoryWalkFeatureMixin,
            MainStoryWalkConfigMixin,
        ):
            with self.subTest(mixin=mixin.__name__):
                self.assertTrue(issubclass(MainStoryFarmTask, mixin))
        self.assertTrue(callable(MainStoryFarmTask.run_main_story_farm))
        self.assertTrue(callable(MainStoryFarmTask.run_main_story_walk))
        self.assertIn("主线当前模式", MainStoryFarmTask.status_keys)
        self.assertIn("推主线当前目标", MainStoryFarmTask.status_keys)


if __name__ == "__main__":
    unittest.main()
