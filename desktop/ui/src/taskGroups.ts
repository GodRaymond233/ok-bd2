import type { TaskPayload } from "./types.ts";

const UPSTREAM_GROUP = "上游扩展";
const SHARED_TRADE_TASK = "MapTradeTask";

export function taskGroups<T extends Pick<TaskPayload, "group_name" | "class_name">>(tasks: T[]) {
  const native = tasks.filter(task => task.group_name !== UPSTREAM_GROUP);
  const upstream = native.length === tasks.length ? [] : tasks.filter(task =>
    task.group_name === UPSTREAM_GROUP || task.class_name === SHARED_TRADE_TASK
  );
  return { native, upstream };
}
