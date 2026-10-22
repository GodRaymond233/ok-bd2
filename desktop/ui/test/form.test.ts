import assert from "node:assert/strict";
import { test } from "node:test";
import { isModified, linesToList, numberStep, parseNumber, stepNumber, toggleChoice, valueText } from "../src/form.ts";
import type { ConfigField } from "../src/types.ts";

function field(overrides: Partial<ConfigField> = {}): ConfigField {
  return {
    key: "次数",
    value: 3,
    default: 3,
    description: "",
    kind: "integer",
    options: null,
    allow_duplication: false,
    minimum: 1,
    maximum: 10,
    sub_config: false,
    ...overrides,
  };
}

test("modified values and their text", () => {
  assert.equal(isModified(field()), false);
  assert.equal(isModified(field({ value: 4 })), true);
  assert.equal(isModified(field({ kind: "multi_selection", value: ["a"], default: ["a"] })), false);
  assert.equal(isModified(field({ default: null, value: 4 })), false);
  assert.equal(valueText(true), "开");
  assert.equal(valueText(["料理", "矿石"]), "料理、矿石");
  assert.equal(valueText(""), "空");
  assert.equal(valueText(0.7), "0.7");
});

test("numbers are checked before they reach the core", () => {
  assert.equal(parseNumber(" 5 ", field()), 5);
  assert.equal(parseNumber("abc", field()), "请输入数字");
  assert.equal(parseNumber("", field()), "请输入数字");
  assert.equal(parseNumber("2.5", field()), "请输入整数");
  assert.equal(parseNumber("0", field()), "不能小于 1");
  assert.equal(parseNumber("11", field()), "不能大于 10");
  assert.equal(parseNumber("2.5", field({ kind: "number" })), 2.5);
});

test("steppers stay on the grid and inside the range", () => {
  assert.equal(stepNumber(3, field(), 1), 4);
  assert.equal(stepNumber(10, field(), 1), 10);
  assert.equal(stepNumber(1, field(), -1), 1);
  const ratio = field({ kind: "number", value: 0.7, minimum: 0.5, maximum: 0.95 });
  assert.equal(numberStep(ratio), 0.01);
  assert.equal(stepNumber(0.7, ratio, 1), 0.71);
  assert.equal(numberStep(field({ kind: "number", minimum: 2, maximum: 30 })), 1);
  assert.equal(numberStep(field({ kind: "number", minimum: 0, maximum: 5 })), 0.1);
});

test("multiple choices and free lists", () => {
  assert.deepEqual(toggleChoice(["料理"], "矿石"), ["料理", "矿石"]);
  assert.deepEqual(toggleChoice(["料理", "矿石"], "料理"), ["矿石"]);
  assert.deepEqual(toggleChoice(null, "料理"), ["料理"]);
  assert.deepEqual(linesToList(" a \n\nb\r\n"), ["a", "b"]);
});
