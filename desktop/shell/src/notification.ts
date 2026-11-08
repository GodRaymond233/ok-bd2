// What a system notification says, and the name and icon Windows shows for its sender.
// Electron-free so it can be tested with plain Node; main.ts hands the results to the tray
// balloon (or Notification) and to reg.exe.

export const APP_NAME = "ok-bd2";

// A task name, not a sentence: anything longer stays in the body.
const MAX_LEAD = 24;

export interface NotificationText {
  title: string;
  body: string;
}

/**
 * Task results arrive without a title and read "自动登录：……流程结束。". The part before the
 * first full-width colon becomes the title, so the notification does not repeat the app name
 * that Windows already shows in its header.
 */
export function notificationText(title: string, message: string): NotificationText {
  const given = title.trim();
  if (given && given !== APP_NAME) return { title: given, body: message };
  const colon = message.indexOf("：");
  if (colon > 0) {
    const lead = message.slice(0, colon).trim();
    const rest = message.slice(colon + 1).trim();
    if (lead && rest && lead.length <= MAX_LEAD && !/[\r\n]/.test(lead)) {
      return { title: lead, body: rest };
    }
  }
  return { title: APP_NAME, body: message };
}

/**
 * reg.exe arguments that give the AppUserModelID a display name and icon. Without them Windows
 * labels the app's notifications with the raw ID ("io.github….desktop") and no icon, because
 * the PyAppify install has no Start-menu shortcut carrying the ID. Written on every start, so a
 * moved install points at its own icon again.
 */
export function appIdentityCommands(appId: string, displayName: string, iconFile: string | null): string[][] {
  const key = `HKCU\\Software\\Classes\\AppUserModelId\\${appId}`;
  const value = (name: string, data: string) => ["add", key, "/v", name, "/t", "REG_SZ", "/d", data, "/f"];
  return [value("DisplayName", displayName), ...(iconFile ? [value("IconUri", iconFile)] : [])];
}
