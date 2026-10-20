"""Native Fluent editor for conditional quick-hunt plans."""

import importlib
import json
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    SubtitleLabel,
)

from src.tasks.quick_hunt_plan import (
    CONDITIONS,
    MODES,
    PLAN_KEY,
    TARGETS,
    describe_plan,
    load_book,
    preset_plans,
    preview_plan,
    step,
    validate_plan,
)
from src.ui.wrap_layout import WrapLayout

DIALOG_DEFAULT_SIZE = (900, 780)
DIALOG_MIN_SIZE = (560, 440)
RICE_INPUT_MAX = 9999
STEP_INPUT_MAX = 999


def create_flow_layout():
    layout = WrapLayout(spacing=8)
    layout.setContentsMargins(0, 0, 0, 0)
    return layout


def create_label(text, parent=None):
    widget = BodyLabel(text, parent)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.PlainText)
    return widget


def create_combo(options, value, callback):
    widget = ComboBox()
    for key, text in options.items():
        widget.addItem(text, userData=key)
    widget.setCurrentIndex(list(options).index(value))
    widget.currentIndexChanged.connect(lambda _: callback(widget.currentData()))
    return widget


def create_button(text, callback, enabled=True):
    widget = PushButton(text)
    widget.setEnabled(enabled)
    widget.clicked.connect(lambda _checked=False: callback())
    return widget


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()


class HuntPlanDialog(QDialog):
    def __init__(self, config, task=None, parent=None):
        super().__init__(parent)
        self.config, self.task = config, task
        self.setWindowTitle("快速狩猎 · 刷取方案")
        screen = self.screen().availableGeometry()
        self.resize(min(DIALOG_DEFAULT_SIZE[0], screen.width() - 40),
                    min(DIALOG_DEFAULT_SIZE[1], screen.height() - 60))
        self.setMinimumSize(*DIALOG_MIN_SIZE)
        try:
            self.book = load_book(config)
            load_error = ""
        except ValueError as exc:
            self.book = {"version": 1, "active": 0, "plans": preset_plans()[:1]}
            load_error = str(exc) + " 原配置尚未修改，保存后替换为当前方案。"
        self.book = deepcopy(self.book)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("安排你的米饭刷取顺序"))
        layout.addWidget(
            create_label("从上往下匹配，只执行第一条满足条件的规则。狩猎场沿用游戏当前关卡。")
        )
        self._add_plan_controls(layout)
        self._add_rule_area(layout)
        self._add_preview_controls(layout)
        self._add_save_controls(layout, load_error)
        self.render()
        if load_error:
            self.error.setText(load_error)

    def _add_plan_controls(self, layout):
        self.selector = ComboBox()
        self.selector.currentIndexChanged.connect(self.select_plan)
        tools = create_flow_layout()
        tools.addWidget(self.selector)
        tools.addWidget(create_button("复制方案", self.copy_plan))
        tools.addWidget(create_button("新建方案", self.new_plan))
        self.delete_button = create_button("删除方案", self.delete_plan)
        tools.addWidget(self.delete_button)
        self.presets = ComboBox()
        self.presets.addItem("添加预设…")
        self.presets.addItems([p["name"] for p in preset_plans()])
        self.presets.currentIndexChanged.connect(self.add_preset)
        tools.addWidget(self.presets)
        layout.addLayout(tools)
        self.name = LineEdit()
        self.name.setPlaceholderText("方案名称")
        self.name.textEdited.connect(self.rename)
        layout.addWidget(self.name)
        reserve_row = QHBoxLayout()
        reserve_row.addWidget(create_label("保留免费米饭"))
        self.reserve = SpinBox()
        self.reserve.setRange(0, RICE_INPUT_MAX)
        self.reserve.valueChanged.connect(lambda value: self.set_field(self.plan, "reserve", value))
        reserve_row.addWidget(self.reserve)
        reserve_row.addWidget(create_label("单次消耗 6 米饭，不足一次的余量保留。"), 1)
        layout.addLayout(reserve_row)

    def _add_rule_area(self, layout):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.rules_layout = QVBoxLayout(self.content)
        self.rules_layout.setContentsMargins(0, 0, 6, 0)
        self.rules_layout.setAlignment(Qt.AlignTop)
        area.setWidget(self.content)
        layout.addWidget(area, 1)

    def _add_preview_controls(self, layout):
        layout.addWidget(SubtitleLabel("模拟执行预览"))
        simulation = create_flow_layout()
        states = {True: "双倍", False: "非双倍", None: "无法确认"}
        self.gold = create_combo(states, True, lambda _: self.preview())
        self.exp = create_combo(states, False, lambda _: self.preview())
        simulation.addWidget(create_label("金币"))
        simulation.addWidget(self.gold)
        simulation.addWidget(create_label("经验 / 史莱姆"))
        simulation.addWidget(self.exp)
        simulation.addWidget(create_label("免费米饭"))
        self.rice = SpinBox()
        self.rice.setRange(0, RICE_INPUT_MAX)
        self.rice.setValue(90)
        self.rice.valueChanged.connect(lambda _: self.preview())
        simulation.addWidget(self.rice)
        layout.addLayout(simulation)
        self.preview_label = create_label("")
        layout.addWidget(self.preview_label)

    def _add_save_controls(self, layout, load_error):
        self.error = create_label(load_error)
        layout.addWidget(self.error)
        footer = QHBoxLayout()
        footer.addWidget(create_label("仅使用免费米饭；预览不操作游戏。"), 1)
        footer.addWidget(create_button("取消", self.reject))
        self.save_button = PrimaryPushButton("保存并使用此方案")
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

    @property
    def plan(self):
        return self.book["plans"][self.book["active"]]

    def render(self):
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems([p["name"] for p in self.book["plans"]])
        self.selector.setCurrentIndex(self.book["active"])
        self.selector.blockSignals(False)
        self.delete_button.setEnabled(len(self.book["plans"]) > 1)
        self.name.setText(self.plan["name"])
        self.reserve.blockSignals(True)
        self.reserve.setValue(self.plan.get("reserve", 0))
        self.reserve.blockSignals(False)
        clear_layout(self.rules_layout)
        for index, rule in enumerate(self.plan["rules"]):
            self.add_rule_widget(index, rule)
        self.rules_layout.addWidget(create_button("＋ 添加条件规则", self.add_rule))
        self.add_rule_widget(-1, {"steps": self.plan["fallback"]})
        self.preview()

    def select_plan(self, index):
        if index >= 0:
            self.book["active"] = index
            self.render()

    def rename(self, text):
        self.plan["name"] = text
        self.selector.setItemText(self.book["active"], text)
        self.preview()

    def append_plan(self, plan):
        self.book["plans"].append(plan)
        self.book["active"] = len(self.book["plans"]) - 1
        self.render()

    def copy_plan(self):
        plan = deepcopy(self.plan)
        plan["name"] += " · 副本"
        self.append_plan(plan)

    def new_plan(self):
        self.append_plan({"name": "我的刷取方案", "rules": [], "fallback": []})

    def delete_plan(self):
        if len(self.book["plans"]) > 1:
            self.book["plans"].pop(self.book["active"])
            self.book["active"] = 0
            self.render()

    def add_preset(self, index):
        if index > 0:
            self.append_plan(preset_plans()[index - 1])
            self.presets.setCurrentIndex(0)

    def add_rule(self):
        self.plan["rules"].append({"condition": "gold", "steps": [step("gold")]})
        self.render()

    def change_order(self, items, index, delta):
        items[index], items[index + delta] = items[index + delta], items[index]
        self.render()

    def remove(self, items, index):
        items.pop(index)
        self.render()

    def set_field(self, item, key, value, rebuild=False):
        item[key] = value
        self.render() if rebuild else self.preview()

    def append_step(self, steps):
        steps.append(step(mode="count"))
        self.render()

    def add_rule_widget(self, index, rule):
        card = QFrame()
        card.setObjectName("huntRule")
        card.setStyleSheet("QFrame#huntRule {border: 1px solid palette(mid); border-radius: 6px;}")
        box = QVBoxLayout(card)
        box.addLayout(self._build_rule_header(index, rule))
        steps = rule["steps"]
        if not steps:
            box.addWidget(create_label("保留米饭，不刷取。"))
        for step_index in range(len(steps)):
            box.addLayout(self._build_step_row(steps, step_index))
        box.addWidget(create_button("＋ 添加步骤", lambda: self.append_step(steps)))
        self.rules_layout.addWidget(card)

    def _build_rule_header(self, index, rule):
        header = create_flow_layout()
        if index >= 0:
            header.addWidget(create_label(f"规则 {index + 1} · 当"))
            header.addWidget(
                create_combo(
                    CONDITIONS,
                    rule["condition"],
                    lambda value: self.set_field(rule, "condition", value),
                )
            )
            rules = self.plan["rules"]
            header.addWidget(
                create_button("↑", lambda: self.change_order(rules, index, -1), index > 0)
            )
            header.addWidget(
                create_button(
                    "↓", lambda: self.change_order(rules, index, 1), index < len(rules) - 1
                )
            )
            header.addWidget(create_button("删除规则", lambda: self.remove(rules, index)))
        else:
            header.addWidget(create_label("其他情况 · 前面的规则都不满足时"))
        return header

    def _build_step_row(self, steps, index):
        hunt_step = steps[index]
        row = create_flow_layout()
        row.addWidget(create_label(f"{index + 1}."))
        row.addWidget(
            create_combo(
                TARGETS,
                hunt_step["target"],
                lambda value: self.set_field(hunt_step, "target", value),
            )
        )
        row.addWidget(
            create_combo(
                MODES,
                hunt_step["mode"],
                lambda value: self.set_field(hunt_step, "mode", value, True),
            )
        )
        if hunt_step["mode"] != "all":
            count = SpinBox()
            count.setRange(1, STEP_INPUT_MAX)
            count.setValue(hunt_step["count"])
            count.setSuffix(" 次" if hunt_step["mode"] == "count" else " 米饭")
            count.valueChanged.connect(lambda value: self.set_field(hunt_step, "count", value))
            row.addWidget(count)
        row.addWidget(create_button("↑", lambda: self.change_order(steps, index, -1), index > 0))
        row.addWidget(
            create_button("↓", lambda: self.change_order(steps, index, 1), index < len(steps) - 1)
        )
        row.addWidget(create_button("删除步骤", lambda: self.remove(steps, index)))
        return row

    def preview(self):
        try:
            for plan in self.book["plans"]:
                validate_plan(plan)
            self.save_button.setEnabled(True)
            self.error.setText("")
        except ValueError as exc:
            self.error.setText(str(exc))
            self.save_button.setEnabled(False)
            self.preview_label.setText("请先修正方案设置。")
            return
        try:
            self.preview_label.setText(
                preview_plan(
                    self.plan,
                    {"金币": self.gold.currentData(), "经验": self.exp.currentData()},
                    self.rice.value(),
                )
            )
        except ValueError as exc:
            self.preview_label.setText(str(exc))

    def save(self):
        if self.task:
            busy = self.task.running or self.task.enabled
            if not busy:
                from src.tasks.DailyBatchTask import DailyBatchTask

                batch = self.task.executor.get_task_by_class(DailyBatchTask)
                # Batch children run directly, so their own task flags remain idle.
                busy = batch is not None and (batch.running or batch.enabled)
            if busy:
                self.error.setText("请先停止正在运行或排队的快速狩猎及一键日常，再保存方案。")
                return
        try:
            raw = json.dumps(self.book, ensure_ascii=False)
            load_book({PLAN_KEY: raw})
            self.config[PLAN_KEY] = raw
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


class HuntPlanConfigWidget(QWidget):
    def __init__(self, config, task):
        super().__init__()
        self.config, self.task = config, task
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 12, 16, 12)
        row = QHBoxLayout()
        self.title = create_label("")
        row.addWidget(self.title, 1)
        row.addWidget(create_button("编辑刷取方案", self.edit))
        self.layout.addLayout(row)
        self.summary = create_label("")
        self.layout.addWidget(self.summary)
        self.update_value()

    def update_value(self):
        try:
            book = load_book(self.config)
            plan = book["plans"][book["active"]]
            self.title.setText(f"米饭刷取方案 · {plan['name']}")
            self.summary.setText(describe_plan(plan))
        except ValueError as exc:
            self.title.setText("米饭刷取方案需要修正")
            self.summary.setText(str(exc))
        self.updateGeometry()
        parent = self.parentWidget()
        while parent is not None:
            if hasattr(parent, "_adjustViewSize"):
                parent._adjustViewSize()
                break
            parent = parent.parentWidget()

    def edit(self):
        dialog = HuntPlanDialog(self.config, self.task, self.window())
        try:
            if dialog.exec() == QDialog.Accepted:
                self.update_value()
        finally:
            try:
                dialog.deleteLater()
            except RuntimeError:
                # The parent may be destroyed during the modal event loop.
                pass


def install_quick_hunt_plan_editor():
    """Install the structured plan editor for PLAN_KEY once, preserving other config widgets."""
    module = importlib.import_module("ok.ui.qt.tasks.ConfigCard")
    original = module.config_widget
    if getattr(original, "_hunt_plan_editor", False):
        return

    def config_widget(config_type, config_desc, config, key, value, task):
        if key == PLAN_KEY:
            return HuntPlanConfigWidget(config, task)
        return original(config_type, config_desc, config, key, value, task)

    config_widget._hunt_plan_editor = True
    module.config_widget = config_widget
