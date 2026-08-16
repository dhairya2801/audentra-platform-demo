"""Morning Brew against PostgreSQL: tenancy, agreement, and delta semantics.

The single most important property of this surface is that a briefing number
and the roster answer behind it are the same selection. These tests hold the
aggregate reads to the assistant's own `find_students`, filter by filter, so
"7 deposited students have overdue requirements" and "who are those 7?" can
never diverge.

Every expectation is derived from the database at run time. Nothing is
hard-coded to the seeded funnel, so the suite keeps its meaning when the
population changes.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from audentra.application.morning_brew import build_morning_brew
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.morning_brew import BREW_COHORTS, BREW_COHORTS_BY_KEY, BREW_DELTAS
from audentra.domain.student_cohort import DUE_SOON_HORIZON_DAYS, build_cohort_filter
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.morning_brew_repository import (
    PostgresMorningBrewRepository,
)
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


def _run(scenario: Any) -> None:
    url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AUDENTRA_TEST_DATABASE_URL is not configured")

    async def main() -> None:
        engine = create_database_engine(url)
        try:
            await reset_relational_data(engine, environment="test", completed_onboarding=True)
            await seed_relational_data(engine, environment="test")
            await scenario(
                PostgresMorningBrewRepository(engine),
                PostgresStaffAssistantRepository(engine),
                engine,
            )
        finally:
            await engine.dispose()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# Agreement with the assistant's cohort reads
# ---------------------------------------------------------------------------


def test_every_briefing_cohort_matches_the_roster_answer_behind_it() -> None:
    """The whole point of the shared vocabulary, asserted per cohort."""

    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        counts = await brew.count_cohorts(HARVARD, BREW_COHORTS)
        assert set(counts) == {cohort.key for cohort in BREW_COHORTS}
        for cohort in BREW_COHORTS:
            result = await assistant.find_students(HARVARD, cohort.to_filter(), limit=50)
            assert counts[cohort.key] == result.total, (
                f"{cohort.key}: briefing counted {counts[cohort.key]}, "
                f"findStudents reports {result.total}"
            )

    _run(scenario)


def test_the_funnel_partitions_without_gaps_or_overlap() -> None:
    """Deposited plus outstanding must equal accepted, or the tiles lie."""

    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        counts = await brew.count_cohorts(HARVARD, BREW_COHORTS)
        assert counts["deposit_paid"] + counts["deposit_outstanding"] == counts["offer_accepted"]
        assert counts["enrollment_ready"] + counts["deposited_blocked"] == counts["deposit_paid"]
        assert (
            counts["onboarding_complete"]
            + counts["onboarding_in_progress"]
            + counts["onboarding_not_started"]
            == counts["roster"]
        )
        assert counts["deposited_overdue"] <= counts["overdue"]
        assert counts["deposited_blocked"] <= counts["blocked"]

    _run(scenario)


def test_the_new_blocking_and_due_soon_dimensions_are_answerable_by_the_assistant() -> None:
    """Morning Brew added two filters; both must work through `findStudents`."""

    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        blocked = await assistant.find_students(
            HARVARD, build_cohort_filter({"hasOpenBlockingRequirement": True}), limit=50
        )
        unblocked = await assistant.find_students(
            HARVARD, build_cohort_filter({"hasOpenBlockingRequirement": False}), limit=50
        )
        everyone = await assistant.find_students(HARVARD, build_cohort_filter({}), limit=50)
        assert blocked.total + unblocked.total == everyone.total
        # The predicate is the same one `open_blocking_count` reports per student.
        for item in blocked.items:
            assert item["requirements"]["openBlocking"] > 0
        for item in unblocked.items:
            assert item["requirements"]["openBlocking"] == 0

        due_soon = await assistant.find_students(
            HARVARD, build_cohort_filter({"requirementState": "due_soon"}), limit=50
        )
        overdue = await assistant.find_students(
            HARVARD, build_cohort_filter({"requirementState": "overdue"}), limit=50
        )
        # "Due soon" is strictly in the future; it can never include an overdue-only student.
        horizon = datetime.now(UTC) + timedelta(days=DUE_SOON_HORIZON_DAYS)
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT DISTINCT jr.student_id
                    FROM student_requirement req
                    JOIN enrollment_journey jr
                      ON jr.id = req.journey_id AND jr.tenant_id = req.tenant_id
                    WHERE req.tenant_id = :tenant
                      AND req.retired_at IS NULL
                      AND req.status NOT IN ('completed', 'waived', 'not_applicable')
                      AND req.due_at IS NOT NULL
                      AND req.due_at >= NOW()
                      AND req.due_at < :horizon
                    """
                ),
                {"tenant": HARVARD_TENANT_ID, "horizon": horizon},
            )
            expected = {str(row[0]) for row in rows}
        assert {item["id"] for item in due_soon.items} == expected
        assert due_soon.total == len(expected)
        assert overdue.total >= 0

    _run(scenario)


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


def test_the_briefing_never_reaches_another_institution() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
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

        harvard = await build_morning_brew(HARVARD, brew)
        aster = await build_morning_brew(ASTER, brew)
        assert harvard["population"]["students"] == len(harvard_ids)
        assert aster["population"]["students"] == len(aster_ids)

        for item in harvard["attention"]:
            for student in item["detail"]["students"]:
                assert student["id"] in harvard_ids
                assert student["id"] not in aster_ids

        for cohort in BREW_COHORTS:
            sample = await brew.cohort_sample(HARVARD, cohort, limit=10)
            assert {row["id"] for row in sample} <= harvard_ids

    _run(scenario)


def test_a_non_staff_actor_is_refused_at_the_repository() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        student = AuthContext(
            tenant_id=HARVARD_TENANT_ID,
            student_id="",
            actor_id=HARVARD_STAFF_ID,
            actor_type="student",
        )
        for call in (
            brew.count_cohorts(student, BREW_COHORTS),
            brew.count_recent_activity(student),
            brew.deadline_runway(student),
            brew.open_student_requests(student),
            brew.staff_work_summary(student),
            brew.engagement_scan_freshness(student),
            brew.cohort_sample(student, BREW_COHORTS_BY_KEY["roster"]),
            brew.blocking_requirement_breakdown(student, BREW_COHORTS_BY_KEY["blocked"]),
        ):
            with pytest.raises(ApiError) as error:
                await call
            assert error.value.code == "STAFF_ACCESS_REQUIRED"

    _run(scenario)


# ---------------------------------------------------------------------------
# Delta semantics
# ---------------------------------------------------------------------------


def test_window_counts_match_a_direct_count_over_the_named_column() -> None:
    """Each delta is checked against the column its `basis` advertises."""

    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        hours = 24
        since = datetime.now(UTC) - timedelta(hours=hours)
        activity = await brew.count_recent_activity(HARVARD, hours=hours)
        checks = {
            "deposits_posted": (
                "SELECT COUNT(*) FROM payment_transaction WHERE tenant_id = :t"
                " AND type = 'enrollment_deposit' AND status = 'succeeded'"
                " AND created_at >= :since"
            ),
            "offers_accepted": (
                "SELECT COUNT(*) FROM admission_offer WHERE tenant_id = :t"
                " AND accepted_at IS NOT NULL AND accepted_at >= :since"
            ),
            "documents_submitted": (
                "SELECT COUNT(*) FROM document_record WHERE tenant_id = :t"
                " AND status <> 'placeholder' AND created_at >= :since"
            ),
            "requests_opened": (
                "SELECT COUNT(*) FROM student_inquiry WHERE tenant_id = :t AND created_at >= :since"
            ),
            "work_items_opened": (
                "SELECT COUNT(*) FROM staff_work_item WHERE tenant_id = :t AND created_at >= :since"
            ),
            "attention_flags": (
                "SELECT COUNT(*) FROM intervention_candidate WHERE tenant_id = :t"
                " AND created_at >= :since"
            ),
        }
        async with engine.connect() as connection:
            for key, sql in checks.items():
                row = await connection.execute(text(sql), {"t": HARVARD_TENANT_ID, "since": since})
                expected = int(row.scalar_one())
                actual = int(activity.get(key, {}).get("count", 0))
                assert actual == expected, f"{key}: expected {expected}, got {actual}"

    _run(scenario)


def test_a_window_with_no_activity_reports_nothing_rather_than_a_guess() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        # A window immediately after a fresh seed still contains the seed
        # writes, so advance the injected clock until the window starts after
        # every stored timestamp.
        quiet = PostgresMorningBrewRepository(
            engine, clock=lambda: datetime.now(UTC) + timedelta(days=3650)
        )
        activity = await quiet.count_recent_activity(HARVARD, hours=1)
        assert activity == {}

        payload = await build_morning_brew(HARVARD, quiet)
        for change in payload["changes"]:
            assert change["count"] == 0
            assert change["occurredAt"] is None
        assert "Nothing was recorded" in payload["synthesis"]["headline"]

    _run(scenario)


def test_every_declared_delta_appears_in_the_briefing() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        payload = await build_morning_brew(HARVARD, brew)
        assert {change["id"] for change in payload["changes"]} == {spec.key for spec in BREW_DELTAS}

    _run(scenario)


# ---------------------------------------------------------------------------
# Deadlines, requests, and blockers
# ---------------------------------------------------------------------------


def test_deadline_rows_collapse_per_requirement_and_bucket() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        deadlines = await brew.deadline_runway(HARVARD)
        seen = [(row["kind"], row["code"], row["bucket"]) for row in deadlines]
        assert len(seen) == len(set(seen)), "a requirement must appear once per bucket"
        for row in deadlines:
            assert row["students"] >= 1
            assert row["bucket"] in {"overdue", "today", "this_week", "this_month"}
            assert row["dueAt"] <= row["latestDueAt"]

    _run(scenario)


def test_overdue_deadline_headcount_never_exceeds_the_overdue_cohort() -> None:
    """A per-requirement row counts people once; the union cannot be smaller."""

    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        deadlines = await brew.deadline_runway(HARVARD)
        counts = await brew.count_cohorts(HARVARD, BREW_COHORTS)
        for row in deadlines:
            if row["bucket"] == "overdue" and row["kind"] == "requirement":
                assert row["students"] <= counts["overdue"]

    _run(scenario)


def test_open_requests_are_active_conversations_only() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        requests = await brew.open_student_requests(HARVARD)
        async with engine.connect() as connection:
            row = await connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM student_inquiry
                    WHERE tenant_id = :t AND archived_at IS NULL
                      AND status IN ('new', 'open', 'waiting_on_student')
                    """
                ),
                {"t": HARVARD_TENANT_ID},
            )
            assert requests["total"] == int(row.scalar_one())
        for item in requests["items"]:
            assert item["status"] in {"new", "open", "waiting_on_student"}
            assert item["waitingHours"] >= 0

    _run(scenario)


def test_blocker_breakdown_counts_requirements_and_distinct_students() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        cohort = BREW_COHORTS_BY_KEY["deposited_overdue"]
        breakdown = await brew.blocking_requirement_breakdown(HARVARD, cohort)
        matching = await assistant.find_students(HARVARD, cohort.to_filter(), limit=50)
        for row in breakdown:
            assert row["students"] <= matching.total
            assert row["requirements"] >= row["students"]
            assert row["overdue"] <= row["requirements"]

    _run(scenario)


# ---------------------------------------------------------------------------
# Whole-payload guarantees
# ---------------------------------------------------------------------------


def test_the_briefing_never_emits_a_synthetic_risk_value() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        import json

        payload = json.dumps(await build_morning_brew(HARVARD, brew)).lower()
        for forbidden in (
            "meltlikelihood",
            "recoverylikelihood",
            "riskband",
            "riskscore",
            "confidence",
            "modelversion",
        ):
            assert forbidden not in payload

    _run(scenario)


def test_every_attention_cohort_can_be_expanded_into_students() -> None:
    async def scenario(brew: Any, assistant: Any, engine: Any) -> None:
        payload = await build_morning_brew(HARVARD, brew)
        for item in payload["attention"]:
            cohort = build_cohort_filter(item["cohort"]["filter"])
            result = await assistant.find_students(HARVARD, cohort, limit=50)
            headline = int(item["impact"][0]["label"].split(" ")[0])
            assert result.total == headline, (
                f"{item['id']} claims {headline} students but the cohort holds {result.total}"
            )
            assert result.filter_clauses == item["cohort"]["clauses"]

    _run(scenario)
