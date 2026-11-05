// The template markup editor's geometry and names, in image pixels. Follows ok-script's canvas:
// a box under three pixels is a slip of the mouse, and one name marks one template.
import type { TemplateBox, TemplateImage } from "./types.ts";

export const MIN_BOX = 3;
export const COPY_OFFSET = 20;

export type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export type Rect = [number, number, number, number];

export function fitScale(width: number, height: number, viewWidth: number, viewHeight: number): number {
  if (width <= 0 || height <= 0 || viewWidth <= 0 || viewHeight <= 0) {
    return 1;
  }
  return Math.min(viewWidth / width, viewHeight / height);
}

/** The box between two points, inside the image. */
export function rectBetween(a: [number, number], b: [number, number], width: number, height: number): Rect {
  const left = Math.max(0, Math.min(a[0], b[0]));
  const top = Math.max(0, Math.min(a[1], b[1]));
  const right = Math.min(width, Math.max(a[0], b[0]));
  const bottom = Math.min(height, Math.max(a[1], b[1]));
  return [Math.round(left), Math.round(top), Math.round(right - left), Math.round(bottom - top)];
}

export function moved(rect: Rect, dx: number, dy: number, width: number, height: number): Rect {
  return [
    Math.round(Math.min(Math.max(0, rect[0] + dx), Math.max(0, width - rect[2]))),
    Math.round(Math.min(Math.max(0, rect[1] + dy), Math.max(0, height - rect[3]))),
    rect[2],
    rect[3],
  ];
}

export function resized(rect: Rect, handle: Handle, dx: number, dy: number, width: number, height: number): Rect {
  let [left, top, right, bottom] = [rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]];
  if (handle.includes("w")) {
    left = Math.min(Math.max(0, left + dx), right - MIN_BOX);
  }
  if (handle.includes("e")) {
    right = Math.max(Math.min(width, right + dx), left + MIN_BOX);
  }
  if (handle.includes("n")) {
    top = Math.min(Math.max(0, top + dy), bottom - MIN_BOX);
  }
  if (handle.includes("s")) {
    bottom = Math.max(Math.min(height, bottom + dy), top + MIN_BOX);
  }
  return [Math.round(left), Math.round(top), Math.round(right - left), Math.round(bottom - top)];
}

/** The topmost box at a point, or -1. */
export function boxAt(boxes: TemplateBox[], x: number, y: number): number {
  for (let index = boxes.length - 1; index >= 0; index -= 1) {
    const [left, top, width, height] = boxes[index].bbox;
    if (x >= left && y >= top && x <= left + width && y <= top + height) {
      return index;
    }
  }
  return -1;
}

/** The name for a copy of a box: its own with _copy, numbered until it is free. */
export function copyName(name: string, used: Set<string>): string {
  let candidate = `${name}_copy`;
  for (let counter = 1; used.has(candidate); counter += 1) {
    candidate = `${name}_copy_${counter}`;
  }
  return candidate;
}

/** Why a name can't mark this box: empty, or marking another template; null when it can. */
export function nameProblem(
  name: string,
  original: string,
  boxes: TemplateBox[],
  images: TemplateImage[],
  current: string,
): { kind: "required" } | { kind: "taken"; owner: string } | null {
  const trimmed = name.trim();
  if (!trimmed) {
    return { kind: "required" };
  }
  if (trimmed === original) {
    return null;
  }
  if (boxes.some((box) => box.category === trimmed)) {
    return { kind: "taken", owner: current };
  }
  const owner = images.find((image) => image.name !== current && image.categories.includes(trimmed));
  return owner ? { kind: "taken", owner: owner.name } : null;
}

export function colorText(red: number, green: number, blue: number): string {
  return `rgb(${red}, ${green}, ${blue})`;
}

export function matchesTemplate(image: TemplateImage, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle || image.name.toLowerCase().includes(needle) || image.categories.some((name) => name.toLowerCase().includes(needle));
}
