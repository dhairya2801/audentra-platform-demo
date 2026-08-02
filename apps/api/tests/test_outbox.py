# ruff: noqa: S608 -- random isolated test-schema identifiers are controlled here.
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from audentra.infrastructure.db.engine import DatabaseEngineOptions, create_database_engine
from audentra.infrastructure.messaging.outbox import (
    CLAIM_SQL_TEMPLATE,
    OutboxRepository,
    OutboxRepositoryConfig,
    calculate_retry_delay,
)


def test_retry_delay_matches_legacy_exponential_jitter() -> None:
    assert calculate_retry_delay(1, 1_000, 300_000, "abc") == 1_000
    assert calculate_retry_delay(2, 1_000, 300_000, "abc") == 1_828
    assert calculate_retry_delay(3, 1_000, 300_000, "abc") == 3_656
    assert calculate_retry_delay(100, 1_000, 300_000, "abc") == 274_200


def test_retry_delay_is_deterministic_and_bounded() -> None:
    first = calculate_retry_delay(8, 500, 10_000, "event-123")
    assert first == calculate_retry_delay(8, 500, 10_000, "event-123")
    assert 500 <= first <= 10_000


def test_claim_query_uses_skip_locked_and_ownership_fencing() -> None:
    normalized = " ".join(CLAIM_SQL_TEMPLATE.split()).upper()
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert "LOCKED_BY = :WORKER_ID" in normalized
    assert "ATTEMPTS < :MAX_ATTEMPTS" in normalized


def test_schema_identifier_cannot_inject_sql() -> None:
    with pytest.raises(ValueError):
        OutboxRepositoryConfig(worker_id="worker-1", schema="public; DROP SCHEMA public")


@pytest.mark.postgres
def test_two_workers_claim_disjoint_rows_with_skip_locked() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    pytest.importorskip("asyncpg")

    async def exercise() -> None:
        schema = f"outbox_test_{uuid4().hex}"
        engine = create_database_engine(
            database_url,
            DatabaseEngineOptions(pool_size=2, application_name="outbox-integration-test"),
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
                await connection.execute(
                    text(
                        f"""
                        CREATE TABLE {schema}.outbox_event (
                          id text PRIMARY KEY,
                          tenant_id text NOT NULL,
                          event_name text NOT NULL,
                          aggregate_type text NOT NULL,
                          aggregate_id text NOT NULL,
                          aggregate_version integer NOT NULL,
                          occurred_at timestamptz NOT NULL,
                          actor_type text NOT NULL,
                          actor_id text NOT NULL,
                          correlation_id text NOT NULL,
                          causation_id text NOT NULL,
                          payload jsonb NOT NULL,
                          published_at timestamptz,
                          attempts integer NOT NULL DEFAULT 0,
                          next_attempt_at timestamptz NOT NULL DEFAULT NOW(),
                          locked_at timestamptz,
                          locked_by text,
                          last_error text,
                          created_at timestamptz NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                )
                for index in range(2):
                    event_id = f"event-{index}"
                    payload = {
                        "eventId": event_id,
                        "eventName": "student.profile_updated.v1",
                        "occurredAt": datetime.now(UTC).isoformat(),
                        "tenantId": "tenant-1",
                        "aggregateType": "student",
                        "aggregateId": f"student-{index}",
                        "aggregateVersion": 1,
                        "actor": {"type": "student", "id": "actor-1"},
                        "correlationId": "request-1",
                        "causationId": "command-1",
                        "data": {"studentId": f"student-{index}"},
                    }
                    await connection.execute(
                        text(
                            f"""
                            INSERT INTO {schema}.outbox_event (
                              id, tenant_id, event_name, aggregate_type,
                              aggregate_id, aggregate_version, occurred_at,
                              actor_type, actor_id, correlation_id, causation_id, payload
                            )
                            VALUES (
                              :id, 'tenant-1', 'student.profile_updated.v1', 'student',
                              :aggregate_id, 1, NOW(), 'student', 'actor-1',
                              'request-1', 'command-1', CAST(:payload AS jsonb)
                            )
                            """
                        ),
                        {
                            "id": event_id,
                            "aggregate_id": f"student-{index}",
                            "payload": json.dumps(payload),
                        },
                    )

            worker_one = OutboxRepository(
                engine,
                OutboxRepositoryConfig(worker_id="worker-1", batch_size=1, schema=schema),
            )
            worker_two = OutboxRepository(
                engine,
                OutboxRepositoryConfig(worker_id="worker-2", batch_size=1, schema=schema),
            )
            first, second = await asyncio.gather(worker_one.claim_batch(), worker_two.claim_batch())
            claimed = [first.events[0].outbox_id, second.events[0].outbox_id]
            assert len(set(claimed)) == 2
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await engine.dispose()

    asyncio.run(exercise())
