// Where ok-script's global settings appear in the client, and the theme kept in this
// browser profile. The core names the fields (src/desktop/settings_text.py).
import type { ConfigField, SettingsGroup } from "./types.ts";

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

/** The group's fields with these keys, in the given order. */
export function pickFields(group: SettingsGroup | undefined, keys: string[]): ConfigField[] {
  return keys.flatMap((key) => group?.fields.filter((field) => field.key === key) ?? []);
}

/** The group's fields except these keys, in the core's order. */
export function otherFields(group: SettingsGroup | undefined, keys: string[]): ConfigField[] {
  return group?.fields.filter((field) => !keys.includes(field.key)) ?? [];
}

export type Theme = "system" | "light" | "dark";
const THEME_KEY = "okbd2.theme";

export function storedTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_KEY);
    if (value === "light" || value === "dark") {
      return value;
    }
  } catch {
    // storage can be unavailable; follow the system
  }
  return "system";
}

export function applyTheme(theme: Theme): void {
  if (theme === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = theme;
  }
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // the theme still applies for this session
  }
}
