import { useEffect, useMemo, useState } from "react";
import { api } from "./api.ts";
import type { Core } from "./App.tsx";
import { durationText } from "./format.ts";
import { t } from "./i18n.ts";
import type { HistoryRun } from "./types.ts";
import { ConfirmDialog, Icon } from "./ui.tsx";

const timestamp = (value: number) => new Date(value * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });

export function RecordsPage({ core }: { core: Core }) {
  const [runs, setRuns] = useState<HistoryRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [task, setTask] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [days, setDays] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<HistoryRun | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.history("", 300).then(
      (value) => { if (!cancelled) { setRuns(value); setError(null); } },
      (reason: unknown) => !cancelled && setError(String(reason)),
    ).finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [core.historyVersion, reload]);
  const tasks = useMemo(() => [...new Set(runs.map((run) => run.task))], [runs]);
  const midnight = Math.floor((core.now + 8 * 3600) / 86400) * 86400 - 8 * 3600;
  const after = days === "all" ? 0 : midnight - (Number(days) - 1) * 86400;
  const filtered = runs.filter((run) => (!task || run.task === task)
    && (outcome === "all" || run.ok === (outcome === "ok")) && run.finished >= after
    && `${run.task} ${run.status} ${Object.values(run.details ?? {}).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="page records-page">
      <div className="ph"><div className="l"><h1 className="title">{t("记录")}</h1></div>
        <button className="btn sm" disabled={loading} onClick={() => setReload((value) => value + 1)}><Icon name="refresh" />{t("刷新记录")}</button>
      </div>
      <p className="ds">{t("保留最近 300 次已结束的任务，时间按北京时间显示。运行中的任务见底部执行面板。")}</p>
      <div className="card record-filters">
        <label>{t("任务")}<select className="select" aria-label={t("筛选任务")} value={task} onChange={(event) => setTask(event.target.value)}>
          <option value="">{t("全部任务")}</option>{tasks.map((name) => <option key={name}>{name}</option>)}
        </select></label>
        <label>{t("结果")}<select className="select" aria-label={t("筛选结果")} value={outcome} onChange={(event) => setOutcome(event.target.value)}>
          <option value="all">{t("全部结果")}</option><option value="ok">{t("成功")}</option><option value="bad">{t("未成功")}</option>
        </select></label>
        <label>{t("时间")}<select className="select" aria-label={t("筛选时间")} value={days} onChange={(event) => setDays(event.target.value)}>
          <option value="all">{t("全部时间")}</option><option value="1">{t("今天")}</option><option value="7">{t("近 7 天")}</option><option value="30">{t("近 30 天")}</option>
        </select></label>
        <label className="grow">{t("搜索")}<input className="input" type="search" aria-label={t("搜索运行记录")} placeholder={t("任务、状态或失败原因")} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      </div>
      {error && <div role="alert" className="ferr">{t("读取记录失败")}：{error}</div>}
      {loading && <p role="status">{t("正在读取记录…")}</p>}
      {!loading && !error && filtered.length === 0 && <div className="card empty">{runs.length ? t("没有符合筛选条件的记录") : t("还没有运行记录。任务结束后会自动记录在这里。")}</div>}
      {filtered.length > 0 && <div className="card record-list">
        <div className="record-heading">{t("符合条件")} {filtered.length} / {runs.length}</div>
        {filtered.map((run, index) => <div className="record-row" key={`${run.task}:${run.finished}:${index}`}>
          <div><b>{run.task}</b><div className="ds">{timestamp(run.finished)} · {t("耗时")} {durationText(run.duration) || "—"}</div></div>
          <span className={run.ok ? "chip ok" : "chip warn"}>{run.ok ? t("成功") : t("未成功")}</span>
          <span className="record-status">{run.status || t("未记录状态")}</span>
          <button className="btn sm" onClick={() => setSelected(run)}>{t("查看详情")}</button>
          <button className="btn sm" onClick={() => core.openLogs(run)}>{t("对应日志")}</button>
        </div>)}
      </div>}
      {selected && <ConfirmDialog title={`${t("运行详情")} · ${selected.task}`} confirm={t("查看对应日志")} cancel={t("关闭")} onCancel={() => setSelected(null)} onConfirm={() => core.openLogs(selected)}>
        <div className="record-detail">
        <p>{t("开始")}：{selected.started ? timestamp(selected.started) : t("未记录")}　{t("结束")}：{timestamp(selected.finished)}</p>
        <p>{selected.status || t("未记录状态")}</p>
        {Object.entries(selected.details ?? {}).map(([key, value]) => <div key={key}><b>{key === "Error" ? t("错误") : key}</b><pre className="record-text">{value}</pre></div>)}
        {!selected.ok && !Object.keys(selected.details ?? {}).length && <p className="ds">{t("这条记录没有额外错误详情，请结合对应日志检查。")}</p>}
        </div>
      </ConfirmDialog>}
    </div>
  );
}
