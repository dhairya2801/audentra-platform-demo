# 2026-08-27 — release candidate: demo persona allowlist

`main` ← `integration/mock-university-v1` (fast-forward), then:

* `audentra.core.demo_personas.DemoPersonaAllowlist` — `DEMO_STUDENT_ALLOWLIST` /
  `DEMO_STAFF_ALLOWLIST` (institution references). Empty keeps development
  open; set, the demo-identity adapter refuses anyone else with
  `403 DEMO_PERSONA_NOT_ALLOWED` on sign-in by reference, the default demo
  student, the per-request student cookie, the staff directory, staff sign-in and
  the resolution of demo staff sessions. Required for `AUDENTRA_ENV=preview`
  with `AUTH_MODE=demo`; preview-vm compose and `.env.example` set it.
* `GET /v1/auth/demo/personas` — `{restricted, students, staff}` for the portal's
  sign-in panels; `GET /v1/auth/demo/staff/directory` gains `restricted`.
* Personas: `SYN-000061` Ada Kettleby; `SYN-ADV-001` Hana Dunmire;
  `SYN-STF-ADV-DIR` Leandro Hartigan; `SYN-STF-VP` Amara Abernathy.
* Lint: two unused locals in `domain/action_center.py` removed (ruff F841 on the
  integrated branch).

Tests: `tests/test_demo_personas.py`, restricted-deployment cases in
`tests/test_auth_http.py`, `test_a_deployed_demo_must_name_its_personas` in
`tests/test_runtime_settings.py`, and the Postgres adapter case
`test_a_restricted_adapter_opens_only_the_named_people` in
`tests/test_postgres_synthetic_university.py`.

Full write-up: `docs/release-candidate-mock-university-v1.md`.
