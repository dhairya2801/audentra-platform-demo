"""Exercise upload routing in a rollback-only transaction on the isolated Camila fixture."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.messaging.envelope import DomainEventEnvelope
from audentra.infrastructure.postgres.demo_task_board_repository import DemoTaskBoardProjection
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.worker.document_review_projector import DocumentReviewProjector


async def exercise_demo_uploads(engine: AsyncEngine, staff_auth: AuthContext) -> None:
    async with engine.connect() as connection:
        outer = await connection.begin()
        try:

            @asynccontextmanager
            async def begin() -> Any:
                async with connection.begin_nested():
                    yield connection

            class TransactionEngine:
                pass

            scoped = TransactionEngine()
            scoped.begin = begin  # type: ignore[attr-defined]
            scoped.connect = begin  # type: ignore[attr-defined]
            portal = PostgresPortalRepository(cast(Any, scoped))
            projection = DemoTaskBoardProjection(
                PostgresStaffRepository(cast(Any, scoped), cast(Any, None))
            )
            auth = replace(staff_auth, actor_type="student", actor_id=staff_auth.student_id)
            params = {"tenant": auth.tenant_id, "student": auth.student_id}
            await connection.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"), params
            )
            before = await projection.read(staff_auth)
            slots = [
                c
                for c in before["cards"]
                if c["board"] == "en-docs" and c["student"]["id"] == auth.student_id
            ]
            # Minimal requirement evidence, rolled back along with every uploaded document.
            params.update({"campus": str(uuid4()), "program": str(uuid4())})
            await connection.execute(
                text("""
                INSERT INTO campus(id,tenant_id,name)
                VALUES(CAST(:campus AS uuid),CAST(:tenant AS uuid),'Upload test')
            """),
                params,
            )
            await connection.execute(
                text("""
                INSERT INTO program(id,tenant_id,name,code,degree,total_credits,description)
                VALUES(CAST(:program AS uuid),CAST(:tenant AS uuid),
                  'Upload test','TEST','BS',120,'Test')
            """),
                params,
            )
            params.update(
                {
                    key: str(uuid4())
                    for key in [
                        "term",
                        "offer",
                        "definition",
                        "journey",
                        "requirement_definition",
                        "requirement",
                    ]
                }
            )
            statements = [
                "INSERT INTO academic_term(id,tenant_id,name,starts_on) VALUES(CAST(:term AS "
                "uuid),CAST(:tenant AS uuid),'Test','2026-09-01')",
                "INSERT INTO "
                """
                admission_offer(id, tenant_id, student_id, program_id,
                academic_term_id, campus_id, response_deadline, deposit_amount_cents,
                status)
                """
                "VALUES(CAST(:offer AS uuid),CAST(:tenant AS uuid),CAST(:student AS "
                "uuid),CAST(:program AS uuid),CAST(:term AS uuid),CAST(:campus AS "
                "uuid),'2026-12-01',0,'offered')",
                "INSERT INTO journey_definition_version(id,tenant_id,code,version) "
                "VALUES(CAST(:definition AS uuid),CAST(:tenant AS uuid),'upload-test',1)",
                "INSERT INTO "
                """
                requirement_definition_version(id, tenant_id, code, title,
                description, display_order, version, submission_type,
                interaction_type)
                """
                "VALUES(CAST(:requirement_definition AS uuid),CAST(:tenant AS "
                "uuid),'upload-test','Document upload','Test',0,1,'document','upload_file')",
                "INSERT INTO journey_requirement_definition VALUES(CAST(:definition AS "
                "uuid),CAST(:requirement_definition AS uuid))",
                "INSERT INTO "
                """
                enrollment_journey(id, tenant_id, student_id, offer_id,
                journey_definition_version_id, status)
                """
                "VALUES(CAST(:journey AS uuid),CAST(:tenant AS uuid),CAST(:student AS "
                "uuid),CAST(:offer AS uuid),CAST(:definition AS uuid),'in_progress')",
                "INSERT INTO "
                """
                student_requirement(id, tenant_id, journey_id,
                requirement_definition_version_id, status)
                """
                "VALUES(CAST(:requirement AS uuid),CAST(:tenant AS uuid),CAST(:journey AS "
                "uuid),CAST(:requirement_definition AS uuid),'ready')",
            ]
            for statement in statements:
                await connection.execute(text(statement), params)

            async def reserve(requirement: str | None = None, category: str = "transcript") -> str:
                result = await portal.reserve_student_document_upload(
                    auth,
                    {
                        "fileName": "Ada-upload.pdf",
                        "mimeType": "application/pdf",
                        "sizeBytes": 200,
                        "category": category,
                        "sha256": "a" * 64,
                    },
                    str(uuid4()),
                    str(uuid4()),
                    requirement,
                )
                return str(result["id"])

            before = await projection.read(staff_auth)
            first = await reserve(params["requirement"])
            assert (await projection.read(staff_auth))["cards"] == before["cards"]
            for denied in [
                replace(auth, actor_type="staff"),
                replace(auth, student_id=str(uuid4())),
            ]:
                if denied.actor_type == "staff":
                    with pytest.raises(ApiError):
                        await portal.attach_demo_document(denied, first, str(uuid4()))
                else:
                    assert not await portal.attach_demo_document(denied, first, str(uuid4()))
            assert await portal.attach_demo_document(auth, first, str(uuid4()))
            attached = await projection.read(staff_auth)
            assert attached["total"] == 64
            card = next(c for c in attached["cards"] if c["documents"])
            assert card["id"] == slots[0]["id"]
            assert card["documents"][0]["id"] == first
            assert card["documents"][0]["fileName"] == "Ada-upload.pdf"
            assert card["documents"][0]["uploadedAt"]
            assert await portal.attach_demo_document(auth, first, str(uuid4()))
            assert (await projection.read(staff_auth)) == attached
            record = await portal.get_student_document(auth, first)
            assert record["status"] == "under_review" and record.get("extraction") is None
            second = await reserve(params["requirement"])
            assert await portal.attach_demo_document(auth, second, str(uuid4()))
            replacement = await projection.read(staff_auth)
            current = next(c for c in replacement["cards"] if c["id"] == card["id"])
            assert [d["id"] for d in current["documents"]] == [second, first]
            assert replacement["total"] == 64
            # Terminal requirement evidence is never reopened by a demo upload.
            await connection.execute(
                text(
                    """
                UPDATE student_requirement SET status='completed' WHERE
                id=CAST(:requirement AS uuid)
                """
                ),
                params,
            )
            third = await reserve(params["requirement"])
            assert await portal.attach_demo_document(auth, third, str(uuid4()))
            assert (
                await connection.execute(
                    text(
                        "SELECT status FROM student_requirement WHERE id=CAST(:requirement AS uuid)"
                    ),
                    params,
                )
            ).scalar_one() == "completed"
            # Fill Ada's remaining slots, then materialize a genuinely new card.
            for _ in range(len(slots)):
                standalone = await reserve()
                assert await portal.attach_demo_document(auth, standalone, str(uuid4()))
            expanded = await projection.read(staff_auth)
            assert expanded["total"] == 65
            assert len({c["id"] for c in expanded["cards"]}) == 65
            financial = await reserve(category="financial_aid")
            assert await portal.attach_demo_document(auth, financial, str(uuid4()))
            assert (
                next(
                    c
                    for c in (await projection.read(staff_auth))["cards"]
                    if any(d["id"] == financial for d in c["documents"])
                )["board"]
                == "fa-docs"
            )
            count_before = (
                await connection.execute(text("SELECT count(*) FROM staff_work_item"))
            ).scalar_one()
            await PostgresStaffRepository(
                cast(Any, scoped), cast(Any, None)
            )._ensure_document_work_items(staff_auth)
            await DocumentReviewProjector(cast(Any, scoped), "demo-test").handle(
                DomainEventEnvelope(
                    event_id=str(uuid4()),
                    event_name="document.stored_for_review.v1",
                    occurred_at=datetime.now(UTC),
                    tenant_id=auth.tenant_id,
                    aggregate_type="document_record",
                    aggregate_id=first,
                    data={},
                )
            )
            assert (
                await connection.execute(text("SELECT count(*) FROM staff_work_item"))
            ).scalar_one() == count_before
            count = (
                await connection.execute(
                    text(
                        """
                SELECT count(*) FROM outbox_event WHERE tenant_id=CAST(:tenant AS
                uuid) AND event_name='document.extraction_requested.v1'
                """
                    ),
                    params,
                )
            ).scalar_one()
            assert count == 0
        finally:
            await outer.rollback()
