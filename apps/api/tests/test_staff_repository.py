# ruff: noqa: S608 -- random isolated test-schema identifiers are controlled here.
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.db.engine import DatabaseEngineOptions, create_database_engine
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository

TENANT_ID = "00000000-0000-7000-8000-000000000001"
FOREIGN_TENANT_ID = "00000000-0000-7000-8000-000000000099"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
STAFF_ID = "00000000-0000-7000-8000-000000000901"
WORK_ITEM_ID = "00000000-0000-7000-8000-000000000911"
DOCUMENT_ID = "00000000-0000-7000-8000-000000000701"
REQUIREMENT_ID = "00000000-0000-7000-8000-000000000401"
NOW = datetime(2026, 7, 24, 12, tzinfo=UTC)


class FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class FakeResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeMappings:
        return FakeMappings(self._rows)


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
    assert sum("INSERT INTO public.staff_work_log" in sql for sql in statements) == 3
    assert any("INSERT INTO public.audit_event" in sql for sql in statements)
    assert any("INSERT INTO public.outbox_event" in sql for sql in statements)
    outbox_values = next(
        values for sql, values in connection.executions if "INSERT INTO public.outbox_event" in sql
    )
    assert outbox_values["event_name"] == "staff.work_item_updated.v1"
    assert json_event(outbox_values)["data"]["studentId"] == STUDENT_ID


def test_document_acceptance_refreshes_dependencies_and_commits_all_side_effects() -> None:
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
                "notifyStudent": True,
            },
            "request.staff.review",
        )
    )

    statements = [sql for sql, _ in connection.executions]
    assert result["document"]["status"] == "accepted"  # type: ignore[index]
    assert result["notification"]["subject"] == "transcript.pdf was accepted"  # type: ignore[index]
    assert engine.begin_count == 1
    assert any("UPDATE public.document_record" in sql for sql in statements)
    assert any("UPDATE public.student_requirement" in sql for sql in statements)
    assert any("WITH completed_journey" in sql for sql in statements)
    assert any("INSERT INTO public.staff_work_log" in sql for sql in statements)
    assert any("INSERT INTO public.student_message" in sql for sql in statements)
    assert any("INSERT INTO public.audit_event" in sql for sql in statements)
    outbox_values = next(
        values for sql, values in connection.executions if "INSERT INTO public.outbox_event" in sql
    )
    assert outbox_values["event_name"] == "student.document_decided_by_staff.v1"


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
      status text NOT NULL, assignee_id uuid, escalated boolean NOT NULL,
      version integer NOT NULL, source_type text, source_id uuid,
      updated_at timestamptz NOT NULL DEFAULT NOW()
    )""",
        f"""CREATE TABLE {schema}.staff_work_log (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL, work_item_id uuid NOT NULL,
      actor_type text NOT NULL, actor_id uuid, actor_name text NOT NULL,
      action text NOT NULL, message text NOT NULL, occurred_at timestamptz NOT NULL
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
