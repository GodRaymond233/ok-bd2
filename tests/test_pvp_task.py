import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.tasks.BaseBD2Task import (
    RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS,
    RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
    RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
    RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD,
    RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
)
from src.tasks.BaseBD2Task import TEMPLATE_DIR as RECENT_CARTRIDGE_TEMPLATE_DIR
from src.tasks.BD2MapCollectionProbeTask import BD2MapCollectionProbeTask
from src.tasks.map_trade.navigator_constants import CHAPTER_HOME_POINT
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.MapTradeTask import MapTradeTask
from src.tasks.PVPTask import (
    ENTRY_REFERENCE_HEIGHT,
    ENTRY_REFERENCE_WIDTH,
    HOME_GACHA_OCR_ROI,
    HOME_ICE_TEMPLATE,
    HOME_RICE_TEMPLATE,
    HOME_TEMPLATE,
    PVP_AUTO_BATTLE_CLICK_REFERENCE,
    PVP_AUTO_BATTLE_SCREEN_ROI,
    PVP_BACK_HOME_REFERENCE_POINT,
    PVP_CARTRIDGE_SLOT_POINT,
    PVP_FAILURE_LEAVE_REFERENCE_ROI,
    PVP_HUB_NOTICE_SCREEN_ROI,
    PVP_HUB_NOTICE_TEMPLATE,
    PVP_HUB_SPECIAL_PAGE_GRACE_SECONDS,
    PVP_LOC_RESET_TEMPLATE,
    PVP_MEDALS_TEMPLATE,
    PVP_NO_FIND_TEMPLATES,
    PVP_RANK_CONFIRM_SETTLE_SECONDS,
    PVP_RANK_PAGE_AFTER_CLICK_SECONDS,
    PVP_RESULT_CLOSE_AFTER_SECONDS,
    PVP_RESULT_CLOSE_SCREEN_POINT,
    PVP_RESULT_SCREEN_ROI,
    PVP_SEASON_REWARD_AFTER_CLICK_SECONDS,
    PVP_STAGE_CLICK_REFERENCE_OFFSET,
    PVP_STAGE_TEMPLATE,
    PVP_SUCCESS_LEAVE_REFERENCE_ROI,
    QUICK_PACK_TEMPLATE,
    QUICK_SWITCH_PAGE_PATTERNS,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    PVPTask,
)
from src.tasks.SquareGoddessTask import SquareGoddessTask
from src.utils.cartridge_quick_switch import (
    BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    BATTLE_GAMEPLAY_CATEGORY_LABEL,
    BATTLE_GAMEPLAY_CATEGORY_OCR_ROI,
    BATTLE_GAMEPLAY_CATEGORY_POINT,
    FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS,
    GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
)
from src.utils.image_utils import candidate_scales


class PVPTaskHelperTest(unittest.TestCase):
    def test_match_without_roi_uses_full_frame(self):
        task = object.__new__(PVPTask)
        task.config = {"主页亮度比例阈值": 0.75}
        task._match_pause_until = 0.0
        task._missing_template_names = set()
        task._match_error_names = set()
        task._templates = {}
        task._load_template = lambda _spec: (
            np.ones((5, 5), dtype=np.uint8),
            None,
        )
        frame = np.zeros((20, 30), dtype=np.uint8)
        candidate = SimpleNamespace(score=0.90, pixel_score=0.85, location=(7, 9))

        with (
            patch(
                "src.utils.image_utils.template_match_response",
                return_value=np.array([[0.90]], dtype=np.float32),
            ) as response_mock,
            patch(
                "src.utils.image_utils.best_pixel_valid_match",
                return_value=candidate,
            ),
            patch("src.utils.image_utils.candidate_scales", return_value=[1.0]),
            patch(
                "src.utils.image_utils.resize_template",
                side_effect=lambda template, _scale: template,
            ),
            patch(
                "src.utils.image_utils.resize_mask",
                side_effect=lambda mask, _scale: mask,
            ),
            patch(
                "src.utils.template_resolution.offline_template_scale",
                return_value=1.0,
            ),
        ):
            result = PVPTask._match(task, frame, HOME_TEMPLATE)

        np.testing.assert_array_equal(response_mock.call_args.args[0], frame)
        self.assertEqual(frame.shape, response_mock.call_args.args[0].shape)
        self.assertEqual((7, 9), result.position)
        self.assertEqual((5, 5), result.size)

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
        self.assertEqual("image/green/QuickSwitchPlayIco.png", QUICK_PACK_TEMPLATE.file_name)
        self.assertEqual("快速切换按钮阈值", QUICK_PACK_TEMPLATE.threshold_key)
        self.assertTrue(QUICK_PACK_TEMPLATE.green_mask)
        self.assertEqual((0.25, 0.85, 0.65, 1.0), QUICK_PACK_TEMPLATE.relative_roi)
        self.assertIn(0.975, QUICK_PACK_TEMPLATE.scale_ratios)
        self.assertNotIn(0.80, QUICK_PACK_TEMPLATE.scale_ratios)
        self.assertEqual(0.85, QUICK_PACK_TEMPLATE.min_pixel_score)
        self.assertEqual(0.88, QUICK_PACK_TEMPLATE.minimum_safe_threshold)
        self.assertEqual(0.85, QUICK_PACK_TEMPLATE.min_zncc_score)
        self.assertIsNotNone(QUICK_PACK_TEMPLATE.candidate_center_roi)

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
            pixel_score=0.90,
            zncc_score=0.90,
            position=(815, 962),
            size=(74, 59),
        )
        clicks = []
        task._click_client = lambda x, y, width, height, after_sleep=0.0: clicks.append(
            (x, y, width, height, after_sleep)
        )
        sleeps = []
        task.sleep = sleeps.append

        self.assertTrue(
            PVPTask._click_template_until(
                task,
                QUICK_PACK_TEMPLATE,
                timeout=0.0,
                name="快速切换按钮",
                stabilize=True,
            )
        )
        self.assertEqual([(852, 991, 1920, 1080, 0.0)], clicks)
        self.assertEqual(10, len(sleeps))
        self.assertTrue(all(seconds == 0.1 for seconds in sleeps))

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

    def test_quick_pack_requires_pixel_similarity_and_zncc(self):
        task = object.__new__(PVPTask)
        task.config = {"快速切换按钮阈值": 0.78}

        low_pixel = SimpleNamespace(score=0.95, pixel_score=0.60, zncc_score=0.95)
        low_zncc = SimpleNamespace(score=0.95, pixel_score=0.95, zncc_score=0.60)
        valid = SimpleNamespace(score=0.90, pixel_score=0.90, zncc_score=0.90)
        unsafe_template_score = SimpleNamespace(
            score=0.83,
            pixel_score=0.95,
            zncc_score=0.95,
        )

        self.assertFalse(PVPTask._passes(task, low_pixel, QUICK_PACK_TEMPLATE))
        self.assertFalse(PVPTask._passes(task, low_zncc, QUICK_PACK_TEMPLATE))
        self.assertTrue(PVPTask._passes(task, valid, QUICK_PACK_TEMPLATE))
        self.assertFalse(PVPTask._passes(task, unsafe_template_score, QUICK_PACK_TEMPLATE))

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

    def test_multiplier_roi_covers_current_auto_battle_value(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[338:366, 1248:1328] = 255
        task.capture_frame = lambda: frame
        task.ocr = lambda frame, **_kwargs: (
            [SimpleNamespace(name="1倍", confidence=1.0)]
            if np.any(frame == 255)
            else []
        )

        self.assertTrue(PVPTask._multiplier_matches(task, 1, timeout=0.1))

    def test_common_cartridge_entry_uses_relative_recent_entry_point(self):
        task = object.__new__(PVPTask)
        calls = []
        task.operate_click = lambda x, y, after_sleep=0: calls.append((x, y, after_sleep))
        settle = []
        task._sleep_after_recognition = lambda: settle.append("settle")
        status = []
        task.info_set = lambda key, value: status.append((key, value))
        stages = []
        task._recent_cartridge_is_pvp = (
            lambda: stages.append("pvp_template") or True
        )
        task._handle_recent_cartridge_special_pages = (
            lambda: stages.append("dialog") or False
        )

        self.assertTrue(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: stages.append("home") or True,
                click_quick_switch=lambda: stages.append("click") or True,
                confirm_quick_switch_page=lambda: stages.append("confirm") or True,
            )
        )
        self.assertEqual([(0.7875, 0.9111111111111111, 0.0)], calls)
        self.assertEqual(["settle"], settle)
        self.assertEqual(
            ["home", "pvp_template", "dialog", "click", "confirm"],
            stages,
        )
        self.assertEqual(3.0, RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS)
        self.assertEqual(
            [
                ("当前阶段", "点击最近卡带"),
                ("当前阶段", "寻找快速切换按钮"),
            ],
            status,
        )

    def test_all_recent_cartridge_tasks_run_shared_pvp_guard(self):
        for task_class in (
            PVPTask,
            SquareGoddessTask,
            MapTradeTask,
            MapCollectionTask,
            BD2MapCollectionProbeTask,
        ):
            with self.subTest(task=task_class.__name__):
                task = object.__new__(task_class)
                calls = []
                task._sleep_after_recognition = lambda: calls.append("settle")
                task.info_set = lambda *_args, **_kwargs: None
                task.operate_click = lambda *_args, **_kwargs: calls.append("entry")
                task._recent_cartridge_is_pvp = (
                    lambda: calls.append("pvp_template") or True
                )
                task._handle_recent_cartridge_special_pages = (
                    lambda: calls.append("pvp_special_page") or True
                )

                self.assertTrue(
                    task.open_cartridge_quick_switcher(
                        ensure_home=lambda: calls.append("home") or True,
                        click_quick_switch=lambda: calls.append("quick") or True,
                        confirm_quick_switch_page=lambda: (
                            calls.append("confirm") or True
                        ),
                    )
                )
                self.assertEqual(
                    [
                        "home",
                        "pvp_template",
                        "settle",
                        "entry",
                        "pvp_special_page",
                        "quick",
                        "confirm",
                    ],
                    calls,
                )

    def test_all_recent_cartridge_tasks_skip_special_pages_for_non_pvp(self):
        for task_class in (
            PVPTask,
            SquareGoddessTask,
            MapTradeTask,
            MapCollectionTask,
            BD2MapCollectionProbeTask,
        ):
            with self.subTest(task=task_class.__name__):
                task = object.__new__(task_class)
                calls = []
                task._sleep_after_recognition = lambda: calls.append("settle")
                task.info_set = lambda *_args, **_kwargs: None
                task.operate_click = lambda *_args, **_kwargs: calls.append("entry")
                task._recent_cartridge_is_pvp = (
                    lambda: calls.append("pvp_template") or False
                )
                task._handle_recent_cartridge_special_pages = lambda: self.fail(
                    "non-PVP recent cartridge must never run special-page OCR"
                )

                self.assertFalse(
                    task.open_cartridge_quick_switcher(
                        ensure_home=lambda: calls.append("home") or True,
                        click_quick_switch=lambda: calls.append("quick") or False,
                        confirm_quick_switch_page=lambda: self.fail(
                            "timed-out non-PVP entry must not confirm a page"
                        ),
                    )
                )
                self.assertEqual(
                    ["home", "pvp_template", "settle", "entry", "quick"],
                    calls,
                )

    def test_common_cartridge_entry_fails_closed_when_pvp_template_errors(self):
        task = object.__new__(SquareGoddessTask)
        task._recent_cartridge_is_pvp = lambda: (_ for _ in ()).throw(
            RuntimeError("missing recent PVP template")
        )
        task._sleep_after_recognition = lambda: self.fail(
            "template failure must stop before the entry settle delay"
        )
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "template failure must stop before clicking the recent cartridge"
        )
        task.info_set = lambda *_args, **_kwargs: None
        warnings = []
        task.log_warning = lambda message, notify=False: warnings.append(
            (message, notify)
        )

        self.assertFalse(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: True,
                click_quick_switch=lambda: self.fail(
                    "template failure must stop before quick-switch search"
                ),
                confirm_quick_switch_page=lambda: self.fail(
                    "template failure must stop before page confirmation"
                ),
            )
        )
        self.assertEqual([("missing recent PVP template", True)], warnings)

    def test_common_cartridge_entry_stops_when_home_is_not_confirmed(self):
        task = object.__new__(PVPTask)
        task.operate_click = lambda *_args, **_kwargs: self.fail("entry must not be clicked")
        task._sleep_after_recognition = lambda: self.fail(
            "settle delay must not run before home is confirmed"
        )
        task._handle_recent_cartridge_special_pages = lambda: self.fail(
            "dialog must not be checked before home is confirmed"
        )
        task._recent_cartridge_is_pvp = lambda: self.fail(
            "recent cartridge must not be classified before home is confirmed"
        )

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
        task._sleep_after_recognition = lambda: None
        task._recent_cartridge_is_pvp = lambda: True
        task._handle_recent_cartridge_special_pages = lambda: False
        status = []
        task.info_set = lambda key, value: status.append((key, value))

        self.assertFalse(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: True,
                click_quick_switch=lambda: False,
                confirm_quick_switch_page=lambda: self.fail("page must not be confirmed"),
            )
        )
        self.assertEqual(
            [
                ("当前阶段", "点击最近卡带"),
                ("当前阶段", "寻找快速切换按钮"),
            ],
            status,
        )

    def test_common_cartridge_entry_rescans_special_pages_after_quick_timeout(self):
        task = object.__new__(PVPTask)
        task.operate_click = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        task.info_set = lambda *_args, **_kwargs: None
        calls = []
        task._recent_cartridge_is_pvp = lambda: True
        task._handle_recent_cartridge_special_pages = (
            lambda: calls.append("dialog") or False
        )

        self.assertFalse(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: True,
                click_quick_switch=lambda: calls.append("quick") or False,
                confirm_quick_switch_page=lambda: self.fail(
                    "page must not be confirmed"
                ),
            )
        )
        self.assertEqual(["dialog", "quick", "dialog"], calls)

    def test_common_cartridge_entry_retries_quick_switch_after_late_special_page(self):
        task = object.__new__(PVPTask)
        task.operate_click = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        task.info_set = lambda *_args, **_kwargs: None
        special_pages = iter((False, True))
        clicks = iter((False, True))
        calls = []
        task._recent_cartridge_is_pvp = lambda: True
        task._handle_recent_cartridge_special_pages = (
            lambda: calls.append("dialog") or next(special_pages)
        )

        self.assertTrue(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: True,
                click_quick_switch=lambda: calls.append("quick") or next(clicks),
                confirm_quick_switch_page=lambda: calls.append("confirm") or True,
            )
        )
        self.assertEqual(
            ["dialog", "quick", "dialog", "quick", "confirm"],
            calls,
        )

    def test_common_cartridge_entry_skips_pvp_pages_for_non_pvp_recent_cartridge(self):
        task = object.__new__(PVPTask)
        calls = []
        task.operate_click = lambda *_args, **_kwargs: calls.append("entry")
        task._sleep_after_recognition = lambda: calls.append("settle")
        task.info_set = lambda *_args, **_kwargs: None
        task._recent_cartridge_is_pvp = lambda: calls.append("template") or False
        task._handle_recent_cartridge_special_pages = lambda: self.fail(
            "non-PVP recent cartridge must skip PVP special-page OCR"
        )

        self.assertTrue(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: calls.append("home") or True,
                click_quick_switch=lambda: calls.append("quick") or True,
                confirm_quick_switch_page=lambda: calls.append("confirm") or True,
            )
        )
        self.assertEqual(
            ["home", "template", "settle", "entry", "quick", "confirm"],
            calls,
        )

    def test_non_pvp_recent_cartridge_does_not_rescan_special_pages_after_timeout(self):
        task = object.__new__(PVPTask)
        task.operate_click = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        task.info_set = lambda *_args, **_kwargs: None
        task._recent_cartridge_is_pvp = lambda: False
        task._handle_recent_cartridge_special_pages = lambda: self.fail(
            "non-PVP recent cartridge must never scan PVP special pages"
        )

        self.assertFalse(
            task.open_cartridge_quick_switcher(
                ensure_home=lambda: True,
                click_quick_switch=lambda: False,
                confirm_quick_switch_page=lambda: self.fail(
                    "page must not be confirmed after quick-switch timeout"
                ),
            )
        )

    def test_recent_pvp_template_asset_and_thresholds(self):
        template_path = RECENT_CARTRIDGE_TEMPLATE_DIR / RECENT_PVP_CARTRIDGE_TEMPLATE_FILE
        raw = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)

        self.assertIsNotNone(raw)
        self.assertEqual((82, 94, 4), raw.shape)
        self.assertGreater(np.count_nonzero(raw[:, :, 3]), 0)
        self.assertLess(np.count_nonzero(raw[:, :, 3]), raw.shape[0] * raw.shape[1])
        self.assertEqual(0.95, RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD)
        self.assertEqual(0.95, RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD)
        self.assertEqual(0.85, RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD)

    def test_recent_pvp_template_matches_at_reference_and_scaled_resolutions(self):
        for frame_width, frame_height, expected_scale in (
            (1920, 1080, 1.0),
            (1280, 720, 2 / 3),
        ):
            with self.subTest(resolution=(frame_width, frame_height)):
                task = object.__new__(PVPTask)
                task._recent_pvp_cartridge_template_cache = None
                template, mask = task._load_recent_pvp_cartridge_template()
                interpolation = (
                    cv2.INTER_AREA if expected_scale < 1.0 else cv2.INTER_CUBIC
                )
                scaled_template = cv2.resize(
                    template,
                    None,
                    fx=expected_scale,
                    fy=expected_scale,
                    interpolation=interpolation,
                )
                scaled_mask = cv2.resize(
                    mask,
                    (scaled_template.shape[1], scaled_template.shape[0]),
                    interpolation=cv2.INTER_NEAREST,
                )
                frame = np.zeros((frame_height, frame_width), dtype=np.uint8)
                x = round(frame_width * 0.72)
                y = round(frame_height * 0.72)
                height, width = scaled_template.shape
                region = frame[y : y + height, x : x + width]
                region[scaled_mask > 0] = scaled_template[scaled_mask > 0]
                region[scaled_mask == 0] = 127

                result = task._match_recent_pvp_cartridge(frame)

                self.assertTrue(result.passed)
                self.assertEqual((x, y), result.position)
                self.assertEqual((width, height), result.size)
                self.assertGreaterEqual(
                    result.score,
                    RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD,
                )
                self.assertGreaterEqual(
                    result.pixel_score,
                    RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
                )
                self.assertGreaterEqual(
                    result.zncc_score,
                    RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
                )

    def test_recent_pvp_template_rejects_nonmatching_frame(self):
        task = object.__new__(PVPTask)
        task._recent_pvp_cartridge_template_cache = None
        frame = np.zeros((720, 1280), dtype=np.uint8)

        result = task._match_recent_pvp_cartridge(frame)

        self.assertFalse(result.passed)

    def test_recent_pvp_template_matches_live_home_fixture(self):
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "pvp"
            / "recent_pvp_home_fhd.png"
        )
        frame = cv2.imread(str(fixture), cv2.IMREAD_COLOR)
        self.assertIsNotNone(frame)
        self.assertEqual((1080, 1920, 3), frame.shape)

        task = object.__new__(SquareGoddessTask)
        task._recent_pvp_cartridge_template_cache = None
        result = task._match_recent_pvp_cartridge(frame)

        self.assertTrue(result.passed)
        self.assertEqual((1659, 935), result.position)
        self.assertEqual((94, 82), result.size)
        self.assertGreaterEqual(result.score, RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD)
        self.assertGreaterEqual(
            result.pixel_score,
            RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
        )
        self.assertGreaterEqual(
            result.zncc_score,
            RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
        )

    def test_recent_pvp_special_pages_click_detected_action_box_center(self):
        cases = (
            ("恭喜晋级。", "确认", (1250, 1324, 60, 32), (2560, 1440)),
            ("段位下滑。", "确认", (938, 993, 42, 24), (1920, 1080)),
            ("赛季奖励", "点击画面即可返回。", (1187, 822, 168, 30), (1920, 1080)),
        )
        for state_text, action_text, action_rect, frame_size in cases:
            with self.subTest(state=state_text):
                task = object.__new__(PVPTask)
                task._executor = SimpleNamespace(
                    method=SimpleNamespace(width=frame_size[0], height=frame_size[1])
                )
                task.info_set = lambda *_args, **_kwargs: None
                task.sleep = lambda *_args, **_kwargs: None
                task._is_beijing_monday = lambda: True
                task._recent_cartridge_ocr_boxes = lambda: [
                    SimpleNamespace(name=state_text, x=800, y=200, width=200, height=50),
                    SimpleNamespace(
                        name=action_text,
                        x=action_rect[0],
                        y=action_rect[1],
                        width=action_rect[2],
                        height=action_rect[3],
                    ),
                ]
                clicks = []
                task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
                    (x, y, after_sleep)
                )

                self.assertTrue(
                    task._handle_recent_cartridge_special_pages(timeout=0.0)
                )
                center_x = action_rect[0] + action_rect[2] / 2
                center_y = action_rect[1] + action_rect[3] / 2
                self.assertEqual(
                    [(center_x / frame_size[0], center_y / frame_size[1], 0.5)],
                    clicks,
                )

    def test_recent_pvp_special_page_ocr_uses_task_threshold(self):
        task = object.__new__(PVPTask)
        task.config = {"PVP OCR 阈值": 0.25}
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        task.ocr = lambda **kwargs: ocr_calls.append(kwargs) or []
        task.info_set = lambda *_args, **_kwargs: None

        self.assertEqual([], task._recent_cartridge_ocr_boxes())
        self.assertEqual(0.25, ocr_calls[0]["threshold"])

    def test_recent_pvp_special_page_ocr_uses_each_callers_threshold(self):
        cases = (
            (PVPTask, "PVP OCR 阈值", 0.21),
            (SquareGoddessTask, "广场 OCR 阈值", 0.22),
            (MapTradeTask, "跑商 OCR 阈值", 0.23),
            (MapCollectionTask, "跑图 OCR 阈值", 0.24),
            (BD2MapCollectionProbeTask, "跑图 OCR 阈值", 0.25),
        )
        for task_class, key, threshold in cases:
            with self.subTest(task=task_class.__name__, key=key):
                task = object.__new__(task_class)
                task.config = {key: threshold}
                task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
                task.info_set = lambda *_args, **_kwargs: None
                ocr_calls = []
                task.ocr = lambda **kwargs: ocr_calls.append(kwargs) or []

                self.assertEqual([], task._recent_cartridge_ocr_boxes())
                self.assertEqual(threshold, ocr_calls[0]["threshold"])
                self.assertEqual(720, ocr_calls[0]["target_height"])

    def test_recent_pvp_special_pages_can_handle_reward_then_rank_page(self):
        task = object.__new__(PVPTask)
        task._executor = SimpleNamespace(
            method=SimpleNamespace(width=1920, height=1080)
        )
        task.info_set = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task._is_beijing_monday = lambda: True
        screens = iter(
            (
                [
                    SimpleNamespace(name="赛季奖励", x=1150, y=220, width=200, height=60),
                    SimpleNamespace(
                        name="点击画面即可返回",
                        x=1180,
                        y=820,
                        width=180,
                        height=30,
                    ),
                ],
                [
                    SimpleNamespace(name="恭喜晋级。", x=850, y=740, width=150, height=40),
                    SimpleNamespace(name="确认", x=930, y=990, width=60, height=30),
                ],
                [],
            )
        )
        task._recent_cartridge_ocr_boxes = lambda: next(screens)
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )

        with patch(
            "src.tasks.BaseBD2Task.monotonic",
            side_effect=(0.0, 0.5, 1.0, 3.1),
        ):
            self.assertTrue(task._handle_recent_cartridge_special_pages(timeout=3.0))

        self.assertEqual(2, len(clicks))

    def test_pvp_special_page_mode_uses_beijing_calendar_day(self):
        monday_in_beijing = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
        tuesday_in_beijing = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)

        self.assertTrue(PVPTask._is_beijing_monday(monday_in_beijing))
        self.assertFalse(PVPTask._is_beijing_monday(tuesday_in_beijing))

    def test_non_monday_ignores_reward_but_still_handles_rank_pages(self):
        reward_boxes = [
            SimpleNamespace(name="赛季奖励", x=900, y=200, width=180, height=50),
            SimpleNamespace(
                name="点击画面即可返回",
                x=1100,
                y=820,
                width=180,
                height=30,
            ),
        ]
        rank_boxes = [
            SimpleNamespace(name="恭喜晋级", x=850, y=700, width=180, height=50),
            SimpleNamespace(name="确认", x=930, y=990, width=60, height=30),
        ]

        _text, reward_action, reward_target = PVPTask._pvp_special_page_action(
            reward_boxes,
            allow_season_reward=False,
        )
        _text, rank_action, rank_target = PVPTask._pvp_special_page_action(
            rank_boxes,
            allow_season_reward=False,
        )

        self.assertEqual("", reward_action)
        self.assertIsNone(reward_target)
        self.assertEqual("恭喜晋级", rank_action)
        self.assertIs(rank_boxes[1], rank_target)

    def test_pvp_special_pages_require_same_frame_text_pairs(self):
        incomplete_frames = (
            ("确认",),
            ("恭喜晋级",),
            ("段位下滑",),
            ("点击画面即可返回",),
            ("赛季奖励",),
            ("赛季奖励", "确认"),
        )
        for texts in incomplete_frames:
            with self.subTest(texts=texts):
                boxes = [
                    SimpleNamespace(name=text, x=0, y=0, width=10, height=10)
                    for text in texts
                ]
                _text, action, target = PVPTask._pvp_special_page_action(
                    boxes,
                    allow_season_reward=True,
                )
                self.assertEqual("", action)
                self.assertIsNone(target)

    def test_quick_switch_page_requires_all_three_ocr_labels(self):
        task = object.__new__(PVPTask)
        task.config = {"卡带选择页确认等待秒数": 1.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        texts = [
            "最近 剧情游戏卡",
            "最近 剧情游戏卡 战斗玩法游戏卡带",
        ]
        task._ocr_text = lambda *_args, **_kwargs: texts.pop(0)
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)

        self.assertTrue(PVPTask._wait_for_quick_switch_page(task))
        self.assertEqual([0.5], sleeps)
        self.assertEqual(
            ("最近", "剧情游戏卡", "战斗玩法游戏卡带"),
            QUICK_SWITCH_PAGE_PATTERNS,
        )

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

    def test_cartridge_home_requires_button_brightness_and_gacha_ocr(self):
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
        ocr_calls = []
        task._ocr_text = lambda _frame, name, roi=None: (
            ocr_calls.append((name, roi)) or "抽抽乐"
        )
        task.sleep = lambda *_args, **_kwargs: None
        task._sleep_after_recognition = lambda: None
        announcement_clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: announcement_clicks.append(
            (x, y, after_sleep)
        )

        self.assertFalse(PVPTask._wait_for_cartridge_home(task))
        self.assertEqual([(169 / 1920, 615 / 1080, 0.2)], announcement_clicks)

        task._home_brightness_ratio = lambda _frame: 0.80
        task._ocr_text = lambda *_args, **_kwargs: ""
        self.assertFalse(PVPTask._wait_for_cartridge_home(task))

        task._ocr_text = lambda _frame, name, roi=None: (
            ocr_calls.append((name, roi)) or "抽抽乐"
        )
        self.assertTrue(PVPTask._wait_for_cartridge_home(task))
        self.assertIn(("主页抽抽乐", HOME_GACHA_OCR_ROI), ocr_calls)

    def test_return_home_uses_same_three_signal_confirmation(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._is_beijing_monday = lambda: True
        task._match = lambda *_args, **_kwargs: SimpleNamespace(score=0.80)
        task._home_brightness_ratio = lambda _frame: 0.80
        task._ocr_text = lambda *_args, **_kwargs: "主页其他文字"
        task.sleep = lambda *_args, **_kwargs: None

        self.assertFalse(PVPTask._wait_for_home(task, timeout=0.0))

        task._ocr_text = lambda *_args, **_kwargs: "抽抽乐"
        self.assertTrue(PVPTask._wait_for_home(task, timeout=0.0))

    def test_pvp_uses_fixed_first_gameplay_cartridge_slot(self):
        self.assertEqual((152 / 1920, 970 / 1080), PVP_CARTRIDGE_SLOT_POINT)

    def test_battle_gameplay_category_requires_ocr_and_visual_highlight(self):
        task = object.__new__(PVPTask)
        task.config = {"玩法类别高亮确认秒数": 0.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.sleep = lambda *_args, **_kwargs: None
        task._ocr_text = lambda *_args, **_kwargs: BATTLE_GAMEPLAY_CATEGORY_LABEL
        frame = {"value": np.zeros((1080, 1920, 3), dtype=np.uint8)}
        task.capture_frame = lambda: frame["value"]

        self.assertFalse(PVPTask._wait_for_battle_gameplay_category(task))

        left = round(BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[0] * REFERENCE_WIDTH)
        top = round(BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[1] * REFERENCE_HEIGHT)
        right = round(BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[2] * REFERENCE_WIDTH)
        bottom = round(BATTLE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION[3] * REFERENCE_HEIGHT)
        frame["value"][top:bottom, left:right] = 255

        self.assertTrue(PVPTask._wait_for_battle_gameplay_category(task))
        self.assertEqual((826, 840, 199, 75), BATTLE_GAMEPLAY_CATEGORY_OCR_ROI)
        self.assertEqual(0.05, GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO)

    def test_pvp_hub_uses_1920_roi_and_calibrated_template_scale(self):
        self.assertEqual((793, 39, 340, 35), PVP_MEDALS_TEMPLATE.roi)
        self.assertIsNone(PVP_MEDALS_TEMPLATE.reference_scale)
        self.assertEqual(0.88, PVP_MEDALS_TEMPLATE.min_pixel_score)
        self.assertEqual(
            [1.18, 1.2, 1.22, 1.25, 1.3],
            candidate_scales(
                1.25,
                PVP_MEDALS_TEMPLATE.scale_ratios,
            ),
        )

        frame = np.zeros((1078, 1918, 3), dtype=np.uint8)
        left, top, crop = PVPTask._roi_frame(frame, PVP_MEDALS_TEMPLATE.roi)
        self.assertEqual((792, 39), (left, top))
        self.assertEqual((35, 340, 3), crop.shape)

    def test_pvp_assets_use_image_folder(self):
        template_root = Path("recognition-assets/template-assets")
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

    def test_pvp_entry_clicks_battle_gameplay_then_fixed_first_slot(self):
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
        task._wait_for_battle_gameplay_category = lambda: True
        hub_waits = []
        task._wait_for_pvp_hub_after_cart = lambda timeout: hub_waits.append(timeout) or True
        task._clear_pvp_hub_notice_if_present = lambda: None
        task.config = {}

        self.assertTrue(PVPTask._enter_pvp_from_home(task))
        self.assertEqual(
            [0.5, FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS],
            sleeps,
        )
        self.assertEqual(
            [
                (*BATTLE_GAMEPLAY_CATEGORY_POINT, 0.0),
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
        task._wait_for_battle_gameplay_category = lambda: True
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
                (*BATTLE_GAMEPLAY_CATEGORY_POINT, 0.0),
                (*PVP_CARTRIDGE_SLOT_POINT, 0.0),
            ],
            clicks,
        )

    def test_pvp_entry_stops_before_slot_when_battle_category_is_not_confirmed(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.open_cartridge_quick_switcher = lambda **_kwargs: True
        task.sleep = lambda *_args, **_kwargs: None
        task._wait_for_battle_gameplay_category = lambda: False
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0: clicks.append(
            (x, y, after_sleep)
        )
        task._wait_for_pvp_hub_after_cart = lambda *_args, **_kwargs: self.fail(
            "PVP slot must not be clicked before the battle category is confirmed"
        )
        task.config = {}

        self.assertFalse(PVPTask._enter_pvp_from_home(task))
        self.assertEqual([(*BATTLE_GAMEPLAY_CATEGORY_POINT, 0.0)], clicks)

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
            SimpleNamespace(
                name="战斗玩法游戏卡带3/3可进行PVP",
                x=470,
                y=590,
                width=360,
                height=30,
            ),
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
            ("战斗玩法游戏卡带", 0.76),
        ]

        self.assertTrue(
            PVPTask._ocr_requirements_met(
                task,
                entries,
                [
                    (r"游戏卡珍藏[集级]", 0.90),
                    (r"角色游戏卡", 0.70),
                    (r"战斗玩法游戏卡带", 0.70),
                ],
            )
        )
        self.assertTrue(
            PVPTask._ocr_requirements_met(
                task,
                [
                    ("游戏卡珍藏级", 0.91),
                    ("角色游戏卡", 0.80),
                    ("战斗玩法游戏卡带", 0.80),
                ],
                [
                    (r"游戏卡珍藏[集级]", 0.90),
                    (r"角色游戏卡", 0.70),
                    (r"战斗玩法游戏卡带", 0.70),
                ],
            )
        )
        self.assertFalse(
            PVPTask._ocr_requirements_met(
                task,
                [
                    ("游戏卡珍藏集", 0.89),
                    ("角色游戏卡", 0.80),
                    ("战斗玩法游戏卡带", 0.80),
                ],
                [
                    (r"游戏卡珍藏[集级]", 0.90),
                    (r"角色游戏卡", 0.70),
                    (r"战斗玩法游戏卡带", 0.70),
                ],
            )
        )

    def test_run_falls_back_to_one_multiplier_when_ap_shortage(self):
        task = object.__new__(PVPTask)
        task.config = {"启用": True, "竞技场战斗倍数": 10, "最多战斗轮次": 3}
        infos = {}
        task.info_set = lambda key, value: infos.__setitem__(key, value)
        notifications = []
        task.log_info = lambda message, notify=False: notifications.append(
            (message, notify)
        )
        task.log_warning = lambda *_args, **_kwargs: None
        task._ensure_pvp_hub = lambda: True
        task.sleep = lambda *_args, **_kwargs: None
        task._click_template_until = lambda *_args, **_kwargs: True

        def fake_wait_for_ocr_patterns(_patterns, timeout, name, **_kwargs):
            if name == "PVP 自动战斗":
                return True, "自动战斗"
            if name == "PVP 自动战斗菜单":
                return True, "鲜血鸡尾酒"
            return False, ""

        task._wait_for_ocr_patterns = fake_wait_for_ocr_patterns
        task._click_ocr_pattern_center = lambda *_args, **_kwargs: True
        task._ensure_free_ap_enabled = lambda: True
        starts = []
        task._ensure_multiplier = lambda multiplier: starts.append(multiplier) or True
        task._select_max_battle_count = lambda: None
        task._click_screen_reference = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)

        def fake_ocr_text(_frame, name, roi=None):
            if name == "PVP 战斗中":
                return ""
            if name == "PVP AP不足":
                return "鲜血鸡尾酒不足"
            return ""

        task._ocr_text = fake_ocr_text
        task._wait_result_and_leave = lambda *_args, **_kwargs: self.fail(
            "battle should not start"
        )

        self.assertTrue(PVPTask.run(task))
        self.assertEqual([10, 1], starts)
        self.assertEqual("鲜血鸡尾酒不足", infos["PVP AP不足 OCR"])
        self.assertEqual(
            ("镜中之战：免费 AP 已耗尽，流程结束。", True),
            notifications[-1],
        )

    def _make_battle_window_task(self):
        harness = SimpleNamespace(
            infos={},
            warnings=[],
            sleeps=[],
            texts={"PVP 战斗中": "", "PVP AP不足": ""},
        )
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda key, value: harness.infos.__setitem__(key, value)
        task.log_warning = lambda message, notify=False: harness.warnings.append(message)
        task.sleep = harness.sleeps.append
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._ocr_text = lambda _frame, name, roi=None: harness.texts.get(name, "")
        return task, harness

    def test_battle_start_window_prefers_battle_signal_over_ap_shortage(self):
        task, harness = self._make_battle_window_task()
        harness.texts["PVP 战斗中"] = "正在进行"
        harness.texts["PVP AP不足"] = "鲜血鸡尾酒不足"

        self.assertEqual("started", PVPTask._wait_battle_start_or_ap_shortage(task, 4))
        self.assertEqual("正在进行", harness.infos["PVP 战斗中 OCR"])
        self.assertNotIn("PVP AP不足 OCR", harness.infos)
        self.assertEqual([], harness.sleeps)

    def test_battle_start_window_reports_ap_shortage_above_multiplier_one(self):
        task, harness = self._make_battle_window_task()
        harness.texts["PVP AP不足"] = "鲜血鸡尾酒不足"

        self.assertEqual(
            "ap_shortage",
            PVPTask._wait_battle_start_or_ap_shortage(task, 4),
        )
        self.assertEqual("鲜血鸡尾酒不足", harness.infos["PVP AP不足 OCR"])

    def test_battle_start_window_reports_ap_depleted_at_multiplier_one(self):
        task, harness = self._make_battle_window_task()
        harness.texts["PVP AP不足"] = "鲜血鸡尾酒不足"

        self.assertEqual(
            "ap_depleted",
            PVPTask._wait_battle_start_or_ap_shortage(task, 1),
        )
        self.assertEqual("鲜血鸡尾酒不足", harness.infos["PVP AP不足 OCR"])

    def test_battle_start_window_times_out_to_settlement_wait(self):
        task, harness = self._make_battle_window_task()
        task.config = {"PVP 战斗开始等待秒数": 30.0}

        with patch(
            "src.tasks.PVPTask.monotonic",
            side_effect=(0.0, 0.0, 100.0),
        ):
            self.assertEqual(
                "started",
                PVPTask._wait_battle_start_or_ap_shortage(task, 1),
            )

        self.assertEqual([0.5], harness.sleeps)
        self.assertEqual(1, len(harness.warnings))

    def test_battle_start_window_skips_none_frames(self):
        task, harness = self._make_battle_window_task()
        frames = iter((None, np.zeros((1440, 2560, 3), dtype=np.uint8)))
        task.capture_frame = lambda: next(frames, None)
        harness.texts["PVP 战斗中"] = "正在进行"

        self.assertEqual("started", PVPTask._wait_battle_start_or_ap_shortage(task, 1))
        self.assertEqual([0.5], harness.sleeps)

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
        self.assertEqual(
            [(*PVP_RESULT_CLOSE_SCREEN_POINT, PVP_RESULT_CLOSE_AFTER_SECONDS)],
            screen_clicks,
        )
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

    def test_pvp_entry_wait_handles_weekly_reward_then_rank_drop_before_hub(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._is_beijing_monday = lambda: True
        screens = iter(
            (
                [],
                [
                    SimpleNamespace(name="赛季奖励", x=1100, y=230, width=180, height=50),
                    SimpleNamespace(
                        name="点击画面即可返回",
                        x=1180,
                        y=820,
                        width=180,
                        height=30,
                    ),
                ],
                [
                    SimpleNamespace(name="段位下滑", x=820, y=700, width=180, height=50),
                    SimpleNamespace(name="确认", x=930, y=990, width=60, height=30),
                ],
                [],
                [],
                [],
            )
        )
        task._pvp_special_page_ocr_boxes = lambda *_args, **_kwargs: next(screens)
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        matched = []
        task._match = lambda _frame, spec: matched.append(spec) or SimpleNamespace(score=0.9)
        task._passes = lambda _result, spec: spec is PVP_MEDALS_TEMPLATE
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)

        with patch(
            "src.tasks.PVPTask.monotonic",
            side_effect=(0.0, 0.1, 0.6, 4.0, 6.5, 7.0, 8.6),
        ):
            self.assertTrue(PVPTask._wait_for_pvp_hub_after_cart(task, timeout=10.0))

        self.assertEqual(
            [
                (
                    (1180 + 180 / 2) / 1920,
                    (820 + 30 / 2) / 1080,
                    PVP_SEASON_REWARD_AFTER_CLICK_SECONDS,
                ),
                (
                    (930 + 60 / 2) / 1920,
                    (990 + 30 / 2) / 1080,
                    PVP_RANK_PAGE_AFTER_CLICK_SECONDS,
                ),
            ],
            clicks,
        )
        self.assertEqual([PVP_MEDALS_TEMPLATE] * 4, matched)
        self.assertEqual([0.5, 0.5, 0.5], sleeps)
        self.assertEqual(2.0, PVP_HUB_SPECIAL_PAGE_GRACE_SECONDS)

    def test_pvp_entry_wait_does_not_repeat_rank_page_click_while_visible(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._is_beijing_monday = lambda: False
        rank_page = [
            SimpleNamespace(name="段位下滑", x=820, y=700, width=180, height=50),
            SimpleNamespace(name="确认", x=930, y=990, width=60, height=30),
        ]
        screens = iter((rank_page, rank_page, [], []))
        task._pvp_special_page_ocr_boxes = lambda *_args, **_kwargs: next(screens)
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task._match = lambda *_args, **_kwargs: SimpleNamespace(score=0.9)
        task._passes = lambda *_args, **_kwargs: True
        task.sleep = lambda *_args, **_kwargs: None

        with patch(
            "src.tasks.PVPTask.monotonic",
            side_effect=(0.0, 0.1, 0.5, 1.0, 3.1),
        ):
            self.assertTrue(PVPTask._wait_for_pvp_hub_after_cart(task, timeout=5.0))

        self.assertEqual(
            [
                (
                    (930 + 60 / 2) / 1920,
                    (990 + 30 / 2) / 1080,
                    PVP_RANK_PAGE_AFTER_CLICK_SECONDS,
                )
            ],
            clicks,
        )

    def test_pvp_entry_wait_keeps_ocr_active_until_hub_is_detected(self):
        task = object.__new__(PVPTask)
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._is_beijing_monday = lambda: False
        ocr_calls = []
        task._pvp_special_page_ocr_boxes = (
            lambda *_args, **_kwargs: ocr_calls.append(True) or []
        )
        scores = iter((0.1, 0.9, 0.9))
        task._match = lambda *_args, **_kwargs: SimpleNamespace(score=next(scores))
        task._passes = lambda result, _spec: result.score >= 0.78
        sleeps = []
        task.sleep = lambda seconds: sleeps.append(seconds)
        task.operate_click = lambda *_args, **_kwargs: self.fail(
            "special pages must not be clicked without paired OCR labels"
        )

        with patch(
            "src.tasks.PVPTask.monotonic",
            side_effect=(0.0, 0.1, 1.0, 3.1),
        ):
            self.assertTrue(PVPTask._wait_for_pvp_hub_after_cart(task, timeout=4.0))
        self.assertEqual(3, len(ocr_calls))
        self.assertEqual([0.5, 0.5], sleeps)

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

    def test_return_home_from_pvp_hub_clicks_top_right_home_and_checks_home(self):
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
        task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task._wait_loading_if_present = lambda name: loading_calls.append(name)
        task._wait_for_home = lambda timeout: home_calls.append(timeout) or True

        self.assertTrue(PVPTask._return_home_from_pvp_hub(task))
        self.assertEqual([(PVP_MEDALS_TEMPLATE, 10.0, "PVP 箱庭")], wait_calls)
        self.assertEqual([(*CHAPTER_HOME_POINT, 2.0)], clicks)
        self.assertEqual(
            CHAPTER_HOME_POINT,
            (
                PVP_BACK_HOME_REFERENCE_POINT[0] / REFERENCE_WIDTH,
                PVP_BACK_HOME_REFERENCE_POINT[1] / REFERENCE_HEIGHT,
            ),
        )
        self.assertEqual(["PVP 返回主页"], loading_calls)
        self.assertEqual([10.0], home_calls)

    def test_return_home_from_pvp_hub_retries_once_when_hub_remains(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        clicks = []
        loading_calls = []
        home_calls = []
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        task._wait_for_template = lambda *_args, **_kwargs: True
        task._click_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task._wait_loading_if_present = lambda name: loading_calls.append(name)
        task._wait_for_home = lambda timeout: home_calls.append(timeout) or (
            len(home_calls) == 2
        )
        task.capture_frame = lambda: frame
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.9)
        task._passes = lambda _result, _spec: True

        self.assertTrue(PVPTask._return_home_from_pvp_hub(task))
        self.assertEqual(
            [(*PVP_BACK_HOME_REFERENCE_POINT, 2.0)] * 2,
            clicks,
        )
        self.assertEqual(["PVP 返回主页"] * 2, loading_calls)
        self.assertEqual(10.0, home_calls[0])
        self.assertAlmostEqual(10.0, home_calls[1], places=5)

    def test_return_home_without_hub_signal_uses_remaining_time_without_second_click(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        clicks = []
        home_calls = []

        task._wait_for_template = lambda *_args, **_kwargs: True
        task._click_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task._wait_loading_if_present = lambda _name: None
        task._wait_for_home = lambda timeout: home_calls.append(timeout) or (
            len(home_calls) == 2
        )
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.1)
        task._passes = lambda _result, _spec: False

        self.assertTrue(PVPTask._return_home_from_pvp_hub(task))
        self.assertEqual([(*PVP_BACK_HOME_REFERENCE_POINT, 2.0)], clicks)
        self.assertEqual(10.0, home_calls[0])
        self.assertAlmostEqual(10.0, home_calls[1], places=5)

    def test_return_home_reference_uses_validated_sandbox_home_point(self):
        self.assertEqual((1797, 63), PVP_BACK_HOME_REFERENCE_POINT)
        self.assertEqual((1920, 1080), (REFERENCE_WIDTH, REFERENCE_HEIGHT))
        self.assertEqual(
            CHAPTER_HOME_POINT,
            (
                PVP_BACK_HOME_REFERENCE_POINT[0] / REFERENCE_WIDTH,
                PVP_BACK_HOME_REFERENCE_POINT[1] / REFERENCE_HEIGHT,
            ),
        )

        task = object.__new__(PVPTask)
        clicks = {}

        def fake_operate_click(x, y, after_sleep=0):
            clicks["x"] = x
            clicks["y"] = y
            clicks["after_sleep"] = after_sleep

        task.operate_click = fake_operate_click
        task._click_reference(*PVP_BACK_HOME_REFERENCE_POINT, after_sleep=2.0)

        self.assertEqual(
            (*CHAPTER_HOME_POINT, 2.0),
            (clicks["x"], clicks["y"], clicks["after_sleep"]),
        )

    def test_click_leave_button_checks_both_regions_and_clicks_failure_target(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        ocr_calls = []
        clicks = []

        def fake_ocr(_frame, name, roi=None):
            ocr_calls.append((name, roi))
            if name == "pvp_leave_failure":
                return [SimpleNamespace(name="离开", x=300, y=20, width=80, height=40)]
            return []

        task._ocr_boxes = fake_ocr
        task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
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
            [(1036 / 1920, 992 / 1080, 2.0)],
            clicks,
        )

    def test_click_leave_button_clicks_success_target(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._ocr_boxes = lambda _frame, name, roi=None: (
            [SimpleNamespace(name="离开", x=70, y=15, width=100, height=30)]
            if name == "pvp_leave_success"
            else []
        )
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task.sleep = lambda *_args, **_kwargs: None

        self.assertTrue(PVPTask._click_leave_button(task))
        self.assertEqual([((1594 + 120) / 1920, (987 + 30) / 1080, 2.0)], clicks)

    def test_leave_ocr_regions_use_1920_reference_coordinates(self):
        self.assertEqual((696, 952, 535, 87), PVP_FAILURE_LEAVE_REFERENCE_ROI)
        self.assertEqual((1594, 987, 240, 66), PVP_SUCCESS_LEAVE_REFERENCE_ROI)

    def test_leave_ocr_center_adds_scaled_roi_offset_at_720p(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        task._ocr_boxes = lambda _frame, name, roi=None: (
            [SimpleNamespace(name="离开", x=200, y=10, width=60, height=20)]
            if name == "pvp_leave_failure"
            else []
        )
        clicks = []
        task.operate_click = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )
        task.sleep = lambda *_args, **_kwargs: None

        self.assertTrue(PVPTask._click_leave_button(task))
        self.assertEqual([((464 + 230) / 1280, (635 + 20) / 720, 2.0)], clicks)

    def test_ensure_pvp_hub_after_leave_returns_when_hub_seen(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task._wait_for_pvp_hub_or_confirm = lambda **_kwargs: ("hub", "", None)
        task._click_frame_point = lambda *_args, **_kwargs: self.fail(
            "confirm should not be clicked after hub is detected"
        )

        self.assertTrue(PVPTask._ensure_pvp_hub_after_leave(task))

    def test_ensure_pvp_hub_after_leave_clicks_confirm_then_waits_hub(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        clicks = []
        sleeps = []
        waits = []

        task._wait_for_pvp_hub_or_confirm = lambda **_kwargs: (
            "confirm",
            "恭喜晋级 确认",
            (960.0, 1030.0),
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.capture_frame = lambda: frame
        task.sleep = sleeps.append
        task._confirm_button_ocr = lambda _frame: (
            "恭喜晋级 确认",
            (970.0, 1040.0),
        )
        task._click_frame_point = lambda seen_frame, point, after_sleep=0.0: clicks.append(
            (seen_frame, point, after_sleep)
        )

        def fake_wait_for_template(spec, timeout, name, **_kwargs):
            waits.append((spec, timeout, name))
            return True

        task._wait_for_template = fake_wait_for_template

        self.assertTrue(PVPTask._ensure_pvp_hub_after_leave(task))
        self.assertEqual([PVP_RANK_CONFIRM_SETTLE_SECONDS], sleeps)
        self.assertIs(frame, clicks[0][0])
        self.assertEqual(((970.0, 1040.0), 1.0), clicks[0][1:])
        self.assertEqual([(PVP_MEDALS_TEMPLATE, 10.0, "PVP 箱庭")], waits)

    def test_ensure_pvp_hub_after_leave_rechecks_confirm_after_settle_delay(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        states = iter(
            (
                ("confirm", "恭喜晋级 确认", (960.0, 1030.0)),
                ("hub", "", None),
            )
        )
        task._wait_for_pvp_hub_or_confirm = lambda **_kwargs: next(states)
        sleeps = []
        task.sleep = sleeps.append
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        confirm_results = iter(
            (
                ("恭喜晋级", None),
            )
        )
        task._confirm_button_ocr = lambda _frame: next(confirm_results)
        task._click_frame_point = lambda *_args, **_kwargs: self.fail(
            "transient confirm must not be clicked"
        )

        self.assertTrue(PVPTask._ensure_pvp_hub_after_leave(task))
        self.assertEqual([PVP_RANK_CONFIRM_SETTLE_SECONDS], sleeps)

    def test_ensure_pvp_hub_after_leave_timeout_does_not_blind_click(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task._wait_for_pvp_hub_or_confirm = lambda **_kwargs: (
            "timeout",
            "战斗 离开",
            None,
        )
        task.capture_frame = lambda: self.fail("timeout must not capture for a click")
        task._click_frame_point = lambda *_args, **_kwargs: self.fail(
            "timeout must not blind click"
        )
        task._wait_for_template = lambda *_args, **_kwargs: self.fail(
            "timeout must not skip directly to hub waiting"
        )

        self.assertFalse(PVPTask._ensure_pvp_hub_after_leave(task))

    def test_ensure_pvp_hub_after_leave_retries_visible_leave_once(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        waits = []
        states = iter(
            (
                ("leave", "成功页:离开", (1728.0, 1008.0)),
                ("hub", "", None),
            )
        )
        task._wait_for_pvp_hub_or_confirm = lambda **kwargs: (
            waits.append((kwargs["timeout"], kwargs["return_on_leave"])) or next(states)
        )
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.capture_frame = lambda: frame
        task._leave_button_ocr = lambda _frame: (
            "失败页:- | 成功页:离开",
            (1714.0, 1017.0),
        )
        clicks = []
        task._click_frame_point = lambda seen_frame, point, after_sleep=0.0: clicks.append(
            (seen_frame, point, after_sleep)
        )

        with patch(
            "src.tasks.PVPTask.monotonic",
            side_effect=(100.0, 100.0, 102.0),
        ):
            self.assertTrue(PVPTask._ensure_pvp_hub_after_leave(task))
        self.assertEqual([(10.0, True), (8.0, False)], waits)
        self.assertEqual(1, len(clicks))
        self.assertIs(frame, clicks[0][0])
        self.assertEqual(((1714.0, 1017.0), 2.0), clicks[0][1:])

    def test_ensure_pvp_hub_after_leave_never_retries_leave_twice(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        states = iter(
            (
                ("leave", "成功页:离开", (1728.0, 1008.0)),
                ("leave", "成功页:离开", (1728.0, 1008.0)),
            )
        )
        task._wait_for_pvp_hub_or_confirm = lambda **_kwargs: next(states)
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._leave_button_ocr = lambda _frame: (
            "失败页:- | 成功页:离开",
            (1714.0, 1017.0),
        )
        clicks = []
        task._click_frame_point = lambda _frame, point, after_sleep=0.0: clicks.append(
            (point, after_sleep)
        )

        with patch(
            "src.tasks.PVPTask.monotonic",
            side_effect=(100.0, 100.0, 102.0),
        ):
            self.assertFalse(PVPTask._ensure_pvp_hub_after_leave(task))
        self.assertEqual([((1714.0, 1017.0), 2.0)], clicks)

    def test_wait_for_pvp_hub_or_confirm_detects_full_frame_confirm_center(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.1)
        task._passes = lambda _result, _spec: False
        ocr_calls = []

        def fake_ocr(_frame, name, roi=None):
            ocr_calls.append((name, roi))
            return [SimpleNamespace(name="确认", x=900, y=1000, width=120, height=40)]

        task._ocr_boxes = fake_ocr
        task.sleep = lambda *_args, **_kwargs: self.fail("confirm should be immediate")

        self.assertEqual(
            ("confirm", "确认", (960.0, 1020.0)),
            PVPTask._wait_for_pvp_hub_or_confirm(task, timeout=1.0),
        )
        self.assertEqual([("PVP 升降级确认", None)], ocr_calls)

    def test_wait_for_pvp_hub_or_confirm_accepts_determine_wording(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.1)
        task._passes = lambda _result, _spec: False
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="确定", x=850, y=980, width=220, height=60)
        ]
        task.sleep = lambda *_args, **_kwargs: self.fail("determine should be immediate")

        self.assertEqual(
            ("confirm", "确定", (960.0, 1010.0)),
            PVPTask._wait_for_pvp_hub_or_confirm(task, timeout=1.0),
        )

    def test_wait_for_pvp_hub_or_confirm_prefers_hub(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.95)
        task._passes = lambda _result, _spec: True
        task._ocr_boxes = lambda *_args, **_kwargs: self.fail(
            "confirm OCR should not run after hub is detected"
        )
        task.sleep = lambda *_args, **_kwargs: self.fail("hub should be immediate")

        self.assertEqual(
            ("hub", "", None),
            PVPTask._wait_for_pvp_hub_or_confirm(task, timeout=1.0),
        )

    def test_wait_for_pvp_hub_or_confirm_detects_still_visible_leave(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._match = lambda _frame, _spec: SimpleNamespace(score=0.1)
        task._passes = lambda _result, _spec: False

        def fake_ocr(_frame, name, roi=None):
            if name == "pvp_leave_success":
                return [SimpleNamespace(name="离开", x=70, y=15, width=100, height=30)]
            return []

        task._ocr_boxes = fake_ocr
        task.sleep = lambda *_args, **_kwargs: self.fail("leave should be immediate")

        self.assertEqual(
            ("leave", "失败页:- | 成功页:离开", (1714.0, 1017.0)),
            PVPTask._wait_for_pvp_hub_or_confirm(task, timeout=1.0),
        )

    def test_wait_for_pvp_hub_or_confirm_waits_through_leave_after_retry(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        frames = []

        def capture_frame():
            frame = np.full((1080, 1920, 3), len(frames), dtype=np.uint8)
            frames.append(frame)
            return frame

        task.capture_frame = capture_frame
        scores = iter((0.1, 0.1, 0.95))
        task._match = lambda _frame, _spec: SimpleNamespace(score=next(scores))
        task._passes = lambda result, _spec: result.score >= 0.9

        def fake_ocr(frame, name, roi=None):
            frame_index = int(frame[0, 0, 0])
            if frame_index < 2 and name == "pvp_leave_success":
                return [SimpleNamespace(name="离开", x=70, y=15, width=100, height=30)]
            return []

        task._ocr_boxes = fake_ocr
        sleeps = []
        task.sleep = sleeps.append

        with patch(
            "src.tasks.PVPTask.monotonic",
            side_effect=(0.0, 0.1, 1.0, 2.0),
        ):
            result = PVPTask._wait_for_pvp_hub_or_confirm(
                task,
                timeout=5.0,
                return_on_leave=False,
            )

        self.assertEqual(("hub", "失败页:- | 成功页:离开", None), result)
        self.assertEqual([0.5, 0.5], sleeps)

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

        PVPTask.drag_client(task, (10, 20), (30, 40), duration=0.0, after_sleep=0.5)

        self.assertEqual([(True, True, True)], operates)
        self.assertEqual([0.5], sleeps)

    def test_start_auto_battle_clicks_start_without_start_ocr_gate(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        template_clicks = []

        def fake_click_template_until(*args, **kwargs):
            template_clicks.append((args, kwargs))
            return True

        task._click_template_until = fake_click_template_until
        task._ensure_free_ap_enabled = lambda: True
        task._ensure_multiplier = lambda _multiplier: True
        task._select_max_battle_count = lambda: None
        auto_clicks = []
        task._click_ocr_pattern_center = lambda *args, **kwargs: (
            auto_clicks.append((args, kwargs)) or True
        )
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task.sleep = lambda *_args, **_kwargs: None

        def fake_ocr_text(_frame, name, roi=None):
            if name == "PVP 战斗中":
                return "正在进行"
            return ""

        task._ocr_text = fake_ocr_text
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
        self.assertEqual(
            [
                (
                    ([r"自动战斗", r"自动"],),
                    {
                        "name": "PVP 自动战斗",
                        "roi": PVP_AUTO_BATTLE_SCREEN_ROI,
                        "after_sleep": 1.0,
                    },
                )
            ],
            auto_clicks,
        )
        self.assertNotIn((*PVP_AUTO_BATTLE_CLICK_REFERENCE, 1.0), clicks)
        self.assertIn((1381, 1061, 2.0), clicks)

    def test_start_auto_battle_falls_back_to_relative_point_when_ocr_box_is_unavailable(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task._click_template_until = lambda *_args, **_kwargs: True
        task._ensure_free_ap_enabled = lambda: True
        task._ensure_multiplier = lambda _multiplier: True
        task._select_max_battle_count = lambda: None
        task._click_ocr_pattern_center = lambda *_args, **_kwargs: False
        task.capture_frame = lambda: np.zeros((1440, 2560, 3), dtype=np.uint8)
        task.sleep = lambda *_args, **_kwargs: None

        def fake_ocr_text(_frame, name, roi=None):
            if name == "PVP 战斗中":
                return "正在进行"
            return ""

        task._ocr_text = fake_ocr_text
        clicks = []
        task._click_screen_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )

        def fake_wait_for_ocr_patterns(_patterns, timeout, name, **_kwargs):
            if name == "PVP 自动战斗":
                return True, "自动战斗"
            if name == "PVP 自动战斗菜单":
                return True, "鲜血鸡尾酒"
            return False, ""

        task._wait_for_ocr_patterns = fake_wait_for_ocr_patterns

        self.assertEqual("started", PVPTask._start_auto_battle(task, 1))
        self.assertIn((*PVP_AUTO_BATTLE_CLICK_REFERENCE, 1.0), clicks)
        self.assertIn((1381, 1061, 2.0), clicks)

    def test_start_auto_battle_fails_when_multiplier_not_confirmed(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        task.log_warning = lambda *_args, **_kwargs: None
        task._click_template_until = lambda *_args, **_kwargs: True

        def fake_wait_for_ocr_patterns(_patterns, timeout, name, **_kwargs):
            if name == "PVP 自动战斗":
                return True, "自动战斗"
            if name == "PVP 自动战斗菜单":
                return True, "鲜血鸡尾酒"
            return False, ""

        task._wait_for_ocr_patterns = fake_wait_for_ocr_patterns
        task._click_ocr_pattern_center = lambda *_args, **_kwargs: True
        task._ensure_free_ap_enabled = lambda: True
        multiplier_calls = []
        task._ensure_multiplier = (
            lambda multiplier: multiplier_calls.append(multiplier) or False
        )
        task._select_max_battle_count = lambda: self.fail(
            "battle count must not be selected when multiplier is unconfirmed"
        )
        task.capture_frame = lambda: self.fail("start window must not run on failure")
        clicks = []
        task._click_screen_reference = lambda x, y, after_sleep=0.0: clicks.append(
            (x, y, after_sleep)
        )

        self.assertEqual("failed", PVPTask._start_auto_battle(task, 10))
        self.assertEqual([10], multiplier_calls)
        self.assertEqual([], clicks)

    def _make_free_ap_task(self, frame):
        harness = SimpleNamespace(infos={}, logs=[])
        task = object.__new__(PVPTask)
        task.capture_frame = lambda: frame
        task.info_set = lambda key, value: harness.infos.__setitem__(key, value)
        task.log_info = lambda message, notify=False: harness.logs.append(message)
        return task, harness

    def test_free_ap_switch_on_accepts_three_channel_yellow_frame(self):
        task, harness = self._make_free_ap_task(
            np.full((1440, 2560, 3), (60, 140, 200), dtype=np.uint8)
        )

        self.assertTrue(PVPTask._free_ap_switch_on(task))
        self.assertEqual("开关黄色占比 1.000", harness.infos["PVP 免费AP"])

    def test_free_ap_switch_on_ignores_alpha_in_four_channel_frame(self):
        task, _harness = self._make_free_ap_task(
            np.full((1440, 2560, 4), (60, 140, 200, 255), dtype=np.uint8)
        )

        self.assertTrue(PVPTask._free_ap_switch_on(task))

    def test_free_ap_switch_on_rejects_grayscale_frame(self):
        task, harness = self._make_free_ap_task(
            np.zeros((1440, 2560), dtype=np.uint8)
        )

        self.assertFalse(PVPTask._free_ap_switch_on(task))
        self.assertEqual(1, len(harness.logs))

    def test_free_ap_switch_on_rejects_empty_crop(self):
        task, harness = self._make_free_ap_task(
            np.zeros((10, 10, 3), dtype=np.uint8)
        )

        self.assertFalse(PVPTask._free_ap_switch_on(task))
        self.assertEqual([], harness.logs)

    def test_click_ocr_pattern_center_uses_reference_roi_and_box_center(self):
        task = object.__new__(PVPTask)
        task.config = {}
        task.info_set = lambda *_args, **_kwargs: None
        task.capture_frame = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._ocr_boxes = lambda *_args, **_kwargs: [
            SimpleNamespace(name="自动战斗", x=10, y=20, width=30, height=40)
        ]
        clicks = []
        task._click_client = lambda *args, **kwargs: clicks.append((args, kwargs))

        self.assertTrue(
            PVPTask._click_ocr_pattern_center(
                task,
                [r"自动战斗"],
                name="PVP 自动战斗",
                roi=PVP_AUTO_BATTLE_SCREEN_ROI,
                after_sleep=1.0,
            )
        )
        self.assertEqual(
            [((1495, 950, 1920, 1080), {"after_sleep": 1.0})],
            clicks,
        )


if __name__ == "__main__":
    unittest.main()
