import unittest

import numpy as np

import src.tasks.PVPTask as pvp_task_module
import src.tasks.SquareGoddessTask as square_goddess_task_module
import src.tasks.trigger.AutoLoginTask as auto_login_task_module
from src.tasks import (
    BargainLevelTask,
    BD2InputTestTask,
    DailyTask,
    FreeGachaTask,
    QuickSuppressionTask,
)
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade import (
    action_icons,
    collector_constants,
    navigator_constants,
    vision,
)
from src.tasks.map_trade.models import MAP_TRADE_REFERENCE
from src.tasks.MapTradeTask import MapAutomationTaskBase, MapTradeTask
from src.tasks.PVPTask import PVPTask
from src.tasks.SquareGoddessTask import SquareGoddessTask
from src.utils.calibration import FHD_1080, HD_720, QHD_1440, ReferenceCalibration


class ReferenceCalibrationTest(unittest.TestCase):
    def test_shared_calibration_constants(self):
        self.assertEqual(ReferenceCalibration(1280, 720), HD_720)
        self.assertEqual(ReferenceCalibration(1920, 1080), FHD_1080)
        self.assertEqual(ReferenceCalibration(2560, 1440), QHD_1440)
        self.assertEqual((1920, 1080), FHD_1080.size)

    def test_task_modules_derive_1080p_reference_from_shared_calibration(self):
        for module in (
            DailyTask,
            FreeGachaTask,
            pvp_task_module,
            square_goddess_task_module,
            auto_login_task_module,
            BargainLevelTask,
            BD2InputTestTask,
        ):
            with self.subTest(module=module.__name__):
                self.assertEqual(FHD_1080.width, module.REFERENCE_WIDTH)
                self.assertEqual(FHD_1080.height, module.REFERENCE_HEIGHT)
        self.assertEqual(FHD_1080.width, QuickSuppressionTask.REFERENCE_WIDTH)

    def test_pvp_and_square_derive_additional_references(self):
        for module in (pvp_task_module, square_goddess_task_module):
            with self.subTest(module=module.__name__):
                self.assertEqual(HD_720.width, module.HD720_REFERENCE_WIDTH)
                self.assertEqual(HD_720.height, module.HD720_REFERENCE_HEIGHT)
                self.assertEqual(QHD_1440.width, module.ENTRY_REFERENCE_WIDTH)
                self.assertEqual(QHD_1440.height, module.ENTRY_REFERENCE_HEIGHT)

    def test_map_trade_vision_reference_is_720p_calibration(self):
        self.assertIs(HD_720, MAP_TRADE_REFERENCE)
        self.assertEqual(
            (960, 540),
            vision.Vision.reference_point(640, 360, 1920, 1080),
        )
        self.assertEqual(
            (480, 270),
            vision.Vision.reference_point(640, 360, 960, 540),
        )

    def test_skill_coordinates_share_single_source_of_truth(self):
        self.assertEqual(
            {
                1: (1671, 1011),
                2: (1749, 1011),
                3: (1824, 1011),
            },
            action_icons.SKILL_GROUP_CENTERS_REFERENCE,
        )
        self.assertIs(
            action_icons.SKILL_GROUP_CENTERS_REFERENCE,
            collector_constants.SKILL_GROUP_REFERENCE_POINTS,
        )
        self.assertEqual(
            action_icons.SKILL_GROUP_CENTERS_REFERENCE[1],
            navigator_constants.SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER,
        )
        self.assertEqual(
            action_icons.SKILL_GROUP_CENTERS_REFERENCE[2],
            navigator_constants.SANDBOX_SKILL_SLOT_2_REFERENCE_CENTER,
        )
        self.assertEqual(
            action_icons.ACTION_SLOT_CENTERS_REFERENCE["teleport"],
            navigator_constants.SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER,
        )
        self.assertEqual(
            (1671 / 1920, 1011 / 1080),
            navigator_constants.SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
        )
        self.assertEqual(
            (1795 / 1920, 788 / 1080),
            navigator_constants.SANDBOX_TELEPORT_SKILL_RELATIVE_POINT,
        )
        self.assertEqual(FHD_1080.width, action_icons.SKILL_REFERENCE_WIDTH)
        self.assertEqual(FHD_1080.height, action_icons.SKILL_REFERENCE_HEIGHT)
        self.assertEqual(FHD_1080.size, collector_constants.SKILL_REFERENCE_SIZE)
        self.assertEqual(FHD_1080.size, navigator_constants.AREA_MAP_REFERENCE_SIZE)


class InputUnificationTest(unittest.TestCase):
    def test_drag_and_scroll_live_on_base_task_once(self):
        self.assertIs(BaseBD2Task.drag_client, MapAutomationTaskBase.drag_client)
        self.assertIs(BaseBD2Task.drag_client, MapTradeTask.drag_client)
        self.assertIs(BaseBD2Task.drag_client, PVPTask.drag_client)
        self.assertIs(BaseBD2Task.drag_client, SquareGoddessTask.drag_client)
        self.assertIs(BaseBD2Task.scroll_client, MapAutomationTaskBase.scroll_client)
        self.assertIs(BaseBD2Task.scroll_client, MapTradeTask.scroll_client)
        self.assertIs(BaseBD2Task.scroll_client, PVPTask.scroll_client)
        self.assertIs(BaseBD2Task.scroll_client, SquareGoddessTask.scroll_client)
        self.assertFalse(hasattr(PVPTask, "_drag_client"))
        self.assertFalse(hasattr(SquareGoddessTask, "_drag_client"))
        self.assertFalse(hasattr(MapAutomationTaskBase, "_drag_client"))
        self.assertFalse(hasattr(PVPTask, "_scroll_client"))
        self.assertFalse(hasattr(MapAutomationTaskBase, "_scroll_client"))
        self.assertFalse(hasattr(PVPTask, "_post_drag_client"))

    def test_vision_drag_reference_delegates_to_public_drag_client(self):
        class RecordingTask:
            def __init__(self, frame):
                self.frame = frame
                self.drags = []

            def capture_frame(self):
                return self.frame

            def drag_client(self, start, end, duration=0.7, after_sleep=0.0):
                self.drags.append((start, end, duration, after_sleep))

        task = RecordingTask(np.zeros((1080, 1920, 3), dtype=np.uint8))
        task_vision = vision.Vision(task)
        task_vision.drag_reference(
            (640, 360),
            (320, 180),
            duration=0.4,
            after_sleep=0.2,
        )
        self.assertEqual([((960, 540), (480, 270), 0.4, 0.2)], task.drags)


class PvpOwnershipTest(unittest.TestCase):
    def test_pvp_special_page_logic_lives_in_pvp_task(self):
        self.assertFalse(BaseBD2Task._recent_cartridge_is_pvp(object.__new__(BaseBD2Task)))
        self.assertIsNot(
            BaseBD2Task._recent_cartridge_is_pvp,
            PVPTask._recent_cartridge_is_pvp,
        )
        self.assertTrue(hasattr(PVPTask, "_match_recent_pvp_cartridge"))
        self.assertTrue(hasattr(PVPTask, "_handle_recent_cartridge_special_pages"))
        self.assertTrue(hasattr(PVPTask, "_pvp_special_page_action"))
        self.assertTrue(hasattr(PVPTask, "_is_beijing_monday"))
        self.assertTrue(hasattr(PVPTask, "_pvp_special_page_ocr_boxes"))
        self.assertTrue(hasattr(PVPTask, "_recent_cartridge_ocr_boxes"))
        self.assertFalse(hasattr(BaseBD2Task, "_match_recent_pvp_cartridge"))
        self.assertFalse(hasattr(BaseBD2Task, "_handle_recent_cartridge_special_pages"))
        self.assertFalse(hasattr(BaseBD2Task, "_pvp_special_page_action"))
        self.assertFalse(hasattr(BaseBD2Task, "_is_beijing_monday"))
        self.assertFalse(hasattr(BaseBD2Task, "_pvp_special_page_ocr_boxes"))
        self.assertFalse(hasattr(BaseBD2Task, "_recent_cartridge_ocr_boxes"))
        self.assertEqual(3, pvp_task_module.RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS)
        self.assertEqual(3.0, pvp_task_module.RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS)
        self.assertEqual(
            "cartridge-image2-left-lower-cutout.png",
            pvp_task_module.RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
        )


if __name__ == "__main__":
    unittest.main()
