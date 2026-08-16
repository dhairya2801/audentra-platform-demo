from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.seeding.media import seed_portal_media
from audentra.infrastructure.seeding.relational import (
    ASTER_STUDENT_ID,
    ASTER_TENANT_ID,
    HARVARD_OFFER_ID,
    HARVARD_PERSON_ID,
    HARVARD_STUDENT_ID,
    HARVARD_TENANT_ID,
    reset_relational_data,
    seed_relational_data,
)
from audentra.infrastructure.storage.s3 import S3ObjectStorage, S3StorageSettings

pytestmark = pytest.mark.integration


def test_relational_seed_is_rerunnable_against_postgres() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AUDENTRA_TEST_DATABASE_URL is not configured")

    async def scenario() -> None:
        engine = create_database_engine(database_url)
        try:
            await reset_relational_data(
                engine,
                environment="test",
                completed_onboarding=False,
                preserve_managed_configurations=False,
            )
            first = await seed_relational_data(engine, environment="test")
            managed_repository = PostgresManagedConfigurationRepository(engine)
            aster_staff_auth = AuthContext(
                tenant_id=ASTER_TENANT_ID,
                student_id=ASTER_STUDENT_ID,
                actor_id="00000000-0000-7000-8000-000000000901",
                actor_type="staff",
                tenant_slug="aster",
            )
            campus_configuration = await managed_repository.get(aster_staff_auth, "campus_life")
            staff_publication = await managed_repository.publish(
                aster_staff_auth,
                "campus_life",
                {
                    "expectedVersion": campus_configuration["version"],
                    "document": campus_configuration["document"],
                    "changeSummary": "Staff-published version retained across seed reruns",
                },
                "seed-integration-staff-publication",
            )
            async with engine.connect() as connection:
                configurations_before = {
                    (str(row["tenant_id"]), str(row["kind"])): (
                        str(row["id"]),
                        int(row["version"]),
                        row["document"],
                    )
                    for row in (
                        await connection.execute(
                            text(
                                """
                                SELECT id, tenant_id, kind, version, document
                                FROM staff_managed_configuration_version
                                WHERE active=true
                                ORDER BY tenant_id, kind
                                """
                            )
                        )
                    )
                    .mappings()
                    .all()
                }
            second = await seed_relational_data(engine, environment="test")
            async with engine.connect() as connection:
                inventory_result = await connection.execute(
                    text(
                        """
                        SELECT tenant.id::text AS tenant_id,
                               (SELECT COUNT(*) FROM student
                                WHERE student.tenant_id=tenant.id) AS students,
                               (SELECT COUNT(*) FROM staff_member
                                WHERE staff_member.tenant_id=tenant.id) AS staff,
                               (SELECT COUNT(*) FROM journey_definition_version
                                WHERE journey_definition_version.tenant_id=tenant.id
                                  AND active=1) AS active_journeys,
                               (SELECT COUNT(DISTINCT code) FROM requirement_definition_version
                                WHERE requirement_definition_version.tenant_id=tenant.id)
                                  AS requirement_definitions,
                               (SELECT COUNT(*) FROM ai_operation_config
                                WHERE ai_operation_config.tenant_id=tenant.id)
                                  AS ai_operations,
                               (SELECT COUNT(*) FROM enrollment_journey
                                WHERE enrollment_journey.tenant_id=tenant.id) AS journeys,
                               (SELECT COUNT(*) FROM student_requirement
                                WHERE student_requirement.tenant_id=tenant.id) AS requirements,
                               (SELECT COUNT(*) FROM staff_action_rule
                                 WHERE staff_action_rule.tenant_id=tenant.id) AS action_rules
                        FROM tenant
                        WHERE tenant.id IN (
                          CAST(:aster_tenant_id AS uuid), CAST(:harvard_tenant_id AS uuid)
                        )
                        ORDER BY tenant.id
                        """
                    ),
                    {
                        "aster_tenant_id": ASTER_TENANT_ID,
                        "harvard_tenant_id": HARVARD_TENANT_ID,
                    },
                )
                inventory = {
                    row["tenant_id"]: dict(row) for row in inventory_result.mappings().all()
                }
                configurations_after = {
                    (str(row["tenant_id"]), str(row["kind"])): (
                        str(row["id"]),
                        int(row["version"]),
                        row["document"],
                    )
                    for row in (
                        await connection.execute(
                            text(
                                """
                                SELECT id, tenant_id, kind, version, document
                                FROM staff_managed_configuration_version
                                WHERE active=true
                                ORDER BY tenant_id, kind
                                """
                            )
                        )
                    )
                    .mappings()
                    .all()
                }
                materialized_media = {
                    str(row["code"]): dict(row)
                    for row in (
                        await connection.execute(
                            text(
                                """
                                SELECT code, source_id, related_videos
                                FROM catalog_course
                                WHERE tenant_id=CAST(:tenant_id AS uuid) AND active=true
                                ORDER BY code
                                """
                            ),
                            {"tenant_id": ASTER_TENANT_ID},
                        )
                    )
                    .mappings()
                    .all()
                }
            assert first.rows == second.rows == 264
            assert len(configurations_before) == 6
            assert configurations_after == configurations_before
            assert configurations_after[(ASTER_TENANT_ID, "campus_life")][1] == 2
            assert staff_publication["version"] == 2
            assert materialized_media["CS 101"]["source_id"] == "cs-101"
            assert materialized_media["CS 101"]["related_videos"][0]["url"].startswith(
                "https://www.youtube.com/"
            )
            assert materialized_media["MATH 151"]["source_id"] == "math-151"
            assert materialized_media["MATH 151"]["related_videos"]
            assert set(inventory) == {ASTER_TENANT_ID, HARVARD_TENANT_ID}
            for tenant_id, tenant in inventory.items():
                # Three long-standing demos plus eleven students spanning the
                # offered, accepted, deposited, ready, and declined funnel.
                assert tenant["students"] == 14
                assert tenant["staff"] == 3
                assert tenant["active_journeys"] == 1
                assert tenant["requirement_definitions"] == 8
                assert tenant["ai_operations"] == 9
                # Both primary offers remain offered until the acceptance
                # exercised below, so the ten accepted funnel students own
                # the materialized journeys at this point.
                assert tenant["journeys"] == 10, tenant_id
                assert tenant["requirements"] == 80, tenant_id
                assert tenant["action_rules"] == 1

            harvard_auth = AuthContext(
                tenant_id=HARVARD_TENANT_ID,
                student_id=HARVARD_STUDENT_ID,
                actor_id=HARVARD_PERSON_ID,
                actor_type="student",
                tenant_slug="harvard",
            )
            accepted = await PostgresPlatformRepository(
                engine,
                clock=lambda: datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            ).accept_admission_offer(
                harvard_auth,
                HARVARD_OFFER_ID,
                "seed-harvard-offer-acceptance",
                "seed-integration-request",
            )
            assert accepted["offerStatus"] == "accepted"
            async with engine.connect() as connection:
                harvard_requirement_count = await connection.scalar(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM student_requirement requirement
                        JOIN enrollment_journey journey ON journey.id=requirement.journey_id
                        WHERE journey.tenant_id=CAST(:tenant_id AS uuid)
                          AND journey.student_id=CAST(:student_id AS uuid)
                        """
                    ),
                    {
                        "tenant_id": HARVARD_TENANT_ID,
                        "student_id": HARVARD_STUDENT_ID,
                    },
                )
            assert harvard_requirement_count == 8

            # Publishing a new requirement-definition version must not make
            # the deterministic demo requirement IDs collide on the next seed pass.
            await seed_relational_data(engine, environment="test")

            async with engine.connect() as connection:
                tenant_configuration_before_reset = await connection.scalar(
                    text(
                        """
                        SELECT jsonb_build_object(
                          'tenant', to_jsonb(tenant_row),
                          'portalConfiguration', to_jsonb(portal_configuration)
                        )
                        FROM tenant tenant_row
                        JOIN tenant_portal_configuration portal_configuration
                          ON portal_configuration.tenant_id=tenant_row.id
                        WHERE tenant_row.id=CAST(:tenant_id AS uuid)
                        """
                    ),
                    {"tenant_id": ASTER_TENANT_ID},
                )

            await reset_relational_data(
                engine,
                environment="test",
                completed_onboarding=True,
            )
            async with engine.connect() as connection:
                completed_result = await connection.execute(
                    text(
                        """
                        SELECT onboarding.tenant_id::text AS tenant_id,
                               onboarding.status,
                               offer.status AS offer_status,
                               COUNT(requirement.id) AS requirement_count
                        FROM student_onboarding onboarding
                        JOIN admission_offer offer
                          ON offer.tenant_id=onboarding.tenant_id
                         AND offer.student_id=onboarding.student_id
                        JOIN enrollment_journey journey
                          ON journey.tenant_id=onboarding.tenant_id
                         AND journey.student_id=onboarding.student_id
                        JOIN student_requirement requirement
                          ON requirement.tenant_id=journey.tenant_id
                         AND requirement.journey_id=journey.id
                        WHERE onboarding.student_id IN (
                          CAST(:aster_student_id AS uuid), CAST(:harvard_student_id AS uuid)
                        )
                        GROUP BY onboarding.tenant_id, onboarding.status, offer.status
                        ORDER BY onboarding.tenant_id
                        """
                    ),
                    {
                        "aster_student_id": ASTER_STUDENT_ID,
                        "harvard_student_id": HARVARD_STUDENT_ID,
                    },
                )
                completed_rows = completed_result.mappings().all()
                tenant_configuration_after_reset = await connection.scalar(
                    text(
                        """
                        SELECT jsonb_build_object(
                          'tenant', to_jsonb(tenant_row),
                          'portalConfiguration', to_jsonb(portal_configuration)
                        )
                        FROM tenant tenant_row
                        JOIN tenant_portal_configuration portal_configuration
                          ON portal_configuration.tenant_id=tenant_row.id
                        WHERE tenant_row.id=CAST(:tenant_id AS uuid)
                        """
                    ),
                    {"tenant_id": ASTER_TENANT_ID},
                )
                managed_configurations_after_reset = {
                    (str(row["tenant_id"]), str(row["kind"])): (
                        str(row["id"]),
                        int(row["version"]),
                        row["document"],
                    )
                    for row in (
                        await connection.execute(
                            text(
                                """
                                SELECT id, tenant_id, kind, version, document
                                FROM staff_managed_configuration_version
                                WHERE active=true
                                ORDER BY tenant_id, kind
                                """
                            )
                        )
                    )
                    .mappings()
                    .all()
                }
            assert len(completed_rows) == 2
            assert tenant_configuration_after_reset == tenant_configuration_before_reset
            assert managed_configurations_after_reset == configurations_before
            assert {row["tenant_id"] for row in completed_rows} == {
                ASTER_TENANT_ID,
                HARVARD_TENANT_ID,
            }
            assert all(row["status"] == "completed" for row in completed_rows)
            assert all(row["offer_status"] == "accepted" for row in completed_rows)
            assert all(row["requirement_count"] == 8 for row in completed_rows)

            # Keep the shared local demo database ready for the onboarding walkthrough.
            await reset_relational_data(
                engine,
                environment="test",
                completed_onboarding=False,
                preserve_managed_configurations=False,
            )
            await seed_relational_data(engine, environment="test")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_portal_media_seed_is_rerunnable_against_s3() -> None:
    endpoint = os.getenv("AUDENTRA_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("AUDENTRA_TEST_S3_ENDPOINT is not configured")
    settings = RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "test",
            "OBJECT_STORAGE_ENDPOINT": endpoint,
            "OBJECT_STORAGE_BUCKET": os.getenv("AUDENTRA_TEST_S3_BUCKET", "vv-documents"),
            "OBJECT_STORAGE_ACCESS_KEY": os.getenv("AUDENTRA_TEST_S3_ACCESS_KEY", "vv_minio"),
            "OBJECT_STORAGE_SECRET_KEY": os.getenv(
                "AUDENTRA_TEST_S3_SECRET_KEY", "vv_minio_password"
            ),
        }
    )
    s3_settings = settings.object_storage
    assert isinstance(s3_settings, S3StorageSettings)
    media_root = Path(__file__).resolve().parents[1] / "assets" / "portal-media"

    async def scenario() -> None:
        storage = S3ObjectStorage(s3_settings)
        try:
            first = await seed_portal_media(
                storage,
                environment="test",
                media_root=media_root,
            )
            second = await seed_portal_media(
                storage,
                environment="test",
                media_root=media_root,
            )
            assert first.assets == second.assets == 7
            assert second.unchanged == 7
        finally:
            await storage.close()

    asyncio.run(scenario())
