import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.compat.windows_graphics import (
    ResizeStabilityGate,
    _capture_can_produce_visible_frame,
    _capture_identity_signature,
    _valid_capture_size,
    patch_windows_graphics_capture_class,
)


class WindowsGraphicsCompatibilityTest(unittest.TestCase):
    def test_first_frame_probe_waits_past_black_frame_for_visible_content(self):
        blank_frame = np.zeros((720, 1280, 4), dtype=np.uint8)
        blank_frame[:, :, 3] = 255
        capture = SimpleNamespace(
            get_frame=Mock(
                side_effect=(
                    blank_frame,
                    np.ones((720, 1280, 3), dtype=np.uint8),
                )
            ),
            get_name=Mock(return_value="Windows Graphics Capture"),
        )
        fake_time = SimpleNamespace(
            monotonic=Mock(side_effect=(0.0, 0.0, 0.1)),
            sleep=Mock(),
        )

        with patch("src.compat.windows_graphics.time", fake_time):
            self.assertTrue(_capture_can_produce_visible_frame(capture, 1.5))

        self.assertEqual(2, capture.get_frame.call_count)

    def test_first_frame_probe_rejects_continuous_black_frames(self):
        capture = SimpleNamespace(
            get_frame=Mock(return_value=np.zeros((720, 1280, 3), dtype=np.uint8)),
            get_name=Mock(return_value="Windows Graphics Capture"),
        )
        fake_time = SimpleNamespace(
            monotonic=Mock(side_effect=(0.0, 0.0, 1.6)),
            sleep=Mock(),
        )

        with patch("src.compat.windows_graphics.time", fake_time):
            self.assertFalse(_capture_can_produce_visible_frame(capture, 1.5))

        capture.get_frame.assert_called_once_with()

    def test_resize_gate_requires_one_continuously_stable_size(self):
        gate = ResizeStabilityGate(delay_seconds=0.8)

        self.assertFalse(gate.observe((1300, 731), 0.0))
        self.assertFalse(gate.observe((1500, 844), 0.4))
        self.assertFalse(gate.observe((1500, 844), 1.1))
        self.assertTrue(gate.observe((1500, 844), 1.21))

    def test_capture_identity_ignores_geometry_but_keeps_window_handles(self):
        window = SimpleNamespace(
            hwnd=10,
            top_hwnd=20,
            width=1280,
            height=720,
            client_width=1280,
            client_height=720,
            hwnds=[(10,), (20,)],
        )
        first = _capture_identity_signature(window)

        window.width = 1920
        window.height = 1080
        window.client_width = 1920
        window.client_height = 1080
        self.assertEqual(first, _capture_identity_signature(window))

        window.hwnd = 30
        self.assertNotEqual(first, _capture_identity_signature(window))

    def test_small_startup_window_is_allowed_to_probe_wgc(self):
        original_start = Mock(return_value=True)

        class FakeCapture:
            def __init__(self):
                self.hwnd_window = SimpleNamespace(
                    exists=True,
                    hwnd=10,
                    top_hwnd=10,
                    hwnds=[(10,)],
                    width=304,
                    height=201,
                )

            start_or_stop = original_start

            def close(self):
                return None

            def frame_arrived_callback(self, *_args):
                return None

        patch_windows_graphics_capture_class(FakeCapture)
        capture = FakeCapture()

        self.assertTrue(capture.start_or_stop())
        original_start.assert_called_once_with(capture, capture_cursor=False)

    def test_established_capture_closes_when_same_window_shrinks_below_minimum(self):
        original_start = Mock(return_value=True)

        class FakeCapture:
            def __init__(self):
                self.lock = threading.RLock()
                self.frame_pool = object()
                self.close_calls = 0
                self.hwnd_window = SimpleNamespace(
                    exists=True,
                    hwnd=10,
                    top_hwnd=10,
                    hwnds=[(10,)],
                    width=1279,
                    height=720,
                )

            start_or_stop = original_start

            def close(self):
                self.close_calls += 1
                self.frame_pool = None

            def frame_arrived_callback(self, *_args):
                return None

        patch_windows_graphics_capture_class(FakeCapture)
        capture = FakeCapture()
        capture._ok_bd2_supported_target_signature = _capture_identity_signature(
            capture.hwnd_window
        )

        self.assertFalse(capture.start_or_stop())
        self.assertEqual(1, capture.close_calls)
        self.assertIsNone(capture.frame_pool)
        original_start.assert_not_called()

        capture.hwnd_window.hwnd = 20
        capture.hwnd_window.top_hwnd = 20
        capture.hwnd_window.hwnds = [(20,)]
        self.assertTrue(capture.start_or_stop())
        original_start.assert_called_once_with(capture, capture_cursor=False)

    def test_rapid_resize_recreates_frame_pool_once_after_frame_close(self):
        lifecycle = []

        class FakeSize:
            def __init__(self, width, height):
                self.Width = width
                self.Height = height

        class FakeFrame:
            def __init__(self, width, height):
                self.ContentSize = FakeSize(width, height)

            def Close(self):
                lifecycle.append("close")

        class FakePool:
            def __init__(self):
                self.frames = []

            def TryGetNextFrame(self):
                return self.frames.pop(0)

        class FakeCapture:
            def __init__(self):
                self.lock = threading.RLock()
                self.exit_event = threading.Event()
                self.frame_requested = threading.Event()
                self.frame_event = threading.Event()
                self.frame_pool = FakePool()
                self.last_size = FakeSize(1280, 720)
                self.last_frame_time = 0.0
                self.last_frame = None
                self.hwnd_window = SimpleNamespace(
                    exists=True,
                    width=1280,
                    height=720,
                )
                self.reset_sizes = []

            def start_or_stop(self, capture_cursor=False):
                return True

            def close(self):
                return None

            def frame_arrived_callback(self, *_args):
                return None

            def convert_dx_frame(self, _frame):
                lifecycle.append("convert")
                return "frame"

            def reset_framepool(self, size):
                if not lifecycle or lifecycle[-1] != "close":
                    raise AssertionError("frame pool reset before frame close")
                lifecycle.append("reset")
                self.reset_sizes.append((size.Width, size.Height))

        patch_windows_graphics_capture_class(FakeCapture)
        capture = FakeCapture()
        clock = iter((0.0, 0.2, 0.4, 1.21))

        with patch(
            "src.compat.windows_graphics.time.monotonic",
            side_effect=lambda: next(clock),
        ):
            for size in ((1300, 731), (1400, 788), (1500, 844), (1500, 844)):
                capture.frame_pool.frames.append(FakeFrame(*size))
                capture.frame_arrived_callback()

        self.assertEqual([(1500, 844)], capture.reset_sizes)
        self.assertEqual(["close", "close", "close", "close", "reset"], lifecycle)

        capture.frame_requested.set()
        capture.frame_pool.frames.append(FakeFrame(1500, 844))
        capture.frame_arrived_callback()
        self.assertEqual("frame", capture.last_frame)
        self.assertTrue(capture.frame_event.is_set())

    def test_supported_capture_size_boundary(self):
        self.assertFalse(_valid_capture_size((304, 201)))
        self.assertFalse(_valid_capture_size((1279, 720)))
        self.assertFalse(_valid_capture_size((1280, 719)))
        self.assertTrue(_valid_capture_size((1280, 720)))


if __name__ == "__main__":
    unittest.main()
