import assert from "node:assert/strict";
import { test } from "node:test";
import { clockText, dateText, durationText, elapsedText, refreshText, untilText, whenText } from "../src/format.ts";

// Thursday 2026-10-22 10:00 in Beijing.
const NOW = Date.UTC(2026, 9, 22, 2, 0) / 1000;
const beijing = (day: number, hour: number, minute = 0) => Date.UTC(2026, 9, day, hour - 8, minute) / 1000;

test("times read in Beijing whatever the machine's zone", () => {
  assert.equal(clockText(beijing(22, 9, 12)), "09:12");
  assert.equal(whenText(beijing(22, 0, 5), NOW), "今天 00:05");
  assert.equal(whenText(beijing(21, 23, 40), NOW), "昨天 23:40");
  assert.equal(whenText(beijing(5, 18, 2), NOW), "10月5日 18:02");
  assert.equal(dateText(NOW), "10月22日 周四");
});

test("upcoming refreshes and the countdown", () => {
  assert.equal(refreshText(beijing(22, 23), NOW), "今日 23:00");
  assert.equal(refreshText(beijing(23, 4), NOW), "明日 04:00");
  assert.equal(refreshText(beijing(26, 8), NOW), "周一 08:00");
  assert.equal(untilText(beijing(23, 4), NOW), "还有 18 小时 0 分");
  assert.equal(untilText(NOW + 59, NOW), "还有 1 分");
  assert.equal(untilText(NOW - 600, NOW), "还有 0 分");
});

test("durations match the Qt cards and the stopwatch pads", () => {
  assert.equal(durationText(null), "");
  assert.equal(durationText(0), "");
  assert.equal(durationText(45.9), "45s");
  assert.equal(durationText(250), "4m 10s");
  assert.equal(durationText(3720), "1h 2m");
  assert.equal(elapsedText(72), "01:12");
  assert.equal(elapsedText(3723), "1:02:03");
  assert.equal(elapsedText(-5), "00:00");
});
