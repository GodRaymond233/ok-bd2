// Same-origin calls to the core. The launch cookie authenticates every request.
import type {
  CaptureUi,
  DesktopInfo,
  ExecutorStatus,
  HistoryRun,
  TaskPayload,
  TodaySummary,
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
  logs: () => request<{ text: string }>("/api/logs?level=INFO"),
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
};
