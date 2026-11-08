// Which URLs the window may show and which links may open in the system browser.
import { pathToFileURL } from "node:url";

export const EXTERNAL_ALLOWLIST = [
  "https://github.com/GodRaymond233/",
  "https://cnb.cool/GodRaymond233/",
];

export function sameOrigin(url: string, origin: string | null): boolean {
  if (!origin) {
    return false;
  }
  try {
    return new URL(url).origin === new URL(origin).origin;
  } catch {
    return false;
  }
}

// Web permissions the core's own pages get; everything else stays denied. Copy buttons need
// clipboard writes (navigator.clipboard.writeText). Reading the clipboard is not allowed.
export const PAGE_PERMISSIONS: readonly string[] = ["clipboard-sanitized-write"];

export function pagePermissionAllowed(permission: string, url: string, origin: string | null): boolean {
  return PAGE_PERMISSIONS.includes(permission) && sameOrigin(url, origin);
}

export function isStatusPage(url: string, statusFile: string): boolean {
  try {
    const target = new URL(url);
    const page = pathToFileURL(statusFile);
    return target.protocol === "file:" && target.pathname === page.pathname;
  } catch {
    return false;
  }
}

export function isAllowedExternal(url: string): boolean {
  try {
    const target = new URL(url);
    return (
      target.protocol === "https:" &&
      EXTERNAL_ALLOWLIST.some((prefix) => target.href.startsWith(prefix))
    );
  } catch {
    return false;
  }
}

export function navigationAllowed(url: string, origin: string | null, statusFile: string): boolean {
  return sameOrigin(url, origin) || isStatusPage(url, statusFile);
}
