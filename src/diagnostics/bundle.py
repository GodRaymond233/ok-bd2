from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.diagnostics.models import DiagnosticSnapshot, ReportResult
from src.diagnostics.redaction import DiagnosticRedactor

SCHEMA_VERSION = 1
MAX_DESCRIPTION_CHARS = 2000
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 6 * 1024 * 1024
MAX_ARCHIVE_BYTES = 15 * 1024 * 1024
MAX_DIAGNOSTIC_FRAME_COUNT = 8
MAX_DIAGNOSTIC_FRAME_BYTES = 4 * 1024 * 1024
MAX_SINGLE_DIAGNOSTIC_FRAME_BYTES = 700 * 1024
MAX_DIAGNOSTIC_FRAME_LOOKBACK_SECONDS = 15 * 60
DIAGNOSTIC_FRAME_CLOCK_SKEW_SECONDS = 5


class ReportBundleBuilder:
    def __init__(
        self,
        *,
        project_root: Path,
        output_dir: Path,
        app_version: str,
        redactor: DiagnosticRedactor | None = None,
    ):
        self.project_root = project_root.resolve()
        self.output_dir = output_dir
        self.app_version = str(app_version)
        self.redactor = redactor or DiagnosticRedactor(known_roots=[self.project_root])

    def build(
        self,
        snapshot: DiagnosticSnapshot,
        description: str,
        *,
        include_screenshot: bool,
    ) -> ReportResult:
        description = self._normalize_description(description)
        report_id = _new_report_id()
        archive_name = f"ok-bd2-report-{report_id}.zip"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.output_dir / archive_name

        with tempfile.TemporaryDirectory(
            prefix=f".{report_id}-",
        ) as temp_dir_name:
            stage = Path(temp_dir_name)
            files: list[dict[str, Any]] = []
            omissions: list[str] = []

            summary = self._summary_text(report_id, snapshot, description)
            _write_text(stage / "summary.txt", summary)
            files.append(_file_record(stage, "summary.txt"))

            task_payload = {
                "captured_at": snapshot.captured_at,
                "safe_point_reached": snapshot.safe_point_reached,
                "task": self._redact_mapping(snapshot.task),
            }
            _write_json(stage / "state" / "task-summary.json", task_payload)
            files.append(_file_record(stage, "state/task-summary.json"))

            trace_payload = {
                "timestamp": snapshot.captured_at,
                "event": "report_snapshot",
                "safe_point_reached": snapshot.safe_point_reached,
                "task": self._redact_mapping(snapshot.task),
            }
            _write_text(
                stage / "state" / "trace.jsonl",
                json.dumps(trace_payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            )
            files.append(_file_record(stage, "state/trace.jsonl"))

            if not flush_ok_logging():
                omissions.append("log_flush_incomplete")
            log_text, log_sources = self._recent_logs()
            if log_text:
                _write_text(stage / "logs" / "recent.log", log_text)
                files.append(_file_record(stage, "logs/recent.log"))
            else:
                omissions.append("recent_log_unavailable")

            screenshot_meta: dict[str, Any] = {"included": False}
            if include_screenshot and snapshot.frame is not None:
                encoded, screenshot_meta = _encode_frame(snapshot.frame)
                if encoded is not None:
                    screenshot_path = stage / "screenshots" / "current.webp"
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    screenshot_path.write_bytes(encoded)
                    files.append(_file_record(stage, "screenshots/current.webp"))
                else:
                    omissions.append("screenshot_exceeded_limit")
            elif include_screenshot:
                omissions.append("screenshot_unavailable")
            else:
                omissions.append("screenshot_declined")

            if include_screenshot:
                diagnostic_frames, diagnostic_omissions = self._diagnostic_frames(
                    stage,
                    snapshot,
                )
            else:
                diagnostic_frames = {"files": [], "metadata": []}
                diagnostic_omissions = ["diagnostic_frames_declined"]
            files.extend(diagnostic_frames["files"])
            omissions.extend(diagnostic_omissions)

            manifest = {
                "schema_version": SCHEMA_VERSION,
                "report_id": report_id,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "application": {"name": "ok-bd2", "version": self.app_version},
                "system": {
                    "name": platform.system() or "unknown",
                    "release": platform.release() or "unknown",
                    "architecture": platform.machine() or "unknown",
                },
                "capture": {
                    **screenshot_meta,
                    "method": self.redactor.redact(snapshot.capture_method or "unknown"),
                    "frame_age_seconds": snapshot.frame_age_seconds,
                    "diagnostic_frames": diagnostic_frames["metadata"],
                },
                "task": self._redact_mapping(snapshot.task),
                "safe_point_reached": snapshot.safe_point_reached,
                "warnings": [self.redactor.redact(item) for item in snapshot.warnings],
                "privacy": {
                    "redacted": True,
                    "raw_config_included": False,
                    "environment_included": False,
                    "process_list_included": False,
                    "machine_or_user_name_included": False,
                },
                "limits": {
                    "log_bytes": MAX_LOG_BYTES,
                    "screenshot_bytes": MAX_SCREENSHOT_BYTES,
                    "archive_bytes": MAX_ARCHIVE_BYTES,
                },
                "log_sources": [self.redactor.redact(name) for name in log_sources],
                "omissions": omissions,
                "files": files,
            }
            _write_json(stage / "manifest.json", manifest)

            checksum_lines = []
            for relative_path in sorted(
                path.relative_to(stage).as_posix()
                for path in stage.rglob("*")
                if path.is_file()
            ):
                digest = _sha256(stage / relative_path)
                checksum_lines.append(f"{digest}  {relative_path}")
            _write_text(stage / "checksums.sha256", "\n".join(checksum_lines) + "\n")

            temporary_archive = self.output_dir / f".{archive_name}.tmp"
            try:
                with zipfile.ZipFile(
                    temporary_archive,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    for path in sorted(stage.rglob("*")):
                        if path.is_file():
                            archive.write(path, path.relative_to(stage).as_posix())
                if temporary_archive.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("诊断包超过 15 MiB 安全上限")
                os.replace(temporary_archive, archive_path)
            finally:
                temporary_archive.unlink(missing_ok=True)

        group_message = (
            "【ok-bd2 问题反馈】\n"
            f"报告编号：{report_id}\n"
            f"程序版本：{self.app_version}\n"
            f"问题现象：{description}\n"
            "附件：请发送同编号的 ok-bd2-report ZIP 文件"
        )
        return ReportResult(
            report_id=report_id,
            archive_path=archive_path,
            group_message=group_message,
            manifest=manifest,
        )

    def _diagnostic_frames(
        self,
        stage: Path,
        snapshot: DiagnosticSnapshot,
    ) -> tuple[dict[str, Any], list[str]]:
        """Package failure frames from the current diagnostic window only."""

        source_dir = self.project_root / "probe_outputs"
        try:
            captured_at = datetime.fromisoformat(snapshot.captured_at).timestamp()
        except (TypeError, ValueError, OverflowError):
            return {"files": [], "metadata": []}, ["diagnostic_frames_unavailable"]

        lower_bound = captured_at - MAX_DIAGNOSTIC_FRAME_LOOKBACK_SECONDS
        if snapshot.task_started_at is not None:
            try:
                task_started_at = float(snapshot.task_started_at)
            except (TypeError, ValueError):
                task_started_at = None
            if task_started_at is not None and task_started_at > 0:
                lower_bound = max(lower_bound, task_started_at)
        upper_bound = captured_at + DIAGNOSTIC_FRAME_CLOCK_SKEW_SECONDS

        matching_files: list[Path] = []
        candidates: list[tuple[Path, float]] = []
        try:
            for path in source_dir.glob("*.png"):
                if not path.is_file() or not path.stem.endswith(("_failed", "_error")):
                    continue
                try:
                    modified_at = path.stat().st_mtime
                except OSError:
                    continue
                matching_files.append(path)
                if lower_bound <= modified_at <= upper_bound:
                    candidates.append((path, modified_at))
        except OSError:
            return {"files": [], "metadata": []}, ["diagnostic_frames_unavailable"]

        candidates.sort(key=lambda item: item[1], reverse=True)
        candidates = candidates[:MAX_DIAGNOSTIC_FRAME_COUNT]
        metadata: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        omissions: list[str] = []
        total_bytes = 0

        for source, modified_at in candidates:
            try:
                # cv2.imread does not reliably handle non-ASCII Windows paths.
                frame = cv2.imdecode(
                    np.fromfile(source, dtype=np.uint8),
                    cv2.IMREAD_UNCHANGED,
                )
                if frame is None:
                    raise ValueError("无法读取图片")
                encoded, image_meta = _encode_frame(
                    frame,
                    max_bytes=MAX_SINGLE_DIAGNOSTIC_FRAME_BYTES,
                )
                if encoded is None:
                    omissions.append(f"diagnostic_frame_exceeded_limit:{source.name}")
                    continue
                if total_bytes + len(encoded) > MAX_DIAGNOSTIC_FRAME_BYTES:
                    omissions.append("diagnostic_frames_exceeded_limit")
                    break

                relative_path = f"screenshots/diagnostic/{source.stem}.webp"
                output_path = stage / relative_path
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(encoded)
                files.append(_file_record(stage, relative_path))
                metadata.append(
                    {
                        "path": relative_path,
                        "source": source.name,
                        "captured_at": datetime.fromtimestamp(
                            modified_at
                        ).astimezone().isoformat(timespec="seconds"),
                        "original_resolution": image_meta.get("original_resolution"),
                        "exported_resolution": image_meta.get("exported_resolution"),
                        "format": image_meta.get("format", "webp"),
                    }
                )
                total_bytes += len(encoded)
            except (OSError, ValueError, cv2.error):
                omissions.append(f"diagnostic_frame_unavailable:{source.name}")
                continue

        if not candidates:
            omissions.append(
                "diagnostic_frames_outside_window"
                if matching_files
                else "diagnostic_frames_unavailable"
            )
        return {"files": files, "metadata": metadata}, omissions

    def _normalize_description(self, description: str) -> str:
        normalized = " ".join(str(description).split())[:MAX_DESCRIPTION_CHARS]
        if not normalized:
            raise ValueError("请先填写问题现象")
        return self.redactor.redact(normalized)

    def _summary_text(
        self,
        report_id: str,
        snapshot: DiagnosticSnapshot,
        description: str,
    ) -> str:
        task_name = self.redactor.redact(snapshot.task.get("class", "未运行任务"))
        task_state = self.redactor.redact(snapshot.task.get("状态", "-"))
        phase = self.redactor.redact(snapshot.task.get("当前阶段", "-"))
        return (
            "ok-bd2 标准问题报告\n"
            f"报告编号：{report_id}\n"
            f"生成时间：{snapshot.captured_at}\n"
            f"程序版本：{self.app_version}\n"
            f"问题现象：{description}\n"
            f"当前任务：{task_name}\n"
            f"任务状态：{task_state}\n"
            f"当前阶段：{phase}\n"
            f"安全暂停点：{'已确认' if snapshot.safe_point_reached else '未确认'}\n\n"
            "隐私说明：本报告仅包含受限运行信息、脱敏后的最近日志，以及用户确认附带的游戏窗口截图；"
            "不包含原始配置、环境变量、进程列表、用户名或机器名。\n"
        )

    def _redact_mapping(self, values: dict[str, Any]) -> dict[str, Any]:
        return {
            self.redactor.redact(key): (
                value
                if value is None or isinstance(value, bool | int | float)
                else self.redactor.redact(value)
            )
            for key, value in values.items()
        }

    def _recent_logs(self) -> tuple[str, list[str]]:
        candidates = _active_log_candidates(self.project_root)
        if not candidates:
            return "", []

        remaining = MAX_LOG_BYTES
        chunks: list[str] = []
        sources: list[str] = []
        for path in candidates[:2]:
            if remaining <= 0:
                break
            raw = _read_tail(path, remaining)
            if not raw:
                continue
            redacted = self.redactor.redact(raw)
            encoded = redacted.encode("utf-8")
            if len(encoded) > remaining:
                encoded = encoded[-remaining:]
                redacted = encoded.decode("utf-8", errors="ignore")
            header = f"===== {path.name} (recent tail) =====\n"
            header_bytes = len(header.encode("utf-8"))
            if header_bytes >= remaining:
                break
            if header_bytes + len(redacted.encode("utf-8")) > remaining:
                allowed = max(0, remaining - header_bytes)
                redacted_bytes = redacted.encode("utf-8")
                redacted = (
                    redacted_bytes[-allowed:].decode("utf-8", errors="ignore")
                    if allowed
                    else ""
                )
            chunk = header + redacted.rstrip() + "\n"
            chunk_size = len(chunk.encode("utf-8"))
            chunks.append(chunk)
            sources.append(path.name)
            remaining -= chunk_size
        return "\n".join(chunks), sources


def flush_ok_logging(timeout: float = 1.0) -> bool:
    """Best-effort bounded flush without stopping ok-script's log listener."""

    try:
        import ok.util.logger as logger_module

        queue_handler = getattr(logger_module, "_queue_handler", None)
        log_queue = getattr(queue_handler, "queue", None)
        deadline = time.monotonic() + max(0.0, timeout)
        while log_queue is not None and getattr(log_queue, "unfinished_tasks", 0):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        file_handler = getattr(logger_module, "_file_handler", None)
        if file_handler is not None:
            file_handler.flush()
        return True
    except Exception:
        return False


def _active_log_candidates(project_root: Path) -> list[Path]:
    candidates: list[Path] = []
    try:
        import ok.util.logger as logger_module

        file_handler = getattr(logger_module, "_file_handler", None)
        active_path = getattr(file_handler, "baseFilename", None)
        if active_path:
            candidates.append(Path(active_path))
    except Exception:
        pass

    log_dir = project_root / "logs"
    candidates.extend(
        [
            log_dir / "ok-script.log",
            log_dir / "ok-bd2.log",
            log_dir / "ok-bd2_error.log",
        ]
    )
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_file():
            continue
        seen.add(key)
        result.append(resolved)
    return result


def _read_tail(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        start = max(0, size - limit)
        stream.seek(start)
        raw = stream.read(limit)
    text = raw.decode("utf-8", errors="replace")
    if start > 0 and "\n" in text:
        text = text.split("\n", 1)[1]
    return text


def _encode_frame(
    frame,
    *,
    max_bytes: int = MAX_SCREENSHOT_BYTES,
) -> tuple[bytes | None, dict[str, Any]]:
    image = frame.copy()
    height, width = image.shape[:2]
    original_resolution = f"{width}x{height}"
    attempts = ((2560, 82), (1920, 70), (1600, 58))
    for max_width, quality in attempts:
        candidate = image
        if width > max_width:
            ratio = max_width / width
            candidate = cv2.resize(
                image,
                (max_width, max(1, round(height * ratio))),
                interpolation=cv2.INTER_AREA,
            )
        success, encoded = cv2.imencode(
            ".webp",
            candidate,
            [cv2.IMWRITE_WEBP_QUALITY, quality],
        )
        if success and len(encoded) <= max_bytes:
            exported_height, exported_width = candidate.shape[:2]
            return encoded.tobytes(), {
                "included": True,
                "original_resolution": original_resolution,
                "exported_resolution": f"{exported_width}x{exported_height}",
                "format": "webp",
                "quality": quality,
            }
    return None, {
        "included": False,
        "original_resolution": original_resolution,
    }


def _new_report_id() -> str:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"RPT-{timestamp}-{secrets.token_hex(4).upper()}"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_record(stage: Path, relative_path: str) -> dict[str, Any]:
    path = stage / relative_path
    return {
        "path": relative_path,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
