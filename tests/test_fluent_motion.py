import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

from src.ui import fluent_motion
from src.ui.fluent_motion import (
    PAGE_IN_MS,
    SELECTION_IN_MS,
    STAGGER_IN_MS,
    STAGGER_MAX_STEPS,
    STAGGER_STEP_MS,
    _install_first_show_hook,
    _maybe_stagger,
    _SlidingSelection,
    install_fluent_page_transition,
    install_start_list_motion,
    set_fluent_motion_enabled,
)


class _MotionTestCase(unittest.TestCase):
    def setUp(self):
        _MotionTestCase.app = QApplication.instance() or QApplication([])
        set_fluent_motion_enabled(True)

    def tearDown(self):
        # Land anything still in flight and restore the default for the
        # next test module.
        set_fluent_motion_enabled(False)
        set_fluent_motion_enabled(True)


class _FakeMainWindow:
    """Weak-referenceable stand-in for the FluentWindow."""

    def __init__(self, stack, host):
        self.stackedWidget = stack
        self._host = host

    def isVisible(self):
        return self._host.isVisible()


class PageTransitionTest(_MotionTestCase):
    def _build(self):
        from qfluentwidgets.window.stacked_widget import StackedWidget

        self.host = QWidget()
        layout = QVBoxLayout(self.host)
        self.stack = StackedWidget()
        # The framework disables the built-in pop animation (MainWindow.py:56);
        # switches then go through setCurrentWidget(popOut=False) like
        # FluentWindowBase.switchTo does.
        self.stack.setAnimationEnabled(False)
        layout.addWidget(self.stack)
        self.page_a = QWidget()
        self.page_b = QWidget()
        self.stack.addWidget(self.page_a)
        self.stack.addWidget(self.page_b)
        self.host.resize(800, 600)
        self.main_window = _FakeMainWindow(self.stack, self.host)
        self.assertTrue(install_fluent_page_transition(self.main_window))

    def test_switch_transitions_incoming_page(self):
        self._build()
        self.host.show()
        QTest.qWait(20)

        self.stack.setCurrentWidget(self.page_b, popOut=False)

        # In flight: the incoming page rises from below at partial opacity;
        # the previous page is hidden by the switch itself — no overlay and
        # no double exposure window exists at all.
        self.assertIsNotNone(self.page_b.graphicsEffect())
        self.assertGreater(self.page_b.pos().y(), 0)
        self.assertFalse(self.page_a.isVisible())
        self.assertIsNone(self.page_a.graphicsEffect())
        # The entrance stagger on the tab yields to the switch transition.
        self.assertTrue(self.page_b.property("_fluent_transitioned"))

        QTest.qWait(PAGE_IN_MS + 150)

        # Steady state is exactly the pre-motion world: no effect, home
        # position, previous page hidden.
        self.assertIsNone(self.page_b.graphicsEffect())
        self.assertEqual(self.page_b.pos(), QPoint(0, 0))
        self.assertFalse(self.page_a.isVisible())

    def test_kill_switch_makes_switches_instant(self):
        self._build()
        self.host.show()
        set_fluent_motion_enabled(False)

        self.stack.setCurrentWidget(self.page_b, popOut=False)

        self.assertIsNone(self.page_b.graphicsEffect())
        self.assertEqual(self.page_b.pos(), QPoint(0, 0))
        self.assertFalse(self.page_a.isVisible())

    def test_kill_switch_mid_flight_lands_clean(self):
        self._build()
        self.host.show()
        self.stack.setCurrentWidget(self.page_b, popOut=False)
        self.assertIsNotNone(self.page_b.graphicsEffect())

        set_fluent_motion_enabled(False)
        QTest.qWait(30)

        self.assertIsNone(self.page_b.graphicsEffect())
        self.assertEqual(self.page_b.pos(), QPoint(0, 0))
        self.assertIsNone(self.page_a.graphicsEffect())
        self.assertFalse(self.page_a.isVisible())

    def test_rapid_second_switch_releases_the_first(self):
        self._build()
        self.host.show()
        QTest.qWait(20)

        self.stack.setCurrentWidget(self.page_b, popOut=False)
        self.stack.setCurrentWidget(self.page_a, popOut=False)

        QTest.qWait(PAGE_IN_MS + 150)

        self.assertIsNone(self.page_a.graphicsEffect())
        self.assertEqual(self.page_a.pos(), QPoint(0, 0))
        self.assertFalse(self.page_b.isVisible())
        self.assertIsNone(self.page_b.graphicsEffect())

    def test_switch_while_window_hidden_stays_instant(self):
        self._build()
        # The host is never shown: startup switches must not animate.
        self.stack.setCurrentWidget(self.page_b, popOut=False)
        self.assertIsNone(self.page_b.graphicsEffect())
        self.assertEqual(self.page_b.pos(), QPoint(0, 0))

    def test_pending_entrance_tab_skips_page_transition(self):
        self._build()
        self.host.show()
        QTest.qWait(20)

        # A tab armed for its first-show content stagger gets that stagger
        # INSTEAD of the whole-page rise: no transition, no marker.
        self.page_b.setProperty("_fluent_entrance_pending", True)
        self.stack.setCurrentWidget(self.page_b, popOut=False)

        self.assertIsNone(self.page_b.graphicsEffect())
        self.assertEqual(self.page_b.pos(), QPoint(0, 0))
        self.assertFalse(self.page_b.property("_fluent_transitioned"))

    def test_install_is_idempotent(self):
        self._build()
        self.assertFalse(install_fluent_page_transition(self.main_window))

    def test_env_disables_parsing(self):
        original = os.environ.get("OK_BD2_FLUENT_MOTION")
        try:
            for value, expected in (
                ("0", True),
                ("off", True),
                (" false ", True),
                ("", False),
                ("1", False),
                ("garbage", False),
            ):
                os.environ["OK_BD2_FLUENT_MOTION"] = value
                self.assertEqual(fluent_motion._env_disables(), expected, value)
        finally:
            if original is None:
                os.environ.pop("OK_BD2_FLUENT_MOTION", None)
            else:
                os.environ["OK_BD2_FLUENT_MOTION"] = original


class StaggerTest(_MotionTestCase):
    def _build_tab(self):
        self.tab = QWidget()
        layout = QVBoxLayout(self.tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)
        self.banner = QFrame()
        self.banner.setFixedSize(600, 60)
        layout.addWidget(self.banner)
        self.cards = []
        for index in range(3):
            card = QFrame()
            card.setFixedSize(600, 50)
            layout.addWidget(card)
            self.cards.append(card)
        self.footer = QFrame()
        self.footer.setFixedSize(600, 20)
        layout.addWidget(self.footer)

        self.tab.quest_banner = self.banner
        self.tab.card_widgets = list(self.cards)
        self.tab.quest_status_bar = self.footer
        self.tab.resize(640, 400)
        self.items = [self.banner, *self.cards, self.footer]

    def _wait_stagger_done(self):
        QTest.qWait(STAGGER_MAX_STEPS * STAGGER_STEP_MS + STAGGER_IN_MS + 150)

    def test_first_show_staggers_and_restores(self):
        self._build_tab()
        self.tab.show()
        QTest.qWait(20)
        positions = [widget.pos() for widget in self.items]

        _maybe_stagger(self.tab)

        self.assertTrue(all(widget.graphicsEffect() is not None for widget in self.items))
        moved = [widget.pos().y() != positions[i].y() for i, widget in enumerate(self.items)]
        self.assertTrue(all(moved))

        self._wait_stagger_done()

        self.assertTrue(all(widget.graphicsEffect() is None for widget in self.items))
        self.assertEqual([widget.pos() for widget in self.items], positions)

    def test_stagger_runs_banner_before_footer(self):
        self._build_tab()
        self.tab.show()
        QTest.qWait(20)

        _maybe_stagger(self.tab)
        QTest.qWait(STAGGER_STEP_MS * 3)

        banner_opacity = self.banner.graphicsEffect().opacity()
        footer_opacity = self.footer.graphicsEffect().opacity()
        self.assertGreater(banner_opacity, footer_opacity)

        self._wait_stagger_done()

    def test_stagger_skipped_when_a_switch_transition_covered_the_tab(self):
        self._build_tab()
        self.tab.show()
        QTest.qWait(20)

        self.tab.setProperty("_fluent_transitioned", True)
        _maybe_stagger(self.tab)
        self.assertTrue(all(widget.graphicsEffect() is None for widget in self.items))
        # The marker is consumed, so a later first-show could still stagger.
        self.assertFalse(self.tab.property("_fluent_transitioned"))

        _maybe_stagger(self.tab)
        self.assertTrue(all(widget.graphicsEffect() is not None for widget in self.items))
        self._wait_stagger_done()
        self.assertTrue(all(widget.graphicsEffect() is None for widget in self.items))

    def test_resize_during_stagger_aborts_to_layout_positions(self):
        self._build_tab()
        self.tab.show()
        QTest.qWait(20)

        _maybe_stagger(self.tab)
        QTest.qWait(60)
        self.tab.resize(420, 400)
        QTest.qWait(60)

        self.assertTrue(all(widget.graphicsEffect() is None for widget in self.items))
        settled = [widget.pos() for widget in self.items]
        QTest.qWait(150)
        # Animations are gone; nothing moves after the abort.
        self.assertEqual([widget.pos() for widget in self.items], settled)

    def test_kill_switch_mid_stagger_restores_positions(self):
        self._build_tab()
        self.tab.show()
        QTest.qWait(20)
        positions = [widget.pos() for widget in self.items]

        _maybe_stagger(self.tab)
        QTest.qWait(80)
        set_fluent_motion_enabled(False)

        # The kill switch lands every item exactly on its layout position.
        self.assertEqual([widget.pos() for widget in self.items], positions)
        self.assertTrue(all(widget.graphicsEffect() is None for widget in self.items))

    def test_layout_change_without_resize_aborts_stagger(self):
        self._build_tab()
        self.tab.show()
        QTest.qWait(20)

        _maybe_stagger(self.tab)
        QTest.qWait(60)
        intruder = QFrame()
        intruder.setFixedSize(600, 30)
        self.tab.layout().addWidget(intruder)
        QTest.qWait(60)

        # A move-only re-layout (nothing resized) still aborts the run.
        self.assertTrue(all(widget.graphicsEffect() is None for widget in self.items))
        QTest.qWait(150)

    def test_first_show_hook_drives_the_stagger(self):
        self._build_tab()
        _install_first_show_hook(self.tab)
        # Second call must not stack a second filter.
        _install_first_show_hook(self.tab)

        self.tab.show()
        self._wait_stagger_done()

        self.assertTrue(all(widget.graphicsEffect() is None for widget in self.items))
        self.assertEqual(self.banner.pos().y(), 0)


class SlidingSelectionTest(_MotionTestCase):
    def _bar_y_for(self, row: int) -> int:
        rect = self.view.visualItemRect(self.view.item(row))
        return rect.y() + round(0.257 * rect.height())

    def _build_list(self):
        from qfluentwidgets import ListWidget

        self.view = ListWidget()
        for index in range(30):
            self.view.addItem(f"row {index}")
        self.view.resize(220, 300)
        self.view.show()
        QTest.qWait(30)

    def test_indicator_slides_between_rows(self):
        self._build_list()
        controller = _SlidingSelection(self.view)

        self.view.setCurrentRow(0)
        QTest.qWait(30)
        self.assertTrue(controller._bar.isVisible())
        self.assertEqual(controller._bar.y(), self._bar_y_for(0))
        self.assertIn("_drawIndicator", vars(self.view.delegate))

        self.view.setCurrentRow(5)
        QTest.qWait(SELECTION_IN_MS // 2)
        self.assertNotEqual(controller._bar.y(), self._bar_y_for(5))

        QTest.qWait(SELECTION_IN_MS + 60)
        self.assertEqual(controller._bar.y(), self._bar_y_for(5))

    def test_scroll_snaps_indicator_to_content(self):
        self._build_list()
        controller = _SlidingSelection(self.view)
        self.view.setCurrentRow(0)
        QTest.qWait(SELECTION_IN_MS + 60)
        self.assertEqual(controller._bar.y(), self._bar_y_for(0))

        self.view.verticalScrollBar().setValue(120)
        QTest.qWait(10)
        self.assertEqual(controller._bar.y(), self._bar_y_for(0))

    def test_kill_switch_restores_the_native_indicator(self):
        self._build_list()
        controller = _SlidingSelection(self.view)
        self.view.setCurrentRow(1)
        QTest.qWait(SELECTION_IN_MS + 60)

        set_fluent_motion_enabled(False)
        self.assertNotIn("_drawIndicator", vars(self.view.delegate))
        self.assertFalse(controller._bar.isVisible())

        set_fluent_motion_enabled(True)
        self.assertIn("_drawIndicator", vars(self.view.delegate))
        self.assertTrue(controller._bar.isVisible())
        self.assertEqual(controller._bar.y(), self._bar_y_for(1))

    def test_install_start_list_motion_covers_the_three_lists(self):
        from types import SimpleNamespace

        from qfluentwidgets import ListWidget

        tab = SimpleNamespace(
            device_list=ListWidget(),
            capture_list=ListWidget(),
            interaction_list=None,
        )
        self.assertTrue(install_start_list_motion(tab))
        self.assertFalse(install_start_list_motion(tab))


class RefreshDriverTest(_MotionTestCase):
    def test_driver_samples_monotonic_progress_and_lands_at_one(self):
        widget = QWidget()
        samples: list[float] = []
        finished: list[bool] = []
        driver = fluent_motion._RefreshDriver(
            widget, 120, samples.append, lambda: finished.append(True)
        )

        driver.start()
        # The first sample lands immediately, before any timer tick.
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0], 0.0)

        QTest.qWait(260)
        self.assertEqual(finished, [True])
        self.assertEqual(samples[-1], 1.0)
        self.assertEqual(samples, sorted(samples))
        self.assertFalse(driver.running)


if __name__ == "__main__":
    unittest.main()
