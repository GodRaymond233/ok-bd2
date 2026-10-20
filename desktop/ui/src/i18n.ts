// Interface text goes through t() from day one. Chinese source strings are the message ids,
// as in the repository's gettext catalogs (i18n/<locale>/LC_MESSAGES); a build step can later
// turn a desktop catalog into this table. Only Chinese ships in stage 1.
const catalog: Record<string, string> = {};

export function t(text: string): string {
  return catalog[text] ?? text;
}
