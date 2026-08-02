"""Private API commands invoked for durable document-processing events."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from audentra.infrastructure.messaging.envelope import DomainEventEnvelope, get_event_string


@dataclass(frozen=True, slots=True)
class DocumentCommandSettings:
    api_internal_url: str = "http://localhost:4000"
    worker_token: str = ""
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.api_internal_url.startswith(("http://", "https://")):
            raise ValueError("api_internal_url must be HTTP(S)")
        if not self.worker_token.strip():
            raise ValueError("worker_token is required")
        if not 1 <= self.timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")


class DocumentExtractionRunner:
    def __init__(
        self,
        settings: DocumentCommandSettings,
        *,
        client: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._logger = logger or logging.getLogger(__name__)

    async def handle(self, event: DomainEventEnvelope) -> None:
        student_id = get_event_string(event.data, "studentId", "student_id")
        if student_id is None:
            raise ValueError("document extraction event is missing studentId")
        actor_id = event.actor.id if event.actor is not None else None
        if actor_id is None:
            raise ValueError("document extraction event is missing an actor id")
        command_path = (
            "document-extraction-reservations"
            if event.event_name == "document.upload_reserved.v1"
            else "document-extractions"
        )
        url = (
            f"{self._settings.api_internal_url.rstrip('/')}"
            f"/v1/student/internal/{command_path}/{event.aggregate_id}"
        )
        headers = {
            "x-vv-worker-token": self._settings.worker_token,
            "x-demo-tenant-id": event.tenant_id,
            "x-demo-student-id": student_id,
            "x-demo-actor-id": actor_id,
            "x-correlation-id": event.correlation_id or event.event_id,
        }
        if self._client is None:
            async with httpx.AsyncClient(timeout=self._settings.timeout_seconds) as client:
                response = await client.post(url, headers=headers)
        else:
            response = await self._client.post(url, headers=headers)
        if not response.is_success:
            raise RuntimeError(
                "document extraction command failed with "
                f"{response.status_code}: {response.text[:400]}"
            )
        self._logger.info(
            "document_extraction_command_completed",
            extra={
                "event_id": event.event_id,
                "document_id": event.aggregate_id,
                "status": response.status_code,
            },
        )
