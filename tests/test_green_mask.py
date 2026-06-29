import unittest

import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task, green_mask_from_template


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

    def test_tolerance_can_ignore_near_green_pixels(self):
        template = np.array(
            [
                [[0, 255, 0], [3, 252, 4]],
                [[8, 247, 8], [9, 247, 8]],
            ],
            dtype=np.uint8,
        )

        mask = green_mask_from_template(template, tolerance=8)

        np.testing.assert_array_equal(
            mask,
            np.array(
                [
                    [0, 0],
                    [0, 255],
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

    def test_grayscale_template_has_no_green_mask(self):
        template = np.zeros((2, 3), dtype=np.uint8)

        mask = green_mask_from_template(template)

        np.testing.assert_array_equal(mask, np.full((2, 3), 255, dtype=np.uint8))

    def test_find_one_green_mask_passes_mask_function(self):
        task = object.__new__(BaseBD2Task)
        calls = {}

        def fake_find_one(*args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs
            return "found"

        task.find_one = fake_find_one

        result = task.find_one_green_mask("feature", threshold=0.8)

        self.assertEqual("found", result)
        self.assertEqual(("feature",), calls["args"])
        self.assertEqual(0.8, calls["kwargs"]["threshold"])
        self.assertIn("mask_function", calls["kwargs"])

        template = np.array([[[0, 255, 0], [255, 255, 255]]], dtype=np.uint8)
        np.testing.assert_array_equal(
            calls["kwargs"]["mask_function"](template),
            np.array([[0, 255]], dtype=np.uint8),
        )


if __name__ == "__main__":
    unittest.main()
