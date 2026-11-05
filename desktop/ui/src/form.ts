// Helpers for the task settings form, which is built from the core's field descriptions.
import { coreText, t } from "./i18n.ts";
import type { ConfigField } from "./types.ts";

export function isModified(field: ConfigField): boolean {
  return field.default !== null && field.default !== undefined && JSON.stringify(field.value) !== JSON.stringify(field.default);
}

export function valueText(value: unknown): string {
  if (typeof value === "boolean") {
    return value ? t("开") : t("关");
  }
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => coreText(String(item))).join(t("、")) : t("无");
  }
  if (value === "" || value === null || value === undefined) {
    return t("空");
  }
  return coreText(String(value));
}

export function modifiedFields(fields: ConfigField[]): ConfigField[] {
  return fields.filter(isModified);
}

/** The number typed into a numeric field, or an error message. */
export function parseNumber(text: string, field: ConfigField): number | string {
  const trimmed = text.trim();
  const value = Number(trimmed);
  if (trimmed === "" || !Number.isFinite(value)) {
    return t("请输入数字");
  }
  if (field.kind === "integer" && !Number.isInteger(value)) {
    return t("请输入整数");
  }
  if (field.minimum !== null && value < field.minimum) {
    return t("不能小于 {value}", { value: field.minimum });
  }
  if (field.maximum !== null && value > field.maximum) {
    return t("不能大于 {value}", { value: field.maximum });
  }
  return value;
}

/** Stepper increment: whole numbers for integers, finer for small decimal ranges. */
export function numberStep(field: ConfigField): number {
  if (field.kind === "integer") {
    return 1;
  }
  const span = Math.max(Math.abs(field.maximum ?? 0), Math.abs(field.minimum ?? 0));
  return span > 0 && span <= 1 ? 0.01 : span > 0 && span <= 10 ? 0.1 : 1;
}

export function stepNumber(value: unknown, field: ConfigField, direction: 1 | -1): number {
  const step = numberStep(field);
  const current = typeof value === "number" ? value : 0;
  let next = Math.round((current + direction * step) / step) * step;
  next = Number(next.toFixed(step < 1 ? 2 : 0));
  if (field.minimum !== null) {
    next = Math.max(field.minimum, next);
  }
  if (field.maximum !== null) {
    next = Math.min(field.maximum, next);
  }
  return next;
}

/** Choices of a select or multiple-choice field, as strings for the controls. */
export function optionList(field: ConfigField): string[] {
  return Array.isArray(field.options) ? field.options.map(String) : [];
}

export function toggleChoice(value: unknown, option: string): string[] {
  const chosen = Array.isArray(value) ? value.map(String) : [];
  return chosen.includes(option) ? chosen.filter((item) => item !== option) : [...chosen, option];
}

/** One entry per line for free-form list fields; blank lines are dropped. */
export function linesToList(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** The field's display name: the core's label for settings, otherwise the key. */
export function fieldLabel(field: ConfigField): string {
  return coreText(field.label || field.key);
}

/** An option's display name; the stored value when the core names none. */
export function optionText(field: ConfigField, option: string): string {
  return coreText(field.option_labels?.[option] ?? option);
}
