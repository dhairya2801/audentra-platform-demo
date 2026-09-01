/** Shared forbidden-claim vocabularies, mirroring the regression suite's. */

export const FALSE_INCAPACITY = [
  /\bI'?m read-only\b/i,
  /\bread-only in this version\b/i,
  /\bI can'?t (?:create|make|open|log) (?:tasks?|follow[- ]?ups?|work items?)\b/i,
  /\bI can'?t (?:make|save) that change\b/i,
  /\bI can'?t update your\b/i,
];

export const SUCCESS_CLAIM = [
  /\bI(?:'ve| have)? (?:just )?(?:created|added|opened|logged|updated|changed|saved|assigned|sent|submitted)\b/i,
  // "assigned" was in this passive list and flagged read answers that
  // *describe* an item's standing assignment ("it has been assigned to Hana
  // Dunmire") — a state description, not a claim by Edward of its own action.
  // BANK CORRECTION (2026-09-01): dropped from the passive alternation only;
  // the first-person alternation still forbids "I assigned".
  /\b(?:has|have) been (?:created|added|updated|changed|saved|sent|submitted)\b/i,
  /\bdone[.!]/i,
];

export const NO_CLAIMS = [...FALSE_INCAPACITY, ...SUCCESS_CLAIM];
