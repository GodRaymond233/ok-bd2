// A small size-rotated log next to the core's logs (logs/desktop-shell.log).
import * as fs from "node:fs";
import * as path from "node:path";

export class ShellLog {
  constructor(
    readonly file: string,
    private readonly maxBytes = 2 * 1024 * 1024,
    private readonly keep = 3,
  ) {}

  info(message: string): void {
    this.write("INFO", message);
  }

  warn(message: string): void {
    this.write("WARN", message);
  }

  error(message: string): void {
    this.write("ERROR", message);
  }

  private write(level: string, message: string): void {
    const line = `${new Date().toISOString()} ${level} ${message}\n`;
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      this.rotate();
      fs.appendFileSync(this.file, line, "utf8");
    } catch {
      // Logging must never break the shell.
    }
  }

  private rotate(): void {
    let size = 0;
    try {
      size = fs.statSync(this.file).size;
    } catch {
      return;
    }
    if (size < this.maxBytes) {
      return;
    }
    for (let index = this.keep - 1; index >= 1; index -= 1) {
      const source = `${this.file}.${index}`;
      if (fs.existsSync(source)) {
        fs.renameSync(source, `${this.file}.${index + 1}`);
      }
    }
    fs.renameSync(this.file, `${this.file}.1`);
    const oldest = `${this.file}.${this.keep + 1}`;
    if (fs.existsSync(oldest)) {
      fs.rmSync(oldest);
    }
  }
}
