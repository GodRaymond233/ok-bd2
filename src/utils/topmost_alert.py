"""Always-on-top message box for "the task needs you now" alerts.

The task runs on a worker thread inside ok-script; a modal MessageBoxW on that
thread would freeze the task until someone clicks OK. So the default path pops
the box from a daemon thread and returns immediately: the box stays above every
window (including the game) until dismissed, while the task itself has already
stopped and can be restarted from the UI.
"""

from __future__ import annotations

import os
import sys
import threading

# MB_ICONWARNING (0x30) | MB_SETFOREGROUND (0x10000) | MB_TOPMOST (0x40000).
TOPMOST_MESSAGEBOX_FLAGS = 0x30 | 0x10000 | 0x40000

DEFAULT_TITLE = "ok-bd2 需要手动处理"
FAILURE_TITLE = "ok-bd2 任务执行失败"


def messagebox_flags() -> int:
    """The flag word used for the alert box; kept as a function for tests."""

    return TOPMOST_MESSAGEBOX_FLAGS


def _messagebox(message: str, title: str, flags: int) -> None:
    """Single call site for the Win32 box so tests can replace it."""

    import ctypes

    ctypes.windll.user32.MessageBoxW(0, message, title, flags)


def _show(message: str, title: str) -> None:
    try:
        _messagebox(message, title, messagebox_flags())
    except Exception as exc:  # pragma: no cover - depends on the desktop
        print(f"topmost alert failed: {exc}", file=sys.stderr)


def show_topmost_alert(
    message: str,
    title: str = DEFAULT_TITLE,
    *,
    blocking: bool = False,
) -> bool:
    """Show ``message`` in a topmost box above the game.

    Returns True when a box was actually raised. ``blocking=True`` waits for
    the user inside the caller (tests / shutdown paths); the default spawns a
    daemon thread so a task can finish its own cleanup right away.
    """

    if not message:
        return False
    if os.name != "nt":
        print(f"{title}: {message}", file=sys.stderr)
        return False

    if blocking:
        _show(message, title)
        return True

    thread = threading.Thread(
        target=_show, args=(message, title), name="bd2-topmost-alert", daemon=True,
    )
    thread.start()
    return True


def show_task_failure_alert(task_name: str, error: str) -> bool:
    """Always-on-top notice for an exception that stopped a one-time task.

    ok-script reports task failures through its own small in-app dialog, which
    stays behind the game window. This box is raised above everything so a
    failure is noticed even when the player is looking at the game.
    """

    detail = str(error or "").strip() or "未知错误"
    return show_topmost_alert(
        f"任务「{task_name}」执行失败，已停止：\n{detail}\n\n"
        "游戏画面留在当前界面，处理完之后可以直接重新启动任务。\n"
        "常见原因：游戏窗口被关闭或最小化、锁屏/UAC 让鼠标输入暂停、"
        "当前界面与脚本预期不一致。",
        title=FAILURE_TITLE,
    )
