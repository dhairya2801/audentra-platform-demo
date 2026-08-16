#!/usr/bin/env node
/**
 * Re-judge a stored batch with the current judge prompt/dimensions — the
 * expensive asking phase is never repeated (typically ~half the cost of a
 * full run).
 *
 *   OPENAI_API_KEY=... node tools/edward-eval/rejudge.mjs --batch python-edward-baseline-4
 *
 * Also refreshes deterministic grading from the current case definitions
 * (same as regrade.mjs) so the batch reflects the harness as it stands.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { accountRun } from "./src/accounting.mjs";
import { deriveFacts, groundTruthLines } from "./src/facts.mjs";
import { gradeTranscripts, finalizeRecords } from "./src/grade.mjs";
import { applicableDimensions, buildJudgeEvidence, judgeTurn } from "./src/judge.mjs";
import { QUESTIONS } from "./src/questions.mjs";
import {
  SpendCeilingExceededError,
  SpendLedger,
  costTrackedFetch,
  createConcurrencyLimiter,
} from "./src/spend-ledger.mjs";
import { summarise, printSummary } from "./src/summarize.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

const batchIndex = process.argv.indexOf("--batch");
const batch = batchIndex >= 0 ? process.argv[batchIndex + 1] : process.argv[2];
const apiKey = process.env.OPENAI_API_KEY;
if (!batch || !apiKey) {
  console.error("Usage: OPENAI_API_KEY=... rejudge.mjs --batch <name>");
  process.exit(2);
}

const directory = join(repoRoot, "artifacts", "runs", batch);
const transcript = JSON.parse(readFileSync(join(directory, "transcript.json"), "utf8"));
const snapshots = JSON.parse(readFileSync(join(directory, "snapshots.json"), "utf8"));
const previousSummary = JSON.parse(readFileSync(join(directory, "summary.json"), "utf8"));

const ledger = new SpendLedger({ file: join(repoRoot, "artifacts", "spend.json"), batch: `${batch}-rejudge` });
const factsByPersona = Object.fromEntries(
  Object.entries(snapshots).map(([persona, snapshot]) => [persona, deriveFacts(snapshot)]),
);

const currentById = new Map(QUESTIONS.map((item) => [item.id, item]));
const refreshed = transcript.map((record) => {
  const current = currentById.get(record.id);
  if (!current) return record;
  return {
    ...record,
    judged: current.judged,
    critical: current.critical,
    tags: current.tags,
    expectedBehavior: current.expectedBehavior,
    judgeFacts: current.judgeFacts,
    turns: record.turns.map((turn, index) => ({
      ...turn,
      judgement: null,
      judgementError: undefined,
      checks: current.turns[index]?.checks ?? turn.checks,
      expect: current.turns[index]?.expect ?? turn.expect,
      judged: current.turns[index]?.judged ?? turn.judged,
    })),
  };
});

const results = gradeTranscripts(refreshed, factsByPersona);

const judgeTotals = { calls: 0, promptTokens: 0, completionTokens: 0, usd: 0 };
const judgeFetch = costTrackedFetch(ledger, {
  onCall: ({ usd, promptTokens, completionTokens }) => {
    judgeTotals.calls += 1;
    judgeTotals.promptTokens += promptTokens;
    judgeTotals.completionTokens += completionTokens;
    judgeTotals.usd += usd;
  },
});
const runBounded = createConcurrencyLimiter(4);
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
console.log(`re-judging ${pending.length} turns of batch "${batch}"…`);
let judged = 0;
let aborted = null;
await Promise.all(
  pending.map(({ record, turn, turnIndex, conversation, groundTruth }) =>
    runBounded(async () => {
      if (aborted) return;
      try {
        turn.judgement = await judgeTurn({
          evidence: buildJudgeEvidence({
            question: turn.question,
            conversation,
            expectedBehavior: record.expectedBehavior,
            groundTruth,
            response: turn.response,
          }),
          dimensions: applicableDimensions(record, turnIndex, conversation),
          fetchImpl: judgeFetch,
          apiKey,
        });
      } catch (error) {
        if (error instanceof SpendCeilingExceededError) {
          aborted = aborted ?? error.message;
          return;
        }
        turn.judgementError = String(error?.message ?? error);
      }
      judged += 1;
      if (judged % 50 === 0) console.log(`  judged ${judged}/${pending.length}`);
    }),
  ),
);
if (aborted) console.error(`ABORTED: ${aborted}`);

finalizeRecords(results);
const accounting = accountRun(results, { ...judgeTotals, usd: Number(judgeTotals.usd.toFixed(6)) });
const summary = summarise(results, batch, accounting);
summary.spend = previousSummary.spend;
summary.rejudgedAt = new Date().toISOString();

writeFileSync(join(directory, "transcript.json"), `${JSON.stringify(results, null, 2)}\n`);
writeFileSync(join(directory, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
printSummary(summary, results);
