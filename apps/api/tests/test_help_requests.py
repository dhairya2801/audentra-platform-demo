from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext
from audentra.core.errors import ConflictError, NotFoundError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.memory.store import DEMO_IDS
from audentra.infrastructure.messaging.envelope import DomainEventEnvelope
from audentra.infrastructure.messaging.outbox import OutboxRepository, OutboxRepositoryConfig
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.interfaces.http.app import create_app

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "10000000-0000-7000-8000-000000000001"
NOW = datetime(2026, 8, 2, 14, 30, tzinfo=UTC)
AUTH = AuthContext(
    tenant_id=TENANT_ID,
    student_id=STUDENT_ID,
    actor_id=STUDENT_ID,
    actor_type="student",
)


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        return {"operation": call.operation}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def http_client() -> AsyncIterator[tuple[AsyncClient, RecordingService]]:
    service = RecordingService()
    transport = ASGITransport(app=create_app(service=service))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service


@pytest.mark.anyio
async def test_help_request_route_validates_trims_and_dispatches_idempotently(
    http_client: tuple[AsyncClient, RecordingService],
) -> None:
    client, service = http_client
    response = await client.post(
        "/v1/demo/help-requests",
        headers={"Idempotency-Key": "help-request-12345678"},
        json={"topicCode": "documents", "message": "  Which transcript should I use?  "},
    )

    assert response.status_code == 200
    call = service.calls[-1]
    assert call.operation == "student.create_help_request"
    assert call.payload == {
        "topicCode": "documents",
        "message": "Which transcript should I use?",
    }
    assert call.idempotency_key == "help-request-12345678"
    assert call.auth is not None and call.auth.actor_type == "student"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"topicCode": "unknown", "message": "Question"},
        {"topicCode": "support", "message": "   "},
        {"topicCode": "support", "message": "x" * 501},
        {"topicCode": "support", "message": "Question", "unexpected": True},
        {"topicCode": "support", "message": 123},
    ],
)
async def test_help_request_route_rejects_invalid_bodies(
    http_client: tuple[AsyncClient, RecordingService], body: dict[str, object]
) -> None:
    client, service = http_client
    response = await client.post(
        "/v1/demo/help-requests",
        headers={"Idempotency-Key": "help-request-12345678"},
        json=body,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert service.calls == []


@pytest.mark.anyio
async def test_help_request_route_requires_idempotency_key(
    http_client: tuple[AsyncClient, RecordingService],
) -> None:
    client, service = http_client
    response = await client.post(
        "/v1/demo/help-requests",
        json={"topicCode": "support", "message": "Please contact me"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert service.calls == []


def test_help_request_openapi_contract_is_strict_and_camel_case() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/v1/demo/help-requests"]["post"]
    request_schema = schema["components"]["schemas"]["CreateStudentHelpRequest"]

    assert "200" in operation["responses"]
    assert "400" in operation["responses"]
    assert "422" not in operation["responses"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {"topicCode", "message"}
    assert request_schema["properties"]["topicCode"]["enum"] == [
        "getting_started",
        "documents",
        "payments",
        "support",
    ]
    assert request_schema["properties"]["message"]["minLength"] == 1
    assert request_schema["properties"]["message"]["maxLength"] == 500


def test_help_request_migration_enforces_scope_lifecycle_and_queue_indexes() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "0019_student_inquiries.sql"
    ).read_text(encoding="utf-8")

    assert "tenant_id uuid NOT NULL REFERENCES tenant(id)" in migration
    assert "student_id uuid NOT NULL REFERENCES student(id)" in migration
    assert "char_length(message) BETWEEN 1 AND 500" in migration
    assert "status IN ('new', 'open', 'waiting_on_student', 'resolved')" in migration
    assert "student_inquiry_student_idx" in migration
    assert "student_inquiry_staff_queue_idx" in migration


def test_in_memory_help_request_replays_and_rejects_key_reuse() -> None:
    service = InMemoryPlatformService()
    memory_auth = AuthContext(
        tenant_id=DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["person_id"],
        actor_type="student",
    )
    first_call = ServiceCall(
        operation="student.create_help_request",
        auth=memory_auth,
        request_id="request-1",
        payload={"topicCode": "support", "message": "I need help"},
        idempotency_key="help-request-12345678",
    )

    first = cast(dict[str, Any], asyncio.run(service.dispatch(first_call)))
    replay = cast(dict[str, Any], asyncio.run(service.dispatch(first_call)))

    assert replay == first
    assert first["priority"] == "high"
    assert service.store.help_requests == [first]
    assert service.store.effects.audit_events == 1
    assert service.store.effects.outbox_events == 1

    with pytest.raises(ConflictError, match="different request"):
        asyncio.run(
            service.dispatch(
                ServiceCall(
                    operation="student.create_help_request",
                    auth=memory_auth,
                    request_id="request-2",
                    payload={"topicCode": "support", "message": "A different question"},
                    idempotency_key="help-request-12345678",
                )
            )
        )


class FakeResult:
    def __init__(self, rows: list[Mapping[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> Mapping[str, Any] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Mapping[str, Any]]:
        return self.rows


Handler = Callable[[str, Mapping[str, Any]], list[Mapping[str, Any]]]


class FakeConnection:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, statement: object, parameters: Mapping[str, Any] | None = None
    ) -> FakeResult:
        sql = str(statement)
        params = dict(parameters or {})
        self.calls.append((sql, params))
        return FakeResult(self.handler(sql, params))


class FakeContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def __init__(self, handler: Handler) -> None:
        self.connection = FakeConnection(handler)

    def begin(self) -> FakeContext:
        return FakeContext(self.connection)

    def connect(self) -> FakeContext:
        return FakeContext(self.connection)


class RecordingOutbox:
    def __init__(self) -> None:
        self.events: list[DomainEventEnvelope] = []

    async def enqueue(self, _connection: AsyncConnection, event: DomainEventEnvelope) -> None:
        self.events.append(event)


def test_postgres_command_scopes_insert_and_avoids_free_text_in_lineage() -> None:
    inquiry_id: str | None = None

    def handler(sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        nonlocal inquiry_id
        if "INSERT INTO student_inquiry" in sql:
            inquiry_id = str(params["id"])
            return [
                {
                    "id": inquiry_id,
                    "topic_code": params["topic_code"],
                    "subject": params["subject"],
                    "message": params["message"],
                    "status": "new",
                    "priority": params["priority"],
                    "assignee_id": None,
                    "version": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
            ]
        return []

    engine = FakeEngine(handler)
    outbox = RecordingOutbox()
    repository = PostgresPortalRepository(cast(AsyncEngine, engine), cast(OutboxRepository, outbox))

    result = asyncio.run(
        repository.create_student_help_request(
            AUTH,
            {"topicCode": "documents", "message": "Which transcript should I use?"},
            "help-request-12345678",
            "request-1",
        )
    )

    assert result == {
        "id": inquiry_id,
        "topicCode": "documents",
        "subject": "Student question about documents",
        "message": "Which transcript should I use?",
        "status": "new",
        "priority": "medium",
        "assigneeId": None,
        "createdAt": "2026-08-02T14:30:00.000Z",
        "updatedAt": "2026-08-02T14:30:00.000Z",
        "version": 1,
    }
    inquiry_call = next(
        call for call in engine.connection.calls if "INSERT INTO student_inquiry" in call[0]
    )
    assert "student.tenant_id=:tenant_id AND student.id=:student_id" in inquiry_call[0]
    assert inquiry_call[1]["tenant_id"] == TENANT_ID
    assert inquiry_call[1]["student_id"] == STUDENT_ID

    audit_call = next(
        call for call in engine.connection.calls if "INSERT INTO audit_event" in call[0]
    )
    audit_metadata = json.loads(audit_call[1]["metadata"])
    assert audit_metadata == {
        "topicCode": "documents",
        "status": "new",
        "priority": "medium",
    }
    assert outbox.events[0].event_name == "student.help_request_created.v1"
    assert outbox.events[0].aggregate_id == inquiry_id
    assert outbox.events[0].data == {
        "studentId": STUDENT_ID,
        "topicCode": "documents",
        "status": "new",
        "priority": "medium",
    }


def test_staff_help_request_read_seam_is_tenant_scoped_and_workspace_shaped() -> None:
    def handler(sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "FROM student_inquiry inquiry" not in sql:
            return []
        assert params == {"tenant_id": TENANT_ID}
        return [
            {
                "id": "00000000-0000-7000-8000-000000000951",
                "topic_code": "documents",
                "subject": "Student question about documents",
                "message": "Which transcript should I use?",
                "status": "new",
                "priority": "medium",
                "assignee_id": None,
                "version": 1,
                "created_at": NOW,
                "updated_at": NOW,
                "student_id": STUDENT_ID,
                "class_year": 2027,
                "first_name": "Alex",
                "last_name": "Morgan",
                "preferred_name": "Alex",
                "program_name": "Computer Science",
            }
        ]

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    staff_auth = AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id="00000000-0000-7000-8000-000000000901",
        actor_type="staff",
    )

    result = asyncio.run(repository.list_staff_help_requests(staff_auth))

    assert result[0]["student"] == {
        "id": STUDENT_ID,
        "name": "Alex Morgan",
        "preferredName": "Alex",
        "programName": "Computer Science",
        "classYear": 2027,
    }
    assert result[0]["assigneeId"] is None
    sql, params = engine.connection.calls[0]
    assert "WHERE inquiry.tenant_id=:tenant_id" in sql
    assert "ORDER BY inquiry.updated_at DESC" in sql
    assert params == {"tenant_id": TENANT_ID}


@pytest.mark.postgres
def test_real_postgres_help_request_is_atomic_isolated_and_replay_safe() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run PostgreSQL integration tests")

    schema = f"help_request_{uuid4().hex}"
    tenant_id = uuid4()
    foreign_tenant_id = uuid4()
    student_id = uuid4()

    async def scenario() -> None:
        admin = create_async_engine(normalize_database_url(database_url))
        engine: AsyncEngine | None = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
                for statement in _integration_schema_statements(schema):
                    await connection.execute(text(statement))

            engine = create_async_engine(
                normalize_database_url(database_url),
                connect_args={"server_settings": {"search_path": schema}},
            )
            outbox = OutboxRepository(
                engine,
                OutboxRepositoryConfig(worker_id="help-request-test", schema=schema),
            )
            repository = PostgresPortalRepository(engine, outbox)
            async with engine.begin() as connection:
                await connection.execute(
                    text("INSERT INTO student (id, tenant_id) VALUES (:id, :tenant_id)"),
                    {"id": student_id, "tenant_id": tenant_id},
                )

            auth = AuthContext(
                tenant_id=str(tenant_id),
                student_id=str(student_id),
                actor_id=str(student_id),
                actor_type="student",
            )
            payload = {"topicCode": "support", "message": "Please contact me"}
            first = await repository.create_student_help_request(
                auth, payload, "help-request-12345678", "request-1"
            )
            replay = await repository.create_student_help_request(
                auth, payload, "help-request-12345678", "request-2"
            )

            assert replay == first
            with pytest.raises(ConflictError) as conflict:
                await repository.create_student_help_request(
                    auth,
                    {"topicCode": "support", "message": "A different request"},
                    "help-request-12345678",
                    "request-3",
                )
            assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"

            foreign = AuthContext(
                tenant_id=str(foreign_tenant_id),
                student_id=str(student_id),
                actor_id=str(student_id),
                actor_type="student",
            )
            with pytest.raises(NotFoundError):
                await repository.create_student_help_request(
                    foreign, payload, "help-request-foreign", "request-foreign"
                )

            async with engine.connect() as connection:
                counts = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT
                              (SELECT COUNT(*) FROM student_inquiry) AS inquiries,
                              (SELECT COUNT(*) FROM audit_event) AS audits,
                              (SELECT COUNT(*) FROM outbox_event) AS events,
                              (SELECT COUNT(*) FROM idempotency_record) AS replays
                            """
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                lineage = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT audit.metadata, event.event_name, event.payload
                            FROM audit_event audit CROSS JOIN outbox_event event
                            LIMIT 1
                            """
                            )
                        )
                    )
                    .mappings()
                    .one()
                )

            assert int(counts["inquiries"]) == 1
            assert int(counts["audits"]) == 1
            assert int(counts["events"]) == 1
            assert int(counts["replays"]) == 1
            assert lineage["metadata"] == {
                "topicCode": "support",
                "status": "new",
                "priority": "high",
            }
            assert lineage["event_name"] == "student.help_request_created.v1"
            assert "message" not in lineage["payload"]["data"]
        finally:
            if engine is not None:
                await engine.dispose()
            try:
                async with admin.begin() as connection:
                    await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            finally:
                await admin.dispose()

    asyncio.run(scenario())


def _integration_schema_statements(schema: str) -> tuple[str, ...]:
    return (
        f"""CREATE TABLE {schema}.student (
          id uuid NOT NULL, tenant_id uuid NOT NULL,
          PRIMARY KEY (tenant_id, id)
        )""",
        f"""CREATE TABLE {schema}.student_inquiry (
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
          topic_code text NOT NULL, subject text NOT NULL, message text NOT NULL,
          status text NOT NULL, priority text NOT NULL, assignee_id uuid,
          version integer NOT NULL, created_at timestamptz NOT NULL,
          updated_at timestamptz NOT NULL
        )""",
        f"""CREATE TABLE {schema}.idempotency_record (
          tenant_id uuid NOT NULL, actor_id uuid NOT NULL, operation text NOT NULL,
          idempotency_key text NOT NULL, request_hash text NOT NULL,
          response_status integer NOT NULL, response_body jsonb NOT NULL,
          created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
        )""",
        f"""CREATE TABLE {schema}.audit_event (
          id uuid PRIMARY KEY, tenant_id uuid NOT NULL, actor_type text NOT NULL,
          actor_id uuid NOT NULL, student_id uuid, action text NOT NULL,
          resource_type text NOT NULL, resource_id uuid NOT NULL,
          authorization_basis text NOT NULL, request_id text NOT NULL,
          correlation_id text NOT NULL, metadata jsonb NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT NOW(),
          created_at timestamptz NOT NULL DEFAULT NOW()
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
