// Electron main process: one window, one tray icon, one supervised Python core.
import {
  BrowserWindow,
  Menu,
  Tray,
  app,
  dialog,
  ipcMain,
  nativeImage,
  session,
  shell,
  type IpcMainInvokeEvent,
} from "electron";
import * as fs from "node:fs";
import * as path from "node:path";
import { CoreError, CoreProcess, type CoreExit } from "./core-process";
import { isAllowedExternal, isStatusPage, navigationAllowed, sameOrigin } from "./navigation";
import { SUPPORTED_API } from "./protocol";
import { parseShellArgs, type ShellOptions } from "./shell-config";
import { ShellLog } from "./shell-log";

type StatusKind = "starting" | "error" | "crashed" | "exiting";

interface ShellStatus {
  kind: StatusKind;
  title: string;
  message: string;
  details: string[];
  canRestart: boolean;
}

const APP_ID = "io.github.godraymond233.okbd2.desktop";
const STATUS_PAGE = path.join(__dirname, "..", "static", "status.html");
const PRELOAD = path.join(__dirname, "preload.js");
// No "persist:" prefix: the launch cookie lives in memory only.
const PARTITION = "okbd2-core";

const parsed = parseShellArgs(process.argv.slice(1));
const options: ShellOptions | null = "error" in parsed ? null : parsed;
if (options?.userDataDir) {
  // Must happen before the single-instance lock, which is scoped to userData.
  app.setPath("userData", options.userDataDir);
}
const log = new ShellLog(
  options ? options.logFile : path.join(app.getPath("logs"), "desktop-shell.log"),
);

let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let core: CoreProcess | null = null;
let quitting = false;
let quitStarted = false;
const startedAt = Date.now();
const smoke: Record<string, unknown> = { shellPid: process.pid };
let status: ShellStatus = {
  kind: "starting",
  title: "正在启动 ok-bd2",
  message: "正在启动自动化核心，请稍候。",
  details: [],
  canRestart: false,
};

function coreOrigin(): string | null {
  return core?.ready?.origin ?? null;
}

function logsDirectory(): string {
  return path.dirname(log.file);
}

function showStatus(next: ShellStatus): void {
  status = next;
  const window = mainWindow;
  if (!window || window.isDestroyed()) {
    return;
  }
  if (isStatusPage(window.webContents.getURL(), STATUS_PAGE)) {
    window.webContents.send("shell:status", status);
  } else {
    void window.loadFile(STATUS_PAGE);
  }
}

function describeFailure(error: unknown): ShellStatus {
  const details = [`日志文件夹：${logsDirectory()}`];
  if (!(error instanceof CoreError)) {
    return { kind: "error", title: "核心启动失败", message: String(error), details, canRestart: true };
  }
  const tail = error.details.stderrTail?.slice(-40) ?? [];
  if (error.kind === "fatal" && error.details.code === "executor-locked") {
    return {
      kind: "error",
      title: "另一个 ok-bd2 正在运行",
      message: error.message,
      details,
      canRestart: true,
    };
  }
  const titles: Record<string, string> = {
    spawn: "无法启动自动化核心",
    fatal: "自动化核心启动失败",
    exited: "自动化核心启动失败",
    timeout: "自动化核心启动超时",
    incompatible: "外壳与核心版本不兼容",
  };
  const exitCode = error.details.exitCode;
  return {
    kind: "error",
    title: titles[error.kind] ?? "自动化核心启动失败",
    message: error.message,
    details: [
      ...(exitCode !== undefined && exitCode !== null ? [`退出码：${exitCode}`] : []),
      ...details,
      ...tail,
    ],
    canRestart: error.kind !== "incompatible",
  };
}

async function startCore(): Promise<void> {
  if (!options) {
    return;
  }
  showStatus({
    kind: "starting",
    title: "正在启动 ok-bd2",
    message: "正在启动自动化核心，请稍候。",
    details: [],
    canRestart: false,
  });
  const process_ = new CoreProcess(options.core);
  core = process_;
  process_.on("exit", (exit: CoreExit) => onCoreExit(process_, exit));
  log.info(`starting core: ${options.core.python} ${options.core.script} (cwd ${options.core.cwd})`);
  try {
    const ready = await process_.start();
    log.info(`core ready: pid=${ready.pid} port=${ready.port} api=${ready.api} version=${ready.coreVersion}`);
    smoke.coreReadyMs = Date.now() - startedAt;
    smoke.corePid = ready.pid;
    smoke.coreVersion = ready.coreVersion;
    smoke.api = ready.api;
    if (core !== process_ || !mainWindow || mainWindow.isDestroyed()) {
      return;
    }
    await mainWindow.loadURL(`${ready.origin}/launch/${process_.token}`);
    smoke.pageLoadedMs = Date.now() - startedAt;
    smoke.pageUrl = mainWindow.webContents.getURL().replace(process_.token, "<token>");
    if (options.smokeTest) {
      await runSmokeCycle(options.smokeTest);
    }
  } catch (error) {
    log.error(`core start failed: ${error instanceof Error ? error.message : String(error)}`);
    if (core === process_ && !quitStarted) {
      showStatus(describeFailure(error));
    }
    if (options.smokeTest) {
      smoke.ok = false;
      smoke.error = describeFailure(error);
      writeSmoke(options.smokeTest);
      quitting = true;
      app.exit(1);
    }
  }
}

function writeSmoke(file: string): void {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(smoke, null, 2), "utf8");
  } catch (error) {
    log.error(`could not write smoke report: ${String(error)}`);
  }
}

async function apiGet(pathname: string): Promise<unknown> {
  const ready = core?.ready;
  if (!core || !ready) {
    return null;
  }
  const response = await fetch(`${ready.origin}${pathname}`, {
    headers: { Authorization: `Bearer ${core.token}` },
    signal: AbortSignal.timeout(5000),
  });
  return { status: response.status, body: await response.json() };
}

async function pageState(): Promise<Record<string, string> | null> {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return null;
  }
  return (await mainWindow.webContents.executeJavaScript(
    "(() => { const root = document.querySelector('[data-core]');" +
      " return root ? { ...root.dataset, heading: document.querySelector('h1')?.textContent ?? ''," +
      " banner: document.querySelector('.banner.error')?.textContent ?? '' } : null; })()",
  )) as Record<string, string> | null;
}

async function waitForClient(timeoutMs: number): Promise<Record<string, string> | null> {
  const deadline = Date.now() + timeoutMs;
  let state: Record<string, string> | null = null;
  while (Date.now() < deadline) {
    state = await pageState().catch(() => null);
    if (state?.core === "ok" && state.events === "connected") {
      return state;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return state;
}

async function runSmokeCycle(file: string): Promise<void> {
  try {
    smoke.client = await waitForClient(15_000);
    smoke.clientReadyMs = Date.now() - startedAt;
    smoke.status = await apiGet("/api/status");
    smoke.info = await apiGet("/api/desktop/info");
    smoke.tasks = ((await apiGet("/api/tasks")) as { body?: { name: string }[] } | null)?.body?.map(
      (task) => task.name,
    );
    smoke.title = mainWindow?.webContents.getTitle();
  } catch (error) {
    smoke.apiError = String(error);
  }
  const before = Date.now();
  quitStarted = true;
  const result = core ? await core.stop("smoke-test") : null;
  smoke.stop = result;
  smoke.stopMs = Date.now() - before;
  const client = smoke.client as Record<string, string> | null;
  smoke.ok =
    result !== null &&
    !result.forced &&
    result.exitCode === 0 &&
    !smoke.apiError &&
    client?.core === "ok" &&
    client?.events === "connected";
  writeSmoke(file);
  quitting = true;
  tray?.destroy();
  app.exit(smoke.ok ? 0 : 1);
}

function onCoreExit(process_: CoreProcess, exit: CoreExit): void {
  log.info(
    `core exited: code=${exit.exitCode} signal=${exit.signal} requested=${exit.requested} ` +
      `reason=${exit.coreReason}`,
  );
  if (core !== process_ || exit.requested || quitStarted) {
    return;
  }
  if (process_.ready === null) {
    return; // start() reports failures before ready
  }
  if (exit.coreReason !== null) {
    // The core ended itself (for example an update or "exit after task"): leave too.
    quitStarted = true;
    quitting = true;
    app.exit(0);
    return;
  }
  log.error(`core crashed; last output:\n${exit.stderrTail.slice(-40).join("\n")}`);
  showStatus({
    kind: "crashed",
    title: "自动化核心意外退出",
    message: "核心进程已经结束，游戏可能停在任意页面。确认游戏状态后，可以重新启动核心。",
    details: [
      `退出码：${exit.exitCode}`,
      `日志文件夹：${logsDirectory()}`,
      ...exit.stderrTail.slice(-40),
    ],
    canRestart: true,
  });
}

async function restartCore(): Promise<void> {
  if (quitStarted) {
    return;
  }
  const previous = core;
  if (previous && previous.state !== "exited") {
    await previous.stop("restart");
  }
  await startCore();
}

async function currentTask(): Promise<string | null> {
  const ready = core?.ready;
  if (!core || !ready || core.state !== "ready") {
    return null;
  }
  try {
    const response = await fetch(`${ready.origin}/api/status`, {
      headers: { Authorization: `Bearer ${core.token}` },
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) {
      return null;
    }
    const data = (await response.json()) as { current_task?: string | null };
    return data.current_task ?? null;
  } catch {
    return null;
  }
}

async function beginQuit(reason: string): Promise<void> {
  if (quitStarted) {
    return;
  }
  if (reason !== "session-end") {
    const running = await currentTask();
    if (running && mainWindow && !mainWindow.isDestroyed()) {
      const choice = await dialog.showMessageBox(mainWindow, {
        type: "warning",
        buttons: ["退出并停止任务", "取消"],
        defaultId: 1,
        cancelId: 1,
        title: "ok-bd2",
        message: `任务"${running}"正在运行。`,
        detail: "退出会停止当前任务。",
      });
      if (choice.response !== 0) {
        return;
      }
    }
  }
  quitStarted = true;
  log.info(`quit requested: ${reason}`);
  showStatus({
    kind: "exiting",
    title: "正在安全退出",
    message: "正在停止任务并保存数据，请稍候。",
    details: [],
    canRestart: false,
  });
  if (core) {
    const result = await core.stop(reason);
    log.info(
      `core stopped: code=${result.exitCode} forced=${result.forced} after ${result.durationMs} ms`,
    );
  }
  quitting = true;
  tray?.destroy();
  app.exit(0);
}

function trustedSender(event: IpcMainInvokeEvent): boolean {
  const url = event.senderFrame?.url ?? "";
  return isStatusPage(url, STATUS_PAGE) || sameOrigin(url, coreOrigin());
}

function registerIpc(): void {
  ipcMain.handle("shell:get-status", (event) => (trustedSender(event) ? status : null));
  ipcMain.handle("shell:info", (event) =>
    trustedSender(event)
      ? {
          shellVersion: app.getVersion(),
          electron: process.versions.electron,
          api: SUPPORTED_API,
        }
      : null,
  );
  ipcMain.handle("shell:restart-core", async (event) => {
    if (trustedSender(event) && (status.kind === "error" || status.kind === "crashed")) {
      await restartCore();
    }
  });
  ipcMain.handle("shell:open-logs", async (event) => {
    if (trustedSender(event)) {
      await shell.openPath(logsDirectory());
    }
  });
  ipcMain.handle("shell:quit", (event) => {
    if (trustedSender(event)) {
      void beginQuit("user");
    }
  });
}

function iconImage(): Electron.NativeImage {
  const file = options ? path.join(options.core.cwd, "icons", "icon.png") : "";
  return file && fs.existsSync(file) ? nativeImage.createFromPath(file) : nativeImage.createEmpty();
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 600,
    minHeight: 450,
    show: false,
    title: "ok-bd2",
    icon: iconImage(),
    autoHideMenuBar: true,
    webPreferences: {
      preload: PRELOAD,
      partition: PARTITION,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      spellcheck: false,
      devTools: options?.devTools ?? false,
    },
  });
  window.once("ready-to-show", () => window.show());
  const contents = window.webContents;
  const guard = (event: Electron.Event, url: string) => {
    if (!navigationAllowed(url, coreOrigin(), STATUS_PAGE)) {
      event.preventDefault();
      log.warn(`blocked navigation to ${url}`);
      if (isAllowedExternal(url)) {
        void shell.openExternal(url);
      }
    }
  };
  contents.on("will-navigate", guard);
  contents.on("will-redirect", guard);
  contents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternal(url)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  contents.on("did-finish-load", () => {
    if (isStatusPage(contents.getURL(), STATUS_PAGE)) {
      contents.send("shell:status", status);
    }
  });
  contents.on("render-process-gone", (_event, details) => {
    log.error(`renderer gone: ${details.reason} (${details.exitCode})`);
    if (details.reason !== "clean-exit" && !quitStarted) {
      contents.reload();
    }
  });
  contents.on("unresponsive", () => log.warn("renderer unresponsive"));
  window.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      void beginQuit("window-closed");
    }
  });
  window.on("session-end", () => {
    void beginQuit("session-end");
  });
  return window;
}

function createTray(): void {
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
    // No desktop session (headless test runs): a status icon would crash GTK.
    log.warn("no desktop session; tray icon disabled");
    return;
  }
  tray = new Tray(iconImage());
  tray.setToolTip("ok-bd2");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "显示窗口",
        click: () => {
          mainWindow?.show();
          mainWindow?.focus();
        },
      },
      { type: "separator" },
      { label: "退出", click: () => void beginQuit("tray-exit") },
    ]),
  );
  tray.on("click", () => {
    mainWindow?.show();
    mainWindow?.focus();
  });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.setAppUserModelId(APP_ID);
  Menu.setApplicationMenu(null);
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.show();
      mainWindow.focus();
    }
  });
  app.on("before-quit", (event) => {
    if (!quitting) {
      event.preventDefault();
      void beginQuit("app-quit");
    }
  });
  app.on("window-all-closed", () => {
    // Quitting always goes through beginQuit so the core gets a safe exit.
  });
  void app.whenReady().then(() => {
    const partition = session.fromPartition(PARTITION);
    partition.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    partition.setPermissionCheckHandler(() => false);
    registerIpc();
    mainWindow = createWindow();
    createTray();
    void mainWindow.loadFile(STATUS_PAGE);
    if (!options) {
      const message = "error" in parsed ? parsed.error : "参数无效";
      log.error(`invalid shell arguments: ${message}`);
      showStatus({
        kind: "error",
        title: "启动参数无效",
        message: `${message}。请通过 ok-bd2 启动器启动。`,
        details: [],
        canRestart: false,
      });
      return;
    }
    void startCore();
  });
}
