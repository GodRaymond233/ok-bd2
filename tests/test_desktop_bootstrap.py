import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.desktop import bootstrap, protocol
from src.desktop.shell_install import install_shell


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

    def test_missing_or_mismatched_manifest(self):
        self.write()
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)
        self.write({"version": "0.0.9", "api": {"min": 1, "max": 1}})
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)
        self.write({"version": bootstrap.REQUIRED_SHELL_VERSION, "api": {"min": 2, "max": 3}})
        self.assert_refused(protocol.EXIT_SHELL_INCOMPATIBLE)


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


class ShellDownloadTest(unittest.TestCase):
    def test_pinned_download_uses_next_source_and_keeps_old_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "shell/0.1.0/sentinel"
            old.parent.mkdir(parents=True)
            old.write_text("old", "utf-8")
            archive = root / "shell.zip"
            manifest = {"version": bootstrap.REQUIRED_SHELL_VERSION, "api": {"min": 1, "max": 1}}
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(bootstrap.SHELL_EXECUTABLE, b"verified shell")
                output.writestr(bootstrap.MANIFEST_NAME, json.dumps(manifest))
            release = root / "release.json"
            release.write_text(
                json.dumps(
                    {
                        "version": bootstrap.REQUIRED_SHELL_VERSION,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "urls": [(root / "missing.zip").as_uri(), archive.as_uri()],
                    }
                ),
                "utf-8",
            )
            destination = root / "shell" / bootstrap.REQUIRED_SHELL_VERSION
            executable = install_shell(
                destination,
                bootstrap.REQUIRED_SHELL_VERSION,
                bootstrap.check_shell,
                release_file=release,
            )
            self.assertEqual(executable.read_bytes(), b"verified shell")
            self.assertEqual(old.read_text("utf-8"), "old")
            archive.unlink()
            self.assertEqual(
                install_shell(
                    destination,
                    bootstrap.REQUIRED_SHELL_VERSION,
                    bootstrap.check_shell,
                    release_file=release,
                ),
                executable,
            )

    def test_hash_mismatch_and_unsafe_zip_leave_no_installed_version(self):
        for name, wrong_hash in (
            ("../outside", False),
            ("C:/outside", False),
            ("file:stream", False),
            ("CON.txt", False),
            (bootstrap.SHELL_EXECUTABLE, True),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(name, b"bad")
                release = root / "release.json"
                release.write_text(
                    json.dumps(
                        {
                            "version": bootstrap.REQUIRED_SHELL_VERSION,
                            "sha256": "0" * 64
                            if wrong_hash
                            else hashlib.sha256(archive.read_bytes()).hexdigest(),
                            "urls": [archive.as_uri()],
                        }
                    ),
                    "utf-8",
                )
                destination = root / "shell" / bootstrap.REQUIRED_SHELL_VERSION
                with self.assertRaises(ValueError):
                    install_shell(
                        destination,
                        bootstrap.REQUIRED_SHELL_VERSION,
                        bootstrap.check_shell,
                        release_file=release,
                    )
                self.assertFalse(destination.exists())
                self.assertFalse((root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
