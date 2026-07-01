from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QGridLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FluentIcon, StrongBodyLabel, SubtitleLabel

REFRESH_INTERVAL_MS = 500
MISSING = object()

STATUS_TEXT = {
    "Running": "运行中",
    "Paused": "已暂停",
    "In Queue": "队列中",
    "Not Started": "未启动",
    "Completed": "已完成",
    "Enabled": "已启用",
    "Disabled": "已禁用",
}


class AutoLoginStatusTab(CustomTab):
    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.ROBOT
        self.title_label = SubtitleLabel("任务状态确认")
        self.add_widget(self.title_label)

        self.status_container = QWidget(self.view)
        self.status_layout = QGridLayout(self.status_container)
        self.status_layout.setContentsMargins(0, 8, 0, 0)
        self.status_layout.setHorizontalSpacing(16)
        self.status_layout.setVerticalSpacing(10)
        self.status_layout.setColumnStretch(0, 1)
        self.status_layout.setColumnStretch(1, 3)
        self.add_widget(self.status_container)

        self._row_shape = []
        self._value_labels = []

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh_status)

    @property
    def name(self):
        return "任务状态确认"

    def showEvent(self, event):
        super().showEvent(event)
        if event.type() == QEvent.Show:
            self.refresh_status()
            if not self.timer.isActive():
                self.timer.start()

    def hideEvent(self, event):
        if self.timer.isActive():
            self.timer.stop()
        super().hideEvent(event)

    def refresh_status(self):
        self._render_rows(self._build_rows())

    def _build_rows(self):
        if self.executor is None:
            return [("empty", "empty", "任务执行器未加载", "")]

        tasks = self._tasks_to_show()
        if not tasks:
            return [("empty", "empty", "暂无任务状态", "")]

        rows = []
        for task in tasks:
            task_id = str(id(task))
            rows.append(("section", f"{task_id}:section", self._task_name(task), ""))
            for key, value in self._base_status_rows(task):
                rows.append(("field", f"{task_id}:base:{key}", key, value))

            seen_keys = set()
            for key in self._ordered_status_keys(task):
                seen_keys.add(key)
                rows.append(
                    (
                        "field",
                        f"{task_id}:status:{key}",
                        self._display_key(task, key),
                        self._task_value(task, key),
                    )
                )

            info = getattr(task, "info", {}) or {}
            for key, value in info.items():
                if key in seen_keys:
                    continue
                rows.append(
                    (
                        "field",
                        f"{task_id}:info:{key}",
                        self._display_key(task, key),
                        value,
                    )
                )

        return rows

    def _tasks_to_show(self):
        all_tasks = self._all_tasks()
        queued_tasks = list(getattr(self.executor, "onetime_task_queue", []) or [])
        trigger_tasks = list(getattr(self.executor, "trigger_tasks", []) or [])
        current_task = getattr(self.executor, "current_task", None)
        selected = []

        def add_task(task):
            if task is None or task in selected:
                return
            if not getattr(task, "visible", True):
                return
            selected.append(task)

        add_task(current_task)

        for task in all_tasks:
            if getattr(task, "running", False) or task in queued_tasks:
                add_task(task)

        for task in trigger_tasks:
            if getattr(task, "enabled", False) and self._has_status(task):
                add_task(task)

        if selected:
            return selected

        for task in all_tasks:
            if self._has_status(task):
                add_task(task)
        return selected

    def _all_tasks(self):
        if self.executor is None:
            return []
        get_all_tasks = getattr(self.executor, "get_all_tasks", None)
        if callable(get_all_tasks):
            return list(get_all_tasks() or [])
        return list(getattr(self.executor, "onetime_tasks", []) or []) + list(
            getattr(self.executor, "trigger_tasks", []) or []
        )

    def _has_status(self, task) -> bool:
        return bool(getattr(task, "info", None)) or bool(getattr(task, "status_keys", None))

    def _base_status_rows(self, task):
        status = self._task_status(task)
        rows = [
            ("任务状态", status),
            ("运行中", self._yes_no(getattr(task, "running", False))),
            ("已启用", self._yes_no(getattr(task, "enabled", False))),
        ]
        elapsed = self._elapsed_text(getattr(task, "start_time", 0))
        if elapsed:
            rows.append(("开始后", elapsed))
        return rows

    def _task_status(self, task) -> str:
        get_status = getattr(task, "get_status", None)
        if callable(get_status):
            status = get_status()
        elif getattr(task, "running", False):
            status = "Running"
        elif getattr(task, "enabled", False):
            status = "Enabled"
        else:
            status = "Not Started"
        return STATUS_TEXT.get(status, str(status))

    def _ordered_status_keys(self, task):
        keys = []
        for key in getattr(task, "status_keys", []) or []:
            if key not in keys:
                keys.append(key)
        for key in (getattr(task, "info", {}) or {}).keys():
            if key not in keys:
                keys.append(key)
        return keys

    def _display_key(self, task, key):
        labels = getattr(task, "status_key_labels", {}) or {}
        return labels.get(key, key)

    def _task_value(self, task, key):
        for mapping_name in ("info", "config", "default_config"):
            value = self._mapping_value(getattr(task, mapping_name, None), key)
            if value is not MISSING:
                return value
        return "-"

    @staticmethod
    def _mapping_value(mapping, key):
        if mapping is None:
            return MISSING
        try:
            if key in mapping:
                return mapping[key]
        except (TypeError, AttributeError):
            pass
        get = getattr(mapping, "get", None)
        if callable(get):
            value = get(key, MISSING)
            if value is not MISSING:
                return value
        return MISSING

    @staticmethod
    def _task_name(task) -> str:
        return str(getattr(task, "name", task.__class__.__name__))

    @staticmethod
    def _yes_no(value) -> str:
        return "是" if bool(value) else "否"

    @staticmethod
    def _elapsed_text(start_time) -> str:
        try:
            start_time = float(start_time)
        except (TypeError, ValueError):
            return ""
        if start_time <= 0:
            return ""

        import time

        elapsed = max(0, int(time.time() - start_time))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _render_rows(self, rows):
        shape = [(kind, identity, label) for kind, identity, label, _value in rows]
        if shape != self._row_shape:
            self._clear_status_layout()
            self._value_labels = []
            for row, (kind, _identity, label, value) in enumerate(rows):
                if kind == "section":
                    section_label = StrongBodyLabel(label)
                    self.status_layout.addWidget(section_label, row, 0, 1, 2)
                    self._value_labels.append(None)
                    continue
                if kind == "empty":
                    empty_label = BodyLabel(label)
                    empty_label.setWordWrap(True)
                    self.status_layout.addWidget(empty_label, row, 0, 1, 2)
                    self._value_labels.append(None)
                    continue

                key_label = CaptionLabel(label)
                value_label = BodyLabel(self._format_value(value))
                value_label.setWordWrap(True)
                self.status_layout.addWidget(key_label, row, 0)
                self.status_layout.addWidget(value_label, row, 1)
                self._value_labels.append(value_label)
            self._row_shape = shape
            return

        for index, (_kind, _identity, _label, value) in enumerate(rows):
            value_label = self._value_labels[index]
            if value_label is not None:
                value_label.setText(self._format_value(value))

    def _clear_status_layout(self):
        while self.status_layout.count():
            item = self.status_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _format_value(value) -> str:
        if value is True:
            return "是"
        if value is False:
            return "否"
        if value is None:
            return "-"
        if isinstance(value, list):
            text = ", ".join(map(str, value))
        else:
            text = str(value)
        return text if text else "-"
