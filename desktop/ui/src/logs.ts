// The logs page: records from the log file and the event stream, filters, and the rows with
// date and run dividers. Log lines carry this computer's local time, so the dividers use
// local time too (run records are epoch seconds).
import type { EventPayload, HistoryRun } from "./types.ts";

export interface LogRecord {
  key: number;
  /** Epoch seconds, from the line's local date and time. */
  ts: number;
  date: string;
  time: string;
  level: number;
  /** The logger's name (ok-script's Logger writes "name:message"). */
  source: string;
  text: string;
  /** Continuation lines, such as a traceback. */
  more: string[];
}

export type LevelFilter = "all" | "info" | "warning" | "error";

export interface LogFilter {
  level: LevelFilter;
  query: string;
  /** Only this run's lines (and its dividers). */
  run: HistoryRun | null;
}

export type LogRow =
  | { kind: "date"; key: string; date: string }
  | { kind: "run"; key: string; edge: "start" | "end"; run: HistoryRun; ts: number }
  | { kind: "line"; key: string; record: LogRecord };

export const LOG_LIMIT = 5000;

const LEVELS: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };

// ok-script's format: "%(asctime)s %(levelname)s %(threadName)s %(message)s", where messages
// from ok-script's Logger start with "name:" (ok/util/logger.py).
const LINE = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}),(\d+) (DEBUG|INFO|WARNING|ERROR|CRITICAL) \S+ ?(.*)$/;

const SOURCE = /^([\w.-]{1,60}):(?!\/\/)(.*)$/s;

let nextKey = 1;

function startRecord(match: RegExpExecArray): LogRecord {
  const [, y, mo, d, h, mi, s, fraction, level, rest] = match;
  const named = SOURCE.exec(rest);
  return {
    key: nextKey++,
    ts: new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)).getTime() / 1000 + Number(`0.${fraction}`),
    date: `${y}-${mo}-${d}`,
    time: `${h}:${mi}:${s}`,
    level: LEVELS[level],
    source: named ? named[1] : "",
    text: named ? named[2] : rest,
    more: [],
  };
}

/** Records from log text; lines that do not start a record continue the one before. */
export function parseLog(text: string): LogRecord[] {
  const records: LogRecord[] = [];
  for (const line of text.split(/\r?\n/)) {
    const match = LINE.exec(line);
    if (match) {
      records.push(startRecord(match));
    } else if (records.length > 0 && line.trim() !== "") {
      records[records.length - 1].more.push(line);
    }
  }
  return records;
}

/** Log-file records followed by the live ones the file did not have yet. */
export function mergeLogs(file: LogRecord[], live: LogRecord[], limit = LOG_LIMIT): LogRecord[] {
  const last = file.length ? file[file.length - 1].ts : -Infinity;
  const identity = (record: LogRecord) => JSON.stringify([record.level, record.source, record.text, record.more]);
  const tail = new Map<string, number>();
  for (const record of file.filter((record) => record.ts === last)) {
    const key = identity(record);
    tail.set(key, (tail.get(key) ?? 0) + 1);
  }
  const fresh = live.filter((record) => {
    if (record.ts !== last) {
      return record.ts > last;
    }
    const key = identity(record);
    const copies = tail.get(key) ?? 0;
    if (copies === 0) {
      return true;
    }
    tail.set(key, copies - 1);
    return false;
  });
  const merged = file.concat(fresh);
  return merged.length > limit ? merged.slice(merged.length - limit) : merged;
}

function levelMatches(level: number, filter: LevelFilter): boolean {
  if (filter === "info") {
    return level < 30;
  }
  if (filter === "warning") {
    return level >= 30 && level < 40;
  }
  if (filter === "error") {
    return level >= 40;
  }
  return true;
}

export function levelCounts(records: LogRecord[]): { warning: number; error: number } {
  return {
    warning: records.filter((record) => levelMatches(record.level, "warning")).length,
    error: records.filter((record) => levelMatches(record.level, "error")).length,
  };
}

export function runStart(run: HistoryRun): number {
  return run.started ?? run.finished - (run.duration ?? 0);
}

export function recordMatches(record: LogRecord, filter: LogFilter): boolean {
  if (!levelMatches(record.level, filter.level)) {
    return false;
  }
  if (filter.run && (record.ts < Math.floor(runStart(filter.run)) || record.ts > Math.ceil(filter.run.finished))) {
    return false;
  }
  const query = filter.query.trim().toLowerCase();
  if (!query) {
    return true;
  }
  return [record.text, record.source, ...record.more].some((part) => part.toLowerCase().includes(query));
}

export function localDate(ts: number): string {
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function localClock(ts: number): string {
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/**
 * The rows to show: matching records in order, a divider where the date changes, and a
 * start and end divider for each run that falls inside the loaded records.
 */
export function logRows(records: LogRecord[], runs: HistoryRun[], filter: LogFilter): LogRow[] {
  const shown = records.filter((record) => recordMatches(record, filter));
  const first = records.length ? records[0].ts : Infinity;
  const inside = (filter.run ? [filter.run] : runs).filter((run) => runStart(run) >= first - 1);
  // Lines with the run's own second sort after its start and before its end.
  const items: { ts: number; order: number; row: LogRow }[] = shown.map((record) => ({
    ts: record.ts,
    order: 1,
    row: { kind: "line", key: `l${record.key}`, record },
  }));
  for (const run of inside) {
    const start = Math.floor(runStart(run));
    const end = Math.ceil(run.finished);
    items.push({ ts: start, order: 0, row: { kind: "run", key: `s${run.task}${run.finished}`, edge: "start", run, ts: start } });
    items.push({ ts: end, order: 2, row: { kind: "run", key: `e${run.task}${run.finished}`, edge: "end", run, ts: end } });
  }
  items.sort((a, b) => a.ts - b.ts || a.order - b.order);
  const rows: LogRow[] = [];
  let date = "";
  for (const item of items) {
    const day = item.row.kind === "line" ? item.row.record.date : localDate(item.ts);
    if (day !== date) {
      date = day;
      rows.push({ kind: "date", key: `d${day}`, date: day });
    }
    rows.push(item.row);
  }
  return rows;
}

/** The whole record as the log file has it (without milliseconds), for copying. */
export function recordText(record: LogRecord): string {
  const head = `${record.date} ${record.time} ${record.source ? `${record.source}:` : ""}${record.text}`;
  return [head, ...record.more].join("\n");
}

/** The records in a "log" event (level, formatted text); a line without a timestamp gets now. */
export function recordsFromEvent(payload: EventPayload, now: number): LogRecord[] {
  if (payload.event !== "log" || !Array.isArray(payload.args) || payload.args.length < 2) {
    return [];
  }
  const [level, text] = payload.args;
  const records = parseLog(String(text));
  if (records.length > 0) {
    return records;
  }
  const [first, ...more] = String(text).split(/\r?\n/);
  return [
    {
      key: nextKey++,
      ts: Math.floor(now),
      date: localDate(now),
      time: localClock(now),
      level: Number(level) || 20,
      source: "",
      text: first,
      more,
    },
  ];
}

export function appendRecords(records: LogRecord[], incoming: LogRecord[], limit = LOG_LIMIT): LogRecord[] {
  const merged = records.concat(incoming);
  return merged.length > limit ? merged.slice(merged.length - limit) : merged;
}
