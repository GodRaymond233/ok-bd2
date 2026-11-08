import unittest

from src.tasks.map_trade import (
    action_icons,
    collector_constants,
    navigator_constants,
)
from src.utils.calibration import (
    FHD_1080,
    reference_rect_to_relative_roi,
)


class ReferenceCalibrationTest(unittest.TestCase):

    def test_reference_rect_to_relative_roi_converts_width_height_to_right_bottom(self):
        self.assertEqual(
            (100 / 1920, 120 / 1080, 400 / 1920, 340 / 1080),
            reference_rect_to_relative_roi((100, 120, 300, 220), FHD_1080),
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


if __name__ == "__main__":
    unittest.main()
