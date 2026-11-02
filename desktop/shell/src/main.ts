// Electron main process: one window, one tray icon, one supervised Python core.
import {
  BrowserWindow,
  Menu,
  Notification,
  Tray,
  app,
  dialog,
  globalShortcut,
  ipcMain,
  nativeImage,
  session,
  screen,
  shell,
  type IpcMainInvokeEvent,
} from "electron";
import * as fs from "node:fs";
import * as path from "node:path";
import { CoreError, CoreProcess, type CoreExit } from "./core-process";
import { ExecutionToggle, HotkeyBinding, QuitGate, type ToggleFailure } from "./desktop-control";
import { isAllowedExternal, isStatusPage, navigationAllowed, sameOrigin } from "./navigation";
import { SUPPORTED_API, type CoreMessage } from "./protocol";
import {
  fitBounds,
  minimumSize,
  parseShellArgs,
  readDesktopState,
  ZOOM_LEVELS,
  type Bounds,
  type ShellOptions,
} from "./shell-config";
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
// Only while a key is held by another program or the settings could not be read.
const DESKTOP_RETRY_MS = 5000;

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
const quit = new QuitGate();
const desktopFile = path.join(app.getPath("userData"), "desktop-state.json");
const desktopState = readDesktopState(desktopFile);
let closeToTray = false;
const hotkey = new HotkeyBinding(
  {
    register: (key, callback) => globalShortcut.register(key, callback),
    unregister: (key) => globalShortcut.unregister(key),
  },
  () => toggleExecution(),
);
const executionToggle = new ExecutionToggle(reportControlFailure);
let syncError: string | null = null;
let desktopSync: Promise<void> = Promise.resolve();
let desktopRetry: ReturnType<typeof setTimeout> | null = null;
let saveTimer: ReturnType<typeof setTimeout> | null = null;
let controlDialogOpen = false;
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

function showWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function saveDesktopState(): void {
  fs.mkdirSync(path.dirname(desktopFile), { recursive: true });
  fs.writeFileSync(`${desktopFile}.tmp`, JSON.stringify(desktopState), "utf8");
  fs.renameSync(`${desktopFile}.tmp`, desktopFile);
}

function rememberWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.isMinimized()) return;
  desktopState.bounds = mainWindow.getNormalBounds();
  desktopState.maximized = mainWindow.isMaximized();
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try { saveDesktopState(); } catch (error) { log.warn(`window state: ${String(error)}`); }
  }, 300);
}

function flushDesktopState(): void {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = null;
  try { saveDesktopState(); } catch (error) { log.warn(`desktop state: ${String(error)}`); }
}

function toggleExecution(): void {
  const process_ = core;
  const ready = process_?.ready;
  if (quit.started || process_?.state !== "ready" || !ready) return;
  void executionToggle.run(() => fetch(`${ready.origin}/api/desktop/toggle-execution`, {
    method: "POST", headers: { Authorization: `Bearer ${process_.token}` },
    signal: AbortSignal.timeout(5000),
  }));
}

function reportControlFailure(failure: ToggleFailure): void {
  log.warn(`desktop control (${failure.kind}): ${failure.detail}`);
  if (quit.started) return;
  // A task may still be running: a notification says it without taking the foreground from the game.
  if (failure.kind === "unknown" && deliverNotification("无法确认启动或暂停的结果", failure.detail, true)) return;
  showWindow();
  if (controlDialogOpen || !mainWindow || mainWindow.isDestroyed()) return;
  controlDialogOpen = true;
  void dialog.showMessageBox(mainWindow, {
    type: "warning", title: "ok-bd2", message: "无法启动或暂停", detail: failure.detail,
  }).catch((error) => log.warn(`desktop control dialog: ${String(error)}`))
    .finally(() => { controlDialogOpen = false; });
}

async function readDesktopPreferences(process_: CoreProcess): Promise<void> {
  try {
    if (process_ !== core || quit.started || process_.state !== "ready") return;
    const result = await apiGet("/api/desktop/preferences") as {
      status: number; body: { closeToTray: boolean; hotkey: string };
    } | null;
    if (process_ !== core || quit.started || process_.state !== "ready") return;
    if (!result || result.status !== 200) throw new Error("无法读取桌面设置");
    closeToTray = result.body.closeToTray === true;
    hotkey.apply(result.body.hotkey);
    syncError = null;
  } catch (error) {
    // The key stays registered: its callback checks the core itself, and a core that is only slow
    // must not lose the hotkey to another program.
    if (process_ === core) syncError = `桌面设置暂不可用：${String(error)}`;
  } finally {
    retryDesktopSoon();
  }
}

// One read after another, so a read that started before a save can never hide the saved value.
function syncDesktop(): Promise<void> {
  const process_ = core;
  if (quit.started || process_?.state !== "ready") return Promise.resolve();
  desktopSync = desktopSync.then(() => readDesktopPreferences(process_));
  return desktopSync;
}

function retryDesktopSoon(): void {
  if (desktopRetry || quit.started || !(syncError || hotkey.waitingForKey)) return;
  desktopRetry = setTimeout(() => {
    desktopRetry = null;
    void syncDesktop();
  }, DESKTOP_RETRY_MS);
}

function desktopInfo() {
  return { zoom: desktopState.zoom, zoomLevels: ZOOM_LEVELS, closeToTray,
    hotkey: hotkey.requested, registeredHotkey: hotkey.registered, hotkeyError: hotkey.error, syncError,
    trayAvailable: tray !== null };
}

/** False when the system could not be asked to show it. */
function deliverNotification(title: string, body: string, error: boolean): boolean {
  if (quit.started) return false;
  try {
    if (process.platform === "win32" && tray) {
      tray.displayBalloon({ title, content: body, iconType: error ? "error" : "info" });
      return true;
    }
    if (Notification.isSupported()) {
      const notification = new Notification({ title, body });
      notification.on("click", showWindow);
      notification.show();
      return true;
    }
  } catch (reason) {
    log.warn(`notification unavailable: ${String(reason)}`);
  }
  return false;
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
  hotkey.reset();
  syncError = null;
  core = process_;
  process_.on("message", (message: CoreMessage) => {
    if (core === process_ && message.type === "notification") {
      deliverNotification(message.title || "ok-bd2", message.message, message.error);
    }
  });
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
    await syncDesktop();
    if (quit.started || core !== process_) return;
    if (process_.state !== "ready") throw new CoreError("exited", "核心在页面加载前退出");
    await mainWindow.loadURL(`${ready.origin}/launch/${process_.token}`);
    smoke.pageLoadedMs = Date.now() - startedAt;
    smoke.pageUrl = mainWindow.webContents.getURL().replace(process_.token, "<token>");
    if (options.smokeTest) {
      await runSmokeCycle(options.smokeTest);
    }
  } catch (error) {
    log.error(`core start failed: ${error instanceof Error ? error.message : String(error)}`);
    // A core that dies during the page load has its crash page already; it says more than this.
    if (core === process_ && !quit.started && status.kind !== "crashed") {
      showStatus(describeFailure(error));
      showWindow();
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

async function checkPageInterfaces(): Promise<void> {
  const required: Record<string, string[] | null> = {
    "/api/desktop/preferences": ["closeToTray", "hotkey"],
    "/api/bd2/today": ["items", "batch", "refresh"],
    "/api/bd2/history": null,
    "/api/bd2/report": ["session_id", "snapshot", "can_resume", "result"],
    ["/api/bd2/hunt-plans/" + encodeURIComponent("快速狩猎")]: ["book", "revision", "presets", "conditions", "targets", "modes"],
    "/api/bd2/window": ["size", "ratio_ok", "resolutions", "default", "resizing"],
    "/api/bd2/game": ["game", "launcher"],
    "/api/settings": null,
    "/api/ui/capture": ["status", "devices", "capture_in_use", "capture_preference", "starter_wait"],
    "/api/logs?level=ALL": ["text"],
  };
  const checks: { path: string; status: number | null; valid: boolean }[] = [];
  smoke.pageInterfaces = checks;
  for (const [pathname, fields] of Object.entries(required)) {
    const response = (await apiGet(pathname)) as { status: number; body: unknown } | null;
    const body = response?.body;
    const valid = response?.status === 200 && (fields === null
      ? Array.isArray(body)
      : body !== null && typeof body === "object" && fields.every((field) => field in body));
    // Keep credentials from /api/settings out of the smoke report.
    checks.push({ path: pathname, status: response?.status ?? null, valid });
    if (!valid) {
      throw new Error(`Page API contract failed: ${pathname}`);
    }
  }
}

async function checkPageNavigation(): Promise<void> {
  const pages: { page: string; heading: string | null; passed: boolean }[] = [];
  smoke.pages = pages;
  for (const label of ["任务", "记录", "日志", "设备", "设置", "问题报告", "今日"]) {
    if (!mainWindow || mainWindow.isDestroyed()) {
      throw new Error("Client window disappeared during page checks");
    }
    const clicked = await mainWindow.webContents.executeJavaScript(
      `(() => { const button = document.querySelector('nav button.nav[title="${label}"]');` +
        " if (!button) return false; button.click(); return true; })()",
    );
    const deadline = Date.now() + 5000;
    let state: Record<string, string> | null = null;
    let selected = false;
    while (clicked && Date.now() < deadline) {
      selected = await mainWindow.webContents.executeJavaScript(
        `document.querySelector('nav button.nav[title="${label}"]')?.getAttribute('aria-current') === 'page'`,
      );
      state = await pageState();
      if (selected && state?.heading && state.core === "ok" && state.events === "connected") {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const passed = Boolean(selected && state?.heading && state.core === "ok" && state.events === "connected");
    pages.push({ page: label, heading: state?.heading ?? null, passed });
    if (!passed) {
      throw new Error(`Page navigation failed: ${label}`);
    }
  }
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
    await checkPageInterfaces();
    await checkPageNavigation();
    smoke.title = mainWindow?.webContents.getTitle();
    await syncDesktop();
    smoke.desktop = desktopInfo();
  } catch (error) {
    smoke.apiError = String(error);
  }
  const before = Date.now();
  quit.started = true;
  const result = core ? await core.stop("smoke-test") : null;
  smoke.stop = result;
  smoke.stopMs = Date.now() - before;
  const client = smoke.client as Record<string, string> | null;
  smoke.ok =
    result !== null &&
    !result.forced &&
    result.exitCode === 0 &&
    !smoke.apiError &&
    syncError === null &&
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
  if (core !== process_ || exit.requested || quit.started) {
    return;
  }
  hotkey.release();
  if (process_.ready === null) {
    return; // start() reports failures before ready
  }
  if (exit.coreReason !== null) {
    // The core ended itself (for example an update or "exit after task"): leave too.
    quit.started = true;
    quitting = true;
    flushDesktopState();
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
  showWindow();
}

async function restartCore(): Promise<void> {
  if (quit.started) {
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

async function confirmQuit(): Promise<boolean> {
  const running = await currentTask();
  if (!running || quit.started || !mainWindow || mainWindow.isDestroyed()) {
    return true;
  }
  showWindow();
  const choice = await dialog.showMessageBox(mainWindow, {
    type: "warning",
    buttons: ["退出并停止任务", "取消"],
    defaultId: 1,
    cancelId: 1,
    title: "ok-bd2",
    message: `任务"${running}"正在运行。`,
    detail: "退出会停止当前任务。",
  });
  return choice.response === 0;
}

async function beginQuit(reason: string): Promise<void> {
  if (!(await quit.begin(reason, confirmQuit))) {
    return;
  }
  hotkey.release();
  if (desktopRetry) clearTimeout(desktopRetry);
  flushDesktopState();
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
  return event.sender === mainWindow?.webContents && event.senderFrame === event.sender.mainFrame &&
    (isStatusPage(url, STATUS_PAGE) || sameOrigin(url, coreOrigin()));
}

function registerIpc(): void {
  ipcMain.handle("shell:desktop", async (event) => {
    if (!trustedSender(event)) throw new Error("页面无权访问桌面设置");
    await syncDesktop();
    return desktopInfo();
  });
  ipcMain.handle("shell:zoom", (event, zoom: unknown) => {
    if (!trustedSender(event) || typeof zoom !== "number" || !ZOOM_LEVELS.includes(zoom)) {
      throw new Error("无效的界面缩放");
    }
    const previous = desktopState.zoom;
    desktopState.zoom = zoom;
    try { saveDesktopState(); } catch (error) { desktopState.zoom = previous; throw error; }
    if (mainWindow) {
      mainWindow.webContents.setZoomFactor(zoom / 100);
      applyMinimumSize(mainWindow);
    }
    return desktopInfo();
  });
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

function workAreas(): Bounds[] {
  return [screen.getPrimaryDisplay().workArea, ...screen.getAllDisplays().map((display) => display.workArea)];
}

// Zooming in must not take the pages below the width they were laid out for.
function applyMinimumSize(window: BrowserWindow): void {
  const normal = window.getNormalBounds();
  const minimum = minimumSize(desktopState.zoom, screen.getDisplayMatching(normal).workArea);
  window.setMinimumSize(minimum.width, minimum.height);
  if (!window.isMaximized() && !window.isMinimized() &&
      (normal.width < minimum.width || normal.height < minimum.height)) {
    window.setBounds(fitBounds(normal, workAreas(), desktopState.zoom));
  }
}

function createWindow(): BrowserWindow {
  const bounds = fitBounds(desktopState.bounds, workAreas(), desktopState.zoom);
  const minimum = minimumSize(desktopState.zoom, screen.getDisplayMatching(bounds).workArea);
  const window = new BrowserWindow({
    ...bounds,
    minWidth: minimum.width,
    minHeight: minimum.height,
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
  window.once("ready-to-show", () => {
    if (desktopState.maximized) window.maximize();
    window.show();
  });
  window.on("resize", rememberWindow);
  window.on("move", rememberWindow);
  window.on("maximize", rememberWindow);
  window.on("unmaximize", rememberWindow);
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
    contents.setZoomFactor(desktopState.zoom / 100);
    if (isStatusPage(contents.getURL(), STATUS_PAGE)) {
      contents.send("shell:status", status);
    }
  });
  contents.on("render-process-gone", (_event, details) => {
    log.error(`renderer gone: ${details.reason} (${details.exitCode})`);
    if (details.reason !== "clean-exit" && !quit.started) {
      contents.reload();
    }
  });
  contents.on("unresponsive", () => log.warn("renderer unresponsive"));
  window.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      // Only a running core has anything to keep alive in the tray.
      if (!quit.started && closeToTray && tray && core?.state === "ready") {
        rememberWindow();
        window.hide();
      } else void beginQuit("window-closed");
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
  try { tray = new Tray(iconImage()); }
  catch (error) { log.warn(`tray unavailable: ${String(error)}`); return; }
  tray.setToolTip("ok-bd2");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "显示窗口",
        click: showWindow,
      },
      { label: "启动 / 暂停", click: () => toggleExecution() },
      { type: "separator" },
      { label: "退出", click: () => void beginQuit("tray-exit") },
    ]),
  );
  tray.on("click", showWindow);
  tray.on("balloon-click", showWindow);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.setAppUserModelId(APP_ID);
  Menu.setApplicationMenu(null);
  app.on("second-instance", showWindow);
  app.on("will-quit", () => hotkey.release());
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
