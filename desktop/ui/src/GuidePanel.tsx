// A task's illustrated guide, in a panel beside the page: it stays open while the settings behind
// it are changed. A picture opens larger above everything, fitted to the window or at full size.
import { type UIEvent, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, errorText } from "./api.ts";
import { type Block, type Inline, parseGuide } from "./guide.ts";
import { coreText, t } from "./i18n.ts";
import type { Guide } from "./types.ts";
import { Icon } from "./ui.tsx";

function Text({ content }: { content: Inline[] }) {
  return (
    <>
      {content.map((part, index) => {
        switch (part.kind) {
          case "strong":
            return <b key={index}>{part.text}</b>;
          case "em":
            return <em key={index}>{part.text}</em>;
          case "code":
            return <code key={index}>{part.text}</code>;
          case "link":
            return <a key={index} href={part.href} target="_blank" rel="noreferrer">{part.text}</a>;
          default:
            return <span key={index}>{part.text}</span>;
        }
      })}
    </>
  );
}

function BlockView({ block, onImage }: { block: Block; onImage: (image: { src: string; alt: string }) => void }) {
  switch (block.kind) {
    case "heading":
      return <h3 id={block.id}>{block.text}</h3>;
    case "paragraph":
      return <p><Text content={block.content} /></p>;
    case "image":
      return (
        <figure>
          <button type="button" title={t("点击放大")} aria-label={`${t("放大图片")}：${block.alt}`} onClick={() => onImage(block)}>
            <img src={block.src} alt={block.alt} loading="lazy" />
          </button>
          {block.alt && <figcaption>{block.alt}</figcaption>}
        </figure>
      );
    case "list": {
      const items = block.items.map((item, index) => <li key={index}><Text content={item} /></li>);
      return block.ordered ? <ol start={block.start}>{items}</ol> : <ul>{items}</ul>;
    }
  }
}

function ImageViewer({ image, onClose }: { image: { src: string; alt: string }; onClose: () => void }) {
  const [actual, setActual] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <div className="guide-viewer" role="dialog" aria-modal="true" aria-label={image.alt || t("查看图片")} onMouseDown={onClose}>
      <div className="guide-viewer-bar" onMouseDown={(event) => event.stopPropagation()}>
        <span className="cap">{image.alt}</span>
        <span className="segpick">
          <button type="button" aria-pressed={!actual} onClick={() => setActual(false)}>{t("适应窗口")}</button>
          <button type="button" aria-pressed={actual} onClick={() => setActual(true)}>{t("原始大小")}</button>
        </span>
        <button type="button" className="btn subtle sm icon" title={t("关闭")} aria-label={t("关闭图片")} onClick={onClose}>
          <Icon name="x" />
        </button>
      </div>
      <div className={actual ? "guide-viewer-body actual" : "guide-viewer-body"}>
        {failed
          ? <div className="ferr" role="alert">{t("图片未找到，请重新安装当前版本。")}</div>
          : <img src={image.src} alt={image.alt} onMouseDown={(event) => event.stopPropagation()} onError={() => setFailed(true)} />}
      </div>
    </div>
  );
}

export function GuidePanel({ id, onClose }: { id: string; onClose: () => void }) {
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [image, setImage] = useState<{ src: string; alt: string } | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    api.guide(id).then(
      (value) => !cancelled && setGuide(value),
      (reason: unknown) => !cancelled && setError(errorText(reason)),
    );
    closeButton.current?.focus();
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (image) {
          setImage(null);
        } else {
          onClose();
        }
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [image, onClose]);

  const blocks = useMemo(() => (guide ? parseGuide(guide.markdown) : []), [guide]);
  const sections = blocks.flatMap((block) => (block.kind === "heading" ? [block] : []));
  const current = active ?? sections[0]?.id;

  const follow = (event: UIEvent<HTMLDivElement>) => {
    const host = event.currentTarget;
    if (host.scrollTop + host.clientHeight >= host.scrollHeight - 2) {
      setActive(sections[sections.length - 1]?.id ?? null);
      return;
    }
    const top = host.getBoundingClientRect().top;
    let reached = sections[0]?.id ?? null;
    for (const section of sections) {
      const heading = host.querySelector(`#${section.id}`);
      if (heading && heading.getBoundingClientRect().top - top > 40) {
        break;
      }
      reached = section.id;
    }
    setActive(reached);
  };

  // On the document itself: the page's own layers would otherwise cover it.
  return createPortal(
    <>
      <aside className="guide-panel" role="complementary" aria-label={guide ? `${coreText(guide.title)} ${t("使用说明")}` : t("使用说明")}>
        <header>
          <div className="tt">
            <small>{t("任务指南")}</small>
            <h2>{guide ? coreText(guide.title) : t("使用说明")}</h2>
          </div>
          <button type="button" className="btn subtle sm icon" ref={closeButton} title={t("关闭")} aria-label={t("关闭说明")} onClick={onClose}>
            <Icon name="x" />
          </button>
        </header>
        {sections.length > 1 && (
          <nav aria-label={t("本页目录")}>
            {sections.map((section) => (
              <button
                key={section.id}
                type="button"
                aria-current={section.id === current}
                onClick={() => {
                  setActive(section.id);
                  body.current?.querySelector(`#${section.id}`)?.scrollIntoView({ block: "start" });
                }}
              >
                {section.text}
              </button>
            ))}
          </nav>
        )}
        <div className="guide-body" ref={body} onScroll={follow} tabIndex={0}>
          {error && <div className="ferr" role="alert">{error}</div>}
          {!guide && !error && <p role="status">{t("读取中…")}</p>}
          {blocks.map((block, index) => <BlockView key={index} block={block} onImage={setImage} />)}
        </div>
      </aside>
      {image && <ImageViewer image={image} onClose={() => setImage(null)} />}
    </>,
    document.body,
  );
}
