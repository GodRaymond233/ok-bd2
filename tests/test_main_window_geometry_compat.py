import unittest

from src.compat.main_window_geometry import (
    MAIN_WINDOW_GEOMETRY_DEBOUNCE_MS,
    patch_main_window_geometry_events,
)


class _Signal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class _Timer:
    instances = []

    def __init__(self, parent):
        self.parent = parent
        self.single_shot = False
        self.interval = 0
        self.starts = 0
        self.stops = 0
        self.active = False
        self.timeout = _Signal()
        self.instances.append(self)

    def setSingleShot(self, value):
        self.single_shot = value

    def setInterval(self, value):
        self.interval = value

    def start(self):
        self.starts += 1
        self.active = True

    def stop(self):
        self.stops += 1
        self.active = False

    def isActive(self):
        return self.active


class MainWindowGeometryCompatibilityTest(unittest.TestCase):
    def setUp(self):
        _Timer.instances.clear()

    def test_top_level_events_share_one_timer_and_close_flushes_it(self):
        class FakeMainWindow:
            def __init__(self):
                self.moves = 0
                self.resizes = 0
                self.updates = 0
                self.filtered = 0
                self.closes = 0

            def moveEvent(self, _event):
                self.moves += 1

            def resizeEvent(self, _event):
                self.resizes += 1

            def closeEvent(self, _event):
                self.closes += 1

            def eventFilter(self, _obj, _event):
                self.filtered += 1
                return "original"

            def update_ok_config(self):
                self.updates += 1

        original_event_filter = FakeMainWindow.eventFilter
        patch_main_window_geometry_events(FakeMainWindow, _Timer)
        window = FakeMainWindow()

        for _index in range(20):
            window.moveEvent(object())
            window.resizeEvent(object())

        self.assertEqual(1, len(_Timer.instances))
        timer = _Timer.instances[0]
        self.assertTrue(timer.single_shot)
        self.assertEqual(MAIN_WINDOW_GEOMETRY_DEBOUNCE_MS, timer.interval)
        self.assertEqual(40, timer.starts)
        self.assertEqual(0, window.updates)
        self.assertIs(original_event_filter, FakeMainWindow.eventFilter)
        self.assertEqual("original", window.eventFilter(window, object()))
        self.assertEqual(1, window.filtered)

        window.closeEvent(object())

        self.assertEqual(1, timer.stops)
        self.assertFalse(timer.isActive())
        self.assertEqual(1, window.updates)
        self.assertEqual(1, window.closes)


if __name__ == "__main__":
    unittest.main()
