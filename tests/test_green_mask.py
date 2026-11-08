import unittest

import numpy as np

from src.tasks.BaseBD2Task import green_mask_from_template


class GreenMaskTest(unittest.TestCase):
    def test_green_pixels_are_ignored(self):
        template = np.array(
            [
                [[0, 255, 0], [255, 255, 255]],
                [[1, 255, 0], [0, 254, 0]],
            ],
            dtype=np.uint8,
        )

        mask = green_mask_from_template(template)

        np.testing.assert_array_equal(
            mask,
            np.array(
                [
                    [0, 255],
                    [255, 255],
                ],
                dtype=np.uint8,
            ),
        )

    def test_transparent_pixels_are_ignored(self):
        template = np.array(
            [
                [[10, 20, 30, 0], [0, 255, 0, 255]],
                [[10, 20, 30, 255], [255, 255, 255, 255]],
            ],
            dtype=np.uint8,
        )

        mask = green_mask_from_template(template)

        np.testing.assert_array_equal(
            mask,
            np.array(
                [
                    [0, 0],
                    [255, 255],
                ],
                dtype=np.uint8,
            ),
        )


if __name__ == "__main__":
    unittest.main()
