#!/usr/bin/env node
/**
 * Edward evaluation CLI.
 *
 *   node tools/edward-eval/run.mjs --batch baseline
 *   node tools/edward-eval/run.mjs --batch causal-fix --categories causal,multi_domain
 *   node tools/edward-eval/run.mjs --batch smoke --limit 5 --no-judge
 *
 * Writes a full transcript and a summary to artifacts/runs/<batch>/.
 * Aborts hard if the cumulative spend ceiling is reached.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runContractChecks, runChecks } from "./src/assertions.mjs";
import { CRITERIA, judgeAnswer } from "./src/judge.mjs";
import { QUESTIONS, questionsByCategory } from "./src/questions.mjs";
import { runCases } from "./src/runner.mjs";
import {
  SpendCeilingExceededError,
  SpendLedger,
  costTrackedFetch,
  createConcurrencyLimiter,
} from "./src/spend-ledger.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const artifacts = join(repoRoot, "artifacts");

function parseArgs(argv) {
  const args = {
    batch: "adhoc",
    categories: [],
    limit: 0,
    judge: true,
    repeat: 1,
    judgeConcurrency: 4,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--categories") args.categories = argv[++index].split(",");
    else if (flag === "--limit") args.limit = Number(argv[++index]);
    else if (flag === "--repeat") args.repeat = Number(argv[++index]);
    else if (flag === "--judge-concurrency")
      args.judgeConcurrency = Number(argv[++index]);
    else if (flag === "--no-judge") args.judge = false;
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
const apiKey = process.env.OPENAI_API_KEY;
if (!apiKey) {
  console.error("OPENAI_API_KEY is required to run the evaluation.");
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

let selected = questionsByCategory(args.categories);
if (args.repeat > 1) {
  selected = Array.from({ length: args.repeat }, (_, run) =>
    selected.map((item) => ({ ...item, id: `${item.id}#${run + 1}` })),
  ).flat();
}
if (args.limit > 0) selected = selected.slice(0, args.limit);
console.log(`${selected.length} case(s) selected of ${QUESTIONS.length} in the suite.`);

let transcripts = [];
let aborted = null;
try {
  transcripts = await runCases({
    cases: selected,
    ledger,
    onProgress: (record, done, total) => {
      if (done % 10 === 0 || done === total) {
        console.log(
          `  asked ${done}/${total}  spend $${ledger.totalUsd.toFixed(4)}`,
        );
      }
    },
  });
} catch (error) {
  if (error instanceof SpendCeilingExceededError) {
    aborted = error.message;
    console.error(`ABORTED: ${error.message}`);
  } else {
    throw error;
  }
}

// Deterministic scoring first: it costs nothing and decides the cases that must
// never be left to a model.
const results = transcripts.map((record) => {
  const contractFailures = runContractChecks(record.response);
  const checkFailures = runChecks(record.checks ?? [], record.answer, record.response);
  return {
    ...record,
    contractFailures,
    checkFailures,
    deterministicPass:
      contractFailures.length === 0 && checkFailures.length === 0 && !record.error,
  };
});

if (args.judge && !aborted) {
  const judgeFetch = costTrackedFetch(ledger, {
    onRetry: ({ status, attempt, maxAttempts, delayMs }) =>
      console.log(
        `  rate limited (${status}); retry ${attempt}/${maxAttempts} in ${delayMs}ms`,
      ),
  });
  // Judging is independent per answer, so it is the one place worth fanning
  // out. The bound keeps that fan-out from being the thing that trips a 429.
  const runBounded = createConcurrencyLimiter(args.judgeConcurrency);
  const pending = results.filter((result) => result.judged && !result.error);
  let judged = 0;
  await Promise.all(
    pending.map((result) =>
      runBounded(async () => {
        if (aborted) return;
        try {
          result.judgement = await judgeAnswer({
            record: result,
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
          result.judgementError = String(error?.message ?? error);
        }
        judged += 1;
        if (judged % 10 === 0) {
          console.log(`  judged ${judged}/${pending.length}  spend $${ledger.totalUsd.toFixed(4)}`);
        }
      }),
    ),
  );
}

const summary = summarise(results, args.batch);
summary.spend = {
  batchUsd: Number((ledger.totalUsd - startingSpend).toFixed(6)),
  cumulativeUsd: Number(ledger.totalUsd.toFixed(6)),
  remainingUsd: Number(ledger.remainingUsd().toFixed(6)),
  perQuestionUsd:
    results.length > 0
      ? Number(((ledger.totalUsd - startingSpend) / results.length).toFixed(6))
      : 0,
};
summary.aborted = aborted;

const outputDirectory = join(artifacts, "runs", args.batch);
mkdirSync(outputDirectory, { recursive: true });
writeFileSync(
  join(outputDirectory, "transcript.json"),
  `${JSON.stringify(results, null, 2)}\n`,
);
writeFileSync(
  join(outputDirectory, "summary.json"),
  `${JSON.stringify(summary, null, 2)}\n`,
);

printSummary(summary, results);

function summarise(items, batch) {
  const judgedItems = items.filter((item) => item.judgement);
  const byCategory = {};
  for (const item of items) {
    const bucket = (byCategory[item.category] ??= {
      total: 0,
      deterministicPass: 0,
      judgedCount: 0,
      scoreSum: 0,
      scoreMax: 0,
    });
    bucket.total += 1;
    if (item.deterministicPass) bucket.deterministicPass += 1;
    if (item.judgement) {
      bucket.judgedCount += 1;
      bucket.scoreSum += item.judgement.total;
      bucket.scoreMax += item.judgement.maximum;
    }
  }
  const criterionTotals = Object.fromEntries(
    CRITERIA.map((criterion) => [criterion.key, { sum: 0, count: 0 }]),
  );
  for (const item of judgedItems) {
    for (const criterion of CRITERIA) {
      criterionTotals[criterion.key].sum += item.judgement.scores[criterion.key];
      criterionTotals[criterion.key].count += 1;
    }
  }
  return {
    batch,
    cases: items.length,
    deterministicPass: items.filter((item) => item.deterministicPass).length,
    deterministicFail: items.filter((item) => !item.deterministicPass).length,
    judged: judgedItems.length,
    meanScorePercent:
      judgedItems.length > 0
        ? Number(
            (
              (judgedItems.reduce((sum, item) => sum + item.judgement.total, 0) /
                judgedItems.reduce((sum, item) => sum + item.judgement.maximum, 0)) *
              100
            ).toFixed(1),
          )
        : null,
    byCategory: Object.fromEntries(
      Object.entries(byCategory).map(([name, bucket]) => [
        name,
        {
          ...bucket,
          meanScorePercent:
            bucket.scoreMax > 0
              ? Number(((bucket.scoreSum / bucket.scoreMax) * 100).toFixed(1))
              : null,
        },
      ]),
    ),
    byCriterion: Object.fromEntries(
      Object.entries(criterionTotals).map(([key, value]) => [
        key,
        value.count > 0 ? Number((value.sum / value.count).toFixed(2)) : null,
      ]),
    ),
  };
}

function printSummary(value, items) {
  console.log("\n=== summary ===");
  console.log(
    `cases ${value.cases} | deterministic pass ${value.deterministicPass}/${value.cases} | judged ${value.judged} | mean quality ${value.meanScorePercent ?? "-"}%`,
  );
  console.log(
    `batch spend $${value.spend.batchUsd.toFixed(4)} | cumulative $${value.spend.cumulativeUsd.toFixed(4)} | per question $${value.spend.perQuestionUsd.toFixed(5)}`,
  );
  console.log("\nby category:");
  for (const [name, bucket] of Object.entries(value.byCategory)) {
    console.log(
      `  ${name.padEnd(20)} pass ${String(bucket.deterministicPass).padStart(2)}/${String(bucket.total).padEnd(3)} quality ${bucket.meanScorePercent ?? "-"}%`,
    );
  }
  console.log("\nby criterion (mean of 2):");
  for (const [key, mean] of Object.entries(value.byCriterion)) {
    console.log(`  ${key.padEnd(22)} ${mean ?? "-"}`);
  }
  const failures = items.filter((item) => !item.deterministicPass);
  if (failures.length > 0) {
    console.log("\ndeterministic failures:");
    for (const failure of failures.slice(0, 25)) {
      console.log(
        `  [${failure.id}] ${failure.question}\n      ${[...failure.contractFailures, ...failure.checkFailures, failure.error].filter(Boolean).join("; ")}`,
      );
    }
  }
  console.log(`\nwritten to artifacts/runs/${value.batch}/`);
}
