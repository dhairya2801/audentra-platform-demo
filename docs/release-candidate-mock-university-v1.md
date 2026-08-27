# Release candidate — mock-university v1 on `main`

`main` now carries the tested `integration/mock-university-v1` product (fast-forward,
see `docs/integration/mock-university-v1.md` for what it contains) plus two
release-facing adjustments:

1. **The Staff Portal's "My desk" UI is gone** (portals repository). The staff
   model behind it — identity, adviser assignments, caseloads, hierarchy,
   availability, time off, appointments, `/v1/staff/me|caseload|appointments`,
   Staff Edward's staff tools, Morning Brew people & capacity, Action Center
   ownership signals — is unchanged.
2. **Demo sign-in is allowlisted by configuration.** Development keeps the
   browse-anyone panels; a deployed demo names exactly the people it exposes and
   the API refuses everyone else.

## Demo personas

| Persona | Reference | Why |
| --- | --- | --- |
| Student **Ada Kettleby** | `SYN-000061` | Primary adviser Hana Dunmire; onboarding in progress at the deposit step; two upcoming appointments, five in history — adviser card, slot picker, cancel/reschedule and Edward all have real data. |
| **Hana Dunmire**, Senior Academic Adviser | `SYN-ADV-001` | A normal, active adviser: 90 advisees of a 160 cap, 14 open items, open slots in the next 14 days, reports to the director below. |
| **Leandro Hartigan**, Director of Academic Advising | `SYN-STF-ADV-DIR` | Ten direct reports covering every people-and-capacity situation (departed, on leave, over cap, spare capacity, falling behind); the Action-Center-at-scale chair. |
| **Amara Abernathy**, Vice President for Enrollment Management | `SYN-STF-VP` | Top of the hierarchy, eleven directors reporting in; Morning Brew and institution-wide Staff Edward questions. |

All four are existing people of the synthetic university (tenant `aster-demo`,
`00000000-0000-7000-8000-000000000003`); nothing was created for sign-in.

## How the allowlist works

Two environment variables, comma-separated institution references:

    DEMO_STUDENT_ALLOWLIST=SYN-000061
    DEMO_STAFF_ALLOWLIST=SYN-ADV-001,SYN-STF-ADV-DIR,SYN-STF-VP

* Both empty (the development default): every demo route keeps its broad
  behaviour — type any `SYN-*` student, browse all ninety staff.
* Both set: `audentra.core.demo_personas.DemoPersonaAllowlist` is enforced by
  the one demo-identity adapter (`PostgresDevelopmentAuth`) on every path that
  mints or resolves a demo identity — `demo_student_by_reference` (sign-in *and*
  the per-request `vv_demo_student` cookie, by reference or UUID), the tenant's
  default `demo_student` (which becomes the named student instead of "first
  row"), `list_demo_staff`, `demo_staff_by_reference`, and `resolve_staff` for
  demo-method sessions (a session minted before the restriction dies). The HTTP
  layer checks the same object again on the sign-in routes and the directory.
  Anyone else answers `403 DEMO_PERSONA_NOT_ALLOWED`; a person outside the
  tenant still answers `404` as before.
* One set without the other is a configuration error, and a deployed demo
  (`AUDENTRA_ENV=preview` with `AUTH_MODE=demo`) refuses to start without both
  (`infra/preview-vm/compose.yaml` and its `.env.example` carry the values above).
* `GET /v1/auth/demo/personas` tells the portal which shape it is in. Restricted:
  the student panel becomes "Continue as Ada Kettleby" and the staff panel lists
  the three chairs without search or filters. Open: the panels are unchanged.
  Every normal protection stays: production still 404s all demo routes, OIDC mode
  still disables them, sessions are still real revocable server-side sessions.

## Running the release candidate locally (original repositories)

Prerequisites already on this machine: `docker compose -f infra/compose.yaml up`
(Postgres :55432, MinIO, Keycloak, Mailpit), the mock university deployed into
`aster-demo`, `OPENAI_API_KEY` exported.

    # 0. the compose api/worker on :4000 run their built image, not this tree
    cd ~/Dhairya/projects/Audentra-platform/infra && docker compose stop api worker

    # 1. API on :4300 in the RELEASE shape (restricted personas)
    cd ~/Dhairya/projects/Audentra-platform
    DEMO_STUDENT_ALLOWLIST=SYN-000061 \
    DEMO_STAFF_ALLOWLIST=SYN-ADV-001,SYN-STF-ADV-DIR,SYN-STF-VP \
    scripts/integration-stack.sh api
    #    …or without the two variables for the open development shape

    # 2. worker (optional; scheduled rules, SLA sweeps, outbox)
    scripts/integration-stack.sh worker

    # 3. portal on :3000 → :4300
    cd ~/Dhairya/projects/Audentra-portals
    cp apps/web/.env.integration.example apps/web/.env.local   # once
    npm run dev

    # 4. University Explorer on :4680 (read-only Postgres :5439)
    cd ~/Dhairya/projects/Audentra-university-explorer && npm run dev

    # (re)seed the mock university into aster-demo only if the tenant is missing:
    cd ~/Dhairya/projects/Audentra-university-explorer && npm run audentra:deploy

## Checks

    # platform (apps/api)
    uv run --locked ruff check src tests && uv run --locked mypy src && uv run --locked pytest -q
    AUDENTRA_TEST_DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment_test \
    TEST_DATABASE_URL=$AUDENTRA_TEST_DATABASE_URL AUDENTRA_ENV=test uv run --locked pytest -q
    AUDENTRA_MOCK_API_URL=http://127.0.0.1:4300 \
    AUDENTRA_MOCK_DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment \
      uv run --locked pytest -q tests/test_mock_university_regression.py   # API in the OPEN shape
    tools/edward-eval/university/bench.sh <batch>
    # portals
    npm test && npm run typecheck && npm run lint
    # explorer (API in the OPEN shape: test:deploy opens several staff and students)
    npm run typecheck && npm run test:staff && AUDENTRA_API_URL=http://localhost:4300 npm run test:deploy \
      && AUDENTRA_API_URL=http://localhost:4300 npm run test:product
