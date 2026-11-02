// The decisions behind the global hotkey and quitting. Electron-free so it can be tested with
// plain Node; main.ts connects them to globalShortcut, dialogs and the core.

export const HOTKEYS = ["F9", "F10", "F11", "F12"];

export interface ToggleResponse {
  status: number;
  json(): Promise<unknown>;
}

export interface ToggleFailure {
  /**
   * refused: the core answered and changed nothing, so no task is moving.
   * unknown: no clear answer. The request may still take effect and a task may be running.
   */
  kind: "refused" | "unknown";
  detail: string;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function refusalReason(response: ToggleResponse): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) {
      return body.detail;
    }
  } catch {
    // Not the core's JSON error: the generic text below says enough.
  }
  return "任务控制失败";
}

/**
 * One start or pause request at a time. Only the request blocks the next press: `onFailure` runs
 * after the lock is released, so a dialog left open never swallows the hotkey.
 */
export class ExecutionToggle {
  private pending = false;

  constructor(private readonly onFailure: (failure: ToggleFailure) => void) {}

  async run(send: () => Promise<ToggleResponse>): Promise<void> {
    if (this.pending) {
      return;
    }
    this.pending = true;
    let failure: ToggleFailure | null = null;
    try {
      const response = await send();
      if (response.status >= 400 && response.status < 500) {
        failure = { kind: "refused", detail: await refusalReason(response) };
      } else if (response.status >= 500) {
        failure = { kind: "unknown", detail: `核心返回 ${response.status}` };
      }
    } catch (error) {
      failure = { kind: "unknown", detail: errorText(error) };
    } finally {
      this.pending = false;
    }
    if (failure) {
      this.onFailure(failure);
    }
  }
}

export interface ShortcutApi {
  register(key: string, callback: () => void): boolean;
  unregister(key: string): void;
}

/** Keeps the registered global shortcut in line with the saved setting. */
export class HotkeyBinding {
  requested = "None";
  registered: string | null = null;
  error: string | null = null;

  constructor(
    private readonly shortcuts: ShortcutApi,
    private readonly callback: () => void,
  ) {}

  apply(key: string): void {
    if (key === this.requested && (key === "None" || key === this.registered)) {
      return;
    }
    this.release();
    this.requested = key;
    this.error = null;
    if (key === "None") {
      return;
    }
    if (!HOTKEYS.includes(key)) {
      this.error = "快捷键设置不受支持";
    } else if (this.shortcuts.register(key, this.callback)) {
      this.registered = key;
    } else {
      this.error = `${key} 已被其他程序占用，快捷键未启用；请更换按键。`;
    }
  }

  /** A valid key another program holds may become free; nothing else is worth retrying. */
  get waitingForKey(): boolean {
    return this.registered === null && HOTKEYS.includes(this.requested);
  }

  release(): void {
    if (this.registered) {
      this.shortcuts.unregister(this.registered);
    }
    this.registered = null;
  }

  /** The next core starts from its own setting. */
  reset(): void {
    this.release();
    this.requested = "None";
    this.error = null;
  }
}

/** Decides who may start the one safe exit. */
export class QuitGate {
  started = false;
  private asking = false;

  /** True when the caller should run the exit now. */
  async begin(reason: string, confirm: () => Promise<boolean>): Promise<boolean> {
    if (this.started) {
      return false;
    }
    // Windows is ending the session: it does not wait for an answer to an open question.
    if (reason !== "session-end") {
      if (this.asking) {
        return false;
      }
      this.asking = true;
      try {
        if (!(await confirm()) || this.started) {
          return false;
        }
      } finally {
        this.asking = false;
      }
    }
    this.started = true;
    return true;
  }
}
