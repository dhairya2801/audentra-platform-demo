"""Production outbox-worker composition, independent of any web framework."""

from __future__ import annotations

from dataclasses import dataclass, replace

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.messaging.outbox import OutboxRepository, OutboxRepositoryConfig
from audentra.infrastructure.worker.dashboard_projector import StudentDashboardProjector
from audentra.infrastructure.worker.document_commands import (
    DocumentCommandSettings,
    DocumentExtractionRunner,
)
from audentra.infrastructure.worker.factory import build_event_dispatcher
from audentra.infrastructure.worker.service import WorkerService

from .settings import RuntimeSettings


@dataclass(slots=True)
class WorkerRuntimeResources:
    engine: AsyncEngine
    http_client: httpx.AsyncClient
    repository: OutboxRepository
    worker: WorkerService

    async def close(self) -> None:
        self.worker.stop()
        try:
            await self.repository.release_claims()
        finally:
            try:
                await self.http_client.aclose()
            finally:
                await self.engine.dispose()


async def build_worker_runtime(settings: RuntimeSettings) -> WorkerRuntimeResources:
    database_options = replace(settings.database, application_name="audentra-worker")
    engine = create_database_engine(settings.database_url, database_options)
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        timeout=httpx.Timeout(settings.worker.command_timeout_seconds, connect=10.0, pool=5.0),
        follow_redirects=False,
    )
    try:
        outbox = OutboxRepository(
            engine,
            OutboxRepositoryConfig(
                worker_id=settings.worker.worker_id,
                batch_size=settings.worker.batch_size,
                lease_seconds=settings.worker.lease_seconds,
                max_attempts=settings.worker.max_attempts,
                base_retry_ms=settings.worker.base_retry_ms,
                max_retry_ms=settings.worker.max_retry_ms,
            ),
        )
        projector = StudentDashboardProjector(
            engine,
            settings.worker.consumer_name,
        )
        document_runner = DocumentExtractionRunner(
            DocumentCommandSettings(
                api_internal_url=settings.worker.api_internal_url,
                worker_token=settings.document_worker_token,
                timeout_seconds=settings.worker.command_timeout_seconds,
            ),
            client=http_client,
        )
        dispatcher = build_event_dispatcher(projector, document_runner)
        worker = WorkerService(
            outbox,
            dispatcher,
            poll_interval_seconds=settings.worker.poll_interval_seconds,
        )
        return WorkerRuntimeResources(engine, http_client, outbox, worker)
    except BaseException:
        try:
            await http_client.aclose()
        finally:
            await engine.dispose()
        raise
