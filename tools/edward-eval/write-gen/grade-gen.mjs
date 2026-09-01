/**
 * Grading for the generalization suite.
 *
 * Everything the regression grader checks is checked here, unchanged, by
 * calling it. This module adds only expectations the new families need, as
 * *new optional spec fields* — the regression bank never uses them, so the
 * two suites cannot drift apart on shared semantics.
 */

import { CODES as BASE_CODES, gradeTurn as baseGradeTurn, verdictFor } from "../write/grade.mjs";

export { answerCorpus } from "../write/grade.mjs";
export { verdictFor };

export const CODES = {
  ...BASE_CODES,
  RESPONSE_KIND_WRONG: "soft",
  BLOCKER_CODE_WRONG: "soft",
  PREVIEW_LEAK: "soft",
};

function push(findings, code, detail) {
  findings.push({ code, severity: CODES[code] ?? "soft", detail });
}

function kindMatches(got, wanted) {
  if (wanted.endsWith("*")) return got.startsWith(wanted.slice(0, -1));
  return got === wanted;
}

export function gradeTurn({ expect: spec, observed, context }) {
  const findings = baseGradeTurn({ expect: spec, observed, context });
  if (!spec) return findings;
  const { payload, intent } = observed;

  // --- response kind: which deterministic surface answered ------------------
  if (spec.responseKind) {
    const wanted = Array.isArray(spec.responseKind) ? spec.responseKind : [spec.responseKind];
    const got = String(payload?.actionResponse?.kind ?? "");
    if (!wanted.some((kind) => kindMatches(got, kind))) {
      push(findings, "RESPONSE_KIND_WRONG", { expected: wanted, got: got || null });
    }
  }

  // --- negative target binding: the one student it must NOT act on ----------
  if (spec.notTargetStudent && intent) {
    const banned = context.students[spec.notTargetStudent];
    const got = intent.preview?.student?.id ?? intent.preview?.recipient?.id ?? null;
    if (banned && got && got === banned.id) {
      push(findings, "WRONG_TARGET_STUDENT", {
        boundTo: `${banned.name} (${banned.ref})`,
        detail: "bound to the one student this turn must not act on",
      });
    }
  }

  // --- preview must not contain these strings -------------------------------
  if (spec.soft?.previewExcludes && intent) {
    const haystack = JSON.stringify(intent.preview ?? {});
    for (const needle of spec.soft.previewExcludes) {
      if (haystack.includes(needle)) push(findings, "PREVIEW_LEAK", { found: needle });
    }
  }

  return findings;
}
