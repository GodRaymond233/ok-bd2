import unittest
from pathlib import Path

import cv2

from src.tasks.map_trade.navigator_constants import (
    QUICK_SWITCH_TEMPLATE as NAVIGATOR_QUICK_SWITCH_TEMPLATE,
)
from src.tasks.PVPTask import QUICK_PACK_TEMPLATE as PVP_QUICK_PACK_TEMPLATE
from src.tasks.SquareGoddessTask import (
    QUICK_SWITCH_TEMPLATE as SQUARE_QUICK_SWITCH_TEMPLATE,
)
from src.tasks.SquareGoddessTask import (
    TEMPLATE_DIR,
)
from src.utils import task_vision
from src.utils.cartridge_quick_switch import (
    BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    BATTLE_GAMEPLAY_CATEGORY_LABEL,
    BATTLE_GAMEPLAY_CATEGORY_POINT,
    FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS,
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
    def test_fixed_slot_pre_click_delay_is_800_ms(self):
        self.assertEqual(0.8, FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS)

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


class QuickSwitchDarkButtonFixtureTest(unittest.TestCase):
    """BUG-20260902-06（RPT-20260902-225925）：梦幻广场内快速切换按钮为白图标
    +深色圆底样式，1600x901 实机帧 zncc 最高 0.838，三处 TemplateSpec 的
    min_zncc_score=0.85 曾确定性误拒。夹具为 1600x901 黑底画布，仅按原始
    坐标保留模板搜索条带（广场内其他玩家昵称在条带外），匹配路径与线上
    完全一致（含 ROI 与候选中心约束）。"""

    def _match(self, frame, spec):
        return task_vision.match_template(
            frame, spec, {}, TEMPLATE_DIR, cache={}, min_size=5
        )

    def test_in_square_dark_button_passes_all_quick_switch_specs(self):
        frame = cv2.imread(
            str(FIXTURE_ROOT / "in_square_dark_button_1600x901.png"),
            cv2.IMREAD_COLOR,
        )
        self.assertIsNotNone(frame)
        for spec in (
            SQUARE_QUICK_SWITCH_TEMPLATE,
            PVP_QUICK_PACK_TEMPLATE,
            NAVIGATOR_QUICK_SWITCH_TEMPLATE,
        ):
            result = self._match(frame, spec)
            self.assertTrue(
                task_vision.passes_match(result, spec, {}),
                f"{spec.file_name} 应识别广场内暗色按钮，得到 {result}",
            )

    def test_home_bottom_strip_without_button_is_rejected(self):
        frame = cv2.imread(
            str(FIXTURE_ROOT / "home_bottom_strip_1600x901.png"),
            cv2.IMREAD_COLOR,
        )
        self.assertIsNotNone(frame)
        for spec in (
            SQUARE_QUICK_SWITCH_TEMPLATE,
            PVP_QUICK_PACK_TEMPLATE,
            NAVIGATOR_QUICK_SWITCH_TEMPLATE,
        ):
            result = self._match(frame, spec)
            self.assertFalse(
                task_vision.passes_match(result, spec, {}),
                f"{spec.file_name} 不应在主页底部条带误检，得到 {result}",
            )


if __name__ == "__main__":
    unittest.main()
