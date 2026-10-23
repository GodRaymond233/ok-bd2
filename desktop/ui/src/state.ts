// Pure helpers for the capture summary and notices (tested with node --test).
import { t } from "./i18n.ts";
import type { CaptureUi, EventPayload } from "./types.ts";

export function captureSummary(
  ui: Pick<CaptureUi, "capture_methods" | "capture_preference" | "capture_in_use">,
): string {
  const label = (id: string) => ui.capture_methods.find((item) => item.id === id)?.label ?? id;
  const inUse = ui.capture_in_use ? label(ui.capture_in_use) : t("未使用");
  if (!ui.capture_preference) {
    return `${inUse}（${t("自动选择")}）`;
  }
  if (ui.capture_preference === ui.capture_in_use) {
    return inUse;
  }
  return `${inUse}（${t("设置为")} ${label(ui.capture_preference)}）`;
}

export interface Notice {
  text: string;
  error: boolean;
}

/**
 * What to tell the user about an event: ok-script's notifications (message, title, error,
 * tray, tab, params) and a failed game start (starting_emulator: done, error, seconds).
 */
export function noticeFromEvent(payload: EventPayload): Notice | null {
  const args = Array.isArray(payload.args) ? payload.args : [];
  if (payload.event === "starting_emulator" && typeof args[1] === "string" && args[1]) {
    return { text: args[1], error: true };
  }
  if (payload.event === "notification" && typeof args[0] === "string" && args[0]) {
    const params = args[5] && typeof args[5] === "object" ? (args[5] as Record<string, unknown>) : {};
    const text = args[0].replace(/\{(\w+)\}/g, (whole, key: string) => (key in params ? String(params[key]) : whole));
    const title = typeof args[1] === "string" && args[1] ? `${args[1]}：` : "";
    return { text: `${title}${text}`, error: Boolean(args[2]) };
  }
  return null;
}
