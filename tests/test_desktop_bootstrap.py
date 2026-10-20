import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.desktop import bootstrap, protocol


class ShellLayoutTest(unittest.TestCase):
    def test_pyappify_layout_and_source_checkout(self):
        working = Path("D:/Games/ok-bd2/data/apps/ok-bd2/working")
        self.assertEqual(
            bootstrap.shell_directory(working, env={}),
            Path("D:/Games/ok-bd2/data/apps/ok-bd2/shell") / bootstrap.REQUIRED_SHELL_VERSION,
        )
        self.assertEqual(
            bootstrap.user_data_directory(working),
            Path("D:/Games/ok-bd2/data/apps/ok-bd2/shell-data"),
        )
        checkout = Path("D:/ok-bd2-worktrees/electron-client")
        self.assertEqual(
            bootstrap.shell_directory(checkout, env={}),
            checkout / "desktop" / "shell" / "release" / "win-unpacked",
        )
        self.assertEqual(
            bootstrap.shell_directory(checkout, env={bootstrap.SHELL_DIR_ENV: "E:/shell"}),
            Path("E:/shell"),
        )

    def test_console_interpreter_is_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            pythonw = Path(directory) / "pythonw.exe"
            python = Path(directory) / "python.exe"
            pythonw.touch()
            self.assertEqual(bootstrap.core_python(str(pythonw)), str(pythonw))
            python.touch()
            self.assertEqual(bootstrap.core_python(str(pythonw)), str(python))


class CheckShellTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, manifest=None, executable=True):
        if executable:
            (self.root / bootstrap.SHELL_EXECUTABLE).write_bytes(b"")
        if manifest is not None:
            (self.root / bootstrap.MANIFEST_NAME).write_text(json.dumps(manifest), "utf-8")

    def assert_refused(self, code):
        with self.assertRaises(bootstrap.BootstrapError) as raised:
            bootstrap.check_shell(self.root)
        self.assertEqual(raised.exception.exit_code, code)

    def test_missing_executable(self):
        self.assert_refused(protocol.EXIT_SHELL_MISSING)

    def test_missing_or_mismatched_manifest(self):
        self.write()
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)
        self.write({"version": "0.0.9", "api": {"min": 1, "max": 1}})
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)
        self.write({"version": bootstrap.REQUIRED_SHELL_VERSION, "api": {"min": 2, "max": 3}})
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)

    def test_matching_shell(self):
        self.write({"version": bootstrap.REQUIRED_SHELL_VERSION, "api": {"min": 1, "max": 1}})
        self.assertEqual(bootstrap.check_shell(self.root), self.root / bootstrap.SHELL_EXECUTABLE)


class MainTest(unittest.TestCase):
    def test_starts_shell_with_core_command_and_returns_its_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "data" / "apps" / "ok-bd2"
            working = base / "working"
            shell = base / "shell" / bootstrap.REQUIRED_SHELL_VERSION
            working.mkdir(parents=True)
            shell.mkdir(parents=True)
            (shell / bootstrap.SHELL_EXECUTABLE).write_bytes(b"")
            (shell / bootstrap.MANIFEST_NAME).write_text(
                json.dumps(
                    {"version": bootstrap.REQUIRED_SHELL_VERSION, "api": {"min": 1, "max": 1}}
                ),
                "utf-8",
            )
            calls = []

            def popen(command, cwd):
                calls.append((command, cwd))
                return SimpleNamespace(wait=lambda: 0)

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(bootstrap.SHELL_DIR_ENV, None)
                code = bootstrap.main(["--debug"], popen=popen, working_dir=working)
        self.assertEqual(code, 0)
        command, cwd = calls[0]
        self.assertEqual(Path(command[0]), shell / bootstrap.SHELL_EXECUTABLE)
        self.assertEqual(
            Path(command[command.index("--core-script") + 1]), working / "main_debug.py"
        )
        self.assertEqual(Path(command[command.index("--core-cwd") + 1]), working)
        self.assertEqual(Path(command[command.index("--user-data") + 1]), base / "shell-data")
        self.assertEqual(Path(cwd), working)

    def test_only_known_shell_flags_are_forwarded(self):
        self.assertEqual(
            bootstrap.forwarded_arguments(
                ["--debug", "--smoke-test", "D:/out/smoke.json", "--unknown", "x", "--dev-tools"]
            ),
            ["--smoke-test", "D:/out/smoke.json", "--dev-tools"],
        )
        self.assertEqual(bootstrap.forwarded_arguments([]), [])

    def test_missing_shell_is_reported_without_starting_anything(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bootstrap, "report") as report,
        ):
            code = bootstrap.main([], popen=None, working_dir=Path(directory))
        self.assertEqual(code, protocol.EXIT_SHELL_MISSING)
        report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
