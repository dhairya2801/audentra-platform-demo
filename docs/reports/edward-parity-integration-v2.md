# Edward parity integration v2

## Result

The integration uses the current remote architecture as its base and manually ports the valuable Student and Staff Edward capabilities. No parity branch was merged or cherry-picked wholesale.

- Branch: `feat/edward-parity-integration-v2`
- Exact fetched `origin/main` base: `9f6b8f2c0136a28c47cdca9da18e5080858e2feb`
- Base rechecked immediately before reporting: unchanged
- Merge base with `origin/main`: `9f6b8f2c0136a28c47cdca9da18e5080858e2feb`
- Implementation delta before this report: 7 commits, 109 files, 27,761 insertions, 1,678 deletions
- PostgreSQL test target: disposable `postgres:17.5-alpine` container named `audentra-parity-test-postgres`, database `audentra_parity_test`, bound only to `127.0.0.1:55439`
- Database lifecycle: complete migration chain applied, tests completed, explicitly named container stopped and removed; no developer, preview, staging, or production database was used

## Remote architecture preserved

- Stateless FastAPI service construction and stateless Staff workspace composition remain authoritative.
- `PostgresTenantRepository` remains the production tenant resolver.
- Tenant portal configuration, support contacts, and managed configuration remain PostgreSQL-backed. Student help reads `tenant_portal_configuration.contacts.support`; no hardcoded parity contact fallback was introduced.
- Existing Student assistant conversation ownership, exchange replay, and transaction behavior were retained and extended rather than replaced.
- Current PostgreSQL contract hardening, signed-template checks, tenant operations, and configuration behavior remain intact.
- Existing session cookie and SameSite validation remain intact. Cross-site settings were not weakened.
- Cloud Run and preview VM infrastructure was not replaced or redesigned.
- Generic Audentra/institution branding remains. The only former demo-brand token in an Edward production module is a normalization stop-word, which prevents it from being parsed as a student name; it is not emitted branding or a tenant assumption.
- Strict Python and TypeScript typing is preserved. MyPy checked 185 source/test files and all TypeScript workspaces passed typecheck.

## Parity functionality ported

### Student Edward V2

- Canonical deterministic-first Python execution.
- Optional, guarded model planning/composition with strict structured output, local normalization, validation, and deterministic retry feedback.
- Durable conversation history, multi-turn referent handling, cross-domain routing, missing-information handling, and expanded planning.
- Trace and model-attempt recording plus developer-only trace/persona endpoints. Trace debug is prohibited in production settings.
- Current evaluation personas, coverage tooling, deterministic smoke harness, tracing, cost ledger, and failure observability.

### Student portal/read parity

- Added canonical `domain/student_state.py` semantics.
- Deposit status is derived from canonical payment/account data as `unknown`, `pending`, `unpaid`, or `paid`; no code assumes `dashboard.offer.depositPaid` exists.
- Added 23 portal-aligned Student Edward tools and expanded derive/composition behavior.
- Read availability and tracked-state semantics distinguish unavailable data from a known empty or incomplete state.
- Portal/Edward agreement tests cover deposit, onboarding, documents, holds, deadlines, housing, aid, registration, accounts, and related reads.
- Financial-aid document satisfaction requires canonical `verified` state.
- Production reads use the newer explicitly typed PostgreSQL collection and retain DB-backed support contacts.

### Staff Edward

- Read-only Staff Edward with server-bound tenant and staff identities.
- Tenant-safe student lookup, individual student reads, timeline, communications, inquiries, work items, and response drafting.
- Durable conversations, referent-aware follow-ups, planner/tool catalog/composer, and strict tool-argument validation.
- No Staff Edward tool reads preview melt, risk, recovery, or probability fields. Requests for fabricated metrics are explicitly refused and redirected to canonical evidence.

### Staff cohort parity

- Added canonical `domain/student_cohort.py` vocabulary.
- Added `findStudents` and `summarizeStudents` with one shared predicate builder for list, total, and grouping semantics.
- Supports program, class year, offer, deposit, onboarding, requirement, document, aid-document, housing, assigned-staff, open-work-item, overdue-requirement, residency, and citizenship filters.
- Supports grouping and grounded `total`, `returned`, and `truncated` pagination metadata.
- Deposit and housing filters/grouping use their respective shared canonical bucket functions.
- Aid-document `satisfied` means verified, and unsupported filters/groups fail explicitly instead of being ignored.
- PostgreSQL tests cover every filter family, shared list/count/grouping behavior, pagination, and tenant isolation.

### Seed and lab/eval integration

- Added a realistic 14-student-per-tenant funnel covering offered, accepted, deposited, ready, and declined states plus aid/document/housing/residency/GPA variation.
- Preserved remote reset behavior: tenant-owned and managed configuration records retain their existing ownership/overwrite rules, and no preview workspace runtime state was added.
- Added the canonical Python Edward Lab backend, personas, trace surfaces, Student cases, Staff cases, deterministic fallback reporting, and spend controls.
- Production portal routes continue to dispatch to canonical Python Edward.

## Functionality intentionally not ported

- `PreviewStaffWorkspaceRepository`, `audentra.infrastructure.preview`, `staff_preview`, `_preview()` service fallbacks, mutable request-time preview synchronization, and the deleted `infrastructure/preview/staff_workspace.py`.
- Compiled tenant/demo-student slug maps.
- Synthetic preview risk, melt, recovery, or enrollment-probability data as assistant inputs.
- The legacy AI gateway `ask_edward` method and the legacy guided keyword Student chat path. The remaining guided module is limited to document extraction fallback needed by the canonical architecture.
- Aster-specific runtime branding or tenant assumptions.
- Hardcoded support contacts.
- The parity branches' conflicting old `0035`/`0036` migrations.
- The proposed 3,000-student seed population or a seed architecture redesign for that scale.
- A production route for the older TypeScript Edward implementation. Existing TypeScript tooling remains non-production development/evaluation code only.

## Migrations

Remote migration files were not altered or renamed. The remote chain already ended at `0036_tenant_portal_configuration.sql`, so the next free migration names were used:

- `0037_ai_provider_openai.sql`: extends the allowed AI provider contract for the canonical provider route.
- `0038_staff_assistant_conversations.sql`: adds durable, tenant-safe Staff conversations and messages.

The complete chain through `0038` was applied to the disposable database. A second migration command completed successfully with no pending migration, and the migration ledger contained both new files. No already-applied migration was modified.

## Staff conversation hardening

Staff persistence was adapted to the current Student guarantees rather than copied from the older parity implementation:

- Composite database foreign keys bind conversation ownership to `(conversation_id, tenant_id, staff_member_id)`.
- Staff members and active/referenced students are tenant-bound by composite foreign keys.
- A transaction-scoped advisory replay lock serializes a staff/client message key before replay lookup or conversation creation.
- Implicit conversation creation and the user/assistant message pair commit atomically.
- Each exchange has exactly one user and one assistant role, protected by a unique exchange/role index.
- Replay/race handling returns the existing exchange without creating an unused conversation.
- Cross-tenant conversation owners and student referents are rejected and roll back without partial records.
- PostgreSQL tests cover an eight-way same-key race, exact replay, distinct concurrent turns, owner isolation, cross-tenant rollback, and durable referent behavior.

## Public contracts and Portals synchronization

Platform's canonical `packages/contracts/src/index.ts` now includes Staff Edward request/response, block, context-receipt, resolved-student, durable-message, conversation, and message-list types. Client inputs intentionally do not accept tenant, staff, or student identity as authority.

The clean sibling `Audentra-portals` repository was synchronized at `packages/contracts/src/index.ts`. That consumer snapshot is deliberately left as one uncommitted change in the sibling repository so this Platform branch does not create an unrelated Portals commit.

- Platform snapshot: `packages/contracts/src/index.ts`
- Portals consumer snapshot: `../Audentra-portals/packages/contracts/src/index.ts`
- SHA-256 of both byte-identical files: `fe25460e7785f31b51fcb6c52157f71a8ea06966c6d2369676aaa60e1b42a8d7`
- Required Portals follow-up: review and commit that single synchronized file in the Portals repository.

## Verification

### Full quality gates

| Command | Result |
|---|---|
| `npm run lint` | PASS — Ruff check and format check; 185 files formatted; all Node workspace lint scripts passed |
| `npm run typecheck` | PASS — MyPy found no issues in 185 files; contracts, state-effects, student-assistant-core, and voice-agent TypeScript checks passed |
| `npm test` with both test database variables set to the disposable URL and provider keys unset | PASS — Python: 781 passed, 3 skipped, 72.8% coverage; all Node workspace suites passed |
| `npm run db:migrate` against the already migrated disposable database | PASS — idempotent, no pending migration |

The three Python skips were all object-storage-only tests because `AUDENTRA_TEST_S3_ENDPOINT` was intentionally not configured: the combined production API S3 case, direct S3 integration, and relational-seed S3 case. The PostgreSQL half of the production API integration and the relational seed database test both ran. No Student or Staff Edward, migration, conversation, parity, seed-database, repository, or concurrency test was skipped.

The full Python run included, among the wider suite:

- Student state domain: 14 passed.
- Student assistant history/cross-domain: 15 passed; model layer: 12 passed; pipeline: 22 passed; trace: 12 passed; gateway: 16 passed.
- Portal/Edward in-memory agreement: 12 passed; PostgreSQL Student parity: 11 passed.
- Staff Edward: 40 passed; PostgreSQL Staff conversation hardening: 6 passed; PostgreSQL Staff cohort parity: 11 passed.
- Migrations: 14 passed; Student assistant conversations: 5 passed.
- PostgreSQL AI attempts: 1 passed; document contracts: 2 passed; payment/profile contracts: 3 passed; portal integration: 2 passed.
- Managed configuration repository: 44 passed; tenant repository: 9 passed; auth repository: 1 passed; Staff repository: 22 passed; help requests: 16 passed.
- Relational PostgreSQL seed integration passed; its separate S3 seed case was the noted object-storage skip.

Visible Node workspace summaries included 114/114 student-assistant/core platform cases, 18/18 Edward eval harness cases, and 56/56 voice-agent cases; every workspace test command exited successfully.

### Edward evaluation gates

All final required evals ran with `OPENAI_API_KEY` and `OPENROUTER_API_KEY` explicitly removed.

| Command | Result |
|---|---|
| `npm run eval:edward:coverage` | PASS — 376 cases, 446 turns, 62 capabilities, 16/16 personas, 23/23 tools covered |
| `npm run eval:edward:smoke` | PASS — 45/45 deterministic cases, 100% clean tool selection, 0% hallucination, $0 cost |
| `npm run eval:edward:staff` | PASS — 36/36 cases, 41 turns |

The coverage report records non-blocking areas where individual tools or dimensions have comparatively weak case counts; all declared coverage and smoke gates pass. No paid/full judged eval was run. During development, one targeted Staff diagnostic inherited an ambient provider credential before the final credential-free gate; the final required runs above were deterministic and cost $0.

### Static invariants

- PASS: no production import of `audentra.infrastructure.preview`.
- PASS: no production `staff_preview` or `_preview()` fallback.
- PASS: `infrastructure/preview/staff_workspace.py` remains deleted.
- PASS: Staff Edward reads canonical PostgreSQL evidence only and never reads synthetic preview risk/melt/recovery values.
- PASS: DB-backed tenant resolution and tenant portal configuration remain.
- PASS: SameSite/cookie security and production trace-debug prohibition remain.
- PASS: generic institution branding remains.
- PASS: support contacts remain DB-backed.
- PASS: Student canonical deposit parity works.
- PASS: aid document satisfaction requires verified state.
- PASS: Staff cohort tenant isolation works.
- PASS: production Student and Staff paths use canonical Python Edward.
- PASS: legacy TypeScript/guided Student Edward cannot serve production portals.

## Commits created

1. `a545b7e` — `feat(assistant): port canonical Student Edward v2`
2. `c1c5cd7` — `feat(assistant): align Student Edward reads with portal state`
3. `00c7b28` — `feat(staff-assistant): add tenant-safe Staff Edward`
4. `9967f1e` — `feat(staff-assistant): add canonical cohort reads`
5. `189fb30` — `feat(seed): add realistic Edward cohort funnel`
6. `b26446e` — `feat(eval): add canonical Edward coverage harness`
7. `00e6259` — `feat(contracts): publish Staff Edward API types`

The report itself is committed separately as the final documentation slice.

## Files changed

The implementation changes 109 files before this report. They are grouped below; this report adds the 110th file.

- Migrations: `apps/api/migrations/0037_ai_provider_openai.sql`, `apps/api/migrations/0038_staff_assistant_conversations.sql`.
- Application/bootstrap/contracts: `apps/api/pyproject.toml`, `application/platform_service.py`, `bootstrap/api.py`, `bootstrap/eval_api.py`, `bootstrap/settings.py`, `contracts/requests.py`.
- Domain: `domain/__init__.py`, `domain/student_state.py`, `domain/student_cohort.py`.
- Infrastructure: `memory/eval_personas.py`, `memory/store.py`, `postgres/portal_repository.py`, `postgres/postgres_service.py`, `postgres/staff_assistant_repository.py`, `postgres/staff_repository.py`, `seeding/relational.py`.
- AI integration: `integrations/ai/edward_safety.py`, `integrations/ai/gateway.py`, `integrations/ai/guided.py`.
- Student assistant: `integrations/assistant/classify.py`, `compose.py`, `derive.py`, `guard.py`, `normalize.py`, `pipeline.py`, `planner.py`, `tools.py`, `trace.py`.
- Staff assistant: every file under `apps/api/src/audentra/integrations/staff_assistant/` (`__init__.py`, `catalog.py`, `classify.py`, `compose.py`, `derive.py`, `guard.py`, `normalize.py`, `pipeline.py`, `planner.py`, `safety.py`, `tools.py`).
- HTTP: `interfaces/http/config.py`, `interfaces/http/routes.py`.
- Python tests: `test_ai_gateway.py`, `test_assistant_history_and_cross_domain.py`, `test_assistant_model_layer.py`, `test_assistant_pipeline.py`, `test_assistant_trace.py`, `test_guided_ai.py`, `test_portal_edward_state_agreement.py`, `test_postgres_edward_portal_parity.py`, `test_postgres_staff_assistant_conversations.py`, `test_postgres_staff_cohort_parity.py`, `test_seed_integration.py`, `test_staff_edward.py`, `test_staff_repository.py`, `test_student_cohort.py`, `test_student_state_domain.py`.
- Root/config/contracts: `infra/.env.example`, `package.json`, `packages/contracts/src/index.ts`.
- Eval: `tools/edward-eval/COVERAGE-legacy-115.md`, `COVERAGE.md`, `README.md`, the top-level eval commands (`calibrate.mjs`, `compare.mjs`, `coverage.mjs`, `inspect.mjs`, `regrade.mjs`, `rejudge.mjs`, `run-staff.mjs`, `run.mjs`), all added/updated files under `tools/edward-eval/src/` and `src/cases/`, and `tools/edward-eval/test/spend-ledger.test.js`.

## Remaining concerns and merge/worktree readiness

- No critical integration failure remains.
- The three skipped S3 cases are unrelated to Edward/PostgreSQL parity and require an intentionally isolated object-storage service; the existing unit storage suite still passed.
- The coverage matrix's weak-coverage notes are future eval-depth opportunities, not missing declared capabilities.
- The sibling Portals contract snapshot is byte-identical but uncommitted and must be reviewed/committed there.
- No 3,000-student runtime population was added. Existing unrelated synthetic-university test tooling in the remote base remains untouched.

The Platform branch is ready to merge after normal review. It is also safe to create the five planned Platform worktrees from this integrated result: the branch is linear from the recorded `origin/main` base, its Platform worktree is clean, its migrations are unique post-base additions, and all critical verification is green. Any Portals worktree that consumes Staff Edward types should include the separately synchronized contract snapshot.

GREEN — READY TO MERGE
