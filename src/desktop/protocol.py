"""Contract between the desktop shell (Electron) and the Python core.

Keep in sync with ``desktop/shell/src/protocol.ts``.

stdin (shell -> core), one JSON object per line:
    first line  {"v": 1, "token": "<url-safe secret>", "parent_pid": <shell pid>}
    afterwards  {"cmd": "shutdown", "reason": "<text>"}
stdout (core -> shell), one line per message, prefixed with ``LINE_PREFIX``:
    {"type": "ready", "port": int, "pid": int, "api": API_VERSION, "core_version": str}
    {"type": "fatal", "code": str, "message": str}
    {"type": "exiting", "reason": str}
    {"type": "notification", "title": str, "message": str, "error": bool}
Anything else on stdout or stderr is log output.
"""

PROTOCOL_VERSION = 1
API_VERSION = 1
LINE_PREFIX = "@@okbd2 "

EXIT_OK = 0
EXIT_HANDSHAKE_FAILED = 10
EXIT_EXECUTOR_LOCKED = 11
EXIT_STARTUP_FAILED = 12
# Bootstrap (main_desktop.py) only.
EXIT_SHELL_MISSING = 13
EXIT_SHELL_INCOMPATIBLE = 14

HANDSHAKE_TIMEOUT_SECONDS = 10.0
READY_TIMEOUT_SECONDS = 90.0
PARENT_POLL_SECONDS = 1.0
EXIT_WATCHDOG_SECONDS = 15.0
GRACEFUL_SERVER_SHUTDOWN_SECONDS = 5

TOKEN_MIN_LENGTH = 32
TOKEN_MAX_LENGTH = 256
