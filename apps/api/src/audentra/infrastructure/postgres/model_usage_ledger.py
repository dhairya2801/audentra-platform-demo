"""Model-usage accounting for interactive assistant turns.

`model_usage` was only ever written by the Action Center enrichment worker,
because a row must hang off an `agent_run`. Edward (staff and student) never
created one, so every interactive model call was invisible to cost tracking.
This records one `agent_run` + one `model_usage` per turn that actually used a
provider, accepting both token key shapes the gateways emit. Best effort:
accounting must never fail the answer.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_LOGGER = logging.getLogger(__name__)
_DETERMINISTIC_PROVIDERS = frozenset({"", "deterministic", "none", "local"})


def _token(usage: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0:
            return int(value)
    return None


def normalize_usage(usage: object) -> dict[str, int | None] | None:
    """Both gateway shapes (`promptTokens`/`inputTokens`) to one ledger row."""

    if not isinstance(usage, Mapping):
        return None
    normalized = {
        "input_tokens": _token(usage, "inputTokens", "promptTokens", "input_tokens"),
        "output_tokens": _token(usage, "outputTokens", "completionTokens", "output_tokens"),
        "cached_input_tokens": _token(usage, "cachedInputTokens", "cached_input_tokens"),
        "latency_ms": _token(usage, "latencyMs", "latency_ms"),
    }
    if normalized["input_tokens"] is None and normalized["output_tokens"] is None:
        return None
    return normalized


async def record_assistant_usage(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    feature: str,
    actor_type: str,
    actor_id: str | None,
    student_id: str | None,
    provider: object,
    model: object,
    usage: object,
    request_id: str,
    started_at: datetime | None = None,
) -> str | None:
    """Write the run + usage pair; return the run id, or None when nothing ran."""

    provider_name = str(provider or "").strip()
    if provider_name.lower() in _DETERMINISTIC_PROVIDERS:
        return None
    normalized = normalize_usage(usage)
    if normalized is None:
        return None
    run_id = uuid4()
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO public.agent_run (
                      id, tenant_id, feature, trigger_type, actor_type, actor_id,
                      student_id, provider, model, status, correlation_id,
                      started_at, completed_at, created_at
                    ) VALUES (
                      :id, :tenant_id, :feature, 'interactive', :actor_type, :actor_id,
                      :student_id, :provider, :model, 'succeeded', :correlation_id,
                      :started_at, :completed_at, :completed_at
                    )
                    """
                ),
                {
                    "id": run_id,
                    "tenant_id": UUID(tenant_id),
                    "feature": feature[:80],
                    "actor_type": actor_type,
                    "actor_id": UUID(actor_id) if actor_id else None,
                    "student_id": UUID(student_id) if student_id else None,
                    "provider": provider_name[:32],
                    "model": str(model or "unknown")[:160],
                    "correlation_id": request_id[:160],
                    "started_at": started_at or now,
                    "completed_at": now,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO public.model_usage (
                      id, tenant_id, agent_run_id, provider, model,
                      input_tokens, output_tokens, cached_input_tokens, latency_ms,
                      created_at
                    ) VALUES (
                      :id, :tenant_id, :agent_run_id, :provider, :model,
                      :input_tokens, :output_tokens, :cached_input_tokens, :latency_ms,
                      :created_at
                    )
                    ON CONFLICT (agent_run_id) DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": UUID(tenant_id),
                    "agent_run_id": run_id,
                    "provider": provider_name[:32],
                    "model": str(model or "unknown")[:160],
                    "created_at": now,
                    **normalized,
                },
            )
    except Exception:
        _LOGGER.exception("model usage accounting failed for %s", feature)
        return None
    return str(run_id)
