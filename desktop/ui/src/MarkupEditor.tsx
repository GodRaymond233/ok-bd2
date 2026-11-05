// Marks named boxes on a template image, in a layer over the whole window as ok-script's markup
// window is. Every finished change saves at once; the wheel zooms around the pointer.
import { type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type WheelEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, errorText } from "./api.ts";
import { t } from "./i18n.ts";
import {
  boxAt,
  colorText,
  COPY_OFFSET,
  copyName,
  fitScale,
  type Handle,
  MIN_BOX,
  moved,
  nameProblem,
  type Rect,
  rectBetween,
  resized,
} from "./markup.ts";
import type { TemplateBox, TemplateImage, TemplateMarkup } from "./types.ts";
import { ConfirmDialog, Icon } from "./ui.tsx";

const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const MAX_SCALE = 50;

type Drag =
  | { kind: "draw"; start: [number, number]; rect: Rect }
  | { kind: "move"; index: number; start: [number, number]; rect: Rect; changed: boolean }
  | { kind: "resize"; index: number; handle: Handle; start: [number, number]; rect: Rect };

function BoxDialog({ box, boxes, images, image, onDone, onCancel }: {
  box: TemplateBox;
  boxes: TemplateBox[];
  images: TemplateImage[];
  image: string;
  onDone: (box: TemplateBox) => void;
  onCancel: () => void;
}) {
  const [category, setCategory] = useState(box.category);
  const [values, setValues] = useState(box.bbox.map((value) => String(Math.round(value))));
  const problem = nameProblem(category, box.category, boxes, images, image);
  const save = () => {
    if (!problem) {
      const [x, y, width, height] = values.map((value) => Math.max(0, Number(value) || 0));
      onDone({ ...box, category: category.trim(), bbox: [x, y, Math.max(MIN_BOX, width), Math.max(MIN_BOX, height)] });
    }
  };
  const labels = ["X", "Y", t("宽"), t("高")];
  return (
    <ConfirmDialog title={t("标注框")} confirm={t("确定")} disabled={Boolean(problem)} onConfirm={save} onCancel={onCancel}>
      <div className="form-stack">
        <label>
          {t("名称")}
          <input className="input" value={category} placeholder={t("标注名称")} autoFocus onChange={(event) => setCategory(event.target.value)} />
          {problem?.kind === "required" && category !== box.category && <small className="danger-text">{t("请填写名称")}</small>}
          {problem?.kind === "taken" && <small className="danger-text">{t("这个名称已用在「{owner}」", { owner: problem.owner })}</small>}
        </label>
        <div className="box-numbers">
          {labels.map((label, index) => (
            <label key={label}>
              {label}
              <input
                className="input"
                type="number"
                min={0}
                value={values[index]}
                onChange={(event) => setValues(values.map((value, at) => (at === index ? event.target.value : value)))}
              />
            </label>
          ))}
        </div>
      </div>
    </ConfirmDialog>
  );
}

export function MarkupEditor({ images, name, onNavigate, onChanged, onClose }: {
  images: TemplateImage[];
  name: string;
  onNavigate: (name: string) => void;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [markup, setMarkup] = useState<TemplateMarkup | null>(null);
  const [boxes, setBoxes] = useState<TemplateBox[]>([]);
  const [selected, setSelected] = useState(-1);
  const [drawing, setDrawing] = useState(false);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [editing, setEditing] = useState<{ box: TemplateBox; index: number | null } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [scale, setScale] = useState(1);
  const [fit, setFit] = useState(1);
  const [color, setColor] = useState<string | null>(null);
  const [pointer, setPointer] = useState<[number, number] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const pixels = useRef<CanvasRenderingContext2D | null>(null);
  const fitted = useRef(0);

  const position = images.findIndex((image) => image.name === name);
  const previous = position > 0 ? images[position - 1].name : null;
  const next = position >= 0 && position < images.length - 1 ? images[position + 1].name : null;

  useEffect(() => {
    let cancelled = false;
    setMarkup(null);
    setSelected(-1);
    setDrawing(false);
    setDrag(null);
    setError(null);
    pixels.current = null;
    api.templateMarkup(name).then(
      (loaded) => {
        if (!cancelled) {
          setMarkup(loaded);
          setBoxes(loaded.annotations);
        }
      },
      (reason: unknown) => !cancelled && setError(errorText(reason)),
    );
    return () => {
      cancelled = true;
    };
  }, [name]);

  useLayoutEffect(() => {
    const host = stage.current;
    if (!host || !markup) {
      return;
    }
    const measure = () => {
      const value = fitScale(markup.width, markup.height, host.clientWidth - 24, host.clientHeight - 24);
      const before = fitted.current;
      fitted.current = value;
      setFit(value);
      // Stays fitted to the window until the wheel zooms in.
      setScale((current) => (!before || current <= before ? value : current));
    };
    fitted.current = 0;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, [markup]);

  const persist = useCallback((list: TemplateBox[]) => {
    setBoxes(list);
    setSaving(true);
    api.saveTemplateMarkup(name, list).then(
      () => {
        setError(null);
        onChanged();
      },
      (reason: unknown) => setError(errorText(reason)),
    ).finally(() => setSaving(false));
  }, [name, onChanged]);

  // The layer takes the keys the page below would otherwise see, as the markup window does.
  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if (editing || confirmDelete || (event.target instanceof HTMLInputElement)) {
        return;
      }
      if (event.key === "Escape") {
        if (drawing) {
          setDrawing(false);
        } else {
          onClose();
        }
      } else if (event.key === "Delete" && selected >= 0) {
        setConfirmDelete(true);
      } else if (event.key === "ArrowLeft" && previous) {
        onNavigate(previous);
      } else if (event.key === "ArrowRight" && next) {
        onNavigate(next);
      }
    };
    document.addEventListener("keydown", keys);
    return () => document.removeEventListener("keydown", keys);
  }, [editing, confirmDelete, drawing, selected, previous, next, onClose, onNavigate]);

  const imagePoint = (event: { clientX: number; clientY: number }, element: Element): [number, number] => {
    const area = element.getBoundingClientRect();
    return [(event.clientX - area.left) / scale, (event.clientY - area.top) / scale];
  };

  const down = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !markup) {
      return;
    }
    const point = imagePoint(event, event.currentTarget);
    if (drawing) {
      event.currentTarget.setPointerCapture(event.pointerId);
      setDrag({ kind: "draw", start: point, rect: [point[0], point[1], 0, 0] });
    } else {
      setSelected(-1);
    }
  };

  const startBox = (event: ReactPointerEvent, index: number, handle?: Handle) => {
    if (event.button !== 0 || drawing) {
      return;
    }
    event.stopPropagation();
    const canvas = event.currentTarget.closest(".markup-canvas");
    if (!canvas) {
      return;
    }
    (canvas as HTMLElement).setPointerCapture(event.pointerId);
    setSelected(index);
    const start = imagePoint(event, canvas);
    const rect = boxes[index].bbox;
    setDrag(handle ? { kind: "resize", index, handle, start, rect } : { kind: "move", index, start, rect, changed: false });
  };

  const readColor = (point: [number, number], target: HTMLImageElement | null) => {
    if (!markup || !target) {
      return;
    }
    if (!pixels.current) {
      const canvas = document.createElement("canvas");
      canvas.width = target.naturalWidth;
      canvas.height = target.naturalHeight;
      pixels.current = canvas.getContext("2d", { willReadFrequently: true });
      pixels.current?.drawImage(target, 0, 0);
    }
    const data = pixels.current?.getImageData(Math.floor(point[0]), Math.floor(point[1]), 1, 1).data;
    if (data) {
      setColor(colorText(data[0], data[1], data[2]));
    }
  };

  const over = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!markup) {
      return;
    }
    const point = imagePoint(event, event.currentTarget);
    const inside = point[0] >= 0 && point[1] >= 0 && point[0] < markup.width && point[1] < markup.height;
    setPointer(inside ? [Math.floor(point[0]), Math.floor(point[1])] : null);
    if (inside) {
      readColor(point, event.currentTarget.querySelector("img"));
    }
    if (!drag) {
      return;
    }
    const dx = point[0] - drag.start[0];
    const dy = point[1] - drag.start[1];
    if (drag.kind === "draw") {
      setDrag({ ...drag, rect: rectBetween(drag.start, point, markup.width, markup.height) });
    } else if (drag.kind === "move") {
      const rect = moved(drag.rect, dx, dy, markup.width, markup.height);
      if (dx || dy) {
        setDrag({ ...drag, changed: true });
      }
      setBoxes(boxes.map((box, index) => (index === drag.index ? { ...box, bbox: rect } : box)));
    } else {
      const rect = resized(drag.rect, drag.handle, dx, dy, markup.width, markup.height);
      setBoxes(boxes.map((box, index) => (index === drag.index ? { ...box, bbox: rect } : box)));
    }
  };

  const up = () => {
    if (!drag) {
      return;
    }
    setDrag(null);
    if (drag.kind === "draw") {
      setDrawing(false);
      if (drag.rect[2] >= MIN_BOX && drag.rect[3] >= MIN_BOX) {
        setEditing({ box: { category: "", bbox: drag.rect }, index: null });
      }
    } else if (drag.kind === "resize" || drag.changed) {
      persist(boxes);
    }
  };

  const zoom = (event: WheelEvent<HTMLDivElement>) => {
    const host = stage.current;
    if (!host) {
      return;
    }
    const value = Math.min(MAX_SCALE, Math.max(fit, scale * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
    const area = host.getBoundingClientRect();
    const canvas = host.querySelector(".markup-canvas")?.getBoundingClientRect();
    if (canvas) {
      const x = (event.clientX - canvas.left) / scale;
      const y = (event.clientY - canvas.top) / scale;
      const offsetX = event.clientX - area.left;
      const offsetY = event.clientY - area.top;
      setScale(value);
      requestAnimationFrame(() => {
        host.scrollLeft = x * value - offsetX;
        host.scrollTop = y * value - offsetY;
      });
    }
  };

  const copySelected = () => {
    const box = boxes[selected];
    if (box && markup) {
      const used = new Set([...boxes.map((item) => item.category), ...images.flatMap((image) => image.categories)]);
      persist([...boxes, { category: copyName(box.category, used), bbox: moved(box.bbox, COPY_OFFSET, COPY_OFFSET, markup.width, markup.height) }]);
    }
  };

  const copyColor = (event: ReactMouseEvent) => {
    event.preventDefault();
    if (color) {
      void navigator.clipboard?.writeText(color).catch(() => undefined);
    }
  };

  const draftRect = drag?.kind === "draw" ? drag.rect : null;

  return createPortal(
    <div className="markup" role="dialog" aria-modal="true" aria-label={t("标注模板")}>
      <header className="markup-bar">
        <button type="button" className="btn subtle icon" disabled={!previous} title={t("上一张")} aria-label={t("上一张")} onClick={() => previous && onNavigate(previous)}><Icon name="chev-l" /></button>
        <span className="nm">{name}<small>{`${position + 1} / ${images.length}`}</small></span>
        <button type="button" className="btn subtle icon" disabled={!next} title={t("下一张")} aria-label={t("下一张")} onClick={() => next && onNavigate(next)}><Icon name="chev-r" /></button>
        <span className="sep" />
        <button type="button" className={drawing ? "btn primary" : "btn"} aria-pressed={drawing} onClick={() => setDrawing(!drawing)}><Icon name="plus" />{t("画框")}</button>
        <button type="button" className="btn" disabled={selected < 0} onClick={() => setEditing({ box: boxes[selected], index: selected })}><Icon name="tag" />{t("修改")}</button>
        <button type="button" className="btn" disabled={selected < 0} onClick={copySelected}><Icon name="tasks" />{t("复制")}</button>
        <button type="button" className="btn" disabled={selected < 0} onClick={() => setConfirmDelete(true)}><Icon name="x" />{t("删除")}</button>
        <span className="grow" />
        <span className="why" role="status">{saving ? t("正在保存…") : t("修改会自动保存")}</span>
        <button type="button" className="btn subtle icon" title={t("关闭")} aria-label={t("关闭标注")} onClick={onClose}><Icon name="x" /></button>
      </header>
      {error && <div className="ferr markup-error" role="alert">{error}</div>}
      <div className="markup-stage" ref={stage} onWheel={zoom} onContextMenu={copyColor}>
        {markup ? (
          <div
            className={`markup-canvas${drawing ? " drawing" : ""}${scale > 1 ? " zoomed" : ""}`}
            style={{ width: markup.width * scale, height: markup.height * scale }}
            onPointerDown={down}
            onPointerMove={over}
            onPointerUp={up}
            onPointerCancel={up}
            onDoubleClick={(event) => {
              // On the canvas: it holds the pointer while a box is pressed, so the box never hears it.
              const point = imagePoint(event, event.currentTarget);
              const index = drawing ? -1 : boxAt(boxes, point[0], point[1]);
              if (index >= 0) {
                setSelected(index);
                setEditing({ box: boxes[index], index });
              }
            }}
          >
            <img src={markup.url} alt={name} draggable={false} />
            {boxes.map((box, index) => (
              <div
                key={index}
                className={index === selected ? "markup-box selected" : "markup-box"}
                style={{ left: box.bbox[0] * scale, top: box.bbox[1] * scale, width: box.bbox[2] * scale, height: box.bbox[3] * scale }}
                onPointerDown={(event) => startBox(event, index)}
              >
                <span className="lbl">{box.category}</span>
                {index === selected && HANDLES.map((handle) => (
                  <i key={handle} className={`hd ${handle}`} onPointerDown={(event) => startBox(event, index, handle)} />
                ))}
              </div>
            ))}
            {draftRect && (
              <div className="markup-box draft" style={{ left: draftRect[0] * scale, top: draftRect[1] * scale, width: draftRect[2] * scale, height: draftRect[3] * scale }} />
            )}
          </div>
        ) : (
          !error && <div className="empty">{t("读取中…")}</div>
        )}
      </div>
      <footer className="markup-foot">
        <span>{drawing ? t("按住左键拖出一个框") : t("单击选中，拖动移动，双击修改；滚轮缩放")}</span>
        <span className="grow" />
        {pointer && <span className="mono">{`${pointer[0]}, ${pointer[1]}`}</span>}
        {color && <span className="mono swatch" title={t("右键复制颜色")}><i style={{ background: color }} />{color}</span>}
        <span className="mono">{`${Math.round(scale * 100)}%`}</span>
      </footer>
      {editing && (
        <BoxDialog
          box={editing.box}
          boxes={boxes}
          images={images}
          image={name}
          onCancel={() => setEditing(null)}
          onDone={(box) => {
            const index = editing.index;
            setEditing(null);
            if (index === null) {
              persist([...boxes, box]);
              setSelected(boxes.length);
            } else {
              persist(boxes.map((item, at) => (at === index ? box : item)));
            }
          }}
        />
      )}
      {confirmDelete && boxes[selected] && (
        <ConfirmDialog
          title={t("删除「{name}」？", { name: boxes[selected].category })}
          confirm={t("删除")}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => {
            setConfirmDelete(false);
            persist(boxes.filter((_, index) => index !== selected));
            setSelected(-1);
          }}
        >
          <p>{t("只删除这个标注框，图片会保留。")}</p>
        </ConfirmDialog>
      )}
    </div>,
    document.body,
  );
}
