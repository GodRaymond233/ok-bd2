// Stand-in for `python main.py --service`: speaks the stdin/stdout protocol only.
import { writeFileSync } from "node:fs";
import { createInterface } from "node:readline";

const mode = process.env.FAKE_CORE_MODE ?? "normal";
const record = process.env.FAKE_CORE_RECORD;
const PREFIX = "@@okbd2 ";
const send = (message) => process.stdout.write(PREFIX + JSON.stringify(message) + "\n");
let handshake = null;

process.stdout.write("2026-10-20 12:00:00,000 INFO MainThread noise before the ready line\n");
const lines = createInterface({ input: process.stdin });
lines.on("line", (line) => {
  const data = JSON.parse(line);
  if (handshake === null) {
    handshake = data;
    if (record) {
      writeFileSync(
        record,
        JSON.stringify({ handshake, argv: process.argv.slice(2), utf8: process.env.PYTHONUTF8 }),
      );
    }
    if (mode === "fatal-lock") {
      send({ type: "fatal", code: "executor-locked", message: "进程 7 正在控制游戏", holder_pid: 7 });
      process.exit(11);
    }
    if (mode === "never-ready") {
      return;
    }
    send({
      type: "ready",
      port: 40000,
      pid: process.pid,
      api: mode === "bad-api" ? 99 : 1,
      core_version: "9.9.9",
    });
    if (mode === "crash-after-ready") {
      process.stderr.write("Traceback (most recent call last): boom\n");
      setTimeout(() => process.exit(3), 100);
    }
    if (mode === "self-exit") {
      setTimeout(() => {
        send({ type: "exiting", reason: "runtime-exit" });
        process.exit(0);
      }, 100);
    }
    return;
  }
  if (data.cmd === "shutdown") {
    if (mode === "hang-on-shutdown") {
      return;
    }
    send({ type: "exiting", reason: `shell:${data.reason}` });
    setTimeout(() => process.exit(0), 50);
  }
});
lines.on("close", () => {
  if (!["hang-on-shutdown", "never-ready"].includes(mode)) {
    process.exit(0);
  }
});
setInterval(() => {}, 1000);
