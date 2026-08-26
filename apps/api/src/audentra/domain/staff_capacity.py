"""Staff and capacity signals for the Morning Brew.

The briefing answers "where are we falling behind, why, and where is capacity
constrained or available?" — questions about *people and offices*, which the
student cohorts cannot answer. This module turns the capacity snapshot (every
person's load, status, absence and open calendar; every component's queue)
into a short ranked list of signals. Each signal is a rule over counted rows:
no score, no model, and the thresholds are named here so a reader can predict
tomorrow's list from today's numbers.

Kept deliberately small: the briefing is a page, not a dashboard. The full
person-by-person view lives on the desk and in the Action Center.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from audentra.domain.action_center import STALE_AFTER

JsonDict = dict[str, Any]
Severity = Literal["critical", "high", "medium", "positive"]

MAX_SIGNALS = 10
MAX_COMPONENTS = 12

# Thresholds — the same ones the desk's team flags use, plus a few that only
# make sense tenant-wide.
FALLING_BEHIND_STALE = 3  # in-progress items untouched ≥ STALE_AFTER ...
FALLING_BEHIND_STALE_SHARE = 0.10  # ... and at least one in ten of their open items
FALLING_BEHIND_UNCLOSED = 5  # past appointments with no outcome
SPARE_CAPACITY_UTILIZATION = 0.5
SPARE_CAPACITY_SLOTS = 40
AWAY_BACKLOG_OVERDUE = 5  # overdue items owned by someone currently away
COMPONENT_BACKLOG_OVERDUE_SHARE = 0.4  # ≥ 40 % of an office's open work is overdue
COMPONENT_BACKLOG_MIN_OPEN = 20
UNASSIGNED_BACKLOG_MIN = 10

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "positive": 3}


@dataclass(frozen=True)
class Signal:
    id: str
    kind: str
    severity: Severity
    title: str
    detail: str
    count: int
    action: str
    staff: JsonDict | None = None
    component: str | None = None
    destination: str = "tasks"
    board_query: Mapping[str, Any] | None = None

    def public(self) -> JsonDict:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "count": self.count,
            "action": self.action,
            "staff": dict(self.staff) if self.staff else None,
            "component": self.component,
            "destination": self.destination,
            "boardQuery": dict(self.board_query) if self.board_query else None,
        }


def _brief(person: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(person.get("id")),
        "name": str(person.get("name")),
        "title": person.get("title"),
        "component": person.get("component"),
        "employmentStatus": person.get("employmentStatus"),
    }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: object) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _date(value: object) -> str:
    return str(value)[:10] if value else "an unknown date"


def person_signals(person: Mapping[str, Any], *, now: datetime) -> list[Signal]:
    """Every rule that fires for one person. Ranking happens later."""

    del now  # the snapshot already carries "as of read time" facts
    status = str(person.get("employmentStatus") or "active")
    name = str(person.get("name"))
    caseload = _mapping(person.get("caseload"))
    work = _mapping(person.get("work"))
    availability = (
        person.get("availability") if isinstance(person.get("availability"), Mapping) else None
    )
    advisees = _int(caseload.get("primaryAdvisees"))
    cap = _int(caseload.get("cap")) or None
    open_items = _int(work.get("open"))
    overdue = _int(work.get("overdue"))
    stale = _int(work.get("staleInProgress"))
    unclosed = _int(work.get("appointmentsAwaitingOutcome"))
    open_slots = _int(availability.get("openSlotsNext14Days")) if availability else None
    brief = _brief(person)
    pid = str(person.get("id"))
    signals: list[Signal] = []

    if status == "departed" and (advisees or open_items):
        signals.append(
            Signal(
                id=f"departed:{pid}",
                kind="departed_with_caseload",
                severity="critical",
                title=f"{name} has left and still owns work",
                detail=(
                    f"Departed {_date(person.get('endedAt'))}; "
                    f"{advisees} students still list them as primary adviser and "
                    f"{open_items} Action Center items are still assigned to them."
                ),
                count=advisees + open_items,
                action="Reassign the caseload and the open items to an active adviser.",
                staff=brief,
                component=str(person.get("component") or "") or None,
                board_query={"assignee": pid, "status": "open"} if open_items else None,
            )
        )
    if status == "on_leave" and (advisees or open_items):
        signals.append(
            Signal(
                id=f"on-leave:{pid}",
                kind="on_leave_with_caseload",
                severity="high",
                title=f"{name} is on leave with an uncovered caseload",
                detail=(
                    f"On leave until {_date(person.get('leaveUntil'))}; "
                    f"{advisees} advisees cannot book them and {open_items} open items "
                    f"({overdue} overdue) sit in their queue."
                ),
                count=advisees + open_items,
                action="Cover the advisees and redistribute the queue until they return.",
                staff=brief,
                component=str(person.get("component") or "") or None,
                board_query={"assignee": pid, "status": "open"} if open_items else None,
            )
        )
    if status == "active" and cap and advisees > cap:
        no_slots = open_slots == 0
        signals.append(
            Signal(
                id=f"over-cap:{pid}",
                kind="over_cap_no_slots" if no_slots else "over_cap",
                severity="high" if no_slots else "medium",
                title=(
                    f"{name} is over caseload cap with no open slots"
                    if no_slots
                    else f"{name} is over caseload cap"
                ),
                detail=(
                    f"{advisees} advisees against a cap of {cap}"
                    + (
                        "; nothing bookable in the next 14 days."
                        if no_slots
                        else f"; {open_slots} open slots in the next 14 days."
                    )
                ),
                count=advisees - cap,
                action=(
                    "Move students to an adviser with spare capacity before registration closes."
                    if no_slots
                    else "Rebalance the caseload toward advisers under cap."
                ),
                staff=brief,
                component=str(person.get("component") or "") or None,
            )
        )
    if status == "active" and person.get("awayUntil") and overdue >= AWAY_BACKLOG_OVERDUE:
        signals.append(
            Signal(
                id=f"away:{pid}",
                kind="away_with_backlog",
                severity="high",
                title=f"{name} is away while their queue ages",
                detail=(
                    f"{str(person.get('awayKind') or 'away').capitalize()} until "
                    f"{_date(person.get('awayUntil'))}; {overdue} of their {open_items} open "
                    "items are already past due."
                ),
                count=overdue,
                action="Reassign the overdue items or name a cover until they are back.",
                staff=brief,
                component=str(person.get("component") or "") or None,
                board_query={"assignee": pid, "due": "overdue"},
            )
        )
    stale_share = stale / open_items if open_items else 0.0
    if status == "active" and (
        (stale >= FALLING_BEHIND_STALE and stale_share >= FALLING_BEHIND_STALE_SHARE)
        or unclosed >= FALLING_BEHIND_UNCLOSED
    ):
        signals.append(
            Signal(
                id=f"falling-behind:{pid}",
                kind="falling_behind",
                severity="medium",
                title=f"{name} is falling behind",
                detail=(
                    f"{stale} items in progress and untouched for {STALE_AFTER.days}+ days; "
                    f"{unclosed} past appointments still without an outcome."
                ),
                count=stale + unclosed,
                action="Review the stale items with them and close out the appointments.",
                staff=brief,
                component=str(person.get("component") or "") or None,
                board_query={"assignee": pid, "stale": True} if stale else None,
            )
        )
    if (
        status == "active"
        and cap
        and open_slots is not None
        and advisees < SPARE_CAPACITY_UTILIZATION * cap
        and open_slots >= SPARE_CAPACITY_SLOTS
    ):
        signals.append(
            Signal(
                id=f"spare:{pid}",
                kind="spare_capacity",
                severity="positive",
                title=f"{name} has spare advising capacity",
                detail=(
                    f"{advisees} of {cap} caseload used and {open_slots} open slots "
                    "in the next 14 days."
                ),
                count=open_slots,
                action="First candidate to absorb an over-cap or uncovered caseload.",
                staff=brief,
                component=str(person.get("component") or "") or None,
                destination="students",
            )
        )
    return signals


def component_signals(components: Sequence[Mapping[str, Any]]) -> list[Signal]:
    signals: list[Signal] = []
    for row in components:
        name = str(row.get("component") or "")
        open_items = _int(row.get("open"))
        overdue = _int(row.get("overdue"))
        unassigned = _int(row.get("unassigned"))
        oldest = row.get("oldestOverdueDays")
        if (
            open_items >= COMPONENT_BACKLOG_MIN_OPEN
            and overdue / open_items >= COMPONENT_BACKLOG_OVERDUE_SHARE
        ):
            share = round(100 * overdue / open_items)
            signals.append(
                Signal(
                    id=f"backlog:{name}",
                    kind="component_backlog",
                    severity="high" if share >= 60 else "medium",
                    title=f"{name} is behind on {share}% of its open work",
                    detail=(
                        f"{overdue} of {open_items} open items are past due"
                        + (f", the oldest by {_int(oldest)} days" if oldest is not None else "")
                        + (
                            f"; {_int(row.get('ownerRisk'))} are owned by someone departed, "
                            "on leave or away."
                            if _int(row.get("ownerRisk"))
                            else "."
                        )
                    ),
                    count=overdue,
                    action="Triage the oldest overdue items and pull in cover for absent owners.",
                    component=name,
                    board_query={"component": name, "due": "overdue"},
                )
            )
        if unassigned >= UNASSIGNED_BACKLOG_MIN:
            signals.append(
                Signal(
                    id=f"unassigned:{name}",
                    kind="unassigned_backlog",
                    severity="medium",
                    title=f"{unassigned} {name} items have no owner",
                    detail=f"{unassigned} of {open_items} open items in {name} are unassigned.",
                    count=unassigned,
                    action="Assign them so the SLA clock has a name on it.",
                    component=name,
                    board_query={"component": name, "assignee": "unassigned"},
                )
            )
    return signals


def student_signals(students: Mapping[str, Any]) -> list[Signal]:
    signals: list[Signal] = []
    deposited = _int(students.get("depositedWithoutPrimaryAdviser"))
    accepted = _int(students.get("acceptedWithoutPrimaryAdviser"))
    if deposited:
        signals.append(
            Signal(
                id="students:no-adviser",
                kind="students_without_adviser",
                severity="high",
                title=f"{deposited} deposited students have no adviser",
                detail=(
                    f"{accepted} accepted students have no primary adviser; {deposited} of them "
                    "have paid the deposit and advising gates their registration."
                ),
                count=deposited,
                action="Assign advisers to deposited students first, then the rest.",
                destination="students",
            )
        )
    return signals


def rank_signals(signals: Sequence[Signal]) -> list[Signal]:
    """Severity first, then breadth: one signal per kind before a second of
    any kind, so five offices behind cannot hide the one adviser who left."""

    ordered = sorted(signals, key=lambda s: (_SEVERITY_RANK[s.severity], -s.count, s.id))
    by_kind: dict[str, list[Signal]] = {}
    for signal in ordered:
        by_kind.setdefault(signal.kind, []).append(signal)
    kinds = sorted(
        by_kind,
        key=lambda kind: (
            _SEVERITY_RANK[by_kind[kind][0].severity],
            -by_kind[kind][0].count,
            kind,
        ),
    )
    ranked: list[Signal] = []
    depth = 0
    while len(ranked) < len(ordered):
        for kind in kinds:
            if depth < len(by_kind[kind]):
                ranked.append(by_kind[kind][depth])
        depth += 1
    return ranked


def compose_staff_capacity(
    snapshot: Mapping[str, Any] | None,
    *,
    now: datetime,
) -> JsonDict:
    """The Morning Brew's staff section: a summary, ranked signals, offices."""

    if snapshot is None:
        return {
            "available": False,
            "summary": None,
            "signals": [],
            "components": [],
            "basis": (
                "This deployment has no staff advising repository, so caseload, "
                "leave and availability cannot be read."
            ),
        }
    people = [p for p in snapshot.get("people", []) if isinstance(p, Mapping)]
    components = [c for c in snapshot.get("components", []) if isinstance(c, Mapping)]
    students = _mapping(snapshot.get("students"))

    signals: list[Signal] = []
    for person in people:
        signals.extend(person_signals(person, now=now))
    signals.extend(component_signals(components))
    signals.extend(student_signals(students))
    ranked = rank_signals(signals)

    def status_is(value: str) -> int:
        return sum(1 for p in people if str(p.get("employmentStatus") or "active") == value)

    kinds = {s.kind for s in ranked}
    owned_by_unavailable = sum(_int(c.get("ownerRisk")) for c in components)
    summary = {
        "staff": len(people),
        "active": status_is("active"),
        "onLeave": status_is("on_leave"),
        "departed": status_is("departed"),
        "awayNow": sum(
            1
            for p in people
            if p.get("awayUntil") and str(p.get("employmentStatus") or "active") == "active"
        ),
        "overCap": sum(1 for s in ranked if s.kind in {"over_cap", "over_cap_no_slots"}),
        "fallingBehind": sum(1 for s in ranked if s.kind == "falling_behind"),
        "spareCapacity": sum(1 for s in ranked if s.kind == "spare_capacity"),
        "itemsOwnedByUnavailable": owned_by_unavailable,
        "staleItems": sum(_int(c.get("stale")) for c in components),
        "unassignedItems": sum(_int(c.get("unassigned")) for c in components),
        "acceptedWithoutAdviser": _int(students.get("acceptedWithoutPrimaryAdviser")),
        "depositedWithoutAdviser": _int(students.get("depositedWithoutPrimaryAdviser")),
        "studentsWithDepartedAdviser": _int(students.get("withDepartedAdviser")),
        "studentsWithAdviserOnLeave": _int(students.get("withAdviserOnLeave")),
        "signalKinds": sorted(kinds),
        "signalsByKind": {kind: sum(1 for s in ranked if s.kind == kind) for kind in sorted(kinds)},
        "signalsTotal": len(ranked),
    }
    return {
        "available": True,
        "summary": summary,
        "signals": [s.public() for s in ranked[:MAX_SIGNALS]],
        "signalsOmitted": max(0, len(ranked) - MAX_SIGNALS),
        "components": [
            {
                "component": str(c.get("component")),
                "open": _int(c.get("open")),
                "overdue": _int(c.get("overdue")),
                "unassigned": _int(c.get("unassigned")),
                "stale": _int(c.get("stale")),
                "urgent": _int(c.get("urgent")),
                "ownerRisk": _int(c.get("ownerRisk")),
                "oldestOverdueDays": c.get("oldestOverdueDays"),
            }
            for c in components[:MAX_COMPONENTS]
        ],
        "basis": (
            "Rules over counted rows: employment status and leave from staff_member, "
            f"absences from staff_time_off, stale = in progress and untouched {STALE_AFTER.days}+ "
            "days, open slots derived from the published availability pattern. "
            "Ranked by severity, then size."
        ),
    }
