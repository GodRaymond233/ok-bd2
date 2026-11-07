// Template images: screenshots of the game and the named boxes marked on them, which scripts
// find by name once saved.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "./api.ts";
import type { Core } from "./App.tsx";
import { t } from "./i18n.ts";
import { MarkupEditor } from "./MarkupEditor.tsx";
import { matchesTemplate } from "./markup.ts";
import type { TemplateImage, TemplatesInfo } from "./types.ts";
import { ConfirmDialog, Icon, PageHead } from "./ui.tsx";

function SaveDialog({ info, onDone, onCancel }: { info: TemplatesInfo; onDone: (folder: string) => void; onCancel: () => void }) {
  const [format, setFormat] = useState("split");
  const [destination, setDestination] = useState("tasks");
  const [generate, setGenerate] = useState(info.generate_label_enum);
  const [enumPath, setEnumPath] = useState(info.enum_path);
  const [error, setError] = useState<string | null>(null);
  const save = () => {
    api.saveTemplates({ format, destination, generate_label_enum: generate, enum_path: enumPath }).then(
      (result) => onDone(result.folder),
      (reason: unknown) => setError(errorText(reason)),
    );
  };
  return (
    <ConfirmDialog title={t("保存模板")} confirm={t("保存")} onConfirm={save} onCancel={onCancel}>
      <div className="form-stack">
        <fieldset>
          <legend>{t("生成方式")}</legend>
          <label className="check"><input type="radio" name="format" checked={format === "split"} onChange={() => setFormat("split")} />{t("拆分为单独的图片（推荐）")}</label>
          <label className="check"><input type="radio" name="format" checked={format === "compress"} onChange={() => setFormat("compress")} />{t("压缩到一个文件（旧方式）")}</label>
        </fieldset>
        <fieldset>
          <legend>{t("保存到")}</legend>
          <label className="check"><input type="radio" name="destination" checked={destination === "tasks"} onChange={() => setDestination("tasks")} />{t("ok_tasks/assets（自定义脚本）")}</label>
          {info.debug && <label className="check"><input type="radio" name="destination" checked={destination === "assets"} onChange={() => setDestination("assets")} />{t("assets（程序自带）")}</label>}
        </fieldset>
        <label className="check"><input type="checkbox" checked={generate} onChange={(event) => setGenerate(event.target.checked)} />{t("生成标注名称的枚举文件")}</label>
        {generate && (
          <label>
            {t("枚举文件路径")}
            <input className="input mono" value={enumPath} placeholder="ok_tasks/LabelEnum.py" onChange={(event) => setEnumPath(event.target.value)} />
            <small>{t("路径相对于程序所在文件夹。")}</small>
          </label>
        )}
        {error && <div className="ferr" role="alert">{error}</div>}
      </div>
    </ConfirmDialog>
  );
}

export function TemplatesPage({ core }: { core: Core }) {
  const [images, setImages] = useState<TemplateImage[] | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [marking, setMarking] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [dialog, setDialog] = useState<"delete" | "save" | null>(null);
  const [info, setInfo] = useState<TemplatesInfo | null>(null);
  const grid = useRef<HTMLDivElement>(null);

  const report = useCallback((reason: unknown) => {
    core.act(() => Promise.reject(reason instanceof Error ? reason : new Error(String(reason))));
  }, [core]);

  const load = useCallback(() => api.templates().then(setImages, report), [report]);

  useEffect(() => {
    void load();
    api.templatesInfo().then(setInfo, () => undefined);
    // Once, on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const capture = () => {
    setCapturing(true);
    api.captureTemplate().then(
      (list) => {
        setImages(list);
        setSelected(list[0]?.name ?? null);
        core.notify(t("已截图：{name}", { name: list[0]?.name ?? "" }));
      },
      report,
    ).finally(() => setCapturing(false));
  };

  const shown = images?.filter((image) => matchesTemplate(image, query)) ?? [];
  const current = images?.find((image) => image.name === selected) ?? null;

  return (
    <div className="page templates-page">
      <PageHead title={t("模板")} note={t("从游戏画面截图，框出要识别的部分并命名，保存后脚本就可以按名称查找它们。")}>
          <button type="button" className="btn primary" disabled={capturing} onClick={capture}><Icon name="camera" />{t("截取画面")}</button>
          <button type="button" className="btn" disabled={!current} onClick={() => setMarking(selected)}><Icon name="tag" />{t("标注")}</button>
          <button type="button" className="btn" disabled={!current} onClick={() => setDialog("delete")}><Icon name="x" />{t("删除")}</button>
          <button type="button" className="btn" disabled={!images?.length || Boolean(core.startBlocked)} title={core.startBlocked ?? undefined} onClick={() => setDialog("save")}><Icon name="check" />{t("保存")}</button>
      </PageHead>
      {images && images.length > 0 && (
        <label className="search template-search">
          <Icon name="search" />
          <input type="search" value={query} placeholder={t("按图片名或标注名搜索")} aria-label={t("按图片名或标注名搜索")} onChange={(event) => setQuery(event.target.value)} />
        </label>
      )}
      {images === null ? (
        <div className="empty">{t("读取中…")}</div>
      ) : images.length === 0 ? (
        <div className="tool-empty">
          <Icon name="camera" />
          <h2>{t("还没有模板")}</h2>
          <p>{t("连接游戏窗口后截一张图，就可以开始标注。")}</p>
          <button type="button" className="btn primary" disabled={capturing} onClick={capture}><Icon name="camera" />{t("截取画面")}</button>
        </div>
      ) : shown.length === 0 ? (
        <div className="empty">{t("没有符合的模板")}</div>
      ) : (
        <div className="template-grid" ref={grid}>
          {shown.map((image) => (
            <button
              key={image.name}
              type="button"
              className="template-card"
              aria-pressed={image.name === selected}
              title={t("双击打开标注")}
              onClick={() => setSelected(image.name)}
              onDoubleClick={() => setMarking(image.name)}
            >
              <img src={`${image.url}?v=${image.modified}`} alt="" loading="lazy" />
              <span className="nm">{image.name}</span>
              <span className="cats">
                {image.categories.length ? image.categories.join("、") : t("未标注")}
              </span>
            </button>
          ))}
        </div>
      )}

      {marking && images && (
        <MarkupEditor
          images={images}
          name={marking}
          onNavigate={(name) => {
            setMarking(name);
            setSelected(name);
          }}
          onChanged={() => void load()}
          onClose={() => setMarking(null)}
        />
      )}
      {dialog === "delete" && current && (
        <ConfirmDialog
          title={t("删除「{name}」？", { name: current.name })}
          confirm={t("删除")}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            setDialog(null);
            api.deleteTemplate(current.name).then(() => {
              setSelected(null);
              void load();
            }, report);
          }}
        >
          <p>{t("图片和它的标注都会被删除，这个操作无法撤销。")}</p>
        </ConfirmDialog>
      )}
      {dialog === "save" && info && (
        <SaveDialog
          info={info}
          onCancel={() => setDialog(null)}
          onDone={(folder) => {
            setDialog(null);
            core.notify(t("已保存到：{folder}", { folder }));
          }}
        />
      )}
    </div>
  );
}
