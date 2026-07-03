import unittest
from types import SimpleNamespace

import numpy as np

from src.tasks.PVPTask import (
    ENTRY_REFERENCE_HEIGHT,
    ENTRY_REFERENCE_WIDTH,
    EVILCASTLE_CARD_TEMPLATE,
    PVP_ENTRY_CARD_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    PVPTask,
)


class PVPTaskHelperTest(unittest.TestCase):
    def test_reference_click_uses_1920_by_1080_ratios(self):
        task = object.__new__(PVPTask)
        calls = {}

        def fake_operate_click(x, y, after_sleep=0):
            calls["x"] = x
            calls["y"] = y
            calls["after_sleep"] = after_sleep

        task.operate_click = fake_operate_click

        task._click_reference(953, 631, after_sleep=1.0)

        self.assertEqual(953 / REFERENCE_WIDTH, calls["x"])
        self.assertEqual(631 / REFERENCE_HEIGHT, calls["y"])
        self.assertEqual(1.0, calls["after_sleep"])

    def test_entry_click_uses_2560_by_1440_ratios(self):
        task = object.__new__(PVPTask)
        calls = {}

        def fake_operate_click(x, y, after_sleep=0):
            calls["x"] = x
            calls["y"] = y
            calls["after_sleep"] = after_sleep

        task.operate_click = fake_operate_click

        task._click_entry_reference(2258, 1307, after_sleep=1.0)

        self.assertEqual(2258 / ENTRY_REFERENCE_WIDTH, calls["x"])
        self.assertEqual(1307 / ENTRY_REFERENCE_HEIGHT, calls["y"])
        self.assertEqual(1.0, calls["after_sleep"])

    def test_evilcastle_uses_separate_threshold(self):
        self.assertEqual("PVP 恶魔城阈值", EVILCASTLE_CARD_TEMPLATE.threshold_key)
        self.assertEqual(0.70, EVILCASTLE_CARD_TEMPLATE.default_threshold)
        self.assertEqual("PVP 入口卡带阈值", PVP_ENTRY_CARD_TEMPLATE.threshold_key)

    def test_crop_reference_scales_roi_to_frame_size(self):
        frame = np.arange(720 * 1280, dtype=np.int32).reshape((720, 1280))

        crop = PVPTask._crop_reference(frame, (960, 540, 192, 108))

        self.assertEqual((72, 128), crop.shape)
        np.testing.assert_array_equal(crop, frame[360:432, 640:768])

    def test_target_multiplier_accepts_supported_values(self):
        task = object.__new__(PVPTask)

        task.config = {"竞技场战斗倍数": "20倍"}
        self.assertEqual(20, PVPTask._target_multiplier(task))

        task.config = {"竞技场战斗倍数": "3倍"}
        self.assertEqual(1, PVPTask._target_multiplier(task))

    def test_matches_any_normalizes_ocr_text(self):
        self.assertTrue(PVPTask._matches_any("战斗 开始", [r"战斗开始"]))
        self.assertTrue(PVPTask._matches_any("40 倍", [r"^40倍$"]))
        self.assertTrue(PVPTask._matches_any("店长游戏卡 2/2", [r"店长游戏卡\s*\d+\s*/\s*\d+"]))
        self.assertTrue(PVPTask._matches_any("剧情游戏卡 12/20", [r"剧情游戏卡\s*\d+\s*/\s*20"]))
        self.assertFalse(
            PVPTask._matches_any(
                "角色游戏卡9/9各种角色的平行世界剧情游戏卡",
                [r"剧情游戏卡\s*\d+\s*/\s*20"],
            )
        )
        self.assertFalse(PVPTask._matches_any("正在进行", [r"反复战斗结果"]))

    def test_pvp_label_click_point_uses_leftmost_lower_label(self):
        boxes = [
            SimpleNamespace(name="玩法游戏卡8/8可进行PVP", x=470, y=590, width=360, height=30),
            SimpleNamespace(name="PvP", x=1500, y=775, width=56, height=28),
            SimpleNamespace(name="PvP", x=410, y=775, width=56, height=28),
        ]

        self.assertEqual((438, 697), PVPTask._pvp_label_click_point(boxes, 1920, 1080))

    def test_pvp_label_click_point_ignores_upper_label(self):
        boxes = [SimpleNamespace(name="PvP", x=410, y=420, width=56, height=28)]

        self.assertIsNone(PVPTask._pvp_label_click_point(boxes, 1920, 1080))

    def test_ocr_requirements_use_per_keyword_confidence(self):
        task = object.__new__(PVPTask)
        entries = [
            ("游戏卡珍藏集", 0.91),
            ("角色游戏卡", 0.70),
            ("玩法游戏卡", 0.76),
        ]

        self.assertTrue(
            PVPTask._ocr_requirements_met(
                task,
                entries,
                [
                    (r"游戏卡珍藏[集级]", 0.90),
                    (r"角色游戏卡", 0.70),
                    (r"玩法游戏卡", 0.70),
                ],
            )
        )
        self.assertTrue(
            PVPTask._ocr_requirements_met(
                task,
                [("游戏卡珍藏级", 0.91), ("角色游戏卡", 0.80), ("玩法游戏卡", 0.80)],
                [
                    (r"游戏卡珍藏[集级]", 0.90),
                    (r"角色游戏卡", 0.70),
                    (r"玩法游戏卡", 0.70),
                ],
            )
        )
        self.assertFalse(
            PVPTask._ocr_requirements_met(
                task,
                [("游戏卡珍藏集", 0.89), ("角色游戏卡", 0.80), ("玩法游戏卡", 0.80)],
                [
                    (r"游戏卡珍藏[集级]", 0.90),
                    (r"角色游戏卡", 0.70),
                    (r"玩法游戏卡", 0.70),
                ],
            )
        )

    def test_run_falls_back_to_one_multiplier_when_ap_shortage(self):
        task = object.__new__(PVPTask)
        task.config = {"启用": True, "竞技场战斗倍数": 10, "最多战斗轮次": 3}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._ensure_pvp_hub = lambda: True
        task.sleep = lambda *_args, **_kwargs: None
        starts = []

        def fake_start(multiplier):
            starts.append(multiplier)
            if len(starts) == 1:
                return "ap_shortage"
            return "ap_depleted"

        task._start_auto_battle = fake_start
        task._wait_result_and_leave = lambda: self.fail("battle should not start")

        self.assertTrue(PVPTask.run(task))
        self.assertEqual([10, 1], starts)

    def test_start_auto_battle_clicks_start_without_start_ocr_gate(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task._click_template_until = lambda *_args, **_kwargs: True
        task._ensure_free_ap_enabled = lambda: True
        task._ensure_multiplier = lambda _multiplier: None
        task._select_max_battle_count = lambda: None
        task._ocr_text = lambda *_args, **_kwargs: self.fail("start click should not wait for OCR")
        clicks = []

        def fake_wait_for_ocr_patterns(_patterns, timeout, name, **_kwargs):
            if name == "PVP 自动战斗":
                return True, "自动战斗"
            if name == "PVP 自动战斗菜单":
                return True, "鲜血鸡尾酒"
            return False, ""

        def fake_click_screen_reference(x, y, after_sleep=0.0):
            clicks.append((x, y, after_sleep))

        task._wait_for_ocr_patterns = fake_wait_for_ocr_patterns
        task._click_screen_reference = fake_click_screen_reference

        self.assertEqual("started", PVPTask._start_auto_battle(task, 1))
        self.assertIn((2026, 1291, 1.0), clicks)
        self.assertIn((1381, 1061, 10.0), clicks)


if __name__ == "__main__":
    unittest.main()
