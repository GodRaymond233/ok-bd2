import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const protocol = require("../dist/protocol.js");
const { parseShellArgs } = require("../dist/shell-config.js");
const navigation = require("../dist/navigation.js");

test("protocol lines", () => {
  const ready = protocol.parseCoreLine(
    '@@okbd2 {"type":"ready","port":5123,"pid":8,"api":1,"core_version":"0.1.1"}',
  );
  assert.deepEqual(ready, { type: "ready", port: 5123, pid: 8, api: 1, core_version: "0.1.1" });
  assert.equal(protocol.parseCoreLine('{"type":"ready"}'), null);
  assert.equal(protocol.parseCoreLine("@@okbd2 not json"), null);
  assert.equal(protocol.parseCoreLine('@@okbd2 {"type":"ready","port":0,"pid":1,"api":1}'), null);
  assert.equal(protocol.parseCoreLine('@@okbd2 {"type":"other"}'), null);
  assert.deepEqual(protocol.parseCoreLine('@@okbd2 {"type":"exiting","reason":"x"}'), {
    type: "exiting",
    reason: "x",
  });
  const fatal = protocol.parseCoreLine('@@okbd2 {"type":"fatal","code":"startup","message":"m"}');
  assert.equal(fatal.code, "startup");
  assert.equal(fatal.holder_pid, null);
});

test("line splitting keeps partial lines", () => {
  const splitter = new protocol.LineSplitter();
  assert.deepEqual(splitter.push("a\r\nb"), ["a"]);
  assert.deepEqual(splitter.push("c\nd\n"), ["bc", "d"]);
  assert.deepEqual(splitter.push("tail"), []);
  assert.deepEqual(splitter.flush(), ["tail"]);
});

test("handshake, shutdown and token", () => {
  const token = protocol.newToken();
  assert.match(token, /^[A-Za-z0-9_-]{43}$/);
  assert.notEqual(token, protocol.newToken());
  assert.deepEqual(JSON.parse(protocol.handshakeLine(token, 42)), {
    v: 1,
    token,
    parent_pid: 42,
  });
  assert.deepEqual(JSON.parse(protocol.shutdownLine("tray-exit")), {
    cmd: "shutdown",
    reason: "tray-exit",
  });
  assert.equal(protocol.apiSupported(1), true);
  assert.equal(protocol.apiSupported(2), false);
  assert.equal(protocol.TIMEOUTS.shutdownMs, 25000);
});

test("shell arguments", () => {
  const cwd = join(tmpdir(), "working");
  const options = parseShellArgs(
    ["--core-python", "C:/py/python.exe", "--core-cwd", cwd, "--core-arg", "--debug", "--dev-tools"],
    {},
  );
  assert.equal(options.core.python, "C:/py/python.exe");
  assert.equal(options.core.script, join(cwd, "main.py"));
  assert.deepEqual(options.core.args, ["--debug"]);
  assert.equal(options.devTools, true);
  assert.equal(options.logFile, join(cwd, "logs", "desktop-shell.log"));
  const fromEnv = parseShellArgs(["--core-script=main_debug.py"], {
    OKBD2_CORE_PYTHON: "py",
    OKBD2_CORE_CWD: cwd,
  });
  assert.equal(fromEnv.core.script, join(cwd, "main_debug.py"));
  assert.ok("error" in parseShellArgs([], {}));
  assert.ok("error" in parseShellArgs(["--core-python"], {}));
});

test("navigation rules", () => {
  const origin = "http://127.0.0.1:5123";
  assert.equal(navigation.sameOrigin("http://127.0.0.1:5123/launch/x", origin), true);
  assert.equal(navigation.sameOrigin("http://127.0.0.1:5124/", origin), false);
  assert.equal(navigation.sameOrigin("http://localhost:5123/", origin), false);
  assert.equal(navigation.sameOrigin("http://127.0.0.1:5123/", null), false);
  assert.equal(navigation.isAllowedExternal("https://github.com/GodRaymond233/ok-bd2"), true);
  assert.equal(navigation.isAllowedExternal("http://github.com/GodRaymond233/ok-bd2"), false);
  assert.equal(navigation.isAllowedExternal("https://github.com/GodRaymond233.evil/x"), false);
  assert.equal(navigation.isAllowedExternal("https://example.com/"), false);
  const page = join(tmpdir(), "static", "status.html");
  const pageUrl = new URL(`file://${page}`).href;
  assert.equal(navigation.navigationAllowed(pageUrl, null, page), true);
  assert.equal(navigation.navigationAllowed("file:///etc/passwd", origin, page), false);
  assert.equal(navigation.navigationAllowed("https://example.com/", origin, page), false);
});
