"""The client's interface language, kept where the Qt window keeps its own choice."""

import gettext
import json
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException
from ok import Config

from src.desktop.config_store import config_lock

I18N_ROOT = Path(__file__).resolve().parents[2] / "i18n"
AUTO = "Auto"
LANGUAGES = (AUTO, "zh_CN", "en_US")
# Chinese is the source language: it has no catalog.
TRANSLATED = ("en_US",)


def preference_path():
    return Path(Config.config_folder or "configs") / "ui_config.json"


def stored_preferences(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def stored_language(path):
    window = stored_preferences(path).get("MainWindow")
    value = window.get("Language") if isinstance(window, dict) else None
    if value in LANGUAGES:
        return value
    # The Qt window also offers languages this client has no text for.
    return "zh_CN" if str(value).startswith("zh") else AUTO


def save_language(path, language):
    data = stored_preferences(path)
    window = data.get("MainWindow")
    data["MainWindow"] = {**(window if isinstance(window, dict) else {}), "Language": language}
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".ui-config-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=4)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def task_messages(language):
    """The repository's gettext catalog: task names, descriptions and settings."""
    try:
        with (I18N_ROOT / language / "LC_MESSAGES" / "ok.mo").open("rb") as stream:
            catalog = gettext.GNUTranslations(stream)._catalog
    except OSError:
        return {}
    return {key: value for key, value in catalog.items() if isinstance(key, str) and key and value}


def install_language(app, runtime):
    def payload():
        return {
            "language": stored_language(preference_path()),
            "catalogs": {language: task_messages(language) for language in TRANSLATED},
        }

    @app.get("/api/desktop/language")
    def language():
        return payload()

    @app.post("/api/desktop/language")
    def set_language(body: dict):
        value = body.get("language")
        if value not in LANGUAGES:
            raise HTTPException(status_code=400, detail="不支持这种语言")
        with config_lock:
            try:
                runtime.ensure_config_write()
                save_language(preference_path(), value)
            except (RuntimeError, OSError) as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
        return payload()
