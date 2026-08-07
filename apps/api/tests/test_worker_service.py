from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from audentra.infrastructure.messaging.dispatcher import DispatchOutcome
from audentra.infrastructure.messaging.envelope import ClaimedOutboxEvent, DomainEventActor
from audentra.infrastructure.messaging.outbox import (
    ClaimedBatch,
    FailureResult,
    RejectedOutboxEvent,
)
from audentra.infrastructure.worker.service import WorkerService


def _event(index: int = 1) -> ClaimedOutboxEvent:
    return ClaimedOutboxEvent(
        outbox_id=f"outbox-{index}",
        attempts=0,
        event_id=f"event-{index}",
        event_name="student.profile_updated.v1",
        occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
        tenant_id="tenant-1",
        aggregate_type="student",
        aggregate_id=f"student-{index}",
        aggregate_version=1,
        actor=DomainEventActor(type="student", id=f"student-{index}"),
        correlation_id="request-1",
        causation_id="command-1",
        data={"studentId": f"student-{index}"},
    )


def _failure(*, recorded: bool = True) -> FailureResult:
    return FailureResult(
        recorded=recorded,
        dead_lettered=False,
        attempts=1,
        next_attempt_at=datetime(2026, 8, 2, 0, 1, tzinfo=UTC),
    )


class FakeOutboxRepository:
    def __init__(self, batch: ClaimedBatch | None = None) -> None:
        self.batch = batch or ClaimedBatch(events=(), rejected=())
        self.claim_error: BaseException | None = None
        self.claim_count = 0
        self.claimed = asyncio.Event()
        self.lease_results: list[object] = []
        self.complete_results: list[object] = []
        self.fail_result: object = _failure()
        self.reject_results: list[object] = []
        self.renewed: list[str] = []
        self.completed: list[str] = []
        self.failed: list[tuple[str, object]] = []
        self.rejected: list[str] = []

    async def claim_batch(self) -> ClaimedBatch:
        self.claim_count += 1
        self.claimed.set()
        if self.claim_error is not None:
            raise self.claim_error
        return self.batch

    async def renew_lease(self, event: ClaimedOutboxEvent) -> bool:
        self.renewed.append(event.event_id)
        result = self.lease_results.pop(0) if self.lease_results else True
        if isinstance(result, BaseException):
            raise result
        return bool(result)

    async def complete(self, event: ClaimedOutboxEvent) -> bool:
        self.completed.append(event.event_id)
        result = self.complete_results.pop(0) if self.complete_results else True
        if isinstance(result, BaseException):
            raise result
        return bool(result)

    async def fail(self, event: ClaimedOutboxEvent, error: object) -> FailureResult:
        self.failed.append((event.event_id, error))
        if isinstance(self.fail_result, BaseException):
            raise self.fail_result
        return cast(FailureResult, self.fail_result)

    async def reject_malformed(self, event: RejectedOutboxEvent) -> FailureResult:
        self.rejected.append(event.outbox_id)
        result = self.reject_results.pop(0) if self.reject_results else _failure()
        if isinstance(result, BaseException):
            raise result
        return cast(FailureResult, result)


class FakeDispatcher:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.errors: dict[str, BaseException] = {}
        self.after_dispatch: Callable[[], None] | None = None

    async def dispatch(self, event: ClaimedOutboxEvent) -> DispatchOutcome:
        self.events.append(event.event_id)
        error = self.errors.get(event.event_id)
        if error is not None:
            raise error
        if self.after_dispatch is not None:
            self.after_dispatch()
        return DispatchOutcome(status="ignored", event_name=event.event_name, reason="canonical")


class FakeScheduledRunner:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    async def run_once(self) -> int:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return 1


def _worker(
    repository: FakeOutboxRepository,
    dispatcher: FakeDispatcher,
    *,
    interval: float = 0.05,
) -> WorkerService:
    return WorkerService(
        cast(Any, repository),
        cast(Any, dispatcher),
        poll_interval_seconds=interval,
    )


@pytest.mark.parametrize("interval", [0.049, 60.01])
def test_worker_rejects_unsafe_poll_intervals(interval: float) -> None:
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        _worker(FakeOutboxRepository(), FakeDispatcher(), interval=interval)


def test_worker_renews_each_lease_and_skips_stale_or_unacknowledged_events() -> None:
    events = (_event(1), _event(2), _event(3))
    repository = FakeOutboxRepository(ClaimedBatch(events=events, rejected=()))
    repository.lease_results = [True, False, True]
    repository.complete_results = [True, False]
    dispatcher = FakeDispatcher()
    worker = _worker(repository, dispatcher)

    processed = asyncio.run(worker.run_once())

    assert processed == 1
    assert repository.renewed == ["event-1", "event-2", "event-3"]
    assert dispatcher.events == ["event-1", "event-3"]
    assert repository.completed == ["event-1", "event-3"]
    status = worker.snapshot()
    assert status.processed == 1
    assert status.failed == 0
    assert status.in_flight == 0
    assert status.polling is False
    assert status.last_successful_poll_at is not None


def test_worker_records_handler_failure_without_acknowledging_event() -> None:
    event = _event()
    repository = FakeOutboxRepository(ClaimedBatch(events=(event,), rejected=()))
    dispatcher = FakeDispatcher()
    dispatcher.errors[event.event_id] = RuntimeError("handler unavailable")
    worker = _worker(repository, dispatcher)

    assert asyncio.run(worker.run_once()) == 0

    assert repository.completed == []
    assert len(repository.failed) == 1
    assert str(repository.failed[0][1]) == "handler unavailable"
    assert worker.snapshot().failed == 1
    assert worker.snapshot().in_flight == 0


def test_worker_runs_scheduled_workflows_once_per_interval() -> None:
    repository = FakeOutboxRepository()
    runner = FakeScheduledRunner()
    worker = WorkerService(
        cast(Any, repository),
        cast(Any, FakeDispatcher()),
        poll_interval_seconds=0.05,
        scheduled_runner=runner,
        scheduled_interval_seconds=60,
    )

    asyncio.run(worker.run_once())
    asyncio.run(worker.run_once())

    assert runner.calls == 1
    assert worker.snapshot().scheduled_runs == 1
    assert worker.snapshot().scheduled_failures == 0


def test_worker_records_scheduled_failure_and_still_polls_outbox() -> None:
    repository = FakeOutboxRepository()
    runner = FakeScheduledRunner(RuntimeError("scheduler unavailable"))
    worker = WorkerService(
        cast(Any, repository),
        cast(Any, FakeDispatcher()),
        poll_interval_seconds=0.05,
        scheduled_runner=runner,
        scheduled_interval_seconds=60,
    )

    assert asyncio.run(worker.run_once()) == 0
    assert repository.claim_count == 1
    assert worker.snapshot().scheduled_failures == 1


def test_worker_survives_failure_recording_error() -> None:
    event = _event()
    repository = FakeOutboxRepository(ClaimedBatch(events=(event,), rejected=()))
    repository.fail_result = RuntimeError("database unavailable")
    dispatcher = FakeDispatcher()
    dispatcher.errors[event.event_id] = ValueError("bad event")
    worker = _worker(repository, dispatcher)

    assert asyncio.run(worker.run_once()) == 0
    assert worker.snapshot().failed == 1
    assert repository.failed[0][0] == event.event_id


def test_worker_rejects_all_malformed_rows_and_continues_after_recording_error() -> None:
    rejected = (
        RejectedOutboxEvent("bad-1", 0, ValueError("invalid JSON")),
        RejectedOutboxEvent("bad-2", 2, ValueError("missing tenant")),
    )
    repository = FakeOutboxRepository(ClaimedBatch(events=(), rejected=rejected))
    repository.reject_results = [_failure(), RuntimeError("write failed")]
    worker = _worker(repository, FakeDispatcher())

    assert asyncio.run(worker.run_once()) == 0
    assert repository.rejected == ["bad-1", "bad-2"]
    assert worker.snapshot().failed == 2


def test_stop_is_idempotent_and_prevents_claimed_tail_from_starting() -> None:
    repository = FakeOutboxRepository(ClaimedBatch(events=(_event(1), _event(2)), rejected=()))
    dispatcher = FakeDispatcher()
    worker = _worker(repository, dispatcher)
    dispatcher.after_dispatch = worker.stop

    assert asyncio.run(worker.run_once()) == 1
    worker.stop()

    assert dispatcher.events == ["event-1"]
    snapshot = worker.snapshot()
    assert snapshot.stopping is True
    snapshot.processed = 999
    assert worker.snapshot().processed == 1


def test_run_loop_reports_poll_error_and_stop_interrupts_backoff() -> None:
    async def exercise() -> WorkerService:
        repository = FakeOutboxRepository()
        repository.claim_error = RuntimeError("database offline")
        worker = _worker(repository, FakeDispatcher())
        task = asyncio.create_task(worker.run())
        await repository.claimed.wait()
        worker.stop()
        await asyncio.wait_for(task, timeout=1)
        return worker

    worker = asyncio.run(exercise())
    assert worker.snapshot().last_poll_error == "database offline"
    assert worker.snapshot().polling is False


def test_run_loop_exits_immediately_after_successful_handler_requests_stop() -> None:
    async def exercise() -> tuple[WorkerService, FakeOutboxRepository]:
        repository = FakeOutboxRepository(ClaimedBatch(events=(_event(),), rejected=()))
        dispatcher = FakeDispatcher()
        worker = _worker(repository, dispatcher)
        dispatcher.after_dispatch = worker.stop
        await asyncio.wait_for(worker.run(), timeout=1)
        return worker, repository

    worker, repository = asyncio.run(exercise())
    assert worker.snapshot().processed == 1
    assert repository.claim_count == 1
