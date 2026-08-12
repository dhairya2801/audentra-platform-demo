"""Reusable async OpenRouter/Groq transport with durable diagnostic hooks."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class ProviderCompletionError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class PromptRuntime:
    operation: str
    model: str
    system_prompt: str
    max_output_tokens: int
    temperature: float
    prompt_template_version_id: str | None = None
    context_policy_version_id: str | None = None
    output_schema_version_id: str | None = None
    config_revision: int | None = None
    cache_status: str = "fallback"


@dataclass(frozen=True, slots=True)
class CompletionContext:
    runtime: PromptRuntime
    tenant_id: str | None = None
    student_id: str | None = None
    document_id: str | None = None
    request_id: str | None = None
    attempt: int = 1
    timeout_seconds: float = 45.0
    context_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderTransport:
    provider: str
    label: str
    url: str
    api_key: str
    headers: Mapping[str, str]


ResponseRecorder = Callable[[dict[str, Any]], Awaitable[None]]


class CompletionClient:
    """One pooled HTTP client per API process; safe to reuse across requests."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        recorder: ResponseRecorder | None = None,
    ) -> None:
        self._http = http
        self._recorder = recorder

    async def complete(
        self,
        body: Mapping[str, Any],
        transport: ProviderTransport,
        context: CompletionContext | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        response: httpx.Response | None = None
        payload: dict[str, Any] | None = None
        raw_text: str | None = None
        transport_error: dict[str, str] | None = None
        try:
            response = await self._http.post(
                transport.url,
                headers=dict(transport.headers),
                json=dict(body),
                timeout=context.timeout_seconds if context else 45.0,
            )
            raw_text = response.text
            try:
                decoded = json.loads(raw_text)
                payload = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                payload = None
        except Exception as exc:
            transport_error = {
                "name": type(exc).__name__[:120],
                "message": str(exc)[:500],
            }
            await self._record(
                body,
                transport,
                context,
                response,
                raw_text,
                payload,
                transport_error,
                started,
            )
            raise

        await self._record(
            body,
            transport,
            context,
            response,
            raw_text,
            payload,
            transport_error,
            started,
        )
        if not response.is_success:
            raise ProviderCompletionError(
                f"{transport.label} returned HTTP {response.status_code}",
                response.status_code,
            )
        choices = payload.get("choices") if payload else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderCompletionError(
                f"{transport.label} returned an empty completion", response.status_code
            )
        if not isinstance(choices[0].get("message"), dict):
            raise ProviderCompletionError(
                f"{transport.label} returned an empty completion", response.status_code
            )
        assert payload is not None
        return payload

    async def _record(
        self,
        body: Mapping[str, Any],
        transport: ProviderTransport,
        context: CompletionContext | None,
        response: httpx.Response | None,
        raw_text: str | None,
        payload: dict[str, Any] | None,
        transport_error: dict[str, str] | None,
        started: float,
    ) -> None:
        if (
            self._recorder is None
            or context is None
            or context.tenant_id is None
            or context.student_id is None
            or context.request_id is None
        ):
            return
        choices = payload.get("choices") if payload else None
        first_choice = choices[0] if isinstance(choices, list) and choices else None
        record: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "tenantId": context.tenant_id,
            "studentId": context.student_id,
            "documentId": context.document_id,
            "requestId": context.request_id,
            "attempt": context.attempt,
            "operation": context.runtime.operation,
            "provider": transport.provider,
            "requestedModel": body.get("model") if isinstance(body.get("model"), str) else None,
            "responseModel": payload.get("model") if payload else None,
            "providerRequestId": payload.get("id") if payload else None,
            "httpStatus": response.status_code if response else None,
            "responseOk": bool(response and response.is_success),
            "finishReason": (
                first_choice.get("finish_reason") if isinstance(first_choice, dict) else None
            ),
            "usage": payload.get("usage") if payload else None,
            "rawResponseText": raw_text,
            "responseBody": payload if payload is not None else raw_text,
            "transportError": transport_error,
            "durationMs": round((time.perf_counter() - started) * 1000),
            "recordedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "promptTemplateVersionId": context.runtime.prompt_template_version_id,
            "contextPolicyVersionId": context.runtime.context_policy_version_id,
            "outputSchemaVersionId": context.runtime.output_schema_version_id,
            "configRevision": context.runtime.config_revision,
            "contextSha256": context.context_sha256,
            "promptCacheStatus": context.runtime.cache_status,
        }
        try:
            await self._recorder(record)
        except Exception:
            # Observability journal failure must never replace the business result.
            return


def openrouter_transport(api_key: str, app_url: str, app_name: str) -> ProviderTransport:
    return ProviderTransport(
        provider="openrouter",
        label="OpenRouter",
        url=OPENROUTER_URL,
        api_key=api_key,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": app_url,
            "X-Title": app_name,
        },
    )


def openai_transport(api_key: str) -> ProviderTransport:
    """Direct OpenAI, selected whenever OPENAI_API_KEY is configured.

    Matches the VV_Edgent-voice preview host: an OpenAI key short-circuits
    OpenRouter for every chat-completion operation, so Edward's planner and
    prose run on the exact provider the original implementation used.
    """

    return ProviderTransport(
        provider="openai",
        label="OpenAI",
        url=OPENAI_URL,
        api_key=api_key,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )


def groq_transport(api_key: str) -> ProviderTransport:
    return ProviderTransport(
        provider="groq",
        label="Groq",
        url=GROQ_URL,
        api_key=api_key,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )


def message_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ProviderCompletionError("Provider returned an empty completion")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ProviderCompletionError("Provider returned an empty completion")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text"))
            for part in content
            if isinstance(part, Mapping) and isinstance(part.get("text"), str)
        )
    raise ProviderCompletionError("Provider returned an empty completion")


def prompt_context_sha256(context: object) -> str:
    canonical = json.dumps(
        context,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
