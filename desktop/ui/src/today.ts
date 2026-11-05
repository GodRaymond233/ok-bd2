// The 今日 page: what each checklist row and the hero card say, before and during a run.
import { durationText, whenText } from "./format.ts";
import { coreText, t } from "./i18n.ts";
import { type ChildRun, type Seal, isActive } from "./run.ts";
import type { SkipReason, TaskPayload, TodayItem, TodaySummary } from "./types.ts";

export type RowIcon = "done" | "todo" | "warn" | "doing" | "pause" | "skip" | "fail" | "off" | "queue";

export interface Row {
  item: TodayItem;
  icon: RowIcon;
  /** Place in the remaining run order, for queued rows. */
  order?: number;
  text: string;
  tone: "" | "bad" | "live" | "held";
  current: boolean;
}

/** The batch run the rows follow: shown while it runs and until its result is dismissed. */
export interface BatchRun {
  batch: TaskPayload;
  seal: Seal;
  runs: Map<string, ChildRun>;
}

/** Participation follows the editable task config, including a just-saved response. */
export function withBatchConfig(summary: TodaySummary | null, tasks: TaskPayload[]): TodaySummary | null {
  const batch = tasks.find((task) => task.name === summary?.batch?.name);
  if (!summary?.batch || !batch?.config) {
    return summary;
  }
  const values = new Map(batch.config.map((field) => [field.key, field.value]));
  const enabled = (key: string, fallback: boolean) => {
    const value = values.get(key);
    return typeof value === "boolean" ? value : fallback;
  };
  return {
    ...summary,
    batch: {
      ...summary.batch,
      enabled: enabled("启用", summary.batch.enabled),
      auto_start: enabled("启动自动执行日常", summary.batch.auto_start),
      shutdown: enabled("完成日常后自动关机", summary.batch.shutdown),
    },
    items: summary.items.map((item) => ({ ...item, enabled: enabled(item.key, item.enabled) })),
  };
}

function completedText(item: TodayItem, now: number): string {
  if (item.completion === "week") {
    return t("本周已完成");
  }
  if (item.completion === "quota") {
    return t("今日额度已用尽（08:00 刷新）");
  }
  const last = item.last;
  if (!last) {
    return t("今日已完成");
  }
  const duration = durationText(last.duration);
  return `${t("{when} 完成", { when: whenText(last.finished, now) })}${duration ? ` · ${t("耗时 {time}", { time: duration })}` : ""}`;
}

function idleRow(item: TodayItem, now: number): Row {
  const base = { item, current: false };
  if (!item.enabled) {
    const state = item.done ? completedText(item, now) : "";
    return { ...base, icon: "off", text: state ? `${t("不参与")} · ${state}` : t("不参与"), tone: "" };
  }
  if (item.done) {
    return { ...base, icon: "done", text: completedText(item, now), tone: "" };
  }
  const last = item.last;
  if (last && !last.ok) {
    const reason = last.status ? ` · ${coreText(last.status)}` : "";
    return { ...base, icon: "warn", text: `${t("{when} 未成功", { when: whenText(last.finished, now) })}${reason}`, tone: "bad" };
  }
  return { ...base, icon: "todo", text: t("今日未执行"), tone: "" };
}

export function skipText(reason: SkipReason | undefined): string {
  switch (reason?.reason) {
    case "done":
      return `${t("本轮跳过")} · ${t("今日已完成")}`;
    case "not_due":
      return reason.minutes
        ? `${t("本轮跳过")} · ${t("调度未到期（约 {minutes} 分钟后）", { minutes: reason.minutes })}`
        : `${t("本轮跳过")} · ${t("调度未到期")}`;
    case "stopped":
      return `${t("本轮跳过")} · ${t("前面的子任务失败，已停止")}`;
    case "settlement":
      return `${t("本轮跳过")} · ${t("赛季结算中，北京时间周一 08:00 开放")}`;
    default:
      return t("本轮跳过");
  }
}

export function checklistRows(
  summary: TodaySummary,
  run: BatchRun | null,
  childStage: (item: TodayItem) => string,
  now: number,
): Row[] {
  if (!run) {
    return summary.items.map((item) => idleRow(item, now));
  }
  // Place in what is left of the run: the current child first, then the queue.
  let position = 0;
  return summary.items.map((item): Row => {
    if (!item.enabled) {
      return idleRow(item, now);
    }
    const base = { item, current: false };
    const last = item.last && item.last.finished >= run.batch.start_time ? item.last : null;
    switch (run.runs.get(item.key)) {
      case "done": {
        const duration = durationText(last?.duration);
        return { ...base, icon: "done", text: duration ? `${t("本轮完成")} · ${duration}` : t("本轮完成"), tone: "" };
      }
      case "fail": {
        const reason = last?.status ? ` · ${coreText(last.status)}` : "";
        return { ...base, icon: "fail", text: `${t("本轮失败")}${reason}`, tone: "bad" };
      }
      case "skip":
        return { ...base, icon: "skip", text: skipText(summary.skipped[item.key]), tone: "" };
      case "doing": {
        position += 1;
        const stage = childStage(item);
        if (run.seal === "stopping") {
          return { item, current: true, icon: "pause", text: `${t("正在停止")}…`, tone: "held" };
        }
        const label = run.seal === "pause" ? t("已暂停") : t("执行中");
        return {
          item,
          current: true,
          icon: run.seal === "pause" ? "pause" : "doing",
          text: stage ? `${label} · ${stage}` : label,
          tone: run.seal === "pause" ? "held" : "live",
        };
      }
      default:
        if (!isActive(run.seal)) {
          return { ...base, icon: "todo", text: t("本轮未执行"), tone: "" };
        }
        position += 1;
        return { ...base, icon: "queue", order: position, text: `${t("待执行")} · ${t("第 {position} 个", { position })}`, tone: "" };
    }
  });
}

export interface Hero {
  done: number;
  total: number;
  /** Rich title parts: text, then the emphasized number or phrase, then the rest. */
  title: [string, string, string];
  allDone: boolean;
  detail: string;
  remaining: TodayItem[];
}

export function hero(summary: TodaySummary, run: BatchRun | null): Hero {
  const taking = summary.items.filter((item) => item.enabled);
  const remaining = taking.filter((item) => !item.done);
  const done = taking.length - remaining.length;
  const base = { done, total: taking.length, remaining };
  if (run && isActive(run.seal)) {
    const order = taking.filter((item) => !["done", "fail", "skip"].includes(run.runs.get(item.key) ?? ""));
    const settled = taking.filter((item) => ["done", "fail"].includes(run.runs.get(item.key) ?? "")).length;
    const toRun = settled + order.length;
    const mode = String(run.batch.info["运行模式"] ?? "").includes("未完成") ? t("正在执行剩余") : t("正在执行");
    const prefix = run.seal === "pause" ? t("已暂停") : run.seal === "stopping" ? `${t("正在停止")}…` : mode;
    return {
      ...base,
      allDone: false,
      title: run.seal === "stopping" ? [prefix, "", ""] : [t("{prefix} · 第 ", { prefix }), String(Math.min(settled + 1, toRun)), t(" / {total} 项", { total: toRun })],
      detail: order.map((item) => coreText(item.name)).join(" → "),
    };
  }
  if (taking.length === 0) {
    return { ...base, allDone: false, title: [`${t("今日日常")} · `, t("没有参与的子任务"), ""], detail: t("在下方清单打开要执行的项目") };
  }
  if (remaining.length === 0) {
    return { ...base, allDone: true, title: [t("今日日常"), t("已全部完成"), ""], detail: "" };
  }
  return {
    ...base,
    allDone: false,
    title: [t("今日日常 · 还剩 "), String(remaining.length), t(" 项")],
    detail: remaining.map((item) => coreText(item.name)).join(t("、")),
  };
}
