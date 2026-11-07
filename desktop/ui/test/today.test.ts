import assert from "node:assert/strict";
import { test } from "node:test";
import { childRuns } from "../src/run.ts";
import { type BatchRun, checklistRows, hero, skipText, withBatchConfig } from "../src/today.ts";
import { item, summary, task } from "./fixtures.ts";

const NOW = Date.UTC(2026, 9, 22, 2, 0) / 1000; // 10:00 Beijing
const AT = (hour: number, minute: number) => Date.UTC(2026, 9, 22, hour - 8, minute) / 1000;

function today() {
  return summary([
    item("公会、小屋、酒馆", "公会、小屋、酒馆", { done: true, completion: "today", last: { finished: AT(9, 12), duration: 125, ok: true, status: "完成。" } }),
    item("跑商", "每日跑商", { last: { finished: AT(9, 31), duration: 60, ok: false, status: "未找到商人" } }),
    item("跑图", "每周跑图", { enabled: false, done: true, weekly: true, completion: "quota" }),
    item("广场女神像", "广场女神像"),
    item("自动PVP", "镜中之战"),
  ]);
}

const names = (rows: ReturnType<typeof checklistRows>) => rows.map((row) => [row.icon, row.text, row.tone]);

test("saved batch settings drive participation while the progress snapshot is still old", () => {
  const snapshot = today();
  const saved = task({
    name: snapshot.batch!.name,
    config: ["公会、小屋、酒馆", "启动自动执行日常", "完成日常后自动关机"].map((key) => ({
      key, value: false, default: true, description: "", kind: "boolean",
      options: null, allow_duplication: false, minimum: null, maximum: null, sub_config: false,
    })),
  });
  snapshot.batch!.auto_start = snapshot.batch!.shutdown = true;
  const updated = withBatchConfig(snapshot, [saved])!;
  assert.equal(updated.items[0].enabled, false);
  assert.equal(updated.batch!.auto_start, false);
  assert.equal(updated.batch!.shutdown, false);
  assert.equal(checklistRows(updated, null, () => "", NOW)[0].icon, "off");
  assert.equal(hero(updated, null).total, hero(snapshot, null).total - 1);
  assert.deepEqual(updated.items[0].last, snapshot.items[0].last);
  assert.equal(snapshot.items[0].enabled, true);
  assert.deepEqual(updated.items.slice(1), snapshot.items.slice(1));
  saved.config![0].value = true;
  assert.equal(withBatchConfig(snapshot, [saved])!.items[0].enabled, true);
  assert.equal(withBatchConfig(snapshot, [task()]), snapshot);
});

test("before a run each row tells its last result", () => {
  assert.deepEqual(names(checklistRows(today(), null, () => "", NOW)), [
    ["done", "今天 09:12 完成 · 耗时 2m 05s", ""],
    ["warn", "今天 09:31 未成功 · 未找到商人", "bad"],
    ["off", "不参与 · 今日额度已用尽（08:00 刷新）", ""],
    ["todo", "今日未执行", ""],
    ["todo", "今日未执行", ""],
  ]);
});

test("during a run the checklist is the progress", () => {
  const data = today();
  const batch = task({
    name: "一键完成日常",
    enabled: true,
    start_time: AT(9, 50),
    info: { 运行模式: "仅执行今日未完成", 完成: "-", 失败: "-", 跳过: "公会、小屋、酒馆、跑商", 当前子任务: "广场女神像" },
  });
  data.skipped = { "公会、小屋、酒馆": { reason: "done", minutes: null }, 跑商: { reason: "not_due", minutes: 25 } };
  const enabled = data.items.filter((entry) => entry.enabled);
  const run: BatchRun = { batch, seal: "run", runs: childRuns(batch, enabled, "run") };
  const rows = checklistRows(data, run, (entry) => (entry.key === "广场女神像" ? "确认主页" : ""), NOW);
  assert.deepEqual(names(rows), [
    ["skip", "本轮跳过 · 今日已完成", ""],
    ["skip", "本轮跳过 · 调度未到期（约 25 分钟后）", ""],
    ["off", "不参与 · 今日额度已用尽（08:00 刷新）", ""],
    ["doing", "执行中 · 确认主页", "live"],
    ["queue", "待执行 · 第 2 个", ""],
  ]);
  assert.equal(rows[4].order, 2);
  assert.equal(rows[3].current, true);

  const card = hero(data, run);
  assert.deepEqual(card.title, ["正在执行剩余 · 第 ", "1", " / 2 项"]);
  assert.equal(card.detail, "广场女神像 → 镜中之战");
  // The ring counts the same run as the title, not what earlier runs finished today.
  assert.deepEqual([card.done, card.total], [0, 2]);

  const paused: BatchRun = { ...run, seal: "pause" };
  assert.deepEqual(names(checklistRows(data, paused, () => "确认主页", NOW))[3], ["pause", "已暂停 · 确认主页", "held"]);
  const stopping: BatchRun = { ...run, seal: "stopping" };
  assert.deepEqual(names(checklistRows(data, stopping, () => "", NOW))[3], ["pause", "正在停止…", "held"]);
});

test("after a run rows show this run's outcome with the child's own record", () => {
  const data = today();
  data.items[3].last = { finished: AT(9, 55), duration: 192, ok: true, status: "完成。" };
  data.items[4].last = { finished: AT(10, 2), duration: 30, ok: false, status: "镜中之战中止。" };
  const batch = task({
    name: "一键完成日常",
    start_time: AT(9, 50),
    run_state: "fail",
    info: { 完成: "广场女神像", 失败: "自动PVP", 跳过: "公会、小屋、酒馆、跑商", 当前子任务: "-" },
  });
  data.skipped = { 跑商: { reason: "stopped", minutes: null } };
  const run: BatchRun = { batch, seal: "issue", runs: childRuns(batch, data.items.filter((entry) => entry.enabled), "issue") };
  const rows = names(checklistRows(data, run, () => "", NOW));
  assert.deepEqual(rows[1], ["skip", "本轮跳过 · 前面的子任务失败，已停止", ""]);
  assert.deepEqual(rows[3], ["done", "本轮完成 · 3m 12s", ""]);
  assert.deepEqual(rows[4], ["fail", "本轮失败 · 镜中之战中止。", "bad"]);
  assert.equal(skipText({ reason: "settlement", minutes: null }), "本轮跳过 · 赛季结算中，北京时间周一 08:00 开放");
  assert.equal(skipText(undefined), "本轮跳过");
});

test("hero card before, without and after the dailies", () => {
  const data = today();
  const card = hero(data, null);
  assert.equal(card.done, 1);
  assert.equal(card.total, 4);
  assert.deepEqual(card.title, ["今日日常 · 还剩 ", "3", " 项"]);
  assert.equal(card.detail, "每日跑商、广场女神像、镜中之战");

  for (const entry of data.items) {
    entry.done = true;
  }
  assert.equal(hero(data, null).allDone, true);

  const none = summary(data.items.map((entry) => ({ ...entry, enabled: false })));
  assert.deepEqual(hero(none, null).title[1], "没有参与的子任务");
  assert.equal(hero(none, null).allDone, false);
});
