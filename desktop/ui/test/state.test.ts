import assert from "node:assert/strict";
import { test } from "node:test";
import {
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
} from "../src/state.ts";
import type { TaskPayload } from "../src/types.ts";

function task(overrides: Partial<TaskPayload> = {}): TaskPayload {
  return {
    name: "白嫖抽抽乐",
    class_name: "FreeGachaTask",
    enabled: false,
    running: false,
    paused: false,
    trigger: false,
    description: "",
    visible: true,
    group_name: null,
    waiting_for: null,
    start_time: 0,
    info: {},
    ...overrides,
  };
}

test("one-time task states and the actions they allow", () => {
  assert.equal(taskState(task(), null), "空闲");
  assert.deepEqual(taskActions(task(), null), ["start"]);
  const queued = task({ enabled: true, waiting_for: "自动登录游戏" });
  assert.equal(taskState(queued, null), "排队中（自动登录游戏）");
  assert.deepEqual(taskActions(queued, null), ["stop"]);
  const running = task({ enabled: true, running: true });
  assert.equal(taskState(running, "白嫖抽抽乐"), "运行中");
  assert.deepEqual(taskActions(running, "白嫖抽抽乐"), ["pause", "stop"]);
  const paused = task({ enabled: true, paused: true });
  assert.equal(taskState(paused, null), "已暂停");
  assert.deepEqual(taskActions(paused, null), ["resume", "stop"]);
});

test("trigger tasks toggle between enabled and disabled", () => {
  const login = task({ name: "自动登录游戏", trigger: true, enabled: true });
  assert.equal(taskState(login, null), "已启用");
  assert.deepEqual(taskActions(login, null), ["disable"]);
  assert.deepEqual(taskActions({ ...login, enabled: false }, null), ["enable"]);
});

test("event stream statistics notice gaps and resyncs", () => {
  let stats = trackEvent(INITIAL_STATS, { event: "log", seq: 1 });
  stats = trackEvent(stats, { event: "log", seq: 2 });
  stats = trackEvent(stats, { event: "task", seq: 5 });
  stats = trackEvent(stats, { event: "task", resync: true });
  assert.equal(stats.received, 4);
  assert.equal(stats.lastSeq, 5);
  assert.equal(stats.gaps, 1);
  assert.equal(stats.resyncs, 1);
});

test("log lines from events and from the log file", () => {
  const line = logFromEvent({ event: "log", args: [30, "旧版 ok-bd2 正在运行"] });
  assert.equal(line?.level, 30);
  assert.equal(levelName(line?.level ?? 0), "WARNING");
  assert.equal(logFromEvent({ event: "task", args: [] }), null);
  const lines = logsFromFile(
    "2026-10-20 10:00:00,000 INFO MainThread a\n\n2026-10-20 10:00:01,000 ERROR MainThread b\n",
  );
  assert.deepEqual(
    lines.map((item) => item.level),
    [20, 40],
  );
  const capped = appendLogs(lines, lines, 3);
  assert.equal(capped.length, 3);
});

test("executor summary", () => {
  const base = { paused: false, running: true, starting: false, current_task: null };
  assert.equal(executorLabel(base), "空闲");
  assert.equal(executorLabel({ ...base, current_task: "一键完成日常" }), "运行中 · 一键完成日常");
  assert.equal(executorLabel({ ...base, paused: true }), "已暂停");
  assert.equal(executorLabel({ ...base, starting: true }), "正在启动游戏");
  assert.equal(executorLabel({ ...base, running: false }), "未启动");
});

test("capture summary shows the method in use and how it was chosen", () => {
  const capture_methods = ["WGC", "BitBlt_RenderFull", "ForegroundBitBlt"].map((id) => ({
    id,
    label: id,
    selected: false,
  }));
  const ui = { capture_methods, capture_preference: null, capture_in_use: "WGC" };
  assert.equal(captureSummary(ui), "WGC（自动选择）");
  assert.equal(captureSummary({ ...ui, capture_in_use: null }), "未使用（自动选择）");
  assert.equal(captureSummary({ ...ui, capture_preference: "WGC" }), "WGC");
  assert.equal(
    captureSummary({ ...ui, capture_preference: "BitBlt_RenderFull" }),
    "WGC（设置为 BitBlt_RenderFull）",
  );
  assert.equal(captureSummary({ ...ui, capture_preference: "WGC", capture_in_use: null }), "未使用（设置为 WGC）");
});
