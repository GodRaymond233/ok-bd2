import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api.ts";
import { App } from "./App.tsx";
import { applyLanguage } from "./i18n.ts";
import { applyTheme, storedTheme } from "./settings.ts";
import "./tokens.css";
import "./styles.css";

// The theme chosen in 设置 applies before the first paint.
applyTheme(storedTheme());

function render() {
  createRoot(document.getElementById("root") as HTMLElement).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

// The language comes from the core; without an answer the page starts in Chinese and says why.
api.language().then(applyLanguage, () => undefined).finally(render);
