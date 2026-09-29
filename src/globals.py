from pathlib import Path

from ok import Logger
from PySide6.QtCore import QObject

logger = Logger.get_logger(__name__)


class Globals(QObject):
    def __init__(self, exit_event):
        super().__init__()
        # Flush the bounded diagnostic evidence queue during application shutdown.
        exit_event.bind_stop(self)
        from src.diagnostics.runtime import install

        self.diagnostic_evidence = install(Path(__file__).resolve().parents[1])

    def stop(self):
        self.diagnostic_evidence.stop()

    def on_show_main_window(self, main_window):
        from ok import og

        from src.game_path import seed_device_manager_launch_path
        from src.ui.feedback_report import install_feedback_report
        from src.ui.fluent_motion import install_fluent_page_transition, install_start_list_motion
        from src.ui.live_screenshot import install_live_screenshot
        from src.ui.nav_sections import install_nav_sections
        from src.ui.quest_theme import apply_app_font
        from src.ui.responsive_start_tab import install_responsive_start_tab

        apply_app_font()

        launch_path = seed_device_manager_launch_path(og.device_manager)
        if launch_path:
            logger.info(f"seed BD2 Starter path {launch_path}")
        install_live_screenshot(main_window.start_tab)
        install_feedback_report(main_window.start_tab)
        # 响应式重排要在 live_screenshot/feedback_report 之后：前者移动
        # debug 卡片并创建实时截图行，后者往 debug 行追加按钮。
        install_responsive_start_tab(main_window.start_tab)
        install_nav_sections(main_window)
        install_fluent_page_transition(main_window)
        install_start_list_motion(main_window.start_tab)
