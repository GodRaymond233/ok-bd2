// The desktop client: navigation rail, pages and the run panel shared by all pages.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.ts";
import { connectEvents } from "./events.ts";
import { t } from "./i18n.ts";
import { DevicePage } from "./DevicePage.tsx";
import { appendRecords, LOG_LIMIT, type LogRecord, parseLog, recordsFromEvent } from "./logs.ts";
import { LogsPage } from "./LogsPage.tsx";
import { RecordsPage } from "./OtherPages.tsx";
import { ReportPage } from "./ReportPage.tsx";
import { type DockRun, RunDock } from "./RunDock.tsx";
import { childRuns, isActive, sealOf, shutdownSeconds } from "./run.ts";
import { SettingsPage } from "./SettingsPage.tsx";
import { shellSettingsKey } from "./settings.ts";
import { captureSummary, type Notice, noticeFromEvent } from "./state.ts";
import { TasksPage } from "./TasksPage.tsx";
import { TodayPage } from "./TodayPage.tsx";
import { type BatchRun, withBatchConfig } from "./today.ts";
import type {
  CaptureUi,
  DesktopInfo,
  Device,
  HistoryRun,
  HuntBook,
  SettingsGroup,
  ShellBridge,
  TaskPayload,
  TodayItem,
  TodaySummary,
} from "./types.ts";
import { Icon, SharedDefs } from "./ui.tsx";

declare global {
  interface Window {
    okbd2Shell?: ShellBridge;
  }
}

export type Page = "today" | "tasks" | "records" | "logs" | "device" | "settings" | "report";

export interface Core {
  ui: CaptureUi | null;
  tasks: TaskPayload[];
  today: TodaySummary | null;
  info: DesktopInfo | null;
  /** ok-script's global settings; null until loaded. */
  settings: SettingsGroup[] | null;
  /** The last log records: the log file's tail at start, then the live ones. */
  logs: LogRecord[];
  /** Seconds since the epoch; ticks every second while a run is active. */
  now: number;
  current: string | null;
  /** A task runs or waits in the queue, or the game is starting. */
  busy: boolean;
  /** Why starting a task is refused right now, if it is. */
  startBlocked: string | null;
  batchRun: BatchRun | null;
  allDone: boolean;
  device: Device | null;
  deviceName: string;
  captureText: string;
  /** Changes whenever a task finishes, so run lists refetch. */
  historyVersion: number;
  /** The task waiting for the game while it starts. */
  starterTask: string | null;
  /** Why the last game start failed, until dismissed or the next start. */
  startError: string | null;
  childStage: (item: TodayItem) => string;
  act: (action: () => Promise<unknown>) => Promise<boolean>;
  setConfig: (task: string, key: string, value: unknown) => Promise<TaskPayload>;
  saveHuntPlans: (task: string, book: HuntBook, revision: string) => Promise<TaskPayload>;
  resetTask: (task: string) => Promise<TaskPayload>;
  startTask: (task: string) => Promise<TaskPayload>;
  startDaily: (mode: "all" | "incomplete") => Promise<TaskPayload>;
  taskAction: (task: string, action: "pause" | "resume" | "stop" | "enable" | "disable") => Promise<TaskPayload>;
  history: (task: string, limit: number) => Promise<HistoryRun[]>;
  refreshDevices: () => Promise<unknown>;
  selectDevice: (id: string) => Promise<unknown>;
  selectCapture: (id: string) => Promise<unknown>;
  cancelStarter: () => Promise<unknown>;
  dismissStartError: () => void;
  setSetting: (group: string, key: string, value: unknown) => Promise<void>;
  resetSettings: (group: string) => Promise<void>;
  tool: (action: "capture" | "export-logs" | "install-folder" | "log-folder" | "screenshot-folder") => Promise<unknown>;
  openLogsFolder: () => Promise<void>;
  /** An information toast. */
  notify: (text: string) => void;
  navigate: (page: Page) => void;
  openTask: (name: string) => void;
  /** The logs page, showing only this run when given. */
  openLogs: (run: HistoryRun | null) => void;
}

const REFRESH_EVENTS = new Set(["task", "task_done", "task_list_updated", "executor_paused"]);
// Game start progress only changes the capture state (status.starting, starter_wait).
const CAPTURE_EVENTS = new Set(["starting_emulator", "starter_wait", "adb_devices"]);
const DOCK_KEY = "okbd2.dockExpanded";

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

function storedDockExpanded(): boolean {
  try {
    const value = localStorage.getItem(DOCK_KEY);
    if (value !== null) {
      return value === "1";
    }
  } catch {
    // storage can be unavailable; fall back to the width rule
  }
  return window.innerWidth >= 900;
}

const NAV: { page: Page; label: string; icon: string; later?: string }[] = [
  { page: "today", label: "今日", icon: "today" },
  { page: "tasks", label: "任务", icon: "tasks" },
  { page: "records", label: "记录", icon: "history" },
  { page: "logs", label: "日志", icon: "logs" },
  { page: "device", label: "设备", icon: "device" },
  { page: "settings", label: "设置", icon: "settings" },
];

export function App() {
  const [ui, setUi] = useState<CaptureUi | null>(null);
  const [tasks, setTasks] = useState<TaskPayload[]>([]);
  const [todaySnapshot, setToday] = useState<TodaySummary | null>(null);
  const today = useMemo(() => withBatchConfig(todaySnapshot, tasks), [todaySnapshot, tasks]);
  const [info, setInfo] = useState<DesktopInfo | null>(null);
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [settings, setSettings] = useState<SettingsGroup[] | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [logFocus, setLogFocus] = useState<HistoryRun | null>(null);
  const [coreError, setCoreError] = useState<string | null>(null);
  const [eventsConnected, setEventsConnected] = useState(false);
  const [everConnected, setEverConnected] = useState(false);
  const [toast, setToast] = useState<Notice | null>(null);
  const [page, setPage] = useState<Page>("today");
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [dockName, setDockName] = useState<string | null>(null);
  const [dockDismissed, setDockDismissed] = useState(false);
  const [dockRecord, setDockRecord] = useState<HistoryRun | null>(null);
  const [shutdownSeen, setShutdownSeen] = useState<number | null>(null);
  const [dockExpanded, setDockExpanded] = useState(storedDockExpanded);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const taskRevision = useRef(0);

  const refresh = useCallback(async () => {
    const revision = taskRevision.current;
    try {
      const [capture, taskList, summary, desktop, groups] = await Promise.all([
        api.capture(),
        api.tasks(),
        api.today(),
        api.info(),
        api.settings(),
      ]);
      setUi(capture);
      // A refresh started before a save must not overwrite the saved task response.
      if (revision === taskRevision.current) {
        setTasks(taskList);
      }
      setToday(summary);
      setInfo(desktop);
      setSettings(groups);
      setCoreError(null);
    } catch (reason) {
      setCoreError(message(reason));
    }
  }, []);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current !== null) {
      return;
    }
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void refresh();
    }, 300);
  }, [refresh]);

  useEffect(() => {
    let connection: { close(): void } | null = null;
    let cancelled = false;
    void (async () => {
      await refresh();
      try {
        const file = await api.logs();
        if (!cancelled) {
          setLogs((current) => appendRecords(parseLog(file.text).slice(-LOG_LIMIT), current));
        }
      } catch {
        // the log file is optional on a fresh install
      }
      const capture = await api.capture().catch(() => null);
      if (cancelled || !capture) {
        return;
      }
      connection = connectEvents(
        capture.event_session_key,
        (payload) => {
          const records = recordsFromEvent(payload, Date.now() / 1000);
          if (records.length) {
            setLogs((current) => appendRecords(current, records));
          }
          if (payload.event === "starting_emulator" && Array.isArray(payload.args)) {
            const [done, error] = payload.args;
            if (!done) {
              setStartError(null);
            } else if (typeof error === "string" && error) {
              setStartError(error);
            }
          }
          if (CAPTURE_EVENTS.has(payload.event)) {
            void api.capture().then(setUi, () => undefined);
          }
          if (payload.ui) {
            setUi(payload.ui);
          }
          const notice = noticeFromEvent(payload);
          if (notice) {
            setToast(notice);
          }
          if (payload.event === "task_done") {
            setHistoryVersion((version) => version + 1);
          }
          if (REFRESH_EVENTS.has(payload.event) || payload.resync) {
            scheduleRefresh();
          }
        },
        (connected) => {
          setEventsConnected(connected);
          if (connected) {
            setEverConnected(true);
            scheduleRefresh();
          }
        },
      );
      void api.uiReady().catch(() => undefined);
    })();
    return () => {
      cancelled = true;
      connection?.close();
    };
  }, [refresh, scheduleRefresh]);

  useEffect(() => {
    if (!toast) {
      return;
    }
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, [toast]);

  const current = ui?.status.current_task ?? null;
  const starting = Boolean(ui?.status.starting);

  // The run panel follows the task that ran last; a new run brings it back after a dismissal.
  useEffect(() => {
    if (current) {
      setDockName(current);
      setDockDismissed(false);
      setDockRecord(null);
      setShutdownSeen(null);
    }
  }, [current]);

  const dockTask = dockName ? tasks.find((task) => task.name === dockName) : undefined;
  const dockSeal = dockTask ? sealOf(dockTask, current) : null;
  const dockActive = dockSeal !== null && isActive(dockSeal);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), dockActive ? 1000 : 30_000);
    setNow(Date.now() / 1000);
    return () => clearInterval(timer);
  }, [dockActive]);

  // Once a run ends, its duration comes from the run record the core wrote.
  const dockStart = dockTask?.start_time ?? 0;
  useEffect(() => {
    if (!dockName || dockActive) {
      return;
    }
    let cancelled = false;
    api.history(dockName, 1).then(
      (runs) => {
        const run = runs[0];
        if (!cancelled && run && run.finished >= dockStart - 1) {
          setDockRecord(run);
        }
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [dockName, dockActive, dockStart, historyVersion]);

  // The shell applies the tray and hotkey settings, and any settings tab can reset them.
  const shellSettings = shellSettingsKey(settings);
  useEffect(() => {
    if (shellSettings) {
      window.okbd2Shell?.desktop?.().catch(() => undefined);
    }
  }, [shellSettings]);

  const shutdownTotal = dockTask && !dockActive ? shutdownSeconds(dockTask) : null;
  useEffect(() => {
    if (shutdownTotal !== null && shutdownSeen === null) {
      setShutdownSeen(Date.now() / 1000);
    }
  }, [shutdownTotal, shutdownSeen]);

  const batch = today?.batch ? tasks.find((task) => task.name === today.batch?.name) : undefined;
  const showRun = Boolean(dockTask && dockSeal && !dockDismissed);
  const batchRun = useMemo<BatchRun | null>(() => {
    if (!showRun || !batch || !dockSeal || dockTask?.name !== batch.name || !today) {
      return null;
    }
    return { batch, seal: dockSeal, runs: childRuns(batch, today.items.filter((item) => item.enabled), dockSeal) };
  }, [showRun, batch, dockSeal, dockTask, today]);

  const dockRun: DockRun | null =
    showRun && dockTask && dockSeal
      ? {
          task: dockTask,
          seal: dockSeal,
          record: dockRecord,
          shutdownLeft:
            shutdownTotal !== null && shutdownSeen !== null ? Math.max(0, shutdownTotal - (now - shutdownSeen)) : null,
        }
      : null;

  const queued = tasks.find((task) => !task.trigger && task.enabled && task.name !== current);
  const busy = Boolean(current) || Boolean(queued) || starting || Boolean(ui?.diagnostic_pause) || Boolean(ui?.maintenance);
  const legacy = info && info.legacy_instances.length > 0 ? info.legacy_instances : null;
  const startBlocked = ui?.maintenance ? t("正在准备更新或恢复备份") : ui?.diagnostic_pause
    ? t("请先完成或取消问题报告")
    : legacy
    ? t("旧版 ok-bd2 正在运行，请先关闭它")
    : starting
      ? t("正在启动游戏")
      : busy
        ? `${t("正在运行")} ${current ?? queued?.name ?? ""}`
        : null;
  const devices = ui?.devices ?? [];
  const device = devices.find((item) => item.selected) ?? devices[0] ?? null;
  const taking = today?.items.filter((item) => item.enabled) ?? [];

  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      try {
        await action();
        return true;
      } catch (reason) {
        setToast({ text: message(reason), error: true });
        return false;
      } finally {
        scheduleRefresh();
      }
    },
    [scheduleRefresh],
  );

  const replaceGroup = useCallback((updated: SettingsGroup) => {
    setSettings((groups) => groups?.map((group) => (group.name === updated.name ? updated : group)) ?? [updated]);
  }, []);

  const replaceTask = useCallback((updated: TaskPayload) => {
    taskRevision.current += 1;
    setTasks((list) => list.map((task) => (task.name === updated.name ? updated : task)));
    return updated;
  }, []);

  const core: Core = {
    ui,
    tasks,
    today,
    info,
    settings,
    logs,
    now,
    current,
    busy,
    startBlocked,
    batchRun,
    allDone: taking.length > 0 && taking.every((item) => item.done),
    device,
    deviceName: device ? device.label.split("：").slice(1).join("：") || device.label : "",
    captureText: ui ? captureSummary(ui) : "",
    historyVersion,
    starterTask: starting ? (queued?.name ?? null) : startError ? (queued?.name ?? null) : null,
    startError,
    childStage: (item) => {
      const child = tasks.find((task) => task.name === item.name);
      const stage = String(child?.info["当前子任务"] || child?.info["状态"] || "");
      return stage === "-" ? "" : stage.replace(/。$/, "");
    },
    act,
    setConfig: async (task, key, value) => {
      const updated = replaceTask(await api.setTaskConfig(task, key, value));
      scheduleRefresh();
      return updated;
    },
    saveHuntPlans: async (task, book, revision) => {
      const updated = replaceTask(await api.saveHuntPlans(task, book, revision));
      scheduleRefresh();
      return updated;
    },
    resetTask: async (task) => replaceTask(await api.resetTaskConfig(task)),
    startTask: api.startTask,
    startDaily: api.startDaily,
    taskAction: (task, action) => api.taskAction(task, action),
    history: api.history,
    refreshDevices: api.refreshDevices,
    selectDevice: async (id) => setUi(await api.selectDevice(id)),
    selectCapture: async (id) => setUi(await api.selectCapture(id)),
    cancelStarter: api.cancelStarter,
    dismissStartError: () => setStartError(null),
    setSetting: async (group, key, value) => replaceGroup(await api.setSetting(group, key, value)),
    resetSettings: async (group) => replaceGroup(await api.resetSettings(group)),
    tool: api.tool,
    openLogsFolder: async () => {
      await window.okbd2Shell?.openLogsFolder();
    },
    notify: (text) => setToast({ text, error: false }),
    openLogs: (run) => {
      setLogFocus(run);
      setPage("logs");
    },
    navigate: (target) => {
      setPage(target);
      if (target === "logs") {
        setLogFocus(null);
      }
      if (target === "tasks") {
        setDetailOpen(false);
      }
    },
    openTask: (name) => {
      setSelectedTask(name);
      setDetailOpen(true);
      setPage("tasks");
    },
  };

  const setExpanded = (expanded: boolean) => {
    setDockExpanded(expanded);
    try {
      localStorage.setItem(DOCK_KEY, expanded ? "1" : "0");
    } catch {
      // remembering the choice is a convenience only
    }
  };

  const connected = Boolean(device?.connected);
  let content;
  switch (page) {
    case "tasks":
      content = (
        <TasksPage
          core={core}
          selected={selectedTask}
          detailOpen={detailOpen}
          onSelect={(name) => {
            setSelectedTask(name);
            setDetailOpen(true);
          }}
          onBack={() => setDetailOpen(false)}
        />
      );
      break;
    case "records":
      content = <RecordsPage core={core} />;
      break;
    case "report":
      content = <ReportPage core={core} />;
      break;
    case "logs":
      content = <LogsPage core={core} focus={logFocus} onFocus={setLogFocus} />;
      break;
    case "device":
      content = <DevicePage core={core} />;
      break;
    case "settings":
      content = <SettingsPage core={core} />;
      break;
    default:
      content = <TodayPage core={core} />;
  }

  return (
    <div
      className="app"
      data-core={coreError ? "error" : ui ? "ok" : "loading"}
      data-events={eventsConnected ? "connected" : "disconnected"}
      data-tasks={tasks.filter((task) => task.visible).length}
    >
      <SharedDefs />
      {coreError && (
        <div className="banner error" role="alert">
          <Icon name="alert" />
          <b>{t("无法连接核心")}</b>
          <span className="t">{coreError}</span>
          <button type="button" className="btn sm" onClick={() => void refresh()}>
            {t("重试")}
          </button>
        </div>
      )}
      {legacy && (
        <div className="banner warning" role="alert">
          <Icon name="alert" />
          <b>{t("旧版 ok-bd2 正在运行")}</b>
          <span className="t">
            {legacy.map((item) => item.description).join("；")}
            {t("。新客户端不能同时控制游戏，开始任务前请先关闭它。")}
          </span>
          <button type="button" className="btn sm" onClick={() => void refresh()}>
            {t("重新检查")}
          </button>
        </div>
      )}
      {ui?.diagnostic_pause && page !== "report" && <div className="banner info" role="status">
        <span className="t">{t("问题报告正在保留现场，执行器保持暂停。")}</span>
        <button className="btn sm" onClick={() => core.navigate("report")}>{t("返回问题报告")}</button>
      </div>}
      {info?.launcher?.outdated && (
        <div className="banner warning" role="alert">
          <Icon name="alert" />
          <b>{t("启动器版本过旧")}</b>
          <span className="t">{t("当前启动器不支持检查更新，以后的版本也装不上。请从下载页重新下载安装（配置和记录会保留）。")}</span>
          <a className="btn sm" href="https://github.com/GodRaymond233/ok-bd2/releases/latest" target="_blank" rel="noreferrer">
            {t("打开下载页")}
          </a>
        </div>
      )}
      {everConnected && !eventsConnected && !coreError && (
        <div className="banner info" role="status">
          <Icon name="refresh" />
          <span className="t">{t("与核心的实时连接中断，正在重新同步…")}</span>
        </div>
      )}
      <div className="app-body">
        <nav className="rail" aria-label={t("主导航")}>
          <div className="id">
            <span className="avatar" aria-hidden="true">
              BD
            </span>
            <span className="txt">
              <div className="nm">ok-bd2</div>
              <div className="ver">{info ? `v${info.core_version.replace(/^v/i, "")}` : "…"}</div>
            </span>
          </div>
          {NAV.map((item) => (
            <button
              key={item.page}
              type="button"
              className="nav"
              aria-current={page === item.page ? "page" : undefined}
              title={t(item.label)}
              onClick={() => core.navigate(item.page)}
            >
              <Icon name={item.icon} />
              <span className="tx">{t(item.label)}</span>
              {item.later && <span className="later">{t(item.later)}</span>}
            </button>
          ))}
          <div className="foot">
            <button
              type="button"
              className="nav conn"
              title={connected ? `${t("已连接")} ${device?.label ?? ""}` : t("未连接游戏")}
              onClick={() => core.navigate("device")}
            >
              <span className={connected ? "d" : "d off"} />
              <span className="t">
                {connected ? t("已连接") : t("未连接游戏")}
                <small>{connected ? core.captureText : t("点此查看")}</small>
              </span>
            </button>
            <button type="button" className="nav" title={t("问题报告")} aria-current={page === "report" ? "page" : undefined} onClick={() => core.navigate("report")}>
              <Icon name="report" />
              <span className="tx">{t("问题报告")}</span>
            </button>
          </div>
        </nav>
        <main className="main">{content}</main>
      </div>
      <RunDock
        core={core}
        run={dockRun}
        logs={logs}
        expanded={dockExpanded}
        onExpand={setExpanded}
        onDismiss={() => setDockDismissed(true)}
      />
      {toast && (
        <div className={toast.error ? "toast" : "toast info"} role={toast.error ? "alert" : "status"}>
          <Icon name={toast.error ? "alert" : "info"} />
          <span>{toast.text}</span>
          <button type="button" className="btn subtle sm icon" aria-label={t("关闭")} onClick={() => setToast(null)}>
            <Icon name="x" />
          </button>
        </div>
      )}
    </div>
  );
}
