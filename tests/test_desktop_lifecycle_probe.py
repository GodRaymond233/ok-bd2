"""The same-directory-mutex scenario of desktop/scripts/lifecycle_probe.py on its own.

F8 of the stage 1 Windows checks: the scenario removes a temporary folder that the older
instance runs in. Under the venv that instance is the python.exe redirector, whose child
interpreter ends a moment after the redirector does; these tests stand in a parent that
behaves the same way. F9: run alone (--only), the scenario wrote its log into a folder that
only the other scenarios created. F10: where psutil may not read working directories, the
process in the folder went unlisted without a word.
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
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


class RemoveFolderTest(unittest.TestCase):

    def test_names_the_processes_in_a_folder_it_cannot_remove(self):
        folder = Path(tempfile.mkdtemp(prefix="okbd2-probe-test-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        # Under the venv on Windows, user is the redirector; the interpreter prints its own PID.
        user = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os, sys; print(os.getpid(), flush=True); sys.stdin.read()",
            ],
            cwd=folder,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(user.wait, 30)
        self.addCleanup(user.stdout.close)
        self.addCleanup(user.stdin.close)
        interpreter = int(user.stdout.readline())  # it has started, in the folder
        try:
            cwd = psutil.Process(interpreter).cwd()
        except psutil.AccessDenied as denied:
            # remove_folder counts such processes as unreadable (see the test below).
            self.skipTest(f"psutil may not read working directories here (F10): {denied!r}")
        checks = []
        in_use = PermissionError(32, "The process cannot access the file")
        with patch.object(probe.shutil, "rmtree", side_effect=in_use):
            probe.remove_folder(folder, checks)
        (result,) = checks
        self.assertFalse(result["passed"])
        self.assertIn("PermissionError", result["detail"]["error"])
        users = [item["pid"] for item in result["detail"]["working directory inside"]]
        self.assertIn(interpreter, users, f"psutil reads {cwd!r}; {result['detail']}")

    def test_matches_the_folder_as_set_or_resolved_and_counts_the_unreadable(self):
        folder = Path(tempfile.mkdtemp(prefix="okbd2-probe-test-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        resolved = folder.parent / "elsewhere" / folder.name  # behind a junction or symlink
        processes = [
            SimpleNamespace(pid=1, info={"name": "as set", "cwd": str(folder / "sub")}),  # Windows
            SimpleNamespace(pid=2, info={"name": "resolved", "cwd": str(resolved)}),  # Linux
            SimpleNamespace(pid=3, info={"name": "beside", "cwd": f"{folder}-other"}),
            SimpleNamespace(pid=4, info={"name": "above", "cwd": str(folder.parent)}),
            SimpleNamespace(pid=5, info={"name": "refused", "cwd": None}),
        ]
        checks = []
        with (
            patch.object(probe.shutil, "rmtree", side_effect=PermissionError(32, "in use")),
            patch.object(probe.os.path, "realpath", return_value=str(resolved)),
            patch.object(probe.psutil, "process_iter", return_value=processes),
        ):
            probe.remove_folder(folder, checks)
        detail = checks[0]["detail"]
        self.assertEqual([item["pid"] for item in detail["working directory inside"]], [1, 2])
        self.assertEqual(detail["working directory unreadable"], 1)


if __name__ == "__main__":
    unittest.main()
