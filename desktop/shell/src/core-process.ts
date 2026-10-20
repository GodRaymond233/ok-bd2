// Starts, watches and stops the Python core. Electron-free so it can be tested with plain Node.
//
// Process boundary (R3): the shell only ever terminates the one process it spawned, through
// the ChildProcess handle it got from spawn(). It never looks processes up by PID or name and
// never ends a process tree, so a game the core launched keeps running.
import { spawn, type ChildProcess, type SpawnOptions } from "node:child_process";
import { EventEmitter } from "node:events";
import {
  LineSplitter,
  TIMEOUTS,
  apiSupported,
  handshakeLine,
  newToken,
  parseCoreLine,
  shutdownLine,
  type CoreMessage,
} from "./protocol";

export interface CoreCommand {
  python: string;
  script: string;
  cwd: string;
  args?: string[];
  env?: Record<string, string>;
}

export interface CoreReady {
  port: number;
  pid: number;
  api: number;
  coreVersion: string;
  origin: string;
}

export interface CoreOptions {
  readyTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  forcedExitWaitMs?: number;
  stderrLines?: number;
  spawnImpl?: (command: string, args: string[], options: SpawnOptions) => ChildProcess;
  parentPid?: number;
}

export type CoreErrorKind = "spawn" | "fatal" | "exited" | "timeout" | "incompatible";

export interface CoreErrorDetails {
  code?: string;
  exitCode?: number | null;
  holderPid?: number | null;
  stderrTail?: string[];
}

export class CoreError extends Error {
  constructor(
    readonly kind: CoreErrorKind,
    message: string,
    readonly details: CoreErrorDetails = {},
  ) {
    super(message);
    this.name = "CoreError";
  }
}

export interface StopResult {
  exitCode: number | null;
  forced: boolean;
  durationMs: number;
}

export interface CoreExit {
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  requested: boolean;
  coreReason: string | null;
  stderrTail: string[];
}

type State = "idle" | "starting" | "ready" | "stopping" | "exited";

export class CoreProcess extends EventEmitter {
  readonly token = newToken();
  state: State = "idle";
  ready: CoreReady | null = null;
  child: ChildProcess | null = null;
  exitCode: number | null = null;
  private stderr: string[] = [];
  private fatal: CoreMessage | null = null;
  private coreReason: string | null = null;
  private stopRequested = false;
  private exited: Promise<void> | null = null;
  private readonly options: Required<Omit<CoreOptions, "spawnImpl" | "parentPid">> &
    Pick<CoreOptions, "spawnImpl" | "parentPid">;

  constructor(
    readonly command: CoreCommand,
    options: CoreOptions = {},
  ) {
    super();
    this.options = {
      readyTimeoutMs: options.readyTimeoutMs ?? TIMEOUTS.readyMs,
      shutdownTimeoutMs: options.shutdownTimeoutMs ?? TIMEOUTS.shutdownMs,
      forcedExitWaitMs: options.forcedExitWaitMs ?? TIMEOUTS.forcedExitWaitMs,
      stderrLines: options.stderrLines ?? 200,
      spawnImpl: options.spawnImpl,
      parentPid: options.parentPid,
    };
  }

  stderrTail(): string[] {
    return [...this.stderr];
  }

  start(): Promise<CoreReady> {
    if (this.state !== "idle") {
      return Promise.reject(new CoreError("spawn", "核心已经启动过"));
    }
    this.state = "starting";
    const spawnImpl = this.options.spawnImpl ?? spawn;
    const args = [this.command.script, "--service", ...(this.command.args ?? [])];
    let child: ChildProcess;
    try {
      child = spawnImpl(this.command.python, args, {
        cwd: this.command.cwd,
        env: {
          ...process.env,
          ...this.command.env,
          PYTHONUTF8: "1",
          PYTHONIOENCODING: "utf-8",
          PYTHONUNBUFFERED: "1",
        },
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (error) {
      this.state = "exited";
      return Promise.reject(new CoreError("spawn", `无法启动核心：${String(error)}`));
    }
    this.child = child;

    return new Promise<CoreReady>((resolve, reject) => {
      let settled = false;
      const settle = (action: () => void) => {
        if (!settled) {
          settled = true;
          clearTimeout(readyTimer);
          action();
        }
      };
      const readyTimer = setTimeout(() => {
        settle(() => {
          reject(
            new CoreError("timeout", `核心在 ${this.options.readyTimeoutMs / 1000} 秒内没有就绪`, {
              stderrTail: this.stderrTail(),
            }),
          );
          void this.stop("ready-timeout");
        });
      }, this.options.readyTimeoutMs);

      const stdoutLines = new LineSplitter();
      child.stdout?.setEncoding("utf8");
      child.stdout?.on("data", (chunk: string) => {
        for (const line of stdoutLines.push(chunk)) {
          const message = parseCoreLine(line);
          if (message === null) {
            this.remember(line);
            continue;
          }
          this.emit("message", message);
          if (message.type === "ready") {
            const ready: CoreReady = {
              port: message.port,
              pid: message.pid,
              api: message.api,
              coreVersion: message.core_version,
              origin: `http://127.0.0.1:${message.port}`,
            };
            if (!apiSupported(ready.api)) {
              settle(() => {
                reject(
                  new CoreError("incompatible", `外壳不支持核心接口版本 ${ready.api}，请更新外壳`),
                );
                void this.stop("incompatible-api");
              });
              continue;
            }
            this.ready = ready;
            this.state = "ready";
            settle(() => resolve(ready));
          } else if (message.type === "fatal") {
            this.fatal = message;
          } else if (message.type === "exiting") {
            this.coreReason = message.reason;
          }
        }
      });
      const stderrLines = new LineSplitter();
      child.stderr?.setEncoding("utf8");
      child.stderr?.on("data", (chunk: string) => {
        for (const line of stderrLines.push(chunk)) {
          this.remember(line);
        }
      });
      child.stdin?.on("error", () => {
        // The core is gone; the exit handler reports it.
      });

      this.exited = new Promise<void>((done) => {
        child.once("exit", (code, signal) => {
          this.exitCode = code;
          const previous = this.state;
          this.state = "exited";
          const exit: CoreExit = {
            exitCode: code,
            signal,
            requested: this.stopRequested,
            coreReason: this.coreReason,
            stderrTail: this.stderrTail(),
          };
          settle(() => {
            const fatal = this.fatal?.type === "fatal" ? this.fatal : null;
            reject(
              fatal
                ? new CoreError("fatal", fatal.message, {
                    code: fatal.code,
                    exitCode: code,
                    holderPid: fatal.holder_pid ?? null,
                    stderrTail: exit.stderrTail,
                  })
                : new CoreError("exited", `核心在就绪前退出（退出码 ${code}）`, {
                    exitCode: code,
                    stderrTail: exit.stderrTail,
                  }),
            );
          });
          this.emit("exit", exit);
          if (previous === "ready" && !this.stopRequested && this.coreReason === null) {
            this.emit("crashed", exit);
          }
          done();
        });
      });
      child.once("error", (error) => {
        settle(() => {
          this.state = "exited";
          reject(new CoreError("spawn", `无法启动核心：${error.message}`));
        });
      });

      child.stdin?.write(handshakeLine(this.token, this.options.parentPid ?? process.pid));
    });
  }

  /** Ask for a safe exit; force-terminate only the registered process after the timeout. */
  async stop(reason: string): Promise<StopResult> {
    const started = Date.now();
    const child = this.child;
    if (child === null || this.state === "exited" || this.exited === null) {
      return { exitCode: this.exitCode, forced: false, durationMs: 0 };
    }
    this.stopRequested = true;
    this.state = "stopping";
    try {
      child.stdin?.write(shutdownLine(reason));
    } catch {
      // stdin already closed; the timeout below still applies.
    }
    const exited = this.exited;
    if (await waitFor(exited, this.options.shutdownTimeoutMs)) {
      return { exitCode: this.exitCode, forced: false, durationMs: Date.now() - started };
    }
    child.kill();
    await waitFor(exited, this.options.forcedExitWaitMs);
    return { exitCode: this.exitCode, forced: true, durationMs: Date.now() - started };
  }

  private remember(line: string): void {
    if (!line) {
      return;
    }
    this.stderr.push(line);
    if (this.stderr.length > this.options.stderrLines) {
      this.stderr.splice(0, this.stderr.length - this.options.stderrLines);
    }
    this.emit("output", line);
  }
}

function waitFor(promise: Promise<void>, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    void promise.then(() => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}
