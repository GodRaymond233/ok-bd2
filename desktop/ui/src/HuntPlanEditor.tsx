import { useEffect, useRef, useState } from "react";
import { api } from "./api.ts";
import type { Core } from "./App.tsx";
import { t } from "./i18n.ts";
import type { HuntBook, HuntPlan, HuntPlans, HuntStep, TaskPayload } from "./types.ts";
import { ConfirmDialog } from "./ui.tsx";

function moved<T>(items: T[], index: number, delta: number): T[] {
  const next = [...items];
  [next[index], next[index + delta]] = [next[index + delta], next[index]];
  return next;
}

function Steps({ steps, group, choices, onChange }: {
  steps: HuntStep[];
  group: string;
  choices: HuntPlans;
  onChange: (steps: HuntStep[]) => void;
}) {
  const edit = (index: number, update: Partial<HuntStep>) =>
    onChange(steps.map((step, at) => at === index ? { ...step, ...update } : step));
  return <div className="hunt-steps">
    {steps.length === 0 && <p className="why">{t("保留米饭，不刷取。")}</p>}
    {steps.map((step, index) => {
      const label = `${group} ${t("步骤")} ${index + 1}`;
      return <div className="hunt-step" key={index}>
        <span>{index + 1}.</span>
        <select className="select" aria-label={`${label} ${t("目标")}`} value={step.target}
          onChange={(event) => edit(index, { target: event.target.value })}>
          {Object.entries(choices.targets).map(([key, text]) => <option key={key} value={key}>{t(text)}</option>)}
        </select>
        <select className="select" aria-label={`${label} ${t("数量方式")}`} value={step.mode}
          onChange={(event) => {
            const count = step.count ?? 1;
            edit(index, { mode: event.target.value,
              count: Number.isInteger(count) && count >= 1 && count <= 999 ? count : 1 });
          }}>
          {Object.entries(choices.modes).map(([key, text]) => <option key={key} value={key}>{t(text)}</option>)}
        </select>
        {step.mode !== "all" && <label className="hunt-count">
          <input className="input" type="number" min={1} max={999} step={1}
            aria-label={`${label} ${t("数量")}`} value={Number.isNaN(step.count) ? "" : step.count ?? 1}
            onChange={(event) => edit(index, { count: event.target.valueAsNumber })} />
          {step.mode === "count" ? t("次") : t("米饭")}
        </label>}
        <span className="grow" />
        <button className="btn sm" aria-label={`${label} ${t("上移")}`} disabled={index === 0}
          onClick={() => onChange(moved(steps, index, -1))}>↑</button>
        <button className="btn sm" aria-label={`${label} ${t("下移")}`} disabled={index === steps.length - 1}
          onClick={() => onChange(moved(steps, index, 1))}>↓</button>
        <button className="btn sm" aria-label={`${label} ${t("删除")}`}
          onClick={() => onChange(steps.filter((_, at) => at !== index))}>{t("删除步骤")}</button>
      </div>;
    })}
    <button className="btn sm" onClick={() => onChange([...steps, { target: "hunt", mode: "count", count: 1 }])}>
      ＋ {t("添加步骤")}
    </button>
  </div>;
}

export function HuntPlanEditor({ core, task, onClose }: { core: Core; task: TaskPayload; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [data, setData] = useState<HuntPlans | null>(null);
  const [book, setBook] = useState<HuntBook | null>(null);
  const [baseline, setBaseline] = useState("null");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<"close" | "reload" | "delete" | "replace" | null>(null);
  const [rice, setRice] = useState(90);
  const [states, setStates] = useState<Record<string, boolean | null>>({ 金币: true, 经验: false });
  const [preview, setPreview] = useState<{ key: string; text: string; error: boolean; runnable: boolean } | null>(null);
  const [retry, setRetry] = useState(0);
  const dirty = JSON.stringify(book) !== baseline;
  const locked = core.busy || Boolean(task.config_busy) || saving;
  const previewKey = JSON.stringify({ book, rice, states });
  const valid = preview?.key === previewKey && !preview.error;
  const plan = book?.plans[book.active];

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const value = await api.huntPlans(task.name);
      setData(value);
      setBook(value.book);
      setBaseline(JSON.stringify(value.book));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    void load();
    return () => element?.close();
  }, [task.name]);

  useEffect(() => {
    if (!book) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api.previewHuntPlan(task.name, book, rice, states, controller.signal).then(
        (value) => {
          if (!controller.signal.aborted) setPreview({ key: previewKey, ...value, error: false });
        },
        (reason: unknown) => {
          if (!controller.signal.aborted) setPreview({ key: previewKey, text: String(reason), error: true, runnable: false });
        },
      );
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [task.name, previewKey, retry]);

  const change = (edit: (draft: HuntBook) => void) => {
    setBook((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      edit(next);
      return next;
    });
    setError(null);
  };
  const editPlan = (edit: (draft: HuntPlan) => void) => change((draft) => edit(draft.plans[draft.active]));
  const append = (next: HuntPlan) => change((draft) => {
    draft.plans.push(structuredClone(next));
    draft.active = draft.plans.length - 1;
  });
  const close = () => { if (!saving) dirty ? setConfirmation("close") : onClose(); };
  const save = async () => {
    if (!book || !data || !valid || locked) return;
    setSaving(true);
    setError(null);
    try {
      await core.saveHuntPlans(task.name, book, data.revision);
      core.notify(t("方案已保存，将用于后续快速狩猎"));
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return <dialog className="dialog hunt-editor" ref={dialog} aria-label={t("快速狩猎方案编辑器")}
    onCancel={(event) => { event.preventDefault(); confirmation ? setConfirmation(null) : close(); }}>
    <div className="row">
      <h2>{task.name} · {t("刷取方案")}</h2><span className="grow" />
      <button className="btn sm" disabled={saving} onClick={close}>{t("关闭")}</button>
    </div>
    <p className="why">{t("从上往下匹配，只执行第一条满足条件的规则；狩猎场沿用游戏当前关卡。仅使用免费米饭。")}</p>
    {error && <p className="ferr" role="alert">{error}</p>}
    {loading ? <p role="status">{t("正在读取方案…")}</p> : <>
      {data?.inherited && <p className="why">{t("当前方案由原有刷取设置转换而来；确认保存前不会修改配置。")}</p>}
      {data?.load_error && <div className="warnline" role="alert">
        {data.load_error} {t("原配置尚未修改，确认保存后才会替换。")}
        {!book && <button className="btn sm" disabled={locked} onClick={() => setBook({ version: 1, active: 0, plans: [structuredClone(data.presets[0])] })}>{t("建立替换草稿")}</button>}
      </div>}
      {locked && <p className="warnline">{t("运行、排队或保留诊断现场期间不能修改方案，可以查看和模拟预览。")}</p>}
      {data && book && plan && <>
        <fieldset className="hunt-fields" disabled={locked}>
          <div className="hunt-toolbar">
            <label>{t("当前方案")}
              <select className="select" aria-label={t("选择刷取方案")} value={book.active}
                onChange={(event) => change((draft) => { draft.active = Number(event.target.value); })}>
                {book.plans.map((entry, index) => <option key={index} value={index}>{entry.name || t("未命名方案")}</option>)}
              </select>
            </label>
            <button className="btn" onClick={() => append({ name: t("我的刷取方案"), rules: [], fallback: [], reserve: 0 })}>{t("新建方案")}</button>
            <button className="btn" onClick={() => append({ ...structuredClone(plan), name: `${plan.name} · ${t("副本")}` })}>{t("复制方案")}</button>
            <button className="btn" disabled={book.plans.length === 1} onClick={() => setConfirmation("delete")}>{t("删除方案")}</button>
            <select className="select" value="" aria-label={t("添加方案预设")} onChange={(event) => { if (event.target.value !== "") append(data.presets[Number(event.target.value)]); }}>
              <option value="">{t("添加预设…")}</option>
              {data.presets.map((entry, index) => <option key={index} value={index}>{entry.name}</option>)}
            </select>
          </div>
          <div className="hunt-toolbar">
            <label className="grow">{t("方案名称")}<input className="input" aria-label={t("方案名称")} value={plan.name}
              onChange={(event) => editPlan((draft) => { draft.name = event.target.value; })} /></label>
            <label>{t("保留免费米饭")}<input className="input" type="number" min={0} max={9999} step={1}
              aria-label={t("保留免费米饭")} value={Number.isNaN(plan.reserve) ? "" : plan.reserve ?? 0}
              onChange={(event) => editPlan((draft) => { draft.reserve = event.target.valueAsNumber; })} /></label>
          </div>
          {plan.rules.map((rule, index) => <section className="hunt-rule" key={index}>
            <div className="hunt-toolbar">
              <b>{t("规则")} {index + 1} · {t("当")}</b>
              <select className="select" aria-label={`${t("规则")} ${index + 1} ${t("条件")}`} value={rule.condition}
                onChange={(event) => editPlan((draft) => { draft.rules[index].condition = event.target.value; })}>
                {Object.entries(data.conditions).map(([key, text]) => <option key={key} value={key}>{t(text)}</option>)}
              </select>
              <span className="grow" />
              <button className="btn sm" disabled={index === 0} aria-label={`${t("规则")} ${index + 1} ${t("上移")}`}
                onClick={() => editPlan((draft) => { draft.rules = moved(draft.rules, index, -1); })}>↑</button>
              <button className="btn sm" disabled={index === plan.rules.length - 1} aria-label={`${t("规则")} ${index + 1} ${t("下移")}`}
                onClick={() => editPlan((draft) => { draft.rules = moved(draft.rules, index, 1); })}>↓</button>
              <button className="btn sm" onClick={() => editPlan((draft) => { draft.rules.splice(index, 1); })}>{t("删除规则")}</button>
            </div>
            <Steps steps={rule.steps} group={`${t("规则")} ${index + 1}`} choices={data}
              onChange={(steps) => editPlan((draft) => { draft.rules[index].steps = steps; })} />
          </section>)}
          <button className="btn" onClick={() => editPlan((draft) => { draft.rules.push({ condition: "gold", steps: [{ target: "gold", mode: "all", count: 1 }] }); })}>＋ {t("添加条件规则")}</button>
          <section className="hunt-rule">
            <h3>{t("其他情况 · 前面的规则都不满足时")}</h3>
            <Steps steps={plan.fallback} group={t("其他情况")} choices={data}
              onChange={(steps) => editPlan((draft) => { draft.fallback = steps; })} />
          </section>
        </fieldset>
        <section className="hunt-preview">
          <h3>{t("模拟执行预览")}</h3>
          <div className="hunt-toolbar">
            {Object.entries(states).map(([key, value]) => <label key={key}>{key}
              <select className="select" aria-label={`${key} ${t("模拟双倍状态")}`} value={String(value)}
                onChange={(event) => setStates({ ...states, [key]: event.target.value === "null" ? null : event.target.value === "true" })}>
                <option value="true">{t("双倍")}</option><option value="false">{t("非双倍")}</option><option value="null">{t("无法确认")}</option>
              </select>
            </label>)}
            <label>{t("模拟免费米饭")}<input className="input" type="number" min={0} max={9999} step={1}
              aria-label={t("模拟免费米饭")} value={Number.isNaN(rice) ? "" : rice} onChange={(event) => setRice(event.target.valueAsNumber)} /></label>
            <button className="btn sm" onClick={() => setRetry((value) => value + 1)}>{t("重新预览")}</button>
          </div>
          <pre className={`record-text ${preview?.error || preview?.runnable === false ? "ferr" : ""}`} role="status">
            {preview?.key === previewKey ? preview.text : t("正在校验并计算预览…")}
          </pre>
          <p className="why">{t("预览不读取游戏、不执行任务；每次消耗 6 米饭，不足一次的余量保留。")}</p>
        </section>
      </>}
    </>}
    <div className="hunt-actions">
      <span className="why">{dirty ? t("草稿尚未保存") : t("与已读取配置一致")}</span><span className="grow" />
      <button className="btn" disabled={loading || saving} onClick={() => dirty ? setConfirmation("reload") : void load()}>{t("重新读取")}</button>
      <button className="btn" disabled={saving} onClick={close}>{t("取消")}</button>
      <button className="btn primary" disabled={loading || locked || !book || !valid}
        onClick={() => data?.load_error ? setConfirmation("replace") : void save()}>{saving ? t("正在保存…") : t("保存并使用此方案")}</button>
    </div>
    {confirmation && <ConfirmDialog
      title={confirmation === "delete" ? t("删除当前草稿方案？") : confirmation === "replace" ? t("替换无法读取的原方案？") : t("放弃未保存的草稿？")}
      confirm={confirmation === "delete" ? t("删除") : confirmation === "replace" ? t("确认替换并保存") : t("放弃草稿")}
      onCancel={() => setConfirmation(null)}
      onConfirm={() => {
        const action = confirmation;
        setConfirmation(null);
        if (action === "close") onClose();
        else if (action === "reload") void load();
        else if (action === "replace") void save();
        else change((draft) => { draft.plans.splice(draft.active, 1); draft.active = Math.min(draft.active, draft.plans.length - 1); });
      }}>
      <p>{confirmation === "delete" ? t("保存前只修改当前草稿，至少保留一个方案。") : confirmation === "replace" ? t("确认后会用当前方案替换原配置；取消则保留原文件。") : t("已保存的配置不会改变，当前未保存的编辑将丢失。")}</p>
    </ConfirmDialog>}
  </dialog>;
}
