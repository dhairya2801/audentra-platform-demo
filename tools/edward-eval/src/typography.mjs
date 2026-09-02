/**
 * Fold typographic punctuation to ASCII before regex grading.
 *
 * Reasoning models write “can’t” with U+2019 and dashes as U+2014; the
 * banks' patterns were authored with straight quotes (`can'?t`). Grading the
 * folded text keeps the checks about meaning rather than about which
 * apostrophe a model prefers, and applies identically to every model.
 */
export function foldTypography(text) {
  return String(text ?? "")
    .replace(/[‘’‚‛′]/g, "'")
    .replace(/[“”„‟″]/g, '"')
    .replace(/[–—−]/g, "-")
    .replace(/…/g, "...")
    .replace(/ /g, " ");
}
