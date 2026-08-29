import types
import unittest
from unittest.mock import Mock, patch

from src.compat import starter_guard
from src.compat.starter_guard import (
    StarterWizardWatcher,
    _is_starter_or_setup_command,
    _patch_execute_preflight,
    _patch_stable_wait,
    get_setup_exe_names,
    get_watcher_exe_names,
    get_wizard_grace_seconds,
    notify_starter_wizard,
    reset_starter_guard_state,
    starter_preflight_warning,
)

STARTER_PATH = r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe"
SETUP_PATH = r"C:\ProgramData\Neowiz\Browndust2Starter\BD2StarterSetup.exe"
GAME_PATH = r"D:\Neowiz\Browndust2\Browndust2_10000001\BrownDust II.exe"


def make_window() -> starter_guard.StarterWindow:
    return starter_guard.StarterWindow(
        pid=4242,
        exe_name="browndust2starter.exe",
        title="BrownDust II",
        width=302,
        height=193,
    )


class StarterWizardWatcherTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.notify = Mock()
        self.watcher = StarterWizardWatcher(
            grace_seconds=30.0,
            find_windows=lambda: [],
            notify=self.notify,
            clock=lambda: self.now[0],
        )

    def poll_with_window(self):
        self.watcher._find_windows = lambda: [make_window()]

    def test_notifies_once_after_grace(self):
        self.poll_with_window()
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.now[0] += 29.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.now[0] += 2.0
        self.watcher.poll_once()
        self.notify.assert_called_once()
        self.assertTrue(self.watcher.notified)

        self.now[0] += 30.0
        self.watcher.poll_once()
        self.notify.assert_called_once()

    def test_no_notify_without_window(self):
        self.watcher.poll_once()
        self.now[0] += 120.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

    def test_grace_clock_resets_when_window_disappears(self):
        self.poll_with_window()
        self.watcher.poll_once()
        self.now[0] += 29.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.watcher._find_windows = lambda: []
        self.watcher.poll_once()
        self.poll_with_window()
        self.now[0] += 2.0
        self.watcher.poll_once()
        self.notify.assert_not_called()

        self.now[0] += 31.0
        self.watcher.poll_once()
        self.notify.assert_called_once()


class StarterGuardConfigTest(unittest.TestCase):
    def test_setup_exe_names_default(self):
        self.assertEqual(get_setup_exe_names(), ["BD2StarterSetup.exe"])

    def test_setup_exe_names_env_list(self):
        env = {"OK_BD2_SETUP_EXE": " OneSetup.exe, 'TwoSetup.exe' "}
        self.assertEqual(get_setup_exe_names(env), ["OneSetup.exe", "TwoSetup.exe"])

    def test_watcher_names_merge_launcher_and_setup(self):
        env = {"OK_BD2_LAUNCHER_EXE": "AltStarter.exe", "OK_BD2_SETUP_EXE": "AltSetup.exe"}
        self.assertEqual(
            get_watcher_exe_names(env),
            {"altstarter.exe", "altsetup.exe"},
        )

    def test_wizard_grace_seconds_default_and_env(self):
        grace_env = {"OK_BD2_STARTER_WIZARD_GRACE_SECONDS": "45"}
        invalid_env = {"OK_BD2_STARTER_WIZARD_GRACE_SECONDS": "abc"}
        floor_env = {"OK_BD2_STARTER_WIZARD_GRACE_SECONDS": "0"}
        self.assertEqual(get_wizard_grace_seconds(), 30.0)
        self.assertEqual(get_wizard_grace_seconds(grace_env), 45.0)
        self.assertEqual(get_wizard_grace_seconds(invalid_env), 30.0)
        self.assertEqual(get_wizard_grace_seconds(floor_env), 1.0)


class StarterPreflightTest(unittest.TestCase):
    def test_warning_when_game_missing(self):
        with patch("src.game_path.resolve_game_exe_path", return_value=""):
            message = starter_preflight_warning()
        self.assertIn("BD2StarterSetup.exe", message)
        self.assertIn("OK_BD2_GAME_PATH", message)

    def test_no_warning_when_game_found(self):
        with patch("src.game_path.resolve_game_exe_path", return_value=GAME_PATH):
            self.assertIsNone(starter_preflight_warning())


class StarterCommandTest(unittest.TestCase):
    def test_accepts_launcher_and_setup_commands(self):
        self.assertTrue(_is_starter_or_setup_command(STARTER_PATH))
        self.assertTrue(_is_starter_or_setup_command(f'"{SETUP_PATH}"'))
        self.assertFalse(_is_starter_or_setup_command(GAME_PATH))
        self.assertFalse(_is_starter_or_setup_command(""))
        self.assertFalse(_is_starter_or_setup_command(None))


class ExecutePreflightPatchTest(unittest.TestCase):
    def setUp(self):
        reset_starter_guard_state()
        self.addCleanup(reset_starter_guard_state)
        self.execute_calls = []

        def inner_execute(game_cmd, arguments=None, start_method="start"):
            self.execute_calls.append((game_cmd, arguments, start_method))
            return True

        self.module = types.SimpleNamespace(execute=inner_execute)
        _patch_execute_preflight(self.module)

    def test_preflight_notifies_once_and_still_launches(self):
        message = "preflight warning"
        with (
            patch.object(starter_guard, "starter_preflight_warning", return_value=message),
            patch.object(starter_guard, "_emit_notification") as emit,
        ):
            self.assertTrue(self.module.execute(STARTER_PATH, start_method="start"))
            self.assertTrue(self.module.execute(STARTER_PATH, start_method="start"))
        emit.assert_called_once_with(message, "BD2 游戏启动前体检")
        self.assertEqual(
            self.execute_calls,
            [(STARTER_PATH, None, "start"), (STARTER_PATH, None, "start")],
        )

    def test_non_starter_command_skips_preflight(self):
        with patch.object(starter_guard, "_emit_notification") as emit:
            self.assertTrue(self.module.execute(GAME_PATH, arguments="--flag"))
        emit.assert_not_called()
        self.assertEqual(self.execute_calls, [(GAME_PATH, "--flag", "start")])

    def test_patch_is_idempotent(self):
        wrapped = self.module.execute
        _patch_execute_preflight(self.module)
        self.assertIs(self.module.execute, wrapped)


class StableWaitPatchTest(unittest.TestCase):
    def setUp(self):
        self.wait_calls = []

        def original_wait(controller):
            self.wait_calls.append(controller)
            return True

        class FakeStartController:
            _wait_until_started_window_stable = original_wait

        self.controller_class = FakeStartController
        self.module = types.SimpleNamespace(StartController=FakeStartController)

    def test_wrapper_runs_wait_inside_watcher_context(self):
        controller = self.controller_class()
        with patch.object(starter_guard, "StarterWizardWatcher") as watcher_cls:
            _patch_stable_wait(self.module)
            self.assertTrue(controller._wait_until_started_window_stable())

        self.assertEqual(self.wait_calls, [controller])
        watcher_cls.assert_called_once()
        watcher_cls.return_value.__enter__.assert_called_once()
        watcher_cls.return_value.__exit__.assert_called_once()

    def test_patch_is_idempotent(self):
        _patch_stable_wait(self.module)
        wrapped = self.controller_class._wait_until_started_window_stable
        _patch_stable_wait(self.module)
        self.assertIs(self.controller_class._wait_until_started_window_stable, wrapped)


class NotifyStarterWizardTest(unittest.TestCase):
    def setUp(self):
        reset_starter_guard_state()
        self.addCleanup(reset_starter_guard_state)

    def test_notify_emits_and_throttles_repeat(self):
        with patch.object(starter_guard, "_emit_notification") as emit:
            notify_starter_wizard([make_window()], 45.0)
            notify_starter_wizard([make_window()], 120.0)
        emit.assert_called_once()

    def test_notify_again_after_reset(self):
        with patch.object(starter_guard, "_emit_notification") as emit:
            notify_starter_wizard([make_window()], 45.0)
            reset_starter_guard_state()
            notify_starter_wizard([make_window()], 45.0)
        self.assertEqual(emit.call_count, 2)


class FindStarterWindowsTest(unittest.TestCase):
    def test_non_windows_returns_empty(self):
        with patch.object(starter_guard.os, "name", "posix"):
            self.assertEqual(starter_guard.find_starter_windows(), [])


if __name__ == "__main__":
    unittest.main()
