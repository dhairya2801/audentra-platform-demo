from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.preview.staff_workspace import PreviewStaffWorkspaceRepository

pytestmark = pytest.mark.anyio

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
STAFF_ID = "00000000-0000-7000-8000-000000000901"
REVIEWER_ID = "00000000-0000-7000-8000-000000000902"
WORK_ITEM_ID = "00000000-0000-7000-8000-000000000911"
CLUB_ID = "51000000-0000-7000-8000-000000000101"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def auth(*, tenant_id: str = TENANT_ID, actor_type: str = "staff") -> AuthContext:
    return AuthContext(
        tenant_id=tenant_id,
        student_id=STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type=actor_type,  # type: ignore[arg-type]
        tenant_slug="aster",
    )


def action_center() -> dict[str, Any]:
    staff = [
        {
            "id": STAFF_ID,
            "name": "Priya Shah",
            "email": "priya.shah@aster.example.edu",
            "component": "Admissions",
        },
        {
            "id": REVIEWER_ID,
            "name": "Marcus Lee",
            "email": "marcus.lee@aster.example.edu",
            "component": "Registrar",
        },
    ]
    item = {
        "id": WORK_ITEM_ID,
        "key": "ENR-104",
        "title": "Review onboarding choices",
        "description": "Confirm the latest choices.",
        "status": "todo",
        "priority": "high",
        "type": "enrollment",
        "component": "Admissions",
        "dueAt": "2027-07-29T17:00:00.000Z",
        "escalated": False,
        "version": 1,
        "createdAt": "2026-07-24T00:00:00.000Z",
        "updatedAt": "2026-07-24T00:00:00.000Z",
        "assignee": staff[0],
        "student": {
            "id": STUDENT_ID,
            "name": "Alex Morgan",
            "preferredName": "Alex",
            "programName": "Computer Science",
            "classYear": 2027,
        },
        "source": {"type": "onboarding", "id": STUDENT_ID},
        "history": [],
    }
    return {
        "items": [item],
        "staff": staff,
        "counts": {
            "todo": 1,
            "inProgress": 0,
            "done": 0,
            "urgent": 0,
            "escalated": 0,
        },
        "generatedAt": "2026-07-24T00:00:00.000Z",
    }


def student_record() -> dict[str, Any]:
    return {
        "student": action_center()["items"][0]["student"],
        "onboarding": {
            "status": "in_progress",
            "currentStep": "offer",
            "completedAt": None,
            "completedSteps": [],
            "data": {},
            "version": 1,
            "updatedAt": "2026-07-24T00:00:00.000Z",
        },
        "profile": {
            "studentId": STUDENT_ID,
            "preferredName": "Alex",
            "firstName": "Alex",
            "lastName": "Morgan",
            "classYear": 2027,
            "email": "alex.morgan@example.com",
            "pronouns": None,
            "mobilePhone": None,
            "communicationPreference": "email",
            "version": 1,
            "updatedAt": "2026-07-24T00:00:00.000Z",
        },
        "requirements": {"items": [], "total": 0, "generatedAt": "2026-07-24T00:00:00.000Z"},
        "documents": {"items": [], "total": 0},
    }


def campus_life() -> dict[str, Any]:
    return {
        "events": [
            {
                "id": "50000000-0000-7000-8000-000000000101",
                "title": "Welcome Week",
                "description": "Meet the community.",
                "startsAt": "2027-08-28T18:00:00.000Z",
                "endsAt": "2027-08-28T21:00:00.000Z",
                "location": "University Green",
                "category": "social",
                "featured": True,
                "accent": "gold",
            }
        ],
        "clubs": [
            {
                "id": CLUB_ID,
                "name": "Aster Robotics",
                "category": "Engineering & Technology",
                "description": "Build robots together.",
                "contactName": "Maya Chen",
                "contactRole": "Club President",
                "contactChannel": "robotics@aster.edu",
                "latestUpdate": "Teams are open.",
                "nextActivity": None,
                "imageUrl": "/media/clubs/robotics.jpg",
                "imageAlt": "Students building a robot",
                "imageAttribution": "Preview fixture",
                "imageSourceUrl": "",
                "membershipOpen": True,
                "events": [],
            }
        ],
        "generatedAt": "2026-07-24T00:00:00.000Z",
    }


def uuid_factory() -> Iterator[UUID]:
    for suffix in range(1, 20):
        yield UUID(f"70000000-0000-7000-8000-{suffix:012d}")


def repository(*, enabled: bool = True) -> PreviewStaffWorkspaceRepository:
    identifiers = uuid_factory()
    return PreviewStaffWorkspaceRepository(
        enabled=enabled,
        clock=lambda: datetime(2026, 8, 2, 20, 0, tzinfo=UTC),
        uuid_factory=lambda: next(identifiers),
    )


def test_default_configuration_root_resolves_packaged_tenant_assets() -> None:
    root = PreviewStaffWorkspaceRepository._resolve_configuration_root(None)

    assert root is not None
    assert (
        root.resolve()
        == (Path(__file__).resolve().parents[1] / "assets" / "config" / "tenants").resolve()
    )
    assert (root / "aster" / "journeys.yaml").is_file()
    assert (root / "harvard" / "journeys.yaml").is_file()


async def workspace(repo: PreviewStaffWorkspaceRepository) -> dict[str, Any]:
    return await repo.get_workspace(
        auth(),
        action_center=action_center(),
        student=student_record(),
        campus_life=campus_life(),
    )


async def test_workspace_merges_canonical_help_requests_into_preview_inquiries() -> None:
    repo = repository()
    result = await repo.get_workspace(
        auth(),
        action_center=action_center(),
        student=student_record(),
        campus_life=campus_life(),
        canonical_inquiries=[
            {
                "id": "70000000-0000-7000-8000-000000000099",
                "student": action_center()["items"][0]["student"],
                "topicCode": "financial_aid",
                "subject": "Help request",
                "message": "Could someone explain my aid offer?",
                "status": "new",
                "priority": "normal",
                "assigneeId": None,
                "createdAt": "2026-08-02T20:00:00.000Z",
                "updatedAt": "2026-08-02T20:00:00.000Z",
                "version": 1,
            }
        ],
    )

    assert [item["id"] for item in result["inquiries"]] == [
        "00000000-0000-7000-8000-000000000951",
        "70000000-0000-7000-8000-000000000099",
    ]
    assert result["portalInventory"][4 + 1]["recordCount"] == 2


async def test_workspace_composes_canonical_reads_with_deterministic_preview() -> None:
    result = await workspace(repository())

    assert result["currentStaff"]["name"] == "Priya Shah"
    assert result["actionCenter"]["items"][0]["id"] == WORK_ITEM_ID
    assert result["student"]["profile"]["studentId"] == STUDENT_ID
    assert len(result["cohort"]) == 400
    assert result["cohortSeed"] == {
        "synthetic": True,
        "count": 400,
        "purpose": "Deterministic staff workflow and scale testing only",
        "generatedAt": "2026-07-24T00:00:00.000Z",
        "tenantSlug": "aster",
    }
    assert result["personalActionCenter"]["counts"]["studentsToday"] == 30
    assert len(result["knowledgeBase"]) == 3
    assert len(result["corePlays"]) == 2
    assert result["capabilities"]["externalOutreach"] == "simulation_only"
    assert result["configurations"]["journeys"]["version"] == 1
    assert result["academicCatalog"]["courses"]


async def test_versioned_content_mutations_reject_stale_writes_and_are_tenant_isolated() -> None:
    repo = repository()
    card = await repo.update_knowledge_card(
        auth(),
        "00000000-0000-7000-8000-000000000931",
        {
            "expectedVersion": 1,
            "title": "Updated deposit policy",
            "summary": "Updated approved guidance.",
            "body": "Confirm the deadline and document the decision.",
            "category": "Enrollment",
            "audience": "internal",
            "status": "published",
        },
    )
    assert card["version"] == 2

    with pytest.raises(ApiError) as stale:
        await repo.update_knowledge_card(
            auth(),
            card["id"],
            {
                "expectedVersion": 1,
                "title": "Stale",
                "summary": "Stale summary",
                "body": "Stale body",
                "category": "Enrollment",
                "audience": "internal",
                "status": "draft",
            },
        )
    assert stale.value.status_code == 409
    assert stale.value.code == "VERSION_CONFLICT"

    foreign = await repo.get_managed_configuration(
        auth(tenant_id="00000000-0000-7000-8000-000000000099"), "journeys"
    )
    assert foreign["version"] == 1
    original = await repo.get_managed_configuration(auth(), "journeys")
    assert original["version"] == 1


async def test_configuration_draft_requires_publish_and_refreshes_workspace_projection() -> None:
    repo = repository()
    draft = await repo.draft_managed_configuration(
        auth(),
        {
            "kind": "journeys",
            "expectedVersion": 1,
            "instruction": "Change Review your offer to 75 points.",
        },
    )
    before = await workspace(repo)
    assert (
        next(item for item in before["journeyBlueprint"] if item["id"] == "review_offer")["points"]
        != 75
    )

    published = await repo.update_managed_configuration(
        auth(),
        "journeys",
        {
            "expectedVersion": draft["expectedVersion"],
            "yaml": draft["yaml"],
            "changeSummary": draft["summary"],
        },
    )
    after = await workspace(repo)

    assert published["version"] == 2
    assert (
        next(item for item in after["journeyBlueprint"] if item["id"] == "review_offer")["points"]
        == 75
    )
    assert after["configurations"]["journeys"]["version"] == 2


async def test_configuration_draft_can_add_a_supplemental_onboarding_task() -> None:
    repo = repository()

    draft = await repo.draft_managed_configuration(
        auth(),
        {
            "kind": "journeys",
            "expectedVersion": 1,
            "instruction": (
                'Add "Choose a meal plan" to onboarding as a single selection worth 20 points.'
            ),
        },
    )

    assert draft["warnings"] == []
    assert draft["changes"] == ["Added Choose a meal plan to onboarding."]
    assert "id: choose_a_meal_plan" in draft["yaml"]
    assert "task_type: single_select" in draft["yaml"]
    assert "- Unlimited dining" in draft["yaml"]
    assert "- Commuter plan" in draft["yaml"]
    assert "points: 20" in draft["yaml"]


async def test_configuration_draft_refuses_a_selection_without_explicit_options() -> None:
    draft = await repository().draft_managed_configuration(
        auth(),
        {
            "kind": "journeys",
            "expectedVersion": 1,
            "instruction": 'Add "Choose a lab section" to enrollment as a single selection.',
        },
    )

    assert draft["changes"] == []
    assert draft["warnings"] == [
        "Choose a lab section needs at least two explicit option values before it can be published."
    ]
    assert "choose_a_lab_section" not in draft["yaml"]


async def test_club_outreach_and_inquiry_mutations_remain_preview_only() -> None:
    repo = repository()
    club = await repo.update_club(
        auth(),
        CLUB_ID,
        {
            "expectedVersion": 1,
            "name": "Aster Robotics Lab",
            "category": "Engineering & Technology",
            "description": "Build autonomous robots together.",
            "latestUpdate": "New teams are open.",
            "contactName": "Maya Chen",
            "contactRole": "Club President",
            "contactChannel": "robotics@aster.edu",
            "membershipOpen": True,
        },
        campus_life=campus_life(),
    )
    run = await repo.simulate_outreach(
        auth(),
        {
            "title": "Deposit reminder",
            "audience": "Students with a deadline in 72 hours",
            "channel": "sms",
            "requestedCount": 25,
        },
    )
    inquiry = await repo.update_inquiry(
        auth(),
        "00000000-0000-7000-8000-000000000951",
        {
            "expectedVersion": 1,
            "status": "open",
            "assigneeId": STAFF_ID,
            "responseNote": "I will confirm the transcript policy.",
            "notifyStudent": True,
        },
        staff=action_center()["staff"],
    )
    result = await workspace(repo)

    assert club["version"] == 2
    assert (
        next(item for item in result["campusLife"]["clubs"] if item["id"] == CLUB_ID)["name"]
        == "Aster Robotics Lab"
    )
    assert run["status"] == "simulation_only"
    assert result["outreachRuns"][0]["id"] == run["id"]
    assert inquiry["assignee"]["id"] == STAFF_ID
    assert result["inquiries"][0]["status"] == "open"


async def test_edward_preview_never_executes_a_write() -> None:
    repo = repository()
    response = await repo.preview_edward(
        auth(), {"message": "Create an outreach email for this cohort and change the journey"}
    )

    assert response["executionMode"] == "preview_only"
    assert {item["capability"] for item in response["plan"]} == {
        "read_student_data",
        "update_journey",
        "draft_message",
        "launch_outreach",
    }
    assert (await workspace(repo))["outreachRuns"] == []


async def test_preview_is_fail_closed_when_disabled_or_called_by_student() -> None:
    with pytest.raises(ApiError) as disabled:
        await repository(enabled=False).get_managed_configuration(auth(), "journeys")
    assert disabled.value.status_code == 404
    assert disabled.value.code == "STAFF_PREVIEW_DISABLED"

    with pytest.raises(ApiError) as denied:
        await repository().get_managed_configuration(auth(actor_type="student"), "journeys")
    assert denied.value.status_code == 403
    assert denied.value.code == "STAFF_ACCESS_REQUIRED"
