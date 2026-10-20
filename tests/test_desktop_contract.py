"""The Python core and the Electron shell share one lifecycle contract."""

import json
import re
import unittest
from pathlib import Path

from src.desktop import bootstrap, protocol

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "desktop" / "shell"


def ts_number(source, name):
    match = re.search(rf"\b{name}:\s*([0-9_]+)", source)
    return int(match.group(1).replace("_", ""))


class ShellContractTest(unittest.TestCase):
    def setUp(self):
        self.source = (SHELL / "src" / "protocol.ts").read_text(encoding="utf-8")

    def test_line_prefix_and_versions_match(self):
        self.assertIn(f'LINE_PREFIX = "{protocol.LINE_PREFIX}"', self.source)
        self.assertIn(f"PROTOCOL_VERSION = {protocol.PROTOCOL_VERSION};", self.source)
        self.assertEqual(ts_number(self.source, "min"), protocol.API_VERSION)
        self.assertGreaterEqual(ts_number(self.source, "max"), protocol.API_VERSION)

    def test_exit_codes_match(self):
        for name in ("OK", "HANDSHAKE_FAILED", "EXECUTOR_LOCKED", "STARTUP_FAILED"):
            with self.subTest(name=name):
                self.assertEqual(ts_number(self.source, name), getattr(protocol, f"EXIT_{name}"))

    def test_timeouts_respect_the_exit_contract(self):
        self.assertEqual(ts_number(self.source, "readyMs"), protocol.READY_TIMEOUT_SECONDS * 1000)
        # The shell only forces the core after the core's own watchdog had its chance.
        self.assertGreater(
            ts_number(self.source, "shutdownMs"), protocol.EXIT_WATCHDOG_SECONDS * 1000 + 5000
        )
        self.assertLessEqual(protocol.PARENT_POLL_SECONDS, 1.0)

    def test_shell_version_is_the_one_the_core_requires(self):
        package = json.loads((SHELL / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], bootstrap.REQUIRED_SHELL_VERSION)
        self.assertEqual(package["build"]["executableName"] + ".exe", "ok-bd2-desktop.exe")
        lock = json.loads((SHELL / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"]["node_modules/electron"]["version"], "44.9.1")
        self.assertEqual(package["devDependencies"]["electron"], "44.9.1")


if __name__ == "__main__":
    unittest.main()
