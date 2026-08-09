# ruff: noqa: S608 -- random isolated test-schema identifiers are controlled here.
from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.db.engine import DatabaseEngineOptions, create_database_engine
from audentra.infrastructure.postgres.staff_repository import (
    PostgresStaffRepository,
    _summary_public_state,
)

TENANT_ID = "00000000-0000-7000-8000-000000000001"
FOREIGN_TENANT_ID = "00000000-0000-7000-8000-000000000099"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
STAFF_ID = "00000000-0000-7000-8000-000000000901"
WORK_ITEM_ID = "00000000-0000-7000-8000-000000000911"
DOCUMENT_ID = "00000000-0000-7000-8000-000000000701"
REQUIREMENT_ID = "00000000-0000-7000-8000-000000000401"
REWARD_RULE_ID = "00000000-0000-7000-8000-000000000461"
NOW = datetime(2026, 7, 24, 12, tzinfo=UTC)


class FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, object]:
        if len(self._rows) != 1:
            raise AssertionError("Expected exactly one mapping row")
        return self._rows[0]


class FakeResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeMappings:
        return FakeMappings(self._rows)

    def scalar_one(self) -> object:
        if len(self._rows) != 1 or len(self._rows[0]) != 1:
            raise AssertionError("Expected exactly one scalar result")
        return next(iter(self._rows[0].values()))

    def scalar_one_or_none(self) -> object | None:
        if not self._rows:
            return None
        return self.scalar_one()

    def one(self) -> dict[str, object]:
        return self.mappings().one()


FakeHandler = Callable[[str, dict[str, object]], FakeResult]


class FakeConnection:
    def __init__(self, handler: FakeHandler) -> None:
        self.handler = handler
        self.executions: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self, statement: object, parameters: Mapping[str, object] | None = None
    ) -> FakeResult:
        sql = str(statement)
        values = dict(parameters or {})
        self.executions.append((sql, values))
        return self.handler(sql, values)


class FakeContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.begin_count = 0

    def connect(self) -> FakeContext:
        return FakeContext(self.connection)

    def begin(self) -> FakeContext:
        self.begin_count += 1
        return FakeContext(self.connection)


class FakeStudentReader:
    async def get_student_onboarding(self, auth: AuthContext) -> Mapping[str, object]:
        return {"version": 1}

    async def get_student_profile(self, auth: AuthContext) -> Mapping[str, object]:
        return {"version": 1}

    async def get_student_requirements(self, auth: AuthContext) -> Mapping[str, object]:
        return {"items": []}

    async def get_student_documents(self, auth: AuthContext) -> Mapping[str, object]:
        return {
            "items": [
                {
                    "id": DOCUMENT_ID,
                    "fileName": "transcript.pdf",
                    "status": "accepted",
                }
            ]
        }


class ReloadingRepository(PostgresStaffRepository):
    async def _require_work_item(self, auth: AuthContext, work_item_id: str) -> dict[str, object]:
        return {
            "id": work_item_id,
            "status": "done",
            "version": 2,
            "student": {"id": STUDENT_ID},
        }


class PreferenceReloadingRepository(PostgresStaffRepository):
    async def get_student_record(self, auth: AuthContext, student_id: str) -> dict[str, object]:
        return {
            "student": {"id": student_id},
            "onboarding": {"version": 2},
            "profile": {"version": 2, "communicationPreference": "sms"},
            "requirements": {"items": []},
            "documents": {"items": []},
        }


class ActionDetailReloadingRepository(PostgresStaffRepository):
    async def get_work_item_detail(self, auth: AuthContext, work_item_id: str) -> dict[str, object]:
        del auth
        return {"workItem": {"id": work_item_id}}


class WorkspaceReadRepository(PostgresStaffRepository):
    async def _ensure_document_work_items(self, auth: AuthContext) -> None:
        del auth


def staff_auth(
    *,
    tenant_id: str = TENANT_ID,
    actor_type: Literal["student", "staff"] = "staff",
) -> AuthContext:
    return AuthContext(
        tenant_id=tenant_id,
        student_id=STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type=actor_type,
    )


def test_staff_guard_runs_before_any_database_access() -> None:
    def unexpected(sql: str, values: dict[str, object]) -> FakeResult:
        raise AssertionError(sql)

    connection = FakeConnection(unexpected)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader()
    )

    with pytest.raises(ApiError) as raised:
        asyncio.run(repository.get_action_center(staff_auth(actor_type="student")))

    assert raised.value.code == "STAFF_ACCESS_REQUIRED"
    assert connection.executions == []


def _knowledge_row(*, version: int = 1) -> dict[str, object]:
    return {
        "id": UUID(WORK_ITEM_ID),
        "title": "Transcript review expectations",
        "summary": "How document review works.",
        "body": "Review extracted fields before confirming a decision.",
        "category": "Documents",
        "audience": "student",
        "status": "published",
        "owner_name": "Marcus Lee",
        "version": version,
        "updated_at": NOW,
    }


def _core_play_row(*, version: int = 1) -> dict[str, object]:
    return {
        "id": UUID(WORK_ITEM_ID),
        "title": "Missing document recovery",
        "description": "Recover a blocking enrollment document.",
        "trigger_description": "A blocking document is overdue",
        "audience": "Students with blocking documents",
        "steps": ["Confirm the reason", "Send resubmission guidance"],
        "status": "active",
        "owner_name": "Marcus Lee",
        "version": version,
        "updated_at": NOW,
    }


def _club_row(*, version: int = 1) -> dict[str, object]:
    return {
        "id": UUID(WORK_ITEM_ID),
        "name": "Code Collective",
        "category": "Technology",
        "description": "Build useful software with other students.",
        "contact_name": "Jordan Lee",
        "contact_role": "Club president",
        "contact_channel": "code@example.edu",
        "latest_update": "Applications are open.",
        "next_activity": None,
        "source_label": "Staff managed campus life",
        "source_url": None,
        "source_status": "tenant_authored",
        "social_links": [],
        "long_description": "Build useful software with other students.",
        "meeting_schedule": None,
        "membership_open": True,
        "version": version,
        "updated_at": NOW,
        "image_url": "/media/clubs/code-collective.jpg",
        "image_alt": "Students collaborating in a campus club",
        "image_attribution": "Aster University",
        "image_source_url": "",
    }


def test_managed_content_read_seeds_defaults_and_maps_durable_rows() -> None:
    def handler(sql: str, _values: dict[str, object]) -> FakeResult:
        if "FROM public.staff_knowledge_card" in sql:
            return FakeResult([_knowledge_row()])
        if "FROM public.staff_core_play" in sql:
            return FakeResult([_core_play_row()])
        return FakeResult()

    connection = FakeConnection(handler)
    engine = FakeEngine(connection)
    repository = PostgresStaffRepository(cast(AsyncEngine, engine), FakeStudentReader())

    result = asyncio.run(repository.get_managed_content(staff_auth()))

    knowledge = cast(list[dict[str, object]], result["knowledgeBase"])
    core_plays = cast(list[dict[str, object]], result["corePlays"])
    assert knowledge[0]["title"] == "Transcript review expectations"
    assert knowledge[0]["updatedAt"] == "2026-07-24T12:00:00.000Z"
    assert core_plays[0]["steps"] == [
        "Confirm the reason",
        "Send resubmission guidance",
    ]
    assert engine.begin_count == 1
    seed_sql = [sql for sql, _values in connection.executions if "ON CONFLICT (id)" in sql]
    assert len(seed_sql) == 2
    assert all("CAST(:tenant_text AS text), chr(58)" in sql for sql in seed_sql)
    assert all(":core-play" not in sql and ":knowledge:" not in sql for sql in seed_sql)


def test_staff_managed_content_crud_is_versioned_audited_and_emits_outbox() -> None:
    club_version = 1

    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        nonlocal club_version
        if "SELECT display_name FROM public.staff_member" in sql:
            return FakeResult([{"display_name": "Marcus Lee"}])
        if "INSERT INTO public.staff_knowledge_card" in sql:
            return FakeResult([_knowledge_row()])
        if "UPDATE public.staff_knowledge_card" in sql:
            return FakeResult([_knowledge_row(version=2)])
        if "INSERT INTO public.staff_core_play" in sql:
            return FakeResult([_core_play_row()])
        if "UPDATE public.staff_core_play" in sql:
            return FakeResult([_core_play_row(version=2)])
        if "INSERT INTO public.student_club" in sql:
            club_version = 1
            return FakeResult([{"id": values["id"]}])
        if "UPDATE public.student_club" in sql:
            club_version = 2
            return FakeResult([{"id": values["id"], "version": club_version}])
        if "FROM public.student_club" in sql:
            return FakeResult([_club_row(version=club_version)])
        return FakeResult()

    connection = FakeConnection(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)),
        FakeStudentReader(),
        clock=lambda: NOW,
        uuid_factory=lambda: UUID(WORK_ITEM_ID),
    )
    knowledge_payload: dict[str, object] = {
        "title": "Transcript review expectations",
        "summary": "How document review works.",
        "body": "Review extracted fields before confirming a decision.",
        "category": "Documents",
        "audience": "student",
        "status": "published",
    }
    core_play_payload: dict[str, object] = {
        "title": "Missing document recovery",
        "description": "Recover a blocking enrollment document.",
        "trigger": "A blocking document is overdue",
        "audience": "Students with blocking documents",
        "steps": ["Confirm the reason", "Send resubmission guidance"],
        "status": "active",
    }
    club_payload: dict[str, object] = {
        "name": "Code Collective",
        "category": "Technology",
        "description": "Build useful software with other students.",
        "contactName": "Jordan Lee",
        "contactRole": "Club president",
        "contactChannel": "code@example.edu",
        "latestUpdate": "Applications are open.",
        "membershipOpen": True,
    }

    created_knowledge = asyncio.run(
        repository.create_knowledge_card(staff_auth(), knowledge_payload, "request-1")
    )
    updated_knowledge = asyncio.run(
        repository.update_knowledge_card(
            staff_auth(),
            WORK_ITEM_ID,
            {**knowledge_payload, "expectedVersion": 1},
            "request-2",
        )
    )
    created_play = asyncio.run(
        repository.create_core_play(staff_auth(), core_play_payload, "request-3")
    )
    updated_play = asyncio.run(
        repository.update_core_play(
            staff_auth(),
            WORK_ITEM_ID,
            {**core_play_payload, "expectedVersion": 1},
            "request-4",
        )
    )
    created_club = asyncio.run(repository.create_club(staff_auth(), club_payload, "request-5"))
    updated_club = asyncio.run(
        repository.update_club(
            staff_auth(),
            WORK_ITEM_ID,
            {**club_payload, "expectedVersion": 1},
            "request-6",
        )
    )

    assert created_knowledge["version"] == 1
    assert updated_knowledge["version"] == 2
    assert created_play["steps"] == ["Confirm the reason", "Send resubmission guidance"]
    assert updated_play["version"] == 2
    assert created_club["version"] == 1
    assert updated_club["version"] == 2
    audit_actions = {
        values["action"]
        for sql, values in connection.executions
        if "INSERT INTO public.audit_event" in sql
    }
    assert audit_actions == {
        "staff.knowledge_card_created",
        "staff.knowledge_card_updated",
        "staff.core_play_created",
        "staff.core_play_updated",
        "staff.club_created",
        "staff.club_updated",
    }
    outbox_events = {
        values["event_name"]
        for sql, values in connection.executions
        if "INSERT INTO public.outbox_event" in sql
    }
    assert outbox_events == {f"{action}.v1" for action in audit_actions}


def test_club_create_rejects_images_outside_tenant_media_library() -> None:
    def handler(sql: str, _values: dict[str, object]) -> FakeResult:
        if "FROM public.media_asset" in sql:
            return FakeResult()
        raise AssertionError(sql)

    connection = FakeConnection(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader()
    )

    with pytest.raises(ApiError) as raised:
        asyncio.run(
            repository.create_club(
                staff_auth(),
                {
                    "name": "Code Collective",
                    "category": "Technology",
                    "description": "Build useful software with other students.",
                    "contactName": "Jordan Lee",
                    "contactRole": "Club president",
                    "contactChannel": "code@example.edu",
                    "latestUpdate": "Applications are open.",
                    "membershipOpen": True,
                    "imageUrl": "https://untrusted.example/club.jpg",
                },
                "request-7",
            )
        )

    assert raised.value.code == "STAFF_CLUB_IMAGE_NOT_FOUND"
    assert all("INSERT INTO public.student_club" not in sql for sql, _ in connection.executions)


def test_manual_work_item_insert_types_status_consistently_for_postgres() -> None:
    source = inspect.getsource(PostgresStaffRepository.create_work_item)

    assert "CAST(:status AS varchar), :priority" in source
    assert "CASE WHEN CAST(:status AS varchar) = 'in_progress'" in source
    assert "THEN CAST(:now AS timestamptz) ELSE NULL::timestamptz END" in source


def test_manual_work_item_creation_is_idempotent_linked_notified_and_ai_queued() -> None:
    created_item_id: UUID | None = None

    def item_row(item_id: UUID) -> dict[str, object]:
        return {
            "id": item_id,
            "key": f"MAN-{str(item_id).replace('-', '')[:8].upper()}",
            "student_id": UUID(STUDENT_ID),
            "title": "Verify transcript review handoff",
            "description": "Confirm that the parsed transcript reached Registrar review.",
            "status": "in_progress",
            "priority": "urgent",
            "work_type": "enrollment",
            "component": "Registrar",
            "due_at": NOW + timedelta(days=3),
            "escalated": False,
            "version": 1,
            "created_at": NOW,
            "updated_at": NOW,
            "assignee_id": UUID(STAFF_ID),
            "action_type": "document_review",
            "selected_channel": None,
            "attempt_count": 0,
            "follow_up_at": None,
            "blocker_code": None,
            "blocker_detail": None,
            "blocker_review_at": None,
            "outcome_code": None,
            "resolution_code": None,
            "next_step": None,
            "terminal_reason": None,
            "started_at": NOW,
            "interaction_completed_at": None,
            "completed_at": None,
            "cancelled_at": None,
            "assignee_name": "Marcus Lee",
            "assignee_email": "marcus@aster.edu",
            "assignee_component": "Registrar",
            "first_name": "Taylor",
            "last_name": "Nguyen",
            "preferred_name": "Taylor",
            "program_name": "Computer Science",
            "class_year": 2027,
            "source_type": None,
            "source_id": None,
        }

    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        nonlocal created_item_id
        if "SELECT request_hash, response_body" in sql:
            return FakeResult()
        if "FROM public.student_requirement" in sql and "FOR SHARE OF requirement" in sql:
            return FakeResult([{"id": UUID(REQUIREMENT_ID), "flow_kind": "enrollment"}])
        if "FROM public.student\n" in sql and "FOR SHARE" in sql:
            return FakeResult([{"id": UUID(STUDENT_ID)}])
        if "SELECT component" in sql and "lower(component)" in sql:
            return FakeResult([{"component": "Registrar"}])
        if "SELECT id, display_name, email_normalized, component" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(STAFF_ID),
                        "display_name": "Marcus Lee",
                        "email_normalized": "marcus@aster.edu",
                        "component": "Registrar",
                    }
                ]
            )
        if "SELECT display_name" in sql and "FROM public.staff_member" in sql:
            return FakeResult([{"display_name": "Marcus Lee"}])
        if "INSERT INTO public.staff_work_item (" in sql:
            created_item_id = cast(UUID, values["id"])
            return FakeResult()
        if "INSERT INTO public.staff_notification" in sql and "RETURNING id" in sql:
            return FakeResult([{"id": values["id"]}])
        if "FROM public.staff_work_item AS item" in sql:
            assert created_item_id is not None
            return FakeResult([item_row(created_item_id)])
        return FakeResult()

    connection = FakeConnection(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)),
        FakeStudentReader(),
        clock=lambda: NOW,
    )

    result = asyncio.run(
        repository.create_work_item(
            staff_auth(),
            {
                "studentId": STUDENT_ID,
                "requirementId": REQUIREMENT_ID,
                "flowKind": "enrollment",
                "title": "Verify transcript review handoff",
                "description": "Confirm that the parsed transcript reached Registrar review.",
                "component": "Registrar",
                "assigneeId": STAFF_ID,
                "priority": "urgent",
                "status": "in_progress",
                "dueAt": NOW + timedelta(days=3),
                "actionType": "document_review",
            },
            "create-browser-qa-task",
            "request.staff.create",
        )
    )

    assert result["status"] == "in_progress"
    assert result["priority"] == "urgent"
    assert result["component"] == "Registrar"
    assert result["assignee"] == {
        "id": STAFF_ID,
        "name": "Marcus Lee",
        "email": "marcus@aster.edu",
        "component": "Registrar",
    }
    statements = [sql for sql, _ in connection.executions]
    assert any("INSERT INTO public.staff_work_item_link" in sql for sql in statements)
    assert any("INSERT INTO public.action_center_ai_job" in sql for sql in statements)
    assert any("INSERT INTO public.staff_notification" in sql for sql in statements)
    assert any("INSERT INTO public.staff_realtime_event" in sql for sql in statements)
    assert any("INSERT INTO public.audit_event" in sql for sql in statements)
    assert any("INSERT INTO public.outbox_event" in sql for sql in statements)
    assert any("INSERT INTO public.idempotency_record" in sql for sql in statements)


def test_realtime_event_stream_bootstraps_replays_and_rejects_invalid_cursors() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        if "SELECT id, display_name, email_normalized, component" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(STAFF_ID),
                        "display_name": "Marcus Lee",
                        "email_normalized": "marcus@aster.edu",
                        "component": "Registrar",
                    }
                ]
            )
        if "SELECT COALESCE(MAX(cursor), 0)" in sql:
            return FakeResult([{"cursor": 14}])
        if "SELECT cursor, event_type, resource_type" in sql:
            assert values["after_cursor"] == 14
            assert values["limit"] == 100
            return FakeResult(
                [
                    {
                        "cursor": 15,
                        "event_type": "staff.notification.created",
                        "resource_type": "staff_notification",
                        "resource_id": "00000000-0000-7000-8000-000000000932",
                        "work_item_id": UUID(WORK_ITEM_ID),
                        "payload": {"invalidate": ["notifications", "workspace"]},
                        "created_at": NOW,
                    }
                ]
            )
        raise AssertionError(sql)

    connection = FakeConnection(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader(), clock=lambda: NOW
    )

    bootstrap = asyncio.run(repository.get_realtime_events(staff_auth(), None))
    replay = asyncio.run(repository.get_realtime_events(staff_auth(), 14, limit=500))

    assert bootstrap == {"events": [], "cursor": 14}
    assert replay == {
        "events": [
            {
                "cursor": 15,
                "type": "staff.notification.created",
                "resourceType": "staff_notification",
                "resourceId": "00000000-0000-7000-8000-000000000932",
                "workItemId": WORK_ITEM_ID,
                "data": {"invalidate": ["notifications", "workspace"]},
                "occurredAt": "2026-07-24T12:00:00.000Z",
            }
        ],
        "cursor": 15,
    }
    with pytest.raises(ApiError) as raised:
        asyncio.run(repository.get_realtime_events(staff_auth(), -1))
    assert raised.value.code == "INVALID_EVENT_CURSOR"


def test_action_center_read_maps_staff_history_and_operational_counts() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        assert values["tenant_id"] == UUID(TENANT_ID)
        if "ORDER BY display_name" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(STAFF_ID),
                        "display_name": "Marcus Lee",
                        "email_normalized": "marcus@aster.edu",
                        "component": "Registrar",
                    }
                ]
            )
        if "FROM public.staff_work_item AS item" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(WORK_ITEM_ID),
                        "key": "MAN-12345678",
                        "student_id": UUID(STUDENT_ID),
                        "title": "Review parsed transcript",
                        "description": "Confirm the extracted courses.",
                        "status": "blocked",
                        "priority": "urgent",
                        "work_type": "enrollment",
                        "component": "Registrar",
                        "due_at": NOW + timedelta(days=2),
                        "escalated": True,
                        "version": 5,
                        "created_at": NOW - timedelta(days=1),
                        "updated_at": NOW,
                        "assignee_id": UUID(STAFF_ID),
                        "action_type": "document_review",
                        "selected_channel": "portal",
                        "attempt_count": 2,
                        "follow_up_at": NOW + timedelta(days=1),
                        "blocker_code": "human_review",
                        "blocker_detail": "Confirm one ambiguous course.",
                        "blocker_review_at": NOW + timedelta(hours=4),
                        "outcome_code": "student_reached",
                        "resolution_code": None,
                        "next_step": "Registrar review",
                        "terminal_reason": None,
                        "started_at": NOW - timedelta(hours=2),
                        "interaction_completed_at": None,
                        "completed_at": None,
                        "cancelled_at": None,
                        "assignee_name": "Marcus Lee",
                        "assignee_email": "marcus@aster.edu",
                        "assignee_component": "Registrar",
                        "first_name": "Taylor",
                        "last_name": "Nguyen",
                        "preferred_name": "Taylor",
                        "program_name": "Computer Science",
                        "class_year": 2027,
                        "source_type": "document",
                        "source_id": UUID(DOCUMENT_ID),
                    }
                ]
            )
        if "FROM public.staff_work_log" in sql:
            return FakeResult(
                [
                    {
                        "id": uuid4(),
                        "work_item_id": UUID(WORK_ITEM_ID),
                        "action": "blocked",
                        "message": "Human review requested.",
                        "actor_name": "Audentra AI",
                        "occurred_at": NOW,
                    }
                ]
            )
        raise AssertionError(sql)

    connection = FakeConnection(handler)
    repository = WorkspaceReadRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader(), clock=lambda: NOW
    )

    center = asyncio.run(repository.get_action_center(staff_auth()))

    assert center["counts"] == {
        "todo": 0,
        "inProgress": 0,
        "followUpRequired": 0,
        "blocked": 1,
        "done": 0,
        "cancelled": 0,
        "urgent": 1,
        "escalated": 1,
    }
    item = cast(list[dict[str, object]], center["items"])[0]
    assert item["blocker"] == {
        "code": "human_review",
        "detail": "Confirm one ambiguous course.",
        "reviewAt": "2026-07-24T16:00:00.000Z",
    }
    assert item["source"] == {"type": "document", "id": DOCUMENT_ID}
    assert cast(list[dict[str, object]], item["history"])[0]["action"] == "blocked"


def test_action_rule_read_maps_schedule_configuration_and_editor_identity() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        assert values == {"tenant_id": UUID(TENANT_ID)}
        assert "FROM public.staff_action_rule" in sql
        return FakeResult(
            [
                {
                    "id": uuid4(),
                    "code": "transcript-due-soon",
                    "name": "Transcript due soon",
                    "description": "Create work before the requirement is due.",
                    "enabled": True,
                    "signal_type": "requirement_due",
                    "flow_kind": "enrollment",
                    "requirement_code": "official_transcript",
                    "lookahead_days": 3,
                    "inactivity_days": None,
                    "cadence_minutes": 5,
                    "component": "Registrar",
                    "priority": "high",
                    "action_type": "document_review",
                    "title_template": "Transcript due for {student}",
                    "description_template": "Review the outstanding transcript.",
                    "version": 2,
                    "last_evaluated_at": NOW - timedelta(minutes=5),
                    "updated_at": NOW,
                    "updated_by_id": UUID(STAFF_ID),
                    "updated_by_name": "Marcus Lee",
                    "updated_by_email": "marcus@aster.edu",
                    "updated_by_component": "Registrar",
                }
            ]
        )

    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(FakeConnection(handler))),
        FakeStudentReader(),
        clock=lambda: NOW,
    )

    rules = asyncio.run(repository.get_action_rules(staff_auth()))

    rule = cast(list[dict[str, object]], rules["items"])[0]
    assert rule["signalType"] == "requirement_due"
    assert rule["lookaheadDays"] == 3
    assert rule["updatedBy"] == {
        "id": STAFF_ID,
        "name": "Marcus Lee",
        "email": "marcus@aster.edu",
        "component": "Registrar",
    }


def test_canonical_roster_includes_students_without_risk_or_work_rows() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        assert "FROM public.student AS student" in sql
        assert "LEFT JOIN public.student_onboarding AS onboarding" in sql
        assert "item.status NOT IN ('done', 'cancelled')" in sql
        assert values == {"tenant_id": UUID(TENANT_ID)}
        return FakeResult(
            [
                {
                    "id": UUID(STUDENT_ID),
                    "first_name": "Casey",
                    "last_name": "Rivera",
                    "preferred_name": "Casey",
                    "program_name": "Computer Science",
                    "class_year": 2027,
                    "onboarding_status": "in_progress",
                    "onboarding_step": "housing",
                    "onboarding_completed": 2,
                    "requirement_completed": 0,
                    "requirement_total": 0,
                    "last_activity_at": NOW,
                    "work_item_id": None,
                    "work_title": None,
                    "work_description": None,
                    "assignee_id": None,
                    "selected_channel": None,
                    "work_priority": None,
                }
            ]
        )

    connection = FakeConnection(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader(), clock=lambda: NOW
    )

    roster = asyncio.run(repository.get_student_roster(staff_auth()))

    assert len(roster) == 1
    assert roster[0]["name"] == "Casey Rivera"
    journey = cast(dict[str, object], roster[0]["journey"])
    risk = cast(dict[str, object], roster[0]["risk"])
    recommended_action = cast(dict[str, object], roster[0]["recommendedAction"])
    assert journey == {
        "stage": "Onboarding",
        "completedTasks": 2,
        "totalTasks": 8,
        "lastActivityAt": "2026-07-24T12:00:00.000Z",
    }
    assert risk["modelVersion"] == "not-evaluated"
    assert recommended_action["taskId"] is None


def test_lazy_document_work_items_preserve_component_and_priority_routing() -> None:
    documents: list[dict[str, object]] = [
        {
            "id": DOCUMENT_ID,
            "student_id": STUDENT_ID,
            "file_name": "aid.pdf",
            "category": "financial_aid",
        },
        {
            "id": "00000000-0000-7000-8000-000000000702",
            "student_id": STUDENT_ID,
            "file_name": "health.pdf",
            "category": "health",
        },
        {
            "id": "00000000-0000-7000-8000-000000000703",
            "student_id": STUDENT_ID,
            "file_name": "identity.pdf",
            "category": "identity",
        },
    ]

    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        if "FROM public.document_record AS document" in sql:
            return FakeResult(documents)
        if "SELECT id" in sql and "FROM public.staff_member" in sql:
            return FakeResult([{"id": UUID(STAFF_ID)}])
        if "INSERT INTO public.staff_work_item" in sql:
            return FakeResult([{"id": values["id"]}])
        return FakeResult()

    connection = FakeConnection(handler)
    engine = FakeEngine(connection)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, engine), FakeStudentReader(), clock=lambda: NOW
    )

    asyncio.run(repository._ensure_document_work_items(staff_auth()))

    inserts = [
        values
        for sql, values in connection.executions
        if "INSERT INTO public.staff_work_item" in sql
    ]
    assert [(row["component"], row["priority"]) for row in inserts] == [
        ("Financial Aid", "urgent"),
        ("Student Health", "high"),
        ("Registrar", "high"),
    ]
    assert engine.begin_count == 3
    assert sum("INSERT INTO public.staff_work_log" in sql for sql, _ in connection.executions) == 3
    assert all(row["tenant_id"] == UUID(TENANT_ID) for row in inserts)


def test_work_item_update_locks_versions_and_writes_log_audit_and_outbox_atomically() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        if "FROM public.staff_work_item" in sql and "FOR UPDATE" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(WORK_ITEM_ID),
                        "student_id": UUID(STUDENT_ID),
                        "status": "todo",
                        "assignee_id": UUID(STAFF_ID),
                        "escalated": False,
                        "version": 1,
                        "source_type": "onboarding",
                        "source_id": UUID(STUDENT_ID),
                    }
                ]
            )
        if "UPDATE public.staff_work_item" in sql and "RETURNING version" in sql:
            return FakeResult([{"version": 2}])
        if "SELECT id" in sql and "FROM public.staff_member" in sql:
            return FakeResult([{"id": UUID(STAFF_ID)}])
        if "SELECT display_name" in sql:
            return FakeResult([{"display_name": "Avery Chen"}])
        return FakeResult()

    connection = FakeConnection(handler)
    engine = FakeEngine(connection)
    repository = ReloadingRepository(
        cast(AsyncEngine, engine), FakeStudentReader(), clock=lambda: NOW
    )

    updated = asyncio.run(
        repository.update_work_item(
            staff_auth(),
            WORK_ITEM_ID,
            {
                "expectedVersion": 1,
                "status": "in_progress",
                "escalated": True,
                "note": "Advisor review started.",
            },
            "request.staff.1",
        )
    )

    assert updated["version"] == 2
    statements = [sql for sql, _ in connection.executions]
    assert engine.begin_count == 1
    assert any("FOR UPDATE" in sql for sql in statements)
    update_sql = next(
        sql
        for sql in statements
        if "UPDATE public.staff_work_item" in sql and "RETURNING version" in sql
    )
    assert "SET status = CAST(:status AS varchar)" in update_sql
    assert "WHEN CAST(:status AS varchar) = 'in_progress'" in update_sql
    assert sum("INSERT INTO public.staff_work_log" in sql for sql in statements) == 3
    assert any("INSERT INTO public.audit_event" in sql for sql in statements)
    assert any("INSERT INTO public.outbox_event" in sql for sql in statements)
    outbox_values = next(
        values for sql, values in connection.executions if "INSERT INTO public.outbox_event" in sql
    )
    assert outbox_values["event_name"] == "staff.work_item_updated.v1"
    assert json_event(outbox_values)["data"]["studentId"] == STUDENT_ID


def test_combined_ai_refresh_queues_one_interaction_job_for_outcome_and_student_summary() -> None:
    interaction_id = "00000000-0000-7000-8000-000000000921"

    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        if "FROM public.staff_work_item" in sql and "FOR UPDATE" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(WORK_ITEM_ID),
                        "student_id": UUID(STUDENT_ID),
                        "version": 3,
                    }
                ]
            )
        if "FROM public.staff_interaction" in sql and "FOR UPDATE" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(interaction_id),
                        "student_id": UUID(STUDENT_ID),
                        "work_item_id": UUID(WORK_ITEM_ID),
                        "source_version": 5,
                    }
                ]
            )
        if "SELECT display_name" in sql:
            return FakeResult([{"display_name": "Avery Chen"}])
        return FakeResult()

    connection = FakeConnection(handler)
    repository = ActionDetailReloadingRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader(), clock=lambda: NOW
    )

    detail = asyncio.run(
        repository.request_ai_refresh(
            staff_auth(),
            WORK_ITEM_ID,
            {
                "expectedWorkItemVersion": 3,
                "scope": "both",
                "interactionId": interaction_id,
            },
            "request.staff.ai-refresh",
        )
    )

    jobs = [
        sql for sql, _ in connection.executions if "INSERT INTO public.action_center_ai_job" in sql
    ]
    assert detail["workItem"] == {"id": WORK_ITEM_ID}
    assert len(jobs) == 1
    assert "'interaction_enrichment'" in jobs[0]
    assert "'student_summary'" not in jobs[0]
    assert "attempts = CASE" in jobs[0]
    assert "status = 'dead_letter'" in jobs[0]


def test_student_summary_refresh_resets_exhausted_dead_letter_attempts() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        if "FROM public.staff_work_item" in sql and "FOR UPDATE" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(WORK_ITEM_ID),
                        "student_id": UUID(STUDENT_ID),
                        "version": 3,
                    }
                ]
            )
        if "SELECT display_name" in sql:
            return FakeResult([{"display_name": "Avery Chen"}])
        return FakeResult()

    connection = FakeConnection(handler)
    repository = ActionDetailReloadingRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader(), clock=lambda: NOW
    )

    asyncio.run(
        repository.request_ai_refresh(
            staff_auth(),
            WORK_ITEM_ID,
            {"expectedWorkItemVersion": 3, "scope": "student_summary"},
            "request.staff.student-summary-refresh",
        )
    )

    jobs = [
        sql for sql, _ in connection.executions if "INSERT INTO public.action_center_ai_job" in sql
    ]
    assert len(jobs) == 1
    assert "'student_summary'" in jobs[0]
    assert "attempts = CASE" in jobs[0]
    assert "status = 'dead_letter'" in jobs[0]


def test_current_student_summary_is_not_hidden_by_an_obsolete_dead_letter_job() -> None:
    state = _summary_public_state(
        {"generated_at": NOW},
        {
            "status": "dead_letter",
            "requested_source_version": 5,
            "covered_source_version": 0,
            "updated_at": NOW - timedelta(seconds=1),
        },
    )

    assert state == "ready"


@pytest.mark.parametrize(
    ("notify_student", "expected_body"),
    [
        (True, "The stored original matches the student record."),
        (False, "Your document was reviewed and accepted."),
    ],
)
def test_document_acceptance_refreshes_dependencies_and_commits_all_side_effects(
    notify_student: bool,
    expected_body: str,
) -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        if "FROM public.staff_work_item" in sql and "FOR UPDATE" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(WORK_ITEM_ID),
                        "student_id": UUID(STUDENT_ID),
                        "status": "todo",
                        "assignee_id": UUID(STAFF_ID),
                        "escalated": False,
                        "version": 1,
                        "source_type": "document",
                        "source_id": UUID(DOCUMENT_ID),
                    }
                ]
            )
        if "FROM public.document_record" in sql and "FOR UPDATE" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(DOCUMENT_ID),
                        "student_id": UUID(STUDENT_ID),
                        "requirement_id": UUID(REQUIREMENT_ID),
                        "file_name": "transcript.pdf",
                        "status": "needs_review",
                    }
                ]
            )
        if "UPDATE public.staff_work_item" in sql and "RETURNING version" in sql:
            return FakeResult([{"version": 2}])
        if "SELECT definition.code" in sql:
            return FakeResult([{"code": "official_transcript"}])
        if "FROM public.tenant_reward_rule" in sql:
            return FakeResult(
                [
                    {
                        "id": UUID(REWARD_RULE_ID),
                        "points": 80,
                        "max_awards_per_student": 1,
                    }
                ]
            )
        if "INSERT INTO public.student_reward_ledger" in sql:
            return FakeResult([{"points": 80}])
        if "SELECT display_name" in sql:
            return FakeResult([{"display_name": "Avery Chen"}])
        if "INSERT INTO public.student_message" in sql:
            return FakeResult([{"sent_at": NOW}])
        return FakeResult()

    connection = FakeConnection(handler)
    engine = FakeEngine(connection)
    repository = ReloadingRepository(
        cast(AsyncEngine, engine), FakeStudentReader(), clock=lambda: NOW
    )

    result = asyncio.run(
        repository.review_document(
            staff_auth(),
            DOCUMENT_ID,
            {
                "workItemId": WORK_ITEM_ID,
                "expectedWorkItemVersion": 1,
                "decision": "accepted",
                "note": "The stored original matches the student record.",
                "notifyStudent": notify_student,
            },
            "request.staff.review",
        )
    )

    statements = [sql for sql, _ in connection.executions]
    assert result["document"]["status"] == "accepted"  # type: ignore[index]
    assert result["notification"]["subject"] == "transcript.pdf was accepted"  # type: ignore[index]
    assert result["notification"]["body"] == expected_body  # type: ignore[index]
    assert result["notification"]["kind"] == "document_review"  # type: ignore[index]
    assert engine.begin_count == 1
    assert any("UPDATE public.document_record" in sql for sql in statements)
    assert any("UPDATE public.student_requirement" in sql for sql in statements)
    assert any("INSERT INTO public.student_reward_ledger" in sql for sql in statements)
    assert any("WITH completed_journey" in sql for sql in statements)
    assert any("INSERT INTO public.staff_work_log" in sql for sql in statements)
    assert any("INSERT INTO public.student_message" in sql for sql in statements)
    assert any("INSERT INTO public.audit_event" in sql for sql in statements)
    outbox_values = next(
        values for sql, values in connection.executions if "INSERT INTO public.outbox_event" in sql
    )
    assert outbox_values["event_name"] == "student.document_decided_by_staff.v1"
    reward_values = next(
        values
        for sql, values in connection.executions
        if "INSERT INTO public.student_reward_ledger" in sql
    )
    assert reward_values["student_id"] == UUID(STUDENT_ID)
    assert reward_values["source_key"] == REQUIREMENT_ID
    assert reward_values["points"] == 80


def test_staff_preferences_lock_both_versions_and_publish_one_atomic_change() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        if "SELECT payload, version" in sql:
            return FakeResult(
                [
                    {
                        "payload": {
                            "communicationPreference": "email",
                            "housingPreference": "on_campus",
                            "housingResidenceOption": "aster_residence_hall",
                            "housingResidencePreferences": ["aster_residence_hall"],
                        },
                        "version": 1,
                    }
                ]
            )
        if "SELECT version" in sql and "FROM public.student_profile" in sql:
            return FakeResult([{"version": 1}])
        if "UPDATE public.student_onboarding" in sql:
            return FakeResult([{"version": 2}])
        if "UPDATE public.student_profile" in sql:
            return FakeResult([{"version": 2}])
        if "SELECT display_name" in sql:
            return FakeResult([{"display_name": "Avery Chen"}])
        if "UPDATE public.staff_work_item" in sql and "RETURNING id" in sql:
            return FakeResult([{"id": UUID(WORK_ITEM_ID)}])
        if "INSERT INTO public.student_message" in sql:
            return FakeResult([{"sent_at": NOW}])
        return FakeResult()

    connection = FakeConnection(handler)
    engine = FakeEngine(connection)
    repository = PreferenceReloadingRepository(
        cast(AsyncEngine, engine), FakeStudentReader(), clock=lambda: NOW
    )

    record = asyncio.run(
        repository.update_student_preferences(
            staff_auth(),
            STUDENT_ID,
            {
                "expectedOnboardingVersion": 1,
                "expectedProfileVersion": 1,
                "communicationPreference": "sms",
                "housingPreference": "off_campus",
                "accommodationInterest": "housing",
                "residencyVerificationPath": "advisor_review",
                "notifyStudent": True,
                "note": "Preferences confirmed with the student.",
            },
            "request.staff.preferences",
        )
    )

    assert record["profile"]["version"] == 2  # type: ignore[index]
    assert engine.begin_count == 1
    onboarding_values = next(
        values for sql, values in connection.executions if "UPDATE public.student_onboarding" in sql
    )
    payload = json.loads(str(onboarding_values["payload"]))
    assert payload["communicationPreference"] == "sms"
    assert payload["housingPreference"] == "off_campus"
    assert "housingResidenceOption" not in payload
    assert "housingResidencePreferences" not in payload
    statements = [sql for sql, _ in connection.executions]
    assert any("INSERT INTO public.student_message" in sql for sql in statements)
    assert any("INSERT INTO public.audit_event" in sql for sql in statements)
    outbox_values = next(
        values for sql, values in connection.executions if "INSERT INTO public.outbox_event" in sql
    )
    assert outbox_values["event_name"] == "student.preferences_updated_by_staff.v1"


def test_staff_notifications_include_direct_and_team_targets_with_read_state() -> None:
    notification_id = "00000000-0000-7000-8000-000000000931"

    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        assert values == {
            "tenant_id": UUID(TENANT_ID),
            "staff_member_id": UUID(STAFF_ID),
        }
        assert "notification.team_component = viewer.component" in sql
        assert "staff_notification_read_receipt" in sql
        return FakeResult(
            [
                {
                    "id": notification_id,
                    "kind": "scheduled_action",
                    "title": "Transcript due soon",
                    "body": "Alex has an incomplete transcript.",
                    "resource_type": "staff_work_item",
                    "resource_id": WORK_ITEM_ID,
                    "staff_member_id": None,
                    "team_component": "Registrar",
                    "created_at": NOW,
                    "read_at": None,
                    "work_item_key": "ENR-1234",
                    "unread_count": 2,
                }
            ]
        )

    connection = FakeConnection(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader(), clock=lambda: NOW
    )

    result = asyncio.run(repository.get_notifications(staff_auth()))

    assert result == {
        "items": [
            {
                "id": notification_id,
                "kind": "scheduled_action",
                "title": "Transcript due soon",
                "body": "Alex has an incomplete transcript.",
                "resourceType": "staff_work_item",
                "resourceId": WORK_ITEM_ID,
                "workItemKey": "ENR-1234",
                "target": "team",
                "isRead": False,
                "readAt": None,
                "createdAt": "2026-07-24T12:00:00.000Z",
            }
        ],
        "unreadCount": 2,
        "generatedAt": "2026-07-24T12:00:00.000Z",
    }


def test_marking_team_notification_read_creates_a_per_staff_receipt() -> None:
    notification_id = "00000000-0000-7000-8000-000000000931"

    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        assert values["tenant_id"] == UUID(TENANT_ID)
        assert values["staff_member_id"] == UUID(STAFF_ID)
        assert values["notification_id"] == UUID(notification_id)
        if "SELECT notification.id" in sql:
            assert "notification.team_component = viewer.component" in sql
            return FakeResult([{"id": notification_id}])
        if "INSERT INTO public.staff_notification_read_receipt" in sql:
            return FakeResult([{"read_at": NOW}])
        raise AssertionError(sql)

    connection = FakeConnection(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, FakeEngine(connection)), FakeStudentReader(), clock=lambda: NOW
    )

    result = asyncio.run(repository.mark_notification_read(staff_auth(), notification_id))

    assert result == {
        "id": notification_id,
        "isRead": True,
        "readAt": "2026-07-24T12:00:00.000Z",
    }
    assert engine_sql_count(connection, "INSERT INTO public.staff_notification_read_receipt") == 1


def engine_sql_count(connection: FakeConnection, fragment: str) -> int:
    return sum(fragment in sql for sql, _ in connection.executions)


def json_event(values: Mapping[str, object]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(str(values["payload"])))


@pytest.mark.postgres
def test_postgres_work_item_update_is_tenant_isolated_and_atomic() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    pytest.importorskip("asyncpg")

    async def exercise() -> None:
        schema = f"staff_test_{uuid4().hex}"
        engine = create_database_engine(
            database_url,
            DatabaseEngineOptions(pool_size=1, application_name="staff-repository-test"),
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
                for statement in _integration_schema_statements(schema):
                    await connection.execute(text(statement))
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {schema}.staff_member (
                          id, tenant_id, display_name, email_normalized, component, active
                        ) VALUES (
                          :staff_id, :tenant_id, 'Avery Chen', 'avery@example.test',
                          'Registrar', true
                        )
                        """
                    ),
                    {"staff_id": UUID(STAFF_ID), "tenant_id": UUID(TENANT_ID)},
                )
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {schema}.staff_work_item (
                          id, tenant_id, student_id, status, assignee_id,
                          escalated, version, source_type, source_id
                        ) VALUES (
                          :item_id, :tenant_id, :student_id, 'todo', :staff_id,
                          false, 1, 'onboarding', :student_id
                        )
                        """
                    ),
                    {
                        "staff_id": UUID(STAFF_ID),
                        "tenant_id": UUID(TENANT_ID),
                        "item_id": UUID(WORK_ITEM_ID),
                        "student_id": UUID(STUDENT_ID),
                    },
                )
            repository = ReloadingRepository(
                engine,
                FakeStudentReader(),
                schema=schema,
                clock=lambda: NOW,
            )
            with pytest.raises(ApiError) as isolated:
                await repository.update_work_item(
                    staff_auth(tenant_id=FOREIGN_TENANT_ID),
                    WORK_ITEM_ID,
                    {"expectedVersion": 1, "status": "done"},
                    "request.foreign",
                )
            assert isolated.value.code == "STAFF_WORK_ITEM_NOT_FOUND"

            await repository.update_work_item(
                staff_auth(),
                WORK_ITEM_ID,
                {"expectedVersion": 1, "status": "in_progress"},
                "request.local",
            )
            async with engine.connect() as connection:
                counts = await connection.execute(
                    text(
                        f"""
                        SELECT
                          (SELECT COUNT(*) FROM {schema}.staff_work_log) AS logs,
                          (SELECT COUNT(*) FROM {schema}.audit_event) AS audits,
                          (SELECT COUNT(*) FROM {schema}.outbox_event) AS events
                        """
                    )
                )
                row = counts.mappings().one()
                assert (row["logs"], row["audits"], row["events"]) == (1, 1, 1)
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await engine.dispose()

    asyncio.run(exercise())


def _integration_schema_statements(schema: str) -> tuple[str, ...]:
    return (
        f"""CREATE TABLE {schema}.staff_member (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL, display_name text NOT NULL,
      email_normalized text NOT NULL, component text NOT NULL, active boolean NOT NULL
    )""",
        f"""CREATE TABLE {schema}.staff_work_item (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
      key text NOT NULL DEFAULT 'ENR-TEST', title text NOT NULL DEFAULT 'Test work',
      status text NOT NULL, assignee_id uuid, escalated boolean NOT NULL,
      version integer NOT NULL, source_type text, source_id uuid,
      work_type text NOT NULL DEFAULT 'enrollment', selected_channel text,
      follow_up_at timestamptz, blocker_code text, blocker_detail text,
      blocker_review_at timestamptz, outcome_code text, resolution_code text,
      next_step text, terminal_reason text, started_at timestamptz,
      interaction_completed_at timestamptz, completed_at timestamptz,
      cancelled_at timestamptz,
      updated_at timestamptz NOT NULL DEFAULT NOW()
    )""",
        f"""CREATE TABLE {schema}.staff_work_log (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL, work_item_id uuid NOT NULL,
      actor_type text NOT NULL, actor_id uuid, actor_name text NOT NULL,
      action text NOT NULL, message text NOT NULL, occurred_at timestamptz NOT NULL
    )""",
        f"""CREATE TABLE {schema}.student_summary_revision (
      tenant_id uuid NOT NULL, student_id uuid NOT NULL, version integer NOT NULL
    )""",
        f"""CREATE TABLE {schema}.action_center_ai_job (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL, purpose text NOT NULL,
      dedupe_key text NOT NULL, student_id uuid NOT NULL, work_item_id uuid,
      interaction_id uuid, status text NOT NULL,
      requested_source_version bigint NOT NULL,
      covered_source_version bigint NOT NULL, base_summary_version integer,
      not_before timestamptz NOT NULL, attempts integer NOT NULL,
      max_attempts integer NOT NULL, created_at timestamptz NOT NULL,
      updated_at timestamptz NOT NULL, completed_at timestamptz,
      last_error_code text, last_error_message text,
      UNIQUE (tenant_id, purpose, dedupe_key)
    )""",
        f"""CREATE TABLE {schema}.audit_event (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL, actor_type text NOT NULL,
      actor_id uuid NOT NULL, student_id uuid, action text NOT NULL,
      resource_type text NOT NULL, resource_id uuid NOT NULL,
      authorization_basis text NOT NULL, request_id text NOT NULL,
      correlation_id text NOT NULL, metadata jsonb NOT NULL,
      occurred_at timestamptz NOT NULL, created_at timestamptz NOT NULL
    )""",
        f"""CREATE TABLE {schema}.outbox_event (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL, event_name text NOT NULL,
      aggregate_type text NOT NULL, aggregate_id uuid NOT NULL,
      aggregate_version integer NOT NULL, occurred_at timestamptz NOT NULL,
      actor_type text NOT NULL, actor_id uuid NOT NULL,
      correlation_id text NOT NULL, causation_id text NOT NULL,
      payload jsonb NOT NULL, created_at timestamptz NOT NULL
    )""",
    )
