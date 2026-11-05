import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { afterEach, test } from "node:test";
import { coreText, resolveLanguage, setLanguage, t } from "../src/i18n.ts";
import english from "../src/locales/en_US.json" with { type: "json" };

const SOURCE = new URL("../src/", import.meta.url);
const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();

afterEach(() => setLanguage("zh_CN"));

test("a preference follows the system only when asked to", () => {
  assert.equal(resolveLanguage("en_US", "zh-CN"), "en_US");
  assert.equal(resolveLanguage("zh_CN", "en-US"), "zh_CN");
  assert.equal(resolveLanguage("Auto", "zh-TW"), "zh_CN");
  assert.equal(resolveLanguage("Auto", "ja-JP"), "en_US");
});

test("Chinese shows the source text with its values filled in", () => {
  assert.equal(t("正在运行 {task}", { task: "跑商" }), "正在运行 跑商");
  assert.equal(coreText("任务已在运行或排队"), "任务已在运行或排队");
});

test("English comes from the client's catalog first, then the task catalog", () => {
  setLanguage("en_US", { 跑商: "Trading", 设置: "from the task catalog" });
  assert.equal(t("设置"), "Settings");
  assert.equal(coreText("跑商"), "Trading");
  assert.equal(t("正在运行 {task}", { task: "x" }), "Running x");
  assert.equal(t("没有这句"), "没有这句");
});

test("text from the core is matched with its values inside, which translate too", () => {
  setLanguage("en_US", { 金币: "Gold" });
  assert.equal(coreText("任务已在运行或排队"), "The task is already running or queued");
  assert.equal(coreText("配置值不正确：端口"), "Incorrect value for: Port");
  assert.equal(coreText("规则 2：金币双倍"), "Rule 2: Gold bonus");
  assert.equal(coreText("金币双倍：金币 · 用完剩余米饭\n保留免费米饭：3"), "Gold bonus: Gold · All remaining rice\nKeep free rice: 3");
  assert.equal(coreText("这句任务自己写的状态"), "这句任务自己写的状态");
});

test("every t() text is in the English catalog, with the same placeholders", () => {
  const known = new Set(Object.keys(english));
  const missing: string[] = [];
  for (const file of readdirSync(SOURCE)) {
    if (!/\.tsx?$/.test(file) || file === "i18n.ts") {
      continue;
    }
    const source = readFileSync(new URL(file, SOURCE), "utf8");
    for (const match of source.matchAll(/\bt\(\s*"((?:[^"\\]|\\.)*)"/g)) {
      const text = JSON.parse(`"${match[1]}"`) as string;
      if (!known.has(text)) {
        missing.push(`${file}: ${text}`);
      }
    }
  }
  assert.deepEqual(missing, []);
  for (const [source, target] of Object.entries(english)) {
    assert.deepEqual(placeholders(target), placeholders(source), source);
  }
});
