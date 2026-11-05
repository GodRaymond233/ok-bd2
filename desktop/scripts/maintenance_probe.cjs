// Real Electron/core/backup subprocess with generated data. The launcher is a recorder;
// no game or real installation is opened, updated or restored.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const cp = require("node:child_process");
const { createRequire } = require("node:module");
const ROOT = path.resolve(__dirname, "../..");
const SHELL = path.join(ROOT, "desktop/shell");
const MODES = ["update", "restore", "unclean-exit", "backup-failure"];

if (!process.versions.electron) {
  const arg = (key) => path.resolve(process.argv[process.argv.indexOf(key) + 1]);
  const out = arg("--out");
  const python = arg("--python");
  fs.mkdirSync(out, { recursive: true });
  const electron = createRequire(path.join(SHELL, "package.json"))("electron");
  const results = [];
  for (const mode of MODES) {
    const fixture = fs.mkdtempSync(path.join(out, `${mode}-`));
    const log = fs.openSync(path.join(fixture, "probe.log"), "w");
    const run = cp.spawnSync(electron, [__filename], {
      cwd: ROOT, timeout: 120_000, windowsHide: true, stdio: ["ignore", log, log],
      env: { ...process.env, PROBE_MODE: mode, PROBE_FIXTURE: fixture, PROBE_PYTHON: python,
        PYTHONPATH: ROOT, PYTHONUTF8: "1", OKBD2_MAINTENANCE_PROBE: "1" },
    });
    fs.closeSync(log);
    const file = path.join(fixture, "result.json");
    const report = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file)) : { error: "no report" };
    results.push({ mode, ...report, exitCode: run.status, fixture });
    console.log(`${mode}: ${report.passed && run.status === 0 ? "PASS" : "FAIL"} ${report.error || ""}`);
  }
  const passed = results.every((entry) => entry.passed && entry.exitCode === 0);
  fs.writeFileSync(path.join(out, "maintenance-probe.json"), JSON.stringify({ passed, results }, null, 2));
  process.exit(passed ? 0 : 1);
} else {
  const { app, BrowserWindow, globalShortcut } = require("electron");
  const mode = process.env.PROBE_MODE;
  const fixture = process.env.PROBE_FIXTURE;
  const working = path.join(fixture, "data/apps/ok-bd2/working");
  const configs = path.join(working, "configs");
  const backups = path.join(working, "../backups");
  fs.mkdirSync(configs, { recursive: true });
  fs.writeFileSync(path.join(configs, ".core_version"), "0.9.7");
  fs.writeFileSync(path.join(configs, "probe-ledger.json"), '{"progress":"before"}');
  fs.writeFileSync(path.join(fixture, "ok-bd2.exe"), "launcher stand-in: never executed");
  const result = { mode, checks: [] };
  const check = (name, value) => { result.checks.push({ name, passed: Boolean(value) }); assert(value, name); };
  let win, core, command, backupId;
  let failBackup = false;
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const until = async (action) => {
    for (let i = 0; i < 500; i++) { if (await action()) return; await delay(50); }
    throw new Error("probe timed out");
  };
  const js = (code) => win.webContents.executeJavaScript(code);
  const api = (route, body) => fetch(core.ready.origin + route, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${core.token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(async (response) => ({ status: response.status, body: await response.json() }));
  const click = (text) => js(`(() => { const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}); if(!b||b.disabled) return false; b.click(); return true; })()`);
  const { CoreProcess } = require(path.join(SHELL, "dist/core-process.js"));
  const originalStart = CoreProcess.prototype.start;
  CoreProcess.prototype.start = function () {
    core = this;
    if (mode === "unclean-exit") {
      const emit = this.emit;
      this.emit = function (event, value, ...rest) {
        if (event === "message" && value?.type === "maintenance-ready") return true;
        return emit.call(this, event, value, ...rest);
      };
    }
    return originalStart.call(this);
  };
  const maintenance = require(path.join(SHELL, "dist/maintenance.js"));
  const originalMaintain = maintenance.maintainData;
  maintenance.maintainData = async (...args) => {
    check("data maintenance begins only after the core exits", core.state === "exited");
    if (mode === "backup-failure") {
      fs.mkdirSync(backups, { recursive: true });
      fs.writeFileSync(path.join(backups, "sentinel"), "keep");
      // A file at the backup root makes the actual helper reject the write.
      const blocked = backups + "-preserved";
      fs.renameSync(backups, blocked);
      fs.writeFileSync(backups, "blocked backup directory");
      failBackup = true;
    }
    return originalMaintain(...args);
  };
  maintenance.launchUpdate = async (cwd, message) => {
    command = maintenance.updateCommand(cwd, message);
    check("launcher receives the checked version", command.args[1] === "v0.9.8");
    check("launcher belongs to the isolated installation", command.executable === path.join(fixture, "ok-bd2.exe"));
    const entries = fs.readdirSync(backups).filter((name) => /^\d/.test(name));
    check("before-update snapshot exists before launcher handoff", entries.some((name) => {
      const metadata = JSON.parse(fs.readFileSync(path.join(backups, name, "manifest.json")));
      return metadata.reason === "before-update" &&
        fs.readFileSync(path.join(backups, name, "configs/probe-ledger.json"), "utf8").includes("before");
    }));
  };
  process.argv.push("--core-python", process.env.PROBE_PYTHON, "--core-script",
    path.join(__dirname, "desktop_probe_core.py"), "--core-cwd", working,
    "--user-data", path.join(fixture, "shell-data"));
  app.on("browser-window-created", (_event, window) => {
    win = window;
    win.setOpacity(0);
    win.show = () => win.showInactive();
    win.focus = () => {};
  });
  const exit = app.exit.bind(app);
  const finish = (error) => {
    if (error) result.error = String(error.stack || error);
    result.passed = !result.error && result.checks.length > 0;
    fs.writeFileSync(path.join(fixture, "result.json"), JSON.stringify(result, null, 2));
    exit(result.passed ? 0 : 1);
  };
  app.exit = (code) => {
    try {
      check("core stopped normally", code === 0 && core.state === "exited");
      check("hotkeys released", !globalShortcut.isRegistered("F9"));
      if (mode === "restore") {
        check("saved snapshot restored", fs.readFileSync(path.join(configs, "probe-ledger.json"), "utf8").includes("before"));
        check("files created after backup removed", !fs.existsSync(path.join(configs, "new-probe-file.json")));
      } else check("launcher was called", Boolean(command));
      finish();
    } catch (error) { finish(error); }
  };
  require(path.join(SHELL, "dist/main.js"));
  (async () => {
    await until(async () => win && core?.state === "ready" &&
      await js("document.querySelector('[data-core]')?.dataset.core==='ok'").catch(() => false));
    check("game starter not called", (await api("/api/probe/state")).body.starts === 0);
    await until(() => click("设置"));
    await until(() => click("关于与更新"));
    await until(() => click("立即备份"));
    await until(async () => {
      const state = (await api("/api/desktop/backups")).body;
      backupId = state.items.find((entry) => entry.reason === "manual")?.id;
      return Boolean(backupId);
    });
    await until(() => js("document.body.textContent.includes('个文件')"));
    check("manual backup exposed in the page", true);
    check("settings remain within the window width", await js("document.documentElement.scrollWidth <= innerWidth"));
    if (mode === "update") {
      await js("[...document.querySelectorAll('h2')].find(e=>e.textContent.includes('配置备份')).scrollIntoView()");
      fs.writeFileSync(path.join(fixture, "backups.png"), (await win.webContents.capturePage()).toPNG());
    }
    if (mode === "restore") {
      fs.writeFileSync(path.join(configs, "probe-ledger.json"), '{"progress":"after"}');
      fs.writeFileSync(path.join(configs, "new-probe-file.json"), "{}");
      check("restore requires explicit confirmation", (await api("/api/desktop/backups/restore", { id: backupId })).status === 400);
      await until(() => click("恢复"));
      await until(() => click("停止任务并恢复"));
    } else {
      await until(() => click("检查更新"));
      await until(() => click("更新到 v0.9.8"));
      await until(() => click("停止任务并更新"));
      if (mode !== "update") {
        await until(async () => await js("document.body.textContent.includes('维护未完成')").catch(() => false));
        check("failed maintenance never launches updater", command === undefined);
        check("current configuration kept", fs.readFileSync(path.join(configs, "probe-ledger.json"), "utf8").includes("before"));
        check("failure remains visible after core exit", core.state === "exited");
        if (mode === "backup-failure") check("real helper encountered unwritable backup location", failBackup);
        finish();
      }
    }
  })().catch(async (error) => {
    if (core) await core.stop("probe-failed");
    finish(error);
  });
}
