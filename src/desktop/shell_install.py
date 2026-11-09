"""Install a pinned shell archive into a new version directory before it is executed."""

import hashlib
import json
import re
import shutil
import stat
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from contextlib import nullcontext
from pathlib import Path, PurePosixPath

MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_UNPACKED = 1536 * 1024 * 1024
MAX_FILES = 5000
RELEASE_FILE = Path(__file__).with_name("shell-release.json")


def install_shell(destination, version, check_shell, *,
                  release_file=RELEASE_FILE, notice=nullcontext):
    destination = Path(destination)
    if destination.is_symlink() or destination.is_junction():
        raise ValueError("桌面程序安装目录不能是链接")
    if destination.exists():
        try:
            return check_shell(destination)
        except (OSError, ValueError):
            if not Path(release_file).is_file():
                raise
    try:
        release = json.loads(Path(release_file).read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("没有可用的桌面程序下载清单，请重新安装完整版本") from error
    if (
        not isinstance(release, dict)
        or release.get("version") != version
        or not re.fullmatch(r"[0-9a-f]{64}", str(release.get("sha256", "")))
        or not isinstance(release.get("urls"), list)
        or not release["urls"]
        or any(not isinstance(url, str) for url in release["urls"])
    ):
        raise ValueError("桌面程序下载清单无效或版本不匹配")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or destination.parent.is_junction():
        raise ValueError("桌面程序安装目录不能是链接")
    if destination.exists():
        try:
            destination.rename(destination.with_name(f"{version}.broken-{time.time_ns()}"))
        except OSError as error:
            raise ValueError("桌面程序正在运行或文件被占用，请关闭后重试") from error
    with (notice(),
          tempfile.TemporaryDirectory(prefix=".download-", dir=destination.parent) as temporary):
        staging = Path(temporary)
        archive = staging / "shell.zip"
        errors = []
        for url in release["urls"]:
            try:
                parsed = urllib.parse.urlparse(url)
                if parsed.scheme not in {"https", "file"} and not (
                    parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
                ):
                    raise ValueError("下载地址必须使用 HTTPS")
                digest, size = hashlib.sha256(), 0
                checked_at, checked_size = time.monotonic(), 0
                with (
                    urllib.request.urlopen(url, timeout=30) as source,
                    archive.open("wb") as target,
                ):
                    read = getattr(source, "read1", source.read)
                    while chunk := read(64 * 1024):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            raise ValueError("桌面程序下载超过大小限制")
                        digest.update(chunk)
                        target.write(chunk)
                        elapsed = time.monotonic() - checked_at
                        if elapsed >= 60:
                            if (size - checked_size) / elapsed < 20 * 1024:
                                raise ValueError("下载持续过慢，切换备用地址")
                            checked_at, checked_size = time.monotonic(), size
                if digest.hexdigest() != release["sha256"]:
                    raise ValueError("桌面程序 SHA256 校验失败")
                break
            except (OSError, ValueError) as error:
                errors.append(type(error).__name__ + ": " + str(error)[:240])
        else:
            raise ValueError("所有桌面程序下载地址均失败：" + "; ".join(errors))
        unpacked = staging / "unpacked"
        unpacked.mkdir()
        try:
            extract_shell(archive, unpacked)
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError("桌面程序压缩包无效，未安装") from error
        check_shell(unpacked)
        try:
            unpacked.rename(destination)
        except FileExistsError:
            # Another bootstrap may have finished the same immutable version first.
            return check_shell(destination)
    from src.desktop.backups import regular_tree

    for broken in sorted(destination.parent.glob(f"{version}.broken-*"), reverse=True)[1:]:
        regular_tree(broken)
        shutil.rmtree(broken)
    return check_shell(destination)


def extract_shell(archive: Path, destination: Path):
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_FILES or sum(item.file_size for item in members) > MAX_UNPACKED:
            raise ValueError("桌面程序压缩包超过展开限制")
        seen = set()
        for item in members:
            path = PurePosixPath(item.filename)
            parts = path.parts
            if (
                not parts
                or path.is_absolute()
                or "\\" in item.filename
                or any(
                    part in {".", ".."}
                    or ":" in part
                    or part.endswith((".", " "))
                    or re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part)
                    for part in parts
                )
                or stat.S_ISLNK(item.external_attr >> 16)
                or item.filename.casefold().rstrip("/") in seen
            ):
                raise ValueError("桌面程序压缩包包含不安全或重复路径")
            seen.add(item.filename.casefold().rstrip("/"))
            target = destination.joinpath(*parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as content, target.open("xb") as output:
                    shutil.copyfileobj(content, output)
