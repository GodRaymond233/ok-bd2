import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { mkdtempSync, writeFileSync, rmSync, linkSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const protocol = require("../dist/protocol.js");
const { parseShellArgs, readDesktopState, fitBounds, minimumSize } = require("../dist/shell-config.js");
const navigation = require("../dist/navigation.js");
const { maintenanceArguments, updateCommand } = require("../dist/maintenance.js");
const { probeClipboard } = require("../../scripts/desktop_probe.cjs");

test("clipboard probe preserves non-text data and restores plain text or an empty clipboard", async () => {
  for (const formats of [[], [13], [8], [15], [13, 49161], null]) {
    let content = formats?.length ? "original" : "";
    let writes = 0;
    let cleared = false;
    const checks = [];
    const skips = [];
    const clipboard = {
      readText: async () => content,
      writeText: async value => { content = value; writes++; },
      clear: () => { content = ""; cleared = true; },
    };
    const result = {};
    await probeClipboard({ clipboard, result, python: "unused", platform: "win32",
      spawnSync: () => ({ status: formats === null ? 1 : 0, stdout: JSON.stringify(formats) }),
      webContents: { focus() {}, async executeJavaScript(code) {
        if (code.includes("writeText")) { await clipboard.writeText("okbd2-probe"); return "ok"; }
        return true;
      } },
      check: (name, passed) => { assert.equal(passed, true, name); checks.push(name); },
      skip: (name, reason) => skips.push({ name, reason }),
      until: async fn => assert.equal(await fn(), true),
    });
    assert.equal(content, formats?.length ? "original" : "");
    if (formats && formats.every(value => [1, 7, 13, 16].includes(value))) {
      assert.equal(skips.length, 0);
      assert.equal(result.clipboardWrite, "ok");
      assert.equal(checks.length, 3);
      assert.equal(cleared, formats.length === 0);
    } else {
      assert.equal(writes, 0);
      assert.equal(skips.length, 2);
      assert.equal(checks.length, 1);
    }
  }
});

test("maintenance validates the version and binds the launcher to its installation", (t) => {
  const root = mkdtempSync(join(tmpdir(), "okbd2-install-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const cwd = join(root, "data/apps/ok-bd2/working");
  const message = { type: "maintenance", operation: "update", version: "v0.9.7",
    target: "v0.9.8", backup_id: "", launcher: join(root, "ok-bd2.exe") };
  writeFileSync(message.launcher, "launcher");
  const alias = join(root, "alias.exe");
  linkSync(message.launcher, alias);
  assert.deepEqual(updateCommand(cwd, message), { executable: message.launcher, cwd: root,
    args: ["--update-to-version", "v0.9.8"] });
  assert.equal(updateCommand(cwd, { ...message, launcher: alias }).executable, message.launcher);
  assert.throws(() => updateCommand(cwd, { ...message, launcher: join(tmpdir(), "other.exe") }));
  assert.throws(() => maintenanceArguments({ ...message, target: "--other-command" }));
  assert.throws(() => maintenanceArguments({ ...message, operation: "restore", backup_id: "../configs" }));
  const line = "@@okbd2 " + JSON.stringify(message);
  assert.deepEqual(protocol.parseCoreLine(line), message);
  assert.equal(protocol.parseCoreLine('@@okbd2 {"type":"maintenance-ready"}'), null);
});

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
  assert.deepEqual(protocol.parseCoreLine('@@okbd2 {"type":"notification","title":"标题","message":"内容","error":true}'),
    { type: "notification", title: "标题", message: "内容", error: true });
  assert.equal(protocol.parseCoreLine('@@okbd2 {"type":"notification","message":3}'), null);
});

test("desktop state tolerates damaged preferences and bounds remain visible after monitor changes", () => {
  const directory = mkdtempSync(join(tmpdir(), "okbd2-desktop-"));
  try {
    const file = join(directory, "state.json");
    assert.deepEqual(readDesktopState(file), { zoom: 100, maximized: false });
    writeFileSync(file, '{broken');
    assert.equal(readDesktopState(file).zoom, 100);
    const bounds = { x: 2200, y: -900, width: 1800, height: 1000 };
    writeFileSync(file, JSON.stringify({ zoom: 125, bounds, maximized: true }));
    assert.deepEqual(readDesktopState(file), { zoom: 125, bounds, maximized: true });
    const primary = { x: 0, y: 0, width: 1280, height: 720 };
    assert.deepEqual(fitBounds(bounds, [primary]), primary);
    const left = { x: -1920, y: 0, width: 1920, height: 1040 };
    const saved = { x: -1800, y: 100, width: 1200, height: 800 };
    assert.deepEqual(fitBounds(saved, [primary, left]), saved);
    writeFileSync(file, JSON.stringify({ zoom: 999, bounds: { ...bounds, width: -1 } }));
    assert.deepEqual(readDesktopState(file), { zoom: 100, bounds: undefined, maximized: false });
    writeFileSync(file, JSON.stringify({ zoom: 100, theme: "dark", dockExpanded: false, railWidth: 280, taskListWidth: 400 }));
    assert.deepEqual(readDesktopState(file), { zoom: 100, bounds: undefined, maximized: false, theme: "dark", dockExpanded: false, railWidth: 280, taskListWidth: 400 });
    writeFileSync(file, JSON.stringify({ zoom: 100, theme: "neon", dockExpanded: "no", railWidth: -1 }));
    assert.deepEqual(readDesktopState(file), { zoom: 100, bounds: undefined, maximized: false });
    for (const railWidth of ["280", 361, 200.5]) {
      writeFileSync(file, JSON.stringify({ railWidth }));
      assert.equal(readDesktopState(file).railWidth, undefined);
    }
    for (const taskListWidth of ["400", 219, 481, 300.5]) {
      writeFileSync(file, JSON.stringify({ taskListWidth }));
      assert.equal(readDesktopState(file).taskListWidth, undefined);
    }
  } finally { rmSync(directory, { recursive: true }); }
});

test("a window across two displays stays on the one it mostly covers", () => {
  const primary = { x: 0, y: 0, width: 1920, height: 1040 };
  const left = { x: -1920, y: 0, width: 1920, height: 1040 };
  const mostlyLeft = { x: -1200, y: 100, width: 1210, height: 800 };
  const fitted = fitBounds(mostlyLeft, [primary, left]);
  assert.equal(fitted.x, -1210);
  assert.equal(fitted.width, 1210);
  const mostlyPrimary = { x: -10, y: 100, width: 1210, height: 800 };
  assert.equal(fitBounds(mostlyPrimary, [primary, left]).x, 0);
  // Off every display: back on the first one, centred when nothing was saved.
  assert.equal(fitBounds({ x: 9000, y: 9000, width: 800, height: 600 }, [primary, left]).x, 1120);
  assert.equal(fitBounds(undefined, [primary, left]).x, 360);
});

test("zooming in raises the minimum window size, within the display", () => {
  const area = { x: 0, y: 0, width: 1920, height: 1040 };
  assert.deepEqual(minimumSize(100, area), { width: 600, height: 450 });
  assert.deepEqual(minimumSize(80, area), { width: 600, height: 450 });
  assert.deepEqual(minimumSize(125, area), { width: 750, height: 563 });
  assert.deepEqual(minimumSize(150, area), { width: 900, height: 675 });
  assert.deepEqual(minimumSize(150, { x: 0, y: 0, width: 800, height: 600 }), { width: 800, height: 600 });
  const small = { x: 100, y: 100, width: 600, height: 450 };
  assert.deepEqual(fitBounds(small, [area], 150), { x: 100, y: 100, width: 900, height: 675 });
  assert.deepEqual(fitBounds(small, [area]), small);
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
  // Copy buttons may write the clipboard; nothing else, and only from the core's pages.
  assert.equal(navigation.pagePermissionAllowed("clipboard-sanitized-write", "http://127.0.0.1:5123/report", origin), true);
  assert.equal(navigation.pagePermissionAllowed("clipboard-sanitized-write", "http://127.0.0.1:5123", origin), true);
  assert.equal(navigation.pagePermissionAllowed("clipboard-sanitized-write", "https://example.com/", origin), false);
  assert.equal(navigation.pagePermissionAllowed("clipboard-sanitized-write", "http://127.0.0.1:5123/", null), false);
  for (const permission of ["clipboard-read", "notifications", "media", "openExternal"]) {
    assert.equal(navigation.pagePermissionAllowed(permission, "http://127.0.0.1:5123/", origin), false);
  }
  assert.equal(navigation.isAllowedExternal("https://github.com/GodRaymond233/ok-bd2"), true);
  assert.equal(navigation.isAllowedExternal("http://github.com/GodRaymond233/ok-bd2"), false);
  assert.equal(navigation.isAllowedExternal("https://github.com/GodRaymond233.evil/x"), false);
  assert.equal(navigation.isAllowedExternal("https://example.com/"), false);
  const page = join(tmpdir(), "static ~#%", "status.html");
  const pageUrl = pathToFileURL(page).href;
  assert.equal(navigation.navigationAllowed(pageUrl, null, page), true);
  assert.equal(navigation.navigationAllowed("file:///etc/passwd", origin, page), false);
  assert.equal(navigation.navigationAllowed("https://example.com/", origin, page), false);
});
