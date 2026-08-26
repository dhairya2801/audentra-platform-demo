"""The Action Center as a bounded, queryable board.

These pin the reference semantics every implementation shares (PostgreSQL,
in-memory store, Staff Edward's queue tool): what "open", "stale", "overdue"
and "owner at risk" mean, how a query is parsed, and that closed work never
outranks live work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from audentra.core.auth import AuthContext
from audentra.core.errors import BadRequestError
from audentra.domain.action_center import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ActionCenterQuery,
    count_board,
    derive_signals,
    evaluate_board,
    facet_board,
    owner_risk_for,
    parse_action_center_query,
    sort_key,
    summarize_board,
)
from audentra.infrastructure.memory.store import DEMO_IDS, InMemoryPlatformStore

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
ME = "00000000-0000-7000-8000-000000000901"
OTHER = "00000000-0000-7000-8000-000000000902"


def _item(
    key: str,
    *,
    status: str = "todo",
    priority: str = "high",
    due_days: int | None = None,
    updated_days: int = 0,
    assignee: dict[str, Any] | None = None,
    component: str = "Registrar",
    student: str = "Taylor Nguyen",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": f"id-{key}",
        "key": key,
        "title": f"Task {key}",
        "description": "Do the thing.",
        "status": status,
        "priority": priority,
        "component": component,
        "escalated": False,
        "dueAt": (NOW + timedelta(days=due_days)).isoformat() if due_days is not None else None,
        "createdAt": (NOW - timedelta(days=30)).isoformat(),
        "updatedAt": (NOW - timedelta(days=updated_days)).isoformat(),
        "assignee": assignee,
        "student": {"id": f"student-{student}", "name": student},
    }
    risk = owner_risk_for(
        (assignee or {}).get("employmentStatus"), away_until=(assignee or {}).get("awayUntil")
    )
    item["signals"] = derive_signals(item, now=NOW, owner_risk=risk)
    return item


def _board() -> list[dict[str, Any]]:
    me = {"id": ME, "name": "Marcus Lee", "employmentStatus": "active"}
    departed = {"id": OTHER, "name": "Quentin Zephyrine", "employmentStatus": "departed"}
    away = {"id": "away", "name": "Camila Okonkwo", "employmentStatus": "active", "awayUntil": "x"}
    return [
        # A closed urgent item from June must never outrank live work.
        _item("OLD-DONE", status="done", priority="urgent", due_days=-80),
        _item("URGENT-OVERDUE", priority="urgent", due_days=-3, assignee=me),
        _item("URGENT-LATER", priority="urgent", due_days=5),
        _item("HIGH-STALE", status="in_progress", updated_days=12, assignee=me),
        _item("HIGH-FRESH", status="in_progress", updated_days=2, assignee=departed),
        _item("MED-AWAY", priority="medium", due_days=-1, assignee=away, component="Financial Aid"),
        _item("LOW-NODUE", priority="low", student="Ines Calderwood"),
        _item("CANCELLED", status="cancelled", priority="urgent", due_days=-40),
    ]


# --- query parsing ---------------------------------------------------------


def test_default_query_is_open_work_priority_order_first_page() -> None:
    query = parse_action_center_query({})
    assert query.status == "open"
    assert query.statuses == ("todo", "in_progress", "follow_up_required", "blocked")
    assert query.sort == "priority"
    assert query.limit == DEFAULT_PAGE_LIMIT
    assert query.offset == 0


def test_query_parses_http_strings_and_clamps_nothing_silently() -> None:
    query = parse_action_center_query(
        {
            "status": "all",
            "assignee": "ME",
            "stale": "true",
            "ownerRisk": "false",
            "due": "overdue",
            "sort": "stale",
            "limit": "200",
            "offset": "150",
            "search": "  Zephyrine ",
        }
    )
    assert query.statuses[-2:] == ("done", "cancelled")
    assert query.assignee == "me"
    assert query.stale is True
    assert query.owner_risk is False
    assert query.search == "Zephyrine"
    assert query.limit == MAX_PAGE_LIMIT and query.offset == 150


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "5000"},
        {"limit": "0"},
        {"status": "everything"},
        {"due": "yesterday"},
        {"sort": "random"},
        {"stale": "maybe"},
        {"search": "x" * 121},
    ],
)
def test_invalid_queries_are_rejected_not_clamped(params: dict[str, str]) -> None:
    with pytest.raises(BadRequestError) as error:
        parse_action_center_query(params)
    assert error.value.code == "INVALID_ACTION_CENTER_QUERY"


# --- signals ---------------------------------------------------------------


def test_signals_derive_overdue_stale_unassigned_and_owner_risk() -> None:
    board = {item["key"]: item for item in _board()}
    assert board["URGENT-OVERDUE"]["signals"] == {
        "overdue": True,
        "overdueDays": 3,
        "stale": False,
        "staleDays": None,
        "ageDays": 30,
        "unassigned": False,
        "ownerRisk": None,
    }
    assert board["HIGH-STALE"]["signals"]["stale"] is True
    assert board["HIGH-STALE"]["signals"]["staleDays"] == 12
    assert board["HIGH-FRESH"]["signals"]["stale"] is False
    assert board["HIGH-FRESH"]["signals"]["ownerRisk"] == "departed"
    assert board["MED-AWAY"]["signals"]["ownerRisk"] == "away"
    assert board["LOW-NODUE"]["signals"]["unassigned"] is True
    # Closed work carries no operational signals, however old its due date.
    assert board["OLD-DONE"]["signals"]["overdue"] is False
    assert board["OLD-DONE"]["signals"]["unassigned"] is False


def test_owner_risk_precedence() -> None:
    assert owner_risk_for("departed", away_until="2026-09-01") == "departed"
    assert owner_risk_for("on_leave") == "on_leave"
    assert owner_risk_for("active", away_until="2026-09-01") == "away"
    assert owner_risk_for("active") is None
    assert owner_risk_for(None) is None


# --- ordering, filtering, paging --------------------------------------------


def test_open_work_outranks_closed_work_of_any_priority() -> None:
    ordered = sorted(_board(), key=lambda item: sort_key(item, "priority"))
    keys = [item["key"] for item in ordered]
    assert keys[:3] == ["URGENT-OVERDUE", "URGENT-LATER", "HIGH-FRESH"]
    assert keys[-2:] == ["OLD-DONE", "CANCELLED"]


def test_default_page_contains_only_open_work_with_board_wide_counts() -> None:
    result = evaluate_board(_board(), ActionCenterQuery(), now=NOW, actor_id=ME)
    assert [item["key"] for item in result["items"]] == [
        "URGENT-OVERDUE",
        "URGENT-LATER",
        "HIGH-FRESH",
        "HIGH-STALE",
        "MED-AWAY",
        "LOW-NODUE",
    ]
    assert result["page"] == {
        "limit": DEFAULT_PAGE_LIMIT,
        "offset": 0,
        "total": 6,
        "hasMore": False,
        "distinctStudents": 2,
    }
    assert result["counts"] == {
        "todo": 4,
        "inProgress": 2,
        "followUpRequired": 0,
        "blocked": 0,
        "done": 1,
        "cancelled": 1,
        "urgent": 2,
        "escalated": 0,
        "open": 6,
        "overdue": 2,
        "stale": 1,
        "unassigned": 2,
        "ownerRisk": 2,
    }


def test_pagination_is_stable_and_bounded() -> None:
    first = evaluate_board(_board(), ActionCenterQuery(limit=2), now=NOW, actor_id=ME)
    second = evaluate_board(_board(), ActionCenterQuery(limit=2, offset=2), now=NOW, actor_id=ME)
    assert [i["key"] for i in first["items"]] == ["URGENT-OVERDUE", "URGENT-LATER"]
    assert first["page"]["hasMore"] is True
    assert [i["key"] for i in second["items"]] == ["HIGH-FRESH", "HIGH-STALE"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (ActionCenterQuery(assignee="me"), ["URGENT-OVERDUE", "HIGH-STALE"]),
        (ActionCenterQuery(assignee="unassigned"), ["URGENT-LATER", "LOW-NODUE"]),
        (ActionCenterQuery(assignee=OTHER), ["HIGH-FRESH"]),
        (ActionCenterQuery(due="overdue"), ["URGENT-OVERDUE", "MED-AWAY"]),
        (ActionCenterQuery(due="no_due"), ["HIGH-FRESH", "HIGH-STALE", "LOW-NODUE"]),
        (ActionCenterQuery(stale=True), ["HIGH-STALE"]),
        (ActionCenterQuery(owner_risk=True), ["HIGH-FRESH", "MED-AWAY"]),
        (ActionCenterQuery(component="financial"), ["MED-AWAY"]),
        (ActionCenterQuery(search="ines"), ["LOW-NODUE"]),
        (ActionCenterQuery(search="zephyrine"), ["HIGH-FRESH"]),
        (ActionCenterQuery(priority="urgent"), ["URGENT-OVERDUE", "URGENT-LATER"]),
        (ActionCenterQuery(status="closed"), ["OLD-DONE", "CANCELLED"]),
        (ActionCenterQuery(status="in_progress", sort="stale"), ["HIGH-STALE", "HIGH-FRESH"]),
        (ActionCenterQuery(sort="due"), ["URGENT-OVERDUE", "MED-AWAY", "URGENT-LATER"]),
    ],
)
def test_filters_and_sorts(query: ActionCenterQuery, expected: list[str]) -> None:
    result = evaluate_board(_board(), query, now=NOW, actor_id=ME)
    keys = [item["key"] for item in result["items"]]
    assert keys[: len(expected)] == expected
    if query.sort == "priority" and query.status not in ("in_progress",):
        assert keys == expected


def test_due_today_is_the_calendar_day_even_when_the_hour_has_passed() -> None:
    """ "Due today" at 17:00 still includes the item that was due at 09:00; that
    item is overdue as well. The windows overlap on purpose; only the
    grouping bucket is exclusive."""

    board = _board()
    earlier_today = _item("TODAY-PAST", due_days=0)
    earlier_today["dueAt"] = NOW.replace(hour=9).isoformat()
    later_today = _item("TODAY-LATER", due_days=0)
    later_today["dueAt"] = NOW.replace(hour=17).isoformat()
    for item in (earlier_today, later_today):
        item["signals"] = derive_signals(item, now=NOW, owner_risk=None)
    board.extend([earlier_today, later_today])
    today = evaluate_board(board, ActionCenterQuery(due="today"), now=NOW, actor_id=ME)
    assert {item["key"] for item in today["items"]} == {"TODAY-PAST", "TODAY-LATER"}
    overdue = evaluate_board(board, ActionCenterQuery(due="overdue"), now=NOW, actor_id=ME)
    assert "TODAY-PAST" in {item["key"] for item in overdue["items"]}
    assert "TODAY-LATER" not in {item["key"] for item in overdue["items"]}
    summary = summarize_board(
        board, ActionCenterQuery(), group_by="due_window", limit=10, now=NOW, actor_id=ME
    )
    assert summary["dueToday"] == 2
    buckets = {row["value"]: row["count"] for row in summary["buckets"]}
    assert buckets["today"] == 1 and buckets["overdue"] == 3


def test_all_statuses_puts_closed_items_last() -> None:
    result = evaluate_board(_board(), ActionCenterQuery(status="all"), now=NOW, actor_id=ME)
    statuses = [item["status"] for item in result["items"]]
    first_closed = next(i for i, s in enumerate(statuses) if s in ("done", "cancelled"))
    assert all(s in ("done", "cancelled") for s in statuses[first_closed:])
    assert result["page"]["total"] == 8


def test_facets_describe_open_work_per_component_and_owner() -> None:
    facets = facet_board(_board())
    assert facets["components"][0] == {
        "component": "Registrar",
        "open": 5,
        "overdue": 1,
        "unassigned": 2,
        "stale": 1,
        "ownerRisk": 1,
        "urgent": 2,
    }
    owners = facets["assignees"]
    assert owners[0]["staff"] is None and owners[0]["open"] == 2  # unassigned first
    named = {row["staff"]["name"]: row for row in owners[1:]}
    assert named["Marcus Lee"]["open"] == 2 and named["Marcus Lee"]["stale"] == 1
    assert named["Camila Okonkwo"]["overdue"] == 1


def test_count_board_never_counts_closed_items_as_urgent_or_escalated() -> None:
    counts = count_board(_board())
    assert counts.urgent == 2  # the done + cancelled urgent items are excluded
    assert counts.open == 6


# --- in-memory store parity -------------------------------------------------


def _staff_auth() -> AuthContext:
    return AuthContext(
        tenant_id=DEMO_IDS["tenant_id"],
        actor_type="staff",
        actor_id=DEMO_IDS["staff_advisor_id"],
        student_id=DEMO_IDS["student_id"],
    )


def test_memory_store_serves_the_same_bounded_shape() -> None:
    store = InMemoryPlatformStore()
    board = store.get_action_center(_staff_auth(), parse_action_center_query({"limit": "1"}))
    assert set(board) >= {"items", "staff", "counts", "page", "facets", "query", "generatedAt"}
    assert len(board["items"]) == 1
    assert board["page"]["limit"] == 1 and board["page"]["total"] >= 2
    assert board["page"]["hasMore"] is True
    assert "signals" in board["items"][0]
    assert set(board["counts"]) == {
        "todo",
        "inProgress",
        "followUpRequired",
        "blocked",
        "done",
        "cancelled",
        "urgent",
        "escalated",
        "open",
        "overdue",
        "stale",
        "unassigned",
        "ownerRisk",
    }
    queue = store.staff_work_queue(_staff_auth(), parse_action_center_query({"assignee": "me"}))
    assert all(item["assignee"]["id"] == DEMO_IDS["staff_advisor_id"] for item in queue["items"])
