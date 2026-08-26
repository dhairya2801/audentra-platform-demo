"""Regression fixture: the synthetic university deployed into a real tenant.

Runs only when a deployed mock university is reachable:

    AUDENTRA_MOCK_API_URL=http://localhost:4000 \\
    AUDENTRA_MOCK_DATABASE_URL=postgresql://vv:vv_local_password@localhost:55432/vv_enrollment \\
    AUDENTRA_MOCK_TENANT_ID=00000000-0000-7000-8000-000000000003 \\
    AUDENTRA_MOCK_STAFF_REF=SYN-STF-ADV-DIR \\
    uv run pytest -m postgres tests/test_mock_university_regression.py

(`AUDENTRA_EXPLORER_DATABASE_URL`, default localhost:5439, adds the Explorer's
ground truth: the planted staff situations.) Every assertion here is a
behaviour the university exposed as missing: unbounded board reads, closed
work outranking live work, invisible stale work and absent owners, and a
briefing with no people in it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.postgres

API = os.getenv("AUDENTRA_MOCK_API_URL")
DATABASE_URL = os.getenv("AUDENTRA_MOCK_DATABASE_URL")
TENANT_ID = os.getenv("AUDENTRA_MOCK_TENANT_ID", "00000000-0000-7000-8000-000000000003")
STAFF_REF = os.getenv("AUDENTRA_MOCK_STAFF_REF", "SYN-STF-ADV-DIR")
EXPLORER_URL = os.getenv(
    "AUDENTRA_EXPLORER_DATABASE_URL",
    "postgresql://explorer:explorer_local_password@localhost:5439/aster_university",
)

# Budgets. The board used to take ~8 s and ~5 MB for 2.7k items; a page must
# stay cheap however large the board grows.
BOARD_LATENCY_BUDGET_S = 1.5
BOARD_PAYLOAD_BUDGET_BYTES = 400_000
BREW_LATENCY_BUDGET_S = 3.0
WORKSPACE_PAYLOAD_BUDGET_BYTES = 2_500_000

OPEN = ("todo", "in_progress", "follow_up_required", "blocked")


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _skip_unless_deployed() -> None:
    if not API or not DATABASE_URL:
        pytest.skip("Set AUDENTRA_MOCK_API_URL and AUDENTRA_MOCK_DATABASE_URL")


class Session:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=str(API), timeout=30.0)
        response = self.client.post(
            "/v1/auth/demo/staff/sign-in-as",
            json={"staffRef": STAFF_REF},
            headers={"x-demo-tenant-id": TENANT_ID},
        )
        if response.status_code != 200:
            pytest.skip(f"demo staff sign-in unavailable: {response.status_code}")
        self.staff_id = str(response.json()["staff"]["id"])

    def get(self, path: str) -> tuple[dict[str, Any], float, int]:
        started = time.perf_counter()
        response = self.client.get(path, headers={"x-demo-tenant-id": TENANT_ID})
        elapsed = time.perf_counter() - started
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text[:200]}"
        return response.json(), elapsed, len(response.content)


def _scalar(sql: str, url: str = "", **params: Any) -> Any:
    async def run() -> Any:
        engine = create_async_engine(_async_url(url or str(DATABASE_URL)))
        try:
            async with engine.connect() as connection:
                result = await connection.execute(text(sql), params)
                return result.scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _rows(sql: str, url: str = "", **params: Any) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        engine = create_async_engine(_async_url(url or str(DATABASE_URL)))
        try:
            async with engine.connect() as connection:
                result = await connection.execute(text(sql), params)
                return [dict(row) for row in result.mappings().all()]
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture(scope="module")
def session() -> Session:
    _skip_unless_deployed()
    return Session()


@pytest.fixture(scope="module")
def truth() -> dict[str, int]:
    """Counts straight from the product database, read once."""

    _skip_unless_deployed()
    row = _rows(
        """
        SELECT
          COUNT(*) FILTER (WHERE status IN ('todo','in_progress','follow_up_required','blocked'))
            AS open,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE status IN ('todo','in_progress','follow_up_required','blocked')
                             AND assignee_id IS NULL) AS unassigned,
          COUNT(*) FILTER (WHERE status IN ('todo','in_progress','follow_up_required','blocked')
                             AND priority = 'urgent') AS urgent,
          COUNT(*) FILTER (WHERE status IN ('todo','in_progress','follow_up_required','blocked')
                             AND due_at < NOW()) AS overdue,
          COUNT(*) FILTER (WHERE status = 'in_progress'
                             AND updated_at < NOW() - INTERVAL '10 days') AS stale,
          COUNT(*) FILTER (WHERE status = 'done') AS done
        FROM staff_work_item WHERE tenant_id = :tenant
        """,
        tenant=TENANT_ID,
    )[0]
    return {key: int(value) for key, value in row.items()}


# --- Action Center ----------------------------------------------------------


def test_default_board_page_is_bounded_fast_and_open_only(
    session: Session, truth: dict[str, int]
) -> None:
    board, elapsed, size = session.get("/v1/staff/action-center")
    assert elapsed < BOARD_LATENCY_BUDGET_S, f"{elapsed:.2f}s"
    assert size < BOARD_PAYLOAD_BUDGET_BYTES, f"{size} bytes"
    assert len(board["items"]) == 50
    assert board["page"]["total"] == truth["open"]
    assert board["page"]["hasMore"] is True
    assert all(item["status"] in OPEN for item in board["items"])
    assert board["counts"]["open"] == truth["open"]
    assert board["counts"]["unassigned"] == truth["unassigned"]
    assert board["counts"]["urgent"] == truth["urgent"]
    assert board["counts"]["overdue"] == truth["overdue"]
    assert board["counts"]["stale"] == truth["stale"]
    assert board["counts"]["done"] == truth["done"]


def test_board_orders_live_urgent_work_first_and_closed_last(session: Session) -> None:
    board, _, _ = session.get("/v1/staff/action-center?limit=200")
    priorities = [item["priority"] for item in board["items"]]
    assert priorities[0] == "urgent"
    rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    assert [rank[p] for p in priorities] == sorted(rank[p] for p in priorities)
    everything, _, _ = session.get("/v1/staff/action-center?status=all&limit=200&offset=2600")
    tail = [item["status"] for item in everything["items"]]
    assert everything["page"]["total"] > everything["counts"]["open"]
    if tail:
        first_closed = next((i for i, s in enumerate(tail) if s in ("done", "cancelled")), None)
        if first_closed is not None:
            assert all(s in ("done", "cancelled") for s in tail[first_closed:])


def test_pagination_walks_the_board_without_duplicates(
    session: Session, truth: dict[str, int]
) -> None:
    seen: list[str] = []
    offset = 0
    while True:
        page, elapsed, _ = session.get(f"/v1/staff/action-center?limit=200&offset={offset}")
        assert elapsed < BOARD_LATENCY_BUDGET_S
        seen.extend(item["id"] for item in page["items"])
        if not page["page"]["hasMore"]:
            break
        offset += 200
        assert offset < 20_000
    assert len(seen) == truth["open"]
    assert len(set(seen)) == len(seen)


@pytest.mark.parametrize(
    ("query", "truth_key"),
    [
        ("assignee=unassigned", "unassigned"),
        ("due=overdue", "overdue"),
        ("stale=true", "stale"),
        ("priority=urgent", "urgent"),
    ],
)
def test_filters_count_exactly_what_the_database_counts(
    session: Session, truth: dict[str, int], query: str, truth_key: str
) -> None:
    board, _, _ = session.get(f"/v1/staff/action-center?{query}&limit=1")
    assert board["page"]["total"] == truth[truth_key]


def test_owner_risk_filter_names_departed_on_leave_and_away_owners(session: Session) -> None:
    board, _, _ = session.get("/v1/staff/action-center?ownerRisk=true&limit=200")
    assert board["page"]["total"] > 0
    risks = {item["signals"]["ownerRisk"] for item in board["items"]}
    assert "departed" in risks
    assert risks <= {"departed", "on_leave", "away"}
    for item in board["items"]:
        assignee = item["assignee"]
        assert assignee is not None
        if item["signals"]["ownerRisk"] == "departed":
            assert assignee["employmentStatus"] == "departed"
        if item["signals"]["ownerRisk"] == "on_leave":
            assert assignee["leaveUntil"]
        if item["signals"]["ownerRisk"] == "away":
            assert assignee["awayUntil"]
    expected = _scalar(
        """
        SELECT COUNT(*) FROM staff_work_item w
        JOIN staff_member m ON m.id = w.assignee_id AND m.tenant_id = w.tenant_id
        WHERE w.tenant_id = :tenant
          AND w.status IN ('todo','in_progress','follow_up_required','blocked')
          AND (m.employment_status IN ('departed','on_leave')
               OR EXISTS (SELECT 1 FROM staff_time_off o
                           WHERE o.tenant_id = w.tenant_id AND o.staff_member_id = m.id
                             AND o.kind IN ('leave','vacation','sick','conference','other')
                             AND o.starts_at <= NOW() AND o.ends_at > NOW()))
        """,
        tenant=TENANT_ID,
    )
    assert board["page"]["total"] == int(expected)


def test_search_and_owner_name_filters(session: Session) -> None:
    by_name, _, _ = session.get("/v1/staff/action-center?assignee=Zephyrine")
    assert by_name["page"]["total"] > 0
    assert all(item["assignee"]["name"] == "Quentin Zephyrine" for item in by_name["items"])
    stale_by_name, _, _ = session.get("/v1/staff/action-center?assignee=Vera%20Jessamy&stale=true")
    assert all(item["signals"]["stale"] for item in stale_by_name["items"])
    key = by_name["items"][0]["key"]
    by_key, _, _ = session.get(f"/v1/staff/action-center?search={key}&status=all")
    assert [item["key"] for item in by_key["items"]] == [key]


def test_mine_filter_matches_the_signed_in_staff_member(session: Session) -> None:
    board, _, _ = session.get("/v1/staff/action-center?assignee=me&limit=200")
    assert all(item["assignee"]["id"] == session.staff_id for item in board["items"])
    expected = _scalar(
        """
        SELECT COUNT(*) FROM staff_work_item WHERE tenant_id = :tenant AND assignee_id = :me
          AND status IN ('todo','in_progress','follow_up_required','blocked')
        """,
        tenant=TENANT_ID,
        me=session.staff_id,
    )
    assert board["page"]["total"] == int(expected)


def test_invalid_query_is_a_400_not_a_full_board(session: Session) -> None:
    response = session.client.get(
        "/v1/staff/action-center?limit=5000", headers={"x-demo-tenant-id": TENANT_ID}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ACTION_CENTER_QUERY"


def test_facets_add_up_to_the_board(session: Session, truth: dict[str, int]) -> None:
    board, _, _ = session.get("/v1/staff/action-center?limit=1")
    components = board["facets"]["components"]
    assert sum(row["open"] for row in components) == truth["open"]
    assert sum(row["unassigned"] for row in components) == truth["unassigned"]
    assert sum(row["stale"] for row in components) == truth["stale"]
    owners = board["facets"]["assignees"]
    assert owners[0]["staff"] is None and owners[0]["open"] == truth["unassigned"]


def test_workspace_no_longer_embeds_the_whole_board(session: Session) -> None:
    workspace, _, size = session.get("/v1/staff/workspace")
    assert size < WORKSPACE_PAYLOAD_BUDGET_BYTES, f"{size} bytes"
    assert len(workspace["actionCenter"]["items"]) <= 50
    assert workspace["actionCenter"]["page"]["total"] > 50


def test_detail_read_is_single_item_not_whole_board(session: Session) -> None:
    board, _, _ = session.get("/v1/staff/action-center?limit=1")
    item_id = board["items"][0]["id"]
    detail, elapsed, size = session.get(f"/v1/staff/work-items/{item_id}")
    assert elapsed < BOARD_LATENCY_BUDGET_S
    assert size < BOARD_PAYLOAD_BUDGET_BYTES
    assert detail["workItem"]["id"] == item_id
    assert "signals" in detail["workItem"]
    assert len(detail["relatedItems"]) <= 20


# --- Morning Brew -----------------------------------------------------------


@pytest.fixture(scope="module")
def brew(session: Session) -> dict[str, Any]:
    payload, elapsed, _ = session.get("/v1/staff/morning-brew")
    assert elapsed < BREW_LATENCY_BUDGET_S, f"{elapsed:.2f}s"
    return payload


def _explorer_rows(sql: str) -> list[dict[str, Any]]:
    try:
        return _rows(sql, url=EXPLORER_URL)
    except Exception as error:
        pytest.skip(f"Explorer database unavailable: {error}")


def test_brew_totals_match_the_database(brew: dict[str, Any], truth: dict[str, int]) -> None:
    assert brew["staffWork"]["openItems"] == truth["open"]
    assert brew["staffWork"]["unassigned"] == truth["unassigned"]
    assert brew["staffWork"]["urgent"] == truth["urgent"]
    assert brew["staffCapacity"]["summary"]["staleItems"] == truth["stale"]
    assert brew["staffCapacity"]["summary"]["unassignedItems"] == truth["unassigned"]


def test_brew_names_the_planted_staff_situations(brew: dict[str, Any]) -> None:
    """The Explorer's archetypes are the oracle for who must appear."""

    planted = _explorer_rows(
        "SELECT first_name || ' ' || last_name AS name, archetype, status FROM staff"
        " WHERE archetype IN ('departed','on_leave','overloaded','underutilized','falling_behind')"
        " OR status IN ('departed','on_leave')"
    )
    signals = brew["staffCapacity"]["signals"]
    by_kind: dict[str, list[str]] = {}
    for signal in signals:
        by_kind.setdefault(signal["kind"], []).append(signal["title"])
    names = {row["name"]: row for row in planted}

    def expect(kind: str, name: str) -> None:
        assert any(title.startswith(name) for title in by_kind.get(kind, [])), (
            f"{kind} should name {name}; got {by_kind}"
        )

    departed = [n for n, r in names.items() if r["status"] == "departed"]
    on_leave = [n for n, r in names.items() if r["status"] == "on_leave"]
    assert departed and on_leave
    expect("departed_with_caseload", departed[0])
    expect("on_leave_with_caseload", on_leave[0])
    overloaded_no_slots = "Elena Larkspur"
    expect("over_cap_no_slots", overloaded_no_slots)
    expect("spare_capacity", "Ximena Calderwood")
    expect("falling_behind", "Vera Jessamy")
    expect("away_with_backlog", "Camila Okonkwo")
    assert by_kind.get("component_backlog"), "the Registrar/ISS backlog must surface"
    assert by_kind.get("students_without_adviser")
    # The control case never appears.
    assert not any("Ada Ashgrove" in signal["title"] for signal in signals)
    # Concise: a page, not a dashboard.
    assert len(signals) <= 10
    assert signals[0]["severity"] == "critical"


def test_brew_capacity_numbers_match_the_explorer(brew: dict[str, Any]) -> None:
    summary = brew["staffCapacity"]["summary"]
    departed_caseload = _explorer_rows(
        "SELECT COUNT(*) AS n FROM student_staff_assignments a JOIN staff s ON s.id=a.staff_id"
        " WHERE a.role='primary_advisor' AND a.ended_at IS NULL AND s.status='departed'"
    )[0]["n"]
    on_leave_caseload = _explorer_rows(
        "SELECT COUNT(*) AS n FROM student_staff_assignments a JOIN staff s ON s.id=a.staff_id"
        " WHERE a.role='primary_advisor' AND a.ended_at IS NULL AND s.status='on_leave'"
    )[0]["n"]
    assert summary["studentsWithDepartedAdviser"] == int(departed_caseload)
    assert summary["studentsWithAdviserOnLeave"] == int(on_leave_caseload)
    assert summary["departed"] == 1 and summary["onLeave"] == 1


def test_brew_priorities_and_synthesis_carry_the_people_dimension(brew: dict[str, Any]) -> None:
    ids = {item["id"]: item for item in brew["priorities"]}
    assert (
        ids["ownership-at-risk"]["count"]
        == brew["staffCapacity"]["summary"]["itemsOwnedByUnavailable"]
    )
    assert ids["stale-work"]["count"] == brew["staffCapacity"]["summary"]["staleItems"]
    text_blob = json.dumps(brew["synthesis"])
    assert any(word in text_blob for word in ("has left", "on leave", "over caseload", "away"))


def test_brew_inactivity_is_not_asserted_without_activity_coverage(brew: dict[str, Any]) -> None:
    scan = brew["engagementScan"]
    assert "activitySignal" in scan
    if not scan["activitySignal"]:
        assert any("inactivity is unknown" in note for note in brew["coverage"]["notes"])


def test_escalations_are_a_counted_change(brew: dict[str, Any]) -> None:
    changes = {change["id"]: change for change in brew["changes"]}
    assert "work_items_escalated" in changes
    assert changes["work_items_escalated"]["basis"] == "staff_work_log.occurred_at"
