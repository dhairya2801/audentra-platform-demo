# Audentra vNext integration

This branch uses the exact synthetic-university implementation as its base. The sibling `portals` clone contains its captured dirty working state plus the intentionally adapted deployed/demo UI. The workspace-level `IMPLEMENTATION-REPORT.md`, `PROVENANCE.md`, and `provenance/` contain the complete audit and results. No source worktree is needed at runtime.

## Local services

The integration created its own PostgreSQL 17 cluster at `artifacts/integration/pgdata`, bound to **127.0.0.1:55591**. Its database is `audentra_university_vnext`. Other databases in this cluster with `test_vnext` in their names are disposable test databases. No existing Audentra or synthetic database is used by these services.

- Portal: `http://localhost:3009/financials`, `/staff`, `/profile`.
- Edward Lab: `http://localhost:3009/dev/edward` and `/dev/staff-edward`.
- API: `http://127.0.0.1:45619`.
- Atlas live: `http://127.0.0.1:4321`.

API and Atlas are loopback development tools. They are not a deployment authentication configuration. The API starts no worker, uses GPT-5.6 Luna with the bounded model read planner, and requires `OPENAI_API_KEY` in its environment. Credentials stay outside Git. Interactive Edward calls incur provider usage separately from the evaluation harness.

To start the API in this clone, using the already-created integration database:

```bash
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/run_runtime.py \
  --database-url postgresql://dhairya2801@127.0.0.1:55591/audentra_university_vnext \
  --port 45619
```

To start Atlas live:

```bash
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/server.py \
  --database-url postgresql://dhairya2801@127.0.0.1:55591/audentra_university_vnext \
  --port 4321
```

The sibling portal's ignored `apps/web/.env.local` points its API and Lab proxy to port 45619. Run `npm --workspace @vv/web run dev -- --port 3009` from `portals`. Avoid the original synthetic portal's port 3000 and do not start a second vinext server from the same checkout.

## Reproducible rebuild

Use a **new empty local database** for a rebuilt institution. Run the existing migration command with that database's explicit `DATABASE_URL`, build with `tools/university/build.py --output <new-output>`, then run `tools/university/import_runtime.py --database-url <new-loopback-university-db> --source <new-output>/university.sqlite`. The full importer is one-time initialization, not a reset job.

`tools/university/import_product_runtime.py --database-url <explicit-loopback-university-db> --source <university.sqlite>` adds the new catalog and approved club publications to a previously imported integration database. It does not overwrite planning inputs, club edits, existing ledger entries, or original university records. Published clubs are canonical `public.student_club` records; `fixtures/club-directory.json` is deterministic publication input, not a second runtime directory. The builder emits `campus-content.json` beside its evaluation database.

Atlas without `--database-url` remains the separate SQLite evaluation/scenario tool. Live mode rejects all POSTs and hides/rejects oracle and sandbox endpoints. Do not expose it publicly or use the evaluation server as a production operator console.

## Domain contracts

- `FinancialPlanService` combines posted ledger, awards, disbursements, payment states, requirements, catalog, actual enrollments and student planning assumptions. All money remains integer cents. `simulate` is a read-only preview.
- `WorkBoardProjection` joins canonical work items with document, case and payment evidence. Its project counts and linked-payment state counts cover the full queue; cards are paginated. Search, owner/priority/office filters, quick filters and sorting run before pagination; project attention/open/overdue totals cover the whole project. Operational status never settles money or proves verification/delivery.
- Planning input edits require student self-authorization, term validation, expected version, idempotency key/payload hash, an atomic audit record and a receipt. No Edward financial write was added.
- Board edits use the existing work/document/communication command boundaries and their authorization/version/domain guards. Original files and external delivery remain dependent on configured storage/providers.
- Official document reviews are immutable per submission. The shared history projection excludes internal notes; replacement uploads preserve earlier decisions. Imported status snapshots identify unknown reviewers/guidance and never supersede separately recorded university revision evidence.
- Saved outreach drafts are canonical staff-only product state with version/payload-bound replay. Saving creates no message or case outcome. Confirmed sending atomically links the reviewed draft version to its delivered communication. Atlas `/api/work-item?work_item_id=<UUID>` and Edward’s existing work-detail read see the same draft.
- Inline Action Center outreach sends to the canonical portal inbox after explicit confirmation. Start/send commands use durable payload-bound replay receipts; external activity recording does not imply delivery. Edward's relationship evidence and Atlas include the latest 50 portal inbox records with operational timestamps.
- Morning Brew is deliberately demo-backed and cannot enter Edward evidence. See the portal's `app/staff/morning-brew/MOCK-BOUNDARY.md`.

See [the actual tool catalog](edward-tool-catalog.md). In university mode legacy financial/housing interpretations are excluded from the model's read catalog.

## Verification

Full contributor gates are `npm run lint`, `npm run typecheck`, `npm test`; the portal additionally requires `npm run build`. PostgreSQL tests require their own isolated test databases, never the interactive integration database. `test_integration_reality.py` checks shared finance/board facts and planning command boundaries. University tests in `tools/university/tests` cover deterministic builds, scenarios, holdouts, mutation and action invariants.

From the sibling portal:

```bash
node tools/university-explorer/integration-parity.mjs
node tools/university-explorer/integration-board-filters.mjs
E2E_BASE_URL=http://localhost:3009 npx playwright test tools/browser-e2e/specs/integration-edward.spec.ts
# Explicit provider-enabled Lab smoke; incurs a model call:
node tools/university-explorer/integration-lab.mjs
```

The parity harness compares HTTP APIs to Atlas and checks the imported UI. The workspace test uses the integrated demo identities and deliberately avoids the legacy browser suite's fixture-reset hooks. Raw screenshots, database files and provider traces are ignored local artifacts, not committed test fixtures.

The document browser regression requires a **separate disposable imported test database**. Start `run_runtime.py --disable-openai --port 45629` and `server.py --port 4329` with that database's explicit URL. From this platform clone, create a fresh pending submission before each run:

```bash
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/prepare_document_review_fixture.py \
  --database-url postgresql://dhairya2801@127.0.0.1:55591/audentra_university_test_vnext_rebuild \
  --output ../portals/artifacts/integration/document-review-fixture.json
```

Then run `node tools/university-explorer/integration-document-review.mjs` from the portal clone. The browser routes all API calls to the test runtime and records a real decision there. This checks command persistence and cross-surface history, using explicit failed-extraction metadata; it does not certify original object storage or provider extraction. `test_document_history_integration.py` additionally tests replacement acceptance, replay/conflict, authorization and stale-submission protection in a rolled-back transaction.

With the same separate API/Atlas test services on 45629/4329, run `node tools/university-explorer/integration-outreach.mjs` from the portal clone. It chooses an open request in that disposable world, verifies draft persistence across reload and conflicting edits, records one confirmed portal message, deliberately loses the successful HTTP response, and proves the retry creates no duplicate. It checks the staff conversation, student inbox and Atlas. No external email is sent. `test_outreach_integration.py` adds authorization, version/hash, receipt and recorded-versus-delivered checks in a rolled-back transaction.

The restored legacy banks run in separate current-schema test databases. See [legacy evaluation results and commands](legacy-evaluation.md) for the read-only source captures, strict fixture import, passing write banks and remaining read limitations. No source database is used by an evaluation API.

This branch has not been pushed or deployed. See the workspace report for readiness limits and the proposed test.audentra.ai rollout sequence.
