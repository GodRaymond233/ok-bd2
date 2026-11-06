import { useEffect, useRef, useState } from "react";
import { api, errorText } from "./api.ts";
import type { Core } from "./App.tsx";
import { coreText, t } from "./i18n.ts";
import { CodeEditor } from "./ScriptsPage.tsx";
import type { ToolState } from "./types.ts";
import { Icon } from "./ui.tsx";

let draft: { code: string; task: string } | null = null;

export function ToolsPage({ core }: { core: Core }) {
  const [state, setState] = useState<ToolState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [code, setCode] = useState(draft?.code ?? "");
  const [task, setTask] = useState(draft?.task ?? "");
  const editor = useRef<HTMLTextAreaElement>(null);
  const initialized = useRef(false);
  const tasks = core.tasks;
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      try {
        const next = await api.tools();
        if (!active) return;
        setState(next);
        setError(null);
        if (!initialized.current) {
          initialized.current = true;
          if (draft === null) {
            const selected = tasks.find((item) => item.class_name === next.task_class)?.name ?? "";
            setCode(next.code); setTask(selected);
            draft = { code: next.code, task: selected };
          }
        }
        timer = setTimeout(() => void refresh(), next.active ? 400 : 1500);
      } catch (reason) {
        if (active) { setError(errorText(reason)); timer = setTimeout(() => void refresh(), 1500); }
      }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [tasks]);

  const run = async (action: () => Promise<ToolState>) => {
    setWorking(true);
    setError(null);
    try { setState(await action()); }
    catch (reason) { setError(errorText(reason)); }
    finally { setWorking(false); }
  };
  const blocked = core.startBlocked;
  const active = Boolean(state?.active);
  const disabled = !state || working || active || Boolean(blocked);
  const updateCode = (value: string) => { setCode(value); draft = { code: value, task }; };
  const output = [state?.output, state?.kind === "code" ? state.result?.value : null, state?.result?.error].filter(Boolean).join("\n");
  const details = state?.result?.details;
  const boxes = details?.boxes;

  return <div className="page tools-page">
    <div className="ph"><div className="l"><h1 className="title">{t("工具")}</h1><div className="sub">{t("检查连接、识别文字，或临时运行一段代码。")}</div></div></div>
    {blocked && !active && <div className="warnline">{blocked}</div>}
    {error && <div className="ferr" role="alert">{error}</div>}
    <section className="card pad tool-actions">
      <div className="row">
        <button className="btn" disabled={disabled} onClick={() => void run(() => api.runTool("check"))}><Icon name="check" />{t("运行基础检查")}</button>
        <button className="btn" disabled={disabled || !core.device?.connected} onClick={() => void run(() => api.runTool("ocr"))}><Icon name="target" />{t("识别文字（OCR）")}</button>
        <button className="btn subtle" onClick={() => void core.act(() => core.tool("screenshot-folder"))}>{t("打开截图文件夹")}</button>
      </div>
      <p className="why">{t("基础检查不会启动游戏。OCR 读取已连接窗口的当前画面，并保存识别截图。")}</p>
      {!core.device?.connected && <p className="why">{t("请先在设备页连接游戏，再使用 OCR。")}</p>}
    </section>
    <section className="card tool-code">
      <div className="bh">{t("临时代码")}</div>
      <div className="pad">
        <p className="dsc">{t("代码可以读写本机文件并操作游戏，只运行你信任的代码。")}</p>
        <p className="why">{t("可使用 og、executor 和选中的 task；变量在多次运行之间保留，最后一行表达式的值会显示出来。")}</p>
        <div className="row">
          <label className="row">{t("关联任务")}<select className="select" aria-label={t("关联任务")} value={task} disabled={active} onChange={(event) => { setTask(event.target.value); draft = { code, task: event.target.value }; }}>
            <option value="">{t("不关联任务")}</option>
            {tasks.map((item) => <option key={item.name} value={item.name}>{coreText(item.name)}</option>)}
          </select></label>
          <span className="grow" />
          <button className="btn" disabled={disabled} onClick={() => void run(api.resetToolVariables)}>{t("重置变量")}</button>
          {active
            ? <button className="btn stop" disabled={working || state?.stopping} onClick={() => void run(api.stopTool)}>{state?.stopping ? t("正在停止…") : t("停止")}</button>
            : <button className="btn primary" disabled={disabled || !code.trim()} onClick={() => void run(() => api.runTool("code", code, task))}><Icon name="play" />{t("运行代码")}</button>}
        </div>
      </div>
      <CodeEditor value={code} errorLine={null} editor={editor} onChange={updateCode} />
      {active && <p className="hidden-note" role="status">{state?.stopping ? t("停止请求已发送；代码会在下一次等待、截图或任务检查时结束。") : t("工具操作进行中，其他任务和配置修改暂时不可用。")}</p>}
    </section>
    <section className="card tool-results">
      <div className="bh">{t("运行结果")}<span className="grow" /><button className="btn sm subtle" disabled={!state} onClick={() => void run(api.clearToolOutput)}>{t("清空输出")}</button></div>
      <div className="pad">
        {state?.result?.cancelled && <p role="status">{t("操作已停止")}</p>}
        {details?.checks && <dl className="tool-checks">{details.checks.map((item) => <div key={item.name}><dt>{coreText(item.name)}</dt><dd className={item.ok ? "saved" : "why"}>{coreText(item.detail)}</dd></div>)}</dl>}
        {boxes && <>
          <div className="row"><b>{t("识别到 {count} 处文字", { count: boxes.length })}</b><span className="grow" /><button className="btn sm" onClick={() => void core.act(async () => { await navigator.clipboard.writeText(boxes.map((box) => box.text).join("\n")); core.notify(t("识别文字已复制")); })}>{t("复制文字")}</button></div>
          {state?.image_url && <svg className="ocr-image" viewBox={`0 0 ${details?.width} ${details?.height}`} aria-label={t("OCR 识别结果图")}>
            <image href={state.image_url} width={details?.width} height={details?.height} />
            {boxes.map((box, index) => <rect key={index} x={box.x} y={box.y} width={box.width} height={box.height}><title>{box.text}</title></rect>)}
          </svg>}
          <div className="ocr-text">{boxes.map((box, index) => <div key={index}><span>{box.text}</span><small>{(box.confidence * 100).toFixed(1)}%</small></div>)}</div>
        </>}
        {output && <pre className="record-text tool-output">{output}</pre>}
        {state?.truncated && <p className="why">{t("输出较长，页面只保留前 100000 个字符。")}</p>}
        {!output && !details && !active && <p className="why">{t("运行后的输出会显示在这里。")}</p>}
      </div>
    </section>
  </div>;
}
