import assert from "node:assert/strict";
import { test } from "node:test";
import {
  appendRecords,
  LOG_LIMIT,
  levelCounts,
  logRows,
  mergeLogs,
  parseLog,
  recordsFromEvent,
  recordText,
  type LogFilter,
  type LogRecord,
} from "../src/logs.ts";
import type { HistoryRun } from "../src/types.ts";

const local = (h: number, m: number, s: number) => new Date(2026, 9, 22, h, m, s).getTime() / 1000;

const FILE = [
  "2026-10-22 10:20:41,001 INFO MainThread DailyBatchTask:一键完成日常：开始，仅执行今日未完成的项目。",
  "2026-10-22 10:26:40,500 WARNING TaskThread MapTradeTask:每日跑商：未找到商人，重试 1 / 2。",
  "2026-10-22 10:27:02,000 ERROR TaskThread MapTradeTask:每日跑商：未找到商人，已返回主页。",
  "Traceback (most recent call last):",
  '  File "src\\tasks\\MapTradeTask.py", line 412, in run',
  "TaskFailed: 未找到商人",
  "",
  "2026-10-22 10:33:41,000 INFO MainThread ok:执行器空闲。",
].join("\n");

const ALL: LogFilter = { level: "all", query: "", run: null };

test("log file records keep their time, level, source and continuation lines", () => {
  const records = parseLog(FILE);
  assert.equal(records.length, 4);
  assert.deepEqual(
    records.map((record) => [record.time, record.level, record.source]),
    [
      ["10:20:41", 20, "DailyBatchTask"],
      ["10:26:40", 30, "MapTradeTask"],
      ["10:27:02", 40, "MapTradeTask"],
      ["10:33:41", 20, "ok"],
    ],
  );
  assert.equal(records[0].ts, local(10, 20, 41) + 0.001);
  assert.equal(records[2].text, "每日跑商：未找到商人，已返回主页。");
  assert.equal(records[2].more.length, 3);
  assert.equal(records[0].date, "2026-10-22");
  assert.deepEqual(levelCounts(records), { warning: 1, error: 1 });
  assert.match(recordText(records[2]), /^2026-10-22 10:27:02 MapTradeTask:每日跑商.*\nTraceback/);
});

test("a message without a logger prefix keeps all its text", () => {
  const [plain, url] = parseLog(
    "2026-10-22 08:00:00,000 INFO MainThread 普通的一行 文字\n2026-10-22 08:00:01,000 INFO MainThread https://example.com/x",
  );
  assert.deepEqual([plain.source, plain.text], ["", "普通的一行 文字"]);
  assert.deepEqual([url.source, url.text], ["", "https://example.com/x"]);
  const [nested] = parseLog("2026-10-22 08:00:02,000 INFO MainThread TaskExecutor:rec_model_dir: D:\\x");
  assert.deepEqual([nested.source, nested.text], ["TaskExecutor", "rec_model_dir: D:\\x"]);
});

test("live records follow the file without repeating it", () => {
  const file = parseLog(FILE);
  const live = [
    ...recordsFromEvent({ event: "log", args: [20, "2026-10-22 10:33:41,000 INFO MainThread ok:执行器空闲。"] }, 0),
    ...recordsFromEvent({ event: "log", args: [20, "2026-10-22 10:33:41,500 INFO MainThread ok:同一秒的新行"] }, 0),
    ...recordsFromEvent({ event: "log", args: [30, "2026-10-22 10:34:00,000 WARNING MainThread ok:新的警告"] }, 0),
  ];
  const merged = mergeLogs(file, live);
  assert.deepEqual(
    merged.slice(3).map((record) => record.text),
    ["执行器空闲。", "同一秒的新行", "新的警告"],
  );
  assert.equal(mergeLogs(file, live, 2).length, 2);
  assert.deepEqual(recordsFromEvent({ event: "task", args: [] }, 0), []);
  const bare = recordsFromEvent({ event: "log", args: [40, "没有时间的一行\n第二行"] }, local(9, 0, 0));
  assert.deepEqual([bare[0].time, bare[0].level, bare[0].text, bare[0].more], ["09:00:00", 40, "没有时间的一行", ["第二行"]]);
});

test("same-second records keep distinct timestamps, sources and repeated occurrences", () => {
  const lines = [
    "2026-10-22 10:33:41,100 INFO MainThread first:完成",
    "2026-10-22 10:33:41,101 INFO MainThread first:完成",
    "2026-10-22 10:33:41,100 INFO MainThread second:完成",
    "2026-10-22 10:33:41,100 ERROR MainThread first:完成",
    "2026-10-22 10:33:41,100 INFO MainThread first:完成",
  ];
  const merged = mergeLogs(parseLog(lines[0]), parseLog(lines.join("\n")));
  assert.equal(merged.length, 5);
  assert.deepEqual(merged.map((record) => record.source), ["first", "first", "second", "first", "first"]);
});

test("live logs retain a continuous bounded history while the page stays open", () => {
  const file = recordsFromEvent({ event: "log", args: [20, "initial"] }, local(10, 0, 0));
  let live = file;
  for (let index = 1; index <= LOG_LIMIT + 1; index++) {
    live = appendRecords(live, recordsFromEvent({ event: "log", args: [20, `line ${index}`] }, local(10, 0, 0) + index));
    if (index === 400) {
      assert.equal(mergeLogs(file, live).length, 401);
    }
  }
  assert.equal(live.length, LOG_LIMIT);
  assert.equal(mergeLogs(file, live).length, LOG_LIMIT);
  assert.equal(live[0].text, "line 2");
  assert.equal(live.at(-1)?.text, `line ${LOG_LIMIT + 1}`);
});

function kinds(rows: ReturnType<typeof logRows>): string[] {
  return rows.map((row) =>
    row.kind === "date" ? `date ${row.date}` : row.kind === "run" ? `${row.edge} ${row.run.task}` : row.record.time,
  );
}

const RUN: HistoryRun = {
  task: "一键完成日常",
  started: local(10, 20, 41),
  finished: local(10, 33, 40),
  duration: 779,
  ok: false,
  status: "存在问题",
};

test("rows put run dividers around the run's lines and a date divider first", () => {
  const records = parseLog(FILE);
  const older: HistoryRun = { ...RUN, task: "旧的运行", started: local(8, 0, 0), finished: local(8, 5, 0) };
  assert.deepEqual(kinds(logRows(records, [RUN, older], ALL)), [
    "date 2026-10-22",
    "start 一键完成日常",
    "10:20:41",
    "10:26:40",
    "10:27:02",
    "end 一键完成日常",
    "10:33:41",
  ]);
});

test("filters by level, text and one run", () => {
  const records: LogRecord[] = parseLog(FILE);
  assert.deepEqual(kinds(logRows(records, [], { ...ALL, level: "warning" })), ["date 2026-10-22", "10:26:40"]);
  assert.deepEqual(kinds(logRows(records, [], { ...ALL, level: "error" })), ["date 2026-10-22", "10:27:02"]);
  assert.deepEqual(kinds(logRows(records, [], { ...ALL, level: "info" })), ["date 2026-10-22", "10:20:41", "10:33:41"]);
  // The text filter looks into tracebacks and sources too.
  assert.deepEqual(kinds(logRows(records, [], { ...ALL, query: "taskfailed" })), ["date 2026-10-22", "10:27:02"]);
  assert.deepEqual(kinds(logRows(records, [], { ...ALL, query: "dailybatch" })), ["date 2026-10-22", "10:20:41"]);
  assert.deepEqual(kinds(logRows(records, [], { ...ALL, run: RUN })), [
    "date 2026-10-22",
    "start 一键完成日常",
    "10:20:41",
    "10:26:40",
    "10:27:02",
    "end 一键完成日常",
  ]);
  assert.deepEqual(kinds(logRows(records, [], { ...ALL, query: "没有这句" })), []);
});
