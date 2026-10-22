// Pure helpers for log lines and the capture summary (tested with node --test).
import { t } from "./i18n.ts";
import type { CaptureUi, EventPayload } from "./types.ts";

export interface LogLine {
  key: number;
  level: number;
  text: string;
}

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

// "2026-10-23 00:09:52,753 INFO MainThread AutoLoginTask:info_set 阶段 等待登录页"
const FILE_LINE = /^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d+ [A-Z]+ \S+ \S+ ([\s\S]*)$/;

/** A log-file line as "time message", without the date, level, thread and logger. */
export function shortLogText(text: string): string {
  const match = FILE_LINE.exec(text);
  return match ? `${match[1]} ${match[2]}` : text;
}

export function appendLogs(lines: LogLine[], incoming: LogLine[], limit = 300): LogLine[] {
  const merged = lines.concat(incoming);
  return merged.length > limit ? merged.slice(merged.length - limit) : merged;
}

export function captureSummary(
  ui: Pick<CaptureUi, "capture_methods" | "capture_preference" | "capture_in_use">,
): string {
  const label = (id: string) => ui.capture_methods.find((item) => item.id === id)?.label ?? id;
  const inUse = ui.capture_in_use ? label(ui.capture_in_use) : t("未使用");
  if (!ui.capture_preference) {
    return `${inUse}（${t("自动选择")}）`;
  }
  if (ui.capture_preference === ui.capture_in_use) {
    return inUse;
  }
  return `${inUse}（${t("设置为")} ${label(ui.capture_preference)}）`;
}

export interface Notice {
  text: string;
  error: boolean;
}

/**
 * What to tell the user about an event: ok-script's notifications (message, title, error,
 * tray, tab, params) and a failed game start (starting_emulator: done, error, seconds).
 */
export function noticeFromEvent(payload: EventPayload): Notice | null {
  const args = Array.isArray(payload.args) ? payload.args : [];
  if (payload.event === "starting_emulator" && typeof args[1] === "string" && args[1]) {
    return { text: args[1], error: true };
  }
  if (payload.event === "notification" && typeof args[0] === "string" && args[0]) {
    const params = args[5] && typeof args[5] === "object" ? (args[5] as Record<string, unknown>) : {};
    const text = args[0].replace(/\{(\w+)\}/g, (whole, key: string) => (key in params ? String(params[key]) : whole));
    const title = typeof args[1] === "string" && args[1] ? `${args[1]}：` : "";
    return { text: `${title}${text}`, error: Boolean(args[2]) };
  }
  return null;
}
