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
    _journey_input_config,
    _journey_input_flow,
    _journey_string_list,
    _journey_task_material_signature,
    _validate_core_onboarding_invariants,
    _validated_fallback,
    academic_courses,
    campus_events,
    materialized_journey_tasks,
    parse_managed_configuration,
)
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
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
        priority: 85
        due_days_after_acceptance: 14
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
    assert tasks[1]["priority"] == 85
    assert tasks[1]["dueOffsetDays"] == 14
    assert tasks[1]["points"] == 30


def test_journey_parser_rejects_dependency_cycles_with_the_cycle_path() -> None:
    with pytest.raises(ApiError, match="first_task -> second_task -> first_task") as error:
        parse_managed_configuration(
            "journeys",
            """
tenant: aster
configuration: journeys
flows:
  - id: enrollment
    kind: enrollment
    tasks:
      - id: first_task
        title: First task
        description: Depends on the second task.
        depends_on: [second_task]
      - id: second_task
        title: Second task
        description: Depends on the first task.
        depends_on: [first_task]
""",
            tenant_slug="aster",
        )

    assert error.value.code == "MANAGED_JOURNEY_DEPENDENCY_CYCLE"


def test_journey_parser_preserves_order_activity_and_interaction_configuration() -> None:
    document = parse_managed_configuration(
        "journeys",
        """
tenant: aster
configuration: journeys
flows:
  - id: offer_onboarding
    kind: onboarding
    tasks:
      - id: review_offer
        student_step: offer
        title: Review offer
        description: Review and approve the admission offer.
        task_type: approval
        required: true
      - id: meal_plan
        title: Choose a meal plan
        description: Select the dining plan you prefer.
        task_type: single_select
        options: [Standard, Vegetarian]
        required: true
      - id: old_upload
        title: Upload old form
        description: This historical task is no longer active.
        task_type: file_upload
        accepted_mime_types: [application/pdf]
        active: false
  - id: enrollment
    kind: enrollment
    tasks:
      - id: interests
        title: Choose interests
        description: Select up to two campus interests.
        task_type: multiple_select
        options: [Clubs, Sports, Volunteering]
        maximum_selections: 2
      - id: consent
        title: Sign consent
        description: Sign the student consent form.
        task_type: docusign
        docusign_template_id: consent-v2
      - id: enrollment_deposit
        title: Pay deposit
        description: Pay the enrollment deposit.
        task_type: payment
""",
        tenant_slug="aster",
    )

    current = materialized_journey_tasks(document)
    authored = materialized_journey_tasks(document, include_inactive=True)

    assert [task["code"] for task in current] == [
        "meal_plan",
        "interests",
        "consent",
        "enrollment_deposit",
    ]
    assert [task["displayOrder"] for task in current] == [20, 10, 20, 30]
    assert [task["code"] for task in authored] == [
        "review_offer",
        "meal_plan",
        "old_upload",
        "interests",
        "consent",
        "enrollment_deposit",
    ]
    assert authored[0]["materialized"] is False
    assert authored[2]["active"] is False
    assert authored[2]["interactionType"] == "upload_file"
    assert authored[2]["inputConfig"]["acceptedMimeTypes"] == ["application/pdf"]
    assert current[1]["inputConfig"]["maximumSelections"] == 2
    assert current[2]["inputConfig"] == {
        "signatureProvider": "docusign",
        "docusignTemplateId": "consent-v2",
    }


def test_journey_input_aliases_normalize_form_signature_and_upload_configuration() -> None:
    form = _journey_input_config(
        {
            "input": {
                "fields": [
                    {
                        "id": "contact_email",
                        "title": "Contact email",
                        "field_type": "email",
                        "required": True,
                    }
                ]
            }
        },
        interaction_type="form",
        authored_task_type="form",
        task_code="contact_form",
    )
    signature = _journey_input_config(
        {"signature_template_id": "consent-template"},
        interaction_type="signature",
        authored_task_type="signature",
        task_code="consent",
    )
    upload = _journey_input_config(
        {
            "accepted_file_types": ["application/pdf", "image/png"],
            "document_categories": ["consent"],
        },
        interaction_type="upload_file",
        authored_task_type="file_upload",
        task_code="consent_upload",
    )

    assert form["fields"][0]["field_type"] == "email"
    assert signature == {
        "signatureProvider": "docusign",
        "docusignTemplateId": "consent-template",
    }
    assert upload == {
        "acceptedMimeTypes": ["application/pdf", "image/png"],
        "documentCategories": ["consent"],
    }


@pytest.mark.parametrize(
    ("raw_task", "interaction_type", "authored_type", "message"),
    [
        ({"input": []}, "form", "form", "must be an object"),
        ({"input": {"fields": []}}, "single_select", "single_select", "require a form"),
        ({"maximum_selections": 2}, "single_select", "single_select", "multiple select"),
        (
            {"options": ["one"], "maximum_selections": 2},
            "multiple_select",
            "multiple_select",
            "exceeds its options",
        ),
        (
            {"signature_provider": "unknown"},
            "signature",
            "signature",
            "invalid signature provider",
        ),
        (
            {"signature_provider": "docusign"},
            "form",
            "form",
            "requires a signature task",
        ),
        (
            {"accepted_file_types": ["text/plain"]},
            "upload_file",
            "file_upload",
            "unsupported accepted MIME type",
        ),
        (
            {"accepted_mime_types": ["application/pdf"]},
            "form",
            "form",
            "requires file upload",
        ),
        ({"options": ["one"]}, "form", "form", "requires a choice interaction"),
        (
            {"input": {"helpText": "x" * 50_001}},
            "form",
            "form",
            "too large",
        ),
    ],
)
def test_journey_input_configuration_rejects_incompatible_or_unsafe_values(
    raw_task: dict[str, object],
    interaction_type: str,
    authored_type: str,
    message: str,
) -> None:
    with pytest.raises(ApiError, match=message) as error:
        _journey_input_config(
            raw_task,
            interaction_type=interaction_type,
            authored_task_type=authored_type,
            task_code="test_task",
        )

    assert error.value.code == "INVALID_MANAGED_JOURNEY_INPUT"


@pytest.mark.parametrize("task_type", ["single_select", "multiple_select"])
def test_published_selection_tasks_require_at_least_two_options(task_type: str) -> None:
    for options in ([], ["only"]):
        with pytest.raises(ApiError, match="at least two selection options"):
            _journey_input_config(
                {"options": options},
                interaction_type=task_type,
                authored_task_type=task_type,
                task_code="invalid_choice",
            )


def test_published_structured_selection_requires_fields_and_choice_options() -> None:
    with pytest.raises(ApiError, match="at least one structured selection field"):
        _journey_input_config(
            {},
            interaction_type="selection_flow",
            authored_task_type="selection_flow",
            task_code="empty_flow",
        )

    with pytest.raises(ApiError, match="field campus must define at least two options"):
        _journey_input_config(
            {
                "flow": [
                    {
                        "id": "campus",
                        "title": "Campus",
                        "field_type": "single_select",
                        "options": ["north"],
                    }
                ]
            },
            interaction_type="selection_flow",
            authored_task_type="selection_flow",
            task_code="invalid_flow",
        )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-list", "must be a list"),
        ([""], "invalid value"),
        (["duplicate", "duplicate"], "duplicate value"),
        (["one", "two", "three"], "too many"),
    ],
)
def test_journey_string_lists_are_bounded_and_unique(value: object, message: str) -> None:
    with pytest.raises(ApiError, match=message):
        _journey_string_list(
            value,
            task_code="test_task",
            label="options",
            maximum=2,
        )


def test_choice_flow_normalizes_conditions_and_rejects_invalid_field_shapes() -> None:
    flow = _journey_input_flow(
        [
            {
                "id": "campus",
                "title": "Choose campus",
                "field_type": "single_select",
                "required": True,
                "options": ["north", "south"],
            },
            {
                "id": "clubs",
                "title": "Choose clubs",
                "field_type": "multiple_select",
                "options": ["art", "sports"],
                "maximum_selections": 1,
                "when": {"field": "campus", "equals": "north"},
            },
        ],
        task_code="preferences",
    )
    assert flow[1]["maximum_selections"] == 1
    assert flow[1]["when"] == {"field": "campus", "equals": "north"}

    invalid_flows = [
        ("not-a-list", "must be a list"),
        (["not-an-object"], "invalid choice-flow field"),
        (
            [
                {"id": "same", "title": "One", "field_type": "text"},
                {"id": "same", "title": "Two", "field_type": "text"},
            ],
            "duplicate choice-flow field id",
        ),
        ([{"id": "score", "title": "Score", "field_type": "number"}], "invalid choice-flow"),
        (
            [{"id": "name", "title": "Name", "field_type": "text", "options": ["x"]}],
            "options require a select field",
        ),
        (
            [
                {
                    "id": "campus",
                    "title": "Campus",
                    "field_type": "single_select",
                    "maximum_selections": 1,
                }
            ],
            "requires multiple select",
        ),
    ]
    for invalid, message in invalid_flows:
        with pytest.raises(ApiError, match=message):
            _journey_input_flow(invalid, task_code="preferences")


def test_journey_parser_validates_core_cards_before_excluding_them() -> None:
    with pytest.raises(ApiError, match="active must be true or false") as error:
        parse_managed_configuration(
            "journeys",
            """
tenant: aster
configuration: journeys
flows:
  - id: offer_onboarding
    kind: onboarding
    tasks:
      - id: review_offer
        student_step: offer
        title: Review offer
        description: Review and approve the admission offer.
        task_type: approval
        active: definitely
""",
            tenant_slug="aster",
        )

    assert error.value.code == "INVALID_MANAGED_JOURNEY_TASK"


def test_legacy_core_submission_type_mismatch_is_accepted_but_not_materialized() -> None:
    document = parse_managed_configuration(
        "journeys",
        """
tenant: aster
configuration: journeys
flows:
  - id: offer_onboarding
    kind: onboarding
    tasks:
      - id: housing_selection
        student_step: housing
        title: Choose housing
        description: Choose your housing preferences.
        task_type: selection_flow
        submission_type: none
        flow:
          - id: living_plan
            title: Choose a living plan
            field_type: single_select
            options: [Campus, Commute]
""",
        tenant_slug="aster",
    )

    assert materialized_journey_tasks(document) == []
    authored = materialized_journey_tasks(document, include_inactive=True)
    assert authored[0]["interactionType"] == "selection_flow"
    assert authored[0]["submissionType"] == "form"
    assert authored[0]["materialized"] is False


def test_journey_parser_rejects_active_dependency_on_inactive_task() -> None:
    with pytest.raises(ApiError, match="inactive task") as error:
        parse_managed_configuration(
            "journeys",
            """
tenant: aster
configuration: journeys
flows:
  - id: enrollment
    kind: enrollment
    tasks:
      - id: first_task
        title: First task
        description: This task is temporarily inactive.
        active: false
      - id: second_task
        title: Second task
        description: This task depends on the first task.
        depends_on: [first_task]
""",
            tenant_slug="aster",
        )

    assert error.value.code == "MANAGED_JOURNEY_DEPENDENCY_NOT_FOUND"


def test_zero_active_supplemental_journey_is_valid() -> None:
    document = parse_managed_configuration(
        "journeys",
        """
tenant: aster
configuration: journeys
flows:
  - id: enrollment
    kind: enrollment
    tasks:
      - id: optional_follow_up
        title: Optional follow-up
        description: This task is currently disabled.
        active: false
""",
        tenant_slug="aster",
    )

    assert materialized_journey_tasks(document) == []
    _validate_core_onboarding_invariants(document, document)


def test_built_in_core_onboarding_content_is_editable_but_structure_is_immutable() -> None:
    original = parse_managed_configuration(
        "journeys",
        """
tenant: aster
configuration: journeys
flows:
  - id: offer_onboarding
    kind: onboarding
    tasks:
      - id: review_offer
        student_step: offer
        title: Review offer
        description: Review your offer.
        task_type: approval
""",
        tenant_slug="aster",
    )
    changed = yaml.safe_load(yaml.safe_dump(original, sort_keys=False))
    changed["flows"][0]["tasks"][0]["title"] = "A different title"
    changed["flows"][0]["tasks"][0]["input"] = {"identity_quick_upload": True}

    _validate_core_onboarding_invariants(changed, original)

    changed["flows"][0]["tasks"][0]["task_type"] = "information"

    with pytest.raises(ApiError, match="Built-in onboarding steps") as error:
        _validate_core_onboarding_invariants(changed, original)

    assert error.value.code == "CORE_ONBOARDING_IMMUTABLE"


def test_legacy_incomplete_selection_can_be_repaired_without_weakening_new_validation() -> None:
    previous = yaml.safe_load(
        """
tenant: aster
configuration: journeys
flows:
  - id: offer_onboarding
    kind: onboarding
    tasks:
      - id: review_offer
        student_step: offer
        title: Review offer
        description: Review your offer.
        task_type: approval
      - id: meal_plan
        title: Choose a meal plan
        description: Choose a dining plan.
        task_type: single_select
"""
    )
    repaired = yaml.safe_load(yaml.safe_dump(previous, sort_keys=False))
    repaired["flows"][0]["tasks"][1]["options"] = ["Unlimited", "Commuter"]

    parsed = parse_managed_configuration(
        "journeys",
        yaml.safe_dump(repaired, sort_keys=False),
        tenant_slug="aster",
    )
    _validate_core_onboarding_invariants(parsed, previous)

    fallback = _validated_fallback(
        "journeys",
        _fallback(yaml.safe_dump(previous, sort_keys=False)),
        validate_materialized_inputs=False,
    )
    assert fallback["version"] == 1

    with pytest.raises(ApiError, match="at least two selection options"):
        parse_managed_configuration(
            "journeys",
            yaml.safe_dump(previous, sort_keys=False),
            tenant_slug="aster",
        )


def test_material_change_signature_ignores_reorder_only_changes() -> None:
    task = {
        "kind": "enrollment",
        "title": "Choose housing",
        "description": "Choose one option.",
        "owner": "Housing",
        "required": True,
        "priority": 20,
        "dependsOn": [],
        "dueOffsetDays": None,
        "initialProgressPercent": 0,
        "submissionType": "form",
        "interactionType": "single_select",
        "inputConfig": {"options": ["Campus", "Commute"]},
        "displayOrder": 10,
    }
    reordered = {**task, "displayOrder": 90}
    retitled = {**task, "title": "Confirm housing"}
    reprioritized = {**task, "priority": 80}

    assert _journey_task_material_signature(task) == _journey_task_material_signature(reordered)
    assert _journey_task_material_signature(task) != _journey_task_material_signature(retitled)
    assert _journey_task_material_signature(task) != _journey_task_material_signature(reprioritized)


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
    image_url: http://localhost:4000/v1/media/12345678-1234-4234-8234-123456789abc.jpg
    image_alt: Students meeting at the welcome picnic
    advertisement_starts_at: 2027-07-01T12:00:00Z
    advertisement_ends_at: 2027-08-28T17:00:00Z
""",
        tenant_slug="aster",
    )

    event = campus_events(document)[0]

    assert event["sourceId"] == "welcome-picnic"
    assert event["starts_at"] == datetime(2027, 8, 28, 18, tzinfo=UTC)
    assert event["ends_at"] == datetime(2027, 8, 28, 20, tzinfo=UTC)
    assert event["image_alt"] == "Students meeting at the welcome picnic"
    assert event["advertisement_starts_at"] == datetime(2027, 7, 1, 12, tzinfo=UTC)
    assert event["advertisement_ends_at"] == datetime(2027, 8, 28, 17, tzinfo=UTC)


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


def test_staff_journey_builder_migration_preserves_evidence_and_backfills_types() -> None:
    migration = (
        Path(__file__).parents[1] / "migrations" / "0022_staff_journey_flow_builder.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN flow_kind" in migration
    assert "ADD COLUMN interaction_type" in migration
    assert "ADD COLUMN input_config" in migration
    assert "ADD COLUMN onboarding_required" in migration
    assert "ADD COLUMN retired_at" in migration
    assert "ADD COLUMN retired_reason" in migration
    assert "WHEN 'document' THEN 'upload_file'" in migration
    assert "jsonb_array_elements" in migration
    assert "WHERE code='system_zero_step_enrollment'" in migration


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
                "options": ["Standard", "Vegetarian"],
                "owner": "Dining Services",
                "required": True,
                "points": 20,
                "due_days_after_acceptance": 10,
            }
        )
        onboarding["tasks"].append(
            {
                "id": "temporary_welcome_task",
                "title": "Temporary welcome task",
                "description": "A draft task used to verify safe published deletion.",
                "task_type": "information",
                "owner": "Enrollment Operations",
                "required": False,
                "points": 10,
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

        dependency_document = yaml.safe_load(yaml.safe_dump(journey_document, sort_keys=False))
        dependency_onboarding = next(
            flow for flow in dependency_document["flows"] if flow.get("kind") == "onboarding"
        )
        temporary_task = next(
            task
            for task in dependency_onboarding["tasks"]
            if task.get("id") == "temporary_welcome_task"
        )
        temporary_task["depends_on"] = ["choose_a_meal_plan"]
        await repository.publish(
            staff_auth,
            "journeys",
            {
                "yaml": yaml.safe_dump(dependency_document, sort_keys=False),
                "expectedVersion": 2,
            },
            _fallback(journey_yaml),
            "managed-journey-dependency-added-integration",
        )
        async with engine.connect() as connection:
            blocked_status = await connection.scalar(
                text(
                    """
                    SELECT requirement.status
                    FROM student_requirement requirement
                    JOIN enrollment_journey journey ON journey.id=requirement.journey_id
                    JOIN requirement_definition_version definition
                      ON definition.id=requirement.requirement_definition_version_id
                    WHERE requirement.tenant_id=CAST(:tenant_id AS uuid)
                      AND journey.student_id=CAST(:student_id AS uuid)
                      AND definition.code='temporary_welcome_task'
                      AND requirement.retired_at IS NULL
                    """
                ),
                {"tenant_id": ASTER_TENANT_ID, "student_id": ASTER_STUDENT_ID},
            )
        assert blocked_status == "blocked"

        temporary_task.pop("depends_on")
        await repository.publish(
            staff_auth,
            "journeys",
            {
                "yaml": yaml.safe_dump(dependency_document, sort_keys=False),
                "expectedVersion": 3,
            },
            _fallback(journey_yaml),
            "managed-journey-dependency-removed-integration",
        )
        async with engine.connect() as connection:
            ready_status = await connection.scalar(
                text(
                    """
                    SELECT requirement.status
                    FROM student_requirement requirement
                    JOIN enrollment_journey journey ON journey.id=requirement.journey_id
                    JOIN requirement_definition_version definition
                      ON definition.id=requirement.requirement_definition_version_id
                    WHERE requirement.tenant_id=CAST(:tenant_id AS uuid)
                      AND journey.student_id=CAST(:student_id AS uuid)
                      AND definition.code='temporary_welcome_task'
                      AND requirement.retired_at IS NULL
                    """
                ),
                {"tenant_id": ASTER_TENANT_ID, "student_id": ASTER_STUDENT_ID},
            )
        assert ready_status == "ready"

        portal = PostgresPortalRepository(engine)
        meal_requirement = await portal.get_student_requirement(
            student_auth,
            "choose_a_meal_plan",
        )
        completed_meal = await portal.submit_student_requirement_response(
            student_auth,
            "choose_a_meal_plan",
            {
                "expectedVersion": meal_requirement["version"],
                "response": {"selectedOption": "Vegetarian"},
            },
            "meal-response-integration",
            "meal-response-request-integration",
        )
        assert completed_meal["status"] == "completed"
        assert completed_meal["response"]["data"] == {"selectedOption": "Vegetarian"}

        next_document = yaml.safe_load(yaml.safe_dump(journey_document, sort_keys=False))
        next_onboarding = next(
            flow for flow in next_document["flows"] if flow.get("kind") == "onboarding"
        )
        for task in next_onboarding["tasks"]:
            if task.get("id") == "choose_a_meal_plan":
                task["active"] = False
        next_onboarding["tasks"] = [
            task for task in next_onboarding["tasks"] if task.get("id") != "temporary_welcome_task"
        ]
        await repository.publish(
            staff_auth,
            "journeys",
            {
                "yaml": yaml.safe_dump(next_document, sort_keys=False),
                "expectedVersion": 4,
            },
            _fallback(journey_yaml),
            "managed-journey-retirement-integration",
        )

        updates_after_retirement = await repository.list_student_updates(student_auth)
        assert not {
            "Choose a meal plan",
            "Temporary welcome task",
        } & {str(update["title"]) for update in updates_after_retirement}
        async with engine.connect() as connection:
            retirement = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                              (SELECT COUNT(*) FROM student_requirement requirement
                               JOIN requirement_definition_version definition
                                 ON definition.id=requirement.requirement_definition_version_id
                               WHERE requirement.tenant_id=CAST(:tenant_id AS uuid)
                                 AND definition.code='choose_a_meal_plan'
                                 AND requirement.retired_reason='inactive') AS inactive_count,
                              (SELECT COUNT(*) FROM student_requirement requirement
                               JOIN requirement_definition_version definition
                                 ON definition.id=requirement.requirement_definition_version_id
                               WHERE requirement.tenant_id=CAST(:tenant_id AS uuid)
                                 AND definition.code='choose_a_meal_plan'
                                 AND requirement.status='completed'
                                 AND requirement.progress_percent=100
                                 AND requirement.retired_reason='inactive') AS completed_evidence,
                              (SELECT COUNT(*) FROM student_requirement requirement
                               JOIN requirement_definition_version definition
                                 ON definition.id=requirement.requirement_definition_version_id
                               WHERE requirement.tenant_id=CAST(:tenant_id AS uuid)
                                 AND definition.code='temporary_welcome_task'
                                 AND requirement.retired_reason='deleted') AS deleted_count,
                              (SELECT COUNT(*) FROM tenant_reward_rule
                               WHERE tenant_id=CAST(:tenant_id AS uuid)
                                 AND code IN ('choose_a_meal_plan','temporary_welcome_task')
                                 AND enabled=true) AS enabled_rewards,
                              (SELECT COUNT(*) FROM student_experience_update update_notice
                               JOIN student_requirement requirement
                                 ON requirement.id=update_notice.requirement_id
                               JOIN requirement_definition_version definition
                                 ON definition.id=requirement.requirement_definition_version_id
                               WHERE update_notice.tenant_id=CAST(:tenant_id AS uuid)
                                 AND definition.code IN (
                                   'choose_a_meal_plan','temporary_welcome_task'
                                 )
                                 AND update_notice.status IN ('pending','deferred'))
                                 AS stale_updates
                            """
                        ),
                        {"tenant_id": ASTER_TENANT_ID},
                    )
                )
                .mappings()
                .one()
            )
        assert int(retirement["inactive_count"]) >= 1
        assert retirement["completed_evidence"] == 1
        assert int(retirement["deleted_count"]) >= 1
        assert retirement["enabled_rewards"] == 0
        assert retirement["stale_updates"] == 0

        core_only_document = yaml.safe_load(yaml.safe_dump(next_document, sort_keys=False))
        for flow in core_only_document["flows"]:
            for task in flow.get("tasks", []):
                if task.get("student_step") is None:
                    task["active"] = False
        await repository.publish(
            staff_auth,
            "journeys",
            {
                "yaml": yaml.safe_dump(core_only_document, sort_keys=False),
                "expectedVersion": 5,
            },
            _fallback(journey_yaml),
            "managed-journey-zero-active-integration",
        )
        async with engine.connect() as connection:
            zero_active = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT definition.onboarding_required,
                                   COUNT(link.requirement_definition_version_id) AS link_count
                            FROM journey_definition_version definition
                            LEFT JOIN journey_requirement_definition link
                              ON link.journey_definition_version_id=definition.id
                            WHERE definition.tenant_id=CAST(:tenant_id AS uuid)
                              AND definition.active=1
                            GROUP BY definition.id
                            """
                        ),
                        {"tenant_id": ASTER_TENANT_ID},
                    )
                )
                .mappings()
                .one()
            )
        assert zero_active["onboarding_required"] is True
        assert zero_active["link_count"] == 0
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
