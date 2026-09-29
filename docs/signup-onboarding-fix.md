# New-account onboarding

Branch: `fix/new-account-onboarding` in `audentra-onboarding-platform`.
Base: `be65e22f275e2225f8d2bd5b95269e0a16d54378` (committed backend integration tip).
The dirty sibling `platform` worktree was not incorporated or modified.

## Behavior

Development/preview credential signup copied the oldest seed offer's response deadline, creating already-expired offers. New signup now prefers active/recent catalog templates and grants the new offer at least 30 days from the database's current date, keeping a later configured date. This is inside the existing development-flow guard. No existing offer is extended and acceptance retains ownership, status and deadline validation.

In imported university tenants, new credential students exist in public tables before an imported dossier exists. The portal's profile, academics and financials now use a tenant/student membership check before selecting university projections. Otherwise they retain the canonical public-table reads. Imported students and delegates retain their existing behavior. No database schema, import dataset or assistant tool implementation changed. The follow-up below updates assistant routing for students absent from the import.

The sibling portal worktree also refreshes onboarding versions after FERPA writes, fixes Select's internal scrolling, and sends the completed onboarding action to My Enrollment. Requirements continue to come from the configured canonical journey; no demo count is copied.

## Isolated verification

Dependencies: `uv sync --directory apps/api --locked --all-groups` and `npm ci`.
Use an explicitly disposable migrated and compact-seeded PostgreSQL database. Never point these tests at a shared developer or preview database.

```sh
TEST_DATABASE_URL="$SIGNUP_TEST_DATABASE_URL" uv run --directory apps/api --locked pytest tests/test_postgres_signup_onboarding.py -q
npm run lint
npm run typecheck
npm test
# Run separately if the Python coverage gate prevents Node tests from starting:
mkdir -p artifacts
npm run test:node
```

The focused integration regression keeps all database changes in a transaction and rolls them back. It expires template offers, creates a new student without an imported dossier, accepts the new offer, replays acceptance idempotently, completes eight stored steps, checks unfinished requirements and verifies expired/another-student offer rejection.

Results: focused PostgreSQL test passed; lint and full typechecks passed; 218 Node tests passed. Standard Python suite: 1,454 passed, 167 optional integrations skipped. **`npm test` remains failing its existing coverage gate: 62.24% vs 67%.** No threshold change was made.

The real browser journey also passed with an imported-tenant marker and expired seed offers, including signing through local MinIO, final My Enrollment, and a new sign-in preserving progress. Its student has 2 of 9 enrollment requirements complete, 6 ready and 1 blocked. Deferred payment and unsubmitted documents remain open. Screenshots and detailed portal coverage are in the sibling portal's `docs/ui-refresh/onboarding.md` and ignored `artifacts/ui-refresh/signup/`.

## Initial compact test runtime (retained, not the interactive preview)

Disposable services used here:

- `audentra-signup-test-pg`: PostgreSQL 17, loopback port 5544, database `signup_test`.
- `audentra-signup-test-storage`: MinIO, loopback port 9100, bucket `signup-documents`.
- API: loopback port 4101. Portal: localhost port 3000.

To recreate services, provision disposable database/storage credentials, migrate with `DATABASE_URL` set, then seed the compact fixtures using `audentra-seed --all` before adding any test-only expired-offer scenario. Set `SIGNUP_TEST_DATABASE_URL`, `SIGNUP_TEST_STORAGE_ACCESS_KEY`, `SIGNUP_TEST_STORAGE_SECRET_KEY` and a test-only `SIGNUP_TEST_FERPA_SECRET` (at least 32 characters), then start:

```sh
AUDENTRA_ENV=test AUTH_MODE=demo BROWSER_AUTH_REQUIRED=true \
DATABASE_URL="$SIGNUP_TEST_DATABASE_URL" API_HOST=127.0.0.1 API_PORT=4101 \
WEB_ORIGIN=http://localhost:3000 \
OBJECT_STORAGE_ENDPOINT=http://127.0.0.1:9100 \
OBJECT_STORAGE_BUCKET=signup-documents OBJECT_STORAGE_FORCE_PATH_STYLE=true \
OBJECT_STORAGE_ACCESS_KEY="$SIGNUP_TEST_STORAGE_ACCESS_KEY" \
OBJECT_STORAGE_SECRET_KEY="$SIGNUP_TEST_STORAGE_SECRET_KEY" \
FERPA_DELEGATE_LINK_SECRET="$SIGNUP_TEST_FERPA_SECRET" \
uv run --directory apps/api --locked audentra-api
```

The compact-fixture frontend would use `API_PROXY_ORIGIN=http://127.0.0.1:4101`; the interactive preview now uses port 4102 as described below. Its writing browser check requires `AUDENTRA_SIGNUP_E2E=isolated` and must only target that disposable runtime. No merge, deployment, live-data repair or renewal of existing expired accounts has been performed.

## Restored named demos and new-account Edward

Current interactive API: `http://127.0.0.1:4102`, portal: `http://localhost:3000`.
Database: `audentra_university_signup_review`, in the disposable PostgreSQL container on port 5544. This is a full read-only dump/restore copy of the existing `audentra_university_vnext` demo dataset. The source and compact signup test databases were not reset or modified during this copy. The previous compact runtime hid named demos because it lacked the full imported population and restricted allowlists.

Start the existing restricted launcher from this worktree with the copied database's URL in `SIGNUP_REVIEW_DATABASE_URL`, an existing `OPENAI_API_KEY`, and the local object-storage/Ferpa settings described above:

```sh
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/run_demo_excellence.py \
  --database-url "$SIGNUP_REVIEW_DATABASE_URL" --port 4102 --portal-origin http://localhost:3000
```

This uses the existing Ada/Camila allowlists and model configuration. The portal's ignored `.env.local` now retains port 4102 and both demo sign-in flags. The original storage objects were subsequently copied into the isolated bucket without overwriting new test signatures. All 119 copied objects passed SHA-256 verification; the browser verified all 22 Task Board original files, PDF rendering, downloads, expanded view and retry.

New students already have durable public records. The request-scoped assistant host now checks `has_student` before enabling imported-university evidence. New or previously created non-imported accounts instead use the same canonical profile, onboarding, checklist, document, payment and academic reads as their portal. They also use the operational clock rather than the imported snapshot's fixed date. Existing imported students keep their full university tool catalog/clock; delegate access keeps its scope boundary. The hold-release boundary uses the same membership check before reading imported account facts. No synthetic academic/billing history or fabricated birth date is inserted to satisfy import-only fields.

Regression commands (both targets must be isolated local fixtures):

```sh
TEST_DATABASE_URL="$SIGNUP_TEST_DATABASE_URL" \
AUDENTRA_SIGNUP_UNIVERSITY_TEST_DATABASE_URL="$SIGNUP_REVIEW_DATABASE_URL" \
uv run --directory apps/api --locked pytest tests/test_postgres_signup_onboarding.py -q
```

Two tests pass: fresh signup + onboarding + actual Edward tool reads + separation between accounts, and imported Ada compatibility + delegate/tenant boundaries. Full lint and typechecks pass. Standard suite: 1,454 Python tests passed, 167 integrations skipped; coverage remains 62.2% against 67%, so the combined gate is still red. All 218 Node tests pass separately. The new imported-demo opt-in test was run explicitly after adding it.

Browser evidence: Ada and Camila sign-in passed; a fresh fictional account completed real onboarding and repeat sign-in in the full demo copy. With explicit user consent, three actual OpenAI responses matched that fictional student's name/email, remaining steps and housing/emergency-contact answers. Ada/Camila records were not sent to the provider. No raw provider responses are exported; boolean/provider metadata and screenshots live in the sibling portal's ignored `artifacts/ui-refresh/signup-edward/`.

The frontend Edward interface, shared styling, tool definitions and prompts remain unchanged. Changes remain in the two review worktrees; no merge or deployment occurred.

## September 29 authorized deployment

Runtime commit `fbbceeb7e0e4064be0834fb3450e1d541d871d51` is deployed and healthy.
The public site passed fictional Morgan Test signup, all onboarding steps,
signing, pay-later, portal entry with actual requirements, and repeat sign-in.
The saved state contains two completed, five ready and two blocked requirements.
With the user's explicit consent, three real OpenAI answers correctly used only
that fictional account's saved identity, pending steps and housing/contact data.
Ada and Camila's UI sign-ins and all 22 original Task Board document reads pass.
Only the API service was recreated; existing database and storage were preserved.
The sibling portal's local `docs/ui-refresh/deployment.md` retains the detailed
release evidence and rollback procedure. Publishing additional infrastructure
details was rejected by automatic approval review; those notes remain local.
