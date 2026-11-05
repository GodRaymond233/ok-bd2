// Texts for the device page and the game start shown in the run panel.
import { elapsedText } from "./format.ts";
import { coreText, t } from "./i18n.ts";
import type { CaptureUi, StarterWait } from "./types.ts";

export const AUTO_CAPTURE = "auto";

const CAPTURE_NOTES: Record<string, string> = {
  WGC: "Windows 图形捕获。窗口被挡住也能截图。",
  BitBlt_RenderFull: "WGC 不可用时的后备方式。",
  ForegroundBitBlt: "只能截最前面的窗口，运行时游戏不能被挡住。",
};

export interface CaptureChoice {
  id: string;
  label: string;
  note: string;
  selected: boolean;
}

/** "Automatic" first, then the methods the core offers, each with a short note. */
export function captureChoices(ui: Pick<CaptureUi, "capture_methods" | "capture_preference">): CaptureChoice[] {
  const order = ui.capture_methods.map((item) => item.label).join(" → ");
  return [
    {
      id: AUTO_CAPTURE,
      label: t("自动（推荐）"),
      note: t("按 {order} 的顺序，用第一个能截到画面的。", { order }),
      selected: !ui.capture_preference,
    },
    ...ui.capture_methods.map((item) => ({
      id: item.id,
      label: item.label,
      note: t(CAPTURE_NOTES[item.id] ?? ""),
      selected: ui.capture_preference === item.id,
    })),
  ];
}

export function sizeText(size: [number, number] | null | undefined): string {
  return size ? `${size[0]} × ${size[1]}` : "—";
}

/** "已等待 0:42 · 最多再等 1:18", or the Starter's own message while it updates. */
export function starterText(wait: StarterWait): string {
  const waited = t("已等待 {time}", { time: elapsedText(wait.waited) });
  if (wait.message) {
    return `${waited} · ${coreText(wait.message.split("\n")[0].replace(/，已等待 \d+ 秒/, ""))}`;
  }
  return wait.seconds_left > 0 ? `${waited} · ${t("最多再等 {time}", { time: elapsedText(wait.seconds_left) })}` : waited;
}

/** Share of the wait used, for the progress bar; null while the Starter updates. */
export function starterProgress(wait: StarterWait): number | null {
  if (wait.message || wait.seconds_left <= 0) {
    return null;
  }
  return Math.min(1, wait.waited / (wait.waited + wait.seconds_left));
}
