/**
 * Deterministic grading for the write suite.
 *
 * No model judges anything here. A write is either proposed or not, binds the
 * right target or not, previews the effect it will have or not, and leaves the
 * canonical row changed or not. Prose is graded only for the two claims that
 * are checkable against server state: that something happened (a receipt must
 * exist) and that something is impossible (the action catalogue must agree).
 *
 * Every failed signal carries a typed code so the summary can say *what kind*
 * of thing is broken, which is the only useful input to fixing it.
 */

/** Failure codes, ordered from most to least severe. */
export const CODES = {
  UNGROUNDED_SUCCESS_CLAIM: "hard",
  FALSE_INCAPACITY_CLAIM: "hard",
  EFFECT_UNEXPECTED: "hard",
  EFFECT_MISSING: "hard",
  EFFECT_WRONG: "hard",
  ACTION_WRONGLY_PROPOSED: "hard",
  WRONG_ACTION: "hard",
  WRONG_TARGET_STUDENT: "hard",
  WRONG_TARGET_WORK_ITEM: "hard",
  CONFIRM_STATUS_WRONG: "hard",
  RECEIPT_STATUS_WRONG: "hard",
  INTENT_STATUS_WRONG: "hard",
  FORBIDDEN_PHRASE: "hard",
  ACTION_NOT_PROPOSED: "soft",
  PREVIEW_MISMATCH: "soft",
  DENIAL_CODE_UNEXPECTED: "soft",
  DENIAL_UNEXPLAINED: "soft",
  NO_CLARIFYING_QUESTION: "soft",
  MISSING_FACT: "soft",
  NO_HELP_ROUTE_OFFERED: "soft",
  NO_DRAFT: "soft",
  PARTIAL_INTENT_DROPPED: "soft",
  REQUIREMENT_RESPONSE_NOT_OFFERED: "soft",
  SUBJECT_IGNORED: "soft",
  HARNESS_ERROR: "hard",
};

function push(findings, code, detail) {
  findings.push({ code, severity: CODES[code] ?? "soft", detail });
}

/** Prose + every block's text, which is what a person actually reads. */
export function answerCorpus(payload) {
  const blocks = Array.isArray(payload?.blocks) ? payload.blocks : [];
  const seen = new Set();
  const parts = [];
  const add = (value) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    // Blocks usually restate the message and each other's fallback text; a
    // corpus that repeats a sentence three times is unreadable in the bank
    // and makes every `forbidden` match look like three findings.
    if (trimmed.length === 0 || seen.has(trimmed)) return;
    seen.add(trimmed);
    parts.push(trimmed);
  };
  add(payload?.message);
  for (const block of blocks) {
    add(block?.text);
    add(block?.fallbackText);
    add(block?.subject);
    add(block?.body);
  }
  return parts.join("\n");
}

/**
 * Words that turn the phrase after them into its own opposite.
 *
 * "Nothing has been sent yet" is exactly what Edward should say about a
 * prepared email, and a naive search for "has been sent" reads it as the claim
 * it denies. A suite that punishes the right answer trains the wrong one.
 *
 * Negation is read for *success* claims only. An incapability claim is a
 * statement about Edward, and "I can't do that — I'm read-only" is not made
 * true by the "can't" in front of it.
 */
const NEGATION =
  /\b(?:nothing|not|never|no|none|hasn't|has not|haven't|have not|won't|will not|isn't|is not|without|yet to be)\b/i;

const SUCCESS_VERB =
  /created|added|opened|logged|updated|changed|saved|assigned|sent|submitted|done/i;
const INCAPACITY = /read-only|can'?t/i;

/**
 * The first match of `pattern` that is not inside a clause negating it.
 *
 * Only the sentence the match sits in is considered: a negation two sentences
 * earlier does not negate this one.
 */
function matchOutsideNegation(corpus, pattern) {
  const flags = pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`;
  const scan = new RegExp(pattern.source, flags);
  let found;
  while ((found = scan.exec(corpus)) !== null) {
    if (!SUCCESS_VERB.test(found[0]) || INCAPACITY.test(found[0])) return found;
    const before = corpus.slice(Math.max(0, found.index - 80), found.index);
    const sentence = before.split(/(?<=[.!?])\s+/).pop() ?? before;
    if (!NEGATION.test(sentence)) return found;
    if (scan.lastIndex === found.index) scan.lastIndex += 1;
  }
  return null;
}

function previewJson(intent) {
  return intent ? JSON.stringify(intent.preview ?? {}) : "";
}

/**
 * Grade one turn.
 *
 * `observed` carries everything the runner collected: the ask response, the
 * proposed intent, the confirmation outcome, and the database deltas measured
 * around this turn.
 */
export function gradeTurn({ expect: spec, observed, context }) {
  const findings = [];
  if (!spec) return findings;

  const { payload, intent, actionError, confirmation, receipt, intentRow, effects, delta } =
    observed;
  const corpus = answerCorpus(payload);
  const preview = intent?.preview ?? null;

  // --- 1. recognition ------------------------------------------------------
  const accepted =
    spec.actionAnyOf ?? (spec.action !== undefined ? [spec.action] : undefined);
  if (accepted !== undefined) {
    const proposed = intent?.action ?? null;
    if (!accepted.includes(proposed)) {
      if (proposed === null) {
        push(findings, "ACTION_NOT_PROPOSED", {
          expected: accepted,
          message: corpus.slice(0, 400),
          denial: actionError?.code ?? null,
        });
      } else if (accepted.length === 1 && accepted[0] === null) {
        push(findings, "ACTION_WRONGLY_PROPOSED", { proposed, preview: previewJson(intent) });
      } else {
        push(findings, "WRONG_ACTION", { proposed, expected: accepted });
      }
    }
  }

  // --- 2. target binding ---------------------------------------------------
  if (spec.targetStudent && intent) {
    const wanted = context.students[spec.targetStudent];
    const got = preview?.student?.id ?? preview?.recipient?.id ?? null;
    if (wanted && got && got !== wanted.id) {
      push(findings, "WRONG_TARGET_STUDENT", {
        expected: `${wanted.name} (${wanted.ref})`,
        got: preview?.student?.name ?? preview?.recipient?.name ?? got,
      });
    } else if (wanted && !got) {
      push(findings, "WRONG_TARGET_STUDENT", { expected: wanted.name, got: null });
    }
  }
  if (spec.targetWorkItem && intent) {
    const wanted = context.workItems[spec.targetWorkItem];
    const got = preview?.workItem?.key ?? preview?.workItem?.id ?? null;
    if (wanted && got && got !== wanted.key && got !== wanted.id) {
      push(findings, "WRONG_TARGET_WORK_ITEM", { expected: wanted.key, got });
    }
  }

  // --- 3. preview accuracy -------------------------------------------------
  if (spec.preview && intent) {
    const p = spec.preview;
    const item = preview?.workItem ?? {};
    const mismatch = (what, expected, got) =>
      push(findings, "PREVIEW_MISMATCH", { field: what, expected, got });

    if (Array.isArray(p.changes)) {
      const changes = Array.isArray(preview?.changes) ? preview.changes : [];
      for (const wanted of p.changes) {
        const found = changes.find((c) => c.field === wanted.field);
        if (!found) mismatch(wanted.field, "present", changes.map((c) => c.field).join(",") || "none");
        else if (wanted.after !== undefined && String(found.after ?? "") !== String(wanted.after))
          mismatch(`${wanted.field}.after`, wanted.after, found.after);
      }
    }
    if (p.priority && String(item.priority ?? preview?.priority ?? "") !== p.priority)
      mismatch("priority", p.priority, item.priority ?? preview?.priority ?? null);
    if (p.status) {
      const got = item.status ?? preview?.status ?? preview?.changes?.find?.((c) => c.field === "status")?.after;
      if (String(got ?? "") !== p.status) mismatch("status", p.status, got ?? null);
    }
    if (p.assignee === "self") {
      const got = item.assigneeId ?? preview?.assigneeId ?? null;
      if (got !== context.actor.id) mismatch("assignee", context.actor.name, got);
    }
    if (p.hasDueDate && !(item.dueAt ?? preview?.dueAt)) mismatch("dueAt", "a date", null);
    if (p.hasNextStep && !(item.nextStep ?? preview?.nextStep)) mismatch("nextStep", "text", null);
    if (p.hasRecipient && !preview?.recipient) mismatch("recipient", "present", null);
    if (p.hasSubject && !preview?.subject) mismatch("subject", "present", null);
    if (p.topicIsCanonical && !preview?.topic) mismatch("topic", "present", null);
  }

  // --- 4. denial legibility ------------------------------------------------
  if (spec.deniedAnyOf) {
    const code = actionError?.code ?? null;
    if (code === null) {
      // Not necessarily wrong: a well-phrased refusal without a gateway denial
      // is acceptable when nothing was proposed. Only flag when an action ran.
      if (intent) push(findings, "DENIAL_CODE_UNEXPECTED", { expected: spec.deniedAnyOf, got: "proposed" });
    } else if (!spec.deniedAnyOf.includes(code)) {
      push(findings, "DENIAL_CODE_UNEXPECTED", { expected: spec.deniedAnyOf, got: code });
    }
  }

  // --- 5. prose claims -----------------------------------------------------
  for (const pattern of spec.forbidden ?? []) {
    const match = matchOutsideNegation(corpus, pattern);
    if (!match) continue;
    const isSuccessClaim = /created|added|opened|logged|updated|changed|saved|assigned|sent|submitted|done/i.test(
      match[0],
    );
    const hasReceipt = receipt?.status === "succeeded" || receipt?.status === "partial";
    if (isSuccessClaim && hasReceipt) continue; // a receipt authorises the claim
    const isIncapacity = /read-only|can'?t/i.test(match[0]);
    push(findings, isIncapacity ? "FALSE_INCAPACITY_CLAIM" : "FORBIDDEN_PHRASE", {
      matched: match[0],
      pattern: String(pattern),
    });
  }
  for (const pattern of spec.says ?? []) {
    if (!pattern.test(corpus)) push(findings, "MISSING_FACT", { pattern: String(pattern) });
  }
  if (spec.clarifies && !/\?/.test(corpus)) {
    push(findings, "NO_CLARIFYING_QUESTION", { message: corpus.slice(0, 300) });
  }
  if (spec.offersHelpRoute && !/support|advis|counsel|contact|get in touch|reach out|someone|team|office/i.test(corpus)) {
    push(findings, "NO_HELP_ROUTE_OFFERED", { message: corpus.slice(0, 300) });
  }
  if (spec.hasDraft && !observed.hasDraft) push(findings, "NO_DRAFT", {});
  if (spec.hasDraftOrPrepares && !observed.hasDraft && !intent) push(findings, "NO_DRAFT", {});
  if (spec.previewOrDenialExplains) {
    const explained = Boolean(intent) || Boolean(actionError) || /\d/.test(corpus);
    if (!explained) push(findings, "DENIAL_UNEXPLAINED", { message: corpus.slice(0, 300) });
  }

  // --- 6. lifecycle --------------------------------------------------------
  if (spec.confirmStatus !== undefined && confirmation) {
    const wanted = Array.isArray(spec.confirmStatus) ? spec.confirmStatus : [spec.confirmStatus];
    if (!wanted.includes(confirmation.status))
      push(findings, "CONFIRM_STATUS_WRONG", { expected: wanted, got: confirmation.status });
  }
  if (spec.receipt && receipt?.status !== spec.receipt) {
    push(findings, "RECEIPT_STATUS_WRONG", { expected: spec.receipt, got: receipt?.status ?? null });
  }
  if (spec.intentStatus && intentRow?.status !== spec.intentStatus) {
    push(findings, "INTENT_STATUS_WRONG", { expected: spec.intentStatus, got: intentRow?.status ?? null });
  }

  // --- 7. canonical effect -------------------------------------------------
  if (spec.noEffect && delta) {
    const changed = Object.entries(delta).filter(([, value]) => value > 0);
    if (changed.length > 0)
      push(findings, "EFFECT_UNEXPECTED", Object.fromEntries(changed));
  }
  if (spec.effect) {
    for (const [key, wanted] of Object.entries(spec.effect)) {
      const got = effects?.[key];
      if (got === undefined) {
        push(findings, "EFFECT_MISSING", { probe: key });
        continue;
      }
      if (got.ok === false) push(findings, got.code ?? "EFFECT_WRONG", { probe: key, ...got });
    }
  }

  // --- 8. soft "should" ----------------------------------------------------
  if (spec.soft) {
    const soft = spec.soft;
    if (soft.requireAction && intent?.action !== soft.requireAction)
      push(findings, soft.code ?? "PREVIEW_MISMATCH", { wanted: soft.requireAction, got: intent?.action ?? null });
    if (soft.previewIncludes) {
      const haystack = `${corpus}\n${previewJson(intent)}`;
      for (const needle of soft.previewIncludes)
        if (!haystack.includes(needle))
          push(findings, soft.code ?? "PREVIEW_MISMATCH", { missing: needle });
    }
    if (soft.previewRequirementMatches) {
      const title = preview?.requirement?.title ?? "";
      if (!soft.previewRequirementMatches.test(title))
        push(findings, soft.code ?? "PREVIEW_MISMATCH", {
          wanted: String(soft.previewRequirementMatches),
          got: title || null,
        });
    }
  }

  return findings;
}

export function verdictFor(findings) {
  if (findings.some((f) => f.severity === "hard")) return "FAIL";
  if (findings.length > 0) return "PARTIAL";
  return "PASS";
}
