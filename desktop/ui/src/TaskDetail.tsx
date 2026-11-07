// The selected task, as ok-script 3's task page shows it (ok/ui/qt/tasks/tasks_page.py TaskDetail):
// a header with the task's actions, then 设置 / 运行状态 / 运行记录 tabs. The 任务 and 一键完成日常
// pages both use it; on the daily page it also says where the task sits in the daily run.
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { Core } from "./App.tsx";
import { ConfigForm } from "./ConfigForm.tsx";
import { GuidePanel } from "./GuidePanel.tsx";
import { HuntPlanEditor } from "./HuntPlanEditor.tsx";
import { modifiedFields, valueText } from "./form.ts";
import { durationText, elapsedText, whenText } from "./format.ts";
import { coreText, t } from "./i18n.ts";
import { runOutcome, sealOf, statusSummary } from "./run.ts";
import { liveChild } from "./today.ts";
import type { HistoryRun, StatusRow, TaskPayload } from "./types.ts";
import { ConfirmDialog, Icon, TaskGlyph, Toggle, useSlider } from "./ui.tsx";

type Tab = "settings" | "status" | "history";

export function StatusValues({ rows }: { rows: StatusRow[] }) {
  return <div className="kv">{rows.map((row) => (
    <div key={row.key} style={{ display: "contents" }}>
      <span className="k">{coreText(row.label)}</span>
      <span className="v">{coreText(row.value)}</span>
    </div>
  ))}</div>;
}

/** Recent runs of one task, newest first; refetched whenever any task finishes. */
export function useHistory(core: Core, task: string, limit: number): HistoryRun[] | null {
  const [history, setHistory] = useState<HistoryRun[] | null>(null);
  const { history: load, historyVersion } = core;
  useEffect(() => {
    let cancelled = false;
    load(task, limit).then(
      (runs) => !cancelled && setHistory(runs),
      () => !cancelled && setHistory([]),
    );
    return () => {
      cancelled = true;
    };
  }, [load, task, limit, historyVersion]);
  return history;
}

export function HistoryList({ core, runs }: { core: Core; runs: HistoryRun[] | null }) {
  return (
    <div className="hist">
      {runs === null && <div className="r">{t("读取中…")}</div>}
      {runs?.length === 0 && <div className="r">{t("还没有运行记录")}</div>}
      {runs?.map((run) => {
        const outcome = runOutcome(run);
        return (
          <div key={`${run.finished}`} className="r">
            <span className={outcome === "ok" ? "d" : outcome === "stopped" ? "d off" : "d bad"} />
            <span className="w">{whenText(run.finished, core.now)}</span>
            <span className={outcome === "bad" ? "s bad" : "s"} title={coreText(run.status)}>
              {outcome === "ok" ? t("成功") : outcome === "stopped" ? t("已中止") : t("未成功")}
              {run.duration ? ` · ${durationText(run.duration)}` : ""}
            </span>
            {outcome === "bad" && run.status && <span className="why-line">{coreText(run.status)}</span>}
            <button type="button" className="linkbtn" aria-label={t("查看这次的日志 {when}", { when: whenText(run.finished, core.now) })} onClick={() => core.openLogs(run)}>
              {t("日志")}
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function TaskDetail({
  core,
  task,
  backLabel,
  onBack,
  context,
  runLabel,
  hideActions,
  noSettings,
}: {
  core: Core;
  task: TaskPayload;
  backLabel: string;
  onBack: () => void;
  runLabel?: string;
  /** The page has the task's controls elsewhere (the daily run's own header). */
  hideActions?: boolean;
  /** The page shows the settings itself (the daily run's switches). */
  noSettings?: boolean;
  /** A line under the header about the task's place elsewhere (the daily run). */
  context?: ReactNode;
}) {
  const [confirming, setConfirming] = useState(false);
  const [editingPlan, setEditingPlan] = useState(false);
  const [readingGuide, setReadingGuide] = useState(false);
  // A child of the daily run is not enabled itself; it runs while the batch reports it.
  const inBatch = !task.enabled && liveChild(core.today, core.batchRun) === task.name;
  const childRunning = inBatch || (!task.enabled && core.current === task.name);
  const seal = inBatch && core.batchRun ? core.batchRun.seal : sealOf(task, core.current);
  const item = core.today?.items.find((candidate) => candidate.name === task.name);
  const stage = inBatch && item ? core.childStage(item) : "";
  const running = !task.trigger && (task.enabled || childRunning);
  const statusRows = task.status_rows ?? [];
  const hasStatus = running || statusRows.length > 0;
  // The daily run as a whole opens on its records: its page header already shows the live state.
  const [tab, setTab] = useState<Tab>(noSettings ? "history" : running ? "status" : "settings");
  // The selected task just started: follow it on its status, as ok-script's page does.
  const wasRunning = useRef(running);
  useEffect(() => {
    if (running && !wasRunning.current && !noSettings) setTab("status");
    wasRunning.current = running;
  }, [running, noSettings]);
  const fallback: Tab = noSettings ? "history" : "settings";
  const shownTab: Tab = (tab === "status" && !hasStatus) || (task.trigger && tab === "history") || (noSettings && tab === "settings") ? fallback : tab;
  const tabs = useSlider<HTMLDivElement>('[aria-selected="true"]', shownTab);
  const history = useHistory(core, task.name, 8);
  const locked = core.busy || Boolean(task.config_busy);
  const fields = task.config ?? [];
  const changed = modifiedFields(fields);
  const status = statusSummary(statusRows);
  const saveField = (key: string, value: unknown) => core.setConfig(task.name, key, value).then(() => undefined);

  const last = history?.[0];
  const outcome = last ? runOutcome(last) : null;
  let line: ReactNode;
  if (task.trigger) {
    line = <span className={task.enabled ? "st live" : "st"}>{task.enabled ? t("已开启 · 游戏启动后自动生效") : t("已关闭")}</span>;
  } else if (task.waiting_for) {
    line = <span className="st info">{t("排队中")} · {t("等待「{task}」完成后开始", { task: coreText(task.waiting_for) })}</span>;
  } else if (running) {
    const label = seal === "pause" ? t("已暂停") : seal === "stopping" ? t("正在停止") : inBatch ? t("一键完成日常执行中") : t("运行中");
    const more = inBatch ? stage : task.start_time > 0 && !childRunning ? t("已运行 {time}", { time: elapsedText(core.now - task.start_time) }) : "";
    line = <span className={seal === "pause" ? "st held" : "st live"}>{label}{more ? ` · ${more}` : ""}</span>;
  } else if (last && outcome) {
    const text = { ok: "{when} 完成", stopped: "{when} 已中止", bad: "{when} 未成功" } as const;
    line = (
      <span className={outcome === "bad" ? "st bad" : "st"}>
        {t("上次运行")} · {t(text[outcome], { when: whenText(last.finished, core.now) })}
        {outcome === "bad" && last.status ? ` · ${coreText(last.status)}` : last.duration ? ` · ${t("耗时 {time}", { time: durationText(last.duration) })}` : ""}
      </span>
    );
  } else {
    line = <span className="st">{history === null ? "" : t("还没有运行记录")}</span>;
  }

  let actions: ReactNode;
  if (task.trigger) {
    actions = (
      <label className="tgl">
        <Toggle
          checked={task.enabled}
          label={t("启用")}
          onChange={(value) => void core.act(() => core.taskAction(task.name, value ? "enable" : "disable"))}
        />
        {t("启用")}
      </label>
    );
  } else if (task.enabled) {
    // A queued task has nothing to pause yet; it can only leave the queue.
    actions = (
      <>
        {!task.waiting_for && <button type="button" className="btn" disabled={seal === "stopping"}
          onClick={() => void core.act(() => core.taskAction(task.name, task.paused ? "resume" : "pause"))}>
          <Icon name={task.paused ? "play" : "pause"} />
          {task.paused ? t("继续") : t("暂停")}
        </button>}
        <button type="button" className="btn stop" disabled={seal === "stopping"}
          onClick={() => void core.act(() => core.taskAction(task.name, "stop"))}>
          <Icon name="stop" />
          {seal === "stopping" ? `${t("正在停止")}…` : t("停止")}
        </button>
      </>
    );
  } else {
    actions = (
      <div className="go-run">
        <button
          type="button"
          className="btn primary"
          disabled={Boolean(core.startBlocked)}
          title={core.startBlocked ?? undefined}
          onClick={() => void core.act(() => core.startTask(task.name))}
        >
          <Icon name="play" />
          {runLabel ?? t("运行任务")}
        </button>
        {core.startBlocked && <div className="why">{core.startBlocked}</div>}
      </div>
    );
  }

  const tabList: [Tab, string, string][] = [
    ...(noSettings ? [] : [["settings", t("设置"), "settings"] as [Tab, string, string]]),
    ...(hasStatus ? [["status", t("运行状态"), "target"] as [Tab, string, string]] : []),
    ...(task.trigger ? [] : [["history", t("运行记录"), "history"] as [Tab, string, string]]),
  ];

  return (
    <section className="detail" aria-label={coreText(task.name)}>
      <button type="button" className="crumb back-only" onClick={onBack}>
        <Icon name="chev-l" />
        {backLabel}
      </button>
      <div className="tcard card">
        <header className="dhead">
          <TaskGlyph name={task.name} group={task.group_name} trigger={task.trigger} />
          <div className="tt">
            <h2>
              {coreText(task.name)}
              {task.trigger && <span className="chip ok">{t("触发任务")}</span>}
            </h2>
            {task.description && <div className="ds">{coreText(task.description)}</div>}
            <div className="metas">{line}</div>
          </div>
          <div className="go">
            {task.guide_id && (
              <button type="button" className="btn guide-open" aria-expanded={readingGuide} onClick={() => setReadingGuide(true)}>
                <Icon name="book" />
                {t("使用说明")}
              </button>
            )}
            {!hideActions && actions}
          </div>
        </header>
        {context}
        {locked && (
          <div className="lockbar" role="status">
            <Icon name="lock" />
            <b>{core.ui?.diagnostic_pause ? t("问题报告期间，设置已锁定") : t("运行中，设置已锁定")}</b>
            <span>{core.current ? t("{task} 结束后可以修改。", { task: coreText(core.current) }) : t("任务结束后可以修改。")}</span>
          </div>
        )}
        <div className="dtabs">
          <div className="utabs slides" ref={tabs} role="tablist" aria-label={t("任务详情")}>
            {tabList.map(([key, label, icon]) => (
              <button key={key} type="button" role="tab" aria-selected={shownTab === key} onClick={() => setTab(key)}>
                <Icon name={icon} />
                {label}
                {key === "status" && running && <span className="d run" />}
              </button>
            ))}
          </div>
          {shownTab === "settings" && changed.length > 0 && (
            <button type="button" className="btn subtle sm" disabled={locked} onClick={() => setConfirming(true)}>
              <Icon name="refresh" />
              {t("恢复默认")}
            </button>
          )}
        </div>
        <div className="dbody" role="tabpanel">
          {shownTab === "settings" && (
            <ConfigForm fields={fields} disabled={locked} saveField={saveField}
              extra={(field) => field.editor === "hunt_plan" ? (
                <button className="btn sm" onClick={() => setEditingPlan(true)}>{t("编辑刷取方案")}</button>
              ) : null} />
          )}
          {shownTab === "status" && (
            statusRows.length > 0 ? (
              <>
                <div className="status-summary"><StatusValues rows={status.summary} /></div>
                {status.details.length > 0 && <details className="status-details">
                  <summary>{t("诊断详情（{count} 项）", { count: status.details.length })}</summary>
                  <StatusValues rows={status.details} />
                </details>}
              </>
            ) : (
              <div className="dempty">{t("任务开始后会在这里显示进度。")}</div>
            )
          )}
          {shownTab === "history" && (
            <>
              <HistoryList core={core} runs={history} />
              <button className="linkbtn more" onClick={() => core.navigate("records")}>{t("查看全部运行记录")}</button>
            </>
          )}
        </div>
      </div>
      {editingPlan && <HuntPlanEditor core={core} task={task} onClose={() => setEditingPlan(false)} />}
      {readingGuide && task.guide_id && <GuidePanel id={task.guide_id} onClose={() => setReadingGuide(false)} />}
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
