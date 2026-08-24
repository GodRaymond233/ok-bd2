import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import win32con
from ok.gui.widget.Card import Card
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.ui.live_screenshot import install_live_screenshot
from src.ui.manual_resolution import (
    DEFAULT_MANUAL_RESOLUTION,
    MANUAL_RESOLUTIONS,
    ManualResolutionError,
    ManualResolutionWidget,
    WindowResizeResult,
    _ResizeJob,
    format_resolution,
    resize_game_window,
)


class _FakeHwndWindow:
    def __init__(self):
        self.hwnd = 101
        self.updates = 0

    def do_update_window_size(self):
        self.updates += 1


class _FakeDeviceManager:
    def __init__(self):
        self.hwnd_window = _FakeHwndWindow()

    @staticmethod
    def get_preferred_device():
        return {"device": "windows"}


class _FakeBackend:
    def __init__(
        self,
        *,
        style=win32con.WS_POPUP,
        rect=(100, 100, 1700, 1000),
        monitor_area=(0, 0, 2560, 1440),
        monitor_bounds=None,
        maximized=False,
        on_get_frame_size=None,
        position_failures=0,
    ):
        self.style = style | (win32con.WS_MAXIMIZE if maximized else 0)
        self.monitor_area = monitor_area
        self.monitor_bounds = monitor_bounds or monitor_area
        self.calls = []
        self.valid = True
        self.minimized = False
        self.maximized = maximized
        self.on_get_frame_size = on_get_frame_size
        self.position_failures = position_failures
        show_command = win32con.SW_SHOWMAXIMIZED if maximized else win32con.SW_SHOWNORMAL
        self.placement = (0, show_command, (0, 0), (0, 0), rect)
        self.rect = self.monitor_bounds if maximized else rect
        self._sync_client_size()

    def _sync_client_size(self):
        width = self.rect[2] - self.rect[0]
        height = self.rect[3] - self.rect[1]
        if self.style & win32con.WS_CAPTION:
            width -= 16
            height -= 39
        self.client_size = (width, height)

    def is_window(self, _hwnd):
        return self.valid

    def is_minimized(self, _hwnd):
        return self.minimized

    def is_maximized(self, _hwnd):
        return self.maximized

    def restore(self, _hwnd):
        self.calls.append(("restore",))
        self.maximized = False
        self.style &= ~win32con.WS_MAXIMIZE
        self.rect = tuple(self.placement[4])
        self._sync_client_size()

    def get_window_placement(self, _hwnd):
        return self.placement

    def set_window_placement(self, _hwnd, placement):
        self.calls.append(("placement", placement))
        self.placement = placement
        self.maximized = placement[1] == win32con.SW_SHOWMAXIMIZED
        if self.maximized:
            self.style |= win32con.WS_MAXIMIZE
            self.rect = self.monitor_bounds
        else:
            self.style &= ~win32con.WS_MAXIMIZE
            self.rect = tuple(placement[4])
        self._sync_client_size()

    def get_style(self, _hwnd):
        return self.style

    def set_style(self, _hwnd, style):
        self.calls.append(("style", style))
        self.style = style

    def get_window_rect(self, _hwnd):
        return self.rect

    def get_client_size(self, _hwnd):
        return self.client_size

    def get_monitor_area(self, _hwnd):
        return self.monitor_area

    def get_monitor_bounds(self, _hwnd):
        return self.monitor_bounds

    def get_frame_size(self, _hwnd, style, _target):
        if self.on_get_frame_size is not None:
            self.on_get_frame_size()
        return (16, 39) if style & win32con.WS_CAPTION else (0, 0)

    def set_window_pos(self, _hwnd, rect, *, frame_changed):
        self.calls.append(("position", rect, frame_changed))
        if self.position_failures > 0:
            self.position_failures -= 1
            raise OSError("SetWindowPos failed")
        self.rect = rect
        self._sync_client_size()


class _StartTabStub(QWidget):
    def __init__(self):
        super().__init__()
        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        top_row = QWidget(self.view)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addWidget(top_row)

        self.device_list = QLabel()
        self.capture_list = QLabel()
        self.interaction_list = QLabel()
        self.device_container = Card("选择窗口", self.device_list, stretch=1)
        self.capture_container = Card("截图方式", self.capture_list, stretch=1)
        self.interaction_container = Card("选择交互方式", self.interaction_list, stretch=1)
        for card in (
            self.device_container,
            self.capture_container,
            self.interaction_container,
        ):
            top_layout.addWidget(card)

        self.debug_widget = QWidget()
        self.debug_card = Card("开发工具", self.debug_widget)
        self.vBoxLayout.addWidget(self.debug_card)
        self.overlay_widget = QWidget()
        self.overlay_card = Card("调试悬浮窗", self.overlay_widget)
        self.vBoxLayout.addWidget(self.overlay_card)


class ManualResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_resolution_catalog_contains_only_requested_common_sizes(self):
        self.assertEqual(
            (
                (3840, 2160),
                (2560, 1440),
                (1920, 1080),
                (1600, 900),
                (1366, 768),
                (1280, 720),
            ),
            MANUAL_RESOLUTIONS,
        )

    def test_widget_lists_resolutions_and_defaults_to_1080p(self):
        widget = ManualResolutionWidget()

        self.assertEqual(len(MANUAL_RESOLUTIONS), widget.resolution_combo.count())
        self.assertEqual(DEFAULT_MANUAL_RESOLUTION, widget.selected_resolution)
        self.assertEqual("3840 × 2160", widget.resolution_combo.itemText(0))
        self.assertEqual("1280 × 720", widget.resolution_combo.itemText(5))
        self.assertEqual("", widget.status_label.text())
        self.assertTrue(widget.status_label.isHidden())

        widget.set_status("正在调整")
        self.assertEqual("正在调整", widget.status_label.text())
        self.assertFalse(widget.status_label.isHidden())
        widget.close()

    def test_resize_converts_borderless_window_and_verifies_client_size(self):
        manager = _FakeDeviceManager()
        backend = _FakeBackend(maximized=True)

        result = resize_game_window(
            manager,
            (1280, 720),
            backend=backend,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(WindowResizeResult(1280, 720, True), result)
        self.assertIn(("restore",), backend.calls)
        self.assertFalse(backend.maximized)
        self.assertTrue(backend.style & win32con.WS_CAPTION)
        self.assertFalse(backend.style & win32con.WS_POPUP)
        self.assertEqual((1280, 720), backend.client_size)
        self.assertEqual((632, 340, 1928, 1099), backend.rect)
        self.assertGreaterEqual(manager.hwnd_window.updates, 2)

    def test_native_monitor_target_uses_borderless_without_caption_flicker(self):
        manager = _FakeDeviceManager()
        backend = _FakeBackend(
            monitor_area=(0, 0, 1920, 1040),
            monitor_bounds=(0, 0, 1920, 1080),
        )

        result = resize_game_window(
            manager,
            (1920, 1080),
            backend=backend,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(WindowResizeResult(1920, 1080, True), result)
        self.assertEqual(win32con.WS_POPUP, backend.style)
        self.assertEqual((0, 0, 1920, 1080), backend.rect)
        self.assertFalse(any(call[0] == "style" for call in backend.calls))

    def test_resize_centers_on_the_monitor_containing_the_game(self):
        manager = _FakeDeviceManager()
        backend = _FakeBackend(
            monitor_area=(1920, 0, 3286, 728),
            monitor_bounds=(1920, 0, 3286, 768),
        )

        result = resize_game_window(
            manager,
            (1280, 720),
            backend=backend,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(WindowResizeResult(1280, 720, True), result)
        self.assertEqual((1963, 4, 3243, 724), backend.rect)
        self.assertEqual(win32con.WS_POPUP, backend.style)

    def test_resize_noop_keeps_existing_window_style(self):
        manager = _FakeDeviceManager()
        backend = _FakeBackend(rect=(100, 100, 1380, 820))

        result = resize_game_window(manager, (1280, 720), backend=backend)

        self.assertEqual(WindowResizeResult(1280, 720, False), result)
        self.assertEqual([], backend.calls)
        self.assertEqual(win32con.WS_POPUP, backend.style)

    def test_failed_resize_preserves_style_geometry_and_maximized_placement(self):
        manager = _FakeDeviceManager()
        original_rect = (100, 100, 1700, 1000)
        backend = _FakeBackend(rect=original_rect, monitor_area=(0, 0, 1920, 1080))

        with self.assertRaisesRegex(ManualResolutionError, "无法容纳"):
            resize_game_window(manager, (3840, 2160), backend=backend)

        self.assertEqual(win32con.WS_POPUP, backend.style)
        self.assertEqual(original_rect, backend.rect)
        self.assertEqual([], backend.calls)

        backend = _FakeBackend(maximized=True, position_failures=1)
        original_placement = backend.placement
        with self.assertRaisesRegex(OSError, "SetWindowPos failed"):
            resize_game_window(manager, (1280, 720), backend=backend)
        self.assertTrue(backend.maximized)
        self.assertEqual(original_placement, backend.placement)
        self.assertEqual(
            win32con.WS_POPUP | win32con.WS_MAXIMIZE,
            backend.style,
        )

    def test_running_task_is_rejected_before_window_changes(self):
        manager = _FakeDeviceManager()
        backend = _FakeBackend()
        executor = SimpleNamespace(current_task=object(), paused=False)

        with self.assertRaisesRegex(ManualResolutionError, "请先暂停任务"):
            resize_game_window(
                manager,
                (1280, 720),
                executor=executor,
                backend=backend,
            )

        self.assertEqual([], backend.calls)

        executor.current_task = None
        backend = _FakeBackend(
            on_get_frame_size=lambda: setattr(executor, "current_task", object())
        )
        with self.assertRaisesRegex(ManualResolutionError, "请先暂停任务"):
            resize_game_window(
                manager,
                (1280, 720),
                executor=executor,
                backend=backend,
            )
        self.assertEqual([], backend.calls)

    def test_resize_job_reports_failures_without_touching_qt_widgets(self):
        def fail(_manager, target, *, executor, cancel_event):
            self.assertFalse(cancel_event.is_set())
            raise ManualResolutionError(f"拒绝 {format_resolution(target)}")

        job = _ResizeJob(object(), None, (1600, 900), fail)
        succeeded = []
        failed = []
        job.signals.succeeded.connect(succeeded.append)
        job.signals.failed.connect(failed.append)

        job.run()

        self.assertEqual([], succeeded)
        self.assertEqual(["拒绝 1600 × 900"], failed)

    def test_install_places_manual_resolution_card_in_lower_side_column_once(self):
        tab = _StartTabStub()

        install_live_screenshot(tab)
        original_card = tab.manual_resolution_card
        install_live_screenshot(tab)

        self.assertIs(original_card, tab.manual_resolution_card)
        self.assertEqual("手动调整分辨率", tab.manual_resolution_card.titleLabel.text())
        self.assertIsInstance(tab.manual_resolution_widget, ManualResolutionWidget)
        self.assertIs(tab.live_screenshot_row.parentWidget(), tab.view)

        tab.live_screenshot_widget._shutdown()
        tab.close()


if __name__ == "__main__":
    unittest.main()
