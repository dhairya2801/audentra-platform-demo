"""The Action Center board as a bounded, queryable list.

An enrollment office's queue is thousands of items at peak; nobody reads it top
to bottom. The board therefore has to be *queried*: open work first, then a
page, then filters that match how staff actually triage (mine / unassigned /
overdue / stale / owned by someone who is not here). This module owns the
vocabulary of that query and the operational signals derived per item, so the
PostgreSQL repository, the in-memory store and Staff Edward's queue tool agree
on what "stale" or "owner at risk" means.

Framework-free on purpose: the rules are testable without a database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from audentra.core.errors import BadRequestError

OPEN_WORK_STATUSES: tuple[str, ...] = ("todo", "in_progress", "follow_up_required", "blocked")
CLOSED_WORK_STATUSES: tuple[str, ...] = ("done", "cancelled")
WORK_STATUSES: tuple[str, ...] = OPEN_WORK_STATUSES + CLOSED_WORK_STATUSES
WORK_PRIORITIES: tuple[str, ...] = ("urgent", "high", "medium", "low")
PRIORITY_RANK: dict[str, int] = {"urgent": 0, "high": 1, "medium": 2, "low": 3}

STATUS_SCOPES: tuple[str, ...] = ("open", "closed", "all")
DUE_WINDOWS: tuple[str, ...] = ("all", "overdue", "today", "seven_days", "no_due")
SORT_ORDERS: tuple[str, ...] = ("priority", "due", "updated", "created", "stale")
OWNER_RISKS: tuple[str, ...] = ("departed", "on_leave", "away")

# Work that was started and then not touched for this long is "stale": it is
# neither moving nor visible as a problem. Shared with the advising desk so a
# director's "falling behind" flag and the board's stale filter agree.
STALE_AFTER = timedelta(days=10)
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
MAX_OFFSET = 100_000
MAX_SEARCH_LENGTH = 120
# Absences that mean "this person is not working on their queue". Training
# blocks and blocked hours are part of a working day, not an absence.
AWAY_TIME_OFF_KINDS: tuple[str, ...] = ("leave", "vacation", "sick", "conference", "other")


@dataclass(frozen=True, slots=True)
class ActionCenterQuery:
    """A normalized board query. Every field has a safe default."""

    status: str = "open"  # a status scope or one concrete status
    priority: str | None = None
    component: str | None = None
    assignee: str | None = None  # "me" | "unassigned" | staff member id
    search: str | None = None
    due: str = "all"
    stale: bool | None = None
    owner_risk: bool | None = None
    escalated: bool | None = None
    sort: str = "priority"
    limit: int = DEFAULT_PAGE_LIMIT
    offset: int = 0

    @property
    def statuses(self) -> tuple[str, ...]:
        if self.status == "open":
            return OPEN_WORK_STATUSES
        if self.status == "closed":
            return CLOSED_WORK_STATUSES
        if self.status == "all":
            return WORK_STATUSES
        return (self.status,)

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "priority": self.priority,
            "component": self.component,
            "assignee": self.assignee,
            "search": self.search,
            "due": self.due,
            "stale": self.stale,
            "ownerRisk": self.owner_risk,
            "escalated": self.escalated,
            "sort": self.sort,
            "limit": self.limit,
            "offset": self.offset,
        }


DEFAULT_QUERY = ActionCenterQuery()


def _bool(value: object, name: str) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise BadRequestError("INVALID_ACTION_CENTER_QUERY", f"{name} must be true or false")


def _int(value: object, name: str, *, default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(str(value))
    except ValueError as error:
        raise BadRequestError(
            "INVALID_ACTION_CENTER_QUERY", f"{name} must be an integer"
        ) from error
    if number < low or number > high:
        raise BadRequestError(
            "INVALID_ACTION_CENTER_QUERY", f"{name} must be between {low} and {high}"
        )
    return number


def _choice(value: object, name: str, choices: Iterable[str], *, default: str) -> str:
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    allowed = tuple(choices)
    if text not in allowed:
        raise BadRequestError(
            "INVALID_ACTION_CENTER_QUERY", f"{name} must be one of {', '.join(allowed)}"
        )
    return text


def _text(value: object, name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise BadRequestError(
            "INVALID_ACTION_CENTER_QUERY", f"{name} must be at most {max_length} characters"
        )
    return text


def parse_action_center_query(params: Mapping[str, object] | None) -> ActionCenterQuery:
    """Normalize raw query parameters (strings from HTTP, or typed values)."""

    raw = dict(params or {})
    status = _choice(raw.get("status"), "status", STATUS_SCOPES + WORK_STATUSES, default="open")
    priority_value = _text(raw.get("priority"), "priority", max_length=16)
    priority = (
        _choice(priority_value, "priority", WORK_PRIORITIES, default="") if priority_value else None
    )
    assignee = _text(raw.get("assignee"), "assignee", max_length=64)
    if assignee is not None and assignee.lower() in {"me", "unassigned"}:
        assignee = assignee.lower()
    return ActionCenterQuery(
        status=status,
        priority=priority or None,
        component=_text(raw.get("component"), "component", max_length=120),
        assignee=assignee,
        search=_text(raw.get("search"), "search", max_length=MAX_SEARCH_LENGTH),
        due=_choice(raw.get("due"), "due", DUE_WINDOWS, default="all"),
        stale=_bool(raw.get("stale"), "stale"),
        owner_risk=_bool(raw.get("ownerRisk"), "ownerRisk"),
        escalated=_bool(raw.get("escalated"), "escalated"),
        sort=_choice(raw.get("sort"), "sort", SORT_ORDERS, default="priority"),
        limit=_int(
            raw.get("limit"), "limit", default=DEFAULT_PAGE_LIMIT, low=1, high=MAX_PAGE_LIMIT
        ),
        offset=_int(raw.get("offset"), "offset", default=0, low=0, high=MAX_OFFSET),
    )


# --- signals ---------------------------------------------------------------


def _parse(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def owner_risk_for(
    employment_status: object,
    *,
    away_until: object = None,
) -> str | None:
    """Why the owner of an item cannot be expected to work it right now."""

    status = str(employment_status or "active")
    if status == "departed":
        return "departed"
    if status == "on_leave":
        return "on_leave"
    if away_until is not None:
        return "away"
    return None


def due_window_for(due_at: object, now: datetime) -> str:
    due = _parse(due_at)
    if due is None:
        return "no_due"
    if due < now:
        return "overdue"
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if due < day_start + timedelta(days=1):
        return "today"
    if due < now + timedelta(days=7):
        return "seven_days"
    return "all"


def derive_signals(
    item: Mapping[str, Any],
    *,
    now: datetime,
    owner_risk: str | None,
) -> dict[str, Any]:
    """Operational signals for one item, from its own timestamps and its owner."""

    status = str(item.get("status") or "")
    is_open = status in OPEN_WORK_STATUSES
    due = _parse(item.get("dueAt"))
    updated = _parse(item.get("updatedAt"))
    created = _parse(item.get("createdAt"))
    overdue = bool(is_open and due is not None and due < now)
    stale = bool(status == "in_progress" and updated is not None and now - updated >= STALE_AFTER)
    return {
        "overdue": overdue,
        "overdueDays": (now - due).days if overdue and due is not None else None,
        "stale": stale,
        "staleDays": (now - updated).days if stale and updated is not None else None,
        "ageDays": max(0, (now - created).days) if created is not None else 0,
        "unassigned": bool(is_open and not item.get("assignee")),
        "ownerRisk": owner_risk if is_open and item.get("assignee") else None,
    }


# --- in-memory evaluation ---------------------------------------------------
#
# The in-memory store (tests, previews) evaluates the same query over JSON-shaped
# items. PostgreSQL does the equivalent in SQL; these functions define the
# reference semantics.


def _signals_of(item: Mapping[str, Any]) -> Mapping[str, Any]:
    signals = item.get("signals")
    return signals if isinstance(signals, Mapping) else {}


def _search_blob(item: Mapping[str, Any]) -> str:
    student = item.get("student") or {}
    assignee = item.get("assignee") or {}
    return " ".join(
        str(part or "")
        for part in (
            item.get("key"),
            item.get("title"),
            item.get("description"),
            student.get("name") if isinstance(student, Mapping) else "",
            student.get("preferredName") if isinstance(student, Mapping) else "",
            item.get("component"),
            assignee.get("name") if isinstance(assignee, Mapping) else "",
        )
    ).lower()


def matches_query(
    item: Mapping[str, Any],
    query: ActionCenterQuery,
    *,
    now: datetime,
    actor_id: str | None,
) -> bool:
    if str(item.get("status")) not in query.statuses:
        return False
    if query.priority and str(item.get("priority")) != query.priority:
        return False
    if query.component and query.component.lower() not in str(item.get("component") or "").lower():
        return False
    assignee = item.get("assignee") if isinstance(item.get("assignee"), Mapping) else None
    if query.assignee == "unassigned" and assignee:
        return False
    if query.assignee == "me" and (not assignee or str(assignee.get("id")) != str(actor_id)):
        return False
    if query.assignee not in (None, "me", "unassigned") and (
        not assignee or str(assignee.get("id")) != query.assignee
    ):
        return False
    if query.search and query.search.lower() not in _search_blob(item):
        return False
    if query.due != "all" and due_window_for(item.get("dueAt"), now) != query.due:
        return False
    signals = _signals_of(item)
    if query.stale is not None and bool(signals.get("stale")) != query.stale:
        return False
    if query.owner_risk is not None and bool(signals.get("ownerRisk")) != query.owner_risk:
        return False
    return not (query.escalated is not None and bool(item.get("escalated")) != query.escalated)


def _ts(value: object, *, default: float) -> float:
    parsed = _parse(value)
    return parsed.timestamp() if parsed is not None else default


def sort_key(item: Mapping[str, Any], sort: str) -> tuple[Any, ...]:
    """Deterministic ordering; closed work always sorts after open work."""

    far = float(10**12)
    closed = 1 if str(item.get("status")) in CLOSED_WORK_STATUSES else 0
    priority = PRIORITY_RANK.get(str(item.get("priority")), 4)
    due = _ts(item.get("dueAt"), default=far)
    updated = _ts(item.get("updatedAt"), default=0.0)
    created = _ts(item.get("createdAt"), default=0.0)
    identity = str(item.get("id") or "")
    if sort == "due":
        return (closed, due, priority, identity)
    if sort == "updated":
        return (closed, -updated, identity)
    if sort == "created":
        return (closed, created, identity)
    if sort == "stale":
        return (closed, updated, identity)
    return (closed, priority, due, -updated, identity)


@dataclass(slots=True)
class BoardCounts:
    todo: int = 0
    in_progress: int = 0
    follow_up_required: int = 0
    blocked: int = 0
    done: int = 0
    cancelled: int = 0
    urgent: int = 0
    escalated: int = 0
    open: int = 0
    overdue: int = 0
    stale: int = 0
    unassigned: int = 0
    owner_risk: int = 0

    def public(self) -> dict[str, int]:
        return {
            "todo": self.todo,
            "inProgress": self.in_progress,
            "followUpRequired": self.follow_up_required,
            "blocked": self.blocked,
            "done": self.done,
            "cancelled": self.cancelled,
            "urgent": self.urgent,
            "escalated": self.escalated,
            "open": self.open,
            "overdue": self.overdue,
            "stale": self.stale,
            "unassigned": self.unassigned,
            "ownerRisk": self.owner_risk,
        }


def count_board(items: Iterable[Mapping[str, Any]]) -> BoardCounts:
    """Board-wide counts. Status/priority/escalation follow the legacy contract
    (whole board); the operational counts describe open work only."""

    counts = BoardCounts()
    for item in items:
        status = str(item.get("status"))
        if status == "todo":
            counts.todo += 1
        elif status == "in_progress":
            counts.in_progress += 1
        elif status == "follow_up_required":
            counts.follow_up_required += 1
        elif status == "blocked":
            counts.blocked += 1
        elif status == "done":
            counts.done += 1
        elif status == "cancelled":
            counts.cancelled += 1
        if str(item.get("priority")) == "urgent" and status in OPEN_WORK_STATUSES:
            counts.urgent += 1
        if bool(item.get("escalated")) and status in OPEN_WORK_STATUSES:
            counts.escalated += 1
        if status not in OPEN_WORK_STATUSES:
            continue
        counts.open += 1
        signals = _signals_of(item)
        counts.overdue += int(bool(signals.get("overdue")))
        counts.stale += int(bool(signals.get("stale")))
        counts.unassigned += int(bool(signals.get("unassigned")))
        counts.owner_risk += int(bool(signals.get("ownerRisk")))
    return counts


@dataclass(slots=True)
class _Facet:
    open: int = 0
    overdue: int = 0
    unassigned: int = 0
    stale: int = 0
    owner_risk: int = 0
    urgent: int = 0
    member: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def facet_board(items: Iterable[Mapping[str, Any]], *, assignee_limit: int = 25) -> dict[str, Any]:
    """Per-component and per-owner rollups of open work, for filters and Brew."""

    components: dict[str, _Facet] = {}
    assignees: dict[str, _Facet] = {}
    for item in items:
        if str(item.get("status")) not in OPEN_WORK_STATUSES:
            continue
        signals = _signals_of(item)
        assignee = item.get("assignee") if isinstance(item.get("assignee"), Mapping) else None
        buckets = [components.setdefault(str(item.get("component") or ""), _Facet())]
        owner_key = str(assignee.get("id")) if assignee else "unassigned"
        owner = assignees.setdefault(owner_key, _Facet())
        if assignee and owner.member is None:
            owner.member = dict(assignee)
        buckets.append(owner)
        for bucket in buckets:
            bucket.open += 1
            bucket.overdue += int(bool(signals.get("overdue")))
            bucket.unassigned += int(bool(signals.get("unassigned")))
            bucket.stale += int(bool(signals.get("stale")))
            bucket.owner_risk += int(bool(signals.get("ownerRisk")))
            bucket.urgent += int(str(item.get("priority")) == "urgent")
    component_rows: list[dict[str, Any]] = [
        {
            "component": name,
            "open": facet.open,
            "overdue": facet.overdue,
            "unassigned": facet.unassigned,
            "stale": facet.stale,
            "ownerRisk": facet.owner_risk,
            "urgent": facet.urgent,
        }
        for name, facet in components.items()
    ]
    component_rows.sort(key=lambda row: (-int(row["open"]), str(row["component"])))
    assignee_rows: list[dict[str, Any]] = [
        {
            "staff": facet.member,
            "open": facet.open,
            "overdue": facet.overdue,
            "stale": facet.stale,
            "urgent": facet.urgent,
        }
        for key, facet in assignees.items()
    ]
    assignee_rows.sort(
        key=lambda row: (
            0 if row["staff"] is None else 1,
            -int(row["open"]),
            str((row["staff"] or {}).get("name") or ""),
        )
    )
    return {"components": component_rows, "assignees": assignee_rows[:assignee_limit]}


def evaluate_board(
    items: list[dict[str, Any]],
    query: ActionCenterQuery,
    *,
    now: datetime,
    actor_id: str | None,
) -> dict[str, Any]:
    """Reference evaluation of a query over already-signalled items."""

    matched = [item for item in items if matches_query(item, query, now=now, actor_id=actor_id)]
    matched.sort(key=lambda item: sort_key(item, query.sort))
    page = matched[query.offset : query.offset + query.limit]
    students = {
        str(item["student"]["id"])
        for item in matched
        if isinstance(item.get("student"), Mapping) and item["student"].get("id")
    }
    return {
        "items": page,
        "page": {
            "limit": query.limit,
            "offset": query.offset,
            "total": len(matched),
            "hasMore": query.offset + len(page) < len(matched),
            "distinctStudents": len(students),
        },
        "counts": count_board(items).public(),
        "facets": facet_board(items),
        "query": query.public(),
    }
