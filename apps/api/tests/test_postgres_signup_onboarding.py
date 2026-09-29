"""Fresh signup must remain usable after the demo seed's offer dates expire."""

from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from datetime import date, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from audentra.core.assistant_execution import DEFAULT_ASSISTANT_EXECUTION
from audentra.core.errors import ConflictError, NotFoundError
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.auth_repository import PostgresDevelopmentAuth
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
)
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.postgres.university_repository import PostgresUniversityRepository
from audentra.integrations.assistant.tools import execute_tool_reads


class BorrowedConnection(AbstractAsyncContextManager[AsyncConnection]):
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> AsyncConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class TransactionEngine:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    def connect(self) -> BorrowedConnection:
        return BorrowedConnection(self.connection)

    def begin(self) -> BorrowedConnection:
        return BorrowedConnection(self.connection)


@pytest.mark.postgres
def test_expired_seed_signup_completes_onboarding_with_its_own_remaining_requirements() -> None:
    url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a migrated, compact-seeded isolated TEST_DATABASE_URL")

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(url))
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    tenant = UUID("00000000-0000-7000-8000-000000000001")
                    bound = cast(AsyncEngine, TransactionEngine(connection))
                    await connection.execute(
                        text(
                            "UPDATE admission_offer SET response_deadline=CURRENT_DATE-1 "
                            "WHERE tenant_id=:tenant"
                        ),
                        {"tenant": tenant},
                    )
                    auth = PostgresDevelopmentAuth(
                        bound, environment="test", staff_invitation_code="local-test-invitation"
                    )
                    email = f"signup-{uuid4()}@example.test"
                    session = await auth.sign_up_student(
                        tenant_id=str(tenant),
                        tenant_slug="aster",
                        email=email,
                        phone=f"+1202{uuid4().int % 10_000_000:07d}",
                        legal_name=None,
                        password=f"Local-{uuid4()}!",
                    )
                    actor = session.context
                    university = PostgresUniversityRepository(bound)
                    # Model the deployed tenant: an import exists, but this new
                    # account has no imported dossier. Its own public record wins.
                    university.tenant_ids = frozenset({str(tenant)})
                    assert not await university.has_student(actor)
                    portal = PostgresPortalRepository(bound, university=university)
                    platform = PostgresPlatformRepository(bound)
                    profile = await portal.get_student_profile(actor)
                    assert profile["email"] == email
                    assert profile["studentId"] == actor.student_id
                    await portal.get_student_academics(actor)
                    await portal.get_student_financials(actor)
                    offer = (
                        (
                            await connection.execute(
                                text(
                                    "SELECT id,response_deadline FROM admission_offer "
                                    "WHERE tenant_id=:tenant AND student_id=:student"
                                ),
                                {"tenant": tenant, "student": UUID(actor.student_id)},
                            )
                        )
                        .mappings()
                        .one()
                    )
                    today = cast(date, await connection.scalar(text("SELECT CURRENT_DATE")))
                    assert offer["response_deadline"] >= today + timedelta(days=30)
                    # Minting a new development offer never renews older offers.
                    old_deadline = await connection.scalar(
                        text(
                            "SELECT MAX(response_deadline) FROM admission_offer "
                            "WHERE tenant_id=:tenant AND student_id<>:student"
                        ),
                        {"tenant": tenant, "student": UUID(actor.student_id)},
                    )
                    assert old_deadline < today
                    offer_id = str(offer["id"])
                    acceptance_key = str(uuid4())
                    accepted = await platform.accept_admission_offer(
                        actor, offer_id, acceptance_key, str(uuid4())
                    )
                    assert accepted == await platform.accept_admission_offer(
                        actor, offer_id, acceptance_key, str(uuid4())
                    )
                    before = await portal.get_student_requirements(actor)
                    assert len(before["items"]) > 0
                    initial = await portal.get_student_bootstrap(actor)
                    assert initial["initialRoute"] == "/onboarding"
                    data = {
                        "firstName": "Morgan",
                        "lastName": "Test",
                        "preferredName": "Morgan",
                        "personalEmail": email,
                        "mobilePhone": session.phone,
                        "citizenshipStatus": "us_citizen",
                        "communicationPreference": "email",
                        "residencyStatus": "domestic",
                        "housingPreference": "off_campus",
                        "emergencyContacts": [
                            {
                                "fullName": "Alex Test",
                                "relationship": "parent",
                                "mobilePhone": "+12025550102",
                            }
                        ],
                        "signatureFullName": "Morgan Test",
                        "signatureMethod": "typed",
                        "signatureConsent": True,
                        "signedDocumentIds": ["enrollment_acknowledgment"],
                        "depositChoice": "pay_later",
                    }
                    onboarding = await portal.get_student_onboarding(actor)
                    for step in (
                        "offer",
                        "about_you",
                        "housing",
                        "campus_life",
                        "emergency_contacts",
                        "family_permissions",
                        "review_and_sign",
                        "deposit",
                    ):
                        onboarding = await portal.update_student_onboarding(
                            actor,
                            {
                                "expectedVersion": onboarding["version"],
                                "currentStep": step,
                                "data": data,
                            },
                            str(uuid4()),
                        )
                    completed = await portal.complete_student_onboarding(
                        actor,
                        {"expectedVersion": onboarding["version"]},
                        str(uuid4()),
                        str(uuid4()),
                    )
                    assert completed["status"] == "completed"
                    bootstrap = await portal.get_student_bootstrap(actor)
                    assert bootstrap["initialRoute"] == "/dashboard"
                    assert bootstrap["onboarding"]["required"] is False
                    after = await portal.get_student_requirements(actor)
                    assert {item["id"] for item in after["items"]} == {
                        item["id"] for item in before["items"]
                    }
                    states = {item["code"]: item["status"] for item in after["items"]}
                    assert states["profile_verification"] == "completed"
                    assert states["identity_document"] != "completed"
                    assert states["enrollment_deposit"] != "completed"
                    assert any(
                        item["status"] in {"ready", "blocked", "in_progress"}
                        for item in after["items"]
                    )
                    assert (await portal.get_student_profile(actor))["firstName"] == "Morgan"
                    # Edward reads the same durable record, even in an imported
                    # tenant. A missing demo dossier must not hide this student.
                    service = PostgresPlatformService(
                        PostgresRepositoryBundle(
                            platform=platform,
                            portal=portal,
                            staff=PostgresStaffRepository(bound, portal),
                            university=university,
                        ),
                        storage=cast(Any, None),
                        ai=cast(Any, None),
                        signed_documents=cast(Any, None),
                        worker_token=str(uuid4()),
                    )
                    host = await service._assistant_host(actor)
                    assert not host.supports("university_record")
                    assert "now" not in service._read_loop_settings(
                        DEFAULT_ASSISTANT_EXECUTION, actor, university_context=False
                    )
                    assert (await host.read("profile"))["email"] == email
                    assert (await host.read("onboarding"))["data"]["firstName"] == "Morgan"
                    assert await host.read("requirements") == after
                    # Sequential reads share the rollback transaction connection.
                    for tool in (
                        "getStudentProfile",
                        "getOnboardingResponses",
                        "getOnboardingChecklist",
                        "getEnrollmentState",
                    ):
                        result = await execute_tool_reads([tool], host, timeout_seconds=10)
                        assert result.reads[tool]["status"] == "available"
                    # An expired existing offer still fails; acceptance policy
                    # and tenant/student scoping are unchanged by signup.
                    second = await auth.sign_up_student(
                        tenant_id=str(tenant),
                        tenant_slug="aster",
                        email=f"other-{uuid4()}@example.test",
                        phone=f"+1202{uuid4().int % 10_000_000:07d}",
                        legal_name=None,
                        password=f"Local-{uuid4()}!",
                    )
                    with pytest.raises(NotFoundError):
                        await platform.accept_admission_offer(
                            second.context, offer_id, str(uuid4()), str(uuid4())
                        )
                    other_host = await service._assistant_host(second.context)
                    assert (await other_host.read("profile"))["email"] != email
                    assert (await other_host.read("profile"))["studentId"] != actor.student_id
                    expired_id = await connection.scalar(
                        text(
                            "UPDATE admission_offer SET response_deadline=CURRENT_DATE-1 "
                            "WHERE tenant_id=:tenant AND student_id=:student RETURNING id"
                        ),
                        {"tenant": tenant, "student": UUID(second.context.student_id)},
                    )
                    with pytest.raises(ConflictError, match="active admission offer"):
                        await platform.accept_admission_offer(
                            second.context, str(expired_id), str(uuid4()), str(uuid4())
                        )
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.postgres
def test_imported_demo_retains_university_reads_and_identity_boundaries() -> None:
    """Read-only check against an explicit local copy of the full demo world."""
    from dataclasses import replace
    from urllib.parse import urlparse

    from audentra.core.auth import AuthContext

    url = os.getenv("AUDENTRA_SIGNUP_UNIVERSITY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an isolated local audentra_university_signup_* demo copy")
    parsed = urlparse(url)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.path.startswith(
        "/audentra_university_signup_"
    ):
        raise ValueError("Use a dedicated local signup demo copy")

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(url))
        try:
            university = PostgresUniversityRepository(engine)
            await university.load_tenants()
            async with engine.connect() as connection:
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT s.tenant_id,s.id,s.person_id FROM public.student s "
                                "JOIN public.tenant t ON t.id=s.tenant_id "
                                "WHERE t.slug='aster-demo' AND s.external_ref='SYN-000061'"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            auth = AuthContext(
                tenant_id=str(row["tenant_id"]),
                student_id=str(row["id"]),
                actor_id=str(row["person_id"]),
                actor_type="student",
            )
            portal = PostgresPortalRepository(engine, university=university)
            service = PostgresPlatformService(
                PostgresRepositoryBundle(
                    platform=PostgresPlatformRepository(engine),
                    portal=portal,
                    staff=PostgresStaffRepository(engine, portal),
                    university=university,
                ),
                storage=cast(Any, None),
                ai=cast(Any, None),
                signed_documents=cast(Any, None),
                worker_token=str(uuid4()),
            )
            host = await service._assistant_host(auth)
            assert host.supports("university_record")
            overview = await host.read("university_record", domain="overview")
            assert overview["student"]["preferred_name"] == "Ada"
            assert overview["student"]["id"] == auth.student_id
            assert "now" in service._read_loop_settings(DEFAULT_ASSISTANT_EXECUTION, auth)
            delegated = replace(auth, actor_type="delegate", delegate_scopes=frozenset({"profile"}))
            delegate_host = await service._assistant_host(delegated)
            assert not delegate_host.supports("university_record")
            assert not delegate_host.supports("requirements")
            other_tenant = str(uuid4())
            university.tenant_ids = frozenset({auth.tenant_id, other_tenant})
            assert not await university.has_student(replace(auth, tenant_id=other_tenant))
        finally:
            await engine.dispose()

    asyncio.run(scenario())
