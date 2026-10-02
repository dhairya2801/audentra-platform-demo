# Five-session final integration

Integration of Sessions 1–5 into clean Platform and Portals `main` branches,
2026-08-17.

## Result

All five feature sets are on `main` in both repositories and pushed to `origin`
as fast-forwards. Session 4's routing coverage gate was validated by a full
A/B on the integrated tree and **promoted to the production default**. One
performance defect that only appears when Session 1 meets Session 5's
population was found and fixed. No functionality was dropped from any session.

## Exact SHAs

| | Platform | Portals |
| --- | --- | --- |
| Local `main` at session start | `13babd284f997cbcb87dadde5d2d388c8c9927bc` | `c2c75e53cd099c97423914ca62ddf60b3cfa6d71` |
| `origin/main` at session start | `9f6b8f2c0136a28c47cdca9da18e5080858e2feb` | `7604ae846104710339e7dd85c3bab82f90022341` |
| Integration baseline | `a062592f93ab2a290d2603e4614006aa9b5ba7ab` (`feat/edward-parity-integration-v2`) | `9c2126533ca6a338d97e23d16b12f8a7200b2053` (`chore/sync-staff-edward-contracts`) |
| Final code tip (last commit before this report) | `002f43bf67c9b4871f5189449437bfbf5f2d396e` | `ada75756c7acf2dd5c9b421be3a2c0036aa7394d` |
| **Final `main` = `origin/main`** | **`2286de849e64ef28c1edb18b5cc0bc153ce70e19`** | **`ada75756c7acf2dd5c9b421be3a2c0036aa7394d`** |

The Platform tip advances by one over the code tip because this report is itself
a commit on `main`; Portals has no report commit, so its two rows coincide.

## Phase 0 — what inspection found

All eleven worktrees were clean; **no completed work existed only as uncommitted
changes**, so nothing had to be rescued before integrating.

Two facts changed the plan:

1. **Local `main` was a superseded lineage, not a behind-by-N branch.** Platform
   `main` (`13babd2`) carried a seven-commit Edward parity stack that diverged
   from `origin/main` at `2726b6f`. `feat/edward-parity-integration-v2` is that
   work *re-ported* onto the newer `origin/main` — its own report says so
   ("No parity branch was merged or cherry-picked wholesale"). Merging the old
   line would have resurrected `infrastructure/preview/staff_workspace.py`
   (deleted upstream, and explicitly out of scope) and collided on migration
   ordinals: the old stack numbered its migrations `0035`/`0036`, which upstream
   already uses for `assistant_message_ownership` and
   `tenant_portal_configuration`. `main` was therefore moved onto `origin/main`
   rather than merged with it.
   The superseded tip is retained on three refs: `backup/edward-parity-stack-20260816`,
   `feat/staff-edward-read-parity-v1` (both exactly `13babd2`), and the new
   `backup/local-main-pre-integration-20260817`. Nothing was lost.

2. **Every session already built on the same baseline.** All five Platform
   branches have both `origin/main` and `feat/edward-parity-integration-v2` as
   ancestors; all four Portals branches descend from
   `chore/sync-staff-edward-contracts`. The tested parity integration is
   therefore in the final history ahead of the five features by construction.

## Phase 1 — Session 5 Portals branch state

**No repair was required.** The reported hygiene concern did not reproduce as
described: what was on `chore/sync-staff-edward-contracts` was the *baseline*,
not stranded Session 5 work. The Portals repository's primary worktree merely
had that baseline branch checked out, which is what made it look like the
session lived there.

Verified before touching anything:

- `feat/canonical-demo-university` = baseline `9c21265` + exactly one commit,
  `31864fd feat(sign-in): log in as a chosen demo student` (6 files: the gate
  helper, api-client demo session calls, the sign-in panel, its CSS, its tests,
  and `.env.example`).
- The canonical Staff Edward contract sync (`49e1ea5`, `packages/contracts/src/index.ts`)
  is reachable from the branch **through the baseline**, so it is preserved
  without being duplicated — the branch's own diff against the baseline does not
  touch the contracts file at all.
- No unrelated work is mixed in.

The only action taken was moving the Portals primary worktree off the baseline
branch and onto `main`.

## Phase 2 — integration baseline

`feat/edward-parity-integration-v2` had **not** merged into `origin/main`; it is
a descendant of it (merge base = `origin/main` tip). Baseline established as:

- Platform: `main` reset to `origin/main` (`9f6b8f2`), then merged
  `feat/edward-parity-integration-v2` → fast-forward to `a062592`.
- Portals: `main` fast-forwarded to `chore/sync-staff-edward-contracts`
  (`9c21265`), which already contains `origin/main` via its merge commit.

Newer remote work was preserved (the 18 commits `2726b6f..9f6b8f2`, including
the Cloud Run/WIF CI fixes, DB-backed tenants, and the preview VM hardening).
Removed preview infrastructure was **not** revived. No force-push at any point.

`packages/contracts/src/index.ts` was byte-identical across the two repositories
at the baseline, and after every subsequent merge.

## Phase 3 — merge order and conflicts

Merged with `--no-ff` so each session is one auditable merge commit.

| # | Session | Platform merge | Portals merge | Conflicts |
| --- | --- | --- | --- | --- |
| 1 | S5 canonical demo university | `2a5a299` | `1b74ae9` | none |
| 2 | S1 Morning Brew | `ffb760c` | `78f0aaa` | none (auto-merged `globals.css`, `api-client.ts`) |
| 3 | S3 Lab deterministic compare | `696b7e3` | `918fdc1` | none (auto-merged `core/ports.py`, `postgres_service.py`, `routes.py`, `api-client.ts`, `rendered-html.test.mjs`) |
| 4 | S2 Edward response quality | `9be8bd5` | `ada7575` | none (auto-merged `platform_service.py`, `postgres_service.py`, `contracts/index.ts`) |
| 5 | S4 routing coverage | `530e482` | — | none (auto-merged `assistant/pipeline.py`) |

**No merge produced a textual conflict.** Because a clean auto-merge is not the
same as a correct one where two sessions edited the same file, each overlap was
verified semantically rather than assumed:

- **`postgres_service.py` / `platform_service.py` (S1 + S2 + S3).** S3 wraps
  every pipeline construction site in `model_hook(execution.mode, …)`; S2
  threads `presented_blocks` through the composer adapters those hooks wrap.
  Confirmed both survive together: `model_hook` appears at all four construction
  sites, and the adapters it wraps still accept and forward `presented_blocks`.
  This is the "S3 added execution-mode plumbing, S2 added composition plumbing,
  preserve both" case, and both are present.
- **`assistant/pipeline.py` (S2 + S4).** S2's `describe_blocks_for_prompt` /
  `presented_blocks` and S4's `coverage_gate` hook coexist; 87 targeted tests
  across the coverage-gate, response-quality, execution-mode and pipeline suites
  pass together. S4 does not touch `compose.py` or `guard.py`, so S2 keeps sole
  ownership of composition — nothing was overwritten with an older baseline.
- **`contracts/src/index.ts` (S1 + S2).** S1 adds `StaffMorningBrew` and ten
  supporting types; S2 adds `rowHrefs` to the table block. Both landed, and the
  two repositories' copies are byte-identical (`diff` clean) in the final state.
- **`core/ports.py` (S3 + S5), `routes.py` (S1 + S3).** S5 put its demo auth in
  `auth_routes.py` / `demo_identity.py` rather than `routes.py`, so the expected
  collision never materialised.

Migration numbering needed no renaming: only S5 adds one (`0039_student_external_reference.sql`),
directly after the baseline's `0038`.

### Functionality preserved, per session

**S5 — canonical demo university.** Synthetic-university import into canonical
PostgreSQL (2,576 of 3,000 students, 70,069 rows); `compact` vs
`synthetic_university` seed profiles with the larger one strictly additive;
migration `0039` as the correct next ordinal; demo-only `POST /v1/auth/demo/sign-in-as`;
tenant isolation; login persistence (sign-in performs reads only); the
`housing_preference → enrollment_deposit` dependency override for the demo
campus; shared canonical state across Student portal, Staff and Edward.
Production auth is not weakened, and denied/waitlisted applicants are still
rejected rather than given fabricated offers.

**S1 — Morning Brew.** `CohortSql` remains the single shared SQL translation
used by both the briefing and Staff Edward; deterministic canonical metrics
only; no risk/confidence/revenue values; real rolling-24h delta semantics with
inexact classes labelled; unsupported metrics declared unavailable; the Edward
drawer calls the real Staff Edward. The 1,855-line hardcoded `content.ts` is
**not** restored — confirmed absent from the final tree.

**S3 — Lab deterministic compare.** `x-edward-mode` header with exactly two
members (`default`, `deterministic`); structural zero-provider-call deterministic
execution via `model_hook`; production gating (five independent checks in
`assistant_execution.py`); `executionMode` / `ignoredExecutionModeRequest` on the
trace; the Lab's Normal-vs-Deterministic UI. `compose.py` and `guard.py` (both
assistants) verified byte-identical to the baseline after this merge, which is
exactly the clean ground S2 needed.

**S2 — response quality.** Prose synthesises while blocks carry collections;
`rowHrefs`; canonical route modules (`assistant/links.py`,
`staff_assistant/links.py`); internal-link validation; block-aware rewrite
prompts; staff caveat preservation; the guard and draft fixes.

**S4 — routing coverage.** See Phase 4.

## Fix made during integration: Morning Brew at 2,576 students

S1's own report asked for exactly this check ("re-run the briefing against
Session 5's population to confirm the bounded reads hold at 3,000 students"). It
did not hold, for a reason that has nothing to do with either session's logic.

Against the demo tenant the briefing took **2,419 ms**, of which **2,109 ms** was
the single 24-cohort `COUNT(*) FILTER` pass. `EXPLAIN (ANALYZE, BUFFERS)`
attributed **1,821 ms of a 2,227 ms execution to JIT**: the statement's planner
cost (1,225,182) clears `jit_inline_above_cost` and `jit_optimize_above_cost`, so
PostgreSQL compiled and optimised **508 expression functions** to serve a ~400 ms
scan. Everything else in the briefing was already fast (deltas 6 ms, deadline
runway 20 ms, requests 4 ms, work summary 2 ms, scan freshness 1 ms).

Fix (`dabfb28`): `SET LOCAL jit = off` inside that statement's own transaction.

| | before | after |
| --- | --- | --- |
| `count_cohorts` | 2,109 ms | 363 ms |
| whole briefing (in-process) | 2,419 ms | 528 ms |
| `GET /v1/staff/morning-brew` over HTTP | — | 691 ms |

The counts are unchanged — all 24 cohorts still equal `findStudents(...).total`
and the serialized payload is the same 38,850 bytes. `SET LOCAL` is
transaction-scoped, verified not to leak: a pooled connection still reports
`SHOW jit = on` after the read. The architecture S1 chose (one pass so the
funnel is consistent at a single instant) is kept intact; only the compiler is
kept out of it.

## Phase 4 — S4 full A/B and promotion

S4 was rebased onto the fully integrated S1/S2/S3/S5 tree first, preserving S2's
composer and guard.

**Deterministic corpus, re-run on the integrated tree** (16 cases, no provider):

| mode | strict coverage | missed asks | gate fired | model calls |
| --- | --- | --- | --- | --- |
| default | 5/14 | 13 | 0 | 0 |
| **augment** | **13/14** | 1 | 9 | **0** |
| planner | 13/14 | 1 | 9 | 0 |

Strict (planned ∧ answered) coverage reproduces the report exactly. Experienced
coverage reads 13 rather than 14 on both sides because S2 moved collections out
of prose and into blocks and this grader reads prose; the decision metric is the
persona-independent strict number, and it is unchanged.

**Full judged-tier A/B, live provider, 376 cases / 446 turns per side:**

| | default | augment |
| --- | --- | --- |
| deterministic pass | 363/376 | **366/376** |
| tool-selection accuracy | 98.3% | **98.7%** |
| unnecessary-tool rate | 0% | 0% |
| hallucination rate | 0% | 0% |
| **planner calls (fall-through)** | **69** | **69** |
| composer calls | 405 | 407 |
| cost / turn | $0.000289 | $0.000295 |
| server p50 / p95 | 1198 / 2459 ms | 1218 / 2451 ms |
| critical invariant failures | **1 (`conf-001`)** | **0** |

`compare.mjs`: **"No deterministic regressions. Every case that passed before
still passes."** Three cases newly pass:

- **`conf-001` (critical)** — "I already paid my deposit weeks ago. Why does it
  still show as due?" First-match routing classified this `deadlines`, read only
  `getStudentDeadlines`, and the model then speculated ("the payment may not have
  been processed... if you paid after that date"), tripping the deposit-state
  grounding guard. The account was never read. Augment supplements the account
  intent and the answer becomes grounded.
- `xd-009`, `mt-011`.

`conf-001` was **not** an integration regression: `classify.py`, `normalize.py`,
`planner.py`, `student_state.py` and `derive.py` are byte-identical to the
`a062592` baseline, so the routing gap and the deposit derivation behind it are
pre-existing. Model phrasing is what makes it visible from run to run — which is
precisely the argument for closing the routing gap rather than the phrasing.

The identical **69 → 69 planner calls** is the load-bearing number: the gate
resolves gaps through the deterministic classifier and never escalates to a
model. The two extra composer calls are turns that previously had no evidence
worth rewriting.

### Promotion performed

**Augment is now the production default routing behaviour** (`07b9642`).

- `resolve_coverage_gate_mode` defaults to `augment`.
- **Planner-gate is not enabled** and is wired nowhere. It remains implemented;
  measured against augment it bought no coverage, made one case worse, and cost
  4.5× the model calls.
- The experimental opt-in flag `AUDENTRA_EXPERIMENTAL_COVERAGE_GATE` is retired
  in favour of `AUDENTRA_ASSISTANT_COVERAGE_GATE`, which now also accepts `off`.
- **Clean disable mechanism preserved** (the architecture already supported it):
  `AssistantPipeline(coverage_gate="off")` in process, or
  `AUDENTRA_ASSISTANT_COVERAGE_GATE=off` at deployment. An unrecognised value
  falls back to the default rather than silently disabling routing coverage.
- **Trace observability kept.** The gate records its stage on every classified
  turn, so "the route already covered the request" (`action: fully_covered`) is
  observable instead of inferred from a missing stage.
- **Compound cases promoted into the normal regression suite**:
  `tools/edward-eval/src/cases/compound-requests.mjs`, 7 cases (383 total), each
  pinning the dropped second ask with `requiredTools`, plus two context-mention
  cases pinning that the gate does not widen a single-ask turn.

Those promoted cases were checked for discriminating power, not just added:
**7/7 pass with the gate on, 3/7 with it off** (4 fail `TOOL_NOT_CALLED` /
`INCOMPLETE_RESPONSE`), while both context-mention cases pass in *both* modes.

Three tests that pinned the old default were re-pointed at `coverage_gate="off"`
so they still assert the pre-gate behaviour — they are now the escape hatch's
regression coverage rather than dead assertions
(`test_assistant_coverage_gate.py`, `test_assistant_history_and_cross_domain.py`,
`test_assistant_trace.py`).

## Phase 5 — full integrated gates

Disposable `postgres:17.5-alpine` container `audentra-integration-pg`, bound to
`127.0.0.1:55450` only, removed afterwards. `vv_enrollment` was never a target;
the test-environment script carried a guard that refuses any URL naming it or
the dev stack ports.

**Platform**

| Gate | Result |
| --- | --- |
| Migrations from an empty database | 41 applied, chain ends at `0039_student_external_reference.sql` |
| Migration idempotency | second run applies 0; ledger holds 41 rows |
| `pytest` (migrated + seeded DB) | **977 passed, 3 skipped, 0 failed** |
| `pytest -m postgres` | **71 passed** |
| `ruff check` + `ruff format --check` | clean, 204 files |
| `mypy src tests` | clean, 204 files |
| `npm run test:node` | **140 passed** (6 + 116 + 18) |
| Student Edward smoke | **45/45**, tool selection 100% clean, hallucination 0% |
| Staff Edward eval | **36/36** cases (41 turns) |
| Edward coverage matrix | 383 cases / 453 turns / 62 capabilities / 16 personas / 23 tools |
| Routing-coverage regression cases | **7/7** (3/7 with the gate off) |
| Deterministic-mode zero-provider tests | **16 passed** |
| Morning Brew Postgres tests | included; brew + cohort suites **64 passed** |
| Synthetic-university Postgres tests | included; S5 suites **119 passed** |
| Auth / demo login tests | included in the above |

**Portals**

| Gate | Result |
| --- | --- |
| `npm run test` | **85 passed, 0 failed** |
| `npm run typecheck` | clean |
| `npm run lint` | **0 errors**, 10 pre-existing `<img>` warnings |
| Production build | succeeds; `/dev/edward` returns 404 from the built worker |

**Contracts identical across repos:** `diff` clean on
`packages/contracts/src/index.ts` in the final state.

### The one test that failed, and why it is not ours

`test_auth_postgres_integration.py::test_real_postgres_browser_auth_and_deterministic_reset`
fails on a database that has been migrated but not seeded (404 on
`/v1/staff/workspace` at line 246). It passes as soon as the demo fixture is
seeded, which is how the full suite above was run.

S5's report described this as order-dependent in the opposite direction, so it
was checked rather than accepted: **the same test, on a pristine database, fails
identically at the pre-integration baseline `a062592`** (verified in the
`Audentra-platform.integration` worktree). Pre-existing, environmental, and not
caused by the integration. Its real precondition is "the target database has
been seeded", not "no other test ran first".

## Phase 6 — integrated smoke against the synthetic university

Integrated API run against a disposable database seeded with
`--profile synthetic_university` (2,576 students in `aster-demo`, plus the
untouched 14-student `aster` and `harvard` compact fixtures — the additive
profile behaves as documented). Seed wall clock 32.7 s on this machine.

**Student demo sign-in — all five requested students.** Every one signed in via
`POST /v1/auth/demo/sign-in-as`, and all six portal reads (`bootstrap`,
`dashboard`, `requirements`, `documents`, `financials`, `payments`) returned 200
for each. Requirement statuses matched S5's persona table exactly:

| Student | Portal state read back |
| --- | --- |
| `SYN-000000` | all eight requirements `completed` |
| `SYN-000002` | transcript `under_review`, housing + orientation `blocked`, deposit `ready` |
| `SYN-000003` | transcript `rejected`, aid verification `ready`, deposit `completed` |
| `SYN-000007` | aid verification `in_progress`, housing + orientation `blocked` |
| `SYN-000009` | transcript + immunization `ready`, housing + orientation `blocked` |

**Edward agreed with portal state on every question**, for every student. All six
representative questions were asked in the same authenticated session. Examples:

- `SYN-000002` "What documents am I missing?" → names the transcript under review
  and the unpaid deposit — matching the requirement rows above.
- `SYN-000003` "What is my deposit status?" → "paid and is confirmed on your
  record", matching `enrollment_deposit: completed`.
- `SYN-000000` "Why can't I apply for housing?" → "nothing blocking you; your
  housing step is already complete", matching `housing_preference: completed`.

**The compound questions exercised S4 coverage in production default.** Both
routed through the gate rather than dropping an ask:

- "What's my account balance and what clubs can I join?" →
  `campus_life + [student_account]`, `source=coverage_gate`, reads include
  `getStudentAccountSummary`. `SYN-000007` answer: *"Your remaining balance is
  $47,400, and your $500 enrollment deposit has not been paid yet… You can check
  out the upcoming campus events listed below."* Both asks answered.
- "I paid my deposit. Why can't I apply for housing and what documents am I
  missing?" → `housing_eligibility + [holds_and_blockers, missing_documents]`,
  `source=coverage_gate`, reads include `getDocumentStatuses`. Both asks
  answered on all five students.

**S2 formatting and links.** For the document-status turn on `SYN-000002`, the
table carried four rows with `rowHrefs`
`/enrollment/requirements/{identity-document-upload, transcript-upload,
financial-aid-verification, immunization-upload}` — all internal, zero external
or malformed hrefs — and **0 of the 4 row values were restated verbatim in the
prose**, in both execution modes. Deterministic prose reads *"Of your 4 required
documents, 3 accepted, 1 under review. The table shows each one:"*.

**Staff — Morning Brew at full population.** `GET /v1/staff/morning-brew`
returned 200 in **691 ms** for the 2,576-student tenant:

- roster 2,576; deposit paid 1,892; deposit outstanding 684 (partitions
  correctly against 2,576 accepted offers)
- four attention cards, all plausible and scoped: *911 deposited students with an
  overdue requirement (911 of 2576)*, *1,448 with an overdue requirement*, *684
  accepted who still owe a deposit*, *480 whose aid file needs their action*
- **no fake confidence/risk/revenue values**: a grep of the serialized payload
  for `meltLikelihood`, `recoveryLikelihood`, `opportunityLikelihood`, `riskBand`,
  `riskScore`, `Confidence:`, `netTuition`, `revenueImpact`, `targetProgress`,
  `Higher Ed News`, `Outlook` returned **NONE**
- synthesis `source: deterministic`; five unsupported metrics declared on the payload

**Cohort opened through Staff Edward — headcount agrees exactly.** Asked the
first card's own question, *"Which deposited students have an overdue
requirement?"*: Staff Edward answered **"There are 911 students who have paid
their deposit and have an overdue requirement"** against the card's declared
**911**, and returned a Student/Program/Deposit/Open-blocking table of 20 sampled
rows with the truncation stated in prose — S1's round-trip guarantee and S2's
cohort table format, both intact at scale.

**Edward Lab — Normal vs Deterministic, same question, same student:**

| | `default` | `deterministic` |
| --- | --- | --- |
| trace `executionMode` | `default` | `deterministic` |
| **model calls** | **1** (`assistant_composer`) | **0** |
| provider | `openai` | `guided` |
| token usage | 1,331 / 85 | none |
| `responseSource` | `model_prose` | `deterministic` |
| server duration | 1,187 ms | **14 ms** |
| tool reads | `getOnboardingChecklist`, `getDocumentStatuses`, `getStudentAccountSummary` | **identical** |

Normal permits model calls, deterministic issues zero, and **both sides read the
same underlying student state** — the identical tool list is the proof that the
comparison isolates the rewrite rather than two different retrieval paths.

### What was not verified through the browser

The staff workspace and the Morning Brew navigation did render in a real
Chromium session against the integrated API. The remaining browser-level checks
(Morning Brew page body for the demo tenant, and `/dev/edward` in the UI) were
**not** completed, for two local-environment reasons rather than product ones:

1. `/dev/edward` needs `NEXT_PUBLIC_DEFAULT_TENANT_SLUG`, which S3's own commit
   message documents as a local-run requirement. This workstation's portal build
   inlines `NEXT_PUBLIC_*` from ignored `.env*` files (per `vite.config.ts`: "application
   environment belongs in ignored `.env*` files"), and the untracked
   `apps/web/.env.local` here predates these sessions and does not set it. I did
   not edit the developer's local env file. S3's `/dev` reservation is present
   and correct in `main` (`tenant.ts:121`), and the route resolves — the served
   page title is "Edward Lab | Audentra".
2. `/aster-demo/staff` refuses the auto-authenticated demo staff identity
   ("not provisioned for this tenant"), which is correct tenant isolation: the
   default demo actor belongs to `aster`.

Both surfaces are covered by the Portals automated suite (85 tests, including the
Morning Brew render tests and the `rendered-html` greps for `Confidence:`,
`meltLikelihoodPercent`, `risk.band`, `Higher Ed News`, `Outlook`, `$NNM`) and by
the API-level evidence above.

Separately, an untracked `apps/web/.env.local` on this machine sets
`NEXT_PUBLIC_EDWARD_DEBUG_ENABLED=true`, which makes the production build ship
the Lab and fails `edward-lab.test.mjs`'s production-gating assertion. All
Portals gates above were therefore run as CI does, with that flag off, giving
85/85. This is a workstation config artifact, not a code defect.

## Final state

**Worktrees**

```
/path/to/projects/Audentra-platform              002f43b [main]
/path/to/projects/Audentra-platform.integration  a062592 [feat/edward-parity-integration-v2]

/path/to/projects/Audentra-portals               ada7575 [main]
/path/to/projects/Audentra-portals.integration   7f97e6c [feat/edward-integration]
```

**Worktrees removed** (plain `git worktree remove`, no `--force`, after proving
each was clean and each branch tip reachable from `origin/main`; `git worktree
prune` run in both repositories):

`Audentra-platform.{s1-brew, s2-quality, s3-lab, s4-router, s5-population}`,
`Audentra-portals.{s1-brew, s2-quality, s3-lab, s5-population}`.

Two leftover processes from Session 5 (an `audentra-api` and a portal dev server
running out of the s5 worktrees) were stopped first.

**Branches intentionally retained** — Platform: `feat/student-edward-read-parity-v1`,
`feat/staff-edward-read-parity-v1`, `backup/edward-parity-stack-20260816`,
`backup/pre-integration-20260811`, `backup/local-main-pre-integration-20260817`
(new), `feat/edward-parity-integration-v2`, `feat/edward-integration`, and all
five feature branches. Portals: `backup-before-deploy`,
`backup/pre-integration-20260811`, `backup/local-main-pre-integration-20260817`
(new), `chore/sync-staff-edward-contracts`, `feat/morning-brew-executive-brief`,
`feat/edward-integration`, and the four feature branches. Nothing was deleted.

**Migrations in final order** (41 total, tail):

```
0033_assistant_conversations.sql
0033_postgres_contract_hardening.sql
0034_assistant_voice_sessions.sql
0035_assistant_message_ownership.sql
0036_tenant_portal_configuration.sql
0037_ai_provider_openai.sql
0038_staff_assistant_conversations.sql
0039_student_external_reference.sql
```

**Final student count in the demo tenant:** 2,576 in `aster-demo` (424 of 3,000
generated applicants rejected as denied/waitlisted, unchanged from S5), alongside
14 in `aster` and 14 in `harvard`.

**Final git status**

```
Audentra-platform:  On branch main
                    Your branch is ahead of 'deploy/main' by 89 commits.
                    nothing to commit, working tree clean
                    main == origin/main == 2286de849e64ef28c1edb18b5cc0bc153ce70e19*

Audentra-portals:   On branch main
                    Your branch is ahead of 'deploy/main' by 13 commits.
                    nothing to commit, working tree clean
                    main == origin/main == ada75756c7acf2dd5c9b421be3a2c0036aa7394d
```

\* plus the one-line amendment to this report's own SHA table, pushed with it.

Both branches also track `deploy/main`, which was deliberately not pushed —
`origin` was the only push target.

## Remaining known product gaps

Carried forward from the session reports, plus what integration added:

1. **`nc-1`-class wide questions.** The 2-supplement cap drops the fourth-plus
   ask of a very wide compound question; drops are recorded on the trace.
2. **Ten stable full-tier eval failures** remain, all non-critical and all
   pre-existing: `enr-009, enr-018, enr-028, hou-009, amb-003, amb-008, mt-002,
   mt-030, mt-034, mt-041`.
3. **Morning Brew deltas.** `requirements_completed` and `work_items_closed` are
   last-write proxies (`exact: false`); attention depth is capped at 4; deadline
   runway is capped at 12 rows / 30 days.
4. **Demo population data quality.** No declined offers; `identity_document` is
   uniformly complete; holds, international requirements, disbursement schedules
   and room assignments have no canonical home (counted in `dropped_records`).
5. **Staff per-entity deep links** are still impossible until the staff portal
   gets URL-addressable students and work items.
6. **The eval CI gate** documented in `tools/edward-eval/README.md` still does not
   exist; nothing enforces critical cases on PRs. This integration found a
   critical case (`conf-001`) that only a full run surfaces, which strengthens
   the case for wiring it.
7. **The eval spend ledger** is at $3.29 of its $4.00 tracked ceiling after this
   A/B; the next full judged run will need it raised or reset.
8. **Local portal env wiring** for `/dev/edward` (see Phase 6) is undocumented
   outside a commit message; `apps/web/.env.example` would be the place to state
   `NEXT_PUBLIC_DEFAULT_TENANT_SLUG`.
9. **Session 5's disposable container `audentra-s5-pg`** (port 55445) is still
   running on this machine; it was left alone as it was not created here.

---

GREEN — ALL FIVE INTEGRATED AND CLEAN
