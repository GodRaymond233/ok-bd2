// 任务: the task list (filtered by group, searchable, each row with its own run button or
// switch) and the selected task's detail with its settings.
import { useEffect, useState } from "react";
import type { Core } from "./App.tsx";
import { ConfigForm } from "./ConfigForm.tsx";
import { GuidePanel } from "./GuidePanel.tsx";
import { HuntPlanEditor } from "./HuntPlanEditor.tsx";
import { modifiedFields, valueText } from "./form.ts";
import { durationText, whenText } from "./format.ts";
import { coreText, t } from "./i18n.ts";
import { checklistRows } from "./today.ts";
import { libraryGroups } from "./library.ts";
import { statusSummary } from "./run.ts";
import type { HistoryRun, StatusRow, TaskPayload } from "./types.ts";
import { ConfirmDialog, Icon, Menu, TaskGlyph, Toggle, useSlider } from "./ui.tsx";

function Marker({ core, task }: { core: Core; task: TaskPayload }) {
  if (task.trigger) {
    return null;
  }
  if (task.waiting_for) {
    return <span className="chip info" title={t("等待「{task}」完成", { task: coreText(task.waiting_for) })}>{t("排队中")}</span>;
  }
  if (task.enabled || core.current === task.name) {
    return <span className="chip run">{t("运行中")}</span>;
  }
  if (task.name === core.today?.batch?.name) {
    return <span className="chip info">{t("合辑")}</span>;
  }
  const item = core.today?.items.find((candidate) => candidate.name === task.name);
  if (item?.done) {
    return (
      <span className="mk ok" title={t("已完成")}>
        <Icon name="check" />
      </span>
    );
  }
  if (item?.last && !item.last.ok) {
    return (
      <span className="mk bad" title={t("上次未成功")}>
        <Icon name="alert" />
      </span>
    );
  }
  return null;
}

function StatusValues({ rows }: { rows: StatusRow[] }) {
  return <div className="kv">{rows.map((row) => (
    <div key={row.key} style={{ display: "contents" }}>
      <span className="k">{coreText(row.label)}</span>
      <span className="v">{coreText(row.value)}</span>
    </div>
  ))}</div>;
}

function TaskRow({ core, task, selected, onSelect }: { core: Core; task: TaskPayload; selected: boolean; onSelect: () => void }) {
  return (
    <div className={selected ? "ti on" : "ti"}>
      <button type="button" className="ti-open" aria-current={selected} title={coreText(task.description || task.name)} onClick={onSelect}>
        <TaskGlyph name={task.name} group={task.group_name} trigger={task.trigger} />
        <span className="txt">
          <span className="nm">{coreText(task.name)}</span>
          {task.description && <span className="ds">{coreText(task.description)}</span>}
        </span>
        <Marker core={core} task={task} />
      </button>
      {task.trigger ? (
        <Toggle
          checked={task.enabled}
          label={coreText(task.name)}
          onChange={(value) => void core.act(() => core.taskAction(task.name, value ? "enable" : "disable"))}
        />
      ) : (
        <button
          type="button"
          className="btn sm icon"
          disabled={Boolean(core.startBlocked)}
          title={core.startBlocked ?? t("开始")}
          aria-label={`${t("开始")} ${coreText(task.name)}`}
          onClick={() => void core.act(() => core.startTask(task.name))}
        >
          <Icon name="play" />
        </button>
      )}
    </div>
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
  const [filter, setFilter] = useState<string | null>(null);
  const [guideOf, setGuideOf] = useState<string | null>(null);
  const tabs = useSlider<HTMLDivElement>('[aria-selected="true"]', filter);
  const everything = libraryGroups(core.tasks, "");
  const groups = libraryGroups(core.tasks, query).filter((group) => !filter || group.name === filter);
  const task = core.tasks.find((candidate) => candidate.name === selected) ?? groups[0]?.tasks[0];
  const total = everything.reduce((count, group) => count + group.tasks.length, 0);
  const tabList: [string | null, string, number][] = [
    [null, t("全部"), total],
    ...everything.map((group): [string, string, number] => [group.name, group.name, group.tasks.length]),
  ];
  return (
    <div className={`lib${detailOpen ? " detail-open" : ""}${guideOf === task?.name ? " guide-open" : ""}`}>
      <nav className="libcol" aria-label={t("任务库")}>
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
        <div className="tabs slides" ref={tabs} role="tablist" aria-label={t("任务分组")}>
          {tabList.map(([value, label, count]) => (
            <button key={label} type="button" role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}>
              {label}
              <span className="count">{count}</span>
            </button>
          ))}
        </div>
        {groups.map((group) => (
          <div key={group.name}>
            {!filter && <div className="grp">{group.name}</div>}
            {group.tasks.map((item) => (
              <TaskRow key={item.name} core={core} task={item} selected={item.name === task?.name} onSelect={() => onSelect(item.name)} />
            ))}
          </div>
        ))}
        {groups.length === 0 && <div className="libnote">{t("没有匹配的任务")}</div>}
      </nav>
      {task && (
        <TaskDetail
          key={task.name}
          core={core}
          task={task}
          onBack={onBack}
          readingGuide={guideOf === task.name}
          onGuide={(open) => setGuideOf(open ? task.name : null)}
        />
      )}
    </div>
  );
}

function TaskDetail({
  core,
  task,
  onBack,
  readingGuide,
  onGuide,
}: {
  core: Core;
  task: TaskPayload;
  onBack: () => void;
  readingGuide: boolean;
  onGuide: (open: boolean) => void;
}) {
  const [history, setHistory] = useState<HistoryRun[] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [editingPlan, setEditingPlan] = useState(false);
  const summary = core.today;
  const isBatch = task.name === summary?.batch?.name;
  const item = summary?.items.find((candidate) => candidate.name === task.name);
  const batch = summary?.batch ? core.tasks.find((candidate) => candidate.name === summary.batch?.name) : undefined;
  const locked = core.busy || Boolean(task.config_busy);
  const fields = task.config ?? [];
  const changed = modifiedFields(fields);
  const status = statusSummary(task.status_rows ?? []);

  const { history: loadHistory, historyVersion } = core;
  useEffect(() => {
    let cancelled = false;
    loadHistory(task.name, 3).then(
      (runs) => !cancelled && setHistory(runs),
      () => !cancelled && setHistory([]),
    );
    return () => {
      cancelled = true;
    };
  }, [loadHistory, task.name, historyVersion]);

  const row = item && summary ? checklistRows(summary, core.batchRun, core.childStage, core.now).find((candidate) => candidate.item === item) : undefined;
  const last = row ? row.text : history?.[0] ? t(history[0].ok ? "{when} 完成" : "{when} 未成功", { when: whenText(history[0].finished, core.now) }) : t("还没有运行记录");
  const lastBad = row ? row.tone === "bad" : history?.[0] ? !history[0].ok : false;
  const saveField = (key: string, value: unknown) => core.setConfig(task.name, key, value).then(() => undefined);

  return (
    <section className="detail" aria-label={coreText(task.name)}>
      <button type="button" className="crumb back-only" onClick={onBack}>
        <Icon name="chev-l" />
        {t("任务")}
      </button>
      <div className="crumb wide-only">
        {t("任务")} <Icon name="chev-r" /> {coreText(task.name)}
      </div>
      <div className="dhead">
        <TaskGlyph name={task.name} group={task.group_name} trigger={task.trigger} />
        <div className="tt">
          <h1>
            {coreText(task.name)}
            {isBatch && <span className="chip info">{t("合辑")}</span>}
            {task.trigger && <span className="chip ok">{t("触发任务")}</span>}
          </h1>
          {task.description && <div className="ds">{coreText(task.description)}</div>}
          <div className="metas">
            {item && batch && (
              <label className="tgl">
                <Toggle
                  checked={item.enabled}
                  disabled={locked}
                  label={t("参与一键日常")}
                  onChange={(value) => void core.act(() => core.setConfig(batch.name, item.key, value))}
                />
                {t("参与一键日常")}
              </label>
            )}
            {!task.trigger && <span className={lastBad ? "last bad" : "last"}>{`${t("上次运行")} · ${last}`}</span>}
            {task.waiting_for && <span className="last">{t("等待「{task}」完成后开始", { task: coreText(task.waiting_for) })}</span>}
          </div>
        </div>
        <div className="go">
          {task.trigger ? (
            <label className="tgl">
              <Toggle
                checked={task.enabled}
                label={t("启用")}
                onChange={(value) => void core.act(() => core.taskAction(task.name, value ? "enable" : "disable"))}
              />
              {t("启用")}
            </label>
          ) : (
            <>
              <div>
                <button
                  type="button"
                  className="btn primary"
                  disabled={Boolean(core.startBlocked)}
                  onClick={() => void core.act(() => core.startTask(task.name))}
                >
                  <Icon name="play" />
                  {t("开始")}
                </button>
                {core.startBlocked && <div className="why">{core.startBlocked}</div>}
              </div>
              <Menu
                items={[
                  {
                    label: `${t("全部恢复默认")}…`,
                    note: changed.length ? t("改过 {count} 项", { count: changed.length }) : t("都是默认值"),
                    icon: "refresh",
                    disabled: locked || changed.length === 0,
                    onSelect: () => setConfirming(true),
                  },
                ]}
              />
            </>
          )}
        </div>
      </div>
      {locked && (
        <div className="lockbar" role="status">
          <Icon name="lock" />
          <b>{core.ui?.diagnostic_pause ? t("问题报告期间，设置已锁定") : t("运行中，设置已锁定")}</b>
          <span>{core.current ? t("{task} 结束后可以修改。", { task: coreText(core.current) }) : t("任务结束后可以修改。")}</span>
        </div>
      )}
      <div className="dgrid">
        <div>
          {isBatch && summary ? (
            <div className="card form">
              {summary.items.map((child, index) => (
                <div key={child.key} className="fld">
                  <div className="lb">
                    <span className="order">{index + 1}</span>
                    {coreText(child.name)}
                  </div>
                  <div className="dsc">
                    {t("是否在一键完成日常中执行{name}。", { name: coreText(child.key) })}
                  </div>
                  <div className="ctrl">
                    <Toggle
                      checked={child.enabled}
                      disabled={locked}
                      label={coreText(child.name)}
                      onChange={(value) => void core.act(() => core.setConfig(task.name, child.key, value))}
                    />
                  </div>
                </div>
              ))}
              {fields
                .filter((field) => field.key === "启动自动执行日常" || field.key === "完成日常后自动关机")
                .map((field) => (
                  <div key={field.key} className="fld">
                    <div className="lb">{coreText(field.key)}</div>
                    <div className="dsc">{coreText(field.description)}</div>
                    <div className="ctrl">
                      <Toggle
                        checked={Boolean(field.value)}
                        disabled={locked}
                        label={coreText(field.key)}
                        onChange={(value) => void core.act(() => core.setConfig(task.name, field.key, value))}
                      />
                    </div>
                  </div>
                ))}
            </div>
          ) : (
            <ConfigForm fields={fields} disabled={locked} saveField={saveField}
              extra={(field) => field.editor === "hunt_plan" ? (
                <button className="btn sm" onClick={() => setEditingPlan(true)}>{t("编辑刷取方案")}</button>
              ) : null} />
          )}
        </div>
        <div className="dside">
          {!task.trigger && (
            <div className="card sc">
              <div className="hd">
                <Icon name="history" />
                <b>{t("最近运行")}</b>
              </div>
              <div className="hist">
                {history === null && <div className="r">{t("读取中…")}</div>}
                {history?.length === 0 && <div className="r">{t("还没有运行记录")}</div>}
                {history?.map((run) => (
                  <div key={`${run.finished}`} className="r">
                    <span className={run.ok ? "d" : "d bad"} />
                    <span className="w">{whenText(run.finished, core.now)}</span>
                    <span className={run.ok ? "s" : "s bad"} title={coreText(run.status)}>
                      {run.ok ? t("成功") : t("未成功")}
                      {run.duration ? ` · ${durationText(run.duration)}` : ""}
                    </span>
                    <button type="button" className="linkbtn" aria-label={t("查看这次的日志 {when}", { when: whenText(run.finished, core.now) })} onClick={() => core.openLogs(run)}>
                      {t("日志")}
                    </button>
                  </div>
                ))}
              </div>
              <button className="linkbtn more" onClick={() => core.navigate("records")}>{t("查看全部运行记录")}</button>
            </div>
          )}
          {(task.status_rows?.length ?? 0) > 0 && (
            <div className="card sc">
              <div className="hd">
                <Icon name="target" />
                <b>{t("运行状态")}</b>
              </div>
              <div className="status-summary"><StatusValues rows={status.summary} /></div>
              {status.details.length > 0 && <details className="status-details">
                <summary>{t("诊断详情（{count} 项）", { count: status.details.length })}</summary>
                <StatusValues rows={status.details} />
              </details>}
            </div>
          )}
          {(task.guide || task.instructions) && (
            <div className="card sc">
              <div className="hd">
                <Icon name="info" />
                <b>{t("使用说明")}</b>
              </div>
              <div className="l1">{task.guide ?? task.instructions}</div>
              {task.guide_id && (
                <button type="button" className="btn sm guide-open" aria-expanded={readingGuide} onClick={() => onGuide(true)}>
                  <Icon name="book" />
                  {t("查看图文说明")}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
      {editingPlan && <HuntPlanEditor core={core} task={task} onClose={() => setEditingPlan(false)} />}
      {readingGuide && task.guide_id && <GuidePanel id={task.guide_id} onClose={() => onGuide(false)} />}
      {confirming && (
        <ConfirmDialog
          title={t("把「{task}」的设置全部恢复默认？", { task: coreText(task.name) })}
          confirm={t("恢复默认")}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            void core.act(() => core.resetTask(task.name));
          }}
        >
          {(task.hidden_config_count ?? 0) > 0 && (
            <p className="why">{t("界面上没有显示的高级参数也会一起恢复默认。")}</p>
          )}
          <ul>
            {changed.map((field) => (
              <li key={field.key}>
                {coreText(field.key)}{t("：")}{valueText(field.value)} → {valueText(field.default)}
              </li>
            ))}
          </ul>
        </ConfirmDialog>
      )}
    </section>
  );
}
