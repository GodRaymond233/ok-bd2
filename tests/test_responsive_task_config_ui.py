import gc
import os
import unittest
import weakref
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok import og
from ok.ui.qt.tasks.ConfigCard import ConfigCard
from ok.ui.qt.tasks.LabelAndSwitchButton import LabelAndSwitchButton
from ok.ui.qt.tasks.LabelAndTextEdit import LabelAndTextEdit
from ok.ui.qt.tasks.LabelAndWidget import LabelAndWidget
from ok.ui.qt.tasks.TaskCard import TaskCard
from PySide6.QtCore import SIGNAL, QCoreApplication, QEvent, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from qfluentwidgets import FluentIcon, qconfig

from src.tasks.QuickHuntTask import QuickHuntTask
from src.ui.responsive_task_config import ResponsiveFlowWidget, install_responsive_task_config_ui


class _AppStub:
    @staticmethod
    def tr(value):
        return value


class _ConfigStub(dict):
    def __init__(self, values):
        super().__init__(values)
        self.default = dict(values)

    def get_default(self, key):
        return self.default[key]

    def has_user_config(self):
        return True

    def reset_to_default(self):
        self.clear()
        self.update(self.default)


class _TaskStub:
    show_create_shortcut = False


class _TaskCardStub:
    name = "响应式任务"
    description = "用于验证 TaskCard 紧凑标题与展开动画。"
    icon = None
    config_description = {
        "启用测试项": "这是用于验证窄窗口换行的较长任务说明。" * 2,
    }
    config_type = {}
    default_config = {}
    is_custom = False
    instructions = ""
    enabled = False
    paused = False
    running = False
    first_run_alert = ""
    show_create_shortcut = False

    def __init__(self):
        self.config = _ConfigStub({"启用测试项": True})


class ResponsiveTaskConfigUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.original_ok_app = og.app
        og.app = _AppStub()
        install_responsive_task_config_ui()

    @classmethod
    def tearDownClass(cls):
        og.app = cls.original_ok_app

    def test_switch_stays_inside_a_narrow_config_row(self):
        widget = LabelAndSwitchButton(
            {"执行跑商": "前往商人，低价进货并按最高价白名单出售。"},
            _ConfigStub({"执行跑商": True}),
            "执行跑商",
        )
        widget.show()
        widget.resize(220, 100)
        self.app.processEvents()

        self.assertLessEqual(widget.switch_button.geometry().right(), widget.width())
        self.assertLess(widget.minimumSizeHint().width(), widget.width())
        widget.close()

    def test_long_text_edit_shrinks_instead_of_forcing_card_width(self):
        widget = LabelAndTextEdit(
            {"出售白名单": "用逗号、分号或换行追加物品。"},
            _ConfigStub({"出售白名单": "料理名称，" * 200}),
            "出售白名单",
        )
        widget.show()
        widget.resize(420, 160)
        self.app.processEvents()

        self.assertEqual(widget.text_edit.minimumWidth(), 120)
        self.assertGreater(widget.text_edit.maximumWidth(), 10_000)
        self.assertLessEqual(widget.text_edit.geometry().right(), widget.width())
        self.assertLess(widget.minimumSizeHint().width(), 300)
        widget.close()

    def test_multi_selection_reflows_when_width_is_reduced(self):
        widget = ResponsiveFlowWidget()
        buttons = []
        for index in range(5):
            button = QPushButton(f"选项 {index}")
            button.setFixedWidth(100)
            buttons.append(button)
            widget.add_widget(button)

        widget.flow_layout.setGeometry(QRect(0, 0, 230, 200))

        self.assertEqual(buttons[0].geometry().y(), buttons[1].geometry().y())
        self.assertGreater(buttons[2].geometry().y(), buttons[0].geometry().y())
        self.assertGreater(buttons[4].geometry().y(), buttons[2].geometry().y())
        widget.close()

    def test_multi_selection_respects_max_columns_cap(self):
        widget = ResponsiveFlowWidget(alignment=Qt.AlignRight, max_columns=2)
        buttons = []
        for index in range(5):
            button = QPushButton(f"选项 {index}")
            button.setFixedWidth(100)
            buttons.append(button)
            widget.add_widget(button)

        widget.flow_layout.setGeometry(QRect(0, 0, 600, 200))

        self.assertEqual(buttons[0].geometry().y(), buttons[1].geometry().y())
        self.assertGreater(buttons[2].geometry().y(), buttons[0].geometry().y())
        self.assertGreater(buttons[4].geometry().y(), buttons[2].geometry().y())
        widget.close()

    def test_multi_selection_flow_accepts_upstream_alignment_argument(self):
        widget = ResponsiveFlowWidget(alignment=Qt.AlignRight)

        self.assertEqual(Qt.AlignRight, widget.alignment)
        widget.close()

    def test_multi_selection_hidden_items_reflow_and_right_align(self):
        widget = ResponsiveFlowWidget(alignment=Qt.AlignRight)
        buttons = []
        for index in range(4):
            button = QPushButton(f"选项 {index}")
            button.setFixedWidth(100)
            buttons.append(button)
            widget.add_widget(button)

        widget.flow_layout.setGeometry(QRect(0, 0, 230, 200))
        full_height = widget.flow_layout.heightForWidth(230)
        self.assertGreater(buttons[0].geometry().x(), 0)
        self.assertGreater(buttons[2].geometry().y(), buttons[0].geometry().y())

        buttons[1].hide()
        buttons[3].hide()
        widget.flow_layout.setGeometry(QRect(0, 0, 230, 200))
        hidden_height = widget.flow_layout.heightForWidth(230)
        self.assertLess(hidden_height, full_height)
        self.assertEqual(buttons[0].geometry().y(), buttons[2].geometry().y())
        widget.close()

    def test_install_is_idempotent_after_ok_script_1_0_190_import(self):
        original_init = LabelAndWidget.__init__
        original_add_layout = LabelAndWidget.add_layout
        original_flow = __import__(
            "ok.ui.qt.tasks.LabelAndMultiSelection", fromlist=["FlowLayout"]
        ).FlowLayout

        install_responsive_task_config_ui()
        install_responsive_task_config_ui()

        self.assertIs(original_init, LabelAndWidget.__init__)
        self.assertIs(original_add_layout, LabelAndWidget.add_layout)
        self.assertIs(original_flow, ResponsiveFlowWidget)

    def test_config_card_visibility_updates_expanded_height(self):
        values = {f"配置项 {index}": index for index in range(4)}
        descriptions = {
            key: "这是一段较长的配置说明，用于验证隐藏配置项后卡片高度会及时收缩。" * 2
            for key in values
        }
        card = ConfigCard(
            _TaskStub(),
            "可见性配置",
            _ConfigStub(values),
            "隐藏配置项后不应残留空白。",
            values,
            descriptions,
            {},
            FluentIcon.INFO,
        )
        card.resize(420, card.card.height())
        card.show()
        self.app.processEvents()
        card.isExpand = True
        card._adjustViewSize()
        self.app.processEvents()
        expanded_height = card.height()

        card.config_widget_by_key["配置项 0"].setVisible(False)
        card._adjustViewSize()
        self.app.processEvents()
        self.assertLess(card.height(), expanded_height)
        card.close()

    def test_task_card_keeps_compact_header_through_expand_and_collapse(self):
        card = TaskCard(_TaskCardStub(), onetime=False)
        card.show()
        self.app.processEvents()
        self.assertEqual(card.card.height(), 50)
        self.assertEqual(card.height(), 50)

        card.setExpand(True)
        QTest.qWait(300)
        self.app.processEvents()
        self.assertTrue(card.isExpand)
        self.assertGreater(card.height(), card.card.height())

        card.setExpand(False)
        QTest.qWait(300)
        self.app.processEvents()
        self.assertFalse(card.isExpand)
        self.assertEqual(card.height(), card.card.height())
        card.close()

    def test_expanded_config_card_height_tracks_its_current_width(self):
        values = {
            f"配置项 {index}": index % 2 == 0
            for index in range(12)
        }
        descriptions = {
            key: "这是一段较长的配置说明，用于验证窗口宽度变化后的自动换行和高度更新。" * 2
            for key in values
        }
        card = ConfigCard(
            _TaskStub(),
            "响应式配置",
            _ConfigStub(values),
            "测试配置卡片不会在底部残留大段空白。",
            values,
            descriptions,
            {},
            FluentIcon.INFO,
        )
        card.resize(1000, card.card.height())
        card.show()
        self.app.processEvents()
        card.isExpand = True
        card._adjustViewSize()
        self.app.processEvents()

        wide_height = card.height()
        self.assertEqual(
            wide_height,
            card.card.height() + card.viewLayout.heightForWidth(card.view.width()),
        )

        card.resize(420, card.height())
        self.app.processEvents()
        narrow_height = card.height()
        self.assertGreater(narrow_height, wide_height)
        self.assertEqual(
            narrow_height,
            card.card.height() + card.viewLayout.heightForWidth(card.view.width()),
        )

        card.resize(1000, card.height())
        self.app.processEvents()
        self.assertEqual(card.height(), wide_height)
        card.close()

    def test_empty_config_card_keeps_upstream_expansion_guard(self):
        card = ConfigCard(
            _TaskStub(),
            "无配置任务",
            _ConfigStub({}),
            "空配置卡不应响应展开操作。",
            {},
            {},
            {},
            FluentIcon.INFO,
        )
        card.show()
        self.app.processEvents()

        self.assertFalse(card.isExpand)
        self.assertFalse(card._expand_enabled)
        card.setExpand(True)
        self.app.processEvents()
        self.assertFalse(card.isExpand)
        card.close()



    def test_task_card_badge_and_category_styling_are_applied(self):
        stub = _TaskCardStub()
        stub.group_name = "日常/周常"
        card = TaskCard(stub, onetime=False)
        self.assertTrue(getattr(card, "_bd2_badge_installed", False))
        self.assertIsNotNone(getattr(card, "badge_label", None))
        self.assertEqual("日常", card.badge_label.text())
        self.assertIn("QLabel#bd2CategoryBadge", card.badge_label.styleSheet())
        card.close()

    def test_default_task_card_badge_without_group(self):
        card = TaskCard(_TaskCardStub(), onetime=False)
        self.assertEqual("任务", card.badge_label.text())
        card.close()

    def test_batch_daily_task_card_has_special_badge(self):
        stub = _TaskCardStub()
        stub.name = "一键完成日常"
        stub.group_name = "日常/周常"
        card = TaskCard(stub, onetime=False)
        self.assertEqual("日常合辑", card.badge_label.text())
        card.close()

    def test_quick_hunt_task_card_restores_branch_configuration(self):
        # The product chain hides maintenance rows (阈值/等待秒数/测试);
        # install it explicitly so this module is order-independent.
        from src.ui.hide_config_rows import install_hide_config_rows

        install_hide_config_rows()

        task = object.__new__(QuickHuntTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch("src.tasks.QuickHuntTask.BaseBD2Task.__init__", return_value=None):
            QuickHuntTask.__init__(task)

        task.config = _ConfigStub(task.default_config)
        task.is_custom = False
        task.instructions = ""
        task._enabled = False
        task._paused = False
        task.running = False
        task.first_run_alert = ""
        task.show_create_shortcut = False

        card = TaskCard(task, onetime=False)
        card.resize(2200, card.height())
        card.show()
        self.app.processEvents()
        card.setExpand(True)
        QTest.qWait(300)
        self.app.processEvents()

        for key in (
            "启用",
            "快速狩猎冒险航线",
            "快速狩猎狩猎场",
            "快速狩猎圣石洞穴",
            "快速狩猎双倍策略",
            "快速狩猎资源倾向",
            "快速狩猎米饭分配",
        ):
            self.assertIn(key, card.config_widget_by_key)
            self.assertTrue(card.config_widget_by_key[key].isVisibleTo(card))
        self.assertNotIn("快速狩猎章节图", card.config_widget_by_key)
        # Maintenance rows are hidden; their config values stay readable.
        for key in (
            "快速狩猎模板阈值",
            "快速狩猎像素相似度阈值",
            "快速狩猎界面等待秒数",
            "快速狩猎结算等待秒数",
            "快速狩猎入口测试",
            "快速狩猎菜单测试",
            "快速狩猎圣石测试",
            "快速狩猎完整测试",
            "识别成功后等待秒数",
            "主页压暗阈值",
            "主页确认等待秒数",
        ):
            self.assertNotIn(key, card.config_widget_by_key)
        self.assertIn("快速狩猎模板阈值", task.default_config)
        self.assertLess(card.height(), 1500)
        card.close()

    def test_theme_callback_does_not_retain_destroyed_task_card(self):
        theme_signal = SIGNAL("themeChanged(PyObject)")
        receivers_before = qconfig.receivers(theme_signal)
        card = TaskCard(_TaskCardStub(), onetime=False)
        card_ref = weakref.ref(card)
        self.assertEqual(receivers_before + 1, qconfig.receivers(theme_signal))
        card.deleteLater()
        del card

        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        gc.collect()

        self.assertIsNone(card_ref())
        self.assertEqual(receivers_before, qconfig.receivers(theme_signal))
        qconfig.themeChanged.emit(qconfig.theme)


if __name__ == "__main__":
    unittest.main()
