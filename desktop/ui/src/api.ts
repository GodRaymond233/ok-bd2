// Same-origin calls to the core. The launch cookie authenticates every request.
import type {
  AboutInfo,
  CaptureUi,
  DesktopInfo,
  ExecutorStatus,
  GamePaths,
  HistoryRun,
  ReportState,
  SettingsGroup,
  TaskPayload,
  TodaySummary,
  UpdateCheck,
  WindowInfo,
} from "./types.ts";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, method: "GET" | "POST" = "GET", body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? (JSON.parse(text) as unknown) : null;
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data ? String((data as { detail: unknown }).detail) : text;
    throw new ApiError(response.status, detail || `HTTP ${response.status}`);
  }
  return data as T;
}

export const api = {
  capture: () => request<CaptureUi>("/api/ui/capture"),
  status: () => request<ExecutorStatus>("/api/status"),
  info: () => request<DesktopInfo>("/api/desktop/info"),
  tasks: () => request<TaskPayload[]>("/api/tasks"),
  logs: () => request<{ text: string }>("/api/logs?level=ALL"),
  settings: () => request<SettingsGroup[]>("/api/settings"),
  setSetting: (group: string, key: string, value: unknown) =>
    request<SettingsGroup>(`/api/settings/${encodeURIComponent(group)}/config`, "POST", { key, value }),
  resetSettings: (group: string) => request<SettingsGroup>(`/api/settings/${encodeURIComponent(group)}/reset`, "POST", {}),
  selectDevice: (id: string) => request<CaptureUi>("/api/devices/select", "POST", { id }),
  /** "auto" goes back to trying the capture methods in order. */
  selectCapture: (id: string) => request<CaptureUi>("/api/capture-methods/select", "POST", { id }),
  tool: (action: "capture" | "export-logs" | "install-folder" | "log-folder" | "screenshot-folder") =>
    request<{ ok: boolean; message: string }>(`/api/tools/${action}`, "POST"),
  window: () => request<WindowInfo>("/api/bd2/window"),
  resizeWindow: (width: number, height: number) =>
    request<{ width: number; height: number; changed: boolean }>("/api/bd2/window/resize", "POST", { width, height }),
  game: () => request<GamePaths>("/api/bd2/game"),
  cancelStarter: () => request<{ cancelled: boolean }>("/api/bd2/starter/cancel", "POST"),
  testNotification: (channel: string) => request<{ sent: boolean }>("/api/bd2/notify/test", "POST", { channel }),
  about: () => request<AboutInfo>("/api/about"),
  updates: () => request<UpdateCheck>("/api/updates"),
  refreshDevices: () => request<{ accepted: boolean }>("/api/devices/refresh", "POST"),
  startTask: (name: string) => request<TaskPayload>(`/api/tasks/${encodeURIComponent(name)}/start`, "POST"),
  taskAction: (name: string, action: string) =>
    request<TaskPayload>(`/api/tasks/${encodeURIComponent(name)}/action`, "POST", { action }),
  pause: () => request<ExecutorStatus>("/api/executor/pause", "POST"),
  resume: () => request<ExecutorStatus>("/api/executor/resume", "POST"),
  stopTask: () => request<ExecutorStatus>("/api/executor/stop-task", "POST"),
  uiReady: () => request<{ accepted: boolean }>("/api/ui/ready", "POST"),
  setTaskConfig: (name: string, key: string, value: unknown) =>
    request<TaskPayload>(`/api/tasks/${encodeURIComponent(name)}/config`, "POST", { key, value }),
  resetTaskConfig: (name: string) =>
    request<TaskPayload>(`/api/tasks/${encodeURIComponent(name)}/config/reset`, "POST", {}),
  today: () => request<TodaySummary>("/api/bd2/today"),
  startDaily: (mode: "all" | "incomplete") => request<TaskPayload>("/api/bd2/daily/start", "POST", { mode }),
  history: (task: string, limit: number) =>
    request<HistoryRun[]>(`/api/bd2/history?task=${encodeURIComponent(task)}&limit=${limit}`),
  preview: async (signal: AbortSignal) => {
    const response = await fetch("/api/bd2/preview", { signal, credentials: "same-origin" });
    if (!response.ok) {
      const error = await response.json() as { detail?: string };
      throw new ApiError(response.status, error.detail ?? "无法读取游戏画面");
    }
    return response.blob();
  },
  reportState: () => request<ReportState>("/api/bd2/report"),
  prepareReport: () => request<ReportState>("/api/bd2/report/prepare", "POST", {}),
  buildReport: (session_id: string, description: string, include_screenshot: boolean) =>
    request<ReportState>("/api/bd2/report/build", "POST", { session_id, description, include_screenshot }),
  finishReport: (session_id: string, resume: boolean) =>
    request<ReportState>("/api/bd2/report/finish", "POST", { session_id, resume }),
  revealReport: (id: string) => request<{ ok: boolean }>(`/api/bd2/reports/${encodeURIComponent(id)}/reveal`, "POST", {}),
};
