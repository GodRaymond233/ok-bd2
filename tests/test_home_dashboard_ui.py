import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.ui.home_dashboard import HomeDashboard, install_home_dashboard


class _Task:
    def __init__(self, name, description="说明", *, batch=False):
        self.name = name
        self.description = description
        self.group_name = "日常/周常"
        self.visible = True
        self.enabled = False
        self.paused = False
        self.running = False
        self.info = {}
        self.child_tasks = (
            tuple(SimpleNamespace(config_key=f"项目{index}") for index in range(3))
            if batch
            else ()
        )
        self.config = {child.config_key: True for child in self.child_tasks}


class _TaskCard:
    def __init__(self, task):
        self.task = task
        self.starts = 0
        self.expanded = False

    def start_clicked(self):
        self.starts += 1

    def setExpand(self, expanded):
        self.expanded = expanded


class _TaskTab:
    def __init__(self, cards):
        self.card_widgets = cards
        self.visible_card = None

    def ensureWidgetVisible(self, card):
        self.visible_card = card


class _NavWidget:
    def __init__(self):
        self.text = "Capture"

    def setText(self, text):
        self.text = text


class _StartTab(QWidget):
    def __init__(self):
        super().__init__()
        self.view = self
        self.vBoxLayout = QVBoxLayout(self)
        self.existing_config = QWidget(self)
        self.debug_widget = QWidget(self)
        self.vBoxLayout.addWidget(self.existing_config)
        self.vBoxLayout.addWidget(self.debug_widget)
        self.report_clicks = 0
        self.feedback_report_button = SimpleNamespace(
            click=lambda: setattr(self, "report_clicks", self.report_clicks + 1)
        )
        self.last_visible_widget = None

    def ensureWidgetVisible(self, widget):
        self.last_visible_widget = widget


class _MainWindow:
    def __init__(self):
        self.daily = _Task("一键完成日常", batch=True)
        self.independent = _Task("快速狩猎")
        self.executor = SimpleNamespace(
            onetime_tasks=[self.daily, self.independent],
            paused=True,
            current_task=None,
        )
        self.daily_card = _TaskCard(self.daily)
        self.independent_card = _TaskCard(self.independent)
        self.task_tab = _TaskTab([self.daily_card, self.independent_card])
        self.grouped_task_tabs = [self.task_tab]
        self.onetime_tab = None
        self.imported_tabs = {}
        self.start_tab = _StartTab()
        self.notification_tab = object()
        self.setting_tab = object()
        self.notification_manager = SimpleNamespace(
            system_enabled=True,
            external_provider_enabled=False,
        )
        self.controller_starts = []
        self.app = SimpleNamespace(
            start_controller=SimpleNamespace(start=self.controller_starts.append)
        )
        self.switched_tabs = []
        nav_widget = _NavWidget()
        self.nav_widget = nav_widget
        nav_item = SimpleNamespace(widget=nav_widget)
        self.navigationInterface = SimpleNamespace(
            panel=SimpleNamespace(items={self.start_tab.objectName(): nav_item})
        )

    def switchTo(self, tab):
        self.switched_tabs.append(tab)


class HomeDashboardUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_installer_is_idempotent_and_keeps_existing_config_and_debug_widgets(self):
        main_window = _MainWindow()
        first = install_home_dashboard(main_window)
        second = install_home_dashboard(main_window)

        self.assertIs(first, second)
        self.assertEqual(0, main_window.start_tab.vBoxLayout.indexOf(first))
        self.assertGreaterEqual(
            main_window.start_tab.vBoxLayout.indexOf(main_window.start_tab.existing_config),
            1,
        )
        self.assertGreaterEqual(
            main_window.start_tab.vBoxLayout.indexOf(main_window.start_tab.debug_widget),
            1,
        )
        self.assertEqual("首页", main_window.nav_widget.text)
        main_window.start_tab.close()

    def test_primary_and_independent_actions_reuse_existing_task_cards(self):
        main_window = _MainWindow()
        dashboard = HomeDashboard(main_window)

        dashboard.daily_start_button.click()
        dashboard.task_tiles[0].run_button.click()

        self.assertEqual(1, main_window.daily_card.starts)
        self.assertEqual(1, main_window.independent_card.starts)
        self.assertEqual([], main_window.controller_starts)
        dashboard.close()
        main_window.start_tab.close()

    def test_config_notification_report_and_debug_entries_keep_existing_targets(self):
        main_window = _MainWindow()
        dashboard = HomeDashboard(main_window)

        dashboard.daily_config_button.click()
        self.app.processEvents()
        self.assertIs(main_window.task_tab, main_window.switched_tabs[-1])
        self.assertTrue(main_window.daily_card.expanded)
        self.assertIs(main_window.daily_card, main_window.task_tab.visible_card)

        dashboard.notification_button.click()
        self.assertIs(main_window.notification_tab, main_window.switched_tabs[-1])

        dashboard.report_button.click()
        self.assertEqual(1, main_window.start_tab.report_clicks)

        dashboard.debug_button.click()
        self.app.processEvents()
        self.assertIs(main_window.start_tab, main_window.switched_tabs[-1])
        self.assertIs(main_window.start_tab.debug_widget, main_window.start_tab.last_visible_widget)
        dashboard.close()
        main_window.start_tab.close()

    def test_status_reads_executor_and_task_info_without_mutating_task_state(self):
        main_window = _MainWindow()
        dashboard = HomeDashboard(main_window)
        original_config = dict(main_window.daily.config)
        main_window.executor.paused = False
        main_window.executor.current_task = main_window.daily
        main_window.daily.enabled = True
        main_window.daily.running = True
        main_window.daily.info = {"当前子任务": "快速狩猎"}

        dashboard.refresh_runtime()

        self.assertEqual("运行中", dashboard.status_pill.text())
        self.assertEqual("一键完成日常", dashboard.status_title_label.text())
        self.assertEqual("当前子任务：快速狩猎", dashboard.status_detail_label.text())
        self.assertFalse(dashboard.daily_start_button.isEnabled())
        self.assertEqual(original_config, main_window.daily.config)
        dashboard.close()
        main_window.start_tab.close()

    def test_layout_reflows_for_wide_and_narrow_windows(self):
        main_window = _MainWindow()
        dashboard = HomeDashboard(main_window)

        dashboard.resize(1000, 700)
        dashboard._reflow()
        self.assertEqual(2, dashboard._summary_columns)
        self.assertEqual(2, dashboard._task_columns)
        self.assertEqual(4, dashboard._entrance_columns)

        dashboard.resize(640, 900)
        dashboard._reflow()
        self.assertEqual(1, dashboard._summary_columns)
        self.assertEqual(1, dashboard._task_columns)
        self.assertEqual(2, dashboard._entrance_columns)
        self.assertEqual(0, dashboard.summary_layout.columnStretch(1))
        self.assertEqual(0, dashboard.task_grid.columnStretch(1))
        self.assertEqual(0, dashboard.entrance_buttons_layout.columnStretch(2))
        dashboard.close()
        main_window.start_tab.close()


if __name__ == "__main__":
    unittest.main()
