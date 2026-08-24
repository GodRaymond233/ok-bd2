import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok import og
from PySide6.QtWidgets import QApplication

from src.tasks.run_history import RunHistoryStore, set_default_store
from src.ui.quest_theme import chip_qss, palette


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
    """Minimal task accepted by the framework TaskCard."""

    show_create_shortcut = False
    is_custom = False
    instructions = ""
    first_run_alert = ""
    icon = None
    group_name = "日常/周常"
    visible = True
    config_description = {}
    config_type = {}
    default_config = {}

    def __init__(self, name="测试任务", config=None, description="描述", info=None):
        self.name = name
        self.description = description
        self.config = config or _ConfigStub({})
        self.enabled = False
        self.paused = False
        self.running = False
        self.info = info or {}
        self.start_time = 0

    def pause(self):
        self.paused = True

    def unpause(self):
        self.paused = False

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False


class _ChildSpec:
    def __init__(self, config_key, task_class):
        self.config_key = config_key
        self.task_class = task_class


def _make_batch_stub():
    from src.tasks.DailyTask import DailyTask
    from src.tasks.PVPTask import PVPTask

    child_keys = ["公会、小屋、酒馆", "自动PVP"]
    task = _TaskStub(
        name="一键完成日常",
        config=_ConfigStub({"启用": True, "公会、小屋、酒馆": True, "自动PVP": True}),
        description="按顺序执行已开启的子任务。",
    )
    task.default_config = {"启用": True, **{key: True for key in child_keys}}
    task.config_type = {"启用": {"sub_configs": {True: child_keys}}}
    task.config_description = {"启用": "总开关", **{key: f"是否执行{key}" for key in child_keys}}
    task.child_tasks = [_ChildSpec("公会、小屋、酒馆", DailyTask), _ChildSpec("自动PVP", PVPTask)]
    return task


class _ExecutorStub:
    def __init__(self, onetime_tasks=(), current_task=None, by_class=None, trigger_tasks=()):
        self.onetime_tasks = list(onetime_tasks)
        self.trigger_tasks = list(trigger_tasks)
        self.current_task = current_task
        self.paused = False
        self._by_class = dict(by_class or {})
        self._by_class.setdefault(type(None), None)
        for task in self.onetime_tasks:
            self._by_class.setdefault(type(task), task)

    def get_task_by_class(self, cls):
        return self._by_class.get(cls)

    def waiting_for_task(self, task):
        return None


def _install_all():
    from src.ui.expand_timing import install_expand_timing
    from src.ui.quest_banner import install_quest_tab_chrome
    from src.ui.quest_cards import install_quest_cards
    from src.ui.responsive_task_config import install_responsive_task_config_ui
    from src.ui.run_panel import install_run_panel

    install_responsive_task_config_ui()
    install_quest_cards()
    install_run_panel()
    install_quest_tab_chrome()
    install_expand_timing()


class QuestUiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._original_app = og.app
        cls._original_executor = getattr(og, "executor", None)
        cls._original_task_manager = getattr(og, "task_manager", None)
        og.app = _AppStub()
        og.executor = _ExecutorStub()
        og.task_manager = SimpleNamespace(imported_scripts={})
        _install_all()
        cls.store_dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        og.app = cls._original_app
        og.executor = cls._original_executor
        og.task_manager = cls._original_task_manager
        set_default_store(None)

    def fresh_store(self):
        store = RunHistoryStore(os.path.join(self.store_dir, self.id().split(".")[-1] + ".json"))
        set_default_store(store)
        return store


class ThemeTokenTest(QuestUiTestBase):
    def test_palettes_share_the_same_keys(self):
        light = palette(dark=False)
        dark = palette(dark=True)
        self.assertEqual(set(light), set(dark))
        for required in ("accent", "ok", "info", "warn", "beta", "card", "line", "ink"):
            self.assertIn(required, light)

    def test_chip_qss_uses_given_colors(self):
        sheet = chip_qss("#107C10", "rgba(16,124,16,0.08)")
        self.assertIn("#107C10", sheet)
        self.assertIn("border-radius", sheet)


class QuestCardTest(QuestUiTestBase):
    def test_card_gets_seal_and_hidden_meta_without_record(self):
        from ok.gui.tasks.TaskCard import TaskCard

        self.fresh_store()
        card = TaskCard(_TaskStub(), onetime=True)
        card.show()
        self.app.processEvents()

        self.assertIsNotNone(card._quest_seal)
        self.assertIsNotNone(card._quest_meta)
        self.assertFalse(card._quest_meta.isVisible())
        self.assertEqual(card.card.height(), 50)
        card.close()

    def test_card_meta_and_header_grow_with_today_record(self):
        import time

        from ok.gui.tasks.TaskCard import TaskCard

        store = self.fresh_store()
        record_task = _TaskStub(name="快速狩猎")
        record_task.start_time = time.time() - 65
        record_task.info = {"状态": "快速狩猎完成。"}
        store.record_task_done(record_task)

        card = TaskCard(_TaskStub(name="快速狩猎"), onetime=True)
        card.show()
        self.app.processEvents()

        from src.ui.quest_cards import refresh_quest_card

        refresh_quest_card(card)
        self.assertTrue(card._quest_meta.isVisible())
        self.assertIn("上次完成", card._quest_meta.text())
        self.assertIn("今天", card._quest_meta.text())
        self.assertEqual(card.card.height(), 68)
        self.assertEqual(card._quest_seal._state, "ok")
        card.close()

    def test_running_task_shows_live_meta_and_run_seal(self):
        from ok.gui.tasks.TaskCard import TaskCard

        from src.ui.quest_cards import refresh_quest_card

        self.fresh_store()
        task = _TaskStub(name="每日跑商")
        task.enabled = True
        task.info = {"当前子任务": "购买", "状态": "进行中"}
        card = TaskCard(task, onetime=True)
        card.show()
        refresh_quest_card(card)

        self.assertEqual(card._quest_seal._state, "run")
        self.assertIn("进行中", card._quest_meta.text())
        self.assertIn("购买", card._quest_meta.text())
        card.close()

    def test_trigger_card_seal_is_ok_when_enabled_not_run(self):
        from ok.gui.tasks.TaskCard import TaskCard

        from src.ui.quest_cards import refresh_quest_card

        self.fresh_store()
        task = _TaskStub(name="自动登录")
        task.enabled = True
        card = TaskCard(task, onetime=False)
        refresh_quest_card(card)

        self.assertEqual(card._quest_seal._state, "ok")
        self.assertFalse(card._quest_meta.text())
        card.close()

    def test_batch_card_child_switches_live_in_expand_view(self):
        from ok.gui.tasks.TaskCard import TaskCard

        self.fresh_store()
        card = TaskCard(_make_batch_stub(), onetime=True)
        card.show()
        self.app.processEvents()

        # No always-visible panel: child switches stay inside the expand view
        # like every other task card's config rows.
        self.assertFalse(hasattr(card, "_quest_sub_panel"))
        for key in ("公会、小屋、酒馆", "自动PVP"):
            widget = card.config_widget_by_key[key]
            self.assertIs(widget.parentWidget(), card.view)
            self.assertGreaterEqual(card.viewLayout.indexOf(widget), 0)
        # Collapsed card is header-only.
        self.assertEqual(card.height(), card.card.height())
        card.close()

    def test_batch_card_expand_and_collapse_reveal_child_switches(self):
        from ok.gui.tasks.TaskCard import TaskCard
        from PySide6.QtTest import QTest

        self.fresh_store()
        card = TaskCard(_make_batch_stub(), onetime=True)
        card.resize(1000, card.height())
        card.show()
        self.app.processEvents()

        collapsed = card.card.height()
        self.assertEqual(card.height(), collapsed)

        card.setExpand(True)
        QTest.qWait(300)
        self.app.processEvents()
        self.assertTrue(card.isExpand)
        self.assertGreater(card.height(), collapsed)
        for key in ("公会、小屋、酒馆", "自动PVP"):
            self.assertFalse(card.config_widget_by_key[key].isHidden())

        card.setExpand(False)
        QTest.qWait(300)
        self.app.processEvents()
        self.assertFalse(card.isExpand)
        # Fixed point: collapse returns to exactly the header height (the
        # anti-oscillation regression for the single-height-writer rule).
        self.assertEqual(card.height(), collapsed)
        card.close()

    def test_expand_and_collapse_animate_progressively(self):
        """Animation is the sole height writer mid-flight (2026-08-18 jank fix).

        Regression for the user-reported "expand has almost no frames, collapse
        flashes": the resize chain used to overwrite the animation's height
        every frame (expand snapped straight to full; collapse oscillated
        between the animation value and the header height).  The heights must
        now progress monotonically through real intermediate values.
        """
        from ok.gui.tasks.TaskCard import TaskCard
        from PySide6.QtTest import QTest

        self.fresh_store()
        card = TaskCard(_make_batch_stub(), onetime=True)
        card.resize(1000, card.height())
        card.show()
        self.app.processEvents()

        def sample(ms=450, step=16):
            heights = []
            elapsed = 0
            while elapsed < ms:
                QTest.qWait(step)
                heights.append(card.height())
                elapsed += step
            return heights

        collapsed = card.height()
        card.setExpand(True)
        expand_heights = sample()
        expanded = card.height()
        self.assertGreater(expanded, collapsed + 100)
        growing = {h for h in expand_heights if collapsed < h < expanded}
        self.assertGreaterEqual(len(growing), 3)  # real intermediate frames
        self.assertEqual(expand_heights, sorted(expand_heights))  # no snap-back

        card.setExpand(False)
        collapse_heights = sample()
        self.assertEqual(card.height(), collapsed)
        shrinking = {h for h in collapse_heights if collapsed < h < expanded}
        self.assertGreaterEqual(len(shrinking), 3)
        self.assertEqual(collapse_heights, sorted(collapse_heights, reverse=True))
        card.close()

    def test_batch_child_switches_follow_master_switch(self):
        from ok.gui.tasks.TaskCard import TaskCard

        self.fresh_store()
        task = _make_batch_stub()
        task.config["启用"] = False
        card = TaskCard(task, onetime=True)
        card.show()
        self.app.processEvents()

        for key in ("公会、小屋、酒馆", "自动PVP"):
            self.assertTrue(card.config_widget_by_key[key].isHidden())

        # Turn the master switch back on through the real switch widget.
        master = card.config_widget_by_key["启用"]
        master.switch_button.setChecked(True)
        self.app.processEvents()
        for key in ("公会、小屋、酒馆", "自动PVP"):
            widget = card.config_widget_by_key[key]
            self.assertFalse(widget.isHidden())
            self.assertIs(widget.parentWidget(), card.view)
        card.close()

    def test_refresh_skips_widget_writes_when_nothing_changed(self):
        from unittest import mock

        from ok.gui.tasks.TaskCard import TaskCard

        from src.ui import quest_cards

        self.fresh_store()
        task = _TaskStub(name="快速狩猎")
        card = TaskCard(task, onetime=True)
        card.show()
        self.app.processEvents()
        quest_cards.refresh_quest_card(card)

        # A quiet 1s tick must not touch chrome (no height-for-width walks,
        # no setFixedHeight, no page relayout).
        with mock.patch.object(quest_cards, "apply_quest_chrome") as chrome:
            quest_cards.refresh_quest_card(card)
            chrome.assert_not_called()

        # A real change (task starts running) must still refresh immediately.
        task.enabled = True
        task.info = {"当前子任务": "购买"}
        with mock.patch.object(
            quest_cards, "apply_quest_chrome", wraps=quest_cards.apply_quest_chrome
        ) as chrome:
            quest_cards.refresh_quest_card(card)
            chrome.assert_called_once()
        self.assertIn("进行中", card._quest_meta.text())
        card.close()


class BannerTest(QuestUiTestBase):
    def test_commission_items_and_banner_counts(self):
        from src.tasks.DailyTask import DailyTask
        from src.tasks.PVPTask import PVPTask
        from src.ui.quest_banner import DailyBoardBanner, commission_items

        store = self.fresh_store()
        batch = _make_batch_stub()
        daily = _TaskStub(name="公会、小屋、酒馆")
        pvp = _TaskStub(name="镜中之战")
        og.executor = _ExecutorStub([batch, daily, pvp], by_class={DailyTask: daily, PVPTask: pvp})
        try:
            items = commission_items(store)
            self.assertEqual([name for name, _ in items], ["公会、小屋、酒馆", "镜中之战"])
            self.assertEqual([done for _, done in items], [False, False])

            record = _TaskStub(name="镜中之战", info={"状态": "ok"})
            store.record_task_done(record)
            items = commission_items(store)
            self.assertEqual([done for _, done in items], [False, True])

            banner = DailyBoardBanner()
            banner.refresh()
            self.assertEqual(banner.ring._done, 1)
            self.assertEqual(banner.ring._total, 2)
            self.assertIn("还剩 1 项", banner.title_label.text())
            self.assertIn("公会、小屋、酒馆", banner.sub_label.text())
            banner.close()
        finally:
            og.executor = _ExecutorStub()

    def test_banner_all_done_text(self):
        from src.tasks.DailyTask import DailyTask
        from src.tasks.PVPTask import PVPTask
        from src.ui.quest_banner import DailyBoardBanner

        store = self.fresh_store()
        batch = _make_batch_stub()
        daily = _TaskStub(name="公会、小屋、酒馆")
        pvp = _TaskStub(name="镜中之战")
        og.executor = _ExecutorStub([batch, daily, pvp], by_class={DailyTask: daily, PVPTask: pvp})
        try:
            for name in ("公会、小屋、酒馆", "镜中之战"):
                store.record_task_done(_TaskStub(name=name, info={"状态": "ok"}))
            banner = DailyBoardBanner()
            banner.refresh()
            self.assertIn("全部完成", banner.title_label.text())
            banner.close()
        finally:
            og.executor = _ExecutorStub()

    def test_banner_excludes_disabled_children(self):
        from src.tasks.DailyTask import DailyTask
        from src.tasks.PVPTask import PVPTask
        from src.ui.quest_banner import commission_items

        store = self.fresh_store()
        batch = _make_batch_stub()
        batch.config["自动PVP"] = False
        daily = _TaskStub(name="公会、小屋、酒馆")
        pvp = _TaskStub(name="镜中之战")
        og.executor = _ExecutorStub(
            [batch, daily, pvp], by_class={DailyTask: daily, PVPTask: pvp}
        )
        try:
            self.assertEqual(
                [name for name, _done in commission_items(store)],
                ["公会、小屋、酒馆"],
            )
        finally:
            og.executor = _ExecutorStub()

    def test_banner_requests_transient_batch_modes(self):
        from src.tasks.DailyTask import DailyTask
        from src.tasks.PVPTask import PVPTask
        from src.ui.quest_banner import DailyBoardBanner

        self.fresh_store()
        batch = _make_batch_stub()
        batch.requested_modes = []
        batch.request_run_mode = batch.requested_modes.append
        daily = _TaskStub(name="公会、小屋、酒馆")
        pvp = _TaskStub(name="镜中之战")
        og.executor = _ExecutorStub(
            [batch, daily, pvp], by_class={DailyTask: daily, PVPTask: pvp}
        )
        starts = []
        original_app = og.app
        og.app = SimpleNamespace(
            tr=lambda value: value,
            start_controller=SimpleNamespace(start=lambda task: starts.append(task)),
        )
        try:
            banner = DailyBoardBanner()
            banner._start_remaining()
            self.assertEqual(batch.requested_modes, ["incomplete"])
            self.assertEqual(starts, [batch])
            banner._start_all()
            self.assertEqual(batch.requested_modes, ["incomplete", "all"])
            banner.close()
        finally:
            og.app = original_app
            og.executor = _ExecutorStub()


class RunPanelTest(QuestUiTestBase):
    def test_running_batch_renders_segments_grid_and_rows(self):
        from src.ui.run_panel import RunPanel

        self.fresh_store()
        task = _make_batch_stub()
        task.enabled = True
        task.start_time = 1000.0
        task.info = {
            "状态": "一键完成日常启动。",
            "当前子任务": "自动PVP",
            "完成": "公会、小屋、酒馆",
            "失败": "-",
            "跳过": "-",
            "Log": "一键完成日常：开始 自动PVP。",
        }
        panel = RunPanel()
        panel.render(task)

        self.assertEqual(panel.pill.text(), "运行中")
        self.assertFalse(panel.pause_button.isHidden())
        self.assertTrue(panel.logs_button.isHidden())
        self.assertIn("完成 1", panel.legend.text())
        self.assertEqual(panel.grid.count(), 2)
        # The name with an internal '、' must stay one grid cell.
        first_cell = panel.grid.itemAt(0).widget()
        self.assertIn("公会、小屋、酒馆", first_cell.text())
        self.assertEqual(panel.rows.count(), 3)  # 状态 / 当前子任务 / 最近日志
        panel.close()

    def test_aborted_batch_shows_fail_state_and_close(self):
        from src.ui.run_panel import RunPanel

        self.fresh_store()
        task = _make_batch_stub()
        task.enabled = False
        task.start_time = 1000.0
        task.info = {
            "状态": "一键完成日常中止。",
            "当前子任务": "-",
            "完成": "公会、小屋、酒馆",
            "失败": "自动PVP",
            "跳过": "-",
            "Log": "一键完成日常：自动PVP 失败，停止后续子任务。",
        }
        panel = RunPanel()
        panel.render(task)

        self.assertEqual(panel.pill.text(), "已中止")
        self.assertTrue(panel.pause_button.isHidden())
        self.assertFalse(panel.logs_button.isHidden())
        self.assertIn("失败 1", panel.legend.text())
        self.assertEqual(panel.rows.count(), 3)  # 状态 / 失败子任务 / 最近日志
        panel.close()

    def test_single_task_hides_batch_sections(self):
        from src.ui.run_panel import RunPanel

        self.fresh_store()
        task = _TaskStub(name="快速狩猎")
        task.enabled = False
        task.info = {"状态": "快速狩猎完成。", "Log": "快速狩猎：狩猎场阶段完成"}
        panel = RunPanel()
        panel.render(task)

        self.assertEqual(panel.pill.text(), "已完成")
        self.assertTrue(panel.segbar.isHidden())
        self.assertTrue(panel.grid_container.isHidden())
        self.assertEqual(panel.rows.count(), 2)  # 状态 / 最近日志
        panel.close()

    def test_run_state_mapping(self):
        from src.ui.run_panel import run_state

        task = _TaskStub()
        self.assertEqual(run_state(task), "done")  # no info -> completed
        task.info = {"状态": "一键完成日常中止。"}
        self.assertEqual(run_state(task), "abort")
        task.info = {"Error": "x"}
        self.assertEqual(run_state(task), "fail")
        task.enabled = True
        self.assertEqual(run_state(task), "run")
        task.paused = True
        self.assertEqual(run_state(task), "pause")

    def test_render_reuses_row_widgets_when_info_unchanged(self):
        from src.ui.run_panel import RunPanel

        self.fresh_store()
        task = _TaskStub(name="快速狩猎")
        task.enabled = False
        task.start_time = 1000.0
        task.info = {"状态": "快速狩猎完成。", "Log": "快速狩猎：完成"}
        panel = RunPanel()
        panel.render(task)
        first_rows = [panel.rows.itemAt(i).widget() for i in range(panel.rows.count())]
        self.assertTrue(first_rows)
        self.assertEqual(panel.pill.text(), "已完成")

        # The 1s timer re-renders a visible done panel forever; with unchanged
        # info the row widgets must survive (no deleteLater/new-widget churn).
        panel.render(task)
        second_rows = [panel.rows.itemAt(i).widget() for i in range(panel.rows.count())]
        self.assertEqual(first_rows, second_rows)
        self.assertEqual(panel.pill.text(), "已完成")

        task.info = {**task.info, "Log": "快速狩猎：新日志"}
        panel.render(task)
        third_rows = [panel.rows.itemAt(i).widget() for i in range(panel.rows.count())]
        self.assertNotEqual(first_rows, third_rows)
        panel.close()


class TaskTabIntegrationTest(QuestUiTestBase):
    def test_daily_group_tab_gets_run_panel_banner_and_status_bar(self):
        from ok.gui.tasks.OneTimeTaskTab import OneTimeTaskTab

        self.fresh_store()
        tab = OneTimeTaskTab(is_standalone=False, group_name="日常/周常")
        self.app.processEvents()

        self.assertTrue(hasattr(tab, "run_panel"))
        self.assertTrue(hasattr(tab, "quest_banner"))
        self.assertTrue(hasattr(tab, "quest_status_bar"))
        self.assertFalse(tab.task_info_container.isVisible())
        self.assertFalse(tab.run_panel.isVisible())

        # A running task in this group surfaces the run panel (the tab itself
        # is not shown in the test, so assert the explicit hidden flag).
        task = _TaskStub(name="每日跑商")
        task.enabled = True
        task.info = {"状态": "进行中"}
        og.executor = _ExecutorStub(onetime_tasks=[], current_task=task)
        try:
            tab.tasks = [task]
            tab.update_info_table()
            self.assertFalse(tab.run_panel.isHidden())
            self.assertEqual(tab.run_panel.pill.text(), "运行中")

            # Closing the panel dismisses this run.
            tab.close_task_info()
            self.assertTrue(tab.run_panel.isHidden())
            tab.update_info_table()
            self.assertTrue(tab.run_panel.isHidden())
        finally:
            og.executor = _ExecutorStub()
            tab.close()

    def test_other_group_tab_has_run_panel_but_no_banner(self):
        from ok.gui.tasks.OneTimeTaskTab import OneTimeTaskTab

        self.fresh_store()
        tab = OneTimeTaskTab(is_standalone=False, group_name="自动刷级")
        self.assertTrue(hasattr(tab, "run_panel"))
        self.assertFalse(hasattr(tab, "quest_banner"))
        tab.close()

    def test_trigger_tab_reuses_run_panel_for_current_trigger(self):
        from ok.gui.tasks.TriggerTaskTab import TriggerTaskTab

        self.fresh_store()
        task = _TaskStub(name="自动登录")
        task.enabled = True
        task.start_time = 1000.0
        task.info = {"状态": "实时触发中"}
        og.executor = _ExecutorStub(current_task=task, trigger_tasks=[task])
        try:
            tab = TriggerTaskTab()
            self.app.processEvents()
            self.assertTrue(hasattr(tab, "run_panel"))
            tab.update_info_table()
            self.assertFalse(tab.run_panel.isHidden())
            self.assertEqual(tab.run_panel.pill.text(), "运行中")
            og.executor.current_task = None
            tab.update_info_table()
            self.assertTrue(tab.run_panel.isHidden())
            tab.close()
        finally:
            og.executor = _ExecutorStub()


class NavSectionsTest(QuestUiTestBase):
    def test_install_inserts_headers_before_anchor_items(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        from qfluentwidgets import NavigationItemPosition

        from src.ui import nav_sections

        class _Item:
            def __init__(self, widget):
                self.widget = widget

        class _Panel:
            def __init__(self):
                self.scroll_widget = QWidget()
                self.scrollLayout = QVBoxLayout(self.scroll_widget)
                self.items = {}
                self.headers = []

            def insertItemHeader(self, index, text, position=None):
                self.assert_position(position)
                self.headers.append((index, text))

            def assert_position(self, position):
                assert position == NavigationItemPosition.SCROLL

        class _StartTab(QWidget):
            pass

        panel = _Panel()
        start_tab = _StartTab()
        start_tab.setObjectName("start")
        for route_key in ("start", "daily", "bd2"):
            widget = QWidget()
            panel.scrollLayout.addWidget(widget)
            panel.items[route_key] = _Item(widget)

        class _Stacked:
            def __init__(self, widgets):
                self._widgets = widgets

            def count(self):
                return len(self._widgets)

            def widget(self, index):
                return self._widgets[index]

        bd2_tab = QWidget()
        bd2_tab.setObjectName("bd2")

        class BD2StatusTab(QWidget):
            pass

        bd2_real = BD2StatusTab()
        bd2_real.setObjectName("bd2")

        class _Window:
            pass

        window = _Window()
        window.navigationInterface = type("NI", (), {"panel": panel})()
        window.start_tab = start_tab
        window.stackedWidget = _Stacked([start_tab, bd2_real])

        self.assertTrue(nav_sections.install_nav_sections(window))
        self.assertEqual(panel.headers[0], (0, "运 行"))
        self.assertEqual(panel.headers[1][1], "诊 断")
        # Second install is a no-op.
        self.assertFalse(nav_sections.install_nav_sections(window))
        self.assertEqual(len(panel.headers), 2)


class HiddenConfigRowsTest(QuestUiTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from src.ui.hide_config_rows import install_hide_config_rows

        install_hide_config_rows()

    def test_token_rows_are_not_created_and_values_survive(self):
        from ok.gui.tasks.TaskCard import TaskCard

        values = {
            "启用": True,
            "跑商等待秒数": 2.5,
            "跑商OCR阈值": 0.9,
            "识别间隔秒数": 1.2,
            "结算基准等待分钟": 3,
            "高亮像素比例": 0.85,
            "关键词最低命中数": 2,
            "单步重试次数": 3,
            "运行模式": "标准",
        }
        task = _TaskStub(name="每日跑商", config=_ConfigStub(values))
        task.default_config = dict(values)
        task.config_description = {key: key for key in values}
        card = TaskCard(task, onetime=True)

        for key in values:
            if key in ("启用", "运行模式"):
                self.assertIn(key, card.config_widget_by_key)
            else:
                self.assertNotIn(key, card.config_widget_by_key)
        self.assertEqual(task.config["跑商等待秒数"], 2.5)
        self.assertEqual(task.config["跑商OCR阈值"], 0.9)
        self.assertEqual(task.config["单步重试次数"], 3)

    def test_sub_config_children_matching_tokens_stay_hidden(self):
        from ok.gui.tasks.TaskCard import TaskCard

        values = {"执行": True, "每步等待秒数": 2, "保留子项": True}
        task = _TaskStub(name="测试合辑", config=_ConfigStub(values))
        task.default_config = dict(values)
        task.config_description = {key: key for key in values}
        task.config_type = {"执行": {"sub_configs": {True: ["每步等待秒数", "保留子项"]}}}
        card = TaskCard(task, onetime=True)

        self.assertIn("执行", card.config_widget_by_key)
        self.assertNotIn("每步等待秒数", card.config_widget_by_key)
        self.assertIn("保留子项", card.config_widget_by_key)
        # The token key was stripped from the rule so it cannot come back
        # through the sub-config recursion (which ignores 'hidden').
        self.assertEqual(task.config_type["执行"]["sub_configs"][True], ["保留子项"])


    def test_string_sub_config_rule_is_also_stripped(self):
        from ok.gui.tasks.TaskCard import TaskCard

        values = {"执行": True, "每步等待秒数": 2}
        task = _TaskStub(name="测试合辑", config=_ConfigStub(values))
        task.default_config = dict(values)
        task.config_description = {key: key for key in values}
        task.config_type = {"执行": {"sub_configs": {True: "每步等待秒数"}}}
        card = TaskCard(task, onetime=True)

        self.assertIn("执行", card.config_widget_by_key)
        self.assertNotIn("每步等待秒数", card.config_widget_by_key)
        self.assertEqual(task.config_type["执行"]["sub_configs"][True], [])

    def test_task_without_matches_is_untouched(self):
        task = _TaskStub(name="广场女神像", config=_ConfigStub({"启用": True}))
        task.default_config = {"启用": True}
        task.config_description = {"启用": "启用"}
        task.config_type = None

        from src.ui.hide_config_rows import mark_hidden_config_keys

        self.assertEqual(mark_hidden_config_keys(task), 0)
        self.assertIsNone(task.config_type)


class ExpandDurationTest(QuestUiTestBase):
    def test_duration_scales_with_content_and_collapses_faster(self):
        from ok.gui.tasks.TaskCard import TaskCard
        from PySide6.QtCore import QEasingCurve

        values = {"启用": True, "运行模式": "标准", "说明文本": "一段说明"}
        task = _TaskStub(name="每日跑商", config=_ConfigStub(values))
        task.default_config = dict(values)
        task.config_description = {key: key for key in values}
        card = TaskCard(task, onetime=True)
        card.show()
        self.app.processEvents()

        card.setExpand(True)
        base = min(420, max(280, int(240 + card.spaceWidget.height() * 0.28)))
        self.assertEqual(card.expandAni.duration(), base)
        self.assertEqual(
            card.expandAni.easingCurve().type(), QEasingCurve.Type.BezierSpline
        )
        self.assertEqual(card.expandAni.endValue(), 0)

        card.setExpand(False)
        self.assertEqual(card.expandAni.duration(), int(base * 0.85))
        # Collapse ends on content_height, not the scrollbar maximum.
        self.assertEqual(card.expandAni.endValue(), card.spaceWidget.height())


class ExpandTimingTest(QuestUiTestBase):
    def setUp(self):
        from src.ui import expand_timing

        expand_timing.set_expand_timing_enabled(True)

    def tearDown(self):
        from src.ui import expand_timing

        expand_timing.set_expand_timing_enabled(True)

    def _make_card(self):
        from ok.gui.tasks.TaskCard import TaskCard

        self.fresh_store()
        card = TaskCard(_make_batch_stub(), onetime=True)
        card.resize(1000, card.height())
        card.show()
        self.app.processEvents()
        return card

    def test_drive_honors_sole_writer_gate_and_cleans_shadows(self):
        """While the driver animates, the sole-writer gates must see the
        animation as running; after it ends every instance shadow is gone."""
        from PySide6.QtCore import QAbstractAnimation
        from PySide6.QtTest import QTest

        from src.ui import expand_timing
        from src.ui.quest_cards import _animation_running

        card = self._make_card()
        collapsed = card.height()
        ani = card.expandAni
        card.setExpand(True)
        QTest.qWait(60)
        mid_gate = _animation_running(card)
        mid_driver = expand_timing._DRIVERS.get(card) is not None
        QTest.qWait(420)

        self.assertTrue(mid_gate, "deferral gates must hold mid-drive")
        self.assertTrue(mid_driver)
        self.assertFalse(_animation_running(card))
        self.assertIsNone(expand_timing._DRIVERS.get(card))
        self.assertNotIn("state", ani.__dict__)
        self.assertNotIn("start", ani.__dict__)
        self.assertEqual(ani.state(), QAbstractAnimation.Stopped)
        self.assertEqual(card.verticalScrollBar().value(), 0)
        self.assertGreater(card.height(), collapsed + 100)
        card.close()

    def test_reversal_mid_drive_lands_opposite_terminal(self):
        from PySide6.QtTest import QTest

        card = self._make_card()
        collapsed = card.height()
        card.setExpand(True)
        QTest.qWait(60)  # mid-drive
        card.setExpand(False)
        QTest.qWait(420)

        self.assertEqual(card.height(), collapsed)
        # The driver must land exactly on the configured end value, which for
        # a collapse is the measured content height.
        self.assertEqual(card.verticalScrollBar().value(), card.expandAni.endValue())
        card.setExpand(True)
        QTest.qWait(420)
        self.assertEqual(card.verticalScrollBar().value(), 0)
        card.close()

    def test_kill_switch_runs_native_animation(self):
        from PySide6.QtCore import QAbstractAnimation
        from PySide6.QtTest import QTest

        from src.ui import expand_timing

        card = self._make_card()
        collapsed = card.height()
        expand_timing.set_expand_timing_enabled(False)
        card.setExpand(True)
        self.assertEqual(card.expandAni.state(), QAbstractAnimation.Running)
        self.assertIsNone(expand_timing._DRIVERS.get(card))
        QTest.qWait(450)
        self.assertGreater(card.height(), collapsed + 100)
        card.close()

    def test_mid_drive_resize_lands_recomputed_terminal(self):
        from PySide6.QtTest import QTest

        from src.ui import expand_timing
        from src.ui.quest_cards import _animation_running, _content_height

        card = self._make_card()
        header = card.card.height()
        card.setExpand(True)
        QTest.qWait(60)  # mid-drive
        card.resize(760, card.height())
        QTest.qWait(60)

        # The geometry change aborts the drive and lands the expand terminal
        # re-measured at the new width, long before the original duration.
        self.assertFalse(_animation_running(card))
        self.assertIsNone(expand_timing._DRIVERS.get(card))
        self.assertEqual(card.verticalScrollBar().value(), 0)
        self.assertEqual(card.height(), header + _content_height(card))

        card.setExpand(False)
        QTest.qWait(60)  # mid-collapse
        card.resize(1000, card.height())
        QTest.qWait(60)

        # Collapse terminal must be the content height re-measured after the
        # resize, not the value captured at toggle time.
        self.assertIsNone(expand_timing._DRIVERS.get(card))
        self.assertEqual(card.height(), header)
        self.assertEqual(card.verticalScrollBar().value(), _content_height(card))
        card.close()

    def test_mid_drive_content_change_lands_recomputed_terminal(self):
        from PySide6.QtTest import QTest

        from src.ui import expand_timing
        from src.ui.quest_cards import _content_height

        card = self._make_card()
        header = card.card.height()
        card.setExpand(True)
        QTest.qWait(60)  # mid-drive
        spacer_before = card.spaceWidget.height()

        # A config row disappearing goes through the framework funnel:
        # re-measure with the cache dropped, spacer updated, drive aborted.
        card.config_widget_by_key["自动PVP"].setVisible(False)
        card._adjustViewSize()
        self.assertLess(card.spaceWidget.height(), spacer_before)
        QTest.qWait(60)

        self.assertIsNone(expand_timing._DRIVERS.get(card))
        self.assertEqual(card.verticalScrollBar().value(), 0)
        self.assertEqual(card.height(), header + _content_height(card))
        card.close()

    def test_adjust_view_size_mid_drive_keeps_animation_sole_writer(self):
        from PySide6.QtTest import QTest

        card = self._make_card()
        card.setExpand(True)
        QTest.qWait(60)  # mid-drive
        mid_height = card.height()
        card._adjustViewSize()

        # A same-geometry re-measure must not write the card height mid-drive;
        # the spacer stays the content funnel for the abort probe.
        self.assertEqual(card.height(), mid_height)
        self.assertLess(mid_height, card.card.height() + card.spaceWidget.height())

        QTest.qWait(420)
        self.assertEqual(card.height(), card.card.height() + card.spaceWidget.height())
        card.close()


if __name__ == "__main__":
    unittest.main()
