"""Official review, resubmission and shared evidence against an isolated real DB."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID
from audentra.integrations.assistant.tools import AssistantToolHost, execute_tool_reads


class BorrowedEngine:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Any]:
        yield self.connection

    begin = connect


def isolated_url() -> str:
    value = os.getenv("AUDENTRA_UNIVERSITY_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("Requires an explicitly isolated imported university test DB")
    parsed = urlparse(value)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university_test"
    ):
        raise ValueError("Document history tests require an isolated local test DB")
    return value


@pytest.mark.postgres
@pytest.mark.integration
def test_rejection_resubmission_acceptance_preserves_one_evidence_chain() -> None:
    async def run() -> None:
        runtime = await build_api_runtime(
            RuntimeSettings.from_environment({"DATABASE_URL": isolated_url()})
        )
        service: Any = runtime.service
        repo = service.repository
        try:
            async with runtime.engine.connect() as c, c.begin():
                await c.execute(
                    text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                    {"tenant": SYNTHETIC_TENANT_ID},
                )
                target = (
                    (
                        await c.execute(
                            text("""
                    SELECT d.id,d.student_id FROM university.document d
                    WHERE d.tenant_id=:tenant
                      AND d.category='transcript' AND d.status='REJECTED'
                    ORDER BY d.id LIMIT 1
                """),
                            {"tenant": SYNTHETIC_TENANT_ID},
                        )
                    )
                    .mappings()
                    .one()
                )
                actor = "01973261-954a-5019-8e9e-24a699abea7b"
                auth = AuthContext(SYNTHETIC_TENANT_ID, target["student_id"], actor, "staff")
                student = replace(auth, actor_type="student", actor_id=auth.student_id)
                engine = BorrowedEngine(c)
                repo.staff._engine = engine
                repo.portal.engine = engine
                repo.university.engine = engine
                baseline = await repo.university.record(student, "account")
                originals = (await repo.portal.get_student_documents(student))["items"]

                async def submission(label: str) -> tuple[dict[str, Any], dict[str, Any]]:
                    key = str(uuid4())
                    document = await repo.portal.reserve_student_document_upload(
                        student,
                        {
                            "fileName": label,
                            "mimeType": "application/pdf",
                            "sizeBytes": 512,
                            "category": "transcript",
                            "sha256": "a" * 64,
                        },
                        key,
                        key,
                    )
                    # Exercise the real worker completion boundary with an explicit failed
                    # extraction. Object storage/provider execution is a separate test.
                    assert await repo.portal.claim_student_document_processing(
                        student, document["id"]
                    )
                    await repo.portal.complete_student_document_extraction(
                        student,
                        document["id"],
                        {
                            "status": "failed",
                            "documentType": "transcript",
                            "provider": "local",
                            "fields": [],
                            "warnings": ["Manual review required"],
                            "model": None,
                            "processedAt": None,
                            "verifiedAt": None,
                        },
                        str(uuid4()),
                    )
                    item = (
                        (
                            await c.execute(
                                text(
                                    "SELECT id,version FROM staff_work_item "
                                    "WHERE tenant_id=:tenant "
                                    "AND source_type='document' AND source_id=CAST(:id AS uuid)"
                                ),
                                {"tenant": auth.tenant_id, "id": document["id"]},
                            )
                        )
                        .mappings()
                        .one()
                    )
                    return document, dict(item)

                first, work = await submission("history-first.pdf")
                before = await repo.university.record(student, "documents")
                assert (
                    next(d for d in before["documents"] if d["id"] == target["id"])["status"]
                    == "UNDER_REVIEW"
                )
                assert not any(r["documentId"] == first["id"] for r in before["reviewDecisions"])
                command = {
                    "workItemId": str(work["id"]),
                    "expectedWorkItemVersion": work["version"],
                    "decision": "rejected",
                    "reasonCode": "incomplete",
                    "note": "Please include the missing final transcript page.",
                    "internalNote": "INTERNAL-ONLY-REVIEW-CONTEXT",
                    "notifyStudent": True,
                }
                with pytest.raises(ApiError):
                    await repo.staff.review_document(student, first["id"], command, "unauthorized")
                key = str(uuid4())
                rejected = await repo.staff.review_document(auth, first["id"], command, key, key)
                replay = await repo.staff.review_document(
                    auth, first["id"], command, str(uuid4()), key
                )
                assert rejected["decision"] == replay["decision"]
                assert rejected["decision"] == rejected["document"]["reviewHistory"][0]
                assert rejected["document"]["reviewHistory"][0]["reasonCode"] == "incomplete"
                with pytest.raises(ApiError):
                    await repo.staff.review_document(
                        auth,
                        first["id"],
                        {**command, "note": "Changed request"},
                        str(uuid4()),
                        key,
                    )
                with pytest.raises(ApiError):
                    await repo.staff.review_document(
                        auth, first["id"], command, str(uuid4()), str(uuid4())
                    )
                dossier = await repo.university.record(student, "documents")
                assert (
                    next(d for d in dossier["documents"] if d["id"] == target["id"])["status"]
                    == "REJECTED"
                )
                decision = next(
                    d for d in dossier["reviewDecisions"] if d["documentId"] == first["id"]
                )
                assert decision["note"] == command["note"]
                assert "INTERNAL-ONLY" not in str(dossier)
                history = (await repo.portal.get_student_documents(student))["items"]
                assert "INTERNAL-ONLY" not in str(history)
                assert {d["id"] for d in originals} <= {d["id"] for d in history}
                count = await c.scalar(
                    text(
                        "SELECT count(*) FROM document_review_decision "
                        "WHERE tenant_id=:tenant AND document_id=CAST(:id AS uuid)"
                    ),
                    {"tenant": auth.tenant_id, "id": first["id"]},
                )
                assert count == 1
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(
                            text(
                                "UPDATE document_review_decision SET student_message='rewritten' "
                                "WHERE tenant_id=:tenant AND document_id=CAST(:id AS uuid)"
                            ),
                            {"tenant": auth.tenant_id, "id": first["id"]},
                        )

                second, second_work = await submission("history-replacement.pdf")
                accepted = await repo.staff.review_document(
                    auth,
                    second["id"],
                    {
                        "workItemId": str(second_work["id"]),
                        "expectedWorkItemVersion": second_work["version"],
                        "decision": "accepted",
                        "note": "The complete transcript is acceptable evidence.",
                        "notifyStudent": True,
                    },
                    str(uuid4()),
                    str(uuid4()),
                )
                assert accepted["document"]["status"] == "accepted"
                final = await repo.university.record(student, "documents")
                assert (
                    next(d for d in final["documents"] if d["id"] == target["id"])["status"]
                    == "ACCEPTED"
                )
                assert (
                    next(d for d in final["reviewDecisions"] if d["documentId"] == first["id"])[
                        "decision"
                    ]
                    == "changes_requested"
                )
                assert (
                    next(d for d in final["reviewDecisions"] if d["documentId"] == second["id"])[
                        "decision"
                    ]
                    == "accepted"
                )
                # An old submission changing state can never replace the new document head.
                await c.execute(
                    text(
                        "UPDATE document_record SET status='needs_resubmission' "
                        "WHERE tenant_id=:tenant AND id=CAST(:id AS uuid)"
                    ),
                    {"tenant": auth.tenant_id, "id": first["id"]},
                )
                assert (
                    await c.scalar(
                        text(
                            "SELECT status FROM university.document "
                            "WHERE tenant_id=:tenant AND id=:id"
                        ),
                        {"tenant": auth.tenant_id, "id": target["id"]},
                    )
                    == "ACCEPTED"
                )
                assert (await repo.university.record(student, "account")) == baseline
                host = AssistantToolHost(
                    {
                        "university_record": lambda **kwargs: repo.university.record(
                            student, **kwargs
                        )
                    }
                )
                reads = await execute_tool_reads(["getUniversityDocuments"], host)
                assert "INTERNAL-ONLY" not in str(reads)
                assert str(second["id"]) in str(reads)
                # Roll all fixture uploads, decisions, receipts and events back together.
                await c.rollback()
        finally:
            await runtime.close()

    asyncio.run(run())
