"""Morning Brew staff-capacity signals, engagement honesty, and usage accounting.

The fixtures mirror the situations the synthetic university plants (a departed
adviser with a caseload, one on leave, one over cap with no slots, one with
spare capacity, one falling behind, an evaluator on vacation behind a queue,
an office with most of its work overdue). The briefing must name each of them
and must not drown them in one repeated kind of signal.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from audentra.domain.engagement import activity_feed_covers_population, decide_inactive
from audentra.domain.staff_capacity import (
    MAX_SIGNALS,
    compose_staff_capacity,
    person_signals,
    rank_signals,
)
from audentra.infrastructure.postgres.model_usage_ledger import normalize_usage
from audentra.integrations.staff_assistant.classify import classify_staff_request
from audentra.integrations.staff_assistant.entities import (
    EntityResolution,
    ResolvedEntity,
    extract_mentions,
    has_self_reference,
    has_staff_context,
    has_student_context,
)
from audentra.integrations.staff_assistant.normalize import normalize_staff_request

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)


def _person(
    name: str,
    *,
    status: str = "active",
    advisees: int = 90,
    cap: int | None = 150,
    open_items: int = 5,
    overdue: int = 1,
    stale: int = 0,
    unclosed: int = 0,
    slots: int | None = 30,
    away_until: str | None = None,
    away_kind: str | None = None,
    component: str = "Academic Advising",
    ended_at: str | None = None,
    leave_until: str | None = None,
) -> dict[str, Any]:
    return {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "title": "Adviser",
        "component": component,
        "employmentStatus": status,
        "endedAt": ended_at,
        "leaveUntil": leave_until,
        "awayUntil": away_until,
        "awayKind": away_kind,
        "studentFacing": True,
        "caseload": {"primaryAdvisees": advisees, "cap": cap},
        "work": {
            "open": open_items,
            "overdue": overdue,
            "urgent": 0,
            "staleInProgress": stale,
            "appointmentsAwaitingOutcome": unclosed,
        },
        "availability": (
            {"bookable": True, "nextOpenSlotAt": None, "openSlotsNext14Days": slots}
            if slots is not None
            else None
        ),
    }


def _snapshot() -> dict[str, Any]:
    return {
        "people": [
            _person("Ada Ashgrove"),  # the control: nothing fires
            _person(
                "Quentin Zephyrine",
                status="departed",
                advisees=79,
                open_items=6,
                slots=None,
                ended_at="2026-08-01",
            ),
            _person(
                "Junia Pemberwell",
                status="on_leave",
                advisees=67,
                open_items=2,
                slots=None,
                leave_until="2026-09-21",
            ),
            _person("Elena Larkspur", advisees=126, cap=110, open_items=16, stale=3, slots=0),
            _person("Ximena Calderwood", advisees=51, cap=150, open_items=2, slots=131),
            _person("Vera Jessamy", advisees=100, cap=149, open_items=12, stale=5, unclosed=8),
            _person(
                "Camila Okonkwo",
                advisees=0,
                cap=None,
                open_items=96,
                overdue=59,
                stale=4,
                away_until="2026-08-29T00:00:00Z",
                away_kind="vacation",
                component="Registrar",
            ),
            # A big steady queue with a few stale items is not "falling behind".
            _person("Kirsten Abernathy", cap=None, open_items=231, stale=19, component="Health"),
        ]
        + [_person(f"Office {index}", component="Admissions") for index in range(5)],
        "components": [
            {
                "component": "Registrar",
                "open": 507,
                "overdue": 268,
                "unassigned": 0,
                "stale": 22,
                "urgent": 0,
                "escalated": 100,
                "ownerRisk": 96,
                "oldestOverdueDays": 22,
            },
            {
                "component": "Student Accounts",
                "open": 223,
                "overdue": 171,
                "unassigned": 61,
                "stale": 3,
                "urgent": 5,
                "escalated": 50,
                "ownerRisk": 0,
                "oldestOverdueDays": 12,
            },
            {
                "component": "New Student Programs",
                "open": 35,
                "overdue": 3,
                "unassigned": 0,
                "stale": 0,
                "urgent": 0,
                "escalated": 0,
                "ownerRisk": 0,
                "oldestOverdueDays": 2,
            },
        ],
        "students": {
            "acceptedWithoutPrimaryAdviser": 434,
            "depositedWithoutPrimaryAdviser": 55,
            "withDepartedAdviser": 79,
            "withAdviserOnLeave": 67,
        },
    }


def _kinds(section: dict[str, Any]) -> dict[str, str]:
    return {s["kind"]: s["title"] for s in section["signals"]}


def test_every_planted_situation_is_named_in_the_signals() -> None:
    section = compose_staff_capacity(_snapshot(), now=NOW)
    kinds = _kinds(section)
    assert kinds["departed_with_caseload"].startswith("Quentin Zephyrine")
    assert kinds["on_leave_with_caseload"].startswith("Junia Pemberwell")
    assert kinds["over_cap_no_slots"].startswith("Elena Larkspur")
    assert kinds["spare_capacity"].startswith("Ximena Calderwood")
    assert kinds["falling_behind"].startswith("Vera Jessamy")
    assert kinds["away_with_backlog"].startswith("Camila Okonkwo")
    assert kinds["component_backlog"].startswith("Registrar")
    assert kinds["unassigned_backlog"].endswith("Student Accounts items have no owner")
    assert kinds["students_without_adviser"] == "55 deposited students have no adviser"
    titles = " ".join(kinds.values())
    assert "Ada Ashgrove" not in titles
    assert "Kirsten Abernathy" not in titles
    assert "New Student Programs" not in titles


def test_signals_are_ranked_by_severity_then_spread_across_kinds() -> None:
    section = compose_staff_capacity(_snapshot(), now=NOW)
    signals = section["signals"]
    assert signals[0]["kind"] == "departed_with_caseload"
    assert signals[0]["severity"] == "critical"
    assert len(signals) <= MAX_SIGNALS
    # One of each kind before any kind repeats.
    first_repeat = next(
        (i for i, s in enumerate(signals) if s["kind"] in {x["kind"] for x in signals[:i]}),
        len(signals),
    )
    assert first_repeat == len({s["kind"] for s in signals})
    # The first pass (one per kind) is severity-ordered; later passes fill in.
    first_pass = signals[:first_repeat]
    severities = [s["severity"] for s in first_pass]
    assert severities == sorted(
        severities, key={"critical": 0, "high": 1, "medium": 2, "positive": 3}.__getitem__
    )


def test_summary_counts_and_board_queries() -> None:
    section = compose_staff_capacity(_snapshot(), now=NOW)
    summary = section["summary"]
    assert summary["staff"] == 13
    assert summary["departed"] == 1 and summary["onLeave"] == 1 and summary["awayNow"] == 1
    assert summary["overCap"] == 1 and summary["spareCapacity"] == 1
    # Vera (5 stale of 12) and the over-cap Elena (3 stale of 16) both qualify.
    assert summary["fallingBehind"] == 2
    assert summary["itemsOwnedByUnavailable"] == 96
    assert summary["depositedWithoutAdviser"] == 55
    by_kind = {s["kind"]: s for s in section["signals"]}
    assert by_kind["departed_with_caseload"]["boardQuery"] == {
        "assignee": "quentin-zephyrine",
        "status": "open",
    }
    assert by_kind["away_with_backlog"]["boardQuery"] == {
        "assignee": "camila-okonkwo",
        "due": "overdue",
    }
    assert by_kind["component_backlog"]["boardQuery"] == {
        "component": "Registrar",
        "due": "overdue",
    }
    assert by_kind["spare_capacity"]["destination"] == "students"


def test_falling_behind_needs_a_meaningful_stale_share() -> None:
    steady = _person("Big Queue", cap=None, open_items=200, stale=5)
    behind = _person("Small Queue", cap=None, open_items=12, stale=5)
    assert [s.kind for s in person_signals(steady, now=NOW)] == []
    assert [s.kind for s in person_signals(behind, now=NOW)] == ["falling_behind"]
    unclosed = _person("Never Closes", unclosed=6)
    assert [s.kind for s in person_signals(unclosed, now=NOW)] == ["falling_behind"]


def test_over_cap_with_slots_is_medium_not_high() -> None:
    signals = person_signals(_person("Lucia Oakenshaw", advisees=125, cap=120), now=NOW)
    assert [(s.kind, s.severity) for s in signals] == [("over_cap", "medium")]


def test_unavailable_snapshot_is_declared_not_zeroed() -> None:
    section = compose_staff_capacity(None, now=NOW)
    assert section["available"] is False
    assert section["signals"] == [] and section["summary"] is None
    assert "cannot be read" in section["basis"]


def test_rank_is_deterministic_for_ties() -> None:
    a = person_signals(_person("A Person", status="departed", advisees=10, slots=None), now=NOW)
    b = person_signals(_person("B Person", status="departed", advisees=10, slots=None), now=NOW)
    assert [s.id for s in rank_signals(b + a)] == ["departed:a-person", "departed:b-person"]


# --- engagement honesty -----------------------------------------------------


def test_inactivity_is_unknown_without_population_coverage() -> None:
    assert activity_feed_covers_population(None, None) is True  # legacy callers
    assert activity_feed_covers_population(5, 2576) is False  # the mock university
    assert activity_feed_covers_population(200, 2576) is True
    assert activity_feed_covers_population(1, 14) is True  # a small demo tenant
    assert activity_feed_covers_population(0, 14) is False
    assert decide_inactive(None, now=NOW, activity_known=False) is None
    assert decide_inactive(None, now=NOW, activity_known=True) is True
    assert decide_inactive(NOW - timedelta(days=8), now=NOW, activity_known=True) is True
    assert decide_inactive(NOW - timedelta(days=2), now=NOW, activity_known=True) is False


# --- model usage ledger -----------------------------------------------------


def test_usage_ledger_accepts_both_gateway_token_shapes() -> None:
    assert normalize_usage({"promptTokens": 120, "completionTokens": 30, "totalTokens": 150}) == {
        "input_tokens": 120,
        "output_tokens": 30,
        "cached_input_tokens": None,
        "latency_ms": None,
    }
    assert normalize_usage({"inputTokens": 7, "outputTokens": 0, "latencyMs": 900}) == {
        "input_tokens": 7,
        "output_tokens": 0,
        "cached_input_tokens": None,
        "latency_ms": 900,
    }
    assert normalize_usage({"totalTokens": 5}) is None
    assert normalize_usage(None) is None


# --- Staff Edward routing ---------------------------------------------------
#
# The people-and-capacity questions the Brew answers are also asked of Edward.
# They route through the staff-aware intents (entity resolution first, then
# the classifier), and every queue read goes through the one Action Center
# query layer the portal's board uses.


def _entities(text: str, staff: tuple[str, ...] = ()) -> EntityResolution:
    resolution = EntityResolution(
        text=text,
        mentions=extract_mentions(text),
        self_reference=has_self_reference(text),
        staff_context=has_staff_context(text),
        student_context=has_student_context(text),
    )
    for name in staff:
        resolution.staff.append(ResolvedEntity("staff", f"id-{name}", name, name))
    return resolution


def test_requests_awaiting_reply_route_to_inquiry_aggregates_not_the_roster() -> None:
    for question in (
        "How many student requests are still awaiting a first reply, and which is the oldest?",
        "How many student inquiries are awaiting a first reply?",
    ):
        classification = classify_staff_request(
            normalize_staff_request(question), _entities(question)
        )
        assert classification is not None, question
        assert classification.request_type == "inquiry_aggregate", question
        assert classification.cohort_filter is not None
        assert classification.cohort_filter.get("status") == "awaiting_first_reply", question
    roster = classify_staff_request(normalize_staff_request("How big is the roster?"))
    assert roster is not None and roster.request_type == "cohort_aggregate"


def test_staff_questions_route_to_staff_intents_never_the_roster() -> None:
    expectations = {
        "Which staff members have the most overdue work?": ("queue_aggregate", ()),
        "Which department has the most unassigned work?": ("queue_aggregate", ()),
        "Is Junia Pemberwell available this week?": ("staff_availability", ("Junia Pemberwell",)),
        "How many students does Elena Larkspur advise?": ("staff_caseload", ("Elena Larkspur",)),
        "When is Elena Larkspur's next open advising slot?": (
            "staff_availability",
            ("Elena Larkspur",),
        ),
        "Which advisers are over their caseload cap?": ("team_overview", ()),
        "Which of Vera Jessamy's work items have been in progress for more than a week?": (
            "staff_workload",
            ("Vera Jessamy",),
        ),
    }
    for question, (expected, staff) in expectations.items():
        classification = classify_staff_request(
            normalize_staff_request(question), _entities(question, staff)
        )
        assert classification is not None, question
        assert classification.request_type == expected, question
    stale = classify_staff_request(
        normalize_staff_request(
            "Which of Vera Jessamy's work items have been in progress for more than a week?"
        ),
        _entities("Which of Vera Jessamy's work items have been in progress?", ("Vera Jessamy",)),
    )
    assert stale is not None and stale.cohort_filter is not None
    assert stale.cohort_filter.get("inProgressOverDays") == 7
    # Student questions that merely mention advisers stay student/cohort scoped.
    for question, expected in {
        "Which students are most at risk?": "attention_ranking",
        "How many students have not paid a deposit?": "cohort_aggregate",
        "What is blocking Ingrid Thistlebrook?": "student_blockers",
    }.items():
        classification = classify_staff_request(normalize_staff_request(question))
        assert classification is not None and classification.request_type == expected, question


def test_queue_tools_share_one_bounded_query_vocabulary() -> None:
    from audentra.integrations.staff_assistant.tools import (
        QUEUE_PAGE_SIZE,
        StaffAssistantToolHost,
        _tool_search_work_queue,
        _tool_summarize_work_queue,
        _tool_work_queue,
    )

    seen: list[tuple[str, dict[str, Any]]] = []

    async def work_queue(query: dict[str, Any] | None = None) -> dict[str, Any]:
        seen.append(("work_queue", dict(query or {})))
        return {
            "items": [
                {
                    "id": "1",
                    "key": "AST-1",
                    "title": "t",
                    "status": "in_progress",
                    "priority": "high",
                    "component": "Registrar",
                    "type": "enrollment",
                    "student": {"id": "s", "name": "S"},
                    "assignee": {"id": "a", "name": "Vera Jessamy", "employmentStatus": "active"},
                    "signals": {"stale": True, "staleDays": 12},
                }
            ],
            "counts": {"open": 2523},
            "page": {"total": 5, "hasMore": False, "distinctStudents": 5},
            "facets": {"components": [], "assignees": []},
            "generatedAt": "2026-08-26T12:00:00.000Z",
        }

    async def work_queue_summary(
        query: dict[str, Any] | None = None, group_by: str | None = None, limit: int = 12
    ) -> dict[str, Any]:
        seen.append(("work_queue_summary", {**dict(query or {}), "groupBy": group_by}))
        return {"total": 5, "buckets": [], "groupBy": group_by}

    host = StaffAssistantToolHost(
        {"work_queue": work_queue, "work_queue_summary": work_queue_summary},
        staff_member_id="me",
    )
    result = asyncio.run(
        _tool_work_queue(
            host,
            {"assigneeName": "Vera Jessamy", "stale": "true", "status": "in_progress"},
            NOW,
        )
    )
    assert seen[-1] == (
        "work_queue",
        {
            "status": "in_progress",
            "search": None,
            "due": "all",
            "stale": True,
            "sort": "priority",
            "limit": QUEUE_PAGE_SIZE,
            "assignee": "Vera Jessamy",
        }
        | {"search": None},
    ) or seen[-1][1] == {
        "status": "in_progress",
        "due": "all",
        "stale": True,
        "sort": "priority",
        "limit": QUEUE_PAGE_SIZE,
        "assignee": "Vera Jessamy",
    }
    assert result["filteredTotal"] == 5 and result["filteredOpen"] == 5
    assert result["items"][0]["signals"]["stale"] is True
    assert result["items"][0]["daysSinceUpdate"] == 12
    assert result["items"][0]["workType"] == "enrollment"

    asyncio.run(_tool_work_queue(host, {"ownership": "mine", "key": "AST-00102"}, NOW))
    assert seen[-1][1]["assignee"] == "me"
    assert seen[-1][1]["status"] == "all" and seen[-1][1]["search"] == "AST-00102"

    # The assistant's page and counts speak the same vocabulary as the board.
    page = asyncio.run(
        _tool_search_work_queue(
            host,
            {"staffId": "staff-1", "inProgressOverDays": 7, "sort": "stalest", "limit": 5},
            NOW,
        )
    )
    assert seen[-1][1] == {
        "status": "open",
        "due": "all",
        "inProgressDays": 7,
        "sort": "stale",
        "limit": 5,
        "assignee": "staff-1",
    }
    assert page["total"] == 5 and page["returned"] == 1 and page["truncated"] is True
    summary = asyncio.run(
        _tool_summarize_work_queue(
            host, {"ownership": "unassigned", "dueWindow": "overdue", "groupBy": "component"}, NOW
        )
    )
    assert seen[-1] == (
        "work_queue_summary",
        {
            "status": "open",
            "due": "overdue",
            "sort": "priority",
            "limit": QUEUE_PAGE_SIZE,
            "assignee": "unassigned",
            "groupBy": "component",
        },
    )
    assert summary["filters"] == {"ownership": "unassigned", "dueWindow": "overdue"}
