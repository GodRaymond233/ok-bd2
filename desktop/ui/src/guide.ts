// The small part of Markdown that docs/guides is written in, turned into plain data so the page
// never renders HTML from a file: headings, paragraphs, lists, pictures, bold, italics, code and
// web links. Anything else stays as text.
export type Inline =
  | { kind: "text" | "strong" | "em" | "code"; text: string }
  | { kind: "link"; text: string; href: string };

export type Block =
  | { kind: "heading"; text: string; id: string }
  | { kind: "paragraph"; content: Inline[] }
  | { kind: "image"; alt: string; src: string }
  | { kind: "list"; ordered: boolean; start: number; items: Inline[][] };

const INLINE = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;
const IMAGE = /^\[?!\[([^\]]*)\]\(([^)\s]+)\)(?:\]\([^)\s]+\))?$/;
const HEADING = /^#{1,6}\s+(.+)$/;
const ORDERED = /^(\d+)\.\s+(.+)$/;
const BULLET = /^[-*]\s+(.+)$/;

export function parseInline(text: string): Inline[] {
  const parts: Inline[] = [];
  let at = 0;
  for (const match of text.matchAll(INLINE)) {
    if (match.index > at) {
      parts.push({ kind: "text", text: text.slice(at, match.index) });
    }
    const [whole, strong, em, code, label, href] = match;
    if (strong !== undefined) {
      parts.push({ kind: "strong", text: strong });
    } else if (em !== undefined) {
      parts.push({ kind: "em", text: em });
    } else if (code !== undefined) {
      parts.push({ kind: "code", text: code });
    } else if (/^https?:\/\//.test(href)) {
      parts.push({ kind: "link", text: label, href });
    } else {
      parts.push({ kind: "text", text: whole });
    }
    at = match.index + whole.length;
  }
  if (at < text.length) {
    parts.push({ kind: "text", text: text.slice(at) });
  }
  return parts;
}

/** Where the core serves a picture that a guide links as ../images/usage/name.png. */
export function imageUrl(source: string): string {
  return `/guide-images/${encodeURIComponent(source.split("/").pop() ?? "")}`;
}

export function parseGuide(markdown: string): Block[] {
  const blocks: Block[] = [];
  let sections = 0;
  for (const raw of markdown.split(/\r?\n/)) {
    const line = raw.trim();
    const last = blocks[blocks.length - 1];
    const heading = HEADING.exec(line);
    const image = IMAGE.exec(line);
    const ordered = ORDERED.exec(line);
    const bullet = BULLET.exec(line);
    if (!line) {
      continue;
    }
    if (heading) {
      sections += 1;
      blocks.push({ kind: "heading", text: heading[1], id: `guide-section-${sections}` });
    } else if (image) {
      blocks.push({ kind: "image", alt: image[1], src: imageUrl(image[2]) });
    } else if (ordered || bullet) {
      const content = parseInline(ordered ? ordered[2] : (bullet as RegExpExecArray)[1]);
      if (last?.kind === "list" && last.ordered === Boolean(ordered)) {
        last.items.push(content);
      } else {
        blocks.push({ kind: "list", ordered: Boolean(ordered), start: ordered ? Number(ordered[1]) : 1, items: [content] });
      }
    } else {
      blocks.push({ kind: "paragraph", content: parseInline(line) });
    }
  }
  return blocks;
}
