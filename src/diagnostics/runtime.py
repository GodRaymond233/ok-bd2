"""Project-owned task evidence; no changes to framework execution semantics."""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import queue
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from src.diagnostics.log_collection import collect, encode_json, redact_tree, sources_for
from src.diagnostics.redaction import DiagnosticRedactor

STORE_LIMIT = 8 * 1024 * 1024
FAILURE_LOG_LIMIT = 512 * 1024
RETENTION_SECONDS = 7 * 24 * 3600
_recorder = None
_run = contextvars.ContextVar("diagnostic_run", default=None)


def safe_info(task, redactor):
    try:
        info = task.info_snapshot()
    except Exception as exc:
        return {"diagnostic_info_omission": type(exc).__name__}
    result = {}
    for key, value in info.items():
        key = redactor.redact(key)
        value = (
            value
            if value is None or isinstance(value, (bool, int, float))
            else redactor.redact(value)
        )
        result.update(redact_tree({key: value}, redactor))
        if len(encode_json(result)) > 32 * 1024:
            result.pop(key)
            result["diagnostic_info_omission"] = "32KiB limit"
            break
    return result


def observe_run(method):
    @functools.wraps(method)
    def observed(task, *args, **kwargs):
        recorder = _recorder
        parent = _run.get()
        if recorder is None or (parent and parent["object"] is task):
            return method(task, *args, **kwargs)
        executor = getattr(task, "_executor", None)
        trigger = any(item is task for item in getattr(executor, "trigger_tasks", ()))
        run = {
            "id": uuid.uuid4().hex,
            "parent": parent["id"] if parent else None,
            "parent_task": parent["task"] if parent else None,
            "task": type(task).__name__,
            "object": task,
            "trigger": trigger,
            "started": time.time(),
            "initial_error": safe_info(task, recorder.redactor).get("Error"),
        }
        token = _run.set(run)
        if not trigger:
            recorder.event(run, "started")
        try:
            result = method(task, *args, **kwargs)
        except Exception as exc:
            from ok.task.exceptions import FinishedException, TaskDisabledException

            outcome = (
                "stopped"
                if isinstance(exc, (TaskDisabledException, FinishedException))
                else "exception"
            )
            recorder.event(run, outcome, detail="".join(traceback.format_exception(exc)))
            if outcome == "stopped" and parent is not None:
                parent["stopped"] = True
            raise
        else:
            info = safe_info(task, recorder.redactor)
            failed = not trigger and (
                result is False
                or bool(info.get("Error"))
                or any(word in str(info.get("状态", "")) for word in ("失败", "中止"))
            )
            new_error = run.get("explicit_error") or (
                info.get("Error") and info.get("Error") != run["initial_error"]
            )
            if not trigger or new_error:
                outcome = "failed" if failed or new_error else "finished"
                recorder.event(run, "stopped" if run.get("stopped") else outcome)
            return result
        finally:
            _run.reset(token)

    return observed


def phase_changed(key, value):
    run = _run.get()
    if (
        _recorder is not None
        and run is not None
        and key in ("状态", "阶段", "当前阶段", "当前子任务")
    ):
        _recorder.event(run, "phase", detail=f"{key}={value}")


def task_error():
    run = _run.get()
    if run is not None:
        run["explicit_error"] = True


class RuntimeEvidence(logging.Filter):
    def __init__(self, root: Path, *, executor_getter=lambda: None):
        super().__init__()
        self.root = root
        self.path = root / "logs" / "diagnostic-evidence.json"
        self.redactor = DiagnosticRedactor(known_roots=[root])
        self.executor_getter = executor_getter
        self.session = uuid.uuid4().hex
        self.events, self.failures, self.omissions = [], [], []
        self.dropped = 0
        self.observed_exceptions = {}
        self.guard = threading.RLock()
        self.pending = queue.Queue(maxsize=128)
        self._load()
        self.worker = threading.Thread(target=self._work, name="BD2DiagnosticEvidence", daemon=True)
        self.worker.start()

    def _load(self):
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(STORE_LIMIT + 1)
            if len(raw) > STORE_LIMIT:
                raise ValueError("size")
            data = json.loads(raw)
            if data.get("version") != 1 or not isinstance(data.get("failures"), list):
                raise ValueError("schema")
            for failure in data["failures"]:
                files = failure["logs"]["files"]
                if (
                    not set(files).issubset({"incidents.log", "recent.log", "recent-digest.json"})
                    or sum(len(text.encode("utf-8")) for text in files.values()) > FAILURE_LOG_LIMIT
                ):
                    raise ValueError("failure log bounds")
                if "recent-digest.json" in files and not isinstance(
                    json.loads(files["recent-digest.json"]), dict
                ):
                    raise ValueError("failure digest schema")
            self.events = [
                event
                for event in data.get("events", [])
                if event["time"] >= time.time() - RETENTION_SECONDS
            ][-200:]
            self.failures = [
                f for f in data["failures"] if f["time"] >= time.time() - RETENTION_SECONDS
            ][-5:]
            self.omissions = data.get("omissions", [])[-20:]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.events, self.failures = [], []
            self.omissions.append("persisted_evidence_unreadable")

    def event(self, run, outcome, detail=""):
        if outcome == "exception":
            self.observed_exceptions[id(run["object"])] = time.monotonic()
            if len(self.observed_exceptions) > 32:
                self.observed_exceptions.pop(next(iter(self.observed_exceptions)))
        now = time.time()
        item = {
            "session_id": self.session,
            "run": run["id"],
            "parent": run.get("parent"),
            "parent_task": run.get("parent_task"),
            "task": run["task"],
            "started": run["started"],
            "time": now,
            "occurred_at": datetime.fromtimestamp(now)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "event": outcome,
            "info": safe_info(run["object"], self.redactor),
            "detail": self.redactor.redact(detail),
        }
        if len(item["detail"].encode("utf-8")) > 64 * 1024:
            item["detail"] = (
                item["detail"].encode("utf-8")[: 64 * 1024].decode("utf-8", errors="ignore")
            )
            item["detail_omitted"] = "exception/detail exceeded 64KiB"
        self._enqueue(item)

    def _enqueue(self, item):
        try:
            self.pending.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def emit(self, record):
        # Only framework exception-stop messages are runtime evidence. OCR text
        # and ordinary WARNING records remain severity-only log candidates.
        if (
            not record.getMessage().startswith("TaskExecutor:")
            or " exception stopped" not in record.getMessage()
        ):
            return
        if _run.get() is not None:
            return
        try:
            task = getattr(self.executor_getter(), "current_task", None)
            if task is None:
                return
            if time.monotonic() - self.observed_exceptions.get(id(task), -100) < 2:
                return
            self.event(
                {
                    "id": f"executor-{getattr(task, 'start_time', record.created)}",
                    "task": type(task).__name__,
                    "object": task,
                    "started": getattr(task, "start_time", record.created),
                },
                "exception",
                record.getMessage(),
            )
        except Exception:
            self.dropped += 1

    def filter(self, record):
        # Framework reconfiguration replaces handlers but retains logger filters.
        # Observe exception stops while allowing every original record through.
        if record.levelno >= logging.ERROR:
            self.emit(record)
        return True

    def _work(self):
        while True:
            item = self.pending.get()
            try:
                if item is None:
                    return
                if isinstance(item, threading.Event):
                    item.set()
                    continue
                self._accept(item)
            except Exception as exc:
                with self.guard:
                    self.omissions.append(f"evidence_write_failed:{type(exc).__name__}")
                    self.omissions = self.omissions[-20:]
            finally:
                self.pending.task_done()

    def _accept(self, item):
        failure = item["event"] in ("failed", "exception")
        with self.guard:
            self.events.append(item)
            self.events = self.events[-200:]
            previous = next(
                (
                    f
                    for f in reversed(self.failures)
                    if (
                        f["run"] == item["run"]
                        or f.get("parent") == item["run"]
                        or item.get("parent") == f["run"]
                    )
                ),
                None,
            )
            if failure and previous:
                previous["count"] += 1
                previous["last_time"] = item["time"]
                previous.setdefault("related_events", []).append(item)
                previous["related_events"] = previous["related_events"][-10:]
                while len(encode_json(previous["related_events"])) > 64 * 1024:
                    previous["related_events"].pop(0)
                    previous["related_events_omitted"] = "64KiB limit"
                failure = False
        if failure:
            saved = {
                **item,
                "count": 1,
                "last_time": item["time"],
                "logs": {"files": {}, "sources": [], "omissions": ["failure_logs_pending"]},
            }
            with self.guard:
                self.failures.append(saved)
            self._persist()
            # This is a dedicated worker, never the logger's emit callback.
            from src.diagnostics.bundle import flush_ok_logging

            flushed = flush_ok_logging()
            try:
                logs = collect(
                    sources_for(self.root),
                    item["time"],
                    self.redactor,
                    budget=FAILURE_LOG_LIMIT,
                    anchors=[item],
                )
            except Exception as exc:
                logs = {
                    "files": {},
                    "sources": [],
                    "omissions": [f"failure_logs_unavailable:{type(exc).__name__}"],
                }
            if not flushed:
                logs["omissions"].append("failure_log_flush_incomplete")
            with self.guard:
                saved["logs"] = logs
        self._persist()

    def _persist(self):
        with self.guard:
            self.failures = [
                f for f in self.failures if f["time"] >= time.time() - RETENTION_SECONDS
            ]
            if len(self.events) == 200:
                self.omissions = list(dict.fromkeys(self.omissions + ["trace_event_limit"]))
            if len(self.failures) > 5:
                self.omissions.append(f"failure_count_limit:{len(self.failures) - 5}")
                self.failures = self.failures[-5:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "events": self.events,
                "failures": self.failures,
                "omissions": self.omissions[-20:]
                + ([f"queue_events_dropped:{self.dropped}"] if self.dropped else []),
            }
            while len(encode_json(payload)) > STORE_LIMIT and payload["events"]:
                payload["events"].pop(0)
                payload["omissions"] = list(
                    dict.fromkeys(payload["omissions"] + ["event_store_byte_limit"])
                )
            while len(encode_json(payload)) > STORE_LIMIT and payload["failures"]:
                payload["failures"].pop(0)
                payload["omissions"] = list(
                    dict.fromkeys(payload["omissions"] + ["failure_store_byte_limit"])
                )
            temporary = self.path.with_suffix(".tmp")
            temporary.write_bytes(encode_json(payload))
            temporary.replace(self.path)

    def snapshot(self, timeout=3.0):
        barrier = threading.Event()
        try:
            self.pending.put(barrier, timeout=timeout)
            ready = barrier.wait(timeout)
        except queue.Full:
            ready = False
        with self.guard:
            result = json.loads(
                encode_json(
                    {
                        "events": self.events,
                        "failures": self.failures,
                        "omissions": self.omissions[-20:],
                    }
                )
            )
        if not ready:
            result["omissions"].append("evidence_worker_pending")
        if self.dropped:
            result["omissions"].append(f"queue_events_dropped:{self.dropped}")
        return result

    def stop(self):
        global _recorder
        if _recorder is self:
            _recorder = None
        logging.getLogger("ok").removeFilter(self)
        try:
            self.pending.put(None, timeout=1)
        except queue.Full:
            return
        self.worker.join(timeout=5)


def install(root):
    global _recorder
    if _recorder is None:
        from ok import og

        _recorder = RuntimeEvidence(root, executor_getter=lambda: getattr(og, "executor", None))
    logger = logging.getLogger("ok")
    if _recorder not in logger.filters:
        logger.addFilter(_recorder)
    return _recorder


def evidence_for(root):
    if _recorder is not None and _recorder.root.resolve() == root.resolve():
        return _recorder.snapshot()
    # Report tooling can read persisted evidence without installing task hooks.
    reader = RuntimeEvidence(root)
    try:
        return reader.snapshot()
    finally:
        reader.stop()
