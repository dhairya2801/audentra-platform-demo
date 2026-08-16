"""Canonical Staff Edward cohort vocabulary and deterministic tool routing."""

from __future__ import annotations

from typing import Any

import pytest

from audentra.domain.student_cohort import (
    COHORT_GROUP_BY,
    DOCUMENT_STATES,
    OFFER_STATUSES,
    CohortFilter,
    CohortFilterError,
    build_cohort_filter,
    validate_group_by,
)
from audentra.integrations.staff_assistant.catalog import (
    ToolArgumentError,
    validate_tool_arguments,
)
from audentra.integrations.staff_assistant.classify import (
    classify_cohort_question,
)
from audentra.integrations.staff_assistant.pipeline import StaffAssistantPipeline
from audentra.integrations.staff_assistant.tools import StaffAssistantToolHost


def test_filter_vocabulary_matches_canonical_database_semantics() -> None:
    assert OFFER_STATUSES == ("offered", "accepted", "declined", "expired")
    assert DOCUMENT_STATES == (
        "missing",
        "submitted",
        "under_review",
        "accepted",
        "rejected",
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"unexpectedFilter": "ignored by the parity implementation"},
        {"offerStatus": "withdrawn"},
        {"depositState": "kind-of-paid"},
        {"documentState": "received"},
        {"assignedStaffId": "not-a-uuid"},
        {"hasOpenWorkItem": "true"},
        {"classYear": "next year"},
    ],
)
def test_unsupported_filters_fail_instead_of_disappearing(raw: dict[str, Any]) -> None:
    with pytest.raises(CohortFilterError):
        build_cohort_filter(raw)


def test_valid_filter_is_conjunctive_and_self_describing() -> None:
    cohort = build_cohort_filter(
        {
            "offerStatus": "accepted",
            "depositState": "unpaid",
            "residencyStatus": "international",
            "hasOpenWorkItem": True,
        }
    )

    assert cohort.offer_status == "accepted"
    assert cohort.deposit_state == "unpaid"
    assert cohort.residency_status == "international"
    assert cohort.has_open_work_item is True
    assert len(cohort.describe()) == 4


def test_grouping_vocabulary_is_strict() -> None:
    assert {validate_group_by(value) for value in COHORT_GROUP_BY} == set(COHORT_GROUP_BY)
    with pytest.raises(CohortFilterError):
        validate_group_by("risk_score")


@pytest.mark.parametrize(
    ("question", "request_type", "expected_filter", "group_by"),
    [
        (
            "Which admitted students haven't paid their deposit?",
            "cohort_search",
            {"offerStatus": "accepted", "depositState": "unpaid"},
            None,
        ),
        (
            "How many international students have incomplete aid verification?",
            "cohort_aggregate",
            {"residencyStatus": "international", "aidDocumentState": "outstanding"},
            "offer_status",
        ),
        (
            "What are the most common blockers for students?",
            "cohort_aggregate",
            {},
            "blocking_requirement",
        ),
    ],
)
def test_deterministic_cohort_classification(
    question: str,
    request_type: str,
    expected_filter: dict[str, Any],
    group_by: str | None,
) -> None:
    classification = classify_cohort_question(question)
    assert classification is not None
    assert classification.request_type == request_type
    assert classification.cohort_filter == expected_filter
    assert classification.cohort_group_by == group_by


def test_tool_catalog_returns_validated_domain_objects() -> None:
    arguments = validate_tool_arguments(
        "findStudents",
        {"filter": {"offerStatus": "accepted", "depositState": "unpaid"}, "limit": 500},
    )
    assert isinstance(arguments["filter"], CohortFilter)
    assert arguments["filter"].deposit_state == "unpaid"
    assert arguments["limit"] == 50

    with pytest.raises(ToolArgumentError) as error:
        validate_tool_arguments("findStudents", {"filter": {"risk": "high"}})
    assert error.value.code == "invalid_cohort_filter"


@pytest.mark.anyio
async def test_pipeline_routes_cohort_without_resolving_a_student() -> None:
    captured: list[CohortFilter] = []

    async def find_students(*, cohort: CohortFilter, limit: int) -> dict[str, Any]:
        captured.append(cohort)
        return {
            "items": [
                {
                    "id": "80000000-0000-7000-8000-000000000101",
                    "name": "Jordan Ellis",
                    "programName": "Computer Science",
                    "classYear": 2027,
                    "offerStatus": "accepted",
                    "depositState": "unpaid",
                    "depositPaid": False,
                    "requirements": {"total": 8, "completed": 3, "openBlocking": 2},
                }
            ],
            "returned": 1,
            "total": 7,
            "truncated": True,
            "filter": ["admission offer status = accepted", "deposit state = unpaid"],
        }

    pipeline = StaffAssistantPipeline(StaffAssistantToolHost({"find_students": find_students}))
    result = await pipeline.execute(message="Which admitted students haven't paid their deposit?")

    assert captured and captured[0].offer_status == "accepted"
    assert captured[0].deposit_state == "unpaid"
    assert result.resolved_student_id is None
    assert "7 student(s) match" in result.message
    assert "Showing the first 1" in result.message
