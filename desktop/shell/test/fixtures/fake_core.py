"""Stdlib-only stand-in for ``python main.py --service`` in the shell's lifecycle tests.

Like the real core it reads the handshake, prints the ready line and from then on treats a
closed stdin or a ``shutdown`` command as the start of its safe exit, which it records in
FAKE_CORE_MARKER (one JSON object per line).  FAKE_CORE_GAME=1 first starts a long-running
child with subprocess.Popen, the way ok-script starts the game.  FAKE_CORE_MODE=hang records
the request but never exits, so only a forced stop can end it.
"""

import json
import os
import subprocess
import sys
import threading
import time

PREFIX = "@@okbd2 "
MARKER = os.environ.get("FAKE_CORE_MARKER")
MODE = os.environ.get("FAKE_CORE_MODE", "normal")


def record(**fields):
    if MARKER:
        with open(MARKER, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "at": time.time(), **fields}) + "\n")


def send(**message):
    try:
        sys.stdout.write(PREFIX + json.dumps(message) + "\n")
        sys.stdout.flush()
    except OSError:
        pass  # the shell is gone, as src.desktop.service.ProtocolChannel tolerates too


def safe_exit(reason):
    record(event="safe-exit", reason=reason)
    if MODE == "hang":
        return
    send(type="exiting", reason=reason)
    os._exit(0)


def watch_shell():
    for line in sys.stdin:
        try:
            command = json.loads(line)
        except ValueError:
            continue
        if isinstance(command, dict) and command.get("cmd") == "shutdown":
            safe_exit(f"shell:{command.get('reason')}")
    safe_exit("stdin-closed")


def main():
    handshake = json.loads(sys.stdin.readline())
    game_pid = None
    if os.environ.get("FAKE_CORE_GAME") == "1":
        game = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        game_pid = game.pid
    record(event="ready", parent_pid=handshake.get("parent_pid"), game_pid=game_pid)
    send(type="ready", port=40000, pid=os.getpid(), api=1, core_version=str(game_pid or "fake"))
    threading.Thread(target=watch_shell, name="ShellCommands", daemon=True).start()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
