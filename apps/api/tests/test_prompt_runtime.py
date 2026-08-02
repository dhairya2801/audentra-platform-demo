import asyncio
from dataclasses import replace

import pytest

from audentra.integrations.ai.prompt_runtime import (
    AiOperation,
    RuntimeConfig,
    VersionedPromptRuntime,
)

BASE = RuntimeConfig(
    tenant_id="tenant-1",
    operation="edward_chat",
    prompt_template_version_id="prompt-1",
    context_policy_version_id="context-1",
    output_schema_version_id=None,
    config_revision=1,
    updated_at="2026-01-01T00:00:00Z",
    system_prompt="A sufficiently long and safe system prompt.",
    user_prompt_template=None,
    context_policy={},
    output_schema=None,
    provider="openrouter",
    model="test/model",
    max_output_tokens=100,
    temperature=0,
)


class Repository:
    def __init__(self) -> None:
        self.current = BASE
        self.loads = 0
        self.checkpoints: list[int] = []

    async def get_head(self, tenant_id: str, operation: AiOperation) -> tuple[int, str] | None:
        return self.current.config_revision, self.current.updated_at

    async def load_published(self, tenant_id: str, operation: AiOperation) -> RuntimeConfig | None:
        self.loads += 1
        await asyncio.sleep(0)
        return self.current

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
        del instance_id, tenant_id, operation, checked_at, loaded_at
        self.checkpoints.append(revision)


@pytest.mark.anyio
async def test_hot_reloads_when_authoritative_revision_changes() -> None:
    repository = Repository()
    runtime = VersionedPromptRuntime(repository, "test-instance")
    first = await runtime.resolve("tenant-1", "edward_chat")
    hit = await runtime.resolve("tenant-1", "edward_chat")
    repository.current = replace(BASE, config_revision=2, model="test/model-v2")
    reloaded = await runtime.resolve("tenant-1", "edward_chat")
    assert (first.cache_status, hit.cache_status, reloaded.cache_status) == (
        "miss",
        "hit",
        "reloaded",
    )
    assert reloaded.model == "test/model-v2"
    assert repository.loads == 2


@pytest.mark.anyio
async def test_coalesces_concurrent_refreshes() -> None:
    repository = Repository()
    runtime = VersionedPromptRuntime(repository, "test-instance")
    values = await asyncio.gather(*(runtime.resolve("tenant-1", "edward_chat") for _ in range(10)))
    assert repository.loads == 1
    assert {value.model for value in values} == {"test/model"}
