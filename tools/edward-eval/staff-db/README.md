# Staff Edward database-backed eval

A realistic evaluation of Staff Edward against the canonical ~2,600-student
synthetic university population in PostgreSQL — the same tenant, repositories,
and Action Center queue the Staff Portal serves. It complements the in-memory
suite (`tools/edward-eval/run-staff.mjs`, single demo student, honesty
invariants) with duplicate-name resolution, Action Center parity, cohort
counts, and cross-domain reasoning over real data volume.

## Suites

Two case banks share one runner and one ground-truth extractor:

| Suite | Cases | Focus |
| --- | --- | --- |
| `v1` (`cases.mjs`, `holdout-cases.mjs`) | 40 dev + 8 holdout | the original bank: lookup, summary, cross-domain, Action Center, aggregate, risk, negative. Kept as a regression floor. |
| `v2` (`cases-v2.mjs`, `holdout-cases-v2.mjs`) | 80 dev + 20 holdout | the comprehensive bank: identification, overview, Action Center, cohorts, risk, cross-domain, communications, recommendations, multi-intent, and the **conversational-scope** family (carry-forward, explicit replacement, cohort/queue/ranking after a student turn). Default. |

Select with `--suite v1` / `--suite v2` (default `v2`).

## Pieces

| File | Role |
| --- | --- |
| `ground_truth.py` | Extracts expected facts from the canonical backend. It builds the production `PostgresPlatformService` staff tool host and executes the real staff tools (`execute_staff_tool_reads`), so every expected fact is the product's own read — never a re-implementation. Read-only. |
| `cases.mjs` | ~40 development cases across lookup / summary / cross-domain / action-center / aggregate / risk / negative. Expected facts reference ground truth via `{{gt:path}}` (regex-escaped string) and `{{num:path}}` (thousands-separator-tolerant number). |
| `holdout-cases.mjs` | 8 cases written with the dev suite but only run after fixes stabilized, to test generalization. |
| `run.mjs` | Drives `POST /v1/staff/assistant/messages` on a running host, fetches the per-turn trace, and grades deterministically: classification, executed tools, traced tool arguments, resolved-student identity, required facts, forbidden claims. No LLM judge — the only model spend is Edward's own planner/composer calls, which the summary reports. |

Grades: **PASS** (all checks), **PARTIAL** (core intent answered, a
non-critical fact missed), **FAIL** (critical fact missed, forbidden claim,
wrong entity, wrong reads).

## Running it

The eval needs a **frozen snapshot** of the dev database — the dev worker
mutates work items between reads, which makes ground truth drift mid-run:

```bash
docker exec audentra-platform-postgres-1 sh -c \
  "psql -U vv -d postgres -c 'DROP DATABASE IF EXISTS vv_enrollment_staff_eval' \
   && psql -U vv -d postgres -c 'CREATE DATABASE vv_enrollment_staff_eval OWNER vv' \
   && pg_dump -U vv vv_enrollment | psql -q -U vv -d vv_enrollment_staff_eval"
```

Start an API host against the snapshot (trace debug on, demo staff headers
allowed, gpt-4o-mini via `OPENAI_API_KEY`):

```bash
DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment_staff_eval \
DEMO_TENANT_ID=00000000-0000-7000-8000-000000000003 \
OPENAI_API_KEY=... OPENAI_MODEL=gpt-4o-mini \
ASSISTANT_TRACE_DEBUG_ENABLED=true API_PORT=45710 \
uv run --directory apps/api --locked audentra-api
```

Generate ground truth, then run:

```bash
mkdir -p artifacts/staff-db-eval
DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment_staff_eval \
  uv run --directory apps/api --locked python \
  "$(pwd)/tools/edward-eval/staff-db/ground_truth.py" \
  > artifacts/staff-db-eval/ground-truth.json

npm run eval:edward:staff-db -- --batch staff-v2-dev -v            # v2 dev suite
npm run eval:edward:staff-db -- --holdout --batch staff-v2-holdout # v2 holdout
npm run eval:edward:staff-db -- --suite v1 --batch staff-v1        # v1 regression floor
npm run eval:edward:staff-db -- --id s2-ctx-008 -v                 # one case
npm run eval:edward:staff-db -- --category context -v              # one category
```

Environment overrides: `STAFF_EVAL_BASE_URL`, `STAFF_EVAL_ACTOR_ID`,
`STAFF_EVAL_GROUND_TRUTH`, `STAFF_EVAL_WORKER_TOKEN` (trace endpoint),
`STAFF_EVAL_TENANT_ID` (ground truth).

Artifacts land in `artifacts/runs/<batch>/` (`transcript.json`,
`summary.json` — including model calls/tokens/estimated USD per run).

## Case-authoring rules

- Every question must be answerable from the current backend state; the
  expected facts must come from `ground-truth.json`, not from prose you
  believe to be true.
- Grade meaning, not wording: use alternations for legitimate phrasings, and
  mark only facts that make the answer wrong-if-missing as `critical`.
- Ambiguity cases must assert both that no student was silently resolved
  (`resolvedStudentId: null`) and that the reply carries usable
  disambiguators (student IDs).
- The dev worker must not run against the snapshot database while the suite
  executes.
- **Regenerate `ground-truth.json` on the day you run the suite.** Some truths
  are relative to `now()` — `overdueRequirements`, and every deadline bucket —
  so a truth file from yesterday fails those cases against a correct answer.
  Observed: `overdueRequirements` moved 1511 → 1535 overnight as requirements
  crossed their due date, failing `s2-coh-007` on a right answer. Only
  time-relative counts drift; everything else is stable against the frozen
  snapshot.
