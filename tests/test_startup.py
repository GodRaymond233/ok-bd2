import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    def test_entrypoints_use_shared_startup(self):
        root = Path(__file__).resolve().parents[1]
        for entrypoint in ("main.py", "main_debug.py"):
            with self.subTest(entrypoint=entrypoint):
                source = (root / entrypoint).read_text(encoding="utf-8")
                self.assertIn(
                    "from src.compat.startup import start_application",
                    source,
                )
                self.assertIn("start_application(", source)

    def test_start_application_checks_dependencies_before_loading_config(self):
        events = []

        class FakeOk:
            def __init__(self, config):
                events.append(("ok_init", config))

            def start(self):
                events.append("start")

        fake_ok = SimpleNamespace(OK=FakeOk)

        def load_config():
            events.append("load_config")
            return {"debug": False}

        def configure(config):
            events.append(("configure", config))

        with (
            patch.object(startup, "open_crash_log") as open_log,
            patch.object(startup.faulthandler, "enable") as enable,
            patch.object(startup.faulthandler, "disable") as disable,
            patch(
                "src.compat.dependency_guard.ensure_core_dependencies",
                side_effect=lambda: events.append("dependencies"),
            ),
            patch.dict(sys.modules, {"ok": fake_ok}),
        ):
            crash_log = open_log.return_value
            startup.start_application(load_config, configure)

        self.assertEqual(
            [
                "dependencies",
                "load_config",
                ("configure", {"debug": False}),
                ("ok_init", {"debug": False}),
                "start",
            ],
            events,
        )
        enable.assert_called_once_with(file=crash_log, all_threads=True)
        disable.assert_called_once_with()
        crash_log.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
