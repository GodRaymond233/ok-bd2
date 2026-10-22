"""Cleanup in desktop/scripts/lifecycle_probe.py (F8 of the stage 1 Windows checks).

The same-directory-mutex scenario removes a temporary folder that the older instance runs
in. Under the venv that instance is the python.exe redirector, whose child interpreter ends
a moment after the redirector does; these tests stand in a parent that behaves the same way.
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil

PROBE = Path(__file__).resolve().parents[1] / "desktop" / "scripts" / "lifecycle_probe.py"
_spec = importlib.util.spec_from_file_location("lifecycle_probe", PROBE)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

# The child ends half a second after its parent is gone (its stdin reaches end of file).
REDIRECTOR = textwrap.dedent(
    """
    import subprocess, sys
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(0.5)"],
        stdin=subprocess.PIPE,
    )
    print("READY", child.pid, flush=True)
    child.wait()
    """
)


class EndHolderTest(unittest.TestCase):
    def test_waits_for_the_child_that_outlives_the_holder(self):
        holder = subprocess.Popen(
            [sys.executable, "-c", REDIRECTOR], stdout=subprocess.PIPE, text=True
        )
        child = int(holder.stdout.readline().split()[1])
        checks = []
        probe.end_holder(holder, checks)
        (result,) = checks
        self.assertTrue(result["passed"], result)
        self.assertIn(child, result["detail"]["still running after the holder ended"])
        self.assertFalse(psutil.pid_exists(child))

    def test_holder_that_already_exited(self):
        holder = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE)
        holder.wait(timeout=30)
        checks = []
        probe.end_holder(holder, checks)
        self.assertEqual([item["passed"] for item in checks], [True])
        self.assertEqual(checks[0]["detail"]["children"], [])


class RemoveFolderTest(unittest.TestCase):
    def test_removes_the_folder(self):
        folder = Path(tempfile.mkdtemp(prefix="okbd2-probe-test-"))
        (folder / "main.py").write_text("", encoding="utf-8")
        checks = []
        probe.remove_folder(folder, checks)
        self.assertEqual([item["passed"] for item in checks], [True])
        self.assertFalse(folder.exists())

    def test_names_the_processes_in_a_folder_it_cannot_remove(self):
        folder = Path(tempfile.mkdtemp(prefix="okbd2-probe-test-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        user = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            cwd=folder,
            stdin=subprocess.PIPE,
        )
        self.addCleanup(user.wait, 30)
        self.addCleanup(user.stdin.close)
        checks = []
        in_use = PermissionError(32, "The process cannot access the file")
        with patch.object(probe.shutil, "rmtree", side_effect=in_use):
            probe.remove_folder(folder, checks)
        (result,) = checks
        self.assertFalse(result["passed"])
        self.assertIn("PermissionError", result["detail"]["error"])
        users = [item["pid"] for item in result["detail"]["working directory inside"]]
        self.assertIn(user.pid, users)


if __name__ == "__main__":
    unittest.main()
