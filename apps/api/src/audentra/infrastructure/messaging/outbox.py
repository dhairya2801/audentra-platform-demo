# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""PostgreSQL transactional-outbox repository with lease ownership fencing."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .envelope import ClaimedOutboxEvent, DomainEventEnvelope, parse_outbox_event

_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ONE_HUNDRED_YEARS_MS = 100 * 365 * 24 * 60 * 60 * 1_000

CLAIM_SQL_TEMPLATE = """
WITH candidates AS (
  SELECT id
  FROM {table}
  WHERE published_at IS NULL
    AND (:filter_events = false OR event_name = ANY(:event_names))
    AND next_attempt_at <= NOW()
    AND attempts < :max_attempts
    AND (
      locked_at IS NULL
      OR locked_at < NOW() - (:lease_seconds * INTERVAL '1 second')
    )
  ORDER BY occurred_at ASC, id ASC
  FOR UPDATE SKIP LOCKED
  LIMIT :batch_size
)
UPDATE {table} AS event
SET locked_at = NOW(),
    locked_by = :worker_id
FROM candidates
WHERE event.id = candidates.id
RETURNING event.*
"""


@dataclass(frozen=True, slots=True)
class OutboxRepositoryConfig:
    worker_id: str
    batch_size: int = 20
    lease_seconds: int = 60
    max_attempts: int = 10
    base_retry_ms: int = 1_000
    max_retry_ms: int = 300_000
    schema: str = "public"
    event_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.worker_id.strip() or len(self.worker_id) > 128:
            raise ValueError("worker_id must be between 1 and 128 characters")
        if not 1 <= self.batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        if not 5 <= self.lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        if not 1 <= self.max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        if not 100 <= self.base_retry_ms <= 3_600_000:
            raise ValueError("base_retry_ms is outside the supported range")
        if not 1_000 <= self.max_retry_ms <= 86_400_000:
            raise ValueError("max_retry_ms is outside the supported range")
        if self.base_retry_ms > self.max_retry_ms:
            raise ValueError("base_retry_ms cannot exceed max_retry_ms")
        if not _SQL_IDENTIFIER.fullmatch(self.schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")


@dataclass(frozen=True, slots=True)
class RejectedOutboxEvent:
    outbox_id: str
    attempts: int
    error: Exception


@dataclass(frozen=True, slots=True)
class ClaimedBatch:
    events: tuple[ClaimedOutboxEvent, ...]
    rejected: tuple[RejectedOutboxEvent, ...]


@dataclass(frozen=True, slots=True)
class FailureResult:
    recorded: bool
    dead_lettered: bool
    attempts: int
    next_attempt_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxStats:
    pending: int
    retrying: int
    dead_lettered: int
    oldest_pending_at: datetime | None


class OutboxRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        config: OutboxRepositoryConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._config = config
        self._table = f"{config.schema}.outbox_event"
        self._clock = clock or (lambda: datetime.now(UTC))

    async def enqueue(
        self,
        connection: AsyncConnection,
        event: DomainEventEnvelope,
    ) -> None:
        """Insert on the caller's transaction so state and event commit atomically."""

        if event.actor is None or event.actor.id is None:
            raise ValueError("persisted outbox events require an actor id")
        if event.aggregate_version is None:
            raise ValueError("persisted outbox events require an aggregate version")
        payload = json.dumps(
            event.model_dump(mode="json", by_alias=True),
            separators=(",", ":"),
        )
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table} (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload, created_at
                )
                VALUES (
                  :id, :tenant_id, :event_name, :aggregate_type, :aggregate_id,
                  :aggregate_version, :occurred_at, :actor_type, :actor_id,
                  :correlation_id, :causation_id, CAST(:payload AS jsonb), NOW()
                )
                """
            ),
            {
                "id": event.event_id,
                "tenant_id": event.tenant_id,
                "event_name": event.event_name,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "aggregate_version": event.aggregate_version,
                "occurred_at": event.occurred_at,
                "actor_type": event.actor.type,
                "actor_id": event.actor.id,
                "correlation_id": event.correlation_id or event.event_id,
                "causation_id": event.causation_id or event.event_id,
                "payload": payload,
            },
        )

    async def claim_batch(self) -> ClaimedBatch:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(CLAIM_SQL_TEMPLATE.format(table=self._table)),
                {
                    "worker_id": self._config.worker_id,
                    "filter_events": bool(self._config.event_names),
                    "event_names": list(self._config.event_names),
                    "lease_seconds": self._config.lease_seconds,
                    "batch_size": self._config.batch_size,
                    "max_attempts": self._config.max_attempts,
                },
            )
            rows = [dict(row) for row in result.mappings().all()]

        events: list[ClaimedOutboxEvent] = []
        rejected: list[RejectedOutboxEvent] = []
        for row in rows:
            try:
                events.append(parse_outbox_event(row))
            except Exception as error:
                rejected.append(
                    RejectedOutboxEvent(
                        outbox_id=str(row.get("id")),
                        attempts=_safe_attempt_count(
                            row.get("attempts", row.get("attempt_count", 0))
                        ),
                        error=error,
                    )
                )
        return ClaimedBatch(events=tuple(events), rejected=tuple(rejected))

    async def complete(self, event: ClaimedOutboxEvent) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table}
                    SET published_at = NOW(),
                        locked_at = NULL,
                        locked_by = NULL,
                        last_error = NULL
                    WHERE id = :outbox_id
                      AND published_at IS NULL
                      AND locked_by = :worker_id
                    """
                ),
                {
                    "outbox_id": event.outbox_id,
                    "worker_id": self._config.worker_id,
                },
            )
            return result.rowcount == 1

    async def renew_lease(self, event: ClaimedOutboxEvent) -> bool:
        """Fence a queued batch item immediately before its handler starts.

        A sequential worker can hold later items longer than one lease interval. If
        another worker has already reclaimed one of those items, this update fails
        and the stale owner must not dispatch it.
        """

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table}
                    SET locked_at = NOW()
                    WHERE id = :outbox_id
                      AND published_at IS NULL
                      AND locked_by = :worker_id
                    """
                ),
                {
                    "outbox_id": event.outbox_id,
                    "worker_id": self._config.worker_id,
                },
            )
            return result.rowcount == 1

    async def fail(self, event: ClaimedOutboxEvent, error: object) -> FailureResult:
        return await self._record_failure(
            outbox_id=event.outbox_id,
            previous_attempts=event.attempts,
            stable_seed=event.event_id,
            error=error,
        )

    async def reject_malformed(self, event: RejectedOutboxEvent) -> FailureResult:
        return await self._record_failure(
            outbox_id=event.outbox_id,
            previous_attempts=event.attempts,
            stable_seed=event.outbox_id,
            error=event.error,
        )

    async def _record_failure(
        self,
        *,
        outbox_id: str,
        previous_attempts: int,
        stable_seed: str,
        error: object,
    ) -> FailureResult:
        attempts = previous_attempts + 1
        dead_lettered = attempts >= self._config.max_attempts
        delay_ms = (
            _ONE_HUNDRED_YEARS_MS
            if dead_lettered
            else calculate_retry_delay(
                attempts,
                self._config.base_retry_ms,
                self._config.max_retry_ms,
                stable_seed,
            )
        )
        next_attempt_at = self._clock() + timedelta(milliseconds=delay_ms)
        message = _error_message(error)[:8_000]
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table}
                    SET attempts = attempts + 1,
                        next_attempt_at = :next_attempt_at,
                        last_error = :last_error,
                        locked_at = NULL,
                        locked_by = NULL
                    WHERE id = :outbox_id
                      AND published_at IS NULL
                      AND locked_by = :worker_id
                    """
                ),
                {
                    "outbox_id": outbox_id,
                    "worker_id": self._config.worker_id,
                    "next_attempt_at": next_attempt_at,
                    "last_error": message,
                },
            )
            recorded = result.rowcount == 1
        return FailureResult(
            recorded=recorded,
            dead_lettered=dead_lettered,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
        )

    async def release_claims(self) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table}
                    SET locked_at = NULL,
                        locked_by = NULL
                    WHERE published_at IS NULL
                      AND locked_by = :worker_id
                    """
                ),
                {"worker_id": self._config.worker_id},
            )
            return result.rowcount or 0

    async def ping(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text(f"SELECT 1 FROM {self._table} LIMIT 0"))

    async def stats(self) -> OutboxStats:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT
                      COUNT(*) FILTER (
                        WHERE published_at IS NULL AND attempts < :max_attempts
                      ) AS pending,
                      COUNT(*) FILTER (
                        WHERE published_at IS NULL
                          AND attempts > 0
                          AND attempts < :max_attempts
                      ) AS retrying,
                      COUNT(*) FILTER (
                        WHERE published_at IS NULL AND attempts >= :max_attempts
                      ) AS dead_lettered,
                      MIN(occurred_at) FILTER (
                        WHERE published_at IS NULL AND attempts < :max_attempts
                      ) AS oldest_pending_at
                    FROM {self._table}
                    """
                ),
                {"max_attempts": self._config.max_attempts},
            )
            row = result.mappings().one()
        oldest = row["oldest_pending_at"]
        return OutboxStats(
            pending=int(row["pending"]),
            retrying=int(row["retrying"]),
            dead_lettered=int(row["dead_lettered"]),
            oldest_pending_at=oldest if isinstance(oldest, datetime) else None,
        )


def calculate_retry_delay(
    attempts: int,
    base_retry_ms: int,
    max_retry_ms: int,
    stable_seed: str,
) -> int:
    """Reproduce the legacy worker's deterministic exponential jitter."""

    exponent = max(0, attempts - 1)
    bounded = min(max_retry_ms, base_retry_ms * 2**exponent)
    hash_value = 0
    for character in stable_seed:
        code_point = ord(character)
        # JavaScript `for ... of` yields a full Unicode character, while
        # charCodeAt(0) hashes only its first UTF-16 code unit.
        code_unit = code_point if code_point <= 0xFFFF else 0xD800 + ((code_point - 0x10000) >> 10)
        hash_value = (hash_value * 31 + code_unit) & 0xFFFFFFFF
    jitter_factor = 0.8 + (hash_value % 401) / 1_000
    # All values are positive, so truncation after adding 0.5 matches
    # JavaScript Math.round without Python's bankers-rounding behavior.
    rounded = int(bounded * jitter_factor + 0.5)
    return min(max_retry_ms, max(base_retry_ms, rounded))


def _safe_attempt_count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and value.isdigit():
        number = int(value)
    else:
        return 0
    return number if number >= 0 else 0


def _error_message(error: object) -> str:
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {error}"
    if isinstance(error, str):
        return error
    try:
        return json.dumps(error, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return "Unknown worker error"
