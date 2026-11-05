"""The illustrated task guides of ``docs/guides`` and their pictures, for the desktop client."""

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from src.task_guides import GUIDE_ROOT, GUIDES

IMAGE_ROOT = GUIDE_ROOT.parent / "images" / "usage"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MISSING_FILE = "说明文件缺失，请重新安装当前版本"


def guide_id(task):
    guide = GUIDES.get(type(task).__name__)
    return guide[1] if guide else None


def read_guide(slug):
    for title, stem, summary in GUIDES.values():
        if stem == slug:
            try:
                markdown = (GUIDE_ROOT / f"{stem}.md").read_text(encoding="utf-8")
            except OSError as error:
                raise HTTPException(404, MISSING_FILE) from error
            return {"id": stem, "title": title, "summary": summary, "markdown": markdown}
    raise HTTPException(404, "没有这份说明")


def image_path(name):
    """The picture called ``name``; a name is a bare file name, never a path."""
    path = IMAGE_ROOT / name
    if Path(name).name != name or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(404, "没有这张图片")
    if not path.is_file():
        raise HTTPException(404, MISSING_FILE)
    return path


def install_guides(app):
    @app.get("/api/bd2/guides/{slug}")
    def guide(slug: str):
        return read_guide(slug)

    @app.get("/guide-images/{name}")
    def guide_image(name: str):
        return FileResponse(image_path(name))
