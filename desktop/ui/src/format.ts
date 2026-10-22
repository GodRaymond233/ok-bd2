// Time and duration text. Game times are Beijing time (UTC+8, no daylight saving), as in
// the Qt cards (src/ui/quest_cards.py), so the client shows the same "今天 09:12".
import { t } from "./i18n.ts";

const BEIJING_OFFSET_MS = 8 * 3600 * 1000;
const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

interface Civil {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  weekday: number;
  /** Days since the epoch in Beijing; differences give calendar-day distances. */
  serial: number;
}

function beijing(seconds: number): Civil {
  const shifted = seconds * 1000 + BEIJING_OFFSET_MS;
  const date = new Date(shifted);
  return {
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
    hour: date.getUTCHours(),
    minute: date.getUTCMinutes(),
    weekday: date.getUTCDay(),
    serial: Math.floor(shifted / 86_400_000),
  };
}

const pad = (value: number) => String(value).padStart(2, "0");

export function clockText(seconds: number): string {
  const moment = beijing(seconds);
  return `${pad(moment.hour)}:${pad(moment.minute)}`;
}

/** "今天 09:12" / "昨天 23:40" / "8月5日 18:02". */
export function whenText(seconds: number, now: number): string {
  const moment = beijing(seconds);
  const days = beijing(now).serial - moment.serial;
  const clock = clockText(seconds);
  if (days === 0) {
    return `${t("今天")} ${clock}`;
  }
  if (days === 1) {
    return `${t("昨天")} ${clock}`;
  }
  return `${moment.month}${t("月")}${moment.day}${t("日")} ${clock}`;
}

/** "10月22日 周四". */
export function dateText(now: number): string {
  const moment = beijing(now);
  return `${moment.month}${t("月")}${moment.day}${t("日")} ${t(WEEKDAYS[moment.weekday])}`;
}

/** "今日 04:00", "明日 08:00" or "周一 08:00" for an upcoming refresh. */
export function refreshText(seconds: number, now: number): string {
  const days = beijing(seconds).serial - beijing(now).serial;
  const label = days <= 0 ? t("今日") : days === 1 ? t("明日") : t(WEEKDAYS[beijing(seconds).weekday]);
  return `${label} ${clockText(seconds)}`;
}

/** "还有 5 小时 12 分"; never negative. */
export function untilText(seconds: number, now: number): string {
  const minutes = Math.max(0, Math.ceil((seconds - now) / 60));
  const hours = Math.floor(minutes / 60);
  return hours > 0
    ? `${t("还有")} ${hours} ${t("小时")} ${minutes % 60} ${t("分")}`
    : `${t("还有")} ${minutes} ${t("分")}`;
}

/** Same shape as the Qt cards: "1h 2m", "4m 10s", "45s"; empty when unknown. */
export function durationText(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) {
    return "";
  }
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  if (hours) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes) {
    return `${minutes}m ${pad(whole % 60)}s`;
  }
  return `${whole % 60}s`;
}

/** Stopwatch text for a run in progress: "01:12" or "1:02:03". */
export function elapsedText(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const rest = `${pad(Math.floor((whole % 3600) / 60))}:${pad(whole % 60)}`;
  return hours ? `${hours}:${rest}` : rest;
}
