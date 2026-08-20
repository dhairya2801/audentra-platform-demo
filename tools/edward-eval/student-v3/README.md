# Student Edward v3 evaluation

A 100-scenario evaluation of Student Edward against the canonical in-memory
platform composition — the same FastAPI routes, `AssistantPipeline`, tool host,
guards and per-turn tracing production serves — across the 16 seeded student
personas in `../src/personas.mjs`.

It complements the older 383-case suite (`../run.mjs`), which grades contract
invariants, tool selection and judge quality across a wider surface. This suite
is narrower and stricter: every scenario is a question a real admitted student
would type, every expectation resolves against **ground truth derived from the
host's own REST snapshot**, and grading is fully deterministic.

## Pieces

| File | Role |
| --- | --- |
| `cases.mjs` | 80 development scenarios across 15 categories (overview, requirements, why-blocked, conflicting claims, deadlines, aid, housing, documents, prioritization, navigation, knowledge, multi-intent, context carry-forward, context reset, unsupported). |
| `holdout-cases.mjs` | 20 scenarios written alongside the dev suite and executed only after the implementation work finished, to measure first-contact generalization. |
| `run.mjs` | Boots one `audentra-eval-api` host per persona (via `../src/runner.mjs`), drives every turn through `POST /v1/student/assistant/messages`, derives per-persona facts with `../src/facts.mjs`, and grades deterministically. |

## Grading

`PASS` (every check), `PARTIAL` (intent answered, a non-critical fact missed or
an intent mismatch), `FAIL` (critical fact missed, forbidden claim, or a
required read never executed).

`expect` fields per turn:

- `requestTypes` — accepted classifications (soft: a mismatch is `PARTIAL`)
- `requiredTools` / `anyOfTools` / `forbiddenTools` / `maxTools`
- `facts` — regex over the full student-visible answer (prose **and** every
  block's `fallbackText`); `critical: true` makes a miss a `FAIL`
- `factGroups` — at least one alternative rubric must match
- `forbidden` — no entry may match

Fact patterns reference ground truth by path rather than asserting prose:

| Template | Meaning |
| --- | --- |
| `{{f:path}}` | a scalar fact, regex-escaped (`{{f:depositAmountUsd}}` → `\$500`) |
| `{{n:path}}` | a number, thousands-separator tolerant |
| `{{any:path}}` | alternation over a list — at least one entry must appear |
| `{{all:path}}` | every list entry must appear |

Paths resolve against `deriveFacts(snapshot)` in `../src/facts.mjs`.

## Running it

```bash
npm run eval:edward:student-v3 -- --batch student-v3-dev        # 80 dev cases
npm run eval:edward:student-v3 -- --holdout --batch holdout     # 20 holdout
npm run eval:edward:student-v3 -- --category navigation -v      # one category
npm run eval:edward:student-v3 -- --id stu-nav-001 -v           # one case
npm run eval:edward:student-v3 -- --regrade student-v3-baseline --batch regraded
```

`--regrade <batch>` re-grades a stored run against the current case specs
without calling Edward — how a corrected expectation is applied to an
already-recorded baseline. It reads persona snapshots from
`artifacts/student-v3-snapshots.json`, which a live run writes.

No LLM judge is involved. The only model spend is Edward's own planner and
composer calls, metered from each turn's trace and reported in `summary.json`
(≈ $0.025 for the full 80-case suite on `gpt-4o-mini`).

Artifacts land in `artifacts/runs/<batch>/`.

## Case-authoring rules

- Every question must be answerable — or intentionally unanswerable — from the
  persona's real backend state. Expected facts come from ground truth, never
  from prose the author believes to be true.
- Grade meaning, not wording: use alternations for legitimate phrasings, and
  mark `critical` only where a miss makes the answer wrong.
- Pick the persona that makes the question have a definite right answer
  (`payment_pending` for "I paid, why am I blocked?", `transcript_under_review`
  for "did you receive it?", `no_aid` for "how much aid am I getting?").
- Context cases must assert both halves: what must carry forward, and what must
  not (`forbidden` on the previous topic's vocabulary).
