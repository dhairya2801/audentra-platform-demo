"""Production outbox-worker composition, independent of any web framework."""

from __future__ import annotations

from dataclasses import dataclass, replace

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.ports import UnavailableBrowserAuthService
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.messaging.outbox import OutboxRepository, OutboxRepositoryConfig
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.staff_email_service import PostgresStaffEmailService
from audentra.infrastructure.storage import ObjectStorage, create_object_storage
from audentra.infrastructure.worker.action_center_enrichment import ActionCenterEnrichmentRunner
from audentra.infrastructure.worker.agentic_scheduler import AgenticWorkflowScheduler
from audentra.infrastructure.worker.call_transcription import CallTranscriptionRunner
from audentra.infrastructure.worker.dashboard_projector import StudentDashboardProjector
from audentra.infrastructure.worker.document_commands import (
    DocumentCommandSettings,
    DocumentExtractionRunner,
)
from audentra.infrastructure.worker.document_review_projector import DocumentReviewProjector
from audentra.infrastructure.worker.factory import build_event_dispatcher
from audentra.infrastructure.worker.service import WorkerService
from audentra.infrastructure.worker.staff_email import StaffEmailEventRunner
from audentra.integrations.ai.gateway import StudentAIGateway
from audentra.integrations.ai.provider import CompletionClient

from .settings import RuntimeSettings


@dataclass(slots=True)
class WorkerRuntimeResources:
    engine: AsyncEngine
    http_client: httpx.AsyncClient
    repository: OutboxRepository
    worker: WorkerService
    object_storage: ObjectStorage | None = None

    async def close(self) -> None:
        self.worker.stop()
        try:
            await self.repository.release_claims()
        finally:
            try:
                await self.http_client.aclose()
            finally:
                try:
                    if self.object_storage is not None:
                        await self.object_storage.close()
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
    object_storage: ObjectStorage | None = None
    try:
        object_storage = create_object_storage(settings.object_storage)
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
                api_internal_audience=settings.worker.api_internal_audience,
                worker_token=settings.document_worker_token,
                timeout_seconds=settings.worker.command_timeout_seconds,
            ),
            client=http_client,
        )
        document_review_projector = DocumentReviewProjector(
            engine,
            settings.worker.consumer_name + ":document-review",
        )
        scheduled_workflows = AgenticWorkflowScheduler(engine)
        platform = PostgresPlatformRepository(engine)
        enrichment_gateway = StudentAIGateway(
            settings.ai,
            CompletionClient(http_client, recorder=platform.record_ai_provider_response),
        )
        action_center_enrichment = ActionCenterEnrichmentRunner(
            engine,
            enrichment_gateway,
            worker_id=f"{settings.worker.worker_id}:action-center",
        )
        call_transcription = CallTranscriptionRunner(
            engine,
            object_storage,
            http_client,
            api_key=settings.ai.openrouter_api_key,
            model=settings.ai.openrouter_transcription_model,
            app_url=settings.ai.app_url,
            app_name=settings.ai.app_name,
            worker_id=f"{settings.worker.worker_id}:call-transcription",
        )
        staff_email = StaffEmailEventRunner(
            PostgresStaffEmailService(
                engine,
                http_client,
                settings.institutional_oauth,
                UnavailableBrowserAuthService(),
            )
        )
        dispatcher = build_event_dispatcher(
            projector,
            document_runner,
            document_review_projector,
            staff_email,
        )
        worker = WorkerService(
            outbox,
            dispatcher,
            poll_interval_seconds=settings.worker.poll_interval_seconds,
            scheduled_runner=scheduled_workflows,
            scheduled_interval_seconds=settings.worker.scheduled_interval_seconds,
            enrichment_runner=action_center_enrichment,
            enrichment_interval_seconds=settings.worker.poll_interval_seconds,
            transcription_runner=call_transcription,
            transcription_interval_seconds=settings.worker.poll_interval_seconds,
        )
        return WorkerRuntimeResources(
            engine=engine,
            http_client=http_client,
            repository=outbox,
            worker=worker,
            object_storage=object_storage,
        )
    except BaseException:
        try:
            await http_client.aclose()
        finally:
            try:
                if object_storage is not None:
                    await object_storage.close()
            finally:
                await engine.dispose()
        raise
