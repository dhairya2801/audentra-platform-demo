# Edward evaluation harness

Evaluates the **canonical Python Edward** — the same FastAPI path production
serves (`POST /v1/student/assistant/messages`): demo-auth student binding,
durable conversation history, `AssistantPipeline` planning and tool reads, the
bounded dependency second read, grounded composition, the claim guard, and
per-turn tracing.

```bash
OPENAI_API_KEY=sk-... npm run eval:edward -- --batch my-run          # full suite
npm run eval:edward:smoke                                            # deterministic invariants, no key needed
npm run eval:edward -- --id xd-001 --verbose                         # run + inspect one case
npm run eval:edward:inspect -- --batch my-run --id xd-001            # inspect from a stored batch, free
npm run eval:edward:compare -- --baseline python-edward-baseline-4 --candidate my-run
npm run eval:edward:coverage                                         # regenerate COVERAGE.md
npm run eval:edward:compare-modes -- --batch modes-v1                # normal vs forced zero-LLM
```

Artifacts land in `artifacts/runs/<batch>/`: `transcript.json` (every graded
turn with its full `AssistantTurnTrace`), `summary.json`, and
`snapshots.json` (the canonical persona state the run graded against).

## Normal vs forced zero-LLM (`compare-modes.mjs`)

`compare-modes.mjs` asks one question twice against the same host: once exactly
as production runs it, once with `X-Edward-Mode: deterministic`, the platform's
development/evaluation control that builds the pipeline with **no** model
planner and **no** prose composer. Both turns are conversation-less, so nothing
persists between them and the second turn reads precisely the state the first
one did; follow-up experiments replay their prior turns as client history so
both sides get identical context.

The experiment set lives in `src/mode-experiments.mjs`, one entry per
(category, persona, question). Nothing is judged: the harness reports what the
two `AssistantTurnTrace`s recorded — route, tool reads, evidence, model calls,
tokens, cost, latency, and whether the final messages differ — and exits
non-zero if any deterministic run was not confirmed zero-LLM by its own trace.
Artifacts land in `artifacts/mode-comparisons/<batch>/`.

Without `OPENAI_API_KEY`/`OPENROUTER_API_KEY` the normal side has no model to
call and the comparison degenerates; the harness says so rather than pretending.

## The suite

~340 cases / ~410 turns in `src/cases/*.mjs`, one file per domain, loaded and
validated by `src/questions.mjs`. Every case names its persona, so every
question has a definite right answer; `coverage.mjs` renders the capability ×
dimension matrix and the tool/persona maps into `COVERAGE.md`.

- **Multi-turn** (~27% of turns): `turns: [...]` cases run through one durable
  server-side conversation, graded per turn; later turns carry the judge's
  continuity dimension.
- **Tool expectations**: a per-turn `expect` block (`requiredTools`,
  `anyOfTools`, `forbiddenTools`, `maxTools`, `dependencyTools`,
  `requestTypes`) is graded independently of answer quality by
  `src/tool-grading.mjs` into typed codes (TOOL_NOT_CALLED, WRONG_TOOL,
  UNNECESSARY_TOOL, DEPENDENCY_READ_MISSING, INTENT_FAILURE). Only genuinely
  required reads are demanded; universal context reads are always acceptable.
- **Fault injection**: `faults: {documents: "timeout"}` boots the host with
  `--fault documents=timeout` (modes: `error`, `timeout`, `empty`) to test
  honest degradation. Ground truth for faulted cases comes from the same
  persona's fault-free snapshot.
- **Tiers**: `--tier smoke` (deterministic invariants + every `critical`
  case; no model key needed), `--tier core` (everything not marked
  `tier: "full"`), `--tier full`. A failing `critical` case exits non-zero
  regardless of aggregate score — that is the CI gate
  (`.github/workflows/edward-eval.yml`).

## Ground truth is derived, never copied

`src/snapshot.mjs` reads each persona's canonical state from the same student
REST endpoints the portal renders; `src/facts.mjs` derives facts from it
(missing documents, deposit unpaid/pending/posted, registration gates, award
amounts, …) mirroring the assistant's own tool projections. Checks reference
facts by name (`mentions_amount: remainingBalanceUsd`,
`deposit_state_consistent`, `mentions_any_fact: clubNames`), so the test
suite cannot drift from the fixture.

## Grading

1. **Deterministic first** (`src/assertions.mjs`): contract invariants on
   every turn (no raw JSON, no internal vocabulary, no markup, block
   contract, receipts) plus per-case checks. Adversarial, privacy, and
   write-safety behaviour is decided here, never by a model.
2. **Tool-selection grading** (`src/tool-grading.mjs`), separate from answer
   quality.
3. **Judge** (`src/judge.mjs`): a typed evidence contract — question,
   conversation, expected behaviour, canonical ground-truth lines, executed
   tools with bounded results, final answer — and ten dimensions scored 0–2
   independently (factual correctness, evidence grounding, completeness,
   reasoning, response-mode fit, helpfulness, next-step quality, continuity,
   personalization, hallucination-free). Dimensions that don't apply to a
   case are not requested. The judge is instructed not to reward verbosity,
   formatting, or unsolicited recommendations.
4. **Taxonomy** (`src/taxonomy.mjs`): every failed signal maps to one typed
   failure class (INTENT_FAILURE … HALLUCINATION … TOOL_ERROR_HANDLING);
   `summary.json` reports counts per class.

## Cost, latency, and spend safety

`src/accounting.mjs` reads planner AND composer usage from each turn's trace
`modelCalls` (the gateway attaches usage to the plan), plus judge usage from
the harness ledger — so a run reports calls, tokens, and dollars per
operation, cost per turn, and server/client latency p50/p95. Pricing lives in
`src/pricing.mjs` (eval configuration, not application code). The cumulative
`SpendLedger` (`artifacts/spend.json`) hard-aborts a runaway batch.

## Judge calibration

`calibrate.mjs` compares the judge against hand labels in
`src/hand-labels.mjs` (per-dimension agreement and good/weak/bad verdict
agreement). Re-run after changing the judge prompt or dimensions; keep the
known limitations section of the baseline report honest.

## Legacy notes

- The original 115 cases live unchanged in `src/cases/legacy.mjs` (ids
  frozen). `coverage.mjs --legacy` shows what they did and did not cover.
- Batches older than the v2 harness store flat single-turn records; `compare`
  and `inspect` read both shapes. Scores are not comparable across the
  TS→Python runtime switch.
- `manual-verification.mjs` and `src/personas.mjs` still target the retired
  demo-api preview and are kept only for reference.
