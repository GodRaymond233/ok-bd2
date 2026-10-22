// The task library's groups: dailies, leveling, any other one-time group, then triggers.
import { t } from "./i18n.ts";
import type { TaskPayload } from "./types.ts";

const GROUP_ORDER = ["日常/周常", "自动刷级"];

export interface Group {
  name: string;
  tasks: TaskPayload[];
}

/** Visible tasks matching the search, grouped in library order. */
export function libraryGroups(tasks: TaskPayload[], query: string): Group[] {
  const needle = query.trim().toLowerCase();
  const visible = tasks.filter(
    (task) =>
      task.visible &&
      (!needle || task.name.toLowerCase().includes(needle) || task.description.toLowerCase().includes(needle)),
  );
  const groups = new Map<string, TaskPayload[]>();
  for (const task of visible) {
    const name = task.trigger ? t("触发任务") : (task.group_name ?? t("其他"));
    groups.set(name, [...(groups.get(name) ?? []), task]);
  }
  const rank = (name: string) => {
    const index = GROUP_ORDER.indexOf(name);
    return index >= 0 ? index : name === t("触发任务") ? 99 : 50;
  };
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([name, grouped]) => ({ name: name === "日常/周常" ? "日常 / 周常" : name, tasks: grouped }));
}
