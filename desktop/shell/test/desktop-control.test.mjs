import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { ExecutionToggle, HotkeyBinding, QuitGate } = require("../dist/desktop-control.js");

function answer(status, body) {
  return {
    status,
    json: async () => {
      if (body === undefined) {
        throw new SyntaxError("not JSON");
      }
      return body;
    },
  };
}

test("toggle: a refusal carries the core's reason and an unclear result is marked unknown", async () => {
  const failures = [];
  const toggle = new ExecutionToggle((failure) => failures.push(failure));
  await toggle.run(async () => answer(200, {}));
  assert.deepEqual(failures, []);
  await toggle.run(async () => answer(400, { detail: "正在启动游戏" }));
  assert.deepEqual(failures.pop(), { kind: "refused", detail: "正在启动游戏" });
  await toggle.run(async () => answer(400));
  assert.deepEqual(failures.pop(), { kind: "refused", detail: "任务控制失败" });
  await toggle.run(async () => answer(500));
  assert.equal(failures.pop().kind, "unknown");
  await toggle.run(async () => {
    throw new Error("timed out");
  });
  assert.deepEqual(failures.pop(), { kind: "unknown", detail: "timed out" });
});

test("toggle: a press during a request is ignored, one during an open dialog is not", async () => {
  let release;
  let sent = 0;
  const failures = [];
  const toggle = new ExecutionToggle((failure) => failures.push(failure));
  const first = toggle.run(() => {
    sent += 1;
    return new Promise((resolve) => {
      release = resolve;
    });
  });
  await toggle.run(async () => {
    sent += 1;
    return answer(200, {});
  });
  assert.equal(sent, 1);
  release(answer(400, { detail: "拒绝" }));
  await first;
  // onFailure is where main.ts opens its dialog, and that dialog is never awaited here.
  assert.equal(failures.length, 1);
  await toggle.run(async () => {
    sent += 1;
    return answer(200, {});
  });
  assert.equal(sent, 2);
});

function shortcuts(occupied = new Set()) {
  const held = new Set();
  return {
    held,
    occupied,
    register(key) {
      if (occupied.has(key)) {
        return false;
      }
      held.add(key);
      return true;
    },
    unregister(key) {
      held.delete(key);
    },
  };
}

test("hotkey: follows the setting and releases the old key", () => {
  const api = shortcuts();
  const binding = new HotkeyBinding(api, () => {});
  binding.apply("F9");
  assert.deepEqual([...api.held], ["F9"]);
  assert.equal(binding.registered, "F9");
  binding.apply("F10");
  assert.deepEqual([...api.held], ["F10"]);
  binding.apply("None");
  assert.deepEqual([...api.held], []);
  assert.equal(binding.registered, null);
  assert.equal(binding.waitingForKey, false);
});

test("hotkey: a key held elsewhere is reported and picked up once it is free", () => {
  const api = shortcuts(new Set(["F9"]));
  const binding = new HotkeyBinding(api, () => {});
  binding.apply("F9");
  assert.match(binding.error, /F9 已被其他程序占用/);
  assert.equal(binding.waitingForKey, true);
  api.occupied.clear();
  binding.apply("F9");
  assert.equal(binding.error, null);
  assert.equal(binding.registered, "F9");
  assert.equal(binding.waitingForKey, false);
});

test("hotkey: an unsupported value is an error nothing retries", () => {
  const binding = new HotkeyBinding(shortcuts(), () => {});
  binding.apply("Ctrl+Alt+Del");
  assert.equal(binding.error, "快捷键设置不受支持");
  assert.equal(binding.waitingForKey, false);
});

test("hotkey: applying the same key keeps the registration in place", () => {
  let registrations = 0;
  const api = shortcuts();
  const binding = new HotkeyBinding(
    {
      register: (key) => {
        registrations += 1;
        return api.register(key);
      },
      unregister: (key) => api.unregister(key),
    },
    () => {},
  );
  binding.apply("F9");
  binding.apply("F9");
  assert.equal(registrations, 1);
  binding.reset();
  assert.equal(binding.requested, "None");
  assert.deepEqual([...api.held], []);
});

test("quit: only one exit starts, and a declined question allows another", async () => {
  const gate = new QuitGate();
  assert.equal(await gate.begin("user", async () => false), false);
  assert.equal(gate.started, false);
  let release;
  const asked = gate.begin("user", () => new Promise((resolve) => {
    release = resolve;
  }));
  assert.equal(await gate.begin("tray-exit", async () => true), false);
  release(true);
  assert.equal(await asked, true);
  assert.equal(gate.started, true);
  assert.equal(await gate.begin("user", async () => true), false);
});

test("quit: the end of the Windows session does not wait for an open question", async () => {
  const gate = new QuitGate();
  let release;
  const asked = gate.begin("window-closed", () => new Promise((resolve) => {
    release = resolve;
  }));
  assert.equal(await gate.begin("session-end", async () => {
    throw new Error("must not ask");
  }), true);
  assert.equal(gate.started, true);
  // Whatever the answer to the old question, the exit runs once.
  release(true);
  assert.equal(await asked, false);
});

test("quit: a failed question leaves the gate open", async () => {
  const gate = new QuitGate();
  await assert.rejects(gate.begin("user", async () => {
    throw new Error("dialog failed");
  }));
  assert.equal(await gate.begin("user", async () => true), true);
});
