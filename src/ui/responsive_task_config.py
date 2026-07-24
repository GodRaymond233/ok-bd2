from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QLayout, QSizePolicy, QTextEdit, QWidget


class WrappingFlowLayout(QLayout):
    """A small flow layout that recomputes rows whenever its width changes."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(),
            margins.top(),
            -margins.right(),
            -margins.bottom(),
        )
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            item_size = item.sizeHint()
            next_x = x + item_size.width() + self._spacing
            if line_height and next_x - self._spacing > effective.right() + 1:
                x = effective.x()
                y += line_height + self._spacing
                next_x = x + item_size.width() + self._spacing
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))
            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + margins.bottom()


class ResponsiveFlowWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.flow_layout = WrappingFlowLayout(self)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def add_widget(self, widget):
        self.flow_layout.addWidget(widget)
        self.updateGeometry()


def install_responsive_task_config_ui():
    """Make ok-script task settings shrink and reflow with the app window."""

    from ok.gui.tasks import LabelAndMultiSelection as multi_selection_module
    from ok.gui.tasks.ConfigCard import ConfigCard
    from ok.gui.tasks.LabelAndTextEdit import LabelAndTextEdit
    from ok.gui.tasks.LabelAndWidget import LabelAndWidget

    if getattr(LabelAndWidget, "_bd2_responsive_ui_installed", False):
        return

    original_label_init = LabelAndWidget.__init__
    original_add_widget = LabelAndWidget.add_widget
    original_config_resize_event = ConfigCard.resizeEvent

    def responsive_label_init(self, title, content=None):
        original_label_init(self, title, content)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        for label in (self.title, getattr(self, "contentLabel", None)):
            if label is None:
                continue
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            size_policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            size_policy.setHeightForWidth(True)
            label.setSizePolicy(size_policy)

    def responsive_add_widget(self, widget, stretch=1):
        original_add_widget(self, widget, stretch=stretch)
        self.layout.setStretch(0, 3)
        widget_index = self.layout.indexOf(widget)
        if widget_index >= 0:
            self.layout.setStretch(widget_index, 2)

    original_text_edit_init = LabelAndTextEdit.__init__

    def responsive_text_edit_init(self, config_desc, config, key):
        original_text_edit_init(self, config_desc, config, key)
        self.text_edit.setMinimumWidth(120)
        self.text_edit.setMaximumWidth(16777215)
        self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout.setStretchFactor(self.text_edit, 2)

    def responsive_text_edit_width(self, _value):
        self.text_edit.setMinimumWidth(120)
        self.text_edit.setMaximumWidth(16777215)

    def responsive_config_content_height(self):
        """Return the layout height for the card's current rendered width."""

        width = max(0, self.view.width())
        if self.viewLayout.hasHeightForWidth():
            height = self.viewLayout.heightForWidth(width)
            if height >= 0:
                return height
        return self.viewLayout.sizeHint().height()

    def responsive_adjust_view_size(self):
        content_height = responsive_config_content_height(self)
        self.spaceWidget.setFixedHeight(content_height)
        if self.isExpand:
            self.setFixedHeight(self.card.height() + content_height)

    def responsive_expand_value_changed(self):
        content_height = responsive_config_content_height(self)
        header_height = self.card.height()
        self.setFixedHeight(
            max(
                header_height + content_height - self.verticalScrollBar().value(),
                header_height,
            )
        )

    def responsive_set_expand(self, is_expand):
        if is_expand and not getattr(self, "_expand_enabled", True):
            return
        if self.isExpand == is_expand:
            return

        self._adjustViewSize()
        self.isExpand = is_expand
        self.setProperty("isExpand", is_expand)
        self.setStyle(QApplication.style())

        content_height = responsive_config_content_height(self)
        if is_expand:
            self.verticalScrollBar().setValue(content_height)
            self.expandAni.setStartValue(content_height)
            self.expandAni.setEndValue(0)
        else:
            self.expandAni.setStartValue(0)
            self.expandAni.setEndValue(self.verticalScrollBar().maximum())

        self.expandAni.start()
        self.card.expandButton.setExpand(is_expand)

    def responsive_config_resize_event(self, event):
        original_config_resize_event(self, event)
        self._adjustViewSize()

    LabelAndWidget.__init__ = responsive_label_init
    LabelAndWidget.add_widget = responsive_add_widget
    LabelAndTextEdit.__init__ = responsive_text_edit_init
    LabelAndTextEdit._update_width = responsive_text_edit_width
    ConfigCard._adjustViewSize = responsive_adjust_view_size
    ConfigCard._onExpandValueChanged = responsive_expand_value_changed
    ConfigCard.setExpand = responsive_set_expand
    ConfigCard.resizeEvent = responsive_config_resize_event
    multi_selection_module.FlowLayout = ResponsiveFlowWidget
    LabelAndWidget._bd2_responsive_ui_installed = True
