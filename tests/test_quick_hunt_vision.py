import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from src.tasks.quick_hunt_vision import parse_free_pool, parse_stone_rows, read_free_switch


def box(text, x=100, y=100, width=40, height=18):
    return SimpleNamespace(name=text, x=x, y=y, width=width, height=height)


def switch_frame(state="on", *, height=720, channels=3, second=False):
    frame = np.full((720, 1280, 3), 40, dtype=np.uint8)
    color = (43, 133, 171) if state == "on" else (88, 88, 88)
    for offset in (0, 42) if second else (0,):
        cv2.rectangle(frame, (823 + offset, 196), (839 + offset, 212), color, -1)
        for x in (823, 839):
            cv2.circle(frame, (x + offset, 204), 8, color, -1, cv2.LINE_AA)
        knob_x = 839 if state == "on" else 823
        cv2.circle(frame, (knob_x + offset, 204), 7, (206, 219, 219), -1, cv2.LINE_AA)
    scale = height / 720
    frame = cv2.resize(frame, None, fx=scale, fy=scale)
    if channels == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    return frame, box("仅使用免费米饭", 704 * scale, 195 * scale, 105 * scale, 18 * scale)


class QuickHuntVisionTests(unittest.TestCase):
    def test_switch_requires_track_and_endpoint_knob_at_capture_resolutions(self):
        for state in ("on", "off"):
            for height in (720, 1080, 2160):
                for channels in (3, 4):
                    with self.subTest(state=state, height=height, channels=channels):
                        frame, label = switch_frame(state, height=height, channels=channels)
                        actual, center = read_free_switch(frame, label)
                        self.assertEqual(state, actual)
                        self.assertAlmostEqual(
                            831 * height / 720, center[0], delta=2 * height / 720
                        )
                        self.assertAlmostEqual(
                            204 * height / 720, center[1], delta=2 * height / 720
                        )

    def test_switch_ambiguous_or_incomplete_evidence_never_becomes_off(self):
        frame, label = switch_frame()
        no_track = np.full_like(frame, 40)
        cv2.circle(no_track, (839, 204), 7, (206, 219, 219), -1, cv2.LINE_AA)
        occluded = frame.copy()
        occluded[194:215, 821:832] = 40
        no_knob = frame.copy()
        no_knob[194:215, 830:850] = 40
        for name, candidate in (
            ("blank", np.full_like(frame, 40)),
            ("dim", (frame * 0.3).astype(np.uint8)),
            ("no_track", no_track),
            ("occluded", occluded),
            ("no_knob", no_knob),
            ("truncated", frame[:, :842]),
            ("two_candidates", switch_frame(second=True)[0]),
            ("grayscale", cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)),
        ):
            with self.subTest(name=name):
                self.assertEqual(("unknown", None), read_free_switch(candidate, label))

    def test_yellow_and_knob_position_must_agree(self):
        frame, label = switch_frame("off")
        track = (frame[:, :, 0] == 88) & (frame[:, :, 1] == 88)
        frame[track] = (43, 133, 171)
        self.assertEqual(("unknown", None), read_free_switch(frame, label))

    def test_free_pool_preserves_capacity_and_excludes_adjacent_stored_rice(self):
        for text, expected, value in (
            ("90/90 +20.5K", 90, (90, 90)),
            ("免费米饭 0 / 90", 90, (0, 90)),
            ("６０／６０", 60, (60, 60)),
            ("12：90", 90, (12, 90)),
            ("0|60", None, (0, 60)),
            ("6-60", 60, (6, 60)),
            ("6~60", 60, (6, 60)),
        ):
            with self.subTest(text=text):
                self.assertEqual(value, parse_free_pool(text, expected))

    def test_free_pool_rejects_ambiguous_partial_or_out_of_range_readings(self):
        for text, capacity in (
            ("90", 90), ("90/", 90), ("/90", 90), ("90/90 60/60", 90),
            ("90/90/90", 90), ("0/90 0/90", 90), ("1.2K/90", 90),
            ("100/90", 90), ("0/0", None), ("12/9000", None),
            ("12/60", 90), ("90/90.5", 90), ("-1/90", 90),
            ("0/90K", 90), ("1,000/90", 90),
        ):
            with self.subTest(text=text, capacity=capacity):
                self.assertIsNone(parse_free_pool(text, capacity))

    def test_stone_counts_join_adjacent_fragments_and_order_five_rows(self):
        boxes = [
            box("1,", y=100, width=15), box("834", x=117, y=100, width=28),
            box("1.2", y=140, width=25), box("K", x=127, y=141, width=12),
            box("0.5M", y=180), box("2.5万", y=220), box("0", y=260),
        ]
        self.assertEqual((1834, 1200, 500000, 25000, 0), parse_stone_rows(reversed(boxes)))

    def test_stone_counts_reject_malformed_counts_and_inventory_ambiguity(self):
        baseline = [box(str(index + 1), y=100 + index * 40) for index in range(5)]
        for name, boxes in (
            ("missing_row", baseline[:-1]),
            ("extra_row", baseline + [box("6", y=300)]),
            ("duplicate", baseline + [baseline[0]]),
            ("overlap", baseline + [box("00", x=130)]),
            ("separate_column", baseline + [box("00", x=200)]),
            ("uncertain_decimal", [box("1.946")] + baseline[1:]),
            ("bad_grouping", [box("1,23")] + baseline[1:]),
            ("unknown_text", [box("库存100")] + baseline[1:]),
            ("fraction", [box("1/2")] + baseline[1:]),
            ("fractional_item", [box("0.0001K")] + baseline[1:]),
            ("invalid_geometry", [box("1", width=0)] + baseline[1:]),
        ):
            with self.subTest(name=name):
                self.assertIsNone(parse_stone_rows(boxes))


if __name__ == "__main__":
    unittest.main()
