from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from src.compat.update_card_ui import launcher_supports_update_check

PATCH_MARKER = "_ok_bd2_launcher_update_notice_enabled"
NOTICE_SHOWN_MARKER = "_ok_bd2_launcher_update_notice_shown"


def launcher_requires_reinstall(version) -> bool:
    return launcher_supports_update_check(version) is False


def launcher_download_url(config: dict) -> str:
    links = config.get("links") or {}
    default_links = links.get("default") or {}
    return str(default_links.get("download") or "").strip()


def show_launcher_update_notice(window, pyappify_module, config) -> None:
    if not launcher_requires_reinstall(getattr(pyappify_module, "pyappify_version", None)):
        return
    download_url = launcher_download_url(config)
    if not download_url:
        return

    version = getattr(pyappify_module, "pyappify_version", "unknown")
    message = (
        f"当前启动器版本为 {version}，版本过旧，无法检查应用更新。\n\n"
        "程序已尝试自动升级启动器；如果升级没有生效，请重新下载安装包。\n"
        "重新安装不会删除你的任务配置。"
    )
    box = QMessageBox(QMessageBox.Icon.Warning, "需要更新启动器", message, parent=window)
    download_button = box.addButton("重新下载最新版", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("稍后提醒", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is download_button:
        QDesktopServices.openUrl(QUrl(download_url))


def install_launcher_update_notice() -> None:
    from ok.ui.qt.MainWindow import MainWindow

    if getattr(MainWindow, PATCH_MARKER, False):
        return

    original_show_event = MainWindow.showEvent

    def show_event_with_launcher_notice(self, event):
        original_show_event(self, event)
        if getattr(self, NOTICE_SHOWN_MARKER, False):
            return
        setattr(self, NOTICE_SHOWN_MARKER, True)
        import pyappify

        QTimer.singleShot(
            750,
            lambda: show_launcher_update_notice(self, pyappify, self.config),
        )

    MainWindow.showEvent = show_event_with_launcher_notice
    setattr(MainWindow, PATCH_MARKER, True)
