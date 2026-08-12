from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.db.migrations import resolve_migrations_directory, run_migrations
from audentra.infrastructure.messaging.envelope import DomainEventEnvelope
from audentra.infrastructure.seeding.relational import reset_relational_data
from audentra.infrastructure.worker.agentic_scheduler import AgenticWorkflowScheduler
from audentra.infrastructure.worker.document_review_projector import DocumentReviewProjector

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_real_postgres_materializes_agentic_workflows_end_to_end() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run agentic workflow integration tests")

    async def scenario() -> None:
        await run_migrations(database_url, resolve_migrations_directory())
        engine = create_database_engine(database_url)
        document_id = uuid4()
        document_event_id = uuid4()
        inbox_id = uuid4()
        unknown_inbox_id = uuid4()
        try:
            await reset_relational_data(
                engine,
                environment="test",
                completed_onboarding=True,
            )
            async with engine.begin() as connection:
                student_result = await connection.execute(
                    text(
                        """
                        SELECT account.tenant_id, account.student_id,
                               account.email_normalized, journey.id AS journey_id
                        FROM credential_account AS account
                        JOIN enrollment_journey AS journey
                          ON journey.tenant_id = account.tenant_id
                         AND journey.student_id = account.student_id
                        WHERE account.status = 'active'
                        ORDER BY account.tenant_id, account.student_id
                        LIMIT 1
                        """
                    )
                )
                student = student_result.mappings().one()
                tenant_id = student["tenant_id"]
                student_id = student["student_id"]
                requirement_result = await connection.execute(
                    text(
                        """
                        SELECT id
                        FROM student_requirement
                        WHERE tenant_id = :tenant_id AND journey_id = :journey_id
                        ORDER BY id
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": tenant_id, "journey_id": student["journey_id"]},
                )
                requirement_id = requirement_result.scalar_one()
                await connection.execute(
                    text(
                        """
                        UPDATE student_requirement
                        SET status = 'blocked', due_at = NOW() + INTERVAL '1 day',
                            updated_at = NOW(), version = version + 1
                        WHERE id = :requirement_id
                        """
                    ),
                    {"requirement_id": requirement_id},
                )
                existing_task_result = await connection.execute(
                    text(
                        """
                        SELECT id FROM staff_work_item
                        WHERE tenant_id = :tenant_id AND student_id = :student_id
                          AND status IN ('todo', 'in_progress')
                          AND work_type = 'communication'
                        ORDER BY updated_at DESC, id
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": tenant_id, "student_id": student_id},
                )
                existing_task_row = existing_task_result.mappings().first()
                if existing_task_row is None:
                    existing_task_id = uuid4()
                    await connection.execute(
                        text(
                            """
                            INSERT INTO staff_work_item (
                              id, tenant_id, student_id, key, title, description,
                              status, priority, work_type, component, escalated, version
                            ) VALUES (
                              :id, :tenant_id, :student_id, :key,
                              'Existing student communication',
                              'Synthetic open task for workflow integration.',
                              'todo', 'medium', 'communication', 'Admissions', false, 1
                            )
                            """
                        ),
                        {
                            "id": existing_task_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                            "key": f"AGENTIC-{existing_task_id.hex[:12].upper()}",
                        },
                    )
                else:
                    existing_task_id = existing_task_row["id"]
                await connection.execute(
                    text(
                        """
                        DELETE FROM activity_event
                        WHERE tenant_id = :tenant_id AND student_id = :student_id
                        """
                    ),
                    {"tenant_id": tenant_id, "student_id": student_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO document_record (
                          id, tenant_id, student_id, file_name, mime_type,
                          size_bytes, category, status, requirement_id
                        ) VALUES (
                          :id, :tenant_id, :student_id, 'agentic-proof.pdf',
                          'application/pdf', 1024, 'identity', 'needs_review', :requirement_id
                        )
                        """
                    ),
                    {
                        "id": document_id,
                        "tenant_id": tenant_id,
                        "student_id": student_id,
                        "requirement_id": requirement_id,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO inbox_event (
                          id, tenant_id, provider, external_message_id, direction,
                          sender_address, recipient_addresses, subject, body_excerpt,
                          occurred_at, status
                        ) VALUES (
                          :id, :tenant_id, 'integration-test', :external_message_id,
                          'inbound', :sender, CAST(:recipients AS jsonb),
                          'Urgent help with my deposit receipt',
                          'Please review the attached payment invoice before my deadline.',
                          NOW(), 'received'
                        )
                        """
                    ),
                    {
                        "id": inbox_id,
                        "tenant_id": tenant_id,
                        "external_message_id": f"integration-{inbox_id}",
                        "sender": student["email_normalized"],
                        "recipients": json.dumps(["admissions@example.test"]),
                    },
                )

            event = DomainEventEnvelope(
                event_id=str(document_event_id),
                event_name="document.extraction_completed.v1",
                occurred_at=datetime.now(UTC),
                tenant_id=str(tenant_id),
                aggregate_type="document",
                aggregate_id=str(document_id),
                aggregate_version=1,
                actor=None,
                correlation_id=f"integration:{document_event_id}",
                causation_id=f"integration:{document_id}",
                data={"documentId": str(document_id), "studentId": str(student_id)},
            )
            projector = DocumentReviewProjector(engine, "agentic-integration-document-review")
            await projector.handle(event)
            await projector.handle(event)

            scheduler = AgenticWorkflowScheduler(engine)
            assert await scheduler.run_once() >= 2
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO inbox_event (
                          id, tenant_id, provider, external_message_id, direction,
                          sender_address, recipient_addresses, subject, body_excerpt,
                          occurred_at, status
                        ) VALUES (
                          :id, :tenant_id, 'integration-test', :external_message_id,
                          'inbound', 'unknown-student@example.test',
                          CAST(:recipients AS jsonb), 'Please help with my deadline',
                          'I need urgent enrollment assistance.', NOW(), 'received'
                        )
                        """
                    ),
                    {
                        "id": unknown_inbox_id,
                        "tenant_id": tenant_id,
                        "external_message_id": f"integration-{unknown_inbox_id}",
                        "recipients": json.dumps(["admissions@example.test"]),
                    },
                )
            assert await scheduler.run_once() == 1

            async with engine.connect() as connection:
                document_work_items = await connection.scalar(
                    text(
                        """
                        SELECT COUNT(*) FROM staff_work_item
                        WHERE tenant_id = :tenant_id AND source_type = 'document'
                          AND source_id = :document_id AND work_type = 'document_review'
                        """
                    ),
                    {"tenant_id": tenant_id, "document_id": document_id},
                )
                document_links = await connection.scalar(
                    text(
                        """
                        SELECT COUNT(*) FROM staff_work_item_link
                        WHERE tenant_id = :tenant_id AND entity_type = 'document'
                          AND entity_id = :document_id
                        """
                    ),
                    {"tenant_id": tenant_id, "document_id": document_id},
                )
                inbox = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT status FROM inbox_event
                            WHERE tenant_id = :tenant_id AND id = :inbox_id
                            """
                            ),
                            {"tenant_id": tenant_id, "inbox_id": inbox_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                communication_task = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT priority, status, work_type, version
                            FROM staff_work_item
                            WHERE tenant_id = :tenant_id AND id = :work_item_id
                            """
                            ),
                            {"tenant_id": tenant_id, "work_item_id": existing_task_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                communication_link_count = await connection.scalar(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM staff_work_item_link AS link
                        JOIN communication_event AS communication
                          ON communication.tenant_id = link.tenant_id
                         AND communication.id = link.entity_id
                        WHERE link.tenant_id = :tenant_id
                          AND link.work_item_id = :work_item_id
                          AND link.entity_type = 'communication'
                          AND communication.inbox_event_id = :inbox_id
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "work_item_id": existing_task_id,
                        "inbox_id": inbox_id,
                    },
                )
                agent_run = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT status, result ->> 'action' AS action
                            FROM agent_run
                            WHERE tenant_id = :tenant_id
                              AND feature = 'inbound_communication_triage'
                              AND trigger_event_id = :inbox_id
                            """
                            ),
                            {"tenant_id": tenant_id, "inbox_id": str(inbox_id)},
                        )
                    )
                    .mappings()
                    .one()
                )
                snapshot = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT blocking_requirement_count, days_to_next_deadline
                            FROM student_engagement_snapshot
                            WHERE tenant_id = :tenant_id AND student_id = :student_id
                            """
                            ),
                            {"tenant_id": tenant_id, "student_id": student_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                intervention = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT priority, reason_codes
                            FROM intervention_candidate
                            WHERE tenant_id = :tenant_id AND student_id = :student_id
                              AND trigger_code = 'engagement_scan'
                            """
                            ),
                            {"tenant_id": tenant_id, "student_id": student_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                unknown_inbox = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT event.status, communication.resolution_status,
                                   run.result ->> 'action' AS action
                            FROM inbox_event AS event
                            JOIN communication_event AS communication
                              ON communication.inbox_event_id = event.id
                            JOIN agent_run AS run
                              ON run.tenant_id = event.tenant_id
                             AND run.trigger_event_id = CAST(event.id AS varchar)
                            WHERE event.tenant_id = :tenant_id AND event.id = :inbox_id
                            """
                            ),
                            {"tenant_id": tenant_id, "inbox_id": unknown_inbox_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                unknown_work_item_count = await connection.scalar(
                    text(
                        """
                        SELECT COUNT(*) FROM staff_work_item
                        WHERE tenant_id = :tenant_id AND source_type = 'message'
                          AND source_id = :inbox_id
                        """
                    ),
                    {"tenant_id": tenant_id, "inbox_id": unknown_inbox_id},
                )

            assert document_work_items == 1
            assert document_links == 1
            assert inbox["status"] == "processed"
            assert communication_task["priority"] == "urgent"
            assert communication_task["status"] in {"todo", "in_progress"}
            assert communication_task["work_type"] == "communication"
            assert communication_task["version"] >= 2
            assert communication_link_count == 1
            assert agent_run["status"] == "succeeded"
            assert agent_run["action"] == "append_to_existing_task"
            assert snapshot["blocking_requirement_count"] >= 1
            assert snapshot["days_to_next_deadline"] <= 1
            assert intervention["priority"] == "urgent"
            assert "blocking_requirement" in intervention["reason_codes"]
            assert "deadline_within_7_days" in intervention["reason_codes"]
            assert unknown_inbox["status"] == "needs_triage"
            assert unknown_inbox["resolution_status"] == "ambiguous"
            assert unknown_inbox["action"] == "human_triage"
            assert unknown_work_item_count == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
