#!/usr/bin/env node
/**
 * Edward evaluation CLI.
 *
 *   node tools/edward-eval/run.mjs --batch baseline
 *   node tools/edward-eval/run.mjs --tier smoke --no-judge
 *   node tools/edward-eval/run.mjs --categories cross_domain,conflict
 *   node tools/edward-eval/run.mjs --id housing-cross-domain-017 --verbose
 *
 * Writes transcript.json, summary.json, and snapshots.json to
 * artifacts/runs/<batch>/. Exits non-zero when a `critical` invariant case
 * fails, regardless of aggregate score. Aborts hard at the spend ceiling.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deriveFacts, groundTruthLines } from "./src/facts.mjs";
import { gradeTranscripts, finalizeRecords } from "./src/grade.mjs";
import { applicableDimensions, buildJudgeEvidence, judgeTurn } from "./src/judge.mjs";
import {
  QUESTIONS,
  questionsByCategory,
  questionsByIds,
  questionsByTier,
} from "./src/questions.mjs";
import { runCases } from "./src/runner.mjs";
import {
  SpendCeilingExceededError,
  SpendLedger,
  costTrackedFetch,
  createConcurrencyLimiter,
} from "./src/spend-ledger.mjs";
import { accountRun } from "./src/accounting.mjs";
import { printInspection } from "./src/inspect-view.mjs";
import { summarise, printSummary } from "./src/summarize.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const artifacts = join(repoRoot, "artifacts");

function parseArgs(argv) {
  const args = {
    batch: "adhoc",
    categories: [],
    ids: [],
    tier: null,
    limit: 0,
    judge: true,
    repeat: 1,
    judgeConcurrency: 4,
    verbose: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--categories") args.categories = argv[++index].split(",");
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--tier") args.tier = argv[++index];
    else if (flag === "--limit") args.limit = Number(argv[++index]);
    else if (flag === "--repeat") args.repeat = Number(argv[++index]);
    else if (flag === "--judge-concurrency") args.judgeConcurrency = Number(argv[++index]);
    else if (flag === "--no-judge") args.judge = false;
    else if (flag === "--verbose") args.verbose = true;
    else {
      console.error(`Unknown flag ${flag}`);
      process.exit(2);
    }
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
const apiKey = process.env.OPENAI_API_KEY;
if (!apiKey && args.judge) {
  console.error(
    "OPENAI_API_KEY is required to judge. Use --no-judge for a deterministic-only run.",
  );
  process.exit(1);
}

const ledger = new SpendLedger({
  file: join(artifacts, "spend.json"),
  batch: args.batch,
});
const startingSpend = ledger.totalUsd;
console.log(
  `batch "${args.batch}" starting. cumulative spend so far $${startingSpend.toFixed(4)} of $${ledger.ceilingUsd.toFixed(2)} tracked ($${ledger.absoluteCeilingUsd.toFixed(2)} absolute).`,
);

let selected =
  args.ids.length > 0
    ? questionsByIds(args.ids)
    : args.categories.length > 0
      ? questionsByCategory(args.categories)
      : questionsByTier(args.tier);
if (args.repeat > 1) {
  selected = Array.from({ length: args.repeat }, (_, run) =>
    selected.map((item) => ({ ...item, id: `${item.id}#${run + 1}` })),
  ).flat();
}
if (args.limit > 0) selected = selected.slice(0, args.limit);
console.log(
  `${selected.length} case(s) / ${selected.reduce((sum, item) => sum + item.turns.length, 0)} turn(s) selected of ${QUESTIONS.length} cases in the suite.`,
);
if (selected.length === 0) process.exit(2);

let transcripts = [];
let snapshots = {};
let aborted = null;
try {
  const result = await runCases({
    cases: selected,
    ledger,
    onProgress: (record, done, total) => {
      if (done % 20 === 0 || done >= total) {
        console.log(`  asked ${done}/${total} turns  spend $${ledger.totalUsd.toFixed(4)}`);
      }
    },
  });
  transcripts = result.transcripts;
  snapshots = result.snapshots;
} catch (error) {
  if (error instanceof SpendCeilingExceededError) {
    aborted = error.message;
    console.error(`ABORTED: ${error.message}`);
  } else {
    throw error;
  }
}

const factsByPersona = Object.fromEntries(
  Object.entries(snapshots).map(([persona, snapshot]) => [persona, deriveFacts(snapshot)]),
);

// Deterministic grading first: costs nothing and decides the invariants that
// must never be left to a model.
const results = gradeTranscripts(transcripts, factsByPersona);

// Judge pass: every judged turn, with conversation context and canonical
// ground truth. Judge spend routes through the tracked fetch.
const judgeTotals = { calls: 0, promptTokens: 0, completionTokens: 0, usd: 0 };
if (args.judge && !aborted) {
  const judgeFetch = costTrackedFetch(ledger, {
    onCall: ({ usd, promptTokens, completionTokens }) => {
      judgeTotals.calls += 1;
      judgeTotals.promptTokens += promptTokens;
      judgeTotals.completionTokens += completionTokens;
      judgeTotals.usd += usd;
    },
    onRetry: ({ status, attempt, maxAttempts, delayMs }) =>
      console.log(`  rate limited (${status}); retry ${attempt}/${maxAttempts} in ${delayMs}ms`),
  });
  const runBounded = createConcurrencyLimiter(args.judgeConcurrency);
  const pending = [];
  for (const record of results) {
    const facts = factsByPersona[record.persona] ?? {};
    const groundTruth = groundTruthLines(facts, record.judgeFacts ?? undefined);
    record.turns.forEach((turn, turnIndex) => {
      if (!record.judged || turn.judged === false || turn.error || !turn.answer) return;
      const conversation = [
        ...record.history,
        ...record.turns.slice(0, turnIndex).flatMap((prior) => [
          { role: "user", content: prior.question },
          { role: "assistant", content: prior.answer },
        ]),
      ];
      pending.push({ record, turn, turnIndex, conversation, groundTruth });
    });
  }
  let judged = 0;
  await Promise.all(
    pending.map(({ record, turn, turnIndex, conversation, groundTruth }) =>
      runBounded(async () => {
        if (aborted) return;
        try {
          const dimensions = applicableDimensions(record, turnIndex, conversation);
          turn.judgement = await judgeTurn({
            evidence: buildJudgeEvidence({
              question: turn.question,
              conversation,
              expectedBehavior: record.expectedBehavior,
              groundTruth,
              response: turn.response,
            }),
            dimensions,
            fetchImpl: judgeFetch,
            apiKey,
          });
        } catch (error) {
          if (error instanceof SpendCeilingExceededError) {
            if (!aborted) {
              aborted = error.message;
              console.error(`ABORTED during judging: ${error.message}`);
            }
            return;
          }
          turn.judgementError = String(error?.message ?? error);
        }
        judged += 1;
        if (judged % 25 === 0) {
          console.log(`  judged ${judged}/${pending.length}  spend $${ledger.totalUsd.toFixed(4)}`);
        }
      }),
    ),
  );
}

// Taxonomy + case-level rollups after both passes.
finalizeRecords(results);

const accounting = accountRun(results, {
  ...judgeTotals,
  usd: Number(judgeTotals.usd.toFixed(6)),
});
const summary = summarise(results, args.batch, accounting);
summary.spend = {
  batchUsd: Number((ledger.totalUsd - startingSpend).toFixed(6)),
  cumulativeUsd: Number(ledger.totalUsd.toFixed(6)),
  remainingUsd: Number(ledger.remainingUsd().toFixed(6)),
};
summary.aborted = aborted;

const outputDirectory = join(artifacts, "runs", args.batch);
mkdirSync(outputDirectory, { recursive: true });
writeFileSync(join(outputDirectory, "transcript.json"), `${JSON.stringify(results, null, 2)}\n`);
writeFileSync(join(outputDirectory, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
writeFileSync(join(outputDirectory, "snapshots.json"), `${JSON.stringify(snapshots, null, 2)}\n`);

printSummary(summary, results);

if (args.verbose) {
  for (const record of results) printInspection(record, factsByPersona[record.persona]);
}

const criticalFailures = results.filter((record) => record.critical && !record.deterministicPass);
if (criticalFailures.length > 0) {
  console.error(
    `\nCRITICAL INVARIANT FAILURES (${criticalFailures.length}): ${criticalFailures.map((record) => record.id).join(", ")}`,
  );
  process.exitCode = 1;
}
if (aborted) process.exitCode = 1;
