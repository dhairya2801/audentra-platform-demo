# Shared demo starting state

Student uploads, staff reviews, replies, and points use the same canonical backend
on localhost and in the eventual hosted demo. They are not hostname-gated.
Switching between Ada and Camila never resets anything. The staff header shows
**Reset demo** only when the backend is explicitly configured for a disposable
demo. The confirmation restores the saved state for everyone and signs them out.
Normal localhost development leaves `DEMO_RESET_TEMPLATE` unset and retains state.
The Action Center iframe's **Reset view** clears only its browser simulation.

## Ada's four steps and rewards

After importing the university and connected Camila board, run once against the
chosen local source database:

```bash
apps/api/.venv/bin/python tools/university/seed_ada_next_steps.py \
  --database-url "$LOCAL_UNIVERSITY_DATABASE_URL"
```

This enables the existing Aster Points program (100 points per dollar), preserves
earned points and completed evidence, and gives Ada these pending steps:

| Step | Completion reward |
| --- | ---: |
| Register for orientation | 40 |
| Upload your latest transcript | 80 |
| Confirm your emergency contact | 30 |
| Share your first-semester goal | 25 |

An already pending updated-transcript request is reused. If that request was
completed, a new request is added and linked to Camila's ENR-184 review card;
prior accepted originals and decisions remain. The two form steps use the shared
requirement engine. The seed marks its completion and subsequent runs preserve
all demo actions. It rejects an unexpected initial task set rather than silently
rewriting it. Review the starting state before taking the baseline snapshot.

## Prepare a disposable hosted demo (not performed by the seed)

Use the existing PostgreSQL server, one API process, and no worker for this demo,
matching the local connected-board runtime. No extra service is needed. The
runtime database must be named `audentra_demo_<suffix>`; its frozen template must
be exactly `<database>_baseline`. The database role must own both databases,
connect to `postgres`, and have `CREATEDB`. This option is not supported for a
multi-replica deployment, background workers, or a production database.

1. Use `pg_dump -Fc` from the matching PostgreSQL major version to snapshot the
   reviewed, seeded source. Keep the dump outside Git.
2. Create a new `<database>_baseline` on the target server and restore the dump
   with `pg_restore --no-owner --no-privileges`. Do not overwrite an existing
   database. Preserve the baseline's referenced object-storage files and their
   keys in the demo bucket.
3. In that new baseline only, run
   `TRUNCATE auth_session, staff_auth_session, ferpa_delegate_session;` so no
   browser session survives reset. Disconnect all clients from the baseline.
4. From `postgres`, freeze it with
   `ALTER DATABASE <database>_baseline ALLOW_CONNECTIONS false;`, then create the
   active demo with `CREATE DATABASE <database> TEMPLATE <database>_baseline;`.
5. Point only the demo API at the new active database. Set:

```dotenv
AUDENTRA_ENV=preview
AUTH_MODE=demo
BROWSER_AUTH_REQUIRED=true
DEMO_STUDENT_ALLOWLIST=SYN-000061
DEMO_STAFF_ALLOWLIST=AU-55ff7e408818
DEMO_TENANT_ID=00000000-0000-7000-8000-000000000003
DEMO_RESET_TEMPLATE=audentra_demo_<suffix>_baseline
```

Keep the existing required runtime secrets and storage settings; set `WEB_ORIGIN`
to the exact portal origin. Do not configure a worker with this flag. Do not
capture a new baseline on startup or sign-in. If schema or starting data changes,
prepare a new matching baseline before enabling the updated demo.

## Reset behavior and recovery

`GET /v1/staff/demo/reset` reports availability to the signed-in demo staff actor.
`POST` requires that actor's tenant, an allowed browser Origin, and
`{"confirmation":"RESET DEMO"}`. Other demo actions are temporarily blocked while
reset runs; an already active write or foreign database client prevents reset.

The API clones the frozen baseline, closes its resources, swaps the disposable
database names, and reopens resources before removing the previous state. A
restart failure swaps the previous database back. Client disconnection does not
interrupt the restore. A database advisory lock prevents overlapping resets.
If a process/server crashes mid-reset, `_next` or `_previous` can remain; further
resets refuse until an operator checks them. Preserve `_previous` during recovery
and determine which database is healthy before removing either leftover.

Reset restores all database state, including points, uploads' metadata, reviews,
messages, and financial changes. It does not delete newly uploaded object bytes;
those become unreferenced and may be cleaned separately later. It cannot undo
external email/payment/provider side effects. Use the existing simulated/manual
demo flows. Originals referenced by the baseline must remain available.

## Verification

The reset integration tests create and remove randomly named disposable databases
on an explicitly selected local test server:

```bash
AUDENTRA_DEMO_RESET_TEST_ADMIN_URL=postgresql://USER@127.0.0.1:PORT/postgres \
  apps/api/.venv/bin/pytest apps/api/tests/test_demo_reset.py -q
```

For browser verification, prepare `audentra_demo_verify` and its frozen baseline
as above from the seeded local fixture; run its API on `localhost:45649` with
`AUDENTRA_ENV=test` and `WEB_ORIGIN=http://localhost:3009`. From `portals`:

```bash
DEMO_RESET_TEST_API=http://localhost:45649 \
  node tools/university-explorer/demo-reset-e2e.mjs
```

The browser serves the UI from port 3009 but redirects every API call to the
isolated reset API. It checks four cards, card and header points, one-time form
rewards, upload → Camila review → student completion, cancellation, explicit
reset, invalidated sessions, and restored tasks/points/documents. No working
localhost or hosted demo data is mutated by the browser test.

The VM uses `infra/preview-vm/demo-reset.override.yaml` together with the existing
`compose.yaml`. Set `DEMO_DATABASE_NAME` and `DEMO_STORAGE_BUCKET` in its protected
deployment environment. After restoring the matching baseline and originals,
stop the old worker and start only `postgres minio api caddy`. The generic
`deploy-platform.sh` is for the original seeded preview and must not be used for
this frozen-baseline profile, since it runs the generic seed and worker.
