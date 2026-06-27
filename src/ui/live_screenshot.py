import time
from concurrent.futures import ThreadPoolExecutor

import cv2
from ok.gui.widget.Card import Card
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

PREVIEW_INTERVAL_MS = 50
PREVIEW_MIN_WIDTH = 320
PREVIEW_ASPECT_WIDTH = 16
PREVIEW_ASPECT_HEIGHT = 9
TOP_ROW_MAX_HEIGHT = 240
CAPTURE_LIST_MAX_HEIGHT = 180
TOP_CARD_CONTENT_HEIGHT = CAPTURE_LIST_MAX_HEIGHT


class LivePreviewLabel(QLabel):
    def __init__(self):
        super().__init__()
        self._image: QImage | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(PREVIEW_MIN_WIDTH, self.heightForWidth(PREVIEW_MIN_WIDTH))
        size_policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        size_policy.setHeightForWidth(True)
        self.setSizePolicy(size_policy)
        self.setText("等待截图")
        self.setStyleSheet(
            "QLabel {background-color: #111111;border-radius: 6px;color: rgba(255, 255, 255, 150);}"
        )

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return max(1, round(width * PREVIEW_ASPECT_HEIGHT / PREVIEW_ASPECT_WIDTH))

    def sizeHint(self):
        return QSize(480, 270)

    def set_image(self, image: QImage | None):
        self._image = image
        self._update_pixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_aspect_height()
        self._update_pixmap()

    def _sync_aspect_height(self):
        width = self.width()
        if width <= 0:
            return

        target_height = self.heightForWidth(width)
        if self.minimumHeight() == target_height and self.maximumHeight() == target_height:
            return

        self.setMinimumHeight(target_height)
        self.setMaximumHeight(target_height)

    def _update_pixmap(self):
        if self._image is None or self._image.isNull():
            self.clear()
            self.setText("等待截图")
            return

        pixmap = QPixmap.fromImage(self._image)
        scaled = pixmap.scaled(
            self.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.setText("")
        self.setPixmap(scaled)


class LiveScreenshotWidget(QWidget):
    frame_ready = Signal(QImage, str)
    status_ready = Signal(str)

    def __init__(self):
        super().__init__()
        self._capture_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="LiveScreenshot",
        )
        self._capture_pending = False
        self._last_status = ""
        self._last_frame_at = 0.0
        self._active = False

        self.preview = LivePreviewLabel()
        self.status_label = CaptionLabel("等待选择窗口")
        self.status_label.setStyleSheet("color: #bbbbbb;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.status_label)

        self.frame_ready.connect(self._display_frame)
        self.status_ready.connect(self._display_empty)

        self.timer = QTimer(self)
        self.timer.setInterval(PREVIEW_INTERVAL_MS)
        self.timer.timeout.connect(self._request_frame)

        self.destroyed.connect(self._shutdown)

    def showEvent(self, event):
        super().showEvent(event)
        self.start_preview()

    def hideEvent(self, event):
        self.stop_preview()
        super().hideEvent(event)

    def start_preview(self):
        self._active = True
        if not self.timer.isActive():
            self.timer.start()
        self._request_frame()

    def stop_preview(self):
        self._active = False
        if self.timer.isActive():
            self.timer.stop()

    def _request_frame(self):
        if self._capture_pending or not self._active or not self.isVisible():
            return

        try:
            from ok import og

            if getattr(og, "exit_event", None) is not None and og.exit_event.is_set():
                self.timer.stop()
                return
        except Exception:
            pass

        self._capture_pending = True
        future = self._capture_executor.submit(self._capture_image)
        future.add_done_callback(self._capture_finished)

    def _capture_finished(self, future):
        try:
            image, status = future.result()
            if not self._active:
                return
            if image is not None:
                self.frame_ready.emit(image, status)
            else:
                self.status_ready.emit(status)
        except Exception as exc:
            self.status_ready.emit(f"截图失败：{exc}")
        finally:
            self._capture_pending = False

    def _capture_image(self) -> tuple[QImage | None, str]:
        from ok import og

        device_manager = getattr(og, "device_manager", None)
        if device_manager is None:
            return None, "设备管理未就绪"

        preferred = device_manager.get_preferred_device()
        if preferred is None:
            return None, "等待选择窗口"

        method = getattr(device_manager, "capture_method", None)
        if method is None:
            return None, "等待选择截图方式"

        try:
            if hasattr(method, "connected") and not method.connected():
                return None, "截图方式未连接"
        except Exception:
            return None, "截图方式未连接"

        frame = self._recent_executor_frame()
        if frame is None:
            frame = method.get_frame()

        if frame is None:
            return None, "暂无截图"

        image = self._frame_to_image(frame)
        height, width = frame.shape[:2]
        method_name = method.get_name() if hasattr(method, "get_name") else str(method)
        return image, f"{width}x{height} · {method_name}"

    @staticmethod
    def _recent_executor_frame():
        from ok import og

        executor = getattr(og, "executor", None)
        if executor is None:
            return None

        frame = executor.nullable_frame()
        if frame is None:
            return None

        last_frame_time = getattr(executor, "_last_frame_time", 0)
        if time.time() - last_frame_time > 0.08:
            return None

        return frame

    @staticmethod
    def _frame_to_image(frame) -> QImage:
        if len(frame.shape) == 2:
            rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        elif frame.shape[2] == 4:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
        else:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        height, width = rgb.shape[:2]
        bytes_per_line = rgb.strides[0]
        return QImage(
            rgb.data,
            width,
            height,
            bytes_per_line,
            QImage.Format_RGB888,
        ).copy()

    def _display_frame(self, image: QImage, status: str):
        if not self._active:
            return
        self._last_frame_at = time.time()
        self.preview.set_image(image)
        self._display_status(status)

    def _display_status(self, status: str):
        if not self._active:
            return
        if status == self._last_status:
            return
        self._last_status = status
        self.status_label.setText(status)

    def _display_empty(self, status: str):
        if not self._active:
            return
        self.preview.set_image(None)
        self._display_status(status)

    def _shutdown(self):
        self.stop_preview()
        self._capture_executor.shutdown(wait=False, cancel_futures=True)


def install_live_screenshot(start_tab) -> None:
    if getattr(start_tab, "_bd2_live_screenshot_installed", False):
        return

    device_container = getattr(start_tab, "device_container", None)
    if device_container is None:
        return

    parent = device_container.parentWidget()
    if parent is None or parent.layout() is None:
        return

    row_layout = parent.layout()
    parent.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    parent.setMaximumHeight(TOP_ROW_MAX_HEIGHT)

    for index in range(row_layout.count()):
        row_layout.setStretch(index, 1)

    device_list = getattr(start_tab, "device_list", None)
    if device_list is not None:
        device_list.setMinimumHeight(TOP_CARD_CONTENT_HEIGHT)
        device_list.setMaximumHeight(TOP_CARD_CONTENT_HEIGHT)
        device_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    for attr in ("device_container", "capture_container", "interaction_container"):
        container = getattr(start_tab, attr, None)
        if container is not None:
            container.setMinimumHeight(TOP_CARD_CONTENT_HEIGHT + 58)
            container.setMaximumHeight(TOP_CARD_CONTENT_HEIGHT + 58)
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    capture_list = getattr(start_tab, "capture_list", None)
    if capture_list is not None:
        capture_list.setMinimumHeight(TOP_CARD_CONTENT_HEIGHT)
        capture_list.setMaximumHeight(TOP_CARD_CONTENT_HEIGHT)
        capture_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    interaction_list = getattr(start_tab, "interaction_list", None)
    if interaction_list is not None:
        interaction_list.setMinimumHeight(TOP_CARD_CONTENT_HEIGHT)
        interaction_list.setMaximumHeight(TOP_CARD_CONTENT_HEIGHT)
        interaction_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    tab_layout = getattr(start_tab, "vBoxLayout", None)
    if tab_layout is None:
        return

    debug_card = _card_for_widget(getattr(start_tab, "debug_widget", None))
    overlay_card = _card_for_widget(getattr(start_tab, "overlay_widget", None))
    for card in (debug_card, overlay_card):
        if card is not None:
            tab_layout.removeWidget(card)

    live_widget = LiveScreenshotWidget()
    live_card = Card("实时截图", live_widget, stretch=0)
    live_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

    lower_row = QWidget(start_tab.view)
    lower_layout = QHBoxLayout(lower_row)
    lower_layout.setContentsMargins(0, 0, 0, 0)
    lower_layout.setSpacing(12)
    lower_layout.addWidget(live_card, 1, Qt.AlignTop)

    side_column = QWidget(lower_row)
    side_layout = QVBoxLayout(side_column)
    side_layout.setContentsMargins(0, 0, 0, 0)
    side_layout.setSpacing(12)
    for card in (debug_card, overlay_card):
        if card is not None:
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            side_layout.addWidget(card, 0)
    side_layout.addStretch(1)
    lower_layout.addWidget(side_column, 1)

    row_index = tab_layout.indexOf(parent)
    tab_layout.insertWidget(row_index + 1, lower_row, 0)

    start_tab.live_screenshot_widget = live_widget
    start_tab.live_screenshot_card = live_card
    start_tab.live_screenshot_row = lower_row
    start_tab._bd2_live_screenshot_installed = True


def _card_for_widget(widget: QWidget | None) -> QWidget | None:
    if widget is None:
        return None

    card_frame = widget.parentWidget()
    if card_frame is None:
        return None

    return card_frame.parentWidget()
