#!/usr/bin/env node
/**
 * Export both 100-scenario question banks as a spreadsheet-pasteable table.
 *
 *   node tools/edward-eval/export-question-bank.mjs [outDir]
 *
 * Writes `edward-eval-question-bank.tsv` (tab-separated — paste it straight
 * into Google Sheets or Excel; tabs avoid the quoting problems commas cause in
 * question text) and `edward-eval-question-bank.csv` alongside it.
 *
 * The point of this export over the raw case files: the `{{gt:…}}` / `{{f:…}}`
 * ground-truth templates are **resolved**, so a reader sees the actual expected
 * values ("685", "Pay the enrollment deposit | Upload an identity document")
 * rather than the regex the grader runs. Edward's answer and grade from the
 * final run are attached to each turn, so the sheet reads
 * question → expected → actual → grade.
 */

import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deriveFacts } from "./src/facts.mjs";
import { CASES as STUDENT_CASES } from "./student-v3/cases.mjs";
import { HOLDOUT_CASES as STUDENT_HOLDOUT } from "./student-v3/holdout-cases.mjs";
import { CASES as STAFF_CASES } from "./staff-db/cases-v2.mjs";
import { HOLDOUT_CASES as STAFF_HOLDOUT } from "./staff-db/holdout-cases-v2.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..");
const OUT_DIR = process.argv[2] ?? join(REPO_ROOT, "docs");

// --- ground truth -----------------------------------------------------------

const studentSnapshots = JSON.parse(
  readFileSync(join(REPO_ROOT, "artifacts", "student-v3-snapshots.json"), "utf8"),
);
const studentFacts = Object.fromEntries(
  Object.entries(studentSnapshots).map(([persona, snapshot]) => [persona, deriveFacts(snapshot)]),
);
const staffTruth = JSON.parse(
  readFileSync(join(REPO_ROOT, "artifacts", "staff-db-eval", "ground-truth.json"), "utf8"),
);

function lookup(root, path) {
  let value = root;
  for (const part of String(path).trim().split(".")) {
    if (value == null) return undefined;
    value = Array.isArray(value) ? value[Number(part)] : value[part];
  }
  return value;
}

/** Every ground-truth reference in a pattern, resolved to its actual value. */
function resolveReferences(pattern, root) {
  const found = [];
  const seen = new Set();
  const push = (path, value) => {
    if (value === undefined || value === null) return;
    const rendered = Array.isArray(value) ? value.map(String).join(" | ") : String(value);
    const key = `${path}=${rendered}`;
    if (seen.has(key)) return;
    seen.add(key);
    found.push(`${path.split(".").pop()}: ${rendered}`);
  };
  for (const match of String(pattern).matchAll(/\{\{(?:gt|num|f|n|any|all):([^}]+)\}\}/g)) {
    const value = lookup(root, match[1]);
    if (value === undefined || value === null) {
      unresolved.push(match[1].trim());
      continue;
    }
    push(match[1].trim(), value);
  }
  return found;
}

/** Any ground-truth path an expectation names that the truth file lacks. A
 *  silent blank cell here would look like "this case asserts nothing". */
const unresolved = [];

/** The wording a hand-written pattern accepts, for readability.
 *
 *  Only regex *syntax* is stripped — the alternatives inside a group are the
 *  interesting part ("resubmi | rejected | returned"), so removing whole
 *  groups would leave the cell blank and make the check look like nothing.
 */
function readableLiterals(pattern) {
  const words = String(pattern)
    .replace(/\{\{[^}]+\}\}/g, " ")
    .replace(/\(\?(?:<[=!]|[=!])[^)]*?\)/g, " ") // drop lookarounds, keep plain (?: groups
    .replace(/\(\?:|\\[bdswBDSW]|[\\^$.*+?()[\]{}|]/g, " ")
    .replace(/\d/g, " ")
    .split(/[^a-zA-Z'-]+/)
    .map((word) => word.replace(/^[-']+|[-']+$/g, "").trim())
    .filter((word) => word.length > 3 && !STOPWORDS.has(word.toLowerCase()));
  return [...new Set(words)].slice(0, 10).join(" | ");
}

// Regex glue and quantifier remnants that carry no meaning for a reader.
const STOPWORDS = new Set([
  "that",
  "this",
  "with",
  "from",
  "have",
  "your",
  "they",
  "them",
  "been",
  "does",
  "will",
  "hasn",
  "haven",
  "doesn",
  "isn",
  "aren",
  "didn",
  "don",
  "won",
]);

// --- final-run answers ------------------------------------------------------

function loadRun(batch) {
  const path = join(REPO_ROOT, "artifacts", "runs", batch, "transcript.json");
  if (!existsSync(path)) return new Map();
  const records = JSON.parse(readFileSync(path, "utf8"));
  return new Map(records.map((record) => [record.id, record]));
}

const runs = {
  "student-development": loadRun("student-v3-final"),
  "student-holdout": loadRun("student-v3-holdout-final"),
  "staff-development": loadRun("staff-v2-final"),
  "staff-holdout": loadRun("staff-v2-holdout-final"),
};

// --- row construction -------------------------------------------------------

const clean = (value) =>
  String(value ?? "")
    .replace(/[\t\r\n]+/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();

function expectedBehaviour(expect, persona) {
  const parts = [];
  if (expect.requestTypes) parts.push(`intent ∈ {${expect.requestTypes.join(", ")}}`);
  if (expect.requiredTools?.length) parts.push(`must read: ${expect.requiredTools.join(", ")}`);
  for (const group of expect.anyOfTools ?? []) parts.push(`must read one of: ${group.join(" | ")}`);
  if (expect.forbiddenTools?.length) parts.push(`must NOT read: ${expect.forbiddenTools.join(", ")}`);
  for (const [tool, pattern] of Object.entries(expect.toolArgPatterns ?? {})) {
    parts.push(`${tool} argument must match: ${pattern}`);
  }
  if (persona === "Staff" && "resolvedStudentId" in expect) {
    if (expect.resolvedStudentId === null) parts.push("must NOT resolve any student");
    else {
      const value = String(expect.resolvedStudentId).startsWith("gt:")
        ? lookup(staffTruth, String(expect.resolvedStudentId).slice(3).replace(/\.id$/, ".name"))
        : expect.resolvedStudentId;
      parts.push(`must resolve: ${value ?? expect.resolvedStudentId}`);
    }
  }
  return parts.join(" · ");
}

function buildRows(cases, persona, suite, runKey) {
  const run = runs[runKey];
  const rows = [];
  for (const testCase of cases) {
    const record = run.get(testCase.id);
    const root = persona === "Staff" ? staffTruth : studentFacts[testCase.persona] ?? {};
    testCase.turns.forEach((turn, index) => {
      const expect = turn.expect ?? {};
      const facts = [...(expect.facts ?? []), ...(expect.factGroups ?? []).flat()];
      const values = [];
      for (const fact of facts) values.push(...resolveReferences(fact.pattern, root));
      const observed = record?.turns?.[index];
      rows.push([
        testCase.id,
        persona,
        suite,
        testCase.category,
        persona === "Staff" ? "aster-demo (2,577 students)" : testCase.persona,
        testCase.turns.length > 1 ? `${index + 1} of ${testCase.turns.length}` : "1 of 1",
        clean(turn.question),
        clean(expectedBehaviour(expect, persona)),
        clean(
          facts.length === 0 && testCase.turns.length > 1 && index === 0
            ? "(context-setting turn — the assertion is on the next turn)"
            : facts
                .map((fact) => `${fact.desc}${fact.critical ? " [critical]" : ""}`)
                .join(" · "),
        ),
        clean([...new Set(values)].join(" · ")),
        clean(
          facts
            .map((fact) =>
              // A purely template-driven fact is already spelled out in the
              // resolved-values column; printing its raw template here adds
              // noise, not information.
              /^\s*\{\{[^}]+\}\}\s*$/.test(String(fact.pattern))
                ? ""
                : readableLiterals(fact.pattern) || String(fact.pattern).slice(0, 120),
            )
            .filter(Boolean)
            .join(" · "),
        ),
        clean((expect.forbidden ?? []).map((fact) => fact.desc).join(" · ")),
        testCase.critical ? "yes" : "",
        clean(observed?.message ?? ""),
        observed?.requestType ?? "",
        clean((observed?.tools ?? []).join(", ")),
        observed?.grade ?? "",
      ]);
    });
  }
  return rows;
}

const HEADERS = [
  "scenario_id",
  "persona",
  "suite",
  "category",
  "data_context",
  "turn",
  "user_question",
  "expected_behaviour",
  "expected_facts",
  "expected_values_from_backend",
  "accepted_wording_cues",
  "forbidden",
  "critical_case",
  "edward_answer_final_run",
  "classified_intent",
  "tools_executed",
  "grade",
];

const rows = [
  ...buildRows(STUDENT_CASES, "Student", "development", "student-development"),
  ...buildRows(STUDENT_HOLDOUT, "Student", "holdout", "student-holdout"),
  ...buildRows(STAFF_CASES, "Staff", "development", "staff-development"),
  ...buildRows(STAFF_HOLDOUT, "Staff", "holdout", "staff-holdout"),
];

mkdirSync(OUT_DIR, { recursive: true });

const tsv = [HEADERS, ...rows].map((row) => row.join("\t")).join("\n");
writeFileSync(join(OUT_DIR, "edward-eval-question-bank.tsv"), `${tsv}\n`);

const csvCell = (value) => `"${String(value).replace(/"/g, '""')}"`;
const csv = [HEADERS, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
writeFileSync(join(OUT_DIR, "edward-eval-question-bank.csv"), `${csv}\n`);

const cases = STUDENT_CASES.length + STUDENT_HOLDOUT.length + STAFF_CASES.length + STAFF_HOLDOUT.length;
if (unresolved.length > 0) {
  console.error(
    `Unresolved ground-truth paths (${unresolved.length}): ` +
      [...new Set(unresolved)].join(", "),
  );
  process.exitCode = 1;
}
console.log(`${cases} scenarios → ${rows.length} turns`);
console.log(`  ${join(OUT_DIR, "edward-eval-question-bank.tsv")}`);
console.log(`  ${join(OUT_DIR, "edward-eval-question-bank.csv")}`);
