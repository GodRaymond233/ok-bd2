"""Bootstrap for the desktop client (``main_desktop.py``).

PyAppify runs a Python script; the Electron shell is a separate native component.  The
bootstrap finds the shell pinned by this core, checks its manifest, starts it with the core
command, and waits for it.  It never takes the executor lock and is not an automation entry.

Layouts:
    PyAppify install  <base>/data/apps/ok-bd2/working   (this script's working directory)
                      <base>/data/apps/ok-bd2/shell/<version>/ok-bd2-desktop.exe
                      <base>/data/apps/ok-bd2/shell-data  (Chromium profile)
    Source checkout   desktop/shell/release/win-unpacked/ok-bd2-desktop.exe
``OK_BD2_SHELL_DIR`` overrides the shell directory. Packaged installs obtain a missing
shell from the release manifest before starting any automation process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from src.desktop import protocol
from src.desktop.backups import installed_base

# The shell build this core expects; bumped together with desktop/shell/package.json.
REQUIRED_SHELL_VERSION = "0.3.6"
MANIFEST_NAME = "shell-manifest.json"
SHELL_EXECUTABLE = "ok-bd2-desktop.exe" if os.name == "nt" else "ok-bd2-desktop"
SHELL_DIR_ENV = "OK_BD2_SHELL_DIR"


class BootstrapError(ValueError):
    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code


def pyappify_app_base(working_dir: Path) -> Path | None:
    """``data/apps/ok-bd2`` when running from a PyAppify working directory."""
    return installed_base(working_dir)


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
            f"没有找到桌面程序：{executable}。请重新安装。",
        )
    try:
        manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BootstrapError(
            protocol.EXIT_SHELL_INCOMPATIBLE, f"桌面程序缺少有效的版本清单：{error}"
        ) from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("api"), dict):
        raise BootstrapError(protocol.EXIT_SHELL_INCOMPATIBLE, "桌面程序版本清单格式无效")
    version = manifest.get("version")
    api = manifest["api"]
    if version != REQUIRED_SHELL_VERSION:
        raise BootstrapError(
            protocol.EXIT_SHELL_INCOMPATIBLE,
            f"桌面程序版本为 {version}，当前需要 {REQUIRED_SHELL_VERSION}，请重新安装。",
        )
    if (type(api.get("min")) is not int or type(api.get("max")) is not int
            or not api["min"] <= protocol.API_VERSION <= api["max"]):
        raise BootstrapError(
            protocol.EXIT_SHELL_INCOMPATIBLE,
            f"桌面程序与当前版本不兼容（接口 {protocol.API_VERSION}），请重新安装。",
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


@contextmanager
def startup_notice(message):
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    title = f"ok-bd2 启动准备 ({os.getpid()})"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    finished = threading.Event()
    window = threading.Thread(
        target=lambda: user32.MessageBoxW(0, message, title, 0x40), daemon=True)
    window.start()

    def close_when_finished():
        finished.wait()
        while window.is_alive():
            if handle := user32.FindWindowW(None, title):
                user32.PostMessageW(handle, 0x10, 0, 0)
                return
            window.join(0.05)

    threading.Thread(target=close_when_finished, daemon=True).start()
    try:
        yield
    finally:
        finished.set()
        window.join(0.5)


def repair_dependencies():
    from src.compat.dependency_guard import ensure_core_dependencies, find_missing_modules
    from src.executor_guard import acquire_executor_lock, release_executor_lock

    if not find_missing_modules():
        return
    acquired, _ = acquire_executor_lock("dependency-repair")
    if not acquired:
        raise BootstrapError(protocol.EXIT_EXECUTOR_LOCKED,
                             "另一个 ok-bd2 正在运行，请先退出再修复运行组件")
    try:
        with startup_notice("正在修复运行组件，可能需要几分钟。完成后请按提示重新打开客户端。"):
            ensure_core_dependencies()
    finally:
        release_executor_lock()


def main(argv: list[str] | None = None, *, popen=subprocess.Popen, working_dir=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    working_dir = Path(working_dir or os.getcwd())
    entry_script = "main_debug.py" if "--debug" in argv else "main.py"
    try:
        repair_dependencies()
        directory = shell_directory(working_dir)
        if pyappify_app_base(working_dir) is not None and not os.environ.get(SHELL_DIR_ENV):
            from src.desktop.shell_install import install_shell

            try:
                executable = install_shell(directory, REQUIRED_SHELL_VERSION, check_shell,
                                           notice=lambda: startup_notice(
                                               "需要下载桌面程序（约 100 MB），完成后会自动打开。"))
            except (OSError, ValueError) as error:
                raise BootstrapError(
                    protocol.EXIT_SHELL_MISSING, str(error)
                    + "\n桌面程序下载：https://cnb.cool/GodRaymond233/ok-bd2-update/-/releases"
                    + f"\n下载后解压到 {directory}。"
                    + "\n如被杀毒软件隔离，请检查隔离记录并信任此桌面程序目录。"
                ) from error
        else:
            executable = check_shell(directory)
    except BootstrapError as error:
        report(str(error))
        return error.exit_code
    command = shell_command(executable, working_dir, entry_script, forwarded_arguments(argv))
    process = popen(command, cwd=str(working_dir))
    return process.wait()
