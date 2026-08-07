"""Sequential polling loop preserving the legacy worker's lease semantics."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol

from audentra.infrastructure.messaging.dispatcher import EventDispatcher
from audentra.infrastructure.messaging.envelope import ClaimedOutboxEvent
from audentra.infrastructure.messaging.outbox import OutboxRepository


class ScheduledWorkflowRunner(Protocol):
    async def run_once(self) -> int: ...


@dataclass(slots=True)
class WorkerStatus:
    started_at: datetime
    stopping: bool = False
    polling: bool = False
    in_flight: int = 0
    processed: int = 0
    failed: int = 0
    scheduled_runs: int = 0
    scheduled_failures: int = 0
    last_successful_poll_at: datetime | None = None
    last_scheduled_run_at: datetime | None = None
    last_poll_error: str | None = None


class WorkerService:
    def __init__(
        self,
        repository: OutboxRepository,
        dispatcher: EventDispatcher,
        *,
        poll_interval_seconds: float = 1.0,
        scheduled_runner: ScheduledWorkflowRunner | None = None,
        scheduled_interval_seconds: float = 300.0,
        logger: logging.Logger | None = None,
    ) -> None:
        if not 0.05 <= poll_interval_seconds <= 60:
            raise ValueError("poll_interval_seconds must be between 0.05 and 60")
        if not 60 <= scheduled_interval_seconds <= 3_600:
            raise ValueError("scheduled_interval_seconds must be between 60 and 3600")
        self._repository = repository
        self._dispatcher = dispatcher
        self._poll_interval_seconds = poll_interval_seconds
        self._scheduled_runner = scheduled_runner
        self._scheduled_interval_seconds = scheduled_interval_seconds
        self._logger = logger or logging.getLogger(__name__)
        self._stop_event = asyncio.Event()
        self._status = WorkerStatus(started_at=datetime.now(UTC))

    def snapshot(self) -> WorkerStatus:
        return replace(self._status)

    async def run_once(self) -> int:
        """Claim and process one batch; useful for tests and one-shot jobs."""

        await self._run_scheduled_if_due()
        self._status.polling = True
        try:
            batch = await self._repository.claim_batch()
            self._status.last_successful_poll_at = datetime.now(UTC)
            self._status.last_poll_error = None
        finally:
            self._status.polling = False

        for rejected in batch.rejected:
            self._status.failed += 1
            try:
                failure = await self._repository.reject_malformed(rejected)
                self._logger.error(
                    "malformed_outbox_event_rejected",
                    extra={
                        "outbox_id": rejected.outbox_id,
                        "attempts": failure.attempts,
                        "dead_lettered": failure.dead_lettered,
                        "failure_recorded": failure.recorded,
                    },
                )
            except Exception:
                self._logger.exception(
                    "malformed_outbox_failure_recording_failed",
                    extra={"outbox_id": rejected.outbox_id},
                )

        processed_in_batch = 0
        for event in batch.events:
            if self._status.stopping:
                break
            if await self._process(event):
                processed_in_batch += 1
        return processed_in_batch

    async def _run_scheduled_if_due(self) -> None:
        if self._scheduled_runner is None:
            return
        last_run = self._status.last_scheduled_run_at
        now = datetime.now(UTC)
        if last_run is not None and (
            now - last_run
        ).total_seconds() < self._scheduled_interval_seconds:
            return
        self._status.last_scheduled_run_at = now
        try:
            await self._scheduled_runner.run_once()
            self._status.scheduled_runs += 1
        except Exception:
            self._status.scheduled_failures += 1
            self._logger.exception("scheduled_agentic_workflow_failed")

    async def run(self) -> None:
        self._logger.info(
            "worker_poll_loop_started",
            extra={"poll_interval_seconds": self._poll_interval_seconds},
        )
        while not self._status.stopping:
            try:
                processed = await self.run_once()
                if processed == 0:
                    await self._interruptible_delay()
            except Exception as error:
                self._status.polling = False
                self._status.last_poll_error = str(error)
                self._logger.exception("worker_poll_failed")
                await self._interruptible_delay()
        self._logger.info("worker_poll_loop_stopped")

    def stop(self) -> None:
        if self._status.stopping:
            return
        self._status.stopping = True
        self._stop_event.set()
        self._logger.info(
            "worker_stop_requested",
            extra={"in_flight": self._status.in_flight},
        )

    async def _process(self, event: ClaimedOutboxEvent) -> bool:
        self._status.in_flight += 1
        started_at = perf_counter()
        try:
            lease_renewed = await self._repository.renew_lease(event)
            if not lease_renewed:
                self._logger.warning(
                    "outbox_event_lease_lost",
                    extra={"event_id": event.event_id, "outbox_id": event.outbox_id},
                )
                return False
            outcome = await self._dispatcher.dispatch(event)
            completed = await self._repository.complete(event)
            if not completed:
                self._logger.warning(
                    "outbox_event_acknowledgement_lost",
                    extra={"event_id": event.event_id, "outbox_id": event.outbox_id},
                )
                return False
            self._status.processed += 1
            self._logger.info(
                "outbox_event_processed",
                extra={
                    "event_id": event.event_id,
                    "event_name": event.event_name,
                    "outcome": outcome.status,
                    "duration_ms": round((perf_counter() - started_at) * 1_000),
                },
            )
            return True
        except Exception as error:
            self._status.failed += 1
            try:
                failure = await self._repository.fail(event, error)
                self._logger.error(
                    "outbox_event_failed",
                    extra={
                        "event_id": event.event_id,
                        "event_name": event.event_name,
                        "attempts": failure.attempts,
                        "dead_lettered": failure.dead_lettered,
                        "failure_recorded": failure.recorded,
                    },
                )
            except Exception:
                self._logger.exception(
                    "outbox_failure_recording_failed",
                    extra={"event_id": event.event_id},
                )
            return False
        finally:
            self._status.in_flight -= 1

    async def _interruptible_delay(self) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=self._poll_interval_seconds,
            )
