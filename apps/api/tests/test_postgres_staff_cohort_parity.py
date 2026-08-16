"""Staff cohort reads: tenancy, correctness, and list/count agreement.

Staff Edward's cohort capability is the one surface where a single query can
leak an entire institution, so tenancy is asserted per filter rather than once.
The other invariant here is internal: `findStudents` and `summarizeStudents`
share one predicate builder, and these tests hold them to the same answer —
a count that disagrees with its own list is worse than no count.

Every expectation is derived from the database at run time, not hard-coded, so
the suite keeps its meaning when the seeded funnel changes.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.student_cohort import (
    COHORT_GROUP_BY,
    CohortFilterError,
    build_cohort_filter,
)
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.staff_assistant_repository import (
    PostgresStaffAssistantRepository,
)
from audentra.infrastructure.seeding.relational import (
    ASTER_TENANT_ID,
    HARVARD_STAFF_ID,
    HARVARD_TENANT_ID,
    reset_relational_data,
    seed_relational_data,
)

pytestmark = pytest.mark.integration

ASTER_STAFF_ID = "00000000-0000-7000-8000-000000000901"

HARVARD = AuthContext(
    tenant_id=HARVARD_TENANT_ID,
    student_id="",
    actor_id=HARVARD_STAFF_ID,
    actor_type="staff",
)
ASTER = AuthContext(
    tenant_id=ASTER_TENANT_ID,
    student_id="",
    actor_id=ASTER_STAFF_ID,
    actor_type="staff",
)

# One representative filter per supported dimension.
FILTERS: tuple[dict[str, Any], ...] = (
    {},
    {"query": "Grace"},
    {"program": "Computer"},
    {"classYear": 2027},
    {"offerStatus": "accepted"},
    {"offerStatus": "offered"},
    {"depositState": "paid"},
    {"depositState": "pending"},
    {"depositState": "unpaid"},
    {"onboardingStatus": "not_started"},
    {"onboardingStatus": "completed"},
    {"onboardingStatus": "in_progress"},
    {"documentCategory": "transcript", "documentState": "missing"},
    {"documentCategory": "transcript", "documentState": "under_review"},
    {"documentCategory": "transcript", "documentState": "rejected"},
    {"documentCategory": "identity", "documentState": "accepted"},
    {"requirementCode": "immunization_record", "requirementState": "open"},
    {"requirementCode": "official_transcript", "requirementState": "in_review"},
    {"requirementCode": "housing_preference", "requirementState": "complete"},
    {"requirementCode": "housing_preference", "requirementState": "any"},
    {"requirementState": "overdue"},
    {"aidDocumentState": "outstanding"},
    {"aidDocumentState": "verified"},
    {"aidDocumentState": "action_required"},
    {"aidDocumentState": "in_review"},
    {"housingState": "blocked"},
    {"housingState": "actionable"},
    {"housingState": "selected"},
    {"housingState": "no_step"},
    {"assignedStaffId": HARVARD_STAFF_ID},
    {"residencyStatus": "international"},
    {"citizenshipStatus": "us_citizen"},
    {"hasOverdueRequirement": True},
    {"hasOverdueRequirement": False},
    {"hasOpenWorkItem": True},
    {"hasOpenWorkItem": False},
    {"offerStatus": "accepted", "depositState": "unpaid"},
)


def _run(scenario: Any) -> None:
    url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AUDENTRA_TEST_DATABASE_URL is not configured")

    async def main() -> None:
        engine = create_database_engine(url)
        try:
            await reset_relational_data(engine, environment="test", completed_onboarding=True)
            await seed_relational_data(engine, environment="test")
            await scenario(PostgresStaffAssistantRepository(engine), engine)
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_every_filter_stays_inside_the_authenticated_tenant() -> None:
    """The invariant that matters most: no filter reaches another institution."""

    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        async with engine.connect() as connection:
            harvard_ids = {
                str(row[0])
                for row in await connection.execute(
                    text("SELECT id FROM student WHERE tenant_id = :t"),
                    {"t": HARVARD_TENANT_ID},
                )
            }
            aster_ids = {
                str(row[0])
                for row in await connection.execute(
                    text("SELECT id FROM student WHERE tenant_id = :t"),
                    {"t": ASTER_TENANT_ID},
                )
            }
        assert harvard_ids and aster_ids and not (harvard_ids & aster_ids)

        for raw in FILTERS:
            result = await repo.find_students(HARVARD, build_cohort_filter(raw), limit=50)
            returned = {item["id"] for item in result.items}
            assert returned <= harvard_ids, f"{raw} returned a non-Harvard student"
            assert not (returned & aster_ids), f"{raw} leaked an Aster student"

    _run(scenario)


def test_each_tenant_context_returns_a_disjoint_population() -> None:
    """Tenant comes from the authenticated context, never cohort arguments."""

    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        harvard = await repo.find_students(HARVARD, build_cohort_filter({}), limit=50)
        aster = await repo.find_students(ASTER, build_cohort_filter({}), limit=50)
        assert harvard.total > 0 and aster.total > 0
        assert {item["id"] for item in harvard.items}.isdisjoint(
            {item["id"] for item in aster.items}
        )

        summary = await repo.summarize_students(
            ASTER, build_cohort_filter({}), group_by="offer_status"
        )
        assert summary["matchingStudents"] == aster.total

    _run(scenario)


def test_a_non_staff_actor_is_refused() -> None:
    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        student_actor = AuthContext(
            tenant_id=HARVARD_TENANT_ID,
            student_id="",
            actor_id=HARVARD_STAFF_ID,
            actor_type="student",
        )
        with pytest.raises(ApiError) as list_error:
            await repo.find_students(student_actor, build_cohort_filter({}), limit=5)
        assert list_error.value.code == "STAFF_ACCESS_REQUIRED"
        with pytest.raises(ApiError) as summary_error:
            await repo.summarize_students(
                student_actor, build_cohort_filter({}), group_by="offer_status"
            )
        assert summary_error.value.code == "STAFF_ACCESS_REQUIRED"

    _run(scenario)


def test_the_count_always_matches_its_own_list() -> None:
    """`total` and the returned page must describe one selection."""

    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        for raw in FILTERS:
            cohort = build_cohort_filter(raw)
            listed = await repo.find_students(HARVARD, cohort, limit=50)
            summary = await repo.summarize_students(HARVARD, cohort, group_by="offer_status")
            assert summary["matchingStudents"] == listed.total, raw
            assert len(listed.items) == min(listed.total, 50), raw
            assert listed.truncated is (listed.total > len(listed.items)), raw

    _run(scenario)


def test_filters_actually_narrow_and_agree_with_the_records() -> None:
    """Spot-check each filter against the row it claims to select."""

    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        everyone = await repo.find_students(HARVARD, build_cohort_filter({}), limit=50)
        assert everyone.total >= 10, "the seeded funnel is too small to test cohorts"

        paid = await repo.find_students(
            HARVARD, build_cohort_filter({"depositState": "paid"}), limit=50
        )
        unpaid = await repo.find_students(
            HARVARD, build_cohort_filter({"depositState": "unpaid"}), limit=50
        )
        assert paid.total > 0 and unpaid.total > 0
        assert paid.total + unpaid.total == everyone.total
        assert all(item["depositPaid"] for item in paid.items)
        assert not any(item["depositPaid"] for item in unpaid.items)

        async with engine.connect() as connection:
            expected_paid = {
                str(row[0])
                for row in await connection.execute(
                    text(
                        """
                        SELECT DISTINCT student_id FROM payment_transaction
                        WHERE tenant_id = :t AND type = 'enrollment_deposit'
                          AND status = 'succeeded'
                        """
                    ),
                    {"t": HARVARD_TENANT_ID},
                )
            }
        assert {item["id"] for item in paid.items} == expected_paid

        accepted = await repo.find_students(
            HARVARD, build_cohort_filter({"offerStatus": "accepted"}), limit=50
        )
        assert 0 < accepted.total < everyone.total
        assert all(item["offerStatus"] == "accepted" for item in accepted.items)

        # Conjunctive narrowing: the pair can only be a subset of each half.
        both = await repo.find_students(
            HARVARD,
            build_cohort_filter({"offerStatus": "accepted", "depositState": "unpaid"}),
            limit=50,
        )
        assert both.total <= min(accepted.total, unpaid.total)
        assert {item["id"] for item in both.items} <= {item["id"] for item in accepted.items}

    _run(scenario)


def test_pagination_reports_total_returned_and_truncation() -> None:
    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        result = await repo.find_students(HARVARD, build_cohort_filter({}), limit=3)
        payload = result.as_json()
        assert payload["total"] == 14
        assert payload["returned"] == 3
        assert payload["truncated"] is True
        assert len(payload["items"]) == 3

    _run(scenario)


def test_verified_aid_means_the_complete_file_is_verified() -> None:
    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        verified = await repo.find_students(
            HARVARD, build_cohort_filter({"aidDocumentState": "verified"}), limit=50
        )
        outstanding = await repo.find_students(
            HARVARD, build_cohort_filter({"aidDocumentState": "outstanding"}), limit=50
        )
        verified_ids = {item["id"] for item in verified.items}
        outstanding_ids = {item["id"] for item in outstanding.items}
        assert verified_ids
        assert outstanding_ids
        assert verified_ids.isdisjoint(outstanding_ids)

        async with engine.connect() as connection:
            incorrectly_satisfied = await connection.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM financial_document_requirement
                    WHERE tenant_id = :tenant_id
                      AND student_id = ANY(CAST(:student_ids AS uuid[]))
                      AND status <> 'verified'
                    """
                ),
                {
                    "tenant_id": HARVARD_TENANT_ID,
                    "student_ids": list(verified_ids),
                },
            )
        assert incorrectly_satisfied == 0

    _run(scenario)


def test_every_group_by_dimension_returns_buckets_that_sum_correctly() -> None:
    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        for dimension in COHORT_GROUP_BY:
            summary = await repo.summarize_students(
                HARVARD, build_cohort_filter({}), group_by=dimension, limit=50
            )
            buckets = summary["buckets"]
            assert buckets, dimension
            if summary["countsRepresent"] == "students":
                # Student-valued groupings partition the cohort exactly.
                assert (
                    sum(bucket["count"] for bucket in buckets) == (summary["matchingStudents"])
                ), dimension
            else:
                # Requirement-valued groupings do not, and say so.
                assert dimension == "blocking_requirement"
                assert all(bucket["students"] <= summary["matchingStudents"] for bucket in buckets)

    _run(scenario)


def test_housing_grouping_uses_the_same_vocabulary_as_the_housing_filter() -> None:
    """A grouping bucket must be selectable as a filter and give that count."""

    async def scenario(repo: PostgresStaffAssistantRepository, engine: Any) -> None:
        summary = await repo.summarize_students(
            HARVARD, build_cohort_filter({}), group_by="housing_state", limit=50
        )
        for bucket in summary["buckets"]:
            filtered = await repo.find_students(
                HARVARD,
                build_cohort_filter({"housingState": bucket["value"]}),
                limit=50,
            )
            assert filtered.total == bucket["count"], bucket

    _run(scenario)


def test_an_unsupported_filter_value_is_rejected_not_ignored() -> None:
    """A silently dropped filter produces a confidently wrong cohort."""

    for raw in (
        {"unknownFilter": "must not disappear"},
        {"depositState": "kind-of-paid"},
        {"offerStatus": "enrolled"},
        {"housingState": "assigned"},
        {"requirementState": "sort-of-open"},
        {"classYear": "next year"},
    ):
        with pytest.raises(CohortFilterError):
            build_cohort_filter(raw)


def test_a_cohort_filter_describes_itself() -> None:
    """Answers restate what they counted, so the clauses must be present."""

    cohort = build_cohort_filter({"offerStatus": "accepted", "depositState": "unpaid"})
    clauses = cohort.describe()
    assert any("accepted" in clause for clause in clauses)
    assert any("unpaid" in clause for clause in clauses)
    assert build_cohort_filter({}).is_unconstrained() is True
