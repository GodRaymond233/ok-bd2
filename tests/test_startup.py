import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.compat import startup


class CrashLogRotationTest(unittest.TestCase):
    def test_open_crash_log_keeps_current_and_bounded_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            for index in range(5):
                path = log_dir / f"crash-old-{index}.log"
                path.write_text(str(index), encoding="utf-8")
                path.touch()

            current_time = datetime.datetime(2026, 9, 25, 20, 0, 0)
            crash_log = startup.open_crash_log(
                log_dir,
                max_logs=3,
                now=current_time,
            )
            current_path = log_dir / "crash-20260925-200000.log"
            crash_log.write("current\n")
            crash_log.close()

            logs = sorted(log_dir.glob("crash-*.log"))
            self.assertEqual(3, len(logs))
            self.assertIn(current_path, logs)
            self.assertEqual("current\n", current_path.read_text(encoding="utf-8"))


class StartApplicationTest(unittest.TestCase):

    def guarded_startup(
        self, events, *, acquired=True, legacy=None, argv=("main.py", "--headless")
    ):
        """Patch the executor guard so tests never take a real session lock."""
        legacy_guard = SimpleNamespace(blocking_message=lambda force=False: legacy)
        holder = None if acquired else SimpleNamespace(describe=lambda: "进程 7（qt）")
        directory = self.enterContext(tempfile.TemporaryDirectory())
        patches = [
            patch.object(startup, "SCHEDULED_FAILURE_DIR", Path(directory) / "failures"),
            patch.object(sys, "argv", list(argv)),
            patch.object(startup, "open_crash_log"),
            patch.object(startup.faulthandler, "enable"),
            patch.object(startup.faulthandler, "disable"),
            patch("src.executor_guard.mark_current_process"),
            patch(
                "src.executor_guard.acquire_executor_lock",
                side_effect=lambda mode: events.append(("lock", mode)) or (acquired, holder),
            ),
            patch(
                "src.executor_guard.install_executor_guards",
                side_effect=lambda: events.append("guards"),
            ),
            patch("src.executor_guard.legacy_guard", return_value=legacy_guard),
            patch(
                "src.executor_guard.show_refusal_dialog",
                side_effect=lambda message: events.append(("dialog", message)),
            ),
            patch(
                "src.compat.dependency_guard.ensure_core_dependencies",
                side_effect=lambda: events.append("dependencies"),
            ),
        ]
        for item in patches:
            self.enterContext(item)

    def fake_ok(self, events):
        class FakeOk:
            def __init__(self, config):
                events.append(("ok_init", dict(config)))
                task = startup.parse_entry_arguments().task
                self.args = {"task": int(task) if task and task.isdigit() else task,
                             "exit": "-e" in sys.argv}

            def start(self):
                events.append("start")

            def start_mcp_server(self):
                events.append("mcp")

            def run_task(self, task, exit_after=False):
                events.append(("run_task", task, exit_after))

            def quit(self):
                events.append("quit")

        return SimpleNamespace(OK=FakeOk)

    def test_start_application_takes_lock_then_checks_dependencies_before_config(self):
        events = []
        argv = ("main.py", "-t", "1", "-e")
        self.guarded_startup(events, argv=argv)

        def load_config():
            events.append("load_config")
            return {"debug": False}

        def configure(config):
            events.append(("configure", dict(config)))

        with patch.dict(sys.modules, {"ok": self.fake_ok(events)}):
            startup.start_application(load_config, configure)
        self.assertEqual(list(argv), sys.argv)

        self.assertEqual(
            [
                ("lock", "headless"),
                "dependencies",
                "load_config",
                ("configure", {"debug": False}),
                "guards",
                ("ok_init", {"debug": False, "use_gui": False, "check_mutex": False}),
                "mcp",
                ("run_task", 1, True),
                "quit",
            ],
            events,
        )
        startup.faulthandler.disable.assert_called_once_with()
        startup.open_crash_log.return_value.close.assert_called_once_with()

    def test_refused_lock_stops_before_dependencies_and_runtime(self):
        for argv, mode, dialog in (
            (("main.py", "--headless", "-t", "1"), "headless", False),
            (("main.py", "-t", "1", "-e"), "headless", True),
        ):
            with self.subTest(mode=mode):
                events = []
                self.guarded_startup(events, acquired=False, argv=argv)
                with (
                    patch.dict(sys.modules, {"ok": self.fake_ok(events)}),
                    self.assertRaises(SystemExit) as raised,
                ):
                    startup.start_application(lambda: {"debug": False})
                self.assertEqual(raised.exception.code, 11)
                self.assertEqual(events[0], ("lock", mode))
                self.assertNotIn("dependencies", events)
                kinds = [event[0] for event in events if isinstance(event, tuple)]
                self.assertNotIn("ok_init", kinds)
                self.assertEqual("dialog" in kinds, dialog)
                records = startup.scheduled_start_failures()
                self.assertEqual(len(records), 1)
                self.assertFalse(records[0]["ok"])
                self.assertEqual(records[0]["details"]["退出码"], "11")

    def test_scheduled_start_failures_are_recorded_and_runtime_is_closed(self):
        for failure in ("legacy", "config", "run"):
            with self.subTest(failure=failure):
                events = []
                self.guarded_startup(
                    events, legacy="旧版客户端仍在运行" if failure == "legacy" else None,
                    argv=("main.py", "-t", "1", "-e"),
                )
                fake = self.fake_ok(events)
                prepare = Mock(
                    side_effect=ValueError("配置恢复失败") if failure == "config" else None)
                if failure == "run":
                    fake.OK.run_task = Mock(side_effect=RuntimeError("Start task failed"))
                with (
                    patch.dict(sys.modules, {"ok": fake}),
                    patch("src.desktop.backups.prepare_installed_configs", prepare),
                    self.assertRaises(SystemExit) as raised,
                ):
                    startup.start_application(lambda: {})
                self.assertEqual(raised.exception.code, 12)
                self.assertEqual("quit" in events, failure == "run")
                records = startup.scheduled_start_failures()
                self.assertEqual(len(records), 1)
                self.assertFalse(records[0]["ok"])
                self.assertIn("失败", records[0]["status"])
                self.assertTrue(any(
                    isinstance(event, tuple) and event[0] == "dialog" for event in events))

    def test_graphical_entrypoints_launch_electron_without_loading_the_core(self):
        for script, configure, expected in (
            ("main.py", None, []),
            ("main_debug.py", Mock(), ["--debug"]),
        ):
            with self.subTest(script=script):
                load_config = Mock()
                with (
                    patch.object(sys, "argv", [script]),
                    patch("src.desktop.bootstrap.main", return_value=10) as desktop,
                    patch("src.executor_guard.acquire_executor_lock") as lock,
                    self.assertRaises(SystemExit) as raised,
                ):
                    startup.start_application(load_config, configure)
                self.assertEqual(raised.exception.code, 10)
                desktop.assert_called_once_with(expected)
                load_config.assert_not_called()
                lock.assert_not_called()

    def test_service_flag_selects_the_desktop_core_without_qt(self):
        events = []
        self.guarded_startup(events, argv=("main.py", "--service"))
        run_service = Mock(return_value=0)
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("src.desktop.service.run_service", run_service),
        ):
            startup.start_application(lambda: {})
            self.assertEqual(os.environ["OK_BD2_UI"], "service")
        run_service.assert_called_once()
        self.assertNotIn("dependencies", events)  # the service checks after its handshake

    def test_entry_arguments_and_modes(self):
        parsed = startup.parse_entry_arguments(["--service", "-t", "2"])
        self.assertTrue(parsed.service)
        self.assertEqual(startup.entry_mode(parsed, None), "service")
        parsed = startup.parse_entry_arguments(["-h", "-t", "1", "-e"])
        self.assertEqual(startup.entry_mode(parsed, None), "headless")
        for task in ("0", "1", "src.tasks.DailyBatchTask.DailyBatchTask"):
            parsed = startup.parse_entry_arguments(["-t", task, "-e"])
            self.assertEqual(startup.entry_mode(parsed, None), "headless")
            self.assertEqual(parsed.task, task)
        parsed = startup.parse_entry_arguments([])
        self.assertEqual(startup.entry_mode(parsed, None), "desktop")
        self.assertEqual(startup.entry_mode(parsed, lambda config: None), "desktop")


if __name__ == "__main__":
    unittest.main()
