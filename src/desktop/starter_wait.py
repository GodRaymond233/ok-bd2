"""Expose the Starter wait (``src.compat.starter_guard``) to the desktop client.

The Qt window shows the wait in its starting dialog with a cancel button
(``src.compat.starter_wait_ui``).  Here the latest status is kept for ``capture_ui``, each
change is pushed on the event stream as ``starter_wait``, and ``cancel`` sets the wait's
own cancel event, which ends the wait as a failed game start.
"""

import threading
import time

from ok.core.events import EventMessage, communicate

from src.compat.starter_guard import starter_wait_status


class StarterWait:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.cancel_event = None
        self.message = ""
        self.seconds_left = 0
        self.since = None

    def install(self):
        starter_wait_status.connect(self.on_status)
        communicate.starting_emulator.connect(self.on_starting)

    def uninstall(self):
        starter_wait_status.disconnect(self.on_status)
        communicate.starting_emulator.disconnect(self.on_starting)

    def on_status(self, message, cancel_event):
        with self.lock:
            if cancel_event.is_set():
                return
            if cancel_event is not self.cancel_event:
                self.cancel_event = cancel_event
                self.since = self.clock()
            self.message = str(message or "")
        self.publish()

    def on_starting(self, done, error=None, seconds=0):
        with self.lock:
            if done:
                self.cancel_event = None
                self.message = ""
                self.seconds_left = 0
                self.since = None
            else:
                self.seconds_left = int(seconds or 0)

    def snapshot(self):
        """The wait in progress for ``capture_ui``, or None."""
        with self.lock:
            if self.cancel_event is None or self.cancel_event.is_set():
                return None
            return {
                "message": self.message,
                "seconds_left": self.seconds_left,
                "waited": max(0, int(self.clock() - self.since)),
            }

    def cancel(self):
        """End the current wait; False when no wait is in progress."""
        with self.lock:
            event = self.cancel_event
            if event is None or event.is_set():
                return False
            event.set()
        self.publish()
        return True

    def publish(self):
        communicate.any.emit(EventMessage("starter_wait", (), {}))


# One per process: starter_wait_status is a module-level signal.
STARTER_WAIT = StarterWait()
