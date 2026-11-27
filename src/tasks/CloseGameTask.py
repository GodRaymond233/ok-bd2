"""Request a graceful close of the running PC game."""

import psutil
import pywintypes
import win32con
import win32gui
import win32process

from src import HWND_CLASS
from src.game_path import get_game_exe_names
from src.icons import TaskIcon
from src.tasks.BaseBD2Task import BaseBD2Task

AFTER_DAILY_KEY = "日常完成后关闭游戏"


class CloseGameTask(BaseBD2Task):
    return_home_after_run = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "关闭游戏"
        self.icon = TaskIcon.POWER_BUTTON
        self.group_name = "上游扩展"
        self.visible = True
        self.support_schedule_task = False
        self.enable_after_start = False
        self.status_keys = ["状态"]
        self.default_config[AFTER_DAILY_KEY] = False

    def run(self):
        manager = self.executor.device_manager
        if manager.config.get("capture") == "ChildSession":
            manager.stop_hwnd()
            self.info_set("状态", "游戏已关闭。")
            return
        names = {name.casefold() for name in get_game_exe_names()}
        processes = {
            process.pid: process
            for process in psutil.process_iter(["name"])
            if (process.info["name"] or "").casefold() in names
        }
        if not processes:
            self.info_set("状态", "游戏已关闭。")
            return
        if len(processes) != 1:
            raise ValueError("检测到多个游戏进程，请先确认要关闭的客户端。")
        pid, process = next(iter(processes.items()))
        windows = []

        def collect(hwnd, _):
            try:
                if (win32gui.IsWindowVisible(hwnd)
                        and win32gui.GetClassName(hwnd) == HWND_CLASS
                        and win32process.GetWindowThreadProcessId(hwnd)[1] == pid):
                    windows.append(hwnd)
            except pywintypes.error:
                pass
            return True

        try:
            win32gui.EnumWindows(collect, None)
            if not process.is_running():
                self.info_set("状态", "游戏已关闭。")
                return
            if len(windows) != 1:
                raise ValueError("未确认唯一的游戏窗口，请在游戏内退出。")
            hwnd = windows[0]
            if (win32gui.GetClassName(hwnd) != HWND_CLASS
                    or win32process.GetWindowThreadProcessId(hwnd)[1] != pid
                    or not process.is_running()):
                raise ValueError("游戏窗口已变化，请重新确认。")
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            process.wait(timeout=8)
        except psutil.TimeoutExpired as error:
            raise ValueError("游戏仍在运行，请查看客户端是否显示退出确认。") from error
        except (pywintypes.error, psutil.AccessDenied) as error:
            raise ValueError("无法确认游戏窗口已关闭，请在游戏内退出。") from error
        self.info_set("状态", "游戏已关闭。")
