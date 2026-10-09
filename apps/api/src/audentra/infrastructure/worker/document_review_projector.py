"""Route reviewable documents into the staff queue from durable events."""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.domain.documents import document_review_task_copy
from audentra.infrastructure.messaging.envelope import DomainEventEnvelope

_REVIEWABLE_STATUSES = {"needs_review", "under_review"}


class DocumentReviewProjector:
    """Idempotently materialize one staff task for a reviewable document.

    The API currently creates this task synchronously as part of the document
    transition. The worker remains responsible for the durable event reaction
    so retries, recovered events, and future ingestion paths converge on the
    same unique source record.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        consumer_name: str,
        *,
        uuid_factory: Callable[[], UUID] = uuid4,
        logger: logging.Logger | None = None,
    ) -> None:
        if not consumer_name.strip() or len(consumer_name) > 120:
            raise ValueError("consumer_name must be between 1 and 120 characters")
        self._engine = engine
        self._consumer_name = consumer_name
        self._uuid_factory = uuid_factory
        self._logger = logger or logging.getLogger(__name__)

    async def handle(self, event: DomainEventEnvelope) -> None:
        document_id = event.aggregate_id
        async with self._engine.begin() as connection:
            receipt = await connection.execute(
                text(
                    """
                    INSERT INTO public.projection_event_receipt (
                      event_id, consumer_name, processed_at
                    ) VALUES (:event_id, :consumer_name, NOW())
                    ON CONFLICT (event_id, consumer_name) DO NOTHING
                    RETURNING event_id
                    """
                ),
                {"event_id": event.event_id, "consumer_name": self._consumer_name},
            )
            if receipt.first() is None:
                return

            document_result = await connection.execute(
                text(
                    """
                    SELECT document.id, document.tenant_id, document.student_id,
                           document.file_name, document.category, document.status,
                           COALESCE(
                             NULLIF(BTRIM(profile.preferred_name), ''),
                             NULLIF(BTRIM(person.first_name), '')
                           ) AS student_name
                    FROM public.document_record AS document
                    JOIN public.student AS student
                      ON student.id = document.student_id
                     AND student.tenant_id = document.tenant_id
                    JOIN public.person AS person
                      ON person.id = student.person_id
                     AND person.tenant_id = student.tenant_id
                    LEFT JOIN public.student_profile AS profile
                      ON profile.student_id = student.id
                     AND profile.tenant_id = student.tenant_id
                    WHERE document.tenant_id = :tenant_id AND document.id = :document_id AND
                    document.superseded_at IS NULL
                    FOR SHARE OF document
                    """
                ),
                {"tenant_id": event.tenant_id, "document_id": document_id},
            )
            document = document_result.mappings().first()
            if document is None:
                return
            if str(document["status"]) not in _REVIEWABLE_STATUSES:
                return

            linked = await connection.execute(
                text("""SELECT 1 FROM public.staff_work_item_link
                    WHERE tenant_id=:tenant_id AND entity_type='document'
                      AND entity_id=:document_id AND relationship='document_submission' LIMIT 1"""),
                {"tenant_id": event.tenant_id, "document_id": document_id},
            )
            if linked.first() is not None:
                return

            component, priority = _document_route(str(document["category"]))
            task_title, task_description = document_review_task_copy(
                document["student_name"],
                document["category"],
            )
            assignee_result = await connection.execute(
                text(
                    """
                    SELECT id
                    FROM public.staff_member
                    WHERE tenant_id = :tenant_id AND active = true
                    ORDER BY CASE WHEN component = :component THEN 0 ELSE 1 END,
                             display_name, id
                    LIMIT 1
                    """
                ),
                {"tenant_id": event.tenant_id, "component": component},
            )
            assignee = assignee_result.mappings().first()
            work_item_id = self._uuid_factory()
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_work_item (
                      id, tenant_id, student_id, key, title, description,
                      status, priority, work_type, action_type, component, due_at,
                      escalated, assignee_id, source_type, source_id, version
                    ) VALUES (
                      :id, :tenant_id, :student_id, :key, :title, :description,
                      'todo', :priority, 'document_review', 'document_review', :component,
                      NOW() + INTERVAL '2 days', false, :assignee_id,
                      'document', :document_id, 1
                    )
                    ON CONFLICT (tenant_id, source_type, source_id)
                    WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                    DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": work_item_id,
                    "tenant_id": event.tenant_id,
                    "student_id": document["student_id"],
                    "key": f"DOC-{document_id.replace('-', '')[:8].upper()}",
                    "title": task_title,
                    "description": task_description,
                    "priority": priority,
                    "component": component,
                    "assignee_id": assignee["id"] if assignee is not None else None,
                    "document_id": document_id,
                },
            )
            if inserted.mappings().first() is None:
                return

            await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_work_item_link (
                      id, tenant_id, work_item_id, entity_type, entity_id, relationship
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'document', :document_id, 'review'
                    )
                    ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id) DO NOTHING
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": event.tenant_id,
                    "work_item_id": work_item_id,
                    "document_id": document_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_work_log (
                      id, tenant_id, work_item_id, actor_type, actor_id,
                      actor_name, action, message, occurred_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'system', NULL,
                      'Audentra workflow', 'created',
                      'A document is ready for a staff decision.', NOW()
                    )
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": event.tenant_id,
                    "work_item_id": work_item_id,
                },
            )

        self._logger.info(
            "document_review_task_projected",
            extra={
                "event_id": event.event_id,
                "event_name": event.event_name,
                "tenant_id": event.tenant_id,
                "document_id": document_id,
            },
        )


def _document_route(category: str) -> tuple[str, str]:
    if category == "financial_aid":
        return "Financial Aid", "urgent"
    if category == "health":
        return "Student Health", "high"
    return "Registrar", "high"
