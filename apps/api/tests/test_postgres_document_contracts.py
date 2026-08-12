from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from audentra.core.auth import AuthContext
from audentra.domain.documents import bounded_document_label
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository


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

    def connect(self) -> _BorrowedConnectionContext:
        return _BorrowedConnectionContext(self.connection)

    def begin(self) -> _BorrowedConnectionContext:
        return _BorrowedConnectionContext(self.connection)


class _DocumentReader:
    def __init__(self, document_id: str, file_name: str) -> None:
        self._document_id = document_id
        self._file_name = file_name

    async def get_student_onboarding(self, _auth: AuthContext) -> dict[str, object]:
        return {}

    async def get_student_profile(self, _auth: AuthContext) -> dict[str, object]:
        return {}

    async def get_student_requirements(self, _auth: AuthContext) -> dict[str, object]:
        return {"items": []}

    async def get_student_documents(self, _auth: AuthContext) -> dict[str, object]:
        return {
            "items": [
                {
                    "id": self._document_id,
                    "fileName": self._file_name,
                    "status": "accepted",
                }
            ]
        }


def _database_url() -> str:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if database_url:
        return database_url
    if os.getenv("CI", "").strip().lower() in {"1", "true", "yes"}:
        pytest.fail(
            "AUDENTRA_TEST_DATABASE_URL or TEST_DATABASE_URL is required for PostgreSQL "
            "contract tests in CI"
        )
    pytest.skip(
        "Set AUDENTRA_TEST_DATABASE_URL or TEST_DATABASE_URL to run PostgreSQL contract tests"
    )


def test_bounded_document_label_preserves_context_and_original_name() -> None:
    file_name = f"{'ü' * 251}.pdf"

    work_title = bounded_document_label(file_name, prefix="Review ")
    accepted_subject = bounded_document_label(file_name, suffix=" was accepted")

    assert len(file_name) == 255
    assert len(work_title) == 240
    assert work_title.startswith("Review ")
    assert work_title.endswith("…")
    assert len(accepted_subject) == 240
    assert accepted_subject.endswith("… was accepted")
    assert file_name == f"{'ü' * 251}.pdf"


@pytest.mark.postgres
def test_real_postgres_long_document_reconciles_once_and_review_subject_fits() -> None:
    database_url = _database_url()
    tenant_id = uuid4()
    person_id = uuid4()
    student_id = uuid4()
    staff_id = uuid4()
    document_id = uuid4()
    file_name = f"{'ü' * 251}.pdf"

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(database_url))
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                        {"id": tenant_id, "name": f"Document contract {tenant_id}"},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO person (
                              id, tenant_id, preferred_name, first_name, last_name
                            ) VALUES (
                              :id, :tenant_id, 'Document', 'Document', 'Contract'
                            )
                            """
                        ),
                        {"id": person_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student (id, tenant_id, person_id, class_year)
                            VALUES (:id, :tenant_id, :person_id, 2031)
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
                            INSERT INTO staff_member (
                              id, tenant_id, display_name, email_normalized, component
                            ) VALUES (
                              :id, :tenant_id, 'Document Reviewer',
                              :email, 'Enrollment Records'
                            )
                            """
                        ),
                        {
                            "id": staff_id,
                            "tenant_id": tenant_id,
                            "email": f"reviewer-{staff_id}@example.test",
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO document_record (
                              id, tenant_id, student_id, file_name, mime_type,
                              size_bytes, category, status
                            ) VALUES (
                              :id, :tenant_id, :student_id, :file_name,
                              'application/pdf', 1024, 'other', 'under_review'
                            )
                            """
                        ),
                        {
                            "id": document_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                            "file_name": file_name,
                        },
                    )

                    repository = PostgresStaffRepository(
                        cast(AsyncEngine, _TransactionBoundEngine(connection)),
                        _DocumentReader(str(document_id), file_name),
                    )
                    auth = AuthContext(
                        tenant_id=str(tenant_id),
                        student_id=str(student_id),
                        actor_id=str(staff_id),
                        actor_type="staff",
                    )

                    first = await repository.get_action_center(auth)
                    second = await repository.get_action_center(auth)
                    first_items = cast(list[dict[str, object]], first["items"])
                    second_items = cast(list[dict[str, object]], second["items"])

                    assert len(first_items) == 1
                    assert len(second_items) == 1
                    assert first_items[0]["id"] == second_items[0]["id"]
                    title = str(first_items[0]["title"])
                    assert len(title) == 240
                    assert title.startswith("Review ")
                    assert title.endswith("…")

                    count_result = await connection.execute(
                        text(
                            """
                            SELECT COUNT(*) AS count
                            FROM staff_work_item
                            WHERE tenant_id=:tenant_id
                              AND source_type='document' AND source_id=:document_id
                            """
                        ),
                        {"tenant_id": tenant_id, "document_id": document_id},
                    )
                    assert int(count_result.mappings().one()["count"]) == 1

                    work_item_id = str(first_items[0]["id"])
                    await repository.review_document(
                        auth,
                        str(document_id),
                        {
                            "workItemId": work_item_id,
                            "expectedWorkItemVersion": 1,
                            "decision": "accepted",
                            "note": "Synthetic PostgreSQL boundary contract.",
                            "notifyStudent": True,
                        },
                        "request-document-boundary",
                    )

                    persisted_result = await connection.execute(
                        text(
                            """
                            SELECT document.file_name, char_length(document.file_name) AS file_len,
                                   message.subject, char_length(message.subject) AS subject_len
                            FROM document_record AS document
                            JOIN student_message AS message
                              ON message.tenant_id=document.tenant_id
                             AND message.student_id=document.student_id
                            WHERE document.tenant_id=:tenant_id
                              AND document.id=:document_id
                              AND message.kind='document_review'
                            """
                        ),
                        {"tenant_id": tenant_id, "document_id": document_id},
                    )
                    persisted = persisted_result.mappings().one()
                    assert persisted["file_name"] == file_name
                    assert int(persisted["file_len"]) == 255
                    assert int(persisted["subject_len"]) == 240
                    assert str(persisted["subject"]).endswith("… was accepted")
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
