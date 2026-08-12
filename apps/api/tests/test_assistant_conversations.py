"""Assistant conversation routes and Postgres persistence."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from audentra.core.auth import AuthContext
from audentra.core.ports import ServiceCall
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.interfaces.http.app import create_app


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
async def test_create_conversation_route_dispatches_with_page_context(
    http_client: tuple[AsyncClient, RecordingService],
) -> None:
    client, service = http_client
    response = await client.post(
        "/v1/student/assistant/conversations",
        json={"pageContext": {"path": "/edward", "label": "Edward"}},
    )

    assert response.status_code == 201
    call = service.calls[-1]
    assert call.operation == "student.create_assistant_conversation"
    assert call.payload == {"pageContext": {"path": "/edward", "label": "Edward"}}


@pytest.mark.anyio
async def test_conversation_messages_route_requires_uuid(
    http_client: tuple[AsyncClient, RecordingService],
) -> None:
    client, service = http_client
    conversation_id = str(uuid4())
    response = await client.get(f"/v1/student/assistant/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    call = service.calls[-1]
    assert call.operation == "student.get_assistant_conversation_messages"
    assert call.path_params == {"conversationId": conversation_id}

    invalid = await client.get("/v1/student/assistant/conversations/not-a-uuid/messages")
    assert invalid.status_code == 400


@pytest.mark.anyio
async def test_ask_route_accepts_conversation_fields_and_structured_page_context(
    http_client: tuple[AsyncClient, RecordingService],
) -> None:
    client, service = http_client
    conversation_id = str(uuid4())
    response = await client.post(
        "/v1/student/assistant/messages",
        json={
            "message": "What documents am I missing?",
            "pageContext": {"path": "/documents", "label": "Documents"},
            "conversationId": conversation_id,
            "clientMessageId": "client-msg-1234",
            "inputMode": "text",
        },
    )

    assert response.status_code == 200
    call = service.calls[-1]
    assert call.operation == "student.ask_edward"
    assert call.payload["conversationId"] == conversation_id
    assert call.payload["clientMessageId"] == "client-msg-1234"
    assert call.payload["pageContext"] == {"path": "/documents", "label": "Documents"}


@pytest.mark.anyio
async def test_ask_route_still_accepts_legacy_string_page_context(
    http_client: tuple[AsyncClient, RecordingService],
) -> None:
    client, service = http_client
    response = await client.post(
        "/v1/student/assistant/messages",
        json={"message": "Hi", "pageContext": "/dashboard"},
    )
    assert response.status_code == 200
    assert service.calls[-1].payload == {"message": "Hi", "pageContext": "/dashboard"}


@pytest.mark.postgres
def test_postgres_conversation_round_trip_and_replay() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run PostgreSQL integration tests")

    schema = f"assistant_{uuid4().hex}"
    tenant_id = str(uuid4())
    student_id = str(uuid4())
    foreign_student_id = str(uuid4())

    async def scenario() -> None:
        admin = create_async_engine(normalize_database_url(database_url))
        engine: AsyncEngine | None = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
                for statement in (
                    f"""CREATE TABLE {schema}.student (
                      id uuid NOT NULL, tenant_id uuid NOT NULL,
                      PRIMARY KEY (tenant_id, id)
                    )""",
                    f"""CREATE TABLE {schema}.assistant_conversation (
                      id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
                      student_id uuid NOT NULL,
                      status varchar(16) NOT NULL DEFAULT 'active',
                      page_path varchar(240), page_label varchar(240),
                      created_at timestamptz NOT NULL DEFAULT now(),
                      last_message_at timestamptz NOT NULL DEFAULT now(),
                      closed_at timestamptz,
                      UNIQUE (id, tenant_id, student_id)
                    )""",
                    f"""CREATE TABLE {schema}.assistant_message (
                      id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
                      conversation_id uuid NOT NULL, student_id uuid NOT NULL,
                      role varchar(12) NOT NULL,
                      input_mode varchar(8) NOT NULL DEFAULT 'text',
                      content text NOT NULL, client_message_id varchar(128),
                      request_id varchar(128), provider varchar(24), model varchar(120),
                      usage jsonb, blocks jsonb,
                      context_receipts jsonb NOT NULL DEFAULT '[]'::jsonb,
                      suggested_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
                      widgets jsonb NOT NULL DEFAULT '[]'::jsonb,
                      created_at timestamptz NOT NULL DEFAULT now(),
                      FOREIGN KEY (conversation_id, tenant_id, student_id)
                        REFERENCES {schema}.assistant_conversation(id, tenant_id, student_id)
                        ON DELETE CASCADE
                    )""",
                    f"""CREATE UNIQUE INDEX am_client_{schema}
                      ON {schema}.assistant_message(
                        tenant_id, student_id, client_message_id
                      ) WHERE client_message_id IS NOT NULL AND role='user'
                    """,
                ):
                    await connection.execute(text(statement))
                await connection.execute(
                    text(
                        f"""INSERT INTO {schema}.student (id, tenant_id)
                            VALUES (:id, :tenant_id), (:foreign_id, :tenant_id)"""  # noqa: S608
                    ),
                    {
                        "id": student_id,
                        "foreign_id": foreign_student_id,
                        "tenant_id": tenant_id,
                    },
                )

            engine = create_async_engine(
                normalize_database_url(database_url),
                connect_args={"server_settings": {"search_path": schema}},
            )
            repository = PostgresPortalRepository(engine)
            auth = AuthContext(
                tenant_id=tenant_id,
                student_id=student_id,
                actor_id=student_id,
                actor_type="student",
            )

            conversation = await repository.create_assistant_conversation(
                auth, page_path="/edward", page_label="Edward"
            )
            assert conversation["status"] == "active"

            stored = await repository.append_assistant_exchange(
                auth,
                conversation_id=str(conversation["id"]),
                page_path="/edward",
                page_label="Edward",
                user_message={
                    "content": "What aid have I accepted?",
                    "clientMessageId": "client-abc-123",
                    "inputMode": "text",
                },
                assistant_message={
                    "content": "You've accepted the Aster Grant.",
                    "provider": "guided",
                    "model": None,
                    "usage": None,
                    "blocks": [{"type": "text", "fallbackText": "x", "text": "x"}],
                    "contextReceipts": [{"source": "financial_aid"}],
                    "suggestedActions": [],
                    "widgets": [],
                },
                request_id="request-1",
            )
            assert stored["conversationId"] == str(conversation["id"])

            replay = await repository.find_assistant_exchange_by_client_id(auth, "client-abc-123")
            assert replay is not None
            assert replay["assistantMessageId"] == stored["assistantMessageId"]
            assert replay["blocks"] == [{"type": "text", "fallbackText": "x", "text": "x"}]

            history = await repository.get_assistant_conversation_messages(
                auth, str(conversation["id"])
            )
            roles = [item["role"] for item in history["messages"]]
            assert roles == ["user", "assistant"]

            async def append_concurrent_replay() -> dict[str, object]:
                return await repository.append_assistant_exchange(
                    auth,
                    conversation_id=None,
                    page_path="/edward",
                    page_label="Concurrent replay contract",
                    user_message={
                        "content": "Show my enrollment status.",
                        "clientMessageId": "client-concurrent-replay",
                        "inputMode": "text",
                    },
                    assistant_message={
                        "content": "Your enrollment is on track.",
                        "provider": "guided",
                        "model": None,
                        "usage": None,
                        "blocks": None,
                        "contextReceipts": [],
                        "suggestedActions": [],
                        "widgets": [],
                    },
                    request_id="request-concurrent-replay",
                )

            concurrent = await asyncio.gather(
                append_concurrent_replay(), append_concurrent_replay()
            )
            assert concurrent[0]["conversationId"] == concurrent[1]["conversationId"]
            assert concurrent[0]["userMessageId"] == concurrent[1]["userMessageId"]
            assert concurrent[0]["assistantMessageId"] == concurrent[1]["assistantMessageId"]

            async with engine.connect() as connection:
                replay_counts = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT
                                  COUNT(*) FILTER (
                                    WHERE role='user'
                                      AND client_message_id='client-concurrent-replay'
                                  ) AS user_count,
                                  COUNT(*) FILTER (WHERE role='assistant') AS assistant_count
                                FROM assistant_message
                                WHERE conversation_id=:conversation_id
                                """
                            ),
                            {"conversation_id": concurrent[0]["conversationId"]},
                        )
                    )
                    .mappings()
                    .one()
                )
                conversation_count = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT COUNT(*) AS count FROM assistant_conversation
                                WHERE page_label='Concurrent replay contract'
                                """
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            assert int(replay_counts["user_count"]) == 1
            assert int(replay_counts["assistant_count"]) == 1
            assert int(conversation_count["count"]) == 1

            # The database must reject a message whose student ownership does
            # not match the conversation, even when the tenant is the same.
            async with engine.begin() as connection:
                savepoint = await connection.begin_nested()
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO assistant_message (
                              id, tenant_id, conversation_id, student_id,
                              role, input_mode, content
                            ) VALUES (
                              :id, :tenant_id, :conversation_id, :student_id,
                              'user', 'text', 'cross-student message'
                            )
                            """
                        ),
                        {
                            "id": str(uuid4()),
                            "tenant_id": tenant_id,
                            "conversation_id": str(conversation["id"]),
                            "student_id": foreign_student_id,
                        },
                    )
                await savepoint.rollback()

            # Tenant isolation: a different student sees nothing.
            foreign_auth = AuthContext(
                tenant_id=str(uuid4()),
                student_id=str(uuid4()),
                actor_id=str(uuid4()),
                actor_type="student",
            )
            assert (
                await repository.find_assistant_exchange_by_client_id(
                    foreign_auth, "client-abc-123"
                )
                is None
            )
        finally:
            if engine is not None:
                await engine.dispose()
            try:
                async with admin.begin() as connection:
                    await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            finally:
                await admin.dispose()

    asyncio.run(scenario())
