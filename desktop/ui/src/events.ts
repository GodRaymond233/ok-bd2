// The core's event stream (WebSocket) with reconnect.
import type { EventPayload } from "./types.ts";

export interface EventConnection {
  close(): void;
}

export function connectEvents(
  sessionKey: string,
  onEvent: (payload: EventPayload) => void,
  onState: (connected: boolean) => void,
): EventConnection {
  let socket: WebSocket | null = null;
  let closed = false;
  let delay = 1000;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    const url = `ws://${location.host}/api/events?session_key=${encodeURIComponent(sessionKey)}`;
    socket = new WebSocket(url);
    socket.onopen = () => {
      delay = 1000;
      onState(true);
    };
    socket.onmessage = (message) => {
      let payload: EventPayload;
      try {
        payload = JSON.parse(String(message.data)) as EventPayload;
      } catch {
        return; // ignore malformed frames
      }
      onEvent(payload);
    };
    socket.onclose = () => {
      onState(false);
      if (!closed) {
        timer = setTimeout(open, delay);
        delay = Math.min(delay * 2, 10_000);
      }
    };
  };
  open();
  return {
    close() {
      closed = true;
      if (timer !== null) {
        clearTimeout(timer);
      }
      socket?.close();
    },
  };
}
