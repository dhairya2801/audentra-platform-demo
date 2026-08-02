from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.seeding.relational import (
    ASTER_TENANT_ID,
    reset_relational_data,
)

STAFF_ID = "00000000-0000-7000-8000-000000000901"


@pytest.mark.postgres
def test_zero_step_acceptance_routes_to_dashboard_and_later_publication_reopens() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run the zero-step integration")

    async def scenario() -> None:
        engine = create_database_engine(database_url)
        try:
            await reset_relational_data(engine, environment="test")
            async with engine.begin() as connection:
                candidate = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT offer.id AS offer_id, offer.student_id,
                                   student.person_id AS actor_id
                            FROM admission_offer offer
                            JOIN student
                              ON student.id=offer.student_id
                             AND student.tenant_id=offer.tenant_id
                            LEFT JOIN enrollment_journey journey
                              ON journey.offer_id=offer.id
                             AND journey.tenant_id=offer.tenant_id
                            WHERE offer.tenant_id=CAST(:tenant_id AS uuid)
                              AND offer.status='offered'
                              AND journey.id IS NULL
                            ORDER BY offer.created_at, offer.id
                            LIMIT 1
                            """
                            ),
                            {"tenant_id": ASTER_TENANT_ID},
                        )
                    )
                    .mappings()
                    .one()
                )
                await connection.execute(
                    text(
                        """
                        UPDATE journey_definition_version
                        SET active=0, updated_at=NOW()
                        WHERE tenant_id=CAST(:tenant_id AS uuid) AND active=1
                        """
                    ),
                    {"tenant_id": ASTER_TENANT_ID},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE student_onboarding
                        SET status='in_progress', completed_at=NULL, updated_at=NOW()
                        WHERE tenant_id=CAST(:tenant_id AS uuid)
                          AND student_id=:student_id
                        """
                    ),
                    {
                        "tenant_id": ASTER_TENANT_ID,
                        "student_id": candidate["student_id"],
                    },
                )

            student_auth = AuthContext(
                tenant_id=ASTER_TENANT_ID,
                student_id=str(candidate["student_id"]),
                actor_id=str(candidate["actor_id"]),
                actor_type="student",
                tenant_slug="aster",
            )
            accepted = await PostgresPlatformRepository(engine).accept_admission_offer(
                student_auth,
                str(candidate["offer_id"]),
                "zero-step-postgres-accept",
                "zero-step-postgres-request",
            )
            bootstrap = await PostgresPortalRepository(engine).get_student_bootstrap(student_auth)

            assert accepted["journeyStatus"] == "completed"
            assert accepted["requirementCount"] == 0
            assert accepted["onboardingRequired"] is False
            assert accepted["initialRoute"] == "/dashboard"
            assert bootstrap["onboarding"]["required"] is False
            assert bootstrap["initialRoute"] == "/dashboard"

            asset = (
                Path(__file__).parents[1]
                / "assets"
                / "config"
                / "tenants"
                / "aster"
                / "journeys.yaml"
            )
            fallback_yaml = asset.read_text(encoding="utf-8")
            document = yaml.safe_load(fallback_yaml)
            assert isinstance(document, dict)
            onboarding = next(
                flow for flow in document["flows"] if flow.get("kind") == "onboarding"
            )
            onboarding["tasks"].append(
                {
                    "id": "choose_a_meal_plan",
                    "title": "Choose a meal plan",
                    "description": "Choose the dining plan that works for you.",
                    "task_type": "single_select",
                    "owner": "Dining Services",
                    "required": True,
                    "points": 20,
                }
            )
            staff_auth = AuthContext(
                tenant_id=ASTER_TENANT_ID,
                student_id=str(candidate["student_id"]),
                actor_id=STAFF_ID,
                actor_type="staff",
                tenant_slug="aster",
            )
            await PostgresManagedConfigurationRepository(engine).publish(
                staff_auth,
                "journeys",
                {
                    "yaml": yaml.safe_dump(document, sort_keys=False),
                    "expectedVersion": 1,
                },
                {
                    "yaml": fallback_yaml,
                    "version": 1,
                    "recordCount": 0,
                    "updatedAt": "2026-08-03T00:00:00Z",
                    "updatedBy": "Packaged tenant configuration",
                },
                "zero-step-postgres-publication",
            )

            async with engine.connect() as connection:
                state = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT journey.status, definition.code AS journey_code,
                              (SELECT COUNT(*)
                               FROM student_requirement requirement
                               JOIN requirement_definition_version requirement_definition
                                 ON requirement_definition.id =
                                    requirement.requirement_definition_version_id
                               WHERE requirement.tenant_id=journey.tenant_id
                                 AND requirement.journey_id=journey.id
                                 AND requirement_definition.code='choose_a_meal_plan')
                                AS meal_requirements,
                              (SELECT COUNT(*)
                               FROM student_experience_update experience_update
                               WHERE experience_update.tenant_id=journey.tenant_id
                                 AND experience_update.student_id=journey.student_id
                                 AND experience_update.status='pending') AS pending_updates
                            FROM enrollment_journey journey
                            JOIN journey_definition_version definition
                              ON definition.id=journey.journey_definition_version_id
                             AND definition.tenant_id=journey.tenant_id
                            WHERE journey.tenant_id=CAST(:tenant_id AS uuid)
                              AND journey.student_id=:student_id
                              AND journey.offer_id=:offer_id
                            """
                            ),
                            {
                                "tenant_id": ASTER_TENANT_ID,
                                "student_id": candidate["student_id"],
                                "offer_id": candidate["offer_id"],
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
            assert state["status"] == "in_progress"
            assert state["journey_code"] == "staff_managed_enrollment"
            assert state["meal_requirements"] == 1
            assert state["pending_updates"] >= 1
        finally:
            try:
                await reset_relational_data(engine, environment="test")
            finally:
                await engine.dispose()

    asyncio.run(scenario())
