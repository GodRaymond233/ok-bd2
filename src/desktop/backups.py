"""Configuration snapshots kept outside PyAppify's replaceable working directory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_BACKUPS = 5
MARKER = ".core_version"
BACKUP_ID = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{8}")


def installed_base(working: Path) -> Path | None:
    if (
        working.name.lower() == "working"
        and working.parent.parent.name.lower() == "apps"
        and working.parent.parent.parent.name.lower() == "data"
    ):
        return working.parent
    return None


def normalized_version(value: str) -> str:
    return str(value).strip().removeprefix("v")


class ConfigVersionError(ValueError):
    def __init__(self, previous, version, backups):
        super().__init__(f"配置来自较新版本 {previous}，当前为 {version}。"
                         "请打开客户端恢复与当前版本匹配的备份；原配置尚未改写。")
        self.previous = previous
        self.version = version
        self.backups = backups


def regular_tree(root: Path) -> list[Path]:
    """Never follow links out of the owned data directory, including Windows junctions."""
    if root.is_symlink() or root.is_junction():
        raise ValueError("配置或备份目录不能是符号链接或目录联接")
    result = []
    if not root.exists():
        return result
    if not root.is_dir():
        raise ValueError("配置或备份路径必须是目录")

    def unreadable(error):
        raise error

    for directory, folders, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in folders + files:
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError("配置或备份中存在链接，已停止操作")
            if path.is_file():
                result.append(path)
            elif not path.is_dir():
                raise ValueError("配置或备份中存在非普通文件")
    return sorted(result)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class BackupStore:
    def __init__(self, working: Path, config_folder="configs"):
        self.working = Path(working).absolute()
        folder = Path(config_folder)
        if folder.is_absolute() or ".." in folder.parts or not folder.parts:
            raise ValueError("备份只支持安装目录内的配置文件夹")
        self.configs = self.working / folder
        base = installed_base(self.working)
        self.root = base / "backups" if base else self.working / ".local-dev" / "backups"
        if self.root.is_relative_to(self.configs):
            raise ValueError("备份目录不能位于配置目录内")

    def _root(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.is_junction():
            raise ValueError("备份目录不能是链接")

    def recover_restore(self):
        journal = self.root / "restore.json"
        if not journal.exists():
            return
        self._root()
        transaction = json.loads(journal.read_text("utf-8"))
        previous_name = transaction["previous"]
        if not isinstance(previous_name, str) or not re.fullmatch(
            re.escape(self.configs.name) + r"\.before-restore-[0-9a-f]{32}", previous_name
        ):
            raise ValueError("恢复记录无效，请人工检查备份目录")
        previous = self.configs.with_name(previous_name)
        regular_tree(previous)
        if not self.configs.exists():
            if not previous.exists():
                raise ValueError("恢复中断且找不到原配置，请人工恢复备份")
            previous.rename(self.configs)
        else:
            regular_tree(self.configs)
            if previous.exists():
                shutil.rmtree(previous)
        journal.unlink()

    def create(self, version: str, reason: str, *, prune=True) -> dict:
        self._root()
        files = regular_tree(self.configs)
        now = datetime.now(timezone.utc)
        backup_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
        staging = Path(tempfile.mkdtemp(prefix=".creating-", dir=self.root))
        try:
            contents = staging / "configs"
            contents.mkdir()
            manifest = {}
            for source in files:
                relative = source.relative_to(self.configs)
                destination = contents / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                before = source.stat()
                shutil.copyfile(source, destination)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise RuntimeError("备份期间配置发生变化，请停止任务后重试")
                manifest[relative.as_posix()] = {
                    "size": destination.stat().st_size,
                    "sha256": file_hash(destination),
                }
            if files != regular_tree(self.configs):
                raise RuntimeError("备份期间配置文件列表发生变化")
            for source in files:
                if (
                    file_hash(source)
                    != manifest[source.relative_to(self.configs).as_posix()]["sha256"]
                ):
                    raise RuntimeError("备份期间配置发生变化，请停止任务后重试")
            metadata = {
                "format": 1,
                "id": backup_id,
                "created_at": now.isoformat(),
                "version": normalized_version(version),
                "reason": reason,
                "files": manifest,
            }
            (staging / "manifest.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), "utf-8"
            )
            self._verify(staging)
            staging.rename(self.root / backup_id)
            if prune:
                self.prune()
            return self.describe(metadata)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def describe(metadata):
        files = metadata["files"]
        return {key: metadata[key] for key in ("id", "created_at", "version", "reason")} | {
            "file_count": len(files),
            "bytes": sum(item["size"] for item in files.values()),
        }

    def _verify(self, directory: Path):
        regular_tree(directory)
        metadata = json.loads((directory / "manifest.json").read_text("utf-8"))
        if (
            not isinstance(metadata, dict)
            or metadata.get("format") != 1
            or not isinstance(metadata.get("files"), dict)
        ):
            raise ValueError("备份清单格式不受支持")
        if not (directory / "configs").is_dir():
            raise ValueError("备份缺少配置目录")
        actual = {
            p.relative_to(directory / "configs").as_posix(): p
            for p in regular_tree(directory / "configs")
        }
        if set(actual) != set(metadata["files"]):
            raise ValueError("备份文件列表与清单不一致")
        for name, path in actual.items():
            item = metadata["files"][name]
            if path.stat().st_size != item["size"] or file_hash(path) != item["sha256"]:
                raise ValueError("备份文件校验失败，原配置保持不变")
        self.describe(metadata)
        return metadata

    def read(self, backup_id: str):
        if not isinstance(backup_id, str) or not BACKUP_ID.fullmatch(backup_id):
            raise ValueError("无效的备份编号")
        if self.root.is_symlink() or self.root.is_junction():
            raise ValueError("备份目录不能是链接")
        try:
            metadata = self._verify(self.root / backup_id)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("备份清单损坏") from error
        if metadata["id"] != backup_id:
            raise ValueError("备份编号与清单不一致")
        return metadata

    def list(self):
        if not self.root.exists():
            return []
        self._root()
        result = []
        for directory in sorted(self.root.iterdir(), reverse=True):
            if BACKUP_ID.fullmatch(directory.name):
                try:
                    result.append(self.describe(self.read(directory.name)))
                except (OSError, ValueError, KeyError, TypeError):
                    result.append({"id": directory.name, "invalid": True})
        return result

    def prune(self):
        valid = [item for item in self.list() if not item.get("invalid")]
        for manual in (False, True):
            items = [item for item in valid if (item["reason"] == "manual") == manual]
            for item in items[MAX_BACKUPS:]:
                shutil.rmtree(self.root / item["id"])

    def prepare_startup(self, version: str):
        self.recover_restore()
        version = normalized_version(version)
        regular_tree(self.configs)
        marker = self.configs / MARKER
        previous = marker.read_text("utf-8").strip() if marker.exists() else "unknown"
        if previous == version:
            return None
        if re.fullmatch(r"\d+\.\d+\.\d+", previous) and re.fullmatch(r"\d+\.\d+\.\d+", version):
            if tuple(map(int, previous.split("."))) > tuple(map(int, version.split("."))):
                raise ConfigVersionError(previous, version, [item for item in self.list()
                                         if not item.get("invalid") and item["version"] == version])
        result = self.create(previous, "version-change") if self.configs.exists() else None
        self.configs.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_name(MARKER + ".tmp")
        temporary.write_text(version, "utf-8")
        temporary.replace(marker)
        return result

    def restore(self, backup_id: str, version: str):
        self.recover_restore()
        metadata = self.read(backup_id)
        if metadata["version"] != normalized_version(version):
            raise ValueError("这份备份来自其他版本，请先切换到对应版本再恢复")
        # Retain the selected backup until it has been copied and the replacement succeeds.
        marker = self.configs / MARKER
        current_version = marker.read_text("utf-8").strip() if marker.exists() else "unknown"
        safety = self.create(current_version, "before-restore", prune=False)
        staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=self.working))
        previous = self.configs.with_name(self.configs.name + ".before-restore-" + uuid.uuid4().hex)
        try:
            shutil.copytree(self.root / backup_id / "configs", staging / "configs")
            (staging / "manifest.json").write_text(json.dumps(metadata), "utf-8")
            self._verify(staging)
            journal = self.root / "restore.json"
            temporary = journal.with_suffix(".tmp")
            temporary.write_text(json.dumps({"previous": previous.name}), "utf-8")
            temporary.replace(journal)
            if self.configs.exists():
                self.configs.rename(previous)
            try:
                (staging / "configs").rename(self.configs)
            except BaseException:
                if previous.exists():
                    previous.rename(self.configs)
                journal.unlink()
                raise
            if previous.exists():
                shutil.rmtree(previous)
            journal.unlink()
            self.prune()
            return safety
        finally:
            shutil.rmtree(staging)


def prepare_installed_configs(config):
    """Automation entries call this under the executor lock, before ok.OK."""
    working = Path.cwd()
    if installed_base(working) is not None:
        BackupStore(working, config.get("config_folder", "configs")).prepare_startup(
            str(config["version"])
        )
