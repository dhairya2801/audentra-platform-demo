from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


class _BorrowedConnectionContext(AbstractAsyncContextManager[AsyncConnection]):
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> AsyncConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _TransactionBoundEngine:
    """Keep repository writes inside the integration test's rollback boundary."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    def begin(self) -> _BorrowedConnectionContext:
        return _BorrowedConnectionContext(self.connection)


def _database_url() -> str:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if database_url:
        return database_url
    if os.getenv("CI", "").lower() == "true":
        pytest.fail("PostgreSQL AI-attempt contract tests require a CI database URL")
    pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run PostgreSQL contract tests")


def _attempt(
    *,
    tenant_id: UUID,
    student_id: UUID,
    document_id: UUID | None,
    request_id: str,
    operation: str,
    attempt_number: int,
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "tenantId": str(tenant_id),
        "studentId": str(student_id),
        "documentId": None if document_id is None else str(document_id),
        "requestId": request_id,
        "attempt": attempt_number,
        "operation": operation,
        "provider": "openrouter",
        "requestedModel": "openai/gpt-4o-mini",
        "responseModel": "openai/gpt-4o-mini",
        "providerRequestId": f"provider-{uuid4()}",
        "httpStatus": 200,
        "responseOk": True,
        "finishReason": "stop",
        "usage": {"total_tokens": 12},
        "rawResponseText": "{}",
        "responseBody": {"operation": operation},
        "transportError": None,
        "durationMs": 100,
        "recordedAt": "2026-08-12T10:30:00Z",
        "promptTemplateVersionId": None,
        "contextPolicyVersionId": None,
        "outputSchemaVersionId": None,
        "configRevision": 1,
        "contextSha256": "a" * 64,
        "promptCacheStatus": "hit",
    }


def test_real_postgres_ai_attempt_replay_and_distinct_delivery_contracts() -> None:
    database_url = _database_url()
    tenant_id = uuid4()
    person_id = uuid4()
    student_id = uuid4()
    document_id = uuid4()

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(database_url))
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                        {"id": tenant_id, "name": f"AI attempt contract {tenant_id}"},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO person (id, tenant_id, first_name, last_name)
                            VALUES (:id, :tenant_id, 'AI', 'Contract')
                            """
                        ),
                        {"id": person_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student (id, tenant_id, person_id, class_year)
                            VALUES (:id, :tenant_id, :person_id, 2030)
                            """
                        ),
                        {
                            "id": student_id,
                            "tenant_id": tenant_id,
                            "person_id": person_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO document_record (
                              id, tenant_id, student_id, file_name, mime_type,
                              size_bytes, category, status
                            ) VALUES (
                              :id, :tenant_id, :student_id, 'contract.pdf',
                              'application/pdf', 1024, 'transcript', 'uploaded'
                            )
                            """
                        ),
                        {
                            "id": document_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                        },
                    )

                    repository = PostgresPlatformRepository(
                        cast(AsyncEngine, _TransactionBoundEngine(connection))
                    )
                    document_request = f"document-contract-{uuid4()}"
                    general_request = f"general-contract-{uuid4()}"

                    async def record(
                        selected_document_id: UUID | None,
                        request_id: str,
                        operation: str,
                        attempt_number: int,
                    ) -> None:
                        await repository.record_ai_provider_response(
                            _attempt(
                                tenant_id=tenant_id,
                                student_id=student_id,
                                document_id=selected_document_id,
                                request_id=request_id,
                                operation=operation,
                                attempt_number=attempt_number,
                            )
                        )

                    await record(document_id, document_request, "document_extraction", 1)
                    await record(document_id, document_request, "document_extraction", 1)
                    await record(document_id, document_request, "course_exemption_mapping", 1)
                    await record(document_id, document_request, "document_extraction", 2)

                    await record(None, general_request, "edward_chat", 1)
                    await record(None, general_request, "edward_chat", 1)
                    await record(None, general_request, "action_center_enrichment", 1)
                    await record(None, general_request, "edward_chat", 2)

                    result = await connection.execute(
                        text(
                            """
                            SELECT document_id, request_id, operation, attempt_number
                            FROM ai_provider_response_attempt
                            WHERE tenant_id = :tenant_id
                              AND request_id IN (:document_request, :general_request)
                            """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "document_request": document_request,
                            "general_request": general_request,
                        },
                    )
                    rows = cast(list[Mapping[str, object]], result.mappings().all())
                    deliveries = {
                        (
                            None if row["document_id"] is None else str(row["document_id"]),
                            str(row["request_id"]),
                            str(row["operation"]),
                            int(cast(int, row["attempt_number"])),
                        )
                        for row in rows
                    }

                    assert len(rows) == 6
                    assert deliveries == {
                        (str(document_id), document_request, "document_extraction", 1),
                        (str(document_id), document_request, "course_exemption_mapping", 1),
                        (str(document_id), document_request, "document_extraction", 2),
                        (None, general_request, "edward_chat", 1),
                        (None, general_request, "action_center_enrichment", 1),
                        (None, general_request, "edward_chat", 2),
                    }
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
