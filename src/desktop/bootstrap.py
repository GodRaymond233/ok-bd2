"""Bootstrap for the desktop client (``main_desktop.py``).

PyAppify runs a Python script; the Electron shell is a separate native component.  The
bootstrap finds the shell pinned by this core, checks its manifest, starts it with the core
command, and waits for it.  It never takes the executor lock and is not an automation entry.

Layouts:
    PyAppify install  <base>/data/apps/ok-bd2/working   (this script's working directory)
                      <base>/data/apps/ok-bd2/shell/<version>/ok-bd2-desktop.exe
                      <base>/data/apps/ok-bd2/shell-data  (Chromium profile)
    Source checkout   desktop/shell/release/win-unpacked/ok-bd2-desktop.exe
``OK_BD2_SHELL_DIR`` overrides the shell directory.  Downloading a missing shell is a
stage 5 task; stage 1 reports it instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.desktop import protocol

# The shell build this core expects; bumped together with desktop/shell/package.json.
REQUIRED_SHELL_VERSION = "0.2.1"
MANIFEST_NAME = "shell-manifest.json"
SHELL_EXECUTABLE = "ok-bd2-desktop.exe" if os.name == "nt" else "ok-bd2-desktop"
SHELL_DIR_ENV = "OK_BD2_SHELL_DIR"


class BootstrapError(Exception):
    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code


def pyappify_app_base(working_dir: Path) -> Path | None:
    """``data/apps/ok-bd2`` when running from a PyAppify working directory."""
    if working_dir.name.lower() == "working" and working_dir.parent.parent.name.lower() == "apps":
        return working_dir.parent
    return None


def shell_directory(working_dir: Path, env=None) -> Path:
    env = os.environ if env is None else env
    if env.get(SHELL_DIR_ENV):
        return Path(env[SHELL_DIR_ENV])
    base = pyappify_app_base(working_dir)
    if base is not None:
        return base / "shell" / REQUIRED_SHELL_VERSION
    return working_dir / "desktop" / "shell" / "release" / "win-unpacked"


def user_data_directory(working_dir: Path) -> Path:
    base = pyappify_app_base(working_dir)
    if base is not None:
        return base / "shell-data"
    return working_dir / "desktop" / "shell" / "user-data"


def check_shell(directory: Path) -> Path:
    executable = directory / SHELL_EXECUTABLE
    if not executable.is_file():
        raise BootstrapError(
            protocol.EXIT_SHELL_MISSING,
            f"没有找到桌面外壳：{executable}。请重新安装，或使用带外壳的测试包。",
        )
    try:
        manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BootstrapError(
            protocol.EXIT_SHELL_INCOMPATIBLE, f"桌面外壳缺少有效的版本清单：{error}"
        ) from error
    version = manifest.get("version")
    api = manifest.get("api") or {}
    if version != REQUIRED_SHELL_VERSION:
        raise BootstrapError(
            protocol.EXIT_SHELL_INCOMPATIBLE,
            f"桌面外壳版本为 {version}，当前核心需要 {REQUIRED_SHELL_VERSION}。",
        )
    if not api.get("min", 0) <= protocol.API_VERSION <= api.get("max", -1):
        raise BootstrapError(
            protocol.EXIT_SHELL_INCOMPATIBLE,
            f"桌面外壳不支持核心接口版本 {protocol.API_VERSION}。",
        )
    return executable


def core_python(executable: str | None = None) -> str:
    """The console interpreter next to ``pythonw.exe``: the shell needs working stdio."""
    path = Path(executable or sys.executable)
    if path.name.lower() == "pythonw.exe":
        console = path.with_name("python.exe")
        if console.is_file():
            return str(console)
    return str(path)


# Shell flags the bootstrap passes through (checks and development only).
FORWARDED_FLAGS = {"--smoke-test": 1, "--dev-tools": 0}


def forwarded_arguments(argv: list[str]) -> list[str]:
    forwarded = []
    index = 0
    while index < len(argv):
        width = FORWARDED_FLAGS.get(argv[index])
        if width is not None:
            forwarded.extend(argv[index : index + 1 + width])
            index += width
        index += 1
    return forwarded


def shell_command(
    executable: Path, working_dir: Path, entry_script: str, extra: list[str] | None = None
) -> list[str]:
    return [
        str(executable),
        "--core-python",
        core_python(),
        "--core-script",
        str(working_dir / entry_script),
        "--core-cwd",
        str(working_dir),
        "--user-data",
        str(user_data_directory(working_dir)),
        *(extra or []),
    ]


def report(message: str) -> None:
    print(message, file=sys.stderr)
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "ok-bd2", 0x10 | 0x1000)


def main(argv: list[str] | None = None, *, popen=subprocess.Popen, working_dir=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    working_dir = Path(working_dir or os.getcwd())
    entry_script = "main_debug.py" if "--debug" in argv else "main.py"
    try:
        executable = check_shell(shell_directory(working_dir))
    except BootstrapError as error:
        report(str(error))
        return error.exit_code
    command = shell_command(executable, working_dir, entry_script, forwarded_arguments(argv))
    process = popen(command, cwd=str(working_dir))
    return process.wait()
