"""Tenant-versioned AI prompt runtime with atomic, coalesced hot reloads."""

from __future__ import annotations

import asyncio
import copy
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

AiOperation = Literal[
    "action_center_enrichment",
    "edward_chat",
    "document_classification",
    "document_extraction",
    "transcript_segment_extraction",
    "transcript_merge",
    "course_label_normalization",
    "course_exemption_mapping",
    "immunization_extraction",
    "immunization_compliance",
]
CacheStatus = Literal["hit", "miss", "reloaded", "fallback"]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    tenant_id: str
    operation: AiOperation
    prompt_template_version_id: str | None
    context_policy_version_id: str | None
    output_schema_version_id: str | None
    config_revision: int
    updated_at: str
    system_prompt: str
    user_prompt_template: str | None
    context_policy: Mapping[str, object]
    output_schema: Mapping[str, object] | None
    provider: Literal["openrouter", "groq"]
    model: str
    max_output_tokens: int
    temperature: float
    cache_status: CacheStatus = "fallback"


class PromptRuntimeRepository(Protocol):
    async def get_head(self, tenant_id: str, operation: AiOperation) -> tuple[int, str] | None: ...

    async def load_published(
        self, tenant_id: str, operation: AiOperation
    ) -> RuntimeConfig | None: ...

    async def checkpoint(
        self,
        *,
        instance_id: str,
        tenant_id: str,
        operation: AiOperation,
        revision: int,
        checked_at: str,
        loaded_at: str,
    ) -> None: ...


class VersionedPromptRuntime:
    def __init__(self, repository: PromptRuntimeRepository, instance_id: str | None = None) -> None:
        self._repository = repository
        self._instance_id = instance_id or f"api-{os.getpid()}-{uuid.uuid4()}"
        self._cache: dict[tuple[str, AiOperation], RuntimeConfig] = {}
        self._refreshes: dict[tuple[str, AiOperation], asyncio.Task[RuntimeConfig]] = {}
        self._refresh_lock = asyncio.Lock()

    async def resolve(self, tenant_id: str, operation: AiOperation) -> RuntimeConfig:
        key = (tenant_id, operation)
        checked_at = _utc_now()
        head = await self._repository.get_head(tenant_id, operation)
        if head is None:
            raise RuntimeError(f"No published AI runtime configuration exists for {operation}")
        revision, _updated_at = head
        cached = self._cache.get(key)
        if cached is not None and cached.config_revision == revision:
            await self._repository.checkpoint(
                instance_id=self._instance_id,
                tenant_id=tenant_id,
                operation=operation,
                revision=revision,
                checked_at=checked_at,
                loaded_at=cached.updated_at,
            )
            return replace(cached, cache_status="hit")

        async with self._refresh_lock:
            refresh = self._refreshes.get(key)
            if refresh is None:
                refresh = asyncio.create_task(
                    self._load_and_validate(key, tenant_id, operation, revision)
                )
                self._refreshes[key] = refresh
        try:
            resolved = await refresh
        finally:
            async with self._refresh_lock:
                if self._refreshes.get(key) is refresh and refresh.done():
                    self._refreshes.pop(key, None)
        await self._repository.checkpoint(
            instance_id=self._instance_id,
            tenant_id=tenant_id,
            operation=operation,
            revision=resolved.config_revision,
            checked_at=checked_at,
            loaded_at=_utc_now(),
        )
        return replace(resolved, cache_status="reloaded" if cached else "miss")

    async def _load_and_validate(
        self,
        key: tuple[str, AiOperation],
        tenant_id: str,
        operation: AiOperation,
        expected_revision: int,
    ) -> RuntimeConfig:
        loaded = await self._repository.load_published(tenant_id, operation)
        if loaded is None or loaded.config_revision != expected_revision:
            raise RuntimeError(
                f"AI runtime configuration changed while loading {operation}; retry the call"
            )
        _validate(loaded, tenant_id, operation)
        immutable = copy.deepcopy(loaded)
        self._cache[key] = immutable
        return immutable


class PostgresPromptRuntimeRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_head(self, tenant_id: str, operation: AiOperation) -> tuple[int, str] | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT config_revision, updated_at
                    FROM ai_operation_config
                    WHERE tenant_id = :tenant_id AND operation = :operation
                    """
                ),
                {"tenant_id": tenant_id, "operation": operation},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return int(row["config_revision"]), _iso(row["updated_at"])

    async def load_published(self, tenant_id: str, operation: AiOperation) -> RuntimeConfig | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT
                      config.tenant_id, config.operation, config.config_revision,
                      config.updated_at, config.provider, config.model,
                      config.max_output_tokens, config.temperature_milli,
                      prompt.id AS prompt_template_version_id, prompt.system_prompt,
                      prompt.user_prompt_template,
                      context.id AS context_policy_version_id, context.context_policy,
                      output.id AS output_schema_version_id, output.output_schema
                    FROM ai_operation_config config
                    JOIN ai_prompt_template_version prompt
                      ON prompt.id = config.prompt_template_version_id
                     AND prompt.tenant_id = config.tenant_id
                     AND prompt.operation = config.operation
                     AND prompt.status = 'published'
                    JOIN ai_context_policy_version context
                      ON context.id = config.context_policy_version_id
                     AND context.tenant_id = config.tenant_id
                     AND context.operation = config.operation
                     AND context.status = 'published'
                    LEFT JOIN ai_output_schema_version output
                      ON output.id = config.output_schema_version_id
                     AND output.tenant_id = config.tenant_id
                     AND output.operation = config.operation
                     AND output.status = 'published'
                    WHERE config.tenant_id = :tenant_id AND config.operation = :operation
                    """
                ),
                {"tenant_id": tenant_id, "operation": operation},
            )
            row = result.mappings().first()
        if row is None:
            return None
        context_policy = row["context_policy"]
        output_schema = row["output_schema"]
        return RuntimeConfig(
            tenant_id=str(row["tenant_id"]),
            operation=str(row["operation"]),  # type: ignore[arg-type]
            prompt_template_version_id=str(row["prompt_template_version_id"]),
            context_policy_version_id=str(row["context_policy_version_id"]),
            output_schema_version_id=(
                str(row["output_schema_version_id"])
                if row["output_schema_version_id"] is not None
                else None
            ),
            config_revision=int(row["config_revision"]),
            updated_at=_iso(row["updated_at"]),
            system_prompt=str(row["system_prompt"]),
            user_prompt_template=(
                str(row["user_prompt_template"])
                if row["user_prompt_template"] is not None
                else None
            ),
            context_policy=(context_policy if isinstance(context_policy, dict) else {}),
            output_schema=(output_schema if isinstance(output_schema, dict) else None),
            provider="groq" if row["provider"] == "groq" else "openrouter",
            model=str(row["model"]),
            max_output_tokens=int(row["max_output_tokens"]),
            temperature=int(row["temperature_milli"]) / 1000,
        )

    async def checkpoint(
        self,
        *,
        instance_id: str,
        tenant_id: str,
        operation: AiOperation,
        revision: int,
        checked_at: str,
        loaded_at: str,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO ai_runtime_config_checkpoint (
                      instance_id, tenant_id, operation, last_seen_revision,
                      last_checked_at, last_loaded_at
                    ) VALUES (
                      :instance_id, :tenant_id, :operation, :revision,
                      :checked_at, :loaded_at
                    )
                    ON CONFLICT (instance_id, tenant_id, operation)
                    DO UPDATE SET
                      last_seen_revision = EXCLUDED.last_seen_revision,
                      last_checked_at = EXCLUDED.last_checked_at,
                      last_loaded_at = CASE
                        WHEN ai_runtime_config_checkpoint.last_seen_revision
                          IS DISTINCT FROM EXCLUDED.last_seen_revision
                        THEN EXCLUDED.last_loaded_at
                        ELSE ai_runtime_config_checkpoint.last_loaded_at
                      END
                    """
                ),
                {
                    "instance_id": instance_id,
                    "tenant_id": tenant_id,
                    "operation": operation,
                    "revision": revision,
                    "checked_at": _as_utc_datetime(checked_at),
                    "loaded_at": _as_utc_datetime(loaded_at),
                },
            )


def _validate(config: RuntimeConfig, tenant_id: str, operation: AiOperation) -> None:
    if (
        config.tenant_id != tenant_id
        or config.operation != operation
        or config.config_revision < 1
        or len(config.system_prompt.strip()) < 20
        or not config.prompt_template_version_id
        or not config.context_policy_version_id
        or not config.model.strip()
        or config.max_output_tokens < 1
    ):
        raise RuntimeError(f"Published AI runtime configuration is invalid for {operation}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _as_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError("database returned an invalid timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
