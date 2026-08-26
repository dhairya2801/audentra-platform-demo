# 2026-08-26 — Action Center at university scale, Morning Brew with a people dimension

Driven by the synthetic university (Aster, tenant `aster-demo`: 2,576 students,
88 staff, ~2,500 open work items). `artifacts/mock-university-report.md` in the
Explorer records what it exposed; this is what changed in the product.

## Action Center is queried, never dumped

`GET /v1/staff/action-center` now takes a query and returns one page:

```
status=open|closed|all|<status>  priority=  component=  assignee=me|unassigned|<id>|<name part>
search=  due=all|overdue|today|seven_days|no_due  stale=true|false  ownerRisk=true|false
escalated=  sort=priority|due|updated|created|stale  limit=1..200 (default 50)  offset=
```

Response: `items` (the page, with history for those items only), `counts`
(whole board: the legacy eight plus `open/overdue/stale/unassigned/ownerRisk`),
`page {limit, offset, total, hasMore, distinctStudents}`, `facets`
(per component, per owner), `query` (the normalized query). Invalid queries are
`400 INVALID_ACTION_CENTER_QUERY`; an unbounded read is not possible.

Every item carries `signals`: `overdue/overdueDays`, `stale/staleDays`
(in progress and untouched 10+ days), `ageDays`, `unassigned`, and `ownerRisk`
= `departed | on_leave | away` (the owner's `employment_status`, or a current
`staff_time_off` of kind leave/vacation/sick/conference/other). The assignee
summary carries `employmentStatus`, `leaveUntil`, `awayUntil`, `awayKind`.

Ordering: closed work always after open work; default priority → due → updated.
Migration `0049` adds partial indexes over the open subset (priority expression,
due, assignee, component, in-progress age) and a per-item log index.

Single-item reads (`GET /v1/staff/work-items/{id}`, create reload, update
locks) no longer read the whole board. `GET /v1/staff/workspace` embeds only
the first page plus counts/facets; the personal action center reads
`assignee=me` separately.

Measured on the mock tenant: board 7.7 s / 4.9 MB → 0.1–0.2 s / 140 KB;
workspace 6.9 s / 5.8 MB → 0.5–0.9 s / 1.2 MB (the remainder is the roster).

## Morning Brew: where are we falling behind, why, and where is capacity

`staffCapacity` is a new section: a summary line, at most ten ranked signals,
and a per-office table. Signals are rules over counted rows
(`domain/staff_capacity.py`): `departed_with_caseload` (critical),
`on_leave_with_caseload`, `over_cap_no_slots`, `away_with_backlog`,
`component_backlog`, `students_without_adviser` (high), `over_cap`,
`falling_behind`, `unassigned_backlog` (medium), `spare_capacity` (positive).
Ranking is severity first, then one signal per kind before any kind repeats, so
five offices behind cannot hide the adviser who left. Each signal carries a
`boardQuery` that opens the Action Center pre-filtered.

Two new priorities (`ownership-at-risk`, `stale-work`), one new delta
(`work_items_escalated` from `staff_work_log`), and the most severe staff
signal becomes a synthesis bullet. The read comes from
`PostgresAdvisingRepository.staff_capacity_snapshot` (tenant-wide people, open
slots derived from the published availability, office rollups, students
without / with a departed / on-leave adviser).

On the mock tenant the briefing names Quentin Zephyrine (departed, 79
students), Junia Pemberwell (leave until 09-21, 67 advisees), Elena Larkspur
(126/110, no slots), Ximena Calderwood (spare capacity), Vera Jessamy (falling
behind), Camila Okonkwo (vacation, 59 overdue), ISS/Advising backlogs, 55
deposited students without an adviser — and never the control case.

## Engagement scan honesty

Inactivity is only asserted where the activity feed covers ≥5 % of the roster
(`domain/engagement.py`). Otherwise the scan records `activity_unknown` instead
of `inactive`, does not count it toward an intervention, and the Brew's
`engagementScan.activitySignal` is false with a coverage note. The mock tenant
(5 of 2,576 students with events) no longer reads as "everyone inactive".

## Staff Edward

- `getStaffWorkQueue` runs its filters server-side on the bounded query
  (page of 25), plus `assigneeName`, `stale`, `ownerRisk`, `sort`, `key`;
  it returns board counts and per-component / per-owner rollups.
- New `getStaffMember(name)`: status, leave/absence, caseload vs cap, open /
  overdue / stale work, unclosed appointments, open slots in 14 days.
- New request type `staff_workload` ("which staff have the most overdue work",
  "is X on leave", "how many students does X advise", "X's items in progress
  for more than a week"): reads the briefing's capacity signals, the queue
  by owner, and the named person — the name is never resolved against the
  student roster.
- `getInquiries` reports counts over the whole read (active, awaiting first
  reply, over 24 h, unassigned, oldest); "how many student requests are
  awaiting a first reply" routes to inquiries, not to a roster count.
- `getMorningBriefing` carries `staffCapacity`.
- Every interactive Edward turn that used a provider now writes an
  `agent_run` + `model_usage` row (`model_usage_ledger.py`), accepting both
  gateway token shapes.

## Tests

- `tests/test_action_center_board.py` — query parsing, signals, ordering,
  filters, paging, facets, in-memory parity.
- `tests/test_staff_capacity_brew.py` — every planted situation named, ranking
  breadth, thresholds, engagement coverage, usage normalization, routing.
- `tests/test_morning_brew.py` — capacity priorities, synthesis, coverage note.
- `tests/test_mock_university_regression.py` (`-m postgres`, needs
  `AUDENTRA_MOCK_API_URL` + `AUDENTRA_MOCK_DATABASE_URL`) — latency and payload
  budgets, count parity with the database, ordering, filters, owner risks,
  and the Brew's planted signals against the Explorer's ground truth.
