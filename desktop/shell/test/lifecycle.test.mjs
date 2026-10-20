// Process lifetimes on the real operating system (F1). The core is a Python stand-in on
// purpose: what matters is how Windows treats the core and the game it starts, and that
// depends on how they were created (by libuv for the core, by subprocess.Popen for the game).
//
// Interpreter: OKBD2_TEST_PYTHON, else `python` (Windows) or `python3`. Use both a venv
// interpreter (a redirector on Windows) and a standalone one, as PyAppify installs.
// Shell host: OKBD2_TEST_HOST (for example the packaged ok-bd2-desktop.exe, run with
// ELECTRON_RUN_AS_NODE=1), else this Node.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { CoreProcess } = require("../dist/core-process.js");
const FAKE_CORE = fileURLToPath(new URL("./fixtures/fake_core.py", import.meta.url));
const HOST = fileURLToPath(new URL("./fixtures/shell-host.cjs", import.meta.url));
const PYTHON =
  process.env.OKBD2_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");

const version = spawnSync(PYTHON, ["-c", "import sys; print(sys.version_info[0])"], {
  encoding: "utf8",
});
if (version.status !== 0 || version.stdout.trim() !== "3") {
  // Fail rather than skip: these are the checks the Windows acceptance relies on.
  throw new Error(`the lifecycle tests need Python 3 at "${PYTHON}"; set OKBD2_TEST_PYTHON`);
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function lines(file) {
  if (!existsSync(file)) {
    return [];
  }
  return readFileSync(file, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function waitFor(check, timeoutMs, message) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = check();
    if (value) {
      return value;
    }
    if (Date.now() >= deadline) {
      throw new assert.AssertionError({ message });
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

function endLater(t, pid) {
  t.after(() => {
    if (pid && alive(pid)) {
      process.kill(pid);
    }
  });
}

test("the core outlives a crashed shell and starts its own safe exit", async (t) => {
  const folder = mkdtempSync(join(tmpdir(), "okbd2-lifecycle-"));
  const report = join(folder, "host.jsonl");
  const marker = join(folder, "core.jsonl");
  const env = {
    ...process.env,
    HOST_REPORT: report,
    HOST_PYTHON: PYTHON,
    HOST_SCRIPT: FAKE_CORE,
    FAKE_CORE_MARKER: marker,
    FAKE_CORE_GAME: "1",
  };
  const external = process.env.OKBD2_TEST_HOST;
  if (external) {
    env.ELECTRON_RUN_AS_NODE = "1";
  }
  const host = spawn(external || process.execPath, [HOST], { cwd: folder, env, stdio: "ignore" });
  t.after(() => host.exitCode === null && host.kill());
  const ready = (
    await waitFor(() => lines(report).find((e) => e.type === "ready"), 30000, "no ready")
  ).ready;
  const spawned = lines(report).find((e) => e.type === "spawned");
  const gamePid = Number(ready.coreVersion);
  endLater(t, gamePid);

  host.kill("SIGKILL"); // a crash, not a quit: TerminateProcess on Windows
  const killedAt = Date.now();
  const safeExit = await waitFor(
    () => lines(marker).find((e) => e.event === "safe-exit"),
    5000,
    "the core never started its safe exit (was it ended together with the shell?)",
  );
  assert.equal(safeExit.reason, "stdin-closed");
  assert.ok(safeExit.at * 1000 - killedAt < 2000, "the safe exit started late");
  await waitFor(() => !alive(ready.pid), 5000, "the core is still running");
  await waitFor(() => !alive(spawned.childPid), 5000, "the spawned interpreter is still running");
  assert.equal(alive(gamePid), true, "the game was ended with the shell");
});

test("a forced stop ends the core but never the game it started", async (t) => {
  const folder = mkdtempSync(join(tmpdir(), "okbd2-lifecycle-"));
  const core = new CoreProcess(
    {
      python: PYTHON,
      script: FAKE_CORE,
      cwd: folder,
      env: {
        FAKE_CORE_MODE: "hang",
        FAKE_CORE_GAME: "1",
        FAKE_CORE_MARKER: join(folder, "core.jsonl"),
      },
    },
    { shutdownTimeoutMs: 500, forcedExitWaitMs: 5000 },
  );
  const ready = await core.start();
  const gamePid = Number(ready.coreVersion);
  endLater(t, gamePid);
  endLater(t, ready.pid);
  assert.equal(alive(gamePid), true);

  const result = await core.stop("user");
  assert.equal(result.forced, true);
  // With a venv redirector the shell ends the redirector; the interpreter must go with it.
  await waitFor(() => !alive(ready.pid), 5000, "the core survived the forced stop");
  assert.equal(alive(core.child.pid), false);
  assert.equal(alive(gamePid), true, "the forced stop ended the game");
});
