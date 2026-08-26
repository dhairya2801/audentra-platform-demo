"""Compose the Morning Brew briefing from canonical reads.

The rule this module exists to enforce: **every sentence and every number in
the briefing is traceable to a row.** Prose is generated from counts that were
already read, never the other way round, so there is no path by which a
narrative can assert something the database does not hold.

Where a fact is not reconstructable the briefing says so in place, rather than
filling the slot. An empty section, a zero, and "not tracked" are three
different states and the payload keeps them distinct — a leader who cannot tell
"nothing happened" from "we do not measure that" cannot trust either.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.morning_brew import (
    BREW_COHORTS,
    BREW_COHORTS_BY_KEY,
    BREW_DELTAS,
    BREW_METRICS,
    BREW_PRIORITIES,
    BREW_WINDOW_HOURS,
    BrewCohort,
    BrewCounts,
    BrewPrioritySpec,
    plural,
    severity_for,
    window_label,
)
from audentra.domain.staff_capacity import compose_staff_capacity
from audentra.domain.student_cohort import DUE_SOON_HORIZON_DAYS

JsonDict = dict[str, Any]

DETAILED_ATTENTION_ITEMS = 4
"""How many attention cards get the extra per-cohort reads. Bounded on purpose:
a briefing that issues one query per rule would scale its cost with the rule
catalogue rather than with the institution."""

_WINDOWS: tuple[JsonDict, ...] = (
    {"id": "now", "label": "Now", "short": "NOW", "description": "Canonical state at read time"},
    {
        "id": "day",
        "label": "Last 24 hours",
        "short": "24H",
        "description": "Events with a canonical timestamp inside the window",
    },
)

_UNSUPPORTED: tuple[JsonDict, ...] = (
    {
        "metric": "Week, month, and cycle-to-date comparisons",
        "reason": (
            "The platform stores no end-of-period snapshot, so an earlier "
            "denominator cannot be reconstructed. Only current state and "
            "timestamped events inside a rolling window are supportable."
        ),
    },
    {
        "metric": "Yield, melt, and conversion forecasts",
        "reason": (
            "There is no validated predictive model in the platform. The "
            "preview workspace's melt and recovery percentages are explicitly "
            "non-canonical and are not read here."
        ),
    },
    {
        "metric": "Net tuition and revenue impact",
        "reason": (
            "Only enrollment-deposit amounts are on the canonical ledger. "
            "Tuition, aid disbursement, and net revenue are not modelled."
        ),
    },
    {
        "metric": "Email and calendar",
        "reason": (
            "The platform has no mailbox or calendar integration. Inbound "
            "student support conversations and canonical deadlines take those "
            "slots instead."
        ),
    },
    {
        "metric": "External higher-education news",
        "reason": "No news source is part of the product.",
    },
)


class MorningBrewReader(Protocol):
    """The canonical reads a briefing needs. Implemented by the repository."""

    async def count_cohorts(
        self, auth: AuthContext, cohorts: Sequence[BrewCohort]
    ) -> dict[str, int]: ...

    async def count_recent_activity(
        self, auth: AuthContext, *, hours: int = BREW_WINDOW_HOURS
    ) -> dict[str, JsonDict]: ...

    async def blocking_requirement_breakdown(
        self, auth: AuthContext, cohort: BrewCohort, *, limit: int = 8
    ) -> list[JsonDict]: ...

    async def deadline_runway(self, auth: AuthContext) -> list[JsonDict]: ...

    async def open_student_requests(self, auth: AuthContext) -> JsonDict: ...

    async def staff_work_summary(self, auth: AuthContext) -> JsonDict: ...

    async def cohort_sample(
        self, auth: AuthContext, cohort: BrewCohort, *, limit: int = 4
    ) -> list[JsonDict]: ...

    async def engagement_scan_freshness(self, auth: AuthContext) -> JsonDict: ...

    async def staff_capacity(self, auth: AuthContext) -> JsonDict | None: ...


async def build_morning_brew(
    auth: AuthContext,
    reader: MorningBrewReader,
    *,
    now: datetime | None = None,
) -> JsonDict:
    """Read the canonical facts, then compose the briefing from them."""

    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_ACCESS_REQUIRED", "Staff access is required")

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    counts_raw, activity, deadlines, requests, staff_work, scan = await asyncio.gather(
        reader.count_cohorts(auth, BREW_COHORTS),
        reader.count_recent_activity(auth, hours=BREW_WINDOW_HOURS),
        reader.deadline_runway(auth),
        reader.open_student_requests(auth),
        reader.staff_work_summary(auth),
        reader.engagement_scan_freshness(auth),
    )
    capacity_snapshot = await reader.staff_capacity(auth)
    counts = BrewCounts(values=dict(counts_raw))
    staff_capacity = compose_staff_capacity(capacity_snapshot, now=moment)

    ranked = _ranked_priorities(counts)
    detailed = ranked[:DETAILED_ATTENTION_ITEMS]
    samples, breakdowns = await _read_attention_detail(auth, reader, detailed)

    attention = [
        _attention_item(spec, counts, samples.get(spec.id, []), breakdowns.get(spec.id, []))
        for spec in detailed
    ]
    metrics = [_metric(spec, counts, activity) for spec in BREW_METRICS]
    changes = _changes(activity)
    priorities = _work_priorities(staff_work, requests, deadlines, counts, staff_capacity)

    return {
        "generatedAt": _iso(moment),
        "window": {
            "id": "day",
            "hours": BREW_WINDOW_HOURS,
            "label": window_label(),
            "since": _iso(moment - timedelta(hours=BREW_WINDOW_HOURS)),
            "basis": (
                "A rolling window ending at read time. The platform keeps no "
                "end-of-day snapshot, so a calendar-day comparison would need a "
                "denominator PostgreSQL does not retain."
            ),
        },
        "windows": [dict(window) for window in _WINDOWS],
        "population": {
            "students": counts.get("roster"),
            "cohorts": {cohort.key: counts.get(cohort.key) for cohort in BREW_COHORTS},
        },
        "synthesis": _synthesis(counts, activity, attention, requests, staff_work, staff_capacity),
        "metrics": metrics,
        "changes": changes,
        "attention": attention,
        "priorities": priorities,
        "deadlines": [_deadline(item) for item in deadlines],
        "requests": {
            "items": [_request(item) for item in requests.get("items", [])],
            "total": int(requests.get("total", 0)),
            "awaitingFirstReply": int(requests.get("awaitingFirstReply", 0)),
            "unassigned": int(requests.get("unassigned", 0)),
        },
        "staffWork": dict(staff_work),
        "staffCapacity": staff_capacity,
        "engagementScan": dict(scan),
        "coverage": {
            "source": "canonical_postgres",
            "notes": _coverage_notes(scan, counts),
            "unsupported": [dict(item) for item in _UNSUPPORTED],
        },
    }


# ---------------------------------------------------------------------------
# Reads that depend on the ranking
# ---------------------------------------------------------------------------


async def _read_attention_detail(
    auth: AuthContext,
    reader: MorningBrewReader,
    specs: Sequence[BrewPrioritySpec],
) -> tuple[dict[str, list[JsonDict]], dict[str, list[JsonDict]]]:
    if not specs:
        return {}, {}
    sample_tasks = [
        reader.cohort_sample(auth, BREW_COHORTS_BY_KEY[spec.cohort_key], limit=4) for spec in specs
    ]
    breakdown_specs = [spec for spec in specs if spec.group_by == "blocking_requirement"]
    breakdown_tasks = [
        reader.blocking_requirement_breakdown(auth, BREW_COHORTS_BY_KEY[spec.cohort_key], limit=6)
        for spec in breakdown_specs
    ]
    results = await asyncio.gather(*sample_tasks, *breakdown_tasks)
    samples = {spec.id: list(results[index]) for index, spec in enumerate(specs)}
    breakdowns = {
        spec.id: list(results[len(specs) + index]) for index, spec in enumerate(breakdown_specs)
    }
    return samples, breakdowns


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _ranked_priorities(counts: BrewCounts) -> list[BrewPrioritySpec]:
    """Rules with somebody in them, heaviest first.

    Ordering is the declared `weight`, then the size of the affected cohort.
    There is no score and no model: a reader who knows the rules can predict
    the order, which is what makes the ranking checkable.
    """

    populated = [spec for spec in BREW_PRIORITIES if counts.get(spec.cohort_key) > 0]
    populated.sort(key=lambda spec: (-spec.weight, -counts.get(spec.cohort_key), spec.id))
    return populated


def _metric(spec: Any, counts: BrewCounts, activity: Mapping[str, JsonDict]) -> JsonDict:
    cohort = BREW_COHORTS_BY_KEY[spec.cohort_key]
    value = counts.get(spec.cohort_key)
    basis_value = counts.get(spec.basis_key) if spec.basis_key else None
    share = counts.share(spec.cohort_key, spec.basis_key)
    delta = activity.get(spec.delta_key or "", {}) if spec.delta_key else {}
    delta_count = int(delta.get("count", 0)) if spec.delta_key else 0

    now_frame: JsonDict = {
        "windowId": "now",
        "value": value,
        "window": spec.window_label,
        "basisLabel": (
            f"{spec.basis_label} ({basis_value})"
            if spec.basis_label and basis_value is not None
            else None
        ),
        "basisValue": basis_value,
        "basisPercent": share,
        "note": spec.definition,
        "unavailable": False,
        "change": (_change_value(spec, delta_count) if spec.delta_key else None),
    }
    if spec.delta_key:
        day_frame: JsonDict = {
            "windowId": "day",
            "value": delta_count,
            "window": f"{spec.delta_label.capitalize()} in {window_label()}",
            "basisLabel": f"of {value} total" if value else None,
            "basisValue": value,
            "basisPercent": round(delta_count * 100 / value, 1) if value else None,
            "note": _delta_note(spec.delta_key),
            "unavailable": False,
            "change": None,
        }
    else:
        day_frame = {
            "windowId": "day",
            "value": value,
            "window": "No 24-hour change is reconstructable",
            "basisLabel": None,
            "basisValue": None,
            "basisPercent": None,
            "note": (
                "This is a state count. The platform records no transition "
                "timestamp for it, so the value shown is current state, not a "
                "change."
            ),
            "unavailable": True,
            "change": None,
        }

    return {
        "id": spec.id,
        "topic": spec.topic,
        "label": spec.label,
        "icon": spec.icon,
        "format": "int",
        "definition": spec.definition,
        "source": "canonical_postgres",
        "cohort": cohort.as_json(),
        "frames": [now_frame, day_frame],
        "segments": [
            {
                "key": key,
                "label": BREW_COHORTS_BY_KEY[key].label,
                "value": counts.get(key),
                "percent": counts.share(key, "roster"),
            }
            for key in spec.segment_keys
        ],
    }


def _change_value(spec: Any, delta_count: int) -> JsonDict:
    favorable = (
        delta_count == 0 or spec.favorable_direction == "none" or spec.favorable_direction == "up"
    )
    return {
        "value": delta_count,
        "label": f"+{delta_count}",
        "direction": "up" if delta_count > 0 else "flat",
        "favorable": favorable,
        "comparison": f"in {window_label()}",
        "basis": _delta_basis(spec.delta_key),
    }


def _delta_basis(key: str) -> str:
    for delta in BREW_DELTAS:
        if delta.key == key:
            return delta.basis
    return "unknown"


def _delta_note(key: str) -> str:
    for delta in BREW_DELTAS:
        if delta.key == key:
            return delta.basis_note
    return ""


def _changes(activity: Mapping[str, JsonDict]) -> list[JsonDict]:
    """Every counted event class, including the quiet ones.

    A zero is kept rather than dropped: "no deposits posted overnight" is a real
    answer to "what changed", and hiding it would make an empty rail look like a
    broken page.
    """

    items: list[JsonDict] = []
    for spec in BREW_DELTAS:
        entry = activity.get(spec.key, {})
        count = int(entry.get("count", 0))
        items.append(
            {
                "id": spec.key,
                "topic": spec.topic,
                "title": spec.title,
                "count": count,
                "metric": plural(count, spec.singular, spec.plural),
                "detail": (
                    f"{plural(count, spec.singular, spec.plural).capitalize()} in {window_label()}."
                    if count
                    else f"Nothing recorded in {window_label()}."
                ),
                "tone": spec.tone if count else "neutral",
                "occurredAt": entry.get("latestAt"),
                "destination": spec.destination,
                "basis": spec.basis,
                "basisNote": spec.basis_note,
                "exact": spec.exact,
            }
        )
    items.sort(key=lambda item: (-int(item["count"]), str(item["id"])))
    return items


def _attention_item(
    spec: BrewPrioritySpec,
    counts: BrewCounts,
    sample: Sequence[Mapping[str, Any]],
    breakdown: Sequence[Mapping[str, Any]],
) -> JsonDict:
    cohort = BREW_COHORTS_BY_KEY[spec.cohort_key]
    count = counts.get(spec.cohort_key)
    roster = counts.get("roster")
    drivers = [
        {
            "label": BREW_COHORTS_BY_KEY[key].label.capitalize(),
            "value": str(counts.get(key)),
            "note": _driver_note(key, counts),
        }
        for key in spec.breakdown_keys
    ]
    top = breakdown[0] if breakdown else None
    return {
        "id": spec.id,
        "topic": spec.topic,
        "label": spec.owner,
        "title": spec.title,
        "severity": severity_for(count, spec.severity),
        "summary": _cohort_sentence(count, cohort.label),
        "scope": (
            f"{count} of {roster} students on the roster"
            if roster
            else "No students are on the roster yet"
        ),
        "impactLabel": "Affected students",
        "impact": _impact_chips(spec, count, top),
        "recommendedAction": spec.action,
        "priorityLevel": spec.level,
        "destination": spec.destination,
        "cohort": cohort.as_json(),
        "detail": {
            "narrative": [spec.reason, spec.action],
            "drivers": drivers,
            "breakdown": [
                {
                    "code": str(item.get("code")),
                    "title": str(item.get("title")),
                    "students": int(item.get("students", 0)),
                    "requirements": int(item.get("requirements", 0)),
                    "overdue": int(item.get("overdue", 0)),
                }
                for item in breakdown
            ],
            "breakdownNote": (
                "Counts open blocking requirements. One student with several "
                "open blockers appears in several rows, so these do not sum to "
                "a headcount."
            )
            if breakdown
            else None,
            "actions": [
                {"title": step, "detail": "", "owner": spec.owner, "due": spec.window}
                for step in spec.steps
            ],
            "students": [
                {
                    "id": str(item.get("id")),
                    "name": str(item.get("name")),
                    "program": str(item.get("programName")),
                    "note": _student_note(item),
                }
                for item in sample
            ],
            "studentsNote": (
                f"Showing {len(sample)} of {count}. Ask Edward for the full list."
                if count > len(sample)
                else None
            ),
            "evidence": [f"Cohort: {clause}" for clause in cohort.to_filter().describe()]
            or ["Cohort: every student in this tenant"],
        },
    }


def _cohort_sentence(count: int, label: str) -> str:
    """ "7 deposited students with an overdue requirement." — the count and its
    definition in one breath, because a cohort size without its definition is
    not a fact a leader can act on."""

    phrase = label.replace("students", "student", 1) if count == 1 else label
    return f"{count} {phrase}."


def _impact_chips(
    spec: BrewPrioritySpec, count: int, top: Mapping[str, Any] | None
) -> list[JsonDict]:
    chips: list[JsonDict] = [
        {
            "label": plural(count, "student", "students"),
            "tone": "positive" if count == 0 else "negative",
        }
    ]
    if top is not None and int(top.get("students", 0)) > 0:
        chips.append(
            {
                "label": f"{top.get('title')}: {top.get('students')}",
                "tone": "neutral",
            }
        )
    if spec.window:
        chips.append({"label": spec.window, "tone": "neutral"})
    return chips


def _driver_note(key: str, counts: BrewCounts) -> str:
    share = counts.share(key, "roster")
    if share is None:
        return "No roster to compare against"
    return f"{share}% of the roster"


def _student_note(item: Mapping[str, Any]) -> str:
    blocking = int(item.get("openBlockingCount") or 0)
    days = item.get("nextDueDays")
    parts: list[str] = []
    if blocking:
        parts.append(plural(blocking, "open blocking requirement", "open blocking requirements"))
    if isinstance(days, int):
        if days < 0:
            parts.append(f"next due date passed {abs(days)}d ago")
        elif days == 0:
            parts.append("next due date is today")
        else:
            parts.append(f"next due date in {days}d")
    return " · ".join(parts) or "No open blocking requirement"


def _work_priorities(
    staff_work: Mapping[str, Any],
    requests: Mapping[str, Any],
    deadlines: Sequence[Mapping[str, Any]],
    counts: BrewCounts,
    staff_capacity: Mapping[str, Any] | None = None,
) -> list[JsonDict]:
    """The queues a staff member actually works, sized from canonical rows.

    Deliberately different from the attention cards: those describe what is
    wrong with the funnel, these describe what is sitting in an inbox.
    """

    # These count *rows* — one per requirement and bucket — so they describe how
    # many distinct obligations are outstanding, never how many people.
    active_requests = int(requests.get("total", 0))
    overdue_slots = sum(1 for item in deadlines if item.get("bucket") == "overdue")
    upcoming_slots = sum(1 for item in deadlines if item.get("bucket") in {"today", "this_week"})
    candidates: list[JsonDict] = [
        {
            "id": "urgent-work",
            "topic": "student_success",
            "title": "Urgent or escalated Action Center items",
            "count": int(staff_work.get("urgent", 0)) + int(staff_work.get("escalated", 0)),
            "level": "High",
            "icon": "⚑",
            "detail": (
                f"{staff_work.get('urgent', 0)} urgent and "
                f"{staff_work.get('escalated', 0)} escalated of "
                f"{staff_work.get('openItems', 0)} open items."
            ),
            "linkLabel": "Open the Action Center",
            "destination": "tasks",
            "window": "Today",
            "breakdown": [
                {"label": "Open items", "value": str(staff_work.get("openItems", 0))},
                {"label": "Urgent", "value": str(staff_work.get("urgent", 0))},
                {"label": "Escalated", "value": str(staff_work.get("escalated", 0))},
                {"label": "Past their due date", "value": str(staff_work.get("overdue", 0))},
                {"label": "Unassigned", "value": str(staff_work.get("unassigned", 0))},
            ],
            "steps": [
                "Assign the unassigned items before anything else.",
                "Work the escalated items ahead of the merely urgent ones.",
            ],
        },
        *_capacity_priorities(staff_capacity or {}),
        {
            "id": "unanswered-requests",
            "topic": "student_success",
            "title": "Student requests waiting on a first reply",
            "count": int(requests.get("awaitingFirstReply", 0)),
            "level": "High",
            "icon": "✦",
            "detail": (
                f"{requests.get('awaitingFirstReply', 0)} of "
                f"{plural(active_requests, 'active conversation', 'active conversations')}"
                " has had no staff reply yet."
            ),
            "linkLabel": "Open Messages",
            "destination": "messages",
            "window": "Today",
            "breakdown": [
                {"label": "Active conversations", "value": str(requests.get("total", 0))},
                {
                    "label": "Awaiting a first reply",
                    "value": str(requests.get("awaitingFirstReply", 0)),
                },
                {"label": "Unassigned", "value": str(requests.get("unassigned", 0))},
            ],
            "steps": [
                "Reply to the oldest waiting conversation first.",
                "Assign an owner to every conversation that needs follow-up.",
            ],
        },
        # The headline for a deadline queue is a *headcount*, not a sum of
        # per-requirement rows: one student with five overdue requirements is
        # one person to contact, and printing five beside "8 students are
        # overdue" elsewhere on the page would read as a contradiction.
        {
            "id": "overdue-deadlines",
            "topic": "student_success",
            "title": "Deadlines already past",
            "count": counts.get("overdue"),
            "level": "High",
            "icon": "!",
            "detail": (
                f"{plural(counts.get('overdue'), 'student is', 'students are')} past a "
                "requirement due date, across "
                f"{plural(overdue_slots, 'overdue requirement', 'overdue requirements')}."
            ),
            "linkLabel": "Review the students",
            "destination": "students",
            "window": "Today",
            "breakdown": [
                {
                    "label": "Students with an overdue requirement",
                    "value": str(counts.get("overdue")),
                },
                {
                    "label": "Deposited students overdue",
                    "value": str(counts.get("deposited_overdue")),
                },
                {"label": "Overdue requirements outstanding", "value": str(overdue_slots)},
            ],
            "steps": [
                "Confirm which overdue requirements are genuinely on the student.",
                "Escalate anything the university is holding.",
            ],
        },
        {
            "id": "deadline-runway",
            "topic": "student_success",
            "title": f"Deadlines inside {DUE_SOON_HORIZON_DAYS} days",
            "count": counts.get("due_soon"),
            "level": "Medium",
            "icon": "◷",
            "detail": (
                f"{plural(counts.get('due_soon'), 'student has', 'students have')} a "
                f"requirement falling due in the next {DUE_SOON_HORIZON_DAYS} days."
            ),
            "linkLabel": "Review the runway",
            "destination": "students",
            "window": f"Next {DUE_SOON_HORIZON_DAYS} days",
            "breakdown": [
                {
                    "label": "Students with a requirement due soon",
                    "value": str(counts.get("due_soon")),
                },
                {"label": "Requirements falling due", "value": str(upcoming_slots)},
            ],
            "steps": [
                "Send reminders before the date, not after it.",
                "Start with students who also owe a deposit.",
            ],
        },
        {
            "id": "documents-waiting",
            "topic": "registrar",
            "title": "Documents waiting on a decision",
            "count": counts.get("documents_in_review"),
            "level": "Medium",
            "icon": "▤",
            "detail": (
                f"{plural(counts.get('documents_in_review'), 'student has', 'students have')} "
                "a document the university has not decided yet."
            ),
            "linkLabel": "Open the review queue",
            "destination": "tasks",
            "window": "Today",
            "breakdown": [
                {
                    "label": "Students with a document in review",
                    "value": str(counts.get("documents_in_review")),
                },
            ],
            "steps": ["Decide the oldest submissions first."],
        },
    ]
    return [item for item in candidates if int(item["count"]) > 0]


def _capacity_priorities(staff_capacity: Mapping[str, Any]) -> list[JsonDict]:
    """Queues that exist because of *people*: absent owners and stalled work."""

    summary = staff_capacity.get("summary")
    if not isinstance(summary, Mapping):
        return []
    owned = int(summary.get("itemsOwnedByUnavailable", 0))
    stale = int(summary.get("staleItems", 0))
    return [
        {
            "id": "ownership-at-risk",
            "topic": "student_success",
            "title": "Open items owned by someone departed, on leave or away",
            "count": owned,
            "level": "High",
            "icon": "⚠",
            "detail": (
                f"{owned} open items are assigned to a person who cannot work them right now "
                f"({summary.get('departed', 0)} departed, {summary.get('onLeave', 0)} on leave, "
                f"{summary.get('awayNow', 0)} away today)."
            ),
            "linkLabel": "Show items with an unavailable owner",
            "destination": "tasks",
            "window": "Today",
            "breakdown": [
                {"label": "Owned by unavailable staff", "value": str(owned)},
                {
                    "label": "Students whose adviser left",
                    "value": str(summary.get("studentsWithDepartedAdviser", 0)),
                },
                {
                    "label": "Students whose adviser is on leave",
                    "value": str(summary.get("studentsWithAdviserOnLeave", 0)),
                },
            ],
            "steps": [
                "Reassign the departed owner's items first; they will never move otherwise.",
                "Name a cover for each person on leave or away this week.",
            ],
            "boardQuery": {"ownerRisk": True},
        },
        {
            "id": "stale-work",
            "topic": "student_success",
            "title": "Work started and then left untouched",
            "count": stale,
            "level": "Medium",
            "icon": "◷",
            "detail": (f"{stale} items have been in progress with no update for 10 or more days."),
            "linkLabel": "Show stale in-progress items",
            "destination": "tasks",
            "window": "Today",
            "breakdown": [
                {"label": "Stale in-progress items", "value": str(stale)},
                {"label": "People falling behind", "value": str(summary.get("fallingBehind", 0))},
            ],
            "steps": ["Ask each owner whether the item is blocked, done, or forgotten."],
            "boardQuery": {"stale": True},
        },
    ]


def _deadline(item: Mapping[str, Any]) -> JsonDict:
    bucket = str(item.get("bucket"))
    days = item.get("daysAway")
    if not isinstance(days, int):
        relative = "Date unavailable"
    elif days < 0:
        relative = f"{abs(days)}d overdue"
    elif days == 0:
        relative = "Due today"
    else:
        relative = f"in {days}d"
    students = int(item.get("students", 0))
    # One row can cover several per-student due dates for the same requirement,
    # so the sentence has to say whether the date shown is *the* date or the
    # earliest of a range.
    spread = bool(item.get("spread"))
    if bucket == "overdue":
        phrase = "past their due date" if spread else "past this date"
    else:
        phrase = "due across staggered dates" if spread else "due on this date"
    return {
        "id": f"{item.get('kind')}:{item.get('code')}:{bucket}",
        "kind": str(item.get("kind")),
        "code": str(item.get("code")),
        "title": str(item.get("title")),
        "dueAt": item.get("dueAt"),
        "latestDueAt": item.get("latestDueAt"),
        "bucket": bucket,
        "relativeLabel": f"from {relative}" if spread else relative,
        "students": students,
        "priority": "high"
        if bucket in {"overdue", "today"}
        else ("medium" if bucket == "this_week" else "low"),
        "detail": f"{plural(students, 'student', 'students')} {phrase}.",
        "destination": "students",
    }


def _request(item: Mapping[str, Any]) -> JsonDict:
    waiting = int(item.get("waitingHours", 0))
    return {
        "id": str(item.get("id")),
        "subject": str(item.get("subject")),
        "summary": str(item.get("summary")),
        "status": str(item.get("status")),
        "priority": str(item.get("priority")),
        "topicCode": str(item.get("topicCode")),
        "studentName": str(item.get("studentName")),
        "programName": str(item.get("programName")),
        "assigneeName": item.get("assigneeName"),
        "createdAt": item.get("createdAt"),
        "lastMessageAt": item.get("lastMessageAt"),
        "waitingHours": waiting,
        "waitingLabel": _waiting_label(waiting),
        "destination": "messages",
    }


def _waiting_label(hours: int) -> str:
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


# ---------------------------------------------------------------------------
# Deterministic synthesis
# ---------------------------------------------------------------------------


def _synthesis(
    counts: BrewCounts,
    activity: Mapping[str, JsonDict],
    attention: Sequence[Mapping[str, Any]],
    requests: Mapping[str, Any],
    staff_work: Mapping[str, Any],
    staff_capacity: Mapping[str, Any] | None = None,
) -> JsonDict:
    """A short executive read, assembled only from numbers already counted.

    No model runs here. Each sentence is a template over a value that appears
    elsewhere in the payload, so the prose and the tiles can never disagree.
    """

    roster = counts.get("roster")
    if roster == 0:
        return {
            "headline": "No students are on the roster for this tenant yet.",
            "bullets": [],
            "source": "deterministic",
            "basis": "Composed from the counted cohorts in this payload.",
        }

    moved = [(spec.key, int(activity.get(spec.key, {}).get("count", 0))) for spec in BREW_DELTAS]
    total_moved = sum(count for _key, count in moved)
    deposits = int(activity.get("deposits_posted", {}).get("count", 0))
    accepted_now = int(activity.get("offers_accepted", {}).get("count", 0))

    if total_moved == 0:
        headline = (
            f"Nothing was recorded in {window_label()}. "
            f"{counts.get('deposit_paid')} of {counts.get('offer_accepted')} accepted "
            "students have a posted deposit."
        )
    else:
        parts = []
        if deposits:
            parts.append(plural(deposits, "deposit posted", "deposits posted"))
        if accepted_now:
            parts.append(plural(accepted_now, "offer accepted", "offers accepted"))
        if not parts:
            parts.append(plural(total_moved, "canonical event", "canonical events"))
        headline = f"{' and '.join(parts)} in {window_label()}."

    bullets: list[str] = []
    top = attention[0] if attention else None
    if top is not None and str(top.get("severity")) != "positive":
        impact = top.get("impact") or []
        first = impact[0].get("label") if impact else ""
        bullets.append(f"{top.get('title')}: {first}. {top.get('recommendedAction')}")
    # The people dimension: the single most severe staff signal, if one fired.
    capacity_signals = (staff_capacity or {}).get("signals") or []
    lead = next(
        (
            signal
            for signal in capacity_signals
            if isinstance(signal, Mapping) and signal.get("severity") in {"critical", "high"}
        ),
        None,
    )
    if lead is not None:
        bullets.append(f"{lead.get('title')}: {lead.get('detail')}")
    ready = counts.get("enrollment_ready")
    deposited = counts.get("deposit_paid")
    if deposited:
        bullets.append(
            f"{ready} of {deposited} deposited students have nothing blocking them; "
            f"{deposited - ready} still do."
        )
    outstanding = counts.get("deposit_outstanding")
    if outstanding:
        bullets.append(
            f"{outstanding} students accepted an offer and have not paid the enrollment deposit."
        )
    waiting = int(requests.get("awaitingFirstReply", 0))
    if waiting:
        bullets.append(
            f"{plural(waiting, 'student request has', 'student requests have')} had no "
            "staff reply yet."
        )
    urgent = int(staff_work.get("urgent", 0)) + int(staff_work.get("escalated", 0))
    if urgent:
        bullets.append(
            f"{urgent} of {staff_work.get('openItems', 0)} open Action Center items are "
            "urgent or escalated."
        )

    return {
        "headline": headline,
        "bullets": bullets[:4],
        "source": "deterministic",
        "basis": "Composed from the counted cohorts and window events in this payload.",
    }


def _coverage_notes(scan: Mapping[str, Any], counts: BrewCounts) -> list[str]:
    notes = [
        "Every figure is a count of canonical PostgreSQL rows, read at one instant.",
        "Cohorts use the same definitions the staff assistant answers from, so any "
        "number here can be expanded into the students behind it.",
    ]
    if counts.get("roster") == 0:
        notes.append("This tenant has no student records, so every cohort is empty.")
    if not scan.get("available"):
        notes.append(
            "The scheduled engagement scan has not projected this tenant yet, so "
            "attention flags are unavailable rather than zero."
        )
    elif scan.get("activitySignal") is False:
        notes.append(
            "No portal activity events have been recorded in the last 30 days, so "
            "inactivity is unknown for every student and is not counted as a reason "
            "to flag anyone."
        )
    return notes


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
