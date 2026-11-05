import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { applyTheme, rememberDockExpanded, rememberRailWidth, storedDockExpanded, storedRailWidth, storedTheme } from "../src/settings.ts";
import type { UiPreferences } from "../src/types.ts";

function setGlobal(name: string, value: unknown): void {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

const dataset: { theme?: string } = {};

function useShell(saved: UiPreferences): UiPreferences[] {
  const sent: UiPreferences[] = [];
  setGlobal("window", {
    okbd2Shell: { uiPreferences: () => saved, setUiPreferences: async (change: UiPreferences) => void sent.push(change) },
  });
  return sent;
}

function useBrowser(storage: Partial<Storage>): void {
  setGlobal("window", {});
  setGlobal("localStorage", storage);
}

function memoryStorage(): Partial<Storage> {
  const values = new Map<string, string>();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => void values.set(key, value) };
}

beforeEach(() => {
  delete dataset.theme;
  setGlobal("document", { documentElement: { dataset } });
});

afterEach(() => {
  for (const name of ["window", "localStorage", "document"]) {
    delete (globalThis as unknown as Record<string, unknown>)[name];
  }
});

test("in the shell, the page's choices are read from and sent to the shell", () => {
  const sent = useShell({ theme: "dark", dockExpanded: false, railWidth: 280 });
  assert.equal(storedTheme(), "dark");
  assert.equal(storedDockExpanded(), false);
  assert.equal(storedRailWidth(), 280);
  applyTheme("light");
  rememberDockExpanded(true);
  rememberRailWidth(260);
  assert.equal(dataset.theme, "light");
  assert.deepEqual(sent, [{ theme: "light" }, { dockExpanded: true }, { railWidth: 260 }]);
});

test("in the shell, nothing saved yet means the defaults", () => {
  useShell({});
  assert.equal(storedTheme(), "system");
  assert.equal(storedDockExpanded(), null);
  assert.equal(storedRailWidth(), 216);
  useShell({ railWidth: 9000 });
  assert.equal(storedRailWidth(), 216);
});

test("a choice the shell could not save still applies to the page", async () => {
  setGlobal("window", {
    okbd2Shell: { uiPreferences: () => ({}), setUiPreferences: () => Promise.reject(new Error("no")) },
  });
  applyTheme("dark");
  assert.equal(dataset.theme, "dark");
  await new Promise((resolve) => setImmediate(resolve));
});

test("the system theme removes the page's override", () => {
  useShell({});
  applyTheme("dark");
  applyTheme("system");
  assert.equal(dataset.theme, undefined);
});

test("without the shell, the choices stay in the browser", () => {
  useBrowser(memoryStorage());
  assert.equal(storedTheme(), "system");
  assert.equal(storedDockExpanded(), null);
  applyTheme("dark");
  rememberDockExpanded(false);
  rememberRailWidth(240);
  assert.equal(storedTheme(), "dark");
  assert.equal(storedDockExpanded(), false);
  assert.equal(storedRailWidth(), 240);
});

test("without the shell or storage, the page still works", () => {
  const unavailable = () => {
    throw new Error("blocked");
  };
  useBrowser({ getItem: unavailable, setItem: unavailable });
  assert.equal(storedTheme(), "system");
  assert.equal(storedDockExpanded(), null);
  assert.equal(storedRailWidth(), 216);
  applyTheme("dark");
  rememberDockExpanded(true);
  rememberRailWidth(260);
  assert.equal(dataset.theme, "dark");
});
