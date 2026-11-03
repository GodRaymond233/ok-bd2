import { useEffect, useState } from "react";
import { api } from "./api.ts";
import type { Core } from "./App.tsx";
import { t } from "./i18n.ts";
import type { ReportState } from "./types.ts";

export function ReportPage({ core }: { core: Core }) {
  const [state, setState] = useState<ReportState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const load = () => {
    setLoading(true);
    return api.reportState().then((value) => { setState(value); setError(null); }, (reason: unknown) => setError(String(reason)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { void load(); }, []);
  const run = async (action: () => Promise<ReportState>) => {
    setBusy(true);
    setError(null);
    await core.act(async () => {
      try { setState(await action()); }
      catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); throw reason; }
    });
    setBusy(false);
  };
  const snapshot = state?.snapshot;
  const result = state?.result;
  const session = state?.session_id;
  return <div className="page report-page">
    <div className="ph"><div className="l"><h1 className="title">{t("问题报告")}</h1></div></div>
    <p className="ds">{t("生成本地诊断 ZIP，包含日志、运行信息和已有的故障截图。文件由你自行检查、发送，不会自动上传。")}</p>
    {error && <div className="ferr" role="alert">{error} <button className="btn sm" disabled={busy || loading} onClick={() => void load()}>{t("重新读取现场")}</button></div>}
    {loading ? <p role="status">{t("正在读取现场状态…")}</p> : !session ? <section className="card report-section">
      <h2>{t("1. 记录问题现场")}</h2><p>{t("如果任务正在运行，会先暂停执行并保留日志和运行信息。完成或取消报告后，由你决定是否继续任务。")}</p>
      <button className="btn primary" disabled={busy} onClick={() => { setDescription(""); void run(api.prepareReport); }}>{busy ? t("正在记录…") : t("记录问题现场")}</button>
    </section> : <>
      <section className="card report-section">
        <h2>{t("1. 已记录现场")}</h2>
        <p>{snapshot ? new Date(snapshot.captured_at).toLocaleString("zh-CN", { hour12: false }) : ""} · {core.tasks.find((task) => task.class_name === snapshot?.task.class)?.name ?? String(snapshot?.task.class ?? "")}</p>
        <p>{snapshot?.was_running ? t("执行器保持暂停，完成报告后可以选择继续。") : t("记录现场前执行器已暂停，不会自动启动任务。")}</p>
        {snapshot?.warnings.map((warning) => <p className="warnline" key={warning}>{warning}</p>)}
      </section>
      {!result ? <section className="card report-section">
        <h2>{t("2. 描述问题")}</h2><label htmlFor="report-description">{t("当时在做什么、哪里不符合预期（5～2000 字）")}</label>
        <textarea className="input" id="report-description" rows={5} maxLength={2000} disabled={busy} value={description} onChange={(event) => setDescription(event.target.value)} />
        <p className="ds">{t("日志中的已知敏感字段会脱敏，请勿在描述里填写密码或令牌。")}</p>
        <button className="btn primary" disabled={busy || description.trim().length < 5} onClick={() => void run(() => api.buildReport(session, description))}>{busy ? t("正在打包…") : t("生成诊断 ZIP")}</button>
      </section> : <section className="card report-section" role="status">
        <h2>{t("3. 报告已生成")}</h2><p className="record-text">{result.path}</p><pre className="record-text">{result.message}</pre>
        <div className="row"><button className="btn primary" onClick={() => void core.act(() => api.revealReport(result.id))}>{t("在文件夹中显示")}</button>
          <button className="btn" onClick={() => void core.act(async () => { await navigator.clipboard.writeText(result.message); core.notify(t("反馈说明已复制")); })}>{t("复制反馈说明")}</button></div>
      </section>}
      <div className="row report-actions"><button className="btn" disabled={busy} onClick={() => void run(() => api.finishReport(session, false))}>{snapshot?.was_running ? t("保持暂停，关闭报告") : t("关闭报告")}</button>
        {state.can_resume && <button className="btn primary" disabled={busy} onClick={() => void run(() => api.finishReport(session, true))}>{t("继续任务并关闭报告")}</button>}
      </div>
    </>}
  </div>;
}
