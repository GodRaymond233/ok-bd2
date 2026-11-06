"""Bound the producer queue before crossing from task threads into asyncio.

Ported from ``codex/webui-refactor`` (``b8f623d``) unchanged in behavior.
"""

import asyncio
import secrets
import threading
import time
from collections import deque

from fastapi import WebSocket, WebSocketDisconnect
from ok.core.events import communicate

from src.desktop.okweb.app import _json_value


class EventBuffer:
    def __init__(self, loop, limit=256):
        self.loop = loop
        self.limit = limit
        self.queue = deque()
        self.lock = threading.Lock()
        self.ready = asyncio.Event()
        self.scheduled = False
        self.closed = False
        self.dropped = 0
        self.sequence = 0
        self.last_telemetry = {}

    def receive(self, event):
        if event.name in {"draw_box", "clear_box", "window", "blur_overlay", "clear_blur_overlay"}:
            return
        with self.lock:
            if self.closed:
                return
            if event.name in {"fps", "frame_time"}:
                now = time.monotonic()
                if now - self.last_telemetry.get(event.name, 0) < 0.25:
                    return
                self.last_telemetry[event.name] = now
            self.sequence += 1
            payload = {
                "event": event.name,
                "args": _json_value(event.args),
                "kwargs": _json_value(event.kwargs),
                "seq": self.sequence,
            }
            if len(self.queue) >= self.limit:
                log_index = next(
                    (i for i, item in enumerate(self.queue) if item["event"] == "log"), None
                )
                if log_index is not None:
                    del self.queue[log_index]
                else:
                    self.queue.popleft()
                self.dropped += 1
            self.queue.append(payload)
            if not self.scheduled:
                self.scheduled = True
                try:
                    self.loop.call_soon_threadsafe(self.ready.set)
                except RuntimeError:
                    self.closed = True
                    self.queue.clear()

    def drain(self):
        with self.lock:
            messages = list(self.queue)
            self.queue.clear()
            dropped, self.dropped = self.dropped, 0
            self.ready.clear()
            self.scheduled = False
        return messages, dropped

    def close(self):
        with self.lock:
            self.closed = True
            self.queue.clear()


def install_events(app, runtime):
    app.router.routes = [
        route for route in app.router.routes if getattr(route, "path", None) != "/api/events"
    ]

    @app.websocket("/api/events")
    async def events(websocket: WebSocket, session_key: str = ""):
        if not secrets.compare_digest(session_key, runtime.event_session_key):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        buffer = EventBuffer(asyncio.get_running_loop())
        communicate.any.connect(buffer.receive)

        async def disconnected():
            while (await websocket.receive()).get("type") != "websocket.disconnect":
                pass

        disconnect = asyncio.create_task(disconnected())
        wake = None
        try:
            while True:
                wake = asyncio.create_task(buffer.ready.wait())
                done, _ = await asyncio.wait(
                    (wake, disconnect), return_when=asyncio.FIRST_COMPLETED
                )
                if disconnect in done:
                    break
                messages, dropped = buffer.drain()
                needs_ui = dropped or any(
                    item["event"]
                    in {
                        "adb_devices",
                        "executor_paused",
                        "executor_exclusive",
                        "starting_emulator",
                        "task",
                        "task_list_updated",
                    }
                    for item in messages
                )
                snapshot = await asyncio.to_thread(runtime.capture_ui) if needs_ui else None
                for payload in messages:
                    if payload["event"] in {
                        "adb_devices",
                        "executor_paused",
                        "executor_exclusive",
                        "starting_emulator",
                        "task",
                        "task_list_updated",
                    }:
                        payload["ui"] = snapshot
                    await websocket.send_json(payload)
                if dropped:
                    await websocket.send_json(
                        {
                            "event": "log",
                            "args": [
                                30,
                                f"界面队列繁忙，已省略 {dropped} 条通知；请查看日志文件。",
                            ],
                            "kwargs": {},
                        }
                    )
                    # A task refresh preserves custom-tab drafts; navigation reloads do not.
                    await websocket.send_json(
                        {
                            "event": "task",
                            "args": [],
                            "kwargs": {},
                            "ui": snapshot,
                            "resync": True,
                        }
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            buffer.close()
            communicate.any.disconnect(buffer.receive)
            for task in (wake, disconnect):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (wake, disconnect) if task is not None), return_exceptions=True
            )
