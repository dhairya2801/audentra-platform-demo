#!/usr/bin/env node
/**
 * Normal Edward vs forced zero-LLM Edward, same question, same state.
 *
 *   OPENAI_API_KEY=sk-... node tools/edward-eval/compare-modes.mjs --batch modes-1
 *   node tools/edward-eval/compare-modes.mjs --persona new_admit --category Ambiguous
 *
 * For every experiment this boots one `audentra-eval-api` per persona and asks
 * the same question twice against that one process: once exactly as production
 * runs it, once with `X-Edward-Mode: deterministic`, which makes the platform
 * build the pipeline with no model planner and no prose composer.
 *
 * Both turns are conversation-less, so neither persists an exchange and the
 * second turn reads precisely the state the first one did. Follow-up
 * experiments replay their prior turns as client history so both sides get
 * identical context.
 *
 * Nothing here judges quality. The harness reports what the two
 * AssistantTurnTraces recorded — route, tools, evidence, model calls, tokens,
 * cost, latency, and whether the final messages differ — and refuses to call a
 * run deterministic unless its trace says `executionMode: "deterministic"`.
 * Without a provider key the "normal" side has no model to call and the
 * comparison degenerates; the harness says so rather than pretending.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { EXPERIMENTS, experimentsFor } from "./src/mode-experiments.mjs";
import { priceUsd, PRICING_VERSION } from "./src/pricing.mjs";
import { startEdward } from "./src/runner.mjs";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const MODES = ["default", "deterministic"];

function parseArgs(argv) {
  const args = { batch: "modes", personas: [], categories: [], ids: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--persona") args.personas.push(argv[++index]);
    else if (flag === "--category") args.categories.push(argv[++index]);
    else if (flag === "--id") args.ids.push(argv[++index]);
  }
  return args;
}

/** Everything the two traces let us say about one run, and nothing more. */
function observe(mode, result) {
  const trace = result.trace;
  const toolCalls = trace?.toolCalls ?? [];
  const modelCalls = trace?.modelCalls ?? [];
  const tokens = modelCalls.reduce(
    (sum, call) => sum + (call.usage?.totalTokens ?? 0),
    0,
  );
  const costUsd = modelCalls.reduce(
    (sum, call) =>
      sum +
      priceUsd(
        call.model ?? "gpt-4o-mini",
        call.usage?.promptTokens ?? 0,
        call.usage?.completionTokens ?? 0,
      ),
    0,
  );
  return {
    mode,
    requestId: result.payload?.requestId ?? null,
    // A run is only "confirmed" when the platform's own trace agrees it ran in
    // the requested mode; anything else is an observation, not a proof.
    modeConfirmed: trace?.executionMode === mode,
    httpStatus: result.status,
    message: result.payload?.message ?? "",
    messageCharacters: (result.payload?.message ?? "").length,
    blockTypes: (result.payload?.blocks ?? []).map((block) => block?.type ?? "unknown"),
    requestType: trace?.classification?.requestType ?? null,
    additionalRequestTypes: trace?.classification?.additionalRequestTypes ?? [],
    toolSelectionSource: trace?.toolSelectionSource ?? null,
    executedTools: toolCalls.map((call) => call.tool),
    dependencyTools: toolCalls
      .filter((call) => call.round === "dependency")
      .map((call) => call.tool),
    evidence: trace?.evidence ?? [],
    responseSource: trace?.responseSource ?? null,
    provider: trace?.provider ?? null,
    model: trace?.model ?? null,
    modelCalls: modelCalls.map((call) => ({
      operation: call.operation,
      outcome: call.outcome,
      durationMs: call.durationMs,
      detail: call.detail ?? null,
    })),
    modelCallCount: modelCalls.length,
    totalTokens: tokens,
    costUsd,
    failureCodes: trace?.failureCodes ?? [],
    serverDurationMs: trace?.durationMs ?? null,
    clientLatencyMs: result.latencyMs,
    traceMissing: trace === null,
  };
}

function diff(normal, deterministic) {
  const onlyIn = (a, b) => a.filter((item) => !b.includes(item));
  return {
    sameMessage: normal.message.trim() === deterministic.message.trim(),
    sameRequestType: normal.requestType === deterministic.requestType,
    sameTools: normal.executedTools.join("|") === deterministic.executedTools.join("|"),
    sameEvidence: normal.evidence.join("|") === deterministic.evidence.join("|"),
    sameBlocks: normal.blockTypes.join("|") === deterministic.blockTypes.join("|"),
    toolsOnlyInNormal: onlyIn(normal.executedTools, deterministic.executedTools),
    toolsOnlyInDeterministic: onlyIn(
      deterministic.executedTools,
      normal.executedTools,
    ),
    serverDurationDeltaMs:
      normal.serverDurationMs === null || deterministic.serverDurationMs === null
        ? null
        : deterministic.serverDurationMs - normal.serverDurationMs,
    tokensAvoided: normal.totalTokens,
    costAvoidedUsd: normal.costUsd,
    characterDelta: deterministic.messageCharacters - normal.messageCharacters,
  };
}

async function runExperiment(edward, experiment) {
  const runs = {};
  for (const mode of MODES) {
    const result = await edward.ask(experiment.question, {
      history: experiment.history ?? [],
      executionMode: mode,
    });
    runs[mode] = observe(mode, result);
  }
  return {
    id: experiment.id,
    category: experiment.category,
    persona: experiment.persona,
    question: experiment.question,
    historyTurns: (experiment.history ?? []).length,
    note: experiment.note ?? null,
    runs,
    diff: diff(runs.default, runs.deterministic),
  };
}

function markdown(rows, meta) {
  const lines = [];
  lines.push("# Edward: normal vs forced zero-LLM", "");
  lines.push(`- Batch: \`${meta.batch}\``);
  lines.push(`- Model configuration: ${meta.modelConfiguration}`);
  lines.push(`- Pricing table: ${PRICING_VERSION}`);
  lines.push(`- Experiments: ${rows.length}`);
  const unconfirmed = rows.filter(
    (row) => !row.runs.default.modeConfirmed || !row.runs.deterministic.modeConfirmed,
  );
  lines.push(
    `- Unconfirmed runs: ${unconfirmed.length}${
      unconfirmed.length > 0 ? ` (${unconfirmed.map((row) => row.id).join(", ")})` : ""
    }`,
  );
  const zeroCall = rows.filter((row) => row.runs.deterministic.modelCallCount === 0);
  lines.push(
    `- Deterministic runs with zero model calls: ${zeroCall.length}/${rows.length}`,
  );
  lines.push("");
  lines.push(
    "| Case | Category | Persona | Same answer | Same route | Same tools | Normal calls / tokens | Det. calls | Normal ms | Det. ms |",
  );
  lines.push("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |");
  for (const row of rows) {
    lines.push(
      `| ${row.id} | ${row.category} | ${row.persona} | ${row.diff.sameMessage ? "yes" : "no"} | ${
        row.diff.sameRequestType ? "yes" : "no"
      } | ${row.diff.sameTools ? "yes" : "no"} | ${row.runs.default.modelCallCount} / ${
        row.runs.default.totalTokens
      } | ${row.runs.deterministic.modelCallCount} | ${
        row.runs.default.serverDurationMs ?? "—"
      } | ${row.runs.deterministic.serverDurationMs ?? "—"} |`,
    );
  }
  lines.push("");
  for (const row of rows) {
    lines.push(`## ${row.id} — ${row.category} (${row.persona})`);
    lines.push("");
    lines.push(`> ${row.question}`);
    if (row.historyTurns > 0) {
      lines.push(`>`, `> _replaying ${row.historyTurns} prior turn(s) as context_`);
    }
    if (row.note) lines.push(`>`, `> _${row.note}_`);
    lines.push("");
    for (const mode of MODES) {
      const run = row.runs[mode];
      lines.push(
        `**${mode}** — route \`${run.requestType ?? "—"}\` via \`${
          run.toolSelectionSource ?? "—"
        }\`, source \`${run.responseSource ?? "—"}\`, ${run.modelCallCount} model call(s), ` +
          `${run.totalTokens} tokens, $${run.costUsd.toFixed(6)}, ${
            run.serverDurationMs ?? "—"
          } ms server, tools: ${run.executedTools.join(", ") || "none"}` +
          `${run.failureCodes.length > 0 ? `, failures: ${run.failureCodes.join(", ")}` : ""}` +
          `${run.modeConfirmed ? "" : "  ⚠ mode not confirmed by trace"}`,
      );
      lines.push("");
      lines.push("```text");
      lines.push(run.message || "(no message)");
      lines.push("```");
      lines.push("");
    }
  }
  return `${lines.join("\n")}\n`;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const selected = experimentsFor(args);
  if (selected.length === 0) {
    console.error("No experiments matched the given filters.");
    process.exit(2);
  }
  const hasKey = Boolean(
    (process.env.OPENAI_API_KEY ?? "").trim() ||
      (process.env.OPENROUTER_API_KEY ?? "").trim(),
  );
  if (!hasKey) {
    console.warn(
      "No provider key is set: the normal-mode side has no model to call, so " +
        "this run compares deterministic Edward with itself.",
    );
  }

  const byPersona = new Map();
  for (const experiment of selected) {
    const list = byPersona.get(experiment.persona) ?? [];
    list.push(experiment);
    byPersona.set(experiment.persona, list);
  }

  const rows = [];
  for (const [persona, experiments] of byPersona) {
    const edward = await startEdward({ persona });
    try {
      for (const experiment of experiments) {
        process.stdout.write(`… ${experiment.id} (${persona})\n`);
        rows.push(await runExperiment(edward, experiment));
      }
    } finally {
      await edward.close();
    }
  }
  rows.sort(
    (a, b) =>
      EXPERIMENTS.findIndex((item) => item.id === a.id) -
      EXPERIMENTS.findIndex((item) => item.id === b.id),
  );

  const meta = {
    batch: args.batch,
    modelConfiguration: hasKey
      ? `provider key present (${process.env.OPENAI_MODEL || "gpt-4o-mini"})`
      : "no provider key — normal mode is deterministic too",
  };
  const outDir = join(REPO_ROOT, "artifacts", "mode-comparisons", args.batch);
  mkdirSync(outDir, { recursive: true });
  writeFileSync(
    join(outDir, "comparisons.json"),
    `${JSON.stringify({ meta, rows }, null, 2)}\n`,
  );
  writeFileSync(join(outDir, "comparisons.md"), markdown(rows, meta));

  const zeroCall = rows.filter(
    (row) => row.runs.deterministic.modelCallCount === 0,
  ).length;
  const confirmed = rows.filter((row) => row.runs.deterministic.modeConfirmed).length;
  console.log("");
  console.log(`${rows.length} experiments → ${outDir}`);
  console.log(`deterministic runs with zero model calls: ${zeroCall}/${rows.length}`);
  console.log(`deterministic runs confirmed by trace:    ${confirmed}/${rows.length}`);
  console.log(
    `same final message: ${rows.filter((row) => row.diff.sameMessage).length}/${rows.length}` +
      `  ·  same route: ${rows.filter((row) => row.diff.sameRequestType).length}/${rows.length}` +
      `  ·  same tools: ${rows.filter((row) => row.diff.sameTools).length}/${rows.length}`,
  );
  // A deterministic run that reached a model is a broken experiment, not a result.
  if (zeroCall !== rows.length || confirmed !== rows.length) {
    console.error("Some deterministic runs were not confirmed zero-LLM.");
    process.exit(1);
  }
}

await main();
