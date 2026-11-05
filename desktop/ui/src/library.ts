// The task library's groups (dailies, leveling, any other one-time group, then triggers) and
// the icon each task shows.
import { coreText, t } from "./i18n.ts";
import type { TaskPayload } from "./types.ts";

const GROUP_ORDER = ["日常/周常", "自动刷级"];

export interface Glyph {
  icon: string;
  category: "daily" | "level" | "trigger" | "other";
}

const TASK_ICONS: [string, string][] = [
  ["一键完成日常", "bolt"],
  ["公会", "home"],
  ["抽抽乐", "gift"],
  ["狩猎", "target"],
  ["女神像", "star"],
  ["跑图", "map"],
  ["跑商", "coins"],
  ["镜中之战", "swords"],
  ["砍价", "tag"],
  ["压制", "level"],
  ["登录", "login"],
];

/** The icon and color family of a task; a task nothing matches gets the plain task icon. */
export function taskGlyph(name: string, group: string | null, trigger: boolean): Glyph {
  const icon = TASK_ICONS.find(([word]) => name.includes(word))?.[1] ?? "tasks";
  const category = trigger ? "trigger" : group === "日常/周常" ? "daily" : group === "自动刷级" ? "level" : "other";
  return { icon, category };
}

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
      (!needle || [task.name, task.description, coreText(task.name), coreText(task.description)].some((text) => text.toLowerCase().includes(needle))),
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
    .map(([name, grouped]) => ({ name: name === "日常/周常" ? t("日常 / 周常") : coreText(name), tasks: grouped }));
}
