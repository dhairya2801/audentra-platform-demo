"""The synthetic demo university, on the production PostgreSQL path.

Everything here runs against a migrated database seeded with
`DEMO_SEED_PROFILE=synthetic_university`, through the same
`PostgresPlatformService` the API serves. The population is imported once for
the module because importing it is a real seed, not a fixture stub.

What these tests are for, in order of importance:

1. Logging in as a student must not change that student. A demo whose state
   resets on sign-in cannot be used to test the product.
2. The Student Portal and Student Edward must give the same answer, for many
   students and many states — not for one curated one.
3. Staff cohort counts must equal the underlying rows. A dashboard that
   disagrees with `SELECT count(*)` is a dashboard nobody can trust.
4. The demo tenant must stay a tenant: no read may cross into it or out of it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.ports import ServiceCall
from audentra.domain.student_cohort import CohortFilter
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.auth_repository import PostgresDevelopmentAuth
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
)
from audentra.infrastructure.postgres.staff_assistant_repository import (
    PostgresStaffAssistantRepository,
)
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.seeding.relational import (
    ASTER_TENANT_ID,
    SYNTHETIC_STAFF_ID,
    seed_relational_data,
)
from audentra.infrastructure.seeding.synthetic_university import (
    SYNTHETIC_TENANT_ID,
    SYNTHETIC_TENANT_SLUG,
    verify_synthetic_invariants,
)
from audentra.integrations.assistant.tools import execute_tool_reads

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

_DONE = {"completed", "waived", "not_applicable"}

#: The generator's ten narrated students, one per interesting state.
PERSONA_REFS = tuple(f"SYN-{index:06d}" for index in range(10))


def _database_url() -> str:
    url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run the synthetic university integration")
    return url


def _service(engine: Any) -> PostgresPlatformService:
    portal = PostgresPortalRepository(engine)
    return PostgresPlatformService(
        PostgresRepositoryBundle(
            platform=PostgresPlatformRepository(engine),
            portal=portal,
            staff=PostgresStaffRepository(engine, portal),
            managed=PostgresManagedConfigurationRepository(engine),
        ),
        storage=cast(Any, None),
        ai=cast(Any, None),
        signed_documents=cast(Any, None),
        worker_token="test-document-worker-token",  # noqa: S106
    )


@dataclass(frozen=True, slots=True)
class _Harness:
    """Everything a scenario needs, all bound to one running event loop."""

    engine: Any
    service: PostgresPlatformService
    auth: PostgresDevelopmentAuth


@pytest.fixture(scope="module")
def seeded_url() -> str:
    """Import the population once, then hand every test the connection string.

    An asyncpg pool belongs to the loop that created it, and each test runs its
    own `asyncio.run`. Sharing the URL rather than the engine is what keeps
    that from turning into "attached to a different loop".
    """

    url = _database_url()

    async def prepare() -> None:
        engine = create_database_engine(url)
        try:
            await seed_relational_data(engine, environment="test", profile="synthetic_university")
        finally:
            await engine.dispose()

    asyncio.run(prepare())
    return url


def _run(url: str, scenario: Any) -> None:
    async def main() -> None:
        engine = create_database_engine(url)
        try:
            await scenario(
                _Harness(
                    engine=engine,
                    service=_service(engine),
                    auth=PostgresDevelopmentAuth(
                        engine,
                        environment="test",
                        staff_invitation_code="test-staff-invitation-code",
                    ),
                )
            )
        finally:
            await engine.dispose()

    asyncio.run(main())


async def _scalar(engine: Any, sql: str, **params: Any) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), params)).scalar_one()


async def _rows(engine: Any, sql: str, **params: Any) -> list[Mapping[str, Any]]:
    async with engine.connect() as connection:
        return [dict(row) for row in (await connection.execute(text(sql), params)).mappings().all()]


async def _auth_for(engine: Any, external_ref: str) -> AuthContext:
    row = (
        await _rows(
            engine,
            """
            SELECT s.id, s.person_id FROM student s
            WHERE s.tenant_id = CAST(:tenant AS uuid) AND s.external_ref = :ref
            """,
            tenant=SYNTHETIC_TENANT_ID,
            ref=external_ref,
        )
    )[0]
    return AuthContext(
        tenant_id=SYNTHETIC_TENANT_ID,
        student_id=str(row["id"]),
        actor_id=str(row["person_id"]),
        actor_type="student",
        tenant_slug=SYNTHETIC_TENANT_SLUG,
    )


async def _portal(
    service: PostgresPlatformService, auth: AuthContext, operation: str, **payload: Any
) -> Any:
    return await service.dispatch(
        ServiceCall(
            operation=operation,
            auth=auth,
            request_id=f"synthetic-{uuid4()}",
            payload=payload,
            idempotency_key=str(uuid4()),
        )
    )


async def _edward(
    service: PostgresPlatformService, auth: AuthContext, *tools: str
) -> dict[str, Mapping[str, Any]]:
    # Reaching into the private host is deliberate: the production wiring is
    # exactly what this suite exists to exercise.
    host = service._assistant_host(auth)
    execution = await execute_tool_reads(list(tools), host, timeout_seconds=20.0)
    for tool in tools:
        read = execution.reads[tool]
        assert read["status"] == "available", f"{tool} was {read['status']}: {read.get('reason')}"
    return {tool: cast(Mapping[str, Any], execution.reads[tool]["data"]) for tool in tools}


# ---------------------------------------------------------------------------
# The import itself
# ---------------------------------------------------------------------------


def test_the_population_lands_in_the_demo_tenant_only(seeded_url: str) -> None:
    async def scenario(harness: _Harness) -> None:
        demo = await _scalar(
            harness.engine,
            "SELECT count(*) FROM student WHERE tenant_id = CAST(:t AS uuid)",
            t=SYNTHETIC_TENANT_ID,
        )
        aster = await _scalar(
            harness.engine,
            "SELECT count(*) FROM student WHERE tenant_id = CAST(:t AS uuid)",
            t=ASTER_TENANT_ID,
        )
        assert demo > 2_000, "the demo campus should hold the synthetic population"
        # The compact fixture is a fixture: fourteen funnel students plus the
        # primary identity and the two named extras. If the synthetic import
        # ever leaks into Aster, the fast suite starts lying.
        assert aster < 100, f"the compact tenant grew to {aster} students"

    _run(seeded_url, scenario)


def test_the_import_is_idempotent(seeded_url: str) -> None:
    """Re-seeding converges rather than duplicating."""

    async def scenario(harness: _Harness) -> None:
        before = await _scalar(
            harness.engine,
            "SELECT count(*) FROM student WHERE tenant_id = CAST(:t AS uuid)",
            t=SYNTHETIC_TENANT_ID,
        )
        await seed_relational_data(
            harness.engine, environment="test", profile="synthetic_university"
        )
        after = await _scalar(
            harness.engine,
            "SELECT count(*) FROM student WHERE tenant_id = CAST(:t AS uuid)",
            t=SYNTHETIC_TENANT_ID,
        )
        assert before == after

    _run(seeded_url, scenario)


def test_domain_invariants_hold_over_the_settled_population(seeded_url: str) -> None:
    async def scenario(harness: _Harness) -> None:
        async with harness.engine.connect() as connection:
            violations = await verify_synthetic_invariants(
                connection, tenant_id=SYNTHETIC_TENANT_ID
            )
        assert violations == []

    _run(seeded_url, scenario)


def test_every_imported_student_has_a_coherent_spine(seeded_url: str) -> None:
    """Person, offer, journey, onboarding and requirements, all or nothing."""

    async def scenario(harness: _Harness) -> None:
        orphans = await _scalar(
            harness.engine,
            """
            SELECT count(*) FROM student s
            WHERE s.tenant_id = CAST(:t AS uuid)
              AND (
                NOT EXISTS (SELECT 1 FROM admission_offer o WHERE o.student_id = s.id)
                OR NOT EXISTS (SELECT 1 FROM enrollment_journey j WHERE j.student_id = s.id)
                OR NOT EXISTS (SELECT 1 FROM student_onboarding b WHERE b.student_id = s.id)
                OR NOT EXISTS (SELECT 1 FROM student_profile p WHERE p.student_id = s.id)
              )
            """,
            t=SYNTHETIC_TENANT_ID,
        )
        assert orphans == 0

        # Nine canonical requirements per journey, including FERPA, none retired.
        odd = await _scalar(
            harness.engine,
            """
            SELECT count(*) FROM (
              SELECT j.id, count(r.id) AS total
              FROM enrollment_journey j
              LEFT JOIN student_requirement r
                ON r.journey_id = j.id AND r.retired_at IS NULL
              WHERE j.tenant_id = CAST(:t AS uuid)
              GROUP BY j.id
            ) counts WHERE total <> 9
            """,
            t=SYNTHETIC_TENANT_ID,
        )
        assert odd == 0

    _run(seeded_url, scenario)


def test_the_population_actually_spans_the_interesting_states(seeded_url: str) -> None:
    """A three-thousand-student demo that is uniformly "ready" is a worse demo
    than fourteen hand-written students."""

    async def scenario(harness: _Harness) -> None:
        rows = await _rows(
            harness.engine,
            """
            SELECT definition.code, requirement.status, count(*) AS total
            FROM student_requirement requirement
            JOIN requirement_definition_version definition
              ON definition.id = requirement.requirement_definition_version_id
            WHERE requirement.tenant_id = CAST(:t AS uuid) AND requirement.retired_at IS NULL
            GROUP BY 1, 2
            """,
            t=SYNTHETIC_TENANT_ID,
        )
        by_code: dict[str, set[str]] = {}
        for row in rows:
            by_code.setdefault(str(row["code"]), set()).add(str(row["status"]))

        assert {"completed", "under_review", "rejected"} <= by_code["official_transcript"]
        assert "blocked" in by_code["housing_preference"]
        assert {"completed", "in_progress", "ready"} <= by_code["enrollment_deposit"]
        assert {"completed", "in_progress"} <= by_code["financial_aid_verification"]

        deposits = await _scalar(
            harness.engine,
            """
            SELECT count(*) FROM payment_transaction
            WHERE tenant_id = CAST(:t AS uuid) AND status = 'succeeded'
            """,
            t=SYNTHETIC_TENANT_ID,
        )
        assert deposits > 500

    _run(seeded_url, scenario)


# ---------------------------------------------------------------------------
# Login persistence — the regression this whole demo exists to make testable
# ---------------------------------------------------------------------------


async def _state_digest(service: PostgresPlatformService, auth: AuthContext) -> str:
    requirements = await _portal(service, auth, "student.list_requirements")
    payments = await _portal(service, auth, "student.list_payments")
    onboarding = await _portal(service, auth, "student.get_onboarding")
    documents = await service.repository.portal.get_student_documents(auth)
    return repr(
        (
            sorted((item["code"], item["status"]) for item in requirements["items"]),
            sorted(
                (item["type"], item["status"], item["amountCents"]) for item in payments["items"]
            ),
            (onboarding["status"], onboarding["currentStep"], sorted(onboarding["completedSteps"])),
            sorted((item["id"], item["status"]) for item in documents["items"]),
        )
    )


@pytest.mark.parametrize("external_ref", ["SYN-000000", "SYN-000006", "SYN-000009"])
def test_signing_in_again_does_not_reset_the_student(seeded_url: str, external_ref: str) -> None:
    async def scenario(harness: _Harness) -> None:
        first = await harness.auth.demo_student_by_reference(
            SYNTHETIC_TENANT_ID, SYNTHETIC_TENANT_SLUG, external_ref
        )
        before = await _state_digest(harness.service, first.context)

        # Sign in again — twice, and by both spellings of the identifier.
        await harness.auth.demo_student_by_reference(
            SYNTHETIC_TENANT_ID, SYNTHETIC_TENANT_SLUG, external_ref
        )
        second = await harness.auth.demo_student_by_reference(
            SYNTHETIC_TENANT_ID, SYNTHETIC_TENANT_SLUG, first.context.student_id
        )
        assert second.context.student_id == first.context.student_id
        after = await _state_digest(harness.service, second.context)
        assert before == after

    _run(seeded_url, scenario)


def test_two_students_do_not_share_state(seeded_url: str) -> None:
    async def scenario(harness: _Harness) -> None:
        clean = await _auth_for(harness.engine, "SYN-000000")
        rejected = await _auth_for(harness.engine, "SYN-000003")
        assert await _state_digest(harness.service, clean) != await _state_digest(
            harness.service, rejected
        )

    _run(seeded_url, scenario)


# ---------------------------------------------------------------------------
# Portal / Edward parity across the population
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("external_ref", PERSONA_REFS)
def test_edward_reads_the_same_checklist_the_portal_renders(
    seeded_url: str, external_ref: str
) -> None:
    async def scenario(harness: _Harness) -> None:
        auth = await _auth_for(harness.engine, external_ref)
        portal = await _portal(harness.service, auth, "student.list_requirements")
        reads = await _edward(harness.service, auth, "getOnboardingChecklist")
        edward = reads["getOnboardingChecklist"]
        assert {item["code"]: item["status"] for item in edward["items"]} == {
            item["code"]: item["status"] for item in portal["items"]
        }
        assert edward["openCount"] == sum(
            1 for item in portal["items"] if item["status"] not in _DONE
        )

    _run(seeded_url, scenario)


@pytest.mark.parametrize("external_ref", PERSONA_REFS)
def test_edward_reads_the_same_deposit_the_payments_page_shows(
    seeded_url: str, external_ref: str
) -> None:
    async def scenario(harness: _Harness) -> None:
        auth = await _auth_for(harness.engine, external_ref)
        payments = await _portal(harness.service, auth, "student.list_payments")
        paid = any(
            item["type"] == "enrollment_deposit" and item["status"] == "succeeded"
            for item in payments["items"]
        )
        reads = await _edward(
            harness.service,
            auth,
            "getStudentAccountSummary",
            "getEnrollmentHolds",
            "getEnrollmentState",
        )
        account = reads["getStudentAccountSummary"]
        assert account["depositPaid"] is paid
        assert account["depositState"]["known"] is True
        assert reads["getEnrollmentHolds"]["depositState"] == account["depositState"]
        assert reads["getEnrollmentState"]["depositState"] == account["depositState"]

    _run(seeded_url, scenario)


@pytest.mark.parametrize("external_ref", PERSONA_REFS)
def test_edward_reads_the_same_documents_and_aid_the_portal_shows(
    seeded_url: str, external_ref: str
) -> None:
    async def scenario(harness: _Harness) -> None:
        auth = await _auth_for(harness.engine, external_ref)
        documents = await harness.service.repository.portal.get_student_documents(auth)
        financials = await _portal(harness.service, auth, "student.get_financials")
        reads = await _edward(
            harness.service,
            auth,
            "getDocumentStatuses",
            "getFinancialAidStatus",
            "getFinancialAidSummary",
            "getAcademicStanding",
        )
        assert {item["id"]: item["status"] for item in reads["getDocumentStatuses"]["items"]} == {
            item["id"]: item["status"] for item in documents["items"]
        }
        assert {
            item["code"]: item["status"]
            for item in reads["getFinancialAidStatus"]["requiredDocuments"]
        } == {item["code"]: item["status"] for item in financials["requiredDocuments"]}
        summary = reads["getFinancialAidSummary"]
        for key in (
            "costOfAttendanceCents",
            "acceptedAidCents",
            "pendingAidCents",
            "remainingBalanceCents",
        ):
            assert summary[key] == financials[key]
        sap = reads["getAcademicStanding"]["satisfactoryAcademicProgress"]
        assert sap["cumulativeGpa"] == financials["sap"]["cumulativeGpa"]
        assert sap["status"] == financials["sap"]["status"]

    _run(seeded_url, scenario)


def test_edward_and_the_portal_agree_across_a_random_sample(seeded_url: str) -> None:
    """Personas are curated. This walks students nobody chose.

    The sample is taken by a deterministic ordering rather than at random so a
    failure is reproducible.
    """

    async def scenario(harness: _Harness) -> None:
        refs = [
            str(row["external_ref"])
            for row in await _rows(
                harness.engine,
                """
                SELECT external_ref FROM student
                WHERE tenant_id = CAST(:t AS uuid) AND external_ref IS NOT NULL
                ORDER BY md5(external_ref)
                LIMIT 25
                """,
                t=SYNTHETIC_TENANT_ID,
            )
        ]
        assert len(refs) == 25
        for ref in refs:
            auth = await _auth_for(harness.engine, ref)
            portal = await _portal(harness.service, auth, "student.list_requirements")
            state = await _edward(
                harness.service, auth, "getEnrollmentState", "getOnboardingChecklist"
            )
            checklist = state["getOnboardingChecklist"]["items"]
            assert {item["code"]: item["status"] for item in checklist} == {
                item["code"]: item["status"] for item in portal["items"]
            }, f"{ref} disagrees"
            dashboard = await _portal(harness.service, auth, "student.get_dashboard")
            admission = state["getEnrollmentState"]["admission"]
            assert admission["programName"] == dashboard["offer"]["programName"], ref
            assert admission["offerStatus"] == dashboard["offer"]["status"], ref

    _run(seeded_url, scenario)


def test_a_blocked_housing_step_explains_itself(seeded_url: str) -> None:
    """The cross-domain question: "why can't I apply for housing?"

    Edward should be able to answer it from the requirement graph rather than
    from prose, so the blocked step and the unmet deposit must both be
    readable in the same breath.
    """

    async def scenario(harness: _Harness) -> None:
        ref = str(
            (
                await _rows(
                    harness.engine,
                    """
                    SELECT s.external_ref
                    FROM student s
                    JOIN enrollment_journey j ON j.student_id = s.id
                    JOIN student_requirement r ON r.journey_id = j.id AND r.retired_at IS NULL
                    JOIN requirement_definition_version d
                      ON d.id = r.requirement_definition_version_id
                    WHERE s.tenant_id = CAST(:t AS uuid)
                      AND d.code = 'housing_preference' AND r.status = 'blocked'
                    ORDER BY s.external_ref LIMIT 1
                    """,
                    t=SYNTHETIC_TENANT_ID,
                )
            )[0]["external_ref"]
        )
        auth = await _auth_for(harness.engine, ref)
        reads = await _edward(
            harness.service, auth, "getOnboardingChecklist", "getStudentAccountSummary"
        )
        statuses = {
            item["code"]: item["status"] for item in reads["getOnboardingChecklist"]["items"]
        }
        assert statuses["housing_preference"] == "blocked"
        # The prerequisite is the deposit, and the deposit is genuinely open.
        assert statuses["enrollment_deposit"] not in _DONE
        assert reads["getStudentAccountSummary"]["depositPaid"] is False

    _run(seeded_url, scenario)


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------


def _staff_auth() -> AuthContext:
    return AuthContext(
        tenant_id=SYNTHETIC_TENANT_ID,
        student_id="00000000-0000-0000-0000-000000000000",
        actor_id=SYNTHETIC_STAFF_ID,
        actor_type="staff",
        tenant_slug=SYNTHETIC_TENANT_SLUG,
    )


def test_staff_cohort_counts_match_the_underlying_rows(seeded_url: str) -> None:
    """The list, the total, and `SELECT count(*)` must be one number."""

    async def scenario(harness: _Harness) -> None:
        repository = PostgresStaffAssistantRepository(harness.engine)
        auth = _staff_auth()

        everyone = await repository.find_students(auth, CohortFilter(), limit=5)
        expected = await _scalar(
            harness.engine,
            "SELECT count(*) FROM student WHERE tenant_id = CAST(:t AS uuid)",
            t=SYNTHETIC_TENANT_ID,
        )
        assert everyone.total == expected
        assert len(everyone.items) == 5, "the page is bounded, the total is not"

        deposited = await repository.find_students(
            auth, CohortFilter(deposit_state="paid"), limit=1
        )
        deposited_rows = await _scalar(
            harness.engine,
            """
            SELECT count(DISTINCT student_id) FROM payment_transaction
            WHERE tenant_id = CAST(:t AS uuid)
              AND type = 'enrollment_deposit' AND status = 'succeeded'
            """,
            t=SYNTHETIC_TENANT_ID,
        )
        assert deposited.total == deposited_rows

        grouped = await repository.summarize_students(
            auth, CohortFilter(), group_by="deposit_state"
        )
        buckets = {str(row["value"]): int(row["count"]) for row in grouped["buckets"]}
        assert grouped["matchingStudents"] == expected
        assert buckets.get("paid") == deposited_rows
        assert sum(buckets.values()) == expected

    _run(seeded_url, scenario)


def test_staff_search_finds_a_synthetic_student_by_name(seeded_url: str) -> None:
    async def scenario(harness: _Harness) -> None:
        repository = PostgresStaffAssistantRepository(harness.engine)
        found = await repository.search_students(_staff_auth(), query="Wren Halloway", limit=5)
        assert found["items"], "the roster search cannot see the synthetic population"
        assert any(item["name"] == "Wren Halloway" for item in found["items"])

    _run(seeded_url, scenario)


@pytest.mark.parametrize("external_ref", ["SYN-000000", "SYN-000006", "SYN-000009"])
def test_staff_edward_reads_the_same_student_the_portal_does(
    seeded_url: str, external_ref: str
) -> None:
    """Staff Edward's individual read and the student's own portal are two
    views of one record, so every fact they share must be the same fact."""

    async def scenario(harness: _Harness) -> None:
        student = await _auth_for(harness.engine, external_ref)
        repository = PostgresStaffAssistantRepository(harness.engine)
        overview = await repository.get_student_overview(_staff_auth(), student.student_id)
        assert overview is not None

        requirements = await _portal(harness.service, student, "student.list_requirements")
        payments = await _portal(harness.service, student, "student.list_payments")
        onboarding = await _portal(harness.service, student, "student.get_onboarding")
        dashboard = await _portal(harness.service, student, "student.get_dashboard")

        assert overview["onboardingStatus"] == onboarding["status"]
        assert overview["programName"] == dashboard["offer"]["programName"]
        assert overview["offer"]["status"] == dashboard["offer"]["status"]
        assert overview["offer"]["depositPaid"] is any(
            item["type"] == "enrollment_deposit" and item["status"] == "succeeded"
            for item in payments["items"]
        )
        assert overview["requirements"]["total"] == len(requirements["items"])
        assert overview["requirements"]["completed"] == sum(
            1 for item in requirements["items"] if item["status"] in _DONE
        )

    _run(seeded_url, scenario)


def test_a_staff_overview_stops_at_the_tenant_boundary(seeded_url: str) -> None:
    async def scenario(harness: _Harness) -> None:
        student = await _auth_for(harness.engine, "SYN-000000")
        repository = PostgresStaffAssistantRepository(harness.engine)
        foreign = AuthContext(
            tenant_id=ASTER_TENANT_ID,
            student_id="00000000-0000-0000-0000-000000000000",
            actor_id="00000000-0000-7000-8000-000000000901",
            actor_type="staff",
            tenant_slug="aster",
        )
        assert await repository.get_student_overview(foreign, student.student_id) is None

    _run(seeded_url, scenario)


def test_staff_work_is_assigned_inside_the_tenant(seeded_url: str) -> None:
    async def scenario(harness: _Harness) -> None:
        leaks = await _scalar(
            harness.engine,
            """
            SELECT count(*) FROM staff_work_item item
            JOIN staff_member staff ON staff.id = item.assignee_id
            WHERE item.tenant_id = CAST(:t AS uuid) AND staff.tenant_id <> item.tenant_id
            """,
            t=SYNTHETIC_TENANT_ID,
        )
        assert leaks == 0
        distinct_assignees = await _scalar(
            harness.engine,
            """
            SELECT count(DISTINCT assignee_id) FROM staff_work_item
            WHERE tenant_id = CAST(:t AS uuid) AND assignee_id IS NOT NULL
            """,
            t=SYNTHETIC_TENANT_ID,
        )
        assert distinct_assignees > 1, "every work item landed on one person"

    _run(seeded_url, scenario)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_a_demo_student_reference_does_not_resolve_in_another_tenant(seeded_url: str) -> None:
    async def scenario(harness: _Harness) -> None:
        resolved = await harness.auth.demo_student_by_reference(
            SYNTHETIC_TENANT_ID, SYNTHETIC_TENANT_SLUG, "SYN-000000"
        )
        assert resolved.external_ref == "SYN-000000"

        for reference in ("SYN-000000", resolved.context.student_id):
            with pytest.raises(ApiError) as raised:
                await harness.auth.demo_student_by_reference(ASTER_TENANT_ID, "aster", reference)
            assert raised.value.status_code == 404

    _run(seeded_url, scenario)


@pytest.mark.parametrize(
    "reference",
    ["", "   ", "SYN-999999", "not-a-student", "x" * 200, "'; DROP TABLE student; --"],
)
def test_an_unresolvable_reference_is_a_clean_not_found(seeded_url: str, reference: str) -> None:
    async def scenario(harness: _Harness) -> None:
        with pytest.raises(ApiError) as raised:
            await harness.auth.demo_student_by_reference(
                SYNTHETIC_TENANT_ID, SYNTHETIC_TENANT_SLUG, reference
            )
        assert raised.value.status_code == 404
        assert raised.value.code == "DEMO_STUDENT_NOT_FOUND"

    _run(seeded_url, scenario)


def test_no_portal_read_crosses_the_tenant_boundary(seeded_url: str) -> None:
    """A demo-campus student id presented with the compact tenant's identity
    must not return the demo campus student's records."""

    async def scenario(harness: _Harness) -> None:
        demo = await _auth_for(harness.engine, "SYN-000000")
        crossed = AuthContext(
            tenant_id=ASTER_TENANT_ID,
            student_id=demo.student_id,
            actor_id=demo.actor_id,
            actor_type="student",
            tenant_slug="aster",
        )
        with pytest.raises(ApiError):
            await _portal(harness.service, crossed, "student.get_dashboard")

    _run(seeded_url, scenario)


# ---------------------------------------------------------------------------
# Query performance
# ---------------------------------------------------------------------------


def test_the_reads_the_demo_depends_on_are_indexed(seeded_url: str) -> None:
    """Sequential scans over three thousand students are survivable; over the
    student table on every sign-in they are not."""

    async def scenario(harness: _Harness) -> None:
        plan = await _explain(
            harness.engine,
            """
            SELECT id FROM student
            WHERE tenant_id = CAST(:t AS uuid) AND external_ref = :ref
            """,
            t=SYNTHETIC_TENANT_ID,
            ref="SYN-001234",
        )
        assert "Index Scan" in plan or "Index Only Scan" in plan, plan

    _run(seeded_url, scenario)


async def _explain(engine: Any, sql: str, **params: Any) -> str:
    async with engine.connect() as connection:
        rows: Sequence[Any] = (
            (await connection.execute(text(f"EXPLAIN {sql}"), params)).scalars().all()
        )
    return "\n".join(str(row) for row in rows)
