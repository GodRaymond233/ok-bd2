import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.tasks.PVPTask import (
    ENTRY_REFERENCE_HEIGHT,
    ENTRY_REFERENCE_WIDTH,
    GAMEPLAY_CARTRIDGE_POINT,
    HOME_ICE_TEMPLATE,
    HOME_RICE_TEMPLATE,
    PVP_BACK_HOME_REFERENCE_POINT,
    PVP_CARTRIDGE_SLOT_POINT,
    PVP_CONFIRM_BUTTON_SCREEN_ROI,
    PVP_FAILURE_LEAVE_REFERENCE_POINT,
    PVP_FAILURE_LEAVE_REFERENCE_ROI,
    PVP_HUB_NOTICE_SCREEN_ROI,
    PVP_HUB_NOTICE_TEMPLATE,
    PVP_LOC_RESET_TEMPLATE,
    PVP_MEDALS_TEMPLATE,
    PVP_NO_FIND_TEMPLATES,
    PVP_RANK_DROP_CONFIRM_SCREEN_POINT,
    PVP_RESULT_CLOSE_SCREEN_POINT,
    PVP_RESULT_SCREEN_ROI,
    PVP_STAGE_CLICK_REFERENCE_OFFSET,
    PVP_STAGE_TEMPLATE,
    PVP_SUCCESS_LEAVE_REFERENCE_POINT,
    PVP_SUCCESS_LEAVE_REFERENCE_ROI,
    QUICK_PACK_TEMPLATE,
    QUICK_SWITCH_PAGE_PATTERNS,
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

    def test_quick_pack_uses_requested_template(self):
        self.assertEqual("image/green/BusinQuickIcoGE.png", QUICK_PACK_TEMPLATE.file_name)
        self.assertEqual("快速切换按钮阈值", QUICK_PACK_TEMPLATE.threshold_key)
        self.assertTrue(QUICK_PACK_TEMPLATE.green_mask)
        self.assertEqual((0.25, 0.85, 0.65, 1.0), QUICK_PACK_TEMPLATE.relative_roi)
        self.assertIn(0.80, QUICK_PACK_TEMPLATE.scale_ratios)
        self.assertEqual(0.72, QUICK_PACK_TEMPLATE.min_pixel_score)

        task = object.__new__(PVPTask)
        task._templates = {}
        unflagged_spec = replace(QUICK_PACK_TEMPLATE, green_mask=False)
        _template, mask = PVPTask._load_template(task, unflagged_spec)
        self.assertIsNotNone(mask)
        self.assertGreater(mask.size, int(np.count_nonzero(mask)))

    def test_quick_pack_clicks_recognized_center(self):
        task = object.__new__(PVPTask)
        task.config = {"快速切换按钮阈值": 0.78}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(
            score=0.90,
            pixel_score=0.80,
            position=(815, 962),
            size=(74, 59),
        )
        clicks = []
        task._click_client = lambda x, y, width, height, after_sleep=0.0: clicks.append(
            (x, y, width, height, after_sleep)
        )

        self.assertTrue(
            PVPTask._click_template_until(
                task,
                QUICK_PACK_TEMPLATE,
                timeout=0.0,
                name="快速切换按钮",
            )
        )
        self.assertEqual([(852, 991, 1920, 1080, 0.0)], clicks)

    def test_template_click_scales_reference_offset_with_client_resolution(self):
        task = object.__new__(PVPTask)
        task.config = {"PVP 舞台阈值": 0.72}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(
            score=0.98,
            pixel_score=0.97,
            position=(492, 474),
            size=(76, 19),
        )
        clicks = []
        task._click_client = lambda x, y, width, height, after_sleep=0.0: clicks.append(
            (x, y, width, height, after_sleep)
        )

        self.assertTrue(
            PVPTask._click_template_until(
                task,
                PVP_STAGE_TEMPLATE,
                timeout=0.0,
                name="PVP 舞台",
                target_reference_offset=PVP_STAGE_CLICK_REFERENCE_OFFSET,
            )
        )
        self.assertEqual([(530, 433, 1280, 720, 0.0)], clicks)

    def test_quick_pack_requires_pixel_similarity(self):
        task = object.__new__(PVPTask)
        task.config = {"快速切换按钮阈值": 0.78}

        low_pixel = SimpleNamespace(score=0.95, pixel_score=0.60)
        valid = SimpleNamespace(score=0.90, pixel_score=0.80)

        self.assertFalse(PVPTask._passes(task, low_pixel, QUICK_PACK_TEMPLATE))
        self.assertTrue(PVPTask._passes(task, valid, QUICK_PACK_TEMPLATE))

    def test_relative_roi_uses_frame_ratios(self):
        frame = np.arange(1080 * 1920, dtype=np.int32).reshape((1080, 1920))

        left, top, crop = PVPTask._relative_roi_frame(
            frame,
            QUICK_PACK_TEMPLATE.relative_roi,
        )

        self.assertEqual((480, 918), (left, top))
        self.assertEqual((162, 768), crop.shape)
        np.testing.assert_array_equal(crop, frame[918:1080, 480:1248])

    def test_crop_reference_scales_roi_to_frame_size(self):
        frame = np.arange(720 * 1280, dtype=np.int32).reshape((720, 1280))

        crop = PVPTask._crop_reference(frame, (960, 540, 192, 108))

        self.assertEqual((72, 128), crop.shape)
        np.testing.assert_array_equal(crop, frame[360:432, 640:768])

    def test_target_multiplier_accepts_supported_values(self):
        task = object.__new__(PVPTask)

        task.config = {"竞技场战斗倍数": "20倍"}
        self.assertEqual(20, PVPTask._target_multiplier(task))

        task.config = {"竞技场战斗倍数": "4倍"}
        self.assertEqual(4, PVPTask._target_multiplier(task))

        task.config = {"竞技场战斗倍数": "3倍"}
        self.assertEqual(1, PVPTask._target_multiplier(task))

    def test_common_cartridge_entry_uses_relative_recent_entry_point(self):
        task = object.__new__(PVPTask)
        calls = []
        task.operate_click = lambda x, y, after_sleep=0: calls.append((x, y, after_sleep))
        stages = []

        self.assertTrue(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: stages.append("home") or True,
                click_quick_switch=lambda: stages.append("click") or True,
                confirm_quick_switch_page=lambda: stages.append("confirm") or True,
            )
        )
        self.assertEqual([(0.7875, 0.9111111111111111, 1.0)], calls)
        self.assertEqual(["home", "click", "confirm"], stages)

    def test_common_cartridge_entry_stops_when_home_is_not_confirmed(self):
        task = object.__new__(PVPTask)
        task.operate_click = lambda *_args, **_kwargs: self.fail("entry must not be clicked")

        self.assertFalse(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: False,
                click_quick_switch=lambda: self.fail("quick switch must not be searched"),
                confirm_quick_switch_page=lambda: self.fail("page must not be confirmed"),
            )
        )

    def test_common_cartridge_entry_stops_when_quick_switch_click_fails(self):
        task = object.__new__(PVPTask)
        task.operate_click = lambda *_args, **_kwargs: None

        self.assertFalse(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: True,
                click_quick_switch=lambda: False,
                confirm_quick_switch_page=lambda: self.fail("page must not be confirmed"),
            )
        )

    def test_quick_switch_page_requires_all_three_ocr_labels(self):
        task = object.__new__(PVPTask)
        task.config = {"卡带选择页确认等待秒数": 1.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        texts = ["最近 剧情游戏卡", "最近 剧情游戏卡 玩法游戏卡"]
        task._ocr_text = lambda *_args, **_kwargs: texts.pop(0)
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)

        self.assertTrue(PVPTask._wait_for_quick_switch_page(task))
        self.assertEqual([0.5], sleeps)
        self.assertEqual((r"最近", r"剧情游戏卡", r"玩法游戏卡"), QUICK_SWITCH_PAGE_PATTERNS)

    def test_quick_switch_page_timeout_stops_entry(self):
        task = object.__new__(PVPTask)
        task.config = {"卡带选择页确认等待秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        logs = []
        task.log_info = lambda message: logs.append(message)
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._ocr_text = lambda *_args, **_kwargs: "最近 剧情游戏卡"
        task.sleep = lambda *_args, **_kwargs: None

        self.assertFalse(PVPTask._wait_for_quick_switch_page(task))
        self.assertIn("未确认卡带选择页", logs[0])

    def test_cartridge_home_requires_button_and_brightness(self):
        task = object.__new__(PVPTask)
        task.config = {
            "主页确认等待秒数": 0.0,
            "主页小屋按钮阈值": 0.70,
            "主页亮度比例阈值": 0.75,
        }
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda *_args, **_kwargs: SimpleNamespace(score=0.80)
        task._home_brightness_ratio = lambda _frame: 0.74
        task.sleep = lambda *_args, **_kwargs: None

        self.assertFalse(PVPTask._wait_for_cartridge_home(task))

        task._home_brightness_ratio = lambda _frame: 0.80
        self.assertTrue(PVPTask._wait_for_cartridge_home(task))

    def test_pvp_uses_fixed_first_gameplay_cartridge_slot(self):
        self.assertEqual((152 / 1920, 970 / 1080), PVP_CARTRIDGE_SLOT_POINT)

    def test_pvp_hub_uses_1920_roi_and_calibrated_template_scale(self):
        self.assertEqual((793, 39, 340, 35), PVP_MEDALS_TEMPLATE.roi)
        self.assertIsNone(PVP_MEDALS_TEMPLATE.reference_scale)
        self.assertEqual(0.88, PVP_MEDALS_TEMPLATE.min_pixel_score)
        self.assertEqual(
            [1.18, 1.2, 1.22, 1.25, 1.3],
            PVPTask._candidate_scales(
                1.25,
                PVP_MEDALS_TEMPLATE.scale_ratios,
            ),
        )

        frame = np.zeros((1078, 1918, 3), dtype=np.uint8)
        left, top, crop = PVPTask._roi_frame(frame, PVP_MEDALS_TEMPLATE.roi)
        self.assertEqual((792, 39), (left, top))
        self.assertEqual((35, 340, 3), crop.shape)

    def test_pvp_assets_use_image_folder(self):
        template_root = Path("offline-train/train-source-screenshots")
        specs = (
            HOME_ICE_TEMPLATE,
            HOME_RICE_TEMPLATE,
            PVP_MEDALS_TEMPLATE,
            PVP_STAGE_TEMPLATE,
            PVP_LOC_RESET_TEMPLATE,
            *PVP_NO_FIND_TEMPLATES,
        )

        for spec in specs:
            self.assertTrue(spec.file_name.startswith("image/"), spec.file_name)
            self.assertTrue((template_root / spec.file_name).is_file(), spec.file_name)

    def test_pvp_entry_clicks_gameplay_then_fixed_first_slot(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.open_cartridge_quick_switcher = lambda **_kwargs: True
        clicks = []
        sleeps = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task.sleep = lambda seconds: sleeps.append(seconds)
        task._click_template_until = lambda *_args, **_kwargs: self.fail(
            "fixed PVP slot selection must not use template matching"
        )
        hub_waits = []
        task._wait_for_pvp_hub_after_cart = lambda timeout: hub_waits.append(timeout) or True
        task._clear_pvp_hub_notice_if_present = lambda: None
        task.config = {}

        self.assertTrue(PVPTask._enter_pvp_from_home(task))
        self.assertEqual([0.5], sleeps)
        self.assertEqual(
            [
                (*GAMEPLAY_CARTRIDGE_POINT, 0.5),
                (*PVP_CARTRIDGE_SLOT_POINT, 0.0),
            ],
            clicks,
        )
        self.assertEqual([30.0], hub_waits)

    def test_pvp_entry_stops_when_hub_is_not_confirmed(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.open_cartridge_quick_switcher = lambda **_kwargs: True
        task.sleep = lambda *_args, **_kwargs: None
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append((x, y, after_sleep))
        task._wait_for_pvp_hub_after_cart = lambda *_args, **_kwargs: False
        task._clear_pvp_hub_notice_if_present = lambda: self.fail(
            "hub notice must not be checked before the PVP hub is confirmed"
        )
        task.config = {}

        self.assertFalse(PVPTask._enter_pvp_from_home(task))
        self.assertEqual(
            [
                (*GAMEPLAY_CARTRIDGE_POINT, 0.5),
                (*PVP_CARTRIDGE_SLOT_POINT, 0.0),
            ],
            clicks,
        )

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

    def test_result_wait_timeout_scales_by_multiplier(self):
        task = object.__new__(PVPTask)
        task.config = {}

        self.assertEqual(20 * 60, PVPTask._result_wait_timeout(task, 1))
        self.assertEqual(5 * 60, PVPTask._result_wait_timeout(task, 4))
        self.assertEqual(4 * 60, PVPTask._result_wait_timeout(task, 5))

    def test_result_patterns_include_completed_count_from_multiplier(self):
        task = object.__new__(PVPTask)

        patterns = PVPTask._pvp_result_patterns(task, 4)
        text = "反复战斗结果 胜利分 已完成10次的战斗。 攻击成绩"

        self.assertGreaterEqual(PVPTask._ocr_pattern_match_count(text, patterns), 4)

    def test_result_screen_roi_converts_from_2560_reference(self):
        self.assertEqual(
            (699, 276, 524, 528),
            PVPTask._screen_reference_roi_to_reference_roi(PVP_RESULT_SCREEN_ROI),
        )

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

    def test_wait_result_uses_dynamic_timeout_and_majority_roi(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        calls = {}
        sleeps = []
        screen_clicks = []

        def fake_wait(patterns, min_matches, timeout, name, roi, **_kwargs):
            calls["patterns"] = patterns
            calls["min_matches"] = min_matches
            calls["timeout"] = timeout
            calls["name"] = name
            calls["roi"] = roi
            return True, "反复战斗结果 胜利分 已完成10次的战斗 攻击成绩"

        task._wait_for_ocr_pattern_majority = fake_wait
        task._click_reference = lambda *_args, **_kwargs: self.fail(
            "result page should not be clicked before leave"
        )
        task.sleep = lambda seconds: sleeps.append(seconds)
        task._click_screen_reference = lambda x, y, after_sleep=0.0: screen_clicks.append(
            (x, y, after_sleep)
        )
        task._click_leave_button = lambda: True
        task._ensure_pvp_hub_after_leave = lambda: True
        task._return_home_from_pvp_hub = lambda: True

        self.assertTrue(PVPTask._wait_result_and_leave(task, 4))
        self.assertEqual([1.0], sleeps)
        self.assertEqual([(*PVP_RESULT_CLOSE_SCREEN_POINT, 0.0)], screen_clicks)
        self.assertEqual(4, calls["min_matches"])
        self.assertEqual(5 * 60, calls["timeout"])
        self.assertEqual("PVP 结算", calls["name"])
        self.assertEqual(
            PVPTask._screen_reference_roi_to_reference_roi(PVP_RESULT_SCREEN_ROI),
            calls["roi"],
        )
        self.assertGreaterEqual(
            PVPTask._ocr_pattern_match_count(
                "反复战斗结果 胜利分 已完成10次的战斗 攻击成绩",
                calls["patterns"],
            ),
            4,
        )

    def test_wait_result_fails_when_return_home_fails(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task._wait_for_ocr_pattern_majority = lambda *_args, **_kwargs: (True, "反复战斗结果")
        task._click_reference = lambda *_args, **_kwargs: self.fail(
            "result page should not be clicked before leave"
        )
        task.sleep = lambda *_args, **_kwargs: None
        task._click_screen_reference = lambda *_args, **_kwargs: None
        task._click_leave_button = lambda: True
        task._ensure_pvp_hub_after_leave = lambda: True
        task._return_home_from_pvp_hub = lambda: False

        self.assertFalse(PVPTask._wait_result_and_leave(task, 1))

    def test_pvp_entry_wait_confirms_rank_drop_before_detecting_hub(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._ocr_text = lambda *_args, **_kwargs: "段位下滑。 确认"
        clicks = []
        task._click_screen_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        matched = []
        task._match = lambda _frame, spec: matched.append(spec) or SimpleNamespace(score=0.9)
        task._passes = lambda _result, spec: spec is PVP_MEDALS_TEMPLATE
        task.sleep = lambda *_args, **_kwargs: self.fail(
            "rank confirmation and hub detection should not add polling sleep"
        )

        self.assertTrue(PVPTask._wait_for_pvp_hub_after_cart(task, timeout=1.0))

        self.assertEqual([(*PVP_RANK_DROP_CONFIRM_SCREEN_POINT, 0.5)], clicks)
        self.assertEqual([PVP_MEDALS_TEMPLATE], matched)

    def test_pvp_entry_wait_keeps_ocr_active_until_hub_is_detected(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        ocr_calls = []
        task._ocr_text = lambda *_args, **_kwargs: ocr_calls.append(True) or "-"
        scores = iter((0.1, 0.9))
        task._match = lambda *_args, **_kwargs: SimpleNamespace(score=next(scores))
        task._passes = lambda result, _spec: result.score >= 0.78
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)
        task._click_screen_reference = lambda *_args, **_kwargs: self.fail(
            "rank confirmation must not be clicked without both OCR labels"
        )

        self.assertTrue(PVPTask._wait_for_pvp_hub_after_cart(task, timeout=1.0))
        self.assertEqual(2, len(ocr_calls))
        self.assertEqual([0.5], sleeps)

    def test_clear_pvp_hub_notice_clicks_notice_center_and_waits(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        matched_specs = []
        sleeps = []
        clicks = []

        def fake_match(_frame, spec):
            matched_specs.append(spec)
            return SimpleNamespace(score=0.9)

        task._match = fake_match
        task._passes = lambda _result, spec: spec is PVP_HUB_NOTICE_TEMPLATE
        task.sleep = lambda seconds: sleeps.append(seconds)
        task._click_screen_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )

        PVPTask._clear_pvp_hub_notice_if_present(task)

        self.assertEqual([PVP_HUB_NOTICE_TEMPLATE], matched_specs)
        self.assertEqual([1.0], sleeps)
        self.assertEqual(
            [(*PVPTask._screen_reference_roi_center(PVP_HUB_NOTICE_SCREEN_ROI), 5.0)],
            clicks,
        )

    def test_ensure_pvp_hub_clears_notice_when_already_in_hub(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        cleared = []

        task._wait_for_template = lambda *_args, **_kwargs: True
        task._clear_pvp_hub_notice_if_present = lambda: cleared.append(True)
        task._enter_pvp_from_home = lambda: self.fail("hub should already be detected")

        self.assertTrue(PVPTask._ensure_pvp_hub(task))
        self.assertEqual([True], cleared)

    def test_return_home_from_pvp_hub_clicks_back_reference_and_checks_home(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        wait_calls = []
        clicks = []
        loading_calls = []
        home_calls = []

        def fake_wait_for_template(spec, timeout, name, **_kwargs):
            wait_calls.append((spec, timeout, name))
            return True

        task._wait_for_template = fake_wait_for_template
        task._click_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task._wait_loading_if_present = lambda name: loading_calls.append(name)
        task._wait_for_home = lambda timeout: home_calls.append(timeout) or True

        self.assertTrue(PVPTask._return_home_from_pvp_hub(task))
        self.assertEqual([(PVP_MEDALS_TEMPLATE, 10.0, "PVP 箱庭")], wait_calls)
        self.assertEqual([(*PVP_BACK_HOME_REFERENCE_POINT, 2.0)], clicks)
        self.assertEqual(["PVP 返回主页"], loading_calls)
        self.assertEqual([20.0], home_calls)

    def test_click_leave_button_checks_both_regions_and_clicks_failure_target(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        clicks = []

        def fake_ocr(_frame, name, roi=None):
            ocr_calls.append((name, roi))
            return "离开" if name == "pvp_leave_failure" else ""

        task._ocr_text = fake_ocr
        task._click_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task.sleep = lambda *_args, **_kwargs: None

        self.assertTrue(PVPTask._click_leave_button(task))
        self.assertEqual(
            [
                ("pvp_leave_failure", PVP_FAILURE_LEAVE_REFERENCE_ROI),
                ("pvp_leave_success", PVP_SUCCESS_LEAVE_REFERENCE_ROI),
            ],
            ocr_calls,
        )
        self.assertEqual(
            (*PVP_FAILURE_LEAVE_REFERENCE_POINT, 2.0),
            clicks[0],
        )

    def test_click_leave_button_clicks_success_target(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._ocr_text = lambda _frame, name, roi=None: (
            "离开" if name == "pvp_leave_success" else ""
        )
        clicks = []
        task._click_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task.sleep = lambda *_args, **_kwargs: None

        self.assertTrue(PVPTask._click_leave_button(task))
        self.assertEqual([(*PVP_SUCCESS_LEAVE_REFERENCE_POINT, 2.0)], clicks)

    def test_leave_targets_use_1920_reference_coordinates(self):
        self.assertEqual((696, 952, 535, 87), PVP_FAILURE_LEAVE_REFERENCE_ROI)
        self.assertEqual((1058, 996), PVP_FAILURE_LEAVE_REFERENCE_POINT)
        self.assertEqual((1594, 987, 240, 66), PVP_SUCCESS_LEAVE_REFERENCE_ROI)
        self.assertEqual((1707, 1021), PVP_SUCCESS_LEAVE_REFERENCE_POINT)

    def test_ensure_pvp_hub_after_leave_returns_when_hub_seen(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task._wait_for_pvp_hub_or_confirm = lambda timeout: ("hub", "")
        task._click_screen_reference = lambda *_args, **_kwargs: self.fail(
            "confirm should not be clicked after hub is detected"
        )

        self.assertTrue(PVPTask._ensure_pvp_hub_after_leave(task))

    def test_ensure_pvp_hub_after_leave_clicks_confirm_then_waits_hub(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        clicks = []
        waits = []

        task._wait_for_pvp_hub_or_confirm = lambda timeout: ("confirm", "确认")
        task._click_screen_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )

        def fake_wait_for_template(spec, timeout, name, **_kwargs):
            waits.append((spec, timeout, name))
            return True

        task._wait_for_template = fake_wait_for_template

        self.assertTrue(PVPTask._ensure_pvp_hub_after_leave(task))
        self.assertEqual(
            [(*PVPTask._screen_reference_roi_center(PVP_CONFIRM_BUTTON_SCREEN_ROI), 1.0)],
            clicks,
        )
        self.assertEqual([(PVP_MEDALS_TEMPLATE, 10.0, "PVP 箱庭")], waits)

    def test_wait_for_pvp_hub_or_confirm_detects_confirm_roi(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.1)
        task._passes = lambda _result, _spec: False
        ocr_calls = []

        def fake_ocr(_frame, name, roi=None):
            ocr_calls.append((name, roi))
            return "确认"

        task._ocr_text = fake_ocr
        task.sleep = lambda *_args, **_kwargs: self.fail("confirm should be immediate")

        self.assertEqual(
            ("confirm", "确认"),
            PVPTask._wait_for_pvp_hub_or_confirm(task, timeout=1.0),
        )
        self.assertEqual(
            [(
                "PVP 升降级确认",
                PVPTask._screen_reference_roi_to_reference_roi(PVP_CONFIRM_BUTTON_SCREEN_ROI),
            )],
            ocr_calls,
        )

    def test_wait_for_pvp_hub_or_confirm_prefers_hub(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.95)
        task._passes = lambda _result, _spec: True
        task._ocr_text = lambda *_args, **_kwargs: self.fail(
            "confirm OCR should not run after hub is detected"
        )
        task.sleep = lambda *_args, **_kwargs: self.fail("hub should be immediate")

        self.assertEqual(
            ("hub", ""),
            PVPTask._wait_for_pvp_hub_or_confirm(task, timeout=1.0),
        )

    def test_drag_client_uses_foreground_operate(self):
        operates = []
        sleeps = []

        class FakeInteraction:
            def post(self, message, w_param=0, l_param=0):
                raise AssertionError("drag should not use background window messages")

        class PVPTaskForTest(PVPTask):
            @property
            def executor(self):
                return SimpleNamespace(interaction=FakeInteraction())

        task = object.__new__(PVPTaskForTest)
        task.operate = lambda func, block=True, restore_cursor=True: operates.append(
            (callable(func), block, restore_cursor)
        )
        task.sleep = lambda seconds: sleeps.append(seconds)

        PVPTask._drag_client(task, (10, 20), (30, 40), duration=0.0, after_sleep=0.5)

        self.assertEqual([(True, True, True)], operates)
        self.assertEqual([0.5], sleeps)

    def test_start_auto_battle_clicks_start_without_start_ocr_gate(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        template_clicks = []

        def fake_click_template_until(*args, **kwargs):
            template_clicks.append((args, kwargs))
            return True

        task._click_template_until = fake_click_template_until
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
        self.assertEqual(
            PVP_STAGE_CLICK_REFERENCE_OFFSET,
            template_clicks[0][1]["target_reference_offset"],
        )
        self.assertIn((2026, 1291, 1.0), clicks)
        self.assertIn((1381, 1061, 10.0), clicks)


if __name__ == "__main__":
    unittest.main()
