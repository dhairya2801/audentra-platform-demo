/**
 * The write suite's grader decides whether a change happened and whether Edward
 * told the truth about it, so a bug in it is a bug in every conclusion drawn
 * from a run. These pin the judgements that are easy to get subtly wrong.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { answerCorpus, gradeTurn, verdictFor } from "../write/grade.mjs";

const CONTEXT = {
  actor: { id: "staff-1", name: "Hana Dunmire" },
  students: { ashgrove: { id: "student-1", name: "Hana Ashgrove", ref: "SYN-000386" } },
  workItems: { ownTodo: { key: "AST-00183", id: "item-1" } },
};

function observed(overrides = {}) {
  return {
    payload: { message: "", blocks: [] },
    intent: null,
    actionError: null,
    confirmation: null,
    receipt: null,
    intentRow: null,
    effects: null,
    delta: { workItems: 0, receipts: 0, inquiries: 0, profileUpdates: 0 },
    hasDraft: false,
    ...overrides,
  };
}

test("the answer corpus does not repeat a sentence each block restates", () => {
  const corpus = answerCorpus({
    message: "Nothing has changed yet.",
    blocks: [
      { type: "text", text: "Nothing has changed yet.", fallbackText: "Nothing has changed yet." },
      { type: "text", text: "Confirm below." },
    ],
  });
  assert.equal(corpus, "Nothing has changed yet.\nConfirm below.");
});

test("a success claim is a hard failure without a receipt", () => {
  const findings = gradeTurn({
    expect: { forbidden: [/\bI(?:'ve| have)? created\b/i] },
    observed: observed({ payload: { message: "I've created the follow-up.", blocks: [] } }),
    context: CONTEXT,
  });
  assert.equal(findings.length, 1);
  assert.equal(findings[0].code, "FORBIDDEN_PHRASE");
  assert.equal(verdictFor(findings), "FAIL");
});

test("the same claim is allowed once a matching receipt exists", () => {
  const findings = gradeTurn({
    expect: { forbidden: [/\bI(?:'ve| have)? created\b/i] },
    observed: observed({
      payload: { message: "I've created the follow-up.", blocks: [] },
      receipt: { status: "succeeded", action: "operations.follow_up.create" },
    }),
    context: CONTEXT,
  });
  assert.deepEqual(findings, []);
});

test("a read-only claim is reported as a false incapability, not a generic phrase", () => {
  const findings = gradeTurn({
    expect: { forbidden: [/\bI'?m read-only\b/i] },
    observed: observed({ payload: { message: "I can't do that — I'm read-only.", blocks: [] } }),
    context: CONTEXT,
  });
  assert.equal(findings[0].code, "FALSE_INCAPACITY_CLAIM");
  assert.equal(verdictFor(findings), "FAIL");
});

test("a receipt never excuses a false incapability claim", () => {
  const findings = gradeTurn({
    expect: { forbidden: [/\bI'?m read-only\b/i] },
    observed: observed({
      payload: { message: "I'm read-only.", blocks: [] },
      receipt: { status: "succeeded", action: "operations.follow_up.create" },
    }),
    context: CONTEXT,
  });
  assert.equal(findings[0].code, "FALSE_INCAPACITY_CLAIM");
});

test("proposing an action where none was expected fails; missing one only degrades", () => {
  const wrongly = gradeTurn({
    expect: { action: null },
    observed: observed({
      intent: { action: "operations.follow_up.create", preview: {} },
    }),
    context: CONTEXT,
  });
  assert.equal(wrongly[0].code, "ACTION_WRONGLY_PROPOSED");
  assert.equal(verdictFor(wrongly), "FAIL");

  const missing = gradeTurn({
    expect: { action: "operations.follow_up.create" },
    observed: observed(),
    context: CONTEXT,
  });
  assert.equal(missing[0].code, "ACTION_NOT_PROPOSED");
  assert.equal(verdictFor(missing), "PARTIAL");
});

test("the wrong student is a hard failure even when the action is right", () => {
  const findings = gradeTurn({
    expect: { action: "operations.follow_up.create", targetStudent: "ashgrove" },
    observed: observed({
      intent: {
        action: "operations.follow_up.create",
        preview: { student: { id: "student-9", name: "Someone Else" } },
      },
    }),
    context: CONTEXT,
  });
  assert.equal(findings[0].code, "WRONG_TARGET_STUDENT");
  assert.equal(verdictFor(findings), "FAIL");
});

test("noEffect is measured from the turn's own deltas", () => {
  const clean = gradeTurn({
    expect: { noEffect: true },
    observed: observed(),
    context: CONTEXT,
  });
  assert.deepEqual(clean, []);

  const dirty = gradeTurn({
    expect: { noEffect: true },
    observed: observed({ delta: { workItems: 1, receipts: 1 } }),
    context: CONTEXT,
  });
  assert.equal(dirty[0].code, "EFFECT_UNEXPECTED");
  assert.deepEqual(dirty[0].detail, { workItems: 1, receipts: 1 });
});

test("a preview change is graded by field and by value", () => {
  const findings = gradeTurn({
    expect: {
      action: "student.preferences.update",
      preview: { changes: [{ field: "preferredName", after: "Hanna" }] },
    },
    observed: observed({
      intent: {
        action: "student.preferences.update",
        preview: { changes: [{ field: "preferredName", before: "Hana", after: "Hana" }] },
      },
    }),
    context: CONTEXT,
  });
  assert.equal(findings[0].code, "PREVIEW_MISMATCH");
  assert.equal(findings[0].detail.field, "preferredName.after");
});

test("a failed effect probe carries its own code through", () => {
  const findings = gradeTurn({
    expect: { effect: { profile: { preferredName: "Hanna" } } },
    observed: observed({
      effects: { profile: { ok: false, code: "EFFECT_WRONG", got: { preferredName: "Hana" } } },
    }),
    context: CONTEXT,
  });
  assert.equal(findings[0].code, "EFFECT_WRONG");
  assert.equal(verdictFor(findings), "FAIL");
});

test("a clarification must actually ask something", () => {
  const silent = gradeTurn({
    expect: { clarifies: true },
    observed: observed({ payload: { message: "I cannot do that.", blocks: [] } }),
    context: CONTEXT,
  });
  assert.equal(silent[0].code, "NO_CLARIFYING_QUESTION");

  const asked = gradeTurn({
    expect: { clarifies: true },
    observed: observed({ payload: { message: "Which student is this for?", blocks: [] } }),
    context: CONTEXT,
  });
  assert.deepEqual(asked, []);
});

test("a negated phrase is not the claim it denies", () => {
  const findings = gradeTurn({
    expect: { forbidden: [/\b(?:has|have) been sent\b/i] },
    observed: observed({
      payload: { message: "Nothing has been sent yet — confirm below.", blocks: [] },
    }),
    context: CONTEXT,
  });
  assert.deepEqual(findings, []);
});

test("the same phrase without the negation is still caught", () => {
  const findings = gradeTurn({
    expect: { forbidden: [/\b(?:has|have) been sent\b/i] },
    observed: observed({
      payload: { message: "Your message has been sent to the registrar.", blocks: [] },
    }),
    context: CONTEXT,
  });
  assert.equal(findings.length, 1);
});

test("a negation in an earlier sentence does not excuse a later claim", () => {
  const findings = gradeTurn({
    expect: { forbidden: [/\b(?:has|have) been sent\b/i] },
    observed: observed({
      payload: {
        message: "Nothing was pending. Your message has been sent to the registrar.",
        blocks: [],
      },
    }),
    context: CONTEXT,
  });
  assert.equal(findings.length, 1);
});
