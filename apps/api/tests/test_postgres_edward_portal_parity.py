"""Portal/Edward parity on the production PostgreSQL path.

The in-memory agreement suite (`test_portal_edward_state_agreement`) proved
the two views agree over fixture state. It could not prove they agree over the
state production actually serves, and they did not: the Postgres dashboard
projection carries no `depositPaid` field, so the assistant's deposit
derivation — which looked for exactly that field — concluded "unpaid" for
every student, including students whose Payments page showed a posted receipt.
The eval personas hid it by injecting the missing field into the fixture.

These tests read through `PostgresPlatformService`, the same object the API
serves, against a migrated and seeded database. For each domain the portal
endpoint's answer and Edward's tool answer must be the same answer.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

import pytest

from audentra.core.auth import AuthContext
from audentra.core.ports import ServiceCall
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
)
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.seeding.relational import (
    HARVARD_OFFER_ID,
    HARVARD_PERSON_ID,
    HARVARD_STUDENT_ID,
    HARVARD_TENANT_ID,
    reset_relational_data,
    seed_relational_data,
)
from audentra.integrations.assistant.tools import execute_tool_reads

pytestmark = pytest.mark.integration

AUTH = AuthContext(
    tenant_id=HARVARD_TENANT_ID,
    student_id=HARVARD_STUDENT_ID,
    actor_id=HARVARD_PERSON_ID,
    actor_type="student",
)

_DONE = {"completed", "waived", "not_applicable"}


def _database_url() -> str:
    url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AUDENTRA_TEST_DATABASE_URL is not configured")
    return url


def _service(engine: Any) -> PostgresPlatformService:
    portal = PostgresPortalRepository(engine)
    platform = PostgresPlatformRepository(engine)
    return PostgresPlatformService(
        PostgresRepositoryBundle(
            platform=platform,
            portal=portal,
            staff=PostgresStaffRepository(engine, portal),
            managed=PostgresManagedConfigurationRepository(engine),
        ),
        # These tests exercise reads only; no upload, model call, or signed
        # document generation is reachable from a portal or assistant read.
        storage=cast(Any, None),
        ai=cast(Any, None),
        signed_documents=cast(Any, None),
        worker_token="test-document-worker-token",  # noqa: S106
    )


async def _portal(service: PostgresPlatformService, operation: str, **payload: Any) -> Any:
    return await service.dispatch(
        ServiceCall(
            operation=operation,
            auth=AUTH,
            request_id=f"parity-{uuid4()}",
            payload=payload,
            idempotency_key=str(uuid4()),
        )
    )


async def _edward(service: PostgresPlatformService, *tools: str) -> dict[str, Mapping[str, Any]]:
    """Run Edward's tools through the same host the assistant pipeline uses."""

    # Reaching into the private host is deliberate: the production wiring
    # is exactly what this suite exists to exercise.
    host = service._assistant_host(AUTH)
    execution = await execute_tool_reads(list(tools), host, timeout_seconds=15.0)
    for tool in tools:
        read = execution.reads[tool]
        assert read["status"] == "available", f"{tool} was {read['status']}: {read.get('reason')}"
    return {tool: cast(Mapping[str, Any], execution.reads[tool]["data"]) for tool in tools}


def _run(scenario: Any) -> None:
    url = _database_url()

    async def main() -> None:
        engine = create_database_engine(url)
        try:
            await reset_relational_data(engine, environment="test", completed_onboarding=True)
            await seed_relational_data(engine, environment="test")
            await scenario(_service(engine))
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_deposit_state_agrees_with_the_payments_page() -> None:
    """The regression this suite exists for.

    The seeded primary student has a posted enrollment deposit. Before the
    shared derivation, Edward reported it unpaid and manufactured a blocker,
    a registration gate, and a deadline out of that mistake.
    """

    async def scenario(service: PostgresPlatformService) -> None:
        await _assert_deposit_parity(service, expected_paid=False)

        # Pay it through the same portal operation the Payments page calls,
        # then re-ask. Both surfaces must move together.
        await _portal(service, "student.create_deposit", offerId=HARVARD_OFFER_ID)
        await _assert_deposit_parity(service, expected_paid=True)

    _run(scenario)


async def _assert_deposit_parity(service: PostgresPlatformService, *, expected_paid: bool) -> None:
    payments = await _portal(service, "student.list_payments")
    posted = [
        item
        for item in payments["items"]
        if item["type"] == "enrollment_deposit" and item["status"] == "succeeded"
    ]
    financials = await _portal(service, "student.get_financials")
    schedule = next(row for row in financials["paymentSchedule"] if row["kind"] == "deposit")
    # Establish what the portal itself shows before asserting on Edward.
    assert bool(posted) is expected_paid, "the payments ledger is not in the expected state"
    assert (schedule["status"] == "paid") is expected_paid, (
        "the two portal surfaces already disagree about the deposit"
    )

    reads = await _edward(
        service, "getStudentAccountSummary", "getEnrollmentHolds", "getEnrollmentState"
    )
    account = reads["getStudentAccountSummary"]
    assert account["depositPaid"] is expected_paid
    assert account["depositState"]["known"] is True

    # Every tool that embeds deposit state must embed the same one.
    assert reads["getEnrollmentHolds"]["depositState"] == account["depositState"]
    assert reads["getEnrollmentState"]["depositState"] == account["depositState"]

    blocker_codes = {blocker["code"] for blocker in reads["getEnrollmentHolds"]["derivedBlockers"]}
    assert ("enrollment_deposit_posted" in blocker_codes) is not expected_paid


def test_deadlines_do_not_invent_a_paid_deposit_deadline() -> None:
    async def scenario(service: PostgresPlatformService) -> None:
        async def deposit_deadlines() -> list[Any]:
            reads = await _edward(service, "getStudentDeadlines")
            return [
                item for item in reads["getStudentDeadlines"]["items"] if item["kind"] == "deposit"
            ]

        assert await deposit_deadlines(), "an unpaid deposit should carry its deadline"
        await _portal(service, "student.create_deposit", offerId=HARVARD_OFFER_ID)
        assert await deposit_deadlines() == [], (
            "a posted deposit must not keep generating a deadline"
        )

    _run(scenario)


def test_checklist_agrees_with_the_enrollment_page() -> None:
    async def scenario(service: PostgresPlatformService) -> None:
        portal = await _portal(service, "student.list_requirements")
        reads = await _edward(service, "getOnboardingChecklist")
        edward = reads["getOnboardingChecklist"]
        assert {item["code"]: item["status"] for item in edward["items"]} == {
            item["code"]: item["status"] for item in portal["items"]
        }
        assert {item["code"]: item["dueAt"] for item in edward["items"]} == {
            item["code"]: item["dueAt"] for item in portal["items"]
        }
        assert edward["openCount"] == sum(
            1 for item in portal["items"] if item["status"] not in _DONE
        )

    _run(scenario)


def test_documents_agree_with_the_documents_page() -> None:
    async def scenario(service: PostgresPlatformService) -> None:
        # The endpoint also lazily materializes signed onboarding PDFs, which
        # needs object storage; the read underneath it is what both surfaces
        # render and what this test compares.
        portal = await service.repository.portal.get_student_documents(AUTH)
        reads = await _edward(service, "getDocumentStatuses")
        assert {item["id"]: item["status"] for item in reads["getDocumentStatuses"]["items"]} == {
            item["id"]: item["status"] for item in portal["items"]
        }

    _run(scenario)


def test_enrollment_state_agrees_with_the_dashboard_strip() -> None:
    """Program, term, campus, class year and progress, as the header shows."""

    async def scenario(service: PostgresPlatformService) -> None:
        dashboard = await _portal(service, "student.get_dashboard")
        reads = await _edward(service, "getEnrollmentState")
        state = reads["getEnrollmentState"]
        assert state["admission"]["programName"] == dashboard["offer"]["programName"]
        assert state["admission"]["termName"] == dashboard["offer"]["termName"]
        assert state["admission"]["campusName"] == dashboard["offer"]["campusName"]
        assert state["admission"]["offerStatus"] == dashboard["offer"]["status"]
        assert state["student"]["classYear"] == dashboard["student"]["classYear"]
        assert state["journey"]["completionPercent"] == dashboard["journey"]["completionPercent"]

        onboarding = await _portal(service, "student.get_onboarding")
        assert state["onboarding"]["status"] == onboarding["status"]
        assert state["onboarding"]["currentStep"] == onboarding["currentStep"]

    _run(scenario)


def test_onboarding_answers_agree_with_the_onboarding_record() -> None:
    """Facts the student entered themselves are readable, not just their step."""

    async def scenario(service: PostgresPlatformService) -> None:
        portal = await _portal(service, "student.get_onboarding")
        data = portal["data"]
        reads = await _edward(service, "getOnboardingResponses")
        responses = reads["getOnboardingResponses"]
        assert responses["citizenshipStatus"] == data.get("citizenshipStatus")
        assert responses["residencyStatus"] == data.get("residencyStatus")
        assert len(responses["emergencyContacts"]) == len(data.get("emergencyContacts") or [])
        if data.get("streetAddress"):
            assert data["streetAddress"] in (responses["mailingAddress"] or "")
        # A completed onboarding record must carry the answers completing it
        # implies; an empty payload here means the seed is not portal-realistic.
        assert portal["status"] != "completed" or responses["signature"]["recorded"] is True

    _run(scenario)


def test_financials_agree_with_the_financials_page() -> None:
    async def scenario(service: PostgresPlatformService) -> None:
        portal = await _portal(service, "student.get_financials")
        reads = await _edward(
            service, "getFinancialAidStatus", "getFinancialAidSummary", "getAcademicStanding"
        )
        status = reads["getFinancialAidStatus"]
        assert {item["code"]: item["status"] for item in status["requiredDocuments"]} == {
            item["code"]: item["status"] for item in portal["requiredDocuments"]
        }
        summary = reads["getFinancialAidSummary"]
        for key in (
            "costOfAttendanceCents",
            "acceptedAidCents",
            "pendingAidCents",
            "remainingBalanceCents",
        ):
            assert summary[key] == portal[key]
        sap = reads["getAcademicStanding"]["satisfactoryAcademicProgress"]
        assert sap["cumulativeGpa"] == portal["sap"]["cumulativeGpa"]
        assert sap["status"] == portal["sap"]["status"]

    _run(scenario)


def test_housing_plan_agrees_with_the_housing_record() -> None:
    async def scenario(service: PostgresPlatformService) -> None:
        portal = await _portal(service, "student.get_housing_plan")
        reads = await _edward(service, "getStudentHousingStatus", "getHousingOptions")
        housing = reads["getStudentHousingStatus"]
        assert housing["preference"] == portal["preference"]
        assert housing["residenceOption"] == portal["residenceOption"]
        assert {item["value"] for item in reads["getHousingOptions"]["residences"]} == {
            item["value"] for item in portal["residences"]
        }

    _run(scenario)


def test_profile_agrees_with_the_profile_page() -> None:
    async def scenario(service: PostgresPlatformService) -> None:
        portal = await _portal(service, "student.get_profile")
        reads = await _edward(service, "getStudentProfile")
        profile = reads["getStudentProfile"]
        for key in ("preferredName", "pronouns", "mobilePhone", "communicationPreference"):
            assert profile[key] == portal.get(key)

    _run(scenario)


def test_messages_and_appointments_agree() -> None:
    async def scenario(service: PostgresPlatformService) -> None:
        messages = await _portal(service, "student.list_messages")
        appointments = await _portal(service, "student.list_appointments")
        reads = await _edward(service, "getStudentMessages", "getStudentAppointments")
        unread = sum(1 for item in messages["items"] if item["readAt"] is None)
        assert reads["getStudentMessages"]["unreadCount"] == unread
        assert reads["getStudentAppointments"]["total"] == len(appointments["items"])

    _run(scenario)


def test_every_tool_reads_successfully_on_the_production_path() -> None:
    """Core reads work; optional adapters are explicitly absent in this fixture."""

    from audentra.integrations.assistant.tools import _TOOL_IMPLEMENTATIONS

    async def scenario(service: PostgresPlatformService) -> None:
        host = service._assistant_host(AUTH)
        execution = await execute_tool_reads(
            list(_TOOL_IMPLEMENTATIONS), host, timeout_seconds=20.0
        )
        unavailable = {
            tool: read.get("reason")
            for tool, read in execution.reads.items()
            if read["status"] != "available"
        }
        from audentra.integrations.assistant.university_catalog import UNIVERSITY_TOOLS

        # This deliberately minimal Harvard fixture installs neither advising
        # nor the institutional corpus nor a v3 world. University runtime
        # integration tests exercise those reads against an imported v3 tenant.
        assert unavailable == {
            name: "not_supported"
            for name in ("getStudentAdvising", "getInstitutionalPolicies", *UNIVERSITY_TOOLS)
        }

    _run(scenario)
