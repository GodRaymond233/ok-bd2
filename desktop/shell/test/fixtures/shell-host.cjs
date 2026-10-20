// Stand-in for the shell's main process in the lifecycle tests and in
// desktop/scripts/lifecycle_probe.py: runs the real CoreProcess (dist/core-process.js) and
// appends what happens to HOST_REPORT as JSON lines. It is run with Node or with the
// packaged Electron (ELECTRON_RUN_AS_NODE=1) and ended abruptly to play a crashed shell.
// The core inherits this process's environment.
const fs = require("node:fs");
const path = require("node:path");
const { CoreProcess } = require(path.join(__dirname, "..", "..", "dist", "core-process.js"));

const report = process.env.HOST_REPORT;
const write = (event) =>
  fs.appendFileSync(report, JSON.stringify({ at: Date.now(), ...event }) + "\n");
const number = (name, fallback) => Number(process.env[name] || fallback);

const core = new CoreProcess(
  {
    python: process.env.HOST_PYTHON,
    script: process.env.HOST_SCRIPT,
    cwd: process.env.HOST_CWD || process.cwd(),
  },
  {
    readyTimeoutMs: number("HOST_READY_MS", 90000),
    shutdownTimeoutMs: number("HOST_SHUTDOWN_MS", 25000),
  },
);
core.on("exit", (exit) => write({ type: "exit", exitCode: exit.exitCode, requested: exit.requested }));
core.on("crashed", () => write({ type: "crashed" }));
const started = core.start();
write({ type: "spawned", hostPid: process.pid, childPid: core.child ? core.child.pid : null });
started.then(
  async (ready) => {
    write({ type: "ready", ready });
    if (process.env.HOST_ACTION === "stop") {
      write({ type: "stopped", result: await core.stop("host") });
      process.exit(0);
    }
  },
  (error) => {
    write({ type: "failed", kind: error.kind, message: error.message });
    // A failed start stops the core itself (for example after the ready timeout): wait.
    if (core.state === "exited") {
      process.exit(1);
    }
    core.once("exit", () => process.exit(1));
  },
);
setInterval(() => {}, 1000);
