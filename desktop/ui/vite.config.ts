import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Python core serves the build from src/desktop/ui_dist: "/" is index.html and
// "/assets" the hashed files. The bundle ships with the core, never inside the shell.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../../src/desktop/ui_dist",
    emptyOutDir: true,
    assetsInlineLimit: 0,
  },
});
