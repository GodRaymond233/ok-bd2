import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { CoreProcess, CoreError } = require("../dist/core-process.js");
const FAKE = fileURLToPath(new URL("./fixtures/fake-core.mjs", import.meta.url));

function command(mode, record) {
  return {
    python: process.execPath,
    script: FAKE,
    cwd: process.cwd(),
    env: { FAKE_CORE_MODE: mode, ...(record ? { FAKE_CORE_RECORD: record } : {}) },
  };
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function onceEvent(emitter, name) {
  return new Promise((resolve) => emitter.once(name, resolve));
}

test("ready after handshake, safe stop without force", async () => {
  const record = join(mkdtempSync(join(tmpdir(), "okbd2-shell-")), "record.json");
  const core = new CoreProcess(command("normal", record));
  const ready = await core.start();
  assert.equal(ready.origin, "http://127.0.0.1:40000");
  assert.equal(ready.coreVersion, "9.9.9");
  const seen = JSON.parse(readFileSync(record, "utf8"));
  assert.deepEqual(seen.handshake, { v: 1, token: core.token, parent_pid: process.pid });
  assert.deepEqual(seen.argv, ["--service"]);
  assert.equal(seen.utf8, "1");
  assert.match(core.token, /^[A-Za-z0-9_-]{43}$/);
  assert.ok(core.stderrTail().some((line) => line.includes("noise before the ready line")));
  const result = await core.stop("user");
  assert.equal(result.forced, false);
  assert.equal(result.exitCode, 0);
  assert.ok(result.durationMs < 5000);
});

test("refused executor lock is reported with the holder", async () => {
  const core = new CoreProcess(command("fatal-lock"));
  await assert.rejects(core.start(), (error) => {
    assert.ok(error instanceof CoreError);
    assert.equal(error.kind, "fatal");
    assert.equal(error.details.code, "executor-locked");
    assert.equal(error.details.holderPid, 7);
    assert.equal(error.details.exitCode, 11);
    return true;
  });
});

test("ready timeout stops the core", async () => {
  const core = new CoreProcess(command("never-ready"), {
    readyTimeoutMs: 300,
    shutdownTimeoutMs: 200,
    forcedExitWaitMs: 3000,
  });
  const exited = onceEvent(core, "exit");
  await assert.rejects(core.start(), (error) => error.kind === "timeout");
  await exited;
  assert.equal(alive(core.child.pid), false);
});

test("unsupported core API is refused", async () => {
  const core = new CoreProcess(command("bad-api"), { shutdownTimeoutMs: 300 });
  const exited = onceEvent(core, "exit");
  await assert.rejects(core.start(), (error) => error.kind === "incompatible");
  await exited;
});

test("crash after ready is reported with the last output", async () => {
  const core = new CoreProcess(command("crash-after-ready"));
  const crashed = onceEvent(core, "crashed");
  await core.start();
  const exit = await crashed;
  assert.equal(exit.exitCode, 3);
  assert.equal(exit.requested, false);
  assert.ok(exit.stderrTail.some((line) => line.includes("boom")));
});

test("core ending itself is not a crash", async () => {
  const core = new CoreProcess(command("self-exit"));
  let crashed = false;
  core.on("crashed", () => {
    crashed = true;
  });
  const exited = onceEvent(core, "exit");
  await core.start();
  const exit = await exited;
  assert.equal(exit.coreReason, "runtime-exit");
  assert.equal(crashed, false);
});

test("hung core is force-ended only after the timeout", async () => {
  const core = new CoreProcess(command("hang-on-shutdown"), {
    shutdownTimeoutMs: 400,
    forcedExitWaitMs: 3000,
  });
  await core.start();
  const started = Date.now();
  const result = await core.stop("user");
  assert.equal(result.forced, true);
  assert.ok(Date.now() - started >= 400);
  assert.equal(alive(core.child.pid), false);
});

// Whether the game survives is tested in lifecycle.test.mjs with a Python core: a Node
// stand-in would put its own children into a kill-on-close job on Windows.

test("the core is spawned outside the shell's kill-on-close job only on Windows", async () => {
  for (const [platform, detached] of [
    ["win32", true],
    ["linux", false],
    ["darwin", false],
  ]) {
    let seen = null;
    const core = new CoreProcess(command("normal"), {
      platform,
      spawnImpl: (_command, _args, options) => {
        seen = options;
        throw new Error("not started");
      },
    });
    await assert.rejects(core.start(), (error) => error.kind === "spawn");
    assert.equal(seen.detached, detached, platform);
    assert.equal(seen.windowsHide, true);
    assert.deepEqual(seen.stdio, ["pipe", "pipe", "pipe"]);
  }
});

test("missing interpreter is a spawn failure", async () => {
  const core = new CoreProcess({ ...command("normal"), python: "/nonexistent/python-okbd2" });
  await assert.rejects(core.start(), (error) => error.kind === "spawn");
});
