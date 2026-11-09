import { spawn } from "node:child_process";
import * as path from "node:path";
import * as fs from "node:fs";
import * as os from "node:os";
import type { MaintenanceMessage } from "./protocol";

export function maintenanceArguments(message: MaintenanceMessage): string[] {
  if (!/^v?\d+\.\d+\.\d+$/.test(message.version)) throw new Error("核心版本无效，无法维护数据");
  if (message.operation === "update" && !/^v?\d+\.\d+\.\d+$/.test(message.target))
    throw new Error("更新版本无效");
  if (message.operation === "restore" && !/^\d{8}T\d{12}Z-[0-9a-f]{8}$/.test(message.backup_id))
    throw new Error("备份编号无效");
  return ["-m", "src.desktop.maintenance", message.operation, "--version", message.version,
    ...(message.operation === "restore" ? ["--backup-id", message.backup_id] : ["--launcher", message.launcher])];
}

export async function maintainData(python: string, cwd: string, message: MaintenanceMessage): Promise<void> {
  const args = maintenanceArguments(message);
  await new Promise<void>((resolve, reject) => {
    const child = spawn(python, args, { cwd, windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
    let errors = "";
    child.stderr?.on("data", (data: Buffer) => { errors = (errors + data.toString()).slice(-4000); });
    child.once("error", reject);
    child.once("exit", (code) => code === 0 ? resolve() : reject(new Error(errors || `备份操作失败：${code}`)));
  });
}

export function updateCommand(cwd: string, message: MaintenanceMessage): { executable: string; cwd: string; args: string[] } {
  maintenanceArguments(message);
  const working = path.resolve(cwd);
  const base = path.dirname(working);
  const install = path.dirname(path.dirname(path.dirname(base)));
  const expected = path.join(install, `${path.basename(base)}.exe`);
  if (path.basename(working).toLowerCase() !== "working" ||
      path.basename(path.dirname(base)).toLowerCase() !== "apps" ||
      path.basename(path.dirname(path.dirname(base))).toLowerCase() !== "data" ||
      !sameFile(message.launcher, expected))
    throw new Error("启动器不属于当前安装，已取消更新");
  return { executable: expected, cwd: install, args: ["--update-to-version", message.target] };
}

function sameFile(left: string, right: string): boolean {
  try {
    const a = fs.statSync(left, { bigint: true });
    const b = fs.statSync(right, { bigint: true });
    return a.dev === b.dev && a.ino === b.ino;
  } catch {
    try {
      return fs.realpathSync.native(left).toLowerCase() === fs.realpathSync.native(right).toLowerCase();
    } catch { return false; }
  }
}

export function launchUpdate(cwd: string, message: MaintenanceMessage): Promise<void> {
  const command = updateCommand(cwd, message);
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "okbd2-update-"));
  const helper = path.join(temporary, "update.ps1");
  fs.writeFileSync(helper, "\uFEFF" + fs.readFileSync(path.join(__dirname, "../static/update.ps1"), "utf8"));
  const system = path.join(process.env.SystemRoot || "C:\\Windows", "System32");
  const quote = (value: string) => "'" + value.replace(/'/g, "''") + "'";
  const invocation = `& ${quote(helper)} -Launcher ${quote(command.executable)} -Working ${quote(cwd)} ` +
    `-Target ${quote(message.target)} -Previous ${quote(message.version)}`;
  const encoded = Buffer.from(invocation, "utf16le").toString("base64");
  const powershell = `"${path.join(system, "WindowsPowerShell/v1.0/powershell.exe")}" ` +
    `-NoProfile -ExecutionPolicy Bypass -EncodedCommand ${encoded}`;
  const wrapper = path.join(temporary, "run.vbs");
  // Windows PowerShell 5 exits before running a script when started with DETACHED_PROCESS.
  // A windowless WSH host survives Electron and starts PowerShell with a usable process context.
  fs.writeFileSync(wrapper, Buffer.from("\uFEFF" +
    `Set runner = CreateObject("WScript.Shell")\r\n` +
    `code = runner.Run("${powershell.replace(/"/g, '""')}", 0, True)\r\n` +
    `Set files = CreateObject("Scripting.FileSystemObject")\r\n` +
    `folder = files.GetParentFolderName(WScript.ScriptFullName)\r\n` +
    `On Error Resume Next\r\nfiles.DeleteFile WScript.ScriptFullName, True\r\n` +
    `files.DeleteFolder folder\r\nWScript.Quit code\r\n`, "utf16le"));
  return new Promise((resolve, reject) => {
    // The launcher stops every process whose executable lives under data/apps/ok-bd2.
    // A system interpreter keeps the handoff alive while the installed Python is replaced.
    const child = spawn(path.join(system, "wscript.exe"), ["//B", "//NoLogo", wrapper],
      { cwd: temporary, detached: true, windowsHide: true, stdio: "ignore" });
    child.once("error", reject);
    child.once("spawn", () => { child.unref(); resolve(); });
  });
}
