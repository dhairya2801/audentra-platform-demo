/**
 * Pure grading for the READ generalization suite: ground-truth templates,
 * per-turn checks and failure classification. Kept free of I/O so it can be
 * unit-tested (`node --test tools/edward-eval/test`) and reused by the
 * exporter.
 */

// ---------------------------------------------------------------------------
// Ground-truth templates
// ---------------------------------------------------------------------------

export function gtLookup(truth, path) {
  let value = truth;
  for (const part of path.split(".")) {
    if (value == null) return undefined;
    value = Array.isArray(value) ? value[Number(part)] : value[part];
  }
  return value;
}

const escapeRegex = (text) => String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function numberPattern(value) {
  const digits = String(Math.trunc(Number(value)));
  if (digits.length <= 3) return `\\b${digits}\\b`;
  let out = "";
  for (let i = 0; i < digits.length; i += 1) {
    out += digits[i];
    const remaining = digits.length - 1 - i;
    if (remaining > 0 && remaining % 3 === 0) out += ",?";
  }
  return `\\b${out}\\b`;
}

/** ±2 % (min ±2) alternation for counts that move with now(). */
export function tolerantNumberPattern(value) {
  const number = Math.trunc(Number(value));
  const slack = Math.max(2, Math.round(number * 0.02));
  const options = [];
  for (let candidate = number - slack; candidate <= number + slack; candidate += 1) {
    if (candidate >= 0) options.push(numberPattern(candidate));
  }
  return `(?:${options.join("|")})`;
}

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/** A date tolerant of ISO / "Sep 11" / "September 11, 2026" / "11 Sep" / "9/11" forms. */
export function datePattern(value) {
  const iso = String(value).slice(0, 10);
  const [year, month, day] = iso.split("-").map(Number);
  if (!year || !month || !day) return escapeRegex(iso);
  const monthName = MONTHS[month - 1];
  return (
    `(?:${iso}|${monthName}[a-z]*\\.?\\s+${day}(?:st|nd|rd|th)?(?:,?\\s+${year})?` +
    `|${day}(?:st|nd|rd|th)?\\s+(?:of\\s+)?${monthName}[a-z]*(?:,?\\s+${year})?|\\b${month}/${day}(?:/${year})?\\b` +
    `|\\b0?${month}/0?${day}\\b)`
  );
}

function listAt(truth, spec) {
  const [path, field] = spec.split("|").map((s) => s.trim());
  const value = gtLookup(truth, path);
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error(`Ground truth missing non-empty list at ${path}`);
  }
  return value.map((entry) => (field ? entry?.[field] : entry)).filter((v) => v != null && v !== "");
}

function missing(path) {
  return new Error(`Ground truth missing value at ${path}`);
}

/**
 * `{{gt:path}}`    scalar, regex-escaped
 * `{{re:path}}`    scalar used verbatim as a regex (patterns ground_truth.py derives)
 * `{{num:path}}`   number, thousands-separator tolerant
 * `{{num~:path}}`  number ±2 % (min ±2)
 * `{{date:path}}`  date in any common rendering
 * `{{any:path}}` / `{{any:path|field}}`  alternation over a list ("at least one")
 * `{{all:path}}` / `{{all:path|field}}`  every entry (lookahead chain)
 */
export function resolveTemplate(pattern, truth) {
  return pattern
    .replace(/\{\{num~:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value == null || Number.isNaN(Number(value))) throw missing(path);
      return tolerantNumberPattern(value);
    })
    .replace(/\{\{num:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value == null || Number.isNaN(Number(value))) throw missing(path);
      return numberPattern(value);
    })
    .replace(/\{\{date:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (!value) throw missing(path);
      return datePattern(value);
    })
    .replace(/\{\{any:([^}]+)\}\}/g, (_, spec) => {
      const entries = listAt(truth, spec);
      return `(?:${entries.map((entry) => escapeRegex(String(entry))).join("|")})`;
    })
    .replace(/\{\{all:([^}]+)\}\}/g, (_, spec) => {
      const entries = listAt(truth, spec);
      return entries.map((entry) => `(?=[\\s\\S]*${escapeRegex(String(entry))})`).join("");
    })
    .replace(/\{\{re:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value == null || value === "") throw missing(path);
      return String(value);
    })
    .replace(/\{\{gt:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value == null || value === "") throw missing(path);
      return escapeRegex(String(value));
    });
}

/** Templates inside a *question* are substituted with the raw value. */
export function resolveQuestion(question, truth) {
  return question.replace(/\{\{gt:([^}]+)\}\}/g, (_, path) => {
    const value = gtLookup(truth, path.trim());
    if (value == null) throw missing(path);
    return String(value);
  });
}

export function resolveExpectedStudent(reference, truth) {
  if (reference === null) return null;
  if (reference === undefined) return undefined;
  if (typeof reference === "string" && reference.startsWith("gt:")) {
    const value = gtLookup(truth, reference.slice(3));
    if (!value) throw new Error(`Ground truth missing student id at ${reference}`);
    return String(value);
  }
  return String(reference);
}

// ---------------------------------------------------------------------------
// Grading
// ---------------------------------------------------------------------------

export function blockText(block) {
  const parts = [];
  if (typeof block?.fallbackText === "string") parts.push(block.fallbackText);
  if (typeof block?.text === "string") parts.push(block.text);
  if (typeof block?.title === "string") parts.push(block.title);
  if (Array.isArray(block?.items)) {
    for (const item of block.items) {
      if (typeof item === "string") parts.push(item);
      else if (typeof item?.text === "string") parts.push(item.text);
      else if (item && typeof item === "object") parts.push(Object.values(item).filter((v) => typeof v === "string").join(" "));
    }
  }
  if (Array.isArray(block?.rows)) {
    for (const row of block.rows) parts.push(Object.values(row ?? {}).join(" "));
  }
  return parts.join("\n");
}

export function answerCorpus(payload) {
  const blocks = Array.isArray(payload.blocks) ? payload.blocks : [];
  return [payload.message ?? "", ...blocks.map(blockText)].join("\n");
}

export function matchFact(fact, corpus, truth) {
  // Typography does not change a claim. Normalize both sides so apostrophes in
  // canonical names and explicit denials obey the same existing expectations.
  const punctuation = (value) => value.replace(/[\u2018\u2019]/g, "'");
  return new RegExp(punctuation(resolveTemplate(fact.pattern, truth)), "i").test(punctuation(corpus));
}

export function toolCalls(trace) {
  return Array.isArray(trace?.toolCalls) ? trace.toolCalls : [];
}

export function executedTools(trace) {
  return toolCalls(trace)
    .filter((call) => call.status === "available")
    .map((call) => call.tool);
}

export function actionIntentsOf(payload) {
  return Array.isArray(payload?.actionIntents) ? payload.actionIntents : [];
}

const READ_FAILED = /couldn'?t (?:read|check|run|load|retrieve)|not available right now|left it out|unable to (?:read|retrieve|load)/i;

export function gradeTurn(turn, payload, trace, truth, actorKind) {
  const expect = turn.expect ?? {};
  const corpus = answerCorpus(payload);
  const message = String(payload.message ?? "");
  const failures = [];
  const softMisses = [];

  if (expect.requestTypes) {
    const actual = trace?.classification?.requestType ?? null;
    const extra = trace?.classification?.additionalRequestTypes ?? [];
    if (!expect.requestTypes.includes(actual) && !extra.some((t) => expect.requestTypes.includes(t))) {
      softMisses.push({ kind: "request_type", detail: `${actual} ∉ ${expect.requestTypes.join("|")}` });
    }
  }

  const tools = new Set(executedTools(trace));
  for (const tool of expect.requiredTools ?? []) {
    if (!tools.has(tool)) failures.push({ kind: "tool_not_called", detail: tool });
  }
  for (const group of expect.anyOfTools ?? []) {
    if (!group.some((tool) => tools.has(tool))) {
      failures.push({ kind: "tool_group_not_called", detail: group.join("|") });
    }
  }
  for (const tool of expect.forbiddenTools ?? []) {
    if (tools.has(tool)) failures.push({ kind: "forbidden_tool_called", detail: tool });
  }

  // Entity resolution is a staff-plane concept (students are always themselves).
  if (actorKind === "staff") {
    if ("resolvedStudentId" in expect) {
      const expected = resolveExpectedStudent(expect.resolvedStudentId, truth);
      const actual = payload.resolvedStudent?.id ?? null;
      if (expected === null && actual !== null) {
        failures.push({ kind: "arbitrary_resolution", detail: `resolved ${actual}` });
      } else if (typeof expected === "string" && actual !== expected) {
        failures.push({ kind: "wrong_student", detail: `${actual} ≠ ${expected}` });
      }
    }
    if (expect.resolvedStudentIn) {
      const entries = gtLookup(truth, expect.resolvedStudentIn);
      const ids = new Set((entries ?? []).map((entry) => String(entry?.id ?? entry)));
      const actual = payload.resolvedStudent?.id ?? null;
      if (actual === null) failures.push({ kind: "not_resolved", detail: "no student resolved" });
      else if (!ids.has(actual)) failures.push({ kind: "wrong_student", detail: `${actual} ∉ ${expect.resolvedStudentIn}` });
    }
  }

  for (const fact of expect.facts ?? []) {
    if (!matchFact(fact, corpus, truth)) {
      const record = { kind: "fact_missing", detail: fact.desc, pattern: fact.pattern };
      (fact.critical === false ? softMisses : failures).push(record);
    }
  }
  if (expect.factGroups) {
    const satisfied = expect.factGroups.some((group) => group.every((fact) => matchFact(fact, corpus, truth)));
    if (!satisfied) {
      failures.push({
        kind: "fact_missing",
        detail: `no acceptable fact group satisfied (${expect.factGroups.map((g) => g.map((f) => f.desc).join("+")).join(" | ")})`,
        pattern: expect.factGroups[0]?.[0]?.pattern,
      });
    }
  }
  for (const fact of expect.forbidden ?? []) {
    if (matchFact(fact, corpus, truth)) {
      failures.push({ kind: "forbidden_claim", detail: fact.desc, pattern: fact.pattern });
    }
  }

  if (expect.mustAsk && !/\?/.test(message) && !/which (?:one|student|of)|did you mean|clarify|specify/i.test(message)) {
    failures.push({ kind: "no_clarification", detail: "answer neither asks nor offers a choice" });
  }

  const intents = actionIntentsOf(payload);
  if (expect.actionIntents === "none" && intents.length > 0) {
    failures.push({ kind: "action_proposed", detail: `${intents.length} intent(s) proposed for a read/unsupported ask` });
  }
  if (expect.actionIntents === "any" && intents.length === 0) {
    failures.push({ kind: "action_not_proposed", detail: "no action intent proposed" });
  }
  if (expect.proposeOrClarify && intents.length === 0 && !/\?/.test(message)) {
    failures.push({ kind: "action_not_proposed", detail: "neither proposed an action nor asked a clarifying question" });
  }

  if (READ_FAILED.test(message) && !expect.allowUnavailable) {
    failures.push({ kind: "read_failed", detail: "answer reports a failed read" });
  }

  const grade = failures.length > 0 ? "FAIL" : softMisses.length > 0 ? "PARTIAL" : "PASS";
  return { grade, failures, softMisses };
}

/**
 * Where a failing turn went wrong, from the trace rather than the prose.
 */
export function classifyFailure(turn, failures, trace, evidenceText, truth) {
  const expect = turn.expect ?? {};
  const kinds = new Set(failures.map((f) => f.kind));
  const calls = toolCalls(trace);
  if (kinds.has("http")) return "transport";
  if (calls.some((call) => call.status === "timeout")) return "timeout";
  if (kinds.has("forbidden_claim")) return "hallucination";
  if (kinds.has("arbitrary_resolution") || kinds.has("wrong_student") || kinds.has("not_resolved")) return "entity";
  if (kinds.has("action_proposed") || kinds.has("action_not_proposed")) return "action";
  if (
    kinds.has("tool_not_called") ||
    kinds.has("tool_group_not_called") ||
    kinds.has("forbidden_tool_called") ||
    kinds.has("read_failed") ||
    calls.some((call) => call.status === "unavailable" || call.status === "error")
  ) {
    return "tool";
  }
  if (expect.requestTypes) {
    const actual = trace?.classification?.requestType ?? null;
    const extra = trace?.classification?.additionalRequestTypes ?? [];
    if (!expect.requestTypes.includes(actual) && !extra.some((t) => expect.requestTypes.includes(t))) {
      return "routing";
    }
  }
  if (turn.productGap) return "product_data";
  const missingFacts = failures.filter((f) => f.kind === "fact_missing" && f.pattern);
  if (missingFacts.length > 0 && evidenceText) {
    const inEvidence = missingFacts.every((f) => {
      try {
        return new RegExp(resolveTemplate(f.pattern, truth), "i").test(evidenceText);
      } catch {
        return false;
      }
    });
    return inEvidence ? "composition" : "query";
  }
  if (kinds.has("no_clarification")) return "entity";
  return "query";
}

export function evidenceTextOf(trace) {
  const lines = Array.isArray(trace?.evidence) ? trace.evidence : [];
  const results = toolCalls(trace).map((call) =>
    typeof call.resultPreview === "string" ? call.resultPreview : JSON.stringify(call.result ?? call.data ?? ""),
  );
  return [...lines, ...results].join("\n");
}

