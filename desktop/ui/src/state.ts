// Pure state helpers for the link check page (tested with node --test).
import { t } from "./i18n.ts";
import type { EventPayload, TaskPayload } from "./types.ts";

export interface LogLine {
  key: number;
  level: number;
  text: string;
}

export interface StreamStats {
  connected: boolean;
  received: number;
  lastSeq: number;
  gaps: number;
  resyncs: number;
}

export const INITIAL_STATS: StreamStats = {
  connected: false,
  received: 0,
  lastSeq: 0,
  gaps: 0,
  resyncs: 0,
};

const LEVELS: [number, string][] = [
  [40, "ERROR"],
  [30, "WARNING"],
  [20, "INFO"],
  [10, "DEBUG"],
];

export function levelName(level: number): string {
  return LEVELS.find(([threshold]) => level >= threshold)?.[1] ?? "DEBUG";
}

let nextLogKey = 1;

export function logFromEvent(payload: EventPayload): LogLine | null {
  if (payload.event !== "log" || !Array.isArray(payload.args) || payload.args.length < 2) {
    return null;
  }
  const [level, text] = payload.args;
  return { key: nextLogKey++, level: Number(level) || 20, text: String(text) };
}

export function logsFromFile(text: string): LogLine[] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "")
    .map((line) => {
      const match = /\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s/.exec(line);
      const level = match ? ({ DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 } as const)[
        match[1] as "DEBUG"
      ] : 20;
      return { key: nextLogKey++, level, text: line };
    });
}

export function appendLogs(lines: LogLine[], incoming: LogLine[], limit = 300): LogLine[] {
  const merged = lines.concat(incoming);
  return merged.length > limit ? merged.slice(merged.length - limit) : merged;
}

export function trackEvent(stats: StreamStats, payload: EventPayload): StreamStats {
  const next = { ...stats, received: stats.received + 1 };
  if (typeof payload.seq === "number") {
    if (stats.lastSeq > 0 && payload.seq > stats.lastSeq + 1) {
      next.gaps += 1;
    }
    next.lastSeq = Math.max(stats.lastSeq, payload.seq);
  }
  if (payload.resync) {
    next.resyncs += 1;
  }
  return next;
}

export type TaskAction = "start" | "pause" | "resume" | "stop" | "enable" | "disable";

export function taskState(task: TaskPayload, current: string | null): string {
  if (task.trigger) {
    return task.enabled ? t("已启用") : t("已停用");
  }
  if (task.paused) {
    return t("已暂停");
  }
  if (task.running || current === task.name) {
    return t("运行中");
  }
  if (task.enabled) {
    return task.waiting_for ? `${t("排队中")}（${task.waiting_for}）` : t("排队中");
  }
  return t("空闲");
}

export function taskActions(task: TaskPayload, current: string | null): TaskAction[] {
  if (task.trigger) {
    return [task.enabled ? "disable" : "enable"];
  }
  if (task.paused) {
    return ["resume", "stop"];
  }
  if (task.running || current === task.name) {
    return ["pause", "stop"];
  }
  if (task.enabled) {
    return ["stop"];
  }
  return ["start"];
}

export const ACTION_LABELS: Record<TaskAction, string> = {
  start: "开始",
  pause: "暂停",
  resume: "继续",
  stop: "停止",
  enable: "启用",
  disable: "停用",
};

export function executorLabel(status: {
  paused: boolean;
  running: boolean;
  starting: boolean;
  current_task: string | null;
}): string {
  if (status.starting) {
    return t("正在启动游戏");
  }
  if (status.current_task) {
    return `${t("运行中")} · ${status.current_task}`;
  }
  if (status.paused) {
    return t("已暂停");
  }
  return status.running ? t("空闲") : t("未启动");
}
