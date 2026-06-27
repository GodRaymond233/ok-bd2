from src import GAME_EXE, GAME_NAME, HWND_CLASS
from src.game_path import resolve_game_exe_path
from src.tasks.BaseBD2Task import BaseBD2Task


class LauncherTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "连接游戏"
        self.visible = False
        self.enable_after_start = False

    def run(self):
        game_path = resolve_game_exe_path() or "未找到"
        self.log_info(
            f"{GAME_NAME} 启动配置已加载。"
            f"游戏程序={GAME_EXE}，窗口类名={HWND_CLASS}，游戏路径={game_path}。"
        )
        return True
