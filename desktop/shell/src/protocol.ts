// Contract between the shell and the Python core. Keep in sync with src/desktop/protocol.py.
import { randomBytes } from "node:crypto";

export const PROTOCOL_VERSION = 1;
export const SUPPORTED_API = { min: 1, max: 1 } as const;
export const LINE_PREFIX = "@@okbd2 ";

export const EXIT = {
  OK: 0,
  HANDSHAKE_FAILED: 10,
  EXECUTOR_LOCKED: 11,
  STARTUP_FAILED: 12,
} as const;

// R3 timing contract: the core starts its safe exit at once and its own watchdog
// ends it within 15 s; the shell forcibly ends the registered core only at 25 s.
export const TIMEOUTS = {
  readyMs: 90_000,
  shutdownMs: 25_000,
  forcedExitWaitMs: 5_000,
} as const;

export type ReadyMessage = {
  type: "ready";
  port: number;
  pid: number;
  api: number;
  core_version: string;
};
export type FatalMessage = {
  type: "fatal";
  code: string;
  message: string;
  holder_pid?: number | null;
};
export type ExitingMessage = { type: "exiting"; reason: string };
export type NotificationMessage = { type: "notification"; title: string; message: string; error: boolean };
export type CoreMessage = ReadyMessage | FatalMessage | ExitingMessage | NotificationMessage;

export function parseCoreLine(line: string): CoreMessage | null {
  if (!line.startsWith(LINE_PREFIX)) {
    return null;
  }
  let data: unknown;
  try {
    data = JSON.parse(line.slice(LINE_PREFIX.length));
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) {
    return null;
  }
  const message = data as Record<string, unknown>;
  switch (message.type) {
    case "ready":
      if (
        Number.isInteger(message.port) &&
        (message.port as number) > 0 &&
        (message.port as number) < 65536 &&
        Number.isInteger(message.pid) &&
        Number.isInteger(message.api)
      ) {
        return {
          type: "ready",
          port: message.port as number,
          pid: message.pid as number,
          api: message.api as number,
          core_version: String(message.core_version ?? ""),
        };
      }
      return null;
    case "fatal":
      return {
        type: "fatal",
        code: String(message.code ?? "unknown"),
        message: String(message.message ?? ""),
        holder_pid: typeof message.holder_pid === "number" ? message.holder_pid : null,
      };
    case "exiting":
      return { type: "exiting", reason: String(message.reason ?? "") };
    case "notification":
      if (typeof message.title !== "string" || typeof message.message !== "string" ||
          typeof message.error !== "boolean") return null;
      return { type: "notification", title: message.title.slice(0, 256),
        message: message.message.slice(0, 4096), error: message.error };
    default:
      return null;
  }
}

export function apiSupported(api: number): boolean {
  return api >= SUPPORTED_API.min && api <= SUPPORTED_API.max;
}

export function newToken(): string {
  return randomBytes(32).toString("base64url");
}

export function handshakeLine(token: string, parentPid: number): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, token, parent_pid: parentPid }) + "\n";
}

export function shutdownLine(reason: string): string {
  return JSON.stringify({ cmd: "shutdown", reason }) + "\n";
}

/** Splits a byte stream into complete lines, keeping a partial tail between chunks. */
export class LineSplitter {
  private rest = "";

  push(chunk: string): string[] {
    const text = this.rest + chunk;
    const parts = text.split(/\r?\n/);
    this.rest = parts.pop() ?? "";
    return parts;
  }

  flush(): string[] {
    const tail = this.rest;
    this.rest = "";
    return tail ? [tail] : [];
  }
}
