// 日志: the log file's last records plus the live ones, with level and text filters, date and
// run dividers, and "follow latest" that stops when you scroll up.
import { type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.ts";
import type { Core } from "./App.tsx";
import { durationText } from "./format.ts";
import { t } from "./i18n.ts";
import {
  type LevelFilter,
  type LogRecord,
  type LogRow,
  levelCounts,
  localClock,
  logRows,
  mergeLogs,
  parseLog,
  recordText,
  runStart,
} from "./logs.ts";
import type { HistoryRun } from "./types.ts";
import { ConfirmDialog, Icon, Toggle } from "./ui.tsx";

const NEAR_BOTTOM = 40;

function Highlight({ text, query }: { text: string; query: string }) {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return <>{text}</>;
  }
  const parts: ReactNode[] = [];
  const lower = text.toLowerCase();
  let at = 0;
  for (let found = lower.indexOf(needle); found >= 0; found = lower.indexOf(needle, at)) {
    parts.push(text.slice(at, found), <mark key={found}>{text.slice(found, found + needle.length)}</mark>);
    at = found + needle.length;
  }
  parts.push(text.slice(at));
  return <>{parts}</>;
}

function dateLabel(date: string, today: string): string {
  const [, month, day] = date.split("-").map(Number);
  return date === today ? `${t("今天")} ${month}月${day}日` : `${month}月${day}日`;
}

function RunDivider({ row, onOnly, only }: { row: Extract<LogRow, { kind: "run" }>; onOnly: () => void; only: boolean }) {
  const { run } = row;
  if (row.edge === "start") {
    return (
      <div className="ldiv" data-run={`${run.task}:${run.finished}`}>
        <Icon name="play" />
        <b>{run.task}</b>
        <span>
          {t("开始")} · {localClock(row.ts)}
        </span>
        <span className="grow" />
        {!only && (
          <button type="button" className="linkbtn" onClick={onOnly}>
            {t("只看这次")}
          </button>
        )}
      </div>
    );
  }
  return (
    <div className={run.ok ? "ldiv" : "ldiv bad"}>
      <Icon name="stop" />
      <b>{run.task}</b>
      <span>
        {t("结束")} · {run.ok ? t("成功") : run.status || t("未成功")}
        {run.duration ? ` · ${t("用时")} ${durationText(run.duration)}` : ""}
      </span>
    </div>
  );
}

function Line({ record, query }: { record: LogRecord; query: string }) {
  const [open, setOpen] = useState(false);
  const level = record.level >= 40 ? "e" : record.level >= 30 ? "w" : "";
  return (
    <div className={`lrow ${level}`}>
      <span className="tm">{record.time}</span>
      <span className="lv">{level === "e" ? t("错误") : level === "w" ? t("警告") : ""}</span>
      <span className="tx" title={record.source}>
        <Highlight text={record.text} query={query} />
      </span>
      <button
        type="button"
        className="linkbtn cp"
        onClick={() => void navigator.clipboard?.writeText(recordText(record))}
        aria-label={`${t("复制")} ${record.time}`}
      >
        {t("复制")}
      </button>
      {record.more.length > 0 && (
        <div className="tb">
          {open && record.more.map((line, index) => <div key={index}><Highlight text={line} query={query} /></div>)}
          <button type="button" className="linkbtn" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? t("收起") : `${t("展开")} ${record.more.length} ${t("行")}`}
          </button>
        </div>
      )}
    </div>
  );
}

export function LogsPage({
  core,
  focus,
  onFocus,
}: {
  core: Core;
  /** A run to show on its own, from the run panel or a task's recent runs. */
  focus: HistoryRun | null;
  onFocus: (run: HistoryRun | null) => void;
}) {
  const [file, setFile] = useState<LogRecord[] | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [runs, setRuns] = useState<HistoryRun[]>([]);
  const [level, setLevel] = useState<LevelFilter>("all");
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const [exporting, setExporting] = useState(false);
  const view = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);

  useEffect(() => {
    let cancelled = false;
    api.logs().then(
      (result) => !cancelled && setFile(parseLog(result.text)),
      (reason: unknown) => {
        if (!cancelled) {
          setFile([]);
          setFileError(reason instanceof Error ? reason.message : String(reason));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const { history, historyVersion } = core;
  useEffect(() => {
    let cancelled = false;
    history("", 50).then(
      (list) => !cancelled && setRuns(list),
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [history, historyVersion]);

  const records = useMemo(() => mergeLogs(file ?? [], core.logs), [file, core.logs]);
  const counts = useMemo(() => levelCounts(records), [records]);
  const rows = useMemo(() => logRows(records, runs, { level, query, run: focus }), [records, runs, level, query, focus]);
  const lines = rows.filter((row) => row.kind === "line").length;

  // New lines scroll into view while following; otherwise they are counted.
  useLayoutEffect(() => {
    const added = records.length - lastCount.current;
    lastCount.current = records.length;
    const element = view.current;
    if (!element || file === null) {
      return;
    }
    if (focus) {
      const target = element.querySelector(`[data-run="${CSS.escape(`${focus.task}:${focus.finished}`)}"]`);
      target?.scrollIntoView({ block: "start" });
      return;
    }
    if (follow) {
      element.scrollTop = element.scrollHeight;
    } else if (added > 0) {
      setUnseen((count) => count + added);
    }
  }, [records, rows, follow, focus, file]);

  const onScroll = () => {
    const element = view.current;
    if (!element || focus) {
      return;
    }
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < NEAR_BOTTOM;
    if (atBottom && !follow) {
      setFollow(true);
      setUnseen(0);
    } else if (!atBottom && follow) {
      setFollow(false);
    }
  };
  const followLatest = () => {
    setFollow(true);
    setUnseen(0);
    onFocus(null);
  };

  const levels: [LevelFilter, string][] = [
    ["all", t("全部")],
    ["info", t("信息")],
    ["warning", `${t("警告")} ${counts.warning}`],
    ["error", `${t("错误")} ${counts.error}`],
  ];
  const today = new Date();
  const todayText = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

  return (
    <div className="page logs-page">
      <div className="ph">
        <div className="l">
          <h1 className="title">{t("日志")}</h1>
          <span className="date">
            {t("本次运行与日志文件")} · {t("最近")} {records.length} {t("条")}
          </span>
        </div>
        <span className="row">
          <button type="button" className="btn sm" onClick={() => void core.openLogsFolder()}>
            <Icon name="folder" />
            {t("打开日志文件夹")}
          </button>
          <button type="button" className="btn sm" onClick={() => setExporting(true)}>
            <Icon name="download" />
            {t("导出日志")}…
          </button>
        </span>
      </div>
      <div className="ltool">
        <span className="segpick" role="group" aria-label={t("级别")}>
          {levels.map(([value, label]) => (
            <button key={value} type="button" aria-pressed={level === value} onClick={() => setLevel(value)}>
              {label}
            </button>
          ))}
        </span>
        <label className="search">
          <Icon name="search" />
          <input
            type="search"
            value={query}
            placeholder={t("搜索日志文字")}
            aria-label={t("搜索日志")}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {focus && (
          <span className="chip info filter">
            {t("只看")}：{focus.task} {localClock(runStart(focus)).slice(0, 5)}–{localClock(focus.finished).slice(0, 5)}
            <button type="button" className="linkbtn" aria-label={t("取消只看这次")} onClick={followLatest}>
              <Icon name="x" />
            </button>
          </span>
        )}
        <span className="grow" />
        {!focus && (
          <label className="tgl">
            <Toggle checked={follow} label={t("跟随最新")} onChange={(value) => (value ? followLatest() : setFollow(false))} />
            {t("跟随最新")}
          </label>
        )}
      </div>
      <div className="lview" ref={view} onScroll={onScroll} aria-live={follow ? "polite" : "off"}>
        {file === null && <div className="empty">{t("正在读取日志…")}</div>}
        {fileError && <div className="ferr">{t("读取日志文件失败")}：{fileError}</div>}
        {rows.map((row) => {
          if (row.kind === "date") {
            return (
              <div key={row.key} className="ldate">
                {dateLabel(row.date, todayText)}
              </div>
            );
          }
          if (row.kind === "run") {
            return <RunDivider key={row.key} row={row} only={focus !== null} onOnly={() => onFocus(row.run)} />;
          }
          return <Line key={row.key} record={row.record} query={query} />;
        })}
        {file !== null && lines === 0 && (
          <div className="empty">
            {t("没有符合条件的日志。")}{" "}
            <button
              type="button"
              className="linkbtn"
              onClick={() => {
                setLevel("all");
                setQuery("");
                onFocus(null);
              }}
            >
              {t("清除筛选")}
            </button>
          </div>
        )}
      </div>
      {!follow && unseen > 0 && (
        <button type="button" className="newpill" onClick={followLatest}>
          {t("有")} {unseen} {t("条新日志")} <Icon name="chev-d" />
        </button>
      )}
      <p className="why">{t("警告、错误的行左侧有竖线并标出级别；多行的记录（如错误堆栈）默认折叠。")}</p>
      {exporting && (
        <ConfirmDialog
          title={t("导出日志")}
          confirm={t("导出")}
          onCancel={() => setExporting(false)}
          onConfirm={() => {
            setExporting(false);
            void core.act(() => core.tool("export-logs"));
          }}
        >
          <p>
            {t("把 logs 和 screenshots 两个文件夹打包成 ok-bd2-log.zip，保存到「下载」文件夹，完成后在资源管理器中显示。")}
          </p>
          <p className="why">{t("截图里可能有你的游戏账号信息，发给别人之前先看一眼。")}</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
