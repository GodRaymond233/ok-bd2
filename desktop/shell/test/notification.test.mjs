import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { appIdentityCommands, notificationText } = require("../dist/notification.js");

test("notification: a task result's name becomes the title instead of the app name", () => {
  const message = "自动登录：主页按钮、亮度和抽抽乐 OCR 已连续确认，流程结束。";
  assert.deepEqual(notificationText("", message), {
    title: "自动登录", body: "主页按钮、亮度和抽抽乐 OCR 已连续确认，流程结束。",
  });
  assert.deepEqual(notificationText("ok-bd2", message).title, "自动登录");
});

test("notification: a given title and text that is not 'name：result' stay as they are", () => {
  assert.deepEqual(notificationText("BD2 游戏启动前体检", "自动登录：失败"), {
    title: "BD2 游戏启动前体检", body: "自动登录：失败",
  });
  for (const message of [
    "任务已完成",
    "：开头就是冒号",
    "只有名字：",
    "这一段说明比任务名长得多，已经是一句完整的话了，不应当被当作标题：后半句",
    "第一行\n第二行：后文",
  ]) {
    assert.deepEqual(notificationText("", message), { title: "ok-bd2", body: message });
  }
});

test("notification: the app ID gets a display name, and an icon only when the file exists", () => {
  const key = "HKCU\\Software\\Classes\\AppUserModelId\\io.example.app";
  assert.deepEqual(appIdentityCommands("io.example.app", "ok-bd2", "D:\\ok bd2\\icons\\icon.png"), [
    ["add", key, "/v", "DisplayName", "/t", "REG_SZ", "/d", "ok-bd2", "/f"],
    ["add", key, "/v", "IconUri", "/t", "REG_SZ", "/d", "D:\\ok bd2\\icons\\icon.png", "/f"],
  ]);
  assert.equal(appIdentityCommands("io.example.app", "ok-bd2", null).length, 1);
});
