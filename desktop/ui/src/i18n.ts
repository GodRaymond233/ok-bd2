// Interface text goes through t(). Chinese source strings are the message ids, as in the
// repository's gettext catalogs (i18n/<locale>/LC_MESSAGES), and locales/ holds the client's own
// translations. The core always speaks Chinese: the client translates what it shows, so a text
// with values inside (an error naming a task) is matched by a catalog entry with {placeholders}.
import english from "./locales/en_US.json" with { type: "json" };

export type LanguagePreference = "Auto" | "zh_CN" | "en_US";
export type Language = "zh_CN" | "en_US";

type Values = Record<string, string | number>;

interface Pattern {
  match: RegExp;
  names: string[];
  template: string;
  weight: number;
}

const PLACEHOLDER = /\{(\w+)\}/g;
const OWN_CATALOGS: Record<Language, Record<string, string>> = { zh_CN: {}, en_US: english };

let preference: LanguagePreference = "Auto";
let language: Language = "zh_CN";
let catalog: Record<string, string> = {};
let patterns: Pattern[] = [];

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Entries with {placeholders}, the most specific first: "{a}：{b}" only takes what's left. */
function compile(messages: Record<string, string>): Pattern[] {
  return Object.entries(messages)
    .filter(([source]) => source.search(PLACEHOLDER) >= 0)
    .map(([source, template]) => {
      const names = [...source.matchAll(PLACEHOLDER)].map((match) => match[1]);
      const parts = source.split(PLACEHOLDER).filter((_, index) => index % 2 === 0);
      return { match: new RegExp(`^${parts.map(escapeRegex).join("(.+?)")}$`), names, template, weight: parts.join("").length };
    })
    .sort((a, b) => b.weight - a.weight);
}

function fill(template: string, values: Values): string {
  return template.replace(PLACEHOLDER, (whole, name: string) => (name in values ? String(values[name]) : whole));
}

/** The language a preference means here: Chinese on a Chinese system, otherwise English. */
export function resolveLanguage(preference: LanguagePreference, system: string): Language {
  if (preference !== "Auto") {
    return preference;
  }
  return system.toLowerCase().startsWith("zh") ? "zh_CN" : "en_US";
}

/** `taskMessages` is the repository's gettext catalog, which the core reads for the client. */
export function setLanguage(next: Language, taskMessages: Record<string, string> = {}): void {
  language = next;
  catalog = next === "zh_CN" ? {} : { ...taskMessages, ...OWN_CATALOGS[next] };
  patterns = compile(catalog);
}

export function currentLanguage(): Language {
  return language;
}

/** The choice in 设置, as the core keeps it, applied to the page. */
export function applyLanguage(
  state: { language: LanguagePreference; catalogs: Record<string, Record<string, string>> },
  system: string = navigator.language,
): void {
  preference = state.language;
  const next = resolveLanguage(preference, system);
  setLanguage(next, state.catalogs[next]);
  document.documentElement.lang = next === "zh_CN" ? "zh-CN" : "en";
}

export function languagePreference(): LanguagePreference {
  return preference;
}

/** Interface text, by its Chinese source; `values` fill its {placeholders}. */
export function t(text: string, values?: Values): string {
  const message = catalog[text] ?? text;
  return values ? fill(message, values) : message;
}

/** Text from the core or a task, line by line, with the values inside translated too. */
export function coreText(text: string): string {
  if (!patterns.length) {
    return text;
  }
  if (text.includes("\n")) {
    return text.split("\n").map(coreText).join("\n");
  }
  const exact = catalog[text];
  if (exact !== undefined) {
    return exact;
  }
  for (const pattern of patterns) {
    const found = pattern.match.exec(text);
    if (found) {
      return fill(pattern.template, Object.fromEntries(pattern.names.map((name, index) => [name, coreText(found[index + 1])])));
    }
  }
  return text;
}
