// The run panel at the bottom of every page: one line while idle, the current or last run
// otherwise. Pause, resume and stop act at once; there is no confirmation.
import type { Core } from "./App.tsx";
import { starterProgress, starterText } from "./device.ts";
import { durationText, elapsedText, refreshText } from "./format.ts";
import { t } from "./i18n.ts";
import { type Segment, SEAL_TEXT, type Seal, infoRows, isActive, segments } from "./run.ts";
import type { LogRecord } from "./logs.ts";
import type { HistoryRun, TaskPayload } from "./types.ts";
import { Icon } from "./ui.tsx";

export interface DockRun {
  task: TaskPayload;
  seal: Seal;
  /** The finished run's record, once the core wrote it. */
  record: HistoryRun | null;
  /** Seconds left before the announced shutdown, if 一键完成日常 scheduled one. */
  shutdownLeft: number | null;
}

const SEGMENT_TEXT: Record<Segment["kind"], string> = {
  done: "完成",
  fail: "失败",
  skip: "跳过",
  doing: "执行中",
  pause: "当前",
  todo: "待执行",
};

function SegmentBar({ parts }: { parts: Segment[] }) {
  return (
    <div className="segbar" role="img" aria-label={parts.map((part) => `${t(SEGMENT_TEXT[part.kind])} ${part.names.length}`).join("，")}>
      {parts.map((part) => (
        <i
          key={part.kind}
          className={`seg-${part.kind}`}
          style={{ flex: part.names.length }}
          title={`${t(SEGMENT_TEXT[part.kind])}：${part.names.join("、")}`}
        />
      ))}
    </div>
  );
}

function Legend({ parts }: { parts: Segment[] }) {
  return (
    <div className="legend">
      {parts.map((part) => (
        <span key={part.kind}>
          <i className={`seg-${part.kind}`} />
          {t(SEGMENT_TEXT[part.kind])}{" "}
          {part.kind === "doing" || part.kind === "pause" ? <b>{part.names.join("、")}</b> : <b>{part.names.length}</b>}
          {part.kind === "fail" && ` ${part.names.join("、")}`}
        </span>
      ))}
    </div>
  );
}

export function RunDock({
  core,
  run,
  logs,
  expanded,
  onExpand,
  onDismiss,
}: {
  core: Core;
  run: DockRun | null;
  logs: LogRecord[];
  expanded: boolean;
  onExpand: (expanded: boolean) => void;
  onDismiss: () => void;
}) {
  const connected = Boolean(core.device?.connected);
  const status = core.ui?.status;
  if (!run && status?.starting) {
    const wait = core.ui?.starter_wait ?? null;
    const progress = wait ? starterProgress(wait) : null;
    return (
      <section className="dock" aria-label={t("运行面板")}>
        <div className="bar">
          <span className="seal run">
            <span className="d" />
            {t("启动游戏")}
          </span>
          {core.starterTask && <span className="nm">{core.starterTask}</span>}
          <span className="sub">{wait ? starterText(wait) : t("正在启动 Starter…")}</span>
          <span className="grow" />
          <button type="button" className="btn sm" onClick={() => core.navigate("device")}>
            {t("详情")}
          </button>
          <button type="button" className="btn sm" disabled={!wait} onClick={() => void core.act(core.cancelStarter)}>
            {t("取消等待")}
          </button>
        </div>
        {progress !== null && (
          <div className="thin">
            <div className="bar-line">
              <i style={{ width: `${Math.round(progress * 100)}%` }} />
            </div>
          </div>
        )}
      </section>
    );
  }
  if (!run && core.startError) {
    return (
      <section className="dock" aria-label={t("运行面板")}>
        <div className="bar">
          <span className="seal issue">
            <span className="d" />
            {t("未开始")}
          </span>
          {core.starterTask && <span className="nm">{core.starterTask}</span>}
          <span className="sub">
            {t("启动游戏失败")}：{core.startError}
          </span>
          <span className="grow" />
          <button type="button" className="btn sm" onClick={() => core.openLogs(null)}>
            {t("查看日志")}
          </button>
          <button type="button" className="btn subtle sm icon" title={t("关闭")} aria-label={t("关闭")} onClick={core.dismissStartError}>
            <Icon name="x" />
          </button>
        </div>
      </section>
    );
  }
  if (!run) {
    return (
      <div className="dock-idle" role="status">
        <span>{core.ui?.status.starting ? t("正在启动游戏") : t("执行器空闲")}</span>
        <span className="sp">|</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className={connected ? "d" : "d off"} />
          {connected ? `${t("已连接")} · ${core.captureText}` : t("未连接游戏")}
          {!connected && (
            <button type="button" className="linkbtn" onClick={() => core.navigate("device")}>
              {t("连接")}
            </button>
          )}
        </span>
        {core.today && (
          <>
            <span className="sp">|</span>
            <span>
              {t("下次刷新")} {refreshText(core.today.refresh.daily, core.now)}
            </span>
          </>
        )}
        <span className="grow" />
        {core.today && core.allDone && <span className="ok">{t("今日日常已全部完成")}</span>}
      </div>
    );
  }

  const { task, seal } = run;
  const active = isActive(seal);
  const batchRun = core.batchRun && core.batchRun.batch.name === task.name ? core.batchRun : null;
  const items = core.today?.items.filter((item) => item.enabled) ?? [];
  const parts = batchRun ? segments(items, batchRun.runs, seal) : [];
  const childName = (key: string) => {
    const item = core.today?.items.find((candidate) => candidate.key === key);
    const stage = item ? core.childStage(item) : "";
    return item ? (stage ? `${item.name} · ${stage}` : item.name) : key;
  };
  const failed = parts.find((part) => part.kind === "fail")?.names ?? [];
  const rows = infoRows(task, seal, childName, failed);
  const current = batchRun && active ? items.find((item) => batchRun.runs.get(item.key) === "doing") : undefined;
  const time = active
    ? `${t("已运行")} ${elapsedText(core.now - task.start_time)}`
    : run.record?.duration
      ? `${t("用时")} ${durationText(run.record.duration)}`
      : "";
  const tail = logs.slice(-3);

  const controls = active ? (
    <>
      <button
        type="button"
        className="btn sm"
        disabled={seal === "stopping"}
        onClick={() => void core.act(() => core.taskAction(task.name, seal === "pause" ? "resume" : "pause"))}
      >
        <Icon name={seal === "pause" ? "play" : "pause"} />
        {seal === "pause" ? t("继续") : t("暂停")}
      </button>
      <button
        type="button"
        className="btn sm stop"
        disabled={seal === "stopping"}
        onClick={() => void core.act(() => core.taskAction(task.name, "stop"))}
      >
        <Icon name="stop" />
        {t("停止")}
      </button>
    </>
  ) : (
    <>
      <button type="button" className="btn sm" onClick={() => void core.openLogsFolder()}>
        <Icon name="folder" />
        {t("日志文件夹")}
      </button>
      <button type="button" className="btn sm" onClick={() => core.navigate("report")}>
        <Icon name="report" />
        {t("问题报告")}
      </button>
      <button type="button" className="btn subtle sm icon" title={t("关闭")} aria-label={t("关闭")} onClick={onDismiss}>
        <Icon name="x" />
      </button>
    </>
  );

  return (
    <section className="dock" aria-label={t("运行面板")}>
      <div className="bar">
        <span className={`seal ${seal}`}>
          <span className="d" />
          {t(SEAL_TEXT[seal])}
        </span>
        <span className="nm">{task.name}</span>
        {current && (
          <span className="sub">
            <Icon name="chev-r" />
            {current.name}
          </span>
        )}
        {time && <span className="time">{time}</span>}
        {!expanded && parts.length > 0 && (
          <span className="mini">
            <SegmentBar parts={parts} />
          </span>
        )}
        <span className="grow" />
        {controls}
        <button
          type="button"
          className="btn subtle sm icon"
          aria-expanded={expanded}
          title={expanded ? t("收起") : t("展开")}
          aria-label={expanded ? t("收起运行面板") : t("展开运行面板")}
          onClick={() => onExpand(!expanded)}
        >
          <Icon name={expanded ? "chev-d" : "chev-u"} />
        </button>
      </div>
      {expanded && (
        <div className="exp">
          {run.shutdownLeft !== null && (
            <div className="shutdown" role="alert">
              <Icon name="power" />
              <span>
                <b>{elapsedText(run.shutdownLeft)}</b> {t("后关机")}
              </span>
              <span className="t">{t("全部已启用的日常今日均已完成。要取消，在命令行执行 shutdown /a。")}</span>
            </div>
          )}
          <div>
            {parts.length > 0 && (
              <>
                <SegmentBar parts={parts} />
                <Legend parts={parts} />
              </>
            )}
            <div className="kv">
              {rows.map((row) => (
                <div key={row.key} style={{ display: "contents" }}>
                  <span className="k">{row.key}</span>
                  <span className={`v ${row.tone}`}>{row.value}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="loghd">
              <span>{t("最近日志")}</span>
              <button type="button" className="linkbtn" onClick={() => core.openLogs(active ? null : run.record)}>
                {t("查看全部")} <Icon name="chev-r" />
              </button>
            </div>
            <div className="logs" aria-live="polite">
              {tail.length === 0
                ? t("暂无日志")
                : tail.map((record) => (
                    <div key={record.key} className={record.level >= 40 ? "bad" : undefined}>
                      {record.time} {record.text}
                    </div>
                  ))}
            </div>
          </div>
        </div>
      )}
      {!expanded && parts.length > 0 && <div className="thin narrow-only"><SegmentBar parts={parts} /></div>}
    </section>
  );
}
