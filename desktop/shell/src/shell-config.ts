// Command line of the shell. The bootstrap (main_desktop.py) passes these; during development
// they can also come from environment variables.
import * as path from "node:path";
import * as fs from "node:fs";
import type { CoreCommand } from "./core-process";

export const ZOOM_LEVELS = [80, 90, 100, 110, 125, 150];
export interface Bounds { x: number; y: number; width: number; height: number }
const THEMES = ["system", "light", "dark"] as const;
type Theme = (typeof THEMES)[number];
/** What the pages remember. A page's own storage is lost: it is in memory and tied to the core's random port. */
export interface UiPreferences { theme?: Theme; dockExpanded?: boolean }
export interface DesktopState extends UiPreferences { zoom: number; bounds?: Bounds; maximized: boolean }

/** The valid part of the preferences in a state file or from a page. */
export function validUiPreferences(value: unknown): UiPreferences {
  const { theme, dockExpanded } = (value ?? {}) as Record<string, unknown>;
  return {
    ...(THEMES.includes(theme as Theme) ? { theme: theme as Theme } : {}),
    ...(typeof dockExpanded === "boolean" ? { dockExpanded } : {}),
  };
}

export function readDesktopState(file: string): DesktopState {
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    const b = data.bounds;
    return {
      zoom: ZOOM_LEVELS.includes(data.zoom) ? data.zoom : 100,
      maximized: data.maximized === true,
      bounds: b && [b.x, b.y, b.width, b.height].every(Number.isInteger) && b.width > 0 && b.height > 0
        ? { x: b.x, y: b.y, width: b.width, height: b.height } : undefined,
      ...validUiPreferences(data),
    };
  } catch {
    return { zoom: 100, maximized: false };
  }
}

/** The window size that keeps the pages' 600x450 CSS pixels at this zoom, within the display. */
export function minimumSize(zoom: number, area: Bounds): { width: number; height: number } {
  const factor = Math.max(1, zoom / 100);
  return {
    width: Math.min(area.width, Math.ceil(600 * factor)),
    height: Math.min(area.height, Math.ceil(450 * factor)),
  };
}

function sharedArea(a: Bounds, b: Bounds): number {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return width > 0 && height > 0 ? width * height : 0;
}

/** The saved window on the display it mostly covers; `areas[0]` when it is on none of them. */
export function fitBounds(saved: Bounds | undefined, areas: Bounds[], zoom = 100): Bounds {
  let area = areas[0];
  let best = 0;
  for (const candidate of saved ? areas : []) {
    const shared = sharedArea(saved as Bounds, candidate);
    if (shared > best) {
      best = shared;
      area = candidate;
    }
  }
  const minimum = minimumSize(zoom, area);
  const width = Math.min(area.width, Math.max(minimum.width, saved?.width ?? 1200));
  const height = Math.min(area.height, Math.max(minimum.height, saved?.height ?? 800));
  return {
    x: Math.max(area.x, Math.min(saved?.x ?? area.x + Math.round((area.width - width) / 2), area.x + area.width - width)),
    y: Math.max(area.y, Math.min(saved?.y ?? area.y + Math.round((area.height - height) / 2), area.y + area.height - height)),
    width, height,
  };
}

export interface ShellOptions {
  core: CoreCommand;
  userDataDir: string | null;
  logFile: string;
  devTools: boolean;
  /** Write a JSON report of one start/load/quit cycle here, then exit (Windows checks). */
  smokeTest: string | null;
}

const VALUE_FLAGS = new Set([
  "--core-python",
  "--core-script",
  "--core-cwd",
  "--core-arg",
  "--user-data",
  "--smoke-test",
]);

export function parseShellArgs(
  argv: string[],
  env: Record<string, string | undefined> = process.env,
): ShellOptions | { error: string } {
  const values: Record<string, string> = {};
  const coreArgs: string[] = [];
  let devTools = env.OKBD2_DEV_TOOLS === "1";
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--dev-tools") {
      devTools = true;
      continue;
    }
    const [flag, inline] = argument.split(/=(.*)/s, 2);
    if (!VALUE_FLAGS.has(flag)) {
      continue;
    }
    const value = inline !== undefined ? inline : argv[++index];
    if (value === undefined) {
      return { error: `参数 ${flag} 缺少取值` };
    }
    if (flag === "--core-arg") {
      coreArgs.push(value);
    } else {
      values[flag] = value;
    }
  }
  const python = values["--core-python"] ?? env.OKBD2_CORE_PYTHON;
  const cwd = values["--core-cwd"] ?? env.OKBD2_CORE_CWD;
  if (!python) {
    return { error: "没有指定核心使用的 Python（--core-python）" };
  }
  if (!cwd) {
    return { error: "没有指定核心的工作目录（--core-cwd）" };
  }
  const script = values["--core-script"] ?? env.OKBD2_CORE_SCRIPT ?? "main.py";
  return {
    core: {
      python,
      script: path.isAbsolute(script) ? script : path.join(cwd, script),
      cwd,
      args: coreArgs,
    },
    userDataDir: values["--user-data"] ?? env.OKBD2_USER_DATA ?? null,
    logFile: path.join(cwd, "logs", "desktop-shell.log"),
    devTools,
    smokeTest: values["--smoke-test"] ?? null,
  };
}
