import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import { applyTheme, storedTheme } from "./settings.ts";
import "./tokens.css";
import "./styles.css";

// The theme chosen in 设置 (kept in this browser profile) applies before the first paint.
applyTheme(storedTheme());

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
