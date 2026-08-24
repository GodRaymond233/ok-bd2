from __future__ import annotations

from weakref import ref

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QTextEdit,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon,
    FluentIconBase,
    isDarkTheme,
    qconfig,
)


def _build_expand_easing() -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.4, 0.0), QPointF(0.2, 1.0), QPointF(1.0, 1.0))
    return curve


_EXPAND_EASING = _build_expand_easing()


class WrappingFlowLayout(QLayout):
    """A small flow layout that recomputes rows whenever its width changes."""

    def __init__(self, parent=None, spacing=8, alignment=Qt.AlignLeft):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self._flow_alignment = alignment
        super().setAlignment(alignment)
        self.setContentsMargins(0, 0, 0, 0)

    def setAlignment(self, alignment):
        self._flow_alignment = alignment
        return super().setAlignment(alignment)

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
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
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
        available_width = max(0, effective.width())
        rows = []
        row = []
        row_width = 0
        row_height = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                if not test_only:
                    item.setGeometry(QRect())
                continue
            item_size = item.sizeHint()
            proposed_width = (
                item_size.width()
                if not row
                else row_width + self._spacing + item_size.width()
            )
            if row and proposed_width > available_width:
                rows.append((row, row_width, row_height))
                row = []
                row_width = 0
                row_height = 0
            row.append((item, item_size))
            row_width += item_size.width() if len(row) == 1 else self._spacing + item_size.width()
            row_height = max(row_height, item_size.height())

        if row:
            rows.append((row, row_width, row_height))

        y = effective.y()
        for row, width, height in rows:
            if self._flow_alignment & Qt.AlignRight:
                x = max(effective.x(), effective.right() - width + 1)
            elif self._flow_alignment & Qt.AlignHCenter:
                x = effective.x() + max(0, (effective.width() - width) // 2)
            else:
                x = effective.x()
            for item, item_size in row:
                if not test_only:
                    item.setGeometry(QRect(QPoint(x, y), item_size))
                x += item_size.width() + self._spacing
            y += height + self._spacing

        if not rows:
            content_height = 0
        else:
            content_height = y - effective.y() - self._spacing
        return content_height + margins.top() + margins.bottom()


class ResponsiveFlowWidget(QWidget):
    def __init__(self, alignment=Qt.AlignLeft):
        super().__init__()
        self.alignment = alignment
        self.flow_layout = WrappingFlowLayout(self, alignment=alignment)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def add_widget(self, widget):
        self.flow_layout.addWidget(widget)
        self.updateGeometry()




def get_task_badge_info(task) -> tuple[str, str, str, str]:
    name = str(getattr(task, "name", ""))
    group = str(getattr(task, "group_name", ""))
    if name == "一键完成日常":
        return "日常合辑", "#d97706", "rgba(217, 119, 6, 0.12)", "rgba(217, 119, 6, 0.28)"
    elif "PVP" in name or "镜中之战" in name:
        return "PVP", "#e11d48", "rgba(225, 29, 72, 0.12)", "rgba(225, 29, 72, 0.28)"
    elif "刷级" in name or "压制" in name or group == "自动刷级":
        return "自动刷级", "#7c3aed", "rgba(124, 58, 237, 0.12)", "rgba(124, 58, 237, 0.28)"
    elif "跑商" in name or "砍价" in name:
        return "跑商", "#ea580c", "rgba(234, 88, 12, 0.12)", "rgba(234, 88, 12, 0.28)"
    elif "内测" in group or "跑图" in name:
        return "内测功能", "#059669", "rgba(5, 150, 105, 0.12)", "rgba(5, 150, 105, 0.28)"
    elif group == "日常/周常":
        return "日常", "#0284c7", "rgba(2, 132, 199, 0.12)", "rgba(2, 132, 199, 0.28)"
    elif group == "测试":
        return "测试", "#475569", "rgba(71, 85, 105, 0.12)", "rgba(71, 85, 105, 0.28)"
    return "任务", "#0284c7", "rgba(2, 132, 199, 0.12)", "rgba(2, 132, 199, 0.28)"


TASK_ICON_MAP: dict[str, FluentIconBase] = {
    "一键完成日常": FluentIcon.COMPLETED,
    "公会、小屋、酒馆": FluentIcon.PEOPLE,
    "快速狩猎": FluentIcon.RINGER,
    "免费抽抽乐": FluentIcon.HEART,
    "广场女神像": FluentIcon.PIN,
    "跑商": FluentIcon.SHOPPING_CART,
    "每日跑商": FluentIcon.SHOPPING_CART,
    "自动PVP": FluentIcon.GAME,
    "PVP": FluentIcon.GAME,
    "镜中之战": FluentIcon.GAME,
    "快速压制": FluentIcon.SYNC,
    "自动刷级": FluentIcon.SYNC,
    "每周跑图": FluentIcon.GLOBE,
    "地图采集": FluentIcon.GLOBE,
}


def apply_task_card_badge_and_style(card, task):
    if getattr(card, "_bd2_badge_installed", False):
        return

    task_name = str(getattr(task, "name", ""))
    if getattr(task, "icon", None) is None:
        matched_icon = None
        for pattern_name, icon in TASK_ICON_MAP.items():
            if pattern_name in task_name:
                matched_icon = icon
                break
        if matched_icon is not None:
            try:
                card.card.iconLabel.setIcon(matched_icon)
            except Exception:
                pass

    badge_text, text_color, bg_color, border_color = get_task_badge_info(task)

    if badge_text and hasattr(card.card, "vBoxLayout") and hasattr(card.card, "titleLabel"):
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        header_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        title_label = card.card.titleLabel
        card.card.vBoxLayout.removeWidget(title_label)

        badge_label = QLabel(badge_text, card.card)
        badge_label.setObjectName("bd2CategoryBadge")
        badge_label.setStyleSheet(
            "QLabel#bd2CategoryBadge {"
            " font-size: 11px;"
            " font-weight: 700;"
            " border-radius: 4px;"
            " padding: 1px 6px;"
            f" color: {text_color};"
            f" background-color: {bg_color};"
            f" border: 1px solid {border_color};"
            " }"
        )

        header_layout.addWidget(title_label, 0, Qt.AlignVCenter)
        header_layout.addWidget(badge_label, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)

        card.card.vBoxLayout.insertLayout(0, header_layout)
        card._bd2_badge_installed = True
        card.badge_label = badge_label

    card_ref = ref(card)

    def update_theme(*_args):
        target = card_ref()
        if target is None:
            return

        is_dark = isDarkTheme()
        if is_dark:
            bg = "rgba(255, 255, 255, 0.05)"
            border = "rgba(255, 255, 255, 0.08)"
            view_bg = "rgba(25, 25, 28, 0.95)"
            view_border = "rgba(255, 255, 255, 0.06)"
            title_color = "#ffffff"
            content_color = "#a1a1aa"
            divider_color = "rgba(255, 255, 255, 0.08)"
        else:
            bg = "rgba(255, 255, 255, 0.85)"
            border = "rgba(0, 0, 0, 0.07)"
            view_bg = "rgba(250, 250, 252, 0.95)"
            view_border = "rgba(0, 0, 0, 0.05)"
            title_color = "#18181b"
            content_color = "#71717a"
            divider_color = "rgba(0, 0, 0, 0.06)"

        sheet = f"""
        ExpandSettingCard {{
            background-color: transparent;
            border: none;
        }}
        HeaderSettingCard {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: 8px;
        }}
        #view {{
            background-color: {view_bg};
            border: 1px solid {view_border};
            border-top: none;
            border-bottom-left-radius: 8px;
            border-bottom-right-radius: 8px;
        }}
        #scrollWidget {{
            border: none;
            background-color: transparent;
        }}
        QLabel#titleLabel {{
            color: {title_color};
            font-size: 14px;
            font-weight: 600;
        }}
        QLabel#contentLabel {{
            color: {content_color};
            font-size: 12px;
        }}
        QFrame#subConfigsDivider {{
            background-color: {divider_color};
            border: none;
        }}
        """
        target.setStyleSheet(sheet)

    def disconnect_theme(*_args):
        try:
            qconfig.themeChanged.disconnect(update_theme)
        except (RuntimeError, TypeError):
            pass

    update_theme()
    qconfig.themeChanged.connect(update_theme)
    card.destroyed.connect(disconnect_theme)


def install_responsive_task_config_ui():
    """Make ok-script task settings shrink and reflow with the app window."""

    from ok.gui.tasks import LabelAndMultiSelection as multi_selection_module
    from ok.gui.tasks.ConfigCard import ConfigCard
    from ok.gui.tasks.LabelAndTextEdit import LabelAndTextEdit
    from ok.gui.tasks.LabelAndWidget import LabelAndWidget
    from ok.gui.tasks.TaskCard import TaskCard

    if getattr(LabelAndWidget, "_bd2_responsive_ui_installed", False):
        return

    original_label_init = LabelAndWidget.__init__
    original_add_widget = LabelAndWidget.add_widget
    original_add_layout = LabelAndWidget.add_layout
    original_config_resize_event = ConfigCard.resizeEvent
    original_task_card_init = TaskCard.__init__

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

    def responsive_add_layout(self, layout, stretch=1):
        original_add_layout(self, layout, stretch=stretch)
        # Button and file-selector rows add a QLayout instead of a QWidget.
        # Give their text column real width so wrapped descriptions cannot be
        # measured one character per line and inflate the row by hundreds of pixels.
        self.layout.setStretch(0, 3)

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
        # Re-polish this card after changing the dynamic selector without
        # replacing the application style for the whole widget subtree.
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

        # _adjustViewSize() has just measured the current content and applied
        # it to the spacer.  Reuse that value instead of walking the layout a
        # second time during the same toggle.
        content_height = self.spaceWidget.height()

        # Perceived smoothness is bounded by the largest single-frame jump, not
        # by the tick rate: an ease that leaves the start value abruptly reads
        # as a jump even at vsync cadence.  (0.4, 0, 0.2, 1) eases in from
        # ~1px/frame, carries the travel through the middle, and cushions into
        # the end over several sub-pixel frames.  Duration scales with distance
        # so per-frame travel stays comparable across card sizes; collapse runs
        # slightly quicker, which reads as responsive rather than hurried.
        base_duration = min(420, max(280, int(240 + content_height * 0.28)))
        self.expandAni.setEasingCurve(_EXPAND_EASING)

        if is_expand:
            self.expandAni.setDuration(base_duration)
            self.verticalScrollBar().setValue(content_height)
            self.expandAni.setStartValue(content_height)
            self.expandAni.setEndValue(0)
        else:
            # End on content_height, not scrollbar maximum(): height clamps at
            # the header, so any value past content_height produces no motion.
            # Overshooting it spends part of the duration on a frozen card and
            # doubles the travel in the frames that do move.
            self.expandAni.setDuration(int(base_duration * 0.85))
            self.expandAni.setStartValue(0)
            self.expandAni.setEndValue(content_height)

        self.expandAni.start()
        self.card.expandButton.setExpand(is_expand)

    def responsive_config_resize_event(self, event):
        original_config_resize_event(self, event)
        self._adjustViewSize()

    LabelAndWidget.__init__ = responsive_label_init
    LabelAndWidget.add_widget = responsive_add_widget
    LabelAndWidget.add_layout = responsive_add_layout
    LabelAndTextEdit.__init__ = responsive_text_edit_init
    LabelAndTextEdit._update_width = responsive_text_edit_width
    ConfigCard._adjustViewSize = responsive_adjust_view_size
    ConfigCard._onExpandValueChanged = responsive_expand_value_changed
    ConfigCard.setExpand = responsive_set_expand
    def responsive_task_card_init(self, task, onetime):
        original_task_card_init(self, task, onetime)
        apply_task_card_badge_and_style(self, task)

    ConfigCard.resizeEvent = responsive_config_resize_event
    TaskCard.__init__ = responsive_task_card_init
    multi_selection_module.FlowLayout = ResponsiveFlowWidget
    LabelAndWidget._bd2_responsive_ui_installed = True
