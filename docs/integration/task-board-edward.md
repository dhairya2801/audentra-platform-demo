# Staff Task Board and Edward

The existing floating staff assistant is available on both Task Board surfaces,
including over an open card. No card layout, stylesheet, parser, or mock workflow
was redesigned.

## Context and reads

The frame publishes its current project and open task key through the existing
same-origin bridge. The portal sends those navigation hints as `pageContext` on
staff assistant turns. Tenant and actor always come from the authenticated session.
The backend validates an open key against the actor's actual board membership;
client-supplied student/staff identities are not accepted.

`TaskBoardAssistant` shares `DemoTaskBoardProjection` with the displayed demo board.
Configured membership takes precedence over the larger staff work queue. For a
staff member without curated membership, the same projection reads their assigned
canonical work. No names, task keys, or demo answers are special-cased.

- `getTaskBoard` exposes matching counts, student counts, project facets, priorities,
  operational status, deadlines, next steps and paged task/student summaries.
  Whole-board totals and filtered totals are separate. Calendar windows use
  America/New_York, matching the existing board; this week is Monday–Sunday.
- `getTaskBoardTask` exposes current task metadata, associated student, linked
  document metadata/decisions, requirements, conversations, private activity and
  related tasks. Documents, messages and activity have bounded detail pages.
  Current review/requirement state accompanies historical-message reads, so an
  obsolete correction message cannot be mistaken for the latest review state.
- The existing model read loop plans these reads and composes answers. Each task
  binds a server-verified `student:<key>` handle for relevant student-record reads.
  Multiple calls to the same task tool retain separate results. Comparing different
  students clears the single-student follow-up referent.
- On Task Board pages, student-name resolution uses the board's students. Broader
  queue/briefing tools and workload counters are excluded from this surface's
  reasoning context; student and policy context remain available. A resolved student's
  cross-project task index supplies task types and document counts before planning,
  so financial-aid document tasks remain visible alongside enrollment documents.
  Student filters require student handles; staff handles cannot silently produce
  an empty board.

## Actions

Task updates still use `operations.work_item.update` and the existing Edward Action
Gateway, with canonical target resolution, authorization, immutable previews,
confirmation, optimistic version checks, audit activity and replay-safe receipts.
Priority, next step, due date, follow-up date and existing guarded operational edits
are supported. Task dates accept named days and explicit ISO dates and resolve to
5 PM in the board timezone. No new write store or confirmation architecture exists.

Explicit keys override navigation hints. The planner resolves explicit “this task/card” references to the validated open
key before reading, even when history discusses a different task. Otherwise,
ambiguous conversational references offer canonical candidates instead of choosing
one from a list. A reply to that clarification resumes the existing edit request.
Confirmation success invalidates/refetches the board. Canonical priority, due date
and next-step fields override cached mock values. Failed transport retries pin the
original message ID and page context.

Portal-message drafts use current task/student evidence and are labelled as drafts.
They do not send messages. Staff can review and use them with the existing connected
student-portal messaging flow.

## Validation

Use an isolated copy of the demo database for mutation tests, never the live demo.
The real-model runner requires an explicitly named local test database and verifies
that response traces are persisted in that database before testing business writes.
It writes diagnostic output to `/tmp`; do not commit provider responses.

```sh
AUDENTRA_CAMILA_TEST_DATABASE_URL=postgresql://USER@127.0.0.1:55591/audentra_university_test_camila_edward \
  apps/api/.venv/bin/pytest apps/api/tests/test_task_board_edward_postgres.py -q --no-cov

PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/check_task_board_edward.py \
  --database-url postgresql://USER@127.0.0.1:55591/audentra_university_test_camila_edward
```

The API runner defaults to loopback port 45629. For the portal browser journey,
serve/proxy the portal on localhost:3019 with `/v1` pointed at that isolated API;
then run in the portals repository:

```sh
CAMILA_CONNECTED_TESTS=1 PORTAL_BASE=http://localhost:3019 \
  node tools/university-explorer/camila-task-board-edward.mjs
```

The browser journey covers the launcher, open-card stacking, canonical request
context, real uploaded-file metadata, confirmation, card refresh and receipt restore.
Unit tests cover calendar boundaries, filtering/pagination, actor context validation,
reference ambiguity, date/next-step parsing and per-task student handles.

## Validation result — September 16, 2026

- Official backend gate: 1,445 tests passed, 163 optional integration tests skipped,
  no test failures. The command exits unsuccessfully because repository coverage
  is 62.31%, below the existing 67% threshold; that threshold was already failing
  before this integration.
- Backend lint/type checks, Node workspace tests/type checks, portal lint/type
  checks, portal tests and production build passed. Portal lint retains existing
  warnings.
- Isolated PostgreSQL checks verified Camila's 64-card, 10-student membership,
  canonical details, pagination and actor/tenant isolation.
- Real-model conversations exercised overview, urgency, calendar windows, task
  details, cross-task comparisons, ambiguity, natural clarification, open-card
  references, current versus historical decisions, and portal drafts. Confirmed
  priority/date/next-step writes were checked against canonical state; replay,
  cancellation, invalid confirmation and stale-version rejection were checked.
- The browser journey verified launcher placement, open-card context, immutable
  previews, confirmed priority persistence, iframe refresh and restored receipts.
  Final read-only API/browser checks used the existing local demo and left its
  task versions unchanged. Mutation tests used only the isolated database.

## Boundaries

The parser remains simulated. Edward can reason about stored file metadata and
recorded review decisions; it does not extract facts from PDF contents in this
integration. Mock workflow stages and sample financial values are not canonical
review/payment evidence. The displayed workflow may differ from operational work
status; Edward distinguishes those records rather than silently completing tasks.
Other staff profiles and institutional timezone configuration are not perfected by
this change. The implementation uses the current board's Eastern-time convention.
