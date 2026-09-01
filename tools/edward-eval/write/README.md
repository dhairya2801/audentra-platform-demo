# Edward write-ability evaluation

A suite about **changing things**, not reading them. Every case is something a
real student or a real staff member types when they want Edward to do
something, asked against a writable clone of the deployed synthetic university
(2,577 students, 88 staff, tenant `aster-demo`), and graded against the
canonical tables afterwards.

The read suites (`../student-v3`, `../staff-db`) answer *did Edward say the
right thing*. This one answers a harder question: **did the row change, was the
change the one Edward described, and did Edward tell the truth about it?**

## What it grades

| Property | How |
| --- | --- |
| **Recognition** | Did Edward understand a change was asked for, across the phrasings people use? |
| **Resolution** | Right student, work item, requirement, cohort — and a refusal to guess when the target is ambiguous. |
| **Preview accuracy** | Are the previewed fields the fields a confirmation would write? |
| **Effect** | After confirming, does the canonical row hold the previewed value? |
| **Honesty** | No success claim without a receipt; no denial without a reason; **no false claim of incapability**. |

Grading is deterministic — no LLM judge. A write is either proposed or not,
binds the right target or not, and leaves the row changed or not. The only
prose Edward is graded on is the two claims that can be checked against server
state: that something happened (there must be a receipt) and that something is
impossible (the action catalogue must agree).

## Layout

| File | Role |
| --- | --- |
| `cases.mjs` | 118 development cases across 14 categories, student and staff. |
| `holdout-cases.mjs` | 38 cases written *before the baseline run* and executed only after the implementation work finished, to measure generalization rather than memorization. |
| `fixtures.mjs` | The people and records cases refer to, by external reference (`SYN-…`) rather than UUID, so a regenerated university keeps working. |
| `db.mjs` | Canonical reads: fixture resolution and the effect probes. `psql` through the shell, zero dependencies, every statement wrapped in one `json_agg`. |
| `grade.mjs` | The typed grader. Every failed signal maps to one failure code. |
| `run.mjs` | Driver: conversations, turns, confirm/cancel, effect measurement, artifacts. |
| `fixture.sql` | Eval-only fixtures the Explorer deploy cannot produce (see below). |
| `reset-db.sh` | Recreates the eval database from the migrated base template. |

## Setup

The suite needs a **writable clone of the deployed university with this
branch's migration applied**, because it commits real writes and must start
from a known row set every run.

```bash
PG=audentra-platform-postgres-1

# 1. Base template: the deployed university, plus 0050_edward_write_v1.
docker exec $PG psql -U vv -d postgres -c \
  "DROP DATABASE IF EXISTS vv_enrollment_write_base"
docker exec $PG psql -U vv -d postgres -c \
  "CREATE DATABASE vv_enrollment_write_base TEMPLATE vv_enrollment OWNER vv"
DATABASE_URL=postgresql://vv:vv_local_password@localhost:5433/vv_enrollment_write_base \
  npm run db:migrate

# 2. Eval-only fixtures.
docker cp tools/edward-eval/write/fixture.sql $PG:/tmp/fixture.sql
docker exec $PG psql -U vv -d vv_enrollment_write_base -v ON_ERROR_STOP=1 -f /tmp/fixture.sql

# 3. An API host bound to the writable clone.
DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:5433/vv_enrollment_write_eval \
DEMO_TENANT_ID=00000000-0000-7000-8000-000000000003 \
ASSISTANT_TRACE_DEBUG_ENABLED=true API_PORT=45720 \
OPENAI_API_KEY=... OPENAI_MODEL=gpt-4o-mini \
  uv run --directory apps/api --locked audentra-api
```

`reset-db.sh` recreates `vv_enrollment_write_eval` from the base before every
batch; the running host reconnects on its own (`pool_pre_ping`).

### Why `fixture.sql` exists

Two facts about the deployed tenant are artefacts of the deployment, not of a
university, and each one made an action unreachable:

* **No mailboxes.** A real mailbox needs an OAuth grant, so the deploy creates
  none and `communications.email.prepare` could only ever deny. One shared
  department mailbox with an all-staff send grant gives it a happy path.
* **No student email addresses.** The deploy creates no credential accounts,
  so 2,576 of 2,577 students had no address — and the preview did not notice,
  which is how the preview/execution mismatch in `_email_prepare` was found.
  The fixture gives every student an address behind an unusable password hash;
  nothing here can be signed in to.

## Running it

```bash
node tools/edward-eval/write/run.mjs --batch write-baseline    # 118 dev cases
node tools/edward-eval/write/run.mjs --holdout --batch holdout # 38 holdout cases
node tools/edward-eval/write/run.mjs --category staff_email -v # one category
node tools/edward-eval/write/run.mjs --id w-fup-002 -v         # one case
node tools/edward-eval/write/run.mjs --no-reset --id w-fup-002 # keep the last state
node tools/edward-eval/write/run.mjs --deterministic            # zero provider calls
```

`--deterministic` sends the platform's `X-Edward-Mode: deterministic` header,
which disables the model planner, the prose composer *and* the model
recognition tier. It is the control that says how much of the write plane
depends on a provider at all — currently, none of it: both suites pass in full
with zero model calls.

Artifacts land in `artifacts/runs/<batch>/`:

* `transcript.json` — every turn with its answer, proposed action, preview,
  confirmation, receipt, canonical deltas and findings.
* `summary.json` — verdicts per category, counts per failure code, model spend,
  latency percentiles.
* `bank.csv` — the human-facing question bank: one row per user turn with the
  question, the expected answer in prose, and what Edward actually said.

A full dev run costs about **$0.005** on `gpt-4o-mini`: most action turns are
deterministic and never reach a model. Under `--deterministic` it costs nothing
and still passes, which is the point of having the control.

## Failure codes

Hard (a `FAIL`): `UNGROUNDED_SUCCESS_CLAIM`, `FALSE_INCAPACITY_CLAIM`,
`EFFECT_UNEXPECTED`, `EFFECT_MISSING`, `EFFECT_WRONG`, `WRONG_ACTION`,
`ACTION_WRONGLY_PROPOSED`, `WRONG_TARGET_STUDENT`, `WRONG_TARGET_WORK_ITEM`,
`CONFIRM_STATUS_WRONG`, `RECEIPT_STATUS_WRONG`, `INTENT_STATUS_WRONG`,
`FORBIDDEN_PHRASE`.

Soft (a `PARTIAL`): `ACTION_NOT_PROPOSED`, `PREVIEW_MISMATCH`,
`DENIAL_CODE_UNEXPECTED`, `DENIAL_UNEXPLAINED`, `NO_CLARIFYING_QUESTION`,
`MISSING_FACT`, `NO_HELP_ROUTE_OFFERED`, `NO_DRAFT`, plus the per-case `soft`
codes.

`FALSE_INCAPACITY_CLAIM` is hard on purpose. An assistant that refuses a
request it can perform, in words that say it is read-only, is worse than one
that fails: the user believes the product cannot do the thing, and stops
asking.

## Case-authoring rules

- Every question must be answerable — or intentionally unanswerable — from the
  *current* state of the fixture student or staff member. Check the row before
  writing the expectation; two cases in the first draft asserted a requirement
  that turned out to be already complete.
- Grade meaning, not wording. Use alternations for legitimate phrasings, and
  reserve `forbidden` for claims that would be *wrong*, not merely different.
- Effects are measured from the start of the **case**, so a later turn can
  assert what an earlier one committed. `noEffect` is measured around a single
  **turn**, so it means "this turn changed nothing".
- A case that confirms must assert the canonical effect, not just the receipt.
  A receipt is Edward's account of itself; the row is not.
- Holdout cases are written before the baseline and never edited to make a run
  pass. If a holdout expectation turns out to be wrong about the data, fix the
  expectation and say so in the report.
