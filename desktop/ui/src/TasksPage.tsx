// 任务: ok-script 3's task page (ok/ui/qt/tasks/tasks_page.py), which ok-ww uses. Filter tabs by kind
// and group across the top; a list of section cards (按需任务, 触发任务), each row with its own run
// button or switch; the selected task's detail beside it. 一键完成日常 has its own page, so it is
// not listed here; its children are, since each also runs on its own.
import { type CSSProperties, type ReactNode, useLayoutEffect, useRef, useState } from "react";
import type { Core } from "./App.tsx";
import { coreText, t } from "./i18n.ts";
import { maximumTaskListWidth, rememberTaskListWidth, storedTaskListWidth, TASK_LIST_WIDTH } from "./settings.ts";
import { TaskDetail } from "./TaskDetail.tsx";
import { liveChild } from "./today.ts";
import type { TaskPayload } from "./types.ts";
import { Icon, PageHead, TaskGlyph, TaskListDivider, Toggle, useSlider } from "./ui.tsx";

const KIND_ALL = "all";
const KIND_ONETIME = "onetime";
const KIND_TRIGGER = "trigger";
const GROUP = "group:";
const GROUP_ORDER = ["日常/周常", "自动刷级"];

// The last filter, kept while the app runs (ok-script keeps it across starts).
let lastFilter = KIND_ALL;

function groupLabel(name: string) {
  return name === "日常/周常" ? t("日常 / 周常") : coreText(name);
}

const groupRank = (name: string | null) => {
  const index = GROUP_ORDER.indexOf(name ?? "");
  return index >= 0 ? index : name ? 50 : 60;
};

/** The tasks this page lists: visible, not the daily run (it has its own page). */
export function listedTasks(core: Core): { onetime: TaskPayload[]; triggers: TaskPayload[] } {
  const batch = core.today?.batch?.name;
  const visible = core.tasks.filter((task) => task.visible && task.name !== batch);
  const onetime = visible.filter((task) => !task.trigger).sort((a, b) => groupRank(a.group_name) - groupRank(b.group_name));
  return { onetime, triggers: visible.filter((task) => task.trigger) };
}

function matches(task: TaskPayload, key: string, needle: string) {
  if (needle && ![task.name, task.description, coreText(task.name), coreText(task.description)]
    .some((text) => text.toLowerCase().includes(needle))) {
    return false;
  }
  if (key === KIND_ONETIME) return !task.trigger;
  if (key === KIND_TRIGGER) return task.trigger;
  if (key.startsWith(GROUP)) return task.group_name === key.slice(GROUP.length);
  return true;
}

function Status({ core, task }: { core: Core; task: TaskPayload }) {
  if (task.trigger) return null;
  if (task.waiting_for) {
    return <span className="chip info" title={t("等待「{task}」完成", { task: coreText(task.waiting_for) })}>{t("排队中")}</span>;
  }
  if (task.enabled && task.paused) return <span className="chip warn">{t("已暂停")}</span>;
  if (task.enabled || core.current === task.name) return <span className="chip run">{t("运行中")}</span>;
  if (liveChild(core.today, core.batchRun) === task.name) {
    return core.batchRun?.seal === "pause" ? <span className="chip warn">{t("已暂停")}</span> : <span className="chip run">{t("日常中")}</span>;
  }
  const item = core.today?.items.find((candidate) => candidate.name === task.name);
  if (item?.done) {
    return <span className="mk ok" title={t("今日已完成")}><Icon name="check" /></span>;
  }
  if (item?.last && !item.last.ok && item.last.status !== "已中止") {
    return <span className="mk bad" title={t("上次未成功")}><Icon name="alert" /></span>;
  }
  return null;
}

function TaskRow({ core, task, selected, onSelect }: { core: Core; task: TaskPayload; selected: boolean; onSelect: () => void }) {
  const running = task.enabled && !task.paused;
  const paused = task.enabled && task.paused;
  return (
    <div className={selected ? "ti on" : "ti"}>
      <button type="button" className="ti-open" aria-current={selected} title={coreText(task.description || task.name)} onClick={onSelect}>
        <TaskGlyph name={task.name} group={task.group_name} trigger={task.trigger} />
        <span className="txt">
          <span className="nm">{coreText(task.name)}</span>
          {task.description && <span className="ds">{coreText(task.description)}</span>}
        </span>
        <Status core={core} task={task} />
      </button>
      {task.trigger ? (
        <Toggle
          checked={task.enabled}
          label={coreText(task.name)}
          onChange={(value) => void core.act(() => core.taskAction(task.name, value ? "enable" : "disable"))}
        />
      ) : running ? (
        <button type="button" className="btn sm icon" title={t("停止")} aria-label={`${t("停止")} ${coreText(task.name)}`}
          onClick={() => void core.act(() => core.taskAction(task.name, "stop"))}>
          <Icon name="stop" />
        </button>
      ) : (
        <button
          type="button"
          className="btn sm icon"
          disabled={Boolean(core.startBlocked) && !paused}
          title={paused ? t("继续") : core.startBlocked ?? t("开始")}
          aria-label={`${paused ? t("继续") : t("开始")} ${coreText(task.name)}`}
          onClick={() => void core.act(() => paused ? core.taskAction(task.name, "resume") : core.startTask(task.name))}
        >
          <Icon name="play" />
        </button>
      )}
    </div>
  );
}

function Section({ title, icon, rows, collapsible, short }: {
  title: string; icon: string; rows: ReactNode[]; collapsible: boolean;
  /** Keeps a short list in view below the other section (ok-script's trigger section). */
  short?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const expanded = open || !collapsible;
  return (
    <section className={`tsec card${expanded ? "" : " folded"}${short ? " short" : ""}`}>
      <button type="button" className="tsec-head" aria-expanded={expanded} disabled={!collapsible} onClick={() => setOpen(!open)}>
        <Icon name={icon} />
        <b>{title}</b>
        <span className="count">{rows.length}</span>
        {collapsible && <span className="chev"><Icon name={expanded ? "chev-u" : "chev-d"} /></span>}
      </button>
      {expanded && <div className="tsec-rows">{rows}</div>}
    </section>
  );
}

export function TasksPage({
  core,
  selected,
  detailOpen,
  onSelect,
  onBack,
}: {
  core: Core;
  selected: string | null;
  detailOpen: boolean;
  onSelect: (name: string) => void;
  onBack: () => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilterState] = useState(lastFilter);
  const setFilter = (key: string) => {
    lastFilter = key;
    setFilterState(key);
  };
  const [preferredWidth, setPreferredWidth] = useState(storedTaskListWidth);
  const [layout, setLayout] = useState({ content: window.innerWidth, viewport: window.innerWidth });
  const library = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = library.current;
    if (!node) return;
    const measure = () => setLayout({ content: node.clientWidth, viewport: window.innerWidth });
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    window.addEventListener("resize", measure);
    measure();
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, []);
  const defaultWidth = layout.viewport < 1200 ? TASK_LIST_WIDTH.compact : TASK_LIST_WIDTH.default;
  // The detail keeps its minimum beside the list and the gap between them.
  const maximum = maximumTaskListWidth(layout.content - 16);
  const width = Math.min(maximum, preferredWidth ?? defaultWidth);

  const { onetime, triggers } = listedTasks(core);
  const all = [...onetime, ...triggers];
  const groups = [...new Set(all.map((task) => task.group_name).filter((name): name is string => Boolean(name)))]
    .sort((a, b) => groupRank(a) - groupRank(b));
  const tabList: [string, string, number][] = [
    [KIND_ALL, t("全部"), all.length],
    ...(onetime.length && triggers.length ? [
      [KIND_ONETIME, t("按需"), onetime.length] as [string, string, number],
      [KIND_TRIGGER, t("触发"), triggers.length] as [string, string, number],
    ] : []),
    ...groups.map((name): [string, string, number] => [GROUP + name, groupLabel(name), all.filter((task) => task.group_name === name).length]),
  ];
  const key = tabList.some(([value]) => value === filter) ? filter : KIND_ALL;
  const tabs = useSlider<HTMLDivElement>('[aria-selected="true"]', key);
  const needle = query.trim().toLowerCase();
  const shownOnetime = onetime.filter((task) => matches(task, key, needle));
  const shownTriggers = triggers.filter((task) => matches(task, key, needle));
  const shown = [...shownOnetime, ...shownTriggers];
  const task = shown.find((candidate) => candidate.name === selected) ?? all.find((candidate) => candidate.name === selected) ?? shown[0];
  const row = (item: TaskPayload) => (
    <TaskRow key={`${item.group_name}:${item.name}`} core={core} task={item} selected={item === task} onSelect={() => onSelect(item.name)} />
  );
  // A group seen as one section takes the group's name, as ok-script titles it.
  const single = key.startsWith(GROUP) && (shownOnetime.length === 0 || shownTriggers.length === 0);
  const groupTitle = single ? groupLabel(key.slice(GROUP.length)) : null;

  return (
    <div className="page tasks-page">
      <PageHead title={t("任务")} aside={
        <div className="tabs slides" ref={tabs} role="tablist" aria-label={t("任务分组")}>
          {tabList.map(([value, label, count], index) => (
            <span key={value} className="tabwrap">
              {value.startsWith(GROUP) && !tabList[index - 1][0].startsWith(GROUP) && <span className="tabsep" />}
              <button type="button" role="tab" aria-selected={key === value} onClick={() => setFilter(value)}>
                {label}
                <span className="count">{count}</span>
              </button>
            </span>
          ))}
        </div>
      }>
        <label className="search">
          <Icon name="search" />
          <input
            type="search"
            value={query}
            placeholder={t("搜索任务名称或说明")}
            aria-label={t("搜索任务")}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </PageHead>
      <div ref={library} style={{ "--lib-col-w": `${width}px` } as CSSProperties}
        className={`lib${detailOpen ? " detail-open" : ""}`}>
        <nav id="task-library" className="libcol" aria-label={t("任务库")}>
          {shownOnetime.length > 0 && (
            <Section title={groupTitle ?? t("按需任务")} icon="bolt" collapsible={key === KIND_ALL && shownTriggers.length > 0}
              rows={shownOnetime.map(row)} />
          )}
          {shownTriggers.length > 0 && (
            <Section title={groupTitle ?? t("触发任务")} icon="history" collapsible={key === KIND_ALL && shownOnetime.length > 0}
              short={shownOnetime.length > 0}
              rows={shownTriggers.map(row)} />
          )}
          {shown.length === 0 && <div className="libnote">{t("没有匹配的任务")}</div>}
        </nav>
        <TaskListDivider width={width} maximum={maximum} defaultWidth={defaultWidth}
          onResize={setPreferredWidth} onCommit={rememberTaskListWidth} />
        {task && (
          <TaskDetail key={`${task.group_name}:${task.name}`} core={core} task={task} backLabel={t("任务")} onBack={onBack}
            context={<DailyMembership core={core} task={task} />} />
        )}
      </div>
    </div>
  );
}

/** Under a daily child's header: its place in 一键完成日常 and whether it takes part. */
function DailyMembership({ core, task }: { core: Core; task: TaskPayload }) {
  const summary = core.today;
  const batch = summary?.batch;
  const index = summary?.items.findIndex((item) => item.name === task.name) ?? -1;
  if (!summary || !batch || index < 0) return null;
  const item = summary.items[index];
  return (
    <div className="dmember">
      <Icon name="bolt" />
      <span>{t("一键完成日常 · 第 {n} 项", { n: index + 1 })}</span>
      <label className="tgl">
        <Toggle
          checked={item.enabled}
          disabled={core.busy}
          label={t("参与一键日常")}
          onChange={(value) => void core.act(() => core.setConfig(batch.name, item.key, value))}
        />
        {item.enabled ? t("参与") : t("不参与")}
      </label>
      <button type="button" className="linkbtn" onClick={() => core.openDaily(item.key)}>
        {t("在日常中查看")}
        <Icon name="chev-r" />
      </button>
    </div>
  );
}
