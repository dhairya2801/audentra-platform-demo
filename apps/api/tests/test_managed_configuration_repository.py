from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
    academic_courses,
    campus_events,
    materialized_journey_tasks,
    parse_managed_configuration,
)
from audentra.infrastructure.seeding.relational import (
    ASTER_STUDENT_ID,
    ASTER_TENANT_ID,
    HARVARD_TENANT_ID,
    reset_relational_data,
)

STAFF_ID = "00000000-0000-7000-8000-000000000901"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_journey_parser_materializes_new_onboarding_and_enrollment_tasks() -> None:
    document = parse_managed_configuration(
        "journeys",
        """
schema_version: 1
tenant: aster
configuration: journeys
flows:
  - id: offer_onboarding
    kind: onboarding
    status: published
    tasks:
      - id: review_offer
        student_step: offer
        title: Review your offer
        description: Review the original offer.
        task_type: approval
        required: true
        points: 20
      - id: arrival_preferences
        student_step: arrival_preferences
        title: Confirm arrival preferences
        description: Tell Student Life when you plan to arrive.
        task_type: form
        owner: Student Life
        required: true
        points: 25
        depends_on: [review_offer]
  - id: enrollment_checklist
    kind: enrollment
    status: published
    tasks:
      - id: profile_verification
        title: Verify your profile
        description: Confirm your contact details.
        task_type: form
        required: true
        points: 30
""",
        tenant_slug="aster",
    )

    tasks = materialized_journey_tasks(document)

    assert [task["code"] for task in tasks] == [
        "arrival_preferences",
        "profile_verification",
    ]
    assert tasks[0]["kind"] == "onboarding"
    assert tasks[0]["dependsOn"] == []
    assert tasks[0]["points"] == 25
    assert tasks[1]["kind"] == "enrollment"
    assert tasks[1]["points"] == 30


def test_journey_parser_rejects_unknown_dependency() -> None:
    yaml_text = """
tenant: aster
configuration: journeys
flows:
  - id: enrollment
    kind: enrollment
    tasks:
      - id: orientation
        title: Register for orientation
        description: Choose an orientation session.
        required: true
        depends_on: [missing_requirement]
"""

    with pytest.raises(ApiError, match="depends on unknown task") as error:
        parse_managed_configuration("journeys", yaml_text, tenant_slug="aster")

    assert error.value.code == "MANAGED_JOURNEY_DEPENDENCY_NOT_FOUND"


def test_managed_parser_rejects_cross_tenant_publication() -> None:
    with pytest.raises(ApiError, match="authenticated tenant") as error:
        parse_managed_configuration(
            "campus_life",
            """
tenant: harvard
configuration: campus_life
events: []
""",
            tenant_slug="aster",
        )

    assert error.value.code == "MANAGED_CONFIGURATION_TENANT_MISMATCH"


def test_campus_event_parser_validates_and_normalizes_timestamps() -> None:
    document = parse_managed_configuration(
        "campus_life",
        """
tenant: aster
configuration: campus_life
events:
  - id: welcome-picnic
    title: Welcome Picnic
    description: Meet classmates on the green.
    starts_at: 2027-08-28T18:00:00Z
    ends_at: 2027-08-28T20:00:00Z
    location: University Green
    category: social
    featured: true
    accent: gold
    visual_theme: festival
""",
        tenant_slug="aster",
    )

    event = campus_events(document)[0]

    assert event["sourceId"] == "welcome-picnic"
    assert event["starts_at"] == datetime(2027, 8, 28, 18, tzinfo=UTC)
    assert event["ends_at"] == datetime(2027, 8, 28, 20, tzinfo=UTC)


def test_academic_course_parser_keeps_prerequisites_and_resources() -> None:
    document = parse_managed_configuration(
        "academics",
        """
tenant: aster
configuration: academics
courses:
  - code: CS 201
    title: Data Structures
    description: Trees, graphs, hashing, and algorithm analysis.
    credits: 4
    level: 200
    instructor_names: [Prof. Jordan Kim]
    prerequisites:
      - course_code: CS 101
        minimum_grade: C
    resources:
      - label: Course handbook
        url: https://example.edu/cs-201
""",
        tenant_slug="aster",
    )

    course = academic_courses(document)[0]

    assert course["code"] == "CS 201"
    assert course["prerequisites"] == [{"courseCode": "CS 101", "minimumGrade": "C"}]
    assert course["resources"] == [
        {"label": "Course handbook", "url": "https://example.edu/cs-201"}
    ]


def test_academic_course_parser_returns_public_error_for_invalid_credits() -> None:
    with pytest.raises(ApiError, match="course credits") as error:
        parse_managed_configuration(
            "academics",
            """
tenant: aster
configuration: academics
courses:
  - code: CS 201
    title: Data Structures
    description: Trees, graphs, hashing, and algorithm analysis.
    credits: many
    level: 200
""",
            tenant_slug="aster",
        )

    assert error.value.code == "INVALID_MANAGED_CONFIGURATION"


def test_staff_managed_experience_migration_is_tenant_scoped_and_versioned() -> None:
    migration = (
        Path(__file__).parents[1] / "migrations" / "0021_staff_managed_experience.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE staff_managed_configuration_version" in migration
    assert "UNIQUE (tenant_id, kind, version)" in migration
    assert "WHERE active = true" in migration
    assert "CREATE TABLE student_experience_update" in migration
    assert "UNIQUE (tenant_id, student_id, publication_id, source_key)" in migration


@pytest.mark.anyio
@pytest.mark.postgres
async def test_publications_materialize_into_student_facing_postgres_tables() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run the managed publication integration")

    engine = create_database_engine(database_url)
    assets = Path(__file__).parents[1] / "assets" / "config" / "tenants" / "aster"
    staff_auth = AuthContext(
        tenant_id=ASTER_TENANT_ID,
        student_id=ASTER_STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type="staff",
        tenant_slug="aster",
    )
    student_auth = AuthContext(
        tenant_id=ASTER_TENANT_ID,
        student_id=ASTER_STUDENT_ID,
        actor_id=ASTER_STUDENT_ID,
        actor_type="student",
        tenant_slug="aster",
    )
    publication_time = datetime(2028, 1, 15, 12, 0, tzinfo=UTC)
    repository = PostgresManagedConfigurationRepository(
        engine,
        clock=lambda: publication_time,
    )
    try:
        await reset_relational_data(engine, environment="test", completed_onboarding=True)

        journey_yaml = (assets / "journeys.yaml").read_text(encoding="utf-8")
        journey_document = yaml.safe_load(journey_yaml)
        assert isinstance(journey_document, dict)
        onboarding = next(
            flow for flow in journey_document["flows"] if flow.get("kind") == "onboarding"
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
                "due_days_after_acceptance": 10,
            }
        )
        published = await repository.publish(
            staff_auth,
            "journeys",
            {
                "yaml": yaml.safe_dump(journey_document, sort_keys=False),
                "expectedVersion": 1,
            },
            _fallback(journey_yaml),
            "managed-journey-integration",
        )
        assert published["version"] == 2

        updates = await repository.list_student_updates(student_auth)
        meal_update = next(update for update in updates if update["title"] == "Choose a meal plan")
        assert meal_update["requirementSlug"] == "choose-a-meal-plan"
        decision = await repository.decide_student_update(
            student_auth,
            str(meal_update["id"]),
            {"action": "later", "expectedVersion": meal_update["version"]},
            "managed-journey-decision-integration",
        )
        assert decision["status"] == "deferred"

        campus_yaml = (assets / "campus-life.yaml").read_text(encoding="utf-8")
        campus_document = yaml.safe_load(campus_yaml)
        assert isinstance(campus_document, dict)
        campus_document["events"][0]["title"] = "Live Sync Welcome Event"
        await repository.publish(
            staff_auth,
            "campus_life",
            {
                "yaml": yaml.safe_dump(campus_document, sort_keys=False),
                "expectedVersion": 1,
            },
            _fallback(campus_yaml),
            "managed-campus-integration",
        )

        academics_yaml = (assets / "academics.yaml").read_text(encoding="utf-8")
        academics_document = yaml.safe_load(academics_yaml)
        assert isinstance(academics_document, dict)
        academics_document["courses"][0]["title"] = "Live Sync Programming"
        async with engine.connect() as connection:
            program_requirements_before = await connection.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM program_requirement
                    WHERE tenant_id=CAST(:tenant_id AS uuid)
                    """
                ),
                {"tenant_id": ASTER_TENANT_ID},
            )
        await repository.publish(
            staff_auth,
            "academics",
            {
                "yaml": yaml.safe_dump(academics_document, sort_keys=False),
                "expectedVersion": 1,
            },
            _fallback(academics_yaml),
            "managed-academics-integration",
        )

        async with engine.connect() as connection:
            materialized = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT
                          (SELECT COUNT(*) FROM student_requirement requirement
                           JOIN requirement_definition_version definition
                             ON definition.id=requirement.requirement_definition_version_id
                           WHERE requirement.tenant_id=CAST(:aster AS uuid)
                             AND definition.code='choose_a_meal_plan') AS requirements,
                          (SELECT COUNT(*) FROM tenant_reward_rule
                           WHERE tenant_id=CAST(:aster AS uuid)
                             AND code='choose_a_meal_plan'
                             AND trigger_type='requirement_completed'
                             AND trigger_key='choose_a_meal_plan'
                             AND points=20 AND enabled=true) AS rewards,
                          (SELECT MIN(requirement.due_at)
                           FROM student_requirement requirement
                           JOIN requirement_definition_version definition
                             ON definition.id=requirement.requirement_definition_version_id
                           WHERE requirement.tenant_id=CAST(:aster AS uuid)
                             AND definition.code='choose_a_meal_plan') AS requirement_due_at,
                          (SELECT COUNT(*) FROM campus_event
                           WHERE tenant_id=CAST(:aster AS uuid)
                             AND title='Live Sync Welcome Event') AS events,
                          (SELECT COUNT(*) FROM catalog_course
                           WHERE tenant_id=CAST(:aster AS uuid)
                             AND title='Live Sync Programming') AS courses,
                          (SELECT COUNT(*) FROM campus_event
                           WHERE tenant_id=CAST(:harvard AS uuid)
                             AND title='Live Sync Welcome Event') AS harvard_events,
                          (SELECT COUNT(*) FROM catalog_course
                           WHERE tenant_id=CAST(:harvard AS uuid)
                             AND title='Live Sync Programming') AS harvard_courses,
                          (SELECT COUNT(*) FROM program_requirement
                           WHERE tenant_id=CAST(:aster AS uuid)) AS program_requirements
                        """
                        ),
                        {"aster": ASTER_TENANT_ID, "harvard": HARVARD_TENANT_ID},
                    )
                )
                .mappings()
                .one()
            )
        assert int(materialized["requirements"]) >= 1
        assert materialized["rewards"] == 1
        assert materialized["requirement_due_at"] == publication_time + timedelta(days=10)
        assert materialized["events"] == 1
        assert materialized["courses"] == 1
        assert materialized["harvard_events"] == 0
        assert materialized["harvard_courses"] == 0
        assert materialized["program_requirements"] == program_requirements_before
    finally:
        try:
            await reset_relational_data(engine, environment="test")
        finally:
            await engine.dispose()


def _fallback(yaml_text: str) -> dict[str, Any]:
    return {
        "yaml": yaml_text,
        "version": 1,
        "recordCount": 0,
        "updatedAt": "2026-08-02T00:00:00Z",
        "updatedBy": "Packaged tenant configuration",
    }
