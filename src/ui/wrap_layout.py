"""宽度不足时自动折行的 QLayout（Qt 官方 FlowLayout 示例的移植，加行对齐）。

框架 ok.ui.qt.widget.FlowLayout 是 QWidget 包装器，其最小宽度等于重建时所在行的
全宽：初始构建发生在宽窗口下会把宿主页 view 永久锁宽（窄窗口 resize 无法触达它，
形成死锁）。本实现是真正的 QLayout：minimumSize 只取最宽单个子项，配和
hasHeightForWidth，窄窗口自然折行、宽窗口回到一行。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class WrapLayout(QLayout):
    def __init__(self, parent=None, alignment=Qt.AlignmentFlag.AlignLeft, *, spacing=None,
                 max_columns=None):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._max_columns = max_columns
        self.setAlignment(alignment)
        if spacing is not None:
            self.setSpacing(spacing)

    def setAlignment(self, alignment):
        self._alignment = alignment
        return super().setAlignment(alignment)

    def __del__(self):  # pragma: no cover - 安全网
        while self._items:
            self._items.pop()

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _horizontal_spacing(self) -> int:
        if self.spacing() >= 0:
            return self.spacing()
        parent = self.parentWidget()
        if parent is None:
            return -1
        return parent.style().layoutSpacing(
            QSizePolicy.ControlType.PushButton,
            QSizePolicy.ControlType.PushButton,
            Qt.Orientation.Horizontal,
        )

    def _vertical_spacing(self) -> int:
        if self.spacing() >= 0:
            return self.spacing()
        parent = self.parentWidget()
        if parent is None:
            return -1
        return parent.style().layoutSpacing(
            QSizePolicy.ControlType.PushButton,
            QSizePolicy.ControlType.PushButton,
            Qt.Orientation.Vertical,
        )

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        h_space = self._horizontal_spacing()
        v_space = self._vertical_spacing()
        line: list[tuple[QLayoutItem, int, int]] = []

        def flush_line(line_y: int) -> None:
            if test_only:
                return
            if self._alignment & Qt.AlignmentFlag.AlignRight:
                line_width = sum(w + h_space for _, w, _ in line) - h_space
                line_x = max(effective.x(), effective.right() - line_width + 1)
            elif self._alignment & Qt.AlignmentFlag.AlignHCenter:
                line_width = sum(w + h_space for _, w, _ in line) - h_space
                line_x = effective.x() + max(0, (effective.width() - line_width) // 2)
            else:
                line_x = effective.x()
            for item, width, height in line:
                item.setGeometry(QRect(line_x, line_y, width, height))
                line_x += width + h_space

        for item in self._items:
            # 只跳过显式隐藏的子项；页面整体不可见（切走 tab）时 isVisible() 为假，
            # 用它会把整页算成零高，污染 SetMinimumSize 链上的卡片高度缓存。
            if item.widget() is not None and item.widget().isHidden():
                if not test_only:
                    item.setGeometry(QRect())
                continue
            hint = item.sizeHint()
            width = max(item.minimumSize().width(), min(hint.width(), effective.width()))
            height = hint.height()
            if item.hasHeightForWidth():
                height = max(item.minimumSize().height(), item.heightForWidth(width))
            next_x = x + width + h_space
            if line and (
                next_x - h_space > effective.right() + 1
                or (self._max_columns is not None and len(line) >= self._max_columns)
            ):
                flush_line(y)
                y += line_height + v_space
                x = effective.x()
                next_x = x + width + h_space
                line = []
                line_height = 0
            line.append((item, width, height))
            x = next_x
            line_height = max(line_height, height)
        flush_line(y)
        return y + line_height - rect.y() + margins.bottom()


def wrap_container(widgets, alignment=Qt.AlignmentFlag.AlignLeft, spacing: int = 8):
    """返回 (容器 widget, WrapLayout)。

    WrapLayout 必须挂在容器 widget 上才能塞进只收 widget 的槽位（如 Card.topLayout）；
    此时父布局只认容器的 sizePolicy，不在策略上声明 heightForWidth 的话，
    宽度变化后父布局不会重算高度，内容会被压成一条细缝。工厂负责把两者接上。
    """
    container = QWidget()
    wrap = WrapLayout(container, alignment=alignment)
    wrap.setSpacing(spacing)
    for widget in widgets:
        wrap.addWidget(widget)
    policy = container.sizePolicy()
    policy.setHeightForWidth(True)
    container.setSizePolicy(policy)
    return container, wrap
