// Same-origin calls to the core. The launch cookie authenticates every request.
import { coreText, type LanguagePreference, t } from "./i18n.ts";
import type {
  AboutInfo,
  BackupEntry,
  BackupList,
  CaptureUi,
  DesktopInfo,
  ExecutorStatus,
  ExportOptions,
  GamePaths,
  Guide,
  HistoryRun,
  HuntBook,
  HuntPlans,
  LanguageState,
  RecordingState,
  ReportState,
  ScriptDocument,
  ScriptEntry,
  ScriptHelper,
  SettingsGroup,
  TaskPayload,
  TemplateBox,
  TemplateImage,
  TemplateMarkup,
  TemplatesInfo,
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

/** What to show for a caught error. */
export function errorText(reason: unknown): string {
  return coreText(reason instanceof Error ? reason.message : String(reason));
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  // FastAPI reports a rejected request as a list of { msg, ... }.
  if (Array.isArray(detail)) {
    return detail.map((item: { msg?: unknown } | null) => item?.msg ?? JSON.stringify(item)).join("；");
  }
  return JSON.stringify(detail);
}

/** A failed response as an error: the core's `detail`, else the body as it came, else `fallback`. */
async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const text = await response.text();
  let data: unknown = null;
  try {
    data = JSON.parse(text);
  } catch {
    // An error from below the routes (a plain "Internal Server Error") is not JSON.
  }
  const message = data && typeof data === "object" && "detail" in data ? detailText(data.detail) : text;
  return new ApiError(response.status, message || fallback);
}

async function request<T>(path: string, method: "GET" | "POST" = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    signal,
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw await responseError(response, `HTTP ${response.status}`);
  }
  const text = await response.text();
  if (!text) {
    return null as T;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(response.status, t("后台服务返回的内容无法解析"));
  }
}

export const api = {
  language: () => request<LanguageState>("/api/desktop/language"),
  setLanguage: (language: LanguagePreference) => request<LanguageState>("/api/desktop/language", "POST", { language }),
  scripts: () => request<ScriptEntry[]>("/api/scripts"),
  scriptHelpers: () => request<ScriptHelper[]>("/api/script-templates"),
  script: (name: string) => request<ScriptDocument>(`/api/scripts/${encodeURIComponent(name)}`),
  createScript: (className: string, taskName: string, description: string) =>
    request<ScriptDocument>("/api/scripts", "POST", { class_name: className, task_name: taskName, description }),
  saveScript: (name: string, code: string) => request<ScriptDocument>(`/api/scripts/${encodeURIComponent(name)}`, "POST", { code }),
  copyScript: (name: string) => request<ScriptDocument>(`/api/scripts/${encodeURIComponent(name)}/copy`, "POST", {}),
  deleteScript: (name: string) => request<{ deleted: string }>(`/api/scripts/${encodeURIComponent(name)}/delete`, "POST", {}),
  runScript: (name: string, code: string) => request<ScriptDocument>(`/api/scripts/${encodeURIComponent(name)}/run`, "POST", { code }),
  exportOptions: () => request<ExportOptions>("/api/scripts-export/options"),
  exportScripts: (selected: string[], fileName: string, scriptName: string, version: string) =>
    request<{ path: string; name: string }>("/api/bd2/scripts/export", "POST", {
      selected,
      file_name: fileName,
      script_name: scriptName,
      version,
    }),
  importScripts: async (file: File) => {
    const response = await fetch(`/api/bd2/scripts/import?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    if (!response.ok) {
      throw await responseError(response, `HTTP ${response.status}`);
    }
    return (await response.json()) as { restart_required: boolean };
  },
  recording: () => request<RecordingState>("/api/bd2/scripts/recording"),
  startRecording: () => request<RecordingState>("/api/scripts-record/start", "POST", {}),
  stopRecording: (code: string, loop: string, count: number) =>
    request<RecordingState & { code: string }>("/api/scripts-record/stop", "POST", { code, loop, count }),
  templates: () => request<TemplateImage[]>("/api/templates"),
  templatesInfo: () => request<TemplatesInfo>("/api/bd2/templates/info"),
  captureTemplate: () => request<TemplateImage[]>("/api/templates/capture", "POST", {}),
  deleteTemplate: (name: string) => request<{ deleted: string }>(`/api/templates/${encodeURIComponent(name)}/delete`, "POST", {}),
  templateMarkup: (name: string) => request<TemplateMarkup>(`/api/templates/${encodeURIComponent(name)}/annotations`),
  saveTemplateMarkup: (name: string, annotations: TemplateBox[]) =>
    request<TemplateMarkup>(`/api/templates/${encodeURIComponent(name)}/annotations`, "POST", { annotations }),
  saveTemplates: (options: { format: string; destination: string; generate_label_enum: boolean; enum_path: string }) =>
    request<{ folder: string }>("/api/bd2/templates/save", "POST", options),
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
  guide: (id: string) => request<Guide>(`/api/bd2/guides/${encodeURIComponent(id)}`),
  openLauncher: () => request<{ opened: boolean }>("/api/desktop/launcher/open", "POST"),
  cancelStarter: () => request<{ cancelled: boolean }>("/api/bd2/starter/cancel", "POST"),
  testNotification: (channel: string) => request<{ sent: boolean }>("/api/bd2/notify/test", "POST", { channel }),
  about: () => request<AboutInfo>("/api/about"),
  updates: () => request<UpdateCheck>("/api/updates"),
  applyUpdate: (version: string) => request<{ accepted: boolean }>("/api/updates/apply", "POST", { version }),
  backups: () => request<BackupList>("/api/desktop/backups"),
  createBackup: () => request<BackupEntry>("/api/desktop/backups", "POST", {}),
  restoreBackup: (id: string) => request<{ accepted: boolean }>("/api/desktop/backups/restore", "POST", { id, confirmed: true }),
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
  huntPlans: (name: string) => request<HuntPlans>(`/api/bd2/hunt-plans/${encodeURIComponent(name)}`),
  previewHuntPlan: (name: string, book: HuntBook, rice: number, states: Record<string, boolean | null>, signal: AbortSignal) =>
    request<{ text: string; runnable: boolean }>(`/api/bd2/hunt-plans/${encodeURIComponent(name)}/preview`, "POST", { book, rice, states }, signal),
  saveHuntPlans: (name: string, book: HuntBook, revision: string) =>
    request<TaskPayload>(`/api/bd2/hunt-plans/${encodeURIComponent(name)}`, "POST", { book, revision }),
  startDaily: (mode: "all" | "incomplete") => request<TaskPayload>("/api/bd2/daily/start", "POST", { mode }),
  history: (task: string, limit: number) =>
    request<HistoryRun[]>(`/api/bd2/history?task=${encodeURIComponent(task)}&limit=${limit}`),
  preview: async (signal: AbortSignal) => {
    const response = await fetch("/api/bd2/preview", { signal, credentials: "same-origin" });
    if (!response.ok) {
      throw await responseError(response, "无法读取游戏画面");
    }
    return response.blob();
  },
  reportState: () => request<ReportState>("/api/bd2/report"),
  prepareReport: () => request<ReportState>("/api/bd2/report/prepare", "POST", {}),
  buildReport: (session_id: string, description: string) =>
    request<ReportState>("/api/bd2/report/build", "POST", { session_id, description }),
  finishReport: (session_id: string, resume: boolean) =>
    request<ReportState>("/api/bd2/report/finish", "POST", { session_id, resume }),
  revealReport: (id: string) => request<{ ok: boolean }>(`/api/bd2/reports/${encodeURIComponent(id)}/reveal`, "POST", {}),
};
