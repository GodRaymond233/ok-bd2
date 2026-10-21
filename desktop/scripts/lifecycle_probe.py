"""Lifecycle checks for the desktop shell and core on the real operating system.

Each scenario starts the real CoreProcess inside a stand-in shell
(``desktop/shell/test/fixtures/shell-host.cjs``, run by the packaged Electron with
ELECTRON_RUN_AS_NODE=1, or by Node) and the real service layer with a stand-in runtime
(``probe_core.py``), then checks what happens to the processes:

    normal-stop                  the shell asks; the core exits by itself, nothing forced;
                                 cv2/numpy loaded only after the reading of stdin began
    shell-crash                  the shell is ended abruptly; the core starts its safe exit
                                 within 2 s, ends within 20 s, and the "game" keeps running
    core-crash                   the core dies after ready; the shell reports it
    ready-timeout                a stalled startup; the shell's stop reaches the core at once
    shell-crash-during-startup   the shell is ended while the core is still starting
    same-directory-mutex         (Windows) the service never ends a same-directory instance

Every process scenario runs with this venv's interpreter (a redirector on Windows) and with
the standalone interpreter behind it, as PyAppify starts it. No game is started: the "game"
is a sleeping Python child of the core. Only processes started here are ever ended.

    python desktop/scripts/lifecycle_probe.py --out <dir> [--host <exe>] [--only <name>]

Writes <dir>/lifecycle-probe.json and exits 0 only if every scenario passed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
HOST_SCRIPT = ROOT / "desktop" / "shell" / "test" / "fixtures" / "shell-host.cjs"
CORE = Path(__file__).with_name("probe_core.py")
GAME_TOKEN = "okbd2-probe-game"
START_TIMEOUT = 90  # a cold first import of the service stack can be slow on Windows


def read_lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def wait_for(check, timeout):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(0.05)


def same_process(pid, created):
    try:
        return abs(psutil.Process(pid).create_time() - created) < 0.01
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def end_game(record):
    """End the probe's sleeping "game" child, and nothing that merely reuses its PID."""
    if not record or not same_process(record["game_pid"], record["create_time"]):
        return
    process = psutil.Process(record["game_pid"])
    if any(GAME_TOKEN in part for part in process.cmdline()):
        process.kill()


class Run:
    def __init__(self, folder, host_command, python, extra_env):
        folder.mkdir(parents=True)
        self.folder = folder
        self.report = folder / "host.jsonl"
        self.marker = folder / "core.jsonl"
        env = dict(
            os.environ,
            HOST_REPORT=str(self.report),
            HOST_PYTHON=str(python),
            HOST_SCRIPT=str(CORE),
            HOST_CWD=str(folder),
            PROBE_MARKER=str(self.marker),
            PROBE_SITE=sysconfig.get_paths()["purelib"],
            PYTHONUTF8="1",
            **extra_env,
        )
        if host_command[0] != "node":
            env["ELECTRON_RUN_AS_NODE"] = "1"
        self.log = open(folder / "host.out.log", "w", encoding="utf-8")
        self.host = subprocess.Popen(
            [*host_command, str(HOST_SCRIPT)],
            cwd=folder,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )

    def host_event(self, kind):
        return next((e for e in read_lines(self.report) if e["type"] == kind), None)

    def core_event(self, kind):
        return next((e for e in read_lines(self.marker) if e["event"] == kind), None)

    def core_gone(self, timeout):
        start = wait_for(lambda: self.core_event("start"), 5)
        if not start:
            return True
        return wait_for(lambda: not same_process(start["pid"], start["create_time"]), timeout)

    def close(self):
        if self.host.poll() is None:
            self.host.kill()
        self.host.wait(timeout=10)
        self.log.close()
        end_game(self.core_event("game"))
        start = self.core_event("start")
        if start and same_process(start["pid"], start["create_time"]):
            psutil.Process(start["pid"]).kill()  # the probe's own core, left over by a failure


def check(checks, name, passed, detail=None):
    checks.append({"check": name, "passed": bool(passed), "detail": detail})


def normal_stop(run, checks):
    stopped = wait_for(lambda: run.host_event("stopped"), START_TIMEOUT + 30)
    check(checks, "the shell's stop finished", stopped)
    config = run.core_event("config")
    check(
        checks,
        "native modules loaded under supervision, as in main.py",
        config and config["native_preloaded"] is False,
        config,
    )
    if stopped:
        check(checks, "not forced", stopped["result"]["forced"] is False, stopped["result"])
    request = run.core_event("request")
    check(
        checks,
        "safe exit requested by the shell",
        request and request["reason"] == "shell:host",
        request,
    )
    check(checks, "core gone within 20 s", run.core_gone(20))
    game = run.core_event("game")
    check(
        checks, "game still running", game and same_process(game["game_pid"], game["create_time"])
    )


def shell_crash(run, checks, during_startup=False):
    awaited = "slow-start" if during_startup else None
    if awaited:
        ok = wait_for(lambda: run.core_event(awaited), START_TIMEOUT)
        check(checks, "core reached its slow startup", ok)
    else:
        ok = wait_for(lambda: run.host_event("ready"), START_TIMEOUT)
        check(checks, "core ready", ok)
    if not ok:
        return
    run.host.kill()  # abrupt: TerminateProcess on Windows
    killed = time.time()
    request = wait_for(lambda: run.core_event("request"), 5)
    check(checks, "core started its safe exit", request, request)
    if request:
        check(
            checks,
            "within 2 s of the shell's end",
            request["at"] - killed <= 2.0,
            round(request["at"] - killed, 3),
        )
        check(
            checks,
            "because the shell is gone",
            request["reason"] in ("stdin-closed", "parent-exited"),
            request["reason"],
        )
    check(checks, "core gone within 20 s", run.core_gone(20))
    if during_startup:
        check(checks, "never became ready", run.host_event("ready") is None)
    game = run.core_event("game")
    check(
        checks, "game still running", game and same_process(game["game_pid"], game["create_time"])
    )


def core_crash(run, checks):
    exit_event = wait_for(lambda: run.host_event("exit"), START_TIMEOUT + 10)
    check(checks, "the shell saw the core exit", exit_event)
    if exit_event:
        check(
            checks,
            "exit code 3, not requested",
            exit_event["exitCode"] == 3 and exit_event["requested"] is False,
            exit_event,
        )
    check(checks, "reported as a crash", wait_for(lambda: run.host_event("crashed"), 5))


def ready_timeout(run, checks):
    failed = wait_for(lambda: run.host_event("failed"), START_TIMEOUT)
    check(checks, "the shell gave up waiting", failed and failed["kind"] == "timeout", failed)
    request = wait_for(lambda: run.core_event("request"), 5)
    check(
        checks,
        "the shell's stop reached the starting core",
        request and request["reason"] == "shell:ready-timeout",
        request,
    )
    check(checks, "core gone within 20 s", run.core_gone(20))
    check(checks, "the shell then exited", wait_for(lambda: run.host.poll() is not None, 30))
    check(checks, "never became ready", run.host_event("ready") is None)


PROCESS_SCENARIOS = {
    "normal-stop": ({"PROBE_GAME": "1", "HOST_ACTION": "stop"}, normal_stop),
    "shell-crash": ({"PROBE_GAME": "1"}, shell_crash),
    "core-crash": ({"PROBE_CRASH_AFTER_READY": "1"}, core_crash),
    "ready-timeout": ({"PROBE_SLOW_START": "60", "HOST_READY_MS": "4000"}, ready_timeout),
    "shell-crash-during-startup": (
        {"PROBE_GAME": "1", "PROBE_SLOW_START": "60"},
        lambda run, checks: shell_crash(run, checks, during_startup=True),
    ),
}


def same_directory_mutex(out, python, checks):
    if os.name != "nt":
        return "skipped: ok-script's directory mutex exists on Windows only"
    env = dict(os.environ, PYTHONUTF8="1", PROBE_SITE=sysconfig.get_paths()["purelib"])
    with tempfile.TemporaryDirectory(prefix="okbd2-probe-mutex-") as folder:
        entry = str(Path(folder) / "main.py")
        holder = subprocess.Popen(
            [str(python), str(CORE), "mutex-holder", entry],
            cwd=folder,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            ready = False
            for line in holder.stdout:  # ok-script may log to stdout first
                if line.strip() == "PROBE_HOLDER_READY":
                    ready = True
                    break
            check(checks, "older instance holds the directory mutex", ready)
            contender = subprocess.run(
                [str(python), str(CORE), "mutex-contender", entry],
                cwd=folder,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            (out / "same-directory-mutex.log").write_text(
                contender.stdout + contender.stderr, encoding="utf-8"
            )
            result = next(
                (
                    json.loads(line.split(" ", 1)[1])
                    for line in contender.stdout.splitlines()
                    if line.startswith("PROBE_CONTENDER ")
                ),
                None,
            )
            check(
                checks,
                "the service runtime got past ok-script's mutex step",
                result and result["reached"],
                result,
            )
            check(
                checks,
                "ok-script's directory mutex check did not run",
                result and not result["check_mutex_called"],
                result,
            )
            time.sleep(0.5)
            check(checks, "the older instance is still running", holder.poll() is None)
        finally:
            if holder.poll() is None:
                holder.kill()
            holder.wait(timeout=10)
            holder.stdout.close()
    return None


def interpreters():
    venv = Path(sys.executable)
    base = Path(getattr(sys, "_base_executable", sys.executable))
    found = [("venv", venv)]
    if base.resolve() != venv.resolve():
        found.append(("standalone", base))
    return found


def default_host():
    packaged = ROOT / "desktop" / "shell" / "release" / "win-unpacked" / "ok-bd2-desktop.exe"
    if packaged.is_file():
        return [str(packaged)]
    return ["node"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--host", help="Electron executable (default: the packaged shell)")
    parser.add_argument("--only", action="append", help="run only these scenarios")
    args = parser.parse_args(argv)
    if not (ROOT / "desktop" / "shell" / "dist" / "core-process.js").is_file():
        parser.error("desktop/shell/dist is missing: run npm run build in desktop/shell first")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    host = [args.host] if args.host else default_host()
    if host[0] == "node":
        host = [shutil.which("node") or "node"]

    results = []
    for label, python in interpreters():
        for name, (extra_env, judge) in PROCESS_SCENARIOS.items():
            if args.only and name not in args.only:
                continue
            checks = []
            run = Run(out / label / name, host, python, extra_env)
            try:
                judge(run, checks)
            finally:
                run.close()
            results.append(
                {
                    "interpreter": label,
                    "python": str(python),
                    "scenario": name,
                    "passed": all(c["passed"] for c in checks),
                    "checks": checks,
                }
            )
            print(f"{'PASS' if results[-1]['passed'] else 'FAIL'} {label} {name}", flush=True)
        if not args.only or "same-directory-mutex" in args.only:
            checks = []
            skipped = same_directory_mutex(out / label, python, checks)
            results.append(
                {
                    "interpreter": label,
                    "python": str(python),
                    "scenario": "same-directory-mutex",
                    "skipped": skipped,
                    "passed": skipped is not None or all(c["passed"] for c in checks),
                    "checks": checks,
                }
            )
            print(
                f"{'SKIP' if skipped else 'PASS' if results[-1]['passed'] else 'FAIL'} "
                f"{label} same-directory-mutex",
                flush=True,
            )

    summary = {
        "passed": all(item["passed"] for item in results),
        "host": host,
        "platform": sys.platform,
        "results": results,
    }
    (out / "lifecycle-probe.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"lifecycle probe {'passed' if summary['passed'] else 'FAILED'}: "
        f"{out / 'lifecycle-probe.json'}"
    )
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
