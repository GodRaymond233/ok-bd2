// Where ok-script's global settings appear in the client, and what the pages remember.
// The core names the fields (src/desktop/settings_text.py).
import type { ConfigField, SettingsGroup, ShellDesktop, Theme } from "./types.ts";

/** Same pace as the shell's own retry (DESKTOP_RETRY_MS in desktop/shell/src/main.ts). */
export const DESKTOP_RETRY_MS = 5000;

export function needsDesktopRetry(desktop: ShellDesktop | null, requestError: string | null): boolean {
  return Boolean(requestError || desktop?.syncError || desktop?.hotkeyError);
}

export const BASIC = "Basic Options";
export const NOTIFICATION = "Notification";

/** Game start and exit options shown on the device page (user decision 2026-10-23). */
export const DEVICE_KEYS = [
  "Auto Start Game When App Starts",
  "Kill Launcher After Start",
  "Exit App when Game Exits",
  "Mute Game while in Background",
];
export const AUTO_RESIZE_KEY = "Auto Resize Game Window";
/** Tray and global hotkey belong to the shell. */
export const SHELL_KEYS = ["Minimize Window to System Tray when Closing", "Start/Stop"];
export const SYSTEM_NOTIFICATION_KEY = "System Notification";

/** Channel switches the core can send a test message through (TEST_CHANNELS). */
export const TEST_CHANNELS: Record<string, string> = {
  "Discord Notification": "discord",
  "Telegram Notification": "telegram",
  "Enterprise WeChat Webhook Notification": "wecom",
  "QQ Bot API Notification": "qqbot",
};

export function findGroup(groups: SettingsGroup[], name: string): SettingsGroup | undefined {
  return groups.find((group) => group.name === name);
}

/** Changes only when the shell has something new to apply; a refresh of the settings does not. */
export function shellSettingsKey(groups: SettingsGroup[] | null): string | null {
  const basic = findGroup(groups ?? [], BASIC);
  return basic ? JSON.stringify(pickFields(basic, SHELL_KEYS).map((field) => field.value)) : null;
}

/** The group's fields with these keys, in the given order. */
export function pickFields(group: SettingsGroup | undefined, keys: string[]): ConfigField[] {
  return keys.flatMap((key) => group?.fields.filter((field) => field.key === key) ?? []);
}

/** The group's fields except these keys, in the core's order. */
export function otherFields(group: SettingsGroup | undefined, keys: string[]): ConfigField[] {
  return group?.fields.filter((field) => !keys.includes(field.key)) ?? [];
}

// The shell keeps these preferences: localStorage is lost when the core restarts on another
// port. A page opened without the shell (a dev core in a browser) keeps them in localStorage.
const THEME_KEY = "okbd2.theme";
const DOCK_KEY = "okbd2.dockExpanded";
const RAIL_KEY = "okbd2.railWidth";
export const RAIL_WIDTH = { default: 216, min: 160, max: 360 } as const;

export function maximumRailWidth(viewportWidth: number): number {
  // Keep room for the task list and its detail at the smallest two-column layout.
  return Math.min(RAIL_WIDTH.max, Math.max(RAIL_WIDTH.min, viewportWidth - 640));
}

export function clampRailWidth(width: number, maximum: number = RAIL_WIDTH.max): number {
  return Math.max(RAIL_WIDTH.min, Math.min(maximum, Math.round(width)));
}

function browserValue(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // storage can be unavailable
  }
}

function rememberInBrowser(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // the choice still applies to this page
  }
}

export function storedTheme(): Theme {
  const shell = window.okbd2Shell;
  const value = shell ? shell.uiPreferences().theme : browserValue(THEME_KEY);
  return value === "light" || value === "dark" ? value : "system";
}

export function applyTheme(theme: Theme): void {
  if (theme === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = theme;
  }
  const shell = window.okbd2Shell;
  if (shell) {
    void shell.setUiPreferences({ theme }).catch(() => undefined);
  } else {
    rememberInBrowser(THEME_KEY, theme);
  }
}

/** The run panel state the user last chose; null before the first choice. */
export function storedDockExpanded(): boolean | null {
  const shell = window.okbd2Shell;
  if (shell) {
    return shell.uiPreferences().dockExpanded ?? null;
  }
  const value = browserValue(DOCK_KEY);
  return value === null ? null : value === "1";
}

export function rememberDockExpanded(dockExpanded: boolean): void {
  const shell = window.okbd2Shell;
  if (shell) {
    void shell.setUiPreferences({ dockExpanded }).catch(() => undefined);
  } else {
    rememberInBrowser(DOCK_KEY, dockExpanded ? "1" : "0");
  }
}

export function storedRailWidth(): number {
  const shell = window.okbd2Shell;
  const value = shell ? shell.uiPreferences().railWidth : Number(browserValue(RAIL_KEY));
  return typeof value === "number" && Number.isInteger(value) && value >= RAIL_WIDTH.min && value <= RAIL_WIDTH.max
    ? value : RAIL_WIDTH.default;
}

export function rememberRailWidth(railWidth: number): void {
  const shell = window.okbd2Shell;
  if (shell) {
    void shell.setUiPreferences({ railWidth }).catch(() => undefined);
  } else {
    rememberInBrowser(RAIL_KEY, String(railWidth));
  }
}
