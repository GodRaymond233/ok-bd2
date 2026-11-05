// What the run panel shows for a task: its state seal, the batch progress and the info rows.
// Mirrors the Qt run panel (src/ui/run_panel.py); the result state itself comes from the core.
import { t } from "./i18n.ts";
import type { TaskPayload, TodayItem } from "./types.ts";

export type Seal = "run" | "pause" | "stopping" | "done" | "issue" | "abort";

export const SEAL_TEXT: Record<Seal, string> = {
  run: "运行中",
  pause: "已暂停",
  stopping: "正在停止",
  done: "已完成",
  issue: "存在问题",
  abort: "已中止",
};

export function sealOf(task: TaskPayload, currentTask: string | null): Seal {
  if (task.enabled) {
    return task.paused ? "pause" : "run";
  }
  if (currentTask === task.name) {
    // Stop disables the task at once; it stays current until its run returns.
    return "stopping";
  }
  switch (task.run_state) {
    case "warn":
    case "fail":
      return "issue";
    case "abort":
      return "abort";
    default:
      return "done";
  }
}

export const isActive = (seal: Seal) => seal === "run" || seal === "pause" || seal === "stopping";

/**
 * Whether a '、'-joined info list contains a name. Names may contain '、' themselves
 * (公会、小屋、酒馆), so this is the boundary-aware test of run_history.contains_joined_name.
 */
export function joinedHas(joined: unknown, name: string): boolean {
  if (typeof joined !== "string" || !name) {
    return false;
  }
  let start = joined.indexOf(name);
  while (start >= 0) {
    const end = start + name.length;
    if ((start === 0 || joined[start - 1] === "、") && (end === joined.length || joined[end] === "、")) {
      return true;
    }
    start = joined.indexOf(name, start + 1);
  }
  return false;
}

export type ChildRun = "done" | "fail" | "skip" | "doing" | "todo";

/** Where each participating child of 一键完成日常 stands in the batch's current or last run. */
export function childRuns(batch: TaskPayload, items: TodayItem[], seal: Seal): Map<string, ChildRun> {
  const info = batch.info;
  const current = String(info["当前子任务"] ?? "");
  const runs = new Map<string, ChildRun>();
  for (const item of items) {
    if (joinedHas(info["完成"], item.key)) {
      runs.set(item.key, "done");
    } else if (joinedHas(info["失败"], item.key)) {
      runs.set(item.key, "fail");
    } else if (joinedHas(info["跳过"], item.key)) {
      runs.set(item.key, "skip");
    } else if (isActive(seal) && current === item.key) {
      runs.set(item.key, "doing");
    } else {
      runs.set(item.key, "todo");
    }
  }
  return runs;
}

export interface Segment {
  kind: "done" | "fail" | "skip" | "doing" | "pause" | "todo";
  names: string[];
}

/** Progress segments in the panel's fixed order: done, failed, skipped, current, waiting. */
export function segments(items: TodayItem[], runs: Map<string, ChildRun>, seal: Seal): Segment[] {
  const pick = (kind: ChildRun) => items.filter((item) => runs.get(item.key) === kind).map((item) => item.name);
  const result: Segment[] = [
    { kind: "done", names: pick("done") },
    { kind: "fail", names: pick("fail") },
    { kind: "skip", names: pick("skip") },
    { kind: seal === "run" ? "doing" : "pause", names: pick("doing") },
    { kind: "todo", names: isActive(seal) ? pick("todo") : [] },
  ];
  return result.filter((segment) => segment.names.length > 0);
}

export interface InfoRow {
  key: string;
  value: string;
  tone: "" | "strong" | "bad";
}

const STANDARD_KEYS = new Set(["状态", "当前子任务", "完成", "失败", "跳过", "Log", "Warning", "Error", "启用"]);
/** The result of 每日跑商: always shown, and they do not count as task-specific values. */
const SALE_KEYS = ["已出售商品", "未出售商品"];

const shown = (value: unknown) => value !== undefined && value !== null && String(value) !== "" && String(value) !== "-";

/**
 * The info rows of the Qt run panel, in its order: status, current child, failed children,
 * warning (once finished), error, what was sold, then up to two task-specific values.
 */
export function infoRows(
  task: TaskPayload,
  seal: Seal,
  childName: (key: string) => string,
  failedNames: string[],
): InfoRow[] {
  const info = task.info;
  const rows: InfoRow[] = [];
  if (shown(info["状态"])) {
    const tone = seal === "issue" || seal === "abort" ? "bad" : "strong";
    rows.push({ key: t("状态"), value: String(info["状态"]), tone });
  }
  if (isActive(seal) && shown(info["当前子任务"])) {
    rows.push({ key: t("当前子任务"), value: childName(String(info["当前子任务"])), tone: "strong" });
  }
  if (failedNames.length > 0) {
    rows.push({ key: t("失败子任务"), value: failedNames.join("、"), tone: "bad" });
  }
  if (!isActive(seal) && shown(info.Warning)) {
    rows.push({ key: t("警告"), value: String(info.Warning), tone: "" });
  }
  if (shown(info.Error)) {
    rows.push({ key: t("错误"), value: String(info.Error), tone: "bad" });
  }
  for (const key of SALE_KEYS) {
    if (shown(info[key])) {
      rows.push({ key, value: String(info[key]), tone: "" });
    }
  }
  const extras = Object.keys(info).filter((key) => !STANDARD_KEYS.has(key) && !SALE_KEYS.includes(key) && shown(info[key]));
  for (const key of extras.slice(0, 2)) {
    rows.push({ key, value: String(info[key]), tone: "" });
  }
  return rows;
}

const SHUTDOWN_PATTERN = /(\d+)\s*秒后自动关机/;

/** Seconds of the shutdown countdown announced by 一键完成日常, or null. */
export function shutdownSeconds(task: TaskPayload): number | null {
  const match = SHUTDOWN_PATTERN.exec(String(task.info["状态"] ?? ""));
  return match ? Number(match[1]) : null;
}
