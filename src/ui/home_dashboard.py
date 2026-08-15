from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
    isDarkTheme,
    qconfig,
)

DAILY_GROUP_NAME = "日常/周常"
DAILY_BATCH_NAME = "一键完成日常"
WIDE_LAYOUT_THRESHOLD = 820


class _TaskTile(QFrame):
    def __init__(self, task, run_callback, config_callback, parent=None):
        super().__init__(parent)
        self.task = task
        self.setObjectName("bd2TaskTile")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(78)

        marker = QLabel(_task_marker(task), self)
        marker.setObjectName("bd2TaskMarker")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(34, 34)

        title = StrongBodyLabel(str(getattr(task, "name", "未命名任务")), self)
        description = CaptionLabel(str(getattr(task, "description", "")), self)
        description.setWordWrap(True)
        description.setMaximumHeight(36)
        description.setObjectName("bd2MutedText")

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(title)
        text_layout.addWidget(description)

        self.config_button = PushButton("配置", self)
        self.config_button.clicked.connect(lambda: config_callback(self.task))
        self.run_button = PushButton(FluentIcon.PLAY, "运行", self)
        self.run_button.clicked.connect(lambda: run_callback(self.task))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(marker, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.config_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignVCenter)


class HomeDashboard(QWidget):
    """Presentation-only home surface backed by existing ok-script task objects."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.executor = getattr(main_window, "executor", None)
        self.daily_task = self._find_daily_task()
        self.independent_tasks = self._find_independent_tasks()
        self.task_tiles: list[_TaskTile] = []
        self._summary_columns = 0
        self._task_columns = 0
        self._entrance_columns = 0

        self.setObjectName("bd2HomeDashboard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._build_ui()
        self._apply_theme()
        self.refresh_runtime()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self.refresh_runtime)
        self.refresh_timer.start()

        qconfig.themeChanged.connect(self._apply_theme)
        QTimer.singleShot(0, self._reflow)

    def _build_ui(self) -> None:
        kicker = CaptionLabel("OK-BD2 · HOME", self)
        kicker.setObjectName("bd2Kicker")
        title = TitleLabel("今天，先做最重要的事", self)
        subtitle = BodyLabel(
            "高频操作集中在首页；任务实现、配置文件与原有调试工具保持不变。",
            self,
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("bd2MutedText")

        self.daily_card = self._build_daily_card()
        self.status_card = self._build_status_card()
        summary_widget = QWidget(self)
        self.summary_layout = QGridLayout(summary_widget)
        self.summary_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_layout.setHorizontalSpacing(12)
        self.summary_layout.setVerticalSpacing(12)

        tasks_card = QFrame(self)
        tasks_card.setObjectName("bd2SurfaceCard")
        tasks_title = SubtitleLabel("独立任务", tasks_card)
        tasks_hint = CaptionLabel(
            "需要单独补做时直接运行；配置仍打开原任务卡。",
            tasks_card,
        )
        tasks_hint.setObjectName("bd2MutedText")
        all_tasks_button = PushButton("查看全部任务", tasks_card)
        all_tasks_button.clicked.connect(self._navigate_to_daily_tasks)

        task_heading = QHBoxLayout()
        task_heading.setContentsMargins(0, 0, 0, 0)
        task_heading.setSpacing(10)
        heading_text = QVBoxLayout()
        heading_text.setContentsMargins(0, 0, 0, 0)
        heading_text.setSpacing(2)
        heading_text.addWidget(tasks_title)
        heading_text.addWidget(tasks_hint)
        task_heading.addLayout(heading_text, 1)
        task_heading.addWidget(all_tasks_button, 0, Qt.AlignmentFlag.AlignTop)

        task_grid_widget = QWidget(tasks_card)
        self.task_grid = QGridLayout(task_grid_widget)
        self.task_grid.setContentsMargins(0, 0, 0, 0)
        self.task_grid.setHorizontalSpacing(10)
        self.task_grid.setVerticalSpacing(10)
        for task in self.independent_tasks:
            tile = _TaskTile(task, self._start_task, self._open_task_config, task_grid_widget)
            self.task_tiles.append(tile)

        tasks_layout = QVBoxLayout(tasks_card)
        tasks_layout.setContentsMargins(18, 16, 18, 18)
        tasks_layout.setSpacing(14)
        tasks_layout.addLayout(task_heading)
        tasks_layout.addWidget(task_grid_widget)

        entrances_card = self._build_entrances_card()
        preserved_hint = CaptionLabel(
            "设备选择、实时截图、原始配置与调试工具仍完整保留在下方。",
            self,
        )
        preserved_hint.setObjectName("bd2PreservedHint")
        preserved_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 8)
        layout.setSpacing(12)
        layout.addWidget(kicker)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(2)
        layout.addWidget(summary_widget)
        layout.addWidget(tasks_card)
        layout.addWidget(entrances_card)
        layout.addWidget(preserved_hint)

    def _build_daily_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("bd2DailyCard")

        marker = QLabel("日", card)
        marker.setObjectName("bd2DailyMarker")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(42, 42)

        label = CaptionLabel("首选操作", card)
        label.setObjectName("bd2DailyEyebrow")
        title = SubtitleLabel(DAILY_BATCH_NAME, card)
        self.daily_summary_label = BodyLabel(self._daily_summary(), card)
        self.daily_summary_label.setWordWrap(True)
        self.daily_summary_label.setObjectName("bd2MutedText")

        heading_text = QVBoxLayout()
        heading_text.setContentsMargins(0, 0, 0, 0)
        heading_text.setSpacing(3)
        heading_text.addWidget(label)
        heading_text.addWidget(title)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(12)
        heading.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)
        heading.addLayout(heading_text, 1)

        self.daily_config_button = PushButton(FluentIcon.SETTING, "调整项目", card)
        self.daily_config_button.clicked.connect(
            lambda: self._open_task_config(self.daily_task)
        )
        self.daily_start_button = PrimaryPushButton(
            FluentIcon.PLAY,
            "开始一键日常",
            card,
        )
        self.daily_start_button.clicked.connect(
            lambda: self._start_task(self.daily_task)
        )

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(self.daily_config_button)
        actions.addWidget(self.daily_start_button)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(heading)
        layout.addWidget(self.daily_summary_label)
        layout.addStretch(1)
        layout.addLayout(actions)
        return card

    def _build_status_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("bd2StatusCard")

        title = StrongBodyLabel("运行状态", card)
        self.status_pill = QLabel("准备就绪", card)
        self.status_pill.setObjectName("bd2StatusPill")
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_pill.setMinimumWidth(76)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(self.status_pill)

        self.status_title_label = SubtitleLabel("暂无运行中的任务", card)
        self.status_detail_label = BodyLabel("选择一项任务开始，进度会在这里同步。", card)
        self.status_detail_label.setWordWrap(True)
        self.status_detail_label.setObjectName("bd2MutedText")
        self.status_detail_button = PushButton("打开运行详情", card)
        self.status_detail_button.clicked.connect(self._open_runtime_detail)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        layout.addLayout(heading)
        layout.addWidget(self.status_title_label)
        layout.addWidget(self.status_detail_label)
        layout.addStretch(1)
        layout.addWidget(self.status_detail_button, 0, Qt.AlignmentFlag.AlignRight)
        return card

    def _build_entrances_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("bd2SurfaceCard")

        title = StrongBodyLabel("配置、通知与支持", card)
        self.notification_summary_label = CaptionLabel(self._notification_summary(), card)
        self.notification_summary_label.setObjectName("bd2MutedText")

        text_widget = QWidget(card)
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(title)
        text_layout.addWidget(self.notification_summary_label)

        self.task_config_button = PushButton(FluentIcon.CALENDAR, "任务配置", card)
        self.task_config_button.clicked.connect(self._navigate_to_daily_tasks)
        self.notification_button = PushButton(FluentIcon.RINGER, "系统通知", card)
        self.notification_button.clicked.connect(self._open_notifications)
        self.report_button = PushButton(FluentIcon.FEEDBACK, "生成问题报告", card)
        self.report_button.clicked.connect(self._create_report)
        self.debug_button = PushButton(FluentIcon.DEVELOPER_TOOLS, "设备与调试", card)
        self.debug_button.clicked.connect(self._navigate_to_debug)

        buttons_widget = QWidget(card)
        self.entrance_buttons_layout = QGridLayout(buttons_widget)
        self.entrance_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.entrance_buttons_layout.setHorizontalSpacing(8)
        self.entrance_buttons_layout.setVerticalSpacing(8)
        self.entrance_buttons = (
            self.task_config_button,
            self.notification_button,
            self.report_button,
            self.debug_button,
        )

        self.entrances_layout = QGridLayout(card)
        self.entrances_layout.setContentsMargins(18, 14, 18, 14)
        self.entrances_layout.setHorizontalSpacing(16)
        self.entrances_layout.setVerticalSpacing(10)
        self.entrance_text_widget = text_widget
        self.entrance_buttons_widget = buttons_widget
        return card

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self) -> None:
        wide = self.width() >= WIDE_LAYOUT_THRESHOLD
        summary_columns = 2 if wide else 1
        if summary_columns != self._summary_columns:
            _clear_layout(self.summary_layout)
            _reset_grid_columns(self.summary_layout, 3)
            if wide:
                self.summary_layout.addWidget(self.daily_card, 0, 0, 1, 2)
                self.summary_layout.addWidget(self.status_card, 0, 2, 1, 1)
                self.summary_layout.setColumnStretch(0, 1)
                self.summary_layout.setColumnStretch(1, 1)
                self.summary_layout.setColumnStretch(2, 1)
            else:
                self.summary_layout.addWidget(self.daily_card, 0, 0)
                self.summary_layout.addWidget(self.status_card, 1, 0)
                self.summary_layout.setColumnStretch(0, 1)
            self._summary_columns = summary_columns

        task_columns = 2 if wide else 1
        if task_columns != self._task_columns:
            _clear_layout(self.task_grid)
            _reset_grid_columns(self.task_grid, 2)
            for index, tile in enumerate(self.task_tiles):
                self.task_grid.addWidget(tile, index // task_columns, index % task_columns)
            for column in range(task_columns):
                self.task_grid.setColumnStretch(column, 1)
            self._task_columns = task_columns

        entrance_columns = 4 if wide else 2
        if entrance_columns != self._entrance_columns:
            _clear_layout(self.entrances_layout)
            _clear_layout(self.entrance_buttons_layout)
            _reset_grid_columns(self.entrances_layout, 2)
            _reset_grid_columns(self.entrance_buttons_layout, 4)
            if wide:
                self.entrances_layout.addWidget(self.entrance_text_widget, 0, 0)
                self.entrances_layout.addWidget(self.entrance_buttons_widget, 0, 1)
                self.entrances_layout.setColumnStretch(0, 1)
            else:
                self.entrances_layout.addWidget(self.entrance_text_widget, 0, 0)
                self.entrances_layout.addWidget(self.entrance_buttons_widget, 1, 0)
                self.entrances_layout.setColumnStretch(0, 1)
            for index, button in enumerate(self.entrance_buttons):
                self.entrance_buttons_layout.addWidget(
                    button,
                    index // entrance_columns,
                    index % entrance_columns,
                )
            for column in range(entrance_columns):
                self.entrance_buttons_layout.setColumnStretch(column, 1)
            self._entrance_columns = entrance_columns

    def refresh_runtime(self) -> None:
        executor = self.executor
        paused = bool(getattr(executor, "paused", True)) if executor is not None else True
        current_task = getattr(executor, "current_task", None) if executor is not None else None

        if current_task is None and paused:
            state, title = "准备就绪", "暂无运行中的任务"
            detail = "选择一项任务开始，进度会在这里同步。"
            tone = "ready"
        elif current_task is None:
            state, title = "等待中", "执行器已启动"
            detail = "正在等待可运行的任务或游戏窗口。"
            tone = "running"
        else:
            task_name = str(getattr(current_task, "name", "未命名任务"))
            state = "已暂停" if paused or getattr(current_task, "paused", False) else "运行中"
            title = task_name
            detail = self._task_phase(current_task)
            tone = "paused" if state == "已暂停" else "running"

        self.status_pill.setText(state)
        self.status_title_label.setText(title)
        self.status_detail_label.setText(detail)
        self._set_status_tone(tone)

        daily_running = current_task is self.daily_task and current_task is not None
        if daily_running and state == "已暂停":
            self.daily_start_button.setText("继续一键日常")
            self.daily_start_button.setEnabled(True)
        elif daily_running:
            self.daily_start_button.setText("一键日常运行中")
            self.daily_start_button.setEnabled(False)
        elif current_task is not None and not paused:
            self.daily_start_button.setText("其他任务运行中")
            self.daily_start_button.setEnabled(False)
        else:
            self.daily_start_button.setText("开始一键日常")
            self.daily_start_button.setEnabled(self.daily_task is not None)

        self.daily_config_button.setEnabled(self.daily_task is not None)
        self.notification_summary_label.setText(self._notification_summary())

    def _task_phase(self, task) -> str:
        info = getattr(task, "info", None)
        if isinstance(info, dict):
            for key in ("当前子任务", "当前任务", "状态"):
                value = info.get(key)
                if value and str(value) != "-":
                    return f"{key}：{value}"
        return "任务已经交给现有执行器，详细进度可在任务页查看。"

    def _find_daily_task(self):
        for task in self._visible_onetime_tasks():
            if getattr(task, "name", None) == DAILY_BATCH_NAME:
                return task
        return None

    def _find_independent_tasks(self) -> list:
        return [
            task
            for task in self._visible_onetime_tasks()
            if task is not self.daily_task
            and getattr(task, "group_name", None) == DAILY_GROUP_NAME
        ]

    def _visible_onetime_tasks(self) -> Iterable:
        tasks = getattr(self.executor, "onetime_tasks", ()) if self.executor is not None else ()
        return (task for task in tasks if getattr(task, "visible", True))

    def _daily_summary(self) -> str:
        if self.daily_task is None:
            return "当前未找到一键日常任务，原有任务页仍可正常使用。"
        config = getattr(self.daily_task, "config", {}) or {}
        children = getattr(self.daily_task, "child_tasks", ())
        enabled = sum(bool(config.get(child.config_key, True)) for child in children)
        return f"已选择 {enabled} 项 · 按既有安全顺序执行 · 子任务配置不会被改写"

    def _notification_summary(self) -> str:
        manager = getattr(self.main_window, "notification_manager", None)
        if manager is None:
            return "通知服务尚未就绪；入口仍保留。"
        try:
            system = "Windows 通知已开启" if manager.system_enabled else "Windows 通知已关闭"
            external = "外部渠道已配置" if manager.external_provider_enabled else "外部渠道未配置"
            return f"{system} · {external} · 独立任务完成会沿用现有通知策略"
        except Exception:
            return "通知状态暂不可读；可进入设置页检查。"

    def _find_task_card(self, task):
        tabs = []
        if getattr(self.main_window, "onetime_tab", None) is not None:
            tabs.append(self.main_window.onetime_tab)
        tabs.extend(getattr(self.main_window, "grouped_task_tabs", ()) or ())
        imported = getattr(self.main_window, "imported_tabs", {}) or {}
        tabs.extend(imported.values())
        for tab in tabs:
            for card in getattr(tab, "card_widgets", ()):
                if getattr(card, "task", None) is task:
                    return tab, card
        return None, None

    def _start_task(self, task) -> None:
        if task is None:
            return
        _tab, card = self._find_task_card(task)
        if card is not None and hasattr(card, "start_clicked"):
            card.start_clicked()
        else:
            controller = getattr(getattr(self.main_window, "app", None), "start_controller", None)
            if controller is not None and hasattr(controller, "start"):
                controller.start(task)
        QTimer.singleShot(0, self.refresh_runtime)

    def _open_task_config(self, task) -> None:
        if task is None:
            return
        tab, card = self._find_task_card(task)
        if tab is None or card is None:
            return
        self.main_window.switchTo(tab)
        if hasattr(card, "setExpand"):
            card.setExpand(True)
        if hasattr(tab, "ensureWidgetVisible"):
            QTimer.singleShot(0, lambda: tab.ensureWidgetVisible(card))

    def _navigate_to_daily_tasks(self) -> None:
        tab, _card = self._find_task_card(self.daily_task)
        if tab is not None:
            self.main_window.switchTo(tab)

    def _open_runtime_detail(self) -> None:
        task = getattr(self.executor, "current_task", None) if self.executor is not None else None
        if task is None:
            task = self.daily_task
        self._open_task_config(task)

    def _open_notifications(self) -> None:
        tab = getattr(self.main_window, "notification_tab", None)
        if tab is not None:
            self.main_window.switchTo(tab)

    def _create_report(self) -> None:
        start_tab = getattr(self.main_window, "start_tab", None)
        button = getattr(start_tab, "feedback_report_button", None)
        if button is not None and hasattr(button, "click"):
            button.click()

    def _navigate_to_debug(self) -> None:
        start_tab = getattr(self.main_window, "start_tab", None)
        if start_tab is None:
            return
        self.main_window.switchTo(start_tab)
        target = getattr(start_tab, "debug_widget", None)
        if target is not None and hasattr(start_tab, "ensureWidgetVisible"):
            QTimer.singleShot(0, lambda: start_tab.ensureWidgetVisible(target))

    def _set_status_tone(self, tone: str) -> None:
        colors = {
            "ready": ("#1f7a4d", "rgba(46, 160, 96, 0.14)"),
            "running": ("#1769aa", "rgba(48, 126, 215, 0.16)"),
            "paused": ("#9a6515", "rgba(218, 151, 42, 0.17)"),
        }
        foreground, background = colors[tone]
        self.status_pill.setStyleSheet(
            f"QLabel {{ color: {foreground}; background: {background}; "
            "border-radius: 10px; padding: 3px 9px; font-weight: 600; }}"
        )

    def _apply_theme(self, *_args) -> None:
        if isDarkTheme():
            surface = "rgba(38, 38, 42, 232)"
            surface_border = "rgba(255, 255, 255, 24)"
            hero = "rgba(70, 52, 30, 235)"
            hero_border = "rgba(245, 178, 82, 92)"
            status = "rgba(31, 43, 55, 235)"
            muted = "rgba(255, 255, 255, 156)"
            tile = "rgba(255, 255, 255, 10)"
            tile_border = "rgba(255, 255, 255, 18)"
            marker = "rgba(245, 178, 82, 42)"
        else:
            surface = "rgba(255, 255, 255, 224)"
            surface_border = "rgba(30, 45, 65, 22)"
            hero = "rgba(255, 247, 233, 242)"
            hero_border = "rgba(219, 143, 40, 86)"
            status = "rgba(239, 247, 255, 242)"
            muted = "rgba(38, 47, 58, 168)"
            tile = "rgba(246, 248, 251, 190)"
            tile_border = "rgba(30, 45, 65, 18)"
            marker = "rgba(222, 146, 45, 34)"

        self.setStyleSheet(
            f"""
            QFrame#bd2SurfaceCard {{
                background-color: {surface};
                border: 1px solid {surface_border};
                border-radius: 14px;
            }}
            QFrame#bd2DailyCard {{
                background-color: {hero};
                border: 1px solid {hero_border};
                border-radius: 16px;
            }}
            QFrame#bd2StatusCard {{
                background-color: {status};
                border: 1px solid {surface_border};
                border-radius: 16px;
            }}
            QFrame#bd2TaskTile {{
                background-color: {tile};
                border: 1px solid {tile_border};
                border-radius: 11px;
            }}
            QLabel#bd2TaskMarker, QLabel#bd2DailyMarker {{
                background-color: {marker};
                border: 1px solid {hero_border};
                border-radius: 11px;
                color: #c87816;
                font-weight: 700;
                font-size: 16px;
            }}
            QLabel#bd2Kicker, QLabel#bd2DailyEyebrow {{
                color: #c87816;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#bd2MutedText, QLabel#bd2PreservedHint {{ color: {muted}; }}
            """
        )
        self.refresh_runtime()


def install_home_dashboard(main_window):
    """Install the dashboard without replacing any existing ok-script widgets."""

    start_tab = getattr(main_window, "start_tab", None)
    if start_tab is None:
        return None
    if getattr(start_tab, "_bd2_home_dashboard_installed", False):
        return getattr(start_tab, "home_dashboard", None)

    tab_layout = getattr(start_tab, "vBoxLayout", None)
    if tab_layout is None:
        return None

    dashboard = HomeDashboard(main_window, start_tab.view)
    tab_layout.insertWidget(0, dashboard)
    start_tab.home_dashboard = dashboard
    start_tab._bd2_home_dashboard_installed = True
    _rename_start_navigation(main_window, start_tab, "首页")
    return dashboard


def _rename_start_navigation(main_window, start_tab, label: str) -> None:
    navigation = getattr(main_window, "navigationInterface", None)
    panel = getattr(navigation, "panel", None)
    items = getattr(panel, "items", {}) or {}
    item = items.get(start_tab.objectName())
    widget = getattr(item, "widget", item)
    if widget is not None and hasattr(widget, "setText"):
        widget.setText(label)


def _task_marker(task) -> str:
    name = str(getattr(task, "name", "任")).strip()
    return name[0] if name else "任"


def _clear_layout(layout) -> None:
    while layout.count():
        layout.takeAt(0)


def _reset_grid_columns(layout, count: int) -> None:
    for column in range(count):
        layout.setColumnStretch(column, 0)
