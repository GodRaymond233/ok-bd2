// Stage 1 link check page: proves the shell → core → game chain. Not the final design.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.ts";
import { connectEvents } from "./events.ts";
import { t } from "./i18n.ts";
import {
  ACTION_LABELS,
  INITIAL_STATS,
  appendLogs,
  captureSummary,
  executorLabel,
  levelName,
  logFromEvent,
  logsFromFile,
  taskActions,
  taskState,
  trackEvent,
  type LogLine,
  type StreamStats,
  type TaskAction,
} from "./state.ts";
import type { CaptureUi, DesktopInfo, ShellBridge, ShellInfo, TaskPayload } from "./types.ts";

declare global {
  interface Window {
    okbd2Shell?: ShellBridge;
  }
}

const REFRESH_EVENTS = new Set(["task", "task_done", "task_list_updated", "executor_paused"]);

export function App() {
  const [ui, setUi] = useState<CaptureUi | null>(null);
  const [info, setInfo] = useState<DesktopInfo | null>(null);
  const [shellInfo, setShellInfo] = useState<ShellInfo | null>(null);
  const [tasks, setTasks] = useState<TaskPayload[]>([]);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [stats, setStats] = useState<StreamStats>(INITIAL_STATS);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [capture, taskList, desktop] = await Promise.all([api.capture(), api.tasks(), api.info()]);
      setUi(capture);
      setTasks(taskList);
      setInfo(desktop);
      setError(null);
    } catch (reason) {
      setError(String(reason instanceof Error ? reason.message : reason));
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
          setLogs((current) => appendLogs(logsFromFile(file.text).slice(-100), current));
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
          setStats((current) => trackEvent(current, payload));
          const line = logFromEvent(payload);
          if (line) {
            setLogs((current) => appendLogs(current, [line]));
          }
          if (payload.ui) {
            setUi(payload.ui);
          }
          if (REFRESH_EVENTS.has(payload.event) || payload.resync) {
            scheduleRefresh();
          }
        },
        (connected) => setStats((current) => ({ ...current, connected })),
      );
      void api.uiReady().catch(() => undefined);
    })();
    void window.okbd2Shell?.info().then(setShellInfo);
    return () => {
      cancelled = true;
      connection?.close();
    };
  }, [refresh, scheduleRefresh]);

  const run = useCallback(
    async (action: () => Promise<unknown>, done?: string) => {
      setBusy(true);
      setMessage(null);
      try {
        await action();
        if (done) {
          setMessage(done);
        }
      } catch (reason) {
        setMessage(String(reason instanceof Error ? reason.message : reason));
      } finally {
        setBusy(false);
        void refresh();
      }
    },
    [refresh],
  );

  const onTaskAction = (task: TaskPayload, action: TaskAction) =>
    run(() => (action === "start" ? api.startTask(task.name) : api.taskAction(task.name, action)));

  const status = ui?.status;
  const current = status?.current_task ?? null;
  const visibleTasks = tasks.filter((task) => task.visible);
  const capture = ui ? captureSummary(ui) : "…";
  const selectedInteraction = ui?.interaction_methods.find((item) => item.selected)?.label ?? t("无");

  return (
    <div
      className="page"
      data-core={error ? "error" : ui ? "ok" : "loading"}
      data-events={stats.connected ? "connected" : "disconnected"}
      data-tasks={visibleTasks.length}
    >
      <header>
        <div>
          <h1>{t("ok-bd2 链路验证")}</h1>
          <p className="muted">
            {t("核心")} {info?.core_version ?? "…"} · {t("接口")} {info?.api ?? "…"} · {t("外壳")}{" "}
            {shellInfo?.shellVersion ?? t("未知")}
          </p>
        </div>
        <div className="actions">
          <button type="button" onClick={() => void window.okbd2Shell?.openLogsFolder()}>
            {t("打开日志文件夹")}
          </button>
          <button type="button" onClick={() => void window.okbd2Shell?.quit()}>
            {t("退出")}
          </button>
        </div>
      </header>

      {error && <div className="banner error">{t("无法连接核心：")}{error}</div>}
      {info && info.legacy_instances.length > 0 && (
        <div className="banner warning">
          {t("检测到正在运行的旧版 ok-bd2，退出它之前不会开始任务：")}
          {info.legacy_instances.map((item) => item.description).join("；")}
        </div>
      )}
      {message && <div className="banner info">{message}</div>}

      <section className="grid">
        <div className="card">
          <h2>{t("连接")}</h2>
          <dl>
            <dt>{t("核心接口")}</dt>
            <dd>{error ? t("断开") : ui ? t("正常") : t("连接中")}</dd>
            <dt>{t("事件流")}</dt>
            <dd>
              {stats.connected ? t("已连接") : t("未连接")} · {t("已收")} {stats.received} · {t("序号")}{" "}
              {stats.lastSeq} · {t("缺口")} {stats.gaps} · {t("重新同步")} {stats.resyncs}
            </dd>
            <dt>{t("执行器")}</dt>
            <dd>{status ? executorLabel(status) : "…"}</dd>
          </dl>
          <div className="actions">
            <button type="button" disabled={busy || !status || status.paused} onClick={() => void run(api.pause)}>
              {t("暂停执行器")}
            </button>
            <button type="button" disabled={busy || !status?.paused} onClick={() => void run(api.resume)}>
              {t("继续执行器")}
            </button>
            <button type="button" disabled={busy || !current} onClick={() => void run(api.stopTask)}>
              {t("停止当前任务")}
            </button>
          </div>
        </div>

        <div className="card">
          <h2>{t("游戏窗口与截图方式")}</h2>
          <dl>
            <dt>{t("窗口")}</dt>
            <dd>
              {ui && ui.devices.length > 0
                ? ui.devices.map((device) => (
                    <span key={device.id} className={device.connected ? "ok" : "muted"}>
                      {device.label}
                    </span>
                  ))
                : t("未找到游戏窗口")}
            </dd>
            <dt>{t("截图方式")}</dt>
            <dd>{capture}</dd>
            <dt>{t("交互方式")}</dt>
            <dd>{selectedInteraction}</dd>
          </dl>
          <div className="actions">
            <button type="button" disabled={busy} onClick={() => void run(api.refreshDevices, t("已请求刷新"))}>
              {t("刷新窗口")}
            </button>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>{t("任务")}</h2>
        <table>
          <thead>
            <tr>
              <th>{t("名称")}</th>
              <th>{t("类型")}</th>
              <th>{t("状态")}</th>
              <th>{t("操作")}</th>
            </tr>
          </thead>
          <tbody>
            {visibleTasks.map((task) => (
              <tr key={task.name}>
                <td title={task.description}>{task.name}</td>
                <td>{task.trigger ? t("触发") : t("一次性")}</td>
                <td>{taskState(task, current)}</td>
                <td className="actions">
                  {taskActions(task, current).map((action) => (
                    <button key={action} type="button" disabled={busy} onClick={() => void onTaskAction(task, action)}>
                      {t(ACTION_LABELS[action])}
                    </button>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>{t("实时日志")}</h2>
        <pre className="log" aria-live="polite">
          {logs.map((line) => (
            <div key={line.key} className={`level-${levelName(line.level).toLowerCase()}`}>
              {line.text}
            </div>
          ))}
        </pre>
      </section>
    </div>
  );
}
