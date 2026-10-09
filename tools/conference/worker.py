# Operator CLIs read small local manifests synchronously; keep SQL statements intact.
"""Existing durable worker, limited to document commands; no scheduler or communications."""

import asyncio

from runtime import environment
from sqlalchemy.ext.asyncio import create_async_engine

from audentra.infrastructure.messaging.outbox import OutboxRepository, OutboxRepositoryConfig
from audentra.infrastructure.worker.dashboard_projector import StudentDashboardProjector
from audentra.infrastructure.worker.document_commands import (
    DocumentCommandSettings,
    DocumentExtractionRunner,
)
from audentra.infrastructure.worker.document_review_projector import DocumentReviewProjector
from audentra.infrastructure.worker.factory import build_event_dispatcher
from audentra.infrastructure.worker.service import WorkerService


async def main():
    e = environment()
    engine = create_async_engine(
        e["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    )
    repo = OutboxRepository(
        engine,
        OutboxRepositoryConfig(
            worker_id="conference-oct8-documents",
            lease_seconds=300,
            event_names=(
                "document.upload_reserved.v1",
                "document.extraction_requested.v1",
                "document.extraction_retry_started.v1",
                "document.extraction_completed.v1",
                "student.document_decided_by_staff.v1",
            ),
        ),
    )
    runner = DocumentExtractionRunner(
        DocumentCommandSettings(
            api_internal_url="http://127.0.0.1:4112",
            worker_token=e["DOCUMENT_WORKER_TOKEN"],
            timeout_seconds=240,
        )
    )
    dispatcher = build_event_dispatcher(
        StudentDashboardProjector(engine, "conference-oct8"),
        runner,
        DocumentReviewProjector(engine, "conference-oct8-review"),
    )
    try:
        await WorkerService(repo, dispatcher).run()
    finally:
        await repo.release_claims()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
