from pathlib import Path

LOG_FOLDER_NAME = "logs"


def log_folder_path(base_path: Path | None = None) -> Path:
    root = Path.cwd() if base_path is None else Path(base_path)
    return (root / LOG_FOLDER_NAME).resolve()


def ensure_log_folder(base_path: Path | None = None) -> Path:
    folder = log_folder_path(base_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def open_log_folder() -> Path:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    folder = ensure_log_folder()
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
        raise RuntimeError(f"无法打开日志文件夹：{folder}")
    return folder
