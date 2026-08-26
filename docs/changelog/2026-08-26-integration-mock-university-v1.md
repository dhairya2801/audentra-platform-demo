# 2026-08-26 — integration/mock-university-v1: one Staff Edward, one Action Center query layer

Combines `feat/mock-university-ops` (bounded Action Center, Morning Brew
people & capacity, model-usage ledger) and `feat/staff-edward-v2` (staff
identity, entity resolution, staff-aware intents, the university benchmark)
on top of the staff/advising foundation in `main`. Both branches had
independently rebuilt the same three things — the queue read Edward uses,
staff-member lookups, and the routing of staff/queue questions — so the merge
is an architectural consolidation, not a textual one. Full notes:
[`docs/integration/mock-university-v1.md`](../integration/mock-university-v1.md).

## One queue vocabulary

`domain/action_center.py` (`ActionCenterQuery`) is the only way anything reads
the board: `GET /v1/staff/action-center`, the workspace's first page, the
Staff Portal task board, and Staff Edward's `getStaffWorkQueue`,
`searchWorkQueue` and `summarizeWorkQueue`. The vocabulary gained the filters
Staff Edward needs — `actionType`, `workType`, `studentId`, `inProgressDays`
("in progress for more than a week") — and a grouped summary
(`PostgresStaffRepository.summarize_work_queue`, `summarize_board` for the
in-memory reference) with the same buckets the assistant answers with
(assignee, component, status, priority, due window, action/work type,
student). Work-item keys resolve through `find_work_item_by_key` (one indexed
row) on both hosts. The Staff Edward v2 `_queue_where` / `search_work_queue` /
`summarize_work_queue` SQL in `staff_operations_repository.py` and its
`work_queue_search` primitive are gone; that repository now holds only the
staff directory, inquiry and department reads.

## One staff lookup and one router

Staff Edward v2's entity resolution (`entities.py`, `identity.py`,
`searchStaff`, `getStaffProfile`, `getStaffTeam`, …) is canonical. The ops
branch's `getStaffMember` tool, its `staff_capacity` tool-host primitive and
its regex `staff_workload` / `_INQUIRY_STATE` classifier branches were
removed; the same questions route through `staff_caseload`,
`staff_availability`, `staff_workload`, `team_overview`, `queue_aggregate`
and `inquiry_aggregate`. The advising repository's `staff_capacity_snapshot`
stays — it is the Morning Brew's read, and `getMorningBriefing` still carries
`staffCapacity`.

Kept from ops inside Edward: the bounded `getStaffWorkQueue` (server-side
`assigneeName`, `stale`, `ownerRisk`, `sort`, `key`; board counts and
per-component / per-owner rollups in the evidence), `getInquiries` counts,
and the Brew's capacity signals in the evidence bundle. Kept from v2: the
JSON tool-cache key, `work_item_by_key` referent resolution, and every
staff-aware tool, intent, composer and the benchmark harness.

## Two seams the benchmark exposed

- **"Due today" is the calendar day.** The ops board defined `due=today` as
  "due later today", so at 17:00 an item due at 09:00 was neither today nor
  in the week (only overdue). Ground truth, Staff Edward v2 and the staff
  member mean the date: `due=today` now matches `day_start ≤ due < day_end`
  in SQL and in the reference (`is_due_today`); the windows overlap on
  purpose and only the `due_window` grouping bucket is exclusive.
- **A default is not a filter.** `getStaffWorkQueue` echoed its default
  scope (`status: "open"`) as an active filter, which sent the composer down
  its "filtered queue" branch and dropped the board-wide unassigned count
  from "catch me up on the Action Center". It now reports only the filters
  the staff member gave.

## Also

- `STALE_WORK_AFTER` (advising) is now an alias of the board's `STALE_AFTER`;
  one `_escape_like`.
- `tools/edward-eval/university/bench.sh` is executable.
- `scripts/integration-stack.sh` runs the API (:4300) and worker from a
  worktree against the compose infrastructure and the `aster-demo` tenant.
