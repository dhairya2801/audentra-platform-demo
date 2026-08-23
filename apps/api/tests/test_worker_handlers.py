from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from pydantic import JsonValue

from audentra.infrastructure.messaging.envelope import DomainEventActor, DomainEventEnvelope
from audentra.infrastructure.worker import document_commands
from audentra.infrastructure.worker.document_commands import (
    DocumentCommandSettings,
    DocumentExtractionRunner,
)
from audentra.infrastructure.worker.factory import build_event_dispatcher

WORKER_TEST_TOKEN = "worker-secret"  # noqa: S105 -- inert test credential


def _event(
    event_name: str = "document.extraction_requested.v1",
    *,
    data: dict[str, str] | None = None,
    actor: dict[str, str | None] | None = None,
) -> DomainEventEnvelope:
    return DomainEventEnvelope(
        event_id="event-1",
        event_name=event_name,
        occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
        tenant_id="tenant-1",
        aggregate_type="document",
        aggregate_id="document-1",
        aggregate_version=1,
        actor=DomainEventActor.model_validate(
            actor if actor is not None else {"type": "student", "id": "actor-1"}
        ),
        correlation_id="correlation-1",
        causation_id="command-1",
        data=cast(
            dict[str, JsonValue],
            data if data is not None else {"studentId": "student-1"},
        ),
    )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"api_internal_url": "ftp://api", "worker_token": "secret"},
        {"api_internal_url": "http://api", "worker_token": "  "},
        {"api_internal_url": "http://api", "worker_token": "secret", "timeout_seconds": 0.9},
        {"api_internal_url": "http://api", "worker_token": "secret", "timeout_seconds": 301},
    ),
)
def test_document_command_settings_fail_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DocumentCommandSettings(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("event_name", "expected_path"),
    (
        (
            "document.upload_reserved.v1",
            "/v1/student/internal/document-extraction-reservations/document-1",
        ),
        (
            "document.extraction_requested.v1",
            "/v1/student/internal/document-extractions/document-1",
        ),
    ),
)
def test_document_runner_sends_tenant_scoped_authenticated_internal_command(
    event_name: str, expected_path: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner = DocumentExtractionRunner(
                DocumentCommandSettings(
                    api_internal_url="http://api.internal/",
                    worker_token=WORKER_TEST_TOKEN,
                ),
                client=client,
            )
            await runner.handle(_event(event_name))

    asyncio.run(exercise())

    assert requests[0].url.path == expected_path
    assert requests[0].method == "POST"
    assert requests[0].headers["x-vv-worker-token"] == WORKER_TEST_TOKEN
    assert requests[0].headers["x-demo-tenant-id"] == "tenant-1"
    assert requests[0].headers["x-demo-student-id"] == "student-1"
    assert requests[0].headers["x-demo-actor-id"] == "actor-1"
    assert requests[0].headers["x-correlation-id"] == "correlation-1"


def test_document_runner_adds_cloud_run_identity_when_an_audience_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        document_commands, "_fetch_google_id_token", lambda _audience: "signed-token"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner = DocumentExtractionRunner(
                DocumentCommandSettings(
                    api_internal_url="https://api.internal/",
                    api_internal_audience="https://api.internal/",
                    worker_token=WORKER_TEST_TOKEN,
                ),
                client=client,
            )
            await runner.handle(_event())

    asyncio.run(exercise())

    assert requests[0].headers["x-serverless-authorization"] == "Bearer signed-token"


@pytest.mark.parametrize(
    "event",
    (
        _event(data={}),
        _event(actor={"type": "system", "id": None}),
    ),
)
def test_document_runner_rejects_events_without_required_identity(
    event: DomainEventEnvelope,
) -> None:
    async def exercise() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            runner = DocumentExtractionRunner(
                DocumentCommandSettings(worker_token=WORKER_TEST_TOKEN), client=client
            )
            await runner.handle(event)

    with pytest.raises(ValueError, match="missing"):
        asyncio.run(exercise())


def test_document_runner_surfaces_bounded_upstream_failure_detail() -> None:
    async def exercise() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(503, text="x" * 500))
        async with httpx.AsyncClient(transport=transport) as client:
            runner = DocumentExtractionRunner(
                DocumentCommandSettings(worker_token=WORKER_TEST_TOKEN), client=client
            )
            await runner.handle(_event())

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(exercise())

    message = str(raised.value)
    assert message.startswith("document extraction command failed with 503: ")
    assert len(message.removeprefix("document extraction command failed with 503: ")) == 400


def test_document_runner_owns_and_closes_short_lived_client_when_not_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OwnedClient:
        entered = False
        exited = False
        request: tuple[str, dict[str, str]] | None = None

        def __init__(self, *, timeout: float) -> None:
            assert timeout == 12

        async def __aenter__(self) -> OwnedClient:
            self.entered = True
            return self

        async def __aexit__(self, *args: object) -> None:
            self.exited = True

        async def post(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
            self.request = (url, headers)
            return httpx.Response(200)

    owned = OwnedClient(timeout=12)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: owned)
    runner = DocumentExtractionRunner(
        DocumentCommandSettings(worker_token=WORKER_TEST_TOKEN, timeout_seconds=12)
    )

    asyncio.run(runner.handle(_event()))

    assert owned.entered is True
    assert owned.exited is True
    assert owned.request is not None


class RecordingHandler:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def handle(self, event: DomainEventEnvelope) -> None:
        self.events.append(event.event_name)


def test_dispatcher_factory_wires_each_handler_and_preserves_explicit_ignores() -> None:
    projector = RecordingHandler()
    document_runner = RecordingHandler()
    document_review_projector = RecordingHandler()
    staff_email = RecordingHandler()
    dispatcher = build_event_dispatcher(
        cast(Any, projector),
        cast(Any, document_runner),
        cast(Any, document_review_projector),
        cast(Any, staff_email),
    )

    async def exercise() -> list[str]:
        outcomes: list[str] = []
        for name in (
            "enrollment.journey_created.v1",
            "document.upload_reserved.v1",
            "document.extraction_requested.v1",
            "document.extraction_completed.v1",
            "document.stored_for_review.v1",
            "staff.mailbox_connected.v1",
            "staff.email_send_queued.v1",
            "student.profile_updated.v1",
        ):
            outcomes.append((await dispatcher.dispatch(_event(name))).status)
        return outcomes

    outcomes = asyncio.run(exercise())

    assert outcomes == [
        "handled",
        "handled",
        "handled",
        "handled",
        "handled",
        "handled",
        "handled",
        "ignored",
    ]
    assert projector.events == ["enrollment.journey_created.v1"]
    assert document_runner.events == [
        "document.upload_reserved.v1",
        "document.extraction_requested.v1",
    ]
    assert document_review_projector.events == [
        "document.extraction_completed.v1",
        "document.stored_for_review.v1",
    ]
    assert staff_email.events == [
        "staff.mailbox_connected.v1",
        "staff.email_send_queued.v1",
    ]
