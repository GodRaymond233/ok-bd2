// 今日: is the game connected, what is left today, and where to start.
import { useState } from "react";
import type { Core } from "./App.tsx";
import { dateText, refreshText, untilText } from "./format.ts";
import { coreText, t } from "./i18n.ts";
import { type Row, checklistRows, hero } from "./today.ts";
import type { TaskPayload } from "./types.ts";
import { Icon, Menu, TaskGlyph, Toggle } from "./ui.tsx";

function Ring({ done, total }: { done: number; total: number }) {
  const circumference = 2 * Math.PI * 32;
  const finished = total > 0 && done === total;
  return (
    <div className={finished ? "ring done" : "ring"} role="img" aria-label={`${t("已完成")} ${done} / ${total}`}>
      <svg viewBox="0 0 72 72">
        <circle className="track" cx="36" cy="36" r="32" fill="none" strokeWidth="5" />
        <circle
          className="val"
          cx="36"
          cy="36"
          r="32"
          fill="none"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - (total ? done / total : 0))}
        />
      </svg>
      <div className="num">
        <span>
          {done}
          <small>/{total}</small>
        </span>
      </div>
    </div>
  );
}

function StateIcon({ row }: { row: Row }) {
  if (row.icon === "queue") {
    return <span className="si queue">{row.order}</span>;
  }
  const icon = { done: "check", warn: "alert", fail: "x", doing: "play", pause: "pause", skip: "skip", off: "minus", todo: "" }[
    row.icon
  ];
  return <span className={`si ${row.icon}`}>{icon && <Icon name={icon} />}</span>;
}

export function TodayPage({ core }: { core: Core }) {
  const [connecting, setConnecting] = useState(false);
  const summary = core.today;
  const batch = summary?.batch ? core.tasks.find((task) => task.name === summary.batch?.name) : undefined;
  const connected = Boolean(core.device?.connected);
  const launcher = core.tasks.find((task) => task.class_name === "LauncherTask");
  const starting = Boolean(core.ui?.status.starting);
  const ready = connected && core.ui?.status.running && !core.ui.status.paused;
  const connect = async () => {
    if (!launcher) return;
    setConnecting(true);
    try { await core.act(() => core.startTask(launcher.name)); }
    finally { setConnecting(false); }
  };
  const head = (
    <>
      <div className="ph">
        <div className="l">
          <h1 className="title">{t("今日")}</h1>
          <span className="date">{dateText(core.now)}</span>
        </div>
        <div className="row">
          <button type="button" className="btn" data-action="connect-game"
            disabled={!launcher || connecting || starting || ready || Boolean(core.startBlocked)}
            title={core.startBlocked ?? undefined} onClick={() => void connect()}>
            <Icon name={ready ? "check" : "play"} />
            {starting || connecting ? t("正在连接…") : ready ? t("已连接游戏") : t("启动／连接游戏")}
          </button>
          {starting && <button type="button" className="btn subtle" disabled={!core.ui?.starter_wait}
            onClick={() => void core.act(core.cancelStarter)}>{t("取消等待")}</button>}
        </div>
      </div>
      {core.startError && <div className="ferr" role="alert">{core.startError}</div>}
      {summary && (
        <div className="refresh">
          <span>
            {t("日常刷新")} <b>{refreshText(summary.refresh.daily, core.now)}</b> ·{" "}
            <span className="cd">{untilText(summary.refresh.daily, core.now)}</span>
          </span>
          <span>
            {t("跑商库存 / 跑图额度")} <b>{refreshText(summary.refresh.collection_quota, core.now)}</b>
          </span>
          <span>
            {t("跑图资源")} <b>{refreshText(summary.refresh.collection_week, core.now)}</b>
          </span>
          <span>{t("北京时间")}</span>
        </div>
      )}
    </>
  );
  if (!summary) {
    return (
      <div className="page">
        {head}
        <div className="card empty">{t("正在读取今日进度…")}</div>
      </div>
    );
  }
  if (!summary.batch || !batch) {
    return (
      <div className="page">
        {head}
        <div className="card empty">{t("没有找到一键完成日常任务。")}</div>
      </div>
    );
  }

  const run = core.batchRun;
  const card = hero(summary, run);
  const rows = checklistRows(summary, run, core.childStage, core.now);
  const locked = core.busy || Boolean(batch.config_busy);
  const lockedReason = locked ? t("运行中不能修改") : undefined;
  const startReason = core.startBlocked;
  const setBatch = (key: string, value: boolean) => void core.act(() => core.setConfig(batch.name, key, value));
  const startDaily = (mode: "all" | "incomplete") => void core.act(() => core.startDaily(mode));
  const menu = (
    <Menu
      items={[
        {
          label: t("全部重新执行"),
          note: t("含今日已完成"),
          icon: "play",
          disabled: Boolean(startReason),
          onSelect: () => startDaily("all"),
        },
        { label: `${t("一键完成日常设置")}…`, icon: "settings", onSelect: () => core.openTask(batch.name) },
      ]}
    />
  );
  let actions;
  if (run && (run.seal === "run" || run.seal === "pause" || run.seal === "stopping")) {
    actions = (
      <button type="button" className="btn primary" disabled>
        <Icon name="play" />
        {t("执行中")}…
      </button>
    );
  } else if (card.allDone || card.total === 0) {
    actions = menu;
  } else {
    actions = (
      <>
        <button type="button" className="btn primary" disabled={Boolean(startReason)} title={startReason ?? undefined} onClick={() => startDaily("incomplete")}>
          <Icon name="play" />
          {t("执行剩余 {count} 项", { count: card.remaining.length })}
        </button>
        {menu}
      </>
    );
  }
  const note = !connected ? (
    <div className="note">
      <Icon name="info" />
      {t("未连接游戏：开始后按「设备」页的设置启动或等待游戏。")}
    </div>
  ) : startReason && !core.busy ? (
    <div className="note">
      <Icon name="lock" />
      {startReason}
    </div>
  ) : null;

  const weekly = summary.items.find((item) => item.weekly);
  const login = core.tasks.find((task) => task.trigger && task.visible);
  const leveling = core.tasks.filter((task) => task.group_name === "自动刷级" && task.visible);

  return (
    <div className="page">
      {head}
      <div className="today">
        <div>
          <div className="card hero">
            <Ring done={card.done} total={card.total} />
            <div style={{ minWidth: 0 }}>
              <div className={card.allDone ? "t1 ok" : "t1"}>
                {card.title[0]}
                <em>{card.title[1]}</em>
                {card.title[2]}
              </div>
              {card.detail && <div className="t2">{card.detail}</div>}
              {card.allDone && (
                <div className="t2">
                  {t("下次刷新 {time}（北京时间）", { time: refreshText(summary.refresh.daily, core.now) })}
                </div>
              )}
            </div>
            <div className="acts">{actions}</div>
            {note}
            <div className="opts">
              <label className="tgl">
                <Toggle
                  checked={summary.batch.shutdown}
                  disabled={locked}
                  title={lockedReason}
                  label={t("完成后关机")}
                  onChange={(value) => setBatch("完成日常后自动关机", value)}
                />
                {t("完成后关机")}
              </label>
              <label className="tgl">
                <Toggle
                  checked={summary.batch.auto_start}
                  disabled={locked}
                  title={lockedReason}
                  label={t("启动后自动执行剩余日常")}
                  onChange={(value) => setBatch("启动自动执行日常", value)}
                />
                {t("启动后自动执行剩余日常")}
              </label>
            </div>
          </div>

          <section className="sec">
            <div className="sech">
              <h2>{t("今日清单")}</h2>
              <span>{t("一键日常按此顺序执行，开关决定是否参与")}</span>
            </div>
            <div className="card list">
              {rows.map((row) => (
                <div
                  key={row.item.key}
                  className={`li${row.current ? " doing" : ""}${row.item.enabled ? "" : " off"}`}
                  role="button"
                  tabIndex={0}
                  aria-label={`${coreText(row.item.name)}${t("，")}${row.text}`}
                  onClick={() => core.openTask(row.item.name)}
                  onKeyDown={(event) => {
                    if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
                      event.preventDefault();
                      core.openTask(row.item.name);
                    }
                  }}
                >
                  <TaskGlyph name={row.item.name} group="日常/周常" />
                  <span className="txt">
                    <span className="nm">{coreText(row.item.name)}</span>
                    <span className={`mt ${row.tone}`}>{row.text}</span>
                  </span>
                  <span className="acts">
                    <StateIcon row={row} />
                    <button
                      type="button"
                      className="btn sm icon"
                      disabled={Boolean(startReason)}
                      title={startReason ?? t("单独运行")}
                      aria-label={`${t("单独运行")} ${coreText(row.item.name)}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        void core.act(() => core.startTask(row.item.name));
                      }}
                    >
                      <Icon name="play" />
                    </button>
                    <Toggle
                      checked={row.item.enabled}
                      disabled={locked}
                      title={lockedReason ?? t("参与一键日常")}
                      label={`${coreText(row.item.name)} ${t("参与一键日常")}`}
                      onChange={(value) => setBatch(row.item.key, value)}
                    />
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="side">
          {weekly && (
            <div
              className="card sc link"
              role="button"
              tabIndex={0}
              onClick={() => core.openTask(weekly.name)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  core.openTask(weekly.name);
                }
              }}
            >
              <div className="hd">
                <TaskGlyph name={weekly.name} group="日常/周常" />
                <b>{coreText(weekly.name)}</b>
                <span className="chev">
                  <Icon name="chev-r" />
                </span>
              </div>
              <div className="l1">
                {weekly.completion === "week"
                  ? t("本周已完成")
                  : weekly.completion === "quota"
                    ? `${t("本周未完成")} · ${t("今日额度已用尽")}`
                    : t("本周未完成")}
              </div>
              <div className="l2">{t("技能次数 08:00、资源周一 08:00 刷新")}</div>
            </div>
          )}
          {login && <TriggerCard core={core} task={login} />}
          {leveling.length > 0 && (
            <div className="card sc">
              <div className="hd">
                <TaskGlyph name="压制" group="自动刷级" />
                <b>{t("自动刷级")}</b>
              </div>
              {leveling.map((task) => (
                <div key={task.name} className="runrow">
                  <span>{coreText(task.name)}</span>
                  <button
                    type="button"
                    className="btn sm icon"
                    disabled={Boolean(startReason)}
                    title={startReason ?? t("开始")}
                    aria-label={`${t("开始")} ${coreText(task.name)}`}
                    onClick={() => void core.act(() => core.startTask(task.name))}
                  >
                    <Icon name="play" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TriggerCard({ core, task }: { core: Core; task: TaskPayload }) {
  return (
    <div className="card sc">
      <div className="hd">
        <TaskGlyph name={task.name} trigger />
        <b>{coreText(task.name)}</b>
        <Toggle
          checked={task.enabled}
          label={coreText(task.name)}
          onChange={(value) => void core.act(() => core.taskAction(task.name, value ? "enable" : "disable"))}
        />
      </div>
      <div className="l1">{task.enabled ? `${t("已开启")} · ${coreText(task.description)}` : t("已关闭")}</div>
    </div>
  );
}
