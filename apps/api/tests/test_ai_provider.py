import httpx
import pytest

from audentra.integrations.ai.provider import (
    CompletionClient,
    CompletionContext,
    PromptRuntime,
    ProviderCompletionError,
    message_content,
    openrouter_transport,
    prompt_context_sha256,
)


@pytest.mark.anyio
async def test_reuses_async_transport_and_records_provider_response() -> None:
    records: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "provider-1",
                "model": "test/model",
                "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                "usage": {"total_tokens": 3},
            },
            request=request,
        )

    async def recorder(value: dict[str, object]) -> None:
        records.append(value)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompletionClient(http, recorder)
        runtime = PromptRuntime("edward_chat", "test/model", "system", 100, 0)
        result = await client.complete(
            {"model": "test/model", "messages": []},
            openrouter_transport("secret", "http://portal", "Audentra"),
            CompletionContext(
                runtime,
                tenant_id="tenant",
                student_id="student",
                request_id="request",
            ),
        )
    assert message_content(result) == "ok"
    assert records[0]["responseOk"] is True
    assert records[0]["providerRequestId"] == "provider-1"


@pytest.mark.anyio
async def test_recorder_failure_does_not_replace_completion() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "safe"}}]},
            request=request,
        )

    async def recorder(_value: dict[str, object]) -> None:
        raise RuntimeError("journal unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await CompletionClient(http, recorder).complete(
            {"model": "m"},
            openrouter_transport("k", "http://portal", "Audentra"),
            CompletionContext(
                PromptRuntime("edward_chat", "m", "s", 1, 0),
                tenant_id="t",
                student_id="s",
                request_id="r",
            ),
        )
    assert message_content(result) == "safe"


@pytest.mark.anyio
async def test_http_error_preserves_status_for_retry_policy() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "limited"}}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderCompletionError) as raised:
            await CompletionClient(http).complete(
                {"model": "m"}, openrouter_transport("k", "http://portal", "Audentra")
            )
    assert raised.value.status == 429


def test_context_hash_is_canonical() -> None:
    assert prompt_context_sha256({"b": 2, "a": 1}) == prompt_context_sha256({"a": 1, "b": 2})
