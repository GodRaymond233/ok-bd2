"""The same-directory-mutex scenario of desktop/scripts/lifecycle_probe.py on its own.

F8 of the stage 1 Windows checks: the scenario removes a temporary folder that the older
instance runs in. Under the venv that instance is the python.exe redirector, whose child
interpreter ends a moment after the redirector does; these tests stand in a parent that
behaves the same way. F9: run alone (--only), the scenario wrote its log into a folder that
only the other scenarios created.
"""

import contextlib
import importlib.util
import io
import os
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

# probe_core.py's mutex modes without ok-script: the holder waits, the contender reports.
STAND_IN_CORE = textwrap.dedent(
    """
    import json, sys, time
    if sys.argv[1] == "mutex-holder":
        print("PROBE_HOLDER_READY", flush=True)
        time.sleep(60)
    else:
        print("PROBE_CONTENDER " + json.dumps({"check_mutex_called": False, "reached": True}))
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


class RunAloneTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "the scenario runs on Windows only")
    def test_scenario_makes_its_own_folder(self):
        with tempfile.TemporaryDirectory() as base:
            core = Path(base) / "core.py"
            core.write_text(STAND_IN_CORE, encoding="utf-8")
            out = Path(base) / "run" / "venv"  # main makes only run; the other scenarios made venv
            checks = []
            with patch.object(probe, "CORE", core):
                skipped = probe.same_directory_mutex(out, Path(sys.executable), checks)
            self.assertIsNone(skipped)
            self.assertTrue(all(item["passed"] for item in checks), checks)
            self.assertIn("PROBE_CONTENDER", (out / "same-directory-mutex.log").read_text("utf-8"))

    def test_misspelt_scenario_is_refused(self):
        base = Path(tempfile.mkdtemp(prefix="okbd2-probe-test-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
            probe.main(["--out", str(base / "run"), "--only", "same-directory-mutx"])
        self.assertEqual(refused.exception.code, 2)
        self.assertFalse((base / "run").exists())


if __name__ == "__main__":
    unittest.main()
