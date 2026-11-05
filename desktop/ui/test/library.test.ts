import assert from "node:assert/strict";
import { test } from "node:test";
import { libraryGroups, taskGlyph } from "../src/library.ts";
import { task } from "./fixtures.ts";

const TASKS = [
  task({ name: "一键完成日常", group_name: "日常/周常" }),
  task({ name: "刷砍价等级", group_name: "自动刷级", description: "在第六章商人处开始。" }),
  task({ name: "快速狩猎", group_name: "日常/周常" }),
  task({ name: "自动登录游戏", trigger: true }),
  task({ name: "连接游戏", visible: false }),
  task({ name: "一键收菜测试", group_name: "测试" }),
];

test("groups in library order with hidden tasks left out", () => {
  assert.deepEqual(
    libraryGroups(TASKS, "").map((group) => [group.name, group.tasks.map((entry) => entry.name)]),
    [
      ["日常 / 周常", ["一键完成日常", "快速狩猎"]],
      ["自动刷级", ["刷砍价等级"]],
      ["测试", ["一键收菜测试"]],
      ["触发任务", ["自动登录游戏"]],
    ],
  );
});

test("search matches names and descriptions", () => {
  assert.deepEqual(
    libraryGroups(TASKS, "商人").map((group) => group.tasks.map((entry) => entry.name)),
    [["刷砍价等级"]],
  );
  assert.deepEqual(libraryGroups(TASKS, "不存在"), []);
});

test("a task's glyph comes from its name, its color family from its group", () => {
  assert.deepEqual(taskGlyph("每日跑商", "日常/周常", false), { icon: "coins", category: "daily" });
  assert.deepEqual(taskGlyph("刷砍价等级", "自动刷级", false), { icon: "tag", category: "level" });
  assert.deepEqual(taskGlyph("自动登录游戏", null, true), { icon: "login", category: "trigger" });
  assert.deepEqual(taskGlyph("BD2 基础检查", "测试", false), { icon: "tasks", category: "other" });
});
