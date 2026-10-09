"""Document-only worker for the explicitly configured conference demo deployment.

Uses the existing runtime environment; never schedules communications or agent jobs.
Run against the same database and API as the demo. Originals and outcomes remain
canonical; retry requests are durable outbox events.
"""

import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from audentra.infrastructure.messaging.outbox import (
    OutboxRepository,
    OutboxRepositoryConfig,
)
from audentra.infrastructure.worker.dashboard_projector import StudentDashboardProjector
from audentra.infrastructure.worker.document_commands import (
    DocumentCommandSettings,
    DocumentExtractionRunner,
)
from audentra.infrastructure.worker.document_review_projector import (
    DocumentReviewProjector,
)
from audentra.infrastructure.worker.factory import build_event_dispatcher
from audentra.infrastructure.worker.service import WorkerService


async def main():
    if os.environ.get("AUTH_MODE") != "demo" or os.environ.get("AUDENTRA_ENV") not in {
        "preview",
        "development",
        "test",
    }:
        raise RuntimeError("This worker is restricted to the configured demo")
    engine = create_async_engine(
        os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://"),
        pool_size=2,
        max_overflow=0,
    )
    repository = OutboxRepository(
        engine,
        OutboxRepositoryConfig(
            worker_id="conference-document-worker",
            batch_size=2,
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
            api_internal_url=os.environ["API_INTERNAL_URL"],
            worker_token=os.environ["DOCUMENT_WORKER_TOKEN"],
            timeout_seconds=240,
        )
    )
    dispatcher = build_event_dispatcher(
        StudentDashboardProjector(engine, "conference-document-dashboard"),
        runner,
        DocumentReviewProjector(engine, "conference-document-review"),
    )
    try:
        await WorkerService(repository, dispatcher).run()
    finally:
        await repository.release_claims()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
