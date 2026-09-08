# Aster v3: the model university

A reproducible synthetic university, an isolated explorer, and the seed source
for the Audentra portal/Edward runtime. **PostgreSQL is the runtime authority.**
The SQLite file is reproducible seed input and an independent evaluation
sandbox; the portals and Edward never query it. The original v1 archive and
v2 baseline remain available for regression tests.

See [the complete runtime report](../../docs/synthetic-university-runtime-report.md)
for the source audit, data mappings, architecture, evaluation results and limits.

## Run the portals and Edward over v3

Use Node 22, the locked API Python environment, and PostgreSQL 15 or newer
(the local verification used 17.6). From `synthetic-university/platform`:

```bash
npm ci
uv sync --directory apps/api --locked --all-groups
apps/api/.venv/bin/python tools/university/build.py
# Create a NEW local DB first, using your local PostgreSQL user/port.
# The importer accepts only loopback hosts and audentra_university* database names.
export UNIVERSITY_DATABASE_URL=postgresql://YOUR_USER@127.0.0.1:5432/audentra_university_v3
createdb --maintenance-db=postgresql://YOUR_USER@127.0.0.1:5432/postgres audentra_university_v3
DATABASE_URL="$UNIVERSITY_DATABASE_URL" npm run db:migrate
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/import_runtime.py \
  --database-url "$UNIVERSITY_DATABASE_URL"
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/run_runtime.py \
  --database-url "$UNIVERSITY_DATABASE_URL"
```

The helper serves the local demo API on `127.0.0.1:45609`, selects the v3
student/staff personas, enables local Lab traces, and starts no worker. OpenAI
is enabled by default using the Luna model planner. Set `OPENAI_API_KEY` in the
environment before starting; a missing key stops startup with an actionable error.
Use `--disable-openai` only for intentional offline portal checks; Edward then
reports that its AI service is unavailable. Interactive usage is separate from
the evaluation runner's budget.

From `synthetic-university/portals`, install dependencies with `npm ci`.
Set these values in the ignored `apps/web/.env.local`, preserving other local
settings. The Cloudflare development worker reads this file; shell overrides
alone do not configure its server-side Lab proxy.

```dotenv
API_PROXY_ORIGIN=http://127.0.0.1:45609
EDWARD_LAB_API_ORIGIN=http://127.0.0.1:45609
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:45609
NEXT_PUBLIC_EDWARD_DEBUG_ENABLED=true
EDWARD_LAB_WORKER_TOKEN=local-development-document-worker-token
```

The worker-token value above is the project's public local-development default,
not a production credential. If your API overrides `DOCUMENT_WORKER_TOKEN`,
use its matching value only in the ignored local portal file. Then `npm run dev`.

Open `http://localhost:3000/dashboard`, `/classrooms`, `/financials`, `/staff`,
and `/dev/staff-edward`. The university panel is also in the staff student
inspector. `node tools/university-explorer/runtime-smoke.mjs` verifies these
pages against the local API without invoking a model.

Import once. Rerunning the importer preserves runtime writes and exits; it is
not a reset or a bidirectional synchronization job. Rebuild into a **new DB**
when changing seed data or policy files. `--resume-bootstrap` is only for a
failed initial import into that same disposable local DB. Do not run the old
`db:seed` command over an imported v3 runtime. Existing file-upload workflows
also need the project's configured object storage; this helper does not start S3.

The separate explorer below remains useful for inspecting source rows, corpus
versions and evaluator sandboxes. Its changes do not alter the portal runtime.

## Open the university

From the isolated `platform` worktree:

```bash
# Python 3.11+ with PyYAML; the existing API virtualenv has the dependency.
apps/api/.venv/bin/python tools/university/build.py
apps/api/.venv/bin/python tools/university/server.py
```

Open **http://127.0.0.1:4310**. Frontend assets live in the sibling
`portals/tools/university-explorer` directory. There is no frontend compilation,
provider credential, network font, API deployment, or database service to set up.
The server binds only to loopback. If that port is occupied, pass `--port 4311`.

The default generated files are under `platform/artifacts/university-v3/`:

- `university.sqlite`: canonical baseline for this evaluation world;
- `manifest.json`: fixed clock, seed, source hashes, counts, checks and DB hash;
- `oracle.json`: **evaluator-only** scenarios, expected reasoning and failure criteria;
- `runs/<sandbox-id>/university.sqlite`: independent action experiments.

These outputs are ignored by Git. Rebuilding validates a temporary database
before replacing the baseline; it does not overwrite experiment forks. To reset
an experiment, create a fresh sandbox. Keep the server stopped while rebuilding
if multiple requests need a consistent build boundary.

## Verify it

```bash
python3 tools/university/validate.py artifacts/university-v3/university.sqlite
python3 -m unittest discover -s tools/university/tests -v
# From the sibling portals worktree, with the explorer running:
node tools/university-explorer/smoke.mjs
```

Browser checks use the existing portal Playwright installation and its Chromium
browser. They write screenshots to `portals/artifacts/university-explorer/` and
exercise a fresh sandbox, never the baseline. `UNIVERSITY_URL` selects another
loopback port. Tests cover deterministic builds and different seeds, corrupted
foreign keys/occupancy/prerequisites, money conservation, audience filtering,
two-time evidence, policy versions, cohorts, and concurrent action retries.

## Export evaluation inputs

```bash
python3 tools/university/evaluate.py --split development --role student \
  --output artifacts/university-v3/student-development.jsonl
python3 tools/university/evaluate.py --split development --role staff \
  --output artifacts/university-v3/staff-development.jsonl
python3 tools/university/evaluate.py --cohort
```

The exporter intentionally excludes rubric and forbidden-claim fields. A
production-style harness should expose bound, permission-checked tools over
`engine.py`, not hand an agent unrestricted SQL or the operator HTTP surface.
Student identity and staff authorization must come from the harness's actor,
not model-supplied `student_id` or `role`. The loopback explorer is an operator
application; its role switches **are not authentication**. Do not expose it to
the public internet or reuse it for real student data.

The exporter alone is not an end-to-end evaluation. The actual PostgreSQL
Edward runner exercises `PostgresPlatformService.dispatch`, including planners,
tools, guards and traces:

```bash
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/evaluate_runtime.py \
  --database-url "$UNIVERSITY_DATABASE_URL" --role student \
  --cases clear-control,pending-is-not-paid,approved-rcl,late-arriving-fact
```

Use `--role staff` for staff scenarios and `--question` for a targeted variant.
The model receives the question and authenticated tools, never oracle answers.
The runner exclusively permits OpenAI GPT-5.6 Luna, reserves cost before each
request, serializes runners, and enforces a persistent $4.50 ceiling in ignored
`artifacts/university-runtime/evaluation/spend.json`. Do not remove that ledger
to continue a budgeted run. Raw responses and sanitized traces stay ignored.
See the runtime report for the completed experiments and spend.

## API and experiment protocol

| Endpoint | Purpose |
| --- | --- |
| `GET /api/evidence?student_id=…&role=student` | Current evidence; no rubric, internal cases, or internal events |
| `GET /api/timeline?student_id=…&known_at=…&effective_at=…` | Events bounded by both clocks; student visibility by default |
| `GET /api/policies?q=…&role=…&at=…&known_at=…&student_id=…` | Version window, publication time, audience and applicability verdict |
| `GET /api/cohort` | Saved pending-payment / active-hold cohort with denominator and clock |
| `POST /api/what-if/drop` | `{student_id, enrollment_id}`; consequences, citations, uncertainty, **no mutation** |
| `POST /api/sandboxes` | `{}`; copies the baseline and returns `sandbox_id` |
| `POST /api/actions/release-hold` | Confirmed, authorized, version-checked action on a sandbox |
| `GET /api/scenarios?rubric=true` | **Operator/evaluator only**; never register as an agent tool |

Use `Content-Type: application/json` for POST requests. Send
`X-University-Sandbox: <sandbox_id>` on reads and actions to select a fork.
Omitting it always selects the baseline and rejects actions.

A hold-release payload has exactly five fields:

```json
{
  "hold_id": "ready-release",
  "actor_id": "<active Student Accounts staff id from this world>",
  "expected_version": 1,
  "confirmed": true,
  "idempotency_key": "evaluation-run-001-release-001"
}
```

Success commits the hold version/release, event and receipt in one immediate
transaction. Retry the exact same payload/key to retrieve the same receipt.
Changing its payload rejects the retry; an incorrect actor, stale version,
missing confirmation or posted balance above $250 produces no mutation. This explorer
only executes financial-hold release in a sandbox. The actual Edward runtime
preserves its existing guarded profile, follow-up, work-item and email-prepare
actions; it does not expose this sandbox hold action.

## Change the world responsibly

- `migrations/0001_world.sql` defines the independent v3 schema. It is not a
  migration for Audentra's PostgreSQL runtime. Add a new numbered migration for
  subsequent schema versions; do not modify already deployed runtime migrations.
- `build.py` imports identities and institutional sources, normalizes records,
  generates academic histories, and derives SAP/financial dependencies.
- `policies/` contains the v3-only operating supplements and version pair.
  Original corpus documents remain under the tenant knowledge directory.
- `scenarios.py` changes concrete source records and separately emits rubrics.
  It must not expose its case labels or answers through `engine.evidence`.
- `validate.py` states executable promises. Add a negative/corruption test when
  adding a relationship Edward might infer. Generation failing validation must
  never replace a working baseline.
- `engine.py` is the small world adapter; `server.py` serves it and the explorer.
  Do not import the oracle into the adapter or silently route real actors here.

See [the audit and completion report](../../docs/synthetic-university-v3.md)
and [the institutional/data model](../../docs/synthetic-university-v3-model.md)
for exact scope, intentional exceptions, limitations and evaluation design.
