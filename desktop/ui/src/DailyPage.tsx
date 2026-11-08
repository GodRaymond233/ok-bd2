// 一键完成日常 as a project of its own: the day's progress and the run controls on top, the fixed
// run order on the left (each child with its participation switch), and the selected child, or the
// run as a whole, on the right with ok-script 3's task detail.
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { Core } from "./App.tsx";
import { clockText, dateText, durationText, elapsedText, refreshText, whenText } from "./format.ts";
import { coreText, t } from "./i18n.ts";
import { isActive, runOutcome, SEAL_TEXT } from "./run.ts";
import { TaskDetail, useHistory } from "./TaskDetail.tsx";
import { dailyProgressRows, hero, type Row, type RowIcon } from "./today.ts";
import type { TodayItem, TodaySummary } from "./types.ts";
import { Icon, Menu, PageHead, TaskGlyph, Toggle } from "./ui.tsx";

/** The key the overview (the run as a whole) is selected by. */
export const OVERVIEW = "";

const STATE_TEXT: Record<RowIcon, string> = {
  done: "完成", fail: "失败", warn: "未成功", doing: "执行中", pause: "已暂停",
  queue: "待执行", todo: "未执行", skip: "跳过", off: "不参与", abort: "已中止",
};

function Badge({ row, index }: { row: Row; index: number }) {
  const icon = { done: "check", warn: "x", fail: "x", doing: "play", pause: "pause", skip: "skip", abort: "stop" }[row.icon as string];
  return <span className={`sn ${row.icon}`}>{icon ? <Icon name={icon} /> : index + 1}</span>;
}

/** How often a child comes round (Beijing time, from the core's refresh times). */
function cycleText(item: TodayItem, summary: TodaySummary) {
  if (item.weekly) {
    return t("每周 · 技能次数每日 {daily} 刷新，资源每周一 {weekly} 刷新", {
      daily: clockText(summary.refresh.collection_quota),
      weekly: clockText(summary.refresh.collection_week),
    });
  }
  const daily = t("每日 · {time} 刷新", { time: clockText(summary.refresh.daily) });
  return item.key === "自动PVP" ? `${daily} · ${t("周日 23:00 至周一 08:00 结算期间跳过")}` : daily;
}

export function DailyPage({
  core,
  selected,
  pinned,
  detailOpen,
  onSelect,
  onBack,
}: {
  core: Core;
  selected: string | null;
  /** Opened on a chosen item (from 任务), which a running batch must not take over. */
  pinned: boolean;
  detailOpen: boolean;
  onSelect: (key: string) => void;
  onBack: () => void;
}) {
  const [connecting, setConnecting] = useState(false);
  const summary = core.today;
  const batch = summary?.batch ? core.tasks.find((task) => task.name === summary.batch?.name) : undefined;
  const run = core.batchRun;
  const active = Boolean(run && isActive(run.seal));
  // While the run goes on, the detail follows its current child until you pick something else.
  const [following, setFollowing] = useState(!pinned);
  const runStart = run?.batch.start_time ?? 0;
  const lastRunStart = useRef(runStart);
  useEffect(() => {
    if (runStart !== lastRunStart.current) setFollowing(true);
    lastRunStart.current = runStart;
  }, [runStart]);

  const batchHistory = useHistory(core, summary?.batch?.name ?? "", 1);
  const launcher = core.tasks.find((task) => task.class_name === "LauncherTask");
  const connected = Boolean(core.device?.connected);
  const starting = Boolean(core.ui?.status.starting);
  const connect = async () => {
    if (!launcher) return;
    setConnecting(true);
    try { await core.act(() => core.startTask(launcher.name)); }
    finally { setConnecting(false); }
  };
  const head = (actions?: ReactNode) => (
    <PageHead title={t("一键完成日常")} aside={<span className="date">{dateText(core.now)}</span>}
      note={t("从主页出发，按下面的顺序执行参与的项目，再回到主页；单独运行的结果也计入今日进度。")}>
      {actions}
    </PageHead>
  );
  if (!summary || !summary.batch || !batch) {
    return (
      <div className="page daily-page">
        {head()}
        <div className="card empty">{summary ? t("没有找到一键完成日常任务。") : t("正在读取今日进度…")}</div>
      </div>
    );
  }

  const card = hero(summary, run);
  const rows = dailyProgressRows(summary, run, core.tasks, core.current, core.childStage, core.now);
  const current = rows.find((row) => row.current);
  const locked = core.busy || Boolean(batch.config_busy);
  const startReason = core.startBlocked;
  const setBatch = (key: string, value: boolean) => void core.act(() => core.setConfig(batch.name, key, value));
  const startDaily = (mode: "all" | "incomplete") => void core.act(() => core.startDaily(mode));

  const wanted = active && following && current ? current.item.key : selected ?? OVERVIEW;
  const chosenItem = summary.items.find((item) => item.key === wanted);
  const chosen = chosenItem ? chosenItem.key : OVERVIEW;
  const detailTask = chosenItem ? core.tasks.find((task) => task.name === chosenItem.name) : batch;
  const pick = (key: string) => {
    if (active) setFollowing(Boolean(current && key === current.item.key));
    onSelect(key);
  };

  let actions;
  if (run && active) {
    const stopping = run.seal === "stopping";
    actions = (
      <>
        <button type="button" className="btn" disabled={stopping}
          onClick={() => void core.act(() => core.taskAction(batch.name, run.seal === "pause" ? "resume" : "pause"))}>
          <Icon name={run.seal === "pause" ? "play" : "pause"} />
          {run.seal === "pause" ? t("继续") : t("暂停")}
        </button>
        <button type="button" className="btn stop" disabled={stopping}
          onClick={() => void core.act(() => core.taskAction(batch.name, "stop"))}>
          <Icon name="stop" />
          {stopping ? `${t("正在停止")}…` : t("停止")}
        </button>
      </>
    );
  } else {
    const rerun = { label: t("全部重新执行"), note: t("含今日已完成"), icon: "play", disabled: Boolean(startReason), onSelect: () => startDaily("all") };
    actions = card.allDone || card.total === 0 ? (
      <button type="button" className="btn" disabled={Boolean(startReason) || card.total === 0} title={startReason ?? undefined} onClick={() => startDaily("all")}>
        <Icon name="play" />
        {t("全部重新执行")}
      </button>
    ) : (
      <>
        <button type="button" className="btn primary" disabled={Boolean(startReason)} title={startReason ?? undefined} onClick={() => startDaily("incomplete")}>
          <Icon name="play" />
          {t("执行剩余 {count} 项", { count: card.remaining.length })}
        </button>
        <Menu items={[rerun]} />
      </>
    );
  }

  const taking = rows.filter((row) => row.item.enabled);
  const lastBatch = batchHistory?.[0];
  const overviewText = run && active
    ? `${t(SEAL_TEXT[run.seal])}${run.batch.start_time > 0 ? ` · ${t("已运行 {time}", { time: elapsedText(core.now - run.batch.start_time) })}` : ""}`
    : lastBatch
      ? `${t("上次 {when} {result}", { when: whenText(lastBatch.finished, core.now), result: t({ ok: "成功", stopped: "已中止", bad: "未成功" }[runOutcome(lastBatch)]) })}${lastBatch.duration ? ` · ${durationText(lastBatch.duration)}` : ""}`
      : t("还没有运行记录");
  const counts = {
    done: taking.filter((row) => row.icon === "done").length,
    bad: taking.filter((row) => row.tone === "bad").length,
  };

  return (
    <div className="page daily-page">
      {head()}
      <section className={`card dhero${card.allDone ? " finished" : ""}${active ? " live" : ""}`} aria-label={t("今日进度")}>
        <div className="dh-count">
          <b>{card.done}</b><span>/{card.total}</span>
          <small>{active ? t("本轮") : t("今日完成")}</small>
        </div>
        <div className="dh-main">
          <div className={card.allDone ? "dh-title ok" : "dh-title"}>
            {card.title[0]}<em>{card.title[1]}</em>{card.title[2]}
          </div>
          <div className="dh-sub">
            {current ? <><b>{coreText(current.item.name)}</b> · {current.text}</>
              : card.allDone ? t("下次刷新 {time}（北京时间）", { time: refreshText(summary.refresh.daily, core.now) })
              : card.detail}
          </div>
          <div className="dsegs" role="img" aria-label={t("成功 {done} 项，未成功 {bad} 项，共 {total} 项", { done: counts.done, bad: counts.bad, total: taking.length })}>
            {rows.map((row) => (
              <span key={row.item.key} className={`dseg ${row.item.enabled ? row.icon : "off"}`}
                title={`${coreText(row.item.name)} · ${t(STATE_TEXT[row.item.enabled ? row.icon : "off"])}`} />
            ))}
          </div>
        </div>
        <div className="dh-acts">
          {actions}
        </div>
        {!connected && !active && (
          <div className="dh-note">
            <Icon name="info" />
            <span>{t("未连接游戏：开始后会按「设备」页的设置启动或等待游戏。")}</span>
            <button type="button" className="btn sm" data-action="connect-game"
              disabled={!launcher || connecting || starting || Boolean(startReason)}
              title={startReason ?? undefined} onClick={() => void connect()}>
              {starting || connecting ? t("正在连接…") : t("启动／连接游戏")}
            </button>
            {starting && <button type="button" className="btn subtle sm"
              onClick={() => void core.act(core.cancelStarter)}>{t("取消等待")}</button>}
          </div>
        )}
        {!summary.batch.enabled && (
          <div className="dh-note warn">
            <Icon name="alert" />
            <span>{t("一键完成日常已停用，开始后不会执行任何项目。")}</span>
            <button type="button" className="btn sm" disabled={locked} onClick={() => setBatch("启用", true)}>{t("启用")}</button>
          </div>
        )}
        <div className="dh-foot">
          <label className="tgl">
            <Toggle checked={summary.batch.auto_start} disabled={locked} title={locked ? t("运行中不能修改") : undefined}
              label={t("启动后自动执行剩余日常")} onChange={(value) => setBatch("启动自动执行日常", value)} />
            {t("启动后自动执行剩余日常")}
          </label>
          <label className="tgl">
            <Toggle checked={summary.batch.shutdown} disabled={locked} title={locked ? t("运行中不能修改") : undefined}
              label={t("完成后关机")} onChange={(value) => setBatch("完成日常后自动关机", value)} />
            {t("完成后关机")}
          </label>
        </div>
      </section>

      <div className={`lib daily-lib${detailOpen ? " detail-open" : ""}`}>
        <nav className="libcol" aria-label={t("执行顺序")}>
          <section className="tsec card">
            <div className="tsec-head static">
              <Icon name="tasks" />
              <b>{t("执行顺序")}</b>
              <span className="count">{t("参与 {n}/{total}", { n: taking.length, total: rows.length })}</span>
            </div>
            <div className="tsec-rows">
              <div className={chosen === OVERVIEW ? "ti on overview" : "ti overview"}>
                <button type="button" className="ti-open" aria-current={chosen === OVERVIEW} onClick={() => pick(OVERVIEW)}>
                  <TaskGlyph name={batch.name} group={batch.group_name} />
                  <span className="txt">
                    <span className="nm">{t("整体运行")}</span>
                    <span className={`ds${active ? " live" : lastBatch && runOutcome(lastBatch) === "bad" ? " bad" : ""}`}>{overviewText}</span>
                  </span>
                </button>
              </div>
              <ol className="steps">
                {rows.map((row, index) => (
                  <li key={row.item.key} className={`ti step ${row.icon}${row.current ? " current" : ""}${row.item.enabled ? "" : " off"}${chosen === row.item.key ? " on" : ""}`}
                    aria-current={row.current ? "step" : undefined}>
                    <button type="button" className="ti-open" aria-current={chosen === row.item.key} onClick={() => pick(row.item.key)}>
                      <Badge row={row} index={index} />
                      <span className="txt">
                        <span className="nm">
                          {coreText(row.item.name)}
                          {row.item.weekly && <span className="wk">{t("每周")}</span>}
                        </span>
                        <span className={`ds ${row.tone}`}>{row.item.enabled ? row.text : `${t("不参与")}${row.icon === "done" ? ` · ${row.text}` : ""}`}</span>
                      </span>
                    </button>
                    <Toggle
                      checked={row.item.enabled}
                      disabled={locked}
                      title={locked ? t("运行中不能修改") : t("参与一键日常")}
                      label={`${coreText(row.item.name)} ${t("参与一键日常")}`}
                      onChange={(value) => setBatch(row.item.key, value)}
                    />
                  </li>
                ))}
              </ol>
            </div>
          </section>
        </nav>
        {detailTask && (
          <TaskDetail
            key={detailTask.name}
            core={core}
            task={detailTask}
            backLabel={t("执行顺序")}
            onBack={onBack}
            runLabel={t("单独运行")}
            hideActions={!chosenItem}
            noSettings={!chosenItem}
            context={chosenItem ? (
              <div className="dmember">
                <Icon name="history" />
                <span>{cycleText(chosenItem, summary)}</span>
                <label className="tgl">
                  <Toggle checked={chosenItem.enabled} disabled={locked} label={t("参与一键日常")}
                    onChange={(value) => setBatch(chosenItem.key, value)} />
                  {t("参与一键日常")}
                </label>
              </div>
            ) : (
              <div className="dmember">
                <Icon name="info" />
                <span>{t("一键完成日常每次从头到尾的运行：结果、当时的诊断信息和日志。各项目的设置在左侧选中后查看。")}</span>
                <label className="tgl">
                  <Toggle checked={summary.batch.enabled} disabled={locked} label={t("启用一键完成日常")}
                    onChange={(value) => setBatch("启用", value)} />
                  {t("启用")}
                </label>
              </div>
            )}
          />
        )}
      </div>
    </div>
  );
}
