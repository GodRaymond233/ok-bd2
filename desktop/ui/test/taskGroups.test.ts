import assert from "node:assert/strict";
import test from "node:test";
import { taskGroups } from "../src/taskGroups.ts";

test("debug extensions share the trade task and keep formal mail in the daily group", () => {
  const trade = { group_name: "日常/周常", class_name: "MapTradeTask" };
  const mail = { ...trade, class_name: "MailRewardTask" };
  const count = { group_name: "上游扩展", class_name: "TradeInventoryTask" };
  assert.deepEqual(taskGroups([trade, mail]), { native: [trade, mail], upstream: [] });
  assert.deepEqual(taskGroups([trade, mail, count]), { native: [trade, mail], upstream: [trade, count] });
});
