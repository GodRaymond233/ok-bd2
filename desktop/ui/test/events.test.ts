import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { connectEvents } from "../src/events.ts";
import type { EventPayload } from "../src/types.ts";

class FakeSocket {
  static latest: FakeSocket;
  onmessage: ((message: { data: string }) => void) | null = null;

  constructor() {
    FakeSocket.latest = this;
  }

  close(): void {}
}

const globals = globalThis as unknown as Record<string, unknown>;
const realSocket = globals.WebSocket;

before(() => {
  globals.WebSocket = FakeSocket;
  Object.defineProperty(globalThis, "location", { value: { host: "core" }, configurable: true });
});

after(() => {
  globals.WebSocket = realSocket;
  delete globals.location;
});

function deliver(data: string): void {
  FakeSocket.latest.onmessage?.({ data });
}

test("a malformed frame is ignored and the next one still arrives", () => {
  const received: EventPayload[] = [];
  const connection = connectEvents("key", (payload) => received.push(payload), () => undefined);
  deliver("not json");
  assert.deepEqual(received, []);
  deliver('{"event":"status"}');
  assert.deepEqual(received, [{ event: "status" }]);
  connection.close();
});

test("an error in the handler is not mistaken for a malformed frame", () => {
  let delivered = 0;
  const connection = connectEvents(
    "key",
    () => {
      delivered += 1;
      if (delivered === 1) {
        throw new Error("handler bug");
      }
    },
    () => undefined,
  );
  assert.throws(() => deliver('{"event":"status"}'), /handler bug/);
  deliver('{"event":"status"}');
  assert.equal(delivered, 2);
  connection.close();
});
