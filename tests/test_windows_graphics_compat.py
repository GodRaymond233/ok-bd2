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
                self.get_frame_lock = threading.RLock()
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
                self.get_frame_lock = threading.RLock()
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

    def test_close_and_target_changes_do_not_invert_pending_frame_locks(self):
        class RequestLock:
            def __init__(self, capture):
                self.capture = capture
                self.lock = threading.RLock()

            def acquire(self, **kwargs):
                if self.capture.request_started.is_set():
                    self.capture.close_waiting.set()
                return self.lock.acquire(**kwargs)

            def release(self):
                self.lock.release()

            def __enter__(self):
                self.acquire()

            def __exit__(self, *_args):
                self.release()

        class FakeCapture:
            def __init__(self):
                self.lock = threading.RLock()
                self.get_frame_lock = RequestLock(self)
                self.exit_event = threading.Event()
                self._frame_cancel_generation = 0
                self.hwnd_window = SimpleNamespace(exists=False)
                self.frame_event = threading.Event()
                self.frame_requested = threading.Event()
                self.request_started = threading.Event()
                self.close_waiting = threading.Event()
                self.woken_before_close_wait = False
                self.failures = []
                self.lifecycle = []
                self.last_size = (1280, 720)
                frame = SimpleNamespace(
                    ContentSize=(1024, 576),
                    Close=lambda: self.lifecycle.append("frame closed"),
                )
                self.frame_pool = SimpleNamespace(TryGetNextFrame=lambda: frame)

            def start_or_stop(self, capture_cursor=False):
                with self.lock:
                    self.close()
                return False

            def close(self):
                # ok-script 2.0.6 wakes a pending reader before taking its lock.
                with self.lock:
                    self._frame_cancel_generation += 1
                    self.frame_requested.clear()
                    self.frame_event.set()
                if not self.get_frame_lock.acquire(timeout=0.5):
                    self.failures.append("close waited on a reader holding get_frame_lock")
                    return
                try:
                    with self.lock:
                        self.frame_pool = None
                        self.lifecycle.append("capture closed")
                finally:
                    self.get_frame_lock.release()

            def do_get_frame(self):
                with self.get_frame_lock:
                    self.request_started.set()
                    if not self.close_waiting.wait(1.0):
                        self.failures.append("close did not reach the pending frame lock")
                    self.woken_before_close_wait = self.frame_event.is_set()
                    if self.lock.acquire(timeout=1.0):
                        self.lock.release()
                    else:
                        self.failures.append("reader waited on capture resource lock")

        patch_windows_graphics_capture_class(FakeCapture)
        for entry in ("close", "frame_arrived_callback", "start_or_stop"):
            with self.subTest(entry=entry):
                capture = FakeCapture()
                capture._ok_bd2_supported_target_signature = ()
                capture._ok_bd2_resize_gate.observe((1024, 576), 0.0)

                def read_frame():
                    try:
                        capture.do_get_frame()
                    except Exception as exc:
                        capture.failures.append(str(exc))

                reader = threading.Thread(target=read_frame)
                closer = threading.Thread(target=getattr(capture, entry))
                reader.start()
                self.assertTrue(capture.request_started.wait(1.0))
                closer.start()
                for worker in (reader, closer):
                    worker.join(2.0)
                    self.assertFalse(worker.is_alive())
                self.assertEqual([], capture.failures)
                self.assertIsNone(capture.frame_pool)
                self.assertFalse(capture._ok_bd2_resize_gate.active)
                if entry != "start_or_stop":
                    self.assertTrue(capture.woken_before_close_wait)
                if entry == "frame_arrived_callback":
                    self.assertEqual(
                        ["frame closed", "capture closed"], capture.lifecycle
                    )

    def test_frame_pool_close_can_finish_an_inflight_callback_under_resource_lock(self):
        class FakeCapture:
            def __init__(self):
                self.lock = threading.RLock()
                self.exit_event = threading.Event()
                self.callback_finished_during_close = False
                self.callback = None
                self.frame_pool = SimpleNamespace(Close=self.close_pool)

            def start_or_stop(self, capture_cursor=False):
                return True

            def frame_arrived_callback(self, *_args):
                pass

            def close_pool(self):
                self.callback = threading.Thread(target=self.frame_arrived_callback)
                self.callback.start()
                self.callback.join(0.5)
                self.callback_finished_during_close = not self.callback.is_alive()

            def close(self):
                with self.lock:
                    self.exit_event.set()
                    self.frame_pool.Close()
                    self.frame_pool = None

        patch_windows_graphics_capture_class(FakeCapture)
        capture = FakeCapture()
        capture.close()
        capture.callback.join(1.0)
        self.assertFalse(capture.callback.is_alive())
        self.assertTrue(capture.callback_finished_during_close)

    def test_frame_timeout_restarts_once_and_respects_window_and_cancellation(self):
        frame = np.ones((720, 1280, 3), dtype=np.uint8)

        class FakeCapture:
            def __init__(self, frames, state):
                self.lock = threading.RLock()
                self.get_frame_lock = threading.RLock()
                self.exit_event = threading.Event()
                self._frame_cancel_generation = 0
                self.hwnd_window = SimpleNamespace(
                    exists=state != "disconnected", hwnd=10, width=1280, height=720
                )
                self.frames = iter(frames)
                self.state = state
                self.read_calls = self.close_calls = 0
                if state == "exit":
                    self.exit_event.set()

            def do_get_frame(self):
                self.read_calls += 1
                if self.state == "cancelled":
                    self._frame_cancel_generation += 1
                return next(self.frames)

            def start_or_stop(self, capture_cursor=False):
                return True

            def close(self):
                self.close_calls += 1
                self._frame_cancel_generation += 1

            def frame_arrived_callback(self, *_args):
                pass

        patch_windows_graphics_capture_class(FakeCapture)
        cases = (
            ("healthy", (frame,), frame, 1, 0),
            ("recovered", (None, frame), frame, 2, 1),
            ("unavailable", (None, None), None, 2, 1),
            ("exit", (None,), None, 1, 0),
            ("disconnected", (None,), None, 1, 0),
            ("cancelled", (None,), None, 1, 0),
        )
        for state, frames, expected, reads, closes in cases:
            with self.subTest(state=state):
                capture = FakeCapture(frames, state)
                self.assertIs(capture.do_get_frame(), expected)
                self.assertEqual(reads, capture.read_calls)
                self.assertEqual(closes, capture.close_calls)

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
