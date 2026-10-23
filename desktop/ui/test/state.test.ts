import assert from "node:assert/strict";
import { test } from "node:test";
import { captureSummary, noticeFromEvent } from "../src/state.ts";

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

test("notifications and a failed game start become notices", () => {
  assert.deepEqual(noticeFromEvent({ event: "starting_emulator", args: [true, "游戏路径不存在，请手动打开游戏", 0] }), {
    text: "游戏路径不存在，请手动打开游戏",
    error: true,
  });
  assert.equal(noticeFromEvent({ event: "starting_emulator", args: [false, null, 60] }), null);
  assert.deepEqual(
    noticeFromEvent({ event: "notification", args: ["已完成 {count} 项", "一键完成日常", false, true, null, { count: 3 }, null] }),
    { text: "一键完成日常：已完成 3 项", error: false },
  );
  assert.deepEqual(noticeFromEvent({ event: "notification", args: ["Game Exited", null, true] }), {
    text: "Game Exited",
    error: true,
  });
  assert.equal(noticeFromEvent({ event: "log", args: [20, "x"] }), null);
});
