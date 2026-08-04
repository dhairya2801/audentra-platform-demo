from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from audentra.core.auth import AuthContext
from audentra.core.errors import NotFoundError
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository

pytestmark = pytest.mark.postgres


class _BorrowedConnectionContext(AbstractAsyncContextManager[AsyncConnection]):
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> AsyncConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _TransactionBoundEngine:
    """Keep repository writes inside the integration test's rollback boundary."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    def connect(self) -> _BorrowedConnectionContext:
        return _BorrowedConnectionContext(self.connection)

    def begin(self) -> _BorrowedConnectionContext:
        return _BorrowedConnectionContext(self.connection)


def test_real_postgres_portal_read_is_tenant_isolated() -> None:
    database_url = os.environ.get("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run PostgreSQL integration tests")

    tenant_id = uuid4()
    foreign_tenant_id = uuid4()
    person_id = uuid4()
    student_id = uuid4()

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(database_url))
        repository = PostgresPortalRepository(engine)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.tenant (id, name)
                        VALUES (:tenant_id, :tenant_name),
                               (:foreign_tenant_id, :foreign_tenant_name)
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "tenant_name": f"Portal integration {tenant_id}",
                        "foreign_tenant_id": foreign_tenant_id,
                        "foreign_tenant_name": f"Foreign integration {foreign_tenant_id}",
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.person (
                          id, tenant_id, preferred_name, first_name, last_name
                        )
                        VALUES (
                          :person_id, :tenant_id, 'Integration', 'Portal', 'Student'
                        )
                        """
                    ),
                    {"person_id": person_id, "tenant_id": tenant_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.student (
                          id, tenant_id, person_id, class_year
                        )
                        VALUES (:student_id, :tenant_id, :person_id, 2030)
                        """
                    ),
                    {
                        "student_id": student_id,
                        "tenant_id": tenant_id,
                        "person_id": person_id,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.student_onboarding (
                          tenant_id, student_id, status, current_step
                        )
                        VALUES (:tenant_id, :student_id, 'not_started', 'offer')
                        """
                    ),
                    {"tenant_id": tenant_id, "student_id": student_id},
                )

            auth = AuthContext(
                tenant_id=str(tenant_id),
                student_id=str(student_id),
                actor_id=str(student_id),
                actor_type="student",
            )
            onboarding = await repository.get_student_onboarding(auth)
            assert onboarding["studentId"] == str(student_id)
            assert onboarding["status"] == "not_started"
            assert onboarding["currentStep"] == "offer"

            foreign = AuthContext(
                tenant_id=str(foreign_tenant_id),
                student_id=str(student_id),
                actor_id=str(student_id),
                actor_type="student",
            )
            with pytest.raises(NotFoundError):
                await repository.get_student_onboarding(foreign)
        finally:
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            """
                            DELETE FROM public.student_onboarding
                            WHERE tenant_id = :tenant_id AND student_id = :student_id
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    await connection.execute(
                        text(
                            """
                            DELETE FROM public.student
                            WHERE tenant_id = :tenant_id AND id = :student_id
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    await connection.execute(
                        text(
                            """
                            DELETE FROM public.person
                            WHERE tenant_id = :tenant_id AND id = :person_id
                            """
                        ),
                        {"tenant_id": tenant_id, "person_id": person_id},
                    )
                    await connection.execute(
                        text(
                            """
                            DELETE FROM public.tenant
                            WHERE id IN (:tenant_id, :foreign_tenant_id)
                            """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "foreign_tenant_id": foreign_tenant_id,
                        },
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_requirement_upload_uses_current_published_definition() -> None:
    database_url = os.environ.get("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run PostgreSQL integration tests")

    tenant_id = uuid4()
    person_id = uuid4()
    student_id = uuid4()
    campus_id = uuid4()
    term_id = uuid4()
    program_id = uuid4()
    offer_id = uuid4()
    journey_definition_id = uuid4()
    evidence_definition_id = uuid4()
    current_definition_id = uuid4()
    journey_id = uuid4()
    requirement_id = uuid4()

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(database_url))
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                        {"id": tenant_id, "name": f"Upload integration {tenant_id}"},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO person (id, tenant_id, first_name, last_name)
                            VALUES (:id, :tenant_id, 'Upload', 'Student')
                            """
                        ),
                        {"id": person_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student (id, tenant_id, person_id, class_year)
                            VALUES (:id, :tenant_id, :person_id, 2030)
                            """
                        ),
                        {
                            "id": student_id,
                            "tenant_id": tenant_id,
                            "person_id": person_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO campus (id, tenant_id, name)
                            VALUES (:id, :tenant_id, 'Main')
                            """
                        ),
                        {"id": campus_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO academic_term (id, tenant_id, name, starts_on)
                            VALUES (:id, :tenant_id, 'Fall 2030', DATE '2030-08-20')
                            """
                        ),
                        {"id": term_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO program (
                              id, tenant_id, name, code, degree, total_credits,
                              description
                            ) VALUES (
                              :id, :tenant_id, 'Testing', 'TEST', 'Bachelor of Testing',
                              120, 'Integration-test program'
                            )
                            """
                        ),
                        {"id": program_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO admission_offer (
                              id, tenant_id, student_id, program_id, academic_term_id,
                              campus_id, response_deadline, deposit_amount_cents, status,
                              accepted_at
                            ) VALUES (
                              :id, :tenant_id, :student_id, :program_id, :term_id,
                              :campus_id, DATE '2030-05-01', 0, 'accepted', NOW()
                            )
                            """
                        ),
                        {
                            "id": offer_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                            "program_id": program_id,
                            "term_id": term_id,
                            "campus_id": campus_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO journey_definition_version (
                              id, tenant_id, code, version, active, onboarding_required
                            ) VALUES (:id, :tenant_id, 'upload-regression', 2, 1, false)
                            """
                        ),
                        {"id": journey_definition_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO requirement_definition_version (
                              id, tenant_id, code, title, description, blocking,
                              display_order, version, submission_type, responsible_office,
                              flow_kind, interaction_type, input_config
                            ) VALUES (
                              :evidence_id, :tenant_id, 'official_transcript',
                              'Old transcript form', 'Historical evidence definition', 1,
                              10, 1, 'form', 'Registrar', 'enrollment', 'form', '{}'::jsonb
                            ), (
                              :current_id, :tenant_id, 'official_transcript',
                              'Upload transcript', 'Current published definition', 1,
                              10, 2, 'document', 'Registrar', 'enrollment', 'upload_file',
                              '{}'::jsonb
                            )
                            """
                        ),
                        {
                            "evidence_id": evidence_definition_id,
                            "current_id": current_definition_id,
                            "tenant_id": tenant_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO journey_requirement_definition (
                              journey_definition_version_id,
                              requirement_definition_version_id
                            ) VALUES (:journey_definition_id, :current_definition_id)
                            """
                        ),
                        {
                            "journey_definition_id": journey_definition_id,
                            "current_definition_id": current_definition_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO enrollment_journey (
                              id, tenant_id, student_id, offer_id,
                              journey_definition_version_id, status
                            ) VALUES (
                              :id, :tenant_id, :student_id, :offer_id,
                              :journey_definition_id, 'in_progress'
                            )
                            """
                        ),
                        {
                            "id": journey_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                            "offer_id": offer_id,
                            "journey_definition_id": journey_definition_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student_requirement (
                              id, tenant_id, journey_id,
                              requirement_definition_version_id, status
                            ) VALUES (
                              :id, :tenant_id, :journey_id, :evidence_definition_id,
                              'ready'
                            )
                            """
                        ),
                        {
                            "id": requirement_id,
                            "tenant_id": tenant_id,
                            "journey_id": journey_id,
                            "evidence_definition_id": evidence_definition_id,
                        },
                    )

                    repository = PostgresPortalRepository(
                        cast(
                            AsyncEngine,
                            _TransactionBoundEngine(connection),
                        )
                    )
                    auth = AuthContext(
                        tenant_id=str(tenant_id),
                        student_id=str(student_id),
                        actor_id=str(student_id),
                        actor_type="student",
                    )

                    rendered = await repository.get_student_requirement(auth, str(requirement_id))
                    assert rendered["submissionType"] == "document"
                    assert rendered["interactionType"] == "upload_file"

                    uploaded = await repository.reserve_student_document_upload(
                        auth,
                        {
                            "fileName": "transcript.pdf",
                            "mimeType": "application/pdf",
                            "sizeBytes": 128,
                            "category": "other",
                            "sha256": "a" * 64,
                            "uploadBundleId": str(uuid4()),
                        },
                        str(uuid4()),
                        str(uuid4()),
                        str(requirement_id),
                    )
                    assert uploaded["requirementId"] == str(requirement_id)
                    assert uploaded["category"] == "transcript"
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
