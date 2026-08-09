from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from audentra.bootstrap import worker as worker_bootstrap
from audentra.bootstrap.settings import RuntimeSettings
from audentra.bootstrap.worker import WorkerRuntimeResources


class StubEngine:
    def __init__(self) -> None:
        self.closed = False

    async def dispose(self) -> None:
        self.closed = True


class StubHttpClient:
    def __init__(self, error: BaseException | None = None) -> None:
        self.closed = False
        self.error = error

    async def aclose(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error


class StubRepository:
    def __init__(self, error: BaseException | None = None) -> None:
        self.released = False
        self.error = error

    async def release_claims(self) -> int:
        self.released = True
        if self.error is not None:
            raise self.error
        return 2


class StubWorker:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _resources(
    *,
    repository_error: BaseException | None = None,
    http_error: BaseException | None = None,
) -> tuple[WorkerRuntimeResources, StubEngine, StubHttpClient, StubRepository, StubWorker]:
    engine = StubEngine()
    http = StubHttpClient(http_error)
    repository = StubRepository(repository_error)
    worker = StubWorker()
    resources = WorkerRuntimeResources(
        cast(Any, engine),
        cast(Any, http),
        cast(Any, repository),
        cast(Any, worker),
    )
    return resources, engine, http, repository, worker


def test_worker_close_releases_claims_before_closing_pools() -> None:
    resources, engine, http, repository, worker = _resources()

    asyncio.run(resources.close())

    assert worker.stopped is True
    assert repository.released is True
    assert http.closed is True
    assert engine.closed is True


def test_worker_close_attempts_http_and_engine_after_claim_release_failure() -> None:
    resources, engine, http, repository, worker = _resources(
        repository_error=RuntimeError("release failed")
    )

    with pytest.raises(RuntimeError, match="release failed"):
        asyncio.run(resources.close())

    assert worker.stopped is True
    assert repository.released is True
    assert http.closed is True
    assert engine.closed is True


def test_worker_close_attempts_engine_after_http_failure() -> None:
    resources, engine, http, repository, worker = _resources(
        http_error=RuntimeError("http close failed")
    )

    with pytest.raises(RuntimeError, match="http close failed"):
        asyncio.run(resources.close())

    assert worker.stopped is True
    assert repository.released is True
    assert http.closed is True
    assert engine.closed is True


@pytest.mark.anyio
async def test_worker_builder_wires_independent_worker_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = RuntimeSettings.from_environment(
        {"AUDENTRA_ENV": "test", "WORKER_ID": "worker-test"}, package_root=tmp_path
    )
    engine = StubEngine()
    http = StubHttpClient()
    repository = StubRepository()
    worker = StubWorker()
    captured: dict[str, object] = {}

    def create_engine(_url: str, options: object) -> StubEngine:
        captured["database_options"] = options
        return engine

    def create_repository(_engine: object, config: object) -> StubRepository:
        captured["outbox_config"] = config
        return repository

    def create_worker(
        outbox: object,
        dispatcher: object,
        *,
        poll_interval_seconds: float,
        scheduled_runner: object,
        scheduled_interval_seconds: float,
        enrichment_runner: object,
        enrichment_interval_seconds: float,
        transcription_runner: object,
        transcription_interval_seconds: float,
    ) -> StubWorker:
        captured["worker_repository"] = outbox
        captured["dispatcher"] = dispatcher
        captured["poll_interval"] = poll_interval_seconds
        captured["scheduled_runner"] = scheduled_runner
        captured["scheduled_interval"] = scheduled_interval_seconds
        captured["enrichment_runner"] = enrichment_runner
        captured["enrichment_interval"] = enrichment_interval_seconds
        captured["transcription_runner"] = transcription_runner
        captured["transcription_interval"] = transcription_interval_seconds
        return worker

    projector = object()
    runner = object()
    review_projector = object()
    scheduled_runner = object()
    dispatcher = object()
    monkeypatch.setattr(worker_bootstrap, "create_database_engine", create_engine)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: http)
    monkeypatch.setattr(worker_bootstrap, "OutboxRepository", create_repository)
    monkeypatch.setattr(
        worker_bootstrap,
        "StudentDashboardProjector",
        lambda *_args: projector,
    )
    monkeypatch.setattr(
        worker_bootstrap,
        "DocumentExtractionRunner",
        lambda *_args, **_kwargs: runner,
    )
    monkeypatch.setattr(
        worker_bootstrap,
        "DocumentReviewProjector",
        lambda *_args, **_kwargs: review_projector,
    )
    monkeypatch.setattr(
        worker_bootstrap,
        "AgenticWorkflowScheduler",
        lambda *_args, **_kwargs: scheduled_runner,
    )
    monkeypatch.setattr(
        worker_bootstrap,
        "build_event_dispatcher",
        lambda received_projector, received_runner, received_review_projector: (
            dispatcher
            if (received_projector, received_runner, received_review_projector)
            == (projector, runner, review_projector)
            else None
        ),
    )
    monkeypatch.setattr(worker_bootstrap, "WorkerService", create_worker)

    resources = await worker_bootstrap.build_worker_runtime(settings)

    assert cast(Any, resources.engine) is engine
    assert cast(Any, resources.http_client) is http
    assert cast(Any, resources.repository) is repository
    assert cast(Any, resources.worker) is worker
    assert resources.object_storage is not None
    assert cast(Any, captured["database_options"]).application_name == "audentra-worker"
    assert cast(Any, captured["outbox_config"]).worker_id == "worker-test"
    assert captured["worker_repository"] is repository
    assert captured["dispatcher"] is dispatcher
    assert captured["poll_interval"] == settings.worker.poll_interval_seconds
    assert captured["scheduled_runner"] is scheduled_runner
    assert captured["scheduled_interval"] == settings.worker.scheduled_interval_seconds
    assert captured["enrichment_runner"] is not None
    assert captured["enrichment_interval"] == settings.worker.poll_interval_seconds
    assert captured["transcription_runner"] is not None
    assert captured["transcription_interval"] == settings.worker.poll_interval_seconds


@pytest.mark.anyio
async def test_worker_builder_disposes_engine_even_when_composition_and_http_cleanup_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = RuntimeSettings.from_environment({"AUDENTRA_ENV": "test"}, package_root=tmp_path)
    engine = StubEngine()
    http = StubHttpClient(RuntimeError("http cleanup failed"))

    monkeypatch.setattr(worker_bootstrap, "create_database_engine", lambda *_args: engine)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: http)

    def fail_repository(*_args: object) -> object:
        raise ValueError("outbox composition failed")

    monkeypatch.setattr(worker_bootstrap, "OutboxRepository", fail_repository)

    with pytest.raises(RuntimeError, match="http cleanup failed"):
        await worker_bootstrap.build_worker_runtime(settings)

    assert http.closed is True
    assert engine.closed is True
