import { spawn } from "node:child_process";
import * as path from "node:path";
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
      path.resolve(message.launcher).toLowerCase() !== expected.toLowerCase())
    throw new Error("启动器不属于当前安装，已取消更新");
  return { executable: expected, cwd: install, args: ["--update-to-version", message.target] };
}

export function launchUpdate(cwd: string, message: MaintenanceMessage): Promise<void> {
  const command = updateCommand(cwd, message);
  return new Promise((resolve, reject) => {
    const child = spawn(command.executable, command.args,
      { cwd: command.cwd, detached: true, windowsHide: true, stdio: "ignore" });
    child.once("error", reject);
    child.once("spawn", () => { child.unref(); resolve(); });
  });
}
