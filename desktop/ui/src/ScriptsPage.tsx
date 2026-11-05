// The script editor: custom tasks in ok_tasks, a palette of BaseTask helpers and a code editor
// with line numbers. Unsaved text survives moving to another page.
import { type ChangeEvent, type KeyboardEvent, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "./api.ts";
import type { Core } from "./App.tsx";
import { coreText, t } from "./i18n.ts";
import {
  dedentSelection,
  type Edit,
  helperCall,
  helperGroups,
  indentSelection,
  insertLine,
  missingParam,
  newLine,
  tokenize,
  validClassName,
} from "./scripts.ts";
import type { ExportOptions, RecordingState, ScriptDocument, ScriptEntry, ScriptHelper } from "./types.ts";
import { ConfirmDialog, Icon, Menu, type MenuItem } from "./ui.tsx";

const GUIDE_URL = "https://github.com/ok-oldking/ok-py";
const WATCH_INTERVAL = 3000;
const IMPORT_COUNTDOWN = 15;
const GROUP_LABELS: Record<string, string> = {
  Mouse: "鼠标",
  Control: "流程控制",
  OCR: "文字识别",
  "Template Matching": "模板匹配",
  Box: "区域",
  Window: "窗口",
  ADB: "ADB",
  Logging: "日志",
  Other: "其他",
};

const drafts = new Map<string, string>();
let lastOpen: string | null = null;

function CodeEditor({ value, errorLine, editor, onChange, onSave }: {
  value: string;
  errorLine: number | null;
  editor: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void;
  onSave: () => void;
}) {
  const gutter = useRef<HTMLDivElement>(null);
  const colored = useRef<HTMLPreElement>(null);
  const tokens = useMemo(() => tokenize(value), [value]);
  const lines = useMemo(() => value.split("\n").length, [value]);

  const apply = (edit: Edit) => {
    onChange(edit.text);
    // The controlled text area takes the new text on the next render.
    requestAnimationFrame(() => editor.current?.setSelectionRange(edit.start, edit.end));
  };

  const keys = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const { selectionStart: start, selectionEnd: end } = event.currentTarget;
    if (event.key === "Tab") {
      event.preventDefault();
      apply(event.shiftKey ? dedentSelection(value, start, end) : indentSelection(value, start, end));
    } else if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      apply(newLine(value, start, end));
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      onSave();
    }
  };

  const follow = () => {
    const host = editor.current;
    if (host && gutter.current && colored.current) {
      gutter.current.scrollTop = host.scrollTop;
      colored.current.scrollTop = host.scrollTop;
      colored.current.scrollLeft = host.scrollLeft;
    }
  };

  return (
    <div className="code">
      <div className="code-lines" ref={gutter} aria-hidden="true">
        {Array.from({ length: lines }, (_, index) => (
          <div key={index} className={index + 1 === errorLine ? "bad" : undefined}>{index + 1}</div>
        ))}
      </div>
      <div className="code-body">
        <pre ref={colored} aria-hidden="true">
          {tokens.map((token, index) => (token.kind === "plain" ? token.text : <span key={index} className={`tk-${token.kind}`}>{token.text}</span>))}
          {"\n"}
        </pre>
        <textarea
          ref={editor}
          aria-label={t("脚本代码")}
          spellCheck={false}
          wrap="off"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={keys}
          onScroll={follow}
        />
      </div>
    </div>
  );
}

function CreateDialog({ onCreated, onCancel }: { onCreated: (document: ScriptDocument) => void; onCancel: () => void }) {
  const [className, setClassName] = useState("");
  const [taskName, setTaskName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = () => {
    if (!validClassName(className.trim())) {
      setError(t("类名只能用英文字母、数字和下划线，且不能以数字开头"));
    } else if (!taskName.trim()) {
      setError(t("请填写任务名称"));
    } else {
      api.createScript(className.trim(), taskName.trim(), description.trim()).then(onCreated, (reason: unknown) => setError(errorText(reason)));
    }
  };
  return (
    <ConfirmDialog title={t("新建脚本")} confirm={t("创建")} onConfirm={create} onCancel={onCancel}>
      <div className="form-stack">
        <label>{t("类名")}<input className="input" value={className} placeholder="MyTask" onChange={(event) => setClassName(event.target.value)} /><small>{t("只能用英文，也是脚本的文件名。")}</small></label>
        <label>{t("任务名称")}<input className="input" value={taskName} onChange={(event) => setTaskName(event.target.value)} /></label>
        <label>{t("说明（可选）")}<input className="input" value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        {error && <div className="ferr" role="alert">{error}</div>}
      </div>
    </ConfirmDialog>
  );
}

function HelperDialog({ helper, onInsert, onCancel }: { helper: ScriptHelper; onInsert: (code: string) => void; onCancel: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<string | null>(null);
  const insert = () => {
    const name = missingParam(helper, values);
    setMissing(name);
    if (!name) {
      onInsert(helperCall(helper, values));
    }
  };
  return (
    <ConfirmDialog title={helper.name} confirm={t("插入")} onConfirm={insert} onCancel={onCancel}>
      {helper.doc && <p>{helper.doc}</p>}
      <div className="form-stack">
        {helper.params.map((param) => (
          <label key={param.name}>
            <code>{param.name}</code>
            <input
              className="input mono"
              value={values[param.name] ?? ""}
              placeholder={param.default ? t("默认：{value}", { value: param.default }) : t("必填")}
              onChange={(event) => setValues({ ...values, [param.name]: event.target.value })}
            />
            {param.doc && <small>{param.doc}</small>}
          </label>
        ))}
        {missing && <div className="ferr" role="alert">{t("请填写 {name}", { name: missing })}</div>}
      </div>
    </ConfirmDialog>
  );
}

function ExportDialog({ options, onDone, onCancel }: {
  options: ExportOptions;
  onDone: (name: string) => void;
  onCancel: () => void;
}) {
  const [selected, setSelected] = useState(() => new Set(options.tasks));
  const [fileName, setFileName] = useState(options.manifest.file_name ?? "");
  const [scriptName, setScriptName] = useState(options.manifest.script_name ?? "");
  const [version, setVersion] = useState(options.manifest.version ?? "1.0.0");
  const [error, setError] = useState<string | null>(null);
  const run = () => {
    api.exportScripts([...selected], fileName.trim(), scriptName.trim(), version.trim()).then(
      (result) => onDone(result.name),
      (reason: unknown) => setError(errorText(reason)),
    );
  };
  return (
    <ConfirmDialog title={t("导出脚本")} confirm={t("导出")} onConfirm={run} onCancel={onCancel}>
      <div className="form-stack">
        <fieldset>
          <legend>{t("要导出的脚本")}</legend>
          {options.tasks.map((name) => (
            <label key={name} className="check">
              <input
                type="checkbox"
                checked={selected.has(name)}
                onChange={(event) => {
                  const next = new Set(selected);
                  if (event.target.checked) {
                    next.add(name);
                  } else {
                    next.delete(name);
                  }
                  setSelected(next);
                }}
              />
              {name.replace(/\.py$/, "")}
            </label>
          ))}
        </fieldset>
        <label>{t("文件名")}<input className="input" value={fileName} onChange={(event) => setFileName(event.target.value)} /><small>{t("只能用英文字母、数字、下划线和连字符。")}</small></label>
        <label>{t("脚本名称")}<input className="input" value={scriptName} onChange={(event) => setScriptName(event.target.value)} /></label>
        <label>{t("版本")}<input className="input" value={version} placeholder="1.0.0" onChange={(event) => setVersion(event.target.value)} /></label>
        {error && <div className="ferr" role="alert">{error}</div>}
      </div>
    </ConfirmDialog>
  );
}

function ImportDialog({ file, onDone, onCancel }: { file: File; onDone: (restart: boolean) => void; onCancel: () => void }) {
  const [understood, setUnderstood] = useState(false);
  const [countdown, setCountdown] = useState(IMPORT_COUNTDOWN);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (countdown <= 0) {
      return;
    }
    const timer = setTimeout(() => setCountdown(countdown - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);
  const run = () => {
    if (understood && countdown <= 0) {
      api.importScripts(file).then((result) => onDone(result.restart_required), (reason: unknown) => setError(errorText(reason)));
    }
  };
  return (
    <ConfirmDialog
      title={t("导入脚本")}
      confirm={countdown > 0 ? t("确认（{seconds}）", { seconds: countdown }) : t("确认")}
      disabled={!understood || countdown > 0}
      onConfirm={run}
      onCancel={onCancel}
    >
      <p>{file.name}</p>
      <p className="danger-text">{t("请确认你信任脚本的发布者。来历不明的脚本可能盗取账号和资料、破坏数据，甚至控制你的电脑。")}</p>
      <label className="check">
        <input type="checkbox" checked={understood} onChange={(event) => setUnderstood(event.target.checked)} />
        {t("我了解风险，仍要导入这个脚本")}
      </label>
      <p className="why">{t("导入后，其他脚本和任务会先关闭。")}</p>
      {error && <div className="ferr" role="alert">{error}</div>}
    </ConfirmDialog>
  );
}

function RecordDialog({ onStart, onCancel }: { onStart: (loop: string, count: number) => void; onCancel: () => void }) {
  const [loop, setLoop] = useState("none");
  const [count, setCount] = useState(10);
  return (
    <ConfirmDialog title={t("录制操作")} confirm={t("开始录制")} onConfirm={() => onStart(loop, count)} onCancel={onCancel}>
      <p>{t("录制结束后，run() 里的内容和截图配置会被录制结果替换。")}</p>
      <p>{t("切换到游戏窗口后开始记录，只记录鼠标点击，不记录键盘。")}</p>
      <div className="form-stack">
        <label>
          {t("循环方式")}
          <select className="select" value={loop} onChange={(event) => setLoop(event.target.value)}>
            <option value="none">{t("不循环")}</option>
            <option value="count">{t("循环指定次数")}</option>
            <option value="forever">{t("一直循环")}</option>
          </select>
        </label>
        {loop === "count" && (
          <label>
            {t("循环次数")}
            <input className="input" type="number" min={1} max={999999} value={count} onChange={(event) => setCount(Number(event.target.value) || 1)} />
          </label>
        )}
      </div>
    </ConfirmDialog>
  );
}

type Dialog =
  | { kind: "create" }
  | { kind: "delete" }
  | { kind: "export"; options: ExportOptions }
  | { kind: "import"; file: File }
  | { kind: "record" }
  | { kind: "helper"; helper: ScriptHelper };

export function ScriptsPage({ core }: { core: Core }) {
  const [entries, setEntries] = useState<ScriptEntry[] | null>(null);
  const [helpers, setHelpers] = useState<ScriptHelper[]>([]);
  const [query, setQuery] = useState("");
  const [closed, setClosed] = useState<Set<string>>(() => new Set(Object.keys(GROUP_LABELS)));
  const [document, setDocument] = useState<ScriptDocument | null>(null);
  const [code, setCode] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [recording, setRecording] = useState<RecordingState | null>(null);
  const [recordLoop, setRecordLoop] = useState({ loop: "none", count: 1 });
  const [working, setWorking] = useState(false);
  const [external, setExternal] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  const importInput = useRef<HTMLInputElement>(null);

  const dirty = document !== null && code !== document.code;
  const blocked = core.startBlocked;
  const isRecording = Boolean(recording?.recording);

  const open = useCallback(async (name: string) => {
    const loaded = await api.script(name);
    lastOpen = name;
    setDocument(loaded);
    setCode(drafts.get(name) ?? loaded.code);
    setExternal(false);
  }, []);

  const report = useCallback((reason: unknown) => {
    core.act(() => Promise.reject(reason instanceof Error ? reason : new Error(String(reason))));
  }, [core]);

  const reload = useCallback(async () => {
    const list = await api.scripts();
    setEntries(list);
    return list;
  }, []);

  useEffect(() => {
    reload().then(
      (list) => {
        const start = (list.find((entry) => entry.name === lastOpen) ?? list.find((entry) => entry.task) ?? list[0])?.name;
        if (start) {
          return open(start);
        }
      },
    ).catch(report);
    api.scriptHelpers().then(setHelpers, () => undefined);
    api.recording().then(setRecording, () => undefined);
    // Once, on arrival; the watch below keeps the list current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Files can change outside the client, as in the Qt window's file watcher.
  const openName = document?.name ?? null;
  const openModified = document?.modified ?? 0;
  useEffect(() => {
    const timer = setInterval(() => {
      void reload().then((list) => {
        const entry = list.find((item) => item.name === openName);
        if (!openName) {
          return;
        }
        if (!entry) {
          drafts.delete(openName);
          setDocument(null);
          setCode("");
        } else if (entry.modified > openModified) {
          if (drafts.has(openName)) {
            setExternal(true);
          } else {
            void open(openName);
          }
        }
      }).catch(() => undefined);
    }, WATCH_INTERVAL);
    return () => clearInterval(timer);
  }, [openName, openModified, open, reload]);

  useEffect(() => {
    if (!isRecording) {
      return;
    }
    const timer = setInterval(() => void api.recording().then(setRecording, () => undefined), 1000);
    return () => clearInterval(timer);
  }, [isRecording]);

  const change = (value: string) => {
    setCode(value);
    if (document) {
      if (value === document.code) {
        drafts.delete(document.name);
      } else {
        drafts.set(document.name, value);
      }
    }
  };

  const settled = (saved: ScriptDocument) => {
    drafts.delete(saved.name);
    setDocument(saved);
    setCode(saved.code);
    setExternal(false);
    void reload().catch(() => undefined);
  };

  const save = async () => {
    if (!document || working || blocked) {
      return;
    }
    setWorking(true);
    try {
      const saved = await api.saveScript(document.name, code);
      settled(saved);
      if (!saved.error) {
        core.notify(t("已保存并重新加载"));
      }
    } catch (reason) {
      report(reason);
    } finally {
      setWorking(false);
    }
  };

  const run = async () => {
    if (!document || working || blocked) {
      return;
    }
    setWorking(true);
    try {
      const saved = await api.runScript(document.name, code);
      settled(saved);
    } catch (reason) {
      report(reason);
    } finally {
      setWorking(false);
    }
  };

  const choose = (name: string) => {
    void open(name).catch(report);
  };

  const pickImport = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) {
      setDialog({ kind: "import", file });
    }
  };

  const insert = (snippet: string) => {
    const at = editor.current?.selectionStart ?? code.length;
    const edit = insertLine(code, at, snippet);
    change(edit.text);
    requestAnimationFrame(() => {
      editor.current?.focus();
      editor.current?.setSelectionRange(edit.start, edit.end);
    });
  };

  const pickHelper = (helper: ScriptHelper) => {
    if (!document) {
      return;
    }
    if (helper.params.length) {
      setDialog({ kind: "helper", helper });
    } else {
      insert(helperCall(helper, {}));
    }
  };

  const startRecording = (loop: string, count: number) => {
    setDialog(null);
    setRecordLoop({ loop, count });
    api.startRecording().then(setRecording, report);
  };

  const stopRecording = () => {
    api.stopRecording(code, recordLoop.loop, recordLoop.count).then((result) => {
      setRecording(result);
      change(result.code);
    }, report);
  };

  const fileItems: MenuItem[] = [
    { label: t("新建脚本"), icon: "plus", disabled: Boolean(blocked), onSelect: () => setDialog({ kind: "create" }) },
    {
      label: t("复制脚本"),
      icon: "tasks",
      disabled: !document || Boolean(blocked),
      onSelect: () => {
        if (document) {
          api.copyScript(document.name).then(async (copy) => {
            await reload();
            lastOpen = copy.name;
            setDocument(copy);
            setCode(copy.code);
          }).catch(report);
        }
      },
    },
    { label: t("删除脚本"), icon: "x", disabled: !document || Boolean(blocked), onSelect: () => setDialog({ kind: "delete" }) },
    { label: t("导入脚本"), icon: "download", disabled: Boolean(blocked), onSelect: () => importInput.current?.click() },
    {
      label: t("导出脚本"),
      icon: "send",
      disabled: !entries?.length,
      onSelect: () => void api.exportOptions().then((options) => setDialog({ kind: "export", options }), report),
    },
  ];

  const groups = helperGroups(helpers, query);

  return (
    <div className="script-page">
      <input ref={importInput} type="file" accept=".okscript" hidden onChange={pickImport} />
      {entries && entries.length === 0 ? (
        <div className="tool-empty">
          <Icon name="logs" />
          <h1>{t("还没有脚本")}</h1>
          <p>{t("脚本是你自己写的任务，保存后会出现在任务列表里。")}</p>
          <div className="row">
            <button type="button" className="btn primary" disabled={Boolean(blocked)} onClick={() => setDialog({ kind: "create" })}><Icon name="plus" />{t("新建脚本")}</button>
            <button type="button" className="btn" disabled={Boolean(blocked)} onClick={() => importInput.current?.click()}><Icon name="download" />{t("导入脚本")}</button>
          </div>
        </div>
      ) : (
        <>
          <aside className="script-palette" aria-label={t("脚本助手")}>
            <label className="search">
              <Icon name="search" />
              <input type="search" value={query} placeholder={t("搜索方法")} aria-label={t("搜索方法")} onChange={(event) => setQuery(event.target.value)} />
            </label>
            <div className="palette-list">
              {groups.map((group) => {
                const expanded = Boolean(query.trim()) || !closed.has(group.name);
                return (
                  <section key={group.name}>
                    <button
                      type="button"
                      className="palette-group"
                      aria-expanded={expanded}
                      onClick={() => {
                        const next = new Set(closed);
                        if (next.has(group.name)) {
                          next.delete(group.name);
                        } else {
                          next.add(group.name);
                        }
                        setClosed(next);
                      }}
                    >
                      <Icon name={expanded ? "chev-d" : "chev-r"} />
                      {t(GROUP_LABELS[group.name] ?? group.name)}
                      <small>{group.helpers.length}</small>
                    </button>
                    {expanded && group.helpers.map((helper) => (
                      <button key={helper.name} type="button" className="palette-item" disabled={!document} title={helper.full_doc} onClick={() => pickHelper(helper)}>
                        {helper.name}
                      </button>
                    ))}
                  </section>
                );
              })}
              {helpers.length > 0 && groups.length === 0 && <p className="why">{t("没有符合的方法")}</p>}
            </div>
          </aside>
          <section className="script-main">
            <header className="script-bar">
              <select
                className="select"
                aria-label={t("选择脚本")}
                value={document?.name ?? ""}
                onChange={(event) => choose(event.target.value)}
              >
                {entries?.map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.task ? `${coreText(entry.task)} (${entry.name})` : entry.name}
                    {drafts.has(entry.name) ? " *" : ""}
                    {entry.error ? ` ${t("（有错误）")}` : ""}
                  </option>
                ))}
              </select>
              <Menu items={fileItems} label={t("文件")} />
              {dirty && <span className="chip warn">{t("未保存")}</span>}
              <span className="grow" />
              <a className="btn subtle" href={GUIDE_URL} target="_blank" rel="noreferrer"><Icon name="book" />{t("教程")}</a>
              {isRecording ? (
                <>
                  <span className="chip run" role="status">
                    {recording?.active ? t("录制中 · {count} 次点击", { count: recording.clicks }) : t("请切换到游戏窗口")}
                  </span>
                  <button type="button" className="btn stop" onClick={stopRecording}><Icon name="stop" />{t("停止录制")}</button>
                </>
              ) : (
                <>
                  <button type="button" className="btn" disabled={!document || Boolean(blocked)} onClick={() => setDialog({ kind: "record" })}><Icon name="target" />{t("录制")}</button>
                  <button type="button" className="btn" disabled={!document || !dirty || working || Boolean(blocked)} onClick={() => void save()}><Icon name="check" />{t("保存")}</button>
                  <button type="button" className="btn primary" disabled={!document || working || Boolean(blocked)} title={blocked ?? undefined} onClick={() => void run()}><Icon name="play" />{t("运行")}</button>
                </>
              )}
            </header>
            {blocked && <p className="why script-note">{t("暂时不能保存或运行脚本：{reason}", { reason: blocked })}</p>}
            {external && (
              <div className="ferr script-note" role="alert">
                <span>{t("文件在外部被修改了，而这里有未保存的内容。")}</span>
                <button type="button" className="btn sm" onClick={() => { if (document) { drafts.delete(document.name); void open(document.name); } }}>{t("载入磁盘上的版本")}</button>
                <button type="button" className="btn sm" onClick={() => setExternal(false)}>{t("保留我的修改")}</button>
              </div>
            )}
            {document?.error && <div className="ferr script-note" role="alert"><span className="mono">{coreText(document.error)}</span></div>}
            {document ? (
              <CodeEditor value={code} errorLine={document.error_line} editor={editor} onChange={change} onSave={() => void save()} />
            ) : (
              <div className="empty">{entries ? t("选择一个脚本开始编辑") : t("读取中…")}</div>
            )}
          </section>
        </>
      )}

      {dialog?.kind === "create" && (
        <CreateDialog
          onCancel={() => setDialog(null)}
          onCreated={(created) => {
            setDialog(null);
            void reload();
            lastOpen = created.name;
            setDocument(created);
            setCode(created.code);
          }}
        />
      )}
      {dialog?.kind === "helper" && (
        <HelperDialog helper={dialog.helper} onCancel={() => setDialog(null)} onInsert={(snippet) => { setDialog(null); insert(snippet); }} />
      )}
      {dialog?.kind === "delete" && document && (
        <ConfirmDialog
          title={t("删除「{name}」？", { name: coreText(document.task ?? document.name) })}
          confirm={t("删除")}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setDialog(null);
            const name = document.name;
            api.deleteScript(name).then(async () => {
              drafts.delete(name);
              lastOpen = null;
              setDocument(null);
              setCode("");
              const list = await reload();
              if (list[0]) {
                await open(list[0].name);
              }
            }).catch(report);
          }}
        >
          <p>{t("脚本文件会被删除，这个操作无法撤销。")}</p>
        </ConfirmDialog>
      )}
      {dialog?.kind === "export" && (
        <ExportDialog
          options={dialog.options}
          onCancel={() => setDialog(null)}
          onDone={(name) => {
            setDialog(null);
            core.notify(t("已导出到下载文件夹：{name}", { name }));
          }}
        />
      )}
      {dialog?.kind === "import" && (
        <ImportDialog
          file={dialog.file}
          onCancel={() => setDialog(null)}
          onDone={(restart) => {
            setDialog(null);
            core.notify(restart ? t("脚本更新已准备好，重启客户端后生效。") : t("脚本已导入，可以在任务列表里找到。"));
            void reload();
          }}
        />
      )}
      {dialog?.kind === "record" && <RecordDialog onCancel={() => setDialog(null)} onStart={startRecording} />}
    </div>
  );
}
