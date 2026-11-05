// The script editor's logic, without the page: the helper palette, Python colouring and the
// indentation edits, which follow ok-script's Qt editor.
import type { ScriptHelper } from "./types.ts";

// Not "Key": the BD2 client receives no keyboard input (AGENTS.md).
export const HELPER_GROUPS = ["Mouse", "Control", "OCR", "Template Matching", "Box", "Window", "ADB", "Logging", "Other"];
const INDENT = "    ";

/** The palette's groups in their fixed order, keeping helpers that match the search. */
export function helperGroups(helpers: ScriptHelper[], query: string): { name: string; helpers: ScriptHelper[] }[] {
  const needle = query.trim().toLowerCase();
  const matching = helpers.filter(
    (helper) => !needle || helper.name.toLowerCase().includes(needle) || helper.full_doc.toLowerCase().includes(needle),
  );
  return HELPER_GROUPS.map((name) => ({ name, helpers: matching.filter((helper) => helper.category === name) })).filter(
    (group) => group.helpers.length > 0,
  );
}

/** A value typed into a helper's parameter, as Python source: what isn't an expression is a string. */
export function pythonValue(text: string): string {
  const value = text.trim();
  if (/^(['"]).*\1$/s.test(value) || /^(True|False|None)$/.test(value)) {
    return value;
  }
  const unbalanced = (value.match(/'/g) ?? []).length % 2 === 1 || (value.match(/"/g) ?? []).length % 2 === 1;
  if (unbalanced || /^[A-Za-z_]\w*$/.test(value) || /\w\s+\w/.test(value) || !/^[\d\s.+\-*/%()[\]{},:'"\w]*$/.test(value)) {
    return `'${value.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
  }
  return value;
}

/** The call a helper inserts; parameters left empty are left out. */
export function helperCall(helper: ScriptHelper, values: Record<string, string>): string {
  const args = helper.params.flatMap((param) => {
    const value = values[param.name]?.trim();
    if (!value) {
      return [];
    }
    return [param.default ? `${param.name}=${pythonValue(value)}` : pythonValue(value)];
  });
  return `${helper.is_static ? helper.class_name : "self"}.${helper.name}(${args.join(", ")})`;
}

/** The first parameter that has to be filled in and isn't. */
export function missingParam(helper: ScriptHelper, values: Record<string, string>): string | null {
  return helper.params.find((param) => !param.default && !values[param.name]?.trim())?.name ?? null;
}

export interface Edit {
  text: string;
  start: number;
  end: number;
}

function lineStart(text: string, at: number): number {
  return text.lastIndexOf("\n", at - 1) + 1;
}

function lineEnd(text: string, at: number): number {
  const end = text.indexOf("\n", at);
  return end < 0 ? text.length : end;
}

/** A helper's call at the caret: on its own line, at the line's indent or that of a method body. */
export function insertLine(text: string, at: number, code: string): Edit {
  const start = lineStart(text, at);
  const end = lineEnd(text, at);
  const line = text.slice(start, end);
  if (line.trim()) {
    const indent = /^ */.exec(line)?.[0] ?? "";
    const inserted = `\n${indent}${code}`;
    return { text: text.slice(0, end) + inserted + text.slice(end), start: end + inserted.length, end: end + inserted.length };
  }
  const indent = line || INDENT.repeat(2);
  const inserted = indent + code;
  return { text: text.slice(0, start) + inserted + text.slice(end), start: start + inserted.length, end: start + inserted.length };
}

/** Tab: four spaces at the caret, or one level for every selected line. */
export function indentSelection(text: string, start: number, end: number): Edit {
  if (start === end) {
    return { text: text.slice(0, start) + INDENT + text.slice(end), start: start + INDENT.length, end: start + INDENT.length };
  }
  const first = lineStart(text, start);
  const selected = text.slice(first, end);
  const replaced = selected.replace(/^/gm, INDENT);
  return {
    text: text.slice(0, first) + replaced + text.slice(end),
    start: start + INDENT.length,
    end: end + replaced.length - selected.length,
  };
}

/** Shift+Tab: up to one level off every selected line. */
export function dedentSelection(text: string, start: number, end: number): Edit {
  const first = lineStart(text, start);
  const last = lineEnd(text, end);
  const selected = text.slice(first, last);
  const replaced = selected.replace(/^ {1,4}/gm, "");
  const removedFirst = (/^ {1,4}/.exec(selected)?.[0] ?? "").length;
  return {
    text: text.slice(0, first) + replaced + text.slice(last),
    start: Math.max(first, start - removedFirst),
    end: Math.max(first, end - (selected.length - replaced.length)),
  };
}

/** Enter: keep the line's indent, one level deeper after a colon. */
export function newLine(text: string, start: number, end: number): Edit {
  const line = text.slice(lineStart(text, start), start);
  const indent = /^ */.exec(line)?.[0] ?? "";
  const inserted = `\n${indent}${line.trimEnd().endsWith(":") ? INDENT : ""}`;
  return { text: text.slice(0, start) + inserted + text.slice(end), start: start + inserted.length, end: start + inserted.length };
}

export type Token = { kind: "plain" | "keyword" | "string" | "comment" | "number" | "self" | "name"; text: string };

const KEYWORDS = new Set(
  "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield".split(" "),
);
const TOKENS = /(#[^\n]*)|("""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)|\b(\d[\d_.]*)\b|\b([A-Za-z_]\w*)\b/g;

/** Python source split into coloured runs; joined, the runs are the source again. */
export function tokenize(source: string): Token[] {
  const tokens: Token[] = [];
  let at = 0;
  let previous = "";
  for (const match of source.matchAll(TOKENS)) {
    if (match.index > at) {
      tokens.push({ kind: "plain", text: source.slice(at, match.index) });
    }
    const [whole, comment, string, number, word] = match;
    if (comment !== undefined) {
      tokens.push({ kind: "comment", text: whole });
    } else if (string !== undefined) {
      tokens.push({ kind: "string", text: whole });
    } else if (number !== undefined) {
      tokens.push({ kind: "number", text: whole });
    } else if (word === "self") {
      tokens.push({ kind: "self", text: whole });
    } else if (previous === "def" || previous === "class") {
      tokens.push({ kind: "name", text: whole });
    } else {
      tokens.push({ kind: KEYWORDS.has(word) ? "keyword" : "plain", text: whole });
    }
    previous = word ?? "";
    at = match.index + whole.length;
  }
  if (at < source.length) {
    tokens.push({ kind: "plain", text: source.slice(at) });
  }
  return tokens;
}

/** A script's class also names its file. */
export function validClassName(name: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name);
}
