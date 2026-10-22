// Payload builders shared by the client tests.
import type { TaskPayload, TodayItem, TodaySummary } from "../src/types.ts";

export function task(overrides: Partial<TaskPayload> = {}): TaskPayload {
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
    run_state: "done",
    ...overrides,
  };
}

export function item(key: string, name: string, overrides: Partial<TodayItem> = {}): TodayItem {
  return { key, name, enabled: true, done: false, completion: null, weekly: false, last: null, ...overrides };
}

export function summary(items: TodayItem[], overrides: Partial<TodaySummary> = {}): TodaySummary {
  return {
    batch: { name: "一键完成日常", enabled: true, auto_start: false, shutdown: false },
    items,
    skipped: {},
    refresh: { daily: 0, collection_quota: 0, collection_week: 0 },
    ...overrides,
  };
}
