from __future__ import annotations

import inspect
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

import pytest
import yaml  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.application.staff_workspace import (
    compose_staff_workspace,
    draft_managed_configuration,
    preview_edward,
    simulate_outreach,
)
from audentra.contracts.requests import UpdateStaffManagedConfigurationRequest
from audentra.core.auth import AuthContext
from audentra.core.errors import BadRequestError, ConflictError, NotFoundError
from audentra.infrastructure.postgres import postgres_service
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
    managed_configuration_input,
)

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
STAFF_ID = "00000000-0000-7000-8000-000000000901"


def _auth() -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type="staff",
        tenant_slug="aster",
    )


def _academics_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "tenant": "aster",
        "configuration": "academics",
        "catalog_version": "test.v1",
        "courses": [],
    }


def _workspace_configurations() -> dict[str, dict[str, object]]:
    return {
        "journeys": {
            "kind": "journeys",
            "version": 4,
            "document": {
                "configuration": "journeys",
                "flows": [
                    {
                        "id": "enrollment",
                        "kind": "enrollment",
                        "title": "Enrollment",
                        "status": "published",
                        "tasks": [
                            {
                                "id": "choose_meal_plan",
                                "title": "Choose a meal plan",
                                "description": "Choose one dining option.",
                                "task_type": "single_select",
                                "options": ["Unlimited", "Commuter"],
                                "required": True,
                                "points": 20,
                            }
                        ],
                    }
                ],
            },
        },
        "campus_life": {
            "kind": "campus_life",
            "version": 2,
            "document": {"configuration": "campus_life", "events": []},
        },
        "academics": {
            "kind": "academics",
            "version": 3,
            "document": {
                "configuration": "academics",
                "catalog_version": "2028.v1",
                "courses": [
                    {
                        "id": "cs-105",
                        "code": "CS 105",
                        "title": "Foundations",
                        "description": "Programming foundations.",
                        "credits": 4,
                        "level": 100,
                        "prerequisites": [],
                        "instructor_names": ["Dr. Ada Rivera"],
                        "meeting_pattern": "Mon/Wed",
                        "availability_label": "Fall",
                        "source_url": "https://catalog.example.edu/cs-105",
                        "related_videos": [
                            {
                                "id": "intro",
                                "title": "Course introduction",
                                "url": "https://www.youtube.com/watch?v=example",
                                "source_label": "Open course media",
                            }
                        ],
                    }
                ],
            },
        },
    }


class _EmptyMappings:
    def first(self) -> None:
        return None


class _EmptyResult:
    def mappings(self) -> _EmptyMappings:
        return _EmptyMappings()


class _EmptyConnection:
    async def execute(
        self, _statement: object, _parameters: Mapping[str, object] | None = None
    ) -> _EmptyResult:
        return _EmptyResult()


class _ConnectionContext(AbstractAsyncContextManager[_EmptyConnection]):
    async def __aenter__(self) -> _EmptyConnection:
        return _EmptyConnection()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _EmptyEngine:
    def connect(self) -> _ConnectionContext:
        return _ConnectionContext()


@pytest.mark.anyio
async def test_missing_managed_configuration_fails_without_a_file_fallback() -> None:
    repository = PostgresManagedConfigurationRepository(cast(AsyncEngine, _EmptyEngine()))

    with pytest.raises(NotFoundError) as raised:
        await repository.get(_auth(), "academics")

    assert raised.value.code == "MANAGED_CONFIGURATION_NOT_PROVISIONED"


def test_structured_document_is_canonical_and_yaml_is_import_only() -> None:
    document = _academics_document()
    parsed, audit_yaml = managed_configuration_input(
        "academics",
        {"document": document},
        tenant_slug="aster",
    )

    assert parsed == document
    assert yaml.safe_load(audit_yaml) == document
    request = UpdateStaffManagedConfigurationRequest(
        expectedVersion=1,
        document=document,
    )
    assert request.document == document

    with pytest.raises(BadRequestError) as raised:
        managed_configuration_input(
            "academics",
            {
                "document": document,
                "yaml": yaml.safe_dump({**document, "catalog_version": "different"}),
            },
            tenant_slug="aster",
        )
    assert raised.value.code == "MANAGED_CONFIGURATION_INPUT_MISMATCH"


def test_draft_returns_a_structured_nonpersistent_document() -> None:
    current: dict[str, Any] = {
        "kind": "academics",
        "version": 3,
        "document": _academics_document(),
    }

    result = draft_managed_configuration(
        _auth(),
        current,
        {
            "expectedVersion": 3,
            "instruction": 'Add course CS 105 called "Foundations" for 4 credits.',
        },
    )

    assert result["persisted"] is False
    assert result["document"]["courses"][0]["code"] == "CS 105"
    assert yaml.safe_load(result["yaml"]) == result["document"]


def test_workspace_is_composed_from_canonical_inputs_without_mutable_history() -> None:
    recommendation = {
        "recommendedToday": True,
        "taskId": "work-1",
    }
    cohort_student = {
        "id": STUDENT_ID,
        "assignedStaffId": STAFF_ID,
        "recommendedAction": recommendation,
        "openWorkItems": 1,
        "overdueWorkItems": 1,
        "attention": {
            "level": "urgent",
            "signals": [
                {"code": "escalated_work", "label": "1 escalated staff action", "count": 1}
            ],
            "evaluatedAt": "2028-01-02T03:04:05.000Z",
        },
    }
    workspace = compose_staff_workspace(
        _auth(),
        action_center={
            "staff": [{"id": STAFF_ID, "name": "Priya Shah"}],
            "items": [
                {
                    "id": "work-1",
                    "status": "in_progress",
                    "assignee": {"id": STAFF_ID},
                    "student": {"id": STUDENT_ID},
                }
            ],
        },
        student={"student": {"id": STUDENT_ID, "name": "Alex Morgan"}},
        campus_life={"events": [{"id": "event-1"}], "clubs": [{"id": "club-1"}]},
        inquiries=[{"id": "inquiry-1", "assigneeId": STAFF_ID}],
        cohort=[cohort_student],
        managed_content={
            "knowledgeBase": [{"id": "card-1", "audience": "student"}],
            "corePlays": [{"id": "play-1"}],
        },
        configurations=_workspace_configurations(),
        generated_at="2028-01-02T03:04:05.000Z",
    )

    assert workspace["currentStaff"]["id"] == STAFF_ID
    # The personal queue is the reader's own open work from the board read,
    # never a roster derivation; counts fall back to the page when a store
    # serves no SQL scope counts.
    assert workspace["personalActionCenter"]["tasks"] == [
        {
            "id": "work-1",
            "status": "in_progress",
            "assignee": {"id": STAFF_ID},
            "student": {"id": STUDENT_ID},
        }
    ]
    assert workspace["personalActionCenter"]["students"] == [cohort_student]
    assert workspace["personalActionCenter"]["counts"] == {
        "open": 1,
        "overdue": 0,
        "dueToday": 0,
        "urgent": 0,
        "escalated": 0,
        "todo": 0,
        "inProgress": 1,
        "followUpRequired": 0,
        "blocked": 0,
        "stale": 0,
        "students": 1,
    }
    assert workspace["personalActionCenter"]["queue"] == {
        "total": 1,
        "limit": 1,
        "hasMore": False,
        "sort": "attention",
    }
    assert workspace["student"]["operation"] == cohort_student
    assert workspace["inquiries"][0]["assignee"]["id"] == STAFF_ID
    assert workspace["journeyBlueprint"][0]["inputConfig"]["options"] == [
        "Unlimited",
        "Commuter",
    ]
    assert workspace["academicCatalog"]["courses"][0]["relatedVideos"][0]["url"].startswith(
        "https://www.youtube.com/"
    )
    assert workspace["outreachRuns"] == []
    assert workspace["capabilities"]["managedYaml"] == "import_only"
    assert workspace["capabilities"]["managedDocument"] is True
    assert {item["id"]: item["recordCount"] for item in workspace["portalInventory"]}[
        "campus_life"
    ] == 2


def test_workspace_requires_the_authenticated_staff_and_all_db_documents() -> None:
    common: dict[str, Any] = {
        "action_center": {"staff": [], "items": []},
        "student": {},
        "campus_life": {"events": [], "clubs": []},
        "inquiries": [],
        "cohort": [],
        "managed_content": {},
        "configurations": _workspace_configurations(),
        "generated_at": "2028-01-02T03:04:05.000Z",
    }
    with pytest.raises(NotFoundError) as missing_staff:
        compose_staff_workspace(_auth(), **common)
    assert missing_staff.value.code == "STAFF_IDENTITY_NOT_PROVISIONED"

    common["action_center"] = {"staff": [{"id": STAFF_ID}], "items": []}
    common["configurations"] = {"journeys": _workspace_configurations()["journeys"]}
    with pytest.raises(NotFoundError) as missing_configuration:
        compose_staff_workspace(_auth(), **common)
    assert missing_configuration.value.code == "MANAGED_CONFIGURATION_NOT_PROVISIONED"


def test_draft_conflict_and_stateless_staff_previews_are_explicit() -> None:
    with pytest.raises(ConflictError) as conflict:
        draft_managed_configuration(
            _auth(),
            {"kind": "academics", "version": 3, "document": _academics_document()},
            {"expectedVersion": 2, "instruction": "Add a course."},
        )
    assert conflict.value.code == "VERSION_CONFLICT"

    outreach = simulate_outreach(
        _auth(),
        {
            "title": "Deadline reminder",
            "audience": "Students with open deposits",
            "channel": "email",
            "requestedCount": 12,
        },
    )
    assert outreach["status"] == "simulation_only"
    assert outreach["persisted"] is False
    assert outreach["createdBy"] == STAFF_ID

    preview = preview_edward(
        _auth(),
        {"message": "Update a course, draft a message, and launch cohort outreach"},
    )
    assert preview["executionMode"] == "preview_only"
    assert preview["persisted"] is False
    assert [step["status"] for step in preview["plan"]] == [
        "available",
        "needs_confirmation",
        "needs_confirmation",
        "simulation_only",
    ]


def test_production_service_has_no_preview_workspace_import_or_state() -> None:
    source = inspect.getsource(postgres_service)

    assert "PreviewStaffWorkspaceRepository" not in source
    assert "infrastructure.preview" not in source
    assert "staff_preview" not in source
