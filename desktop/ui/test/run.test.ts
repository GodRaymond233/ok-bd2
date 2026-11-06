import assert from "node:assert/strict";
import { test } from "node:test";
import { childRuns, infoRows, joinedHas, sealOf, segments, shutdownSeconds } from "../src/run.ts";
import { item, task } from "./fixtures.ts";

const ITEMS = [
  item("公会、小屋、酒馆", "公会、小屋、酒馆"),
  item("快速狩猎", "快速狩猎"),
  item("跑商", "每日跑商"),
  item("自动PVP", "镜中之战"),
];

test("joined names respect names that contain the separator", () => {
  assert.ok(joinedHas("公会、小屋、酒馆、快速狩猎", "公会、小屋、酒馆"));
  assert.ok(joinedHas("公会、小屋、酒馆、快速狩猎", "快速狩猎"));
  assert.ok(!joinedHas("快速狩猎、跑商", "狩猎"));
  assert.ok(!joinedHas("每日跑商", "跑商"));
  assert.ok(!joinedHas("-", "跑商"));
  assert.ok(!joinedHas(undefined, "跑商"));
});

test("what 每日跑商 sold is always shown and leaves room for two other values", () => {
  const trade = task({ info: { 状态: "完成", 甲: 1, 乙: 2, 丙: 3, 已出售商品: "苹果 x3\n梨 x1", 未出售商品: "无" } });
  const rows = infoRows(trade, "done", (key) => key, []).map((row) => row.key);
  assert.deepEqual(rows, ["状态", "已出售商品", "未出售商品", "甲", "乙"]);
});

test("the seal follows the task and the core's result state", () => {
  assert.equal(sealOf(task({ enabled: true }), "白嫖抽抽乐"), "run");
  assert.equal(sealOf(task({ enabled: true, paused: true }), "白嫖抽抽乐"), "pause");
  assert.equal(sealOf(task({ run_state: "abort" }), "白嫖抽抽乐"), "stopping");
  assert.equal(sealOf(task({ run_state: "abort" }), null), "abort");
  assert.equal(sealOf(task({ run_state: "fail" }), null), "issue");
  assert.equal(sealOf(task({ run_state: "warn" }), null), "issue");
  assert.equal(sealOf(task({ run_state: "done" }), null), "done");
});

test("batch progress while running and once finished", () => {
  const running = task({
    name: "一键完成日常",
    enabled: true,
    info: { 完成: "快速狩猎", 失败: "-", 跳过: "公会、小屋、酒馆", 当前子任务: "跑商" },
  });
  const runs = childRuns(running, ITEMS, "run");
  assert.deepEqual([...runs.values()], ["skip", "done", "doing", "todo"]);
  assert.deepEqual(
    segments(ITEMS, runs, "run").map((part) => [part.kind, part.names]),
    [
      ["done", ["快速狩猎"]],
      ["skip", ["公会、小屋、酒馆"]],
      ["doing", ["每日跑商"]],
      ["todo", ["镜中之战"]],
    ],
  );
  assert.deepEqual(segments(ITEMS, childRuns(running, ITEMS, "pause"), "pause")[2], { kind: "pause", names: ["每日跑商"] });

  const stopped = segments(ITEMS, childRuns(running, ITEMS, "abort"), "abort");
  assert.deepEqual(stopped.map((part) => [part.kind, part.names]), [
    ["done", ["快速狩猎"]],
    ["skip", ["公会、小屋、酒馆"]],
    ["incomplete", ["每日跑商", "镜中之战"]],
  ]);
  assert.equal(stopped.reduce((total, part) => total + part.names.length, 0), ITEMS.length);

  const finished = task({
    name: "一键完成日常",
    run_state: "fail",
    info: { 完成: "快速狩猎、自动PVP", 失败: "跑商", 跳过: "公会、小屋、酒馆", 当前子任务: "-" },
  });
  const done = childRuns(finished, ITEMS, "issue");
  assert.deepEqual([...done.values()], ["skip", "done", "fail", "done"]);
  assert.deepEqual(
    segments(ITEMS, done, "issue").map((part) => part.kind),
    ["done", "fail", "skip"],
  );
});

test("info rows keep the Qt panel's order and hide placeholders", () => {
  const running = task({
    enabled: true,
    info: { 状态: "运行中", 运行模式: "仅执行今日未完成", 当前子任务: "跑商", 失败: "-", Warning: "稍慢", 额外: "-", 次数: 3 },
  });
  assert.deepEqual(
    infoRows(running, "run", (key) => `名称:${key}`, []).map((row) => [row.key, row.value, row.tone]),
    [
      ["状态", "运行中", "strong"],
      ["当前子任务", "名称:跑商", "strong"],
      ["运行模式", "仅执行今日未完成", ""],
      ["次数", "3", ""],
    ],
  );
  const failed = task({ info: { 状态: "一键完成日常执行结束，1 项失败。", Warning: "未找到商人", Error: "超时" } });
  assert.deepEqual(
    infoRows(failed, "issue", (key) => key, ["每日跑商"]).map((row) => [row.key, row.tone]),
    [
      ["状态", "bad"],
      ["失败子任务", "bad"],
      ["警告", ""],
      ["错误", "bad"],
    ],
  );
});

test("the shutdown countdown announced by the batch", () => {
  assert.equal(shutdownSeconds(task({ info: { 状态: "今日日常已全部完成，60 秒后自动关机。" } })), 60);
  assert.equal(shutdownSeconds(task({ info: { 状态: "一键完成日常完成。" } })), null);
});
