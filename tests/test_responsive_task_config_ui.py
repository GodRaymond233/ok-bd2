import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok import og
from ok.gui.tasks.ConfigCard import ConfigCard
from ok.gui.tasks.LabelAndSwitchButton import LabelAndSwitchButton
from ok.gui.tasks.LabelAndTextEdit import LabelAndTextEdit
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QPushButton
from qfluentwidgets import FluentIcon

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


if __name__ == "__main__":
    unittest.main()
