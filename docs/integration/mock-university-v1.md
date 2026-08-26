# integration/mock-university-v1 — running and manually testing the combined stack

The integration candidate combines, on top of `main` (the staff/advising
foundation): `feat/mock-university-ops` (bounded Action Center, Morning Brew
people & capacity, engagement coverage, model-usage ledger) and
`feat/staff-edward-v2` (staff identity, entity resolution, staff-aware
intents, the university benchmark). What was consolidated and why is in
`docs/changelog/2026-08-26-integration-mock-university-v1.md`; the portal
side is in the portals repository's changelog of the same name.

Worktrees (branch `integration/mock-university-v1` in each repository):

    ~/Dhairya/projects/worktrees/integration/Audentra-platform
    ~/Dhairya/projects/worktrees/integration/Audentra-portals
    ~/Dhairya/projects/worktrees/integration/Audentra-university-explorer

## Architecture after the merge

    Staff Edward reasoning / planning        (integrations/staff_assistant: identity, entities, classify, planner)
            ↓
    bounded domain tools                     (staff_assistant/tools.py: getStaffWorkQueue, searchWorkQueue,
                                              summarizeWorkQueue, searchStaff, getStaffProfile, getStaffTeam, …)
            ↓
    shared staff / Action Center query layer (domain/action_center.py ActionCenterQuery →
                                              PostgresStaffRepository.get_work_queue / summarize_work_queue /
                                              find_work_item_by_key; PostgresStaffOperationsRepository for the
                                              staff directory, inquiries and departments; PostgresAdvisingRepository
                                              for caseloads, calendars and the Brew's capacity snapshot)
            ↓
    PostgreSQL (migration 0049 indexes over the open subset)

The Staff Portal's task board and `GET /v1/staff/action-center` use exactly
the same `ActionCenterQuery`, so a count Edward gives and a filtered board
the staff member opens can never disagree.

## Starting the stack for manual testing

Prerequisites already on this machine: the compose infrastructure
(`docker compose -f infra/compose.yaml up` — Postgres :55432, MinIO, Keycloak,
Mailpit), the mock university deployed into tenant `aster-demo`
(`npm run audentra:deploy` in the Explorer), `OPENAI_API_KEY` exported.

1. Stop the compose `api`/`worker` containers (they run the image they were
   built from, not this branch, and a second worker would move the tenant's
   queue underneath you):

       cd ~/Dhairya/projects/worktrees/integration/Audentra-platform/infra
       docker compose stop api worker          # `docker compose start api worker` restores them

2. Backend — API on :4300 and the worker, from the integration worktree:

       cd ~/Dhairya/projects/worktrees/integration/Audentra-platform
       scripts/integration-stack.sh api        # terminal 1 — http://localhost:4300/health/ready
       scripts/integration-stack.sh worker     # terminal 2 (optional; scheduled rules, SLA sweeps, outbox)

3. Frontend — the portal on :3000 pointed at :4300 (`apps/web/.env.local` is
   already in place; it is a copy of `apps/web/.env.integration.example`):

       cd ~/Dhairya/projects/worktrees/integration/Audentra-portals
       npm run dev                             # http://localhost:3000

4. University Explorer on :4680 (its own read-only Postgres on :5439):

       cd ~/Dhairya/projects/worktrees/integration/Audentra-university-explorer
       npm run dev                             # http://localhost:4680

Student portal: http://localhost:3000/sign-in → "Log in as demo student"
(enter a SYN reference). Staff portal: http://localhost:3000/staff → "Log in
as synthetic staff" (search by name or SYN reference). Edward Lab:
http://localhost:3000/dev/staff-edward.

## Personas

| Persona | Reference | Why |
| --- | --- | --- |
| Leandro Hartigan, Director of Academic Advising | `SYN-STF-ADV-DIR` | manager: Morning Brew, team overview, Action Center at scale |
| Elena Larkspur, Academic Adviser (126 advisees / cap 110, no open slots) | `SYN-ADV-012` | overloaded adviser; four students share her full name |
| Junia Pemberwell, Senior Academic Adviser (on leave, 67 advisees) | `SYN-ADV-005` | on leave with a caseload |
| Quentin Zephyrine, Academic Adviser (departed, 79 advisees) | `SYN-ADV-008` | departed adviser still owning students |
| Vera Jessamy, Transfer Success Adviser | `SYN-ADV-003` | falling behind (stale in-progress items, unclosed appointments) |
| Ximena Calderwood, Senior Academic Adviser | `SYN-ADV-009` | spare capacity |
| Camila Okonkwo, Transcript Evaluator (Registrar, on vacation) | `SYN-STF-REG-EV2` | away with a backlog |
| Student Milo Ironwood (adviser Elena Larkspur) | `SYN-000023` | student adviser experience, booking against a full calendar |
| Student Ximena Vellacourt (adviser Junia Pemberwell, on leave) | `SYN-000039` | leave behaviour on the student side |
| Student Nadia Brightwater (adviser Quentin Zephyrine, departed) | `SYN-000026` | departed-adviser behaviour |
| Student Ivo Ravensworth (deposited, no adviser) | `SYN-001478` | unassigned work / deposited without an adviser |

## Suggested sequence

1. **Staff · Leandro Hartigan.** Morning Brew → *People & capacity* should name
   Quentin (critical), Junia, Elena (over cap, no slots), Ximena (spare),
   Vera (falling behind), Camila (away with backlog), an office backlog and
   deposited students without an adviser. Click a signal → the task board
   opens pre-filtered. Action Center: open work only, "Showing 100 of ~2,500",
   Load more, Sort, Signals → Stale / Owner unavailable, Task type, search.
   My Desk. Staff Edward: "How many students does Elena Larkspur advise?",
   "Which department has the most unassigned work?", "Which of Vera Jessamy's
   work items have been in progress for more than a week?", "When is Elena
   Larkspur's next open slot?", "What happened on AST-00102?", then "how many
   of those are overdue" as a follow-up.
2. **Staff · Elena Larkspur.** My Desk (caseload 126/110, today's appointments),
   "Show me my overdue items", "Who on my team is away?".
3. **Student · Milo Ironwood.** Adviser card shows Elena and her next open
   time; Appointments: slots derive from her availability, booking a taken
   or outside-hours slot is refused, cancel/reschedule works.
4. **Student · Ximena Vellacourt / Nadia Brightwater.** Adviser on leave / departed is
   stated honestly; booking routes accordingly.
5. **Explorer.** Compare any of the above with the Explorer's Staff Explorer
   and scenarios (the oracle); `npm run test:product` and `npm run test:deploy`
   with `AUDENTRA_API_URL=http://localhost:4300` re-run the parity checks.

## Automated checks used for this candidate

    # platform (apps/api): lint, types, unit suite
    uv run --locked ruff check src tests && uv run --locked mypy src && uv run --locked pytest -q
    # platform: Postgres-marked tests need a migrated + seeded database, plus the mock tenant for
    # tests/test_mock_university_regression.py
    AUDENTRA_TEST_DATABASE_URL=… TEST_DATABASE_URL=… AUDENTRA_ENV=test \
    AUDENTRA_MOCK_API_URL=http://127.0.0.1:4300 AUDENTRA_MOCK_DATABASE_URL=…/vv_enrollment uv run --locked pytest -q
    # Staff Edward university benchmark (snapshot → API :45710 → ground truth → 144 cases)
    tools/edward-eval/university/bench.sh <batch>            # dev suite
    tools/edward-eval/university/bench.sh <batch> -- --holdout
    # portals
    NEXT_PUBLIC_EDWARD_DEBUG_ENABLED=false npm test && npm run typecheck && npm run lint
    # explorer
    npm run typecheck && npm run lint && npm run test:staff && npm run test:deploy && npm run test:product
