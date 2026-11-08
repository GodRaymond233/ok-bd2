// Desktop behaviour on the real Electron main process and the real service layer: hotkey, tray,
// close, quit, zoom and notifications. It needs no game and sends no input: the start controller
// is a stand-in (desktop_probe_core.py) and hotkey callbacks are called directly, so a real key
// press and how Windows shows a notification remain for acceptance on the machine itself.
//
// Under Node this is the runner:
//   node desktop/scripts/desktop_probe.cjs --python <exe> --out <folder>
// It starts Electron once per mode on this same file, against one throwaway fixture folder:
// each mode writes probe-<mode>.json, and desktop-probe.json is the summary.
// Build desktop/shell (dist) and desktop/ui first.
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const { createRequire } = require("node:module");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const SHELL = path.join(ROOT, "desktop", "shell");
const MODES = ["first", "restart", "close", "session", "early-crash"];
const MODE_TIMEOUT_MS = 120_000;

function runner() {
  const value = (flag) => {
    const index = process.argv.indexOf(flag);
    if (index < 0 || !process.argv[index + 1]) {
      console.error("usage: desktop_probe.cjs --python <exe> --out <folder>");
      process.exit(2);
    }
    return path.resolve(process.argv[index + 1]);
  };
  const python = value("--python");
  const out = value("--out");
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(out, { recursive: true });
  // Under plain Node the electron package exports the path of its binary.
  const electron = createRequire(path.join(SHELL, "package.json"))("electron");
  const results = [];
  for (const mode of MODES) {
    const output = fs.openSync(path.join(out, `probe-${mode}.log`), "w");
    const run = childProcess.spawnSync(electron, [__filename], {
      cwd: SHELL,
      env: { ...process.env, PROBE_MODE: mode, PROBE_OUT: out, PROBE_PYTHON: python },
      stdio: ["ignore", output, output],
      timeout: MODE_TIMEOUT_MS + 30_000,
    });
    fs.closeSync(output);
    const report = path.join(out, `probe-${mode}.json`);
    const result = fs.existsSync(report) ? JSON.parse(fs.readFileSync(report, "utf8")) : { error: "no report" };
    const passed = run.status === 0 && result.passed === true;
    results.push({ mode, passed, exitCode: run.status, checks: result.checks?.length ?? 0, error: result.error ?? null });
    console.log(`${passed ? "ok  " : "FAIL"} ${mode}: ${result.checks?.length ?? 0} checks${result.error ? ` (${result.error})` : ""}`);
  }
  const passed = results.every((result) => result.passed);
  fs.writeFileSync(path.join(out, "desktop-probe.json"), JSON.stringify({ passed, results }, null, 2));
  process.exit(passed ? 0 : 1);
}

function probe() {
  const electron = require("electron");
  const { app, clipboard, globalShortcut, Notification, Tray, dialog } = electron;
  const mode = process.env.PROBE_MODE;
  const out = process.env.PROBE_OUT;
  const fixture = path.join(out, "fixture");
  const stateFile = path.join(out, "probe-state.json");
  fs.mkdirSync(fixture, { recursive: true });
  process.env.OKBD2_DESKTOP_FIXTURE = path.join(fixture, "configs");
  // The probe reads Chinese text, whatever the system's language.
  fs.mkdirSync(process.env.OKBD2_DESKTOP_FIXTURE, { recursive: true });
  fs.writeFileSync(path.join(process.env.OKBD2_DESKTOP_FIXTURE, "ui_config.json"), JSON.stringify({ MainWindow: { Language: "zh_CN" } }));
  process.argv.push(
    "--core-python", process.env.PROBE_PYTHON,
    "--core-script", path.join(__dirname, "desktop_probe_core.py"),
    "--core-cwd", ROOT,
    "--user-data", path.join(fixture, "shell-data"),
  );
  const result = { mode, checks: [] };
  const callbacks = new Map();
  const delivered = [];
  const dialogs = [];
  let win;
  let supervised;
  let tray;
  let menu;
  let probedKeys = [];

  const register = globalShortcut.register.bind(globalShortcut);
  globalShortcut.register = (key, callback) => {
    const ok = register(key, callback);
    if (ok) callbacks.set(key, callback);
    return ok;
  };
  const setMenu = Tray.prototype.setContextMenu;
  Tray.prototype.setContextMenu = function (value) {
    tray = this;
    menu = value;
    return setMenu.call(this, value);
  };
  Tray.prototype.displayBalloon = function (value) {
    delivered.push({ title: value.title, body: value.content });
  };
  Notification.prototype.show = function () {
    delivered.push({ title: this.title, body: this.body });
  };
  // Dialogs stay open until the probe answers, as they do when nobody is looking at them.
  dialog.showMessageBox = (_window, options) =>
    new Promise((resolve) => dialogs.push({ options, answer: (response) => resolve({ response }) }));
  const realFetch = globalThis.fetch;
  let releasePreferences = () => {};
  // early-crash: startCore waits on its first settings read until the core has crashed.
  let holdPreferences = mode !== "early-crash" ? null : new Promise((resolve) => {
    releasePreferences = () => {
      holdPreferences = null;
      resolve();
    };
  });
  let failPreferences = false;
  let failToggle = false;
  globalThis.fetch = async (url, init) => {
    if (String(url).endsWith("/api/desktop/preferences")) {
      if (failPreferences) throw new Error("probe: core too slow");
      if (holdPreferences) await holdPreferences;
    }
    if (failToggle && String(url).endsWith("/api/desktop/toggle-execution")) {
      throw new Error("probe: no answer");
    }
    return realFetch(url, init);
  };

  const { CoreProcess } = require(path.join(SHELL, "dist", "core-process.js"));
  const start = CoreProcess.prototype.start;
  CoreProcess.prototype.start = function () {
    supervised = this;
    return start.call(this);
  };
  app.on("browser-window-created", (_event, value) => {
    win = value;
    win.setOpacity(0);
    win.show = () => win.showInactive();
    win.focus = () => {};
  });

  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const until = async (predicate, label, attempts = 300) => {
    for (let index = 0; index < attempts; index += 1) {
      if (await predicate()) return;
      await delay(100);
    }
    throw new Error(`timeout: ${label}`);
  };
  const js = (code) => win.webContents.executeJavaScript(code);
  const check = (name, value) => {
    result.checks.push({ name, passed: Boolean(value) });
    assert(value, name);
  };
  const api = (url, body) =>
    js(`fetch(${JSON.stringify(url)},${body === undefined ? "{}" : JSON.stringify({
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })}).then(async r=>({status:r.status,body:await r.json()}))`);
  const desktop = () => js("window.okbd2Shell.desktop()");
  const setting = async (key, value) => {
    const response = await api(`/api/settings/${encodeURIComponent("Basic Options")}/config`, { key, value });
    assert.equal(response.status, 200, JSON.stringify(response));
    return desktop();
  };
  const direct = (route) =>
    realFetch(`${supervised.ready.origin}${route}`, {
      method: "POST", headers: { Authorization: `Bearer ${supervised.token}` },
    });
  const clientReady = async () =>
    win && supervised?.state === "ready" &&
    (await js(`document.querySelector('[data-core]')?.dataset.core==='ok'`).catch(() => false));
  const railRestored = () => js("(()=>{const expected=innerWidth<900?60:Math.min(280,Math.max(160,innerWidth-640));return window.okbd2Shell.uiPreferences().railWidth===280 && Math.abs(document.querySelector('.rail').getBoundingClientRect().width-expected)<1})()");

  const actualExit = app.exit.bind(app);
  app.exit = (code) => {
    result.exitCode = code;
    result.coreStopped = supervised?.state === "exited";
    result.hotkeysReleased = probedKeys.every((key) => !globalShortcut.isRegistered(key));
    result.passed = Boolean(result.complete && result.coreStopped && result.hotkeysReleased && !result.error);
    fs.writeFileSync(path.join(out, `probe-${mode}.json`), JSON.stringify(result, null, 2));
    actualExit(result.passed ? 0 : 1);
  };

  async function checkRailResize() {
    const width = () => js("Math.round(document.querySelector('.rail').getBoundingClientRect().width)");
    const drag = async (requested, expected, preview = false) => {
      const position = await js("(()=>{const r=document.querySelector('.rail-divider').getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+100),width:Math.round(document.querySelector('.rail').getBoundingClientRect().width)}})()");
      const x = position.x + requested - position.width;
      win.webContents.sendInputEvent({ type: "mouseMove", x: position.x, y: position.y });
      win.webContents.sendInputEvent({ type: "mouseDown", x: position.x, y: position.y, button: "left", clickCount: 1 });
      try {
        win.webContents.sendInputEvent({ type: "mouseMove", x, y: position.y, modifiers: ["leftButtonDown"] });
        await until(async () => await width() === expected, "dragged navigation width");
        if (preview) check("drag previews without saving every move", await js("window.okbd2Shell.uiPreferences().railWidth===undefined"));
      } finally {
        win.webContents.sendInputEvent({ type: "mouseUp", x, y: position.y, button: "left", clickCount: 1 });
      }
      await until(() => js(`window.okbd2Shell.uiPreferences().railWidth===${expected}`), "saved navigation width");
    };
    check("navigation starts at its default width", await width() === 216);
    await drag(280, 280, true);
    check("navigation can be dragged right", await width() === 280);
    await drag(40, 160);
    check("navigation has a usable minimum width", await width() === 160);
    await drag(700, 360);
    check("navigation has a bounded maximum width", await width() === 360);
    await js("document.querySelector('.rail-divider').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}))");
    await until(async () => await width() === 350, "keyboard navigation width");
    check("navigation divider supports keyboard adjustment", true);
    await js("document.querySelector('.rail-divider').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
    await until(async () => await width() === 216, "reset navigation width");
    check("double click resets navigation width", true);
    await drag(280, 280);
    fs.writeFileSync(path.join(out, "sidebar-resized.png"), (await win.webContents.capturePage()).toPNG());
  }

  async function checkTaskListResize() {
    win.setBounds({ x: 80, y: 80, width: 1480, height: 900 });
    await js(`document.querySelector('nav button[title="任务"]').click()`);
    const width = () => js("Math.round(document.querySelector('.libcol').getBoundingClientRect().width)");
    await until(async () => await width() === 340, "default task list width");
    const drag = async (requested, expected, preview = false) => {
      const position = await js("(()=>{const r=document.querySelector('.task-list-divider').getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+100),width:Math.round(document.querySelector('.libcol').getBoundingClientRect().width)}})()");
      const x = position.x + requested - position.width;
      win.webContents.sendInputEvent({ type: "mouseMove", x: position.x, y: position.y });
      win.webContents.sendInputEvent({ type: "mouseDown", x: position.x, y: position.y, button: "left", clickCount: 1 });
      try {
        win.webContents.sendInputEvent({ type: "mouseMove", x, y: position.y, modifiers: ["leftButtonDown"] });
        await until(async () => await width() === expected, "dragged task list");
        if (preview) check("task list previews width before saving", await js("window.okbd2Shell.uiPreferences().taskListWidth===undefined"));
      } finally {
        win.webContents.sendInputEvent({ type: "mouseUp", x, y: position.y, button: "left", clickCount: 1 });
      }
      await until(() => js(`window.okbd2Shell.uiPreferences().taskListWidth===${expected}`), "saved task list width");
    };
    await drag(400, 400, true);
    check("task list can be dragged wider", await width() === 400);
    await drag(100, 220);
    check("task list has a usable minimum", await width() === 220);
    await drag(700, 480);
    check("task list has a bounded maximum", await width() === 480);
    await js("document.querySelector('.task-list-divider').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}))");
    await until(async () => await width() === 470, "task list keyboard adjustment");
    check("task list divider supports keyboard adjustment", true);
    await js("document.querySelector('.task-list-divider').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
    await until(async () => await width() === 340, "task list reset");
    check("task list double click resets width", true);
    await drag(400, 400);
    fs.writeFileSync(path.join(out, "task-list-resized.png"), (await win.webContents.capturePage()).toPNG());
    win.setBounds({ x: 80, y: 80, width: 1000, height: 720 });
    await until(() => js("document.querySelector('.detail').getBoundingClientRect().width>=360 && document.documentElement.scrollWidth<=innerWidth"), "detail space while narrowing");
    check("task list contracts to preserve detail space without changing preference", await js("window.okbd2Shell.uiPreferences().taskListWidth===400"));
    win.setBounds({ x: 80, y: 80, width: 800, height: 720 });
    await until(() => js("getComputedStyle(document.querySelector('.task-list-divider')).display==='none'"), "compact task list");
    check("compact task view keeps the saved task list width", await js("window.okbd2Shell.uiPreferences().taskListWidth===400"));
    win.setBounds({ x: 80, y: 80, width: 1480, height: 900 });
    await until(async () => await width() === 400, "restored wide task list");
    check("widening restores the chosen task list width", true);
    await js(`document.querySelector('nav button[title="日常"]').click()`);
  }

  async function checkTaskListRestored(reason) {
    await js(`document.querySelector('nav button[title="任务"]').click()`);
    await until(() => js("(()=>{const lib=document.querySelector('.lib'),col=document.querySelector('.libcol');if(!lib||!col)return false;const expected=innerWidth<900?lib.clientWidth:Math.min(400,Math.max(220,Math.min(480,lib.clientWidth-376)));return window.okbd2Shell.uiPreferences().taskListWidth===400&&Math.abs(col.getBoundingClientRect().width-expected)<1})()"), "task list after " + reason);
    check("task list width survives " + reason, true);
  }

  async function firstRun() {
    await checkRailResize();
    await checkTaskListResize();
    // Whatever this machine has free; another program may hold F9, as it did on the build machine.
    const free = ["F10", "F11", "F12", "F9"].filter((key) => {
      if (!register(key, () => {})) return false;
      globalShortcut.unregister(key);
      return true;
    });
    assert(free.length >= 2, `two free function keys are needed, found ${free}`);
    const [busyKey, key] = free;
    probedKeys = [busyKey, key];

    check("native notifier is owned by shell", (await api("/api/probe/state")).body.notifier === "ShellNotifier");
    let info = await setting("Start/Stop", "None");
    check("None releases shortcut", info.registeredHotkey === null && !globalShortcut.isRegistered(key));
    check("occupy a key for the conflict test", register(busyKey, () => {}));
    info = await setting("Start/Stop", busyKey);
    check("registration conflict is reported", info.registeredHotkey === null && info.hotkeyError.includes("占用"));
    globalShortcut.unregister(busyKey);
    info = await desktop();
    check("released shortcut can be registered", info.registeredHotkey === busyKey && globalShortcut.isRegistered(busyKey));
    info = await setting("Start/Stop", key);
    check("changing shortcut releases old key", info.registeredHotkey === key && !globalShortcut.isRegistered(busyKey));

    failPreferences = true;
    info = await desktop();
    check("a failed settings read is reported", Boolean(info.syncError));
    check("a failed settings read keeps the shortcut", info.registeredHotkey === key && globalShortcut.isRegistered(key));
    failPreferences = false;
    info = await desktop();
    check("the next read clears the failure", info.syncError === null && info.registeredHotkey === key);

    const press = callbacks.get(key);
    const state = async () => (await api("/api/probe/state")).body;
    press();
    await until(async () => (await state()).starts === 1, "safe start callback");
    check("shortcut invokes the core control API", true);
    press();
    await until(async () => (await state()).paused, "pause callback");
    check("second shortcut pauses", true);

    await api("/api/probe/guard", { active: true });
    press();
    await until(() => dialogs.length === 1, "refused control");
    check("diagnostic pause blocks shortcut resume", (await state()).starts === 1);
    check("a refusal gives the reason without the error type", !dialogs[0].options.detail.startsWith("Error"));
    press();
    await delay(400);
    check("repeated refusals do not stack dialogs", dialogs.length === 1);
    await api("/api/probe/guard", { active: false });
    // The dialog from the refusal is still open, behind the game for all anyone knows.
    press();
    await until(async () => (await state()).starts === 2, "resume with the dialog open");
    check("an open dialog does not swallow the shortcut", true);
    dialogs[0].answer(0);
    await delay(100);

    failToggle = true;
    const shown = delivered.length;
    press();
    await until(() => delivered.length === shown + 1, "unknown result notification");
    check("an unclear result is a notification, not a dialog", dialogs.length === 1);
    failToggle = false;
    delivered.length = shown;

    // Window discovery may already find a game; the connection test starts with an idle runner.
    await api("/api/probe/current-task", { name: null, running: false });
    await until(() => js("document.querySelector('[data-action=connect-game]')?.disabled===false"), "home connection button");
    fs.writeFileSync(path.join(out, "home-connection.png"), (await win.webContents.capturePage()).toPNG());
    await js("document.querySelector('[data-action=connect-game]').click()");
    await until(async () => (await state()).last_start_task === "LauncherTask", "standalone connection request");
    check("home connects through LauncherTask without selecting daily execution", (await state()).current_task === null);

    await setting("Minimize Window to System Tray when Closing", true);
    win.close();
    await delay(100);
    check("window close keeps core running in tray", !win.isVisible() && supervised.state === "ready");
    menu.items.find((item) => item.label === "显示窗口").click();
    check("tray restores window", win.isVisible());
    win.minimize();
    tray.emit("click");
    await delay(150);
    check("tray restores minimized window", !win.isMinimized() && win.isVisible());

    await js(`document.querySelector('nav button[title="设置"]').click()`);
    await until(() => js(`document.querySelector('[aria-label="界面缩放"]')?.disabled===false`), "settings page");
    await js(`(()=>{const e=document.querySelector('[aria-label="界面缩放"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,'125');e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await until(() => Math.abs(win.webContents.getZoomFactor() - 1.25) < 0.01, "zoom from UI");
    check("zoom selection applies to actual webContents", true);
    const before = (await desktop()).zoom;
    const refused = await js("window.okbd2Shell.setZoom(999).then(()=>false,()=>true)");
    check("invalid IPC zoom rejected", refused && (await desktop()).zoom === before);
    win.setBounds({ x: 80, y: 80, width: 980, height: 720 });
    await delay(400);
    win.webContents.reload();
    await until(clientReady, "reload");
    check("zoom survives page reload", Math.abs(win.webContents.getZoomFactor() - 1.25) < 0.01);
    check("task list width survives page reload", await js("window.okbd2Shell.uiPreferences().taskListWidth===400"));

    win.setMinimumSize(600, 450);
    win.setSize(600, 450);
    await delay(300);
    await js("window.okbd2Shell.setZoom(150)");
    await delay(300);
    const [minWidth, minHeight] = win.getMinimumSize();
    check("zooming in raises the minimum size", minWidth === 900 && minHeight === 675);
    check("zooming in grows a window below it", win.getNormalBounds().width >= 900 && win.getNormalBounds().height >= 675);
    // The minimum counts the window frame, as it does at 100%.
    check("the page keeps its layout width at the minimum", (await js("window.innerWidth")) >= 580);
    check("the minimum size has no sideways scrolling", await js("document.documentElement.scrollWidth<=window.innerWidth"));
    check("compact windows keep the icon rail and hide its divider", await js("Math.round(document.querySelector('.rail').getBoundingClientRect().width)===60 && getComputedStyle(document.querySelector('.rail-divider')).display==='none'"));
    check("compact layout preserves the chosen wide navigation width", await js("window.okbd2Shell.uiPreferences().railWidth===280"));
    await js("window.okbd2Shell.setZoom(125)");
    win.setBounds({ x: 80, y: 80, width: 980, height: 720 });
    await delay(400);

    // Copy buttons (report, logs, OCR text): the page may write the clipboard, never read it.
    const savedClipboard = await clipboard.readText();
    try {
      clipboard.writeText("okbd2-native-probe");
      result.nativeClipboardAvailable = clipboard.readText() === "okbd2-native-probe";
      win.webContents.focus();
      // As a button click: with a user gesture, which is what makes Electron ask the shell.
      const written = await win.webContents.executeJavaScript("navigator.clipboard.writeText('okbd2-probe').then(() => 'ok', (error) => String(error))", true);
      check("core pages receive clipboard-write permission", written === "ok");
      if (result.nativeClipboardAvailable) {
        await until(() => clipboard.readText() === "okbd2-probe", "clipboard write visible", 30);
        check("clipboard contents match the page write", true);
      }
      check("core pages cannot read the clipboard", await js("navigator.clipboard.readText().then(() => false, () => true)"));
    } finally {
      clipboard.writeText(savedClipboard);
    }

    await api(`/api/settings/${encodeURIComponent("Notification")}/config`, { key: "System Notification", value: true });
    check("local notification accepted", (await api("/api/bd2/notify/test", { channel: "system" })).status === 200);
    await until(() => delivered.length === shown + 1, "notification pipe");
    check("one notification routed to shell", delivered.at(-1).title === "ok-bd2 测试消息" && delivered.at(-1).body.includes("测试消息"));
    win.hide();
    tray.emit("balloon-click");
    check("notification click restores window", win.isVisible());
    await api(`/api/settings/${encodeURIComponent("Notification")}/config`, { key: "System Notification", value: false });
    const count = delivered.length;
    check("disabled system notification not delivered", (await api("/api/bd2/notify/test", { channel: "system" })).status === 400 && delivered.length === count);

    await js(`document.querySelector('nav button[title="日常"]').click()`);
    const firstChild = (await api("/api/probe/batch-progress", { step: 0 })).body.name;
    await until(() => js(`document.querySelector('.dock .sub')?.textContent.trim()===${JSON.stringify(firstChild)}`), "initial batch child");
    // Let the initial lifecycle refresh settle before advancing info without an event.
    await delay(1200);
    const nextChild = (await api("/api/probe/batch-progress", { step: 1 })).body.name;
    await until(() => js(`document.querySelector('.dock .sub')?.textContent.trim()===${JSON.stringify(nextChild)}`), "live child transition", 40);
    check("batch child advances without a lifecycle event", true);
    check("daily run order follows the same live transition", await js(`document.querySelector('.daily-page .steps')?.textContent.includes('执行中 · 探针阶段')`));
    await js(`document.querySelector('nav button[title="任务"]').click()`);
    await until(() => js(`Boolean([...document.querySelectorAll('.ti-open .nm')].find(e=>e.textContent===${JSON.stringify(nextChild)}))`), "child in task library");
    await js(`[...document.querySelectorAll('.ti-open .nm')].find(e=>e.textContent===${JSON.stringify(nextChild)}).closest('button').click()`);
    await until(() => js("document.querySelector('.status-summary')?.textContent.includes('探针警告')"), "compact status");
    check("status keeps warnings visible and folds raw matching data", await js("!document.querySelector('.status-summary').textContent.includes('match=') && !document.querySelector('.status-details').open"));
    await js("document.querySelector('.status-details summary').click()");
    check("diagnostic details can be expanded without losing evidence", await js("document.querySelector('.status-details').open && document.querySelector('.status-details .kv').textContent.includes('match=0.981')"));
    await js("document.querySelector('.status-details summary').click()");
    await js("window.okbd2Shell.setZoom(100)");
    win.setBounds({ x: 80, y: 80, width: 1280, height: 900 });
    await delay(350);
    fs.writeFileSync(path.join(out, "compact-status.png"), (await win.webContents.capturePage()).toPNG());
    await js("window.okbd2Shell.setZoom(125)");
    win.setBounds({ x: 80, y: 80, width: 980, height: 720 });
    const batchTask = (await api("/api/tasks")).body.find(task => task.class_name === "DailyBatchTask");
    // 一键完成日常 has its own page; its whole-run row shows the run's history.
    await js(`document.querySelector('nav button[title="日常"]').click()`);
    await until(() => js("Boolean(document.querySelector('.ti.overview .ti-open'))"), "daily whole-run row");
    await js("document.querySelector('.ti.overview .ti-open').click()");
    await until(() => js(`document.querySelector('.detail')?.getAttribute('aria-label')===${JSON.stringify(batchTask.name)} && document.querySelector('.hist')?.textContent.includes('还没有运行记录')`), "batch history before stop");
    const stoppedBatch = await api(`/api/tasks/${encodeURIComponent(batchTask.name)}/action`, { action: "stop" });
    assert.equal(stoppedBatch.status, 200, JSON.stringify(stoppedBatch));
    await api("/api/probe/batch-progress", { stop: true });
    await until(() => js("Boolean(document.querySelector('.dock .seal.abort') && document.querySelector('.hist .s[title=已中止]'))"), "interrupted history after task event");
    check("interrupted run appears without navigating or reloading", true);
    check("stop notification uses Chinese in the Chinese interface", await js("document.querySelector('.toast')?.textContent.includes('已中止') && !document.querySelector('.toast').textContent.includes('Stopped')"));
    await js("window.okbd2Shell.setZoom(100)");
    win.setBounds({ x: 80, y: 80, width: 1480, height: 900 });
    await until(() => js("[...document.querySelectorAll('.dock .segbar')].some(bar=>bar.getBoundingClientRect().width>0 && bar.querySelector('.seg-incomplete')?.getBoundingClientRect().width>bar.querySelector('.seg-done')?.getBoundingClientRect().width)"), "unfinished share of stopped batch");
    check("stopped batch retains the unfinished share of its progress", true);
    fs.writeFileSync(path.join(out, "stopped-batch.png"), (await win.webContents.capturePage()).toPNG());
    await js("window.okbd2Shell.setZoom(125)");
    win.setBounds({ x: 80, y: 80, width: 980, height: 720 });

    const reportButton = `[...document.querySelectorAll('.dock button')].find(b=>['问题报告','生成问题报告'].includes(b.textContent.trim()))`;
    await api("/api/probe/current-task", { name: (await api("/api/tasks")).body[0].name });
    await until(() => js("Boolean(document.querySelector('.dock'))"), "run panel");
    await api("/api/probe/current-task", { name: null });
    await until(() => js(`Boolean(${reportButton})`), "finished run panel");
    check("a finished run can open the report page", await js(`!${reportButton}.disabled`));
    await js(`${reportButton}.click()`);
    await until(() => js("document.querySelector('h1.title')?.textContent==='问题报告'"), "report page");
    check("the finished run's button leads to the report page", true);

    const dockToggle = "document.querySelector('.dock button[aria-expanded]')";
    const dockExpanded = !(await js(`${dockToggle}.getAttribute('aria-expanded')==='true'`));
    await js(`${dockToggle}.click()`);
    await until(() => js(`window.okbd2Shell.uiPreferences().dockExpanded===${dockExpanded}`), "run panel choice");
    await js(`document.querySelector('nav button[title="设置"]').click()`);
    await until(() => js(`Boolean(document.querySelector('[aria-label="主题"]'))`), "theme choice");
    await js(`[...document.querySelectorAll('[aria-label="主题"] button')].find(b=>b.textContent.trim()==='深色').click()`);
    await until(() => js("window.okbd2Shell.uiPreferences().theme==='dark'"), "theme saved");
    check("the theme applies at once", await js("document.documentElement.dataset.theme==='dark'"));
    const savedDesktop = () => JSON.parse(fs.readFileSync(path.join(fixture, "shell-data", "desktop-state.json"), "utf8"));
    check("the page's choices reach the state file", savedDesktop().theme === "dark" && savedDesktop().dockExpanded === dockExpanded && savedDesktop().railWidth === 280);
    await js("window.okbd2Shell.setUiPreferences({theme:'neon',dockExpanded:'no',railWidth:0})");
    check("invalid preferences are ignored", savedDesktop().theme === "dark" && savedDesktop().dockExpanded === dockExpanded && savedDesktop().railWidth === 280);

    check("shortcut is active before normal quit", globalShortcut.isRegistered(key));
    win.maximize();
    await delay(400);
    // A bare X server has no window manager to maximize anything; Windows always does.
    if (process.platform === "win32") check("the window maximizes", win.isMaximized());
    fs.writeFileSync(stateFile, JSON.stringify({ key, maximized: win.isMaximized(), dockExpanded }));
    result.complete = true;
    await js("window.okbd2Shell.quit()");
  }

  async function restartRun() {
    const { key, maximized, dockExpanded } = JSON.parse(fs.readFileSync(stateFile, "utf8"));
    probedKeys = [key];
    const info = await desktop();
    check("zoom persisted after process restart", info.zoom === 125 && Math.abs(win.webContents.getZoomFactor() - 1.25) < 0.01);
    check("minimum size follows the saved zoom", win.getMinimumSize()[0] === 750);
    check("normal bounds restored", win.getNormalBounds().width === 980 && win.getNormalBounds().height === 720);
    check("maximized state restored", win.isMaximized() === maximized);
    await until(railRestored, "restored navigation width");
    check("navigation width is restored after process restart", true);
    await checkTaskListRestored("process restart");
    win.unmaximize();
    check("tray preference persisted", info.closeToTray === true);
    check("theme persisted after process restart", await js("document.documentElement.dataset.theme==='dark'"));
    await api("/api/probe/current-task", { name: (await api("/api/tasks")).body[0].name });
    await until(() => js("Boolean(document.querySelector('.dock button[aria-expanded]'))"), "run panel");
    check("run panel choice persisted after process restart",
      (await js("document.querySelector('.dock button[aria-expanded]').getAttribute('aria-expanded')==='true'")) === dockExpanded);
    await api("/api/probe/current-task", { name: null });
    check("shortcut is active before core crash", globalShortcut.isRegistered(key));
    win.close();
    await delay(100);
    check("close hides the live window", !win.isVisible() && supervised.state === "ready");
    void api("/api/probe/crash", {}).catch(() => {});
    await until(() => supervised.state === "exited", "fixture crash");
    await until(() => win.webContents.getURL().startsWith("file:"), "crash page");
    check("crash restores hidden window", win.isVisible());
    check("crash releases global shortcut", !globalShortcut.isRegistered(key));
    result.complete = true;
    // Nothing is left to keep alive: with the tray preference on, this still has to exit.
    win.close();
  }

  async function closeRun() {
    probedKeys = [JSON.parse(fs.readFileSync(stateFile, "utf8")).key];
    // A restarted core has another port, so the page has another origin.
    const origin = new URL(win.webContents.getURL()).origin;
    void api("/api/probe/crash", {}).catch(() => {});
    await until(() => win.webContents.getURL().startsWith("file:"), "crash page");
    await delay(1000); // Whatever the crash left behind lets go of the executor lock first.
    void js("window.okbd2Shell.restartCore()").catch(() => {});
    await until(clientReady, "restarted core");
    check("a restarted core has another origin", new URL(win.webContents.getURL()).origin !== origin);
    check("theme survives a core restart", await js("document.documentElement.dataset.theme==='dark'"));
    await until(railRestored, "navigation width after core restart");
    check("navigation width survives a core restart", true);
    await checkTaskListRestored("core restart");
    await js(`document.querySelector('nav button[title="设置"]').click()`);
    await until(() => js(`document.querySelector('[aria-label="界面缩放"]')?.disabled===false`), "settings loaded");
    await setting("Minimize Window to System Tray when Closing", false);
    check("close preference is disabled", (await desktop()).closeToTray === false);
    result.complete = true;
    win.close();
  }

  async function sessionRun() {
    await setting("Minimize Window to System Tray when Closing", false);
    await api("/api/probe/current-task", { name: "probe-task" });
    win.close();
    await until(() => dialogs.length === 1, "quit question");
    check("quitting with a running task asks first", dialogs[0].options.message.includes("probe-task"));
    check("the question leaves the core running", supervised.state === "ready");
    result.complete = true;
    // Windows ends the session with the question still open: the safe exit must run anyway.
    win.emit("session-end");
  }

  async function earlyCrashRun() {
    // The crash happens while startCore waits for the first settings read.
    await until(() => supervised?.state === "ready", "core ready");
    await direct("/api/probe/crash").catch(() => {});
    await until(() => supervised.state === "exited", "fixture crash");
    await until(() => win.webContents.getURL().startsWith("file:"), "status page");
    await delay(200);
    releasePreferences();
    await delay(1000);
    const text = await js("document.body.innerText");
    check("the crash page stays up after the settings read finishes", text.includes("意外退出"));
    result.complete = true;
    await js("window.okbd2Shell.quit()");
  }

  require(path.join(SHELL, "dist", "main.js"));
  app.whenReady().then(async () => {
    try {
      if (mode === "early-crash") {
        await until(() => win, "window");
        await earlyCrashRun();
        return;
      }
      await until(clientReady, "client ready");
      await { first: firstRun, restart: restartRun, close: closeRun, session: sessionRun }[mode]();
    } catch (error) {
      result.error = String(error);
      console.error(error);
      if (supervised) result.stop = await supervised.stop("failed-probe");
      globalShortcut.unregisterAll();
      app.exit(1);
    }
  });
  setTimeout(() => {
    result.error = "probe timeout";
    if (supervised) void supervised.stop("timeout").then(() => app.exit(1));
    else app.exit(1);
  }, MODE_TIMEOUT_MS);
}

if (process.versions.electron) {
  probe();
} else {
  runner();
}
