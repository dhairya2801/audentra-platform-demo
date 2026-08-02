from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from audentra.core.auth import AuthContext
from audentra.core.errors import NotFoundError
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository

pytestmark = pytest.mark.postgres


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
