import unittest
from pathlib import Path

import cv2

from src.utils.cartridge_quick_switch import (
    BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    BATTLE_GAMEPLAY_CATEGORY_LABEL,
    BATTLE_GAMEPLAY_CATEGORY_POINT,
    GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    LIFE_GAMEPLAY_CATEGORY_LABEL,
    LIFE_GAMEPLAY_CATEGORY_POINT,
    QUICK_SWITCH_PAGE_LABELS,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    category_highlight_ratio,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "quick_switch"


class CartridgeQuickSwitchLayoutTest(unittest.TestCase):
    def test_new_page_labels_and_category_points_follow_left_to_right_layout(self):
        self.assertEqual(
            (
                "最近",
                "店长游戏卡",
                "剧情游戏卡",
                "角色游戏卡",
                BATTLE_GAMEPLAY_CATEGORY_LABEL,
                LIFE_GAMEPLAY_CATEGORY_LABEL,
                "活动游戏卡",
            ),
            QUICK_SWITCH_PAGE_LABELS,
        )
        self.assertEqual(
            (923 / REFERENCE_WIDTH, 875 / REFERENCE_HEIGHT),
            BATTLE_GAMEPLAY_CATEGORY_POINT,
        )
        self.assertEqual(
            (1126 / REFERENCE_WIDTH, 875 / REFERENCE_HEIGHT),
            LIFE_GAMEPLAY_CATEGORY_POINT,
        )

    def test_live_fhd_fixtures_separate_battle_and_life_selected_states(self):
        battle = cv2.imread(
            str(FIXTURE_ROOT / "battle_gameplay_selected_fhd.png"),
            cv2.IMREAD_COLOR,
        )
        life = cv2.imread(
            str(FIXTURE_ROOT / "life_gameplay_selected_fhd.png"),
            cv2.IMREAD_COLOR,
        )
        self.assertIsNotNone(battle)
        self.assertIsNotNone(life)
        self.assertEqual((REFERENCE_HEIGHT, REFERENCE_WIDTH), battle.shape[:2])
        self.assertEqual((REFERENCE_HEIGHT, REFERENCE_WIDTH), life.shape[:2])

        battle_selected = category_highlight_ratio(
            battle,
            BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
        )
        battle_when_life_selected = category_highlight_ratio(
            life,
            BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
        )
        life_selected = category_highlight_ratio(
            life,
            LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
        )
        life_when_battle_selected = category_highlight_ratio(
            battle,
            LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
        )

        self.assertGreaterEqual(battle_selected, GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO)
        self.assertLess(battle_when_life_selected, GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO)
        self.assertGreaterEqual(life_selected, GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO)
        self.assertLess(life_when_battle_selected, GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO)


if __name__ == "__main__":
    unittest.main()
