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


def test_requests_awaiting_reply_route_to_inquiries_not_the_roster() -> None:
    for question in (
        "How many student requests are still awaiting a first reply, and which is the oldest?",
        "How many student inquiries are awaiting a first reply?",
        "how many open student requests are waiting on us for a response",
        "Which requests are unanswered?",
    ):
        classification = classify_staff_request(normalize_staff_request(question))
        assert classification is not None, question
        assert classification.request_type == "inquiries", question
    roster = classify_staff_request(normalize_staff_request("How big is the roster?"))
    assert roster is not None and roster.request_type == "cohort_aggregate"


def test_queue_tool_sends_filters_to_the_server_and_stays_bounded() -> None:
    from audentra.integrations.staff_assistant.tools import (
        QUEUE_PAGE_SIZE,
        StaffAssistantToolHost,
        _tool_work_queue,
    )

    seen: list[dict[str, Any]] = []

    async def work_queue(query: dict[str, Any] | None = None) -> dict[str, Any]:
        seen.append(dict(query or {}))
        return {
            "items": [
                {
                    "id": "1",
                    "key": "AST-1",
                    "title": "t",
                    "status": "in_progress",
                    "priority": "high",
                    "component": "Registrar",
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

    host = StaffAssistantToolHost({"work_queue": work_queue}, staff_member_id="me")
    result = asyncio.run(
        _tool_work_queue(
            host,
            {"assigneeName": "Vera Jessamy", "stale": "true", "status": "in_progress"},
            NOW,
        )
    )
    assert seen == [
        {
            "status": "in_progress",
            "component": None,
            "search": None,
            "due": "all",
            "stale": "true",
            "ownerRisk": None,
            "sort": "priority",
            "limit": QUEUE_PAGE_SIZE,
            "assignee": "Vera Jessamy",
        }
    ]
    assert result["filteredTotal"] == 5 and result["filteredOpen"] == 5
    assert result["items"][0]["signals"]["stale"] is True
    assert result["items"][0]["assignee"]["name"] == "Vera Jessamy"

    asyncio.run(_tool_work_queue(host, {"ownership": "mine", "key": "AST-00102"}, NOW))
    assert seen[-1]["assignee"] == "me"
    assert seen[-1]["status"] == "all" and seen[-1]["search"] == "AST-00102"


def test_staff_questions_route_to_the_staff_workload_read() -> None:
    expectations = {
        "Which staff members have the most overdue work?": "staff:",
        "Is Junia Pemberwell available this week?": "staff:Junia Pemberwell",
        "How many students does Elena Larkspur advise?": "staff:Elena Larkspur",
        "When is Elena Larkspur's next open advising slot?": "staff:Elena Larkspur",
        "Which advisers are over their caseload cap?": "staff:",
        "Are there any students whose adviser has left the university?": "staff:",
        "Which of Vera Jessamy's work items have been in progress for more than a week?": (
            "staff:Vera Jessamy|stale"
        ),
    }
    for question, reference in expectations.items():
        classification = classify_staff_request(normalize_staff_request(question))
        assert classification is not None, question
        assert classification.request_type == "staff_workload", question
        assert classification.reference == reference, question
    # Student questions that merely mention advisers stay student/cohort scoped.
    for question, expected in {
        "Which students are most at risk?": "attention_ranking",
        "How many students have not paid a deposit?": "cohort_aggregate",
        "What is blocking Ingrid Thistlebrook?": "student_blockers",
    }.items():
        classification = classify_staff_request(normalize_staff_request(question))
        assert classification is not None and classification.request_type == expected, question


def test_staff_reference_binds_queue_and_directory_arguments() -> None:
    from audentra.integrations.staff_assistant.classify import StaffClassification
    from audentra.integrations.staff_assistant.pipeline import _cohort_arguments

    classification = StaffClassification(
        "staff_workload", 0.96, reference="staff:Vera Jessamy|stale"
    )
    assert _cohort_arguments("getStaffWorkQueue", classification) == {
        "assigneeName": "Vera Jessamy",
        "stale": "true",
        "status": "in_progress",
    }
    assert _cohort_arguments("getStaffMember", classification) == {"name": "Vera Jessamy"}
    anonymous = StaffClassification("staff_workload", 0.96, reference="staff:")
    assert _cohort_arguments("getStaffWorkQueue", anonymous) == {}
    assert _cohort_arguments("getStaffMember", anonymous) == {}
