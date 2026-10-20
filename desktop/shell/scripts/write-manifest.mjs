// Writes shell-manifest.json next to the packaged executable; the core's bootstrap
// (src/desktop/bootstrap.py) refuses shells whose version or API range does not match.
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("..", import.meta.url));
const target = process.argv[2] ?? join(root, "release", "win-unpacked");
if (!existsSync(target)) {
  console.error(`packaged shell not found: ${target}`);
  process.exit(1);
}
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const { SUPPORTED_API } = require(join(root, "dist", "protocol.js"));
const manifest = {
  version: pkg.version,
  api: SUPPORTED_API,
  electron: pkg.devDependencies.electron,
};
writeFileSync(join(target, "shell-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
console.log(`wrote ${join(target, "shell-manifest.json")}: ${JSON.stringify(manifest)}`);
