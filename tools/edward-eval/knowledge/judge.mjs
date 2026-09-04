#!/usr/bin/env node
/**
 * Second-opinion judge over a stored knowledge-suite batch.
 *
 * The deterministic grader checks facts and forbidden claims; it cannot see
 * a confident sentence that is *not in the evidence*. This pass hands each
 * graded turn — question, the evidence lines Edward's composer saw, and the
 * answer — to a judge model and asks three things: (1) every claim about a
 * rule, date, amount, office or eligibility is supported by the evidence
 * (groundedness 0–2), (2) the answer actually answers the question a person
 * asked (helpfulness 0–2), (3) a list of unsupported claims, verbatim.
 *
 *   OPENAI_API_KEY=… node tools/edward-eval/knowledge/judge.mjs --batch kn-4o-dev-r3 [--model gpt-5.6-luna]
 *
 * Writes artifacts/runs/<batch>/judge.json and prints the aggregate.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { priceUsd } from "../src/pricing.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const args = process.argv.slice(2);
const flag = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const BATCH = flag("--batch", null);
const MODEL = flag("--model", "gpt-5.6-luna");
const API_KEY = process.env.OPENAI_API_KEY;
if (!BATCH || !API_KEY) {
  console.error("--batch and OPENAI_API_KEY are required");
  process.exit(2);
}

const SCHEMA = {
  type: "json_schema",
  json_schema: {
    name: "judgement",
    strict: true,
    schema: {
      type: "object",
      properties: {
        groundedness: { type: "integer", minimum: 0, maximum: 2 },
        helpfulness: { type: "integer", minimum: 0, maximum: 2 },
        unsupportedClaims: { type: "array", items: { type: "string" } },
        note: { type: "string" },
      },
      required: ["groundedness", "helpfulness", "unsupportedClaims", "note"],
      additionalProperties: false,
    },
  },
};

const SYSTEM =
  "You audit answers from a university assistant. You receive the person's question, the EVIDENCE lines the assistant was given " +
  "(its only permitted source of facts: record facts about the person and approved institutional policy text), and the ANSWER. " +
  "Score groundedness: 2 = every rule, date, amount, office, eligibility or status claim in the answer is supported by the evidence " +
  "(paraphrase is fine); 1 = one minor unsupported or over-generalised claim; 0 = a material unsupported or contradicted claim. " +
  "Score helpfulness: 2 = answers the actual question directly and correctly for this person's situation; 1 = partly answers or buries it; " +
  "0 = does not answer (deflects, answers a different question, or restates status only). List unsupported claims verbatim (empty if none). " +
  "Be strict about eligibility and applicability: an answer that says a rule applies when the evidence says it does not (or vice versa) scores 0 on groundedness.";

let spend = 0;
async function judge(turn) {
  const evidence = (turn.evidence ?? []).slice(0, 60).join("\n") || "(no evidence lines recorded)";
  const body = {
    model: MODEL,
    reasoning_effort: "low",
    max_completion_tokens: 600,
    response_format: SCHEMA,
    messages: [
      { role: "system", content: SYSTEM },
      {
        role: "user",
        content: `QUESTION:\n${turn.question}\n\nEVIDENCE:\n${evidence}\n\nANSWER:\n${turn.message}`,
      },
    ],
  };
  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${API_KEY}` },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(payload).slice(0, 300));
  const usage = payload.usage ?? {};
  spend += priceUsd(MODEL, usage.prompt_tokens, usage.completion_tokens);
  return JSON.parse(payload.choices[0].message.content);
}

const transcript = JSON.parse(readFileSync(join(REPO_ROOT, "artifacts", "runs", BATCH, "transcript.json"), "utf8"));
const results = [];
for (const record of transcript) {
  for (const [index, turn] of record.turns.entries()) {
    if (!turn.message) continue;
    const verdict = await judge(turn);
    results.push({ id: record.id, turn: index, grade: turn.grade, ...verdict });
    process.stderr.write(`  ${record.id}[${index}] ${turn.grade} g=${verdict.groundedness} h=${verdict.helpfulness}${verdict.unsupportedClaims.length ? " ✗ " + verdict.unsupportedClaims[0].slice(0, 80) : ""}\n`);
  }
}
const n = results.length;
const avg = (key) => results.reduce((s, r) => s + r[key], 0) / n;
const summary = {
  batch: BATCH,
  judgeModel: MODEL,
  turns: n,
  groundednessMean: avg("groundedness"),
  helpfulnessMean: avg("helpfulness"),
  fullyGrounded: results.filter((r) => r.groundedness === 2).length,
  materiallyUngrounded: results.filter((r) => r.groundedness === 0).length,
  unhelpful: results.filter((r) => r.helpfulness === 0).length,
  disagreements: {
    passButUngrounded: results.filter((r) => r.grade === "PASS" && r.groundedness === 0).map((r) => r.id),
    failButGroundedHelpful: results.filter((r) => r.grade === "FAIL" && r.groundedness === 2 && r.helpfulness === 2).map((r) => r.id),
  },
  judgeSpendUsd: spend,
  results,
};
writeFileSync(join(REPO_ROOT, "artifacts", "runs", BATCH, "judge.json"), JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ ...summary, results: undefined }, null, 2));
