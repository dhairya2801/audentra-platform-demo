# ruff: noqa: S608 -- identifiers are generated for isolated test schemas.
"""Staff document links must not borrow the student browser session."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_staff_repository import FOREIGN_TENANT_ID, FakeStudentReader, staff_auth

from audentra.core.errors import ApiError
from audentra.infrastructure.db.engine import DatabaseEngineOptions, create_database_engine
from audentra.infrastructure.postgres.staff_repository import (
    PostgresStaffRepository,
    _staff_document_links,
)


def test_staff_projection_does_not_mutate_student_document_links() -> None:
    original: dict[str, Any] = {
        "items": [
            {"id": "upload", "contentUrl": "/v1/student/documents/upload/content"},
            {
                "id": "signature",
                "contentUrl": "/v1/student/documents/signature/content",
                "signature": {"title": "Agreement"},
            },
            {"id": "missing"},
        ],
        "total": 3,
    }
    projected = _staff_document_links(original)
    assert projected == {
        "items": [
            {"id": "upload", "contentUrl": "/v1/staff/documents/upload/content"},
            {
                "id": "signature",
                "contentUrl": "/v1/staff/documents/signature/content",
                "signature": {"title": "Agreement"},
            },
            {"id": "missing"},
        ],
        "total": 3,
    }
    assert original["items"][0]["contentUrl"].startswith("/v1/student/")


@pytest.mark.postgres
def test_staff_originals_and_signatures_remain_tenant_and_role_scoped() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        schema = f"staff_document_test_{uuid4().hex}"
        engine = create_database_engine(database_url, DatabaseEngineOptions(pool_size=1))
        upload, signed, ferpa, missing = (str(uuid4()) for _ in range(4))
        auth = staff_auth()
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA {schema}"))
                for table in ("document_record", "student_signed_document"):
                    await conn.execute(
                        text(
                            f"CREATE TABLE {schema}.{table} (id uuid, tenant_id uuid, "
                            "storage_key text, file_name text, mime_type text, template_code text)"
                        )
                    )
                for table, doc_id, template in (
                    ("document_record", upload, None),
                    ("student_signed_document", signed, "enrollment_agreement"),
                    ("student_signed_document", ferpa, "ferpa_release"),
                ):
                    await conn.execute(
                        text(
                            f"INSERT INTO {schema}.{table} VALUES (:id, :tenant, "
                            "'protected/source', 'source.pdf', 'application/pdf', :template)"
                        ),
                        {"id": doc_id, "tenant": auth.tenant_id, "template": template},
                    )
            repository = PostgresStaffRepository(engine, FakeStudentReader(), schema=schema)
            for doc_id in (upload, signed):
                ref = await repository.get_document_content_reference(auth, doc_id)
                assert ref == {
                    "storageKey": "protected/source",
                    "fileName": "source.pdf",
                    "mimeType": "application/pdf",
                }
                with pytest.raises(ApiError) as denied:
                    await repository.get_document_content_reference(
                        replace(auth, tenant_id=FOREIGN_TENANT_ID), doc_id
                    )
                assert denied.value.status_code == 404
                with pytest.raises(ApiError) as denied:
                    await repository.get_document_content_reference(
                        staff_auth(actor_type="student"), doc_id
                    )
                assert denied.value.status_code == 403
            for doc_id in (ferpa, missing):
                with pytest.raises(ApiError) as denied:
                    await repository.get_document_content_reference(auth, doc_id)
                assert denied.value.status_code == 404
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await engine.dispose()

    asyncio.run(exercise())
