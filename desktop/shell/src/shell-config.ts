// Command line of the shell. The bootstrap (main_desktop.py) passes these; during development
// they can also come from environment variables.
import * as path from "node:path";
import type { CoreCommand } from "./core-process";

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
